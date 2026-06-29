"""Unit tests for assorted Tier-2 helpers: doc_update_xref, timeline, cam_generate.

Each tool has one or two pure helpers worth pinning:
  - doc_update_xref._ref_name      — safe name extraction with a fallback.
  - timeline._entity_type       — group vs entity-class-name vs None.
  - cam_generate._live_op_tally — the operation-state tally that drives the
    progress signal (valid / out_of_date / generating counts).
"""

from types import SimpleNamespace

from conftest import load_tool

uxref = load_tool("doc_update_xref")
timeline = load_tool("design_get_timeline")
gtp = load_tool("cam_generate")


# ── doc_update_xref._ref_name ──────────────────────────────────────────────────

class TestRefName:
    def test_reads_datafile_name(self):
        ref = SimpleNamespace(dataFile=SimpleNamespace(name="Vise.f3d"))
        assert uxref._ref_name(ref) == "Vise.f3d"

    def test_missing_datafile_falls_back(self):
        # dataFile access raises -> the helper must not crash, returns "(unknown)".
        class Ref:
            @property
            def dataFile(self):
                raise RuntimeError("no data file")
        assert uxref._ref_name(Ref()) == "(unknown)"


# ── doc_update_xref.handler ────────────────────────────────────────────────

class _XRef:
    def __init__(self, name, ood=False, version=1, latest_returns=True, after_version=None):
        self.dataFile = SimpleNamespace(name=name)
        self.isOutOfDate = ood
        self.version = version
        self._latest_returns = latest_returns
        self._after = after_version if after_version is not None else version + 1

    def getLatestVersion(self):
        if self._latest_returns:
            self.version = self._after
        return self._latest_returns


class _XRefColl:
    def __init__(self, refs):
        self._refs = list(refs)

    @property
    def count(self):
        return len(self._refs)

    def item(self, i):
        return self._refs[i]


def _install_xref(monkeypatch, refs):
    doc = SimpleNamespace(documentReferences=_XRefColl(refs))
    monkeypatch.setattr(uxref, "app", SimpleNamespace(activeDocument=doc))
    return doc


def _xref_payload(res):
    assert res["isError"] is False, res
    return _json.loads(res["content"][0]["text"])


class TestUpdateXrefHandler:
    def test_no_active_document(self, monkeypatch):
        monkeypatch.setattr(uxref, "app", SimpleNamespace(activeDocument=None))
        res = uxref.handler()
        assert res["isError"] is True and "No active document" in res["message"]

    def test_no_references_is_a_clean_noop(self, monkeypatch):
        _install_xref(monkeypatch, [])
        out = _xref_payload(uxref.handler())
        assert out["updated_count"] == 0
        assert "no external references" in out["note"].lower()

    def test_updates_out_of_date_ref(self, monkeypatch):
        _install_xref(monkeypatch, [_XRef("Vise.f3d", ood=True, version=2, after_version=5)])
        out = _xref_payload(uxref.handler())
        assert out["updated_count"] == 1
        u = out["updated"][0]
        assert u["name"] == "Vise.f3d"
        assert u["version_before"] == 2 and u["version_after"] == 5
        assert u["was_out_of_date"] is True

    def test_up_to_date_ref_is_skipped(self, monkeypatch):
        _install_xref(monkeypatch, [_XRef("Fresh.f3d", ood=False)])
        out = _xref_payload(uxref.handler())   # only_out_of_date default true
        assert out["updated_count"] == 0
        assert out["skipped"][0]["name"] == "Fresh.f3d"
        assert "up to date" in out["skipped"][0]["reason"]

    def test_force_refresh_when_only_out_of_date_false(self, monkeypatch):
        _install_xref(monkeypatch, [_XRef("Fresh.f3d", ood=False, version=3, after_version=4)])
        out = _xref_payload(uxref.handler(only_out_of_date=False))
        assert out["updated_count"] == 1
        assert out["updated"][0]["version_after"] == 4

    def test_name_filter_targets_one_ref(self, monkeypatch):
        _install_xref(monkeypatch, [
            _XRef("A.f3d", ood=True), _XRef("B.f3d", ood=True)])
        out = _xref_payload(uxref.handler(name="B.f3d"))
        assert {u["name"] for u in out["updated"]} == {"B.f3d"}

    def test_unknown_name_lists_available(self, monkeypatch):
        _install_xref(monkeypatch, [_XRef("A.f3d", ood=True)])
        res = uxref.handler(name="Ghost.f3d")
        assert res["isError"] is True
        assert "Ghost.f3d" in res["message"] and "A.f3d" in res["message"]

    def test_get_latest_false_is_an_error(self, monkeypatch):
        _install_xref(monkeypatch, [_XRef("A.f3d", ood=True, latest_returns=False)])
        res = uxref.handler()
        assert res["isError"] is True
        assert "failed to update" in res["message"]


