"""Unit tests for ``model_base_feature.py`` - opening and closing a base-feature edit scope.

Pinned: start captures the hidden scope with its document identity; finish selects only the active
document's captured objects, keeps any unknown-owner or failed-close handle, and never gates on mode.
An explicit name must resolve before mutation. The mode-guard rejection names PARAMETRIC, and the
capability map get_mode_handler is derived from the same reader that guard runs on.
"""

import json

import pytest

from conftest import FakeBaseFeature, FakeFeatures, FakeTimeline, MakeDesign, load_tool

dm = load_tool("model_base_feature")
dc = load_tool("_design_common")


def _wire_modes(monkeypatch):
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion, "BaseFeature", _FakeBaseFeature)


class _FakeBaseFeature(FakeBaseFeature):
    """The shared base feature, hiding itself through the back-reference its collection sets on
    add(): while it is in edit the API drops it from count and itemByName, so the only handle to it
    is the object add() returned."""
    def __init__(self, name="BaseFeature1"):
        super().__init__(name=name)
        self.editing = False
        self.start_returns = True
        self.finish_count = 0
        self.deleted = False
        self._coll = None        # set by _Coll.add() so edit can hide/show this item

    def startEdit(self):
        self.editing = True
        if self._coll is not None:
            self._coll._hide(self)        # the live API hides an in-edit base feature
        return self.start_returns

    def finishEdit(self):
        self.editing = False
        self.finish_count += 1
        if self._coll is not None:
            self._coll._show(self)        # re-enumerable once the edit closes
        return True

    def deleteMe(self):
        self.deleted = True
        if self._coll is not None:
            self._coll._remove(self)
        return True


class _Coll:
    """Counted collection with itemByName + add(). Mirrors the live API: an in-edit base feature is
    HIDDEN (not in count / itemByName) - see _FakeBaseFeature."""
    def __init__(self, items=()):
        self._items = list(items)
        self._hidden = []
        self.added = []
        self.add_returns = None  # if set, add() returns this instead of a fresh base feature
        for it in self._items:
            if isinstance(it, _FakeBaseFeature):
                it._coll = self

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def itemByName(self, n):
        for it in self._items:
            if getattr(it, "name", None) == n:
                return it
        return None

    def add(self):
        if self.add_returns is not None:
            bf = self.add_returns
            if isinstance(bf, _FakeBaseFeature):    # a real fake: wire it up; a bool/None models add() failing
                bf._coll = self
                self._items.append(bf)
                self.added.append(bf)
            return bf
        bf = _FakeBaseFeature(name=f"BaseFeature{len(self._items) + 1}")
        bf._coll = self
        self._items.append(bf)
        self.added.append(bf)
        return bf

    def _hide(self, bf):
        if bf in self._items:
            self._items.remove(bf)
            self._hidden.append(bf)

    def _show(self, bf):
        if bf in self._hidden:
            self._hidden.remove(bf)
            self._items.append(bf)

    def _remove(self, bf):
        if bf in self._items:
            self._items.remove(bf)
        if bf in self._hidden:
            self._hidden.remove(bf)


class _Features(FakeFeatures):
    """comp.features carrying the baseFeatures collection this tool opens a scope in."""
    def __init__(self, base_features):
        super().__init__(base_features=base_features)


class _Comp:
    def __init__(self, name="Comp", base_features=None):
        self.name = name
        self.features = _Features(base_features if base_features is not None else _Coll())


class _Timeline(FakeTimeline):
    """The shared timeline sized to `count` entries - the one read this tool takes off it."""
    def __init__(self, count):
        super().__init__(items=[None] * count)


class FakeDesign(MakeDesign):
    """The shared design under this tool's reads: designType, the activeEditObject a scope check
    looks at, and a root whose features carry baseFeatures. `no_timeline` is the design whose
    timeline does not read at all."""
    def __init__(self, design_type=1, timeline_count=0, base_features=None,
                 edit_object=None, no_timeline=False):
        bf = base_features if base_features is not None else _Coll()
        super().__init__(comp=_Comp("Root", base_features=bf), design_type=design_type,
                         active_edit_object=edit_object,
                         timeline=None if no_timeline else _Timeline(timeline_count))


def _install(monkeypatch, design, document_handle="session:test"):
    """Point the shared _common.design/target_component at `design`."""
    _wire_modes(monkeypatch)
    app = type("A", (), {"activeProduct": design})()
    monkeypatch.setattr(dm._common, "app", app)
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, FakeDesign) else None)
    monkeypatch.setattr(dm._inputs._common, "design", lambda: design)
    monkeypatch.setattr(dm._inputs._common, "target_component", lambda d: d.rootComponent)
    monkeypatch.setattr(dm._write_guard, "active_document_handle", lambda: document_handle)
    return design


