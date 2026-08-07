"""Tests for `doc_save_milestone` - saving the active document as a NAMED milestone version.

Pins the measured contract: saveMilestone returns TRUE on a clean document while creating nothing,
so a clean document is REFUSED up front; the confirming read goes through a FRESH findFileById
fetch (the handle the save was issued on never advances) and RETRIES until the new tip appears at
about 4.4s; the milestone mark itself only becomes readable at about 15.7s, so it is reported
pending with the observed reason - never as a milestone that landed, never as one that did not.
"""

import json

from conftest import load_tool, error_message

dsm = load_tool("doc_save_milestone")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _Milestone:
    def __init__(self, name): self.name = name


class _Milestones:
    """The Milestones collection on a DataFile. itemByName is modelled as measured: a HIT returns
    the milestone, a MISS RAISES '3 : invalid argument name' instead of returning null. Under safe()
    that raise flattens to the same None an unreadable collection gives, so an itemByName check
    cannot tell a miss from an unreadable read - the tool walks the entries instead, which never
    provokes the raise. unreadable=True models the collection whose count reads None."""
    def __init__(self, names=(), unreadable=False):
        self._m = [_Milestone(n) for n in names]
        self._unreadable = unreadable

    @property
    def count(self): return None if self._unreadable else len(self._m)

    def item(self, i): return self._m[i]

    def itemByName(self, name):
        for m in self._m:
            if m.name == name:
                return m
        raise RuntimeError("3 : invalid argument name")


class _FreshFile:
    """A DataFile as returned by findFileById AFTER the save - the only trustworthy read."""
    def __init__(self, latest, is_milestone=True, names=("MS",), unreadable=False):
        self.latestVersionNumber = latest
        self.isMilestone = is_milestone
        self.milestones = _Milestones(names, unreadable=unreadable)


class _StaleFile:
    """The handle held across the save. Measured to keep its PRE-save values: the version never
    advances and isMilestone stays False even after the milestone lands."""
    def __init__(self, lineage="urn:lineage", vnum=1, latest=1):
        self.id = lineage
        self.versionNumber = vnum
        self.latestVersionNumber = latest
        self.isMilestone = False
        self.milestones = _Milestones(unreadable=True)


class _Doc:
    def __init__(self, df, modified=True, result=True, raises=False, name="Bracket"):
        self.name = name
        self.dataFile = df
        self.isModified = modified
        self._result = result
        self._raises = raises
        self.calls = []

    def saveMilestone(self, milestone_name, version_description):
        self.calls.append((milestone_name, version_description))
        if self._raises:
            raise RuntimeError("cloud refused")
        return self._result


class _Data:
    """findFileById. A list of files serves one per call (the last repeats), so the cloud's
    'not visible yet, then visible' sequence can be modelled."""
    def __init__(self, fresh):
        self._seq = list(fresh) if isinstance(fresh, (list, tuple)) else [fresh]
        self.calls = 0

    def findFileById(self, lineage):
        self.calls += 1
        self.queried = getattr(self, "queried", []) + [lineage]
        return self._seq[min(self.calls - 1, len(self._seq) - 1)]


class _App:
    def __init__(self, doc, fresh):
        self.activeDocument = doc
        self.data = _Data(fresh)


def _use(monkeypatch, doc, fresh, deadline=0.0):
    """Point the tool at a fake app. deadline=0 makes the version pump take a single attempt;
    raise it (with sleep 0) to exercise the retry loop without waiting."""
    app = _App(doc, fresh)
    monkeypatch.setattr(dsm, "app", app)
    monkeypatch.setattr(dsm, "_VERSION_DEADLINE_S", deadline)
    monkeypatch.setattr(dsm, "_POLL_SLEEP", 0)
    return app


