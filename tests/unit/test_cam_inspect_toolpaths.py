"""Unit tests for ``cam_inspect_toolpaths.py`` - the toolpath validity verdict.

Covers the two dispatch paths (no scope -> CAM.checkAllToolpaths; a NAME -> CAM.checkToolpath on the
resolved setup/folder/pattern/operation), the shared resolver's refusals reaching this entry point
(a miss lists the available names, a duplicated name is refused and nothing is checked), the payload
composition (verdict + states tally + the per-operation rows, both from the one shared classifier),
the disagreement branches (the verdict and the per-operation reads are independent), the row cap,
and the guards (the shared no-CAM gate, a non-boolean verdict, a raising check).
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, make_cam
from conftest import FakeSetup, FakeCAMFolder, FakeOperation

mod = load_tool("cam_inspect_toolpaths")
cc = load_tool("_cam_common")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _cam(*setups, verdict=True, scoped_verdict=None, raises=None, all_raises=None,
         setup_verdicts=None):
    """A CAM product carrying `setups` plus the two validity-check entry points the tool calls.
    Every target asked about is recorded on `checked` ("all" for the whole-document call), so a test
    can pin WHICH object was checked, not just the returned verdict. `all_raises`/`raises` make an
    entry point raise; `setup_verdicts` gives each setup NAME its own verdict."""
    cam = make_cam(*setups)
    cam.checked = []

    def check_all():
        cam.checked.append("all")
        if all_raises is not None:
            raise all_raises
        return verdict

    def check_toolpath(target):
        cam.checked.append(target)
        if raises is not None:
            raise raises
        if setup_verdicts is not None:
            return setup_verdicts[target.name]
        return verdict if scoped_verdict is None else scoped_verdict

    cam.checkAllToolpaths = check_all
    cam.checkToolpath = check_toolpath
    return cam


@pytest.fixture
def wire(monkeypatch):
    """Wire one CAM product into the tool's shared get_cam seam; the patch undoes itself."""
    def _wire(cam):
        monkeypatch.setattr(mod, "get_cam", lambda: (cam, None))
        return cam
    return _wire


# ── scope dispatch: which API entry point, on which object ──────────────────────────────────────────

class TestScopeDispatch:
    def test_no_scope_checks_the_whole_document(self, wire):
        cam = wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1")])))
        out = _payload(mod.handler())
        assert cam.checked == ["all"]                      # checkAllToolpaths, not a scoped check
        assert out["measured"]["scope"] == "document"
        assert out["checked"] == "checkAllToolpaths"

    def test_named_setup_is_checked_by_that_setup_object(self, wire):
        setup = FakeSetup("Roughing", ops=[FakeOperation("Face1")])
        cam = wire(_cam(setup, FakeSetup("Finishing")))
        out = _payload(mod.handler(scope="roughing"))      # case-insensitive exact
        assert cam.checked == [setup]
        assert out["measured"]["scope"] == "setup 'Roughing'"
        assert out["checked"] == "checkToolpath"

    def test_named_operation_scopes_the_breakdown_to_that_operation(self, wire):
        op = FakeOperation("Drill1", operation_state=1)
        cam = wire(_cam(FakeSetup("S1", ops=[op, FakeOperation("Face1")])))
        out = _payload(mod.handler(scope="Drill1"))
        assert cam.checked == [op]
        assert out["measured"]["scope"] == "operation 'Drill1'"
        assert out["measured"]["states"]["total"] == 1     # the sibling Face1 is out of scope

    def test_folder_scope_checks_the_folder_and_its_nested_operations(self, wire):
        folder = FakeCAMFolder("Drilling", ops=[FakeOperation("D1"), FakeOperation("D2")])
        cam = wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1")], folders=[folder])))
        out = _payload(mod.handler(scope="Drilling"))
        assert cam.checked == [folder]
        assert out["measured"]["scope"] == "folder 'Drilling'"
        assert out["measured"]["states"]["total"] == 2

    def test_pattern_scope_resolves_too(self, wire):
        pattern = FakeCAMFolder("Bolt Pattern", ops=[FakeOperation("P1")])
        cam = wire(_cam(FakeSetup("S1", patterns=[pattern])))
        out = _payload(mod.handler(scope="Bolt Pattern"))
        assert cam.checked == [pattern]
        assert out["measured"]["scope"] == "pattern 'Bolt Pattern'"


