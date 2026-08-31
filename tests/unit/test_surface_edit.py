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
import types

from conftest import MakeComp, body_proxy, load_tool, make_source_document, _NamedCollection

se = load_tool("surface_edit")
inp = se._inputs


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeBody:
    def __init__(self, name="Body1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class FakeFeature:
    def __init__(self, name="Feat1", bodies=None, faces=None, distance_cm=None, thickness_cm=None):
        self.name = name
        self.bodies = _NamedCollection(bodies if bodies is not None else [FakeBody()])
        if faces is not None:
            self.faces = _NamedCollection(faces)   # a counted collection of created faces
        # ExtendFeature.distance and ThickenFeature.thickness are ModelParameters reading CM; None
        # gives a feature whose length parameter cannot be read at all.
        if distance_cm is not None:
            self.distance = types.SimpleNamespace(value=distance_cm)
        if thickness_cm is not None:
            self.thickness = types.SimpleNamespace(value=thickness_cm)


def _face_on(body):
    """A face the feature CREATED, owned by `body` - the read _created_bodies walks."""
    return types.SimpleNamespace(body=body)


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


def _stale_cell_at(cells, index):
    """Make cells.item(index) RAISE - a stale BRepCell proxy, which safe() reads as no cell at all
    while the cells after it keep their own indices."""
    intact = cells.item

    def item(i):
        if i == index:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return intact(i)
    cells.item = item


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


class _SwallowingExtendInput(FakeExtendInput):
    """An ExtendFeatureInput that ACCEPTS the extendAlignment write and keeps its default anyway -
    nothing raises and the extend would just run free-edged. The shape set_verified catches."""
    def __setattr__(self, name, value):
        object.__setattr__(self, name, "FreeEdges" if name == "extendAlignment" else value)


class FakeExtendFeatures:
    def __init__(self, result_bodies=None, input_cls=FakeExtendInput, landed_cm=None,
                 distance_readable=True):
        # landed_cm: the distance the created feature reads back, when it differs from the one the
        # input was given (live, the two agree). distance_readable=False models a feature whose
        # distance parameter cannot be read at all.
        self.last_input = None
        self._result = result_bodies
        self._input_cls = input_cls
        self._landed_cm = landed_cm
        self._distance_readable = distance_readable
    def createInput(self, edges, dist, et, chaining):
        self.last_input = self._input_cls(edges, dist, et, chaining)
        return self.last_input
    def add(self, inp):
        landed = None
        if self._distance_readable:
            landed = self._landed_cm if self._landed_cm is not None else inp.dist[1]
        return FakeFeature(name="Extend1", bodies=self._result, distance_cm=landed)


class FakeOffsetInput:
    def __init__(self, ents, dist, op, chain):
        self.ents = ents
        self.dist = dist
        self.op = op
        self.chain = chain


class FakeOffsetFeatures:
    def __init__(self, result_bodies=None, created_faces=None):
        self.last_input = None
        self._result = result_bodies
        self._faces = created_faces
    def createInput(self, ents, dist, op, chain):
        self.last_input = FakeOffsetInput(ents, dist, op, chain)
        return self.last_input
    def add(self, inp):
        return FakeFeature(name="Offset1", bodies=self._result, faces=self._faces)


class FakeThickenInput:
    def __init__(self, faces, thick, sym, op, chain):
        self.faces = faces
        self.thick = thick
        self.sym = sym
        self.op = op
        self.chain = chain


class _SwallowingThickenInput(FakeThickenInput):
    """A ThickenFeatureInput that ACCEPTS the thickenType write and keeps its default anyway."""
    def __setattr__(self, name, value):
        object.__setattr__(self, name, "SharpThickenType" if name == "thickenType" else value)


class FakeThickenFeatures:
    def __init__(self, result_bodies=None, created_faces=None, input_cls=FakeThickenInput,
                 landed_cm=None, thickness_readable=True):
        # landed_cm / thickness_readable play the same roles for the wall's own thickness parameter
        # as their counterparts on FakeExtendFeatures do for the extend distance.
        self.last_input = None
        self._result = result_bodies
        self._faces = created_faces
        self._input_cls = input_cls
        self._landed_cm = landed_cm
        self._thickness_readable = thickness_readable
    def createInput(self, faces, thick, sym, op, chain):
        self.last_input = self._input_cls(faces, thick, sym, op, chain)
        return self.last_input
    def add(self, inp):
        landed = None
        if self._thickness_readable:
            landed = self._landed_cm if self._landed_cm is not None else inp.thick[1]
        return FakeFeature(name="Thicken1", bodies=self._result, faces=self._faces,
                           thickness_cm=landed)


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

    def test_trim_that_removes_no_area_bites(self):
        # committed, cells were marked removed, but the surface area is identical -> error, not ok
        surf = FakeBRepBody("Surf1", is_solid=False)
        surf.area = 12.0
        rb = FakeBody("Surf1", is_solid=False)
        rb.area = 12.0                                    # area unchanged by the commit
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        res = se.trim_handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "did not decrease" in res["message"]

    def test_trim_that_shrinks_area_passes(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        surf.area = 12.0
        rb = FakeBody("Surf1", is_solid=False)
        rb.area = 9.0                                     # the removed cells' area is gone
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True

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

    def _keep_scene(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(3.0, 9.0, 1.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        return tf

    def test_keep_out_of_range_index_is_refused_naming_the_value(self):
        # silently falling back to 'largest' trims a DIFFERENT piece than the caller asked to keep,
        # with nothing in the payload saying so - the refusal names the index and the legal range
        tf = self._keep_scene()
        res = se.trim_handler(surface="S", trim_tool="T", keep=[7, 9])
        assert res["isError"] is True
        assert "'keep' index 7 does not exist" in res["message"]
        assert "0..2" in res["message"]
        assert tf.last_input.cancelled is True            # the open transaction was aborted

    def test_keep_index_at_the_last_cell_is_accepted(self):
        # total-1 is IN range: the boundary the out-of-range refusal must not swallow
        tf = self._keep_scene()
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep=2))
        assert out["cells_kept"] == [2]
        assert tf.last_input.cancelled is False

    def test_keep_index_one_past_the_last_cell_is_refused(self):
        # total itself is OUT of range - the > vs >= boundary of the range check
        self._keep_scene()
        res = se.trim_handler(surface="S", trim_tool="T", keep=3)
        assert res["isError"] is True
        assert "'keep' index 3 does not exist" in res["message"]

    def test_negative_keep_index_is_refused_not_read_as_from_the_end(self):
        self._keep_scene()
        res = se.trim_handler(surface="S", trim_tool="T", keep=-1)
        assert res["isError"] is True
        assert "'keep' index -1 does not exist" in res["message"]

    def test_bad_keep_is_refused_naming_the_value(self):
        tf = self._keep_scene()
        res = se.trim_handler(surface="S", trim_tool="T", keep="garbage")
        assert res["isError"] is True
        assert "'garbage' is not one" in res["message"]
        assert "'larger', 'smaller'" in res["message"]
        assert tf.last_input.cancelled is True

    def test_boolean_keep_is_refused_not_taken_as_index_one(self):
        # True is an int in Python - taken as an index it would keep cell 1 silently
        self._keep_scene()
        res = se.trim_handler(surface="S", trim_tool="T", keep=True)
        assert res["isError"] is True
        assert "True is not one" in res["message"]

    def test_a_list_with_one_bad_member_is_refused_whole(self):
        # keeping the parseable members and dropping the rest would trim a piece nobody named
        self._keep_scene()
        res = se.trim_handler(surface="S", trim_tool="T", keep=[0, "x"])
        assert res["isError"] is True
        assert "'x' is not one" in res["message"]

    def test_empty_keep_still_takes_the_larger_default(self):
        # '' / [] are an OMISSION, not a bad value - they must not be refused
        self._keep_scene()
        out = _payload(se.trim_handler(surface="S", trim_tool="T", keep=""))
        assert out["cells_kept"] == [1]                   # largest (area 9 at index 1)

    def test_an_unreadable_cell_leaves_the_area_of_every_later_cell_at_its_own_index(self):
        # 'keep' takes a cell INDEX and the kept indices are published, so the areas list is indexed
        # by cell number. A cell that cannot be read measures 0 in ITS slot: compacting the list
        # instead would make the third cell the second, and 'keep larger' would select the stale one.
        inp = FakeTrimInput(FakeFace(), (3.0, 9.0, 5.0))
        _stale_cell_at(inp.bRepCells, 1)
        kept, kept_area, total, err = se._select_cells(inp, None)
        assert err is None and total == 3
        assert kept == [2] and kept_area == 5.0      # the largest READABLE cell, at its true index

    def test_an_unreadable_cell_does_not_shift_which_cells_are_selected(self):
        # the selection walk writes isSelected per index: cell 2 was asked for, so cell 2 is the one
        # left unselected (KEPT for a trim) and cell 0 is the one selected for removal
        inp = FakeTrimInput(FakeFace(), (3.0, 9.0, 5.0))
        first, third = inp.bRepCells.item(0), inp.bRepCells.item(2)
        _stale_cell_at(inp.bRepCells, 1)
        kept, _area, total, err = se._select_cells(inp, 2)
        assert err is None and kept == [2] and total == 3
        assert third.isSelected is False             # kept: the cell the caller named
        assert first.isSelected is True              # removed

    def test_phantom_cell_kept_area_exceeds_input_aborts(self):
        # A coincident/overlapping surface injects a cell LARGER than the target's own area (the compute
        # spans every visible surface the tool crosses). 'keep larger' would latch onto it: kept_area >
        # surf.area is impossible for a real subset of the target -> abort BEFORE add(), cancel the txn.
        surf = FakeBRepBody("Surf1", is_solid=False)
        surf.area = 16.0
        tf = FakeTrimFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                              cell_areas=(4.0, 20.0, 6.0))   # largest cell (20) exceeds the 16 input
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        res = se.trim_handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "larger than" in res["message"] and "HIDE" in res["message"]
        assert tf.last_input.cancelled is True          # transaction aborted, no feature landed

    def test_kept_area_within_input_still_trims(self):
        # The same scene without the phantom: the kept cell is smaller than the input -> a normal trim.
        surf = FakeBRepBody("Surf1", is_solid=False)
        surf.area = 16.0
        rb = FakeBody("Surf1", is_solid=False)
        rb.area = 12.0
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(4.0, 12.0, 6.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True and out["kept_area"] == 12.0

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

    def test_extend_alignment_set_on_input_and_reported(self):
        import adsk.fusion
        body = object()
        e1 = FakeEdge(body=body)
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": e1})
        out = _payload(se.extend_handler(edges=["E1"], distance=4, extend_alignment="align_edges"))
        # the member name is BARE (AlignEdges), not suffixed like the neighbouring extend types
        assert xf.last_input.extendAlignment is adsk.fusion.SurfaceExtendAlignment.AlignEdges
        assert out["extend_alignment"] == "align_edges"

    def test_free_edges_alignment_resolves_its_own_member(self):
        # free_edges must land on FreeEdges, not on the other member of the pair: both names
        # resolve, so set_verified's read-back cannot tell them apart - only this can.
        import adsk.fusion
        body = object()
        e1 = FakeEdge(body=body)
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": e1})
        out = _payload(se.extend_handler(edges=["E1"], distance=4, extend_alignment="free_edges"))
        assert xf.last_input.extendAlignment is adsk.fusion.SurfaceExtendAlignment.FreeEdges
        assert xf.last_input.extendAlignment is not adsk.fusion.SurfaceExtendAlignment.AlignEdges
        assert out["extend_alignment"] == "free_edges"

    def test_extend_alignment_omitted_writes_nothing_and_reports_nothing(self):
        # a fresh input's extendAlignment reads 0 (measured), so an unwritten one keeps that;
        # the payload must not claim a value nobody set
        body = object()
        e1 = FakeEdge(body=body)
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": e1})
        out = _payload(se.extend_handler(edges=["E1"], distance=4))
        assert not hasattr(xf.last_input, "extendAlignment")
        assert "extend_alignment" not in out

    def test_distance_that_reads_back_wrong_is_an_error(self):
        # the extend landed a distance Fusion took, not the one asked for -> error, never an ok
        # payload echoing the request as if it were the surface's growth
        body = object()
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)], landed_cm=0.25)
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": FakeEdge(body=body)})
        res = se.extend_handler(edges=["E1"], distance=4, units="mm")
        assert res["isError"] is True
        assert "reads back 2.5" in res["message"] and "requested 4.0" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_distance_read_off_the_feature_is_published(self):
        body = object()
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": FakeEdge(body=body)})
        out = _payload(se.extend_handler(edges=["E1"], distance=4, units="mm"))
        assert out["distance"] == 4.0            # the feature's own parameter, in the caller's units
        assert "unverified" not in out

    def test_unreadable_distance_is_flagged_unverified_not_silently_echoed(self):
        body = object()
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                                distance_readable=False)
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": FakeEdge(body=body)})
        out = _payload(se.extend_handler(edges=["E1"], distance=4, units="mm"))
        assert out["unverified"] == ["distance"]
        assert "Not read back off the feature: distance." in out["note"]
        assert out["distance"] == 4.0            # the request, published only because it is flagged

    def test_unknown_extend_alignment_rejected(self):
        comp = FakeComp(FakeFeatures(extend=FakeExtendFeatures()))
        _wire(comp, handle_map={"E1": FakeEdge()})
        res = se.extend_handler(edges=["E1"], distance=4, extend_alignment="snap_to_grid")
        assert res["isError"] is True
        assert "free_edges, align_edges" in res["message"]

    def test_extend_alignment_that_does_not_take_is_refused(self):
        body = object()
        e1 = FakeEdge(body=body)
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                                input_cls=_SwallowingExtendInput)
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": e1})
        res = se.extend_handler(edges=["E1"], distance=4, extend_alignment="align_edges")
        assert res["isError"] is True
        assert "extend_alignment=align_edges" in res["message"]
        assert "reads back unchanged" in res["message"]

    def test_extend_alignment_unavailable_member_is_refused_not_silently_defaulted(self, monkeypatch):
        # a missing enum class/member must REFUSE - running the extend on its default while the
        # payload echoes the request is the failure mode this path exists to prevent
        import adsk.fusion
        body = object()
        e1 = FakeEdge(body=body)
        monkeypatch.setattr(adsk.fusion, "SurfaceExtendAlignment", object())
        xf = FakeExtendFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": e1})
        res = se.extend_handler(edges=["E1"], distance=4, extend_alignment="align_edges")
        assert res["isError"] is True
        assert "not available on this Fusion version" in res["message"]


