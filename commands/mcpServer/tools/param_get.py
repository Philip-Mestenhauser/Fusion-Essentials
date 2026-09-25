# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read the active design's parameters - user parameters by default, model/feature ones on request."""

from itertools import islice

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from ._cam_common import clamp_rows
from ._param_common import _expression_identifiers, _owner_facts, _param_summary

_MAX_PARAMS = 2000
_ROWS_CAP = 300              # rows emitted per read, either list
_TRACE_CAP = 200             # closure members one trace reads, whatever depth is asked for
_TRACE_DEPTH_DEFAULT = 1     # hops of dependents RETURNED; the closure is always read to the cap
_TRACE_ROWS_DEFAULT = 50
_TRACE_ROWS_MAX = 200
_CONSUMER_SKETCHES_CAP = 20  # sketches listed under sketch_consumers, and features under each
_CONSUMER_FEATURES_CAP = 20

# The naming library parts use for their own parameters. The filter below is a NAME test and nothing
# more, so a parameter the modeller happened to name adsk_something is skipped by it too.
_GENERATED_PREFIX = "adsk_"

_OWNER_NOTE = ("Model parameter rows carry their maker: 'owner' (its name), 'owner_type', "
               "'owner_sketch' when the owner lives in a sketch, and 'role' - the slot the "
               "parameter fills on that owner. A key that did not read is absent from the row.")

_NARROW_NOTE = "Narrow with name='<one parameter>' or favorites_only=true."

_TRACE_NOTE = ("'dependents' holds the rows within trace_depth, grouped by maker; 'reach' counts "
               "the whole closure, 'unlinked' the members no expression link reached, 'sketches' "
               "the closure only. trace_depth widens the rows, max_results pages them. Scope: the "
               "trace follows parameter EXPRESSION references - geometry a feature consumes "
               "without naming a parameter (a projected edge) is not read here.")


def _is_generated(nm):
    """True when the parameter's name starts with the library prefix, case-insensitively."""
    return isinstance(nm, str) and nm.lower().startswith(_GENERATED_PREFIX)


# ── the dependency trace (trace=true): what one parameter references, what it reaches ─────────────

def _owner_sketch(p):
    """The Sketch a model parameter's owner IS, or the sketch that owner lives in; else None."""
    owner = safe(lambda: p.createdBy)
    if owner is None:
        return None
    if type(owner).__name__ == "Sketch":
        return owner
    return safe(lambda: owner.parentSketch)


def _owner_timeline_index(p):
    """The timeline index of a model parameter's owner, read through its sketch when the owner is a
    dimension rather than a timeline item itself."""
    owner = safe(lambda: p.createdBy)
    if owner is None:
        return None
    index = _common.counted(lambda: owner.timelineObject.index)
    if index is not None:
        return index
    sketch = _owner_sketch(p)
    return _common.counted(lambda: sketch.timelineObject.index) if sketch is not None else None


def _trace_row(p):
    """One REFERENCED parameter: name and expression, plus a model parameter's owner and where it
    sits - the dependencies row, which has no hop count of its own."""
    row = {"name": safe(lambda: p.name), "expression": safe(lambda: p.expression)}
    owner = _owner_facts(p)
    row.update(owner)
    if owner:
        index = _owner_timeline_index(p)
        if index is not None:
            row["owner_timeline_index"] = index
    return row


def _dependency_rows(target, cap):
    """(rows, truncated, upstream) - the parameters the target's OWN expression names, found in the
    closure dependencyParameters answers with; 'upstream' is that closure's whole size."""
    refs = _expression_identifiers(safe(lambda: target.expression))
    upstream = _common.counted(lambda: target.dependencyParameters.count)
    rows, truncated, scanned = [], False, 0
    for p in _common.iter_collection(safe(lambda: target.dependencyParameters)):
        if scanned >= cap:
            truncated = True
            break
        scanned += 1
        if (safe(lambda p=p: p.name) or "") in refs:
            rows.append(_trace_row(p))
    return rows, truncated, upstream


def _owner_identity(owner, facts, index, name):
    """The key ONE maker is grouped and counted under: its native identity, else the facts published
    for it - never Python identity, which mints a fresh proxy per read."""
    ident = _common.native_identity(owner)
    if ident is not None:
        return ident
    if not facts.get("owner"):
        # A nameless maker whose identity did not read is told apart from another by nothing, so it
        # keys PER PARAMETER: two rows for one maker beats one row asserting a shared maker.
        return ("unidentified", facts.get("owner_type"), index, name)
    return (facts.get("owner"), facts.get("owner_type"), index)


