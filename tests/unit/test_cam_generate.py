"""Unit tests for ``cam_generate.py`` — launch/poll toolpath generation.

``_live_op_tally`` is already pinned in test_tier2_misc.py. This file covers the rest of the real
logic (no live Fusion): target resolution through the shared ``_cam_common.resolve_cam_node``
(setup/folder classification, the duplicate-name refusal, not-found), ``_collect_op_health``
(warning/error collection and the EMPTY-toolpath text derivation), and the two handlers' branching —
generate's skip-valid short-circuit and target-not-found, and status's handle/'latest' resolution,
the unknown-handle guard, the pump-budget clamp, the "nothing generating but out-of-date remain"
stall warning, and the NO-HANDLE live-poll path (document + by-name target) that reports an inline/UI
generation with no cam_generate handle.
"""

import json
from types import SimpleNamespace

from conftest import load_tool, _NamedCollection
from conftest import FakeSetup as SharedSetup, FakeCAMFolder as SharedFolder, FakeOperation as SharedOp

gen = load_tool("cam_generate")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── target resolution (via the shared _cam_common.resolve_cam_node) ─────────────────────────────────

class _FakeCAM:
    def __init__(self, setups):
        s = list(setups)
        self.setups = SimpleNamespace(count=len(s), item=lambda i: s[i])
        self.generate_calls = []

    def generateToolpath(self, tgt):
        self.generate_calls.append(("target", tgt))
        return SimpleNamespace(numberOfOperations=1)

    def generateAllToolpaths(self, skip_valid):
        self.generate_calls.append(("all", skip_valid))
        return SimpleNamespace(numberOfOperations=3)


def _setup(name, ops=()):
    return SimpleNamespace(name=name, allOperations=_NamedCollection(ops))


class TestTargetResolution:
    """cam_generate's 'target' resolves through the shared _cam_common resolver - pin the
    classification, the folder reachability, and THE contract fix: a duplicated operation name is
    refused at this entry point, never first-matched."""

    def _install(self, monkeypatch, setups):
        cam = _FakeCAM(setups)
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        return cam

    def test_setup_target_launches_scoped_generation_ci(self, monkeypatch):
        setup = SharedSetup("Roughing", ops=[SharedOp("Face1", operation_state=1)])
        cam = self._install(monkeypatch, [setup])
        out = _payload(gen.generate_handler(target="roughing"))   # case-insensitive exact
        assert out["launched"] is True
        assert cam.generate_calls == [("target", setup)]
        assert out["target"] == "setup 'roughing'"

    def test_folder_target_resolves_via_explicit_folder_walk(self, monkeypatch):
        # setup.allOperations DROPS folder containers (live-verified), so a folder target is only
        # reachable through the shared walk's explicit .folders recursion.
        folder = SharedFolder("Drilling", ops=[SharedOp("D1", operation_state=1)])
        setup = SharedSetup("S", folders=[folder])
        cam = self._install(monkeypatch, [setup])
        out = _payload(gen.generate_handler(target="Drilling", skip_valid=False))
        assert out["launched"] is True
        assert cam.generate_calls == [("target", folder)]
        assert out["target"] == "folder 'Drilling'"

    def test_duplicate_op_name_across_setups_is_refused(self, monkeypatch):
        # "Drill1" exists in TWO setups - generating by that name must REFUSE with both setup
        # paths and launch NOTHING, never regenerate whichever op the walk met first.
        cam = self._install(monkeypatch, [SharedSetup("Setup1", ops=[SharedOp("Drill1")]),
                                          SharedSetup("Setup2", ops=[SharedOp("Drill1")])])
        res = gen.generate_handler(target="Drill1", skip_valid=False)
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "Setup1 / Drill1" in res["message"] and "Setup2 / Drill1" in res["message"]
        assert cam.generate_calls == []                      # nothing was launched


# ── _collect_op_health: warnings / errors / empty derivation ────────────────────────────────────────

def _op(name, warning=None, error=None):
    return SimpleNamespace(
        name=name,
        hasWarning=warning is not None, warning=warning or "",
        hasError=error is not None, error=error or "",
    )


def _cam_with_ops(ops):
    setup = SimpleNamespace(allOperations=_NamedCollection(ops))
    return SimpleNamespace(setups=SimpleNamespace(count=1, item=lambda i: setup))


