"""Unit tests for ``model_revolve.py`` — revolve a sketch profile about an axis.

Pinned (no live Fusion): the unknown-operation/zero-angle guards, sketch + profile resolution
(named vs most recent; profile_index bounds), axis resolution (x/y/z origin axis, an edge or
axis-defining FACE handle, OR 'line:<index>'),
the degrees → radians conversion handed to setAngleExtent, and the operation-name mapping.
"""

import json
import math
import types

import pytest

from conftest import (BRepEdge, BRepFace, Cylinder, FakePoint, FakeVector3D, Line3D, Plane,
                      _NamedCollection, _SimpleNamed, assert_no_active_design, entity_proxy,
                      load_tool)

rv = load_tool("model_revolve")


class FakeProfiles:
    def __init__(self, n):
        self._n = n
    @property
    def count(self):
        return self._n
    def item(self, i):
        return ("profile", i)


class FakeLines:
    def __init__(self, n):
        self._n = n
    @property
    def count(self):
        return self._n
    def item(self, i):
        return ("line", i)


class FakeSketch:
    def __init__(self, name, profile_count=1, line_count=2):
        self.name = name
        self.profiles = FakeProfiles(profile_count)
        self.sketchCurves = type("C", (), {"sketchLines": FakeLines(line_count)})()


class FakeSketches:
    def __init__(self, sketches):
        self._items = list(sketches)
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i]
    def itemByName(self, name):
        for s in self._items:
            if s.name == name:
                return s
        return None


class FakeRevInput:
    def __init__(self, profile, axis, operation):
        self.profile = profile
        self.axis = axis
        self.operation = operation
        self.angle_extent = None
        self.two_sides = None
    def setAngleExtent(self, isSymmetric, angle):
        self.angle_extent = (isSymmetric, angle)
        return True
    # Real API name (confirmed live). Only the real name is provided - a fake that also accepted
    # a wrong name like `setTwoSidesExtent` would let the test pass against a method that doesn't
    # exist on the real RevolveFeatureInput; here the wrong name raises AttributeError.
    def setTwoSideAngleExtent(self, a, b):
        self.two_sides = (a, b)
        return True


class FakeRevFeature:
    name = "Revolve1"
    class bodies:
        count = 1
        @staticmethod
        def item(i):
            return type("B", (), {"name": "Body1"})()


class FakeRevFeatures:
    def __init__(self):
        self.last_input = None
        self.add_calls = 0            # counted separately: a feature can be BUILT on a collection
    def createInput(self, profile, axis, operation):      # whose createInput was never called
        self.last_input = FakeRevInput(profile, axis, operation)
        return self.last_input
    def add(self, inp):
        self.add_calls += 1
        return FakeRevFeature()


class FakeComp:
    def __init__(self, sketches, rf):
        self.name = "Comp"
        self.sketches = FakeSketches(sketches)
        self.features = type("F", (), {"revolveFeatures": rf})()
        self.xConstructionAxis = ("axis", "x")
        self.yConstructionAxis = ("axis", "y")
        self.zConstructionAxis = ("axis", "z")


class FakeDesign:
    def __init__(self, comp):
        self.activeComponent = comp
        self.rootComponent = comp


def _install(sketches):
    rf = FakeRevFeatures()
    comp = FakeComp(sketches, rf)
    design = FakeDesign(comp)
    rv.app = type("A", (), {"activeProduct": design})()
    rv._common.app = rv.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    return rf


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_unknown_operation(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="S", operation="weld")
        assert res["isError"] is True and "Unknown operation" in res["message"]

    def test_zero_angle(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="S", angle_deg=0)
        assert res["isError"] is True and "non-zero 'angle_deg'" in res["message"]

    def test_no_sketch_named(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="Nope")
        assert res["isError"] is True and "No sketch named 'Nope'" in res["message"]

    def test_profile_out_of_range(self):
        _install([FakeSketch("S", profile_count=1)])
        res = rv.handler(sketch_name="S", profile_index=5)
        assert res["isError"] is True and "out of range" in res["message"]

    def test_bad_axis(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="S", axis="q")
        assert res["isError"] is True and "Could not resolve axis" in res["message"]


