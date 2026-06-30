# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create a Joint between two joint inputs.

  joint -> create a Joint (timeline feature) between two inputs, with a chosen motion type
           (rigid / revolute / slider / cylindrical / planar / ball / pin-slot) and optional
           offset / angle / flip. WRITES to the design.

This is the API equivalent of the Joint command. A joint is defined by TWO inputs and a motion
type — the tool just builds the feature; it does not assume what the inputs represent or why you
are joining them. Fusion's Joints.createInput accepts a JointGeometry OR a JointOrigin for each
side; this block resolves each input by JOINT-ORIGIN NAME (the common, unambiguous case). Other
input kinds (a face/edge → JointGeometry) can be added to the same resolver later without
changing the tool's shape.

Grounded in adsk.core / adsk.fusion:
  - Component.joints (Joints).createInput(inputOne, inputTwo) -> JointInput  (each input is a
    JointGeometry or JointOrigin); JointInput.setAs<Type>JointMotion(...); .offset/.angle/.isFlipped
  - Joints.add(jointInput) -> Joint
  - JointDirections (X=0,Y=1,Z=2,Custom=3) for motion axes; ValueInput.createByReal(cm/rad)
  - Design.rootComponent.allJoints / jointOrigins.itemByName for resolution/reporting
Handler runs on the main thread; WRITES to the design.
"""

import math

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe
from . import _common
from . import _inputs

_AXES = {"x": 0, "y": 1, "z": 2}  # JointDirections (Custom=3 not exposed here)

# A joint input may be a find_geometry handle (resolved via the shared GeometryHandle kind, require=any
# since a joint can land on a face/edge/vertex/point). Not required at the kind level — a non-token spec
# (a JO name or a '<occ>:<snap>') just fails to resolve as a handle and falls through in _resolve_input.
_HANDLE = _inputs.GeometryHandle("input", require="any")

# joint_type -> (label, needs_axis). The setter is dispatched in _apply_motion.
_JOINT_TYPES = {
"rigid": ("rigid", False),
"revolute": ("revolute", True),
"slider": ("slider", True),
"cylindrical": ("cylindrical", True),
"planar": ("planar", True),
"ball": ("ball", False),
}


def _fmt_num(v):
    """Format a number for a parameter expression: drop a trailing '.0' (e.g. -200, not -200.0)."""
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


def _find_joint_origin(design, name):
    """Resolve a joint input by joint-origin name. Returns a JointOrigin usable as a joint input.

    A joint origin that lives inside a child/referenced OCCURRENCE must be supplied to the joint
    as its ASSEMBLY-CONTEXT PROXY (jo.createForAssemblyContext(occurrence)), NOT the native JO —
    the native one yields "Provided input paths for joint are not valid". So: a JO on the root
    component is returned as-is; a JO that belongs to a sub-component is resolved through the
    occurrence that brings it into the assembly, as a proxy.
    """
    name = (name or "").strip()
    if not name:
        return None
    root = design.rootComponent

    # On the root component -> native JO is fine (it is already in assembly context).
    jo = safe(lambda: root.jointOrigins.itemByName(name))
    if jo:
        return jo

    # Otherwise find the native JO's owning component, then the occurrence that instances it,
    # and return the JO's proxy in that occurrence's context.
    native = None
    for c in safe(lambda: design.allComponents, []) or []:
        cand = safe(lambda c=c: c.jointOrigins.itemByName(name))
        if cand:
            native = cand
            break
    if not native:
        return None
    owner_name = safe(lambda: native.parentComponent.name)
    if not owner_name:
        return native
    # Find an occurrence of the owning component and proxy the JO into it. NOTE: match by NAME,
    # not `is` — the Fusion API returns fresh wrapper objects for the same component, so identity
    # comparison (occ.component is owner) is unreliable and silently fails.
    try:
        for occ in root.allOccurrences:
            if (safe(lambda occ=occ: occ.component.name) or "") == owner_name:
                proxy = safe(lambda occ=occ: native.createForAssemblyContext(occ))
                if proxy:
                    return proxy
    except Exception:
        pass
    return native  # last resort (will likely error on add, but better than nothing)


def _find_occurrence(design, name):
    """Resolve a SINGLE occurrence by fullPathName (unambiguous) or name via the shared OccurrenceRef
    logic — refuses an ambiguous substring instead of grabbing the first instance. Returns
    (occurrence, error_or_None)."""
    return _inputs._resolve_occurrence(name, name)


def _resolve_snap_entity(design, occ_name, snap):
    """Resolve an occurrence's geometry to a single PROXIED BRep entity (no human selection),
    in the occurrence's assembly context. snap: origin | center | top | bottom | cylinder.
    Returns (entity_or_None, kind, error) where kind is 'point' | 'planar' | 'cylinder'.

    This is the shared geometry resolver used both to build joint inputs (wrapped in a
    JointGeometry) and to build assembly-constraint relationships (the raw entity)."""
    occ, occ_err = _find_occurrence(design, occ_name)
    if not occ:
        return None, None, occ_err

    if snap == "origin":
        op = safe(lambda: occ.component.originConstructionPoint)
        if not op:
            return None, None, f"'{occ_name}' has no origin construction point."
        pt = safe(lambda: op.createForAssemblyContext(occ)) or op
        return pt, "point", None

    body = safe(lambda: occ.component.bRepBodies.item(0))
    if not body:
        return None, None, f"'{occ_name}' has no body to snap to."
    faces = safe(lambda: body.faces)
    if not faces or safe(lambda: faces.count, 0) == 0:
        return None, None, f"'{occ_name}' body has no faces."

    if snap == "cylinder":
        cyl = None
        for f in faces:
            if safe(lambda f=f: f.geometry.surfaceType, None) == 3:  # CylinderSurfaceType
                cyl = f
                break
        if not cyl:
            return None, None, f"'{occ_name}' has no cylindrical face to snap to."
        proxy = safe(lambda: cyl.createForAssemblyContext(occ)) or cyl
        return proxy, "cylinder", None

    # top / bottom / center -> a planar face
    face = _pick_face(faces, snap)
    if not face:
        return None, None, f"Could not pick a '{snap}' face on '{occ_name}'."
    proxy = safe(lambda: face.createForAssemblyContext(occ)) or face
    return proxy, "planar", None


def _resolve_snap_input(design, occ_name, snap):
    """Build a JointGeometry from an occurrence's geometry (no human selection), proxied into the
    occurrence's assembly context. snap: origin | center | top | bottom | cylinder.
    Returns (jointGeometry_or_None, error_or_None)."""
    entity, kind, err = _resolve_snap_entity(design, occ_name, snap)
    if not entity:
        return None, err
    JG = adsk.fusion.JointGeometry
    KP = adsk.fusion.JointKeyPointTypes
    if kind == "point":
        g = safe(lambda: JG.createByPoint(entity))
        return (g, None) if g else (None, "createByPoint failed for origin.")
    if kind == "cylinder":
        g = safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
        return (g, None) if g else (None, "createByNonPlanarFace failed.")
    g = safe(lambda: JG.createByPlanarFace(entity, None, KP.CenterKeyPoint))
    return (g, None) if g else (None, "createByPlanarFace failed.")


def _jg_from_entity(entity):
    """Build a JointGeometry from a live BRep/construction entity, picking a VALID keypoint per kind
    (CenterKeyPoint is invalid on a cylinder/cone — use MiddleKeyPoint). Returns (geometry, label,
    error). Mirrors joint_at_geometry's resolver so a handle is a first-class joint input here too —
    AT the real geometry, NOT collapsed to the part origin the way a ':origin' snap does."""
    JG = adsk.fusion.JointGeometry
    KP = adsk.fusion.JointKeyPointTypes
    if isinstance(entity, adsk.fusion.BRepFace):
        st = safe(lambda: entity.geometry.surfaceType)
        if st == adsk.core.SurfaceTypes.PlaneSurfaceType:
            g = safe(lambda: JG.createByPlanarFace(entity, None, KP.CenterKeyPoint))
            return g, "planar_face@center", None if g else "createByPlanarFace failed"
        g = safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
        return g, "nonplanar_face@middle", None if g else "createByNonPlanarFace failed"
    if isinstance(entity, adsk.fusion.BRepEdge):
        ct = safe(lambda: entity.geometry.curveType)
        kp = KP.CenterKeyPoint if ct == adsk.core.Curve3DTypes.Circle3DCurveType else KP.MiddleKeyPoint
        g = safe(lambda: JG.createByCurve(entity, kp))
        return g, "edge", None if g else "createByCurve failed for this edge"
    if isinstance(entity, (adsk.fusion.BRepVertex, adsk.fusion.ConstructionPoint, adsk.fusion.SketchPoint)):
        g = safe(lambda: JG.createByPoint(entity))
        return g, "point", None if g else "createByPoint failed"
    return None, None, f"entity kind {type(entity).__name__} is not a supported joint geometry"


def _resolve_input(design, spec):
    """Resolve one joint input, in order: (1) a find_geometry 'handle' (entity token) -> a JointGeometry
    AT that real geometry; (2) a geometry snap '<occ>:<snap>'; (3) a joint-origin name. Returns
    (input_object_or_None, label, error_or_None)."""
    # (1) handle first: a find_geometry token resolves to a live entity. Distinguished from a JO name
    # by RESOLVING — if findEntityByToken yields nothing, fall through to snap/name (a JO name is never
    # a valid token). This is the geometry-as-values path: joint AT the geometry, not at a collapsed origin.
    ent, herr = _HANDLE.resolve(spec)
    if ent is not None:
        g, label, gerr = _jg_from_entity(ent)
        return (g, f"handle:{label}", None) if g else (None, spec, gerr)
    occ_name, snap = _parse_snap(spec)
    if snap:
        g, err = _resolve_snap_input(design, occ_name, snap)
        return g, f"{occ_name}:{snap}", err
    jo = _find_joint_origin(design, spec)
    if jo:
        return jo, spec, None
    return None, spec, (f"'{spec}' is not a find_geometry handle, a Joint Origin name, or a recognized "
    "'<occurrence>:<snap>' spec (snap = origin/center/top/bottom/left/right/front/back/cylinder).")


# Autonomous geometry "snaps": resolve a joint input from an occurrence's geometry — no human
# selection. An input string may be '<occurrence>:<snap>' where snap is one of these keywords.
_SNAP_KEYWORDS = ("origin", "center", "top", "bottom", "left", "right", "front", "back", "cylinder")


def _parse_snap(spec):
    """Split '<occurrence>:<snap>' into (occurrence_name, snap) when the trailing token is a known
    snap keyword; otherwise return (None, None) so the input is treated as a joint-origin name.

    Note an occurrence name itself contains a ':<instance>' (e.g. 'Boom:1'), so ONLY a final token
    matching a snap keyword counts as a snap — 'Boom:1' is a plain name, 'Boom:1:top' is a snap.
    """
    s = (spec or "").strip()
    if ":" not in s:
        return None, None
    head, _, tail = s.rpartition(":")
    if head and tail.lower() in _SNAP_KEYWORDS:
        return head, tail.lower()
    return None, None


def _face_extent(face, axis):
    """Return (min, max) of a face's bounding box along axis 0/1/2 (x/y/z)."""
    bb = safe(lambda: face.boundingBox)
    if not bb:
        return 0.0, 0.0
    coord = ("x", "y", "z")[axis]
    return (safe(lambda: getattr(bb.minPoint, coord), 0.0),
            safe(lambda: getattr(bb.maxPoint, coord), 0.0))


