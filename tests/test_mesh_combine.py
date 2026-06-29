"""Unit tests for ``mesh_combine.py`` — boolean join/cut/intersect/merge of MESH bodies.

No live Fusion. We model the slice of the mesh-combine API the tool touches — MeshCombineFeatures
(createInput -> input -> add -> feature with .bodies), the operation/algorithm enums, and MeshBody /
BRepBody fakes NAMED to match the Fusion types so the _inputs.MeshBodyRef / BodyRefList(kind="mesh")
kind discrimination branches correctly.

Pinned (the DoD):
  • each operation (join/cut/intersect/merge) resolves the right MeshCombineOperationTypes enum and
    calls createInput(target, [tools]).
  • the tools LIST rejects a BRep handle (MeshBodyRef redirect) BEFORE any mutation.
  • the same-body guard (target also in tools) is rejected.
  • the combine routes through run_in_base_feature — a parametric design opens a base-feature scope
    (startEdit/finishEdit) around the add(); a direct design opens NONE.
  • a bad operation is rejected by the Choice input.
  • algorithm default is enhanced; the add() mutation is NOT swallowed by safe().
"""

import json

from conftest import load_tool

mc = load_tool("mesh_combine")
inp = mc._inputs


# ── fakes (named to match the Fusion type names the kind discrimination reads) ──────────────────

class MeshBody:
    """Stands in for adsk.fusion.MeshBody (a SEPARATE type from BRepBody)."""
    def __init__(self, name="Mesh1", token=None, parent=None):
        self.name = name
        self.entityToken = token or f"MTOK::{name}"
        self.parentComponent = parent


class BRepBody:
    """Stands in for adsk.fusion.BRepBody — the WRONG kind for a mesh input."""
    def __init__(self, name="Body1", is_solid=True, token=None):
        self.name = name
        self.isSolid = is_solid
        self.entityToken = token or f"BTOK::{name}"


class _Coll:
    def __init__(self, items=()):
        self._items = list(items)

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


# ── mesh-combine feature fakes (createInput -> input ; add -> feature with .bodies) ──────────────

class _CombineInput:
    def __init__(self, target, tools):
        self.target = target
        self.tools = tools
        self.operation = None
        self.algorithm = None


class _FeatureResult:
    def __init__(self, name, bodies):
        self.name = name
        self.bodies = _Coll(bodies)


class _MeshCombineFeatures:
    """comp.features.meshCombineFeatures — createInput(target, list) then add(input) -> feature whose
    .bodies hold the result. raise_on_add forces a mutation failure (must surface, not be swallowed)."""
    def __init__(self, result_bodies, feat_name="MeshCombine1", raise_on_add=False, none_feature=False):
        self._result_bodies = result_bodies
        self._feat_name = feat_name
        self.raise_on_add = raise_on_add
        self.none_feature = none_feature
        self.last_input = None
        self.create_args = None

    def createInput(self, target, tools):
        self.create_args = (target, tools)
        self.last_input = _CombineInput(target, tools)
        return self.last_input

    def add(self, inp):
        if self.raise_on_add:
            raise RuntimeError("combine failed")
        if self.none_feature:
            return None
        return _FeatureResult(self._feat_name, self._result_bodies)


class _Features:
    def __init__(self, mesh_combine=None, base_features=None):
        self.meshCombineFeatures = mesh_combine
        self.baseFeatures = base_features


class _BaseFeature:
    def __init__(self):
        self.name = "BaseFeature1"
        self.started = False
        self.finished = False

    def startEdit(self):
        self.started = True
        return True

    def finishEdit(self):
        self.finished = True
        return True


class _BaseFeatures:
    def __init__(self, made):
        self._made = made

    def add(self):
        return self._made


class FakeComp:
    def __init__(self, name="Comp", features=None, mesh_bodies=None):
        self.name = name
        self.features = features
        # comp.meshBodies — the non-parametric side-effect probe reads its .count.
        self.meshBodies = mesh_bodies if mesh_bodies is not None else _Coll()


