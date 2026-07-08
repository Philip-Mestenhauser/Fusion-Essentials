# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Measurement: handler docstrings that merely restate the wire description.

The house rule (tools/CLAUDE.md "Module docstrings"): a handler docstring that only restates the
tool's wire description should be one line or omitted - the agent already reads the description, so a
paraphrase of it adds context cost without information. This sizes how many handler docstrings are
mostly a paraphrase of their `*_DESCRIPTION`, as input to a one-time cleanup.

It is deliberately a RATCHET (count <= _BASELINE), not a zero gate: the cleanup is a human pass the
owner has not yet green-lit, so this measures and blocks REGRESSION without forcing that pass now.
Drive _BASELINE down as docstrings are trimmed; it may never rise. A single-`handler`-per-module
shape is measured; grandfathered multi-tool modules (no lone top-level `handler`) are skipped.
"""

import ast
import os
import re

from conftest import TOOLS_DIR

_WORD = re.compile(r"[a-z0-9]+")
# Count of handler docstrings currently mostly-restating their description. Ratchet DOWN only - this
# is a measured backlog (a deferred human cleanup the owner has not green-lit), not an accepted state.
_BASELINE = 62
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
            f"(baseline {_BASELINE}). Trim to one line or omit, then lower _BASELINE:\n  "
            + "\n  ".join(f"{fn} overlap={ov} :: {first}" for fn, ov, first in offenders))
