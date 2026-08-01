# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: set the color/appearance of a body, occurrence, or component. WRITES.

Reuses the design's existing appearance of the target name (addByCopy refuses duplicate names) or
copies a base appearance (Appearances.addByCopy) so the tool owns an editable instance, sets its
ColorProperty, then assigns it to the resolved target - a component (or the whole design) colors all
its bodies.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()


def _parse_color(spec):
    """Parse '#RRGGBB' / 'RRGGBB' / 'r,g,b' -> (r, g, b) ints 0-255, or (None, msg)."""
    s = (spec or "").strip()
    if not s:
        return None, "Provide 'color' as '#RRGGBB', 'RRGGBB', or 'r,g,b' (0-255 each)."
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip() != ""]
        if len(parts) != 3:
            return None, f"'{spec}' is not 'r,g,b' (three 0-255 components)."
        try:
            rgb = tuple(int(p) for p in parts)
        except ValueError:
            return None, f"'{spec}' has non-integer components; use 'r,g,b' (0-255 each)."
    else:
        h = s[1:] if s.startswith("#") else s
        if len(h) != 6:
            return None, f"'{spec}' is not a 6-digit hex color (e.g. '#1E8E3E')."
        try:
            rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None, f"'{spec}' is not valid hex; use '#RRGGBB'."
    if any(c < 0 or c > 255 for c in rgb):
        return None, f"'{spec}' has a component outside 0-255."
    return rgb, None


# A face/body/occurrence/component (or '' = whole design) all carry a settable .appearance - exactly
# TargetRef's universe (minus mesh: a MeshBody has no appearance override). The whole-design ('') case
# is a Component (apply to all bodies), so 'design' is folded into the component branch below.
_TARGET = _inputs.TargetRef("target", allow=("body", "face", "occurrence", "component", "design"))


def _base_appearance(design):
    """An Appearance to copy as the override base: prefer one already in the design (always present
    once a body exists), else a generic appearance from any material library. Returns None if none."""
    apps = safe(lambda: design.appearances)
    n = safe(lambda: apps.count, 0) or 0
    if n:
        return apps.item(0)
    # fallback: first appearance of the first material library that has any
    libs = safe(lambda: app.materialLibraries)
    ln = safe(lambda: libs.count, 0) or 0
    for i in range(ln):
        lib_apps = safe(lambda i=i: libs.item(i).appearances)
        if lib_apps and (safe(lambda: lib_apps.count, 0) or 0) > 0:
            return lib_apps.item(0)
    return None


def _make_colored_appearance(design, rgb, opacity, name):
    """An appearance named `name` with the requested color set: REUSE one already in the design
    (addByCopy refuses a duplicate name, so a second same-color call - or a retry after a half-made
    copy - must find the existing one, not fail), else copy a base appearance in. The color is
    (re)applied either way, so a reused appearance always ends up at the requested color.
    Returns (appearance, reused, err)."""
    appearances = design.appearances
    appr = safe(lambda: appearances.itemByName(name))
    reused = appr is not None
    if appr is None:
        base = _base_appearance(design)
        if base is None:
            return None, False, ("No base appearance available to copy (the design has none and no "
                                 "material library exposed one). Open a design with at least one "
                                 "appearance.")
        appr = safe(lambda: appearances.addByCopy(base, name))
        if not appr:
            # a parallel call can land the name between the lookup and the copy - re-check once
            appr = safe(lambda: appearances.itemByName(name))
            reused = appr is not None
        if not appr:
            return None, False, "Could not create an appearance copy (addByCopy returned nothing)."
    color = adsk.core.Color.create(rgb[0], rgb[1], rgb[2], opacity)
    # Find the color-bearing property and set it. Most appearances expose one ColorProperty
    # (named 'Color' / 'Albedo' / etc.) - set every ColorProperty so the override takes regardless of
    # the localized name.
    set_any = False
    props = safe(lambda: appr.appearanceProperties)
    pn = safe(lambda: props.count, 0) or 0
    for i in range(pn):
        p = props.item(i)
        if safe(lambda p=p: type(p).__name__) == "ColorProperty":
            try:
                p.value = color
                set_any = True
            except Exception:
                pass  # some ColorProperties are read-only/texture-backed; try the next
    if not set_any:
        return None, False, "The base appearance has no editable color property to override."
    return appr, reused, None


