"""Unit tests for ``_pmi.py`` - the shared PMI substrate every pmi_* tool rides: the design-wide
walk, the raw name resolution behind find_annotation, the read-back gates in apply_note_format /
apply_hole_flags / apply_hole_values, the tolerance and display codecs, the enum labeller, the
below-floor extension lift, and the text-point projection.

The gates are the point: each one is what stands between "the platform accepted the assignment"
and "the value is actually there", so each is exercised in BOTH directions - the set that took and
the set that silently did not.
"""

import math
from types import SimpleNamespace

import pytest

from conftest import load_tool

pm = load_tool("_pmi")

import adsk.core   # noqa: E402  the installed mock - alignment enum members come from it
import adsk.fusion  # noqa: E402


def _ann(name="Note1", suffix="PMILeaderLineNote", **extra):
    a = SimpleNamespace(name=name, objectType="adsk::fusion::" + suffix)
    for k, v in extra.items():
        setattr(a, k, v)
    return a


def _comp(name, annotations=()):
    anns = list(annotations)
    return SimpleNamespace(
        name=name,
        pmiAnnotations=SimpleNamespace(count=len(anns), item=lambda i: anns[i]))


# ── the design-wide walk + the raw name resolution ────────────────────────────────────────────

class TestWalkAnnotations:
    def test_walks_every_component_and_pairs_each_annotation_with_its_owner(self, monkeypatch):
        a, b, c = _ann("A"), _ann("B"), _ann("C")
        c1, c2 = _comp("Root", [a, b]), _comp("Sub", [c])
        monkeypatch.setattr(pm._common, "all_components", lambda d: [c1, c2])
        pairs = list(pm.walk_annotations(object()))
        assert [(comp.name, ann.name) for comp, ann in pairs] == [
            ("Root", "A"), ("Root", "B"), ("Sub", "C")]

    def test_a_component_with_no_pmi_collection_is_skipped_not_fatal(self, monkeypatch):
        good = _comp("Root", [_ann("A")])
        blank = SimpleNamespace(name="NoPMI", pmiAnnotations=None)
        monkeypatch.setattr(pm._common, "all_components", lambda d: [blank, good, blank])
        assert [a.name for _c, a in pm.walk_annotations(object())] == ["A"]

    def test_an_item_that_declines_to_read_drops_out_of_the_walk(self, monkeypatch):
        def boom(i):
            if i == 0:
                raise RuntimeError("bad index")
            return _ann("B")
        comp = SimpleNamespace(name="Root",
                               pmiAnnotations=SimpleNamespace(count=2, item=boom))
        monkeypatch.setattr(pm._common, "all_components", lambda d: [comp])
        assert [a.name for _c, a in pm.walk_annotations(object())] == ["B"]