class TestHappyPath:
    def test_confirmed_when_fresh_read_shows_the_new_milestone_version(self, monkeypatch):
        doc = _Doc(_StaleFile(vnum=1, latest=1))
        _use(monkeypatch, doc, _FreshFile(latest=2, is_milestone=True, names=("v2 release",)))
        out = _payload(dsm.handler(milestone_name="v2 release", description="ready"))
        assert out["save_call_returned_true"] is True
        assert out["latest_version_before"] == 1 and out["latest_version_after"] == 2
        assert out["version_confirmed"] is True
        assert out["milestone_confirmed"] is True
        assert out.get("pending") is None
        assert out["milestone_count_after"] == 1
        assert out["document_id"] == "urn:lineage"

    def test_description_carries_the_ai_agent_marker(self, monkeypatch):
        doc = _Doc(_StaleFile())
        _use(monkeypatch, doc, _FreshFile(latest=2))
        out = _payload(dsm.handler(milestone_name="MS", description="ready"))
        assert doc.calls == [("MS", "[AI agent] ready")]      # the marker reaches the API call
        assert out["description"] == "[AI agent] ready"

    def test_confirmation_never_reads_the_handle_the_save_was_issued_on(self, monkeypatch):
        # the held DataFile keeps latest=1 and isMilestone=False; reading it instead of re-fetching
        # would report a save that plainly worked as unconfirmed.
        doc = _Doc(_StaleFile(vnum=1, latest=1))
        _use(monkeypatch, doc, _FreshFile(latest=2, is_milestone=True, names=("MS",)))
        out = _payload(dsm.handler(milestone_name="MS"))
        assert out["version_confirmed"] is True
        assert out["milestone_confirmed"] is True
        assert doc.dataFile.latestVersionNumber == 1        # the stale handle never advanced

    def test_version_is_confirmed_only_because_the_pump_retried(self, monkeypatch):
        # measured: the new tip is not visible the instant saveMilestone returns - it appears on a
        # fresh fetch at about 4.4s. A single-shot fetch would report this successful save pending.
        doc = _Doc(_StaleFile(latest=1))
        not_yet = _FreshFile(latest=1, is_milestone=False, names=())
        landed = _FreshFile(latest=2, is_milestone=True, names=("MS",))
        app = _use(monkeypatch, doc, [not_yet, landed], deadline=5.0)
        out = _payload(dsm.handler(milestone_name="MS"))
        assert app.data.calls >= 2                  # the first fetch did NOT show the new tip
        assert out["version_confirmed"] is True
        assert out["latest_version_after"] == 2


class TestPendingReportsWhatWasObserved:
    def test_flag_reading_false_is_named_as_false(self, monkeypatch):
        # inside the measured 15-20s window: the tip is there, its isMilestone flag still reads False.
        doc = _Doc(_StaleFile())
        _use(monkeypatch, doc, _FreshFile(latest=2, is_milestone=False, names=()))
        out = _payload(dsm.handler(milestone_name="MS"))
        assert out["version_confirmed"] is True
        assert out["milestone_confirmed"] is False
        assert out["pending"] is True
        assert "reads FALSE" in out["note"]
        # the measured RANGE (both runs), never one run's number and never "minutes"
        assert "15-20s" in out["note"] and "15.7s and 19.9s" in out["note"]
        assert "doc_get include=['versions']" in out["note"]

    def test_unreadable_flag_is_named_as_unreadable_not_as_false(self, monkeypatch):
        class _NoFlag:
            latestVersionNumber = 2
            milestones = _Milestones(("MS",))
        _use(monkeypatch, _Doc(_StaleFile()), _NoFlag())
        note = _payload(dsm.handler(milestone_name="MS"))["note"]
        assert "could not be read" in note and "reads FALSE" not in note

    def test_collection_without_the_name_is_named_as_such(self, monkeypatch):
        # the flag says milestone, but no entry carries this name - report exactly that.
        doc = _Doc(_StaleFile())
        _use(monkeypatch, doc, _FreshFile(latest=2, is_milestone=True, names=("SomeoneElse",)))
        out = _payload(dsm.handler(milestone_name="MS"))
        assert out["milestone_confirmed"] is False
        assert "holds no entry named 'MS'" in out["note"]

    def test_named_entry_is_required_for_confirmation(self, monkeypatch):
        # the name term is load-bearing: an isMilestone flag alone does not prove THIS milestone.
        doc = _Doc(_StaleFile())
        _use(monkeypatch, doc, _FreshFile(latest=2, is_milestone=True, names=("Other", "Another")))
        assert _payload(dsm.handler(milestone_name="MS"))["milestone_confirmed"] is False

    def test_unresolvable_fresh_file_is_pending_not_confirmed(self, monkeypatch):
        doc = _Doc(_StaleFile())
        _use(monkeypatch, doc, None)                 # findFileById returns nothing
        out = _payload(dsm.handler(milestone_name="MS"))
        assert out["version_confirmed"] is False
        assert out["milestone_confirmed"] is False
        assert out["latest_version_after"] is None
        assert out["milestone_count_after"] is None


