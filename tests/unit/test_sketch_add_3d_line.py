"""Unit tests for sketch_add_3d_line.py - the off-plane end point and the construction flag."""

from types import SimpleNamespace
import pytest
from conftest import (MakeComp, Profile, Sketch, SketchCurves, _NamedCollection, install,
                      load_tool, make_design)
import json

sk = load_tool("sketch_add_3d_line")


class _Curve:
    def __init__(self):
        self.isConstruction = False
        # polyline/closed_path share these so the chain is continuous + closeable.
        self.startSketchPoint = type("SP", (), {})()
        self.endSketchPoint = type("SP", (), {})()


class _SketchPoint:
    """A SketchPoint the sketch owns - the 'point:<index>' address space sketchPoints indexes."""
    def __init__(self):
        self.isConstruction = False


class _Coll(_NamedCollection):
    """One per-kind curve collection: the shared count/item protocol plus the add* factories the
    handler calls, each recording its arguments in `last`."""
    def __init__(self, centres_into=None):
        super().__init__()
        self.last = None
        # A circle's/arc's CENTRE is a SketchPoint the sketch owns: the constructor lands it in
        # sketchPoints, so every later point's index sits one further along per curve drawn.
        self._centres_into = centres_into
    def _make(self, *a):
        c = _Curve(); self._items.append(c); self.last = a
        if self._centres_into is not None:
            c.centerSketchPoint = self._centres_into._land_point()
        return c
    def _land_point(self):
        p = _SketchPoint()
        self._items.append(p)
        return p
    def _land(self, n=1, construction=False):
        """n curves landing in this collection WITHOUT a factory call on it - what a Sketch-level
        constructor (addCenterToCenterSlot, the slot constructors) does. 'last' stays untouched, so
        a test can still tell a factory call on this collection from a landing in it. Returns the
        curves it created, in creation order."""
        made = []
        for _ in range(n):
            c = _Curve()
            c.isConstruction = construction
            self._items.append(c)
            made.append(c)
        return made

    def _make_many(self, n, *a):
        """n curves from ONE factory call - the shape a rectangle constructor has: the handler must
        publish the collection's own delta, not one-per-call."""
        made = [self._make(*a) for _ in range(n)]
        return made[0]
    # the various add* methods the handler calls
    def addByTwoPoints(self, a, b): return self._make("line", a, b)
    def addTwoPointRectangle(self, a, b): return self._make_many(4, "rect", a, b)
    def addCenterPointRectangle(self, c, corner): return self._make_many(4, "crect", c, corner)
    def addByCenterRadius(self, c, r): return self._make("circle", c, r)
    def addByCenterStartSweep(self, c, s, sw): return self._make("arc", c, s, sw)
    # a scribed polygon lands one SketchLine per side, all from the one factory call
    def addScribedPolygon(self, c, n, a, r, b): return self._make_many(int(n), "poly", c, n, r)
    def addByAngle(self, c, major, minor, start, sweep):
        return self._make("elliptical_arc", c, major, minor, start, sweep)
    def add(self, *a): return self._make("add", *a)


class _AllCurves(SketchCurves):
    """The shared SketchCurves with each per-kind sub-collection swapped for a recording _Coll, and
    count/item a unified view over all of them plus the sketch's own points."""
    def __init__(self, points):
        super().__init__()
        self.sketchLines = _Coll()
        self.sketchCircles = _Coll(centres_into=points)
        self.sketchArcs = _Coll(centres_into=points)
        self.sketchEllipses = _Coll()
        self.sketchFittedSplines = _Coll()
        self.sketchControlPointSplines = _Coll()
        self.sketchConicCurves = _Coll()
        self.sketchEllipticalArcs = _Coll()
        self._colls = [self.sketchLines, self.sketchCircles, self.sketchArcs, self.sketchEllipses,
                       self.sketchFittedSplines, self.sketchControlPointSplines,
                       self.sketchConicCurves, self.sketchEllipticalArcs, points]

    @property
    def _flat(self):
        return [cv for c in self._colls for cv in c._items]

    @property
    def count(self):
        return len(self._flat)

    def item(self, i):
        return self._flat[i]

    def __iter__(self):
        return iter(self._flat)


class _GeomConstraints:
    """sketch.geometricConstraints - records addCoincident calls; raise_on_add simulates the API
    rejecting the call so the honesty-contract test can prove the raise propagates."""
    def __init__(self):
        self.raise_on_add = False
        self.added = []
    def addCoincident(self, a, b):
        if self.raise_on_add:
            raise RuntimeError("addCoincident rejected by the API")
        self.added.append((a, b))
        return object()


