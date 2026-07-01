# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_activate_setup - make a CAM setup active and fit the view (a state-changer, not a read; it
stays a standalone tool rather than a cam_get slice)."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam

app = adsk.core.Application.get()


def activate_setup_handler(setup: str = "") -> dict:
    want = (setup or "").strip()
    if not want:
        return error("Provide 'setup' - the name of the setup to activate.")
    cam, err = get_cam()
    if err:
        return error(err)

    available = []
    target = None
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            nm = safe(lambda: s.name)
            available.append(nm)
            if (nm or "").lower() == want.lower():
                target = s
                break
    except Exception as e:
        return error(f"Could not read setups: {e}")

    if not target:
        return error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available if n) or '(none)'}")

    try:
        target.activate()
    except Exception as e:
        return error(f"Failed to activate '{want}': {e}")
    # Fit the view so a subsequent view_screenshot frames the setup.
    try:
        vp = app.activeViewport
        if vp:
            vp.fit()
    except Exception:
        pass
    return ok({"activated": safe(lambda: target.name),
        "note": "Setup activated and view fit. Use view_screenshot to capture it."})


_activate_tool = Tool.create_with_string_input(
    name="cam_activate_setup",
    description=(
        "Activate a CAM setup by name and fit the view, so you can then capture it with "
        "view_screenshot. Use this to review each setup in turn. Changes the active setup."
    ),
    input_param_name="setup",
    input_param_description="The setup name to activate.",
)
activate_setup_item = Item.create_tool_item(
    tool=_activate_tool, write="write", handler=activate_setup_handler, run_on_main_thread=True
)


def register_tool():
    register(activate_setup_item)
