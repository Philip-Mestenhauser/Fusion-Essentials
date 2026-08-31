# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared joint substrate: the JointGeometry keypoint factory (planar/cylinder/cone face, edge,
vertex/point), the motion-type dispatcher (frame-relative axis, or a custom direction entity for a
true world axis or a geometry's own axis), and the Joint/AsBuiltJoint-by-name lookup every joint tool
that edits, drives, or links an existing joint resolves through.
"""

import adsk.core
import adsk.fusion

from . import _common
from . import _geom
from ._common import safe


def is_joint_origin(x):
    """isinstance(x, adsk.fusion.JointOrigin) that degrades to False when the type isn't a real class
    (an un-modelled Mock attribute under test) instead of raising - the one JO type check every tool
    that branches on 'is this resolved handle a JointOrigin?' shares."""
    try:
        return isinstance(x, adsk.fusion.JointOrigin)
    except TypeError:
        return False


def is_as_built_joint(x):
    """isinstance(x, adsk.fusion.AsBuiltJoint), degrading to False when the type isn't a real class
    (a Mock under test). apply_motion routes an as-built joint through a DIFFERENT setter arity."""
    try:
        return isinstance(x, adsk.fusion.AsBuiltJoint)
    except TypeError:
        return False

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it. The
# mechanism behind a clause lives at the symbol itself, in its test, or in VERIFIED_API_FACTS.md.
MAP_BLURB = (
    "build_joint_geometry - the keypoint factory per entity kind; apply_motion - the motion-type "
    "dispatch, frame-relative or a custom direction entity; all_joints - the full joint walk "
    "(joints AND asBuiltJoints, root and every sub-component) the health rollups count broken "
    "joints over; find_joints_by_name / find_joint - the list form over those same scopes and the "
    "resolve-one over it, which REFUSES a name SEVERAL joints carry, since a joint name is only "
    "component-locally unique; motion_link_partner - a joint's own MotionLink membership -> "
    "linked-partner name, which joint_drive's second-member refusal gates on; all_joint_origins - "
    "the ONE JointOrigin walk the collect-names / read-axes / resolve-one leaf ops sit on, with "
    "find_joint_origins_by_name the resolve-one over it and jo_assembly_proxy the same JO in "
    "ASSEMBLY CONTEXT, which a native sub-component JO must become before a joint accepts it; "
    "component_world_matrix - the ONE matrix-to-world "
    "ladder every axis lift and world-frame read resolves through, answering None where several "
    "placements would each give a different frame; motion_param_names/OFFSET_PARAM_NOTE - the "
    "joint's own offset/angle dNN read and the one offset-is-frame-Z wire sentence every joint "
    "payload appends; pending_position/pending_move_guard/PENDING_MOVE_REFUSAL - the ONE "
    "moved-but-uncaptured position read and the refusal every joint CREATE returns while it is "
    "set, since the create's recompute silently reverts the uncaptured pose; planar_outward_normal/"
    "normals_oppose/FLIP_HINT - the flush face-to-face detection (two planar faces whose outward "
    "normals OPPOSE) and the hint every joint create that can seat two faces publishes")


def planar_outward_normal(entity):
    """Outward unit normal of a PLANAR face (the shared evaluator sample), else None - the input to
    the flush face-to-face detection: two planar faces whose outward normals OPPOSE."""
    try:
        if not isinstance(entity, adsk.fusion.BRepFace):
            return None
    except TypeError:               # the type is unavailable - nothing can be a face then
        return None
    if safe(lambda: entity.geometry.surfaceType) != adsk.core.SurfaceTypes.PlaneSurfaceType:
        return None
    return _geom.evaluator_normal_at(entity, safe(lambda: entity.pointOnFace))


def normals_oppose(n1, n2):
    """True when two unit normals point at each other (dot below -0.9) - the flush face-to-face
    pick. False when either is absent."""
    return (n1 is not None and n2 is not None
            and (n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]) < -0.9)


# The ONE flip-hint sentence every joint create publishes for the flush face-to-face pick without
# flip (live-verified: a joint aligns the two geometry frames Z-onto-Z - each planar face's frame Z
# is its OUTWARD normal - so opposing normals rotate the free part 180 deg, typically embedding it).
FLIP_HINT = ("The two planar faces' outward normals OPPOSE (the flush face-to-face pick). A joint "
             "aligns the two geometry frames Z-onto-Z, so the free part was ROTATED 180 deg to "
             "satisfy that - typically embedding it. For the seated flush mate, re-run with "
             "flip=true (or joint_edit flip).")


def motion_param_names(joint):
    """The joint's OWN ModelParameter names: {'offset': dNN, 'angle': dNN}, absent ones omitted.
    Joint.offset moves the anchor along the joint frame's TERTIARY (Z) axis (the API's own docstring;
    live-verified) and is the ONLY parametric position drive a joint has; a slider's slide VALUE has
    no ModelParameter at all, even after joint_drive poses it (live-verified). OFFSET_PARAM_NOTE
    carries both to the caller."""
    out = {}
    for key in ("offset", "angle"):
        nm = safe(lambda k=key: getattr(joint, k).name)
        if nm:
            out[key] = nm
    return out


# The one wire sentence appended wherever a payload carries model_parameters (single shared home -
# three tools return the block; the teaching must not fork).
OFFSET_PARAM_NOTE = (
    " model_parameters are the joint's own dNN params: param_set 'offset' to an expression for a "
    "PARAMETRIC position - it ALWAYS moves along the joint FRAME'S Z axis, not the motion axis, and "
    "neither 'flip' (which does not invert its sign) nor 'world_axis' redirects it. A slider's slide "
    "VALUE has no parameter (joint_drive poses it; driven poses do not survive recompute), so "
    "parametric TRAVEL comes from co-driving the geometry the joint anchors on - there is no slide "
    "parameter to set.")


# The design-wide moved-but-uncaptured position flag, and the refusal a joint CREATE returns while it
# is set. Home for both: every joint-creation tool and assembly_capture_position read the same flag,
# and a second copy is how one of them keeps creating through a pending move after the other stops.

def pending_position(design):
    """Whether the design carries a moved-but-uncaptured occurrence position
    (Design.snapshots.hasPendingSnapshot) as True / False / None - None when the flag cannot be read
    at all (a design exposing no snapshots surface), which is NOT evidence either way.

    A free move (assembly_move) and a joint_drive pose both set this same flag; assembly_capture_position
    records the pose into the timeline, discards it, or reports the flag. A design_add_instance
    PLACEMENT does not set it (measured: the flag still reads false after a placed instance, and a
    capture there refuses with "Nothing to capture")."""
    return _common.read_flag(lambda: design.snapshots.hasPendingSnapshot)


PENDING_MOVE_REFUSAL = (
    "Uncaptured occurrence moves exist and this joint creation would silently revert them - "
    "assembly_capture_position(action='capture') first to record the current pose into the timeline, "
    "or assembly_capture_position(action='discard_pending') to throw the move away deliberately. The "
    "flag is design-wide, so it does not name the moved occurrences; "
    "assembly_capture_position(action='status') reports it and lists the captured markers.")


def pending_move_guard(design):
    """The refusal a joint CREATE returns while an uncaptured move is pending, else None.

    Creating a joint recomputes the assembly, and a recompute REVERTS an uncaptured position - the
    parts snap back to their last captured (or joint-defined) pose and the new joint freezes THAT
    pose, not the one the caller placed. Refusing beats creating a joint at a position the caller
    never asked for. Only a flag that reads True refuses: an unreadable flag (None) is not evidence
    a move is pending, so it never blocks the create.

    Two measured facts bound what this refuses, and both are what keeps an automated
    move-then-joint sequence from deadlocking on it: driving a joint BACK to 0 clears the flag
    (joint_drive to 30 sets it, joint_drive to 0 clears it), so a sequence that restores its drives
    before creating a joint never meets this guard; and a design_add_instance placement never sets
    the flag at all, so placing instances then jointing them is likewise unaffected."""
    return _common.error(PENDING_MOVE_REFUSAL) if pending_position(design) is True else None

# axis keyword -> JointDirections axis index (Custom=3 is not indexed here - it is selected by
# passing a custom_entity to apply_motion instead).
AXES = {"x": 0, "y": 1, "z": 2}


def _non_planar_face_geometry(entity, keypoint):
    """createByNonPlanarFace(entity, keypoint) as (geometry, error_or_None). The API's OWN raise text
    is carried into the error rather than swallowed: it names the keypoint a face type demands
    ("Key point type should be CenterKeyPoint, if the face is sphere and torus face"), which a flat
    "createByNonPlanarFace failed" would hide from the caller."""
    try:
        g = adsk.fusion.JointGeometry.createByNonPlanarFace(entity, keypoint)
    except Exception as e:
        return None, f"createByNonPlanarFace failed: {e}"
    return g, None if g else "createByNonPlanarFace failed"


# Two keypoints agreeing to this in cm are the same point - the trap below misses by whole
# centimetres, so the band only absorbs float noise.
_KEYPOINT_TOL_CM = 1e-4


def _xyz(pt):
    """(x, y, z) off a Point3D, or None when any component is unreadable."""
    if pt is None:
        return None
    vals = (safe(lambda: pt.x), safe(lambda: pt.y), safe(lambda: pt.z))
    return None if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in vals) else vals


def _fmt_point(xyz):
    """A published coordinate - the caller labels the FRAME it is in. Rounded to 4dp (a micron in
    cm) so a float artefact never reads as a real offset."""
    return "(%.4f, %.4f, %.4f)" % xyz


def _occurrence_chain(occ):
    """`occ` and each of its assembly ancestors, innermost first - the path a proxy is reached
    through. A top-level occurrence's assemblyContext reads None (measured), which ends the walk;
    the depth cap is a cycle guard, not a real assembly limit."""
    out = []
    while occ is not None and len(out) < 64:
        out.append(occ)
        occ = safe(lambda o=occ: o.assemblyContext)
    return out


def component_world_matrix(design, comp, context_occ=None):
    """The Matrix3D taking `comp`'s OWN coordinate frame into WORLD, or None when no single
    placement answers for it. The ONE matrix-to-world ladder in this module.

    The resolution ladder is ``jo_assembly_proxy``'s, answering with a matrix instead of a proxy:
    the ROOT component's frame IS world (identity); a component placed ONCE is carried by that
    occurrence's transform2; a component placed SEVERAL times has no one world frame.

    transform2 is the ONLY matrix read. ``transform`` is the LOCAL one and composes no parent, so on
    a nested occurrence it names a different frame - falling back to it would answer with a matrix
    this function's own contract calls unknowable, under a caller that reads None as "make no
    judgement". An unreadable transform2 is therefore None, like any other unresolved placement.

    `context_occ` is the occurrence the caller reached the geometry through: when `comp` is placed by
    it or by one of its assembly ancestors, THAT instance's transform is the answer, so a
    multi-placed component still resolves for the instance actually being measured. With no context
    and several placements this returns None - the caller then refuses, or makes no judgement,
    rather than picking an instance whose rotation may differ from the one in hand."""
    root = safe(lambda: design.rootComponent)
    if comp is None or root is None:
        return None
    # `is True` on both: an identity comparison that did not read cannot mint a frame. The identity
    # matrix is the claim "this component's frame IS world" and an occurrence's transform2 is the
    # claim "THIS instance carries it" - an unproven match falls through to the placement ladder and,
    # failing that, to the None this function's callers read as "make no judgement".
    if _common.same_component(comp, root) is True:
        return safe(lambda: adsk.core.Matrix3D.create())
    for o in _occurrence_chain(context_occ):
        if _common.same_component(safe(lambda o=o: o.component), comp) is True:
            return safe(lambda o=o: o.transform2)
    occs = list(safe(lambda: root.allOccurrencesByComponent(comp)) or [])
    if len(occs) == 1:
        return safe(lambda: occs[0].transform2)
    return None


def _world_placement(entity):
    """The Matrix3D taking `entity`'s owning component's frame into WORLD, or None when no single
    placement answers for it - the module's one matrix-to-world ladder,
    ``component_world_matrix``, over the entity's owner and the occurrence it was reached through.

    An entity reached through an assembly proxy names its instance in assemblyContext; a NATIVE one
    carries no context, and its owning component's own placement answers. None means the caller
    makes NO judgement rather than a wrong one."""
    return component_world_matrix(_common.design(),
                                  safe(lambda: entity.body.parentComponent),
                                  safe(lambda: entity.assemblyContext))


def _world_torus_centre(entity):
    """The torus face's own centre in WORLD coordinates, or None when it cannot be established.

    Which frame ``geometry.origin`` answers in follows assemblyContext, MEASURED on a torus centred
    at component-local (0, 0, -1) in a component turned 30 deg about Z and placed 8 cm out: the
    NATIVE face reads (0, 0, -1) and needs the lift through its owning component's one placement,
    while the face reached through the assembly PROXY reads (8, 0, -1) - already world, and lifting
    it a second time lands (14.9282, 4.0, -1.0), a point on no part of the model."""
    origin = safe(lambda: entity.geometry.origin)
    if origin is None:
        return None
    if safe(lambda: entity.assemblyContext) is not None:
        return _xyz(origin)
    m = _world_placement(entity)
    if m is None:
        return None
    moved = safe(lambda: origin.copy())
    if moved is None or not safe(lambda: moved.transformBy(m)):
        return None
    return _xyz(moved)


def _torus_keypoint_error(g, entity):
    """Error text when a TORUS CenterKeyPoint does not describe the torus face, else None.

    Measured rule for createByNonPlanarFace(torus_face, CenterKeyPoint), across three rigs:
      - a PARAMETRIC torus returns the true centre, world-framed, from a native face or a proxy;
      - a torus inside a BASE FEATURE returns the OWNING COMPONENT'S ORIGIN, world-framed, whatever
        the torus centre is - (0,0,0) for a root-component body, the child's world origin for a
        placed one. Nothing raises, so the returned origin is the only signal there is.
    The component origin is right only when the torus happens to be centred on it.

    So the discriminating comparison is the keypoint against the torus's own centre read in the
    SAME world frame. A world-origin signature alone would catch only root-component bodies and pass
    a placed one's plausible-but-wrong point silently; comparing against the raw component-LOCAL
    centre would false-refuse every placed assembly. Either side unestablished -> no judgement."""
    kp = _xyz(safe(lambda: g.origin))
    centre = _world_torus_centre(entity)
    if kp is None or centre is None:
        return None
    if max(abs(a - b) for a, b in zip(kp, centre)) <= _KEYPOINT_TOL_CM:
        return None
    return (f"This torus face's joint keypoint came back as {_fmt_point(kp)} cm in WORLD space, but "
            f"the torus face is centred at {_fmt_point(centre)} cm in WORLD space - the keypoint "
            "does not describe the face. A torus face inside a BASE FEATURE returns its owning "
            "COMPONENT'S ORIGIN from this call with no error, so the joint would be anchored there "
            "instead. Pick a circular EDGE or a planar face on this body, or rebuild the torus "
            "parametrically (model_revolve).")


def build_joint_geometry(entity, edge_keypoint=None):
    """Build a JointGeometry for a face/edge/vertex/point entity, picking the keypoint the API accepts
    for that entity's kind. CenterKeyPoint is INVALID on a cylinder/cone face - MiddleKeyPoint is used
    there instead - while a SPHERE or TORUS face accepts ONLY CenterKeyPoint; a circular edge centers,
    a straight edge uses its midpoint. edge_keypoint overrides the automatic edge pick with an explicit
    JointKeyPointTypes value (a start/middle/end/center choice an anchor tool offers its caller).
    Returns (geometry, label, error_or_None)."""
    JG = adsk.fusion.JointGeometry
    KP = adsk.fusion.JointKeyPointTypes
    if isinstance(entity, adsk.fusion.BRepFace):
        st = safe(lambda: entity.geometry.surfaceType)
        if st == adsk.core.SurfaceTypes.PlaneSurfaceType:
            g = safe(lambda: JG.createByPlanarFace(entity, None, KP.CenterKeyPoint))
            return g, "planar_face@center", None if g else "createByPlanarFace failed"
        if st in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
            g, err = _non_planar_face_geometry(entity, KP.MiddleKeyPoint)
            return g, "cylinder_face@middle", err
        # A sphere or torus face takes ONLY CenterKeyPoint, both measured on live faces:
        # MiddleKeyPoint raises "Key point type should be CenterKeyPoint, if the face is sphere and
        # torus face", CenterKeyPoint returns a JointGeometry at the face's centre.
        centre_only = {adsk.core.SurfaceTypes.SphereSurfaceType: "sphere_face@center",
                       adsk.core.SurfaceTypes.TorusSurfaceType: "torus_face@center"}
        if st in centre_only:
            g, err = _non_planar_face_geometry(entity, KP.CenterKeyPoint)
            if err is None and st == adsk.core.SurfaceTypes.TorusSurfaceType:
                err = _torus_keypoint_error(g, entity)
                if err:
                    g = None
            return g, centre_only[st], err
        g, err = _non_planar_face_geometry(entity, KP.MiddleKeyPoint)
        return g, "nonplanar_face@middle", err
    if isinstance(entity, adsk.fusion.BRepEdge):
        if edge_keypoint is not None:
            kp = edge_keypoint
        else:
            ct = safe(lambda: entity.geometry.curveType)
            kp = KP.CenterKeyPoint if ct == adsk.core.Curve3DTypes.Circle3DCurveType else KP.MiddleKeyPoint
        g = safe(lambda: JG.createByCurve(entity, kp))
        return g, "edge", None if g else "createByCurve failed for this edge"
    if isinstance(entity, adsk.fusion.SketchPoint):
        g = safe(lambda: JG.createByPoint(entity))
        return g, "sketch_point", None if g else "createByPoint failed"
    if isinstance(entity, (adsk.fusion.BRepVertex, adsk.fusion.ConstructionPoint)):
        g = safe(lambda: JG.createByPoint(entity))
        return g, "point", None if g else "createByPoint failed"
    return None, None, f"entity kind {type(entity).__name__} is not a supported joint geometry"


def apply_motion(ji, jtype, axis_idx, custom_entity=None, slide_axis_idx=None):
    """Set rigid/revolute/slider/cylindrical/planar/ball/pin_slot motion on a JointInput (or an
    existing Joint being redefined). axis_idx (0/1/2 = x/y/z) selects the FRAME-relative axis unless
    custom_entity is given, in which case JointDirections.CustomJointDirection pairs with that entity
    for a TRUE direction instead of the joint geometry's local frame - either a world construction axis
    (an explicit world-axis override) or a cylinder/cone face's or circular edge's own axis (deriving
    the motion axis from the geometry itself).

    pin_slot alone takes TWO frame-relative directions: axis_idx is the ROTATION axis and slide_axis_idx
    (0/1/2, default = the next frame axis so it is guaranteed distinct) is the perpendicular SLIDE
    direction; the two must differ. custom_entity, when given with pin_slot, re-points the ROTATION axis
    to a true direction while the slide stays frame-relative. Returns (did, error_or_None)."""
    JD = adsk.fusion.JointDirections
    dirs = [JD.XAxisJointDirection, JD.YAxisJointDirection, JD.ZAxisJointDirection]
    if custom_entity is not None:
        ax = JD.CustomJointDirection
    else:
        ax = dirs[axis_idx]
    # An EXISTING as-built joint being redefined takes a DIFFERENT setter arity than a JointInput: the
    # motion setters carry an extra JointGeometry arg (setAsSliderJointMotion(direction, geometry
    # [, customEntity]) - live-verified via sys_get_api_doc), and a rigid as-built joint carries NO
    # geometry ("Geometry should not be null if joint motion is not rigid" - live-verified), so it
    # cannot be converted to any motion type. Route it here rather than let the JointInput calls below
    # misfile the custom entity as the geometry ("wrong number or type of arguments" overload error).
    if jtype != "rigid" and is_as_built_joint(ji):
        geom = safe(lambda: ji.geometry)
        if geom is None:
            return False, (f"this is a rigid AS-BUILT joint with no joint geometry, so the API "
                           f"cannot redefine it as a '{jtype}' joint (it has no anchor to move "
                           "along). Delete it and build the motion joint with joint_create_as_built "
                           "(same two occurrences, plus the 'geometry' the motion anchors on), "
                           "joint_create (a ':origin' snap) or joint_at_geometry (a real face/edge).")
        setter = {"revolute": "setAsRevoluteJointMotion", "slider": "setAsSliderJointMotion",
                  "cylindrical": "setAsCylindricalJointMotion",
                  "planar": "setAsPlanarJointMotion"}.get(jtype)
        if setter is None:
            return False, (f"redefining an as-built joint as '{jtype}' is not supported here - "
                           "delete it and use joint_create.")
        try:
            fn = getattr(ji, setter)
            if custom_entity is not None:
                return bool(fn(ax, geom, custom_entity)), None
            return bool(fn(ax, geom)), None
        except Exception as e:
            return False, str(e)
    try:
        if jtype == "rigid":
            return bool(ji.setAsRigidJointMotion()), None
        if jtype == "pin_slot":
            # setAsPinSlotJointMotion(rotationAxis, slideDirection[, customRotationAxisEntity,
            # customSlideDirectionEntity]). Rotation = ax (custom or frame); slide = a distinct frame
            # axis. Passing custom_entity positionally fills customRotationAxisEntity (pairs with
            # ax == CustomJointDirection); the slide direction stays frame-relative.
            s_idx = slide_axis_idx if slide_axis_idx is not None else (axis_idx + 1) % 3
            if s_idx == axis_idx:
                return False, "pin_slot rotation axis and slide direction must differ."
            slide_dir = dirs[s_idx]
            if custom_entity is not None:
                return bool(ji.setAsPinSlotJointMotion(ax, slide_dir, custom_entity)), None
            return bool(ji.setAsPinSlotJointMotion(ax, slide_dir)), None
        if jtype == "revolute":
            if custom_entity is not None:
                return bool(ji.setAsRevoluteJointMotion(ax, custom_entity)), None
            return bool(ji.setAsRevoluteJointMotion(ax)), None
        if jtype == "slider":
            if custom_entity is not None:
                return bool(ji.setAsSliderJointMotion(ax, custom_entity)), None
            return bool(ji.setAsSliderJointMotion(ax)), None
        if jtype == "cylindrical":
            if custom_entity is not None:
                return bool(ji.setAsCylindricalJointMotion(ax, custom_entity)), None
            return bool(ji.setAsCylindricalJointMotion(ax)), None
        if jtype == "planar":
            if custom_entity is not None:
                return bool(ji.setAsPlanarJointMotion(ax, custom_entity)), None
            return bool(ji.setAsPlanarJointMotion(ax)), None
        if jtype == "ball":
            # pitch MUST be Z, yaw MUST be X (not the intuitive X/Y) - the API rejects any other pair
            # with "Invalid parameter pitchDirection".
            return bool(ji.setAsBallJointMotion(JD.ZAxisJointDirection, JD.XAxisJointDirection)), None
    except Exception as e:
        return False, str(e)
    return False, f"unsupported joint_type '{jtype}'"


_MOTION_CLASS_TO_TYPE = {
"RigidJointMotion": "rigid", "RevoluteJointMotion": "revolute",
"SliderJointMotion": "slider", "CylindricalJointMotion": "cylindrical",
"PlanarJointMotion": "planar", "BallJointMotion": "ball",
"PinSlotJointMotion": "pin_slot",
}


def current_joint_type(joint):
    """Map a joint's current JointMotion subclass to the create/edit joint_type keyword (or '' if the
    motion is absent or unrecognized)."""
    jm = safe(lambda: joint.jointMotion)
    return _MOTION_CLASS_TO_TYPE.get(type(jm).__name__, "") if jm else ""


# JointMotion subclass -> the single JointMotionTypes DOF a MotionLink.setMotionData couples. This is
# the DEGREE OF FREEDOM enum (RevoluteJointRotateMotionType, ...), a DIFFERENT enum from JointTypes:
# jointMotion.jointType returns a JointTypes value (RevoluteJointType == 1), which setMotionData
# REJECTS as "BAD_JOINT_DOF - Motion Link joint DOF is wrong type" - verified live, along with the
# accepted DOF values below. Cylindrical exposes both a rotate and a slide DOF (both accepted live);
# rotation is the gear/belt coupling default. Rigid (no DOF) and the multi-DOF ball/planar/pin_slot
# joints have no single DOF this tool can pick unambiguously, so they map to None.
def motion_link_dof(joint):
    """The JointMotionTypes DOF that MotionLink.setMotionData couples for `joint`, as (value, None), or
    (None, reason) when the joint has no single linkable rotate/slide DOF (rigid, or a multi-DOF
    ball/planar/pin_slot). setMotionData wants this DOF, NOT the joint's JointTypes value."""
    JMT = adsk.fusion.JointMotionTypes
    table = {
        "RevoluteJointMotion": JMT.RevoluteJointRotateMotionType,
        "SliderJointMotion": JMT.SliderJointSlideMotionType,
        "CylindricalJointMotion": JMT.CylindricalJointRotateMotionType,
    }
    jm = safe(lambda: joint.jointMotion)
    cls = type(jm).__name__ if jm else ""
    if cls in table:
        return table[cls], None
    kw = _MOTION_CLASS_TO_TYPE.get(cls, "") or "unknown"
    if kw == "rigid":
        return None, "is a rigid joint (no motion to link)"
    return None, (f"is a '{kw}' joint - motion links couple single-DOF joints "
                  "(revolute, slider, or cylindrical)")


