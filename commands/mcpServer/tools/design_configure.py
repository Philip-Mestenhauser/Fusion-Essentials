# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""BUILDS and switches a Configured Design: converts the active design and defines configuration rows
plus the columns that vary across them (a parameter, a feature suppress, a body visibility, a
per-config appearance or material theme, or a nested part insert). One action-dispatched verb for the whole
configuration-table subsystem; design_get(include=['configurations']) reads the table; this WRITES it.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, iter_collection, safe
from . import _common
from . import _data_common
from . import _inputs
from ._doc_common import component_solids as _solids, occurrence_pose as _pose
from ._doc_common import path_fault as _path_fault, path_resolves as _resolves
from ._doc_common import same_pose as _same_pose

app = adsk.core.Application.get()

_BODY = _inputs.BodyRef("body")


def _find_row(table, target):
    """Find a configuration row by exact name or id (name first, then id), or None."""
    target = target.strip()
    rows = safe(lambda: table.rows)
    if not rows:
        return None
    for attr in ("name", "id"):
        try:
            for r in rows:
                if (safe(lambda r=r, a=attr: getattr(r, a)) or "") == target:
                    return r
        except Exception:
            pass
    return None

_ACTIONS = ("create", "activate", "add_configuration", "rename_configuration", "add_parameter",
            "add_suppress", "add_visibility", "set_appearance", "add_material", "add_insert",
            "delete_configuration", "copy_configuration", "switch_occurrence", "set_parameter")
_OCCURRENCE = _inputs.OccurrenceRef("occurrence")


# ── resolvers (patched in tests; real lookups here) ─────────────────────────

# Timeline entity names are NOT design-wide unique (two components can each hold an 'Extrude1'), so
# the suppress column's target goes through FeatureRef, which refuses the ambiguity.
_FEATURE = _inputs.FeatureRef("feature", allow_group=True)


def _resolve_appearance(design, name):
    """An appearance by name already present in the design's appearances."""
    apps = safe(lambda: design.appearances)
    return safe(lambda: apps.itemByName(name)) if apps else None


def _resolve_material(design, name):
    """A physical material by EXACT (case-insensitive) name among the design's OWN materials.
    Returns (material, error_or_None). A material cell only accepts a material that already exists
    in the design, so a library material has to be copied in before it can be configured. A name two
    document materials share is refused rather than resolved to whichever comes first."""
    want = (name or "").strip()
    if not want:
        return None, "Provide a material name for every configuration in 'materials'."
    hits, names = [], []
    for m in iter_collection(safe(lambda: design.materials)):
        nm = safe(lambda m=m: m.name)
        if not nm:
            continue
        names.append(nm)
        if nm.lower() == want.lower():
            hits.append(m)
    listed = ", ".join(f"'{n}'" for n in sorted(set(names))[:12]) or "none"
    if not hits:
        return None, (f"No material named '{want}' in the design. Copy it into the design first "
                      "(design.materials.addByCopy from a loaded material library) - a configuration "
                      f"material cell only accepts a material the design already holds. In the "
                      f"design now: {listed}.")
    if len(hits) > 1:
        return None, (f"'{want}' names {len(hits)} of the design's materials - refusing to pick one. "
                      "Rename or remove the duplicate, then retry.")
    return hits[0], None


def _resolve_datafile(design, name_or_id):
    """(DataFile, problem): a configured-design DataFile by lineage id (a urn, or a Fusion web/share
    URL), else by name in the ACTIVE DOCUMENT's own project - the project a referenced insert
    requires the part to share. Patched in tests."""
    # A urn or a pasted Fusion web URL: route through the shared candidate decoder, which also
    # base64url-decodes the lineage segment a share link carries (a raw startswith('urn:') check
    # silently failed on a pasted URL).
    df, _resolved, _tried = _data_common._resolve_data_file(name_or_id)
    if df:
        return df, None
    proj, problem = _data_common.active_project()
    if not proj:
        return None, problem
    folder = safe(lambda: proj.rootFolder)
    if folder is None:
        return None, f"the root folder of project '{safe(lambda: proj.name)}' does not read."
    # A folder can hold several files of one name (distinct lineages), so the shared resolver
    # refuses that rather than handing back whichever comes first.
    hit, shared = _data_common._file_in_folder_by_name(folder, name_or_id)
    if shared:
        return None, shared
    return hit, None


def _part_config_rows(datafile):
    """{name: ConfigurationRow} from a configured part's DataFile table."""
    ct = safe(lambda: datafile.configurationTable)
    out = {}
    if not ct:
        return out
    rows = safe(lambda: ct.rows)
    for i in range(safe(lambda: rows.count, 0) or 0):
        r = safe(lambda i=i: rows.item(i))
        nm = safe(lambda r=r: r.name)
        if r is not None and nm:
            out[nm] = r
    return out


def _top_table(design):
    return safe(lambda: design.configurationTopTable)


def _doc_is_saved():
    """True if the active document has ever been saved (has a cloud file) - the configured-design
    conversion only materializes for the user once saved. Patched in tests."""
    return bool(safe(lambda: app.activeDocument.isSaved, False))


def _row_names(table):
    out = []
    for i in range(safe(lambda: table.rows.count, 0) or 0):
        out.append(safe(lambda i=i: table.rows.item(i).name))
    return out


# The unit a TEXT parameter reads (measured: a text parameter reads 'Text', a length one 'mm').
_TEXT_UNIT = "Text"


def _column_ids(table):
    """Every column id the table lists, or None when the collection would not enumerate - an
    unreadable list is not an empty one, and a rollback may not be claimed on it."""
    cols = safe(lambda: table.columns)
    n = safe(lambda: cols.count) if cols is not None else None
    if not isinstance(n, int) or isinstance(n, bool):
        return None
    return [safe(lambda i=i: cols.item(i).id) for i in range(n)]


def _column_rolled_back(table, col):
    """Remove a column whose cells could not be filled and PROVE it is gone - the table must stop
    listing its id. deleteMe answering true is the request, not the proof."""
    col_id = safe(lambda: col.id)
    if not safe(lambda: col.deleteMe()):
        return False
    after = _column_ids(table)
    return after is not None and col_id is not None and col_id not in after


# ── action handlers ──────────────────────────────────────────────────────────

def _do_create(design):
    if _top_table(design):
        return ok({"configured": True, "created": False,
                   "note": "Design is already a configured design; reusing its configuration table. "
                           "Add configurations with action='add_configuration' and columns with "
                           "add_parameter / add_suppress / add_visibility / set_appearance."})
    if not _doc_is_saved():
        return error("Save the document first (doc_save_as), THEN run create. Converting an unsaved "
                     "document builds the table only in memory - it won't materialize as a configured "
                     "design (no DataFile to carry it, and the UI won't show the Configurations panel). "
                     "Save is the commit step for the conversion.")
    table = design.createConfiguredDesign()      # MUTATION - let it raise on failure
    if not table:
        return error("createConfiguredDesign() returned no table.")
    return ok({"configured": True, "created": True,
               "configurations": _row_names(table),
               "note": "Design converted to a configured design (one configuration so far). Add "
                       "configurations and columns with the other actions. To see it in the UI: SAVE, "
                       "then REOPEN by the URN that save reports - the document can move to a NEW "
                       "lineage on a save (lineage_changed), and the pre-conversion one is relocated "
                       "to a Fusion-managed project ('System Project - CONFIG') that deleting this "
                       "design leaves behind."})


