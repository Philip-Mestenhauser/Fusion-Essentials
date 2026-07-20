"""Unit tests for ``construction.py`` — point / axis / plane construction datums.

Pinned: units scaling on coordinates/offset, the kind/axis/plane guards, that a point sets the
scaled Point3D, an axis builds an InfiniteLine with the right direction, a plane offsets from the
named origin plane, and the friendly direct-modeling 'Environment is not supported' error. Also
pins every non-legacy mode (plane/axis/point): mode-vs-kind dispatch, per-mode required-input
guards (exact counts, missing scalars, wrong geometry kind), the setBy*-returned-false path, and
the geometry sanity read-back (normal/direction/origin read off the CREATED datum).
"""

import json
import math

from conftest import (BRepEdge, BRepFace, Circle3D, Cone, Cylinder, FakePoint, FakeUnitsManager,
                      FakeVector3D, Line3D, Plane, load_tool)

cn = load_tool("model_construction")


class _CollOut:
    """Fakes a constructionPoints/Axes/Planes collection: createInput() returns an Inp that
    captures EVERY setBy* call's raw args, and returns `next_result` (default True, matching the
    live API returning true on success) - a test flips it False to drive the returned-false guard
    path. add() hands back an object whose `.geometry` is `result_geometry` (default None, so
    ``_geometry_readback`` degrades to {} exactly as it does for an un-modelled fake)."""
    def __init__(self):
        self.captured = None
        self.named = None
        self.next_result = True
        self.result_geometry = None
    def createInput(self):
        self.captured = {}
        outer = self
        class Inp:
            def setByPoint(self, p):
                outer.captured["point"] = p
            def setByLine(self, line):
                outer.captured["line"] = line
            def setByEdge(self, edge):              # parametric-legal edge-axis path
                outer.captured["edge"] = edge
            def setByOffset(self, base, val):
                outer.captured["offset"] = (base, val)
            def setByAngle(self, linear, angle_val, planar):
                outer.captured["angle"] = (linear, angle_val, planar)
                return outer.next_result
            def setByThreePoints(self, p1, p2, p3):
                outer.captured["three_points"] = (p1, p2, p3)
                return outer.next_result
            def setByTwoPlanes(self, p1, p2):
                outer.captured["two_planes"] = (p1, p2)
                return outer.next_result
            def setByTangentAtPoint(self, face, pt):
                outer.captured["tangent_at_point"] = (face, pt)
                return outer.next_result
            def setByTwoEdges(self, e1, e2):
                outer.captured["two_edges"] = (e1, e2)
                return outer.next_result
            def setByCircularFace(self, face):
                outer.captured["circular_face"] = face
                return outer.next_result
            def setByTwoPoints(self, p1, p2):
                outer.captured["two_points"] = (p1, p2)
                return outer.next_result
            def setByPerpendicularAtPoint(self, face, pt):
                outer.captured["perpendicular_at_point"] = (face, pt)
                return outer.next_result
            def setByCenter(self, edge):
                outer.captured["center"] = edge
                return outer.next_result
            def setByThreePlanes(self, p1, p2, p3):
                outer.captured["three_planes"] = (p1, p2, p3)
                return outer.next_result
            def setByEdgePlane(self, edge, plane):
                outer.captured["edge_plane"] = (edge, plane)
                return outer.next_result
        self._inp = Inp()
        return self._inp
    def add(self, inp):
        obj = type("O", (), {"name": "Datum", "geometry": self.result_geometry})()
        return obj


class FakeComp:
    def __init__(self):
        self.name = "Comp"
        self.constructionPoints = _CollOut()
        self.constructionAxes = _CollOut()
        self.constructionPlanes = _CollOut()
        self.xYConstructionPlane = ("plane", "xy")
        self.xZConstructionPlane = ("plane", "xz")
        self.yZConstructionPlane = ("plane", "yz")


