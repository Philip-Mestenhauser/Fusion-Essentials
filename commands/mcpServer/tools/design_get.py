# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""RICH READ: design_get - the active design's structure by zoom level (see CLAUDE.md "Reads are
RICH"). Default: a cheap orientation slice (design type, feature count, timeline health, content
fingerprint). include=['tree'|'timeline'|'mode'|'configurations'|'materials'|'appearances'|
'attributes'] pulls one deeper slice at a time via a thin router over _slice_*() helpers. Read-only.
"""

import json
from itertools import islice

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, terse
from . import _common
from . import _cam_common
from . import _inputs
from . import _materials

app = adsk.core.Application.get()

# The deeper slices an agent can opt into (the default returns NONE of these in full - only summaries).
_SLICES = ("mode", "tree", "timeline", "configurations", "materials", "appearances", "attributes")


# ── slice helpers - each builds one slice's payload, independently testable ─────────────────────────
#
# mode/health delegate to the shared get_mode_handler/health_handler (design_mode.py / design_ops.py);
# tree/timeline/configurations read directly. _unwrap decodes a handler's ok() result.

def _unwrap(result):
    """Decode a handler's result -> (payload_dict, None) on ok, or (None, error_result) on error
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
_TREE_BODY_CAP = 25          # per-node body records when tree_bodies=true


def _body_rows(bodies):
    """Per-body records for one tree node, capped at _TREE_BODY_CAP: name + entityToken handle (the
    exact identity BodyRef/TargetRef consumers accept - the counterpart of the node's occurrence
    handle) + solid/visible flags via read_flag (None = unreadable, never a coerced False). Returns
    (rows, truncated)."""
    items = list(islice(_common.iter_collection(bodies), _TREE_BODY_CAP + 1))
    truncated = len(items) > _TREE_BODY_CAP
    rows = [{
        "name": safe(lambda b=b: b.name),
        "handle": safe(lambda b=b: b.entityToken),
        "is_solid": _common.read_flag(lambda b=b: b.isSolid),
        "visible": _common.read_flag(lambda b=b: b.isVisible),
    } for b in items[:_TREE_BODY_CAP]]
    return rows, truncated


def _find_occurrence_by_name(root, want):
    """Resolve the occurrence to root the tree at: a COMPONENT name (root at its first instance - every
    instance of a component shows the same structure) or an occurrence name/fullPathName (via the
    shared ambiguity-refusing _inputs._resolve_occurrence). Returns (occurrence, error): an ambiguous
    occurrence name is refused with its candidates, not silently matched to the first. Bounded DFS."""
    want_lower = (want or "").strip().lower()
    # 1) A COMPONENT name (exact): all instances share one structure, so the first instance roots it.
    try:
        stack = list(root.occurrences)
    except Exception:
        stack = []
    seen = 0
    while stack and seen < _TREE_MAX_NODES:
        occ = stack.pop()
        seen += 1
        if (safe(lambda: occ.component.name, "") or "").lower() == want_lower:
            return occ, None
        try:
            stack.extend(list(occ.childOccurrences))
        except Exception:
            pass
    # 2) An OCCURRENCE name / fullPathName - the shared resolver refuses an ambiguous name instead of
    # returning whichever same-named instance came first.
    occ, occ_err = _inputs._resolve_occurrence("component", want)
    if occ is not None:
        return occ, None
    if occ_err and "ambiguous" in occ_err.lower():
        return None, occ_err
    return None, None


def _unresolved_node(occ, detail):
    """The tree row for an occurrence whose referenced component will not load. Every other read on it
    RAISES (fullPathName, isVisible, childOccurrences.count included), so the row carries the name -
    the one identity that still reads - and the raise text, instead of a full node built from
    swallowed defaults. Its PRESENCE is the point: the row was absent from this tree entirely, so a
    container holding one read child_count 4 with four healthy children listed."""
    name = safe(lambda: occ.name)
    return {"name": name if name else "(unreadable name)",
            "unresolved": True,
            "detail": detail}


def _unresolved_children(occ):
    """The unresolved children of `occ`, classified by the shared detector over the COMPONENT-LOCAL
    collection. childOccurrences - what this tree descends - silently DROPS an occurrence whose
    reference is broken (its assembly path is invalid), while component.occurrences holds it."""
    comp = safe(lambda: occ.component)
    rows = []
    for child in _common.iter_collection(safe(lambda: comp.occurrences) if comp else None):
        is_broken, detail = _common.broken_reference(child)
        if is_broken:
            rows.append(_unresolved_node(child, detail))
    return rows


