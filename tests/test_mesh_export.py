"""Unit tests for ``mesh_export.py`` â€” the mesh-aware export (OBJ/3MF/STL) and the BRep->MeshBody
tessellation (save_as_mesh).

No live Fusion. Fakes mimic the small mesh slice the tools touch:
  â€¢ ExportManager.createOBJ/C3MF/STLExportOptions(geom, path) + execute() -> True (writes a fake file),
  â€¢ BRepBody.meshManager.createMeshCalculator() + setQuality + calculate() -> TriangleMesh,
  â€¢ Component.meshBodies.addByTriangleMeshData(coords, idx, normals, normalIdx) -> MeshBody.

Pinned (the DoD):
  â€¢ mesh_export each format resolves the RIGHT create*Options + executes + reports file_exists.
  â€¢ a bad format is rejected by the Choice (never reaches the exporter).
  â€¢ save_as_mesh tessellates, adds a mesh body, and routes the WRITE through run_in_base_feature
    (a base-feature scope is OPENED in parametric, NONE in direct).
  â€¢ save_as_mesh reports triangle / node counts.
"""

import json

from conftest import load_tool

mx = load_tool("mesh_export")
inp = mx._inputs


# â”€â”€ fakes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class BRepBody:
    """Stands in for adsk.fusion.BRepBody â€” the source for tessellation / a valid export target."""
    def __init__(self, name="Body1", is_solid=True, token=None, parent=None, mesh_manager=None):
        self.name = name
        self.isSolid = is_solid
        self.entityToken = token or f"BTOK::{name}"
        self.parentComponent = parent
        self.meshManager = mesh_manager


class MeshBody:
    """Stands in for adsk.fusion.MeshBody (a SEPARATE type from BRepBody)."""
    def __init__(self, name="Mesh1", token=None):
        self.name = name
        self.entityToken = token or f"MTOK::{name}"


class TriangleMesh:
    def __init__(self, tri=12, nodes=8):
        self.nodeCoordinatesAsDouble = [0.0] * (nodes * 3)
        self.nodeIndices = list(range(tri * 3))
        self.normalVectorsAsDouble = [0.0] * (nodes * 3)
        self.normalIndices = list(range(tri * 3))
        self.triangleCount = tri
        self.nodeCount = nodes


class FakeMeshCalculator:
    def __init__(self, tm, raise_on_calc=False):
        self._tm = tm
        self.raise_on_calc = raise_on_calc
        self.quality = None

    def setQuality(self, q):
        self.quality = q

    def calculate(self):
        if self.raise_on_calc:
            raise RuntimeError("calculate blew up")
        return self._tm


class FakeMeshManager:
    def __init__(self, calc):
        self._calc = calc

    def createMeshCalculator(self):
        return self._calc


class FakeMeshBodies:
    """comp.meshBodies â€” records the addByTriangleMeshData args and returns a new MeshBody."""
    def __init__(self, result=None, raise_on_add=False):
        self._result = result if result is not None else MeshBody("SavedMesh")
        self.raise_on_add = raise_on_add
        self.add_args = None

    def addByTriangleMeshData(self, coords, coord_idx, normals, normal_idx):
        if self.raise_on_add:
            raise RuntimeError("add failed")
        self.add_args = (coords, coord_idx, normals, normal_idx)
        return self._result


class FakeBodyColl:
    """A bRepBodies-style collection — HAS itemByName (the real adsk.fusion.BRepBodies does)."""
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


class FakeMeshBodyColl:
    """A meshBodies-style collection — REALISTIC: the live adsk.fusion.MeshBodies has NO itemByName,
    only count + item(i). So mesh-by-name resolution MUST iterate (Bug A). Deliberately no itemByName."""
    def __init__(self, bodies):
        self._list = list(bodies)

    @property
    def count(self):
        return len(self._list)

    def item(self, i):
        return self._list[i] if 0 <= i < len(self._list) else None


