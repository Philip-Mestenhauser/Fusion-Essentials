"""Unit tests for ``joint_create_edit.py`` pure logic.

Targets: ``_available_joint_origins`` (the collect-names leaf over the shared _joints JO walk),
``_resolve_input`` (handle / snap / JO-name dispatch, now routing the JO-name path through the
JointOriginRef kind), and ``_apply_motion`` (dispatch by joint type, incl. the unsupported-type
fallthrough). Resolve-one/qualified/ambiguity of a JO by name is the JointOriginRef kind's contract -
tested in test_inputs.py, not here.
"""

import json
from types import SimpleNamespace

import adsk.core

from conftest import load_tool, _NamedCollection

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
        # find_joint answers (joint, ambiguity_error_or_None) - a name several joints share refuses.
        monkeypatch.setattr(joint, "_find_joint", lambda design, name: (SimpleNamespace(name="J"), None))
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
        changed, unverified, err = joint._apply_limits(m, min_deg=-45, max_deg=90)
        assert err is None and unverified == []
        assert m.rotationLimits.isMinimumValueEnabled and m.rotationLimits.isMaximumValueEnabled
        assert abs(m.rotationLimits.minimumValue - _math.radians(-45)) < 1e-9
        assert abs(m.rotationLimits.maximumValue - _math.radians(90)) < 1e-9
        assert changed["min_deg"] == -45 and changed["max_deg"] == 90

    def test_linear_in_cm(self):
        m = _SlideMotion()
        changed, unverified, err = joint._apply_limits(m, min_mm=0, max_mm=300, cm_scale=0.1)
        assert err is None and unverified == []
        assert abs(m.slideLimits.maximumValue - 30.0) < 1e-9   # 300 mm -> 30 cm
        assert changed["max_mm"] == 300

    def test_rest_values(self):
        m = _RevMotion()
        joint._apply_limits(m, rest_deg=10)
        assert m.rotationLimits.isRestValueEnabled
        assert abs(m.rotationLimits.restValue - _math.radians(10)) < 1e-9

    def test_rotation_on_slider_errors(self):
        m = _SlideMotion()
        changed, unverified, err = joint._apply_limits(m, min_deg=10)
        assert err is not None and "rotation" in err.lower()

    def test_linear_on_revolute_errors(self):
        m = _RevMotion()
        changed, unverified, err = joint._apply_limits(m, max_mm=100)
        assert err is not None and ("slide" in err.lower() or "linear" in err.lower())

    def test_inverted_rotation_pair_is_refused_before_any_write(self):
        # min > max is an EMPTY feasible range that silently makes the joint undrivable while every
        # health field reads healthy - refused, and nothing is enabled on the motion.
        m = _RevMotion()
        changed, unverified, err = joint._apply_limits(m, min_deg=60, max_deg=-60)
        assert err is not None and "INVERTED" in err
        assert changed == {} and m.rotationLimits.isMinimumValueEnabled is False

    def test_inverted_slide_pair_is_refused_before_any_write(self):
        m = _SlideMotion()
        changed, unverified, err = joint._apply_limits(m, min_mm=50, max_mm=10, cm_scale=0.1)
        assert err is not None and "INVERTED" in err
        assert changed == {} and m.slideLimits.isMaximumValueEnabled is False


class _DeafLim(_Lim):
    """A JointLimits that ACCEPTS every assignment and keeps its own value (and optionally its own
    enabled flag) - the platform shape a request-echoing payload cannot tell from a landed write."""
    def __init__(self, held_value=0.0, hold_flag=None):
        object.__setattr__(self, "_held", held_value)
        object.__setattr__(self, "_hold_flag", hold_flag)
        super().__init__()

    def __setattr__(self, name, value):
        if name in ("minimumValue", "maximumValue", "restValue"):
            return object.__setattr__(self, name, object.__getattribute__(self, "_held"))
        held_flag = object.__getattribute__(self, "_hold_flag")
        if name.startswith("is") and held_flag is not None:
            return object.__setattr__(self, name, held_flag)
        return object.__setattr__(self, name, value)


class _BlindLim(_Lim):
    """A JointLimits whose value read RAISES after the assignment - the unreadable re-read."""
    def __init__(self, blind_flag=False):
        object.__setattr__(self, "_blind_flag", blind_flag)
        super().__init__()

    def __getattribute__(self, name):
        if name in ("minimumValue", "maximumValue", "restValue"):
            raise RuntimeError("limit value unreadable")
        if (name.startswith("is") and name.endswith("Enabled")
                and object.__getattribute__(self, "_blind_flag")):
            raise RuntimeError("limit flag unreadable")
        return object.__getattribute__(self, name)


class _BandLim(_Lim):
    """A JointLimits that lands every value OFF by a fixed amount, given in the caller's own units
    (degrees) so a test can sit either side of the read-back band."""
    def __init__(self, deg_error):
        object.__setattr__(self, "_deg_error", deg_error)
        super().__init__()

    def __setattr__(self, name, value):
        if name in ("minimumValue", "maximumValue", "restValue") and value is not None:
            off = object.__getattribute__(self, "_deg_error")
            return object.__setattr__(self, name, value + _math.radians(off))
        return object.__setattr__(self, name, value)


