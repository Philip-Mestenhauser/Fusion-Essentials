"""Unit tests for mesh_insert.py - the STL/OBJ/3MF import and its base-feature scope."""

import json
from conftest import load_tool, make_bbox
import adsk.fusion  # noqa: E402

mo = load_tool("mesh_insert")
mesh_get = load_tool("mesh_get")


inp = load_tool("_inputs")


class TriangleMesh:
    def __init__(self, tri, nodes):
        self.triangleCount = tri
        self.nodeCount = nodes


class PolygonMesh:
    def __init__(self, tri, polys, nodes):
        self.triangleCount = tri
        self.polygonCount = polys
        self.nodeCount = nodes


_UNSET = object()


class MeshBody:
    """Stands in for adsk.fusion.MeshBody (a SEPARATE type from BRepBody).

    area/volume default to UNSET (the attribute is not set at all), so a plain access raises
    AttributeError - a field that cannot be READ, which the record must publish as null. It is NOT
    the open-mesh shape: MeshBody.volume on a mesh that is not closed RETURNS 0.0 (measured on a
    single-triangle STL reading is_closed false), so an open-mesh fake passes volume=0.0 and the
    record publishes 0.0. Pass explicit cm values to model a real reading."""
    def __init__(self, name="Mesh1", tri=1000, nodes=502, is_closed=True, is_oriented=True,
                 token=None, bbox=None, parent=None, area=_UNSET, volume=_UNSET):
        self.name = name
        self.displayMesh = TriangleMesh(tri, nodes)
        self.mesh = PolygonMesh(tri, tri, nodes)
        self.isClosed = is_closed
        self.isOriented = is_oriented
        self.entityToken = token or f"MTOK::{name}"
        self.boundingBox = bbox or make_bbox((0, 0, 0), (1, 2, 3))   # cm
        self.parentComponent = parent
        if area is not _UNSET:
            self.area = area
        if volume is not _UNSET:
            self.volume = volume


class BRepBody:
    """Stands in for adsk.fusion.BRepBody — the WRONG kind for a mesh input."""
    def __init__(self, name="Body1", is_solid=True, token=None):
        self.name = name
        self.isSolid = is_solid
        self.entityToken = token or f"BTOK::{name}"


class _Coll:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def itemByName(self, n):
        for it in self._items:
            if getattr(it, "name", None) == n:
                return it
        return None


class _Features:
    def __init__(self, reduce=None, remesh=None, convert=None, base_features=None):
        self.meshReduceFeatures = reduce
        self.meshRemeshFeatures = remesh
        self.meshConvertFeatures = convert
        self.baseFeatures = base_features


class _BaseFeature:
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


class _BaseFeatures:
    def __init__(self, made):
        self._made = made

    def add(self):
        return self._made


class _MeshBodies:
    """comp.meshBodies — a counted collection that ALSO imports via add(path, units, base_feature)."""
    def __init__(self, existing=(), import_result=None, raise_on_add=False):
        self._existing = list(existing)
        self._import_result = import_result
        self.raise_on_add = raise_on_add
        self.add_args = None

    @property
    def count(self):
        return len(self._existing)

    def item(self, i):
        return self._existing[i] if 0 <= i < len(self._existing) else None

    def add(self, path, units, base_feature):
        if self.raise_on_add:
            raise RuntimeError("import failed")
        self.add_args = (path, units, base_feature)
        return self._import_result


class FakeComp:
    def __init__(self, name="Comp", meshes=(), features=None, mesh_bodies=None, brep_bodies=None):
        self.name = name
        self.meshBodies = mesh_bodies if mesh_bodies is not None else _Coll(meshes)
        # comp.bRepBodies — the mesh_to_brep non-parametric side-effect probe (new body appeared).
        self.bRepBodies = brep_bodies if brep_bodies is not None else _Coll()
        self.features = features


class FakeDesign:
    def __init__(self, comp, design_type=0, edit_object=None, all_comps=None):
        self.activeComponent = comp
        self.rootComponent = comp
        self.designType = design_type           # 0 direct, 1 parametric
        self.activeEditObject = edit_object
        self._all = all_comps if all_comps is not None else [comp]

    @property
    def allComponents(self):
        # A COUNTED collection (count/item), the live shape every design-wide component walk reads -
        # a bare list makes `.count` a method and the walk cannot run at all.
        return _Coll(self._all)

    @property
    def allOccurrences(self):
        return []

    def findEntityByToken(self, tok):
        return self._handle_map.get(tok, [])

    _handle_map = {}


