# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Make an existing component the active edit target, or return to the root component. WRITES."""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

_OCCURRENCE = _inputs.OccurrenceRef("occurrence",
        description="Occurrence to activate; '' or 'root' is the root.")


def _active_occurrence(design):
    """The currently active-edit occurrence, if any (isActive == True). None if root is active."""
    # The shared census, not a bare root.allOccurrences: that property RAISES on a design holding an
    # unresolved external reference, and an empty walk would report "root is active" - a wrong answer.
    for o in _common.all_occurrences(design):
        if safe(lambda o=o: o.isActive, False):
            return o
    return None


def handler(occurrence: str = "") -> dict:
    """Make an EXISTING component the active edit target, or return to the root component with ''
    (or 'root'). WRITES (UI edit target)."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    want = (occurrence or "").strip()

    # Return to root: activating the root deactivates any occurrence edit target.
    if want == "" or want.lower() == "root":
        root = safe(lambda: design.rootComponent)
        # Design.activateRootComponent() is not on every build, so an absent one falls back to
        # deactivating the active occurrence.
        did = safe(lambda: design.activateRootComponent(), None)
        if did is None:
            active_occ = safe(lambda: _active_occurrence(design))
            if active_occ is not None:
                active_occ.deactivate()
        now = safe(lambda: design.activeComponent.name)
        root_name = safe(lambda: root.name)
        if now is not None and root_name is not None and now != root_name:
            return error(f"Activation was accepted but the active component still reads '{now}' - "
                         "the edit target did not return to root.")
        return ok({
        "activated": "root",
        "active_component": now,
        "note": "Root component is the active edit target - new geometry builds at the root.",
        })

    occ, occ_err = _OCCURRENCE.resolve(want)
    if occ_err:
        return error(occ_err)

    did = bool(safe(lambda: occ.activate(), False))
    if not did:
        return error(f"Occurrence.activate() returned false for '{occurrence}' - could not make it the "
                     "active edit target.")
    now = safe(lambda: design.activeComponent.name)
    want_comp = safe(lambda: occ.component.name)
    if now is not None and want_comp is not None and now != want_comp:
        return error(f"activate() returned true but the active component still reads '{now}' "
                     f"(expected '{want_comp}') - the activation did not take.")
    return ok({
    "activated": safe(lambda: occ.name),
    "component": safe(lambda: occ.component.name),
    "active_component": safe(lambda: design.activeComponent.name),
    "note": ("This component is now the active edit target - sketch_create / model_extrude / "
            "sketch_dimension build into it. Activate 'root' (or '') to return to the root."),
    })


TOOL_DESCRIPTION = ("Make an existing component the active edit target, or return to the root. "
            "Subsequent sketch_create / model_extrude / sketch_dimension / sketch_constrain build "
            "into the active component. This changes the edit target, not geometry.")

tool = (
    Tool.create_simple(
        name="design_activate_component",
        description=TOOL_DESCRIPTION)
    .add_input_property(*_OCCURRENCE.as_property())
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_design_activate_component.py::TestActivateComponent"
                      "::test_activation_that_does_not_take_bites"))


def register_tool():
    register(item)
