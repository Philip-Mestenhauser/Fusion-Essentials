"""Unit tests for surface_edit.py — EDIT open surface bodies (trim/extend/offset/thicken).

The load-bearing cases:
  * surface_trim COMMITS via add() on success, and CANCELS the open transaction on failure (the
    lifecycle hazard) — cancel() must be CALLED, not swallowed.
  * surface_offset stays a SURFACE (is_solid false); surface_thicken makes a SOLID (is_solid true).
  * a wrong-kind body (a SOLID passed where a surface is required) gets the redirecting error.
  * extend rejects edges spanning more than one body.
No live Fusion — fake feature classes capture inputs and record cancel()/add() calls.
"""

import json

from conftest import load_tool

se = load_tool("surface_edit")
inp = se._inputs


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeBody:
    def __init__(self, name="Body1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class FakeBodies:
    def __init__(self, bodies):
        self._b = list(bodies)
    @property
    def count(self):
        return len(self._b)
    def item(self, i):
        return self._b[i]


class FakeFeature:
    def __init__(self, name="Feat1", bodies=None):
        self.name = name
        self.bodies = FakeBodies(bodies if bodies is not None else [FakeBody()])


class FakeCellBody:
    def __init__(self, area):
        self.area = area


class FakeBRepCell:
    """A candidate cell the trim tool divided the surface into. isSelected is settable; for a Trim
    feature a SELECTED cell is REMOVED. cellBody.area sizes it."""
    def __init__(self, area):
        self.isSelected = False
        self.cellBody = FakeCellBody(area)


class FakeBRepCells:
    def __init__(self, areas):
        self._cells = [FakeBRepCell(a) for a in areas]
    @property
    def count(self):
        return len(self._cells)
    def item(self, i):
        return self._cells[i]


class FakeTrimInput:
    def __init__(self, tool, cell_areas):
        self.tool = tool
        self.cancelled = False
        self.bRepCells = FakeBRepCells(cell_areas)
    def cancel(self):
        self.cancelled = True


class FakeTrimFeatures:
    """createInput partial-computes and populates input.bRepCells (all isSelected=False). add()
    REPRODUCES the live contract: it RAISES "No cells are selected" when no cell isSelected — so a
    handler that forgets the selection step fails exactly as it did live. raise_on_add / null_feature
    exercise the failure paths where cancel() MUST be called."""
    def __init__(self, result_bodies=None, raise_on_add=False, null_feature=False,
                 cell_areas=(3.0, 9.0, 1.0)):
        self.last_input = None
        self._result = result_bodies
        self._raise = raise_on_add
        self._null = null_feature
        self._cell_areas = list(cell_areas)
    def createInput(self, tool):
        self.last_input = FakeTrimInput(tool, self._cell_areas)
        return self.last_input
    def add(self, inp):
        # live contract: with zero cells selected, add() raises "No cells are selected"
        if not any(inp.bRepCells.item(i).isSelected for i in range(inp.bRepCells.count)):
            raise RuntimeError("3 : No cells are selected.")
        if self._raise:
            raise RuntimeError("trim tool does not intersect")
        if self._null:
            return None
        return FakeFeature(name="Trim1", bodies=self._result)


class FakeExtendInput:
    def __init__(self, edges, dist, et, chaining):
        self.edges = edges
        self.dist = dist
        self.et = et
        self.chaining = chaining


class FakeExtendFeatures:
    def __init__(self, result_bodies=None):
        self.last_input = None
        self._result = result_bodies
    def createInput(self, edges, dist, et, chaining):
        self.last_input = FakeExtendInput(edges, dist, et, chaining)
        return self.last_input
    def add(self, inp):
        return FakeFeature(name="Extend1", bodies=self._result)


class FakeOffsetInput:
    def __init__(self, ents, dist, op, chain):
        self.ents = ents
        self.dist = dist
        self.op = op
        self.chain = chain


class FakeOffsetFeatures:
    def __init__(self, result_bodies=None):
        self.last_input = None
        self._result = result_bodies
    def createInput(self, ents, dist, op, chain):
        self.last_input = FakeOffsetInput(ents, dist, op, chain)
        return self.last_input
    def add(self, inp):
        return FakeFeature(name="Offset1", bodies=self._result)


class FakeThickenInput:
    def __init__(self, faces, thick, sym, op, chain):
        self.faces = faces
        self.thick = thick
        self.sym = sym
        self.op = op
        self.chain = chain


class FakeThickenFeatures:
    def __init__(self, result_bodies=None):
        self.last_input = None
        self._result = result_bodies
    def createInput(self, faces, thick, sym, op, chain):
        self.last_input = FakeThickenInput(faces, thick, sym, op, chain)
        return self.last_input
    def add(self, inp):
        return FakeFeature(name="Thicken1", bodies=self._result)


class FakeFeatures:
    def __init__(self, trim=None, extend=None, offset=None, thicken=None):
        self.trimFeatures = trim
        self.extendFeatures = extend
        self.offsetFeatures = offset
        self.thickenFeatures = thicken


class FakeComp:
    def __init__(self, features):
        self.features = features


class FakeDesign:
    def __init__(self, comp):
        self.rootComponent = comp
        self.activeComponent = comp


class _OC:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)


