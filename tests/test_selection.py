"""Unit tests for the ``selection.py`` MCP tool's pure logic.

The bug surface here is the geometry classification: ``_unit`` (vector
normalization, including the zero-vector guard), ``_face_direction`` /
``_edge_direction`` (which branch on the runtime surface/curve type name), and
``_classify`` (the big dispatch by ``type(entity).__name__``). None of it needs
a live Fusion — only fakes whose class names match Fusion's type names.
"""

import math

from conftest import (
    BRepEdge,
    BRepFace,
    Circle3D,
    Cylinder,
    FakePoint,
    FakeVector3D,
    Line3D,
    Plane,
    Sphere,
    load_tool,
)

sel = load_tool("selection")


# ── _unit: normalization + zero-vector guard ───────────────────────────────

class TestUnit:
    def test_normalizes_to_length_one(self):
        u = sel._unit(FakeVector3D(0, 0, 5))
        assert u == [0.0, 0.0, 1.0]

    def test_arbitrary_vector_normalized(self):
        u = sel._unit(FakeVector3D(3, 4, 0))  # length 5
        assert u == [0.6, 0.8, 0.0]
        assert math.isclose(math.sqrt(sum(c * c for c in u)), 1.0, abs_tol=1e-9)

    def test_zero_vector_returns_none(self):
        # A zero-length vector has no direction — must be None, not [0,0,0].
        assert sel._unit(FakeVector3D(0, 0, 0)) is None

    def test_none_input_returns_none(self):
        assert sel._unit(None) is None


# ── _face_direction: branch on surface type ────────────────────────────────

class TestFaceDirection:
    def test_planar_face_returns_normal(self):
        face = BRepFace(Plane(FakeVector3D(0, 0, 2)))
        vec, kind = sel._face_direction(face)
        assert vec == [0.0, 0.0, 1.0]
        assert kind == "face_normal"

    def test_cylindrical_face_returns_axis(self):
        face = BRepFace(Cylinder(FakeVector3D(0, 10, 0)))
        vec, kind = sel._face_direction(face)
        assert vec == [0.0, 1.0, 0.0]
        assert kind == "axis"

    def test_sphere_has_no_direction(self):
        face = BRepFace(Sphere())
        vec, kind = sel._face_direction(face)
        assert vec is None
        assert kind is None


# ── _edge_direction: branch on curve type ──────────────────────────────────

class TestEdgeDirection:
    def test_linear_edge_direction_is_end_minus_start(self):
        edge = BRepEdge(Line3D(), start=FakePoint(1, 0, 0), end=FakePoint(4, 0, 0))
        vec, kind = sel._edge_direction(edge)
        assert vec == [1.0, 0.0, 0.0]   # +X, unit
        assert kind == "edge_direction"

    def test_circular_edge_direction_is_plane_normal(self):
        edge = BRepEdge(Circle3D(FakeVector3D(0, 0, 7)))
        vec, kind = sel._edge_direction(edge)
        assert vec == [0.0, 0.0, 1.0]
        assert kind == "axis"


# ── _classify: dispatch by entity type ─────────────────────────────────────

class TestClassify:
    def test_face_is_classified_with_direction(self):
        out = sel._classify(BRepFace(Plane(FakeVector3D(0, 0, 1))))
        assert out["object_type"] == "BRepFace"
        assert out["kind"] == "face"
        assert out["surface_type"] == "Plane"
        assert out["direction"] == [0.0, 0.0, 1.0]
        assert out["direction_kind"] == "face_normal"

    def test_edge_is_classified_as_edge(self):
        out = sel._classify(BRepEdge(Line3D(), start=FakePoint(0, 0, 0), end=FakePoint(0, 2, 0)))
        assert out["kind"] == "edge"
        assert out["curve_type"] == "Line3D"
        assert out["direction"] == [0.0, 1.0, 0.0]

    def test_unknown_entity_falls_through_to_other(self):
        class Mystery:
            name = "weird"
        out = sel._classify(Mystery())
        assert out["object_type"] == "Mystery"
        assert out["kind"] == "other"


# ── handler 'require' mismatch flagging ────────────────────────────────────

class TestRequireFlag:
    """sys_get_selection should flag when the selection doesn't match 'require'.

    We bypass the live UI by stubbing _ui()/_classify the minimum needed: build a
    fake Selections collection holding one BRepFace and check the require logic.
    """

    def _fake_ui_with(self, entities):
        class _Sel:
            def __init__(self, e):
                self.entity = e
                self.point = FakePoint(0, 0, 0)

        class _Sels:
            def __init__(self, es):
                self._es = [_Sel(e) for e in es]

            @property
            def count(self):
                return len(self._es)

            def item(self, i):
                return self._es[i]

        class _UI:
            activeSelections = _Sels(entities)

        return _UI()

    def test_require_face_matches_a_face(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with([BRepFace(Plane(FakeVector3D(0, 0, 1)))]))
        result = sel.get_user_selection_handler(require="face")
        payload = _payload(result)
        assert payload["matches_required"] is True

    def test_require_edge_flags_mismatch_when_face_selected(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with([BRepFace(Plane(FakeVector3D(0, 0, 1)))]))
        result = sel.get_user_selection_handler(require="edge")
        payload = _payload(result)
        assert payload["matches_required"] is False
        assert "note" in payload

    def test_nothing_selected_is_an_error(self, monkeypatch):
        monkeypatch.setattr(sel, "_ui", lambda: self._fake_ui_with([]))
        result = sel.get_user_selection_handler()
        assert result["isError"] is True


def _payload(result):
    import json
    return json.loads(result["content"][0]["text"])