# ── surface_offset vs surface_thicken: output body kind ─────────────────────

class TestOffsetThickenKind:
    def test_offset_produces_a_surface(self):
        # LIVE SHAPE: feature.bodies lists the pre-existing SOURCE solid alongside the new surface
        # (verified live: [Body1, Body2]). is_solid/result_bodies must be read off the CREATED
        # surface (via the created faces), never the source solid - an any_solid read over
        # feature.bodies reports is_solid=true for a genuine open surface.
        f1 = FakeFace()
        surf = FakeBody("Surf2", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Body1", is_solid=True), surf],
                                created_faces=[_face_on(surf)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.offset_handler(faces=["F1"], distance=2, units="mm"))
        assert out["offset"] is True
        assert out["is_solid"] is False           # the CREATED surface, not the polluting source solid
        assert out["result_bodies"] == ["Surf2"]  # source solid excluded
        assert out["faces_requested"] == 1 and out["faces_offset"] == 1
        assert of.last_input.dist == ("real", 0.2)

    def test_offset_default_chaining_is_off(self):
        # chaining=true silently swept a filleted body's whole tangent-connected skin (live: one
        # picked face -> a surface wrapping the entire box). The pick is explicit; expansion is opt-in.
        f1 = FakeFace()
        surf = FakeBody("Surf2", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[surf], created_faces=[_face_on(surf)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        _payload(se.offset_handler(faces=["F1"], distance=2))
        assert of.last_input.chain is False

    def test_offset_chaining_expansion_is_reported(self):
        # chaining=true: 1 face requested, 6 tangent-connected faces offset -> the result SAYS so.
        f1 = FakeFace()
        surf = FakeBody("Skin1", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[surf],
                                created_faces=[_face_on(surf) for _ in range(6)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.offset_handler(faces=["F1"], distance=2, chaining=True))
        assert out["faces_requested"] == 1 and out["faces_offset"] == 6
        assert "EXPANDED" in out["note"] and "chaining=false" in out["note"]

    def test_offset_that_creates_no_faces_bites(self):
        # add() 'succeeded' but the feature created NO faces -> error, never a silent ok
        f1 = FakeFace()
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Body1", is_solid=True)], created_faces=[])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        res = se.offset_handler(faces=["F1"], distance=2)
        assert res["isError"] is True and "created no faces" in res["message"]

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

    def test_thicken_that_stays_a_surface_bites(self):
        # the wall did not close into a solid: no result body reads isSolid=true -> error, not ok
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=False)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        res = se.thicken_handler(faces=["F1"], thickness=3)
        assert res["isError"] is True
        assert "did not close into a solid" in res["message"]

    def test_thicken_beside_preexisting_solid_bites(self):
        # feature.bodies can carry a PRE-EXISTING solid beside the failed wall (the same class as
        # the offset read) - the gate must read the bodies owning the CREATED faces, or the source
        # solid false-passes the closed-into-a-solid check.
        f1 = FakeFace()
        wall = FakeBody("Wall1", is_solid=False)
        tf = FakeThickenFeatures(
            result_bodies=[FakeBody("Source", is_solid=True), wall],
            created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        res = se.thicken_handler(faces=["F1"], thickness=3)
        assert res["isError"] is True
        assert "CREATED body" in res["message"]

    def test_thicken_gate_names_the_created_body(self):
        # with created faces readable, result_bodies is the CREATED body - not the whole
        # feature.bodies list with the source solid in it.
        f1 = FakeFace()
        wall = FakeBody("Wall1", is_solid=True)
        tf = FakeThickenFeatures(
            result_bodies=[FakeBody("Source", is_solid=True), wall],
            created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3))
        assert out["result_bodies"] == ["Wall1"]
        assert out["is_solid"] is True

    def test_thickness_that_reads_back_wrong_is_an_error(self):
        # the solid gate passes (a solid wall landed) while the wall is the WRONG thickness - only
        # the feature's own parameter catches that, so it is an error, not an echoed request
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)], landed_cm=0.5)
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.thicken_handler(faces=["F1"], thickness=3, units="mm")
        assert res["isError"] is True
        assert "reads back 5.0" in res["message"] and "requested 3.0" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_thickness_read_off_the_feature_is_published(self):
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, units="mm"))
        assert out["thickness"] == 3.0           # the wall's own parameter, in the caller's units
        assert "unverified" not in out

    def test_unreadable_thickness_is_flagged_unverified_not_silently_echoed(self):
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)],
                                 thickness_readable=False)
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, units="mm"))
        assert out["unverified"] == ["thickness"]
        assert "Not read back off the feature: thickness." in out["note"]
        assert out["thickness"] == 3.0           # the request, published only because it is flagged

    def test_thicken_type_set_on_input_and_reported(self):
        import adsk.fusion
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, thicken_type="rounded"))
        assert tf.last_input.thickenType is adsk.fusion.ThickenTypes.RoundedThickenType
        assert out["thicken_type"] == "rounded"

    def test_thicken_type_omitted_writes_nothing_and_reports_nothing(self):
        # a fresh input's thickenType reads 0 (measured), so an unwritten one keeps that; the
        # payload must not claim a value nobody set
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3))
        assert not hasattr(tf.last_input, "thickenType")
        assert "thicken_type" not in out

    def test_unknown_thicken_type_rejected(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.thicken_handler(faces=["F1"], thickness=3, thicken_type="chamfered")
        assert res["isError"] is True and "sharp, rounded" in res["message"]

    def test_thicken_type_that_does_not_take_is_refused(self):
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)],
                                 input_cls=_SwallowingThickenInput)
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        res = se.thicken_handler(faces=["F1"], thickness=3, thicken_type="rounded")
        assert res["isError"] is True
        assert "thicken_type=rounded" in res["message"]
        assert "reads back unchanged" in res["message"]

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


