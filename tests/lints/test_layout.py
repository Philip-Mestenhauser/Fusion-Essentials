# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: the test tree stays organized by KIND.

Two buckets, so the file tree teaches the two-layer testing model:
  tests/unit/   - tests that EXERCISE behavior (per-tool handlers + the shared framework + the server)
  tests/lints/  - tests that READ the codebase to enforce a CONVENTION (this file included)
plus tests/live/ (Fusion-driven scripts, run on demand - never collected by this mock suite) and the
shared harness at tests/ root (conftest.py, the gen_*.py generators, the generated TOOL_MANIFEST/TOOL_POINTER_MAP).

_LINT_TESTS names every convention-enforcing test; anything else belongs in unit/. Adding a lint
means adding its name here AND placing it in lints/ - the gate fails until both are true, so the
split can't silently un-organize as tests are added.
"""

from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent

# The convention-enforcing tests (this file included). Shrink/grow deliberately, never by accident.
_LINT_TESTS = frozenset({
    "test_assert_strength", "test_axis_vectors_shared", "test_banned_vocabulary",
    "test_bespoke_fake_ratchet", "test_dead_code",
    "test_doc_citations", "test_docstring_restatement", "test_enum_families_measured",
    "test_evergreen_no_baggage", "test_fake_shapes_exist", "test_frame_disclosure",
    "test_generated_docs_current", "test_generators", "test_helper_duplication", "test_layout",
    "test_bool_returns_checked",
    "test_material_effect_verified",
    "test_no_duplicate_defs", "test_no_fabricated_fallbacks",
    "test_input_property_names", "test_rename_adoption",
    "test_no_first_match_resolvers", "test_no_hand_cast_product", "test_no_hand_seeded_enums",
    "test_strict_schema",
    "test_occurrence_ref_lint",
    "test_operations_shared",
    "test_output_contracts",
    "test_permission_posture",
    "test_postconditions_declared", "test_tool_autodiscovery", "test_tool_citations",
    "test_tool_naming", "test_tool_verify_complete", "test_unit_coverage_complete",
    "test_units_scaled", "test_units_typed", "test_wire_ascii", "test_wire_budget",
    "test_wire_shape", "test_write_status_annotations",
})


def _test_files():
    return sorted(TESTS.rglob("test_*.py"))


class TestLayout:
    def test_each_test_is_in_its_correct_bucket(self):
        offenders = []
        for path in _test_files():
            rel = path.relative_to(TESTS)
            bucket = rel.parts[0] if len(rel.parts) > 1 else "<tests root>"
            expected = "lints" if path.stem in _LINT_TESTS else "unit"
            if bucket != expected:
                offenders.append(f"{rel} is in '{bucket}', belongs in '{expected}/' "
                                 f"({'a convention lint' if expected == 'lints' else 'a behavior test'})")
        assert not offenders, (
            "Misplaced test files - move them (a convention-enforcing test goes in lints/ and is "
            "named in _LINT_TESTS; a behavior test goes in unit/):\n  " + "\n  ".join(offenders))

    def test_the_bucket_check_bites(self, tmp_path, monkeypatch):
        # a lint-named file parked under unit/ must be reported - the detection, not just the
        # reverse stale-name direction, has a test that goes red when it breaks
        import test_layout as tl
        fake_tests = tmp_path
        (fake_tests / "unit").mkdir()
        (fake_tests / "lints").mkdir()
        misplaced = fake_tests / "unit" / "test_wire_ascii.py"   # a _LINT_TESTS name, wrong bucket
        misplaced.write_text("", encoding="utf-8")
        monkeypatch.setattr(tl, "TESTS", fake_tests)
        try:
            tl.TestLayout().test_each_test_is_in_its_correct_bucket()
        except AssertionError as e:
            assert "test_wire_ascii" in str(e)
        else:
            raise AssertionError("a misplaced lint-named file was not reported")

    def test_no_behavior_tests_under_live(self):
        # tests/live/ holds Fusion-driven scripts; a test_*.py there would be collected by this mock
        # suite and fail with no Fusion. Live checks are run on demand, not pytest-collected.
        live = sorted((TESTS / "live").rglob("test_*.py"))
        assert not live, ("test_*.py under tests/live/ would be collected without Fusion:\n  "
                          + "\n  ".join(str(p.relative_to(TESTS)) for p in live))

    def test_lint_set_has_no_stale_names(self):
        present = {p.stem for p in _test_files()}
        stale = sorted(_LINT_TESTS - present)
        assert not stale, ("_LINT_TESTS names tests that don't exist (renamed/removed?):\n  "
                           + "\n  ".join(stale))
