"""Unit tests for surface_trim.py - cell OWNERSHIP, the cell selection, the phantom-cell gate and
the abort."""

import types

import adsk.core
import adsk.fusion
import pytest

from conftest import (BRepBody, BRepEdge, BRepFace, FakeFeature as _SharedFeature, MakeComp,
                      _make_object_collection, _NamedCollection, install, load_tool, make_design,
                      make_occurrence, payload)

se = load_tool("surface_trim")
surface_common_mod = load_tool("_surface_common")


@pytest.fixture(autouse=True)
def _adsk_seams(monkeypatch):
    """The adsk types the input kinds isinstance-check, and a ValueInput.createByReal returning the
    ('real', cm) pair a scaled length is read off. FeatureOperations arrives seeded."""
    monkeypatch.setattr(adsk.fusion, "BRepBody", BRepBody, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepFace", BRepFace, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepEdge", BRepEdge, raising=False)
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal",
                        staticmethod(lambda v: ("real", v)), raising=False)


_UNSET = object()


def _body(name="Surf1", is_solid=False, area=None, solid_readable=True, parent_component=_UNSET):
    """One BRep body - the trim target or a result body; `area` left None models one whose area
    will not read, which is the reading the phantom-cell gate stands down on. It owns a component
    placed nowhere, as every live body owns one; `parent_component=None` is that read FAILING."""
    comp = (MakeComp(name="Root", entity_token="ROOT") if parent_component is _UNSET
            else parent_component)
    return BRepBody(name, is_solid=is_solid, area=area, solid_readable=solid_readable,
                    parent_component=comp)


def _wire(trim_features, handle_map=None):
    """Install a design whose active component carries `trim_features`, with `handle_map` behind
    the geometry handles."""
    bodies = [b for b in (handle_map or {}).values() if isinstance(b, BRepBody)]
    comp = MakeComp(bodies=bodies)
    comp.features = types.SimpleNamespace(trimFeatures=trim_features)
    install(se, make_design(comp=comp, tokens=dict(handle_map or {})))
    return comp


class FakeFeature(_SharedFeature):
    """A TrimFeature: the shared feature under the result bodies the area read-back walks."""
    def __init__(self, name="Feat1", bodies=None):
        super().__init__(name=name,
                         bodies=bodies if bodies is not None else [_body("Body1")])


def _source_tools(owner):
    """The ObjectCollection BRepCell.sourceTools answers, holding the measured pair - the tool
    BRepFace, then the ONE BRepBody the cell was cut from."""
    collection = _make_object_collection()
    collection.add(BRepFace(None))
    collection.add(owner)
    return collection


class FakeBRepCell:
    """A candidate cell the trim tool divided a surface into - a local double, BRepCell carrying no
    shape dump. isSelected is settable; for a Trim feature a SELECTED cell is REMOVED, and
    cellBody.area sizes it. `owner` None leaves the cell answering no sourceTools at all: a DECLARED
    worst case, not a measured shape - what it pins is that an ownership the handler cannot read is
    refused rather than guessed at."""
    def __init__(self, area, owner=None):
        self.isSelected = False
        self.cellBody = _body("Cell", area=area)
        if owner is not None:
            self.sourceTools = _source_tools(owner)


class FakeBRepCells:
    """The cells one createInput populated. Without `owners` every cell belongs to the default
    target body, the scene where the tool crosses nothing else."""
    def __init__(self, areas, owners=None):
        owners = list(owners) if owners is not None else [_body() for _ in areas]
        self._cells = [FakeBRepCell(a, owner=owners[i]) for i, a in enumerate(areas)]
    @property
    def count(self):
        return len(self._cells)
    def item(self, i):
        return self._cells[i]


