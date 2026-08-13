# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP building block: capture the Fusion viewport so the agent can visually review results.

Returns the image as an MCP image content block (base64 PNG). Optionally reorients the camera first
(top/front/iso/etc.) and fits the view; the camera is set to exact world-axis vectors because
assigning camera.viewOrientation is unreliable. 'file_path' additionally writes the captured PNG to
local disk - the raster writer a rendered view needs to reach a drawing sheet
(drawing_insert_image).
"""

import base64

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, safe
from . import _common
from . import _export
from . import _inputs
from . import _view_common

app = adsk.core.Application.get()

# The named views: 'current' (leave the camera as-is) + the shared camera-orientation table,
# which supplies the exact eye/target/up vectors each name maps to.
_VIEWS = ("current",) + tuple(_view_common.VIEW_DIRECTIONS)

_MAX_DIM = 4096

# The capture is a PNG whatever the path says - this is the extension _export.prepare_out_path
# appends, so no file lands with PNG bytes under another format's name.
_PNG_EXT = ".png"

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
    so the shot shows just the named one. Returns (restore_callable, target_occurrence, error) -
    error is set (and the other two None) when the occurrence didn't resolve (including an
    ambiguous name, which names the candidates); the target feeds the bodies-only camera fit.

    The restore callable returns the NAMES of the occurrences whose bulb it could not put back
    (empty when everything was restored): this tool mutates visibility to take its picture, so a
    restore that silently failed would leave the document changed by a read."""
    design = _common.design()
    root = safe(lambda: design.rootComponent) if design else None
    if not root:
        return None, None, f"fit_to: no active design to resolve '{name}' against."
    # Resolve via the shared OccurrenceRef kind (fullPathName-preferring, ambiguity-refusing) so an
    # ambiguous name doesn't silently frame the wrong instance.
    target, err = _FIT_TO.resolve(name)
    if target is None:
        return None, None, err
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

    # ALSO hide non-body geometry design-wide for the shot: vp.fit() frames every VISIBLE entity,
    # and construction geometry owned by the fitted component (measured: a datum plane) blows the
    # frame to the whole scene while the occurrence isolation holds. The per-component display
    # FOLDER bulbs (the shared _view_common map) switch sketches/construction/origins/joints off
    # in one write each without touching any entity's own bulb.
    folder_prev = []                       # (component, attr, saved_value) - only bulbs we moved
    for comp in _view_common.all_display_components(design):
        for attr in _view_common.DISPLAY_FOLDERS.values():
            if _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr)):
                folder_prev.append((comp, attr))
                safe(lambda comp=comp, attr=attr: setattr(comp, attr, False))

    def restore():
        stuck = []
        for o in prev:
            safe(lambda o=o: setattr(o, "isLightBulbOn", True))
            if safe(lambda o=o: o.isLightBulbOn) is not True:
                stuck.append(safe(lambda o=o: o.fullPathName)
                             or safe(lambda o=o: o.name) or "?")
        for comp, attr in folder_prev:
            safe(lambda comp=comp, attr=attr: setattr(comp, attr, True))
            if _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr)) is not True:
                stuck.append(f"{safe(lambda comp=comp: comp.name) or '?'}:{attr}")
        return stuck
    return restore, target, None


def _restore_message(restore_fit_to):
    """Run the fit_to restore and return the sentence naming what it could NOT put back, or None.

    A restore that did not take leaves the document changed by a READ tool, and the caller is the
    only one who can undo it - so EVERY exit that reaches the isolate runs this, not just the
    successful capture. Returns None when there was nothing to restore or everything came back."""
    if not restore_fit_to:
        return None
    try:
        stuck = restore_fit_to() or []
    except Exception as e:
        stuck = [f"the restore raised: {e}"]
    if not stuck:
        return None
    return ("fit_to hid the other occurrences for this shot and could NOT turn "
            f"{len(stuck)} of them back on: {', '.join(str(s) for s in stuck[:5])}. "
            "The document is left with those hidden - view_set(action='show', target=...) "
            "restores them.")


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


