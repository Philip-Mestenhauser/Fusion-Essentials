"""Unit tests for ``doc_update_xref.py`` - refresh out-of-date external references.

Pins: no-refs early-out, matched/skipped/error paths, and that getLatestVersion
raises rather than being swallowed when the call fails.
"""

import json

from conftest import load_tool

xr = load_tool("doc_update_xref")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class FakeRef:
    """Models BOTH refresh paths doc_update_xref uses: getLatestVersion() (occurrence xrefs) and the
    'version' property SETTER (derive links - getLatestVersion() raises live for those). Assigning
    .version recomputes isOutOfDate against dataFile.latestVersionNumber, mirroring the real API's
    documented "setting this property will cause ... to update" effect."""
    def __init__(self, name, is_out_of_date=True, version=1, latest_returns=True,
                 latest_raises=None, stays_stale=False, latest_version=None, setter_raises=None,
                 file_id=None):
        self._name = name
        self._is_out_of_date = is_out_of_date
        self._version = version
        self._latest_returns = latest_returns
        self._latest_raises = latest_raises
        self._stays_stale = stays_stale       # the platform lie: True returned, ref still stale
        self._setter_raises = setter_raises   # confirmed live: the setter can ALSO refuse a derive
        lv = version + 1 if latest_version is None else latest_version
        # file_id is the source DataFile's LINEAGE id - what says two rows point at one file. The
        # default None models the id that will not read.
        self.dataFile = type("DF", (), {"name": name, "latestVersionNumber": lv,
                                        "id": file_id})()

    @property
    def version(self):
        return self._version

    @version.setter
    def version(self, v):
        if self._setter_raises:
            raise RuntimeError(self._setter_raises)
        self._version = v
        self._is_out_of_date = (v != self.dataFile.latestVersionNumber)

    @property
    def isOutOfDate(self):
        return self._is_out_of_date

    @isOutOfDate.setter
    def isOutOfDate(self, v):
        self._is_out_of_date = v

    def getLatestVersion(self):
        if self._latest_raises:
            raise RuntimeError(self._latest_raises)
        if self._latest_returns and not self._stays_stale:
            self.isOutOfDate = False
            self.version += 1
        return self._latest_returns


class FakeRefs:
    def __init__(self, refs):
        self._refs = list(refs)

    @property
    def count(self):
        return len(self._refs)

    def item(self, i):
        return self._refs[i]


class FakeDeriveFeatColl:
    def __init__(self, items):
        self._i = list(items)

    @property
    def count(self):
        return len(self._i)

    def item(self, i):
        return self._i[i]


class FakeDeriveFeat:
    """A DeriveFeature: .documentReference is any FakeRef-shaped object (same dataFile/isOutOfDate/
    version/getLatestVersion surface as an occurrence's DocumentReference)."""
    def __init__(self, name, dref=None):
        self.name = name
        self.documentReference = dref


class FakeFeatures:
    def __init__(self, derive_feats):
        self.deriveFeatures = FakeDeriveFeatColl(derive_feats)


class FakeComp:
    def __init__(self, derive_feats=None):
        self.features = FakeFeatures(derive_feats or [])


class FakeDesign:
    def __init__(self, root_comp):
        self.rootComponent = root_comp


class FakeProducts:
    def __init__(self, design):
        self._design = design

    def itemByProductType(self, kind):
        return self._design if kind == 'DesignProductType' else None


class FakeDoc:
    def __init__(self, refs, design=None):
        self.documentReferences = FakeRefs(refs)
        self.products = FakeProducts(design)


def _install(refs=None, derive_feats=None):
    """derive_feats (if given) populate the ROOT component's features.deriveFeatures - the design-level
    walk doc_update_xref now runs ALONGSIDE Document.documentReferences."""
    design = FakeDesign(FakeComp(derive_feats)) if derive_feats is not None else None
    doc = FakeDoc(refs or [], design=design)
    xr.app = type("A", (), {"activeDocument": doc})()
    return doc


