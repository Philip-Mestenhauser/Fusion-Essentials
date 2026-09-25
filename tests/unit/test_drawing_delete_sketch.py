"""Unit tests for ``drawing_delete_sketch.py`` - delete a named sketch from a drawing sheet.

DrawingSketch.deleteMe is the only mutation surface a drawing sketch offers (a created entity
exposes nothing else, per drawing_add_sketch's own rollback), so the honesty gate is the sheet's
own sketch COUNT (before - 1) and the name's absence from a fresh walk - deleteMe's boolean is
reported beside that, never in its place. No live Fusion.
"""

import types

import pytest

from conftest import (FakeDrawingSketch, FakeDrawingSketches, FakeSheet, error_message, load_tool,
                      make_drawing, make_drawing_session, payload)

dd = load_tool("drawing_delete_sketch")


@pytest.fixture
def wire(monkeypatch):
    def _install(sheets=(("Sheet1", ("Detail",)),), active=0, is_drawing=True, delete_ok=True,
                 no_sketches=False):
        """`sheets` is [(name, (sketch_name, ...)), ...]; every sketch is built with `delete_ok`
        and OWNED by its collection (as Sketches.add would wire it) so deleteMe's removal reaches
        the collection the test asserts against."""
        built = []
        for sheet_name, sketch_names in sheets:
            items = [FakeDrawingSketch(name=n, delete_ok=delete_ok) for n in sketch_names]
            collection = FakeDrawingSketches(sketches=items)
            for s in items:
                s._owner = collection
            built.append(FakeSheet(sheet_name, sketches=collection))
        if no_sketches:
            for sheet in built:
                sheet.sketches = None
        document = make_drawing(sheets=built, active=active, name="Drw")
        make_drawing_session(monkeypatch, document if is_drawing else object())
        return types.SimpleNamespace(sheets=built, drawing=document.drawing)
    return _install


class TestDelete:
    def test_delete_lands_and_the_count_falls(self, wire):
        state = wire(sheets=(("Sheet1", ("Front", "Detail")),))
        out = payload(dd.handler(sketch="Detail"))
        assert out["deleted"] is True
        assert out["sketch"] == "Detail" and out["sheet"] == "Sheet1"
        assert out["sketch_count_before"] == 2 and out["sketch_count"] == 1
        assert state.sheets[0].sketches.count == 1
        assert "Front" == state.sheets[0].sketches.item(0).name   # the other sketch survives

    def test_the_name_match_is_case_insensitive(self, wire):
        state = wire(sheets=(("Sheet1", ("Detail",)),))
        out = payload(dd.handler(sketch="detail"))
        assert out["sketch"] == "detail" and state.sheets[0].sketches.count == 0

    def test_a_name_two_sketches_carry_refuses(self, wire):
        state = wire(sheets=(("Sheet1", ("Detail", "detail")),))
        msg = error_message(dd.handler(sketch="Detail"))
        assert "matches 2 sketches" in msg
        assert state.sheets[0].sketches.count == 2           # nothing deleted

    def test_an_unknown_name_lists_the_available_sketches(self, wire):
        wire(sheets=(("Sheet1", ("Front",)),))
        msg = error_message(dd.handler(sketch="Nope"))
        assert "No sketch named 'Nope'" in msg and "Front" in msg

    def test_a_deleteme_false_is_an_error(self, wire):
        state = wire(sheets=(("Sheet1", ("Detail",)),), delete_ok=False)
        msg = error_message(dd.handler(sketch="Detail"))
        assert "deleteMe returned false" in msg
        assert state.sheets[0].sketches.count == 1            # still there

    def test_empty_sketch_name_is_refused(self, wire):
        wire()
        assert "sketch" in error_message(dd.handler())

    def test_declared_returns_are_present(self, wire):
        wire(sheets=(("Sheet1", ("Detail",)),))
        out = payload(dd.handler(sketch="Detail"))
        for spec in dd.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)


class TestHonesty:
    def test_a_count_that_did_not_fall_is_an_error(self, wire):
        # deleteMe answers true while the sheet goes on listing the sketch - the count decides,
        # never the bare boolean.
        state = wire(sheets=(("Sheet1", ("Detail",)),), delete_ok="silent")
        msg = error_message(dd.handler(sketch="Detail"))
        assert "unverified" in msg
        assert state.sheets[0].sketches.count == 1

    def test_an_unreadable_count_after_success_is_an_error_not_a_silent_ok(self, wire):
        # deleteMe DID remove the sketch (the name walk finds no match) but the sheet's count will
        # not read AFTERWARD - an unconfirmable delete must not report ok either. The collection
        # only starts raising once deleteMe runs, so the pre-delete resolve still finds the sketch.
        state = wire(sheets=(("Sheet1", ("Detail",)),))
        collection = state.sheets[0].sketches
        sketch = collection.item(0)
        real_delete = sketch.deleteMe

        def _delete_then_break_count():
            landed = real_delete()
            collection._count_raises = True
            return landed
        sketch.deleteMe = _delete_then_break_count
        assert "unverified" in error_message(dd.handler(sketch="Detail"))


class TestSheetTargeting:
    def test_no_sheet_name_targets_the_active_sheet(self, wire):
        state = wire(sheets=(("Sheet1", ("A",)), ("Sheet2", ("B",))), active=1)
        out = payload(dd.handler(sketch="B"))
        assert out["sheet"] == "Sheet2"
        assert state.sheets[1].sketches.count == 0
        assert state.sheets[0].sketches.count == 1            # Sheet1 untouched

    def test_a_named_sheet_is_resolved_exactly(self, wire):
        state = wire(sheets=(("Sheet1", ("A",)), ("Sheet2", ("B",))), active=1)
        out = payload(dd.handler(sketch="A", sheet="Sheet1"))
        assert out["sheet"] == "Sheet1"
        assert state.sheets[0].sketches.count == 0

    def test_an_unknown_sheet_lists_the_sheets_instead_of_deleting(self, wire):
        state = wire(sheets=(("Sheet1", ("A",)),))
        msg = error_message(dd.handler(sketch="A", sheet="Nope"))
        assert "No sheet named 'Nope'" in msg
        assert state.sheets[0].sketches.count == 1


class TestDrawingDocument:
    def test_a_non_drawing_active_document_is_refused(self, wire):
        wire(is_drawing=False)
        assert "not a drawing" in error_message(dd.handler(sketch="Detail"))

    def test_a_sheet_with_no_sketches_collection_is_reported(self, wire):
        wire(no_sketches=True)
        assert "no sketches collection" in error_message(dd.handler(sketch="Detail"))
