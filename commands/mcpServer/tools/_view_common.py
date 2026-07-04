# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared camera-orientation table for the standard named views (top/bottom/front/back/left/right
plus the four iso corners; Fusion is Z-up). VIEW_DIRECTIONS is the eye-target direction; consumers
that instead need the opposite-sign LOOK direction (target-eye) call look_direction(). See
docs/fusion-api-notes.md "Viewport / camera" for the API gotcha this table works around.
"""

MAP_BLURB = ("camera-orientation table for the standard named views - view_direction/"
             "look_direction/up_vector plus the true-orthographic-face set")

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
