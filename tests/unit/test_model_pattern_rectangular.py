"""Unit tests for model_pattern_rectangular.py - the grid, its directions and its counts."""

import json
import types
from conftest import BRepEdge, BRepFace, Circle3D, FakePoint, FakeVector3D, Line3D, Plane, _make_object_collection, entity_proxy, load_tool

pt = load_tool("model_pattern_rectangular")


class FakeOcc:
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name
        # A real Occurrence always answers `component`; a read that RAISES is the
        # unresolved-external-reference signal the shared occurrence census filters on.
        self.component = types.SimpleNamespace(name=name.split(":")[0])


class FakeRectInput:
    def __init__(self, coll, d1, q1, dist1, dist_type, refuse_two=False):
        self.coll = coll
        self.d1 = d1
        self.q1 = q1
        self.dist1 = dist1
        self.dist_type = dist_type
        self.dir_two = None
        self._refuse_two = refuse_two

    def setDirectionTwo(self, d2, q2, dist2):
        # The live setter ANSWERS whether it took; a refusal must not be read as success.
        if self._refuse_two:
            return False
        self.dir_two = (d2, q2, dist2)
        return True


def _component_key(comp):
    """What identifies a COMPONENT across two references to it: its entityToken, falling back to
    identity when a fixture supplies none. Never the object itself - see entity_proxy."""
    return getattr(comp, "entityToken", None) or id(comp)


def _add_refusal(direction, comp):
    """The message the kernel raises at add() for a direction it will not build with, or ''.

    MEASURED, and only at add(): the input accepts a face and a foreign NATIVE entity, and reads
    both back unchanged, so nothing before add() can see the refusal coming.

    The kernel discriminates on the COMPONENT, not on the wrapper object: two references to one
    component are different Python objects sharing one entityToken, so this compares tokens. Keyed by
    identity the fake would refuse an entity from its OWN component and model a kernel that does not
    exist."""
    if isinstance(direction, BRepFace):
        return "3 : Unsupported direction entity's type."
    if isinstance(direction, BRepEdge) and getattr(direction, "assemblyContext", None) is None:
        body = getattr(direction, "body", None)
        owner = getattr(body, "parentComponent", comp) if body is not None else comp
        if owner is not comp and _component_key(owner) != _component_key(comp):
            return "InternalValidationError : res"
    return ""


class FakeRectFeatures:
    def __init__(self, refuse_two=False):
        self.last_input = None
        self.add_calls = 0
        self.comp = None                 # the component this collection hangs off (set by its owner)
        self._refuse_two = refuse_two

    def createInput(self, coll, d1, q1, dist1, dist_type):
        self.last_input = FakeRectInput(coll, d1, q1, dist1, dist_type, self._refuse_two)
        return self.last_input

    def add(self, inp):
        self.add_calls += 1
        for d in (inp.d1, inp.dir_two[0] if inp.dir_two else None):
            msg = _add_refusal(d, self.comp)
            if msg:
                raise RuntimeError(msg)
        return type("F", (), {"name": "R-Pattern1"})()


class FakeCircInput:
    """`ignores` names properties whose assignment the platform silently DROPS - the SWIG-proxy
    shape set_verified exists to catch (the value lands on a dead attribute, no exception, and the
    object keeps its API default)."""
    def __init__(self, coll, axis, ignores=()):
        object.__setattr__(self, "_ignores", set(ignores))
        self.coll = coll
        self.axis = axis
        self.quantity = None
        self.totalAngle = None
        self.isSymmetric = False

    def __setattr__(self, name, value):
        if name in self._ignores:
            return
        object.__setattr__(self, name, value)


class FakeCircFeatures:
    def __init__(self, ignores=()):
        self.last_input = None
        self.ignores = ignores
        self.add_calls = 0

    def createInput(self, coll, axis):
        self.last_input = FakeCircInput(coll, axis, self.ignores)
        return self.last_input

    def add(self, inp):
        self.add_calls += 1
        return type("F", (), {"name": "C-Pattern1"})()


