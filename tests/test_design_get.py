"""Tests for `design_get` - the first RICH READ (one tool, default slice + include= deeper slices).

CANONICAL EXAMPLE for a rich-read tool (see tests/README.md "The canonical examples"). The pattern,
and the one rule that makes it leak-free: a `@pytest.fixture` stubs the tool's `_slice_*` seams with
`monkeypatch.setattr` (pytest undoes every patch after the test - no module state is left poked), then
each test asserts the ROUTER's composition. The slices DELEGATE to source handlers; that cross-tool
wiring is proven by live validation, not by re-mocking each handler's internals. New rich reads
(cam_get, doc_get, ...) copy this shape.

Pinned: the DEFAULT call returns only the orientation slice (mode summary + health + tree_summary) and
NONE of the heavy slices; each include= adds exactly its slice; the default note advertises the
remaining slices; unknown include errors; no-active-design guards.
"""

import json

import pytest

from conftest import load_tool, error_message

dg = load_tool("design_get")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── ROUTER composition: stub the slice SEAMS (not the source-tool internals) ────────────────────────
#
# design_get's slices DELEGATE to the 5 source handlers — so the router's own job (compose the default,
# add include= slices, advertise the rest, degrade gracefully) is what the unit tests pin. We stub the
# _slice_* functions to fixed payloads and assert the COMPOSITION. The real slice→source-handler
# delegation is proven by live validation (the honest test of cross-tool wiring), not by mocking 5
# handlers' internals (which would recreate the bespoke-fake problem this convention exists to avoid).

@pytest.fixture
def stub_slices(monkeypatch):
    monkeypatch.setattr(dg._common, "design", lambda: object())   # a non-None design
    monkeypatch.setattr(dg, "_slice_mode", lambda d: (
        {"design_type": "parametric", "has_timeline": True, "timeline_feature_count": 4,
         "in_base_feature_edit": False, "can": {"timeline_ops": True}}, None))
    monkeypatch.setattr(dg, "_slice_health", lambda d: (
        {"healthy": True, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []}, None))
    monkeypatch.setattr(dg, "_fingerprint", lambda d: {"bodies": 2, "sketches": 3})
    monkeypatch.setattr(dg, "_slice_tree", lambda d, max_depth, component: (
        {"root": "Root", "max_depth": max_depth, "children": []}, None))
    monkeypatch.setattr(dg, "_slice_timeline", lambda d, include_suppressed, group: (
        {"count": 4, "timeline": []}, None))
    monkeypatch.setattr(dg, "_slice_configurations", lambda d: ({"table_name": "Configs"}, None))


# ── default orientation slice is DENSE + bounded (the core rich-read contract) ──────────────────────

class TestDefaultSlice:
    def test_default_is_the_dense_orientation(self, stub_slices):
        out = _payload(dg.handler())                   # no include=
        # the headline: design_type + feature_count + TIMELINE_healthy + a CONTENT fingerprint.
        # The name is scoped: timeline_healthy is timeline-only (NOT stale refs — that's a tree node's
        # is_out_of_date, and the doc-wide verdict is workspace_orient.is_healthy).
        assert out["design_type"] == "parametric" and out["feature_count"] == 4
        assert out["timeline_healthy"] is True and "healthy" not in out
        assert out["contents"] == {"bodies": 2, "sketches": 3}   # "what IS this model"
        # the HEAVY slices must be absent by default (the anti-flood contract)
        assert "tree" not in out and "timeline" not in out and "configurations" not in out
        assert "mode_detail" not in out                # the full can{} map is opt-in only

    def test_default_omits_noise_when_healthy(self, stub_slices):
        # a healthy design carries no health DETAIL block + no in_base_feature_edit:false
        out = _payload(dg.handler())
        assert "health" not in out                     # 'timeline_healthy: True' says it all
        assert "in_base_feature_edit" not in out       # only present when True

    def test_default_emits_pointers_for_hidden_content(self, monkeypatch, stub_slices):
        # a design WITH parameters gets a pointers block naming param_get/param_set - the inbound
        # breadcrumb to the otherwise-undiscoverable param family.
        monkeypatch.setattr(dg, "_fingerprint", lambda d: {"parameters": 40, "bodies": 2})
        out = _payload(dg.handler())
        assert "param_get" in out["pointers"]["parameters"]

    def test_default_no_pointers_when_only_obvious_content(self, stub_slices):
        # stub_slices' fingerprint is bodies+sketches only (obvious), and no CAM -> no pointers block.
        out = _payload(dg.handler())
        assert "pointers" not in out

    def test_default_points_at_cam_when_cam_present(self, monkeypatch, stub_slices):
        # a CAM document (design_get is blind to its machining state) gets a cam_get breadcrumb.
        monkeypatch.setattr(dg, "_has_cam", lambda d: True)
        out = _payload(dg.handler())
        assert "cam_get" in out["pointers"]["cam"]

    def test_default_surfaces_health_detail_when_unhealthy(self, monkeypatch, stub_slices):
        monkeypatch.setattr(dg, "_slice_health", lambda d: (
            {"healthy": False, "error_count": 1, "warning_count": 0,
             "errors": ["Extrude3"], "warnings": []}, None))
        out = _payload(dg.handler())
        assert out["timeline_healthy"] is False and out["health"]["errors"] == ["Extrude3"]

    def test_default_note_advertises_remaining_slices(self, stub_slices):
        out = _payload(dg.handler())
        assert "include=" in out["note"]               # un-named flags are invisible — must advertise


