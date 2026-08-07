"""Unit tests for ``cam_select_geometry`` — set the machining geometry (and optional heights) on a CAM
operation, then optionally regenerate.

adsk.cam is mocked. What we PIN is the handler's own logic:
  - dispatch by selection kind: the curve family (chain/pocket/face/silhouette/sketch/
    pocket_recognition) via getCurveSelections -> createNew*Selection -> inputGeometry ->
    applyCurveSelections, vs the holes family via holeFaces.value = [faces];
  - each kind takes its geometry from ONE input (handles / bodies / sketches) and refuses the others;
  - the per-kind knobs (chain is_open/reverted, loop_type/side_type, the pocket-recognition filter),
    each confirmed by a read-back, and REFUSED on a kind whose selection class lacks the property;
  - sketch names are resolved design-wide (via _inputs.SketchRefList) and a name several sketches
    share is refused;
  - the post-apply read-back: outputGeometry paths/segments + value entities, and a selection whose
    hasError is set turning the call into an error carrying Fusion's own reason;
  - diameter filtering of cylinder faces (mm), and the empty-after-filter guard;
  - height setting via _mode/_offset (never _value), validated-before-mutate;
  - generation LAUNCH-and-return: the handler never pumps/waits on the future - it registers the
    future in cam_generate._GENERATIONS (keeps it alive) and the note teaches the
    cam_get_status(target=...) poll;
  - the guards (bad selection, no CAM, missing/ambiguous op, 0 selections, no faces after filter).

The GeometryHandleList/BodyRefList input kinds have their own tests; here we patch those resolve
seams to hand back fake entities so we exercise the handler, not the resolver. The sketch input is
resolved for real against a fake design, because refusing a duplicated sketch name is this tool's
own promise.
"""

import json

import pytest

from conftest import load_tool, make_cam, install, make_sketch, MakeComp, MakeDesign
from conftest import FakeSetup as SharedSetup, FakeOperation as SharedOp

cg = load_tool("cam_select_geometry")


@pytest.fixture(autouse=True)
def _restore_resolver():
    """The tests patch _inputs.GeometryHandleList.resolve / BodyRefList.resolve (shared class
    methods). Restore them after each test so the patch doesn't leak into other modules' tests."""
    orig_handles = cg._inputs.GeometryHandleList.resolve
    orig_bodies = cg._inputs.BodyRefList.resolve
    yield
    cg._inputs.GeometryHandleList.resolve = orig_handles
    cg._inputs.BodyRefList.resolve = orig_bodies


@pytest.fixture(autouse=True)
def _clean_generations():
    """A launch registers a future in cam_generate._GENERATIONS (shared, session-lived) - clear it
    around each test so entries never leak into test_cam_generate's registry assertions."""
    cg.cam_generate._GENERATIONS.clear()
    cg.cam_generate._HANDLE_SEQ[0] = 0
    yield
    cg.cam_generate._GENERATIONS.clear()
    cg.cam_generate._HANDLE_SEQ[0] = 0


# ── fakes ────────────────────────────────────────────────────────────────────

class _Cyl:
    def __init__(self, radius_cm):
        self.radius = radius_cm


class _Face:
    """A BRep face; cylinder faces carry .geometry.radius (cm)."""
    def __init__(self, radius_cm=None):
        self.geometry = _Cyl(radius_cm) if radius_cm is not None else object()


class _Edge:
    pass


class _Body:
    def __init__(self, name="Body1"):
        self.name = name


class _Path:
    """A Curve3DPath: a counted collection of connected Curve3D objects."""
    def __init__(self, count):
        self.count = count


