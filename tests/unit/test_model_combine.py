"""Unit tests for ``combine.py`` — boolean join/cut/intersect of solid bodies.

Pinned: the operation guard, target/tool body resolution by name, the same-body guard, missing-tool
reporting, the name-list-vs-comma-string parsing, and that the FeatureOperations + isKeepToolBodies
are set on the CombineInput.
"""

import json
import types

from conftest import (_NamedCollection, assert_no_active_design, body_proxy, entity_proxy, go_stale,
                      load_tool)

cb = load_tool("model_combine")


class _Lumps:
    def __init__(self, count):
        self.count = count


class FakeBody:
    """`parent_component` is the component the body actually LIVES in - which a combine's census must
    be scoped to. Measured: a combine whose target and tool both sit in a sub-component moves nothing
    the ACTIVE component can see.

    Carries an entityToken because that is what identifies a body ACROSS two references to it: two
    itemByName reads of one body are distinct objects with EQUAL tokens (measured), so a guard that
    compares only identity would never fire."""
    def __init__(self, name, parent_component=None, entity_token=None, lumps=None):
        self.name = name
        self.entityToken = entity_token or "BTOK::" + name
        if parent_component is not None:
            self.parentComponent = parent_component
        if lumps is not None:
            # BRepBody.lumps: how many DISCONNECTED pieces the body holds. A body left without it
            # stands for one whose lump count cannot be read (a mesh body carries none at all).
            self.lumps = _Lumps(lumps)


class FakeBodies:
    def __init__(self, names, lumps=None):
        lumps = lumps or {}
        self._b = [FakeBody(n, lumps=lumps.get(n)) for n in names]
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
    """`ignores` names properties whose assignment the platform silently DROPS - the SWIG-proxy
    shape set_verified exists to catch (the assignment lands on a dead attribute while the object
    keeps its API default, and no exception is raised)."""
    def __init__(self, target, tools, ignores=()):
        object.__setattr__(self, "_ignores", set(ignores))
        self.target = target
        self.tools = tools
        self.operation = None
        self.isKeepToolBodies = False
        self.isNewComponent = False

    def __setattr__(self, name, value):
        if name in self._ignores:
            return
        object.__setattr__(self, name, value)


class FakeCombineFeatures:
    """`returns_nothing` models the return with NO feature object in it - the measured direct-mode
    shape, where the boolean still lands."""
    def __init__(self, returns_nothing=False, host=None, ignores=()):
        self.last_input = None
        self.comp = None              # wired by _install so add() can consume tool bodies
        self.host = host              # the component the bodies live in, when not the active one
        self.returns_nothing = returns_nothing
        self.ignores = ignores
        self.add_calls = 0
    def createInput(self, target, tools):
        self.last_input = FakeCombineInput(target, tools, self.ignores)
        return self.last_input
    def add(self, inp):
        self.add_calls += 1
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
    def __init__(self, names, cf, lumps=None):
        self.name = "Comp"
        self.bRepBodies = FakeBodies(names, lumps)
        self.features = type("F", (), {"combineFeatures": cf})()


class FakeDesign:
    def __init__(self, comp, design_type=None):
        self.activeComponent = comp
        self.rootComponent = comp
        if design_type is not None:
            # the modelling mode current_design_type reads (1 parametric, 0 direct); absent, the
            # design reports neither, which is the 'unknown' mode
            self.designType = design_type