class TestOffsetZeroDistance:
    def test_zero_distance_copies_the_face_as_a_coincident_surface(self):
        # distance=0 is legal (measured live: the feature lands, the copy reads coincident) - the
        # machining-prep copy-face idiom. The note names the coincident copy so a caller knows the
        # surface is indistinguishable from its source by eye.
        f1 = FakeFace()
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Copy1", is_solid=False)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.offset_handler(faces=["F1"], distance=0))
        assert out["offset"] is True
        assert out["distance"] == 0.0
        assert "COINCIDENT" in out["note"]


class _UnreadableSolidBody:
    """A result body whose isSolid will not read - the shape bool(safe(...)) turned into a confident
    'this is a surface'."""
    def __init__(self, name="Wall1"):
        self.name = name

    @property
    def isSolid(self):
        raise RuntimeError("4 : An API Object refers to a deleted Object")


class TestEmptyResultSetIsAnError:
    def test_thicken_with_no_result_body_is_an_error(self):
        # with no body there is nothing to read isSolid off, so a payload here can only assert a
        # SOLID wall it cannot show - result_bodies [] and is_solid false beside it
        tf = FakeThickenFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.thicken_handler(faces=["F1"], thickness=3)
        assert res["isError"] is True
        assert "owns no result body" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_one_result_body_is_the_boundary_that_passes(self):
        # exactly one body is the smallest non-empty set - the gate must bite at 0 and only at 0
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3))
        assert out["result_bodies"] == ["Wall1"]

    def test_thicken_note_states_the_flag_it_read_back(self):
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3))
        assert out["is_solid"] is True
        assert "reading back isSolid=true" in out["note"]

    def test_unreadable_is_solid_is_null_and_unverified_not_a_solid_claim(self):
        # nothing read the flag, so the note may not narrate a SOLID wall and is_solid may not be
        # a fabricated false either
        tf = FakeThickenFeatures(result_bodies=[_UnreadableSolidBody("Wall1")])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3))
        assert out["is_solid"] is None
        assert "is_solid" in out["unverified"]
        assert "UNVERIFIED" in out["note"]
        assert "isSolid=true" not in out["note"]


