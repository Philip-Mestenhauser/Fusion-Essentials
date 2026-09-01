# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Creates (joint_create) and edits in place (joint_edit) a Joint between two joint inputs, with a
chosen motion type (rigid/revolute/slider/cylindrical/planar/ball/pin-slot) and optional offset/angle/
flip. Each input resolves by JOINT-ORIGIN NAME, a find_geometry handle, or an autonomous
'<occurrence>:<snap>' geometry snap. WRITES to the design.
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import apply_rename, error, ok, safe
from . import _common
from . import _inputs
from . import _assert
from ._joints import (
    AXES as _AXES,
    OFFSET_PARAM_NOTE as _OFFSET_PARAM_NOTE,
    all_joint_origins as _all_joint_origins,
    apply_motion as _apply_motion,
    build_joint_geometry as _jg_from_entity,
    current_joint_type as _current_joint_type,
    find_joint as _find_joint,
    is_as_built_joint as _is_as_built_joint,
    is_joint_origin as _is_joint_origin,
    motion_param_names as _motion_param_names,
    pending_move_guard as _pending_move_guard,
)
from . import _joints

# A joint input may be a find_geometry handle (resolved via the shared GeometryHandle kind, require=any
# since a joint can land on a face/edge/vertex/point). Not required at the kind level - a non-token spec
# (a JO name or a '<occ>:<snap>') just fails to resolve as a handle and falls through in _resolve_input.
_HANDLE = _inputs.GeometryHandle("input", require="any")

# A joint input may also be a JOINT ORIGIN, by the handle assembly_get(include=['joint_origins']) mints
# OR by name (bare when unique, else '<occurrence>:<JO name>'; ambiguity refused). This ONE kind is the
# resolve-one leaf over the shared _joints.all_joint_origins walk (the JO-name path).
_JO_REF = _inputs.JointOriginRef("input")

# joint_type -> (label, needs_axis). The setter is dispatched in _apply_motion. pin_slot needs_axis is
# True for its ROTATION axis; its perpendicular SLIDE direction comes from 'slide_axis' (defaulting to
# the next frame axis).
_JOINT_TYPES = {
"rigid": ("rigid", False),
"revolute": ("revolute", True),
"slider": ("slider", True),
"cylindrical": ("cylindrical", True),
"planar": ("planar", True),
"ball": ("ball", False),
"pin_slot": ("pin_slot", True),
}

# The motion Choice for a joint WRITE surface: the shared six + pin_slot. A consumer imports THIS
# list rather than re-spelling it, so the wire vocabulary cannot drift per tool.
_MOTIONS = list(_inputs.JOINT_MOTIONS) + ["pin_slot"]


# The one honest note appended whenever a rest limit is set. A rest value is the joint LIMIT's
# equilibrium setpoint (used by motion study); setting it does NOT move the static model - the part
# stays at the joint's home value (0), live-verified on a bare slider (rest_mm 20/50/0 moved nothing).
# So a caller must not read "rest_mm applied" as "the slider is now posed".
_REST_LIMIT_NOTE = (
    " NOTE: rest_mm/rest_deg set the joint LIMIT'S rest value (a motion-study equilibrium), which does "
    "NOT reposition the static model - it stays at the joint's home value. To POSE the mechanism use "
    "joint_drive (a driven pose does not survive recompute).")


def _fmt_num(v):
    """Format a number for a parameter expression: drop a trailing '.0' (e.g. -200, not -200.0)."""
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


def _find_occurrence(design, name):
    """Resolve a SINGLE occurrence by entityToken handle (the exact identity) or fullPathName/name via
    the shared OccurrenceRef logic - refuses an ambiguous path/name instead of grabbing the first
    instance. Returns (occurrence, error_or_None)."""
    return _inputs._resolve_occurrence(name, name)


def _available_joint_origins(design, limit=8):
    """List the design's joint-origin names with where each lives - so a resolve failure can be
    corrected from the error alone instead of the agent guessing or dropping to a raw script. The
    collect-names leaf over the ONE JO walk (_joints.all_joint_origins). Returns (listed, overflow)."""
    root_name = safe(lambda: design.rootComponent.name)
    found = []
    for jo, comp in _all_joint_origins(design):
        nm = safe(lambda jo=jo: jo.name)
        if not nm:
            continue
        cname = safe(lambda comp=comp: comp.name)
        where = "root" if cname == root_name else f"in component '{cname}'"
        found.append(f"'{nm}' ({where})")
    return found[:limit], max(len(found) - limit, 0)


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
        # The enum MEMBER, never its integer: CylinderSurfaceType is 1 and 3 is SphereSurfaceType,
        # so a hand-typed 3 here matches spheres and silently reports that a cylinder has no
        # cylindrical face. Cones count too - a tapered pin is round and seats the same way, which
        # is the pair joint_at_geometry already accepts.
        want = (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType)
        cyl = None
        for f in faces:
            if safe(lambda f=f: f.geometry.surfaceType, None) in want:
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
    entity, _kind, err = _resolve_snap_entity(design, occ_name, snap)
    if not entity:
        return None, err
    g, _label, gerr = _jg_from_entity(entity)
    return (g, None) if g else (None, gerr)