def handler(target: str = "", color: str = "", opacity: int = 255, name: str = "") -> dict:
    """Apply a solid-color appearance override to the resolved target. WRITES."""
    rgb, cerr = _parse_color(color)
    if cerr:
        return error(cerr)
    try:
        opacity = int(opacity)
    except (TypeError, ValueError):
        return error("'opacity' must be an integer 0-255.")
    if opacity < 0 or opacity > 255:
        return error("'opacity' must be 0-255.")

    design = _common.design()
    if not design:
        return error("No active design with geometry.")

    resolved, terr = _TARGET.resolve(target)
    if terr:
        return terr if isinstance(terr, dict) else error(terr)
    entity, kind = resolved
    if kind == "design":
        kind = "component"      # whole design = the root component; color all its bodies
    desc = (f"{kind} '{safe(lambda: entity.fullPathName) or safe(lambda: entity.name)}'"
            if safe(lambda: entity.name) else kind)

    appr_name = (name or "").strip() or f"AgentColor_{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    appr, appr_reused, aerr = _make_colored_appearance(design, rgb, opacity, appr_name)
    if aerr:
        return error(aerr)

    applied_to = []
    failed = []
    if kind == "component":
        # a Component has no single .appearance; apply to each of its bodies. A failure on one body
        # must not hide that other bodies already got colored - collect per-body, don't abort the loop.
        bodies = safe(lambda: entity.bRepBodies)
        bn = safe(lambda: bodies.count, 0) or 0
        if bn == 0:
            return error(f"{desc} has no bodies to color.")
        for i in range(bn):
            b = bodies.item(i)
            name = safe(lambda b=b: b.name)
            try:
                b.appearance = appr
            except Exception as e:
                failed.append({"body": name, "error": str(e)})
                continue
            got = safe(lambda b=b: b.appearance.name)
            if got is not None and got != safe(lambda: appr.name):
                failed.append({"body": name, "error": f"appearance still reads '{got}' after the set"})
            else:
                applied_to.append(name)
        if not applied_to:
            return error(f"Could not apply appearance to any body of {desc}: "
                         f"{failed[0]['error'] if failed else 'unknown error'}.")
    else:
        # body / occurrence / face all carry a settable .appearance
        try:
            entity.appearance = appr
        except Exception as e:
            return error(f"Could not apply appearance to {desc}: {e}")
        got = safe(lambda: entity.appearance.name)
        if got is not None and got != safe(lambda: appr.name):
            return error(f"Assignment was accepted but {desc} still reads appearance '{got}' - "
                         "the override did not take.")
        # a BRepFace has no .name; fall back to the target description
        applied_to.append(safe(lambda: entity.name) or desc)

    note = ("Appearance override applied. Set a new color anytime; to revert, the override is on "
            "the body/occurrence (.appearance). Pair with view_screenshot to see it.")
    if failed:
        note = (f"Appearance applied to {len(applied_to)} of {len(applied_to) + len(failed)} bodies; "
                f"{len(failed)} failed - see 'failed'. " + note)

    result = {
        "applied": True,
        "target": desc,
        "kind": kind,
        "color_rgb": list(rgb),
        "color_hex": f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}",
        "opacity": opacity,
        "appearance": safe(lambda: appr.name),
        "appearance_reused": appr_reused,
        "applied_to": applied_to,
        "note": note,
    }
    if failed:
        result["failed"] = failed
    return ok(result)


_DESC = (
"Set the color/appearance of a FACE, body, occurrence, or component (all its bodies) as a revertible "
"override. 'target' = a find_geometry FACE handle (colors one face) or body, an occurrence name/"
"fullPath, a body name, or a component name (empty = whole design). 'color' = '#RRGGBB', 'RRGGBB', or "
"'r,g,b' (0-255); 'opacity' 0-255 (default 255). Pair with view_screenshot to verify."
)

tool = (
    Tool.create_simple(name="appearance_set", description=_DESC)
    .add_input_property(*_TARGET.as_property())
    .add_input_property("color", {"type": "string",
            "description": "Color as '#RRGGBB', 'RRGGBB', or 'r,g,b' (0-255 each)."})
    .add_input_property("opacity", {"type": "integer",
            "description": "Opacity 0-255 (default 255 = opaque)."})
    .add_input_property("name", {"type": "string",
            "description": "Optional name for the created appearance."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
