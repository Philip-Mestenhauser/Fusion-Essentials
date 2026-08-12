"""Unit tests for ``surface_ops.py`` — LOFT / STITCH / UNSTITCH (surface<->solid bridge).

No live Fusion. The logic worth pinning with mocks:
  - LOFT: sections added IN ORDER (order is load-bearing), <2 rejected, rails-AND-centerline rejected,
    is_solid read back off the feature.
  - STITCH: validates all inputs are surfaces (rejects a solid up front), tolerance scaled to cm,
    became_solid=TRUE on a watertight result and FALSE (honest, NOT an error) when a result body stays
    open.
  - UNSTITCH: uses add(faces_collection, chain) directly (no createInput), peels a body / faces.

Fakes are named to match the Fusion type names the input-kinds branch on (BRepBody for the isSolid
surface/solid discrimination, BRepFace for face handles), mirroring test_model_extrude / test_model_combine.
"""

import json

from conftest import load_tool

so = load_tool("surface_ops")


# ── fakes ───────────────────────────────────────────────────────────────────

class _FakeBRepBody:
    """Named to register as adsk.fusion.BRepBody so _BODY_KINDS surface/solid checks fire on isSolid."""
    def __init__(self, name, is_solid=False):
        self.name = name
        self.isSolid = is_solid


class _FakeProfile:
    """Named to register as adsk.fusion.Profile so ProfileRef's isinstance check passes."""
    def __init__(self, tag=""):
        self.tag = tag


class _FakeFace:
    def __init__(self, name):
        self.name = name


class _FakeBodies:
    """A result-feature .bodies collection (count/item) of bodies with isSolid flags."""
    def __init__(self, bodies):
        self._b = list(bodies)
    @property
    def count(self):
        return len(self._b)
    def item(self, i):
        return self._b[i]


class _FakeFeature:
    def __init__(self, name, result_bodies, is_solid=None):
        self.name = name
        self.bodies = _FakeBodies(result_bodies)
        # loft/unstitch read feature.isSolid; stitch reads body.isSolid. Provide both.
        if is_solid is not None:
            self.isSolid = is_solid


class _FakeColl:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)
    @property
    def count(self):
        return len(self.items)


# ── loft fakes ──────────────────────────────────────────────────────────────

class _FakeLoftSections:
    def __init__(self):
        self.added = []      # records ORDER of section adds
    def add(self, section):
        self.added.append(section)
        return section


class _FakeCenterLineOrRails:
    def __init__(self):
        self.centerlines = []
        self.rails = []
    def addCenterLine(self, c):
        self.centerlines.append(c)
    def addRail(self, r):
        self.rails.append(r)


class _FakeLoftInput:
    def __init__(self, op):
        self.operation = op
        self.loftSections = _FakeLoftSections()
        self.centerLineOrRails = _FakeCenterLineOrRails()
        self.isSolid = True


class _SwallowingLoftInput(_FakeLoftInput):
    """A LoftFeatureInput that ACCEPTS the isClosed write and keeps its default anyway - the SWIG
    shape set_verified exists to catch (nothing raises, the loft would just run open)."""
    def __setattr__(self, name, value):
        object.__setattr__(self, name, False if name == "isClosed" else value)


class _FakeLoftFeatures:
    def __init__(self, result_is_solid=True, result_bodies=None, input_cls=_FakeLoftInput):
        self.last_input = None
        self._result_is_solid = result_is_solid
        self._input_cls = input_cls
        self._result_bodies = result_bodies if result_bodies is not None else [_FakeBRepBody("Body1", True)]
    def createInput(self, op):
        self.last_input = self._input_cls(op)
        return self.last_input
    def add(self, inp):
        return _FakeFeature("Loft1", self._result_bodies, is_solid=self._result_is_solid)


# ── stitch fakes ────────────────────────────────────────────────────────────

class _FakeStitchInput:
    def __init__(self, surfaces, tolerance, op):
        self.surfaces = surfaces
        self.tolerance = tolerance
        self.operation = op


class _FakeStitchFeatures:
    def __init__(self, result_bodies):
        self.last_input = None
        self._result_bodies = result_bodies
    def createInput(self, surfaces, tolerance, op):
        self.last_input = _FakeStitchInput(surfaces, tolerance, op)
        return self.last_input
    def add(self, inp):
        return _FakeFeature("Stitch1", self._result_bodies)


# ── unstitch fakes ──────────────────────────────────────────────────────────