def _traced_item(p, name, expression):
    """One closure member with everything the hop placement, the rows and the reach read off it."""
    facts = _owner_facts(p)
    base = {"param": p, "name": name, "expression": expression, "hops": None, "facts": facts,
            "refs": _expression_identifiers(expression)}
    if not facts:
        return dict(base, sketch_key=None, index=None, owner_key=None)
    sketch = _owner_sketch(p)
    index = _owner_timeline_index(p)
    return dict(
        base, index=index,
        sketch_key=((_common.native_identity(sketch) or safe(lambda: sketch.name))
                    if sketch is not None else None),
        owner_key=_owner_identity(safe(lambda: p.createdBy), facts, index, name),
    )


def _closure(target, cap):
    """(items, truncated) - what dependentParameters answers with, each member read once."""
    # MEASURED: that list is the whole TRANSITIVE closure, not the direct edges - a chain A -> B ->
    # C answers A.dependentParameters [B, C] - so the hops below are placed from the expressions.
    items, truncated = [], False
    for p in _common.iter_collection(safe(lambda: target.dependentParameters)):
        if len(items) >= cap:
            truncated = True
            break
        nm = safe(lambda p=p: p.name)
        if isinstance(nm, str) and nm:
            items.append(_traced_item(p, nm, safe(lambda p=p: p.expression)))
    return items, truncated


def _place_hops(target_name, items):
    """Hop counts over the EXPRESSION references inside the closure: hop 1 names the target, hop 2
    names a hop-1 member, and so on. A member naming none of them keeps hops null."""
    frontier, hop = {target_name}, 0
    while frontier:
        hop += 1
        landed = set()
        for it in items:
            if it["hops"] is None and (it["refs"] & frontier):
                it["hops"] = hop
                landed.add(it["name"])
        frontier = landed


def _reach(items, truncated, upstream, downstream):
    """The whole closure in a few numbers, whatever the model's size: how many parameters at each
    hop, how many no expression link reached, the distinct makers and sketches, the depth, and the
    size of each closure the design answered with."""
    per_hop, features, sketches, unlinked = {}, set(), set(), 0
    for it in items:
        if it["hops"] is None:
            unlinked += 1
        else:
            key = str(it["hops"])
            per_hop[key] = per_hop.get(key, 0) + 1
        if it["sketch_key"] is not None:
            sketches.add(it["sketch_key"])
        elif it["owner_key"] is not None:
            features.add(it["owner_key"])
    hops = [it["hops"] for it in items if it["hops"] is not None]
    return {"rows_per_hop": per_hop, "unlinked": unlinked, "upstream": upstream,
            "downstream": downstream, "features": len(features), "sketches": len(sketches),
            "max_hops": max(hops or [0]), "truncated": truncated}


def _dependent_rows(items, depth, cap):
    """(rows, parameters, truncated) - the placed members within `depth`, nearest hop first: ONE row
    per maker carrying the parameters it owns, one row per user parameter, capped at `cap` rows.
    Past the direct ring a row is a census, so only hop-1 rows carry an expression."""
    rows, by_owner, used, truncated = [], {}, [], False
    placed = sorted((it for it in items if it["hops"] is not None), key=lambda it: it["hops"])
    for it in placed:
        if it["hops"] > depth:
            continue
        p, hops = it["param"], it["hops"]
        if not it["facts"]:
            if len(rows) >= cap:
                truncated = True
                continue
            row = {"name": it["name"], "hops": hops}
            if hops == 1:
                row["expression"] = it["expression"]
            rows.append(row)
            used.append(p)
            continue
        row = by_owner.get(it["owner_key"])
        if row is None:
            if len(rows) >= cap:
                truncated = True
                continue
            # 'role' is the PARAMETER's slot on this maker, so it rides each entry, not the row.
            row = {k: v for k, v in it["facts"].items() if k != "role"}
            if it["index"] is not None:
                row["owner_timeline_index"] = it["index"]
            row["parameters"] = []
            by_owner[it["owner_key"]] = row
            rows.append(row)
        entry = {"name": it["name"], "hops": hops}
        if "role" in it["facts"]:
            entry["role"] = it["facts"]["role"]
        if hops == 1:
            entry["expression"] = it["expression"]
        row["parameters"].append(entry)
        used.append(p)
    return rows, used, truncated


