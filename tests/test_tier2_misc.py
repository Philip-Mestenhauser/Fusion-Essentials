"""Unit tests for assorted Tier-2 helpers: doc_update_xref, timeline, cam_generate.

Each tool has one or two pure helpers worth pinning:
  - doc_update_xref._ref_name      — safe name extraction with a fallback.
  - timeline._entity_type       — group vs entity-class-name vs None.
  - cam_generate._live_op_tally — the operation-state tally that drives the
    progress signal (valid / out_of_date / generating counts).
"""

from types import SimpleNamespace

from conftest import load_tool

uxref = load_tool("update_xref")
timeline = load_tool("timeline")
gtp = load_tool("generate_toolpaths")


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
