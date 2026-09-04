"""Unit tests for sketch_create.py - the plane/face target, the frame and the rename."""

from types import SimpleNamespace
import pytest
from conftest import _NamedCollection, load_tool
import json

sk = load_tool("sketch_create")


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


class _Coll:
    def __init__(self, centres_into=None):
        self._items = []
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
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i]


class _AllCurves:
    """sketch.sketchCurves: a unified count/item view over every sub-collection's curves."""
    def __init__(self, sketch):
        self._s = sketch
    @property
    def count(self):
        return sum(c.count for c in self._s._colls)
    def item(self, i):
        flat = [cv for c in self._s._colls for cv in c._items]
        return flat[i]
    # the handler also calls sketch.sketchCurves.sketchLines etc. via _draw -> use attribute access
    def __getattr__(self, n):
        return getattr(self._s, n)


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


class FakeSketch:
    def __init__(self, name="S"):
        self.name = name
        self.isComputeDeferred = False
        self.isVisible = True
        self.geometricConstraints = _GeomConstraints()
        self.sketchPoints = _Coll()
        self.sketchLines = _Coll()
        self.sketchCircles = _Coll(centres_into=self.sketchPoints)
        self.sketchArcs = _Coll(centres_into=self.sketchPoints)
        self.sketchEllipses = _Coll()
        self.sketchFittedSplines = _Coll()
        self.sketchControlPointSplines = _Coll()
        self.sketchConicCurves = _Coll()
        self.sketchEllipticalArcs = _Coll()
        self._colls = [self.sketchLines, self.sketchCircles, self.sketchArcs,
                       self.sketchEllipses, self.sketchFittedSplines,
                       self.sketchControlPointSplines, self.sketchConicCurves,
                       self.sketchEllipticalArcs, self.sketchPoints]
        self.profiles = type("P", (), {"count": 1})()
        self.slot_call = None
        self.center_point_arc_slot_args = None
        self.three_point_arc_slot_args = None
        self.overall_slot_args = None
        self.center_point_slot_args = None
    @property
    def sketchCurves(self):
        return _AllCurves(self)

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


class FakeSketches:
    def __init__(self, sk_):
        # sk_=None builds an EMPTY collection - the design where a blank sketch_name has no most
        # recent sketch to fall back on, which is the only way to reach "No sketch to draw on".
        self._l = [] if sk_ is None else [sk_]
        self.added = None          # the plane entity sketches.add() was handed
    @property
    def count(self):
        return len(self._l)
    def item(self, i):
        return self._l[i]
    def itemByName(self, n):
        return next((s for s in self._l if s.name == n), None)
    def add(self, planar):
        self.added = planar
        return self._l[0]


def _datum(name):
    """One construction plane: its name, its owning component, and the assembly-context proxy Fusion
    mints for a plane native to ANOTHER component - tagged with its occurrence, so a test can tell
    the proxy from the native and see which instance carried it."""
    cp = SimpleNamespace(name=name, component=None)
    cp.createForAssemblyContext = lambda occ: SimpleNamespace(
        name=name, component=cp.component, native=cp, context=occ)
    return cp


class FakeDesignDraw:
    """The design the sketch tools build into: each component's sketches collection, the origin
    construction planes PlaneRef's xy/xz/yz alias reads off the ACTIVE one, and - for the design-wide
    name lookup - the sub-components carrying their own datums plus the occurrences placing them.

    planes: [datum] on the root. subs: [(component name, [datum names], [occurrence fullPathNames])].
    active: the component name to treat as active (default: the root).
    """

    def __init__(self, sketch, planes=(), subs=(), active=None):
        root = self._component("Root", sketch, planes)
        comps, occs = [root], []
        for comp_name, datum_names, paths in subs:
            sub = self._component(comp_name, sketch, [_datum(n) for n in datum_names])
            for cp in sub.constructionPlanes:
                cp.component = sub
            comps.append(sub)
            occs += [SimpleNamespace(fullPathName=p, name=p, component=sub) for p in paths]
        self.rootComponent = root
        self.allComponents = _NamedCollection(comps)
        self.activeComponent = self.allComponents.itemByName(active) or root
        root.allOccurrences = occs
        root.allOccurrencesByComponent = lambda c: _NamedCollection(
            [o for o in occs if o.component is c])

    @staticmethod
    def _component(name, sketch, planes):
        # entityToken, because _common.same_component compares on it and answers None without one -
        # and the assembly-context lift REFUSES an owner it cannot tell from the root rather than
        # hand back a component-local datum Fusion would reject.
        return SimpleNamespace(name=name, entityToken=f"TOKEN:{name}",
                               sketches=FakeSketches(sketch),
                               constructionPlanes=_NamedCollection(list(planes)),
                               xYConstructionPlane=_datum("XY"),
                               xZConstructionPlane=_datum("XZ"),
                               yZConstructionPlane=_datum("YZ"))


