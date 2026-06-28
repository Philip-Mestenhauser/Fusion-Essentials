"""Unit tests for ``joint_at_geometry.py`` — joint two parts at geometry handles.

The VALUE of this tool is the baked-in runtime rules, so that's what's pinned: `_joint_geometry_for`
must pick a VALID keypoint by entity kind — a cylinder/cone face uses MiddleKeyPoint (CenterKeyPoint
is invalid there, the rule that cost a live crash), a planar face uses CenterKeyPoint, a circular
edge uses center, a vertex uses createByPoint. Plus the motion mapping and the handle-resolution
guards. The geometry construction is captured on fakes so we assert which JointGeometry factory +
keypoint were used, without a live design.
"""

import json

from conftest import load_tool

jg = load_tool("joint_at_geometry")


# ── fakes for the JointGeometry factory + keypoint enum ─────────────────────

class _Recorder:
    """Records which JointGeometry factory was called with which keypoint."""
    def __init__(self):
        self.calls = []
    def createByNonPlanarFace(self, face, kp):
        self.calls.append(("nonplanar", kp)); return ("geo", "nonplanar", kp)
    def createByPlanarFace(self, face, edge, kp):
        self.calls.append(("planar", kp)); return ("geo", "planar", kp)
    def createByCurve(self, edge, kp):
        self.calls.append(("curve", kp)); return ("geo", "curve", kp)
    def createByPoint(self, pt):
        self.calls.append(("point", None)); return ("geo", "point", None)


# entity-kind fakes — must pass the isinstance() checks in the handler, so we monkeypatch the
# adsk.fusion class symbols the handler tests against to these fakes.
class FakeBRepFace:
    def __init__(self, surface_type):
        self.geometry = type("G", (), {"surfaceType": surface_type})()


class FakeBRepEdge:
    def __init__(self, curve_type):
        self.geometry = type("G", (), {"curveType": curve_type})()


class FakeBRepVertex:
    geometry = None


def _install():
    import adsk.fusion, adsk.core
    rec = _Recorder()
    # JointGeometry factory -> our recorder
    adsk.fusion.JointGeometry = rec
    # keypoint + direction enums as sentinels
    kp = adsk.fusion.JointKeyPointTypes
    kp.CenterKeyPoint = "CENTER"; kp.MiddleKeyPoint = "MIDDLE"; kp.StartKeyPoint = "START"
    st = adsk.core.SurfaceTypes
    st.PlaneSurfaceType = "PLANE"; st.CylinderSurfaceType = "CYL"; st.ConeSurfaceType = "CONE"
    st.SphereSurfaceType = "SPHERE"; st.TorusSurfaceType = "TORUS"
    ct = adsk.core.Curve3DTypes
    ct.Circle3DCurveType = "CIRCLE"; ct.Line3DCurveType = "LINE"; ct.Arc3DCurveType = "ARC"
    jd = adsk.fusion.JointDirections
    jd.XAxisJointDirection = "XD"; jd.YAxisJointDirection = "YD"; jd.ZAxisJointDirection = "ZD"
    jd.CustomJointDirection = "CUSTOM"
    # make the handler's isinstance checks use our fakes
    adsk.fusion.BRepFace = FakeBRepFace
    adsk.fusion.BRepEdge = FakeBRepEdge
    adsk.fusion.BRepVertex = FakeBRepVertex
    adsk.fusion.ConstructionPoint = type("CP", (), {})
    adsk.fusion.SketchPoint = type("SP", (), {})
    return rec


# ── the runtime-rule logic (the whole point of the tool) ────────────────────

class TestJointGeometryRules:
    def test_cylinder_face_uses_MIDDLE_not_center(self):
        # THE rule that cost a live crash: CenterKeyPoint is invalid on a cylinder face.
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepFace("CYL"))
        assert err is None
        assert g[1] == "nonplanar" and g[2] == "MIDDLE"     # createByNonPlanarFace + MiddleKeyPoint
        assert "cylinder" in label

    def test_cone_face_also_uses_middle(self):
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepFace("CONE"))
        assert err is None and g[2] == "MIDDLE"

    def test_planar_face_uses_CENTER(self):
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepFace("PLANE"))
        assert err is None
        assert g[1] == "planar" and g[2] == "CENTER"

    def test_circular_edge_uses_center(self):
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepEdge("CIRCLE"))
        assert err is None and g[1] == "curve" and g[2] == "CENTER"

    def test_line_edge_uses_middle(self):
        _install()
        g, _, err = jg._joint_geometry_for(FakeBRepEdge("LINE"))
        assert err is None and g[2] == "MIDDLE"

    def test_vertex_uses_point(self):
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepVertex())
        assert err is None and g[1] == "point"


