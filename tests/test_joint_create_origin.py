"""Unit tests for ``joint_origin.py`` pure logic.

Targets: ``_kp_name`` (reverse keypoint-enum -> readable name), ``_vec``
(rounding/None), and the input-validation branches of ``_geometry_from_args``
(missing ``sketch_name``, out-of-range entity index) that gate geometry
construction before any Fusion call.
"""

from types import SimpleNamespace

from conftest import load_tool

jo = load_tool("joint_create_origin")


# ── _vec: round to 6, None passthrough ─────────────────────────────────────

class TestVec:
    def test_rounds_components(self):
        assert jo._vec(SimpleNamespace(x=1.0000001, y=2.5, z=-3.0)) == [1.0, 2.5, -3.0]

    def test_none_passthrough(self):
        assert jo._vec(None) is None


# ── _kp_name: reverse lookup against the keypoint table ────────────────────

class TestKpName:
    def test_known_value_maps_to_name(self):
        # Pick any registered keypoint and round-trip it through the reverse map.
        name, value = next(iter(jo._KEYPOINTS.items()))
        assert jo._kp_name(value) == name

    def test_unknown_value_falls_back_to_str(self):
        assert jo._kp_name(123456) == "123456"


# ── _geometry_from_args: validation gates ──────────────────────────────────

def _call(anchor="coordinates", target="at", x=0, y=0, z=0,
          sketch_name="", entity_index=0, keypoint=None, design=None, comp=None,
          geometry_handle=None):
    # Signature: (design, comp, anchor, target, x_cm, y_cm, z_cm,
    #             sketch_name, entity_index, keypoint, geometry_handle)
    return jo._geometry_from_args(
        design or SimpleNamespace(), comp or SimpleNamespace(),
        anchor, target, x, y, z, sketch_name, entity_index, keypoint, geometry_handle,
    )


class TestGeometryFromArgsValidation:
    def test_sketch_line_without_sketch_name_errors(self):
        g, desc, err = _call(anchor="sketch_line", sketch_name="")
        assert g is None
        assert "sketch_name" in err

    def test_sketch_point_without_sketch_name_errors(self):
        g, desc, err = _call(anchor="sketch_point", sketch_name="")
        assert g is None
        assert "sketch_name" in err

    def test_sketch_line_missing_sketch_errors(self, monkeypatch):
        # sketch_name given, but no such sketch exists -> clear "no sketch named" error.
        monkeypatch.setattr(jo, "_find_sketch", lambda design, name: None)
        g, desc, err = _call(anchor="sketch_line", sketch_name="Ghost")
        assert g is None
        assert "Ghost" in err

    def test_sketch_line_index_out_of_range_errors(self, monkeypatch):
        # A sketch exists with 1 line; asking for index 5 must be rejected.
        one_line = SimpleNamespace(
            sketchCurves=SimpleNamespace(
                sketchLines=SimpleNamespace(count=1)
            )
        )
        monkeypatch.setattr(jo, "_find_sketch", lambda design, name: one_line)
        g, desc, err = _call(anchor="sketch_line", sketch_name="S", entity_index=5)
        assert g is None
        assert "out of range" in err


# ── anchor='geometry': BRep face/edge/vertex handle (geometry-as-values) ────────────────────────

class _FakePlane:
    pass


class _FakeFace:
    def __init__(self, planar):
        self.geometry = _FakePlane() if planar else object()  # plane vs non-plane surface


class _FakeEdge:
    pass


class _FakeVertex:
    pass


def _install_geom(handle_map):
    """Wire adsk types + a design whose findEntityByToken resolves the geometry handles."""
    import adsk.core, adsk.fusion
    adsk.core.Plane = _FakePlane
    adsk.fusion.BRepFace = _FakeFace
    adsk.fusion.BRepEdge = _FakeEdge
    adsk.fusion.BRepVertex = _FakeVertex
    # JointGeometry factory records which create* was used
    calls = {}
    class JG:
        @staticmethod
        def createByPlanarFace(face, edge, kp):
            calls["kind"] = "planar_face"; return ("g", "planar")
        @staticmethod
        def createByNonPlanarFace(face, kp):
            calls["kind"] = "non_planar_face"; return ("g", "nonplanar")
        @staticmethod
        def createByCurve(edge, kp):
            calls["kind"] = "curve"; return ("g", "curve")
        @staticmethod
        def createByPoint(v):
            calls["kind"] = "point"; return ("g", "point")
    adsk.fusion.JointGeometry = JG
    kpt = adsk.fusion.JointKeyPointTypes
    kpt.CenterKeyPoint = 3; kpt.MiddleKeyPoint = 1
    class _D:
        def findEntityByToken(self, t):
            e = handle_map.get(t)
            return [e] if e is not None else []
    jo._inputs._common.design = lambda: _D()
    return calls


class TestGeometryAnchor:
    def test_planar_face_uses_createByPlanarFace(self):
        calls = _install_geom({"F": _FakeFace(planar=True)})
        g, desc, err = _call(anchor="geometry", geometry_handle="F")
        assert err is None and g is not None
        assert calls["kind"] == "planar_face"
        assert "normal" in desc

    def test_cylinder_face_uses_nonplanar(self):
        calls = _install_geom({"C": _FakeFace(planar=False)})
        g, desc, err = _call(anchor="geometry", geometry_handle="C")
        assert err is None and calls["kind"] == "non_planar_face"

    def test_edge_uses_createByCurve(self):
        calls = _install_geom({"E": _FakeEdge()})
        g, desc, err = _call(anchor="geometry", geometry_handle="E", keypoint=1)
        assert err is None and calls["kind"] == "curve"
        assert "edge" in desc.lower()

    def test_vertex_uses_createByPoint(self):
        calls = _install_geom({"V": _FakeVertex()})
        g, desc, err = _call(anchor="geometry", geometry_handle="V")
        assert err is None and calls["kind"] == "point"

    def test_bad_geometry_handle_errors(self):
        _install_geom({})   # nothing resolves
        g, desc, err = _call(anchor="geometry", geometry_handle="missing")
        assert g is None and err is not None