def _install_draw(monkeypatch, sketch, **design_kw):
    """Wire a fake sketch into the tool's design seams for one test; monkeypatch undoes it after.
    Extra keywords (planes / subs / active) shape the design PlaneRef resolves against. Returns it.

    Both seams are patched: the handler's own `_common` AND the one `_inputs` resolves its kinds
    through, so a PlaneRef lookup sees the same design the handler does."""
    import adsk.fusion, adsk.core
    design = FakeDesignDraw(sketch, **design_kw)
    monkeypatch.setattr(sk, "app", SimpleNamespace(activeProduct=design))
    monkeypatch.setattr(sk._common, "app", sk.app)
    monkeypatch.setattr(sk._inputs._common, "app", sk.app)
    monkeypatch.setattr(adsk.fusion.Design, "cast",
                        lambda x: x if isinstance(x, FakeDesignDraw) else None)
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        lambda x, y, z: type("P", (), {"x": x, "y": y, "z": z})())
    # a Vector3D whose components ARE its magnitude along each axis - the elliptical arc's major/
    # minor axis vectors carry their radius as the vector's magnitude.
    monkeypatch.setattr(adsk.core.Vector3D, "create",
                        lambda x, y, z: type("V", (), {"x": x, "y": y, "z": z})())

    class _OC:
        def __init__(self): self._i = []
        def add(self, x): self._i.append(x)
        @property
        def count(self): return len(self._i)
    monkeypatch.setattr(adsk.core.ObjectCollection, "create", _OC)
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal", lambda v: ("real", v))
    monkeypatch.setattr(adsk.core.ValueInput, "createByString", lambda s: ("string", s))
    return design


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class _ScopedDesign:
    """Two (or more) components, each holding its OWN sketch collection.

    Both components hold a sketch of the SAME name on purpose - that is the only fixture in which
    the identity filter actually runs. Two DIFFERENT names would resolve design-wide with no scope
    involved and the test would pass against unscoped code."""

    def __init__(self, pairs):
        comps = [SimpleNamespace(name=name, sketches=_NamedCollection(list(sketches)),
                                 constructionPlanes=_NamedCollection([]),
                                 xYConstructionPlane=_datum("XY"),
                                 xZConstructionPlane=_datum("XZ"),
                                 yZConstructionPlane=_datum("YZ"))
                 for name, sketches in pairs]
        self.rootComponent = comps[0]
        self.allComponents = _NamedCollection(comps)
        self.activeComponent = comps[0]
        self.rootComponent.allOccurrences = []

    def component(self, name):
        return self.allComponents.itemByName(name)


def _install_scoped(monkeypatch, pairs):
    """Point sketch_create at a multi-component design. Both design seams are patched (the handler's
    own _common and the one _inputs resolves through), inside monkeypatch so each undoes itself."""
    import adsk.fusion, adsk.core
    design = _ScopedDesign(pairs)
    monkeypatch.setattr(sk, "app", SimpleNamespace(activeProduct=design))
    monkeypatch.setattr(sk._common, "app", sk.app)
    monkeypatch.setattr(sk._inputs._common, "app", sk.app)
    monkeypatch.setattr(adsk.fusion.Design, "cast",
                        lambda x: x if isinstance(x, _ScopedDesign) else None)
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


