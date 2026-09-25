# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The seam reads: normal angles never a stand-in 0, curvature signs, and the one partner face."""

import math
import types

import pytest

from conftest import BRepBody, BRepEdge, BRepFace, FakePoint, FakeVector3D, load_tool

cont = load_tool("_continuity")

R_CM = 2.8


def _surface(normal, kmax=0.0, kmin=0.0, max_dir=(0.0, 0.0, 0.0), curvatures_answer=True,
             shift=(0.0, 0.0, 0.0), on_face=lambda prm: True):
    """A face's local evaluator double (no shape dump); `normal` is a vector or f(parameter)."""
    def normal_at(p):
        return FakeVector3D(*(normal(p) if callable(normal) else normal))
    face = BRepFace(None)
    face.evaluator = types.SimpleNamespace(
        getParametersAtPoints=lambda pts: [True, list(pts)],
        getNormalsAtParameters=lambda prm: [True, [normal_at(p) for p in prm]],
        getPointsAtParameters=lambda prm: [True, [FakePoint(*(c + d for c, d in zip(
            (p.x, p.y, p.z), shift))) for p in prm]],
        getCurvatures=lambda prm: [curvatures_answer, [FakeVector3D(*max_dir) for _ in prm],
                                   [kmax for _ in prm], [kmin for _ in prm]],
        isParameterOnFace=on_face)
    return face


def _tenths(p):
    """The sample a parameter stands for: 1, 3, 5, 7, 9 for five samples along a unit z edge."""
    return round(p.z * 10)


def _seam_edge(tangent, *faces):
    """A straight edge along `tangent` - a local curve-evaluator double: no shape dump."""
    edge = BRepEdge(None, faces=list(faces))

    def at(t):
        return FakePoint(*(c * t for c in tangent))
    edge.evaluator = types.SimpleNamespace(
        getParameterExtents=lambda: [True, 0.0, 1.0],
        getPointsAtParameters=lambda ts: [True, [at(t) for t in ts]],
        getTangents=lambda ts: [True, [FakeVector3D(*tangent) for _ in ts]],
        getPointAtParameter=lambda t: [True, at(t)],
        getParameterAtPoint=lambda p: [True, sum(a * b for a, b in zip((p.x, p.y, p.z), tangent))])
    return edge