class TestWalkHoles:
    """The walk's own honesty: what it could NOT read is counted, so a caller publishing tallies
    over it can say the design may hold more than it saw."""

    def test_a_complete_walk_reports_zero_holes(self, monkeypatch):
        comp = _comp("Root", [_ann("A"), _ann("B")])
        monkeypatch.setattr(pm._common, "all_components", lambda d: [comp])
        stats = {}
        assert len(list(pm.walk_annotations(object(), stats))) == 2
        assert stats == {"components_unreadable": 0, "items_unreadable": 0}

    def test_an_empty_but_readable_component_is_not_a_hole(self, monkeypatch):
        # the exact boundary: count 0 IS an answer. Counting it as unreadable would report a hole
        # on every component that simply carries no PMI.
        monkeypatch.setattr(pm._common, "all_components", lambda d: [_comp("Root", [])])
        stats = {}
        assert list(pm.walk_annotations(object(), stats)) == []
        assert stats["components_unreadable"] == 0

    def test_a_component_whose_collection_will_not_read_is_counted(self, monkeypatch):
        blank = SimpleNamespace(name="NoPMI", pmiAnnotations=None)
        monkeypatch.setattr(pm._common, "all_components",
                            lambda d: [blank, _comp("Root", [_ann("A")]), blank])
        stats = {}
        assert [a.name for _c, a in pm.walk_annotations(object(), stats)] == ["A"]
        assert stats["components_unreadable"] == 2 and stats["items_unreadable"] == 0

    def test_a_count_that_raises_is_a_hole_not_an_empty_component(self, monkeypatch):
        class _Raising:
            @property
            def count(self):
                raise RuntimeError("the collection is not available")

            def item(self, i):
                raise AssertionError("must never be reached")
        comp = SimpleNamespace(name="Root", pmiAnnotations=_Raising())
        monkeypatch.setattr(pm._common, "all_components", lambda d: [comp])
        stats = {}
        assert list(pm.walk_annotations(object(), stats)) == []
        assert stats["components_unreadable"] == 1

    def test_an_item_that_will_not_read_is_counted_without_dropping_its_siblings(self, monkeypatch):
        def boom(i):
            if i == 0:
                raise RuntimeError("bad index")
            return _ann("B")
        comp = SimpleNamespace(name="Root",
                               pmiAnnotations=SimpleNamespace(count=2, item=boom))
        monkeypatch.setattr(pm._common, "all_components", lambda d: [comp])
        stats = {}
        assert [a.name for _c, a in pm.walk_annotations(object(), stats)] == ["B"]
        assert stats["items_unreadable"] == 1 and stats["components_unreadable"] == 0

    def test_annotation_hits_hands_the_hole_record_through(self, monkeypatch):
        blank = SimpleNamespace(name="NoPMI", pmiAnnotations=None)
        monkeypatch.setattr(pm._common, "all_components",
                            lambda d: [blank, _comp("Root", [_ann("Note1")])])
        stats = {}
        hits, _available = pm.annotation_hits(object(), "Note1", stats=stats)
        assert len(hits) == 1 and stats["components_unreadable"] == 1


class TestAnnotationHits:
    """The raw resolution: it COUNTS, it never refuses - that judgment belongs to the caller, and
    pmi_edit's suppressed-PMI re-check needs the count to tell a miss from an ambiguity."""

    @pytest.fixture
    def two_components(self, monkeypatch):
        c1 = _comp("A", [_ann("Note1"), _ann("Other")])
        c2 = _comp("B", [_ann("Note1")])
        monkeypatch.setattr(pm._common, "all_components", lambda d: [c1, c2])
        return c1, c2

    def test_a_name_in_two_components_returns_both_hits(self, two_components):
        hits, available = pm.annotation_hits(object(), "note1")
        assert [c.name for _a, c in hits] == ["A", "B"]
        assert sorted(available) == ["Note1", "Note1", "Other"]

    def test_component_scope_narrows_both_hits_and_available(self, two_components):
        hits, available = pm.annotation_hits(object(), "Note1", component="B")
        assert len(hits) == 1 and hits[0][1].name == "B"
        assert available == ["Note1"]

    def test_a_miss_returns_no_hits_but_still_lists_what_exists(self, two_components):
        hits, available = pm.annotation_hits(object(), "Ghost")
        assert hits == [] and "Other" in available


# ── the alignment / perpendicular / extension read-back gates ─────────────────────────────────

class _Note:
    """A note whose format properties can be made to IGNORE an assignment - the exact platform
    shape every read-back gate exists for."""

    def __init__(self, ignore=()):
        object.__setattr__(self, "_ignore", set(ignore))
        # seeded past __setattr__ so an IGNORED property still READS - the platform shape is a
        # value that stays at its old reading, not one that vanishes.
        for key, value in (("horizontalAlignment", None), ("verticalAlignment", None),
                           ("isPerpendicularLine", False), ("leaderLineExtension", 0.5)):
            object.__setattr__(self, key, value)

    def __setattr__(self, key, value):
        if key != "_ignore" and key in getattr(self, "_ignore", ()):
            return
        object.__setattr__(self, key, value)