def _wire_adsk(handle_map=None, parametric=False, mesh_units_ok=True):
    """Install the adsk.fusion type identities + enums the tools/kinds read. Returns nothing; the
    caller builds the design separately."""
    import adsk.fusion
    adsk.fusion.MeshBody = MeshBody
    adsk.fusion.BRepBody = BRepBody
    # ModeGuard reads BaseFeature for scope detection (DesignTypes ints come seeded).
    adsk.fusion.BaseFeature = _BaseFeature
    # mesh units enum
    if mesh_units_ok:
        mu = adsk.fusion.MeshUnits
        mu.MillimeterMeshUnit = "MM"; mu.CentimeterMeshUnit = "CM"; mu.MeterMeshUnit = "M"
        mu.InchMeshUnit = "IN"; mu.FootMeshUnit = "FT"
    return adsk.fusion


def _install(design, handle_map=None):
    """Point the tool + the MeshBodyRef kind at a fake design and a token resolver."""
    handle_map = handle_map or {}
    design._handle_map = {k: [v] for k, v in handle_map.items()}
    mo.app = type("A", (), {"activeProduct": design})()
    mo._common.app = mo.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # MeshBodyRef resolves via _common.design()/target_component()
    inp._common.design = lambda: design
    inp._common.target_component = lambda d: design.activeComponent
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestMeshInsert:

    def test_gates_on_base_feature_scope_in_parametric_when_scope_cannot_open(self):
        # Parametric design where baseFeatures.add() returns None -> run_in_base_feature cannot open the
        # scope -> honest error (NOT a false-negative recheck guard).
        _wire_adsk()
        imported = MeshBody("Imported")
        mb_coll = _MeshBodies(import_result=_Coll([imported]))
        feats = _Features(base_features=_BaseFeatures(made=None))   # add() -> None: scope won't open
        comp = FakeComp("Comp", features=feats, mesh_bodies=mb_coll)
        des = FakeDesign(comp, design_type=1)                       # parametric
        _install(des)
        mo.os.path.isfile = lambda p: True
        res = mo.handler(file_path="C:/scan.stl", units="mm")
        assert res["isError"] is True
        assert "base-feature scope" in res["message"].lower()

    def test_parametric_succeeds_even_when_scope_is_invisible_to_a_guard(self):
        # An open base-feature scope is undetectable (activeEditObject is None even though the scope is
        # open), so run_in_base_feature must NOT re-check it after startEdit. The insert succeeds when
        # meshBodies.add returns a non-empty list, regardless of the unobservable scope state.
        _wire_adsk()
        bf = _BaseFeature()
        imported = MeshBody("Imported", tri=777)
        feats = _Features(base_features=_BaseFeatures(made=bf))
        mb_coll = _MeshBodies(import_result=_Coll([imported]))
        comp = FakeComp("Comp", features=feats, mesh_bodies=mb_coll)
        des = FakeDesign(comp, design_type=1, edit_object=None)     # scope invisible to any guard
        _install(des)
        mo.os.path.isfile = lambda p: True
        out = _payload(mo.handler(file_path="C:/scan.stl"))
        assert out["imported"] is True                              # no false "could not open scope"
        assert out["base_feature"] == "BaseFeature1"
        assert out["bodies"][0]["triangle_count"] == 777
        # the import ran INSIDE the helper's atomic scope (opened AND finished)
        assert bf.started is True and bf.finished is True
        assert mb_coll.add_args[2] is bf

    def test_works_in_parametric_with_visible_scope(self):
        # Parametric: the import runs inside the helper's base-feature scope and succeeds.
        _wire_adsk()
        bf = _BaseFeature()
        imported = MeshBody("Imported", tri=500)
        feats = _Features(base_features=_BaseFeatures(made=bf))
        mb_coll = _MeshBodies(import_result=_Coll([imported]))
        comp = FakeComp("Comp", features=feats, mesh_bodies=mb_coll)
        des = FakeDesign(comp, design_type=1, edit_object=bf)
        _install(des)
        mo.os.path.isfile = lambda p: True
        out = _payload(mo.handler(file_path="C:/scan.stl", units="mm", name="MyScan"))
        assert out["imported"] is True
        assert out["base_feature"] == "BaseFeature1"
        assert out["bodies"][0]["triangle_count"] == 500
        # the import was wrapped in startEdit/finishEdit on the helper-opened base feature
        assert bf.started is True and bf.finished is True
        assert mb_coll.add_args[0] == "C:/scan.stl" and mb_coll.add_args[2] is bf

    def test_works_in_direct_without_scope(self):
        # DIRECT design -> NO base-feature scope; baseOrFormFeature passed as None; import succeeds.
        _wire_adsk()
        imported = MeshBody("Imported", tri=320)
        mb_coll = _MeshBodies(import_result=_Coll([imported]))
        feats = _Features(base_features=_BaseFeatures(made=_BaseFeature()))
        comp = FakeComp("Comp", features=feats, mesh_bodies=mb_coll)
        des = FakeDesign(comp, design_type=0)                        # direct
        _install(des)
        mo.os.path.isfile = lambda p: True
        out = _payload(mo.handler(file_path="C:/scan.obj"))
        assert out["imported"] is True
        assert out["base_feature"] is None                           # no scope in direct
        assert mb_coll.add_args[2] is None                           # baseOrFormFeature was None

    def test_bad_extension_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp", mesh_bodies=_MeshBodies())
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        res = mo.handler(file_path="C:/model.step")
        assert res["isError"] is True and ".stl" in res["message"]

    def test_missing_file_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp", mesh_bodies=_MeshBodies())
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: False
        res = mo.handler(file_path="C:/nope.stl")
        assert res["isError"] is True and "not found" in res["message"].lower()

    def test_named_target_component_imports_into_it(self):
        # target_component=<name> imports into THAT component, not the active one
        _wire_adsk()
        imported = MeshBody("Imported", tri=64)
        sub_coll = _MeshBodies(import_result=_Coll([imported]))
        root = FakeComp("Root", features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                        mesh_bodies=_MeshBodies())
        sub = FakeComp("SubPart",
                       features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                       mesh_bodies=sub_coll)
        des = FakeDesign(root, design_type=0, all_comps=[root, sub])
        _install(des)
        mo.os.path.isfile = lambda p: True
        out = _payload(mo.handler(file_path="C:/scan.stl", target_component="SubPart"))
        assert out["imported"] is True
        assert out["component"] == "SubPart"
        assert sub_coll.add_args is not None        # the import went into SubPart's collection
        assert root.meshBodies.add_args is None     # NOT the active/root component

    def test_duplicate_target_component_name_refused_no_import(self):
        # two components named 'SubPart': importing into whichever the walk reached first would put
        # the mesh in the wrong part, so the import refuses before it runs
        _wire_adsk()
        root = FakeComp("Root", features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                        mesh_bodies=_MeshBodies())
        a = FakeComp("SubPart", features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                     mesh_bodies=_MeshBodies())
        b = FakeComp("SubPart", features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                     mesh_bodies=_MeshBodies())
        _install(FakeDesign(root, design_type=0, all_comps=[root, a, b]))
        mo.os.path.isfile = lambda p: True
        res = mo.handler(file_path="C:/scan.stl", target_component="SubPart")
        assert res["isError"] is True
        assert "2 components match 'SubPart'" in res["message"]
        # target_component is this tool's ONLY component vocabulary, so the remedy is the active
        # component - the one route that still reaches a specific instance here
        assert "design_activate_component" in res["message"]
        assert "rename" not in res["message"].lower()
        assert a.meshBodies.add_args is None and b.meshBodies.add_args is None
        assert root.meshBodies.add_args is None       # and not into the active component either

    def test_unknown_target_component_errors(self):
        _wire_adsk()
        root = FakeComp("Root",
                        features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                        mesh_bodies=_MeshBodies())
        _install(FakeDesign(root, design_type=0, all_comps=[root]))
        mo.os.path.isfile = lambda p: True
        res = mo.handler(file_path="C:/scan.stl", target_component="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"]

    def test_unknown_units_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp",
                        features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                        mesh_bodies=_MeshBodies())
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        res = mo.handler(file_path="C:/scan.stl", units="parsec")
        assert res["isError"] is True
        assert "mm, cm, m, in, or ft" in res["message"]

    def test_empty_import_result_errors(self):
        # meshBodies.add returns an EMPTY list (file unreadable as a mesh) -> honest error
        _wire_adsk()
        mb_coll = _MeshBodies(import_result=_Coll([]))
        feats = _Features(base_features=_BaseFeatures(made=_BaseFeature()))
        comp = FakeComp("Comp", features=feats, mesh_bodies=mb_coll)
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        res = mo.handler(file_path="C:/scan.stl")
        assert res["isError"] is True and "no bodies" in res["message"].lower()

    def test_import_failure_surfaces_not_swallowed(self):
        # meshBodies.add raises -> must become an error, NOT a false success (no safe() around mutation)
        _wire_adsk()
        mb_coll = _MeshBodies(raise_on_add=True)
        feats = _Features(base_features=_BaseFeatures(made=_BaseFeature()))
        comp = FakeComp("Comp", features=feats, mesh_bodies=mb_coll)
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        res = mo.handler(file_path="C:/scan.stl")
        assert res["isError"] is True and "import failed" in res["message"]


