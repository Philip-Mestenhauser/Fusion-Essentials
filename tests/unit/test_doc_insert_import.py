"""Unit tests for ``doc_insert_import.py``: format resolution from the extension, the refusals the
ImportManager API forces (DXF/SVG never go to a new document; f3z has no import path), the file
guard, per-format targeting (component / sketch / new document), and the landing read-back that
turns an import which created nothing into an error.
"""

import types

import pytest

import adsk.fusion
from conftest import (BRepBody, MakeComp, _NamedCollection, error_message, load_tool, make_design,
                      payload)

mod = load_tool("doc_insert_import")

_ABSENT = object()


def _fake_manager(created=(), dxf_results=(), fail=None, options=_ABSENT, new_document=None,
                  on_import=None):
    """A stand-in adsk.core.ImportManager. ``created`` is what importToTarget2 returns (None models
    the null the API returns for a failed import); ``fail`` is an exception the import raises;
    ``on_import`` fires the side effect a real import has on the target. Returns (manager, calls),
    where calls records the arguments each method received."""
    calls = {"option_paths": [], "targets": [], "planar_entity": _ABSENT, "new_documents": 0}
    opts = types.SimpleNamespace(results=_NamedCollection(list(dxf_results)))

    def factory(path, planar_entity=_ABSENT):
        calls["option_paths"].append(path)
        calls["planar_entity"] = planar_entity
        return opts if options is _ABSENT else options

    def import_to_target2(_options, target):
        calls["targets"].append(target)
        if fail is not None:
            raise fail
        if on_import is not None:
            on_import()
        return None if created is None else _NamedCollection(list(created))

    def import_to_new_document(_options):
        calls["new_documents"] += 1
        if fail is not None:
            raise fail
        return new_document

    return types.SimpleNamespace(
        createSTEPImportOptions=factory,
        createIGESImportOptions=factory,
        createSATImportOptions=factory,
        createSMTImportOptions=factory,
        createFusionArchiveImportOptions=factory,
        createDXF2DImportOptions=factory,
        createSVGImportOptions=factory,
        importToTarget2=import_to_target2,
        importToNewDocument=import_to_new_document,
    ), calls


def _new_doc(design, name="Imported v1"):
    """A Document whose Design product is ``design`` - what importToNewDocument returns."""
    products = types.SimpleNamespace(
        itemByProductType=lambda kind: design if kind == "DesignProductType" else None)
    return types.SimpleNamespace(name=name, products=products)


@pytest.fixture
def cad(tmp_path):
    """Factory: a real file on disk with the given name."""
    def _make(name="part.step"):
        path = tmp_path / name
        path.write_text("cad", encoding="utf-8")
        return str(path)
    return _make


@pytest.fixture
def wire(monkeypatch):
    """Factory: wire a design and a stand-in ImportManager into the module, patching BOTH design
    seams (the handler's own _common and the input kinds' _inputs._common)."""
    def _wire(design=None, **manager_kwargs):
        design = make_design() if design is None else design
        manager, calls = _fake_manager(**manager_kwargs)
        monkeypatch.setattr(mod, "app", types.SimpleNamespace(importManager=manager))
        monkeypatch.setattr(mod._common, "design", lambda: design)
        monkeypatch.setattr(mod._inputs._common, "design", lambda: design)
        return design, manager, calls
    return _wire


def _sketch(name="Sketch1", curves=()):
    return types.SimpleNamespace(name=name, sketchCurves=_NamedCollection(list(curves)))


def _occurrence(name="Part:1", component=None):
    return types.SimpleNamespace(name=name, fullPathName=name,
                                 component=component if component is not None else MakeComp(name))