def _install(body_names, cf=None, design_type=None, lumps=None):
    cf = cf if cf is not None else FakeCombineFeatures()
    comp = FakeComp(body_names, cf, lumps)
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

    def test_same_body_refused_when_the_tool_is_an_occurrence_PROXY_of_the_target(self):
        # Two wrappers of ONE body carrying DIFFERENT entityTokens: 'Comp:A' names the component's
        # own (native) wrapper, while the bare name resolves to the PLACEMENT. A guard on each
        # wrapper's own token reads them as two bodies and combines a body into itself.
        cf = _install(["A", "B"])
        comp = cb._inputs._common.target_component(None)
        native = comp.bRepBodies.itemByName("A")
        proxy = body_proxy(native, types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1"))
        comp.allOccurrences = [types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1",
                                                     bRepBodies=_NamedCollection([proxy]))]
        assert proxy.entityToken != native.entityToken
        res = cb.handler(target="Comp:A", tools=["A"])
        assert res["isError"] is True and "same as the target" in res["message"]
        assert cf.add_calls == 0                     # rejected before any mutation

    def test_same_body_refused_when_the_TARGET_is_the_proxy_and_the_tool_the_native(self):
        # The REVERSED wrapper arrangement: the bare name puts the PLACEMENT on the target side and
        # the component-qualified form puts the native on the tool side. The guard reads the native
        # token on BOTH sides - a bare-token read on the target side alone misses this pair and
        # combines a body into itself.
        cf = _install(["A", "B"])
        comp = cb._inputs._common.target_component(None)
        native = comp.bRepBodies.itemByName("A")
        proxy = body_proxy(native, types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1"))
        comp.allOccurrences = [types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1",
                                                     bRepBodies=_NamedCollection([proxy]))]
        assert proxy.entityToken != native.entityToken
        res = cb.handler(target="A", tools=["Comp:A"])
        assert res["isError"] is True and "same as the target" in res["message"]
        assert cf.add_calls == 0


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
        self.lumps = None          # the result body's lump count, when the test models one
    @property
    def count(self):
        return len(self._n)
    def item(self, i):
        b = type("B", (), {"name": self._n[i]})()
        if self.lumps is not None:
            b.lumps = _Lumps(self.lumps)
        return b


def _feature_with_bodies(names, lumps=None):
    bodies = _ResultBodies(names)
    bodies.lumps = lumps
    return type("F", (), {"name": "Combine1", "bodies": bodies})()


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


# ── a join that fused nothing ────────────────────────────────────────────────────────────────
#
# MEASURED (parametric, 2705.0.87): joining two DISJOINT solids reports success and the feature's
# result holds BOTH input bodies - nothing is consumed, nothing merges. So several RESULT BODIES is
# the primary nothing-fused verdict. A single result body carrying several LUMPS is the second arm,
# and in DIRECT mode - no feature to count result bodies on - the body census answers instead.

