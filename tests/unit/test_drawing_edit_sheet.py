"""Unit tests for ``drawing_edit_sheet.py`` - the active drawing's sheet lifecycle.

The fake carries the measured contract of the sheet surface, and each clause of it is a way this
tool can report a change that did not happen:
  - a duplicate sheet name RAISES on add and is a SILENT NO-OP on rename (sheet names are
    case-insensitively unique), so the name a sheet reports is the only name worth publishing;
  - a sheet size belonging to the other drawing standard RAISES, as does portrait on the largest
    sheet of a standard - and a raise inside a drawing document is not reliably rolled back, so
    both are refused before anything is set;
  - width and height are read-only and derive from size + orientation, so they are the evidence a
    resize really landed;
  - deleteMe returns true while the sheet collection still reports its pre-delete count - the
    boolean IS the effect, and an unchanged count is never a failure;
  - Sheet.tidyUp is a property whose READ tidies the sheet, and on an already-modified document the
    modified flag cannot confirm it.
"""

import sys
import types
from types import SimpleNamespace

import pytest

import adsk  # the mock package conftest installed at import time
from conftest import error_message, load_tool, payload

es = load_tool("drawing_edit_sheet")

# fake SheetSizes / SheetOrientationTypes values -> the landscape (width, height) the fake sheet
# derives, the way Fusion derives its read-only width/height from size + orientation.
_SIZE_EXTENT = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0),
                "A0": (1189.0, 841.0), "B": (431.8, 279.4)}


class FakeSheet:
    """One Sheet. A class, not a namespace, because the surface under test is its PROPERTIES:
    width/height derived from size + orientation, a name assignment that silently no-ops on a
    duplicate, size/orientation assignments that raise the way Fusion does, and tidyUp - a property
    whose READ performs the tidy-up."""

    def __init__(self, name, size="A3", orientation="LAND", views=0, sketches=0, tables=0,
                 delete_result=True, tidy_result=True, rename_lands=True, size_raises=False,
                 size_ignored=False, orientation_raises=False, orientation_ignored=False):
        self._name = name
        self._size = size
        self._orientation = orientation
        self.doc = None                     # set when it joins a drawing
        self.views = SimpleNamespace(count=views)
        self.sketches = SimpleNamespace(count=sketches)
        self.customTables = SimpleNamespace(count=tables)
        self.delete_result = delete_result
        self.tidy_result = tidy_result
        self.rename_lands = rename_lands
        self.size_raises = size_raises
        self.size_ignored = size_ignored
        self.orientation_raises = orientation_raises
        self.orientation_ignored = orientation_ignored
        self.copy_result = "ok"
        self.copy_activates = True
        self.copy_args = []
        self.size_sets = 0                  # assignments ATTEMPTED - a pre-guard must leave it at 0
        self.orientation_sets = 0
        self.tidy_reads = 0
        self.deleted = False

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        # a duplicate (or case-variant) name is ignored without raising - measured
        if self.rename_lands:
            self._name = value

    @property
    def sheetSize(self):
        return self._size

    @sheetSize.setter
    def sheetSize(self, value):
        self.size_sets += 1
        if self.size_raises:
            raise RuntimeError("3 : Sheet size is not valid for the active drawing standard.")
        if not self.size_ignored:
            self._size = value

    @property
    def orientation(self):
        return self._orientation

    @orientation.setter
    def orientation(self, value):
        self.orientation_sets += 1
        if self.orientation_raises:
            raise RuntimeError("3 : Portrait orientation is not supported for ISO A0 sheet size.")
        if not self.orientation_ignored:
            self._orientation = value

    @property
    def width(self):
        w, h = _SIZE_EXTENT[self._size]
        return w if self._orientation == "LAND" else h

    @property
    def height(self):
        w, h = _SIZE_EXTENT[self._size]
        return h if self._orientation == "LAND" else w

    @property
    def tidyUp(self):
        self.tidy_reads += 1
        if self.doc is not None:
            self.doc.isModified = True
        return self.tidy_result

    def deleteMe(self):
        # the collection deliberately does NOT shrink: a drawing delete is invisible in its own call
        self.deleted = True
        return self.delete_result

    def copy(self, name, before):
        self.copy_args.append((name, before))
        if self.copy_result == "null":
            return None
        made = FakeSheet(name or (self._name + " copy"), size=self._size,
                         orientation=self._orientation, views=self.views.count,
                         sketches=self.sketches.count, tables=self.customTables.count)
        made.doc = self.doc
        if self.copy_result != "no_growth":
            self.landing(made, activate=self.copy_activates)
        return made


