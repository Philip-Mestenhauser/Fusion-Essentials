# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Run every doc generator (or every --check) in ONE process.

Each generator pays a full mocked-registry load when run as its own process; importing them here
means that load happens once, so the pre-commit gate stays fast. Same contracts as running them
individually: writes tests/generated/* (and the CLAUDE.md spliced maps), or with --check exits 1
naming what is stale.

    py -3 tests/gen_all.py           # regenerate everything
    py -3 tests/gen_all.py --check   # exit 1 if any generated artifact is stale
"""

import argparse
import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

_GENERATORS = ("gen_manifest", "gen_wiring", "gen_posture", "gen_api_surface")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any generated artifact is stale (writes nothing)")
    args = ap.parse_args()
    worst = 0
    for name in _GENERATORS:
        mod = __import__(name)
        sys.argv = [name + ".py"] + (["--check"] if args.check else [])
        try:
            mod.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
            worst = worst or code
    return worst


if __name__ == "__main__":
    sys.exit(main())