class TestLimitsAreReadBack:
    """Every limit and its enabled flag is published as the JOINT READS IT BACK, never as the
    request: a JointLimits assignment that the platform keeps at its own value leaves a payload
    built from the request claiming a limit that is not in force."""

    def test_a_landed_limit_publishes_the_read_back_value(self):
        m = _RevMotion()
        changed, unverified, err = joint._apply_limits(m, min_deg=-45)
        assert err is None and unverified == []
        assert changed["min_deg"] == -45.0

    def test_the_published_number_is_the_read_back_not_the_request(self):
        # the joint lands the limit slightly off (inside the band, so no refusal): the payload must
        # carry what the JOINT holds, or a caller reading it back learns nothing it did not send
        m = _RevMotion()
        m.rotationLimits = _BandLim(joint._LIMIT_BAND * 0.5)
        changed, unverified, err = joint._apply_limits(m, min_deg=-45)
        assert err is None
        assert changed["min_deg"] == -44.9995        # the read-back, not the requested -45

    def test_a_rotation_limit_that_did_not_take_errors_naming_it(self):
        m = _RevMotion()
        m.rotationLimits = _DeafLim(held_value=0.0)
        changed, unverified, err = joint._apply_limits(m, min_deg=-45)
        assert err is not None
        assert "min_deg did not take" in err
        assert "-45" in err and "0.0" in err          # requested vs what the joint reads back
        assert changed == {}                          # the failed limit is not published as applied

    def test_a_slide_limit_that_did_not_take_errors_naming_it(self):
        m = _SlideMotion()
        m.slideLimits = _DeafLim(held_value=0.0)
        changed, unverified, err = joint._apply_limits(m, max_mm=300, cm_scale=0.1)
        assert err is not None and "max_mm did not take" in err
        assert "300" in err

    def test_an_enabled_flag_that_reads_back_false_errors(self):
        # the value can land while the joint keeps the limit DISABLED - then it constrains nothing
        m = _RevMotion()
        m.rotationLimits = _DeafLim(held_value=_math.radians(-45), hold_flag=False)
        changed, unverified, err = joint._apply_limits(m, min_deg=-45)
        assert err is not None
        assert "min_deg did not take" in err and "isMinimumValueEnabled" in err

    def test_earlier_limits_that_landed_are_kept_in_changed(self):
        # the partial-success disclosure both handlers build reads `changed` - a limit that landed
        # before the failing one must still be named there
        m = _RevMotion()
        changed, unverified, err = joint._apply_limits(m, min_deg=-45, max_deg=90, rest_deg=10)
        assert err is None and set(changed) == {"min_deg", "max_deg", "rest_deg"}
        m2 = _RevMotion()
        m2.rotationLimits = _DeafLim(held_value=_math.radians(-45))
        changed2, _unv, err2 = joint._apply_limits(m2, min_deg=-45, max_deg=90)
        assert err2 is not None and "max_deg did not take" in err2
        assert changed2 == {"min_deg": -45.0}         # the one that landed, read back

    def test_an_unreadable_read_back_publishes_null_and_a_marker(self):
        # the value read RAISES: publishing the request would report a write nobody confirmed
        m = _RevMotion()
        m.rotationLimits = _BlindLim()
        changed, unverified, err = joint._apply_limits(m, min_deg=-45)
        assert err is None
        assert changed == {"min_deg": None} and unverified == ["min_deg"]

    def test_an_unreadable_enabled_flag_also_publishes_null(self):
        # an unreadable FLAG is not a False - it is unknown, so the limit is unverified, not failed
        m = _SlideMotion()
        m.slideLimits = _BlindLim(blind_flag=True)
        changed, unverified, err = joint._apply_limits(m, min_mm=5, cm_scale=0.1)
        assert err is None
        assert changed == {"min_mm": None} and unverified == ["min_mm"]


class TestLimitReadBackBand:
    """The read-back is compared in the REQUEST'S own units (a limit round-trips deg -> rad -> deg),
    within _LIMIT_BAND. Both sides of that edge are pinned - a units round trip must not error, a
    real miss must not slip under the band - and the edge itself: a difference EQUAL to the band is
    inside it (the comparison is `>`, not `>=`)."""

    def _apply(self, deg_error, wanted=-45):
        m = _RevMotion()
        m.rotationLimits = _BandLim(deg_error)
        return joint._apply_limits(m, min_deg=wanted)

    def test_a_difference_inside_the_band_lands(self):
        changed, unverified, err = self._apply(joint._LIMIT_BAND * 0.5)
        assert err is None and unverified == []

    def test_a_difference_exactly_at_the_band_lands(self):
        # min_deg=0 is the one request whose round trip lands the difference EXACTLY on the band
        # (at -45 the float error puts it just under), so this is what separates `>` from `>=`.
        changed, unverified, err = self._apply(joint._LIMIT_BAND, wanted=0)
        assert err is None, "a difference equal to the band is inside it"
        assert abs(changed["min_deg"] - 0.0) == joint._LIMIT_BAND, "the boundary case itself moved"

    def test_a_difference_beyond_the_band_errors(self):
        changed, unverified, err = self._apply(joint._LIMIT_BAND * 2.0)
        assert err is not None and "min_deg did not take" in err

    def test_the_band_is_small_enough_to_catch_a_real_miss(self):
        # a limit that landed a whole degree off is a wrong limit, not round-trip noise
        assert self._apply(1.0)[2] is not None


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
            # allComponents lives on the DESIGN, is a COUNTED collection (count/item) and CARRIES the
            # root - the live shape _common.all_components reads, and the walk the shared JO walk
            # sits on. A bare list models neither half.
            self.allComponents = _NamedCollection([self.rootComponent])

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
        design.allComponents = _NamedCollection([root, sub])
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
        planar = SimpleNamespace(geometry=SimpleNamespace(
            surfaceType=adsk.core.SurfaceTypes.PlaneSurfaceType))
        cyl = SimpleNamespace(geometry=SimpleNamespace(
            surfaceType=adsk.core.SurfaceTypes.CylinderSurfaceType))
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
    # No allComponents on the COMPONENT: that collection is a Design property in the live API.
    root = SimpleNamespace(name="Root", jointOrigins=_JOCollection(jos), joints=joints_coll,
                           allOccurrences=[])

    class FakeDesign:
        def __init__(self):
            self.rootComponent = root
            # counted, and carrying the root - the collection _common.all_components walks.
            self.allComponents = _NamedCollection([root])
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


