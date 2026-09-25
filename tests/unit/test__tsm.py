# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The TSM codec against Fusion's own read-backs (tests/fixtures/tsm): parse, compare, validate."""

import math
import os

import pytest

from conftest import load_tool

tsm = load_tool("_tsm")

_FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "fixtures", "tsm")


def _fixture(name):
    with open(os.path.join(_FIXTURES, name + ".tsm"), encoding="utf-8") as fh:
        return fh.read()


def _fan(n):
    """An open disc of n quads round one centre vertex - the centre an interior vertex of
    valence n, every rim vertex on at most two faces."""
    verts = [[0.0, 0.0, 0.0]]
    for i in range(n):
        a = 2 * math.pi * i / n
        verts.append([math.cos(a), math.sin(a), 0.0])
    for i in range(n):
        a = 2 * math.pi * (i + 0.5) / n
        verts.append([2 * math.cos(a), 2 * math.sin(a), 0.0])
    faces = [[0, 1 + i, 1 + n + i, 1 + (i + 1) % n] for i in range(n)]
    return {"vertices": verts, "faces": faces, "creases": []}


class TestParse:
    def test_a_clean_read_back_is_a_cage_that_re_emits_to_what_fusion_read(self):
        text = _fixture("gen_box3_cm_readback")
        cage, census, reasons = tsm.parse(text)
        assert reasons == [] and census["faces"] == 54 and census["stars"] == {3: 8}
        assert tsm.compare(tsm.emit(cage), text) == []

    def test_a_grip_mapped_creased_read_back_keeps_its_creases(self):
        text = _fixture("gen_box3_crease_list_readback")
        cage, census, _reasons = tsm.parse(text)
        assert census["creases"] == 12 and len(cage["creases"]) == 12
        assert tsm.compare(tsm.emit(cage), text) == []

    def test_a_repaired_read_back_is_no_cage(self):
        cage, census, reasons = tsm.parse(_fixture("a3_box3_inconsistent_knot3_readback"))
        assert cage is None and census["repaired"] == 108 and "106ek" in reasons[0]

    def test_a_read_back_whose_seeds_emit_cannot_reproduce_is_no_cage(self):
        cage, census, reasons = tsm.parse(_fixture("gen_grid3x3_multiknots_readback"))
        assert cage is None and census["faces"] == 9 and "'v' record" in reasons[0]

    def test_a_t_junction_read_back_is_no_cage_and_counts_its_t_junctions(self):
        cage, census, reasons = tsm.parse(_fixture("a3_box3_tjunc_ring2_readback"))
        assert cage is None and census["t_junctions"] == 4 and census["stars"] == {3: 8}
        assert reasons


def _grid(nx, ny):
    """An open nx x ny quad grid in the xy plane - a disc."""
    verts = [[float(x), float(y), 0.0] for y in range(ny + 1) for x in range(nx + 1)]
    faces = [[y * (nx + 1) + x, y * (nx + 1) + x + 1, (y + 1) * (nx + 1) + x + 1,
              (y + 1) * (nx + 1) + x] for y in range(ny) for x in range(nx)]
    return {"vertices": verts, "faces": faces, "creases": []}


def _torus(n=4):
    """An n x n quad torus - one closed piece of genus 1."""
    def v(i, j):
        return (j % n) * n + (i % n)
    verts = [[(3 + math.cos(2 * math.pi * j / n)) * math.cos(2 * math.pi * i / n),
              (3 + math.cos(2 * math.pi * j / n)) * math.sin(2 * math.pi * i / n),
              math.sin(2 * math.pi * j / n)] for j in range(n) for i in range(n)]
    faces = [[v(i, j), v(i + 1, j), v(i + 1, j + 1), v(i, j + 1)]
             for j in range(n) for i in range(n)]
    return {"vertices": verts, "faces": faces, "creases": []}


def _flipped_box():
    cage = tsm.box([2.0, 2.0, 2.0], [1, 1, 1])
    cage["faces"][0] = cage["faces"][0][::-1]
    return cage


def _swap_first_edges(text):
    lines = text.splitlines()
    i, j = [n for n, ln in enumerate(lines) if ln.startswith("e ")][:2]
    lines[i], lines[j] = lines[j], lines[i]
    return "\n".join(lines) + "\n"


class TestCompare:
    @pytest.mark.parametrize("capped,fixture", [(True, "gen_cyl8x4_capped_readback"),
                                                (False, "gen_cyl8x4_open_readback")])
    def test_the_cylinder_primitive_is_the_cage_fusion_read_back(self, capped, fixture):
        cage, err = tsm.primitive("cylinder", [20.0, 40.0], [4], capped)
        sent = tsm.emit(tsm.translated(cage, 1.0, [0.0, 0.0, 20.0]))
        assert err is None and tsm.compare(sent, _fixture(fixture)) == []

    @pytest.mark.parametrize("edit,said", [
        (lambda t: t.replace("\ntol ", "\nzz unknown 1 2\ntol "), "carries record 'zz'"),
        (lambda t: t.replace("MULTIPLE_KNOTS", "SUBD_CREASES"),
         "end-conditions reads SUBD_CREASES, not MULTIPLE_KNOTS"),
        (_swap_first_edges, "the 'e' records name other links")])
    def test_a_read_back_record_the_emitter_did_not_write_is_named(self, edit, said):
        sent = tsm.emit(tsm.box([2.0, 2.0, 2.0], [3, 3, 3]))
        assert any(said in f for f in tsm.compare(sent, edit(sent)))

    def test_the_box_primitive_against_fusions_repair_of_it_names_the_repair(self):
        sent = tsm.emit(tsm.box([20.0, 20.0, 20.0], [3, 3, 3]))
        found = tsm.compare(sent, _fixture("a3_box3_inconsistent_knot3_readback"))
        assert any("knot interval" in f for f in found) and any("106ek" in f for f in found)
        # the f/v/l tables themselves match: the box primitive IS the cage Fusion repaired
        assert not any("record" in f and "differs" in f for f in found)

    def test_a_cap_type_other_than_g1caps_is_named(self):
        text = _fixture("gen_box3_cm_readback")
        cage = tsm.parse(text)[0]
        found = tsm.compare(tsm.emit(cage), text.replace("G1CAPS", "G0CAPS"))
        assert found and "G0CAPS" in found[0]


