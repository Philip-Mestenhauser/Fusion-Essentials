"""Wire budget: the tools/list payload an agent pays for every session stays under a ceiling.

Budgets are RATCHETS with named escape valves: lower them as the surface slims; a tool that
legitimately needs more gets a _DESCRIPTION_OVERRIDES entry with a one-line audited reason
(the same shrink-only convention as the other lint tables) - the bare globals are never bumped
to absorb silent growth. A description that outgrows its ceiling should lose prose to a typed
input or a result note (see tools/CLAUDE.md "What actually crosses the wire"). Measured baseline
when set: 137 tools, 213,037 bytes total, heaviest tool 4,038 bytes, longest description
1,295 chars. Sizes are measured on the canonical compact JSON encoding of the tools/list result
(the token-cost driver), not the indented HTTP body. Headroom above baseline is deliberate
(~8%): ordinary tool additions must not trip the total ceiling; re-inflating boilerplate across
many tools still will.
"""

import json

import pytest

from conftest import load_mcp_server, register_all_tools

TOTAL_PAYLOAD_BUDGET_BYTES = 230_000
PER_TOOL_BUDGET_BYTES = 4_500

# General per-description ceiling; a named override carries its own audited reason and is
# dropped the moment the description fits the general ceiling again (a test enforces both).
# Entry shape: "tool_name": <char limit>,  # one-line reason
DESCRIPTION_BUDGET_CHARS = 1_300
_DESCRIPTION_OVERRIDES = {}


@pytest.fixture(scope="module")
def wire_tools():
    """The tools/list entries exactly as the REAL server sends them, all tools registered."""
    mcp_server = load_mcp_server()
    srv = mcp_server.SimpleMCPServer()
    for item in register_all_tools():
        srv.register(item)
    return srv._handle_tools_list(1)["result"]["tools"]


def test_tools_list_payload_within_budget(wire_tools):
    size = len(json.dumps({"tools": wire_tools}))
    assert size <= TOTAL_PAYLOAD_BUDGET_BYTES, (
        f"tools/list payload is {size:,} bytes (> {TOTAL_PAYLOAD_BUDGET_BYTES:,}). This is what an "
        "agent loads before its first turn - slim a description or type an input instead of "
        "raising the budget.")


def test_no_single_tool_exceeds_wire_ceiling(wire_tools):
    over = {t["name"]: len(json.dumps(t)) for t in wire_tools
            if len(json.dumps(t)) > PER_TOOL_BUDGET_BYTES}
    assert not over, (
        f"tool entries over {PER_TOOL_BUDGET_BYTES:,} bytes on the wire: {over}")


def test_no_description_exceeds_ceiling(wire_tools):
    over = {}
    for t in wire_tools:
        limit = _DESCRIPTION_OVERRIDES.get(t["name"], DESCRIPTION_BUDGET_CHARS)
        n = len(t.get("description", ""))
        if n > limit:
            over[t["name"]] = f"{n} > {limit}"
    assert not over, (
        f"descriptions over their ceiling: {over} - move workflow prose to a result note or the "
        "server instructions, type the input, or add a NAMED override with an audited reason.")


def test_override_table_matches_reality(wire_tools):
    # an override for a tool that no longer exists, or that now fits the general ceiling, is a
    # stale table entry - the same freshness rule the other lint tables follow.
    by_name = {t["name"]: len(t.get("description", "")) for t in wire_tools}
    stale = []
    for name, limit in _DESCRIPTION_OVERRIDES.items():
        if name not in by_name:
            stale.append(f"{name}: tool gone")
        elif by_name[name] <= DESCRIPTION_BUDGET_CHARS:
            stale.append(f"{name}: fits the general ceiling now - drop the override")
    assert not stale, "Stale _DESCRIPTION_OVERRIDES entries:\n" + "\n".join(stale)
