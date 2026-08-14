"""Unit tests for ``_assert.py`` - the postcondition kernel (verify-the-effect).

Covers the wrap() contract (verify runs only on JSON ok results; a hard reason converts ok into
isError; a soft reason marks the payload unconfirmed; evidence merges via setdefault so handler
values win; error results and non-JSON pass through untouched; a capture()/verify() crash on a HARD
postcondition fails CLOSED - verification impossible is an honest error naming the exception and
the kind's read_tool, never a pass - while a soft one annotates) and each shipped kind against
fakes: VersionAdvanced (the
Document.save() false-success), ReferencesFresh (the lying isUpToDate), FileLanded (export wrote
nothing), FeatureHealthy (a feature add()ed but computed with an error health state). Every gate is
proven to BITE (the failure case goes red through the wrapper).
"""

import json
import types

import pytest

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

    def __init__(self, reason="", evidence=None, severity="hard", capture_value=None, boom=False,
                 capture_boom=False, read_tool=None):
        self._reason = reason
        self._evidence = evidence or {}
        self.severity = severity
        self._capture_value = capture_value
        self._boom = boom
        self._capture_boom = capture_boom
        self.read_tool = read_tool
        self.saw_before = None
        self.saw_kwargs = None

    def capture(self, kwargs):
        if self._capture_boom:
            raise RuntimeError("capture exploded")
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

    def test_hard_verify_crash_fails_closed_with_honest_wording(self):
        # verification IMPOSSIBLE on a hard postcondition = an error, never a possible no-op as ok.
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}),
                              [Fixed(boom=True, read_tool="design_get")])
        res = wrapped()
        assert res["isError"] is True
        assert "verification could not run" in res["message"]
        assert "verify exploded" in res["message"]              # the raising exception is named
        body = json.loads(res["content"][0]["text"])
        assert "may have succeeded" in body["note"]             # honest: the mutation is NOT claimed undone
        assert "design_get" in body["note"]                     # the kind's read tool is the next step

    def test_soft_verify_crash_stays_an_annotation(self):
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}),
                              [Fixed(boom=True, severity="soft")])
        out = _payload(wrapped())                               # still ok - soft never fails the call
        assert out["fixed_confirmed"] is False
        assert "verify exploded" in out["verify_error"]

    def test_hard_capture_crash_fails_closed(self):
        # a raising capture leaves no baseline, so verify can never run - same fail-closed error.
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}), [Fixed(capture_boom=True)])
        res = wrapped()
        assert res["isError"] is True
        assert "verification could not run" in res["message"]
        assert "capture exploded" in res["message"]

    def test_soft_capture_crash_stays_an_annotation(self):
        wrapped = kernel.wrap(lambda **kw: _ok({"done": True}),
                              [Fixed(capture_boom=True, severity="soft")])
        out = _payload(wrapped())
        assert out["fixed_confirmed"] is False
        assert "capture exploded" in out["verify_error"]

    def test_capture_crash_does_not_block_the_handler(self):
        # fail-closed applies to the RESULT, not the mutation: the handler still runs - the kernel
        # never rolls back or pre-empts the work, it reports the unverifiable outcome honestly.
        calls = {"n": 0}

        def handler(**kw):
            calls["n"] += 1
            return _ok({"done": True})

        res = kernel.wrap(handler, [Fixed(capture_boom=True)])()
        assert calls["n"] == 1 and res["isError"] is True

    def test_handler_error_passes_through_even_when_capture_crashed(self):
        # an error result carries its own honest failure; the verification-impossible error must not
        # replace it (the mutation did NOT claim success, so there is nothing to fail closed about).
        err = {"content": [], "isError": True, "message": "handler refused"}
        assert kernel.wrap(lambda **kw: err, [Fixed(capture_boom=True)])() is err

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

    def test_an_unreadable_modified_flag_is_disclosed_not_silently_passed(self, monkeypatch):
        # the gate could not run; the payload must SAY so rather than look like a confirmed save
        class _Doc:
            @property
            def isModified(self):
                raise RuntimeError("the document is gone")

        monkeypatch.setattr(kernel, "app", types.SimpleNamespace(activeDocument=_Doc()))
        out = _payload(kernel.wrap(lambda **kw: _ok({"saved": True}), [kernel.VersionAdvanced()])())
        assert out["version_confirmed"] is False


