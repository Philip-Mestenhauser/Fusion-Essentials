# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Set the machining geometry (and optional heights) on a CAM operation via find_geometry handles.
Two selection mechanisms exist: curve chains (contours/pockets/boundaries) and direct
object-lists (drill hole faces); heights are a mode+offset parameter group."""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, scale
from ._cam_common import get_cam, resolve_cam_node
from . import _inputs
from . import cam_generate  # its _GENERATIONS registry keeps a launched Future alive (see below)

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
# (same CadObjectParameterValue shape - set .value to a list of cylinder faces). Probe in order.
_HOLE_PARAM_CANDIDATES = ("holeFaces", "circularFaces")

_CURVE_BUILDER = {
    _CHAIN: "createNewChainSelection",
    _POCKET: "createNewPocketSelection",
    _FACE: "createNewFaceContourSelection",
    _SILHOUETTE: "createNewSilhouetteSelection",
}

# ── seams (patched in tests) ─────────────────────────────────────────────────

def _curve_param(op):
    """The op's curve-selection parameter (CadContours2dParameterValue), or None."""
    for nm in _CURVE_PARAM_CANDIDATES:
        p = safe(lambda nm=nm: op.parameters.itemByName(nm))
        if p is not None:
            return p
    return None


def _launch_generation(cam, op, op_name):
    """Launch toolpath generation for the op and return IMMEDIATELY - never wait; generation runs
    in the background on its own. The Future is registered in cam_generate._GENERATIONS (if it were
    garbage-collected, Fusion would ABANDON the in-progress generation; the registry also gives
    cam_get_status's handle path the same read-and-cleanup lifecycle a cam_generate launch gets).
    Returns (handle, None) or (None, err)."""
    try:
        fut = cam.generateToolpath(op)
    except Exception as e:
        return None, str(e)
    if not fut:
        return None, "generateToolpath returned no future."
    handle, _total = cam_generate.register_future(fut, f"operation '{op_name}'", "operation", False)
    return handle, None


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
        return None, f"createNew...({selection}) returned nothing on this operation."
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
                      "'circularFaces' - 'holes' selection is for drilling/boring strategies "
                      "(drill / bore / circular / tap / ...).")
    try:
        p.value.value = faces                 # MUTATION
    except Exception as e:
        return None, f"Could not set {nm}: {e}"
    nv = safe(lambda: p.value.value)
    return (len(list(nv)) if nv is not None else 0), None


def _filter_by_diameter(faces, min_d, max_d, factor):
    """Keep cylinder faces whose diameter (in display units; 'factor' = cm per unit) is within
    [min_d, max_d]. Non-cylinder faces are dropped. Returns (kept, non_cylinder, out_of_range)."""
    kept, non_cyl, out_range = [], 0, 0
    for f in faces:
        g = safe(lambda f=f: f.geometry)
        r = safe(lambda g=g: g.radius)        # cm; cylinder faces only
        if r is None:
            non_cyl += 1
            continue
        d = (2.0 * r) / factor                # cm radius -> diameter in display units
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
            units: str = "mm", generate: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    selection = (selection or "").strip().lower()
    if selection not in _SELECTIONS:
        return error(f"selection must be one of {', '.join(_SELECTIONS)}; got '{selection}'.")

    cam, cerr = get_cam()
    if cerr:
        return error(cerr)
    node, oerr = resolve_cam_node(cam, operation, kinds=("operation",), label="operation")
    if oerr:
        return error(oerr)
    op = node.obj

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
            factor = scale(units)
            if factor is None:
                return error(f"Unknown units '{units}'. Use mm, cm, or in.")
            faces, non_cyl, out_range = _filter_by_diameter(entities, min_diameter, max_diameter, factor)
            diam_note = (f"diameter filter [{min_diameter},{max_diameter}]{units} kept {len(faces)} "
                         f"(dropped {out_range} out-of-range, {non_cyl} non-cylinder).")
            if not faces:
                return error("No cylinder faces left after the diameter filter. " + diam_note)
        count, aerr = _apply_holes(op, faces)
    else:
        count, aerr = _apply_curve(op, selection, entities, is_open, reverted)
    if aerr:
        return error(aerr)
    if not count:
        return error("Selection applied but the operation reports 0 selections - the geometry was "
                     "rejected. Check the handles match the strategy (edges for chain, the pocket "
                     "floor face for pocket, cylinder faces for holes).")
    result["selections"] = count
    if diam_note:
        result["diameter_filter"] = diam_note

    # ── generate: LAUNCH async and return - generation runs in the background on its own ──
    if not generate:
        result["note"] = "Selection applied; pass generate=true (or cam_generate) to compute the toolpath."
        return ok(result)

    op_name = result["operation"] or operation
    handle, gerr = _launch_generation(cam, op, op_name)
    if gerr:
        result["generate_error"] = gerr
        result["note"] = (f"Selection applied but generation failed to launch: {gerr}. The selection "
                          f"is saved - fix the cause, then run cam_generate(target='{op_name}').")
        return ok(result)
    result["launched"] = True
    result["handle"] = handle
    result["note"] = (f"Selection applied; generation is launched and runs in the background - "
                      f"check cam_get_status(target='{op_name}') until completed=true. If it "
                      "completes with has_toolpath False the op produced no path - the "
                      "warning channel can be silent there; check the heights (a zero-depth cut: drill "
                      "derives depth from the holes, contour does not) and the selection.")
    return ok(result)


