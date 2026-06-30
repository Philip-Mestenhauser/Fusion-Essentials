# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: SEARCH the server's own tools + input kinds (the anti-blindness tool).

  sys_find_tool(query="profile") -> the registered tools whose name/description/inputs match, AND the
                                    _inputs.py input-kinds that match — so an agent can ask "is there
                                    already a way to do/reference X?" BEFORE hand-rolling it.

Why this exists: the tool set + the typed input-kinds in _inputs.py have grown large enough that
agents (and compactions) lose track of what already exists and re-invent it — a fresh
`sketch_name+profile_index` resolver when ProfileRef already exists, a second CAM-tool reader, etc.
A STATIC index goes stale the instant a tool is added (a prior TOOL_INDEX was removed for exactly
that). So this searches the LIVE registry + the live _inputs module every call — it can't drift.

Read-only, no adsk.* — pure introspection of the registry and _inputs. Safe to run anytime.
"""

import inspect

from ._common import ok, error
from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register, get_tools
from . import _inputs


def _score(query_terms, *texts):
    """A simple relevance score: how many query terms appear in the combined text (name hits weigh
    more — handled by the caller passing name first and we bonus early matches)."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return 0
    return sum(1 for t in query_terms if t in blob)


def _tool_matches(query_terms):
    out = []
    for item in get_tools():
        prim = getattr(item, "primitive", None)
        if not isinstance(prim, Tool):
            continue
        name = prim.name or ""
        desc = prim.description or ""
        props = list((prim.input_schema or {}).get("properties", {}).keys())
        # name matches weigh double (the thing you'd grep for)
        s = 2 * _score(query_terms, name) + _score(query_terms, desc) + _score(query_terms, " ".join(props))
        if s > 0:
            out.append((s, {
                "tool": name,
                "inputs": props,
                # first sentence of the description — enough to decide relevance without the full blob
                "summary": (desc.split(". ")[0][:160] + ("…" if len(desc) > 160 else "")),
            }))
    out.sort(key=lambda t: -t[0])
    return [m for _, m in out]


def _kind_matches(query_terms):
    """Search the _inputs.py InputKind subclasses by class name + first docstring line. This is the
    'is there already a typed kind for referencing X?' half — the answer that stops a hand-rolled
    name/index resolver."""
    out = []
    base = getattr(_inputs, "InputKind", None)
    if base is None:
        return out
    for cname, cls in inspect.getmembers(_inputs, inspect.isclass):
        if cls is base or not issubclass(cls, base):
            continue
        doc = (inspect.getdoc(cls) or "").strip().split("\n")[0]
        s = 2 * _score(query_terms, cname) + _score(query_terms, doc)
        if s > 0:
            out.append((s, {"kind": cname, "summary": doc[:160]}))
    out.sort(key=lambda t: -t[0])
    return [m for _, m in out]


def handler(query: str = "", include_kinds: bool = True) -> dict:
    """Search the registered tools (and optionally the _inputs.py input-kinds) by keyword.

    query: words to match against tool names/descriptions/input names and kind names/docs (e.g.
    'profile', 'select cam geometry', 'body reference'). include_kinds: also search the typed input
    kinds (default true) — check these BEFORE hand-rolling a name/index input. Read-only.
    """
    q = (query or "").strip().lower()
    if not q:
        return error("Provide 'query' — keywords to search tool names/descriptions/inputs (and the "
                     "_inputs.py kinds). E.g. 'profile', 'cam geometry', 'reference a body'.")
    terms = [t for t in q.replace(",", " ").split() if t]

    tools = _tool_matches(terms)
    result = {
        "query": query,
        "tool_count": len(tools),
        "tools": tools[:25],
    }
    if include_kinds:
        kinds = _kind_matches(terms)
        result["kinds"] = kinds[:15]
        if kinds:
            result["note"] = ("Before adding a tool input that REFERENCES existing geometry/profile/"
                              "body/etc., use one of these _inputs.py kinds (extend the kind if it's "
                              "close); don't hand-roll a name/index. See CLAUDE.md 'Input kinds'.")
    if not tools and not result.get("kinds"):
        result["note"] = "No tool or input-kind matched. Try broader/different keywords."
    return ok(result)


TOOL_DESCRIPTION = (
    "SEARCH this server's own tools + the typed input-kinds (in _inputs.py) by keyword — to find "
    "what ALREADY EXISTS before building or hand-rolling it. 'query' matches tool names/descriptions/"
    "input names and kind names/docs (e.g. 'profile', 'cam geometry', 'reference a body'). Returns "
    "ranked tools (name + inputs + one-line summary) and matching input-kinds. LIVE (reads the "
    "registry each call, never stale). Use it before adding a tool or a name/index input — there's "
    "usually already a tool or a ProfileRef/BodyRef/GeometryHandle kind for it. Read-only."
)

tool = (
    Tool.create_simple(name="sys_find_tool", description=TOOL_DESCRIPTION)
    .add_input_property("query", {"type": "string",
            "description": "Keywords to match tool names/descriptions/inputs + input-kind names/docs."})
    .add_input_property("include_kinds", {"type": "boolean",
            "description": "Also search the _inputs.py input-kinds (default true)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=False)


def register_tool():
    register(item)