def _is_planar(face):
    # adsk.fusion.SurfaceTypes.PlaneSurfaceType == 0; our fake uses 0 for planar too.
    return safe(lambda: face.geometry.surfaceType, None) == 0


# Directional snap -> (axis index, want_max). 'right/left' = +X/-X, 'back/front' = +Y/-Y,
# 'top/bottom' = +Z/-Z. The extreme PLANAR face along that axis is chosen.
_FACE_DIRECTIONS = {
"right": (0, True), "left": (0, False),
"back": (1, True), "front": (1, False),
"top": (2, True), "bottom": (2, False),
}


def _pick_face(faces, snap):
    """Choose a PLANAR face from a body by snap.

    Directional snaps (top/bottom/left/right/front/back) pick the extreme planar face along the
    corresponding world axis — e.g. 'right' = greatest +X, 'front' = least Y. 'center' = the
    largest-area planar face. Only PLANAR faces are considered: a snap targets a face center via
    createByPlanarFace, which rejects non-planar faces — and a cylinder's curved wall would
    otherwise win on raw extent (the cable-cap bug). Returns the face or None."""
    if snap in _FACE_DIRECTIONS:
        axis, want_max = _FACE_DIRECTIONS[snap]
        # The extreme face LIES IN the extreme plane (e.g. the top cap has min==max==zmax of the
        # body). A side wall merely REACHES that plane (its max == zmax) but also spans inward
        # (its min is far lower). So rank by the face's NEAR coordinate: for 'max' pick the face
        # whose MIN is greatest (sits highest as a whole); for 'min' pick the face whose MAX is
        # least. This selects the cap, not a side wall that happens to touch the extreme.
        best, best_v = None, None
        for f in faces:
            if not _is_planar(f):
                continue
            mn, mx = _face_extent(f, axis)
            v = mn if want_max else mx
            if best_v is None or (v > best_v if want_max else v < best_v):
                best_v, best = v, f
        return best
    # center -> largest planar face
    best, best_area = None, -1.0
    for f in faces:
        if not _is_planar(f):
            continue
        a = safe(lambda f=f: f.area, 0.0) or 0.0
        if a > best_area:
            best_area, best = a, f
    return best