class TestOffsetUnreadableFaces:
    def test_unreadable_feature_faces_publish_null_plus_an_unverified_marker(self):
        # feature.faces would not read, so the zero-faces refusal never ran: publishing
        # faces_offset 0 (and an empty body list, and is_solid false) fabricates the very reads
        # that failed
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Copy1", is_solid=False)])  # no created faces
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.offset_handler(faces=["F1"], distance=2))
        assert out["faces_offset"] is None
        assert out["result_bodies"] is None
        assert out["is_solid"] is None
        assert out["unverified"] == ["faces_offset", "result_bodies", "is_solid"]
        assert "Not read back off the feature: faces_offset" in out["note"]

    def test_readable_faces_carry_no_unverified_marker(self):
        surf = FakeBody("Surf2", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[surf], created_faces=[_face_on(surf)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.offset_handler(faces=["F1"], distance=2))
        assert out["faces_offset"] == 1
        assert "unverified" not in out

    def test_unreadable_is_solid_on_a_created_body_is_null_not_false(self):
        wall = _UnreadableSolidBody("Copy1")
        of = FakeOffsetFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.offset_handler(faces=["F1"], distance=2))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "isSolid=false" not in out["note"]


class TestTrimPhantomGuardDisclosure:
    def test_a_committed_trim_discloses_what_the_guard_checked(self):
        # the guard is ONE-SIDED: a foreign cell larger than every target cell but smaller than the
        # target's whole area passes it. The payload must not let a caller read a committed trim as
        # "no foreign cell was involved".
        surf = FakeBRepBody("Surf1", is_solid=False)
        surf.area = 16.0
        rb = FakeBody("Surf1", is_solid=False)
        rb.area = 12.0
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(4.0, 12.0, 6.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["phantom_cell_guard"] == "kept_area_not_above_target_area"
        assert "smaller than" in out["note"] and "NOT detected" in out["note"]

    def test_an_unreadable_target_area_says_the_guard_never_ran(self):
        # surf.area does not read -> the gate's own condition is false, so nothing was checked
        surf = FakeBRepBody("Surf1", is_solid=False)
        rb = FakeBody("Surf1", is_solid=False)
        rb.area = 12.0
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(4.0, 12.0, 6.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["phantom_cell_guard"] == "not_applied"
        assert "NOT applied" in out["note"]


class TestSolidVerdictOverBodyFacts:
    """trim/extend collapse the shared per-body {name, is_solid} projection into one verdict. That
    projection publishes True/False/None, so the collapse keeps the three apart - any() would fold
    an unreadable flag into a confident 'a surface'."""

    def test_the_verdict_keeps_the_three_states_apart(self):
        assert se._solid_verdict([False, True]) is True        # any solid wins
        assert se._solid_verdict([False, None]) is False       # a flag READ false is an answer
        assert se._solid_verdict([None, None]) is None         # nothing read -> unknown
        assert se._solid_verdict([]) is None                   # no body read -> unknown

    def test_trim_publishes_a_null_is_solid_with_an_unverified_marker(self):
        surf = FakeBRepBody("Surf1", is_solid=False)
        surf.area = 16.0
        rb = _UnreadableSolidBody("Surf1")
        rb.area = 12.0
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(4.0, 12.0, 6.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "Not read back off the feature: is_solid." in out["note"]

    def test_trim_on_a_readable_flag_carries_no_marker(self):
        # the boundary: a flag that READ false is an answer, so the disclosure must not fire on it.
        surf = FakeBRepBody("Surf1", is_solid=False)
        surf.area = 16.0
        rb = FakeBody("Surf1", is_solid=False)
        rb.area = 12.0
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(4.0, 12.0, 6.0))
        comp = FakeComp(FakeFeatures(trim=tf))
        _wire(comp, handle_map={"S": surf, "T": FakeFace()})
        out = _payload(se.trim_handler(surface="S", trim_tool="T"))
        assert out["is_solid"] is False and "unverified" not in out

    def test_extend_publishes_a_null_is_solid_beside_its_landed_distance(self):
        body = _UnreadableSolidBody("Surf1")
        xf = FakeExtendFeatures(result_bodies=[body], landed_cm=0.5)
        comp = FakeComp(FakeFeatures(extend=xf))
        _wire(comp, handle_map={"E1": FakeEdge(body=FakeBRepBody("Surf1", is_solid=False))})
        out = _payload(se.extend_handler(edges=["E1"], distance=5))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert out["distance"] == 5.0                 # the length still read back off the feature


class TestThickenJoinDisclosure:
    def test_join_that_fused_nothing_is_disclosed(self):
        # operation='join' with a sheet touching no solid mints a NEW free-floating body while
        # publishing operation:'join' (measured) - the token diff discloses it.
        f1 = FakeFace()
        wall = FakeBody("Wall1", is_solid=True)
        tf = FakeThickenFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, operation="join"))
        assert out["fused"] is False and out["disjoint_join"] is True
        assert "fused NOTHING" in out["note"]

    def test_join_into_an_existing_solid_is_not_flagged(self, monkeypatch):
        # The created faces land on a body whose token stood in the pre-add census - a real fuse.
        import types as _t
        f1 = FakeFace()
        target = FakeBody("Target", is_solid=True)
        target.entityToken = "tok-target"
        tf = FakeThickenFeatures(result_bodies=[target], created_faces=[_face_on(target)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: target)
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, operation="join"))
        assert "fused" not in out and "disjoint_join" not in out