class FakeTrimInput:
    def __init__(self, tool, cell_areas, cell_owners=None):
        self.tool = tool
        self.cancelled = False
        self.bRepCells = FakeBRepCells(cell_areas, cell_owners)
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
    REPRODUCES the live contract: it RAISES "No cells are selected" when no cell isSelected - so a
    handler that forgets the selection step fails exactly as it did live. raise_on_add / null_feature
    exercise the failure paths where cancel() MUST be called."""
    def __init__(self, result_bodies=None, raise_on_add=False, null_feature=False,
                 cell_areas=(3.0, 9.0, 1.0), cell_owners=None):
        self.last_input = None
        self._result = result_bodies
        self._raise = raise_on_add
        self._null = null_feature
        self._cell_areas = list(cell_areas)
        self._cell_owners = cell_owners
    def createInput(self, tool):
        self.last_input = FakeTrimInput(tool, self._cell_areas, self._cell_owners)
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


class TestSurfaceTrim:

    def _keep_scene(self):
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1")], cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        return tf

    def test_commits_via_add_on_success(self):
        # cells: areas 3, 9, 1 -> default keeps the largest (index 1)
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1")], cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T"))
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
        tf = FakeTrimFeatures(cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True   # passes only because a cell is now selected before add()

    def test_trim_that_removes_no_area_bites(self):
        # committed, cells were marked removed, but the surface area is identical -> error, not ok
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1", area=12.0)],
                              cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(area=12.0), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "did not decrease" in res["message"]

    def test_trim_that_shrinks_area_passes(self):
        # the removed cells' area is gone
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1", area=9.0)],
                              cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(area=12.0), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True

    def test_keep_smaller_keeps_smallest_cell(self):
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1")], cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T", keep="smaller"))
        cells = tf.last_input.bRepCells
        assert cells.item(2).isSelected is False          # smallest (area 1) kept
        assert cells.item(0).isSelected is True and cells.item(1).isSelected is True
        assert out["cells_kept"] == [2] and out["kept_area"] == 1.0

    def test_keep_by_index_keeps_that_cell(self):
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1")], cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T", keep="0"))
        cells = tf.last_input.bRepCells
        assert cells.item(0).isSelected is False          # kept by index
        assert cells.item(1).isSelected is True and cells.item(2).isSelected is True
        assert out["cells_kept"] == [0]

    def test_keep_list_of_indices(self):
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1")], cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T", keep=[0, 2]))
        cells = tf.last_input.bRepCells
        assert cells.item(0).isSelected is False and cells.item(2).isSelected is False
        assert cells.item(1).isSelected is True           # only the unlisted cell removed
        assert out["cells_kept"] == [0, 2] and out["cells_removed"] == [1]

    def test_keep_int_index_keeps_that_cell(self):
        # keep passed as an actual int (not a string) -> _select_cells int branch
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1")], cell_areas=(3.0, 9.0, 1.0))
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T", keep=2))
        cells = tf.last_input.bRepCells
        assert cells.item(2).isSelected is False          # kept by int index
        assert cells.item(0).isSelected is True and cells.item(1).isSelected is True
        assert out["cells_kept"] == [2]

    def test_keep_out_of_range_index_is_refused_naming_the_value(self):
        # silently falling back to 'largest' trims a DIFFERENT piece than the caller asked to keep,
        # with nothing in the payload saying so - the refusal names the index and the legal range
        tf = self._keep_scene()
        res = se.handler(surface="S", trim_tool="T", keep=[7, 9])
        assert res["isError"] is True
        assert "'keep' index 7 does not exist" in res["message"]
        assert "0..2" in res["message"]
        assert tf.last_input.cancelled is True            # the open transaction was aborted

    def test_keep_index_at_the_last_cell_is_accepted(self):
        # total-1 is IN range: the boundary the out-of-range refusal must not swallow
        tf = self._keep_scene()
        out = payload(se.handler(surface="S", trim_tool="T", keep=2))
        assert out["cells_kept"] == [2]
        assert tf.last_input.cancelled is False

    def test_keep_index_one_past_the_last_cell_is_refused(self):
        # total itself is OUT of range - the > vs >= boundary of the range check
        self._keep_scene()
        res = se.handler(surface="S", trim_tool="T", keep=3)
        assert res["isError"] is True
        assert "'keep' index 3 does not exist" in res["message"]

    def test_negative_keep_index_is_refused_not_read_as_from_the_end(self):
        self._keep_scene()
        res = se.handler(surface="S", trim_tool="T", keep=-1)
        assert res["isError"] is True
        assert "'keep' index -1 does not exist" in res["message"]

    def test_bad_keep_is_refused_naming_the_value(self):
        tf = self._keep_scene()
        res = se.handler(surface="S", trim_tool="T", keep="garbage")
        assert res["isError"] is True
        assert "'garbage' is not one" in res["message"]
        assert "'larger', 'smaller'" in res["message"]
        assert tf.last_input.cancelled is True

    def test_boolean_keep_is_refused_not_taken_as_index_one(self):
        # True is an int in Python - taken as an index it would keep cell 1 silently
        self._keep_scene()
        res = se.handler(surface="S", trim_tool="T", keep=True)
        assert res["isError"] is True
        assert "True is not one" in res["message"]

    def test_a_list_with_one_bad_member_is_refused_whole(self):
        # keeping the parseable members and dropping the rest would trim a piece nobody named
        self._keep_scene()
        res = se.handler(surface="S", trim_tool="T", keep=[0, "x"])
        assert res["isError"] is True
        assert "'x' is not one" in res["message"]

    def test_empty_keep_still_takes_the_larger_default(self):
        # '' / [] are an OMISSION, not a bad value - they must not be refused
        self._keep_scene()
        out = payload(se.handler(surface="S", trim_tool="T", keep=""))
        assert out["cells_kept"] == [1]                   # largest (area 9 at index 1)

    def test_an_unreadable_cell_leaves_the_area_of_every_later_cell_at_its_own_index(self):
        # 'keep' takes a cell INDEX and the kept indices are published, so the areas list is indexed
        # by cell number. A cell that cannot be read measures 0 in ITS slot: compacting the list
        # instead would make the third cell the second, and 'keep larger' would select the stale one.
        inp = FakeTrimInput(BRepFace(None), (3.0, 9.0, 5.0))
        _stale_cell_at(inp.bRepCells, 1)
        info, _foreign, err = se._select_cells(inp, None, _body())
        assert err is None and info["cells_total"] == 3 and info["cells_unread"] == 1
        # the largest READABLE cell, at its true index
        assert info["cells_kept"] == [2] and info["kept_area"] == 5.0

    def test_an_unreadable_cell_does_not_shift_which_cells_are_selected(self):
        # the selection walk writes isSelected per index: cell 2 was asked for, so cell 2 is the one
        # left unselected (KEPT for a trim) and cell 0 is the one selected for removal
        inp = FakeTrimInput(BRepFace(None), (3.0, 9.0, 5.0))
        first, third = inp.bRepCells.item(0), inp.bRepCells.item(2)
        _stale_cell_at(inp.bRepCells, 1)
        info, _foreign, err = se._select_cells(inp, 2, _body())
        assert err is None and info["cells_kept"] == [2] and info["cells_total"] == 3
        assert third.isSelected is False             # kept: the cell the caller named
        assert first.isSelected is True              # removed

    def test_phantom_cell_kept_area_exceeds_input_aborts(self):
        # The gate that stands INDEPENDENT of ownership: a cell claiming the target as its owner but
        # measuring more than the target's whole area cannot be a piece of it. 'keep larger' would
        # latch onto it -> abort BEFORE add(), cancel the txn, and name the other owners read.
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1")],
                              cell_areas=(4.0, 20.0, 6.0))   # largest cell (20) exceeds the 16 input
        _wire(tf, handle_map={"S": _body(area=16.0), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "more than the target's own" in res["message"]
        assert "Other cell owners read: none" in res["message"]
        assert tf.last_input.cancelled is True          # transaction aborted, no feature landed

    def test_kept_area_within_input_still_trims(self):
        # The same scene without the phantom: the kept cell is smaller than the input -> a normal trim.
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1", area=12.0)],
                              cell_areas=(4.0, 12.0, 6.0))
        _wire(tf, handle_map={"S": _body(area=16.0), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True and out["kept_area"] == 12.0

    def test_no_cells_cancels_and_reports_no_intersection(self):
        tf = FakeTrimFeatures(cell_areas=())              # createInput divided nothing
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert tf.last_input.cancelled is True            # open transaction aborted
        assert "no cells" in res["message"].lower()

    def test_cancels_open_transaction_when_add_raises(self):
        tf = FakeTrimFeatures(raise_on_add=True)
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        # THE HAZARD: cancel() was CALLED (not swallowed) so the open transaction is aborted
        assert tf.last_input.cancelled is True

    def test_cancels_when_add_returns_null_feature(self):
        tf = FakeTrimFeatures(null_feature=True)
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert tf.last_input.cancelled is True
        assert "cancelled" in res["message"].lower()

    def test_wrong_kind_surface_gets_redirect_before_any_transaction(self):
        # a SOLID handed where a surface is required -> redirecting error, no createInput called
        tf = FakeTrimFeatures()
        _wire(tf, handle_map={"S": _body("Body1", is_solid=True), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "OPEN SURFACE body" in res["message"] and "SOLID body" in res["message"]
        assert tf.last_input is None        # never opened a transaction


class TestTrimCellOwnership:

    """The coplanar-neighbour scene MEASURED live (Fusion 2705.1.15): two overlapping sheets and a
    cylinder crossing both give 5 cells - 2 on the target, 2 on the neighbour, 1 on the cutter
    itself - so a pick over ALL of them removes the target outright."""

    def _decoy_scene(self, **kw):
        """(trim features, target, neighbour) for the rig: target Body1 owns cells 0-1, neighbour
        Body2 cells 2-3, the cutter Body3 cell 4. The cell areas are the live cm2 readings, and
        each sheet's 16.0 is the sum of its own two."""
        target = _body("Body1", area=16.0)
        decoy, cutter = _body("Body2", area=16.0), _body("Body3", area=8.0)
        tf = FakeTrimFeatures(cell_areas=(0.7854, 15.2146, 0.3927, 15.6073, 6.2832),
                              cell_owners=[target, target, decoy, decoy, cutter], **kw)
        _wire(tf, handle_map={"S": target, "T": BRepFace(None)})
        return tf, target, decoy

    def test_only_the_targets_cells_are_removed_with_a_larger_neighbour_present(self):
        # cell 3 (15.6073) is the largest cell of all, and it belongs to the NEIGHBOUR: a pick over
        # every cell keeps that one and selects cells 0-1, removing the whole target.
        tf, _target, _decoy = self._decoy_scene(result_bodies=[_body("Body1", area=15.2146)])
        out = payload(se.handler(surface="S", trim_tool="T"))
        cells = tf.last_input.bRepCells
        assert out["cells_kept"] == [1] and out["cells_removed"] == [0]
        assert cells.item(1).isSelected is False        # the target's larger cell KEPT
        assert cells.item(0).isSelected is True         # the disc inside the cutter REMOVED
        assert [cells.item(i).isSelected for i in (2, 3, 4)] == [False, False, False]
        assert out["foreign_cells"] == [{"index": 2, "owner": "Root:Body2"},
                                        {"index": 3, "owner": "Root:Body2"},
                                        {"index": 4, "owner": "Root:Body3"}]
        assert out["cells_total"] == 5 and "cells_unread" not in out
        assert out["foreign_bodies_unchanged"] is True
        assert "3 cell(s) owned by another body were left in place" in out["note"]

    def test_keep_naming_a_foreign_cell_is_refused_naming_its_owner(self):
        # index 3 is a legal GLOBAL cell address, so the range check passes it - what refuses it is
        # ownership. Trimming on it would keep a neighbour's cell and remove the target's own.
        tf, _target, _decoy = self._decoy_scene()
        res = se.handler(surface="S", trim_tool="T", keep=3)
        assert res["isError"] is True
        assert "'keep' cell 3 belongs to 'Root:Body2', not the target" in res["message"]
        assert "[0, 1]" in res["message"]               # the cells that CAN be kept
        assert tf.last_input.cancelled is True

    def test_keep_smaller_picks_the_smallest_cell_of_the_target(self):
        # cell 2 (0.3927) is the smallest cell in the compute and belongs to the neighbour; the
        # target's own smallest is cell 0.
        tf, _target, _decoy = self._decoy_scene(result_bodies=[_body("Body1", area=0.7854)])
        out = payload(se.handler(surface="S", trim_tool="T", keep="smaller"))
        cells = tf.last_input.bRepCells
        assert out["cells_kept"] == [0] and out["cells_removed"] == [1]
        assert cells.item(2).isSelected is False        # the smaller foreign cell is NOT removed
        assert out["kept_area"] == 0.7854

    def test_a_foreign_body_whose_area_moved_after_the_add_is_an_error(self):
        # the effect read: the trim may not reach a body the target does not own, and an ok payload
        # over a neighbour that lost area is the false success this gate exists for. The neighbour
        # moves by its OWN half-disc (16.0 -> 15.6073, 2.45%) - the size of the mistake this gate
        # exists to catch, not a hole a coarse tolerance would still see.
        tf, _target, decoy = self._decoy_scene(result_bodies=[_body("Body1", area=15.2146)])
        landed = tf.add

        def add(inp):
            feature = landed(inp)
            decoy.area = 15.6073       # the add took the neighbour's half-disc too
            return feature
        tf.add = add
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        # named ONCE, though two of its cells were in the compute
        assert res["message"].count("'Root:Body2' 16.0 -> 15.6073 cm2") == 1

    def test_a_foreign_area_that_will_not_read_leaves_the_verdict_null(self):
        # the tri-state: a body whose area will not read is not a body PROVEN untouched, and a
        # confident True would claim an effect check that never ran.
        target = _body("Body1", area=12.0)
        tf = FakeTrimFeatures(result_bodies=[_body("Body1", area=9.0)],
                              cell_areas=(3.0, 9.0, 4.0),
                              # 'Other' carries no area at all, so neither side of the add reads
                              cell_owners=[target, target, _body("Other")])
        _wire(tf, handle_map={"S": target, "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T"))
        assert out["foreign_bodies_unchanged"] is None
        assert out["unverified"] == ["foreign_bodies_unchanged"]
        assert "UNVERIFIED" in out["note"]

    def test_a_hidden_target_names_visibility_at_both_refusals(self):
        # MEASURED: a hidden body contributes NO cells to createInput. So a hidden target's compute
        # carries only other bodies' cells - and "the trim tool must INTERSECT the TARGET" is then a
        # false diagnosis of a cutter that does intersect it.
        for areas, owners in (((3.0, 9.0), [_body("Other")] * 2), ((), None)):
            target = _body("Body1", area=16.0)
            target.isLightBulbOn = False
            tf = FakeTrimFeatures(cell_areas=areas, cell_owners=owners)
            _wire(tf, handle_map={"S": target, "T": BRepFace(None)})
            res = se.handler(surface="S", trim_tool="T")
            assert res["isError"] is True
            assert "'Root:Body1' does not read as visible" in res["message"]
            assert "view_set action='show'" in res["message"]
            assert "INTERSECT" not in res["message"]
            assert tf.last_input.cancelled is True

    def test_all_unread_ownership_refuses_and_cancels(self):
        # no cell named an owning body, so no cell can be proven the target's - selecting any of
        # them is a guess at which surface gets cut.
        tf = FakeTrimFeatures(cell_areas=(3.0, 9.0), cell_owners=[None, None])
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "none of the 2 cell(s) named an owning body" in res["message"]
        assert tf.last_input.cancelled is True

    def _placed_scene(self, *paths, walk=None):
        """A target body owned by a component placed at each of `paths` - the placement census the
        multi-instance guard counts. `walk` replaces that census with one a curated map cannot
        express: a walk that will not run, or one whose count or rows will not read."""
        sheet = MakeComp(name="Sheet", entity_token="SHEET")
        target = _body("Body1", area=16.0)
        target.parentComponent = sheet
        tf = FakeTrimFeatures(result_bodies=[_body("Body1", area=9.0)], cell_areas=(3.0, 9.0),
                              cell_owners=[target, target])
        comp = MakeComp(bodies=[target], occurrences_by_component={
            "Sheet": [make_occurrence(path=p, component=sheet) for p in paths]})
        comp.features = types.SimpleNamespace(trimFeatures=tf)
        if walk is not None:
            comp.allOccurrencesByComponent = walk
        install(se, make_design(comp=comp, tokens={"S": target, "T": BRepFace(None)}))
        return tf

    def test_a_component_placed_twice_refuses_before_any_transaction(self):
        # MEASURED: a trim is a feature of the COMPONENT - both placements changed, and the call
        # reported keeping a 0.478 cm2 cell while 0.785 cm2 landed, read the same through either
        # placement. Both area gates passed while the cells described neither.
        tf = self._placed_scene("Sheet:1", "Sheet:2")
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "placed 2 times" in res["message"]
        assert "'Sheet:1'" in res["message"] and "'Sheet:2'" in res["message"]
        assert tf.last_input is None        # refused before the transaction was ever opened

    def test_a_component_placed_once_still_trims(self):
        # the boundary the refusal must not swallow: one placement is the ordinary case
        tf = self._placed_scene("Sheet:1")
        out = payload(se.handler(surface="S", trim_tool="T"))
        assert out["trimmed"] is True and tf.last_input.cancelled is False

    def _unowned_scene(self):
        """A target whose owning component does not read - live every body has one, so this is the
        READ failing, never a body owned by nothing."""
        tf = FakeTrimFeatures()
        _wire(tf, handle_map={"S": _body(parent_component=None), "T": BRepFace(None)})
        return tf

    def test_a_placement_read_that_does_not_answer_refuses(self):
        # "could not count" is not "placed once": the area gates do not catch a multi-placement
        # trim, so proceeding on an unread census is proceeding on the state this guard exists for.
        def unreadable(_component):
            raise RuntimeError("2 : InternalValidationError : res")
        for build, named in (
                (lambda: self._placed_scene("Sheet:1", walk=unreadable), "the placement census"),
                (lambda: self._placed_scene("Sheet:1",
                                            walk=lambda _c: _NamedCollection([], raises="stale")),
                 "the placement count"),
                (self._unowned_scene, "the target's own component")):
            tf = build()
            res = se.handler(surface="S", trim_tool="T")
            assert res["isError"] is True
            assert f"{named} did not read" in res["message"]
            assert tf.last_input is None

    def test_a_census_of_two_whose_rows_do_not_read_still_counts_two(self):
        # the count is the SIZE, never the rows that survived the walk: dropping an unreadable row
        # turns a component placed twice into one placed once, which is the trim this guard allows.
        tf = self._placed_scene("Sheet:1", "Sheet:2",
                                walk=lambda _c: _NamedCollection([None, None], item_raises="stale"))
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "placed 2 times ('?', '?')" in res["message"]
        assert tf.last_input is None

    def test_cells_that_all_belong_to_another_body_refuse_naming_the_owner(self):
        tf = FakeTrimFeatures(cell_areas=(3.0, 9.0), cell_owners=[_body("Other")] * 2)
        _wire(tf, handle_map={"S": _body(), "T": BRepFace(None)})
        res = se.handler(surface="S", trim_tool="T")
        assert res["isError"] is True
        assert "none of the 2 cell(s) computed belong to the target" in res["message"]
        assert "'Root:Other'" in res["message"]
        assert tf.last_input.cancelled is True


