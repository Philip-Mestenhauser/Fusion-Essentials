"""Unit tests for ``mesh_ops.py`` — the MESH environment (adsk.fusion.MeshBody).

No live Fusion. We model the small slice of the mesh API the tools touch — MeshBody / MeshBodies /
TriangleMesh / the mesh feature collections — with fakes NAMED to match the Fusion types so the
isinstance / kind discrimination in _inputs.MeshBodyRef branches correctly.

Pinned (the DoD):
  • mesh_get lists meshes with triangle/vertex counts.
  • mesh_insert GATES on a base-feature scope in PARAMETRIC (the rejection names MODE_BASE_FEATURE)
    and WORKS in DIRECT (no scope).
  • mesh_to_brep PRE-CHECKS is_closed (refuses a non-watertight mesh with a clear message) and
    reports the chosen method.
  • MeshBodyRef rejects a BRep handle with the redirect message.
  • An API-not-available op (organic convert without the extension) surfaces an HONEST error, not a
    fake success.
"""

import json

from conftest import load_tool

mo = load_tool("mesh_ops")

# Measured convert enum (seeded from live_api_facts) - fakes and assertions speak these.
import adsk.fusion  # noqa: E402
_CONV = adsk.fusion.MeshConvertMethodTypes
inp = mo._inputs


# ── fakes (named to match the Fusion type names the kind discrimination reads) ──────────────────

class TriangleMesh:
    def __init__(self, tri, nodes):
        self.triangleCount = tri
        self.nodeCount = nodes


class PolygonMesh:
    def __init__(self, tri, polys, nodes):
        self.triangleCount = tri
        self.polygonCount = polys
        self.nodeCount = nodes


class FakePoint:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class FakeBBox:
    def __init__(self, mn, mx):
        self.minPoint = FakePoint(*mn)
        self.maxPoint = FakePoint(*mx)


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
        self.boundingBox = bbox or FakeBBox((0, 0, 0), (1, 2, 3))   # cm
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


# ── ValueInput discipline (mirror the live API: the reduce setters take a ValueInput, NOT a raw num) ──

class _FakeValueInput:
    """What ValueInput.createByReal returns in the harness — a marker carrying the real value. The
    live MeshReduceFeatureInput setters require a Ptr<ValueInput>, so a bare float/int must be
    REJECTED; this is the only type the realistic input below accepts. realValue mirrors the live
    ValueInput property (the remesh density read-back reads it)."""
    def __init__(self, real):
        self.real = real
        self.realValue = real


def _make_value_input(v):
    return _FakeValueInput(v)


class _ReduceInput:
    """A REALISTIC MeshReduceFeatureInput: its proportion/facecount/maximumDeviation setters REQUIRE a
    ValueInput-like object and raise TypeError on a bare float/int — reproducing the live divergence the
    raw-number bug hit. Other attributes (meshReduceTargetType / meshReduceMethodType) are free-form."""
    def __init__(self):
        object.__setattr__(self, "_vi_fields", {"proportion", "facecount", "maximumDeviation"})

    def __setattr__(self, name, value):
        if name in object.__getattribute__(self, "_vi_fields"):
            if not isinstance(value, _FakeValueInput):
                raise TypeError(
                    f"MeshReduceFeatureInput.{name} requires an adsk.core.ValueInput "
                    f"(Ptr<ValueInput>), got {type(value).__name__} {value!r}")
        object.__setattr__(self, name, value)


# ── mesh-feature fakes (createInput -> input ; add -> feature with .bodies) ──────────────────────

class _FeatureResult:
    def __init__(self, name, bodies):
        self.name = name
        self.bodies = _Coll(bodies)


class _MeshFeatures:
    """A reduce/remesh/convert feature collection. add() returns a feature whose .bodies hold the
    result; raise_on_add lets a test force a mutation failure (must surface, not be swallowed).

    none_feature -> add() returns None (the NON-PARAMETRIC contract: a direct design or a base-feature
    scope). on_add_append: an optional (coll, body) the add() appends so a None return still leaves an
    observable side effect (a new BRep body on the component) to detect success by.

    input_factory builds the input createInput returns; mesh_reduce uses the strict _ReduceInput so a
    raw-number assignment to proportion/facecount/maximumDeviation FAILS (it would on the live API)."""
    def __init__(self, result_bodies, feat_name="MeshFeat1", raise_on_add=False, none_feature=False,
                 on_add_append=None, on_add=None, input_factory=None):
        self._result_bodies = result_bodies
        self._feat_name = feat_name
        self.raise_on_add = raise_on_add
        self.none_feature = none_feature
        self._on_add_append = on_add_append
        self._on_add = on_add               # an in-place mutation the add() performs (e.g. reduce)
        self._input_factory = input_factory or (lambda: type("Inp", (), {})())
        self.last_input = None

    def createInput(self, *a):
        self.last_input = self._input_factory()
        return self.last_input

    def add(self, inp):
        if self.raise_on_add:
            raise RuntimeError("conversion failed")
        if self._on_add is not None:
            self._on_add()                  # model the in-place edit (e.g. the mesh's tri count drops)
        if self._on_add_append is not None:
            coll, body = self._on_add_append
            coll._items.append(body)        # the convert produced a NEW BRep body on the component
        if self.none_feature:
            return None
        return _FeatureResult(self._feat_name, self._result_bodies)


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


# ── mesh_get: lists meshes with tri/vertex counts ───────────────────────────────────────────────

