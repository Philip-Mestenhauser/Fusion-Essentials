"""Unit tests for ``cam_info.py`` pure helpers.

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

cam = load_tool("cam_info")


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
