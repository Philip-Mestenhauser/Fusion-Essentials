"""Unit tests for ``design_export.py`` - export a body/component/whole-design to a neutral CAD file.

Covers the format dispatch (step/iges/sat/smt/usd/f3d/stl/3mf/obj), target resolution (handle / body
name / component name / whole design), path defaulting + extension handling, the invisible-content
and per-format option knobs (STL binary/units, DXF construction/points/projected/units), and that the
right ExportManager.create*Options call is used per format. No live Fusion - fakes mimic ExportManager.
"""

import json
from conftest import load_tool

dx = load_tool("design_export")


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeBody:
    def __init__(self, name):
        self.name = name


class FakeBodies:
    def __init__(self, bodies):
        self._b = {b.name: b for b in bodies}
        self._list = bodies
    def itemByName(self, n):
        return self._b.get(n)
    @property
    def count(self):
        return len(self._list)
    def item(self, i):
        return self._list[i]


class FakeOcc:
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name
        # A real Occurrence always answers `component`; a read that RAISES is the
        # unresolved-external-reference signal the shared occurrence census filters on.
        self.component = type("C", (), {"name": name.split(":")[0]})()


class FakeOccs:
    def __init__(self, occs=()):
        self._l = list(occs)
    def itemByName(self, n):
        return None
    @property
    def count(self):
        return len(self._l)
    def item(self, i):
        return self._l[i]
    def __iter__(self):
        return iter(self._l)


class FakeComp:
    def __init__(self, name, bodies, occurrences=()):
        self.name = name
        self.bRepBodies = FakeBodies(bodies)
        self.occurrences = FakeOccs(occurrences)
        self.allOccurrences = list(occurrences)


class FakeOptions:
    """Stands in for a *ExportOptions/DXFSketchExportOptions object: dict-style access for the
    harness's own record-keeping (kind/path/geom - what em.calls[-1]["x"] reads), plus free attribute
    get/set for whatever properties the handler configures (isBinaryFormat, unitType,
    isIncludingInvisibleBodies, isConstructionExported, ...)."""
    def __init__(self, kind, path, geom=None):
        self.kind = kind
        self.path = path
        self.geom = geom
    def __getitem__(self, k):
        return getattr(self, k, None)
    def __setitem__(self, k, v):
        setattr(self, k, v)


class FakeExportManager:
    """Records which create*Options was called + with what geometry/path, and that execute ran."""
    def __init__(self):
        self.calls = []
        self.executed = None
        # A live options object can IGNORE a setter - the property keeps its own value however it
        # is written. Swap this to model that, which is the only way the refusal path is reachable.
        self.options_class = FakeOptions
    def _opt(self, kind, path, geom=None):
        rec = self.options_class(kind, path, geom)
        self.calls.append(rec)
        return rec
    def createSTEPExportOptions(self, path, geom=None):
        return self._opt("step", path, geom)
    def createIGESExportOptions(self, path, geom=None):
        return self._opt("iges", path, geom)
    def createSATExportOptions(self, path, geom=None):
        return self._opt("sat", path, geom)
    def createSMTExportOptions(self, path, geom=None):
        return self._opt("smt", path, geom)
    def createUSDExportOptions(self, path, geom=None):
        return self._opt("usd", path, geom)
    def createFusionArchiveExportOptions(self, path, geom=None):
        return self._opt("f3d", path, geom)
    def createSTLExportOptions(self, geom, path):
        # STL signature is (geometry, filename) in the real API
        return self._opt("stl", path, geom)
    def createC3MFExportOptions(self, geom, path):
        return self._opt("3mf", path, geom)
    def createOBJExportOptions(self, geom, path):
        return self._opt("obj", path, geom)
    def createDXFSketchExportOptions(self, path, sketch):
        # signature is (filename, sketch), live-verified - (sketch, filename) raises TypeError
        opts = self._opt("dxf", path, sketch)
        # reading DXFSketchExportOptions.units aborts the live transaction - the fake raises so any
        # regression that touches .units (even via safe()) is visible in a test run
        cls = type(opts)
        if not hasattr(cls, "units"):
            def _units_boom(self_):
                raise RuntimeError("Distance unit is not supported by DXF")
            cls.units = property(_units_boom)
        return opts
    def execute(self, opts):
        self.executed = opts
        # actually write a stub file so the handler's os.path.getsize/exists checks see it
        try:
            with open(opts["path"], "w") as f:
                f.write("stub")
        except Exception:
            pass
        return True


class FakeDesign:
    def __init__(self, comp, em):
        self.rootComponent = comp
        self.exportManager = em
        self._tokens = {}
    def findEntityByToken(self, t):
        e = self._tokens.get(t)
        return [e] if e is not None else []


def _install(monkeypatch, bodies=None, comp_name="Root", occurrences=()):
    bodies = bodies if bodies is not None else [FakeBody("Body1")]
    comp = FakeComp(comp_name, bodies, occurrences)
    em = FakeExportManager()
    design = FakeDesign(comp, em)
    app = type("A", (), {"activeProduct": design})()
    monkeypatch.setattr(dx, "app", app)
    monkeypatch.setattr(dx._common, "app", app)
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, FakeDesign) else None)
    monkeypatch.setattr(adsk.fusion, "BRepBody", FakeBody)
    return design, em, comp


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── format dispatch ──────────────────────────────────────────────────────────

