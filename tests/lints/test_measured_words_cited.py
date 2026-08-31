# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a conftest docstring that SHOUTS "MEASURED" names the measurement row behind the claim.

The shared fakes in conftest.py encode live API facts, and the load-bearing ones say so in capitals
- MEASURED, LIVE-MEASURED. Capitals are a claim about PROVENANCE, and a provenance claim needs
something that fails when it stops holding: a renamed row, a re-measured fact, and a claim that
never entered the registry all read identically to a human reader.

The registry is measure_api.py. Its module-level ``ROWS`` is a list of measurement-row dicts, and
each row's ``id`` key IS the claim id - the same string VERIFIED_API_FACTS.md prints in its "claim
id" column and ``--only`` selects a row by. This lint IMPORTS that list rather than scraping the
source, so an id renamed in the registry turns every docstring that cited it red.

THE RULE, one rule: every top-level class/def in conftest.py whose DOCSTRING contains MEASURED must
name, in that same docstring, at least one claim id that resolves against ``ROWS``. The scope is the
shouted marker in a docstring - lowercase "measured" is ordinary prose, and a comment is not a
docstring - so there is a single check to satisfy and nothing to tune. A citation counts as a whole
hyphenated TOKEN, so a longer phrase that merely opens with an id is not one.

A shouted claim no row carries is neither deleted nor handed a citation it cannot support: it goes
in _PENDING_CITATION, where a reader sees which measured words are standing on nothing.
"""

import ast
import os
import re
import sys

import _corpus

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFTEST = os.path.join(TESTS_DIR, "conftest.py")
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import measure_api  # noqa: E402  the claim-id registry - ROWS, one dict per row, keyed 'id'

# The shouted marker. LIVE-MEASURED carries the same token, so one pattern covers both spellings.
_SHOUTED = re.compile(r"\bMEASURED\b")

# A claim id's grammar: lowercase words joined by hyphens (point3d-vectorto,
# cam-ncprogram-filtered-ops-tie-by-operationid). Reading whole TOKENS rather than testing for a
# substring is what keeps a longer hyphenated phrase from passing as a citation of the id it opens
# with, in either direction.
_ID_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")

# Shouted sites no measurement row backs. Each stays in its docstring, uncited, until a row measures
# the claim - inventing a citation would launder the words the rule exists to check. Shrink-only:
# the staleness check below fails the moment a site earns a citation, loses the marker, or goes.
_PENDING_CITATION = {
    "entity_proxy": "orphan - no row backs this yet",
    "body_proxy": "orphan - no row backs this yet",
    "make_inspection_cam": "orphan - no row backs this yet",
    "FakeOccurrence": "orphan - no row backs this yet",
}


def claim_ids():
    """Every claim id the live measurement registry defines."""
    return {row["id"] for row in measure_api.ROWS}


def measured_sites(path=CONFTEST):
    """{name: docstring} for each top-level class/def whose docstring shouts MEASURED."""
    sites = {}
    for node in _corpus.tree(path).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            if _SHOUTED.search(doc):
                sites[node.name] = doc
    return sites


def cited_ids(docstring, known):
    """The known claim ids a docstring names, each as a whole hyphenated token."""
    return sorted(set(_ID_TOKEN.findall(docstring)) & set(known))


def _uncited_sites(sites, known, pending):
    """Shouted sites that resolve no claim id and that no pending entry excuses."""
    return sorted(name for name, doc in sites.items()
                  if not cited_ids(doc, known) and name not in pending)


class TestMeasuredWordsCited:
    def test_every_shouted_docstring_cites_a_measurement_row(self):
        uncited = _uncited_sites(measured_sites(), claim_ids(), _PENDING_CITATION)
        assert not uncited, (
            "conftest.py docstrings shout MEASURED without naming a claim id measure_api.py "
            "carries, so nothing fails when the words outlive the measurement. Name the row that "
            "backs each claim (VERIFIED_API_FACTS.md's 'encoded in' column usually names the fake) "
            "- or, when no row carries the claim, add a measurement row and measure it live (py -3 "
            "tests/live/measure_api.py with Fusion up), parking the site in _PENDING_CITATION with "
            "a reason until that run lands:\n  " + "\n  ".join(uncited))

    def test_pending_citation_entries_are_still_pending(self):
        sites = measured_sites()
        known = claim_ids()
        stale = []
        for name, reason in _PENDING_CITATION.items():
            assert reason.strip(), f"{name} _PENDING_CITATION entry needs a plain-English reason"
            if name not in sites:
                stale.append(f"{name}: no top-level conftest site shouts MEASURED under that name "
                             "- remove the entry")
            elif cited_ids(sites[name], known):
                stale.append(f"{name}: the docstring now cites "
                             + ", ".join(cited_ids(sites[name], known)) + " - remove the entry")
        assert not stale, "stale _PENDING_CITATION entries:\n  " + "\n  ".join(stale)

    def test_every_claim_id_is_citable(self):
        # A citation is read with the token grammar above, so an id that grammar cannot express
        # could never be cited - the rule would be unsatisfiable for whichever fake encodes it.
        uncitable = sorted(i for i in claim_ids() if not _ID_TOKEN.fullmatch(i))
        assert not uncitable, (
            "measure_api.py carries claim ids that are not lowercase hyphenated tokens, so no "
            "docstring can cite them - rename the row, or widen _ID_TOKEN to the new shape: "
            + ", ".join(uncitable))

    def test_the_citation_gate_bites(self):
        known = {"point3d-vectorto", "find-entity-token-multi"}
        # a shouted docstring naming no id MUST be flagged, whether it cites nothing at all or
        # cites a hyphenated phrase that is not an id...
        assert _uncited_sites({"FakeThing": "MEASURED: it answers None."}, known, {}) == ["FakeThing"]
        assert _uncited_sites({"FakeThing": "LIVE-MEASURED, see the de-dup note."},
                              known, {}) == ["FakeThing"]
        # ...a real citation clears it, under either spelling of the marker...
        assert _uncited_sites({"FakeThing": "MEASURED (point3d-vectorto)."}, known, {}) == []
        assert _uncited_sites({"FakeThing": "LIVE-MEASURED: find-entity-token-multi."},
                              known, {}) == []
        # ...an id is matched as a WHOLE token, so neither a longer phrase built on one nor a
        # fragment of one is a citation...
        assert cited_ids("see point3d-vectorto-and-back", known) == []
        assert cited_ids("see find-entity-token", known) == []
        assert cited_ids("see point3d-vectorto.", known) == ["point3d-vectorto"]
        # ...a pending entry excuses exactly its own site, and the order is deterministic.
        assert _uncited_sites({"FakeThing": "MEASURED."}, known, {"FakeThing": "orphan"}) == []
        assert _uncited_sites({"B": "MEASURED.", "A": "MEASURED."}, known, {}) == ["A", "B"]

    def test_the_scope_of_the_scan_bites(self, tmp_path):
        # What the rule reaches decides what it can defend: a marker in a comment, in a nested
        # method, or written in lowercase prose is deliberately out of scope, and each of those
        # exclusions is a way a shouted claim could otherwise pass unseen.
        mod = tmp_path / "scoped.py"
        mod.write_text("\n".join([
            'def shouts():',
            '    """MEASURED: it answers None."""',
            '',
            '',
            'class Shouts:',
            '    """LIVE-MEASURED: the token differs from its native\'s."""',
            '',
            '',
            'def lowercase_prose():',
            '    """the measured live protocol, count/item/itemByName."""',
            '',
            '',
            'def marker_in_a_comment():',
            '    """A plain docstring."""',
            '    # MEASURED: a comment is not a docstring',
            '',
            '',
            'def no_docstring():',
            '    pass',
            '',
            '',
            'class Quiet:',
            '    """A plain docstring."""',
            '',
            '    def method(self):',
            '        """MEASURED: a method is not a top-level site."""',
            '',
        ]), encoding="utf-8")
        assert set(measured_sites(mod)) == {"shouts", "Shouts"}, (
            "the scan must collect a top-level class or def whose own DOCSTRING shouts the marker, "
            "and only those")

    def test_the_site_scan_reads_real_conftest_docstrings(self):
        # The gate is only as good as the scan feeding it: a scan that found no site, or that read
        # comments instead of docstrings, would pass this lint green over any conftest at all.
        sites = measured_sites()
        assert sites, "no top-level conftest.py site shouts MEASURED - the docstring scan is dead"
        for name, doc in sites.items():
            assert _SHOUTED.search(doc), f"{name} was collected without the marker in its docstring"