class FakeOcc:
    """A top-level occurrence (a valid export geometry that writes a file in the fake execute)."""
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name


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


class FakeBaseFeature:
    def __init__(self):
        self.name = "BaseFeature1"
        self.started = False
        self.finished = False

    def startEdit(self):
        self.started = True
        return True

    def finishEdit(self):
        self.finished = True
        return True


class FakeBaseFeatures:
    def __init__(self, made=None):
        self._made = made if made is not None else FakeBaseFeature()
        self.count = 0

    def add(self):
        return self._made


class FakeFeatures:
    def __init__(self, base_features=None):
        self.baseFeatures = base_features if base_features is not None else FakeBaseFeatures()


class FakeExportOptions:
    """An export-options object that supports attribute set/get (so the tool's setattr for
    meshRefinement works, the way the real ExportOptions objects do)."""
    def __init__(self, kind, geom, path):
        self.kind = kind
        self.geom = geom
        self.path = path
        self.meshRefinement = None


class FakeExportManager:
    """Records which create*Options ran + the geometry, and that execute ran (writing a fake file)."""
    def __init__(self):
        self.calls = []
        self.executed = None
        self._last_path = None

    def _opt(self, kind, geom, path):
        rec = FakeExportOptions(kind, geom, path)
        self.calls.append(rec)
        self._last_path = path
        return rec

    def createOBJExportOptions(self, geom, path):
        return self._opt("obj", geom, path)

    def createC3MFExportOptions(self, geom, path):
        return self._opt("3mf", geom, path)

    def createSTLExportOptions(self, geom, path):
        return self._opt("stl", geom, path)

    def execute(self, opts):
        self.executed = opts
        # REALISTIC live divergence (Bug A): execute() always returns True, but only writes a file when
        # the geometry is a BRep body / component / occurrence. A BARE MeshBody geometry writes NOTHING
        # (the file is a no-op) even though the return is truthy.
        if not isinstance(opts.geom, MeshBody):
            with open(opts.path, "w") as fh:
                fh.write("fake-mesh")
        return True


class FakeComp:
    def __init__(self, name="Root", bodies=(), mesh_bodies=None, features=None, occurrences=()):
        self.name = name
        self.bRepBodies = FakeBodyColl(list(bodies))
        self.meshBodies = mesh_bodies if mesh_bodies is not None else FakeMeshBodies()
        self.occurrences = FakeOccs(occurrences)
        self.allOccurrences = list(occurrences)
        self.features = features if features is not None else FakeFeatures()


class FakeDesign:
    def __init__(self, comp, em=None, design_type=0, all_comps=None):
        self.rootComponent = comp
        self.activeComponent = comp
        self.exportManager = em if em is not None else FakeExportManager()
        self.designType = design_type            # 0 direct, 1 parametric
        self._all = all_comps if all_comps is not None else [comp]
        self._tokens = {}

    @property
    def allComponents(self):
        return self._all

    @property
    def allOccurrences(self):
        return []

    def findEntityByToken(self, t):
        e = self._tokens.get(t)
        return [e] if e is not None else []


def _wire_adsk():
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.fusion.BRepBody = BRepBody
    adsk.fusion.MeshBody = MeshBody
    dts = adsk.fusion.DesignTypes
    dts.ParametricDesignType = 1
    dts.DirectDesignType = 0
    adsk.fusion.BaseFeature = FakeBaseFeature
    # export refinement + tessellation-quality enums
    mrs = adsk.fusion.MeshRefinementSettings
    mrs.MeshRefinementHigh = "RHIGH"; mrs.MeshRefinementMedium = "RMED"; mrs.MeshRefinementLow = "RLOW"
    tmo = adsk.fusion.TriangleMeshQualityOptions
    tmo.LowQualityTriangleMesh = 8; tmo.NormalQualityTriangleMesh = 11
    tmo.HighQualityTriangleMesh = 13; tmo.VeryHighQualityTriangleMesh = 15
    return adsk.fusion


