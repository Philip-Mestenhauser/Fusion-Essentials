# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_compare_operations - diff two operations' CAM parameters (a relational read over two named ops,
not a domain disclosure, so it stays its own tool rather than a cam_get slice)."""

from collections import Counter

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
import adsk.cam

from ._common import CM_TO_UNIT, iter_collection, native_identity, ok, error, read_flag, safe
# The surface-group reader and its per-group record come from the SUBSTRATE, not from the tool that
# writes them, so this read and that write cannot drift apart.
from ._cam_common import (AVOID_GROUPS_PARAM, STRATEGY_PAIR_NOTE, avoid_groups, clamp_rows,
                          get_cam, group_record, op_settled, op_state_facts, resolve_cam_node,
                          strategy_pair)
from . import _inputs
# The selection parameter tables and the loop/side vocabularies themselves, from the one place
# cam_select_geometry routes and spells them.
from .cam_select_geometry import (_CURVE_PARAM_CANDIDATES, _DIRECT_PARAM, _LOOP_TYPE, _SIDE_TYPE,
                                  _SURFACE_TARGET_PARAM)


_DIFFERENCES_CAP = 200   # two operations can differ across hundreds of CAM parameters; bound the rows
_DIFFERENCES_CEILING = 400   # every row crosses the wire; a caller cannot lift the cap past this

# Every OWN property of the curve-selection subclasses on this build, read through safe(): a class
# not carrying one omits it, so a row states what THIS selection answered. The base's error/warning
# channels are left out - they report the last apply, not what is selected.
_SELECTION_PROPS = ("isOpen", "isOpenAllowed", "isReverted",
                    "extensionType", "extensionMethod",
                    "startExtensionLength", "endExtensionLength",
                    "loopType", "sideType", "isSelectingSamePlaneFaces",
                    "isSetupModelSelected", "silhouetteTolerance",
                    "areHolesIncluded", "minimumHoleDiameter", "minimumCornerRadius",
                    "maximumCornerRadius", "minimumPocketDepth", "maximumPocketDepth")


def _spellings(*pairs):
    """{enum value: spelling} over the pairs this build's enum actually answers - a member it does
    not carry drops out, and a value with no spelling publishes as the raw int."""
    return {value: name for value, name in pairs if value is not None}


# The subset carrying a LENGTH: Fusion answers these in internal cm, so each is scaled into 'units'
# and the row states which unit it is in.
_LENGTH_PROPS = frozenset({"startExtensionLength", "endExtensionLength", "silhouetteTolerance",
                           "minimumHoleDiameter", "minimumCornerRadius", "maximumCornerRadius",
                           "minimumPocketDepth", "maximumPocketDepth"})

# A chain's extension capping and its method - the two enums that make one 2D contour differ from
# another on the SAME chain. Each member is named literally so the harness's enum scan reaches the
# family and live_api_facts pins its int members.
_EXTENSION_TYPE = _spellings(
    (safe(lambda: adsk.cam.ExtensionTypes.BoundaryExtensionType), "boundary"),
    (safe(lambda: adsk.cam.ExtensionTypes.DistanceExtensionType), "distance"))
_EXTENSION_METHOD = _spellings(
    (safe(lambda: adsk.cam.ExtensionMethods.TangentExtensionMethod), "tangent"),
    (safe(lambda: adsk.cam.ExtensionMethods.ClosestBoundaryExtensionMethod), "closest_boundary"),
    (safe(lambda: adsk.cam.ExtensionMethods.ParallelExtensionMethod), "parallel"))

# The object-set parameters: the direct family's own spellings plus every surface role.
_OBJECT_SET_PARAMS = tuple(dict.fromkeys(
    [nm for names in _DIRECT_PARAM.values() for nm in names] + list(_SURFACE_TARGET_PARAM.values())))

# The enum-valued properties, decoded so a row states a spelling rather than an ordinal.
_ENUM_PROPS = ("loopType", "sideType", "extensionType", "extensionMethod")

_SELECTION_ROWS_CAP = 20   # one row per selection; a chain-per-contour op grows this with the model
_ENTITY_BASES_CAP = 100    # one basis per selected entity; a surface set grows this with the model

# The basis published for an entity that answered native_identity - the only reading a claim about
# the geometry may rest on; the other basis a row can carry is a bounding-box centre.
_NATIVE_BASIS = "native identity"

# Said of a row whose two selections BOTH read a native identity per entity and whose identities do
# not match - the only reading that supports a claim about the geometry.
_PICKS_DIFFER = "the selected entities resolve to different geometry"

# Said on a 0/0 answer whose entity readings stopped at their bound: the sets past it were never
# compared, so the match is not established.
_TRUNCATED_PICKS_NOTE = (
    " 0 differences, and entity_bases_truncated names the set(s) whose entity readings stopped at "
    "the first {cap} - what those sets hold past that bound was not compared, so they are NOT "
    "shown to match. Narrow the compare to the operations' own parameters instead.")

# Said only on a 0/0 answer where geometry WAS read - the one a caller reads as 'these are the same'.
_NOTHING_DIFFERED_NOTE = (
    " 0 differences is what these reads OPENED matching: the parameter expressions, each selection "
    "set's counts and entity readings, and the surface groups' flags and modes. A property no "
    "selection answered is not compared.")

# The other 0/0: no selection parameter on either operation answered, so the geometry counts are 0
# because nothing was read, not because the two match.
_NO_GEOMETRY_NOTE = (
    " NO selection set answered on either operation - the geometry counts are absent evidence "
    "about what the two CUT, not agreement.")


# The refusal while either named operation is still generating. The geometry half below reads the
# selection objects off both operations, and one such compare on a document whose operations read
# isGenerating true ended the Fusion process.
_GENERATING_REFUSAL = (
    "{n} of the 2 named operations still {verb} generating to do ({names}), so nothing was "
    "compared. This read walks the selection objects on both operations, and one such call on a "
    "document still regenerating ended the Fusion process. Poll cam_get_status until "
    "completed=true, then compare.")


def _generating(pairs):
    """The names of the named operations with generating LEFT to do, in the order they were named.
    op_settled is the judge cam_get_status settles completed on, so the refusal's remedy is
    reachable: the isGenerating flag alone stays true over an operation whose state already
    answered."""
    rows = []
    for op, name in pairs:
        facts = op_state_facts(op)
        if facts.get("is_generating") and not op_settled(facts):
            rows.append(name)
    return rows


def _reverse_enum(family, table):
    """{enum value: the key cam_select_geometry spells it by} over `family`, whose members that tool
    already names literally - a value with no key publishes as the raw int."""
    return _spellings(*((safe(lambda m=member: getattr(family, m)), key)
                        for key, member in table.items()))


def _enum_spellings(prop):
    """{enum value: spelling} for one enum-valued selection property, or {} where none decodes."""
    if prop == "loopType":
        return _reverse_enum(adsk.cam.LoopTypes, _LOOP_TYPE)
    if prop == "sideType":
        return _reverse_enum(adsk.cam.SideTypes, _SIDE_TYPE)
    return _EXTENSION_TYPE if prop == "extensionType" else _EXTENSION_METHOD


def handler(operation_a: str = "", operation_b: str = "",
                                max_results: int = _DIFFERENCES_CAP, units: str = "mm") -> dict:
    """Diff the CAM parameters of two operations (by name) to show what differs."""
    if not (operation_a or "").strip() or not (operation_b or "").strip():
        return error("Provide both 'operation_a' and 'operation_b' (operation names).")
    units_key = (units or "mm").strip().lower()
    inv = CM_TO_UNIT.get(units_key)      # cm -> the caller's units, for every selection LENGTH
    if inv is None:
        return error(f"Unknown units '{units}'. Valid: {', '.join(sorted(CM_TO_UNIT))}.")
    cam, err = get_cam()
    if err:
        return error(err)

    node_a, err_a = resolve_cam_node(cam, operation_a, kinds=("operation",), label="operation")
    if err_a:
        return error(err_a)
    node_b, err_b = resolve_cam_node(cam, operation_b, kinds=("operation",), label="operation")
    if err_b:
        return error(err_b)
    op_a, op_b = node_a.obj, node_b.obj

    busy = _generating(((op_a, safe(lambda: op_a.name) or operation_a),
                        (op_b, safe(lambda: op_b.name) or operation_b)))
    if busy:
        return error(_GENERATING_REFUSAL.format(n=len(busy), names=", ".join(busy),
                                                verb="has" if len(busy) == 1 else "have"))

    params_a, titles_a = _operation_params(op_a)
    params_b, titles_b = _operation_params(op_b)

    all_keys = sorted(set(params_a) | set(params_b))
    differences = []
    same_count = 0
    for k in all_keys:
        a = params_a.get(k)
        b = params_b.get(k)
        if a == b:
            same_count += 1
        else:
            differences.append({"parameter": k,
        "title": titles_a.get(k) or titles_b.get(k) or k,
        "operation_a": a if k in params_a else "(not present)",
        "operation_b": b if k in params_b else "(not present)"})

    total = len(differences)
    cap = clamp_rows(max_results, _DIFFERENCES_CAP, _DIFFERENCES_CEILING)
    differences_out = differences[:cap]
    truncated = total > len(differences_out)

    # The GEOMETRY half: two ops can carry identical parameters and cut different material, because
    # what is selected lives on the selection objects, not in the parameter expressions.
    geom_a, picks_a = _geometry_facts(op_a, inv)
    geom_b, picks_b = _geometry_facts(op_b, inv)
    geometry_differences = []
    geometry_same = 0
    for k in sorted(set(geom_a) | set(geom_b)):
        a, b = geom_a.get(k), geom_b.get(k)
        verdict = (_picks_verdict(picks_a.get(k) or [], picks_b.get(k) or [])
                   if k in geom_a and k in geom_b else None)
        if a == b and verdict is None:
            geometry_same += 1
            continue
        row = {"parameter": k,
               "operation_a": a if k in geom_a else "(not present)",
               "operation_b": b if k in geom_b else "(not present)"}
        if verdict:
            row["entity_verdict"] = verdict
        geometry_differences.append(row)

    pair_a, pair_b = strategy_pair(op_a), strategy_pair(op_b)
    out = {
        "operation_a": safe(lambda: op_a.name),
        "operation_b": safe(lambda: op_b.name),
    "tool_a": _op_tool_desc(op_a),
    "tool_b": _op_tool_desc(op_b),
    # Both vocabularies per side: the 'strategy' difference row below carries the internal id, which
    # is not a name cam_create_operation takes.
    "strategy_a": pair_a["strategy"], "strategy_name_a": pair_a["strategy_name"],
    "strategy_b": pair_b["strategy"], "strategy_name_b": pair_b["strategy_name"],
    "same_parameter_count": same_count,
    "difference_count": total,
    "differences": differences_out,
    "truncated": truncated,
    "same_geometry_count": geometry_same,
    "geometry_difference_count": len(geometry_differences),
    "geometry_differences": geometry_differences,
    "note": STRATEGY_PAIR_NOTE,
    }
    # Names the unit every selection LENGTH above is scaled into; the counts carry no unit.
    if geom_a or geom_b:
        out["geometry_units"] = units_key
    if truncated:
        out["note"] += (f" differences was capped at {cap} of {total}; raise max_results to see "
                        "the rest.")
    # No set answering at all is disclosed WHATEVER the parameter diff said: two ops differing only
    # in feed still cut different material through geometry this read never opened.
    if not (geom_a or geom_b):
        out["note"] += _NO_GEOMETRY_NOTE
    elif not total and not geometry_differences:
        # The other zero: the sets were read and MATCHED - unless a reading stopped at its bound,
        # which no difference row carries here because none is emitted.
        capped = _capped_sets(geom_a, geom_b)
        out["geometry_properties_read"] = list(_SELECTION_PROPS)
        if capped:
            out["entity_bases_truncated"] = capped
            out["note"] += _TRUNCATED_PICKS_NOTE.format(cap=_ENTITY_BASES_CAP)
        else:
            out["note"] += _NOTHING_DIFFERED_NOTE
    return ok(out)


def _capped_sets(*sides):
    """The selection parameters whose entity readings stopped at _ENTITY_BASES_CAP on either side -
    what a 0/0 answer has to disclose, since it emits no row to carry the flag."""
    return sorted({k for facts in sides for k, v in facts.items()
                   if isinstance(v, dict) and v.get("entity_bases_truncated")})


def _operation_params(op):
    """({name: expression}, {name: title}) for one operation's CAM parameters - keyed by NAME,
    which is scope-unique on an op where a parameter TITLE is not."""
    values, titles = {}, {}
    try:
        params = op.parameters
        for p in iter_collection(params):
            name = safe(lambda: p.name)
            if not name:
                continue
            values[name] = safe(lambda: p.expression)
            titles[name] = safe(lambda: p.title) or name
    except Exception:
        pass
    return values, titles


def _selection_row(sel, inv):
    """ONE selection's answered properties: a LENGTH scaled out of internal cm by `inv`, and an enum
    decoded to its member spelling - a value this build's enum does not answer stays the raw int."""
    row = {}
    for prop in _SELECTION_PROPS:
        answered = safe(lambda prop=prop, sel=sel: getattr(sel, prop))
        if answered is None:
            continue
        if prop in _LENGTH_PROPS:
            row[prop] = round(float(answered) * inv, 6)
        elif prop in _ENUM_PROPS:
            row[prop] = _enum_spellings(prop).get(answered, answered)
        else:
            row[prop] = answered
    return row


