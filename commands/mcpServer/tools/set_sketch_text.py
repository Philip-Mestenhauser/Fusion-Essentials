# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: set the text of sketch text entities in the active design.

  sketch_set_text -> set the displayed string of one or more sketch-text entities (e.g. an
                     engraved label / nameplate). Target by sketch name, or update every sketch
                     text in the design. WRITES to the design.

General-purpose: this just edits sketch text. Common uses are stamping a part/file name onto a
fixture or label, but the tool is agnostic about why.

HOW (grounded live): a SketchText's content is NOT settable via its definition
(`MultiLineTextDefinition` has no `.text`). The writable handle is `SketchText.textParameter`
— a ModelParameter whose expression is the QUOTED string (e.g. `'Label Text'`). Setting
`textParameter.expression = "'NewText'"` updates the engraving. No assembly-context proxy is
needed for the write (verified live).

Grounded in adsk.fusion:
  - Sketch.sketchTexts (SketchTexts) — iterate; each is a SketchText
  - SketchText.textParameter (ModelParameter) — .expression is the quoted string (settable)
  - Design.allComponents -> Component.sketches.itemByName / iterate
Handler runs on the main thread; WRITES to the design.
"""

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

_MAX = 500


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        try:
            design = adsk.fusion.Design.cast(
                app.activeDocument.products.itemByProductType('DesignProductType'))
        except Exception:
            design = None
    return design


_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}


def _scale(units):
    return _UNIT_TO_CM.get((units or "mm").strip().lower())


def _create_text(design, text, sketch_name, height, x, y, units):
    """Create a new SketchText in the named sketch at (x,y) with the given height. WRITES."""
    if not (sketch_name or "").strip():
        return _error("create=true needs 'sketch_name' — the sketch to add the text to (create one "
                      "first with sketch_create).")
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Use mm, cm, or in.")
    try:
        h = float(height)
    except Exception:
        return _error("'height' must be a number (text height in 'units').")
    if h <= 0:
        return _error("'height' must be > 0.")

    root = _safe(lambda: design.rootComponent)
    sk = _safe(lambda: root.sketches.itemByName(sketch_name.strip())) if root else None
    if not sk:
        return _error(f"No sketch named '{sketch_name}'. Create it first with sketch_create.")

    try:
        texts = sk.sketchTexts
        ipt = texts.createInput2(text, h * k)              # text + height (cm)
        ipt.setAsMultiLine(
            adsk.core.Point3D.create(x * k, y * k, 0),
            adsk.core.Point3D.create(x * k + max(len(text), 1) * h * k, y * k + h * k, 0),
            adsk.core.HorizontalAlignments.LeftHorizontalAlignment,
            adsk.core.VerticalAlignments.BottomVerticalAlignment, 0)
        st = texts.add(ipt)
    except Exception as e:
        return _error(f"Could not create sketch text in '{sketch_name}': {e}.")
    if not st:
        return _error("Creating the sketch text returned nothing.")

    return _ok({
        "created": True,
        "sketch": _safe(lambda: sk.name),
        "text": text,
        "height": round(h, 6),
        "position": {"x": x, "y": y, "units": units},
        "note": "Sketch text created. Extrude/emboss the sketch to engrave it, or edit it later with "
                "set_sketch_text (without create).",
    })


def _unquote(expr):
    """The textParameter expression is a quoted string ('foo'); return the inner text."""
    if expr is None:
        return None
    s = str(expr)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _quote(text):
    """Quote a plain string for a text-parameter expression, escaping any single quotes."""
    return "'" + str(text).replace("'", "\\'") + "'"


def _iter_sketch_texts(design, sketch_name):
    """Yield (component_name, sketch_name, sketch_text) for the target sketch(es)."""
    want = (sketch_name or "").strip()
    for comp in _safe(lambda: design.allComponents, []) or []:
        try:
            sketches = comp.sketches
        except Exception:
            continue
        for i in range(_safe(lambda: sketches.count, 0)):
            sk = sketches.item(i)
            sk_name = _safe(lambda sk=sk: sk.name) or ""
            if want and sk_name != want:
                continue
            texts = _safe(lambda sk=sk: sk.sketchTexts)
            if not texts:
                continue
            for j in range(_safe(lambda texts=texts: texts.count, 0)):
                yield (_safe(lambda comp=comp: comp.name), sk_name, texts.item(j))


def handler(text: str = "", sketch_name: str = "", index: int = -1,
            create: bool = False, height: float = 5.0, x: float = 0.0, y: float = 0.0,
            units: str = "mm") -> dict:
    """Set the displayed string of sketch text entities — or CREATE new text (create=true).

    text: the string to display. sketch_name: the sketch to act in (for create it's REQUIRED; for
    edit, omit to update EVERY sketch text). index: for edit, the 0-based text to update (-1 = all).
    create=true: ADD a new sketch text at (x,y) in 'units' with 'height' (the text height in 'units')
    in the named sketch, instead of editing. WRITES.
    """
    if text is None:
        return _error("Provide 'text' — the string to display.")

    design = _design()
    if not design:
        return _error("No active design (open a document with sketch text).")

    if create:
        return _create_text(design, text, sketch_name, height, x, y, units)

    targets = list(_iter_sketch_texts(design, sketch_name))
    if not targets:
        if (sketch_name or "").strip():
            return _error(f"No sketch text found in a sketch named '{sketch_name}'. (Use "
                          "sketch_get to list sketches; the text must live in a sketch with "
                          "that exact name.)")
        return _error("No sketch text found in the active design.")

    want_index = int(index) if index is not None else -1
    changed = []
    skipped = 0
    # Track per-sketch running index so 'index' selects the Nth text within that sketch.
    per_sketch_counter = {}
    for comp_name, sk_name, st in targets:
        if len(changed) >= _MAX:
            break
        k = per_sketch_counter.get(sk_name, 0)
        per_sketch_counter[sk_name] = k + 1
        if want_index >= 0 and k != want_index:
            skipped += 1
            continue
        before = _unquote(_safe(lambda st=st: st.textParameter.expression))
        try:
            st.textParameter.expression = _quote(text)
        except Exception as e:
            return _error(f"Failed to set sketch text in sketch '{sk_name}': {e}")
        after = _unquote(_safe(lambda st=st: st.textParameter.expression))
        changed.append({"component": comp_name, "sketch": sk_name, "before": before, "after": after})

    if not changed:
        return _error(f"No sketch text matched index {want_index} in sketch '{sketch_name}'.")

    # Force a recompute so DOWNSTREAM features rebuild against the new text. Changing
    # textParameter.expression updates the sketch, but a feature that consumes the text (e.g. an
    # Emboss/extrude that engraves it) can show STALE geometry until the design recomputes — which
    # is why an engraving can look unchanged even though the text value is correct. computeAll()
    # makes the visible model match. Only meaningful in parametric mode (direct mode has no tree).
    recomputed = False
    try:
        if _safe(lambda: design.designType) == 1:  # ParametricDesignType
            design.computeAll()
            recomputed = True
    except Exception:
        recomputed = False

    return _ok({
        "set": True,
        "text": text,
        "changed_count": len(changed),
        "changed": changed,
        "recomputed": recomputed,
        "note": ("Sketch text updated" + (" and design recomputed so any engraving/emboss that "
                 "consumes it rebuilt" if recomputed else "") + ". View it with view_screenshot."),
    })


TOOL_DESCRIPTION = (
    "Set the displayed string of sketch text entities (e.g. an engraved label / nameplate) in "
    "the active design. 'text' is the new string. 'sketch_name' limits the change to sketch "
    "texts inside the sketch with that exact name (omit to update EVERY sketch text in the "
    "design — use a name like 'File_Name' to target one). 'index' picks one text if a sketch "
    "has several (0-based; default all). WRITES to the design; reports each text's before/after. "
    "Read sketch names with sketch_get. Generic: it just edits the text — stamping a part/file "
    "name onto a fixture is a common use but not the only one."
)

tool = (
    Tool.create_with_string_input(
        name="sketch_set_text",
        description=TOOL_DESCRIPTION,
        input_param_name="text",
        input_param_description="The new string to display.",
    )
    .add_input_property("sketch_name", {"type": "string",
                                        "description": "Only update sketch texts in the sketch with this name (omit = all)."})
    .add_input_property("index", {"type": "integer",
                                  "description": "If a sketch has multiple texts, the 0-based one to update (default all)."})
    .add_input_property("create", {"type": "boolean",
                                   "description": "CREATE new text instead of editing: add it to 'sketch_name' at (x,y) with 'height'. Default false."})
    .add_input_property("height", {"type": "number", "description": "Text height in 'units' (create only; default 5)."})
    .add_input_property("x", {"type": "number", "description": "Text X position in 'units' (create only)."})
    .add_input_property("y", {"type": "number", "description": "Text Y position in 'units' (create only)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm; create only)."})
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
