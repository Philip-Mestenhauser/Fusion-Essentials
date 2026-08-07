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

from conftest import load_tool, FakePoint, BRepEdge, Circle3D, Line3D

mh = load_tool("model_hole")

# The REAL _resolve_clearance, captured at import time before any _install() swaps in the test stub -
# the unknown-fastener test drives it directly so the asserted error text is the PRODUCT's, not the
# stub's echo.
_REAL_RESOLVE_CLEARANCE = mh._resolve_clearance


# ── fakes that record the hole-input construction ───────────────────────────

class _ExtentDir:
    PositiveExtentDirection = "POS"
    NegativeExtentDirection = "NEG"


class FakeHoleInput:
    # refuse_placement models the live setters' documented bool return: they answer False when the
    # entities cannot carry the placement, and a False that is not read lets add() run anyway.
    def __init__(self, kind, args, refuse_placement=False):
        self.refuse_placement = refuse_placement
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
    def setPositionBySketchPoint(self, sp):
        self.placed = ("point", sp); return True
    def setPositionBySketchPoints(self, coll):
        self.placed = ("points", list(coll.items)); return True
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
        self.extent = ("distance", v); return True
    def setAllExtent(self, direction):
        self.extent = ("all", direction); return True
    def setToTappedHole(self, ti):
        self.tap = ti; self.holeTapType = 2; return True
    def setToClearanceHole(self, chi):
        self.clearance = chi; return True


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
        return FakeHoleInput("simple", {"dia": dia}, self.refuse_placement)
    def createCounterboreInput(self, dia, cbd, cbdepth):
        return FakeHoleInput("counterbore", {"dia": dia, "cb_dia": cbd, "cb_depth": cbdepth})
    def createCountersinkInput(self, dia, csd, csa):
        return FakeHoleInput("countersink", {"dia": dia, "cs_dia": csd, "cs_angle": csa})
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
    def __init__(self, name="Sketch1"):
        self.name = name
        self.sketchPoints = _SketchPoints()
        self.deleted = False

    def sketchToModelSpace(self, p):
        # identity: these test sketches sit on the XY plane at the origin
        return p

    def deleteMe(self):
        self.deleted = True
        return True


class _Sketches:
    def __init__(self):
        self._byname = {}
        self.created_on = []
    def add(self, plane):
        # The live Sketches.add rejects a wrong-typed argument with a TypeError (it expects a face/plane
        # entity, NOT a tuple). Model that so an unpacking bug — passing _resolve_face's (entity, error)
        # tuple instead of the entity — fails here as it does live, rather than silently passing.
        if isinstance(plane, tuple):
            raise TypeError("Wrong number or type of arguments for overloaded function 'Sketches_add'.")
        s = _Sketch("HolePts%d" % len(self.created_on)); self.created_on.append(plane)
        self._byname[s.name] = s; return s
    def itemByName(self, n):
        return self._byname.get(n)


class _Features:
    def __init__(self):
        self.holeFeatures = FakeHoleFeatures()
        self.threadFeatures = FakeThreadFeatures()


class _Root:
    def __init__(self):
        self.sketches = _Sketches()
        self.features = _Features()


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
    # ObjectCollection + ValueInput + ExtentDirections seams
    mh._object_collection = _ObjColl.create
    mh._value = lambda s: ("V", s)
    mh._extent_dirs = _ExtentDir
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
        assert inp.extent == ("all", "POS")

    def test_multiple_points_one_feature(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[2, 2, 0], [5, 2, 0], [8, 2, 0]], extent="through"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.placed[0] == "points" and len(inp.placed[1]) == 3
        assert out["points"] == 3


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


# ── counterbore / countersink ───────────────────────────────────────────────

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