class TestApplyNoteFormat:
    def test_alignment_that_does_not_take_is_an_error_naming_the_knob(self):
        note = _Note(ignore=["horizontalAlignment"])
        err = pm.apply_note_format(note, align="left")
        assert err and "'align'=left" in err and "did not take" in err

    def test_valign_that_does_not_take_is_an_error_naming_the_knob(self):
        note = _Note(ignore=["verticalAlignment"])
        err = pm.apply_note_format(note, valign="top")
        assert err and "'valign'=top" in err

    def test_alignment_that_takes_returns_no_error(self):
        note = _Note()
        assert pm.apply_note_format(note, align="right", valign="bottom") is None
        assert note.horizontalAlignment == adsk.core.HorizontalAlignments.RightHorizontalAlignment
        assert note.verticalAlignment == adsk.core.VerticalAlignments.BottomVerticalAlignment

    def test_perpendicular_that_does_not_take_is_an_error_not_a_silent_ok(self):
        # The bool is invisible to the caller any other way: without the re-read a swallowed
        # perpendicular no-op returns ok and the payload echoes the request.
        note = _Note(ignore=["isPerpendicularLine"])
        err = pm.apply_note_format(note, perpendicular=True)
        assert err and "'perpendicular'=True" in err and "re-read False" in err
        assert note.isPerpendicularLine is False

    def test_perpendicular_that_takes_is_written_and_accepted(self):
        note = _Note()
        assert pm.apply_note_format(note, perpendicular=True) is None
        assert note.isPerpendicularLine is True

    def test_extension_that_does_not_take_is_an_error_naming_the_reread(self):
        note = _Note(ignore=["leaderLineExtension"])
        err = pm.apply_note_format(note, extension_cm=0.6)
        assert err and "did not take" in err and "0.5" in err

    def test_extension_under_the_floor_is_refused_before_any_write(self):
        note = _Note()
        err = pm.apply_note_format(note, extension_cm=0.1)
        assert err and str(pm.LEADER_EXT_FLOOR) in err
        assert note.leaderLineExtension == 0.5          # untouched

    def test_an_unknown_align_token_lists_the_vocabulary(self):
        err = pm.apply_note_format(_Note(), align="sideways")
        assert err and "left" in err and "center" in err


class TestNormalizeExtension:
    def test_a_below_floor_note_is_lifted_to_the_default(self):
        note = _Note()
        note.leaderLineExtension = 0.1
        assert pm.normalize_extension(note) is None
        assert note.leaderLineExtension == pm.LEADER_EXT_DEFAULT

    def test_a_note_at_or_above_the_floor_is_left_alone(self):
        note = _Note()
        note.leaderLineExtension = pm.LEADER_EXT_FLOOR
        assert pm.normalize_extension(note) is None
        assert note.leaderLineExtension == pm.LEADER_EXT_FLOOR

    def test_a_repair_that_raises_names_the_recreate_path(self):
        class Bricked:
            leaderLineExtension = 0.1

            def __setattr__(self, key, value):
                raise RuntimeError("Leader line extension is too small")
        err = pm.normalize_extension(Bricked())
        assert err and "recreate" in err and "pmi_delete" in err


# ── hole flags / values ───────────────────────────────────────────────────────────────────────

class TestApplyHoleFlags:
    def test_a_flag_that_does_not_read_back_is_an_error(self):
        note = _Note()
        note.isThrough = False
        object.__setattr__(note, "_ignore", {"isThrough"})
        applied, err = pm.apply_hole_flags(note, {"through": True})
        assert applied is None and "'through'=True did not take" in err

    def test_every_flag_that_takes_is_reported_back(self):
        note = _Note()
        note.isThrough = False
        note.isThreaded = False
        applied, err = pm.apply_hole_flags(note, {"through": True, "threaded": False})
        assert err is None and applied == {"through": True, "threaded": False}

    def test_an_unknown_flag_lists_the_vocabulary(self):
        applied, err = pm.apply_hole_flags(_Note(), {"bogus": True})
        assert applied is None and "bogus" in err and "quantity_note" in err

    def test_a_non_object_flags_payload_is_refused(self):
        applied, err = pm.apply_hole_flags(_Note(), ["through"])
        assert applied is None and "must be an object" in err


