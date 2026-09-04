"""Unit tests for mesh_get.py - the MESH body listing, and the shared mesh reads it publishes."""

import json
import re
from conftest import load_tool, make_bbox
import adsk.fusion  # noqa: E402

mo = load_tool("mesh_get")
mesh_common_mod = load_tool("_mesh_common")


inp = mo._inputs


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


class TestMeshGet:

    def test_lists_meshes_with_counts(self):
        _wire_adsk()
        m1 = MeshBody("ScanA", tri=1200, nodes=602)
        m2 = MeshBody("ScanB", tri=80, nodes=42)
        comp = FakeComp("Comp", meshes=[m1, m2])
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target=""))
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
        out = _payload(mo.handler(target=""))
        assert out["count"] == 0 and out["meshes"] == []

    def test_no_design_errors(self):
        _wire_adsk()
        mo.app = type("A", (), {"activeProduct": None})()
        mo._common.app = mo.app
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = mo.handler(target="")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_named_component_scopes_to_that_component(self):
        # target=<component name> lists only that component's meshes (not the whole design)
        _wire_adsk()
        root_mesh = MeshBody("RootScan", tri=10)
        sub_mesh = MeshBody("SubScan", tri=20)
        root = FakeComp("Root", meshes=[root_mesh])
        sub = FakeComp("SubPart", meshes=[sub_mesh])
        _install(FakeDesign(root, all_comps=[root, sub]))
        out = _payload(mo.handler(target="SubPart"))
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
        out = _payload(mo.handler(target="SubPart:1"))
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
        res = mo.handler(target="Bolt:1")
        assert res["isError"] is True
        assert "2 occurrences" in res["message"]
        assert "SubA:1+Bolt:1" in res["message"] and "SubB:1+Bolt:1" in res["message"]

    def test_a_component_name_two_components_share_is_refused(self):
        # Two components named 'SubPart' - listing the meshes of whichever the design-wide walk
        # reached first would answer about the wrong part, exactly as for a shared occurrence name.
        _wire_adsk()
        one = FakeComp("SubPart", meshes=[MeshBody("ScanA")])
        two = FakeComp("SubPart", meshes=[MeshBody("ScanB")])
        root = FakeComp("Root", meshes=[])
        _install(FakeDesign(root, all_comps=[root, one, two]))
        res = mo.handler(target="SubPart")
        assert res["isError"] is True
        assert "2 components match 'SubPart'" in res["message"]
        # this read takes no handle, so it offers the occurrence vocabulary and its own '' scope -
        # and never a handle it would refuse
        assert "occurrence name/fullPathName" in res["message"]
        assert "target=''" in res["message"]
        assert "find_geometry" not in res["message"]

    def test_unknown_component_name_errors(self):
        _wire_adsk()
        root = FakeComp("Root", meshes=[MeshBody("RootScan")])
        _install(FakeDesign(root, all_comps=[root]))
        res = mo.handler(target="Ghost")
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
        out = _payload(mo.handler(target=""))
        names = [m["name"] for m in out["meshes"]]
        assert names.count("Shared") == 1     # deduped despite being in two components
        assert out["count"] == 1

    def test_under_cap_untruncated_and_unchanged(self):
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}") for i in range(5)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target=""))
        assert out["truncated"] is False
        assert out["count"] == 5 and len(out["meshes"]) == 5

    def test_at_cap_truncates_and_flags(self):
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}", token=f"T{i}") for i in range(60)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target="", max_results=50))
        assert out["truncated"] is True
        assert len(out["meshes"]) == 50
        # the full count is still honest, even though the array is capped
        assert out["count"] == 60

    def test_the_default_caps_the_array_when_no_max_results_is_named(self):
        # Every other cap test here names max_results, so none of them exercises the DEFAULT the
        # description promises - the number a caller who names no cap actually gets. 51 meshes with
        # nothing named must come back as 50, and the promise and the applied cap must be one number:
        # a signature carrying its own literal beside the interpolated description lets them drift.
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}", token=f"T{i}") for i in range(51)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target=""))
        assert len(out["meshes"]) == 50
        assert out["truncated"] is True and out["count"] == 51
        promised = mo.tool.to_dict()["inputSchema"]["properties"]["max_results"]["description"]
        assert len(out["meshes"]) == int(re.search(r"default (\d+)", promised).group(1))

    def test_a_zero_max_results_falls_back_to_the_default_not_the_ceiling(self):
        # The constant's SECOND use site: the 'default' argument handed to clamp_rows. The test
        # above only exercises the signature, which a request of 0 never reaches - 0 is a legal
        # wire value (integer, no minimum) and clamp_rows falls a falsy request back to its
        # 'default' argument. Handing the CEILING there instead reads identically until a caller
        # sends 0, and then 120 rows cross the wire where 50 should.
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}", token=f"T{i}") for i in range(120)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target="", max_results=0))
        assert len(out["meshes"]) == 50
        assert out["truncated"] is True and out["count"] == 120
        promised = mo.tool.to_dict()["inputSchema"]["properties"]["max_results"]["description"]
        assert len(out["meshes"]) == int(re.search(r"default (\d+)", promised).group(1))

    def test_a_caller_cannot_lift_the_cap_past_the_ceiling(self):
        # every row crosses the wire: max_results is clamped into 1..200, so an oversized
        # request is held at the ceiling, not honoured.
        _wire_adsk()
        meshes = [MeshBody(f"Scan{i}", token=f"T{i}") for i in range(210)]
        comp = FakeComp("Comp", meshes=meshes)
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target="", max_results=999999))
        assert len(out["meshes"]) == 200
        assert out["truncated"] is True and out["count"] == 210

    def test_reports_area_and_volume_scaled_to_units(self):
        # MeshBody.area/volume are cm^2/cm^3 (Fusion's internal units) - default units=mm scales by
        # inv_scale^2 / inv_scale^3 (10^2 / 10^3), the same cm-based idiom model_inspect uses.
        _wire_adsk()
        m = MeshBody("Scan", area=6.0, volume=2.0)     # cm^2, cm^3
        comp = FakeComp("Comp", meshes=[m])
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target=""))
        rec = out["meshes"][0]
        assert abs(rec["area"] - 600.0) < 1e-6      # 6 cm^2 -> 600 mm^2
        assert abs(rec["volume"] - 2000.0) < 1e-6   # 2 cm^3 -> 2000 mm^3
        assert out["units"] == "mm"

    def test_area_and_volume_respect_units_param(self):
        _wire_adsk()
        m = MeshBody("Scan", area=6.0, volume=2.0)     # cm^2, cm^3
        comp = FakeComp("Comp", meshes=[m])
        _install(FakeDesign(comp))
        out = _payload(mo.handler(target="", units="cm"))
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
        out = _payload(mo.handler(target=""))
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
        out = _payload(mo.handler(target=""))
        rec = out["meshes"][0]
        assert rec["volume"] is None
        assert abs(rec["area"] - 400.0) < 1e-6   # unaffected by the volume failure

    def test_the_note_tells_a_zero_volume_apart_from_a_null_one(self):
        # the wire note is the only place a reader learns which of the two a number/null means.
        _wire_adsk()
        comp = FakeComp("Comp", meshes=[MeshBody("Scan")])
        _install(FakeDesign(comp))
        note = _payload(mo.handler(target=""))["note"]
        assert "reads 0.0" in note and "is_closed=false" in note
        assert "could not be read" in note
        assert "is null for a mesh that is not watertight" not in note

    def test_unknown_units_rejected(self):
        _wire_adsk()
        comp = FakeComp("Comp", meshes=[MeshBody("Scan")])
        _install(FakeDesign(comp))
        res = mo.handler(target="", units="parsec")
        assert res["isError"] is True


