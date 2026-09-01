# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Blurb budget: the shared-helper catalog is bounded per entry and across the catalog.

``gen_manifest`` splices every helper's blurb into the ``tools/CLAUDE.md`` catalog table, and that
table is context an agent loads before it authors a tool - the session-cost twin of the tools/list
payload ``test_wire_budget.py`` bounds. A blurb earns its characters by naming the symbols to reuse
and saying when to reach for each; the mechanism behind a clause belongs at the symbol, in its
behavior test, or in VERIFIED_API_FACTS.md, where it is read by whoever opens that code.

THE UNIT is CHARACTERS of the blurb string ``gen_manifest._collect_helpers()`` hands the catalog
renderer - imported from the generator rather than re-derived, so the lint and the catalog cannot
disagree about what a blurb IS. That reader takes ``MAP_BLURB`` when the module declares one and
the first line of the module docstring (capped at 120) when it does not, so a helper carries a
budget either way and deleting the constant is not a way out from under one. Characters, not bytes:
the catalog is markdown a reader scans, not a wire payload.

Both limits are structural, so no helper module is named anywhere in this file: no blurb's size is
recorded here, by design. A slimmed or reworded blurb moves nothing to re-pin, and a failure prints
the current catalog total and every blurb's size - all of it computed at failure time.
"""

import pytest

import gen_manifest

# ONE blurb's ceiling, and the whole catalog's. The per-blurb cap bounds the worst single entry;
# the catalog total catches a fleet-wide creep no single entry is remarkable for. Raising either
# number is the owner's decision - a red bar is answered by slimming the blurbs the failure names,
# not by moving the line.
PER_BLURB_BUDGET_CHARS = 2_000
CATALOG_BUDGET_CHARS = 20_000


@pytest.fixture(scope="module")
def blurbs():
    """module -> the length of the blurb the catalog splices, through the generator's extraction."""
    return _measured({h["module"]: h["blurb"] for h in gen_manifest._collect_helpers()})


def _measured(catalog):
    """module -> blurb LENGTH: the one reduction from catalog text to the budgeted number, so a
    synthetic case is measured by exactly the rule the real catalog is."""
    return {module: len(blurb) for module, blurb in catalog.items()}


def _sizes(sizes):
    """(module, size) for every helper, heaviest first - the order a remedy is read in."""
    return sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))


def _over_cap(sizes):
    """module -> size, for every blurb over the per-blurb ceiling."""
    return {module: n for module, n in sizes.items() if n > PER_BLURB_BUDGET_CHARS}


def _report(sizes):
    """What a failed limit prints, every line of it computed HERE at failure time.

    The catalog total and the per-blurb cap fail for different reasons and share one remedy view:
    which blurbs are heavy, in order. No size is recorded in this file, so this listing is the only
    per-helper weight there is - a failure that named none of them would say only that the catalog
    is too big."""
    total = sum(sizes.values())
    over = _over_cap(sizes)
    lines = ", ".join(f"{module}: {n:,}" for module, n in _sizes(sizes))
    head = (f"helper catalog: {total:,} characters over {len(sizes)} blurbs against a budget of "
            f"{CATALOG_BUDGET_CHARS:,}, per-blurb ceiling {PER_BLURB_BUDGET_CHARS:,}.")
    if over:
        head += ("\n  over the per-blurb ceiling: "
                 + ", ".join(f"{module}: {n:,}" for module, n in _sizes(over)))
    return (head + "\n  every blurb, heaviest first: " + lines
            + "\n  cut a blurb to its symbol names plus one when-to-use clause each; the mechanism "
              "belongs at the symbol or in its behavior test.")


def test_no_blurb_exceeds_the_per_blurb_ceiling(blurbs):
    assert not _over_cap(blurbs), _report(blurbs)


def test_the_catalog_total_stays_within_its_budget(blurbs):
    assert sum(blurbs.values()) <= CATALOG_BUDGET_CHARS, _report(blurbs)


