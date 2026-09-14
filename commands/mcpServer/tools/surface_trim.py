# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: trim an OPEN surface body against a tool that intersects and divides it.
TrimFeatures.createInput opens a partial-compute transaction that must be committed via add() or
aborted via TrimFeatureInput.cancel() - never let an exception leak it open. WRITES.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _inputs
from . import _assert
from ._surface_common import _body_names_and_solid
from ._view_common import same_body

app = adsk.core.Application.get()

_SURFACE = _inputs.SurfaceBodyRef("surface", required=True)
_TRIM_TOOL = _inputs.GeometryHandle("trim_tool", require="face", required=True)


def _abort(trim_input):
    """Cancel this tool's open TrimFeatureInput transaction - the shared abort, named for the trim."""
    return _common.cancel_input(trim_input, "trim")


_KEEP_FORMS = "'keep' takes 'larger', 'smaller', or cell index number(s)"


def _parse_keep_indices(keep, total):
    """(set of cell indices, error) for an explicit 'keep'. A value that is not a cell index, or an
    index outside 0..total-1, is REFUSED naming the value - never swapped for the largest cell,
    which would trim away a different piece of the surface than the caller asked to keep."""
    if isinstance(keep, (list, tuple)):
        items = list(keep)
    elif isinstance(keep, str):
        items = [s.strip() for s in keep.split(",") if s.strip()]
    else:
        items = [keep]
    out = set()
    for v in items:
        if isinstance(v, bool):
            return None, f"{_KEEP_FORMS} - {v} is not one."
        try:
            i = int(v)
        except (TypeError, ValueError):
            return None, f"{_KEEP_FORMS} - '{v}' is not one."
        if not 0 <= i < total:
            return None, (f"'keep' index {i} does not exist - this trim computed {total} cell(s), "
                          f"so the valid indices are 0..{total - 1}.")
        out.add(i)
    if not out:
        return None, f"{_KEEP_FORMS} - '{keep}' names no cell."
    return out, ""


def _placement_label(occs, i):
    """One placement's assembly path, its name, or '?' - read POSITIONALLY, so a row that will not
    read still stands in the count the refusal names."""
    return (safe(lambda: occs.item(i).fullPathName) or safe(lambda: occs.item(i).name) or "?")


def _placements_refusal(design, target):
    """The refusal for a target whose component is placed more than once, or whose placement count
    did not read at all - and '' for a component placed once or not at all."""
    # MEASURED: a trim is a feature of the COMPONENT, so both placements changed - and the call
    # reported keeping a 0.478 cm2 cell while 0.785 cm2 landed, read the same through either
    # placement. The cells of such a compute describe neither placement nor the geometry that lands.
    comp = safe(lambda: target.parentComponent)
    root = safe(lambda: design.rootComponent)
    occs = safe(lambda: root.allOccurrencesByComponent(comp)) if comp is not None else None
    n = _common.counted(lambda: occs.count)
    unread = ("the target's own component" if comp is None else
              "the design root" if root is None else
              "the placement census" if occs is None else
              "the placement count" if n is None else "")
    if unread:
        # An unread count is NOT the answer "placed once": the area gates do not catch a
        # multi-placement trim, so this is the only read standing between it and a committed feature.
        return (f"Trim refused: {unread} did not read, so how many times the target's component is "
                "placed is unknown - and a trim of a component placed twice trims every placement. "
                "Re-read the design with assembly_get and retry.")
    if n < 2:
        return ""
    where = _common.named_with_remainder([f"'{_placement_label(occs, i)}'" for i in range(n)])
    return (f"Trim refused: the target's component is placed {n} times ({where}). A trim is a "
            "feature of the COMPONENT, so every placement is trimmed, and the cells the call "
            "reports then describe neither placement nor the geometry that lands. Trim a component "
            "placed once: make this instance unique, or trim before the copies are placed.")


def _hidden_target_refusal(target):
    """The refusal for a target that does not read VISIBLE, or ''."""
    # MEASURED: a body whose bulb is off contributes no cells to createInput at all (a hidden sheet
    # dropped a 5-cell compute to 3), while a hidden TOOL still drives the trim. So a hidden target
    # computes nothing of its own, and the cells that do arrive belong to other bodies.
    if safe(lambda: target.isVisible) is True:
        return ""
    return (f"Trim refused: the target surface '{_inputs.qualified_body_name(target)}' does not "
            "read as visible, and a hidden body contributes no cells for a trim to keep. Show it "
            "with view_set action='show' and retry.")


def _cell_owner(cell):
    """The BRepBody a cell was cut from - BRepCell.sourceTools holds it beside the tool face - or
    None when that read does not answer one, an ownership this trim cannot scope on."""
    tools = safe(lambda: cell.sourceTools) if cell is not None else None
    for t in (_common.iter_collection(tools) if tools is not None else ()):
        if _inputs._is_brep(t):
            return t
    return None


