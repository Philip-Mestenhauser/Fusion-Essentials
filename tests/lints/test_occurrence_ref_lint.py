"""Lint: single-occurrence resolution must go through the shared OccurrenceRef resolver.

An occurrence's `name` is only LOCALLY unique, so a tool that resolves a SINGLE target by "exact
name, else substring-match name" silently grabs the first of several same-named instances instead of
refusing the ambiguity. Every such tool routes through `_inputs._resolve_occurrence` (prefers the
unambiguous `fullPathName`; refuses an ambiguous substring instead of guessing).

This lint locks that in two ways:
  1. The tools that were fixed must KEEP delegating — their source must not contain a raw
     `<x>.lower() in <occ>.name.lower()` substring loop again (a regression guard).
  2. The canonical resolver must exist and behave (fullPathName beats a same-named instance; an
     ambiguous bare name errors). A behavioural anchor so the helper can't be gutted.

Delegating means calling `_inputs._resolve_occurrence` directly, OR going through the typed
`OccurrenceRef`/`OccurrenceRefList` kind (which calls it internally) - both count.

Deliberately NOT flagged (justified substring matches, different shape/domain):
  - view_inspect: MULTI-match isolate/show/hide ("hide all bolts") — returns a
    LIST of every match, not one guessed instance.
  - cam_show_toolpath: matches CAM operations (not occurrences).
  - doc_lifecycle: matches documents by name (not occurrences).
"""

import os
import re

from conftest import load_tool, TOOLS_DIR

# Tools whose SINGLE-occurrence resolution was routed through _inputs._resolve_occurrence. Each must
# stay on the shared resolver — i.e. not hand-roll a substring-on-occurrence-name loop again.
_ROUTED_TOOLS = (
    "assembly_transform",
    "assembly_joints_advanced",
    "model_arrange",
    "model_pattern",
    "joint_create_edit",
    "view_screenshot",
    "view_section",
)

# A raw substring match of a search term against an occurrence's .name — the wrong-instance smell.
_SUBSTRING_NAME = re.compile(r"\.lower\(\)\s*in\s+.*\.name", re.I)


class TestRoutedToolsStayOnSharedResolver:
    def test_no_routed_tool_hand_rolls_a_substring_name_match(self):
        offenders = []
        for name in _ROUTED_TOOLS:
            src = open(os.path.join(TOOLS_DIR, f"{name}.py"), encoding="utf-8").read()
            for i, line in enumerate(src.splitlines(), 1):
                if _SUBSTRING_NAME.search(line):
                    offenders.append(f"{name}.py:{i}: {line.strip()}")
        assert not offenders, (
            "These tools must resolve a single occurrence via _inputs._resolve_occurrence (the "
            "fullPathName-preferring, ambiguity-refusing resolver), NOT a substring-on-name loop:\n"
            + "\n".join(offenders)
        )

    def test_routed_tools_reference_the_shared_resolver(self):
        # Positive check: each fixed tool actually calls the shared resolver - either directly
        # (`_resolve_occurrence`) or via the typed kind that wraps it (`OccurrenceRef`/
        # `OccurrenceRefList`, which call `_resolve_occurrence` internally) - so the negative test
        # above can't pass merely because the tool stopped resolving occurrences at all.
        missing = []
        for name in _ROUTED_TOOLS:
            src = open(os.path.join(TOOLS_DIR, f"{name}.py"), encoding="utf-8").read()
            if "_resolve_occurrence" not in src and "OccurrenceRef" not in src:
                missing.append(name)
        assert not missing, (
            "expected these to call _inputs._resolve_occurrence (directly or via "
            "OccurrenceRef/OccurrenceRefList): " + ", ".join(missing))

    def test_the_lint_bites(self):
        # prove the substring-name regex catches a loose containment test and skips the correct
        # exact-match shape (the resolver's own comparison, which is never ambiguous).
        assert _SUBSTRING_NAME.search("if want.lower() in occ.name.lower():")
        assert not _SUBSTRING_NAME.search("if nm.lower() == want:")


class TestSharedResolverBehaviour:
    """A behavioural anchor: the resolver the lint points everyone at must actually refuse ambiguity."""

    def _seam(self, *occs):
        inp = load_tool("_inputs")

        class _Root:
            allOccurrences = list(occs)

        class _Design:
            rootComponent = _Root()
        inp._common.design = lambda: _Design()
        return inp

    def test_fullpath_beats_a_same_named_instance(self):
        from types import SimpleNamespace
        a = SimpleNamespace(name="Bolt:1", fullPathName="Sub-A:1+Bolt:1")
        b = SimpleNamespace(name="Bolt:1", fullPathName="Sub-B:1+Bolt:1")
        inp = self._seam(a, b)
        occ, err = inp._resolve_occurrence("t", "Sub-B:1+Bolt:1")
        assert err is None and occ is b

    def test_ambiguous_bare_name_errors(self):
        from types import SimpleNamespace
        a = SimpleNamespace(name="Bolt:1", fullPathName="Sub-A:1+Bolt:1")
        b = SimpleNamespace(name="Bolt:1", fullPathName="Sub-B:1+Bolt:1")
        inp = self._seam(a, b)
        occ, err = inp._resolve_occurrence("t", "Bolt")
        assert occ is None and "ambiguous" in err.lower()
