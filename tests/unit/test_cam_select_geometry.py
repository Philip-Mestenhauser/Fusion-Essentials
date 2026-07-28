"""Unit tests for ``cam_select_geometry`` — set the machining geometry (and optional heights) on a CAM
operation, then optionally regenerate.

adsk.cam is mocked. What we PIN is the handler's own logic:
  - dispatch by selection kind: curve family (chain/pocket/face/silhouette) via getCurveSelections ->
    createNew*Selection -> inputGeometry -> applyCurveSelections, vs the holes family via
    holeFaces.value = [faces];
  - chain knobs (is_open / reverted) only applied for chain;
  - diameter filtering of cylinder faces (mm), and the empty-after-filter guard;
  - height setting via _mode/_offset (never _value), validated-before-mutate;
  - generation LAUNCH-and-return: the handler never pumps/waits on the future - it registers the
    future in cam_generate._GENERATIONS (keeps it alive) and the note teaches the
    cam_get_status(target=...) poll;
  - the guards (bad selection, no CAM, missing/ambiguous op, 0 selections, no faces after filter).

The GeometryHandleList input kind has its own tests; here we patch the tool's resolve seam to hand
back fake entities so we exercise the handler, not the resolver.
"""

import json

import pytest

from conftest import load_tool, make_cam
from conftest import FakeSetup as SharedSetup, FakeOperation as SharedOp

cg = load_tool("cam_select_geometry")


@pytest.fixture(autouse=True)
def _restore_resolver():
    """The tests patch _inputs.GeometryHandleList.resolve (a shared class method). Restore it after
    each test so the patch doesn't leak into other modules' tests."""
    orig = cg._inputs.GeometryHandleList.resolve
    yield
    cg._inputs.GeometryHandleList.resolve = orig


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


def _install(monkeypatch, cam, entities):
    monkeypatch.setattr(cg, "get_cam", lambda: (cam, None))
    # patch the geometry-handle resolver to hand back fake entities (resolver has its own tests)
    cg._inputs.GeometryHandleList.resolve = lambda self, raw: (entities, None)
    return cam


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

    def test_pocket_uses_pocket_builder_and_ignores_chain_knobs(self, monkeypatch):
        op = _curve_op(name="2D Pocket1")
        cam = _CAM([_Setup([op])])
        _install(monkeypatch, cam, [_Face()])
        cg.handler(operation="2D Pocket1", selection="pocket", handles=["f"], is_open=True,
                   generate=False)
        sel = op.parameters.itemByName("contours").value.getCurveSelections().item(0)
        assert sel.kind == "pocket"
        assert sel.isOpen is None        # chain-only knob NOT applied to a pocket selection

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
