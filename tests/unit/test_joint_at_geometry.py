"""Unit tests for ``joint_at_geometry.py`` — joint two parts at geometry handles.

The VALUE of this tool is the baked-in runtime rules, so that's what's pinned: `_joint_geometry_for`
must pick a VALID keypoint by entity kind — a cylinder/cone face uses MiddleKeyPoint (CenterKeyPoint
is invalid on a cylinder/cone face), a planar face uses CenterKeyPoint, a circular edge uses center,
a vertex uses createByPoint. Plus the motion mapping and the handle-resolution guards. The geometry
construction is captured on fakes so we assert which JointGeometry factory + keypoint were used,
without a live design.
"""

import json

import adsk.core
import adsk.fusion

from conftest import load_tool

jg = load_tool("joint_at_geometry")

# Measured enum shorthands (seeded from live_api_facts) - the fakes and assertions speak these.
_ST = adsk.core.SurfaceTypes
_CT = adsk.core.Curve3DTypes
_KP = adsk.fusion.JointKeyPointTypes
_JD = adsk.fusion.JointDirections


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


class _MovingOcc:
    """An occurrence whose transform.translation tracks a mutable origin (cm) - lets a test move it
    across joint creation and assert the reported moved_by delta."""
    def __init__(self, name, pos):
        self.name = name
        self._pos = list(pos)
    def move_to(self, pos):
        self._pos = list(pos)
    @property
    def transform(self):
        pos = self._pos
        vec = type("V", (), {"x": pos[0], "y": pos[1], "z": pos[2]})()
        return type("M", (), {"translation": vec})()


