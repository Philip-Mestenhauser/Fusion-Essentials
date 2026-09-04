# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""sys_get_guidance - the server's packaged CAD design guidance, one section per call. Static
content read through ``..guidance.loader``; nothing here touches the Fusion API."""

from ._common import ok, error
from . import _inputs
from ..guidance import loader
from ..guidance import resources
from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

_SECTION = _inputs.Choice(
    "section", loader.SECTION_IDS,
    description="Which section's rules to return. Omit for the section index.")

INDEX_NOTE = (
    "The one design-guidance document this server packages. Call again with section=<id> for that "
    "section's rules - one section per call. 'sha256' is the content hash of the document served, "
    "and each rule declares which of 'scenarios' it applies to. 'resource_uri' is where the same "
    "guidance is served as one Markdown document over MCP's resource channel.")

SECTION_NOTE = (
    "Each rule is a record: 'when' the condition it applies under, 'do' the practice, 'except' "
    "where it does not apply, 'prove' the tool call to read the result back through and what to "
    "observe in it, 'scenarios' the cases it declares itself for. 'kind' is carried only by a "
    "safety invariant; a rule without it is strategy. 'next_sections' names what is left to ask "
    "for.")

TRUNCATED_NOTE = (
    " This section holds more rules than one call returns: 'rule_count' of 'rule_total' are in "
    "'rules' and the rest are not here. Read the document at 'resource_uri' over the resource "
    "channel for all of them - it is rendered whole, with no per-section cap.")


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
    result = {"guidance_id": doc.get("guidance_id"), "title": doc.get("title"), "sha256": sha256,
              "resource_uri": resources.uri_for(doc.get("guidance_id"))}

    if not wanted:
        result.update({"section": None,
                       "sections": loader.section_index(doc),
                       "scenarios": list(doc.get("scenarios") or []),
                       "next_sections": ids,
                       "note": INDEX_NOTE})
        return ok(result)

    sec = loader.find_section(doc, wanted)
    if sec is None:
        return error(f"The packaged guidance document carries no section '{wanted}'. It carries: "
                     + ", ".join(str(i) for i in ids) + ".")

    rules = list(sec.get("rules") or [])
    shown = rules[:loader.MAX_SECTION_RULES]
    result.update({"section": wanted,
                   "section_title": sec.get("title"),
                   "rule_count": len(shown),
                   "rules": shown,
                   "next_sections": [i for i in ids if i != wanted],
                   "note": SECTION_NOTE})
    if len(rules) > loader.MAX_SECTION_RULES:
        result["truncated"] = True
        result["rule_total"] = len(rules)
        result["note"] = SECTION_NOTE + TRUNCATED_NOTE
    return ok(result)


TOOL_DESCRIPTION = (
    "Read this server's packaged CAD DESIGN GUIDANCE: task-agnostic practice for building a part "
    "or an assembly - what to settle before the first feature, how design intent is carried in "
    "parameters and sketches, how an assembly's degrees of freedom are structured. No arguments: "
    "the section index. 'section': that one section's rules, each saying when it applies, what to "
    "do, and the tool to read the result back through. One section per call; the payload names "
    "the sections left to ask for."
)

tool = (
    Tool.create_simple(name="sys_get_guidance", description=TOOL_DESCRIPTION)
    .add_input_property(*_SECTION.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=False)


def register_tool():
    register(item)
