# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The rich CAM read (CLAUDE.md "Reads are RICH"): the active document's CAM state, zoomed via
include=[...]. The handler is a thin router over _slice_*() helpers, one per slice. Operation
validity is only trustworthy once the Manufacture workspace has been entered."""

import json

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import (CM_TO_UNIT, iter_collection, measured, named_with_remainder, ok, error, safe,
                      terse)
from ._cam_common import get_cam, find_setup, resolve_cam_node, resolve_operation
from . import _inputs

app = adsk.core.Application.get()

_SLICES = ("operations", "parameters", "tool", "references", "nc_programs", "time", "tools", "library",
           "library_types", "machine", "machines", "templates", "inspection")

# Keep operation rows readable (via _common.terse): a healthy op collapses to {name, tool, strategy, state}; an
# abnormal op keeps the flag(s) that aren't default (is_suppressed=true, has_error=true, ...) and pops.
# A null preset and a spindle speed inside the machine's limit are the quiet answers, so they drop
# too - an op that is OVER the limit (true) or could not be checked (null) survives and pops.
_OP_NOISE = {"is_generating": False, "is_suppressed": False, "is_optional": False,
             "has_warning": False, "has_error": False, "is_out_of_date": False,
             "has_toolpath": True, "toolpath_valid": True,
             "preset": None, "spindle_over_machine_max": False}


# ── slice helpers - each calls a read-implementation handler in _cam_read and unwraps its payload ──
#
# The handlers (get_cam_setups_handler, get_cam_operations_handler, ...) live in _cam_read - the CAM
# read cores. cam_get is the thin rich-read router/surface over them; a slice decodes the handler's
# ok() payload to a dict, then shapes/bounds it for the include= projection.

def _unwrap(result):
    """(payload, None) on ok; (None, error_result) on error (so a slice's own guard can surface)."""
    if result.get("isError"):
        return None, result
    try:
        return json.loads(result["content"][0]["text"]), None
    except Exception:
        return None, result


def _slice_setups(cam, setup):
    """The setups orientation default: machine + model/fixture/stock + per-setup operation_count (the
    REAL total, incl. ops nested in folders) + folder_count (the depth breadcrumb)."""
    from . import _cam_read as _cr
    return _unwrap(_cr.get_cam_setups_handler())


def _dedupe_orientation(out, inc):
    """Content-aware de-dup: the orientation block STAYS on a deep call (machine, op_states, names are
    the context a cold deep-call needs), but a fact the included slice RESTATES at finer grain is
    dropped from the orientation copy. When 'operations' is included, each op carries its own
    invalidation_reasons, so the SETUP-level rollup of them is the duplicate."""
    if "operations" in inc:
        for s in out.get("setups", []):
            s.pop("invalidation_reasons", None)


# Action tools for the actionable states the setups slice surfaces. Present-only: a pointer appears
# only when that state is actually there, so an agent reading the orientation knows the NEXT tool, not
# just the problem. (Op detail is already advertised via include=['operations'] in the note.)
def _cam_pointers(setups):
    """Map the present, actionable CAM states to the tool that resolves them. setups is the list from
    the orientation slice (each with op_states + machine_out_of_date)."""
    ptrs = {}
    stale = 0
    machine_stale = False
    for s in setups or []:
        st = s.get("op_states") or {}
        stale += (st.get("out_of_date", 0) or 0) + (st.get("no_toolpath", 0) or 0)
        if s.get("machine_out_of_date"):
            machine_stale = True
    if stale:
        ptrs["toolpaths"] = (f"cam_generate to regenerate the {stale} out-of-date / ungenerated "
                             "operation(s); cam_get(include=['operations']) for the per-op detail.")
    if machine_stale:
        ptrs["machine"] = "cam_edit_setup to refresh the out-of-date machine definition."
    return ptrs


_OPERATIONS_CAP = 250   # a large CAM doc can hold hundreds of ops; cap the per-turn dump + flag it.


def _slice_operations(cam, setup):
    """Per-operation state grouped by setup (+ tools_used rollup), with healthy-row noise dropped (a
    normal op is {name,tool,strategy,state}; a suppressed/errored op keeps its flags and stands out).
    Bounded: across all setups the operation rows are capped (the counts in the default setups slice
    are unbounded, so the agent always sees the true total; 'setup' scopes to one setup)."""
    from . import _cam_read as _cr
    payload, err = _unwrap(_cr.get_cam_operations_handler(setup=setup))
    if payload:
        emitted = 0
        for su in payload.get("setups", []):
            rows = []
            for op in su.get("operations", []):
                if emitted >= _OPERATIONS_CAP:
                    payload["truncated"] = True
                    break
                rows.append(terse(op, _OP_NOISE))
                emitted += 1
            su["operations"] = rows
        if payload.get("truncated"):
            payload["note"] = (f"Operation rows capped at {_OPERATIONS_CAP}. Pass 'setup' to scope to "
                               "one setup, or read the per-setup operation_count in the default "
                               "slice. " + (payload.get("note") or ""))
    return payload, err


# WHAT the references slice counts, in the words of the read it actually takes. The underlying walk
# tests each of a setup's model/fixture/stock entries with Occurrence.isReferencedComponent and does
# not descend, so the census is the SELECTED entries - not the references reachable from them.
# Measured on a two-setup job: each setup selects three occurrences that read isReferencedComponent
# False, the read counted 0, and three referenced components sat one level below them (one under the
# selected model, two under the selected fixture) - which doc_get(include=['xref_tree']) found.
# The number is stated every time, because a 0 that does not say what it counted reads as a verdict
# on the document.
_REFERENCE_CENSUS = (
    "Counted {found} referenced component(s) among the model/fixture/stock entries that the "
    "{setups} setup(s) in scope SELECT DIRECTLY - the top-level setups[] slice names those entries "
    "in selected_models / fixtures / stock_solids - and only an entry that is ITSELF a referenced "
    "component is counted. A reference nested INSIDE a selected entry is not examined, so 0 here is "
    "not 'this document has no external references': measured on a job whose setups each select "
    "three local container occurrences, this read counted 0 while three referenced components sat "
    "one level below them. doc_get(include=['xref_tree']) walks every depth.")


def _slice_references(cam, setup):
    """Each setup's external X-ref models/fixtures/stock -> source document, plus the census sentence
    saying which entries that count covers (see _REFERENCE_CENSUS)."""
    from . import _cam_read as _cr
    payload, err = _unwrap(_cr.get_setup_references_handler(setup=setup))
    if payload:
        rows = payload.get("setups") or []
        payload["counted"] = ("the model/fixture/stock entries each setup selects directly, and of "
                              "those only the ones that are themselves referenced components")
        note = _REFERENCE_CENSUS.format(
            found=sum(r.get("reference_count") or 0 for r in rows), setups=len(rows))
        if any(r.get("references_truncated") for r in rows):
            note += (" references_truncated is set on at least one setup, so even that selected-entry "
                     "census is incomplete - a selection list would not read, or its cap was hit.")
        payload["note"] = note
    return payload, err


def _slice_nc_programs(cam):
    """The NC/post programs - SUMMARY only (name, machine, post, op count + post_parameter_count). The
    full post_parameters are the post's static schema (often 60+ rows, identical across programs), a
    deeper level not dumped here - point at it rather than flooding (CLAUDE.md 'point, don't inline')."""
    from . import _cam_read as _cr
    payload, err = _unwrap(_cr.get_nc_programs_handler())
    if payload:
        for p in payload.get("nc_programs", []):
            params = p.pop("post_parameters", None)
            if params is not None:
                p["post_parameter_count"] = len(params)
    return payload, err


def _slice_time(cam, setup, units):
    """Machining cycle-time estimate (per setup + per operation), suppressed ops excluded."""
    from . import _cam_read as _cr
    return _unwrap(_cr.get_machining_time_handler(setup=setup, units=units))


def _slice_machine(cam, setup, units):
    """The machine's own LIMITS per setup: spindle speed range + per-axis travels, off the machine's
    kinematics. Distinct from 'machines' (the catalog of machines you can assign)."""
    from . import _cam_read as _cr
    return _unwrap(_cr.get_machine_limits_handler(setup=setup, units=units))


def _slice_tools(cam):
    """The distinct cutting tools used across operations (the tool sheet)."""
    from . import _cam_read as _cr
    return _unwrap(_cr.get_tool_list_handler())


def _slice_library(cam, scope, library, tool_type):
    """A tool LIBRARY's catalog (the tools you can ADD), by scope: document/local/cloud/hub. A shared
    scope with no 'library' lists the libraries there. Distinct from 'tools' (what ops USE); the write
    actions stay on cam_edit_tools."""
    from . import cam_edit_tools
    return _unwrap(cam_edit_tools.read_library(scope or "document", library, tool_type))


def _slice_library_types(cam):
    """The tool-type vocabulary (the geometry families a sample can be cloned from) that
    cam_edit_tools resolves add_tools[].from_type against. Distinct from 'library' (a catalog's
    tools); the add/remove/edit writes stay on cam_edit_tools."""
    from . import cam_edit_tools
    return _unwrap(cam_edit_tools._do_list_types())


def _slice_machines(cam, vendor, machine_type):
    """The machine catalog cam_edit_setup's 'machine' input can resolve from (Local + Fusion360
    locations), each row name/vendor/model/location/kind/simulation_ready. 'vendor' and
    'machine_type' (milling/turning/cutting/additive) filter."""
    from . import cam_edit_setup
    return _unwrap(cam_edit_setup.read_machines(vendor or "", machine_type or ""))


def _slice_inspection(cam, measure, max_results, units):
    """The recorded surface-inspection (probing) results: a per-measure state rollup + its worst
    out-of-tolerance point by default; 'measure'=<index> (or '<index>/<path>') drills that scope's
    out-of-tolerance points, capped by 'max_results'."""
    from . import _cam_read as _cr
    return _unwrap(_cr.get_inspection_results_handler(
        measure=measure, max_results=max_results, units=units))


def _slice_templates(cam, template_location, template_url, template_depth):
    """The CAM toolpath TEMPLATE library tree (folders + templates by URL) for a location
    (cloud/local/fusion/...) or a specific folder 'template_url'. Apply/save stay on cam_apply_template
    / cam_save_template."""
    from . import cam_templates
    return _unwrap(cam_templates.list_cam_templates_handler(
        location=template_location or "cloud", url=template_url, max_depth=template_depth or 4))


# ── ONE operation's detail: its parameters / tool / a tool preset (the deepest level) ──────────────
#
# An operation holds 400+ CAMParameters; most are internal plumbing. We keep only the visible+enabled
# ones (the UI-relevant subset, ~80) and group them into the same sections the Fusion panel shows -
# Feed & Speed, Geometry, Passes, ... - using the `group_*` marker params in declaration order (there
# is no group API; the order is what the panel itself relies on). A tool carries a table of presets
# (we list their NAMES here); pass 'preset' to read one preset's feeds/speeds expressions (~17 rows).
# The caller scopes to one operation first, then reads the detail they want.

def _op_miss_error(operation, names, refusal):
    """The refusal for an unscoped operation resolve that came back empty.

    A remedy in THIS tool's own input vocabulary REPLACES the shared resolver's, but only where one
    exists: the setups holding the duplicates, offered as the 'setup' value that scopes the read
    (_resolve_op_in_scope resolves inside it). Sharing one operation name across setups is normal
    shop practice, so a scope the caller can pass beats an address it has to count out.

    Everything else - a plain miss, and duplicates no setup separates - returns ``refusal``,
    resolve_cam_node's own text, whose '<name>#<n>' addresses this same input reads back. Wording a
    second refusal here would be a re-roll that could only offer a rename."""
    want = (operation or "").strip().lower()
    paths = [n for n in names if n and n.split(" / ")[-1].strip().lower() == want]
    if paths:
        # The setup is the FIRST segment of each breadcrumb (walk_cam_tree builds it setup-first).
        # Two segments are dropped before anything is offered, because setup= must RESOLVE:
        #   - a path with no separator is a bare name, not a breadcrumb, so its first segment is
        #     the operation itself;
        #   - a setup holding SEVERAL of the duplicates refuses again when scoped to, so naming it
        #     would print a value this tool rejects - the very defect this remedy exists to end.
        # Only a setup appearing EXACTLY ONCE holds exactly one of them, so only it is offered.
        heads = [p.split(" / ")[0].strip() for p in paths if " / " in p]
        scopes = [s for s in dict.fromkeys(heads) if s and heads.count(s) == 1]
        if scopes:
            # Capped like every other list that crosses the wire: one name can be shared by dozens
            # of operations, and a silently truncated list reads as the complete set.
            return error(f"'{operation}' is ambiguous - {len(paths)} operations share that name: "
                         f"{named_with_remainder(paths)}. Retry with the setup that holds the one "
                         "you mean: " + ", ".join(f"setup='{s}'" for s in scopes) + ".")
    return error(refusal)


def _resolve_op_in_scope(cam, operation, setup):
    """(operation, error_result_or_None) - the ONE operation resolve behind the deep per-operation
    slices (parameters, tool), so both accept the same vocabulary and refuse the same way.

    'setup' SCOPES the resolve, not only the listing: an operation name is unique only WITHIN a
    setup, so with several setups holding the same name an unscoped read can do nothing but refuse -
    which is why _op_miss_error offers setup= and this resolves inside it. The scoped resolve is the
    shared resolve_cam_node over that setup's subtree alone (case-insensitive exact, a miss lists
    that setup's operations).

    UNSCOPED it is that same resolver over the whole tree, so the '<name>#<n>' address its ambiguity
    refusal hands back is a spelling this call reads. The duplicates' breadcrumbs - what
    _op_miss_error builds the narrower setup= remedy from - come back from the SAME call through
    resolve_operation, off the one operation pool it resolved against: the remedy therefore cannot
    name setups from a census the refusal was not about, and an unscoped miss walks the tree once."""
    want_setup = (setup or "").strip()
    if not want_setup:
        node, rerr, names = resolve_operation(cam, operation)
        if node is not None:
            return node.obj, None
        return None, _op_miss_error(operation, names, rerr)
    s, _names, serr = find_setup(cam, want_setup)
    if not s:
        return None, error(serr)
    node, rerr = resolve_cam_node(cam, operation, kinds=("operation",), setup=s,
                                  label=f"operation in setup '{want_setup}'")
    if rerr:
        return None, error(rerr)
    return node.obj, None


def _grouped_visible_params(param_coll):
    """The VISIBLE + ENABLED parameters, grouped into the UI's sections via the `group_*`/group-toggle
    sentinels in order. Returns {section_title: [{name, title, expression}]} - the machining values an
    agent reads, organized like the Fusion panel, not a flat 400-row dump."""
    groups = {}
    current = "General"
    for p in iter_collection(param_coll):
        if not (safe(lambda p=p: p.isVisible, False) and safe(lambda p=p: p.isEnabled, False)):
            continue
        nm = safe(lambda p=p: p.name) or ""
        title = safe(lambda p=p: p.title) or nm
        # a group sentinel ('group_feedspeed', 'stockDefinition', 'useShaftAndHolder', ...) opens a
        # section and is NOT itself a value row.
        if nm.startswith("group_") or (nm[:1].islower() and safe(lambda p=p: p.value, None) is True
                                       and not safe(lambda p=p: p.expression, "").strip("truefalse ")):
            current = title
            groups.setdefault(current, [])
            continue
        groups.setdefault(current, []).append({
            "name": nm, "title": title, "expression": safe(lambda p=p: p.expression)})
    # drop empty sections (a sentinel with no following values)
    return {g: rows for g, rows in groups.items() if rows}


# The setup's stock/model extents. These are COMPUTED parameters: they read isVisible False (so the
# visible+enabled grouping above drops them) and isEditable False, and they are the numbers the
# stock-size expressions in the visible rows refer to.
#
# A CAMParameter carries the SAME length two ways, in two DIFFERENT units, measured on a millimetre
# document: .value.value is Fusion's internal CENTIMETRES (stockXLow -17.63) while .expression is the
# authored text in the document's own display unit (-176.3). So the value is scaled out of cm like
# every other length this server publishes, and the expression ships verbatim - converting it would
# corrupt an authored string that may not even be a number ('stockXHigh - stockXLow').
_STOCK_EXTENTS = ("stockXLow", "stockXHigh", "stockYLow", "stockYHigh", "stockZLow", "stockZHigh",
                  "surfaceXLow", "surfaceXHigh", "surfaceYLow", "surfaceYHigh",
                  "surfaceZLow", "surfaceZHigh")

_SETUP_PARAM_NOTE = (
    "The setup's own parameters, filtered to the visible+enabled rows and grouped like the Fusion "
    "panel (job_stockMode is the stock mode). 'stock_extents' adds the computed low/high extents "
    "the stock-size rows refer to - they read isVisible false, so the grouping above drops them. "
    "Each extent carries the same length twice, in DIFFERENT units: 'value' is scaled into the "
    "'units' this block names, while 'expression' is the parameter's authored text in the "
    "document's own display unit (measured: value -17.63 beside expression -176.3 on the same "
    "parameter). Subtract low from high in ONE of them, never across the two.")


def _stock_extents(param_coll, factor, unit) -> dict:
    """{units, <name>: {value, expression}} for the computed stock/model extents that ARE present. A
    name the setup does not carry is simply absent - nothing is reported for a parameter that did
    not read. 'value' is scaled out of Fusion's internal cm; 'expression' is left as authored."""
    out = {}
    for name in _STOCK_EXTENTS:
        p = safe(lambda name=name: param_coll.itemByName(name))
        if p is None:
            continue
        row = {"value": measured(lambda p=p: p.value.value, factor),
               "expression": safe(lambda p=p: p.expression)}
        if row["value"] is not None or row["expression"]:
            out[name] = row
    if out:
        out["units"] = unit          # names the unit 'value' is in; 'expression' is not in it
    return out


def _slice_setup_parameters(cam, setup, units):
    """ONE SETUP's own parameters (the read-back side of cam_edit_setup's writes): the visible rows
    grouped by section, plus the computed stock extents."""
    factor = CM_TO_UNIT.get((units or "mm").strip().lower())
    if factor is None:
        return None, error(f"Unknown units '{units}'. Valid: mm, cm, in.")
    s, _names, err = find_setup(cam, setup)
    if not s:
        return None, error(err)
    params = safe(lambda: s.parameters)
    if params is None:
        return None, error(f"Setup '{setup}' exposes no readable parameters - nothing about its "
                           "stock or job settings can be read.")
    groups = _grouped_visible_params(params)
    out = {"setup": safe(lambda: s.name), "sections": groups,
           "parameter_count": sum(len(v) for v in groups.values()),
           "note": _SETUP_PARAM_NOTE}
    extents = _stock_extents(params, factor, (units or "mm").strip().lower())
    if extents:
        out["stock_extents"] = extents
    return out, None


def _slice_parameters(cam, operation, setup, units="mm"):
    """ONE operation's machining parameters, or ONE setup's own (pass 'setup' with no 'operation'),
    visible-only and grouped by section. With BOTH named, 'setup' scopes which operation of that
    name is read."""
    if not (operation or "").strip():
        if (setup or "").strip():
            return _slice_setup_parameters(cam, setup, units)
        return None, error("include=['parameters'] needs 'operation' - the operation whose settings to "
                           "read (scope first with cam_get(setup=..., include=['operations'])); or "
                           "'setup' alone for that SETUP's own parameters (stock mode + extents).")
    op, oerr = _resolve_op_in_scope(cam, operation, setup)
    if oerr:
        return None, oerr
    groups = _grouped_visible_params(safe(lambda: op.parameters))
    return {"operation": safe(lambda: op.name), "strategy": safe(lambda: op.strategy),
            "sections": groups,
            "parameter_count": sum(len(v) for v in groups.values())}, None


def _slice_tool(cam, operation, preset, setup=""):
    """ONE operation's tool: spec + its preset NAMES (a tool can hold 20+); 'preset' drills one preset's
    expressions (the feeds/speeds recipe). Requires 'operation'; 'setup' scopes which operation of
    that name is read, through the same resolve the parameters slice runs."""
    if not (operation or "").strip():
        return None, error("include=['tool'] needs 'operation' - the operation whose tool to read.")
    op, oerr = _resolve_op_in_scope(cam, operation, setup)
    if oerr:
        return None, oerr
    t = safe(lambda: op.tool)
    if not t:
        return {"operation": safe(lambda: op.name), "tool": None}, None
    presets = list(iter_collection(safe(lambda: t.presets)))
    pnames = [safe(lambda p=p: p.name) for p in presets]
    from . import _cam_common as _cc
    # WHICH of those presets this operation runs. Two operations can share one tool and use
    # different presets (measured: one 3mm bullnose driving a 'Wall_Finishing' op and a
    # 'Floor_Finishing' one), so the tool description alone cannot say what feeds an op is cutting
    # at. Null when the operation carries no preset.
    active = safe(lambda: op.toolPreset)
    out = {"operation": safe(lambda: op.name),
           "tool": safe(lambda: t.description),
           "holder": _cc.tool_holder(t),          # assigned holder identity (None if the tool has none)
           "active_preset": ({"name": safe(lambda: active.name), "id": safe(lambda: active.id)}
                             if active is not None else None),
           "preset_names": pnames, "preset_count": len(pnames)}
    want_preset = (preset or "").strip()
    if want_preset:
        chosen = None
        for ps in presets:
            if (safe(lambda ps=ps: ps.name) or "") == want_preset:
                chosen = ps
                break
        if not chosen:
            return None, error(f"No preset named '{want_preset}' on this tool. Available: "
                               f"{', '.join(n for n in pnames if n)}.")
        exprs = {}
        for param in iter_collection(safe(lambda: chosen.parameters)):
            exprs[safe(lambda param=param: param.name)] = safe(lambda param=param: param.expression)
        out["preset"] = {"name": want_preset, "expressions": exprs}
    return out, None


# ── the router ─────────────────────────────────────────────────────────────────────────────────────

def handler(include=None, setup: str = "", operation: str = "", preset: str = "",
            scope: str = "", library: str = "", tool_type: str = "", vendor: str = "",
            machine_type: str = "",
            template_location: str = "", template_url: str = "", template_depth: int = 0,
            measure: str = "", max_results: int = 0, units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    cam, cerr = get_cam()
    if not cam:
        return error(cerr)

    inc = _normalize_include(include)
    bad = [s for s in inc if s not in _SLICES]
    if bad:
        return error(f"Unknown include {bad}. Valid: {', '.join(_SLICES)}.")

    out, serr = _slice_setups(cam, setup)
    if serr:
        return serr

    if "operations" in inc:
        out["operations"], e = _slice_operations(cam, setup)
        if e:
            return e
    if "parameters" in inc:                     # deep: ONE operation's (or setup's) settings, grouped
        out["parameters"], e = _slice_parameters(cam, operation, setup, units)
        if e:
            return e
    if "tool" in inc:                           # deep: ONE operation's tool + presets (preset= drills)
        out["tool"], e = _slice_tool(cam, operation, preset, setup)
        if e:
            return e
    if "references" in inc:
        out["references"], e = _slice_references(cam, setup)
        if e:
            return e
    if "nc_programs" in inc:
        out["nc_programs"], e = _slice_nc_programs(cam)
        if e:
            return e
    if "time" in inc:
        out["time"], e = _slice_time(cam, setup, units)
        if e:
            return e
    if "machine" in inc:                        # the assigned machine's spindle/axis limits
        out["machine"], e = _slice_machine(cam, setup, units)
        if e:
            return e
    if "tools" in inc:
        out["tools"], e = _slice_tools(cam)
        if e:
            return e
    if "library" in inc:                        # the tool-library catalog (tools you can ADD)
        out["library"], e = _slice_library(cam, scope, library, tool_type)
        if e:
            return e
    if "library_types" in inc:                  # the from_type vocabulary add_tools clones from
        out["library_types"], e = _slice_library_types(cam)
        if e:
            return e
    if "machines" in inc:                       # the machine catalog (names cam_edit_setup accepts)
        out["machines"], e = _slice_machines(cam, vendor, machine_type)
        if e:
            return e
    if "templates" in inc:                      # the CAM toolpath template library tree
        out["templates"], e = _slice_templates(cam, template_location, template_url, template_depth)
        if e:
            return e
    if "inspection" in inc:                     # recorded probing results ('measure' drills one)
        out["inspection"], e = _slice_inspection(cam, measure, max_results, units)
        if e:
            return e

    _dedupe_orientation(out, inc)

    # name the tool that resolves each present, actionable state (stale toolpaths -> cam_generate, etc.)
    # so an agent reading the orientation knows the next action, not just that something is out of date.
    ptrs = _cam_pointers(out.get("setups"))
    if ptrs:
        out["pointers"] = ptrs

    remaining = [s for s in _SLICES if s not in inc]
    if remaining:
        out["note"] = ("Setups orientation slice. Pull deeper with include=" + str(remaining) +
                       ". Scope then deepen: include=['operations'] ('setup' filters) -> "
                       "include=['parameters'] or ['tool'] with 'operation'=<name> for one op's "
                       "settings/tool -> 'preset'=<name> for a preset's feeds/speeds.")
    return ok(out)


def _normalize_include(include):
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


TOOL_DESCRIPTION = (
    "Read the active document's CAM (Manufacture) state by zoom level. Default (no 'include'): "
    "per setup, op_states (the per-state tally), invalidation_reasons (why ops are stale), "
    "machine_out_of_date and wcs (origin/orientation mode + bound geometry). 'include' deepens; "
    "scope first: 'operations' (per-op state, folder, preset, spindle-vs-machine; 'setup' filters; "
    "cam_compare_operations diffs two) -> 'parameters' or 'tool' with 'operation'=<name> for one "
    "op's settings (grouped by section) or its tool + presets -> 'preset'=<name> for its "
    "feeds/speeds expressions. 'parameters' with 'setup' alone reads that SETUP's own stock "
    "parameters; 'machine' reads its machine's spindle and axis limits. Document-level: "
    "'references' (X-ref sources), 'nc_programs', 'time' (cycle estimate), 'tools' (the tool "
    "sheet), 'library' (a tool library's catalog to add from - "
    "'scope'/'library'/'tool_type' filter it; edits stay on cam_edit_tools), 'library_types' (the "
    "add_tools[].from_type vocabulary), 'machines' (the catalog cam_edit_setup assigns), "
    "'templates' (the toolpath library; apply/save: cam_apply_template / cam_save_template), "
    "'inspection' (probing results; 'measure'=<index> drills its out-of-tolerance points). "
    "Readable from any workspace; op validity is trustworthy only after Manufacture is entered."
)

tool = (
    Tool.create_simple(name="cam_get", description=TOOL_DESCRIPTION)
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper slices: operations | parameters | tool | references | nc_programs | "
                           "time | tools | library | library_types | machine | machines | templates | inspection "
                           "(list or comma-string). tool needs 'operation'; parameters needs 'operation' or "
                           "'setup'. Omit for the setups orientation slice."})
    .add_input_property("setup", {"type": "string",
            "description": "Scope operations/references/time/machine to this setup name, and the target for a setup parameter read (omit = all setups). It also picks WHICH operation a parameters/tool read means when several setups hold that operation name."})
    .add_input_property("operation", {"type": "string",
            "description": "The operation whose parameters/tool to read (required for include=tool)."})
    .add_input_property("preset", {"type": "string",
            "description": "With include=['tool']: drill this tool preset's feeds/speeds expressions."})
    .add_input_property("scope", {"type": "string", "enum": ["document", "local", "cloud", "hub"],
            "description": "With include=['library']: which library location (default document)."})
    .add_input_property("library", {"type": "string",
            "description": "With include=['library'] + a shared scope: the library name/url (omit to list the libraries there)."})
    .add_input_property("tool_type", {"type": "string",
            "description": "With include=['library']: filter the catalog by tool type (e.g. 'ball', 'drill')."})
    .add_input_property("vendor", {"type": "string",
            "description": "With include=['machines']: filter the machine catalog by vendor (e.g. 'Haas')."})
    .add_input_property("machine_type", {"type": "string",
            "enum": ["milling", "turning", "cutting", "additive"],
            "description": "With include=['machines']: keep only machines with this capability (the bundled catalog is mostly additive printers - 'milling' finds the mills)."})
    .add_input_property("template_location", {"type": "string",
            "description": "With include=['templates']: library location (cloud/local/fusion/...; default cloud)."})
    .add_input_property("template_url", {"type": "string",
            "description": "With include=['templates']: a specific folder URL to start at (overrides location)."})
    .add_input_property("template_depth", {"type": "integer",
            "description": "With include=['templates']: folder depth to walk (default 4)."})
    .add_input_property("measure", {"type": "string",
            "description": "With include=['inspection']: drill one measure's out-of-tolerance points by INDEX ('0'), or one of its paths ('0/1'). A measure folder has no API-readable name."})
    .add_input_property("max_results", {"type": "integer",
            "description": "With include=['inspection'] + 'measure': cap on point rows (default 50, max 200)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
