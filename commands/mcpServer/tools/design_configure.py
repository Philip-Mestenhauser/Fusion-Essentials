# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: BUILD a Configured Design — the write counterpart to design_get_configurations.

  design_configure -> convert a design to a configured design and define its configurations: add
                      configuration rows and the columns that vary across them (a model PARAMETER, a
                      feature SUPPRESS, a body/feature VISIBILITY, or a per-config APPEARANCE theme).
                      One action-dispatched verb for the whole configuration-table subsystem (mirrors
                      the composable view_inspect pattern) so the calling agent pays for one tool, not
                      six.

Why this exists: the API to author configurations (Design.createConfiguredDesign +
ConfigurationTopTable.columns.add*) is intricate and easy to get wrong; this bakes the live-verified
sequence into one tool. design_get_configurations READS/switches; this one BUILDS.

Grounded in adsk.fusion (every call below was live-verified on a parametric bracket — see
docs/fusion-api-notes.md "Configurations"):
  - Design.createConfiguredDesign() -> ConfigurationTopTable (one row, no columns). Idempotent here:
    if the design is already configured we reuse its configurationTopTable.
  - ConfigurationTopTable.rows.add(name) -> ConfigurationRow (.name/.id/.index/.activate()).
  - columns.addParameterColumn(Parameter) -> ConfigurationParameterColumn; cell.expression = "50 mm".
  - columns.addSuppressColumn(feature) -> ConfigurationSuppressColumn; cell.isSuppressed = True.
  - columns.addVisibilityColumn(entity) -> ConfigurationVisibilityColumn; cell.isVisible = False.
  - appearanceTable.columns.add(body) -> ConfigurationAppearanceColumn. ORDERING GOTCHA: adding the
    body column auto-creates the first THEME row; add extra theme rows AFTER (adding rows first throws
    InternalValidationError). Each config row links to a theme row via
    appearanceTable.parentTableColumn (ConfigurationThemeColumn) .getCell(i).referencedTableRow.
  - Every column addresses cells by getCellByRowName(name) — the robust path (no index juggling).
  - A parameter column only changes geometry if the parameter actually DRIVES a dimension; switch with
    ConfigurationRow.activate() then Design.computeAll() to rebuild.
