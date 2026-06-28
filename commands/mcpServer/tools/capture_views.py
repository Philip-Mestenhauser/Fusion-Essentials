# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: capture SEVERAL views of the model in one call (multi-view "eyes").

  view_screenshot_multi -> orient the camera to each requested view, capture each as a separate image, and
                  return them all (interleaved with text labels) in one response. A single isometric
                  is easy to misread; seeing front/top/right/iso together lets the agent reliably
                  judge geometry, position, and proportion.

Each view is captured by re-orienting + fit + saveAsImageFile (the same mechanism as
view_screenshot, which captures only ONE viewport per call). The user's camera is saved once and
restored at the end, so this is a non-destructive read.

Grounded in the Fusion API:
  - app.activeViewport.camera.viewOrientation = ViewOrientations.* ; viewport.fit()
  - viewport.saveAsImageFile(path, w, h) -> bool
Handler runs on the main thread; read-only (restores the camera).
"""

import base64
import os
import tempfile

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _error

app = adsk.core.Application.get()

# Friendly name -> ViewOrientations enum (mirrors view_screenshot; no 'current' here — every view
# is an explicit orientation).
_ORIENTATIONS = {
    "top": "TopViewOrientation", "bottom": "BottomViewOrientation",
    "front": "FrontViewOrientation", "back": "BackViewOrientation",
    "left": "LeftViewOrientation", "right": "RightViewOrientation",
    "iso-top-left": "IsoTopLeftViewOrientation", "iso-top-right": "IsoTopRightViewOrientation",
    "iso-bottom-left": "IsoBottomLeftViewOrientation", "iso-bottom-right": "IsoBottomRightViewOrientation",
}
_DEFAULT_VIEWS = ["front", "top", "right", "iso-top-right"]
_ALL_ORTHOS = ["front", "back", "left", "right", "top", "bottom"]
_MAX_DIM = 4096
_MAX_VIEWS = 8


def _parse_views(views: str):
    """Resolve the 'views' argument to an ordered, de-duplicated list of known view names.

    "" -> a sensible default multi-view set; "all" -> the six orthographic views; otherwise a
    comma-separated list. Returns (list, None) or (None, error_message)."""
    s = (views or "").strip().lower()
    if not s:
        return list(_DEFAULT_VIEWS), None
    if s == "all":
        return list(_ALL_ORTHOS), None
    out, seen = [], set()
    for tok in s.split(","):
        name = tok.strip()
        if not name:
            continue
        if name not in _ORIENTATIONS:
            return None, (f"Unknown view '{name}'. Valid: {', '.join(_ORIENTATIONS)} "
                          "(or 'all' for the six orthographic views).")
        if name not in seen:
            seen.add(name)
            out.append(name)
    if not out:
        return list(_DEFAULT_VIEWS), None
    return out, None


def handler(views: str = "", width: int = 600, height: int = 500) -> dict:
    """Capture several views of the model in one call.

    views: comma-separated view names (front/back/left/right/top/bottom/iso-top-right/...), or 'all'
    for the six orthographic views; omit for a default front/top/right/iso set. width/height: pixel
    size of EACH image. Returns one labelled image per view; the camera is restored afterward.
    """
    names, err = _parse_views(views)
    if err:
        return _error(err)
    names = names[:_MAX_VIEWS]
    try:
        width = max(1, min(int(width), _MAX_DIM))
        height = max(1, min(int(height), _MAX_DIM))
    except Exception:
        width, height = 600, 500

    vp = app.activeViewport
    if not vp:
        return _error("No active viewport (is a document open?).")

    content = []
    saved_camera = vp.camera   # restore once at the end
    captured = []
    try:
        for name in names:
            try:
                cam = vp.camera
                cam.viewOrientation = getattr(adsk.core.ViewOrientations, _ORIENTATIONS[name])
                vp.camera = cam
                vp.fit()
            except Exception as e:
                content.append({"type": "text", "text": f"[{name}] failed to orient: {e}"})
                continue
            temp_path = None
            try:
                fd, temp_path = tempfile.mkstemp(prefix="fe_mcp_views", suffix=".png")
                os.close(fd)
                ok = vp.saveAsImageFile(temp_path, width, height)
                if not ok or not os.path.exists(temp_path):
                    content.append({"type": "text", "text": f"[{name}] capture failed."})
                    continue
                with open(temp_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                content.append({"type": "text", "text": f"View: {name}"})
                content.append({"type": "image", "data": b64, "mimeType": "image/png"})
                captured.append(name)
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
    finally:
        try:
            vp.camera = saved_camera
        except Exception:
            pass

    if not captured:
        return _error("No views were captured.")
    content.insert(0, {"type": "text",
                       "text": f"Captured {len(captured)} view(s): {', '.join(captured)}. "
                               "Each image is labelled with its view above it."})
    return {"content": content, "isError": False}


TOOL_DESCRIPTION = (
    "Capture SEVERAL views of the model in ONE call — front/top/right/iso etc. as separate labelled "
    "images — so you can read geometry/position reliably instead of guessing from a single "
    "isometric. 'views' = comma-separated view names (front, back, left, right, top, bottom, "
    "iso-top-right, iso-top-left, iso-bottom-right, iso-bottom-left), or 'all' for the six "
    "orthographic views; omit for a default front/top/right/iso set. 'width'/'height' size each "
    "image. The camera is restored afterward (read-only). Prefer this over view_screenshot when "
    "judging a 3D layout."
)

tool = (
    Tool.create_simple(name="view_screenshot_multi", description=TOOL_DESCRIPTION)
    .add_input_property("views", {"type": "string",
                                  "description": "Comma-separated views, or 'all'; omit for front/top/right/iso default."})
    .add_input_property("width", {"type": "integer", "description": "Width of each image in px (default 600)."})
    .add_input_property("height", {"type": "integer", "description": "Height of each image in px (default 500)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
