"""Unit tests for the ``sys_selection.py`` MCP tool's pure logic.

Vector normalization (``_geom.unit_vector``, including the zero-vector guard) is pinned in
test__geom.py, shared with find_geometry. The bug surface here is the geometry classification:
``_face_direction`` / ``_edge_direction`` (which branch on the runtime surface/curve type name), and
``_classify`` (the big dispatch by ``type(entity).__name__``). None of it needs a live Fusion — only
fakes whose class names match Fusion's type names.

The sys_request_selection HOLD path (wait_seconds) is exercised with a FAKE TaskManager (a
types.SimpleNamespace stand-in, not a bespoke class - see _install_fake_task_manager) whose post()
runs the marshaled main-thread step synchronously on the test thread instead of a real cross-thread
Fusion event loop. This proves the orchestration (setup -> wait -> pick/timeout -> cleanup) without
needing live Fusion threading; the actual main-thread safety property (never blocking Fusion) is a
live-verify concern, not a unit-test one.
"""

import threading
import time
import types

import pytest

from conftest import (
    BRepBody,
    BRepEdge,
    BRepFace,
    Circle3D,
    Cylinder,
    FakePoint,
    FakeVector3D,
    Line3D,
    Plane,
    Sphere,
    load_tool,
)

sel = load_tool("sys_selection")


# ── _face_direction: branch on surface type ────────────────────────────────

class TestFaceDirection:
    def test_planar_face_returns_normal(self):
        face = BRepFace(Plane(FakeVector3D(0, 0, 2)))
        vec, kind = sel._face_direction(face)
        assert vec == [0.0, 0.0, 1.0]
        assert kind == "face_normal"

    def test_cylindrical_face_returns_axis(self):
        face = BRepFace(Cylinder(FakeVector3D(0, 10, 0)))
        vec, kind = sel._face_direction(face)
        assert vec == [0.0, 1.0, 0.0]
        assert kind == "axis"

    def test_sphere_has_no_direction(self):
        face = BRepFace(Sphere())
        vec, kind = sel._face_direction(face)
        assert vec is None
        assert kind is None


# ── _edge_direction: branch on curve type ──────────────────────────────────

class TestEdgeDirection:
    def test_linear_edge_direction_is_end_minus_start(self):
        edge = BRepEdge(Line3D(), start=FakePoint(1, 0, 0), end=FakePoint(4, 0, 0))
        vec, kind = sel._edge_direction(edge)
        assert vec == [1.0, 0.0, 0.0]   # +X, unit
        assert kind == "edge_direction"

    def test_circular_edge_direction_is_plane_normal(self):
        edge = BRepEdge(Circle3D(FakeVector3D(0, 0, 7)))
        vec, kind = sel._edge_direction(edge)
        assert vec == [0.0, 0.0, 1.0]
        assert kind == "axis"


# ── _classify: dispatch by entity type ─────────────────────────────────────

class TestClassify:
    def test_face_is_classified_with_direction(self):
        out = sel._classify(BRepFace(Plane(FakeVector3D(0, 0, 1))))
        assert out["object_type"] == "BRepFace"
        assert out["kind"] == "face"
        assert out["surface_type"] == "Plane"
        assert out["direction"] == [0.0, 0.0, 1.0]
        assert out["direction_kind"] == "face_normal"

    def test_edge_is_classified_as_edge(self):
        out = sel._classify(BRepEdge(Line3D(), start=FakePoint(0, 0, 0), end=FakePoint(0, 2, 0)))
        assert out["kind"] == "edge"
        assert out["curve_type"] == "Line3D"
        assert out["direction"] == [0.0, 1.0, 0.0]

    def test_unknown_entity_falls_through_to_other(self):
        class Mystery:
            name = "weird"
        out = sel._classify(Mystery())
        assert out["object_type"] == "Mystery"
        assert out["kind"] == "other"


# ── handler 'require' mismatch flagging ────────────────────────────────────