class FakeRoot:
    def __init__(self, occurrences, rf, cf):
        self.name = "Root"
        # Components carry an entityToken and are compared on it: a wrapper is never identity-stable
        # (two reads of design.rootComponent are DIFFERENT objects sharing one token), so a fake that
        # keys on id() models a stability the platform does not have.
        self.entityToken = "TOKEN:Root"
        self.allOccurrences = list(occurrences)
        self.xConstructionAxis = "AXIS_X"
        self.yConstructionAxis = "AXIS_Y"
        self.zConstructionAxis = "AXIS_Z"
        self.features = type("F", (), {"rectangularPatternFeatures": rf,
                                       "circularPatternFeatures": cf})()
        rf.comp = self               # so add() can tell an OWN entity from a foreign native one
        # Components placed nowhere by default; a test that patterns across components installs its
        # own mapping (Fusion's root-level component -> its occurrences lookup).
        self.allOccurrencesByComponent = lambda comp: _occurrences_of(self, comp)
        self.occurrences_by_component = {}


def _occurrences_of(root, comp):
    """The occurrence collection a root exposes for one component (count/item, as Fusion's is).

    Keyed by entityToken, not id(): two references to one component are different Python objects, so
    an id()-keyed fake would answer for one wrapper and not for another wrapper of the SAME
    component."""
    items = root.occurrences_by_component.get(_component_key(comp), [])
    return type("OC", (), {"count": len(items), "item": staticmethod(lambda i: items[i])})()


class FakeDesign:
    def __init__(self, occurrences, rf, cf):
        self.rootComponent = FakeRoot(occurrences, rf, cf)


def _install(occ_names, refuse_two=False, circ_ignores=()):
    rf, cf = FakeRectFeatures(refuse_two), FakeCircFeatures(circ_ignores)
    occs = [FakeOcc(n) for n in occ_names]
    design = FakeDesign(occs, rf, cf)
    pt.app = type("A", (), {"activeProduct": design})()
    pt._common.app = pt.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.ObjectCollection.create = staticmethod(_make_object_collection)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))
    pdt = adsk.fusion.PatternDistanceType
    pdt.SpacingPatternDistanceType = "Spacing"
    pdt.ExtentPatternDistanceType = "Extent"
    return rf, cf


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _FakeBody:
    def __init__(self, name):
        self.name = name


def _install_with_bodies(body_map):
    """Install a design that also resolves body handles/names (the app-reference seam).

    The handler now resolves its design via _common.design() (the SAME seam _inputs uses), so there is
    ONE design. We take _install's rich FakeDesign (its root has the construction axes the handler
    needs) and EXTEND its root with body-by-name + findEntityByToken, then point both _common.design
    and _inputs._common.design at it."""
    rf, cf = _install([])
    import adsk.fusion
    adsk.fusion.BRepBody = _FakeBody
    design = pt.app.activeProduct                 # rich FakeDesign with rootComponent + axes
    root = design.rootComponent
    root.bRepBodies = type("BB", (), {
        "itemByName": staticmethod(lambda n: body_map.get(n)),
        "count": len(body_map), "item": staticmethod(lambda i: list(body_map.values())[i]),
    })()
    design.findEntityByToken = lambda t, bm=body_map: ([bm[t]] if t in bm else [])
    pt._common.design = lambda: design
    pt._common.target_component = lambda x: root
    pt._inputs._common.design = lambda: design
    pt._inputs._common.target_component = lambda x: root
    return rf, cf


class _BodyInSub:
    """A body whose parentComponent is a distinct sub-component (its OWN axes + pattern features).
    Taking the axis from ROOT and building the feature there mismatches the body's object path -
    Fusion raises 'InternalValidationError getObjectPath'. _owning_component must resolve to the
    body's parent."""
    def __init__(self, name, parent):
        self.name = name
        self.parentComponent = parent