def all_joints(design):
    """Every Joint AND AsBuiltJoint in the design, as a flat list of the joint objects: the root
    component plus every sub-component (both are SEPARATE collections, and a joint internal to a
    sub-component lives on that component - a root-only walk under-reports, so a broken sub-component or
    as-built joint would be invisible to a health rollup). The ONE joint walk: find_joint resolves a
    name over it, and the assembly_get / workspace_orient health rollups count broken joints over it,
    so 'which joints exist' is answered the same way everywhere. Joints are de-duplicated by
    entityToken: design.allComponents includes the root as a proxy DISTINCT from
    design.rootComponent, so the root's joints are reached twice - counting them once each would
    over-report joint_count and repeat a broken joint in the health rollup."""
    out, seen = [], set()
    scopes = [safe(lambda: design.rootComponent)] + list(safe(lambda: design.allComponents, []) or [])
    for c in scopes:
        if c is None:
            continue
        for coll_name in ("joints", "asBuiltJoints"):
            jc = safe(lambda c=c, cn=coll_name: getattr(c, cn))
            for i in range(safe(lambda: jc.count, 0) or 0 if jc else 0):
                j = safe(lambda i=i: jc.item(i))
                if j is None:
                    continue
                # entityToken is stable across the two root proxies. A SUPPRESSED joint's token can
                # read None (it degrades toward a bare feature), and an id() fallback then splits
                # the two root proxies into two records (measured: joint_count 2 for ONE suppressed
                # joint) - so the fallback key is (name, objectType, owning component), which the
                # two proxies of one joint share; id() remains only for a joint with no readable name.
                token = safe(lambda j=j: j.entityToken)
                if token is not None:
                    key = ("tok", token)
                else:
                    nm = safe(lambda j=j: j.name)
                    key = (("nm", nm, safe(lambda j=j: j.objectType),
                            safe(lambda j=j: j.parentComponent.name))
                           if nm else ("id", id(j)))
                if key in seen:
                    continue
                seen.add(key)
                out.append(j)
    return out


