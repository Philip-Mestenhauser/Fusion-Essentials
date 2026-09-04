"""Unit tests for model_stitch.py - the tolerance and the became_solid verdict."""

import json
from conftest import load_tool

so = load_tool("model_stitch")


class _FakeBRepBody:
    """Named to register as adsk.fusion.BRepBody so _BODY_KINDS surface/solid checks fire on isSolid.
    `volume` is the reading a cut/intersect is judged on; None models a body whose volume will not
    read (an unmeasurable body, or one the operation consumed whole)."""
    def __init__(self, name, is_solid=False, volume=None):
        self.name = name
        self.isSolid = is_solid
        self.volume = volume


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
        # count/item(i) is the live collection protocol a volume census walks; itemByName is what a
        # BodyRef resolves through. Both are real BRepBodies members, so the fake carries both.
        self.bRepBodies = type("BB", (), {
            "itemByName": staticmethod(lambda n: comp._bodies.get(n)),
            "item": staticmethod(lambda i: list(comp._bodies.values())[i]),
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


class TestStitch:

    def test_became_solid_true_on_watertight(self):
        # input surfaces (isSolid False), result a single closed solid (isSolid True)
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        result = [_FakeBRepBody("Solid1", is_solid=True)]
        _install(_FakeFeatures(stitch=_FakeStitchFeatures(result)),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.handler(bodies=["Srf1", "Srf2"]))
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
        res = so.handler(bodies=["Srf1", "Srf2"])
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
        res = so.handler(bodies=["Srf1", "Block"])
        assert res["isError"] is True
        assert "Block" in res["message"] or "SOLID" in res["message"]

    def test_fewer_than_two_rejected(self):
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])),
                 bodies_by_name={"Srf1": s1})
        res = so.handler(bodies=["Srf1"])
        assert res["isError"] is True
        assert "at least 2" in res["message"]

    def test_tolerance_scaled_to_cm(self):
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        sf = _FakeStitchFeatures([_FakeBRepBody("Solid1", is_solid=True)])
        _install(_FakeFeatures(stitch=sf), bodies_by_name={"Srf1": s1, "Srf2": s2})
        _payload(so.handler(bodies=["Srf1", "Srf2"], tolerance=1, units="mm"))
        # 1 mm -> 0.1 cm handed to ValueInput.createByReal
        tol = sf.last_input.tolerance
        assert tol[0] == "real" and abs(tol[1] - 0.1) < 1e-9

    def test_default_tolerance_when_omitted(self):
        # no tolerance -> default 0.01 mm = 0.001 cm to the ValueInput; payload reports 0.01
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        sf = _FakeStitchFeatures([_FakeBRepBody("Solid1", is_solid=True)])
        _install(_FakeFeatures(stitch=sf), bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.handler(bodies=["Srf1", "Srf2"]))
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
        out = _payload(so.handler(bodies=["Srf1", "Srf2"], units="cm"))
        assert out["tolerance"] == 0.001
        tol = sf.last_input.tolerance
        assert tol[0] == "real" and abs(tol[1] - 0.001) < 1e-12   # same internal cm value either way

    def test_unknown_units_rejected(self):
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        res = so.handler(bodies=["Srf1", "Srf2"], units="smoots")
        assert res["isError"] is True and "mm, cm, or in" in res["message"]

    def test_unknown_operation_rejected(self):
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])))
        res = so.handler(bodies=["Srf1", "Srf2"], operation="weld")
        assert res["isError"] is True
        assert "new, join, cut, intersect" in res["message"]

    def test_became_solid_is_null_when_one_result_flag_will_not_read(self):
        # bool(safe(...)) turns an unreadable isSolid into a False, and all() then publishes
        # became_solid=false with a gap diagnosis ("increase tolerance") off a flag nobody read.
        class _BlindBody:
            name = "Mystery"

            @property
            def isSolid(self):
                raise RuntimeError("3 : flag unavailable")

        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        result = [_FakeBRepBody("Solid1", is_solid=True), _BlindBody()]
        _install(_FakeFeatures(stitch=_FakeStitchFeatures(result)),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.handler(bodies=["Srf1", "Srf2"]))
        assert out["is_solid"] == [True, None]
        assert out["became_solid"] is None
        assert out["unverified"] == ["became_solid"]
        assert "UNVERIFIED" in out["note"]
        assert "did NOT close" not in out["note"]

    def test_an_empty_result_set_is_an_error_not_a_gap_diagnosis(self):
        # 'stitched: true' with became_solid=false told the caller to increase the tolerance - a GAP
        # diagnosis needing a body whose isSolid READ false. With no result body at all nothing was
        # stitched, and the payload named neither a body nor a real verdict.
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([])),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        res = so.handler(bodies=["Srf1", "Srf2"])
        assert res["isError"] is True
        assert "owns no result body" in res["message"]
        assert "increase tolerance" not in res["message"]

    def test_one_result_body_is_the_boundary_that_still_reports(self):
        # the size boundary beside the empty set: ONE result body is a real verdict, not a failure.
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        _install(_FakeFeatures(stitch=_FakeStitchFeatures([_FakeBRepBody("Solid1", is_solid=True)])),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.handler(bodies=["Srf1", "Srf2"]))
        assert out["result_bodies"] == ["Solid1"] and out["became_solid"] is True

    def test_every_flag_readable_and_true_is_still_a_solid(self):
        # the boundary beside the null: with every flag READ, all() still decides - the tri-state
        # must not turn a genuine watertight stitch into an unverified one.
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        result = [_FakeBRepBody("Solid1", is_solid=True), _FakeBRepBody("Solid2", is_solid=True)]
        _install(_FakeFeatures(stitch=_FakeStitchFeatures(result)),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.handler(bodies=["Srf1", "Srf2"]))
        assert out["became_solid"] is True and "unverified" not in out

    def test_became_solid_false_when_only_some_result_bodies_closed(self):
        # mixed result: one closed solid + one still-open surface -> all(flags) is False -> NOT solid
        s1 = _FakeBRepBody("Srf1", is_solid=False)
        s2 = _FakeBRepBody("Srf2", is_solid=False)
        result = [_FakeBRepBody("Solid1", is_solid=True), _FakeBRepBody("Surf2", is_solid=False)]
        _install(_FakeFeatures(stitch=_FakeStitchFeatures(result)),
                 bodies_by_name={"Srf1": s1, "Srf2": s2})
        out = _payload(so.handler(bodies=["Srf1", "Srf2"]))
        assert out["is_solid"] == [True, False]
        assert out["became_solid"] is False
        assert "did NOT close" in out["note"]