class TestMeshGet:
    def test_lists_meshes_with_counts(self):
        _wire_adsk()
        m1 = MeshBody("ScanA", tri=1200, nodes=602)
        m2 = MeshBody("ScanB", tri=80, nodes=42)
        comp = FakeComp("Comp", meshes=[m1, m2])
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target=""))
        assert out["count"] == 2
        by_name = {m["name"]: m for m in out["meshes"]}
        assert by_name["ScanA"]["triangle_count"] == 1200
        assert by_name["ScanA"]["node_count"] == 602
        assert by_name["ScanB"]["triangle_count"] == 80
        # the handle (entityToken) is surfaced for the geometry-as-values bridge
        assert by_name["ScanA"]["handle"] == "MTOK::ScanA"

    def test_empty_when_no_meshes(self):
        _wire_adsk()
        comp = FakeComp("Comp", meshes=[])
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target=""))
        assert out["count"] == 0 and out["meshes"] == []

    def test_no_design_errors(self):
        _wire_adsk()
        mo.app = type("A", (), {"activeProduct": None})()
        mo._common.app = mo.app
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = mo.mesh_get_handler(target="")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_named_component_scopes_to_that_component(self):
        # target=<component name> lists only that component's meshes (not the whole design)
        _wire_adsk()
        root_mesh = MeshBody("RootScan", tri=10)
        sub_mesh = MeshBody("SubScan", tri=20)
        root = FakeComp("Root", meshes=[root_mesh])
        sub = FakeComp("SubPart", meshes=[sub_mesh])
        _install(FakeDesign(root, all_comps=[root, sub]))
        out = _payload(mo.mesh_get_handler(target="SubPart"))
        assert out["count"] == 1
        assert out["meshes"][0]["name"] == "SubScan"
        assert out["scope"] == "SubPart"

    def test_named_occurrence_scopes_to_its_component(self):
        # target=<occurrence name> lists that INSTANCE's component - the vocabulary a tree read emits.
        _wire_adsk()
        sub = FakeComp("SubPart", meshes=[MeshBody("SubScan", tri=20)])
        root = FakeComp("Root", meshes=[MeshBody("RootScan", tri=10)])

        class _Occ:
            def __init__(self, name, path, comp):
                self.name, self.fullPathName, self.component = name, path, comp

        root.allOccurrences = [_Occ("SubPart:1", "SubPart:1", sub)]
        _install(FakeDesign(root, all_comps=[root, sub]))
        out = _payload(mo.mesh_get_handler(target="SubPart:1"))
        assert out["count"] == 1 and out["meshes"][0]["name"] == "SubScan"

    def test_an_occurrence_name_two_instances_share_is_refused(self):
        # Occurrence names are NOT unique (two sub-assemblies each hold a 'Bolt:1'), so listing the
        # meshes of whichever instance the walk reached first would answer about the wrong part.
        _wire_adsk()
        one = FakeComp("BoltA", meshes=[MeshBody("ScanA")])
        two = FakeComp("BoltB", meshes=[MeshBody("ScanB")])
        root = FakeComp("Root", meshes=[])

        class _Occ:
            def __init__(self, name, path, comp):
                self.name, self.fullPathName, self.component = name, path, comp

        root.allOccurrences = [_Occ("Bolt:1", "SubA:1+Bolt:1", one),
                               _Occ("Bolt:1", "SubB:1+Bolt:1", two)]
        _install(FakeDesign(root, all_comps=[root, one, two]))
        res = mo.mesh_get_handler(target="Bolt:1")
        assert res["isError"] is True
        assert "2 occurrences" in res["message"]
        assert "SubA:1+Bolt:1" in res["message"] and "SubB:1+Bolt:1" in res["message"]

    def test_unknown_component_name_errors(self):
        _wire_adsk()
        root = FakeComp("Root", meshes=[MeshBody("RootScan")])
        _install(FakeDesign(root, all_comps=[root]))
        res = mo.mesh_get_handler(target="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "design_get" in res["message"]

    def test_dedup_same_mesh_listed_once(self):
        # the same MeshBody reachable through two components must appear only once (seen-set dedup)
        _wire_adsk()
        shared = MeshBody("Shared", token="MTOK::Shared")
        root = FakeComp("Root", meshes=[shared])
        sub = FakeComp("SubPart", meshes=[shared])   # same object, different component
        _install(FakeDesign(root, all_comps=[root, sub]))
        # whole-design scan reaches root; force the sub into the comps list by also listing via name?
        # Simpler: scan from the named SubPart AND verify the whole-design path dedups via two comps.
        # Build a design whose root.allOccurrences yields the sub component holding the SAME mesh.

        class _Occ:
            def __init__(self, comp):
                self.component = comp
                self.name = comp.name

        root.allOccurrences = [_Occ(sub)]
        out = _payload(mo.mesh_get_handler(target=""))
        names = [m["name"] for m in out["meshes"]]
        assert names.count("Shared") == 1     # deduped despite being in two components
        assert out["count"] == 1

    def test_under_cap_untruncated_and_unchanged(self):
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}") for i in range(5)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target=""))
        assert out["truncated"] is False
        assert out["count"] == 5 and len(out["meshes"]) == 5

    def test_at_cap_truncates_and_flags(self):
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}", token=f"T{i}") for i in range(60)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target="", max_results=50))
        assert out["truncated"] is True
        assert len(out["meshes"]) == 50
        # the full count is still honest, even though the array is capped
        assert out["count"] == 60

    def test_a_caller_cannot_lift_the_cap_past_the_ceiling(self):
        # every row crosses the wire: max_results is clamped into 1..200, so an oversized
        # request is held at the ceiling, not honoured.
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}", token=f"T{i}") for i in range(210)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target="", max_results=999999))
        assert len(out["meshes"]) == 200
        assert out["truncated"] is True and out["count"] == 210

    def test_reports_area_and_volume_scaled_to_units(self):
        # MeshBody.area/volume are cm^2/cm^3 (Fusion's internal units) - default units=mm scales by
        # inv_scale^2 / inv_scale^3 (10^2 / 10^3), the same cm-based idiom model_inspect uses.
        _wire_adsk()
        m = MeshBody("Scan", area=6.0, volume=2.0)     # cm^2, cm^3
        comp = FakeComp("Comp", meshes=[m])
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target=""))
        rec = out["meshes"][0]
        assert abs(rec["area"] - 600.0) < 1e-6      # 6 cm^2 -> 600 mm^2
        assert abs(rec["volume"] - 2000.0) < 1e-6   # 2 cm^3 -> 2000 mm^3
        assert out["units"] == "mm"

    def test_area_and_volume_respect_units_param(self):
        _wire_adsk()
        m = MeshBody("Scan", area=6.0, volume=2.0)     # cm^2, cm^3
        comp = FakeComp("Comp", meshes=[m])
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target="", units="cm"))
        rec = out["meshes"][0]
        assert abs(rec["area"] - 6.0) < 1e-6
        assert abs(rec["volume"] - 2.0) < 1e-6
        assert out["units"] == "cm"

    def test_an_open_mesh_publishes_volume_zero_not_null(self):
        # MeshBody.volume on a mesh that is not closed RETURNS 0.0 - it does not raise - so 0.0 is
        # the API's answer for a body that encloses nothing and the record publishes it as a number.
        _wire_adsk()
        m = MeshBody("OpenScan", is_closed=False, area=4.0, volume=0.0)
        comp = FakeComp("Comp", meshes=[m])
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target=""))
        rec = out["meshes"][0]
        assert rec["is_closed"] is False
        assert rec["volume"] == 0.0
        assert rec["volume"] is not None
        assert abs(rec["area"] - 400.0) < 1e-6   # 4 cm^2 -> 400 mm^2

    def test_volume_is_null_only_when_the_field_cannot_be_read(self):
        # the OTHER meaning of the field: a read that raises is published as null and never sinks
        # the record - area (which DOES read cleanly) is still reported.
        _wire_adsk()
        m = MeshBody("DeadScan", is_closed=True, area=4.0)    # volume left UNSET -> raises on access
        comp = FakeComp("Comp", meshes=[m])
        _install(FakeDesign(comp))
        out = _payload(mo.mesh_get_handler(target=""))
        rec = out["meshes"][0]
        assert rec["volume"] is None
        assert abs(rec["area"] - 400.0) < 1e-6   # unaffected by the volume failure

    def test_the_note_tells_a_zero_volume_apart_from_a_null_one(self):
        # the wire note is the only place a reader learns which of the two a number/null means.
        _wire_adsk()
        comp = FakeComp("Comp", meshes=[MeshBody("Scan")])
        _install(FakeDesign(comp))
        note = _payload(mo.mesh_get_handler(target=""))["note"]
        assert "reads 0.0" in note and "is_closed=false" in note
        assert "could not be read" in note
        assert "is null for a mesh that is not watertight" not in note

    def test_unknown_units_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp", meshes=[MeshBody("Scan")])
        _install(FakeDesign(comp))
        res = mo.mesh_get_handler(target="", units="parsec")
        assert res["isError"] is True


