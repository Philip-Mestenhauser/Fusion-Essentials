"""Unit tests for surface_create.py — CREATE open (non-solid) surface bodies.

Pins the surface discriminator: surface_extrude/revolve set isSolid=False and REPORT is_solid=false,
and REJECT a result that came back solid (a closed profile slipped through). surface_patch fills a
closed edge loop (single edge auto-completes; multi-edge collection). No live Fusion — fake feature
classes capture what was passed in.
"""

import json
import types

import pytest

from conftest import load_tool

sc = load_tool("surface_create")
inp = sc._inputs


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeBody:
    def __init__(self, name="Surf1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class FakeBodies:
    def __init__(self, bodies):
        self._b = list(bodies)
    @property
    def count(self):
        return len(self._b)
    def item(self, i):
        return self._b[i]


class FakeFeature:
    def __init__(self, name="Surface1", bodies=None, extent_cm=None):
        self.name = name
        self.bodies = FakeBodies(bodies if bodies is not None else [FakeBody()])
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


class _RailsColl:
    """What interiorRailsAndPoints reads back: a FRESH ObjectCollection on every read (never the
    object assigned), whose count reads None when it is EMPTY. Both measured live."""
    def __init__(self, items):
        self.items = list(items)

    @property
    def count(self):
        return len(self.items) or None


class FakePatchInput:
    def __init__(self, boundary, op, rails_dropped=0):
        self.boundary = boundary
        self.operation = op
        self.continuity = None
        self._rails = []
        self._rails_dropped = rails_dropped

    @property
    def interiorRailsAndPoints(self):
        kept = self._rails[:len(self._rails) - self._rails_dropped]
        return _RailsColl(kept)

    @interiorRailsAndPoints.setter
    def interiorRailsAndPoints(self, coll):
        self._rails = list(getattr(coll, "items", []))


class _ContinuityRejectingPatchInput:
    """A patch input whose .continuity setter raises - models the API rejecting the value. The set
    must NOT be wrapped in safe(): a failed patch surfaces as an error, never a silent no-op that
    reports success."""
    def __init__(self, boundary, op):
        self.boundary = boundary
        self.operation = op
    def __setattr__(self, name, value):
        if name == "continuity":
            raise RuntimeError("continuity rejected by the API")
        object.__setattr__(self, name, value)


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


class FakePatchFeatures:
    def __init__(self, result_bodies=None, feature=True, raises=None, rails_dropped=0):
        # raises: add() raises this message - models the kernel refusing the patch
        # (e.g. a tangent saddle opening the single-seed auto-complete cannot chain).
        # rails_dropped: how many assigned rails the input fails to keep, so the count read-back
        # disagrees with what was assigned.
        self.last_input = None
        self._result = result_bodies
        self._feature = feature
        self._raises = raises
        self._rails_dropped = rails_dropped
    def createInput(self, boundary, op):
        self.last_input = FakePatchInput(boundary, op, rails_dropped=self._rails_dropped)
        return self.last_input
    def add(self, inp):
        if self._raises:
            raise RuntimeError(self._raises)
        if not self._feature:
            return None
        return FakeFeature(name="Patch1", bodies=self._result)


class FakePatchFeaturesRejectContinuity(FakePatchFeatures):
    """createInput returns a patch input that raises when 'continuity' is set."""
    def createInput(self, boundary, op):
        self.last_input = _ContinuityRejectingPatchInput(boundary, op)
        return self.last_input


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
    # SurfaceContinuityTypes members are NOT installed here: a test asserts against
    # adsk.fusion.SurfaceContinuityTypes.<member> itself, so the value comes from the mock/seeded
    # enum rather than a local sentinel.
    # handle resolution is attached to the real design in _install now (see below) — this only wires
    # the adsk enum stand-ins.


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
    # surface_create resolves curves/boundary through _inputs; point those at our comp too
    inp._common.target_component = lambda d: comp
    sc._common.target_component = lambda d: comp


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── surface_extrude ─────────────────────────────────────────────────────────

class TestSurfaceExtrude:
    def test_sets_isSolid_false_and_reports_it(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5, units="mm"))
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
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5))
        assert out["created"] is True and out["is_solid"] is False

    def test_solid_result_contradicts_the_sheet_note(self):
        # a result that reads back SOLID must not carry the open-surface note - the note reports
        # the observed body, not the intent
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Body1", is_solid=True)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5))
        assert out["is_solid"] is True
        assert "SOLID" in out["note"]
        assert "Open surface body created" not in out["note"]

    def test_zero_distance_guard(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.extrude_handler(sketch_name="S", distance=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_unknown_operation_rejected(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.extrude_handler(sketch_name="S", distance=5, operation="cut")
        assert res["isError"] is True and "new, join" in res["message"]

    def test_from_edge_curves_uses_edge_profile(self):
        e1, e2 = FakeEdge(), FakeEdge()
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef))
        _install(comp, handle_map={"E1": e1, "E2": e2})
        out = _payload(sc.extrude_handler(curves=["E1", "E2"], distance=3))
        assert out["is_solid"] is False
        assert out["open_edge_count"] == 2
        # B-Rep edges -> createBRepEdgeProfile path
        assert ef.last_input.profile == ("edge_profile", None)

    def test_no_sketch_no_curves_errors(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[])
        _install(comp)
        res = sc.extrude_handler(distance=5)
        assert res["isError"] is True
        assert "No sketch or 'curves' to extrude" in res["message"]

    def test_unknown_units_rejected(self):
        comp = FakeComp(FakeFeatures(ef=FakeExtrudeFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.extrude_handler(sketch_name="S", distance=5, units="furlong")
        assert res["isError"] is True
        assert "furlong" in res["message"] and "mm, cm, or in" in res["message"]

    def test_join_op_and_symmetric_passed_through(self):
        # operation=join maps to the JoinFeatureOperation enum; symmetric flows to setDistanceExtent
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5, operation="join",
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
        res = sc.extrude_handler(sketch_name="Empty", distance=5)
        assert res["isError"] is True and "no curves" in res["message"].lower()

    def test_depth_that_reads_back_wrong_is_an_error(self):
        # the extrude landed a depth Fusion took, not the one asked for -> error, never an ok
        # payload echoing the request as if it were the sheet's depth
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)], landed_cm=0.37)
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.extrude_handler(sketch_name="S", distance=5, units="mm")
        assert res["isError"] is True
        assert "reads back 3.7" in res["message"] and "requested 5.0" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_depth_read_off_the_feature_is_published(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5, units="mm"))
        assert out["distance"] == 5.0            # the feature's own extent, in the caller's units
        assert "unverified" not in out

    def test_a_flipped_depth_is_an_error_not_a_magnitude_match(self):
        # a -15 mm request that landed +15 mm points the sheet the other way; only a SIGNED
        # comparison catches it (the extent parameter keeps the requested sign, measured)
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)], landed_cm=1.5)
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.extrude_handler(sketch_name="S", distance=-15, units="mm")
        assert res["isError"] is True
        assert "reads back 15.0" in res["message"] and "requested -15.0" in res["message"]

    def test_unreadable_depth_is_flagged_unverified_not_silently_echoed(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)],
                                 extent_readable=False)
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5, units="mm"))
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
        out = _payload(sc.extrude_handler(sketch_name="OwnedSketch", distance=5))
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
            return sc.extrude_handler(distance=5, **kwargs)
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        _install(FakeComp(FakeFeatures(rf=rf), sketches=sketches))
        return sc.revolve_handler(angle_deg=180, **kwargs)

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

    @pytest.mark.parametrize("tool_name", ["surface_extrude_tool", "surface_revolve_tool"])
    def test_the_component_scope_is_declared_on_the_wire(self, tool_name):
        # both schemas are strict, so a handler parameter no property declares is refused before it
        # reaches the handler - the scope would be unreachable and its refusal would name it anyway.
        sd = load_tool("_sketch_detail")
        schema = getattr(sc, tool_name).input_schema["properties"]
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
        res = sc.extrude_handler(sketch_name="S", distance=5)
        assert res["isError"] is True
        assert "owns no result body" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_one_extrude_body_is_the_boundary_that_passes(self):
        ef = FakeExtrudeFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5))
        assert out["result_bodies"] == ["Surf1"]

    def test_revolve_with_no_result_body_is_an_error(self):
        rf = FakeRevolveFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.revolve_handler(sketch_name="S", angle_deg=180)
        assert res["isError"] is True
        assert "owns no result body" in res["message"]

    def test_one_revolve_body_is_the_boundary_that_passes(self):
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.revolve_handler(sketch_name="S", angle_deg=180))
        assert out["result_bodies"] == ["Surf1"]

    def test_extrude_unreadable_is_solid_is_null_and_unverified(self):
        ef = FakeExtrudeFeatures(result_bodies=[_UnreadableSolidBody("Surf1")])
        comp = FakeComp(FakeFeatures(ef=ef), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.extrude_handler(sketch_name="S", distance=5))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "UNVERIFIED" in out["note"]
        assert "isSolid=false" not in out["note"]

    def test_revolve_unreadable_is_solid_is_null_and_unverified(self):
        rf = FakeRevolveFeatures(result_bodies=[_UnreadableSolidBody("Surf1")])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.revolve_handler(sketch_name="S", angle_deg=180))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "isSolid=false" not in out["note"]


