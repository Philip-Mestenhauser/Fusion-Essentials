# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lifecycle for the three assembly relations - rigid group, motion link, assembly constraint:
suppress/unsuppress, delete, and reverse or re-value a motion link. Editing a rigid group's
MEMBERSHIP after creation raises at EVERY timeline marker position (measured on Fusion 2704.1.39),
so set_occurrences refuses up front and names the delete-and-recreate path instead.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, timeline_health
from . import _common
from . import _inputs
from . import _relations

app = adsk.core.Application.get()

# Which actions each relation kind supports. Only a motion link carries a direction and a pair of
# coupled values; all three carry isSuppressed and deleteMe (measured on the installed bindings).
# set_occurrences stays in the rigid group's set so the caller meets the measured refusal below
# rather than an unknown verb.
_KIND_ACTIONS = {
    "rigid_group": ("suppress", "unsuppress", "delete", "set_occurrences"),
    "motion_link": ("suppress", "unsuppress", "delete", "reverse", "set_values"),
    "constraint": ("suppress", "unsuppress", "delete"),
}
_ACTIONS = ("suppress", "unsuppress", "delete", "set_occurrences", "reverse", "set_values")

_KIND = _inputs.Choice(
    "kind", list(_relations.KINDS), required=True,
    description="Which relation to act on - the three live in separate namespaces.")
_ACTION = _inputs.Choice(
    "action", list(_ACTIONS), required=True,
    description="What to do: suppress/unsuppress and delete apply to every kind; reverse and "
                "set_values re-couple a motion link; set_occurrences is refused by the platform "
                "and answers with the delete-and-recreate path.")
_OCCURRENCES = _inputs.OccurrenceRefList(
    "occurrences", description="set_occurrences: the members you want - the action is refused and "
                               "names the path that works.")

_RATIO_TOLERANCE = 1e-6

# Measured on Fusion 2704.1.39: RigidGroup.setOccurrences raises at EVERY marker position - with
# the marker at the end or immediately AFTER the group, and with it rolled BEFORE the group (the
# position the binding prose instructs). A same-membership no-op raises too, and the members read
# back unchanged after each raise, so post-creation membership editing is unusable on this build.
_SET_OCCURRENCES_REFUSAL = (
    "action='set_occurrences' is refused: this Fusion build will not edit a rigid group's members "
    "after it is created. Measured at every marker position - with the marker at the end or just "
    "after the group, setOccurrences raises '3 : Cannot be edited before rolling back'; with the "
    "marker rolled to just BEFORE the group, it raises '3 : Provided input paths or alignments are "
    "not valid.' Even a same-membership call raises. To change the members: "
    "assembly_edit_relations(kind='rigid_group', name=..., action='delete'), then "
    "assembly_rigid_group with the occurrences you want. Nothing was changed."
)

# What suppression/deletion OBSERVABLY did, per kind - the flag this call read back, plus the tool
# that re-creates the kind. What a suppressed relation does to the parts it relates is not measured.
_CREATE_TOOL = {"rigid_group": "assembly_rigid_group", "motion_link": "joint_motion_link",
                "constraint": "assembly_constrain"}


def _health_delta(before, design):
    """Timeline features newly in error since `before` - a suppress/delete can break a downstream
    feature that consumed what this relation held."""
    after, warnings, _total = timeline_health(design)
    return [n for n in after if n not in before], warnings


def _suppress_note(kind, state):
    """What the suppress call OBSERVED, per kind - the flag read back, and where to look for the
    effect. What suppression does to the members/coupling is not measured, so it is not claimed."""
    if kind == "motion_link":
        return (f"isSuppressed now reads {state} on the motion link, which couples two joints' "
                "motion. Drive one member (joint_drive) and read the partner back to see what "
                "changed.")
    if kind == "constraint":
        return (f"isSuppressed now reads {state} on the assembly constraint - the flag is what this "
                "call confirmed; what suppression does to its geometric relationships is not "
                "measured here.")
    return (f"isSuppressed now reads {state} on the rigid group; it stays in the timeline until "
            "action='delete'. Re-read positions with assembly_get to see the effect.")