class _Selection:
    """A CurveSelection (chain/pocket/sketch/...). Records what was set - in `writes`, in order - and
    carries the read-back channel every kind inherits from CurveSelection: outputGeometry, value,
    hasError/error, hasWarning/warning - plus the per-kind knobs the appliers set."""
    def __init__(self, kind):
        object.__setattr__(self, "writes", [])
        self.areHolesIncluded = None          # gates minimumHoleDiameter below - set it first
        self._min_hole_dia = None
        self.kind = kind
        self.inputGeometry = None
        self.isOpen = None
        self.isReverted = None
        self.loopType = None
        self.sideType = None
        self.isSetupModelSelected = None
        self.minimumCornerRadius = None
        self.maximumCornerRadius = None
        self.minimumPocketDepth = None
        self.maximumPocketDepth = None
        self.outputGeometry = []
        self.hasError = False
        self.error = ""
        self.hasWarning = False
        self.warning = ""
        self._value = None
        object.__setattr__(self, "writes", [])   # only the handler's own writes are recorded

    def __setattr__(self, name, value):
        self.writes.append(name)
        object.__setattr__(self, name, value)

    @property
    def minimumHoleDiameter(self):
        return self._min_hole_dia

    @minimumHoleDiameter.setter
    def minimumHoleDiameter(self, value):
        # The binding gates this one: "It can only be set if areHoldeIncluded is set to true." Set
        # out of order, the selection keeps its default and the caller's bound never applies.
        if self.areHolesIncluded:
            self._min_hole_dia = value

    @property
    def value(self):
        # CurveSelection.value reports the input selection unless a kind expands it.
        return (self.inputGeometry or []) if self._value is None else self._value


class _DeafHoleDiameter(_Selection):
    """A selection that keeps its default hole-diameter bound whatever is written to it - so the
    payload can only report the true value by READING it back."""
    @property
    def minimumHoleDiameter(self):
        return None

    @minimumHoleDiameter.setter
    def minimumHoleDiameter(self, value):
        pass


class _InertLoopType(_Selection):
    """A selection that SWALLOWS a loopType assignment and keeps its default - the SWIG behaviour a
    set-then-read-back exists to catch."""
    @property
    def loopType(self):
        return None
    @loopType.setter
    def loopType(self, value):
        pass


class _CurveSelections:
    def __init__(self):
        self._sels = []
        self.cleared = 0
    def clear(self):
        self.cleared += 1
        self._sels = []
    @property
    def count(self):
        return len(self._sels)
    def item(self, i):
        return self._sels[i]
    def _make(self, kind):
        s = _Selection(kind); self._sels.append(s); return s
    def createNewChainSelection(self):       return self._make("chain")
    def createNewPocketSelection(self):      return self._make("pocket")
    def createNewFaceContourSelection(self): return self._make("face")
    def createNewSilhouetteSelection(self):  return self._make("silhouette")
    def createNewSketchSelection(self):      return self._make("sketch")
    def createNewPocketRecognitionSelection(self): return self._make("pocket_recognition")


class _CurveParamValue:
    def __init__(self):
        self._cs = _CurveSelections()
        self.applied = 0
    def getCurveSelections(self):
        return self._cs
    def applyCurveSelections(self, cs):
        self.applied += 1
        self._cs = cs


class _HoleParamValue:
    def __init__(self):
        self.value = []          # set to [faces] by the handler


class _Param:
    def __init__(self, value):
        self.value = value
        self.expression = None


class _Params:
    def __init__(self, d):
        self._d = d
    def itemByName(self, name):
        return self._d.get(name)
    @property
    def count(self):
        return len(self._d)


class _Future:
    def __init__(self, complete=True):
        self.isGenerationCompleted = complete


class _Op:
    def __init__(self, name, params, has_tp=True, valid=True, warning=""):
        self.name = name
        self.parameters = _Params(params)
        self.hasToolpath = has_tp
        self.isToolpathValid = valid
        self.warning = warning
        self.isGenerating = False


class _Coll:
    def __init__(self, items=()):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class _Setup:
    def __init__(self, ops):
        self.operations = _Coll(ops)
        self.folders = _Coll()
        self.patterns = _Coll()


class _CAM:
    def __init__(self, setups, future=None):
        self.setups = _Coll(setups)
        self._future = future or _Future(True)
        self.generated = []
    def generateToolpath(self, op):
        self.generated.append(op)
        return self._future


def _curve_op(name="2D Contour1", **kw):
    return _Op(name, {"contours": _Param(_CurveParamValue()),
                      "topHeight_mode": _Param(None), "topHeight_offset": _Param(None),
                      "bottomHeight_mode": _Param(None), "bottomHeight_offset": _Param(None)}, **kw)


def _drill_op(name="Drill1", **kw):
    return _Op(name, {"holeFaces": _Param(_HoleParamValue())}, **kw)


