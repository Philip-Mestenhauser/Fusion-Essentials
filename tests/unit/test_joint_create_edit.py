"""Unit tests for ``joint_create_edit.py`` pure logic.

Targets: ``_available_joint_origins`` (the collect-names leaf over the shared _joints JO walk),
``_resolve_input`` (handle / snap / JO-name dispatch, now routing the JO-name path through the
JointOriginRef kind), and ``_apply_motion`` (dispatch by joint type, incl. the unsupported-type
fallthrough). Resolve-one/qualified/ambiguity of a JO by name is the JointOriginRef kind's contract -
tested in test_inputs.py, not here.
"""

import json
from types import SimpleNamespace

from conftest import load_tool

joint = load_tool("joint_create_edit")


# ── JO collection fake: count/item (the shared _joints walk) AND itemByName (scoped lookups) ─────────

class _JOCollection:
    """A JointOrigins collection exposing BOTH the walk protocol (count/item) and itemByName."""
    def __init__(self, by_name):
        self._by_name = dict(by_name)
        self._items = list(by_name.values())

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def itemByName(self, name):
        return self._by_name.get(name)


# ── _apply_motion: dispatch + fallthrough ──────────────────────────────────

class _JointInput:
    """Records which motion setter was called; each returns True (success)."""
    def __init__(self):
        self.called = None

    def setAsRigidJointMotion(self):
        self.called = "rigid"; return True

    def setAsRevoluteJointMotion(self, ax):
        self.called = ("revolute", ax); return True

    def setAsSliderJointMotion(self, ax):
        self.called = ("slider", ax); return True

    def setAsPlanarJointMotion(self, ax):
        self.called = ("planar", ax); return True

    def setAsCylindricalJointMotion(self, ax):
        self.called = ("cylindrical", ax); return True

    def setAsBallJointMotion(self, a, b):
        self.called = "ball"; return True

    def setAsPinSlotJointMotion(self, rot, slide, *rest):
        # rest = (customRotationAxisEntity[, customSlideDirectionEntity]) when a custom axis is used.
        self.called = ("pin_slot", rot, slide, rest); return True


class TestApplyMotion:
    def test_rigid(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "rigid", 2)
        assert ok is True and err is None
        assert ji.called == "rigid"

    def test_slider_uses_axis_index(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "slider", 0)  # X axis
        assert ok is True and err is None
        assert ji.called[0] == "slider"

    def test_unsupported_type_reports_error(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "warp_drive", 2)
        assert ok is False
        assert "warp_drive" in err
        assert ji.called is None


# ── _apply_motion: pin_slot dispatch (rotation axis + distinct slide direction) ─────────────────────

import adsk.fusion as _adsk_fusion

_JD = _adsk_fusion.JointDirections


class TestApplyMotionPinSlot:
    def test_default_slide_is_next_frame_axis(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "pin_slot", 2)   # rotation = z
        assert ok is True and err is None
        kind, rot, slide, rest = ji.called
        assert kind == "pin_slot"
        assert rot is _JD.ZAxisJointDirection               # rotation = axis_idx 2
        assert slide is _JD.XAxisJointDirection             # slide default = (2+1)%3 = 0 -> x
        assert rest == ()                                   # no custom entity

    def test_explicit_slide_axis_used(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "pin_slot", 0, slide_axis_idx=2)   # rot x, slide z
        assert ok is True and err is None
        kind, rot, slide, rest = ji.called
        assert rot is _JD.XAxisJointDirection and slide is _JD.ZAxisJointDirection

    def test_slide_equal_to_rotation_refused(self):
        ji = _JointInput()
        ok, err = joint._apply_motion(ji, "pin_slot", 1, slide_axis_idx=1)
        assert ok is False
        assert "differ" in err
        assert ji.called is None                            # setter never reached

    def test_custom_entity_repoints_rotation_slide_stays_frame(self):
        ji = _JointInput()
        sentinel = object()
        ok, err = joint._apply_motion(ji, "pin_slot", 2, custom_entity=sentinel)
        assert ok is True and err is None
        kind, rot, slide, rest = ji.called
        assert rot is _JD.CustomJointDirection              # rotation re-pointed to the custom axis
        assert slide is _JD.XAxisJointDirection             # slide still frame-relative
        assert rest == (sentinel,)