class TestCollectOpHealth:
    def _wire_cast(self, monkeypatch):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))

    def test_warnings_and_errors_separated(self, monkeypatch):
        self._wire_cast(monkeypatch)
        ops = [_op("a", warning="Spindle too fast"), _op("b", error="bad geometry"), _op("c")]
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (_cam_with_ops(ops), None))
        out = gen._collect_op_health()
        assert out["warnings"] == [{"name": "a", "warning": "Spindle too fast"}]
        assert out["errors"] == [{"name": "b", "error": "bad geometry"}]

    def test_empty_toolpath_derived_from_warning_text(self, monkeypatch):
        self._wire_cast(monkeypatch)
        ops = [_op("face", warning="The toolpath is empty.")]
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (_cam_with_ops(ops), None))
        out = gen._collect_op_health()
        # surfaces in BOTH warnings and the convenience 'empty' list
        assert out["empty"] == ["face"]
        assert out["warnings"][0]["name"] == "face"

    def test_warning_text_stripped(self, monkeypatch):
        self._wire_cast(monkeypatch)
        ops = [_op("a", warning="  padded  ")]
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (_cam_with_ops(ops), None))
        out = gen._collect_op_health()
        assert out["warnings"][0]["warning"] == "padded"


# ── generate_handler: scope selection + skip-valid short-circuit ────────────────────────────────────