class FakeDesign:
    def __init__(self, comp, design_type=0, all_comps=None):
        self.activeComponent = comp
        self.rootComponent = comp
        self.designType = design_type            # 0 direct, 1 parametric
        self._all = all_comps if all_comps is not None else [comp]

    @property
    def allComponents(self):
        return self._all

    @property
    def allOccurrences(self):
        return []

    def findEntityByToken(self, tok):
        return self._handle_map.get(tok, [])

    _handle_map = {}


def _wire_adsk():
    """Install the adsk.fusion type identities + enums the tool/kinds read."""
    import adsk.fusion
    adsk.fusion.MeshBody = MeshBody
    adsk.fusion.BRepBody = BRepBody
    dts = adsk.fusion.DesignTypes
    dts.ParametricDesignType = 1
    dts.DirectDesignType = 0
    adsk.fusion.BaseFeature = _BaseFeature
    ot = adsk.fusion.MeshCombineOperationTypes
    ot.JoinMeshCombineOperationType = "JOIN"
    ot.CutMeshCombineOperationType = "CUT"
    ot.IntersectMeshCombineOperationType = "INTERSECT"
    ot.MergeMeshCombineOperationType = "MERGE"
    at = adsk.fusion.MeshCombineAlgorithmTypes
    at.LegacyMeshCombineAlgorithmType = "LEGACY"
    at.EnhancedMeshCombineAlgorithmType = "ENHANCED"
    return adsk.fusion


def _install(design, handle_map=None):
    """Point the tool + the body-ref kinds at a fake design and a token resolver."""
    handle_map = handle_map or {}
    design._handle_map = {k: [v] for k, v in handle_map.items()}
    mc.app = type("A", (), {"activeProduct": design})()
    mc._common.app = mc.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # body-ref kinds resolve via _common.design()/target_component()
    inp._common.design = lambda: design
    inp._common.target_component = lambda d: design.activeComponent
    return design


def _build(design_type=0, raise_on_add=False, none_feature=False, result_name="Result",
           mesh_bodies=None):
    """A target mesh + two tool meshes in a component with a wired mesh-combine feature collection.
    Returns (design, feats, target, tool_a, tool_b)."""
    _wire_adsk()
    result = MeshBody(result_name)
    feats = _MeshCombineFeatures([result], raise_on_add=raise_on_add, none_feature=none_feature)
    bf = _BaseFeature()
    comp = FakeComp("Comp", features=_Features(mesh_combine=feats, base_features=_BaseFeatures(made=bf)),
                    mesh_bodies=mesh_bodies)
    target = MeshBody("Target", parent=comp)
    tool_a = MeshBody("ToolA", parent=comp)
    tool_b = MeshBody("ToolB", parent=comp)
    des = FakeDesign(comp, design_type=design_type)
    des._bf = bf
    _install(des, handle_map={"T": target, "A": tool_a, "B": tool_b})
    return des, feats, target, tool_a, tool_b


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── each operation resolves the right enum + calls createInput(target, [tools]) ─────────────────