class TestRequireFlag:
    """sys_get_selection should flag when the selection doesn't match 'require'.

    We bypass the live UI by stubbing _ui()/_classify the minimum needed: build a
    fake Selections collection holding one BRepFace and check the require logic.
    """

    def _fake_ui_with(self, entities):
        class _Sel:
            def __init__(self, e):
                self.entity = e
                self.point = FakePoint(0, 0, 0)

        class _Sels:
            def __init__(self, es):
                self._es = [_Sel(e) for e in es]

            @property
            def count(self):
                return len(self._es)

            def item(self, i):
                return self._es[i]

        class _UI:
            activeSelections = _Sels(entities)

        return _UI()

    def test_require_face_matches_a_face(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with([BRepFace(Plane(FakeVector3D(0, 0, 1)))]))
        result = sel.get_user_selection_handler(require="face")
        payload = _payload(result)
        assert payload["matches_required"] is True

    def test_require_edge_flags_mismatch_when_face_selected(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with([BRepFace(Plane(FakeVector3D(0, 0, 1)))]))
        result = sel.get_user_selection_handler(require="edge")
        payload = _payload(result)
        assert payload["matches_required"] is False
        assert "note" in payload

    def test_nothing_selected_is_an_error(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with([]))
        result = sel.get_user_selection_handler()
        assert result["isError"] is True
        assert "Nothing is selected in Fusion" in result["message"]


# ── BOUNDED READS: the selection echo is capped (CLAUDE.md "Bound it") ──────────────────────────

class TestSelectionCap:
    def _fake_ui_with(self, entities):
        class _Sel:
            def __init__(self, e):
                self.entity = e
                self.point = FakePoint(0, 0, 0)

        class _Sels:
            def __init__(self, es):
                self._es = [_Sel(e) for e in es]

            @property
            def count(self):
                return len(self._es)

            def item(self, i):
                return self._es[i]

        class _UI:
            activeSelections = _Sels(entities)

        return _UI()

    def test_under_cap_untruncated_and_unchanged(self, monkeypatch):
        entities = [BRepFace(Plane(FakeVector3D(0, 0, 1))) for _ in range(5)]
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with(entities))
        out = _payload(sel.get_user_selection_handler())
        assert out["truncated"] is False
        assert len(out["selections"]) == 5
        assert out["selection_count"] == 5

    def test_at_cap_truncates_and_flags(self, monkeypatch):
        entities = [BRepFace(Plane(FakeVector3D(0, 0, 1))) for _ in range(60)]
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with(entities))
        out = _payload(sel.get_user_selection_handler(max_results=50))
        assert out["truncated"] is True
        assert len(out["selections"]) == 50
        # the full count is still honest, even though the array is capped
        assert out["selection_count"] == 60

    def test_a_selection_that_will_not_read_is_refused_not_quietly_dropped(self, monkeypatch):
        # the user picked three entities. Skipping the one that will not read publishes two records
        # under a count of three and calls the shortfall 'truncated' - the caller then acts on a
        # selection list that is missing the pick between the two it can see.
        ui = self._fake_ui_with([BRepFace(Plane(FakeVector3D(0, 0, 1))) for _ in range(3)])
        sels = ui.activeSelections
        intact = sels.item

        def item(i):
            if i == 1:
                raise RuntimeError("4 : An API Object refers to a deleted Object")
            return intact(i)
        monkeypatch.setattr(sels, "item", item)
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        res = sel.get_user_selection_handler()
        assert res["isError"] is True
        assert "Could not read the selection" in res["message"]


def _payload(result):
    import json
    return json.loads(result["content"][0]["text"])


# ── _geometry_handle / _selection_record: find_geometry-style handle minting ────────────────────

