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

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("build_joint_geometry (keypoint factory per entity kind) + apply_motion (motion-type "
             "dispatch, frame-relative or a custom direction entity) + all_joints (the full joint walk "
             "- joints AND asBuiltJoints, root and every sub-component - that the health rollups count "
             "broken joints over) + find_joint (resolve ONE by name over those same scopes)")

# axis keyword -> JointDirections axis index (Custom=3 is not indexed here - it is selected by
# passing a custom_entity to apply_motion instead).
AXES = {"x": 0, "y": 1, "z": 2}


def build_joint_geometry(entity, edge_keypoint=None):
    """Build a JointGeometry for a face/edge/vertex/point entity, picking the keypoint the API accepts
    for that entity's kind. CenterKeyPoint is INVALID on a cylinder/cone face - MiddleKeyPoint is used
    there instead; a circular edge centers, a straight edge uses its midpoint. edge_keypoint overrides
    the automatic edge pick with an explicit JointKeyPointTypes value (a start/middle/end/center choice
    an anchor tool offers its caller). Returns (geometry, label, error_or_None)."""
    JG = adsk.fusion.JointGeometry
    KP = adsk.fusion.JointKeyPointTypes
    if isinstance(entity, adsk.fusion.BRepFace):
        st = safe(lambda: entity.geometry.surfaceType)
        if st == adsk.core.SurfaceTypes.PlaneSurfaceType:
            g = safe(lambda: JG.createByPlanarFace(entity, None, KP.CenterKeyPoint))
            return g, "planar_face@center", None if g else "createByPlanarFace failed"
        if st in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
            g = safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
            return g, "cylinder_face@middle", None if g else "createByNonPlanarFace failed"
        g = safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
        return g, "nonplanar_face@middle", None if g else "createByNonPlanarFace failed"
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


def all_joints(design):
    """Every Joint AND AsBuiltJoint in the design, as a flat list of the joint objects: the root
    component plus every sub-component (both are SEPARATE collections, and a joint internal to a
    sub-component lives on that component - a root-only walk under-reports, so a broken sub-component or
    as-built joint would be invisible to a health rollup). The ONE joint walk: find_joint resolves a
    name over it, and the assembly_probe / workspace_orient health rollups count broken joints over it,
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