def _do_activate(design, table, name):
    """Switch the live design to a configuration (by name or id) and rebuild, so its geometry shows."""
    target = (name or "").strip()
    if not target:
        return error("Provide 'name' - the configuration to activate (a configuration name or id).")
    row = _find_row(table, target)
    if not row:
        avail = ", ".join(_row_names(table)) or "(none)"
        return error(f"No configuration matched '{target}'. Available: {avail}.")
    before = safe(lambda: table.activeRow.name)
    err_before, _, _ = _common.timeline_health(design)
    if not safe(lambda: row.activate(), False):
        return error(f"Activating configuration '{target}' failed (activate() returned false).")
    safe(lambda: design.computeAll()) # rebuild so the switched geometry shows
    now = safe(lambda: table.activeRow.name)
    want_row = safe(lambda: row.name)
    if now is not None and want_row is not None and now != want_row:
        return error(f"activate() returned true but the active configuration still reads '{now}' "
                     f"(expected '{want_row}') - the switch did not take.")
    # A different configuration can drive parameters that over/under-constrain the model, so the
    # rebuilt geometry may carry NEW timeline errors even though the switch itself took. Surface them
    # (the switch stands) rather than reporting a clean success over a broken model.
    err_after, warn_after, _ = _common.timeline_health(design)
    out = {"activated": True, "requested": target, "previous": before,
           "now_active": now,
           "note": "Configuration switched + rebuilt. Pair with view_screenshot to view it, or "
                   "design_get(include=['timeline']) / param_get to see what changed."}
    if len(err_after) > len(err_before):
        out["timeline_warning"] = (
            f"Switching to '{target}' left the timeline with a new error ({err_after}). This "
            "configuration's values may over/under-constrain the model - the switch stands; inspect "
            "with design_get(include=['timeline']).")
    elif warn_after:
        out["timeline_warnings"] = warn_after
    return ok(out)


def _do_add_configuration(table, name):
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' for the new configuration (e.g. 'Large').")
    if name in _row_names(table):
        return error(f"A configuration named '{name}' already exists.")
    row = table.rows.add(name)                   # MUTATION
    if not row:
        return error(f"Adding configuration '{name}' failed.")
    # Adding a row to the TOP table ACTIVATES the new configuration - the design now shows it, not
    # the one that was active before the call. Publish the read-back so the caller can see the switch.
    return ok({"configuration": name, "id": safe(lambda: row.id),
               "configurations": _row_names(table),
               "active_configuration": safe(lambda: table.activeRow.name),
               "note": "Configuration row added, and adding it ACTIVATED it - 'active_configuration' "
                       "is what the design shows now; switch back with action='activate'. Set its "
                       "values via add_parameter/add_suppress/add_visibility/set_appearance/"
                       "add_material (address by this name)."})


def _do_rename_configuration(table, name, new_name):
    """Rename an existing configuration row (the default row is 'Configuration 1' - usually worth
    renaming to something meaningful like 'Medium')."""
    name = (name or "").strip()
    new_name = (new_name or "").strip()
    if not name or not new_name:
        return error("Provide 'name' (the existing configuration) and 'new_name' (what to call it).")
    row = _find_row(table, name)
    if not row:
        return error(f"No configuration named '{name}'. Existing: "
                     f"{', '.join(str(n) for n in _row_names(table))}.")
    if new_name in _row_names(table) and new_name != name:
        return error(f"A configuration named '{new_name}' already exists.")
    row.name = new_name                          # MUTATION
    if safe(lambda: row.name) != new_name:
        return error(f"Renaming '{name}' to '{new_name}' did not take (the API may still be persisting "
                     "a recent save - retry shortly).")
    return ok({"renamed": True, "from": name, "to": new_name,
               "configurations": _row_names(table),
               "note": "Configuration renamed. Address it by the new name from now on."})


_ROW_CAP = 1000         # rows one census reads
_LIST_CAP = 100         # rows or columns one reply lists

# The cell reads the add_* actions verify by; a field a cell does not carry raises and is skipped.
_CELL_FIELDS = (("expression", lambda c: c.expression),
                ("is_suppressed", lambda c: c.isSuppressed),
                ("is_visible", lambda c: c.isVisible),
                ("part_configuration", lambda c: c.row.name),
                ("theme_row", lambda c: c.referencedTableRow.name))


def _row_census(table):
    """(records, row objects, problem): every top-table row as {name, id, index, active}."""
    rows = safe(lambda: table.rows)
    count = _common.counted(lambda: rows.count)
    if count is None:
        return None, None, "the configuration rows did not count"
    if count > _ROW_CAP:
        return None, None, f"the table holds {count} rows, over the {_ROW_CAP} read here"
    active_id = safe(lambda: table.activeRow.id)
    records, objects = [], []
    for i in range(count):
        row = safe(lambda i=i: rows.item(i))
        name, row_id = safe(lambda: row.name), safe(lambda: row.id)
        if name is None or row_id is None:
            return None, None, f"configuration row {i} did not read its name and id"
        # ConfigurationRow.index is published as read; its base is not assumed.
        records.append({"name": name, "id": row_id, "index": safe(lambda: row.index),
                        "active": None if active_id is None else row_id == active_id})
        objects.append(row)
    return records, objects, None


def _one_row(records, target):
    """(position, problem): the ONE census row whose name, else id, is exactly `target`."""
    for key in ("name", "id"):
        hits = [i for i, r in enumerate(records) if r[key] == target]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            ids = _common.named_with_remainder([str(records[i]["id"]) for i in hits])
            return None, (f"'{target}' matches {len(hits)} configurations by {key} (ids: {ids}). "
                          "Pass one row's id as 'name'.")
    names = _common.named_with_remainder([str(r["name"]) for r in records], _LIST_CAP)
    return None, f"No configuration matched '{target}'. Available: {names or '(none)'}."


def _row_ref(record):
    """A census row as the {name, id, index} a reply publishes."""
    return {k: record[k] for k in ("name", "id", "index")}


def _active_ref(records):
    """{name, id} of the row the census marks active, or None when the active row did not read."""
    hits = [r for r in records if r["active"] is True]
    return {"name": hits[0]["name"], "id": hits[0]["id"]} if len(hits) == 1 else None


def _active_facts(out, before, after):
    """Fill active_before/active_after/active_changed in `out`; returns the two active refs."""
    was, now = _active_ref(before), _active_ref(after)
    out["active_before"], out["active_after"] = was, now
    out["active_changed"] = None if was is None or now is None else was["id"] != now["id"]
    if out["active_changed"] is None:
        out["active_unread"] = "the active row did not read on both sides of the write"
    return was, now


def _table_state(before, after, problem):
    """One sentence saying whether the re-read census equals the one before the write."""
    if after is None:
        return f"The table did not re-read ({problem}); read design_get(include=['configurations'])."
    names = _common.named_with_remainder([f"'{r['name']}'" for r in after], _LIST_CAP)
    active = _active_ref(after)
    state = f"rows {names or '(none)'}" + (f"; active '{active['name']}'" if active else "")
    return (f"The table reads unchanged ({state})." if after == before
            else f"The table now reads {state}.")


def _cell_facts(col, row_name):
    """The fields of one cell that read, addressed by row name; None when none did."""
    cell = safe(lambda: col.getCellByRowName(row_name))
    if cell is None:
        return None
    facts = {}
    for key, read in _CELL_FIELDS:
        value = safe(lambda read=read: read(cell))
        if value is not None:
            facts[key] = value
    return facts or None


def _copied_cells(table, source_name, copy_name):
    """(rows, truncated, problem): per column, the copy's cell against its source's cell."""
    cols = safe(lambda: table.columns)
    count = _common.counted(lambda: cols.count)
    if count is None:
        return None, False, "the table's columns did not count"
    rows = []
    for i in range(min(count, _LIST_CAP)):
        col = safe(lambda i=i: cols.item(i))
        src, cpy = _cell_facts(col, source_name), _cell_facts(col, copy_name)
        row = {"column": safe(lambda: col.title), "column_id": safe(lambda: col.id),
               "source": src, "copy": cpy, "equal": None}
        if src is None or cpy is None:
            blank = [w for w, v in (("source", src), ("copy", cpy)) if v is None]
            row["unread"] = f"no known field read on the {' and the '.join(blank)} cell"
        elif set(src) != set(cpy):
            row["unread"] = "the two cells read different fields"
        else:
            row["equal"] = src == cpy
        rows.append(row)
    return rows, count > _LIST_CAP, None


