"""Unit tests for ``joint_create_origin.py``.

Targets: ``_kp_name`` (reverse keypoint-enum -> readable name), ``_vec``
(rounding/None), the input-validation branches of ``_geometry_from_args``
(missing ``sketch_name``, out-of-range entity index) that gate geometry
construction before any Fusion call, and ``handler()`` itself - its own
guards (unknown anchor/target/keypoint/units, no active design), the
coordinate-anchor unit scaling, and the created-origin report (frame_axes,
name override).
"""

import json
from types import SimpleNamespace

from conftest import load_tool

jo = load_tool("joint_create_origin")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


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

class _FakeFace:
    def __init__(self, planar):
        stype = "PLANE" if planar else "CYL"
        self.geometry = type("G", (), {"surfaceType": stype})()


class _FakeEdge:
    pass


class _FakeVertex:
    pass


def _install_geom(handle_map):
    """Wire adsk types + a design whose findEntityByToken resolves the geometry handles."""
    import adsk.core, adsk.fusion
    adsk.fusion.BRepFace = _FakeFace
    adsk.fusion.BRepEdge = _FakeEdge
    adsk.fusion.BRepVertex = _FakeVertex
    adsk.fusion.ConstructionPoint = type("CP", (), {})
    adsk.fusion.SketchPoint = type("SP", (), {})
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
    st = adsk.core.SurfaceTypes
    st.PlaneSurfaceType = "PLANE"; st.CylinderSurfaceType = "CYL"; st.ConeSurfaceType = "CONE"
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


# ── handler(): guards, coordinate-anchor scaling, and the created-origin report ─────────────────

class _FakeSketchPoints:
    def add(self, pt):
        return SimpleNamespace(point=pt)


class _FakeSketch:
    def __init__(self):
        self.name = None
        self.sketchPoints = _FakeSketchPoints()


class _FakeSketches:
    def add(self, plane):
        return _FakeSketch()


class _FakeJointOriginInput:
    def __init__(self):
        self.primaryAxisVector = SimpleNamespace(x=0.0, y=0.0, z=1.0)
        self.secondaryAxisVector = SimpleNamespace(x=1.0, y=0.0, z=0.0)
        self.thirdAxisVector = SimpleNamespace(x=0.0, y=1.0, z=0.0)


class _FakeJointOrigin:
    def __init__(self):
        self.name = "JointOrigin1"


class _FakeJointOrigins:
    def __init__(self):
        self.count = 0

    def createInput(self, geom):
        return _FakeJointOriginInput()

    def add(self, jo_input):
        self.count += 1
        return _FakeJointOrigin()


class _FakeComp:
    def __init__(self):
        self.name = "Comp1"
        self.sketches = _FakeSketches()
        self.xYConstructionPlane = object()
        self.jointOrigins = _FakeJointOrigins()


class _FakeDesign:
    def __init__(self):
        self.rootComponent = _FakeComp()


def _install_handler(monkeypatch, design=None):
    """Wire a fake design + the adsk seams _geometry_from_args/handler touch for anchor='coordinates'.
    Returns (design, point3d_calls) so a test can assert the exact cm values Point3D.create received.
    """
    d = design if design is not None else _FakeDesign()
    monkeypatch.setattr(jo._common, "design", lambda: d)
    import adsk.core
    import adsk.fusion
    calls = []

    def _create(x, y, z):
        calls.append((x, y, z))
        return SimpleNamespace(x=x, y=y, z=z)

    monkeypatch.setattr(adsk.core.Point3D, "create", staticmethod(_create))
    monkeypatch.setattr(adsk.fusion.JointGeometry, "createByPoint",
                        staticmethod(lambda pt: SimpleNamespace(anchor_point=pt)))
    return d, calls


class TestHandlerGuards:
    def test_no_active_design_errors(self, monkeypatch):
        monkeypatch.setattr(jo._common, "design", lambda: None)
        res = jo.handler()
        assert res["isError"] is True and "design" in res["message"].lower()

    def test_unknown_anchor_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(anchor="wormhole")
        assert res["isError"] is True and "wormhole" in res["message"]

    def test_unknown_target_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(target="mars")
        assert res["isError"] is True and "mars" in res["message"]

    def test_unknown_keypoint_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(keypoint="nowhere")
        assert res["isError"] is True and "nowhere" in res["message"]

    def test_unknown_units_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(units="furlong")
        assert res["isError"] is True and "furlong" in res["message"]


class TestHandlerCoordinateAnchor:
    def test_coordinates_at_scales_by_the_unit_factor(self, monkeypatch):
        _, calls = _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates", target="at", x=10, y=0, z=0, units="mm"))
        assert calls[-1] == (1.0, 0.0, 0.0)          # 10 mm * 0.1 cm/mm
        assert out["location"] == {"x": 10, "y": 0, "z": 0, "units": "mm"}

    def test_target_origin_ignores_xyz_and_reports_zero_location(self, monkeypatch):
        _, calls = _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates", target="origin",
                                          x=99, y=99, z=99, units="mm"))
        assert calls[-1] == (0.0, 0.0, 0.0)
        assert out["location"] == {"x": 0.0, "y": 0.0, "z": 0.0, "units": "mm"}

    def test_creates_joint_origin_and_reports_frame_axes(self, monkeypatch):
        d, _ = _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates"))
        assert out["created"] is True
        assert out["joint_origin_name"] == "JointOrigin1"
        assert out["frame_axes"]["primary_axis_Z"] == [0.0, 0.0, 1.0]
        assert d.rootComponent.jointOrigins.count == 1

    def test_custom_name_is_applied_to_the_new_joint_origin(self, monkeypatch):
        _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates", name="Anchor1"))
        assert out["joint_origin_name"] == "Anchor1"