def _box_centre(entity, inv):
    """One entity's bounding-box centre in the CALLER's units (`inv` is cm -> those units), or
    None - the fallback reading where no identity answers. It names a PLACE, not an entity."""
    box = safe(lambda: entity.boundingBox)
    centre = safe(lambda: tuple(
        round((getattr(box.minPoint, a) + getattr(box.maxPoint, a)) / 2.0 * inv, 6)
        for a in ("x", "y", "z")))
    return None if centre is None else f"box{centre}"


def _entity_reading(entity, inv):
    """(what this entity IS for comparison, the basis a row publishes for it) - native_identity,
    the PHYSICAL-entity key this repo compares on: it normalizes an occurrence PROXY through
    nativeObject, whose token differs from the proxy's, and pairs that token with its source
    document, which two x-refs' byte-identical tokens would otherwise merge."""
    ident = native_identity(entity)
    if ident is not None:
        return ident, _NATIVE_BASIS
    centre = _box_centre(entity, inv)
    return (centre, centre) if centre is not None else (None, "unread")


def _picks_verdict(a_reads, b_reads):
    """The sentence a difference row states about WHICH entities the two selections hold, or None
    where the readings establish nothing - unequal counts are a difference of their own, and a
    centroid reading names a place, so only identities on BOTH sides support the claim. The two
    identity MULTISETS are compared, so a shared entity cannot cover an unshared one."""
    if not a_reads or len(a_reads) != len(b_reads):
        return None
    if {b for _v, b in a_reads} | {b for _v, b in b_reads} != {_NATIVE_BASIS}:
        return None
    if Counter(v for v, _b in a_reads) == Counter(v for v, _b in b_reads):
        return None
    return _PICKS_DIFFER


