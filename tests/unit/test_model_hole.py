"""Unit tests for ``model_hole`` — the real HoleFeatures building block (not a sketch+extrude-cut).

The Fusion API is mocked; what we pin is the tool's OWN logic: the type dispatch (simple / counterbore /
countersink) and which create*Input builder + value args each uses, placement by sketch points created
on the target face, the extent choice (blind => setDistanceExtent, through => setAllExtent with the
PositiveExtentDirection that is required, live-verified), optional tapping (createThreadInfo +
setToTappedHole), and the guards (unknown type, missing diameters, no face/points, bad extent).

The HoleFeatureInput fake RECORDS the calls so we can assert the exact builder path.
"""

import json

import pytest

from conftest import (load_tool, FakePoint, BRepEdge, Circle3D, Line3D, FakeMatrix3D, MakeComp,
                      make_occurrence)

mh = load_tool("model_hole")

# The REAL _resolve_clearance, captured at import time before any _install() swaps in the test stub -
# the unknown-fastener test drives it directly so the asserted error text is the PRODUCT's, not the
# stub's echo.
_REAL_RESOLVE_CLEARANCE = mh._resolve_clearance


# ── fakes that record the hole-input construction ───────────────────────────



class FakeHoleInput:
    # refuse_placement models the live setters' documented bool return: they answer False when the
    # entities cannot carry the placement, and a False that is not read lets add() run anyway.
    # refuse_extent is the same for setAllExtent - a declined extent never lands, so the input goes
    # into add() without one.
    # refusals maps a setter NAME to the answer it gives instead of True, so a test can distinguish
    # an outright False (a refusal the handler must report) from a None (no answer read at all).
    def __init__(self, kind, args, refuse_placement=False, refuse_extent=False, refusals=None):
        self.refuse_placement = refuse_placement
        self.refuse_extent = refuse_extent
        self.refusals = dict(refusals or {})
        self.kind = kind            # 'simple' | 'counterbore' | 'countersink'
        self.args = args            # the ValueInput strings passed to the builder
        self.placed = None          # ('point', pt) / ('points', [pts]) / ('center', edge) /
                                     # ('on_edge', (edge, offset)) / ('plane_offsets', (...))
        self.extent = None          # ('distance', val) or ('all', direction)
        self.tap = None             # ThreadInfo or None
        self.clearance = None       # ClearanceHoleInfo or None
        self.isModeled = False
        self.isDefaultDirection = True
        self.holeTapType = 0
        self.tipAngle = None
    def _answer(self, setter):
        """The setter's bool. A False means it DECLINED, so the caller skips its side effect; any
        other answer (True, or a None that read as nothing) means the setting landed."""
        return self.refusals.get(setter, True)
    def setPositionBySketchPoint(self, sp):
        answer = self._answer("setPositionBySketchPoint")
        if answer is False:
            return False
        self.placed = ("point", sp); return answer
    def setPositionBySketchPoints(self, coll):
        answer = self._answer("setPositionBySketchPoints")
        if answer is False:
            return False
        self.placed = ("points", list(coll.items)); return answer
    def setPositionAtCenter(self, planar_entity, center_edge):
        assert planar_entity == "FACE"
        assert isinstance(center_edge, BRepEdge)
        if self.refuse_placement:
            return False
        self.placed = ("center", (planar_entity, center_edge)); return True
    def setPositionOnEdge(self, planar_entity, edge, position):
        assert planar_entity == "FACE"
        assert isinstance(edge, BRepEdge)
        if self.refuse_placement:
            return False
        self.placed = ("on_edge", (planar_entity, edge, position)); return True
    def setPositionByPlaneAndOffsets(self, *args):
        assert len(args) in (4, 6)
        assert args[0] == "FACE"
        if self.refuse_placement:
            return False
        self.placed = ("plane_offsets", args); return True
    def setDistanceExtent(self, v):
        answer = self._answer("setDistanceExtent")
        if answer is False:
            return False
        self.extent = ("distance", v); return answer
    def setAllExtent(self, direction):
        if self.refuse_extent:
            return False
        self.extent = ("all", direction); return True
    def setToTappedHole(self, ti):
        answer = self._answer("setToTappedHole")
        if answer is False:
            return False
        self.tap = ti; self.holeTapType = 2; return answer
    def setToClearanceHole(self, chi):
        answer = self._answer("setToClearanceHole")
        if answer is False:
            return False
        self.clearance = chi; return answer


class _Param:
    def __init__(self, value):
        self.value = value


def _deg_to_rad(tip):
    """The fake's tipAngle input is ('V', '<n> deg'); a live ModelParameter reads radians."""
    import math as _m
    return _m.radians(float(str(tip[1]).split()[0]))


class _Axis:
    def __init__(self, x=0.0, y=0.0, z=1.0):
        self.x, self.y, self.z = x, y, z


class _CylGeo:
    """A created face's geometry (Cylinder/Cone duck-type): origin + axis, the drill-line pair the
    per-point verify reads back."""
    def __init__(self, origin):
        self.origin = origin
        self.axis = _Axis()          # holes drill along Z in these tests


class _FaceColl:
    def __init__(self, faces):
        self._f = list(faces)
    @property
    def count(self):
        return len(self._f)
    def item(self, i):
        return self._f[i]


class _UnreadableFaceColl:
    """A faces collection that COUNTS but hands back nothing - a stale face proxy after a rebuild.
    The count is real; every item() raises, so the shared walk yields nothing."""
    def __init__(self, count):
        self.count = count
    def item(self, i):
        raise RuntimeError("face proxy is stale")


class FakeHoleFeature:
    """Mirrors the live partial-failure shape: a point that misses the body creates NO faces, yet
    add() still returns the feature with only a warning ('N Reference Failures' - live-verified)."""
    def __init__(self, inp, miss_indices=(), modeled_readback=True):
        modeled_readback = inp.isModeled if modeled_readback is True else modeled_readback
        self.name = "Hole1"
        self._inp = inp
        self.deleted = False
        # A live HoleFeature reports these back, and each lives on a DIFFERENT object: the tap
        # designation on tappedHoleInfo, the helix flag on the thread feature a tapped hole also
        # creates, and the tip angle as a ModelParameter carrying RADIANS.
        self.tappedHoleInfo = inp.tap
        self.thread = (type("T", (), {"isModeled": modeled_readback})()
                       if inp.tap and modeled_readback is not None else None)
        self.tipAngle = _Param(_deg_to_rad(inp.tipAngle)) if inp.tipAngle else None
        kind = inp.placed[0]
        if kind == "points":
            pts = inp.placed[1]
        elif kind == "point":
            pts = [inp.placed[1]]
        else:
            # center / on_edge / plane_offsets - ONE synthetic hole (these modes place exactly one;
            # the handler verifies the drilled-axis COUNT, not per-point coordinates, for them).
            pts = [_SketchPoint(FakePoint(0.0, 0.0, 0.0))]
        made = [sp for i, sp in enumerate(pts) if i not in miss_indices]
        self.faces = _FaceColl([type("F", (), {"geometry": _CylGeo(sp.geometry)})()
                                for sp in made])
        n_missed = len(pts) - len(made)
        self.errorOrWarningMessage = (
            "No target body!<b>%d Reference Failures</b><br/>No target body!Hole1" % n_missed
            if n_missed else "")
    def deleteMe(self):
        self.deleted = True
        return True


class FakeHoleFeatures:
    def __init__(self):
        self.added = []
        self.miss_indices = ()       # placement-point indices that MISS the body (cut nothing)
        self.refuse_placement = False   # the setPosition* setters answer False
        self.refuse_extent = False      # setAllExtent answers False (the through-all extent declined)
        self.refusals = {}              # setter name -> the answer it gives instead of True
        # True echoes the input; a bool models a flag that read back different; None models one
        # that could not be read at all
        self.modeled_readback = True
    def __bool__(self):
        # a real Fusion collection is FALSY when empty (count==0). The tool must test `is None`,
        # not `not holes`, or an empty-but-valid HoleFeatures collection is wrongly rejected.
        return len(self.added) > 0
    def __len__(self):
        return len(self.added)
    def createSimpleInput(self, dia):
        return FakeHoleInput("simple", {"dia": dia}, self.refuse_placement, self.refuse_extent,
                             self.refusals)
    def createCounterboreInput(self, dia, cbd, cbdepth):
        return FakeHoleInput("counterbore", {"dia": dia, "cb_dia": cbd, "cb_depth": cbdepth},
                             refusals=self.refusals)
    def createCountersinkInput(self, dia, csd, csa):
        return FakeHoleInput("countersink", {"dia": dia, "cs_dia": csd, "cs_angle": csa},
                             refusals=self.refusals)
    def add(self, inp):
        if inp.placed is None or inp.extent is None:
            raise RuntimeError("InternalValidationError : logicalSelection")
        f = FakeHoleFeature(inp, miss_indices=self.miss_indices,
                            modeled_readback=self.modeled_readback)
        self.added.append(f); return f