class TestApplyHoleValues:
    def _hole(self):
        return SimpleNamespace(
            diameter=SimpleNamespace(hasValue=True, value=0.0, isOverriddenValue=False,
                                     tolerance=None),
            depth=SimpleNamespace(hasValue=True, value=0.0, isOverriddenValue=False,
                                  tolerance=None))

    def test_a_non_numeric_value_is_refused_in_the_pre_pass(self):
        # Decidable from the request alone, so it refuses beside the other refusal classes -
        # a good key listed AHEAD of it must be left unwritten.
        note = self._hole()
        applied, err = pm.apply_hole_values(note, {"diameter": 6, "depth": "deep"}, 0.1)
        assert applied is None and "'depth' must be a number" in err and "'deep'" in err
        assert note.diameter.value == 0.0 and note.depth.value == 0.0

    def test_a_structured_value_is_refused_rather_than_raising_mid_write(self):
        note = self._hole()
        applied, err = pm.apply_hole_values(note, {"diameter": [6]}, 0.1)
        assert applied is None and "'diameter' must be a number" in err

    def test_a_bad_angle_value_names_degrees_in_the_refusal(self):
        note = SimpleNamespace(countersinkAngle=SimpleNamespace(
            hasValue=True, value=0.0, isOverriddenValue=False, tolerance=None))
        applied, err = pm.apply_hole_values(note, {"countersink_angle_deg": None or "wide"}, 0.1)
        assert applied is None and "degrees" in err

    def test_a_value_that_will_not_read_back_stops_at_that_key(self):
        note = self._hole()
        note.depth = SimpleNamespace(hasValue=False, value=0.0, isOverriddenValue=False,
                                     tolerance=None)
        applied, err = pm.apply_hole_values(note, {"depth": 3}, 0.1)
        assert applied is None and "'depth' did not read back" in err


# ── the tolerance codec ───────────────────────────────────────────────────────────────────────

class _Tol:
    """Records which setter the codec chose and what it was handed."""

    def __init__(self, accept=True):
        self.calls = []
        self._accept = accept
        self.hasTolerances = True
        self.toleranceType = 0
        self.hasUpperTolerance = True
        self.upperTolerance = 0.0
        self.hasLowerTolerance = True
        self.lowerTolerance = 0.0
        self.hasToleranceClass = True
        self.toleranceClassDeviation = "H"
        self.toleranceClassGrade = "7"
        self.hasShaftToleranceClass = True
        self.shaftToleranceClassDeviation = "h"
        self.shaftToleranceClassGrade = "6"

    def _record(self, name):
        def call(*args):
            self.calls.append((name, args))
            return self._accept
        return call

    def __getattr__(self, name):
        if name.startswith("set"):
            return self._record(name)
        raise AttributeError(name)


@pytest.fixture
def tol(monkeypatch):
    made = []

    def _create():
        made.append(_Tol())
        return made[-1]
    monkeypatch.setattr(pm.adsk.fusion, "PMIGeometricValueTolerance",
                        SimpleNamespace(create=_create))
    return made


class TestBuildTolerance:
    @pytest.mark.parametrize("spec,setter,args", [
        ({"type": "symmetric", "value": 5}, "setSymmetric", (0.5,)),
        ({"type": "deviation", "upper": 5, "lower": 2}, "setDeviation", (0.5, 0.2)),
        ({"type": "limits", "min": 1, "max": 3}, "setLimitsStacked", (0.1, 0.3)),
        ({"type": "limits_linear", "min": 1, "max": 3}, "setLimitsLinear", (0.1, 0.3)),
        ({"type": "max"}, "setMAX", ()),
        ({"type": "min"}, "setMIN", ()),
    ])
    def test_each_type_calls_its_own_setter_with_scaled_bounds(self, tol, spec, setter, args):
        built, err = pm.build_tolerance(spec, 0.1)
        assert err is None and built is tol[0]
        assert len(tol[0].calls) == 1 and tol[0].calls[0][0] == setter
        assert tol[0].calls[0][1] == pytest.approx(args)

    @pytest.mark.parametrize("kind,setter", [
        ("fits_stacked", "setLimitsFitsStacked"),
        ("fits_linear", "setLimitsFitsLinear"),
        ("fits_size_limits", "setLimitsFitsSizeLimits"),
        ("fits_tolerance", "setLimitsFitsTolerance"),
    ])
    def test_each_fits_type_passes_size_and_both_fit_classes(self, tol, kind, setter):
        built, err = pm.build_tolerance(
            {"type": kind, "size": 20, "hole_fit": "H7", "shaft_fit": "h6"}, 0.1)
        assert err is None and built is tol[0]
        assert tol[0].calls == [(setter, (2.0, "H7", "h6"))]

    def test_a_setter_returning_false_is_a_refusal_not_a_tolerance(self, monkeypatch):
        monkeypatch.setattr(pm.adsk.fusion, "PMIGeometricValueTolerance",
                            SimpleNamespace(create=lambda: _Tol(accept=False)))
        built, err = pm.build_tolerance({"type": "symmetric", "value": 5}, 0.1)
        assert built is None and "declined by the platform" in err

    def test_the_type_is_matched_case_insensitively(self, tol):
        built, err = pm.build_tolerance({"type": "Symmetric", "value": 5}, 0.1)
        assert err is None and tol[0].calls[0][0] == "setSymmetric"