def _bore_op(name="Bore1", **kw):
    # bore/circular use 'circularFaces' (no 'holeFaces') — same object-list shape
    return _Op(name, {"circularFaces": _Param(_HoleParamValue())}, **kw)


def _install(monkeypatch, cam, entities):
    monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
    # patch the geometry-handle resolver to hand back fake entities (resolver has its own tests)
    cg._inputs.GeometryHandleList.resolve = lambda self, raw: (entities, None)
    return cam


def _install_bodies(monkeypatch, cam, bodies):
    monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
    cg._inputs.BodyRefList.resolve = lambda self, raw: (list(bodies), None)
    return cam


def _selection_of(op):
    return op.parameters.itemByName("contours").value.getCurveSelections().item(0)


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_bad_selection(self, monkeypatch):
        res = cg.handler(operation="X", selection="nonsense", handles=["h"])
        assert res["isError"] is True and "selection" in res["message"].lower()

    def test_no_cam(self, monkeypatch):
        monkeypatch.setattr(cg, "get_cam", lambda: (None, "no CAM data"))
        res = cg.handler(operation="X", selection="chain", handles=["h"])
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_op_not_found(self, monkeypatch):
        cam = _CAM([_Setup([_curve_op("A")])])
        _install(monkeypatch, cam, [_Edge()])
        res = cg.handler(operation="Ghost", selection="chain", handles=["h"])
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_duplicate_op_name_across_setups_is_refused(self, monkeypatch):
        # "Drill1" exists in TWO setups - selecting geometry by that name must REFUSE with both
        # setup paths, never silently target whichever setup's op the walk met first.
        cam = make_cam(SharedSetup("Setup1", ops=[SharedOp("Drill1")]),
                       SharedSetup("Setup2", ops=[SharedOp("Drill1")]))
        monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
        cg._inputs.GeometryHandleList.resolve = lambda self, raw: ([_Face(0.3)], None)
        res = cg.handler(operation="Drill1", selection="holes", handles=["a"])
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "Setup1 / Drill1" in res["message"] and "Setup2 / Drill1" in res["message"]

    def test_handle_resolve_error_propagates(self, monkeypatch):
        cam = _CAM([_Setup([_curve_op()])])
        monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
        cg._inputs.GeometryHandleList.resolve = lambda self, raw: (None, "bad handle")
        res = cg.handler(operation="2D Contour1", selection="chain", handles=["h"])
        assert res["isError"] is True and "bad handle" in res["message"]

    def test_unknown_units_refused(self, monkeypatch):
        cam = _CAM([_Setup([_curve_op()])])
        _install(monkeypatch, cam, [_Edge()])
        res = cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                         units="furlongs", generate=False)
        assert res["isError"] is True and "furlongs" in res["message"]

    def test_handles_on_a_body_selection_is_refused(self, monkeypatch):
        # silhouette's inputGeometry takes BRepBody, so face handles are the wrong input entirely -
        # the refusal must name the input that DOES carry it rather than resolve them as faces.
        cam = _CAM([_Setup([_curve_op()])])
        _install(monkeypatch, cam, [_Face()])
        res = cg.handler(operation="2D Contour1", selection="silhouette", handles=["h"],
                         generate=False)
        assert res["isError"] is True
        assert "'bodies'" in res["message"] and "'handles'" in res["message"]

    def test_bodies_on_a_handle_selection_is_refused(self, monkeypatch):
        cam = _CAM([_Setup([_curve_op()])])
        _install(monkeypatch, cam, [_Edge()])
        res = cg.handler(operation="2D Contour1", selection="chain", bodies=["Carrier"],
                         generate=False)
        assert res["isError"] is True and "'handles'" in res["message"]


# ── curve family (chain/pocket/...) ──────────────────────────────────────────