# ── mesh measurement: bbox + counts + watertight (model_inspect calls this on a mesh target) ────

class TestMeshMeasure:
    def test_measures_a_mesh_body(self):
        _wire_adsk()
        m = MeshBody("Scan", tri=999, nodes=500, bbox=FakeBBox((0, 0, 0), (1, 2, 4)))
        out = _payload(mo.mesh_measure_of_body(m, units="mm"))
        assert out["triangle_count"] == 999 and out["node_count"] == 500
        assert out["is_closed"] is True
        # bbox scaled from cm -> mm (x10): 1cm,2cm,4cm -> 10,20,40
        assert abs(out["bbox"]["x"] - 10) < 1e-6
        assert abs(out["bbox"]["z"] - 40) < 1e-6

    def test_non_watertight_carries_warning(self):
        _wire_adsk()
        m = MeshBody("Open", is_closed=False)
        out = _payload(mo.mesh_measure_of_body(m))
        assert out["is_closed"] is False and "not watertight" in out["note"].lower()

    def test_measure_reports_area_volume_scaled(self):
        _wire_adsk()
        m = MeshBody("Scan", area=10.0, volume=5.0)
        out = _payload(mo.mesh_measure_of_body(m, units="mm"))
        assert abs(out["area"] - 1000.0) < 1e-6      # 10 cm^2 -> 1000 mm^2
        assert abs(out["volume"] - 5000.0) < 1e-6    # 5 cm^3 -> 5000 mm^3

    def test_measure_of_an_open_mesh_reports_volume_zero_and_says_why(self):
        # 0.0 is the measured open-mesh reading, so the note has to say the body is not empty - it
        # encloses nothing - or a caller reads the number as a vanished body.
        _wire_adsk()
        m = MeshBody("Open", is_closed=False, area=4.0, volume=0.0)
        out = _payload(mo.mesh_measure_of_body(m))
        assert out["volume"] == 0.0
        assert "reads 0.0" in out["note"] and "nothing enclosed" in out["note"]

    def test_measure_volume_null_when_the_field_cannot_be_read(self):
        _wire_adsk()
        m = MeshBody("Dead")   # area/volume UNSET -> both raise -> both null
        out = _payload(mo.mesh_measure_of_body(m))
        assert out["volume"] is None
        assert out["area"] is None


