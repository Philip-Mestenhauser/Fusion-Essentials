# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: boolean combine of solid BODIES (join / cut / intersect).

  model_combine -> the Combine feature: fuse, subtract, or intersect one or more TOOL bodies into a
                   TARGET body. Optionally keep the tool bodies. WRITES.

The body-on-body boolean that model_extrude/model_revolve's cut/join can't do (those act on a
profile, not existing geometry).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _geom
from . import _inputs
from . import _assert

# BodyRef inputs: target body + tool bodies, each by handle (precise) or name.
_TARGET = _inputs.BodyRef("target", required=True, description="The target body (kept/modified).")
_TOOLS = _inputs.BodyRefList("tools", required=True, description="The tool bodies to combine into the target.")

app = adsk.core.Application.get()

_OPERATIONS = ("join", "cut", "intersect")   # combine needs an existing target; no "new"


def _join_verdict(result_bodies, result_lumps, input_lumps, direct_no_feature,
                  before_bodies, after_bodies, tool_count, keep_tools, new_component):
    """(payload fields, warning sentence) for a JOIN that did not fuse - ({}, "") when nothing the
    call could read says it failed.

    MEASURED (parametric, 2705.0.87): a join of two DISJOINT solids reports success and its feature
    result holds BOTH input bodies - nothing is consumed and nothing merges. So MORE THAN ONE RESULT
    BODY is the nothing-fused verdict, and it is the primary one; the pieces are separate bodies, not
    lumps of one.

    The lump read is the second arm, for the single-result-body case: one body carrying several
    DISCONNECTED lumps also fused nothing. It stays unstated when the count cannot be read.

    Direct mode has no feature whose result bodies could be counted, so the census answers instead: a
    join that consumed its tools drops the host's body count by one per tool body. Any tool body left
    standing did not fuse. The census cannot speak when the tools were KEPT on purpose or the result
    was moved to a new component, so neither case claims a verdict."""
    # keep_tools is excluded from this arm: what a KEPT tool body does to the feature's result set is
    # not measured, so a several-body result there could be the kept copies rather than a failed fuse,
    # and a warning that can fire on a good join is worse than silence.
    if len(result_bodies) > 1 and not keep_tools:
        named = ", ".join(n for n in result_bodies if n)
        return ({"disjoint_join": True, "fused": False, "result_body_count": len(result_bodies)},
                f"WARNING: this join fused NOTHING - it left {len(result_bodies)} separate bodies "
                f"({named}), which is what a join of pieces that do not touch produces.")

    if result_lumps is not None and result_lumps > 1:
        # input_lump_total rides as DATA beside the verdict (null when any input's lumps would not
        # read), so 'nothing fused' is computable from the payload instead of parsed out of prose.
        total_in = (sum(input_lumps) if all(isinstance(n, int) for n in input_lumps) else None)
        fields = {"disjoint_join": True, "input_lump_total": total_in}
        if total_in is not None:
            fields["fused"] = total_in != result_lumps
        return (fields,
                f"WARNING: this join produced a {result_lumps}-lump body - {result_lumps} pieces "
                "that do not touch each other"
                + (f" (nothing fused: the result holds the same {total_in} lumps the inputs did)"
                   if total_in == result_lumps else "") + ".")

    if (direct_no_feature and not keep_tools and not new_component
            and isinstance(before_bodies, int) and isinstance(after_bodies, int)):
        survived = after_bodies - (before_bodies - tool_count)
        if survived > 0:
            return ({"disjoint_join": True, "fused": False, "unfused_tool_bodies": survived},
                    f"WARNING: {survived} of the {tool_count} tool bodies did not fuse into the "
                    "target - it is still standing after the join, so those pieces do not touch.")
    return {}, ""


