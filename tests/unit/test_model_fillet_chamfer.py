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
        self.isSolid = True          # BodyRef(kind='solid') checks this


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


class FakeCountingFeature:
    """A created feature that ANSWERS .faces.count/.edges.count - what the measured read-back reads."""
    def __init__(self, name, faces, edges):
        self.name = name
        self.faces = type("C", (), {"count": faces})()
        self.edges = type("C", (), {"count": edges})()
        self.deleted = False
    def deleteMe(self):
        self.deleted = True
        return True


class FakeFilletFeatures:
    def __init__(self):
        self.last = None
        # default created feature answers only .name (no .faces/.edges - the attribute-silent case)
        self.result = type("F", (), {"name": "Fillet1"})()
    def createInput(self):
        self.last = FakeFilletInput()
        return self.last
    def add(self, inp):
        return self.result


class FakeChamferFeatures:
    def __init__(self):
        self.last = None
        self.result = type("F", (), {"name": "Chamfer1"})()
    def createInput(self, edges, tangent):
        self.last = FakeChamferInput(edges, tangent)
        return self.last
    def add(self, inp):
        return self.result


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
    # BodyRef (the 'body_name' kind) resolves through the _inputs._common seam — patch it to the SAME
    # design, else the body resolves against a stale/empty design (the documented dual-seam trap).
    fl._inputs._common.design = lambda: design
    fl._inputs._common.target_component = lambda _d=None: comp
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # BodyRef(kind='solid') does isinstance(body, adsk.fusion.BRepBody) + checks isSolid — make the
    # fake body pass the BRep type check.
    adsk.fusion.BRepBody = FakeBody
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
        # BodyRef resolves the named body; an unknown name errors, naming the value + the handle path.
        _install([FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="X", radius=1, edge_filter="all")
        assert res["isError"] is True and "no body or component named 'X'" in res["message"]

    def test_omitted_scope_refuses(self):
        # No 'edges' and no 'edge_filter': REFUSED, naming both scoping paths. An omitted scope
        # must never silently mean the whole body - implicit blanket rounding was every
        # executor's default design language while 'all' was the default.
        _install([FakeBody("B", [True, True])])
        res = fl._fillet_handler(body_name="B", radius=1)
        assert res["isError"] is True
        assert "edges" in res["message"] and "edge_filter" in res["message"]
        res2 = fl._chamfer_handler(body_name="B", distance=1)
        assert res2["isError"] is True and "edge_filter" in res2["message"]

    def test_blanket_filter_reports_blast_radius(self):
        # An explicit filter sweep names how many of the body's edges it took.
        _install([FakeBody("B", [True, False, True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="convex"))
        assert "BLANKET" in out["note"] and "2 of the body's 3 edges" in out["note"]

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
        out = _payload(fl._fillet_handler(body_name="Block", radius=2, units="mm", edge_filter="all"))
        assert out["filleted"] is True and out["edges_requested"] == 3
        edges, val, tangent = ff.last.edge_set
        assert val == ("real", 0.2)        # 2mm -> 0.2cm

    def test_fillet_convex_filter(self):
        ff, _ = _install([FakeBody("B", [True, False, True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="convex"))
        assert out["edges_requested"] == 2  # only the two convex edges

    def test_fillet_concave_filter(self):
        ff, _ = _install([FakeBody("B", [True, False, True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="concave"))
        assert out["edges_requested"] == 1

    def test_default_most_recent_body(self):
        _install([FakeBody("First", [True]), FakeBody("Last", [True, True])])
        out = _payload(fl._fillet_handler(radius=1, edge_filter="all"))
        assert out["body"] == "Last"

    def test_unknown_convexity_included_under_filter(self):
        # An edge whose isConvex is None (unknown) is INCLUDED rather than silently dropped, even
        # under a convex/concave filter. Body: [convex, unknown] under 'convex' -> both pass.
        _install([FakeBody("B", [True, None])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="convex"))
        assert out["edges_requested"] == 2

    def test_radius_echoed_rounded_in_payload(self):
        _install([FakeBody("B", [True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=3.5, units="mm", edge_filter="all"))
        # the raw (un-scaled) radius is echoed under 'radius'
        assert out["radius"] == 3.5
        assert out["edge_selection"] == "filter"

    def test_partial_edge_application_errors_and_rolls_back(self):
        # The feature consumed FEWER edges than handed in: reporting filleted:true with the input
        # echo as the "result" would be the honesty-contract cardinal sin (a fillet silently rounded
        # fewer edges than requested). The read-back count must gate the call: error naming
        # requested vs applied, and the inert/partial feature must be rolled back (deleteMe called).
        ff, _ = _install([FakeBody("B", [True, True, True])])
        ff.result = FakeCountingFeature("Fillet1", faces=2, edges=1)
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="all")
        assert res["isError"] is True
        assert "PARTIALLY" in res["message"]
        assert "3" in res["message"] and "1" in res["message"]   # requested vs applied, both named
        assert ff.result.deleted is True

    def test_measured_fields_omitted_when_feature_does_not_answer(self):
        # safe() returning None means the attribute did not answer - the keys are OMITTED,
        # never reported as null (the default fake feature carries only .name). The no-op guard is
        # gated on faces_created == 0, so a None (unanswered) count does NOT trip it.
        _install([FakeBody("B", [True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="all"))
        assert "edges_measured" not in out and "faces_created" not in out
        assert "read from the created feature" not in out["note"]

    def test_fillet_no_op_on_tangent_edge_errors(self):
        # A fillet whose feature reports ZERO created faces rounded nothing (a tangent edge - two
        # faces meeting smoothly, e.g. a hole tangent to a face). filleted:true would be a false ok;
        # the 0-face read-back must convert it to an error naming the tangent cause AND remove the
        # inert feature.
        ff, _ = _install([FakeBody("B", [True])])
        ff.result = FakeCountingFeature("Fillet1", faces=0, edges=0)
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="all")
        assert res["isError"] is True
        assert "rounded nothing" in res["message"] and "TANGENT" in res["message"]
        assert ff.result.deleted is True          # the no-op feature was removed

    def test_chamfer_zero_faces_not_treated_as_no_op(self):
        # The fillet-specific TANGENT no-op guard (faces_created==0 alone) is fillet-only: a chamfer
        # whose feature reports 0 faces is NOT auto-errored on that basis - its face read-back
        # semantics differ. But the edge count still matches what was requested (1 of 1), so the
        # separate partial-application guard (which prefers the edge count when available) does not
        # fire either: a full, matched application succeeds even if faces_created reads 0.
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=0, edges=1)
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, edge_filter="all"))
        assert out["chamfered"] is True

    def test_chamfer_partial_edge_application_errors_and_rolls_back(self):
        # The SAME partial-application guard applies to chamfer as to fillet: 2 edges requested, the
        # feature's own edge count reads only 1 applied - error naming both counts, and the partial
        # feature is rolled back.
        _, cf = _install([FakeBody("B", [True, True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1, edges=1)
        res = fl._chamfer_handler(body_name="B", distance=1, edge_filter="all")
        assert res["isError"] is True
        assert "PARTIALLY" in res["message"]
        assert "2" in res["message"] and "1" in res["message"]
        assert cf.result.deleted is True


class TestChamfer:
    def test_chamfer_scales_distance(self):
        _, cf = _install([FakeBody("B", [True, True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, units="in", edge_filter="all"))
        assert out["chamfered"] is True
        assert cf.last.distance == ("real", 2.54)

    def test_two_distance_chamfer(self):
        _, cf = _install([FakeBody("B", [True, True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, distance_two=4, units="mm", edge_filter="all"))
        # setToTwoDistances used (not equal-distance), both scaled to cm
        assert cf.last.two_distances == (("real", 0.2), ("real", 0.4))
        assert cf.last.distance is None
        assert out["distance_two"] == 4

    def test_equal_distance_when_no_second(self):
        _, cf = _install([FakeBody("B", [True, True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, units="mm", edge_filter="all"))
        assert cf.last.two_distances is None
        assert cf.last.distance == ("real", 0.2)
        assert "distance_two" not in out


# ── A1: fillet SPECIFIC edges via handles ────────────────────────────────────

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
    # Rebinding the handler's module-global (imported by value from _common) is safe across tests:
    # conftest's autouse seam-restore reverts `target_component`/`app`/`design` on every loaded
    # tools module after each test, so no install leaks into the next one.
    fl.target_component = lambda x: comp
    return ff, cf


class TestEdgeHandles:
    def test_fillet_specific_edges_via_handles(self):
        e1, e2 = _FakeEdgeEnt("Bracket"), _FakeEdgeEnt("Bracket")
        ff, _ = _install_edge_handles({"E1": e1, "E2": e2})
        out = _payload(fl._fillet_handler(edges=["E1", "E2"], radius=2, units="mm"))
        assert out["filleted"] is True
        assert out["edges_requested"] == 2           # only the 2 named edges, not a whole body
        assert "handle" in out["edge_selection"]
        assert out["body"] == "Bracket"             # labelled from the edge's owning body

    def test_edges_take_precedence_over_body(self):
        e1 = _FakeEdgeEnt("X")
        ff, _ = _install_edge_handles({"E1": e1})
        out = _payload(fl._fillet_handler(edges=["E1"], body_name="ignored", radius=1))
        assert out["edges_requested"] == 1           # used the handle, not body_name

    def test_bad_edge_handle_errors(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})   # E2 missing
        res = fl._fillet_handler(edges=["E1", "E2"], radius=1)
        assert res["isError"] is True and "edges" in res["message"]

    def test_stale_handle_among_n_names_requested_count(self):
        # A stale/unresolvable handle among N passed handles must refuse the WHOLE call before any
        # feature is created (never silently fillet just the live ones) - and name how many handles
        # were requested, plus point back at find_geometry for a fresh one.
        _install_edge_handles({"E1": _FakeEdgeEnt()})   # E2 is stale/unresolvable
        res = fl._fillet_handler(edges=["E1", "E2"], radius=1)
        assert res["isError"] is True
        assert "2 edge handle(s) were requested" in res["message"]
        assert "find_geometry" in res["message"]


# ── occurrence-qualified body name (two chamfers on different components stay distinct) ──

class TestQualifiedBodyName:
    def test_body_name_qualified_with_occurrence_path(self):
        # a body reachable through an occurrence proxy reports '<occ fullPathName>/<name>', so the same
        # local name in two components is distinguishable.
        occ = type("Occ", (), {"fullPathName": "Gearbox:2"})()
        body = type("Bdy", (), {"name": "Housing", "assemblyContext": occ,
                                "parentComponent": type("C", (), {"name": "GearboxComp"})()})()
        assert fl._qualified_body_name(body) == "Gearbox:2/Housing"

    def test_body_name_unqualified_when_no_context(self):
        # a root-owned body with no readable context degrades to the plain local name (no '?/name').
        body = type("Bdy", (), {"name": "Plate"})()
        assert fl._qualified_body_name(body) == "Plate"

    def test_none_body_is_none(self):
        assert fl._qualified_body_name(None) is None
