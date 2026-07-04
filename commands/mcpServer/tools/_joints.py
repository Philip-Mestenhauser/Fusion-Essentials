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
             "dispatch, frame-relative or a custom direction entity) + find_joint (walks joints AND "
             "asBuiltJoints, root and every sub-component)")

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


def apply_motion(ji, jtype, axis_idx, custom_entity=None):
    """Set rigid/revolute/slider/cylindrical/planar/ball motion on a JointInput (or an existing Joint
    being redefined). axis_idx (0/1/2 = x/y/z) selects the FRAME-relative axis unless custom_entity is
    given, in which case JointDirections.CustomJointDirection pairs with that entity for a TRUE
    direction instead of the joint geometry's local frame - either a world construction axis (an
    explicit world-axis override) or a cylinder/cone face's or circular edge's own axis (deriving the
    motion axis from the geometry itself). Returns (did, error_or_None)."""
    JD = adsk.fusion.JointDirections
    if custom_entity is not None:
        ax = JD.CustomJointDirection
    else:
        ax = [JD.XAxisJointDirection, JD.YAxisJointDirection, JD.ZAxisJointDirection][axis_idx]
    try:
        if jtype == "rigid":
            return bool(ji.setAsRigidJointMotion()), None
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
}


def current_joint_type(joint):
    """Map a joint's current JointMotion subclass to the create/edit joint_type keyword (or '' if the
    motion is absent or unrecognized)."""
    jm = safe(lambda: joint.jointMotion)
    return _MOTION_CLASS_TO_TYPE.get(type(jm).__name__, "") if jm else ""


def find_joint(design, name):
    """Find a Joint or AsBuiltJoint by name. Joints between components live on the root component; a
    joint internal to a sub-component lives there instead - and asBuiltJoints is a separate collection
    from joints, so both must be searched or an as-built joint is invisible."""
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