# ── _slide_index / _slide_name: pin_slot slide-axis resolution ──────────────────────────────────────

class TestSlideAxisHelpers:
    def test_blank_defaults_to_none(self):
        assert joint._slide_index("", "z") == (None, None)

    def test_valid_distinct_axis(self):
        assert joint._slide_index("x", "z") == (0, None)

    def test_same_as_rotation_errors(self):
        idx, err = joint._slide_index("z", "z")
        assert idx is None and "differ" in err

    def test_unknown_axis_errors(self):
        idx, err = joint._slide_index("w", "z")
        assert idx is None and "Unknown slide_axis" in err

    def test_slide_name_default_is_perpendicular(self):
        assert joint._slide_name(None, "z") == "x"
        assert joint._slide_name(None, "x") == "y"
        assert joint._slide_name(2, "x") == "z"


# ── handler: pin_slot flows through create and reports the effective slide axis ─────────────────────

from conftest import payload as _payload


def _fake_design_for_create(ji, joint_obj):
    joints = SimpleNamespace(createInput=lambda a, b: ji, add=lambda inp: joint_obj)
    return SimpleNamespace(rootComponent=SimpleNamespace(joints=joints))


class TestCreatePinSlot:
    def _wire(self, monkeypatch, ji, joint_obj):
        design = _fake_design_for_create(ji, joint_obj)
        monkeypatch.setattr(joint._common, "design", lambda: design)
        monkeypatch.setattr(joint, "_resolve_input",
                            lambda d, spec: (SimpleNamespace(name=spec), spec, None))
        return design

    def test_create_pin_slot_reports_default_slide_axis(self, monkeypatch):
        ji = _JointInput()
        self._wire(monkeypatch, ji, SimpleNamespace(name="Joint1"))
        out = _payload(joint.handler(occurrence_one="A", occurrence_two="B",
                                     joint_type="pin_slot", axis="z"))
        assert out["created"] is True
        assert out["joint_type"] == "pin_slot"
        assert out["axis"] == "z"
        assert out["slide_axis"] == "x"                     # default perpendicular, surfaced
        assert ji.called[0] == "pin_slot"

    def test_create_pin_slot_explicit_slide_axis(self, monkeypatch):
        ji = _JointInput()
        self._wire(monkeypatch, ji, SimpleNamespace(name="Joint1"))
        out = _payload(joint.handler(occurrence_one="A", occurrence_two="B",
                                     joint_type="pin_slot", axis="x", slide_axis="y"))
        assert out["slide_axis"] == "y"

    def test_create_pin_slot_slide_equal_axis_refused_before_build(self, monkeypatch):
        ji = _JointInput()
        self._wire(monkeypatch, ji, SimpleNamespace(name="Joint1"))
        res = joint.handler(occurrence_one="A", occurrence_two="B",
                            joint_type="pin_slot", axis="z", slide_axis="z")
        assert res["isError"] is True
        assert "differ" in res["message"]
        assert ji.called is None                            # never reached the motion setter


# ── edit_handler: posing is joint_drive's job ──────────────────────────────

class TestEditRotationRedirect:
    def test_rotation_deg_redirects_to_joint_drive(self, monkeypatch):
        monkeypatch.setattr(joint._common, "design", lambda: SimpleNamespace())
        monkeypatch.setattr(joint, "_find_joint", lambda design, name: SimpleNamespace(name="J"))
        out = joint.edit_handler(joint_name="J", rotation_deg=45)
        assert out["isError"] is True
        msg = out["content"][0]["text"]
        assert "joint_drive" in msg
        assert "assembly_move" not in msg


