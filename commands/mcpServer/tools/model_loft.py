# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: loft a body through ORDERED sections, optionally shaped by rails or a
centerline. WRITES; isSolid and each end condition set are read back off the feature, never assumed.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _geom
from . import _inputs
from . import _assert
from . import _sketch_detail
from ._surface_common import _OPERATION_KEYS, _feature_operation, _result_body_report

app = adsk.core.Application.get()

# scope_input: a {sketch, profile_index} element or a '<sketch>/<type>:<index>' section addresses a
# sketch BY NAME, and Fusion numbers sketches per component from 1, so a name two components carry
# is refused with the remedy spelled as this tool's own 'component' input.
_LOFT_PROFILES = _inputs.LoftSectionList("profiles", required=True, scope_input="component")
_LOFT_RAILS = _inputs.GeometryHandleList("rails", require="any", required=False)
_LOFT_CENTERLINE = _inputs.GeometryHandle("centerline", require="any", required=False)
_LOFT_START = _inputs.LoftEndCondition("start")
_LOFT_END = _inputs.LoftEndCondition("end")
_RAIL_CONTINUITY = _inputs.Choice("rail_continuity", ["g0", "g1", "g2"])

# The LoftRailEdgeConditions member each rail_continuity value writes.
_RAIL_MEMBERS = {"g0": "G0LoftRailEdgeCondition", "g1": "G1LoftRailEdgeCondition",
                 "g2": "G2LoftRailEdgeCondition"}

# A section's endCondition objectType, after its 'adsk::fusion::' namespace -> the condition word.
_END_READ = {"LoftFreeEndCondition": "free", "LoftTangentEndCondition": "tangent",
             "LoftSmoothEndCondition": "smooth", "LoftDirectionEndCondition": "direction",
             "LoftPointSharpEndCondition": "point_sharp",
             "LoftPointTangentEndCondition": "point_tangent"}

# The GUIDES' own scope. Measured: a loft hosted on its profiles' component accepts a rail owned by a
# sketch in ANOTHER component, so one scope over both would leave that build with no legal call.
_GUIDE_SCOPE = _sketch_detail.component_scope("guide_component", narrows="rails/centerline")


def _resolve_sketch_curve(design, raw, label, guide_component=""):
    """(SketchCurve, None) for a '<sketch>/<type>:<index>' ref, or (None, error) - resolved inside
    'guide_component', which scopes the GUIDES alone: a rail may live in another component than the
    sections (measured: a loft hosted on its profiles takes a rail owned elsewhere)."""
    curve, err = _inputs.resolve_sketch_entity(design, raw, guide_component, _GUIDE_SCOPE[0])
    return (None, f"{label} {err}") if err else (curve, None)


def _resolve_guides(design, raw, kind, label, guide_component=""):
    """(entities, error) for rails/centerline: each item is a find_geometry handle OR a sketch-curve
    ref, resolved item by item so the two spellings can be mixed in one call."""
    # LoftCenterLineOrRails.addRail takes a SketchCurve (measured: addRail(SketchArc) returns a
    # LoftCenterLineOrRail), but find_geometry mints handles for BRep faces/edges/vertices only -
    # so a spine drawn before any body exists needs the second spelling, '<sketch>/<type>:<index>'.
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    if any(_inputs.sketch_entity_ref(i) for i in items):
        out = []
        for i in items:
            if _inputs.sketch_entity_ref(i):
                ent, err = _resolve_sketch_curve(design, i, label, guide_component)
            else:
                ent, err = kind.resolve(i) if not isinstance(kind, _inputs.GeometryHandleList) \
                    else _inputs.GeometryHandle.resolve(kind, i)
            if err:
                return None, err
            out.append(ent)
        return out, None
    return kind.resolve(raw)