class FakeSketch(Sketch):
    """The shared Sketch fake with a recording draw surface: the per-kind curve collections a
    factory call lands in, and the slot constructors that live on the Sketch itself. Each per-kind
    collection is reachable both here and through sketchCurves - one object, two paths."""
    def __init__(self, name="S"):
        points = _Coll()
        curves = _AllCurves(points)
        super().__init__(name=name, curves=curves, profiles=[Profile()])
        self.sketchPoints = points
        self.isVisible = True
        self.geometricConstraints = _GeomConstraints()
        self.sketchLines = curves.sketchLines
        self.sketchCircles = curves.sketchCircles
        self.sketchArcs = curves.sketchArcs
        self.sketchEllipses = curves.sketchEllipses
        self.sketchFittedSplines = curves.sketchFittedSplines
        self.sketchControlPointSplines = curves.sketchControlPointSplines
        self.sketchConicCurves = curves.sketchConicCurves
        self.sketchEllipticalArcs = curves.sketchEllipticalArcs
        self.slot_call = None
        self.center_point_arc_slot_args = None
        self.three_point_arc_slot_args = None
        self.overall_slot_args = None
        self.center_point_slot_args = None

    # addCenterToCenterSlot is on the Sketch, NOT sketchLines. Capturing it here (and not on _Coll)
    # makes a call to curves.sketchLines.addCenterToCenterSlot AttributeError instead of passing.
    # Measured landing: 2 solid SketchLines + 1 CONSTRUCTION SketchLine (the centre-to-centre line)
    # + 2 SketchArc end caps = 5 sketch curves. The return is a BaseVector: len() answers 5, [0]
    # indexes, iteration yields the two ARCS FIRST and then the three lines; item() and objectType
    # do not answer on it. A plain Python list carries exactly that surface, so the fake returns
    # one - and the collection deltas, not the return, are what verify the draw.
    def addCenterToCenterSlot(self, p1, p2, width):
        self.slot_call = {"p1": p1, "p2": p2, "width": width}
        sides = self.sketchLines._land(2)
        centre_line = self.sketchLines._land(1, construction=True)
        caps = self.sketchArcs._land(2)
        return caps + sides + centre_line

    # Both arc-slot constructors are Sketch methods too, and each builds the slot out of five
    # SketchArcs (two end caps plus the inner/centre/outer arcs) - so the fake lands them in
    # sketchArcs, the collection the draw's before/after count is verified against. Captured as raw
    # *args because the ARITY is the contract: a bool in the radius or angle slot is not an overload.
    def _land_arc_slot(self):
        for _ in range(5):
            self.sketchArcs._make("arc_slot")
        return _Curve()

    def addCenterPointArcSlot(self, *args):
        self.center_point_arc_slot_args = args
        return self._land_arc_slot()

    def addThreePointArcSlot(self, *args):
        self.three_point_arc_slot_args = args
        return self._land_arc_slot()

    # addOverallSlot / addCenterPointSlot land SketchLines and two SketchArc end caps, so 'line' is
    # the collection their draw is counted against. Their return is a BaseVector with len()/[i] and
    # no .count. Measured line counts: three lines, and a fourth ONLY once the length/angle tail is
    # passed - the bool-only 4-argument form still lands three.
    def _land_linear_slot(self, args):
        for _ in range(4 if len(args) >= 5 else 3):
            self.sketchLines._make("slot_side")
        for _ in range(2):
            self.sketchArcs._make("slot_cap")
        return ["arc-slot-entity"]

    def addOverallSlot(self, *args):
        self.overall_slot_args = args
        return self._land_linear_slot(args)

    def addCenterPointSlot(self, *args):
        self.center_point_slot_args = args
        return self._land_linear_slot(args)


class FakeSketches(_NamedCollection):
    """A component's sketches: the shared collection protocol plus the add() that records the plane
    entity it was handed."""
    def __init__(self, sk_):
        # sk_=None builds an EMPTY collection - the design where a blank sketch_name has no most
        # recent sketch to fall back on, which is the only way to reach "No sketch to draw on".
        super().__init__([] if sk_ is None else [sk_])
        self.added = None          # the plane entity sketches.add() was handed

    def add(self, planar):
        self.added = planar
        return self._items[0]


def _datum(name):
    """One construction plane: its name, its owning component, and the assembly-context proxy Fusion
    mints for a plane native to ANOTHER component - tagged with its occurrence, so a test can tell
    the proxy from the native and see which instance carried it."""
    cp = SimpleNamespace(name=name, component=None)
    cp.createForAssemblyContext = lambda occ: SimpleNamespace(
        name=name, component=cp.component, native=cp, context=occ)
    return cp