class _ThreadInfo:
    def __init__(self, internal, ttype, desig, cls):
        self.internal, self.ttype, self.desig, self.cls = internal, ttype, desig, cls
        # the live ThreadInfo's own member names, which the read-back path reads
        self.threadDesignation = desig
        self.threadType = ttype


class FakeThreadDataQuery:
    """The thread library the shared resolver walks. Shaped as it reads live: a designation can sit
    in SEVERAL types - 'M5x0.5' is in both metric profiles here, as 540 of the 1510 live
    designations are - while 'M5x0.8' and the inch call-out sit in exactly one."""
    def __init__(self):
        self.types = ["ANSI Metric M Profile", "ISO Metric profile", "ANSI Unified Screw Threads"]
    @property
    def allThreadTypes(self):
        return tuple(self.types)
    def allSizes(self, t):
        return ("5.0", "6.0")
    def allDesignations(self, t, size):
        if t == "ANSI Unified Screw Threads":
            return ("1/4-20 UNC",) if size.startswith("5") else ()
        if t == "ISO Metric profile":
            return ("M5x0.5",) if size.startswith("5") else ()
        return ("M5x0.8", "M5x0.5") if size.startswith("5") else ("M6x1",)
    def allClasses(self, internal, t, desig):
        return ("6H",) if internal else ("6g",)


class FakeThreadFeatures:
    def __init__(self):
        self.threadDataQuery = FakeThreadDataQuery()
        self.created = []
    def __len__(self):
        # a live ThreadFeatures collection is falsy while empty
        return len(self.created)
    def createThreadInfo(self, internal, ttype, desig, cls):
        ti = _ThreadInfo(internal, ttype, desig, cls); self.created.append(ti); return ti


# sketch / point machinery ----------------------------------------------------

class _SketchPoint:
    def __init__(self, xyz):
        self.geometry = xyz


class _SketchPoints:
    def __init__(self):
        self.items = []
    def add(self, pt):
        sp = _SketchPoint(pt); self.items.append(sp); return sp


class _Sketch:
    def __init__(self, name="Sketch1", to_sketch=None, parent=None):
        self.name = name
        self.sketchPoints = _SketchPoints()
        self.deleted = False
        # modelToSketchSpace maps a point from the sketch's PARENT COMPONENT's model space into the
        # sketch's own 2D space. `to_sketch` is the mapping a live sketch applies; None models one
        # that cannot convert at all. `parent` is what decides WHOSE model space it maps from.
        self._to_sketch = to_sketch
        self.parentComponent = parent

    def modelToSketchSpace(self, p):
        if self._to_sketch is None:
            raise RuntimeError("sketch space unavailable")
        return FakePoint(*self._to_sketch(p.x, p.y, p.z))

    def sketchToModelSpace(self, p):
        # identity: these test sketches sit on the XY plane at the origin
        return p

    def deleteMe(self):
        self.deleted = True
        return True


class _Sketches:
    def __init__(self, owner=None):
        self._byname = {}
        self.created_on = []
        self.to_sketch = None      # the model -> sketch mapping every sketch created here applies
        self.owner = owner         # the component they report as parentComponent
    def add(self, plane):
        # The live Sketches.add rejects a wrong-typed argument with a TypeError (it expects a face/plane
        # entity, NOT a tuple). Model that so an unpacking bug — passing _resolve_face's (entity, error)
        # tuple instead of the entity — fails here as it does live, rather than silently passing.
        if isinstance(plane, tuple):
            raise TypeError("Wrong number or type of arguments for overloaded function 'Sketches_add'.")
        s = _Sketch("HolePts%d" % len(self.created_on), to_sketch=self.to_sketch, parent=self.owner)
        self.created_on.append(plane)
        self._byname[s.name] = s; return s
    def itemByName(self, n):
        return self._byname.get(n)


class _Features:
    def __init__(self):
        self.holeFeatures = FakeHoleFeatures()
        self.threadFeatures = FakeThreadFeatures()


def _bracket(name="Bracket"):
    """A component the placement ladder can identify - same_component compares entityToken, so the
    token is chosen rather than inherited."""
    return MakeComp(name=name, entity_token=f"tok:{name}")


def _placing(comp, transform2, path="Bracket:1"):
    """The occurrence PLACING `comp` with `transform2` - the transform2/assemblyContext pair the
    placement ladder walks, off conftest's shared occurrence."""
    return make_occurrence(path, comp, transform2=transform2)


class _Root:
    def __init__(self):
        # the root OWNS the sketches created in it, which is what makes their model space world
        self.sketches = _Sketches(owner=self)
        self.features = _Features()
        self.name = "Root"
        self.entityToken = "tok:Root"
        # component entityToken -> the occurrences placing it: the census the placement ladder
        # walks when no context occurrence names one instance.
        self.placements = {}

    def allOccurrencesByComponent(self, comp):
        return list(self.placements.get(getattr(comp, "entityToken", None), []))


class _ObjColl:
    """adsk ObjectCollection.create() stand-in (patched onto the tool)."""
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)
    @classmethod
    def create(cls):
        return cls()


class _Design:
    def __init__(self):
        self.rootComponent = _Root()


class _ClearanceInfo:
    def __init__(self, standard, ftype, size, fit):
        self.standard, self.fastenerType, self.size, self.fit = standard, ftype, size, fit


def _install():
    design = _Design()
    mh._common.design = lambda: design
    mh._target_component = lambda d: d.rootComponent
    # face resolver returns (entity, error) — the GeometryHandle.resolve contract. The handler MUST
    # unpack it; passing the whole tuple to sketches.add raises TypeError (see _Sketches.add).
    mh._resolve_face = lambda d, h: ("FACE", None)
    # additive-placement resolvers — default to a circular edge (so placement='center' works
    # out of the box); a test that needs a different shape overrides the seam directly.
    mh._resolve_edge = lambda d, h: (BRepEdge(Circle3D(None)), None)
    # setPositionByPlaneAndOffsets measures from LINEAR edges, so the defaults are straight ones
    mh._resolve_offset_edge_one = lambda d, h: (BRepEdge(Line3D()), None)
    mh._resolve_offset_edge_two = lambda d, h: (BRepEdge(Line3D()), None)
    # ObjectCollection + ValueInput seams. ExtentDirections stays the MEASURED family the tool
    # bound at import (conftest seeds it from live_api_facts) - no sentinel override.
    mh._object_collection = _ObjColl.create
    mh._value = lambda s: ("V", s)
    # clearance seam: real impl validates vs the live catalog + builds a ClearanceHoleInfo.
    # the fake echoes a recognisable info object for known fasteners, else raises like the catalog would.
    def _fake_clear(comp, fastener, fit):
        known = {"Socket Head Cap Screw", "Hex Head Bolt", "Flat Head Machine Screw"}
        # parse "M6 Socket Head Cap Screw" -> size 'M6', type rest
        parts = fastener.split(" ", 1)
        size, ftype = parts[0], (parts[1] if len(parts) > 1 else "")
        if ftype not in known:
            return None, "Unknown fastener type '%s'." % ftype
        return _ClearanceInfo("ANSI Metric M Profile", ftype, size, fit), None
    mh._resolve_clearance = _fake_clear
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_type(self):
        _install()
        res = mh.handler(hole_type="oval", diameter="5 mm", face="h", points=[[1, 2, 0]])
        assert res["isError"] is True and "type" in res["message"].lower()

    def test_missing_diameter(self):
        _install()
        res = mh.handler(hole_type="simple", face="h", points=[[1, 2, 0]])
        assert res["isError"] is True and "diameter" in res["message"].lower()

    def test_no_points(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[])
        assert res["isError"] is True and "point" in res["message"].lower()

    def test_counterbore_requires_its_dims(self):
        _install()
        res = mh.handler(hole_type="counterbore", diameter="5 mm", face="h", points=[[1, 2, 0]])
        assert res["isError"] is True and "counterbore" in res["message"].lower()

    def test_through_and_depth_conflict_or_missing(self):
        _install()
        # blind extent but no depth
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[1, 2, 0]], extent="blind")
        assert res["isError"] is True and "depth" in res["message"].lower()


