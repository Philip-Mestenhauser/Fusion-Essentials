"""Unit tests for ``sketch_insert_svg.py`` - Sketch.importSVG into a named-or-most-recent sketch.

Pinned: the mandatory on-disk file guard (a missing path is refused BY NAME and importSVG is never
called, so the raise that poisons the transaction cannot happen), the x/y display-unit -> centimetre
conversion, 'scale' crossing as a plain unitless multiplier, the curve-count delta that convicts a
true return which imported nothing, the size read back off the SKETCH (importSVG ignores the file's
width/height and viewBox, so the request predicts nothing about the result), and the sketch
resolution contract.
"""

import pytest

from conftest import (FakeBoundingBox3D, FakePoint, error_message, install, load_tool, make_design,
                      make_sketch, make_sketch_curve, payload)

# The bounding box a real import produced, in cm: rect.svg at x=1.0 y=0.5 scale=2 measured
# min (1.1048, -0.4535) max (3.0118, 0.3952) - 1.9070 x 0.8487 cm of geometry.
_LANDED = (FakePoint(1.1048, -0.4535, 0.0), FakePoint(3.0118, 0.3952, 0.0))


def _importer(store, returns=True, adds=1, bbox=None):
    """A Sketch.importSVG stand-in: records its positional arguments, grows the sketch by `adds`
    curves (0 = the API answered true while importing nothing) and - like the real call - leaves
    the sketch with the bounding box the imported geometry produced."""
    def _import(sketch, *args):
        store.append(args)
        for i in range(adds):
            sketch.sketchCurves._items.append(make_sketch_curve(f"svg{len(store)}_{i}"))
        if bbox is not None:
            sketch.boundingBox = FakeBoundingBox3D(*bbox)
        return returns
    return _import


def _wire(sketch, importer):
    """Bind `importer` as the sketch's OWN importSVG - the curves land in the sketch it is called
    on, so each sketch gets its own bound copy."""
    sketch.importSVG = lambda *args: importer(sketch, *args)


def _svg_file(tmp_path, name="logo.svg"):
    path = tmp_path / name
    path.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
    return str(path)


@pytest.fixture
def mod():
    return load_tool("sketch_insert_svg")


@pytest.fixture
def svg(tmp_path):
    """A real file on disk, so the isfile guard passes for the happy-path tests."""
    return _svg_file(tmp_path)


@pytest.fixture
def sketch(mod):
    """A 'Plate' sketch holding one curve, wired into the tool module."""
    sk = make_sketch("Plate", lines=[make_sketch_curve("L0", length=10.0)])
    install(mod, make_design(sketches=[sk]))
    return sk


@pytest.fixture
def two_sketches(mod):
    """'Plate' (1 curve) then 'Label' (0 curves) - Label is the most recent."""
    plate = make_sketch("Plate", lines=[make_sketch_curve("L0", length=10.0)])
    label = make_sketch("Label", lines=[])
    install(mod, make_design(sketches=[plate, label]))
    _wire(plate, _importer([], adds=3))
    _wire(label, _importer([], adds=3))
    return plate, label


class TestFileGuard:
    def test_missing_file_is_refused_by_name_without_calling_import(self, mod, sketch, tmp_path):
        calls = []
        _wire(sketch, _importer(calls))
        missing = str(tmp_path / "nope.svg")
        msg = error_message(mod.handler(file_path=missing))
        assert missing in msg and "No readable file" in msg
        # the raise this guard exists for rolls back the whole surrounding transaction, so the API
        # call must not have happened at all.
        assert calls == []

    def test_a_directory_path_is_refused_like_a_missing_file(self, mod, sketch, tmp_path):
        calls = []
        _wire(sketch, _importer(calls))
        folder = tmp_path / "art.svg"
        folder.mkdir()
        assert "No readable file" in error_message(mod.handler(file_path=str(folder)))
        assert calls == []

    def test_missing_file_path_is_refused(self, mod, sketch):
        assert "'file_path' is required" in error_message(mod.handler())

    def test_a_non_svg_extension_is_refused_naming_the_other_tool(self, mod, sketch, tmp_path):
        step = tmp_path / "part.step"
        step.write_text("x", encoding="utf-8")
        msg = error_message(mod.handler(file_path=str(step)))
        assert "not an .svg file" in msg and "doc_insert_import" in msg
        # the other entry point lands art the same way this one does - it has no scale of its own
        assert "no offset or scale to pass" in msg and "its own scale" not in msg

    def test_an_uppercase_extension_is_accepted(self, mod, sketch, tmp_path):
        _wire(sketch, _importer([]))
        out = payload(mod.handler(file_path=_svg_file(tmp_path, "LOGO.SVG")))
        assert out["imported"] is True