class FakeDesign:
    # designType: 0 = Direct (setByPoint/setByLine legal), 1 = Parametric (they fail).
    def __init__(self, comp, design_type=0):
        self.activeComponent = comp
        self.rootComponent = comp
        self.designType = design_type


def _install(raise_env=False, design_type=0):
    comp = FakeComp()
    if raise_env:
        def boom():
            raise RuntimeError("3 : Environment is not supported")
        comp.constructionPoints.createInput = boom
    design = FakeDesign(comp, design_type)
    cn.app = type("A", (), {"activeProduct": design})()
    cn._common.app = cn.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # axis (AxisRef) + plane (PlaneRef) resolve via _common — point them at the fake comp.
    cn._inputs._common.design = lambda: design
    cn._inputs._common.target_component = lambda d: comp
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: ("vec", x, y, z))
    adsk.core.InfiniteLine3D.create = staticmethod(lambda o, d: ("line", o, d))
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))
    return comp


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── fakes for the new modes' typed-kind inputs ──────────────────────────────────────────────────
#
# The new modes resolve 'edges'/'points'/'face'/'plane2'/'plane3' through GeometryHandleList /
# GeometryHandle / PlaneRef - already unit-tested in test_inputs.py. So here (matching the file's
# own established shortcut - see TestParametricConstraint.test_edge_axis_...) we monkeypatch each
# shared kind's OWN .resolve() to hand back a controlled fake entity, and pin THIS tool's logic:
# mode dispatch, per-mode count/presence guards, the exact setBy* args, and the geometry read-back.
#
# Entities are built from conftest's SHARED, live-shape-checked fakes (BRepFace/BRepEdge wrapping
# a Plane/Cylinder/Cone/Line3D/Circle3D geometry object; FakePoint/FakeVector3D for coordinates) -
# never a parallel ad-hoc shape. A 'point' entity is a bare FakePoint: this tool never reads
# .geometry off a resolved point/vertex, only passes it straight through to a setBy* call, so a
# BRepVertex-shaped wrapper would add nothing (also matches Point3D's own measured shape).

def _planar_face(normal_xyz=(0, 0, 1), origin_xyz=None):
    origin = FakePoint(*origin_xyz) if origin_xyz else None
    return BRepFace(Plane(FakeVector3D(*normal_xyz), origin))


def _cylinder_face(axis_xyz=(0, 0, 1)):
    return BRepFace(Cylinder(FakeVector3D(*axis_xyz)))


def _cone_face(axis_xyz=(0, 0, 1)):
    return BRepFace(Cone(FakeVector3D(*axis_xyz)))


def _straight_edge():
    return BRepEdge(Line3D())


def _circular_edge():
    return BRepEdge(Circle3D(FakeVector3D(0, 0, 1)))


def _stub_resolve(monkeypatch, kind, value):
    """monkeypatch.setattr(cn.<KIND>, 'resolve', lambda v: (value, None)) - auto-restored."""
    monkeypatch.setattr(kind, "resolve", lambda raw: (value, None))