def _install(design, handle_map=None):
    handle_map = handle_map or {}
    design._tokens = handle_map
    mx.app = type("A", (), {"activeProduct": design})()
    mx._common.app = mx.app
    # BodyRef resolves via _common.design()/target_component()
    inp._common.design = lambda: design
    inp._common.target_component = lambda d: design.activeComponent
    return design


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# â”€â”€ mesh_export: format dispatch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestExportFormatDispatch:
    def test_obj_uses_obj_options_and_executes(self, tmp_path):
        _wire_adsk()
        comp = FakeComp("Root", bodies=[BRepBody("Body1")])
        des = _install(FakeDesign(comp))
        out = _payload(mx.export_handler(format="obj", file_path=str(tmp_path / "p.obj")))
        assert out["exported"] is True
        assert des.exportManager.calls[-1].kind == "obj"
        assert des.exportManager.executed is not None
        assert out["file_exists"] is True and out["size_bytes"] > 0

    def test_3mf_uses_c3mf_options(self, tmp_path):
        _wire_adsk()
        des = _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        out = _payload(mx.export_handler(format="3mf", file_path=str(tmp_path / "p.3mf")))
        assert des.exportManager.calls[-1].kind == "3mf"
        assert out["format"] == "3mf"

    def test_stl_uses_stl_options(self, tmp_path):
        _wire_adsk()
        des = _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        _payload(mx.export_handler(format="stl", file_path=str(tmp_path / "p.stl")))
        assert des.exportManager.calls[-1].kind == "stl"

    def test_default_format_is_3mf(self, tmp_path):
        _wire_adsk()
        des = _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        out = _payload(mx.export_handler(file_path=str(tmp_path / "p")))
        assert des.exportManager.calls[-1].kind == "3mf"
        assert out["file_path"].lower().endswith(".3mf")   # extension auto-appended

    def test_bad_format_rejected_by_choice(self, tmp_path):
        _wire_adsk()
        _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        res = mx.export_handler(format="dwg", file_path=str(tmp_path / "p.dwg"))
        assert res["isError"] is True and "format" in res["message"]


