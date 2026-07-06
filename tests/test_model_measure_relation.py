"""Tests for `model_measure_relation` - named geometric predicates over two entities.

This tool is mostly PURE MATH (angle between directions, offset between axis lines, min-distance
thresholding), so it is tested hard: per relation a passing case, a failing case, and the boundary
exactly at tolerance. The reason-to-exist case - parallel-but-OFFSET axes must FAIL 'coaxial' - is
pinned explicitly. Entity RESOLUTION is TargetRef's job (tested elsewhere); here the two resolve seams
+ the measureManager are stubbed, and fake cylinder/planar-face geometry drives the extraction+math.
"""

import json
import math

import pytest

from conftest import load_tool, error_message

mr = load_tool("model_measure_relation")

_REAL_A, _REAL_B = mr._A.resolve, mr._B.resolve


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    """Stub the design seam; restore the entity resolvers after each test."""
    monkeypatch.setattr(mr._common, "design", lambda: object())
    yield
    mr._A.resolve, mr._B.resolve = _REAL_A, _REAL_B


# ── fake geometry ─────────────────────────────────────────────────────────────

class _P:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z


def _cyl(origin, axis):
    # surfaceType read at CREATION time (not module import) - the shared mock's enum attr is
    # reassigned by other test files, so a value captured at import can go stale under random order.
    st = mr.adsk.core.SurfaceTypes.CylinderSurfaceType
    return type("Cyl", (), {"origin": _P(*origin), "axis": _P(*axis),
                            "radius": 1.0, "surfaceType": st})()


def _plane(origin, normal):
    st = mr.adsk.core.SurfaceTypes.PlaneSurfaceType
    return type("Pl", (), {"origin": _P(*origin), "normal": _P(*normal),
                           "surfaceType": st})()


def _face(geom, name="F"):
    return type("Face", (), {"geometry": geom, "name": name})()


def _resolve_ab(ent_a, kind_a, ent_b, kind_b):
    mr._A.resolve = lambda raw: ((ent_a, kind_a), None)
    mr._B.resolve = lambda raw: ((ent_b, kind_b), None)


def _faces(ga, gb):
    _resolve_ab(_face(ga), "face", _face(gb), "face")


def _dir_at(theta_deg):
    """A unit direction at `theta_deg` from world +Z, in the X-Z plane."""
    t = math.radians(theta_deg)
    return (math.sin(t), 0.0, math.cos(t))


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _MR:
    def __init__(self, value, p1=None, p2=None):
        self.value = value
        self.positionOne, self.positionTwo = p1, p2


def _install_mgr(monkeypatch, result, raises=False):
    class _Mgr:
        def measureMinimumDistance(self, a, b):
            if raises:
                raise RuntimeError("measurement failed")
            return result
    monkeypatch.setattr(mr.app, "measureManager", _Mgr())


# ── coaxial: the tool's reason to exist ───────────────────────────────────────

