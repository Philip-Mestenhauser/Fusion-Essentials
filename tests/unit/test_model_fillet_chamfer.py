"""Unit tests for ``fillet.py`` — model_fillet + model_chamfer.

Pinned: units scaling, positive-size + edge_filter + body guards, the convex/concave edge filter
selection, the radius/distance scaled to cm onto the API, and the default 'most recent body'.
"""

import json
import math

import pytest

import types

from conftest import (BRepEdge, BRepFace, FakePoint, FakeUnitsManager, FakeVector3D, load_tool,
                      _NamedCollection)

fl = load_tool("model_fillet_chamfer")

# Every rig below is ONE edge running along +Z through the origin. The classifier reads only local
# things there: the edge's own tangent, and per bounding coEdge its face normal plus whether that
# coEdge heads against the edge. A manifold edge's two coEdges head opposite ways round it.
_PLUS_X, _PLUS_Y, _PLUS_Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)


def _coedge(normal, opposed):
    """A BRepCoEdge: the face it bounds (answering its out-of-material normal at any point) reached
    through its loop, and whether it runs against the edge's own direction."""
    face = BRepFace(surface=None, normal=FakeVector3D(*normal))
    return types.SimpleNamespace(isOpposedToEdge=opposed,
                                 loop=types.SimpleNamespace(face=face))


def _rig(*sides, tangent=_PLUS_Z, param_reversed=False):
    """A BRepEdge bounded by the given (normal, opposed) coEdge sides; tangent None is an edge whose
    curve evaluator does not answer, param_reversed None one whose isParamReversed does not."""
    return BRepEdge(curve=None, point_on_edge=FakePoint(),
                    tangent=FakeVector3D(*tangent) if tangent else None,
                    param_reversed=param_reversed,
                    co_edges=[_coedge(n, o) for n, o in sides])


def _tilted(deg):
    """The +X normal turned `deg` about the edge - the second face of a dihedral that shallow."""
    a = math.radians(deg)
    return (math.cos(a), math.sin(a), 0.0)


def _edge(kind):
    """One rig per named dihedral. True = convex (the cube corner), False = concave (the same two
    normals with both coEdges reversed), 'smooth' = two faces meeting flat, 'antiparallel' = normals
    pointing at each other, 'split' = coEdges that cannot both be right, None = no coEdges at all."""
    if kind is None:
        return BRepEdge(curve=None, point_on_edge=FakePoint(), tangent=FakeVector3D(*_PLUS_Z))
    return {
        True: lambda: _rig((_PLUS_X, False), (_PLUS_Y, True)),
        False: lambda: _rig((_PLUS_X, True), (_PLUS_Y, False)),
        "smooth": lambda: _rig((_PLUS_X, False), (_PLUS_X, True)),
        "antiparallel": lambda: _rig((_PLUS_X, False), ((-1.0, 0.0, 0.0), True)),
        # the two coEdges round one edge head opposite ways, so this pair cannot both be right
        "split": lambda: _rig((_PLUS_X, False), (_PLUS_Y, False)),
    }[kind]()


class FakeBody:
    def __init__(self, name, edge_kinds, volume=None):
        self.name = name
        self.edges = _NamedCollection([_edge(k) for k in edge_kinds])
        self.isSolid = True          # BodyRef(kind='solid') checks this
        # None = the volume read does not answer, so the volume gate has nothing to judge.
        self.volume = volume


class FakeFilletInput:
    """FilletFeatureInput. Each add*EdgeSet returns True (the API returns a bool), and the arity of
    each matches the real one - addVariableRadiusEdgeSet has NO isTangentChain argument, and its
    positions/radii are plain Python lists (a vector argument): an ObjectCollection raises live, so
    it raises here."""

    def __init__(self):
        self.edge_set = None
        self.variable_set = None
        self.chord_set = None
        self.refuse = False

    def addConstantRadiusEdgeSet(self, edges, val, tangent):
        self.edge_set = (edges, val, tangent)
        return not self.refuse

    def addVariableRadiusEdgeSet(self, tangentEdges, startRadius, endRadius, positions, radii):
        for name, arg in (("positions", positions), ("radii", radii)):
            if not isinstance(arg, list):
                raise TypeError(f"in method 'FilletFeatureInput_addVariableRadiusEdgeSet', "
                                f"argument '{name}' is not a vector")
        self.variable_set = (tangentEdges, startRadius, endRadius, positions, radii)
        return not self.refuse

    def addChordLengthEdgeSet(self, edges, chordLength, tangent):
        self.chord_set = (edges, chordLength, tangent)
        return not self.refuse


class FakeRuleFilletInput:
    """RuleFilletFeatureInput. setByAllEdges/setByBetweenFacesOrFeatures take plain Python lists of
    faces (not an ObjectCollection) and return a bool; radius and topologyType are settable."""

    def __init__(self):
        self.all_edges = None
        self.between = None
        self.radius = None
        self.topologyType = None
        self.refuse = False

    def _check(self, name, arg):
        if not isinstance(arg, list):
            raise TypeError(f"in method 'RuleFilletFeatureInput_{name}', argument is not a vector")

    def setByAllEdges(self, facesOrFeatures):
        self._check("setByAllEdges", facesOrFeatures)
        self.all_edges = facesOrFeatures
        return not self.refuse

    def setByBetweenFacesOrFeatures(self, one, two):
        self._check("setByBetweenFacesOrFeatures", one)
        self._check("setByBetweenFacesOrFeatures", two)
        self.between = (one, two)
        return not self.refuse


# The sentinel a fresh ChamferFeatureInput carries, so "never assigned" is distinguishable from
# "assigned Fusion's own default member".
_UNSET_CORNER = "corner:api-default"


class FakeChamferInput:
    """ChamferFeatureInput. Each setTo* returns its documented bool, and cornerType is a PROPERTY -
    so the SWIG trap applies to it: an assignment the proxy drops leaves the API default in place
    and only a read-back tells. `swallow_corner_set` models that."""

    swallow_corner_set = False

    def __init__(self, edges, tangent):
        self.edges = edges
        self.tangent = tangent
        self.distance = None
        self.two_distances = None
        self.distance_and_angle = None
        self.refuse = None            # the name of the setTo* that answers False
        self._corner = _UNSET_CORNER
    def setToEqualDistance(self, val):
        self.distance = val
        return self.refuse != "equal"
    def setToTwoDistances(self, val1, val2):
        self.two_distances = (val1, val2)
        return self.refuse != "two"
    def setToDistanceAndAngle(self, distance, angle):
        self.distance_and_angle = (distance, angle)
        return self.refuse != "angle"

    @property
    def cornerType(self):
        return self._corner

    @cornerType.setter
    def cornerType(self, value):
        if not self.swallow_corner_set:
            self._corner = value


class FakeCountingFeature:
    """A created feature that ANSWERS .faces.count - the only per-edge effect read-back the real
    Fillet/ChamferFeature offers (neither exposes an .edges collection, measured live).
    healthState 2 with an EMPTY errorOrWarningMessage is a real shape: a variable-radius chain listed
    out of order comes back failed and silent, reading 0 faces like a tangent no-op does."""
    def __init__(self, name, faces, health=0, message=""):
        self.name = name
        self.faces = type("C", (), {"count": faces})()
        self.healthState = health
        self.errorOrWarningMessage = message
        self.deleted = False
    def deleteMe(self):
        self.deleted = True
        return True


