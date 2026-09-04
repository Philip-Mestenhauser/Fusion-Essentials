"""Unit tests for ``cam_generate.py`` — launch toolpath generation / read its progress.

``_live_op_tally`` is already pinned in test_tier2_misc.py. This file covers the rest of the real
logic (no live Fusion): target resolution through the shared ``_cam_common.resolve_cam_node``
(setup/folder classification, the duplicate-name refusal, not-found), ``_collect_op_health``
(which operations earn a warning/error/empty row, off the shared state flags), and the two handlers' branching —
generate's skip-valid short-circuit and target-not-found, and status's handle/'latest' resolution,
the unknown-handle guard, the wrong-active-document fallback (Future progress only, no foreign
tallies), the "nothing generating but out-of-date remain" stall warning, and the NO-HANDLE live
path (document + by-name target) that reports an inline/UI generation with no cam_generate handle.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, make_cam, _NamedCollection
from conftest import FakeSetup as SharedSetup, FakeCAMFolder as SharedFolder, FakeOperation as SharedOp

gen = load_tool("cam_generate")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── target resolution (via the shared _cam_common.resolve_cam_node) ─────────────────────────────────

class _FakeCAM:
    def __init__(self, setups, machining_times=None):
        s = list(setups)
        self.setups = SimpleNamespace(count=len(s), item=lambda i: s[i])
        self.generate_calls = []
        # The EMPTY class's second signal: an operation whose toolpath generated empty reads
        # hasToolpath True and only its machining time answers. The answer is the SHARED one
        # (conftest.make_cam), borrowed rather than re-rolled.
        self.getMachiningTime = make_cam(machining_times=machining_times).getMachiningTime

    def generateToolpath(self, tgt):
        self.generate_calls.append(("target", tgt))
        return SimpleNamespace(numberOfOperations=1)

    def generateAllToolpaths(self, skip_valid):
        self.generate_calls.append(("all", skip_valid))
        return SimpleNamespace(numberOfOperations=3)


class _DocHandle:
    """A Document wrapper. The same open document reads as a NEW wrapper on every
    app.activeDocument access - `is` answers False across two reads while `==` answers True
    (measured) - so two of them are compared by EQUALITY, and a fake that models identity only
    would pass a comparison the live objects fail."""

    def __init__(self, ident):
        self._ident = ident

    def __eq__(self, other):
        return isinstance(other, _DocHandle) and other._ident == self._ident

    def __hash__(self):
        return hash(self._ident)


def _setup(name, ops=(), machine=SimpleNamespace(description="Haas VF-2")):
    """A setup as the poll walks it. It carries an assigned machine by default: the scoped verdict
    reads _cam_common.setup_blockers off Setup.machine, so a machine-less fake is a setup blocked by
    no_machine_selected, not a clean one."""
    return SimpleNamespace(name=name, allOperations=_NamedCollection(ops), machine=machine)


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

    def test_scoped_launch_records_the_resolved_target_name(self, monkeypatch):
        # The handle carries the RAW resolved name (not just the display description), because the
        # status read settles completion on that target's OWN operations - see _handle_scope_state.
        gen._GENERATIONS.clear()
        setup = SharedSetup("Roughing", ops=[SharedOp("Face1", operation_state=1)])
        self._install(monkeypatch, [setup])
        out = _payload(gen.generate_handler(target="roughing"))
        entry = gen._GENERATIONS[out["handle"]]
        assert entry["target_name"] == "Roughing" and entry["scope"] == "setup"

    def test_document_launch_records_no_target_name(self, monkeypatch):
        gen._GENERATIONS.clear()
        self._install(monkeypatch, [SharedSetup("S", ops=[SharedOp("Face1", operation_state=1)])])
        out = _payload(gen.generate_handler(target=""))
        entry = gen._GENERATIONS[out["handle"]]
        assert entry["target_name"] == "" and entry["scope"] == "document"

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

# The fake answers the SAME parameter name the tool keys the rail triage on: spelled here instead,
# a rename would leave both sides agreeing on a name no operation carries.
_RAIL_PARAM = gen._cam_common.SWARF_CONTOURS_PARAM


def _op(name, warning=None, error=None, has_toolpath=True, toolpath_valid=True,
        suppressed=False, state=0, rail=False):
    op = SimpleNamespace(
        name=name,
        hasWarning=warning is not None, warning=warning or "",
        hasError=error is not None, error=error or "",
        hasToolpath=has_toolpath, isToolpathValid=toolpath_valid,
        isSuppressed=suppressed, isGenerating=False, operationState=state,
    )
    # A rail-driven strategy is the one that carries the rail-PAIR drive parameter, read from its
    # ONE home; every other operation answers None, the way a collection answers an absent name.
    op.parameters = SimpleNamespace(
        itemByName=lambda nm: object() if (rail and nm == _RAIL_PARAM) else None)
    return op


class TestCollectOpHealth:
    """The collector reads exactly the operations it is HANDED - its caller decides the scope, so
    the lists and the tally published beside them describe one set."""

    def test_warnings_and_errors_separated(self):
        out = gen._collect_op_health([_op("a", warning="Spindle too fast"),
                                      _op("b", error="bad geometry"), _op("c")])
        assert out["warnings"] == [{"name": "a", "warning": "Spindle too fast"}]
        assert out["errors"] == [{"name": "b", "error": "bad geometry"}]

    def test_empty_toolpath_read_from_the_state_flags(self):
        # an op that GENERATED and produced no toolpath: state IsValid, isToolpathValid true,
        # hasToolpath false. Read from the flags, so it lands in 'empty' whatever its warning says.
        out = gen._collect_op_health([_op("face", warning="The toolpath is empty.",
                                          has_toolpath=False)])
        # surfaces in BOTH warnings and the convenience 'empty' list
        assert out["empty"] == ["face"]
        assert out["warnings"][0]["name"] == "face"

    def test_a_generated_op_with_a_toolpath_is_not_empty(self):
        # the discriminator: same warning text, but the op HAS a toolpath - it cut something.
        ops = [_op("face", warning="Toolpath is empty in one region.", has_toolpath=True)]
        assert gen._collect_op_health(ops)["empty"] == []

    def test_a_toolpath_that_generated_empty_is_named_from_its_machining_time(self, monkeypatch):
        # the shape the flags read as a clean valid: hasToolpath TRUE, state IsValid, 0.0 s. The
        # cutting operation beside it carries a warning of its own and stays out of the list.
        cam = make_cam(machining_times={"Swarf1": 0.0, "Cut": 4.193083})
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = gen._collect_op_health([_op("Swarf1", warning="No passes to link."),
                                      _op("Cut", warning="Tool was lifted.")])
        assert out["empty"] == ["Swarf1"]
        assert [r["name"] for r in out["warnings"]] == ["Swarf1", "Cut"]

    def test_the_empty_list_falls_back_to_the_flags_when_no_product_resolves(self, monkeypatch):
        # no CAM product means no time signal, so the list covers the flags shape alone rather
        # than claiming the operation holding a toolpath cut something
        monkeypatch.setattr(gen._cam_common, "get_cam",
                            lambda: (None, "This document has no CAM product yet"))
        out = gen._collect_op_health([_op("Swarf1"), _op("NoPath", has_toolpath=False)])
        assert out["empty"] == ["NoPath"]

    def test_the_empty_rows_are_named_by_the_labels_the_caller_passed(self, monkeypatch):
        # a document-scope list names a shared operation name by its path; the empty row has to
        # take that same label, or two payload lists address one operation two ways
        cam = make_cam(machining_times={"Bore": 0.0})
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = gen._collect_op_health([_op("Bore")], ["Front / Bore"])
        assert out["empty"] == ["Front / Bore"]

    def test_a_suppressed_op_with_no_toolpath_is_not_empty(self):
        # a parked op has no toolpath BY DESIGN (measured state 2) - counting it as empty would
        # report every parked menu item as an operation that cuts nothing.
        ops = [_op("parked", has_toolpath=False, toolpath_valid=False, suppressed=True, state=2)]
        assert gen._collect_op_health(ops)["empty"] == []

    def test_warning_text_stripped(self):
        out = gen._collect_op_health([_op("a", warning="  padded  ")])
        assert out["warnings"][0]["warning"] == "padded"

    def test_a_warned_and_errored_op_is_reported_as_an_error_only(self):
        # the gate is _cam_common.counts_as_warning, the predicate live_states.warnings counts
        # through: a warning on an op that ALREADY blocks the post adds nothing to its error, and a
        # row here that the tally does not count is exactly the disagreement health_scope denies.
        out = gen._collect_op_health([_op("Contour20", warning="Chip load is high",
                                          error="Top height must not be below the bottom height")])
        assert out["warnings"] == []
        assert out["errors"][0]["name"] == "Contour20"

    def test_a_suppressed_warned_op_is_not_a_warning_row(self):
        # a parked op carries no toolpath - measured: setting isSuppressed True discards it - so
        # its warning never reaches a readiness surface.
        out = gen._collect_op_health([_op("Parked drill", warning="Parked and warned",
                                          has_toolpath=False, toolpath_valid=False,
                                          suppressed=True, state=2)])
        assert out["warnings"] == [] and out["empty"] == []

    def test_no_operations_reads_no_health(self):
        # the size-0 end of the scope contract: a scope holding nothing reports nothing, rather
        # than falling back to some wider set.
        assert gen._collect_op_health([]) == {"warnings": [], "errors": [], "empty": [],
                                              "empty_rail": []}

    def test_only_a_rail_driven_empty_toolpath_earns_the_rail_row(self, monkeypatch):
        # the triage names otherSide, the rail order and the flute length - inputs a 2D contour has
        # not got, so the rail row is keyed on the drive parameter, never on being empty.
        cam = make_cam(machining_times={"Swarf1": 0.0, "Contour1": 0.0})
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = gen._collect_op_health([_op("Swarf1", rail=True), _op("Contour1")])
        assert out["empty"] == ["Swarf1", "Contour1"]
        assert out["empty_rail"] == ["Swarf1"]

    def test_a_rail_op_that_cut_something_is_no_rail_row(self, monkeypatch):
        # the row is the intersection of the two: rail-driven AND empty. A rail op with a toolpath
        # has nothing to triage.
        cam = make_cam(machining_times={"Swarf1": 4.19})
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        assert gen._collect_op_health([_op("Swarf1", rail=True)])["empty_rail"] == []


class TestRailTriageRidesTheEmptyToolpathDisclosure:
    """The rail triage is disclosed where the empty toolpath shows up, and only over the operations
    it describes - not on every successful rail selection."""

    def _attach(self, monkeypatch, empty, empty_rail):
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [],
                                                      "empty": empty, "empty_rail": empty_rail})
        payload = {}
        return payload, gen._attach_op_health(payload, [], "document")

    def test_an_empty_rail_toolpath_publishes_the_triage_and_the_note_names_it(self, monkeypatch):
        payload, note = self._attach(monkeypatch, ["Swarf1"], ["Swarf1"])
        assert payload["empty_rail_toolpaths"] == ["Swarf1"]
        assert "rail_triage" in note and "1 rail-driven" in note

    def test_the_triage_orders_the_empty_toolpath_suspects_as_measured(self, monkeypatch):
        # An upper-first pair and a wrong otherSide produce the SAME empty-toolpath signature, so
        # the triage orders the suspects rather than naming one; the flute check comes last because
        # that failure announces itself in the warning channel.
        payload, _note = self._attach(monkeypatch, ["Swarf1"], ["Swarf1"])
        triage = payload["rail_triage"]
        assert "No passes to link." in triage and "Invalid contours." in triage
        assert triage.index("otherSide") < triage.index("rail order") < triage.index("flute")

    def test_a_non_rail_empty_toolpath_carries_no_triage(self, monkeypatch):
        payload, note = self._attach(monkeypatch, ["Contour1"], [])
        assert "rail_triage" not in payload and "rail_triage" not in note
        assert "empty_rail_toolpaths" not in payload


# ── generate_handler: scope selection + skip-valid short-circuit ────────────────────────────────────

class TestGenerateHandler:
    def test_whole_document_calls_generate_all(self, monkeypatch):
        # the setup carries an out-of-date operation: a scope with nothing to build is the
        # skipped payload, not a launch, so a launch test needs something in scope.
        cam = _FakeCAM([_setup("S", [SharedOp("Face1", operation_state=1)])])
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


# ── the launch hands completion to the status read, and claims none of its own ──────────────────


class TestLaunchHandsOffToTheStatusRead:
    """Generation runs in the background at its own pace once launched, so this call can only
    report the LAUNCH. The payload therefore names the read that settles completion and mints the
    handle that read is spent on - and asserts nothing about a toolpath being finished."""

    def test_the_launch_claims_no_completion_and_names_the_poller(self, monkeypatch):
        gen._GENERATIONS.clear()
        cam = _FakeCAM([SharedSetup("S", ops=[SharedOp("Face1", operation_state=1)])])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        out = _payload(gen.generate_handler(target=""))
        assert out["launched"] is True                       # the launch, and only the launch
        assert "completed" not in out and "generated" not in out
        assert "cam_get_status" in out["note"]               # where completion IS confirmed
        assert out["handle"] in gen._GENERATIONS             # the handle that read is spent on


class TestLaunchCountIsTheScopeWalk:
    """operations_to_generate names the operations THIS launch covers, walked off the resolved
    scope. The generation Future's own numberOfOperations counts some other collection (2 over a
    six-operation turning setup, measured), so it is never what the payload publishes."""

    def _install(self, monkeypatch, setups, future_count):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        cam = _FakeCAM(setups)
        cam.generateAllToolpaths = lambda skip_valid: SimpleNamespace(
            numberOfOperations=future_count)
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        return cam

    def _ops(self):
        return [SharedOp("Todo1", operation_state=1), SharedOp("Todo2", operation_state=1),
                SharedOp("Todo3", operation_state=1), SharedOp("Done", operation_state=0),
                SharedOp("Parked", operation_state=2, suppressed=True)]

    def test_the_document_count_is_the_scope_not_the_futures_number(self, monkeypatch):
        # THE BITE: the Future counts 13 while three operations in scope are out of date - its
        # counter is not this scope, so it is not what the payload publishes.
        gen._GENERATIONS.clear()
        self._install(monkeypatch, [SharedSetup("S", ops=self._ops())], 13)
        out = _payload(gen.generate_handler(target="", skip_valid=True))
        assert out["operations_to_generate"] == 3
        gen._GENERATIONS.clear()

    def test_the_walk_runs_before_the_launch_that_moves_the_state_it_reads(self, monkeypatch):
        # generateAllToolpaths marks every operation valid here. Counting after it would read the
        # states the launch just wrote and report 0 over a launch covering three.
        gen._GENERATIONS.clear()
        ops = self._ops()
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)], 13)
        plain = cam.generateAllToolpaths

        def _launch_and_settle(skip_valid):
            for op in ops:
                op._operation_state = 0
            return plain(skip_valid)

        cam.generateAllToolpaths = _launch_and_settle
        out = _payload(gen.generate_handler(target="", skip_valid=True))
        assert out["operations_to_generate"] == 3
        gen._GENERATIONS.clear()

    def test_a_scope_with_nothing_to_build_is_skipped_not_a_launch(self, monkeypatch):
        # launched=true with a count of 0 sends the caller to poll a generation nobody started.
        gen._GENERATIONS.clear()
        ops = [SharedOp("Done", operation_state=0),
               SharedOp("Parked", operation_state=2, suppressed=True)]
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)], 13)
        out = _payload(gen.generate_handler(target="", skip_valid=True))
        assert out["launched"] is False and out["skipped"] is True
        assert "1 already valid, 1 suppressed" in out["reason"]
        assert out["hint"] == "Pass skip_valid=false to force-regenerate the valid one(s)."
        assert "handle" not in out                    # no Future was minted
        assert cam.generate_calls == []               # and nothing was launched
        assert gen._GENERATIONS == {}

    def test_a_scope_holding_no_operations_says_so_and_names_the_create(self, monkeypatch):
        # '0 already valid, 0 suppressed' describes an exclusion that never happened - the scope is
        # simply empty, and the remedy is a create, not a flag.
        gen._GENERATIONS.clear()
        cam = self._install(monkeypatch, [SharedSetup("Empty")], 13)
        out = _payload(gen.generate_handler(target="Empty"))
        assert out["launched"] is False and out["skipped"] is True
        assert out["reason"] == "no operations in scope - there is nothing to generate."
        assert "cam_create_operation" in out["hint"]
        assert "already valid" not in out["reason"] and "suppressed" not in out["reason"]
        assert cam.generate_calls == []

    def test_an_all_suppressed_scope_names_the_suppression_not_skip_valid(self, monkeypatch):
        # skip_valid excluded nothing here, so pointing at it would be a remedy that changes
        # nothing - the exclusion that emptied the scope is what the hint names.
        gen._GENERATIONS.clear()
        ops = [SharedOp("Parked", operation_state=2, suppressed=True)]
        self._install(monkeypatch, [SharedSetup("Roughing", ops=ops)], 13)
        out = _payload(gen.generate_handler(target="Roughing", skip_valid=False))
        assert out["launched"] is False and "0 already valid, 1 suppressed" in out["reason"]
        assert "cam_edit_operation(suppressed=false)" in out["hint"]

    def test_skip_valid_false_counts_the_valid_op_and_never_the_suppressed_one(self, monkeypatch):
        # the boundary of the skip: state 0 joins the count only while skip_valid is off, and the
        # suppressed operation stays out either way - it has no toolpath to build.
        gen._GENERATIONS.clear()
        self._install(monkeypatch, [SharedSetup("S", ops=self._ops())], 13)
        out = _payload(gen.generate_handler(target="", skip_valid=False))
        assert out["operations_to_generate"] == 4
        gen._GENERATIONS.clear()

    def test_a_setup_target_counts_every_unsuppressed_op_whatever_skip_valid_says(self, monkeypatch):
        # generateToolpath takes no skip_valid flag - it regenerates the whole target - so a count
        # narrowed by that flag would under-report what the platform is about to rebuild.
        gen._GENERATIONS.clear()
        ops = [SharedOp("Todo1", operation_state=1), SharedOp("Done", operation_state=0),
               SharedOp("Parked", operation_state=2, suppressed=True)]
        self._install(monkeypatch, [SharedSetup("Roughing", ops=ops)], 13)
        out = _payload(gen.generate_handler(target="Roughing", skip_valid=True))
        assert out["operations_to_generate"] == 2
        gen._GENERATIONS.clear()


# ── status_handler: guards, handle resolution, clamp, stall warning ─────────────────────────────────

class TestStatusHandler:
    def setup_method(self):
        gen._GENERATIONS.clear()
        gen._HANDLE_SEQ[0] = 0

    # Entries carry the launch document's KEY (what register_future stamps) plus its name/urn (what
    # the payload names it by); the status read compares that key to the ACTIVE document's. These
    # tests run in the same-document case unless a test says otherwise.
    @pytest.fixture(autouse=True)
    def _same_active_document(self, monkeypatch):
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")

    def test_unknown_handle_lists_active(self):
        gen._GENERATIONS["gen1"] = {"future": SimpleNamespace(isGenerationCompleted=True),
                                    "target": "t", "started_at": 0, "total": 1}
        res = gen.status_handler(handle="gen99")
        assert res["isError"] is True and "gen1" in res["message"]

    def test_the_worst_composed_completed_note_fits_the_wire_budget(self, monkeypatch):
        # the completed note is assembled at run time from five pieces, so test_prose_budget
        # measures none of the compositions: the completion sentence, the per-op pointer, the
        # repeated-name clause, the rail pointer and the count caveat all ride together.
        readiness = gen._cam_common.ready_verdict(
            "6 of 8 active ops valid", 2,
            {"name": "Bore Deep Holes", "warning": "Tool is too short for this operation."}, None)
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=True, numberOfOperations=8,
                                      numberOfCompleted=8),
            "target": "all setups", "started_at": 0.0, "total": 8,
            "doc_name": "Doc", "doc_urn": "urn:doc", "doc_key": "urn:doc"}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 6, "generating": 0, "total": 8,
                                      "readiness": readiness}, None))
        # two operations of ONE name in different setups - what earns the substitution clause
        monkeypatch.setattr(gen, "_document_ops", lambda: [
            SimpleNamespace(name="Rough clean", path="Roughing / Rough clean", obj=object()),
            SimpleNamespace(name="Rough clean", path="Finishing / Rough clean", obj=object())])
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [],
                                                      "empty": list(labels or []),
                                                      "empty_rail": list(labels or [])})
        out = _payload(gen.status_handler(handle="gen1"))
        note = out["note"]
        # the unbounded verdict rides as its own key rather than inside the bounded note
        assert out["readiness"] == readiness and readiness not in note
        assert "'Setup / op' paths" in note and "rail_triage" in note
        assert "numberOfCompleted at THIS read" in note
        assert len(note) <= 400, len(note)      # test_prose_budget.NOTE_BUDGET_CHARS

    def _completed_entry(self):
        # numberOfCompleted is pass-through data, not a completion signal - live it reads 0 even
        # when isGenerationCompleted is True (cam-generate-future in tests/live/VERIFIED_API_FACTS.md).
        return {"future": SimpleNamespace(isGenerationCompleted=True, numberOfOperations=2,
                                          numberOfCompleted=2),
                "target": "all setups", "started_at": 0.0, "total": 2,
                "doc_name": "Doc", "doc_urn": "urn:doc",
                "doc_key": "urn:doc"}

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

    def _stub_health(self, monkeypatch):
        """Both halves of the health attachment: WHICH operations it reads (for a document-scope
        read, the document walk - no CAM tree is wired in these routing tests) and what it makes of
        them. A test stubbing only the second half reaches the real walk through the first."""
        monkeypatch.setattr(gen, "_document_ops", list)
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})

    def test_latest_resolves_to_last_handle(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._GENERATIONS["gen2"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 2
        monkeypatch.setattr(gen._cam_common, "live_readiness", self._readiness(readiness="ready to post."))
        self._stub_health(monkeypatch)
        out = _payload(gen.status_handler(handle="latest"))
        assert out["handle"] == "gen2" and out["completed"] is True

    def test_stall_warning_when_nothing_generating_but_ood_remains(self, monkeypatch):
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=False, numberOfOperations=2,
                                      numberOfCompleted=0),
            "target": "all setups", "started_at": 0.0, "total": 2,
            "doc_name": "Doc", "doc_urn": "urn:doc",
                "doc_key": "urn:doc"}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(out_of_date=2, generating=0, total=2,
                                            readiness="0 of 2 active ops valid - run cam_generate to finish the rest."))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False
        assert "WARNING" in out["note"]

    # ── operations_completed is DISCLOSED, not smoothed (CAM-13b) ───────────────────────────────
    #
    # The Future's numberOfCompleted has been observed to FALL between two reads of one generation
    # (24 -> 23 -> 25) and MEASURED reading 0 after a completed single-op generation. Nothing here
    # publishes a running maximum in its place - a high-water mark would keep claiming progress the
    # counter has stopped standing behind - so the figure ships as the instantaneous one it is, and
    # every payload that carries the number carries the sentence saying so.

    def test_a_completed_poll_names_the_count_as_instantaneous(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(readiness="ready to post."))
        self._stub_health(monkeypatch)
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["operations_completed"] == 2
        assert "numberOfCompleted at THIS read" in out["note"]
        assert "can fall between reads" in out["note"]
        assert "read 0 once complete" in out["note"]

    def test_an_incomplete_poll_carries_the_caveat_on_a_ZERO_count(self, monkeypatch):
        # 0 is the reading most likely to be misread as "nothing has happened yet", so the caveat
        # has to ride the incomplete note too - not only the completion one.
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=False, numberOfOperations=2,
                                      numberOfCompleted=0),
            "target": "all setups", "started_at": 0.0, "total": 2,
            "doc_name": "Doc", "doc_urn": "urn:doc", "doc_key": "urn:doc"}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(generating=2, total=2, readiness=""))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False and out["operations_completed"] == 0
        assert "numberOfCompleted at THIS read" in out["note"]

    def test_a_foreign_document_poll_carries_the_caveat_too(self, monkeypatch):
        # the Future-alone path publishes operations_completed as well, so the sentence about what
        # that number is has to travel with it there.
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen, "document_key", lambda: "urn:another")
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["operations_completed"] == 2
        assert "NOT the active document" in out["note"]
        assert "numberOfCompleted at THIS read" in out["note"]

    def test_a_future_whose_count_does_not_read_carries_no_caveat(self, monkeypatch):
        # numberOfCompleted raises "Generation not started" on the launch tick: the payload's
        # operations_completed is null, and a caveat about a figure that is not there teaches
        # nothing.
        class _NoCount:
            isGenerationCompleted = True
            numberOfOperations = 2

            @property
            def numberOfCompleted(self):
                raise RuntimeError("Generation not started")

        gen._GENERATIONS["gen1"] = {
            "future": _NoCount(), "target": "all setups", "started_at": 0.0, "total": 2,
            "doc_name": "Doc", "doc_urn": "urn:doc", "doc_key": "urn:doc"}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(readiness="ready to post."))
        self._stub_health(monkeypatch)
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["operations_completed"] is None
        assert "instantaneous figure" not in out["note"]

    def test_the_caveat_is_one_bounded_sentence(self):
        # it rides EVERY poll that publishes the number, so it is paid on every call while being
        # only one clause of a note the wire budgets at 400 characters as a whole.
        caveat = gen._COUNT_IS_INSTANTANEOUS
        assert caveat.count(".") == 1, caveat
        assert len(caveat) <= 150, len(caveat)

    def test_errored_op_surfaced_while_still_generating(self, monkeypatch):
        # An errored op (hasError) will NEVER finish, so a still-generating poll must
        # flag it NOW (the BLOCKER readiness + one sample + a pointer to cam_get), not wait for a
        # completion that can't come. The verdict comes from _cam_common.live_readiness (one source).
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=False, numberOfOperations=3,
                                      numberOfCompleted=0),
            "target": "all setups", "started_at": 0.0, "total": 3,
            "doc_name": "Doc", "doc_urn": "urn:doc",
                "doc_key": "urn:doc"}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(errored=1, generating=2, total=3,
                                            readiness="BLOCKER: 1 operation(s) have errors - the job will not post until fixed.",
                                            samples={"op": {"name": "Rough to Model Top",
                                                            "error": "Top height must not be below the bottom height"},
                                                     "setup": None, "program": None}))
        out = _payload(gen.status_handler(handle="gen1"))
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
            "target": "all setups", "started_at": 0.0, "total": 2,
            "doc_name": "Doc", "doc_urn": "urn:doc",
                "doc_key": "urn:doc"}
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=1, out_of_date=1, generating=1, total=2, setups_errored=1,
                                            readiness="BLOCKER: 1 setup(s) have errors - the job will not post until fixed.",
                                            samples={"op": None, "program": None,
                                                     "setup": {"name": "Op1", "error": "WCS orientation is invalid"}}))
        out = _payload(gen.status_handler(handle="gen1"))
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
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False
        assert out["live_states"]["generating"] == 3
        assert "gen1" in gen._GENERATIONS          # not popped while still settling

    def test_completed_when_future_done_and_live_settled(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=2, generating=0, total=2, readiness="ready to post."))
        self._stub_health(monkeypatch)
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True

    def test_status_handler_accepts_exactly_its_wire_schema(self):
        # The schema is strict, and the kernel rejects an unknown argument BEFORE dispatch unless the
        # tool is listed in _SCHEMA_OMITTED_ARGS - so a handler kwarg with no schema property of its
        # own is unreachable over the wire and can only mislead a reader of the signature.
        import inspect
        props = set(gen.status_tool.to_dict()["inputSchema"]["properties"])
        params = set(inspect.signature(gen.status_handler).parameters)
        assert params == props, f"signature {sorted(params)} vs schema {sorted(props)}"

    # ── completion settles on the HANDLE'S OWN scope, not the document-wide generating count ────
    # A handle launched against ONE setup/folder/operation must not be starved by a SECOND
    # generation running beside it: the other job's ops keep the document tally above zero forever.

    def _scoped_entry(self, name, future_done=True):
        return {"future": SimpleNamespace(isGenerationCompleted=future_done, numberOfOperations=2,
                                          numberOfCompleted=2),
                "target": f"setup '{name}'", "scope": "setup", "target_name": name,
                "started_at": 0.0, "total": 2, "doc_name": "Doc", "doc_urn": "urn:doc",
                "doc_key": "urn:doc"}

    def _install_cam(self, monkeypatch, *setups):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        cam = _FakeCAM(list(setups))
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        return cam

    def test_scoped_handle_completes_while_another_job_generates(self, monkeypatch):
        # THE STARVATION BITE: this handle's own setup has settled (nothing generating in it), but a
        # SECOND generation is running in another setup, so the DOCUMENT tally still reads
        # generating=2. Completion must key on this handle's own operations, not that count.
        gen._GENERATIONS["gen1"] = self._scoped_entry("Roughing")
        gen._HANDLE_SEQ[0] = 1
        self._install_cam(monkeypatch,
                          _setup("Roughing", [_live_op("R1", state=0), _live_op("R2", state=0)]),
                          _setup("Finishing", [_live_op("F1", state=1, generating=True),
                                               _live_op("F2", state=1, generating=True)]))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=2, out_of_date=2, generating=2, total=4))
        self._stub_health(monkeypatch)
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True
        assert out["live_states"]["generating"] == 0           # the handle's OWN scope, not 2
        assert "Roughing" in out["completion_basis"]
        assert "gen1" not in gen._GENERATIONS                  # done -> popped

    def test_scoped_handle_waits_for_its_own_ops_even_when_the_document_looks_idle(self, monkeypatch):
        # The mirror of the bite: the document tally reads generating=0 while THIS handle's own
        # setup is still computing - the scoped read is what decides, so completed stays False.
        gen._GENERATIONS["gen1"] = self._scoped_entry("Roughing")
        gen._HANDLE_SEQ[0] = 1
        self._install_cam(monkeypatch,
                          _setup("Roughing", [_live_op("R1", state=1, generating=True)]))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=9, generating=0, total=9, readiness="ready to post."))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False
        assert out["live_states"]["generating"] == 1
        assert "gen1" in gen._GENERATIONS                      # still running - entry kept

    def test_unreadable_tally_reports_the_future_alone_with_the_reason(self, monkeypatch):
        # live_readiness returns its ERROR form: no tally was read at all. An empty tally reads as
        # "nothing is generating", so the verdict rests on the Future alone - the basis and note must
        # say that and name the reason, and no live_states may be published under it.
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: (None, "No CAM product in the active document."))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True                        # the Future is done
        assert "live_states" not in out                        # no tally was read - publish none
        assert "Future alone" in out["completion_basis"]
        assert "No CAM product" in out["completion_basis"] and "No CAM product" in out["note"]
        assert "gen1" not in gen._GENERATIONS                  # done -> popped

    def test_unreadable_tally_while_still_generating_keeps_the_handle(self, monkeypatch):
        entry = self._completed_entry()
        entry["future"] = SimpleNamespace(isGenerationCompleted=False, numberOfOperations=2,
                                          numberOfCompleted=0)
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness", lambda: (None, "CAM unavailable."))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False
        assert "Future alone" in out["completion_basis"] and "CAM unavailable." in out["note"]
        assert "gen1" in gen._GENERATIONS

    def test_document_scope_handle_keeps_the_document_tally(self, monkeypatch):
        # A whole-document launch IS the document, so its basis stays the document readiness.
        gen._GENERATIONS["gen1"] = self._completed_entry()     # no scope/target_name = document
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(out_of_date=3, generating=3, total=3))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completion_basis"] == "document"
        assert out["completed"] is False and out["live_states"]["generating"] == 3

    def test_vanished_scope_target_falls_back_to_the_document_and_says_so(self, monkeypatch):
        # The launch target was renamed/deleted mid-generation: the scoped walk cannot resolve it, so
        # the read falls back to the document tally - and the basis NAMES the wider fallback.
        gen._GENERATIONS["gen1"] = self._scoped_entry("Roughing")
        gen._HANDLE_SEQ[0] = 1
        self._install_cam(monkeypatch, _setup("RenamedSetup", [_live_op("R1", state=0)]))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(out_of_date=1, generating=1, total=1))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False                        # the document tally governs the fallback
        assert "Roughing" in out["completion_basis"]
        assert "could not be re-resolved" in out["completion_basis"]

    def test_wrong_active_document_reports_future_progress_only(self, monkeypatch):
        # The per-op tallies read the ACTIVE document. When the generating document is NOT active,
        # a foreign tally must neither be attached nor gate completion - the Future alone decides,
        # and the note names the generating document.
        entry = self._completed_entry()
        entry["doc_urn"] = entry["doc_key"] = "urn:other"
        entry["doc_name"] = "OtherDoc"
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        # live_readiness (of the WRONG doc) claims ops still generating - it must be ignored.
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(generating=3, out_of_date=3, total=3))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True                    # the Future is done; wrong-doc tally ignored
        assert "live_states" not in out
        assert out["generating_document"]["name"] == "OtherDoc"
        assert "NOT the active document" in out["note"]
        assert "gen1" not in gen._GENERATIONS              # done -> popped

    def test_wrong_active_document_still_generating(self, monkeypatch):
        entry = self._completed_entry()
        entry["future"] = SimpleNamespace(isGenerationCompleted=False, numberOfOperations=5,
                                          numberOfCompleted=2)
        entry["doc_urn"] = entry["doc_key"] = "urn:other"
        entry["doc_name"] = "OtherDoc"
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False
        assert out["operations_completed"] == 2
        assert "live_states" not in out
        assert "gen1" in gen._GENERATIONS                  # still running - entry kept

    # ── which document a status read ANSWERS ABOUT ──────────────────────────────────────────────
    # A BARE read (no handle, no target) is a question about what is open NOW. Answering it from the
    # launch registry reported a CLOSED document's job as completed:true, which a caller reading
    # 'completed' alone takes as a verdict on the active document.

    def _live_document(self, monkeypatch, **states):
        """Wire the live ACTIVE-document path (the one a bare read must reach)."""
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen._cam_common, "live_readiness", self._readiness(**states))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})

    def test_a_bare_read_answers_about_the_active_document_not_a_registered_handle(self, monkeypatch):
        # THE BITE: a stale handle of another (here, no-longer-active) document sits in the registry
        # and its Future says done. The bare read must report the ACTIVE document's live state -
        # 2 of 5 valid, still generating - not the handle's completed:true.
        entry = self._completed_entry()
        entry["doc_urn"], entry["doc_key"], entry["doc_name"] = "urn:closed", "urn:closed", "ClosedDoc"
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        self._live_document(monkeypatch, valid=2, out_of_date=3, generating=1, total=5,
                            readiness="2 of 5 active ops valid - run cam_generate to finish the rest.")
        out = _payload(gen.status_handler())
        assert out["handle"] is None                 # the live path, not the handle path
        assert out["target"] == "document"
        assert out["completed"] is False             # the ACTIVE document's state decided this
        assert "generating_document" not in out      # nothing about the registry's document
        assert "gen1" in gen._GENERATIONS            # a bare read consumes no handle

    def test_a_bare_read_reads_live_even_when_the_handle_is_the_active_documents(self, monkeypatch):
        # The routing is by ARGUMENT, not by luck of the document matching: 'latest' is the way to
        # ask about the most recent launch, so a bare read never picks one up.
        gen._GENERATIONS["gen1"] = self._completed_entry()      # SAME document as active
        gen._HANDLE_SEQ[0] = 1
        self._live_document(monkeypatch, valid=1, generating=1, total=2,
                            readiness="1 of 2 active ops valid - run cam_generate to finish the rest.")
        out = _payload(gen.status_handler())
        assert out["handle"] is None and out["completed"] is False

    def test_latest_is_refused_when_its_launch_document_is_not_active(self, monkeypatch):
        # 'latest' is a POSITIONAL pick - it names no document - so it may not answer from a foreign
        # one. The refusal names all three ways forward in this tool's own vocabulary.
        entry = self._completed_entry()
        entry["doc_urn"], entry["doc_key"], entry["doc_name"] = "urn:other", "urn:other", "OtherDoc"
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        res = gen.status_handler(handle="latest")
        assert res["isError"] is True
        msg = res["message"]
        assert "OtherDoc" in msg and "not the active document" in msg
        assert "handle='gen1'" in msg and "doc_activate" in msg and "omit 'handle'" in msg

    def test_latest_with_an_empty_registry_is_refused_not_silently_the_active_document(self):
        # the fourth cell of the routing: the caller asked about a LAUNCH. Falling through to the
        # active document would answer a different question with no word that no launch exists -
        # so it refuses the way an unknown explicit handle does, and names the same ways out.
        res = gen.status_handler(handle="latest")     # registry cleared by setup_method
        assert res["isError"] is True
        msg = res["message"]
        assert "'latest' resolves to handle 'gen0'" in msg and "not registered" in msg
        assert "Active handles: (none)" in msg
        assert "Omit 'handle'" in msg and "'target'" in msg

    def test_latest_pointing_at_a_completed_handle_names_the_ones_that_remain(self):
        # a generation is dropped from the registry on completion, so 'latest' can address a handle
        # that is gone while OLDER ones survive - the refusal lists what is actually there.
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 2                        # gen2 launched, completed, and was popped
        res = gen.status_handler(handle="latest")
        assert res["isError"] is True
        assert "'gen2'" in res["message"] and "Active handles: gen1" in res["message"]

    def test_latest_still_answers_when_its_launch_document_is_active(self, monkeypatch):
        # the other side of the boundary: same document, so the positional pick names the right job.
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=2, generating=0, total=2,
                                            readiness="2 of 2 active ops valid - ready to post."))
        self._stub_health(monkeypatch)
        out = _payload(gen.status_handler(handle="latest"))
        assert out["handle"] == "gen1" and out["completed"] is True

    def test_an_explicit_handle_still_reports_its_own_foreign_documents_progress(self, monkeypatch):
        # An explicit handle NAMES one job, so it is answered (and disclosed) rather than refused -
        # the active document can change under a running generation with no call from the caller.
        entry = self._completed_entry()
        entry["future"] = SimpleNamespace(isGenerationCompleted=False, numberOfOperations=5,
                                          numberOfCompleted=2)
        entry["doc_urn"], entry["doc_key"], entry["doc_name"] = "urn:other", "urn:other", "OtherDoc"
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["generating_document"]["name"] == "OtherDoc"
        assert out["operations_completed"] == 2

    # ── what `completed` claims ─────────────────────────────────────────────────────────────────
    # Measured beside a readiness of "0 of 34 active ops valid": completed:true does not mean the
    # operations succeeded, only that nothing is generating. Every path that publishes it true says so.

    def test_the_handle_path_words_what_completed_means(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=0, out_of_date=2, generating=0, total=2,
                                            readiness="0 of 2 active ops valid - run cam_generate to finish the rest."))
        self._stub_health(monkeypatch)
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True                      # nothing is generating
        assert "not a success verdict" in out["note"]        # ...which is NOT the same as succeeded
        # the verdict itself is a key: it embeds an op name and warning text, so a note that
        # inlined it could not be bounded, and the note points at the key instead
        assert "0 of 2 active ops valid" in out["readiness"] and "'readiness'" in out["note"]

    def test_the_foreign_document_path_words_what_completed_means(self, monkeypatch):
        entry = self._completed_entry()
        entry["doc_urn"], entry["doc_key"], entry["doc_name"] = "urn:other", "urn:other", "OtherDoc"
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True and "not a success verdict" in out["note"]

    def test_the_unreadable_tally_path_words_what_completed_means(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._completed_entry()
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness", lambda: (None, "No CAM product."))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True and "not a success verdict" in out["note"]


class TestSameDocumentIdentity:
    """A never-saved document carries no lineage URN, and its NAME is not a substitute: measured
    live, two open never-saved documents both answer 'Untitled', so a name match is not evidence
    that the launch document and the active one are one document.

    What settles it is the key register_future stamped (_write_guard.document_key): the lineage urn
    for a saved document, and for a never-saved one a token minted per document INSTANCE, which is
    why a launch from a scratch document is comparable at all rather than falling back to the
    Future alone. _same_document still hands back None - not False - where NO identity was readable
    on one side or the other, since 'not confirmable' and 'a different document' are different
    facts with different remedies.
    """

    def _entry(self, doc_name, doc_key, doc_urn=None):
        return {"future": SimpleNamespace(isGenerationCompleted=True, numberOfOperations=1,
                                          numberOfCompleted=1),
                "target": "all setups", "started_at": 0.0, "total": 1,
                "doc_name": doc_name, "doc_urn": doc_urn, "doc_key": doc_key}

    def _active(self, monkeypatch, doc):
        """What app.activeDocument hands back for this call - the seam register_future stamped the
        launch document from."""
        monkeypatch.setattr(gen._cam_common, "app", SimpleNamespace(activeDocument=doc))

    # ── _same_document: the three answers ────────────────────────────────────────────────────────

    def test_two_never_saved_documents_sharing_a_name_are_not_called_the_same(self, monkeypatch):
        # THE BITE. Both documents are unsaved and both are named 'Untitled' - exactly the live
        # repro. A name comparison answers True here and attaches the wrong document's tallies;
        # the per-instance keys differ, so this is a definite False.
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:2")
        assert gen._same_document(self._entry("Untitled", "unsaved:1")) is False

    def test_a_never_saved_launch_document_is_identified_when_it_is_still_active(self, monkeypatch):
        # The other half, and what the key BUYS: a launch from a never-saved document is confirmed
        # as the active one, so the status read attaches that document's per-op tallies instead of
        # falling back to the Future alone. A urn-only comparison answers None here.
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        assert gen._same_document(self._entry("Untitled", "unsaved:1")) is True

    def test_a_launch_that_recorded_no_identity_answers_none_rather_than_false(self, monkeypatch):
        # None is the honest answer, and it has to be DISTINCT from False: False means 'a different
        # document', which the callers word as "is NOT the active document" - a claim nothing here
        # supports, and one that is wrong whenever the launch document IS the active one.
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        assert gen._same_document(self._entry("Untitled", None)) is None

    def test_no_readable_active_document_answers_none_rather_than_false(self, monkeypatch):
        # The same fact on the other side: the launch is identified but nothing reads now, so the
        # two cannot be compared - not evidence that a different document is in front of the caller.
        monkeypatch.setattr(gen, "_active_identity", lambda: (None, None))
        monkeypatch.setattr(gen, "document_key", lambda: None)
        assert gen._same_document(self._entry("Doc", "urn:doc", doc_urn="urn:doc")) is None

    def test_a_matching_key_is_the_same_document(self, monkeypatch):
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")
        assert gen._same_document(self._entry("Doc", "urn:doc", doc_urn="urn:doc")) is True

    def test_a_differing_key_is_a_definite_false(self, monkeypatch):
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")
        assert gen._same_document(self._entry("OtherDoc", "urn:other", doc_urn="urn:other")) is False

    def test_an_unsaved_launch_against_a_saved_active_document_is_a_definite_false(self, monkeypatch):
        # Not None: both sides carry a key and the keys differ. A name collision is real here too -
        # a saved document may be named 'Untitled' - so the name cannot settle this pair either way.
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", "urn:saved"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:saved")
        assert gen._same_document(self._entry("Untitled", "unsaved:1")) is False

    def test_a_saved_launch_against_an_unsaved_active_document_is_not_the_same(self, monkeypatch):
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        assert gen._same_document(self._entry("Doc", "urn:doc", doc_urn="urn:doc")) is False

    # ── the key is DERIVED, so a key that stopped matching is not yet a different document ───────

    def test_a_launch_document_saved_mid_generation_is_still_the_active_document(self, monkeypatch):
        # THE BITE. document_key prefers a readable data-file id, so the FIRST SAVE of a scratch
        # document replaces the key the launch stamped ('unsaved:1' -> the new urn) while the same
        # document stays open and active. Compared on the key alone this is a definite False, and
        # the status read then says "is NOT the active document" about the document in front of the
        # caller and withholds its own per-op tallies.
        launched, active = _DocHandle("doc-a"), _DocHandle("doc-a")
        assert launched is not active            # a new wrapper per read, as the live API hands back
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Bracket", "urn:new"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:new")
        self._active(monkeypatch, active)
        entry = self._entry("Untitled", "unsaved:1")
        entry["doc"] = launched
        assert gen._same_document(entry) is True

    def test_a_different_active_document_stays_a_definite_false(self, monkeypatch):
        # The fallback may only turn a stale key into a match: two documents compare unequal, and
        # answering True there would attach another document's tallies to this handle.
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Other", "urn:other"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:other")
        self._active(monkeypatch, _DocHandle("doc-b"))
        entry = self._entry("Untitled", "unsaved:1")
        entry["doc"] = _DocHandle("doc-a")
        assert gen._same_document(entry) is False

    def test_a_launch_handle_that_will_not_compare_is_a_definite_false(self, monkeypatch):
        # A leftover wrapper whose comparison RAISES proves nothing about the active document - it
        # is not a match, and it is not an exception out of a status poll either.
        class _Unreadable:
            def __eq__(self, other):
                raise RuntimeError("An API Object refers to a deleted Object")
            __hash__ = None

        monkeypatch.setattr(gen, "_active_identity", lambda: ("Bracket", "urn:new"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:new")
        self._active(monkeypatch, _DocHandle("doc-a"))
        entry = self._entry("Untitled", "unsaved:1")
        entry["doc"] = _Unreadable()
        assert gen._same_document(entry) is False

    def test_no_readable_active_document_still_answers_none_with_a_handle_kept(self, monkeypatch):
        # The tri-state survives the fallback: nothing reads now, so the two cannot be COMPARED -
        # the handle does not turn that into a claim that a different document is open.
        monkeypatch.setattr(gen, "_active_identity", lambda: (None, None))
        monkeypatch.setattr(gen, "document_key", lambda: None)
        self._active(monkeypatch, _DocHandle("doc-a"))
        entry = self._entry("Untitled", "unsaved:1")
        entry["doc"] = _DocHandle("doc-a")
        assert gen._same_document(entry) is None

    def test_an_entry_with_no_launch_handle_compares_on_its_key_alone(self, monkeypatch):
        # Every launch registered before this session's add-in reload carries no handle; the entry
        # falls back to the key comparison rather than reading the active document as a match.
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Bracket", "urn:new"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:new")
        self._active(monkeypatch, _DocHandle("doc-a"))
        assert gen._same_document(self._entry("Untitled", "unsaved:1")) is False

    # ── consumer: the handle path attaches the tallies once the document is confirmed ────────────

    def test_the_status_read_attaches_the_tallies_of_a_saved_launch_document(self, monkeypatch):
        # The consumer's own branch on the new match: _status_future attaches the per-op tallies
        # only over a launch document it can CONFIRM is active, so a saved-mid-generation document
        # gets its own health back instead of the Future-alone fallback.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Bracket", "urn:new"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:new")
        self._active(monkeypatch, _DocHandle("doc-a"))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 1, "generating": 0, "total": 1,
                                      "readiness": "ready to post."}, None))
        monkeypatch.setattr(gen, "_document_ops", list)
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        entry = self._entry("Untitled", "unsaved:1")
        entry["doc"] = _DocHandle("doc-a")
        gen._GENERATIONS["gen1"] = entry
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["live_states"]["valid"] == 1       # its OWN document's tally, attached
        assert out["completion_basis"] == "document"  # not "its document is not active"
        assert "is NOT the active document" not in out["note"]

    # ── consumer 1: the 'latest' routing gate ────────────────────────────────────────────────────
    # _same_document's None is a new state for this caller's `if`, so it gets its own test here -
    # the helper's own tests do not cover the branch this consumer takes on it.

    def test_latest_is_refused_when_the_launch_document_cannot_be_identified(self, monkeypatch):
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        gen._GENERATIONS["gen1"] = self._entry("Untitled", None)
        gen._HANDLE_SEQ[0] = 1
        res = gen.status_handler(handle="latest")
        assert res["isError"] is True
        msg = res["message"]
        assert "no document identity could be read" in msg
        # the refusal must NOT claim the document is inactive - it does not know that
        assert "is not the active document" not in msg
        assert "handle='gen1'" in msg

    def test_the_unidentifiable_refusal_is_worded_apart_from_the_foreign_document_one(self, monkeypatch):
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")
        gen._GENERATIONS["gen1"] = self._entry("OtherDoc", "urn:other", doc_urn="urn:other")
        gen._HANDLE_SEQ[0] = 1
        msg = gen.status_handler(handle="latest")["message"]
        assert "is not the active document" in msg and "OtherDoc" in msg
        assert "no document identity" not in msg     # the wrong diagnosis for THIS refusal

    def test_latest_answers_over_a_never_saved_launch_document_that_is_still_active(self, monkeypatch):
        # What the key buys the 'latest' gate: a scratch-document launch is CONFIRMED as the active
        # one, so 'latest' answers over it instead of refusing for want of a readable identity.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 1, "generating": 0, "total": 1,
                                      "readiness": "ready to post."}, None))
        monkeypatch.setattr(gen, "_document_ops", list)
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        gen._GENERATIONS["gen1"] = self._entry("Untitled", "unsaved:1")
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="latest"))
        assert out["handle"] == "gen1" and out["completed"] is True
        assert out["live_states"]["valid"] == 1       # its OWN document's tally, attached

    # ── consumer 2: the explicit-handle tally gate ───────────────────────────────────────────────

    def test_a_never_saved_launch_document_gets_its_own_per_op_tallies(self, monkeypatch):
        # What the key buys this consumer: a scratch document carries no urn, so an identity read on
        # the urn alone cannot confirm the launch and the tally is skipped entirely - no readiness
        # line at all. The per-instance key confirms it, so that document's own tally lands.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        gen._GENERATIONS["gen1"] = self._entry("Untitled", "unsaved:1")
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 0, "out_of_date": 1, "generating": 0, "total": 1,
                                      "readiness": "0 of 1 active ops valid - run cam_generate to "
                                                   "finish the rest."}, None))
        monkeypatch.setattr(gen, "_document_ops", list)
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["live_states"]["out_of_date"] == 1
        assert "0 of 1 active ops valid" in out["readiness"]

    def test_a_handle_with_no_recorded_identity_attaches_no_tallies(self, monkeypatch):
        # THE BITE for this consumer: no tally may be attached over a document the call cannot
        # confirm - a wrong-document tally under this handle reads as this generation's own state.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        gen._GENERATIONS["gen1"] = self._entry("Untitled", None)
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 0, "out_of_date": 1, "generating": 0, "total": 1,
                                      "readiness": "0 of 1 active ops valid - run cam_generate to "
                                                   "finish the rest."}, None))
        out = _payload(gen.status_handler(handle="gen1"))
        assert "live_states" not in out                  # the unconfirmed tally never lands
        assert "0 of 1 active ops valid" not in out["note"]
        assert out["completed"] is True                  # the Future alone settles it
        assert "document identity could be read" in out["note"]
        assert "no document identity" in out["completion_basis"]

    def test_an_unreadable_document_is_not_NAMED_on_the_wire(self, monkeypatch):
        # THE ENTRY SHAPE THAT ACTUALLY REACHES THIS BRANCH: a launch made while no document read
        # records (None, None) from _active_identity and no key, so every field is None. An
        # interpolated name then puts the literal 'None' on the wire as the document this
        # generation belongs to - a name nothing ever read. Every other test here launches from
        # 'Untitled', where a fabricated name is indistinguishable from a real one.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: (None, None))
        monkeypatch.setattr(gen, "document_key", lambda: None)
        gen._GENERATIONS["gen1"] = {"future": SimpleNamespace(isGenerationCompleted=True,
                                                             numberOfOperations=1,
                                                             numberOfCompleted=1),
                                    "target": "all setups", "started_at": 0.0, "total": 1,
                                    "doc_name": None, "doc_urn": None, "doc_key": None}
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="gen1"))
        assert "'None'" not in out["note"] and "None" not in out["note"]
        assert "document identity could be read" in out["note"]   # the fact still reaches the wire
        # the same entry through the 'latest' gate, the other consumer of this branch
        msg = gen.status_handler(handle="latest")["message"]
        assert "'None'" not in msg

    def test_the_unidentifiable_note_does_not_claim_the_document_is_inactive(self, monkeypatch):
        # The document may well BE the active one - the call simply cannot tell. Saying it is not
        # active states a fact nothing read.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Untitled", None))
        monkeypatch.setattr(gen, "document_key", lambda: "unsaved:1")
        gen._GENERATIONS["gen1"] = self._entry("Untitled", None)
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="gen1"))
        assert "NOT the active document" not in out["note"]
        assert "doc_activate" not in out["note"]         # the remedy for a DIFFERENT document

    def test_a_foreign_document_handle_still_says_it_is_not_active(self, monkeypatch):
        # the other side of the boundary: an identified, different document keeps its own wording
        # and its own remedy, so the two states stay distinguishable on the wire.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")
        gen._GENERATIONS["gen1"] = self._entry("OtherDoc", "urn:other", doc_urn="urn:other")
        gen._HANDLE_SEQ[0] = 1
        out = _payload(gen.status_handler(handle="gen1"))
        assert "NOT the active document" in out["note"] and "doc_activate" in out["note"]
        assert "document identity could be read" not in out["note"]
        assert "live_states" not in out


# ── status_handler live-poll path: NO cam_generate handle (inline / UI generation) ──────────────────

def _live_op(name, state=0, generating=False, error=False):
    return SimpleNamespace(name=name, operationState=state, isGenerating=generating,
                           hasError=error, error="broken" if error else "")


def _warn_op(name, state=0, warning="Contour Selection: contours are missing selections.",
             error=False, suppressed=False):
    """One op as the SCOPED tally reads it. The warning default is the measured live shape of a
    geometry-less 2D Contour: hasWarning True while operationState still reads 0."""
    return SimpleNamespace(name=name, operationState=state, isGenerating=False,
                           hasError=error, error="broken" if error else "",
                           hasWarning=bool(warning), warning=warning or "",
                           isSuppressed=suppressed)


class TestScopedReadinessWarningVerdict:
    """A SCOPED poll (target=a setup/operation, or a scoped handle) ends on the same shared verdict
    the document poll does - _cam_common.ready_verdict - so it cannot read plainly ready over
    warnings the document-level signal names. Getting there needs the tally to CARRY them: drop
    warnings/warning_sample out of _op_tally and the plain verdict silently comes back."""

    def _out(self, monkeypatch, ops):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        cam = _FakeCAM([_setup("Roughing", ops)])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        return _payload(gen.status_handler(target="Roughing"))

    def test_the_tally_carries_the_warning_count_and_its_sample(self, monkeypatch):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        t = gen._op_tally([_warn_op("Face1", warning=""), _warn_op("2D Contour1")])
        assert t["warnings"] == 1
        assert t["samples"]["warning"]["name"] == "2D Contour1"
        assert t["samples"]["warning"]["warning"].startswith("Contour Selection")

    def test_zero_warnings_keeps_the_plain_scoped_ready_verdict(self, monkeypatch):
        out = self._out(monkeypatch, [_warn_op("Face1", warning=""),
                                      _warn_op("Face2", warning="")])
        assert out["live_states"]["warnings"] == 0
        assert out["live_states"]["readiness"] == "2 of 2 active ops valid - ready to post."

    def test_one_warning_demotes_the_scoped_verdict_and_names_the_op_and_its_line(self, monkeypatch):
        # the exact boundary the scoped path was overstating: 1 warning, everything else valid.
        out = self._out(monkeypatch, [_warn_op("Face1", warning=""), _warn_op("2D Contour1")])
        readiness = out["live_states"]["readiness"]
        assert out["live_states"]["warnings"] == 1
        assert "ready to post." not in readiness
        assert "postable" in readiness and "1 with WARNINGS" in readiness
        assert "2D Contour1" in readiness
        assert "contours are missing selections" in readiness
        assert out["readiness"] == readiness             # and it reaches the agent as its own key

    def test_a_suppressed_warned_op_leaves_the_scoped_verdict_plain(self, monkeypatch):
        out = self._out(monkeypatch, [_warn_op("Face1", warning=""),
                                      _warn_op("Off1", state=2, suppressed=True)])
        assert out["live_states"]["warnings"] == 0
        assert out["live_states"]["readiness"] == "1 of 1 active ops valid - ready to post."

    def test_an_errored_scope_still_reads_BLOCKER_not_the_warning_verdict(self, monkeypatch):
        out = self._out(monkeypatch, [_warn_op("Drill1", error=True, warning=""),
                                      _warn_op("2D Contour1")])
        assert out["live_states"]["readiness"].startswith("BLOCKER:")


class TestScopedReadinessSetupBlockers:
    """A SCOPED poll reads the same SETUP-level prerequisites the document signal does. The
    discriminating fixture is a setup that is fully op-valid, warning-free and error-free AND has no
    machine: a verdict built from op state alone reads 'ready to post' on it, which is exactly what
    cam_get's blocked_by contradicted."""

    def _out(self, monkeypatch, target, machine=None, folder=None):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        ops = [_warn_op("Face1", warning=""), _warn_op("Face2", warning="")]
        if folder:
            setup = SharedSetup("Roughing", folders=[SharedFolder(folder, ops=ops)])
            setup.machine = machine
        else:
            setup = _setup("Roughing", ops, machine=machine)
        cam = _FakeCAM([setup])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        return _payload(gen.status_handler(target=target))

    def test_a_machine_less_setup_never_reads_ready_to_post_however_valid_its_ops(self, monkeypatch):
        out = self._out(monkeypatch, "Roughing", machine=None)
        readiness = out["live_states"]["readiness"]
        assert out["live_states"]["valid"] == 2 and out["live_states"]["warnings"] == 0
        assert "- ready to post." not in readiness          # op state alone did NOT earn it
        assert "no_machine_selected" in readiness and "'Roughing'" in readiness
        assert "cam_edit_setup" in readiness                # the remedy, in tool vocabulary
        assert out["live_states"]["setups_blocked"] == [
            {"name": "Roughing", "blocked_by": ["no_machine_selected"]}]
        assert out["readiness"] == readiness                # and it reaches the agent as its own key

    def test_an_assigned_machine_restores_the_plain_scoped_verdict(self, monkeypatch):
        # the other side of the boundary - the demotion must key on the blocker, not on being scoped
        out = self._out(monkeypatch, "Roughing",
                        machine=SimpleNamespace(description="Haas VF-2"))
        assert out["live_states"]["setups_blocked"] == []
        assert out["live_states"]["readiness"] == "2 of 2 active ops valid - ready to post."

    def test_a_folder_scoped_poll_reads_its_OWNING_setups_blockers(self, monkeypatch):
        # the parent-chain walk (owning_setup): the target is a FOLDER, so its blockers are the
        # setup's - a scope that only looked at its own object would find no machine field at all.
        out = self._out(monkeypatch, "Finishing ops", machine=None, folder="Finishing ops")
        assert "folder" in out["target"]
        assert out["live_states"]["setups_blocked"] == [
            {"name": "Roughing", "blocked_by": ["no_machine_selected"]}]
        assert "- ready to post." not in out["live_states"]["readiness"]

    def test_a_folder_under_a_machined_setup_stays_plainly_ready(self, monkeypatch):
        out = self._out(monkeypatch, "Finishing ops", folder="Finishing ops",
                        machine=SimpleNamespace(description="Haas VF-2"))
        assert out["live_states"]["readiness"] == "2 of 2 active ops valid - ready to post."