def _world_axis_entity(design, axis_idx):
    """Return the root component's world construction axis (x/y/z) for use as a CUSTOM joint
    direction. CRITICAL: the XAxis/YAxis/ZAxisJointDirection enums are relative to the JOINT
    GEOMETRY's local frame, NOT the world — a snap whose local Z points along world Y will pivot
    about world Y when you ask for 'Z'. Passing a world construction axis as the custom direction
    makes the motion about a TRUE world axis regardless of the snap frame."""
    root = design.rootComponent
    attr = ["xConstructionAxis", "yConstructionAxis", "zConstructionAxis"][axis_idx]
    return safe(lambda: getattr(root, attr))


def _apply_limits(motion, *, min_deg=None, max_deg=None, rest_deg=None,
                  min_mm=None, max_mm=None, rest_mm=None, cm_scale=0.1):
    """Apply rotation and/or linear (slide) limits to a JointMotion. Returns (changed, error).

    Angular limits go on motion.rotationLimits (radians); linear go on motion.slideLimits
    (centimeters). A revolute motion has no slideLimits and a slider has no rotationLimits, so
    asking for the wrong kind errors clearly instead of silently no-op'ing. 'rest' is the resting
    value. cm_scale converts the caller's length units to cm."""
    import math as _m
    changed = {}

    want_rot = any(v is not None for v in (min_deg, max_deg, rest_deg))
    want_lin = any(v is not None for v in (min_mm, max_mm, rest_mm))

    if want_rot:
        rl = safe(lambda: motion.rotationLimits)
        if rl is None:
            return changed, ("This joint's motion has no ROTATION limits "
    "(min_deg/max_deg/rest_deg need a revolute or cylindrical joint).")
        if min_deg is not None:
            rl.isMinimumValueEnabled = True
            rl.minimumValue = _m.radians(float(min_deg))
            changed["min_deg"] = float(min_deg)
        if max_deg is not None:
            rl.isMaximumValueEnabled = True
            rl.maximumValue = _m.radians(float(max_deg))
            changed["max_deg"] = float(max_deg)
        if rest_deg is not None:
            rl.isRestValueEnabled = True
            rl.restValue = _m.radians(float(rest_deg))
            changed["rest_deg"] = float(rest_deg)

    if want_lin:
        sl = safe(lambda: motion.slideLimits)
        if sl is None:
            return changed, ("This joint's motion has no LINEAR/slide limits "
    "(min_mm/max_mm/rest_mm need a slider or cylindrical joint).")
        if min_mm is not None:
            sl.isMinimumValueEnabled = True
            sl.minimumValue = float(min_mm) * cm_scale
            changed["min_mm"] = float(min_mm)
        if max_mm is not None:
            sl.isMaximumValueEnabled = True
            sl.maximumValue = float(max_mm) * cm_scale
            changed["max_mm"] = float(max_mm)
        if rest_mm is not None:
            sl.isRestValueEnabled = True
            sl.restValue = float(rest_mm) * cm_scale
            changed["rest_mm"] = float(rest_mm)

    return changed, None


