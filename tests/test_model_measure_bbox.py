"""Unit tests for the ``model_measure_bbox`` MCP tool.

Each test targets a *specific, plausible bug* in the tool's pure logic — not
"does the function exist." The Fusion API itself is mocked (see conftest); what
we exercise is the tool's own decisions: unit conversion, target resolution,
the "which body do I measure" fallback, and the error/result contract every MCP
tool must honour.

Coordinates fed to the fakes are in centimetres (Fusion's internal unit); the
tool converts to the requested output unit.
"""

from conftest import (
    BRepBody, Component, FakeBoundingBox3D, FakePoint, FakeVector3D,
    _NamedCollection, load_tool,
)

mbb = load_tool("model_measure_bbox")


# ── unit conversion (a real math bug surface) ──────────────────────────────

class TestUnitConversion:
    def test_world_extents_in_mm(self, bbox):
        body = BRepBody("Plate", bbox((0, 0, 0), (5, 3, 1)))  # 5×3×1 cm
        result = _measure_entity(mbb, body, units="mm")
        assert result["isError"] is False
        payload = _payload(result)
        # 5 cm -> 50 mm, 3 cm -> 30 mm, 1 cm -> 10 mm
        assert payload["x"] == 50.0
        assert payload["y"] == 30.0
        assert payload["z"] == 10.0

    def test_world_extents_in_inches(self, bbox):
        body = BRepBody("Plate", bbox((0, 0, 0), (2.54, 5.08, 0)))  # cm
        result = _measure_entity(mbb, body, units="in")
        payload = _payload(result)
        assert payload["x"] == 1.0     # 2.54 cm -> 1 in
        assert payload["y"] == 2.0     # 5.08 cm -> 2 in
        assert payload["z"] == 0.0

    def test_unknown_units_is_an_error_not_a_crash(self):
        result = mbb.handler(target="", units="furlongs")
        # Bad input must come back as a structured error, never an exception.
        assert result["isError"] is True
        assert "furlongs" in result["message"]


# ── world-aligned center / min / max math ──────────────────────────────────

class TestWorldBoxMath:
    def test_center_is_midpoint_of_offset_box(self, bbox):
        # box from (2,4,6) to (8,10,12) cm -> extents 6,6,6; center 5,7,9 cm -> mm
        body = BRepBody("Off", bbox((2, 4, 6), (8, 10, 12)))
        p = _payload(_measure_entity(mbb, body, units="cm"))
        assert p["x"] == 6.0 and p["y"] == 6.0 and p["z"] == 6.0
        assert p["center"] == {"x": 5.0, "y": 7.0, "z": 9.0}

    def test_min_max_points_scaled(self, bbox):
        body = BRepBody("Off", bbox((1, 2, 3), (4, 5, 6)))   # cm
        p = _payload(_measure_entity(mbb, body, units="mm"))
        assert p["min_point"] == {"x": 10.0, "y": 20.0, "z": 30.0}
        assert p["max_point"] == {"x": 40.0, "y": 50.0, "z": 60.0}

    def test_oriented_false_for_world(self, bbox):
        body = BRepBody("B", bbox((0, 0, 0), (1, 1, 1)))
        p = _payload(_measure_entity(mbb, body))
        assert p["oriented"] is False
        assert "world" in p["frame"].lower()

    def test_no_bounding_box_errors(self):
        body = BRepBody("Empty", bbox=None)
        res = _measure_entity(mbb, body)
        assert res["isError"] is True and "No bounding box" in res["message"]

    def test_bbox_with_none_points_errors(self):
        bb = FakeBoundingBox3D(None, None)
        body = BRepBody("B", bb)
        res = _measure_entity(mbb, body)
        assert res["isError"] is True and "no min/max" in res["message"]


# ── _measurable_geometry: cover sizes 0, 1, N ──────────────────────────────