class TestReferencesFresh:
    def _app(self, flags):
        refs = [types.SimpleNamespace(isOutOfDate=f) for f in flags]
        coll = types.SimpleNamespace(count=len(refs), item=lambda i: refs[i])
        return types.SimpleNamespace(activeDocument=types.SimpleNamespace(documentReferences=coll))

    def test_surviving_stale_reference_bites(self, monkeypatch):
        monkeypatch.setattr(kernel, "app", self._app([False, True]))
        # the re-read is a bounded settle wait (the refresh lands asynchronously); a zero budget
        # still samples once, so the bite is exercised without spending the shipped budget.
        monkeypatch.setattr(kernel, "_REFERENCE_SETTLE_S", 0.0)
        res = kernel.wrap(lambda **kw: _ok({"updated": True}), [kernel.ReferencesFresh()])()
        assert res["isError"] is True
        assert "still out of date" in res["message"].lower()
        assert "to settle" in res["message"]      # the error says how long the refresh was given

    def test_all_fresh_confirms(self, monkeypatch):
        monkeypatch.setattr(kernel, "app", self._app([False, False]))
        out = _payload(kernel.wrap(lambda **kw: _ok({"updated": True}), [kernel.ReferencesFresh()])())
        assert out["stale_references_after"] == 0

    def test_an_unreadable_reference_walk_is_disclosed_not_silently_passed(self, monkeypatch):
        class _Doc:
            @property
            def documentReferences(self):
                raise RuntimeError("references unavailable")

        monkeypatch.setattr(kernel, "app", types.SimpleNamespace(activeDocument=_Doc()))
        monkeypatch.setattr(kernel, "_REFERENCE_SETTLE_S", 0.0)
        out = _payload(kernel.wrap(lambda **kw: _ok({"updated": True}), [kernel.ReferencesFresh()])())
        assert out["references_confirmed"] is False
        assert "stale_references_after" not in out


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

    def test_an_empty_files_list_bites_as_no_deliverable(self):
        # a split export whose every write failed reaches the gate with files: [] - an empty list
        # names NO deliverable, so it must fail on the same footing as a missing key, never pass a
        # walk over nothing.
        res = kernel.wrap(lambda **kw: _ok({"exported": False, "files": []}),
                          [kernel.DeliverablesExist()])()
        assert res["isError"] is True
        assert "names no deliverable" in res["message"]
        assert "EMPTY" in res["message"]

    def test_an_empty_list_still_defers_to_a_single_claimed_file(self, tmp_path):
        p = tmp_path / "one.nc"
        p.write_text("G0 X0")
        out = _payload(kernel.wrap(lambda **kw: _ok({"files": [], "file_path": str(p)}),
                                   [kernel.DeliverablesExist()])())
        assert out["deliverables_verified"] == 1


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

    def test_a_count_that_raises_at_VERIFY_is_disclosed_not_failed_closed(self, monkeypatch):
        # capture counts the timeline, then the verify re-count raises (a mid-recompute read):
        # the count-is-None disposition must disclose feature_health_confirmed: False - without
        # it the numeric comparison against the baseline raises and fails a possibly-fine edit
        # closed.
        class _CountsOnceTimeline:
            def __init__(self):
                self.reads = 0
            @property
            def count(self):
                self.reads += 1
                if self.reads > 1:
                    raise RuntimeError("timeline mid-recompute")
                return 3
        p = self._wire(monkeypatch, _CountsOnceTimeline())
        out = _payload(kernel.wrap(lambda **kw: _ok({"edited": True}), [p])())
        assert out["feature_health_confirmed"] is False
        assert "features_verified" not in out

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
        assert "feature_health_confirmed" not in out   # ...and the gate DID run

    def test_no_timeline_design_is_disclosed_not_silent(self, monkeypatch):
        # a DIRECT design has no timeline to walk, so the health gate cannot run - the payload says
        # so instead of reading like a clean compute
        p = self._wire(monkeypatch, None)
        out = _payload(kernel.wrap(lambda **kw: _ok({"done": True}), [p])())
        assert out["feature_health_confirmed"] is False
        assert "features_verified" not in out

    def test_an_unreadable_timeline_count_is_disclosed(self, monkeypatch):
        class _PoisonCount(_FakeTimeline):
            @property
            def count(self):
                raise RuntimeError("timeline unavailable inside a base-feature scope")

        p = self._wire(monkeypatch, _PoisonCount())
        out = _payload(kernel.wrap(lambda **kw: _ok({"done": True}), [p])())
        assert out["feature_health_confirmed"] is False


