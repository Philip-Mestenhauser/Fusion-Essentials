"""Wire budget: the tools/list payload is bounded per entry and across the fleet.

The tools/list payload is the token block every connected agent downloads before its first turn.
The limits here are measured in TWO units, and no number in this file is comparable to another
without naming which unit it is in:

- TRANSPORT bytes - ``json.dumps(entry, indent=2)``, the serialization mcp_server._send_json
  writes. Pretty-printing is not free: a newline plus indentation per key costs real bytes, and the
  cost lands hardest on the entries with the MOST keys. PER_TOOL_BUDGET_BYTES bounds one entry's
  worst case in this unit; DESCRIPTION_BUDGET_CHARS counts characters of the description alone,
  which pretty-printing does not touch.
- COMPACT bytes - ``json.dumps(entry, separators=(",", ":"))``. Every MCP client parses tools/list
  and re-serializes each definition into its own prompt format, so the indentation crosses the
  socket and costs the model nothing: what a model pays for tracks the compact form. The two FLEET
  limits are measured in this unit.

The fleet limits are structural, so no tool is named anywhere in this file. The total is an
allowance PER REGISTERED TOOL: a fleet that gains a tool gains one tool's worth of room, which
keeps "the fleet grew" and "the tools got fatter" separate failures. The P90 catches a heavy tail
the mean absorbs. A failed limit prints the current total, the current P90, the entry the P90 rank
reads with the entries just above it, and the ten heaviest - every line of it computed at failure
time. That message is the only per-tool weight view there is: no entry's weight is recorded
anywhere, by design, so a slimmed or reworded description moves nothing here and leaves nothing to
re-pin. (tests/generated/TOOL_MANIFEST.md is the roster of registered tools and carries no weights.)

Bytes == characters throughout: every wire string is ASCII (test_wire_ascii.py). What the numbers
exclude: inside the real response each entry sits at result.tools[i], so every line carries 6 more
spaces of indent - each measurement is the entry's own cost, in one unit for every tool, not the
full delivered size. The jargon gate keeps repo-internal vocabulary out of agent-facing prose.
"""

import json
import math

import pytest

from conftest import load_mcp_server, register_all_tools

# The per-entry hard ceilings, in TRANSPORT bytes. PER_TOOL_BUDGET_BYTES bites on whichever entry
# is nearest it, so no tool can grow past it unnoticed.
PER_TOOL_BUDGET_BYTES = 6_300
DESCRIPTION_BUDGET_CHARS = 1_300
_DESCRIPTION_OVERRIDES = {}

# The fleet limits, in COMPACT bytes. The total is an allowance PER REGISTERED TOOL; the P90
# ceiling carries roughly 2 percent of headroom over the fleet's measured nearest-rank P90 at
# calibration time. Recalibrating either number is the owner's decision - a red bar is answered by
# slimming the entries the failure names, not by raising the number.
FLEET_BYTES_PER_TOOL = 1_900
FLEET_P90_BUDGET_BYTES = 3_370

# Repo-internal vocabulary that must not leak into agent-facing descriptions - an agent reading
# tools/list has no repo context. Lower-case substring match.
_WIRE_JARGON = ("disclose read", "acquire tool", "rich read", "orient read", "write guard",
                "input kind", "the registry", "postcondition")


@pytest.fixture(scope="module")
def wire_tools():
    """The tools/list entries exactly as the REAL server sends them, all tools registered."""
    mcp_server = load_mcp_server()
    srv = mcp_server.SimpleMCPServer()
    for item in register_all_tools():
        srv.register(item)
    return srv._handle_tools_list(1)["result"]["tools"]


def _wire_bytes(entry):
    """One entry's TRANSPORT size: json.dumps(..., indent=2), what the server sends. The ONE place
    that unit is chosen, so the per-tool ceiling can never drift onto the other one."""
    return len(json.dumps(entry, indent=2))


def _compact_bytes(entry):
    """One entry's COMPACT size: json.dumps(..., separators=(",", ":")), what a client's own
    re-serialization tracks. The ONE place that unit is chosen, so the fleet limits can never drift
    onto the transport's."""
    return len(json.dumps(entry, separators=(",", ":")))


