"""Tests for `model_inspect` — the measurement rich read (bbox default + include=['mass']; mesh routing).

Same fixture pattern as test_design_get/test_cam_get: stub the slice SEAMS + the TargetRef resolution,
assert the ROUTER's job — default = bbox, include=['mass'] adds mass, a MESH target routes to mesh stats,
the kind tag is surfaced, unknown include + target errors guard. The slice→handler delegation is proven
by live validation.
"""

import json

import pytest

from conftest import load_tool, error_message

mi = load_tool("model_inspect")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _resolve_to(monkeypatch, kind):
    """Make TargetRef resolve to (a dummy entity, `kind`) and a design exist."""
    monkeypatch.setattr(mi._common, "design", lambda: object())
    monkeypatch.setattr(mi._TARGET, "resolve", lambda raw: ((object(), kind), None))


def _ok(payload):
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}


@pytest.fixture
def stub_slices(monkeypatch):
    """Stub the inline measure CORES (_bbox / _physical_properties) + the mesh core (imported lazily
    from mesh_ops). The router's job — dispatch + compose — is what these tests pin; the cores' own
    numbers are covered by live validation."""
    import sys
    monkeypatch.setattr(mi, "_bbox", lambda design, ent, desc, frame, units: _ok({"x": 10, "y": 5, "z": 2}))
    monkeypatch.setattr(mi, "_physical_properties",
                        lambda design, ent, desc, units, accuracy, per_body: _ok({"mass_kg": 1.5}))
    monkeypatch.setitem(sys.modules, "mcpServer.tools.mesh_ops",
        type("Mesh", (), {"mesh_measure_of_body": staticmethod(
            lambda mb, units: _ok({"triangle_count": 900, "is_closed": True}))}))


class TestDefaultAndDispatch:
    def test_solid_default_is_bbox(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        out = _payload(mi.handler(target="Body1"))
        assert out["x"] == 10 and out["kind"] == "body"
        assert "mass" not in out                         # mass is opt-in
        assert "include=" in out["note"]                 # advertises mass

    def test_design_default_is_bbox(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "design")
        out = _payload(mi.handler(target=""))
        assert out["kind"] == "design" and "x" in out

    def test_include_mass_adds_properties(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        out = _payload(mi.handler(target="Body1", include=["mass"]))
        assert out["mass"]["mass_kg"] == 1.5

    def test_mesh_target_routes_to_mesh_stats(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "mesh")
        out = _payload(mi.handler(target="Mesh1"))
        assert out["triangle_count"] == 900 and out["kind"] == "mesh"
        assert "x" not in out                            # NOT the bbox path
        assert "mesh" in out["note"].lower()             # breadcrumb explains the mesh path

    def test_mesh_is_not_a_valid_include(self, monkeypatch, stub_slices):
        # mesh stats are automatic for a mesh target, NOT a selectable slice — advertising it would lie.
        _resolve_to(monkeypatch, "body")
        res = mi.handler(target="Body1", include=["mesh"])
        assert res["isError"] and "mesh" in error_message(res).lower()


class TestGuards:
    def test_unresolvable_target_errors(self, monkeypatch):
        monkeypatch.setattr(mi._common, "design", lambda: object())
        monkeypatch.setattr(mi._TARGET, "resolve", lambda raw: (None, "no such target 'Ghost'"))
        res = mi.handler(target="Ghost")
        assert "ghost" in error_message(res).lower()

    def test_unknown_include_errors(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        res = mi.handler(target="Body1", include=["bogus"])
        assert "bogus" in error_message(res).lower() or "unknown" in error_message(res).lower()


class TestNormalizeInclude:
    def test_comma_string(self):
        assert mi._normalize_include("mass") == ["mass"]

    def test_none_empty(self):
        assert mi._normalize_include(None) == [] and mi._normalize_include("") == []
