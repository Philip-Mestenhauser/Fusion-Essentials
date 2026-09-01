# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared camera-orientation table for the standard named views (top/bottom/front/back/left/right
plus the four iso corners; Fusion is Z-up). VIEW_DIRECTIONS is the eye-target direction; consumers
that instead need the opposite-sign LOOK direction (target-eye) call look_direction(). The table
exists because assigning camera.viewOrientation does not reliably move the eye/target - consumers
set explicit vectors instead. apply_named_view/capture_png_b64 are the shared orient-then-grab
mechanics the screenshot tools sit on.
"""

import base64
import os
import tempfile

import adsk.core

from . import _common

MAP_BLURB = (
    "view_direction/look_direction/up_vector + the true-orthographic-face set - the "
    "camera-orientation table for the standard named views; apply_named_view/capture_png_b64 - "
    "the orient + refresh-then-grab capture mechanics; DISPLAY_FOLDERS/all_display_components - "
    "the category -> Component folder-bulb map and the deduped component walk, for any toggle of "
    "non-body clutter; keep_visible/isolate_for_fit/restore_message - the ONE "
    "frame-on-one-occurrence isolate and its restore-with-disclosure, for a viewport fit that must "
    "frame the target rather than the whole scene")


# Display category -> the Component FOLDER bulb that controls it (one switch per component; the
# folder bulb is separate from each entity's own bulb, so toggling it never disturbs per-entity
# state). The ONE map every non-body visibility control reads.
DISPLAY_FOLDERS = {
    "sketches": "isSketchFolderLightBulbOn",
    "construction": "isConstructionFolderLightBulbOn",
    "origins": "isOriginFolderLightBulbOn",
    "joints": "isJointsFolderLightBulbOn",
}


def all_display_components(design):
    """Every component ONCE (root + allComponents) - the walk a design-wide folder-bulb toggle runs.

    De-duplicated by _common.native_identity, the (token, source-document urn) pair, because the two
    simpler keys are each wrong in one direction. Python identity SPLITS: allComponents holds a root
    proxy distinct from rootComponent, so the root would be toggled twice. The bare entityToken
    MERGES: a token is DOCUMENT-LOCAL and every document's ROOT component carries the same one -
    MEASURED on a CAM job assembled from 7 source documents, where 7 distinct root components read
    one byte-identical token. Keyed on that token those 7 collapse to 1 entry, and the folder bulbs
    of the other 6 are never written at all.

    The `or id(c)` last resort keys an identity-less component apart from every other one: it
    over-counts, never merges."""
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
    """Keep an occurrence visible during a frame-on-one isolate if it IS the target, an ANCESTOR of it,
    or a DESCENDANT of it. Hiding an ancestor hides the nested target (BLANK view); hiding a descendant
    drops part of the target's own subtree. Nesting is by fullPathName ('Frame:1+Pedestal:1', '+' per
    level). Comparison is by PATH, never Python `is` - the API mints a fresh occurrence proxy on each
    access, so `o is target` is never true across two walks and would hide the target itself."""
    if not o_path or not target_path:
        return False
    return (o_path == target_path
            or target_path.startswith(o_path + "+")     # o is an ancestor of the target
            or o_path.startswith(target_path + "+"))     # o is a descendant of the target


def isolate_for_fit(name, ref):
    """Temporarily hide every occurrence that is NOT the named one, its ancestors, or its descendants,
    so a viewport fit frames just the named one. 'ref' is the caller's own OccurrenceRef (its name
    words the errors). Returns (restore_callable, target_occurrence, error) - error is set (and the
    other two None) when the occurrence didn't resolve (including an ambiguous name, which names the
    candidates); the target feeds the bodies-only camera fit.

    The restore callable returns the NAMES of the occurrences whose bulb it could not put back
    (empty when everything was restored): framing mutates visibility, so a restore that silently
    failed would leave the document changed by a call that only meant to move the camera.
    """
    design = _common.design()
    root = _common.safe(lambda: design.rootComponent) if design else None
    if not root:
        return None, None, f"{ref.name}: no active design to resolve '{name}' against."
    # Resolve via the caller's OccurrenceRef kind (fullPathName-preferring, ambiguity-refusing) so an
    # ambiguous name doesn't silently frame the wrong instance.
    target, err = ref.resolve(name)
    if target is None:
        return None, None, err
    target_path = _common.safe(lambda: target.fullPathName)
    # The shared census, not a bare root.allOccurrences: that property RAISES on a design holding an
    # unresolved external reference, and an empty walk would hide NOTHING while the view is published
    # as framed on the target.
    occs = _common.all_occurrences(design)
    prev = []
    for o in occs:
        if keep_visible(_common.safe(lambda o=o: o.fullPathName), target_path):
            continue
        was = _common.safe(lambda o=o: o.isLightBulbOn)
        if was:
            prev.append(o)
            _common.safe(lambda o=o: setattr(o, "isLightBulbOn", False))

    # ALSO hide non-body geometry design-wide for the fit: Viewport.fit() frames every VISIBLE entity,
    # and construction geometry owned by the fitted component (measured: a datum plane) blows the
    # frame to the whole scene while the occurrence isolation holds. The per-component display
    # FOLDER bulbs (DISPLAY_FOLDERS) switch sketches/construction/origins/joints off in one write
    # each without touching any entity's own bulb.
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
    None. A restore that did not take leaves the document changed by a call that only framed the
    camera, and the caller is the only one who can undo it - so EVERY exit that reaches the isolate
    runs this, not just the successful one. Returns None when there was nothing to restore or
    everything came back."""
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