class TestSketchCurvesChanged:
    """The entity-set fingerprint gate: an edit that changed nothing bites, and a sketch that cannot
    be read on one side of the call is DISCLOSED rather than passed as a confirmed change."""

    def _wire(self, monkeypatch, fingerprints):
        p = kernel.SketchCurvesChanged()
        seq = iter(fingerprints)
        monkeypatch.setattr(p, "_fingerprint", lambda kwargs: next(seq))
        return p

    def _handler(self, **payload):
        def handler(sketch_name=""):
            return _ok(payload or {"moved": True})
        return handler

    def test_an_unchanged_entity_set_bites(self, monkeypatch):
        marks = {"marks": {("curve", "TOK1"): (4.0, None)}, "curves": 1}
        p = self._wire(monkeypatch, [dict(marks), dict(marks)])
        res = kernel.wrap(self._handler(), [p])(sketch_name="Sketch1")
        assert res["isError"] is True
        assert "unchanged" in res["message"]

    def test_a_changed_entity_set_confirms_with_the_curve_count(self, monkeypatch):
        p = self._wire(monkeypatch, [{"marks": {("curve", "TOK1"): (4.0, None)}, "curves": 1},
                                     {"marks": {("curve", "TOK1"): (9.0, None)}, "curves": 1}])
        out = _payload(kernel.wrap(self._handler(), [p])(sketch_name="Sketch1"))
        assert out["curve_count_after"] == 1

    def test_an_unreadable_sketch_is_disclosed_not_silently_passed(self, monkeypatch):
        p = self._wire(monkeypatch, [None, None])
        out = _payload(kernel.wrap(self._handler(), [p])(sketch_name="Sketch1"))
        assert out["sketch_curves_confirmed"] is False
        assert "curve_count_after" not in out


# ── ChildGeometryMoved: a joint's reposition must reach the NESTED body geometry ────────────────
#
# The fake occurrence tree is built from types.SimpleNamespace (no bespoke Fake* classes): a handler
# moves a part by reassigning occ.transform.translation and, when it propagates, the nested body's
# boundingBox.minPoint - exactly the world reads the kind captures before / verifies after.

def _pt(x, y, z):
    return types.SimpleNamespace(x=x, y=y, z=z)


def _body(bbmin):
    return types.SimpleNamespace(boundingBox=types.SimpleNamespace(minPoint=bbmin))


def _coll(items):
    return types.SimpleNamespace(count=len(items), item=lambda i, items=items: items[i])


def _occ(name, transl, bodies=None, children=None):
    return types.SimpleNamespace(
        name=name,
        transform=types.SimpleNamespace(translation=transl),
        bRepBodies=_coll(bodies or []),
        childOccurrences=_coll(children or []))


