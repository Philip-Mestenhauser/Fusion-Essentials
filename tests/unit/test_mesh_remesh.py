"""Unit tests for mesh_remesh.py - the retriangulation and its density read-back."""

import json
from conftest import load_tool, make_bbox
import adsk.fusion  # noqa: E402

mo = load_tool("mesh_remesh")


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


class TestMeshRemesh:

    def test_remesh_reports_before_after(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        result = MeshBody("Scan", tri=1500)
        feats = _MeshFeatures([result])
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mo.handler(mesh="H"))
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
        out = _payload(mo.handler(mesh="H"))
        assert out["changed"] is False
        assert "unchanged" in out["note"]

    def test_missing_remesh_features_collection_errors(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        comp = FakeComp("Comp", features=_Features(remesh=None))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mo.handler(mesh="H")
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
        out = _payload(mo.handler(mesh="H"))
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
        out = _payload(mo.handler(mesh="H"))
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
        out = _payload(mo.handler(mesh="H"))
        assert out["remeshed"] is True
        assert out["after"]["triangle_count"] == 1500
        assert bf.started is True and bf.finished is True


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
        out = _payload(mo.handler(mesh="H", density=12))
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
        res = mo.handler(mesh="H", density=12)
        assert res["isError"] is True and "did not land" in res["message"]

    def test_no_density_asks_for_no_read_back(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=2000)
        feats = _MeshFeatures([MeshBody("Scan", tri=900)])
        comp = FakeComp("Comp", features=_Features(remesh=feats))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        out = _payload(mo.handler(mesh="H"))
        assert "density_applied" not in out