def _walk_occurrence(occ, depth, max_depth, counter, with_bodies=False):
    counter["n"] += 1
    if counter["n"] >= _TREE_MAX_NODES:
        counter["truncated"] = True
    is_broken, broken_detail = _common.broken_reference(occ)
    if is_broken:
        return _unresolved_node(occ, broken_detail)
    node = {
        "name": safe(lambda: occ.name),
        # The entityToken is the EXACT instance identity, and the only one that always is: Fusion
        # enforces no name uniqueness, so a name repeats under every sub-assembly ("Bolt:1") and two
        # siblings can even wear one fullPathName (measured). Every occurrence-taking tool accepts this
        # handle; the path is the convenience form, which refuses rather than guess when it collides.
        "handle": safe(lambda: occ.entityToken),
        "full_path": safe(lambda: occ.fullPathName),
        "component": safe(lambda: occ.component.name),
        # read_flag, not safe(..., False): an unreadable flag is None here (the same honesty
        # _body_rows holds for is_solid/visible), and the freshness gate below treats None as
        # "try it" rather than as "local".
        "is_reference": _common.read_flag(lambda: occ.isReferencedComponent),
        # counted, not safe(..., 0): these two counts are the caller's evidence of what this node
        # HOLDS, and a coerced 0 says "no bodies / no children" about a collection nothing was read
        # from - which is also what stops the walk below descending. null is the only honest answer
        # for an unreadable count, and it takes the same (do not descend) branch without claiming it.
        "body_count": _common.counted(lambda: occ.bRepBodies.count),
        "child_count": _common.counted(lambda: occ.childOccurrences.count),
    }
    if with_bodies and node["body_count"]:
        rows, truncated = _body_rows(safe(lambda: occ.bRepBodies, None))
        if rows:
            node["bodies"] = rows
            if truncated:
                node["bodies_truncated"] = True
    # None (the flag did not read) takes the SAME branch as True: a local occurrence simply has no
    # documentReference, so attempting the read costs one safe() read and publishes real freshness
    # for an xref whose own flag is unreadable - where the False branch would silently drop it.
    if node["is_reference"] is not False:
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
    # The unresolved children are found on the COMPONENT-LOCAL collection and counted here, so
    # child_count (read off childOccurrences, which drops them) is never the whole story on its own.
    unresolved_kids = _unresolved_children(occ)
    if unresolved_kids:
        node["children_unresolved"] = len(unresolved_kids)
    if depth + 1 < max_depth and (node["child_count"] or unresolved_kids) and counter["n"] < _TREE_MAX_NODES:
        kids = []
        try:
            for child in occ.childOccurrences:
                if counter["n"] >= _TREE_MAX_NODES:
                    counter["truncated"] = True
                    break
                kids.append(_walk_occurrence(child, depth + 1, max_depth, counter, with_bodies))
        except Exception:
            pass
        kids.extend(unresolved_kids)
        if kids:
            node["children"] = kids
    elif node["child_count"] or unresolved_kids:
        node["children_truncated"] = True
    return node


def _slice_tree(design, max_depth, component, with_bodies=False):
    """The component/occurrence tree, bounded by max_depth + node cap (truncated flag). with_bodies
    adds each node's per-body records (name/handle/solid/visible, capped) - the read that makes a
    body targetable without replaying old feature receipts."""
    try:
        depth = max(1, min(int(max_depth), _TREE_MAX_DEPTH))
    except Exception:
        depth = _TREE_DEFAULT_DEPTH
    root = safe(lambda: design.rootComponent)
    if root is None:
        return None, error("No root component.")
    counter = {"n": 0, "truncated": False}
    if (component or "").strip():
        start, amb = _find_occurrence_by_name(root, component)
        if amb:
            return None, error(amb)
        if not start:
            return None, error(f"Component/occurrence not found: '{component}'.")
        # Walk FIRST, read the truncated flag AFTER: a dict literal evaluates its values in
        # order, so reading counter["truncated"] before the walk would pin the pre-walk False.
        scoped_tree = _walk_occurrence(start, 0, depth, counter, with_bodies)
        return {"root": component, "max_depth": depth, "truncated": counter["truncated"],
                "tree": scoped_tree}, None
    children = []
    try:
        for occ in root.occurrences:
            if counter["n"] >= _TREE_MAX_NODES:
                counter["truncated"] = True
                break
            children.append(_walk_occurrence(occ, 0, depth, counter, with_bodies))
    except Exception as e:
        return None, error(f"Could not read root occurrences: {e}")
    out = {"root": safe(lambda: root.name), "max_depth": depth, "node_count": counter["n"],
           "truncated": counter["truncated"], "children": children}
    # Bodies that live directly in the ROOT component (not in any occurrence). The occurrence walk above
    # never sees these, so without this an agent reading the tree can't tell they exist - and a root body
    # is NOT a jointable occurrence (promote it to a component to joint it).
    if with_bodies:
        root_rows, root_tr = _body_rows(safe(lambda: root.bRepBodies, None))
        if root_rows:
            out["root_bodies"] = root_rows
            if root_tr:
                out["root_bodies_truncated"] = True
    else:
        root_bodies = _root_body_names(root)
        if root_bodies:
            out["root_bodies"] = root_bodies
    if out.get("root_bodies"):
        out["root_bodies_note"] = ("Bodies directly in the root component (not occurrences). A root body "
                                   "can't be jointed - model_create_component then move it in to joint it.")
    return out, None