def _apply_motion(ji, jtype, axis_idx, world_axis_entity=None):
    """Set the joint motion on the JointInput (or existing Joint). Returns (did, error_or_None).

    When world_axis_entity is given, the motion axis is the CUSTOM world construction axis (true
    world direction) instead of the frame-relative XAxis/YAxis/ZAxisJointDirection enum."""
    JD = adsk.fusion.JointDirections
    if world_axis_entity is not None:
        ax = JD.CustomJointDirection
    else:
        ax = [JD.XAxisJointDirection, JD.YAxisJointDirection, JD.ZAxisJointDirection][axis_idx]
    try:
        if jtype == "rigid":
            return bool(ji.setAsRigidJointMotion()), None
        if jtype == "revolute":
            if world_axis_entity is not None:
                return bool(ji.setAsRevoluteJointMotion(ax, world_axis_entity)), None
            return bool(ji.setAsRevoluteJointMotion(ax)), None
        if jtype == "slider":
            if world_axis_entity is not None:
                return bool(ji.setAsSliderJointMotion(ax, world_axis_entity)), None
            return bool(ji.setAsSliderJointMotion(ax)), None
        if jtype == "cylindrical":
            if world_axis_entity is not None:
                return bool(ji.setAsCylindricalJointMotion(ax, world_axis_entity)), None
            return bool(ji.setAsCylindricalJointMotion(ax)), None
        if jtype == "planar":
            if world_axis_entity is not None:
                return bool(ji.setAsPlanarJointMotion(ax, world_axis_entity)), None
            return bool(ji.setAsPlanarJointMotion(ax)), None
        if jtype == "ball":
            # pitch MUST be Z, yaw MUST be X (not the intuitive X/Y) — the API rejects any other pair
            # with "Invalid parameter pitchDirection". Enforced by test_ball_uses_valid_pitch_and_yaw_directions.
            return bool(ji.setAsBallJointMotion(JD.ZAxisJointDirection, JD.XAxisJointDirection)), None
    except Exception as e:
        return False, str(e)
    return False, f"unsupported joint_type '{jtype}'"