class TestSeam:
    def test_opposed_normals_fold_to_zero_not_one_eighty(self):
        facts, err = cont.seam(_seam_edge((0, 0, 1)), _surface((1, 0, 0)), _surface((-1, 0, 0)), 5)
        assert err is None
        assert facts["max_normal_angle_deg"] < 1e-9
        assert facts["normals_opposed"] is True

    def test_face_b_curvature_is_read_in_face_a_orientation(self):
        # the one cylinder read from both sides: each face signs kmax against its own normal
        outside = _surface((1, 0, 0), kmax=-1 / R_CM, max_dir=(0, 1, 0))
        inside = _surface((-1, 0, 0), kmax=1 / R_CM, max_dir=(0, 1, 0))
        facts, err = cont.seam(_seam_edge((0, 0, 1), outside, inside), outside, inside, 5)
        assert err is None
        assert facts["max_curvature_jump_per_cm"] < 1e-12

    def test_a_cylinder_reads_its_curvature_across_the_axis_and_none_along_it(self):
        cylinder = _surface((1, 0, 0), kmax=-1 / R_CM, max_dir=(0, 1, 0))
        plane = _surface((1, 0, 0))
        along_axis, _ = cont.seam(_seam_edge((0, 0, 1)), cylinder, plane, 9)
        around, _ = cont.seam(_seam_edge((0, 1, 0)), cylinder, plane, 9)
        assert abs(along_axis["max_curvature_jump_per_cm"] - 1 / R_CM) < 1e-12
        assert around["max_curvature_jump_per_cm"] < 1e-12

    def test_one_body_reads_a_crease_past_ninety_as_itself_and_two_bodies_fold_it(self):
        a160 = math.radians(160.0)
        faces = (_surface((1, 0, 0)), _surface((math.cos(a160), math.sin(a160), 0.0)))
        one, _ = cont.seam(_seam_edge((0, 0, 1)), *faces, 5, one_body=True)
        two, _ = cont.seam(_seam_edge((0, 0, 1)), *faces, 5)
        assert one["max_normal_angle_deg"] == pytest.approx(160.0)
        assert two["max_normal_angle_deg"] == pytest.approx(20.0)

    def test_the_gap_is_the_distance_between_the_two_faces_points(self):
        facts, err = cont.seam(_seam_edge((0, 0, 1)), _surface((1, 0, 0)),
                               _surface((1, 0, 0), shift=(0.0, 0.0, 0.004)), 5)
        assert err is None and facts["max_gap_cm"] == pytest.approx(0.004)

    def test_samples_off_the_face_or_unread_are_counted(self):
        def on_face(p):
            if _tenths(p) == 9:
                raise RuntimeError("isParameterOnFace")
            return _tenths(p) not in (1, 5)
        facts, err = cont.seam(_seam_edge((0, 0, 1)), _surface((1, 0, 0)),
                               _surface((1, 0, 0), on_face=on_face), 5)
        assert err is None and facts["off_face_samples"] == 3

    def test_worst_at_is_the_sample_of_the_largest_angle(self):
        turn = {1: 1.0, 3: 2.0, 5: 3.0, 7: 5.0, 9: 4.0}

        def turned(p):
            a = math.radians(turn[_tenths(p)])
            return (math.cos(a), math.sin(a), 0.0)
        facts, err = cont.seam(_seam_edge((0, 0, 1)), _surface((1, 0, 0)), _surface(turned), 5)
        assert err is None and facts["max_normal_angle_deg"] == pytest.approx(5.0)
        assert facts["worst_at_cm"] == pytest.approx((0.0, 0.0, 0.7))

    def test_an_evaluator_that_answers_false_is_named(self):
        facts, err = cont.seam(_seam_edge((0, 0, 1)), _surface((1, 0, 0)),
                               _surface((1, 0, 0), curvatures_answer=False), 3)
        assert facts is None
        assert "face B's SurfaceEvaluator.getCurvatures did not answer" in err


def _segment_distance(edge, pt):
    """(a MeasureResults double, None): the distance from pt to the fake edge's 0..1 segment."""
    a = edge.evaluator.getPointAtParameter(0.0)[1]
    b = edge.evaluator.getPointAtParameter(1.0)[1]
    ab = [q - p for p, q in zip((a.x, a.y, a.z), (b.x, b.y, b.z))]
    ap = [q - p for p, q in zip((a.x, a.y, a.z), (pt.x, pt.y, pt.z))]
    t = max(0.0, min(1.0, sum(u * v for u, v in zip(ab, ap)) / sum(u * u for u in ab)))
    d = math.dist((pt.x, pt.y, pt.z), tuple(p + t * u for p, u in zip((a.x, a.y, a.z), ab)))
    return types.SimpleNamespace(value=d), None