# ── simple holes ─────────────────────────────────────────────────────────────

class TestSimple:
    def test_simple_blind(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="8 mm", face="h",
                                  points=[[2, 3, 0]], extent="blind", depth="10 mm"))
        hf = d.rootComponent.features.holeFeatures
        assert len(hf.added) == 1
        inp = hf.added[0]._inp
        assert inp.kind == "simple"
        assert inp.extent[0] == "distance"
        assert inp.placed[0] == "point"
        assert out["holes"] == 1 and out["hole_type"] == "simple"

    def test_simple_through_uses_positive_direction(self):
        d = _install()
        _payload(mh.handler(hole_type="simple", diameter="8 mm", face="h",
                            points=[[2, 3, 0]], extent="through"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        # live-verified: THROUGH must use PositiveExtentDirection (Negative fails)
        import adsk.fusion
        assert inp.extent == ("all", adsk.fusion.ExtentDirections.PositiveExtentDirection)

    def test_refused_through_extent_is_an_honest_error(self):
        # setAllExtent answers "did it take"; a False that is not read runs on to add() and reports a
        # hole for an extent the platform declined. The refusal must be NAMED, not read as a garbled
        # validation raise from the add that follows.
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        hf.refuse_extent = True
        res = mh.handler(hole_type="simple", diameter="8 mm", face="h", points=[[2, 3, 0]],
                         extent="through")
        assert res["isError"] is True
        assert "setAllExtent returned false" in res["message"]
        assert hf.added == []                       # no feature was built on the declined extent
        # the placement sketch is rolled back, never orphaned on the face
        assert d.rootComponent.sketches._byname["HolePts0"].deleted is True

    def test_multiple_points_one_feature(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[2, 2, 0], [5, 2, 0], [8, 2, 0]], extent="through"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.placed[0] == "points" and len(inp.placed[1]) == 3
        assert out["points"] == 3


# ── the input setters that answer "did it take" ──────────────────────────────
#
# Every one of these is declared bool by the bindings. A False that is not read lets the handler run
# straight on to add() and report a hole built with a setting the platform declined.

class TestRefusedInputSetters:
    def test_refused_single_point_placement_is_an_honest_error(self):
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        hf.refusals = {"setPositionBySketchPoint": False}
        res = mh.handler(hole_type="simple", diameter="8 mm", face="h", points=[[2, 3, 0]],
                         extent="through")
        assert res["isError"] is True
        assert "setPositionBySketchPoint returned false" in res["message"]
        assert hf.added == []
        assert d.rootComponent.sketches._byname["HolePts0"].deleted is True

    def test_refused_multi_point_placement_names_the_plural_setter(self):
        # the two placement setters are different API calls; the error must name the one that ran
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        hf.refusals = {"setPositionBySketchPoints": False}
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h",
                         points=[[2, 2, 0], [5, 2, 0]], extent="through")
        assert res["isError"] is True
        assert "setPositionBySketchPoints returned false" in res["message"]
        assert hf.added == []

    def test_refused_blind_depth_is_an_honest_error(self):
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        hf.refusals = {"setDistanceExtent": False}
        res = mh.handler(hole_type="simple", diameter="8 mm", face="h", points=[[2, 3, 0]],
                         extent="blind", depth="10 mm")
        assert res["isError"] is True
        assert "setDistanceExtent returned false" in res["message"]
        assert "10 mm" in res["message"]             # the refused value is named
        assert hf.added == []
        assert d.rootComponent.sketches._byname["HolePts0"].deleted is True

    def test_an_extent_answer_that_read_as_nothing_is_not_a_refusal(self):
        # the exact boundary of `is False`. The bindings declare setDistanceExtent -> bool, and
        # gen_api_surface subtracts any name that returns non-bool ANYWHERE, so a live call answers
        # True or False and nothing else; the call is direct rather than wrapped in safe(), so a
        # platform failure raises instead of answering None. A non-bool is therefore unreachable in
        # production and is pinned here only as the boundary - against the real bindings `is False`
        # and `not` cannot differ.
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        hf.refusals = {"setDistanceExtent": None}
        out = _payload(mh.handler(hole_type="simple", diameter="8 mm", face="h",
                                  points=[[2, 3, 0]], extent="blind", depth="10 mm"))
        assert out["holes"] == 1
        assert hf.added[0]._inp.extent == ("distance", ("V", "10 mm"))

    def test_refused_tap_is_an_honest_error(self):
        # a swallowed False here drills an UNTAPPED hole while the payload reports the designation
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        hf.refusals = {"setToTappedHole": False}
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[5, 3, 0]],
                         extent="blind", depth="12 mm", tap="M5x0.8")
        assert res["isError"] is True
        assert "setToTappedHole returned false" in res["message"]
        assert "M5x0.8" in res["message"]
        assert hf.added == []

    def test_refused_clearance_spec_is_an_honest_error(self):
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        hf.refusals = {"setToClearanceHole": False}
        res = mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                         fastener="M6 Socket Head Cap Screw", fit="normal")
        assert res["isError"] is True
        assert "setToClearanceHole returned false" in res["message"]
        assert "M6 Socket Head Cap Screw" in res["message"]
        assert hf.added == []


# ── per-point verification: a point that misses the body cuts NOTHING while add() 'succeeds' ─────
#
# Live-verified: with 2 of 3 points off the target body, holes.add returned the feature with only a
# garbled warning ('No target body!<b>1 Reference Failures</b>...') and the tool reported ok while
# only 1 hole existed. The handler must read the created faces back, match each placement point to a
# drilled axis, roll a partial feature back, and error naming the failed points.

def _install_verified(monkeypatch):
    """_install plus REAL point geometry (Point3D.create -> FakePoint) so the axis-match math runs."""
    import adsk.core
    d = _install()
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        staticmethod(lambda x, y, z: FakePoint(x, y, z)))
    return d


class TestPerPointVerification:
    def test_all_points_drilled_reports_verified_hole_count(self, monkeypatch):
        d = _install_verified(monkeypatch)
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[2, 2, 0], [5, 2, 0], [8, 2, 0]], extent="through"))
        assert out["holes"] == 3                 # the VERIFIED count, not a constant 1
        assert out["holes_verified"] is True

    def test_partial_cut_errors_and_rolls_back(self, monkeypatch):
        d = _install_verified(monkeypatch)
        hf = d.rootComponent.features.holeFeatures
        hf.miss_indices = (1, 2)                 # points 1+2 miss the body - like off-face coords
        res = mh.handler(hole_type="simple", diameter="4 mm", face="h",
                         points=[[0, 0, 0], [100, 0, 0], [120, 0, 0]], extent="through")
        assert res["isError"] is True
        assert "2 of 3" in res["message"]
        assert "[100" in res["message"]          # names the failed points, not just a count
        assert "Reference Failures" in res["message"]      # the platform's reason, markup stripped
        assert "<b>" not in res["message"] and "<br/>" not in res["message"]
        # the partial feature AND its placement sketch were rolled back - nothing half-done remains
        assert hf.added[0].deleted is True
        assert d.rootComponent.sketches.created_on and \
            d.rootComponent.sketches._byname[next(iter(d.rootComponent.sketches._byname))].deleted

    def test_all_points_missing_errors(self, monkeypatch):
        d = _install_verified(monkeypatch)
        hf = d.rootComponent.features.holeFeatures
        hf.miss_indices = (0, 1)
        res = mh.handler(hole_type="simple", diameter="4 mm", face="h",
                         points=[[50, 0, 0], [60, 0, 0]], extent="through")
        assert res["isError"] is True and "2 of 2" in res["message"]

    def test_unreadable_faces_skip_verification_without_false_alarm(self):
        # feature.faces unreadable at all -> NOTHING was checked; the tool must not false-alarm, and
        # must say so (holes_verified=false) instead of claiming a verified count.
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        orig_add = hf.add
        def add_no_faces(inp):
            f = orig_add(inp)
            del f.faces                          # the faces read raises -> verified=False
            return f
        hf.add = add_no_faces
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[2, 2, 0], [5, 2, 0]], extent="through"))
        assert out["holes"] == 2 and out["holes_verified"] is False

    def test_faces_that_count_but_never_read_is_inconclusive_not_a_verified_zero(self):
        # The faces collection COUNTS 3 but every item() raises. The shared walk skips an unreadable
        # item, so the walk yields nothing - which must read as "nothing could be checked", never as
        # "this feature drilled no holes". A verified zero here deletes a hole that landed.
        axes, verified = mh._drill_axes(type("F", (), {"faces": _UnreadableFaceColl(3)})())
        assert axes == [] and verified is False

    def test_unreadable_face_items_never_roll_a_landed_hole_back(self):
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        orig_add = hf.add
        def add_unreadable_faces(inp):
            f = orig_add(inp)
            f.faces = _UnreadableFaceColl(3)     # counts 3, every item() raises
            return f
        hf.add = add_unreadable_faces
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[2, 2, 0], [5, 2, 0], [8, 2, 0]], extent="through"))
        assert out["holes_verified"] is False
        assert hf.added[0].deleted is False       # the landed feature survives the unreadable check