TOOL_DESCRIPTION = (
    "SELECT the machining geometry on a CAM operation using find_geometry handles, then (optionally) "
    "regenerate. 'selection': chain (seed edges -> Fusion walks a contour chain; is_open/reverted) / "
    "pocket (the pocket-floor face) / face / silhouette / holes (drill/bore/circular: cylinder faces, optionally "
    "filtered by min_diameter/max_diameter in 'units'); see 'handles' for its accepted forms. "
    "Optional top_mode/top_offset + bottom_mode/bottom_offset set heights (mode = "
    "e.g. 'from stock top'/'from contour'/'from hole bottom'; never set the resolved _value). "
    "'generate' (default true) LAUNCHES regeneration and returns immediately - poll "
    "cam_get_status(target=<operation>) until completed=true; the note teaches the empty-toolpath "
    "checks. Pair: cam_create_operation -> this; find_geometry supplies handles."
)

tool = (
    Tool.create_simple(name="cam_select_geometry", description=TOOL_DESCRIPTION)
    .add_input_property("operation", {"type": "string", "description": "Operation name (cam_get(include=['operations']))."})
    .add_input_property("selection", {"type": "string", "enum": list(_SELECTIONS),
            "description": "chain / pocket / face / silhouette / holes."})
    .add_input_property("handles", {"type": "array", "items": {"type": "string"},
            "description": "find_geometry handles: edges for chain, faces for pocket/face/holes."})
    .add_input_property("is_open", {"type": "boolean", "description": "Chain: open profile (default closed)."})
    .add_input_property("reverted", {"type": "boolean", "description": "Chain: flip side/direction."})
    .add_input_property("min_diameter", {"type": "number", "description": "holes: min cylinder dia. (in 'units'); filters the PASSED handles only, never discovers - pass every candidate face."})
    .add_input_property("max_diameter", {"type": "number", "description": "holes: max cylinder dia. (in 'units')."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("top_mode", {"type": "string", "description": "top height mode, e.g. 'from stock top'."})
    .add_input_property("top_offset", {"type": "string", "description": "top height offset, e.g. '0 mm'."})
    .add_input_property("bottom_mode", {"type": "string", "description": "bottom height mode, e.g. 'from contour'."})
    .add_input_property("bottom_offset", {"type": "string", "description": "bottom height offset, e.g. '-10 mm'."})
    .add_input_property("generate", {"type": "boolean", "description": "Launch regeneration after (default true; async - poll cam_get_status)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