class _FakeUnstitchFeatures:
    def __init__(self, result_bodies):
        self.last_call = None        # (faces_collection, chain)
        self._result_bodies = result_bodies
    def add(self, faces, chain):
        self.last_call = (faces, chain)
        return _FakeFeature("Unstitch1", self._result_bodies)


class _FakeFeatures:
    def __init__(self, loft=None, stitch=None, unstitch=None):
        if loft is not None:
            self.loftFeatures = loft
        if stitch is not None:
            self.stitchFeatures = stitch
        if unstitch is not None:
            self.unstitchFeatures = unstitch


class _FakeComp:
    def __init__(self, features, bodies_by_name=None):
        self.name = "Comp"
        self.features = features
        self._bodies = bodies_by_name or {}
        comp = self
        self.bRepBodies = type("BB", (), {
            "itemByName": staticmethod(lambda n: comp._bodies.get(n)),
            "count": property(lambda s: len(comp._bodies)),
        })()
        # Live meshBodies has count/item but NO itemByName (meshbodies-no-itembyname in
        # tests/live/VERIFIED_API_FACTS.md); this comp holds no meshes.
        self.meshBodies = type("MB", (), {"count": 0, "item": staticmethod(lambda i: None)})()


class _FakeDesign:
    def __init__(self, comp, handle_map=None):
        self.activeComponent = comp
        self.rootComponent = comp
        self._handles = handle_map or {}
    def findEntityByToken(self, t):
        e = self._handles.get(t)
        return [e] if e is not None else []


def _install(features, bodies_by_name=None, handle_map=None):
    comp = _FakeComp(features, bodies_by_name)
    design = _FakeDesign(comp, handle_map)
    so.app = type("A", (), {"activeProduct": design})()
    so._common.app = so.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _FakeDesign) else None
    adsk.fusion.BRepBody = _FakeBRepBody
    adsk.fusion.BRepFace = _FakeFace
    adsk.fusion.Profile = _FakeProfile
    # input-kinds resolve via _inputs._common (the app-reference seam), not so.app.
    so._inputs._common.design = lambda: design
    so._inputs._common.target_component = lambda d: comp
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ObjectCollection.create = staticmethod(lambda: _FakeColl())
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── LOFT ─────────────────────────────────────────────────────────────────────