# ── the whole-document path: checkAllToolpaths, then the per-setup fallback ─────────────────────────

_NOT_CAM_OBJECTS = RuntimeError("3 : The operations are not CAM objects")


class TestWholeDocumentFallback:
    def test_a_raising_check_all_falls_back_to_the_per_setup_checks(self, wire):
        s1 = FakeSetup("S1", ops=[FakeOperation("Face1")])
        s2 = FakeSetup("S2", ops=[FakeOperation("Face2")])
        cam = wire(_cam(s1, s2, verdict=True, all_raises=_NOT_CAM_OBJECTS))
        out = _payload(mod.handler())
        assert cam.checked == ["all", s1, s2]              # tried the document, then every setup
        assert out["passed"] is True
        assert out["checked"] == "per-setup fallback"
        assert "CAM.checkAllToolpaths raised on this document" in out["note"]

    def test_the_fallback_verdict_is_the_and_of_the_setups(self, wire):
        s1 = FakeSetup("S1", ops=[FakeOperation("Face1")])
        s2 = FakeSetup("S2", ops=[FakeOperation("Bore", operation_state=1)])
        cam = wire(_cam(s1, s2, all_raises=_NOT_CAM_OBJECTS,
                        setup_verdicts={"S1": True, "S2": False}))
        out = _payload(mod.handler())
        assert out["passed"] is False                      # one failing setup fails the document
        assert cam.checked == ["all", s1, s2]              # every setup asked, no short-circuit
        assert out["measured"]["not_valid"] == [{"operation": "Bore", "state": "out_of_date"}]

    def test_both_paths_raising_is_one_clean_error_naming_each(self, wire):
        cam = wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1")]),
                        all_raises=_NOT_CAM_OBJECTS, raises=RuntimeError("2 : bad target")))
        res = mod.handler()
        assert res["isError"] is True
        assert "3 : The operations are not CAM objects" in res["message"]
        assert "the per-setup fallback raised 2 : bad target" in res["message"]
        assert cam.checked == ["all", cam.setups.item(0)]

    def test_a_non_boolean_from_the_fallback_is_refused_not_and_ed(self, wire):
        # AND-ing a non-boolean would launder it into a true/false verdict; the guard names it
        wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1")]), all_raises=_NOT_CAM_OBJECTS,
                  setup_verdicts={"S1": "true"}))
        res = mod.handler()
        assert res["isError"] is True
        assert "not a true/false verdict" in res["message"] and "str" in res["message"]


# ── the scoped path: checkToolpath on the target, then the per-operation fallback ───────────────────

_INPUT_IS_NULL = RuntimeError("3 : input is null")