class TestGuards:
    def test_no_active_document(self):
        xr.app = type("A", (), {"activeDocument": None})()
        res = xr.handler()
        assert res["isError"] is True and "No active document" in res["message"]

    def test_no_refs_reports_zero(self):
        _install([])
        out = _payload(xr.handler())
        assert out["updated_count"] == 0

    def test_name_not_found_lists_available(self):
        _install([FakeRef("PartA"), FakeRef("PartB")])
        res = xr.handler(name="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]
        assert "PartA" in res["message"] and "PartB" in res["message"]


class TestUpdateBehavior:
    def test_updates_out_of_date_refs(self):
        ref = FakeRef("PartA", is_out_of_date=True, version=2, latest_returns=True)
        ref_upd = ref
        _install([ref])
        out = _payload(xr.handler())
        assert out["updated_count"] == 1
        assert out["updated"][0]["name"] == "PartA"
        assert out["updated"][0]["was_out_of_date"] is True

    def test_skips_up_to_date_refs_when_flag_set(self):
        ref = FakeRef("PartA", is_out_of_date=False)
        _install([ref])
        out = _payload(xr.handler(only_out_of_date=True))
        assert out["updated_count"] == 0
        assert out["skipped"][0]["name"] == "PartA"

    def test_force_updates_up_to_date_when_flag_false(self):
        ref = FakeRef("PartA", is_out_of_date=False, latest_returns=True)
        _install([ref])
        out = _payload(xr.handler(only_out_of_date=False))
        assert out["updated_count"] == 1

    def test_still_stale_after_true_return_is_an_error(self):
        # the platform lie: getLatestVersion returns true but the ref still reads out of date
        ref = FakeRef("PartA", is_out_of_date=True, latest_returns=True, stays_stale=True)
        _install([ref])
        res = xr.handler()
        assert res["isError"] is True
        assert "still out of date" in res["message"]

    def test_false_return_from_get_latest_reported_as_error(self):
        ref = FakeRef("PartA", is_out_of_date=True, latest_returns=False)
        _install([ref])
        res = xr.handler()
        assert res["isError"] is True
        assert "returned false" in res["message"]

    def test_get_latest_raises_propagates(self):
        # A getLatestVersion() raise must propagate out of the handler so the MCP framework
        # reports it - swallowing it would misreport the raise as "returned false".
        import pytest
        ref = FakeRef("PartA", is_out_of_date=True, latest_raises="network timeout")
        _install([ref])
        with pytest.raises(RuntimeError, match="network timeout"):
            xr.handler()

    def test_name_filter_updates_only_matching(self):
        r1 = FakeRef("Alpha", is_out_of_date=True)
        r2 = FakeRef("Beta", is_out_of_date=True)
        _install([r1, r2])
        out = _payload(xr.handler(name="Alpha"))
        assert out["updated_count"] == 1
        assert out["updated"][0]["name"] == "Alpha"


class TestNameFilterIdentity:
    """'name' is a source document's display NAME, so it is matched case-INSENSITIVELY (the caller
    types what a read printed) - and a name is not an identity: Fusion allows same-name files in
    different folders. Every row of the matched FILE refreshes together; a name covering two
    DIFFERENT files is refused instead of refreshing both."""

    def test_match_is_case_insensitive(self):
        _install([FakeRef("PartA", is_out_of_date=True, file_id="urn:a")])
        out = _payload(xr.handler(name="parta"))
        assert out["updated_count"] == 1
        assert out["updated"][0]["name"] == "PartA"

    def test_every_row_of_one_file_refreshes_together(self):
        # an occurrence xref and a derive off the SAME source document: one file, two rows.
        xref = FakeRef("Src", is_out_of_date=True, file_id="urn:one")
        dref = FakeRef("Src", is_out_of_date=True, file_id="urn:one")
        _install(refs=[xref], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        out = _payload(xr.handler(name="Src"))
        assert out["updated_count"] == 2
        assert {row["kind"] for row in out["updated"]} == {"xref", "derive"}

    def test_two_distinct_files_sharing_a_name_are_refused(self):
        # THE boundary: 2 distinct source files behind one name - refuse, refreshing NEITHER.
        a = FakeRef("Bolt", is_out_of_date=True, file_id="urn:a")
        b = FakeRef("Bolt", is_out_of_date=True, file_id="urn:b")
        _install([a, b])
        res = xr.handler(name="Bolt")
        assert res["isError"] is True
        assert "2 DIFFERENT source files" in res["message"]
        assert "urn:a" in res["message"] and "urn:b" in res["message"]
        assert "Omit 'name'" in res["message"]          # the escape that refreshes everything
        assert a.version == 1 and b.version == 1       # neither was touched
        assert a.isOutOfDate is True and b.isOutOfDate is True

    def test_unreadable_source_ids_cannot_prove_one_file(self):
        # two rows whose file ids do not read cannot be SHOWN to be one file, so the ambiguity is
        # reported rather than merged on an assumption the reads do not support.
        _install([FakeRef("Bolt", is_out_of_date=True), FakeRef("Bolt", is_out_of_date=True)])
        res = xr.handler(name="Bolt")
        assert res["isError"] is True
        assert "source file id unreadable" in res["message"]

    def test_a_single_matched_file_still_refreshes(self):
        # the other side of the boundary: 1 distinct file, however many rows point at it.
        _install([FakeRef("Bolt", is_out_of_date=True, file_id="urn:a"),
                  FakeRef("Nut", is_out_of_date=True, file_id="urn:b")])
        out = _payload(xr.handler(name="Bolt"))
        assert out["updated_count"] == 1
        assert out["updated"][0]["name"] == "Bolt"

    def test_a_case_variant_miss_still_lists_the_available_names(self):
        _install([FakeRef("PartA", file_id="urn:a")])
        res = xr.handler(name="Ghost")
        assert res["isError"] is True and "PartA" in res["message"]


# ── derive links (Document.documentReferences can miss these entirely - confirmed live) ───────────

class TestDeriveReferences:
    def test_derive_refresh_uses_the_version_setter_not_get_latest_version(self):
        # Confirmed LIVE: calling getLatestVersion() on a DeriveFeature's documentReference raises
        # InternalValidationError (it works fine for an occurrence's) - the derive path must advance
        # via the 'version' property setter instead. latest_raises would blow up this test if the
        # derive path ever called getLatestVersion() again.
        dref = FakeRef("DeriveSrc", is_out_of_date=True, version=1,
                       latest_raises="InternalValidationError: derive path must not call this")
        _install(refs=[], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        out = _payload(xr.handler())
        assert out["updated_count"] == 1
        assert out["updated"][0]["version_after"] == 2
        assert out["updated"][0]["was_out_of_date"] is True

    def test_stale_derive_is_enumerated_and_refreshed(self):
        # Document.documentReferences is EMPTY here (no occurrence xrefs) - only the derive walk
        # (component.features.deriveFeatures) can find this reference.
        dref = FakeRef("DeriveSrc", is_out_of_date=True, version=1, latest_returns=True)
        _install(refs=[], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        out = _payload(xr.handler())
        assert out["updated_count"] == 1
        assert out["updated"][0]["name"] == "DeriveSrc"
        assert out["updated"][0]["kind"] == "derive"

    def test_setter_failure_is_reported_honestly_not_swallowed(self):
        # Confirmed LIVE: the version setter can ALSO raise for a whole-design derive
        # (InternalValidationError) - this must surface as an honest, actionable per-reference error
        # (never a false "updated"), and must not crash the whole call with a bare stack trace.
        dref = FakeRef("DeriveSrc", is_out_of_date=True, version=1,
                       setter_raises="2 : InternalValidationError : res")
        _install(refs=[], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        res = xr.handler()
        assert res["isError"] is True
        assert "DeriveSrc" in res["message"]
        assert "re-derive" in res["message"]
        assert "InternalValidationError" in res["message"]

    def test_up_to_date_derive_is_skipped(self):
        dref = FakeRef("DeriveSrc", is_out_of_date=False)
        _install(refs=[], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        out = _payload(xr.handler())
        assert out["updated_count"] == 0
        assert out["skipped"][0]["name"] == "DeriveSrc"
        assert out["skipped"][0]["kind"] == "derive"

    def test_empty_derive_features_no_crash(self):
        _install(refs=[], derive_feats=[])
        out = _payload(xr.handler())
        assert out["updated_count"] == 0
        assert "no external references" in out["note"].lower()

    def test_missing_products_attribute_no_crash(self):
        # a document exposing no .products at all (design product unresolvable) must not crash the
        # derive walk - every step is guarded by safe().
        class BareDoc:
            def __init__(self, refs):
                self.documentReferences = FakeRefs(refs)
        xr.app = type("A", (), {"activeDocument": BareDoc([])})()
        out = _payload(xr.handler())
        assert out["updated_count"] == 0

    def test_both_kinds_refreshed_together_and_counted(self):
        xref = FakeRef("PartA", is_out_of_date=True, latest_returns=True)
        dref = FakeRef("DeriveSrc", is_out_of_date=True, latest_returns=True)
        _install(refs=[xref], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        out = _payload(xr.handler())
        assert out["updated_count"] == 2
        assert out["total_references"] == 2
        kinds = {row["name"]: row["kind"] for row in out["updated"]}
        assert kinds == {"PartA": "xref", "DeriveSrc": "derive"}

    def test_name_filter_matches_a_derive_by_its_source_name(self):
        xref = FakeRef("PartA", is_out_of_date=True)
        dref = FakeRef("DeriveSrc", is_out_of_date=True, latest_returns=True)
        _install(refs=[xref], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        out = _payload(xr.handler(name="DeriveSrc"))
        assert out["updated_count"] == 1
        assert out["updated"][0]["name"] == "DeriveSrc"

    def test_zero_document_references_but_derive_present_still_refreshes(self):
        # THE live-confirmed gap this fix closes: on a cold reopen, Document.documentReferences can
        # read count=0 (the derive's link isn't resolved in-session) even though a genuinely stale
        # derive exists - the derive walk must find it regardless of documentReferences' state.
        dref = FakeRef("DeriveSrc", is_out_of_date=True, version=2, latest_returns=True)
        _install(refs=[], derive_feats=[FakeDeriveFeat("Derive1", dref)])
        out = _payload(xr.handler())
        assert out["total_references"] == 1
        assert out["updated_count"] == 1
        assert out["updated"][0]["version_after"] == 3
