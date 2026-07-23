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

The COMMITTED ``.claude/settings.json`` (the team's default posture) is held to the same truth: its
fusion-wire allow entries must be exactly the registry's read bucket - so adding a read tool forces
the allow-list update its own comment asks for, and a write-kind tool can never sit auto-approved.
"""

import json
import os

import gen_manifest
import gen_posture

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              os.pardir, os.pardir, ".claude", "settings.json")


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


def _fusion_allow_diff(allow_entries, read_names):
    """(missing, extra) between the committed allow list's fusion-wire entries and the registry's
    read bucket. Non-fusion entries (another server's tools, shell rules) are out of scope - the
    invariant governs only what THIS server auto-approves."""
    prefix = gen_posture.WIRE_PREFIX
    committed = {e[len(prefix):] for e in allow_entries if e.startswith(prefix)}
    reads = set(read_names)
    return sorted(reads - committed), sorted(committed - reads)


class TestCommittedSettingsMatchTheReadBucket:
    def test_committed_allow_list_is_exactly_the_read_bucket(self):
        with open(_SETTINGS_PATH, encoding="utf-8") as fh:
            allow = json.load(fh)["permissions"]["allow"]
        reads = gen_posture.buckets(gen_manifest.collect()["tools"])["read"]
        missing, extra = _fusion_allow_diff(allow, reads)
        assert not missing, (
            "read-kind tools missing from .claude/settings.json's allow list - add them (the "
            "committed posture auto-approves exactly the read bucket): " + ", ".join(missing))
        assert not extra, (
            "non-read tools auto-approved in .claude/settings.json - an agent would run them "
            "unattended; remove them (or fix the tool's write= kind if it truly reads): "
            + ", ".join(extra))

    def test_the_diff_bites_both_ways(self):
        reads = ["cam_get", "doc_get"]
        wire = [gen_posture.WIRE_PREFIX + n for n in reads]
        assert _fusion_allow_diff(wire + ["Bash(git status)"], reads) == ([], [])
        assert _fusion_allow_diff(wire[:1], reads) == (["doc_get"], [])
        assert _fusion_allow_diff(wire + [gen_posture.WIRE_PREFIX + "cam_delete"], reads) == (
            [], ["cam_delete"])