def _do_suppress(design, obj, kind, name, suppressed):
    label = _relations.kind_label(kind)
    was = safe(lambda: obj.isSuppressed)
    errors_before, _w, _t = timeline_health(design)
    try:
        obj.isSuppressed = bool(suppressed)
    except Exception as e:
        return error(f"Could not set isSuppressed on {label} '{name}': {e}")
    now = safe(lambda: obj.isSuppressed)
    if bool(now) != bool(suppressed):
        return error(f"Setting isSuppressed={bool(suppressed)} on {label} '{name}' did not take - "
                     f"it reads {now}.")
    new_errors, warnings = _health_delta(errors_before, design)
    out = {"kind": kind, "name": name, "is_suppressed": bool(now),
           # null, not False, when the prior flag could not be read - an unreadable state is not "off".
           "was_suppressed": (None if was is None else bool(was)),
           "note": _suppress_note(kind, bool(now))}
    if new_errors:
        out["timeline_errors_after"] = new_errors
        out["note"] = (f"The change left {len(new_errors)} feature(s) in error: "
                       + ", ".join(new_errors) + ". Set the opposite action to restore it.")
    elif warnings:
        out["timeline_warnings"] = warnings
    return ok(out)


def _do_delete(design, obj, kind, name):
    label = _relations.kind_label(kind)
    errors_before, _w, _t = timeline_health(design)
    try:
        did = obj.deleteMe()
    except Exception as e:
        return error(f"Deleting {label} '{name}' failed: {e}")
    if not did:
        return error(f"Fusion declined to delete {label} '{name}' (deleteMe returned false) - it is "
                     "still in the design.")
    # Re-list the kind: a deleted relation must be GONE from the collection, not merely reported so.
    # relation_names drops an unreadable name, so `remaining` is the READABLE count of this kind.
    remaining = _relations.relation_names(design, kind)
    if any((n or "").lower() == name.lower() for n in remaining):
        return error(f"deleteMe reported success but {label} '{name}' is still listed - it was not "
                     "deleted.")
    new_errors, warnings = _health_delta(errors_before, design)
    out = {"deleted": True, "kind": kind, "name": name, "remaining": len(remaining),
           "note": (f"The {label} is no longer listed (re-read to confirm); 'remaining' counts the "
                    f"readable {label}s left. Undo in Fusion if unintended - the API cannot restore "
                    f"it. Re-create one with {_CREATE_TOOL[kind]}.")}
    if new_errors:
        out["timeline_errors_after"] = new_errors
        out["note"] += (f" WARNING: {len(new_errors)} feature(s) are now in error: "
                        + ", ".join(new_errors) + ".")
    elif warnings:
        out["timeline_warnings"] = warnings
    return ok(out)


def _do_reverse(ml, name):
    was = safe(lambda: ml.isReversed)
    if was is None:
        return error(f"Motion link '{name}' does not report isReversed, so there is no direction to "
                     "flip.")
    want = not bool(was)
    try:
        ml.isReversed = want
    except Exception as e:
        return error(f"Could not set isReversed on motion link '{name}': {e}")
    now = safe(lambda: ml.isReversed)
    if bool(now) != want:
        return error(f"Setting isReversed={want} on motion link '{name}' did not take - it reads "
                     f"{now}.")
    return ok({"kind": "motion_link", "name": name, "reversed": bool(now),
               "was_reversed": bool(was),
               "note": "The linked joints now move in the opposite sense relative to each other. "
                       "Drive ONE member (joint_drive) and read the partner back."})


def _do_set_values(ml, name, ratio):
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return error(f"'ratio' must be a number (got {ratio!r}).")
    if r == 0:
        return error("'ratio' must be non-zero (a 0 ratio links no motion).")
    # setMotionData re-states WHICH degrees of freedom are coupled, so the link's existing
    # motionOne/motionTwo are read back and passed through - picking a DOF here would silently
    # re-couple a different pair.
    m1 = safe(lambda: ml.motionOne)
    m2 = safe(lambda: ml.motionTwo)
    if m1 is None or m2 is None:
        return error(f"Motion link '{name}' does not report both coupled motions (motionOne/"
                     "motionTwo), so its values cannot be re-set without guessing which degrees of "
                     "freedom it links.")
    # setMotionData carries isReversed, so the SIGN of ratio SETS the direction outright: a positive
    # ratio clears an existing reversal. was_reversed reports what that overwrote.
    was_rev = safe(lambda: ml.isReversed)
    reversed_link = r < 0
    mag = abs(r)
    v1 = adsk.core.ValueInput.createByReal(1.0)
    v2 = adsk.core.ValueInput.createByReal(mag)
    try:
        did = ml.setMotionData(m1, v1, m2, v2, reversed_link)
    except Exception as e:
        return error(f"setMotionData on motion link '{name}' failed: {e}. (The platform refuses a "
                     "coupling it cannot solve; the link is unchanged.)")
    if not did:
        return error(f"Fusion declined to re-value motion link '{name}' (setMotionData returned "
                     "false) - its ratio is unchanged.")
    one = safe(lambda: ml.valueOne.value)
    two = safe(lambda: ml.valueTwo.value)
    now_rev = safe(lambda: ml.isReversed)
    if one is None or two is None:
        return error(f"setMotionData reported success on '{name}' but its valueOne/valueTwo "
                     "parameters cannot be read back, so nothing confirms the new ratio.")
    if not one:
        return error(f"setMotionData reported success on '{name}' but its valueOne parameter reads "
                     f"{one} - a zero first value is no coupling at all.")
    got = two / one
    if abs(got - mag) > max(_RATIO_TOLERANCE, _RATIO_TOLERANCE * mag):
        return error(f"setMotionData reported success on '{name}' but its parameters read "
                     f"{one}:{two} (a ratio of {got}), not {mag} - the ratio did not take.")
    if bool(now_rev) != reversed_link:
        return error(f"setMotionData reported success on '{name}' but it reads isReversed="
                     f"{bool(now_rev)}, not {reversed_link} - the direction did not take.")
    return ok({"kind": "motion_link", "name": name, "ratio": r, "value_one": one, "value_two": two,
               "reversed": bool(now_rev),
               "was_reversed": (None if was_rev is None else bool(was_rev)),
               "note": "Coupling re-valued: joint_two moves |ratio| per unit of joint_one, and the "
                       "SIGN of ratio SETS the direction - so a positive ratio CLEARS an existing "
                       "reversal (was_reversed reports what it overwrote). To flip the direction "
                       "without re-valuing, use action='reverse'. Drive ONE member (joint_drive) "
                       "and read the partner back."})


