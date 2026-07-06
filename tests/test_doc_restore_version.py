"""Tests for `doc_restore_version` - promoting a prior cloud version back to latest.

Pins the honesty contract: a promote() that returns false is an ERROR (never a false ok); a promote()
that reports success but leaves the tip unchanged is flagged 'pending' (not claimed confirmed); and the
verify-after-write re-reads the fresh DataFile to confirm the new tip actually appeared. Plus the guards
(no cloud DataFile, unknown version, no selector, already-latest no-op).
"""

import json

from conftest import load_tool, error_message

drv = load_tool("doc_restore_version")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _VerR:
    def __init__(self, num, promote_result=True, raises=False):
        self.versionNumber = num
        self.versionId = f"urn:v:{num}"
        self._pr = promote_result
        self._raises = raises

    def promote(self):
        if self._raises:
            raise RuntimeError("boom")
        return self._pr


class _VerColl:
    def __init__(self, vers): self._v = vers
    @property
    def count(self): return len(self._v)
    def item(self, i): return self._v[i]


class _DF:
    """The active doc's DataFile: itself the tip version, df.versions holds the older ones."""
    def __init__(self, latest, others):
        self.versionNumber = latest
        self.versionId = f"urn:v:{latest}"
        self.latestVersionNumber = latest
        self.id = "urn:lineage"
        self.versions = _VerColl(others)


class _Doc:
    def __init__(self, df): self.dataFile = df


class _Fresh:
    def __init__(self, latest): self.latestVersionNumber = latest


class _Data:
    def __init__(self, fresh_latest): self._latest = fresh_latest
    def findFileById(self, lineage): return _Fresh(self._latest)


class _App:
    def __init__(self, doc, fresh_latest):
        self.activeDocument = doc
        self.data = _Data(fresh_latest)


def _use(monkeypatch, doc, fresh_latest):
    monkeypatch.setattr(drv, "app", _App(doc, fresh_latest))


class TestRestoreHonesty:
    def test_confirmed_when_new_tip_appears(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2), _VerR(3), _VerR(4)])
        _use(monkeypatch, _Doc(df), fresh_latest=6)   # after promote the tip advanced to 6
        out = _payload(drv.handler(version_number=2))
        assert out["restored"] is True
        assert out.get("pending") is None
        assert out["restored_version"] == 2
        assert out["latest_before"] == 5 and out["latest_after"] == 6

    def test_promote_false_is_an_error_not_a_false_ok(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2, promote_result=False)])
        _use(monkeypatch, _Doc(df), fresh_latest=5)
        res = drv.handler(version_number=2)
        assert res["isError"] is True
        assert "did not take effect" in error_message(res)

    def test_pending_when_tip_did_not_advance(self, monkeypatch):
        # promote() said true but the fresh DataFile tip is unchanged -> report pending, not confirmed.
        df = _DF(latest=5, others=[_VerR(2, promote_result=True)])
        _use(monkeypatch, _Doc(df), fresh_latest=5)
        out = _payload(drv.handler(version_number=2))
        assert out["restored"] is True
        assert out["pending"] is True


class TestGuards:
    def test_restoring_the_latest_is_a_noop(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2)])
        _use(monkeypatch, _Doc(df), fresh_latest=5)
        out = _payload(drv.handler(version_number=5))
        assert out["restored"] is False
        assert "already the latest" in out["note"]

    def test_unknown_version_errors_and_lists_available(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2), _VerR(3), _VerR(4)])
        _use(monkeypatch, _Doc(df), fresh_latest=5)
        res = drv.handler(version_number=99)
        assert res["isError"] is True
        msg = error_message(res)
        assert "99" in msg and "5" in msg      # available numbers surfaced

    def test_no_cloud_datafile_is_guarded(self, monkeypatch):
        class _NoDF:
            dataFile = None
        _use(monkeypatch, _NoDF(), fresh_latest=5)
        res = drv.handler(version_number=2)
        assert res["isError"] is True
        assert "no cloud DataFile" in error_message(res)