class TestChildGeometryMoved:
    def _wire(self, monkeypatch, occs):
        p = kernel.ChildGeometryMoved()
        monkeypatch.setattr(p, "_top_occurrences", lambda: occs)
        return p

    def test_propagated_move_confirms(self, monkeypatch):
        cbody = _body(_pt(0, 0, 0))
        child = _occ("Child:1", _pt(0, 0, 0), bodies=[cbody])
        wrapper = _occ("Wrapper:1", _pt(0, 0, 0), children=[child])
        p = self._wire(monkeypatch, [wrapper])

        def handler(**kw):
            wrapper.transform.translation = _pt(8.5, 8.5, 0)   # the joint moved the wrapper
            cbody.boundingBox.minPoint = _pt(8.5, 8.5, 0)      # ...and the nested child followed
            return _ok({"created": True})

        out = _payload(kernel.wrap(handler, [p])())
        assert out["child_geometry_move_verified"] is True

    def test_frozen_nested_child_bites_and_teaches_the_lock_workaround(self, monkeypatch):
        cbody = _body(_pt(0, 0, 0))
        child = _occ("Child:1", _pt(0, 0, 0), bodies=[cbody])
        wrapper = _occ("Wrapper:1", _pt(0, 0, 0), children=[child])
        p = self._wire(monkeypatch, [wrapper])

        def handler(**kw):
            wrapper.transform.translation = _pt(8.5, 8.5, 0)   # transform moved...
            # ...but cbody stays at origin: the non-propagation defect
            return _ok({"created": True})

        res = kernel.wrap(handler, [p])()
        assert res["isError"] is True
        assert "did not propagate" in res["message"].lower()
        assert "Wrapper:1" in res["message"]
        # The taught remedy is the multi-level-proven one: LOCK every nested free occurrence
        # (ground_to_parent), then joint the WRAPPER - jointing the nested occurrence directly
        # repositions the top-most free ancestor and strands deeper geometry.
        assert "LOCK" in res["message"] and "ground_to_parent" in res["message"]
        assert "joint the WRAPPER" in res["message"]

    def test_no_move_passes_trivially(self, monkeypatch):
        cbody = _body(_pt(0, 0, 0))
        child = _occ("Child:1", _pt(0, 0, 0), bodies=[cbody])
        wrapper = _occ("Wrapper:1", _pt(0, 0, 0), children=[child])
        p = self._wire(monkeypatch, [wrapper])
        out = _payload(kernel.wrap(lambda **kw: _ok({"created": True}), [p])())
        assert out["child_geometry_move_verified"] is True   # expected-zero motion passes

    def test_samples_the_deepest_nested_body_not_the_direct_body(self, monkeypatch):
        # The wrapper's OWN direct body follows the move, but the deeper nested child body is frozen -
        # the guard must sample the DEEPEST body (the nested child) so it still bites (the exact
        # 'directly-owned bodies moved, nested child did not' shape).
        direct = _body(_pt(0, 0, 0))
        nested = _body(_pt(0, 0, 0))
        child = _occ("Child:1", _pt(0, 0, 0), bodies=[nested])
        wrapper = _occ("Wrapper:1", _pt(0, 0, 0), bodies=[direct], children=[child])
        p = self._wire(monkeypatch, [wrapper])

        def handler(**kw):
            wrapper.transform.translation = _pt(8.5, 8.5, 0)
            direct.boundingBox.minPoint = _pt(8.5, 8.5, 0)     # the direct body moved
            # nested stays frozen
            return _ok({"created": True})

        res = kernel.wrap(handler, [p])()
        assert res["isError"] is True
        assert "did not propagate" in res["message"].lower()

    def test_world_transform_is_read_off_transform2_not_transform(self, monkeypatch):
        # .transform is the occurrence's LOCAL matrix - a nested proxy under a rotated+translated
        # parent reads its parent's placement OUT of it - while .transform2 is the composed WORLD
        # matrix. This kind compares the translation against a WORLD geometry point, so reading the
        # local one reports "moved" against geometry that did not move (or the reverse). Here the
        # LOCAL matrix stays put across the handler while the WORLD one moves with the geometry:
        # off .transform the kind would see an expected-zero move and pass anything.
        cbody = _body(_pt(0, 0, 0))
        child = _occ("Child:1", _pt(0, 0, 0), bodies=[cbody])
        wrapper = _occ("Wrapper:1", _pt(0, 0, 0), children=[child])
        wrapper.transform2 = types.SimpleNamespace(translation=_pt(50, 0, 0))
        p = self._wire(monkeypatch, [wrapper])

        def handler(**kw):
            wrapper.transform2.translation = _pt(58.5, 8.5, 0)   # world moved
            # the LOCAL matrix never changes - the parent absorbed the placement
            return _ok({"created": True})

        res = kernel.wrap(handler, [p])()
        assert res["isError"] is True                  # the world move did NOT reach the geometry
        assert "did not propagate" in res["message"].lower()

    def test_transform_is_the_fallback_when_transform2_is_absent(self, monkeypatch):
        # A build (or a proxy) carrying no transform2 must still be gated, not silently skipped.
        cbody = _body(_pt(0, 0, 0))
        child = _occ("Child:1", _pt(0, 0, 0), bodies=[cbody])
        wrapper = _occ("Wrapper:1", _pt(0, 0, 0), children=[child])
        assert not hasattr(wrapper, "transform2")
        p = self._wire(monkeypatch, [wrapper])

        def handler(**kw):
            wrapper.transform.translation = _pt(8.5, 8.5, 0)
            return _ok({"created": True})

        res = kernel.wrap(handler, [p])()
        assert res["isError"] is True
        assert "did not propagate" in res["message"].lower()

    def test_an_unreadable_occurrence_walk_is_disclosed_not_silently_passed(self, monkeypatch):
        # no part was sampled before the mutation, so nothing was gated - the flag reports that the
        # CHECK did not run rather than leaving the payload looking verified
        p = kernel.ChildGeometryMoved()
        monkeypatch.setattr(p, "_top_occurrences", lambda: None)
        out = _payload(kernel.wrap(lambda **kw: _ok({"created": True}), [p])())
        assert out["child_geometry_move_verified"] is False

    def test_a_part_that_cannot_be_re_read_is_named_not_skipped(self, monkeypatch):
        # one part propagates cleanly, the other's geometry can no longer be sampled after the
        # mutation: reporting a clean verified pass would claim coverage the walk never had
        gbody = _body(_pt(0, 0, 0))
        good = _occ("Good:1", _pt(0, 0, 0), bodies=[gbody])
        gone = _occ("Gone:1", _pt(0, 0, 0), bodies=[_body(_pt(0, 0, 0))])
        p = self._wire(monkeypatch, [good, gone])

        def handler(**kw):
            good.transform.translation = _pt(5, 0, 0)
            gbody.boundingBox.minPoint = _pt(5, 0, 0)
            gone.bRepBodies = _coll([])          # its geometry is no longer reachable
            return _ok({"created": True})

        out = _payload(kernel.wrap(handler, [p])())
        assert out["child_geometry_move_verified"] is False
        assert out["child_geometry_unverified"] == ["Gone:1"]

    def test_occurrence_without_bodies_is_skipped(self, monkeypatch):
        empty = _occ("Empty:1", _pt(0, 0, 0))   # no bodies anywhere - nothing to gate
        p = self._wire(monkeypatch, [empty])

        def handler(**kw):
            empty.transform.translation = _pt(8.5, 8.5, 0)
            return _ok({"created": True})

        out = _payload(kernel.wrap(handler, [p])())
        assert "child_geometry_move_verified" not in out


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


