"""Unit tests for ``joint_create_origin.py``.

Targets: ``_kp_name`` (reverse keypoint-enum -> readable name), ``_vec``
(rounding/None), the input-validation branches of ``_geometry_from_args``
(missing ``sketch_name``, out-of-range entity index) that gate geometry
construction before any Fusion call, and ``handler()`` itself - its own
guards (unknown anchor/target/keypoint/units, no active design), the
coordinate-anchor unit scaling, and the created-origin report (frame_axes,
name override).
"""

import json
from types import SimpleNamespace

from conftest import load_tool

jo = load_tool("joint_create_origin")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── _vec: round to 6, None passthrough ─────────────────────────────────────

class TestVec:
    def test_rounds_components(self):
        assert jo._vec(SimpleNamespace(x=1.0000001, y=2.5, z=-3.0)) == [1.0, 2.5, -3.0]

    def test_none_passthrough(self):
        assert jo._vec(None) is None


# ── _kp_name: reverse lookup against the keypoint table ────────────────────

class TestKpName:
    def test_known_value_maps_to_name(self):
        # Pick any registered keypoint and round-trip it through the reverse map.
        name, value = next(iter(jo._KEYPOINTS.items()))
        assert jo._kp_name(value) == name

    def test_unknown_value_falls_back_to_str(self):
        assert jo._kp_name(123456) == "123456"


# ── _geometry_from_args: validation gates ──────────────────────────────────

def _call(anchor="coordinates", target="at", x=0, y=0, z=0,
          sketch_name="", entity_index=0, keypoint=None, design=None, comp=None,
          geometry_handle=None, bbox_target=None, orient_axis="z", flip=False, meta=None):
    # Signature: (design, comp, anchor, target, x_cm, y_cm, z_cm,
    #             sketch_name, entity_index, keypoint, geometry_handle,
    #             bbox_target, orient_axis, flip, meta)
    return jo._geometry_from_args(
        design or SimpleNamespace(), comp or SimpleNamespace(),
        anchor, target, x, y, z, sketch_name, entity_index, keypoint, geometry_handle,
        bbox_target, orient_axis, flip, meta,
    )


class TestGeometryFromArgsValidation:
    def test_sketch_line_without_sketch_name_errors(self):
        g, desc, err = _call(anchor="sketch_line", sketch_name="")
        assert g is None
        assert "sketch_name" in err

    def test_sketch_point_without_sketch_name_errors(self):
        g, desc, err = _call(anchor="sketch_point", sketch_name="")
        assert g is None
        assert "sketch_name" in err

    def test_sketch_line_missing_sketch_errors(self, monkeypatch):
        # sketch_name given, but no such sketch exists -> clear "no sketch named" error.
        monkeypatch.setattr(jo, "_find_sketch", lambda design, name: None)
        g, desc, err = _call(anchor="sketch_line", sketch_name="Ghost")
        assert g is None
        assert "Ghost" in err

    def test_sketch_line_index_out_of_range_errors(self, monkeypatch):
        # A sketch exists with 1 line; asking for index 5 must be rejected.
        one_line = SimpleNamespace(
            sketchCurves=SimpleNamespace(
                sketchLines=SimpleNamespace(count=1)
            )
        )
        monkeypatch.setattr(jo, "_find_sketch", lambda design, name: one_line)
        g, desc, err = _call(anchor="sketch_line", sketch_name="S", entity_index=5)
        assert g is None
        assert "out of range" in err


# ── anchor='geometry': BRep face/edge/vertex handle (geometry-as-values) ────────────────────────

class _FakeFace:
    def __init__(self, planar):
        import adsk.core
        st = adsk.core.SurfaceTypes
        stype = st.PlaneSurfaceType if planar else st.CylinderSurfaceType
        self.geometry = type("G", (), {"surfaceType": stype})()


class _FakeEdge:
    pass


class _FakeVertex:
    pass