class FakeFilletFeatures:
    def __init__(self):
        self.last = None
        self.rule_last = None
        # default created feature answers only .name (no .faces/.edges - the attribute-silent case)
        self.result = type("F", (), {"name": "Fillet1"})()
        self.rule_result = FakeCountingFeature("RuleFillet1", faces=4)
        self.rule_settings_override = None   # set to model a radius/topology that did not land
        # applied when a feature is added, so a test can model the geometry actually moving
        self.on_add = None
        self.refuse_edge_set = False
    def createInput(self):
        self.last = FakeFilletInput()
        self.last.refuse = self.refuse_edge_set
        return self.last
    def add(self, inp):
        if self.on_add:
            self.on_add()
        return self.result
    def createRuleFilletInput(self):
        self.rule_last = FakeRuleFilletInput()
        self.rule_last.refuse = self.refuse_edge_set
        return self.rule_last
    def addRuleFillet(self, inp):
        if self.on_add:
            self.on_add()
        # A live RuleFilletFeature reports what it applied through ruleFilletSettings; the fake
        # echoes the input unless a test overrides it to model a setter the API ignored.
        if self.rule_result is not None and self.rule_settings_override is None:
            applied = getattr(inp.radius, "value", inp.radius)
            self.rule_result.ruleFilletSettings = type(
                "S", (), {"radius": type("V", (), {"value": applied})(),
                          "topologyType": inp.topologyType})()
        elif self.rule_result is not None:
            self.rule_result.ruleFilletSettings = self.rule_settings_override
        return self.rule_result


class FakeChamferFeatures:
    """A live ChamferFeature reports what it BUILT, and that is the only signal a wrong corner type
    or angle leaves - all three corner types build the same face count. So the created feature
    carries cornerType, and (for a distance-and-angle chamfer) chamferType plus a
    chamferTypeDefinition whose .distance/.angle are ModelParameters in CM and RADIANS. The fake
    echoes what the input took; the *_override knobs model the platform building something else."""

    def __init__(self):
        self.last = None
        self.result = type("F", (), {"name": "Chamfer1"})()
        self.refuse = None            # 'equal' | 'two' | 'angle' - which setTo* answers False
        self.added = 0
        self.corner_override = None       # (value,) - what the FEATURE reports instead
        self.definition_override = None   # (distance_cm, angle_rad) - ditto, or False for absent
        self.chamfer_type_override = None
        # applied when a feature is added, so a test can model the geometry actually moving
        self.on_add = None
    def createInput(self, edges, tangent):
        self.last = FakeChamferInput(edges, tangent)
        self.last.refuse = self.refuse
        return self.last
    def add(self, inp):
        import adsk.fusion
        self.added += 1
        if self.on_add:
            self.on_add()
        if self.result is None:
            return self.result
        if self.corner_override is not False:      # False = the feature does not answer cornerType
            self.result.cornerType = (self.corner_override[0] if self.corner_override
                                      else inp.cornerType)
        if inp.distance_and_angle is not None:
            self.result.chamferType = (
                self.chamfer_type_override[0] if self.chamfer_type_override
                else getattr(adsk.fusion.ChamferTypes, "DistanceAndAngleChamferType"))
            if self.definition_override is False:
                self.result.chamferTypeDefinition = None
            else:
                dist_cm, angle_rad = (self.definition_override or
                                      (inp.distance_and_angle[0][1], inp.distance_and_angle[1][1]))
                self.result.chamferTypeDefinition = type("D", (), {
                    "distance": type("P", (), {"value": dist_cm})(),
                    "angle": type("P", (), {"value": angle_rad})()})()
        return self.result