# ── mesh_insert: base-feature gate in parametric; works in direct ───────────────────────────────

class TestMeshInsert:
    def _file_ok(self):
        # the handler checks os.path.isfile — make it pass
        import mesh_ops_isfile_patch  # noqa  (not a real module; we patch os instead)

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
        res = mo.mesh_insert_handler(file_path="C:/scan.stl", units="mm")
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
        out = _payload(mo.mesh_insert_handler(file_path="C:/scan.stl"))
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
        out = _payload(mo.mesh_insert_handler(file_path="C:/scan.stl", units="mm", name="MyScan"))
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
        out = _payload(mo.mesh_insert_handler(file_path="C:/scan.obj"))
        assert out["imported"] is True
        assert out["base_feature"] is None                           # no scope in direct
        assert mb_coll.add_args[2] is None                           # baseOrFormFeature was None

    def test_bad_extension_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp", mesh_bodies=_MeshBodies())
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        res = mo.mesh_insert_handler(file_path="C:/model.step")
        assert res["isError"] is True and ".stl" in res["message"]

    def test_missing_file_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp", mesh_bodies=_MeshBodies())
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: False
        res = mo.mesh_insert_handler(file_path="C:/nope.stl")
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
        out = _payload(mo.mesh_insert_handler(file_path="C:/scan.stl", target_component="SubPart"))
        assert out["imported"] is True
        assert out["component"] == "SubPart"
        assert sub_coll.add_args is not None        # the import went into SubPart's collection
        assert root.meshBodies.add_args is None     # NOT the active/root component

    def test_unknown_target_component_errors(self):
        _wire_adsk()
        root = FakeComp("Root",
                        features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                        mesh_bodies=_MeshBodies())
        _install(FakeDesign(root, design_type=0, all_comps=[root]))
        mo.os.path.isfile = lambda p: True
        res = mo.mesh_insert_handler(file_path="C:/scan.stl", target_component="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"]

    def test_unknown_units_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp",
                        features=_Features(base_features=_BaseFeatures(made=_BaseFeature())),
                        mesh_bodies=_MeshBodies())
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        res = mo.mesh_insert_handler(file_path="C:/scan.stl", units="parsec")
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
        res = mo.mesh_insert_handler(file_path="C:/scan.stl")
        assert res["isError"] is True and "no bodies" in res["message"].lower()

    def test_import_failure_surfaces_not_swallowed(self):
        # meshBodies.add raises -> must become an error, NOT a false success (no safe() around mutation)
        _wire_adsk()
        mb_coll = _MeshBodies(raise_on_add=True)
        feats = _Features(base_features=_BaseFeatures(made=_BaseFeature()))
        comp = FakeComp("Comp", features=feats, mesh_bodies=mb_coll)
        _install(FakeDesign(comp, design_type=0))
        mo.os.path.isfile = lambda p: True
        res = mo.mesh_insert_handler(file_path="C:/scan.stl")
        assert res["isError"] is True and "import failed" in res["message"]


# ── mesh_insert: the imported bodies' stats are scaled into the units the payload reports ───────
#
# MeshBody.area/volume read in cm^2/cm^3 (Fusion internal). Publishing them raw beside units='mm'
# understates the same body by 100x in area and 1000x in volume - and mesh_get, reading the very
# same body, reports the scaled figures, so the two tools disagree about one body.

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
        out = _payload(mo.mesh_insert_handler(file_path="C:/scan.stl", units=units))
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
        listed = _payload(mo.mesh_get_handler(target="", units="mm"))["meshes"][0]
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


# ── mesh_reduce ─────────────────────────────────────────────────────────────────────────────