def _root_body_names(root):
    """Names of bodies directly in the root component (capped). [] if none."""
    names = []
    try:
        bodies = root.bRepBodies
        for i, b in enumerate(islice(_common.iter_collection(bodies), _TREE_MAX_NODES)):
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
        # read_flag: an unreadable row flag publishes null, and null does NOT equal the False in
        # _TIMELINE_NOISE, so terse KEEPS it - the row stands out as unknown instead of being
        # dropped as routine. The include_suppressed filter below keys on True only, so a row whose
        # is_suppressed did not read is always listed rather than hidden by a flag nobody read.
        "is_group": _common.read_flag(lambda: obj.isGroup),
        "is_suppressed": _common.read_flag(lambda: obj.isSuppressed),
        "is_rolled_back": _common.read_flag(lambda: obj.isRolledBack),
        "parent_group": safe(lambda: obj.parentGroup.name if obj.parentGroup else None),
        "health": _HEALTH_LABELS.get(health, health),
    }
    msg = safe(lambda: obj.errorOrWarningMessage)
    if msg:
        out["message"] = msg
    return out


# ── the model parameters a timeline feature owns (include=['timeline'], timeline_params=true) ──────
#
# A timeline row carries type+name only, so a fillet's radius is unreadable from it. The honest route
# to that number is the feature's own ModelParameters: each carries .createdBy (the Feature / Joint /
# JointOrigin that made it) and .role (its slot on that owner - 'OffsetZ', 'alignAngle', ...). This is
# ONE pass over design.allParameters grouped by owner, not a per-feature probe.
_PARAMS_PER_ROW = 16


def _owner_keys(entity):
    """The keys ONE owner is indexed and looked up under: its entityToken when that reads (exact),
    plus name+type as the fallback for a side whose token does not read. [] when neither reads."""
    keys = []
    tok = safe(lambda: entity.entityToken)
    if isinstance(tok, str) and tok:
        keys.append(("token", tok))
    name = safe(lambda: entity.name)
    if isinstance(name, str) and name:
        keys.append(("name", name, type(entity).__name__))
    return keys


def _model_parameters_by_owner(design):
    """{owner key -> {rows, tokens}} from ONE pass over design.allParameters.

    A ModelParameter exposes .createdBy and .role; a UserParameter has NO createdBy and the read
    raises - safe() turns that into None and the parameter is skipped, so the guard is the missing
    attribute itself, not a type test. Each owner is indexed under every key it can be matched by;
    the tokens collected under a key are what tells a name+type collision (two owners) apart from
    one owner, so a colliding key can be refused instead of mixing two features' parameters."""
    index = {}
    for p in _common.iter_collection(safe(lambda: design.allParameters)):
        owner = safe(lambda p=p: p.createdBy)
        if owner is None:
            continue
        keys = _owner_keys(owner)
        if not keys:
            continue
        row = {"name": safe(lambda p=p: p.name),
               "role": safe(lambda p=p: p.role),
               "expression": safe(lambda p=p: p.expression),
               "value": _common.measured(lambda p=p: p.value)}
        # A tokenless owner still needs a DISTINCT identity in the collision guard: two different
        # tokenless features wearing one name+type must not collapse and answer a row with the
        # UNION of two features' parameters. id(owner) is NOT that identity (measured live:
        # .createdBy mints a fresh proxy per read, and two proxies of DIFFERENT owners reused one
        # address in a single pass). The owner's timeline index is stable per feature; when even
        # that does not read, a per-PARAMETER sentinel refuses the collision - which may also
        # drop a multi-parameter tokenless feature's rows, the safe direction (an absent answer,
        # never two features' parameters merged as one).
        if keys[0][0] == "token":
            token = keys[0][1]
        else:
            tl_index = safe(lambda: owner.timelineObject.index)
            # dNN model-parameter names are design-unique, so the sentinel is per-parameter.
            token = f"tl:{tl_index}" if tl_index is not None else f"anon:{safe(lambda p=p: p.name)}"
        for key in keys:
            entry = index.setdefault(key, {"rows": [], "tokens": set()})
            entry["rows"].append(row)
            entry["tokens"].add(token)
    return index


