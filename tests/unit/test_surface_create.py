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
    def __init__(self, result_bodies=None, feature=True, raises=None):
        # raises: add() raises this message - models the kernel refusing the patch
        # (e.g. a tangent saddle opening the single-seed auto-complete cannot chain).
        self.last_input = None
        self._result = result_bodies
        self._feature = feature
        self._raises = raises
    def createInput(self, boundary, op):
        self.last_input = FakePatchInput(boundary, op)
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