class TestLoft:
    def _profiles_design(self, result_is_solid=True, result_bodies=None):
        lf = _FakeLoftFeatures(result_is_solid=result_is_solid, result_bodies=result_bodies)
        # three profile handles -> live Profile entities (order P0, P1, P2)
        p0, p1, p2 = (_FakeProfile("0"), _FakeProfile("1"), _FakeProfile("2"))
        handles = {"H0": p0, "H1": p1, "H2": p2}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        return lf, (p0, p1, p2)

    def test_three_profiles_added_in_order(self):
        lf, (p0, p1, p2) = self._profiles_design()
        out = _payload(so.loft_handler(profiles=["H0", "H1", "H2"]))
        assert out["lofted"] is True
        assert out["profiles_count"] == 3
        # ORDER is the whole game: sections added exactly H0,H1,H2.
        assert lf.last_input.loftSections.added == [p0, p1, p2]

    def test_reports_is_solid_read_back(self):
        self._profiles_design(result_is_solid=True)
        out = _payload(so.loft_handler(profiles=["H0", "H1", "H2"]))
        assert out["is_solid"] is True

    def test_surface_loft_reports_not_solid(self):
        self._profiles_design(result_is_solid=False,
                              result_bodies=[_FakeBRepBody("Srf1", False)])
        out = _payload(so.loft_handler(profiles=["H0", "H1"], as_surface=True))
        assert out["is_solid"] is False
        assert "SURFACE" in out["note"]

    def test_as_surface_sets_isSolid_false_on_input(self):
        lf, _ = self._profiles_design()
        _payload(so.loft_handler(profiles=["H0", "H1"], as_surface=True))
        assert lf.last_input.isSolid is False

    def test_fewer_than_two_rejected(self):
        self._profiles_design()
        res = so.loft_handler(profiles=["H0"])
        assert res["isError"] is True
        assert "at least 2 profiles" in res["message"]

    def test_is_closed_set_on_input_and_reported(self):
        lf, _ = self._profiles_design()
        out = _payload(so.loft_handler(profiles=["H0", "H1", "H2"], is_closed=True))
        assert lf.last_input.isClosed is True
        assert out["is_closed"] is True

    def test_is_closed_omitted_writes_nothing_and_reports_nothing(self):
        # an unwritten isClosed keeps the API default; the payload must not claim a value for it
        lf, _ = self._profiles_design()
        out = _payload(so.loft_handler(profiles=["H0", "H1"]))
        assert not hasattr(lf.last_input, "isClosed")
        assert "is_closed" not in out

    def test_is_closed_false_is_written_not_skipped(self):
        # False is a REQUEST, not an omission - `if is_closed:` would silently drop it
        lf, _ = self._profiles_design()
        out = _payload(so.loft_handler(profiles=["H0", "H1"], is_closed=False))
        assert lf.last_input.isClosed is False
        assert out["is_closed"] is False

    def test_is_closed_accepts_two_sections(self):
        # measured: a TWO-section closed loft builds a real feature - there is no >=3 guard
        self._profiles_design()
        out = _payload(so.loft_handler(profiles=["H0", "H1"], is_closed=True))
        assert out["lofted"] is True and out["profiles_count"] == 2

    def test_is_closed_that_does_not_take_is_refused(self):
        lf = _FakeLoftFeatures(input_cls=_SwallowingLoftInput)
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1")}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        res = so.loft_handler(profiles=["H0", "H1"], is_closed=True)
        assert res["isError"] is True
        assert "is_closed=True" in res["message"] and "reads back unchanged" in res["message"]

    def test_rails_and_centerline_both_rejected(self):
        lf = _FakeLoftFeatures()
        rail_ent = object()
        center_ent = object()
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1"),
                   "R": rail_ent, "C": center_ent}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        res = so.loft_handler(profiles=["H0", "H1"], rails=["R"], centerline="C")
        assert res["isError"] is True
        assert "centerline OR rails" in res["message"] or "not both" in res["message"]

    def test_centerline_set_on_input(self):
        lf = _FakeLoftFeatures()
        center_ent = object()
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1"), "C": center_ent}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        out = _payload(so.loft_handler(profiles=["H0", "H1"], centerline="C"))
        assert lf.last_input.centerLineOrRails.centerlines == [center_ent]
        assert out["has_centerline"] is True

    def test_rails_added_and_counted(self):
        lf = _FakeLoftFeatures()
        r1, r2 = object(), object()
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1"), "R1": r1, "R2": r2}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        out = _payload(so.loft_handler(profiles=["H0", "H1"], rails=["R1", "R2"]))
        assert lf.last_input.centerLineOrRails.rails == [r1, r2]
        assert out["rails_count"] == 2
        assert out["has_centerline"] is False

    def test_unknown_operation_rejected(self):
        self._profiles_design()
        res = so.loft_handler(profiles=["H0", "H1"], operation="weld")
        assert res["isError"] is True
        assert "new, join, cut, intersect" in res["message"]

    def test_loft_built_on_the_profiles_owning_component(self):
        # The profiles are OWNED by a sub-component while a DIFFERENT component is active. Handing
        # another component's native profile to the active component's features raises bSet live,
        # so the loft feature must be created on the OWNER's features. The active comp carries its own
        # loftFeatures; the owner carries a SEPARATE one - the test proves the owner's got the call.
        owner_lf = _FakeLoftFeatures()
        owner = type("Owner", (), {"features": _FakeFeatures(loft=owner_lf)})()
        # each profile's parentSketch.parentComponent points at the owner (the live Profile chain)
        p0 = _FakeProfile("0"); p0.parentSketch = type("Sk", (), {"parentComponent": owner})()
        p1 = _FakeProfile("1"); p1.parentSketch = type("Sk", (), {"parentComponent": owner})()
        active_lf = _FakeLoftFeatures()
        _install(_FakeFeatures(loft=active_lf), handle_map={"H0": p0, "H1": p1})
        out = _payload(so.loft_handler(profiles=["H0", "H1"]))
        assert out["lofted"] is True
        assert owner_lf.last_input is not None     # the OWNER built the loft
        assert active_lf.last_input is None        # NOT the active component (the bSet trap)


# ── STITCH ────────────────────────────────────────────────────────────────────