class TestCurveSelection:
    def test_chain_applies_and_sets_knobs(self, monkeypatch):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Edge(), _Edge(), _Edge(), _Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain",
                                  handles=["a", "b", "c", "d"], is_open=True, reverted=True,
                                  generate=False))
        pv = op.parameters.itemByName("contours").value
        sel = pv.getCurveSelections().item(0)
        assert sel.kind == "chain"
        assert len(sel.inputGeometry) == 4
        assert sel.isOpen is True and sel.isReverted is True
        assert pv.applied == 1 and out["selections"] == 1

    def test_pocket_uses_pocket_builder(self, monkeypatch):
        op = _curve_op(name="2D Pocket1")
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face()])
        cg.handler(operation="2D Pocket1", selection="pocket", handles=["f"], generate=False)
        assert _selection_of(op).kind == "pocket"

    def test_zero_selections_is_error(self, monkeypatch):
        # applyCurveSelections leaves count 0 -> geometry rejected -> hard error
        op = _curve_op()
        pv = op.parameters.itemByName("contours").value
        # make applyCurveSelections drop everything
        pv.applyCurveSelections = lambda cs: setattr(pv, "_cs", _CurveSelections())
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Edge()])
        res = cg.handler(operation="2D Contour1", selection="chain", handles=["h"], generate=False)
        assert res["isError"] is True and "0 selection" in res["message"]


# ── the two body selections (silhouette / pocket_recognition) ────────────────

class TestBodySelections:
    def test_no_bodies_machines_the_setup_models(self, monkeypatch):
        # The zero-body form is a NAMED flag (isSetupModelSelected), not an undocumented default of
        # passing nothing - so it is set explicitly and reported.
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install_bodies(monkeypatch, cam, [])
        out = _payload(cg.handler(operation="2D Contour1", selection="silhouette", generate=False))
        sel = _selection_of(op)
        assert sel.kind == "silhouette" and sel.isSetupModelSelected is True
        assert out["setup_models_selected"] is True

    def test_named_bodies_clear_the_setup_model_flag(self, monkeypatch):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        bodies = [_Body("Carrier")]
        _install_bodies(monkeypatch, cam, bodies)
        out = _payload(cg.handler(operation="2D Contour1", selection="silhouette",
                                  bodies=["Carrier"], generate=False))
        sel = _selection_of(op)
        assert sel.inputGeometry == bodies and sel.isSetupModelSelected is False
        assert out["setup_models_selected"] is False

    def test_pocket_recognition_uses_its_own_builder(self, monkeypatch):
        op = _curve_op(name="Adaptive1")
        cam = _CAM([_Setup([op])])
        _install_bodies(monkeypatch, cam, [_Body("Carrier")])
        cg.handler(operation="Adaptive1", selection="pocket_recognition", bodies=["Carrier"],
                   generate=False)
        assert _selection_of(op).kind == "pocket_recognition"