def _write_png(b64, path):
    """Write the captured PNG to 'path'. Returns (size_bytes, error).

    The capture hands back base64, so the bytes are decoded here and the file that lands is READ
    BACK for a non-zero size through the shared file-landed verifier - holding a base64 string is
    not proof a file exists on disk."""
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        return 0, f"The captured image could not be decoded to write '{path}': {e}"
    try:
        with open(path, "wb") as fh:
            fh.write(raw)
    except Exception as e:
        return 0, f"Could not write the screenshot to '{path}': {e}"
    size, verr = _export.verify_written(path)
    if verr:
        return 0, f"The viewport was captured but {verr}. Treating this as a failure."
    return size, None


def handler(view: str = "current", width: int = 800, height: int = 600,
            zoom: float = 1.0, fit_to: str = "", transparent_background=None,
            anti_aliased=None, file_path: str = "") -> dict:
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

    # The output path is prepared BEFORE the camera moves, so an unusable destination refuses
    # without having reoriented the user's view for a picture that is not going to land.
    out_path, perr = _export.prepare_out_path(file_path, _PNG_EXT)
    if perr:
        return error(perr)

    # 'fit_to' frames the camera on ONE occurrence (best-effort: temporarily isolate it so fit()
    # tightens onto it, then restore visibility). Returns the original visibility so a read tool
    # leaves no permanent change.
    saved_camera = None
    restore_fit_to = None
    want_fit = (fit_to or "").strip()
    if want_fit:
        restore_fit_to, _fit_target, fit_err = _isolate_for_fit(want_fit)
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
            # The orient failed AFTER fit_to already hid the other occurrences, so this exit owes
            # the same restore disclosure the capture exits give - a bulb the restore could not
            # put back is named here or nowhere.
            stuck_msg = _restore_message(restore_fit_to)
            return error(f"Failed to set view '{view}': {e}"
                         + (" " + stuck_msg if stuck_msg else ""))

    try:
        b64, cerr = _view_common.capture_png_b64(
            vp, width, height, transparent_background=transparent_background,
            anti_aliased=anti_aliased)
        result = error(cerr) if cerr else None
        if result is None:
            content = []
            note = _active_component_note(_common.design())
            if note:
                content.append({"type": "text", "text": note})
            written = None
            if out_path:
                # A requested file that did not land is a FAILURE, not an ok carrying the inline
                # image: the caller asked for a file to hand on (drawing_insert_image takes a local
                # path), and a false ok would send it looking for one that is not there.
                size, werr = _write_png(b64, out_path)
                if werr:
                    result = error(werr)
                else:
                    written = (f"PNG also written to disk: file_path={out_path} "
                               f"size_bytes={size}. drawing_insert_image places it on a drawing "
                               "sheet.")
            if result is None:
                if written:
                    content.append({"type": "text", "text": written})
                content.append({"type": "image", "data": b64, "mimeType": "image/png"})
                result = {"content": content, "isError": False}
    except Exception as e:
        result = error(f"Screenshot error: {e}")

    # Restore the user's original camera if we changed it.
    if saved_camera is not None:
        try:
            vp.camera = saved_camera
        except Exception:
            pass
    # Restore visibility if fit_to isolated something.
    stuck_msg = _restore_message(restore_fit_to)
    if stuck_msg:
        if result.get("isError"):
            return error(result.get("message", "") + " " + stuck_msg)
        result["content"].insert(0, {"type": "text", "text": stuck_msg})
    return result


TOOL_DESCRIPTION = (
    "Capture a screenshot of the current Fusion viewport and return it as an image "
    "so you can visually inspect the model and verify your work. Optionally set "
    "'view' to reorient the camera (default 'current' = leave as-is). "
    "'width'/'height' set the pixel size (default 800x600, max 4096). 'zoom' scales the view after "
    "fitting (>1 zooms OUT, <1 zooms IN; default 1). 'fit_to' frames the camera on ONE occurrence "
    "by name (hides the others for the shot, restores them after, names any it could not). "
    "'transparent_background'/'anti_aliased' control the render; omit both for "
    "Fusion's standard capture. 'file_path' also writes the PNG to local disk (the image still "
    "returns inline) - the raster file drawing_insert_image takes. "
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
    .add_input_property("file_path", {"type": "string",
            "description": "Local path to ALSO write the PNG to ('.png' appended if missing, "
                           "directory created)."})
    .strict_schema()
)

# write="write": 'file_path' writes a caller-named PNG to local disk and an existing file at that
# path is overwritten without a refusal, so this tool cannot sit in the auto-approve read bucket -
# the name keeps its Acquire verb (see test_tool_naming.py's write-verb exemption).
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