class TestScopedFallback:
    """A scoped check has the same shape as the document one: when checkToolpath raises on the named
    target, the verdict is the AND of the checks on the operations nested under it (checkToolpath
    takes an Operation too). A target that IS an operation has nothing narrower to ask, so its raise
    is reported as the raise it was."""

    def _wire_raising_target(self, wire, setup, target, op_verdicts=None):
        """A CAM whose checkToolpath raises for `target` only; anything else answers from
        `op_verdicts` (by name), or True."""
        cam = wire(_cam(setup))

        def check(obj):
            cam.checked.append(obj)
            if obj is target:
                raise _INPUT_IS_NULL
            return True if op_verdicts is None else op_verdicts[obj.name]

        cam.checkToolpath = check
        return cam

    def test_a_raising_setup_check_falls_back_to_its_operations(self, wire):
        ops = [FakeOperation("Face1"), FakeOperation("Face2")]
        setup = FakeSetup("Roughing", ops=ops)
        cam = self._wire_raising_target(wire, setup, setup)
        out = _payload(mod.handler(scope="Roughing"))
        assert cam.checked == [setup, ops[0], ops[1]]   # the target, then every nested operation
        assert out["passed"] is True
        assert out["checked"] == "per-operation fallback"
        assert "CAM.checkToolpath raised on the setup 'Roughing'" in out["note"]
        assert "per-setup checks" not in out["note"]    # the document path's sentence, not this one

    def test_the_fallback_verdict_is_the_and_of_the_operations(self, wire):
        ops = [FakeOperation("Face1"), FakeOperation("Bore", operation_state=1)]
        setup = FakeSetup("Roughing", ops=ops)
        cam = self._wire_raising_target(wire, setup, setup, {"Face1": True, "Bore": False})
        out = _payload(mod.handler(scope="Roughing"))
        assert out["passed"] is False                   # one failing operation fails the scope
        assert cam.checked == [setup, ops[0], ops[1]]   # every operation asked, no short-circuit

    def test_a_folder_target_falls_back_to_the_operations_nested_in_it(self, wire):
        ops = [FakeOperation("D1"), FakeOperation("D2")]
        folder = FakeCAMFolder("Drilling", ops=ops)
        setup = FakeSetup("S1", ops=[FakeOperation("Face1")], folders=[folder])
        cam = self._wire_raising_target(wire, setup, folder)
        out = _payload(mod.handler(scope="Drilling"))
        assert cam.checked == [folder, ops[0], ops[1]]  # the sibling Face1 is out of scope
        assert out["checked"] == "per-operation fallback"

    def test_one_nested_operation_is_enough_to_fall_back(self, wire):
        # Boundary: exactly 1 child. The fallback needs a target NARROWER than the raising one, not
        # several of them.
        op = FakeOperation("Face1")
        setup = FakeSetup("Roughing", ops=[op])
        cam = self._wire_raising_target(wire, setup, setup, {"Face1": False})
        out = _payload(mod.handler(scope="Roughing"))
        assert cam.checked == [setup, op]
        assert out["passed"] is False and out["checked"] == "per-operation fallback"

    def test_a_setup_with_no_operations_reports_the_raise(self, wire):
        # Boundary: 0 children - nothing narrower exists to ask, so the raise IS the answer.
        setup = FakeSetup("Empty")
        cam = self._wire_raising_target(wire, setup, setup)
        res = mod.handler(scope="Empty")
        assert res["isError"] is True
        assert "setup 'Empty'" in res["message"] and "3 : input is null" in res["message"]
        assert cam.checked == [setup]                   # nothing else was asked in its place

    def test_an_operation_target_has_nothing_narrower_and_reports_the_raise(self, wire):
        op = FakeOperation("Drill1")
        setup = FakeSetup("S1", ops=[op, FakeOperation("Face1")])
        cam = self._wire_raising_target(wire, setup, op)
        res = mod.handler(scope="Drill1")
        assert res["isError"] is True
        assert "operation 'Drill1'" in res["message"] and "3 : input is null" in res["message"]
        assert cam.checked == [op]                      # a SIBLING is never checked in its place

    def test_both_the_target_and_the_fallback_raising_names_each(self, wire):
        cam = wire(_cam(FakeSetup("Roughing", ops=[FakeOperation("Face1")]),
                        raises=_INPUT_IS_NULL))         # every checkToolpath raises
        res = mod.handler(scope="Roughing")
        assert res["isError"] is True
        assert "setup 'Roughing'" in res["message"]
        assert "the per-operation fallback raised" in res["message"]
        assert res["message"].count("3 : input is null") == 2

    def test_a_non_boolean_from_the_fallback_is_refused_not_and_ed(self, wire):
        setup = FakeSetup("Roughing", ops=[FakeOperation("Face1")])
        self._wire_raising_target(wire, setup, setup, {"Face1": "true"})
        res = mod.handler(scope="Roughing")
        assert res["isError"] is True
        assert "not a true/false verdict" in res["message"] and "str" in res["message"]