# ── timeline._entity_type ──────────────────────────────────────────────────

class TestEntityType:
    def test_group_returns_timelinegroup(self):
        obj = SimpleNamespace(isGroup=True)
        assert timeline._entity_type(obj) == "TimelineGroup"

    def test_entity_class_name(self):
        class ExtrudeFeature:
            pass
        obj = SimpleNamespace(isGroup=False, entity=ExtrudeFeature())
        assert timeline._entity_type(obj) == "ExtrudeFeature"

    def test_none_entity_returns_none(self):
        obj = SimpleNamespace(isGroup=False, entity=None)
        assert timeline._entity_type(obj) is None


# ── timeline._object_summary ───────────────────────────────────────────────

import json as _json


def _tlobj(index=0, name="Extrude1", is_group=False, suppressed=False, rolled_back=False,
           parent_group=None, health=0, message=None, entity_name="ExtrudeFeature"):
    class _Ent:
        pass
    _Ent.__name__ = entity_name
    pg = SimpleNamespace(name=parent_group) if parent_group is not None else None
    return SimpleNamespace(
        index=index, name=name, isGroup=is_group, isSuppressed=suppressed,
        isRolledBack=rolled_back, parentGroup=pg, healthState=health,
        errorOrWarningMessage=message,
        entity=(None if is_group else _Ent()),
    )


class TestObjectSummary:
    def test_maps_known_health_label(self):
        out = timeline._object_summary(_tlobj(health=2))
        assert out["health"] == "error"

    def test_unknown_health_stays_numeric(self):
        out = timeline._object_summary(_tlobj(health=99))
        assert out["health"] == 99

    def test_group_type_and_flag(self):
        out = timeline._object_summary(_tlobj(is_group=True, name="Grp"))
        assert out["is_group"] is True
        assert out["type"] == "TimelineGroup"

    def test_parent_group_name_surfaced(self):
        out = timeline._object_summary(_tlobj(parent_group="Wheels"))
        assert out["parent_group"] == "Wheels"

    def test_no_parent_group_is_none(self):
        out = timeline._object_summary(_tlobj(parent_group=None))
        assert out["parent_group"] is None

    def test_message_only_when_present(self):
        with_msg = timeline._object_summary(_tlobj(message="needs rebuild"))
        without = timeline._object_summary(_tlobj(message=None))
        assert with_msg["message"] == "needs rebuild"
        assert "message" not in without

    def test_suppressed_and_rolled_back_flags(self):
        out = timeline._object_summary(_tlobj(suppressed=True, rolled_back=True))
        assert out["is_suppressed"] is True and out["is_rolled_back"] is True


# ── timeline.handler: filters + groups roster + truncation ─────────────────

class _Timeline:
    def __init__(self, items, marker=0, groups=()):
        self._items = list(items)
        self.markerPosition = marker
        self.timelineGroups = list(groups)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


def _install_tl(monkeypatch, timeline_obj):
    design = SimpleNamespace(timeline=timeline_obj)
    monkeypatch.setattr(timeline._common, "design", lambda: design)
    return design


def _tl_payload(res):
    assert res["isError"] is False, res
    return _json.loads(res["content"][0]["text"])