class TestPocketFilter:
    def _run(self, monkeypatch, flt, units="mm"):
        op = _curve_op(name="Adaptive1")
        cam = _CAM([_Setup([op])])
        _install_bodies(monkeypatch, cam, [_Body("Carrier")])
        res = cg.handler(operation="Adaptive1", selection="pocket_recognition", bodies=["Carrier"],
                         pocket_filter=flt, units=units, generate=False)
        return op, res

    def test_min_hole_diameter_without_holes_is_refused(self, monkeypatch):
        # minimumHoleDiameter can only be set while areHolesIncluded is true - refuse rather than
        # write a value the API will not keep.
        op, res = self._run(monkeypatch, {"min_hole_diameter": 2.5})
        assert res["isError"] is True and "holes=true" in res["message"]
        assert _selection_of(op).minimumHoleDiameter is None

    def test_holes_is_set_before_the_hole_diameter(self, monkeypatch):
        # areHolesIncluded GATES minimumHoleDiameter, so the flag has to be written first or the
        # bound never lands (the fake keeps its default when the order is wrong).
        op, res = self._run(monkeypatch, {"holes": True, "min_hole_diameter": 2.5})
        sel = _selection_of(op)
        assert res["isError"] is False
        assert sel.areHolesIncluded is True
        assert sel.minimumHoleDiameter == pytest.approx(0.25)      # 2.5 mm -> 0.25
        assert sel.writes.index("areHolesIncluded") < sel.writes.index("minimumHoleDiameter")
        assert _payload(res)["pocket_filter_applied"] == {"holes": True, "min_hole_diameter": 0.25}

    def test_every_length_lands_scaled_and_is_published(self, monkeypatch):
        op, res = self._run(monkeypatch, {"min_corner_radius": 1.0, "max_corner_radius": 8.0,
                                          "min_depth": 3.0, "max_depth": 40.0})
        sel = _selection_of(op)
        assert res["isError"] is False
        assert (sel.minimumCornerRadius, sel.maximumCornerRadius) == pytest.approx((0.1, 0.8))
        assert (sel.minimumPocketDepth, sel.maximumPocketDepth) == pytest.approx((0.3, 4.0))
        assert _payload(res)["pocket_filter_applied"] == {
            "min_corner_radius": 0.1, "max_corner_radius": 0.8,
            "min_depth": 0.3, "max_depth": 4.0}

    def test_inch_units_scale_the_filter(self, monkeypatch):
        op, res = self._run(monkeypatch, {"min_depth": 1.0}, units="in")
        assert res["isError"] is False
        assert _selection_of(op).minimumPocketDepth == pytest.approx(2.54)
        assert _payload(res)["pocket_filter_applied"] == {"min_depth": 2.54}

    def test_a_filter_value_the_selection_drops_is_an_error(self, monkeypatch):
        # min_hole_diameter is refused when holes is false; when holes is TRUE but the selection
        # still fails to keep the value, the read-back - not the written number - is what tells us.
        op = _curve_op(name="Adaptive1")
        cam = _CAM([_Setup([op])])
        _install_bodies(monkeypatch, cam, [_Body("Carrier")])
        pv = op.parameters.itemByName("contours").value
        def _deaf(kind):
            sel = _DeafHoleDiameter(kind)
            pv._cs._sels.append(sel)
            return sel
        pv._cs._make = _deaf
        res = cg.handler(operation="Adaptive1", selection="pocket_recognition", bodies=["Carrier"],
                         pocket_filter={"holes": True, "min_hole_diameter": 2.5}, generate=False)
        assert res["isError"] is True and "min_hole_diameter" in res["message"]
        assert pv.applied == 0

    def test_unknown_filter_key_is_refused(self, monkeypatch):
        op, res = self._run(monkeypatch, {"min_taper": 3.0})
        assert res["isError"] is True and "min_taper" in res["message"]

    def test_non_numeric_length_is_refused(self, monkeypatch):
        op, res = self._run(monkeypatch, {"min_depth": "deep"})
        assert res["isError"] is True and "min_depth" in res["message"]


# ── the sketch selection (whole sketches, by name) ───────────────────────────

def _sketch_design(*comps):
    root = comps[0]
    return MakeDesign(comp=root, all_components=list(comps))


class TestSketchSelection:
    def test_resolves_a_sketch_by_name(self, monkeypatch):
        sk = make_sketch("Pocket Outline")
        install(cg, _sketch_design(MakeComp("Root", sketches=[sk])))
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
        out = _payload(cg.handler(operation="2D Contour1", selection="sketch",
                                  sketches=["Pocket Outline"], generate=False))
        sel = _selection_of(op)
        assert sel.kind == "sketch" and sel.inputGeometry == [sk]
        assert out["selections"] == 1

    def test_duplicate_sketch_name_across_components_is_refused(self, monkeypatch):
        # Two components can each hold a "Sketch1", so a first-match resolve would silently machine
        # the wrong one. The refusal counts the SKETCHES it found and names where they live.
        root = MakeComp("Carrier", sketches=[make_sketch("Sketch1")])
        sub = MakeComp("Bracket", sketches=[make_sketch("Sketch1")])
        install(cg, _sketch_design(root, sub))
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
        res = cg.handler(operation="2D Contour1", selection="sketch", sketches=["Sketch1"],
                         generate=False)
        assert res["isError"] is True
        assert "2 sketches are named 'Sketch1'" in res["message"]
        assert "'Carrier'" in res["message"] and "'Bracket'" in res["message"]
        assert op.parameters.itemByName("contours").value.applied == 0   # nothing was applied

    def test_unknown_sketch_lists_the_available_names(self, monkeypatch):
        install(cg, _sketch_design(MakeComp("Root", sketches=[make_sketch("Outline")])))
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
        res = cg.handler(operation="2D Contour1", selection="sketch", sketches=["Ghost"],
                         generate=False)
        assert res["isError"] is True and "Ghost" in res["message"] and "Outline" in res["message"]

    def test_sketch_selection_needs_a_sketch(self, monkeypatch):
        install(cg, _sketch_design(MakeComp("Root", sketches=[make_sketch("Outline")])))
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
        res = cg.handler(operation="2D Contour1", selection="sketch", generate=False)
        assert res["isError"] is True and "sketches" in res["message"]