class TestScopedHealthLists:
    """A SCOPED status read's warning/error/empty LISTS and their counts describe the scope its
    tally does, and the payload names which set that is.

    Measured on a 99-op job: a folder-scoped read reported live_states.total 6 and
    operations_total 6 beside counts.with_warnings 34, with warning rows from both setups (and one
    operation name that exists in each). A reader could not tell which rows were its target's, and
    the counts contradicted the tally sitting next to them.

    The fixture below is that shape in miniature: two setups that REPEAT an operation name in each
    of the three buckets - warned ('Rough to Model Top'), errored ('Contour20') and empty ('Rough
    clean') - because duplicate names across setups are this template's documented reality, and a
    count that deduplicated by name would undercount every one of them. Every bucket is also a
    DIFFERENT size, within each scope and across the document, so a count wired to the wrong list
    cannot hide behind a coincidence. Roughing holds the two operations the warning predicate must
    reject - one warned AND errored, one warned AND suppressed."""

    def _cam(self, monkeypatch):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        roughing = SharedSetup("Roughing", ops=[               # 2 warned, 1 errored, 3 empty
            _op("Rough to Model Top", warning="Spindle speed exceeds the machine limit"),
            _op("Rough adaptive", warning="Spindle speed exceeds the machine limit"),
            _op("Contour20", warning="Chip load is high",
                error="Top height must not be below the bottom height"),
            _op("Parked drill", warning="Parked and warned", has_toolpath=False,
                toolpath_valid=False, suppressed=True, state=2),
            _op("Rough clean", has_toolpath=False),
            _op("Rough clean 2", has_toolpath=False),
            _op("Rough clean 3", has_toolpath=False)])
        finishing = SharedSetup("Finishing", ops=[             # 4 warned, 2 errored, 2 empty
            _op("Rough to Model Top", warning="Spindle speed exceeds the machine limit"),
            _op("Contour21", warning="Contour Selection: contours are missing selections."),
            _op("Finish chamfer", warning="Spindle speed exceeds the machine limit"),
            _op("Finish contour", warning="Spindle speed exceeds the machine limit"),
            _op("Finish bore", error="broken"),
            _op("Contour20", error="Top height must not be below the bottom height"),
            _op("Finish clean", has_toolpath=False),
            _op("Rough clean", has_toolpath=False)])
        for s in (roughing, finishing):
            s.machine = SimpleNamespace(description="Haas VF-2")
        cam = _FakeCAM([roughing, finishing])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        return cam

    def _names(self, rows):
        return [r["name"] for r in rows]

    def test_a_scoped_read_lists_only_its_own_scopes_warnings_errors_and_empties(self, monkeypatch):
        # THE BITE: 'Contour21', 'Finish chamfer', the other setup's 'Rough to Model Top' and
        # 'Finish clean' belong to a scope this call did not name - none may appear under 'Roughing'.
        self._cam(monkeypatch)
        out = _payload(gen.status_handler(target="Roughing"))
        assert out["live_states"]["total"] == 7
        assert self._names(out["operations_with_warnings"]) == ["Rough to Model Top",
                                                                "Rough adaptive"]
        assert self._names(out["operations_with_errors"]) == ["Contour20"]
        assert out["empty_toolpaths"] == ["Rough clean", "Rough clean 2", "Rough clean 3"]

    def test_the_scoped_counts_never_pair_with_a_wider_list(self, monkeypatch):
        # A scoped tally beside document-wide counts is a pair a reader cannot tell apart. Counts and
        # lists are built from ONE operation set, so each count is its own list's length - and the
        # three buckets are different sizes, so a count reading the wrong list is a wrong number.
        self._cam(monkeypatch)
        out = _payload(gen.status_handler(target="Roughing"))
        assert out["counts"] == {"with_warnings": 2, "with_errors": 1, "empty_toolpaths": 3}
        assert out["counts"]["with_warnings"] == len(out["operations_with_warnings"])
        assert out["counts"]["with_errors"] == len(out["operations_with_errors"])
        assert out["counts"]["empty_toolpaths"] == len(out["empty_toolpaths"])

    def test_the_warning_count_and_the_readiness_tally_share_one_predicate(self, monkeypatch):
        # health_scope asserts the tally and the lists describe one set - so they must also AGREE.
        # live_states.warnings counts through _cam_common.counts_as_warning, which drops a warning
        # on an ERRORED op (its error already blocks the post) and on a SUPPRESSED one (excluded
        # from the post); the health count reads the same predicate, so the two numbers match.
        self._cam(monkeypatch)
        out = _payload(gen.status_handler(target="Roughing"))
        assert out["live_states"]["warnings"] == out["counts"]["with_warnings"] == 2
        named = self._names(out["operations_with_warnings"])
        assert "Contour20" not in named        # warned AND errored - its error row is the report
        assert "Parked drill" not in named     # suppressed - it is not in the post at all

    def test_the_payload_names_which_operations_the_health_lists_cover(self, monkeypatch):
        self._cam(monkeypatch)
        out = _payload(gen.status_handler(target="Roughing"))
        # health_scope is the ONE place the lists' scope is stated - the note does not restate it
        assert out["health_scope"] == "setup 'Roughing'"
        assert "Roughing" not in out["note"]

    def test_a_document_read_still_covers_every_setup(self, monkeypatch):
        # the other side of the boundary: unscoped, the lists ARE the whole document's - it is the
        # scope that narrows them, so a read naming none narrows nothing.
        self._cam(monkeypatch)
        out = _payload(gen.status_handler())
        assert out["live_states"]["total"] == 15
        # A name TWO of these operations carry is rendered as its 'Setup / op' path - the only
        # thing that separates them; a name unique in this list stays the plain name a caller
        # passes back. Every bucket carries both kinds, so neither rule can be missing.
        assert self._names(out["operations_with_warnings"]) == [
            "Roughing / Rough to Model Top", "Rough adaptive", "Finishing / Rough to Model Top",
            "Contour21", "Finish chamfer", "Finish contour"]
        assert self._names(out["operations_with_errors"]) == [
            "Roughing / Contour20", "Finish bore", "Finishing / Contour20"]
        assert out["empty_toolpaths"] == ["Roughing / Rough clean", "Rough clean 2",
                                          "Rough clean 3", "Finish clean",
                                          "Finishing / Rough clean"]
        assert out["health_scope"] == "document"
        # 6 / 3 / 5 - three different numbers, so each count is pinned to its OWN list; and each
        # exceeds its bucket's DISTINCT-name count (5 / 2 / 4), so a count that deduplicated by
        # name - which would drop a real operation on any job reusing a name across setups - reads
        # short here rather than passing.
        assert out["counts"] == {"with_warnings": 6, "with_errors": 3, "empty_toolpaths": 5}
        assert out["live_states"]["warnings"] == out["counts"]["with_warnings"]

    def test_a_document_read_says_it_named_the_repeats_by_path(self, monkeypatch):
        # A reader meeting 'Roughing / Rough to Model Top' in a NAME field has to be told why it is
        # not a name. The clause ships only where the substitution happened.
        self._cam(monkeypatch)
        note = _payload(gen.status_handler())["note"]
        assert "Repeated names show as 'Setup / op' paths" in note

    def test_a_scope_whose_names_are_already_distinct_claims_no_substitution(self, monkeypatch):
        # the other side: inside 'Roughing' every name identifies one operation, so the rows keep
        # their plain names AND the note must not claim a rename it did not make.
        self._cam(monkeypatch)
        out = _payload(gen.status_handler(target="Roughing"))
        assert "Rough to Model Top" in self._names(out["operations_with_warnings"])
        assert "Roughing / Rough to Model Top" not in self._names(out["operations_with_warnings"])
        assert "named by their setup path" not in out["note"]

    def test_a_folder_scoped_read_stops_at_the_folder(self, monkeypatch):
        # A folder-scoped read covers the folder's OWN operations, so the setup's other operations
        # stay out of the folder's lists.
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        folder = SharedFolder("WindowFrame Roughing", ops=[    # 1 warned, 0 errored, 2 empty
            _op("Contour20", warning="Spindle speed exceeds the machine limit"),
            _op("Rough clean", has_toolpath=False),
            _op("Rough clean 2", has_toolpath=False)])
        setup = SharedSetup("WindowFrame", ops=[_op("Contour21", warning="outside the folder")],
                            folders=[folder])
        setup.machine = SimpleNamespace(description="Haas VF-2")
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (_FakeCAM([setup]), None))
        out = _payload(gen.status_handler(target="WindowFrame Roughing"))
        assert out["health_scope"] == "folder 'WindowFrame Roughing'"
        assert out["live_states"]["total"] == 3
        assert self._names(out["operations_with_warnings"]) == ["Contour20"]
        assert out["empty_toolpaths"] == ["Rough clean", "Rough clean 2"]
        assert out["counts"] == {"with_warnings": 1, "with_errors": 0, "empty_toolpaths": 2}

    def test_a_scoped_handle_read_lists_only_its_launch_targets_operations(self, monkeypatch):
        # the handle path settles completion on the launch target's own ops (_handle_scope_state);
        # its health lists read that same set, and health_scope repeats the completion basis.
        gen._GENERATIONS.clear()
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")
        self._cam(monkeypatch)
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=True, numberOfOperations=7,
                                      numberOfCompleted=7),
            "target": "setup 'Roughing'", "scope": "setup", "target_name": "Roughing",
            "started_at": 0.0, "total": 7, "doc_name": "Doc", "doc_urn": "urn:doc",
                "doc_key": "urn:doc"}
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True
        assert out["health_scope"] == out["completion_basis"] == "setup 'Roughing'"
        assert self._names(out["operations_with_warnings"]) == ["Rough to Model Top",
                                                                "Rough adaptive"]
        assert out["counts"] == {"with_warnings": 2, "with_errors": 1, "empty_toolpaths": 3}

    def test_an_incomplete_scoped_read_publishes_no_health_lists(self, monkeypatch):
        # nothing is claimed about a scope still computing - the lists (and the scope name that
        # qualifies them) appear only once the read reports completed.
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        setup = SharedSetup("Roughing", ops=[_op("Rough clean", has_toolpath=False)])
        setup.operations._items[0].isGenerating = True
        setup.machine = SimpleNamespace(description="Haas VF-2")
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (_FakeCAM([setup]), None))
        out = _payload(gen.status_handler(target="Roughing"))
        assert out["completed"] is False
        assert "empty_toolpaths" not in out and "health_scope" not in out

    def test_include_operations_false_publishes_no_health_lists(self, monkeypatch):
        self._cam(monkeypatch)
        out = _payload(gen.status_handler(target="Roughing", include_operations=False))
        assert out["completed"] is True
        assert "counts" not in out and "health_scope" not in out
        assert "cover:" not in out["note"]