class TestPlacementAndScale:
    def test_x_and_y_are_converted_from_display_units_to_centimetres(self, mod, sketch, svg):
        calls = []
        _wire(sketch, _importer(calls))
        out = payload(mod.handler(file_path=svg, x=20, y=-5, units="mm"))
        _path, x_cm, y_cm, _factor = calls[0]
        assert (x_cm, y_cm) == (2.0, -0.5)
        # the payload reports the offsets back in the units the caller asked in
        assert out["position"] == {"x": 20.0, "y": -5.0, "units": "mm"}

    def test_inches_scale_by_their_own_factor(self, mod, sketch, svg):
        calls = []
        _wire(sketch, _importer(calls))
        mod.handler(file_path=svg, x=1, y=2, units="in")
        _path, x_cm, y_cm, _factor = calls[0]
        assert (round(x_cm, 6), round(y_cm, 6)) == (2.54, 5.08)

    def test_omitted_offsets_default_to_the_sketch_origin(self, mod, sketch, svg):
        calls = []
        _wire(sketch, _importer(calls))
        mod.handler(file_path=svg)
        assert calls[0][1:3] == (0.0, 0.0)

    def test_the_file_path_reaches_the_api_first_positionally(self, mod, sketch, svg):
        calls = []
        _wire(sketch, _importer(calls))
        mod.handler(file_path=svg)
        assert calls[0][0] == svg

    def test_scale_crosses_unscaled_by_the_units(self, mod, sketch, svg):
        # 'scale' is a MULTIPLIER on the SVG's own size: mm units must not turn 2 into 0.2.
        calls = []
        _wire(sketch, _importer(calls))
        out = payload(mod.handler(file_path=svg, scale=2, units="mm"))
        assert calls[0][3] == 2.0
        assert out["scale"] == 2.0

    def test_scale_defaults_to_one(self, mod, sketch, svg):
        calls = []
        _wire(sketch, _importer(calls))
        mod.handler(file_path=svg)
        assert calls[0][3] == 1.0

    def test_a_non_positive_scale_is_refused_naming_the_value(self, mod, sketch, svg):
        assert "greater than 0, got -1.5" in error_message(
            mod.handler(file_path=svg, scale=-1.5))

    def test_a_non_numeric_scale_is_refused(self, mod, sketch, svg):
        assert "'scale' must be a number" in error_message(mod.handler(file_path=svg, scale="big"))

    def test_unknown_units_are_refused(self, mod, sketch, svg):
        assert "furlong" in error_message(mod.handler(file_path=svg, x=1, units="furlong"))