class TestVersionNeverAdvanced:
    def test_note_names_the_nothing_happened_signature_without_a_lag_excuse(self, monkeypatch):
        # measured: a real milestone save's version is visible at 4.4s. After the full pump, a tip
        # that has not moved is the clean-document signature, not cloud lag.
        doc = _Doc(_StaleFile(latest=1))
        _use(monkeypatch, doc, _FreshFile(latest=1, is_milestone=False, names=()))
        out = _payload(dsm.handler(milestone_name="MS"))
        assert out["version_confirmed"] is False
        assert out["pending"] is True
        note = out["note"]
        assert "versioned nothing" in note
        assert "4.4s" in note
        assert "lag" not in note.lower()


class TestHonesty:
    def test_clean_document_is_refused_not_falsely_saved(self, monkeypatch):
        # the measured false OK: saveMilestone returns True on an unmodified doc and creates nothing.
        doc = _Doc(_StaleFile(), modified=False)
        _use(monkeypatch, doc, _FreshFile(latest=2))
        res = dsm.handler(milestone_name="MS")
        msg = error_message(res)
        assert "no unsaved changes" in msg
        assert "only creates a NEW milestone version" in msg   # the tool's limit, not a platform claim
        assert doc.calls == []                                 # the API was never called

    def test_false_return_is_an_error_not_a_false_ok(self, monkeypatch):
        doc = _Doc(_StaleFile(), result=False)
        _use(monkeypatch, doc, _FreshFile(latest=2))
        res = dsm.handler(milestone_name="MS")
        assert "returned false" in error_message(res)

    def test_raised_failure_surfaces(self, monkeypatch):
        doc = _Doc(_StaleFile(), raises=True)
        _use(monkeypatch, doc, _FreshFile(latest=2))
        assert "cloud refused" in error_message(dsm.handler(milestone_name="MS"))


class TestGuards:
    def test_empty_name_is_refused(self, monkeypatch):
        doc = _Doc(_StaleFile())
        _use(monkeypatch, doc, _FreshFile(latest=2))
        assert "milestone_name" in error_message(dsm.handler(milestone_name="   "))
        assert doc.calls == []

    def test_never_saved_document_points_at_doc_save_as(self, monkeypatch):
        doc = _Doc(None)
        _use(monkeypatch, doc, None)
        assert "doc_save_as" in error_message(dsm.handler(milestone_name="MS"))

    def test_no_active_document_is_guarded(self, monkeypatch):
        _use(monkeypatch, None, None)
        assert "no active document" in error_message(dsm.handler(milestone_name="MS")).lower()


class TestLineageFork:
    def test_a_forking_save_confirms_on_the_new_lineage_and_reports_the_fork(self, monkeypatch):
        # The first save after a configured-design conversion moves the document to a NEW lineage
        # URN whose versions restart at 1 - confirming against the superseded URN watches a stream this
        # save never advances, so the check must re-anchor on the lineage the doc holds NOW.
        doc = _Doc(_StaleFile(lineage="urn:old", vnum=3, latest=3))
        def fork(name, desc):
            doc.calls.append((name, desc))
            doc.dataFile = _StaleFile(lineage="urn:new", vnum=1, latest=1)
            return True
        doc.saveMilestone = fork
        app = _use(monkeypatch, doc, _FreshFile(latest=1, is_milestone=False, names=()))
        out = _payload(dsm.handler(milestone_name="MS"))
        assert out["lineage_changed"] == {"from": "urn:old", "to": "urn:new"}
        assert out["document_id"] == "urn:new"
        assert app.data.queried == ["urn:new"]
        assert out["version_confirmed"] is True      # the new lineage answered with its tip
        assert out["pending"] is True                # the mark is unconfirmed there
        assert "NEW LINEAGE" in out["note"] and "NOT applied" in out["note"]

    def test_a_same_lineage_save_reports_no_fork(self, monkeypatch):
        doc = _Doc(_StaleFile(lineage="urn:same", vnum=1, latest=1))
        _use(monkeypatch, doc, _FreshFile(latest=2, is_milestone=True, names=("MS",)))
        out = _payload(dsm.handler(milestone_name="MS"))
        assert "lineage_changed" not in out
        assert out["document_id"] == "urn:same"
        assert "NEW LINEAGE" not in out["note"]
