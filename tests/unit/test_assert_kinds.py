"""Unit tests for ``_assert.py`` - the postcondition kernel (verify-the-effect).

Covers the wrap() contract (verify runs only on JSON ok results; a hard reason converts ok into
isError; a soft reason marks the payload unconfirmed; evidence merges via setdefault so handler
values win; error results and non-JSON pass through untouched; a verify() crash degrades to
unconfirmed, never a false pass) and each shipped kind against fakes: VersionAdvanced (the
Document.save() false-success), ReferencesFresh (the lying isUpToDate), FileLanded (export wrote
nothing), FeatureHealthy (a feature add()ed but computed with an error health state). Every gate is
proven to BITE (the failure case goes red through the wrapper).
"""

import json
import types

from conftest import load_tool

kernel = load_tool("_assert")


def _ok(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class Fixed(kernel.Postcondition):
    """A postcondition scripted per-test: returns the queued (reason, evidence)."""
    name = "fixed"

    def __init__(self, reason="", evidence=None, severity="hard", capture_value=None, boom=False):
        self._reason = reason
        self._evidence = evidence or {}
        self.severity = severity
        self._capture_value = capture_value
        self._boom = boom
        self.saw_before = None
        self.saw_kwargs = None

    def capture(self, kwargs):
        return self._capture_value

    def verify(self, kwargs, payload, before):
        if self._boom:
            raise RuntimeError("verify exploded")
        self.saw_before = before
        self.saw_kwargs = kwargs
        return self._reason, self._evidence


class TestWrapContract:
    def test_confirmed_effect_merges_evidence(self):
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}), [Fixed(evidence={"size_bytes": 42})])
        out = _payload(wrapped())
        assert out["done"] is True and out["size_bytes"] == 42

    def test_handler_values_win_over_evidence(self):
        wrapped = kernel.wrap(lambda **kw: _ok({"size_bytes": 7}), [Fixed(evidence={"size_bytes": 42})])
        assert _payload(wrapped())["size_bytes"] == 7   # setdefault - never clobber the handler

    def test_hard_reason_converts_ok_into_error(self):
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}), [Fixed(reason="nothing changed")])
        res = wrapped()
        assert res["isError"] is True
        assert "nothing changed" in res["message"]

    def test_soft_reason_marks_unconfirmed_not_error(self):
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}),
                              [Fixed(reason="cloud still processing", severity="soft")])
        out = _payload(wrapped())
        assert out["verified"]["fixed"]["confirmed"] is False

    def test_error_results_pass_through_unverified(self):
        p = Fixed(reason="would fail")
        err = {"content": [], "isError": True, "message": "handler refused"}
        assert kernel.wrap(lambda **kw: err, [p])() is err

    def test_capture_value_reaches_verify(self):
        p = Fixed(capture_value={"was": 3})
        _payload(kernel.wrap(lambda **kw: _ok({}), [p])(x=1))
        assert p.saw_before == {"was": 3}
        assert p.saw_kwargs == {"x": 1}

    def test_verify_crash_degrades_to_unconfirmed_never_false_pass(self):
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}), [Fixed(boom=True)])
        out = _payload(wrapped())
        assert out["fixed_confirmed"] is False          # surfaced, not swallowed into silence
        assert "verify exploded" in out["verify_error"]

    def test_no_postconditions_returns_handler_unwrapped(self):
        h = lambda **kw: _ok({})
        assert kernel.wrap(h, []) is h

    def test_wrapper_exposes_declaration_for_the_lint(self):
        posts = [Fixed()]
        wrapped = kernel.wrap(lambda **kw: _ok({}), posts)
        assert wrapped.__assert_postconditions__ == posts   # wrap copies the list defensively


# ── the shipped kinds against fakes ──────────────────────────────────────────

