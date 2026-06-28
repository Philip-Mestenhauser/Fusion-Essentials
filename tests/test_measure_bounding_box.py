"""Unit tests for the ``model_measure_bbox`` MCP tool.

Each test targets a *specific, plausible bug* in the tool's pure logic — not
"does the function exist." The Fusion API itself is mocked (see conftest); what
we exercise is the tool's own decisions: unit conversion, target resolution,
the "which body do I measure" fallback, and the error/result contract every MCP
tool must honour.

Coordinates fed to the fakes are in centimetres (Fusion's internal unit); the
tool converts to the requested output unit.
"""

from conftest import BRepBody, Component, load_tool

mbb = load_tool("measure_bounding_box")


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


# ── error contract shared by every MCP tool ────────────────────────────────

class TestResultContract:
    def test_ok_shape(self):
        out = mbb._ok({"hello": "world"})
        assert out["isError"] is False
        assert out["content"][0]["type"] == "text"
        # payload is JSON-encoded into the text field
        import json
        assert json.loads(out["content"][0]["text"]) == {"hello": "world"}

    def test_error_shape_carries_message(self):
        out = mbb._error("nope")
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
    orig_design = mod._design
    mod._design = lambda: object()  # truthy "design"
    mod._resolve_target = lambda design, target: (entity, "test entity")
    try:
        return mod.handler(target="anything", units=units)
    finally:
        mod._resolve_target = orig_resolve
        mod._design = orig_design