class TestCoaxial:
    def test_same_axis_line_passes(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 3), (0, 0, 1)))
        out = _payload(mr.handler(relation="coaxial"))
        assert out["passed"] is True
        assert out["measured"]["angle_deg"] == 0.0
        assert out["measured"]["axis_offset"] == 0.0

    def test_parallel_but_offset_axes_FAIL(self):
        # The classic trap: angle 0 (parallel) but the axis lines are 5 cm apart -> NOT coaxial.
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((5, 0, 0), (0, 0, 1)))
        out = _payload(mr.handler(relation="coaxial"))
        assert out["passed"] is False
        assert out["measured"]["angle_deg"] == 0.0          # parallel...
        assert out["measured"]["axis_offset"] == 50.0       # ...but 50 mm offset
        assert "parallel" in out["note"].lower() and "offset" in out["note"].lower()

    def test_non_parallel_axes_fail(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), (0, 1, 0)))
        out = _payload(mr.handler(relation="coaxial"))
        assert out["passed"] is False
        assert out["measured"]["angle_deg"] == 90.0
        assert "not parallel" in out["note"].lower()

    def test_offset_exactly_at_tolerance_passes(self):
        # offset = 0.5 cm = 5 mm; tolerance 5 mm -> boundary is inclusive (<=).
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0.5, 0, 0), (0, 0, 1)))
        out = _payload(mr.handler(relation="coaxial", tolerance=5, units="mm"))
        assert out["passed"] is True

    def test_offset_just_over_tolerance_fails(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0.5001, 0, 0), (0, 0, 1)))
        out = _payload(mr.handler(relation="coaxial", tolerance=5, units="mm"))
        assert out["passed"] is False

    def test_wrong_kind_planar_faces_refused(self):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        res = mr.handler(relation="coaxial")
        assert res["isError"] is True
        msg = error_message(res).lower()
        assert "coaxial" in msg and "planar face" in msg

    def test_units_invariant_verdict_mm_vs_in(self):
        # Same physical geometry (0.5 cm axis offset). A tolerance of 6 mm and of 0.25 in are both
        # larger than 5 mm, so BOTH must PASS - while the reported offset is in the caller's units.
        # (If 'units' were ignored, 0.25 would read as 0.25 cm < 0.5 cm and the in-case would FAIL.)
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0.5, 0, 0), (0, 0, 1)))
        mm = _payload(mr.handler(relation="coaxial", tolerance=6, units="mm"))
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0.5, 0, 0), (0, 0, 1)))
        inch = _payload(mr.handler(relation="coaxial", tolerance=0.25, units="in"))
        assert mm["passed"] is True and inch["passed"] is True
        assert mm["measured"]["axis_offset"] == 5.0                 # 0.5 cm -> 5 mm
        assert inch["measured"]["axis_offset"] == round(0.5 / 2.54, 4)   # 0.5 cm -> 0.1969 in


# ── parallel ──────────────────────────────────────────────────────────────────

class TestParallel:
    def test_parallel_axes_pass(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((3, 0, 0), (0, 0, 1)))
        out = _payload(mr.handler(relation="parallel"))
        assert out["passed"] is True and out["measured"]["angle_deg"] == 0.0

    def test_anti_parallel_still_parallel(self):
        # A reversed direction (0,0,-1) is the SAME line -> folded angle 0, so parallel PASSES.
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), (0, 0, -1)))
        out = _payload(mr.handler(relation="parallel"))
        assert out["passed"] is True and out["measured"]["angle_deg"] == 0.0

    def test_perpendicular_axes_not_parallel(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), (0, 1, 0)))
        out = _payload(mr.handler(relation="parallel"))
        assert out["passed"] is False

    def test_boundary_at_one_degree(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), _dir_at(1.0)))
        assert _payload(mr.handler(relation="parallel", tolerance_deg=1.0))["passed"] is True
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), _dir_at(1.0)))
        assert _payload(mr.handler(relation="parallel", tolerance_deg=0.9))["passed"] is False

    def test_two_planar_faces_parallel(self):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((9, 9, 9), (0, 0, 1)))
        assert _payload(mr.handler(relation="parallel"))["passed"] is True


# ── perpendicular ───────────────────────────────────────────────────────────────

class TestPerpendicular:
    def test_ninety_degrees_passes(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), (0, 1, 0)))
        out = _payload(mr.handler(relation="perpendicular"))
        assert out["passed"] is True and out["measured"]["deviation_from_90_deg"] == 0.0

    def test_parallel_is_not_perpendicular(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), (0, 0, 1)))
        assert _payload(mr.handler(relation="perpendicular"))["passed"] is False

    def test_boundary_half_degree_off(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), _dir_at(89.5)))
        assert _payload(mr.handler(relation="perpendicular", tolerance_deg=0.5))["passed"] is True
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), _dir_at(89.4)))
        assert _payload(mr.handler(relation="perpendicular", tolerance_deg=0.5))["passed"] is False


