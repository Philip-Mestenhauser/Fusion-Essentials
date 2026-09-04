"""Unit tests for model_unstitch.py - the explode and its identity-operation gate."""

import json
from conftest import load_tool

so = load_tool("model_unstitch")


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


class TestUnstitch:

    def test_explode_body_uses_add_not_createInput(self):
        body = _FakeBRepBody("Solid1", is_solid=True)
        result = [_FakeBRepBody("Srf1", False), _FakeBRepBody("Srf2", False),
                  _FakeBRepBody("Srf3", False)]
        uf = _FakeUnstitchFeatures(result)
        _install(_FakeFeatures(unstitch=uf), bodies_by_name={"Solid1": body})
        out = _payload(so.handler(target="Solid1"))
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
        out = _payload(so.handler(faces=["H1", "H2"], chain=False))
        assert out["surface_body_count"] == 2
        faces_coll, chain = uf.last_call
        assert chain is False
        assert faces_coll.count == 2          # two faces collected

    def test_needs_target_or_faces(self):
        _install(_FakeFeatures(unstitch=_FakeUnstitchFeatures([])))
        res = so.handler()
        assert res["isError"] is True
        assert "target" in res["message"] and "faces" in res["message"]

    def test_target_and_faces_both_rejected(self):
        body = _FakeBRepBody("Solid1", is_solid=True)
        f1 = _FakeFace("F1")
        _install(_FakeFeatures(unstitch=_FakeUnstitchFeatures([])),
                 bodies_by_name={"Solid1": body}, handle_map={"H1": f1})
        res = so.handler(target="Solid1", faces=["H1"])
        assert res["isError"] is True
        assert "not both" in res["message"]

    def test_null_feature_is_error(self):
        # add() returns None (not unstitchable) -> honest error, never reported as success
        class _NullUnstitch:
            def add(self, faces, chain):
                return None
        body = _FakeBRepBody("Solid1", is_solid=True)
        _install(_FakeFeatures(unstitch=_NullUnstitch()), bodies_by_name={"Solid1": body})
        res = so.handler(target="Solid1")
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
        res = so.handler(target="Loose1")
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
        out = _payload(so.handler(target="Solid1"))
        assert out["bodies_before"] == 1 and out["bodies_after"] == 3