class TestOpLabelsRepeatedPath:
    """A label reaching two rows is the row count restated and nothing else.

    _op_labels substitutes a row's 'Setup / ... / op' path for a name several rows carry. That path
    is the container's address joined with the SAME name, so two rows agreeing on both agree on the
    whole string and the substitution alone prints one address twice. Such a row takes the position
    it holds in this list beside its path - the discriminator workspace_orient._empty_labels spends
    on the same shape, so the two listings name a repeated address one way rather than two."""

    def _node(self, name, path):
        return gen._cam_common.CamNode(object(), "operation", name, "S", path, None)

    def test_a_name_only_one_row_carries_stays_the_bare_name(self):
        rows = [self._node("Bore", "S1 / Bore"), self._node("Face", "S1 / Face")]
        assert gen._op_labels(rows) == ["Bore", "Face"]

    def test_a_repeated_name_whose_paths_differ_spends_no_position(self):
        rows = [self._node("Bore", "S1 / Bore"), self._node("Bore", "S2 / Bore")]
        assert gen._op_labels(rows) == ["S1 / Bore", "S2 / Bore"]

    def test_a_repeated_PATH_takes_the_rows_position_beside_it(self):
        # THE BITE: one name AND one address on both rows, so the path substitution by itself
        # prints 'S1 / Bore' twice and separates neither.
        rows = [self._node("Bore", "S1 / Bore"), self._node("Bore", "S1 / Bore")]
        labels = gen._op_labels(rows)
        assert labels == ["S1 / Bore (operation 1)", "S1 / Bore (operation 2)"]
        assert len(set(labels)) == len(labels)

    def test_the_position_is_spent_only_where_the_path_failed(self):
        rows = [self._node("Bore", "S1 / Bore"), self._node("Bore", "S1 / Bore"),
                self._node("Bore", "S2 / Bore")]
        assert gen._op_labels(rows) == ["S1 / Bore (operation 1)", "S1 / Bore (operation 2)",
                                        "S2 / Bore"]

    def test_the_position_counts_over_the_whole_list_not_per_repeated_address(self):
        # Two different repeated addresses: a counter restarting per address prints
        # '(operation 1)' twice and separates neither pair.
        rows = [self._node("Bore", "S1 / Bore"), self._node("Bore", "S1 / Bore"),
                self._node("Face", "S2 / Face"), self._node("Face", "S2 / Face")]
        assert gen._op_labels(rows) == [
            "S1 / Bore (operation 1)", "S1 / Bore (operation 2)",
            "S2 / Face (operation 3)", "S2 / Face (operation 4)"]

    def test_a_row_whose_path_is_empty_keeps_its_plain_name(self):
        # A blank discriminator addresses nothing, so it is never dressed up with a position -
        # told_apart's own rule, unchanged by the repeat check.
        rows = [self._node("Bore", ""), self._node("Bore", "S2 / Bore")]
        assert gen._op_labels(rows) == ["Bore", "S2 / Bore"]

    def test_a_repeated_address_reaches_the_wire_separated_and_explained(self, monkeypatch):
        # end to end: the rows a reader meets in a 'name' field are distinct, and the note says
        # what the trailing number is - a reader cannot recover that from the payload.
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        setup = SharedSetup("Roughing", ops=[_op("Rough clean", has_toolpath=False),
                                             _op("Rough clean", has_toolpath=False)])
        setup.machine = SimpleNamespace(description="Haas VF-2")
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (_FakeCAM([setup]), None))
        out = _payload(gen.status_handler(target="Roughing"))
        assert out["empty_toolpaths"] == ["Roughing / Rough clean (operation 1)",
                                          "Roughing / Rough clean (operation 2)"]
        assert "then by position" in out["note"]


