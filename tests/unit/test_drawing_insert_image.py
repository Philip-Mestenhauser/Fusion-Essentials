"""Unit tests for ``drawing_insert_image.py`` - place an image file on the active drawing sheet.

Covers: the file-exists refusal BEFORE any Fusion call (a raise inside createInput/insert rolls the
whole script transaction back), the sheet position in the drawing's own length unit (reported from
the drawing's own setting - never a guessed default), the input-did-not-take read-backs, and the
honesty gate - insert returning true while the document stays unmodified is a failure, and an
UNREADABLE modified flag is published as null, never false, since the Images collection exposes no
count, item or delete. No live Fusion.
"""

import json
import sys
import types

import pytest

import adsk  # the mock package conftest installed at import time
from conftest import load_tool

ins = load_tool("drawing_insert_image")

# The DrawingUnitTypes member names this build carries; the ints come SEEDED from the generated
# live_api_facts.py (conftest wires the strict enum), so a name absent from the measured family
# raises here instead of minting a fabricated value.
EXPECTED_UNIT_MEMBERS = {
    name: getattr(adsk.drawing.DrawingUnitTypes, name)
    for name in ("InchDrawingUnitType", "MillimeterDrawingUnitType")
}


class _PathIgnoringInput:
    """An ImageInsertInput whose imageFilePath assignment silently does not take - the SWIG-proxy
    shape the handler's read-back guards against."""

    imageFilePath = property(lambda self: "", lambda self, value: None)

    def __init__(self):
        self.position = None
        self.scale = None


class _UnreadableModifiedDoc:
    """A drawing document whose isModified read RAISES - the flag the payload must publish as null."""

    def __init__(self, drawing):
        self.drawing = drawing

    @property
    def isModified(self):
        raise RuntimeError("isModified unavailable")


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


@pytest.fixture
def env(monkeypatch):
    """An active drawing document with one millimetre sheet and a recording Images collection.

    The WHOLE adsk.drawing surface the tool and _drawing_common read is installed here (module
    attribute + sys.modules), so the test does not depend on what another drawing test file left
    behind, in either collection order.
    """
    state = {"insert_result": True, "modifies": True, "input": None, "insert_calls": [],
             "create_calls": 0}
    doc = types.SimpleNamespace(isModified=False)

    def _create_input():
        state["create_calls"] += 1
        state["input"] = state.get("input_factory", lambda: types.SimpleNamespace(
            imageFilePath="", position=None, scale=None))()
        return state["input"]

    def _insert(inp):
        state["insert_calls"].append(inp)
        if state["modifies"]:
            doc.isModified = True
        return state["insert_result"]

    images = types.SimpleNamespace(createInput=_create_input, insert=_insert)
    sheet = types.SimpleNamespace(name="Sheet1", images=images)
    settings = types.SimpleNamespace(units=EXPECTED_UNIT_MEMBERS["MillimeterDrawingUnitType"])
    doc.drawing = types.SimpleNamespace(activeSheet=sheet, documentSettings=settings)
    holder = types.SimpleNamespace(activeDocument=doc)

    fake_drawing = types.SimpleNamespace(
        DrawingDocument=types.SimpleNamespace(
            cast=lambda d: d if getattr(d, "drawing", None) is not None else None),
        DrawingUnitTypes=types.SimpleNamespace(**EXPECTED_UNIT_MEMBERS),
    )
    monkeypatch.setattr(adsk, "drawing", fake_drawing, raising=False)
    monkeypatch.setitem(sys.modules, "adsk.drawing", fake_drawing)
    monkeypatch.setattr(adsk.core.Application, "get", lambda: holder)
    monkeypatch.setattr(adsk.core.Point2D, "create", lambda x, y: ("pt", x, y))
    return types.SimpleNamespace(state=state, doc=doc, holder=holder, sheet=sheet,
                                 settings=settings, drawing=doc.drawing)


@pytest.fixture
def image_file(tmp_path):
    p = tmp_path / "logo.png"
    p.write_bytes(b"PNG-STUB")
    return str(p)