def _curve_facts(p, inv):
    """What ONE curve-selection parameter holds: the counts cam_select_geometry reads back off an
    applied selection, which entities those counts are OF, and each selection's own answered
    properties. None when it does not read."""
    pv = safe(lambda: p.value)
    cs = safe(lambda: pv.getCurveSelections()) if pv is not None else None
    if cs is None:
        return None, None
    count = safe(lambda: cs.count, 0) or 0
    rows, picked, paths, segments = [], [], 0, 0
    for i in range(count):
        sel = safe(lambda i=i: cs.item(i))
        if sel is None:
            continue
        # outputGeometry is a Curve3DPathVector and value a BaseVector - plain iterables with no
        # count/item, so they are listed rather than walked by iter_collection.
        out = safe(lambda sel=sel: list(sel.outputGeometry))
        if out is not None:
            paths += len(out)
            segments += sum((safe(lambda q=q: q.count, 0) or 0) for q in out)
        value = safe(lambda sel=sel: list(sel.value))
        if value is not None:
            picked.extend(value)
        if len(rows) < _SELECTION_ROWS_CAP:
            rows.append(_selection_row(sel, inv))
    facts = {"selections": count, "curve_paths": paths, "curve_segments": segments,
             "entities": len(picked), "properties": rows}
    if count > len(rows):
        facts["properties_truncated"] = True   # absent = every selection's row is here
    return facts, picked