class TestStatusLivePoll:
    def setup_method(self):
        gen._GENERATIONS.clear()
        gen._HANDLE_SEQ[0] = 0

    def _states(self, **kw):
        return TestStatusHandler._states(TestStatusHandler(), **kw)

    def _readiness(self, **kw):
        return TestStatusHandler._readiness(TestStatusHandler(), **kw)

    # An op generated INLINE (cam_create_operation(generate=true), cam_select_geometry, or the UI) has
    # no cam_generate handle. A status read with NO handle must report its live generation state, not
    # refuse for lack of a launched generation.
    def test_no_handle_reports_inline_generation(self, monkeypatch):
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(generating=1, out_of_date=1, total=2,
                                            readiness="0 of 2 active ops valid - run cam_generate to finish the rest."))
        out = _payload(gen.status_handler())     # no handle, no generations registered
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
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        out = _payload(gen.status_handler())
        assert out["completed"] is True and out["handle"] is None

    def test_the_live_path_words_what_completed_means(self, monkeypatch):
        # the same claim on the no-handle path: nothing generating, but 0 of 34 valid - so the note
        # says completed is not a success verdict and carries the readiness line that is.
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(valid=0, out_of_date=34, generating=0, total=34,
                                            readiness="0 of 34 active ops valid - run cam_generate to finish the rest."))
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        out = _payload(gen.status_handler())
        assert out["completed"] is True
        assert "not a success verdict" in out["note"]
        assert "0 of 34 active ops valid" in out["readiness"] and "'readiness'" in out["note"]

    def test_live_errored_op_flagged_not_generating_forever(self, monkeypatch):
        # an errored op will NEVER finish - a still-generating live poll must flag the BLOCKER now, not
        # report it as generating forever.
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            self._readiness(errored=1, generating=1, total=2,
                                            readiness="BLOCKER: 1 operation(s) have errors - the job will not post until fixed.",
                                            samples={"op": {"name": "Bad Op", "error": "broken"},
                                                     "setup": None, "program": None}))
        out = _payload(gen.status_handler())
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
        out = _payload(gen.status_handler(target="Roughing"))
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
        out = _payload(gen.status_handler(target="Finish"))
        assert out["live_states"]["errored"] == 1
        assert out["live_states"]["valid"] == 1
        assert out["completed"] is True                         # nothing generating (errored != generating)
        assert "BLOCKER" in out["readiness"]                    # the verdict beside the flag

    def test_target_not_found_errors(self, monkeypatch):
        cam = _FakeCAM([_setup("Roughing")])
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        res = gen.status_handler(target="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_live_read_is_a_single_snapshot_no_pumping(self, monkeypatch):
        # The status read reports the CURRENT state once and returns - it never loops, sleeps, or
        # pumps the event loop (generation runs in the background on its own). One _scope_state
        # read per call, even when ops are still generating.
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (object(), None))
        reads = {"n": 0}

        def one_read(cam, target):
            reads["n"] += 1
            return self._states(generating=1, total=1), "document", list, None
        monkeypatch.setattr(gen, "_scope_state", one_read)
        out = _payload(gen.status_handler())
        assert reads["n"] == 1                                  # exactly one snapshot
        assert out["completed"] is False
        assert "pumped_seconds" not in out