class FakeComp:
    def __init__(self, bodies, ff, cf):
        self.name = "Comp"
        self.bRepBodies = _NamedCollection(bodies)
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

    def test_nonnumeric_radius_is_read_as_an_expression_and_refused_by_name(self):
        # A non-numeric radius string is a parameter EXPRESSION, so the refusal comes from the units
        # engine that could not evaluate it - naming the input and the value, not "not a number".
        _install([FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="B", radius="big")
        assert res["isError"] is True
        assert "'radius'" in res["message"] and "did not evaluate" in res["message"]
        assert "'big'" in res["message"]

    def test_a_nonnumeric_chamfer_distance_is_still_just_not_a_number(self):
        # Only the fillet RADIUS takes the expression form; the chamfer's distance is compared as a
        # number against the created feature, so a string there is refused outright.
        _install([FakeBody("B", [True])])
        res = fl._chamfer_handler(body_name="B", distance="big", edge_filter="all")
        assert res["isError"] is True and "'distance' must be a number." in res["message"]

    def test_no_matching_edges_errors(self):
        # body has only convex edges; a concave filter matches nothing
        _install([FakeBody("B", [True, True])])
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="concave")
        assert res["isError"] is True and "No matching edges" in res["message"]
        assert "body has 2 edges" in res["message"]

    def test_a_body_with_no_readable_edges_refuses_under_all(self):
        # 'all' classifies nothing, so there is no census for this refusal to name - and naming one
        # anyway raises out of the handler instead of refusing.
        _install([FakeBody("B", [])])
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="all")
        assert res["isError"] is True and "No matching edges" in res["message"]
        assert "body has 0 edges" in res["message"]


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

    def test_a_smooth_edge_is_in_neither_filter(self):
        # BRepEdge exposes no isConvex, so each edge is classified here; two faces meeting flat are
        # neither convex nor concave and match no filter. The split is published beside the sweep.
        _install([FakeBody("B", [True, "smooth", False])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="concave"))
        assert (out["edges_convex"], out["edges_concave"], out["edges_smooth"]) == (1, 1, 1)
        assert "1 convex, 1 concave, 1 smooth" in out["note"]

    def test_convex_and_concave_are_told_apart_on_one_body(self):
        # The two differ ONLY in which way each coEdge heads round the edge - the local read.
        _install([FakeBody("B", [True, False, True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="convex"))
        assert (out["edges_convex"], out["edges_concave"]) == (2, 1)
        assert out["edges_requested"] == 2 and "edges_swept" not in out

    def test_an_unclassifiable_edge_refuses_a_filtered_sweep(self):
        # An edge the classifier could not answer must NOT be swept in silently. The refusal names
        # the branch and the count.
        ff, _ = _install([FakeBody("B", [True, None])])
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="convex")
        assert res["isError"] is True
        assert "1 of the 2 edges on 'B' could not be classified" in res["message"]
        assert "1 unreadable" in res["message"]
        assert "'edges' handles" in res["message"] and "edge_filter='all'" in res["message"]
        assert ff.last is None            # refused BEFORE any fillet input was created

    def test_every_unclassified_branch_holds_the_sweep_back(self):
        # A knife edge and a disagreeing pair are as unswept as an unreadable one: all three count
        # toward the refusal, and each is named so the caller knows which it met.
        ff, _ = _install([FakeBody("B", [True, "antiparallel", "split"])])
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="convex")
        assert res["isError"] is True
        assert "2 of the 3 edges on 'B' could not be classified" in res["message"]
        assert "1 knife" in res["message"] and "1 disagreed" in res["message"]
        assert ff.last is None            # refused BEFORE any fillet input was created

    def test_all_sweeps_every_edge_without_classifying(self, monkeypatch):
        # 'all' gates on nothing, so it neither pays the classifier nor publishes a census it did
        # not take - and an edge no classifier could answer is no reason to refuse.
        _install([FakeBody("B", [True, None])])
        called = []
        monkeypatch.setattr(fl, "_edge_convexity", lambda e: called.append(e))
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="all"))
        assert out["edges_requested"] == 2 and called == []
        assert "convex" not in out["note"] and "edges_convex" not in out

    def test_two_coedges_heading_the_same_way_are_not_signed(self):
        # The pair runs OPPOSITE ways round one edge, so this pair cannot both be right and the
        # heading the sign would be read off is not known.
        assert fl._edge_convexity(_edge("split")) == "disagreed"

    def test_anti_parallel_normals_are_a_knife_not_a_smooth_join(self):
        # Two out-of-material normals pointing at each other bound a knife edge or a crack: no
        # material wedge to sign - and it is NOT a flat join either.
        assert fl._edge_convexity(_edge("antiparallel")) == "knife"

    def test_an_edge_running_against_its_curve_flips_the_verdict(self):
        # The evaluator follows the CURVE; isParamReversed says whether the edge runs against it.
        # Uncorrected, both headings negate together and the verdict flips with nothing to catch it.
        assert fl._edge_convexity(_rig((_PLUS_X, False), (_PLUS_Y, True))) == "convex"
        assert fl._edge_convexity(
            _rig((_PLUS_X, False), (_PLUS_Y, True), param_reversed=True)) == "concave"

    def test_an_edge_whose_param_direction_does_not_read_is_unreadable(self):
        assert fl._edge_convexity(
            _rig((_PLUS_X, False), (_PLUS_Y, True), param_reversed=None)) == "unreadable"

    def test_an_edge_whose_tangent_does_not_read_is_unreadable(self):
        # Every side may read and the dihedral still have no heading to sign against.
        assert fl._edge_convexity(
            _rig((_PLUS_X, False), (_PLUS_Y, True), tangent=None)) == "unreadable"

    def test_an_edge_without_exactly_two_coedges_is_unreadable(self):
        assert fl._edge_convexity(_rig((_PLUS_X, False))) == "unreadable"
        assert fl._edge_convexity(
            _rig((_PLUS_X, False), (_PLUS_Y, True), (_PLUS_Y, False))) == "unreadable"

    def test_the_dihedral_tolerance_pins_the_smooth_band(self):
        # 0.05 deg: a 0.04 deg dihedral is a flat join, a 0.06 deg one is a corner to round.
        assert fl._edge_convexity(_rig((_PLUS_X, False), (_tilted(0.04), True))) == "smooth"
        assert fl._edge_convexity(_rig((_PLUS_X, False), (_tilted(0.06), True))) == "convex"

    def test_the_smooth_band_excludes_its_own_boundary(self, monkeypatch):
        # '>' not '>=': a dihedral sitting exactly ON the tolerance is a corner, not a smooth join.
        corner = _edge(True)                       # 90 deg: the two normals' cosine is exactly 0.0
        monkeypatch.setattr(fl, "_SMOOTH_JOIN_COS", 0.0)
        assert fl._edge_convexity(corner) == "convex"
        monkeypatch.setattr(fl, "_SMOOTH_JOIN_COS", -1e-9)
        assert fl._edge_convexity(corner) == "smooth"

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
        ff.result = FakeCountingFeature("Fillet1", faces=2)
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="all")
        assert res["isError"] is True
        assert "PARTIALLY" in res["message"]
        assert "3" in res["message"] and "2" in res["message"]   # requested vs applied, both named
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
        ff.result = FakeCountingFeature("Fillet1", faces=0)
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="all")
        assert res["isError"] is True
        assert "rounded nothing" in res["message"] and "TANGENT" in res["message"]
        assert ff.result.deleted is True          # the no-op feature was removed

    def test_chamfer_zero_faces_errors_as_partial(self):
        # A chamfer creates one bevel face per edge it actually cut, and the feature offers no
        # per-edge read-back besides its faces (no .edges collection, measured live) - so a chamfer
        # whose feature holds ZERO faces cut nothing, and the partial-application guard converts it
        # to an error and rolls the inert feature back.
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=0)
        res = fl._chamfer_handler(body_name="B", distance=1, edge_filter="all")
        assert res["isError"] is True
        assert "PARTIALLY" in res["message"]
        assert cf.result.deleted is True

    def test_chamfer_partial_edge_application_errors_and_rolls_back(self):
        # The SAME partial-application guard applies to chamfer as to fillet: 2 edges requested, the
        # feature's own edge count reads only 1 applied - error naming both counts, and the partial
        # feature is rolled back.
        _, cf = _install([FakeBody("B", [True, True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
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

    def test_a_refused_equal_distance_is_an_error(self):
        # The refusal message must name the requested size, not fall through the handler's
        # catch-all as an exception about an undefined name.
        _, cf = _install([FakeBody("B", [True])])
        cf.refuse = "equal"
        res = fl._chamfer_handler(body_name="B", distance=2, units="mm", edge_filter="all")
        assert res["isError"] is True and "equal-distance chamfer of 2.0 mm" in res["message"]
        assert cf.added == 0

    def test_a_refused_two_distance_is_an_error(self):
        _, cf = _install([FakeBody("B", [True])])
        cf.refuse = "two"
        res = fl._chamfer_handler(body_name="B", distance=2, distance_two=3, units="mm",
                                  edge_filter="all")
        assert res["isError"] is True and "two-distance chamfer (2.0/3.0 mm)" in res["message"]
        assert cf.added == 0


class TestDistanceAndAngle:
    """The third chamfer definition: setToDistanceAndAngle(distance, angle)."""

    def test_angle_routes_to_set_to_distance_and_angle(self):
        _, cf = _install([FakeBody("B", [True, True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, angle_deg=30, units="mm",
                                           edge_filter="all"))
        distance, angle = cf.last.distance_and_angle
        assert distance == ("real", pytest.approx(0.2))          # 2 mm -> cm
        # Measured live: a REAL ValueInput angle is read as RADIANS - setToDistanceAndAngle(0.3,
        # radians(30)) cut legs of 0.3 cm and 0.1732 cm (= 0.3*tan(30)).
        assert angle == ("real", pytest.approx(0.5235987755982988))
        assert cf.last.distance is None and cf.last.two_distances is None
        assert out["angle_deg"] == 30 and "distance_two" not in out

    def test_the_angle_is_not_scaled_by_the_length_units(self):
        # 'units' scales the distance only - an inch job must not multiply the angle by 2.54.
        _, cf = _install([FakeBody("B", [True])])
        _payload(fl._chamfer_handler(body_name="B", distance=1, angle_deg=45, units="in",
                                     edge_filter="all"))
        distance, angle = cf.last.distance_and_angle
        assert distance == ("real", pytest.approx(2.54))
        assert angle == ("real", pytest.approx(math.radians(45)))

    def test_angle_with_distance_two_is_refused_naming_both(self):
        # Two DIFFERENT definitions of the same chamfer; silently dropping one would bevel
        # something other than what was asked for.
        _, cf = _install([FakeBody("B", [True])])
        res = fl._chamfer_handler(body_name="B", distance=2, distance_two=3, angle_deg=45,
                                  edge_filter="all")
        assert res["isError"] is True
        assert "'angle_deg' (45)" in res["message"] and "'distance_two' (3)" in res["message"]
        assert cf.last is None and cf.added == 0      # refused before any input was even created

    def test_a_refused_distance_and_angle_errors_naming_the_values(self):
        _, cf = _install([FakeBody("B", [True])])
        cf.refuse = "angle"
        res = fl._chamfer_handler(body_name="B", distance=2, angle_deg=45, units="mm",
                                  edge_filter="all")
        assert res["isError"] is True
        assert "2.0 mm at 45.0 deg" in res["message"]
        assert cf.added == 0                          # nothing was added on a refused definition

    def test_a_nonpositive_angle_is_refused(self):
        _install([FakeBody("B", [True])])
        res = fl._chamfer_handler(body_name="B", distance=2, angle_deg=0, edge_filter="all")
        assert res["isError"] is True and "'angle_deg' must be positive, got 0.0" in res["message"]

    def test_a_nonnumeric_angle_is_refused(self):
        _install([FakeBody("B", [True])])
        res = fl._chamfer_handler(body_name="B", distance=2, angle_deg="steep", edge_filter="all")
        assert res["isError"] is True and "'angle_deg' must be a number" in res["message"]

    def test_no_angle_leaves_the_equal_distance_path_alone(self):
        _, cf = _install([FakeBody("B", [True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, units="mm", edge_filter="all"))
        assert cf.last.distance_and_angle is None
        assert "angle_deg" not in out


class TestCornerType:
    """ChamferFeatureInput.cornerType - a PROPERTY on the classic input, so it goes through the
    set-then-read-back a SWIG proxy demands."""

    def test_the_map_carries_the_apis_own_member_spelling(self):
        # The API spells it BlendCornertype, lowercase 't'; "correcting" it to BlendCornerType
        # names a member that does not exist, and getattr would raise on the live enum.
        assert fl._CORNER_TYPES == {"chamfer": "ChamferCornerType", "miter": "MiterCornerType",
                                    "blend": "BlendCornertype"}

    @pytest.mark.parametrize("key,member", [("chamfer", "ChamferCornerType"),
                                            ("miter", "MiterCornerType"),
                                            ("blend", "BlendCornertype")])
    def test_each_option_lands_the_matching_enum_member(self, key, member):
        import adsk.fusion
        _, cf = _install([FakeBody("B", [True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, edge_filter="all",
                                           corner_type=key))
        assert cf.last.cornerType == getattr(adsk.fusion.ChamferCornerTypes, member)
        assert out["corner_type"] == key

    def test_omitting_corner_type_leaves_the_api_default_untouched(self):
        _, cf = _install([FakeBody("B", [True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, edge_filter="all"))
        assert cf.last.cornerType == _UNSET_CORNER      # never assigned
        assert "corner_type" not in out

    def test_an_unknown_corner_type_is_refused(self):
        _, cf = _install([FakeBody("B", [True])])
        res = fl._chamfer_handler(body_name="B", distance=1, edge_filter="all",
                                  corner_type="rounded")
        assert res["isError"] is True
        assert "'corner_type' must be one of" in res["message"] and "rounded" in res["message"]
        assert cf.added == 0

    def test_a_corner_type_the_input_never_took_is_an_error(self, monkeypatch):
        # A SWIG proxy accepts an assignment to a name it does not define; the value lands nowhere
        # and the chamfer would run on the DEFAULT corner while the payload claimed 'miter'.
        _, cf = _install([FakeBody("B", [True])])
        monkeypatch.setattr(FakeChamferInput, "swallow_corner_set", True)
        res = fl._chamfer_handler(body_name="B", distance=1, edge_filter="all", corner_type="miter")
        assert res["isError"] is True
        assert "did not take" in res["message"] and "corner_type='miter'" in res["message"]
        assert cf.added == 0

    def test_an_unavailable_enum_family_is_reported_not_guessed(self):
        import adsk.fusion
        _, cf = _install([FakeBody("B", [True])])
        adsk.fusion.ChamferCornerTypes = None
        res = fl._chamfer_handler(body_name="B", distance=1, edge_filter="all", corner_type="blend")
        assert res["isError"] is True and "not available on this Fusion version" in res["message"]
        assert cf.added == 0

    def test_corner_type_rides_along_with_a_distance_and_angle_chamfer(self):
        import adsk.fusion
        _, cf = _install([FakeBody("B", [True])])
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, angle_deg=45,
                                           edge_filter="all", corner_type="miter"))
        assert cf.last.distance_and_angle is not None
        assert cf.last.cornerType == adsk.fusion.ChamferCornerTypes.MiterCornerType
        assert out["angle_deg"] == 45 and out["corner_type"] == "miter"


class TestChamferReadsBackWhatItBuilt:
    """The INPUT taking a value is not the feature building it, and a chamfer leaves no indirect
    trace - measured live, all three corner types build the same face count. So the corner type,
    the definition kind and the distance/angle pair are read off the created feature."""

    def test_a_feature_reporting_a_different_corner_errors_and_rolls_back(self):
        import adsk.fusion
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.corner_override = (adsk.fusion.ChamferCornerTypes.ChamferCornerType,)
        res = fl._chamfer_handler(body_name="B", distance=1, edge_filter="all", corner_type="blend")
        assert res["isError"] is True
        want = adsk.fusion.ChamferCornerTypes.BlendCornertype
        got = adsk.fusion.ChamferCornerTypes.ChamferCornerType
        assert f"reads back {got}" in res["message"]          # what the feature says
        assert f"'blend' ({want})" in res["message"]          # what was asked for
        assert cf.result.deleted is True and "rolled back" in res["message"]

    def test_a_feature_reporting_a_different_angle_errors_and_rolls_back(self):
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.definition_override = (0.2, math.radians(30))     # asked 45 deg, built 30
        res = fl._chamfer_handler(body_name="B", distance=2, angle_deg=45, units="mm",
                                  edge_filter="all")
        assert res["isError"] is True
        assert "angle reads back 30.0 deg" in res["message"] and "requested 45.0" in res["message"]
        assert cf.result.deleted is True and "rolled back" in res["message"]

    def test_a_feature_reporting_a_different_distance_errors_and_rolls_back(self):
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.definition_override = (0.5, math.radians(45))     # asked 2 mm, built 5 mm
        res = fl._chamfer_handler(body_name="B", distance=2, angle_deg=45, units="mm",
                                  edge_filter="all")
        assert res["isError"] is True
        assert "distance reads back 5.0" in res["message"] and "requested 2.0" in res["message"]
        assert cf.result.deleted is True

    def test_a_feature_built_as_another_definition_errors(self):
        # A distance-and-angle request that fell back to equal-distance would still read a
        # plausible distance; chamferType is what names the definition that got built.
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.chamfer_type_override = ("equal-distance-type",)
        res = fl._chamfer_handler(body_name="B", distance=2, angle_deg=45, edge_filter="all")
        assert res["isError"] is True
        assert "chamfer type equal-distance-type" in res["message"]
        assert cf.result.deleted is True

    def test_the_reported_values_come_from_the_feature_not_the_request(self):
        # The feature answers a distance/angle that round-trip within tolerance of the request, so
        # the call succeeds - and the payload carries the FEATURE's numbers.
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.definition_override = (0.2, math.radians(45))
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, angle_deg=45, units="mm",
                                           edge_filter="all"))
        assert out["angle_deg"] == 45.0 and out["distance"] == 2.0
        # both fields are named as READ BACK - a value that merely survived as the request echo
        # would be missing from this list even though the number happens to match.
        assert "angle_deg/distance are read back off the created feature" in out["note"]
        assert "angle_deg_unverified" not in out
        assert "chamfer_type_unverified" not in out

    def test_an_unreadable_definition_is_flagged_never_echoed(self):
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.definition_override = False        # the feature answers no chamferTypeDefinition
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, angle_deg=45, units="mm",
                                           edge_filter="all"))
        assert "angle_deg" not in out                      # never echoed as if confirmed
        assert out["angle_deg_unverified"] is True and out["distance_unverified"] is True
        assert "could NOT be read back" in out["note"]

    def test_an_unreadable_corner_type_is_flagged_never_echoed(self):
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.corner_override = False            # the feature does not answer cornerType
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, edge_filter="all",
                                           corner_type="miter"))
        assert "corner_type" not in out
        assert out["corner_type_unverified"] is True

    def test_an_equal_distance_chamfer_reads_back_nothing_extra(self):
        # The definition read-back is scoped to the distance-and-angle path; the other two
        # definitions are unchanged and carry no verified/unverified fields.
        _, cf = _install([FakeBody("B", [True])])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        out = _payload(fl._chamfer_handler(body_name="B", distance=2, units="mm",
                                           edge_filter="all"))
        assert not [key for key in out if key.endswith("_unverified")]
        assert "angle_deg" not in out and "corner_type" not in out


# ── A1: fillet SPECIFIC edges via handles ────────────────────────────────────

class _FakeEdgeEnt:
    """A BRep edge resolved from a handle; carries .body.name for the result label."""
    def __init__(self, body_name="Block", body=None):
        self.body = body if body is not None else type("B", (), {"name": body_name})()


def _face(body_name="Block"):
    """A BRep face resolved from a handle - what a rule fillet selects through. conftest's shared
    BRepFace, so the face surface stays swept against the live shape record."""
    return BRepFace(surface=None, body_name=body_name)


def _install_edge_handles(handle_map):
    """Install design so the GeometryHandleList resolves edge/face handles. The kind goes through
    _inputs._common.design()/findEntityByToken + isinstance(BRepEdge/BRepFace)."""
    ff = FakeFilletFeatures(); cf = FakeChamferFeatures()
    comp = FakeComp([], ff, cf)
    design = FakeDesign(comp)
    fl.app = type("A", (), {"activeProduct": design})()
    fl._common.app = fl.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.fusion.BRepEdge = _FakeEdgeEnt
    adsk.fusion.BRepFace = BRepFace
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


# ── variable-radius, chord-length and rule fillet types ──────────────────────

class TestFilletType:
    def test_unknown_fillet_type_refused(self):
        _install([FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="all", fillet_type="rolling")
        assert res["isError"] is True and "fillet_type" in res["message"]

    def test_constant_is_the_default_and_is_named_in_the_payload(self):
        ff, _ = _install([FakeBody("B", [True])])
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="all"))
        assert out["fillet_type"] == "constant"
        assert ff.last.variable_set is None and ff.last.chord_set is None

    def test_refused_edge_set_errors_instead_of_adding_a_feature(self):
        # addConstantRadiusEdgeSet returns a bool; a False means nothing was selected, so adding the
        # feature anyway would report a fillet that rounds nothing.
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt()})
        ff.refuse_edge_set = True
        res = fl._fillet_handler(edges=["E1"], radius=1)
        assert res["isError"] is True and "constant-radius" in res["message"]


class TestVariableRadius:
    def test_positions_and_radii_cross_as_plain_lists(self):
        # The API takes positions/radii as vectors - plain Python lists. An ObjectCollection there
        # raises a vector-type argument error live, and the fake raises the same way.
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt()})
        out = _payload(fl._fillet_handler(edges=["E1"], radius=2, end_radius=5, units="mm",
                                          fillet_type="variable",
                                          positions=[0.25, 0.75], radii=[3, 4]))
        tangent_edges, start, end, positions, radii = ff.last.variable_set
        assert isinstance(positions, list) and isinstance(radii, list)
        assert positions == [("real", 0.25), ("real", 0.75)]     # a position is unitless, unscaled
        assert radii == [("real", pytest.approx(0.3)), ("real", pytest.approx(0.4))]  # mm -> cm
        assert start == ("real", 0.2) and end == ("real", 0.5)
        assert tangent_edges.count == 1
        assert ff.last.edge_set is None                          # not the constant-radius path
        assert out["fillet_type"] == "variable" and out["end_radius"] == 5
        assert out["positions"] == [0.25, 0.75] and out["radii"] == [3.0, 4.0]

    def test_without_intermediate_radii_the_arrays_are_empty(self):
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt()})
        out = _payload(fl._fillet_handler(edges=["E1"], radius=1, end_radius=2,
                                          fillet_type="variable"))
        _, _, _, positions, radii = ff.last.variable_set
        assert positions == [] and radii == []
        assert "positions" not in out and "radii" not in out

    def test_needs_end_radius(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})
        res = fl._fillet_handler(edges=["E1"], radius=1, fillet_type="variable")
        assert res["isError"] is True and "end_radius" in res["message"]

    def test_refuses_mismatched_positions_and_radii(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})
        res = fl._fillet_handler(edges=["E1"], radius=1, end_radius=2, fillet_type="variable",
                                 positions=[0.3, 0.6], radii=[4])
        assert res["isError"] is True
        assert "2 position(s)" in res["message"] and "1 radius(es)" in res["message"]

    def test_refuses_a_position_outside_the_unit_interval(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})
        res = fl._fillet_handler(edges=["E1"], radius=1, end_radius=2, fillet_type="variable",
                                 positions=[1.5], radii=[4])
        assert res["isError"] is True
        assert "'positions'[0] is 1.5" in res["message"]

    def test_refuses_a_nonpositive_intermediate_radius(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})
        res = fl._fillet_handler(edges=["E1"], radius=1, end_radius=2, fillet_type="variable",
                                 positions=[0.5], radii=[0])
        assert res["isError"] is True and "'radii'[0] must be positive" in res["message"]

    def test_requires_edge_handles_not_a_filter_sweep(self):
        # The chain must be tangentially connected and in order; a filter sweep has no such order,
        # so there is no start end for the start radius.
        _install([FakeBody("B", [True, True])])
        res = fl._fillet_handler(body_name="B", radius=1, end_radius=2, edge_filter="all",
                                 fillet_type="variable")
        assert res["isError"] is True and "edges" in res["message"]

    def test_refused_variable_edge_set_errors(self):
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt()})
        ff.refuse_edge_set = True
        res = fl._fillet_handler(edges=["E1"], radius=1, end_radius=2, fillet_type="variable")
        assert res["isError"] is True and "variable-radius" in res["message"]