class TestEffectIsReadBack:
    def test_the_curve_delta_is_published(self, mod, sketch, svg):
        _wire(sketch, _importer([], adds=7))
        out = payload(mod.handler(file_path=svg))
        assert out["curves_added"] == 7
        assert (out["curve_count_before"], out["curve_count_after"]) == (1, 8)
        assert out["sketch"] == "Plate"

    def test_true_with_no_new_curves_is_an_error(self, mod, sketch, svg):
        _wire(sketch, _importer([], returns=True, adds=0))
        msg = error_message(mod.handler(file_path=svg))
        assert "gained no curves" in msg and "still 1" in msg

    def test_a_false_return_is_reported_as_a_refusal(self, mod, sketch, svg):
        _wire(sketch, _importer([], returns=False, adds=0))
        msg = error_message(mod.handler(file_path=svg))
        assert "returned false" in msg and "Plate" in msg

    def test_a_raising_import_is_an_error_not_a_crash(self, mod, sketch, svg):
        def _boom(*args):
            raise RuntimeError("3 : invalid argument filename, not found")
        sketch.importSVG = _boom
        msg = error_message(mod.handler(file_path=svg))
        assert "Could not import" in msg and "invalid argument filename" in msg

    def test_the_note_states_that_imported_curves_append(self, mod, sketch, svg):
        # measured: an import APPENDS - the ids already in use keep their entities. A note claiming
        # a renumber would send the caller re-reading ids that never moved.
        _wire(sketch, _importer([]))
        note = payload(mod.handler(file_path=svg))["note"]
        assert "APPEND" in note and "sketch_get" in note
        assert "RENUMBER" not in note.upper()

    def test_the_note_does_not_point_at_a_tool_that_refuses_sketch_curves(self, mod, sketch, svg):
        # model_measure_between's TargetRef refuses a sketch curve, so pointing there is a dead end -
        # the measured 'sketch_extent' carries the size evidence instead.
        _wire(sketch, _importer([]))
        assert "model_measure_between" not in payload(mod.handler(file_path=svg))["note"]


class TestMeasuredExtent:
    def test_the_extent_is_read_off_the_sketch_after_the_import(self, mod, sketch, svg):
        _wire(sketch, _importer([], bbox=_LANDED))
        out = payload(mod.handler(file_path=svg, x=10, y=5, units="mm", scale=2))
        assert out["sketch_extent"] == {
            "width": 19.07, "height": 8.487,
            "min": {"x": 11.048, "y": -4.535}, "max": {"x": 30.118, "y": 3.952},
            "units": "mm"}

    def test_the_extent_does_not_track_the_requested_scale(self, mod, sketch, svg):
        # the size is MEASURED: importSVG ignores the file's width/height and viewBox, so a payload
        # derived from 'scale' (or from x/y) would be a confident fiction.
        _wire(sketch, _importer([], bbox=_LANDED))
        one = payload(mod.handler(file_path=svg, scale=1))["sketch_extent"]
        _wire(sketch, _importer([], bbox=_LANDED))
        ten = payload(mod.handler(file_path=svg, scale=10))["sketch_extent"]
        assert one == ten and one["width"] == 19.07

    def test_the_extent_follows_the_geometry_that_actually_landed(self, mod, sketch, svg):
        _wire(sketch, _importer([], bbox=(FakePoint(0.0, 0.0, 0.0), FakePoint(5.0, 2.0, 0.0))))
        out = payload(mod.handler(file_path=svg, units="cm"))
        assert (out["sketch_extent"]["width"], out["sketch_extent"]["height"]) == (5.0, 2.0)

    def test_a_sketch_that_already_held_geometry_publishes_both_ends(self, mod, sketch, svg):
        # sketch.boundingBox spans the WHOLE sketch: art 19.07mm wide imported beside curves out at
        # x=-50mm reads 80.118mm. Calling that the art's size sends the caller re-scaling art that
        # is already right - so both ends ship and the note refuses the art-size claim.
        sketch.boundingBox = FakeBoundingBox3D(FakePoint(-5.0, -0.5, 0.0), FakePoint(-4.0, 0.5, 0.0))
        _wire(sketch, _importer([], bbox=(FakePoint(-5.0, -0.5, 0.0), _LANDED[1])))
        out = payload(mod.handler(file_path=svg, units="mm"))
        assert out["sketch_extent_before"]["width"] == 10.0
        assert out["sketch_extent"]["width"] == 80.118
        assert "WHOLE sketch" in out["note"] and "sketch_extent_before" in out["note"]
        assert "the imported art's own size" not in out["note"]

    def test_an_empty_sketch_lets_the_extent_claim_the_art_size(self, mod, svg):
        blank = make_sketch("Blank", lines=[])
        install(mod, make_design(sketches=[blank]))
        _wire(blank, _importer([], bbox=_LANDED))
        out = payload(mod.handler(file_path=svg, units="mm"))
        assert out["sketch_extent_before"] is None      # nothing to measure yet
        assert out["sketch_extent"]["width"] == 19.07
        assert "the imported art's own size" in out["note"]
        assert "WHOLE sketch" not in out["note"]

    def test_the_before_extent_is_captured_before_the_import(self, mod, sketch, svg):
        sketch.boundingBox = FakeBoundingBox3D(FakePoint(0.0, 0.0, 0.0), FakePoint(1.0, 1.0, 0.0))
        _wire(sketch, _importer([], bbox=_LANDED))
        out = payload(mod.handler(file_path=svg, units="cm"))
        assert out["sketch_extent_before"]["width"] == 1.0     # the pre-import box, not the after
        assert out["sketch_extent"]["width"] == 1.907

    def test_an_unreadable_bounding_box_publishes_none_not_a_guess(self, mod, sketch, svg):
        _wire(sketch, _importer([]))          # the fake sketch carries no boundingBox
        assert payload(mod.handler(file_path=svg))["sketch_extent"] is None

    def test_the_description_states_the_measured_unit_convention(self, mod):
        # the one fact a caller cannot recover after the call: the file's own size is ignored.
        assert "IGNORED" in mod.TOOL_DESCRIPTION
        assert "1/96 inch" in mod.TOOL_DESCRIPTION and "3.7795" in mod.TOOL_DESCRIPTION

    def test_the_description_states_where_the_art_lands(self, mod):
        # the other half of the same convention: SVG y points DOWN, so art anchored at y=0 hangs
        # into NEGATIVE sketch y. A caller who does not know reads the negative extent as a bug.
        assert "NEGATIVE sketch y" in mod.TOOL_DESCRIPTION
        assert "-height" in mod.TOOL_DESCRIPTION

    def test_the_description_names_the_other_svg_entry_point(self, mod):
        # two tools import SVG; this one is the one with an offset and a scale to pass
        assert "doc_insert_import imports svg as well" in mod.TOOL_DESCRIPTION

    def test_every_declared_output_is_in_the_payload(self, mod, sketch, svg):
        _wire(sketch, _importer([], bbox=_LANDED))
        out = payload(mod.handler(file_path=svg))
        for declared in mod.RETURNS:
            assert declared.assert_present(out) == "", declared.key

    def test_the_declared_postcondition_convicts_an_import_that_added_nothing(self, mod, sketch):
        kind = load_tool("_assert").SketchCurvesChanged()
        before = kind.capture({"sketch_name": "Plate"})
        reason, evidence = kind.verify({"sketch_name": "Plate"}, {}, before)
        assert "the sketch's entities are unchanged" in reason and evidence == {}