class TestFormatDispatch:
    def test_step_uses_step_options(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="step", file_path=str(tmp_path / "p.step")))
        assert out["exported"] is True
        assert em.calls[-1]["kind"] == "step"
        assert em.executed is not None

    def test_iges_uses_iges_options(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        _payload(dx.handler(format="iges", file_path=str(tmp_path / "p.igs")))
        assert em.calls[-1]["kind"] == "iges"

    def test_sat_uses_sat_options(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        _payload(dx.handler(format="sat", file_path=str(tmp_path / "p.sat")))
        assert em.calls[-1]["kind"] == "sat"

    def test_stl_uses_stl_options(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        _payload(dx.handler(format="stl", file_path=str(tmp_path / "p.stl")))
        assert em.calls[-1]["kind"] == "stl"

    def test_smt_uses_smt_options(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="smt", file_path=str(tmp_path / "p.smt")))
        assert out["exported"] is True
        assert em.calls[-1]["kind"] == "smt"

    def test_usd_uses_usd_options(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="usd", file_path=str(tmp_path / "p.usdz")))
        assert out["exported"] is True
        assert em.calls[-1]["kind"] == "usd"

    def test_f3d_uses_fusion_archive_options(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="f3d", file_path=str(tmp_path / "p.f3d")))
        assert out["exported"] is True
        assert em.calls[-1]["kind"] == "f3d"

    def test_3mf_uses_c3mf_options_geom_first(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="3mf", file_path=str(tmp_path / "p.3mf")))
        assert out["exported"] is True
        assert em.calls[-1]["kind"] == "3mf"
        # 3MF is a mesh-style format - geometry-first arg order like STL
        assert em.calls[-1]["geom"] is not None

    def test_obj_uses_obj_options_geom_first(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="obj", file_path=str(tmp_path / "p.obj")))
        assert out["exported"] is True
        assert em.calls[-1]["kind"] == "obj"

    def test_unknown_format_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        res = dx.handler(format="dwg", file_path=str(tmp_path / "p.dwg"))
        assert res["isError"] is True and "format" in res["message"]


# ── target resolution ────────────────────────────────────────────────────────