def find_joints_by_name(design, name):
    """Every Joint or AsBuiltJoint whose name EXACTLY matches `name`, over all_joints - a LIST,
    because a joint name is only component-locally unique (two sub-assemblies can each hold a
    'Revolute1'). The caller decides: one hit resolves, several REFUSE with candidates (the house
    rule for a non-unique name space); never grab the first. The same shape as
    find_joint_origins_by_name, over the same walk the health rollups count."""
    want = (name or "").strip()
    if not want:
        return []
    return [j for j in all_joints(design) if (safe(lambda j=j: j.name) or "") == want]


def find_joint(design, name):
    """Resolve ONE Joint or AsBuiltJoint by name over all_joints - the walk that reaches joints AND
    asBuiltJoints on the root component and every sub-component. Returns (joint, error_or_None).

    A name carried by SEVERAL joints is REFUSED, naming each hit's owning component: the name space
    is component-local, so picking one of them targets an arbitrary assembly's joint. A name no
    joint carries is (None, None) - the caller words its own not-found error, each pointing at the
    listing read it already names."""
    hits = find_joints_by_name(design, name)
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, None
    want = (name or "").strip()
    where = ", ".join(
        f"'{want}' in {safe(lambda j=j: j.parentComponent.name) or '(unreadable component)'}"
        for j in hits[:8])
    return None, (f"'{want}' names {len(hits)} joints ({where}) - joint names are only unique within "
                  "a component. Rename one in Fusion so the name resolves to a single joint "
                  "(assembly_get lists every joint in the design).")