def _params_for(index, entity):
    """(rows, truncated) - the model parameters `entity` owns, capped; (None, False) when it owns
    none. A name+type key that collected two DIFFERENT owner tokens is skipped, not answered with
    the union of two features' parameters."""
    for key in _owner_keys(entity):
        entry = index.get(key)
        if not entry:
            continue
        if len(entry["tokens"]) > 1:
            continue
        rows = entry["rows"]
        return rows[:_PARAMS_PER_ROW], len(rows) > _PARAMS_PER_ROW
    return None, False


_PARAMS_NOTE = ("Each params[].value is in Fusion internal units (cm / radians) - params[].expression "
                "carries the authored unit. Full records: param_get(include_model_parameters=true).")


def _slice_timeline(design, include_suppressed, group, with_params=False):
    """The ordered parametric timeline, with healthy-row noise dropped (a normal row is
    {index,name,type}; a suppressed/errored row keeps its flags and stands out). with_params adds
    each row's own model parameters (the readable form of a feature's radius/distance/angle)."""
    try:
        timeline = design.timeline
    except Exception as e:
        return None, error(f"This design has no timeline (direct-modeling, or no history): {e}")
    want_group = (group or "").strip()
    items, truncated = [], False
    states, exceptions = {}, []                 # exception-first rollup over the timeline
    param_index = _model_parameters_by_owner(design) if with_params else None
    any_params = False
    try:
        total = timeline.count      # a timeline that cannot be counted is a refusal, not an empty read
        for obj in _common.iter_collection(timeline):
            if len(items) >= _TIMELINE_MAX_ITEMS:
                truncated = True
                break
            summ = _object_summary(obj)
            health = summ.get("health", "healthy")
            states[health] = states.get(health, 0) + 1
            # exception = a feature that FAILED (error/warning health) - not just suppressed (intentional).
            if health in ("error", "warning"):
                exceptions.append({"name": summ.get("name"), "index": summ.get("index"), "health": health})
            if not include_suppressed and summ["is_suppressed"] is True:
                continue
            if want_group and (summ["parent_group"] or "") != want_group:
                continue
            row = terse(summ, _TIMELINE_NOISE)
            if param_index is not None:
                entity = safe(lambda obj=obj: obj.entity)
                prows, ptrunc = _params_for(param_index, entity) if entity is not None else (None, False)
                if prows:
                    row["params"] = prows
                    if ptrunc:
                        row["params_truncated"] = True
                    any_params = True
            items.append(row)
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
               "count": total, "returned": len(items),
               "summary": {"states": states, "exceptions": exceptions},
               "groups": groups, "timeline": items}
    if truncated:
        payload["truncated"] = True
    if any_params:
        payload["params_note"] = _PARAMS_NOTE
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


# ── entity attributes (the tags design_edit_timeline's set_attribute writes) ───────────────────────
_ATTR_MAX_ROWS = 500


def _attribute_rows(design, group, key):
    """(rows, error_text) - every attribute in the design under `group` (and `key` when given),
    as {group, key, value, entity}. Design.findAttributes hands back an AttributeVector - len()/[i],
    never .count/.item(i) - so this walks it by index rather than through iter_collection. An
    Attribute exposes .name (the key), .value, and .parent (the entity it is attached to)."""
    found = safe(lambda: design.findAttributes(group, key))
    if found is None:
        return None, (f"Searching attributes for group '{group}' failed - Design.findAttributes "
                      "raised or returned nothing.")
    total = _common.counted(lambda: len(found))
    if total is None:
        return None, f"The attribute search for group '{group}' returned a result that has no length."
    rows = []
    for i in range(min(total, _ATTR_MAX_ROWS)):
        attr = safe(lambda i=i: found[i])
        if attr is None:
            continue
        parent = safe(lambda: attr.parent)
        entity = {"type": type(parent).__name__ if parent is not None else None}
        parent_name = safe(lambda: parent.name) if parent is not None else None
        if parent_name is not None:
            entity["name"] = parent_name
        # 'group' is the QUERIED group, not a read off the attribute: every row in the vector
        # matched it, so echoing the query says the same thing without a second property read.
        rows.append({"group": group, "key": safe(lambda: attr.name),
                     "value": safe(lambda: attr.value), "entity": entity})
    return {"total": total, "rows": rows}, None