def _do_copy_configuration(table, name, new_name):
    """Copy one configuration row and judge the copy by the row census taken after it."""
    name, new_name = (name or "").strip(), (new_name or "").strip()
    if not name:
        return error("Provide 'name' - the configuration to copy (its name or id); 'new_name' "
                     "names the copy.")
    before, objects, problem = _row_census(table)
    if problem:
        return error(f"Nothing was copied: {problem}. Read design_get(include=['configurations']).")
    pos, problem = _one_row(before, name)
    if problem:
        return error(problem)
    source = before[pos]
    made = objects[pos].copy(new_name)           # MUTATION
    after, _objects, problem = _row_census(table)
    if problem:
        return error(f"copy() ran on configuration '{source['name']}', but afterwards {problem} - "
                     "whether a row was added is unread. Read "
                     "design_get(include=['configurations']) before retrying.")
    known = {r["id"] for r in before}
    fresh = [r for r in after if r["id"] not in known]
    made_id = safe(lambda: made.id) if made is not None else None
    landed = [r for r in fresh if r["id"] == made_id]
    if len(landed) != 1:
        listed = _common.named_with_remainder([f"'{r['name']}'" for r in fresh])
        return error(f"Copying configuration '{source['name']}' did not verify: copy() returned "
                     + ("no row" if made is None else f"a row whose id reads {made_id!r}")
                     + f" and the table lists {len(fresh)} new row(s)"
                     + (f" ({listed})" if fresh else "")
                     + ". Read design_get(include=['configurations']) before retrying.")
    new = landed[0]
    out = {"copied": True, "source": _row_ref(source), "configuration": new["name"],
           "id": new["id"], "index": new["index"]}
    note = f"Configuration '{source['name']}' copied as '{new['name']}'"
    if new_name and new["name"] != new_name:
        out["requested_name"], out["adopted_differs"] = new_name, True
        note += f", not the requested '{new_name}'"
    was, now = _active_facts(out, before, after)
    if out["active_changed"]:
        note += f". The active configuration reads '{now['name']}' now (was '{was['name']}')"
    shared = [n for n in (source["name"], new["name"])
              if sum(1 for r in after if r["name"] == n) != 1]
    if shared:
        out["cells"] = None
        out["cells_unread"] = ("cells are addressed by row name, and more than one row is named "
                               + _common.named_with_remainder([f"'{n}'" for n in shared]))
    else:
        cells, truncated, problem = _copied_cells(table, source["name"], new["name"])
        out["cells"] = cells
        if problem:
            out["cells_unread"] = problem
        if truncated:
            out["cells_truncated"] = True
    out["configurations"] = [r["name"] for r in after[:_LIST_CAP]]
    if len(after) > _LIST_CAP:
        out["configurations_truncated"] = True
    # Only a cell read on both sides can differ; an unread cell stays unknown and is no mismatch.
    differ = [str(c["column"] or c["column_id"]) for c in out["cells"] or [] if c["equal"] is False]
    if differ:
        out["verified"], out["cells_differ"] = False, differ
        return error(f"{note}, but {len(differ)} copied cell(s) read differently from the source "
                     f"in column(s) {_common.named_with_remainder(differ)}; the new row stays in "
                     "the table. Read design_get(include=['configurations']); to discard the copy, "
                     f"call action='delete_configuration', name='{new['id']}'.", out)
    out["note"] = note + ". Address the copy by 'configuration' or 'id'."
    return ok(out)


def _do_delete_configuration(table, name):
    """Delete one configuration row and judge the delete by the row census taken after it."""
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' - the configuration to delete (its name or id).")
    before, objects, problem = _row_census(table)
    if problem:
        return error(f"Nothing was deleted: {problem}. Read design_get(include=['configurations']).")
    pos, problem = _one_row(before, name)
    if problem:
        return error(problem)
    target = before[pos]
    try:
        answered = objects[pos].deleteMe()       # MUTATION
    except Exception as exc:
        after, _objects, problem = _row_census(table)
        return error(f"Fusion refused deleting configuration '{target['name']}': {exc} "
                     + _table_state(before, after, problem))
    after, _objects, problem = _row_census(table)
    if problem:
        return error(f"deleteMe() answered {answered!r} for configuration '{target['name']}', but "
                     f"afterwards {problem} - whether the row is gone is unread. Read "
                     "design_get(include=['configurations']) before retrying.")
    still = any(r["id"] == target["id"] for r in after)
    if answered is not True or still:
        return error(f"Deleting configuration '{target['name']}' did not verify: deleteMe() "
                     f"answered {answered!r} and its id {target['id']!r} is "
                     f"{'still' if still else 'no longer'} listed. "
                     + _table_state(before, after, problem))
    out = {"deleted": _row_ref(target), "remaining": [_row_ref(r) for r in after[:_LIST_CAP]]}
    if len(after) > _LIST_CAP:
        out["remaining_truncated"] = True
    was, now = _active_facts(out, before, after)
    note = (f"Configuration '{target['name']}' deleted; this tool cannot restore a deleted "
            "configuration.")
    if target["active"] is True or out["active_changed"]:
        note += " It was the active one: " if target["active"] is True else " "
        note += (f"'{now['name']}' is active now" if now is not None
                 else "which configuration is active now did not read")
        note += ("; the active configuration changed, so read the model with design_get / "
                 "param_get.")
    out["note"] = note
    return ok(out)


def _validate_rows(table, value_map):
    """Every key in value_map must be an existing configuration row name."""
    names = set(_row_names(table))
    unknown = [k for k in value_map if k not in names]
    return unknown


def _do_add_parameter(design, table, parameter, values):
    if not parameter:
        return error("Provide 'parameter' - the name of a model parameter to vary across configurations.")
    values = values or {}
    p = safe(lambda: design.allParameters.itemByName(parameter))
    if not p:
        return error(f"No parameter named '{parameter}'. (Add/expose it first; a parameter column "
                     "only matters if the parameter drives geometry.)")
    unknown = _validate_rows(table, values)
    if unknown:
        return error(f"Values reference configurations that don't exist: {', '.join(unknown)}. "
                     f"Existing: {', '.join(str(n) for n in _row_names(table))}.")
    # A TEXT parameter's column is refused BEFORE it lands rather than rolled back after. Measured
    # on a scratch configured design: the column is created, then every write to it is dropped - so
    # the refusal is about the column being undrivable, not about this tool's read-back.
    if safe(lambda: p.unit) == _TEXT_UNIT:
        return error(f"'{parameter}' is a {_TEXT_UNIT} parameter and a {_TEXT_UNIT} column cannot "
                     "be driven: a cell's expression and its textValue each accept a write and "
                     "discard it, cell.textValue raises on read, and the parameter's textValue is "
                     "the same after activating each row. Vary a length/number parameter here, and "
                     "relabel per configuration with param_set after activating the row.")
    col = table.columns.addParameterColumn(p)    # MUTATION
    if not col:
        return error(f"addParameterColumn for '{parameter}' returned null.")
    n = 0
    for rname, expr in values.items():
        cell = safe(lambda rname=rname: col.getCellByRowName(rname))
        if cell is None:
            return error(f"No cell for configuration '{rname}' in the '{parameter}' column.")
        try:
            cell.expression = str(expr) # MUTATION
        except Exception as exc:
            removed = _column_rolled_back(table, col)
            return error(f"Setting '{parameter}' cell '{rname}' to {expr!r} failed: {exc}"
                         + (f" ({n} earlier cell(s) read back)" if n else "") + ". "
                         + ("The new column was removed." if removed else
                            "New-column removal is unconfirmed.")
                         + " Read design_get(include=['configurations']) before retrying.")
        # An unreadable read-back is a FAILURE, not a pass - the same rule the material path
        # states; a silently dropped write reads back None here.
        got = safe(lambda cell=cell: cell.expression)
        if got is None or got != str(expr):
            # The COLUMN is a mutation that already landed, so a bare refusal would leave an
            # orphan behind and every retry would add another. Roll it back and say which happened.
            removed = _column_rolled_back(table, col)
            return error(f"Cell '{rname}' of the '{parameter}' column reads "
                         f"{'nothing' if got is None else repr(got)} after the set - the "
                         f"expression '{expr}' did not verifiably take"
                         + (f" ({n} earlier cell(s) did)" if n else "") + ". "
                         + ("The column has been rolled back." if removed else
                            "The column could NOT be auto-removed and is still on the table - "
                            "delete it before retrying.")
                         + " Relabel per configuration with param_set after activating the row.")
        n += 1
    return ok({"parameter": parameter, "column_id": safe(lambda: col.id), "set": n,
               "note": "Parameter column added and per-configuration expressions set. Switch with "
                       "design_configure(action='activate', name=...) - the geometry rebuilds only if this "
                       "parameter drives a dimension."})