def _enum(**members):
    return SimpleNamespace(**members)


def _drawing_module():
    """A COMPLETE stand-in for adsk.drawing. It is installed WHOLESALE (module object + the
    sys.modules entry) rather than by patching attributes onto whatever adsk.drawing happens to be:
    sibling drawing test modules swap that object at import time, so an attribute patch survives
    only in one collection order."""
    d = types.ModuleType("adsk.drawing")
    d.DrawingDocument = SimpleNamespace(cast=lambda doc: None)
    d.SheetSizes = _enum(
        A4ISOSheetSize="A4", A3ISOSheetSize="A3", A2ISOSheetSize="A2", A1ISOSheetSize="A1",
        A0ISOSheetSize="A0", AASMESheetSize="A", BASMESheetSize="B", CASMESheetSize="C",
        DASMESheetSize="D", EASMESheetSize="E")
    d.SheetOrientationTypes = _enum(
        LandscapeSheetOrientationType="LAND", PortraitSheetOrientationType="PORT")
    d.DrawingStandardTypes = _enum(ISODrawingStandardType="ISO", ASMEDrawingStandardType="ASME")
    d.DrawingUnitTypes = _enum(MillimeterDrawingUnitType="MM", InchDrawingUnitType="IN")
    return d


@pytest.fixture
def wire(monkeypatch):
    """Install a drawing document as the active document; return its live state."""
    drawing = _drawing_module()
    monkeypatch.setattr(adsk, "drawing", drawing, raising=False)
    monkeypatch.setitem(sys.modules, "adsk.drawing", drawing)

    def _install(sheets=None, standard="ISO", units="MM", active=0, is_drawing=True,
                 add_result="ok", modified=False, copy_activates=True):
        objs = list(sheets or [FakeSheet("Sheet1")])
        doc = SimpleNamespace(name="Widget Drawing", isModified=modified)
        for s in objs:
            s.doc = doc
        coll = SimpleNamespace(count=len(objs), created_inputs=[])
        dwg = SimpleNamespace(sheets=coll, activeSheet=objs[active] if objs else None,
                              documentSettings=SimpleNamespace(standard=standard, units=units))

        def landing(made, activate=True):
            """A sheet joining the drawing: it lands last and becomes the active sheet."""
            made.doc = doc
            made.landing = landing
            made.copy_activates = copy_activates
            objs.append(made)
            coll.count = len(objs)
            if activate:
                dwg.activeSheet = made

        for s in objs:
            s.landing = landing
            s.copy_activates = copy_activates
        coll.item = lambda i: objs[i]
        coll.itemByName = lambda n: next((s for s in objs if s.name == n), None)

        def create_input():
            si = SimpleNamespace(name="")
            coll.created_inputs.append(si)
            return si

        def add(sheet_input):
            if add_result == "null":
                return None
            if any(s.name == sheet_input.name for s in objs):
                raise RuntimeError("3 : A sheet with that name already exists.")
            active_sheet = dwg.activeSheet
            made = FakeSheet(sheet_input.name or "Sheet%d" % (len(objs) + 1),
                             size=active_sheet.sheetSize, orientation=active_sheet.orientation)
            if add_result == "renamed":
                # the created sheet reports a name of its own, whatever was asked for
                made._name = (sheet_input.name or "") + " (2)"
            if add_result == "no_growth":
                made.doc = doc
                made.landing = landing
                return made
            landing(made)
            return made

        coll.createInput = create_input
        coll.add = add
        monkeypatch.setattr(es, "app", SimpleNamespace(activeDocument=doc))
        drawing.DrawingDocument.cast = (
            lambda _doc: SimpleNamespace(drawing=dwg) if is_drawing else None)
        return SimpleNamespace(drawing=dwg, sheets=objs, doc=doc, collection=coll)

    return _install