# â”€â”€ mesh_export: target resolution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestExportTarget:
    def test_whole_design_when_no_target(self, tmp_path):
        _wire_adsk()
        comp = FakeComp("Root", bodies=[BRepBody("Body1")])
        des = _install(FakeDesign(comp))
        out = _payload(mx.export_handler(format="obj", file_path=str(tmp_path / "p.obj")))
        # whole-design export passes the ROOT COMPONENT as the geometry
        assert des.exportManager.calls[-1].geom is comp
        assert "design" in out["target"].lower() or "root" in out["target"].lower()

    def test_body_by_name(self, tmp_path):
        _wire_adsk()
        comp = FakeComp("Root", bodies=[BRepBody("Widget")])
        des = _install(FakeDesign(comp))
        out = _payload(mx.export_handler(format="obj", target="Widget", file_path=str(tmp_path / "p.obj")))
        assert des.exportManager.calls[-1].geom.name == "Widget"
        assert "Widget" in out["target"]

    def test_mesh_body_by_handle_redirects_to_its_component(self, tmp_path):
        # Bug A: a bare MeshBody can't be export-written (execute()->True but no file). The tool
        # REDIRECTS to the mesh's parentComponent (which DOES write a file) and flags the redirect.
        _wire_adsk()
        comp = FakeComp("Root")
        m = MeshBody("ScanA")
        m.parentComponent = comp
        comp.meshBodies = FakeMeshBodyColl([m])     # realistic: no itemByName
        des = _install(FakeDesign(comp), handle_map={"H": m})
        out = _payload(mx.export_handler(format="3mf", target="H", file_path=str(tmp_path / "p.3mf")))
        # the COMPONENT was exported, not the bare mesh — and a file actually landed
        assert des.exportManager.calls[-1].geom is comp
        assert out["redirected_from_mesh"] is True
        assert out["file_exists"] is True and out["size_bytes"] > 0
        assert "mesh" in out["note"].lower()

    def test_mesh_body_by_name_redirects_to_its_component(self, tmp_path):
        # Bug A: a mesh resolves by NAME via count/item iteration (meshBodies has NO itemByName), then
        # redirects to its parentComponent for the actual file write.
        _wire_adsk()
        comp = FakeComp("Root")
        m = MeshBody("ScanByName")
        m.parentComponent = comp
        comp.meshBodies = FakeMeshBodyColl([m])     # realistic: no itemByName -> must iterate
        des = _install(FakeDesign(comp))
        out = _payload(mx.export_handler(format="3mf", target="ScanByName",
                                         file_path=str(tmp_path / "p.3mf")))
        assert des.exportManager.calls[-1].geom is comp
        assert out["redirected_from_mesh"] is True
        assert out["file_exists"] is True

    def test_false_success_when_no_file_written_is_error(self, tmp_path):
        # Bug A core: execute() returns True but NO file lands -> tool must ERROR, not report
        # exported:true. Force the no-write by exporting a BARE mesh whose parent ALSO writes nothing
        # (the FakeExportManager skips the write for any MeshBody geometry).
        _wire_adsk()
        comp = FakeComp("Root")
        m = MeshBody("Orphan")
        m.parentComponent = None                    # redirect falls back to root component...
        # ...but make the root resolve to the mesh itself so execute still writes nothing:
        # simplest: target a mesh whose redirect component has no exportable geometry -> use a comp
        # that the fake exporter treats as a mesh is impossible; instead drive the generic no-file
        # path with a non-mesh geom whose execute is stubbed to not write.
        des = _install(FakeDesign(comp), handle_map={"H": m})
        # execute() returns True without writing a file — the handler must verify the file exists.
        des.exportManager.execute = lambda opts: True
        res = mx.export_handler(format="3mf", target="H", file_path=str(tmp_path / "p.3mf"))
        assert res["isError"] is True
        assert "no file" in res["message"].lower() or "wrote no file" in res["message"].lower()

    def test_brep_target_that_writes_a_file_still_succeeds(self, tmp_path):
        # the verification must NOT regress the happy path: a BRep/component target that DOES write a
        # file still reports exported:true.
        _wire_adsk()
        comp = FakeComp("Root", bodies=[BRepBody("Body1")])
        des = _install(FakeDesign(comp))
        out = _payload(mx.export_handler(format="3mf", target="Body1",
                                         file_path=str(tmp_path / "p.3mf")))
        assert out["exported"] is True
        assert out["redirected_from_mesh"] is False
        assert out["file_exists"] is True and out["size_bytes"] > 0

    def test_component_name_fallback_target(self, tmp_path):
        # a Component NAME (not a body) resolves to the whole component as the export geometry
        _wire_adsk()
        root = FakeComp("Root", bodies=[BRepBody("Body1")])
        sub = FakeComp("SubPart", bodies=[BRepBody("Inner")])
        des = _install(FakeDesign(root, all_comps=[root, sub]))
        out = _payload(mx.export_handler(format="obj", target="SubPart",
                                         file_path=str(tmp_path / "p.obj")))
        assert des.exportManager.calls[-1].geom is sub
        assert "component" in out["target"].lower() and "SubPart" in out["target"]

    def test_occurrence_by_name_target(self, tmp_path):
        # a name that is neither a body nor a component resolves via the allOccurrences scan
        _wire_adsk()
        occ = FakeOcc("Arm:1", full_path="Root/Arm:1")
        comp = FakeComp("Root", bodies=[BRepBody("Body1")], occurrences=[occ])
        des = _install(FakeDesign(comp))
        out = _payload(mx.export_handler(format="obj", target="Arm:1",
                                         file_path=str(tmp_path / "p.obj")))
        assert des.exportManager.calls[-1].geom is occ
        assert "occurrence" in out["target"].lower() and "Arm:1" in out["target"]

    def test_occurrence_by_full_path_target(self, tmp_path):
        _wire_adsk()
        occ = FakeOcc("Arm:1", full_path="Root/Sub/Arm:1")
        comp = FakeComp("Root", bodies=[BRepBody("Body1")], occurrences=[occ])
        des = _install(FakeDesign(comp))
        out = _payload(mx.export_handler(format="obj", target="Root/Sub/Arm:1",
                                         file_path=str(tmp_path / "p.obj")))
        assert des.exportManager.calls[-1].geom is occ
        assert "occurrence" in out["target"].lower()

    def test_missing_named_target_errors(self, tmp_path):
        _wire_adsk()
        _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        res = mx.export_handler(format="obj", target="Nope", file_path=str(tmp_path / "p.obj"))
        assert res["isError"] is True and "Nope" in res["message"]

    def test_missing_path_errors(self):
        _wire_adsk()
        _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        res = mx.export_handler(format="obj")
        assert res["isError"] is True and "file_path" in res["message"]