class TestMeshReduce:
    def _setup(self, before_tri=1000, after_tri=300, raise_on_add=False, none_feature=False,
               parametric=False, base_feature=None):
        _wire_adsk()
        import adsk.core
        import adsk.fusion
        # The reduce setters require a ValueInput — wire createByReal to the marker the strict input
        # accepts. A bare float would raise TypeError on _ReduceInput (as it does live).
        adsk.core.ValueInput.createByReal = staticmethod(_make_value_input)
        src = MeshBody("Scan", tri=before_tri)
        result = MeshBody("Scan", tri=after_tri)
        src.parentComponent = None
        feats = _MeshFeatures([result], raise_on_add=raise_on_add, none_feature=none_feature,
                              input_factory=_ReduceInput)
        bf = base_feature
        comp = FakeComp("Comp", features=_Features(
            reduce=feats, base_features=_BaseFeatures(made=bf) if bf else None))
        src.parentComponent = comp
        des = FakeDesign(comp, design_type=1 if parametric else 0,
                         edit_object=bf if parametric else None)
        _install(des, handle_map={"H": src})
        return src, feats

    def test_proportion_reduces_and_reports_pct(self):
        src, feats = self._setup(before_tri=1000, after_tri=300)
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=30))
        assert out["reduced"] is True
        assert out["before"]["triangle_count"] == 1000
        assert out["after"]["triangle_count"] == 300
        assert abs(out["reduced_pct"] - 70.0) < 1e-6
        # proportion must be set as a ValueInput (NOT a raw float) — the live API requirement, and the
        # value is the PERCENT as-is (30 = 30%), not 0.30.
        vi = getattr(feats.last_input, "proportion", None)
        assert isinstance(vi, _FakeValueInput)
        assert abs(vi.real - 30.0) < 1e-9

    def test_proportion_out_of_range_rejected(self):
        self._setup()
        res = mo.mesh_reduce_handler(mesh="H", target="proportion", value=150)
        assert res["isError"] is True and "percent" in res["message"]

    def test_facecount_sets_lowercase_field_as_valueinput(self):
        src, feats = self._setup()
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="face_count", value=500))
        assert out["reduced"] is True
        # 'facecount' is set as a ValueInput, not a raw int (the API rejects a bare number); the count
        # is carried as a real (500.0).
        vi = getattr(feats.last_input, "facecount", None)
        assert isinstance(vi, _FakeValueInput)
        assert abs(vi.real - 500.0) < 1e-9

    def test_facecount_below_one_rejected(self):
        self._setup()
        res = mo.mesh_reduce_handler(mesh="H", target="face_count", value=0)
        assert res["isError"] is True and "positive" in res["message"].lower()

    def test_max_deviation_sets_valueinput_scaled_to_cm(self):
        src, feats = self._setup()
        # max_deviation is a LENGTH: 1 mm input -> 0.1 cm handed to the ValueInput.
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="max_deviation", value=1, units="mm"))
        assert out["reduced"] is True
        vi = getattr(feats.last_input, "maximumDeviation", None)
        assert isinstance(vi, _FakeValueInput)
        assert abs(vi.real - 0.1) < 1e-9

    def test_add_failure_surfaces(self):
        self._setup(raise_on_add=True)
        res = mo.mesh_reduce_handler(mesh="H", target="proportion", value=50)
        assert res["isError"] is True and "failed" in res["message"].lower()

    def test_non_numeric_value_rejected(self):
        self._setup()
        res = mo.mesh_reduce_handler(mesh="H", target="proportion", value="lots")
        assert res["isError"] is True and "number" in res["message"]

    def test_max_deviation_below_zero_rejected(self):
        self._setup()
        res = mo.mesh_reduce_handler(mesh="H", target="max_deviation", value=-1)
        assert res["isError"] is True and "positive length" in res["message"]

    def test_missing_reduce_features_collection_errors(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=1000)
        comp = FakeComp("Comp", features=_Features(reduce=None))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mo.mesh_reduce_handler(mesh="H", target="proportion", value=50)
        assert res["isError"] is True
        assert "meshReduceFeatures collection" in res["message"]

    def test_create_input_none_errors(self):
        src, feats = self._setup()
        feats.createInput = lambda *a: None
        res = mo.mesh_reduce_handler(mesh="H", target="proportion", value=50)
        assert res["isError"] is True and "returned nothing" in res["message"]

    def test_slow_note_for_large_source_mesh(self):
        # a SOURCE mesh above the slow threshold carries the fire-and-poll advisory note
        src, feats = self._setup(before_tri=300_000, after_tri=100_000)
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=33))
        assert "30s" in out["note"] and "300000" in out["note"]

    def test_no_slow_note_for_small_mesh(self):
        src, feats = self._setup(before_tri=1000, after_tri=300)
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=30))
        assert "note" not in out

    def test_none_feature_is_success_in_place(self):
        # add() returns None in a DIRECT design; mesh_reduce edits the mesh in place, so success is
        # the mesh's updated triangle count, not the None feature return.
        src, feats = self._setup(before_tri=1000, none_feature=True)
        # the in-place reduction lands DURING add(): before_tri (read first) stays 1000, after = 250
        feats._on_add = lambda: setattr(src.displayMesh, "triangleCount", 250)
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=25))
        assert out["reduced"] is True
        assert out["design_mode"] == "direct"
        assert out["base_feature"] is None            # direct opens no scope
        assert out["feature"] is None
        assert out["before"]["triangle_count"] == 1000
        assert out["after"]["triangle_count"] == 250
        assert mo._common.DIRECT_FEATURE_NOTE in out["note"]

    def test_null_feature_in_a_parametric_scope_reports_parametric(self):
        # the scope - not the design's mode - is why the feature is null, so the payload reports the
        # design as PARAMETRIC and names the base feature the reduce actually landed in.
        bf = _BaseFeature()
        src, feats = self._setup(before_tri=1000, parametric=True, base_feature=bf,
                                 none_feature=True)
        feats._on_add = lambda: setattr(src.displayMesh, "triangleCount", 400)
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=40))
        assert out["feature"] is None
        assert out["design_mode"] == "parametric"
        assert out["base_feature"] == "BaseFeature1"
        assert "BaseFeature1" in out["note"]
        assert "direct" not in out["note"].lower()

    def test_mode_is_read_before_the_scope_opens(self):
        # designType reads DIRECT while a base-feature edit scope is open, so a mode read taken after
        # the reduce would report 'direct' for a parametric design.
        bf = _BaseFeature()
        src, feats = self._setup(before_tri=1000, parametric=True, base_feature=bf,
                                 none_feature=True)
        feats._on_add = lambda: setattr(src.displayMesh, "triangleCount", 400)
        des = mo.app.activeProduct
        real_start = bf.startEdit

        def start_and_flip():
            des.designType = 0        # what the platform reports while the scope is open
            return real_start()

        bf.startEdit = start_and_flip
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=40))
        assert out["design_mode"] == "parametric"

    def test_unreduced_count_is_an_error_not_success(self):
        # the honesty gate: add() succeeded but the triangle count did not decrease -> error, not ok
        src, feats = self._setup(before_tri=1000, after_tri=1000)
        res = mo.mesh_reduce_handler(mesh="H", target="proportion", value=30)
        assert res["isError"] is True
        assert "did not decrease" in res["message"]

    def test_proportion_100_keep_everything_is_not_gated(self):
        # proportion=100 asks to keep every triangle - an unchanged count is the requested outcome
        src, feats = self._setup(before_tri=1000, after_tri=1000)
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=100))
        assert out["reduced"] is True

    def test_parametric_routes_through_base_feature_scope(self):
        # REGRESSION: in PARAMETRIC the createInput->set->add runs INSIDE the helper's base-feature
        # scope (opened AND finished). The feature add must not be defeated by an undetectable-scope
        # guard — it succeeds.
        bf = _BaseFeature()
        src, feats = self._setup(before_tri=1000, after_tri=400, parametric=True, base_feature=bf,
                                 none_feature=True)
        feats._on_add = lambda: setattr(src.displayMesh, "triangleCount", 400)
        out = _payload(mo.mesh_reduce_handler(mesh="H", target="proportion", value=40))
        assert out["reduced"] is True
        assert out["after"]["triangle_count"] == 400
        # the reduce ran inside the helper-opened base-feature scope (leak-proof open/close)
        assert bf.started is True and bf.finished is True


