# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: SELECT the machining geometry on a CAM operation (+ optionally its heights).

  cam_select_geometry -> tell an operation WHICH geometry to machine, using find_geometry handles:
      - chain      : seed BRepEdges/SketchLines into a CurveSelection chain (contour/pocket/3D
                     machiningBoundarySel). Fusion walks the connected chain. isOpen / reverted.
      - pocket     : seed the pocket-FLOOR BRepFace into a PocketSelection (2D pocket).
      - face       : seed faces into a FaceContourSelection.
      - silhouette : seed a body/face into a SilhouetteSelection.
      - holes      : set drill holeFaces directly to cylinder faces (object-list), optionally filtered
                     to a diameter range (min_diameter/max_diameter, mm).
    Plus optional top/bottom HEIGHT control (mode + offset, the from-geometry pattern the pros use).

This is the gap that made earlier ops generate EMPTY: cam_create_operation makes an op but couldn't
target geometry. Two API mechanisms (researched live + in pro sample docs, see
docs/fusion-api-notes.md "Operation geometry selections"):
  (A) CURVE selections — param.value.getCurveSelections() -> createNew*Selection() ->
      sel.inputGeometry=[...] -> param.value.applyCurveSelections(cs).  contours/pockets/machiningBoundarySel.
  (B) DIRECT object-list — param.value.value = [faces].  drill holeFaces.

Heights are a 4-part group (_mode/_offset/_value/_ref): SET _mode + _offset; never _value.
Generation is ASYNC — we gate on GenerateToolpathFuture.isGenerationCompleted, NOT op.isGenerating
(the flag clears early -> a false empty/invalid read). An empty toolpath with no warning is almost
always ZERO DEPTH (top & bottom resolving to the same Z) — we surface that hint.
"""

import time

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _inputs

app = adsk.core.Application.get()

# selection kind -> (CurveSelections builder method, geometry requirement) for the CURVE (A) family.
# 'holes' is the DIRECT (B) family and handled separately.
_CHAIN = "chain"
_POCKET = "pocket"
_FACE = "face"
_SILHOUETTE = "silhouette"
_HOLES = "holes"
_SELECTIONS = (_CHAIN, _POCKET, _FACE, _SILHOUETTE, _HOLES)

# Which operation parameter carries the selection, by strategy family. The curve param is whichever of
# these the op actually has; we probe in order. (machiningBoundarySel = 3D adaptive/parallel boundary.)
_CURVE_PARAM_CANDIDATES = ("contours", "pockets", "machiningBoundarySel", "stockContours")
# 'holes' = the direct object-list family. DRILL uses 'holeFaces'; BORE/CIRCULAR use 'circularFaces'
# (same CadObjectParameterValue shape — set .value to a list of cylinder faces). Probe in order.
_HOLE_PARAM_CANDIDATES = ("holeFaces", "circularFaces")

_CURVE_BUILDER = {
    _CHAIN: "createNewChainSelection",
    _POCKET: "createNewPocketSelection",
    _FACE: "createNewFaceContourSelection",
    _SILHOUETTE: "createNewSilhouetteSelection",
}

_HEIGHTS = ("top", "bottom")


# ── seams (patched in tests) ─────────────────────────────────────────────────

def _get_cam():
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    for i in range(safe(lambda: doc.products.count, 0) or 0):
        p = safe(lambda i=i: doc.products.item(i))
        if p is not None and safe(lambda p=p: p.productType) == "CAMProductType":
            return adsk.cam.CAM.cast(p), None
    return None, "No CAM data in this document. Create a setup/operation first."


def _find_operation(cam, name):
    """Find an Operation by name across all setups/folders/patterns (recursive)."""
    name = (name or "").strip()
    found = []

    def walk(container):
        ops = safe(lambda: container.operations)
        for i in range(safe(lambda: ops.count, 0) or 0):
            o = safe(lambda i=i: ops.item(i))
            if o is not None:
                found.append(o)
        for getter in (lambda: container.folders, lambda: container.patterns):
            coll = safe(getter)
            for i in range(safe(lambda: coll.count, 0) or 0):
                c = safe(lambda i=i: coll.item(i))
                if c is not None:
                    walk(c)

    for si in range(safe(lambda: cam.setups.count, 0) or 0):
        s = safe(lambda si=si: cam.setups.item(si))
        if s is not None:
            walk(s)
    matches = [o for o in found if safe(lambda o=o: o.name) == name]
    if not matches:
        names = [safe(lambda o=o: o.name) for o in found]
        return None, f"No operation named '{name}'. Operations: {', '.join(str(n) for n in names)[:300]}."
    if len(matches) > 1:
        return None, f"'{name}' is ambiguous — {len(matches)} operations share it. Rename so it's unique."
    return matches[0], None


def _curve_param(op):
    """The op's curve-selection parameter (CadContours2dParameterValue), or None."""
    for nm in _CURVE_PARAM_CANDIDATES:
        p = safe(lambda nm=nm: op.parameters.itemByName(nm))
        if p is not None:
            return p
    return None