# ── counterbore / countersink ───────────────────────────────────────────────

# -- points_space: world points converted by the sketch's OWN converter --------------------------
# A placement sketch's space is not predictable from the face - it follows the sketch's OWNER. The
# tool hands the world point to that sketch's own modelToSketchSpace rather than doing frame maths,
# and refuses when the sketch did not land in the root component (whose model space IS world).

_ROOT = object()        # "owned by the design root", for _framed


def _identity(x, y, z):
    return (x, y, z)


def _framed(monkeypatch, to_sketch, owner=_ROOT, placements=None):
    """_install plus a placement sketch whose modelToSketchSpace applies `to_sketch`.

    `owner` defaults to the design root - the space that IS world; pass another object to model a
    sketch that landed in a sub-component, and None one whose owner cannot be read. `placements`
    maps such a component to the occurrences that place it, which is what the world lift resolves
    its frame through. adsk.core.Matrix3D stays UNPATCHED: a root-owned sketch must short-circuit
    before any matrix is built, so a lift that reached for one would fail loudly here."""
    import adsk.core
    d = _install()
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        staticmethod(lambda x, y, z: FakePoint(x, y, z)))
    d.rootComponent.sketches.to_sketch = to_sketch
    d.rootComponent.sketches.owner = d.rootComponent if owner is _ROOT else owner
    for comp, occs in (placements or {}).items():
        d.rootComponent.placements[comp.entityToken] = list(occs)
    return d


def _placed(d):
    """(x, y, z) in cm of every point actually added to the placement sketch."""
    sketch = next(iter(d.rootComponent.sketches._byname.values()))
    return [(p.geometry.x, p.geometry.y, p.geometry.z) for p in sketch.sketchPoints.items]


def _drill(**kw):
    kw.setdefault("hole_type", "simple")
    kw.setdefault("diameter", "5 mm")
    kw.setdefault("face", "h")
    kw.setdefault("extent", "through")
    return mh.handler(**kw)


class TestPointsSpaceWorld:
    def test_world_points_go_through_the_sketchs_own_converter(self, monkeypatch):
        # a sketch space rotated about origin (1,2,0): its +X runs along world +Y, its +Y along -X
        d = _framed(monkeypatch, lambda x, y, z: (y - 2, -(x - 1), z))
        out = _payload(_drill(points=[[30, 40, 0]], points_space="world"))
        assert _placed(d) == [(2.0, -2.0, 0.0)]
        assert out["points_space"] == "world"

    def test_a_flipped_axis_space_flips_the_local_coordinate(self, monkeypatch):
        # the measured axis-SIGN case: passing world numbers through raw would mirror the hole
        d = _framed(monkeypatch, lambda x, y, z: (-x, y, -z))
        _drill(points=[[50, 0, 0]], points_space="world")
        assert _placed(d) == [(-5.0, 0.0, 0.0)]

    def test_an_offset_space_shifts_every_point(self, monkeypatch):
        # the measured origin case: the sketch origin is nowhere near the face plane's own origin
        d = _framed(monkeypatch, lambda x, y, z: (x - 3, y - 3.5, z - 2.5))
        _drill(points=[[40, 55, 25]], points_space="world")
        assert _placed(d) == [(1.0, 2.0, 0.0)]

    def test_every_point_of_a_bolt_circle_converts(self, monkeypatch):
        d = _framed(monkeypatch, lambda x, y, z: (x - 1, y - 1, z))
        out = _payload(_drill(points=[[20, 10, 0], [10, 20, 0], [30, 30, 0]], points_space="world"))
        assert _placed(d) == [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (2.0, 2.0, 0.0)]
        assert out["holes"] == 3

    def test_an_off_plane_world_point_is_refused_naming_it_and_the_distance(self, monkeypatch):
        # the converter's z IS the off-plane distance; projecting it would drill somewhere else
        d = _framed(monkeypatch, _identity)
        res = _drill(points=[[0, 0, 5]], points_space="world")
        assert res["isError"] is True
        assert "[0, 0, 5]" in res["message"] and "5 'mm'" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []       # nothing was drilled
        sketch = next(iter(d.rootComponent.sketches._byname.values()))
        assert sketch.deleted is True                                  # no orphaned placement sketch

    def test_the_off_plane_distance_is_reported_in_the_callers_units(self, monkeypatch):
        d = _framed(monkeypatch, _identity)
        res = _drill(points=[[0, 0, 1]], points_space="world", units="cm")
        assert res["isError"] is True and "1 'cm'" in res["message"]

    def test_a_point_inside_the_tolerance_band_still_drills(self, monkeypatch):
        # the band absorbs the rounding a world read publishes - it is not a licence to project
        d = _framed(monkeypatch, _identity)
        _drill(points=[[10, 0, 0.02]], points_space="world")
        assert _placed(d) == [(1.0, 0.0, 0.0)]

    def test_default_points_space_leaves_the_points_untouched(self, monkeypatch):
        # back-compatible: with no points_space the numbers go in exactly as before, even though
        # this sketch WOULD have converted them somewhere else.
        d = _framed(monkeypatch, lambda x, y, z: (y - 2, -(x - 1), z))
        out = _payload(_drill(points=[[30, 40, 0]]))
        assert _placed(d) == [(3.0, 4.0, 0.0)]
        assert out["points_space"] == "sketch"

    def test_a_sketch_that_cannot_convert_refuses_instead_of_guessing(self, monkeypatch):
        d = _framed(monkeypatch, None)          # modelToSketchSpace raises
        res = _drill(points=[[10, 10, 0]], points_space="world")
        assert res["isError"] is True and "points_space='sketch'" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []

    def test_world_space_with_another_placement_is_refused(self):
        # the other placements take no 'points', so accepting it would silently do nothing
        _install()
        res = _drill(placement="center", edge="e", points_space="world")
        assert res["isError"] is True and "sketch_points" in res["message"]

    def test_an_unknown_points_space_is_refused_listing_the_options(self):
        _install()
        res = _drill(points=[[1, 2, 0]], points_space="face")
        assert res["isError"] is True
        assert "sketch" in res["message"] and "world" in res["message"]