def _slice_attributes(design, group, key):
    """The design's entity attributes under ONE group. The group is REQUIRED - without it this would
    dump every attribute in the design (Fusion itself stores internal ones), which is the flood a
    scoped read exists to avoid."""
    g = (group or "").strip()
    if not g:
        return None, error("include=['attributes'] needs 'attribute_group' - the group to read (the "
                           "same group design_edit_timeline(action='set_attribute') wrote with). "
                           "Leave 'attribute_key' empty to get every key in that group.")
    k = (key or "").strip()
    result, err = _attribute_rows(design, g, k)
    if err:
        return None, error(err)
    out = {"group": g, "key": k or None, "count": result["total"],
           "returned": len(result["rows"]), "attributes": result["rows"]}
    if result["total"] > len(result["rows"]):
        out["truncated"] = True
    return out, None


# ── material / appearance catalog (both slices, one walk in _materials) ────────────────────────────

def _slice_materials(design, library, name_filter, max_results):
    """The PHYSICAL-material catalog: the document's own materials plus a census of every loaded
    library, or one named library's materials. The names model_set_material resolves."""
    return _materials.browse(design, "materials", library, name_filter, max_results)


def _slice_appearances(design, library, name_filter, max_results):
    """The APPEARANCE (cosmetic) catalog, same two levels as the materials slice. A document
    appearance name is what design_configure(action='set_appearance') resolves."""
    return _materials.browse(design, "appearances", library, name_filter, max_results)