class TestChordLength:
    def test_chord_length_scaled_and_tangent_chained(self):
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt()})
        out = _payload(fl._fillet_handler(edges=["E1"], fillet_type="chord_length",
                                          chord_length=2, units="mm"))
        edges, chord, tangent = ff.last.chord_set
        assert chord == ("real", 0.2) and tangent is True
        assert ff.last.edge_set is None
        assert out["chord_length"] == 2 and "radius" not in out
        assert out["fillet_type"] == "chord_length"

    def test_needs_a_chord_length(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})
        res = fl._fillet_handler(edges=["E1"], radius=3, fillet_type="chord_length")
        assert res["isError"] is True and "chord_length" in res["message"]

    def test_nonpositive_chord_length_names_the_input(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})
        res = fl._fillet_handler(edges=["E1"], fillet_type="chord_length", chord_length=-1)
        assert res["isError"] is True and "positive chord_length" in res["message"]

    def test_sweeps_a_body_by_filter_too(self):
        ff, _ = _install([FakeBody("B", [True, False])])
        out = _payload(fl._fillet_handler(body_name="B", fillet_type="chord_length",
                                          chord_length=1, edge_filter="convex"))
        assert out["edges_requested"] == 1
        assert ff.last.chord_set[1] == ("real", 0.1)