def _install_geom(handle_map):
    """Wire adsk types + a design whose findEntityByToken resolves the geometry handles."""
    import adsk.core, adsk.fusion
    adsk.fusion.BRepFace = _FakeFace
    adsk.fusion.BRepEdge = _FakeEdge
    adsk.fusion.BRepVertex = _FakeVertex
    adsk.fusion.ConstructionPoint = type("CP", (), {})
    adsk.fusion.SketchPoint = type("SP", (), {})
    # JointGeometry factory records which create* was used
    calls = {}
    class JG:
        @staticmethod
        def createByPlanarFace(face, edge, kp):
            calls["kind"] = "planar_face"; return ("g", "planar")
        @staticmethod
        def createByNonPlanarFace(face, kp):
            calls["kind"] = "non_planar_face"; return ("g", "nonplanar")
        @staticmethod
        def createByCurve(edge, kp):
            calls["kind"] = "curve"; return ("g", "curve")
        @staticmethod
        def createByPoint(v):
            calls["kind"] = "point"; return ("g", "point")
    adsk.fusion.JointGeometry = JG
    class _D:
        def findEntityByToken(self, t):
            e = handle_map.get(t)
            return [e] if e is not None else []
    jo._inputs._common.design = lambda: _D()
    return calls


class TestGeometryAnchor:
    def test_planar_face_uses_createByPlanarFace(self):
        calls = _install_geom({"F": _FakeFace(planar=True)})
        g, desc, err = _call(anchor="geometry", geometry_handle="F")
        assert err is None and g is not None
        assert calls["kind"] == "planar_face"
        assert "normal" in desc

    def test_cylinder_face_uses_nonplanar(self):
        calls = _install_geom({"C": _FakeFace(planar=False)})
        g, desc, err = _call(anchor="geometry", geometry_handle="C")
        assert err is None and calls["kind"] == "non_planar_face"

    def test_edge_uses_createByCurve(self):
        calls = _install_geom({"E": _FakeEdge()})
        g, desc, err = _call(anchor="geometry", geometry_handle="E", keypoint=1)
        assert err is None and calls["kind"] == "curve"
        assert "edge" in desc.lower()

    def test_vertex_uses_createByPoint(self):
        calls = _install_geom({"V": _FakeVertex()})
        g, desc, err = _call(anchor="geometry", geometry_handle="V")
        assert err is None and calls["kind"] == "point"

    def test_bad_geometry_handle_errors(self):
        _install_geom({})   # nothing resolves
        g, desc, err = _call(anchor="geometry", geometry_handle="missing")
        assert g is None and err is not None


# ── handler(): guards, coordinate-anchor scaling, and the created-origin report ─────────────────

class _FakeSketchPoints:
    def add(self, pt):
        return SimpleNamespace(point=pt)


class _FakeSketch:
    def __init__(self):
        self.name = None
        self.sketchPoints = _FakeSketchPoints()


class _FakeSketches:
    def add(self, plane):
        return _FakeSketch()


class _FakeJointOriginInput:
    def __init__(self):
        self.primaryAxisVector = SimpleNamespace(x=0.0, y=0.0, z=1.0)
        self.secondaryAxisVector = SimpleNamespace(x=1.0, y=0.0, z=0.0)
        self.thirdAxisVector = SimpleNamespace(x=0.0, y=1.0, z=0.0)
        self.offsetX = self.offsetY = self.offsetZ = None   # set by the handler for anchor=coordinates


class _FakeJointOrigin:
    def __init__(self):
        self.name = "JointOrigin1"
        # offsetX/Y/Z ModelParameters (each carries .value in cm + a dNN .name) - populated by add()
        # from the input.
        self.offsetX = SimpleNamespace(value=0.0, name="d5")
        self.offsetY = SimpleNamespace(value=0.0, name="d6")
        self.offsetZ = SimpleNamespace(value=0.0, name="d7")


class _FakeJointOrigins:
    def __init__(self):
        self.count = 0

    def createInput(self, geom):
        return _FakeJointOriginInput()

    def add(self, jo_input):
        self.count += 1
        origin = _FakeJointOrigin()
        # Model the offset parameters: the created JO reports back whatever offsets the input carried
        # (createByReal wraps the cm value as ._real). This is what the honesty read-back verifies.
        for ax, dnn in (("offsetX", "d5"), ("offsetY", "d6"), ("offsetZ", "d7")):
            vi = getattr(jo_input, ax, None)
            if vi is not None:
                setattr(origin, ax, SimpleNamespace(value=getattr(vi, "_real", 0.0), name=dnn))
        return origin