# ── surface_revolve ─────────────────────────────────────────────────────────

class TestSurfaceRevolve:
    def test_sets_isSolid_false(self):
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Surf1", is_solid=False)])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.revolve_handler(sketch_name="S", axis="y", angle_deg=180))
        assert out["is_solid"] is False
        assert out["axis"] == "y-axis"
        assert rf.last_input.isSolid is False

    def test_reports_result_is_solid_read_back(self):
        # is_solid is read back from the body, not assumed; a sheet revolve makes an open shell
        # (is_solid False, verified live) and SUCCEEDS rather than rejecting.
        rf = FakeRevolveFeatures(result_bodies=[FakeBody("Body1", is_solid=False)])
        comp = FakeComp(FakeFeatures(rf=rf), sketches=[FakeSketch("S")])
        _install(comp)
        out = _payload(sc.revolve_handler(sketch_name="S", angle_deg=360))
        assert out["created"] is True and out["is_solid"] is False

    def test_zero_angle_guard(self):
        comp = FakeComp(FakeFeatures(rf=FakeRevolveFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.revolve_handler(sketch_name="S", angle_deg=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_non_numeric_angle_rejected(self):
        comp = FakeComp(FakeFeatures(rf=FakeRevolveFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.revolve_handler(sketch_name="S", angle_deg="lots")
        assert res["isError"] is True and "number" in res["message"]

    def test_unknown_axis_rejected(self):
        comp = FakeComp(FakeFeatures(rf=FakeRevolveFeatures()), sketches=[FakeSketch("S")])
        _install(comp)
        res = sc.revolve_handler(sketch_name="S", angle_deg=90, axis="w")
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
        out = _payload(sc.revolve_handler(sketch_name="OwnedSketch", angle_deg=180))
        assert out["created"] is True
        assert owner_rf.last_input is not None      # the OWNER built the surface revolve
        assert active_rf.last_input is None         # NOT the active component (the bSet trap)


# ── surface_patch ───────────────────────────────────────────────────────────

class TestSurfacePatch:
    def test_patch_over_closed_edge_loop(self):
        e1, e2, e3 = FakeEdge(), FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "E2": e2, "E3": e3})
        out = _payload(sc.patch_handler(boundary=["E1", "E2", "E3"]))
        assert out["patched"] is True
        assert out["is_solid"] is False
        assert out["boundary_edge_count"] == 3
        # multiple edges -> an ObjectCollection was passed as the boundary
        assert isinstance(pf.last_input.boundary, _OC)

    def test_single_edge_passes_edge_for_autocomplete(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.patch_handler(boundary="E1"))
        assert out["patched"] is True
        # a single edge is passed directly (Fusion auto-finds the loop), NOT wrapped in a collection
        assert pf.last_input.boundary is e1

    def test_null_feature_errors(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(feature=False)
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1")
        assert res["isError"] is True and "no feature" in res["message"]

    def test_chain_failure_names_both_known_causes_not_just_tangent_saddle(self):
        # a single-seed patch failure ('invalid argument chainOptions', live-verified) has TWO known
        # causes that read identically from the exception string alone: a degenerate TANGENT saddle
        # opening, OR an edge loop SPLIT into more segments by a later feature (e.g. a rim fillet).
        # The message must name BOTH causes and both recipes, never assert tangent-saddle alone.
        e1 = FakeEdge()
        pf = FakePatchFeatures(raises="3 : invalid argument chainOptions")
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1")
        assert res["isError"] is True
        msg = res["message"]
        # cause 1: tangent saddle + its two-half-edge recipe
        assert "TANGENT" in msg and "two half-edges" in msg
        # cause 2: a fillet-split loop + its all-edges recipe
        assert "SPLIT" in msg and "fillet" in msg and "ALL of the loop's edges" in msg
        # the discriminating probe: find_geometry's edge count
        assert "find_geometry" in msg and "exactly 2 means case (1), more means case (2)" in msg

    def test_chain_failure_message_does_not_assert_tangent_as_sole_cause(self):
        # regression guard for the exact misdiagnosis: the message must not present the tangent-saddle
        # story as the ONLY explanation ("this looks like a TANGENT saddle opening") - it must frame
        # it as one of two possibilities.
        e1 = FakeEdge()
        pf = FakePatchFeatures(raises="3 : invalid argument chainOptions")
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1")
        msg = res["message"]
        assert "this looks like a tangent saddle opening" not in msg.lower()
        assert "two known causes" in msg

    def test_generic_patch_failure_keeps_the_plain_closed_loop_hint(self):
        # a non-tangency failure keeps the existing closed-loop hint, not the tangency teaching.
        e1 = FakeEdge()
        pf = FakePatchFeatures(raises="some other kernel error")
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1")
        assert res["isError"] is True
        assert "CLOSED loop" in res["message"] and "TANGENT" not in res["message"]

    def test_unknown_operation_rejected(self):
        e1 = FakeEdge()
        comp = FakeComp(FakeFeatures(pf=FakePatchFeatures()))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1", operation="cut")
        assert res["isError"] is True and "new, new_component" in res["message"]

    def test_boundaries_patches_every_loop_in_one_call(self):
        # the "patch all 8 holes at once" case: 4 separate rim edges -> 4 patches in one call
        edges = {f"R{i}": FakeEdge() for i in range(4)}
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map=edges)
        out = _payload(sc.patch_handler(boundaries=["R0", "R1", "R2", "R3"]))
        assert out["patched"] == 4 and out["requested"] == 4 and out["failed"] == 0
        assert len(out["patches"]) == 4
        assert len(out["result_bodies"]) == 4   # one patch body per loop

    def test_boundaries_reports_per_loop_failure_without_aborting(self):
        # one good rim + one stale handle -> 1 patched, 1 failed, the rest still done
        edges = {"R0": FakeEdge(), "R2": FakeEdge()}   # "R1" intentionally unresolvable (stale)
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map=edges)
        out = _payload(sc.patch_handler(boundaries=["R0", "R1", "R2"]))
        assert out["patched"] == 2 and out["failed"] == 1
        assert out["errors"][0]["index"] == 1   # the failing loop is identified by index

    def test_neither_boundary_nor_boundaries_errors(self):
        comp = FakeComp(FakeFeatures(pf=FakePatchFeatures()))
        _install(comp)
        res = sc.patch_handler()
        assert res["isError"] is True and "boundaries" in res["message"]

    def test_unknown_continuity_rejected(self):
        e1 = FakeEdge()
        comp = FakeComp(FakeFeatures(pf=FakePatchFeatures()))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1", continuity="silky")
        assert res["isError"] is True
        assert "connected, tangent, curvature" in res["message"]

    def test_continuity_set_failure_surfaces_as_error(self):
        # A continuity-set rejection must abort the patch and report the real failure, not be
        # dropped under safe() while the patch still reports success.
        e1 = FakeEdge()
        pf = FakePatchFeaturesRejectContinuity(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1", continuity="tangent")
        assert res["isError"] is True
        assert "continuity rejected" in res["message"]

    def test_continuity_tangent_set_on_input(self):
        # continuity resolves through SurfaceContinuityTypes - the PLURAL class is the one that
        # exists; a singular SurfaceContinuityType resolves to nothing, so the value the input
        # carries must come from the plural class or the patch runs on its default.
        import adsk.fusion
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.patch_handler(boundary="E1", continuity="tangent"))
        assert out["continuity"] == "tangent"
        assert (pf.last_input.continuity
                is adsk.fusion.SurfaceContinuityTypes.TangentSurfaceContinuityType)

    def test_continuity_unavailable_member_is_refused_not_run_on_the_default(self, monkeypatch):
        # an unresolvable continuity member must REFUSE. Reporting continuity=curvature while the
        # patch ran connected is the defect this path closes.
        import adsk.fusion
        e1 = FakeEdge()
        monkeypatch.setattr(adsk.fusion, "SurfaceContinuityTypes", object())
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1", continuity="curvature")
        assert res["isError"] is True
        assert "continuity=curvature" in res["message"]
        assert "not available on this Fusion version" in res["message"]

    def test_continuity_that_does_not_take_is_refused(self):
        # the input accepts the write and keeps its default: nothing raises, so only the read-back
        # catches it
        import adsk.fusion

        class _SwallowingPatchInput(FakePatchInput):
            def __setattr__(self, name, value):
                if name == "continuity":
                    object.__setattr__(self, "continuity", "connected-default")
                    return
                object.__setattr__(self, name, value)

        class _Feats(FakePatchFeatures):
            def createInput(self, boundary, op):
                self.last_input = _SwallowingPatchInput(boundary, op)
                return self.last_input

        e1 = FakeEdge()
        pf = _Feats(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1", continuity="tangent")
        assert res["isError"] is True
        assert "continuity=tangent" in res["message"] and "reads back unchanged" in res["message"]

    def test_interior_rails_assigned_and_verified_by_count(self):
        e1, r1, r2 = FakeEdge(), FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "R1": r1, "R2": r2})
        out = _payload(sc.patch_handler(boundary="E1", interior_rails=["R1", "R2"]))
        # the published count is the one the INPUT reads back, not the number of handles asked for
        assert out["interior_rail_count"] == pf.last_input.interiorRailsAndPoints.count == 2
        # the read-back is a FRESH collection each time - identity can never be the check, so the
        # verification is the COUNT
        first, second = pf.last_input.interiorRailsAndPoints, pf.last_input.interiorRailsAndPoints
        assert first is not second
        assert first.items == [r1, r2]

    def test_interior_rails_count_mismatch_is_refused(self):
        # the input keeps only some of the assigned rails -> the patch would run without them
        e1, r1, r2 = FakeEdge(), FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)], rails_dropped=1)
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "R1": r1, "R2": r2})
        res = sc.patch_handler(boundary="E1", interior_rails=["R1", "R2"])
        assert res["isError"] is True
        assert "reads back 1 entity(ies) after assigning 2" in res["message"]

    def test_interior_rails_empty_readback_counts_as_zero_not_as_success(self):
        # an EMPTY ObjectCollection reads count None (measured) - a None treated as "unreadable, so
        # assume it took" would pass a patch that dropped every rail
        e1, r1 = FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)], rails_dropped=1)
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "R1": r1})
        res = sc.patch_handler(boundary="E1", interior_rails=["R1"])
        assert res["isError"] is True
        assert "reads back 0 entity(ies) after assigning 1" in res["message"]

    def test_interior_rails_rejected_with_the_multi_loop_form(self):
        r1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": r1})
        res = sc.patch_handler(boundaries=["R0"], interior_rails=["R1"])
        assert res["isError"] is True
        assert "interior_rails" in res["message"] and "boundary" in res["message"]
        assert pf.last_input is None      # refused BEFORE any patch was attempted

    def test_interior_rails_omitted_writes_nothing(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.patch_handler(boundary="E1"))
        assert pf.last_input._rails == []
        assert "interior_rail_count" not in out

    def test_interior_rails_failure_names_both_causes_without_asserting_one(self):
        e1, r1 = FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(raises="ASM_BL_BAD_INPUT")
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "R1": r1})
        res = sc.patch_handler(boundary="E1", interior_rails=["R1"])
        assert res["isError"] is True
        msg = res["message"]
        assert "two candidate causes" in msg and "asserts neither" in msg
        assert "Retry WITHOUT interior_rails" in msg

    def test_boundaries_all_fail_reports_zero_patched(self):
        # every loop is a stale handle -> 0 patched, all failed, still a non-error multi report
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={})   # nothing resolves
        out = _payload(sc.patch_handler(boundaries=["R0", "R1"]))
        assert out["patched"] == 0 and out["requested"] == 2 and out["failed"] == 2
        assert out["result_bodies"] == []
        assert "Some loops failed" in out["note"]

    def test_boundaries_accepts_composite_handles_without_comma_shredding(self):
        # A find_geometry handle is COMPOSITE ('<token>|@<kind>:x,y,z') - its locator carries commas.
        # The plural 'boundaries' path passes each element as a bare STRING, so a naive comma-split
        # shreds ONE handle into broken fragments ('2.000000','3.000000') that resolve as stale -
        # 'boundaries' must accept the same fresh handles the singular 'boundary' accepts.
        # Each composite handle must resolve as ONE edge.
        e0, e1 = FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"TOK0": e0, "TOK1": e1})
        h0 = "TOK0|@circular_edge:1.000000,2.000000,3.000000"
        h1 = "TOK1|@circular_edge:4.000000,5.000000,6.000000"
        out = _payload(sc.patch_handler(boundaries=[h0, h1]))
        assert out["patched"] == 2 and out["failed"] == 0

    def test_patch_is_solid_is_read_back_not_hardcoded_false(self):
        # is_solid is read off the patch body, never the module's expectation that a patch is a
        # surface: a literal False here would report an open sheet for a body reading solid
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=True)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.patch_handler(boundary="E1"))
        assert out["is_solid"] is True
        assert "SOLID" in out["note"]
        assert "isSolid=false" not in out["note"]

    def test_patch_unreadable_is_solid_is_null_and_unverified(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[_UnreadableSolidBody("Patch1")])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.patch_handler(boundary="E1"))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "UNVERIFIED" in out["note"]

    def test_patch_with_no_result_body_is_an_error(self):
        # 'patched: true' beside result_bodies [] claims a fill the payload cannot show
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.patch_handler(boundary="E1")
        assert res["isError"] is True
        assert "owns no result body" in res["message"]

    def test_one_patch_body_is_the_boundary_that_passes(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.patch_handler(boundary="E1"))
        assert out["result_bodies"] == ["Patch1"]

    def test_multi_loop_bodyless_patch_is_a_per_loop_failure(self):
        pf = FakePatchFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.patch_handler(boundaries=["R0", "R1"]))
        assert out["patched"] == 0 and out["failed"] == 2
        assert "owns no result body" in out["errors"][0]["error"]

    def test_multi_loop_is_solid_is_the_aggregate_read_back(self):
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.patch_handler(boundaries=["R0", "R1"]))
        assert out["is_solid"] is False
        assert [p["is_solid"] for p in out["patches"]] == [False, False]

    def test_multi_loop_solid_result_is_not_reported_as_a_surface(self):
        # the aggregate is read off the patch bodies - a literal False here would call a body
        # reading isSolid=true an open surface
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=True)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.patch_handler(boundaries=["R0", "R1"]))
        assert out["is_solid"] is True
        assert "isSolid=false" not in out["note"]

    def test_multi_loop_unreadable_is_solid_is_null_and_unverified(self):
        pf = FakePatchFeatures(result_bodies=[_UnreadableSolidBody("P")])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.patch_handler(boundaries=["R0", "R1"]))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]

    def test_singular_boundary_composite_handle_string_not_shredded(self):
        # A lone composite handle passed as a STRING resolves to its ONE edge
        # (the singular param's schema is an array, but a raw string must not be comma-shredded either).
        e0 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"TOK0": e0})
        h0 = "TOK0|@circular_edge:1.000000,2.000000,3.000000"
        out = _payload(sc.patch_handler(boundary=h0))
        assert out["patched"] is True
        assert pf.last_input.boundary is e0     # the ONE edge, not a shredded fragment
