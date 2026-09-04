"""Unit tests for surface_extrude.py - the open profile, the sheet result and its depth."""

import json
import types
import pytest
from conftest import load_tool, _NamedCollection

sc = load_tool("surface_extrude")
surface_revolve = load_tool("surface_revolve")


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


class FakeExtrudeInput:
    def __init__(self, profile, op):
        self.profile = profile
        self.operation = op
        self.isSolid = None
        self.distance_extent = None
    def setDistanceExtent(self, sym, dist):
        self.distance_extent = (sym, dist)
        return True


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


class FakeExtrudeFeatures:
    def __init__(self, result_bodies=None, landed_cm=None, extent_readable=True):
        # landed_cm: the depth the created feature's extent parameter reads back, when it differs
        # from the depth handed to setDistanceExtent (live, the two agree). extent_readable=False
        # models a feature whose extent parameter cannot be read at all.
        self.last_input = None
        self.added = False
        self._result = result_bodies
        self._landed_cm = landed_cm
        self._extent_readable = extent_readable
    def createInput(self, profile, op):
        self.last_input = FakeExtrudeInput(profile, op)
        return self.last_input
    def add(self, inp):
        self.added = True
        extent = None
        if self._extent_readable:
            extent = (self._landed_cm if self._landed_cm is not None
                      else inp.distance_extent[1][1])
        return FakeFeature(bodies=self._result, extent_cm=extent)


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


class TestSurfaceExtrude:

    def test_sets_isSolid_false_and_reports_it(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5, units="mm"))
        assert out["created"] is True
        assert out["is_solid"] is False
        assert ef.last_input.isSolid is False        # the surface switch was actually set
        sym, dist = ef.last_input.distance_extent
        assert dist == ("real", 0.5)                 # 5 mm -> 0.5 cm
        assert ef.last_input.operation == "NewBodyFeatureOperation"

    def test_reports_result_is_solid_read_back(self):
        # is_solid is READ BACK from the result body, not assumed. With createOpenProfile + isSolid=False
        # a closed boundary makes an open sheet/tube (is_solid False, verified live), so the tool
        # SUCCEEDS — it doesn't reject; it reports what the body actually is.
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Body1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5))
        assert out["created"] is True and out["is_solid"] is False

    def test_solid_result_contradicts_the_sheet_note(self):
        # a result that reads back SOLID must not carry the open-surface note - the note reports
        # the observed body, not the intent
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Body1", is_solid=True)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5))
        assert out["is_solid"] is True
        assert "SOLID" in out["note"]
        assert "Open surface body created" not in out["note"]

    def test_zero_distance_guard(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", distance=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_unknown_operation_rejected(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", distance=5, operation="cut")
        assert res["isError"] is True and "new, join" in res["message"]

    def test_from_edge_curves_uses_edge_profile(self):
        e1, e2 = FakeEdge(), FakeEdge()
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef))
        _install(comp, handle_map={"E1": e1, "E2": e2})
        out = _payload(sc.handler(curves=["E1", "E2"], distance=3))
        assert out["is_solid"] is False
        assert out["open_edge_count"] == 2
        # B-Rep edges -> createBRepEdgeProfile path
        assert ef.last_input.profile == ("edge_profile", None)

    def test_no_sketch_no_curves_errors(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[])
        _install(comp)
        res = sc.handler(distance=5)
        assert res["isError"] is True
        assert "No sketch or 'curves' to extrude" in res["message"]

    def test_unknown_units_rejected(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", distance=5, units="furlong")
        assert res["isError"] is True
        assert "furlong" in res["message"] and "mm, cm, or in" in res["message"]

    def test_join_op_and_symmetric_passed_through(self):
        # operation=join maps to the JoinFeatureOperation enum; symmetric flows to setDistanceExtent
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5, operation="join",
                                          symmetric=True))
        assert out["operation"] == "join"
        assert out["symmetric"] is True
        assert ef.last_input.operation == "JoinFeatureOperation"
        sym, _dist = ef.last_input.distance_extent
        assert sym is True

    def test_sketch_with_no_curves_errors(self):
        # _open_sketch_profile: a sketch present but with zero curves -> honest error, no add()
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()),
                        sketches=[FakeSketch("Empty", curve_count=0)])
        _install(comp)
        res = sc.handler(sketch_name="Empty", distance=5)
        assert res["isError"] is True and "no curves" in res["message"].lower()

    def test_depth_that_reads_back_wrong_is_an_error(self):
        # the extrude landed a depth Fusion took, not the one asked for -> error, never an ok
        # payload echoing the request as if it were the sheet's depth
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)], landed_cm=0.37)
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", distance=5, units="mm")
        assert res["isError"] is True
        assert "reads back 3.7" in res["message"] and "requested 5.0" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_depth_read_off_the_feature_is_published(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5, units="mm"))
        assert out["distance"] == 5.0            # the feature's own extent, in the caller's units
        assert "unverified" not in out

    def test_a_flipped_depth_is_an_error_not_a_magnitude_match(self):
        # a -15 mm request that landed +15 mm points the sheet the other way; only a SIGNED
        # comparison catches it (the extent parameter keeps the requested sign, measured)
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)], landed_cm=1.5)
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", distance=-15, units="mm")
        assert res["isError"] is True
        assert "reads back 15.0" in res["message"] and "requested -15.0" in res["message"]

    def test_unreadable_depth_is_flagged_unverified_not_silently_echoed(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                                 extent_readable=False)
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5, units="mm"))
        assert out["unverified"] == ["distance"]
        assert "Not read back off the feature: distance." in out["note"]
        assert out["distance"] == 5.0            # the request, published only because it is flagged

    def test_surface_extrude_built_on_the_sketchs_owning_component(self):
        # The named sketch lives in a SUB-component (its parentComponent) while a DIFFERENT component
        # is active. Building the open profile + feature on the active component while the sketch is
        # owned elsewhere raises bSet, so both must be built on the sketch's OWNER. The active
        # comp and the owner carry SEPARATE extrudeFeatures; the test proves the owner's got the call.
        owner_ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        owned_sketch = FakeSketch("OwnedSketch")
        owner = FakeComp(FakeFeatures(ef=owner_ef), sketches=[owned_sketch])
        owned_sketch.parentComponent = owner
        active_ef = FakeExtrudeFeatures(result_bodies=[FakeBody("X", is_solid=False)])
        active = FakeComp(FakeFeatures(ef=active_ef), sketches=[])
        _install_multi(active, sub_components=[owner])
        out = _payload(sc.handler(sketch_name="OwnedSketch", distance=5))
        assert out["created"] is True
        assert owner_ef.last_input is not None      # the OWNER built the surface extrude
        assert active_ef.last_input is None         # NOT the active component (the bSet trap)