class _FakeComp:
    def __init__(self):
        self.name = "Comp1"
        self.sketches = _FakeSketches()
        self.xYConstructionPlane = object()
        self.originConstructionPoint = object()     # the stable anchor for anchor=coordinates
        self.jointOrigins = _FakeJointOrigins()


class _FakeDesign:
    def __init__(self):
        self.rootComponent = _FakeComp()


def _install_handler(monkeypatch, design=None):
    """Wire a fake design + the adsk seams _geometry_from_args/handler touch for anchor='coordinates'.
    Returns (design, point3d_calls) so a test can assert the exact cm values Point3D.create received.
    """
    d = design if design is not None else _FakeDesign()
    monkeypatch.setattr(jo._common, "design", lambda: d)
    import adsk.core
    import adsk.fusion
    calls = []

    def _create(x, y, z):
        calls.append((x, y, z))
        return SimpleNamespace(x=x, y=y, z=z)

    monkeypatch.setattr(adsk.core.Point3D, "create", staticmethod(_create))
    monkeypatch.setattr(adsk.fusion.JointGeometry, "createByPoint",
                        staticmethod(lambda pt: SimpleNamespace(anchor_point=pt)))
    # createByReal wraps a cm value; the coordinate-anchor path uses it for offsetX/Y/Z.
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal",
                        staticmethod(lambda v: SimpleNamespace(_real=v)))
    return d, calls


class TestHandlerGuards:
    def test_no_active_design_errors(self, monkeypatch):
        monkeypatch.setattr(jo._common, "design", lambda: None)
        res = jo.handler()
        assert res["isError"] is True and "design" in res["message"].lower()

    def test_unknown_anchor_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(anchor="wormhole")
        assert res["isError"] is True and "wormhole" in res["message"]

    def test_unknown_target_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(target="mars")
        assert res["isError"] is True and "mars" in res["message"]

    def test_unknown_keypoint_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(keypoint="nowhere")
        assert res["isError"] is True and "nowhere" in res["message"]

    def test_unknown_units_errors(self, monkeypatch):
        _install_handler(monkeypatch)
        res = jo.handler(units="furlong")
        assert res["isError"] is True and "furlong" in res["message"]