# ── flush ────────────────────────────────────────────────────────────────────

class TestFlush:
    def test_coplanar_faces_pass(self):
        # Same normal, origins differ only WITHIN the plane -> zero offset along the normal.
        _faces(_plane((0, 0, 2), (0, 0, 1)), _plane((5, 7, 2), (0, 0, 1)))
        out = _payload(mr.handler(relation="flush"))
        assert out["passed"] is True and out["measured"]["plane_offset"] == 0.0

    def test_parallel_but_stepped_fails(self):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 3), (0, 0, 1)))
        out = _payload(mr.handler(relation="flush"))
        assert out["passed"] is False and "stepped" in out["note"].lower()

    def test_tilted_faces_fail(self):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 0), (0, 1, 1)))
        assert _payload(mr.handler(relation="flush"))["passed"] is False

    def test_offset_boundary(self):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 0.5), (0, 0, 1)))   # 0.5 cm = 5 mm step
        assert _payload(mr.handler(relation="flush", tolerance=5, units="mm"))["passed"] is True
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 0.5001), (0, 0, 1)))
        assert _payload(mr.handler(relation="flush", tolerance=5, units="mm"))["passed"] is False

    def test_cylinder_face_refused(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _plane((0, 0, 0), (0, 0, 1)))
        res = mr.handler(relation="flush")
        assert res["isError"] is True
        assert "planar" in error_message(res).lower()


# ── clearance / touching (measureManager stubbed) ─────────────────────────────

class TestClearance:
    def test_clears_passes(self, monkeypatch):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        _install_mgr(monkeypatch, _MR(8.0, _P(0, 0, 0), _P(0, 0, 8)))
        out = _payload(mr.handler(relation="clearance", tolerance=5, units="mm"))
        assert out["passed"] is True                    # 80 mm >= 5 mm
        assert out["measured"]["min_distance"] == 80.0
        assert out["measured"]["closest_point_on_b"]["z"] == 80.0

    def test_too_close_fails(self, monkeypatch):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        _install_mgr(monkeypatch, _MR(0.02))
        assert _payload(mr.handler(relation="clearance", tolerance=5, units="mm"))["passed"] is False

    def test_boundary_at_required_gap(self, monkeypatch):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        _install_mgr(monkeypatch, _MR(0.5))             # 0.5 cm == 5 mm required -> PASS (>=)
        assert _payload(mr.handler(relation="clearance", tolerance=5, units="mm"))["passed"] is True

    def test_measure_failure_surfaced(self, monkeypatch):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        _install_mgr(monkeypatch, None, raises=True)
        res = mr.handler(relation="clearance")
        assert res["isError"] is True and "failed" in error_message(res).lower()


class TestTouching:
    def test_within_gap_passes(self, monkeypatch):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        _install_mgr(monkeypatch, _MR(0.005))           # 0.05 mm <= 0.1 mm default
        assert _payload(mr.handler(relation="touching"))["passed"] is True

    def test_far_apart_fails(self, monkeypatch):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        _install_mgr(monkeypatch, _MR(5.0))
        assert _payload(mr.handler(relation="touching"))["passed"] is False

    def test_zero_distance_flags_overlap(self, monkeypatch):
        _faces(_plane((0, 0, 0), (0, 0, 1)), _plane((0, 0, 1), (0, 0, 1)))
        _install_mgr(monkeypatch, _MR(0.0))
        out = _payload(mr.handler(relation="touching"))
        assert out["passed"] is True
        assert "assembly_interference" in out["note"]   # does not silently call an overlap "touching"


# ── concentric: two circular entities whose CENTER POINTS coincide (distinct from coaxial) ────────

def _circ_edge(center, arc=False):
    # curveType read at CREATION time (the shared enum mock is reassigned by other files - see _cyl).
    ct = (mr.adsk.core.Curve3DTypes.Arc3DCurveType if arc
          else mr.adsk.core.Curve3DTypes.Circle3DCurveType)
    return type("E", (), {"geometry": type("G", (), {"curveType": ct, "center": _P(*center)})()})()


