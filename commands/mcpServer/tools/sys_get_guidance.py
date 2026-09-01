# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""sys_get_guidance - the server's packaged CAD design guidance, one section per call.

Static packaged content read through ``..guidance.loader``: no arguments gives the section index,
``section`` gives that one section's rules as the records the document carries. Nothing here
touches the Fusion API, so the tool runs off the main thread.
"""

from ._common import ok, error
from . import _inputs
from ..guidance import loader
from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

# The most rules one response carries: a section that grows can never make one call answer with the
# whole document. test_sys_get_guidance.py pins that no shipped section reaches it, so a truncation
# would be a document change, not a normal read.
MAX_RULES = 8

_SECTION = _inputs.Choice(
    "section", loader.SECTION_IDS,
    description="Which section's rules to return. Omit for the section index.")

INDEX_NOTE = (
    "The one design-guidance document this server packages. Call again with section=<id> for that "
    "section's rules - one section per call. 'sha256' is the content hash of the document served, "
    "and each rule declares which of 'scenarios' it applies to.")

SECTION_NOTE = (
    "Each rule is a record: 'when' the condition it applies under, 'do' the practice, 'except' "
    "where it does not apply, 'prove' the tool call to read the result back through and what to "
    "observe in it, 'scenarios' the cases it declares itself for. 'next_sections' names what is "
    "left to ask for.")


def handler(section=None) -> dict:
    """See TOOL_DESCRIPTION."""
    wanted, refusal = _SECTION.resolve(section)
    if refusal:
        return error(refusal)

    try:
        doc, sha256 = loader.load()
    except loader.GuidanceUnavailable as exc:
        return error(str(exc))

    ids = loader.section_ids(doc)
    result = {"guidance_id": doc.get("guidance_id"), "title": doc.get("title"), "sha256": sha256}

    if not wanted:
        result.update({"section": None,
                       "sections": loader.section_index(doc),
                       "scenarios": list(doc.get("scenarios") or []),
                       "next_sections": ids,
                       "note": INDEX_NOTE})
        return ok(result)

    sec = loader.find_section(doc, wanted)
    if sec is None:
        # Only reachable when the shipped document disagrees with the declared section list - a
        # defect in the packaged data, reported as what the document DOES carry.
        return error(f"The packaged guidance document carries no section '{wanted}'. It carries: "
                     + ", ".join(str(i) for i in ids) + ".")

    rules = list(sec.get("rules") or [])
    shown = rules[:MAX_RULES]
    result.update({"section": wanted,
                   "section_title": sec.get("title"),
                   "rule_count": len(shown),
                   "rules": shown,
                   "next_sections": [i for i in ids if i != wanted],
                   "note": SECTION_NOTE})
    if len(rules) > MAX_RULES:
        result["truncated"] = True
        result["rule_total"] = len(rules)
    return ok(result)


TOOL_DESCRIPTION = (
    "Read this server's packaged CAD DESIGN GUIDANCE: task-agnostic practice for building a part "
    "or an assembly - what to settle before the first feature, how design intent is carried in "
    "parameters and sketches, how an assembly's degrees of freedom are structured, and the read "
    "that proves each one. No arguments: the section index, with the document's id and content "
    "hash. 'section': that one section's rules, each a structured record - when it applies, what "
    "to do, where it does not, and the tool to read the result back through. One section per "
    "call; the payload names the sections left to ask for."
)

tool = (
    Tool.create_simple(name="sys_get_guidance", description=TOOL_DESCRIPTION)
    .add_input_property(*_SECTION.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=False)


def register_tool():
    register(item)