class TestGeometryHandle:
    def test_face_handle_uses_centroid_and_token(self):
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=FakePoint(1, 2, 3), entity_token="TOK_F")
        h = sel._geometry_handle(face, "face")
        assert h == "TOK_F|@face:1.000000,2.000000,3.000000"

    def test_edge_handle_uses_point_on_edge_and_token(self):
        edge = BRepEdge(Line3D(), point_on_edge=FakePoint(4, 5, 6), entity_token="TOK_E")
        h = sel._geometry_handle(edge, "edge")
        assert h == "TOK_E|@edge:4.000000,5.000000,6.000000"

    def test_vertex_handle_uses_geometry_point(self):
        vertex = types.SimpleNamespace(geometry=FakePoint(7, 8, 9), entityToken="TOK_V")
        h = sel._geometry_handle(vertex, "vertex")
        assert h == "TOK_V|@vertex:7.000000,8.000000,9.000000"

    def test_body_kind_mints_no_handle(self):
        # find_geometry itself mints no handle for a body (only face/edge/vertex) - a picked BODY
        # must not fabricate one either.
        assert sel._geometry_handle(BRepBody(name="Block"), "body") is None

    def test_component_kind_mints_no_handle(self):
        assert sel._geometry_handle(object(), "component") is None

    def test_missing_point_falls_back_to_bare_token(self):
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=None, entity_token="TOK_BARE")
        assert sel._geometry_handle(face, "face") == "TOK_BARE"


class TestSelectionRecordHandle:
    def _sel(self, entity, point=None):
        return types.SimpleNamespace(entity=entity, point=point or FakePoint(0, 0, 0))

    def test_face_selection_record_carries_handle(self):
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=FakePoint(1, 1, 1), entity_token="TOK1")
        rec = sel._selection_record(self._sel(face))
        assert rec["kind"] == "face"
        assert rec["handle"] == "TOK1|@face:1.000000,1.000000,1.000000"

    def test_body_selection_record_has_no_handle_key(self):
        rec = sel._selection_record(self._sel(BRepBody(name="Block")))
        assert rec["kind"] == "body"
        assert "handle" not in rec

    def test_get_user_selection_handler_output_includes_handle(self, monkeypatch):
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=FakePoint(2, 2, 2), entity_token="TOK2")
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui(entities=[face]))
        out = _payload(sel.get_user_selection_handler())
        assert out["selections"][0]["handle"] == "TOK2|@face:2.000000,2.000000,2.000000"
        # RETURNS contract: the declared 'handle' output key really is present in a list item.
        assert sel.RETURNS[0].assert_present(out) == ""
        assert "PRODUCES" in out["note"]


# ── shared fakes for the sys_request_selection orchestration tests ──────────────────────────────
# Nested classes matching this file's pre-existing _fake_ui_with pattern. The ratchet lint walks
# nested classes too; these stay outside its count because their names are not fake-shaped.

def _fake_ui(entities=()):
    """activeSelections (clear/count/item) + activeSelectionChanged (add/remove) - the surface
    _begin_request/_PickHandler touch."""
    class _Sel:
        def __init__(self, e):
            self.entity = e
            self.point = FakePoint(0, 0, 0)

    class _Sels:
        def __init__(self, es):
            self._es = [_Sel(e) for e in es]

        @property
        def count(self):
            return len(self._es)

        def item(self, i):
            return self._es[i]

        def clear(self):
            self._es = []
            return True

    class _ChangedEvent:
        def __init__(self):
            self.handlers = []

        def add(self, h):
            self.handlers.append(h)
            return True

        def remove(self, h):
            if h in self.handlers:
                self.handlers.remove(h)
                return True
            return False

    class _UI:
        def __init__(self, es):
            self.activeSelections = _Sels(es)
            self.activeSelectionChanged = _ChangedEvent()

    return _UI(list(entities))


def _install_fake_task_manager(monkeypatch, run_immediately=True):
    """monkeypatch sel._task_manager to a synchronous stand-in: post(command, callback, data)
    invokes callback(data) immediately on the CALLING (test) thread instead of a real cross-thread
    Fusion marshal - exercises request_user_selection_handler's own orchestration without real
    Fusion threading. Returns the list of (command, data) pairs posted."""
    calls = []

    def _post(command, callback, data):
        calls.append((command, data))
        if run_immediately:
            callback(data)
        return "fake-task"

    ns = types.SimpleNamespace(is_running=lambda: True, start=lambda: True, post=_post,
                               cancel=lambda task_id: False)
    monkeypatch.setattr(sel, "_task_manager", lambda: ns)
    return calls