# ── mesh_remesh ─────────────────────────────────────────────────────────────────────────────

class TestMeshRemesh:
    def test_remesh_reports_before_after(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        result = MeshBody("Scan", tri=1500)
        feats = _MeshFeatures([result])
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mo.mesh_remesh_handler(mesh="H"))
        assert out["remeshed"] is True
        assert out["changed"] is True
        assert out["before"]["triangle_count"] == 2000
        assert out["after"]["triangle_count"] == 1500

    def test_unchanged_count_is_flagged_not_asserted(self):
        # count identical after the remesh -> the payload says so instead of implying a fresh mesh
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        result = MeshBody("Scan", tri=2000)
        feats = _MeshFeatures([result])
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mo.mesh_remesh_handler(mesh="H"))
        assert out["changed"] is False
        assert "unchanged" in out["note"]

    def test_missing_remesh_features_collection_errors(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        comp = FakeComp("Comp", features=_Features(remesh=None))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mo.mesh_remesh_handler(mesh="H")
        assert res["isError"] is True
        assert "meshRemeshFeatures collection" in res["message"]

    def test_none_feature_is_success_in_place(self):
        # add() returns None in a DIRECT design; remesh edits in place, so success is the mesh's
        # updated counts, not the None feature return.
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        feats = _MeshFeatures([], none_feature=True,
                              on_add=lambda: setattr(src.displayMesh, "triangleCount", 1800))
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mo.mesh_remesh_handler(mesh="H"))
        assert out["remeshed"] is True
        assert out["design_mode"] == "direct"
        assert out["base_feature"] is None            # direct opens no scope
        assert out["feature"] is None
        assert out["before"]["triangle_count"] == 2000
        assert out["after"]["triangle_count"] == 1800
        assert mo._common.DIRECT_FEATURE_NOTE in out["note"]

    def test_null_feature_in_a_parametric_scope_reports_parametric(self):
        # the scope suppresses the feature; the payload still reports the DESIGN's own mode and names
        # the base feature the remesh landed in.
        _wire_adsk()
        bf = _BaseFeature()
        src = MeshBody("Scan", tri=2000)
        feats = _MeshFeatures([], none_feature=True,
                              on_add=lambda: setattr(src.displayMesh, "triangleCount", 1800))
        comp = FakeComp("Comp", features=_Features(remesh=feats, base_features=_BaseFeatures(made=bf)))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=1, edit_object=bf), handle_map={"H": src})
        out = _payload(mo.mesh_remesh_handler(mesh="H"))
        assert out["feature"] is None
        assert out["design_mode"] == "parametric"
        assert out["base_feature"] == "BaseFeature1"
        assert "BaseFeature1" in out["note"]
        assert "direct" not in out["note"].lower()

    def test_parametric_routes_through_base_feature_scope(self):
        # REGRESSION: in PARAMETRIC the remesh createInput->add runs INSIDE the helper's base-feature
        # scope (opened AND finished) and succeeds.
        _wire_adsk()
        bf = _BaseFeature()
        src = MeshBody("Scan", tri=2000)
        result = MeshBody("Scan", tri=1500)
        feats = _MeshFeatures([result])
        comp = FakeComp("Comp", features=_Features(remesh=feats, base_features=_BaseFeatures(made=bf)))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=1, edit_object=bf), handle_map={"H": src})
        out = _payload(mo.mesh_remesh_handler(mesh="H"))
        assert out["remeshed"] is True
        assert out["after"]["triangle_count"] == 1500
        assert bf.started is True and bf.finished is True


# ── mesh_to_brep: watertight pre-check, method reporting, honest organic gating ─────────────────