class TestOperations:
    def test_join(self):
        des, feats, *_ = _build()
        out = _payload(mc.handler(target="T", tools=["A"], operation="join"))
        assert out["combined"] is True and out["operation"] == "join"
        assert feats.last_input.operation == "JOIN"
        # createInput got (target, list[MeshBody])
        tgt, tools = feats.create_args
        assert tgt.name == "Target"
        assert isinstance(tools, list) and [t.name for t in tools] == ["ToolA"]

    def test_cut(self):
        des, feats, *_ = _build()
        _payload(mc.handler(target="T", tools=["A"], operation="cut"))
        assert feats.last_input.operation == "CUT"

    def test_intersect(self):
        des, feats, *_ = _build()
        _payload(mc.handler(target="T", tools=["A"], operation="intersect"))
        assert feats.last_input.operation == "INTERSECT"

    def test_merge(self):
        des, feats, *_ = _build()
        _payload(mc.handler(target="T", tools=["A"], operation="merge"))
        assert feats.last_input.operation == "MERGE"

    def test_multiple_tool_bodies(self):
        des, feats, *_ = _build()
        out = _payload(mc.handler(target="T", tools=["A", "B"]))
        assert out["tools"] == ["ToolA", "ToolB"]
        _, tools = feats.create_args
        assert [t.name for t in tools] == ["ToolA", "ToolB"]

    def test_comma_string_tools_parsed(self):
        des, feats, *_ = _build()
        out = _payload(mc.handler(target="T", tools="A, B"))
        _, tools = feats.create_args
        assert [t.name for t in tools] == ["ToolA", "ToolB"]


# ── algorithm: default enhanced; explicit legacy ────────────────────────────────────────────────

class TestAlgorithm:
    def test_default_enhanced(self):
        des, feats, *_ = _build()
        out = _payload(mc.handler(target="T", tools=["A"]))
        assert out["algorithm"] == "enhanced"
        assert feats.last_input.algorithm == "ENHANCED"

    def test_legacy(self):
        des, feats, *_ = _build()
        out = _payload(mc.handler(target="T", tools=["A"], algorithm="legacy"))
        assert out["algorithm"] == "legacy"
        assert feats.last_input.algorithm == "LEGACY"


# ── the tools LIST rejects a BRep handle (redirect) BEFORE any mutation ──────────────────────────

class TestMeshKindEnforcement:
    def test_brep_in_tools_list_redirected_no_mutation(self):
        des, feats, target, *_ = _build()
        brep = BRepBody("SolidBody", is_solid=True)
        des._handle_map["BR"] = [brep]
        res = mc.handler(target="T", tools=["A", "BR"])
        assert res["isError"] is True
        assert "must be a MESH body" in res["message"]
        assert "SOLID body" in res["message"]
        # nothing was created/added — the kind gate fired before any mutation
        assert feats.create_args is None and feats.last_input is None

    def test_brep_target_redirected(self):
        des, feats, *_ = _build()
        brep = BRepBody("SolidTarget", is_solid=True)
        des._handle_map["BR"] = [brep]
        res = mc.handler(target="BR", tools=["A"])
        assert res["isError"] is True and "must be a MESH body" in res["message"]
        assert feats.create_args is None


# ── same-body guard ─────────────────────────────────────────────────────────────────────────────

class TestSameBodyGuard:
    def test_target_in_tools_rejected(self):
        des, feats, *_ = _build()
        res = mc.handler(target="T", tools=["T"])
        assert res["isError"] is True and "same as the target" in res["message"]
        assert feats.create_args is None      # rejected before any mutation


# ── base-feature routing: parametric opens a scope, direct opens none ───────────────────────────

class TestBaseFeatureRouting:
    def test_direct_no_scope(self):
        des, feats, *_ = _build(design_type=0)         # direct
        out = _payload(mc.handler(target="T", tools=["A"]))
        assert out["combined"] is True
        # no base-feature scope opened in direct mode
        assert des._bf.started is False and des._bf.finished is False

    def test_parametric_opens_and_finishes_scope(self):
        des, feats, *_ = _build(design_type=1)         # parametric
        out = _payload(mc.handler(target="T", tools=["A"]))
        assert out["combined"] is True
        # the add() ran INSIDE an atomic base-feature scope (opened then finished)
        assert des._bf.started is True and des._bf.finished is True


# ── Choice rejects a bad operation ──────────────────────────────────────────────────────────────

class TestChoiceGuards:
    def test_bad_operation_rejected(self):
        des, feats, *_ = _build()
        res = mc.handler(target="T", tools=["A"], operation="weld")
        assert res["isError"] is True
        assert "operation" in res["message"] and "weld" in res["message"]
        assert feats.create_args is None      # rejected before any mutation

    def test_bad_algorithm_rejected(self):
        des, feats, *_ = _build()
        res = mc.handler(target="T", tools=["A"], algorithm="turbo")
        assert res["isError"] is True and "algorithm" in res["message"]