def _cut_check_bodies(comp):
    """The solid bodies a cut/intersect loft can act on: every solid directly in the feature's host
    component, resolved ONCE before the mutation and re-read afterwards (_geom.volumes keys on
    id()). A loft takes no participant-body scoping, so there is no narrower sample."""
    return [b for b in _common.iter_collection(safe(lambda: comp.bRepBodies))
            if safe(lambda b=b: b.isSolid)]


def _end_specs(secs, start, end, is_closed):
    """({'start': condition or None, 'end': condition or None}, error) - each legal on its section."""
    specs = {}
    for kind, raw, index in ((_LOFT_START, start, 0), (_LOFT_END, end, len(secs) - 1)):
        cond, err = kind.resolve(raw)
        if err:
            return None, err
        if cond and is_closed:
            return None, (f"'{kind.name}' conditions an end of an open loft, and is_closed=true "
                          "leaves this one no end - drop one of the two.")
        refusal = kind.legal_on(cond, secs[index][1], index) if cond else ""
        if refusal:
            return None, refusal
        specs[kind.name] = cond or None
    return specs, None


def _rail_member(raw, rail_ents, center_ent):
    """((the LoftRailEdgeConditions member to write, its key) or None when omitted, error)."""
    if raw in (None, ""):
        return None, None
    key, err = _RAIL_CONTINUITY.resolve(raw)
    if err:
        return None, err
    if center_ent is not None or not rail_ents:
        return None, ("'rail_continuity' sets the continuity along B-Rep edge 'rails', and this "
                      "loft has none.")
    bad = [i for i, r in enumerate(rail_ents)
           if safe(lambda r=r: isinstance(r, adsk.fusion.BRepEdge)) is not True]
    if bad:
        return None, (f"'rail_continuity' is set on B-Rep edge rails, and rails[{bad[0]}] is a "
                      f"{type(rail_ents[bad[0]]).__name__} - pass find_geometry edge handles, or "
                      "drop 'rail_continuity'.")
    family = getattr(adsk.fusion, "LoftRailEdgeConditions", None)
    member = getattr(family, _RAIL_MEMBERS[key], None) if family is not None else None
    if member is None:
        return None, f"This Fusion build carries no LoftRailEdgeConditions.{_RAIL_MEMBERS[key]}."
    return (member, key), None


def _apply_end(section, cond, side):
    """Write one end condition at the documented defaults (weight 1, 0 deg): '' when it took."""
    vi = adsk.core.ValueInput
    setters = {
        "free": lambda: section.setFreeEndCondition(),
        "tangent": lambda: section.setTangentEndCondition(vi.createByReal(1.0)),
        "smooth": lambda: section.setSmoothEndCondition(vi.createByReal(1.0)),
        "direction": lambda: section.setDirectionEndCondition(vi.createByString("0 deg"),
                                                              vi.createByReal(1.0)),
        "point_sharp": lambda: section.setPointSharpEndCondition(),
        "point_tangent": lambda: section.setPointTangentEndCondition(vi.createByReal(1.0)),
    }
    try:
        took = setters[cond]()
    except Exception as e:
        return f"The {side} section would not take a {cond} end: {e}"
    if took is not True:
        return f"The {side} section's {cond} end setter answered {took!r}, so that end was not set."
    return ""


def _end_read(feature, index):
    """The condition word section `index` of the built feature reads, or None."""
    kind = safe(lambda: feature.loftSections.item(index).endCondition.objectType)
    if not isinstance(kind, str):
        return None
    tail = kind.rpartition("::")[2]
    return _END_READ.get(tail, tail)


def _end_parameters(feature, last):
    """{'start_weight': 'dN', ...} - the model parameters the built ends carry, each one read."""
    out = {}
    for side, index in (("start", 0), ("end", last)):
        cond = safe(lambda i=index: feature.loftSections.item(i).endCondition)
        for what in ("weight", "angle"):
            name = safe(lambda c=cond, w=what: getattr(c, w).name)
            if isinstance(name, str) and name:
                out[f"{side}_{what}"] = name
    return out