# The x-ref shape, measured on a host holding two x-refs of one design: two DISTINCT bodies read one
# byte-identical entityToken while their source documents' lineage ids differ.
_URN_XREF = "urn:adsk.wipprod:dm.lineage:K3I2nkywRlaWPHJexysOdA"
_URN_HOST = "urn:adsk.wipprod:dm.lineage:N_QoPrrrSJmF__f9BZV86A"
_SHARED_TOKEN = "/vB+AAEAAwAAAAAAAAAAAAAA"


def _body_from_document(name, token, urn):
    """A solid body owned by a component in the document with lineage id `urn` - the chain a body's
    source document is read through (parentComponent -> parentDesign -> parentDocument ->
    dataFile.id)."""
    b = FakeBody(name, is_solid=True)
    b.entityToken = token
    b.parentComponent = MakeComp(name=name, parent_design=make_source_document(urn))
    return b


class TestThickenJoinAcrossDocuments:
    """The pre-add census is taken on the FACE's own component (census_host) while the thicken is
    added to the ACTIVE component's features, so the two sides of the before/after diff need not be
    one document - and an entityToken is DOCUMENT-LOCAL."""

    def _scene(self):
        import types as _t
        standing = _body_from_document("Xref_Frame", _SHARED_TOKEN, _URN_XREF)
        wall = _body_from_document("Wall1", _SHARED_TOKEN, _URN_HOST)
        tf = FakeThickenFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: standing)
        _wire(comp, handle_map={"F1": FakeFace()})
        return standing, wall

    def test_the_x_ref_fixture_really_models_the_collision(self):
        # Both halves must be real: with no token collision the created body was never going to be
        # mistaken for a standing one, and with one document there is nothing to tell them apart by.
        standing, wall = self._scene()
        assert standing is not wall and standing.entityToken == wall.entityToken
        assert (standing.parentComponent.parentDesign.parentDocument.dataFile.id
                != wall.parentComponent.parentDesign.parentDocument.dataFile.id)

    def test_a_new_body_whose_token_collides_with_a_censused_one_is_still_disclosed(self):
        # Keyed on the bare token the created wall reads as a body that already stood there, so the
        # join is reported as a fuse that never happened - a wrong report, not a refusal.
        self._scene()
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, operation="join"))
        assert out["fused"] is False and out["disjoint_join"] is True
        assert "Wall1" in out["note"] and "fused NOTHING" in out["note"]

    def test_a_created_body_with_NO_readable_identity_is_still_disclosed(self):
        # An unreadable identity is None, and None must not enter the census: admitted there, a
        # created body nobody could identify matches a standing body nobody could identify, and the
        # join publishes a fuse that was never verified. Both bodies here answer no entityToken.
        import types as _t
        standing = FakeBody("Standing", is_solid=True)
        wall = FakeBody("Wall1", is_solid=True)
        assert not hasattr(standing, "entityToken") and not hasattr(wall, "entityToken")
        tf = FakeThickenFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: standing)
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, operation="join"))
        assert out["fused"] is False and out["disjoint_join"] is True

    def test_a_real_fuse_inside_ONE_saved_document_is_still_not_flagged(self):
        # The other direction: reading the document must not split a body from itself, or every join
        # in a saved document would publish a fuse-nothing warning.
        import types as _t
        target = _body_from_document("Target", _SHARED_TOKEN, _URN_HOST)
        tf = FakeThickenFeatures(result_bodies=[target], created_faces=[_face_on(target)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: target)
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.thicken_handler(faces=["F1"], thickness=3, operation="join"))
        assert "fused" not in out and "disjoint_join" not in out