class TestFormatResolution:
    @pytest.mark.parametrize("filename,expected", [
        ("part.step", "step"), ("part.STP", "step"),
        ("part.iges", "iges"), ("part.igs", "iges"),
        ("part.sat", "sat"), ("part.smt", "smt"), ("part.f3d", "f3d"),
    ])
    def test_extension_names_the_format(self, wire, cad, filename, expected):
        wire(created=[BRepBody("Imported")])
        out = payload(mod.handler(file_path=cad(filename)))
        assert out["format"] == expected

    def test_explicit_format_contradicting_the_extension_is_refused(self, wire, cad):
        _design, _mgr, calls = wire(created=[BRepBody("Imported")])
        msg = error_message(mod.handler(file_path=cad("part.step"), format="iges"))
        assert "iges" in msg and ".step" in msg
        assert calls["option_paths"] == []          # refused before any options were built

    def test_explicit_format_agreeing_with_the_extension_passes(self, wire, cad):
        wire(created=[BRepBody("Imported")])
        out = payload(mod.handler(file_path=cad("part.stp"), format="step"))
        assert out["format"] == "step"

    def test_unknown_format_value_is_refused_by_the_enum(self, wire, cad):
        wire(created=[BRepBody("Imported")])
        msg = error_message(mod.handler(file_path=cad("part.step"), format="parasolid"))
        assert "format" in msg and "parasolid" in msg

    def test_unsupported_extension_lists_the_supported_ones(self, wire, cad):
        wire()
        msg = error_message(mod.handler(file_path=cad("part.3dm")))
        assert ".3dm" in msg
        assert ".step" in msg and ".svg" in msg

    def test_f3z_is_refused_pointing_at_the_upload_path(self, wire, cad):
        _design, _mgr, calls = wire()
        msg = error_message(mod.handler(file_path=cad("assembly.f3z")))
        assert ".f3d" in msg and "data_upload_file" in msg
        assert calls["option_paths"] == []


class TestGuards:
    def test_missing_file_path(self, wire):
        wire()
        assert "file_path" in error_message(mod.handler(file_path=""))

    def test_file_not_on_disk_is_named(self, wire, tmp_path):
        wire()
        missing = str(tmp_path / "ghost.step")
        msg = error_message(mod.handler(file_path=missing))
        assert missing in msg

    def test_no_active_design_points_at_the_new_document_path(self, monkeypatch, wire, cad):
        wire()
        monkeypatch.setattr(mod._common, "design", lambda: None)
        msg = error_message(mod.handler(file_path=cad("part.step")))
        assert "No active design" in msg and "new_document" in msg

    def test_missing_import_manager_errors(self, monkeypatch, wire, cad):
        wire()
        monkeypatch.setattr(mod, "app", types.SimpleNamespace(importManager=None))
        assert "importManager" in error_message(mod.handler(file_path=cad("part.step")))

    @pytest.mark.parametrize("filename", ["plate.dxf", "logo.svg"])
    def test_sketch_formats_cannot_go_to_a_new_document(self, wire, cad, filename):
        _design, _mgr, calls = wire()
        msg = error_message(mod.handler(file_path=cad(filename), new_document=True))
        assert "new document" in msg
        assert "new_document=false" in msg
        assert calls["new_documents"] == 0 and calls["option_paths"] == []

    def test_options_factory_returning_nothing_errors(self, wire, cad):
        wire(options=None)
        msg = error_message(mod.handler(file_path=cad("part.step")))
        assert "createSTEPImportOptions" in msg