class TestMeshToBrep:
    def _setup(self, is_closed=True, raise_on_add=False, none_feature=False, organic=True,
               none_appends_body=False, parametric=False, base_feature=None):
        _wire_adsk()
        import adsk.fusion
        if not organic:
            # organic method ABSENT (older Fusion / no Product Design Extension) -> _organic_available()
            # False -> honest refusal. Genuinely REMOVE the seeded member so the safe() read raises,
            # matching the live "attribute does not exist" case (conftest restores it after the test).
            _mct = adsk.fusion.MeshConvertMethodTypes
            if hasattr(_mct, "OrganicMeshConvertMethodType"):
                delattr(_mct, "OrganicMeshConvertMethodType")
        result_brep = BRepBody("ConvertedBody", is_solid=True)
        brep_coll = _Coll()                      # comp.bRepBodies — starts empty
        # In non-parametric mode add() returns None; none_appends_body models the side effect: the
        # convert still drops a new BRep body onto the component (that body is the success signal).
        append = (brep_coll, BRepBody("ConvertedBody", is_solid=True)) if none_appends_body else None
        feats = _MeshFeatures([result_brep], raise_on_add=raise_on_add, none_feature=none_feature,
                              on_add_append=append)
        src = MeshBody("Scan", is_closed=is_closed)
        bf = base_feature
        comp = FakeComp("Comp", features=_Features(
            convert=feats, base_features=_BaseFeatures(made=bf) if bf else None),
            brep_bodies=brep_coll)
        src.parentComponent = comp
        des = FakeDesign(comp, design_type=1 if parametric else 0,
                         edit_object=bf if parametric else None)
        _install(des, handle_map={"H": src})
        return src, feats

    def test_prismatic_converts_and_reports_method(self):
        self._setup(is_closed=True)
        out = _payload(mo.mesh_to_brep_handler(mesh="H", method="prismatic"))
        assert out["converted"] is True
        assert out["method"] == "prismatic"
        assert out["brep_bodies"][0]["name"] == "ConvertedBody"
        assert out["brep_bodies"][0]["handle"] == "BTOK::ConvertedBody"

    def test_non_watertight_refused_up_front(self):
        # the watertight pre-check: an open mesh is refused BEFORE any add(), with the likely cause
        src, feats = self._setup(is_closed=False)
        res = mo.mesh_to_brep_handler(mesh="H", method="prismatic")
        assert res["isError"] is True
        assert "not watertight" in res["message"].lower()
        # and the mutation was never attempted (no input was created)
        assert feats.last_input is None

    def test_organic_without_extension_is_honest_error(self):
        # API-not-available op surfaces an HONEST error, NOT a fake success or a silent fallback
        src, feats = self._setup(is_closed=True, organic=False)
        res = mo.mesh_to_brep_handler(mesh="H", method="organic")
        assert res["isError"] is True
        assert "Product Design Extension" in res["message"]
        assert "silently" in res["message"].lower() or "not silently" in res["message"].lower()
        assert feats.last_input is None      # refused before any mutation

    def test_conversion_add_failure_surfaces(self):
        self._setup(is_closed=True, raise_on_add=True)
        res = mo.mesh_to_brep_handler(mesh="H", method="prismatic")
        assert res["isError"] is True and "failed" in res["message"].lower()

    def test_none_feature_with_new_brep_body_is_success(self):
        # add() returns None in a direct design but the conversion applied — a new BRep body appeared on
        # the component. Success is judged by that body, not by the (None) feature return.
        self._setup(is_closed=True, none_feature=True, none_appends_body=True)
        out = _payload(mo.mesh_to_brep_handler(mesh="H", method="prismatic"))
        assert out["converted"] is True
        assert out["design_mode"] == "direct"
        assert out["base_feature"] is None            # direct opens no scope
        assert out["feature"] is None
        assert out["brep_bodies"][0]["name"] == "ConvertedBody"
        assert mo._common.DIRECT_FEATURE_NOTE in out["note"]

    def test_null_feature_in_a_parametric_scope_reports_parametric(self):
        # the scope suppresses the feature; the payload reports the DESIGN's own mode and names the
        # base feature the conversion landed in, instead of labelling the design non-parametric.
        bf = _BaseFeature()
        self._setup(is_closed=True, parametric=True, base_feature=bf, none_feature=True,
                    none_appends_body=True)
        out = _payload(mo.mesh_to_brep_handler(mesh="H", method="prismatic"))
        assert out["feature"] is None
        assert out["design_mode"] == "parametric"
        assert out["base_feature"] == "BaseFeature1"
        assert "BaseFeature1" in out["note"]
        assert "direct" not in out["note"].lower()

    def test_none_feature_with_no_new_body_is_real_failure_with_hint(self):
        # add() returned None AND no new BRep body appeared -> a REAL failure. Keep the prismatic
        # face-groups hint so the agent knows the likely fix.
        self._setup(is_closed=True, none_feature=True, none_appends_body=False)
        res = mo.mesh_to_brep_handler(mesh="H", method="prismatic")
        assert res["isError"] is True
        assert "did not produce a BRep body" in res["message"]
        assert "mesh_generate_face_groups" in res["message"]

    def test_parametric_routes_through_base_feature_scope(self):
        # REGRESSION: in PARAMETRIC the convert runs INSIDE the helper's base-feature scope (opened AND
        # finished). The parametric add returns a feature carrying .bodies; success is reported without
        # any undetectable-scope guard defeating it.
        bf = _BaseFeature()
        self._setup(is_closed=True, parametric=True, base_feature=bf)
        out = _payload(mo.mesh_to_brep_handler(mesh="H", method="prismatic"))
        assert out["converted"] is True
        assert out["brep_bodies"][0]["name"] == "ConvertedBody"
        assert bf.started is True and bf.finished is True

    def test_brep_handle_to_convert_is_redirected(self):
        # passing a BRep body to mesh_to_brep (it wants a MESH) -> MeshBodyRef redirect
        _wire_adsk()
        brep = BRepBody("AlreadySolid", is_solid=True)
        comp = FakeComp("Comp")
        _install(FakeDesign(comp, design_type=0), handle_map={"H": brep})
        res = mo.mesh_to_brep_handler(mesh="H")
        assert res["isError"] is True and "must be a MESH body" in res["message"]

    def test_missing_convert_features_collection_errors(self):
        _wire_adsk()
        src = MeshBody("Scan", is_closed=True)
        comp = FakeComp("Comp", features=_Features(convert=None))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mo.mesh_to_brep_handler(mesh="H", method="prismatic")
        assert res["isError"] is True
        assert "meshConvertFeatures collection" in res["message"]

    def test_create_input_none_errors(self):
        src, feats = self._setup(is_closed=True)
        feats.createInput = lambda *a: None
        res = mo.mesh_to_brep_handler(mesh="H", method="prismatic")
        assert res["isError"] is True and "returned nothing" in res["message"]

    def test_faceted_method_resolves_enum(self):
        # the faceted branch maps to FacetedMeshConvertMethodType on the input
        src, feats = self._setup(is_closed=True)
        out = _payload(mo.mesh_to_brep_handler(mesh="H", method="faceted"))
        assert out["method"] == "faceted"
        assert getattr(feats.last_input, "meshConvertMethodType", None) == _CONV.FacetedMeshConvertMethodType