class TestHappyPath:
    def test_places_the_image_at_the_requested_sheet_position(self, env, image_file):
        out = _payload(ins.handler(image_path=image_file, x=60, y=100, scale=0.25))
        assert out["inserted"] is True
        assert out["image_path"] == image_file
        assert out["position"] == [60.0, 100.0]
        assert out["scale"] == 0.25
        assert out["sheet"] == "Sheet1"
        inp = env.state["input"]
        assert inp.imageFilePath == image_file
        assert inp.position == ("pt", 60.0, 100.0)      # x/y in sheet order, not swapped
        assert inp.scale == 0.25
        assert env.state["insert_calls"] == [inp]

    def test_omitted_scale_leaves_the_api_default_and_reports_null(self, env, image_file):
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert out["scale"] is None
        assert env.state["input"].scale is None         # never assigned - no invented 1.0

    def test_declared_returns_are_present(self, env, image_file):
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        for spec in ins.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)


class TestSheetUnits:
    def test_units_follow_the_drawings_own_setting(self, env, image_file):
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert out["sheet_units"] == "mm"
        env.settings.units = EXPECTED_UNIT_MEMBERS["InchDrawingUnitType"]
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert out["sheet_units"] == "in"

    def test_unreadable_units_are_published_as_null_not_guessed_mm(self, env, image_file):
        env.drawing.documentSettings = None
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert out["sheet_units"] is None

    def test_unrecognised_unit_value_is_published_as_null(self, env, image_file):
        env.settings.units = 99
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert out["sheet_units"] is None


class TestInputGuards:
    def test_missing_file_is_refused_before_any_fusion_call(self, env, tmp_path):
        res = ins.handler(image_path=str(tmp_path / "nope.png"), x=1, y=2)
        assert res["isError"] is True
        assert "not found" in res["message"]
        assert env.state["create_calls"] == 0          # nothing entered the Fusion transaction

    def test_empty_path_is_refused(self, env):
        res = ins.handler(x=1, y=2)
        assert res["isError"] is True
        assert "image_path" in res["message"]

    def test_missing_position_is_refused(self, env, image_file):
        res = ins.handler(image_path=image_file, x=1)
        assert res["isError"] is True
        assert "'x' and 'y'" in res["message"]
        assert env.state["create_calls"] == 0

    def test_non_numeric_position_is_refused(self, env, image_file):
        res = ins.handler(image_path=image_file, x="left", y=2)
        assert res["isError"] is True
        assert "must be numbers" in res["message"]

    def test_non_positive_scale_is_refused_naming_the_value(self, env, image_file):
        res = ins.handler(image_path=image_file, x=1, y=2, scale=0)
        assert res["isError"] is True
        assert "greater than 0" in res["message"]
        assert env.state["create_calls"] == 0

    def test_image_path_that_does_not_take_is_refused(self, env, image_file):
        env.state["input_factory"] = _PathIgnoringInput
        res = ins.handler(image_path=image_file, x=1, y=2)
        assert res["isError"] is True
        assert "reads back empty" in res["message"]
        assert env.state["insert_calls"] == []

    def test_active_document_that_is_not_a_drawing_is_refused(self, env, image_file):
        env.holder.activeDocument = types.SimpleNamespace(name="Design")
        res = ins.handler(image_path=image_file, x=1, y=2)
        assert res["isError"] is True
        assert "not a drawing" in res["message"]


class TestEffectHonesty:
    def test_insert_false_is_a_failure(self, env, image_file):
        env.state["insert_result"] = False
        res = ins.handler(image_path=image_file, x=1, y=2)
        assert res["isError"] is True
        assert "returned false" in res["message"]

    def test_success_that_leaves_the_document_unmodified_is_a_failure(self, env, image_file):
        env.state["modifies"] = False          # the API says true and changes nothing
        res = ins.handler(image_path=image_file, x=1, y=2)
        assert res["isError"] is True
        assert "still unmodified" in res["message"]

    def test_already_modified_document_is_reported_as_inconclusive(self, env, image_file):
        env.doc.isModified = True
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert out["document_modified_before"] is True
        assert "ALREADY modified" in out["note"]

    def test_unreadable_modified_flag_is_published_as_null_not_false(self, env, image_file):
        env.holder.activeDocument = _UnreadableModifiedDoc(env.drawing)
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert out["document_modified"] is None
        assert out["document_modified_before"] is None
        assert "could not be read" in out["note"]

    def test_note_states_the_image_cannot_be_read_back_or_removed(self, env, image_file):
        out = _payload(ins.handler(image_path=image_file, x=1, y=2))
        assert "no count, item or delete" in out["note"]