def _install_body_in_subcomponent():
    rf, cf = _install([])           # installs adsk fakes (ValueInput, ObjectCollection, axes enum)
    import adsk.fusion
    adsk.fusion.BRepBody = _BodyInSub
    # the SUB-component that owns the body — distinct axes + its OWN pattern-feature collections
    sub_rf, sub_cf = FakeRectFeatures(), FakeCircFeatures()
    sub = type("Sub", (), {
        "xConstructionAxis": "SUB_X", "yConstructionAxis": "SUB_Y", "zConstructionAxis": "SUB_Z",
        "features": type("F", (), {"rectangularPatternFeatures": sub_rf,
                                   "circularPatternFeatures": sub_cf})(),
    })()
    body = _BodyInSub("SubBoss", sub)
    comp = type("C", (), {"bRepBodies": type("BB", (), {
        "itemByName": staticmethod(lambda n: body if n == "SubBoss" else None),
        "count": 1, "item": staticmethod(lambda i: body)})()})()
    # root carries DIFFERENT axes so a mistaken root build would be detectable
    root_rf, root_cf = FakeRectFeatures(), FakeCircFeatures()
    root = type("Root", (), {
        "xConstructionAxis": "ROOT_X", "yConstructionAxis": "ROOT_Y", "zConstructionAxis": "ROOT_Z",
        "features": type("F", (), {"rectangularPatternFeatures": root_rf,
                                   "circularPatternFeatures": root_cf})()})()

    class _D:
        rootComponent = root
        def findEntityByToken(self, t):
            return []
    d = _D()
    pt._inputs._common.design = lambda: d
    pt._inputs._common.target_component = lambda x: comp
    # the tool's own _design() must see this design too
    pt.app = type("A", (), {"activeProduct": d})()
    pt._common.app = pt.app
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _D) else None
    return sub_rf, sub_cf, root_rf, root_cf


def _linear_edge():
    return BRepEdge(curve=Line3D(start=FakePoint(0, 0, 0), end=FakePoint(1, 0, 0)))


def _curved_edge():
    return BRepEdge(curve=Circle3D(normal=FakeVector3D(0, 0, 1)))


def _planar_face():
    return BRepFace(Plane(normal=FakeVector3D(0, 0, 1)))


def _install_with_direction_handles(handle_map):
    """_install plus handle resolution for a DIRECTION (AxisRef routes through findEntityByToken).

    The entity classes must be REAL classes: AxisRef isinstance-checks BRepEdge/SketchLine/BRepFace,
    and a bare Mock attribute is not a type, so isinstance would raise rather than answer False."""
    rf, cf = _install(["Block:1"])
    import adsk.fusion
    adsk.fusion.BRepEdge = BRepEdge
    adsk.fusion.BRepFace = BRepFace
    adsk.fusion.SketchLine = type("SL", (), {})
    design = pt.app.activeProduct
    design.findEntityByToken = lambda t, m=handle_map: ([m[t]] if t in m else [])
    return rf, cf


def _edge_owned_by(component, context=None):
    """A straight edge whose BODY belongs to `component`. context=None is a NATIVE entity."""
    edge = _linear_edge()
    edge.assemblyContext = context
    edge.body = types.SimpleNamespace(parentComponent=component)
    return edge


def _foreign_edge(component):
    """(native edge owned by `component`, the proxy its createForAssemblyContext hands back)."""
    proxy = _edge_owned_by(component, context="ASSEMBLY-CONTEXT")
    edge = _edge_owned_by(component)
    edge.createForAssemblyContext = lambda occ, p=proxy: p
    return edge, proxy


def _place(root, component, *full_paths):
    """Place `component` in the assembly under the given occurrence fullPathNames."""
    root.occurrences_by_component[_component_key(component)] = [
        types.SimpleNamespace(fullPathName=p) for p in full_paths]


def _other_component(name="Rail"):
    return type("C", (), {"name": name, "entityToken": "TOKEN:" + name})()


class TestResolution:

    def test_exact_name(self):
        rf, _ = _install(["Block:1", "Other:1"])
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10))
        assert out["entities"] == ["Block:1"]

    def test_substring_fallback(self):
        rf, _ = _install(["Block:1"])
        out = _payload(pt.handler(occurrences="block", quantity_one=2, spacing_one=10))
        assert out["entities"] == ["Block:1"]

    def test_missing_reported(self):
        _install(["Block:1"])
        res = pt.handler(occurrences="Ghost", quantity_one=2, spacing_one=10)
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_comma_separated_multiple(self):
        _install(["A:1", "B:1"])
        out = _payload(pt.handler(occurrences="A:1, B:1", quantity_one=2, spacing_one=10))
        assert set(out["entities"]) == {"A:1", "B:1"}