# ── the tool description a connected agent reads before it ever calls ───────────────────────────

class TestMeshGetDescription:
    def test_the_description_states_the_measured_open_mesh_volume(self):
        # the description is the only thing an agent knows about the field before the first call,
        # so it carries the same 0.0-vs-null split the payload note does.
        desc = mo.mesh_get_tool.to_dict()["description"]
        assert "reads 0.0 on a mesh that is not watertight" in desc
        assert "null only when the field could not be read" in desc
        assert "'volume' is null for a mesh that is not watertight" not in desc


class TestRemeshDensityReadBack:
    def test_density_that_lands_is_echoed(self):
        # density takes a ValueInput (live-verified; a raw float raises in the SWIG layer) and the
        # set is read back off realValue; a landed density is published, never silently assumed.
        _wire_adsk()
        import adsk.core
        adsk.core.ValueInput.createByReal = staticmethod(_make_value_input)
        src = MeshBody("Scan", tri=2000)
        feats = _MeshFeatures([MeshBody("Scan", tri=900)])
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mo.mesh_remesh_handler(mesh="H", density=12))
        assert out["density_applied"] == 12.0
        assert feats.last_input.density.realValue == 12.0

    def test_density_the_build_drops_is_refused(self):
        # Measured: a silent setattr drop ran the default remesh while implying the density took -
        # a set whose read-back does not echo refuses the input instead.
        _wire_adsk()
        import adsk.core
        adsk.core.ValueInput.createByReal = staticmethod(_make_value_input)

        class _DropsDensity:
            def __setattr__(self, name, value):
                if name == "density":
                    return                      # the SWIG-proxy silent drop
                object.__setattr__(self, name, value)

        src = MeshBody("Scan", tri=2000)
        feats = _MeshFeatures([MeshBody("Scan", tri=900)],
                              input_factory=lambda: _DropsDensity())
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mo.mesh_remesh_handler(mesh="H", density=12)
        assert res["isError"] is True and "did not land" in res["message"]

    def test_no_density_asks_for_no_read_back(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        feats = _MeshFeatures([MeshBody("Scan", tri=900)])
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mo.mesh_remesh_handler(mesh="H"))
        assert "density_applied" not in out


# ── the second signal a triangle-count gate is backed with (_area_volume / _mesh_moved) ─────────

class TestAreaVolumeSignal:
    def test_reads_area_and_volume_in_internal_cm_units(self):
        assert mo._area_volume(MeshBody("M", area=150.0, volume=125.0)) == (150.0, 125.0)

    def test_an_unreadable_field_is_None_not_zero(self):
        # 0.0 is an ANSWER for MeshBody.volume (a body enclosing nothing), so an unreadable read
        # must not borrow it - a coerced 0.0 would read as "the geometry vanished".
        assert mo._area_volume(MeshBody("M")) == (None, None)
        assert mo._area_volume(MeshBody("M", area=12.0)) == (12.0, None)

    def test_a_moved_area_reports_movement(self):
        assert mo._mesh_moved((150.0, 125.0), (90.0, 125.0)) is True

    def test_a_moved_volume_alone_reports_movement(self):
        # Either signal is sufficient: a cut can shave volume while the surface area holds.
        assert mo._mesh_moved((150.0, 125.0), (150.0, 62.5)) is True

    def test_identical_readings_are_flat(self):
        assert mo._mesh_moved((150.0, 125.0), (150.0, 125.0)) is False

    def test_a_difference_inside_the_band_is_flat(self):
        # Float noise on a re-read is not a cut; the band is what keeps it from reading as one.
        assert mo._mesh_moved((150.0, 125.0), (150.0 + 1e-12, 125.0 - 1e-12)) is False

    def test_one_readable_signal_still_decides(self):
        assert mo._mesh_moved((None, 125.0), (None, 62.5)) is True
        assert mo._mesh_moved((None, 125.0), (None, 125.0)) is False

    def test_neither_signal_readable_is_UNKNOWN_never_flat(self):
        # None, not False: a caller that treated unknown as "flat" would refuse a landed cut it
        # simply could not measure.
        assert mo._mesh_moved((None, None), (None, None)) is None
        assert mo._mesh_moved((150.0, 125.0), (None, None)) is None
