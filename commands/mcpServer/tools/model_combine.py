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
from . import _inputs
from . import _assert

# BodyRef inputs: target body + tool bodies, each by handle (precise) or name.
_TARGET = _inputs.BodyRef("target", required=True, description="The target body (kept/modified).")
_TOOLS = _inputs.BodyRefList("tools", required=True, description="The tool bodies to combine into the target.")

app = adsk.core.Application.get()

_OPERATIONS = ("join", "cut", "intersect")   # combine needs an existing target; no "new"


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

    # same-body guard: compared by entityToken, never by Python identity alone - the API mints a
    # FRESH wrapper per access (live-measured on face.body/edge.body), so `is` can read False for two
    # references to the same physical body and the guard would never fire. Same shape as
    # mesh_combine's guard, which already compares tokens.
    tgt_token = safe(lambda: tgt.entityToken)
    coll = adsk.core.ObjectCollection.create()
    for b in tool_bodies:
        b_token = safe(lambda b=b: b.entityToken)
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
    target_name = safe(lambda: tgt.name)
    tool_names = [safe(lambda b=b: b.name) for b in tool_bodies]
    host_name = safe(lambda: host.name)

    try:
        ci = comp.features.combineFeatures.createInput(tgt, coll)
        ci.operation = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
        ci.isKeepToolBodies = bool(keep_tools)
        ci.isNewComponent = bool(new_component)
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
    result_bodies = []
    if not direct_no_feature:
        fb = safe(lambda: feature.bodies)
        for i in range(safe(lambda: fb.count, 0) if fb else 0):
            result_bodies.append(safe(lambda i=i: fb.item(i).name))

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
    if op_key in ("cut", "intersect") and len(result_bodies) > 1:
        payload["body_split"] = result_bodies
        payload["note"] += (f" WARNING: this {op_key} DISCONNECTED the target into {len(result_bodies)} "
                            f"separate bodies ({', '.join(n for n in result_bodies if n)}) - reference "
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