class TestSketchResolution:
    def test_a_named_sketch_receives_the_curves(self, mod, two_sketches, svg):
        plate, label = two_sketches
        out = payload(mod.handler(file_path=svg, sketch_name="Plate"))
        assert out["sketch"] == "Plate" and out["curves_added"] == 3
        assert label.sketchCurves.count == 0

    def test_an_unnamed_call_targets_the_most_recent_sketch(self, mod, two_sketches, svg):
        plate, _label = two_sketches
        out = payload(mod.handler(file_path=svg))
        assert out["sketch"] == "Label"
        assert plate.sketchCurves.count == 1

    def test_an_unknown_sketch_name_lists_what_is_available(self, mod, sketch, svg):
        msg = error_message(mod.handler(file_path=svg, sketch_name="Nope"))
        assert "No sketch named 'Nope'" in msg and "Plate" in msg

    def test_a_design_with_no_sketch_points_at_sketch_create(self, mod, svg):
        install(mod, make_design(sketches=[]))
        assert "sketch_create" in error_message(mod.handler(file_path=svg))

    def test_no_active_design_is_a_clean_error(self, mod, sketch, svg, monkeypatch):
        monkeypatch.setattr(mod._common, "design", lambda: None)
        monkeypatch.setattr(mod._inputs._common, "design", lambda: None)
        assert "design" in error_message(mod.handler(file_path=svg)).lower()
