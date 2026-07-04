"""Lint: the generated docs (SPEC/MANIFEST/tool-wiring + the CLAUDE.md map) match the live tree.

Three scripts derive documentation from the tests/registry/source instead of being hand-maintained:
``gen_spec.py`` (SPEC.md from test names), ``gen_manifest.py`` (MANIFEST.md + the CLAUDE.md map from
the registry), ``gen_wiring.py`` (docs/tool-wiring.md from the tool source). Each ships a ``--check``
mode that exits non-zero if its output would differ from what is committed. Since agents run pytest
constantly but rarely remember to re-run a generator, this test shells all three ``--check`` runs so a
stale doc shows up as a normal test failure instead of silently rotting.
"""

import subprocess
import sys
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(REPO_ROOT, "tests")

_GENERATORS = [
    ("gen_spec.py", "py -3 tests/gen_spec.py"),
    ("gen_manifest.py", "py -3 tests/gen_manifest.py"),
    ("gen_wiring.py", "py -3 tests/gen_wiring.py"),
]


def _run_check(script_name):
    return subprocess.run(
        [sys.executable, os.path.join(TESTS_DIR, script_name), "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )


class TestGeneratedDocsAreCurrent:
    @pytest.mark.parametrize("script_name, regen_command", _GENERATORS)
    def test_generator_check_passes(self, script_name, regen_command):
        proc = _run_check(script_name)
        assert proc.returncode == 0, (
            f"tests/{script_name} reports its generated doc is stale. Regenerate it with "
            f"`{regen_command}` (drop --check) and commit the result.\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