class TestHandlerCoordinateAnchor:
    # anchor='coordinates' anchors on the MODEL ORIGIN and holds the
    # position in REAL parametric offsetX/Y/Z - not an undimensioned point floating in a hidden sketch
    # (which read plausibly while spawning 0.00mm parameters). The reported location is the read-back
    # offsets, so it is verifiable and recompute-robust.

    def test_coordinates_held_in_parametric_offsets_not_a_floating_point(self, monkeypatch):
        _, calls = _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates", target="at", x=10, y=0, z=0, units="mm"))
        # NO floating sketch point is created for the anchor (that path is gone).
        assert calls == []
        # location is the READ-BACK offset parameters: 10 mm on X (1.0 cm internal, reported in mm).
        assert out["location"] == {"x": 10.0, "y": 0.0, "z": 0.0, "units": "mm"}
        assert out["held_by"] == "parametric offsetX/Y/Z from the model origin"
        assert out["offset_parameters"] == {"x": 10.0, "y": 0.0, "z": 0.0, "units": "mm"}

    def test_target_origin_reports_zero_offsets(self, monkeypatch):
        _, calls = _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates", target="origin",
                                  x=99, y=99, z=99, units="mm"))
        # target=origin ignores x/y/z; the offsets are a genuine 0 (the JO IS at the origin), not fake.
        assert out["location"] == {"x": 0.0, "y": 0.0, "z": 0.0, "units": "mm"}
        assert out["offset_parameters"] == {"x": 0.0, "y": 0.0, "z": 0.0, "units": "mm"}

    def test_mismatched_offset_readback_errors_and_rolls_back(self, monkeypatch):
        # If the offsets don't stick (the JO reports a different position than asked), that is a
        # mislocated origin - a hard error with a rollback, never a false success.
        d, _ = _install_handler(monkeypatch)
        orig_add = d.rootComponent.jointOrigins.add
        rolled = {"back": False}

        def _bad_add(jo_input):
            origin = orig_add(jo_input)
            origin.offsetX = SimpleNamespace(value=9.9)      # asked 1.0 cm, reports 9.9 cm
            origin.deleteMe = lambda: rolled.__setitem__("back", True) or True
            return origin
        monkeypatch.setattr(d.rootComponent.jointOrigins, "add", _bad_add)
        res = jo.handler(anchor="coordinates", target="at", x=10, y=0, z=0, units="mm")
        assert res["isError"] is True and "did not take" in res["message"]
        assert rolled["back"] is True                        # the mislocated origin was rolled back

    def test_creates_joint_origin_and_reports_frame_axes(self, monkeypatch):
        d, _ = _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates"))
        assert out["created"] is True
        assert out["joint_origin_name"] == "JointOrigin1"
        assert out["frame_axes"]["primary_axis_Z"] == [0.0, 0.0, 1.0]
        assert d.rootComponent.jointOrigins.count == 1

    def test_custom_name_is_applied_to_the_new_joint_origin(self, monkeypatch):
        _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates", name="Anchor1"))
        assert out["joint_origin_name"] == "Anchor1"

    def test_reports_dnn_names_for_the_offset_parameters(self, monkeypatch):
        # the dNN names are what param_set needs to drive the frame with an expression (creation
        # accepts numeric offsets only) - values alone leave the agent fishing through param_get
        _install_handler(monkeypatch)
        out = _payload(jo.handler(anchor="coordinates", target="at", x=10, y=0, z=0, units="mm"))
        assert out["model_parameters"] == {"offset_x": "d5", "offset_y": "d6", "offset_z": "d7"}
        assert "param_set" in out["note"]


# ── anchor='bbox_center': computed center + oriented Z (geometry-building level) ─────────────────

class _FakeBBox:
    def __init__(self, mn, mx):
        self.minPoint = SimpleNamespace(x=mn[0], y=mn[1], z=mn[2])
        self.maxPoint = SimpleNamespace(x=mx[0], y=mx[1], z=mx[2])


class _FakeBody:
    def __init__(self, mn, mx, name="Body1"):
        self.boundingBox = _FakeBBox(mn, mx)
        self.name = name


class _CapLines:
    def __init__(self, store):
        self.store = store

    def addByTwoPoints(self, p1, p2):
        self.store.append((p1, p2))
        return SimpleNamespace(kind="line")


class _CapSketch:
    def __init__(self, store):
        self.name = None
        self.isVisible = True
        self.sketchCurves = SimpleNamespace(sketchLines=_CapLines(store))


class _CapSketches:
    def __init__(self, store):
        self.store = store

    def add(self, plane):
        return _CapSketch(self.store)


def _cap_comp(store):
    return SimpleNamespace(name="Comp1", sketches=_CapSketches(store), xYConstructionPlane=object())


def _install_bbox_target(monkeypatch, body):
    """Wire the adsk types + a design whose findEntityByToken resolves the bbox_target handle to `body`,
    and record every Point3D.create call so a test can read the orientation line's endpoints."""
    import adsk.core
    import adsk.fusion
    # Distinct class identities so TargetRef classifies `body` as a BODY (not a face/mesh).
    monkeypatch.setattr(adsk.fusion, "BRepFace", type("F", (), {}), raising=False)
    monkeypatch.setattr(adsk.fusion, "MeshBody", type("M", (), {}), raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepBody", _FakeBody, raising=False)

    class _D:
        def findEntityByToken(self, t):
            return [body]

    d = _D()
    monkeypatch.setattr(jo._inputs._common, "design", lambda: d)
    monkeypatch.setattr(jo._common, "design", lambda: d)
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        staticmethod(lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)))
    monkeypatch.setattr(adsk.fusion.JointGeometry, "createByCurve",
                        staticmethod(lambda curve, kp: SimpleNamespace(curve=curve, kp=kp)))
    monkeypatch.setattr(adsk.fusion.JointKeyPointTypes, "StartKeyPoint", 0, raising=False)


