"""Lint: the generated docs (SPEC/MANIFEST/tool-wiring + the CLAUDE.md map) match the live tree.

Three scripts derive documentation from the tests/registry/source instead of being hand-maintained:
``gen_spec.py`` (SPEC.md from test names), ``gen_manifest.py`` (MANIFEST.md + the CLAUDE.md map from
the registry), ``gen_wiring.py`` (tests/generated/tool-wiring.md from the tool source). ``gen_all.py``
fronts all three in one process and its ``--check`` exits non-zero if any output would differ from
what is committed. Since agents run pytest constantly but rarely remember to re-run a generator,
this test shells that ``--check`` so a stale doc shows up as a normal test failure instead of
silently rotting.
"""

import subprocess
import sys
import os

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/lints/ -> tests/
REPO_ROOT = os.path.dirname(TESTS_DIR)


class TestGeneratedDocsAreCurrent:
    def test_generator_check_passes(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(TESTS_DIR, "gen_all.py"), "--check"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            "a generated doc is stale. Regenerate with `py -3 tests/gen_all.py` and commit the "
            f"result.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