def motion_link_partner(joint):
    """The name of the joint motion-linked to `joint`, or None when it is in no link. Read off the
    joint's OWN membership (Joint/AsBuiltJoint.motionLinks - 'the MotionLink objects that this joint
    is involved in'), so a pair linked inside an xref'd sub-assembly is seen through the same joint
    find_joint resolved - no component walk. Joint.motionLinks returns a MotionLinkVector - a plain
    SEQUENCE (len/index/iterate; it has NO .count/.item, so a collection-style read finds nothing,
    verified live) - unlike Component.motionLinks which is a MotionLinks collection. The partner is
    whichever of MotionLink.jointOne/jointTwo is not this joint (jointTwo is null for a same-joint
    two-DOF link - no partner to report)."""
    my_name = safe(lambda: joint.name)
    if not my_name:
        return None
    for ml in safe(lambda: list(joint.motionLinks), []) or []:
        if ml is None:
            continue
        one = safe(lambda: ml.jointOne.name)
        two = safe(lambda: ml.jointTwo.name)
        if one == my_name and two:
            return two
        if two == my_name and one:
            return one
    return None


def all_joint_origins(design):
    """Every JointOrigin in the design as a flat list of (jo, owning_component): the root component plus
    every sub-component (a JO internal to a sub-component lives on that component, so a root-only walk
    under-reports). The ONE JointOrigin walk - the same shape as all_joints - that the three JO leaf ops
    share: collect-names (joint_create's available-JO list), read-axes (model_inspect's oriented bbox
    frame), and resolve-one-by-name (find_joint_origins_by_name, under the JointOriginRef kind). Joints
    know 'which joints exist' one way; this answers 'which joint origins exist' the same way everywhere.
    De-duplicated by entityToken for the reason all_joints records, which here would double-list a root
    JO (the token is stable across the two root proxies, verified live for all_joints; id() falls back
    for fakes)."""
    out, seen = [], set()
    scopes = [safe(lambda: design.rootComponent)] + list(safe(lambda: design.allComponents, []) or [])
    for c in scopes:
        if c is None:
            continue
        jos = safe(lambda c=c: c.jointOrigins)
        for i in range(safe(lambda: jos.count, 0) or 0 if jos else 0):
            jo = safe(lambda i=i: jos.item(i))
            if jo is None:
                continue
            token = safe(lambda jo=jo: jo.entityToken)
            key = token if token is not None else id(jo)
            if key in seen:
                continue
            seen.add(key)
            out.append((jo, c))
    return out