# ── each include= adds exactly its slice ────────────────────────────────────────────────────────────

class TestIncludeSlices:
    @pytest.mark.parametrize("slice_name,key", [
        ("tree", "tree"),
        ("timeline", "timeline"),
        ("mode", "mode_detail"),
        ("configurations", "configurations"),
    ])
    def test_include_adds_the_slice(self, stub_slices, slice_name, key):
        out = _payload(dg.handler(include=[slice_name]))
        assert key in out

    def test_include_mode_adds_full_capability_map(self, stub_slices):
        out = _payload(dg.handler(include=["mode"]))
        assert "can" in out["mode_detail"]             # the full map, vs the summary in the default

    def test_multiple_includes(self, stub_slices):
        out = _payload(dg.handler(include=["tree", "timeline"]))
        assert "tree" in out and "timeline" in out


class TestFingerprint:
    """The content fingerprint (`_fingerprint`) - the 'what IS this model' counts in the default slice."""

    def _design(self, bodies=0, sketches=0, comps=0, joints=0, asbuilt=0, params=0):
        from types import SimpleNamespace
        c = lambda n: SimpleNamespace(count=n)
        root = SimpleNamespace(bRepBodies=c(bodies), sketches=c(sketches),
                               allOccurrences=c(comps), joints=c(joints), asBuiltJoints=c(asbuilt))
        return SimpleNamespace(rootComponent=root, userParameters=c(params))

    def test_counts_both_joint_collections(self):
        # joints and asBuiltJoints are separate collections; the count must include both.
        fp = dg._fingerprint(self._design(joints=2, asbuilt=3))
        assert fp["joints"] == 5

    def test_as_built_only_still_counts(self):
        # a design whose ONLY joints are as-built must not report 0 joints.
        fp = dg._fingerprint(self._design(asbuilt=1))
        assert fp["joints"] == 1

    def test_counts_user_parameters(self):
        # parameters must appear in the fingerprint - they were previously invisible (the whole reason
        # the param_* family had no inbound breadcrumb).
        fp = dg._fingerprint(self._design(params=40))
        assert fp["parameters"] == 40

    def test_zero_counts_omitted(self):
        fp = dg._fingerprint(self._design(bodies=1))
        assert fp == {"bodies": 1}                 # no joints/sketches/components/parameters when zero


class TestContentPointers:
    """`_content_pointers` - the inbound breadcrumb: a present content class names the tool acting on it."""

    def test_parameters_present_points_at_param_tools(self):
        p = dg._content_pointers({"parameters": 40, "bodies": 2})
        assert "parameters" in p and "param_get" in p["parameters"] and "param_set" in p["parameters"]

    def test_obvious_classes_get_no_pointer(self):
        # bodies/sketches are omitted - every agent already knows model_*/sketch_*; only the hidden
        # families (parameters/joints/components) get a breadcrumb.
        p = dg._content_pointers({"bodies": 5, "sketches": 3})
        assert p == {}

    def test_only_present_classes_pointed(self):
        p = dg._content_pointers({"joints": 3})
        assert set(p) == {"joints"} and "assembly_probe" in p["joints"]

    def test_empty_contents_no_pointers(self):
        assert dg._content_pointers({}) == {} and dg._content_pointers(None) == {}


