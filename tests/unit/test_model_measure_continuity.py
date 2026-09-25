# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""model_measure_continuity: the faces each seam is read between, the caps, and the units."""

import adsk.fusion
import pytest

from conftest import (BRepBody, BRepEdge, BRepFace, MeshBody, error_message, install, load_tool,
                      make_design, payload)

mc = load_tool("model_measure_continuity")

_FACTS = {"samples": 9, "max_gap_cm": 0.002, "max_normal_angle_deg": 0.5,
          "max_curvature_jump_per_cm": 0.25, "worst_at_cm": (1.0, 2.0, 3.0),
          "normals_opposed": True, "off_face_samples": 0}


@pytest.fixture
def world(monkeypatch):
    """Two-faced E0..E24, one-faced H1, three-faced H3, body B, mesh M; seam() records its faces."""
    monkeypatch.setattr(adsk.fusion, "BRepEdge", BRepEdge, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepBody", BRepBody, raising=False)
    monkeypatch.setattr(adsk.fusion, "MeshBody", MeshBody, raising=False)
    tokens = {f"E{i}": BRepEdge(None, faces=[BRepFace(None), BRepFace(None)]) for i in range(25)}
    tokens["H1"] = BRepEdge(None, faces=[BRepFace(None)])
    tokens["H3"] = BRepEdge(None, faces=[BRepFace(None)] * 3)
    tokens["B"] = BRepBody("Loft1", is_solid=False)
    tokens["M"] = MeshBody("Scan")
    install(mc, make_design(tokens=tokens))
    seen = []

    def seam(edge, face_a, face_b, samples, one_body=False):
        seen.append((edge, face_a, face_b, samples, one_body))
        return dict(_FACTS, samples=samples), None
    monkeypatch.setattr(mc._continuity, "seam", seam)
    return tokens, seen


class TestFaces:
    def test_a_one_faced_edge_without_against_is_refused(self, world):
        msg = error_message(mc.handler(edges=["H1"]))
        assert "'edges'[0] borders one face" in msg and "'against'" in msg

    def test_a_one_faced_edge_is_read_against_the_partner_face(self, world, monkeypatch):
        tokens, seen = world
        partner = BRepFace(None)
        monkeypatch.setattr(mc._continuity, "partner_face",
                            lambda edge, body: (partner, None) if body is tokens["B"] else (None, "x"))
        out = payload(mc.handler(edges=["H1"], against="B"))
        assert out["edges"][0]["mode"] == "two_bodies"
        assert seen[0][2] is partner and seen[0][4] is False
        assert "was not used" not in out["note"]

    def test_a_two_faced_edge_reads_its_own_seam_unfolded_and_says_against_went_unused(
            self, world, monkeypatch):
        tokens, seen = world
        monkeypatch.setattr(mc._continuity, "partner_face", lambda edge, body: (BRepFace(None), None))
        out = payload(mc.handler(edges=["H1", "E0"], against="B"))
        assert [s[4] for s in seen] == [False, True]
        assert "'against' was not used for 'edges'[1]" in out["note"]

    def test_a_three_faced_edge_is_refused(self, world):
        msg = error_message(mc.handler(edges=["H3"]))
        assert "'edges'[0] reads 3 faces" in msg

    def test_a_mesh_against_is_refused(self, world):
        msg = error_message(mc.handler(edges=["H1"], against="M"))
        assert "'against' must be a BRep (non-mesh) body" in msg


class TestCaps:
    def test_the_edge_cap_takes_twenty_and_refuses_twenty_one(self, world):
        assert len(payload(mc.handler(edges=[f"E{i}" for i in range(mc.MAX_EDGES)]))["edges"]) == 20
        msg = error_message(mc.handler(edges=[f"E{i}" for i in range(mc.MAX_EDGES + 1)]))
        assert "at most 20 - got 21" in msg

    def test_a_comma_separated_string_meets_the_same_edge_cap(self, world):
        msg = error_message(mc.handler(edges=",".join(f"E{i}" for i in range(mc.MAX_EDGES + 1))))
        assert "at most 20 - got 21" in msg

    def test_the_sample_cap_takes_fifty_and_refuses_fifty_one_and_zero(self, world):
        assert payload(mc.handler(edges=["E0"], samples=mc.MAX_SAMPLES))["edges"][0]["samples"] == 50
        assert "got 51" in error_message(mc.handler(edges=["E0"], samples=mc.MAX_SAMPLES + 1))
        assert "got 0" in error_message(mc.handler(edges=["E0"], samples=0))


class TestUnits:
    def test_lengths_divide_and_curvature_multiplies_by_the_unit(self, world):
        out = payload(mc.handler(edges=["E0"], units="mm"))
        row = out["edges"][0]
        assert row["max_gap"] == pytest.approx(0.02)
        assert row["max_curvature_jump"] == pytest.approx(0.025)
        assert row["worst_at"] == pytest.approx([10.0, 20.0, 30.0])
        assert out["max_curvature_jump"] == pytest.approx(0.025)
        assert "was not used" not in out["note"]

    def test_the_worst_edge_is_the_one_with_the_largest_angle(self, world, monkeypatch):
        angles = iter([0.1, 20.0, 0.3])
        monkeypatch.setattr(mc._continuity, "seam",
                            lambda e, a, b, n, one_body=False: (
                                dict(_FACTS, max_normal_angle_deg=next(angles)), None))
        out = payload(mc.handler(edges=["E0", "E1", "E2"]))
        assert out["worst_edge"] == 1 and out["max_normal_angle_deg"] == 20.0

    def test_off_face_samples_are_counted_in_the_note(self, world, monkeypatch):
        monkeypatch.setattr(mc._continuity, "seam",
                            lambda e, a, b, n, one_body=False: (
                                dict(_FACTS, off_face_samples=3), None))
        out = payload(mc.handler(edges=["E0"]))
        assert out["edges"][0]["off_face_samples"] == 3
        assert "3 sample(s) read isParameterOnFace false" in out["note"]