def _draw_component(name, sketch, planes):
    # entityToken, because _common.same_component compares on it and answers None without one -
    # and the assembly-context lift REFUSES an owner it cannot tell from the root rather than
    # hand back a component-local datum Fusion would reject.
    comp = MakeComp(name=name, entity_token=f"TOKEN:{name}")
    comp.sketches = FakeSketches(sketch)
    comp.constructionPlanes = _NamedCollection(list(planes))
    comp.xYConstructionPlane = _datum("XY")
    comp.xZConstructionPlane = _datum("XZ")
    comp.yZConstructionPlane = _datum("YZ")
    return comp


def _draw_design(sketch, planes=(), subs=(), active=None):
    """The design the sketch tools build into: each component's sketches collection, the origin
    construction planes PlaneRef's xy/xz/yz alias reads off the ACTIVE one, and - for the design-wide
    name lookup - the sub-components carrying their own datums plus the occurrences placing them.

    planes: [datum] on the root. subs: [(component name, [datum names], [occurrence fullPathNames])].
    active: the component name to treat as active (default: the root).
    """
    root = _draw_component("Root", sketch, planes)
    comps, occs = [root], []
    for comp_name, datum_names, paths in subs:
        sub = _draw_component(comp_name, sketch, [_datum(n) for n in datum_names])
        for cp in sub.constructionPlanes:
            cp.component = sub
        comps.append(sub)
        occs += [SimpleNamespace(fullPathName=p, name=p, component=sub) for p in paths]
    design = make_design(comp=root, all_components=comps)
    design.activeComponent = design.allComponents.itemByName(active) or root
    root.allOccurrences = occs
    root.allOccurrencesByComponent = lambda c: _NamedCollection(
        [o for o in occs if o.component is c])
    return design


def _install_draw(monkeypatch, sketch, **design_kw):
    """Wire a fake sketch into the tool's design seams for one test. Extra keywords
    (planes / subs / active) shape the design PlaneRef resolves against. Returns it."""
    import adsk.core
    design = install(sk, _draw_design(sketch, **design_kw))
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        lambda x, y, z: type("P", (), {"x": x, "y": y, "z": z})())
    # a Vector3D whose components ARE its magnitude along each axis - the elliptical arc's major/
    # minor axis vectors carry their radius as the vector's magnitude.
    monkeypatch.setattr(adsk.core.Vector3D, "create",
                        lambda x, y, z: type("V", (), {"x": x, "y": y, "z": z})())
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal", lambda v: ("real", v))
    monkeypatch.setattr(adsk.core.ValueInput, "createByString", lambda s: ("string", s))
    return design


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


def _scoped_design(pairs):
    """Two (or more) components, each holding its OWN sketch collection.

    Both components hold a sketch of the SAME name on purpose - that is the only fixture in which
    the identity filter actually runs. Two DIFFERENT names would resolve design-wide with no scope
    involved and the test would pass against unscoped code."""
    comps = []
    for name, sketches in pairs:
        comp = MakeComp(name=name, sketches=list(sketches))
        comp.constructionPlanes = _NamedCollection([])
        comp.xYConstructionPlane = _datum("XY")
        comp.xZConstructionPlane = _datum("XZ")
        comp.yZConstructionPlane = _datum("YZ")
        comps.append(comp)
    return make_design(comp=comps[0], all_components=comps)


def _install_scoped(monkeypatch, pairs):
    """Point sketch_add_3d_line at a multi-component design."""
    import adsk.core
    design = install(sk, _scoped_design(pairs))
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        lambda x, y, z: type("P", (), {"x": x, "y": y, "z": z})())
    return design


@pytest.fixture
def shared_name(monkeypatch):
    """'Sketch1' in BOTH components. The two sketches start with DIFFERENT curve counts (Alpha has
    one line already, Beta none), so a call that reached the wrong one is visible in the counts and
    not merely in a name that both sketches share."""
    alpha, beta = FakeSketch("Sketch1"), FakeSketch("Sketch1")
    alpha.sketchLines._land(1)
    design = _install_scoped(monkeypatch, [("Alpha", [alpha]), ("Beta", [beta])])
    return design, alpha, beta