def _consumed_sketches(entity):
    """The sketches a feature takes its shape from: its 'profile' (extrude/revolve/sweep, one
    Profile or a collection of them), else a loft's section entities."""
    # Only 'profile' and a loft's sections are read. MEASURED: reading SweepFeature.path right after
    # the sweep was created raised '3 : Didn't roll editing feature back' and rolled the whole
    # script back, so a sweep contributes its profile's sketch alone.
    prof = safe(lambda: entity.profile)
    if prof is None:
        sections = _common.iter_collection(safe(lambda: entity.loftSections))
        found = [safe(lambda s=s: s.entity.parentSketch) for s in sections]
        return [s for s in found if s is not None]
    one = safe(lambda: prof.parentSketch)
    if one is not None:
        return [one]
    # MEASURED: an extrude built from several profiles answers an ObjectCollection whose OWN
    # parentSketch raises - each item carries it instead.
    found = [safe(lambda p=p: p.parentSketch) for p in _common.iter_collection(prof)]
    return [s for s in found if s is not None]


def _features_by_sketch(design):
    """{sketch identity -> [feature row]} from ONE timeline walk - the features whose inputs name a
    parent sketch. Keyed on native identity: a feature reaches a sketch through the occurrence PROXY
    (its own token, measured), and the parameter's maker is the native."""
    index = {}
    for obj in _common.iter_collection(safe(lambda: design.timeline)):
        ent = safe(lambda o=obj: o.entity)
        if ent is None:
            continue
        row = {"name": safe(lambda e=ent: e.name), "type": type(ent).__name__,
               "component": safe(lambda e=ent: e.parentComponent.name),
               "timeline_index": _common.counted(lambda o=obj: o.index)}
        for key in {_common.native_identity(s) for s in _consumed_sketches(ent)}:
            if key is not None:
                index.setdefault(key, []).append(row)
    return index


def _sketch_consumers(design, parameters):
    """([{sketch, features}], truncated) for each DISTINCT sketch owning the traced parameter or one
    it reaches - the sketch list and each feature list both capped."""
    owners, truncated = {}, False
    for p in parameters:
        sketch = _owner_sketch(p)
        key = _common.native_identity(sketch) if sketch is not None else None
        if key is None or key in owners:
            continue
        if len(owners) >= _CONSUMER_SKETCHES_CAP:
            truncated = True
            continue
        owners[key] = safe(lambda s=sketch: s.name)
    if not owners:
        return [], truncated
    by_sketch = _features_by_sketch(design)
    rows = []
    for key, nm in owners.items():
        features = by_sketch.get(key, [])
        if len(features) > _CONSUMER_FEATURES_CAP:
            truncated = True
        rows.append({"sketch": nm, "features": features[:_CONSUMER_FEATURES_CAP]})
    return rows, truncated


def _trace(design, target, depth, cap):
    """(payload, error) - what one parameter references, the rows within `depth`, and the whole
    closure in numbers. A closure that will not answer its size is an error, never an empty list."""
    name = safe(lambda: target.name)
    downstream = _common.counted(lambda: target.dependentParameters.count)
    if downstream is None:
        return None, error(f"The parameters following '{name}' could not be read, so this trace "
                           "would publish an empty list as the answer 'nothing follows it'. The "
                           "plain read without trace still answers.")
    dependencies, deps_cut, upstream = _dependency_rows(target, _TRACE_CAP)
    items, closure_cut = _closure(target, _TRACE_CAP)
    _place_hops(name, items)
    rows, used, rows_cut = _dependent_rows(items, depth, cap)
    # The TARGET rides with the parameters whose rows were RETURNED: tracing a sketch dimension
    # itself is the case where the only sketch in the trace is the traced parameter's own.
    consumers, consumers_cut = _sketch_consumers(design, [target] + used)
    out = {"dependencies": dependencies, "dependents": rows,
           "reach": _reach(items, closure_cut, upstream, downstream),
           "sketch_consumers": consumers, "note": _TRACE_NOTE}
    if deps_cut:
        out["dependencies_truncated"] = True
    if rows_cut:
        out["dependents_truncated"] = True
    if consumers_cut:
        out["sketch_consumers_truncated"] = True
    return out, None