def _select_cells(trim_input, keep, target):
    """(cell facts, the foreign owner bodies, err): every cell is classified by the body its
    sourceTools names, 'larger'/'smaller'/an index picks among the TARGET's cells only, and a cell
    owned by another body or of unread ownership is left unselected - never removed."""
    # For a Trim feature a SELECTED cell is REMOVED, so a kept cell keeps isSelected=False.
    # createInput partial-computes cells over other surfaces the tool crosses too, and with zero
    # cells selected add() raises "No cells are selected".
    cells = trim_input.bRepCells
    total = int(safe(lambda: cells.count, 0) or 0)
    if total == 0:
        return None, [], (_hidden_target_refusal(target)
                          or "Trim failed: the trim tool does not divide the surface (no cells). "
                             "(The trim tool must INTERSECT the surface and divide it.)")

    areas = [float(safe(lambda i=i: cells.item(i).cellBody.area, 0.0) or 0.0) for i in range(total)]
    # A cell's INDEX is its address ('keep' takes an int index, 'areas' and the owner list are
    # indexed by it, and the kept indices are published), so these walks stay positional:
    # iter_collection drops an unreadable cell, sliding every later cell onto the wrong index.
    owners = [_cell_owner(safe(lambda i=i: cells.item(i))) for i in range(total)]
    mine, foreign, unread = [], [], []
    for i, owner in enumerate(owners):
        (unread if owner is None else mine if same_body(owner, target) else foreign).append(i)
    labels = {i: _inputs.qualified_body_name(owners[i]) for i in foreign}
    if len(unread) == total:
        return None, [], (f"Trim refused: none of the {total} cell(s) named an owning body, so this "
                          "trim cannot be scoped to the target. Re-read the surface with "
                          "find_geometry and retry.")
    if not mine:
        whose = _common.named_with_remainder(sorted({f"'{labels[i]}'" for i in foreign}))
        return None, [], (_hidden_target_refusal(target)
                          or f"Trim failed: none of the {total} cell(s) computed belong to the "
                             f"target surface - they belong to {whose}. (The trim tool must "
                             "INTERSECT the TARGET and divide it.)")

    named = keep.strip().lower() if isinstance(keep, str) else keep
    if named in (None, "", [], "larger"):
        # DEFAULT: keep the target's single largest cell by area
        keep_set = {max(mine, key=lambda i: areas[i])}
    elif named == "smaller":
        keep_set = {min(mine, key=lambda i: areas[i])}
    else:
        keep_set, kerr = _parse_keep_indices(named, total)
        if kerr:
            return None, [], kerr
        outside = sorted(set(keep_set) - set(mine))
        if outside:
            owner = labels.get(outside[0])
            whose = f"belongs to '{owner}'" if owner else "named no owning body"
            return None, [], (f"'keep' cell {outside[0]} {whose}, not the target - only the "
                              f"target's own cells can be kept, and they are {mine}.")

    kept_area = 0.0
    mine_set = set(mine)
    for i in range(total):
        cell = safe(lambda i=i: cells.item(i))
        if cell is None:
            continue
        if i in keep_set:
            cell.isSelected = False        # KEEP this cell
            kept_area += areas[i]
        else:
            cell.isSelected = i in mine_set   # REMOVE (select) only a cell the target owns
    info = {"cells_total": total, "cells_kept": sorted(keep_set),
            "cells_removed": [i for i in mine if i not in keep_set],
            "kept_area": round(kept_area, 6),
            "foreign_cells": [{"index": i, "owner": labels[i]} for i in foreign]}
    if unread:
        info["cells_unread"] = len(unread)
    # One body owning several cells is ONE body to read back, keyed on its physical identity - the
    # qualified name alone would merge two same-named bodies out of two same-named components.
    owners_out, seen = [], set()
    for i in foreign:
        key = _common.native_identity(owners[i]) or labels[i]
        if key not in seen:
            seen.add(key)
            owners_out.append(owners[i])
    return info, owners_out, None


def _foreign_effect(rows):
    """(verdict, the bodies whose area moved) over (label, body, area-before) rows: True when every
    foreign body still reads its pre-add area, False when one moved, None when no pair read."""
    changed, read = [], False
    for label, body, before in rows:
        after = safe(lambda body=body: body.area)
        if before is None or after is None:
            continue
        read = True
        if abs(after - before) > abs(before) * 1e-6:
            changed.append(f"'{label}' {round(before, 4)} -> {round(after, 4)} cm2")
    if changed:
        return False, changed
    return (True if read else None), []