# ── the shared resolver's refusals reach this entry point ───────────────────────────────────────────

class TestScopeRefusals:
    def test_unknown_scope_lists_the_available_names_and_checks_nothing(self, wire):
        cam = wire(_cam(FakeSetup("Roughing", ops=[FakeOperation("Face1")])))
        res = mod.handler(scope="Ghost")
        assert res["isError"] is True
        assert "Roughing" in res["message"] and "Face1" in res["message"]
        assert cam.checked == []

    def test_duplicate_name_is_refused_with_both_paths(self, wire):
        cam = wire(_cam(FakeSetup("Setup1", ops=[FakeOperation("Drill1")]),
                        FakeSetup("Setup2", ops=[FakeOperation("Drill1")])))
        res = mod.handler(scope="Drill1")
        assert res["isError"] is True and "ambiguous" in res["message"]
        assert "Setup1 / Drill1" in res["message"] and "Setup2 / Drill1" in res["message"]
        assert cam.checked == []                           # no verdict taken on a guessed target


# ── verdict + breakdown composition ─────────────────────────────────────────────────────────────────

class TestVerdictAndBreakdown:
    def test_true_verdict_carries_the_tally_and_no_rows(self, wire):
        wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1"), FakeOperation("Face2")]),
                  verdict=True))
        out = _payload(mod.handler())
        assert out["relation"] == "toolpaths_valid"
        assert out["passed"] is True
        assert out["measured"]["states"] == {"valid": 2, "out_of_date": 0, "no_toolpath": 0,
                                             "error": 0, "suppressed": 0, "generating": 0,
                                             "total": 2}
        assert out["measured"]["not_valid"] == []
        assert "the validity check passed and every operation reads valid" in out["note"]

    def test_false_verdict_names_every_operation_outside_the_valid_state(self, wire):
        ops = [FakeOperation("Face1"),
               FakeOperation("Bore", operation_state=1),
               FakeOperation("Slot", operation_state=3),
               FakeOperation("Chamfer", operation_state=2, suppressed=True),
               FakeOperation("Drill", has_error=True, error="Tool is not selected.\nsecond line")]
        wire(_cam(FakeSetup("S1", ops=ops), verdict=False))
        out = _payload(mod.handler())
        assert out["passed"] is False
        assert out["measured"]["not_valid"] == [
            {"operation": "Bore", "state": "out_of_date"},
            {"operation": "Slot", "state": "no_toolpath"},
            {"operation": "Chamfer", "state": "suppressed"},
            {"operation": "Drill", "state": "error", "error": "Tool is not selected."},
        ]
        assert out["measured"]["states"] == {"valid": 1, "out_of_date": 1, "no_toolpath": 1,
                                             "error": 1, "suppressed": 1, "generating": 0,
                                             "total": 5}
        assert out["measured"]["not_valid_truncated"] is False
        assert "cam_get(include=['operations'])" in out["note"] and "cam_generate" in out["note"]

    def test_tally_and_rows_agree_on_one_vocabulary(self, wire):
        # the shared invariant: uncapped, every non-valid operation is a row, and each row's state
        # is a key of states - one classifier, so a bucket can never be counted two ways
        ops = [FakeOperation("Face1"),
               FakeOperation("Bore", operation_state=1),
               FakeOperation("Chamfer", operation_state=2, suppressed=True)]
        wire(_cam(FakeSetup("S1", ops=ops), verdict=False))
        states = _payload(mod.handler())["measured"]["states"]
        rows = _payload(mod.handler())["measured"]["not_valid"]
        assert len(rows) == states["total"] - states["valid"] == 2
        assert all(r["state"] in states for r in rows)
        assert sum(v for k, v in states.items() if k != "total") == states["total"]

    def test_a_non_operation_in_the_walk_is_skipped(self, wire, monkeypatch):
        import adsk.cam
        # Operation.cast is the gate: a node that does not cast to an Operation is neither tallied
        # nor given a row
        monkeypatch.setattr(adsk.cam.Operation, "cast",
                            staticmethod(lambda x: None if x.name == "NotAnOp" else x))
        wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1"),
                                       FakeOperation("NotAnOp", operation_state=1)]),
                  verdict=True))
        out = _payload(mod.handler())
        assert out["measured"]["states"]["total"] == 1
        assert out["measured"]["not_valid"] == []

    def test_empty_scope_reports_nothing_to_check(self, wire):
        wire(_cam(FakeSetup("S1"), verdict=True))
        out = _payload(mod.handler())
        assert out["measured"]["states"]["total"] == 0
        assert "no operations to check" in out["note"]


