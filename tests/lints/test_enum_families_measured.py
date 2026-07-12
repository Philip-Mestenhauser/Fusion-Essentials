# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every adsk enum family the tools reference is MEASURED - first contact fails loudly.

Guard lints can only defend facts that exist; a new tool touching a new enum family would get no
nudge at all (measured-facts lints stay silent on unmeasured API). This gate closes that hole:
the SAME scraper the live enum-sweep uses lists every referenced family, and each must exist in
the generated live_api_facts.ENUMS. Red here means one command with Fusion running -
``py -3 tests/live/measure_api.py`` - the enum-sweep measures the new family automatically and
the regenerated facts file turns this green with zero hand-edits. The second gate does the same
for BEHAVIOR keys the harness consumes."""

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
