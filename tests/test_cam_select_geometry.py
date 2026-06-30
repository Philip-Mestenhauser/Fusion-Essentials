"""Unit tests for ``cam_select_geometry`` — set the machining geometry (and optional heights) on a CAM
operation, then optionally regenerate.

adsk.cam is mocked. What we PIN is the handler's own logic:
  - dispatch by selection kind: curve family (chain/pocket/face/silhouette) via getCurveSelections ->
    createNew*Selection -> inputGeometry -> applyCurveSelections, vs the holes family via
    holeFaces.value = [faces];
  - chain knobs (is_open / reverted) only applied for chain;
  - diameter filtering of cylinder faces (mm), and the empty-after-filter guard;
  - height setting via _mode/_offset (never _value), validated-before-mutate;
  - generation gated on the FUTURE's isGenerationCompleted (not op.isGenerating), and the zero-depth
    diagnostic when a toolpath comes back empty with no warning;
  - the guards (bad selection, no CAM, missing/ambiguous op, 0 selections, no faces after filter).

The GeometryHandleList input kind has its own tests; here we patch the tool's resolve seam to hand
back fake entities so we exercise the handler, not the resolver.
"""

import json

import pytest

from conftest import load_tool

cg = load_tool("cam_select_geometry")


@pytest.fixture(autouse=True)
def _restore_resolver():
    """The tests patch _inputs.GeometryHandleList.resolve (a shared class method). Restore it after
    each test so the patch doesn't leak into other modules' tests."""
    orig = cg._inputs.GeometryHandleList.resolve
    yield
    cg._inputs.GeometryHandleList.resolve = orig


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


class _Selection:
    """A CurveSelection (chain/pocket/...). Records what was set."""
    def __init__(self, kind):
        self.kind = kind
        self.inputGeometry = None
        self.isOpen = None
        self.isReverted = None


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


def _install(cam, entities):
    cg._get_cam = lambda: (cam, None)
    # patch the geometry-handle resolver to hand back fake entities (resolver has its own tests)
    cg._inputs.GeometryHandleList.resolve = lambda self, raw: (entities, None)
    # make doEvents a no-op
    import adsk.core  # noqa
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_bad_selection(self):
        res = cg.handler(operation="X", selection="nonsense", handles=["h"])
        assert res["isError"] is True and "selection" in res["message"].lower()

    def test_no_cam(self):
        cg._get_cam = lambda: (None, "no CAM data")
        res = cg.handler(operation="X", selection="chain", handles=["h"])
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_op_not_found(self):
        cam = _CAM([_Setup([_curve_op("A")])])
        _install(cam, [_Edge()])
        res = cg.handler(operation="Ghost", selection="chain", handles=["h"])
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_handle_resolve_error_propagates(self):
        cam = _CAM([_Setup([_curve_op()])])
        cg._get_cam = lambda: (cam, None)
        cg._inputs.GeometryHandleList.resolve = lambda self, raw: (None, "bad handle")
        res = cg.handler(operation="2D Contour1", selection="chain", handles=["h"])
        assert res["isError"] is True and "bad handle" in res["message"]


# ── curve family (chain/pocket/...) ──────────────────────────────────────────

class TestCurveSelection:
    def test_chain_applies_and_sets_knobs(self):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install(cam, [_Edge(), _Edge(), _Edge(), _Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain",
                                  handles=["a", "b", "c", "d"], is_open=True, reverted=True,
                                  generate=False))
        pv = op.parameters.itemByName("contours").value
        sel = pv.getCurveSelections().item(0)
        assert sel.kind == "chain"
        assert len(sel.inputGeometry) == 4
        assert sel.isOpen is True and sel.isReverted is True
        assert pv.applied == 1 and out["selections"] == 1

    def test_pocket_uses_pocket_builder_and_ignores_chain_knobs(self):
        op = _curve_op(name="2D Pocket1")
        cam = _CAM([_Setup([op])])
        _install(cam, [_Face()])
        cg.handler(operation="2D Pocket1", selection="pocket", handles=["f"], is_open=True,
                   generate=False)
        sel = op.parameters.itemByName("contours").value.getCurveSelections().item(0)
        assert sel.kind == "pocket"
        assert sel.isOpen is None        # chain-only knob NOT applied to a pocket selection

    def test_zero_selections_is_error(self):
        # applyCurveSelections leaves count 0 -> geometry rejected -> hard error
        op = _curve_op()
        pv = op.parameters.itemByName("contours").value
        # make applyCurveSelections drop everything
        pv.applyCurveSelections = lambda cs: setattr(pv, "_cs", _CurveSelections())
        cam = _CAM([_Setup([op])])
        _install(cam, [_Edge()])
        res = cg.handler(operation="2D Contour1", selection="chain", handles=["h"], generate=False)
        assert res["isError"] is True and "0 selection" in res["message"]


# ── holes family + diameter filter ───────────────────────────────────────────

