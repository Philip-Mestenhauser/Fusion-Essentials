# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a handler docstring must not merely restate the tool's wire description.

The house rule (tools/CLAUDE.md "Module docstrings"): a handler docstring that only restates the
tool's wire description should be one line or omitted - the agent already reads the description, so a
paraphrase of it adds context cost without information. A handler whose docstring shares >= _OVERLAP_MIN
of its words with its `*_DESCRIPTION` (over _MIN_WORDS words) is an offender; the count is a ratchet at
_BASELINE and may never rise. A single top-level `handler` is measured; a module with no lone `handler`
(a multi-tool module) is skipped.
"""

import ast
import os
import re

from conftest import TOOLS_DIR

_WORD = re.compile(r"[a-z0-9]+")
# Handler docstrings that paraphrase their wire description. Ratchet: may never rise.
_BASELINE = 0
_OVERLAP_MIN = 0.6      # fraction of the docstring's words also in the description to call it a restatement
_MIN_WORDS = 5          # ignore a terse one-liner - too short to carry independent information anyway


def _tokens(text):
    return set(_WORD.findall((text or "").lower()))


def _handler_doc_and_desc(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    handler_doc, desc = None, []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "handler":
            handler_doc = ast.get_docstring(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.endswith("DESCRIPTION"):
                    desc += [n.value for n in ast.walk(node.value)
                             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return handler_doc, " ".join(desc)


def _restatements():
    offenders = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py") or fn.startswith("_") or fn == "__init__.py":
            continue
        doc, desc = _handler_doc_and_desc(os.path.join(TOOLS_DIR, fn))
        if not doc or not desc:
            continue
        dt = _tokens(doc)
        if len(dt) < _MIN_WORDS:
            continue
        overlap = len(dt & _tokens(desc)) / len(dt)
        if overlap >= _OVERLAP_MIN:
            offenders.append((fn, round(overlap, 2), doc.splitlines()[0][:70]))
    return offenders


class TestDocstringRestatement:
    def test_restatement_count_does_not_regress(self):
        offenders = _restatements()
        assert len(offenders) <= _BASELINE, (
            f"handler docstrings mostly restating the wire description: {len(offenders)} "
            f"(baseline {_BASELINE}). Trim each to one line ('See TOOL_DESCRIPTION.') or omit:\n  "
            + "\n  ".join(f"{fn} overlap={ov} :: {first}" for fn, ov, first in offenders))