# â”€â”€ mesh_export: refinement â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestExportRefinement:
    def test_refinement_applied_when_supported(self, tmp_path):
        _wire_adsk()
        des = _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        out = _payload(mx.export_handler(format="obj", refinement="high",
                                         file_path=str(tmp_path / "p.obj")))
        # the options object carried the high refinement enum
        assert des.exportManager.calls[-1].meshRefinement == "RHIGH"
        assert out["refinement"] == "high"

    def test_bad_refinement_rejected(self, tmp_path):
        _wire_adsk()
        _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))
        res = mx.export_handler(format="obj", refinement="ultra", file_path=str(tmp_path / "p.obj"))
        assert res["isError"] is True and "refinement" in res["message"]

    def test_refinement_not_applied_still_reports_requested_key(self, tmp_path):
        # build whose ExportOptions ignores meshRefinement (STL-style): _apply_refinement returns None,
        # but the payload still reports the requested refinement key (applied_refinement or ref).
        _wire_adsk()
        des = _install(FakeDesign(FakeComp("Root", bodies=[BRepBody("Body1")])))

        class _NoRefineOptions(FakeExportOptions):
            # setting meshRefinement is a no-op -> reading it back never equals the enum, so
            # _apply_refinement returns None (the format doesn't support it).
            def __setattr__(self, k, v):
                if k == "meshRefinement":
                    return
                object.__setattr__(self, k, v)

        def _stl_opt(geom, path):
            rec = _NoRefineOptions("stl", geom, path)
            des.exportManager.calls.append(rec)
            return rec
        des.exportManager.createSTLExportOptions = _stl_opt
        out = _payload(mx.export_handler(format="stl", refinement="high",
                                         file_path=str(tmp_path / "p.stl")))
        # the options object never carried the enum (unsupported) ...
        assert getattr(des.exportManager.calls[-1], "meshRefinement", None) is None
        # ... yet the reported refinement falls back to the requested key, not None
        assert out["refinement"] == "high"