class TestVersionAdvanced:
    def _app(self, modified):
        return types.SimpleNamespace(activeDocument=types.SimpleNamespace(isModified=modified))

    def test_false_success_still_modified_bites(self, monkeypatch):
        monkeypatch.setattr(kernel, "app", self._app(True))
        wrapped = kernel.wrap(lambda **kw: _ok({"saved": True}), [kernel.VersionAdvanced()])
        res = wrapped()
        assert res["isError"] is True
        assert "still modified" in res["message"].lower()

    def test_real_save_confirms(self, monkeypatch):
        monkeypatch.setattr(kernel, "app", self._app(False))
        out = _payload(kernel.wrap(lambda **kw: _ok({"saved": True}), [kernel.VersionAdvanced()])())
        assert out["version_confirmed"] is True

    def test_already_current_noop_skips_the_check(self, monkeypatch):
        monkeypatch.setattr(kernel, "app", self._app(True))   # doc dirty, but handler did nothing
        out = _payload(kernel.wrap(lambda **kw: _ok({"saved": True, "already_current": True}),
                                   [kernel.VersionAdvanced()])())
        assert out["saved"] is True


class TestReferencesFresh:
    def _app(self, flags):
        refs = [types.SimpleNamespace(isOutOfDate=f) for f in flags]
        coll = types.SimpleNamespace(count=len(refs), item=lambda i: refs[i])
        return types.SimpleNamespace(activeDocument=types.SimpleNamespace(documentReferences=coll))

    def test_surviving_stale_reference_bites(self, monkeypatch):
        monkeypatch.setattr(kernel, "app", self._app([False, True]))
        res = kernel.wrap(lambda **kw: _ok({"updated": True}), [kernel.ReferencesFresh()])()
        assert res["isError"] is True
        assert "still out of date" in res["message"].lower()

    def test_all_fresh_confirms(self, monkeypatch):
        monkeypatch.setattr(kernel, "app", self._app([False, False]))
        out = _payload(kernel.wrap(lambda **kw: _ok({"updated": True}), [kernel.ReferencesFresh()])())
        assert out["stale_references_after"] == 0


class TestDeliverablesExist:
    def test_every_listed_file_is_verified(self, tmp_path):
        a, b = tmp_path / "a.nc", tmp_path / "b.nc"
        a.write_text("G0 X0"); b.write_text("G1 X1")
        out = _payload(kernel.wrap(
            lambda **kw: _ok({"files": [{"file_path": str(a)}, {"file_path": str(b)}]}),
            [kernel.DeliverablesExist()])())
        assert out["deliverables_verified"] == 2

    def test_one_missing_listed_file_bites(self, tmp_path):
        a = tmp_path / "a.nc"
        a.write_text("G0 X0")
        res = kernel.wrap(
            lambda **kw: _ok({"files": [{"file_path": str(a)},
                                        {"file_path": str(tmp_path / "ghost.nc")}]}),
            [kernel.DeliverablesExist()])()
        assert res["isError"] is True
        assert "ghost.nc" in res["message"]

    def test_single_file_path_shape_is_covered(self, tmp_path):
        p = tmp_path / "out.dxf"
        p.write_text("0\nSECTION")
        out = _payload(kernel.wrap(lambda **kw: _ok({"file_path": str(p)}),
                                   [kernel.DeliverablesExist()])())
        assert out["deliverables_verified"] == 1

    def test_claiming_no_deliverable_at_all_bites(self):
        res = kernel.wrap(lambda **kw: _ok({"exported": True}), [kernel.DeliverablesExist()])()
        assert res["isError"] is True
        assert "names no deliverable" in res["message"]

    def test_list_entry_without_a_path_bites(self, tmp_path):
        res = kernel.wrap(lambda **kw: _ok({"files": [{"size_bytes": 9}]}),
                          [kernel.DeliverablesExist()])()
        assert res["isError"] is True
        assert "files[0]" in res["message"]


class _FakeTimelineItem:
    def __init__(self, name, health, msg=""):
        self.name = name
        self.healthState = health
        self.errorOrWarningMessage = msg


class _FakeTimeline:
    def __init__(self, items=None):
        self.items = list(items or [])

    @property
    def count(self):
        return len(self.items)

    def item(self, i):
        return self.items[i]


