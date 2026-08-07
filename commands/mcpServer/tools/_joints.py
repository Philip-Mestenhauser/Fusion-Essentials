# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared joint substrate: the JointGeometry keypoint factory (planar/cylinder/cone face, edge,
vertex/point), the motion-type dispatcher (frame-relative axis, or a custom direction entity for a
true world axis or a geometry's own axis), and the Joint/AsBuiltJoint-by-name lookup every joint tool
that edits, drives, or links an existing joint resolves through.
"""

import adsk.core
import adsk.fusion

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

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("build_joint_geometry (keypoint factory per entity kind) + apply_motion (motion-type "
             "dispatch, frame-relative or a custom direction entity) + all_joints (the full joint walk "
             "- joints AND asBuiltJoints, root and every sub-component - that the health rollups count "
             "broken joints over) + find_joint (resolve ONE by name over those same scopes) + "
             "motion_link_partner (a joint's own MotionLink membership -> linked-partner name; "
             "joint_drive's second-member refusal gates on it) + "
             "all_joint_origins (the ONE JointOrigin walk) / find_joint_origins_by_name / "
             "jo_assembly_proxy (the JO leaf ops resolve-one/collect-names/read-axes sit on) + "
             "motion_param_names/OFFSET_PARAM_NOTE (the joint's own offset/angle dNN read + the one "
             "offset-is-frame-Z wire sentence every joint payload appends)")


def motion_param_names(joint):
    """The joint's OWN ModelParameter names: {'offset': dNN, 'angle': dNN}, absent ones omitted.
    Joint.offset moves the anchor along the joint frame's TERTIARY (Z) axis (the API's own docstring;
    live-verified) - it is the ONLY parametric position drive a joint has. A slider's slide VALUE has
    no ModelParameter at all, even after joint_drive poses it (live-verified)."""
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
    "VALUE has no parameter (joint_drive poses it; driven poses do not survive recompute).")

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
                # entityToken is stable across the two root proxies; id() falls back for fakes.
                token = safe(lambda j=j: j.entityToken)
                key = token if token is not None else id(j)
                if key in seen:
                    continue
                seen.add(key)
                out.append(j)
    return out


def find_joint(design, name):
    """Find a Joint or AsBuiltJoint by name (via itemByName - the API's own per-scope resolve-one),
    over the same scopes as all_joints: the root component, then every sub-component, joints AND
    asBuiltJoints (both are separate collections, and a joint internal to a sub-component lives there,
    so a root-only lookup would miss it)."""
    want = (name or "").strip()
    j = safe(lambda: design.rootComponent.joints.itemByName(want))
    if j:
        return j
    j = safe(lambda: design.rootComponent.asBuiltJoints.itemByName(want))
    if j:
        return j
    for c in safe(lambda: design.allComponents, []) or []:
        cand = safe(lambda c=c: c.joints.itemByName(want))
        if cand:
            return cand
        cand = safe(lambda c=c: c.asBuiltJoints.itemByName(want))
        if cand:
            return cand
    return None


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
    De-duplicated by entityToken: design.allComponents includes the root as a proxy DISTINCT from
    design.rootComponent, so a root JO is reached twice - counting it once each would double-list it (the
    token is stable across the two root proxies, verified live for all_joints; id() falls back for fakes)."""
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
    root_name = safe(lambda: root.name)
    if comp is root or (comp is not None and safe(lambda: comp.name) == root_name):
        return jo, None
    occs = list(safe(lambda: root.allOccurrencesByComponent(comp)) or []) if root else []
    if len(occs) == 1:
        proxy = safe(lambda: jo.createForAssemblyContext(occs[0]))
        return (proxy or jo), None
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
    root_name = safe(lambda: root.name)
    if comp is root or (comp is not None and safe(lambda: comp.name) == root_name):
        return [nm]
    occs = list(safe(lambda: root.allOccurrencesByComponent(comp)) or []) if root else []
    out = [f"{safe(lambda o=o: o.fullPathName)}:{nm}" for o in occs if safe(lambda o=o: o.fullPathName)]
    return out or [nm]