class TestDisjointJoin:
    def test_a_join_that_left_two_separate_bodies_is_the_nothing_fused_verdict(self):
        # The measured live shape for the case this row was filed on (a block 44 mm from its stub).
        cf = _install(["Tensioner", "Stub"], lumps={"Tensioner": 1, "Stub": 1})
        cf.add = lambda inp: _feature_with_bodies(["Tensioner", "Stub"])
        out = _payload(cb.handler(target="Tensioner", tools=["Stub"], operation="join"))
        assert out["disjoint_join"] is True
        assert out["fused"] is False
        assert out["result_body_count"] == 2
        # the warning NAMES the pieces that are still standing
        assert "fused NOTHING" in out["note"]
        assert "left 2 separate bodies (Tensioner, Stub)" in out["note"]
        # the remedy is the parametric one - there IS a timeline feature to remove
        assert "design_delete_feature" in out["note"]

    def test_a_kept_tools_join_claims_no_verdict_from_the_result_count(self):
        # What a KEPT tool body does to the feature's result set is not measured, so a several-body
        # result there may be the kept copies rather than a failed fuse. Silence beats a warning that
        # can fire on a good join.
        cf = _install(["Base", "Boss"], lumps={"Base": 1, "Boss": 1})
        cf.add = lambda inp: _feature_with_bodies(["Base", "Boss"])
        out = _payload(cb.handler(target="Base", tools=["Boss"], operation="join", keep_tools=True))
        assert "disjoint_join" not in out and "WARNING" not in out["note"]

    def test_the_direct_mode_census_catches_a_tool_body_left_standing(self):
        # No feature to count result bodies on. A join that consumed its tool drops the host count by
        # one; the fake here consumes nothing while the volume moves, so the tool is still standing.
        cf = FakeCombineFeatures(returns_nothing=True)
        _install(["T", "a"], cf=cf, design_type=0)
        body = cb._inputs._common.target_component(None).bRepBodies._b[0]
        body.volume = 100.0

        def landed_without_consuming(inp):
            body.volume = 260.0        # the boolean touched the target, so the no-op gate passes
            return None                # ... but the tool body was not consumed

        cf.add = landed_without_consuming
        out = _payload(cb.handler(target="T", tools=["a"], operation="join"))
        assert out["disjoint_join"] is True and out["fused"] is False
        assert out["unfused_tool_bodies"] == 1
        assert "did not fuse into the" in out["note"]
        # direct mode has no timeline entry to delete
        assert "design_delete_feature" not in out["note"] and "undo in Fusion" in out["note"]

    def test_the_direct_mode_census_says_nothing_when_the_tools_were_kept(self):
        # keep_tools means nothing is consumed BY DESIGN, so a standing tool body is not evidence of
        # a failed fuse - the census cannot speak here, and a warning would fire on a good join.
        cf = FakeCombineFeatures(returns_nothing=True)
        _install(["T", "a"], cf=cf, design_type=0)
        body = cb._inputs._common.target_component(None).bRepBodies._b[0]
        body.volume = 100.0

        def landed(inp):
            body.volume = 260.0        # a real fuse: the target grew, the kept tool still stands
            return None

        cf.add = landed
        out = _payload(cb.handler(target="T", tools=["a"], operation="join", keep_tools=True))
        assert "disjoint_join" not in out and "unfused_tool_bodies" not in out
        assert "WARNING" not in out["note"]

    def test_a_join_that_consumed_its_tool_in_direct_mode_is_a_clean_ok(self):
        _install(["T", "a"], cf=FakeCombineFeatures(returns_nothing=True), design_type=0)
        out = _payload(cb.handler(target="T", tools=["a"], operation="join"))
        assert "disjoint_join" not in out and "WARNING" not in out["note"]

    def test_a_multi_lump_single_result_body_is_the_second_arm(self):
        # If the platform ever hands back ONE body holding disconnected lumps, that is also a join
        # that fused nothing - kept covered, with the comparison carried as data.
        cf = _install(["Tensioner", "Stub"], lumps={"Tensioner": 1, "Stub": 1})
        cf.add = lambda inp: _feature_with_bodies(["Tensioner"], lumps=2)
        out = _payload(cb.handler(target="Tensioner", tools=["Stub"], operation="join"))
        assert out["lump_count"] == 2 and out["disjoint_join"] is True
        assert "2-lump body" in out["note"] and "do not touch" in out["note"]
        assert "nothing fused: the result holds the same 2 lumps the inputs did" in out["note"]
        assert out["input_lump_total"] == 2 and out["fused"] is False

    def test_a_multi_lump_input_is_counted_by_its_lumps_not_by_the_body(self):
        # A 2-lump target joined with a 1-lump tool holds THREE lumps between them. Counting BODIES
        # instead of lumps makes the total 2, so a 3-lump result reads as a fuse that happened, and
        # any per-BODY wording of the verdict is false for this shape.
        cf = _install(["Frame", "Clip"], lumps={"Frame": 2, "Clip": 1})
        cf.add = lambda inp: _feature_with_bodies(["Frame"], lumps=3)
        out = _payload(cb.handler(target="Frame", tools=["Clip"], operation="join"))
        assert out["input_lump_total"] == 3
        assert out["fused"] is False
        assert "the result holds the same 3 lumps the inputs did" in out["note"]
        assert "one lump per input body" not in out["note"]

    def test_an_unreadable_input_lump_leaves_the_verdict_unstated(self):
        # One input whose lumps will not read: the total is unknown, so the payload publishes null
        # and no 'fused' verdict at all rather than a comparison against a guessed total.
        cf = _install(["A", "B"], lumps={"A": 1})          # 'B' carries no lumps
        cf.add = lambda inp: _feature_with_bodies(["A"], lumps=2)
        out = _payload(cb.handler(target="A", tools=["B"], operation="join"))
        assert out["disjoint_join"] is True
        assert out["input_lump_total"] is None and "fused" not in out
        assert "nothing fused" not in out["note"]

    def test_a_fused_join_is_a_clean_ok(self):
        cf = _install(["Base", "Boss"], lumps={"Base": 1, "Boss": 1})
        cf.add = lambda inp: _feature_with_bodies(["Base"], lumps=1)
        out = _payload(cb.handler(target="Base", tools=["Boss"], operation="join"))
        assert out["lump_count"] == 1
        assert "disjoint_join" not in out and "fused" not in out
        assert "WARNING" not in out["note"]

    def test_a_partial_fuse_warns_without_claiming_nothing_fused(self):
        # three 1-lump inputs -> a 2-lump result: two of them DID fuse, one is still floating. The
        # warning has to fire, but the "nothing fused" clause would be a false statement here.
        cf = _install(["T", "a", "b"], lumps={"T": 1, "a": 1, "b": 1})
        cf.add = lambda inp: _feature_with_bodies(["T"], lumps=2)
        out = _payload(cb.handler(target="T", tools=["a", "b"], operation="join"))
        assert out["disjoint_join"] is True and out["lump_count"] == 2
        assert "nothing fused" not in out["note"]
        assert out["input_lump_total"] == 3 and out["fused"] is True

    def test_direct_mode_reads_the_lumps_off_the_target(self):
        # No feature object to read result bodies from, so the join's landing place IS the target.
        # Without that fallback a direct-mode disjoint join stays silent - the mode the live
        # occurrences were built in.
        cf = FakeCombineFeatures(returns_nothing=True)
        _install(["T", "a"], cf=cf, design_type=0, lumps={"T": 2, "a": 1})
        out = _payload(cb.handler(target="T", tools=["a"], operation="join"))
        assert out["lump_count"] == 2
        assert out["disjoint_join"] is True
        # direct mode has no timeline entry to delete - the remedy must not send the caller there
        assert "design_delete_feature" not in out["note"] and "undo in Fusion" in out["note"]

    def test_an_unreadable_lump_count_claims_nothing(self):
        # Bodies with no readable lumps: the payload must carry no lump_count and no warning rather
        # than a fabricated verdict either way.
        cf = _install(["A", "B"])
        cf.add = lambda inp: _feature_with_bodies(["A"])
        out = _payload(cb.handler(target="A", tools=["B"], operation="join"))
        assert "lump_count" not in out and "disjoint_join" not in out

    def test_a_multi_lump_cut_is_not_reported_as_a_disjoint_join(self):
        # A cut can legitimately leave a multi-lump body; the disjoint-JOIN claim is about fusing.
        cf = _install(["Ring", "Bore"], lumps={"Ring": 1, "Bore": 1})
        cf.add = lambda inp: _feature_with_bodies(["Ring"], lumps=2)
        out = _payload(cb.handler(target="Ring", tools=["Bore"], operation="cut"))
        assert out["lump_count"] == 2          # still published - it is a fact about the result
        assert "disjoint_join" not in out

    def test_several_result_bodies_leave_the_lump_read_unstated(self):
        # With more than one result body there is no single 'the join landed here' body to read, so
        # no lump count is published (picking one body's lumps would describe part of the result as
        # if it were the whole). The verdict still lands - from the body COUNT, the primary arm.
        cf = _install(["A", "B"], lumps={"A": 1, "B": 1})
        cf.add = lambda inp: _feature_with_bodies(["A", "B"], lumps=2)
        out = _payload(cb.handler(target="A", tools=["B"], operation="join"))
        assert "lump_count" not in out
        assert out["disjoint_join"] is True and out["result_body_count"] == 2
        assert "input_lump_total" not in out       # that comparison belongs to the lump arm only