class TestPointsSpaceWorldOnAnOffsetComponent:
    """The regression a hand-rolled occurrence transform got wrong. Measured rig: an occurrence at
    +2cm X, box local 0..3cm, drilled on the PROXY top face from the root. The sketch the tool
    creates is ROOT-owned, so its space is WORLD - origin (2,0,1)cm, the world face corner - and
    modelToSketchSpace((4,2,1)) answers (2,2,0). Compensating for the occurrence on top of that
    subtracted the offset twice and drilled at u=0 where world u=40mm was asked for."""

    def _measured_rig(self, monkeypatch):
        return _framed(monkeypatch, lambda x, y, z: (x - 2, y, z - 1))

    def test_a_world_point_over_an_offset_component_lands_where_find_geometry_said(self, monkeypatch):
        d = self._measured_rig(monkeypatch)
        _drill(points=[[40, 20, 10]], points_space="world")
        assert _placed(d) == [(2.0, 2.0, 0.0)]

    def test_sketch_space_points_are_untouched_on_the_same_rig(self, monkeypatch):
        # points_space='sketch' means "already in the sketch's space" - no conversion at all
        d = self._measured_rig(monkeypatch)
        _drill(points=[[40, 20, 0]])
        assert _placed(d) == [(4.0, 2.0, 0.0)]

    def test_a_sketch_outside_the_root_with_no_single_placement_is_refused(self, monkeypatch):
        # modelToSketchSpace maps from the sketch OWNER's model space. Outside the root the point is
        # carried into that component's frame first - but only when ONE placement answers for it.
        # Two placements put the component in two different frames, so a world point names neither.
        sub = _bracket()
        d = _framed(monkeypatch, lambda x, y, z: (x - 2, y, z - 1), owner=sub,
                    placements={sub: [_placing(sub, FakeMatrix3D(0.0), "Bracket:1"),
                                      _placing(sub, FakeMatrix3D(90.0), "Bracket:2")]})
        res = _drill(points=[[40, 20, 10]], points_space="world")
        assert res["isError"] is True
        assert "Bracket" in res["message"] and "no single placement" in res["message"]
        assert "sketch_add_geometry" in res["message"]      # a remedy the caller can perform
        assert d.rootComponent.features.holeFeatures.added == []
        sketch = next(iter(d.rootComponent.sketches._byname.values()))
        assert sketch.deleted is True

    def test_a_sketch_with_no_readable_owner_refuses_too(self, monkeypatch):
        # an unproven space is refused; an unreadable owner is not a yes
        d = _framed(monkeypatch, _identity, owner=None)
        res = _drill(points=[[10, 0, 0]], points_space="world")
        assert res["isError"] is True and "no single placement" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []


class TestWorldPointsIntoANestedComponent:
    """The world path from INSIDE the component that owns the body - the case that had no route at
    all: from the root the hole finds no target body, and the owning component refused the world
    frame outright. The placement sketch lands in that component, so a world point is carried
    through the occurrence's own placement before the sketch's converter reads it.

    The rig's occurrence is ROTATED 90 deg about Z and offset (2, 0, 1) cm, and `to_sketch` is the
    IDENTITY, so the lift is the only thing that moves the point: world (4, 2, 1) cm answers
    (2, -2, 0) local. Straight through it would be (4, 2, 1) - a centimetre off the plane; lifted
    the wrong way round, (0, 4, 2)."""

    def _nested(self, monkeypatch, occs=None, sub=None):
        sub = sub or _bracket()
        occs = occs if occs is not None else [_placing(sub, FakeMatrix3D(90.0, (2.0, 0.0, 1.0)))]
        return _framed(monkeypatch, _identity, owner=sub, placements={sub: occs}), sub

    def _at(self, d):
        """Where the points landed, rounded past the trig's last bit."""
        return [tuple(round(c, 9) for c in p) for p in _placed(d)]

    def test_a_world_point_is_lifted_through_the_rotated_placement(self, monkeypatch):
        d, _sub = self._nested(monkeypatch)
        out = _payload(_drill(points=[[40, 20, 10]], points_space="world"))
        assert self._at(d) == [(2.0, -2.0, 0.0)]
        assert out["points_space"] == "world" and out["holes"] == 1

    def test_the_lift_is_disclosed_by_the_component_it_went_through(self, monkeypatch):
        d, _sub = self._nested(monkeypatch)
        out = _payload(_drill(points=[[40, 20, 10]], points_space="world"))
        assert out["world_lift_component"] == "Bracket"
        assert "'Bracket'" in out["note"]

    def test_a_root_owned_sketch_reports_no_lift(self, monkeypatch):
        # the root's model space IS world, so nothing is carried and nothing is claimed
        _framed(monkeypatch, _identity)
        out = _payload(_drill(points=[[10, 0, 0]], points_space="world"))
        assert "world_lift_component" not in out

    def test_the_off_plane_guard_MEASURES_the_lifted_distance(self, monkeypatch):
        # World z=30 mm sits 2 cm off the plane once the placement's own z (1 cm) is taken out, and
        # 3 cm off if it never is. Both trip the guard, so only the DISTANCE in the message tells
        # the two apart - the substring "off the plane" alone cannot.
        d, _sub = self._nested(monkeypatch)
        res = _drill(points=[[40, 20, 30]], points_space="world")
        assert res["isError"] is True and "off the plane" in res["message"]
        assert "20 'mm'" in res["message"]           # lifted
        assert "30 'mm'" not in res["message"]       # unlifted
        assert d.rootComponent.features.holeFeatures.added == []

    def test_sketch_space_points_are_untouched_inside_the_same_component(self, monkeypatch):
        d, _sub = self._nested(monkeypatch)
        _drill(points=[[40, 20, 0]])
        assert _placed(d) == [(4.0, 2.0, 0.0)]

    def test_the_face_context_picks_WHICH_instance_of_several(self, monkeypatch):
        # Two placements alone refuse, but a face reached THROUGH one of them names the instance in
        # hand, and that instance's frame - not the first occurrence's - carries the point.
        sub = _bracket()
        first = _placing(sub, FakeMatrix3D(0.0, (0.0, 0.0, 0.0)), "Bracket:1")
        second = _placing(sub, FakeMatrix3D(90.0, (2.0, 0.0, 1.0)), "Bracket:2")
        d, _sub = self._nested(monkeypatch, occs=[first, second], sub=sub)
        face = type("F", (), {"assemblyContext": second})()
        mh._resolve_face = lambda _d, _h: (face, None)
        _payload(_drill(points=[[40, 20, 10]], points_space="world"))
        assert self._at(d) == [(2.0, -2.0, 0.0)]        # second's frame, not first's identity

    def test_a_placement_that_will_not_invert_is_refused(self, monkeypatch):
        sub = _bracket()
        d, _sub = self._nested(monkeypatch, occs=[_placing(sub, FakeMatrix3D(90.0, invertible=False))], sub=sub)
        res = _drill(points=[[40, 20, 10]], points_space="world")
        assert res["isError"] is True and "did not invert" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []


class TestSketchSpacePoint:
    """The conversion on its own: (u, v, off_plane) in cm, straight off the sketch's converter."""

    @pytest.fixture(autouse=True)
    def _real_points(self, monkeypatch):
        # the conversion builds a Point3D to hand to the converter, so it needs real coordinates
        import adsk.core
        monkeypatch.setattr(adsk.core.Point3D, "create",
                            staticmethod(lambda x, y, z: FakePoint(x, y, z)))

    def test_it_returns_the_converters_three_coordinates(self):
        s = _Sketch("S", to_sketch=lambda x, y, z: (x - 1, y - 2, z - 3))
        assert mh._sketch_space_point(s, 4.0, 6.0, 3.5) == (3.0, 4.0, 0.5)

    def test_off_plane_is_signed(self):
        s = _Sketch("S", to_sketch=_identity)
        assert mh._sketch_space_point(s, 0.0, 0.0, 0.7)[2] == 0.7
        assert mh._sketch_space_point(s, 0.0, 0.0, -0.7)[2] == -0.7

    def test_a_sketch_that_cannot_convert_returns_nones(self):
        assert mh._sketch_space_point(_Sketch("S"), 1.0, 2.0, 3.0) == (None, None, None)