def _fingerprint(design):
    """The cheap 'what IS this model' digest for the default: counts of bodies / sketches / components /
    occurrences / joints / parameters, so an agent learns the model's SHAPE without include=tree. The
    field the thin mode+health summary was missing.

    bodies/sketches are DESIGN-WIDE (every component, not just root) via the shared
    _common.design_wide_counts - the same count workspace_orient reports, so the two agree on one
    design instead of this fingerprint under-reporting a multi-component doc whose geometry lives in
    sub-components."""
    root = safe(lambda: design.rootComponent)
    if root is None:
        return None
    body_total, sketch_total = _common.design_wide_counts(design)
    fp = {
        "bodies": body_total,
        "sketches": sketch_total,
        # components = distinct component DEFINITIONS beyond the root (0 = single-component
        # design); occurrences = placed instances. The two differ in any multi-instance assembly,
        # and labeling the instance count "components" misstates the design's shape.
        "components": max(0, safe(lambda: design.allComponents.count, 0) - 1),
        # null, never 0, when neither walk enumerated: root.allOccurrences RAISES on a design holding
        # an unresolved external reference, and safe(read, 0) published that failure as "no
        # occurrences" on a multi-part assembly.
        "occurrences": _common.occurrence_walk(design).total,
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
    "joints": "assembly_get (wiring + health), joint_drive (pose by value)",
    "components": "design_get(include=['tree']) for the full tree; assembly_get for positions",
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
    breadcrumb."""
    return _cam_common.get_cam()[0] is not None


# ── the router ─────────────────────────────────────────────────────────────────────────────────────

def handler(include=None, max_depth: int = 3, component: str = "", tree_bodies: bool = False,
            include_suppressed: bool = True, group: str = "", timeline_params: bool = False,
            library: str = "", name_filter: str = "", max_results: int = 0,
            attribute_group: str = "", attribute_key: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
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
        out["tree"], terr = _slice_tree(design, max_depth, component, bool(tree_bodies))
        if terr:
            return terr
    if "timeline" in inc:
        out["timeline"], tlerr = _slice_timeline(design, include_suppressed, group,
                                                 bool(timeline_params))
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
    if "materials" in inc:
        out["materials"], materr = _slice_materials(design, library, name_filter, max_results)
        if materr:
            return materr
    if "appearances" in inc:
        out["appearances"], apperr = _slice_appearances(design, library, name_filter, max_results)
        if apperr:
            return apperr
    if "attributes" in inc:
        out["attributes"], atterr = _slice_attributes(design, attribute_group, attribute_key)
        if atterr:
            return atterr

    # advertise the slices NOT yet pulled (load-bearing: an un-named flag is invisible to the agent).
    remaining = [s for s in _SLICES if s not in inc]
    if remaining:
        out["note"] = ("Orientation slice. Pull deeper with include=" + str(remaining) +
                       " (e.g. include=['tree'] for the full component tree, ['timeline'] for the "
                       "feature list, ['mode'] for the capability map, ['configurations'] for configs, "
                       "['materials'] or ['appearances'] for the assignable catalog, ['attributes'] "
                       "for entity attributes in one group). "
                       "'max_depth'/'component' scope the tree and 'tree_bodies' adds each node's "
                       "body records (name/handle/solid/visible); 'group'/'include_suppressed' the "
                       "timeline and 'timeline_params' adds each feature's own parameters (a "
                       "fillet's radius); 'library'/'name_filter'/'max_results' the catalog; "
                       "'attribute_group' (required) / 'attribute_key' the attributes.")
    return ok(out)


def _normalize_include(include):
    """Accept include as a list, a comma-string, or a single slice name; -> a list of slice names."""
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


TOOL_DESCRIPTION = (
    "Read the active DESIGN by zoom level (one call for mode + tree + timeline + health + "
    "configs). Default (no 'include'): modelling mode, a shallow component-tree "
    "summary, and timeline_healthy (timeline errors/warnings ONLY - NOT stale references, which appear "
    "as is_out_of_date on tree nodes; the whole-document verdict is workspace_orient.is_healthy). "
    "'include' pulls deeper: 'tree' (full component/occurrence tree; "
    "'max_depth'/'component' scope it, 'tree_bodies' adds each node's body records - name, a handle "
    "any body-taking tool accepts, solid/visible - and upgrades root_bodies from names to the same "
    "records), 'timeline' (the feature list; 'group'/'include_suppressed' "
    "scope it, and 'timeline_params' adds each row's own model parameters with their roles - a "
    "fillet's radius, an extrude's distance), 'mode' (full capability map), "
    "'configurations' (the config table), 'materials' / "
    "'appearances' (the catalog to assign FROM: the document's entries plus a count-only census of "
    "each loaded library; 'library' lists one, 'name_filter'/'max_results' page it), 'attributes' "
    "(entity attributes and what each is attached to; 'attribute_group' required, empty "
    "'attribute_key' = every key in it). The default is "
    "safe to call blind and names its deeper slices."
)

tool = (
    Tool.create_simple(name="design_get", description=TOOL_DESCRIPTION)
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper slices to include: any of tree | timeline | mode | configurations "
                           "| materials | appearances | attributes (a list or comma-string). Omit "
                           "for the orientation slice."})
    .add_input_property("max_depth", {"type": "integer",
            "description": "Tree depth when include=tree (default 3, max 8)."})
    .add_input_property("component", {"type": "string",
            "description": "Start the tree at this component/occurrence name (include=tree)."})
    .add_input_property("tree_bodies", {"type": "boolean",
            "description": "Add each tree node's body records (name, handle, is_solid, visible; "
                           "capped per node) when include=tree; root_bodies also upgrades from "
                           "bare names to the same records. Default false."})
    .add_input_property("include_suppressed", {"type": "boolean",
            "description": "Include suppressed timeline objects when include=timeline (default true)."})
    .add_input_property("group", {"type": "string",
            "description": "Only this timeline group when include=timeline."})
    .add_input_property("timeline_params", {"type": "boolean",
            "description": "Add each timeline row's own model parameters (name/role/expression/"
                           "value). Default false."})
    .add_input_property("library", {"type": "string",
            "description": "One material library's entries, by exact name from the census the "
                           "catalog slices return when this is omitted."})
    .add_input_property("name_filter", {"type": "string",
            "description": "Catalog entries whose name contains this text."})
    .add_input_property("max_results", {"type": "integer",
            "description": "Catalog rows per page (default 50, cap 200); the true total comes too."})
    .add_input_property("attribute_group", {"type": "string",
            "description": "Attribute group to read; required by include=attributes."})
    .add_input_property("attribute_key", {"type": "string",
            "description": "One key within that group; empty means every key in it."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