class TestSketchNameResolution:

    """The name-or-most-recent sketch branch, which surface_extrude and surface_revolve each carry
    (both taken only when 'curves' is empty)."""

    @staticmethod
    def _call(which, sketches, **kwargs):
        if which == "extrude":
            ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
            _install(FakeComp(FakeFeatures(ef=ef), sketches=sketches))
            return sc.handler(distance=5, **kwargs)
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        _install(FakeComp(FakeFeatures(rf=rf), sketches=sketches))
        return surface_revolve.handler(angle_deg=180, **kwargs)

    @pytest.mark.parametrize("which", ["extrude", "revolve"])
    def test_a_padded_sketch_name_is_reported_stripped(self, which):
        # the walk searches the STRIPPED name, so the miss must name that one - quoting the padded
        # input sends the caller looking for a sketch whose name carries the spaces it typed.
        res = self._call(which, [FakeSketch("S")], sketch_name="  Ghost  ")
        assert res["isError"] is True
        assert "No sketch named 'Ghost'" in res["message"]
        assert "'  Ghost  '" not in res["message"]

    @pytest.mark.parametrize("which,verb", [("extrude", "extrude"), ("revolve", "revolve")])
    def test_a_blank_sketch_name_with_no_sketch_never_quotes_none(self, which, verb):
        # a blank name leaves the requested name None, so the named-miss wording would print
        # "No sketch named 'None'" - a sketch nobody asked for. The blank branch words its own.
        res = self._call(which, [], sketch_name="")
        assert res["isError"] is True
        assert res["message"] == (f"No sketch or 'curves' to {verb}. Draw an OPEN chain first, or "
                                  "pass curves.")
        assert "'None'" not in res["message"]

    @pytest.mark.parametrize("module_name", ["surface_extrude", "surface_revolve"])
    def test_the_component_scope_is_declared_on_the_wire(self, module_name):
        # both schemas are strict, so a handler parameter no property declares is refused before it
        # reaches the handler - the scope would be unreachable and its refusal would name it anyway.
        sd = load_tool("_sketch_detail")
        schema = load_tool(module_name).tool.input_schema["properties"]
        assert schema["component"] == sd.COMPONENT_SCOPE[1]

    @pytest.mark.parametrize("which", ["extrude", "revolve"])
    def test_the_component_scope_reaches_the_sketch_resolve(self, which, monkeypatch):
        # a sketch name two components carry is Fusion's default state, so each of these two
        # handlers needs its own 'component' to say which one it means - and must hand it to the
        # shared resolver, whose refusals name that same input back.
        seen = {}

        def _scoped(design, name, component, input_name="component"):
            seen.update(component=component, input_name=input_name)
            return None, name, "refused"

        monkeypatch.setattr(sc._sketch_detail, "scoped_or_recent_sketch", _scoped)
        res = self._call(which, [FakeSketch("S")], sketch_name="S", component="Frame")
        assert res["isError"] is True and res["message"] == "refused"
        assert seen == {"component": "Frame", "input_name": "component"}

    @pytest.mark.parametrize("which", ["extrude", "revolve"])
    def test_a_whitespace_only_sketch_name_uses_the_most_recent_sketch(self, which):
        # ' ' strips to blank, which is the most-recent-sketch request - not a search for a sketch
        # named with a space.
        out = _payload(self._call(which, [FakeSketch("First"), FakeSketch("Last")],
                                  sketch_name=" "))
        assert out["source"] == "Last"


class TestEmptyResultSetIsAnError:

    """'created: true' beside result_bodies [] claims a sheet the payload cannot show - and leaves
    is_solid with nothing to read off, so the sheet/solid note is narrated from nothing."""

    def test_extrude_with_no_result_body_is_an_error(self):
        ef = FakeExtrudeFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.handler(sketch_name="S", distance=5)
        assert res["isError"] is True
        assert "owns no result body" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_one_extrude_body_is_the_boundary_that_passes(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5))
        assert out["result_bodies"] == ["Surf1"]

    def test_extrude_unreadable_is_solid_is_null_and_unverified(self):
        ef = FakeExtrudeFeatures(result_bodies=[_UnreadableSolidBody("Surf1")])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.handler(sketch_name="S", distance=5))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "UNVERIFIED" in out["note"]
        assert "isSolid=false" not in out["note"]
