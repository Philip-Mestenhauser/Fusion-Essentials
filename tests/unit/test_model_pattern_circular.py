"""Unit tests for model_pattern_circular.py - the ring, its axis and its counts."""

import json
import types
from conftest import BRepEdge, BRepFace, Cylinder, FakePoint, FakeVector3D, Line3D, Plane, _NamedCollection, _SimpleNamed, _make_object_collection, load_tool

pt = load_tool("model_pattern_circular")


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


def _planar_face():
    return BRepFace(Plane(normal=FakeVector3D(0, 0, 1)))


def _place(root, component, *full_paths):
    """Place `component` in the assembly under the given occurrence fullPathNames."""
    root.occurrences_by_component[_component_key(component)] = [
        types.SimpleNamespace(fullPathName=p) for p in full_paths]


def _other_component(name="Rail"):
    return type("C", (), {"name": name, "entityToken": "TOKEN:" + name})()


def _install_with_axis_handles(handle_map=None, axes=()):
    """_install plus token resolution and a component that owns construction axes (the two paths
    AxisRef adds beyond a world key).

    A construction axis reaches this tool as a NAMED entity it passes straight to createInput, so
    conftest's name-only stand-in carries it - bound as the ConstructionAxis type AxisRef
    isinstance-checks (a bare Mock attribute is not a type)."""
    rf, cf = _install(["Spoke:1"])
    import adsk.fusion
    adsk.fusion.BRepEdge = BRepEdge
    adsk.fusion.BRepFace = BRepFace
    adsk.fusion.SketchLine = type("SL", (), {})
    adsk.fusion.ConstructionAxis = _SimpleNamed
    design = pt.app.activeProduct
    design.findEntityByToken = lambda t, m=dict(handle_map or {}): ([m[t]] if t in m else [])
    design.rootComponent.constructionAxes = _NamedCollection(list(axes))
    return rf, cf


def _cylindrical_face():
    return BRepFace(Cylinder(FakeVector3D(0, 0, 1)))


class TestCircular:

    def test_basic_full_ring(self):
        _, cf = _install(["Spoke:1"])
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=6,
                                           total_angle_deg=360, axis="z"))
        assert out["quantity"] == 6
        inp = cf.last_input
        assert inp.axis == "AXIS_Z"
        assert inp.quantity == ("real", 6)
        assert inp.totalAngle == ("str", "360.0 deg")

    def test_axis_selection(self):
        _, cf = _install(["Spoke:1"])
        _payload(pt.handler(occurrences="Spoke:1", quantity=4, axis="y"))
        assert cf.last_input.axis == "AXIS_Y"

    def test_symmetric_flag(self):
        _, cf = _install(["Spoke:1"])
        _payload(pt.handler(occurrences="Spoke:1", quantity=4, symmetric=True))
        assert cf.last_input.isSymmetric is True
        assert cf.add_calls == 1

    def test_a_symmetric_the_platform_drops_refuses_before_the_pattern_runs(self):
        # A SWIG proxy ACCEPTS an assignment it then ignores, with no exception - and the only
        # read-back a pattern has (patternElements.count) is IDENTICAL for a symmetric and an
        # asymmetric spread, so nothing downstream would ever catch it.
        _, cf = _install(["Spoke:1"], circ_ignores=("isSymmetric",))
        res = pt.handler(occurrences="Spoke:1", quantity=4, symmetric=True)
        assert res["isError"] is True
        assert "symmetric" in res["message"] and "No pattern was created" in res["message"]
        assert cf.add_calls == 0

    def test_quantity_must_be_at_least_two(self):
        _install(["Spoke:1"])
        res = pt.handler(occurrences="Spoke:1", quantity=1)
        assert res["isError"] is True and "quantity must be >= 2" in res["message"]

    def test_unknown_axis(self):
        # AxisRef owns the refusal now - it names every accepted form, not just x/y/z.
        _install(["Spoke:1"])
        res = pt.handler(occurrences="Spoke:1", quantity=4, axis="w")
        assert res["isError"] is True
        assert "'axis': 'w' is not a world axis" in res["message"]

    def test_partial_arc_angle_string(self):
        _, cf = _install(["Spoke:1"])
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=3, total_angle_deg=90))
        # the angle is formatted as a "<float> deg" ValueInput string and echoed in payload
        assert cf.last_input.totalAngle == ("str", "90.0 deg")
        assert out["total_angle_deg"] == 90.0

    def test_symmetric_defaults_false(self):
        _, cf = _install(["Spoke:1"])
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=4))
        assert cf.last_input.isSymmetric is False
        assert out["symmetric"] is False


