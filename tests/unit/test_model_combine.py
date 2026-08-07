"""Unit tests for ``combine.py`` — boolean join/cut/intersect of solid bodies.

Pinned: the operation guard, target/tool body resolution by name, the same-body guard, missing-tool
reporting, the name-list-vs-comma-string parsing, and that the FeatureOperations + isKeepToolBodies
are set on the CombineInput.
"""

import json

from conftest import assert_no_active_design, entity_proxy, go_stale, load_tool

cb = load_tool("model_combine")


class FakeBody:
    """`parent_component` is the component the body actually LIVES in - which a combine's census must
    be scoped to. Measured: a combine whose target and tool both sit in a sub-component moves nothing
    the ACTIVE component can see.

    Carries an entityToken because that is what identifies a body ACROSS two references to it: two
    itemByName reads of one body are distinct objects with EQUAL tokens (measured), so a guard that
    compares only identity would never fire."""
    def __init__(self, name, parent_component=None, entity_token=None):
        self.name = name
        self.entityToken = entity_token or "BTOK::" + name
        if parent_component is not None:
            self.parentComponent = parent_component


class FakeBodies:
    def __init__(self, names):
        self._b = [FakeBody(n) for n in names]
    @property
    def count(self):
        return len(self._b)
    def itemByName(self, name):
        # A FRESH wrapper per read, as the platform hands back - two reads of one body are distinct
        # objects sharing a token. Returning the same object would make an identity-only guard look
        # correct.
        for b in self._b:
            if b.name == name:
                return entity_proxy(b)
        return None


def safe_token(b):
    """A body's entityToken - what identifies it across two references (see entity_proxy)."""
    return getattr(b, "entityToken", None)


class FakeCombineInput:
    def __init__(self, target, tools):
        self.target = target
        self.tools = tools
        self.operation = None
        self.isKeepToolBodies = False
        self.isNewComponent = False


class FakeCombineFeatures:
    """`returns_nothing` models the return with NO feature object in it - the measured direct-mode
    shape, where the boolean still lands."""
    def __init__(self, returns_nothing=False, host=None):
        self.last_input = None
        self.comp = None              # wired by _install so add() can consume tool bodies
        self.host = host              # the component the bodies live in, when not the active one
        self.returns_nothing = returns_nothing
    def createInput(self, target, tools):
        self.last_input = FakeCombineInput(target, tools)
        return self.last_input
    def add(self, inp):
        # a real Combine CONSUMES the tool bodies (unless isKeepToolBodies) - remove them from the
        # component THE BODIES LIVE IN (`host`, defaulting to the active one) so bodies_remaining
        # pins the post-combine read-back, not a static echo
        host = self.host if self.host is not None else self.comp
        if host is not None and not inp.isKeepToolBodies:
            # matched by TOKEN: the collection holds the originals while `inp.tools` holds the
            # wrappers itemByName handed out, so `in` (identity) would never find them
            doomed = {safe_token(b) for b in inp.tools._i}
            host.bRepBodies._b = [b for b in host.bRepBodies._b
                                  if safe_token(b) not in doomed]
        # a consumed body's proxy stops answering its identity reads - the names belong to the
        # pre-mutation capture, not to a post-combine projection
        go_stale(inp.target, *list(inp.tools._i))
        if self.returns_nothing:
            return None
        return type("F", (), {"name": "Combine1"})()


class FakeComp:
    def __init__(self, names, cf):
        self.name = "Comp"
        self.bRepBodies = FakeBodies(names)
        self.features = type("F", (), {"combineFeatures": cf})()


class FakeDesign:
    def __init__(self, comp, design_type=None):
        self.activeComponent = comp
        self.rootComponent = comp
        if design_type is not None:
            # the modelling mode current_design_type reads (1 parametric, 0 direct); absent, the
            # design reports neither, which is the 'unknown' mode
            self.designType = design_type