class TestHasCam:
    """`_has_cam` - a CAM document's machining state is invisible to design_get, so it needs a cam_get
    breadcrumb. Detection is doc.products.itemByProductType('CAMProductType') is not None."""

    def _design(self, cam_product):
        from types import SimpleNamespace
        products = SimpleNamespace(itemByProductType=lambda t: cam_product if t == "CAMProductType" else None)
        return SimpleNamespace(parentDocument=SimpleNamespace(products=products))

    def test_cam_present(self, monkeypatch):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.CAM, "cast", lambda x: x)   # pass-through
        assert dg._has_cam(self._design(cam_product=object())) is True

    def test_no_cam(self, monkeypatch):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.CAM, "cast", lambda x: x)
        assert dg._has_cam(self._design(cam_product=None)) is False

    def test_no_parent_document_is_safe(self):
        from types import SimpleNamespace
        assert dg._has_cam(SimpleNamespace()) is False    # no parentDocument -> False, no raise


class TestTimelineSlice:
    """Timeline slice logic moved into design_get (_entity_type / _object_summary / _slice_timeline)."""

    def _tlobj(self, index=0, name="Extrude1", is_group=False, suppressed=False, rolled_back=False,
               parent_group=None, health=0, message=None, entity_name="ExtrudeFeature"):
        from types import SimpleNamespace
        _Ent = type(entity_name, (), {})
        pg = SimpleNamespace(name=parent_group) if parent_group is not None else None
        return SimpleNamespace(index=index, name=name, isGroup=is_group, isSuppressed=suppressed,
                               isRolledBack=rolled_back, parentGroup=pg, healthState=health,
                               errorOrWarningMessage=message, entity=(None if is_group else _Ent()))

    def test_entity_type_group(self):
        from types import SimpleNamespace
        assert dg._entity_type(SimpleNamespace(isGroup=True)) == "TimelineGroup"

    def test_entity_type_class_name(self):
        assert dg._entity_type(self._tlobj()) == "ExtrudeFeature"

    def test_object_summary_maps_health_label(self):
        assert dg._object_summary(self._tlobj(health=2))["health"] == "error"

    def test_object_summary_message_only_when_present(self):
        assert dg._object_summary(self._tlobj(message="x"))["message"] == "x"
        assert "message" not in dg._object_summary(self._tlobj(message=None))

    def _design_with(self, items, marker=0, groups=()):
        from types import SimpleNamespace
        tl = SimpleNamespace(_items=list(items), markerPosition=marker, timelineGroups=list(groups),
                             count=len(items), item=lambda i, _it=list(items): _it[i])
        return SimpleNamespace(timeline=tl)

    def test_slice_returns_all_with_marker_count(self):
        d = self._design_with([self._tlobj(0, "A"), self._tlobj(1, "B")], marker=2)
        out, err = dg._slice_timeline(d, include_suppressed=True, group="")
        assert err is None and out["count"] == 2 and out["marker_position"] == 2
        assert [o["name"] for o in out["timeline"]] == ["A", "B"]

    def test_slice_include_suppressed_false(self):
        d = self._design_with([self._tlobj(0, "Live"), self._tlobj(1, "Hid", suppressed=True)])
        out, _ = dg._slice_timeline(d, include_suppressed=False, group="")
        assert out["returned"] == 1 and out["count"] == 2

    def test_slice_group_filter(self):
        d = self._design_with([self._tlobj(0, "A", parent_group="W"),
                               self._tlobj(1, "B", parent_group="F")])
        out, _ = dg._slice_timeline(d, include_suppressed=True, group="W")
        assert [o["name"] for o in out["timeline"]] == ["A"]

    def test_slice_summary_states_and_exceptions(self):
        # the timeline slice leads with a states tally + exceptions = the FAILED features
        # (error/warning health). A suppressed (intentional) feature is NOT an exception.
        d = self._design_with([self._tlobj(0, "Good", health=0),
                               self._tlobj(1, "Bad", health=2),                    # error
                               self._tlobj(2, "Warned", health=1),                 # warning
                               self._tlobj(3, "Off", suppressed=True, health=3)])  # suppressed
        out, _ = dg._slice_timeline(d, include_suppressed=True, group="")
        s = out["summary"]
        assert s["states"]["error"] == 1 and s["states"]["warning"] == 1 and s["states"]["healthy"] == 1
        names = {e["name"] for e in s["exceptions"]}
        assert names == {"Bad", "Warned"}                # the failures; suppressed excluded

    def test_slice_no_timeline_errors(self):
        from types import SimpleNamespace
        class _NoTL:
            @property
            def timeline(self): raise RuntimeError("direct design")
        out, err = dg._slice_timeline(_NoTL(), include_suppressed=True, group="")
        assert out is None and err["isError"] is True


