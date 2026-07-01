# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: design_get - one read for the active design's structure, by zoom level.

The "rich read" pattern (see CLAUDE.md "Reads are RICH"): a default
orientation slice + `include=[...]` to pull deeper slices on demand. GraphQL-shaped - ask for exactly
the fields you need, pay for depth only when you want it.

Zoom levels:
  default (no include)  -> ORIENTATION: design type + feature count + timeline_healthy (TIMELINE-scoped
                           only; stale refs show as is_out_of_date on tree nodes, the doc-wide verdict is
                           workspace_orient.is_healthy) + a CONTENT fingerprint (bodies/sketches/
                           components/joints). Cheap, safe-blind, never floods. The note advertises include=.
  include=['tree']      -> the component/occurrence tree (honors max_depth; truncates + flags).
  include=['timeline']  -> the ordered parametric timeline (honors 'group'; include_suppressed).
  include=['mode']      -> the full modelling-mode capability map (the can{} block).
  include=['configurations'] -> the configuration table (READ only; switching configs is design_configure).

The handler is a THIN ROUTER over _slice_*() helpers - one per slice, each independently testable; the
file stays readable-whole. Tree/timeline/configs read directly here; mode/health delegate to the
shared get_mode_handler/health_handler (which design_recompute also reports). Read-only.
"""

import json

import adsk.core
import adsk.cam
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, terse
from . import _common

app = adsk.core.Application.get()

# The deeper slices an agent can opt into (the default returns NONE of these in full - only summaries).
_SLICES = ("mode", "tree", "timeline", "configurations")


# ── slice helpers - each DELEGATES to the source tool's existing handler and unwraps its payload ───
#
# Each slice calls the corresponding old tool's handler (which already returns the exact ok() payload)
# and returns the decoded dict - so the fold reproduces the originals VERBATIM and CANNOT drift from
# them while both coexist. When the source tools are deleted, their read helpers move here; until then
# this is the safest fold (one source of truth per slice). _unwrap returns (payload, error_result).

def _unwrap(result):
    """Decode a source handler's result -> (payload_dict, None) on ok, or (None, error_result) on error
    so the caller can surface a slice's own guard (e.g. timeline raises in direct mode)."""
    if result.get("isError"):
        return None, result
    try:
        return json.loads(result["content"][0]["text"]), None
    except Exception:
        return None, result


def _slice_mode(design):
    """The modelling-mode + capability map (via design_mode.get_mode_handler)."""
    from . import design_mode
    return _unwrap(design_mode.get_mode_handler())


def _slice_health(design):
    """The timeline health rollup (via design_ops.health_handler) - cheap; in the default slice."""
    from . import design_ops
    return _unwrap(design_ops.health_handler())


# ── component/occurrence tree ──────────────────────────────────────────────────────────────────────
_TREE_DEFAULT_DEPTH = 3
_TREE_MAX_DEPTH = 8
_TREE_MAX_NODES = 2000


def _find_occurrence_by_name(root, want_lower):
    """Depth-first search for an occurrence whose name (or its component's name) matches (bounded)."""
    try:
        stack = list(root.occurrences)
    except Exception:
        return None
    seen = 0
    while stack and seen < _TREE_MAX_NODES:
        occ = stack.pop()
        seen += 1
        nm = safe(lambda: occ.name, "")
        comp_nm = safe(lambda: occ.component.name, "")
        if want_lower in (nm or "").lower() or want_lower == (comp_nm or "").lower():
            return occ
        try:
            stack.extend(list(occ.childOccurrences))
        except Exception:
            pass
    return None