class TestRuleFillet:
    def test_all_edges_takes_a_plain_face_list(self):
        f1, f2 = _face(), _face()
        ff, _ = _install_edge_handles({"F1": f1, "F2": f2})
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1", "F2"], radius=3,
                                          units="mm"))
        assert ff.rule_last.all_edges == [f1, f2]
        assert ff.rule_last.between is None
        assert ff.rule_last.radius == ("real", pytest.approx(0.3))
        assert out["rule"] == "all_edges" and out["faces_selected"] == 2
        assert out["fillet_type"] == "rule" and out["faces_created"] == 4

    def test_between_two_face_sets(self):
        f1, f2 = _face(), _face()
        ff, _ = _install_edge_handles({"F1": f1, "F2": f2})
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], second_faces=["F2"],
                                          radius=1))
        assert ff.rule_last.between == ([f1], [f2])
        assert ff.rule_last.all_edges is None
        assert out["rule"] == "between_faces" and out["faces_selected"] == 2

    def test_topology_maps_to_the_api_member(self):
        import adsk.fusion
        ff, _ = _install_edge_handles({"F1": _face()})
        _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=1,
                                    topology="rounds_only"))
        assert (ff.rule_last.topologyType
                is adsk.fusion.RuleFilletTopologyTypes.RoundsOnlyRuleFilletTopologyType)

    def test_default_topology_takes_rounds_and_fillets(self):
        import adsk.fusion
        ff, _ = _install_edge_handles({"F1": _face()})
        _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=1))
        assert (ff.rule_last.topologyType
                is adsk.fusion.RuleFilletTopologyTypes.RoundsAndFilletsRuleFilletTopologyType)

    def test_unknown_topology_refused(self):
        _install_edge_handles({"F1": _face()})
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=1, topology="corners")
        assert res["isError"] is True and "topology" in res["message"]

    def test_needs_faces(self):
        _install_edge_handles({})
        res = fl._fillet_handler(fillet_type="rule", radius=1)
        assert res["isError"] is True and "faces" in res["message"]

    def test_an_edge_handle_in_faces_is_refused(self):
        _install_edge_handles({"E1": _FakeEdgeEnt()})
        res = fl._fillet_handler(fillet_type="rule", faces=["E1"], radius=1)
        assert res["isError"] is True and "must be a face" in res["message"]

    def test_zero_created_faces_errors_and_rolls_back(self):
        ff, _ = _install_edge_handles({"F1": _face()})
        ff.rule_result = FakeCountingFeature("RuleFillet1", faces=0)
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=1)
        assert res["isError"] is True and "rounded nothing" in res["message"]
        assert ff.rule_result.deleted is True

    def test_unchanged_volume_beats_a_positive_face_count(self):
        # The measured volume is the authority when it can be read: a feature reporting 4 created
        # faces over geometry that did not move is still a no-op, not a rounded body.
        body = FakeBody("B", [], volume=10.0)
        ff, _ = _install_edge_handles({"F1": BRepFace(surface=None, body=body)})
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=1)
        assert res["isError"] is True and "rounded nothing" in res["message"]
        assert ff.rule_result.deleted is True

    def test_measured_volume_change_is_reported(self):
        body = FakeBody("B", [], volume=10.0)
        ff, _ = _install_edge_handles({"F1": BRepFace(surface=None, body=body)})
        ff.on_add = lambda: setattr(body, "volume", 9.75)
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=1))
        assert out["volume_delta_cm3"] == -0.25

    def test_refused_face_set_errors(self):
        ff, _ = _install_edge_handles({"F1": _face()})
        ff.refuse_edge_set = True
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=1)
        assert res["isError"] is True and "refused" in res["message"]

    def test_nonpositive_radius_refused(self):
        _install_edge_handles({"F1": _face()})
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=0)
        assert res["isError"] is True and "positive radius" in res["message"]