def _object_set_facts(p, _inv):
    """What ONE direct/surface set parameter holds: how many CAD objects and which ones, or None
    where the list did not read."""
    pv = safe(lambda: p.value)
    value = safe(lambda: list(pv.value)) if pv is not None else None
    return (None, None) if value is None else ({"entities": len(value)}, value)


def _surface_group_facts(op):
    """What the operation's SURFACE GROUPS hold: how many, whether the set would take a WRITE, and
    per group its face count, over-holes flag and machining mode - the same reader
    cam_select_geometry writes through, with its write gate off. A set refusing a write still
    answers what it holds, so only an operation carrying no such parameter reads None here."""
    _pv, groups, gerr = avoid_groups(op, writable=False)
    count = None if gerr else safe(lambda: groups.count)
    if count is None:
        return None
    p = safe(lambda: op.parameters.itemByName(AVOID_GROUPS_PARAM))
    return {"groups": count, "editable": read_flag(lambda: p.isEditable),
            "rows": [group_record(safe(lambda i=i: groups.item(i))) for i in range(count)]}


def _geometry_facts(op, inv):
    """({parameter: what that selection parameter holds}, {parameter: the reading per selected
    entity}) for every geometry-selection parameter this operation carries - read through the same
    properties cam_select_geometry writes. The readings are the cross-operation compare's, and each
    entity's own BASIS is published so a fallback is never mistaken for an identity."""
    facts, picks = {}, {}
    for names, reader in ((_CURVE_PARAM_CANDIDATES, _curve_facts),
                          (_OBJECT_SET_PARAMS, _object_set_facts)):
        for nm in names:
            p = safe(lambda nm=nm: op.parameters.itemByName(nm))
            if p is None:
                continue
            record, entities = reader(p, inv)
            if record is None:
                continue
            entities = list(entities or [])
            reads = [_entity_reading(e, inv) for e in entities[:_ENTITY_BASES_CAP]]
            record["entity_bases"] = [b for _v, b in reads]
            if len(entities) > _ENTITY_BASES_CAP:
                record["entity_bases_truncated"] = True   # absent = every entity is read here
            facts[nm], picks[nm] = record, reads
    groups = _surface_group_facts(op)
    if groups is not None:
        facts[AVOID_GROUPS_PARAM] = groups
    return facts, picks


def _op_tool_desc(op):
    try:
        t = op.tool
        return t.description if t else None
    except Exception:
        return None


TOOL_DESCRIPTION = (
    "Compare two CAM operations by name: which parameters and which geometry selections differ, "
    "with the value on each side."
)

tool = (
    Tool.create_with_string_input(
        name="cam_compare_operations",
        description=TOOL_DESCRIPTION,
        input_param_name="operation_a",
        input_param_description="Operation name (from cam_get).",
    )
    .add_input_property("operation_b", {"type": "string",
            "description": "Operation name (from cam_get)."})
    .add_input_property("max_results", {"type": "integer", "description":
            f"Max {_DIFFERENCES_CEILING}."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