def _do_set_parameter(table, parameter, values):
    """Set one existing inactive parameter cell and read its expression back."""
    if not parameter:
        return error("Provide 'parameter' - the parameter column to edit.")
    if not isinstance(values, dict) or len(values) != 1:
        return error("set_parameter needs 'values' with exactly one {configuration: expression} entry.")
    target, expression = next(iter(values.items()))
    if not isinstance(expression, str) or not expression.strip():
        return error(f"The expression for configuration {target!r} must be a nonempty string; "
                     f"got {expression!r}.")
    records, _rows, problem = _row_census(table)
    if records is None:
        return error(f"Cannot edit {parameter!r}: {problem}.")
    position, problem = _one_row(records, target)
    if problem:
        return error(problem)
    row = records[position]
    if row["active"] is None:
        return error("The active configuration did not read; switch to a different row and retry.")
    if row["active"]:
        return error(f"Configuration '{row['name']}' is active. Activate another row before "
                     "editing this cell, then activate it again to verify the driven model.")
    columns = safe(lambda: table.columns)
    count = _common.counted(lambda: columns.count)
    if count is None or count > _ROW_CAP:
        return error(f"The configuration columns did not read within the {_ROW_CAP} column bound.")
    matches = []
    for index in range(count):
        col = safe(lambda index=index: columns.item(index))
        if col is None:
            return error(f"Configuration column {index} did not read; cannot establish a unique "
                         f"'{parameter}' parameter column. Nothing was written.")
        if type(col).__name__ != "ConfigurationParameterColumn":
            continue
        controlled = safe(lambda col=col: col.parameter)
        controlled_name = safe(lambda controlled=controlled: controlled.name)
        if controlled_name is None:
            return error(f"Parameter column {index} did not identify its parameter; cannot "
                         f"establish a unique '{parameter}' column. Nothing was written.")
        if controlled_name == parameter:
            matches.append((col, controlled))
    if len(matches) != 1:
        return error(f"Expected one existing parameter column for '{parameter}', found "
                     f"{len(matches)}. Read design_get(include=['configurations']) to inspect "
                     "the columns before editing.")
    col, controlled = matches[0]
    if safe(lambda: controlled.unit) == _TEXT_UNIT:
        return error(f"'{parameter}' is a {_TEXT_UNIT} parameter; its configuration cell may "
                     "discard writes. Vary a numeric parameter instead.")
    col_id = safe(lambda: col.id)
    cell = safe(lambda: col.getCellByRowId(row["id"]))
    before = safe(lambda: cell.expression)
    if col_id is None or cell is None or before is None:
        return error(f"The existing '{parameter}' cell for '{row['name']}' did not read its "
                     "column id and expression. Nothing was written.")
    if before == expression:
        return ok({"changed": False, "parameter": parameter, "configuration": _row_ref(row),
                   "column_id": col_id, "expression": before,
                   "note": "The cell already has this expression; nothing was written."})
    try:
        cell.expression = expression
    except Exception as exc:
        after = safe(lambda: cell.expression)
        return error(f"Setting '{parameter}' cell '{row['name']}' to {expression!r} raised "
                     f"{exc}; it now reads {after!r} after {before!r} before. "
                     "Read design_get(include=['configurations']) before retrying.")
    after = safe(lambda: cell.expression)
    new_records, _rows, row_problem = _row_census(table)
    if after != expression or new_records != records:
        return error(f"The '{parameter}' cell for '{row['name']}' reads {after!r} after "
                     f"{expression!r} was assigned (before {before!r}); "
                     + (f"row census changed: {new_records!r}." if new_records is not None else
                        f"row census did not read: {row_problem}.")
                     + " Read design_get(include=['configurations']) before retrying.")
    return ok({"changed": True, "parameter": parameter, "configuration": _row_ref(row),
               "column_id": col_id, "previous_expression": before, "expression": after,
               "active_configuration": _active_ref(new_records),
               "note": "The inactive cell reads the new expression. The active model remains on "
                       "its prior row; activate this configuration, then read param_get and "
                       "model_inspect to verify driven geometry."})


def _group_members(group, count):
    """[(name, isSuppressed or None)] for each member of a timeline group, in group order."""
    out = []
    for k in range(count):
        member = safe(lambda k=k: group.item(k))
        out.append((safe(lambda: member.name) or f"member {k}",
                    _common.read_flag(lambda: member.isSuppressed)))
    return out


def _group_snapshot(group):
    """(snapshot, problem): a timeline group's name, index, collapsed flag and members as read."""
    count = _common.counted(lambda: group.count)
    if count is None:
        return None, "the group's member count did not read"
    if count > _ROW_CAP:
        return None, f"the group holds {count} members, over the {_ROW_CAP} read here"
    return {"name": safe(lambda: group.name), "index": safe(lambda: group.index),
            "collapsed": _common.read_flag(lambda: group.isCollapsed), "member_count": count,
            "members": _group_members(group, count)}, None


def _group_cells(col, rows):
    """[{configuration, id, suppressed}] - the group column's cell per census row, as read."""
    cells = []
    for r in rows[:_LIST_CAP]:
        got = _common.read_flag(lambda r=r: col.getCellByRowId(r["id"]).isSuppressed)
        cells.append({"configuration": r["name"], "id": r["id"], "suppressed": got})
        if got is None:
            cells[-1]["unread"] = "the cell's isSuppressed did not read"
    return cells