class TestStitch:
    def test_became_solid_true_on_watertight(self):
        # input surfaces (isSolid False), result a single closed solid (isSolid True)
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        result = [_FakeBRepBody("Solid1", is_solid=True)]
        _install(_FakeFeatures(stitch=_FakeStitchFeatures(result)),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.stitch_handler(bodies=["Srf1", "Srf2"]))
        assert out["stitched"] is True
        assert out["became_solid"] is True
        assert out["is_solid"] == [True]
        assert "SOLID" in out["note"]

    def test_became_solid_false_when_gaps_remain(self):
        # HONEST: result body stays open -> became_solid False, NOT an error.
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        result = [_FakeBRepBody("StillSurface", is_solid=False)]
        _install(_FakeFeatures(stitch=_FakeStitchFeatures(result)),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        res = so.stitch_handler(bodies=["Srf1", "Srf2"])
        assert res["isError"] is False        # NOT an error — honest report
        out = _payload(res)
        assert out["became_solid"] is False
        assert out["is_solid"] == [False]
        assert "did NOT close" in out["note"]

    def test_rejects_solid_input(self):
        # one of the inputs is a SOLID -> SurfaceBodyRefList rejects it before any mutation.
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        solid = _FakeBRepBody("Block", is_solid=True)
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])),
                 bodies_by_name={"Srf1": s1, "Block": solid})
        res = so.stitch_handler(bodies=["Srf1", "Block"])
        assert res["isError"] is True
        assert "Block" in res["message"] or "SOLID" in res["message"]

    def test_fewer_than_two_rejected(self):
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])),
                 bodies_by_name={"Srf1": s1})
        res = so.stitch_handler(bodies=["Srf1"])
        assert res["isError"] is True
        assert "at least 2" in res["message"]

    def test_tolerance_scaled_to_cm(self):
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        sf = _FakeStitchFeatures([_FakeBRepBody("Solid1", is_solid=True)])
        _install(_FakeFeatures(stitch=sf), bodies_by_name={"Srf1": s1, "Srf2": s2})
        _payload(so.stitch_handler(bodies=["Srf1", "Srf2"], tolerance=1, units="mm"))
        # 1 mm -> 0.1 cm handed to ValueInput.createByReal
        tol = sf.last_input.tolerance
        assert tol[0] == "real" and abs(tol[1] - 0.1) < 1e-9

    def test_default_tolerance_when_omitted(self):
        # no tolerance -> default 0.01 mm = 0.001 cm to the ValueInput; payload reports 0.01
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        sf = _FakeStitchFeatures([_FakeBRepBody("Solid1", is_solid=True)])
        _install(_FakeFeatures(stitch=sf), bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.stitch_handler(bodies=["Srf1", "Srf2"]))
        assert out["tolerance"] == 0.01
        tol = sf.last_input.tolerance
        assert tol[0] == "real" and abs(tol[1] - 0.001) < 1e-12   # 0.01 mm -> 0.001 cm

    def test_default_tolerance_reported_in_caller_units_not_raw_cm(self):
        # The default is a raw internal value (0.01 mm == 0.001 cm), not "0.01" in whatever
        # units the caller asked for. units='cm' omitted -> must report 0.001, not the bare 0.01.
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        sf = _FakeStitchFeatures([_FakeBRepBody("Solid1", is_solid=True)])
        _install(_FakeFeatures(stitch=sf), bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.stitch_handler(bodies=["Srf1", "Srf2"], units="cm"))
        assert out["tolerance"] == 0.001
        tol = sf.last_input.tolerance
        assert tol[0] == "real" and abs(tol[1] - 0.001) < 1e-12   # same internal cm value either way

    def test_unknown_units_rejected(self):
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        res = so.stitch_handler(bodies=["Srf1", "Srf2"], units="smoots")
        assert res["isError"] is True and "mm, cm, or in" in res["message"]

    def test_unknown_operation_rejected(self):
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])))
        res = so.stitch_handler(bodies=["Srf1", "Srf2"], operation="weld")
        assert res["isError"] is True
        assert "new, join, cut, intersect" in res["message"]

    def test_became_solid_false_when_only_some_result_bodies_closed(self):
        # mixed result: one closed solid + one still-open surface -> all(flags) is False -> NOT solid
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        result = [_FakeBRepBody("Solid1", is_solid=True), _FakeBRepBody("Surf2", is_solid=False)]
        _install(_FakeFeatures(stitch=_FakeStitchFeatures(result)),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.stitch_handler(bodies=["Srf1", "Srf2"]))
        assert out["is_solid"] == [True, False]
        assert out["became_solid"] is False
        assert "did NOT close" in out["note"]