# ── per-kind knobs ───────────────────────────────────────────────────────────

class TestKnobs:
    def test_loop_and_side_type_land_on_a_face_selection(self, monkeypatch):
        op = _curve_op(name="Face1")
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face()])
        out = _payload(cg.handler(operation="Face1", selection="face", handles=["f"],
                                  loop_type="outside", side_type="always_inside", generate=False))
        sel = _selection_of(op)
        assert sel.loopType is cg.adsk.cam.LoopTypes.OnlyOutsideLoops
        assert sel.sideType is cg.adsk.cam.SideTypes.AlwaysInsideSideType
        assert out["loop_type"] == "outside" and out["side_type"] == "always_inside"

    def test_loop_type_on_holes_is_refused(self, monkeypatch):
        # A drill's object-list selection has no loopType at all - dropping the knob silently would
        # leave the caller believing an option applied.
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face(0.3)])
        res = cg.handler(operation="Drill1", selection="holes", handles=["a"], loop_type="outside",
                         generate=False)
        assert res["isError"] is True and "loop_type" in res["message"] and "holes" in res["message"]
        assert op.parameters.itemByName("holeFaces").value.value == []   # refused before any write

    def test_chain_knob_on_a_pocket_selection_is_refused(self, monkeypatch):
        op = _curve_op(name="2D Pocket1")
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face()])
        res = cg.handler(operation="2D Pocket1", selection="pocket", handles=["f"], is_open=True,
                         generate=False)
        assert res["isError"] is True and "is_open" in res["message"]

    def test_pocket_filter_on_a_silhouette_is_refused(self, monkeypatch):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install_bodies(monkeypatch, cam, [])
        res = cg.handler(operation="2D Contour1", selection="silhouette",
                         pocket_filter={"holes": True}, generate=False)
        assert res["isError"] is True and "pocket_filter" in res["message"]

    def test_bad_loop_type_value_is_refused(self, monkeypatch):
        op = _curve_op(name="Face1")
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face()])
        res = cg.handler(operation="Face1", selection="face", handles=["f"], loop_type="sideways",
                         generate=False)
        assert res["isError"] is True and "sideways" in res["message"]

    def test_a_knob_that_does_not_stick_is_an_error(self, monkeypatch):
        # A SWIG proxy accepts an assignment to a property it does not define; only the read-back
        # reveals it, so a knob that reads back unchanged must fail the call.
        op = _curve_op(name="Face1")
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face()])
        pv = op.parameters.itemByName("contours").value
        def _inert(kind):
            sel = _InertLoopType(kind)
            pv._cs._sels.append(sel)
            return sel
        pv._cs._make = _inert
        res = cg.handler(operation="Face1", selection="face", handles=["f"],
                         loop_type="outside", generate=False)
        assert res["isError"] is True and "loop_type" in res["message"]
        assert pv.applied == 0                      # never applied on an unset knob


# ── read-back off the applied selection ──────────────────────────────────────

