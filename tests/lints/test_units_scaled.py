# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: unit<->cm conversion routes through _common.scale, never a re-inlined or copied table.

`_common.scale(units)` IS exactly `UNIT_TO_CM.get((units or "mm").strip().lower())`. A tool that writes
that expression itself, or keeps its own copy of the UNIT_TO_CM table, diverges from the single source
the moment the shared table changes (a new unit, a corrected factor). Companion to `test_units_typed`,
which polices the input side.

Two clean signals, banned outside _common.py:
  1. raw `UNIT_TO_CM` access (`.get(` or `[`) - call `_common.scale(units)` instead;
  2. a dict literal mapping `"mm"` and `"cm"` to numbers - a copy of `UNIT_TO_CM`.

A hardcoded scalar conversion (`* 10` / `/ 10`) has no clean signature and is NOT caught here - only
a human read of the arithmetic catches those.
"""

import ast
import os
import re

from conftest import TOOLS_DIR

_HOME = "_common.py"
_RAW_ACCESS = re.compile(r"\bUNIT_TO_CM\s*[.\[]")


def _tool_files():
    return [fn for fn in sorted(os.listdir(TOOLS_DIR)) if fn.endswith(".py") and fn != _HOME]


class TestUnitsScaled:
    def test_no_raw_unit_to_cm_access_outside_common(self):
        offenders = []
        for fn in _tool_files():
            src = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read()
            for i, line in enumerate(src.splitlines(), 1):
                if _RAW_ACCESS.search(line):
                    offenders.append(f"{fn}:{i}: {line.strip()}")
        assert not offenders, (
            "unit conversion re-inlines _common.scale() via raw UNIT_TO_CM access - call "
            "`_common.scale(units)` (unit->cm) or `_common.CM_TO_UNIT[units]` (cm->unit) instead:\n  "
            + "\n  ".join(offenders))

    def test_no_local_unit_table_copy_outside_common(self):
        offenders = []
        for fn in _tool_files():
            path = os.path.join(TOOLS_DIR, fn)
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                numeric = node.values and all(
                    isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) for v in node.values)
                if "mm" in keys and "cm" in keys and numeric:
                    offenders.append(f"{fn}:{getattr(node, 'lineno', '?')}: local unit-factor table "
                                     "(keys 'mm'+'cm' -> numbers)")
        assert not offenders, (
            "a local copy of the unit-factor table diverges from _common.UNIT_TO_CM - import it (or use "
            "_common.scale/CM_TO_UNIT):\n  " + "\n  ".join(offenders))

    def test_the_lint_bites(self):
        # prove the raw-access regex catches a direct subscript/attribute hit and skips a longer
        # identifier that merely starts with the same prefix.
        assert _RAW_ACCESS.search("k = UNIT_TO_CM['mm']")
        assert _RAW_ACCESS.search("k = UNIT_TO_CM.get(units)")
        assert not _RAW_ACCESS.search("k = UNIT_TO_CMX['mm']")
