# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Camera-orientation table for the standard named views, plus the shared capture mechanics."""

import base64
import os
import tempfile

import adsk.core

from . import _common

MAP_BLURB = (
    "view_direction/look_direction/up_vector/is_ortho_face - camera vectors for a named view; "
    "apply_named_view/capture_png_b64 - orient and grab the viewport; "
    "standoff_distance/STANDOFF_FALLBACK_CM - the eye-target standoff an orient rebuilds from; "
    "DISPLAY_FOLDERS/all_display_components - toggling non-body clutter; "
    "keep_visible/isolate_for_fit/restore_message - framing on one occurrence")


# Display category -> the Component FOLDER bulb controlling it. The folder bulb is separate from
# each entity's own bulb, so toggling it never disturbs per-entity state.
DISPLAY_FOLDERS = {
    "sketches": "isSketchFolderLightBulbOn",
    "construction": "isConstructionFolderLightBulbOn",
    "origins": "isOriginFolderLightBulbOn",
    "joints": "isJointsFolderLightBulbOn",
}


def all_display_components(design):
    """Every component ONCE (root + allComponents), keyed by _common.native_identity."""
    # entityToken is document-local: every document's root component carries the same one, so a
    # bare-token key merges the roots of distinct source documents. native_identity pairs the token
    # with the source-document urn; `or id(c)` over-counts an identity-less component, never merges.
    root = _common.safe(lambda: design.rootComponent)
    comps = ([root] if root is not None else []) + list(
        _common.safe(lambda: design.allComponents, []) or [])
    out, seen = [], set()
    for c in comps:
        key = _common.native_identity(c) or id(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def keep_visible(o_path, target_path):
    """True if an occurrence path IS the target path, an ANCESTOR of it, or a DESCENDANT of it."""
    # Nesting reads off fullPathName ('Frame:1+Pedestal:1', '+' per level). The API mints a fresh
    # occurrence proxy on each access, so Python `is` is never true across two walks.
    if not o_path or not target_path:
        return False
    return (o_path == target_path
            or target_path.startswith(o_path + "+")     # o is an ancestor of the target
            or o_path.startswith(target_path + "+"))     # o is a descendant of the target


def isolate_for_fit(name, ref):
    """Hide every occurrence outside the named one's subtree so a fit frames it; 'ref' is the
    caller's OccurrenceRef. Returns (restore, target, error) - restore() answers the names whose
    bulb it could not put back."""
    design = _common.design()
    root = _common.safe(lambda: design.rootComponent) if design else None
    if not root:
        return None, None, f"{ref.name}: no active design to resolve '{name}' against."
    target, err = ref.resolve(name)
    if target is None:
        return None, None, err
    target_path = _common.safe(lambda: target.fullPathName)
    # root.allOccurrences RAISES on a design holding an unresolved external reference; the shared
    # census survives it.
    occs = _common.all_occurrences(design)
    prev = []
    for o in occs:
        if keep_visible(_common.safe(lambda o=o: o.fullPathName), target_path):
            continue
        was = _common.safe(lambda o=o: o.isLightBulbOn)
        if was:
            prev.append(o)
            _common.safe(lambda o=o: setattr(o, "isLightBulbOn", False))

    # Viewport.fit() frames every VISIBLE entity, so construction geometry outside the isolated
    # subtree still blows the frame open; the folder bulbs switch that clutter off design-wide.
    folder_prev = []                       # (component, attr) - only bulbs we moved
    for comp in all_display_components(design):
        for attr in DISPLAY_FOLDERS.values():
            if _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr)):
                folder_prev.append((comp, attr))
                _common.safe(lambda comp=comp, attr=attr: setattr(comp, attr, False))

    def restore():
        stuck = []
        for o in prev:
            _common.safe(lambda o=o: setattr(o, "isLightBulbOn", True))
            if _common.safe(lambda o=o: o.isLightBulbOn) is not True:
                stuck.append(_common.safe(lambda o=o: o.fullPathName)
                             or _common.safe(lambda o=o: o.name) or "?")
        for comp, attr in folder_prev:
            _common.safe(lambda comp=comp, attr=attr: setattr(comp, attr, True))
            if _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr)) is not True:
                stuck.append(f"{_common.safe(lambda comp=comp: comp.name) or '?'}:{attr}")
        return stuck
    return restore, target, None


def restore_message(restore, label, purpose):
    """Run an isolate_for_fit restore and return the sentence naming what it could NOT put back, or
    None when everything came back."""
    if not restore:
        return None
    try:
        stuck = restore() or []
    except Exception as e:
        stuck = [f"the restore raised: {e}"]
    if not stuck:
        return None
    return (f"{label} hid the other occurrences {purpose} and could NOT turn "
            f"{len(stuck)} of them back on: {', '.join(str(s) for s in stuck[:5])}. "
            "The document is left with those hidden - view_set(action='show', target=...) "
            "restores them.")

# eye - target direction per named view, not pre-normalized; view_direction() normalizes on read.
# Fusion is Z-up: a positive z aims the camera down at the TOP face, so an iso-bottom-* entry
# carries a NEGATIVE z and mirrors its iso-top-* twin across z.
VIEW_DIRECTIONS = {
    "front": (0, -1, 0),
    "back": (0, 1, 0),
    "top": (0, 0, 1),
    "bottom": (0, 0, -1),
    "right": (1, 0, 0),
    "left": (-1, 0, 0),
    "iso-top-right": (1, -1, 1),
    "iso-top-left": (-1, -1, 1),
    "iso-bottom-right": (1, -1, -1),
    "iso-bottom-left": (-1, -1, -1),
}

