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
"""

import os
import re

from conftest import load_tool, TOOLS_DIR

# a unit token, allowing a leading digit ("5mm") but not a letter ("swimming", "incoming").
_UNIT = re.compile(r"(?<![A-Za-z])(mm|cm|inch(?:es)?|millimet\w*|centimet\w*)(?![A-Za-z])", re.I)

# "tool.input" paths where a bare unit in prose is deliberate (a fixed unit the agent cannot select).
# Shrink-only; each entry needs a reason.
_EXEMPT = {
    # "some_tool.some_input": "reason a fixed unit is correct here",
}


def _all_registered_tools():
    names = [fn[:-3] for fn in sorted(os.listdir(TOOLS_DIR))
             if fn.endswith(".py") and not fn.startswith("_") and fn != "__init__.py"]
    load_tool(names[0])                       # bootstrap sys.path + the mcpServer.tools stub FIRST
    from mcpServer.mcp_primitives import registry
    registry.reset_registry()
    for n in names:
        mod = load_tool(n)
        reg = getattr(mod, "register_tool", None)
        if callable(reg):
            reg()
    return registry.get_tools()


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
        for it in _all_registered_tools():
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
