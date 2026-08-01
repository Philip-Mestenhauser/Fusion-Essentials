"""Tests for `pmi_edit` - per-action read-back gating: a set that does not take is an error, an
imported annotation refuses text edits, and the below-floor leader extension is normalized before
a segment edit."""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, error_message

pe = load_tool("pmi_edit")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _FakeAnn:
    def __init__(self, name="Note1", suffix="PMILeaderLineNote", out_of_date=False):
        self.name = name
        self.objectType = "adsk::fusion::" + suffix
        self.plainText = "TEXT"
        self.isVisible = True
        self.isOutOfDate = out_of_date
        self.isSuppressed = False
        self.errorOrWarningMessage = ""
        self.isLightBulbOn = True
        self.leaderLineExtension = 0.5
        self.segments = None

    def markUpToDate(self):
        self.isOutOfDate = False
        return True


@pytest.fixture
def rig(monkeypatch):
    ann = _FakeAnn()
    comp = SimpleNamespace(name="Root")
    monkeypatch.setattr(pe._common, "design", lambda: object())
    monkeypatch.setattr(pe._pmi, "find_annotation", lambda d, n, c="": (ann, comp, None))
    monkeypatch.setattr(pe._pmi, "segments_markup", lambda a: "NEW")
    return SimpleNamespace(ann=ann, comp=comp, monkeypatch=monkeypatch)


class TestSetText:
    def test_replaces_segments_and_reports_the_markup(self, rig):
        out = _payload(pe.handler(action="set_text", annotation="Note1", text="NEW"))
        assert out["markup"] == "NEW" and rig.ann.segments is not None

    def test_imported_pmi_is_refused(self, rig):
        rig.ann.objectType = "adsk::fusion::PMIImportedNote"
        msg = error_message(pe.handler(action="set_text", annotation="Note1", text="X"))
        assert "read-only" in msg and "convert_imported" in msg

    def test_below_floor_extension_is_normalized_first(self, rig):
        rig.ann.leaderLineExtension = 0.13
        _payload(pe.handler(action="set_text", annotation="Note1", text="X"))
        assert rig.ann.leaderLineExtension == pe._pmi.LEADER_EXT_DEFAULT

    def test_bad_token_is_refused(self, rig):
        assert "'{bogus}'" in error_message(
            pe.handler(action="set_text", annotation="Note1", text="{bogus}"))


class TestRename:
    def test_rename_reads_back(self, rig):
        out = _payload(pe.handler(action="rename", annotation="Note1", new_name="Flatness"))
        assert out["name"] == "Flatness"

    def test_rename_that_does_not_take_is_an_error(self, rig):
        class Stubborn(_FakeAnn):
            @property
            def name(self):
                return "Note1"

            @name.setter
            def name(self, v):
                pass
        stubborn = Stubborn()
        rig.monkeypatch.setattr(pe._pmi, "find_annotation",
                                lambda d, n, c="": (stubborn, rig.comp, None))
        assert "did not take" in error_message(
            pe.handler(action="rename", annotation="Note1", new_name="X"))

    def test_missing_new_name_is_an_error(self, rig):
        assert "new_name" in error_message(pe.handler(action="rename", annotation="Note1"))


class TestVisibility:
    def test_hide_gates_on_the_reread(self, rig):
        out = _payload(pe.handler(action="hide", annotation="Note1"))
        assert out["light_bulb_on"] is False and rig.ann.isLightBulbOn is False

    def test_a_toggle_that_does_not_take_is_an_error(self, rig):
        class Frozen(_FakeAnn):
            @property
            def isLightBulbOn(self):
                return True

            @isLightBulbOn.setter
            def isLightBulbOn(self, v):
                pass
        rig.monkeypatch.setattr(pe._pmi, "find_annotation",
                                lambda d, n, c="": (Frozen(), rig.comp, None))
        assert "did not take" in error_message(pe.handler(action="hide", annotation="Note1"))

    def test_show_with_a_parent_bulb_off_reports_the_cause(self, rig):
        rig.ann.isLightBulbOn = False
        rig.ann.isVisible = False
        out = _payload(pe.handler(action="show", annotation="Note1"))
        assert "light bulb" in out["note"].lower()


class TestUpToDateAndConvert:
    def test_already_up_to_date_is_a_no_op_note(self, rig):
        out = _payload(pe.handler(action="mark_up_to_date", annotation="Note1"))
        assert "Already up to date" in out["note"]

    def test_a_decline_is_an_error(self, rig):
        rig.ann.isOutOfDate = True
        rig.ann.markUpToDate = lambda: False
        assert "declined" in error_message(pe.handler(action="mark_up_to_date", annotation="Note1"))

    def test_dismisses_and_rereads(self, rig):
        rig.ann.isOutOfDate = True
        out = _payload(pe.handler(action="mark_up_to_date", annotation="Note1"))
        assert "out_of_date" not in out

    def test_convert_on_fusion_authored_is_refused(self, rig):
        assert "already Fusion-authored" in error_message(
            pe.handler(action="convert_imported", annotation="Note1"))

    def test_convert_decline_is_an_error(self, rig):
        rig.ann.objectType = "adsk::fusion::PMIImportedDimension"
        rig.ann.convertImportedToFusionPMI = lambda: None
        assert "declined" in error_message(
            pe.handler(action="convert_imported", annotation="Note1"))

    def test_convert_reports_the_new_kind(self, rig):
        rig.ann.objectType = "adsk::fusion::PMIImportedDimension"
        rig.ann.convertImportedToFusionPMI = lambda: _FakeAnn(name="Note1",
                                                             suffix="PMIHoleThreadNote")
        out = _payload(pe.handler(action="convert_imported", annotation="Note1"))
        assert out["converted_to"] == "hole_note"