class TestMeasurableGeometry:
    def test_zero_bodies_returns_none(self):
        comp = Component("Empty", bodies=())
        geom, note = mbb._measurable_geometry(comp)
        assert geom is None

    def test_single_body_is_used_directly(self, bbox):
        only = BRepBody("OnlyBody", bbox((0, 0, 0), (1, 1, 1)))
        comp = Component("OneBody", bodies=(only,))
        geom, note = mbb._measurable_geometry(comp)
        assert geom is only
        assert "OnlyBody" in note

    def test_picks_largest_body_by_volume(self, bbox):
        small = BRepBody("Small", bbox((0, 0, 0), (1, 1, 1)))      # vol 1
        big = BRepBody("Big", bbox((0, 0, 0), (10, 10, 10)))       # vol 1000
        comp = Component("Multi", bodies=(small, big))
        geom, note = mbb._measurable_geometry(comp)
        assert geom is big
        assert "Big" in note

    def test_brep_body_passed_through_unchanged(self, bbox):
        body = BRepBody("Direct", bbox((0, 0, 0), (2, 2, 2)))
        geom, note = mbb._measurable_geometry(body)
        assert geom is body
        assert note == ""

    def test_body_without_bbox_skipped_in_volume_scan(self, bbox):
        # 'NoBox' has no boundingBox so it can't be measured; the scan must skip it
        # and still pick the real largest body, not crash.
        nobox = BRepBody("NoBox", bbox=None)
        big = BRepBody("Big", bbox((0, 0, 0), (10, 10, 10)))
        comp = Component("Multi", bodies=(nobox, big))
        geom, note = mbb._measurable_geometry(comp)
        assert geom is big and "Big" in note

    def test_all_bodies_without_bbox_falls_back_to_first(self):
        a = BRepBody("First", bbox=None)
        b = BRepBody("Second", bbox=None)
        comp = Component("Multi", bodies=(a, b))
        geom, note = mbb._measurable_geometry(comp)
        # no measurable volume anywhere -> falls back to bodies.item(0)
        assert geom is a

    def test_note_warns_when_picking_one_of_many(self, bbox):
        small = BRepBody("Small", bbox((0, 0, 0), (1, 1, 1)))
        big = BRepBody("Big", bbox((0, 0, 0), (3, 3, 3)))
        comp = Component("Multi", bodies=(small, big))
        _, note = mbb._measurable_geometry(comp)
        assert "largest of 2 bodies" in note


# ── _resolve_target: empty / occurrence / body / not-found ─────────────────

class _FakeRoot:
    def __init__(self, occurrences=(), bodies=(), all_occ=None):
        self.occurrences = _NamedCollection(occurrences)
        self.bRepBodies = _NamedCollection(bodies)
        self.allOccurrences = list(all_occ if all_occ is not None else occurrences)


class _FakeOcc:
    def __init__(self, name, full=None, bodies=()):
        self.name = name
        self.fullPathName = full or name
        self.bRepBodies = _NamedCollection(bodies)


class _FakeDesign:
    def __init__(self, root):
        self.rootComponent = root


class TestResolveTarget:
    def test_empty_target_is_root_component(self):
        root = _FakeRoot()
        ent, desc = mbb._resolve_target(_FakeDesign(root), "")
        assert ent is root
        assert "whole design" in desc

    def test_occurrence_by_name(self):
        occ = _FakeOcc("Arm:1")
        root = _FakeRoot(occurrences=(occ,))
        ent, desc = mbb._resolve_target(_FakeDesign(root), "Arm:1")
        assert ent is occ and "occurrence 'Arm:1'" in desc

    def test_body_by_name_in_root(self, bbox):
        body = BRepBody("Plate", bbox((0, 0, 0), (1, 1, 1)))
        root = _FakeRoot(bodies=(body,))
        ent, desc = mbb._resolve_target(_FakeDesign(root), "Plate")
        assert ent is body and "body 'Plate'" in desc

    def test_body_by_name_inside_occurrence(self, bbox):
        body = BRepBody("Hidden", bbox((0, 0, 0), (1, 1, 1)))
        occ = _FakeOcc("Sub:1", bodies=(body,))
        root = _FakeRoot(occurrences=(), bodies=(), all_occ=(occ,))
        ent, desc = mbb._resolve_target(_FakeDesign(root), "Hidden")
        assert ent is body and "Sub:1" in desc

    def test_not_found_returns_none(self):
        root = _FakeRoot()
        ent, desc = mbb._resolve_target(_FakeDesign(root), "Ghost")
        assert ent is None and desc is None

    def test_not_found_target_is_an_error(self):
        # via handler: an unresolvable name comes back as a structured error
        root = _FakeRoot()
        orig = mbb._common.design
        mbb._common.design = lambda: _FakeDesign(root)
        try:
            res = mbb.handler(target="Ghost")
        finally:
            mbb._common.design = orig
        assert res["isError"] is True and "Ghost" in res["message"]


# ── oriented (part-space / frame) measurement ──────────────────────────────

class _FakeJointOrigin:
    def __init__(self, name, x, y, z):
        self.name = name
        self.secondaryAxisVector = x   # X
        self.thirdAxisVector = y       # Y
        self.primaryAxisVector = z     # Z


class _FakeOBB:
    def __init__(self, length, width, height, center):
        self.length = length
        self.width = width
        self.height = height
        self.centerPoint = center