class TestValidate:
    def test_a_face_naming_a_missing_vertex_names_the_face_and_the_index(self):
        err = tsm.validate({"vertices": [[0, 0, 0]] * 8, "faces": [[0, 1, 2, 99]]})
        assert "face 0" in err and "99" in err

    def test_an_inward_closed_cage_is_refused(self):
        cage = tsm.box([2.0, 2.0, 2.0], [1, 1, 1])
        cage["faces"] = [f[::-1] for f in cage["faces"]]
        assert "reversing each face's vertex order" in tsm.validate(cage)

    @pytest.mark.parametrize("n,ok", [(3, True), (6, True), (7, False), (2, False)])
    def test_the_interior_valence_range_is_the_measured_one(self, n, ok):
        err = tsm.validate(_fan(n))
        assert (err is None) is ok, err
        if not ok:
            assert f"vertex 0 has {n} edges" in err

    def test_the_face_cap_is_inclusive(self):
        cage, err = tsm.primitive("box", [1.0, 1.0, 1.0], [30, 30, 30])
        assert err is None and len(cage["faces"]) == 5400 and tsm.validate(cage) is None
        err = tsm.validate({"vertices": [[0, 0, 0]] * 4, "faces": [[0, 1, 2, 3]] * 5401})
        assert "5401 faces" in err

    def test_a_crease_that_is_not_a_closed_loop_is_refused(self):
        cage = tsm.box([2.0, 2.0, 2.0], [3, 3, 3])
        a, b, c, _d = cage["faces"][0]
        cage["creases"] = [[a, b], [b, c]]
        assert "exactly 2" in tsm.validate(cage)

    def test_a_boundary_vertex_on_three_faces_is_refused(self):
        # three quads of a 2 x 2 grid: the centre vertex sits on the notch the fourth leaves
        grid = {"vertices": [[x, y, 0.0] for y in range(3) for x in range(3)][:8],
                "faces": [[0, 1, 4, 3], [1, 2, 5, 4], [3, 4, 7, 6]], "creases": []}
        assert "lies on 3 faces" in tsm.validate(grid)

    @pytest.mark.parametrize("cage,said", [
        (_flipped_box(), "faces 0 and "),
        ({"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 1, 0], [2, 2, 0],
                       [1, 2, 0]], "faces": [[0, 1, 2, 3], [2, 4, 5, 6]]},
         "vertex 2 joins faces that do not form one fan"),
        ({"vertices": [[x, y, 0] for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
          + [[x + 5, y, 0] for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))],
          "faces": [[0, 1, 2, 3], [4, 5, 6, 7]]}, "the cage is 2 separate pieces"),
        (_torus(), "Euler characteristic 0, 0 open boundary loop(s)"),
        (dict(_grid(2, 2), creases=[[0, 1]]), "crease 0 [0, 1] is on the open boundary")],
        ids=["flipped-face", "pinch", "two-pieces", "torus", "boundary-crease"])
    def test_a_malformed_cage_is_refused_naming_its_offender(self, cage, said):
        assert said in tsm.validate(cage)

    def test_a_coordinate_past_the_reach_is_refused_and_one_at_it_kept(self):
        cage = tsm.box([2.0, 2.0, 2.0], [1, 1, 1])
        at = tsm.translated(cage, 1.0, [tsm.MAX_REACH_CM - 1.0, 0.0, 0.0])
        assert tsm.validate(at, tsm.MAX_REACH_CM) is None
        past = tsm.validate(tsm.translated(cage, 1.0, [tsm.MAX_REACH_CM - 0.5, 0.0, 0.0]),
                            tsm.MAX_REACH_CM)
        assert past.startswith("vertex ") and "within 100000 of the origin" in past

    @pytest.mark.parametrize("size,offset,said", [
        (1.0, [1e8, 1e8, 1e8], "reversing each face's vertex order"),
        (1e160, [0.0, 0.0, 0.0], "signed volume reads nan")])
    def test_an_inward_cage_far_out_or_huge_is_still_refused(self, size, offset, said):
        cage = tsm.box([size] * 3, [1, 1, 1])
        cage["faces"] = [f[::-1] for f in cage["faces"]]
        assert said in tsm.validate(tsm.translated(cage, 1.0, offset))


class TestHash:
    def test_the_hash_ignores_crease_pair_order(self):
        cage = tsm.box([2.0, 2.0, 2.0], [3, 3, 3])
        a, b, c, d = cage["faces"][0]
        loop = [[a, b], [b, c], [c, d], [d, a]]
        flipped = dict(cage, creases=[p[::-1] for p in reversed(loop)])
        assert tsm.canonical_hash(dict(cage, creases=loop)) == tsm.canonical_hash(flipped)