class TestCreatedJointHealth:
    """A joint can be ADDED yet fail to COMPUTE. joints.add() hands back a truthy Joint while Fusion
    marks it 'Compute Failed' and assembly_get counts it under broken_joints - measured on a joint to
    the child of a ground_to_parent occurrence, which moved NOTHING and still published created:true.
    The create reads the state back, so a broken joint is a refusal, never a plain success."""

    def _with_health(self, monkeypatch, joint_health=None, timeline_health=None, msg=""):
        _d, joints_coll = _install_create(monkeypatch)
        made = SimpleNamespace(name="Joint1", jointMotion=None, errorOrWarningMessage=msg)
        if joint_health is not None:
            made.healthState = joint_health
        if timeline_health is not None:
            made.timelineObject = SimpleNamespace(healthState=timeline_health,
                                                  errorOrWarningMessage=msg)
        joints_coll.add = lambda ji, m=made: m
        return joints_coll

    def test_a_joint_that_computed_BROKEN_is_refused_not_created_true(self, monkeypatch):
        # The measured shape: healthState reads WARNING (Fusion's 'Compute Failed'), so the create
        # refuses instead of publishing created:true.
        self._with_health(monkeypatch, joint_health=1,
                          msg="Can't resolve some component positions because there are conflicts "
                              "with assembly relationships in the design.Compute FailedJoint1")
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True
        assert "FAILED to compute" in res["message"]
        assert "conflicts with assembly relationships" in res["message"]
        assert "Compute Failed" not in res["message"]     # the repeating blob is condensed
        assert "design_delete_feature" in res["message"]  # the joint REMAINS - say how to remove it
        assert "ground_to_parent" in res["message"]       # and name the measured cause

    def test_an_ERROR_health_state_is_refused_too(self, monkeypatch):
        self._with_health(monkeypatch, joint_health=2, msg="over-constrained")
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True
        assert "FAILED to compute" in res["message"]

    def test_a_failure_visible_only_on_the_TIMELINE_item_is_still_caught(self, monkeypatch):
        # The measured failure showed on BOTH the joint and its timeline item; a build where only
        # the timeline item carries it must not slip through.
        self._with_health(monkeypatch, joint_health=0, timeline_health=2, msg="broken")
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True
        assert "FAILED to compute" in res["message"]

    def test_a_HEALTHY_joint_is_created_and_says_so(self, monkeypatch):
        self._with_health(monkeypatch, joint_health=0)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B"))
        assert out["created"] is True
        assert out["healthy"] is True

    def test_an_UNREADABLE_health_state_is_null_not_a_coerced_healthy(self, monkeypatch):
        # Neither the joint nor a timeline item answers healthState: whether it solved is UNKNOWN.
        # A True here would be a clean bill of health nobody read.
        self._with_health(monkeypatch)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B"))
        assert out["created"] is True
        assert out["healthy"] is None
        assert "'healthy' is null" in out["note"]


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


class TestAxisIsAdvertisedFrameRelative:
    """'axis' names an axis of the JOINT GEOMETRY's frame, not a world axis (measured: a snap whose
    local Z points along world Y pivots about world Y). Both tools take that same frame-relative
    axis, so both must say so: a description that reads as if it were a world direction sends a
    caller to rebuild the joint instead of re-pointing it with joint_edit(world_axis=...)."""

    def _axis_property(self, t):
        return t.to_dict()["inputSchema"]["properties"]["axis"]["description"]

    def test_create_axis_input_says_frame_relative(self):
        assert "FRAME-relative" in self._axis_property(joint.tool)

    def test_edit_axis_input_says_the_same_thing(self):
        assert "FRAME-relative" in self._axis_property(joint.edit_tool)

    def test_create_description_names_the_frame_and_the_fix(self):
        desc = joint.TOOL_DESCRIPTION
        assert "FRAME-relative" in desc
        assert "joint_edit(world_axis=" in desc      # the tool that re-points it


_UNREADABLE_FLAG = object()


class _BlindSnapshots:
    """Design.snapshots whose pending flag RAISES - the unknown-flag case, not a False."""
    @property
    def hasPendingSnapshot(self):
        raise RuntimeError("pending flag unreadable")


class TestPendingMoveRefusal:
    """A joint CREATE recomputes the assembly, and a recompute REVERTS an uncaptured occurrence
    position: the parts snap back and the new joint freezes the reverted pose. So a create while
    Design.snapshots.hasPendingSnapshot is set is refused, naming capture / discard_pending. An
    UNREADABLE flag is not evidence a move is pending and must not block the create."""

    def _install(self, monkeypatch, pending):
        d, coll = _install_create(monkeypatch)
        if pending is _UNREADABLE_FLAG:
            d.snapshots = _BlindSnapshots()
        else:
            d.snapshots = SimpleNamespace(hasPendingSnapshot=pending)
        return d, coll

    def test_refuses_the_create_while_a_move_is_pending(self, monkeypatch):
        _, coll = self._install(monkeypatch, True)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True
        assert "would silently revert" in res["message"]
        assert coll.added is None                       # nothing was created

    def test_the_refusal_names_both_remedies(self, monkeypatch):
        self._install(monkeypatch, True)
        msg = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")["message"]
        assert "assembly_capture_position(action='capture')" in msg
        assert "action='discard_pending'" in msg

    def test_creates_normally_with_nothing_pending(self, monkeypatch):
        _, coll = self._install(monkeypatch, False)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B"))
        assert out["created"] is True and coll.added is not None

    def test_an_unreadable_flag_does_not_refuse(self, monkeypatch):
        _, coll = self._install(monkeypatch, _UNREADABLE_FLAG)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B"))
        assert out["created"] is True and coll.added is not None


class TestSlideValueHasNoParameter:
    """A slider's slide VALUE carries no ModelParameter (measured twice in anger), so the shared
    offset note has to say both halves: there is no slide parameter to set, and parametric TRAVEL
    comes from co-driving the anchor geometry - otherwise the next caller re-derives it by failing."""

    def test_the_offset_note_states_there_is_no_slide_parameter(self):
        note = joint._OFFSET_PARAM_NOTE
        assert "slide" in note and "no parameter" in note

    def test_the_offset_note_names_the_parametric_travel_route(self):
        assert "co-driving the geometry" in joint._OFFSET_PARAM_NOTE

    def test_a_created_joint_carrying_params_appends_the_note(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        coll.add = lambda ji: SimpleNamespace(
            name="Slider1", jointMotion=None,
            offset=SimpleNamespace(name="d12"), angle=SimpleNamespace(name="d13"))
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="slider"))
        assert out["model_parameters"] == {"offset": "d12", "angle": "d13"}
        assert "co-driving the geometry" in out["note"]


class TestCreateFlipHint:
    """Parity with joint_at_geometry: a create that seats two OPPOSING planar faces without flip
    rotates the free part 180 deg - the payload carries the one shared FLIP_HINT."""

    def _wire(self, monkeypatch, n1, n2):
        _, coll = _install_create(monkeypatch)
        face1, face2 = object(), object()
        monkeypatch.setattr(joint, "_resolve_input",
                            lambda d, spec: (SimpleNamespace(name=spec,
                                                             entityOne=face1 if spec == "JO_A"
                                                             else face2),
                                             spec, None))
        normals = {face1: n1, face2: n2}
        monkeypatch.setattr(joint._joints, "planar_outward_normal",
                            lambda e: normals.get(e))
        return coll

    def test_opposing_faces_without_flip_carry_the_hint(self, monkeypatch):
        self._wire(monkeypatch, (0.0, 0.0, 1.0), (0.0, 0.0, -1.0))
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B"))
        assert "OPPOSE" in out["flip_hint"] and "flip=true" in out["flip_hint"]

    def test_flip_true_suppresses_the_hint(self, monkeypatch):
        self._wire(monkeypatch, (0.0, 0.0, 1.0), (0.0, 0.0, -1.0))
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B", flip=True))
        assert "flip_hint" not in out

    def test_aligned_faces_carry_no_hint(self, monkeypatch):
        self._wire(monkeypatch, (0.0, 0.0, 1.0), (0.0, 0.0, 1.0))
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B"))
        assert "flip_hint" not in out

    def test_an_unreadable_normal_carries_no_hint(self, monkeypatch):
        self._wire(monkeypatch, None, (0.0, 0.0, -1.0))
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B"))
        assert "flip_hint" not in out