def _read_back(design, feature, secs, specs, rails):
    """(payload additions, unverified names, error) off the ends and rails the feature reads."""
    # `rails` is (the member written, its rail_continuity key, the rail count), or None.
    name, remedy = safe(lambda: feature.name), _common.failed_effect_remedy(design, feature)
    ends = {"start": _end_read(feature, 0), "end": _end_read(feature, len(secs) - 1)}
    unverified = []
    for side, cond in specs.items():
        if cond is None:
            continue
        if ends[side] is None:
            unverified.append(f"{side}_end")
        elif ends[side] != cond:
            return None, None, (f"Loft '{name}' was built, but its {side} section reads a "
                                f"{ends[side]} end, not the {cond} end asked for. " + remedy)
    extra = {"section_kinds": [kind for _e, kind, _src in secs], "ends": ends}
    params = _end_parameters(feature, len(secs) - 1)
    if params:
        extra["model_parameters"] = params
    if rails is not None:
        member, key, count = rails
        landed = [safe(lambda i=i: feature.centerLineOrRails.item(i).edgeCondition)
                  for i in range(count)]
        wrong = [i for i, v in enumerate(landed) if v is not None and v != member]
        if wrong:
            return None, None, (f"Loft '{name}' was built, but rail {wrong[0]} reads edgeCondition "
                                f"{landed[wrong[0]]}, not the {key} asked for. " + remedy)
        extra["rail_continuity"] = key if None not in landed else None
        if None in landed:
            unverified.append("rail_continuity")
    return extra, unverified, None