def _suppress_group(table, group, suppressed_in):
    """add_suppress for a timeline group: the column by id and title, each requested cell re-read."""
    before, _objects, problem = _row_census(table)
    if problem:
        return error(f"No column was added: {problem}. Read design_get(include=['configurations']).")
    snap, problem = _group_snapshot(group)
    if problem:
        return error(f"No column was added: {problem}. Read design_get(include=['timeline']).")
    known = _column_ids(table)
    if known is None:
        return error("No column was added: the table's columns did not list. Read "
                     "design_get(include=['configurations']).")
    wanted = []
    for target in suppressed_in or []:
        pos, problem = _one_row(before, str(target))
        if problem:
            return error("No column was added - suppressed_in: "
                         + problem.replace("as 'name'", "in 'suppressed_in'"))
        if pos not in wanted:
            wanted.append(pos)
    name = snap["name"]
    col = table.columns.addSuppressColumn(group)  # MUTATION
    kind = type(col).__name__ if col else None
    col_id = safe(lambda: col.id) if col else None
    listed = _column_ids(table)
    fresh = None if listed is None else [i for i in listed if i not in known]
    existed = col_id is not None and col_id in known and fresh == []
    if kind != "ConfigurationSuppressColumn" or col_id is None or not (existed
                                                                     or fresh == [col_id]):
        return error(f"The suppress column for group '{name}' did not verify: addSuppressColumn "
                     + ("returned nothing" if not col else
                        f"returned a {kind} whose id reads {col_id!r}")
                     + (", and the columns did not re-list" if fresh is None else
                        f", and the table lists {len(fresh)} new column id(s)")
                     + ". Read design_get(include=['configurations']) before retrying.")
    # A group column's .feature reads None, so the column is known by its id and title alone.
    title = safe(lambda: col.title)
    took, failed = [], []
    for pos in wanted:
        row, why = before[pos], None
        cell = safe(lambda row=row: col.getCellByRowId(row["id"]))
        if cell is None:
            why = "no cell"
        else:
            try:
                cell.isSuppressed = True          # MUTATION
            except Exception as exc:
                why = f"the set raised: {exc}"
        if why is None:
            got = _common.read_flag(lambda cell=cell: cell.isSuppressed)
            why = None if got is True else f"reads {'nothing' if got is None else got!r}"
        if why is None:
            took.append(row)
        else:
            failed.append(f"'{row['name']}' ({why})")
    after, _objects, problem = _row_census(table)
    rows = before if after is None else after
    active = None if after is None else _active_ref(after)
    members = [n for n, _s in snap["members"]]
    out = {"target_kind": "group",
           "group": {"name": name, "index": snap["index"], "member_count": snap["member_count"],
                     "members": members[:_LIST_CAP]},
           "column_id": col_id, "column_title": title, "column_existed": existed,
           "suppressed_in": [r["name"] for r in took],
           "cells": _group_cells(col, rows), "active_configuration": active}
    if len(members) > _LIST_CAP:
        out["group"]["members_truncated"] = True
    if len(rows) > _LIST_CAP:
        out["cells_truncated"] = True
    if active is None:
        out["active_unread"] = problem or "the active row did not read after the write"
    state = "was already in the table" if existed else "was added"
    if failed:
        out["cells_failed"] = failed
        return error(f"Suppress column {title!r} (id {col_id}) for group '{name}' {state} and "
                     f"stays, but {len(failed)} of {len(wanted)} requested cell(s) do not read "
                     f"suppressed: {_common.named_with_remainder(failed)}. Cells that do: "
                     + (_common.named_with_remainder([f"'{r['name']}'" for r in took]) or "none")
                     + ". Read design_get(include=['configurations']).", out)
    note = (f"Suppress column {title!r} {state} for timeline group '{name}'; a group column is "
            "linked to its group by title only.")
    if title != name:
        note += " That title is not the group's name."
    live = active is not None and any(r["id"] == active["id"] for r in took)
    later = [f"'{r['name']}'" for r in took if not (live and r["id"] == active["id"])]
    if live:
        now = _group_members(group, snap["member_count"])
        on = [n for n, s in now if s is True]
        off = [n for n, s in now if s is False]
        unread = [n for n, s in now if s is None]
        if unread or not now:
            out["members_suppressed_now"] = None
            out["members_unread"] = ("the group holds no members" if not now else
                                     "isSuppressed did not read on "
                                     + _common.named_with_remainder(unread))
        elif on and off:
            out["members_suppressed_now"] = "mixed"
            out["members_differ"] = {"suppressed": on[:_LIST_CAP],
                                     "not_suppressed": off[:_LIST_CAP]}
            return error(f"Group '{name}' reads half applied under the active configuration "
                         f"'{active['name']}': suppressed {_common.named_with_remainder(on)}; "
                         f"not suppressed {_common.named_with_remainder(off)}. The column and "
                         "its cells stay. Read design_get(include=['timeline']).", out)
        else:
            out["members_suppressed_now"] = bool(on)
        note += (f" '{active['name']}' is active: members_suppressed_now is the members' "
                 "state read after the write.")
    if later:
        note += (f" The model changes for {_common.named_with_remainder(later)} when that "
                 "configuration is activated (action='activate').")
    if not wanted:
        note += " No cell was set; name configurations in suppressed_in."
    out["note"] = note
    return ok(out)


def _do_add_suppress(design, table, feature, suppressed_in):
    if not feature:
        return error("Provide 'feature' - the timeline feature name to suppress per configuration.")
    resolved, ferr = _FEATURE.resolve(feature)
    if ferr:
        return error(ferr)
    feat, feature = resolved            # publish the TIMELINE name that resolved, not the raw input
    # The kind hands back a group only after reading isCollapsed True on it.
    if safe(lambda: feat.isCollapsed) is True:
        return _suppress_group(table, feat, suppressed_in)
    suppressed_in = suppressed_in or []
    unknown = [r for r in suppressed_in if r not in set(_row_names(table))]
    if unknown:
        return error(f"suppressed_in names unknown configurations: {', '.join(unknown)}.")
    # addSuppressColumn takes the TIMELINE object. A modelling feature (Extrude, Fillet) is also
    # accepted as its entity, but a Sketch, an Occurrence and a Joint are refused that way with
    # "3 : invalid feature argument" (measured), so hand over the timeline object whenever the
    # entity carries one.
    target = safe(lambda: feat.timelineObject) or feat
    col = table.columns.addSuppressColumn(target)  # MUTATION
    if not col:
        return error(f"addSuppressColumn for '{feature}' returned null.")
    for rname in suppressed_in:
        cell = safe(lambda rname=rname: col.getCellByRowName(rname))
        if cell is None:
            return error(f"No suppress cell for configuration '{rname}'.")
        cell.isSuppressed = True # MUTATION
        # read_flag semantics by hand: True is the only pass; False AND unreadable both fail.
        if safe(lambda cell=cell: cell.isSuppressed) is not True:
            return error(f"Suppress cell '{rname}' does not read suppressed after the set - the "
                         "suppression did not verifiably take.")
    return ok({"feature": feature, "suppressed_in": suppressed_in,
               "note": "Suppress column added; the feature is suppressed in the listed configurations "
                       "(present in the others)."})


def _do_add_visibility(design, table, body, hidden_in):
    if not body:
        return error("Provide 'body' - the body name whose visibility varies per configuration.")
    ent, body_err = _BODY.resolve(body)
    if body_err:
        return error(body_err)
    hidden_in = hidden_in or []
    unknown = [r for r in hidden_in if r not in set(_row_names(table))]
    if unknown:
        return error(f"hidden_in names unknown configurations: {', '.join(unknown)}.")
    col = table.columns.addVisibilityColumn(ent)  # MUTATION
    if not col:
        return error(f"addVisibilityColumn for '{body}' returned null.")
    for rname in hidden_in:
        cell = safe(lambda rname=rname: col.getCellByRowName(rname))
        if cell is None:
            return error(f"No visibility cell for configuration '{rname}'.")
        cell.isVisible = False # MUTATION
        # False is the only pass; still-visible AND unreadable both fail (unreadable != hidden).
        if safe(lambda cell=cell: cell.isVisible) is not False:
            return error(f"Visibility cell '{rname}' does not read hidden after the set - the hide "
                         "did not verifiably take.")
    return ok({"body": body, "hidden_in": hidden_in,
               "note": "Visibility column added; the body is hidden in the listed configurations."})