def handler(occurrence_one: str = "", occurrence_two: str = "", joint_type: str = "rigid",
            axis: str = "z", offset: float = 0.0, angle: float = 0.0, units: str = "mm",
            flip: bool = False, name: str = "", min_deg=None, max_deg=None, rest_deg=None,
            min_mm=None, max_mm=None, rest_mm=None) -> dict:
    """Create a joint between two joint inputs (resolved by joint-origin name).

    occurrence_one / occurrence_two: the two joint inputs — names of Joint Origins to join.
    joint_type: rigid (default) | revolute | slider | cylindrical | planar | ball. axis (x/y/z):
    the motion axis for types that need one. offset (in 'units') and angle (degrees) position the
    joint; flip reverses it. min_deg/max_deg/rest_deg set rotation limits (revolute/cylindrical);
    min_mm/max_mm/rest_mm set linear/slide limits (slider/cylindrical, in 'units'). WRITES.
    """
    design = _common.design()
    if not design:
        return error("No active design (open a document with assembly geometry).")

    jtype = (joint_type or "rigid").strip().lower()
    if jtype not in _JOINT_TYPES:
        return error(f"Unknown joint_type '{joint_type}'. Valid: {', '.join(_JOINT_TYPES)}.")

    ax_name = (axis or "z").strip().lower()
    if ax_name not in _AXES:
        return error(f"Unknown axis '{axis}'. Valid: x, y, z.")

    scale = UNIT_TO_CM.get((units or "mm").strip().lower())
    if scale is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    n1, n2 = (occurrence_one or "").strip(), (occurrence_two or "").strip()
    if not n1 or not n2:
        return error("Provide 'occurrence_one' and 'occurrence_two' — each a Joint Origin name OR "
    "an autonomous geometry snap '<occurrence>:<snap>' "
    "(snap = origin/center/top/bottom/left/right/front/back/cylinder).")

    jo1, label1, err1 = _resolve_input(design, n1)
    jo2, label2, err2 = _resolve_input(design, n2)
    if not jo1:
        return error(err1 or f"Could not resolve joint input '{n1}'.")
    if not jo2:
        return error(err2 or f"Could not resolve joint input '{n2}'.")

    # Joints live on the root component (a joint between two components is owned there).
    joints = design.rootComponent.joints
    try:
        ji = joints.createInput(jo1, jo2)
    except Exception as e:
        return error(f"Could not create joint input: {e}")
    if not ji:
        return error("createInput returned nothing for these inputs.")

    did, err = _apply_motion(ji, jtype, _AXES[ax_name])
    if not did:
        return error(f"Could not set {jtype} motion: {err or 'setter returned false'}.")

    # Optional offset / angle / flip.
    try:
        if offset:
            ji.offset = adsk.core.ValueInput.createByReal(offset * scale)
        if angle:
            ji.angle = adsk.core.ValueInput.createByReal(math.radians(angle))
        if flip:
            ji.isFlipped = True
    except Exception as e:
        return error(f"Could not apply offset/angle/flip: {e}")

    try:
        joint = joints.add(ji)
    except Exception as e:
        return error(f"Joint creation failed: {e}")
    if not joint:
        return error("joints.add returned nothing.")

    new_name = (name or "").strip()
    if new_name:
        try:
            joint.name = new_name
        except Exception:
            pass

    # Optional limits (rotation and/or linear) — applied after the joint exists so its jointMotion
    # is established. Same routing as joint_edit.
    limits_out = {}
    if any(v is not None for v in (min_deg, max_deg, rest_deg, min_mm, max_mm, rest_mm)):
        jm = safe(lambda: joint.jointMotion)
        if jm is None:
            return error("Limits requested but this joint type has no motion to limit "
            "(rigid/inferred). Use revolute/slider/cylindrical.")
        lim_changed, lim_err = _apply_limits(
            jm, min_deg=min_deg, max_deg=max_deg, rest_deg=rest_deg,
            min_mm=min_mm, max_mm=max_mm, rest_mm=rest_mm, cm_scale=scale)
        if lim_err:
            return error(lim_err)
        limits_out = lim_changed

    return ok({
        "created": True,
        "joint_name": safe(lambda: joint.name),
        "joint_type": jtype,
        "input_one": label1,
        "input_two": label2,
        "axis": (ax_name if _JOINT_TYPES[jtype][1] else None),
        "offset": offset if offset else None,
        "angle_deg": angle if angle else None,
        "flipped": bool(flip),
        **limits_out,
        "note": "Joint created as a timeline feature. View it with view_screenshot.",
    })


def _find_joint(design, name):
    """Find a Joint by name. Joints between components live on the root component; a joint internal
    to a sub-component lives there — search both."""
    want = (name or "").strip()
    j = safe(lambda: design.rootComponent.joints.itemByName(want))
    if j:
        return j
    for c in safe(lambda: design.allComponents, []) or []:
        cand = safe(lambda c=c: c.joints.itemByName(want))
        if cand:
            return cand
    return None


_MOTION_CLASS_TO_TYPE = {
"RigidJointMotion": "rigid", "RevoluteJointMotion": "revolute",
"SliderJointMotion": "slider", "CylindricalJointMotion": "cylindrical",
"PlanarJointMotion": "planar", "BallJointMotion": "ball",
}


def _current_joint_type(joint):
    """Map a joint's current JointMotion subclass to our joint_type keyword (or '')."""
    jm = safe(lambda: joint.jointMotion)
    return _MOTION_CLASS_TO_TYPE.get(type(jm).__name__, "") if jm else ""