# ── _apply_limits: shared by create + edit; rotation(rad) vs linear(cm) ──────

import math as _math


class _Lim:
    def __init__(self):
        self.isMinimumValueEnabled = False
        self.isMaximumValueEnabled = False
        self.isRestValueEnabled = False
        self.minimumValue = None
        self.maximumValue = None
        self.restValue = None


class _RevMotion:
    def __init__(self):
        self.rotationLimits = _Lim()
        self.slideLimits = None   # revolute has no slide limits


class _SlideMotion:
    def __init__(self):
        self.rotationLimits = None
        self.slideLimits = _Lim()


class TestApplyLimits:
    def test_rotation_in_radians(self):
        m = _RevMotion()
        changed, err = joint._apply_limits(m, min_deg=-45, max_deg=90)
        assert err is None
        assert m.rotationLimits.isMinimumValueEnabled and m.rotationLimits.isMaximumValueEnabled
        assert abs(m.rotationLimits.minimumValue - _math.radians(-45)) < 1e-9
        assert abs(m.rotationLimits.maximumValue - _math.radians(90)) < 1e-9
        assert changed["min_deg"] == -45 and changed["max_deg"] == 90

    def test_linear_in_cm(self):
        m = _SlideMotion()
        changed, err = joint._apply_limits(m, min_mm=0, max_mm=300, cm_scale=0.1)
        assert err is None
        assert abs(m.slideLimits.maximumValue - 30.0) < 1e-9   # 300 mm -> 30 cm
        assert changed["max_mm"] == 300

    def test_rest_values(self):
        m = _RevMotion()
        joint._apply_limits(m, rest_deg=10)
        assert m.rotationLimits.isRestValueEnabled
        assert abs(m.rotationLimits.restValue - _math.radians(10)) < 1e-9

    def test_rotation_on_slider_errors(self):
        m = _SlideMotion()
        changed, err = joint._apply_limits(m, min_deg=10)
        assert err is not None and "rotation" in err.lower()

    def test_linear_on_revolute_errors(self):
        m = _RevMotion()
        changed, err = joint._apply_limits(m, max_mm=100)
        assert err is not None and ("slide" in err.lower() or "linear" in err.lower())


# ── _resolve_input: the geometry-as-values HANDLE path ──────────
#
# A find_geometry handle is now a first-class joint input: it resolves to a JointGeometry AT the real
# geometry, instead of the ':origin' snap that collapses both parts to (0,0,0). Distinguished from a
# JointOrigin NAME by RESOLVING — a token that findEntityByToken yields nothing for falls through to the
# snap/JO-name paths (so existing inputs keep working).

class _FakePlanarFace:
    def __init__(self):
        import adsk.core
        self.geometry = type("G", (), {"surfaceType": adsk.core.SurfaceTypes.PlaneSurfaceType})()


def _install_resolve_seam(monkeypatch, token_map):
    import adsk.fusion, adsk.core
    # JointGeometry factory -> a sentinel recorder so we can assert which builder ran.
    rec = type("R", (), {
        "createByPlanarFace": staticmethod(lambda f, e, kp: ("planar", kp)),
        "createByNonPlanarFace": staticmethod(lambda f, kp: ("nonplanar", kp)),
        "createByCurve": staticmethod(lambda c, kp: ("curve", kp)),
        "createByPoint": staticmethod(lambda p: ("point",)),
    })
    monkeypatch.setattr(adsk.fusion, "JointGeometry", rec)
    monkeypatch.setattr(adsk.fusion, "BRepFace", _FakePlanarFace)
    monkeypatch.setattr(adsk.fusion, "BRepEdge", type("E", (), {}))
    monkeypatch.setattr(adsk.fusion, "BRepVertex", type("V", (), {}))
    monkeypatch.setattr(adsk.fusion, "ConstructionPoint", type("CP", (), {}))
    monkeypatch.setattr(adsk.fusion, "SketchPoint", type("SP", (), {}))
    # a real type so is_joint_origin() works
    monkeypatch.setattr(adsk.fusion, "JointOrigin", type("JointOrigin", (), {}))

    class FakeDesign:
        def __init__(self):
            self.rootComponent = SimpleNamespace(name="Root", jointOrigins=_JOCollection({}))
            self.allComponents = []

        def findEntityByToken(self, h):
            e = token_map.get(h)
            return [e] if e is not None else []
    d = FakeDesign()
    # Dual-seam: the handler reads design via joint._common, but the JointOriginRef kind (the JO-name
    # path) resolves through joint._inputs._common - patch BOTH to the same design (tests/CLAUDE.md).
    monkeypatch.setattr(joint._common, "design", lambda: d)
    monkeypatch.setattr(joint._inputs._common, "design", lambda: d)
    return d