def find_joint_origins_by_name(design, name):
    """Every (jo, owning_component) whose JointOrigin name EXACTLY matches `name`, over all_joint_origins
    - a LIST, because a JO name is only component-locally unique (two sub-assemblies can each carry a
    'Center of Model'). The caller decides: one hit resolves, several REFUSE with candidates (the house
    rule for a non-unique name space); never grab the first."""
    want = (name or "").strip()
    if not want:
        return []
    return [(jo, c) for jo, c in all_joint_origins(design)
            if (safe(lambda jo=jo: jo.name) or "") == want]


def jo_assembly_proxy(design, jo, comp):
    """Return `jo` usable in ASSEMBLY CONTEXT: the native JO when it's on the root component (already in
    context), else its proxy in the SINGLE occurrence of its owning component (a native sub-component JO
    yields Fusion's 'Provided input paths for joint are not valid' - it must be proxied). Returns
    (obj, error): an owning component instanced MORE THAN ONCE is ambiguous which instance carries the
    frame, so it refuses and names the '<occurrence>:<JO name>' form that picks one."""
    root = safe(lambda: design.rootComponent)
    # `is True`: only a PROVEN root JO is handed back native (the form Fusion refuses anywhere else).
    # An unproven owner takes the placement walk below, which ends on the same native when nothing
    # places the component - so the unknown costs one lookup and claims nothing.
    if _common.same_component(comp, root) is True:
        return jo, None
    occs = list(safe(lambda: root.allOccurrencesByComponent(comp)) or []) if root else []
    if len(occs) == 1:
        proxy = safe(lambda: jo.createForAssemblyContext(occs[0]))
        if proxy is None:
            # The native is the object the line above says Fusion refuses, so falling back to it
            # hands the joint the input that yields 'Provided input paths for joint are not valid'.
            nm = safe(lambda: jo.name) or "?"
            path = safe(lambda: occs[0].fullPathName) or safe(lambda: occs[0].name) or "its one occurrence"
            return None, (f"Joint Origin '{nm}' could not be read in the assembly's space ({path}), "
                          "so where that frame sits in the model is unknown. Pass its handle from "
                          "assembly_get(include=['joint_origins']).")
        return proxy, None
    if not occs:
        return jo, None                       # not instanced in the assembly; native is the only form
    nm = safe(lambda: jo.name) or "?"
    return None, (f"Joint Origin '{nm}' is instanced {len(occs)} times - address it as "
                  f"'<occurrence>:{nm}' to pick which instance (assembly_get(include=['joint_origins']) "
                  "lists the qualified names).")


def jo_reference_names(design, jo, comp):
    """The resolvable reference string(s) for a JointOrigin: its BARE name when it's on the root
    component (unique there), else '<occurrence fullPathName>:<name>' for EACH occurrence of its owning
    component. A JointOriginRef / joint tool accepts any of these; the qualified form is what
    disambiguates a name shared across components or instanced several times. Shared by the
    assembly_get JO slice (its qualified_name field) and the JointOriginRef ambiguity candidate list."""
    nm = safe(lambda: jo.name) or "?"
    root = safe(lambda: design.rootComponent)
    # `is True`: an owner proven to be the root is reachable by the bare name. An unproven one takes
    # the occurrence walk, which prints the qualified spellings that exist and falls back to the bare
    # name when none do - so no reference string is offered on an identity that did not read.
    if _common.same_component(comp, root) is True:
        return [nm]
    occs = list(safe(lambda: root.allOccurrencesByComponent(comp)) or []) if root else []
    out = [f"{safe(lambda o=o: o.fullPathName)}:{nm}" for o in occs if safe(lambda o=o: o.fullPathName)]
    return out or [nm]