class TestCreatePublishesLimitsItRead:
    """joint_create's limit fields are the joint's own read-back or they are null - never the
    request. A payload built from the request reports a limit that may never have been applied."""

    def _with_motion(self, monkeypatch, limits):
        _d, coll = _install_create(monkeypatch)
        motion = SimpleNamespace(rotationLimits=limits, slideLimits=None)
        coll.add = lambda ji: SimpleNamespace(name="Joint1", jointMotion=motion)
        return coll

    def test_a_landed_limit_is_published_from_the_joint(self, monkeypatch):
        self._with_motion(monkeypatch, _BandLim(joint._LIMIT_BAND * 0.5))
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="revolute", min_deg=-45))
        assert out["created"] is True
        assert out["min_deg"] == -44.9995        # what the joint holds, not the -45 requested
        assert "limits_unverified" not in out

    def test_a_limit_that_did_not_take_refuses_the_create_naming_it(self, monkeypatch):
        self._with_motion(monkeypatch, _DeafLim(held_value=0.0))
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                            joint_type="revolute", min_deg=-45)
        assert res["isError"] is True
        assert "min_deg did not take" in res["message"]
        # the joint EXISTS - the partial-success disclosure has to say so and name the removal path
        assert "WAS CREATED" in res["message"] and "design_delete_feature" in res["message"]

    def test_an_unreadable_limit_publishes_null_and_the_marker(self, monkeypatch):
        self._with_motion(monkeypatch, _BlindLim())
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="revolute", min_deg=-45, max_deg=90))
        assert out["min_deg"] is None and out["max_deg"] is None
        assert out["limits_unverified"] == ["min_deg", "max_deg"]
        assert "Limits published null" in out["note"] and "not a 'yes'" in out["note"]


class TestSuppressedEditDisclosure:
    def _rig(self, monkeypatch, suppressed):
        j = SimpleNamespace(name="J", isFlipped=False, isSuppressed=suppressed,
                            motionLinks=[], jointMotion=None, timelineObject=None)
        design = SimpleNamespace(computeAll=lambda: None, timeline=None)
        monkeypatch.setattr(joint._common, "design", lambda: design)
        monkeypatch.setattr(joint, "_find_joint", lambda d, n: (j, None))
        return j

    def test_suppressed_joint_edit_is_disclosed_as_inert(self, monkeypatch):
        # The edit is real (the write lands) but a suppressed joint positions nothing (measured:
        # the part sat 47mm from its jointed placement with no mention) - disclosed, not silent.
        j = self._rig(monkeypatch, suppressed=True)
        out = _payload(joint.edit_handler(joint_name="J", flip=True))
        assert j.isFlipped is True                       # the write itself landed
        assert out["suppressed"] is True and "INERT" in out["note"]

    def test_active_joint_edit_carries_no_suppression_note(self, monkeypatch):
        self._rig(monkeypatch, suppressed=False)
        out = _payload(joint.edit_handler(joint_name="J", flip=True))
        assert "suppressed" not in out and "INERT" not in out["note"]


# ── _find_occurrence: the shared OccurrenceRef resolver, not a hand-rolled name match ────────────

class TestFindOccurrence:
    def test_delegates_to_the_shared_occurrence_resolver(self, monkeypatch):
        # The name is passed as BOTH the field label and the raw spec, so the resolver's refusal
        # names the input the caller actually typed. A local name walk here would resolve an
        # ambiguous 'Bolt:1' to the first instance instead of refusing it.
        seen = []
        monkeypatch.setattr(joint._inputs, "_resolve_occurrence",
                            lambda name, raw: seen.append((name, raw)) or ("OCC", None))
        assert joint._find_occurrence(SimpleNamespace(), "Boom:1") == ("OCC", None)
        assert seen == [("Boom:1", "Boom:1")]


class TestAvailableJointOriginsSkipsUnnamed:
    def test_a_jo_with_no_readable_name_is_left_out_of_the_listing(self, monkeypatch):
        # A blank entry would render as "'' (root)" and teach the agent a name that resolves nothing.
        design = _install_resolve_seam(monkeypatch, {})
        design.rootComponent.jointOrigins = _IterableJOs(
            {"named": SimpleNamespace(name="Center of Model"), "blank": SimpleNamespace(name="")})
        listed, more = joint._available_joint_origins(design)
        assert listed == ["'Center of Model' (root)"] and more == 0


# ── _resolve_snap_entity: an occurrence's geometry -> one proxied BRep entity ─────────────────────

def _face_bag(items, count=None):
    """A faces collection: the .count the guard reads plus the iteration the pickers walk."""
    n = len(items) if count is None else count
    return type("_FaceBag", (), {"count": n, "__iter__": lambda self: iter(items)})()


def _snap_face(surface_type, *, area=1.0, proxy=None):
    """One face: its surface type, its area, and what proxying yields. Surface types come from the
    MEASURED enum (adsk.core.SurfaceTypes) - Cylinder is 1 and 3 is Sphere, so a fake built on a
    hand-typed 3 describes a sphere and lets a cylinder-picker that never matched look correct."""
    return SimpleNamespace(geometry=SimpleNamespace(surfaceType=surface_type), area=area,
                           createForAssemblyContext=lambda occ, _p=proxy: _p)


def _snap_occurrence(*, origin_point=None, has_body=True, faces=None):
    """An occurrence exposing exactly what _resolve_snap_entity reads off it."""
    body = SimpleNamespace(faces=faces) if has_body else None
    comp = SimpleNamespace(originConstructionPoint=origin_point,
                           bRepBodies=SimpleNamespace(item=lambda i, _b=body: _b))
    return SimpleNamespace(component=comp)