class TestMeshInsertStats:

    def _insert(self, units="mm", area=6.0, volume=1.0, existing=()):
        return self._insert_with_collection(units, area, volume, existing)[0]

    def _insert_with_collection(self, units="mm", area=6.0, volume=1.0, existing=()):
        _wire_adsk()
        imported = MeshBody("Imported", area=area, volume=volume)
        mb_coll = _MeshBodies(existing=existing or [imported], import_result=_Coll([imported]))
        comp = FakeComp("Comp",
                        features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                        mesh_bodies=mb_coll)
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        out = _payload(mo.handler(file_path="C:/scan.stl", units=units))
        return out, mb_coll

    def test_each_unit_key_pairs_its_import_enum_with_its_own_factor(self):
        # ONE table drives both halves: the MeshUnits enum handed to meshBodies.add AND the factor
        # the reported stats are scaled by. A row whose enum and factor belong to different units
        # imports at one scale and reports at another - so both are asserted per key, against
        # cm-per-unit restated here rather than read from the tool.
        rows = [("mm", "MM", 0.1), ("cm", "CM", 1.0), ("m", "M", 100.0),
                ("in", "IN", 2.54), ("ft", "FT", 30.48)]
        for key, enum_sentinel, cm_per_unit in rows:
            out, coll = self._insert_with_collection(units=key, area=6.0, volume=1.0)
            assert coll.add_args[1] == enum_sentinel, f"{key} imported as {coll.add_args[1]}"
            assert out["units"] == key
            assert abs(out["bodies"][0]["area"] - round(6.0 / cm_per_unit ** 2, 6)) < 1e-6, key
            assert abs(out["bodies"][0]["volume"] - round(1.0 / cm_per_unit ** 3, 6)) < 1e-6, key

    def test_area_and_volume_are_scaled_into_the_reported_units(self):
        out = self._insert(units="mm", area=6.0, volume=1.0)      # cm^2, cm^3
        assert out["units"] == "mm"
        assert abs(out["bodies"][0]["area"] - 600.0) < 1e-6       # 6 cm^2 -> 600 mm^2
        assert abs(out["bodies"][0]["volume"] - 1000.0) < 1e-6    # 1 cm^3 -> 1000 mm^3

    def test_the_same_body_reads_the_same_from_mesh_get(self):
        # the two tools' figures for ONE body must agree; a raw-cm insert payload disagrees with
        # mesh_get by a factor of 100 (area) / 1000 (volume) on the identical mesh.
        out = self._insert(units="mm", area=6.0, volume=1.0)
        listed = _payload(mesh_get.handler(target="", units="mm"))["meshes"][0]
        assert out["bodies"][0]["area"] == listed["area"]
        assert out["bodies"][0]["volume"] == listed["volume"]

    def test_inch_authored_units_scale_by_the_shared_factor(self):
        out = self._insert(units="in", area=2.54 ** 2, volume=2.54 ** 3)
        assert abs(out["bodies"][0]["area"] - 1.0) < 1e-6         # 1 in^2
        assert abs(out["bodies"][0]["volume"] - 1.0) < 1e-6       # 1 in^3

    def test_metre_authored_units_scale_too(self):
        # m/ft are outside the shared mm/cm/in length kind, so a scaling path that only knew that
        # kind would leave a metre-authored file's stats in raw cm.
        out = self._insert(units="m", area=20000.0, volume=1_000_000.0)
        assert abs(out["bodies"][0]["area"] - 2.0) < 1e-6         # 2 m^2
        assert abs(out["bodies"][0]["volume"] - 1.0) < 1e-6       # 1 m^3

    def test_foot_authored_units_scale_too(self):
        out = self._insert(units="ft", area=30.48 ** 2, volume=30.48 ** 3)
        assert abs(out["bodies"][0]["area"] - 1.0) < 1e-6         # 1 ft^2
        assert abs(out["bodies"][0]["volume"] - 1.0) < 1e-6       # 1 ft^3