class TestTimelineHandler:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(timeline._common, "design", lambda: None)
        res = timeline.handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_returns_all_with_marker_and_count(self, monkeypatch):
        tl = _Timeline([_tlobj(0, "A"), _tlobj(1, "B")], marker=2)
        _install_tl(monkeypatch, tl)
        out = _tl_payload(timeline.handler())
        assert out["count"] == 2
        assert out["returned"] == 2
        assert out["marker_position"] == 2
        assert [o["name"] for o in out["timeline"]] == ["A", "B"]

    def test_include_suppressed_false_omits_suppressed(self, monkeypatch):
        tl = _Timeline([_tlobj(0, "Live"), _tlobj(1, "Hidden", suppressed=True)])
        _install_tl(monkeypatch, tl)
        out = _tl_payload(timeline.handler(include_suppressed=False))
        assert out["returned"] == 1
        assert [o["name"] for o in out["timeline"]] == ["Live"]
        # but count still reflects the full timeline
        assert out["count"] == 2

    def test_group_filter_returns_only_that_group(self, monkeypatch):
        tl = _Timeline([
            _tlobj(0, "A", parent_group="Wheels"),
            _tlobj(1, "B", parent_group="Frame"),
            _tlobj(2, "C", parent_group="Wheels"),
        ])
        _install_tl(monkeypatch, tl)
        out = _tl_payload(timeline.handler(group="Wheels"))
        assert [o["name"] for o in out["timeline"]] == ["A", "C"]

    def test_groups_roster_maps_name_to_member_count(self, monkeypatch):
        groups = [SimpleNamespace(name="Wheels", count=3), SimpleNamespace(name="Frame", count=1)]
        tl = _Timeline([_tlobj(0, "A")], groups=groups)
        _install_tl(monkeypatch, tl)
        out = _tl_payload(timeline.handler())
        assert out["groups"] == {"Wheels": 3, "Frame": 1}

    def test_truncation_at_cap(self, monkeypatch):
        monkeypatch.setattr(timeline, "_MAX_ITEMS", 2)
        tl = _Timeline([_tlobj(i, f"F{i}") for i in range(5)])
        _install_tl(monkeypatch, tl)
        out = _tl_payload(timeline.handler())
        assert out["returned"] == 2
        assert out["truncated"] is True
        assert "truncated" in out["note"].lower()


# ── cam_generate._live_op_tally ──────────────────────────────────────

def _op(state, generating=False, progress=None, name="op"):
    return SimpleNamespace(
        operationState=state, isGenerating=generating,
        generatingProgress=progress, name=name,
    )


def _cam_with(ops):
    """Fake CAM with a single setup holding the given operations."""
    setup = SimpleNamespace(allOperations=list(ops))

    class _Setups:
        count = 1

        def item(self, i):
            return setup
    return SimpleNamespace(setups=_Setups())


class TestLiveOpTally:
    def test_counts_states(self, monkeypatch):
        # states: 0=valid, 1/3=out_of_date, 2=suppressed
        ops = [_op(0), _op(0), _op(1), _op(3), _op(2)]
        monkeypatch.setattr(gtp, "_get_cam", lambda: (_cam_with(ops), None))
        tally = gtp._live_op_tally()
        assert tally["valid"] == 2
        assert tally["out_of_date"] == 2     # one invalid + one no_toolpath
        assert tally["suppressed"] == 1
        assert tally["total"] == 5

    def test_active_op_captured_with_real_progress(self, monkeypatch):
        ops = [_op(1, generating=True, progress="Pending", name="queued"),
               _op(1, generating=True, progress="42.0%", name="running")]
        monkeypatch.setattr(gtp, "_get_cam", lambda: (_cam_with(ops), None))
        tally = gtp._live_op_tally()
        assert tally["generating"] == 2
        # The op with real progress wins over the "Pending" one.
        assert tally["active"]["op"] == "running"
        assert tally["active"]["progress"] == "42.0%"

    def test_cam_unavailable_returns_none(self, monkeypatch):
        monkeypatch.setattr(gtp, "_get_cam", lambda: (None, "no CAM"))
        assert gtp._live_op_tally() is None
