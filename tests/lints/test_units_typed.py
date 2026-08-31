# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a length/coordinate input carries its unit in a typed selector, not in loose prose.

The north star (CLAUDE.md "Input kinds"): a fact about an input - here, what unit a number is in -
belongs in the typed surface an agent must consume to call the tool, not asserted in a description
string that nothing checks. A numeric input whose description names a unit (mm/cm/inch) while its
owning tool exposes no 'units' selector is a unit fact stranded in prose: an agent that learned
"pass units=cm" from a sibling tool gets a silently wrong-by-a-factor result with no feedback. The
remedy is to pair the number with the Distance + UnitField kinds from _inputs.py (which add the
'units' selector), so the unit is declared and resolved rather than asserted.

Flags a number (or array-of-number) input property whose description names a unit while its tool
declares no 'units' input. The shrink-only _EXEMPT table carries any input where a fixed, non-agent-
selectable unit is deliberate, each with a one-line reason.

READ-SIDE companion (test_units_reporting_read_wires_the_units_kind): the mirror convention - a tool
that REPORTS a 'units' field in its result payload must let the agent CHOOSE those units through the
shared _inputs.UNITS enum kind (mm/cm/in), not a hand-rolled 'units' string. Every geometry-reporting
read scales its output via _common.CM_TO_UNIT keyed by that selector, so the selector must be the
typed kind or the report and the request silently disagree.
"""

import inspect
import os
import re

from conftest import load_tool, register_all_tools, TOOLS_DIR

# a unit token, allowing a leading digit ("5mm") but not a letter ("swimming", "incoming").
_UNIT = re.compile(r"(?<![A-Za-z])(mm|cm|inch(?:es)?|millimet\w*|centimet\w*)(?![A-Za-z])", re.I)

# "tool.input" paths where a bare unit in prose is deliberate (a fixed unit the agent cannot select).
# Shrink-only; each entry needs a reason.
_EXEMPT = {
    # "some_tool.some_input": "reason a fixed unit is correct here",
}

# Tools that REPORT a 'units' field yet legitimately do NOT wire the shared UNITS kind. Shrink-only;
# each needs a reason a scaled-measurement selector is wrong here.
_REPORTS_EXEMPT = {
    "workspace_orient": "reports the design's own defaultLengthUnits as a FACT (a zero-input "
                        "orientation read), not a scaled measurement the agent picks units for",
    "mesh_insert": "hand-rolls a WIDER authored-unit set (mm/cm/m/in/ft) than the length UNITS kind - "
                   "a mesh file can be authored in metres or feet",
    "cam_post": "reports NC OUTPUT units via a domain Choice (document/inch/mm) - not a CM_TO_UNIT "
                "length measurement, and it is already a typed enum, not stranded prose",
    "drawing_create": "reports DRAWING display units via a domain Choice (mm/inch) - a drawing display "
                      "setting, not a CM_TO_UNIT length measurement, and already a typed enum",
}


def _is_units_kind(schema) -> bool:
    """True if a 'units' input property is the shared UNITS enum kind - an enum covering mm/cm/in, the
    signature _inputs.UNITS / UnitField / units_property emit. A hand-rolled 'units' string (no enum)
    is NOT the shared kind."""
    if not isinstance(schema, dict):
        return False
    enum = schema.get("enum")
    return isinstance(enum, list) and {"mm", "cm", "in"} <= set(enum)


def _tools_with_report_source():
    """(tool_name, input_props, source) per registered tool. `source` is the MODULE source for a
    single-tool module (so a units payload built in a _slice_* helper still counts) and the HANDLER's
    OWN source for a grandfathered multi-tool file (so one tool is never blamed for a sibling's units
    payload). A '"units":' occurrence in `source` = the tool reports a units field."""
    files = [fn for fn in sorted(os.listdir(TOOLS_DIR)) if fn.endswith(".py") and not fn.startswith("_")]
    if files:
        load_tool(files[0][:-3])                     # bootstraps COMMANDS_DIR onto sys.path first
    from mcpServer.mcp_primitives import registry    # importable only after the bootstrap above
    out = []
    for fn in files:
        mod = load_tool(fn[:-3])
        rt = getattr(mod, "register_tool", None)
        if not callable(rt):
            continue
        registry.reset_registry()
        rt()
        items = list(registry.get_tools())
        module_src = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read()
        for it in items:
            d = it.to_dict()
            props = (d.get("inputSchema") or {}).get("properties", {}) or {}
            src = module_src
            if len(items) > 1:
                try:
                    src = inspect.getsource(it.handler)
                except (OSError, TypeError):
                    src = module_src
            out.append((d.get("name"), props, src))
    return out


def _is_numeric(schema):
    """True for a number/integer, or an array that bottoms out in one (array of number, ...)."""
    if not isinstance(schema, dict):
        return False
    t = schema.get("type")
    if t in ("number", "integer"):
        return True
    if t == "array":
        return _is_numeric(schema.get("items"))
    return False


def _numeric_unit_props(props, path, out):
    for name, schema in props.items():
        if not isinstance(schema, dict):
            continue
        if _is_numeric(schema):
            m = _UNIT.search(schema.get("description") or "")
            if m:
                out.append((f"{path}.{name}", m.group(0), schema.get("description") or ""))
        nested = schema.get("properties")
        if isinstance(nested, dict):
            _numeric_unit_props(nested, f"{path}.{name}", out)


class TestUnitsAreTyped:
    def test_numeric_input_naming_a_unit_has_a_units_selector(self):
        offenders = []
        for it in register_all_tools():
            d = it.to_dict()
            name = d.get("name")
            props = (d.get("inputSchema") or {}).get("properties", {}) or {}
            if "units" in props:                       # tool already exposes a unit selector
                continue
            found = []
            _numeric_unit_props(props, name, found)
            for path, unit, desc in found:
                if path in _EXEMPT:
                    continue
                offenders.append(f"{path}: numeric input says '{unit}' in prose but the tool has no "
                                 f"'units' selector - pair it with _inputs.Distance/UnitField. "
                                 f"(desc: {desc[:70]!r})")
        assert not offenders, (
            "Unit stranded in prose - declare it with a typed kind (Distance + UnitField), or add the "
            "input path to _EXEMPT with a reason:\n  " + "\n  ".join(offenders))

    def test_units_reporting_read_wires_the_units_kind(self):
        # The read-side mirror: a tool that REPORTS a 'units' field must expose the shared UNITS enum
        # kind so the agent can SELECT the units it reads back (scaled via CM_TO_UNIT) - a hand-rolled
        # 'units' string lets the report and the request drift.
        offenders = []
        for name, props, src in _tools_with_report_source():
            if name in _REPORTS_EXEMPT:
                continue
            if '"units":' not in src:                  # this tool does not report a units field
                continue
            if not _is_units_kind(props.get("units")):
                offenders.append(
                    f"{name}: reports a 'units' field but its 'units' input is not the shared "
                    "_inputs.UNITS enum kind - wire *_inputs.UNITS.as_property() (or units_property()), "
                    "or add it to _REPORTS_EXEMPT with a reason.")
        assert not offenders, (
            "A units-reporting read must let the agent choose those units via the shared UNITS kind:\n  "
            + "\n  ".join(offenders))

    def test_the_reporting_lint_bites(self):
        # The discriminator the read-side lint hangs on: the shared enum kind passes, a hand-rolled
        # 'units' string (the drift this catches) does not.
        assert _is_units_kind({"type": "string", "enum": ["mm", "cm", "in"]})
        assert _is_units_kind({"type": "string", "enum": ["mm", "cm", "in", "ft"]})
        assert not _is_units_kind({"type": "string", "description": "mm | cm | in"})
        assert not _is_units_kind(None)


# ── exemption staleness - an entry must still exist and still need its exemption ────────────────

def _stale_exempt_entries(exempt, tool_props):
    """Stale _EXEMPT entries, given {tool_name: input props}: an entry is dead weight when its tool
    is gone, its tool now exposes a 'units' selector, or no numeric input at that path names a unit
    in prose any more - in each case the main check passes without it, so the entry must go."""
    stale = []
    for path, reason in exempt.items():
        assert reason.strip(), f"{path} exemption needs a plain-English reason"
        tool_name = path.split(".", 1)[0]
        props = tool_props.get(tool_name)
        if props is None:
            stale.append(f"{path}: no such tool")
            continue
        if "units" in props:
            stale.append(f"{path}: the tool now exposes a 'units' selector - remove the entry")
            continue
        found = []
        _numeric_unit_props(props, tool_name, found)
        if path not in [p for p, _, _ in found]:
            stale.append(f"{path}: no numeric input naming a unit in prose at this path - remove the entry")
    return stale


def _stale_reports_exempt_entries(exempt, report_rows):
    """Stale _REPORTS_EXEMPT entries, given [(name, props, src)] rows: an entry is dead weight when
    its tool is gone, no longer reports a units field, or already wires the shared UNITS kind - in
    each case the read-side check passes without it, so the entry must go."""
    rows = {name: (props, src) for name, props, src in report_rows}
    stale = []
    for name, reason in exempt.items():
        assert reason.strip(), f"{name} exemption needs a plain-English reason"
        if name not in rows:
            stale.append(f"{name}: no such tool")
            continue
        props, src = rows[name]
        if '"units":' not in src:
            stale.append(f"{name}: no longer reports a units field - remove the entry")
        elif _is_units_kind(props.get("units")):
            stale.append(f"{name}: its 'units' input is already the shared UNITS kind - the read-side "
                         "check passes without the exemption")
    return stale


class TestExemptionsAreNotStale:
    def test_exempt_entries_still_need_their_exemption(self):
        tool_props = {}
        for it in register_all_tools():
            d = it.to_dict()
            tool_props[d.get("name")] = (d.get("inputSchema") or {}).get("properties", {}) or {}
        stale = _stale_exempt_entries(_EXEMPT, tool_props)
        assert not stale, "stale _EXEMPT entries:\n  " + "\n  ".join(stale)

    def test_reports_exempt_entries_still_need_their_exemption(self):
        stale = _stale_reports_exempt_entries(_REPORTS_EXEMPT, _tools_with_report_source())
        assert not stale, "stale _REPORTS_EXEMPT entries:\n  " + "\n  ".join(stale)

    def test_the_staleness_checks_bite(self):
        # _EXEMPT: a live entry (numeric input naming a unit, no selector) is kept...
        live = {"depth": {"type": "number", "description": "cut depth in mm"}}
        assert _stale_exempt_entries({"t.depth": "fixed unit"}, {"t": live}) == []
        # ...a vanished tool, a vanished input, and a now-typed tool each go stale.
        assert _stale_exempt_entries({"gone.depth": "r"}, {"t": live})
        assert _stale_exempt_entries({"t.other": "r"}, {"t": live})
        typed = dict(live, units={"type": "string", "enum": ["mm", "cm", "in"]})
        assert _stale_exempt_entries({"t.depth": "r"}, {"t": typed})
        # _REPORTS_EXEMPT: a live entry (reports units, hand-rolled input) is kept...
        rows = [("t", {"units": {"type": "string"}}, 'payload = {"units": u}')]
        assert _stale_reports_exempt_entries({"t": "domain choice"}, rows) == []
        # ...a vanished tool, a no-longer-reporting tool, and a now-shared-kind tool each go stale.
        assert _stale_reports_exempt_entries({"gone": "r"}, rows)
        assert _stale_reports_exempt_entries({"t": "r"}, [("t", {}, "payload = {}")])
        shared = [("t", {"units": {"type": "string", "enum": ["mm", "cm", "in"]}},
                   'payload = {"units": u}')]
        assert _stale_reports_exempt_entries({"t": "r"}, shared)