class TestRectangular:

    def test_single_direction_scales_spacing(self):
        rf, _ = _install(["Block:1"])
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=3,
                                              spacing_one=30, direction_one="x", units="mm"))
        assert out["total_instances"] == 3
        inp = rf.last_input
        assert inp.d1 == "AXIS_X"
        assert inp.q1 == ("real", 3)
        assert inp.dist1[0] == "real" and abs(inp.dist1[1] - 3.0) < 1e-9   # 30 mm -> 3 cm
        # Direction two is ALWAYS set explicitly to quantity 1 for a single row: a fresh createInput
        # carries a UI-style quantityTwo=3 default, so leaving it unset silently TRIPLES the pattern
        # (verified live: quantity_one=2 with dir-two unset produced 6 coincident instances).
        assert inp.dir_two is not None
        assert inp.dir_two[1] == ("real", 1)     # quantityTwo pinned to 1, never the API default
        assert inp.dist_type == "Spacing"

    def test_two_directions(self):
        rf, _ = _install(["Block:1"])
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=3, spacing_one=30,
                                              direction_one="x", quantity_two=2, spacing_two=20,
                                              direction_two="y", units="mm"))
        assert out["total_instances"] == 6
        d2, q2, dist2 = rf.last_input.dir_two
        assert d2 == "AXIS_Y"
        assert q2 == ("real", 2)
        assert dist2[0] == "real" and abs(dist2[1] - 2.0) < 1e-9   # 20 mm -> 2 cm

    def test_quantity_one_must_be_positive(self):
        _install(["Block:1"])
        res = pt.handler(occurrences="Block:1", quantity_one=0, spacing_one=10)
        assert res["isError"] is True and "quantity_one must be >= 1" in res["message"]

    def test_unknown_direction(self):
        _install(["Block:1"])
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     direction_one="w")
        # Not a world axis and not a resolvable handle: the AxisRef kind names both accepted forms.
        assert res["isError"] is True
        assert "'direction_one': 'w' is not a world axis" in res["message"]
        # entity_only refuses a face, so the miss text must not advertise a face handle either.
        assert "face" not in res["message"]

    def test_unknown_units(self):
        _install(["Block:1"])
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_unknown_direction_two_errors(self):
        # quantity_two>1 forces direction_two resolution; a bad axis must error
        _install(["Block:1"])
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     quantity_two=2, spacing_two=5, direction_two="w")
        assert res["isError"] is True
        assert "'direction_two': 'w' is not a world axis" in res["message"]

    def test_single_row_direction_two_none_in_payload(self):
        _install(["Block:1"])
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=4, spacing_one=10,
                                              quantity_two=1))
        # quantity_two==1 -> direction_two reported as None, total = quantity_one
        assert out["direction_two"] is None
        assert out["total_instances"] == 4

    def test_spacing_scaled_inches(self):
        rf, _ = _install(["Block:1"])
        _payload(pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=1,
                                        units="in"))
        # 1in -> 2.54cm
        assert abs(rf.last_input.dist1[1] - 2.54) < 1e-9


class TestBodyTargets:

    def test_rectangular_patterns_bodies_by_name(self):
        rf, _ = _install_with_bodies({"Boss": _FakeBody("Boss")})
        out = _payload(pt.handler(bodies="Boss", quantity_one=3, spacing_one=10))
        assert out["entity_kind"] == "bodies"
        assert out["entities"] == ["Boss"]
        assert out["total_instances"] == 3

    def test_bodies_take_precedence_over_occurrences(self):
        rf, _ = _install_with_bodies({"Boss": _FakeBody("Boss")})
        out = _payload(pt.handler(occurrences="ignored", bodies="Boss",
                                              quantity_one=2, spacing_one=5))
        assert out["entity_kind"] == "bodies" and out["entities"] == ["Boss"]

    def test_bad_body_name_errors(self):
        _install_with_bodies({"Boss": _FakeBody("Boss")})
        res = pt.handler(bodies="Nope", quantity_one=2, spacing_one=5)
        assert res["isError"] is True and "Nope" in res["message"]