# ── the verdict and the per-operation reads are independent - the note must say so ──────────────────

class TestDisagreement:
    def test_false_verdict_with_every_operation_valid_states_both_facts(self, wire):
        wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1"), FakeOperation("Face2")]),
                  verdict=False))
        out = _payload(mod.handler())
        assert out["passed"] is False
        assert out["measured"]["not_valid"] == []
        assert "the validity check failed while every operation reads valid" in out["note"]
        assert "measured.not_valid is empty" in out["note"]
        assert "validity_basis" in out["note"]
        assert "names them" not in out["note"]      # nothing is named when the list is empty

    def test_true_verdict_with_a_suppressed_operation_does_not_claim_all_valid(self, wire):
        wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1"),
                                       FakeOperation("Chamfer", operation_state=2,
                                                     suppressed=True)]),
                  verdict=True))
        out = _payload(mod.handler())
        assert out["passed"] is True
        assert out["measured"]["not_valid"] == [{"operation": "Chamfer", "state": "suppressed"}]
        assert "every operation reads valid" not in out["note"]
        assert "the validity check passed while 1 operation(s) read outside the valid state" in out["note"]
        assert "validity_basis" in out["note"]

    def test_a_generating_operation_is_one_bucket_in_both_the_tally_and_the_rows(self, wire):
        # isGenerating is a real Operation flag; an op can read operationState=0 WHILE generating,
        # and the one classifier puts it in exactly one bucket
        op = FakeOperation("Face1", operation_state=0)
        op.isGenerating = True
        wire(_cam(FakeSetup("S1", ops=[op]), verdict=False))
        out = _payload(mod.handler())
        assert out["measured"]["states"] == {"valid": 0, "out_of_date": 0, "no_toolpath": 0,
                                             "error": 0, "suppressed": 0, "generating": 1,
                                             "total": 1}
        assert out["measured"]["not_valid"] == [{"operation": "Face1", "state": "generating"}]

    def test_scoped_verdict_is_the_one_reported(self, wire):
        # the document is dirty overall; the named setup's own verdict is what a scoped call returns
        wire(_cam(FakeSetup("Roughing", ops=[FakeOperation("Face1")]),
                  verdict=False, scoped_verdict=True))
        out = _payload(mod.handler(scope="Roughing"))
        assert out["passed"] is True


# ── the row cap ─────────────────────────────────────────────────────────────────────────────────────