def _generate_and_wait(cam, op, timeout=25.0):
    """generateToolpath then PUMP the future to true completion (not op.isGenerating). Returns
    (completed: bool, err: str|None)."""
    try:
        fut = cam.generateToolpath(op)
    except Exception as e:
        return False, str(e)
    t0 = time.time()
    while time.time() - t0 < timeout:
        safe(lambda: adsk.doEvents())
        if safe(lambda: fut.isGenerationCompleted, False) is True:
            return True, None
    return False, "generation did not complete within the time budget"


# ── selection appliers ───────────────────────────────────────────────────────

def _apply_curve(op, selection, entities, is_open, reverted):
    """Mechanism (A): build a CurveSelection of the given kind from `entities` and apply it. Returns
    (selection_count, None) or (None, error)."""
    p = _curve_param(op)
    if p is None:
        return None, (f"Operation '{safe(lambda: op.name)}' has no curve-selection parameter "
                      f"(looked for {', '.join(_CURVE_PARAM_CANDIDATES)}). Its strategy may need a "
                      "different selection kind (e.g. 'holes' for drilling).")
    pv = p.value
    cs = safe(lambda: pv.getCurveSelections())
    if cs is None:
        return None, "Could not read the operation's curve selections."
    safe(lambda: cs.clear())
    builder = _CURVE_BUILDER[selection]
    sel = safe(lambda: getattr(cs, builder)())
    if sel is None:
        return None, f"createNew…({selection}) returned nothing on this operation."
    try:
        sel.inputGeometry = entities          # MUTATION
    except Exception as e:
        return None, f"Could not set inputGeometry for the {selection} selection: {e}"
    # chain-only knobs
    if selection == _CHAIN:
        if is_open is not None:
            safe(lambda: setattr(sel, "isOpen", bool(is_open)))
        if reverted is not None:
            safe(lambda: setattr(sel, "isReverted", bool(reverted)))
    try:
        pv.applyCurveSelections(cs)           # MUTATION
    except Exception as e:
        return None, f"applyCurveSelections failed: {e}"
    return safe(lambda: pv.getCurveSelections().count, 0) or 0, None


def _hole_param(op):
    """The op's cylinder-face selection parameter: drill -> 'holeFaces', bore/circular ->
    'circularFaces' (probe in order). Returns (name, param) or (None, None)."""
    for nm in _HOLE_PARAM_CANDIDATES:
        p = safe(lambda nm=nm: op.parameters.itemByName(nm))
        if p is not None:
            return nm, p
    return None, None


def _apply_holes(op, faces):
    """Mechanism (B): set the op's cylinder-face selection directly (holeFaces for drill,
    circularFaces for bore/circular). Returns (count, None) or (None, error)."""
    nm, p = _hole_param(op)
    if p is None:
        return None, (f"Operation '{safe(lambda: op.name)}' has neither 'holeFaces' nor "
                      "'circularFaces' — 'holes' selection is for drilling/boring strategies "
                      "(drill / bore / circular / tap / …).")
    try:
        p.value.value = faces                 # MUTATION
    except Exception as e:
        return None, f"Could not set {nm}: {e}"
    nv = safe(lambda: p.value.value)
    return (len(list(nv)) if nv is not None else 0), None


