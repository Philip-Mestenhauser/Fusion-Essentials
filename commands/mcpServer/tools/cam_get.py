# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: cam_get - one read for the active document's CAM (Manufacture) state, by zoom level.

The "rich read" pattern (see CLAUDE.md "Reads are RICH"): a default
orientation slice + `include=[...]` to pull deeper slices on demand. GraphQL-shaped - ask for the
fields you need, pay for depth only when you want it.

Zoom levels:
  default (no include)  -> ORIENTATION: the setups (machine + selected models/fixtures/stock) + per-setup
                           op_states (valid/out_of_date/suppressed/error/warning/no_toolpath tally - zero
                           buckets dropped), invalidation_reasons (WHY the out-of-date ops are stale:
                           Design changed: WCS/Fixture/Model, Dependency changed, ...),
                           machine_out_of_date, and blocked_by (e.g. no_machine_selected). "What CAM
                           jobs exist, are they current, and why not."
  include=['operations']  -> per-op state grouped by setup, LED BY a summary (states tally + exceptions
                           = the active blockers + a validity_basis-gated readiness verdict). Each op
                           carries blocked_by/requires. (The setup-level invalidation_reasons is dropped
                           here - each op restates it.)
  include=['references']  -> each setup's external (X-ref) models/fixtures/stock -> source document.
  include=['nc_programs'] -> the NC/post programs.
  include=['time']        -> machining cycle-time estimate (per setup + total).
  include=['tools']       -> the distinct cutting tools used across operations (the tool sheet).

A 'setup' filter scopes operations/references/time to one setup. The handler is a THIN ROUTER over
_slice_*() helpers - one per slice, each independently testable; the file stays readable-whole.

CAVEAT (carried from the CAM reads): operation VALIDITY / is_out_of_date is only trustworthy once the
MANUFACTURE workspace has been entered - the CAM model doesn't re-evaluate against changed geometry
until then. The status slice reports the flags but they can be stale from Design. Read-only.
"""

import json

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, terse

app = adsk.core.Application.get()

_SLICES = ("operations", "parameters", "tool", "references", "nc_programs", "time", "tools", "library",
           "templates")

# Keep operation rows readable (via _common.terse): a healthy op collapses to {name, tool, strategy, state}; an
# abnormal op keeps the flag(s) that aren't default (is_suppressed=true, has_error=true, ...) and pops.
_OP_NOISE = {"is_generating": False, "is_suppressed": False, "is_optional": False,
             "has_warning": False, "has_error": False, "is_out_of_date": False,
             "has_toolpath": True, "toolpath_valid": True}


# ── slice helpers - each calls a read-implementation handler in _cam_common and unwraps its payload ──
#
# The handlers (get_cam_setups_handler, get_cam_operations_handler, ...) live in _cam_common - the
# shared CAM substrate. cam_get is the thin rich-read router/surface over them; a slice decodes the
# handler's ok() payload to a dict, then shapes/bounds it for the include= projection.

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
    from . import _cam_common as _cc
    return _unwrap(_cc.get_cam_setups_handler())


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
    from . import _cam_common as _cc
    payload, err = _unwrap(_cc.get_cam_operations_handler(setup=setup))
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
                               "one setup, or read the per-setup operation_count in the default slice.")
    return payload, err


def _slice_references(cam, setup):
    """Each setup's external X-ref models/fixtures/stock -> source document."""
    from . import _cam_common as _cc
    return _unwrap(_cc.get_setup_references_handler(setup=setup))


def _slice_nc_programs(cam):
    """The NC/post programs - SUMMARY only (name, machine, post, op count + post_parameter_count). The
    full post_parameters are the post's static schema (often 60+ rows, identical across programs), a
    deeper level not dumped here - point at it rather than flooding (CLAUDE.md 'point, don't inline')."""
    from . import _cam_common as _cc
    payload, err = _unwrap(_cc.get_nc_programs_handler())
    if payload:
        for p in payload.get("nc_programs", []):
            params = p.pop("post_parameters", None)
            if params is not None:
                p["post_parameter_count"] = len(params)
    return payload, err


def _slice_time(cam, setup):
    """Machining cycle-time estimate (per setup + total)."""
    from . import _cam_common as _cc
    return _unwrap(_cc.get_machining_time_handler(setup=setup))


def _slice_tools(cam):
    """The distinct cutting tools used across operations (the tool sheet)."""
    from . import _cam_common as _cc
    return _unwrap(_cc.get_tool_list_handler())


