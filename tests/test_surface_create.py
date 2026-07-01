"""Unit tests for surface_create.py — CREATE open (non-solid) surface bodies.

Pins the surface discriminator: surface_extrude/revolve set isSolid=False and REPORT is_solid=false,
and REJECT a result that came back solid (a closed profile slipped through). surface_patch fills a
closed edge loop (single edge auto-completes; multi-edge collection). No live Fusion — fake feature
classes capture what was passed in.
"""

import json

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
    def __init__(self, name="Surface1", bodies=None):
        self.name = name
        self.bodies = FakeBodies(bodies if bodies is not None else [FakeBody()])


class FakeExtrudeInput:
    def __init__(self, profile, op):
        self.profile = profile
        self.operation = op
        self.isSolid = None
        self.distance_extent = None
    def setDistanceExtent(self, sym, dist):
        self.distance_extent = (sym, dist)


class FakeRevolveInput:
    def __init__(self, profile, axis, op):
        self.profile = profile
        self.axis = axis
        self.operation = op
        self.isSolid = None
        self.angle_extent = None
    def setAngleExtent(self, sym, ang):
        self.angle_extent = (sym, ang)


class FakePatchInput:
    def __init__(self, boundary, op):
        self.boundary = boundary
        self.operation = op
        self.continuity = None


class FakeExtrudeFeatures:
    def __init__(self, result_bodies=None):
        self.last_input = None
        self.added = False
        self._result = result_bodies
    def createInput(self, profile, op):
        self.last_input = FakeExtrudeInput(profile, op)
        return self.last_input
    def add(self, inp):
        self.added = True
        return FakeFeature(bodies=self._result)


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
    def __init__(self, result_bodies=None, feature=True):
        self.last_input = None
        self._result = result_bodies
        self._feature = feature
    def createInput(self, boundary, op):
        self.last_input = FakePatchInput(boundary, op)
        return self.last_input
    def add(self, inp):
        if not self._feature:
            return None
        return FakeFeature(name="Patch1", bodies=self._result)


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


class FakeDesign:
    def __init__(self, comp):
        self._comp = comp
        self.rootComponent = comp
        self.activeComponent = comp


class _OC:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)


class FakeEdge:
    """Stands in for adsk.fusion.BRepEdge; .body identifies the owning body."""
    def __init__(self, body=None):
        self.body = body


def _wire_adsk(handle_map=None):
    import adsk.fusion, adsk.core
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation", "NewComponentFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    adsk.fusion.BRepEdge = FakeEdge
    sct = adsk.fusion.SurfaceContinuityType
    for n in ("ConnectedSurfaceContinuityType", "TangentSurfaceContinuityType",
              "CurvatureSurfaceContinuityType"):
        setattr(sct, n, n)
    # handle resolution is attached to the real design in _install now (see below) — this only wires
    # the adsk enum stand-ins.


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

    def test_continuity_tangent_set_on_input(self):
        # continuity=tangent resolves to the TangentSurfaceContinuityType enum on the patch input
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.patch_handler(boundary="E1", continuity="tangent"))
        assert out["continuity"] == "tangent"
        assert pf.last_input.continuity == "TangentSurfaceContinuityType"

    def test_boundaries_all_fail_reports_zero_patched(self):
        # every loop is a stale handle -> 0 patched, all failed, still a non-error multi report
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={})   # nothing resolves
        out = _payload(sc.patch_handler(boundaries=["R0", "R1"]))
        assert out["patched"] == 0 and out["requested"] == 2 and out["failed"] == 2
        assert out["result_bodies"] == []
        assert "Some loops failed" in out["note"]