class TestStatusNamesAToolpathThatGeneratedEmpty:
    """The status read's own shape for the EMPTY class's second half: an operation whose toolpath
    generated empty reads hasToolpath True and state IsValid, so nothing in the flags separates it
    from one that cut - its own machining time (0.0 s against 4.193 s) is what does."""

    def _out(self, monkeypatch, times):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        setup = SharedSetup("S1", ops=[_op("Swarf1", warning="No passes to link."),
                                       _op("Swarf4", warning="Tool was lifted.")])
        cam = _FakeCAM([setup], machining_times=times)
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        return _payload(gen.status_handler(target="S1"))

    def test_the_empty_operation_is_named_and_the_cutting_one_is_not(self, monkeypatch):
        out = self._out(monkeypatch, {"Swarf1": 0.0, "Swarf4": 4.193083})
        assert out["empty_toolpaths"] == ["Swarf1"]
        assert out["counts"]["empty_toolpaths"] == 1
        # both still bucket VALID - the empty list is an overlay on that bucket, not a state change
        assert out["live_states"]["valid"] == 2

    def test_an_unread_time_names_nothing(self, monkeypatch):
        # every getMachiningTime raises here: an unread signal is not a measurement of zero
        out = self._out(monkeypatch, {})
        assert out["empty_toolpaths"] == [] and out["counts"]["empty_toolpaths"] == 0