class TestWorldLift:
    """The lift on its own: the matrix carrying a WORLD point into the space the placement sketch
    converts from, or the refusal when no single placement answers for that component."""

    def _design(self, root):
        return type("D", (), {"rootComponent": root})()

    def _lifted(self, m, x, y, z):
        p = FakePoint(x, y, z)
        p.transformBy(m)
        return (round(p.x, 9), round(p.y, 9), round(p.z, 9))

    def test_a_root_owned_sketch_needs_no_lift_at_all(self):
        root = _Root()
        assert mh._world_lift(self._design(root), _Sketch("S", parent=root), None) == (None, "")

    def test_a_single_placement_answers_with_its_INVERSE(self):
        root, sub = _Root(), _bracket()
        root.placements[sub.entityToken] = [_placing(sub, FakeMatrix3D(90.0, (2.0, 0.0, 1.0)))]
        m, err = mh._world_lift(self._design(root), _Sketch("S", parent=sub), None)
        assert err == ""
        assert self._lifted(m, 4.0, 2.0, 1.0) == (2.0, -2.0, 0.0)

    def test_the_occurrences_own_transform_is_not_inverted_in_place(self):
        # the placement ladder hands back the occurrence's OWN transform2; inverting that rather
        # than a copy would turn a real part's placement inside out
        root, sub = _Root(), _bracket()
        placement = FakeMatrix3D(90.0, (2.0, 0.0, 1.0))
        root.placements[sub.entityToken] = [_placing(sub, placement)]
        mh._world_lift(self._design(root), _Sketch("S", parent=sub), None)
        assert (placement._deg, placement._t) == (90.0, (2.0, 0.0, 1.0))

    def test_several_placements_refuse_naming_the_component_and_a_remedy(self):
        root, sub = _Root(), _bracket()
        root.placements[sub.entityToken] = [_placing(sub, FakeMatrix3D(0.0)),
                                            _placing(sub, FakeMatrix3D(90.0))]
        m, err = mh._world_lift(self._design(root), _Sketch("S", parent=sub), None)
        assert m is None
        assert "'Bracket'" in err and "no single placement" in err
        # both remedies, and both are things the caller can actually do: name the instance on the
        # way in, or cut the hole as a profile
        assert "find_geometry" in err and "'target'" in err
        assert "sketch_create" in err and "model_extrude(operation='cut')" in err

    def test_a_placement_whose_reads_throw_is_not_a_placement(self):
        # ONE occurrence places the component, but it is an unresolved external reference: every
        # read on it throws, transform2 with the rest. The ladder gets no frame, so the lift refuses
        # instead of drilling at a guessed one.
        root, sub = _Root(), _bracket()
        root.placements[sub.entityToken] = [
            make_occurrence("Bracket:1", sub, raises="reference is not resolved",
                            transform2=FakeMatrix3D(90.0))]
        m, err = mh._world_lift(self._design(root), _Sketch("S", parent=sub), None)
        assert m is None and "no single placement" in err

    def test_no_placement_at_all_refuses(self):
        root, sub = _Root(), _bracket()
        m, err = mh._world_lift(self._design(root), _Sketch("S", parent=sub), None)
        assert m is None and "no single placement" in err

    def test_an_unreadable_owner_refuses(self):
        m, err = mh._world_lift(self._design(_Root()), _Sketch("S", parent=None), None)
        assert m is None and "no single placement" in err

    def test_a_matrix_that_refuses_to_invert_refuses(self):
        root, sub = _Root(), _bracket()
        root.placements[sub.entityToken] = [_placing(sub, FakeMatrix3D(90.0, invertible=False))]
        m, err = mh._world_lift(self._design(root), _Sketch("S", parent=sub), None)
        assert m is None and "did not invert" in err


class TestSketchSpacePointTakesTheLift:
    """The conversion applies the lift BEFORE the sketch's own converter, and answers nothing at all
    when the lift will not apply."""

    @pytest.fixture(autouse=True)
    def _liftable_points(self, monkeypatch):
        import adsk.core
        monkeypatch.setattr(adsk.core.Point3D, "create",
                            staticmethod(lambda x, y, z: FakePoint(x, y, z)))

    def test_the_lift_runs_BEFORE_the_sketchs_converter(self):
        # the converter subtracts 1 from x, the lift adds 10 - the order shows in the answer
        s = _Sketch("S", to_sketch=lambda x, y, z: (x - 1, y, z))
        assert mh._sketch_space_point(s, 4.0, 0.0, 0.0,
                                      FakeMatrix3D(0.0, (10.0, 0.0, 0.0))) == (13.0, 0.0, 0.0)

    def test_no_lift_leaves_the_point_where_it_was(self):
        s = _Sketch("S", to_sketch=_identity)
        assert mh._sketch_space_point(s, 4.0, 2.0, 0.0, None) == (4.0, 2.0, 0.0)

    def test_a_transform_that_refuses_voids_the_conversion(self):
        class _Refusing:
            def _apply_point(self, x, y, z):
                raise RuntimeError("transformBy refused")
        s = _Sketch("S", to_sketch=_identity)
        assert mh._sketch_space_point(s, 1.0, 2.0, 3.0, _Refusing()) == (None, None, None)


class TestForeignFaceIsNamedOnAShortfall:
    """A hole built in one component against a 'face' owned by another cuts nothing, and Fusion says
    only 'No target body!'. The refusal names both components - each read, neither inferred - and
    the call that makes the face's own component the build target."""

    def _face_in(self, comp_name):
        body = type("B", (), {"parentComponent": _bracket(comp_name)})()
        return type("F", (), {"body": body, "assemblyContext": None})()

    def _shortfall(self, monkeypatch, face=None):
        d = _install_verified(monkeypatch)
        if face is not None:
            mh._resolve_face = lambda _d, _h: (face, None)
        d.rootComponent.features.holeFeatures.miss_indices = (0,)
        return mh.handler(hole_type="simple", diameter="4 mm", face="h",
                          points=[[10, 10, 0]], extent="through")

    def test_the_shortfall_names_the_faces_component_and_the_activation(self, monkeypatch):
        res = self._shortfall(monkeypatch, self._face_in("Bracket"))
        assert res["isError"] is True
        assert "'Bracket'" in res["message"] and "'Root'" in res["message"]
        assert "design_activate_component" in res["message"]

    def test_a_face_in_the_SAME_component_adds_no_clause(self, monkeypatch):
        res = self._shortfall(monkeypatch, self._face_in("Root"))
        assert res["isError"] is True and "design_activate_component" not in res["message"]

    def test_an_unreadable_face_owner_adds_no_clause(self, monkeypatch):
        # nothing was read, so nothing is claimed about where the face lives
        res = self._shortfall(monkeypatch)
        assert res["isError"] is True and "design_activate_component" not in res["message"]

    def test_an_owner_that_cannot_be_TOLD_APART_adds_no_clause(self, monkeypatch):
        # The owner READS but same_component answers None (no entityToken on it). The clause STATES
        # that the two components differ, so emitting it here would publish a component sentence
        # from a comparison that was never made - and send the caller to activate a component the
        # hole may already have been built in.
        body = type("B", (), {"parentComponent": MakeComp(name="Bracket")})()   # no entityToken
        face = type("F", (), {"body": body, "assemblyContext": None})()
        res = self._shortfall(monkeypatch, face)
        assert res["isError"] is True
        assert "design_activate_component" not in res["message"]
        assert "'Bracket'" not in res["message"]

    def test_the_clause_is_the_ONLY_thing_the_verdict_gates(self, monkeypatch):
        # the positive branch, pinned beside the two silent ones: a PROVEN difference names both
        # components as read, so a verdict flipped to silence is caught here rather than passing as
        # "no clause was due".
        res = self._shortfall(monkeypatch, self._face_in("Bracket"))
        assert "belongs to component 'Bracket'" in res["message"]
        assert "built in 'Root'" in res["message"]



class TestCounterboreCountersink:
    def test_counterbore_passes_three_dims(self):
        d = _install()
        mh.handler(hole_type="counterbore", diameter="6 mm", cbore_diameter="12 mm",
                   cbore_depth="4 mm", face="h", points=[[3, 3, 0]], extent="through")
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.kind == "counterbore"
        assert set(inp.args.keys()) == {"dia", "cb_dia", "cb_depth"}

    def test_countersink_passes_angle(self):
        d = _install()
        mh.handler(hole_type="countersink", diameter="6 mm", csink_diameter="12 mm",
                   csink_angle="90 deg", face="h", points=[[3, 3, 0]], extent="blind", depth="12 mm")
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.kind == "countersink"
        assert "cs_angle" in inp.args


# ── tapped ───────────────────────────────────────────────────────────────────

class TestTapped:
    def test_tapped_builds_thread_info_and_taps(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[5, 3, 0]], extent="blind", depth="12 mm",
                                  tap="M5x0.8"))
        tf = d.rootComponent.features.threadFeatures
        assert len(tf.created) == 1
        ti = tf.created[0]
        assert ti.internal is True and ti.desig == "M5x0.8"
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.tap is ti and inp.holeTapType == 2
        assert out.get("tapped") == "M5x0.8"

    def test_unknown_tap_designation_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h",
                         points=[[5, 3, 0]], extent="blind", depth="12 mm", tap="M99x9")
        assert res["isError"] is True and "M99x9" in res["message"]


# ── fastener-aware clearance holes ───────────────────────────────────────────