class TestToleranceRecord:
    def test_an_untoleranced_value_records_nothing(self):
        assert pm.tolerance_record(SimpleNamespace(hasTolerances=False), 10.0) is None
        assert pm.tolerance_record(None, 10.0) is None

    def test_bounds_and_both_fit_classes_are_published_scaled(self):
        t = _Tol()
        t.upperTolerance, t.lowerTolerance = 0.05, 0.02
        rec = pm.tolerance_record(t, 10.0)
        assert rec["upper"] == 0.5 and rec["lower"] == 0.2
        assert rec["hole_fit"] == "H7" and rec["shaft_fit"] == "h6"

    def test_absent_halves_are_omitted_rather_than_zeroed(self):
        t = _Tol()
        t.hasLowerTolerance = False
        t.hasToleranceClass = False
        t.hasShaftToleranceClass = False
        rec = pm.tolerance_record(t, 10.0)
        assert "lower" not in rec and "hole_fit" not in rec and "shaft_fit" not in rec


class TestValueRecord:
    def test_an_angle_reports_degrees_and_a_length_reports_display_units(self):
        angle = pm.value_record(SimpleNamespace(hasValue=True, value=math.radians(90),
                                                isOverriddenValue=False, tolerance=None),
                                10.0, angle=True)
        length = pm.value_record(SimpleNamespace(hasValue=True, value=0.6,
                                                 isOverriddenValue=False, tolerance=None), 10.0)
        assert angle["value"] == 90.0        # NOT 0.6 rad * 10
        assert length["value"] == 6.0

    def test_an_angle_bound_is_converted_in_degrees_not_by_the_length_factor(self):
        t = _Tol()
        t.upperTolerance = t.lowerTolerance = math.radians(1)
        t.hasToleranceClass = t.hasShaftToleranceClass = False
        rec = pm.value_record(SimpleNamespace(hasValue=True, value=math.radians(90),
                                              isOverriddenValue=False, tolerance=t),
                              10.0, angle=True)
        assert rec["tolerance"]["upper"] == 1.0

    def test_an_overridden_value_says_so_and_an_unset_one_records_nothing(self):
        rec = pm.value_record(SimpleNamespace(hasValue=True, value=0.6, isOverriddenValue=True,
                                              tolerance=None), 10.0)
        assert rec["overridden"] is True
        assert pm.value_record(SimpleNamespace(hasValue=False), 10.0) is None


# ── the display codec + the ONE display writer ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def display_settings(monkeypatch):
    """A FRESH PMIDisplaySettings per create() - the shared mock hands back one object, which
    would make a primary/secondary pair look identical however they were written."""
    monkeypatch.setattr(
        pm.adsk.fusion, "PMIDisplaySettings",
        SimpleNamespace(create=lambda: SimpleNamespace(
            precision=None, unitType=None, hasLeadingZeros=None, hasTrailingZeros=None,
            hasUnitAbbreviation=None)))


