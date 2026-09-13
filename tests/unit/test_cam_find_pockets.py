# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Unit tests for ``cam_find_pockets`` - CAM pocket recognition with per-pocket face handles.

Pinned here (no live Fusion): the recognizer's own rows are published unchanged, the bottom type
resolves BY MEMBER NAME with 'unread' for anything else, depth crosses in the caller's units,
include_bosses picks the recognizer entry point and a boss-route raise is relayed with the plain
route as its remedy, a zero or non-finite attack vector is refused, the cap sets truncated while
pocket_count stays the total, the body census skips and counts what is not a solid, and a loop
vector that did not read publishes null.
"""

import pytest

import adsk.cam
import adsk.core
import adsk.fusion

from conftest import (BRepBody, BRepFace, FakePoint, FakeVector3D, Line3D, MakeComp, MeshBody,
                      error_message, install, load_tool, make_design, payload as _payload,
                      resolved_path, _NamedCollection)

cfp = load_tool("cam_find_pockets")


def _member(name):
    """One adsk.cam.RecognizedPocketBottomType member, read at call time so the double and the tool
    share it."""
    return getattr(adsk.cam.RecognizedPocketBottomType, name)


def _loop(segments):
    """One boundary/island loop: the shared Curve3DPath double over `segments` line curves."""
    return resolved_path([Line3D(None, None) for _ in range(segments)])


class _Pocket:
    """A RecognizedPocket - no live shape dump exists, so this is a local double."""

    def __init__(self, depth=1.0, bottom="RecognizedPocketBottomTypeFlat", is_closed=True,
                 is_through=False, boundaries=(4,), islands=(), faces=(), shared=(),
                 attack=(0.0, 0.0, -1.0)):
        self.depth = depth
        self._bottom = bottom
        self.isClosed = is_closed
        self.isThrough = is_through
        self.boundaries = [_loop(n) for n in boundaries]
        self.islands = [_loop(n) for n in islands]
        self.faces = list(faces)
        self.sharedFaces = list(shared)
        self.attackVector = FakeVector3D(*attack)

    @property
    def bottomType(self):
        if self._bottom is None:
            raise RuntimeError("3 : the pocket bottom type is unavailable")
        return self._bottom if isinstance(self._bottom, int) else _member(self._bottom)


def _face(token, centroid=(0.0, 0.0, 0.0)):
    """A BRepFace whose entityToken and centroid the published handle is minted from."""
    return BRepFace(None, centroid=FakePoint(*centroid), entity_token=token)


@pytest.fixture
def scene(monkeypatch):
    """Build the design and stub the recognizer; the returned dict records what reached it."""
    seen = {}

    def build(pockets=(), bodies=None, mesh_bodies=None, per_body=None):
        monkeypatch.setattr(adsk.fusion, "BRepBody", BRepBody, raising=False)
        monkeypatch.setattr(adsk.fusion, "MeshBody", MeshBody, raising=False)
        monkeypatch.setattr(adsk.core.Vector3D, "create",
                            lambda x, y, z: seen.setdefault("created", (x, y, z)), raising=False)
        solids = [BRepBody(name="Solid1")] if bodies is None else list(bodies)
        design = make_design(comp=MakeComp(bodies=solids, mesh_bodies=mesh_bodies))
        install(cfp, design)

        def recognize(body, vector, include_bosses):
            seen.setdefault("bodies", []).append(body)
            seen["vector"] = vector
            seen["include_bosses"] = include_bosses
            found = per_body.get(body.name, ()) if per_body else pockets
            return _NamedCollection(list(found)), None

        monkeypatch.setattr(cfp, "_recognize", recognize)
        return seen
    return build


class TestRows:
    def test_the_recognizers_own_rows_are_published_unchanged(self, scene):
        # Two pockets of the SAME depth: a tool that re-grouped by geometry would merge them.
        scene([_Pocket(depth=1.0), _Pocket(depth=1.0, is_closed=False)])
        out = _payload(cfp.handler())
        assert out["pocket_count"] == 2
        assert [p["index"] for p in out["pockets"]] == [0, 1]
        assert [p["is_closed"] for p in out["pockets"]] == [True, False]
        assert "truncated" not in out

    def test_depth_and_units_cross_in_the_requested_units(self, scene):
        scene([_Pocket(depth=1.6)])
        out = _payload(cfp.handler(units="mm"))
        assert out["pockets"][0]["depth"] == 16.0 and out["units"] == "mm"
        assert out["pockets"][0]["attack_vector"] == [0.0, 0.0, -1.0]

    def test_each_loop_publishes_its_own_segment_count(self, scene):
        scene([_Pocket(boundaries=(4, 3), islands=(1,))])
        row = _payload(cfp.handler())["pockets"][0]
        assert row["boundaries"] == [4, 3] and row["islands"] == [1]

    def test_a_boss_row_reads_an_island_and_no_boundary(self, scene):
        # The shape include_bosses adds: a boss carries an island loop and NO boundary, so a tool
        # that published islands as boundaries would make a boss indistinguishable from a pocket.
        scene([_Pocket(depth=0.5, is_closed=False, boundaries=(), islands=(1,))])
        row = _payload(cfp.handler(include_bosses=True))["pockets"][0]
        assert row["boundaries"] == [] and row["islands"] == [1]

    def test_a_loop_vector_that_did_not_read_publishes_null(self, scene):
        pocket = _Pocket()
        del pocket.islands
        scene([pocket])
        row = _payload(cfp.handler())["pockets"][0]
        # null, not []: nothing read the islands, so "this pocket has none" was never measured.
        assert row["islands"] is None and row["boundaries"] == [4]

    def test_shared_face_count_is_the_vectors_own_length(self, scene):
        scene([_Pocket(shared=(_face("TOK_S1"), _face("TOK_S2")))])
        assert _payload(cfp.handler())["pockets"][0]["shared_face_count"] == 2

    def test_a_handle_is_minted_for_every_pocket_face_in_order(self, scene):
        scene([_Pocket(faces=[_face("TOK_WALL", (1.0, 2.0, 3.0)), _face("TOK_FLOOR")])])
        assert _payload(cfp.handler())["pockets"][0]["faces"] == [
            "TOK_WALL|@face:1.000000,2.000000,3.000000",
            "TOK_FLOOR|@face:0.000000,0.000000,0.000000"]

    def test_declared_outputs_present(self, scene):
        scene([_Pocket(faces=[_face("TOK")])])
        out = _payload(cfp.handler())
        for r in cfp.RETURNS:
            assert r.assert_present(out) == "", r.key

    def test_the_note_points_at_the_selection_tool(self, scene):
        scene([_Pocket(faces=[_face("TOK")])])
        assert "cam_select_geometry" in _payload(cfp.handler())["note"]


class TestBottomType:
    def test_a_flat_floor_reads_flat_however_its_member_compares(self, scene):
        # The member is read through `raw is None`, never `not raw`: the Flat member is 0 live, so
        # a truthiness guard would publish every flat floor as 'unread'.
        scene([_Pocket(bottom="RecognizedPocketBottomTypeFlat")])
        assert _payload(cfp.handler())["pockets"][0]["bottom_type"] == "flat"

    def test_a_through_floor_reads_through(self, scene):
        scene([_Pocket(bottom="RecognizedPocketBottomTypeThrough")])
        assert _payload(cfp.handler())["pockets"][0]["bottom_type"] == "through"

    def test_a_type_no_member_holds_reads_unread_rather_than_crossing_raw(self, scene):
        # A bottom type this build reports and the tool has no name for is 'unread', never the raw
        # value: the payload is JSON, and an enum object echoed into it does not serialize.
        scene([_Pocket(bottom=99)])
        assert _payload(cfp.handler())["pockets"][0]["bottom_type"] == "unread"

    def test_an_unreadable_bottom_type_reads_unread(self, scene):
        scene([_Pocket(bottom=None, depth=1.0)])
        row = _payload(cfp.handler())["pockets"][0]
        assert row["bottom_type"] == "unread" and row["depth"] == 10.0


class TestAttackVector:
    def test_the_default_attack_runs_straight_down(self, scene):
        seen = scene([_Pocket()])
        cfp.handler()
        assert seen["created"] == (0.0, 0.0, -1.0)
        assert seen["vector"] == (0.0, 0.0, -1.0)

    def test_an_explicit_attack_reaches_the_recognizer_as_given(self, scene):
        seen = scene([_Pocket()])
        cfp.handler(attack_vector=[1, 0, 0])
        assert seen["created"] == (1.0, 0.0, 0.0)

    def test_a_zero_attack_vector_is_refused(self, scene):
        scene([_Pocket()])
        res = cfp.handler(attack_vector=[0, 0, 0])
        assert res["isError"] is True and "no direction" in error_message(res)

    def test_a_signed_zero_triple_is_still_a_zero_vector(self, scene):
        # The exact boundary: -0.0 is falsy and 0.0 is falsy, so all three must be tested, not just
        # the first - a guard reading only `x` lets [0.0, -0.0, 0.0] through as a direction.
        scene([_Pocket()])
        res = cfp.handler(attack_vector=[0.0, -0.0, 0.0])
        assert res["isError"] is True and "no direction" in error_message(res)

    def test_one_nonzero_component_is_a_direction(self, scene):
        scene([_Pocket()])
        assert _payload(cfp.handler(attack_vector=[0, 0, -1]))["pocket_count"] == 1

    def test_a_triple_of_the_wrong_length_is_refused_naming_the_value(self, scene):
        scene([_Pocket()])
        res = cfp.handler(attack_vector=[0, -1])
        assert res["isError"] is True and "[0, -1]" in error_message(res)

    def test_a_non_numeric_component_is_refused(self, scene):
        scene([_Pocket()])
        res = cfp.handler(attack_vector=[0, 0, "down"])
        assert res["isError"] is True and "three numbers" in error_message(res)

    def test_a_non_finite_component_is_refused_before_the_api_call(self, scene):
        # The recognizer terminates Fusion on a direction it cannot use, so an infinity or a NaN
        # has to be refused here - there is no failure downstream to report.
        seen = scene([_Pocket()])
        for bad in (float("inf"), float("nan")):
            res = cfp.handler(attack_vector=[0, 0, bad])
            assert res["isError"] is True and "finite" in error_message(res)
        assert "created" not in seen and "vector" not in seen


class TestRecognizerRouting:
    @pytest.fixture
    def recognizer(self, monkeypatch):
        """Stub both adsk.cam entry points; the returned dict records which one ran."""
        seen = {}

        class _Input:
            """A RecognizedPocketInput double - the boss-aware route is configured through it."""
            body = None
            attackVectors = None
            isIncludingBosses = None

        def plain(body, vector):
            seen["plain"] = (body, vector)
            return _NamedCollection([])

        def with_input(inp):
            seen["with_input"] = inp
            return _NamedCollection([])

        monkeypatch.setattr(adsk.cam.RecognizedPocket, "recognizePockets", plain, raising=False)
        monkeypatch.setattr(adsk.cam.RecognizedPocket, "recognizePocketsWithInput", with_input,
                            raising=False)
        monkeypatch.setattr(adsk.cam.RecognizedPocketInput, "create",
                            lambda: seen.setdefault("input", _Input()), raising=False)
        return seen

    def test_include_bosses_false_takes_the_no_input_recognizer(self, recognizer):
        body, vector = object(), object()
        pockets, err = cfp._recognize(body, vector, False)
        assert err is None and pockets is not None
        assert recognizer["plain"] == (body, vector)
        assert "with_input" not in recognizer

    def test_include_bosses_true_takes_the_input_recognizer_and_asks_for_bosses(self, recognizer):
        body, vector = object(), object()
        pockets, err = cfp._recognize(body, vector, True)
        assert err is None and pockets is not None
        assert "plain" not in recognizer
        inp = recognizer["input"]
        assert inp.body is body and inp.attackVectors == [vector]
        assert inp.isIncludingBosses is True

    def test_a_boss_route_raise_relays_it_and_names_the_plain_route(self, monkeypatch):
        # The message is relayed as the platform worded it and the remedy is the OTHER route: this
        # call reads no entitlement, so it cannot say an extension is what the refusal was about.
        def boom(inp):
            raise RuntimeError("3 : InternalValidationError : bosses")

        monkeypatch.setattr(adsk.cam.RecognizedPocketInput, "create",
                            lambda: type("I", (), {})(), raising=False)
        monkeypatch.setattr(adsk.cam.RecognizedPocket, "recognizePocketsWithInput", boom,
                            raising=False)
        pockets, err = cfp._recognize(object(), object(), True)
        assert pockets is None
        assert "InternalValidationError : bosses" in err and "include_bosses=false" in err

    def test_a_raising_recognizer_is_an_error_naming_what_it_raised(self, monkeypatch):
        def boom(body, vector):
            raise RuntimeError("3 : InternalValidationError : res")

        monkeypatch.setattr(adsk.cam.RecognizedPocket, "recognizePockets", boom, raising=False)
        pockets, err = cfp._recognize(BRepBody(name="Solid1"), object(), False)
        assert pockets is None and "InternalValidationError" in err and "Solid1" in err

    def test_a_recognizer_error_fails_the_call(self, scene, monkeypatch):
        scene([_Pocket()])
        monkeypatch.setattr(cfp, "_recognize", lambda b, v, i: (None, "recognition refused"))
        res = cfp.handler()
        assert res["isError"] is True and "recognition refused" in error_message(res)


class TestCap:
    def test_a_call_at_the_cap_is_complete(self, scene):
        scene([_Pocket() for _ in range(cfp._POCKETS_DEFAULT)])
        out = _payload(cfp.handler())
        assert len(out["pockets"]) == cfp._POCKETS_DEFAULT
        assert out["pocket_count"] == cfp._POCKETS_DEFAULT and "truncated" not in out

    def test_one_pocket_past_the_cap_truncates_and_still_counts_them_all(self, scene):
        scene([_Pocket() for _ in range(cfp._POCKETS_DEFAULT + 1)])
        out = _payload(cfp.handler())
        assert len(out["pockets"]) == cfp._POCKETS_DEFAULT
        assert out["pocket_count"] == cfp._POCKETS_DEFAULT + 1 and out["truncated"] is True

    def test_max_results_reaches_the_pockets_past_the_default(self, scene):
        scene([_Pocket() for _ in range(cfp._POCKETS_DEFAULT + 10)])
        out = _payload(cfp.handler(max_results=cfp._POCKETS_DEFAULT + 10))
        assert len(out["pockets"]) == cfp._POCKETS_DEFAULT + 10 and "truncated" not in out

    def test_max_results_is_clamped_to_the_ceiling(self, scene):
        scene([_Pocket() for _ in range(cfp._POCKETS_MAX + 1)])
        out = _payload(cfp.handler(max_results=10_000))
        assert len(out["pockets"]) == cfp._POCKETS_MAX and out["truncated"] is True


class TestBodyCensus:
    def test_an_omitted_bodies_list_scans_the_solids_and_counts_what_it_skipped(self, scene):
        seen = scene([], bodies=[BRepBody(name="Solid1"), BRepBody(name="Solid2"),
                                 BRepBody(name="Sheet1", is_solid=False)],
                     mesh_bodies=[MeshBody(name="Scan1")])
        out = _payload(cfp.handler())
        assert [b.name for b in seen["bodies"]] == ["Solid1", "Solid2"]
        assert out["bodies_scanned"] == 2
        assert out["surface_bodies_skipped"] == 1 and out["mesh_bodies_skipped"] == 1
        assert "unreadable_bodies_skipped" not in out

    def test_a_body_whose_is_solid_will_not_read_is_its_own_skip_bucket(self, scene):
        # An unreadable isSolid is NOT a surface body: nothing measured this body either way, and
        # counting it as one would publish a census term no read supports.
        seen = scene([], bodies=[BRepBody(name="Solid1"),
                                 BRepBody(name="Mystery", solid_readable=False)])
        out = _payload(cfp.handler())
        assert [b.name for b in seen["bodies"]] == ["Solid1"]
        assert out["unreadable_bodies_skipped"] == 1 and out["surface_bodies_skipped"] == 0
        assert out["bodies_scanned"] == 1

    def test_every_scanned_bodys_pockets_land_in_one_list(self, scene):
        scene([], bodies=[BRepBody(name="Solid1"), BRepBody(name="Solid2")],
              per_body={"Solid1": [_Pocket(depth=1.0)],
                        "Solid2": [_Pocket(depth=2.0), _Pocket(depth=3.0)]})
        out = _payload(cfp.handler())
        assert out["pocket_count"] == 3
        assert [p["depth"] for p in out["pockets"]] == [10.0, 20.0, 30.0]
        assert [p["index"] for p in out["pockets"]] == [0, 1, 2]

    def test_a_named_body_is_the_only_one_scanned(self, scene):
        seen = scene([], bodies=[BRepBody(name="Solid1"), BRepBody(name="Solid2")])
        out = _payload(cfp.handler(bodies=["Solid2"]))
        assert [b.name for b in seen["bodies"]] == ["Solid2"]
        assert out["bodies_scanned"] == 1 and out["surface_bodies_skipped"] == 0

    def test_a_design_with_no_solid_body_is_refused_naming_the_skips(self, scene):
        scene([], bodies=[BRepBody(name="Sheet1", is_solid=False)])
        res = cfp.handler()
        assert res["isError"] is True
        assert "1 surface" in error_message(res) and "No solid body" in error_message(res)

    def test_a_surface_body_named_in_bodies_is_refused_by_the_typed_kind(self, scene):
        scene([], bodies=[BRepBody(name="Solid1"), BRepBody(name="Sheet1", is_solid=False)])
        res = cfp.handler(bodies=["Sheet1"])
        assert res["isError"] is True and "SOLID" in error_message(res)


class TestGuards:
    def test_unknown_units_are_refused(self, scene):
        scene([_Pocket()])
        res = cfp.handler(units="furlong")
        assert res["isError"] is True and "Unknown units" in error_message(res)
