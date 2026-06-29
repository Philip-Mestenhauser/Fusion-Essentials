"""Unit tests for ``cam_read.py`` pure helpers.

Focus: ``_hms`` (seconds -> H:MM:SS, with boundary/garbage handling) and
``_operation_summary`` (the state-name mapping and the ``is_out_of_date``
roll-up, which is real branching a machinist relies on).

``_operation_type_name`` is intentionally NOT tested here: it keys a dict off
``adsk.cam.OperationTypes`` members, which are Mocks in this harness, so a test
would assert on the mock rather than on real behaviour. That tool's enum mapping
is better exercised by an in-Fusion integration test.
"""

from types import SimpleNamespace

from conftest import load_tool

cam = load_tool("cam_read")


# ── _hms: seconds -> H:MM:SS ───────────────────────────────────────────────

class TestHms:
    def test_zero(self):
        assert cam._hms(0) == "0:00:00"

    def test_under_a_minute_pads_seconds(self):
        assert cam._hms(5) == "0:00:05"

    def test_minutes_and_seconds_padded(self):
        assert cam._hms(125) == "0:02:05"   # 2 min 5 s

    def test_hours_not_padded_minutes_seconds_are(self):
        assert cam._hms(3661) == "1:01:01"  # 1 h 1 m 1 s

    def test_rounds_fractional_seconds(self):
        assert cam._hms(59.6) == "0:01:00"  # rounds up across the minute boundary

    def test_garbage_input_is_safe(self):
        assert cam._hms("not a number") == "0:00:00"


# ── _operation_summary: state mapping + out-of-date logic ──────────────────

def _op(**kw):
    """Build a fake CAM operation; unspecified attrs default to benign values."""
    defaults = dict(
        name="Op", tool=None, strategy="face", operationState=0,
        hasToolpath=True, isToolpathValid=True, isGenerating=False,
        isSuppressed=False, isOptional=False, hasWarning=False, hasError=False,
        warning="", error="",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestOperationSummary:
    def test_valid_state_name(self):
        s = cam._operation_summary(_op(operationState=0))
        assert s["state"] == "valid"
        assert s["is_out_of_date"] is False

    def test_invalid_state_is_out_of_date(self):
        # state 1 (invalid) and not suppressed -> needs regeneration.
        s = cam._operation_summary(_op(operationState=1, isSuppressed=False))
        assert s["state"] == "invalid"
        assert s["is_out_of_date"] is True

    def test_no_toolpath_state_is_out_of_date(self):
        s = cam._operation_summary(_op(operationState=3, isSuppressed=False))
        assert s["state"] == "no_toolpath"
        assert s["is_out_of_date"] is True

    def test_suppressed_invalid_is_not_out_of_date(self):
        # Suppression deliberately exempts an op from "needs regen".
        s = cam._operation_summary(_op(operationState=1, isSuppressed=True))
        assert s["is_out_of_date"] is False

    def test_warning_text_surfaced(self):
        s = cam._operation_summary(_op(hasWarning=True, warning="  Spindle too fast  "))
        assert s["has_warning"] is True
        assert s["warning"] == "Spindle too fast"   # stripped

    def test_unknown_state_falls_back_to_raw_value(self):
        s = cam._operation_summary(_op(operationState=99))
        assert s["state"] == 99


# ── cam_get_time: getMachiningTime UNITS (the audit's confirmed quantitative bug) ───────────────
#
# getMachiningTime(operations, feedScale, rapidFeed, toolChangeTime) — confirmed live:
#   feedScale is a PERCENT (100, not 1.0 -> the old 1.0 meant 1% feed, ~100x too slow);
#   rapidFeed is cm/SECOND (the old 1000 'cm/min' was ~600 m/min). These pin the corrected values.

import json


class _FakeMT:
    machiningTime = 60.0
    totalFeedTime = 50.0
    totalRapidTime = 10.0
    toolChangeCount = 2


class _FakeSetup:
    def __init__(self, name):
        self.name = name


class _FakeSetups:
    def __init__(self, names):
        self._s = [_FakeSetup(n) for n in names]
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]


class _FakeCAM:
    def __init__(self, setup_names):
        self.setups = _FakeSetups(setup_names)
        self.time_calls = []
    def getMachiningTime(self, obj, feed_scale, rapid_feed, tool_change):
        self.time_calls.append((obj, feed_scale, rapid_feed, tool_change))
        return _FakeMT()