class TestDisplay:
    def test_build_display_maps_the_unit_token_and_the_bools(self):
        ds, err = pm.build_display({"precision": 3, "units": "mm", "leading_zeros": True,
                                    "trailing_zeros": False, "unit_abbreviation": True})
        assert err is None
        assert ds.precision == 3
        assert ds.unitType == adsk.fusion.PMIUnitTypes.MillimetersPMIUnitType
        assert ds.hasLeadingZeros is True and ds.hasTrailingZeros is False

    def test_a_non_object_display_is_refused(self):
        ds, err = pm.build_display("precision=3")
        assert ds is None and "must be an object" in err

    def test_display_record_reads_the_five_fields_back(self):
        ds = SimpleNamespace(precision=2,
                             unitType=adsk.fusion.PMIUnitTypes.MillimetersPMIUnitType,
                             hasLeadingZeros=True, hasTrailingZeros=False,
                             hasUnitAbbreviation=True)
        rec = pm.display_record(ds)
        assert rec["precision"] == 2 and rec["units"] == "millimeters"
        assert rec["leading_zeros"] is True and rec["trailing_zeros"] is False
        assert pm.display_record(None) is None

    def test_apply_display_writes_the_secondary_settings_and_turns_the_flag_on(self):
        note = SimpleNamespace(primaryDisplaySettings=None, secondaryDisplaySettings=None,
                               hasSecondaryDisplaySettings=False)
        err = pm.apply_display(note, {"precision": 2, "secondary": {"precision": 4}})
        assert err is None
        assert note.primaryDisplaySettings.precision == 2
        assert note.hasSecondaryDisplaySettings is True
        assert note.secondaryDisplaySettings.precision == 4

    def test_a_bad_secondary_spec_is_refused_naming_the_secondary(self):
        note = SimpleNamespace(primaryDisplaySettings=None, secondaryDisplaySettings=None,
                               hasSecondaryDisplaySettings=False)
        err = pm.apply_display(note, {"secondary": {"units": "furlong"}})
        assert err and err.startswith("display.secondary:")

    def test_a_primary_only_spec_leaves_the_secondary_flag_alone(self):
        note = SimpleNamespace(primaryDisplaySettings=None, secondaryDisplaySettings=None,
                               hasSecondaryDisplaySettings=False)
        assert pm.apply_display(note, {"precision": 1}) is None
        assert note.hasSecondaryDisplaySettings is False


# ── the enum labeller ─────────────────────────────────────────────────────────────────────────

class TestEnumLabel:
    def test_a_member_value_becomes_its_snake_name_with_the_suffix_stripped(self):
        got = pm.enum_label(adsk.core, "HorizontalAlignments", "HorizontalAlignment",
                            adsk.core.HorizontalAlignments.LeftHorizontalAlignment)
        assert got == "left"

    def test_a_multiword_member_is_split_on_the_case_boundaries(self):
        got = pm.enum_label(adsk.fusion, "PMIUnitTypes", "PMIUnitType",
                            adsk.fusion.PMIUnitTypes.UseDocumentUnitPMIUnitType)
        assert got == "use_document_unit"

    def test_an_unmapped_value_falls_back_to_the_raw_int_as_a_string(self):
        assert pm.enum_label(adsk.fusion, "PMIUnitTypes", "PMIUnitType", 99_999) == "99999"

    def test_an_unknown_enum_class_does_not_raise(self):
        assert pm.enum_label(adsk.fusion, "NoSuchEnumTypes", "NoSuchEnumType", 3) == "3"


# ── the text-point projection ─────────────────────────────────────────────────────────────────