# Up vector per named view - the SAME for view_direction and look_direction.
UP_VECTORS = {
    "front": (0, 0, 1), "back": (0, 0, 1),
    "top": (0, 1, 0), "bottom": (0, 1, 0),
    "right": (0, 0, 1), "left": (0, 0, 1),
    "iso-top-right": (0, 0, 1), "iso-top-left": (0, 0, 1),
    "iso-bottom-right": (0, 0, 1), "iso-bottom-left": (0, 0, 1),
}

# The 6 true orthographic faces - these force an orthographic camera; the iso corners keep
# whatever camera type is already active.
ORTHO_FACE_VIEWS = {"front", "back", "top", "bottom", "right", "left"}


def _normalize(vec):
    x, y, z = vec
    ss = x * x + y * y + z * z
    inv = ss ** -0.5 if ss else 1.0
    return (x * inv, y * inv, z * inv)


def view_direction(name):
    """Unit (eye - target) direction for a named view, or None if 'name' is not a known view."""
    v = VIEW_DIRECTIONS.get(name)
    return _normalize(v) if v is not None else None


def look_direction(name):
    """Unit (target - eye) direction - the negation of view_direction() - for a named view."""
    v = view_direction(name)
    if v is None:
        return None
    x, y, z = v
    return (-x, -y, -z)


def up_vector(name):
    """Up vector for a named view, or None if 'name' is not a known view."""
    return UP_VECTORS.get(name)


def is_ortho_face(name):
    """True if 'name' is one of the 6 true orthographic faces (front/back/top/bottom/left/right)."""
    return name in ORTHO_FACE_VIEWS


# The eye-target standoff used when the camera's own distance is UNUSABLE - it did not read, or it
# read non-positive, which rebuilds the eye ON the target and leaves the view no direction at all.
STANDOFF_FALLBACK_CM = 100.0


def standoff_distance(cam):
    """(the eye-target distance to rebuild the eye at, the fallback it stands in for or None) - the
    camera's own distance, else STANDOFF_FALLBACK_CM. The ONE standoff read every orient shares."""
    dist = _common.safe(lambda: cam.eye.distanceTo(cam.target))
    if dist is None or dist <= 0:
        return STANDOFF_FALLBACK_CM, STANDOFF_FALLBACK_CM
    return dist, None


def apply_named_view(vp, name):
    """Point the viewport's camera at a named view and fit; no-op for an unknown/'current' name.
    Returns STANDOFF_FALLBACK_CM when the camera's eye-target distance did not read or read
    non-positive, else None."""
    # Exact eye/up vectors, not a viewOrientation assignment: that leaves a tilt off world axes.
    look = look_direction(name)
    if look is None:
        return None
    up = up_vector(name)
    cam = vp.camera
    tgt = cam.target
    dist, fallback = standoff_distance(cam)
    cam.eye = adsk.core.Point3D.create(
        tgt.x - look[0] * dist, tgt.y - look[1] * dist, tgt.z - look[2] * dist)
    cam.upVector = adsk.core.Vector3D.create(*up)
    if is_ortho_face(name):
        cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    vp.camera = cam                   # assigning back applies the change
    vp.fit()
    return fallback


def _write_image(vp, path, width, height, transparent_background, anti_aliased):
    """Render the viewport to 'path' at width x height; returns (did, name of the overload used)."""
    # saveAsImageFileWithOptions is the only overload carrying isBackgroundTransparent/isAntiAliased.
    if transparent_background is None and anti_aliased is None:
        return bool(vp.saveAsImageFile(path, width, height)), "saveAsImageFile"
    opts = adsk.core.SaveImageFileOptions.create(path)
    # A fresh options object starts at width/height 0, so the size is assigned explicitly.
    opts.width = width
    opts.height = height
    if transparent_background is not None:
        opts.isBackgroundTransparent = bool(transparent_background)
    if anti_aliased is not None:
        opts.isAntiAliased = bool(anti_aliased)
    return bool(vp.saveAsImageFileWithOptions(opts)), "saveAsImageFileWithOptions"


def capture_png_b64(vp, width, height, prefix="fe_mcp_shot", transparent_background=None,
                    anti_aliased=None):
    """Grab the viewport as a base64 PNG via a temp file (always removed); returns (b64, error)."""
    # The refresh below is required: a capture can otherwise race an un-refreshed frame and a
    # camera or visibility change that has not drawn yet reads as blank.
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix=".png")
        os.close(fd)
        _common.safe(lambda: vp.refresh())
        did, api = _write_image(vp, temp_path, width, height, transparent_background, anti_aliased)
        if not did or not os.path.exists(temp_path):
            return None, f"Viewport capture failed ({api} returned false)."
        with open(temp_path, "rb") as f:
            raw = f.read()
        # mkstemp already created the file, so existence proves nothing about the render.
        if not raw:
            return None, f"Viewport capture failed ({api} wrote a 0-byte file)."
        return base64.b64encode(raw).decode("ascii"), None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