class TestBodyOwningComponent:

    def test_rectangular_builds_on_bodys_parent_component(self):
        sub_rf, sub_cf, root_rf, root_cf = _install_body_in_subcomponent()
        _payload(pt.handler(bodies="SubBoss", quantity_one=3, spacing_one=10,
                                        direction_one="x"))
        assert sub_rf.last_input is not None and sub_rf.last_input.d1 == "SUB_X"
        assert root_rf.last_input is None


class TestDirectionFromGeometry:

    def test_direction_one_takes_an_edge_handle(self):
        e = _linear_edge()
        rf, _ = _install_with_direction_handles({"E1": e})
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=3,
                                              spacing_one=10, direction_one="E1"))
        # the EDGE itself reaches createInput - not the component's construction axis
        assert rf.last_input.d1 is e
        assert out["direction_one"] == "BRepEdge"

    def test_direction_two_takes_an_edge_handle(self):
        e = _linear_edge()
        rf, _ = _install_with_direction_handles({"E2": e})
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                              quantity_two=2, spacing_two=5, direction_two="E2"))
        d2, _q2, _dist2 = rf.last_input.dir_two
        assert d2 is e
        assert out["direction_two"] == "BRepEdge"

    def test_world_axis_still_resolves_to_the_construction_axis(self):
        rf, _ = _install_with_direction_handles({})
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=2,
                                              spacing_one=10, direction_one="y"))
        assert rf.last_input.d1 == "AXIS_Y"
        assert out["direction_one"] == "y"

    def test_blank_direction_uses_the_kinds_default_axis(self):
        rf, _ = _install_with_direction_handles({})
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=2,
                                              spacing_one=10, direction_one=""))
        assert rf.last_input.d1 == "AXIS_X"          # AxisRef default 'x', not a blank lookup
        assert out["direction_one"] == "x"

    def test_curved_edge_handle_refused(self):
        rf, _ = _install_with_direction_handles({"ARC": _curved_edge()})
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     direction_one="ARC")
        assert res["isError"] is True and "not straight" in res["message"]
        assert rf.last_input is None                 # refused before any feature transaction opened

    def test_face_handle_refused_naming_what_it_is(self):
        rf, _ = _install_with_direction_handles({"F": _planar_face()})
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     direction_one="F")
        assert res["isError"] is True
        assert "direction VECTOR" in res["message"] and "linear ENTITY" in res["message"]
        assert rf.last_input is None

    def test_bad_direction_two_is_refused_before_createinput(self):
        # direction_two is ALWAYS set, so it must be resolved before the transaction starts -
        # otherwise a bad value aborts a half-built pattern input.
        rf, _ = _install_with_direction_handles({})
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     quantity_two=2, spacing_two=5, direction_two="nope")
        assert res["isError"] is True
        assert rf.last_input is None

    def test_refused_second_direction_stops_before_add(self):
        # setDirectionTwo ANSWERS whether it took; a false must abort, not fall through to add()
        # and report a pattern built on the API's own default second direction.
        rf, _ = _install(["Block:1"], refuse_two=True)
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     quantity_two=2, spacing_two=5)
        assert res["isError"] is True and "setDirectionTwo returned" in res["message"]
        assert rf.add_calls == 0


class TestDirectionInThePatternsOwnComponent:

    """A direction owned by the pattern's OWN component passes straight through. The owner and the
    pattern's component are DISTINCT wrappers sharing one entityToken - the measured shape (two reads
    of design.rootComponent are different objects). An identity test reads False here, falls through
    to allOccurrencesByComponent, gets 0 for the root, and REFUSES a legal direction while telling
    the caller to pass exactly what they just passed."""

    def test_own_root_edge_passes_through_untouched(self):
        handles = {}
        rf, _ = _install_with_direction_handles(handles)
        root = pt.app.activeProduct.rootComponent
        # the edge's body reports a DIFFERENT wrapper of the same root component
        edge = _edge_owned_by(entity_proxy(root))
        handles["E"] = edge
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=2,
                                              spacing_one=10, direction_one="E"))
        assert rf.last_input.d1 is edge           # the NATIVE entity reaches createInput, unproxied
        assert out["direction_one"] == "BRepEdge"

    def test_own_component_edge_is_not_refused_for_being_unplaced(self):
        # An unplaced own-component direction is legal: the "belongs to component '...', which is not
        # placed in the assembly" refusal is for a FOREIGN component's geometry, never for the
        # pattern's own.
        handles = {}
        rf, _ = _install_with_direction_handles(handles)
        root = pt.app.activeProduct.rootComponent
        handles["E"] = _edge_owned_by(entity_proxy(root))
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     direction_one="E")
        assert res["isError"] is False, res
        assert rf.add_calls == 1