class TestMeshMeasure:

    def test_measures_a_mesh_body(self):
        _wire_adsk()
        m = MeshBody("Scan", tri=999, nodes=500, bbox=make_bbox((0, 0, 0), (1, 2, 4)))
        out = _payload(mesh_common_mod.mesh_measure_of_body(m, units="mm"))
        assert out["triangle_count"] == 999 and out["node_count"] == 500
        assert out["is_closed"] is True
        # bbox scaled from cm -> mm (x10): 1cm,2cm,4cm -> 10,20,40
        assert abs(out["bbox"]["x"] - 10) < 1e-6
        assert abs(out["bbox"]["z"] - 40) < 1e-6

    def test_non_watertight_carries_warning(self):
        _wire_adsk()
        m = MeshBody("Open", is_closed=False)
        out = _payload(mesh_common_mod.mesh_measure_of_body(m))
        assert out["is_closed"] is False and "not watertight" in out["note"].lower()

    def test_measure_reports_area_volume_scaled(self):
        _wire_adsk()
        m = MeshBody("Scan", area=10.0, volume=5.0)
        out = _payload(mesh_common_mod.mesh_measure_of_body(m, units="mm"))
        assert abs(out["area"] - 1000.0) < 1e-6      # 10 cm^2 -> 1000 mm^2
        assert abs(out["volume"] - 5000.0) < 1e-6    # 5 cm^3 -> 5000 mm^3

    def test_measure_of_an_open_mesh_reports_volume_zero_and_says_why(self):
        # 0.0 is the measured open-mesh reading, so the note has to say the body is not empty - it
        # encloses nothing - or a caller reads the number as a vanished body.
        _wire_adsk()
        m = MeshBody("Open", is_closed=False, area=4.0, volume=0.0)
        out = _payload(mesh_common_mod.mesh_measure_of_body(m))
        assert out["volume"] == 0.0
        assert "reads 0.0" in out["note"] and "nothing enclosed" in out["note"]

    def test_measure_volume_null_when_the_field_cannot_be_read(self):
        _wire_adsk()
        m = MeshBody("Dead")   # area/volume UNSET -> both raise -> both null
        out = _payload(mesh_common_mod.mesh_measure_of_body(m))
        assert out["volume"] is None
        assert out["area"] is None