def _do_set_appearance(design, table, body, appearances):
    if not body:
        return error("Provide 'body' - the body to color per configuration.")
    ent, body_err = _BODY.resolve(body)
    if body_err:
        return error(body_err)
    appearances = appearances or {}
    unknown = _validate_rows(table, appearances)
    if unknown:
        return error(f"Appearance map references unknown configurations: {', '.join(unknown)}.")
    # Resolve every named appearance up front (fail before mutating).
    resolved = {}
    for rname, aname in appearances.items():
        a = _resolve_appearance(design, aname)
        if not a:
            return error(f"No appearance named '{aname}' in the design. Copy it in first "
                         "(design.appearances.addByCopy) - appearance_set copies the Fusion "
                         "Appearance Library's 'Paint - Enamel Glossy (White)' and keeps it in the "
                         "document as 'MCP Neutral Base'.")
        resolved[rname] = a

    appt = safe(lambda: table.appearanceTable)
    if not appt:
        return error("This design has no appearance table.")
    # ORDERING GOTCHA: add the body column FIRST (auto-creates the first theme row), then add extra
    # theme rows so there is one theme per configuration we want to color.
    col = appt.columns.add(ent)                   # MUTATION (creates first theme row)
    if not col:
        return error(f"appearanceTable.columns.add for '{body}' returned null.")
    needed = len(resolved)
    # The adds go through the shared minting rule, which owns the fact that rows.add of a taken name
    # adds NOTHING and hands that row back. Bounded by the number wanted - a census that will not
    # read counts as no rows - and the count REACHED is asserted below rather than trusted.
    for _ in range(needed):
        rows = _theme_rows(appt)
        if len(rows) >= needed:
            break
        _mint_theme_row(appt, rows, prefix="Theme")      # MUTATION
    reached = _common.counted(lambda: appt.rows.count)
    if reached is None or reached < needed:
        return error(f"The appearance table holds {reached} theme rows after adding, not the "
                     f"{needed} this call needs - one per configuration named in 'appearances'. "
                     "Name fewer configurations, or add the theme rows in the UI first.")

    # Assign each named appearance to a distinct theme row, and link config row -> theme row.
    theme_col = safe(lambda: appt.parentTableColumn)
    if theme_col is None:
        return error("The appearance table has no theme column (parentTableColumn) to link configurations.")
    theme_rows = _theme_rows(appt)
    set_count = 0
    for theme_idx, (rname, appearance) in enumerate(resolved.items()):
        theme_row, theme_name = (theme_rows[theme_idx] if theme_idx < len(theme_rows)
                                 else (None, None))
        cell = safe(lambda theme_name=theme_name: col.getCellByRowName(theme_name))
        if cell is None or theme_row is None:
            return error(f"No appearance cell/row on theme row {theme_name!r} (for configuration "
                         f"'{rname}').")
        cell.appearance = appearance # MUTATION (assign appearance to this theme row)
        # An unreadable read-back is a FAILURE, not a pass (the material path's stated rule).
        got = safe(lambda cell=cell: cell.appearance.name)
        if got is None or got != safe(lambda: appearance.name):
            return error(f"Appearance cell on theme row '{theme_name}' reads "
                         f"{'nothing' if got is None else repr(got)} after the set - the "
                         "assignment did not verifiably take.")
        # Link the configuration row to this theme row. CRITICAL: the theme column's getCell(index)
        # does NOT share top.rows ordering - addressing by positional index links the WRONG config
        # (live-caught: Small got Large's theme). Address the theme cell by the CONFIG ROW NAME.
        tcell = safe(lambda rname=rname: theme_col.getCellByRowName(rname))
        if tcell is None:
            return error(f"No theme cell for configuration '{rname}'.")
        tcell.referencedTableRow = theme_row # MUTATION
        got_row = safe(lambda tcell=tcell: tcell.referencedTableRow.name)
        if got_row is None or got_row != safe(lambda theme_row=theme_row: theme_row.name):
            return error(f"Configuration '{rname}' links theme "
                         f"{'nothing readable' if got_row is None else repr(got_row)} after the "
                         "set - the theme link did not verifiably take.")
        set_count += 1
    return ok({"body": body, "themes": set_count,
               "note": "Appearance theme column added and configurations linked to theme rows. Switch "
                       "configurations to see the color change (design_configure(action='activate', name=...))."})


def _theme_rows(mtbl):
    """[(row, name)] for every theme row of a theme table. The NAME is the argument a column's
    getCellByRowName takes - ConfigurationMaterialColumn and ConfigurationAppearanceColumn both
    expose it; a column's getCell index runs over the column's own rowCount, which is not the
    table's row order."""
    out = []
    for i in range(safe(lambda: mtbl.rows.count, 0) or 0):
        r = safe(lambda i=i: mtbl.rows.item(i))
        if r is not None:
            out.append((r, safe(lambda r=r: r.name)))
    return out


def _theme_links(table, theme_col):
    """{configuration name: the name of the theme row it references} for every configuration row."""
    links = {}
    for cname in _row_names(table):
        cell = safe(lambda cname=cname: theme_col.getCellByRowName(cname))
        links[cname] = safe(lambda cell=cell: cell.referencedTableRow.name) if cell is not None else None
    return links


def _carry_theme_materials(mtbl, from_name, to_name):
    """Copy every column's material from one theme row to another. A minted row copies the row ABOVE
    it, which is not the row the configuration being moved was referencing - without this carry, the
    OTHER bodies in that configuration silently take some other configuration's materials. Returns
    the title of a column whose copy did not read back, or None."""
    for c in iter_collection(safe(lambda: mtbl.columns)):
        src = safe(lambda c=c: c.getCellByRowName(from_name))
        dst = safe(lambda c=c: c.getCellByRowName(to_name))
        mat = safe(lambda src=src: src.material) if src is not None else None
        if dst is None or mat is None:
            continue
        dst.material = mat # MUTATION (carry the outgoing row's material onto the new one)
        want = safe(lambda mat=mat: mat.name)
        if safe(lambda dst=dst: dst.material.name) != want:
            return safe(lambda c=c: c.title) or "a column"
    return None


def _mint_theme_row(mtbl, rows, prefix="Material"):
    """Add a theme row named '<prefix> N' under a name no existing row carries: (row, name).
    The name search is load-bearing: rows.add(<a name an existing row carries>) adds NOTHING and
    returns THAT row, so a colliding name would hand this configuration a row another configuration
    already references."""
    taken = {n for (_r, n) in rows}
    n = len(rows) + 1
    while ("%s %d" % (prefix, n)) in taken:
        n += 1
    name = "%s %d" % (prefix, n)
    return mtbl.rows.add(name), name                  # MUTATION