def handler(target: str = "", tools=None, operation: str = "join",
            keep_tools: bool = False, new_component: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    op_key = (operation or "join").strip().lower()
    if op_key not in _OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: join, cut, intersect.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # target + tools are BodyRef / BodyRefList inputs: resolve each by HANDLE (precise - bodies are
    # auto-named) or by name, with validation. The handler drops its bespoke body-by-name resolver.
    tgt, terr = _TARGET.resolve(target)
    if terr:
        return error(terr)
    tool_bodies, lerr = _TOOLS.resolve(tools)
    if lerr:
        return error(lerr)

    # same-body guard: compared by _common.native_token, never by Python identity alone - the API
    # mints a FRESH wrapper per access (live-measured on face.body/edge.body), so `is` can read False
    # for two references to the same physical body and the guard would never fire. The NATIVE token,
    # because the target and a tool can name one body through DIFFERENT wrappers ('Jaw' - a
    # single-body COMPONENT name - resolving to the native, 'Pin' / 'Jaw:1:Pin' to that occurrence's
    # proxy), whose own tokens differ - in either direction. Same shape as mesh_combine's guard.
    tgt_token = _common.native_token(tgt)
    coll = adsk.core.ObjectCollection.create()
    for b in tool_bodies:
        b_token = _common.native_token(b)
        if b is tgt or (tgt_token and b_token and b_token == tgt_token):
            return error("A tool body is the same as the target - pick distinct bodies.")
        coll.add(b)
    if coll.count == 0:
        return error("No valid tool bodies resolved.")

    # Captured BEFORE the mutation, all of it:
    #  - the census host, resolved ONCE off the TARGET (see _common.census_host). MEASURED: a
    #    cross-component combine is ACCEPTED, and one whose target AND tool both sit in a
    #    sub-component moves nothing the ACTIVE component can see (root 3 -> 3 throughout), so a
    #    census scoped to target_component(design) would be blind to it;
    #  - BOTH signals, because either one alone is blind to a real case. MEASURED: a direct cut that
    #    severed a bar left the body COUNT at 5 -> 5 (the consumed cutter -1 and the new lump +1
    #    cancel) while the target's VOLUME moved 240 -> 96; and a join of two NON-TOUCHING bodies
    #    moved neither, with add() still returning None - which is why the effect, not the None,
    #    decides;
    #  - the NAMES, because a combine CONSUMES its tool bodies - reading b.name afterwards asks a
    #    proxy whose body no longer exists and publishes nulls for bodies that resolved fine.
    host = _common.census_host(tgt, comp)
    before_bodies = _common.body_count(host)
    before_volume = _common.measured(lambda: tgt.volume)
    # The LUMPS every input holds between them - the join's own effect check. A join of bodies that
    # touch collapses lumps; a result still carrying the inputs' total fused nothing, which neither
    # the body count nor the volume can see (both read a clean combine either way).
    input_lumps = [_geom.lump_count(b) for b in [tgt] + list(tool_bodies)]
    target_name = safe(lambda: tgt.name)
    tool_names = [safe(lambda b=b: b.name) for b in tool_bodies]
    host_name = safe(lambda: host.name)
    # Per-tool AABB gap, captured BEFORE the mutation (a cut/intersect CONSUMES its tools, so this
    # evidence is unreadable afterwards). Only the no-effect refusal below reads it: a positive gap
    # PROVES that tool never touched the target.
    tool_gaps = [_geom.aabb_gap(tgt, b) for b in tool_bodies]

    try:
        ci = comp.features.combineFeatures.createInput(tgt, coll)
        ci.operation = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
        # Both flags go through set_verified: a SWIG proxy accepts an assignment it then ignores, and
        # neither flag leaves a trace anywhere else in the result - a swallowed isKeepToolBodies
        # consumes bodies the caller asked to keep and the census below still reads a clean combine.
        for prop, value, label in (("isKeepToolBodies", bool(keep_tools), "keep_tools"),
                                   ("isNewComponent", bool(new_component), "new_component")):
            seterr = _common.set_verified(ci, prop, value, label, "CombineFeatureInput")
            if seterr:
                return error(f"{seterr} Nothing was combined.")
        feature = comp.features.combineFeatures.add(ci)
    except Exception as e:
        return error(f"Combine failed: {e}. (Bodies must overlap for cut/intersect; all bodies "
    "must be solids in the same component.)")
    # MEASURED: combineFeatures.add returns None in a DIRECT design while the boolean LANDS (a join
    # took 2 bodies to 1). With no feature to read, the census below is the verdict; in parametric a
    # None feature is unmeasured as a success and stays an honest error.
    direct_no_feature = _common.direct_feature_absence(design, feature)
    if not feature and not direct_no_feature:
        return error(_common.no_feature_error(design, "Combine"))

    after_bodies = _common.body_count(host)
    if direct_no_feature:
        after_volume = _common.measured(lambda: tgt.volume)
        counted = isinstance(before_bodies, int) and isinstance(after_bodies, int)
        volumed = before_volume is not None and after_volume is not None
        if not counted and not volumed:
            return error("Combine ran in a DIRECT design, which returns no feature object, and "
                         f"neither '{host_name}' body count nor the target's volume could be read "
                         "back - so whether the bodies were combined is UNVERIFIED. Check with "
                         "design_get(include=['tree']) / model_inspect.")
        if not (counted and after_bodies != before_bodies) and not (volumed and after_volume != before_volume):
            # Built from what the reads ACTUALLY returned: a signal that could not be read is named
            # as unread, never reported as an observed sameness.
            seen = [f"'{host_name}' still holds {before_bodies} bodies" if counted
                    else f"'{host_name}' body count could not be read",
                    f"'{target_name}' measures the same volume ({after_volume} cm3)" if volumed
                    else f"'{target_name}' volume could not be read"]
            return error("Combine reported no error but nothing it could measure changed - "
                         + ", and ".join(seen) + ". For cut/intersect the bodies must overlap; "
                         "confirm with design_get(include=['tree']) / model_inspect. "
                         + _common.failed_effect_remedy(design, feature))

    # body-split: a cut/intersect that DISCONNECTS the single target leaves it in >1 piece.
    # CombineFeature.bodies returns the bodies this feature modified/created; the tool bodies were
    # consumed, so for a cut/intersect more than one result body means the target split (there is
    # exactly one target here, unlike an unscoped extrude, so no multi-body ambiguity). This read is
    # SKIPPED in direct mode - there is no feature to read it off, and an empty list there would read
    # as "no disconnection was found" rather than "the check could not run" (the note says which).
    result_objs = [] if direct_no_feature else _common.result_bodies(feature)
    result_bodies = [f["name"] for f in _common.body_facts(result_objs)]
    # split_pieces: the result set the DISCONNECT warning judges. With keep_tools the feature's
    # result bodies INCLUDE the kept tool copies (measured: a kept disjoint tool was counted as a
    # split piece of the target), so kept tool NAMES are excluded before the >1 verdict.
    split_pieces = ([n for n in result_bodies if n not in set(filter(None, tool_names))]
                    if keep_tools else result_bodies)

    # cut/intersect no-effect gate, BOTH modes: a boolean that left the target's volume unmoved
    # removed/kept nothing - the API reports success on a DISJOINT tool (measured: combined:true,
    # volume unchanged, tool consumed). A split target can't be a no-op, so the gate only runs on a
    # single-piece result; an unreadable volume skips it (cannot verify is not "verified same").
    if op_key in ("cut", "intersect") and len(split_pieces) <= 1:
        after_volume_now = _common.measured(lambda: tgt.volume)
        if (before_volume is not None and after_volume_now is not None
                and abs(after_volume_now - before_volume) <= _common.NO_VOLUME_CHANGE_CM3):
            clear = [f"'{n}' ({g:.1f} cm clear of the target)" if g is not None and g > 0 else f"'{n}'"
                     for n, g in zip(tool_names, tool_gaps)]
            rolled_back = False if feature is None else bool(safe(lambda: feature.deleteMe(), False))
            fate = ("the combine feature was rolled back, restoring the tool bodies" if rolled_back
                    else ("the tool bodies were CONSUMED and could not be restored"
                          if not keep_tools else "the tool bodies were kept"))
            return error(f"This {op_key} changed NOTHING - '{target_name}' measures the same volume "
                         f"({before_volume} cm3) after the combine, which is what a tool that does "
                         f"not overlap the target produces. Tools: {', '.join(clear)}; {fate}. "
                         "Move the tool into the target (model_move) and combine again.")

    # The body a JOIN landed in: the feature's single result body, or - in direct mode, where there is
    # no feature to read - the target the join was built on. Its lump count is read back FRESH off
    # that body (an input reference can go invalid once the feature rebuilds).
    joined = result_objs[0] if len(result_objs) == 1 else (tgt if direct_no_feature else None)
    result_lumps = _geom.lump_count(joined) if joined is not None else None

    payload = {
        "combined": True,
        "operation": op_key,
        "target": target_name,
        "tools": tool_names,
        "kept_tools": bool(keep_tools),
        "new_component": bool(new_component),
        "bodies_remaining": after_bodies,
        "note": "Bodies combined. Pair with view_screenshot to view the result.",
    }
    # Direct mode: no feature object, so no name - and no feature.bodies, which is where the
    # disconnection warning below comes from. Flag the omission instead of implying either exists.
    if direct_no_feature:
        payload["no_timeline_feature"] = True
        payload["note"] += (" " + _common.DIRECT_FEATURE_NOTE + " A cut/intersect that DISCONNECTED "
                            "the target cannot be detected here (that check reads the feature's own "
                            "result bodies) - check with design_get(include=['tree']).")
    else:
        payload["feature"] = safe(lambda: feature.name)
    if result_lumps is not None:
        payload["lump_count"] = result_lumps
    if op_key == "join":
        fields, warning = _join_verdict(
            result_bodies, result_lumps, input_lumps, direct_no_feature,
            before_bodies, after_bodies, len(tool_bodies), keep_tools, new_component)
        payload.update(fields)
        if warning:
            payload["note"] += (" " + warning + " " + _common.failed_effect_remedy(design, feature)
                                + " Move the pieces into contact (model_move) and join again.")
    if op_key in ("cut", "intersect") and len(split_pieces) > 1:
        payload["body_split"] = split_pieces
        payload["note"] += (f" WARNING: this {op_key} DISCONNECTED the target into {len(split_pieces)} "
                            f"separate bodies ({', '.join(n for n in split_pieces if n)}) - reference "
                            "each piece by name; a later op assuming one body may hit the wrong piece.")
    return ok(payload)


TOOL_DESCRIPTION = (
"Boolean-combine solid BODIES - the Combine feature. 'target' is the body to keep/modify; "
"'tools' is the body name(s) to combine into it (a list, or comma-separated). 'operation': "
"join (fuse into one) | cut (subtract the tools from the target - e.g. bore a hole with a "
"cylinder body) | intersect (keep only the shared volume). This is the body-on-body boolean "
"that model_extrude/model_revolve's cut/join can't do (those act on a profile). Bodies are "
"referenced by handle or name within the active component."
)

combine_tool = (
    Tool.create_simple(name="model_combine", description=TOOL_DESCRIPTION)
    .add_input_property("target", _TARGET.schema())
    .add_input_property("tools", _TOOLS.schema())
    .add_input_property(*_inputs.boolean_op(options=("join", "cut", "intersect"), default="join").as_property())
    .add_input_property("keep_tools", {"type": "boolean",
            "description": "Keep the tool bodies after combining (default false = consume)."})
    .add_input_property("new_component", {"type": "boolean",
            "description": "Put the combined result in a NEW component instead of modifying in place (default false)."})
    .strict_schema()
)
combine_item = Item.create_tool_item(tool=combine_tool, write="write", handler=handler, run_on_main_thread=True,
                                     postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(combine_item)