class TestTargetResolution:
    def test_whole_design_when_no_target(self, tmp_path, monkeypatch):
        _, em, comp = _install(monkeypatch)
        out = _payload(dx.handler(format="step", file_path=str(tmp_path / "p.step")))
        # whole-design export passes the root component as the geometry
        assert em.calls[-1]["geom"] is comp
        assert "design" in out["target"].lower() or "root" in out["target"].lower()

    def test_body_by_name(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch, bodies=[FakeBody("Widget")])
        out = _payload(dx.handler(format="step", target="Widget", file_path=str(tmp_path / "p.step")))
        assert em.calls[-1]["geom"].name == "Widget"
        assert "Widget" in out["target"]

    def test_body_by_handle(self, tmp_path, monkeypatch):
        design, em, _ = _install(monkeypatch, bodies=[FakeBody("Body1")])
        h = "/v" + "X" * 70
        design._tokens[h] = FakeBody("FromHandle")
        out = _payload(dx.handler(format="step", target=h, file_path=str(tmp_path / "p.step")))
        assert em.calls[-1]["geom"].name == "FromHandle"

    def test_long_body_name_not_mistaken_for_handle(self, tmp_path, monkeypatch):
        # A long body NAME is not a handle: _resolve_token_entity returns None for a non-token, so
        # resolution falls through to the name lookup and a long body name exports by NAME.
        long_name = "Left-Outrigger-Pivot-Bracket-Weldment-Subassembly-Body-Number-Seven"
        assert len(long_name) > 60
        _, em, _ = _install(monkeypatch, bodies=[FakeBody(long_name)])
        out = _payload(dx.handler(format="step", target=long_name, file_path=str(tmp_path / "p.step")))
        assert em.calls[-1]["geom"].name == long_name
        assert long_name in out["target"]

    def test_occurrence_by_name(self, tmp_path, monkeypatch):
        # occurrence resolution goes through the shared _resolve_occurrence (exact name/fullPathName)
        occ = FakeOcc("Gear:1")
        _, em, _ = _install(monkeypatch, occurrences=[occ])
        out = _payload(dx.handler(format="step", target="Gear:1", file_path=str(tmp_path / "p.step")))
        assert em.calls[-1]["geom"] is occ
        assert "Gear:1" in out["target"]

    def test_missing_named_target_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch, bodies=[FakeBody("Body1")])
        res = dx.handler(format="step", target="Nope", file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True and "Nope" in res["message"]

    def test_ambiguous_name_refused_not_first_instance(self, tmp_path, monkeypatch):
        # two instances share the local name "Bolt:1" under different sub-assemblies - the real
        # shared resolver (_inputs._resolve_occurrence) must REFUSE the bare substring, naming both
        # fullPathNames, never export the first (wrong) instance.
        _, em, _ = _install(monkeypatch, occurrences=[FakeOcc("Bolt:1", "Sub-A:1+Bolt:1"),
                                                      FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")])
        res = dx.handler(format="step", target="Bolt", file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Sub-A:1+Bolt:1" in res["message"] and "Sub-B:1+Bolt:1" in res["message"]
        assert em.executed is None                        # nothing was exported

    def test_an_EXACT_shared_name_refusal_keeps_its_candidate_list(self, tmp_path, monkeypatch):
        # the shared-EXACT-name refusal carries no "ambiguous" wording - only the OCCURRENCE_MISS
        # stem tells a plain miss from a refusal - so its candidate list must reach the caller
        # instead of degrading to a generic not-found.
        _, em, _ = _install(monkeypatch, occurrences=[FakeOcc("Bolt:1", "Sub-A:1+Bolt:1"),
                                                      FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")])
        res = dx.handler(format="step", target="Bolt:1", file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True
        assert "Sub-A:1+Bolt:1" in res["message"] and "Sub-B:1+Bolt:1" in res["message"]
        assert em.executed is None                        # nothing was exported


# ── path handling ────────────────────────────────────────────────────────────

class TestPathHandling:
    def test_missing_path_errors(self, monkeypatch):
        _install(monkeypatch)
        res = dx.handler(format="step")
        assert res["isError"] is True and "file_path" in res["message"]

    def test_extension_auto_appended(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        p = str(tmp_path / "noext")
        out = _payload(dx.handler(format="step", file_path=p))
        # the path handed to the exporter ends with the format extension
        assert em.calls[-1]["path"].lower().endswith(".step")
        assert out["file_path"].lower().endswith(".step")


# ── invisible-content + per-format option knobs ─────────────────────────────

class TestOptionsApplied:
    def test_an_option_the_api_refuses_is_named_not_reported_as_applied(self, tmp_path, monkeypatch):
        # A knob the platform ignores must NOT appear under options_applied: that key states the
        # value the file was written with, so a refused option listed there is a false claim.
        _, em, _ = _install(monkeypatch)

        class Stubborn(FakeOptions):
            @property
            def isBinaryFormat(self):
                return True          # always binary, whatever is written
            @isBinaryFormat.setter
            def isBinaryFormat(self, value):
                pass

        em.options_class = Stubborn
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path / "p.stl"),
                                  stl_binary=False))
        assert out.get("options_applied", {}).get("stl_binary") is None
        assert "stl_binary" in out["options_refused"]
        assert "did not take these options" in out["note"]

    def test_default_call_sets_no_extra_options(self, tmp_path, monkeypatch):
        # the common case (no opt-in flags) must keep the plain export payload shape - no
        # 'options_applied' key when nothing was requested.
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="step", file_path=str(tmp_path / "p.step")))
        assert "options_applied" not in out
        opts = em.calls[-1]
        assert not hasattr(opts, "isIncludingInvisibleBodies")
        assert not hasattr(opts, "isBinaryFormat")

    def test_include_invisible_bodies_and_components(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="step", file_path=str(tmp_path / "p.step"),
                                   include_invisible_bodies=True, include_invisible_components=True))
        opts = em.calls[-1]
        assert opts.isIncludingInvisibleBodies is True
        assert opts.isIncludingInvisibleComponents is True
        assert out["options_applied"]["invisible_bodies"] is True
        assert out["options_applied"]["invisible_components"] is True

    def test_invisible_flags_ignored_when_false(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        dx.handler(format="step", file_path=str(tmp_path / "p.step"),
                  include_invisible_bodies=False, include_invisible_components=False)
        opts = em.calls[-1]
        assert not hasattr(opts, "isIncludingInvisibleBodies")

    def test_stl_binary_true(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path / "p.stl"), stl_binary=True))
        assert em.calls[-1].isBinaryFormat is True
        assert out["options_applied"]["stl_binary"] is True

    def test_stl_binary_false_ascii(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path / "p.stl"), stl_binary=False))
        assert em.calls[-1].isBinaryFormat is False
        # False here states the FILE IS ASCII - options_applied carries the value that landed,
        # never a did-it-stick flag, which under this key would read as the value it is not
        assert out["options_applied"]["stl_binary"] is False

    def test_stl_binary_omitted_leaves_factory_default_untouched(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        dx.handler(format="stl", file_path=str(tmp_path / "p.stl"))
        assert not hasattr(em.calls[-1], "isBinaryFormat")

    def test_stl_units_applied_via_distance_units_enum(self, tmp_path, monkeypatch):
        # unitType takes DistanceUnits, live-verified; MeshUnits has mm/cm SWAPPED relative to it,
        # so pinning the enum family here is what catches a silent 10x-wrong-geometry regression.
        _, em, _ = _install(monkeypatch)
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path / "p.stl"), stl_units="in"))
        assert em.calls[-1].unitType is dx.adsk.fusion.DistanceUnits.InchDistanceUnits
        # the VALUE that landed, in the tool's own vocabulary - not a did-it-stick flag, which
        # under this key would read as the value it is not
        assert out["options_applied"]["stl_units"] == "in"

    def test_stl_units_omitted_leaves_factory_default_untouched(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        dx.handler(format="stl", file_path=str(tmp_path / "p.stl"))
        assert not hasattr(em.calls[-1], "unitType")

    def test_stl_units_bad_value_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        res = dx.handler(format="stl", file_path=str(tmp_path / "p.stl"), stl_units="parsecs")
        assert res["isError"] is True and "stl_units" in res["message"]

    def test_stl_options_ignored_for_other_formats(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        dx.handler(format="step", file_path=str(tmp_path / "p.step"), stl_binary=True, stl_units="mm")
        opts = em.calls[-1]
        assert not hasattr(opts, "isBinaryFormat")
        assert not hasattr(opts, "unitType")


# ── format=dxf (sketch / face-profile 2D export) ──────────────────────────────

class FakeCount:
    def __init__(self, n=0):
        self.count = n


class FakeSketchCurves:
    def __init__(self, lines=0, arcs=0, circles=0):
        self.sketchLines = FakeCount(lines)
        self.sketchArcs = FakeCount(arcs)
        self.sketchCircles = FakeCount(circles)


class FakeSketch:
    """Stands in for a Sketch: deleteMe records that the scratch sketch was removed, project2 grows
    the line count by 'project_adds'. DXF writing itself goes through the design's exportManager
    (createDXFSketchExportOptions), not a method on the sketch."""
    def __init__(self, name="Sketch1", lines=0, arcs=0, circles=0, points=0, project_adds=1):
        self.name = name
        self.sketchCurves = FakeSketchCurves(lines, arcs, circles)
        self.sketchPoints = FakeCount(points)
        self._project_adds = project_adds
        self.deleted = False
        self.project_calls = []

    def deleteMe(self):
        self.deleted = True
        return True

    def project2(self, entities, is_linked):
        self.project_calls.append((entities, is_linked))
        self.sketchCurves.sketchLines.count += self._project_adds
        return [object()] * self._project_adds


class FakeSketchesColl:
    def __init__(self, sketch):
        self._sketch = sketch
        self.added_with = None

    def add(self, face):
        self.added_with = face
        return self._sketch


class FakeFaceComp:
    def __init__(self, sketch):
        self.sketches = FakeSketchesColl(sketch)


class FakeFaceBody:
    def __init__(self, comp):
        self.parentComponent = comp


class FakeFace:
    def __init__(self, comp):
        self.body = FakeFaceBody(comp)


class TestDxfExport:
    def test_sketch_happy_path(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        sk = FakeSketch(lines=2)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (sk, None))
        out = _payload(dx.handler(format="dxf", dxf_sketch="Profile1",
                                   file_path=str(tmp_path / "p")))
        assert out["exported"] is True
        assert out["format"] == "dxf"
        assert out["file_path"].lower().endswith(".dxf")
        assert em.calls[-1]["kind"] == "dxf"
        assert em.calls[-1]["geom"] is sk
        assert em.calls[-1]["path"] == out["file_path"]

    def test_sketch_export_defaults_include_everything(self, tmp_path, monkeypatch):
        # Sketch.saveAsDXF takes no filter options at all (unfiltered output); the options-based
        # export must reproduce that by defaulting all three content flags to True.
        _, em, _ = _install(monkeypatch)
        sk = FakeSketch(lines=2)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (sk, None))
        _payload(dx.handler(format="dxf", dxf_sketch="Profile1", file_path=str(tmp_path / "p.dxf")))
        opts = em.calls[-1]
        assert opts.isConstructionExported is True
        assert opts.isPointsExported is True
        assert opts.isProjectedGeometryExported is True

    def test_sketch_export_flags_can_be_narrowed(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        sk = FakeSketch(lines=2)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (sk, None))
        _payload(dx.handler(format="dxf", dxf_sketch="Profile1", file_path=str(tmp_path / "p.dxf"),
                            dxf_export_construction=False, dxf_export_points=False))
        opts = em.calls[-1]
        assert opts.isConstructionExported is False
        assert opts.isPointsExported is False
        assert opts.isProjectedGeometryExported is True   # left at its default (True)

    def test_dxf_never_touches_the_units_property(self, tmp_path, monkeypatch):
        # DXFSketchExportOptions.units is a poison property (reading it aborts the live
        # transaction) - the fake's .units getter raises, so this export succeeding proves the
        # handler leaves it alone entirely. There is deliberately no dxf_units input.
        _, em, _ = _install(monkeypatch)
        sk = FakeSketch(lines=1)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (sk, None))
        out = _payload(dx.handler(format="dxf", dxf_sketch="Profile1",
                                  file_path=str(tmp_path / "p.dxf")))
        assert out["exported"] is True

    def test_missing_sketch_and_face_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        res = dx.handler(format="dxf", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert "dxf_sketch" in res["message"] and "dxf_face" in res["message"]

    def test_both_sketch_and_face_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        res = dx.handler(format="dxf", dxf_sketch="Profile1", dxf_face="H" * 40,
                          file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True and "only one" in res["message"].lower()

    def test_empty_sketch_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        sk = FakeSketch(lines=0, arcs=0, circles=0, points=0)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (sk, None))
        res = dx.handler(format="dxf", dxf_sketch="Empty", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True and "empty" in res["message"].lower()

    def test_sketch_not_found_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (None, None))
        monkeypatch.setattr(dx._common, "all_sketch_names", lambda design: ["Sketch1"])
        res = dx.handler(format="dxf", dxf_sketch="Nope", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True and "Nope" in res["message"]

    def test_a_shared_sketch_name_is_refused_with_its_owners_not_called_missing(
            self, tmp_path, monkeypatch):
        # Two components each holding a 'Profile1' is a REFUSAL naming both, never "No sketch
        # named 'Profile1'" - that sentence states the opposite of what the walk read.
        _install(monkeypatch)
        refusal = "2 sketches are named 'Profile1' ('Profile1' in Root, 'Profile1' in Frame)"
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (None, refusal))
        res = dx.handler(format="dxf", dxf_sketch="Profile1", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert res["message"] == refusal
        assert "No sketch named" not in res["message"]

    def test_execute_false_is_reported_as_failure(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        sk = FakeSketch(lines=1)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (sk, None))
        em.execute = lambda opts: False
        res = dx.handler(format="dxf", dxf_sketch="Profile1", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert "nothing was written" in res["message"].lower()

    def test_face_happy_path_cleans_up_scratch_sketch(self, tmp_path, monkeypatch):
        _, em, _ = _install(monkeypatch)
        sk = FakeSketch(lines=0, project_adds=3)
        comp = FakeFaceComp(sk)
        face = FakeFace(comp)
        monkeypatch.setattr(dx._DXF_FACE, "resolve", lambda raw: (face, None))
        out = _payload(dx.handler(format="dxf", dxf_face="H" * 40,
                                   file_path=str(tmp_path / "p.dxf")))
        assert out["exported"] is True
        assert comp.sketches.added_with is face
        assert sk.deleted is True
        assert "removed" in out["note"].lower()
        # the face path's entire content is projected geometry - the flag must default True
        assert em.calls[-1].isProjectedGeometryExported is True

    def test_face_no_geometry_errors_and_cleans_up(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        sk = FakeSketch(lines=0, project_adds=0)
        comp = FakeFaceComp(sk)
        face = FakeFace(comp)
        monkeypatch.setattr(dx._DXF_FACE, "resolve", lambda raw: (face, None))
        res = dx.handler(format="dxf", dxf_face="H" * 40, file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert "nothing to write" in res["message"].lower()
        assert sk.deleted is True

    def test_extension_auto_appended(self, tmp_path, monkeypatch):
        _install(monkeypatch)
        sk = FakeSketch(lines=1)
        monkeypatch.setattr(dx._common, "find_sketch", lambda design, name: (sk, None))
        out = _payload(dx.handler(format="dxf", dxf_sketch="Profile1",
                                   file_path=str(tmp_path / "noext")))
        assert out["file_path"].lower().endswith(".dxf")


class TestDxfWriterGuards:
    """Every read _write_dxf needs before it can write is guarded and NAMED - an absent
    exportManager, a build without the DXF factory, and a factory that raises."""

    def _sketch_export(self, tmp_path, monkeypatch, sketch=None):
        design, em, _ = _install(monkeypatch)
        monkeypatch.setattr(dx._common, "find_sketch",
                            lambda d, name: (sketch or FakeSketch(lines=1), None))
        return design, em

    def test_a_design_with_no_export_manager_is_named(self, tmp_path, monkeypatch):
        design, _em = self._sketch_export(tmp_path, monkeypatch)
        design.exportManager = None
        res = dx.handler(format="dxf", dxf_sketch="Profile1", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert "exposes no exportManager" in res["message"]

    def test_a_build_without_the_dxf_factory_is_named(self, tmp_path, monkeypatch):
        _design, em = self._sketch_export(tmp_path, monkeypatch)
        # absent, as on a build that never had it - not None-valued
        monkeypatch.delattr(FakeExportManager, "createDXFSketchExportOptions")
        res = dx.handler(format="dxf", dxf_sketch="Profile1", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert "no createDXFSketchExportOptions" in res["message"]
        assert em.executed is None

    def test_a_factory_that_raises_reports_its_reason(self, tmp_path, monkeypatch):
        _design, em = self._sketch_export(tmp_path, monkeypatch)

        def boom(path, sketch):
            raise RuntimeError("the sketch is not exportable")

        em.createDXFSketchExportOptions = boom
        res = dx.handler(format="dxf", dxf_sketch="Profile1", file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert "Could not create DXF export options" in res["message"]
        assert "not exportable" in res["message"]

    def test_a_failed_write_whose_scratch_sketch_also_survives_names_both(self, tmp_path,
                                                                         monkeypatch):
        # the compensating delete is best-effort: if BOTH the write and the cleanup fail, the
        # caller is told the sketch is still in their design, not just that the export failed.
        _design, em, _ = _install(monkeypatch)
        sk = FakeSketch(name="Scratch7", lines=0, project_adds=2)
        sk.deleteMe = lambda: False
        face = FakeFace(FakeFaceComp(sk))
        monkeypatch.setattr(dx._DXF_FACE, "resolve", lambda raw: (face, None))
        em.execute = lambda opts: False
        res = dx.handler(format="dxf", dxf_face="H" * 40, file_path=str(tmp_path / "p.dxf"))
        assert res["isError"] is True
        assert "nothing was written" in res["message"].lower()
        assert "Scratch7" in res["message"] and "delete it manually" in res["message"]

    def test_a_written_dxf_whose_scratch_sketch_survives_says_so_in_the_note(self, tmp_path,
                                                                            monkeypatch):
        _install(monkeypatch)
        sk = FakeSketch(name="Scratch7", lines=0, project_adds=2)
        sk.deleteMe = lambda: False
        face = FakeFace(FakeFaceComp(sk))
        monkeypatch.setattr(dx._DXF_FACE, "resolve", lambda raw: (face, None))
        out = _payload(dx.handler(format="dxf", dxf_face="H" * 40,
                                  file_path=str(tmp_path / "p.dxf")))
        assert out["exported"] is True
        assert "Scratch7" in out["note"] and "could not be removed" in out["note"]
        assert "the design is unchanged" not in out["note"]


# ── split_by_component (one file per top-level occurrence) ─────────────────────

class TestSplitByComponent:
    def test_one_file_per_occurrence(self, tmp_path, monkeypatch):
        occs = [FakeOcc("Body:1"), FakeOcc("Cab:1"), FakeOcc("Wheels:1")]
        _, em, _ = _install(monkeypatch, occurrences=occs)
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert out["split_by_component"] is True
        assert out["file_count"] == 3
        # each occurrence was the geometry handed to the exporter (one execute per part)
        geoms = [c["geom"].name for c in em.calls]
        assert set(geoms) == {"Body:1", "Cab:1", "Wheels:1"}

    def test_split_publishes_the_option_knobs_PER_FILE(self, tmp_path, monkeypatch):
        # The split path configures its own options object PER FILE, so the read-back is per file
        # too - the same applied/requested shape mesh_export publishes. One file's read-back
        # standing in for the rest would report a knob as landed on a file that never read it back.
        occs = [FakeOcc("Body:1"), FakeOcc("Cab:1")]
        _, _em, _ = _install(monkeypatch, occurrences=occs)
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True,
                                  stl_binary=True))
        assert out["file_count"] == 2
        assert out["options_requested"] == {"stl_binary": True}
        assert [f["options_applied"] for f in out["files"]] == [{"stl_binary": True},
                                                                {"stl_binary": True}]
        # the first-file-representative key and its consistency flag are gone with the shape
        assert "options_applied" not in out and "options_applied_consistent" not in out

    def test_a_knob_that_did_not_read_back_is_null_on_that_files_record(self, tmp_path, monkeypatch):
        # An option Fusion ignored must show as null on the FILE it did not land on - an export
        # that silently wrote ASCII while 'stl_binary' was asked for is the false success this
        # catches, and 'options_requested' keeps the request readable beside it.
        occs = [FakeOcc("Body:1"), FakeOcc("Cab:1")]
        _, em, _ = _install(monkeypatch, occurrences=occs)

        class Stubborn(FakeOptions):
            @property
            def isBinaryFormat(self):
                return True
            @isBinaryFormat.setter
            def isBinaryFormat(self, value):
                pass

        em.options_class = Stubborn
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True,
                                  stl_binary=False))
        assert out["options_requested"] == {"stl_binary": False}
        assert [f["options_applied"] for f in out["files"]] == [{"stl_binary": None},
                                                                {"stl_binary": None}]
        assert "stl_binary did NOT land for 2 of the 2 exported file(s)" in out["note"]
        assert "options_refused" not in out

    def test_one_file_refusing_leaves_the_other_files_read_back_intact(self, tmp_path, monkeypatch):
        # The per-file shape exists for exactly this case: a knob that lands on one file and not on
        # another. A single representative value would report ONE of the two states for both.
        occs = [FakeOcc("Body:1"), FakeOcc("Cab:1")]
        _, em, _ = _install(monkeypatch, occurrences=occs)

        class Stubborn(FakeOptions):
            @property
            def isBinaryFormat(self):
                return False
            @isBinaryFormat.setter
            def isBinaryFormat(self, value):
                pass

        made = {"n": 0}

        def _opt(kind, path, geom=None):
            made["n"] += 1
            cls = Stubborn if made["n"] == 2 else FakeOptions   # the SECOND file ignores the set
            rec = cls(kind, path, geom)
            em.calls.append(rec)
            return rec

        monkeypatch.setattr(em, "_opt", _opt)
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path),
                                  split_by_component=True, stl_binary=True))
        assert [f["options_applied"] for f in out["files"]] == [{"stl_binary": True},
                                                                {"stl_binary": None}]
        assert "stl_binary did NOT land for 1 of the 2 exported file(s)" in out["note"]

    def test_no_option_requested_publishes_neither_key(self, tmp_path, monkeypatch):
        # Nothing was asked for, so there is nothing to state - an empty applied/requested pair on
        # every file record would be noise a caller has to read past.
        _install(monkeypatch, occurrences=[FakeOcc("Body:1")])
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert "options_requested" not in out
        assert "options_applied" not in out["files"][0]
        assert "did NOT land" not in out["note"]

    def test_filenames_sanitized_and_extensioned(self, tmp_path, monkeypatch):
        _, _, _ = _install(monkeypatch, occurrences=[FakeOcc("Loader Arm:1")])
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        fp = out["files"][0]["file_path"]
        # ':1' instance suffix dropped, space -> '_', extension applied
        assert fp.replace("\\", "/").endswith("/Loader_Arm.stl")

    def test_duplicate_stems_disambiguated(self, tmp_path, monkeypatch):
        # two instances whose sanitized stem collides must not overwrite each other
        _install(monkeypatch, occurrences=[FakeOcc("Wheel:1"), FakeOcc("Wheel:2")])
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        paths = [f["file_path"] for f in out["files"]]
        assert len(set(paths)) == 2                       # distinct files
        assert any(p.endswith("Wheel.stl") for p in paths)
        assert any(p.endswith("Wheel_2.stl") for p in paths)

    def test_no_occurrences_errors(self, tmp_path, monkeypatch):
        _install(monkeypatch, occurrences=[])
        res = dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True)
        assert res["isError"] is True and "no top-level occurrences" in res["message"].lower()

    def test_an_unreadable_occurrence_collection_refuses_as_unread_not_as_empty(
            self, tmp_path, monkeypatch):
        # The census never happened, so the design's components are unknown. Reporting "no
        # top-level occurrences" would state a fact about the design that was never read, and
        # writing zero files would look like a clean export of nothing.
        _design, _em, comp = _install(monkeypatch, occurrences=[FakeOcc("Body:1")])

        class _Blind:
            @property
            def count(self):
                raise RuntimeError("boom")
            def item(self, i):
                raise RuntimeError("boom")

        comp.occurrences = _Blind()
        res = dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True)
        assert res["isError"] is True
        assert "did not read" in res["message"]
        assert "no top-level occurrences" not in res["message"].lower()
        assert list(tmp_path.iterdir()) == []               # and nothing was written

    def test_partial_failure_records_failed_list(self, tmp_path, monkeypatch):
        # one occurrence exports, one fails -> exported=true, file_count counts only the good one,
        # and the failures land under a 'failed' key (not silently dropped).
        good = FakeOcc("Good:1")
        bad = FakeOcc("Bad:1")
        _, em, _ = _install(monkeypatch, occurrences=[good, bad])

        real_exec = em.execute
        def selective(opts):
            if "Bad" in opts["path"]:
                return False               # Fusion declines this one
            return real_exec(opts)
        em.execute = selective

        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert out["exported"] is True          # at least one succeeded
        assert out["file_count"] == 1
        assert [f["occurrence"] for f in out["files"]] == ["Good:1"]
        assert "failed" in out
        assert out["failed"][0]["occurrence"] == "Bad:1"
        # the shortfall is flagged AND worded - file_count alone reads like a complete export
        assert out["partial"] is True
        assert "PARTIAL" in out["note"] and "1 of 2" in out["note"]

    def test_a_split_that_lands_nothing_is_an_error_carrying_the_reasons(self, tmp_path,
                                                                        monkeypatch):
        # ZERO deliverables is a FAILED export, not an ok payload carrying exported:false - and the
        # refusal names every occurrence that failed, the only place those reasons can travel.
        _, em, _ = _install(monkeypatch, occurrences=[FakeOcc("A:1"), FakeOcc("B:1")])
        em.execute = lambda opts: False
        res = dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True)
        assert res["isError"] is True
        assert "wrote NO files" in res["message"]
        assert "A:1" in res["message"] and "B:1" in res["message"]
        assert "nothing was written" in res["message"]

    def test_execute_true_but_no_file_written_is_a_split_failure(self, tmp_path, monkeypatch):
        # execute() lying (True, but nothing landed on disk) must land the occurrence in 'failed',
        # not 'files' - the split path is gated on file existence the same as the single-target path.
        _, em, _ = _install(monkeypatch, occurrences=[FakeOcc("Good:1"), FakeOcc("Ghost:1")])
        real_exec = em.execute

        def lying_execute(opts):
            if "Ghost" in opts["path"]:
                em.executed = opts
                return True   # lies: writes nothing
            return real_exec(opts)

        em.execute = lying_execute
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert out["file_count"] == 1
        assert [f["occurrence"] for f in out["files"]] == ["Good:1"]
        assert out["partial"] is True
        assert out["failed"][0]["occurrence"] == "Ghost:1"
        assert "no file was written" in out["failed"][0]["error"].lower()