class TestGuards:
    def test_non_drawing_active_document_is_refused(self, wire):
        wire(is_drawing=False)
        assert "not a drawing" in error_message(es.handler(action="add"))

    def test_unknown_action_lists_the_vocabulary(self, wire):
        wire()
        msg = error_message(es.handler(action="reorder"))
        assert "tidy_up" in msg and "reorder" in msg

    def test_missing_action_is_refused(self, wire):
        wire()
        assert "action" in error_message(es.handler())


class TestSheetTargeting:
    def test_unknown_name_lists_the_drawing_sheets(self, wire):
        wire([FakeSheet("Front"), FakeSheet("Detail")])
        msg = error_message(es.handler(action="tidy_up", sheet="Sction"))
        assert "Front" in msg and "Detail" in msg

    def test_name_matches_case_insensitively_and_exactly(self, wire):
        state = wire([FakeSheet("Front", views=2), FakeSheet("Front Detail", views=3)])
        out = payload(es.handler(action="tidy_up", sheet="front"))
        assert out["sheet"] == "Front" and out["views"] == 2
        assert state.sheets[1].tidy_reads == 0

    def test_omitted_sheet_acts_on_the_active_sheet(self, wire):
        wire([FakeSheet("Front"), FakeSheet("Detail")], active=1)
        assert payload(es.handler(action="tidy_up"))["sheet"] == "Detail"


class TestAdd:
    def test_new_sheet_inherits_the_active_sheet_and_becomes_active(self, wire):
        state = wire([FakeSheet("Front", size="A2", orientation="PORT")])
        out = payload(es.handler(action="add", new_name="Detail"))
        assert out["sheet"] == "Detail"
        assert out["sheet_count_before"] == 1 and out["sheet_count"] == 2
        assert out["facts"]["sheet_size"] == "a2" and out["facts"]["orientation"] == "portrait"
        assert out["facts"]["width"] == 420.0 and out["facts"]["height"] == 594.0
        assert out["sheet_units"] == "mm"
        assert state.drawing.activeSheet.name == "Detail"

    def test_a_duplicate_name_carries_the_platform_refusal(self, wire):
        # measured: Sheets.add with a name a sheet already holds RAISES - there is no dedupe
        state = wire([FakeSheet("Front")])
        msg = error_message(es.handler(action="add", new_name="Front"))
        assert "already exists" in msg
        assert len(state.sheets) == 1

    def test_the_published_name_is_the_one_the_sheet_reports(self, wire):
        # whatever name the created sheet reports is the one a caller can address later, so the
        # payload publishes THAT - never the string the call asked for
        state = wire([FakeSheet("Front")], add_result="renamed")
        out = payload(es.handler(action="add", new_name="Detail"))
        assert out["sheet"] == "Detail (2)" == state.sheets[-1].name
        assert out["requested_name"] == "Detail"
        assert "Detail (2)" in out["name_warning"]

    def test_add_returning_nothing_is_an_error(self, wire):
        wire(add_result="null")
        assert "returned nothing" in error_message(es.handler(action="add"))

    def test_add_that_did_not_grow_the_drawing_is_an_error(self, wire):
        wire(add_result="no_growth")
        assert "did not take" in error_message(es.handler(action="add", new_name="Detail"))