class TestReadBack:
    def _apply(self, monkeypatch, prepare):
        op = _curve_op(name="Face1")
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face(), _Face()])
        pv = op.parameters.itemByName("contours").value
        real_make = pv._cs._make
        def _prepared(kind):
            sel = real_make(kind)
            prepare(sel)
            return sel
        pv._cs._make = _prepared
        return cg.handler(operation="Face1", selection="face", handles=["a", "b"], generate=False)

    def test_reports_what_fusion_resolved(self, monkeypatch):
        def prep(sel):
            sel.outputGeometry = [_Path(3), _Path(5)]
            sel._value = [object()] * 7        # value can EXCEED the input (same-plane expansion)
        out = _payload(self._apply(monkeypatch, prep))
        assert out["resolved"] == {"curve_paths": 2, "curve_segments": 8, "entities": 7}

    def test_zero_resolved_paths_are_reported_not_hidden(self, monkeypatch):
        out = _payload(self._apply(monkeypatch, lambda sel: None))
        assert out["resolved"]["curve_paths"] == 0 and out["resolved"]["curve_segments"] == 0
        assert out["resolved"]["entities"] == 2          # value mirrors the input geometry

    def test_selection_error_after_apply_is_an_error_carrying_fusions_reason(self, monkeypatch):
        # The selection's own error channel is populated by applyCurveSelections; reporting ok
        # because the COLLECTION count is nonzero would swallow a rejected selection.
        def prep(sel):
            sel.hasError = True
            sel.error = "The selected geometry is not planar."
        res = self._apply(monkeypatch, prep)
        assert res["isError"] is True
        assert "not planar" in res["message"] and "face" in res["message"]
        # The collection was cleared before this selection was built, so the operation is NOT back on
        # what it held before the call - the refusal has to say so.
        assert "cleared" in res["message"] and "select its geometry again" in res["message"]

    def test_selection_warning_is_surfaced_without_failing(self, monkeypatch):
        def prep(sel):
            sel.hasWarning = True
            sel.warning = "Some contours were skipped."
        out = _payload(self._apply(monkeypatch, prep))
        assert out["selection_warning"] == "Some contours were skipped."


# ── holes family + diameter filter ───────────────────────────────────────────

class TestHoles:
    def test_holes_sets_holefaces_directly(self, monkeypatch):
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        faces = [_Face(0.3), _Face(0.3), _Face(0.5)]   # Ø6,Ø6,Ø10 (cm radius)
        _install(monkeypatch, cam, faces)
        out = _payload(cg.handler(operation="Drill1", selection="holes", handles=["a","b","c"],
                                  generate=False))
        assert op.parameters.itemByName("holeFaces").value.value == faces
        assert out["selections"] == 3

    def test_diameter_filter_keeps_in_range(self, monkeypatch):
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        faces = [_Face(0.3), _Face(0.3), _Face(0.3), _Face(0.3), _Face(0.5), _Face(0.5)]  # 4×Ø6, 2×Ø10
        _install(monkeypatch, cam, faces)
        out = _payload(cg.handler(operation="Drill1", selection="holes", handles=["a"]*6,
                                  min_diameter=5.5, max_diameter=6.5, generate=False))
        assert len(op.parameters.itemByName("holeFaces").value.value) == 4   # only the Ø6
        assert out["selections"] == 4 and "diameter_filter" in out

    def test_diameter_filter_empty_is_error(self, monkeypatch):
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face(0.5), _Face(0.5)])    # both Ø10, filter for Ø6 -> none
        res = cg.handler(operation="Drill1", selection="holes", handles=["a","b"],
                         min_diameter=5.5, max_diameter=6.5, generate=False)
        assert res["isError"] is True and "diameter filter" in res["message"].lower()

    def test_holes_on_nonhole_op_errors(self, monkeypatch):
        op = _curve_op()                # neither holeFaces nor circularFaces
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face(0.3)])
        res = cg.handler(operation="2D Contour1", selection="holes", handles=["a"], generate=False)
        assert res["isError"] is True
        assert "holeFaces" in res["message"] and "circularFaces" in res["message"]

    def test_holes_on_bore_uses_circularFaces(self, monkeypatch):
        # bore/circular have 'circularFaces' (no 'holeFaces') — the holes mode must drive it
        op = _bore_op()
        cam = _CAM([_Setup([op])])
        faces = [_Face(0.6), _Face(0.6)]
        _install(monkeypatch, cam, faces)
        out = _payload(cg.handler(operation="Bore1", selection="holes", handles=["a", "b"],
                                  generate=False))
        assert op.parameters.itemByName("circularFaces").value.value == faces
        assert out["selections"] == 2

    def test_holes_prefers_holeFaces_when_both_absent_irrelevant(self, monkeypatch):
        # a drill op (only holeFaces) still works — holeFaces is probed first
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face(0.4)])
        out = _payload(cg.handler(operation="Drill1", selection="holes", handles=["a"], generate=False))
        assert len(op.parameters.itemByName("holeFaces").value.value) == 1 and out["selections"] == 1


# ── heights ──────────────────────────────────────────────────────────────────

