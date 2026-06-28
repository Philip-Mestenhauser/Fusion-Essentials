# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP building block: capture the Fusion viewport so the agent can visually review results.

Returns the image as an MCP image content block (base64 PNG). Optionally reorients
the camera first (top/front/iso/etc.) and fits the view.

Grounded in the Fusion API:
  - app.activeViewport.saveAsImageFile(path, width, height) -> bool (re-renders)
  - viewport.camera / camera.viewOrientation = ViewOrientations.* + viewport.fit()
"""

import base64
import os
import tempfile

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _error, _safe

app = adsk.core.Application.get()

# Map friendly names -> ViewOrientations enum values (from the API reference).
_ORIENTATIONS = {
    "current": None,  # leave the camera as-is
    "top": adsk.core.ViewOrientations.TopViewOrientation,
    "bottom": adsk.core.ViewOrientations.BottomViewOrientation,
    "front": adsk.core.ViewOrientations.FrontViewOrientation,
    "back": adsk.core.ViewOrientations.BackViewOrientation,
    "left": adsk.core.ViewOrientations.LeftViewOrientation,
    "right": adsk.core.ViewOrientations.RightViewOrientation,
    "iso-top-left": adsk.core.ViewOrientations.IsoTopLeftViewOrientation,
    "iso-top-right": adsk.core.ViewOrientations.IsoTopRightViewOrientation,
    "iso-bottom-left": adsk.core.ViewOrientations.IsoBottomLeftViewOrientation,
    "iso-bottom-right": adsk.core.ViewOrientations.IsoBottomRightViewOrientation,
}

_MAX_DIM = 4096


def _isolate_for_fit(name):
    """Temporarily hide every other occurrence so vp.fit() frames just the named one. Returns a
    restore() callable, or None if no occurrence matched. Best-effort + non-destructive."""
    import adsk.fusion
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = _safe(lambda: design.rootComponent) if design else None
    if not root:
        return None
    occs = _safe(lambda: list(root.allOccurrences)) or []
    target = None
    for o in occs:
        nm = _safe(lambda o=o: o.name) or ""
        fp = _safe(lambda o=o: o.fullPathName) or ""
        if nm == name or fp == name:
            target = o
            break
    if target is None:                                     # substring fallback
        for o in occs:
            if name.lower() in (_safe(lambda o=o: o.name) or "").lower():
                target = o
                break
    if target is None:
        return None
    prev = []
    for o in occs:
        if o is target:
            continue
        was = _safe(lambda o=o: o.isLightBulbOn)
        if was:
            prev.append(o)
            _safe(lambda o=o: setattr(o, "isLightBulbOn", False))

    def restore():
        for o in prev:
            _safe(lambda o=o: setattr(o, "isLightBulbOn", True))
    return restore


def handler(view: str = "current", width: int = 800, height: int = 600,
            zoom: float = 1.0, fit_to: str = "") -> dict:
    """Capture the active viewport and return it as a base64 PNG image block."""
    view = (view or "current").strip().lower()
    if view not in _ORIENTATIONS:
        return _error(f"Unknown view '{view}'. Valid: {', '.join(_ORIENTATIONS)}")

    try:
        width = max(1, min(int(width), _MAX_DIM))
        height = max(1, min(int(height), _MAX_DIM))
    except Exception:
        width, height = 800, 600

    vp = app.activeViewport
    if not vp:
        return _error("No active viewport (is a document open?).")

    # 'fit_to' frames the camera on ONE occurrence (best-effort: temporarily isolate it so fit()
    # tightens onto it, then restore visibility). Returns the original visibility so a read tool
    # leaves no permanent change.
    saved_camera = None
    restore_fit_to = None
    want_fit = (fit_to or "").strip()
    if want_fit:
        restore_fit_to = _isolate_for_fit(want_fit)
        if restore_fit_to is None:
            return _error(f"fit_to: no occurrence matched '{want_fit}'. Use design_get_tree to list.")

    # Reorient the camera if a specific view was requested, saving the user's current
    # camera so we can restore it afterward (a read tool shouldn't permanently change
    # the user's view as a side effect).
    if _ORIENTATIONS[view] is not None or want_fit or (zoom and zoom != 1.0):
        try:
            saved_camera = vp.camera          # snapshot of the user's current view
            cam = vp.camera
            if _ORIENTATIONS[view] is not None:
                cam.viewOrientation = _ORIENTATIONS[view]
            vp.camera = cam                   # assigning back applies the change
            vp.fit()
            # zoom: scale the camera-to-target distance after fitting (>1 zooms OUT, <1 zooms IN).
            z = float(zoom or 1.0)
            if z and z != 1.0 and z > 0:
                cam = vp.camera
                try:
                    cam.viewExtents = cam.viewExtents * z   # smaller extents = zoomed in
                    vp.camera = cam
                except Exception:
                    pass
        except Exception as e:
            if restore_fit_to:
                restore_fit_to()
            return _error(f"Failed to set view '{view}': {e}")

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix="fe_mcp_shot", suffix=".png")
        os.close(fd)
        ok = vp.saveAsImageFile(temp_path, width, height)
        if not ok or not os.path.exists(temp_path):
            return _error("Viewport capture failed (saveAsImageFile returned false).")
        with open(temp_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return {
            "content": [{"type": "image", "data": b64, "mimeType": "image/png"}],
            "isError": False,
        }
    except Exception as e:
        return _error(f"Screenshot error: {e}")
    finally:
        # Restore the user's original camera if we changed it.
        if saved_camera is not None:
            try:
                vp.camera = saved_camera
            except Exception:
                pass
        # Restore visibility if fit_to isolated something.
        if restore_fit_to:
            try:
                restore_fit_to()
            except Exception:
                pass
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


TOOL_DESCRIPTION = (
    "Capture a screenshot of the current Fusion viewport and return it as an image "
    "so you can visually inspect the model and verify your work. Optionally set "
    "'view' to reorient the camera: current (default), top, bottom, front, back, "
    "left, right, iso-top-left, iso-top-right, iso-bottom-left, iso-bottom-right. "
    "'width'/'height' set the pixel size (default 800x600, max 4096). 'zoom' scales the view after "
    "fitting (>1 zooms OUT, <1 zooms IN; default 1). 'fit_to' frames the camera on ONE occurrence "
    "by name (best-effort: it isolates that occurrence for the shot, then restores visibility). "
    "Take a screenshot before editing to understand the model, and after to confirm changes."
)

tool = (
    Tool.create_simple(name="view_screenshot", description=TOOL_DESCRIPTION)
    .add_input_property("view", {"type": "string", "description": "Camera orientation (default 'current')."})
    .add_input_property("width", {"type": "integer", "description": "Width in px (1-4096, default 800)."})
    .add_input_property("height", {"type": "integer", "description": "Height in px (1-4096, default 600)."})
    .add_input_property("zoom", {"type": "number", "description": "Zoom factor after fitting (>1 out, <1 in; default 1)."})
    .add_input_property("fit_to", {"type": "string", "description": "Occurrence name to frame the camera on (isolates it for the shot, then restores)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