class FakeBRepBody:
    """adsk.fusion.BRepBody stand-in for SurfaceBodyRef kind validation."""
    def __init__(self, name="Surf1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class FakeFace:
    pass


class FakeEdge:
    def __init__(self, body=None):
        self.body = body


def _wire(comp, handle_map=None):
    design = FakeDesign(comp)
    se.app = type("A", (), {"activeProduct": design})()
    se._common.app = se.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "NewComponentFeatureOperation"):
        setattr(fo, n, n)
    sxt = adsk.fusion.SurfaceExtendTypes
    for n in ("NaturalSurfaceExtendType", "TangentSurfaceExtendType", "PerpendicularSurfaceExtendType"):
        setattr(sxt, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    adsk.fusion.BRepBody = FakeBRepBody
    adsk.fusion.BRepFace = FakeFace
    adsk.fusion.BRepEdge = FakeEdge
    handle_map = handle_map or {}

    class _D:
        rootComponent = comp
        activeComponent = comp
        def findEntityByToken(self, t):
            e = handle_map.get(t)
            return [e] if e is not None else []
    inp._common.design = lambda: _D()
    inp._common.target_component = lambda d: comp


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── surface_trim: commit AND the cancel-on-failure path ─────────────────────

class TestSurfaceTrim:
    def test_commits_via_add_on_success(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tool = FakeFace()
        # cells: areas 3, 9, 1 -> default keeps the largest (index 1)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": tool})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True
        # success path: the transaction was committed, NOT cancelled
        assert tf.last_input.cancelled is False
        # DEFAULT (keep larger): the single largest cell kept, the rest removed (selected)
        cells = tf.last_input.bRepCells
        assert cells.item(1).isSelected is False          # largest kept
        assert cells.item(0).isSelected is True           # removed
        assert cells.item(2).isSelected is True           # removed
        assert out["cells_kept"] == [1] and out["cells_removed"] == [0, 2]
        assert out["kept_area"] == 9.0

    def test_trim_selects_a_cell_before_add(self):
        # A handler that skips the cell-selection step leaves all cells unselected, so add() raises
        # "No cells are selected". The trim must select a cell first.
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True   # passes only because a cell is now selected before add()

    def test_keep_smaller_keeps_smallest_cell(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep="smaller"))
        cells = tf.last_input.bRepCells
        assert cells.item(2).isSelected is False          # smallest (area 1) kept
        assert cells.item(0).isSelected is True and cells.item(1).isSelected is True
        assert out["cells_kept"] == [2] and out["kept_area"] == 1.0

    def test_keep_by_index_keeps_that_cell(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep="0"))
        cells = tf.last_input.bRepCells
        assert cells.item(0).isSelected is False          # kept by index
        assert cells.item(1).isSelected is True and cells.item(2).isSelected is True
        assert out["cells_kept"] == [0]

    def test_keep_list_of_indices(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep=[0, 2]))
        cells = tf.last_input.bRepCells
        assert cells.item(0).isSelected is False and cells.item(2).isSelected is False
        assert cells.item(1).isSelected is True           # only the unlisted cell removed
        assert out["cells_kept"] == [0, 2] and out["cells_removed"] == [1]

    def test_keep_int_index_keeps_that_cell(self):
        # keep passed as an actual int (not a string) -> _select_cells int branch
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep=2))
        cells = tf.last_input.bRepCells
        assert cells.item(2).isSelected is False          # kept by int index
        assert cells.item(0).isSelected is True and cells.item(1).isSelected is True
        assert out["cells_kept"] == [2]

    def test_keep_out_of_range_index_falls_back_to_larger(self):
        # an index >= total is dropped, leaving an empty set -> default keeps the largest cell
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep=[7, 9]))
        assert out["cells_kept"] == [1]                   # largest (area 9 at index 1)

    def test_bad_keep_falls_back_to_larger_default(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep="garbage"))
        assert out["cells_kept"] == [1]                   # fell back to largest

    def test_no_cells_cancels_and_reports_no_intersection(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(cell_areas=())              # createInput divided nothing
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        res = se.trim_handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert tf.last_input.cancelled is True            # open transaction aborted
        assert "no cells" in res["message"].lower()

    def test_cancels_open_transaction_when_add_raises(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tool = FakeFace()
        tf = FakeTrimFeatures(raise_on_add=True)
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": tool})
        res = se.trim_handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        # THE HAZARD: cancel() was CALLED (not swallowed) so the open transaction is aborted
        assert tf.last_input.cancelled is True

    def test_cancels_when_add_returns_null_feature(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tool = FakeFace()
        tf = FakeTrimFeatures(null_feature=True)
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": tool})
        res = se.trim_handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert tf.last_input.cancelled is True
        assert "cancelled" in res["message"].lower()

    def test_wrong_kind_surface_gets_redirect_before_any_transaction(self):
        # a SOLID handed where a surface is required -> redirecting error, no createInput called
        solid = FakeBRepBody("Body1", is_solid=True)
        tool = FakeFace()
        tf = FakeTrimFeatures()
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": solid, "T": tool})
        res = se.trim_handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "OPEN SURFACE body" in res["message"] and "SOLID body" in res["message"]
        assert tf.last_input is None        # never opened a transaction


# ── surface_extend ──────────────────────────────────────────────────────────

class TestSurfaceExtend:
    def test_extends_from_open_edges(self):
        body = object()
        e1, e2 = FakeEdge(body=body), FakeEdge(body=body)
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": e1, "E2": e2})
        out = _payload(se.extend_handler(edges=["E1", "E2"], distance=4, units="mm"))
        assert out["extended"] is True and out["is_solid"] is False
        assert xf.last_input.dist == ("real", 0.4)
        assert xf.last_input.et == "NaturalSurfaceExtendType"

    def test_rejects_edges_from_more_than_one_body(self):
        e1, e2 = FakeEdge(body=object()), FakeEdge(body=object())
        comp = FakeComp(FakeFeatures(extend=FakeExtendFeatures()))
        _wire(comp, handle_map={"E1": e1, "E2": e2})
        res = se.extend_handler(edges=["E1", "E2"], distance=4)
        assert res["isError"] is True and "ONE surface body" in res["message"]

    def test_zero_distance_guard(self):
        comp = FakeComp(FakeFeatures(extend=FakeExtendFeatures()))
        _wire(comp, handle_map={"E1": FakeEdge()})
        res = se.extend_handler(edges=["E1"], distance=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_unknown_units_rejected(self):
        comp = FakeComp(FakeFeatures(extend=FakeExtendFeatures()))
        _wire(comp, handle_map={"E1": FakeEdge()})
        res = se.extend_handler(edges=["E1"], distance=4, units="cubits")
        assert res["isError"] is True and "mm, cm, or in" in res["message"]

    def test_unknown_extend_type_rejected(self):
        comp = FakeComp(FakeFeatures(extend=FakeExtendFeatures()))
        _wire(comp, handle_map={"E1": FakeEdge()})
        res = se.extend_handler(edges=["E1"], distance=4, extend_type="warp")
        assert res["isError"] is True
        assert "natural, tangent, perpendicular" in res["message"]

    def test_tangent_extend_type_resolves_enum(self):
        body = object()
        e1 = FakeEdge(body=body)
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": e1})
        out = _payload(se.extend_handler(edges=["E1"], distance=4, extend_type="tangent"))
        assert out["extend_type"] == "tangent"
        assert xf.last_input.et == "TangentSurfaceExtendType"


# ── surface_offset vs surface_thicken: output body kind ─────────────────────

class TestOffsetThickenKind:
    def test_offset_produces_a_surface(self):
        f1 = FakeFace()
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Surf2", is_solid=False)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.offset_handler(faces=["F1"], distance=2, units="mm"))
        assert out["offset"] is True
        assert out["is_solid"] is False           # offset stays a surface
        assert of.last_input.dist == ("real", 0.2)

    def test_thicken_produces_a_solid(self):
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, units="mm"))
        assert out["thickened"] is True
        assert out["is_solid"] is True            # thicken makes a solid wall
        assert tf.last_input.thick[0] == "real" and abs(tf.last_input.thick[1] - 0.3) < 1e-9
        assert tf.last_input.op == "NewBodyFeatureOperation"

    def test_thicken_symmetric_passed(self):
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        _payload(se.thicken_handler(faces=["F1"], thickness=3, symmetric=True))
        assert tf.last_input.sym is True

    def test_thicken_zero_thickness_guard(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.thicken_handler(faces=["F1"], thickness=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_offset_unknown_operation_rejected(self):
        comp = FakeComp(FakeFeatures(offset=FakeOffsetFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.offset_handler(faces=["F1"], distance=2, operation="cut")
        assert res["isError"] is True and "new, new_component" in res["message"]

    def test_offset_unknown_units_rejected(self):
        comp = FakeComp(FakeFeatures(offset=FakeOffsetFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.offset_handler(faces=["F1"], distance=2, units="leagues")
        assert res["isError"] is True and "mm, cm, or in" in res["message"]

    def test_thicken_unknown_units_rejected(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.thicken_handler(faces=["F1"], thickness=3, units="parsec")
        assert res["isError"] is True and "mm, cm, or in" in res["message"]

    def test_thicken_unknown_operation_rejected(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.thicken_handler(faces=["F1"], thickness=3, operation="intersect")
        assert res["isError"] is True and "new, join, cut" in res["message"]

    def test_thicken_join_op_maps_enum(self):
        # operation=join resolves to JoinFeatureOperation onto the thicken input
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, operation="join"))
        assert out["operation"] == "join"
        assert tf.last_input.op == "JoinFeatureOperation"
