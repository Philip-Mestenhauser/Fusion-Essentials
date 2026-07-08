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
    def __init__(self, name, is_out_of_date=True, version=1, latest_returns=True,
                 latest_raises=None, stays_stale=False):
        self._name = name
        self.isOutOfDate = is_out_of_date
        self.version = version
        self._latest_returns = latest_returns
        self._latest_raises = latest_raises
        self._stays_stale = stays_stale       # the platform lie: True returned, ref still stale
        self.dataFile = type("DF", (), {"name": name})()

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


class FakeDoc:
    def __init__(self, refs):
        self.documentReferences = FakeRefs(refs)


def _install(refs=None):
    doc = FakeDoc(refs or [])
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
