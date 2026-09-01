# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Blurb budget: every shared helper's MAP_BLURB is pinned in a per-module manifest.

gen_manifest splices these blurbs into the tools/CLAUDE.md catalog table, so every one of them is
context an agent loads before authoring a tool - the same kind of session cost a tool description
carries on the wire, which test_wire_budget.py bounds with fleet-level caps.
This is that manifest's shape on this surface: a NAMED entry per module, deliberately no aggregate
constant, so a blurb that grows by a paragraph arrives as a line a reviewer sees rather than as
prose absorbed into a block nobody measures.
"""

import pytest

import gen_manifest

# THE UNIT: characters of the blurb string ``gen_manifest._collect_helpers()`` hands the catalog
# renderer - imported from the generator rather than re-derived, so the lint and the catalog cannot
# disagree about what a blurb IS. That reader takes MAP_BLURB when the module declares one, and the
# first line of the module docstring (capped at 120) when it does not - so a helper carries a
# budget either way and dropping the constant is not a way out from under one. Characters, not
# bytes: the catalog is markdown a reader scans, not a wire payload.
#
# module -> exact blurb length. Regenerate a failing entry from the test's own failure message.
# A NEW helper module: measure it and add the printed entry.
_BLURB_CHARS = {
    "_assembly_detail": 1560,
    "_assert": 171,
    "_cam_common": 3644,
    "_cam_presets": 1312,
    "_cam_read": 984,
    "_common": 4078,
    "_contacts": 435,
    "_data_common": 577,
    "_data_read": 478,
    "_drawing_common": 1752,
    "_export": 2196,
    "_geom": 2186,   # was 1982: the 'address' clause - the symbol is public because model_measure_between._echo consumes it cross-module and the helper-duplication denylist locks the reuse in; the clause is scoped to the MEASURE tools' rows, its only consumers
    "_holder": 85,
    "_inputs": 1148,
    "_joints": 2565,   # +110: names DRIVES_ANY beside the DRIVES_ANGLE/DRIVES_SLIDE pair it unions - the drivable-at-all gate joint_drive's refusal and the as-built pose pointer both consume; a shared constant the catalog must name or the copies come back
    "_materials": 468,
    "_outputs": 91,
    "_pmi": 755,
    "_relations": 527,   # +58: names the shared walk (_common.all_components) and the never-prepend-rootComponent trap the REL-1 doubling came from - the two facts a reuse catalog exists to carry
    "_sketch_detail": 1927,
    "_threads": 220,
    "_view_common": 708,
    "_write_guard": 1258,
}


@pytest.fixture(scope="module")
def blurbs():
    """module -> the length of the blurb the catalog splices, through the generator's extraction."""
    return {h["module"]: len(h["blurb"]) for h in gen_manifest._collect_helpers()}


def _drift(measured, pinned):
    """Every module whose blurb no longer matches its pin, as the manifest lines that fix it."""
    out = []
    for module, size in sorted(measured.items()):
        was = pinned.get(module)
        if was is not None and size != was:
            direction = "GREW" if size > was else "shrank"
            out.append(f'    "{module}": {size},   # was {was} ({direction})')
    return out


def _unpinned(measured, pinned):
    """Every measured module the manifest does not name, as the entry that adds it."""
    return [f'    "{module}": {measured[module]},'
            for module in sorted(set(measured) - set(pinned))]


def _stale(measured, pinned):
    """Every module the manifest names that the catalog no longer carries a blurb for."""
    return sorted(set(pinned) - set(measured))


def test_every_blurb_matches_its_manifest_entry(blurbs):
    drift = _drift(blurbs, _BLURB_CHARS)
    assert not drift, (
        "helper blurb sizes drifted from the manifest. A GROWN blurb: cut it to the symbols and "
        "the WHEN clauses first; if the reuse it teaches genuinely needs the room, update the "
        "entry and say why in the commit message. A SHRUNK blurb: lock the win in. Corrected "
        "entries:\n" + "\n".join(drift))


def test_new_helpers_are_measured_into_the_manifest(blurbs):
    lines = _unpinned(blurbs, _BLURB_CHARS)
    assert not lines, (
        "helper modules missing from the _BLURB_CHARS manifest - measure and add the printed "
        "entry:\n" + "\n".join(lines))


def test_no_stale_manifest_entries(blurbs):
    gone = _stale(blurbs, _BLURB_CHARS)
    assert not gone, (
        "the manifest names helpers the catalog no longer carries a blurb for - drop them: "
        + ", ".join(gone))


def test_the_manifest_measures_the_catalog_generators_own_extraction():
    # The pin is only worth its bytes while it measures the string that reaches the catalog. Every
    # blurb this manifest sizes lands in render_catalog's table, so a lint that grew its own reader
    # could budget a string no agent ever loads.
    helpers = gen_manifest._collect_helpers()
    block = gen_manifest.render_catalog({"kinds": [], "helpers": helpers})
    missing = [h["module"] for h in helpers if h["blurb"].replace("|", "\\|") not in block]
    assert not missing, f"blurbs measured here that the catalog table does not carry: {missing}"
    # one row per module: two helpers under one name would collapse in the fixture's dict, and the
    # second one's blurb would ride into the catalog with no pin behind it.
    assert len(helpers) == len({h["module"] for h in helpers}), "the extraction repeats a module"


def test_the_manifest_checks_bite():
    # a doctored drift, missing entry, and stale entry must each be detectable by the helpers
    assert _drift({"_geom": 12}, {"_geom": 10}) == ['    "_geom": 12,   # was 10 (GREW)']
    assert _drift({"_geom": 8}, {"_geom": 10}) == ['    "_geom": 8,   # was 10 (shrank)']
    assert _drift({"_geom": 10}, {"_geom": 10}) == []
    assert _unpinned({"_new": 40, "_geom": 10}, {"_geom": 10}) == ['    "_new": 40,']
    assert _stale({"_geom": 10}, {"_geom": 10, "_gone": 5}) == ["_gone"]
