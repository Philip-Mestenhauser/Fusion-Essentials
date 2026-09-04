"""Unit tests for surface_revolve.py - the axis, the angle extent and the sheet result."""

import json
import types
from conftest import load_tool, _NamedCollection

sc = load_tool("surface_revolve")


inp = sc._inputs


class FakeBody:
    def __init__(self, name="Surf1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class FakeFeature:
    def __init__(self, name="Surface1", bodies=None, extent_cm=None):
        self.name = name
        self.bodies = _NamedCollection(bodies if bodies is not None else [FakeBody()])
        if extent_cm is not None:
            # ExtrudeFeature.extentOne is a DistanceExtentDefinition (a SymmetricExtentDefinition
            # for a symmetric extrude) whose .distance is a ModelParameter reading CM, signed as
            # requested. extent_cm=None gives a feature whose extent cannot be read at all.
            self.extentOne = types.SimpleNamespace(
                distance=types.SimpleNamespace(value=extent_cm))


class FakeRevolveInput:
    def __init__(self, profile, axis, op):
        self.profile = profile
        self.axis = axis
        self.operation = op
        self.isSolid = None
        self.angle_extent = None
    def setAngleExtent(self, sym, ang):
        self.angle_extent = (sym, ang)
        return True


class FakeRevolveFeatures:
    def __init__(self, result_bodies=None):
        self.last_input = None
        self._result = result_bodies
    def createInput(self, profile, axis, op):
        self.last_input = FakeRevolveInput(profile, axis, op)
        return self.last_input
    def add(self, inp):
        return FakeFeature(bodies=self._result)


class FakeFeatures:
    def __init__(self, ef=None, rf=None, pf=None):
        self.extrudeFeatures = ef
        self.revolveFeatures = rf
        self.patchFeatures = pf


class _FakeSketchCurves:
    """Models adsk.fusion.SketchCurves: a flat, indexable collection (count + item(i)).
    _open_sketch_profile enumerates these into an ObjectCollection (the real createOpenProfile
    wants the individual curve entities, NOT the SketchCurves object)."""
    def __init__(self, n=2):
        self._items = [object() for _ in range(n)]

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


class FakeSketch:
    def __init__(self, name="Sketch1", curve_count=2):
        self.name = name
        self.sketchCurves = _FakeSketchCurves(curve_count)


class FakeSketches:
    def __init__(self, sketches):
        self._s = list(sketches)
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i] if 0 <= i < len(self._s) else None
    def itemByName(self, n):
        for s in self._s:
            if s.name == n:
                return s
        return None


class FakeComp:
    def __init__(self, features, sketches=None):
        self.features = features
        self.sketches = FakeSketches(sketches or [])
        self.xConstructionAxis = ("axis", "x")
        self.yConstructionAxis = ("axis", "y")
        self.zConstructionAxis = ("axis", "z")
        self._open_profile = ("open_profile", None)
        self._edge_profile = ("edge_profile", None)
    def createOpenProfile(self, curves, chained):
        return self._open_profile
    def createBRepEdgeProfile(self, edges):
        return self._edge_profile


class _CompColl:
    """A counted+iterable collection of components, as Design.allComponents is in the live API (a
    Component has NO such attribute - only the Design does)."""
    def __init__(self, comps):
        self._c = list(comps)
    @property
    def count(self):
        return len(self._c)
    def item(self, i):
        return self._c[i] if 0 <= i < len(self._c) else None
    def __iter__(self):
        return iter(self._c)


class FakeDesign:
    def __init__(self, comp):
        self._comp = comp
        self.rootComponent = comp
        self.activeComponent = comp
        self._all_components = [comp]

    @property
    def allComponents(self):
        # allComponents lives on the DESIGN only (never a Component), as in the live API; defaults to
        # just the root so single-component tests are unchanged.
        return _CompColl(self._all_components)


class _OC:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)


class FakeEdge:
    """Stands in for adsk.fusion.BRepEdge; .body identifies the owning body."""
    def __init__(self, body=None):
        self.body = body


class _UnreadableSolidBody:
    """A result body whose isSolid will not read - the shape bool(safe(...)) turned into a confident
    'this is an open sheet'."""
    def __init__(self, name="Surf1"):
        self.name = name

    @property
    def isSolid(self):
        raise RuntimeError("4 : An API Object refers to a deleted Object")


def _wire_adsk(handle_map=None):
    import adsk.fusion, adsk.core
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation", "NewComponentFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    adsk.fusion.BRepEdge = FakeEdge