def _slice_library(cam, scope, library, tool_type):
    """A tool LIBRARY's catalog (the tools you can ADD), by scope: document/local/cloud/hub. A shared
    scope with no 'library' lists the libraries there. Distinct from 'tools' (what ops USE); the write
    actions stay on cam_edit_tools."""
    from . import cam_edit_tools
    return _unwrap(cam_edit_tools.read_library(scope or "document", library, tool_type))


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

def _find_operation(cam, name):
    """The Operation named `name` (exact, then case-insensitive substring), or (None, available-names)."""
    want = (name or "").strip()
    exact = contains = None
    names = []
    for i in range(safe(lambda: cam.setups.count, 0)):
        s = cam.setups.item(i)
        for o in safe(lambda s=s: s.allOperations, []) or []:
            oc = adsk.cam.Operation.cast(o)
            if not oc:
                continue
            nm = safe(lambda oc=oc: oc.name) or ""
            if len(names) < 60:
                names.append(nm)
            if nm == want:
                exact = oc
            elif contains is None and want and want.lower() in nm.lower():
                contains = oc
    return (exact or contains), names


def _grouped_visible_params(param_coll):
    """The VISIBLE + ENABLED parameters, grouped into the UI's sections via the `group_*`/group-toggle
    sentinels in order. Returns {section_title: [{name, title, expression}]} - the machining values an
    agent reads, organized like the Fusion panel, not a flat 400-row dump."""
    groups = {}
    current = "General"
    n = safe(lambda: param_coll.count, 0)
    for i in range(n):
        p = param_coll.item(i)
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


def _slice_parameters(cam, operation):
    """ONE operation's machining parameters, visible-only and grouped by section. Requires 'operation'."""
    if not (operation or "").strip():
        return None, error("include=['parameters'] needs 'operation' - the operation whose settings to "
                           "read (scope first with cam_get(setup=..., include=['operations'])).")
    op, names = _find_operation(cam, operation)
    if not op:
        return None, error(f"No operation matching '{operation}'. Available (sample): "
                           f"{', '.join(n for n in names[:20] if n)}.")
    groups = _grouped_visible_params(safe(lambda: op.parameters))
    return {"operation": safe(lambda: op.name), "strategy": safe(lambda: op.strategy),
            "sections": groups,
            "parameter_count": sum(len(v) for v in groups.values())}, None


def _slice_tool(cam, operation, preset):
    """ONE operation's tool: spec + its preset NAMES (a tool can hold 20+); 'preset' drills one preset's
    expressions (the feeds/speeds recipe). Requires 'operation'."""
    if not (operation or "").strip():
        return None, error("include=['tool'] needs 'operation' - the operation whose tool to read.")
    op, names = _find_operation(cam, operation)
    if not op:
        return None, error(f"No operation matching '{operation}'. Available (sample): "
                           f"{', '.join(n for n in names[:20] if n)}.")
    t = safe(lambda: op.tool)
    if not t:
        return {"operation": safe(lambda: op.name), "tool": None}, None
    presets = safe(lambda: t.presets)
    pnames = []
    for i in range(safe(lambda: presets.count, 0) if presets else 0):
        pnames.append(safe(lambda i=i: presets.item(i).name))
    out = {"operation": safe(lambda: op.name),
           "tool": safe(lambda: t.description),
           "preset_names": pnames, "preset_count": len(pnames)}
    want_preset = (preset or "").strip()
    if want_preset:
        chosen = None
        for i in range(safe(lambda: presets.count, 0) if presets else 0):
            ps = presets.item(i)
            if (safe(lambda ps=ps: ps.name) or "") == want_preset:
                chosen = ps
                break
        if not chosen:
            return None, error(f"No preset named '{want_preset}' on this tool. Available: "
                               f"{', '.join(n for n in pnames if n)}.")
        exprs = {}
        pp = safe(lambda: chosen.parameters)
        for j in range(safe(lambda: pp.count, 0) if pp else 0):
            param = pp.item(j)
            exprs[safe(lambda param=param: param.name)] = safe(lambda param=param: param.expression)
        out["preset"] = {"name": want_preset, "expressions": exprs}
    return out, None


# ── the router ─────────────────────────────────────────────────────────────────────────────────────