# â”€â”€ mesh_export: split_by_component (one mesh file per top-level occurrence) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestExportSplitByComponent:
    def test_one_file_per_occurrence(self, tmp_path):
        _wire_adsk()
        occs = [FakeOcc("Body:1"), FakeOcc("Wheels:1")]
        des = _install(FakeDesign(FakeComp("Root", occurrences=occs)))
        out = _payload(mx.export_handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert out["split_by_component"] is True
        assert out["file_count"] == 2
        geoms = [c.geom.name for c in des.exportManager.calls]
        assert set(geoms) == {"Body:1", "Wheels:1"}

    def test_filenames_sanitized(self, tmp_path):
        _wire_adsk()
        _install(FakeDesign(FakeComp("Root", occurrences=[FakeOcc("Loader Arm:1")])))
        out = _payload(mx.export_handler(format="3mf", file_path=str(tmp_path), split_by_component=True))
        assert out["files"][0]["file_path"].replace("\\", "/").endswith("/Loader_Arm.3mf")

    def test_duplicate_stems_disambiguated(self, tmp_path):
        _wire_adsk()
        _install(FakeDesign(FakeComp("Root", occurrences=[FakeOcc("Wheel:1"), FakeOcc("Wheel:2")])))
        out = _payload(mx.export_handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        paths = [f["file_path"] for f in out["files"]]
        assert len(set(paths)) == 2
        assert any(p.endswith("Wheel.stl") for p in paths) and any(p.endswith("Wheel_2.stl") for p in paths)

    def test_no_occurrences_errors(self, tmp_path):
        _wire_adsk()
        _install(FakeDesign(FakeComp("Root", occurrences=[])))
        res = mx.export_handler(format="stl", file_path=str(tmp_path), split_by_component=True)
        assert res["isError"] is True and "no top-level occurrences" in res["message"].lower()

    def test_split_reports_per_occurrence_failure_without_aborting(self, tmp_path):
        # one occurrence writes a file, one fails (execute raises) -> 1 file, the failure in 'failed'
        _wire_adsk()
        good = FakeOcc("Good:1")
        bad = FakeOcc("Bad:1")
        des = _install(FakeDesign(FakeComp("Root", occurrences=[good, bad])))
        real_execute = des.exportManager.execute
        def _selective(opts):
            if getattr(opts.geom, "name", "") == "Bad:1":
                raise RuntimeError("write blew up")
            return real_execute(opts)
        des.exportManager.execute = _selective
        out = _payload(mx.export_handler(format="stl", file_path=str(tmp_path),
                                         split_by_component=True))
        assert out["file_count"] == 1
        assert out["exported"] is True            # at least one landed
        assert out["files"][0]["occurrence"] == "Good:1"
        assert "failed" in out
        assert out["failed"][0]["occurrence"] == "Bad:1"

    def test_split_all_fail_reports_not_exported(self, tmp_path):
        # every occurrence fails -> exported False, no files, all in 'failed'
        _wire_adsk()
        des = _install(FakeDesign(FakeComp("Root", occurrences=[FakeOcc("A:1"), FakeOcc("B:1")])))
        des.exportManager.execute = lambda opts: (_ for _ in ()).throw(RuntimeError("nope"))
        out = _payload(mx.export_handler(format="stl", file_path=str(tmp_path),
                                         split_by_component=True))
        assert out["exported"] is False
        assert out["file_count"] == 0
        assert len(out["failed"]) == 2


# â”€â”€ save_as_mesh: tessellate + add a mesh body + route through run_in_base_feature â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _mesh_source(name="SolidA", tri=12, nodes=8, parent_comp=None, raise_on_calc=False):
    """A BRep body wired with a meshManager that yields a TriangleMesh of the given counts."""
    tm = TriangleMesh(tri=tri, nodes=nodes)
    calc = FakeMeshCalculator(tm, raise_on_calc=raise_on_calc)
    return BRepBody(name, parent=parent_comp, mesh_manager=FakeMeshManager(calc))


class TestSaveAsMesh:
    def test_direct_tessellates_adds_mesh_no_scope(self):
        _wire_adsk()
        mb_coll = FakeMeshBodies(result=MeshBody("SavedMesh"))
        bf = FakeBaseFeature()
        comp = FakeComp("Comp", mesh_bodies=mb_coll, features=FakeFeatures(FakeBaseFeatures(made=bf)))
        src = _mesh_source("SolidA", tri=12, nodes=8, parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})   # DIRECT
        out = _payload(mx.save_as_mesh_handler(body="H", quality="normal"))
        assert out["saved_as_mesh"] is True
        assert out["name"] == "SavedMesh"
        assert out["triangle_count"] == 12 and out["node_count"] == 8
        assert mb_coll.add_args is not None            # the mesh was actually added
        # DIRECT: run_in_base_feature ran the op with NO scope (the base feature was never started)
        assert bf.started is False and bf.finished is False

    def test_parametric_routes_through_base_feature_scope(self):
        _wire_adsk()
        mb_coll = FakeMeshBodies(result=MeshBody("SavedMesh"))
        bf = FakeBaseFeature()
        comp = FakeComp("Comp", mesh_bodies=mb_coll, features=FakeFeatures(FakeBaseFeatures(made=bf)))
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=1), handle_map={"H": src})   # PARAMETRIC
        out = _payload(mx.save_as_mesh_handler(body="H"))
        assert out["saved_as_mesh"] is True
        # PARAMETRIC: the write was wrapped in an OPEN/CLOSED base-feature scope
        assert bf.started is True and bf.finished is True
        assert mb_coll.add_args is not None

    def test_quality_passed_to_calculator(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mx.save_as_mesh_handler(body="H", quality="very_high"))
        assert out["quality"] == "very_high"
        # the calculator received the VeryHigh quality enum value
        assert src.meshManager._calc.quality == 15

    def test_optional_name_renames_the_mesh(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mx.save_as_mesh_handler(body="H", name="MyMesh"))
        assert out["name"] == "MyMesh"

    def test_bad_quality_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mx.save_as_mesh_handler(body="H", quality="ultra")
        assert res["isError"] is True and "quality" in res["message"]

    def test_mesh_source_rejected(self):
        # passing an existing MESH body to save_as_mesh (it wants a BRep) -> honest refusal
        _wire_adsk()
        comp = FakeComp("Comp")
        m = MeshBody("AlreadyMesh")
        _install(FakeDesign(comp, design_type=0), handle_map={"H": m})
        res = mx.save_as_mesh_handler(body="H")
        assert res["isError"] is True and "already a MESH" in res["message"]

    def test_calculate_failure_surfaces(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp, raise_on_calc=True)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mx.save_as_mesh_handler(body="H")
        assert res["isError"] is True and "tessellation" in res["message"].lower()

    def test_add_failure_surfaces_not_swallowed(self):
        _wire_adsk()
        mb_coll = FakeMeshBodies(raise_on_add=True)
        comp = FakeComp("Comp", mesh_bodies=mb_coll)
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        # in DIRECT mode the add runs directly inside run_in_base_feature; the raise must propagate
        try:
            res = mx.save_as_mesh_handler(body="H")
        except RuntimeError as e:
            assert "add failed" in str(e)
        else:
            assert res["isError"] is True


