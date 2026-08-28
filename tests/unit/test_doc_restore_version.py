"""Tests for `doc_restore_version` - promoting a prior cloud version back to latest.

Pins the honesty contract: a promote() that returns false is an ERROR (never a false ok); 'restored'
means the cloud tip ACTUALLY advanced, with the raw API answer kept beside it under
promote_call_returned_true; the confirming read is PUMPED to a deadline (the new tip is not visible the
instant promote returns) and a tip that never moves is reported pending with what was read. Plus the
guards (no cloud DataFile, unknown version, no selector, already-latest no-op).
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
    """findFileById. A LIST of tip numbers serves one per call (the last repeats), so the cloud's
    'not visible yet, then visible' sequence can be modelled; None serves no file at all."""
    def __init__(self, fresh_latest):
        self._seq = list(fresh_latest) if isinstance(fresh_latest, (list, tuple)) else [fresh_latest]
        self.calls = 0

    def findFileById(self, lineage):
        self.calls += 1
        latest = self._seq[min(self.calls - 1, len(self._seq) - 1)]
        return None if latest is None else _Fresh(latest)


class _App:
    def __init__(self, doc, fresh_latest):
        self.activeDocument = doc
        self.data = _Data(fresh_latest)


def _use(monkeypatch, doc, fresh_latest, deadline=0.0):
    """Point the tool at a fake app. deadline=0 makes the confirming pump take a single attempt;
    raise it (the poll sleep is 0) to exercise the retry without waiting."""
    app = _App(doc, fresh_latest)
    monkeypatch.setattr(drv, "app", app)
    monkeypatch.setattr(drv, "_VERSION_DEADLINE_S", deadline)
    monkeypatch.setattr(drv, "_POLL_SLEEP", 0)
    return app


class TestRestoreHonesty:
    def test_confirmed_when_new_tip_appears(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2), _VerR(3), _VerR(4)])
        _use(monkeypatch, _Doc(df), fresh_latest=6)   # after promote the tip advanced to 6
        out = _payload(drv.handler(version_number=2))
        assert out["restored"] is True
        assert out["promote_call_returned_true"] is True
        assert out.get("pending") is None
        assert out["restored_version"] == 2
        assert out["latest_before"] == 5 and out["latest_after"] == 6

    def test_promote_false_is_an_error_not_a_false_ok(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2, promote_result=False)])
        _use(monkeypatch, _Doc(df), fresh_latest=5)
        res = drv.handler(version_number=2)
        assert res["isError"] is True
        assert "did not take effect" in error_message(res)

    def test_a_settled_equal_tip_is_not_restored(self, monkeypatch):
        # THE boundary: latest_after == latest_before is NOT an advance. promote() returning true is
        # kept as its own raw fact; 'restored' may only claim what the re-read showed.
        df = _DF(latest=5, others=[_VerR(2, promote_result=True)])
        _use(monkeypatch, _Doc(df), fresh_latest=5)
        out = _payload(drv.handler(version_number=2))
        assert out["restored"] is False
        assert out["promote_call_returned_true"] is True
        assert out["pending"] is True
        note = out["note"]
        assert "NOT advanced" in note
        assert f"{drv._VERSION_DEADLINE_S:.0f}s of re-reading" in note   # what was actually waited
        assert "latest reads 5, was 5" in note

    def test_one_more_than_the_baseline_is_restored(self, monkeypatch):
        # the other side of the same boundary: exactly +1 counts as the new tip.
        df = _DF(latest=5, others=[_VerR(2)])
        _use(monkeypatch, _Doc(df), fresh_latest=6)
        assert _payload(drv.handler(version_number=2))["restored"] is True

    def test_a_tip_that_appears_on_a_later_read_is_confirmed_by_the_pump(self, monkeypatch):
        # The cloud tip is not visible the instant promote() returns. A single immediate sample
        # reports this successful restore as pending; the pump re-reads until it lands.
        df = _DF(latest=5, others=[_VerR(2)])
        app = _use(monkeypatch, _Doc(df), fresh_latest=[5, 5, 6], deadline=5.0)
        out = _payload(drv.handler(version_number=2))
        assert app.data.calls >= 3            # the first fetches did NOT show the new tip
        assert out["restored"] is True
        assert out["latest_after"] == 6

    def test_the_pump_gives_up_at_the_bound_with_the_reading_it_last_got(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2)])
        app = _use(monkeypatch, _Doc(df), fresh_latest=5, deadline=0.02)
        out = _payload(drv.handler(version_number=2))
        assert app.data.calls >= 2            # it retried rather than single-shotting
        assert out["latest_after"] == 5       # the LAST reading, not a dropped one
        assert out["restored"] is False

    def test_an_unreadable_pre_call_tip_is_reported_not_diagnosed(self, monkeypatch):
        # With no pre-call number there is nothing to settle against: the read runs once, and the
        # payload may claim neither a duration it did not spend nor a non-advancement it never saw.
        class _NoTipBefore:
            id = "urn:lineage"
            versionNumber = 5
            versions = _VerColl([_VerR(2)])

            @property
            def latestVersionNumber(self):
                raise RuntimeError("2 : InternalValidationError")

        app = _use(monkeypatch, _Doc(_NoTipBefore()), fresh_latest=7, deadline=5.0)
        out = _payload(drv.handler(version_number=2))
        assert app.data.calls == 1                  # nothing to settle against - no fake wait
        assert out["restored"] is False and out["pending"] is True
        assert out["latest_before"] is None
        assert out["latest_after"] == 7             # what WAS read is still reported
        note = out["note"]
        assert "could not be read BEFORE the call" in note and "7" in note
        assert "of re-reading" not in note          # no duration was spent

    def test_without_a_lineage_urn_the_held_handle_is_the_only_read(self, monkeypatch):
        # No lineage id means there is nothing to re-fetch BY, so the held handle is all there is -
        # and it carries the pre-call number, which is why this reports pending rather than restored.
        df = _DF(latest=5, others=[_VerR(2)])
        df.id = None
        app = _use(monkeypatch, _Doc(df), fresh_latest=9)
        out = _payload(drv.handler(version_number=2))
        assert app.data.calls == 0                  # findFileById was never reached
        assert out["latest_after"] == 5 and out["restored"] is False

    def test_an_unresolvable_fresh_file_reports_an_unreadable_tip(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2)])
        _use(monkeypatch, _Doc(df), fresh_latest=None)   # findFileById answers nothing
        out = _payload(drv.handler(version_number=2))
        assert out["restored"] is False and out["pending"] is True
        assert out["latest_after"] is None
        assert "latest reads unreadable" in out["note"]


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


class TestPendingDescribedByWhatWasRead:
    """'pending' is set by ONE observation: the tip had not advanced by the time the pumped re-read
    gave up. What the cloud was doing meanwhile is not readable from here, so the wire may not name
    it as the cause."""

    def test_the_description_states_the_observation_not_a_cause(self):
        desc = drv.TOOL_DESCRIPTION
        assert "the tip had not advanced" in desc
        assert "cloud is still processing" not in desc

    def test_the_pending_note_states_the_same_observation(self, monkeypatch):
        df = _DF(latest=5, others=[_VerR(2)])
        _use(monkeypatch, _Doc(df), fresh_latest=5)      # the tip never advances past latest_before
        note = _payload(drv.handler(version_number=2))["note"]
        assert "NOT advanced" in note
        assert "still processing" not in note