# ── the add() mutation is NOT swallowed by safe() ───────────────────────────────────────────────

class TestMutationSurfaces:
    def test_add_failure_surfaces(self):
        des, feats, *_ = _build(raise_on_add=True)
        res = mc.handler(target="T", tools=["A"])
        assert res["isError"] is True and "failed" in res["message"].lower()

    def test_create_input_raise_surfaces(self):
        des, feats, *_ = _build()
        def _boom(target, tools):
            raise RuntimeError("create blew up")
        feats.createInput = _boom
        res = mc.handler(target="T", tools=["A"])
        assert res["isError"] is True
        assert "Could not create the mesh-combine input" in res["message"]

    def test_create_input_none_surfaces(self):
        des, feats, *_ = _build()
        feats.createInput = lambda target, tools: None
        res = mc.handler(target="T", tools=["A"])
        assert res["isError"] is True
        assert "returned nothing" in res["message"]


# ── the meshCombineFeatures collection may be absent on a given design ───────────────────────────

class TestNoCollection:
    def test_missing_mesh_combine_features_errors(self):
        # the component has no meshCombineFeatures collection -> honest error before any work
        _wire_adsk()
        comp = FakeComp("Comp", features=_Features(mesh_combine=None))
        target = MeshBody("Target", parent=comp)
        tool_a = MeshBody("ToolA", parent=comp)
        des = FakeDesign(comp, design_type=0)
        _install(des, handle_map={"T": target, "A": tool_a})
        res = mc.handler(target="T", tools=["A"])
        assert res["isError"] is True
        assert "no meshCombineFeatures collection" in res["message"]


# ── REGRESSION: a None add() return is non-parametric SUCCESS, not a failure ─────────────────────
# add() "Return nothing in the case where the feature is non-parametric" (a DIRECT design OR an add
# inside the BaseFeature scope). A None return must report SUCCESS via the target mesh, not error.

class TestNonParametricSuccess:
    def test_none_feature_is_success_via_target_body(self):
        # add() returns None (non-parametric) -> SUCCESS, reported against the target mesh body.
        des, feats, target, *_ = _build(none_feature=True)
        out = _payload(mc.handler(target="T", tools=["A"]))
        assert out["combined"] is True
        assert out["non_parametric"] is True
        assert out["feature"] is None
        # the result is observed on the TARGET (combine lands in place), not via the None feature
        assert out["result_bodies"][0]["name"] == "Target"
        assert out["result_bodies"][0]["handle"] == "MTOK::Target"

    def test_none_feature_in_parametric_scope_is_success(self):
        # PARAMETRIC: the scoped add still returns None (the scope makes it non-parametric) -> SUCCESS,
        # and the base-feature scope was opened/closed around it.
        des, feats, *_ = _build(design_type=1, none_feature=True)
        out = _payload(mc.handler(target="T", tools=["A"]))
        assert out["combined"] is True and out["non_parametric"] is True
        assert des._bf.started is True and des._bf.finished is True


# ── result bodies are reported ──────────────────────────────────────────────────────────────────

class TestResult:
    def test_reports_result_bodies(self):
        des, feats, *_ = _build(result_name="Combined")
        out = _payload(mc.handler(target="T", tools=["A"]))
        assert out["result_bodies"][0]["name"] == "Combined"
        assert out["result_bodies"][0]["handle"] == "MTOK::Combined"
        assert out["target"] == "Target"

    def test_no_design_errors(self):
        _wire_adsk()
        mc.app = type("A", (), {"activeProduct": None})()
        mc._common.app = mc.app
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = mc.handler(target="T", tools=["A"])
        assert res["isError"] is True and "No active design" in res["message"]