class TestClearanceFastener:
    def test_clearance_tags_hole_and_sizes_from_table(self):
        # fastener='M6 Socket Head Cap Screw' -> setToClearanceHole tag + M6 normal clearance diameter.
        d = _install()
        out = _payload(mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]],
                                  extent="through", fastener="M6 Socket Head Cap Screw", fit="normal"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        # the semantic tag is attached (real ClearanceHoleInfo on this version doesn't resize, so we ALSO
        # drive the diameter ourselves from the table)
        assert inp.clearance is not None and inp.clearance.size == "M6"
        # the base diameter passed to createSimpleInput must be the table clearance (M6 normal = 6.6 mm)
        assert inp.kind == "simple"
        assert inp.args["dia"] == ("V", "6.6 mm")
        assert out["fastener"] == "M6 Socket Head Cap Screw" and out["fit"] == "normal"
        assert out["clearance_diameter"] == "6.6 mm"

    def test_fit_changes_diameter(self):
        # close < normal < loose for the same fastener
        d = _install()
        mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                   fastener="M6 Socket Head Cap Screw", fit="close")
        close = d.rootComponent.features.holeFeatures.added[0]._inp.args["dia"][1]
        d2 = _install()
        mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                   fastener="M6 Socket Head Cap Screw", fit="loose")
        loose = d2.rootComponent.features.holeFeatures.added[0]._inp.args["dia"][1]
        # 6.4 close, 7.0 loose
        assert float(close.split()[0]) < float(loose.split()[0])

    def test_fastener_overrides_explicit_diameter(self):
        # giving a fastener means the diameter comes from the table, not the 'diameter' arg
        d = _install()
        mh.handler(hole_type="simple", diameter="99 mm", face="h", points=[[2, 3, 0]],
                   extent="through", fastener="M8 Socket Head Cap Screw", fit="normal")
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.args["dia"] == ("V", "9 mm")     # M8 normal clearance, not 99

    def test_unknown_fastener_size_errors(self):
        _install()
        res = mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                         fastener="M7 Socket Head Cap Screw", fit="normal")
        # M7 isn't in the clearance table -> clean error naming it
        assert res["isError"] is True and "M7" in res["message"]

    def test_unknown_fastener_type_errors(self):
        # Drive the REAL _resolve_clearance (not _install's stub) against a fake live catalog, so the
        # error text/shape asserted here is the product's own - including the available-types listing.
        import adsk.fusion
        _install()
        mh._resolve_clearance = _REAL_RESOLVE_CLEARANCE

        class _Query:
            allStandards = ("ANSI Metric M Profile",)
            def allFastenerTypes(self, std):
                return ("Socket Head Cap Screw", "Hex Head Bolt")
            def allSizes(self, std, ftype):
                return ("M6", "M8")

        adsk.fusion.ClearanceHoleDataQuery = type("CQ", (), {"create": staticmethod(_Query)})
        res = mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                         fastener="M6 Banana Bolt", fit="normal")
        assert res["isError"] is True
        assert "Unknown fastener type 'Banana Bolt'" in res["message"]
        # the real error lists the catalog's available fastener types
        assert "Available: Socket Head Cap Screw, Hex Head Bolt" in res["message"]

    def test_bad_fit_errors(self):
        _install()
        res = mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                         fastener="M6 Socket Head Cap Screw", fit="snug")
        assert res["isError"] is True and "fit" in res["message"].lower()

    def test_set_to_clearance_hole_failure_propagates(self):
        # setToClearanceHole must raise on rejection, not swallow it under safe() and still report the
        # hole as TAGGED for the fastener. The handler has no local try/except around this mutation,
        # matching holes.add's own raise-and-abort style, so the MCP server's top-level handler wraps
        # it into a tool-execution error.
        _install()
        orig = FakeHoleInput.setToClearanceHole

        def _boom(self, chi):
            raise RuntimeError("setToClearanceHole rejected by the API")

        FakeHoleInput.setToClearanceHole = _boom
        try:
            with pytest.raises(RuntimeError, match="setToClearanceHole rejected"):
                mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                           fastener="M6 Socket Head Cap Screw", fit="normal")
        finally:
            FakeHoleInput.setToClearanceHole = orig

    def test_counterbore_with_fastener_keeps_counterbore(self):
        # a counterbore clearance hole: through-diameter from the table, cbore dims still honored
        d = _install()
        mh.handler(hole_type="counterbore", face="h", points=[[2, 3, 0]], extent="through",
                   cbore_diameter="11 mm", cbore_depth="6.5 mm",
                   fastener="M6 Socket Head Cap Screw", fit="normal")
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.kind == "counterbore"
        assert inp.args["dia"] == ("V", "6.6 mm")       # clearance through-bore
        assert inp.args["cb_dia"] == ("V", "11 mm")


# ── additive placement modes: center / on_edge / plane_offsets ──────────────────────────────────
#
# HoleFeatureInput.setPositionAtCenter/setPositionOnEdge/setPositionByPlaneAndOffsets each take the
# planar entity ('face') as their first argument (fusion.py bindings).

class TestPlacementModes:
    def test_unknown_placement_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through", placement="orbit")
        assert res["isError"] is True and "placement" in res["message"].lower()

    def test_center_missing_edge_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through", placement="center")
        assert res["isError"] is True and "edge" in res["message"].lower()

    def test_center_requires_circular_edge(self):
        _install()
        mh._resolve_edge = lambda d, h: (BRepEdge(Line3D()), None)
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through", placement="center",
                         edge="e1")
        assert res["isError"] is True and "circular" in res["message"].lower()

    def test_center_happy_path(self):
        d = _install()
        edge_obj = BRepEdge(Circle3D(None))
        mh._resolve_edge = lambda d_, h: (edge_obj, None)
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                                  placement="center", edge="e1"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.placed == ("center", ("FACE", edge_obj))
        assert out["holes"] == 1 and out["placement"] == "center"

    def test_on_edge_missing_edge_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through", placement="on_edge",
                         edge_position="start")
        assert res["isError"] is True and "edge" in res["message"].lower()

    def test_on_edge_missing_position_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through", placement="on_edge",
                         edge="e1")
        assert res["isError"] is True and "edge_position" in res["message"].lower()

    def test_on_edge_happy_path(self):
        d = _install()
        edge_obj = BRepEdge(Line3D())
        mh._resolve_edge = lambda d_, h: (edge_obj, None)
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                                  placement="on_edge", edge="e1", edge_position="middle"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.placed[0] == "on_edge"
        face_arg, edge_arg, pos_arg = inp.placed[1]
        assert face_arg == "FACE"
        assert edge_arg is edge_obj
        assert pos_arg is mh.adsk.fusion.HoleEdgePositions.EdgeMidPointPosition
        assert out["holes"] == 1

    def test_plane_offsets_missing_point_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="plane_offsets", offset_edge_one="e1", offset_one="4 mm")
        assert res["isError"] is True and "point" in res["message"].lower()

    def test_plane_offsets_missing_offset_one_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="plane_offsets", point=[1, 2, 0])
        assert res["isError"] is True and "offset_edge_one" in res["message"]

    def test_plane_offsets_offset_two_needs_its_edge(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="plane_offsets", point=[1, 2, 0],
                         offset_edge_one="e1", offset_one="4 mm", offset_two="6 mm")
        assert res["isError"] is True
        assert "offset_edge_two" in res["message"] and "offset_two" in res["message"]

    def test_plane_offsets_one_edge_happy_path(self, monkeypatch):
        import adsk.core
        monkeypatch.setattr(adsk.core.Point3D, "create",
                            staticmethod(lambda x, y, z: FakePoint(x, y, z)))
        d = _install()
        e1 = BRepEdge(Line3D())
        mh._resolve_offset_edge_one = lambda d_, h: (e1, None)
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                                  placement="plane_offsets", point=[1, 2, 0],
                                  offset_edge_one="e1", offset_one="4 mm"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.placed[0] == "plane_offsets"
        args = inp.placed[1]
        assert len(args) == 4
        assert args[0] == "FACE"
        assert (args[1].x, args[1].y, args[1].z) == (0.1, 0.2, 0.0)   # mm -> cm, factor 0.1
        assert args[2] is e1 and args[3] == ("V", "4 mm")
        assert out["holes"] == 1

    def test_plane_offsets_two_edges_happy_path(self, monkeypatch):
        import adsk.core
        monkeypatch.setattr(adsk.core.Point3D, "create",
                            staticmethod(lambda x, y, z: FakePoint(x, y, z)))
        d = _install()
        e1, e2 = BRepEdge(Line3D()), BRepEdge(Line3D())
        mh._resolve_offset_edge_one = lambda d_, h: (e1, None)
        mh._resolve_offset_edge_two = lambda d_, h: (e2, None)
        mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                   placement="plane_offsets", point=[1, 2, 0],
                   offset_edge_one="e1", offset_one="4 mm",
                   offset_edge_two="e2", offset_two="6 mm")
        args = d.rootComponent.features.holeFeatures.added[0]._inp.placed[1]
        assert len(args) == 6
        assert args[2] is e1 and args[3] == ("V", "4 mm")
        assert args[4] is e2 and args[5] == ("V", "6 mm")

    def test_non_sketch_placement_skips_the_sketch(self):
        d = _install()
        edge_obj = BRepEdge(Circle3D(None))
        mh._resolve_edge = lambda d_, h: (edge_obj, None)
        _payload(mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                            placement="center", edge="e1"))
        assert d.rootComponent.sketches.created_on == []

    def test_center_miss_rolls_back_and_errors(self):
        d = _install()
        edge_obj = BRepEdge(Circle3D(None))
        mh._resolve_edge = lambda d_, h: (edge_obj, None)
        hf = d.rootComponent.features.holeFeatures
        hf.miss_indices = (0,)
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through", placement="center",
                         edge="e1")
        assert res["isError"] is True
        assert "1 of 1" in res["message"]
        assert hf.added[0].deleted is True