class TestDirectionFromAnotherComponent:

    def test_native_foreign_edge_is_proxied_into_its_single_occurrence(self):
        handles = {}
        rf, _ = _install_with_direction_handles(handles)
        root = pt.app.activeProduct.rootComponent
        rail = _other_component()
        edge, proxy = _foreign_edge(rail)
        _place(root, rail, "Assy:1+Rail:1")
        handles["E"] = edge
        out = _payload(pt.handler(occurrences="Block:1", quantity_one=2,
                                              spacing_one=10, direction_one="E"))
        assert rf.last_input.d1 is proxy          # the PROXY reaches createInput, not the native
        assert rf.add_calls == 1                  # add() accepted it; a native one raises
        assert out["direction_one"] == "BRepEdge"

    def test_two_occurrences_refused_naming_each_path(self):
        handles = {}
        rf, _ = _install_with_direction_handles(handles)
        root = pt.app.activeProduct.rootComponent
        rail = _other_component()
        edge, _proxy = _foreign_edge(rail)
        _place(root, rail, "Assy:1+Rail:1", "Assy:1+Rail:2")
        handles["E"] = edge
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     direction_one="E")
        assert res["isError"] is True
        assert "Assy:1+Rail:1" in res["message"] and "Assy:1+Rail:2" in res["message"]
        assert rf.last_input is None and rf.add_calls == 0

    def test_unplaced_component_direction_refused(self):
        handles = {}
        rf, _ = _install_with_direction_handles(handles)
        rail = _other_component()
        edge, _proxy = _foreign_edge(rail)         # never placed - no occurrence to proxy into
        handles["E"] = edge
        res = pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                     direction_one="E")
        assert res["isError"] is True and "not placed in the assembly" in res["message"]
        assert rf.last_input is None

    def test_edge_already_in_context_passes_untouched(self):
        handles = {}
        rf, _ = _install_with_direction_handles(handles)
        rail = _other_component()
        handles["E"] = _edge_owned_by(rail, context="ASSEMBLY-CONTEXT")
        _payload(pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                        direction_one="E"))
        assert rf.last_input.d1 is handles["E"]    # already a proxy - not re-proxied
        assert rf.add_calls == 1

    def test_same_component_edge_passes_untouched(self):
        handles = {}
        rf, _ = _install_with_direction_handles(handles)
        root = pt.app.activeProduct.rootComponent
        handles["E"] = _edge_owned_by(root)        # native, but the pattern's OWN component
        _payload(pt.handler(occurrences="Block:1", quantity_one=2, spacing_one=10,
                                        direction_one="E"))
        assert rf.last_input.d1 is handles["E"]
        assert rf.add_calls == 1


class TestZeroSpacingGuards:

    def test_rect_zero_spacing_one_refused(self):
        # spacing_one=0 with quantity>1 stacks every instance on the seed (coincident duplicates
        # reported as a clean pattern) - refused before any feature transaction opens.
        res = pt.handler(bodies="B", quantity_one=3, spacing_one=0)
        assert res["isError"] is True and "spacing_one=0" in res["message"]

    def test_rect_zero_spacing_two_refused(self):
        res = pt.handler(bodies="B", quantity_one=2, spacing_one=5,
                                     quantity_two=2, spacing_two=0)
        assert res["isError"] is True and "spacing_two=0" in res["message"]

    def test_single_row_zero_spacing_two_is_fine_to_pass_the_guard(self, monkeypatch):
        # quantity_two=1 never uses spacing_two - the guard must not refuse it. The absent-design
        # error AFTER the guard is the proof the guard let the call through.
        monkeypatch.setattr(pt._common, "design", lambda: None)
        res = pt.handler(bodies="B", quantity_one=2, spacing_one=5,
                                     quantity_two=1, spacing_two=0)
        assert "spacing_two" not in res.get("message", "")
        assert "No active design" in res["message"]
