# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP building block: capture the Fusion viewport so the agent can visually review results.

Returns the image as an MCP image content block (base64 PNG). Optionally reorients the camera first
(top/front/iso/etc.) and fits the view; the camera is set to exact world-axis vectors because
assigning camera.viewOrientation is unreliable.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, safe
from . import _common
from . import _inputs
from . import _view_common

app = adsk.core.Application.get()

# The named views: 'current' (leave the camera as-is) + the shared camera-orientation table,
# which supplies the exact eye/target/up vectors each name maps to.
_VIEWS = ("current",) + tuple(_view_common.VIEW_DIRECTIONS)

_MAX_DIM = 4096

_FIT_TO = _inputs.OccurrenceRef("fit_to",
        description="Occurrence to frame the camera on (isolates it for the shot, then restores).")


def _keep_visible(o_path, target_path):
    """Keep an occurrence visible during a fit-to isolate if it IS the target, an ANCESTOR of it, or a
    DESCENDANT of it. Hiding an ancestor hides the nested target (BLANK image); hiding a descendant
    drops part of the target's own subtree. Nesting is by fullPathName ('Frame:1+Pedestal:1', '+' per
    level). Comparison is by PATH, never Python `is` - the API mints a fresh occurrence proxy on each
    access, so `o is target` is never true across two walks and would hide the target itself."""
    if not o_path or not target_path:
        return False
    return (o_path == target_path
            or target_path.startswith(o_path + "+")     # o is an ancestor of the target
            or o_path.startswith(target_path + "+"))     # o is a descendant of the target


def _isolate_for_fit(name):
    """Temporarily hide every occurrence that is NOT the named one, its ancestors, or its descendants,
    so vp.fit() frames just the named one. Returns (restore_callable, error_or_None) - error is set (and
    restore is None) when the occurrence didn't resolve (including an ambiguous name, which names the
    candidates). Best-effort + non-destructive."""
    design = _common.design()
    root = safe(lambda: design.rootComponent) if design else None
    if not root:
        return None, f"fit_to: no active design to resolve '{name}' against."
    # Resolve via the shared OccurrenceRef kind (fullPathName-preferring, ambiguity-refusing) so an
    # ambiguous name doesn't silently frame the wrong instance.
    target, err = _FIT_TO.resolve(name)
    if target is None:
        return None, err
    target_path = safe(lambda: target.fullPathName)
    occs = safe(lambda: list(root.allOccurrences)) or []
    prev = []
    for o in occs:
        if _keep_visible(safe(lambda o=o: o.fullPathName), target_path):
            continue
        was = safe(lambda o=o: o.isLightBulbOn)
        if was:
            prev.append(o)
            safe(lambda o=o: setattr(o, "isLightBulbOn", False))

    def restore():
        for o in prev:
            safe(lambda o=o: setattr(o, "isLightBulbOn", True))
    return restore, None


def _active_component_note(design):
    """If a NON-root component is activated, Fusion renders everything outside it as dimmed/translucent
    'ghosts'. Return a one-line warning naming the activated occurrence so the agent reads the
    washed-out image as activation scope, not a lighting problem. Root detection is
    design.activeOccurrence - null exactly when the root is active (API doc). An identity comparison
    of activeComponent against rootComponent can never be true (each property access mints a NEW
    proxy object), which made this warning fire at root."""
    occ = safe(lambda: design.activeOccurrence) if design else None
    if occ is None:
        return None
    nm = safe(lambda: occ.name) or "a sub-component"
    return (f"Active component is '{nm}' - everything outside it renders dimmed/translucent in this "
            "image (activation scope, not a lighting issue). Activate the root to see all parts solid.")


def handler(view: str = "current", width: int = 800, height: int = 600,
            zoom: float = 1.0, fit_to: str = "", transparent_background=None,
            anti_aliased=None) -> dict:
    """See TOOL_DESCRIPTION."""
    view = (view or "current").strip().lower()
    if view not in _VIEWS:
        return error(f"Unknown view '{view}'. Valid: {', '.join(_VIEWS)}")

    try:
        width = max(1, min(int(width), _MAX_DIM))
        height = max(1, min(int(height), _MAX_DIM))
    except Exception:
        width, height = 800, 600

    vp = app.activeViewport
    if not vp:
        return error("No active viewport (is a document open?).")

    # 'fit_to' frames the camera on ONE occurrence (best-effort: temporarily isolate it so fit()
    # tightens onto it, then restore visibility). Returns the original visibility so a read tool
    # leaves no permanent change.
    saved_camera = None
    restore_fit_to = None
    want_fit = (fit_to or "").strip()
    if want_fit:
        restore_fit_to, fit_err = _isolate_for_fit(want_fit)
        if restore_fit_to is None:
            return error(fit_err or f"fit_to: no occurrence matched '{want_fit}'. "
                         "Use design_get(include=['tree']) to list.")

    # Reorient the camera if a specific view was requested, saving the user's current
    # camera so we can restore it afterward (a read tool shouldn't permanently change
    # the user's view as a side effect).
    if view != "current" or want_fit or (zoom and zoom != 1.0):
        try:
            saved_camera = vp.camera          # snapshot of the user's current view
            if view != "current":
                # Every named view resolves in the shared table (_VIEWS is built from it); the shared
                # apply sets exact world-axis vectors (guaranteed square), forces ortho for the 6
                # faces, and fits.
                _view_common.apply_named_view(vp, view)
            else:
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
            return error(f"Failed to set view '{view}': {e}")

    try:
        b64, cerr = _view_common.capture_png_b64(
            vp, width, height, transparent_background=transparent_background,
            anti_aliased=anti_aliased)
        if cerr:
            return error(cerr)
        content = []
        note = _active_component_note(_common.design())
        if note:
            content.append({"type": "text", "text": note})
        content.append({"type": "image", "data": b64, "mimeType": "image/png"})
        return {"content": content, "isError": False}
    except Exception as e:
        return error(f"Screenshot error: {e}")
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


TOOL_DESCRIPTION = (
    "Capture a screenshot of the current Fusion viewport and return it as an image "
    "so you can visually inspect the model and verify your work. Optionally set "
    "'view' to reorient the camera (default 'current' = leave as-is). "
    "'width'/'height' set the pixel size (default 800x600, max 4096). 'zoom' scales the view after "
    "fitting (>1 zooms OUT, <1 zooms IN; default 1). 'fit_to' frames the camera on ONE occurrence "
    "by name. 'transparent_background'/'anti_aliased' control the render; omit both for "
    "Fusion's standard capture. "
    "Take a screenshot before editing to understand the model, and after to confirm changes."
)

tool = (
    Tool.create_simple(name="view_screenshot", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.Choice("view", list(_VIEWS), default="current",
            description="Camera orientation.").as_property())
    .add_input_property("width", {"type": "integer", "description": "Width in px (1-4096, default 800)."})
    .add_input_property("height", {"type": "integer", "description": "Height in px (1-4096, default 600)."})
    .add_input_property("zoom", {"type": "number", "description": "Zoom factor after fitting (>1 out, <1 in; default 1)."})
    .add_input_property(*_FIT_TO.as_property())
    .add_input_property("transparent_background", {"type": "boolean",
            "description": "Render the background transparent."})
    .add_input_property("anti_aliased", {"type": "boolean",
            "description": "Anti-alias the rendered image."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