def _open_handles():
    """The base-feature handles retained in the document-owned scope store."""
    return [bf for _owner, bf in dm._OPEN_BASE_FEATURES]


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


@pytest.fixture(autouse=True)
def _reset_open_scopes():
    """The captured-open-scope list is module state; clear it between tests so cases don't leak
    open scopes into each other."""
    dm._OPEN_BASE_FEATURES.clear()
    yield
    dm._OPEN_BASE_FEATURES.clear()


class TestBaseFeature:
    def test_no_active_design(self, monkeypatch):
        _install(monkeypatch, None)
        res = dm.handler(action="start")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_refused_in_direct_names_parametric(self, monkeypatch):
        # refusing in a direct design, the error names PARAMETRIC as the requirement (not the inverse).
        _install(monkeypatch, FakeDesign(design_type=0, no_timeline=True))
        res = dm.handler(action="start")
        assert res["isError"] is True
        assert "needs parametric mode" in res["message"]
        assert "in direct mode" in res["message"]

    def test_bad_action(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1))
        res = dm.handler(action="dance")
        assert res["isError"] is True and "must be one of" in res["message"]

    def test_the_action_enum_matches_the_action_tuple(self):
        # Catches a HAND-EDITED schema drifting from the tuple the handler guards on.
        assert dm.tool.input_schema["properties"]["action"]["enum"] == list(dm._ACTIONS)

    def test_every_advertised_action_dispatches(self, monkeypatch):
        # A member the guard admits but no arm reads falls through to the finish path, so the wire
        # would offer an action that CLOSES the scope instead of doing what it names.
        for name in dm._ACTIONS:
            _install(monkeypatch, FakeDesign(design_type=1))
            out = _payload(dm.handler(action=name))
            assert out["action"] == name, name

    def test_capability_map_matches_modeguard(self, monkeypatch):
        # the non-drift guarantee: the mode read's can{} is derived from the SAME reader this guard
        # uses, so a design the guard admits reads base_feature_scope true.
        des = _install(monkeypatch, FakeDesign(design_type=1, timeline_count=1))
        out = _payload(dc.get_mode_handler())
        good, _ = dm._PARAMETRIC_GUARD.check(des)
        assert good is True and out["can"]["base_feature_scope"] is True

    def test_start_opens_a_scope(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        out = _payload(dm.handler(action="start"))
        assert out["editing"] is True
        bf = des.rootComponent.features.baseFeatures.added[-1]
        assert bf.editing is True                 # startEdit() actually called
        assert out["base_feature"] == bf.name
        # the open scope was CAPTURED (the only handle to it - it is now invisible to enumeration)
        assert out["open_scope_count"] == 1
        assert bf in _open_handles()

    def test_start_names_the_base_feature(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        out = _payload(dm.handler(action="start", base_feature="MeshScope"))
        bf = des.rootComponent.features.baseFeatures.added[-1]
        assert bf.name == "MeshScope" and out["base_feature"] == "MeshScope"

    def test_start_errors_and_cleans_up_when_startEdit_returns_false(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        bf = _FakeBaseFeature()
        bf.start_returns = False
        des.rootComponent.features.baseFeatures.add_returns = bf
        res = dm.handler(action="start")
        assert res["isError"] is True and "startEdit returned false" in res["message"]
        # the orphan feature is deleted and nothing is captured
        assert bf.deleted is True
        assert dm._OPEN_BASE_FEATURES == []

    def test_start_refuses_before_mutation_when_document_identity_is_unreadable(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1), document_handle=None)
        res = dm.handler(action="start", base_feature="OwnedUnknown")
        assert res["isError"] is True and "document identity is unreadable" in res["message"]
        assert des.rootComponent.features.baseFeatures.added == []
        assert dm._OPEN_BASE_FEATURES == []

    def test_finish_closes_the_captured_open_scope(self, monkeypatch):
        # start opens a scope (which the API then HIDES from enumeration), so finish must close
        # THAT captured object - not try to re-find it by name.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        dm.handler(action="start")
        bf = des.rootComponent.features.baseFeatures.added[-1]
        assert bf.editing is True
        # while open the API hides it: count drops, itemByName is None - re-finding by name cannot work
        assert des.rootComponent.features.baseFeatures.count == 0
        assert des.rootComponent.features.baseFeatures.itemByName(bf.name) is None
        out = _payload(dm.handler(action="finish"))
        assert bf.finish_count == 1 and bf.editing is False        # the captured scope was closed
        assert out["open_scope_count"] == 0
        assert len(out["closed_scopes"]) == 1
        assert dm._OPEN_BASE_FEATURES == []

    def test_finish_closes_multiple_captured_scopes(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        dm.handler(action="start")
        dm.handler(action="start")
        added = des.rootComponent.features.baseFeatures.added
        out = _payload(dm.handler(action="finish"))
        assert len(out["closed_scopes"]) == 2
        assert all(bf.finish_count == 1 for bf in added)
        assert dm._OPEN_BASE_FEATURES == []

    def test_finish_named_also_closes_an_enumerable_feature(self, monkeypatch):
        # a NOT-in-edit base feature named X (e.g. opened elsewhere and already closed) can still be
        # finished by name as a harmless no-op convenience.
        bf = _FakeBaseFeature("Scope1")
        _install(monkeypatch, FakeDesign(design_type=1, base_features=_Coll([bf])))
        out = _payload(dm.handler(action="finish", base_feature="Scope1"))
        assert out["editing"] is False and bf.finish_count == 1

    def test_enumerable_named_finish_discloses_active_captured_scope_remains(self, monkeypatch):
        # Offline response-shape control only: it does not claim Fusion enumerates Old while an
        # independently captured Open scope exists.
        old = _FakeBaseFeature("Old")
        _install(monkeypatch, FakeDesign(design_type=0, base_features=_Coll([old]), no_timeline=True))
        open_scope = _FakeBaseFeature("Open")
        dm._OPEN_BASE_FEATURES.append(("session:test", open_scope))

        out = _payload(dm.handler(action="finish", base_feature="Old"))
        assert old.finish_count == 1 and out["named_finished"] == "Old"
        assert open_scope.finish_count == 0 and _open_handles() == [open_scope]
        assert out["editing"] is None and out["open_scope_count"] == 1
        assert "owned by the active document remains open" in out["note"]
        assert "DIFFERENT session" not in out["note"]

    def test_wrong_name_refuses_before_finish_then_returned_name_closes(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        started = _payload(dm.handler(action="start", base_feature="OwnedScope"))
        bf = des.rootComponent.features.baseFeatures.added[-1]
        refused = dm.handler(action="finish", base_feature="MissingScope")
        assert refused["isError"] is True
        assert "No captured or existing base feature named 'MissingScope'" in refused["message"]
        assert "'OwnedScope'" in refused["message"]
        assert bf.finish_count == 0 and bf.editing is True
        assert _open_handles() == [bf]

        out = _payload(dm.handler(action="finish", base_feature=started["base_feature"]))
        assert bf.finish_count == 1 and bf.editing is False
        assert out["named_finished"] == "OwnedScope"
        assert out["open_scope_count"] == 0

    @pytest.mark.parametrize("with_name", [False, True])
    def test_finish_only_closes_active_document_scopes(self, monkeypatch, with_name):
        _install(monkeypatch, FakeDesign(design_type=1))
        owner = {"handle": "session:b"}
        monkeypatch.setattr(dm._write_guard, "active_document_handle",
                            lambda: owner["handle"])
        scope_a = _FakeBaseFeature("OwnedShared")
        scope_b = _FakeBaseFeature("OwnedShared")
        dm._OPEN_BASE_FEATURES.extend([
            ("session:a", scope_a),
            ("session:b", scope_b),
        ])

        args = {"action": "finish"}
        if with_name:
            args["base_feature"] = "OwnedShared"
        out_b = _payload(dm.handler(**args))
        assert scope_b.finish_count == 1
        assert scope_a.finish_count == 0
        assert _open_handles() == [scope_a]
        assert out_b["open_scope_count"] == 1

        owner["handle"] = "session:a"
        out_a = _payload(dm.handler(**args))
        assert scope_a.finish_count == 1
        assert _open_handles() == []
        assert out_a["open_scope_count"] == 0

    def test_finish_refuses_before_mutation_when_captured_ownership_is_unknown(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1))
        unknown = _FakeBaseFeature("UnknownOwner")
        dm._OPEN_BASE_FEATURES.append((None, unknown))
        res = dm.handler(action="finish")
        assert res["isError"] is True and "no readable document owner" in res["message"]
        assert unknown.finish_count == 0
        assert _open_handles() == [unknown]

    def test_finish_no_open_scope_is_idempotent(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1))
        out = _payload(dm.handler(action="finish"))
        assert out["editing"] is False and out["closed_scopes"] == []

    def test_a_raising_finish_keeps_the_only_handle_to_the_scope(self, monkeypatch):
        # finishEdit RAISES: the scope is not proven closed, and this object is the only way back to
        # it (an open base feature is invisible to enumeration). Popping it would strand the scope
        # open for the rest of the session.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        dm.handler(action="start")
        bf = des.rootComponent.features.baseFeatures.added[-1]

        def boom():
            raise RuntimeError("finishEdit blew up")

        bf.finishEdit = boom
        out = _payload(dm.handler(action="finish"))
        assert out["closed_scopes"] == []
        assert out["unclosed_scopes"] == [{"name": bf.name, "finished": None,
                                           "error": "finishEdit blew up"}]
        assert out["editing"] is None                  # not a confirmed False
        assert out["open_scope_count"] == 1
        assert _open_handles() == [bf]                 # the handle survives the call
        assert "may still be" in out["note"]
        assert "handles are KEPT" in out["note"]
        assert "remains open" not in out["note"]

    def test_a_kept_handle_still_closes_the_scope_on_a_retry(self, monkeypatch):
        # what keeping the handle buys: the caller can finish again and actually close it.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        dm.handler(action="start")
        bf = des.rootComponent.features.baseFeatures.added[-1]
        bf.finishEdit = lambda: (_ for _ in ()).throw(RuntimeError("transient"))
        dm.handler(action="finish")
        assert _open_handles() == [bf]
        del bf.finishEdit                              # the retry meets a working finishEdit
        out = _payload(dm.handler(action="finish"))
        assert bf.finish_count == 1 and bf.editing is False
        assert len(out["closed_scopes"]) == 1 and out["open_scope_count"] == 0

    def test_a_false_finish_keeps_the_handle_too(self, monkeypatch):
        # finishEdit returning False is the platform saying it did NOT close: same unproven scope,
        # same kept handle - publishing finished:false while dropping the object loses it anyway.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        dm.handler(action="start")
        bf = des.rootComponent.features.baseFeatures.added[-1]
        bf.finishEdit = lambda: False
        out = _payload(dm.handler(action="finish"))
        assert out["closed_scopes"] == []
        assert out["unclosed_scopes"] == [{"name": bf.name, "finished": False}]
        assert out["editing"] is None
        assert _open_handles() == [bf]
        assert "may still be" in out["note"] and "handles are KEPT" in out["note"]
        assert "remains open" not in out["note"]

    def test_one_failing_scope_does_not_hold_the_others_open(self, monkeypatch):
        # two scopes, the inner one raising: the outer still closes and only the failing handle is
        # kept, so one wedged scope cannot strand the rest.
        des = _install(monkeypatch, FakeDesign(design_type=1))
        dm.handler(action="start")
        dm.handler(action="start")
        first, second = des.rootComponent.features.baseFeatures.added[-2:]
        second.finishEdit = lambda: (_ for _ in ()).throw(RuntimeError("inner stuck"))
        out = _payload(dm.handler(action="finish"))
        assert [c["name"] for c in out["closed_scopes"]] == [first.name]
        assert first.finish_count == 1
        assert _open_handles() == [second]
        assert out["open_scope_count"] == 1

    def test_a_raising_named_finish_is_disclosed_not_reported_as_finished(self, monkeypatch):
        # the by-name convenience path publishes the name it finished; a raise means it finished
        # nothing, so the name is null and the platform's own text is handed back.
        bf = _FakeBaseFeature("Scope1")
        _install(monkeypatch, FakeDesign(design_type=1, base_features=_Coll([bf])))
        bf.finishEdit = lambda: (_ for _ in ()).throw(RuntimeError("named finish blew up"))
        out = _payload(dm.handler(action="finish", base_feature="Scope1"))
        assert out["named_finished"] is None
        assert out["named_finish_error"] == "named finish blew up"

    def test_finish_works_while_design_reads_direct(self, monkeypatch):
        # while a scope is open the design READS direct; finish must NOT gate on mode. We simulate the
        # captured open scope on a design reading direct and confirm finish still closes it.
        des = _install(monkeypatch, FakeDesign(design_type=0, no_timeline=True))
        bf = _FakeBaseFeature("Open")
        bf._coll = des.rootComponent.features.baseFeatures
        dm._OPEN_BASE_FEATURES.append(("session:test", bf))
        out = _payload(dm.handler(action="finish"))
        assert bf.finish_count == 1 and out["open_scope_count"] == 0