class TestBboxCenterGeometry:
    def test_center_is_the_bbox_midpoint(self, monkeypatch):
        body = _FakeBody((0, 0, 0), (10, 20, 30))
        _install_bbox_target(monkeypatch, body)
        store, meta = [], {}
        g, desc, err = _call(anchor="bbox_center", comp=_cap_comp(store),
                             bbox_target="BODYH", orient_axis="z", meta=meta)
        assert err is None and g is not None
        # The orientation line STARTS at the bbox center (5,10,15) - that is the frame origin.
        start = store[0][0]
        assert (start.x, start.y, start.z) == (5.0, 10.0, 15.0)
        assert meta["anchor_cm"] == (5.0, 10.0, 15.0)

    def test_z_line_runs_along_requested_world_axis(self, monkeypatch):
        body = _FakeBody((0, 0, 0), (10, 20, 30))
        _install_bbox_target(monkeypatch, body)
        store = []
        _call(anchor="bbox_center", comp=_cap_comp(store), bbox_target="BODYH", orient_axis="x")
        start, end = store[0]
        d = (end.x - start.x, end.y - start.y, end.z - start.z)
        assert d == (1.0, 0.0, 0.0)          # Z aligned to world X

    def test_flip_reverses_the_oriented_axis(self, monkeypatch):
        body = _FakeBody((0, 0, 0), (10, 20, 30))
        _install_bbox_target(monkeypatch, body)
        store = []
        _call(anchor="bbox_center", comp=_cap_comp(store), bbox_target="BODYH",
              orient_axis="z", flip=True)
        start, end = store[0]
        assert (end.x - start.x, end.y - start.y, end.z - start.z) == (0.0, 0.0, -1.0)


# ── orient_axis from a PLANAR-FACE handle: AxisRef now sources a direction from a face ───────────
# Once AxisRef accepts a planar-face handle (returning its NORMAL as a ('world', vec) direction),
# orient_axis takes a face with NO code change in joint_create_origin - the frame's Z aligns to that
# normal. This proves the one-call parity insert-into-template's Phase 2 relies on.

class _FakeOrientFace:
    def __init__(self, normal):
        import adsk.core
        self.geometry = SimpleNamespace(surfaceType=adsk.core.SurfaceTypes.PlaneSurfaceType,
                                        normal=SimpleNamespace(x=normal[0], y=normal[1], z=normal[2]))


def _install_face_orient(monkeypatch, body, face):
    """Wire the adsk types + a design whose findEntityByToken resolves BODYH -> body and FACEH -> face,
    so bbox_target resolves a body and orient_axis (AxisRef) resolves a planar face to its normal.
    Real (non-Mock) BRepEdge/SketchLine classes so AxisRef's isinstance ladder reaches the face branch."""
    import adsk.core
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion, "BRepFace", _FakeOrientFace, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepEdge", type("E", (), {}), raising=False)
    monkeypatch.setattr(adsk.fusion, "SketchLine", type("SL", (), {}), raising=False)
    monkeypatch.setattr(adsk.fusion, "MeshBody", type("M", (), {}), raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepBody", _FakeBody, raising=False)
    handles = {"BODYH": body, "FACEH": face}

    class _D:
        def findEntityByToken(self, t):
            e = handles.get(t)
            return [e] if e is not None else []
    d = _D()
    monkeypatch.setattr(jo._common, "design", lambda: d)
    monkeypatch.setattr(jo._inputs._common, "design", lambda: d)
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        staticmethod(lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)))
    monkeypatch.setattr(adsk.fusion.JointGeometry, "createByCurve",
                        staticmethod(lambda curve, kp: SimpleNamespace(curve=curve, kp=kp)))
    monkeypatch.setattr(adsk.fusion.JointKeyPointTypes, "StartKeyPoint", 0, raising=False)