class TestDraw3dLine:

    def _line_sketch(self):
        s = FakeSketch("S3D")

        def _add(p1, p2):
            c = _Curve()
            c.startSketchPoint = type("SP", (), {"geometry": p1})()
            c.endSketchPoint = type("SP", (), {"geometry": p2})()
            return c
        s.sketchLines.addByTwoPoints = _add
        s.originPoint = object()
        return s

    def test_end_off_plane_detected_and_scaled(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.handler(x1=0, y1=0, z1=0, x2=0, y2=0, z2=10, units="mm"))
        # z 10mm -> end z back in mm = 10; flagged off-plane
        assert out["end"]["z"] == 10.0
        assert out["end_is_off_plane"] is True

    def test_on_plane_end_not_flagged(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.handler(x1=0, y1=0, z1=0, x2=10, y2=0, z2=0, units="mm"))
        assert out["end_is_off_plane"] is False
        assert out["end"]["x"] == 10.0

    def test_missing_end_point_errors(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        res = sk.handler(x1=0, y1=0, z1=0, x2=5, y2=5)   # z2 missing
        assert res["isError"] is True and "x2, y2, z2" in res["message"]

    def test_is_construction_marks_the_line_and_reports_it(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.handler(x2=1, y2=1, z2=1, is_construction=True))
        assert out["is_construction"] is True             # read BACK off the line, not echoed

    def test_default_is_not_construction(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.handler(x2=1, y2=1, z2=1))
        assert out["is_construction"] is False

    def test_a_stuck_construction_flag_is_published_as_the_line_reads_it(self, monkeypatch):
        # the flag assignment is accepted and changes nothing: the payload is a READ of the line
        # that landed, so it publishes false. Echoing the request would claim construction geometry
        # over a line that is still a profile edge.
        s = self._line_sketch(); _install_draw(monkeypatch, s)

        class _Stuck:
            def __init__(self, p1, p2):
                self.startSketchPoint = type("SP", (), {"geometry": p1})()
                self.endSketchPoint = type("SP", (), {"geometry": p2})()

            @property
            def isConstruction(self):
                return False

            @isConstruction.setter
            def isConstruction(self, v):
                pass                                  # accepted and ignored

        s.sketchLines.addByTwoPoints = lambda p1, p2: _Stuck(p1, p2)
        res = sk.handler(x2=1, y2=1, z2=1, is_construction=True)
        assert res["isError"] is False                # the line WAS drawn - not an error
        out = _payload(res)
        assert out["is_construction"] is False        # as it READS, not as it was asked for

    def test_is_construction_set_failure_is_reported(self, monkeypatch):
        # the API rejecting the flag must surface (naming the drawn-but-unmarked state), not no-op
        s = self._line_sketch(); _install_draw(monkeypatch, s)

        class _Locked:
            def __init__(self, p1, p2):
                self.startSketchPoint = type("SP", (), {"geometry": p1})()
                self.endSketchPoint = type("SP", (), {"geometry": p2})()
            @property
            def isConstruction(self):
                return False
            @isConstruction.setter
            def isConstruction(self, v):
                raise RuntimeError("isConstruction locked")
        s.sketchLines.addByTwoPoints = lambda p1, p2: _Locked(p1, p2)
        res = sk.handler(x2=1, y2=1, z2=1, is_construction=True)
        assert res["isError"] is True and "could not be marked construction" in res["message"]


class TestSharedSketchNameRefused:

    _REFUSAL = "2 sketches are named 'S' ('S' in Root, 'S' in Frame)"

    def test_draw_3d_line_refuses_with_its_owners(self, monkeypatch):
        _install_draw(monkeypatch, FakeSketch("S"))
        monkeypatch.setattr(sk._common, "find_or_recent_sketch",
                            lambda d, n, remedy=None: (None, n, self._REFUSAL))
        res = sk.handler(sketch_name="S", x2=1, y2=1, z2=1)
        assert res["isError"] is True
        assert res["message"] == self._REFUSAL and "No sketch named" not in res["message"]


class TestSketchNameReporting:

    def test_draw_3d_line_blank_name_with_no_sketch_says_nothing_to_draw_on(self, monkeypatch):
        _install_draw(monkeypatch, None)
        res = sk.handler(sketch_name="", x2=1, y2=1, z2=1)
        assert res["isError"] is True
        assert "No sketch to draw on" in res["message"] and "No sketch named" not in res["message"]


class TestDrawComponentScope:

    def test_3d_line_scope_reaches_the_named_components_sketch(self, shared_name):
        _d, alpha, beta = shared_name
        before_alpha = alpha.sketchLines.count
        out = _payload(sk.handler(sketch_name="Sketch1", component="Beta",
                                               x2=1, y2=1, z2=1))
        assert out["sketch_name"] == "Sketch1"
        assert beta.sketchLines.count == 1 and alpha.sketchLines.count == before_alpha

    def test_3d_line_unscoped_shared_name_refuses_and_names_the_scope_input(self, shared_name):
        _d, alpha, beta = shared_name
        res = sk.handler(sketch_name="Sketch1", x2=1, y2=1, z2=1)
        assert res["isError"] is True
        assert "2 sketches are named 'Sketch1'" in res["message"] and "'component'" in res["message"]
        assert beta.sketchLines.count == 0