# ── modeled (real helical) thread ────────────────────────────────────────────────────────────────

class TestModeledThread:
    def test_modeled_without_tap_errors(self):
        _install()
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[1, 2, 0]],
                         extent="through", modeled=True)
        assert res["isError"] is True and "modeled" in res["message"].lower()

    def test_modeled_true_sets_real_thread(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[5, 3, 0]],
                                  extent="blind", depth="12 mm", tap="M5x0.8", modeled=True))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.isModeled is True
        assert out["modeled"] is True

    def test_modeled_default_false_is_cosmetic(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[5, 3, 0]],
                                  extent="blind", depth="12 mm", tap="M5x0.8"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.isModeled is False
        assert out["modeled"] is False


# ── drill tip angle ───────────────────────────────────────────────────────────────────────────────

class TestTipAngle:
    def test_tip_angle_blind_sets_value(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[1, 2, 0]],
                                  extent="blind", depth="10 mm", tip_angle="118 deg"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.tipAngle == ("V", "118 deg")
        assert out["tip_angle"] == "118 deg"

    def test_tip_angle_sets_value_on_a_through_hole_too(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[1, 2, 0]],
                                  extent="through", tip_angle="90 deg"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.tipAngle == ("V", "90 deg")
        assert out["tip_angle"] == "90 deg"


class TestPlacementGuardsBite:
    def test_a_circular_offset_edge_is_refused_naming_the_input(self):
        _install()
        mh._resolve_offset_edge_one = lambda d_, h: (BRepEdge(Circle3D(None)), None)
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="plane_offsets", point=[1, 2, 0],
                         offset_edge_one="e1", offset_one="4 mm")
        assert res["isError"] is True
        assert "offset_edge_one" in res["message"] and "circular" in res["message"]

    def test_a_circular_second_offset_edge_is_refused_naming_the_input(self):
        _install()
        mh._resolve_offset_edge_one = lambda d_, h: (BRepEdge(Line3D()), None)
        mh._resolve_offset_edge_two = lambda d_, h: (BRepEdge(Circle3D(None)), None)
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="plane_offsets", point=[1, 2, 0],
                         offset_edge_one="e1", offset_one="4 mm",
                         offset_edge_two="e2", offset_two="6 mm")
        assert res["isError"] is True
        assert "offset_edge_two" in res["message"]

    def test_an_unresolvable_face_is_refused_under_a_non_sketch_placement(self):
        d = _install()
        mh._resolve_face = lambda d_, h: (None, "no planar face for that handle")
        mh._resolve_edge = lambda d_, h: (BRepEdge(Circle3D(None)), None)
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="center", edge="e1")
        assert res["isError"] is True and "planar face" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []


class TestTapReadBackIsHonest:
    def test_a_tap_that_did_not_take_is_error(self):
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        orig = hf.add
        def _untapped(inp):
            f = orig(inp); f.tappedHoleInfo = None; return f
        hf.add = _untapped
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[1, 2, 0]],
                         extent="through", tap="M5x0.8")
        assert res["isError"] is True
        assert "carries no tap" in res["message"] and "Hole1" in res["message"]

    def test_a_tap_that_reads_back_a_different_designation_is_error(self):
        d = _install()
        hf = d.rootComponent.features.holeFeatures
        orig = hf.add
        def _other(inp):
            f = orig(inp); f.tappedHoleInfo.threadDesignation = "M6x1"; return f
        hf.add = _other
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[1, 2, 0]],
                         extent="through", tap="M5x0.8")
        assert res["isError"] is True and "'M6x1'" in res["message"]

    def test_a_modeled_flag_that_reads_back_different_is_error(self):
        d = _install()
        d.rootComponent.features.holeFeatures.modeled_readback = False
        res = mh.handler(hole_type="simple", diameter="5 mm", face="h", points=[[1, 2, 0]],
                         extent="through", tap="M5x0.8", modeled=True)
        assert res["isError"] is True
        assert "modeled" in res["message"] and "cosmetic" in res["message"]

    def test_an_ambiguous_designation_discloses_the_alternatives(self):
        _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[1, 2, 0]], extent="through", tap="M5x0.5"))
        assert out["thread_type"] == "ANSI Metric M Profile"
        assert out["thread_type_alternatives"] == ["ANSI Metric M Profile", "ISO Metric profile"]

    def test_thread_type_picks_the_standard(self):
        _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[1, 2, 0]], extent="through", tap="M5x0.5",
                                  thread_type="ISO Metric profile"))
        assert out["thread_type"] == "ISO Metric profile"

    def test_an_unambiguous_designation_reports_no_alternatives(self):
        _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[1, 2, 0]], extent="through", tap="M5x0.8"))
        assert "thread_type_alternatives" not in out


class TestPlacementRefusalIsRead:
    """A setPosition* setter that answers False placed nothing; letting add() run past it would
    report a hole at a position Fusion never accepted."""

    def _refusing(self):
        d = _install()
        d.rootComponent.features.holeFeatures.refuse_placement = True
        mh._resolve_edge = lambda d_, h: (BRepEdge(Circle3D(None)), None)
        mh._resolve_offset_edge_one = lambda d_, h: (BRepEdge(Line3D()), None)
        return d

    def test_center_refusal_is_an_error(self):
        d = self._refusing()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="center", edge="e1")
        assert res["isError"] is True and "refused to centre" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []

    def test_on_edge_refusal_is_an_error(self):
        d = self._refusing()
        mh._resolve_edge = lambda d_, h: (BRepEdge(Line3D()), None)
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="on_edge", edge="e1", edge_position="middle")
        assert res["isError"] is True and "middle" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []

    def test_plane_offsets_refusal_is_an_error(self):
        d = self._refusing()
        res = mh.handler(hole_type="simple", diameter="5 mm", extent="through",
                         placement="plane_offsets", point=[1, 2, 0],
                         offset_edge_one="e1", offset_one="4 mm")
        assert res["isError"] is True and "refused the plane-and-offsets" in res["message"]
        assert d.rootComponent.features.holeFeatures.added == []


class TestBlindDepthGuard:
    def test_zero_depth_refused_with_the_cause_named(self):
        # depth='0 mm' reached the API bare and came back as a non-diagnostic passthrough - the
        # guard names the cause and the two ways out.
        res = mh.handler(face="F", points=[[0, 0, 0]], diameter="8 mm", extent="blind", depth="0 mm")
        assert res["isError"] is True
        assert "POSITIVE depth" in res["message"] and "through" in res["message"]

    def test_negative_depth_refused(self):
        res = mh.handler(face="F", points=[[0, 0, 0]], diameter="8 mm", extent="blind", depth="-3 mm")
        assert res["isError"] is True and "POSITIVE depth" in res["message"]

    def test_expression_depth_passes_the_guard(self, monkeypatch):
        # A parameter expression is the API's to evaluate - the guard must not float-parse it away.
        # The absent-design error AFTER the guard is the proof it let the expression through.
        monkeypatch.setattr(mh._common, "design", lambda: None)
        res = mh.handler(face="F", points=[[0, 0, 0]], diameter="8 mm", extent="blind",
                         depth="hole_depth")
        assert "POSITIVE depth" not in res.get("message", "")
        assert "No active design" in res["message"]