def _filter_by_diameter(faces, min_d, max_d):
    """Keep cylinder faces whose diameter (mm) is within [min_d, max_d]. Non-cylinder faces are
    dropped. Returns (kept, dropped_non_cylinder, dropped_out_of_range)."""
    kept, non_cyl, out_range = [], 0, 0
    for f in faces:
        g = safe(lambda f=f: f.geometry)
        r = safe(lambda g=g: g.radius)        # cm; cylinder faces only
        if r is None:
            non_cyl += 1
            continue
        d = r * 20.0                          # cm radius -> mm diameter
        if (min_d is not None and d < min_d - 1e-6) or (max_d is not None and d > max_d + 1e-6):
            out_range += 1
            continue
        kept.append(f)
    return kept, non_cyl, out_range


def _set_height(op, which, mode, offset):
    """Set a top/bottom height via _mode and/or _offset (never the resolved _value). Returns an error
    string, or None on success. Validates each param exists before mutating it."""
    if mode is not None:
        p = safe(lambda: op.parameters.itemByName(f"{which}Height_mode"))
        if p is None:
            return f"{which}Height_mode not found on this operation."
        try:
            p.expression = str(mode)          # ChoiceParameterValue takes the choice string
        except Exception as e:
            return f"Could not set {which}Height_mode='{mode}': {e}"
    if offset is not None:
        p = safe(lambda: op.parameters.itemByName(f"{which}Height_offset"))
        if p is None:
            return f"{which}Height_offset not found on this operation."
        try:
            p.expression = str(offset)
        except Exception as e:
            return f"Could not set {which}Height_offset='{offset}': {e}"
    return None


def handler(operation: str = "", selection: str = "", handles=None,
            is_open: bool = None, reverted: bool = None,
            min_diameter: float = None, max_diameter: float = None,
            top_mode: str = None, top_offset: str = None,
            bottom_mode: str = None, bottom_offset: str = None,
            generate: bool = True) -> dict:
    """Select machining geometry on a CAM operation (+ optional heights), then optionally regenerate.

    operation: op name (cam_get_operations). selection: chain/pocket/face/silhouette/holes.
    handles: find_geometry handles (edges for chain, faces for pocket/face/holes). is_open/reverted:
    chain knobs. min_diameter/max_diameter: filter cylinder faces (mm) for 'holes'. top_*/bottom_*:
    height mode+offset. generate: regenerate after (default true), gated on the async future. WRITES.
    """
    selection = (selection or "").strip().lower()
    if selection not in _SELECTIONS:
        return error(f"selection must be one of {', '.join(_SELECTIONS)}; got '{selection}'.")

    cam, cerr = _get_cam()
    if cerr:
        return error(cerr)
    op, oerr = _find_operation(cam, operation)
    if oerr:
        return error(oerr)

    # resolve geometry handles to live BRep entities (require edge for chain, face otherwise)
    require = "edge" if selection == _CHAIN else "face"
    kind = _inputs.GeometryHandleList("handles", require=require)
    entities, herr = kind.resolve(handles)
    if herr:
        return error(herr)

    result = {"operation": safe(lambda: op.name), "selection": selection}

    # ── heights FIRST (before the selection) ──
    # A height _mode's valid enum is CONTEXT-DEPENDENT and applying a selection can transiently
    # invalidate a value that was valid in the op's settled state (found live: setting
    # bottomHeight_mode after re-applying the chain threw 'Invalid enumeration value'). So set heights
    # while the op is settled, then apply the geometry. (Offsets are robust; modes are the finicky part.)
    applied = []
    for which, mode, offset in (("top", top_mode, top_offset), ("bottom", bottom_mode, bottom_offset)):
        if mode is None and offset is None:
            continue
        herr = _set_height(op, which, mode, offset)
        if herr:
            return error(herr)
        if mode is not None:
            applied.append(f"{which}Height_mode={mode}")
        if offset is not None:
            applied.append(f"{which}Height_offset={offset}")
    if applied:
        result["heights_set"] = applied

    # ── apply the selection ──
    diam_note = None
    if selection == _HOLES:
        faces = entities
        if min_diameter is not None or max_diameter is not None:
            faces, non_cyl, out_range = _filter_by_diameter(entities, min_diameter, max_diameter)
            diam_note = (f"diameter filter [{min_diameter},{max_diameter}]mm kept {len(faces)} "
                         f"(dropped {out_range} out-of-range, {non_cyl} non-cylinder).")
            if not faces:
                return error("No cylinder faces left after the diameter filter. " + diam_note)
        count, aerr = _apply_holes(op, faces)
    else:
        count, aerr = _apply_curve(op, selection, entities, is_open, reverted)
    if aerr:
        return error(aerr)
    if not count:
        return error("Selection applied but the operation reports 0 selections — the geometry was "
                     "rejected. Check the handles match the strategy (edges for chain, the pocket "
                     "floor face for pocket, cylinder faces for holes).")
    result["selections"] = count
    if diam_note:
        result["diameter_filter"] = diam_note

    # ── generate (async, future-gated) ──
    if not generate:
        result["note"] = "Selection applied; pass generate=true (or cam_generate) to compute the toolpath."
        return ok(result)

    completed, gerr = _generate_and_wait(cam, op)
    has_tp = bool(safe(lambda: op.hasToolpath, False))
    valid = bool(safe(lambda: op.isToolpathValid, False))
    warn = safe(lambda: op.warning)
    result.update({"generated": completed, "has_toolpath": has_tp, "toolpath_valid": valid})
    if gerr:
        result["generate_error"] = gerr
        result["note"] = f"Selection applied but generation errored: {gerr}"
    elif has_tp and valid:
        result["note"] = "Selection applied and a valid toolpath generated."
    else:
        # the classic trap: applied + generated but empty, with no warning -> almost always zero depth
        if not warn:
            result["note"] = ("Selection applied and generation completed, but the toolpath is EMPTY "
                              "with no warning — this is almost always ZERO DEPTH (top & bottom "
                              "resolve to the same Z). Set bottom_mode/bottom_offset (or top_*) to "
                              "give the cut real depth. (Drill derives depth from the holes; contour "
                              "does not.)")
        else:
            result["warning"] = warn
            result["note"] = f"Selection applied; toolpath has a warning: {warn}"
    return ok(result)