def edit_handler(joint_name: str = "", input_one: str = "", input_two: str = "",
                 joint_type: str = "", axis: str = "", world_axis: str = "", flip=None,
                 offset=None, angle=None, units: str = "mm",
                 rotation_deg=None, min_deg=None, max_deg=None, rest_deg=None,
                 min_mm=None, max_mm=None, rest_mm=None) -> dict:
    """Edit an EXISTING joint in place — no remaking. Re-select snap inputs, change motion type/axis,
    toggle flip, drive/limit the rotation.

    joint_name: the joint to edit. Any subset of: input_one/input_two (new snap inputs — a Joint
    Origin name OR '<occurrence>:<snap>'); joint_type (rigid/revolute/slider/cylindrical/planar/ball)
    + axis (x/y/z) to redefine the motion; flip (true/false) to toggle direction; rotation_deg to
    drive a revolute/slider value; min_deg/max_deg to set rotation limits. WRITES.

    The Fusion API requires the timeline marker be positioned just before the joint to edit its
    geometry/flip/motion, so this rolls the marker before, applies the edits, then rolls it back.
    """
    design = _common.design()
    if not design:
        return error("No active design.")
    joint = _find_joint(design, joint_name)
    if not joint:
        return error(f"No joint named '{joint_name}'. Use design_get_timeline or check the name.")

    # DRIVING the rotation value (jointMotion.rotationValue = "Drive Joints") destabilizes the
    # server connection when set from this context (reproduced: a clean revolute joint dropped the
    # socket). Refuse it and redirect to the proven path. (See the safe edits below: re-snap inputs,
    # motion type/axis, world_axis, flip, limits.)
    if rotation_deg is not None:
        return error("Driving a joint to a rotation value from here is unsafe (it closes the "
    "server connection). To pose a jointed assembly, use assembly_move (rotate "
    "the moving occurrence) + assembly_capture_position instead — that path is proven safe.")

    # world_axis (re-point the motion to a TRUE WORLD axis) forces a motion re-set even if the
    # joint_type isn't changing — that's the whole point (fixing a frame-relative axis).
    wa_name = (world_axis or "").strip().lower()
    if wa_name and wa_name not in _AXES:
        return error(f"Unknown world_axis '{world_axis}'. Valid: x, y, z.")

    # Validate units (used by offset).
    if (offset is not None) and (UNIT_TO_CM.get((units or "mm").strip().lower()) is None):
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    # Decide what's being changed; refuse a no-op so we never roll the timeline for nothing.
    want_inputs = bool((input_one or "").strip() or (input_two or "").strip())
    want_motion = bool((joint_type or "").strip()) or bool(wa_name)
    want_flip = flip is not None
    want_offset = offset is not None
    want_angle = angle is not None
    want_limits = any(v is not None for v in
                      (min_deg, max_deg, rest_deg, min_mm, max_mm, rest_mm))
    if not (want_inputs or want_motion or want_flip or want_offset or want_angle or want_limits):
        return error("Nothing to change. Provide at least one of: input_one/input_two, joint_type "
                      "(+axis), world_axis, flip, offset (+units), angle, "
                      "min_deg/max_deg/rest_deg (rotation), min_mm/max_mm/rest_mm (linear).")

    # Validate motion type up front (before touching the timeline). If only world_axis is given,
    # re-apply the joint's CURRENT motion type with the world axis.
    jtype = (joint_type or "").strip().lower()
    if (joint_type or "").strip() and jtype not in _JOINT_TYPES:
        return error(f"Unknown joint_type '{joint_type}'. Valid: {', '.join(_JOINT_TYPES)}.")
    if want_motion and not jtype:
        jtype = _current_joint_type(joint)
        if jtype not in _JOINT_TYPES:
            return error("world_axis given but the joint's current motion type is not "
    "axis-based (rigid/ball have no single axis to re-point).")
    ax_name = (axis or "z").strip().lower()
    if want_motion and not wa_name and _JOINT_TYPES[jtype][1] and ax_name not in _AXES:
        return error(f"Unknown axis '{axis}'. Valid: x, y, z.")

    # Resolve new snap inputs (before rolling, so a bad input fails cleanly).
    new1 = new2 = None
    label1 = label2 = None
    if (input_one or "").strip():
        new1, label1, err1 = _resolve_input(design, input_one.strip())
        if not new1:
            return error(err1 or f"Could not resolve input_one '{input_one}'.")
    if (input_two or "").strip():
        new2, label2, err2 = _resolve_input(design, input_two.strip())
        if not new2:
            return error(err2 or f"Could not resolve input_two '{input_two}'.")

    changed = {}
    rolled = False
    try:
        # The marker MUST be before the joint to edit geometry/flip/motion.
        safe(lambda: joint.timelineObject.rollTo(True))
        rolled = True

        if new1 is not None:
            joint.geometryOrOriginOne = new1
            changed["input_one"] = label1
        if new2 is not None:
            joint.geometryOrOriginTwo = new2
            changed["input_two"] = label2

        if want_motion:
            wa_entity = _world_axis_entity(design, _AXES[wa_name]) if wa_name else None
            did, err = _apply_motion(joint, jtype, _AXES.get(ax_name, 2), wa_entity)
            if not did:
                return error(f"Could not set {jtype} motion: {err or 'setter returned false'}.")
            changed["joint_type"] = jtype
            if wa_name:
                changed["world_axis"] = wa_name
            elif _JOINT_TYPES[jtype][1]:
                changed["axis"] = ax_name

        if want_flip:
            joint.isFlipped = bool(flip)
            changed["flipped"] = bool(flip)

        # offset / angle are ModelParameters on the Joint — set via an explicit-units expression
        # (robust regardless of document units), matching the create-joint tool's behaviour.
        if want_offset:
            op = safe(lambda: joint.offset)
            if op is None:
                return error("This joint has no offset parameter (rigid/inferred or already 0-DOF).")
            u = (units or "mm").strip().lower()
            u = "in" if u == "inch" else u
            # NOT safe()-wrapped: this is the mutation the tool was ASKED to do — let a failure raise
            # into the handler's try/except below so it's reported, not swallowed into a false success.
            op.expression = f"{_fmt_num(offset)} {u}"
            changed["offset"] = float(offset)
            changed["units"] = u

        if want_angle:
            ap = safe(lambda: joint.angle)
            if ap is None:
                return error("This joint has no angle parameter.")
            ap.expression = f"{_fmt_num(angle)} deg"
            changed["angle"] = float(angle)

        if want_limits:
            jm = safe(lambda: joint.jointMotion)
            if jm is None:
                return error("This joint has no editable motion (rigid/inferred has no limits).")
            lim_scale = UNIT_TO_CM.get((units or "mm").strip().lower(), 0.1)
            lim_changed, lim_err = _apply_limits(
                jm, min_deg=min_deg, max_deg=max_deg, rest_deg=rest_deg,
                min_mm=min_mm, max_mm=max_mm, rest_mm=rest_mm, cm_scale=lim_scale)
            changed.update(lim_changed)
            if lim_err:
                return error(lim_err)
    except Exception as e:
        return error(f"Edit failed: {e}")
    finally:
        if rolled:
            safe(lambda: joint.timelineObject.rollTo(False))  # restore marker to the end

    # Editing a joint rolls the timeline marker, which can leave DOWNSTREAM features (patterns, later
    # joints) in a stale compute-failed state until a full recompute. Do it here so the caller gets a
    # settled, accurate model — they no longer have to remember to call design_recompute. (Live: a
    # joint offset edit left 4 downstream features broken until computeAll.)
    recompute_errors = None
    try:
        design.computeAll()
        tl = safe(lambda: design.timeline)
        errs = []
        for i in range(safe(lambda: tl.count, 0) or 0):
            it = safe(lambda i=i: tl.item(i))
            if safe(lambda it=it: it.healthState) == 2:
                errs.append(safe(lambda it=it: it.name) or f"#{i}")
        recompute_errors = errs
    except Exception:
        pass

    out = {"edited": True, "joint_name": safe(lambda: joint.name), "changes": changed}
    # surface the most-asked fields at top level for convenience
    for key in ("input_one", "input_two", "joint_type", "axis", "world_axis", "flipped",
                       "offset", "angle", "min_deg", "max_deg", "rest_deg", "min_mm", "max_mm", "rest_mm"):
        if key in changed:
            out[key] = changed[key]
    out["recomputed"] = True
    if recompute_errors:
        out["timeline_errors_after"] = recompute_errors
        out["note"] = ("Joint edited + recomputed, but the timeline still has errored feature(s) "
                       f"({', '.join(recompute_errors)}) — the edit may over-constrain something.")
    else:
        out["note"] = ("Joint edited in place + full recompute (downstream features settled). "
                       "view_screenshot to view.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Create a Joint between two inputs. Each input ('occurrence_one'/'occurrence_two'), most precise "
    "first: a find_geometry handle (joints AT that exact face/edge — a real offset); a Joint Origin "
    "name (from joint_create_origin); or a snap-string '<occurrence>:<snap>' where snap = origin | "
    "center (largest planar face) | top | bottom | cylinder (cyl-face axis), e.g. 'Boom:1:top'. Note "
    "':origin' collapses to the part origin (zero offset) — use a handle for a real offset. 'joint_type' "
    "= rigid (default)/revolute/slider/cylindrical/planar/ball; 'axis' = x/y/z for types needing a "
    "motion axis. Optional 'offset' ('units'=mm/cm/in), 'angle' (deg), 'flip'."
)