class TestSolidImport:
    def test_reports_the_objects_and_the_component_growth(self, wire, cad):
        design = make_design()
        room = design.rootComponent
        wire(design=design, created=[BRepBody("Imported")],
             on_import=lambda: room.bRepBodies._items.append(BRepBody("Imported")))
        out = payload(mod.handler(file_path=cad("part.step")))
        assert out["imported"] is True
        assert out["objects_created"] == 1
        assert out["bodies_added"] == 1
        assert out["created"] == [{"name": "Imported", "type": "BRepBody"}]
        assert "Root" in out["into"]

    def test_target_handed_to_the_api_is_the_component(self, wire, cad):
        design, _mgr, calls = wire(created=[BRepBody("Imported")])
        mod.handler(file_path=cad("part.step"))
        assert calls["targets"] == [design.rootComponent]

    def test_an_unreadable_created_object_drops_out_of_the_listing(self, wire, cad):
        # the created objects are counted and named, never addressed by position, so one that will
        # not read is simply absent - the readable ones keep their names and the count matches them
        design = make_design()
        room = design.rootComponent
        wire(design=design, created=[BRepBody("First"), None, BRepBody("Third")],
             on_import=lambda: room.bRepBodies._items.append(BRepBody("First")))
        out = payload(mod.handler(file_path=cad("part.step")))
        assert out["objects_created"] == 2
        assert out["created"] == [{"name": "First", "type": "BRepBody"},
                                  {"name": "Third", "type": "BRepBody"}]

    def test_nothing_landed_is_an_error(self, wire, cad):
        wire(created=[])
        msg = error_message(mod.handler(file_path=cad("part.step")))
        assert "nothing landed" in msg
        assert "no body and no occurrence" in msg

    def test_an_assembly_landing_as_occurrences_counts_as_landed(self, wire, cad):
        design = make_design()
        room = design.rootComponent
        wire(design=design, created=[],
             on_import=lambda: room.occurrences._items.append(_occurrence("Sub:1")))
        out = payload(mod.handler(file_path=cad("part.step")))
        assert out["occurrences_added"] == 1
        assert out["objects_created"] == 0

    def test_null_return_is_reported_as_a_failed_import(self, wire, cad):
        wire(created=None)
        msg = error_message(mod.handler(file_path=cad("part.step")))
        assert "null" in msg and "FAILED" in msg

    def test_a_raising_import_is_an_error_not_a_false_ok(self, wire, cad):
        wire(fail=RuntimeError("bad STEP entity"))
        msg = error_message(mod.handler(file_path=cad("part.step")))
        assert "importToTarget2 raised" in msg and "bad STEP entity" in msg

    def test_into_component_resolves_through_the_occurrence_kind(self, wire, cad):
        sub = MakeComp("Bracket")
        occ = _occurrence("Bracket:1", component=sub)
        design = make_design(occurrences=[occ])
        _design, _mgr, calls = wire(design=design, created=[BRepBody("Imported")])
        out = payload(mod.handler(file_path=cad("part.step"), into_component="Bracket:1"))
        assert calls["targets"] == [sub]
        assert "Bracket" in out["into"]

    def test_unknown_into_component_is_refused_before_importing(self, wire, cad):
        design = make_design(occurrences=[_occurrence("Bracket:1")])
        _design, _mgr, calls = wire(design=design, created=[BRepBody("Imported")])
        msg = error_message(mod.handler(file_path=cad("part.step"), into_component="Ghost"))
        assert "Ghost" in msg
        assert calls["targets"] == []

    def test_an_occurrence_with_no_component_is_refused_before_importing(self, wire, cad):
        occ = _occurrence("Bracket:1")
        occ.component = None
        design = make_design(occurrences=[occ])
        _design, _mgr, calls = wire(design=design, created=[BRepBody("Imported")])
        msg = error_message(mod.handler(file_path=cad("part.step"), into_component="Bracket:1"))
        assert "Occurrence 'Bracket:1' has no component to import into" in msg
        assert calls["targets"] == []

    def test_a_design_with_no_component_to_import_into_is_refused(self, wire, cad, monkeypatch):
        _design, _mgr, calls = wire(created=[BRepBody("Imported")])
        monkeypatch.setattr(mod._common, "target_component", lambda design: None)
        msg = error_message(mod.handler(file_path=cad("part.step")))
        assert "exposes no component to import into" in msg
        assert calls["targets"] == []


class TestTheBuildLacksTheFactory:
    """ImportManager gains factories over Fusion versions - a build without the one this format
    needs is named, never AttributeError'd out of the handler."""

    def test_a_missing_options_factory_is_named_with_the_format(self, wire, cad):
        _design, mgr, calls = wire(created=[BRepBody("Imported")])
        del mgr.createSTEPImportOptions
        msg = error_message(mod.handler(file_path=cad("part.step")))
        assert "no ImportManager.createSTEPImportOptions" in msg and "STEP import is unavailable" in msg
        assert calls["targets"] == []

    def test_a_missing_factory_stops_a_new_document_import_too(self, wire, cad):
        _design, mgr, calls = wire()
        del mgr.createIGESImportOptions
        msg = error_message(mod.handler(file_path=cad("part.iges"), new_document=True))
        assert "no ImportManager.createIGESImportOptions" in msg
        assert calls["new_documents"] == 0


