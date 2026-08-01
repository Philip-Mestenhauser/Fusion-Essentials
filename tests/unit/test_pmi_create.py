"""Tests for `pmi_create` - the guards (one entity per note, faces-only hole notes, the closed
{symbol} vocabulary), the leader-extension pin, and the create-then-read-back composition."""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, error_message, BRepFace, BRepEdge

pc = load_tool("pmi_create")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _FakeAnn:
    def __init__(self, name="Note1", suffix="PMILeaderLineNote", text="DEBURR"):
        self.name = name
        self.objectType = "adsk::fusion::" + suffix
        self.plainText = text
        self.isVisible = True
        self.isOutOfDate = False
        self.isSuppressed = False
        self.errorOrWarningMessage = ""


class _FakeNotes:
    """leaderLineNotes/holeThreadNotes: createInput captures, add returns the canned annotation."""
    def __init__(self, ann, input_extension=0.5):
        self.ann = ann
        self.input = SimpleNamespace(leaderLineExtension=input_extension, segments=None)
        self.added = None

    def createInput(self, target):
        self.input.target = target
        return self.input

    def add(self, note_input):
        self.added = note_input
        return self.ann


def _face(comp):
    f = BRepFace(None)
    f.body = SimpleNamespace(parentComponent=comp)
    return f


@pytest.fixture
def rig(monkeypatch):
    """A component whose PMI collections are fakes; geometry resolution is stubbed per-test."""
    ann = _FakeAnn()
    notes = _FakeNotes(ann)
    hole_ann = _FakeAnn(name="Hole Note1", suffix="PMIHoleThreadNote", text="QTY")
    hole_notes = _FakeNotes(hole_ann)
    comp = SimpleNamespace(name="Root",
                           pmiAnnotations=SimpleNamespace(leaderLineNotes=notes,
                                                          holeThreadNotes=hole_notes))
    design = SimpleNamespace(rootComponent=comp)
    monkeypatch.setattr(pc._common, "design", lambda: design)

    def stub_geometry(ents):
        monkeypatch.setattr(pc._GEOMETRY, "resolve", lambda raw: (ents, None))
    return SimpleNamespace(comp=comp, notes=notes, hole_notes=hole_notes, ann=ann,
                           hole_ann=hole_ann, stub_geometry=stub_geometry)


class TestNoteGuards:
    def test_note_takes_exactly_one_entity(self, rig):
        rig.stub_geometry([_face(rig.comp), _face(rig.comp)])
        msg = error_message(pc.handler(kind="note", geometry=["a", "b"], text="X"))
        assert "exactly ONE" in msg and "2" in msg

    def test_note_refuses_a_non_brep_entity(self, rig):
        rig.stub_geometry([SimpleNamespace()])
        assert "face/edge/vertex" in error_message(
            pc.handler(kind="note", geometry=["a"], text="X"))

    def test_unknown_symbol_token_is_refused_with_the_vocabulary(self, rig):
        rig.stub_geometry([_face(rig.comp)])
        msg = error_message(pc.handler(kind="note", geometry=["a"], text="{bogus}"))
        assert "'{bogus}'" in msg and "flatness" in msg

    def test_no_active_design_is_an_error(self, monkeypatch):
        monkeypatch.setattr(pc._common, "design", lambda: None)
        assert "No active design" in error_message(
            pc.handler(kind="note", geometry=["a"], text="X"))


class TestHoleNoteGuards:
    def test_hole_note_refuses_non_face_entities(self, rig):
        rig.stub_geometry([BRepEdge(None)])
        assert "FACE handles" in error_message(pc.handler(kind="hole_note", geometry=["a"]))

    def test_hole_note_refuses_text(self, rig):
        rig.stub_geometry([_face(rig.comp)])
        msg = error_message(pc.handler(kind="hole_note", geometry=["a"], text="custom"))
        assert "does not take 'text'" in msg


class TestCreate:
    def test_note_created_and_read_back(self, rig):
        rig.stub_geometry([_face(rig.comp)])
        out = _payload(pc.handler(kind="note", geometry=["a"], text="{flatness}0.05"))
        assert out["annotation"] == "Note1" and out["kind"] == "note"
        assert rig.notes.added is rig.notes.input
        assert len(rig.notes.input.segments) == 2      # symbol + text landed on the input

    def test_low_input_extension_is_pinned_to_the_default(self, rig):
        rig.notes.input.leaderLineExtension = 0.13     # below the platform edit floor
        rig.stub_geometry([_face(rig.comp)])
        _payload(pc.handler(kind="note", geometry=["a"], text="X"))
        assert rig.notes.input.leaderLineExtension == pc._pmi.LEADER_EXT_DEFAULT

    def test_null_add_is_an_error(self, rig):
        rig.notes.add = lambda note_input: None
        rig.stub_geometry([_face(rig.comp)])
        assert "no annotation was created" in error_message(
            pc.handler(kind="note", geometry=["a"], text="X"))

    def test_hole_note_created(self, rig):
        rig.stub_geometry([_face(rig.comp)])
        out = _payload(pc.handler(kind="hole_note", geometry=["a"]))
        assert out["annotation"] == "Hole Note1" and out["kind"] == "hole_note"

    def test_rename_that_does_not_take_is_reported(self, rig):
        class Stubborn(_FakeAnn):
            @property
            def name(self):
                return "Note1"

            @name.setter
            def name(self, v):
                pass
        rig.notes.ann = Stubborn()
        rig.stub_geometry([_face(rig.comp)])
        out = _payload(pc.handler(kind="note", geometry=["a"], text="X", name="MyNote"))
        assert "did not take" in out["rename_warning"]

    def test_rename_applies(self, rig):
        rig.stub_geometry([_face(rig.comp)])
        out = _payload(pc.handler(kind="note", geometry=["a"], text="X", name="MyNote"))
        assert out["annotation"] == "MyNote" and "rename_warning" not in out

    def test_bad_text_point_reports_but_names_the_created_annotation(self, rig):
        rig.stub_geometry([_face(rig.comp)])
        msg = error_message(pc.handler(kind="note", geometry=["a"], text="X",
                                       text_point=["x", 0, 0]))
        assert "text_point" in msg and "WAS created" in msg