class TestOrientAxisFromFace:
    def test_planar_face_handle_aligns_z_to_the_face_normal(self, monkeypatch):
        body = _FakeBody((0, 0, 0), (10, 20, 30))
        face = _FakeOrientFace((0, 1, 0))                 # normal along world +Y
        _install_face_orient(monkeypatch, body, face)
        store = []
        g, desc, err = _call(anchor="bbox_center", comp=_cap_comp(store),
                             bbox_target="BODYH", orient_axis="FACEH", meta={})
        assert err is None and g is not None
        start, end = store[0]
        # The orientation line (the frame's Z) runs along the face normal (0,1,0), from the bbox center.
        assert (end.x - start.x, end.y - start.y, end.z - start.z) == (0.0, 1.0, 0.0)
        assert (start.x, start.y, start.z) == (5.0, 10.0, 15.0)

    def test_construction_axis_handle_aligns_z_to_the_datum_direction(self, monkeypatch):
        # A ConstructionAxis's geometry is an InfiniteLine3D - origin/direction, NO start/end points.
        # Reading start/end directly refuses an axis the schema advertises; the shared axis_line_of
        # reads both shapes (and lifts a datum into world space).
        import adsk.fusion
        body = _FakeBody((0, 0, 0), (10, 20, 30))
        datum = SimpleNamespace(name="Spin", component=None, assemblyContext=None,
                                geometry=SimpleNamespace(origin=SimpleNamespace(x=0, y=0, z=0),
                                                         direction=SimpleNamespace(x=0, y=0, z=2)))
        _install_face_orient(monkeypatch, body, _FakeOrientFace((0, 1, 0)))
        monkeypatch.setattr(adsk.fusion, "ConstructionAxis", type(datum), raising=False)
        # ONE design resolving both tokens: the body for bbox_target, the datum for orient_axis.
        handles = {"BODYH": body, "AXISH": datum}
        design = SimpleNamespace(rootComponent=None,
                                 findEntityByToken=lambda t: [handles[t]] if t in handles else [])
        monkeypatch.setattr(jo._common, "design", lambda: design)
        monkeypatch.setattr(jo._inputs._common, "design", lambda: design)
        store = []
        g, desc, err = _call(anchor="bbox_center", comp=_cap_comp(store),
                             bbox_target="BODYH", orient_axis="AXISH", meta={})
        assert err is None and g is not None
        start, end = store[0]
        # the datum's direction, normalized - not a refusal
        assert (end.x - start.x, end.y - start.y, end.z - start.z) == (0.0, 0.0, 1.0)


# ── anchor='face_center': planar face guard + normal orientation ────────────────────────────────

class TestFaceCenterGeometry:
    def test_planar_face_builds_via_planar_factory(self):
        calls = _install_geom({"F": _FakeFace(planar=True)})
        g, desc, err = _call(anchor="face_center", geometry_handle="F")
        assert err is None and g is not None
        assert calls["kind"] == "planar_face"
        assert "normal" in desc

    def test_non_planar_face_is_rejected(self):
        _install_geom({"C": _FakeFace(planar=False)})
        g, desc, err = _call(anchor="face_center", geometry_handle="C")
        assert g is None
        assert "PLANAR" in err

    def test_non_face_handle_is_rejected(self):
        _install_geom({"E": _FakeEdge()})
        g, desc, err = _call(anchor="face_center", geometry_handle="E")
        assert g is None
        assert "not a face" in err


# ── handler(): bbox_center guards + honesty read-back ───────────────────────────────────────────

class _JOInputBbox:
    primaryAxisVector = SimpleNamespace(x=0.0, y=0.0, z=1.0)
    secondaryAxisVector = SimpleNamespace(x=1.0, y=0.0, z=0.0)
    thirdAxisVector = SimpleNamespace(x=0.0, y=1.0, z=0.0)


class _JOWithOrigin:
    def __init__(self, origin_cm):
        self.name = "JointOrigin1"
        self.geometry = SimpleNamespace(
            origin=SimpleNamespace(x=origin_cm[0], y=origin_cm[1], z=origin_cm[2]))
        self.deleted = False

    def deleteMe(self):
        self.deleted = True
        return True