class TestMachiningTimeUnits:
    def _run(self, setup_names=("Setup1",), setup=""):
        fake = _FakeCAM(setup_names)
        real = cam._get_cam
        cam._get_cam = lambda: (fake, None)
        try:
            res = cam.get_machining_time_handler(setup=setup)
        finally:
            cam._get_cam = real
        assert res["isError"] is False, res
        return fake, json.loads(res["content"][0]["text"])

    def test_feed_scale_is_percent_not_fraction(self):
        fake, _ = self._run()
        _, feed_scale, _, _ = fake.time_calls[0]
        assert feed_scale == 100.0      # 100%, NOT 1.0 (which would be 1% feed)

    def test_rapid_feed_is_cm_per_second(self):
        fake, _ = self._run()
        _, _, rapid_feed, _ = fake.time_calls[0]
        # ~250 in/min = 10.58 cm/s — a sane machine traverse, NOT the old 1000.
        assert 5.0 < rapid_feed < 30.0

    def test_assumptions_reported_in_payload(self):
        _, out = self._run()
        a = out["assumptions"]
        assert a["feed_scale_percent"] == 100.0
        assert 5.0 < a["rapid_feed_cm_per_s"] < 30.0


# ── handler-level logic: setup filtering, tallies, de-dupe, sort, diff ──────────────────────────────
# These exercise the per-handler pure logic (name filter, the not-found error, the tools_used /
# distinct-tool tallies, the X-ref de-dupe, the compare diff) by faking _get_cam to return a small
# in-memory CAM. The cast pass-throughs (Operation.cast/Occurrence.cast) are wired in conftest.

import adsk.cam
import adsk.fusion


def _run(handler, **kw):
    res = handler(**kw)
    if res["isError"]:
        return res, None
    return res, json.loads(res["content"][0]["text"])


def _with_cam(fake, fn):
    real = cam._get_cam
    cam._get_cam = lambda: (fake, None)
    try:
        return fn()
    finally:
        cam._get_cam = real


class _Op:
    """A fake CAM operation. .name/.tool.description drive grouping; .parameters drive the diff."""
    def __init__(self, name, tool_desc=None, params=None, strategy="face"):
        self.name = name
        self.strategy = strategy
        self.operationState = 0
        self.hasToolpath = True; self.isToolpathValid = True
        self.isGenerating = False; self.isSuppressed = False; self.isOptional = False
        self.hasWarning = False; self.hasError = False; self.warning = ""; self.error = ""
        self.tool = SimpleNamespace(description=tool_desc) if tool_desc else None
        self._params = params or {}

    @property
    def parameters(self):
        items = [SimpleNamespace(title=k, name=k, expression=v) for k, v in self._params.items()]
        return SimpleNamespace(count=len(items), item=lambda i: items[i])


class _Setup:
    def __init__(self, name, ops=(), models=()):
        self.name = name
        self._ops = list(ops)
        self.models = list(models)
        self.fixtures = []
        self.stockSolids = []

    @property
    def allOperations(self):
        return list(self._ops)

    @property
    def operations(self):
        return SimpleNamespace(count=len(self._ops))


class _CAM:
    def __init__(self, setups):
        self._setups = list(setups)

    @property
    def setups(self):
        s = self._setups
        return SimpleNamespace(count=len(s), item=lambda i: s[i])


class TestGetOperations:
    def test_filters_to_named_setup_case_insensitive(self):
        fake = _CAM([_Setup("Roughing", [_Op("Face1", "EM6")]),
                     _Setup("Finishing", [_Op("Contour", "BN3")])])
        _, out = _with_cam(fake, lambda: _run(cam.get_cam_operations_handler, setup="finishing"))
        assert out["setup_count"] == 1
        assert out["setups"][0]["setup"] == "Finishing"

    def test_not_found_lists_available(self):
        fake = _CAM([_Setup("Roughing"), _Setup("Finishing")])
        res, _ = _with_cam(fake, lambda: _run(cam.get_cam_operations_handler, setup="Nope"))
        assert res["isError"] is True
        assert "Roughing" in res["message"] and "Finishing" in res["message"]

    def test_tools_used_tally_counts_each_tool(self):
        fake = _CAM([_Setup("S", [_Op("a", "EM6"), _Op("b", "EM6"), _Op("c", "BN3")])])
        _, out = _with_cam(fake, lambda: _run(cam.get_cam_operations_handler))
        counts = {t["tool"]: t["operation_count"] for t in out["tools_used"]}
        assert counts == {"EM6": 2, "BN3": 1}


class TestGetToolList:
    def test_groups_by_tool_and_sorts_by_use_desc(self):
        fake = _CAM([_Setup("S1", [_Op("a", "EM6"), _Op("b", "BN3")]),
                     _Setup("S2", [_Op("c", "EM6"), _Op("d", "EM6")])])
        _, out = _with_cam(fake, lambda: _run(cam.get_tool_list_handler))
        assert out["distinct_tool_count"] == 2
        # most-used first: EM6 (3) before BN3 (1)
        assert out["tools"][0]["tool"] == "EM6" and out["tools"][0]["operation_count"] == 3
        assert out["tools"][0]["setups"] == ["S1", "S2"]   # sorted, both setups
        assert out["tools"][1]["tool"] == "BN3"

    def test_ops_without_a_tool_are_skipped(self):
        fake = _CAM([_Setup("S", [_Op("a", "EM6"), _Op("noTool", None)])])
        _, out = _with_cam(fake, lambda: _run(cam.get_tool_list_handler))
        assert out["distinct_tool_count"] == 1