# ── the entitlement pre-flight ─────────────────────────────────────────────────────────────────────
#
# Measured: a whole-document generate holding two operations whose strategy reads
# isGenerationAllowed false regenerated NOTHING while three healthy out-of-date ops sat in the same
# scope, and targeting one of those healthy ops alone generated it in 2.6 s. So the launch reads the
# entitlement first, excludes the operations reading false, and launches the rest one at a time.

def _entitlement(table):
    """A ``_cam_common.strategy_generation_allowed`` stand-in: strategy name -> True / False / None.
    A name absent from the table reads None - the flag that would not read, which blocks nothing."""
    return lambda name: table.get(name)


class TestEntitlementPreflight:
    def _install(self, monkeypatch, setups, table):
        import adsk.cam
        monkeypatch.setattr(adsk.cam.Operation, "cast", staticmethod(lambda x: x))
        cam = _FakeCAM(setups)
        monkeypatch.setattr(gen._cam_common, "get_cam", lambda: (cam, None))
        monkeypatch.setattr(gen._cam_common, "strategy_generation_allowed", _entitlement(table))
        return cam

    def _launched(self, cam):
        return [tgt.name for kind, tgt in cam.generate_calls if kind == "target"]

    def test_a_blocked_op_is_excluded_and_the_healthy_ones_still_launch(self, monkeypatch):
        # THE BITE: the whole-document sweep is what regenerated nothing, so it must not be the call
        # made here - each operation that did not read false is launched on its own.
        gen._GENERATIONS.clear()
        setup = SharedSetup("S", ops=[SharedOp("Cham", operation_state=1, strategy="chamfer"),
                                      SharedOp("Face1", operation_state=1, strategy="face"),
                                      SharedOp("Face2", operation_state=1, strategy="face")])
        cam = self._install(monkeypatch, [setup], {"chamfer": False, "face": True})
        out = _payload(gen.generate_handler(target=""))
        assert out["launched"] is True
        assert [kind for kind, _ in cam.generate_calls] == ["target", "target"]
        assert self._launched(cam) == ["Face1", "Face2"]
        assert out["entitlement_blocked"] == [{"name": "Cham", "strategy": "chamfer"}]
        assert out["operations_to_generate"] == 2
        assert "isGenerationAllowed false" in out["note"] and "Cham" in out["note"]
        assert "Machining Extension" in out["note"]          # the remedy cam_create_operation uses
        assert "cam_get_status" in out["note"]               # this launch claims no completion either

    def test_one_handle_covers_every_future_the_split_launch_made(self, monkeypatch):
        # the futures must all stay REFERENCED: Fusion abandons an in-progress generation whose
        # Future is garbage-collected, so the registry entry holds them, not just the first.
        gen._GENERATIONS.clear()
        setup = SharedSetup("S", ops=[SharedOp("Cham", operation_state=1, strategy="chamfer"),
                                      SharedOp("Face1", operation_state=1, strategy="face"),
                                      SharedOp("Face2", operation_state=1, strategy="face")])
        self._install(monkeypatch, [setup], {"chamfer": False, "face": True})
        out = _payload(gen.generate_handler(target=""))
        entry = gen._GENERATIONS[out["handle"]]
        assert len(entry["futures"]) == 2
        assert entry["future"] is entry["futures"][0]
        gen._GENERATIONS.clear()

    def test_every_op_blocked_refuses_and_launches_nothing(self, monkeypatch):
        setup = SharedSetup("S", ops=[SharedOp("Cham", operation_state=1, strategy="chamfer"),
                                      SharedOp("Walls", operation_state=1, strategy="inclined_walls")])
        cam = self._install(monkeypatch, [setup],
                            {"chamfer": False, "inclined_walls": False})
        res = gen.generate_handler(target="")
        assert res["isError"] is True
        assert "Cham" in res["message"] and "Walls" in res["message"]
        assert "Machining Extension" in res["message"]
        assert cam.generate_calls == []

    def test_a_blocked_operation_target_is_refused_by_name(self, monkeypatch):
        setup = SharedSetup("S", ops=[SharedOp("Cham", operation_state=1, strategy="chamfer")])
        cam = self._install(monkeypatch, [setup], {"chamfer": False})
        res = gen.generate_handler(target="Cham", skip_valid=False)
        assert res["isError"] is True and "isGenerationAllowed false" in res["message"]
        assert cam.generate_calls == []

    def test_an_unreadable_flag_excludes_nothing_and_is_disclosed(self, monkeypatch):
        # an unread flag is no entitlement verdict, so the plain document sweep still runs - and the
        # payload says the pre-flight was not made rather than staying silent about it.
        gen._GENERATIONS.clear()
        setup = SharedSetup("S", ops=[SharedOp("Odd", operation_state=1, strategy="mystery")])
        cam = self._install(monkeypatch, [setup], {})
        out = _payload(gen.generate_handler(target=""))
        assert cam.generate_calls == [("all", True)]
        assert "entitlement_blocked" not in out
        assert out["entitlement_unread"] == 1 and "did not read" in out["note"]
        gen._GENERATIONS.clear()

    def test_the_split_launch_skips_the_valid_and_the_suppressed_ones(self, monkeypatch):
        gen._GENERATIONS.clear()
        ops = [SharedOp("Cham", operation_state=1, strategy="chamfer"),
               SharedOp("Done", operation_state=0, strategy="face"),
               SharedOp("Parked", operation_state=2, suppressed=True, strategy="face"),
               SharedOp("Todo", operation_state=1, strategy="face")]
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)],
                            {"chamfer": False, "face": True})
        out = _payload(gen.generate_handler(target="", skip_valid=True))
        assert self._launched(cam) == ["Todo"]
        assert out["operations_to_generate"] == 1
        gen._GENERATIONS.clear()

    def test_skip_valid_false_regenerates_the_valid_op_but_never_the_suppressed_one(self, monkeypatch):
        # the boundary of the skip test: state 0 is skipped only while skip_valid is set, and a
        # suppressed op is skipped either way (measured: generateAllToolpaths skips it too).
        gen._GENERATIONS.clear()
        ops = [SharedOp("Cham", operation_state=1, strategy="chamfer"),
               SharedOp("Done", operation_state=0, strategy="face"),
               SharedOp("Parked", operation_state=2, suppressed=True, strategy="face"),
               SharedOp("Todo", operation_state=1, strategy="face")]
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)],
                            {"chamfer": False, "face": True})
        _payload(gen.generate_handler(target="", skip_valid=False))
        assert self._launched(cam) == ["Done", "Todo"]
        gen._GENERATIONS.clear()

    def test_the_blocked_arms_skip_is_built_by_the_one_shared_builder(self, monkeypatch):
        # Two arms returning a hand-built skip drift: this one carries the entitlement rows and the
        # note ON TOP of the shared reason/hint, never a second wording of them.
        gen._GENERATIONS.clear()
        ops = [SharedOp("Cham", operation_state=1, strategy="chamfer"),
               SharedOp("Done", operation_state=0, strategy="face")]
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)],
                            {"chamfer": False, "face": True})
        out = _payload(gen.generate_handler(target="", skip_valid=True))
        shared = gen._nothing_to_launch("all setups", 0, 1)
        assert out["reason"] == shared["reason"] and out["hint"] == shared["hint"]
        assert out["entitlement_blocked"] == [{"name": "Cham", "strategy": "chamfer"}]
        assert "isGenerationAllowed false" in out["note"]
        assert cam.generate_calls == []

    def test_blocked_beside_only_valid_ops_launches_nothing_and_says_why(self, monkeypatch):
        ops = [SharedOp("Cham", operation_state=1, strategy="chamfer"),
               SharedOp("Done", operation_state=0, strategy="face")]
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)],
                            {"chamfer": False, "face": True})
        out = _payload(gen.generate_handler(target="", skip_valid=True))
        assert out["launched"] is False and cam.generate_calls == []
        assert "1 already valid" in out["reason"] and "0 suppressed" in out["reason"]
        assert out["entitlement_blocked"][0]["name"] == "Cham"

    def test_a_launch_that_raises_is_named_beside_the_ones_that_started(self, monkeypatch):
        # partial success is reported as partial: the two that started keep their handle, and the
        # one the platform refused is named rather than swallowed into the count.
        gen._GENERATIONS.clear()
        ops = [SharedOp("Cham", operation_state=1, strategy="chamfer"),
               SharedOp("Bad", operation_state=1, strategy="face"),
               SharedOp("Good", operation_state=1, strategy="face")]
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)],
                            {"chamfer": False, "face": True})
        real = cam.generateToolpath

        def _launch(tgt):
            if tgt.name == "Bad":
                raise RuntimeError("3 : Toolpath requires tool to be selected.")
            return real(tgt)
        cam.generateToolpath = _launch
        out = _payload(gen.generate_handler(target=""))
        assert out["launched"] is True and out["operations_to_generate"] == 1
        assert out["launch_failures"][0]["name"] == "Bad"
        assert "tool to be selected" in out["launch_failures"][0]["error"]
        gen._GENERATIONS.clear()

    def test_every_remaining_launch_failing_is_an_error_not_a_launched_payload(self, monkeypatch):
        ops = [SharedOp("Cham", operation_state=1, strategy="chamfer"),
               SharedOp("Bad", operation_state=1, strategy="face")]
        cam = self._install(monkeypatch, [SharedSetup("S", ops=ops)],
                            {"chamfer": False, "face": True})
        cam.generateToolpath = lambda tgt: None      # returns no future: nothing is running
        res = gen.generate_handler(target="")
        assert res["isError"] is True and "Bad" in res["message"]

    def test_a_clean_scope_still_takes_the_plain_launch(self, monkeypatch):
        # no blocked op means no behaviour change: the document sweep and the scoped launch stay
        # the single measured calls they were.
        gen._GENERATIONS.clear()
        setup = SharedSetup("S", ops=[SharedOp("Face1", operation_state=1, strategy="face")])
        cam = self._install(monkeypatch, [setup], {"face": True})
        out = _payload(gen.generate_handler(target=""))
        assert cam.generate_calls == [("all", True)]
        assert "entitlement_blocked" not in out and "entitlement_unread" not in out
        gen._GENERATIONS.clear()

    def test_the_scoped_readiness_names_the_blocked_op_instead_of_a_regenerate(self, monkeypatch):
        # the poll's own half of the row: 'run cam_generate to finish the rest' is circular advice
        # over an operation cam_generate excludes, so the verdict names it.
        ops = [SharedOp("Cham", operation_state=1, strategy="chamfer"),
               SharedOp("Face1", operation_state=0, strategy="face")]
        self._install(monkeypatch, [SharedSetup("S", ops=ops)], {"chamfer": False, "face": True})
        out = _payload(gen.status_handler(target="S"))
        readiness = out["live_states"]["readiness"]
        assert "Cham" in readiness and "isGenerationAllowed false" in readiness
        assert "run cam_generate to finish the rest" not in readiness

    def test_a_stalled_poll_carries_the_readiness_that_names_them(self, monkeypatch):
        # the stall warning fires exactly where a blocked op parks: nothing generating, out-of-date
        # ops left. Without the verdict beside it the note only guesses at broken geometry.
        gen._GENERATIONS.clear()
        gen._GENERATIONS["gen1"] = {
            "future": SimpleNamespace(isGenerationCompleted=False, numberOfOperations=2,
                                      numberOfCompleted=0),
            "target": "all setups", "started_at": 0.0, "total": 2,
            "doc_name": "Doc", "doc_urn": "urn:doc", "doc_key": "urn:doc"}
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 0, "out_of_date": 2, "generating": 0, "total": 2,
                                      "readiness": "0 of 2 active ops valid; 1 of them read "
                                                   "isGenerationAllowed false"}, None))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False and "WARNING" in out["note"]
        assert "isGenerationAllowed false" in out["note"]
        gen._GENERATIONS.clear()