def _do_add_material(design, table, body, materials):
    if not body:
        return error("Provide 'body' - the body whose physical material varies per configuration.")
    ent, body_err = _BODY.resolve(body)
    if body_err:
        return error(body_err)
    materials = materials or {}
    if not materials:
        return error("Provide 'materials' - {configuration_name: material_name} for at least one "
                     "configuration.")
    unknown = _validate_rows(table, materials)
    if unknown:
        return error(f"Material map references unknown configurations: {', '.join(unknown)}. "
                     f"Existing: {', '.join(str(n) for n in _row_names(table))}.")
    # Resolve every named material up front (fail before mutating).
    resolved = {}
    for rname, mname in materials.items():
        m, merr = _resolve_material(design, mname)
        if merr:
            return error(merr)
        resolved[rname] = m

    mtbl = safe(lambda: table.materialTable)
    if not mtbl:
        return error("This design has no material table.")
    # Add the body column FIRST: on an empty table it also mints the first theme row, and the first
    # non-root column auto-creates a root-component column ahead of it, so the column COUNT jumps by
    # two - the returned column object is the signal, never the count.
    col = mtbl.columns.add(ent)                   # MUTATION (creates first theme row)
    if not col:
        return error(f"materialTable.columns.add for '{body}' returned null.")
    theme_col = safe(lambda: mtbl.parentTableColumn)
    if theme_col is None:
        return error("The material table has no theme column (parentTableColumn) to link configurations.")

    # Theme rows and the configuration -> theme link are table-GLOBAL, and a fresh column leaves
    # every configuration on the one auto-created row - so a row shared with another configuration
    # is replaced by a minted one rather than written through.
    links = _theme_links(table, theme_col)
    applied = {}
    for rname, material in resolved.items():
        rows = _theme_rows(mtbl)
        cur = links.get(rname)
        held = next(((r, n) for (r, n) in rows if n == cur), None) if cur else None
        shared = [c for c, t in links.items() if t == cur and c != rname]
        if held is None or shared:
            new_row, new_name = _mint_theme_row(mtbl, rows)
            if not new_row:
                return error(f"Adding a theme row for configuration '{rname}' returned null.")
            rows = _theme_rows(mtbl)
            entry = next(((r, n) for (r, n) in rows if r is new_row or n == new_name), None)
            if entry is None:
                return error(f"Theme row '{new_name}' is not in the material table after adding it.")
            if held is not None:
                bad = _carry_theme_materials(mtbl, held[1], entry[1])
                if bad:
                    return error(f"Configuration '{rname}' moved to a new theme row, but column "
                                 f"'{bad}' did not carry its material over - the material table is "
                                 "partially built; inspect it before retrying.")
        else:
            entry = held
        theme_row, want_row = entry

        cell = safe(lambda want_row=want_row: col.getCellByRowName(want_row))
        if cell is None:
            return error(f"No material cell on theme row '{want_row}' for configuration '{rname}'.")
        cell.material = material # MUTATION (assign material to this configuration's theme row)
        want = safe(lambda material=material: material.name)
        got = safe(lambda cell=cell: cell.material.name)
        # An unreadable read-back is a failure, not a pass: an unset cell leaves that configuration
        # carrying whatever material it had, which looks correct in a payload that echoes the request.
        if got is None:
            return error(f"Material cell for configuration '{rname}' reads back no material after "
                         f"setting '{want}' - the assignment could not be confirmed.")
        if got != want:
            return error(f"Material cell for configuration '{rname}' still reads '{got}' after "
                         f"the set - the material '{want}' did not take.")
        # Link the configuration row to its theme row, addressed by the CONFIG ROW NAME: the theme
        # column's getCell(index) does NOT share top.rows ordering, so a positional index can hand
        # one configuration another configuration's theme.
        tcell = safe(lambda rname=rname: theme_col.getCellByRowName(rname))
        if tcell is None:
            return error(f"No theme cell for configuration '{rname}'.")
        tcell.referencedTableRow = theme_row # MUTATION
        got_row = safe(lambda tcell=tcell: tcell.referencedTableRow.name)
        if got_row is None or got_row != want_row:
            return error(f"Configuration '{rname}' links theme '{got_row}' after the set (expected "
                         f"'{want_row}') - the theme link did not take.")
        links[rname] = want_row
        applied[rname] = got

    unset = [c for c in _row_names(table) if c not in applied]
    note = ("Material column added for this body, each listed configuration linked to its own "
            "theme row; the names above are read back off the table cells. Read "
            "design_get(include=['configurations']) for the configured table; this reply does not "
            "verify the body's assigned material.")
    out = {"body": body, "themes": len(applied), "materials": applied,
           "column_id": safe(lambda: col.id), "column_title": safe(lambda: col.title)}
    if unset:
        out["configurations_unset"] = unset
        note += (" Configurations you did not name (" + ", ".join(unset) + ") keep the theme row they "
                 "already reference, so this body's material in them is whatever that row holds - "
                 "name them in 'materials' to set them.")
    out["note"] = note
    return ok(out)


def _do_add_insert(design, table, insert_part, insert_config, insert_map):
    """Insert a configured PART into this (assembly) configured design and map each assembly
    configuration to one of the part's configurations - a NESTED configuration. The part must be in
    the same project as the assembly (referenced insert requires it)."""
    if not insert_part:
        return error("Provide 'insert_part' - the configured part to insert (lineage urn or its name "
                     "in the active project).")
    insert_map = insert_map or {}
    df, problem = _resolve_datafile(design, insert_part)
    if not df:
        why = problem or ("it resolves as no lineage urn or Fusion URL, and the project's root "
                          "folder holds no file of that name.")
        return error(f"Could not find a configured part '{insert_part}': {why} It must be saved in "
                     "the SAME project as this assembly.")
    if not safe(lambda: df.isConfiguredDesign, False):
        return error(f"'{insert_part}' is not a configured design - use a normal insert for a "
                     "non-configured part. (Only configured parts get an insert column.)")
    part_rows = _part_config_rows(df)
    if not part_rows:
        return error(f"'{insert_part}' exposes no configuration rows.")

    # validate the map BEFORE inserting (fail clean, no half-built state)
    asm_names = set(_row_names(table))
    bad_asm = [a for a in insert_map if a not in asm_names]
    if bad_asm:
        return error(f"insert_map references assembly configurations that don't exist: "
                     f"{', '.join(bad_asm)}. Existing: {', '.join(str(n) for n in _row_names(table))}.")
    bad_part = [p for p in insert_map.values() if p not in part_rows]
    if bad_part:
        return error(f"insert_map references part configurations that don't exist: "
                     f"{', '.join(bad_part)}. The part '{insert_part}' has: "
                     f"{', '.join(part_rows.keys())}.")

    # choose which config to physically insert (default: the part's first row)
    init_name = (insert_config or "").strip() or next(iter(part_rows))
    if init_name not in part_rows:
        return error(f"insert_config '{init_name}' is not a configuration of '{insert_part}'. "
                     f"Available: {', '.join(part_rows.keys())}.")

    import adsk.core as _ac
    transform = _ac.Matrix3D.create()
    occ = design.rootComponent.occurrences.addFromConfiguration(part_rows[init_name], transform)  # MUTATION
    if not occ:
        return error(f"Inserting '{insert_part}' ({init_name}) returned no occurrence (same-project "
                     "requirement, or the part isn't accessible).")

    col = table.columns.addInsertColumn(occ)     # MUTATION
    if not col:
        return error("addInsertColumn returned null.")
    mapped = 0
    for acfg, pcfg in insert_map.items():
        cell = safe(lambda acfg=acfg: col.getCellByRowName(acfg))
        if cell is None:
            return error(f"No insert cell for assembly configuration '{acfg}'.")
        cell.row = part_rows[pcfg] # MUTATION (by-name part row - see appearance lesson)
        got_cfg = safe(lambda cell=cell: cell.row.name)
        if got_cfg is not None and got_cfg != safe(lambda pcfg=pcfg: part_rows[pcfg].name):
            return error(f"Insert cell '{acfg}' still selects part configuration '{got_cfg}' after "
                         "the set - the mapping did not take.")
        mapped += 1
    return ok({"inserted_part": insert_part, "inserted_config": init_name, "mapped": mapped,
               "occurrence": safe(lambda: occ.name),
               "note": "Configured part inserted and an insert column added: each listed assembly "
                       "configuration now selects the mapped part configuration (nested config). Switch "
                       "with design_configure(action='activate', name=...) + computeAll to see it follow. "
                       "This insert can run for minutes; the read that proves it landed without this "
                       "payload is design_get(include=['configurations']) showing the insert column."})


def _as_map(val, field):
    """(dict-or-None, error). A map input that arrived as JSON text is parsed; anything else
    non-dict is refused naming the field, never iterated as characters."""
    if val is None or isinstance(val, dict):
        return val, None
    if isinstance(val, str):
        if not val.strip():
            return None, None
        try:
            parsed = json.loads(val)
        except ValueError:
            return None, (f"'{field}' must be a JSON object ({{name: value}}), got unparseable "
                          f"text starting {val[:40]!r}.")
        if not isinstance(parsed, dict):
            return None, f"'{field}' must be a JSON object ({{name: value}}), got {type(parsed).__name__}."
        return parsed, None
    return None, f"'{field}' must be a JSON object ({{name: value}}), got {type(val).__name__}."