def _resolve_input(design, spec):
    """Resolve one joint input, in order: (1) a find_geometry 'handle' -> a JointGeometry AT that real
    geometry, OR - when the handle points at a JOINT ORIGIN (assembly_get mints those) - the JO itself;
    (2) a geometry snap '<occ>:<snap>'; (3) a Joint Origin by name (bare when unique, else qualified
    '<occ>:<JO name>'; an ambiguous name is refused with candidates). On failure the error lists the
    design's JOs. Returns (input_object_or_None, label, error_or_None)."""
    # (1) handle first: a find_geometry / assembly_get token resolves to a live entity. A JOINT ORIGIN
    # handle is a first-class joint input; a face/edge/vertex handle becomes a JointGeometry AT the
    # geometry (joint at the geometry, not at a collapsed origin). A JO NAME is never a valid token, so
    # it falls through to the name path below.
    ent, herr = _HANDLE.resolve(spec)
    if ent is not None:
        if _is_joint_origin(ent):
            return ent, "handle:joint_origin", None
        g, label, gerr = _jg_from_entity(ent)
        return (g, f"handle:{label}", None) if g else (None, spec, gerr)
    # (2) geometry snap
    occ_name, snap = _parse_snap(spec)
    if snap:
        g, err = _resolve_snap_input(design, occ_name, snap)
        return g, f"{occ_name}:{snap}", err
    # (3) a Joint Origin by name - the ONE resolver (bare/qualified/ambiguity), shared with cam WCS bind.
    jo, jerr = _JO_REF.resolve(spec)
    if jo is not None:
        return jo, spec, None
    if jerr and "ambiguous" in jerr.lower():
        return None, spec, jerr        # surface the disambiguation candidates verbatim
    # (4) nothing matched - the comprehensive form-guide + the available JOs (self-correction data)
    msg = (f"'{spec}' is not a find_geometry handle, a Joint Origin (handle or name), or a recognized "
    "'<occurrence>:<snap>' spec (snap = origin/center/top/bottom/left/right/front/back/cylinder). "
    "A Joint Origin may be passed bare ('Center of Model') or scoped through the occurrence that "
    "carries it ('<occurrence>:<JO name>').")
    listed, more = _available_joint_origins(design)
    if listed:
        msg += " Joint Origins in this design: " + ", ".join(listed)
        msg += f" (+{more} more)." if more else "."
    return None, spec, msg


# Autonomous geometry "snaps": resolve a joint input from an occurrence's geometry - no human
# selection. An input string may be '<occurrence>:<snap>' where snap is one of these keywords.
_SNAP_KEYWORDS = ("origin", "center", "top", "bottom", "left", "right", "front", "back", "cylinder")


def _parse_snap(spec):
    """Split '<occurrence>:<snap>' into (occurrence_name, snap) when the trailing token is a known
    snap keyword; otherwise return (None, None) so the input is treated as a joint-origin name.

    Note an occurrence name itself contains a ':<instance>' (e.g. 'Boom:1'), so ONLY a final token
    matching a snap keyword counts as a snap - 'Boom:1' is a plain name, 'Boom:1:top' is a snap.
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
    corresponding world axis - e.g. 'right' = greatest +X, 'front' = least Y. 'center' = the
    largest-area planar face. Only PLANAR faces are considered: a snap targets a face center via
    createByPlanarFace, which rejects non-planar faces - and a cylinder's curved wall would
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
    GEOMETRY's local frame, NOT the world - a snap whose local Z points along world Y will pivot
    about world Y when you ask for 'Z'. Passing a world construction axis as the custom direction
    makes the motion about a TRUE world axis regardless of the snap frame."""
    root = design.rootComponent
    return _inputs.world_construction_axis(root, "xyz"[axis_idx])


# The band a limit's read-back may differ from the request by, expressed in the REQUEST'S OWN units
# (degrees, or the caller's length unit). A limit makes a units round trip in each direction - degrees
# to radians, display length to cm - so the read-back is converted back and compared there, against
# the same display-unit band joint_drive's value_now gate holds its own round trip to.
_LIMIT_BAND = 1e-3

# native -> degrees, for the radians a rotation limit stores.
_DEG_PER_RAD = 180.0 / math.pi

# (payload key, enabled-flag property, value property) per limit, in the order they are applied.
_ROT_LIMITS = (("min_deg", "isMinimumValueEnabled", "minimumValue"),
               ("max_deg", "isMaximumValueEnabled", "maximumValue"),
               ("rest_deg", "isRestValueEnabled", "restValue"))
_LIN_LIMITS = (("min_mm", "isMinimumValueEnabled", "minimumValue"),
               ("max_mm", "isMaximumValueEnabled", "maximumValue"),
               ("rest_mm", "isRestValueEnabled", "restValue"))


def _unverified_limits_note(keys):
    """The one sentence a payload appends for limits whose read-back could not be taken - the ONE
    home for that wording, shared by joint_create and joint_edit."""
    return (f" Limits published null ({', '.join(keys)}) - each was assigned but could not be read "
            "back off the joint, so whether it TOOK is UNKNOWN here (it is not a 'yes'). Read the "
            "joint's limits with assembly_get before relying on them.")


