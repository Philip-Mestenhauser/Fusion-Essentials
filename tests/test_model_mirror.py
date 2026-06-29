"""Unit tests for ``mirror.py`` — mirror solid bodies across an origin plane.

Pinned: the plane guard, body resolution + missing-body reporting, the list-vs-comma parsing, the
mirror-plane selection (xy/xz/yz), and the join (isCombine) flag.
"""

from conftest import load_tool, make_design, install, payload as _payload

mr = load_tool("model_mirror")


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


def _install(names):
    """Mirror-specific component (features.mirrorFeatures + the origin construction planes) wired into
    a standard design via conftest's make_design/install (which patch both seams + Design.cast +
    ObjectCollection.create uniformly, so nothing leaks)."""
    from conftest import MakeComp
    mf = FakeMirrorFeatures()
    comp = MakeComp(name="Comp", bodies=names)
    comp.features = type("F", (), {"mirrorFeatures": mf})()
    comp.xYConstructionPlane = ("plane", "xy")
    comp.xZConstructionPlane = ("plane", "xz")
    comp.yZConstructionPlane = ("plane", "yz")
    install(mr, make_design(comp=comp))
    return mf


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

    def test_join_defaults_false(self):
        mf = _install(["A"])
        out = _payload(mr.handler(bodies=["A"], plane="yz"))
        assert mf.last.isCombine is False
        assert out["joined"] is False

    def test_mirror_across_xz(self):
        mf = _install(["A"])
        out = _payload(mr.handler(bodies=["A"], plane="xz"))
        assert out["plane"] == "xz"
        assert mf.last.plane == ("plane", "xz")

    def test_multiple_result_bodies_collected(self):
        mf = _install(["A"])

        # feature.bodies returns several names -> result_bodies lists them all
        class _Bodies:
            count = 3
            @staticmethod
            def item(i):
                return type("B", (), {"name": f"R{i}"})()

        class _Feat:
            name = "Mirror1"
            bodies = _Bodies

        mf.add = lambda inp: _Feat()
        out = _payload(mr.handler(bodies=["A"], plane="yz"))
        assert out["result_bodies"] == ["R0", "R1", "R2"]

    def test_zero_result_bodies_is_empty_list(self):
        mf = _install(["A"])

        class _Bodies:
            count = 0
            @staticmethod
            def item(i):
                raise AssertionError("should not be called when count==0")

        class _Feat:
            name = "Mirror1"
            bodies = _Bodies

        mf.add = lambda inp: _Feat()
        out = _payload(mr.handler(bodies=["A"], plane="yz"))
        assert out["result_bodies"] == []