# ── _export_one (per-file write result) ───────────────────────────────────────

class TestExportOne:
    def test_stl_arg_order_is_geom_then_path(self, tmp_path):
        em = FakeExportManager()
        out = str(tmp_path / "out.stl")
        okk, err, applied = dx._export_one(em, "createSTLExportOptions", True, "GEOM", out)
        assert okk is True and err is None and applied == ({}, [])
        # STL records (geom, path); the call captured the geometry, not the path, as geom
        assert em.calls[-1]["geom"] == "GEOM" and em.calls[-1]["path"] == out

    def test_non_stl_arg_order_is_path_then_geom(self, tmp_path):
        em = FakeExportManager()
        out = str(tmp_path / "out.step")
        dx._export_one(em, "createSTEPExportOptions", False, "GEOM", out)
        assert em.calls[-1]["geom"] == "GEOM" and em.calls[-1]["path"] == out

    def test_execute_false_is_a_failure(self, tmp_path):
        em = FakeExportManager()
        em.execute = lambda opts: False
        okk, err, applied = dx._export_one(em, "createSTEPExportOptions", False, "G",
                                           str(tmp_path / "out"))
        assert okk is False and "nothing was written" in err and applied == ({}, [])

    def test_exception_captured_as_error_string(self, tmp_path):
        em = FakeExportManager()
        def boom(path, geom=None):
            raise RuntimeError("disk full")
        em.createSTEPExportOptions = boom
        okk, err, applied = dx._export_one(em, "createSTEPExportOptions", False, "G",
                                           str(tmp_path / "out"))
        assert okk is False and "disk full" in err

    def test_configure_callback_runs_before_execute(self, tmp_path):
        em = FakeExportManager()
        seen = {}
        def configure(opts):
            seen["kind"] = opts.kind
            opts.customFlag = True
            return {"custom": True}
        okk, err, applied = dx._export_one(em, "createSTEPExportOptions", False, "G",
                                           str(tmp_path / "out"), configure)
        assert okk is True
        assert seen["kind"] == "step"
        assert em.calls[-1].customFlag is True
        assert applied == {"custom": True}

    def test_configure_never_blocks_a_failed_execute(self, tmp_path):
        # a decorative-option configure step must not stop the real failure from being reported.
        em = FakeExportManager()
        em.execute = lambda opts: False
        okk, err, applied = dx._export_one(em, "createSTEPExportOptions", False, "G",
                                           str(tmp_path / "out"),
                                           lambda opts: {"x": True})
        assert okk is False and "nothing was written" in err


