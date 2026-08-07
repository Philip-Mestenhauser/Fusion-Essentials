"""Unit tests for ``joint_at_geometry.py`` — joint two parts at geometry handles.

The VALUE of this tool is the baked-in runtime rules, so that's what's pinned: `_joint_geometry_for`
must pick a VALID keypoint by entity kind — a cylinder/cone face uses MiddleKeyPoint (CenterKeyPoint
is invalid on a cylinder/cone face), a sphere/torus face uses CenterKeyPoint (MiddleKeyPoint is the
one the API refuses there), a planar face uses CenterKeyPoint, a circular edge uses center,
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


# The verbatim message createByNonPlanarFace raises when the keypoint is wrong for the face type.
_KEYPOINT_RAISE = "3 : Key point type should be CenterKeyPoint, if the face is sphere and torus face"


class _RaisingRecorder(_Recorder):
    """createByNonPlanarFace raises the way the live API does for a keypoint a face type refuses."""
    def createByNonPlanarFace(self, face, kp):
        self.calls.append(("nonplanar", kp))
        raise RuntimeError(_KEYPOINT_RAISE)


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


def _install(monkeypatch, rec=None):
    rec = rec if rec is not None else _Recorder()
    # JointGeometry factory -> our recorder
    monkeypatch.setattr(adsk.fusion, "JointGeometry", rec)
    # make the handler's isinstance checks use our fakes
    monkeypatch.setattr(adsk.fusion, "BRepFace", FakeBRepFace)
    monkeypatch.setattr(adsk.fusion, "BRepEdge", FakeBRepEdge)
    monkeypatch.setattr(adsk.fusion, "BRepVertex", FakeBRepVertex)
    monkeypatch.setattr(adsk.fusion, "ConstructionPoint", type("CP", (), {}))
    monkeypatch.setattr(adsk.fusion, "SketchPoint", type("SP", (), {}))
    return rec


# ── the runtime-rule logic (the whole point of the tool) ────────────────────

class TestJointGeometryRules:
    def test_cylinder_face_uses_MIDDLE_not_center(self, monkeypatch):
        # The key rule: CenterKeyPoint is invalid on a cylinder face — use MiddleKeyPoint.
        _install(monkeypatch)
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.CylinderSurfaceType))
        assert err is None
        assert g[1] == "nonplanar" and g[2] == _KP.MiddleKeyPoint     # createByNonPlanarFace + MiddleKeyPoint
        assert "cylinder" in label

    def test_cone_face_also_uses_middle(self, monkeypatch):
        _install(monkeypatch)
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.ConeSurfaceType))
        assert err is None and g[2] == _KP.MiddleKeyPoint

    def test_planar_face_uses_CENTER(self, monkeypatch):
        rec = _install(monkeypatch)
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.PlaneSurfaceType))
        assert err is None
        assert g[1] == "planar" and g[2] == _KP.CenterKeyPoint
        # the planar path is the ONLY factory a planar face touches - never createByNonPlanarFace
        assert rec.calls == [("planar", _KP.CenterKeyPoint)]

    def test_sphere_face_uses_CENTER_via_nonplanar(self, monkeypatch):
        # A sphere face accepts ONLY CenterKeyPoint; MiddleKeyPoint raises. Live: CenterKeyPoint
        # returns a JointGeometry at the sphere centre.
        rec = _install(monkeypatch)
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.SphereSurfaceType))
        assert err is None
        assert g[1] == "nonplanar" and g[2] == _KP.CenterKeyPoint
        assert label == "sphere_face@center"
        assert rec.calls == [("nonplanar", _KP.CenterKeyPoint)]

    def test_torus_face_uses_CENTER_via_nonplanar(self, monkeypatch):
        # Measured on a live torus face (surfaceType 4): MiddleKeyPoint raises the same keypoint
        # sentence the sphere raised, CenterKeyPoint returns a JointGeometry at the torus centre.
        _install(monkeypatch)
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.TorusSurfaceType))
        assert err is None
        assert g[1] == "nonplanar" and g[2] == _KP.CenterKeyPoint
        assert label == "torus_face@center"

    def test_other_nonplanar_face_still_uses_MIDDLE(self, monkeypatch):
        # only sphere/torus move to the centre keypoint - a NURBS face keeps the middle fallback
        _install(monkeypatch)
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.NurbsSurfaceType))
        assert err is None and g[2] == _KP.MiddleKeyPoint
        assert label == "nonplanar_face@middle"

    def test_api_raise_text_reaches_the_error(self, monkeypatch):
        # the platform's own sentence names the keypoint the face demands - it must not be flattened
        # to "createByNonPlanarFace failed", which tells the caller nothing actionable.
        _install(monkeypatch, _RaisingRecorder())
        g, label, err = jg._joint_geometry_for(FakeBRepFace(_ST.CylinderSurfaceType))
        assert g is None
        assert "should be CenterKeyPoint" in err and "sphere and torus" in err

    def test_circular_edge_uses_center(self, monkeypatch):
        _install(monkeypatch)
        g, label, err = jg._joint_geometry_for(FakeBRepEdge(_CT.Circle3DCurveType))
        assert err is None and g[1] == "curve" and g[2] == _KP.CenterKeyPoint

    def test_line_edge_uses_middle(self, monkeypatch):
        _install(monkeypatch)
        g, _, err = jg._joint_geometry_for(FakeBRepEdge(_CT.Line3DCurveType))
        assert err is None and g[2] == _KP.MiddleKeyPoint

    def test_vertex_uses_point(self, monkeypatch):
        _install(monkeypatch)
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
        self.motion = ("ball", a, b); return True


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


def _install_design(monkeypatch, token_map, joint_health=0, joint_msg="", rec=None):
    rec = _install(monkeypatch, rec)
    joints = _FakeJoints(joint_health, joint_msg)
    root = type("R", (), {"joints": joints})()
    class FakeDesign:
        rootComponent = root
        def findEntityByToken(self, h):
            e = token_map.get(h)
            return [e] if e is not None else []
    d = FakeDesign()
    app = type("A", (), {"activeProduct": d})()
    monkeypatch.setattr(jg, "app", app)
    monkeypatch.setattr(jg._common, "app", app)
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, FakeDesign) else None)
    return joints


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestHandler:
    def test_unknown_motion(self, monkeypatch):
        _install_design(monkeypatch, {})
        res = jg.handler(handle_one="a", handle_two="b", motion="weld")
        assert res["isError"] is True and "Unknown motion" in res["message"]

    def test_unresolved_handle(self, monkeypatch):
        _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType)})   # 'b' not in map
        res = jg.handler(handle_one="a", handle_two="b")
        # The typed GeometryHandle kind names the offending input and flags possible staleness.
        assert res["isError"] is True
        assert "handle_two" in res["message"] and "did not resolve" in res["message"]

    def test_revolute_named_axis_is_frame_relative(self, monkeypatch):
        joints = _install_design(monkeypatch, {"rod": FakeBRepFace(_ST.CylinderSurfaceType), "pin": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="rod", handle_two="pin", motion="revolute", axis="x"))
        assert out["jointed"] is True
        assert out["occurrence_one"] == "Rod:1" and out["occurrence_two"] == "Crank:1"
        # axis='x' passes XAxisJointDirection with NO custom entity - the joint FRAME's X, which is
        # world X only when the picked geometry's frame is world-aligned.
        assert joints.last_input.motion == ("revolute", _JD.XAxisJointDirection)

    def test_revolute_auto_axis_uses_geometry_axis(self, monkeypatch):
        # axis='auto' (default) on cylinder faces derives the axis FROM the geometry
        # (CustomJointDirection + the cylinder face as the axis entity), not a world axis.
        pin = FakeBRepFace(_ST.CylinderSurfaceType)
        joints = _install_design(monkeypatch, {"rod": FakeBRepFace(_ST.CylinderSurfaceType), "pin": pin})
        out = _payload(jg.handler(handle_one="rod", handle_two="pin", motion="revolute"))
        m = joints.last_input.motion
        assert m[0] == "revolute" and m[1] == _JD.CustomJointDirection      # CustomJointDirection used
        assert m[2] is not None                              # an axis entity was passed
        assert out["axis"] == "auto(geometry)"

    def test_slider_auto_axis_from_geometry(self, monkeypatch):
        joints = _install_design(monkeypatch, {"pis": FakeBRepFace(_ST.CylinderSurfaceType), "bore": FakeBRepFace(_ST.CylinderSurfaceType)})
        _payload(jg.handler(handle_one="pis", handle_two="bore", motion="slider"))
        m = joints.last_input.motion
        assert m[0] == "slider" and m[1] == _JD.CustomJointDirection

    def test_reports_health_warning_when_joint_fails_to_compute(self, monkeypatch):
        # a joint can ADD fine yet report healthState=1 (over-constrained / Compute Failed) - the
        # handler must surface that as a health warning, not a false success.
        _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)},
                        joint_health=1, joint_msg="Can't resolve positions.Compute FailedX")
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert out["healthy"] is False
        assert "FAILED TO COMPUTE" in out["health_warning"]
        assert "Compute Failed" not in out["health_warning"]   # message trimmed

    def test_healthy_joint_no_warning(self, monkeypatch):
        _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert out["healthy"] is True and "health_warning" not in out

    def test_rigid_motion_has_null_axis(self, monkeypatch):
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert joints.last_input.motion == ("rigid",)
        assert out["axis"] is None                       # rigid has no motion axis to report

    def test_ball_motion_pins_pitch_z_yaw_x(self, monkeypatch):
        # Live API fact: setAsBallJointMotion(pitchDirection, yawDirection) REJECTS X as the pitch
        # ("Invalid parameter pitchDirection") - the valid pair is pitch=Z, yaw=X. A mock accepts
        # any args, so only pinning the enum pair catches a swap before a live document does.
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="ball"))
        assert joints.last_input.motion == ("ball", _JD.ZAxisJointDirection, _JD.XAxisJointDirection)
        assert out["jointed"] is True

    def test_slider_named_axis_is_frame_relative(self, monkeypatch):
        # axis='z' on cylinder faces takes the frame-relative Z direction (no CustomJointDirection).
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        _payload(jg.handler(handle_one="a", handle_two="b", motion="slider", axis="z"))
        assert joints.last_input.motion == ("slider", _JD.ZAxisJointDirection)

    def test_cylindrical_named_axis_is_frame_relative(self, monkeypatch):
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        _payload(jg.handler(handle_one="a", handle_two="b", motion="cylindrical", axis="y"))
        assert joints.last_input.motion == ("cyl", _JD.YAxisJointDirection)

    def test_auto_axis_with_no_geometry_axis_falls_back_to_frame_z(self, monkeypatch):
        # PLANAR faces give _axis_entity nothing -> 'auto' can't derive an axis; the motion uses the
        # default frame-relative Z direction and the reported axis is plain 'auto', NOT 'auto(geometry)'.
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.PlaneSurfaceType), "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert joints.last_input.motion == ("revolute", _JD.ZAxisJointDirection)   # frame Z, not CUSTOM
        assert out["axis"] == "auto"

    def test_unknown_axis_keyword_errors(self, monkeypatch):
        # An unrecognized axis string (not x/y/z, not auto) must be REFUSED, naming the offending
        # value - not silently coerced to the world Z direction.
        _install_design(monkeypatch, {"a": FakeBRepFace(_ST.PlaneSurfaceType), "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        res = jg.handler(handle_one="a", handle_two="b", motion="revolute", axis="diagonal")
        assert res["isError"] is True
        assert "diagonal" in res["message"]

    def test_circular_edge_is_an_axis_entity_for_auto(self, monkeypatch):
        # a circular edge can define the motion axis (auto -> CustomJointDirection + the edge).
        joints = _install_design(monkeypatch, {"a": FakeBRepEdge(_CT.Circle3DCurveType), "b": FakeBRepEdge(_CT.Circle3DCurveType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        m = joints.last_input.motion
        assert m[0] == "revolute" and m[1] == _JD.CustomJointDirection and m[2] is not None
        assert out["axis"] == "auto(geometry)"

    def test_reports_moved_by_when_part_repositioned(self, monkeypatch):
        # joint_at aligns the picked keypoints, repositioning handle_one's occurrence; a real move
        # must surface as moved_by + move_warning, not silently (the teleport defect: a member
        # relocating to the joint without the caller being told).
        moving = _MovingOcc("Rod:1", (10.0, 0.0, 0.0))    # cm - the moving occurrence's origin
        face_a = FakeBRepFace(_ST.PlaneSurfaceType); face_a.assemblyContext = moving
        face_b = FakeBRepFace(_ST.PlaneSurfaceType)
        joints = _install_design(monkeypatch, {"a": face_a, "b": face_b})
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

    def test_reports_moved_by_when_the_fixed_side_moves(self, monkeypatch):
        # grounding / an existing joint can make the solver move occurrence_TWO instead of one - both
        # sides are watched, and the mover is named (observed live: the wrong member can be the one that moves).
        moving = _MovingOcc("Crank:1", (0.0, 0.0, 0.0))
        face_a = FakeBRepFace(_ST.PlaneSurfaceType)                        # handle_one stays put
        face_b = FakeBRepFace(_ST.PlaneSurfaceType); face_b.assemblyContext = moving
        joints = _install_design(monkeypatch, {"a": face_a, "b": face_b})
        orig_add = joints.add
        def moving_add(ji):
            j = orig_add(ji); moving.move_to((3.0, 0.0, 0.0)); return j    # 3 cm = 30 mm
        joints.add = moving_add
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "moved_by" in out and out["moved_by"]["distance_mm"] == 30.0
        assert "Crank:1" in out["move_warning"]

    def test_no_moved_by_when_part_stays_put(self, monkeypatch):
        # a well-matched pair whose keypoints already coincide does not move -> no moved_by / warning.
        still = _MovingOcc("Rod:1", (4.0, 1.0, 0.0))
        face_a = FakeBRepFace(_ST.PlaneSurfaceType); face_a.assemblyContext = still
        face_b = FakeBRepFace(_ST.PlaneSurfaceType)
        _install_design(monkeypatch, {"a": face_a, "b": face_b})       # add() leaves the position unchanged
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "moved_by" not in out and "move_warning" not in out

    def test_motion_setter_failure_reports_error(self, monkeypatch):
        # if the motion setter raises (e.g. incompatible geometry), the handler returns an error
        # naming the motion + the axis hint, NOT a false success.
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})

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
        assert "pass axis=x/y/z" in res["message"]      # a motion WITH an axis gets the axis advice

    def test_ball_motion_failure_carries_no_axis_advice(self, monkeypatch):
        # setAsBallJointMotion never reads 'axis', so telling a failed ball caller to pass one
        # contradicts the input's own "ball uses none" and sends them after a knob that does nothing.
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType), "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        orig = joints.createInput
        def make(g1, g2):
            ji = orig(g1, g2)
            ji.setAsBallJointMotion = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rejected"))
            return ji
        joints.createInput = make
        res = jg.handler(handle_one="a", handle_two="b", motion="ball")
        assert res["isError"] is True
        assert "Could not set ball motion" in res["message"]
        assert "axis=" not in res["message"]

    def test_sphere_face_handle_joints_instead_of_being_refused(self, monkeypatch):
        # a sphere face IS supported joint geometry - it just needs CenterKeyPoint; the handler must
        # build it, not refuse the handle.
        _install_design(monkeypatch, {"a": FakeBRepFace(_ST.SphereSurfaceType),
                                      "b": FakeBRepFace(_ST.CylinderSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="ball"))
        assert out["jointed"] is True and out["geometry_one"] == "sphere_face@center"

    def test_keypoint_raise_reaches_the_handler_error(self, monkeypatch):
        # the API's own actionable sentence must survive to the caller, named to the offending input
        _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType),
                                      "b": FakeBRepFace(_ST.CylinderSurfaceType)},
                        rec=_RaisingRecorder())
        res = jg.handler(handle_one="a", handle_two="b", motion="revolute")
        assert res["isError"] is True
        assert "handle_one" in res["message"] and "should be CenterKeyPoint" in res["message"]

    def test_flip_sets_isFlipped_on_the_joint_input(self, monkeypatch):
        # flip=true must reach the JointInput BEFORE add() - a dropped flag silently recreates the
        # 180-deg flush-mate rotation the input exists to prevent.
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.PlaneSurfaceType),
                                  "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid", flip=True))
        assert joints.last_input.isFlipped is True
        assert out["flipped"] is True

    def test_no_flip_leaves_joint_input_unflipped(self, monkeypatch):
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.PlaneSurfaceType),
                                  "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert not getattr(joints.last_input, "isFlipped", False)
        assert out["flipped"] is False


class TestAxisNote:
    """The note must state what the motion axis ACTUALLY is. A named x/y/z takes the frame-relative
    path: measured on a 120-deg-rotated frame, axis='y' drove about the frame's Y, (0,-0.5,0.866) -
    120 deg off world Y. Claiming a world axis there would be a false claim on the wire. A ball joint
    takes NO axis at all, so it gets no axis claim in either the note or the payload."""

    def _cyl_pair(self, monkeypatch):
        return _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType),
                                             "b": FakeBRepFace(_ST.CylinderSurfaceType)})

    def test_named_axis_note_says_frame_not_world(self, monkeypatch):
        self._cyl_pair(monkeypatch)
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute", axis="y"))
        assert "FRAME's y axis, NOT world y" in out["note"]
        assert "joint_edit(world_axis=" in out["note"]

    def test_auto_geometry_axis_note_credits_the_geometry(self, monkeypatch):
        self._cyl_pair(monkeypatch)
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert "derived the motion axis from the geometry" in out["note"]
        assert "NOT world" not in out["note"]

    def test_auto_without_a_geometry_axis_still_warns_frame_relative(self, monkeypatch):
        # planar faces -> no derivable axis, so the frame-relative default Z is what was used
        _install_design(monkeypatch, {"a": FakeBRepFace(_ST.PlaneSurfaceType),
                                      "b": FakeBRepFace(_ST.PlaneSurfaceType)})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="revolute"))
        assert "FRAME's z axis, NOT world z" in out["note"]

    def test_rigid_note_makes_no_axis_claim(self, monkeypatch):
        self._cyl_pair(monkeypatch)
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "axis" not in out["note"]

    def test_ball_note_makes_no_axis_claim_even_with_a_named_axis(self, monkeypatch):
        # setAsBallJointMotion hard-codes pitch=Z / yaw=X and reads NEITHER the axis keyword nor a
        # custom entity - a ball landed with axis='x' is identical to one landed with 'auto'. Any
        # axis sentence here (frame-relative OR geometry-derived) would be a measured-false claim.
        self._cyl_pair(monkeypatch)
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="ball", axis="x"))
        assert "FRAME's" not in out["note"]
        assert "world_axis=" not in out["note"]
        assert "derived the motion axis" not in out["note"]

    def test_ball_payload_axis_is_null(self, monkeypatch):
        # cylinder faces make _axis_entity fire, so the un-carved payload would report
        # 'auto(geometry)' - false: the ball motion consumed no axis entity at all.
        self._cyl_pair(monkeypatch)
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="ball"))
        assert out["axis"] is None

    def test_ball_payload_axis_is_null_with_a_named_axis(self, monkeypatch):
        self._cyl_pair(monkeypatch)
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="ball", axis="x"))
        assert out["axis"] is None


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
        _install_design(monkeypatch, {"a": fa, "b": fb})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "flip_hint" in out and "180" in out["flip_hint"] and "flip=true" in out["flip_hint"]

    def test_opposing_normals_with_flip_no_hint(self, monkeypatch):
        fa, fb = self._faces(monkeypatch, [0.0, 0.0, -1.0], [0.0, 0.0, 1.0])
        _install_design(monkeypatch, {"a": fa, "b": fb})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid", flip=True))
        assert "flip_hint" not in out

    def test_agreeing_normals_no_hint(self, monkeypatch):
        fa, fb = self._faces(monkeypatch, [0.0, 0.0, 1.0], [0.0, 0.0, 1.0])
        _install_design(monkeypatch, {"a": fa, "b": fb})
        out = _payload(jg.handler(handle_one="a", handle_two="b", motion="rigid"))
        assert "flip_hint" not in out


class TestAxisSchema:
    def test_axis_reaches_the_wire_as_a_validated_enum(self):
        # The legal values must be carried by the SCHEMA, where they are machine-validated, not
        # asserted in prose that drifts. Read off the BUILT tool, so unwiring the kind fails too.
        props = jg.joint_at_tool.to_dict()["inputSchema"]["properties"]
        assert props["axis"]["enum"] == ["auto", "x", "y", "z"]
        assert jg._AXIS.default == "auto"
        # the one axis fact no enum can carry: a ball joint reads no axis at all
        assert "ball uses none" in props["axis"]["description"]


class TestAsBuiltRigidRefusal:
    """A rigid as-built joint carries NO joint geometry ("Geometry should not be null if joint motion
    is not rigid"), so the API cannot redefine it as a motion joint. The refusal must point at the
    tool that CAN build one - joint_create_as_built, which takes the 'geometry' the motion anchors on
    - rather than fail bare, and it must not attempt the setter first."""

    def _as_built(self, monkeypatch, geometry):
        class FakeAsBuilt:
            def __init__(self):
                self.geometry = geometry
                self.calls = []
            def setAsRevoluteJointMotion(self, *args):
                self.calls.append(args); return True
        monkeypatch.setattr(adsk.fusion, "AsBuiltJoint", FakeAsBuilt)
        return FakeAsBuilt()

    def test_refusal_points_at_the_tool_that_can_build_the_motion_joint(self, monkeypatch):
        ji = self._as_built(monkeypatch, None)
        did, err = jg.apply_motion(ji, "revolute", 2)
        assert did is False
        # the pointer must name the tool AND the input that makes it work, or it is not actionable
        assert "joint_create_as_built" in err and "'geometry'" in err
        assert "revolute" in err                      # names the motion that was refused
        assert ji.calls == []                         # nothing attempted on the joint

    def test_as_built_with_geometry_takes_the_extra_arity_setter(self, monkeypatch):
        # the refusal is scoped to the no-geometry case: an as-built joint that HAS an anchor is
        # redefined through the setter's as-built arity (direction, geometry).
        geom = object()
        ji = self._as_built(monkeypatch, geom)
        did, err = jg.apply_motion(ji, "revolute", 1)
        assert did is True and err is None
        assert ji.calls == [(_JD.YAxisJointDirection, geom)]


class TestModelParameters:
    def test_payload_names_the_joints_own_dnn_params(self, monkeypatch):
        # the created joint's offset/angle ModelParameter names must reach the payload (with the
        # shared offset-is-frame-Z teaching) so an agent can param_set the right dNN.
        joints = _install_design(monkeypatch, {"a": FakeBRepFace(_ST.CylinderSurfaceType),
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
