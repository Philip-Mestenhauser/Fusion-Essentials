"""Unit tests for ``combine.py`` — boolean join/cut/intersect of solid bodies.

Pinned: the operation guard, target/tool body resolution by name, the same-body guard, missing-tool
reporting, the name-list-vs-comma-string parsing, and that the FeatureOperations + isKeepToolBodies
are set on the CombineInput.
"""

import json

from conftest import assert_no_active_design, load_tool

cb = load_tool("model_combine")


class FakeBody:
    def __init__(self, name):
        self.name = name


class FakeBodies:
    def __init__(self, names):
        self._b = [FakeBody(n) for n in names]
    @property
    def count(self):
        return len(self._b)
    def itemByName(self, name):
        for b in self._b:
            if b.name == name:
                return b
        return None


class FakeCombineInput:
    def __init__(self, target, tools):
        self.target = target
        self.tools = tools
        self.operation = None
        self.isKeepToolBodies = False
        self.isNewComponent = False


class FakeCombineFeatures:
    def __init__(self):
        self.last_input = None
        self.comp = None              # wired by _install so add() can consume tool bodies
    def createInput(self, target, tools):
        self.last_input = FakeCombineInput(target, tools)
        return self.last_input
    def add(self, inp):
        # a real Combine CONSUMES the tool bodies (unless isKeepToolBodies) - remove them from the
        # component so bodies_remaining pins the post-combine read-back, not a static echo
        if self.comp is not None and not inp.isKeepToolBodies:
            for b in list(inp.tools._i):
                if b in self.comp.bRepBodies._b:
                    self.comp.bRepBodies._b.remove(b)
        return type("F", (), {"name": "Combine1"})()


class FakeComp:
    def __init__(self, names, cf):
        self.name = "Comp"
        self.bRepBodies = FakeBodies(names)
        self.features = type("F", (), {"combineFeatures": cf})()


class FakeDesign:
    def __init__(self, comp):
        self.activeComponent = comp
        self.rootComponent = comp


def _install(body_names):
    cf = FakeCombineFeatures()
    comp = FakeComp(body_names, cf)
    cf.comp = comp
    design = FakeDesign(comp)
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