class TestSetTextPoint:
    @pytest.fixture(autouse=True)
    def point3d(self, monkeypatch):
        monkeypatch.setattr(adsk.core.Point3D, "create",
                            staticmethod(lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)))

    def _note(self, plane):
        return SimpleNamespace(leaderLineExtension=0.5, plane=plane, annotationTextPoint=None)

    def test_an_off_plane_point_is_projected_onto_the_annotation_plane(self):
        # plane z=1, normal +z: a point at z=5 (mm) must land AT the plane, not above it.
        plane = SimpleNamespace(normal=SimpleNamespace(x=0.0, y=0.0, z=1.0),
                                origin=SimpleNamespace(x=0.0, y=0.0, z=1.0))
        note = self._note(plane)
        got, err = pm.set_text_point(note, [10, 20, 50], 0.1)
        assert err is None
        assert (got.x, got.y, got.z) == (1.0, 2.0, 1.0)     # x/y kept, z pulled onto the plane

    def test_a_non_unit_plane_normal_still_projects_exactly(self):
        # The projection divides by |n|^2, so a normal of length 3 must not scale the correction.
        plane = SimpleNamespace(normal=SimpleNamespace(x=0.0, y=0.0, z=3.0),
                                origin=SimpleNamespace(x=0.0, y=0.0, z=1.0))
        note = self._note(plane)
        got, err = pm.set_text_point(note, [0, 0, 50], 0.1)
        assert err is None and got.z == pytest.approx(1.0)

    def test_a_note_with_no_readable_plane_writes_the_point_unprojected(self):
        note = self._note(None)
        got, err = pm.set_text_point(note, [10, 20, 50], 0.1)
        assert err is None and (got.x, got.y, got.z) == (1.0, 2.0, 5.0)

    def test_a_non_numeric_point_is_refused_before_any_write(self):
        note = self._note(None)
        got, err = pm.set_text_point(note, ["x", 0, 0], 0.1)
        assert got is None and "[x, y, z] numbers" in err
        assert note.annotationTextPoint is None

    def test_a_point_that_does_not_read_back_is_an_error(self):
        class Stubborn:
            leaderLineExtension = 0.5
            plane = None

            def __setattr__(self, key, value):
                pass                    # accepts the assignment, keeps nothing

            def __getattr__(self, key):
                if key == "annotationTextPoint":
                    return None
                raise AttributeError(key)
        got, err = pm.set_text_point(Stubborn(), [1, 2, 3], 0.1)
        assert got is None and "did not take" in err

    def test_a_point_that_reads_back_somewhere_else_is_an_error(self):
        # the platform accepts the assignment and the re-read WORKS - it just answers a different
        # place. Existence alone passes that; the comparison is what catches it, and without it the
        # payload publishes the wrong anchor as the new one.
        class Drifting:
            leaderLineExtension = 0.5
            plane = None
            annotationTextPoint = SimpleNamespace(x=9.0, y=9.0, z=9.0)

            def __setattr__(self, key, value):
                pass                    # accepts the assignment, keeps its own point
        got, err = pm.set_text_point(Drifting(), [10, 20, 30], 0.1)
        assert got is None and "did not take" in err and "9.0" in err

    def test_a_sub_micron_settle_is_not_a_failed_move(self):
        # the exact boundary on the other side of 1e-6 cm: float settle must not fail the move
        class Settling:
            leaderLineExtension = 0.5
            plane = None

            def __setattr__(self, key, value):
                if key == "annotationTextPoint":
                    object.__setattr__(self, key, SimpleNamespace(
                        x=value.x + 5e-7, y=value.y, z=value.z))
                else:
                    object.__setattr__(self, key, value)
        got, err = pm.set_text_point(Settling(), [10, 20, 30], 0.1)
        assert err is None and got.x == pytest.approx(1.0, abs=1e-5)

    def test_a_drift_just_over_the_tolerance_is_reported(self):
        class Drifting:
            leaderLineExtension = 0.5
            plane = None

            def __setattr__(self, key, value):
                if key == "annotationTextPoint":
                    object.__setattr__(self, key, SimpleNamespace(
                        x=value.x + 1.1e-6, y=value.y, z=value.z))
                else:
                    object.__setattr__(self, key, value)
        got, err = pm.set_text_point(Drifting(), [10, 20, 30], 0.1)
        assert got is None and "did not take" in err

    def test_a_point_whose_coordinates_will_not_read_is_unknown_not_confirmed(self):
        class Opaque:
            leaderLineExtension = 0.5
            plane = None

            def __setattr__(self, key, value):
                pass

            def __getattr__(self, key):
                if key == "annotationTextPoint":
                    return SimpleNamespace()        # a point object with no x/y/z at all
                raise AttributeError(key)
        got, err = pm.set_text_point(Opaque(), [1, 2, 3], 0.1)
        assert got is None and "UNKNOWN" in err


# ── the light record ──────────────────────────────────────────────────────────────────────────