class TestOneHandleOverSeveralFutures:
    """A split launch registers several Futures under ONE handle, so the poll settles on all of
    them: one still running keeps the handle open, and the counts are the sum."""

    def setup_method(self):
        gen._GENERATIONS.clear()
        gen._HANDLE_SEQ[0] = 0

    @pytest.fixture(autouse=True)
    def _same_active_document(self, monkeypatch):
        monkeypatch.setattr(gen, "_active_identity", lambda: ("Doc", "urn:doc"))
        monkeypatch.setattr(gen, "document_key", lambda: "urn:doc")

    def _entry(self, *pairs):
        futures = [SimpleNamespace(isGenerationCompleted=done, numberOfOperations=ops,
                                   numberOfCompleted=ops if done else 0)
                   for ops, done in pairs]
        return {"future": futures[0], "futures": futures, "target": "all setups",
                "started_at": 0.0, "total": None, "doc_name": "Doc", "doc_urn": "urn:doc",
                "doc_key": "urn:doc"}

    def test_one_future_still_running_keeps_the_handle_incomplete(self, monkeypatch):
        # nothing reads as generating in the live tally, so ONLY the second Future's own flag can
        # hold this back - reading the first alone would publish a premature completed:true.
        gen._GENERATIONS["gen1"] = self._entry((1, True), (1, False))
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 2, "generating": 0, "total": 2,
                                      "readiness": "ready to post."}, None))
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is False and "gen1" in gen._GENERATIONS

    def test_the_totals_are_summed_across_the_futures(self, monkeypatch):
        gen._GENERATIONS["gen1"] = self._entry((2, True), (3, True))
        gen._HANDLE_SEQ[0] = 1
        monkeypatch.setattr(gen._cam_common, "live_readiness",
                            lambda: ({"valid": 5, "generating": 0, "total": 5,
                                      "readiness": "ready to post."}, None))
        monkeypatch.setattr(gen, "_document_ops", list)
        monkeypatch.setattr(gen, "_collect_op_health",
                            lambda ops, labels=None: {"warnings": [], "errors": [], "empty": [],
                                                      "empty_rail": []})
        out = _payload(gen.status_handler(handle="gen1"))
        assert out["completed"] is True
        assert out["operations_total"] == 5 and out["operations_completed"] == 5