def _sizes(entries):
    """(name, COMPACT size) for every entry, ascending by size."""
    return sorted(((e["name"], _compact_bytes(e)) for e in entries), key=lambda kv: kv[1])


def _rank_index(count, pct):
    """The 0-based index the NEAREST-RANK percentile reads over `count` values: the
    ceil(pct/100 * count)-th smallest, 1-indexed. -1 when there are no values at all. No
    interpolation, so the answer is always a size some entry really measures."""
    return max(1, math.ceil(pct / 100 * count)) - 1 if count else -1


def _fleet(entries):
    """(total, P90, the ten heaviest as (name, size)) over every entry's COMPACT size."""
    ordered = _sizes(entries)
    rank = _rank_index(len(ordered), 90)
    p90 = ordered[rank][1] if rank >= 0 else 0
    return sum(size for _, size in ordered), p90, list(reversed(ordered[-10:]))


def _fleet_report(entries):
    """What a failed fleet limit prints, every line of it computed HERE at failure time.

    Two remedies, cheapest first. The P90 is one entry's own size, so slimming that entry lifts the
    number to the NEXT entry above it - naming the rank entry and the three above it is what makes
    the cheap fix findable. The heaviest ten are the expensive fix, and the whole listing: no
    entry's weight is recorded anywhere in this file."""
    ordered = _sizes(entries)
    total, p90, heaviest = _fleet(entries)
    allowance = len(entries) * FLEET_BYTES_PER_TOOL
    rank = _rank_index(len(ordered), 90)
    band = ordered[rank:rank + 4] if rank >= 0 else []
    return (f"fleet compact wire over {len(entries)} tools: total {total:,} bytes against an "
            f"allowance of {allowance:,} ({len(entries)} x {FLEET_BYTES_PER_TOOL:,} per tool), "
            f"P90 {p90:,} against a ceiling of {FLEET_P90_BUDGET_BYTES:,}.\n"
            "  at the P90 rank, then the entries above it (slimming one lifts the P90 to the "
            "next): " + ", ".join(f"{name}: {size:,}" for name, size in band) + "\n"
            "  heaviest ten: " + ", ".join(f"{name}: {size:,}" for name, size in heaviest)
            + "\n  slim these - move workflow prose to a result note, or type the input.")


def _over_ceiling(entries):
    """name -> TRANSPORT size, for every entry over the per-tool ceiling."""
    return {e["name"]: n for e in entries if (n := _wire_bytes(e)) > PER_TOOL_BUDGET_BYTES}


def _sized_entry(name, size, measure=_compact_bytes, fill="d"):
    """A synthetic tools/list entry measuring EXACTLY ``size`` in ``measure``'s unit.

    The padding is one ASCII character per byte: a plain description character needs no JSON escape,
    and in the indented form the whole description still sits on its own single line."""
    entry = {"name": name, "description": "", "inputSchema": {"type": "object", "properties": {}}}
    pad = size - measure(entry)
    assert pad >= 0, f"{size} is under the empty entry's own {measure(entry)} bytes"
    entry["description"] = fill * pad
    assert measure(entry) == size, f"padded to {measure(entry)}, wanted {size}"
    return entry


def test_no_single_tool_exceeds_wire_ceiling(wire_tools):
    over = _over_ceiling(wire_tools)
    assert not over, f"tool entries over {PER_TOOL_BUDGET_BYTES:,} bytes on the wire: {over}"


def test_no_description_exceeds_ceiling(wire_tools):
    over = {}
    for t in wire_tools:
        limit = _DESCRIPTION_OVERRIDES.get(t["name"], DESCRIPTION_BUDGET_CHARS)
        n = len(t.get("description", ""))
        if n > limit:
            over[t["name"]] = f"{n} > {limit}"
    assert not over, (
        f"descriptions over their ceiling: {over} - move workflow prose to a result note, type "
        "the input, or add a NAMED override with an audited reason.")