class TestCopy:
    def test_copy_publishes_what_the_copy_carries_and_where_it_landed(self, wire):
        state = wire([FakeSheet("Front", sketches=1, tables=2, views=4)])
        out = payload(es.handler(action="copy", sheet="Front", new_name="Front Copy"))
        assert out["copied_from"] == "Front" and out["sheet"] == "Front Copy"
        # the facts are read off the COPY, not off the sheet it was copied from
        assert out["facts"]["name"] == "Front Copy"
        assert out["facts"]["sketches"] == 1 and out["facts"]["custom_tables"] == 2
        assert out["sheet_count"] == 2 and state.drawing.activeSheet.name == "Front Copy"

    def test_a_copy_carries_the_source_sheets_settings_not_the_active_sheets(self, wire):
        # a copy takes the SOURCE sheet's size and orientation - only an ADD inherits the active
        # sheet's, so the payload and its note must not claim otherwise
        wire([FakeSheet("Front", size="A2", orientation="PORT"), FakeSheet("Detail", size="A4")],
             active=1)
        out = payload(es.handler(action="copy", sheet="Front", new_name="Front Copy"))
        assert out["facts"]["sheet_size"] == "a2" and out["facts"]["orientation"] == "portrait"
        assert "SOURCE" in out["note"] and "not the active sheet's" in out["note"]

    def test_the_facts_come_from_the_sheet_copy_returned(self, wire):
        # the object copy() handed back is the one to read - not whichever sheet the drawing
        # happens to report as active afterwards
        wire([FakeSheet("Front", size="A2", sketches=1)], copy_activates=False)
        out = payload(es.handler(action="copy", sheet="Front", new_name="Front Copy"))
        assert out["sheet"] == "Front Copy" and out["facts"]["name"] == "Front Copy"

    def test_the_copy_call_pins_the_measured_argument_shape(self, wire):
        # copy(name, False) appends the copy at the END and makes it active - the tool's payload
        # says so, so the flag it passes is load-bearing
        state = wire([FakeSheet("Front")])
        payload(es.handler(action="copy", sheet="Front", new_name="Front Copy"))
        assert state.sheets[0].copy_args == [("Front Copy", False)]

    def test_null_copy_names_the_async_cause(self, wire):
        sheet = FakeSheet("Front")
        sheet.copy_result = "null"
        wire([sheet])
        msg = error_message(es.handler(action="copy", sheet="Front"))
        assert "asynchronously" in msg and "returned nothing" in msg

    def test_copy_that_did_not_grow_the_drawing_is_an_error(self, wire):
        sheet = FakeSheet("Front")
        sheet.copy_result = "no_growth"
        wire([sheet])
        assert "did not take" in error_message(es.handler(action="copy", sheet="Front"))


class TestDelete:
    def test_unchanged_count_is_reported_not_failed(self, wire):
        # measured: deleteMe returns true while the collection still reports its pre-delete count -
        # the delete must NOT be called a failure, and the count must NOT be sold as a verification
        state = wire([FakeSheet("Front"), FakeSheet("Detail")])
        out = payload(es.handler(action="delete", sheet="Detail"))
        assert out["deleted"] is True and out["sheet"] == "Detail"
        assert out["sheet_count_before"] == 2 and out["sheet_count_still_reads"] == 2
        assert "NOT a verification" in out["note"] and "later call" in out["note"]
        assert state.sheets[1].deleted is True

    def test_refused_delete_is_an_error(self, wire):
        wire([FakeSheet("Front"), FakeSheet("Detail", delete_result=False)])
        assert "refused" in error_message(es.handler(action="delete", sheet="Detail"))

    def test_the_only_sheet_is_not_deleted(self, wire):
        state = wire([FakeSheet("Front")])
        assert "only sheet" in error_message(es.handler(action="delete", sheet="Front"))
        assert state.sheets[0].deleted is False