def _set_one_limit(limits, flag_prop, value_prop, key, wanted, native, unit_scale):
    """Enable and assign ONE joint limit, then read BOTH back off the live JointLimits.

    A JointLimits is a LIVE object, not a FeatureInput, so this is a direct re-read rather than
    _common.set_verified (whose exact-equality compare suits a FeatureInput enum, not a float that
    makes a units round trip). `native` is the value assigned in the API's own units (radians / cm)
    and `unit_scale` converts a native read back into the caller's units, where the comparison
    happens within _LIMIT_BAND.

    Returns (published, error): `published` is the read-back in the caller's units, or None when the
    re-read could not be taken - never the request echoed back; `error` names the limit that did not
    take."""
    try:
        setattr(limits, flag_prop, True)
        setattr(limits, value_prop, native)
    except Exception as e:
        return None, f"Setting {key} raised: {e}."
    enabled = _common.read_flag(lambda: getattr(limits, flag_prop))
    if enabled is False:
        return None, (f"{key} did not take - the joint reads {flag_prop} back as false, so that "
                      "limit is not in force.")
    landed = _common.measured(lambda: getattr(limits, value_prop), scale=unit_scale, places=9)
    if landed is None or enabled is None:
        return None, None
    if abs(landed - float(wanted)) > _LIMIT_BAND:
        return None, (f"{key} did not take - {wanted} was requested and the joint reads back "
                      f"{landed} in the same units.")
    return landed, None


def _apply_limits(motion, *, min_deg=None, max_deg=None, rest_deg=None,
                  min_mm=None, max_mm=None, rest_mm=None, cm_scale=0.1):
    """Apply rotation and/or linear (slide) limits to a JointMotion, reading each one BACK off the
    joint. Returns (changed, unverified, error).

    Angular limits go on motion.rotationLimits (radians); linear go on motion.slideLimits
    (centimeters). A revolute motion has no slideLimits and a slider has no rotationLimits, so
    asking for the wrong kind errors clearly instead of silently no-op'ing. 'rest' is the resting
    value. cm_scale converts the caller's length units to cm.

    `changed` carries each limit as the JOINT READS IT BACK, never the request; `unverified` names
    the limits whose read-back could not be taken (published null in `changed`); `error` names the
    first limit whose value or enabled flag did not take, leaving `changed` holding the ones that
    landed before it."""
    changed, unverified = {}, []

    # Inverted-pair guard (the ONE home - joint_create and joint_edit both apply limits here): a
    # min above its max creates an EMPTY feasible range that silently makes the joint undrivable
    # while every health field keeps reading healthy (measured) - refuse it instead.
    if min_deg is not None and max_deg is not None and float(min_deg) > float(max_deg):
        return changed, unverified, (
            f"Rotation limits are INVERTED: min_deg ({min_deg}) > max_deg ({max_deg}) "
            "- an empty feasible range silently makes the joint undrivable. Swap them.")
    if min_mm is not None and max_mm is not None and float(min_mm) > float(max_mm):
        return changed, unverified, (
            f"Slide limits are INVERTED: min_mm ({min_mm}) > max_mm ({max_mm}) - an "
            "empty feasible range silently makes the joint undrivable. Swap them.")

    want_rot = any(v is not None for v in (min_deg, max_deg, rest_deg))
    want_lin = any(v is not None for v in (min_mm, max_mm, rest_mm))

    if want_rot:
        rl = safe(lambda: motion.rotationLimits)
        if rl is None:
            return changed, unverified, ("This joint's motion has no ROTATION limits "
    "(min_deg/max_deg/rest_deg need a revolute or cylindrical joint).")
        for (key, flag_prop, value_prop), wanted in zip(_ROT_LIMITS,
                                                        (min_deg, max_deg, rest_deg)):
            if wanted is None:
                continue
            published, lerr = _set_one_limit(rl, flag_prop, value_prop, key, wanted,
                                             math.radians(float(wanted)), _DEG_PER_RAD)
            if lerr:
                return changed, unverified, lerr
            changed[key] = published
            if published is None:
                unverified.append(key)

    if want_lin:
        sl = safe(lambda: motion.slideLimits)
        if sl is None:
            return changed, unverified, ("This joint's motion has no LINEAR/slide limits "
    "(min_mm/max_mm/rest_mm need a slider or cylindrical joint).")
        for (key, flag_prop, value_prop), wanted in zip(_LIN_LIMITS, (min_mm, max_mm, rest_mm)):
            if wanted is None:
                continue
            published, lerr = _set_one_limit(sl, flag_prop, value_prop, key, wanted,
                                             float(wanted) * cm_scale, 1.0 / cm_scale)
            if lerr:
                return changed, unverified, lerr
            changed[key] = published
            if published is None:
                unverified.append(key)

    return changed, unverified, None


def _slide_index(slide_axis, ax_name):
    """Resolve the pin_slot SLIDE direction index. Blank -> the next frame axis after the rotation
    axis (guaranteed distinct). Returns (slide_idx_or_None, error_or_None); slide_idx None means 'use
    the default in _apply_motion'."""
    s = (slide_axis or "").strip().lower()
    if not s:
        return None, None
    if s not in _AXES:
        return None, f"Unknown slide_axis '{slide_axis}'. Valid: x, y, z."
    if _AXES[s] == _AXES[ax_name]:
        return None, "For pin_slot, 'slide_axis' must differ from 'axis' (the rotation axis)."
    return _AXES[s], None


def _slide_name(slide_idx, ax_name):
    """Human name of the effective pin_slot slide axis for the response."""
    idx = slide_idx if slide_idx is not None else (_AXES[ax_name] + 1) % 3
    return ("x", "y", "z")[idx]


