# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every adsk enum family the tools reference is MEASURED - first contact fails loudly.

Guard lints can only defend facts that exist; a new tool touching a new enum family would get no
nudge at all (measured-facts lints stay silent on unmeasured API). This gate closes that hole:
the SAME scraper the live enum-sweep uses lists every referenced family, and each must exist in
the generated live_api_facts.ENUMS. Red here means one command with Fusion running -
``py -3 tests/live/measure_api.py`` - the enum-sweep measures the new family automatically and
the regenerated facts file turns this green with zero hand-edits. The second gate does the same
for BEHAVIOR keys the harness consumes. The third gate is the REVERSE direction: every behavior
key measure_api can EMIT (facts_on_pass and FACT prints) must exist in the generated
live_api_facts.BEHAVIOR - a key renamed in a measurement row without a live regen would
otherwise leave the fakes consuming the orphaned old fact forever."""

import os
import re
import sys

import live_api_facts

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import measure_api  # noqa: E402  the scraper's home - one scraper, two consumers

_BEHAVIOR_KEY = re.compile(r"BEHAVIOR\[\s*\"([a-z0-9_]+)\"\s*\]")


class TestEnumFamiliesMeasured:
    def test_every_referenced_family_is_measured(self):
        missing = [f for f in measure_api.referenced_enum_families()
                   if f not in live_api_facts.ENUMS]
        assert not missing, (
            "Tool code references adsk enum families that live_api_facts.py has not MEASURED. "
            "With Fusion running: py -3 tests/live/measure_api.py  (the enum-sweep measures "
            "them automatically) - then commit the regenerated file. Missing: "
            + ", ".join(missing))

    def test_every_consumed_behavior_key_is_measured(self):
        consumed = set()
        for root, dirs, files in os.walk(TESTS_DIR):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "live")]
            for fn in files:
                if fn.endswith(".py") and fn != "live_api_facts.py":
                    with open(os.path.join(root, fn), encoding="utf-8") as fh:
                        consumed |= set(_BEHAVIOR_KEY.findall(fh.read()))
        missing = sorted(consumed - set(live_api_facts.BEHAVIOR))
        assert not missing, (
            "The harness consumes BEHAVIOR keys live_api_facts.py does not carry - add a "
            "measurement row (facts_on_pass or a FACT line) to tests/live/measure_api.py and "
            "regenerate against live Fusion. Missing: " + ", ".join(missing))


# Emitted keys the generated facts file does not carry YET - each is a measurement-row rename or
# addition awaiting the next live regen (a fully-PASSING py -3 tests/live/measure_api.py rewrites
# BEHAVIOR and empties this table). Shrink-only; the staleness check below fails the moment the
# regen lands the key, so an entry cannot outlive its excuse.
_PENDING_REGEN = {}


def _uncarried_emitted_keys(emitted, carried, pending):
    """Emitted behavior keys that live_api_facts.BEHAVIOR does not carry and no pending-regen
    entry excuses."""
    return sorted(set(emitted) - set(carried) - set(pending))


class TestEmittedBehaviorKeysAreCarried:
    def test_every_emitted_behavior_key_is_carried(self):
        missing = _uncarried_emitted_keys(measure_api.emitted_behavior_keys(),
                                          live_api_facts.BEHAVIOR, _PENDING_REGEN)
        assert not missing, (
            "measure_api.py can emit behavior keys the generated live_api_facts.BEHAVIOR does not "
            "carry - a renamed/new flag in a measurement row needs a live regen (py -3 "
            "tests/live/measure_api.py with Fusion up, commit the regenerated file), or a "
            "reasoned _PENDING_REGEN entry until that run happens. Missing: " + ", ".join(missing))

    def test_pending_regen_entries_are_still_pending(self):
        emitted = set(measure_api.emitted_behavior_keys())
        stale = []
        for key, reason in _PENDING_REGEN.items():
            assert reason.strip(), f"{key} _PENDING_REGEN entry needs a plain-English reason"
            if key not in emitted:
                stale.append(f"{key}: measure_api no longer emits it - remove the entry")
            elif key in live_api_facts.BEHAVIOR:
                stale.append(f"{key}: the regen landed it in BEHAVIOR - remove the entry")
        assert not stale, "stale _PENDING_REGEN entries:\n  " + "\n  ".join(stale)

    def test_the_reverse_check_bites(self):
        # a doctored emitted key with no carried fact and no excuse MUST be flagged...
        assert _uncarried_emitted_keys(["ghost_flag"], {"real_flag": True}, {}) == ["ghost_flag"]
        # ...a carried key and a pending-regen key are not, and order is deterministic.
        assert _uncarried_emitted_keys(["real_flag"], {"real_flag": True}, {}) == []
        assert _uncarried_emitted_keys(["ghost_flag"], {}, {"ghost_flag": "pending"}) == []
        assert _uncarried_emitted_keys(["b", "a"], {}, {}) == ["a", "b"]