def handler(kind: str = "", name: str = "", action: str = "", occurrences=None,
            include_children: bool = False, ratio=None) -> dict:
    """See TOOL_DESCRIPTION."""
    values, verr = _inputs.resolve_inputs([_KIND, _ACTION], {"kind": kind, "action": action})
    if verr:
        return verr
    kind, action = values["kind"], values["action"]

    allowed = _KIND_ACTIONS[kind]
    if action not in allowed:
        return error(f"action='{action}' does not apply to a {_relations.kind_label(kind)} - it "
                     f"supports: {', '.join(allowed)}.")
    # Refused BEFORE anything is resolved or rolled: the platform refuses the edit itself, so
    # resolving the group and its occurrences first would only add failure modes ahead of the
    # teaching. Kept in the action set so the agent is taught, not met with an unknown verb.
    if action == "set_occurrences":
        return error(_SET_OCCURRENCES_REFUSAL)

    design = _common.design()
    if not design:
        return error("No active design with components.")

    obj, _comp, rerr = _relations.find_relation(design, kind, name)
    if rerr:
        return error(rerr)
    nm = safe(lambda: obj.name) or (name or "").strip()

    if action in ("suppress", "unsuppress"):
        return _do_suppress(design, obj, kind, nm, action == "suppress")
    if action == "delete":
        return _do_delete(design, obj, kind, nm)
    if action == "reverse":
        return _do_reverse(obj, nm)
    return _do_set_values(obj, nm, ratio)


TOOL_DESCRIPTION = (
    "Edit or remove an existing assembly relation - a rigid group, a motion link, or an assembly "
    "constraint - by name (from assembly_get(include=['relations']); a repeated name is refused, "
    "not guessed). 'suppress'/'unsuppress' park one without deleting it, reporting any feature the "
    "change breaks; 'delete' removes it IRREVERSIBLY and re-lists to confirm. 'reverse' flips a "
    "motion link's direction; 'set_values' re-couples it to 'ratio' (joint_two per unit of "
    "joint_one; the SIGN sets direction, so a positive value clears a reversal). 'set_occurrences' "
    "is REFUSED - this build cannot edit a rigid group's members after creation - and the error "
    "names the path that works. Create relations with assembly_rigid_group / joint_motion_link / "
    "assembly_constrain."
)

tool = (
    Tool.create_simple(name="assembly_edit_relations", description=TOOL_DESCRIPTION)
    .add_input_property(*_KIND.as_property())
    .add_input_property("name", {"type": "string",
            "description": "The relation's name, from assembly_get(include=['relations'])."})
    .add_input_property(*_ACTION.as_property())
    .add_input_property(*_OCCURRENCES.as_property())
    .add_input_property("include_children", {"type": "boolean",
            "description": "set_occurrences: kept so the refused call is answered, not schema-rejected."})
    .add_input_property("ratio", {"type": "number",
            "description": "set_values: joint_two's motion per unit of joint_one; the SIGN sets the direction, so a positive value clears an existing reversal."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler,
                             run_on_main_thread=True)


def register_tool():
    register(item)
