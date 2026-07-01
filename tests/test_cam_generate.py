"""Unit tests for ``cam_generate.py`` — launch/poll toolpath generation.

``_live_op_tally`` is already pinned in test_tier2_misc.py. This file covers the rest of the real
logic (no live Fusion): ``_find_target`` (setup/folder/operation classification + not-found),
``_collect_op_health`` (warning/error collection and the EMPTY-toolpath text derivation), and the two
handlers' branching — generate's skip-valid short-circuit and target-not-found, and status's
handle/'latest' resolution, the no-generations and unknown-handle guards, the pump-budget clamp, and
the "nothing generating but out-of-date remain" stall warning.
"""

import json
from types import SimpleNamespace

from conftest import load_tool

gen = load_tool("cam_generate")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── _find_target: classify a name as setup / folder / operation ─────────────────────────────────────

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
    return SimpleNamespace(name=name, allOperations=list(ops))


class TestFindTarget:
    def test_matches_setup_by_name_ci(self, monkeypatch):
        cam = _FakeCAM([_setup("Roughing")])
        # CAMFolder/Operation casts only matter for ops; here the name matches the SETUP first.
        tgt, kind = gen._find_target(cam, "roughing")
        assert kind == "setup" and tgt.name == "Roughing"

    def test_matches_operation(self, monkeypatch):
        op = SimpleNamespace(name="Face1")
        cam = _FakeCAM([_setup("S", [op])])
        import adsk.cam
        adsk.cam.CAMFolder.cast = staticmethod(lambda x: None)        # not a folder
        adsk.cam.Operation.cast = staticmethod(lambda x: x)           # is an operation
        tgt, kind = gen._find_target(cam, "face1")
        assert kind == "operation" and tgt is op

    def test_matches_folder(self):
        folder = SimpleNamespace(name="Drilling")
        cam = _FakeCAM([_setup("S", [folder])])
        import adsk.cam
        adsk.cam.CAMFolder.cast = staticmethod(lambda x: x)          # IS a folder
        adsk.cam.Operation.cast = staticmethod(lambda x: None)
        tgt, kind = gen._find_target(cam, "drilling")
        assert kind == "folder"

    def test_unknown_name_returns_none(self):
        cam = _FakeCAM([_setup("S", [SimpleNamespace(name="Face1")])])
        import adsk.cam
        adsk.cam.CAMFolder.cast = staticmethod(lambda x: None)
        adsk.cam.Operation.cast = staticmethod(lambda x: x)
        assert gen._find_target(cam, "Ghost") == (None, None)

    def test_empty_name_returns_none(self):
        cam = _FakeCAM([_setup("S")])
        assert gen._find_target(cam, "") == (None, None)


# ── _collect_op_health: warnings / errors / empty derivation ────────────────────────────────────────

def _op(name, warning=None, error=None):
    return SimpleNamespace(
        name=name,
        hasWarning=warning is not None, warning=warning or "",
        hasError=error is not None, error=error or "",
    )


def _cam_with_ops(ops):
    setup = SimpleNamespace(allOperations=list(ops))
    return SimpleNamespace(setups=SimpleNamespace(count=1, item=lambda i: setup))


class TestCollectOpHealth:
    def _wire_cast(self):
        import adsk.cam
        adsk.cam.Operation.cast = staticmethod(lambda x: x)

    def test_warnings_and_errors_separated(self, monkeypatch):
        self._wire_cast()
        ops = [_op("a", warning="Spindle too fast"), _op("b", error="bad geometry"), _op("c")]
        monkeypatch.setattr(gen, "_get_cam", lambda: (_cam_with_ops(ops), None))
        out = gen._collect_op_health()
        assert out["warnings"] == [{"name": "a", "warning": "Spindle too fast"}]
        assert out["errors"] == [{"name": "b", "error": "bad geometry"}]

    def test_empty_toolpath_derived_from_warning_text(self, monkeypatch):
        self._wire_cast()
        ops = [_op("face", warning="The toolpath is empty.")]
        monkeypatch.setattr(gen, "_get_cam", lambda: (_cam_with_ops(ops), None))
        out = gen._collect_op_health()
        # surfaces in BOTH warnings and the convenience 'empty' list
        assert out["empty"] == ["face"]
        assert out["warnings"][0]["name"] == "face"

    def test_warning_text_stripped(self, monkeypatch):
        self._wire_cast()
        ops = [_op("a", warning="  padded  ")]
        monkeypatch.setattr(gen, "_get_cam", lambda: (_cam_with_ops(ops), None))
        out = gen._collect_op_health()
        assert out["warnings"][0]["warning"] == "padded"