class TestResolveInputHandle:
    def test_handle_resolves_to_joint_geometry_at_real_face(self, monkeypatch):
        face = _FakePlanarFace()
        design = _install_resolve_seam(monkeypatch, {"H_FACE": face})
        g, label, err = joint._resolve_input(design, "H_FACE")
        import adsk.fusion
        assert err is None
        # built a JointGeometry from the face, not an origin
        assert g == ("planar", adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
        assert label.startswith("handle:")

    def test_non_token_falls_through_to_jo_name(self, monkeypatch):
        # 'JO_A' is not a resolvable token -> handle path declines, JO-name path (JointOriginRef) resolves it.
        design = _install_resolve_seam(monkeypatch, {})
        target = SimpleNamespace(name="JO_A")
        design.rootComponent.jointOrigins = _JOCollection({"JO_A": target})
        g, label, err = joint._resolve_input(design, "JO_A")
        assert err is None and g is target and label == "JO_A"

    def test_joint_origin_handle_used_directly(self, monkeypatch):
        # A JOINT ORIGIN handle (assembly_get mints these) is a first-class joint input - used directly,
        # NOT run through build_joint_geometry (which would reject it).
        import adsk.fusion
        design = _install_resolve_seam(monkeypatch, {})
        jo = adsk.fusion.JointOrigin()
        jo.name = "Stock_Center"
        design.rootComponent.jointOrigins = _JOCollection({})
        design._tokens = {}
        # re-point findEntityByToken at a map holding the JO handle
        design.findEntityByToken = lambda h: [jo] if h == "H_JO" else []
        g, label, err = joint._resolve_input(design, "H_JO")
        assert err is None and g is jo and label == "handle:joint_origin"

    def test_unresolvable_spec_errors_naming_all_paths(self, monkeypatch):
        design = _install_resolve_seam(monkeypatch, {})
        g, label, err = joint._resolve_input(design, "Nope")
        assert g is None
        assert "handle" in err and "Joint Origin" in err and "snap" in err


# The resolve-one / qualified '<occurrence>:<JO name>' / ambiguity contract now lives on the
# JointOriginRef kind - exercised in test_inputs.py (TestJointOriginRef), not here.

class _IterableJOs:
    """A JointOrigins collection: count/item (the shared walk) + itemByName + iteration."""
    def __init__(self, by_name):
        self._by_name = dict(by_name)
        self._items = list(by_name.values())

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def itemByName(self, name):
        return self._by_name.get(name)

    def __iter__(self):
        return iter(self._by_name.values())


# ── resolve-failure error lists the design's Joint Origins (self-correction data) ───────────

class TestResolveErrorListsJointOrigins:
    def _design_with_jos(self, monkeypatch):
        root_jo = SimpleNamespace(name="Attach Center of Workpiece")
        sub_jo = SimpleNamespace(name="Center of Model")
        root = SimpleNamespace(name="RootComp", jointOrigins=_IterableJOs(
            {"Attach Center of Workpiece": root_jo}))
        sub = SimpleNamespace(name="SculpturalTower", jointOrigins=_IterableJOs(
            {"Center of Model": sub_jo}))
        design = _install_resolve_seam(monkeypatch, {})
        design.rootComponent = root
        design.allComponents = [root, sub]
        return design

    def test_error_names_each_jo_and_owner(self, monkeypatch):
        design = self._design_with_jos(monkeypatch)
        g, label, err = joint._resolve_input(design, "Wrong Name")
        assert g is None
        assert "'Attach Center of Workpiece' (root)" in err
        assert "'Center of Model' (in component 'SculpturalTower')" in err

    def test_listing_is_capped_with_overflow_count(self, monkeypatch):
        design = self._design_with_jos(monkeypatch)
        many = {f"JO_{i}": SimpleNamespace(name=f"JO_{i}") for i in range(12)}
        design.rootComponent.jointOrigins = _IterableJOs(many)
        listed, more = joint._available_joint_origins(design, limit=8)
        assert len(listed) == 8
        assert more == 5  # 12 root + 1 sub-component JO, 8 listed

    def test_no_jos_keeps_error_unadorned(self, monkeypatch):
        design = _install_resolve_seam(monkeypatch, {})
        g, label, err = joint._resolve_input(design, "Nope")
        assert "Joint Origins in this design" not in err


# ── _fmt_num: parameter-expression number formatting ────────────────────────
# offset/angle expressions feed straight into a Fusion ModelParameter ("{n} mm"); a trailing ".0"
# is undesirable. _fmt_num drops it for whole numbers but keeps real fractions.

class TestFmtNum:
    def test_whole_number_drops_trailing_zero(self):
        assert joint._fmt_num(200) == "200"
        assert joint._fmt_num(200.0) == "200"
        assert joint._fmt_num(-200.0) == "-200"
        assert joint._fmt_num(0) == "0"

    def test_fractional_kept(self):
        assert joint._fmt_num(2.5) == "2.5"
        assert joint._fmt_num(-0.125) == "-0.125"


# ── _jg_from_entity: VALID keypoint per entity kind (the runtime rule) ──────
# Mirrors joint_at_geometry: a planar face -> CenterKeyPoint, a cyl/cone (non-planar) face ->
# MiddleKeyPoint (CenterKeyPoint is invalid there), a circular edge -> center, a line edge ->
# middle, a vertex/point -> createByPoint.

def _jg_seam(monkeypatch):
    """Install the JointGeometry recorder + entity-kind fakes the resolver branches on."""
    import adsk.fusion, adsk.core
    calls = []
    rec = type("R", (), {
        "createByPlanarFace": staticmethod(lambda f, e, kp: calls.append(("planar", kp)) or ("planar", kp)),
        "createByNonPlanarFace": staticmethod(lambda f, kp: calls.append(("nonplanar", kp)) or ("nonplanar", kp)),
        "createByCurve": staticmethod(lambda c, kp: calls.append(("curve", kp)) or ("curve", kp)),
        "createByPoint": staticmethod(lambda p: calls.append(("point", None)) or ("point",)),
    })
    monkeypatch.setattr(adsk.fusion, "JointGeometry", rec)

    class _Face:
        def __init__(self, stype):
            self.geometry = type("G", (), {"surfaceType": stype})()

    class _Edge:
        def __init__(self, ctype):
            self.geometry = type("G", (), {"curveType": ctype})()

    class _Vertex:
        pass

    monkeypatch.setattr(adsk.fusion, "BRepFace", _Face)
    monkeypatch.setattr(adsk.fusion, "BRepEdge", _Edge)
    monkeypatch.setattr(adsk.fusion, "BRepVertex", _Vertex)
    monkeypatch.setattr(adsk.fusion, "ConstructionPoint", type("CP", (), {}))
    monkeypatch.setattr(adsk.fusion, "SketchPoint", type("SP", (), {}))
    return _Face, _Edge, _Vertex


class TestJgFromEntity:
    def test_planar_face_center(self, monkeypatch):
        import adsk.core, adsk.fusion
        Face, _, _ = _jg_seam(monkeypatch)
        g, label, err = joint._jg_from_entity(Face(adsk.core.SurfaceTypes.PlaneSurfaceType))
        assert err is None and "planar" in label
        assert g == ("planar", adsk.fusion.JointKeyPointTypes.CenterKeyPoint)

    def test_cylinder_face_middle_not_center(self, monkeypatch):
        import adsk.core, adsk.fusion
        Face, _, _ = _jg_seam(monkeypatch)
        g, label, err = joint._jg_from_entity(Face(adsk.core.SurfaceTypes.CylinderSurfaceType))
        # CenterKeyPoint invalid on a cylinder
        assert err is None and g == ("nonplanar", adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)

    def test_circular_edge_center(self, monkeypatch):
        import adsk.core, adsk.fusion
        _, Edge, _ = _jg_seam(monkeypatch)
        g, label, err = joint._jg_from_entity(Edge(adsk.core.Curve3DTypes.Circle3DCurveType))
        assert err is None and label == "edge"
        assert g == ("curve", adsk.fusion.JointKeyPointTypes.CenterKeyPoint)

    def test_line_edge_middle(self, monkeypatch):
        import adsk.core, adsk.fusion
        _, Edge, _ = _jg_seam(monkeypatch)
        g, _, err = joint._jg_from_entity(Edge(adsk.core.Curve3DTypes.Line3DCurveType))
        assert err is None and g == ("curve", adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)

    def test_vertex_uses_point(self, monkeypatch):
        _, _, Vertex = _jg_seam(monkeypatch)
        g, label, err = joint._jg_from_entity(Vertex())
        assert err is None and g == ("point",) and label == "point"

    def test_unsupported_entity_errors(self, monkeypatch):
        _jg_seam(monkeypatch)
        g, label, err = joint._jg_from_entity(object())
        assert g is None and err is not None and "not a supported joint geometry" in err


# ── _current_joint_type: JointMotion subclass name -> our keyword ───────────

class TestCurrentJointType:
    def _joint_with_motion(self, class_name):
        jm = type(class_name, (), {})() if class_name else None
        return SimpleNamespace(jointMotion=jm)

    def test_maps_each_motion_class(self):
        assert joint._current_joint_type(self._joint_with_motion("RevoluteJointMotion")) == "revolute"
        assert joint._current_joint_type(self._joint_with_motion("SliderJointMotion")) == "slider"
        assert joint._current_joint_type(self._joint_with_motion("CylindricalJointMotion")) == "cylindrical"
        assert joint._current_joint_type(self._joint_with_motion("PlanarJointMotion")) == "planar"
        assert joint._current_joint_type(self._joint_with_motion("RigidJointMotion")) == "rigid"
        assert joint._current_joint_type(self._joint_with_motion("BallJointMotion")) == "ball"

    def test_unknown_class_is_empty(self):
        assert joint._current_joint_type(self._joint_with_motion("MysteryJointMotion")) == ""

    def test_no_motion_is_empty(self):
        assert joint._current_joint_type(self._joint_with_motion(None)) == ""


# ── _world_axis_entity: pick the right root construction axis ────────────────

class TestWorldAxisEntity:
    def test_picks_axis_by_index(self):
        root = SimpleNamespace(xConstructionAxis="WX", yConstructionAxis="WY", zConstructionAxis="WZ")
        design = SimpleNamespace(rootComponent=root)
        assert joint._world_axis_entity(design, 0) == "WX"
        assert joint._world_axis_entity(design, 1) == "WY"
        assert joint._world_axis_entity(design, 2) == "WZ"


# ── _face_extent / _is_planar: bbox projection + planarity gate ─────────────

class TestFaceExtentAndPlanar:
    def test_extent_projects_onto_axis(self):
        f = _Face_for_extent((-3.0, 1.0, 5.0), (7.0, 2.0, 9.0))
        assert joint._face_extent(f, 0) == (-3.0, 7.0)   # x
        assert joint._face_extent(f, 1) == (1.0, 2.0)    # y
        assert joint._face_extent(f, 2) == (5.0, 9.0)    # z

    def test_no_bbox_returns_zeros(self):
        f = SimpleNamespace(boundingBox=None)
        assert joint._face_extent(f, 2) == (0.0, 0.0)

    def test_is_planar_true_only_for_surface_type_zero(self):
        planar = SimpleNamespace(geometry=SimpleNamespace(surfaceType=0))
        cyl = SimpleNamespace(geometry=SimpleNamespace(surfaceType=3))
        assert joint._is_planar(planar) is True
        assert joint._is_planar(cyl) is False


def _Face_for_extent(mn, mx):
    pt = lambda t: SimpleNamespace(x=t[0], y=t[1], z=t[2])
    return SimpleNamespace(boundingBox=SimpleNamespace(minPoint=pt(mn), maxPoint=pt(mx)))


# ── create handler: end-to-end logic (motion dispatch, axis field, offset/angle scaling) ──
# The create handler had no handler-level test; pin the validation gates and the computed output
# fields (axis null for non-axis types, offset/angle ValueInput scaling, joint_type echo).

class _CreateJointInput:
    def __init__(self):
        self.called = None
        self.offset = None
        self.angle = None
        self.isFlipped = False
    def setAsRigidJointMotion(self):
        self.called = ("rigid",); return True
    def setAsRevoluteJointMotion(self, ax, *rest):
        self.called = ("revolute", ax) + rest; return True
    def setAsSliderJointMotion(self, ax, *rest):
        self.called = ("slider", ax) + rest; return True
    def setAsCylindricalJointMotion(self, ax, *rest):
        self.called = ("cylindrical", ax) + rest; return True
    def setAsPlanarJointMotion(self, ax, *rest):
        self.called = ("planar", ax) + rest; return True
    def setAsBallJointMotion(self, a, b):
        self.called = ("ball", a, b); return True


class _CreateJoints:
    def __init__(self):
        self.last_input = None
        self.added = None
    def createInput(self, a, b):
        self.last_input = _CreateJointInput(); return self.last_input
    def add(self, ji):
        self.added = ji
        return SimpleNamespace(name="Joint1", jointMotion=None)


def _install_create(monkeypatch, jo_names=("JO_A", "JO_B")):
    import adsk.fusion, adsk.core
    jos = {n: SimpleNamespace(name=n) for n in jo_names}
    joints_coll = _CreateJoints()
    root = SimpleNamespace(name="Root", jointOrigins=_JOCollection(jos), joints=joints_coll,
                           allOccurrences=[], allComponents=[])

    class FakeDesign:
        def __init__(self):
            self.rootComponent = root
            self.allComponents = []
        def findEntityByToken(self, h):
            return []
    d = FakeDesign()
    # Dual-seam: the JO-name inputs resolve through the JointOriginRef kind (joint._inputs._common).
    monkeypatch.setattr(joint._common, "design", lambda: d)
    monkeypatch.setattr(joint._inputs._common, "design", lambda: d)
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal", staticmethod(lambda v: ("real", v)))
    return d, joints_coll


def _payload2(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestCreateHandler:
    def test_requires_both_inputs(self, monkeypatch):
        _install_create(monkeypatch)
        res1 = joint.handler(occurrence_one="JO_A")
        assert res1["isError"] is True
        assert "Provide 'occurrence_one' and 'occurrence_two'" in res1["message"]
        res2 = joint.handler(occurrence_two="JO_B")
        assert res2["isError"] is True
        assert "Provide 'occurrence_one' and 'occurrence_two'" in res2["message"]

    def test_unknown_joint_type_errors(self, monkeypatch):
        _install_create(monkeypatch)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B", joint_type="weld")
        assert res["isError"] is True and "Unknown joint_type" in res["message"]

    def test_unknown_axis_errors(self, monkeypatch):
        _install_create(monkeypatch)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                            joint_type="revolute", axis="q")
        assert res["isError"] is True and "Unknown axis" in res["message"]

    def test_unknown_units_errors(self, monkeypatch):
        _install_create(monkeypatch)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B", units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_revolute_dispatches_axis_and_echoes_axis_field(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="revolute", axis="y"))
        assert coll.last_input.called == ("revolute", 1)   # YAxisJointDirection == 1
        assert out["joint_type"] == "revolute" and out["axis"] == "y"

    def test_rigid_has_null_axis_field(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="rigid"))
        assert coll.last_input.called == ("rigid",)
        assert out["axis"] is None                         # rigid needs no axis

    def test_ball_has_null_axis_field(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="ball"))
        assert coll.last_input.called[0] == "ball"
        assert out["axis"] is None

    def test_ball_uses_valid_pitch_and_yaw_directions(self, monkeypatch):
        # Live API fact this pins: setAsBallJointMotion(pitchDirection, yawDirection) REJECTS XAxis
        # as the pitch direction ("Invalid parameter pitchDirection") - it requires
        # pitch=ZAxisJointDirection and yaw=XAxisJointDirection, and a mock accepts any args, so
        # only pinning the enums here catches a wrong pair before a live document does.
        import adsk.fusion
        JD = adsk.fusion.JointDirections
        _, coll = _install_create(monkeypatch)
        joint.handler(occurrence_one="JO_A", occurrence_two="JO_B", joint_type="ball")
        kind, pitch, yaw = coll.last_input.called
        assert kind == "ball"
        assert pitch == JD.ZAxisJointDirection, "pitchDirection must be ZAxisJointDirection (not X)"
        assert yaw == JD.XAxisJointDirection, "yawDirection must be XAxisJointDirection"

    def test_offset_scaled_to_cm_on_value_input(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="rigid", offset=10, units="mm"))
        # 10 mm -> 1.0 cm passed to ValueInput.createByReal
        assert coll.last_input.offset == ("real", 1.0)
        assert out["offset"] == 10

    def test_offset_inch_scaling(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                joint_type="rigid", offset=2, units="in"))
        assert abs(coll.last_input.offset[1] - 5.08) < 1e-9   # 2 in -> 5.08 cm

    def test_angle_converted_to_radians(self, monkeypatch):
        import math
        _, coll = _install_create(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="revolute", angle=90))
        assert abs(coll.last_input.angle[1] - math.radians(90)) < 1e-9
        assert out["angle_deg"] == 90

    def test_flip_sets_is_flipped(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="rigid", flip=True))
        assert coll.last_input.isFlipped is True and out["flipped"] is True

    def test_no_offset_angle_reported_as_none(self, monkeypatch):
        _install_create(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="rigid"))
        assert out["offset"] is None and out["angle_deg"] is None

    def test_add_failure_on_input_paths_hints_the_proxy_fix(self, monkeypatch):
        # Fusion's "Provided input paths for joint are not valid" = an input not in assembly
        # context. The error must carry the remedy (pass JOs by name so the tool proxies them),
        # not just echo Fusion's opaque message.
        _, coll = _install_create(monkeypatch)
        def boom(ji):
            raise RuntimeError("3 : Provided input paths for joint are not valid.")
        coll.add = boom
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True
        assert "assembly context" in res["message"]
        assert "<occurrence>:<JO name>" in res["message"]

    def test_add_failure_other_errors_unadorned(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        def boom(ji):
            raise RuntimeError("5 : something else entirely")
        coll.add = boom
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True
        assert "assembly context" not in res["message"]