class TestHeights:
    def test_sets_mode_and_offset(self, monkeypatch):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                                  bottom_mode="from contour", bottom_offset="-10 mm", generate=False))
        assert op.parameters.itemByName("bottomHeight_mode").expression == "from contour"
        assert op.parameters.itemByName("bottomHeight_offset").expression == "-10 mm"
        assert any("bottomHeight" in s for s in out["heights_set"])

    def test_heights_set_before_selection(self, monkeypatch):
        # ordering matters live: a height _mode's valid enum is context-dependent and applying the
        # selection can transiently invalidate it. So heights must be set BEFORE the selection applies.
        order = []
        op = _curve_op()
        pv = op.parameters.itemByName("contours").value
        real_apply = pv.applyCurveSelections
        def tracked_apply(cs):
            order.append("selection")
            return real_apply(cs)
        pv.applyCurveSelections = tracked_apply
        mode_param = op.parameters.itemByName("bottomHeight_mode")
        class _Tracking:
            def __init__(self, p): self._p = p
            @property
            def expression(self): return self._p.expression
            @expression.setter
            def expression(self, v):
                order.append("height"); self._p.expression = v
        op.parameters._d["bottomHeight_mode"] = _Tracking(mode_param)
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Edge()])
        cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                   bottom_mode="from contour", generate=False)
        assert order.index("height") < order.index("selection")

    def test_missing_height_param_errors(self, monkeypatch):
        op = _curve_op()
        del op.parameters._d["topHeight_offset"]      # simulate an op without that height
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Edge()])
        res = cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                         top_offset="0 mm", generate=False)
        assert res["isError"] is True and "topHeight_offset" in res["message"]


# ── generation: launch-and-return (the poll owns the pumping) ────────────────

class TestGenerate:
    def test_returns_immediately_without_pumping_while_future_incomplete(self, monkeypatch):
        # The handler LAUNCHES generation and returns - it must never pump adsk.doEvents waiting on
        # the future (that blocks Fusion's UI for the whole compute; cam_get_status owns the pump).
        # An INCOMPLETE future proves it: a wait loop would spin its full time budget here.
        import adsk
        pumps = []
        monkeypatch.setattr(adsk, "doEvents", lambda: pumps.append(1), raising=False)
        op = _curve_op()
        cam = _CAM([_Setup([op])], future=_Future(complete=False))   # never completes
        _install(monkeypatch, cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"]))
        assert cam.generated == [op]
        assert out["launched"] is True
        assert pumps == []                                # returned with the future still incomplete

    def test_future_registered_in_cam_generate_registry(self, monkeypatch):
        # The returned handle keys cam_generate._GENERATIONS and the entry holds THE launched future:
        # a dropped future is garbage-collected and Fusion abandons the generation; the registry is
        # also what cam_get_status's handle path polls and cleans up.
        op = _curve_op()
        cam = _CAM([_Setup([op])], future=_Future(complete=False))
        _install(monkeypatch, cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"]))
        entry = cg.cam_generate._GENERATIONS[out["handle"]]
        assert entry["future"] is cam._future
        assert entry["scope"] == "operation" and "2D Contour1" in entry["target"]

    def test_note_teaches_cam_get_status_target_poll(self, monkeypatch):
        op = _curve_op()
        cam = _CAM([_Setup([op])], future=_Future(complete=False))
        _install(monkeypatch, cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"]))
        assert "cam_get_status(target='2D Contour1')" in out["note"]
        assert "completed=true" in out["note"]

    def test_launch_failure_reported_with_selection_kept(self, monkeypatch):
        # The selection mutation already took; a failed LAUNCH is reported as generate_error with the
        # cam_generate retry pointer, not a false 'launched' and not an isError that hides the applied
        # selection.
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        def _boom(op):
            raise RuntimeError("launch refused")
        cam.generateToolpath = _boom
        _install(monkeypatch, cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"]))
        assert out["selections"] == 1
        assert "launch refused" in out["generate_error"]
        assert "launched" not in out and "handle" not in out
        assert "cam_generate" in out["note"]

    def test_generate_false_skips(self, monkeypatch):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                                  generate=False))
        assert "launched" not in out and cam.generated == []
        assert cg.cam_generate._GENERATIONS == {}