def _walk_occurrence(occ, depth, max_depth, counter):
    counter["n"] += 1
    if counter["n"] >= _TREE_MAX_NODES:
        counter["truncated"] = True
    node = {
        "name": safe(lambda: occ.name),
        # fullPathName is the UNAMBIGUOUS instance key (a name is only locally unique - the same
        # "Bolt:1" recurs under every sub-assembly); name-consuming tools resolve by it via OccurrenceRef.
        "full_path": safe(lambda: occ.fullPathName),
        "component": safe(lambda: occ.component.name),
        "is_reference": safe(lambda: occ.isReferencedComponent, False),
        "body_count": safe(lambda: occ.bRepBodies.count, 0),
        "child_count": safe(lambda: occ.childOccurrences.count, 0),
    }
    if node["is_reference"]:
        try:
            dr = occ.documentReference
            if dr:
                node["source_version"] = safe(lambda: dr.version)
                node["is_out_of_date"] = safe(lambda: dr.isOutOfDate)
                df = safe(lambda: dr.dataFile)
                if df:
                    node["source_id"] = safe(lambda: df.id)
                    node["source_name"] = safe(lambda: df.name)
                    node["source_url"] = safe(lambda: df.fusionWebURL)
        except Exception:
            pass
    if depth + 1 < max_depth and node["child_count"] and counter["n"] < _TREE_MAX_NODES:
        kids = []
        try:
            for child in occ.childOccurrences:
                if counter["n"] >= _TREE_MAX_NODES:
                    counter["truncated"] = True
                    break
                kids.append(_walk_occurrence(child, depth + 1, max_depth, counter))
        except Exception:
            pass
        if kids:
            node["children"] = kids
    elif node["child_count"]:
        node["children_truncated"] = True
    return node


def _slice_tree(design, max_depth, component):
    """The component/occurrence tree, bounded by max_depth + node cap (truncated flag)."""
    try:
        depth = max(1, min(int(max_depth), _TREE_MAX_DEPTH))
    except Exception:
        depth = _TREE_DEFAULT_DEPTH
    root = safe(lambda: design.rootComponent)
    if root is None:
        return None, error("No root component.")
    counter = {"n": 0, "truncated": False}
    want = (component or "").strip().lower()
    if want:
        start = _find_occurrence_by_name(root, want)
        if not start:
            return None, error(f"Component/occurrence not found: '{component}'.")
        return {"root": component, "max_depth": depth, "truncated": counter["truncated"],
                "tree": _walk_occurrence(start, 0, depth, counter)}, None
    children = []
    try:
        for occ in root.occurrences:
            if counter["n"] >= _TREE_MAX_NODES:
                counter["truncated"] = True
                break
            children.append(_walk_occurrence(occ, 0, depth, counter))
    except Exception as e:
        return None, error(f"Could not read root occurrences: {e}")
    out = {"root": safe(lambda: root.name), "max_depth": depth, "node_count": counter["n"],
           "truncated": counter["truncated"], "children": children}
    # Bodies that live directly in the ROOT component (not in any occurrence). The occurrence walk above
    # never sees these, so without this an agent reading the tree can't tell they exist - and a root body
    # is NOT a jointable occurrence (promote it to a component to joint it).
    root_bodies = _root_body_names(root)
    if root_bodies:
        out["root_bodies"] = root_bodies
        out["root_bodies_note"] = ("Bodies directly in the root component (not occurrences). A root body "
                                   "can't be jointed - model_create_component then move it in to joint it.")
    return out, None


def _root_body_names(root):
    """Names of bodies directly in the root component (capped). [] if none."""
    names = []
    try:
        bodies = root.bRepBodies
        for i in range(min(safe(lambda: bodies.count, 0), _TREE_MAX_NODES)):
            b = bodies.item(i)
            if b is not None:
                names.append(safe(lambda b=b: b.name) or f"Body{i+1}")
    except Exception:
        pass
    return names


# ── parametric timeline ────────────────────────────────────────────────────────────────────────────
_TIMELINE_MAX_ITEMS = 5000
_HEALTH_LABELS = {0: "healthy", 1: "warning", 2: "error", 3: "suppressed", 4: "rolled_back", 5: "unknown"}

# Keep timeline rows readable (via _common.terse): a healthy row collapses to {index, name, type}; an
# abnormal row keeps (and pops with) its is_suppressed=true / health="error" / rolled_back. Keys -> the
# value that means "all normal".
_TIMELINE_NOISE = {"is_group": False, "is_suppressed": False, "is_rolled_back": False,
                   "parent_group": None, "health": "healthy"}


def _entity_type(obj):
    """The timeline object's entity class name (ExtrudeFeature/Sketch/Joint/...); 'TimelineGroup' for a group."""
    if safe(lambda: obj.isGroup):
        return "TimelineGroup"
    ent = safe(lambda: obj.entity)
    return type(ent).__name__ if ent is not None else None