def handler(occurrence_one: str = "", occurrence_two: str = "", joint_type: str = "rigid",
            axis: str = "z", slide_axis: str = "", offset: float = 0.0, angle: float = 0.0,
            units: str = "mm", flip: bool = False, name: str = "", min_deg=None, max_deg=None,
            rest_deg=None, min_mm=None, max_mm=None, rest_mm=None) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design (open a document with assembly geometry).")

    pending = _pending_move_guard(design)
    if pending:
        return pending

    jtype = (joint_type or "rigid").strip().lower()
    if jtype not in _JOINT_TYPES:
        return error(f"Unknown joint_type '{joint_type}'. Valid: {', '.join(_JOINT_TYPES)}.")

    ax_name = (axis or "z").strip().lower()
    if ax_name not in _AXES:
        return error(f"Unknown axis '{axis}'. Valid: x, y, z.")

    slide_idx = None
    if jtype == "pin_slot":
        slide_idx, slide_err = _slide_index(slide_axis, ax_name)
        if slide_err:
            return error(slide_err)

    scale = _common.scale(units)
    if scale is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    n1, n2 = (occurrence_one or "").strip(), (occurrence_two or "").strip()
    if not n1 or not n2:
        return error("Provide 'occurrence_one' and 'occurrence_two' - each a Joint Origin name OR "
    "an autonomous geometry snap '<occurrence>:<snap>' "
    "(snap = origin/center/top/bottom/left/right/front/back/cylinder).")

    jo1, label1, err1 = _resolve_input(design, n1)
    jo2, label2, err2 = _resolve_input(design, n2)
    if not jo1:
        return error(err1 or f"Could not resolve joint input '{n1}'.")
    if not jo2:
        return error(err2 or f"Could not resolve joint input '{n2}'.")

    # Flush face-to-face detection, sampled PRE-ADD off the resolved JointGeometry entities (the
    # add moves the free part) - the same shared hint joint_at_geometry publishes, so a snap-based
    # create that seats two opposing planar faces gets the same 180-deg warning.
    opposing = _joints.normals_oppose(
        _joints.planar_outward_normal(safe(lambda: jo1.entityOne)),
        _joints.planar_outward_normal(safe(lambda: jo2.entityOne)))

    # Joints live on the root component (a joint between two components is owned there).
    joints = design.rootComponent.joints
    try:
        ji = joints.createInput(jo1, jo2)
    except Exception as e:
        return error(f"Could not create joint input: {e}")
    if not ji:
        return error("createInput returned nothing for these inputs.")

    did, err = _apply_motion(ji, jtype, _AXES[ax_name], slide_axis_idx=slide_idx)
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
        msg = f"Joint creation failed: {e}"
        if "input paths" in str(e).lower():
            # Fusion's "Provided input paths for joint are not valid": an input is not in assembly
            # context (typical when scripting a joint to a native sub-component JO). This tool's
            # by-name resolution proxies JOs for exactly that reason - so steer to it.
            msg += (" - an input is likely not in assembly context. Pass Joint Origins by NAME "
    "(bare or '<occurrence>:<JO name>') so this tool proxies them into the assembly; "
    "for geometry, use a fresh find_geometry handle.")
        return error(msg)
    if not joint:
        return error("joints.add returned nothing.")

    joint_name_final, rename_warning = apply_rename(joint, name)

    # Optional limits (rotation and/or linear) - applied after the joint exists so its jointMotion
    # is established. Same routing as joint_edit.
    limits_out, limits_unverified = {}, []
    if any(v is not None for v in (min_deg, max_deg, rest_deg, min_mm, max_mm, rest_mm)):
        jm = safe(lambda: joint.jointMotion)
        if jm is None:
            return error("Limits requested but this joint type has no motion to limit "
            "(rigid/inferred). Use revolute/slider/cylindrical.")
        lim_changed, limits_unverified, lim_err = _apply_limits(
            jm, min_deg=min_deg, max_deg=max_deg, rest_deg=rest_deg,
            min_mm=min_mm, max_mm=max_mm, rest_mm=rest_mm, cm_scale=scale)
        if lim_err:
            # PARTIAL SUCCESS disclosed: the joint EXISTS in the timeline and any limits applied
            # before the failing one HAVE been written (measured: a bad max_mm after a good
            # min_deg left the joint created with the rotation minimum set) - a bare error would
            # hide both, inviting a duplicate re-create.
            applied_txt = (", ".join(f"{k}={v}" for k, v in lim_changed.items())
                           if lim_changed else "none")
            return error(
                f"Joint '{joint_name_final}' WAS CREATED, but a limit failed: {lim_err} "
                f"Limits already applied before the failure: {applied_txt}. Fix the limits with "
                f"joint_edit(joint_name='{joint_name_final}', ...) or remove the joint with "
                "design_delete_feature - do NOT re-create it.")
        limits_out = lim_changed

    # A joint can be ADDED yet fail to COMPUTE: joints.add() hands back a truthy Joint while Fusion
    # marks it 'Compute Failed', and every design-wide rollup then counts it BROKEN (assembly_get's
    # broken_joints, _common.timeline_health). Measured: jointing the child of a ground_to_parent
    # occurrence returned a joint whose healthState read WARNING with "conflicts with assembly
    # relationships", moved NOTHING, and still published created:true. So the state is read back here
    # and a create that did not solve is a refusal, never a plain success. Both the joint and its
    # timeline item are asked - the measured failure showed on both - through _assert.compute_state,
    # the ONE home for that pairing, which assembly_get's rows and workspace_orient's rollup share.
    state, failure = _assert.compute_state(joint)
    if failure:
        state_label, detail = failure
        return error(
            f"Joint '{joint_name_final}' WAS CREATED but FAILED to compute (health state: "
            f"{state_label})" + (f": {detail}" if detail else " (it reports no message)")
            + f" - it does not position the parts. It REMAINS in the timeline: remove it with "
              f"design_delete_feature(name='{joint_name_final}'), or fix its inputs with joint_edit. "
              "A part locked by assembly_ground(ground_to_parent=true) - the part itself or an "
              "ancestor of it - conflicts with a joint that would move it; read the current state "
              "with assembly_get (is_healthy, broken_joints, ground_to_parent per occurrence).")

    # No failure found - but that verdict rests on a state that must actually have been READ. When
    # neither the joint nor its timeline item answers healthState, 'healthy' is null (unknown), never
    # a coerced true: an unread state is not a clean bill of health.
    healthy = True if state == "healthy" else None

    payload = {
        "created": True,
        "healthy": healthy,
        "joint_name": joint_name_final,
        "joint_type": jtype,
        "input_one": label1,
        "input_two": label2,
        "axis": (ax_name if _JOINT_TYPES[jtype][1] else None),
        "slide_axis": (_slide_name(slide_idx, ax_name) if jtype == "pin_slot" else None),
        "offset": offset if offset else None,
        "angle_deg": angle if angle else None,
        "flipped": bool(flip),
        **limits_out,
        "note": "Joint created as a timeline feature. View it with view_screenshot.",
    }
    if rename_warning:
        payload["rename_warning"] = rename_warning
    if healthy is None:
        payload["note"] += (" 'healthy' is null - the joint's compute state could not be read off "
                            "either the joint or its timeline item, so whether it SOLVED is UNKNOWN "
                            "here (it is not a 'yes'). Check it with assembly_get (is_healthy, "
                            "broken_joints) before relying on the parts' positions.")
    # The shared flush face-to-face hint (see _joints.FLIP_HINT) - parity with joint_at_geometry:
    # a snap create that seats two opposing planar faces without flip lands the part rotated
    # 180 deg, and the payload says so instead of leaving a coincident-looking embed unexplained.
    if opposing and not flip:
        payload["flip_hint"] = _joints.FLIP_HINT
    # A limit whose read-back could not be taken is published NULL above (its key is in limits_out
    # with a None value, never the request echoed back) and named here, so a caller cannot mistake
    # the absence of a mismatch for a confirmed write.
    if limits_unverified:
        payload["limits_unverified"] = limits_unverified
        payload["note"] += _unverified_limits_note(limits_unverified)
    if any(k in limits_out for k in ("rest_mm", "rest_deg")):
        payload["note"] += _REST_LIMIT_NOTE
    mp = _motion_param_names(joint)
    if mp:
        payload["model_parameters"] = mp
        payload["note"] += _OFFSET_PARAM_NOTE
    return ok(payload)


