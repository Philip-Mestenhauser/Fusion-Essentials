"""Unit tests for ``fillet.py`` — model_fillet + model_chamfer.

Pinned: units scaling, positive-size + edge_filter + body guards, the convex/concave edge filter
selection, the radius/distance scaled to cm onto the API, and the default 'most recent body'.
"""

import json

from conftest import load_tool

fl = load_tool("model_fillet_chamfer")


class FakeEdge:
    def __init__(self, convex):
        self.isConvex = convex


class FakeEdges:
    def __init__(self, convex_flags):
        self._e = [FakeEdge(c) for c in convex_flags]
    @property
    def count(self):
        return len(self._e)
    def item(self, i):
        return self._e[i]


class FakeBody:
    def __init__(self, name, convex_flags):
        self.name = name
        self.edges = FakeEdges(convex_flags)


class FakeBodies:
    def __init__(self, bodies):
        self._b = list(bodies)
    @property
    def count(self):
        return len(self._b)
    def item(self, i):
        return self._b[i]
    def itemByName(self, name):
        for b in self._b:
            if b.name == name:
                return b
        return None


class FakeFilletInput:
    def __init__(self):
        self.edge_set = None
    def addConstantRadiusEdgeSet(self, edges, val, tangent):
        self.edge_set = (edges, val, tangent)


class FakeChamferInput:
    def __init__(self, edges, tangent):
        self.edges = edges
        self.tangent = tangent
        self.distance = None
        self.two_distances = None
    def setToEqualDistance(self, val):
        self.distance = val
    def setToTwoDistances(self, val1, val2):
        self.two_distances = (val1, val2)


class FakeFilletFeatures:
    def __init__(self):
        self.last = None
    def createInput(self):
        self.last = FakeFilletInput()
        return self.last
    def add(self, inp):
        return type("F", (), {"name": "Fillet1"})()


class FakeChamferFeatures:
    def __init__(self):
        self.last = None
    def createInput(self, edges, tangent):
        self.last = FakeChamferInput(edges, tangent)
        return self.last
    def add(self, inp):
        return type("F", (), {"name": "Chamfer1"})()


class FakeComp:
    def __init__(self, bodies, ff, cf):
        self.name = "Comp"
        self.bRepBodies = FakeBodies(bodies)
        self.features = type("F", (), {"filletFeatures": ff, "chamferFeatures": cf})()


class FakeDesign:
    def __init__(self, comp):
        self.activeComponent = comp
        self.rootComponent = comp