def test_override_table_matches_reality(wire_tools):
    by_name = {t["name"]: len(t.get("description", "")) for t in wire_tools}
    stale = []
    for name, limit in _DESCRIPTION_OVERRIDES.items():
        if name not in by_name:
            stale.append(f"{name}: tool gone")
        elif by_name[name] <= DESCRIPTION_BUDGET_CHARS:
            stale.append(f"{name}: fits the general ceiling now - drop the override")
    assert not stale, "Stale _DESCRIPTION_OVERRIDES entries:\n" + "\n".join(stale)


def test_no_repo_jargon_in_wire_prose(wire_tools):
    hits = []
    for t in wire_tools:
        blob = json.dumps(t).lower()
        for term in _WIRE_JARGON:
            if term in blob:
                hits.append(f"{t['name']}: '{term}'")
    assert not hits, (
        "repo-internal vocabulary leaked into agent-facing wire prose (an agent reading "
        "tools/list has no repo context) - reword:\n  " + "\n  ".join(hits))


def test_the_fleet_total_stays_within_its_per_tool_allowance(wire_tools):
    total, _, _ = _fleet(wire_tools)
    assert total <= len(wire_tools) * FLEET_BYTES_PER_TOOL, _fleet_report(wire_tools)


def test_the_fleet_p90_stays_under_its_ceiling(wire_tools):
    _, p90, _ = _fleet(wire_tools)
    assert p90 <= FLEET_P90_BUDGET_BYTES, _fleet_report(wire_tools)


def test_the_per_tool_ceiling_measures_the_transport_serialization():
    # The ceiling is in the transport's unit: mcp_server._send_json writes json.dumps(data,
    # indent=2), and pretty-printing costs real bytes per key. An entry measured compact reads
    # SMALLER than what the agent downloads, which is the drift this pins against.
    entry = {"name": "t", "description": "d", "inputSchema": {"type": "object", "properties": {}}}
    assert _wire_bytes(entry) == len(json.dumps(entry, indent=2))
    assert _wire_bytes(entry) > len(json.dumps(entry))


def test_the_fleet_limits_measure_the_compact_serialization():
    # The fleet limits are in the OTHER unit, and the two are never interchangeable: the same entry
    # is smaller compacted than it is on the transport (and smaller than json's own default
    # separators), so a fleet limit read in transport bytes would be a looser bar than it says.
    entry = {"name": "t", "description": "d", "inputSchema": {"type": "object", "properties": {}}}
    assert _compact_bytes(entry) == len(json.dumps(entry, separators=(",", ":")))
    assert _compact_bytes(entry) < len(json.dumps(entry)) < _wire_bytes(entry)


def test_nearest_rank_reads_a_size_some_entry_measures():
    # ceil(pct/100 * N)-th smallest, 1-indexed: over ten entries the P90 is the ninth, over five it
    # is the fifth (4.5 rounds up), and the answer is one entry's own size, never an interpolation.
    assert _rank_index(10, 90) == 8
    assert _rank_index(5, 90) == 4
    assert _rank_index(5, 40) == 1
    assert _rank_index(1, 90) == 0
    assert _rank_index(0, 90) == -1
    ten = [_sized_entry(f"t{i}", 500 + i) for i in range(10)]
    assert _fleet(ten)[1] == 508, "the ninth smallest of ten, not a value between two entries"
    assert _fleet([]) == (0, 0, []), "an empty fleet answers zero rather than raising"


def test_the_per_tool_ceiling_bites():
    at_the_line = _sized_entry("at_the_line", PER_TOOL_BUDGET_BYTES, _wire_bytes)
    over = _sized_entry("one_byte_over", PER_TOOL_BUDGET_BYTES + 1, _wire_bytes)
    assert _over_ceiling([at_the_line]) == {}, "an entry exactly AT the ceiling is legal"
    assert _over_ceiling([at_the_line, over]) == {"one_byte_over": PER_TOOL_BUDGET_BYTES + 1}
    test_no_single_tool_exceeds_wire_ceiling([at_the_line])
    with pytest.raises(AssertionError, match="one_byte_over"):
        test_no_single_tool_exceeds_wire_ceiling([at_the_line, over])