def edit_handler(joint_name: str = "", input_one: str = "", input_two: str = "",
                 joint_type: str = "", axis: str = "", slide_axis: str = "", world_axis: str = "",
                 flip=None, offset=None, angle=None, units: str = "mm",
                 rotation_deg=None, min_deg=None, max_deg=None, rest_deg=None,
                 min_mm=None, max_mm=None, rest_mm=None) -> dict:
    """See EDIT_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design.")
    joint, ambiguous = _find_joint(design, joint_name)
    if ambiguous:
        return error(ambiguous)
    if not joint:
        return error(f"No joint named '{joint_name}'. Use design_get(include=['timeline']) or check the name.")

    # Posing a joint to a drive value is joint_drive's job (the Drive Joints command); joint_edit
    # changes the joint DEFINITION (type/axis/snaps/flip/limits), not its pose. Redirect.
    if rotation_deg is not None:
        return error("Posing a joint to a rotation value is joint_drive's job. Use "
    "joint_drive(joint_name=..., angle_deg=...) to drive it; joint_edit changes the joint "
    "definition (type/axis/snaps/limits), not its pose.")

    # world_axis (re-point the motion to a TRUE WORLD axis) forces a motion re-set even if the
    # joint_type isn't changing - that's the whole point (fixing a frame-relative axis).
    wa_name = (world_axis or "").strip().lower()
    if wa_name and wa_name not in _AXES:
        return error(f"Unknown world_axis '{world_axis}'. Valid: x, y, z.")

    # Validate units (used by offset).
    if (offset is not None) and (_common.scale(units) is None):
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

    # pin_slot slide direction (validated up front, before touching the timeline).
    slide_idx = None
    if jtype == "pin_slot":
        slide_idx, slide_err = _slide_index(slide_axis, ax_name if ax_name in _AXES else "z")
        if slide_err:
            return error(slide_err)

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

    # A joint can only reference geometry/origins that exist BEFORE it in the timeline: editing rolls
    # the marker to just before the joint, where a feature CREATED AFTER the joint does not exist yet -
    # the platform raises a bare 'InternalValidationError: findObjectPath' (live-verified: rewiring a
    # slider to a JO made after it fails; rewiring a joint to a JO made before it succeeds). Catch a
    # later Joint-Origin input here and refuse with an actionable message, before rolling the timeline.
    joint_tl = safe(lambda: joint.timelineObject.index)
    for lbl, newx in (("input_one", new1), ("input_two", new2)):
        if newx is not None and _is_joint_origin(newx) and joint_tl is not None:
            jo_tl = safe(lambda nx=newx: nx.timelineObject.index)
            if jo_tl is not None and jo_tl >= joint_tl:
                return error(
                    f"Cannot rewire '{joint_name}' {lbl} to that Joint Origin: the Joint Origin is "
                    f"LATER in the timeline (position {jo_tl}) than the joint (position {joint_tl}). "
                    "Editing a joint rolls the timeline to just before it, where a later feature does "
                    "not exist yet. Create the Joint Origin before the joint, or delete the joint and "
                    "recreate it after the Joint Origin with joint_create.")

    changed = {}
    limits_unverified = []
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
            did, err = _apply_motion(joint, jtype, _AXES.get(ax_name, 2), wa_entity,
                                     slide_axis_idx=slide_idx)
            if not did:
                return error(f"Could not set {jtype} motion: {err or 'setter returned false'}.")
            changed["joint_type"] = jtype
            if wa_name:
                changed["world_axis"] = wa_name
            elif _JOINT_TYPES[jtype][1]:
                changed["axis"] = ax_name
            if jtype == "pin_slot":
                changed["slide_axis"] = _slide_name(slide_idx, ax_name if ax_name in _AXES else "z")

        if want_flip:
            joint.isFlipped = bool(flip)
            changed["flipped"] = bool(flip)

        # offset / angle are ModelParameters on the Joint - set via an explicit-units expression
        # (robust regardless of document units), matching the create-joint tool's behaviour.
        if want_offset:
            op = safe(lambda: joint.offset)
            if op is None:
                if _is_as_built_joint(joint):
                    # An AsBuiltJoint carries no offset/angle ModelParameter of any kind, whatever
                    # its motion - so no expression can position it and rest_mm only sets a motion
                    # -study equilibrium (see the note below). The parametric path is a real Joint.
                    return error(
                        f"'{joint_name}' is an AS-BUILT joint, which exposes no offset parameter "
                        "for ANY motion type - its position cannot be driven by a parameter or an "
                        "expression. Delete it (design_delete_feature) and build the pair with "
                        "joint_create instead: that joint's offset is a ModelParameter, moving "
                        "along the joint frame's Z axis.")
                return error("This joint has no offset parameter (rigid/inferred or already 0-DOF).")
            u = (units or "mm").strip().lower()
            u = "in" if u == "inch" else u
            # NOT safe()-wrapped: this is the mutation the tool was ASKED to do - let a failure raise
            # into the handler's try/except below so it's reported, not swallowed into a false success.
            op.expression = f"{_fmt_num(offset)} {u}"
            changed["offset"] = float(offset)
            changed["units"] = u

        if want_angle:
            ap = safe(lambda: joint.angle)
            if ap is None:
                if _is_as_built_joint(joint):
                    return error(
                        f"'{joint_name}' is an AS-BUILT joint, which exposes no offset/angle "
                        "ModelParameter for ANY motion type - no expression can drive it. Delete "
                        "it (design_delete_feature) and build the pair with joint_create instead.")
                return error("This joint has no angle parameter.")
            ap.expression = f"{_fmt_num(angle)} deg"
            changed["angle"] = float(angle)

        if want_limits:
            jm = safe(lambda: joint.jointMotion)
            if jm is None:
                return error("This joint has no editable motion (rigid/inferred has no limits).")
            lim_scale = _common.scale(units) or 0.1
            lim_changed, limits_unverified, lim_err = _apply_limits(
                jm, min_deg=min_deg, max_deg=max_deg, rest_deg=rest_deg,
                min_mm=min_mm, max_mm=max_mm, rest_mm=rest_mm, cm_scale=lim_scale)
            changed.update(lim_changed)
            if lim_err:
                # PARTIAL SUCCESS disclosed: every edit recorded in `changed` so far HAS landed
                # (earlier fields and any limit applied before the failing one) - a bare error
                # would hide the writes that took.
                applied_txt = (", ".join(f"{k}={v}" for k, v in changed.items())
                               if changed else "none")
                return error(f"{lim_err} Edits already applied before the failure: {applied_txt}.")
    except Exception as e:
        msg = f"Edit failed: {e}"
        if "findObjectPath" in str(e) or "InternalValidationError" in str(e):
            # A referenced input (geometry or origin) is later in the timeline than the joint, so it
            # does not exist at the rolled-back marker. Name the cause rather than ship the raw error.
            msg += (" - a re-selected input likely appears LATER in the timeline than the joint; a "
                    "joint can only reference geometry/origins created before it. Recreate the joint "
                    "after that input with joint_create.")
        return error(msg)
    finally:
        if rolled:
            # Roll the marker to the TRUE END of the timeline, not just past THIS joint. rollTo(False)
            # stops immediately AFTER the edited joint, leaving downstream features (patterns, later
            # joints) rolled OUT - they silently revert to home while still reading healthy. Setting
            # markerPosition = timeline.count restores the full model so the recompute below settles it.
            tl = safe(lambda: design.timeline)
            n = safe(lambda: tl.count, 0) or 0
            if tl is not None and n:
                safe(lambda: setattr(tl, "markerPosition", n))
            else:
                safe(lambda: joint.timelineObject.rollTo(False))

    # Editing a joint rolls the timeline marker, which can leave DOWNSTREAM features (patterns, later
    # joints) in a stale compute-failed state until a full recompute. Do it here so the caller gets a
    # settled, accurate model without a separate design_recompute call. A computeAll that RAISES is
    # not a failure of the edit (which already landed), but it must not be reported as a recompute
    # that happened - 'recomputed' publishes what actually ran.
    recompute_errors = None
    recomputed = False
    try:
        design.computeAll()
        recomputed = True
        recompute_errors, _, _ = _common.timeline_health(design)
    except Exception:
        pass

    out = {"edited": True, "joint_name": safe(lambda: joint.name), "changes": changed}
    # surface the most-asked fields at top level for convenience
    for key in ("input_one", "input_two", "joint_type", "axis", "slide_axis", "world_axis", "flipped",
                       "offset", "angle", "min_deg", "max_deg", "rest_deg", "min_mm", "max_mm", "rest_mm"):
        if key in changed:
            out[key] = changed[key]
    out["recomputed"] = recomputed
    if recompute_errors:
        out["timeline_errors_after"] = recompute_errors
        out["note"] = ("Joint edited + recomputed, but the timeline still has errored feature(s) "
                       f"({', '.join(recompute_errors)}) - the edit may over-constrain something.")
    elif recomputed:
        out["note"] = ("Joint edited in place + full recompute (downstream features settled). "
                       "view_screenshot to view.")
    else:
        out["note"] = ("Joint edited in place, but the full recompute RAISED - downstream features "
                       "may be unsettled and their health unread. Run design_recompute and check "
                       "workspace_orient before trusting the model state.")
    # A limit whose read-back could not be taken is published NULL in 'changes' (and at top level),
    # never the request echoed back, and named here so the null is not read as a confirmed write.
    if limits_unverified:
        out["limits_unverified"] = limits_unverified
        out["note"] += _unverified_limits_note(limits_unverified)
    if any(k in changed for k in ("rest_mm", "rest_deg")):
        out["note"] += _REST_LIMIT_NOTE
    mp = _motion_param_names(joint)
    if mp:
        out["model_parameters"] = mp
        out["note"] += _OFFSET_PARAM_NOTE
    # Suppressed-joint disclosure: the edit is REAL (a limits write lands) but the joint is
    # INERT while suppressed - measured, an edited suppressed joint left its part 47mm from the
    # jointed placement with no mention. BOTH flags are consulted and OR'd (live-verified on
    # 2705.0.87: Joint.isSuppressed keeps reading False when the suppression was set on the
    # TIMELINE item, so the entity flag alone under-reports). read_flag, not a coerced False: two
    # unreadable flags stay undisclosed rather than asserting active.
    sup = (_common.read_flag(lambda: joint.isSuppressed) or
           _common.read_flag(lambda: joint.timelineObject.isSuppressed))
    if sup:
        out["suppressed"] = True
        out["note"] += (" WARNING: this joint is SUPPRESSED - the edit landed on the definition but "
                        "the joint is INERT and positions nothing until it is unsuppressed "
                        "(design_edit_timeline action='suppress', suppressed=false).")
    return ok(out)


TOOL_DESCRIPTION = (
    "Create a Joint between two inputs. Each input ('occurrence_one'/'occurrence_two'), most precise "
    "first: a find_geometry handle (joints AT that exact face/edge - a real offset); a Joint Origin "
    "name - bare ('Center of Model') or scoped '<occurrence>:<JO name>' for a JO inside an inserted/"
    "referenced part (the tool proxies it into assembly context; do NOT script this yourself); or a "
    "snap-string '<occurrence>:<snap>' where snap = origin | center (largest planar face) | top | "
    "bottom | cylinder (cyl-face axis), e.g. 'Boom:1:top'. ':origin' collapses to the part origin AND "
    "aligns its FULL local frame (un-rotating a pre-rotated part) - use a handle for a real offset. "
    "Creating a joint MOVES the free (ungrounded) part so its snap/JO point lands on the other "
    "input's location - do not pre-place it. 'joint_type' = rigid (default)/revolute/slider/"
    "cylindrical/planar/ball/pin_slot; 'axis' is FRAME-relative - an axis of the joint geometry's "
    "frame, not a world one - so re-point a joint that pivots the wrong way with "
    "joint_edit(world_axis=...). For pin_slot it is the rotation axis, with 'slide_axis' the "
    "perpendicular slide. "
    "Optional 'offset' ('units'=mm/cm/in), 'angle' (deg), 'flip'."
)

tool = (
    Tool.create_with_string_input(
        name="joint_create",
        description=TOOL_DESCRIPTION,
        input_param_name="occurrence_one",
        input_param_description="First input: a find_geometry handle, a Joint Origin name (bare or '<occurrence>:<JO name>'), or a snap '<occurrence>:<snap>' - see the tool description for the accepted forms.",
    )
    .add_input_property("occurrence_two", {"type": "string",
            "description": "Second input: same forms as occurrence_one."})
    .add_input_property(*_inputs.joint_motion(default="rigid", options=_MOTIONS).as_property())
    .add_input_property(*_inputs.frame_axis("axis", default="z",
            description="Motion axis for types that need one (FRAME-relative; for pin_slot: the rotation axis).").as_property())
    .add_input_property(*_inputs.frame_axis("slide_axis", default="",
            description="pin_slot only: the perpendicular SLIDE direction (default = the next frame axis; must differ from 'axis').").as_property())
    .add_input_property("offset", {"type": "number", "description": "Offset distance (in 'units'; default 0)."})
    .add_input_property("angle", {"type": "number", "description": "Angle in degrees (default 0)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("flip", {"type": "boolean", "description": "Reverse the joint direction (default false)."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the joint."})
    .add_input_property("min_deg", {"type": "number", "description": "Rotation limit min (degrees) - revolute/cylindrical."})
    .add_input_property("max_deg", {"type": "number", "description": "Rotation limit max (degrees) - revolute/cylindrical."})
    .add_input_property("rest_deg", {"type": "number", "description": "Rotation rest value (degrees) - revolute/cylindrical."})
    .add_input_property("min_mm", {"type": "number", "description": "Linear/slide limit min (in 'units') - slider/cylindrical."})
    .add_input_property("max_mm", {"type": "number", "description": "Linear/slide limit max (in 'units') - slider/cylindrical."})
    .add_input_property("rest_mm", {"type": "number", "description": "Linear/slide rest value (in 'units') - slider/cylindrical."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.FeatureHealthy(), _assert.ChildGeometryMoved()])


EDIT_DESCRIPTION = (
"Edit an existing joint in place. 'joint_name' selects it; pass any subset to change: 'input_one'/"
"'input_two' re-select the snap inputs (a Joint Origin name or '<occurrence>:<snap>' = origin/center/"
"top/bottom/cylinder); 'joint_type' (rigid/revolute/slider/cylindrical/planar/ball/pin_slot) + 'axis' "
"(+ 'slide_axis' for pin_slot) redefine the motion; 'world_axis' re-points rotation/slide to a TRUE world axis (fixes a "
"joint pivoting about the wrong axis when the snap frame isn't world-aligned); 'flip'; 'offset' "
"('units') + 'angle' (deg); rotation limits 'min_deg'/'max_deg'/'rest_deg' (revolute/cylindrical) and "
"linear limits 'min_mm'/'max_mm'/'rest_mm' (slider/cylindrical). To DRIVE a joint to a pose use "
"joint_drive, not this."
)
edit_tool = (
    Tool.create_simple(name="joint_edit", description=EDIT_DESCRIPTION)
    .add_input_property("joint_name", {"type": "string", "description": "Name of the joint to edit."})
    .add_input_property("input_one", {"type": "string",
            "description": "New first input: Joint Origin name OR '<occurrence>:<snap>'."})
    .add_input_property("input_two", {"type": "string",
            "description": "New second input: Joint Origin name OR '<occurrence>:<snap>'."})
    .add_input_property(*_inputs.joint_motion(default="rigid", options=_MOTIONS, description="Redefine the joint motion type.").as_property())
    .add_input_property(*_inputs.frame_axis("axis", default="z",
            description="Motion axis for types that need one (FRAME-relative; for pin_slot: the rotation axis).").as_property())
    .add_input_property(*_inputs.frame_axis("slide_axis", default="",
            description="pin_slot only: the perpendicular SLIDE direction (default = the next frame axis; must differ from 'axis').").as_property())
    .add_input_property(*_inputs.frame_axis("world_axis", default="",
            description="Re-point the motion to a TRUE WORLD axis via a construction axis - fixes a joint that pivots about the wrong world axis because the snap frame isn't world-aligned. Re-applies the current motion type if joint_type is omitted.").as_property())
    .add_input_property("flip", {"type": "boolean", "description": "Toggle the joint direction."})
    .add_input_property("offset", {"type": "number", "description": "Set the joint ANCHOR offset (the offset ModelParameter, in 'units') - along the joint FRAME'S Z axis; NOT a slider's slide value (joint_drive poses that)."})
    .add_input_property("angle", {"type": "number", "description": "Set the joint angle between the inputs (degrees)."})
    .add_input_property(*_inputs.units_property(description="Units for 'offset'."))
    # rotation_deg is intentionally NOT exposed: the handler still accepts the kwarg and returns a
    # helpful redirect if passed, but advertising a parameter whose only behavior is to error wastes
    # context. To pose a joint, use joint_drive.
    .add_input_property("min_deg", {"type": "number", "description": "Rotation limit min (degrees) - revolute/cylindrical."})
    .add_input_property("max_deg", {"type": "number", "description": "Rotation limit max (degrees) - revolute/cylindrical."})
    .add_input_property("rest_deg", {"type": "number", "description": "Rotation rest value (degrees) - revolute/cylindrical."})
    .add_input_property("min_mm", {"type": "number", "description": "Linear/slide limit min (in 'units') - slider/cylindrical."})
    .add_input_property("max_mm", {"type": "number", "description": "Linear/slide limit max (in 'units') - slider/cylindrical."})
    .add_input_property("rest_mm", {"type": "number", "description": "Linear/slide rest value (in 'units') - slider/cylindrical."})
    .strict_schema()
)
edit_item = Item.create_tool_item(tool=edit_tool, write="write", handler=edit_handler, run_on_main_thread=True)


def register_tool():
    register(item)
    register(edit_item)