class TestDxfImport:
    def _design_with_plane(self, sketches=()):
        comp = MakeComp("Root", sketches=sketches)
        comp.xYConstructionPlane = types.SimpleNamespace(name="XY")
        return make_design(comp=comp)

    def test_sketches_land_and_are_reported(self, wire, cad):
        design = self._design_with_plane()
        room = design.rootComponent
        wire(design=design, created=[_sketch("Outline")],
             on_import=lambda: room.sketches._items.append(_sketch("Outline")))
        out = payload(mod.handler(file_path=cad("plate.dxf")))
        assert out["format"] == "dxf"
        assert out["sketches_added"] == 1
        assert [c["name"] for c in out["created"]] == ["Outline"]

    def test_the_resolved_plane_is_handed_to_the_options_factory(self, wire, cad):
        design = self._design_with_plane()
        _design, _mgr, calls = wire(design=design, created=[_sketch("Outline")])
        mod.handler(file_path=cad("plate.dxf"))
        assert calls["planar_entity"] is design.rootComponent.xYConstructionPlane

    def test_results_on_the_options_object_is_the_fallback(self, wire, cad):
        design = self._design_with_plane()
        room = design.rootComponent
        wire(design=design, created=[], dxf_results=[_sketch("Layer0")],
             on_import=lambda: room.sketches._items.append(_sketch("Layer0")))
        out = payload(mod.handler(file_path=cad("plate.dxf")))
        assert [c["name"] for c in out["created"]] == ["Layer0"]

    def test_no_sketch_landed_is_an_error(self, wire, cad):
        wire(design=self._design_with_plane(), created=[])
        msg = error_message(mod.handler(file_path=cad("plate.dxf")))
        assert "no sketch landed" in msg
        assert "3D geometry" in msg

    def test_unresolvable_plane_is_refused_before_importing(self, wire, cad):
        design = self._design_with_plane()
        _design, _mgr, calls = wire(design=design, created=[_sketch("Outline")])
        msg = error_message(mod.handler(file_path=cad("plate.dxf"), plane="NoSuchPlane"))
        assert "NoSuchPlane" in msg
        assert calls["targets"] == []

    def test_options_the_factory_would_not_build_are_reported_with_both_causes(self, wire, cad):
        # createDXF2DImportOptions answering nothing means the FILE or the PLANE was rejected -
        # the refusal names both rather than guessing which.
        _design, _mgr, calls = wire(design=self._design_with_plane(), options=None)
        msg = error_message(mod.handler(file_path=cad("plate.dxf")))
        assert "createDXF2DImportOptions returned nothing" in msg
        assert "planar face" in msg
        assert calls["targets"] == []

    def test_a_raising_dxf_import_is_an_error_not_a_false_ok(self, wire, cad):
        wire(design=self._design_with_plane(), fail=RuntimeError("layer table is corrupt"))
        msg = error_message(mod.handler(file_path=cad("plate.dxf")))
        assert "importToTarget2 raised" in msg and "layer table is corrupt" in msg

    def test_a_design_with_no_component_refuses_the_dxf_import(self, wire, cad, monkeypatch):
        _design, _mgr, calls = wire(design=self._design_with_plane(), created=[_sketch("Outline")])
        monkeypatch.setattr(mod._common, "target_component", lambda design: None)
        msg = error_message(mod.handler(file_path=cad("plate.dxf")))
        assert "exposes no component to import into" in msg
        assert calls["targets"] == []


class TestSvgImport:
    def test_curves_land_in_the_named_sketch(self, wire, cad):
        target = _sketch("Logo")
        design = make_design(sketches=[target])
        wire(design=design, created=[types.SimpleNamespace(name="Curve")],
             on_import=lambda: target.sketchCurves._items.append(object()))
        out = payload(mod.handler(file_path=cad("logo.svg"), sketch="Logo"))
        assert out["curves_added"] == 1
        assert out["into"] == "sketch 'Logo'"

    def test_the_sketch_is_the_import_target(self, wire, cad):
        target = _sketch("Logo")
        design = make_design(sketches=[target])
        _design, _mgr, calls = wire(design=design, created=[types.SimpleNamespace(name="Curve")])
        mod.handler(file_path=cad("logo.svg"), sketch="Logo")
        assert calls["targets"] == [target]

    def test_no_sketch_in_the_design_names_sketch_create(self, wire, cad):
        wire(design=make_design())
        msg = error_message(mod.handler(file_path=cad("logo.svg")))
        assert "sketch_create" in msg

    def test_unknown_sketch_name_lists_the_available_ones(self, wire, cad):
        design = make_design(sketches=[_sketch("Logo")])
        wire(design=design)
        msg = error_message(mod.handler(file_path=cad("logo.svg"), sketch="Ghost"))
        assert "Ghost" in msg and "Logo" in msg

    def test_a_sketch_that_gained_no_curve_is_an_error(self, wire, cad):
        design = make_design(sketches=[_sketch("Logo")])
        wire(design=design, created=[])
        msg = error_message(mod.handler(file_path=cad("logo.svg"), sketch="Logo"))
        assert "gained no" in msg and "Logo" in msg

    def test_options_the_factory_would_not_build_are_reported(self, wire, cad):
        design = make_design(sketches=[_sketch("Logo")])
        _design, _mgr, calls = wire(design=design, options=None)
        msg = error_message(mod.handler(file_path=cad("logo.svg"), sketch="Logo"))
        assert "createSVGImportOptions returned nothing" in msg
        assert calls["targets"] == []

    def test_a_raising_svg_import_is_an_error_not_a_false_ok(self, wire, cad):
        target = _sketch("Logo")
        design = make_design(sketches=[target])
        wire(design=design, fail=RuntimeError("path data is malformed"))
        msg = error_message(mod.handler(file_path=cad("logo.svg"), sketch="Logo"))
        assert "importToTarget2 raised" in msg and "path data is malformed" in msg