class TestOnFacePlaneNameMisuse:

    def test_construction_plane_name_points_at_plane_param(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s, planes=[_datum("MidPlane")])
        monkeypatch.setattr(sk._ON_FACE, "resolve",
                            lambda raw: (None, "stale handle - re-run find_geometry"))
        res = sk.handler(on_face="MidPlane")
        assert res["isError"] is True
        assert "plane='MidPlane'" in res["message"]
        assert "construction PLANE name" in res["message"]

    def test_an_origin_alias_passed_as_on_face_points_at_the_plane_param_too(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        monkeypatch.setattr(sk._ON_FACE, "resolve",
                            lambda raw: (None, "stale handle - re-run find_geometry"))
        res = sk.handler(on_face="xy")
        assert res["isError"] is True and "plane='xy'" in res["message"]

    def test_genuinely_bad_handle_keeps_the_resolver_error(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        monkeypatch.setattr(sk._ON_FACE, "resolve",
                            lambda raw: (None, "stale handle - re-run find_geometry"))
        res = sk.handler(on_face="NOTAPLANE")
        assert res["isError"] is True
        assert "stale handle" in res["message"]


class TestCreateSketchPlaneRef:

    """sketch_create hands 'plane' to _inputs.PlaneRef, so the sketch lands on the entity the SHARED
    kind resolved: an origin alias off the ACTIVE component, a construction-plane name resolved
    DESIGN-WIDE (a sub-component's datum proxied into the occurrence that places it), the qualified
    '<occurrence>:<plane>' form - and a name several components share is REFUSED, never resolved to
    whichever component happened to answer first."""

    def test_xy_alias_lands_on_the_active_components_origin_plane(self, monkeypatch):
        s = FakeSketch(); d = _install_draw(monkeypatch, s)
        out = _payload(sk.handler(plane="xy"))
        assert d.rootComponent.sketches.added is d.rootComponent.xYConstructionPlane
        assert out["on"] == "plane 'xy'"

    @pytest.mark.parametrize("given, attr", [
        ("xy", "xYConstructionPlane"), ("xz", "xZConstructionPlane"), ("yz", "yZConstructionPlane"),
        ("top", "xYConstructionPlane"), ("front", "xZConstructionPlane"),
        ("right", "yZConstructionPlane"), ("  XY Plane ", "xYConstructionPlane"),
        ("xzplane", "xZConstructionPlane"), ("YZPlane", "yZConstructionPlane")])
    def test_every_alias_spelling_the_tool_takes_still_lands_on_its_origin_plane(
            self, monkeypatch, given, attr):
        # the '<alias> plane' spellings are the kind's now, not a local fold - the tool must still
        # take every one of them.
        s = FakeSketch(); d = _install_draw(monkeypatch, s)
        _payload(sk.handler(plane=given))
        assert d.rootComponent.sketches.added is getattr(d.rootComponent, attr)

    def test_a_datum_named_mid_plane_still_resolves_by_name(self, monkeypatch):
        # the suffix handling must not swallow a construction plane whose own name ends in 'plane'
        cp = _datum("Mid plane")
        s = FakeSketch(); d = _install_draw(monkeypatch, s, planes=[cp])
        _payload(sk.handler(plane="Mid plane"))
        assert d.rootComponent.sketches.added is cp

    def test_an_empty_plane_defaults_to_xy(self, monkeypatch):
        s = FakeSketch(); d = _install_draw(monkeypatch, s)
        out = _payload(sk.handler(plane=""))
        assert d.rootComponent.sketches.added is d.rootComponent.xYConstructionPlane
        assert out["on"] == "plane 'xy'"      # the label names the default that actually resolved

    def test_a_root_construction_plane_resolves_by_name(self, monkeypatch):
        cp = _datum("Datum1")
        s = FakeSketch(); d = _install_draw(monkeypatch, s, planes=[cp])
        _payload(sk.handler(plane="Datum1"))
        assert d.rootComponent.sketches.added is cp

    def test_a_sub_component_datum_resolves_as_a_proxy_into_its_occurrence(self, monkeypatch):
        # the capability the active-component-only lookup had no reach for: a datum created inside a
        # sub-component. Its NATIVE form is component-local, so it must arrive PROXIED into the one
        # occurrence that places its owner.
        s = FakeSketch()
        d = _install_draw(monkeypatch, s, subs=[("Tower", ["Datum_A"], ["Tower:1"])])
        _payload(sk.handler(plane="Datum_A"))
        landed = d.rootComponent.sketches.added
        native = d.allComponents.itemByName("Tower").constructionPlanes.itemByName("Datum_A")
        assert landed.native is native
        assert landed.context.fullPathName == "Tower:1"

    def test_a_datum_name_two_components_share_is_refused_with_its_candidates(self, monkeypatch):
        s = FakeSketch()
        d = _install_draw(monkeypatch, s,
                          subs=[("A", ["Mid"], ["A:1"]), ("B", ["Mid"], ["B:1"])])
        res = sk.handler(plane="Mid")
        assert res["isError"] is True and "ambiguous" in res["message"]
        assert "A:1:Mid" in res["message"] and "B:1:Mid" in res["message"]
        assert d.rootComponent.sketches.added is None      # refused BEFORE the sketch was created

    def test_the_qualified_occurrence_form_picks_one_instance(self, monkeypatch):
        s = FakeSketch()
        d = _install_draw(monkeypatch, s,
                          subs=[("A", ["Mid"], ["A:1"]), ("B", ["Mid"], ["B:1"])])
        _payload(sk.handler(plane="B:1:Mid"))
        landed = d.rootComponent.sketches.added
        assert landed.native is d.allComponents.itemByName("B").constructionPlanes.itemByName("Mid")
        assert landed.context.fullPathName == "B:1"

    def test_the_active_components_own_datum_name_shadows_another_components(self, monkeypatch):
        # Fusion default-names the FIRST datum of every component 'Plane1', so the active
        # component's own must win rather than the design-wide vote refusing the commonest name.
        s = FakeSketch()
        d = _install_draw(monkeypatch, s, active="A",
                          subs=[("A", ["Plane1"], ["A:1"]), ("B", ["Plane1"], ["B:1"])])
        _payload(sk.handler(plane="Plane1"))
        native = d.allComponents.itemByName("A").constructionPlanes.itemByName("Plane1")
        assert d.activeComponent.sketches.added is native   # native: already the build context

    def test_an_unresolvable_plane_names_the_vocabulary_that_would_work(self, monkeypatch):
        s = FakeSketch(); d = _install_draw(monkeypatch, s)
        res = sk.handler(plane="nonsense")
        assert res["isError"] is True
        assert "not an origin alias" in res["message"] and "find_geometry" in res["message"]
        assert d.rootComponent.sketches.added is None

    def test_the_plane_schema_is_the_kinds_own(self, monkeypatch):
        # the contract an agent reads comes from PlaneRef, so it cannot drift from what resolves
        prop = sk.tool.to_dict()["inputSchema"]["properties"]["plane"]
        assert prop["description"] == sk._PLANE.schema()["description"]
        assert "top/front/right" in prop["description"]     # the aliases the tool has always taken
        assert "handle" in prop["description"]              # plus the handle form the kind adds


class TestCreateFrameParity:

    """sketch_create and sketch_get publish the SAME frame block from the one helper, so a caller
    places geometry against the numbers it later verifies against."""

    @staticmethod
    def _framed(z_cm=1.5):
        s = FakeSketch()
        s.origin = SimpleNamespace(x=0.0, y=0.0, z=z_cm)
        s.xDirection = SimpleNamespace(x=1.0, y=0.0, z=0.0)
        s.yDirection = SimpleNamespace(x=0.0, y=0.0, z=-1.0)
        # Root-owned: local IS world. A sketch whose component is instanced several times gets the
        # component-local frame instead (test__sketch_detail's TestFrameSpace). The token is the
        # design root's own - two wrappers of one root component measured share one entityToken,
        # and that is what same_component compares.
        root = SimpleNamespace(name="Root", entityToken="TOKEN:Root")
        root.parentDesign = SimpleNamespace(rootComponent=root)
        s.parentComponent = root
        return s

    def test_created_sketch_publishes_origin_axes_and_normal(self, monkeypatch):
        # origin 1.5 cm up world Z reads 15 mm; the normal is x cross y = (1,0,0) x (0,0,-1).
        _install_draw(monkeypatch, self._framed())
        out = _payload(sk.handler(plane="xz"))
        assert out["frame"] == {"origin_mm": [0.0, 0.0, 15.0],
                                "x_world": [1.0, 0.0, 0.0],
                                "y_world": [0.0, 0.0, -1.0],
                                "normal": [0.0, 1.0, 0.0],
                                "space": "world"}

    def test_frame_comes_from_the_shared_helper(self):
        # one definition, imported - a second local copy is how create and read start disagreeing.
        detail = load_tool("_sketch_detail")
        assert sk.sketch_world_frame is detail.sketch_world_frame

    def test_a_sketch_with_no_readable_plane_reports_frame_null(self, monkeypatch):
        _install_draw(monkeypatch, FakeSketch())      # no origin/xDirection/yDirection
        out = _payload(sk.handler(plane="xy"))
        assert out["frame"] is None
        assert out["created"] is True                 # the create still succeeded


class TestCreateFrameNote:

    def test_note_states_the_xz_origin_plane_axis_mapping(self, monkeypatch):
        # the create result teaches the origin-plane local-axis -> world mapping so an agent
        # need not discover it (the xz plane maps local +Y to world -Z, live-proven).
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.handler(plane="xz"))
        assert "local +Y maps to world -Z" in out["note"]
        # The sentence points at the frame's own +Y axis rather than naming a key: the axis key is
        # y_world or y_local depending on frame.space, so a hard-coded name would send the caller
        # to a key that is absent on a component-local frame.
        assert "the frame's own +Y axis" in out["note"]
        assert "frame.y_world" not in out["note"]


class TestCreateRenameDisclosure:

    def test_a_swallowed_rename_is_disclosed_beside_the_actual_name(self, monkeypatch):
        # the platform can accept the name assignment and keep its existing name: the payload's
        # sketch_name is the read-back, and the declined rename is DISCLOSED - never swallowed.
        class StubbornSketch(FakeSketch):
            @property
            def name(self):
                return "Sketch1"

            @name.setter
            def name(self, v):
                pass

        s = StubbornSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.handler(plane="xy", name="Pocket Outline"))
        assert out["sketch_name"] == "Sketch1"
        assert "Pocket Outline" in out["rename_warning"]
        assert "did not take" in out["rename_warning"]

    def test_a_clean_rename_carries_no_warning(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.handler(plane="xy", name="Pocket Outline"))
        assert out["sketch_name"] == "Pocket Outline"
        assert "rename_warning" not in out
