"""Unit tests for save_as_mesh.py - the tessellation, the vertex weld and the landed body."""

import json
from conftest import load_tool

mx = load_tool("save_as_mesh")


inp = mx._inputs


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
        return True

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
        # REALISTIC live divergence: execute() always returns True, but only writes a file when
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
        # a COUNTED collection (count + item(i)), the shape _common.all_components walks
        class _Coll:
            def __init__(self, items):
                self._l = items

            @property
            def count(self):
                return len(self._l)

            def item(self, i):
                return self._l[i] if 0 <= i < len(self._l) else None
        return _Coll(self._all)

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
    adsk.fusion.BaseFeature = FakeBaseFeature
    # export refinement + tessellation-quality enums
    # MeshRefinementSettings members are NOT hand-seeded: they come off the mock (measured values
    # once live_api_facts carries the family), and every assertion below reads them by name through
    # _refine_member so a real int - MeshRefinementHigh is 0, a FALSY member - reads identically.
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


def _mesh_source(name="SolidA", tri=12, nodes=8, parent_comp=None, raise_on_calc=False):
    """A BRep body wired with a meshManager that yields a TriangleMesh of the given counts."""
    tm = TriangleMesh(tri=tri, nodes=nodes)
    calc = FakeMeshCalculator(tm, raise_on_calc=raise_on_calc)
    return BRepBody(name, parent=parent_comp, mesh_manager=FakeMeshManager(calc))


class TestSaveAsMesh:

    def _tessellate_without_the_quality_member(self, quality="high"):
        """save_as_mesh on a build carrying no TriangleMeshQualityOptions member for the request -
        setQuality is never called and the calculator runs at its own default level of detail."""
        fusion = _wire_adsk()
        delattr(fusion.TriangleMeshQualityOptions, "HighQualityTriangleMesh")
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        return _payload(mx.handler(body="H", quality=quality)), src

    def test_direct_tessellates_adds_mesh_no_scope(self):
        _wire_adsk()
        mb_coll = FakeMeshBodies(result=MeshBody("SavedMesh"))
        bf = FakeBaseFeature()
        comp = FakeComp("Comp", mesh_bodies=mb_coll, features=FakeFeatures(FakeBaseFeatures(made=bf)))
        src = _mesh_source("SolidA", tri=12, nodes=8, parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})   # DIRECT
        out = _payload(mx.handler(body="H", quality="normal"))
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
        out = _payload(mx.handler(body="H"))
        assert out["saved_as_mesh"] is True
        # PARAMETRIC: the write was wrapped in an OPEN/CLOSED base-feature scope
        assert bf.started is True and bf.finished is True
        assert mb_coll.add_args is not None

    def test_phantom_body_that_never_lands_bites(self):
        # a returned body object is not proof it joined the component - the count is
        _wire_adsk()

        class PhantomMeshBodies(FakeMeshBodies):
            count = 3                                    # static: the add never actually lands

        mb_coll = PhantomMeshBodies(result=MeshBody("Phantom"))
        comp = FakeComp("Comp", mesh_bodies=mb_coll)
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mx.handler(body="H")
        assert res["isError"] is True
        assert "did not increase" in res["message"]

    def test_landed_body_grows_the_count_and_passes(self):
        _wire_adsk()

        class LandingMeshBodies(FakeMeshBodies):
            def __init__(self, result=None):
                super().__init__(result)
                self.count = 3

            def addByTriangleMeshData(self, *a):
                out = super().addByTriangleMeshData(*a)
                self.count += 1
                return out

        mb_coll = LandingMeshBodies(result=MeshBody("SavedMesh"))
        comp = FakeComp("Comp", mesh_bodies=mb_coll)
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mx.handler(body="H"))
        assert out["saved_as_mesh"] is True

    def test_quality_passed_to_calculator(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mx.handler(body="H", quality="very_high"))
        assert out["quality"] == "very_high"
        assert out["quality_requested"] == "very_high"
        # the calculator received the VeryHigh quality enum value
        assert src.meshManager._calc.quality == 15

    def test_quality_that_never_reached_setquality_is_published_null(self):
        # the tessellation ran at the calculator's DEFAULT LOD, so publishing the requested key as
        # 'quality' would report a level of detail the mesh does not have.
        out, src = self._tessellate_without_the_quality_member()
        assert src.meshManager._calc.quality is None      # setQuality was never called
        assert out["quality"] is None
        assert out["quality_requested"] == "high"

    def test_an_unlanded_quality_says_so_on_the_wire(self):
        out, _src = self._tessellate_without_the_quality_member()
        assert "did NOT land" in out["note"] and "default level of detail" in out["note"]

    def test_a_landed_quality_adds_no_did_not_land_note(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mx.handler(body="H", quality="low"))
        assert out["quality"] == "low"
        assert "did NOT land" not in out["note"]

    def test_optional_name_renames_the_mesh(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mx.handler(body="H", name="MyMesh"))
        assert out["name"] == "MyMesh"

    def test_bad_quality_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mx.handler(body="H", quality="ultra")
        assert res["isError"] is True and "quality" in res["message"]

    def test_mesh_source_rejected(self):
        # passing an existing MESH body to save_as_mesh (it wants a BRep) -> honest refusal
        _wire_adsk()
        comp = FakeComp("Comp")
        m = MeshBody("AlreadyMesh")
        _install(FakeDesign(comp, design_type=0), handle_map={"H": m})
        res = mx.handler(body="H")
        assert res["isError"] is True and "already a MESH" in res["message"]

    def test_calculate_failure_surfaces(self):
        _wire_adsk()
        comp = FakeComp("Comp")
        src = _mesh_source("SolidA", parent_comp=comp, raise_on_calc=True)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mx.handler(body="H")
        assert res["isError"] is True and "tessellation" in res["message"].lower()

    def test_add_failure_surfaces_not_swallowed(self):
        _wire_adsk()
        mb_coll = FakeMeshBodies(raise_on_add=True)
        comp = FakeComp("Comp", mesh_bodies=mb_coll)
        src = _mesh_source("SolidA", parent_comp=comp)
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        # in DIRECT mode the add runs directly inside run_in_base_feature; the raise must propagate
        try:
            res = mx.handler(body="H")
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

    def test_vertices_agreeing_to_the_quantization_merge(self):
        # the merge keys on round(x, 6), not on equality: 1e-9 apart is ONE vertex.
        wc, wi = mx._weld([0.0, 0.0, 0.0, 1e-9, 0.0, 0.0], [0, 1])
        assert len(wc) // 3 == 1 and wi == [0, 0]

    def test_vertices_beyond_the_quantization_stay_distinct(self):
        # 1e-3 apart is two vertices - the quantization is a floor, not a modelling tolerance.
        wc, wi = mx._weld([0.0, 0.0, 0.0, 1e-3, 0.0, 0.0], [0, 1])
        assert len(wc) // 3 == 2 and wi == [0, 1]

    def test_malformed_input_returned_unchanged(self):
        # ragged coordinate list (not a multiple of 3) is passed through untouched, never raises
        bad = [0.0, 1.0]
        assert mx._weld(bad, [0]) == (bad, [0])
        assert mx._weld([], []) == ([], [])