class TestPartnerFace:
    @pytest.fixture(autouse=True)
    def _distances(self, monkeypatch):
        monkeypatch.setattr(cont._common, "min_distance", _segment_distance)

    def _body(self, *edges):
        return BRepBody("Loft1", edges=edges)

    def test_a_partner_whose_parameter_read_answers_its_start_is_still_found(self):
        mine, theirs = _surface((1, 0, 0)), _surface((1, 0, 0))
        partner = _seam_edge((0, 0, 1), theirs)
        partner.evaluator.getParameterAtPoint = lambda p: [True, 0.0]
        face, err = cont.partner_face(_seam_edge((0, 0, 1), mine), self._body(partner))
        assert err is None and face is theirs

    def test_the_one_coincident_edge_gives_its_face(self):
        mine, theirs = _surface((1, 0, 0)), _surface((1, 0, 0))
        edge = _seam_edge((0, 0, 1), mine)
        face, err = cont.partner_face(edge, self._body(_seam_edge((1, 0, 0)),
                                                        _seam_edge((0, 0, 1), theirs)))
        assert err is None and face is theirs

    def test_two_coincident_edges_are_refused_naming_the_count(self):
        edge = _seam_edge((0, 0, 1), _surface((1, 0, 0)))
        body = self._body(_seam_edge((0, 0, 1), _surface((1, 0, 0))),
                          _seam_edge((0, 0, 1), _surface((1, 0, 0))))
        face, err = cont.partner_face(edge, body)
        assert face is None
        assert err.startswith("2 edges of 'Loft1' lie along it")

    def test_a_partner_split_along_the_edge_names_the_pieces_and_the_swap(self):
        edge = _seam_edge((0, 0, 1), _surface((1, 0, 0)))
        edge.body = BRepBody("Tube")
        low, high = (_seam_edge((0, 0, 1), _surface((1, 0, 0))) for _ in range(2))
        low.evaluator.getPointAtParameter = lambda t: [True, FakePoint(0.0, 0.0, 0.5 * t)]
        high.evaluator.getPointAtParameter = lambda t: [True, FakePoint(0.0, 0.0, 0.5 + 0.5 * t)]
        face, err = cont.partner_face(edge, self._body(low, high))
        assert face is None
        assert "lie on 2 different edges of 'Loft1'" in err and "('Tube')" in err

    def test_a_partner_edge_bordering_two_faces_is_refused_not_taken_first(self):
        edge = _seam_edge((0, 0, 1), _surface((1, 0, 0)))
        body = self._body(_seam_edge((0, 0, 1), _surface((1, 0, 0)), _surface((0, 1, 0))))
        face, err = cont.partner_face(edge, body)
        assert face is None and "borders 2 faces" in err


def _face(normal, answers=True):
    """A face whose evaluator answers the live [bool, value] lists, the normal the same at every
    parameter; `answers` False is the evaluator that returns False."""
    face = BRepFace(None)
    face.evaluator = types.SimpleNamespace(
        getParameterAtPoint=lambda pt: [answers, (pt.x, pt.y)],
        getNormalAtParameter=lambda prm: [answers, normal])
    return face


def _edge(*faces):
    edge = BRepEdge(None, faces=list(faces))
    edge.evaluator = types.SimpleNamespace(
        getParameterExtents=lambda: [True, 0.0, 1.0],
        getPointAtParameter=lambda t: [True, FakePoint(t, 0.0, 0.0)])
    return edge


class TestEdgeAngles:
    def test_a_crease_reads_ninety_and_a_smooth_seam_zero(self):
        up, side = FakeVector3D(0, 0, 1), FakeVector3D(1, 0, 0)
        assert abs(cont.edge_angles(_edge(_face(up), _face(side))) - 90.0) < 1e-9
        assert cont.edge_angles(_edge(_face(up), _face(up))) == 0.0

    def test_a_sample_that_will_not_read_is_none_not_smooth(self):
        up = FakeVector3D(0, 0, 1)
        assert cont.edge_angles(_edge(_face(up), _face(up, answers=False))) is None

    def test_a_one_faced_edge_is_none(self):
        assert cont.edge_angles(_edge(_face(FakeVector3D(0, 0, 1)))) is None


class _Unread:
    """A collection whose count read raises."""
    @property
    def count(self):
        raise RuntimeError("count")


class TestBodySeams:
    def test_an_unread_edge_list_or_face_count_is_an_unread_seam_not_no_seam(self):
        assert cont.body_seams(types.SimpleNamespace(edges=_Unread())) == [(None, None)]
        odd = types.SimpleNamespace(faces=_Unread())
        edges = types.SimpleNamespace(count=1, item=lambda i: odd)
        assert cont.body_seams(types.SimpleNamespace(edges=edges)) == [(odd, None)]
