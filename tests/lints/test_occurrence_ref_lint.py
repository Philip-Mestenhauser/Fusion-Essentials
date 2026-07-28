"""Lint: single-occurrence resolution must go through the shared OccurrenceRef resolver.

An occurrence's `name` is only LOCALLY unique, so a tool that resolves a SINGLE target by "exact
name, else substring-match name" silently grabs the first of several same-named instances instead of
refusing the ambiguity. Every such tool routes through `_inputs._resolve_occurrence` (prefers the
unambiguous `fullPathName`; refuses an ambiguous substring instead of guessing).

This lint locks that in one way: each routed tool must still REFERENCE the shared resolver, so it
can't quietly stop delegating (and regrow a local resolver). Delegating means calling
`_inputs._resolve_occurrence` directly, OR going through the typed `OccurrenceRef`/
`OccurrenceRefList` kind (which calls it internally) - both count.

The companion checks live where they belong: the tools-wide SMELL ban (no substring/first-match
name resolution anywhere under tools/) is `test_no_first_match_resolvers.py`, and the resolver's
own refuse-ambiguity behaviour is pinned by the unit tests in `test_inputs.py`
(TestSharedResolverBehaviour).
"""

import os

from conftest import TOOLS_DIR

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

class TestRoutedToolsStayOnSharedResolver:
    def test_routed_tools_reference_the_shared_resolver(self):
        # Positive check: each fixed tool actually calls the shared resolver - either directly
        # (`_resolve_occurrence`) or via the typed kind that wraps it (`OccurrenceRef`/
        # `OccurrenceRefList`, which call `_resolve_occurrence` internally) - so the tools-wide
        # smell ban (test_no_first_match_resolvers.py) can't pass merely because the tool stopped
        # resolving occurrences at all.
        missing = []
        for name in _ROUTED_TOOLS:
            src = open(os.path.join(TOOLS_DIR, f"{name}.py"), encoding="utf-8").read()
            if "_resolve_occurrence" not in src and "OccurrenceRef" not in src:
                missing.append(name)
        assert not missing, (
            "expected these to call _inputs._resolve_occurrence (directly or via "
            "OccurrenceRef/OccurrenceRefList): " + ", ".join(missing))