class TestResolveSnapEntity:
    def _wire(self, monkeypatch, occ, err=None):
        monkeypatch.setattr(joint, "_find_occurrence", lambda d, n: (occ, err))

    def test_an_unresolved_occurrence_error_is_passed_through(self, monkeypatch):
        self._wire(monkeypatch, None, "no occurrence 'Boom:1'")
        assert joint._resolve_snap_entity(None, "Boom:1", "top") == (None, None, "no occurrence 'Boom:1'")

    def test_origin_snap_returns_the_assembly_context_proxy(self, monkeypatch):
        op = SimpleNamespace(createForAssemblyContext=lambda occ: "PROXY_PT")
        self._wire(monkeypatch, _snap_occurrence(origin_point=op))
        assert joint._resolve_snap_entity(None, "Boom:1", "origin") == ("PROXY_PT", "point", None)

    def test_origin_snap_falls_back_to_the_native_point(self, monkeypatch):
        # A root-component point has no proxy to make; the native entity is the usable form.
        op = SimpleNamespace(createForAssemblyContext=lambda occ: None)
        self._wire(monkeypatch, _snap_occurrence(origin_point=op))
        ent, kind, err = joint._resolve_snap_entity(None, "Boom:1", "origin")
        assert ent is op and kind == "point" and err is None

    def test_origin_snap_without_an_origin_point_names_the_occurrence(self, monkeypatch):
        self._wire(monkeypatch, _snap_occurrence(origin_point=None))
        ent, kind, err = joint._resolve_snap_entity(None, "Boom:1", "origin")
        assert ent is None and err == "'Boom:1' has no origin construction point."

    def test_an_occurrence_with_no_body_names_the_occurrence(self, monkeypatch):
        self._wire(monkeypatch, _snap_occurrence(has_body=False))
        ent, kind, err = joint._resolve_snap_entity(None, "Boom:1", "top")
        assert ent is None and err == "'Boom:1' has no body to snap to."

    def test_a_body_with_zero_faces_is_refused(self, monkeypatch):
        self._wire(monkeypatch, _snap_occurrence(faces=_face_bag([], count=0)))
        ent, kind, err = joint._resolve_snap_entity(None, "Boom:1", "top")
        assert ent is None and err == "'Boom:1' body has no faces."

    def test_cylinder_snap_picks_the_cylindrical_face_and_proxies_it(self, monkeypatch):
        st = adsk.core.SurfaceTypes
        self._wire(monkeypatch, _snap_occurrence(faces=_face_bag([
            _snap_face(st.PlaneSurfaceType, proxy="FLAT"),
            _snap_face(st.CylinderSurfaceType, proxy="CYL")])))
        assert joint._resolve_snap_entity(None, "Boom:1", "cylinder") == ("CYL", "cylinder", None)

    def test_cylinder_snap_takes_a_cone_too(self, monkeypatch):
        # a tapered pin is round and seats the same way - the pair joint_at_geometry accepts.
        st = adsk.core.SurfaceTypes
        self._wire(monkeypatch, _snap_occurrence(faces=_face_bag([
            _snap_face(st.PlaneSurfaceType, proxy="FLAT"),
            _snap_face(st.ConeSurfaceType, proxy="CONE")])))
        assert joint._resolve_snap_entity(None, "Boom:1", "cylinder") == ("CONE", "cylinder", None)

    def test_cylinder_snap_does_not_match_a_sphere(self, monkeypatch):
        # SphereSurfaceType is 3; a picker hand-typed against that integer matches spheres and
        # reports every real cylinder as having no cylindrical face.
        st = adsk.core.SurfaceTypes
        self._wire(monkeypatch, _snap_occurrence(faces=_face_bag([
            _snap_face(st.SphereSurfaceType, proxy="BALL")])))
        ent, kind, err = joint._resolve_snap_entity(None, "Boom:1", "cylinder")
        assert ent is None and err == "'Boom:1' has no cylindrical face to snap to."

    def test_cylinder_snap_on_a_body_with_no_cylinder_is_refused(self, monkeypatch):
        self._wire(monkeypatch, _snap_occurrence(
            faces=_face_bag([_snap_face(adsk.core.SurfaceTypes.PlaneSurfaceType)])))
        ent, kind, err = joint._resolve_snap_entity(None, "Boom:1", "cylinder")
        assert ent is None and err == "'Boom:1' has no cylindrical face to snap to."

    def test_a_planar_snap_returns_the_picked_face_proxied(self, monkeypatch):
        self._wire(monkeypatch, _snap_occurrence(faces=_face_bag(
            [_snap_face(0, area=1.0, proxy="SMALL"), _snap_face(0, area=9.0, proxy="BIG")])))
        # 'center' = the largest planar face
        assert joint._resolve_snap_entity(None, "Boom:1", "center") == ("BIG", "planar", None)

    def test_a_planar_snap_with_no_planar_face_names_the_snap(self, monkeypatch):
        self._wire(monkeypatch, _snap_occurrence(faces=_face_bag([_snap_face(3)])))
        ent, kind, err = joint._resolve_snap_entity(None, "Boom:1", "top")
        assert ent is None and err == "Could not pick a 'top' face on 'Boom:1'."


class TestResolveSnapInput:
    def test_builds_a_joint_geometry_from_the_snapped_entity(self, monkeypatch):
        import adsk.core, adsk.fusion
        Face, _, _ = _jg_seam(monkeypatch)
        face = Face(adsk.core.SurfaceTypes.PlaneSurfaceType)
        monkeypatch.setattr(joint, "_resolve_snap_entity", lambda d, n, s: (face, "planar", None))
        g, err = joint._resolve_snap_input(None, "Boom:1", "top")
        assert err is None and g == ("planar", adsk.fusion.JointKeyPointTypes.CenterKeyPoint)

    def test_an_entity_failure_is_passed_through_untouched(self, monkeypatch):
        monkeypatch.setattr(joint, "_resolve_snap_entity", lambda d, n, s: (None, None, "no body"))
        assert joint._resolve_snap_input(None, "Boom:1", "top") == (None, "no body")

    def test_a_geometry_build_failure_is_reported_not_swallowed(self, monkeypatch):
        _jg_seam(monkeypatch)
        monkeypatch.setattr(joint, "_resolve_snap_entity", lambda d, n, s: (object(), "planar", None))
        g, err = joint._resolve_snap_input(None, "Boom:1", "top")
        assert g is None and "not a supported joint geometry" in err