def test_the_budget_measures_the_catalog_generators_own_extraction(blurbs):
    # The budget is only worth its bytes while it measures the string that reaches the catalog.
    # Every blurb sized here lands in render_catalog's table, so a lint that grew its own reader
    # could budget a string no agent ever loads.
    helpers = gen_manifest._collect_helpers()
    block = gen_manifest.render_catalog({"kinds": [], "helpers": helpers})
    missing = [h["module"] for h in helpers if h["blurb"].replace("|", "\\|") not in block]
    assert not missing, f"blurbs measured here that the catalog table does not carry: {missing}"
    # one row per module: two helpers under one name would collapse in the fixture's dict, and the
    # second one's blurb would ride into the catalog unmeasured.
    assert len(helpers) == len({h["module"] for h in helpers}), "the extraction repeats a module"
    # the budgeted NUMBER is that same blurb's length: without this the fixture could measure a
    # string of its own and both limits would bound something no agent ever loads.
    assert blurbs == {h["module"]: len(h["blurb"]) for h in helpers}


def test_the_per_blurb_ceiling_bites():
    at_the_line = _measured({"at_the_line": "b" * PER_BLURB_BUDGET_CHARS})
    over = _measured({"at_the_line": "b" * PER_BLURB_BUDGET_CHARS,
                      "one_char_over": "b" * (PER_BLURB_BUDGET_CHARS + 1)})
    assert _over_cap(at_the_line) == {}, "a blurb exactly AT the ceiling is legal"
    assert _over_cap(over) == {"one_char_over": PER_BLURB_BUDGET_CHARS + 1}
    test_no_blurb_exceeds_the_per_blurb_ceiling(at_the_line)
    with pytest.raises(AssertionError, match="one_char_over"):
        test_no_blurb_exceeds_the_per_blurb_ceiling(over)


def test_the_catalog_total_bites_on_catalog_wide_growth():
    # twenty blurbs sharing the budget exactly: legal. One character on each and no single blurb is
    # remarkable - only the total moves, which is the creep this limit exists for.
    each = CATALOG_BUDGET_CHARS // 20
    fleet = _measured({f"_h{i}": "b" * each for i in range(20)})
    assert sum(fleet.values()) == CATALOG_BUDGET_CHARS
    test_the_catalog_total_stays_within_its_budget(fleet)          # exactly at it - legal
    grown = _measured({f"_h{i}": "b" * (each + 1) for i in range(20)})
    assert _over_cap(grown) == {}, "the growth is invisible to the per-blurb ceiling"
    with pytest.raises(AssertionError, match=f"{CATALOG_BUDGET_CHARS + 20:,} characters"):
        test_the_catalog_total_stays_within_its_budget(grown)


def test_a_shrink_or_a_reword_passes_with_nothing_to_edit_here():
    # the limits are structural - no helper is named in this file - so slimming a blurb, or
    # rewording one at the same length, passes on its own.
    each = CATALOG_BUDGET_CHARS // 20
    fleet = _measured({f"_h{i}": "b" * each for i in range(20)})
    slimmer = _measured({f"_h{i}": "b" * (each - 100) for i in range(20)})
    reworded = _measured({f"_h{i}": "x" * each for i in range(20)})
    for variant in (slimmer, reworded):
        test_the_catalog_total_stays_within_its_budget(variant)
        test_no_blurb_exceeds_the_per_blurb_ceiling(variant)
    assert sum(reworded.values()) == sum(fleet.values()), "a same-length reword moves no number"
    assert sum(slimmer.values()) < sum(fleet.values()), "a shrink lowers the total"


def test_the_report_names_every_blurb_heaviest_first():
    # the failure message is the whole remedy: with no size recorded anywhere in this file, a red
    # limit that named no helper would leave nothing to act on.
    sizes = _measured({"_light": "b" * 10, "_heavy": "b" * (PER_BLURB_BUDGET_CHARS + 5),
                       "_mid": "b" * 500})
    report = _report(sizes)
    assert f"{PER_BLURB_BUDGET_CHARS + 515:,} characters over 3 blurbs" in report, report
    assert f"over the per-blurb ceiling: _heavy: {PER_BLURB_BUDGET_CHARS + 5:,}" in report, report
    assert report.index("_heavy") < report.index("_mid") < report.index("_light"), report