def _install(body_names, cf=None, design_type=None):
    cf = cf if cf is not None else FakeCombineFeatures()
    comp = FakeComp(body_names, cf)
    cf.comp = comp
    design = FakeDesign(comp, design_type)
    cb.app = type("A", (), {"activeProduct": design})()
    cb._common.app = cb.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # BodyRef inputs resolve via _common.design()/target_component() — point them at the fake comp
    # (the app-reference seam: input-kinds use _common, not cb.app). Names are short -> name path.
    cb._inputs._common.design = lambda: design
    cb._inputs._common.target_component = lambda d: comp
    fo = adsk.fusion.FeatureOperations
    for n in ("JoinFeatureOperation", "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)

    class FakeColl:
        def __init__(self):
            self._i = []
        def add(self, x):
            self._i.append(x)
        @property
        def count(self):
            return len(self._i)
    adsk.core.ObjectCollection.create = staticmethod(lambda: FakeColl())
    return cf


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_unknown_operation(self):
        _install(["A", "B"])
        res = cb.handler(target="A", tools=["B"], operation="weld")
        assert res["isError"] is True and "Unknown operation" in res["message"]

    def test_target_not_found(self):
        # BodyRef ('target') now owns the not-found error
        _install(["A", "B"])
        res = cb.handler(target="Nope", tools=["B"])
        assert res["isError"] is True and "no body or component named 'Nope'" in res["message"]

    def test_no_tools(self):
        # BodyRefList ('tools', required) owns the empty error
        _install(["A"])
        res = cb.handler(target="A", tools=[])
        assert res["isError"] is True and "tools" in res["message"] and "at least one body" in res["message"]

    def test_tool_not_found(self):
        _install(["A", "B"])
        res = cb.handler(target="A", tools=["B", "X"])
        assert res["isError"] is True and "no body or component named 'X'" in res["message"]

    def test_tool_same_as_target(self):
        _install(["A", "B"])
        res = cb.handler(target="A", tools=["A"])
        assert res["isError"] is True and "same as the target" in res["message"]

    def test_same_body_refused_when_the_two_references_are_distinct_wrappers(self):
        # The target and the tool are resolved by two SEPARATE reads, so they are different Python
        # objects sharing one entityToken (measured: two itemByName reads of one body are distinct
        # objects with equal tokens). An identity-only guard reads False here and lets a body be
        # combined into itself.
        _install(["A", "B"])
        tgt, tool = cb._TARGET.resolve("A")[0], cb._TOOLS.resolve(["A"])[0][0]
        assert tgt is not tool                       # the fixture really is the hard case
        assert tgt.entityToken == tool.entityToken
        res = cb.handler(target="A", tools=["A"])
        assert res["isError"] is True and "same as the target" in res["message"]


class TestCombine:
    def test_join_sets_operation(self):
        cf = _install(["Base", "Boss"])
        out = _payload(cb.handler(target="Base", tools=["Boss"], operation="join"))
        assert out["combined"] is True and out["operation"] == "join"
        assert cf.last_input.operation == "JoinFeatureOperation"

    def test_cut_sets_operation(self):
        cf = _install(["Part", "Drill"])
        _payload(cb.handler(target="Part", tools=["Drill"], operation="cut"))
        assert cf.last_input.operation == "CutFeatureOperation"

    def test_intersect_sets_operation(self):
        cf = _install(["A", "B"])
        out = _payload(cb.handler(target="A", tools=["B"], operation="intersect"))
        assert cf.last_input.operation == "IntersectFeatureOperation"
        assert out["operation"] == "intersect"

    def test_operation_case_insensitive(self):
        cf = _install(["A", "B"])
        _payload(cb.handler(target="A", tools=["B"], operation="CUT"))
        assert cf.last_input.operation == "CutFeatureOperation"

    def test_multiple_tools_all_added(self):
        cf = _install(["T", "a", "b", "c"])
        out = _payload(cb.handler(target="T", tools=["a", "b", "c"]))
        assert out["tools"] == ["a", "b", "c"]
        assert cf.last_input.tools.count == 3

    def test_bodies_remaining_reports_count(self):
        # the fake's combine CONSUMES the tool body: 2 bodies before, 1 after. bodies_remaining must
        # be the POST-combine read-back (1), not an echo of the pre-combine count.
        cf = _install(["T", "a"])
        out = _payload(cb.handler(target="T", tools=["a"]))
        assert out["bodies_remaining"] == 1

    def test_keep_tools_defaults_false(self):
        cf = _install(["T", "a"])
        out = _payload(cb.handler(target="T", tools=["a"]))
        assert cf.last_input.isKeepToolBodies is False
        assert out["kept_tools"] is False

    def test_comma_string_tools_parsed(self):
        cf = _install(["T", "a", "b"])
        out = _payload(cb.handler(target="T", tools="a, b"))
        assert out["tools"] == ["a", "b"]
        assert cf.last_input.tools.count == 2

    def test_keep_tools_flag(self):
        cf = _install(["T", "a"])
        out = _payload(cb.handler(target="T", tools=["a"], keep_tools=True))
        assert cf.last_input.isKeepToolBodies is True
        assert out["bodies_remaining"] == 2       # kept tools -> nothing consumed

    def test_new_component_flag(self):
        cf = _install(["T", "a"])
        out = _payload(cb.handler(target="T", tools=["a"], new_component=True))
        assert cf.last_input.isNewComponent is True
        assert out["new_component"] is True


# ── honesty: failed/absent mutation must surface as isError, never a false ok ─
# (the paths test_model_mirror.py / test_model_shell.py treat as mandatory)

class TestHonesty:
    def test_add_returning_none_is_error(self):
        cf = _install(["T", "a"])
        cf.add = lambda inp: None
        res = cb.handler(target="T", tools=["a"])
        assert res["isError"] is True and "no feature" in res["message"].lower()


# ── DIRECT mode: combineFeatures.add returns nothing while the boolean LANDS (measured) ──────

class TestDirectModeNoFeature:
    def test_direct_none_with_a_changed_body_census_is_ok(self):
        # the measured shape: a join took 2 bodies to 1 and handed back no feature
        _install(["T", "a"], cf=FakeCombineFeatures(returns_nothing=True), design_type=0)
        out = _payload(cb.handler(target="T", tools=["a"], operation="join"))
        assert out["combined"] is True and out["bodies_remaining"] == 1

    def test_direct_none_publishes_no_feature_name(self):
        _install(["T", "a"], cf=FakeCombineFeatures(returns_nothing=True), design_type=0)
        out = _payload(cb.handler(target="T", tools=["a"], operation="join"))
        assert "feature" not in out
        assert out["no_timeline_feature"] is True
        assert "DIRECT mode" in out["note"]

    def test_names_come_from_the_pre_mutation_capture(self):
        # a join CONSUMES the tool body: projecting b.name after add() publishes nulls for bodies
        # that resolved perfectly well.
        _install(["T", "a"], cf=FakeCombineFeatures(returns_nothing=True), design_type=0)
        out = _payload(cb.handler(target="T", tools=["a"], operation="join"))
        assert out["tools"] == ["a"] and out["target"] == "T"

    def test_direct_none_with_nothing_changed_is_an_error(self):
        # keep_tools consumes nothing, and this target's volume is unreadable - so neither signal
        # moved and the call must not report a success it cannot show.
        _install(["T", "a"], cf=FakeCombineFeatures(returns_nothing=True), design_type=0)
        res = cb.handler(target="T", tools=["a"], operation="join", keep_tools=True)
        assert res["isError"] is True and "nothing it could measure changed" in res["message"]

    def test_the_nothing_changed_error_only_claims_what_it_read(self):
        # The body count WAS read (2, unchanged); the volume was NOT - the sentence must say each
        # of those, and never report an unread signal as an observed sameness.
        _install(["T", "a"], cf=FakeCombineFeatures(returns_nothing=True), design_type=0)
        msg = cb.handler(target="T", tools=["a"], operation="join", keep_tools=True)["message"]
        assert "still holds 2 bodies" in msg
        assert "'T' volume could not be read" in msg
        assert "measures the same volume" not in msg
        assert "None bodies" not in msg
        # direct mode: no timeline entry to delete
        assert "design_delete_feature" not in msg and "undo in Fusion" in msg

    def test_parametric_none_stays_an_error(self):
        # Even with the census showing the join: a None feature in a PARAMETRIC design is
        # unmeasured as a success, so it is refused.
        _install(["T", "a"], cf=FakeCombineFeatures(returns_nothing=True), design_type=1)
        res = cb.handler(target="T", tools=["a"], operation="join")
        assert res["isError"] is True and "returned no feature" in res["message"]
        assert "DIRECT mode" not in res["message"]

    def test_census_follows_the_target_body_not_the_active_component(self, monkeypatch):
        # MEASURED: a combine whose target AND tool both live in a sub-component leaves the ACTIVE
        # component's body count untouched (root 3 -> 3 throughout), so a census scoped to the
        # active component is BLIND and would call this landed join a no-op.
        host = FakeComp(["T", "a"], None)
        host.name = "SubPart"
        cf = FakeCombineFeatures(returns_nothing=True, host=host)
        _install(["Other1", "Other2", "Other3"], cf=cf, design_type=0)
        # the resolved bodies are the HOST's, and they carry it as their parentComponent
        tgt, tool = host.bRepBodies._b
        for b in (tgt, tool):
            b.parentComponent = host
        monkeypatch.setattr(cb._TARGET, "resolve", lambda raw: (tgt, None))
        monkeypatch.setattr(cb._TOOLS, "resolve", lambda raw: ([tool], None))
        out = _payload(cb.handler(target="T", tools=["a"], operation="join"))
        assert out["combined"] is True
        assert out["bodies_remaining"] == 1        # SubPart 2 -> 1, not the active root's 3

    def test_add_raising_surfaces_as_error(self):
        cf = _install(["T", "a"])

        def _boom(inp):
            raise RuntimeError("bodies do not intersect")

        cf.add = _boom
        res = cb.handler(target="T", tools=["a"], operation="cut")
        assert res["isError"] is True
        assert "Combine failed" in res["message"] and "bodies do not intersect" in res["message"]

    def test_no_active_design(self):
        _install(["T", "a"])
        assert_no_active_design(cb, cb.handler, target="T", tools=["a"])


# ── body-split: a cut/intersect that DISCONNECTS the single target warns naming the pieces ──

class _ResultBodies:
    def __init__(self, names):
        self._n = list(names)
    @property
    def count(self):
        return len(self._n)
    def item(self, i):
        return type("B", (), {"name": self._n[i]})()


def _feature_with_bodies(names):
    return type("F", (), {"name": "Combine1", "bodies": _ResultBodies(names)})()


class TestBodySplit:
    def test_cut_disconnecting_target_warns(self):
        # CombineFeature.bodies returns >1 result body for a cut that split the target (tools were
        # consumed, so the extra body is a disconnected piece, not another target).
        cf = _install(["Ring", "Bore"])
        cf.add = lambda inp: _feature_with_bodies(["Ring", "Ring1"])
        out = _payload(cb.handler(target="Ring", tools=["Bore"], operation="cut"))
        assert out["body_split"] == ["Ring", "Ring1"]
        assert "DISCONNECTED" in out["note"]

    def test_single_result_body_no_split_warning(self):
        cf = _install(["A", "B"])
        cf.add = lambda inp: _feature_with_bodies(["A"])
        out = _payload(cb.handler(target="A", tools=["B"], operation="cut"))
        assert "body_split" not in out

    def test_join_with_several_bodies_not_flagged_as_split(self):
        # the split warning is scoped to cut/intersect - a join is never a disconnection.
        cf = _install(["A", "B"])
        cf.add = lambda inp: _feature_with_bodies(["A", "B"])
        out = _payload(cb.handler(target="A", tools=["B"], operation="join"))
        assert "body_split" not in out