# eye - target direction per named view. Not pre-normalized (the iso corners are (+-1, -1, +-1));
# view_direction()/look_direction() normalize on read. An iso-bottom-* entry's z is NEGATIVE - the
# camera sits UNDER the model and mirrors its iso-top-* twin across z. Fusion is Z-up, so a positive
# z there aims the camera down at the TOP face: measured on 2705.1.4 against a plate carrying a
# through-pocket on its underside, an eye-target of (1,1,1) rendered the top face and (1,-1,-1)
# rendered the pocket.
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

# Up vector per named view - the SAME for view_direction and look_direction (only the primary
# direction flips sign between the two conventions; up does not).
UP_VECTORS = {
    "front": (0, 0, 1), "back": (0, 0, 1),
    "top": (0, 1, 0), "bottom": (0, 1, 0),
    "right": (0, 0, 1), "left": (0, 0, 1),
    "iso-top-right": (0, 0, 1), "iso-top-left": (0, 0, 1),
    "iso-bottom-right": (0, 0, 1), "iso-bottom-left": (0, 0, 1),
}

# The 6 true orthographic faces (force an orthographic camera for zero perspective parallax); the
# iso corners keep whatever camera type is already active.
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


def apply_named_view(vp, name):
    """Point the viewport's camera at a named view and fit. The camera is set to EXACT world-axis
    eye/target/up vectors so the view is GUARANTEED square to world (a rotate-toward/viewOrientation
    assignment leaves a tilt that distorts an orthographic read); target keeps the current focus, eye
    is placed along the exact look direction, and the 6 true faces force an orthographic camera.
    Raises on failure (the caller words its own error); no-op for an unknown/'current' name."""
    look = look_direction(name)
    if look is None:
        return
    up = up_vector(name)
    cam = vp.camera
    tgt = cam.target
    dist = _common.safe(lambda: cam.eye.distanceTo(cam.target), 100.0) or 100.0
    cam.eye = adsk.core.Point3D.create(
        tgt.x - look[0] * dist, tgt.y - look[1] * dist, tgt.z - look[2] * dist)
    cam.upVector = adsk.core.Vector3D.create(*up)
    if is_ortho_face(name):
        cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    vp.camera = cam                   # assigning back applies the change
    vp.fit()


def _write_image(vp, path, width, height, transparent_background, anti_aliased):
    """Render the viewport to 'path' at width x height. Both switches unset -> the plain
    Viewport.saveAsImageFile(path, width, height) overload. Either set -> SaveImageFileOptions +
    Viewport.saveAsImageFileWithOptions, the only overload carrying isBackgroundTransparent /
    isAntiAliased. Returns (did, api_name) - api_name names which overload answered."""
    if transparent_background is None and anti_aliased is None:
        return bool(vp.saveAsImageFile(path, width, height)), "saveAsImageFile"
    opts = adsk.core.SaveImageFileOptions.create(path)
    # A fresh options object starts at width/height 0 (live-measured:
    # behavior.save_image_options_defaults), so the requested size is assigned explicitly.
    opts.width = width
    opts.height = height
    if transparent_background is not None:
        opts.isBackgroundTransparent = bool(transparent_background)
    if anti_aliased is not None:
        opts.isAntiAliased = bool(anti_aliased)
    return bool(vp.saveAsImageFileWithOptions(opts)), "saveAsImageFileWithOptions"


def capture_png_b64(vp, width, height, prefix="fe_mcp_shot", transparent_background=None,
                    anti_aliased=None):
    """Grab the viewport as a base64 PNG string via a temp file (always removed). Forces a viewport
    refresh FIRST - the capture can otherwise race an un-refreshed frame (a camera/visibility change
    that hasn't drawn yet reads as blank). transparent_background/anti_aliased are tri-state: None
    leaves the plain capture path untouched, True/False routes through the options overload.
    Returns (b64, error)."""
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
        # mkstemp already created the file, so existence proves nothing about the render - a
        # zero-byte file is a capture that reported success and wrote no image.
        if not raw:
            return None, f"Viewport capture failed ({api} wrote a 0-byte file)."
        return base64.b64encode(raw).decode("ascii"), None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