class TestCreatedBodyWalkKeysOnPhysicalIdentity:
    """_created_bodies delegates its owning-body walk to _geom.owning_bodies, whose de-dup key is
    _common.native_identity. A key built on the wrapper's own entityToken is wrong in two
    directions: it MERGES two distinct bodies whose document-local tokens collide, and it SPLITS one
    body reached both natively and through an occurrence proxy."""

    def test_two_created_bodies_sharing_a_document_local_token_are_both_published(self):
        # Keyed on the bare token these two DISTINCT bodies collapse to one entry, and the offset
        # publishes a single result body while the second disappears from the payload with no trace.
        a = _body_from_document("SurfA", _SHARED_TOKEN, _URN_XREF)
        b = _body_from_document("SurfB", _SHARED_TOKEN, _URN_HOST)
        assert a.entityToken == b.entityToken            # the fixture really models the collision
        of = FakeOffsetFeatures(result_bodies=[a, b], created_faces=[_face_on(a), _face_on(b)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.offset_handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["SurfA", "SurfB"]

    def test_one_body_reached_natively_and_through_its_proxy_is_ONE_result_body(self):
        native = _body_from_document("Surf1", _SHARED_TOKEN, _URN_HOST)
        proxy = body_proxy(native, types.SimpleNamespace(name="Surf1:1"))
        # the other half of the fixture: the proxy's OWN token differs, so a wrapper-token key would
        # report one physical body twice
        assert proxy.entityToken != native.entityToken
        of = FakeOffsetFeatures(result_bodies=[native],
                                created_faces=[_face_on(native), _face_on(proxy)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.offset_handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["Surf1"]
        assert out["faces_offset"] == 2      # the FACE count is the collection's own, not the walk's

    def test_two_created_bodies_with_no_readable_identity_stay_distinct(self):
        # The `or id(b)` last resort, unchanged by the delegation: two bodies nothing can be
        # identified from must over-count rather than merge into one entry.
        a, b = FakeBody("SurfA", is_solid=False), FakeBody("SurfB", is_solid=False)
        assert not hasattr(a, "entityToken") and not hasattr(b, "entityToken")
        of = FakeOffsetFeatures(result_bodies=[a, b], created_faces=[_face_on(a), _face_on(b)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.offset_handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["SurfA", "SurfB"]

    def test_several_faces_of_ONE_body_still_collapse_to_one(self):
        # The de-dup's day job, unchanged: three faces of one surface are one result body.
        surf = _body_from_document("Skin1", _SHARED_TOKEN, _URN_HOST)
        of = FakeOffsetFeatures(result_bodies=[surf],
                                created_faces=[_face_on(surf) for _ in range(3)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.offset_handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["Skin1"] and out["faces_offset"] == 3