TOOL_DESCRIPTION = (
    "SELECT the machining geometry on a CAM operation using find_geometry handles, then (optionally) "
    "regenerate. 'selection': chain (seed edges -> Fusion walks a contour chain; is_open/reverted) / "
    "pocket (the pocket-floor face) / face / silhouette / holes (drill/bore/circular: cylinder faces, optionally "
    "filtered by min_diameter/max_diameter in mm). 'handles' = find_geometry handles (edges for chain, "
    "faces otherwise). Optional top_mode/top_offset + bottom_mode/bottom_offset set heights (mode = "
    "e.g. 'from stock top'/'from contour'/'from hole bottom'; never set the resolved _value). WRITES; "
    "generation is async and gated internally. An empty toolpath with no warning usually means ZERO "
    "DEPTH — set a bottom height. Pair: cam_create_operation -> this; find_geometry supplies handles."
)

tool = (
    Tool.create_simple(name="cam_select_geometry", description=TOOL_DESCRIPTION)
    .add_input_property("operation", {"type": "string", "description": "Operation name (cam_get_operations)."})
    .add_input_property("selection", {"type": "string", "enum": list(_SELECTIONS),
            "description": "chain / pocket / face / silhouette / holes."})
    .add_input_property("handles", {"type": "array", "items": {"type": "string"},
            "description": "find_geometry handles: edges for chain, faces for pocket/face/holes."})
    .add_input_property("is_open", {"type": "boolean", "description": "Chain: open profile (default closed)."})
    .add_input_property("reverted", {"type": "boolean", "description": "Chain: flip side/direction."})
    .add_input_property("min_diameter", {"type": "number", "description": "holes: min cylinder Ø (mm)."})
    .add_input_property("max_diameter", {"type": "number", "description": "holes: max cylinder Ø (mm)."})
    .add_input_property("top_mode", {"type": "string", "description": "top height mode, e.g. 'from stock top'."})
    .add_input_property("top_offset", {"type": "string", "description": "top height offset, e.g. '0 mm'."})
    .add_input_property("bottom_mode", {"type": "string", "description": "bottom height mode, e.g. 'from contour'."})
    .add_input_property("bottom_offset", {"type": "string", "description": "bottom height offset, e.g. '-10 mm'."})
    .add_input_property("generate", {"type": "boolean", "description": "Regenerate after (default true)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