class TestGenerateHandler:
    def test_whole_document_calls_generate_all(self, monkeypatch):
        cam = _FakeCAM([_setup("S")])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = _payload(gen.generate_handler(target=""))
        assert out["launched"] is True
        assert cam.generate_calls[0][0] == "all"

    def test_target_not_found_errors(self, monkeypatch):
        cam = _FakeCAM([_setup("S", [SimpleNamespace(name="Face1")])])
        import adsk.cam
        monkeypatch.setattr(adsk.cam.CAMFolder, "cast", staticmethod(lambda x: None))
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        res = gen.generate_handler(target="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_skip_valid_short_circuits_already_valid_operation(self, monkeypatch):
        op = SimpleNamespace(name="Face1", operationState=0)   # 0 = valid/up-to-date
        cam = _FakeCAM([_setup("S", [op])])
        import adsk.cam
        monkeypatch.setattr(adsk.cam.CAMFolder, "cast", staticmethod(lambda x: None))
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = _payload(gen.generate_handler(target="Face1", skip_valid=True))
        assert out["launched"] is False and out["skipped"] is True
        assert cam.generate_calls == []          # never launched a generation

    def test_skip_valid_false_forces_regen_of_valid_op(self, monkeypatch):
        op = SimpleNamespace(name="Face1", operationState=0)
        cam = _FakeCAM([_setup("S", [op])])
        import adsk.cam
        monkeypatch.setattr(adsk.cam.CAMFolder, "cast", staticmethod(lambda x: None))
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = _payload(gen.generate_handler(target="Face1", skip_valid=False))
        assert out["launched"] is True
        assert cam.generate_calls[0][0] == "target"


# ── status_handler: guards, handle resolution, clamp, stall warning ─────────────────────────────────

class TestStatusHandler:
    def setup_method(self):
        gen._GENERATIONS.clear()
        gen._HANDLE_SEQ[0] = 0

    def test_unknown_handle_lists_active(self):
        gen._GENERATIONS["gen1"] = {"future": SimpleNamespace(isGenerationCompleted=True),
                                    "target": "t", "started_at": 0, "total": 1}
        res = gen.status_handler(handle="gen99")
        assert res["isError"] is True and "gen1" in res["message"]

    def _completed_entry(self):
        # numberOfCompleted is pass-through data, not a completion signal - live it reads 0 even
        # when isGenerationCompleted is True (cam-generate-future in tests/live/VERIFIED_API_FACTS.md).
        return {"future": SimpleNamespace(isGenerationCompleted=True, numberOfOperations=2,
                                          numberOfCompleted=2),
                "target": "all setups", "started_at": 0.0, "total": 2}

    # status_handler delegates CAM health to _cam_common.live_readiness (the single source) - tests
    # patch that seam (gen._cam_common.live_readiness -> (signal, None)) instead of a local tally.
    def _states(self, **kw):
        base = {"valid": 0, "out_of_date": 0, "errored": 0, "generating": 0, "suppressed": 0,
                "total": 0, "active": None, "setups_errored": 0, "programs_errored": 0,
                "readiness": "", "samples": {"op": None, "setup": None, "program": None}}
        base.update(kw)
        return base

    def _readiness(self, **kw):
        base = self._states(**kw)
        return lambda: (base, None)

    def test_latest_resolves_to_last_handle(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._GENERATIONS["gen2"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 2
        monkeypatch.setattr(gen._cam_common, "live_readiness", self._readiness(readiness="ready to post."))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda: {"warnings": [], "errors": [], "empty": []})
        out = _payload(gen.status_handler(handle="latest", pump_seconds=0))
        assert out["handle"] == "gen2" and out["completed"] is True

    def test_stall_warning_when_nothing_generating_but_ood_remains(self, monkeypatch):
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=False, numberOfOperations=2,
                                      numberOfCompleted=0),
            "target": "all setups", "started_at": 0.0, "total": 2}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(out_of_date=2, generating=0, total=2,
                                            readiness="0 of 2 active ops valid - run cam_generate to finish the rest."))
        out = _payload(gen.status_handler(handle="gen1", pump_seconds=0))
        assert out["completed"] is False
        assert "WARNING" in out["note"]

    def test_errored_op_surfaced_while_still_generating(self, monkeypatch):
        # An errored op (hasError) will NEVER finish, so a still-generating poll must
        # flag it NOW (the BLOCKER readiness + one sample + a pointer to cam_get), not wait for a
        # completion that can't come. The verdict comes from _cam_common.live_readiness (one source).
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=False, numberOfOperations=3,
                                      numberOfCompleted=0),
            "target": "all setups", "started_at": 0.0, "total": 3}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(errored=1, generating=2, total=3,
                                            readiness="BLOCKER: 1 operation(s) have errors - the job will not post until fixed.",
                                            samples={"op": {"name": "Rough to Model Top",
                                                            "error": "Top height must not be below the bottom height"},
                                                     "setup": None, "program": None}))
        out = _payload(gen.status_handler(handle="gen1", pump_seconds=0))
        assert out["completed"] is False
        assert out["live_states"]["errored"] == 1
        # the note carries the BLOCKER verdict, the sample op, and points at the deeper read
        assert "BLOCKER" in out["note"]
        assert "Rough to Model Top" in out["note"]
        assert "will NOT complete" in out["note"]
        assert "cam_get(include=['operations'])" in out["note"]

    def test_setup_error_blocks_via_readiness(self, monkeypatch):
        # a faulted SETUP is in the BLOCKER readiness from live_readiness - status surfaces it + stops.
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=False, numberOfOperations=2,
                                      numberOfCompleted=0),
            "target": "all setups", "started_at": 0.0, "total": 2}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=1, out_of_date=1, generating=1, total=2, setups_errored=1,
                                            readiness="BLOCKER: 1 setup(s) have errors - the job will not post until fixed.",
                                            samples={"op": None, "program": None,
                                                     "setup": {"name": "Op1", "error": "WCS orientation is invalid"}}))
        out = _payload(gen.status_handler(handle="gen1", pump_seconds=0))
        assert "BLOCKER" in out["note"]
        assert "Op1" in out["note"]
        assert "will NOT complete" in out["note"]

    def test_completed_waits_for_live_states_to_settle(self, monkeypatch):
        # the Future flips isGenerationCompleted a poll BEFORE live op state settles (live: completed
        # while live_states showed generating=3). completed must stay False until live_states.generating
        # hits 0, so the caller never reads a premature done.
        gen._GENERATIONS["gen1"] = self._completed_entry()      # future says done
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(out_of_date=3, generating=3, total=3,
                                            readiness="0 of 3 active ops valid - run cam_generate to finish the rest."))
        out = _payload(gen.status_handler(handle="gen1", pump_seconds=0))
        assert out["completed"] is False
        assert out["live_states"]["generating"] == 3
        assert "gen1" in gen._GENERATIONS          # not popped while still settling

    def test_completed_when_future_done_and_live_settled(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=2, generating=0, total=2, readiness="ready to post."))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda: {"warnings": [], "errors": [], "empty": []})
        out = _payload(gen.status_handler(handle="gen1", pump_seconds=0))
        assert out["completed"] is True

    def test_pump_budget_is_clamped(self, monkeypatch):
        # a huge pump_seconds must be clamped to <=10; with a completed future no pumping happens.
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness", self._readiness(readiness="ready to post."))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda: {"warnings": [], "errors": [], "empty": []})
        out = _payload(gen.status_handler(handle="gen1", pump_seconds=9999))
        # completed already -> no pumping loop entered, pumped stays 0
        assert out["pumped_seconds"] == 0.0


# ── status_handler live-poll path: NO cam_generate handle (inline / UI generation) ──────────────────

