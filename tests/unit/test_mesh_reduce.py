"""Unit tests for mesh_reduce.py - the decimation targets and the unreduced-count gate."""

import json
from conftest import load_tool, make_bbox
import adsk.fusion  # noqa: E402

mo = load_tool("mesh_reduce")


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
        out = _payload(mo.handler(mesh="H", target="proportion", value=30))
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
        res = mo.handler(mesh="H", target="proportion", value=150)
        assert res["isError"] is True and "percent" in res["message"]

    def test_facecount_sets_lowercase_field_as_valueinput(self):
        src, feats = self._setup()
        out = _payload(mo.handler(mesh="H", target="face_count", value=500))
        assert out["reduced"] is True
        # 'facecount' is set as a ValueInput, not a raw int (the API rejects a bare number); the count
        # is carried as a real (500.0).
        vi = getattr(feats.last_input, "facecount", None)
        assert isinstance(vi, _FakeValueInput)
        assert abs(vi.real - 500.0) < 1e-9

    def test_facecount_below_one_rejected(self):
        self._setup()
        res = mo.handler(mesh="H", target="face_count", value=0)
        assert res["isError"] is True and "positive" in res["message"].lower()

    def test_facecount_negative_rejected(self):
        self._setup()
        res = mo.handler(mesh="H", target="face_count", value=-5)
        assert res["isError"] is True and "positive" in res["message"].lower()

    def test_a_fractional_facecount_under_one_is_refused_naming_it(self):
        # 0.5 truncated to int is a ZERO-face target - a request the after<before gate reads as a
        # successful reduce. It is refused instead, and the refusal names the offending value.
        src, feats = self._setup()
        res = mo.handler(mesh="H", target="face_count", value=0.5)
        assert res["isError"] is True
        assert "0.5" in res["message"] and "WHOLE face count" in res["message"]
        assert feats.last_input is None            # nothing was configured, nothing ran

    def test_a_fractional_facecount_is_refused_rather_than_silently_truncated(self):
        # 10.9 -> 10 would decimate to a target the caller never asked for, with no echo of the shift.
        src, feats = self._setup()
        res = mo.handler(mesh="H", target="face_count", value=10.9)
        assert res["isError"] is True
        assert "10.9" in res["message"]
        assert feats.last_input is None

    def test_the_smallest_whole_facecount_is_accepted(self):
        # 1 is the boundary the > 0 guard admits - a whole count, so it runs.
        src, feats = self._setup()
        out = _payload(mo.handler(mesh="H", target="face_count", value=1))
        assert out["reduced"] is True
        assert out["face_count_target"] == 1
        assert abs(feats.last_input.facecount.real - 1.0) < 1e-9

    def test_an_integral_float_facecount_is_accepted_and_echoed(self):
        # 10.0 IS a whole count (the wire carries numbers, not ints) - accepted, and the integer that
        # reached the feature input is published so the caller can see what was targeted.
        src, feats = self._setup()
        out = _payload(mo.handler(mesh="H", target="face_count", value=10.0))
        assert out["reduced"] is True
        assert out["face_count_target"] == 10
        assert abs(feats.last_input.facecount.real - 10.0) < 1e-9

    def test_the_applied_facecount_target_is_only_published_for_face_count(self):
        # a proportion reduce has no face-count target to report
        src, feats = self._setup()
        out = _payload(mo.handler(mesh="H", target="proportion", value=30))
        assert "face_count_target" not in out

    def test_max_deviation_sets_valueinput_scaled_to_cm(self):
        src, feats = self._setup()
        # max_deviation is a LENGTH: 1 mm input -> 0.1 cm handed to the ValueInput.
        out = _payload(mo.handler(mesh="H", target="max_deviation", value=1, units="mm"))
        assert out["reduced"] is True
        vi = getattr(feats.last_input, "maximumDeviation", None)
        assert isinstance(vi, _FakeValueInput)
        assert abs(vi.real - 0.1) < 1e-9

    def test_add_failure_surfaces(self):
        self._setup(raise_on_add=True)
        res = mo.handler(mesh="H", target="proportion", value=50)
        assert res["isError"] is True and "failed" in res["message"].lower()

    def test_non_numeric_value_rejected(self):
        self._setup()
        res = mo.handler(mesh="H", target="proportion", value="lots")
        assert res["isError"] is True and "number" in res["message"]

    def test_max_deviation_below_zero_rejected(self):
        self._setup()
        res = mo.handler(mesh="H", target="max_deviation", value=-1)
        assert res["isError"] is True and "positive length" in res["message"]

    def test_missing_reduce_features_collection_errors(self):
        _wire_adsk()
        src = MeshBody("Scan", tri=1000)
        comp = FakeComp("Comp", features=_Features(reduce=None))
        src.parentComponent = comp
        _install(FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = mo.handler(mesh="H", target="proportion", value=50)
        assert res["isError"] is True
        assert "meshReduceFeatures collection" in res["message"]

    def test_create_input_none_errors(self):
        src, feats = self._setup()
        feats.createInput = lambda *a: None
        res = mo.handler(mesh="H", target="proportion", value=50)
        assert res["isError"] is True and "returned nothing" in res["message"]

    def test_slow_note_for_large_source_mesh(self):
        # a SOURCE mesh above the slow threshold carries the fire-and-poll advisory note
        src, feats = self._setup(before_tri=300_000, after_tri=100_000)
        out = _payload(mo.handler(mesh="H", target="proportion", value=33))
        assert "30s" in out["note"] and "300000" in out["note"]

    def test_no_slow_note_for_small_mesh(self):
        src, feats = self._setup(before_tri=1000, after_tri=300)
        out = _payload(mo.handler(mesh="H", target="proportion", value=30))
        assert "note" not in out

    def test_none_feature_is_success_in_place(self):
        # add() returns None in a DIRECT design; mesh_reduce edits the mesh in place, so success is
        # the mesh's updated triangle count, not the None feature return.
        src, feats = self._setup(before_tri=1000, none_feature=True)
        # the in-place reduction lands DURING add(): before_tri (read first) stays 1000, after = 250
        feats._on_add = lambda: setattr(src.displayMesh, "triangleCount", 250)
        out = _payload(mo.handler(mesh="H", target="proportion", value=25))
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
        out = _payload(mo.handler(mesh="H", target="proportion", value=40))
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
        out = _payload(mo.handler(mesh="H", target="proportion", value=40))
        assert out["design_mode"] == "parametric"

    def test_unreduced_count_is_an_error_not_success(self):
        # the honesty gate: add() succeeded but the triangle count did not decrease -> error, not ok
        src, feats = self._setup(before_tri=1000, after_tri=1000)
        res = mo.handler(mesh="H", target="proportion", value=30)
        assert res["isError"] is True
        assert "did not decrease" in res["message"]

    def test_proportion_100_keep_everything_is_not_gated(self):
        # proportion=100 asks to keep every triangle - an unchanged count is the requested outcome
        src, feats = self._setup(before_tri=1000, after_tri=1000)
        out = _payload(mo.handler(mesh="H", target="proportion", value=100))
        assert out["reduced"] is True

    def test_parametric_routes_through_base_feature_scope(self):
        # REGRESSION: in PARAMETRIC the createInput->set->add runs INSIDE the helper's base-feature
        # scope (opened AND finished). The feature add must not be defeated by an undetectable-scope
        # guard — it succeeds.
        bf = _BaseFeature()
        src, feats = self._setup(before_tri=1000, after_tri=400, parametric=True, base_feature=bf,
                                 none_feature=True)
        feats._on_add = lambda: setattr(src.displayMesh, "triangleCount", 400)
        out = _payload(mo.handler(mesh="H", target="proportion", value=40))
        assert out["reduced"] is True
        assert out["after"]["triangle_count"] == 400
        # the reduce ran inside the helper-opened base-feature scope (leak-proof open/close)
        assert bf.started is True and bf.finished is True