def _object_summary(obj):
    health = safe(lambda: obj.healthState)
    out = {
        "index": safe(lambda: obj.index),
        "name": safe(lambda: obj.name),
        "type": _entity_type(obj),
        "is_group": bool(safe(lambda: obj.isGroup)),
        "is_suppressed": bool(safe(lambda: obj.isSuppressed)),
        "is_rolled_back": bool(safe(lambda: obj.isRolledBack)),
        "parent_group": safe(lambda: obj.parentGroup.name if obj.parentGroup else None),
        "health": _HEALTH_LABELS.get(health, health),
    }
    msg = safe(lambda: obj.errorOrWarningMessage)
    if msg:
        out["message"] = msg
    return out


def _slice_timeline(design, include_suppressed, group):
    """The ordered parametric timeline, with healthy-row noise dropped (a normal row is
    {index,name,type}; a suppressed/errored row keeps its flags and stands out)."""
    try:
        timeline = design.timeline
    except Exception as e:
        return None, error(f"This design has no timeline (direct-modeling, or no history): {e}")
    want_group = (group or "").strip()
    items, truncated = [], False
    states, exceptions = {}, []                 # exception-first rollup over the timeline
    try:
        for i in range(timeline.count):
            if len(items) >= _TIMELINE_MAX_ITEMS:
                truncated = True
                break
            summ = _object_summary(timeline.item(i))
            health = summ.get("health", "healthy")
            states[health] = states.get(health, 0) + 1
            # exception = a feature that FAILED (error/warning health) - not just suppressed (intentional).
            if health in ("error", "warning"):
                exceptions.append({"name": summ.get("name"), "index": summ.get("index"), "health": health})
            if not include_suppressed and summ["is_suppressed"]:
                continue
            if want_group and (summ["parent_group"] or "") != want_group:
                continue
            items.append(terse(summ, _TIMELINE_NOISE))
    except Exception as e:
        return None, error(f"Could not read the timeline: {e}")
    groups = {}
    try:
        for tg in timeline.timelineGroups:
            gname = safe(lambda tg=tg: tg.name)
            if gname is not None:
                groups[gname] = safe(lambda tg=tg: tg.count, 0)
    except Exception:
        pass
    payload = {"marker_position": safe(lambda: timeline.markerPosition),
               "count": safe(lambda: timeline.count), "returned": len(items),
               "summary": {"states": states, "exceptions": exceptions},
               "groups": groups, "timeline": items}
    if truncated:
        payload["truncated"] = True
    return payload, None


# ── configurations (read side) ──────────────────────────────────────────────────────────────────────
_CONFIG_MAX_ROWS = 1000


def _row_summary(row, active_id):
    rid = safe(lambda: row.id)
    return {"name": safe(lambda: row.name), "id": rid, "index": safe(lambda: row.index),
            "is_active": (rid is not None and rid == active_id)}


def _column_summary(col):
    # ConfigurationColumn exposes .title (readable label), NOT .name (.name raises).
    return {"title": safe(lambda: col.title), "id": safe(lambda: col.id),
            "index": safe(lambda: col.index), "type": safe(lambda: type(col).__name__)}


def _slice_configurations(design):
    """The configuration table, READ side. Returns (payload, error); a non-configured design errors
    (the router degrades that to a {configured: False} marker rather than failing the read)."""
    table = safe(lambda: design.configurationTopTable)
    if not table:
        return None, error("The active design is not a Configured Design (it has no configuration "
                           "table) - e.g. a design with Variant A/Variant B style options.")
    active_row = safe(lambda: table.activeRow)
    active_id = safe(lambda: active_row.id) if active_row else None
    rows, truncated = [], False
    try:
        for r in table.rows:
            if len(rows) >= _CONFIG_MAX_ROWS:
                truncated = True
                break
            rows.append(_row_summary(r, active_id))
    except Exception:
        pass
    columns = []
    try:
        for c in table.columns:
            columns.append(_column_summary(c))
    except Exception:
        pass
    out = {"table_name": safe(lambda: table.name), "table_id": safe(lambda: table.id),
           "active_configuration": safe(lambda: active_row.name) if active_row else None,
           "configuration_count": len(rows), "configurations": rows, "columns": columns}
    if truncated:
        out["truncated"] = True
    return out, None