class TestVolumeReadBack:
    def test_unchanged_volume_errors_and_rolls_back(self):
        # A fillet cuts a convex corner away or fills a concave one; an unchanged volume means the
        # feature exists but moved nothing, which must not read as filleted:true.
        body = FakeBody("B", [True], volume=10.0)
        ff, _ = _install([body])
        ff.result = FakeCountingFeature("Fillet1", faces=1)
        res = fl._fillet_handler(body_name="B", radius=1, edge_filter="all")
        assert res["isError"] is True and "moved no material" in res["message"]
        assert ff.result.deleted is True

    def test_measured_volume_change_is_reported(self):
        body = FakeBody("B", [True], volume=10.0)
        ff, _ = _install([body])
        ff.result = FakeCountingFeature("Fillet1", faces=1)
        ff.on_add = lambda: setattr(body, "volume", 9.5)
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="all"))
        assert out["volume_delta_cm3"] == -0.5

    def test_unreadable_volume_is_not_treated_as_a_no_op(self):
        ff, _ = _install([FakeBody("B", [True])])          # volume defaults to None
        ff.result = FakeCountingFeature("Fillet1", faces=1)
        out = _payload(fl._fillet_handler(body_name="B", radius=1, edge_filter="all"))
        assert out["filleted"] is True and "volume_delta_cm3" not in out

    def test_chamfer_with_unchanged_volume_errors_and_rolls_back(self):
        # A chamfer that moved no material must not read chamfered:true - the same volume gate as
        # its fillet sibling (a bevel always removes or adds material).
        body = FakeBody("B", [True], volume=10.0)
        _, cf = _install([body])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        res = fl._chamfer_handler(body_name="B", distance=1, edge_filter="all")
        assert res["isError"] is True and "moved no material" in res["message"]
        assert cf.result.deleted is True

    def test_chamfer_reports_volume_delta_like_its_sibling(self):
        body = FakeBody("B", [True], volume=10.0)
        _, cf = _install([body])
        cf.result = FakeCountingFeature("Chamfer1", faces=1)
        cf.on_add = lambda: setattr(body, "volume", 9.75)
        out = _payload(fl._chamfer_handler(body_name="B", distance=1, edge_filter="all"))
        assert out["chamfered"] is True and out["volume_delta_cm3"] == -0.25


class TestRuleFilletReadsBackWhatItApplied:
    """ruleFilletSettings carries the applied radius and topology (live-verified: radius 0.3 cm and
    topologyType 0 read back off a real rule fillet), so a setter the API ignored is caught."""

    def test_a_radius_that_did_not_land_is_an_error(self):
        ff, _ = _install_edge_handles({"F1": _face()})
        ff.rule_settings_override = type(
            "S", (), {"radius": type("V", (), {"value": 9.9})(), "topologyType": 0})()
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=3, units="mm")
        assert res["isError"] is True
        assert "radius reads back" in res["message"] and "RuleFillet1" in res["message"]

    def test_a_topology_that_did_not_land_is_an_error(self):
        ff, _ = _install_edge_handles({"F1": _face()})
        ff.rule_settings_override = type(
            "S", (), {"radius": type("V", (), {"value": 0.3})(), "topologyType": 99})()
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=3, units="mm")
        assert res["isError"] is True and "topology is not the requested" in res["message"]

    def test_the_reported_radius_comes_from_the_feature(self):
        _install_edge_handles({"F1": _face()})
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=3, units="mm"))
        assert out["radius"] == 3.0