class TestBodyTargets:

    def test_circular_patterns_bodies_by_handle(self):
        h = "/v" + "B" * 70
        rf, _ = _install_with_bodies({h: _FakeBody("FromHandle")})
        out = _payload(pt.handler(bodies=h, quantity=4))
        assert out["entity_kind"] == "bodies"
        assert out["entities"] == ["FromHandle"]


class TestBodyOwningComponent:

    def test_circular_builds_on_bodys_parent_component(self):
        sub_rf, sub_cf, root_rf, root_cf = _install_body_in_subcomponent()
        out = _payload(pt.handler(bodies="SubBoss", quantity=6, axis="y"))
        assert out["entity_kind"] == "bodies"
        # the feature was created on the SUB-component (axis from the sub, not root)
        assert sub_cf.last_input is not None and sub_cf.last_input.axis == "SUB_Y"
        assert root_cf.last_input is None          # root must NOT be used


class TestCircularAxisFromGeometry:

    def test_cylindrical_face_handle_reaches_createinput_as_the_face(self):
        f = _cylindrical_face()
        _, cf = _install_with_axis_handles({"CYL": f})
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=5, axis="CYL"))
        assert cf.last_input.axis is f            # the FACE, not the component's origin axis
        assert out["axis"] == "BRepFace"

    def test_straight_edge_handle_reaches_createinput(self):
        e = _linear_edge()
        _, cf = _install_with_axis_handles({"E": e})
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=3, axis="E"))
        assert cf.last_input.axis is e
        assert out["axis"] == "BRepEdge"

    def test_construction_axis_by_name(self):
        ax = _SimpleNamed("WheelAxis")
        _, cf = _install_with_axis_handles(axes=[ax])
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=5, axis="WheelAxis"))
        assert cf.last_input.axis is ax
        assert out["axis"] == "WheelAxis"         # the datum's NAME, never the raw input token

    def test_ambiguous_construction_axis_name_is_refused(self):
        _, cf = _install_with_axis_handles(
            axes=[_SimpleNamed("Hinge"), _SimpleNamed("Hinge")])
        res = pt.handler(occurrences="Spoke:1", quantity=5, axis="Hinge")
        assert res["isError"] is True
        assert "names 2 construction axes" in res["message"]   # refused, not resolved to the first
        assert cf.last_input is None              # refused before any feature transaction opened

    def test_planar_face_handle_is_refused(self):
        _, cf = _install_with_axis_handles({"F": _planar_face()})
        res = pt.handler(occurrences="Spoke:1", quantity=5, axis="F")
        assert res["isError"] is True and "cylindrical" in res["message"]
        assert cf.last_input is None

    def test_world_keys_still_resolve_to_the_origin_construction_axis(self):
        # regression: the world x/y/z keys must keep working exactly as they did.
        _, cf = _install_with_axis_handles()
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=4, axis="y"))
        assert cf.last_input.axis == "AXIS_Y" and out["axis"] == "y"

    def test_blank_axis_uses_the_kinds_default(self):
        _, cf = _install_with_axis_handles()
        out = _payload(pt.handler(occurrences="Spoke:1", quantity=4, axis=""))
        assert cf.last_input.axis == "AXIS_Z" and out["axis"] == "z"

    def test_a_foreign_construction_axis_is_proxied_via_its_component_not_its_parent(self):
        # A datum's .parent is a BASE FEATURE when the datum is non-parametric in a parametric
        # design; .component always answers the owning component, and the proxy decision hangs on
        # getting that owner right - read from .parent here, the axis would be judged against a
        # base feature and refused as "not placed in the assembly".
        rail = _other_component()
        ax = _SimpleNamed("Hinge")
        ax.component = rail
        ax.parent = types.SimpleNamespace(name="BaseFeature1")
        proxy = _SimpleNamed("Hinge")
        ax.createForAssemblyContext = lambda occ, p=proxy: p
        _, cf = _install_with_axis_handles({"CA": ax})
        _place(pt.app.activeProduct.rootComponent, rail, "Assy:1+Rail:1")
        _payload(pt.handler(occurrences="Spoke:1", quantity=4, axis="CA"))
        assert cf.last_input.axis is proxy      # the PROXY reaches createInput, not the native
