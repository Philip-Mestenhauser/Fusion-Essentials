"""Unit tests for ``mirror.py`` — mirror solid bodies across an origin plane.

Pinned: the plane guard, body resolution + missing-body reporting, the list-vs-comma parsing, the
mirror-plane selection (xy/xz/yz), and the join (isCombine) flag.
"""

import json

from conftest import load_tool

mr = load_tool("mirror")


class FakeBody:
    def __init__(self, name):
        self.name = name


class FakeBodies:
    def __init__(self, names):
        self._b = [FakeBody(n) for n in names]
    @property
    def count(self):
        return len(self._b)
    def itemByName(self, name):
        for b in self._b:
            if b.name == name:
                return b
        return None


class FakeMirrorInput:
    def __init__(self, bodies, plane):
        self.bodies = bodies
        self.plane = plane
        self.isCombine = False


class FakeMirrorFeature:
    name = "Mirror1"
    class bodies:
        count = 1
        @staticmethod
        def item(i):
            return type("B", (), {"name": "Body2"})()


class FakeMirrorFeatures:
    def __init__(self):
        self.last = None
    def createInput(self, bodies, plane):
        self.last = FakeMirrorInput(bodies, plane)
        return self.last
    def add(self, inp):
        return FakeMirrorFeature()


class FakeComp:
    def __init__(self, names, mf):
        self.name = "Comp"
        self.bRepBodies = FakeBodies(names)
        self.features = type("F", (), {"mirrorFeatures": mf})()
        self.xYConstructionPlane = ("plane", "xy")
        self.xZConstructionPlane = ("plane", "xz")
        self.yZConstructionPlane = ("plane", "yz")


class FakeDesign:
    def __init__(self, comp):
        self.activeComponent = comp
        self.rootComponent = comp


def _install(names):
    mf = FakeMirrorFeatures()
    comp = FakeComp(names, mf)
    design = FakeDesign(comp)
    mr.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # the PlaneRef input resolves via _common.design()/target_component() — point those at our fake
    # so the kind sees the same component (the app-reference seam: kinds use _common, not mr.app).
    mr._inputs._common.design = lambda: design
    mr._inputs._common.target_component = lambda d: comp

    class FakeColl:
        def __init__(self):
            self._i = []
        def add(self, x):
            self._i.append(x)
        @property
        def count(self):
            return len(self._i)
    adsk.core.ObjectCollection.create = staticmethod(lambda: FakeColl())
    return mf


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_bad_plane(self):
        _install(["A"])
        res = mr.handler(bodies=["A"], plane="qq")
        # PlaneRef now owns the error: 'qq' is not an origin alias / construction name / handle
        assert res["isError"] is True and "not an origin alias" in res["message"]

    def test_no_bodies(self):
        # BodyRefList ('bodies', required) owns the empty error
        _install(["A"])
        res = mr.handler(bodies=[], plane="yz")
        assert res["isError"] is True and "bodies" in res["message"] and "at least one body" in res["message"]

    def test_body_not_found(self):
        _install(["A"])
        res = mr.handler(bodies=["A", "X"], plane="yz")
        assert res["isError"] is True and "no body named 'X'" in res["message"]


class TestMirror:
    def test_mirror_across_yz(self):
        mf = _install(["BankL"])
        out = _payload(mr.handler(bodies=["BankL"], plane="yz"))
        assert out["mirrored"] is True and out["plane"] == "yz"
        assert mf.last.plane == ("plane", "yz")
        assert out["result_bodies"] == ["Body2"]

    def test_comma_string_bodies(self):
        mf = _install(["a", "b"])
        out = _payload(mr.handler(bodies="a, b", plane="xy"))
        assert out["source_bodies"] == ["a", "b"]
        assert mf.last.bodies.count == 2

    def test_join_sets_iscombine(self):
        mf = _install(["A"])
        _payload(mr.handler(bodies=["A"], plane="yz", join=True))
        assert mf.last.isCombine is True
