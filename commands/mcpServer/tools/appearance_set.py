# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: set the color/appearance of a body or occurrence.

  appearance_set -> apply a solid-color appearance override to a body, occurrence, or every body of a
                    component. WRITES.

Why this exists: there was no appearance tool at all, so every part rendered identical gray. For an
agent that drives by screenshot, distinct colors are a DEBUGGING INSTRUMENT — they make it possible to
tell parts apart and confirm "the right body got the edit" — not just cosmetics.

Mechanism (the reliable color-override idiom): copy a base appearance into the design with
Appearances.addByCopy (so we own an editable instance), set its ColorProperty value to the requested
Color, then assign it to the target's .appearance. The base is taken from an existing design appearance
when available (always present once a body exists), else a generic appearance from a material library.

GUARDS:
  - parses '#RRGGBB' / 'RRGGBB' / 'r,g,b' colors; a malformed color is rejected with the accepted forms;
  - resolves the target (body handle / occurrence / body name / component) the same way model_measure_bbox
    does; a component applies to ALL its bodies (a Component has no single .appearance);
  - if no base appearance can be found to copy, says so rather than failing opaquely.

Grounded in adsk.core / adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Design.appearances (Appearances): .addByCopy(appearance, name) -> Appearance, .itemByName, count/item
  - Appearance.appearanceProperties -> Properties; a ColorProperty has settable .value (adsk.core.Color)
  - adsk.core.Color.create(r, g, b, opacity)  [each 0-255]
  - BRepBody.appearance / Occurrence.appearance (settable; null removes the override)
  - Application.materialLibraries[i].appearances (fallback base source)
Handler runs on the main thread; WRITES.
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


def _resolve_target(design, target):
    """Resolve target name -> (entity, description, kind). kind in {body, occurrence, component}.

    Same resolution shape as model_measure_bbox: a find_geometry handle / occurrence name-or-path /
    body name / component name; empty -> the root component (whole design)."""
    root = design.rootComponent
    name = (target or "").strip()
    if not name:
        return root, "root component (whole design)", "component"

    # A find_geometry handle pins a SPECIFIC body OR face (both carry a settable .appearance, so both
    # are valid override targets — a face lets you color ONE face, not the whole body). Try the
    # sanctioned resolver (composite-handle aware + self-healing) FIRST; a plain name returns None and
    # falls through to the name lookups below — no handle-vs-name guess by string length.
    ent = _inputs._resolve_token_entity(design, name)
    if ent is not None:
        if isinstance(ent, adsk.fusion.BRepFace):
            return ent, f"face (handle {name[:10]}…)", "face"
        if isinstance(ent, adsk.fusion.BRepBody):
            return ent, f"body (handle {name[:10]}…)", "body"
        return None, None, None

    occ = safe(lambda: root.occurrences.itemByName(name))
    if occ:
        return occ, f"occurrence '{name}'", "occurrence"
    try:
        for o in root.allOccurrences:
            if (safe(lambda o=o: o.fullPathName) or "") == name or (safe(lambda o=o: o.name) or "") == name:
                return o, f"occurrence '{name}'", "occurrence"
    except Exception:
        pass

    body = safe(lambda: root.bRepBodies.itemByName(name))
    if body:
        return body, f"body '{name}'", "body"
    try:
        for o in root.allOccurrences:
            b = safe(lambda o=o: o.bRepBodies.itemByName(name))
            if b:
                return b, f"body '{name}' in '{safe(lambda o=o: o.name)}'", "body"
    except Exception:
        pass

    # Component by name -> apply to all its bodies.
    try:
        for o in root.allOccurrences:
            if (safe(lambda o=o: o.component.name) or "") == name:
                return o.component, f"component '{name}'", "component"
    except Exception:
        pass
    if (safe(lambda: root.name) or "") == name:
        return root, f"component '{name}'", "component"

    return None, None, None


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
    """Copy a base appearance into the design and set its color. Returns (appearance, err)."""
    base = _base_appearance(design)
    if base is None:
        return None, ("No base appearance available to copy (the design has none and no material "
                      "library exposed one). Open a design with at least one appearance.")
    appearances = design.appearances
    appr = safe(lambda: appearances.addByCopy(base, name))
    if not appr:
        return None, "Could not create an appearance copy (addByCopy returned nothing)."
    color = adsk.core.Color.create(rgb[0], rgb[1], rgb[2], opacity)
    # Find the color-bearing property and set it. Most appearances expose one ColorProperty
    # (named 'Color' / 'Albedo' / etc.) — set every ColorProperty so the override takes regardless of
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
        return None, "The base appearance has no editable color property to override."
    return appr, None


def handler(target: str = "", color: str = "", opacity: int = 255, name: str = "") -> dict:
    """Apply a solid-color appearance override to a FACE, body, occurrence, or component's bodies. WRITES.

    target: a find_geometry handle to a FACE (colors just that one face) or a body, an occurrence
    name/full path, a body name, or a component name (applies to all its bodies); empty -> whole design.
    color: '#RRGGBB', 'RRGGBB', or 'r,g,b'. opacity: 0-255 (default 255, opaque). name: optional name
    for the created appearance.
    """
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

    entity, desc, kind = _resolve_target(design, target)
    if not entity:
        return error(f"No face/body/occurrence/component matching '{target}'. Use design_get_tree "
                     "for names, or a find_geometry handle for a specific body or FACE.")

    appr_name = (name or "").strip() or f"AgentColor_{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    appr, aerr = _make_colored_appearance(design, rgb, opacity, appr_name)
    if aerr:
        return error(aerr)

    applied_to = []
    try:
        if kind == "component":
            # a Component has no single .appearance; apply to each of its bodies
            bodies = safe(lambda: entity.bRepBodies)
            bn = safe(lambda: bodies.count, 0) or 0
            if bn == 0:
                return error(f"{desc} has no bodies to color.")
            for i in range(bn):
                b = bodies.item(i)
                b.appearance = appr
                applied_to.append(safe(lambda b=b: b.name))
        else:
            # body / occurrence / face all carry a settable .appearance
            entity.appearance = appr
            # a BRepFace has no .name; fall back to the target description
            applied_to.append(safe(lambda: entity.name) or desc)
    except Exception as e:
        return error(f"Could not apply appearance to {desc}: {e}")

    return ok({
        "applied": True,
        "target": desc,
        "kind": kind,
        "color_rgb": list(rgb),
        "color_hex": f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}",
        "opacity": opacity,
        "appearance": safe(lambda: appr.name),
        "applied_to": applied_to,
        "note": "Appearance override applied. Set a new color anytime; to revert, the override is on "
        "the body/occurrence (.appearance). Pair with view_screenshot to see it.",
    })


_DESC = (
"Set the color/appearance of a FACE, body, occurrence, or component (all its bodies) as a revertible "
"override. 'target' = a find_geometry FACE handle (colors one face) or body, an occurrence name/"
"fullPath, a body name, or a component name (empty = whole design). 'color' = '#RRGGBB', 'RRGGBB', or "
"'r,g,b' (0-255); 'opacity' 0-255 (default 255). WRITES. Pair with view_screenshot."
)

tool = (
    Tool.create_simple(name="appearance_set", description=_DESC)
    .add_input_property("target", {"type": "string",
            "description": "find_geometry handle to a FACE or body, occurrence name/fullPath, body "
            "name, or component name (empty = whole design)."})
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