def _install_fake_pick_handler(monkeypatch):
    """monkeypatch sel._PickHandler to a plain, working stand-in with the same (box, done)
    constructor + notify(args) shape, delegating to the real _on_selection_changed logic.

    adsk.core.ActiveSelectionEventHandler is a bare Mock() under the unit-test harness, and
    `class _PickHandler(that_mock)` does not produce a real subclass there (Python's class
    statement degrades `class X(mock_instance)` to ANOTHER mock - confirmed empirically), so
    _PickHandler's own __init__/notify never run under test. This stand-in sidesteps that mock
    artifact while still exercising the real notify logic via _on_selection_changed."""
    class _Stub:
        def __init__(self, box, done):
            self._box, self._done = box, done

        def notify(self, args):
            sel._on_selection_changed(args, self._box, self._done)

    monkeypatch.setattr(sel, "_PickHandler", _Stub)


def _fake_pickable_design(bodies=1, sketches=0, occs=0):
    """A design SimpleNamespace just deep enough for _pickable_counts: rootComponent carrying
    bRepBodies/sketches/allOccurrences counts, no allComponents attribute (so the shared
    _common.all_components walk falls back to [root] - the same shape the real walk degrades to)."""
    c = lambda n: types.SimpleNamespace(count=n)
    root = types.SimpleNamespace(bRepBodies=c(bodies), sketches=c(sketches), allOccurrences=c(occs))
    return types.SimpleNamespace(rootComponent=root)


@pytest.fixture(autouse=True)
def _reset_pending_and_identity(monkeypatch):
    # Every test in this file gets a clean pending-request slot (module-level state _pending isn't
    # one of conftest's auto-restored seam attrs), a JSON-safe document identity (the raw adsk
    # mock's app.activeDocument.name is itself a non-serializable Mock), a PICKABLE design (the
    # nothing-to-select guard reads _active_design, and the raw adsk mock is not count-walkable),
    # and a working _PickHandler stand-in (see _install_fake_pick_handler).
    sel._pending["active"] = False
    sel._pending["handler"] = None
    sel._pending["started"] = None
    monkeypatch.setattr(sel._write_guard, "_active_identity", lambda: ("TestDoc", "urn:test:doc"))
    monkeypatch.setattr(sel, "_active_design", lambda: _fake_pickable_design())
    _install_fake_pick_handler(monkeypatch)
    yield
    sel._pending["active"] = False
    sel._pending["handler"] = None
    sel._pending["started"] = None


# ── _on_selection_changed: the notify() logic, tested directly (see _install_fake_pick_handler) ──

class TestOnSelectionChanged:
    def test_pick_captures_result_and_sets_done(self, monkeypatch):
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=FakePoint(1, 1, 1), entity_token="TOK1")
        ui = _fake_ui()
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        sentinel_handler = object()
        sel._pending["handler"] = sentinel_handler
        ui.activeSelectionChanged.handlers.append(sentinel_handler)

        box, done = {}, threading.Event()
        picked = types.SimpleNamespace(entity=face, point=FakePoint(1, 1, 1))
        sel._on_selection_changed(types.SimpleNamespace(currentSelection=[picked]), box, done)

        assert done.is_set()
        assert box["result"]["selection_count"] == 1
        assert box["result"]["selections"][0]["handle"] == "TOK1|@face:1.000000,1.000000,1.000000"
        assert sel._pending["handler"] is None            # detached itself
        assert sentinel_handler not in ui.activeSelectionChanged.handlers

    def test_empty_selection_keeps_waiting(self, monkeypatch):
        ui = _fake_ui()
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        box, done = {}, threading.Event()
        sel._on_selection_changed(types.SimpleNamespace(currentSelection=[]), box, done)
        assert not done.is_set()
        assert box == {}


# ── sys_request_selection: wait_seconds guard (<=300; the error names the value) ────────────────