class TestNewDocument:
    def test_bodies_are_read_back_off_the_new_document(self, wire, cad):
        imported = make_design(bodies=["Imported"])
        wire(new_document=_new_doc(imported, name="Gearbox v1"))
        out = payload(mod.handler(file_path=cad("part.step"), new_document=True))
        assert out["bodies"] == 1
        assert out["into"] == "new document 'Gearbox v1'"

    def test_null_return_is_an_error(self, wire, cad):
        wire(new_document=None)
        msg = error_message(mod.handler(file_path=cad("part.step"), new_document=True))
        assert "null" in msg and "FAILED" in msg

    def test_a_document_with_no_design_product_is_an_error(self, wire, cad):
        wire(new_document=_new_doc(None))
        msg = error_message(mod.handler(file_path=cad("part.step"), new_document=True))
        assert "no Design product" in msg

    def test_an_empty_new_document_is_an_error(self, wire, cad):
        wire(new_document=_new_doc(make_design()))
        msg = error_message(mod.handler(file_path=cad("part.step"), new_document=True))
        assert "no body and no occurrence" in msg
        assert "doc_close" in msg

    def test_a_raising_import_is_an_error(self, wire, cad):
        wire(fail=RuntimeError("unreadable archive"))
        msg = error_message(mod.handler(file_path=cad("part.f3d"), new_document=True))
        assert "importToNewDocument raised" in msg


class TestFeatureHealthAcrossADocumentSwitch:
    """FeatureHealthy captures the ACTIVE design's timeline count before the handler and walks the
    items past it after. new_document=True activates a DIFFERENT document, so the two reads describe
    two different timelines - the walked slice is meaningless and can skip a feature that failed to
    compute. The gate declares itself unrun instead."""

    def test_the_gate_is_declared_unrun_when_the_import_made_a_new_document(self):
        post = mod._FeatureHealthyHere()
        reason, evidence = post.verify({"new_document": True}, {"imported": True}, 0)
        assert reason == ""
        assert evidence["feature_health_verified"] is False
        assert "NEW document" in evidence["feature_health_note"]

    def test_a_same_document_import_still_runs_the_real_gate(self, monkeypatch):
        # The skip may only apply to the new-document path: an import into the OPEN design must
        # still fail on a feature that computed with an error.
        post = mod._FeatureHealthyHere()
        error_state = adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState
        broken = types.SimpleNamespace(name="Imported1", healthState=error_state,
                                       errorOrWarningMessage="bad geometry")
        timeline = types.SimpleNamespace(count=1, item=lambda i: broken)
        monkeypatch.setattr(post, "_timeline", lambda: timeline)
        reason, _evidence = post.verify({"new_document": False}, {"imported": True}, 0)
        assert "FAILED to compute" in reason
        assert "Imported1" in reason

    def test_the_kind_declares_the_input_it_reads(self):
        # wrap() checks input_keys against the handler signature, so a typo here would fail at
        # registration rather than silently reading None and skipping nothing.
        assert mod._FeatureHealthyHere.input_keys == ("new_document",)