class TestHoles:
    def test_holes_sets_holefaces_directly(self):
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        faces = [_Face(0.3), _Face(0.3), _Face(0.5)]   # Ø6,Ø6,Ø10 (cm radius)
        _install(cam, faces)
        out = _payload(cg.handler(operation="Drill1", selection="holes", handles=["a","b","c"],
                                  generate=False))
        assert op.parameters.itemByName("holeFaces").value.value == faces
        assert out["selections"] == 3

    def test_diameter_filter_keeps_in_range(self):
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        faces = [_Face(0.3), _Face(0.3), _Face(0.3), _Face(0.3), _Face(0.5), _Face(0.5)]  # 4×Ø6, 2×Ø10
        _install(cam, faces)
        out = _payload(cg.handler(operation="Drill1", selection="holes", handles=["a"]*6,
                                  min_diameter=5.5, max_diameter=6.5, generate=False))
        assert len(op.parameters.itemByName("holeFaces").value.value) == 4   # only the Ø6
        assert out["selections"] == 4 and "diameter_filter" in out

    def test_diameter_filter_empty_is_error(self):
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        _install(cam, [_Face(0.5), _Face(0.5)])    # both Ø10, filter for Ø6 -> none
        res = cg.handler(operation="Drill1", selection="holes", handles=["a","b"],
                         min_diameter=5.5, max_diameter=6.5, generate=False)
        assert res["isError"] is True and "diameter filter" in res["message"].lower()

    def test_holes_on_nonhole_op_errors(self):
        op = _curve_op()                # neither holeFaces nor circularFaces
        cam = _CAM([_Setup([op])])
        _install(cam, [_Face(0.3)])
        res = cg.handler(operation="2D Contour1", selection="holes", handles=["a"], generate=False)
        assert res["isError"] is True
        assert "holeFaces" in res["message"] and "circularFaces" in res["message"]

    def test_holes_on_bore_uses_circularFaces(self):
        # bore/circular have 'circularFaces' (no 'holeFaces') — the holes mode must drive it
        op = _bore_op()
        cam = _CAM([_Setup([op])])
        faces = [_Face(0.6), _Face(0.6)]
        _install(cam, faces)
        out = _payload(cg.handler(operation="Bore1", selection="holes", handles=["a", "b"],
                                  generate=False))
        assert op.parameters.itemByName("circularFaces").value.value == faces
        assert out["selections"] == 2

    def test_holes_prefers_holeFaces_when_both_absent_irrelevant(self):
        # a drill op (only holeFaces) still works — holeFaces is probed first
        op = _drill_op()
        cam = _CAM([_Setup([op])])
        _install(cam, [_Face(0.4)])
        out = _payload(cg.handler(operation="Drill1", selection="holes", handles=["a"], generate=False))
        assert len(op.parameters.itemByName("holeFaces").value.value) == 1 and out["selections"] == 1


# ── heights ──────────────────────────────────────────────────────────────────

class TestHeights:
    def test_sets_mode_and_offset(self):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install(cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                                  bottom_mode="from contour", bottom_offset="-10 mm", generate=False))
        assert op.parameters.itemByName("bottomHeight_mode").expression == "from contour"
        assert op.parameters.itemByName("bottomHeight_offset").expression == "-10 mm"
        assert any("bottomHeight" in s for s in out["heights_set"])

    def test_heights_set_before_selection(self):
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
        _install(cam, [_Edge()])
        cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                   bottom_mode="from contour", generate=False)
        assert order.index("height") < order.index("selection")

    def test_missing_height_param_errors(self):
        op = _curve_op()
        del op.parameters._d["topHeight_offset"]      # simulate an op without that height
        cam = _CAM([_Setup([op])])
        _install(cam, [_Edge()])
        res = cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                         top_offset="0 mm", generate=False)
        assert res["isError"] is True and "topHeight_offset" in res["message"]


# ── generation: future-gated + zero-depth diagnostic ─────────────────────────

class TestGenerate:
    def test_generate_waits_on_future_and_reports_valid(self):
        op = _curve_op(has_tp=True, valid=True)
        cam = _CAM([_Setup([op])], future=_Future(True))
        _install(cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"]))
        assert cam.generated == [op]
        assert out["generated"] is True and out["toolpath_valid"] is True

    def test_empty_no_warning_gives_zero_depth_hint(self):
        # generation completes, but no toolpath + no warning -> the zero-depth diagnostic
        op = _curve_op(has_tp=False, valid=False, warning="")
        cam = _CAM([_Setup([op])], future=_Future(True))
        _install(cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"]))
        assert out["toolpath_valid"] is False
        assert "ZERO DEPTH" in out["note"]

    def test_warning_is_surfaced(self):
        op = _curve_op(has_tp=False, valid=False, warning="missing selection")
        cam = _CAM([_Setup([op])], future=_Future(True))
        _install(cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"]))
        assert out.get("warning") == "missing selection"

    def test_generate_false_skips(self):
        op = _curve_op()
        cam = _CAM([_Setup([op])])
        _install(cam, [_Edge()])
        out = _payload(cg.handler(operation="2D Contour1", selection="chain", handles=["h"],
                                  generate=False))
        assert "generated" not in out and cam.generated == []