class TestResolveInputSnapPath:
    def test_a_snap_spec_is_labelled_occurrence_and_snap(self, monkeypatch):
        design = _install_resolve_seam(monkeypatch, {})
        monkeypatch.setattr(joint, "_resolve_snap_input", lambda d, occ, snap: ("G", None))
        assert joint._resolve_input(design, "Boom:1:top") == ("G", "Boom:1:top", None)

    def test_a_snap_failure_keeps_the_snap_label_and_its_error(self, monkeypatch):
        # The snap path OWNS the spec once it parses - it must not fall through to the JO-name
        # form-guide, which would hide "has no body to snap to" behind "not a Joint Origin".
        design = _install_resolve_seam(monkeypatch, {})
        monkeypatch.setattr(joint, "_resolve_snap_input", lambda d, occ, snap: (None, "no body"))
        assert joint._resolve_input(design, "Boom:1:cylinder") == (None, "Boom:1:cylinder", "no body")


class TestResolveInputAmbiguousJointOrigin:
    def test_an_ambiguous_jo_name_is_surfaced_verbatim(self, monkeypatch):
        # Two components carrying the same JO name: the kind's refusal (with the qualified
        # candidates) is what the caller needs - not the generic "not a handle/JO/snap" guide.
        design = _install_resolve_seam(monkeypatch, {})
        shared = "Center of Model"
        root = SimpleNamespace(name="Root",
                               jointOrigins=_IterableJOs({shared: SimpleNamespace(name=shared)}))
        sub = SimpleNamespace(name="Tower",
                              jointOrigins=_IterableJOs({shared: SimpleNamespace(name=shared)}))
        design.rootComponent = root
        design.allComponents = _NamedCollection([root, sub])
        g, label, err = joint._resolve_input(design, shared)
        assert g is None and label == shared
        assert "ambiguous" in err and "2 Joint Origins share that name" in err
        assert "is not a find_geometry handle" not in err


# ── create handler: the failure paths that must report, never return a false ok ───────────────────

def _raise_refusal(*_a, **_k):
    """A platform call that refuses - injected where the handler must report, not swallow."""
    raise RuntimeError("7 : the platform refused")


class TestCreateHandlerFailurePaths:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(joint._common, "design", lambda: None)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_an_unresolvable_first_input_carries_the_resolver_error(self, monkeypatch):
        _install_create(monkeypatch)
        res = joint.handler(occurrence_one="Nope", occurrence_two="JO_B")
        assert res["isError"] is True and "'Nope' is not a find_geometry handle" in res["message"]

    def test_an_unresolvable_second_input_carries_the_resolver_error(self, monkeypatch):
        _install_create(monkeypatch)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="Nope")
        assert res["isError"] is True and "'Nope' is not a find_geometry handle" in res["message"]

    def test_a_silent_first_input_failure_still_names_the_input(self, monkeypatch):
        # A resolver that declines without a reason must not produce a bare/blank refusal.
        _install_create(monkeypatch)
        monkeypatch.setattr(joint, "_resolve_input", lambda d, spec: (None, spec, None))
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert "Could not resolve joint input 'JO_A'" in res["message"]

    def test_a_silent_second_input_failure_names_the_second_input(self, monkeypatch):
        _install_create(monkeypatch)
        monkeypatch.setattr(
            joint, "_resolve_input",
            lambda d, spec: (SimpleNamespace(name=spec), spec, None) if spec == "JO_A"
            else (None, spec, None))
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert "Could not resolve joint input 'JO_B'" in res["message"]

    def test_a_raising_create_input_is_reported(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        coll.createInput = _raise_refusal
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True and "Could not create joint input" in res["message"]
        assert "the platform refused" in res["message"]

    def test_a_create_input_returning_nothing_is_reported(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        coll.createInput = lambda a, b: None
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True and "createInput returned nothing" in res["message"]

    def test_a_motion_setter_returning_false_is_not_a_success(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        coll.createInput = lambda a, b: SimpleNamespace(setAsRigidJointMotion=lambda: False)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True and "Could not set rigid motion" in res["message"]
        assert coll.added is None                      # never reached joints.add

    def test_an_offset_that_cannot_be_valued_is_reported(self, monkeypatch):
        import adsk.core
        _, coll = _install_create(monkeypatch)
        monkeypatch.setattr(adsk.core.ValueInput, "createByReal", staticmethod(_raise_refusal))
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B", offset=10)
        assert res["isError"] is True and "Could not apply offset/angle/flip" in res["message"]
        assert coll.added is None

    def test_an_add_returning_nothing_is_reported(self, monkeypatch):
        _, coll = _install_create(monkeypatch)
        coll.add = lambda ji: None
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B")
        assert res["isError"] is True and "joints.add returned nothing" in res["message"]


class TestCreateLimits:
    def _with_motion(self, monkeypatch, motion):
        _, coll = _install_create(monkeypatch)
        coll.add = lambda ji, _m=motion: SimpleNamespace(name="Pivot", jointMotion=_m)
        return coll

    def test_limits_on_a_joint_with_no_motion_are_refused(self, monkeypatch):
        _install_create(monkeypatch)                   # the default add() yields jointMotion None
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B", min_deg=-45)
        assert res["isError"] is True and "no motion to limit" in res["message"]

    def test_rotation_limits_land_on_the_new_joints_motion(self, monkeypatch):
        motion = _RevMotion()
        self._with_motion(monkeypatch, motion)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="revolute", min_deg=-45, max_deg=90))
        assert abs(motion.rotationLimits.maximumValue - _math.radians(90)) < 1e-9
        assert out["min_deg"] == -45 and out["max_deg"] == 90

    def test_a_limit_kind_the_motion_lacks_is_refused(self, monkeypatch):
        self._with_motion(monkeypatch, _RevMotion())
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                            joint_type="revolute", max_mm=100)
        assert res["isError"] is True and "LINEAR/slide limits" in res["message"]

    def test_slide_limits_scale_by_the_requested_units(self, monkeypatch):
        motion = _SlideMotion()
        self._with_motion(monkeypatch, motion)
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="slider", max_mm=2, units="in"))
        assert abs(motion.slideLimits.maximumValue - 5.08) < 1e-9    # 2 in -> 5.08 cm
        assert out["max_mm"] == 2

    def test_a_rest_limit_appends_the_it_does_not_pose_disclaimer(self, monkeypatch):
        self._with_motion(monkeypatch, _RevMotion())
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="revolute", rest_deg=10))
        assert out["rest_deg"] == 10
        assert "does NOT reposition the static model" in out["note"]
        assert "joint_drive" in out["note"]

    def test_ordinary_limits_carry_no_rest_disclaimer(self, monkeypatch):
        self._with_motion(monkeypatch, _RevMotion())
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      joint_type="revolute", max_deg=90))
        assert "does NOT reposition the static model" not in out["note"]