class TestValidateWaitSeconds:
    def test_zero_is_valid(self):
        v, err = sel._validate_wait_seconds(0)
        assert err is None and v == 0.0

    def test_default_is_valid(self):
        v, err = sel._validate_wait_seconds(60)
        assert err is None and v == 60.0

    def test_max_bound_is_valid(self):
        v, err = sel._validate_wait_seconds(300)
        assert err is None and v == 300.0

    def test_negative_is_rejected_naming_the_value(self):
        v, err = sel._validate_wait_seconds(-5)
        assert v is None
        assert "-5" in err and "wait_seconds" in err

    def test_above_max_is_rejected_naming_the_value(self):
        v, err = sel._validate_wait_seconds(301)
        assert v is None
        assert "301" in err and "300" in err

    def test_non_numeric_is_rejected(self):
        v, err = sel._validate_wait_seconds("soon")
        assert v is None and "number" in err

    def test_handler_surfaces_the_guard_as_an_honest_error(self):
        res = sel.request_user_selection_handler(wait_seconds=500)
        assert res["isError"] is True
        assert "500" in res["message"]


# ── sys_request_selection: wait_seconds=0 preserves the legacy fire-and-return ───────────────────

class TestRequestSelectionFireAndReturn:
    def test_wait_seconds_zero_behaves_like_legacy_fire_and_return(self, monkeypatch):
        _install_fake_task_manager(monkeypatch)
        ui = _fake_ui()
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        out = _payload(sel.request_user_selection_handler(what="face", wait_seconds=0))
        assert out["status"] == "awaiting_selection"
        assert out["awaiting_user_selection"] is True
        assert out["cleared_previous_selection"] is True
        assert ui.activeSelections.count == 0
        assert sel._pending["handler"] is None   # no listener registered for the legacy path

    def test_clear_current_false_with_nothing_selected_still_awaits(self, monkeypatch):
        _install_fake_task_manager(monkeypatch)
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui())
        out = _payload(sel.request_user_selection_handler(wait_seconds=0, clear_current=False))
        assert out["status"] == "awaiting_selection"
        assert out["cleared_previous_selection"] is None


# ── sys_request_selection: already-selected short-circuit (no wait needed) ───────────────────────

class TestRequestSelectionImmediatePick:
    def test_already_selected_short_circuits_without_waiting(self, monkeypatch):
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=FakePoint(1, 1, 1), entity_token="TOK1")
        _install_fake_task_manager(monkeypatch)
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui(entities=[face]))
        out = _payload(sel.request_user_selection_handler(clear_current=False, wait_seconds=30))
        assert out["status"] == "picked"
        assert out["selections"][0]["handle"].startswith("TOK1|@face:")
        assert sel._pending["handler"] is None   # short-circuited - never registered a listener

    def test_an_unreadable_selection_in_the_immediate_pick_is_disclosed(self, monkeypatch):
        # iter_collection drops a selection whose item() raises; publishing the raw count beside
        # the shorter list would silently claim completeness, so the hole is disclosed.
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=FakePoint(1, 1, 1), entity_token="TOK1")
        _install_fake_task_manager(monkeypatch)
        ui = _fake_ui(entities=[face, face])
        sels = ui.activeSelections
        real_item = sels.item
        def item(i, _real=real_item):
            if i == 1:
                raise RuntimeError("4 : An API Object refers to a deleted Object")
            return _real(i)
        monkeypatch.setattr(sels, "item", item, raising=False)
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        out = _payload(sel.request_user_selection_handler(clear_current=False, wait_seconds=30))
        assert out["status"] == "picked"
        assert out["selection_count"] == 2
        assert len(out["selections"]) == 1
        assert out["unread_selections"] == 1
        assert "could not be read" in out["note"]


# ── sys_request_selection: a pick DURING the wait completes the call in one shot ─────────────────