def handler(include=None, setup: str = "", operation: str = "", preset: str = "",
            scope: str = "", library: str = "", tool_type: str = "",
            template_location: str = "", template_url: str = "", template_depth: int = 0) -> dict:
    """Read the active document's CAM state at the right zoom level (rich read - CLAUDE.md "Reads are
    RICH"). Default (no 'include'): the orientation slice - setups + per-setup op counts + status.
    'include' widens; scope first, then deepen:
      setups (default) -> include=['operations'] (per-op, 'setup' scopes) -> include=['parameters']
      or ['tool'] with 'operation' (ONE op's settings / tool) -> 'preset' drills one tool preset.
    Also: 'references', 'nc_programs', 'time' (document/setup level). Read-only.
    """
    cam, cerr = _get_cam()
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
    if "parameters" in inc:                     # deep: ONE operation's settings, grouped
        out["parameters"], e = _slice_parameters(cam, operation)
        if e:
            return e
    if "tool" in inc:                           # deep: ONE operation's tool + presets (preset= drills)
        out["tool"], e = _slice_tool(cam, operation, preset)
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
        out["time"], e = _slice_time(cam, setup)
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
    if "templates" in inc:                      # the CAM toolpath template library tree
        out["templates"], e = _slice_templates(cam, template_location, template_url, template_depth)
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


def _get_cam():
    """The active document's CAM product, or (None, reason). Works regardless of active workspace."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    cam = safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "This document has no CAM (Manufacture) data."
    return cam, None


def _normalize_include(include):
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


TOOL_DESCRIPTION = (
    "Read the active document's CAM (Manufacture) state by zoom level. Default (no 'include'): the "
    "orientation slice - per setup: op_states (valid/out_of_date/suppressed/error/warning tally), "
    "invalidation_reasons (why ops are stale), and machine_out_of_date. 'include' deepens, "
    "and you SCOPE before you deepen: 'operations' (per-op state; 'setup' filters; cam_compare_operations "
    "diffs two by name) -> 'parameters' or "
    "'tool' WITH 'operation'=<name> for ONE op's machining settings (grouped by section) or its tool + "
    "presets -> 'preset'=<name> for that preset's feeds/speeds expressions. Document-level slices: "
    "'references' (X-ref source docs), 'nc_programs', 'time' (cycle estimate), 'tools' (the tool sheet "
    "ops USE), 'library' (a tool LIBRARY's catalog you can add FROM; 'scope'=document/local/cloud/hub, "
    "'library'=shared-lib name/url, 'tool_type' filters; the add/remove/edit writes stay on "
    "cam_edit_tools), 'templates' (the toolpath TEMPLATE library tree; 'template_location'="
    "cloud/local/fusion/..., 'template_url' a folder, 'template_depth'; apply/save stay on "
    "cam_apply_template / cam_save_template). Read-only; works without switching to Manufacture (op "
    "VALIDITY is only trustworthy once Manufacture has been entered)."
)

tool = (
    Tool.create_simple(name="cam_get", description=TOOL_DESCRIPTION)
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper slices: operations | parameters | tool | references | nc_programs | "
                           "time | tools (list or comma-string). parameters/tool need 'operation'. "
                           "Omit for the setups orientation slice."})
    .add_input_property("setup", {"type": "string",
            "description": "Scope operations/references/time to this setup name (omit = all setups)."})
    .add_input_property("operation", {"type": "string",
            "description": "The operation whose parameters/tool to read (required for include=parameters/tool)."})
    .add_input_property("preset", {"type": "string",
            "description": "With include=['tool']: drill this tool preset's feeds/speeds expressions."})
    .add_input_property("scope", {"type": "string", "enum": ["document", "local", "cloud", "hub"],
            "description": "With include=['library']: which library location (default document)."})
    .add_input_property("library", {"type": "string",
            "description": "With include=['library'] + a shared scope: the library name/url (omit to list the libraries there)."})
    .add_input_property("tool_type", {"type": "string",
            "description": "With include=['library']: filter the catalog by tool type (e.g. 'ball', 'drill')."})
    .add_input_property("template_location", {"type": "string",
            "description": "With include=['templates']: library location (cloud/local/fusion/...; default cloud)."})
    .add_input_property("template_url", {"type": "string",
            "description": "With include=['templates']: a specific folder URL to start at (overrides location)."})
    .add_input_property("template_depth", {"type": "integer",
            "description": "With include=['templates']: folder depth to walk (default 4)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