def _install(bodies):
    ff = FakeFilletFeatures(); cf = FakeChamferFeatures()
    comp = FakeComp(bodies, ff, cf)
    design = FakeDesign(comp)
    fl.app = type("A", (), {"activeProduct": design})()
    fl._common.app = fl.app
    # The handler calls the module-global `target_component` (imported from _common). The edge-handle
    # install below REASSIGNS `fl.target_component`/`fl._design` to its own comp; the conftest seam-
    # restore doesn't cover this module, so reset them to pristine here so this body-based install is
    # order-independent (otherwise a prior TestEdgeHandles run leaks an empty-comp target_component and
    # the handler builds onto the wrong fake -> cf.last stays None).
    fl.target_component = fl._common.target_component
    fl._design = fl._common.design
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))

    class FakeColl:
        def __init__(self):
            self._i = []
        def add(self, x):
            self._i.append(x)
        @property
        def count(self):
            return len(self._i)
    adsk.core.ObjectCollection.create = staticmethod(lambda: FakeColl())
    return ff, cf


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_unknown_units(self):
        _install([FakeBody("B", [True, True])])
        res = fl._fillet_handler(body_name="B", radius=1, units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_nonpositive_radius(self):
        _install([FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="B", radius=0)
        assert res["isError"] is True and "positive" in res["message"]

    def test_body_not_found(self):
        _install([FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="X", radius=1)
        assert res["isError"] is True and "not found" in res["message"]

    def test_bad_edge_filter(self):
        _install([FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="weird")
        assert res["isError"] is True and "edge_filter" in res["message"]

    def test_nonnumeric_radius(self):
        _install([FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="B", radius="big")
        assert res["isError"] is True and "must be a number" in res["message"]

    def test_no_matching_edges_errors(self):
        # body has only convex edges; a concave filter matches nothing
        _install([FakeBody("B", [True, True])])
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="concave")
        assert res["isError"] is True and "No matching edges" in res["message"]
        assert "body has 2 edges" in res["message"]


class TestFillet:
    def test_fillet_all_edges_scaled(self):
        ff, _ = _install([FakeBody("Block", [True, True, False])])
        out = _payload(fl._fillet_handler(body_name="Block", radius=2, units="mm"))
        assert out["filleted"] is True and out["edges_affected"] == 3
        edges, val, tangent = ff.last.edge_set
        assert val == ("real", 0.2)        # 2mm -> 0.2cm

    def test_fillet_convex_filter(self):
        ff, _ = _install([FakeBody("B", [True, False, True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="convex"))
        assert out["edges_affected"] == 2  # only the two convex edges

    def test_fillet_concave_filter(self):
        ff, _ = _install([FakeBody("B", [True, False, True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="concave"))
        assert out["edges_affected"] == 1

    def test_default_most_recent_body(self):
        _install([FakeBody("First", [True]), FakeBody("Last", [True, True])])
        out = _payload(fl._fillet_handler(radius=1))
        assert out["body"] == "Last"

    def test_unknown_convexity_included_under_filter(self):
        # An edge whose isConvex is None (unknown) is INCLUDED rather than silently dropped, even
        # under a convex/concave filter. Body: [convex, unknown] under 'convex' -> both pass.
        _install([FakeBody("B", [True, None])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="convex"))
        assert out["edges_affected"] == 2

    def test_radius_echoed_rounded_in_payload(self):
        _install([FakeBody("B", [True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=3.5, units="mm"))
        # the raw (un-scaled) radius is echoed under 'radius'
        assert out["radius"] == 3.5
        assert out["edge_selection"] == "filter"


class TestChamfer:
    def test_chamfer_scales_distance(self):
        _, cf = _install([FakeBody("B", [True, True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, units="in"))
        assert out["chamfered"] is True
        assert cf.last.distance == ("real", 2.54)

    def test_two_distance_chamfer(self):
        _, cf = _install([FakeBody("B", [True, True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, distance_two=4, units="mm"))
        # setToTwoDistances used (not equal-distance), both scaled to cm
        assert cf.last.two_distances == (("real", 0.2), ("real", 0.4))
        assert cf.last.distance is None
        assert out["distance_two"] == 4

    def test_equal_distance_when_no_second(self):
        _, cf = _install([FakeBody("B", [True, True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, units="mm"))
        assert cf.last.two_distances is None
        assert cf.last.distance == ("real", 0.2)
        assert "distance_two" not in out


# ── A1: fillet SPECIFIC edges via handles (the gap this rollout closes) ──────

class _FakeEdgeEnt:
    """A BRep edge resolved from a handle; carries .body.name for the result label."""
    def __init__(self, body_name="Block"):
        self.body = type("B", (), {"name": body_name})()


def _install_edge_handles(handle_map):
    """Install design so the GeometryHandleList resolves edge handles. The kind goes through
    _inputs._common.design()/findEntityByToken + isinstance(BRepEdge)."""
    ff = FakeFilletFeatures(); cf = FakeChamferFeatures()
    comp = FakeComp([], ff, cf)
    design = FakeDesign(comp)
    fl.app = type("A", (), {"activeProduct": design})()
    fl._common.app = fl.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.fusion.BRepEdge = _FakeEdgeEnt
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))

    class FakeColl:
        def __init__(self):
            self._i = []
        def add(self, x):
            self._i.append(x)
        @property
        def count(self):
            return len(self._i)
    adsk.core.ObjectCollection.create = staticmethod(lambda: FakeColl())

    class FakeDesignWithTokens(FakeDesign):
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    d = FakeDesignWithTokens(comp)
    fl._inputs._common.design = lambda: d
    fl._inputs._common.target_component = lambda x: comp
    fl._design = lambda: d        # the handler's own _design() (stays module-local)
    fl.target_component = lambda x: comp   # fillet now imports target_component from _common
    return ff, cf


class TestEdgeHandles:
    def test_fillet_specific_edges_via_handles(self):
        e1, e2 = _FakeEdgeEnt("Bracket"), _FakeEdgeEnt("Bracket")
        ff, _ = _install_edge_handles({"E1": e1, "E2": e2})
        out = _payload(fl._fillet_handler(edges=["E1", "E2"], radius=2, units="mm"))
        assert out["filleted"] is True
        assert out["edges_affected"] == 2           # only the 2 named edges, not a whole body
        assert "handle" in out["edge_selection"]
        assert out["body"] == "Bracket"             # labelled from the edge's owning body

    def test_edges_take_precedence_over_body(self):
        e1 = _FakeEdgeEnt("X")
        ff, _ = _install_edge_handles({"E1": e1})
        out = _payload(fl._fillet_handler(edges=["E1"], body_name="ignored", radius=1))
        assert out["edges_affected"] == 1           # used the handle, not body_name

    def test_bad_edge_handle_errors(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})   # E2 missing
        res = fl._fillet_handler(edges=["E1", "E2"], radius=1)
        assert res["isError"] is True and "edges" in res["message"]