def _install():
    rec = _Recorder()
    # JointGeometry factory -> our recorder
    adsk.fusion.JointGeometry = rec
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
        # The key rule: CenterKeyPoint is invalid on a cylinder face — use MiddleKeyPoint.
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.CylinderSurfaceType))
        assert err is None
        assert g[1] == "nonplanar" and g[2] == _KP.MiddleKeyPoint     # createByNonPlanarFace + MiddleKeyPoint
        assert "cylinder" in label

    def test_cone_face_also_uses_middle(self):
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.ConeSurfaceType))
        assert err is None and g[2] == _KP.MiddleKeyPoint

    def test_planar_face_uses_CENTER(self):
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.PlaneSurfaceType))
        assert err is None
        assert g[1] == "planar" and g[2] == _KP.CenterKeyPoint

    def test_circular_edge_uses_center(self):
        _install()
        g, label, err = jg._joint_geometry_for(FakeBRepEdge(_CT.Circle3DCurveType))
        assert err is None and g[1] == "curve" and g[2] == _KP.CenterKeyPoint

    def test_line_edge_uses_middle(self):
        _install()
        g, _, err = jg._joint_geometry_for(FakeBRepEdge(_CT.Line3DCurveType))
        assert err is None and g[2] == _KP.MiddleKeyPoint

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
    jg._common.app = jg.app
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
        _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType)})   # 'b' not in map
        res = jg.handler(handle_one="a", handle_two="b")
        # The typed GeometryHandle kind names the offending input and flags possible staleness.
        assert res["isError"] is True
        assert "handle_two" in res["message"] and "did not resolve" in res["message"]

    def test_revolute_forced_world_axis(self):
        joints = _install_design({"rod": FakeBRepFace(_ST.CylinderSurfaceType), "pin": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="rod", handle_two="pin", motion="revolute", axis="x"))
        assert out["jointed"] is True
        assert out["occurrence_one"] == "Rod:1" and out["occurrence_two"] == "Crank:1"
        # axis='x' forces the world X direction (no custom-axis entity)
        assert joints.last_input.motion == ("revolute", _JD.XAxisJointDirection)

    def test_revolute_auto_axis_uses_geometry_axis(self):
        # axis='auto' (default) on cylinder faces derives the axis FROM the geometry
        # (CustomJointDirection + the cylinder face as the axis entity), not a world axis.
        pin = FakeBRepFace(_ST.CylinderSurfaceType)
        joints = _install_design({"rod": FakeBRepFace(_ST.CylinderSurfaceType), "pin": pin})
        out = _payload(jg.handler(handle_one="rod", handle_two="pin", motion="revolute"))
        m = joints.last_input.motion
        assert m[0] == "revolute" and m[1] == _JD.CustomJointDirection      # CustomJointDirection used
        assert m[2] is not None                              # an axis entity was passed
        assert out["axis"] == "auto(geometry)"

    def test_slider_auto_axis_from_geometry(self):
        joints = _install_design({"pis": FakeBRepFace(_ST.CylinderSurfaceType), "bore": FakeBRepFace(_ST.CylinderSurfaceType)})
        _payload(jg.handler(handle_one="pis", handle_two="bore", motion="slider"))
        m = joints.last_input.motion
        assert m[0] == "slider" and m[1] == _JD.CustomJointDirection

    def test_reports_health_warning_when_joint_fails_to_compute(self):
        # a joint can ADD fine yet report healthState=1 (over-constrained / Compute Failed) - the
        # handler must surface that as a health warning, not a false success.
        _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)},
                        joint_health=1, joint_msg="Can't resolve positions.Compute FailedX")
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert out["healthy"] is False
        assert "FAILED TO COMPUTE" in out["health_warning"]
        assert "Compute Failed" not in out["health_warning"]   # message trimmed

    def test_healthy_joint_no_warning(self):
        _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert out["healthy"] is True and "health_warning" not in out

    def test_rigid_motion_has_null_axis(self):
        joints = _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert joints.last_input.motion == ("rigid",)
        assert out["axis"] is None                       # rigid has no motion axis to report

    def test_ball_motion_uses_two_world_directions(self):
        joints = _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="ball"))
        assert joints.last_input.motion == ("ball",)     # setAsBallJointMotion(Z, X)
        assert out["jointed"] is True

    def test_slider_forced_world_axis(self):
        # axis='z' on cylinder faces still FORCES the world Z direction (no CustomJointDirection).
        joints = _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        _payload(jg.handler(handle_one="a", handle_two="b", motion="slider", axis="z"))
        assert joints.last_input.motion == ("slider", _JD.ZAxisJointDirection)

    def test_cylindrical_forced_world_axis(self):
        joints = _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        _payload(jg.handler(handle_one="a", handle_two="b", motion="cylindrical", axis="y"))
        assert joints.last_input.motion == ("cyl", _JD.YAxisJointDirection)

    def test_auto_axis_with_no_geometry_axis_falls_back_to_world_z(self):
        # PLANAR faces give _axis_entity nothing -> 'auto' can't derive an axis; the motion uses the
        # default world Z direction and the reported axis is plain 'auto', NOT 'auto(geometry)'.
        joints = _install_design({"a": FakeBRepFace(_ST.PlaneSurfaceType), "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert joints.last_input.motion == ("revolute", _JD.ZAxisJointDirection)   # world Z, not CUSTOM
        assert out["axis"] == "auto"

    def test_unknown_axis_keyword_errors(self):
        # An unrecognized axis string (not x/y/z, not auto) must be REFUSED, naming the offending
        # value - not silently coerced to the world Z direction.
        _install_design({"a": FakeBRepFace(_ST.PlaneSurfaceType), "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        res = jg.handler(handle_one="a", handle_two="b", motion="revolute", axis="diagonal")
        assert res["isError"] is True
        assert "diagonal" in res["message"]

    def test_circular_edge_is_an_axis_entity_for_auto(self):
        # a circular edge can define the motion axis (auto -> CustomJointDirection + the edge).
        joints = _install_design({"a": FakeBRepEdge(_CT.Circle3DCurveType), "b": FakeBRepEdge(_CT.Circle3DCurveType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        m = joints.last_input.motion
        assert m[0] == "revolute" and m[1] == _JD.CustomJointDirection and m[2] is not None
        assert out["axis"] == "auto(geometry)"

    def test_reports_moved_by_when_part_repositioned(self):
        # joint_at aligns the picked keypoints, repositioning handle_one's occurrence; a real move
        # must surface as moved_by + move_warning, not silently (the item-6 teleport defect).
        moving = _MovingOcc("Rod:1", (10.0, 0.0, 0.0))    # cm - the moving occurrence's origin
        face_a = FakeBRepFace(_ST.PlaneSurfaceType); face_a.assemblyContext = moving
        face_b = FakeBRepFace(_ST.PlaneSurfaceType)
        joints = _install_design({"a": face_a, "b": face_b})
        orig_add = joints.add
        def moving_add(ji):                                # add() repositions the occurrence
            j = orig_add(ji); moving.move_to((2.5, 0.5, 0.0)); return j
        joints.add = moving_add
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "moved_by" in out
        # delta (-7.5, 0.5, 0) cm -> 75.17 mm; direction points -X
        assert 75.0 < out["moved_by"]["distance_mm"] < 75.3
        assert out["moved_by"]["direction"][0] < 0
        assert "move_warning" in out and "keypoints" in out["move_warning"].lower()

    def test_reports_moved_by_when_the_fixed_side_moves(self):
        # grounding / an existing joint can make the solver move occurrence_TWO instead of one - both
        # sides are watched, and the mover is named (a live case moved handle_two, not handle_one).
        moving = _MovingOcc("Crank:1", (0.0, 0.0, 0.0))
        face_a = FakeBRepFace(_ST.PlaneSurfaceType)                        # handle_one stays put
        face_b = FakeBRepFace(_ST.PlaneSurfaceType); face_b.assemblyContext = moving
        joints = _install_design({"a": face_a, "b": face_b})
        orig_add = joints.add
        def moving_add(ji):
            j = orig_add(ji); moving.move_to((3.0, 0.0, 0.0)); return j    # 3 cm = 30 mm
        joints.add = moving_add
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "moved_by" in out and out["moved_by"]["distance_mm"] == 30.0
        assert "Crank:1" in out["move_warning"]

    def test_no_moved_by_when_part_stays_put(self):
        # a well-matched pair whose keypoints already coincide does not move -> no moved_by / warning.
        still = _MovingOcc("Rod:1", (4.0, 1.0, 0.0))
        face_a = FakeBRepFace(_ST.PlaneSurfaceType); face_a.assemblyContext = still
        face_b = FakeBRepFace(_ST.PlaneSurfaceType)
        _install_design({"a": face_a, "b": face_b})       # add() leaves the position unchanged
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "moved_by" not in out and "move_warning" not in out

    def test_motion_setter_failure_reports_error(self):
        # if the motion setter raises (e.g. incompatible geometry), the handler returns an error
        # naming the motion + the axis hint, NOT a false success.
        joints = _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})

        def boom(*a, **k):
            raise RuntimeError("geometry rejected")
        # patch createInput to return a JointInput whose revolute setter raises
        orig = joints.createInput
        def make(g1, g2):
            ji = orig(g1, g2)
            ji.setAsRevoluteJointMotion = boom
            return ji
        joints.createInput = make
        res = jg.handler(handle_one="a", handle_two="b", motion="revolute", axis="x")
        assert res["isError"] is True
        assert "Could not set revolute motion" in res["message"]

    def test_flip_sets_isFlipped_on_the_joint_input(self):
        # flip=true must reach the JointInput BEFORE add() - a dropped flag silently recreates the
        # 180-deg flush-mate rotation the input exists to prevent.
        joints = _install_design({"a": FakeBRepFace(_ST.PlaneSurfaceType),
                                  "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid", flip=True))
        assert joints.last_input.isFlipped is True
        assert out["flipped"] is True

    def test_no_flip_leaves_joint_input_unflipped(self):
        joints = _install_design({"a": FakeBRepFace(_ST.PlaneSurfaceType),
                                  "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert not getattr(joints.last_input, "isFlipped", False)
        assert out["flipped"] is False


class TestFlipHint:
    """Two planar faces whose OUTWARD normals oppose (the flush face-to-face pick) rotate the free
    part 180 deg unless flip is passed - the payload must flag exactly that case."""

    def _faces(self, monkeypatch, n1, n2):
        # route the normal sample through the face's own stub value, via monkeypatch so the real
        # shared _geom module is restored (an imperative poke here leaks into test__geom /
        # test_find_geometry); pointOnFace present so the real _planar_outward_normal path
        # (isinstance + surfaceType + sample) runs.
        monkeypatch.setattr(jg._geom, "evaluator_normal_at",
                            lambda face, point, decimals=6: getattr(face, "unit_normal", None))
        fa = FakeBRepFace(_ST.PlaneSurfaceType); fa.unit_normal = n1; fa.pointOnFace = object()
        fb = FakeBRepFace(_ST.PlaneSurfaceType); fb.unit_normal = n2; fb.pointOnFace = object()
        return fa, fb

    def test_opposing_normals_without_flip_flag_the_hint(self, monkeypatch):
        fa, fb = self._faces(monkeypatch, [0.0, 0.0, -1.0], [0.0, 0.0, 1.0])
        _install_design({"a": fa, "b": fb})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "flip_hint" in out and "180" in out["flip_hint"] and "flip=true" in out["flip_hint"]

    def test_opposing_normals_with_flip_no_hint(self, monkeypatch):
        fa, fb = self._faces(monkeypatch, [0.0, 0.0, -1.0], [0.0, 0.0, 1.0])
        _install_design({"a": fa, "b": fb})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid", flip=True))
        assert "flip_hint" not in out

    def test_agreeing_normals_no_hint(self, monkeypatch):
        fa, fb = self._faces(monkeypatch, [0.0, 0.0, 1.0], [0.0, 0.0, 1.0])
        _install_design({"a": fa, "b": fb})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "flip_hint" not in out


class TestModelParameters:
    def test_payload_names_the_joints_own_dnn_params(self):
        # the created joint's offset/angle ModelParameter names must reach the payload (with the
        # shared offset-is-frame-Z teaching) so an agent can param_set the right dNN.
        joints = _install_design({"a": FakeBRepFace(_ST.CylinderSurfaceType),
                                  "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        orig_add = joints.add
        def add_with_params(ji):
            j = orig_add(ji)
            j.offset = type("P", (), {"name": "d8"})()
            j.angle = type("P", (), {"name": "d5"})()
            return j
        joints.add = add_with_params
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert out["model_parameters"] == {"offset": "d8", "angle": "d5"}
        assert "FRAME'S Z" in out["note"]

    def test_motion_param_names_omits_absent_params(self):
        j = type("J", (), {"offset": None, "angle": None})()
        assert jg.motion_param_names(j) == {}

    def test_motion_param_names_reads_both(self):
        j = type("J", (), {"offset": type("P", (), {"name": "d12"})(),
                           "angle": type("P", (), {"name": "d11"})()})()
        assert jg.motion_param_names(j) == {"offset": "d12", "angle": "d11"}