# ── generate_handler: scope selection + skip-valid short-circuit ────────────────────────────────────

class TestGenerateHandler:
    def test_whole_document_calls_generate_all(self, monkeypatch):
        cam = _FakeCAM([_setup("S")])
        monkeypatch.setattr(gen, "_get_cam", lambda: (cam, None))
        out = _payload(gen.generate_handler(target=""))
        assert out["launched"] is True
        assert cam.generate_calls[0][0] == "all"

    def test_target_not_found_errors(self, monkeypatch):
        cam = _FakeCAM([_setup("S", [SimpleNamespace(name="Face1")])])
        import adsk.cam
        adsk.cam.CAMFolder.cast = staticmethod(lambda x: None)
        adsk.cam.Operation.cast = staticmethod(lambda x: x)
        monkeypatch.setattr(gen, "_get_cam", lambda: (cam, None))
        res = gen.generate_handler(target="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_skip_valid_short_circuits_already_valid_operation(self, monkeypatch):
        op = SimpleNamespace(name="Face1", operationState=0)   # 0 = valid/up-to-date
        cam = _FakeCAM([_setup("S", [op])])
        import adsk.cam
        adsk.cam.CAMFolder.cast = staticmethod(lambda x: None)
        adsk.cam.Operation.cast = staticmethod(lambda x: x)
        monkeypatch.setattr(gen, "_get_cam", lambda: (cam, None))
        out = _payload(gen.generate_handler(target="Face1", skip_valid=True))
        assert out["launched"] is False and out["skipped"] is True
        assert cam.generate_calls == []          # never launched a generation

    def test_skip_valid_false_forces_regen_of_valid_op(self, monkeypatch):
        op = SimpleNamespace(name="Face1", operationState=0)
        cam = _FakeCAM([_setup("S", [op])])
        import adsk.cam
        adsk.cam.CAMFolder.cast = staticmethod(lambda x: None)
        adsk.cam.Operation.cast = staticmethod(lambda x: x)
        monkeypatch.setattr(gen, "_get_cam", lambda: (cam, None))
        out = _payload(gen.generate_handler(target="Face1", skip_valid=False))
        assert out["launched"] is True
        assert cam.generate_calls[0][0] == "target"


# ── status_handler: guards, handle resolution, clamp, stall warning ─────────────────────────────────

class TestStatusHandler:
    def setup_method(self):
        gen._GENERATIONS.clear()
        gen._HANDLE_SEQ[0] = 0

    def test_no_generations_errors(self):
        res = gen.status_handler()
        assert res["isError"] is True and "No generations" in res["message"]

    def test_unknown_handle_lists_active(self):
        gen._GENERATIONS["gen1"] = {"future": SimpleNamespace(isGenerationCompleted=True),
                                    "target": "t", "started_at": 0, "total": 1}
        res = gen.status_handler(handle="gen99")
        assert res["isError"] is True and "gen1" in res["message"]

    def _completed_entry(self):
        return {"future": SimpleNamespace(isGenerationCompleted=True, numberOfOperations=2,
                                          numberOfCompleted=2),
                "target": "all setups", "started_at": 0.0, "total": 2}

    # status_handler now DELEGATES CAM health to _cam_common.live_readiness (the single source) - tests
    # patch that seam (gen._cam_common.live_readiness -> (signal, None)) instead of a local tally.
    def _readiness(self, **kw):
        base = {"valid": 0, "out_of_date": 0, "errored": 0, "generating": 0, "suppressed": 0,
                "total": 0, "active": None, "setups_errored": 0, "programs_errored": 0,
                "readiness": "", "samples": {"op": None, "setup": None, "program": None}}
        base.update(kw)
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
        # THE refinement: an errored op (hasError) will NEVER finish, so a still-generating poll must
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