class TestWeld:
    """_weld merges coincident vertices so a watertight solid tessellates to a watertight mesh. The
    calculator emits one vertex per triangle corner; without welding the mesh is topologically open
    (isClosed=false) and mesh_to_brep refuses it."""

    def test_box_corners_merge_24_to_8(self):
        # 8 distinct corners, each repeated 3x (one per adjacent face) = 24 emitted vertices.
        corners = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                   (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
        coords, idx = [], []
        for i, c in enumerate(corners):
            for _ in range(3):                       # emit each corner 3 times (unwelded)
                idx.append(len(coords) // 3)
                coords.extend(c)
        wc, wi = mx._weld(coords, idx)
        assert len(wc) // 3 == 8                      # 24 -> 8 unique vertices
        # every welded index points at the right merged coordinate
        for emitted, new in zip(idx, wi):
            ex = coords[3 * emitted: 3 * emitted + 3]
            got = wc[3 * new: 3 * new + 3]
            assert ex == got

    def test_distinct_vertices_are_preserved(self):
        coords = [0, 0, 0, 1, 0, 0, 0, 1, 0]         # 3 distinct vertices, no duplicates
        idx = [0, 1, 2]
        wc, wi = mx._weld(coords, idx)
        assert len(wc) // 3 == 3 and wi == [0, 1, 2]

    def test_malformed_input_returned_unchanged(self):
        # ragged coordinate list (not a multiple of 3) is passed through untouched, never raises
        bad = [0.0, 1.0]
        assert mx._weld(bad, [0]) == (bad, [0])
        assert mx._weld([], []) == ([], [])