# ── UNSTITCH ──────────────────────────────────────────────────────────────────

class TestUnstitch:
    def test_explode_body_uses_add_not_createInput(self):
        body = _FakeBRepBody("Solid1", is_solid=True)
        result = [_FakeBRepBody("Srf1", False), _FakeBRepBody("Srf2", False),
                  _FakeBRepBody("Srf3", False)]
        uf = _FakeUnstitchFeatures(result)
        _install(_FakeFeatures(unstitch=uf), bodies_by_name={"Solid1": body})
        out = _payload(so.unstitch_handler(target="Solid1"))
        assert out["unstitched"] is True
        assert out["surface_body_count"] == 3
        # add() was called (the createInput-less shape) with (collection, chain)
        assert uf.last_call is not None
        faces_coll, chain = uf.last_call
        assert chain is True
        assert faces_coll.count == 1          # the one body collected

    def test_peel_faces(self):
        f1 = _FakeFace("F1")
        f2 = _FakeFace("F2")
        result = [_FakeBRepBody("Srf1", False), _FakeBRepBody("Srf2", False)]
        uf = _FakeUnstitchFeatures(result)
        _install(_FakeFeatures(unstitch=uf), handle_map={"H1": f1, "H2": f2})
        out = _payload(so.unstitch_handler(faces=["H1", "H2"], chain=False))
        assert out["surface_body_count"] == 2
        faces_coll, chain = uf.last_call
        assert chain is False
        assert faces_coll.count == 2          # two faces collected

    def test_needs_target_or_faces(self):
        _install(_FakeFeatures(unstitch=_FakeUnstitchFeatures([])))
        res = so.unstitch_handler()
        assert res["isError"] is True
        assert "target" in res["message"] and "faces" in res["message"]

    def test_target_and_faces_both_rejected(self):
        body = _FakeBRepBody("Solid1", is_solid=True)
        f1 = _FakeFace("F1")
        _install(_FakeFeatures(unstitch=_FakeUnstitchFeatures([])),
                 bodies_by_name={"Solid1": body}, handle_map={"H1": f1})
        res = so.unstitch_handler(target="Solid1", faces=["H1"])
        assert res["isError"] is True
        assert "not both" in res["message"]

    def test_null_feature_is_error(self):
        # add() returns None (not unstitchable) -> honest error, never reported as success
        class _NullUnstitch:
            def add(self, faces, chain):
                return None
        body = _FakeBRepBody("Solid1", is_solid=True)
        _install(_FakeFeatures(unstitch=_NullUnstitch()), bodies_by_name={"Solid1": body})
        res = so.unstitch_handler(target="Solid1")
        assert res["isError"] is True
        assert "loose surfaces" in res["message"] or "unstitchable" in res["message"]


class TestUnstitchIdentityGate:
    def test_identity_unstitch_on_a_loose_surface_is_refused(self):
        # An unstitch of an ALREADY-LOOSE surface is an identity op the API reports as success
        # (measured: same census, the body re-serialized under a new name) - the count gate refuses.
        import types as _t
        body = _FakeBRepBody("Loose1", is_solid=False)
        body.parentComponent = _t.SimpleNamespace(
            bRepBodies=_t.SimpleNamespace(count=3, item=lambda i: None))
        uf = _FakeUnstitchFeatures([_FakeBRepBody("Loose1 (1)", False)])
        _install(_FakeFeatures(unstitch=uf), bodies_by_name={"Loose1": body})
        res = so.unstitch_handler(target="Loose1")
        assert res["isError"] is True and "identity operation" in res["message"]

    def test_a_real_explode_reports_the_census(self):
        # A genuine unstitch grows the host's body count; the payload carries both sides.
        import types as _t

        counts = [1, 3]

        class _CountingBodies:
            @property
            def count(self):
                return counts.pop(0)
            def item(self, i):
                return None

        body = _FakeBRepBody("Solid1", is_solid=True)
        body.parentComponent = _t.SimpleNamespace(bRepBodies=_CountingBodies())
        uf = _FakeUnstitchFeatures([_FakeBRepBody("S1", False), _FakeBRepBody("S2", False),
                                    _FakeBRepBody("S3", False)])
        _install(_FakeFeatures(unstitch=uf), bodies_by_name={"Solid1": body})
        out = _payload(so.unstitch_handler(target="Solid1"))
        assert out["bodies_before"] == 1 and out["bodies_after"] == 3
