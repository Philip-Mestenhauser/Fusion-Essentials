# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a tool name cited in the constitution docs resolves to a registered tool.

A doc that names tools to teach the surface ("data_get reads the Data API", "convert with
mesh_to_brep") rots when a tool is renamed or folded away: the reader is sent to a tool that no
longer exists. This resolves every tool-SHAPED token cited in the docs against the LIVE registry and
fails on a miss - the tool-name analog of the file resolver in test_doc_citations.py, and together
the in-house, dependency-free equivalent of a doc-build cross-reference check, but against runtime
truth instead of symbols a doc tool happens to know.

SCOPE is the constitution docs, not source. In source, a tool-shaped token can't be told apart from a
result-dict key or a param name (`sketch_name`, `joint_count`, `saved_to_cloud` are all family_word
snake_case), so a source scan is all false positives - a cross-tool redirect in a wire string is
verified by code review, not here. In docs, tool names are what get cited, so the signal is clean.

A token counts as a tool citation when it is snake_case AND either its first segment is a known tool
family or it is a converter shaped X_to_Y. `known` (registered tools + every tool/helper MODULE
basename) and the shrink-only _NOT_A_TOOL table (a helper function or an action value a doc names on
purpose) keep legitimate non-tool references from flagging.
"""

import re

from conftest import register_all_tools
# One membership for both citation lints, defined beside the file resolver this is the tool-name
# analog of - never a second copy that can drift to a different set of docs.
from test_doc_citations import CONSTITUTION_DOCS, MCP, REPO

_SNAKE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
_INLINE_CODE = re.compile(r"`([^`]+)`")

# tool-shaped tokens a doc names on purpose that are NOT tools (a helper function, or an action= value
# of an action-dispatched tool). Shrink-only; each needs a reason.
_NOT_A_TOOL = {
    "find_setup": "a _cam_common helper that resolves a CAM setup by exact name, not a tool",
    "find_operation": "a _cam_common helper that resolves a CAM operation by exact name, not a tool",
}


def _registered_names():
    return {it.to_dict().get("name") for it in register_all_tools()}


def _module_basenames():
    # a grandfathered module (data_ops, doc_lifecycle) or helper (_cam_common) is family-shaped but is
    # a FILE a doc legitimately names, not a dangling tool - never flag one.
    return {p.stem for p in MCP.rglob("*.py")}


def _is_tool_citation(tok, families):
    return tok.split("_", 1)[0] in families or "_to_" in tok


class TestToolCitations:
    def test_cited_tool_names_are_registered(self):
        registered = _registered_names()
        families = {n.split("_", 1)[0] for n in registered}
        stems = _module_basenames()
        # helper modules are cited WITH their leading underscore (`_cam_common`) but tokenize without
        # it, so admit the stripped form too; a truly stale `data_read.py` is owned by the file lint.
        known = registered | stems | {s.lstrip("_") for s in stems}
        offenders = []
        for doc in CONSTITUTION_DOCS:
            if not doc.exists():
                continue
            code = " ".join(m.group(1) for m in _INLINE_CODE.finditer(doc.read_text(encoding="utf-8")))
            for tok in sorted(set(_SNAKE.findall(code))):
                if tok in known or tok in _NOT_A_TOOL:
                    continue
                if _is_tool_citation(tok, families):
                    offenders.append(f"{doc.relative_to(REPO)} cites `{tok}` - not a registered tool")
        assert not offenders, (
            "A doc cites a tool name no registered tool answers to (rename to the current tool, or add "
            "a genuine helper/action to _NOT_A_TOOL with a reason):\n  " + "\n  ".join(offenders))

    def test_not_a_tool_entries_still_cited_and_still_not_tools(self):
        # both staleness directions for the exemption table: an entry that became a real tool now
        # SHADOWS the check; an entry no doc cites anymore is dead weight. Either way, drop it.
        registered = _registered_names()
        cited = set()
        for doc in CONSTITUTION_DOCS:
            if doc.exists():
                code = " ".join(m.group(1)
                                for m in _INLINE_CODE.finditer(doc.read_text(encoding="utf-8")))
                cited |= set(_SNAKE.findall(code))
        stale = []
        for tok, reason in _NOT_A_TOOL.items():
            assert reason.strip(), f"_NOT_A_TOOL: {tok} needs a plain-English reason"
            if tok in registered:
                stale.append(f"{tok}: now a registered tool - drop the entry")
            elif tok not in cited:
                stale.append(f"{tok}: no doc cites it anymore - drop the entry")
        assert not stale, "stale _NOT_A_TOOL entries:\n  " + "\n  ".join(stale)