class TestActivateSetup:
    def test_empty_setup_errors(self):
        fake = _CAM([_Setup("S")])
        res, _ = _with_cam(fake, lambda: _run(cam.activate_setup_handler, setup=""))
        assert res["isError"] is True and "Provide 'setup'" in res["message"]

    def test_not_found_lists_available(self):
        fake = _CAM([_Setup("Roughing")])
        res, _ = _with_cam(fake, lambda: _run(cam.activate_setup_handler, setup="Ghost"))
        assert res["isError"] is True and "Roughing" in res["message"]

    def test_activates_matching_setup(self):
        activated = {}
        s = _Setup("Roughing")
        s.activate = lambda: activated.setdefault("hit", True)
        fake = _CAM([s])
        _, out = _with_cam(fake, lambda: _run(cam.activate_setup_handler, setup="roughing"))
        assert activated.get("hit") is True
        assert out["activated"] == "Roughing"


class TestCompareOperations:
    def test_requires_both_names(self):
        fake = _CAM([_Setup("S")])
        res, _ = _with_cam(fake, lambda: _run(cam.compare_operations_handler, operation_a="x"))
        assert res["isError"] is True and "both" in res["message"].lower()

    def test_operation_not_found(self):
        fake = _CAM([_Setup("S", [_Op("Face1", "EM6")])])
        res, _ = _with_cam(fake, lambda: _run(
            cam.compare_operations_handler, operation_a="Face1", operation_b="Ghost"))
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_diff_reports_same_and_differing_params(self):
        a = _Op("Face1", "EM6", params={"Stepover": "5mm", "Feed": "1000", "Depth": "2mm"})
        b = _Op("Face2", "EM6", params={"Stepover": "5mm", "Feed": "800", "Depth": "2mm"})
        fake = _CAM([_Setup("S", [a, b])])
        _, out = _with_cam(fake, lambda: _run(
            cam.compare_operations_handler, operation_a="Face1", operation_b="Face2"))
        assert out["same_parameter_count"] == 2          # Stepover + Depth
        assert out["difference_count"] == 1
        diff = out["differences"][0]
        assert diff["parameter"] == "Feed"
        assert diff["operation_a"] == "1000" and diff["operation_b"] == "800"

    def test_param_present_in_only_one_marked_not_present(self):
        a = _Op("Face1", "EM6", params={"Tilt": "on"})
        b = _Op("Face2", "EM6", params={})
        fake = _CAM([_Setup("S", [a, b])])
        _, out = _with_cam(fake, lambda: _run(
            cam.compare_operations_handler, operation_a="Face1", operation_b="Face2"))
        assert out["difference_count"] == 1
        diff = out["differences"][0]
        assert diff["parameter"] == "Tilt"
        assert diff["operation_b"] == "(not present)"


class TestReferencesDedupe:
    def test_same_ref_in_two_roles_deduped(self):
        # one external occurrence appears as BOTH a model and a fixture -> reported ONCE.
        def _occ():
            df = SimpleNamespace(id="urn:src:1", name="Vise.f3d", fusionWebURL="http://v")
            docref = SimpleNamespace(dataFile=df, version=2, isOutOfDate=False)
            return SimpleNamespace(name="Vise:1", isReferencedComponent=True,
                                   documentReference=docref)
        occ = _occ()
        s = _Setup("S", models=[occ])
        s.fixtures = [occ]
        fake = _CAM([s])
        # Occurrence.cast must pass our fake through (conftest models it as a passthrough Mock by
        # default returning a child Mock; wire it explicitly here to be a real passthrough).
        adsk.fusion.Occurrence.cast = staticmethod(lambda x: x)
        _, out = _with_cam(fake, lambda: _run(cam.get_setup_references_handler))
        refs = out["setups"][0]["references"]
        assert out["setups"][0]["reference_count"] == 1
        assert refs[0]["source_id"] == "urn:src:1"
        assert refs[0]["source_name"] == "Vise.f3d"

    def test_non_referenced_occurrence_skipped(self):
        occ = SimpleNamespace(name="Local:1", isReferencedComponent=False)
        s = _Setup("S", models=[occ])
        fake = _CAM([s])
        adsk.fusion.Occurrence.cast = staticmethod(lambda x: x)
        _, out = _with_cam(fake, lambda: _run(cam.get_setup_references_handler))
        assert out["setups"][0]["reference_count"] == 0