class TestRowCap:
    def _stale_document(self, count):
        return FakeSetup("S1", ops=[FakeOperation(f"Op{i}", operation_state=1)
                                    for i in range(count)])

    def test_rows_are_capped_and_flagged_while_the_tally_stays_whole(self, wire):
        wire(_cam(self._stale_document(5), verdict=False))
        out = _payload(mod.handler(max_results=2))
        assert [r["operation"] for r in out["measured"]["not_valid"]] == ["Op0", "Op1"]
        assert out["measured"]["not_valid_truncated"] is True
        assert out["measured"]["states"]["out_of_date"] == 5      # the tally is never capped
        assert "capped at 2 row(s)" in out["note"]

    def test_under_the_cap_nothing_is_flagged(self, wire):
        wire(_cam(self._stale_document(3), verdict=False))
        out = _payload(mod.handler(max_results=25))
        assert len(out["measured"]["not_valid"]) == 3
        assert out["measured"]["not_valid_truncated"] is False
        assert "capped at" not in out["note"]

    def test_max_results_cannot_lift_the_ceiling(self, wire):
        # Every row crosses the wire, so the request is CLAMPED - a caller asking for 10000 rows
        # still gets at most _ROWS_MAX, and the truncation is flagged rather than silently obeyed.
        wire(_cam(self._stale_document(mod._ROWS_MAX + 5), verdict=False))
        out = _payload(mod.handler(max_results=10000))
        assert len(out["measured"]["not_valid"]) == mod._ROWS_MAX
        assert out["measured"]["not_valid_truncated"] is True
        assert out["measured"]["states"]["out_of_date"] == mod._ROWS_MAX + 5   # the tally is whole

    def test_a_non_numeric_max_results_falls_back_to_the_default(self, wire):
        # max_results arrives off the wire; a bare int() on it RAISES instead of answering.
        wire(_cam(self._stale_document(30), verdict=False))
        out = _payload(mod.handler(max_results="lots"))
        assert len(out["measured"]["not_valid"]) == mod._ROWS_CAP

    def test_a_negative_max_results_still_returns_one_row(self, wire):
        wire(_cam(self._stale_document(3), verdict=False))
        out = _payload(mod.handler(max_results=-5))
        assert len(out["measured"]["not_valid"]) == 1


# ── guards ──────────────────────────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_active_document_surfaces_the_shared_cam_gate(self, monkeypatch):
        # the real _cam_common.get_cam decides this - the tool must report its reason, not its own
        monkeypatch.setattr(cc, "app", SimpleNamespace(activeDocument=None))
        res = mod.handler()
        assert res["isError"] is True
        assert res["message"] == "No active document."

    def test_missing_cam_product_reason_is_passed_through(self, monkeypatch):
        monkeypatch.setattr(mod, "get_cam", lambda: (None, "This document has no CAM data."))
        res = mod.handler()
        assert res["isError"] is True
        assert res["message"] == "This document has no CAM data."

    def test_non_boolean_verdict_is_refused_rather_than_coerced(self, wire):
        cam = wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1")])))
        cam.checkAllToolpaths = lambda: "true"
        res = mod.handler()
        assert res["isError"] is True
        assert "not a true/false verdict" in res["message"] and "str" in res["message"]

    def test_a_raising_check_is_reported_with_its_scope(self, wire):
        wire(_cam(FakeSetup("Roughing", ops=[FakeOperation("Face1")]),
                  raises=RuntimeError("3 : invalid argument")))
        res = mod.handler(scope="Roughing")
        assert res["isError"] is True
        assert "setup 'Roughing'" in res["message"] and "3 : invalid argument" in res["message"]


# ── the Manufacture-workspace trust gate ────────────────────────────────────────────────────────────

class TestValidityBasis:
    def test_outside_manufacture_the_verdict_carries_its_caveat(self, wire):
        wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1")])))
        out = _payload(mod.handler())
        assert out["tolerance_used"] == {"criterion": "valid_and_up_to_date",
                                         "validity_basis": "unverified_design_workspace"}
        assert "Manufacture workspace" in out["note"]

    def test_inside_manufacture_the_caveat_drops(self, wire, monkeypatch):
        wire(_cam(FakeSetup("S1", ops=[FakeOperation("Face1")])))
        monkeypatch.setattr(mod, "validity_basis", lambda: "manufacture_verified")
        out = _payload(mod.handler())
        assert out["tolerance_used"]["validity_basis"] == "manufacture_verified"
        assert "Manufacture workspace" not in out["note"]