class TestTimelineRazor:
    """Keeping rows terse: a healthy timeline row drops its boring-default fields; an abnormal row keeps
    them and stands out. Tests _terse directly (pure, no design needed)."""

    def test_healthy_row_drops_noise(self):
        row = {"index": 1, "name": "Extrude1", "type": "ExtrudeFeature", "is_group": False,
               "is_suppressed": False, "is_rolled_back": False, "parent_group": None, "health": "healthy"}
        out = dg.terse(row, dg._TIMELINE_NOISE)
        assert out == {"index": 1, "name": "Extrude1", "type": "ExtrudeFeature"}

    def test_abnormal_row_keeps_its_flags(self):
        row = {"index": 2, "name": "Extrude2", "type": "ExtrudeFeature", "is_suppressed": True,
               "is_rolled_back": False, "health": "error"}
        out = dg.terse(row, dg._TIMELINE_NOISE)
        assert out["is_suppressed"] is True and out["health"] == "error"   # the interesting bits pop
        assert "is_rolled_back" not in out                                  # the boring one still dropped

    def test_tree_scope_params_pass_through(self, stub_slices):
        out = _payload(dg.handler(include=["tree"], max_depth=5))
        assert out["tree"]["max_depth"] == 5           # filter args reach the slice

    def test_configurations_degrades_for_non_configured_design(self, monkeypatch, stub_slices):
        # a non-configured design errors in the source -> design_get degrades to a marker, not a failure
        monkeypatch.setattr(dg, "_slice_configurations",
                            lambda d: (None, {"isError": True, "message": "not a configured design"}))
        out = _payload(dg.handler(include=["configurations"]))
        assert out["configurations"]["configured"] is False


# ── guards ──────────────────────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_include_errors(self, monkeypatch):
        monkeypatch.setattr(dg._common, "design", lambda: object())   # a design, so we reach the check
        res = dg.handler(include=["bogus"])
        msg = error_message(res)
        assert "bogus" in msg.lower() or "unknown" in msg.lower()

    def test_no_active_design_guard(self, monkeypatch):
        monkeypatch.setattr(dg._common, "design", lambda: None)
        res = dg.handler()
        assert error_message(res)


# ── include normalization (pure, no design needed) ─────────────────────────────────────────────────

class TestNormalizeInclude:
    def test_none_empty(self):
        assert dg._normalize_include(None) == [] and dg._normalize_include("") == []

    def test_comma_string(self):
        assert dg._normalize_include("tree, timeline") == ["tree", "timeline"]

    def test_list_lowercased(self):
        assert dg._normalize_include(["Tree", "TIMELINE"]) == ["tree", "timeline"]


class TestRootBodies:
    """The tree walks occurrences, so bodies directly in the ROOT component would be invisible
    without _root_body_names (a root body isn't a jointable occurrence — the agent must be told it
    exists). Regression: design_get(tree) omitted root-level bodies entirely."""

    def test_root_body_names_lists_direct_bodies(self):
        from types import SimpleNamespace

        def _coll(items):
            return SimpleNamespace(count=len(items), item=lambda i: items[i])

        body = SimpleNamespace(name="RootBlock")
        root = SimpleNamespace(bRepBodies=_coll([body]))
        assert dg._root_body_names(root) == ["RootBlock"]

    def test_no_root_bodies_returns_empty(self):
        from types import SimpleNamespace
        root = SimpleNamespace(bRepBodies=SimpleNamespace(count=0, item=lambda i: None))
        assert dg._root_body_names(root) == []