class TestRevolve:
    def test_full_revolve_converts_deg_to_radians(self):
        rf = _install([FakeSketch("Cup")])
        out = _payload(rv.handler(sketch_name="Cup", axis="z", angle_deg=360))
        assert out["revolved"] is True and out["axis"] == "z-axis"
        sym, ang = rf.last_input.angle_extent
        assert ang[0] == "real" and abs(ang[1] - 2 * math.pi) < 1e-9
        assert sym is False

    def test_partial_angle(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", angle_deg=90))
        _, ang = rf.last_input.angle_extent
        assert abs(ang[1] - math.pi / 2) < 1e-9

    def test_axis_x_resolves(self):
        rf = _install([FakeSketch("S")])
        out = _payload(rv.handler(sketch_name="S", axis="x"))
        assert rf.last_input.axis == ("axis", "x") and out["axis"] == "x-axis"

    def test_axis_sketch_line(self):
        rf = _install([FakeSketch("S", line_count=3)])
        out = _payload(rv.handler(sketch_name="S", axis="line:1"))
        assert rf.last_input.axis == ("line", 1) and "line:1" in out["axis"]

    def test_operation_cut_mapping(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", operation="cut"))
        assert rf.last_input.operation == "CutFeatureOperation"

    def test_symmetric_flag(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", symmetric=True))
        sym, _ = rf.last_input.angle_extent
        assert sym is True

    def test_two_sided_asymmetric(self):
        import math
        rf = _install([FakeSketch("S")])
        out = _payload(rv.handler(sketch_name="S", angle_deg=90, second_angle_deg=30))
        # setTwoSideAngleExtent used (not setAngleExtent), with both angles in radians
        assert rf.last_input.two_sides is not None
        assert rf.last_input.angle_extent is None
        a, b = rf.last_input.two_sides
        assert abs(a[1] - math.radians(90)) < 1e-9 and abs(b[1] - math.radians(30)) < 1e-9
        assert out["second_angle_deg"] == 30

    def test_fake_rejects_the_nonexistent_method_name(self):
        # The real API method is setTwoSideAngleExtent; the fake must not expose the nonexistent
        # setTwoSidesExtent, so a handler calling it AttributeErrors here instead of silently passing.
        assert not hasattr(FakeRevInput("p", "a", "o"), "setTwoSidesExtent")

    def test_second_angle_ignored_when_symmetric(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", angle_deg=90, second_angle_deg=30, symmetric=True))
        assert rf.last_input.two_sides is None     # symmetric wins
        assert rf.last_input.angle_extent is not None


# ── honesty: failed/absent mutation must surface as isError, never a false ok ─
# (the paths test_model_mirror.py / test_model_shell.py treat as mandatory)

class TestHonesty:
    def test_add_returning_none_is_error(self):
        rf = _install([FakeSketch("S")])
        rf.add = lambda inp: None
        res = rv.handler(sketch_name="S")
        assert res["isError"] is True and "no feature" in res["message"].lower()

    def test_add_raising_surfaces_as_error(self):
        rf = _install([FakeSketch("S")])

        def _boom(inp):
            raise RuntimeError("axis intersects the profile")

        rf.add = _boom
        res = rv.handler(sketch_name="S")
        assert res["isError"] is True
        assert "Revolve failed" in res["message"] and "axis intersects the profile" in res["message"]
        # the hint may not claim the axis and profile must be COPLANAR: an axis outside the profile's
        # plane is projected onto it, so that is never the cause the caller should chase
        assert "coplanar" not in res["message"].lower()

    def test_no_active_design(self):
        _install([FakeSketch("S")])
        assert_no_active_design(rv, rv.handler, sketch_name="S")


# -- the axis from GEOMETRY: the entity that DEFINES the axis, not a world key ------------------
#
# RevolveFeatures.createInput's axis takes "a sketch line, construction axis, linear edge or a face
# that defines an axis (cylinder, cone, torus, etc.)" (the installed API's own doc), so the input is
# an AxisRef with face_entity=True. A face mapped to a direction VECTOR and then to a world axis key
# keeps the direction but drops the axis POSITION: a cylinder face at x=30 revolves about the world
# axis through the ORIGIN, which is wrong geometry reported as success.

@pytest.fixture
def wire(monkeypatch):
    """Factory: fake design + handle resolution, wired into the tool by monkeypatch (undone after
    the test). One `app` patch covers both design seams - the handler's `_common` and the `_common`
    that `_inputs` binds are the same module object - and the entity classes AxisRef isinstance-checks
    must be REAL classes, since a bare Mock attribute is not a type.

    `axes` gives the ACTIVE component construction axes (the by-name axis path); `extra_components`
    puts further components in the design-wide walk (a sketch in a sub-component); `placements` maps
    a component NAME to the occurrences that place it (what the cross-component proxy resolves
    through). Returns the ACTIVE component's revolveFeatures."""
    import adsk.core
    import adsk.fusion

    def _wire(sketches, tokens=None, axes=(), extra_components=(), placements=None):
        rf = FakeRevFeatures()
        root = FakeComp(sketches, rf)
        root.constructionAxes = _NamedCollection(list(axes))
        root.allOccurrencesByComponent = lambda c, m=dict(placements or {}): _NamedCollection(
            list(m.get(safe_name(c), [])))
        design = FakeDesign(root)
        design.allComponents = _NamedCollection([root, *extra_components])
        design.findEntityByToken = lambda t, m=dict(tokens or {}): ([m[t]] if t in m else [])
        app = type("A", (), {"activeProduct": design})()
        monkeypatch.setattr(rv, "app", app)
        monkeypatch.setattr(rv._common, "app", app)
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, FakeDesign) else None)
        monkeypatch.setattr(adsk.fusion, "BRepEdge", BRepEdge)
        monkeypatch.setattr(adsk.fusion, "BRepFace", BRepFace)
        monkeypatch.setattr(adsk.fusion, "SketchLine", type("SL", (), {}))
        monkeypatch.setattr(adsk.core.ValueInput, "createByReal", staticmethod(lambda v: ("real", v)))
        return rf
    return _wire


def safe_name(comp):
    return getattr(comp, "name", None)


def _cylindrical_face(owner=None):
    """A cylinder face whose axis points along +z - the shape that hides a dropped axis POSITION: its
    DIRECTION is a world key, so only the entity reaching createInput proves the position survived.
    `owner` makes it NATIVE to that component (read through the face's body)."""
    body = types.SimpleNamespace(parentComponent=owner) if owner is not None else None
    return BRepFace(Cylinder(FakeVector3D(0, 0, 1)), body=body)


def _occurrence(path):
    return types.SimpleNamespace(fullPathName=path)


class TestAxisFromGeometry:
    def test_cylindrical_face_handle_reaches_createinput_as_the_face(self, wire):
        f = _cylindrical_face()
        rf = wire([FakeSketch("Ring")], tokens={"CYL": f})
        out = _payload(rv.handler(sketch_name="Ring", axis="CYL"))
        # the FACE itself, never the component's origin construction axis - the entity is what
        # carries the axis position an off-origin revolve turns about
        assert rf.last_input.axis is f
        assert out["axis"] == "BRepFace"

    def test_planar_face_handle_is_refused(self, wire):
        rf = wire([FakeSketch("S")], tokens={"F": BRepFace(Plane(normal=FakeVector3D(0, 0, 1)))})
        res = rv.handler(sketch_name="S", axis="F")
        assert res["isError"] is True and "cylindrical" in res["message"]
        assert rf.last_input is None          # refused before any feature transaction opened

    def test_straight_edge_handle_still_reaches_createinput(self, wire):
        e = BRepEdge(curve=Line3D(start=FakePoint(0, 0, 0), end=FakePoint(1, 0, 0)))
        rf = wire([FakeSketch("S")], tokens={"E": e})
        out = _payload(rv.handler(sketch_name="S", axis="E"))
        # no BRepEdge carries a name, so the label falls back to what the entity IS
        assert rf.last_input.axis is e and out["axis"] == "BRepEdge"

    def test_construction_axis_by_name_publishes_the_datums_name(self, wire):
        ax = _SimpleNamed("WheelAxis")
        rf = wire([FakeSketch("S")], axes=[ax])
        out = _payload(rv.handler(sketch_name="S", axis="WheelAxis"))
        assert rf.last_input.axis is ax
        assert out["axis"] == "WheelAxis"       # the datum's own NAME, not the raw input or a type

    def test_ambiguous_construction_axis_name_is_refused(self, wire):
        rf = wire([FakeSketch("S")], axes=[_SimpleNamed("Hinge"), _SimpleNamed("Hinge")])
        res = rv.handler(sketch_name="S", axis="Hinge")
        assert res["isError"] is True and "names 2 construction axes" in res["message"]
        assert rf.last_input is None            # refused, never resolved to the first hit

    def test_world_key_still_resolves_to_the_origin_construction_axis(self, wire):
        rf = wire([FakeSketch("S")], tokens={"CYL": _cylindrical_face()})
        out = _payload(rv.handler(sketch_name="S", axis="y"))
        assert rf.last_input.axis == ("axis", "y") and out["axis"] == "y-axis"

    def test_line_index_still_resolves_in_the_profiles_own_sketch(self, wire):
        rf = wire([FakeSketch("S", line_count=3)], tokens={"CYL": _cylindrical_face()})
        out = _payload(rv.handler(sketch_name="S", axis="line:2"))
        assert rf.last_input.axis == ("line", 2) and out["axis"] == "sketch line:2"


# -- an axis owned by ANOTHER component: proxied into its occurrence, or refused ----------------
#
# A face NATIVE to another component (assemblyContext None) kills the revolve call outright; the
# same face proxied into the occurrence that places it (createForAssemblyContext) is accepted and
# turns about the correct off-origin axis. A component placed several times is refused instead:
# each instance holds that axis somewhere else, and a revolve's read-back cannot tell them apart.

class TestCrossComponentAxis:
    def test_native_face_from_another_component_is_proxied_into_its_occurrence(self, wire):
        other = FakeComp([], FakeRevFeatures())
        other.name = "PartB"
        f = _cylindrical_face(owner=other)
        proxied = _cylindrical_face()
        proxied.assemblyContext = _occurrence("PartB:1")
        f.createForAssemblyContext = lambda occ, p=proxied: p
        rf = wire([FakeSketch("Ring")], tokens={"CYL": f},
                  placements={"PartB": [_occurrence("PartB:1")]})
        out = _payload(rv.handler(sketch_name="Ring", axis="CYL"))
        assert rf.last_input.axis is proxied     # the PROXY, never the native cross-component face
        assert out["axis"] == "BRepFace"

    def test_component_placed_twice_is_refused_with_both_paths(self, wire):
        other = FakeComp([], FakeRevFeatures())
        other.name = "PartB"
        f = _cylindrical_face(owner=other)
        f.createForAssemblyContext = lambda occ: _cylindrical_face()
        rf = wire([FakeSketch("Ring")], tokens={"CYL": f},
                  placements={"PartB": [_occurrence("PartB:1"), _occurrence("PartB:2")]})
        res = rv.handler(sketch_name="Ring", axis="CYL")
        assert res["isError"] is True
        assert "placed 2 times" in res["message"]
        assert "PartB:1" in res["message"] and "PartB:2" in res["message"]
        assert rf.last_input is None             # refused before any feature transaction opened

    def test_proxy_that_cannot_be_built_is_refused_not_passed_native(self, wire):
        other = FakeComp([], FakeRevFeatures())
        other.name = "PartB"
        f = _cylindrical_face(owner=other)
        f.createForAssemblyContext = lambda occ: None      # the context could not be built
        rf = wire([FakeSketch("Ring")], tokens={"CYL": f},
                  placements={"PartB": [_occurrence("PartB:1")]})
        res = rv.handler(sketch_name="Ring", axis="CYL")
        assert res["isError"] is True and "could not be brought into" in res["message"]
        assert rf.last_input is None and rf.add_calls == 0

    def test_unplaced_component_is_refused(self, wire):
        other = FakeComp([], FakeRevFeatures())
        other.name = "PartB"
        rf = wire([FakeSketch("Ring")], tokens={"CYL": _cylindrical_face(owner=other)})
        res = rv.handler(sketch_name="Ring", axis="CYL")
        assert res["isError"] is True and "not placed in the assembly" in res["message"]
        assert rf.last_input is None

    def test_face_owned_by_the_revolves_own_component_passes_untouched(self, wire):
        # the owner is a DIFFERENT Python object for the same component (component wrappers are
        # never identity-stable), so an `owner is comp` test would send this face down the
        # cross-component path and refuse a perfectly legal axis
        rf = wire([FakeSketch("Ring")], tokens={"CYL": None})
        host = rv.app.activeProduct.rootComponent
        f = _cylindrical_face(owner=entity_proxy(host))
        rv.app.activeProduct.findEntityByToken = lambda t, e=f: ([e] if t == "CYL" else [])
        out = _payload(rv.handler(sketch_name="Ring", axis="CYL"))
        assert rf.last_input.axis is f and out["axis"] == "BRepFace"


# -- the feature is built on the sketch's OWNING component, not the active one ------------------

class TestHostComponent:
    def test_feature_and_axis_come_from_the_sketchs_owning_component(self, wire):
        owner_rf = FakeRevFeatures()
        sketch = FakeSketch("Rim")
        owner = FakeComp([sketch], owner_rf)
        owner.name = "Hub"
        for key in ("x", "y", "z"):
            setattr(owner, f"{key}ConstructionAxis", ("axis", key, "Hub"))
        sketch.parentComponent = owner
        active_rf = wire([], extra_components=[owner])
        _payload(rv.handler(sketch_name="Rim", axis="z"))
        # a profile handed to ANOTHER component's features collection raises 'InternalValidationError
        # : bSet', so both the feature and its origin axis must come from the sketch's owner
        assert active_rf.last_input is None and active_rf.add_calls == 0
        assert owner_rf.last_input is not None and owner_rf.add_calls == 1
        assert owner_rf.last_input.axis == ("axis", "z", "Hub")