# -- both boolean flags go through set_verified -------------------------------------------------
#
# A SWIG proxy ACCEPTS an assignment to a property it then ignores, with no exception. Neither flag
# leaves a trace anywhere else in the result: a dropped isKeepToolBodies consumes the tool bodies
# the caller asked to KEEP, and the body-count/volume census still reads a perfectly clean combine.

class TestFlagsThatDoNotTake:
    def test_a_dropped_keep_tools_refuses_before_the_combine_runs(self):
        cf = _install(["Target", "Tool"],
                      cf=FakeCombineFeatures(ignores=("isKeepToolBodies",)))
        res = cb.handler(target="Target", tools="Tool", operation="join", keep_tools=True)
        msg = res["message"]
        assert "keep_tools" in msg and "Nothing was combined" in msg
        assert cf.add_calls == 0

    def test_a_dropped_new_component_refuses_before_the_combine_runs(self):
        cf = _install(["Target", "Tool"],
                      cf=FakeCombineFeatures(ignores=("isNewComponent",)))
        res = cb.handler(target="Target", tools="Tool", operation="join", new_component=True)
        msg = res["message"]
        assert "new_component" in msg and "Nothing was combined" in msg
        assert cf.add_calls == 0

    def test_flags_that_take_reach_the_input_and_the_combine_runs(self):
        cf = _install(["Target", "Tool"])
        res = cb.handler(target="Target", tools="Tool", operation="join", keep_tools=True,
                         new_component=True)
        assert res["isError"] is False, res
        assert cf.last_input.isKeepToolBodies is True
        assert cf.last_input.isNewComponent is True
        assert cf.add_calls == 1