class TestCreateRenameDisclosure:
    def test_a_rename_that_does_not_take_is_disclosed_not_swallowed(self, monkeypatch):
        # The create succeeded, so a declined rename is a disclosure, never an error - but the
        # payload must publish the name the joint ACTUALLY holds plus the warning.
        _, coll = _install_create(monkeypatch)
        stubborn = type("_UnrenamableJoint", (),
                        {"name": property(lambda self: "Joint1", lambda self, v: None)})()
        stubborn.jointMotion = None
        coll.add = lambda ji: stubborn
        out = _payload2(joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                                      name="BoomPivot"))
        assert out["joint_name"] == "Joint1"
        assert "BoomPivot" in out["rename_warning"] and "did not take" in out["rename_warning"]


# ── edit handler: guards, rewiring order, and the failure wordings ───────────────────────────────

def _edit_joint(tl_index=None, **over):
    """A minimal editable joint: records rollTo, carries no motion/params unless overridden."""
    j = SimpleNamespace(name="J", isFlipped=False, jointMotion=None)
    j.rolls = []
    j.timelineObject = SimpleNamespace(
        index=tl_index, rollTo=lambda before: j.rolls.append(bool(before)) or True)
    for k, v in over.items():
        setattr(j, k, v)
    return j


def _joint_raising_on(attr, exc, tl_index=None):
    """A joint whose ATTR assignment raises - the shape of a platform refusal mid-edit."""
    def _setter(self, value):
        raise exc
    j = type("_RefusingJoint", (), {attr: property(lambda self: None, _setter)})()
    j.name = "J"
    j.jointMotion = None
    j.rolls = []
    j.timelineObject = SimpleNamespace(
        index=tl_index, rollTo=lambda before: j.rolls.append(bool(before)) or True)
    return j


def _edit_rig(monkeypatch, j, design=None):
    """Point BOTH design seams at one design and hand the edit handler `j` as the named joint."""
    d = design if design is not None else SimpleNamespace(computeAll=lambda: None, timeline=None)
    monkeypatch.setattr(joint._common, "design", lambda: d)
    monkeypatch.setattr(joint._inputs._common, "design", lambda: d)
    monkeypatch.setattr(joint, "_find_joint", lambda des, n: (j, None))
    return d


class TestEditGuards:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(joint._common, "design", lambda: None)
        res = joint.edit_handler(joint_name="J", flip=True)
        assert res["isError"] is True and "No active design" in res["message"]

    def test_world_axis_on_a_joint_with_no_axis_based_motion_is_refused(self, monkeypatch):
        # world_axis re-applies the CURRENT motion type; rigid/ball (and an unreadable motion)
        # have no single axis to re-point, so there is nothing to re-apply.
        j = _edit_joint()
        _edit_rig(monkeypatch, j)
        res = joint.edit_handler(joint_name="J", world_axis="z")
        assert res["isError"] is True and "not axis-based" in res["message"]
        assert j.rolls == []                           # refused before the timeline moved

    def test_an_unknown_axis_is_refused_before_the_timeline_moves(self, monkeypatch):
        j = _edit_joint()
        _edit_rig(monkeypatch, j)
        res = joint.edit_handler(joint_name="J", joint_type="revolute", axis="q")
        assert res["isError"] is True and "Unknown axis 'q'" in res["message"]
        assert j.rolls == []

    def test_pin_slot_slide_axis_equal_to_the_rotation_axis_is_refused(self, monkeypatch):
        j = _edit_joint()
        _edit_rig(monkeypatch, j)
        res = joint.edit_handler(joint_name="J", joint_type="pin_slot", axis="y", slide_axis="y")
        assert res["isError"] is True and "differ" in res["message"]
        assert j.rolls == []


class TestEditPinSlot:
    def test_pin_slot_reports_the_effective_slide_axis(self, monkeypatch):
        calls = []
        j = _edit_joint(setAsPinSlotJointMotion=lambda rot, slide, *rest:
                        calls.append((rot, slide)) or True)
        _edit_rig(monkeypatch, j)
        out = _payload(joint.edit_handler(joint_name="J", joint_type="pin_slot", axis="z"))
        assert out["joint_type"] == "pin_slot" and out["axis"] == "z"
        assert out["slide_axis"] == "x"                # default = the next frame axis
        assert calls == [(_JD.ZAxisJointDirection, _JD.XAxisJointDirection)]


class TestEditInputResolution:
    def test_a_bad_input_one_fails_before_the_timeline_moves(self, monkeypatch):
        j = _edit_joint()
        _edit_rig(monkeypatch, j)
        monkeypatch.setattr(joint, "_resolve_input",
                            lambda d, spec: (None, spec, f"no '{spec}' here"))
        res = joint.edit_handler(joint_name="J", input_one="Ghost")
        assert res["isError"] is True and "no 'Ghost' here" in res["message"]
        assert j.rolls == []

    def test_a_bad_input_two_fails_before_the_timeline_moves(self, monkeypatch):
        j = _edit_joint()
        _edit_rig(monkeypatch, j)
        monkeypatch.setattr(
            joint, "_resolve_input",
            lambda d, spec: (SimpleNamespace(name=spec), spec, None) if spec == "A"
            else (None, spec, f"no '{spec}' here"))
        res = joint.edit_handler(joint_name="J", input_one="A", input_two="Ghost")
        assert res["isError"] is True and "no 'Ghost' here" in res["message"]
        assert j.rolls == []