def handler(profiles=None, rails=None, centerline="", operation="new",
            as_surface=None, is_closed=None, component: str = "",
            guide_component: str = "", start=None, end=None, rail_continuity=None) -> dict:
    """Loft a body through an ORDERED list of sections, optionally shaped by rails OR a centerline."""
    op_key = (operation or "new").strip().lower()
    if op_key not in _OPERATION_KEYS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    secs, perr = _LOFT_PROFILES.resolve(profiles, component)
    if perr:
        return error(perr)
    if not secs or len(secs) < 2:
        return error(f"Loft needs at least 2 sections (got {len(secs) if secs else 0}).")
    specs, serr = _end_specs(secs, start, end, is_closed)
    if serr:
        return error(serr)

    has_rails = rails not in (None, "", [])
    has_centerline = bool((centerline or "").strip()) if isinstance(centerline, str) else centerline not in (None, [])
    if has_rails and has_centerline:
        return error("centerLineOrRails takes a centerline OR rails, not both.")

    rail_ents = []
    if has_rails:
        rail_ents, rerr = _resolve_guides(design, rails, _LOFT_RAILS, "rails", guide_component)
        if rerr:
            return error(rerr)
    center_ent = None
    if has_centerline:
        center_ent, cerr = _resolve_guides(design, centerline, _LOFT_CENTERLINE, "centerline",
                                           guide_component)
        if cerr:
            return error(cerr)
        if isinstance(center_ent, list):
            center_ent = center_ent[0] if center_ent else None
    rail_spec, rerr = _rail_member(rail_continuity, rail_ents, center_ent)
    if rerr:
        return error(rerr)

    # Host the loft on the profiles' OWNING component: handing another component's native profile to
    # features.createInput raises 'InternalValidationError : bSet', so the feature - and its body
    # - is built on the sketch's owner, not the active component.
    root = _inputs.profile_host_component(secs[0][2], None, target_component(design))
    op = _feature_operation(op_key)
    try:
        loft_input = root.features.loftFeatures.createInput(op)
    except Exception as e:
        return error(f"Could not start loft: {e}")

    # Add sections IN ORDER - this ordering is the whole game (do NOT sort/reorder).
    try:
        added = [loft_input.loftSections.add(ent) for ent, _kind, _src in secs]
    except Exception as e:
        return error(f"Could not add loft sections: {e}")
    for side, section in (("start", added[0]), ("end", added[-1])):
        if specs[side] is not None:
            eerr = _apply_end(section, specs[side], side)
            if eerr:
                return error(eerr)

    # centerline XOR rails on the LoftCenterLineOrRails object.
    rail_objs = []
    try:
        if center_ent is not None:
            loft_input.centerLineOrRails.addCenterLine(center_ent)
        else:
            rail_objs = [loft_input.centerLineOrRails.addRail(r) for r in rail_ents]
    except Exception as e:
        return error(f"Could not set loft centerline/rails: {e}")
    if rail_spec is not None:
        for rail in rail_objs:
            cerr = _common.set_verified(rail, "edgeCondition", rail_spec[0],
                                        f"rail_continuity={rail_spec[1]}", "LoftCenterLineOrRail")
            if cerr:
                return error(cerr)

    if as_surface is not None:
        try:
            loft_input.isSolid = not bool(as_surface)
        except Exception as e:
            return error(f"Could not set loft solid/surface mode: {e}")

    # isClosed has no independent effect the result bodies show, so the set-then-read-back on the
    # input is what proves it took (a SWIG proxy accepts an unknown property name silently). Two
    # sections are enough for a closed loft - measured, so there is no >=3 guard.
    if is_closed is not None:
        cerr = _common.set_verified(loft_input, "isClosed", bool(is_closed),
                                    f"is_closed={bool(is_closed)}", "LoftFeatureInput")
        if cerr:
            return error(cerr)

    # cut/intersect MATERIAL evidence: the volumes the operation must move, sampled BEFORE the add.
    # A loft whose swept shape misses the body still reports a healthy feature, so only this
    # before/after pair can say material actually changed.
    check_bodies = _cut_check_bodies(root) if op_key in ("cut", "intersect") else []
    vol_before = _geom.volumes(check_bodies)
    # A join is told "grew a body" from "made a second one" by the host's body NAMES before the add.
    bodies_before = _common.component_body_names(root) if op_key == "join" else None

    try:
        feature = root.features.loftFeatures.add(loft_input)
    except Exception as e:
        # Lead with the API's OWN message - a cut through air says "No target body" (measured).
        clause = (" The API answers 'No target body' for a cut/intersect whose path meets no body."
                  if op_key in ("cut", "intersect") else "")
        return error(f"Loft failed: {e}.{clause}")
    if not feature:
        return error(_common.no_feature_error(design, "Loft"))

    volume_delta_cm3 = None
    # These live outside the census branch: the empty-result gate below reads them to decide whether
    # material movement was PROVEN, and an un-run census must read as "proved nothing", not as an
    # absent variable. readable=False is exactly that case - no body's volume read at both ends.
    delta, readable, consumed = 0.0, False, []
    if check_bodies:
        delta, readable = _geom.volume_delta(check_bodies, vol_before)
        # A body whose volume read BEFORE and reads unreadable now was consumed whole - a real effect
        # that contributes no delta, so it must not be counted as "nothing moved".
        consumed = [b for b in check_bodies
                    if vol_before.get(id(b)) is not None and _geom.signed_volume(b) is None]
        if readable:
            volume_delta_cm3 = round(delta, 6)
        if readable and not consumed and abs(delta) < _common.NO_VOLUME_CHANGE_CM3:
            where = safe(lambda: root.name) or "the host component"
            return error(f"Loft reported success but this {op_key} changed nothing - every solid "
                         f"body in '{where}' measures the volume it had before and none was "
                         "consumed, so the lofted shape does not overlap any of them. Check the "
                         "profiles bracket the target body (an 'intersect' whose target lies "
                         "entirely INSIDE the lofted solid also reads this way). "
                         + _common.failed_effect_remedy(design, feature))

    body_names, _flags = _result_body_report(feature)
    # An empty result set is a failure except for a cut/intersect PROVEN to have moved material: a
    # body consumed whole, or a volume delta that READ and cleared the no-change band. A census
    # whose volumes never read buys no exemption.
    moved = bool(consumed) or (readable and abs(delta) >= _common.NO_VOLUME_CHANGE_CM3)
    if not body_names and not moved:
        return error("Loft reported success but the feature owns no result body - nothing was "
                     "built. " + _common.failed_effect_remedy(design, feature))
    # read_flag, not safe(): an unreadable isSolid is not a surface, and the note below is worded
    # off this value rather than off the falsiness of an absent read.
    is_solid = _common.read_flag(lambda: feature.isSolid)
    if is_solid is True:
        shape = "Result is a SOLID."
    elif is_solid is False:
        shape = "Result is a SURFACE - pair with model_stitch/surface_thicken to close it."
    else:
        shape = ("The feature's isSolid flag could not be read back, so whether the result is a "
                 "solid or a surface is UNVERIFIED.")
    extra, unverified = {}, []
    if (any(kind != "profile" for _e, kind, _src in secs) or rail_spec is not None
            or any(s is not None for s in specs.values())):
        rails = (rail_spec[0], rail_spec[1], len(rail_ents)) if rail_spec is not None else None
        extra, unverified, berr = _read_back(design, feature, secs, specs, rails)
        if berr:
            return error(berr)
    note = ("Lofted through %d sections in order. " % len(secs)) + shape
    join_clause = _common.join_new_body_clause(op_key, bodies_before, body_names)
    if join_clause:
        note += " " + join_clause
    if "model_parameters" in extra:
        note += (" 'model_parameters' names each end's weight (and a direction end's angle) - "
                 "param_set one to change that end.")
    if "edge" in extra.get("section_kinds", ()):
        note += " model_measure_continuity reads the seams at its edge sections."
    payload = {
        "lofted": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "profiles_count": len(secs),
        "rails_count": len(rail_ents),
        "has_centerline": center_ent is not None,
        "is_solid": is_solid,
        "result_bodies": body_names,
        **extra,
        "note": note,
    }
    unverified = (["is_solid"] if is_solid is None else []) + unverified
    if unverified:
        payload["unverified"] = unverified
    if is_closed is not None:
        payload["is_closed"] = bool(is_closed)
    # Published only where the before/after pair was READABLE: a null here would read as "no material
    # moved" rather than "the measurement could not be taken", so the key is simply absent instead.
    if volume_delta_cm3 is not None:
        payload["volume_delta_cm3"] = volume_delta_cm3
    return ok(payload)


TOOL_DESCRIPTION = (
"Loft through ordered sections; model_stitch closes a surface loft."
)

tool = (
    Tool.create_simple(name="model_loft", description=TOOL_DESCRIPTION)
    .add_input_property("profiles", _LOFT_PROFILES.schema())
    .add_input_property("rails", _LOFT_RAILS.schema())
    .add_input_property("centerline", _LOFT_CENTERLINE.schema())
    .add_input_property(*_inputs.boolean_op(default="new", description="").as_property())
    .add_input_property("as_surface", {"type": "boolean"})
    .add_input_property("is_closed", {"type": "boolean"})
    .add_input_property(*_sketch_detail.COMPONENT_SCOPE)
    .add_input_property(*_GUIDE_SCOPE)
    .add_input_property(*_LOFT_START.as_property())
    .add_input_property(*_LOFT_END.as_property())
    .add_input_property(*_RAIL_CONTINUITY.as_property())
    .add_required_input("profiles")
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.FeatureHealthy()],
                             verification=Verification(
                                 kind="inline", rung="geometry",
                                 evidence_test="tests/unit/test_model_loft.py::TestLoft"
                                               "::test_cut_that_moves_no_volume_is_an_error"))


def register_tool():
    register(item)
