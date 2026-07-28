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

MAP_BLURB = ("camera-orientation table for the standard named views - view_direction/"
             "look_direction/up_vector plus the true-orthographic-face set + "
             "apply_named_view/capture_png_b64 (the orient + refresh-then-grab capture mechanics)")

# eye - target direction per named view. Not pre-normalized (the iso corners are (+-1,+-1,+-1));
# view_direction()/look_direction() normalize on read.
VIEW_DIRECTIONS = {
    "front": (0, -1, 0),
    "back": (0, 1, 0),
    "top": (0, 0, 1),
    "bottom": (0, 0, -1),
    "right": (1, 0, 0),
    "left": (-1, 0, 0),
    "iso-top-right": (1, -1, 1),
    "iso-top-left": (-1, -1, 1),
    "iso-bottom-right": (1, 1, 1),
    "iso-bottom-left": (-1, 1, 1),
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


def capture_png_b64(vp, width, height, prefix="fe_mcp_shot"):
    """Grab the viewport as a base64 PNG string via a temp file (always removed). Forces a viewport
    refresh FIRST - the capture can otherwise race an un-refreshed frame (a camera/visibility change
    that hasn't drawn yet reads as blank). Returns (b64, error)."""
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix=".png")
        os.close(fd)
        _common.safe(lambda: vp.refresh())
        did = vp.saveAsImageFile(temp_path, width, height)
        if not did or not os.path.exists(temp_path):
            return None, "Viewport capture failed (saveAsImageFile returned false)."
        with open(temp_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
