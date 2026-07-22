"""Wire budget: the tools/list payload an agent pays for every session stays under a ceiling.

Budgets are RATCHETS with named escape valves: lower them as the surface slims; a tool that
legitimately needs more gets a _DESCRIPTION_OVERRIDES entry with a one-line audited reason
(the same shrink-only convention as the other lint tables) - the bare globals are never bumped
to absorb silent growth. A description that outgrows its ceiling should lose prose to a typed
input or a result note (see tools/CLAUDE.md "What actually crosses the wire"). Measured baseline
when set: 139 tools, 226,099 bytes total, longest description 1,295 chars. Sizes are measured on
the canonical compact JSON encoding of the tools/list result (the token-cost driver), not the
indented HTTP body.

The total headroom is deliberately SNUG: a NEW tool that pushes the total over must be paid for by
slimming existing prose in the same change, not by bumping the global (that is the ratchet working
as intended). The per-tool count is the ratchet's granularity: if you add a tool, the total moving
up should be roughly its own weight. A tool whose own CAPABILITY genuinely grows (not just its
prose) earns the same treatment as a new tool: the global moves up by that tool's own measured
weight, named here - model_extrude's extent coverage (through_all/to_face/two_side added to the
prior distance/to_object pair) added +609 bytes measured (224,124->224,733) after trimming its own
existing prose first; sys_request_selection's HOLD (wait_seconds + expect_document) together with
sys_get_selection's handle-minting are the current largest joint contributor, +1,258 bytes measured
(224,733->225,991) after trimming both descriptions first; sys_request_selection's
nothing-to-select refusal + the announce-the-pick-first caller norm added +108 measured
(225,991->226,099) after trimming the schema-restating clauses from the same description;
doc_insert_derive's source-subset scoping (source_components/source_bodies + exclude_components/
exclude_bodies, and the require-open contract) added +867 bytes measured (226,099->226,966) after
trimming its own description first - the derive tool went from whole-design-only to any component/
body granularity, the crux of the derive-scoping build.

assembly_get's joint_origins slice (the JOINT-ORIGIN SEAM read: include=['joint_origins'] +
max_joint_origins, each Joint Origin surfaced as a referenceable, handle-bearing row so a machining
WCS / joint binds to it by name-or-handle instead of a fragile box-point) added +537 bytes measured
(226,966->227,503) after trimming its own description first - a whole new rich-read slice plus the
first-class JointOrigin handle it mints.

cam_edit_setup's wcs now accepts a Joint Origin (handle OR name), binding the WCS to it as a live
associative reference (proven live: bound_entities=1, mode='point') - the CAM WCS<-JointOrigin binding
that closes the run-08 gap (a setup could not bind to the self-centering stock origin). +175 bytes
measured (227,503->227,678).

model_construction's offset plane now accepts a parameter EXPRESSION ('StockZ/2', '25 mm') routed
through createByString and NAMES the model parameter (dNN) it created (retargetable via param_set) -
the same landed shape as model_extrude's distance fix. +129 bytes measured (227,678->227,807). The
three live-verified honesty notes that rode the same batch (joint_create_origin lands on ROOT;
sketch_create's frame is component-LOCAL; sketch_add_3d_line's z is along the sketch's LOCAL normal)
were absorbed net-neutral by trimming redundant prose in those same descriptions.

Three capability growths land together, +210 bytes measured (227,807->228,017) after trimming their
own descriptions first: doc_copy's source_folder input (scopes the budget-bounded by-name walk - the
main-thread stall fix's cheap escape path), view_set's hide/show accepting BODY targets (root-level
bodies / one body of a multi-body component, per-body bulb read-back), and cam_edit_operation's
expression-evaluation read-back claim (a non-evaluating expression rolls back all params).

The watch-build disclosure batch lands +533 bytes measured (228,017->228,550) after trimming the
same tools' own prose first: sketch_add_geometry's closed_path ~48-point solver limit + the
polyline/repeated-point workaround and center_rectangle's no-implicit-constraints fact,
sketch_dimension's lone-line length + point:0-is-origin facts, sketch_add_3d_line's new
is_construction input, joint_create's free-part-moves warning, joint_drive's poses-do-not-survive-
recompute warning, design_delete_feature's indices-shift warning, and joint_create_origin's
model_parameters (dNN names) read-back.
"""

import json

import pytest

from conftest import load_mcp_server, register_all_tools

# Snug watermark: current real total is 228,550. Ratchet DOWN as prose moves to errors/notes or a
# description tightens; a new tool (or a tool whose capability genuinely grows) that needs the room
# slims something else in the same change, or raises this by exactly its own measured weight.
TOTAL_PAYLOAD_BUDGET_BYTES = 228_550
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