def _install_multi(active, sub_components=()):
    """Like _install but the design spans several components: `active` is the ACTIVE/root component and
    each sub-component carries its own sketches/features. resolve_sketch walks design.allComponents to
    find a sketch owned by a sub-component (the master-sketch shape)."""
    design = FakeDesign(active)
    design._all_components = [active] + list(sub_components)
    design.findEntityByToken = lambda t: []
    sc.app = type("A", (), {"activeProduct": design})()
    sc._common.app = sc.app
    sc._common.design = lambda: design
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    _wire_adsk({})
    inp._common.design = lambda: design
    inp._common.target_component = lambda d: active
    sc._common.target_component = lambda d: active
    return design


def _install(comp, handle_map=None):
    handle_map = handle_map or {}
    design = FakeDesign(comp)
    # The handler resolves its design via _common.design() (the SAME seam _inputs uses for handle
    # resolution), so ONE design serves both: give the real FakeDesign the handle lookup, and point
    # both _common.design and _inputs._common.design at it.
    design.findEntityByToken = lambda t, hm=handle_map: ([hm[t]] if t in hm else [])
    sc.app = type("A", (), {"activeProduct": design})()
    sc._common.app = sc.app
    sc._common.design = lambda: design
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    _wire_adsk(handle_map)
    inp._common.design = lambda: design
    # the handler resolves curves/boundary through _inputs; point those at our comp too
    inp._common.target_component = lambda d: comp
    sc._common.target_component = lambda d: comp


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestEmptyResultSetIsAnError:

    """'created: true' beside result_bodies [] claims a sheet the payload cannot show - and leaves
    is_solid with nothing to read off, so the sheet/solid note is narrated from nothing."""

    def test_revolve_with_no_result_body_is_an_error(self):
        rf = FakeRevolveFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", angle_deg=180)
        assert res["isError"] is True
        assert "owns no result body" in res["message"]

    def test_one_revolve_body_is_the_boundary_that_passes(self):
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", angle_deg=180))
        assert out["result_bodies"] == ["Surf1"]

    def test_revolve_unreadable_is_solid_is_null_and_unverified(self):
        rf = FakeRevolveFeatures(result_bodies=[_UnreadableSolidBody("Surf1")])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", angle_deg=180))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "isSolid=false" not in out["note"]


class TestSurfaceRevolve:

    def test_sets_isSolid_false(self):
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", axis="y", angle_deg=180))
        assert out["is_solid"] is False
        assert out["axis"] == "y-axis"
        assert rf.last_input.isSolid is False

    def test_reports_result_is_solid_read_back(self):
        # is_solid is read back from the body, not assumed; a sheet revolve makes an open shell
        # (is_solid False, verified live) and SUCCEEDS rather than rejecting.
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Body1", is_solid=False)])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", angle_deg=360))
        assert out["created"] is True and out["is_solid"] is False

    def test_zero_angle_guard(self):
        comp = FakeComp(FakeFeatures(rf=FakeRevolveFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", angle_deg=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_non_numeric_angle_rejected(self):
        comp = FakeComp(FakeFeatures(rf=FakeRevolveFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", angle_deg="lots")
        assert res["isError"] is True and "number" in res["message"]

    def test_unknown_axis_rejected(self):
        comp = FakeComp(FakeFeatures(rf=FakeRevolveFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", angle_deg=90, axis="w")
        assert res["isError"] is True and "x, y, or z" in res["message"]

    def test_surface_revolve_built_on_the_sketchs_owning_component(self):
        # Named sketch owned by a SUB-component while a different component is active. Both the profile
        # and the origin axis must come from the OWNER (an axis from the wrong component mixes contexts,
        # and the profile-consuming feature raises bSet on the active component). Proven by which
        # revolveFeatures object got the call.
        owner_rf = FakeRevolveFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        owned_sketch = FakeSketch("OwnedSketch")
        owner = FakeComp(FakeFeatures(rf=owner_rf), sketches=[owned_sketch])
        owned_sketch.parentComponent = owner
        active_rf = FakeRevolveFeatures(result_bodies=[FakeBody("X", is_solid=False)])
        active = FakeComp(FakeFeatures(rf=active_rf), sketches=[])
        _install_multi(active, sub_components=[owner])
        out = _payload(sc.handler(sketch_name="OwnedSketch", angle_deg=180))
        assert out["created"] is True
        assert owner_rf.last_input is not None      # the OWNER built the surface revolve
        assert active_rf.last_input is None         # NOT the active component (the bSet trap)