class _JointOriginsBbox:
    def __init__(self, jo_obj):
        self.count = 0
        self._jo = jo_obj

    def createInput(self, geom):
        return _JOInputBbox()

    def add(self, ji):
        self.count += 1
        return self._jo


def _install_bbox_handler(monkeypatch, origin_cm, bbox=((0, 0, 0), (10, 20, 30))):
    """Full handler wiring for anchor='bbox_center': a body at `bbox`, and a created joint origin whose
    geometry.origin reads back at `origin_cm` (cm) so a test can exercise the landing-point check."""
    import adsk.core
    import adsk.fusion
    body = _FakeBody(bbox[0], bbox[1])
    jo_obj = _JOWithOrigin(origin_cm)
    store = []
    comp = SimpleNamespace(name="Comp1", sketches=_CapSketches(store), xYConstructionPlane=object(),
                           jointOrigins=_JointOriginsBbox(jo_obj))
    design = SimpleNamespace(rootComponent=comp)
    design.findEntityByToken = lambda t: [body]
    monkeypatch.setattr(adsk.fusion, "BRepFace", type("F", (), {}), raising=False)
    monkeypatch.setattr(adsk.fusion, "MeshBody", type("M", (), {}), raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepBody", _FakeBody, raising=False)
    monkeypatch.setattr(jo._common, "design", lambda: design)
    monkeypatch.setattr(jo._inputs._common, "design", lambda: design)
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        staticmethod(lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)))
    monkeypatch.setattr(adsk.fusion.JointGeometry, "createByCurve",
                        staticmethod(lambda curve, kp: SimpleNamespace(curve=curve, kp=kp)))
    monkeypatch.setattr(adsk.fusion.JointKeyPointTypes, "StartKeyPoint", 0, raising=False)
    return jo_obj


class TestBboxCenterHandler:
    def test_reports_computed_anchor_and_readback_in_display_units(self, monkeypatch):
        # JO lands on the computed center (5,10,15) cm -> 50,100,150 mm.
        _install_bbox_handler(monkeypatch, origin_cm=(5.0, 10.0, 15.0))
        out = _payload(jo.handler(anchor="bbox_center", bbox_target="BODYH",
                                  orient_axis="z", units="mm"))
        assert out["computed_anchor"]["x"] == 50.0
        assert out["computed_anchor"]["y"] == 100.0
        assert out["computed_anchor"]["z"] == 150.0
        assert out["origin_readback"]["z"] == 150.0
        assert out["frame_axes"]["primary_axis_Z"] == [0.0, 0.0, 1.0]

    def test_wrong_landing_point_errors_and_rolls_back(self, monkeypatch):
        # JO lands far from the computed center -> honesty failure: error + deleteMe().
        jo_obj = _install_bbox_handler(monkeypatch, origin_cm=(99.0, 99.0, 99.0))
        res = jo.handler(anchor="bbox_center", bbox_target="BODYH", units="mm")
        assert res["isError"] is True
        assert "computed anchor" in res["message"]
        assert jo_obj.deleted is True

    def test_missing_target_errors(self, monkeypatch):
        _install_bbox_handler(monkeypatch, origin_cm=(5.0, 10.0, 15.0))
        res = jo.handler(anchor="bbox_center", bbox_target="")
        assert res["isError"] is True
        assert "bbox_target" in res["message"]


class TestBboxCenterAmbiguousTarget:
    def test_ambiguous_name_is_refused(self, monkeypatch):
        occ1 = SimpleNamespace(fullPathName="root+Bolt:1", name="Bolt:1")
        occ2 = SimpleNamespace(fullPathName="root+Bolt:2", name="Bolt:2")
        root = SimpleNamespace(allOccurrences=[occ1, occ2])
        design = SimpleNamespace(rootComponent=root, findEntityByToken=lambda t: [])
        monkeypatch.setattr(jo._common, "design", lambda: design)
        monkeypatch.setattr(jo._inputs._common, "design", lambda: design)
        res = jo.handler(anchor="bbox_center", bbox_target="Bolt")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