class TestRequestSelectionCompletedPick:
    def test_pick_during_wait_returns_handle_and_classification_in_one_call(self, monkeypatch):
        face = BRepFace(Plane(FakeVector3D(0, 0, 1)), centroid=FakePoint(3, 3, 3), entity_token="TOK3")
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui())

        def _post(command, callback, data):
            callback(data)   # runs _begin_request "on the main thread": registers the pick listener
            handler = sel._pending["handler"]
            assert handler is not None, "expected a pick listener while nothing is selected yet"
            picked = types.SimpleNamespace(entity=face, point=FakePoint(3, 3, 3))
            handler.notify(types.SimpleNamespace(currentSelection=[picked]))
            return "fake-task"

        monkeypatch.setattr(sel, "_task_manager", lambda: types.SimpleNamespace(
            is_running=lambda: True, start=lambda: True, post=_post, cancel=lambda t: False))

        out = _payload(sel.request_user_selection_handler(what="face", wait_seconds=5))
        assert out["status"] == "picked"
        assert out["selection_count"] == 1
        # described the way sys_get_selection describes it (the SAME _classify()/_selection_record
        # shape) PLUS the minted handle.
        assert out["selections"][0]["kind"] == "face"
        assert out["selections"][0]["surface_type"] == "Plane"
        assert out["selections"][0]["handle"] == "TOK3|@face:3.000000,3.000000,3.000000"
        assert out["acted_on"] == {"name": "TestDoc", "document_id": "urn:test:doc"}
        assert sel._pending["handler"] is None   # detached itself after firing


# ── sys_request_selection: timeout is an honest ok(), never an error ─────────────────────────────

class TestRequestSelectionTimeout:
    def test_timeout_is_an_honest_non_error_ok(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui())   # nothing selected, nobody ever picks
        _install_fake_task_manager(monkeypatch)   # runs _begin_request, then _cancel_pending_wait

        res = sel.request_user_selection_handler(wait_seconds=0.05)
        assert res["isError"] is False
        out = _payload(res)
        assert out["status"] == "timeout"
        assert out["waited_seconds"] == 0.05
        assert "not a tool defect" in out["note"]
        assert sel._pending["handler"] is None   # the timeout cleanup detached the listener


# ── sys_request_selection: only one wait may be pending at a time ────────────────────────────────

class TestRequestSelectionPendingGuard:
    def test_second_request_refused_while_one_is_pending(self):
        sel._pending["active"] = True
        sel._pending["started"] = time.monotonic()
        res = sel.request_user_selection_handler(wait_seconds=1)
        assert res["isError"] is True
        assert "already waiting" in res["message"]

    def test_pending_flag_clears_after_a_timeout(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui())
        _install_fake_task_manager(monkeypatch)
        sel.request_user_selection_handler(wait_seconds=0.05)
        assert sel._pending["active"] is False


# ── sys_request_selection: expect_document is honored (checked on the main thread) ───────────────

class TestRequestSelectionExpectDocument:
    def test_mismatched_expect_document_refuses_without_touching_selection(self, monkeypatch):
        monkeypatch.setattr(sel._write_guard, "_active_identity", lambda: ("Other.f3d", "urn:other"))
        ui = _fake_ui(entities=[BRepBody(name="X")])
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        _install_fake_task_manager(monkeypatch)
        res = sel.request_user_selection_handler(expect_document="Expected.f3d", wait_seconds=0)
        assert res["isError"] is True
        assert "active_document_changed" in res["message"]
        assert ui.activeSelections.count == 1   # never cleared - refused before touching it

    def test_ambiguous_expect_document_name_refuses_with_candidates(self, monkeypatch):
        # A bare name shared by two OPEN docs must REFUSE here exactly like the wrapped write guard -
        # the active twin may not be the one meant. Gated through _write_guard._document_refusal, so
        # an ambiguous name lists the candidate URNs instead of proceeding against the active one.
        monkeypatch.setattr(sel._write_guard, "_active_identity", lambda: ("Twin.f3d", None))
        monkeypatch.setattr(sel._write_guard, "_open_documents", lambda: [
            {"name": "Twin.f3d", "document_id": "urn:a", "open_index": 0, "is_active": True},
            {"name": "Twin.f3d", "document_id": "urn:b", "open_index": 1, "is_active": False},
        ])
        ui = _fake_ui(entities=[BRepBody(name="X")])
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        _install_fake_task_manager(monkeypatch)
        res = sel.request_user_selection_handler(expect_document="Twin.f3d", wait_seconds=0)
        assert res["isError"] is True
        assert "ambiguous_document_name" in res["message"]
        payload = _payload(res)
        assert {c["document_id"] for c in payload["candidates"]} == {"urn:a", "urn:b"}
        assert ui.activeSelections.count == 1   # never cleared - refused before touching it