class TestRename:
    def test_rename_reads_the_name_back(self, wire):
        state = wire([FakeSheet("Sheet1")])
        out = payload(es.handler(action="rename", sheet="Sheet1", new_name="Front"))
        assert out["sheet"] == "Front" and out["previous_name"] == "Sheet1"
        assert out["changed"] is True
        assert state.sheets[0].name == "Front"

    def test_a_silently_ignored_rename_is_an_error(self, wire):
        # measured: assigning a name another sheet holds (or a case variant) no-ops without raising
        state = wire([FakeSheet("Front", rename_lands=False), FakeSheet("Detail")])
        msg = error_message(es.handler(action="rename", sheet="Front", new_name="Detail"))
        assert "did not take" in msg and "case-insensitively unique" in msg
        assert state.sheets[0].name == "Front"

    def test_same_name_is_a_no_op(self, wire):
        wire([FakeSheet("Front")])
        out = payload(es.handler(action="rename", sheet="Front", new_name="Front"))
        assert out["changed"] is False

    def test_rename_needs_a_new_name(self, wire):
        wire([FakeSheet("Front")])
        assert "new_name" in error_message(es.handler(action="rename", sheet="Front"))


class TestSetSize:
    def test_size_and_extent_are_read_back(self, wire):
        state = wire([FakeSheet("Front", size="A3", orientation="LAND")])
        out = payload(es.handler(action="set_size", sheet="Front", sheet_size="a4"))
        assert out["sheet_size"] == "a4" and out["previous_sheet_size"] == "a3"
        assert out["width"] == 297.0 and out["height"] == 210.0
        assert out["previous_width"] == 420.0 and out["sheet_units"] == "mm"
        assert state.sheets[0].sheetSize == "A4"

    def test_a_size_from_the_other_standard_is_refused_before_anything_is_set(self, wire):
        # measured: Fusion RAISES on a mismatched size, and a raise in a drawing document is not
        # reliably rolled back - so the mismatch must never reach the assignment
        state = wire([FakeSheet("Front", size="A3", size_raises=True)], standard="ISO")
        msg = error_message(es.handler(action="set_size", sheet="Front", sheet_size="b"))
        # the full phrase is pinned: the refusal reads as English, never "a ASME sheet size"
        assert "The ASME sheet size 'b' is not valid for this drawing" in msg
        assert "Choose one of the ISO sizes." in msg
        assert state.sheets[0].sheetSize == "A3"
        assert state.sheets[0].size_sets == 0        # the assignment was never attempted

    def test_a_platform_refusal_reaches_the_caller_verbatim(self, wire):
        # standard unreadable -> the guard cannot fire, so the raise itself must be carried
        state = wire([FakeSheet("Front", size="A3", size_raises=True)], standard=None)
        msg = error_message(es.handler(action="set_size", sheet="Front", sheet_size="a4"))
        assert "not valid for the active drawing standard" in msg
        assert state.sheets[0].sheetSize == "A3"

    def test_a_silently_ignored_size_is_an_error(self, wire):
        wire([FakeSheet("Front", size="A3", size_ignored=True)])
        assert "did not take" in error_message(es.handler(action="set_size", sheet="Front",
                                                          sheet_size="a4"))

    def test_set_size_needs_a_size(self, wire):
        wire([FakeSheet("Front")])
        assert "sheet_size" in error_message(es.handler(action="set_size", sheet="Front"))