class TestMeshGetDescription:

    def test_the_description_states_the_measured_open_mesh_volume(self):
        # the description is the only thing an agent knows about the field before the first call,
        # so it carries the same 0.0-vs-null split the payload note does.
        desc = mo.tool.to_dict()["description"]
        assert "reads 0.0 on a mesh that is not watertight" in desc
        assert "null only when the field could not be read" in desc
        assert "'volume' is null for a mesh that is not watertight" not in desc


class TestAreaVolumeSignal:

    def test_reads_area_and_volume_in_internal_cm_units(self):
        assert mesh_common_mod._area_volume(MeshBody("M", area=150.0, volume=125.0)) == (150.0, 125.0)

    def test_an_unreadable_field_is_None_not_zero(self):
        # 0.0 is an ANSWER for MeshBody.volume (a body enclosing nothing), so an unreadable read
        # must not borrow it - a coerced 0.0 would read as "the geometry vanished".
        assert mesh_common_mod._area_volume(MeshBody("M")) == (None, None)
        assert mesh_common_mod._area_volume(MeshBody("M", area=12.0)) == (12.0, None)

    def test_a_moved_area_reports_movement(self):
        assert mesh_common_mod._mesh_moved((150.0, 125.0), (90.0, 125.0)) is True

    def test_a_moved_volume_alone_reports_movement(self):
        # Either signal is sufficient: a cut can shave volume while the surface area holds.
        assert mesh_common_mod._mesh_moved((150.0, 125.0), (150.0, 62.5)) is True

    def test_identical_readings_are_flat(self):
        assert mesh_common_mod._mesh_moved((150.0, 125.0), (150.0, 125.0)) is False

    def test_a_difference_inside_the_band_is_flat(self):
        # Float noise on a re-read is not a cut; the band is what keeps it from reading as one.
        assert mesh_common_mod._mesh_moved((150.0, 125.0), (150.0 + 1e-12, 125.0 - 1e-12)) is False

    def test_one_readable_signal_still_decides(self):
        assert mesh_common_mod._mesh_moved((None, 125.0), (None, 62.5)) is True
        assert mesh_common_mod._mesh_moved((None, 125.0), (None, 125.0)) is False

    def test_neither_signal_readable_is_UNKNOWN_never_flat(self):
        # None, not False: a caller that treated unknown as "flat" would refuse a landed cut it
        # simply could not measure.
        assert mesh_common_mod._mesh_moved((None, None), (None, None)) is None
        assert mesh_common_mod._mesh_moved((150.0, 125.0), (None, None)) is None
