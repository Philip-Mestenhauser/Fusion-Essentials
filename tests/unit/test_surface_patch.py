"""Unit tests for surface_patch.py - the loop fill, the rails and the multi-loop shape."""

import json
import types
from conftest import load_tool, _NamedCollection

sc = load_tool("surface_patch")


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


class TestSurfacePatch:

    def test_patch_over_closed_edge_loop(self):
        e1, e2, e3 = FakeEdge(), FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "E2": e2, "E3": e3})
        out = _payload(sc.handler(boundary=["E1", "E2", "E3"]))
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
        out = _payload(sc.handler(boundary="E1"))
        assert out["patched"] is True
        # a single edge is passed directly (Fusion auto-finds the loop), NOT wrapped in a collection
        assert pf.last_input.boundary is e1

    def test_null_feature_errors(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(feature=False)
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.handler(boundary="E1")
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
        res = sc.handler(boundary="E1")
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
        res = sc.handler(boundary="E1")
        msg = res["message"]
        assert "this looks like a tangent saddle opening" not in msg.lower()
        assert "two known causes" in msg

    def test_generic_patch_failure_keeps_the_plain_closed_loop_hint(self):
        # a non-tangency failure keeps the existing closed-loop hint, not the tangency teaching.
        e1 = FakeEdge()
        pf = FakePatchFeatures(raises="some other kernel error")
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.handler(boundary="E1")
        assert res["isError"] is True
        assert "CLOSED loop" in res["message"] and "TANGENT" not in res["message"]

    def test_unknown_operation_rejected(self):
        e1 = FakeEdge()
        comp = FakeComp(FakeFeatures(pf=FakePatchFeatures()))
        _install(comp, handle_map={"E1": e1})
        res = sc.handler(boundary="E1", operation="cut")
        assert res["isError"] is True and "new, new_component" in res["message"]

    def test_boundaries_patches_every_loop_in_one_call(self):
        # the "patch all 8 holes at once" case: 4 separate rim edges -> 4 patches in one call
        edges = {f"R{i}": FakeEdge() for i in range(4)}
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map=edges)
        out = _payload(sc.handler(boundaries=["R0", "R1", "R2", "R3"]))
        assert out["patched"] == 4 and out["requested"] == 4 and out["failed"] == 0
        assert len(out["patches"]) == 4
        assert len(out["result_bodies"]) == 4   # one patch body per loop

    def test_boundaries_reports_per_loop_failure_without_aborting(self):
        # one good rim + one stale handle -> 1 patched, 1 failed, the rest still done
        edges = {"R0": FakeEdge(), "R2": FakeEdge()}   # "R1" intentionally unresolvable (stale)
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map=edges)
        out = _payload(sc.handler(boundaries=["R0", "R1", "R2"]))
        assert out["patched"] == 2 and out["failed"] == 1
        assert out["errors"][0]["index"] == 1   # the failing loop is identified by index

    def test_neither_boundary_nor_boundaries_errors(self):
        comp = FakeComp(FakeFeatures(pf=FakePatchFeatures()))
        _install(comp)
        res = sc.handler()
        assert res["isError"] is True and "boundaries" in res["message"]

    def test_unknown_continuity_rejected(self):
        e1 = FakeEdge()
        comp = FakeComp(FakeFeatures(pf=FakePatchFeatures()))
        _install(comp, handle_map={"E1": e1})
        res = sc.handler(boundary="E1", continuity="silky")
        assert res["isError"] is True
        assert "connected, tangent, curvature" in res["message"]

    def test_continuity_set_failure_surfaces_as_error(self):
        # A continuity-set rejection must abort the patch and report the real failure, not be
        # dropped under safe() while the patch still reports success.
        e1 = FakeEdge()
        pf = FakePatchFeaturesRejectContinuity(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.handler(boundary="E1", continuity="tangent")
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
        out = _payload(sc.handler(boundary="E1", continuity="tangent"))
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
        res = sc.handler(boundary="E1", continuity="curvature")
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
        res = sc.handler(boundary="E1", continuity="tangent")
        assert res["isError"] is True
        assert "continuity=tangent" in res["message"] and "reads back unchanged" in res["message"]

    def test_interior_rails_assigned_and_verified_by_count(self):
        e1, r1, r2 = FakeEdge(), FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "R1": r1, "R2": r2})
        out = _payload(sc.handler(boundary="E1", interior_rails=["R1", "R2"]))
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
        res = sc.handler(boundary="E1", interior_rails=["R1", "R2"])
        assert res["isError"] is True
        assert "reads back 1 entity(ies) after assigning 2" in res["message"]

    def test_interior_rails_empty_readback_counts_as_zero_not_as_success(self):
        # an EMPTY ObjectCollection reads count None (measured) - a None treated as "unreadable, so
        # assume it took" would pass a patch that dropped every rail
        e1, r1 = FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)], rails_dropped=1)
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "R1": r1})
        res = sc.handler(boundary="E1", interior_rails=["R1"])
        assert res["isError"] is True
        assert "reads back 0 entity(ies) after assigning 1" in res["message"]

    def test_interior_rails_rejected_with_the_multi_loop_form(self):
        r1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": r1})
        res = sc.handler(boundaries=["R0"], interior_rails=["R1"])
        assert res["isError"] is True
        assert "interior_rails" in res["message"] and "boundary" in res["message"]
        assert pf.last_input is None      # refused BEFORE any patch was attempted

    def test_interior_rails_omitted_writes_nothing(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.handler(boundary="E1"))
        assert pf.last_input._rails == []
        assert "interior_rail_count" not in out

    def test_interior_rails_failure_names_both_causes_without_asserting_one(self):
        e1, r1 = FakeEdge(), FakeEdge()
        pf = FakePatchFeatures(raises="ASM_BL_BAD_INPUT")
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1, "R1": r1})
        res = sc.handler(boundary="E1", interior_rails=["R1"])
        assert res["isError"] is True
        msg = res["message"]
        assert "two candidate causes" in msg and "asserts neither" in msg
        assert "Retry WITHOUT interior_rails" in msg

    def test_boundaries_all_fail_reports_zero_patched(self):
        # every loop is a stale handle -> 0 patched, all failed, still a non-error multi report
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={})   # nothing resolves
        out = _payload(sc.handler(boundaries=["R0", "R1"]))
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
        out = _payload(sc.handler(boundaries=[h0, h1]))
        assert out["patched"] == 2 and out["failed"] == 0

    def test_patch_is_solid_is_read_back_not_hardcoded_false(self):
        # is_solid is read off the patch body, never the module's expectation that a patch is a
        # surface: a literal False here would report an open sheet for a body reading solid
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=True)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.handler(boundary="E1"))
        assert out["is_solid"] is True
        assert "SOLID" in out["note"]
        assert "isSolid=false" not in out["note"]

    def test_patch_unreadable_is_solid_is_null_and_unverified(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[_UnreadableSolidBody("Patch1")])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.handler(boundary="E1"))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "UNVERIFIED" in out["note"]

    def test_patch_with_no_result_body_is_an_error(self):
        # 'patched: true' beside result_bodies [] claims a fill the payload cannot show
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        res = sc.handler(boundary="E1")
        assert res["isError"] is True
        assert "owns no result body" in res["message"]

    def test_one_patch_body_is_the_boundary_that_passes(self):
        e1 = FakeEdge()
        pf = FakePatchFeatures(result_bodies=[FakeBody("Patch1", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"E1": e1})
        out = _payload(sc.handler(boundary="E1"))
        assert out["result_bodies"] == ["Patch1"]

    def test_multi_loop_bodyless_patch_is_a_per_loop_failure(self):
        pf = FakePatchFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.handler(boundaries=["R0", "R1"]))
        assert out["patched"] == 0 and out["failed"] == 2
        assert "owns no result body" in out["errors"][0]["error"]

    def test_multi_loop_is_solid_is_the_aggregate_read_back(self):
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=False)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.handler(boundaries=["R0", "R1"]))
        assert out["is_solid"] is False
        assert [p["is_solid"] for p in out["patches"]] == [False, False]

    def test_multi_loop_solid_result_is_not_reported_as_a_surface(self):
        # the aggregate is read off the patch bodies - a literal False here would call a body
        # reading isSolid=true an open surface
        pf = FakePatchFeatures(result_bodies=[FakeBody("P", is_solid=True)])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.handler(boundaries=["R0", "R1"]))
        assert out["is_solid"] is True
        assert "isSolid=false" not in out["note"]

    def test_multi_loop_unreadable_is_solid_is_null_and_unverified(self):
        pf = FakePatchFeatures(result_bodies=[_UnreadableSolidBody("P")])
        comp = FakeComp(FakeFeatures(pf=pf))
        _install(comp, handle_map={"R0": FakeEdge(), "R1": FakeEdge()})
        out = _payload(sc.handler(boundaries=["R0", "R1"]))
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
        out = _payload(sc.handler(boundary=h0))
        assert out["patched"] is True
        assert pf.last_input.boundary is e0     # the ONE edge, not a shredded fragment