class TestSetOrientation:
    def test_orientation_swaps_the_extent(self, wire):
        state = wire([FakeSheet("Front", size="A4", orientation="LAND")])
        out = payload(es.handler(action="set_orientation", sheet="Front", orientation="portrait"))
        assert out["orientation"] == "portrait" and out["previous_orientation"] == "landscape"
        assert out["width"] == 210.0 and out["height"] == 297.0
        assert out["previous_width"] == 297.0
        assert state.sheets[0].orientation == "PORT"

    def test_portrait_on_the_largest_sheet_is_refused_before_anything_is_set(self, wire):
        # measured: portrait on an ISO A0 sheet RAISES
        state = wire([FakeSheet("Front", size="A0", orientation_raises=True)], standard="ISO")
        msg = error_message(es.handler(action="set_orientation", sheet="Front",
                                       orientation="portrait"))
        # the full phrase is pinned, and the orientation it reports is the one it READ
        assert "portrait orientation on the ISO A0 sheet size" in msg
        assert "keeps its current orientation ('landscape')" in msg
        assert state.sheets[0].orientation == "LAND"
        assert state.sheets[0].orientation_sets == 0  # the assignment was never attempted

    def test_an_unreadable_current_orientation_is_named_as_such(self, wire):
        # the refusal publishes what it READ - an orientation it cannot decode says so
        wire([FakeSheet("Front", size="A0", orientation="SIDEWAYS")], standard="ISO")
        msg = error_message(es.handler(action="set_orientation", sheet="Front",
                                       orientation="portrait"))
        assert "keeps its current orientation ('unreadable')" in msg

    def test_a_platform_refusal_reaches_the_caller_verbatim(self, wire):
        state = wire([FakeSheet("Front", size="A4", orientation_raises=True)], standard=None)
        msg = error_message(es.handler(action="set_orientation", sheet="Front",
                                       orientation="portrait"))
        assert "not supported for ISO A0 sheet size" in msg
        assert state.sheets[0].orientation == "LAND"

    def test_a_silently_ignored_orientation_is_an_error(self, wire):
        wire([FakeSheet("Front", size="A4", orientation_ignored=True)])
        assert "did not take" in error_message(es.handler(action="set_orientation", sheet="Front",
                                                          orientation="portrait"))

    def test_an_unknown_orientation_is_refused(self, wire):
        wire([FakeSheet("Front")])
        assert "orientation" in error_message(es.handler(action="set_orientation", sheet="Front",
                                                         orientation="sideways"))


class TestTidyUp:
    def test_tidy_up_reads_the_property_exactly_once(self, wire):
        state = wire([FakeSheet("Front", views=4)])
        out = payload(es.handler(action="tidy_up", sheet="Front"))
        assert out["tidied"] is True and out["views"] == 4
        assert out["document_modified"] is True and out["modified_confirmed"] is True
        assert state.doc.isModified is True
        assert state.sheets[0].tidy_reads == 1

    def test_a_tidy_that_left_the_document_clean_is_an_error(self, wire):
        sheet = FakeSheet("Front")
        state = wire([sheet])
        state.doc.isModified = False
        sheet.doc = None                       # tidyUp returns true but dirties nothing
        assert "still unmodified" in error_message(es.handler(action="tidy_up", sheet="Front"))

    def test_an_already_modified_document_cannot_confirm_the_tidy(self, wire):
        # measured: tidying an already-modified document returns true with isModified already true
        wire([FakeSheet("Front")], modified=True)
        out = payload(es.handler(action="tidy_up", sheet="Front"))
        assert out["tidied"] is True and out["modified_confirmed"] is False
        assert "cannot confirm" in out["note"]

    def test_a_false_tidy_up_is_an_error(self, wire):
        wire([FakeSheet("Front", tidy_result=False)])
        assert "did not tidy" in error_message(es.handler(action="tidy_up", sheet="Front"))

    @pytest.mark.parametrize("kwargs", [
        {"action": "add", "new_name": "Detail"},
        {"action": "rename", "sheet": "Front", "new_name": "Cover"},
        {"action": "set_size", "sheet": "Front", "sheet_size": "a4"},
        {"action": "set_orientation", "sheet": "Front", "orientation": "portrait"},
        {"action": "copy", "sheet": "Front"},
        {"action": "delete", "sheet": "Front"},
    ])
    def test_no_other_action_touches_the_mutating_property(self, wire, kwargs):
        # reading Sheet.tidyUp TIDIES the sheet - every other path must leave it untouched
        state = wire([FakeSheet("Front", views=2), FakeSheet("Spare")])
        es.handler(**kwargs)
        assert [s.tidy_reads for s in state.sheets] == [0] * len(state.sheets)


class TestDescriptionClaims:
    def test_the_add_vs_copy_inheritance_split_is_pinned(self):
        # Both halves are measured: an added sheet inherits the ACTIVE sheet's settings, a copy
        # carries the SOURCE sheet's. A swap is a wrong wire claim on every add or copy.
        assert "An ADDED sheet inherits the ACTIVE sheet" in es.TOOL_DESCRIPTION
        assert "a COPY the SOURCE sheet" in es.TOOL_DESCRIPTION
