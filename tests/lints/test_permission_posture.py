"""Lint: the generated permission presets NEVER auto-allow a hard-to-reverse tool.

``gen_posture.py`` emits ready-to-paste Claude Code ``settings.json`` presets straight from the
registry's write= truth: reads auto-allow, writes ask, destructive writes ask/deny, and the
arbitrary-code hatch (``sys_execute_script``) is denied everywhere. The load-bearing SECURITY
invariant is that no bucket rule ever routes a destructive-kind tool - above all the script hatch -
into an allow list. A generator refactor that miscategorised one tool would silently hand an agent
unattended delete/close/arbitrary-code power, and a freshness check alone would not catch it (the
committed doc would match the wrong-but-consistent generator). This lint asserts the invariant
against the presets the generator actually builds, and proves it bites on a doctored bad preset.

FRESHNESS is already covered: ``gen_posture`` is registered in ``gen_all.py``'s generator list, and
``test_generated_docs_current.py`` shells ``gen_all.py --check`` - so a stale PERMISSION_POSTURE.md
fails there, naming ``py -3 tests/gen_all.py`` to regenerate. This lint does NOT duplicate that gate;
it enforces the one thing freshness cannot: that the generator's OWN output is safe.
"""

import gen_manifest
import gen_posture


def _must_never_auto_allow(tools):
    """The registry-truth set of tool names that must never appear in ANY allow list: every
    destructive-kind tool, plus the arbitrary-code hatch (which is destructive too, named explicitly
    so the intent survives even if its kind were ever mislabeled)."""
    banned = {t["name"] for t in tools if t["write"] == "destructive"}
    banned.add(gen_posture.SCRIPT_HATCH)
    return banned


def _auto_allow_violations(presets, banned):
    """Every (preset, tool) pair where a banned tool leaked into that preset's allow list. Empty ==
    the invariant holds. Wire names are stripped back to bare tool names for the membership test."""
    prefix = gen_posture.WIRE_PREFIX
    violations = []
    for preset_name, preset in presets.items():
        for wire in preset.get("allow", []):
            name = wire[len(prefix):] if wire.startswith(prefix) else wire
            if name in banned:
                violations.append((preset_name, name))
    return violations


class TestPermissionPostureNeverAutoAllowsDestructive:
    def test_no_preset_auto_allows_a_destructive_tool_or_the_script_hatch(self):
        tools = gen_manifest.collect()["tools"]
        presets = gen_posture.build_presets(tools)
        banned = _must_never_auto_allow(tools)
        violations = _auto_allow_violations(presets, banned)
        assert not violations, (
            "gen_posture put a destructive-kind tool (or sys_execute_script) into an allow list - an "
            "agent would run it unattended. Fix the bucket routing in gen_posture.build_presets:\n  "
            + "\n  ".join(f"{p}: {n}" for p, n in violations))

    def test_the_script_hatch_is_denied_in_every_preset(self):
        tools = gen_manifest.collect()["tools"]
        presets = gen_posture.build_presets(tools)
        hatch = gen_posture.WIRE_PREFIX + gen_posture.SCRIPT_HATCH
        for name, preset in presets.items():
            assert hatch in preset.get("deny", []), (
                f"{name}: {gen_posture.SCRIPT_HATCH} must be explicitly denied, not left to fall "
                "through to a prompt")

    def test_the_check_bites_on_a_doctored_allow_list(self):
        # Doctor a preset the way a broken generator would: slip a real destructive tool into allow.
        # The checker MUST flag it - otherwise it is decoration that would pass any output.
        banned = {"cam_delete", gen_posture.SCRIPT_HATCH}
        good = {"conservative": {"allow": [gen_posture.WIRE_PREFIX + "cam_get"], "ask": [], "deny": []}}
        assert _auto_allow_violations(good, banned) == []          # clean output is not flagged

        bad = {"conservative": {"allow": [gen_posture.WIRE_PREFIX + "cam_delete"], "ask": [], "deny": []}}
        assert _auto_allow_violations(bad, banned) == [("conservative", "cam_delete")]

        hatch_leak = {"modeling": {"allow": [gen_posture.WIRE_PREFIX + gen_posture.SCRIPT_HATCH]}}
        assert _auto_allow_violations(hatch_leak, banned) == [("modeling", gen_posture.SCRIPT_HATCH)]