class TestFilletGuardsBite:
    """Input guards on the three new fillet shapes - none had an asserting test."""

    def test_variable_without_edges_is_refused(self):
        _install_edge_handles({"F1": _face()})
        res = fl._fillet_handler(fillet_type="variable", radius=2, end_radius=5, units="mm")
        assert res["isError"] is True and "edges" in res["message"]

    def test_chord_length_without_its_length_is_refused(self):
        _install_edge_handles({"E1": _FakeEdgeEnt("Bracket")})
        res = fl._fillet_handler(fillet_type="chord_length", edges=["E1"], units="mm")
        assert res["isError"] is True and "chord_length" in res["message"]

    def test_an_unknown_unit_is_refused(self):
        _install_edge_handles({"E1": _FakeEdgeEnt("Bracket")})
        res = fl._fillet_handler(fillet_type="constant", edges=["E1"], radius=2, units="furlong")
        assert res["isError"] is True and "furlong" in res["message"]

    def test_rule_without_faces_is_refused(self):
        _install_edge_handles({"E1": _FakeEdgeEnt("Bracket")})
        res = fl._fillet_handler(fillet_type="rule", radius=2, units="mm")
        assert res["isError"] is True and "faces" in res["message"]

    def test_a_position_at_either_endpoint_is_refused(self):
        # add() raises "position value must be greater than 0 and less than 1" - the interval is
        # open, and the two ends already carry 'radius' and 'end_radius'.
        for endpoint in (0.0, 1.0):
            _install_edge_handles({"E1": _FakeEdgeEnt("Bracket")})
            res = fl._fillet_handler(fillet_type="variable", edges=["E1"], radius=2, end_radius=5,
                                     positions=[endpoint], radii=[3], units="mm")
            assert res["isError"] is True
            assert str(endpoint) in res["message"] and "exclusive" in res["message"]

    def test_an_interior_position_is_accepted(self):
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt("Bracket")})
        ff.result = FakeCountingFeature("Fillet1", faces=3)
        ff.on_add = lambda: setattr(ff, "_bumped", True)
        res = fl._fillet_handler(fillet_type="variable", edges=["E1"], radius=2, end_radius=5,
                                 positions=[0.5], radii=[3], units="mm")
        assert res.get("isError") is not True or "exclusive" not in res.get("message", "")

    def test_a_feature_reported_failed_is_an_error_not_a_tangent_diagnosis(self):
        # A mis-ordered variable chain yields healthState 2 with NO message and 0 faces. Reading
        # only the face count would blame a tangent edge - a cause that observation cannot support.
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt("Bracket")})
        ff.result = FakeCountingFeature("Fillet1", faces=0, health=2, message="")
        res = fl._fillet_handler(fillet_type="variable", edges=["E1"], radius=2, end_radius=5,
                                 units="mm")
        assert res["isError"] is True
        assert "FAILED" in res["message"] and "reports no message" in res["message"]
        assert "TANGENT edge" not in res["message"]
        assert ff.result.deleted is True              # the failed feature is rolled back

    def test_a_failed_feature_passes_fusions_own_message_through(self):
        ff, _ = _install_edge_handles({"E1": _FakeEdgeEnt("Bracket")})
        ff.result = FakeCountingFeature("Fillet1", faces=0, health=2,
                                        message="radius too large for the edge")
        res = fl._fillet_handler(edges=["E1"], radius=2, units="mm")
        assert res["isError"] is True and "radius too large for the edge" in res["message"]

    def test_size_hint_only_when_the_platform_text_names_a_size_cause(self):
        # The retry hint is gated on the raise's own text: a size-flavored raise gets "try a
        # smaller value", any other raise (a tangent-chain conflict reads the same at every
        # radius) must NOT - that hint sent agents into shrink-and-retry loops no radius fixes.
        def raising(msg):
            def _raise():
                raise RuntimeError(msg)
            return _raise
        ff, _ = _install([FakeBody("B", [True])])
        ff.on_add = raising("5 : radius too large for adjacent geometry")
        res = fl._fillet_handler(body_name="B", radius=50, edge_filter="all")
        assert res["isError"] is True and "try a smaller value" in res["message"]

        ff, _ = _install([FakeBody("B", [True])])
        ff.on_add = raising("2 : FILLET_NO_EDGE_FOUND")
        res = fl._fillet_handler(body_name="B", radius=50, edge_filter="all")
        assert res["isError"] is True
        assert "try a smaller value" not in res["message"]
        assert "not necessarily the problem" in res["message"]


def _parametric(monkeypatch, install, *args, engine=None, **kw):
    """`install` (either installer) plus conftest's shared units engine on the design and a
    ValueInput.createByString seam, so the two ValueInput forms are told apart by shape:
    ('real', cm) vs ('string', expr).

    The default engine resolves 'WallT/2' and nothing else, at 6.5 mm - which the engine's measured
    unit mapping answers as 0.65 internal cm, a value no literal in these tests uses. Passing
    valid=() gives an engine that RAISES on every call, which is how a literal is proved never to
    reach it."""
    import adsk.core
    out = install(*args, **kw)
    design = fl._inputs._common.design()
    design.unitsManager = engine if engine is not None else FakeUnitsManager(valid=("WallT/2",),
                                                                             value=6.5)
    monkeypatch.setattr(adsk.core.ValueInput, "createByString",
                        staticmethod(lambda s: ("string", s)), raising=False)
    return out + (design,)


def _blind_engine():
    """A units engine that resolves NOTHING - every evaluateExpression raises."""
    return FakeUnitsManager(valid=())