class TestEditRewireTimelineOrder:
    """Editing rolls the marker to just before the joint, where a LATER feature does not exist -
    the platform answers a bare findObjectPath there, so the order is checked before rolling."""

    def _rig(self, monkeypatch, jo_index, joint_index):
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion, "JointOrigin", type("JointOrigin", (), {}))
        jo = adsk.fusion.JointOrigin()
        jo.timelineObject = SimpleNamespace(index=jo_index)
        j = _edit_joint(tl_index=joint_index)
        _edit_rig(monkeypatch, j)
        monkeypatch.setattr(joint, "_resolve_input", lambda d, spec: (jo, spec, None))
        return j

    def test_a_later_joint_origin_is_refused_naming_both_positions(self, monkeypatch):
        j = self._rig(monkeypatch, jo_index=9, joint_index=4)
        res = joint.edit_handler(joint_name="J", input_one="LateJO")
        assert res["isError"] is True
        assert "position 9" in res["message"] and "position 4" in res["message"]
        assert "joint_create" in res["message"]
        assert j.rolls == []                           # refused BEFORE the timeline is rolled

    def test_a_joint_origin_at_the_joints_own_position_is_refused(self, monkeypatch):
        # Equal index is still "not yet built" at the rolled-back marker, so the guard is >=.
        self._rig(monkeypatch, jo_index=4, joint_index=4)
        res = joint.edit_handler(joint_name="J", input_one="SameSlotJO")
        assert res["isError"] is True and "position 4" in res["message"]

    def test_an_earlier_joint_origin_is_rewired_normally(self, monkeypatch):
        j = self._rig(monkeypatch, jo_index=1, joint_index=4)
        out = _payload(joint.edit_handler(joint_name="J", input_one="EarlyJO"))
        assert out["input_one"] == "EarlyJO"
        assert j.rolls[0] is True                      # the edit did roll the marker


class TestEditMotionFailure:
    def test_a_setter_returning_false_is_reported_not_claimed_as_edited(self, monkeypatch):
        j = _edit_joint(setAsRevoluteJointMotion=lambda ax, *rest: False)
        _edit_rig(monkeypatch, j)
        res = joint.edit_handler(joint_name="J", joint_type="revolute", axis="z")
        assert res["isError"] is True and "Could not set revolute motion" in res["message"]


class TestEditLimitsGuard:
    def test_limits_on_a_joint_with_no_motion_are_refused(self, monkeypatch):
        j = _edit_joint()
        _edit_rig(monkeypatch, j)
        res = joint.edit_handler(joint_name="J", min_deg=10)
        assert res["isError"] is True and "no editable motion" in res["message"]


class TestEditFailureReporting:
    def test_a_platform_refusal_is_reported_as_an_error(self, monkeypatch):
        j = _joint_raising_on("isFlipped", RuntimeError("3 : the flip was refused"))
        _edit_rig(monkeypatch, j)
        res = joint.edit_handler(joint_name="J", flip=True)
        assert res["isError"] is True
        assert "Edit failed: 3 : the flip was refused" in res["message"]
        assert "LATER in the timeline" not in res["message"]

    def test_an_object_path_failure_names_the_timeline_cause(self, monkeypatch):
        j = _joint_raising_on("geometryOrOriginOne",
                              RuntimeError("2 : InternalValidationError : findObjectPath"))
        _edit_rig(monkeypatch, j)
        monkeypatch.setattr(joint, "_resolve_input", lambda d, spec: ("G", spec, None))
        res = joint.edit_handler(joint_name="J", input_one="SomeJO")
        assert res["isError"] is True
        assert "LATER in the timeline" in res["message"] and "joint_create" in res["message"]

    def test_the_timeline_marker_is_restored_even_when_the_edit_fails(self, monkeypatch):
        j = _joint_raising_on("isFlipped", RuntimeError("refused"))
        _edit_rig(monkeypatch, j)
        joint.edit_handler(joint_name="J", flip=True)
        assert j.rolls == [True, False]                # rolled before the joint, then back


class TestEditRecomputeFailure:
    def test_a_failing_recompute_does_not_sink_the_edit(self, monkeypatch):
        j = _edit_joint()
        _edit_rig(monkeypatch, j,
                  design=SimpleNamespace(computeAll=_raise_refusal, timeline=None))
        out = _payload(joint.edit_handler(joint_name="J", flip=True))
        assert out["edited"] is True and out["flipped"] is True and j.isFlipped is True
        assert "timeline_errors_after" not in out

    def test_a_failing_recompute_is_not_reported_as_recomputed(self, monkeypatch):
        # computeAll raising means the model was NOT settled - the payload says so instead of
        # claiming a recompute that never ran.
        j = _edit_joint()
        _edit_rig(monkeypatch, j,
                  design=SimpleNamespace(computeAll=_raise_refusal, timeline=None))
        out = _payload(joint.edit_handler(joint_name="J", flip=True))
        assert out["recomputed"] is False
        assert "recompute RAISED" in out["note"] and "design_recompute" in out["note"]

    def test_a_clean_recompute_still_reads_recomputed_true(self, monkeypatch):
        j = _edit_joint()
        _edit_rig(monkeypatch, j)
        out = _payload(joint.edit_handler(joint_name="J", flip=True))
        assert out["recomputed"] is True and "full recompute" in out["note"]


class TestLimitsPartialSuccessDisclosure:
    def test_create_limit_failure_names_the_created_joint_and_the_landed_limits(self, monkeypatch):
        # The joint EXISTS and min_deg landed before max_mm failed - a bare error would invite a
        # duplicate re-create; the message names the joint, what landed, and the two ways forward.
        _, coll = _install_create(monkeypatch)
        motion = _RevMotion()
        coll.add = lambda ji, _m=motion: SimpleNamespace(name="Pivot", jointMotion=_m)
        res = joint.handler(occurrence_one="JO_A", occurrence_two="JO_B",
                            joint_type="revolute", min_deg=-45, max_mm=100)
        assert res["isError"] is True
        msg = res["content"][0]["text"]
        assert "'Pivot' WAS CREATED" in msg
        assert "min_deg=-45" in msg
        assert "do NOT re-create" in msg
        assert abs(motion.rotationLimits.minimumValue - _math.radians(-45)) < 1e-9

    def test_edit_limit_failure_names_the_edits_that_landed(self, monkeypatch):
        j = _edit_joint(jointMotion=_RevMotion())
        _edit_rig(monkeypatch, j)
        res = joint.edit_handler(joint_name="J", flip=True, min_deg=-30, max_mm=50)
        assert res["isError"] is True
        msg = res["content"][0]["text"]
        assert "Edits already applied before the failure" in msg
        assert "flipped=True" in msg and "min_deg=-30" in msg


class TestEditModelParameters:
    def test_the_joints_own_parameters_are_published_with_the_offset_note(self, monkeypatch):
        j = _edit_joint(offset=SimpleNamespace(name="d12"), angle=SimpleNamespace(name="d13"))
        _edit_rig(monkeypatch, j)
        out = _payload(joint.edit_handler(joint_name="J", flip=True))
        assert out["model_parameters"] == {"offset": "d12", "angle": "d13"}
        assert "co-driving the geometry" in out["note"]