def _fingerprint(design):
    """The cheap 'what IS this model' digest for the default: counts of bodies / sketches / components /
    occurrences / joints / parameters, so an agent learns the model's SHAPE without include=tree. The
    field the thin mode+health summary was missing."""
    root = safe(lambda: design.rootComponent)
    if root is None:
        return None
    fp = {
        "bodies": safe(lambda: root.bRepBodies.count, 0),
        "sketches": safe(lambda: root.sketches.count, 0),
        "components": safe(lambda: root.allOccurrences.count, 0),   # 0 = single-component design
        # asBuiltJoints is a separate collection from joints; count both or as-built joints read as 0
        "joints": safe(lambda: root.joints.count, 0) + safe(lambda: root.asBuiltJoints.count, 0),
        # userParameters = the ones an agent can drive; modelParameters includes internal ones it can't.
        "parameters": safe(lambda: design.userParameters.count, 0),
    }
    return {k: v for k, v in fp.items() if v}   # omit zero counts (single-component, no joints, ...)


# The action tools for each content class the fingerprint can report. Only classes that are PRESENT and
# not already-obvious get a pointer (bodies/sketches are omitted - every agent knows model_*/sketch_*).
# The param family in particular is a closed clique nothing else points into, so this is its one inbound
# breadcrumb from a read an agent actually starts with.
_CONTENT_TOOLS = {
    "parameters": "param_get (list/read), param_set / param_add (change or create)",
    "joints": "assembly_probe (wiring + health), joint_drive (pose by value)",
    "components": "design_get(include=['tree']) for the full tree; assembly_probe for positions",
}


def _content_pointers(contents):
    """Map the present content classes to the tools that act on them (present + non-obvious only), so a
    read that says 'parameters: 40' also says HOW to read/change them - the missing inbound breadcrumb."""
    if not contents:
        return {}
    return {k: _CONTENT_TOOLS[k] for k in _CONTENT_TOOLS if contents.get(k)}


def _has_cam(design):
    """True when the active document also has a CAM (Manufacture) product. A CAM document's whole point
    is its machining state, which design_get is blind to - so a cam pointer to cam_get is its inbound
    breadcrumb. itemByProductType('CAMProductType') is None when there's no CAM data."""
    doc = safe(lambda: design.parentDocument)
    if doc is None:
        return False
    return safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType("CAMProductType"))) is not None


# ── the router ─────────────────────────────────────────────────────────────────────────────────────