class _PoisonHealthyItem:
    """A HEALTHY item whose errorOrWarningMessage getter RAISES - measured on a fresh
    AssemblyConstraint (and a caught adsk error has rollback risk in some contexts), which is
    why the walk reads the message only inside the unhealthy branches."""
    def __init__(self, name):
        self.name = name
        self.healthState = 0
    @property
    def errorOrWarningMessage(self):
        raise RuntimeError("InternalValidationError on a healthy item")


class TestCheckInputKeys:
    """Registration-time wiring gate: a postcondition keyed to a handler parameter that does not
    exist would read None from kwargs, fall back to the kind's default target, verify the WRONG
    state, and still report success - so wrap() raises at import time instead."""

    class _KeyedPost(kernel.Postcondition):
        name = "keyed"
        input_keys = ("sketch_name",)

        def capture(self, kwargs):
            return None

        def verify(self, kwargs, captured, payload):
            return None

    def test_a_key_the_handler_lacks_raises_at_wrap_time(self):
        def handler(body_name="", distance=0.0):
            return _ok({})
        with pytest.raises(ValueError) as exc:
            kernel.wrap(handler, [self._KeyedPost()])
        msg = str(exc.value)
        assert "sketch_name" in msg and "handler" in msg
        assert "verify the wrong state" in msg

    def test_a_key_the_handler_takes_passes(self):
        def handler(sketch_name="", distance=0.0):
            return _ok({})
        assert callable(kernel.wrap(handler, [self._KeyedPost()]))

    def test_an_unintrospectable_handler_is_not_refused(self):
        # A C-level/builtin callable has no signature to check against - the gate stays quiet
        # rather than blocking registration on a check it cannot run.
        assert callable(kernel.wrap(min, [self._KeyedPost()]))

    def test_a_post_with_no_input_keys_never_trips_the_gate(self):
        def handler(body_name=""):
            return _ok({})
        assert callable(kernel.wrap(handler, [Fixed()]))


class TestPoisonGetterOnHealthyItems:
    def test_healthy_walk_never_reads_the_message_getter(self, monkeypatch):
        tl = _FakeTimeline([_PoisonHealthyItem("Constraint1")])
        p = kernel.FeatureHealthy()
        monkeypatch.setattr(p, "_timeline", lambda: tl)

        def handler(**kw):
            tl.items.append(_PoisonHealthyItem("Constraint2"))
            return _ok({"done": True})

        out = _payload(kernel.wrap(handler, [p])())
        assert out["features_verified"] == 1        # a raise here would have failed the wrap

    def test_unhealthy_item_still_gets_its_message_read(self, monkeypatch):
        tl = _FakeTimeline()
        p = kernel.FeatureHealthy()
        monkeypatch.setattr(p, "_timeline", lambda: tl)

        def handler(**kw):
            tl.items.append(_FakeTimelineItem("Broken1", 2, "No target body"))
            return _ok({"done": True})

        res = kernel.wrap(handler, [p])()
        assert res["isError"] is True and "No target body" in res["message"]
