# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Unit tests for the harness's own PRISTINE SEAM capture (conftest).

load_tool hands back ONE canonical module object per tool for the whole session, so a test that
patches a module-level seam (``app`` / ``design`` / ``target_component`` / ``_design`` / ``_data``)
would leak into the next test using that module. The autouse fixture undoes that by restoring each
loaded module's seams to a recorded PRISTINE value - which is only worth anything if the recorded
value is the AS-IMPORTED one. This pins WHERE that recording happens: at load, not at the first
sweep that happens to see the module.
"""

import sys

import pytest

import conftest

# A tool module re-imported here on purpose - the vehicle for the import ORDER, not itself under
# test. Any module carrying a module-level seam serves: this one binds `target_component` from
# _common and calls no register() at import, so a second execution mints its own Tool/Item and
# changes nothing outside its new module object.
_MODULE = "model_hole"
_FULL = f"mcpServer.tools.{_MODULE}"

_PATCHED = object()


def _seams(mod):
    """The seam attributes `mod` actually carries - what a restore acts on, and what a pristine
    record has to hold."""
    return {a: getattr(mod, a) for a in conftest._SEAM_ATTRS if hasattr(mod, a)}


@pytest.fixture
def freshly_imported(monkeypatch):
    """The tool module imported for the first time INSIDE a test - the order the autouse fixture's
    setup sweep cannot reach, since that sweep has already run by the time a test body executes.

    The session's canonical module object and its recorded seams are dropped through monkeypatch, so
    both are put back afterwards and no other test meets this second copy."""
    conftest.load_tool(_MODULE)                                    # something to restore afterwards
    monkeypatch.delitem(sys.modules, _FULL)
    monkeypatch.delitem(conftest._PRISTINE_SEAMS, _FULL, raising=False)
    return conftest.load_tool(_MODULE)


class TestPristineSeamCapture:
    def test_a_module_imported_inside_a_test_is_recorded_at_load(self, freshly_imported):
        assert _FULL in conftest._PRISTINE_SEAMS, (
            "load_tool recorded no pristine seams for a module it just executed - the first "
            "recording then happens at TEARDOWN, after the body has had its chance to patch them")
        assert conftest._PRISTINE_SEAMS[_FULL] == _seams(freshly_imported)

    def test_the_recorded_seam_set_is_not_empty(self, freshly_imported):
        # the equality above passes vacuously against a module carrying no seam at all, so the
        # vehicle's own seams are pinned here rather than assumed.
        assert conftest._PRISTINE_SEAMS[_FULL], f"{_MODULE} carries no seam - pick another vehicle"

    def test_a_test_body_patch_never_becomes_the_pristine_value(self, freshly_imported):
        # the imperative patch shape the autouse restore exists to undo (tests/CLAUDE.md), applied
        # to a module this very test imported: the recorded value must still be the imported one, or
        # the restore hands the patch to every test that follows.
        mod = freshly_imported
        seam = sorted(_seams(mod))[0]
        as_imported = getattr(mod, seam)
        setattr(mod, seam, _PATCHED)
        assert conftest._pristine_seam(_FULL, mod)[seam] is as_imported
