"""Unit tests for ``model_hole`` — the real HoleFeatures building block (not a sketch+extrude-cut).

The Fusion API is mocked; what we pin is the tool's OWN logic: the type dispatch (simple / counterbore /
countersink) and which create*Input builder + value args each uses, placement by sketch points created
on the target face, the extent choice (blind => setDistanceExtent, through => setAllExtent with the
PositiveExtentDirection that the live spike proved is required), optional tapping (createThreadInfo +
setToTappedHole), and the guards (unknown type, missing diameters, no face/points, bad extent).

The HoleFeatureInput fake RECORDS the calls so we can assert the exact builder path.
"""

import json

from conftest import load_tool

mh = load_tool("model_hole")


# ── fakes that record the hole-input construction ───────────────────────────

class _ExtentDir:
    PositiveExtentDirection = "POS"
    NegativeExtentDirection = "NEG"


class FakeHoleInput:
    def __init__(self, kind, args):
        self.kind = kind            # 'simple' | 'counterbore' | 'countersink'
        self.args = args            # the ValueInput strings passed to the builder
        self.placed = None          # ('point', pt) or ('points', [pts])
        self.extent = None          # ('distance', val) or ('all', direction)
        self.tap = None             # ThreadInfo or None
        self.clearance = None       # ClearanceHoleInfo or None
        self.isModeled = False
        self.isDefaultDirection = True
        self.holeTapType = 0
    def setPositionBySketchPoint(self, sp):
        self.placed = ("point", sp); return True
    def setPositionBySketchPoints(self, coll):
        self.placed = ("points", list(coll.items)); return True
    def setDistanceExtent(self, v):
        self.extent = ("distance", v); return True
    def setAllExtent(self, direction):
        self.extent = ("all", direction); return True
    def setToTappedHole(self, ti):
        self.tap = ti; self.holeTapType = 2; return True
    def setToClearanceHole(self, chi):
        self.clearance = chi; return True


class FakeHoleFeature:
    def __init__(self, inp):
        self.name = "Hole1"
        self._inp = inp


class FakeHoleFeatures:
    def __init__(self):
        self.added = []
    def __bool__(self):
        # a real Fusion collection is FALSY when empty (count==0). The tool must test `is None`,
        # not `not holes`, or an empty-but-valid HoleFeatures collection is wrongly rejected.
        return len(self.added) > 0
    def __len__(self):
        return len(self.added)
    def createSimpleInput(self, dia):
        return FakeHoleInput("simple", {"dia": dia})
    def createCounterboreInput(self, dia, cbd, cbdepth):
        return FakeHoleInput("counterbore", {"dia": dia, "cb_dia": cbd, "cb_depth": cbdepth})
    def createCountersinkInput(self, dia, csd, csa):
        return FakeHoleInput("countersink", {"dia": dia, "cs_dia": csd, "cs_angle": csa})
    def add(self, inp):
        if inp.placed is None or inp.extent is None:
            raise RuntimeError("InternalValidationError : logicalSelection")
        f = FakeHoleFeature(inp); self.added.append(f); return f


class _ThreadInfo:
    def __init__(self, internal, ttype, desig, cls):
        self.internal, self.ttype, self.desig, self.cls = internal, ttype, desig, cls


class FakeThreadDataQuery:
    def __init__(self):
        self.types = ["ANSI Metric M Profile", "ANSI Unified Screw Threads"]
    @property
    def allThreadTypes(self):
        return tuple(self.types)
    def allSizes(self, t):
        return ("5.0", "6.0")
    def allDesignations(self, t, size):
        return ("M5x0.8", "M5x0.5") if size.startswith("5") else ("M6x1",)
    def allClasses(self, internal, t, desig):
        return ("6H",) if internal else ("6g",)


class FakeThreadFeatures:
    def __init__(self):
        self.threadDataQuery = FakeThreadDataQuery()
        self.created = []
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


class _Sketches:
    def __init__(self):
        self._byname = {}
        self.created_on = []
    def add(self, plane):
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
    # face resolver returns a stand-in planar face; the tool builds a sketch on it
    mh._resolve_face = lambda d, h: ("FACE", h)
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
        # the live spike proved THROUGH must use PositiveExtentDirection (Negative fails)
        assert inp.extent == ("all", "POS")

    def test_multiple_points_one_feature(self):
        d = _install()
        out = _payload(mh.handler(hole_type="simple", diameter="5 mm", face="h",
                                  points=[[2, 2, 0], [5, 2, 0], [8, 2, 0]], extent="through"))
        inp = d.rootComponent.features.holeFeatures.added[0]._inp
        assert inp.placed[0] == "points" and len(inp.placed[1]) == 3
        assert out["points"] == 3


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
        _install()
        res = mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                         fastener="M6 Banana Bolt", fit="normal")
        assert res["isError"] is True

    def test_bad_fit_errors(self):
        _install()
        res = mh.handler(hole_type="simple", face="h", points=[[2, 3, 0]], extent="through",
                         fastener="M6 Socket Head Cap Screw", fit="snug")
        assert res["isError"] is True and "fit" in res["message"].lower()

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