def _live_op(name, state=0, generating=False, error=False):
    return SimpleNamespace(name=name, operationState=state, isGenerating=generating,
                           hasError=error, error="broken" if error else "")


class TestStatusLivePoll:
    def setup_method(self):
        gen._GENERATIONS.clear()
        gen._HANDLE_SEQ[0] = 0

    def _states(self, **kw):
        return TestStatusHandler._states(TestStatusHandler(), **kw)

    def _readiness(self, **kw):
        return TestStatusHandler._readiness(TestStatusHandler(), **kw)

    # An op generated INLINE (cam_create_operation(generate=true), cam_select_geometry, or the UI) has
    # no cam_generate handle. Polling with NO handle must report its live generation state, not refuse
    # for lack of a launched generation.
    def test_no_handle_reports_inline_generation(self, monkeypatch):
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(generating=1, out_of_date=1, total=2,
                                            readiness="0 of 2 active ops valid - run cam_generate to finish the rest."))
        out = _payload(gen.status_handler(pump_seconds=0))     # no handle, no generations registered
        assert out["handle"] is None                            # no self-minted handle
        assert out["target"] == "document"
        assert out["completed"] is False
        assert out["live_states"]["generating"] == 1

    def test_document_completed_only_when_nothing_generating(self, monkeypatch):
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=3, generating=0, total=3,
                                            readiness="3 of 3 active ops valid - ready to post."))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda: {"warnings": [], "errors": [], "empty": []})
        out = _payload(gen.status_handler(pump_seconds=0))
        assert out["completed"] is True and out["handle"] is None

    def test_live_errored_op_flagged_not_generating_forever(self, monkeypatch):
        # an errored op will NEVER finish - a still-generating live poll must flag the BLOCKER now, not
        # report it as generating forever.
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(errored=1, generating=1, total=2,
                                            readiness="BLOCKER: 1 operation(s) have errors - the job will not post until fixed.",
                                            samples={"op": {"name": "Bad Op", "error": "broken"},
                                                     "setup": None, "program": None}))
        out = _payload(gen.status_handler(pump_seconds=0))
        assert out["completed"] is False
        assert "BLOCKER" in out["note"] and "will NOT complete" in out["note"]
        assert "Bad Op" in out["note"]

    def test_target_by_name_reports_that_setups_state(self, monkeypatch):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        setup = _setup("Roughing", [_live_op("Op1", state=0),
                                    _live_op("Op2", state=1, generating=True)])
        cam = _FakeCAM([setup])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = _payload(gen.status_handler(target="Roughing", pump_seconds=0))
        assert "Roughing" in out["target"]
        assert out["live_states"]["valid"] == 1
        assert out["live_states"]["generating"] == 1
        assert out["completed"] is False                        # Op2 still generating

    def test_target_by_name_errored_op_is_its_own_bucket(self, monkeypatch):
        # an errored op in a scoped walk is counted as errored (never out_of_date/generating).
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        setup = _setup("Finish", [_live_op("Bad", error=True), _live_op("Good", state=0)])
        cam = _FakeCAM([setup])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = _payload(gen.status_handler(target="Finish", pump_seconds=0))
        assert out["live_states"]["errored"] == 1
        assert out["live_states"]["valid"] == 1
        assert out["completed"] is True                         # nothing generating (errored != generating)
        assert "BLOCKER" in out["note"]

    def test_target_not_found_errors(self, monkeypatch):
        cam = _FakeCAM([_setup("Roughing")])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        res = gen.status_handler(target="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_live_poll_pumps_then_returns_non_blocking(self, monkeypatch):
        # pumps a bounded burst (calls adsk.doEvents) and RETURNS the moment nothing is generating -
        # never sleeps to completion. Stub _scope_state so the first read is generating, the next is done.
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda: {"warnings": [], "errors": [], "empty": []})
        pumps = {"n": 0}
        monkeypatch.setattr(gen.adsk, "doEvents", lambda: pumps.__setitem__("n", pumps["n"] + 1),
                            raising=False)
        monkeypatch.setattr(gen.time, "sleep", lambda s: None)
        seq = iter([self._states(generating=1, total=1),
                    self._states(valid=1, generating=0, total=1,
                                 readiness="1 of 1 active ops valid - ready to post.")])
        monkeypatch.setattr(gen, "_scope_state", lambda cam, target: (next(seq), "document", None))
        out = _payload(gen.status_handler(pump_seconds=5))
        assert pumps["n"] >= 1                                   # it pumped the main-thread loop
        assert out["completed"] is True                         # broke out as soon as generating hit 0