Handler runs on the main thread; WRITES (mutates the design's configuration table).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
# Reuse the read tool's row resolver (single source of truth for name/id lookup).
from .design_get_configurations import _find_row

app = adsk.core.Application.get()

_ACTIONS = ("create", "add_configuration", "rename_configuration", "add_parameter",
            "add_suppress", "add_visibility", "set_appearance", "add_insert")


# ── resolvers (patched in tests; real lookups here) ─────────────────────────

def _resolve_feature(design, name):
    """A timeline feature (or any timeline-listed object) by name — for suppress columns."""
    tl = safe(lambda: design.timeline)
    if not tl:
        return None
    for i in range(safe(lambda: tl.count, 0) or 0):
        obj = safe(lambda i=i: tl.item(i).entity)
        if obj is not None and (safe(lambda o=obj: o.name) == name):
            return obj
    return None


def _resolve_body(design, name):
    """A BRepBody by name across all components — for visibility / appearance columns."""
    for comp in safe(lambda: design.allComponents, []) or []:
        bodies = safe(lambda c=comp: c.bRepBodies)
        for j in range(safe(lambda: bodies.count, 0) or 0):
            b = safe(lambda j=j: bodies.item(j))
            if b is not None and safe(lambda b=b: b.name) == name:
                return b
    return None


def _resolve_appearance(design, name):
    """An appearance by name already present in the design's appearances."""
    apps = safe(lambda: design.appearances)
    return safe(lambda: apps.itemByName(name)) if apps else None


def _resolve_datafile(design, name_or_id):
    """A configured-design DataFile by lineage id (urn:...) or by name within the active project.
    Patched in tests. The part MUST be in the same project as the assembly for a referenced insert."""
    # an explicit lineage urn resolves directly
    if isinstance(name_or_id, str) and name_or_id.startswith("urn:"):
        df = safe(lambda: app.data.findFileById(name_or_id))
        if df:
            return df
    # otherwise search the active project's files by name
    proj = safe(lambda: app.data.activeProject)
    folder = safe(lambda: proj.rootFolder) if proj else None
    if folder:
        files = safe(lambda: folder.dataFiles)
        for i in range(safe(lambda: files.count, 0) or 0):
            f = safe(lambda i=i: files.item(i))
            if f is not None and safe(lambda f=f: f.name) == name_or_id:
                return f
    return None


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
    """True if the active document has ever been saved (has a cloud file). The configured-design
    conversion only MATERIALIZES for the user once the document is saved — converting an unsaved
    document leaves the table in memory but the DataFile (which carries isConfiguredDesign and what the
    UI presents) doesn't exist yet. Verified live: dataFile.isConfiguredDesign flips True only post-save.
    Patched in tests."""
    return bool(safe(lambda: app.activeDocument.isSaved, False))


def _row_names(table):
    out = []
    for i in range(safe(lambda: table.rows.count, 0) or 0):
        out.append(safe(lambda i=i: table.rows.item(i).name))
    return out


# ── action handlers ──────────────────────────────────────────────────────────

def _do_create(design):
    if _top_table(design):
        return ok({"configured": True, "created": False,
                   "note": "Design is already a configured design; reusing its configuration table. "
                           "Add configurations with action='add_configuration' and columns with "
                           "add_parameter / add_suppress / add_visibility / set_appearance."})
    if not _doc_is_saved():
        return error("Save the document first (doc_save_as), THEN run create. Converting an unsaved "
                     "document builds the table only in memory — it won't materialize as a configured "
                     "design (no DataFile to carry it, and the UI won't show the Configurations panel). "
                     "Save is the commit step for the conversion.")
    table = design.createConfiguredDesign()      # MUTATION — let it raise on failure
    if not table:
        return error("createConfiguredDesign() returned no table.")
    return ok({"configured": True, "created": True,
               "configurations": _row_names(table),
               "note": "Design converted to a configured design (one configuration so far). Add columns "
                       "(add_parameter/add_suppress/add_visibility/set_appearance) and configurations "
                       "(add_configuration). THEN to see it as a configured design in the UI: SAVE, then "
                       "REOPEN the document — the Configurations dropdown only appears when the document "
                       "is opened against the saved configured file (a live, already-open doc won't "
                       "retrofit it)."})


def _do_add_configuration(table, name):
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' for the new configuration (e.g. 'Large').")
    if name in _row_names(table):
        return error(f"A configuration named '{name}' already exists.")
    row = table.rows.add(name)                   # MUTATION
    if not row:
        return error(f"Adding configuration '{name}' failed.")
    return ok({"configuration": name, "id": safe(lambda: row.id),
               "configurations": _row_names(table),
               "note": "Configuration row added. Set its values via add_parameter/add_suppress/"
                       "add_visibility (address by this name)."})


def _do_rename_configuration(table, name, new_name):
    """Rename an existing configuration row (the default row is 'Configuration 1' — usually worth
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
                     "a recent save — retry shortly).")
    return ok({"renamed": True, "from": name, "to": new_name,
               "configurations": _row_names(table),
               "note": "Configuration renamed. Address it by the new name from now on."})


def _validate_rows(table, value_map):
    """Every key in value_map must be an existing configuration row name."""
    names = set(_row_names(table))
    unknown = [k for k in value_map if k not in names]
    return unknown


def _do_add_parameter(design, table, parameter, values):
    if not parameter:
        return error("Provide 'parameter' — the name of a model parameter to vary across configurations.")
    values = values or {}
    p = safe(lambda: design.allParameters.itemByName(parameter))
    if not p:
        return error(f"No parameter named '{parameter}'. (Add/expose it first; a parameter column "
                     "only matters if the parameter drives geometry.)")
    unknown = _validate_rows(table, values)
    if unknown:
        return error(f"Values reference configurations that don't exist: {', '.join(unknown)}. "
                     f"Existing: {', '.join(str(n) for n in _row_names(table))}.")
    col = table.columns.addParameterColumn(p)    # MUTATION
    if not col:
        return error(f"addParameterColumn for '{parameter}' returned null.")
    n = 0
    for rname, expr in values.items():
        cell = safe(lambda rname=rname: col.getCellByRowName(rname))
        if cell is None:
            return error(f"No cell for configuration '{rname}' in the '{parameter}' column.")
        cell.expression = str(expr)              # MUTATION
        n += 1
    return ok({"parameter": parameter, "column_id": safe(lambda: col.id), "set": n,
               "note": "Parameter column added and per-configuration expressions set. Switch with "
                       "design_get_configurations(activate=...) — the geometry rebuilds only if this "
                       "parameter drives a dimension."})


def _do_add_suppress(design, table, feature, suppressed_in):
    if not feature:
        return error("Provide 'feature' — the timeline feature name to suppress per configuration.")
    feat = _resolve_feature(design, feature)
    if not feat:
        return error(f"No timeline feature named '{feature}'.")
    suppressed_in = suppressed_in or []
    unknown = [r for r in suppressed_in if r not in set(_row_names(table))]
    if unknown:
        return error(f"suppressed_in names unknown configurations: {', '.join(unknown)}.")
    col = table.columns.addSuppressColumn(feat)  # MUTATION
    if not col:
        return error(f"addSuppressColumn for '{feature}' returned null.")
    for rname in suppressed_in:
        cell = safe(lambda rname=rname: col.getCellByRowName(rname))
        if cell is None:
            return error(f"No suppress cell for configuration '{rname}'.")
        cell.isSuppressed = True                 # MUTATION
    return ok({"feature": feature, "suppressed_in": suppressed_in,
               "note": "Suppress column added; the feature is suppressed in the listed configurations "
                       "(present in the others)."})


def _do_add_visibility(design, table, body, hidden_in):
    if not body:
        return error("Provide 'body' — the body name whose visibility varies per configuration.")
    ent = _resolve_body(design, body)
    if not ent:
        return error(f"No body named '{body}'.")
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
        cell.isVisible = False                    # MUTATION
    return ok({"body": body, "hidden_in": hidden_in,
               "note": "Visibility column added; the body is hidden in the listed configurations."})


def _do_set_appearance(design, table, body, appearances):
    if not body:
        return error("Provide 'body' — the body to color per configuration.")
    ent = _resolve_body(design, body)
    if not ent:
        return error(f"No body named '{body}'.")
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
                         "(design.appearances.addByCopy) — e.g. an opaque base like 'Powder Coat'.")
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
    while safe(lambda: appt.rows.count, 0) < needed:
        appt.rows.add("Theme %d" % (safe(lambda: appt.rows.count, 0) + 1))   # MUTATION

    # Assign each named appearance to a distinct theme row, and link config row -> theme row.
    theme_col = safe(lambda: appt.parentTableColumn)
    if theme_col is None:
        return error("The appearance table has no theme column (parentTableColumn) to link configurations.")
    set_count = 0
    for theme_idx, (rname, appearance) in enumerate(resolved.items()):
        theme_row = safe(lambda theme_idx=theme_idx: appt.rows.item(theme_idx))
        cell = safe(lambda theme_idx=theme_idx: col.getCell(theme_idx))
        if cell is None or theme_row is None:
            return error(f"No appearance cell/row at theme index {theme_idx}.")
        cell.appearance = appearance              # MUTATION (assign appearance to this theme row)
        # Link the configuration row to this theme row. CRITICAL: the theme column's getCell(index)
        # does NOT share top.rows ordering — addressing by positional index links the WRONG config
        # (live-caught: Small got Large's theme). Address the theme cell by the CONFIG ROW NAME.
        tcell = safe(lambda rname=rname: theme_col.getCellByRowName(rname))
        if tcell is None:
            return error(f"No theme cell for configuration '{rname}'.")
        tcell.referencedTableRow = theme_row      # MUTATION
        set_count += 1
    return ok({"body": body, "themes": set_count,
               "note": "Appearance theme column added and configurations linked to theme rows. Switch "
                       "configurations to see the color change (design_get_configurations(activate=...))."})


def _do_add_insert(design, table, insert_part, insert_config, insert_map):
    """Insert a configured PART into this (assembly) configured design and map each assembly
    configuration to one of the part's configurations — a NESTED configuration. The part must be in
    the same project as the assembly (referenced insert requires it)."""
    if not insert_part:
        return error("Provide 'insert_part' — the configured part to insert (lineage urn or its name "
                     "in the active project).")
    insert_map = insert_map or {}
    df = _resolve_datafile(design, insert_part)
    if not df:
        return error(f"Could not find a configured part '{insert_part}' (by urn or name in the active "
                     "project). It must be saved in the SAME project as this assembly.")
    if not safe(lambda: df.isConfiguredDesign, False):
        return error(f"'{insert_part}' is not a configured design — use a normal insert for a "
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
        cell.row = part_rows[pcfg]               # MUTATION (by-name part row — see appearance lesson)
        mapped += 1
    return ok({"inserted_part": insert_part, "inserted_config": init_name, "mapped": mapped,
               "occurrence": safe(lambda: occ.name),
               "note": "Configured part inserted and an insert column added: each listed assembly "
                       "configuration now selects the mapped part configuration (nested config). Switch "
                       "with design_get_configurations(activate=...) + computeAll to see it follow."})


def handler(action: str = "", name: str = "", new_name: str = "", parameter: str = "",
            feature: str = "", body: str = "", values: dict = None, suppressed_in: list = None,
            hidden_in: list = None, appearances: dict = None,
            insert_part: str = "", insert_config: str = "", insert_map: dict = None) -> dict:
    """Build/extend a configured design. 'action' selects the verb:

      create               — convert the active design into a configured design (idempotent).
      add_configuration    — add a configuration row ('name').
      rename_configuration — rename configuration 'name' to 'new_name' (e.g. 'Configuration 1'->'Medium').
      add_parameter        — vary a model parameter ('parameter') across configs ('values' =
                             {config_name: "expression"}).
      add_suppress         — suppress a timeline feature ('feature') in configs ('suppressed_in' = [names]).
      add_visibility       — hide a body ('body') in configs ('hidden_in' = [names]).
      set_appearance       — per-config color a body ('body') with 'appearances' = {config_name: appearance_name}.
      add_insert           — insert a configured part ('insert_part', initial 'insert_config') and map
                             each assembly config to a part config ('insert_map' = {asm_config: part_config})
                             — a NESTED configuration. Part must be in the same project.

    WRITES. After switching configurations the geometry rebuilds only if the varied parameter drives a
    dimension; use design_get_configurations(activate=...) to switch and view.
    """
    action = (action or "").strip()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Use one of: {', '.join(_ACTIONS)}.")

    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first.")

    if action == "create":
        return _do_create(design)

    # all other actions need an existing configuration table
    table = _top_table(design)
    if not table:
        return error("The active design is not yet a configured design. Run action='create' first.")

    if action == "add_configuration":
        return _do_add_configuration(table, name)
    if action == "rename_configuration":
        return _do_rename_configuration(table, name, new_name)
    if action == "add_parameter":
        return _do_add_parameter(design, table, parameter, values)
    if action == "add_suppress":
        return _do_add_suppress(design, table, feature, suppressed_in)
    if action == "add_visibility":
        return _do_add_visibility(design, table, body, hidden_in)
    if action == "set_appearance":
        return _do_set_appearance(design, table, body, appearances)
    if action == "add_insert":
        return _do_add_insert(design, table, insert_part, insert_config, insert_map)
    return error(f"Unhandled action '{action}'.")   # unreachable (guarded above)


TOOL_DESCRIPTION = (
    "BUILD a Configured Design (the write side of design_get_configurations). 'action' selects the "
    "verb: 'create' (convert the active design to a configured design, idempotent); 'add_configuration' "
    "(add a row by 'name'); 'rename_configuration' ('name'->'new_name'); 'add_parameter' (vary model "
    "'parameter' across configs via 'values'={config:\"expr\"}); 'add_suppress' (suppress timeline "
    "'feature' in 'suppressed_in'=[configs]); 'add_visibility' (hide 'body' in 'hidden_in'=[configs]); "
    "'set_appearance' (per-config color 'body' via 'appearances'={config:appearance_name}); 'add_insert' "
    "(insert configured 'insert_part' and map 'insert_map'={asm_config:part_config} — a NESTED "
    "configuration; part must be in the same project). WRITES. "
    "'create' REQUIRES a saved document and the conversion only shows in the UI after you SAVE and "
    "REOPEN the doc (an already-open doc won't show the Configurations dropdown). A parameter column "
    "only changes geometry if that parameter drives a dimension; switch/preview with "
    "design_get_configurations(activate=...). Appearances must already exist in the design."
)

tool = (
    Tool.create_simple(name="design_configure", description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "enum": list(_ACTIONS),
            "description": "Which configuration operation to perform."})
    .add_input_property("name", {"type": "string", "description": "Configuration name (add_configuration; the existing one for rename_configuration)."})
    .add_input_property("new_name", {"type": "string", "description": "New name for rename_configuration."})
    .add_input_property("parameter", {"type": "string", "description": "Model parameter name (add_parameter)."})
    .add_input_property("feature", {"type": "string", "description": "Timeline feature name (add_suppress)."})
    .add_input_property("body", {"type": "string", "description": "Body name (add_visibility / set_appearance)."})
    .add_input_property("values", {"type": "object", "description": "{config_name: expression} (add_parameter)."})
    .add_input_property("suppressed_in", {"type": "array", "items": {"type": "string"},
            "description": "Configurations to suppress the feature in (add_suppress)."})
    .add_input_property("hidden_in", {"type": "array", "items": {"type": "string"},
            "description": "Configurations to hide the body in (add_visibility)."})
    .add_input_property("appearances", {"type": "object",
            "description": "{config_name: appearance_name} (set_appearance)."})
    .add_input_property("insert_part", {"type": "string",
            "description": "Configured part to insert: lineage urn or its name in the active project (add_insert)."})
    .add_input_property("insert_config", {"type": "string",
            "description": "Which part configuration to physically insert; defaults to the part's first (add_insert)."})
    .add_input_property("insert_map", {"type": "object",
            "description": "{assembly_config: part_config} — nested config mapping (add_insert)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