def handler(name: str = "", include_model_parameters: bool = False,
            include_generated: bool = False, favorites_only: bool = False,
            trace: bool = False, trace_depth: int = _TRACE_DEPTH_DEFAULT,
            max_results: int = _TRACE_ROWS_DEFAULT) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry).")

    want = (name or "").strip()
    if trace and not want:
        return error("'trace' traces ONE parameter, so it needs 'name' - pass the parameter to "
                     "trace, or drop 'trace' to list the table.")

    # Single named parameter (search user first, then all).
    if want:
        target = None
        try:
            target = design.userParameters.itemByName(want)
        except Exception:
            target = None
        if not target:
            try:
                for p in design.allParameters:
                    if (safe(lambda: p.name) or "") == want:
                        target = p
                        break
            except Exception:
                pass
        if not target:
            return error(f"Parameter not found: '{name}'.")
        one = {"parameter": _param_summary(target)}
        if trace:
            try:
                depth = max(1, int(trace_depth))
            except (TypeError, ValueError):
                depth = _TRACE_DEPTH_DEFAULT
            traced, terr = _trace(design, target, depth,
                                  clamp_rows(max_results, _TRACE_ROWS_DEFAULT, _TRACE_ROWS_MAX))
            if terr:
                return terr
            one.update(traced)
        return ok(one)

    # Collection. The AUTHORED set by default: rows whose name starts with the library prefix are
    # counted rather than listed.
    user_params, kept, generated = [], 0, 0
    try:
        ups = design.userParameters
        total = ups.count       # an uncountable collection is a refusal, not an empty read
        walked = min(total, _MAX_PARAMS)
        for p in islice(_common.iter_collection(ups), walked):
            nm = safe(lambda p=p: p.name)
            if not include_generated and _is_generated(nm):
                generated += 1
                continue
            if favorites_only and _common.read_flag(lambda p=p: p.isFavorite) is not True:
                continue
            kept += 1
            if len(user_params) < _ROWS_CAP:
                user_params.append(_param_summary(p))
    except Exception as e:
        return error(f"Could not read user parameters: {e}")

    payload = {"user_parameter_count": total, "matched": kept, "returned": len(user_params),
               "user_parameters": user_params}
    notes = []
    if walked < total:
        # The counts below cover the rows the walk reached, not the whole table.
        payload["walk_truncated"] = True
        notes.append(f"The walk stopped at {walked} of {total} user parameters, so 'matched' and "
                     "'generated_skipped' count only those.")
    if generated:
        payload["generated_skipped"] = generated
        notes.append(f"{generated} of {walked} user parameters were skipped: their names start "
                     f"{_GENERATED_PREFIX} (the naming library parts use). include_generated=true "
                     "lists them.")
    if kept > len(user_params):
        payload["truncated"] = True
        notes.append(f"Listed {len(user_params)} of {kept} matching rows. " + _NARROW_NOTE)

    if include_model_parameters:
        model_params, model_kept, examined = [], 0, 0
        model_walk_truncated = False
        try:
            for p in islice(design.allParameters, _MAX_PARAMS + 1):
                examined += 1
                if examined > _MAX_PARAMS:
                    model_walk_truncated = True
                    break
                nm = safe(lambda p=p: p.name)
                if not isinstance(nm, str) or not nm:
                    return error("Could not read a parameter name while classifying model parameters.")
                try:
                    is_user = design.userParameters.itemByName(nm)
                except Exception as e:
                    return error(f"Could not classify parameter '{nm}' as user or model: {e}")
                if is_user:
                    continue
                model_kept += 1
                if len(model_params) < _ROWS_CAP:
                    model_params.append(_param_summary(p))
        except Exception as e:
            return error(f"Could not read model parameters: {e}")
        payload["model_parameter_count"] = model_kept
        payload["model_parameters"] = model_params
        if model_walk_truncated:
            payload["model_walk_truncated"] = True
            notes.append(f"The model parameter walk stopped at {_MAX_PARAMS} of at least {examined} rows, so "
                         "'model_parameter_count' covers only the observed subset.")
        if model_kept > len(model_params):
            payload["model_parameters_truncated"] = True
            notes.append(f"Listed {len(model_params)} of {model_kept} model parameters. "
                         + _NARROW_NOTE)
        # Advertised only when a row actually carries owner keys - a note describing keys that are
        # not there would send a caller looking for them.
        if any("owner_type" in row for row in model_params):
            notes.append(_OWNER_NOTE)

    if notes:
        payload["note"] = " ".join(notes)
    return ok(payload)


TOOL_DESCRIPTION = (
"Read the active design's parameters - name, expression, value, unit, comment. The user parameters "
"by default; 'name' fetches one; trace=true reads what a parameter drives, direct hop plus a "
"'reach' count. Change one with param_set."
)

tool = (
    Tool.create_simple(name="param_get", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string",
            "description": "One parameter to fetch."})
    .add_input_property("include_model_parameters", {"type": "boolean"})
    .add_input_property("include_generated", {"type": "boolean",
            "description": f"Also list the {_GENERATED_PREFIX}*-named parameters."})
    .add_input_property("favorites_only", {"type": "boolean"})
    .add_input_property("trace", {"type": "boolean",
            "description": "Needs 'name'."})
    .add_input_property("trace_depth", {"type": "integer",
            "description": "Hops of dependents to return."})
    .add_input_property("max_results", {"type": "integer",
            "description": f"Dependent rows, max {_TRACE_ROWS_MAX}."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