def _edges(ca, cb, arc_b=False):
    _resolve_ab(_circ_edge(ca), "edge", _circ_edge(cb, arc=arc_b), "edge")


class TestConcentric:
    def test_coincident_centers_pass(self):
        _edges((1, 2, 3), (1, 2, 3))
        out = _payload(mr.handler(relation="concentric"))
        assert out["passed"] is True
        assert out["measured"]["center_distance"] == 0.0
        assert "concentric" in out["note"].lower()

    def test_offset_centers_fail_and_point_at_coaxial(self):
        # 0.5 cm apart, default tol 0.1 mm -> FAIL. The note must distinguish concentric from coaxial.
        _edges((0, 0, 0), (0.5, 0, 0))
        out = _payload(mr.handler(relation="concentric"))
        assert out["passed"] is False
        assert out["measured"]["center_distance"] == 5.0          # 0.5 cm -> 5 mm
        assert "coaxial" in out["note"].lower()

    def test_boundary_exactly_at_tolerance(self):
        _edges((0, 0, 0), (0.5, 0, 0))                            # 5 mm apart
        assert _payload(mr.handler(relation="concentric", tolerance=5, units="mm"))["passed"] is True
        _edges((0, 0, 0), (0.5001, 0, 0))
        assert _payload(mr.handler(relation="concentric", tolerance=5, units="mm"))["passed"] is False

    def test_an_arc_edge_also_supplies_a_center(self):
        _edges((2, 0, 0), (2, 0, 0), arc_b=True)
        assert _payload(mr.handler(relation="concentric"))["passed"] is True

    def test_cylindrical_faces_use_their_axis_base_point(self):
        _resolve_ab(_face(_cyl((0, 0, 0), (0, 0, 1))), "face",
                    _face(_cyl((0, 0, 0), (0, 0, 1))), "face")
        assert _payload(mr.handler(relation="concentric"))["passed"] is True

    def test_straight_edge_is_refused_as_non_circular(self):
        ct = mr.adsk.core.Curve3DTypes.Line3DCurveType
        straight = type("E", (), {"geometry": type("G", (), {"curveType": ct})()})()
        _resolve_ab(straight, "edge", _circ_edge((0, 0, 0)), "edge")
        res = mr.handler(relation="concentric")
        assert res["isError"] is True
        assert "circular" in error_message(res).lower()


# ── guards + contract ─────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_relation_errors(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), (0, 0, 1)))
        res = mr.handler(relation="bogus")
        assert res["isError"] is True and "relation" in error_message(res).lower()

    def test_bad_units_errors(self):
        res = mr.handler(relation="clearance", units="furlong")
        assert res["isError"] is True and "furlong" in error_message(res).lower()

    def test_negative_tolerance_deg_errors(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 0), (0, 0, 1)))
        res = mr.handler(relation="parallel", tolerance_deg=-1)
        assert res["isError"] is True and "tolerance_deg" in error_message(res).lower()

    def test_no_active_design_errors(self, monkeypatch):
        monkeypatch.setattr(mr._common, "design", lambda: None)
        res = mr.handler(relation="coaxial")
        assert res["isError"] is True and "design" in error_message(res).lower()

    def test_unresolvable_entity_surfaced(self):
        mr._A.resolve = lambda raw: (None, "no such target 'Ghost'")
        mr._B.resolve = lambda raw: ((object(), "body"), None)
        res = mr.handler(relation="clearance", entity_a="Ghost")
        assert res["isError"] is True and "ghost" in error_message(res).lower()

    def test_passed_is_a_declared_output(self):
        _faces(_cyl((0, 0, 0), (0, 0, 1)), _cyl((0, 0, 3), (0, 0, 1)))
        out = _payload(mr.handler(relation="coaxial"))
        assert mr.RETURNS[0].assert_present(out) == ""      # the RETURNS contract mints 'passed'
