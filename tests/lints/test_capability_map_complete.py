# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: sys_capability_map's authored _FAMILY rows cover EXACTLY the registry's families, and
every entry tool they name is a registered tool.

The capability map is the runtime discovery surface - the first thing a cold agent reads to learn
what this server can do. Its handler keeps an unmapped family visible with a generic fallback row,
so a gap here is not invisibility; it is a family the server ships without an authored one-line
summary or a chosen entry tool, which is a discovery surface quietly degrading. A hand-written
enumeration of a registry population gets a gate against that population: a NEW family is red here
until its authored row exists, a REMOVED family is red until its row dies, and a renamed or
deleted entry tool is red the moment the registry stops carrying it.
"""

from conftest import load_tool, register_all_tools

cm = load_tool("sys_capability_map")


def _registry():
    items = register_all_tools()
    names = {it.get_name() for it in items}
    families = {cm._family_of(n) for n in names}
    return names, families


class TestCapabilityMapComplete:
    def test_every_registry_family_has_an_authored_row(self):
        _, families = _registry()
        missing = sorted(families - set(cm._FAMILY))
        assert not missing, (
            "these registered tool families have NO authored row in sys_capability_map._FAMILY - "
            "agents get only the generic fallback summary for them; add a one-line summary + entry "
            "tool per family:\n  " + "\n  ".join(missing))

    def test_no_authored_row_outlives_its_family(self):
        _, families = _registry()
        stale = sorted(set(cm._FAMILY) - families)
        assert not stale, (
            "these _FAMILY rows name families the registry no longer carries - delete the row or "
            "restore the family:\n  " + "\n  ".join(stale))

    def test_every_authored_entry_tool_is_registered(self):
        names, _ = _registry()
        bad = sorted(f"{fam} -> {entry}" for fam, (_s, entry) in cm._FAMILY.items()
                     if entry not in names)
        assert not bad, (
            "these _FAMILY rows point at an entry tool the registry does not carry (renamed or "
            "deleted) - re-point the row:\n  " + "\n  ".join(bad))