class TestFeatureHealthy:
    def _wire(self, monkeypatch, timeline):
        p = kernel.FeatureHealthy()
        monkeypatch.setattr(p, "_timeline", lambda: timeline)
        return p

    def test_healthy_added_feature_confirms_with_count(self, monkeypatch):
        tl = _FakeTimeline([_FakeTimelineItem("Extrude1", 0)])
        p = self._wire(monkeypatch, tl)

        def handler(**kw):
            tl.items.append(_FakeTimelineItem("Extrude2", 0))
            return _ok({"extruded": True})

        out = _payload(kernel.wrap(handler, [p])())
        assert out["features_verified"] == 1

    def test_compute_failed_feature_bites_with_name_and_message(self, monkeypatch):
        tl = _FakeTimeline()
        p = self._wire(monkeypatch, tl)

        def handler(**kw):
            tl.items.append(_FakeTimelineItem("Fillet1", 2, "radius too large for the geometry"))
            return _ok({"filleted": True})

        res = kernel.wrap(handler, [p])()
        assert res["isError"] is True
        assert "Fillet1" in res["message"]
        assert "radius too large" in res["message"]

    def test_compute_warning_is_evidence_not_failure(self, monkeypatch):
        tl = _FakeTimeline()
        p = self._wire(monkeypatch, tl)

        def handler(**kw):
            tl.items.append(_FakeTimelineItem("Hole1", 1, "hole extends outside the body"))
            return _ok({"holed": True})

        out = _payload(kernel.wrap(handler, [p])())
        assert out["features_verified"] == 1
        assert out["feature_warnings"] == ["Hole1: hole extends outside the body"]

    def test_only_items_added_by_the_call_are_gated(self, monkeypatch):
        tl = _FakeTimeline([_FakeTimelineItem("OldBroken", 2, "an old unrelated break")])
        p = self._wire(monkeypatch, tl)

        def handler(**kw):
            tl.items.append(_FakeTimelineItem("Combine1", 0))
            return _ok({"combined": True})

        out = _payload(kernel.wrap(handler, [p])())
        assert out["features_verified"] == 1    # a break present before the call is not gated here

    def test_no_new_timeline_items_skips_silently(self, monkeypatch):
        tl = _FakeTimeline([_FakeTimelineItem("Old1", 0)])
        p = self._wire(monkeypatch, tl)
        out = _payload(kernel.wrap(lambda **kw: _ok({"done": True}), [p])())
        assert "features_verified" not in out   # nothing added -> nothing gated

    def test_no_timeline_design_skips_silently(self, monkeypatch):
        p = self._wire(monkeypatch, None)
        out = _payload(kernel.wrap(lambda **kw: _ok({"done": True}), [p])())
        assert "features_verified" not in out


class TestFileLanded:
    def test_missing_file_bites(self, tmp_path):
        ghost = str(tmp_path / "ghost.pdf")
        res = kernel.wrap(lambda **kw: _ok({"file_path": ghost}), [kernel.FileLanded()])()
        assert res["isError"] is True
        assert "file_exists=False" in res["message"]

    def test_empty_file_bites(self, tmp_path):
        p = tmp_path / "empty.pdf"
        p.write_text("")
        res = kernel.wrap(lambda **kw: _ok({"file_path": str(p)}), [kernel.FileLanded()])()
        assert res["isError"] is True
        assert "file_exists=True" in res["message"] and "size_bytes=0" in res["message"]

    def test_real_file_confirms_and_supplies_size(self, tmp_path):
        p = tmp_path / "real.pdf"
        p.write_text("PDF-STUB")
        out = _payload(kernel.wrap(lambda **kw: _ok({"file_path": str(p)}), [kernel.FileLanded()])())
        assert out["file_exists"] is True and out["size_bytes"] > 0

    def test_missing_path_key_is_named(self, tmp_path):
        res = kernel.wrap(lambda **kw: _ok({"other": 1}), [kernel.FileLanded("file_path")])()
        assert res["isError"] is True
        assert "file_path" in res["message"]