# ── file-existence gate (single-target export) ────────────────────────────────

class TestFileExistenceGate:
    def test_execute_true_but_no_file_written_is_a_failure(self, tmp_path, monkeypatch):
        # execute() returning true is NOT proof a file landed on disk - success is gated on
        # os.path.isfile + a non-zero size.
        _, em, _ = _install(monkeypatch)

        def lying_execute(opts):
            em.executed = opts
            return True   # lies: writes nothing

        em.execute = lying_execute
        res = dx.handler(format="step", file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True
        assert "no file was written" in res["message"].lower()

    def test_empty_file_is_also_a_failure(self, tmp_path, monkeypatch):
        # a zero-byte file on disk is not a real export either.
        _, em, _ = _install(monkeypatch)
        target = str(tmp_path / "p.step")

        def empty_execute(opts):
            em.executed = opts
            open(opts["path"], "w").close()   # writes an empty file
            return True

        em.execute = empty_execute
        res = dx.handler(format="step", file_path=target)
        assert res["isError"] is True
        assert "no file was written" in res["message"].lower()

    def test_a_stale_file_from_an_earlier_export_is_not_a_landing(self, tmp_path, monkeypatch):
        # the strongest form of the lie: execute() returns true, writes nothing, and a file of the
        # right name is ALREADY there - an existence-only check reports that old file as this
        # export's deliverable.
        _, em, _ = _install(monkeypatch)
        target = tmp_path / "p.step"
        target.write_text("ISO-10303-21; an export from an earlier call")
        em.execute = lambda opts: True
        res = dx.handler(format="step", file_path=str(target))
        assert res["isError"] is True
        assert "already there before this call" in res["message"]


# ── _resolve_target ordering ──────────────────────────────────────────────────

class TestResolveTargetExtra:
    def test_handle_resolving_to_non_body_is_not_found(self, tmp_path, monkeypatch):
        # a long token that resolves to something that is NOT a BRepBody -> (None) -> handler error
        design, _, _ = _install(monkeypatch, bodies=[FakeBody("Body1")])
        h = "/v" + "Z" * 70
        design._tokens[h] = object()              # not a FakeBody (BRepBody)
        res = dx.handler(format="step", target=h, file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True and "not found" in res["message"].lower()

    def test_no_active_design_errors(self, tmp_path, monkeypatch):
        # design() returns None -> the no-design error, not a crash
        monkeypatch.setattr(dx._common, "design", lambda: None)
        res = dx.handler(format="step", file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True and "no active design" in res["message"].lower()