class TestRadiusTakesAParameterExpression:
    """A fillet radius may be a parameter EXPRESSION, so the fillet is driven by a user parameter
    rather than frozen at a number. Only the RADIUS: the chamfer's distance is compared as a number
    against the distance the created feature reports, and 'chord_length' is not opened here - both
    stay numeric, and their schemas say so."""

    def test_an_expression_radius_crosses_as_the_string_not_an_evaluated_number(self, monkeypatch):
        ff, _cf, _d = _parametric(monkeypatch, _install, [FakeBody("B", [True])])
        out = _payload(fl._fillet_handler(body_name="B", radius="WallT/2", units="mm",
                                          edge_filter="all"))
        _edges, val, _tangent = ff.last.edge_set
        assert val == ("string", "WallT/2")
        # not the evaluated 0.65 cm, and not any scaling of it: substituting the number would
        # freeze the fillet at today's value of the parameter
        assert val != ("real", 0.65) and val != ("real", 0.065)
        assert out["radius"] == "WallT/2"

    def test_a_literal_radius_still_crosses_as_a_scaled_number(self, monkeypatch):
        # the engine resolves nothing, so a literal routed through it would come back refused
        ff, _cf, _d = _parametric(monkeypatch, _install, [FakeBody("B", [True])],
                                  engine=_blind_engine())
        out = _payload(fl._fillet_handler(body_name="B", radius=2, units="mm", edge_filter="all"))
        assert ff.last.edge_set[1] == ("real", pytest.approx(0.2))
        assert out["radius"] == 2.0

    def test_a_numeric_STRING_is_a_literal_not_an_expression(self, monkeypatch):
        # '2' is a number written as a string - resolving it as an expression would tie the fillet
        # to nothing, and against this engine it would be refused outright
        ff, _cf, _d = _parametric(monkeypatch, _install, [FakeBody("B", [True])],
                                  engine=_blind_engine())
        out = _payload(fl._fillet_handler(body_name="B", radius="2", units="mm", edge_filter="all"))
        assert ff.last.edge_set[1] == ("real", pytest.approx(0.2))
        assert out["radius"] == 2.0

    def test_an_unresolvable_expression_is_refused_before_any_feature_is_built(self, monkeypatch):
        ff, _cf, _d = _parametric(monkeypatch, _install, [FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="B", radius="Missing/2", units="mm", edge_filter="all")
        assert res["isError"] is True
        assert "'radius'" in res["message"] and "Missing/2" in res["message"]
        assert "param_get" in res["message"]
        assert ff.last is None            # createInput was never reached

    def test_the_expression_is_refused_ahead_of_the_edge_scope_guard(self, monkeypatch):
        # the same order a literal is judged in: the size before the edge scope
        _parametric(monkeypatch, _install, [FakeBody("B", [True])])
        res = fl._fillet_handler(body_name="B", radius="Missing/2", units="mm")
        assert res["isError"] is True and "did not evaluate" in res["message"]

    def test_a_variable_fillets_START_radius_takes_the_expression(self, monkeypatch):
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles,
                                  {"E1": _FakeEdgeEnt("Bracket")})
        ff.result = FakeCountingFeature("Fillet1", faces=1)
        _payload(fl._fillet_handler(fillet_type="variable", edges=["E1"], radius="WallT/2",
                                    end_radius=5, units="mm"))
        _edges, start, end, _pos, _rad = ff.last.variable_set
        assert start == ("string", "WallT/2")
        assert end == ("real", pytest.approx(0.5))     # end_radius stays a number

    def test_a_chord_LENGTH_is_still_a_number_only(self, monkeypatch):
        # the expression form is opened on 'radius' alone; chord_length's schema types it as a
        # number, and the refusal is what keeps the two surfaces saying the same thing
        _parametric(monkeypatch, _install_edge_handles, {"E1": _FakeEdgeEnt("Bracket")})
        res = fl._fillet_handler(fillet_type="chord_length", edges=["E1"], chord_length="WallT/2",
                                 units="mm")
        assert res["isError"] is True and "'chord_length' must be a number." in res["message"]

    def test_a_chamfer_distance_is_still_a_number_only(self, monkeypatch):
        # _chamfer_readback compares the created feature's distance against this number
        _parametric(monkeypatch, _install, [FakeBody("B", [True])])
        res = fl._chamfer_handler(body_name="B", distance="Chamf", units="mm", edge_filter="all")
        assert res["isError"] is True and "'distance' must be a number." in res["message"]

    def test_the_positive_and_zero_guards_still_bite_on_a_literal(self, monkeypatch):
        _parametric(monkeypatch, _install, [FakeBody("B", [True])])
        for bad in (0, -1):
            res = fl._fillet_handler(body_name="B", radius=bad, units="mm", edge_filter="all")
            assert res["isError"] is True and "positive radius" in res["message"]

    def test_an_expression_evaluating_NON_POSITIVE_is_refused_naming_the_value(self, monkeypatch):
        # '-1 mm' and '0 mm' are legal expressions the engine resolves happily; only the evaluated
        # value catches them, and without it they reach filletFeatures.add
        for expr, value, shown in (("Neg", -1.0, "-1.0 mm"), ("Zero", 0.0, "0.0 mm")):
            ff, _cf, _d = _parametric(monkeypatch, _install, [FakeBody("B", [True])],
                                      engine=FakeUnitsManager(valid=(expr,), value=value))
            res = fl._fillet_handler(body_name="B", radius=expr, units="mm", edge_filter="all")
            assert res["isError"] is True, expr
            assert "positive radius" in res["message"] and f"'{expr}'" in res["message"]
            assert shown in res["message"]
            assert ff.last is None            # refused before the input was built

    def test_the_boundary_a_hair_ABOVE_zero_is_accepted(self, monkeypatch):
        # the guard is <= 0, not < 0: the smallest positive value must still build
        ff, _cf, _d = _parametric(monkeypatch, _install, [FakeBody("B", [True])],
                                  engine=FakeUnitsManager(valid=("Tiny",), value=0.001))
        _payload(fl._fillet_handler(body_name="B", radius="Tiny", units="mm", edge_filter="all"))
        assert ff.last.edge_set[1] == ("string", "Tiny")

    def test_a_units_engine_answering_a_NON_NUMBER_does_not_refuse_the_call(self, monkeypatch):
        # an unreadable evaluation is not evidence of a bad radius - it withholds the guard rather
        # than inventing a verdict
        engine = FakeUnitsManager(valid=("Odd",))
        engine.evaluateExpression = lambda expr, units=None: object()
        ff, _cf, _d = _parametric(monkeypatch, _install, [FakeBody("B", [True])], engine=engine)
        _payload(fl._fillet_handler(body_name="B", radius="Odd", units="mm", edge_filter="all"))
        assert ff.last.edge_set[1] == ("string", "Odd")


class TestRuleFilletRadiusTakesAnExpression:
    """The rule fillet's radius is the same input, so it takes the same two forms - and the applied
    radius is read back and compared on BOTH: a literal against its own scaled number, an
    expression against the value the units engine evaluated it to."""

    def _settings(self, cm, topology=0):
        return type("S", (), {"radius": type("V", (), {"value": cm})(),
                              "topologyType": topology})()

    def test_the_expression_reaches_the_rule_input_as_a_string(self, monkeypatch):
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()})
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius="WallT/2",
                                          units="mm"))
        assert ff.rule_last.radius == ("string", "WallT/2")
        assert out["radius_expression"] == "WallT/2"

    def test_an_unresolvable_rule_radius_is_refused_before_the_input_is_built(self, monkeypatch):
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()})
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius="Missing", units="mm")
        assert res["isError"] is True and "did not evaluate" in res["message"]
        assert ff.rule_last is None

    def test_a_settings_radius_that_disagrees_IS_an_error_for_an_expression(self, monkeypatch):
        # the case the skipped compare let through: asked for 6.5 mm, landed at 99, reported ok
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()})
        ff.rule_settings_override = self._settings(9.9)
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius="WallT/2", units="mm")
        assert res["isError"] is True
        assert "radius reads back 99.0 mm" in res["message"]
        assert "6.5 mm" in res["message"] and "'WallT/2'" in res["message"]

    def test_the_same_disagreement_IS_an_error_for_a_literal(self, monkeypatch):
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()})
        ff.rule_settings_override = self._settings(9.9)
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=3, units="mm")
        assert res["isError"] is True and "radius reads back" in res["message"]

    def test_a_radius_MATCHING_the_evaluated_expression_is_accepted(self, monkeypatch):
        # the other half of the compare: the applied radius IS what the expression evaluates to
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()})
        ff.rule_settings_override = self._settings(0.65)
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius="WallT/2",
                                          units="mm"))
        assert out["radius"] == 6.5 and out["radius_expression"] == "WallT/2"

    def test_a_literal_radius_publishes_no_expression_key(self, monkeypatch):
        _parametric(monkeypatch, _install_edge_handles, {"F1": _face()})
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=3, units="mm"))
        assert out["radius"] == 3.0 and "radius_expression" not in out

    def test_the_topology_check_still_bites_under_an_expression(self, monkeypatch):
        # the radius here MATCHES what the expression evaluates to, so the topology mismatch is the
        # one thing left to report
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()})
        ff.rule_settings_override = self._settings(0.65, topology=99)
        res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius="WallT/2", units="mm")
        assert res["isError"] is True and "topology is not the requested" in res["message"]

    def test_a_rule_expression_evaluating_NON_POSITIVE_is_refused(self, monkeypatch):
        # both sides of the <= 0 boundary a legal expression can land on
        for expr, value, shown in (("Neg", -2.0, "-2.0 mm"), ("Zero", 0.0, "0.0 mm")):
            ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()},
                                      engine=FakeUnitsManager(valid=(expr,), value=value))
            res = fl._fillet_handler(fillet_type="rule", faces=["F1"], radius=expr, units="mm")
            assert res["isError"] is True, expr
            assert "positive radius" in res["message"] and shown in res["message"]
            assert ff.rule_last is None

    def test_an_UNREADABLE_evaluation_withholds_the_compare(self, monkeypatch):
        # want_cm None means nothing was measured to compare the applied radius against. Comparing
        # against 0 instead would roll a healthy fillet out reporting "reads back 6.5 mm, not the
        # requested 0.0" - a verdict from a read that never answered.
        engine = FakeUnitsManager(valid=("Odd",))
        engine.evaluateExpression = lambda expr, units=None: object()
        ff, _cf, _d = _parametric(monkeypatch, _install_edge_handles, {"F1": _face()},
                                  engine=engine)
        ff.rule_settings_override = self._settings(0.65)
        out = _payload(fl._fillet_handler(fillet_type="rule", faces=["F1"], radius="Odd",
                                          units="mm"))
        assert out["radius"] == 6.5 and out["radius_expression"] == "Odd"