def _do_switch_occurrence(design, occurrence, name):
    """Switch ONE inserted configured occurrence to another row of its own table."""
    if not (occurrence or "").strip():
        return error("action='switch_occurrence' needs 'occurrence' - the inserted instance, by "
                     "handle or full path (design_get(include=['tree'])).")
    occ, oerr = _OCCURRENCE.resolve(occurrence)
    if oerr:
        return error(oerr)
    configured = _common.read_flag(lambda: occ.isConfiguration)
    if configured is not True:
        return error(f"'{occurrence}' " + ("is not an inserted configuration, so it has no row "
                                           "to switch." if configured is False else
                                           "did not read whether it is a configuration."))
    row_now = safe(lambda: occ.configurationRow.name)
    rows = safe(lambda: occ.configurationRow.parentTable.rows)
    held = [rows.item(i) for i in range(safe(lambda: rows.count, 0) or 0)] if rows else []
    names = [safe(lambda r=r: r.name) for r in held]
    if row_now is None or not names:
        return error(f"The configuration rows of '{occurrence}' did not read, so nothing was "
                     "switched.")
    wanted = (name or "").strip()
    if names.count(wanted) != 1:
        return error(f"'name' must be one row of the configuration '{occurrence}' was inserted "
                     f"from. Got '{name}'. Rows: {', '.join(str(n) for n in names)}.")
    if wanted == row_now:
        return error(f"'{occurrence}' is already on row '{wanted}'; nothing to switch.")
    path_before, pose_before = safe(lambda: occ.fullPathName), _pose(occ)
    volume_before = _solids(safe(lambda: occ.component))[1]
    try:
        answer = occ.switchConfiguration(held[names.index(wanted)])
    except Exception as e:
        return error(f"Could not switch '{occurrence}' to row '{wanted}': {e}")
    if answer is not True:
        return error(f"switchConfiguration answered {answer!r} for '{occurrence}'; re-read it "
                     "with design_get(include=['tree']).")
    if _common.read_flag(lambda: occ.isValid) is not True:
        return error(f"The switch of '{path_before}' answered true but the occurrence could not "
                     "be re-read afterwards; find it with design_get(include=['tree']).")
    path, landed = safe(lambda: occ.fullPathName), safe(lambda: occ.configurationRow.name)
    faults = []
    if landed != wanted:
        faults.append(f"it reads row '{landed}', not '{wanted}'")
    if not _same_pose(pose_before, _pose(occ)):
        faults.append("it did not keep its placement")
    if _path_fault(design, path):
        faults.append(_path_fault(design, path))
    if faults:
        return error(f"The switch of '{path_before}' WAS made, but " + "; ".join(faults)
                     + f". It now reads '{path}'.")
    volume = _solids(safe(lambda: occ.component))[1]
    return ok({"switched": True, "occurrence": path, "path_before": path_before,
               "row": landed, "row_before": row_now,
               "old_path_resolves": _resolves(design, path_before),
               "volume_cm3_before": volume_before, "volume_cm3": volume,
               "note": ("The occurrence took the row's name: use 'occurrence' from here on. Its "
                        "placement is unchanged. Not checked: joints on it and nested "
                        "occurrences - read assembly_get.")})


def handler(action: str = "", name: str = "", new_name: str = "", parameter: str = "",
            feature: str = "", body: str = "", values: dict = None, suppressed_in: list = None,
            hidden_in: list = None, appearances: dict = None, materials: dict = None,
            insert_part: str = "", insert_config: str = "", insert_map: dict = None,
            occurrence: str = "") -> dict:
    """Build/extend a configured design - 'action' selects the verb, dispatched below. WRITES."""
    action = (action or "").strip()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Use one of: {', '.join(_ACTIONS)}.")

    # Some MCP clients deliver an object-typed input as its JSON TEXT; iterating that string as a
    # map sprays per-character "unknown configuration" refusals, so parse-or-refuse each map input.
    values, verr = _as_map(values, "values")
    appearances, aerr = _as_map(appearances, "appearances")
    materials, merr = _as_map(materials, "materials")
    insert_map, ierr = _as_map(insert_map, "insert_map")
    for e in (verr, aerr, merr, ierr):
        if e:
            return error(e)

    design, design_refusal = _common.require_design()
    if design_refusal is not None:
        return design_refusal

    if action == "create":
        return _do_create(design)
    # A host that carries an inserted configuration need not be a configured design itself.
    if action == "switch_occurrence":
        return _do_switch_occurrence(design, occurrence, name)
    if (occurrence or "").strip():
        return error(f"'occurrence' belongs to action='switch_occurrence', not '{action}'.")

    # all other actions need an existing configuration table
    table = _top_table(design)
    if not table:
        return error("The active design is not yet a configured design. Run action='create' first.")

    if action == "activate":
        return _do_activate(design, table, name)
    if action == "add_configuration":
        return _do_add_configuration(table, name)
    if action == "rename_configuration":
        return _do_rename_configuration(table, name, new_name)
    if action == "copy_configuration":
        return _do_copy_configuration(table, name, new_name)
    if action == "delete_configuration":
        return _do_delete_configuration(table, name)
    if action == "add_parameter":
        return _do_add_parameter(design, table, parameter, values)
    if action == "set_parameter":
        return _do_set_parameter(table, parameter, values)
    if action == "add_suppress":
        return _do_add_suppress(design, table, feature, suppressed_in)
    if action == "add_visibility":
        return _do_add_visibility(design, table, body, hidden_in)
    if action == "set_appearance":
        return _do_set_appearance(design, table, body, appearances)
    if action == "add_material":
        return _do_add_material(design, table, body, materials)
    if action == "add_insert":
        return _do_add_insert(design, table, insert_part, insert_config, insert_map)
    return error(f"Unhandled action '{action}'.")   # unreachable (guarded above)


TOOL_DESCRIPTION = (
    "Build or switch a Configured Design; 'action' picks the verb. Read the table back with "
    "design_get(include=['configurations'])."
)

tool = (
    Tool.create_simple(name="design_configure", description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "enum": list(_ACTIONS)})
    .add_input_property("name", {"type": "string",
            "description": "The configuration (rename_configuration: the existing one)."})
    .add_input_property("new_name", {"type": "string"})
    .add_input_property("parameter", {"type": "string"})
    .add_input_property("feature", {"type": "string",
            "description": "Feature/group name or index; qualify features."})
    .add_input_property(*_BODY.as_property())
    .add_input_property("values", {"type": "object",
                        "description": "{config: expression} (add_parameter/set_parameter)."})
    .add_input_property("suppressed_in", {"type": "array", "items": {"type": "string"},
            "description": "Which configurations (add_suppress)."})
    .add_input_property("hidden_in", {"type": "array", "items": {"type": "string"},
            "description": "Which configurations (add_visibility)."})
    .add_input_property("appearances", {"type": "object",
            "description": "{config: appearance} (set_appearance)."})
    .add_input_property("materials", {"type": "object",
            "description": "{config: material} (add_material)."})
    .add_input_property("insert_part", {"type": "string",
            "description": "Lineage urn or project name (add_insert)."})
    .add_input_property("insert_config", {"type": "string",
            "description": "add_insert; default the part's first."})
    .add_input_property("insert_map", {"type": "object",
            "description": "{assembly_config: part_config} (add_insert)."})
    .add_input_property("occurrence", {"type": "string",
            "description": "For switch_occurrence."})
    .strict_schema()
)
# enforce_timeout=False: add_insert's addFromConfiguration is a blocking, uninterruptible
# main-thread call that COMMITS - one ran past the server's cap with the occurrence, the column and
# both cell mappings landed. The flag covers every action, so each one's loops are bounded.
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    enforce_timeout=False,
    verification=Verification(
        kind="inline", rung="value",
        evidence_test="tests/unit/test_design_configure.py::TestAddParameterRefusals"
                      "::test_an_expression_that_does_not_take_is_an_error"))


def register_tool():
    register(item)