def test_the_fleet_total_bites_on_fleet_wide_growth():
    fleet = [_sized_entry(f"t{i}", FLEET_BYTES_PER_TOOL) for i in range(20)]
    assert _fleet(fleet)[0] == 20 * FLEET_BYTES_PER_TOOL
    test_the_fleet_total_stays_within_its_per_tool_allowance(fleet)   # exactly at it - legal
    # every entry one byte heavier: no single entry is remarkable, the fleet is over
    grown = [_sized_entry(f"t{i}", FLEET_BYTES_PER_TOOL + 1) for i in range(20)]
    assert _over_ceiling(grown) == {}, "the growth is invisible to the per-tool ceiling"
    with pytest.raises(AssertionError, match=f"total {20 * (FLEET_BYTES_PER_TOOL + 1):,}"):
        test_the_fleet_total_stays_within_its_per_tool_allowance(grown)
    # one MORE tool at the same weight raises the allowance by exactly one tool's worth: growing
    # the fleet is a different event from growing the tools, and only the second is a failure
    test_the_fleet_total_stays_within_its_per_tool_allowance(
        fleet + [_sized_entry("t20", FLEET_BYTES_PER_TOOL)])


def test_the_fleet_p90_bites_on_a_heavier_tail():
    # ten entries, so the nearest-rank P90 is the ninth smallest: 'ninth' is the entry the ceiling
    # reads, and one heavier tool rides above it without moving the number.
    def fleet(ninth):
        return ([_sized_entry(f"light{i}", 500) for i in range(8)]
                + [_sized_entry("ninth", ninth), _sized_entry("heaviest", 4_000)])

    assert _fleet(fleet(FLEET_P90_BUDGET_BYTES))[1] == FLEET_P90_BUDGET_BYTES
    test_the_fleet_p90_stays_under_its_ceiling(fleet(FLEET_P90_BUDGET_BYTES))   # at it - legal
    with pytest.raises(AssertionError, match=f"P90 {FLEET_P90_BUDGET_BYTES + 1:,}"):
        test_the_fleet_p90_stays_under_its_ceiling(fleet(FLEET_P90_BUDGET_BYTES + 1))


def test_a_shrink_or_a_reword_passes_with_nothing_to_edit_here():
    # the limits are structural - no entry is named in this file - so slimming a description, or
    # rewording one at the same size, passes on its own.
    fleet = [_sized_entry(f"t{i}", FLEET_BYTES_PER_TOOL) for i in range(20)]
    slimmer = [_sized_entry(f"t{i}", FLEET_BYTES_PER_TOOL - 100) for i in range(20)]
    reworded = [_sized_entry(f"t{i}", FLEET_BYTES_PER_TOOL, fill="x") for i in range(20)]
    for variant in (slimmer, reworded):
        test_the_fleet_total_stays_within_its_per_tool_allowance(variant)
        test_the_fleet_p90_stays_under_its_ceiling(variant)
        assert _over_ceiling(variant) == {}
    assert _fleet(reworded)[0] == _fleet(fleet)[0], "a same-size reword moves no number"
    assert _fleet(slimmer)[0] < _fleet(fleet)[0], "a shrink lowers the total"


def test_the_fleet_report_names_the_rank_band_and_the_heaviest():
    # the failure message is the whole remedy: with no per-tool weight recorded anywhere, a red
    # fleet limit that named no entry would say only that the fleet is too big. The rank band is
    # the cheap fix (the P90 is that entry's own size), the heaviest ten the expensive one.
    fleet = [_sized_entry(f"t{i}", 500 + i) for i in range(30)]
    report = _fleet_report(fleet)
    assert "total 15,435" in report and "P90 526" in report, report
    assert "allowance of 57,000" in report, report
    # the entry the rank reads, then the three above it, in that order
    assert "t26: 526, t27: 527, t28: 528, t29: 529" in report, report
    assert "heaviest ten: t29: 529" in report and "t20: 520" in report, report
    assert "t19: 519" not in report, "only the ten heaviest are listed"