class TestSolidVerdictOverBodyFacts:

    """trim/extend collapse the shared per-body {name, is_solid} projection into one verdict. That
    projection publishes True/False/None, so the collapse keeps the three apart - any() would fold
    an unreadable flag into a confident 'a surface'."""

    def test_the_verdict_keeps_the_three_states_apart(self):
        assert surface_common_mod._solid_verdict([False, True]) is True        # any solid wins
        assert surface_common_mod._solid_verdict([False, None]) is False       # a flag READ false is an answer
        assert surface_common_mod._solid_verdict([None, None]) is None         # nothing read -> unknown
        assert surface_common_mod._solid_verdict([]) is None                   # no body read -> unknown

    def test_trim_publishes_a_null_is_solid_with_an_unverified_marker(self):
        rb = _body("Surf1", area=12.0, solid_readable=False)
        tf = FakeTrimFeatures(result_bodies=[rb], cell_areas=(4.0, 12.0, 6.0))
        _wire(tf, handle_map={"S": _body(area=16.0), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T"))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "Not read back off the feature: is_solid." in out["note"]

    def test_trim_on_a_readable_flag_carries_no_marker(self):
        # the boundary: a flag that READ false is an answer, so the disclosure must not fire on it.
        tf = FakeTrimFeatures(result_bodies=[_body("Surf1", area=12.0)],
                              cell_areas=(4.0, 12.0, 6.0))
        _wire(tf, handle_map={"S": _body(area=16.0), "T": BRepFace(None)})
        out = payload(se.handler(surface="S", trim_tool="T"))
        assert out["is_solid"] is False and "unverified" not in out