# ── _call_on_main_thread: the marshal itself, independent of _begin_request ──────────────────────

class TestCallOnMainThread:
    def test_post_failure_reports_an_error_without_raising(self, monkeypatch):
        monkeypatch.setattr(sel, "_task_manager", lambda: types.SimpleNamespace(
            is_running=lambda: True, start=lambda: True,
            post=lambda command, callback, data: None, cancel=lambda t: False))
        out = sel._call_on_main_thread(lambda: 1, {})
        assert out.get("error")

    def test_marshal_timeout_cancels_the_task_and_reports(self, monkeypatch):
        monkeypatch.setattr(sel, "_task_manager", lambda: types.SimpleNamespace(
            is_running=lambda: True, start=lambda: True,
            post=lambda command, callback, data: "task-x",   # never invokes callback
            cancel=lambda t: True))
        out = sel._call_on_main_thread(lambda: 1, {}, timeout=0.05)
        assert "did not respond" in out.get("error", "")

    def test_a_raised_exception_propagates_to_the_caller(self, monkeypatch):
        def _boom():
            raise ValueError("kaboom")

        monkeypatch.setattr(sel, "_task_manager", lambda: types.SimpleNamespace(
            is_running=lambda: True, start=lambda: True,
            post=lambda command, callback, data: (callback(data), "task-1")[1],
            cancel=lambda t: False))
        with pytest.raises(ValueError, match="kaboom"):
            sel._call_on_main_thread(_boom, {})


# ── sys_request_selection: the nothing-to-select refusal ────────────────────────────────────────

class TestNothingToSelect:
    """_begin_request refuses BEFORE clearing the selection or registering a pick listener when the
    session has nothing pickable (no design, or an all-empty design), on the hold path and the
    wait_seconds=0 path alike - a request there could only time out."""

    def test_no_open_design_refuses_before_prompting(self, monkeypatch):
        _install_fake_task_manager(monkeypatch)
        ui = _fake_ui()
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        monkeypatch.setattr(sel, "_active_design", lambda: None)
        res = sel.request_user_selection_handler(wait_seconds=5)
        assert res["isError"] is True
        assert "no design is open" in res["message"]
        assert ui.activeSelectionChanged.handlers == []   # refused before any listener existed

    def test_empty_design_refuses_naming_the_zero_counts(self, monkeypatch):
        _install_fake_task_manager(monkeypatch)
        ui = _fake_ui()
        monkeypatch.setattr(sel, "_ui", lambda: ui)
        monkeypatch.setattr(sel, "_active_design", lambda: _fake_pickable_design(0, 0, 0))
        res = sel.request_user_selection_handler(wait_seconds=5)
        assert res["isError"] is True
        assert "bodies=0" in res["message"] and "occurrences=0" in res["message"]
        assert ui.activeSelectionChanged.handlers == []

    def test_empty_design_refuses_the_fire_and_return_path_too(self, monkeypatch):
        _install_fake_task_manager(monkeypatch)
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui())
        monkeypatch.setattr(sel, "_active_design", lambda: _fake_pickable_design(0, 0, 0))
        res = sel.request_user_selection_handler(wait_seconds=0)
        assert res["isError"] is True

    def test_sketch_only_design_is_pickable(self, monkeypatch):
        # A sketch-only doc (the unbodied parametric-plan handoff) has clickable sketch geometry -
        # the guard must not over-refuse it.
        _install_fake_task_manager(monkeypatch)
        monkeypatch.setattr(sel, "_ui", lambda: _fake_ui())
        monkeypatch.setattr(sel, "_active_design",
                            lambda: _fake_pickable_design(bodies=0, sketches=2, occs=0))
        out = _payload(sel.request_user_selection_handler(wait_seconds=0))
        assert out["status"] == "awaiting_selection"

    def test_pickable_counts_walks_the_design_and_is_none_without_one(self, monkeypatch):
        monkeypatch.setattr(sel, "_active_design", lambda: None)
        assert sel._pickable_counts() is None
        monkeypatch.setattr(sel, "_active_design", lambda: _fake_pickable_design(2, 1, 3))
        assert sel._pickable_counts() == (2, 1, 3)
