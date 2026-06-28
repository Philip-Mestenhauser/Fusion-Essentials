"""Unit tests for ``find_geometry.py`` — query geometry, return stable handles.

This is the QUERY half of geometry-as-values: it must return each match's handle (entityToken),
kind, position, and shape data, and filter by kind / radius / nearest_to. Pinned here (no live
Fusion): the units scaling on positions/radii, the kind filter, the radius filter (5% tol), the
nearest_to sort, and that every match carries a handle.
"""

import json

from conftest import load_tool

fg = load_tool("find_geometry")


# ── fakes mimicking adsk BRep faces/edges ───────────────────────────────────

class _Pt:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z


class _CylGeo:
    def __init__(self, r, axis=(1, 0, 0)):
        self.surfaceType = "CYL"
        self.radius = r
        self.axis = _Pt(*axis)


class _PlaneGeo:
    surfaceType = "PLANE"


class FakeFace:
    def __init__(self, token, geo, centroid, area=10.0):
        self.entityToken = token
        self.geometry = geo
        self.centroid = _Pt(*centroid)
        self.area = area


class FakeBody:
    def __init__(self, faces=(), edges=(), vertices=()):
        self.faces = list(faces)
        self.edges = list(edges)
        self.vertices = list(vertices)


class FakeOcc:
    def __init__(self, name, comp, bodies):
        self.name = name
        self.component = type("C", (), {"name": comp})()
        self.bRepBodies = list(bodies)


class _OccColl:
    def __init__(self, occs):
        self._o = list(occs)
    @property
    def count(self):
        return len(self._o)
    def item(self, i):
        return self._o[i]


class FakeRoot:
    def __init__(self, occs):
        self.occurrences = _OccColl(occs)
        self.bRepBodies = type("B", (), {"itemByName": staticmethod(lambda n: None)})()


class FakeDesign:
    def __init__(self, occs):
        self.rootComponent = FakeRoot(occs)


def _install(occs):
    design = FakeDesign(occs)
    fg.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # surfaceType enum: map our string sentinels onto the SurfaceTypes attrs the code reads
    st = adsk.core.SurfaceTypes
    st.CylinderSurfaceType = "CYL"
    st.PlaneSurfaceType = "PLANE"
    st.ConeSurfaceType = "CONE"
    st.SphereSurfaceType = "SPHERE"
    st.TorusSurfaceType = "TORUS"


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _cyl(token, r, centroid, axis=(1, 0, 0)):
    return FakeFace(token, _CylGeo(r, axis), centroid)


def _plane(token, centroid):
    return FakeFace(token, _PlaneGeo(), centroid)


class TestGuards:
    def test_unknown_units(self):
        _install([FakeOcc("P:1", "P", [FakeBody()])])
        res = fg.handler(units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_unresolved_target(self):
        _install([FakeOcc("P:1", "P", [FakeBody()])])
        res = fg.handler(target="Nope")
        assert res["isError"] is True and "Could not resolve target" in res["message"]


class TestFind:
    def test_returns_handles_and_scaled_positions(self):
        # cylinder at world (2.45,2,0)cm -> reported in mm
        f = _cyl("TOK_PIN", 0.8, (2.45, 2.0, 0.0))
        _install([FakeOcc("Crank:1", "Crank", [FakeBody(faces=[f])])])
        out = _payload(fg.handler(target="Crank:1", units="mm"))
        m = out["matches"][0]
        assert m["handle"] == "TOK_PIN"
        assert m["kind"] == "cylinder_face"
        assert m["position"] == [24.5, 20.0, 0.0]      # cm -> mm
        assert m["radius"] == 8.0                        # 0.8cm -> 8mm

    def test_kind_filter_cylinder_only(self):
        body = FakeBody(faces=[_cyl("C", 0.8, (0, 0, 0)), _plane("P", (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        out = _payload(fg.handler(target="X:1", kind="cylinder_face"))
        kinds = {m["kind"] for m in out["matches"]}
        assert kinds == {"cylinder_face"}

    def test_radius_filter(self):
        body = FakeBody(faces=[_cyl("PIN", 0.8, (0, 0, 0)), _cyl("JRN", 1.0, (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        out = _payload(fg.handler(target="X:1", kind="cylinder_face", radius=8, units="mm"))
        assert out["returned"] == 1 and out["matches"][0]["handle"] == "PIN"   # only the r8mm pin

    def test_nearest_to_sorts(self):
        # faces at world 10cm (FAR) and 1cm (NEAR); nearest_to is in mm.
        body = FakeBody(faces=[_cyl("FAR", 0.8, (10, 0, 0)), _cyl("NEAR", 0.8, (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        # nearest_to=[10,0,0]mm = 1cm -> NEAR (at 1cm) is closest
        out = _payload(fg.handler(target="X:1", nearest_to=[10, 0, 0], units="mm"))
        assert out["matches"][0]["handle"] == "NEAR"
        # nearest_to=[100,0,0]mm = 10cm -> FAR (at 10cm) is closest
        out2 = _payload(fg.handler(target="X:1", nearest_to=[100, 0, 0], units="mm"))
        assert out2["matches"][0]["handle"] == "FAR"

    def test_every_match_has_a_handle(self):
        body = FakeBody(faces=[_cyl("A", 0.8, (0, 0, 0)), _plane("B", (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        out = _payload(fg.handler(target="X:1"))
        assert all(m.get("handle") for m in out["matches"])