class TestAnnotationRecord:
    def test_a_fusion_authored_note_publishes_its_plain_text(self):
        rec = pm.annotation_record(SimpleNamespace(name="Root"),
                                   _ann("Note1", plainText="DEBURR", isVisible=True))
        assert rec["text"] == "DEBURR" and rec["kind"] == "note"

    def test_imported_pmi_omits_the_text_key_rather_than_publishing_null(self):
        # plainText lives on the two Fusion-authored classes and on no imported one, so an
        # imported row that published text:null would contradict the tool's own description.
        imported = _ann("Dim1", suffix="PMIImportedDimension", isVisible=True)
        rec = pm.annotation_record(SimpleNamespace(name="Root"), imported)
        assert rec["kind"] == "imported_dimension"
        assert "text" not in rec

    def test_only_the_created_kinds_carry_plaintext_in_the_generated_api_surface(self):
        # The reason the key is conditional, checked against the generated member lists rather
        # than asserted in prose: if a Fusion update gives an imported class plainText, this goes
        # red and the record can publish it unconditionally again.
        import api_surface
        with_text = {k.split(".")[-1] for k, members in api_surface.PROPERTIES.items()
                     if k.startswith("fusion.PMI") and "plainText" in members}
        assert with_text == {"PMILeaderLineNote", "PMIHoleThreadNote"}

    def test_the_measured_reference_failure_message_reads_as_sentences(self):
        # The exact string Fusion hands back for three failed references: an HTML count, a <br/>,
        # three sentences glued to their neighbours, and a title plus the annotation's own NAME
        # after the last full stop - the tail the reader cuts, so no entity name is re-spaced.
        raw = ("Face 1 missingFace 2 missingFace 3 missing<b>3 Reference Failures</b><br/>The "
               "model is using cached geometry to solve. Please reselect reference geometry for "
               "failed features in the timeline.The selection geometry has become invalid. It is "
               "likely that the selection is missing. \nPlease edit this annotation and select new "
               "reference geometry.Selected Geometry LostProbeNote")
        rec = pm.annotation_record(SimpleNamespace(name="Root"),
                                   _ann("ProbeNote", errorOrWarningMessage=raw))
        assert rec["warning"] == (
            "Face 1 missing Face 2 missing Face 3 missing 3 Reference Failures The model is using "
            "cached geometry to solve. Please reselect reference geometry for failed features in "
            "the timeline. The selection geometry has become invalid. It is likely t ...")
        assert len(rec["warning"]) <= pm._WARNING_LIMIT + 4

    def test_the_trailing_title_and_annotation_name_are_cut_not_re_spaced(self):
        # A ONE-failure message is short enough that the bound cannot hide the tail: the title and
        # the annotation's own name sit after the last full stop, and 'ProbeNote' must not come
        # back out as two words.
        raw = ("Face 1 missing<b>1 Reference Failure</b><br/>The model is using cached geometry "
               "to solve.Selected Geometry LostProbeNote")
        rec = pm.annotation_record(SimpleNamespace(name="Root"),
                                   _ann("ProbeNote", errorOrWarningMessage=raw))
        assert rec["warning"] == ("Face 1 missing 1 Reference Failure The model is using cached "
                                  "geometry to solve.")
        assert "Probe Note" not in rec["warning"]

    def test_a_message_with_no_terminator_keeps_its_whole_text(self):
        # THE FALLBACK BOUNDARY: cutting at the last terminator must not empty a message that
        # carries none - the short warnings ('reference lost') are exactly that shape.
        rec = pm.annotation_record(SimpleNamespace(name="Root"),
                                   _ann("N", errorOrWarningMessage="reference lost"))
        assert rec["warning"] == "reference lost"

    def test_the_warning_flags_ride_only_when_set(self):
        healthy = _ann("N", isVisible=True, isOutOfDate=False, isSuppressed=False,
                       errorOrWarningMessage="")
        rec = pm.annotation_record(SimpleNamespace(name="Root"), healthy)
        assert "out_of_date" not in rec and "suppressed" not in rec and "warning" not in rec
        sick = _ann("N", isVisible=False, isOutOfDate=True, isSuppressed=True,
                    errorOrWarningMessage="reference lost")
        rec = pm.annotation_record(SimpleNamespace(name="Root"), sick)
        assert rec["out_of_date"] is True and rec["suppressed"] is True
        assert rec["warning"] == "reference lost"