# ── handler guards + wiring ─────────────────────────────────────────────────

class _FakeJointInput:
    def __init__(self):
        self.motion = None
    # *args so we capture the optional custom-axis-entity 2nd arg
    def setAsRigidJointMotion(self):
        self.motion = ("rigid",); return True
    def setAsRevoluteJointMotion(self, *args):
        self.motion = ("revolute",) + args; return True
    def setAsSliderJointMotion(self, *args):
        self.motion = ("slider",) + args; return True
    def setAsCylindricalJointMotion(self, *args):
        self.motion = ("cyl",) + args; return True
    def setAsBallJointMotion(self, a, b):
        self.motion = ("ball",); return True


class _FakeJoints:
    def __init__(self, health_state=0, message=""):
        self.last_input = None
        self._hs = health_state
        self._msg = message
    def createInput(self, g1, g2):
        self.last_input = _FakeJointInput(); return self.last_input
    def add(self, ji):
        return type("J", (), {"name": "Joint1", "healthState": self._hs,
                              "errorOrWarningMessage": self._msg,
                              "occurrenceOne": type("O", (), {"name": "Rod:1"})(),
                              "occurrenceTwo": type("O", (), {"name": "Crank:1"})()})()


def _install_design(token_map, joint_health=0, joint_msg=""):
    rec = _install()
    joints = _FakeJoints(joint_health, joint_msg)
    root = type("R", (), {"joints": joints})()
    class FakeDesign:
        rootComponent = root
        def findEntityByToken(self, h):
            e = token_map.get(h)
            return [e] if e is not None else []
    d = FakeDesign()
    jg.app = type("A", (), {"activeProduct": d})()
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return joints


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestHandler:
    def test_unknown_motion(self):
        _install_design({})
        res = jg.handler(handle_one="a", handle_two="b", motion="weld")
        assert res["isError"] is True and "Unknown motion" in res["message"]

    def test_unresolved_handle(self):
        _install_design({"a": FakeBRepFace("CYL")})   # 'b' not in map
        res = jg.handler(handle_one="a", handle_two="b")
        assert res["isError"] is True and "handle_two did not resolve" in res["message"]

    def test_revolute_forced_world_axis(self):
        joints = _install_design({"rod": FakeBRepFace("CYL"), "pin": FakeBRepFace("CYL")})
        out = _payload(jg.handler(handle_one="rod", handle_two="pin", motion="revolute", axis="x"))
        assert out["jointed"] is True
        assert out["occurrence_one"] == "Rod:1" and out["occurrence_two"] == "Crank:1"
        # axis='x' forces the world X direction (no custom-axis entity)
        assert joints.last_input.motion == ("revolute", "XD")

    def test_revolute_auto_axis_uses_geometry_axis(self):
        # THE FIX: axis='auto' (default) on cylinder faces derives the axis FROM the geometry
        # (CustomJointDirection + the cylinder face as the axis entity), not a world axis.
        pin = FakeBRepFace("CYL")
        joints = _install_design({"rod": FakeBRepFace("CYL"), "pin": pin})
        out = _payload(jg.handler(handle_one="rod", handle_two="pin", motion="revolute"))
        m = joints.last_input.motion
        assert m[0] == "revolute" and m[1] == "CUSTOM"      # CustomJointDirection used
        assert m[2] is not None                              # an axis entity was passed
        assert out["axis"] == "auto(geometry)"

    def test_slider_auto_axis_from_geometry(self):
        joints = _install_design({"pis": FakeBRepFace("CYL"), "bore": FakeBRepFace("CYL")})
        _payload(jg.handler(handle_one="pis", handle_two="bore", motion="slider"))
        m = joints.last_input.motion
        assert m[0] == "slider" and m[1] == "CUSTOM"

    def test_reports_health_warning_when_joint_fails_to_compute(self):
        # the bug we lived: a joint ADDS fine but healthState=1 (over-constrained / Compute Failed).
        _install_design({"a": FakeBRepFace("CYL"), "b": FakeBRepFace("CYL")},
                        joint_health=1, joint_msg="Can't resolve positions.Compute FailedX")
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert out["healthy"] is False
        assert "FAILED TO COMPUTE" in out["health_warning"]
        assert "Compute Failed" not in out["health_warning"]   # message trimmed

    def test_healthy_joint_no_warning(self):
        _install_design({"a": FakeBRepFace("CYL"), "b": FakeBRepFace("CYL")})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert out["healthy"] is True and "health_warning" not in out