def handler(include=None, max_depth: int = 3, component: str = "",
            include_suppressed: bool = True, group: str = "") -> dict:
    """Read the active design at the right zoom level (rich read - see CLAUDE.md "Reads are RICH").

    Default (no 'include'): the ORIENTATION slice - modelling mode summary + a shallow component-tree
    summary + timeline_healthy (timeline-only; not stale refs). Cheap, safe to call blind. 'include'
    widens to deeper slices:
    'tree' (full component tree; 'max_depth'/'component' scope it), 'timeline' (the feature list;
    'include_suppressed'/'group' scope it), 'mode' (the full capability map), 'configurations' (the
    config table - read only). Read-only.
    """
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    inc = _normalize_include(include)
    bad = [s for s in inc if s not in _SLICES]
    if bad:
        return error(f"Unknown include {bad}. Valid: {', '.join(_SLICES)}.")

    # ── default ORIENTATION slice - always present, always BOUNDED + DENSE ──
    mode_full, merr = _slice_mode(design)
    if merr:
        return merr
    health, _herr = _slice_health(design)          # cheap rollup; degrades to None on a direct design

    out = {
        # the headline: what kind of design, how big the build, what's in it. design_type lives ONCE
        # here (not restated in a sub-dict). in_base_feature_edit only when TRUE (omit it when false).
        "design_type": safe(lambda: mode_full.get("design_type")),
        "feature_count": safe(lambda: mode_full.get("timeline_feature_count")),
        # SCOPE: timeline-only (errored/warned features). It does NOT cover stale references - those show
        # as is_out_of_date on tree nodes, and the doc-wide verdict (timeline + refs + joints) is
        # workspace_orient's is_healthy. The qualified name stops an agent reading a green timeline as a
        # whole-document all-clear when a reference is out of date.
        "timeline_healthy": safe(lambda: (health or {}).get("healthy")),
        "contents": _fingerprint(design),          # bodies/sketches/components/joints/parameters
    }
    # for each PRESENT + non-obvious content class, name the tool that acts on it - the inbound
    # breadcrumb to families (param_*, joint_*) that a read-first agent would otherwise never find.
    ptrs = _content_pointers(out["contents"])
    # a CAM document's machining state is invisible to this design read - point at cam_get so an agent
    # drilling via design_get learns the Manufacture half exists.
    if _has_cam(design):
        ptrs["cam"] = "cam_get for the machining setups/operations (this document has CAM data)"
    if ptrs:
        out["pointers"] = ptrs
    if mode_full.get("in_base_feature_edit"):
        out["in_base_feature_edit"] = True
    # surface health DETAIL only when there's something wrong (else 'healthy' above says it all).
    if health and (health.get("error_count") or health.get("warning_count")):
        out["health"] = {k: v for k, v in health.items()
                         if k in ("errors", "warnings", "error_count", "warning_count") and v}

    # ── deeper slices (opt-in) ──
    if "mode" in inc:
        out["mode_detail"] = mode_full          # the full capability can{} map
    if "tree" in inc:
        out["tree"], terr = _slice_tree(design, max_depth, component)
        if terr:
            return terr
    if "timeline" in inc:
        out["timeline"], tlerr = _slice_timeline(design, include_suppressed, group)
        if tlerr:
            return tlerr
    if "configurations" in inc:
        # A non-configured design legitimately has no config table - that's not an error of design_get,
        # so degrade to a null + reason rather than failing the whole read.
        cfg, cerr = _slice_configurations(design)
        out["configurations"] = cfg if cfg is not None else {
            "configured": False,
            "reason": (cerr.get("message") if cerr else "no configuration table"),
        }

    # advertise the slices NOT yet pulled (load-bearing: an un-named flag is invisible to the agent).
    remaining = [s for s in _SLICES if s not in inc]
    if remaining:
        out["note"] = ("Orientation slice. Pull deeper with include=" + str(remaining) +
                       " (e.g. include=['tree'] for the full component tree, ['timeline'] for the "
                       "feature list, ['mode'] for the capability map, ['configurations'] for configs). "
                       "'max_depth'/'component' scope the tree; 'group'/'include_suppressed' the timeline.")
    return ok(out)


def _normalize_include(include):
    """Accept include as a list, a comma-string, or a single slice name; -> a list of slice names."""
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


TOOL_DESCRIPTION = (
    "Read the active DESIGN by zoom level (one rich read for mode + tree + timeline + health + "
    "configs). Default (no 'include'): the orientation slice - modelling mode, a shallow component-tree "
    "summary, and timeline_healthy (timeline errors/warnings ONLY - NOT stale references, which appear "
    "as is_out_of_date on tree nodes; the whole-document verdict is workspace_orient.is_healthy). "
    "'include' pulls deeper: 'tree' (full component/occurrence tree; "
    "'max_depth'/'component' scope it), 'timeline' (the feature list; 'group'/'include_suppressed' "
    "scope it), 'mode' (full capability map), 'configurations' (the config table). Read-only; the "
    "default is safe to call blind and names its deeper slices."
)

tool = (
    Tool.create_simple(name="design_get", description=TOOL_DESCRIPTION)
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper slices to include: any of tree | timeline | mode | configurations "
                           "(a list or comma-string). Omit for the orientation slice."})
    .add_input_property("max_depth", {"type": "integer",
            "description": "Tree depth when include=tree (default 3, max 8)."})
    .add_input_property("component", {"type": "string",
            "description": "Start the tree at this component/occurrence name (include=tree)."})
    .add_input_property("include_suppressed", {"type": "boolean",
            "description": "Include suppressed timeline objects when include=timeline (default true)."})
    .add_input_property("group", {"type": "string",
            "description": "Only this timeline group when include=timeline."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
