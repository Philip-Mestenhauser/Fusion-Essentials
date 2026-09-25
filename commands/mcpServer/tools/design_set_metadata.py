# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: set a component's engineering metadata - part number, description. WRITES.

Both values live on the COMPONENT, so an occurrence target sets them for every instance of it.
"""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs
from . import _outputs

# A body/face carries neither field and '' (the whole design) has nothing to address, so the kinds
# are the component and the occurrence that places it.
_TARGET = _inputs.TargetRef("target", allow=("occurrence", "component"))

RETURNS = [
    _outputs.ReturnsValue("part_number", "the part number read back off the component",
                          consumers=["design_get"]),
]


def handler(target: str = "", part_number: str = None, description: str = None) -> dict:
    """Set what was given, read both fields back, and publish what LANDED. WRITES."""
    if part_number is None and description is None:
        return error("Nothing to set - pass 'part_number', 'description', or both.")
    if part_number == "":
        # MEASURED: assigning '' leaves the number the component already holds - Fusion ignores it,
        # and the API doc's "'' resets it to the component name" does not hold on this build.
        return error("An empty 'part_number' is ignored by Fusion - the component keeps the number "
                     "it already has. Pass the number to set.")

    design = _common.design()
    if not design:
        return error("No active design. Open a document first (see doc_open / doc_new).")

    resolved, terr = _TARGET.resolve(target)
    if terr:
        return error(terr)
    entity, kind = resolved
    if kind == "occurrence":
        # An occurrence carries no metadata of its own - the component it places does.
        occurrence = entity
        entity = safe(lambda: occurrence.component)
        if entity is None:
            return error(f"Could not reach the component behind occurrence '{target}' to set its "
                         "metadata.")
        kind = "component"

    component = entity
    addressed = f"component '{safe(lambda: component.name) or target}'"
    previous_number = safe(lambda: component.partNumber)
    previous_description = safe(lambda: component.description)

    try:
        # The MUTATIONS - not safe-wrapped, so a refusal is reported instead of swallowed.
        if part_number is not None:
            component.partNumber = part_number
        if description is not None:
            component.description = description
    except Exception as e:
        return error(f"Could not set metadata on {addressed}: {e}")

    landed_number = safe(lambda: component.partNumber)
    landed_description = safe(lambda: component.description)
    for field, requested, landed in (("part_number", part_number, landed_number),
                                     ("description", description, landed_description)):
        if requested is None:
            continue
        if landed is None:
            return error(f"Set {field} on {addressed} to '{requested}', but the value could not be "
                         "read back, so the set is unverified. Read it with "
                         "design_get(include=['metadata']).")
        if landed != requested:
            return error(f"{addressed} reads {field} '{landed}' after it was set to '{requested}' - "
                         "the value that LANDED is not the one requested.")

    result = {
        "set": True,
        "kind": kind,
        "addressed": addressed,
        "component": safe(lambda: component.name),
        "part_number": landed_number,
        "description": landed_description,
        "previous_part_number": previous_number,
        "previous_description": previous_description,
        "note": f"{addressed}: part_number '{landed_number}', description "
                f"'{landed_description}' - read back after the set.",
    }
    if part_number is not None:
        result["part_number_requested"] = part_number
    if description is not None:
        result["description_requested"] = description
    return ok(result)


_DESC = (
"Set a component's part number and/or description; an occurrence sets its COMPONENT's. Read them "
"with design_get(include=['metadata']).\n"
+ _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="design_set_metadata", description=_DESC)
    .add_input_property(*_TARGET.as_property())
    .add_input_property("part_number", {"type": "string",
            "description": "Left unchanged when omitted."})
    .add_input_property("description", {"type": "string",
            "description": "Left unchanged when omitted."})
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline", rung="value",
        evidence_test="tests/unit/test_design_set_metadata.py::TestGuards"
                      "::test_a_read_back_that_does_not_match_the_request_is_an_error"))


def register_tool():
    register(item)
