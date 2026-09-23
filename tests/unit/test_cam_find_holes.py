# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Unit tests for ``cam_find_holes`` - CAM hole recognition as groups with per-segment handles.

Pinned here (no live Fusion): the recognizer's own grouping is published unchanged, is_through is
read off isThrough rather than inferred from a bottom diameter, a cone's half angle crosses in
degrees, the diameter window filters on top_diameter, the body census skips and counts what is not
a solid, an unreadable segment type reads 'unread', the per-group cap sets truncated, and every
segment face carries a handle.
"""

import math

import pytest

import adsk.cam
import adsk.fusion

from conftest import (BRepBody, BRepFace, FakePoint, FakeVector3D, MakeComp, MeshBody,
                      error_message, install, load_tool, make_design, payload as _payload,
                      _NamedCollection)

cfh = load_tool("cam_find_holes")


def _member(name):
    """One adsk.cam.HoleSegmentType member, read at call time so the double and the tool share it."""
    return getattr(adsk.cam.HoleSegmentType, name)


class _Segment:
    """A RecognizedHoleSegment (no live shape dump exists); kind=None makes its type read RAISE."""

    def __init__(self, kind=None, top=0.6, bottom=0.6, height=1.0, half_angle=0.0, faces=()):
        self._kind = kind
        self.topDiameter = top
        self.bottomDiameter = bottom
        self.height = height
        self.halfAngle = half_angle
        self.faces = list(faces)

    @property
    def holeSegmentType(self):
        if self._kind is None:
            raise RuntimeError("3 : the hole segment type is unavailable")
        return self._kind


class _Hole:
    """A RecognizedHole - no live shape dump exists, so this is a local double."""

    def __init__(self, segments=(), top=(1.0, 1.0, 1.0), bottom=(1.0, 1.0, 0.0),
                 axis=(0.0, 0.0, 1.0), top_diameter=0.6, bottom_diameter=0.6, total_length=1.0,
                 is_through=True, is_threaded=False):
        self._segments = list(segments)
        self.segmentCount = len(self._segments)
        self.top = FakePoint(*top)
        self.bottom = FakePoint(*bottom)
        self.axis = FakeVector3D(*axis)
        self.topDiameter = top_diameter
        self.bottomDiameter = bottom_diameter
        self.totalLength = total_length
        self.isThrough = is_through
        self.isThreaded = is_threaded
        self.hasErrors = False
        self.hasWarnings = False

    def segment(self, i):
        return self._segments[i]


class _Group:
    """A RecognizedHoleGroup: the count/item(i) collection of holes the recognizer called alike."""

    def __init__(self, holes):
        self._holes = list(holes)
        self.count = len(self._holes)

    def item(self, i):
        return self._holes[i]


def _face(token, centroid=(0.0, 0.0, 0.0)):
    """A BRepFace whose entityToken and centroid the published handle is minted from."""
    return BRepFace(None, centroid=FakePoint(*centroid), entity_token=token)


def _cylinder(diameter=0.6, faces=(), height=1.0):
    return _Segment(_member("HoleSegmentTypeCylinder"), top=diameter, bottom=diameter,
                    height=height, faces=faces)


@pytest.fixture
def scene(monkeypatch):
    """Build the design and stub the recognizer; the returned dict records what reached it."""
    seen = {}

    def build(groups=(), bodies=None, mesh_bodies=None):
        monkeypatch.setattr(adsk.fusion, "BRepBody", BRepBody, raising=False)
        monkeypatch.setattr(adsk.fusion, "MeshBody", MeshBody, raising=False)
        solids = [BRepBody(name="Solid1")] if bodies is None else list(bodies)
        design = make_design(comp=MakeComp(bodies=solids, mesh_bodies=mesh_bodies))
        install(cfh, design)

        def recognize(scan, include_partial):
            seen["bodies"] = list(scan)
            seen["include_partial"] = include_partial
            return _NamedCollection(list(groups)), None

        monkeypatch.setattr(cfh, "_recognize_groups", recognize)
        return seen
    return build


class TestGroups:
    def test_the_recognizers_own_grouping_is_published_unchanged(self, scene):
        # Two groups of the SAME diameter: a tool that re-grouped by geometry would merge them.
        scene([_Group([_Hole(), _Hole()]), _Group([_Hole()])])
        out = _payload(cfh.handler())
        assert out["group_count"] == 2 and out["hole_count"] == 3
        assert [g["hole_count"] for g in out["groups"]] == [2, 1]
        assert [g["index"] for g in out["groups"]] == [0, 1]

    def test_through_and_blind_are_read_from_is_through(self, scene):
        # A through hole whose bottomDiameter is 0 (a drill point) and a blind hole whose bottom
        # diameter equals its top: either diameter rule reads BOTH of these backwards.
        scene([_Group([_Hole(bottom_diameter=0.0, is_through=True)]),
               _Group([_Hole(bottom_diameter=0.6, is_through=False)])])
        out = _payload(cfh.handler())
        assert [g["is_through"] for g in out["groups"]] == [True, False]
        assert [g["bottom_diameter"] for g in out["groups"]] == [0.0, 6.0]

    def test_group_facts_are_scaled_into_the_requested_units(self, scene):
        scene([_Group([_Hole(top_diameter=0.6, total_length=1.0)])])
        out = _payload(cfh.handler(units="mm"))
        group = out["groups"][0]
        assert group["top_diameter"] == 6.0 and group["total_length"] == 10.0
        assert out["units"] == "mm"
        assert group["holes"][0]["top"] == {"x": 10.0, "y": 10.0, "z": 10.0}
        assert group["holes"][0]["axis"] == [0.0, 0.0, 1.0]


class TestSegments:
    def test_a_cone_half_angle_is_published_in_degrees(self, scene):
        cone = _Segment(_member("HoleSegmentTypeCone"), top=0.6, bottom=0.0, height=0.3004,
                        half_angle=1.0297)
        scene([_Group([_Hole(segments=[_cylinder(), cone])])])
        out = _payload(cfh.handler())
        segs = out["groups"][0]["segments"]
        assert [s["type"] for s in segs] == ["cylinder", "cone"]
        assert segs[1]["half_angle_deg"] == round(math.degrees(1.0297), 3)
        assert segs[0]["half_angle_deg"] == 0.0      # measured zero, never dropped to null

    def test_an_unreadable_segment_type_reads_unread(self, scene):
        scene([_Group([_Hole(segments=[_Segment(None, top=0.6)])])])
        out = _payload(cfh.handler())
        seg = out["groups"][0]["segments"][0]
        assert seg["type"] == "unread" and seg["top_diameter"] == 6.0

    def test_a_type_no_member_holds_reads_unread_rather_than_crossing_raw(self, scene):
        # A segment type this build reports and the tool has no name for is 'unread', never the raw
        # value: the payload is JSON, and an enum object echoed into it does not serialize.
        scene([_Group([_Hole(segments=[_Segment(99)])])])
        out = _payload(cfh.handler())
        assert out["groups"][0]["segments"][0]["type"] == "unread"

    def test_a_cylinder_segment_reads_cylinder_however_its_member_compares(self, scene):
        # The member is read through `raw is None`, never `not raw`: HoleSegmentTypeCylinder is 0
        # live, so a truthiness guard would publish every cylinder as 'unread'.
        scene([_Group([_Hole(segments=[_cylinder()])])])
        assert _payload(cfh.handler())["groups"][0]["segments"][0]["type"] == "cylinder"

    def test_a_handle_is_minted_for_every_segment_face_in_order(self, scene):
        top, through = _face("TOK_CBORE", (1.0, 2.0, 3.0)), _face("TOK_DRILL")
        scene([_Group([_Hole(segments=[_cylinder(1.08, faces=[top]),
                                       _cylinder(0.6, faces=[through])])])])
        out = _payload(cfh.handler())
        handles = out["groups"][0]["holes"][0]["faces"]
        assert handles == [["TOK_CBORE|@face:1.000000,2.000000,3.000000"],
                           ["TOK_DRILL|@face:0.000000,0.000000,0.000000"]]

    def test_a_segment_owning_two_faces_keeps_them_in_its_own_list(self, scene):
        # A notch severs a through hole's wall: ONE segment, TWO faces. Flattening them would put
        # two handles beside one segment and a caller could no longer map handle to segment.
        severed = _cylinder(0.6, faces=[_face("TOK_A", (1.0, 0.0, 0.0)),
                                        _face("TOK_B", (2.0, 0.0, 0.0))])
        scene([_Group([_Hole(segments=[severed])])])
        group = _payload(cfh.handler())["groups"][0]
        assert len(group["segments"]) == 1
        assert [len(f) for f in group["holes"][0]["faces"]] == [2]
        assert group["holes"][0]["faces"][0][0].startswith("TOK_A|@face:")

    def test_a_hole_whose_segment_count_does_not_read_publishes_no_faces(self, scene):
        hole = _Hole(segments=[_cylinder(faces=[_face("TOK")])])
        del hole.segmentCount
        scene([_Group([hole])])
        out = _payload(cfh.handler())
        # null, not []: nothing read the segments, so "this hole has no faces" was never measured.
        assert out["groups"][0]["segments"] is None
        assert out["groups"][0]["holes"][0]["faces"] is None

    def test_declared_outputs_present(self, scene):
        scene([_Group([_Hole(segments=[_cylinder(faces=[_face("TOK")])])])])
        out = _payload(cfh.handler())
        for r in cfh.RETURNS:
            assert r.assert_present(out) == "", r.key


class TestDiameterWindow:
    def test_a_group_at_the_minimum_is_kept(self, scene):
        scene([_Group([_Hole(top_diameter=0.6)]), _Group([_Hole(top_diameter=1.2)])])
        out = _payload(cfh.handler(min_diameter=6.0))
        assert [g["top_diameter"] for g in out["groups"]] == [6.0, 12.0]
        assert out["groups_out_of_range"] == 0

    def test_a_group_under_the_minimum_is_excluded_and_counted(self, scene):
        scene([_Group([_Hole(top_diameter=0.6)]), _Group([_Hole(top_diameter=1.2)])])
        out = _payload(cfh.handler(min_diameter=6.1))
        assert [g["top_diameter"] for g in out["groups"]] == [12.0]
        assert out["group_count"] == 1 and out["hole_count"] == 1
        assert out["groups_out_of_range"] == 1

    def test_a_group_over_the_maximum_is_excluded(self, scene):
        scene([_Group([_Hole(top_diameter=0.6)]), _Group([_Hole(top_diameter=1.2)])])
        out = _payload(cfh.handler(max_diameter=11.9))
        assert [g["top_diameter"] for g in out["groups"]] == [6.0]

    def test_no_window_publishes_no_out_of_range_count(self, scene):
        scene([_Group([_Hole(top_diameter=0.6)])])
        assert "groups_out_of_range" not in _payload(cfh.handler())

    def test_a_group_whose_diameter_did_not_read_is_never_excluded(self, scene):
        # Nothing measured this group's diameter, so the window cannot say it is out of range -
        # dropping it would hide a hole on a read that never happened.
        lead = _Hole(top_diameter=0.6)
        del lead.topDiameter
        scene([_Group([lead])])
        out = _payload(cfh.handler(min_diameter=99.0))
        assert out["group_count"] == 1 and out["groups"][0]["top_diameter"] is None
        assert out["groups_out_of_range"] == 0


class TestCap:
    def test_a_group_at_the_default_cap_is_complete(self, scene):
        scene([_Group([_Hole() for _ in range(cfh._HOLES_PER_GROUP_DEFAULT)])])
        group = _payload(cfh.handler())["groups"][0]
        assert len(group["holes"]) == cfh._HOLES_PER_GROUP_DEFAULT
        assert "truncated" not in group

    def test_one_hole_past_the_cap_truncates_and_still_counts_them_all(self, scene):
        scene([_Group([_Hole() for _ in range(cfh._HOLES_PER_GROUP_DEFAULT + 1)])])
        group = _payload(cfh.handler())["groups"][0]
        assert len(group["holes"]) == cfh._HOLES_PER_GROUP_DEFAULT
        assert group["truncated"] is True
        assert group["hole_count"] == cfh._HOLES_PER_GROUP_DEFAULT + 1

    def test_max_results_reaches_the_holes_past_the_default(self, scene):
        # The only tool that mints these handles: a group of like holes cannot be split by any
        # filter, so without this the rows past the default are unreachable.
        scene([_Group([_Hole() for _ in range(cfh._HOLES_PER_GROUP_DEFAULT + 10)])])
        group = _payload(cfh.handler(max_results=cfh._HOLES_PER_GROUP_DEFAULT + 10))["groups"][0]
        assert len(group["holes"]) == cfh._HOLES_PER_GROUP_DEFAULT + 10
        assert "truncated" not in group

    def test_max_results_is_clamped_to_the_ceiling(self, scene):
        scene([_Group([_Hole() for _ in range(cfh._HOLES_PER_GROUP_MAX + 1)])])
        group = _payload(cfh.handler(max_results=10_000))["groups"][0]
        assert len(group["holes"]) == cfh._HOLES_PER_GROUP_MAX
        assert group["truncated"] is True


class TestBodyCensus:
    def test_an_omitted_bodies_list_scans_the_solids_and_counts_what_it_skipped(self, scene):
        seen = scene([], bodies=[BRepBody(name="Solid1"), BRepBody(name="Solid2"),
                                 BRepBody(name="Sheet1", is_solid=False)],
                     mesh_bodies=[MeshBody(name="Scan1")])
        out = _payload(cfh.handler())
        assert [b.name for b in seen["bodies"]] == ["Solid1", "Solid2"]
        assert out["bodies_scanned"] == 2
        assert out["surface_bodies_skipped"] == 1 and out["mesh_bodies_skipped"] == 1

    def test_a_named_body_is_the_only_one_scanned(self, scene):
        seen = scene([], bodies=[BRepBody(name="Solid1"), BRepBody(name="Solid2")])
        out = _payload(cfh.handler(bodies=["Solid2"]))
        assert [b.name for b in seen["bodies"]] == ["Solid2"]
        assert out["bodies_scanned"] == 1 and out["surface_bodies_skipped"] == 0

    def test_a_design_with_no_solid_body_is_refused_naming_the_skips(self, scene):
        scene([], bodies=[BRepBody(name="Sheet1", is_solid=False)])
        res = cfh.handler()
        assert res["isError"] is True
        assert "1 surface" in error_message(res) and "No solid body" in error_message(res)

    def test_a_surface_body_named_in_bodies_is_refused_by_the_typed_kind(self, scene):
        scene([], bodies=[BRepBody(name="Solid1"), BRepBody(name="Sheet1", is_solid=False)])
        res = cfh.handler(bodies=["Sheet1"])
        assert res["isError"] is True and "SOLID" in error_message(res)


class TestRecognizerCall:
    def test_include_partial_false_asks_the_recognizer_to_filter_partial_holes(self, monkeypatch):
        captured = {}

        class _Input:
            """A RecognizedHolesInput double - the flag the recognizer is configured through."""
            filterPartialHoles = None

        def create():
            captured["input"] = _Input()
            return captured["input"]

        monkeypatch.setattr(adsk.cam.RecognizedHolesInput, "create", create, raising=False)
        monkeypatch.setattr(adsk.cam.RecognizedHoleGroup, "recognizeHoleGroupsWithInput",
                            lambda bodies, inp: _NamedCollection([]), raising=False)
        groups, err = cfh._recognize_groups([object()], False)
        assert err is None and groups is not None
        assert captured["input"].filterPartialHoles is True
        cfh._recognize_groups([object()], True)
        assert captured["input"].filterPartialHoles is False

    def test_a_raising_recognizer_is_an_error_naming_what_it_raised(self, monkeypatch):
        def boom(bodies, inp):
            raise RuntimeError("3 : InternalValidationError : res")

        monkeypatch.setattr(adsk.cam.RecognizedHolesInput, "create",
                            lambda: type("I", (), {"filterPartialHoles": None})(), raising=False)
        monkeypatch.setattr(adsk.cam.RecognizedHoleGroup, "recognizeHoleGroupsWithInput", boom,
                            raising=False)
        groups, err = cfh._recognize_groups([object()], False)
        assert groups is None and "InternalValidationError" in err
        assert "entitlement" not in err

    def test_an_unentitled_recognizer_names_the_extension_and_the_by_hand_route(self, monkeypatch):
        # measured on an install without the extension: the platform raises this text, and the
        # agent's next step is the typed face selection, not a retry
        def boom(bodies, inp):
            raise RuntimeError("3 : Requires the Manufacturing Extension to be active.")

        monkeypatch.setattr(adsk.cam.RecognizedHolesInput, "create",
                            lambda: type("I", (), {"filterPartialHoles": None})(), raising=False)
        monkeypatch.setattr(adsk.cam.RecognizedHoleGroup, "recognizeHoleGroupsWithInput", boom,
                            raising=False)
        groups, err = cfh._recognize_groups([object()], False)
        assert groups is None
        assert "Manufacturing Extension" in err and "entitlement" in err
        assert "find_geometry(kind='cylinder_face')" in err and "selection='holes'" in err


class TestGuards:
    def test_unknown_units_are_refused(self, scene):
        scene([_Group([_Hole()])])
        res = cfh.handler(units="furlong")
        assert res["isError"] is True and "Unknown units" in error_message(res)

    def test_the_note_points_at_the_selection_tool(self, scene):
        scene([_Group([_Hole(segments=[_cylinder(faces=[_face("TOK")])])])])
        out = _payload(cfh.handler())
        assert "cam_select_geometry" in out["note"]