class TestNewActions:
    def test_set_flags_on_a_leader_note_is_refused(self, rig):
        assert "hole/thread callouts only" in error_message(
            pe.handler(action="set_flags", annotation="Note1", flags={"quantity_note": False}))

    def test_set_values_on_a_leader_note_is_refused(self, rig):
        assert "hole/thread callouts only" in error_message(
            pe.handler(action="set_values", annotation="Note1", values={"diameter": 6}))

    def test_set_extension_below_the_floor_is_refused(self, rig):
        msg = error_message(pe.handler(action="set_extension", annotation="Note1",
                                       leader_extension=1))     # 1 mm < 2.5 mm floor
        assert "floor" in msg

    def test_set_extension_reads_back(self, rig):
        out = _payload(pe.handler(action="set_extension", annotation="Note1",
                                  leader_extension=6))
        assert out["leader_extension"] == 6.0
        assert rig.ann.leaderLineExtension == pytest.approx(0.6)

    def test_suppress_without_a_timeline_feature_is_refused(self, rig):
        rig.ann.timelineObject = None
        assert "timeline" in error_message(pe.handler(action="suppress", annotation="Note1"))

    def test_suppress_gates_on_the_reread(self, rig):
        tl = SimpleNamespace(isSuppressed=False)
        rig.ann.timelineObject = tl
        out = _payload(pe.handler(action="suppress", annotation="Note1"))
        assert out["suppressed"] is True and tl.isSuppressed is True

    def test_unsuppress_reaches_a_suppressed_pmi_through_the_timeline(self, rig):
        # suppressed PMI leaves the collections; only the suppressed timeline feature's name
        # survives - unsuppress flips it and verifies the annotation reappears.
        item = SimpleNamespace(isSuppressed=True)
        rig.monkeypatch.setattr(pe._pmi, "suppressed_pmi_features", lambda d: [(item, "Note1")])
        rig.monkeypatch.setattr(
            pe._pmi, "find_annotation",
            lambda d, n, c="": ((None, None, "No PMI named 'Note1'.") if item.isSuppressed
                                else (rig.ann, rig.comp, None)))
        out = _payload(pe.handler(action="unsuppress", annotation="Note1"))
        assert out["suppressed"] is False and item.isSuppressed is False

    def test_unsuppress_rolls_back_a_wrong_same_named_feature(self, rig):
        item = SimpleNamespace(isSuppressed=True)
        rig.monkeypatch.setattr(pe._pmi, "suppressed_pmi_features", lambda d: [(item, "Note1")])
        rig.monkeypatch.setattr(pe._pmi, "find_annotation",
                                lambda d, n, c="": (None, None, "No PMI named 'Note1'."))
        msg = error_message(pe.handler(action="unsuppress", annotation="Note1"))
        assert "not a PMI" in msg and item.isSuppressed is True

    def test_set_leader_point_on_a_hole_note_is_refused(self, rig):
        rig.ann.objectType = "adsk::fusion::PMIHoleThreadNote"
        assert "leader notes only" in error_message(
            pe.handler(action="set_leader_point", annotation="Note1", leader_point=[1, 2, 3]))

    def test_set_leader_point_gates_on_the_platform_bool(self, rig):
        rig.ann.setAnnotationTargetPoint = lambda p: False
        assert "declined" in error_message(
            pe.handler(action="set_leader_point", annotation="Note1", leader_point=[1, 2, 3]))

    def test_set_alignment_needs_at_least_one_knob(self, rig):
        assert "needs" in error_message(pe.handler(action="set_alignment", annotation="Note1"))

    def test_set_plane_refuses_an_unsupported_type(self, rig):
        rig.ann.supportedAnnotationPlaneTypes = [99]
        assert "not supported" in error_message(
            pe.handler(action="set_plane", annotation="Note1", plane="xy"))

    def test_set_display_on_a_leader_note_is_refused(self, rig):
        assert "hole/thread callouts only" in error_message(
            pe.handler(action="set_display", annotation="Note1", display={"precision": 2}))


class TestGuards:
    def test_resolver_error_surfaces(self, rig):
        rig.monkeypatch.setattr(pe._pmi, "find_annotation",
                                lambda d, n, c="": (None, None, "No PMI named 'X'."))
        assert "No PMI named" in error_message(pe.handler(action="rename", annotation="X",
                                                          new_name="Y"))

    def test_no_active_design_is_an_error(self, rig):
        rig.monkeypatch.setattr(pe._common, "design", lambda: None)
        assert "No active design" in error_message(pe.handler(action="hide", annotation="N"))