def _install_oriented(jo=None, obb=None, has_mgr=True):
    """Wire up a design whose root has a named joint origin + a measureManager.getOrientedBoundingBox."""
    body = BRepBody("Part", FakeBoundingBox3D(FakePoint(0, 0, 0), FakePoint(1, 1, 1)))
    root = _FakeRoot(bodies=(body,))
    root.jointOrigins = _NamedCollection((jo,) if jo else ())
    design = _FakeDesign(root)
    design.allComponents = []

    class _Mgr:
        def __init__(self):
            self.called_with = None
        def getOrientedBoundingBox(self, geom, xv, yv):
            self.called_with = (geom, xv, yv)
            return obb
    mgr = _Mgr() if has_mgr else None
    mbb.app = type("A", (), {"measureManager": mgr})()
    mbb._common.design = lambda: design
    return design, body, mgr


class TestOrientedMeasurement:
    def setup_method(self):
        self._orig_app = mbb.app
        self._orig_design = mbb._common.design

    def teardown_method(self):
        mbb.app = self._orig_app
        mbb._common.design = self._orig_design

    def test_xyz_map_length_width_height_in_frame(self):
        jo = _FakeJointOrigin("CMS", FakeVector3D(1, 0, 0), FakeVector3D(0, 1, 0), FakeVector3D(0, 0, 1))
        obb = _FakeOBB(5.0, 3.0, 2.0, FakePoint(1, 1, 1))   # cm
        _install_oriented(jo=jo, obb=obb)
        res = mbb.handler(target="Part", frame="CMS", units="mm")
        p = _payload(res)
        assert p["oriented"] is True
        # length->x, width->y, height->z, cm->mm
        assert p["x"] == 50.0 and p["y"] == 30.0 and p["z"] == 20.0
        assert p["center"] == {"x": 10.0, "y": 10.0, "z": 10.0}

    def test_frame_axes_reported(self):
        jo = _FakeJointOrigin("F", FakeVector3D(1, 0, 0), FakeVector3D(0, 1, 0), FakeVector3D(0, 0, 1))
        obb = _FakeOBB(1, 1, 1, FakePoint(0, 0, 0))
        _install_oriented(jo=jo, obb=obb)
        p = _payload(mbb.handler(target="Part", frame="F"))
        assert p["frame_axes"]["x_axis"] == [1.0, 0.0, 0.0]
        assert p["frame_axes"]["z_axis"] == [0.0, 0.0, 1.0]

    def test_obb_built_from_jo_x_and_y_axes(self):
        xv, yv = FakeVector3D(1, 0, 0), FakeVector3D(0, 1, 0)
        jo = _FakeJointOrigin("F", xv, yv, FakeVector3D(0, 0, 1))
        obb = _FakeOBB(1, 1, 1, FakePoint(0, 0, 0))
        _, body, mgr = _install_oriented(jo=jo, obb=obb)
        mbb.handler(target="Part", frame="F")
        geom, gx, gy = mgr.called_with
        assert geom is body and gx is xv and gy is yv

    def test_missing_frame_is_an_error(self):
        _install_oriented(jo=None, obb=None)
        res = mbb.handler(target="Part", frame="Nope")
        assert res["isError"] is True and "Nope" in res["message"]


# ── error contract shared by every MCP tool ────────────────────────────────

class TestResultContract:
    def test_ok_shape(self):
        out = mbb.ok({"hello": "world"})
        assert out["isError"] is False
        assert out["content"][0]["type"] == "text"
        # payload is JSON-encoded into the text field
        import json
        assert json.loads(out["content"][0]["text"]) == {"hello": "world"}

    def test_error_shape_carries_message(self):
        out = mbb.error("nope")
        assert out["isError"] is True
        assert out["message"] == "nope"
        assert out["content"][0]["text"] == "nope"


# ── helpers ────────────────────────────────────────────────────────────────

def _payload(result):
    import json
    return json.loads(result["content"][0]["text"])


def _measure_entity(mod, entity, units="mm"):
    """Drive handler() against a specific entity by stubbing target resolution.

    handler() resolves a target *name* to an entity via the active design. To
    test the measurement math directly we bypass resolution: temporarily make
    _resolve_target return our entity and _design return a truthy stand-in.
    """
    orig_resolve = mod._resolve_target
    orig_design = mod._common.design
    mod._common.design = lambda: object()  # truthy "design"
    mod._resolve_target = lambda design, target: (entity, "test entity")
    try:
        return mod.handler(target="anything", units=units)
    finally:
        mod._resolve_target = orig_resolve
        mod._common.design = orig_design