tool = (
    Tool.create_with_string_input(
        name="joint_create",
        description=TOOL_DESCRIPTION,
        input_param_name="occurrence_one",
        input_param_description="First input: a find_geometry 'handle' (joints AT real geometry), a Joint Origin name, OR a snap '<occurrence>:<snap>' (origin/center/top/bottom/left/right/front/back/cylinder).",
    )
    .add_input_property("occurrence_two", {"type": "string",
            "description": "Second input: a find_geometry 'handle' (joints AT real geometry), a Joint Origin name, OR a snap '<occurrence>:<snap>' (origin/center/top/bottom/left/right/front/back/cylinder)."})
    .add_input_property(*_inputs.joint_motion(default="rigid").as_property())
    .add_input_property("axis", {"type": "string",
            "description": "Motion axis for types that need one: x | y | z (default z)."})
    .add_input_property("offset", {"type": "number", "description": "Offset distance (in 'units'; default 0)."})
    .add_input_property("angle", {"type": "number", "description": "Angle in degrees (default 0)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("flip", {"type": "boolean", "description": "Reverse the joint direction (default false)."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the joint."})
    .add_input_property("min_deg", {"type": "number", "description": "Rotation limit min (degrees) — revolute/cylindrical."})
    .add_input_property("max_deg", {"type": "number", "description": "Rotation limit max (degrees) — revolute/cylindrical."})
    .add_input_property("rest_deg", {"type": "number", "description": "Rotation rest value (degrees) — revolute/cylindrical."})
    .add_input_property("min_mm", {"type": "number", "description": "Linear/slide limit min (in 'units') — slider/cylindrical."})
    .add_input_property("max_mm", {"type": "number", "description": "Linear/slide limit max (in 'units') — slider/cylindrical."})
    .add_input_property("rest_mm", {"type": "number", "description": "Linear/slide rest value (in 'units') — slider/cylindrical."})
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


EDIT_DESCRIPTION = (
"Edit an existing joint in place. 'joint_name' selects it; pass any subset to change: 'input_one'/"
"'input_two' re-select the snap inputs (a Joint Origin name or '<occurrence>:<snap>' = origin/center/"
"top/bottom/cylinder); 'joint_type' (rigid/revolute/slider/cylindrical/planar/ball) + 'axis' (x/y/z) "
"redefine the motion; 'world_axis' (x/y/z) re-points rotation/slide to a TRUE world axis (fixes a "
"joint pivoting about the wrong axis when the snap frame isn't world-aligned); 'flip'; 'offset' "
"('units') + 'angle' (deg); rotation limits 'min_deg'/'max_deg'/'rest_deg' (revolute/cylindrical) and "
"linear limits 'min_mm'/'max_mm'/'rest_mm' (slider/cylindrical). To DRIVE a joint to a pose use "
"assembly_move + assembly_capture_position, not this."
)
edit_tool = (
    Tool.create_simple(name="joint_edit", description=EDIT_DESCRIPTION)
    .add_input_property("joint_name", {"type": "string", "description": "Name of the joint to edit."})
    .add_input_property("input_one", {"type": "string",
            "description": "New first input: Joint Origin name OR '<occurrence>:<snap>'."})
    .add_input_property("input_two", {"type": "string",
            "description": "New second input: Joint Origin name OR '<occurrence>:<snap>'."})
    .add_input_property(*_inputs.joint_motion(default="rigid", description="Redefine the joint motion type.").as_property())
    .add_input_property("axis", {"type": "string", "description": "Motion axis (x/y/z) for types that need one (FRAME-relative)."})
    .add_input_property("world_axis", {"type": "string",
            "description": "Re-point the motion to a TRUE WORLD axis (x/y/z) via a construction axis — fixes a joint that pivots about the wrong world axis because the snap frame isn't world-aligned. Re-applies the current motion type if joint_type is omitted."})
    .add_input_property("flip", {"type": "boolean", "description": "Toggle the joint direction."})
    .add_input_property("offset", {"type": "number", "description": "Set the joint offset distance (in 'units'; the offset ModelParameter)."})
    .add_input_property("angle", {"type": "number", "description": "Set the joint angle between the inputs (degrees)."})
    .add_input_property(*_inputs.units_property(description="Units for 'offset'."))
    # rotation_deg is intentionally NOT exposed: the handler still accepts the kwarg and returns a
    # helpful redirect if passed, but advertising a parameter whose only behavior is to error wastes
    # context. To pose a joint, use assembly_move + assembly_capture_position.
    .add_input_property("min_deg", {"type": "number", "description": "Rotation limit min (degrees) — revolute/cylindrical."})
    .add_input_property("max_deg", {"type": "number", "description": "Rotation limit max (degrees) — revolute/cylindrical."})
    .add_input_property("rest_deg", {"type": "number", "description": "Rotation rest value (degrees) — revolute/cylindrical."})
    .add_input_property("min_mm", {"type": "number", "description": "Linear/slide limit min (in 'units') — slider/cylindrical."})
    .add_input_property("max_mm", {"type": "number", "description": "Linear/slide limit max (in 'units') — slider/cylindrical."})
    .add_input_property("rest_mm", {"type": "number", "description": "Linear/slide rest value (in 'units') — slider/cylindrical."})
    .strict_schema()
)
edit_item = Item.create_tool_item(tool=edit_tool, write="write", handler=edit_handler, run_on_main_thread=True)


def register_tool():
    register(item)
    register(edit_item)