def handler(surface=None, trim_tool=None, keep=None) -> dict:
    """Trim a surface against a tool that intersects it - remove the unwanted cell(s)."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    surf, serr = _SURFACE.resolve(surface)     # validates isSolid == false, redirecting error otherwise
    if serr:
        return error(serr)
    tool, terr = _TRIM_TOOL.resolve(trim_tool)
    if terr:
        return error(terr)
    placements = _placements_refusal(design, surf)
    if placements:
        return error(placements)
    area_before = safe(lambda: surf.area)

    # createInput opens a transaction: commit via add or abort via cancel, explicitly and NOT under
    # safe, including on an exception or a null feature.
    trim_input = None
    cell_info = None
    try:
        trim_input = comp.features.trimFeatures.createInput(tool)
        cell_info, foreign_owners, cerr = _select_cells(trim_input, keep, surf)
        if cerr:
            # no intersection, or a 'keep' naming no cell the target owns - either way the open
            # transaction must be aborted before returning, and nothing is trimmed on a guessed cell.
            return error(cerr + _abort(trim_input))
        kept_area = cell_info["kept_area"]
        foreign_before = [(_inputs.qualified_body_name(b), b, safe(lambda b=b: b.area))
                          for b in foreign_owners]
        # PHANTOM-CELL GATE, independent of ownership: a kept area LARGER than the target's own is
        # a cell that cannot be a piece of the target. One-sided - a smaller cell sails through.
        if kept_area is not None and area_before and kept_area > area_before * (1 + 1e-6):
            aborted = _abort(trim_input)
            owners = _common.named_with_remainder(
                sorted({f"'{label}'" for label, _b, _a in foreign_before}) or ["none"])
            return error(
                f"Trim aborted: the kept cells measure {round(kept_area * 100.0, 1)} mm2, more "
                f"than the target's own {round(area_before * 100.0, 1)} mm2. Other cell owners "
                f"read: {owners}. The transaction was cancelled." + aborted)
        feature = comp.features.trimFeatures.add(trim_input)
    except Exception as e:
        # abort the open partial-compute transaction so Fusion isn't left in a bad state
        return error(f"Trim failed: {e}. (The trim tool must INTERSECT the surface and divide "
                     f"it.){_abort(trim_input)}")
    if not feature:
        # add returned nothing but didn't raise - still must abort the transaction we opened
        return error(_common.no_feature_error(design, "Trim",
                                              "(The tool may not intersect the surface.)")
                     + (_abort(trim_input) or " The open transaction was cancelled."))

    names, any_solid = _body_names_and_solid(feature)
    # Commit proof: removing cells must shrink the surface's area; unchanged area = no cell removed.
    area_after = None
    vals = [safe(lambda b=b: b.area) for b in _common.result_bodies(feature)]
    vals = [v for v in vals if v]
    if vals:
        area_after = sum(vals)
    if (cell_info and cell_info["cells_removed"] and area_before and area_after is not None
            and area_after >= area_before * (1 - 1e-6)):
        return error(f"Trim committed but the surface area did not decrease "
                     f"({round(area_before, 4)} cm2 before and after) - no cell was actually removed.")
    unchanged, moved = _foreign_effect(foreign_before)
    if unchanged is False:
        return error("Trim committed but a body the target does not own changed area: "
                     f"{_common.named_with_remainder(moved)}. Undo in Fusion before continuing.")
    owned = len(cell_info["cells_kept"]) + len(cell_info["cells_removed"])
    payload = {
    "trimmed": True,
    "feature": safe(lambda: feature.name),
    "surface": safe(lambda: surf.name),
    "result_body": names[0] if names else None,
    "result_bodies": names,
    "is_solid": any_solid,
    "foreign_bodies_unchanged": unchanged,
    "note": f"Surface trimmed: the target owned {owned} of {cell_info['cells_total']} computed "
            f"cell(s), and {len(cell_info['foreign_cells'])} cell(s) owned by another body were "
            "left in place.",
    }
    payload.update(cell_info)
    unverified = []
    if any_solid is None:
        unverified.append("is_solid")
        payload["note"] += " Not read back off the feature: is_solid."
    if cell_info["foreign_cells"] and unchanged is None:
        unverified.append("foreign_bodies_unchanged")
        payload["note"] += (" No foreign body's area read on both sides of the add, so whether "
                            "this trim left them alone is UNVERIFIED.")
    if unverified:
        payload["unverified"] = unverified
    return ok(payload)


TOOL_DESCRIPTION = (
"Trim a surface body with an intersecting tool; only the target loses cells."
)
tool = (
    Tool.create_simple(name="surface_trim", description=TOOL_DESCRIPTION)
    .add_input_property("surface", _SURFACE.schema())
    .add_input_property("trim_tool", _TRIM_TOOL.schema())
    .add_input_property("keep", {"type": ["string", "array"],
            "description": "'larger' (default), 'smaller', or cell indexes."})
    .add_required_input("surface")
    .add_required_input("trim_tool")
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler,
                             run_on_main_thread=True,
                             postconditions=[_assert.FeatureHealthy()],
                             verification=Verification(
                                 kind="inline", rung="geometry",
                                 evidence_test="tests/unit/test_surface_trim.py::TestSurfaceTrim"
                                               "::test_trim_that_removes_no_area_bites"))


def register_tool():
    register(item)