class TestGuards:
    def test_unknown_units(self):
        _install()
        res = cn.handler(kind="point", units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_unknown_kind(self):
        _install()
        res = cn.handler(kind="blob")
        assert res["isError"] is True and "Unknown kind" in res["message"]

    def test_bad_axis(self):
        # AxisRef owns the error: 'q' is neither a world axis nor a resolvable edge handle
        _install()
        res = cn.handler(kind="axis", axis="q")
        assert res["isError"] is True and "not a world axis" in res["message"]

    def test_bad_plane(self):
        # PlaneRef owns the error
        _install()
        res = cn.handler(kind="plane", plane="qq")
        assert res["isError"] is True and "not an origin alias" in res["message"]

    def test_direct_modeling_env_error_is_friendly(self):
        _install(raise_env=True)
        res = cn.handler(kind="point", x=1)
        assert res["isError"] is True
        assert "DIRECT-modeling" in res["message"] and "Parametric" in res["message"]


class TestConstruction:
    def test_point_scales_coords(self):
        comp = _install()
        out = _payload(cn.handler(kind="point", x=10, y=0, z=20, units="mm"))
        assert out["kind"] == "point"
        # 10mm,20mm -> 1.0cm, 2.0cm
        assert comp.constructionPoints.captured["point"] == ("pt", 1.0, 0.0, 2.0)

    def test_axis_direction_and_origin(self):
        comp = _install()
        out = _payload(cn.handler(kind="axis", x=5, axis="x", units="mm"))
        assert out["kind"] == "axis" and out["axis"] == "x"
        tag, origin, direction = comp.constructionAxes.captured["line"]
        assert origin == ("pt", 0.5, 0.0, 0.0)      # 5mm -> 0.5cm
        assert direction == ("vec", 1, 0, 0)

    def test_plane_offset_from_named_plane(self):
        comp = _install()
        out = _payload(cn.handler(kind="plane", plane="xz", offset=15, units="mm"))
        assert out["kind"] == "plane" and out["offset_from"] == "xz"
        base, val = comp.constructionPlanes.captured["offset"]
        assert base == ("plane", "xz") and val == ("real", 1.5)   # 15mm -> 1.5cm

    def test_point_scales_inches(self):
        comp = _install()
        _payload(cn.handler(kind="point", x=1, y=2, z=0, units="in"))
        # 1in -> 2.54cm, 2in -> 5.08cm
        assert comp.constructionPoints.captured["point"] == ("pt", 2.54, 5.08, 0.0)

    def test_axis_through_field_reports_raw_coords(self):
        _install()
        out = _payload(cn.handler(kind="axis", x=5, y=6, z=7, axis="y", units="mm"))
        # 'through' echoes the RAW (un-scaled) coordinates
        assert out["through"] == {"x": 5.0, "y": 6.0, "z": 7.0}

    def test_point_at_field_reports_raw_coords(self):
        _install()
        out = _payload(cn.handler(kind="point", x=3, y=4, z=5, units="mm"))
        assert out["at"] == {"x": 3.0, "y": 4.0, "z": 5.0}

    def test_custom_name_applied(self):
        comp = _install()
        # The created object names itself "Datum"; a custom name must overwrite it.
        captured = {}
        real_add = comp.constructionPoints.add
        def add(inp):
            obj = type("O", (), {})()
            obj.name = "Datum"
            return obj
        comp.constructionPoints.add = add
        out = _payload(cn.handler(kind="point", x=1, name="CrankPin"))
        assert out["name"] == "CrankPin"

    def test_generic_exception_is_reported(self):
        comp = _install()
        def boom():
            raise RuntimeError("kaboom-unexpected")
        comp.constructionPlanes.createInput = boom
        res = cn.handler(kind="plane", plane="xy", offset=1)
        assert res["isError"] is True
        assert "kaboom-unexpected" in res["message"]


# ── the direct-edit-only constraint ─────────────────────────────────────────────────────────────
#
# setByPoint(Point3D)/setByLine(InfiniteLine3D) FAIL in parametric mode (live API docstrings). The
# old tool called them anyway then told the user to switch the WRONG way. These pin the corrected
# behaviour: in parametric, refuse coordinate point/axis with an actionable message; the EDGE-axis
# path uses parametric-legal setByEdge; an offset plane works in BOTH modes.

class TestParametricConstraint:
    def test_point_at_coord_refused_in_parametric(self):
        comp = _install(design_type=1)            # parametric
        res = cn.handler(kind="point", x=10, y=0, z=20)
        assert res["isError"] is True
        assert "DIRECT" in res["message"] and "sketch" in res["message"].lower()
        # and it must NOT have attempted the doomed setByPoint
        assert comp.constructionPoints.captured is None

    def test_world_axis_at_coord_refused_in_parametric(self):
        comp = _install(design_type=1)
        res = cn.handler(kind="axis", x=5, axis="x")
        assert res["isError"] is True and "DIRECT" in res["message"]
        assert comp.constructionAxes.captured is None

    def test_point_at_coord_works_in_direct(self):
        comp = _install(design_type=0)            # direct
        out = _payload(cn.handler(kind="point", x=10, y=0, z=20, units="mm"))
        assert out["kind"] == "point"
        assert comp.constructionPoints.captured["point"] == ("pt", 1.0, 0.0, 2.0)

    def test_edge_axis_uses_setByEdge_and_works_in_parametric(self):
        # An edge handle is parametric-legal via setByEdge. Patch AxisRef to resolve to an edge.
        comp = _install(design_type=1)            # parametric — should still succeed
        real_resolve = cn._AXIS.resolve
        cn._AXIS.resolve = lambda v: (("edge", "EDGE_HANDLE"), None)
        try:
            out = _payload(cn.handler(kind="axis", axis="<edge-handle>"))
        finally:
            cn._AXIS.resolve = real_resolve
        assert out["kind"] == "axis"
        assert comp.constructionAxes.captured["edge"] == "EDGE_HANDLE"   # setByEdge, not setByLine
        assert "line" not in comp.constructionAxes.captured

    def test_offset_plane_works_in_parametric(self):
        comp = _install(design_type=1)
        out = _payload(cn.handler(kind="plane", plane="xz", offset=15, units="mm"))
        assert out["kind"] == "plane"
        base, val = comp.constructionPlanes.captured["offset"]
        assert val == ("real", 1.5)


# ── mode-vs-kind dispatch guard ─────────────────────────────────────────────────────────────────

class TestModeKindValidation:
    def test_mode_invalid_for_kind_is_refused(self):
        _install()
        res = cn.handler(kind="axis", mode="offset")
        assert res["isError"] is True
        assert "not valid for kind='axis'" in res["message"]
        assert "circular_face" in res["message"]      # lists the LEGAL modes for axis

    def test_unknown_mode_is_refused(self):
        _install()
        res = cn.handler(kind="plane", mode="not_a_real_mode")
        assert res["isError"] is True
        assert "not valid for kind='plane'" in res["message"]

    def test_two_edges_mode_routes_to_the_right_collection_per_kind(self, monkeypatch):
        # 'two_edges' is a legal mode for BOTH plane and point - each call must hit its OWN
        # collection, never the other kind's.
        comp = _install()
        e1, e2 = _straight_edge(), _straight_edge()
        _stub_resolve(monkeypatch, cn._EDGES, [e1, e2])
        _payload(cn.handler(kind="plane", mode="two_edges"))
        assert "two_edges" in comp.constructionPlanes.captured
        assert comp.constructionPoints.captured is None
        _payload(cn.handler(kind="point", mode="two_edges"))
        assert "two_edges" in comp.constructionPoints.captured


# ── plane modes ──────────────────────────────────────────────────────────────────────────────────

class TestPlaneAtAngle:
    def test_calls_setByAngle_with_radians_and_reports_normal_changed(self, monkeypatch):
        comp = _install()
        base_plane = _planar_face((0, 0, 1))
        _stub_resolve(monkeypatch, cn._PLANE, base_plane)
        edge = _straight_edge()
        _stub_resolve(monkeypatch, cn._EDGES, [edge])
        comp.constructionPlanes.result_geometry = Plane(FakeVector3D(1, 0, 0), FakePoint(0, 0, 0))
        out = _payload(cn.handler(kind="plane", mode="at_angle", angle=45))
        assert out["kind"] == "plane" and out["mode"] == "at_angle"
        linear, angle_val, planar = comp.constructionPlanes.captured["angle"]
        assert linear is edge and planar is base_plane
        assert math.isclose(angle_val[1], math.radians(45))    # ValueInput.createByReal(radians)
        assert out["angle_deg"] == 45.0
        # the rotated plane's normal (1,0,0) differs from the base plane's (0,0,1) - proves the
        # rotation actually took, the exact sanity check the write-honesty bar asks for.
        assert out["normal_changed"] is True

    def test_needs_exactly_one_edge(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._EDGES, [])
        res = cn.handler(kind="plane", mode="at_angle", angle=10)
        assert res["isError"] is True
        assert "needs exactly 1 'edges'" in res["message"]

    def test_setByAngle_false_is_reported(self, monkeypatch):
        comp = _install()
        _stub_resolve(monkeypatch, cn._EDGES, [_straight_edge()])
        comp.constructionPlanes.next_result = False
        res = cn.handler(kind="plane", mode="at_angle", angle=10)
        assert res["isError"] is True and "setByAngle returned false" in res["message"]


class TestPlaneThreePoints:
    def test_calls_setByThreePoints(self, monkeypatch):
        comp = _install()
        v1, v2, v3 = FakePoint(), FakePoint(), FakePoint()
        _stub_resolve(monkeypatch, cn._POINTS, [v1, v2, v3])
        out = _payload(cn.handler(kind="plane", mode="three_points"))
        assert out["mode"] == "three_points" and out["point_count"] == 3
        assert comp.constructionPlanes.captured["three_points"] == (v1, v2, v3)

    def test_needs_exactly_three_points(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._POINTS, [FakePoint(), FakePoint()])
        res = cn.handler(kind="plane", mode="three_points")
        assert res["isError"] is True
        assert "needs exactly 3 'points'" in res["message"]

    def test_setByThreePoints_false_is_reported(self, monkeypatch):
        comp = _install()
        _stub_resolve(monkeypatch, cn._POINTS, [FakePoint(), FakePoint(), FakePoint()])
        comp.constructionPlanes.next_result = False
        res = cn.handler(kind="plane", mode="three_points")
        assert res["isError"] is True and "setByThreePoints returned false" in res["message"]


class TestPlaneMidplane:
    def test_calls_setByTwoPlanes(self, monkeypatch):
        comp = _install()
        p2 = _planar_face()
        _stub_resolve(monkeypatch, cn._PLANE2, p2)
        out = _payload(cn.handler(kind="plane", mode="midplane", plane="xz"))
        assert out["mode"] == "midplane"
        p1, p2_captured = comp.constructionPlanes.captured["two_planes"]
        assert p1 == ("plane", "xz") and p2_captured is p2

    def test_needs_plane2(self):
        _install()
        res = cn.handler(kind="plane", mode="midplane")     # plane2 omitted -> resolves to None
        assert res["isError"] is True and "needs 'plane2'" in res["message"]


class TestPlaneTangentAtPoint:
    def test_calls_setByTangentAtPoint(self, monkeypatch):
        comp = _install()
        face = _cylinder_face()
        _stub_resolve(monkeypatch, cn._FACE, face)
        v = FakePoint()
        _stub_resolve(monkeypatch, cn._POINTS, [v])
        out = _payload(cn.handler(kind="plane", mode="tangent_at_point"))
        assert out["mode"] == "tangent_at_point"
        f, pt = comp.constructionPlanes.captured["tangent_at_point"]
        assert f is face and pt is v

    def test_rejects_planar_face(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._FACE, _planar_face())
        _stub_resolve(monkeypatch, cn._POINTS, [FakePoint()])
        res = cn.handler(kind="plane", mode="tangent_at_point")
        assert res["isError"] is True
        assert "CYLINDRICAL or CONICAL" in res["message"] and "planar" in res["message"]

    def test_needs_face(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._POINTS, [FakePoint()])
        res = cn.handler(kind="plane", mode="tangent_at_point")
        assert res["isError"] is True and "needs 'face'" in res["message"]


class TestPlaneTwoEdges:
    def test_calls_setByTwoEdges(self, monkeypatch):
        comp = _install()
        e1, e2 = _straight_edge(), _straight_edge()
        _stub_resolve(monkeypatch, cn._EDGES, [e1, e2])
        out = _payload(cn.handler(kind="plane", mode="two_edges"))
        assert out["mode"] == "two_edges"
        assert comp.constructionPlanes.captured["two_edges"] == (e1, e2)

    def test_needs_exactly_two_edges(self, monkeypatch):
        # the "too many" side of the exact-count guard (0/1 cases are covered elsewhere).
        _install()
        _stub_resolve(monkeypatch, cn._EDGES, [_straight_edge()] * 3)
        res = cn.handler(kind="plane", mode="two_edges")
        assert res["isError"] is True and "needs exactly 2 'edges'" in res["message"]


# ── axis modes ───────────────────────────────────────────────────────────────────────────────────

class TestAxisCircularFace:
    def test_calls_setByCircularFace_and_reports_alignment(self, monkeypatch):
        comp = _install()
        face = _cylinder_face((0, 0, 1))
        _stub_resolve(monkeypatch, cn._FACE, face)
        comp.constructionAxes.result_geometry = type(
            "G", (), {"direction": FakeVector3D(0, 0, 1), "origin": FakePoint(0, 0, 0)})()
        out = _payload(cn.handler(kind="axis", mode="circular_face"))
        assert out["mode"] == "circular_face"
        assert comp.constructionAxes.captured["circular_face"] is face
        assert out["aligned_to_face_axis"] is True

    def test_rejects_planar_face(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._FACE, _planar_face())
        res = cn.handler(kind="axis", mode="circular_face")
        assert res["isError"] is True and "CYLINDRICAL or CONICAL" in res["message"]

    def test_accepts_conical_face(self, monkeypatch):
        comp = _install()
        _stub_resolve(monkeypatch, cn._FACE, _cone_face())
        out = _payload(cn.handler(kind="axis", mode="circular_face"))
        assert out["mode"] == "circular_face"
        assert "circular_face" in comp.constructionAxes.captured

    def test_needs_face(self):
        _install()
        res = cn.handler(kind="axis", mode="circular_face")
        assert res["isError"] is True and "needs 'face'" in res["message"]


class TestAxisTwoPoints:
    def test_calls_setByTwoPoints(self, monkeypatch):
        comp = _install()
        v1, v2 = FakePoint(), FakePoint()
        _stub_resolve(monkeypatch, cn._POINTS, [v1, v2])
        out = _payload(cn.handler(kind="axis", mode="two_points"))
        assert out["mode"] == "two_points"
        assert comp.constructionAxes.captured["two_points"] == (v1, v2)

    def test_needs_exactly_two_points(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._POINTS, [FakePoint()])
        res = cn.handler(kind="axis", mode="two_points")
        assert res["isError"] is True and "needs exactly 2 'points'" in res["message"]

    def test_setByTwoPoints_false_is_reported(self, monkeypatch):
        comp = _install()
        _stub_resolve(monkeypatch, cn._POINTS, [FakePoint(), FakePoint()])
        comp.constructionAxes.next_result = False
        res = cn.handler(kind="axis", mode="two_points")
        assert res["isError"] is True and "setByTwoPoints returned false" in res["message"]


class TestAxisTwoPlanes:
    def test_calls_setByTwoPlanes(self, monkeypatch):
        comp = _install()
        p2 = _planar_face()
        _stub_resolve(monkeypatch, cn._PLANE2, p2)
        out = _payload(cn.handler(kind="axis", mode="two_planes", plane="yz"))
        assert out["mode"] == "two_planes"
        p1, p2_captured = comp.constructionAxes.captured["two_planes"]
        assert p1 == ("plane", "yz") and p2_captured is p2

    def test_needs_plane2(self):
        _install()
        res = cn.handler(kind="axis", mode="two_planes")
        assert res["isError"] is True and "needs 'plane2'" in res["message"]


class TestAxisPerpendicularAtPoint:
    def test_calls_setByPerpendicularAtPoint_and_reports_alignment(self, monkeypatch):
        comp = _install()
        face = _planar_face((0, 0, 1))
        _stub_resolve(monkeypatch, cn._FACE, face)
        v = FakePoint()
        _stub_resolve(monkeypatch, cn._POINTS, [v])
        comp.constructionAxes.result_geometry = type(
            "G", (), {"direction": FakeVector3D(0, 0, 1), "origin": FakePoint(0, 0, 0)})()
        out = _payload(cn.handler(kind="axis", mode="perpendicular_at_point"))
        assert out["mode"] == "perpendicular_at_point"
        f, pt = comp.constructionAxes.captured["perpendicular_at_point"]
        assert f is face and pt is v
        assert out["aligned_to_face_normal"] is True

    def test_needs_exactly_one_point(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._FACE, _planar_face())
        _stub_resolve(monkeypatch, cn._POINTS, [])
        res = cn.handler(kind="axis", mode="perpendicular_at_point")
        assert res["isError"] is True and "needs exactly 1 'points'" in res["message"]


# ── point modes ──────────────────────────────────────────────────────────────────────────────────

class TestPointCircleCenter:
    def test_calls_setByCenter(self, monkeypatch):
        comp = _install()
        edge = _circular_edge()
        _stub_resolve(monkeypatch, cn._EDGES, [edge])
        out = _payload(cn.handler(kind="point", mode="circle_center"))
        assert out["mode"] == "circle_center"
        assert comp.constructionPoints.captured["center"] is edge

    def test_rejects_straight_edge(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._EDGES, [_straight_edge()])
        res = cn.handler(kind="point", mode="circle_center")
        assert res["isError"] is True
        assert "CIRCULAR" in res["message"] and "straight" in res["message"]


class TestPointTwoEdges:
    def test_calls_setByTwoEdges(self, monkeypatch):
        comp = _install()
        e1, e2 = _straight_edge(), _straight_edge()
        _stub_resolve(monkeypatch, cn._EDGES, [e1, e2])
        out = _payload(cn.handler(kind="point", mode="two_edges"))
        assert out["mode"] == "two_edges"
        assert comp.constructionPoints.captured["two_edges"] == (e1, e2)

    def test_needs_exactly_two_edges(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._EDGES, [])
        res = cn.handler(kind="point", mode="two_edges")
        assert res["isError"] is True and "needs exactly 2 'edges'" in res["message"]


class TestPointThreePlanes:
    def test_calls_setByThreePlanes(self, monkeypatch):
        comp = _install()
        p2, p3 = _planar_face(), _planar_face()
        _stub_resolve(monkeypatch, cn._PLANE2, p2)
        _stub_resolve(monkeypatch, cn._PLANE3, p3)
        out = _payload(cn.handler(kind="point", mode="three_planes", plane="xz"))
        assert out["mode"] == "three_planes"
        p1, p2_captured, p3_captured = comp.constructionPoints.captured["three_planes"]
        assert p1 == ("plane", "xz") and p2_captured is p2 and p3_captured is p3

    def test_needs_plane3(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._PLANE2, _planar_face())
        res = cn.handler(kind="point", mode="three_planes")
        assert res["isError"] is True and "needs 'plane3'" in res["message"]


class TestPointEdgePlane:
    def test_calls_setByEdgePlane(self, monkeypatch):
        comp = _install()
        edge = _straight_edge()
        _stub_resolve(monkeypatch, cn._EDGES, [edge])
        out = _payload(cn.handler(kind="point", mode="edge_plane", plane="xz"))
        assert out["mode"] == "edge_plane"
        e, p = comp.constructionPoints.captured["edge_plane"]
        assert e is edge and p == ("plane", "xz")

    def test_needs_exactly_one_edge(self, monkeypatch):
        _install()
        _stub_resolve(monkeypatch, cn._EDGES, [])
        res = cn.handler(kind="point", mode="edge_plane")
        assert res["isError"] is True and "needs exactly 1 'edges'" in res["message"]


# ── the geometry sanity read-back (universal across modes) ─────────────────────────────────────

class TestGeometryReadback:
    def test_reports_geometry_when_available(self):
        comp = _install()
        comp.constructionPlanes.result_geometry = Plane(FakeVector3D(0, 0, 1), FakePoint(1, 2, 3))
        out = _payload(cn.handler(kind="plane", plane="xy", offset=5))
        assert out["geometry"]["normal"] == [0.0, 0.0, 1.0]
        # origin is internally cm; mm display units -> *10.
        assert out["geometry"]["origin"] == {"x": 10.0, "y": 20.0, "z": 30.0}

    def test_missing_geometry_degrades_to_empty_dict(self):
        _install()
        out = _payload(cn.handler(kind="point", x=1, y=2, z=3))
        assert out["geometry"] == {}


# ── offset-plane parameter EXPRESSIONS + the model-parameter (dNN) read-back ────────────────────
#
# mode=offset accepts a parameter EXPRESSION string ('StockZ/2', '25 mm') routed through
# ValueInput.createByString (ties the plane's offset to a live parameter), validated via the units
# engine so an unresolvable one is refused BY NAME. And an offset plane NAMES the model parameter
# (dNN) it created so the plane is retargetable via param_set - the same shape as the landed
# model_extrude distance fix.

def _with_units_mgr(mgr=None):
    """Attach the shared fake units engine (conftest.FakeUnitsManager) to the installed design so
    string expressions can evaluate."""
    cn.app.activeProduct.unitsManager = mgr or FakeUnitsManager()


def _plane_with_offset_param(comp, dname="d5"):
    """Make constructionPlanes.add() return a plane exposing the offset ModelParameter read-back
    path (obj.definition.offset.name = dname), so _offset_parameter surfaces the dNN."""
    param = type("MP", (), {"name": dname})()
    defn = type("Def", (), {"offset": param})()
    comp.constructionPlanes.add = lambda inp: type(
        "O", (), {"name": "Datum", "geometry": None, "definition": defn})()


class TestOffsetExpression:
    def test_string_expression_uses_createByString_not_scaled_real(self):
        comp = _install()
        _with_units_mgr()
        out = _payload(cn.handler(kind="plane", plane="xy", offset="25 mm"))
        base, val = comp.constructionPlanes.captured["offset"]
        assert val == ("str", "25 mm")        # createByString - NOT a scaled createByReal
        assert out["offset"] == "25 mm"        # echoed as the expression, not a rounded number

    def test_expression_references_a_parameter(self):
        comp = _install()
        _with_units_mgr()
        _payload(cn.handler(kind="plane", plane="xz", offset="StockZ/2"))
        _base, val = comp.constructionPlanes.captured["offset"]
        assert val == ("str", "StockZ/2")

    def test_numeric_string_is_a_literal_scaled_via_createByReal(self):
        # a PLAIN numeric string is a literal, not an expression - it still scales through createByReal.
        comp = _install()
        out = _payload(cn.handler(kind="plane", plane="xy", offset="15", units="mm"))
        _base, val = comp.constructionPlanes.captured["offset"]
        assert val == ("real", 1.5)           # "15" mm -> 1.5 cm, the literal path
        assert out["offset"] == 15.0

    def test_unresolvable_expression_refused_by_name(self):
        _install()
        _with_units_mgr()                      # only the known set evaluates; this one does not
        res = cn.handler(kind="plane", plane="xy", offset="NoSuchParam * 2")
        assert res["isError"] is True
        assert "NoSuchParam * 2" in res["message"]


class TestOffsetModelParameter:
    def test_names_the_offset_model_parameter(self):
        comp = _install()
        _plane_with_offset_param(comp, "d7")
        out = _payload(cn.handler(kind="plane", plane="xy", offset=5))
        assert out["model_parameters"]["offset"] == "d7"

    def test_note_advertises_the_model_parameter(self):
        comp = _install()
        _plane_with_offset_param(comp)
        out = _payload(cn.handler(kind="plane", plane="xy", offset=5))
        assert "model_parameters" in out["note"] and "param_set" in out["note"]

    def test_absent_when_no_offset_parameter_exists(self):
        # the default fake plane exposes no .definition -> the key is simply omitted, never a crash.
        _install()
        out = _payload(cn.handler(kind="plane", plane="xy", offset=5))
        assert "model_parameters" not in out
