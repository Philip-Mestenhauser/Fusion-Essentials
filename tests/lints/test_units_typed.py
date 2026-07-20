# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a length/coordinate input carries its unit in a typed selector, not in loose prose.

The north star (CLAUDE.md "Input kinds"): a fact about an input - here, what unit a number is in -
belongs in the typed surface an agent must consume to call the tool, not asserted in a description
string that nothing checks. A numeric input whose description names a unit (mm/cm/inch) while its
owning tool exposes no 'units' selector is a unit fact stranded in prose: an agent that learned
"pass units=cm" from a sibling tool gets a silently wrong-by-a-factor result with no feedback. The
fix is to pair the number with the Distance + UnitField kinds from _inputs.py (which add the 'units'
selector), so the unit is declared and resolved rather than asserted.

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
