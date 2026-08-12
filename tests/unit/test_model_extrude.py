"""Unit tests for ``extrude.py`` — turn a sketch profile into a solid.

The logic worth pinning (no live Fusion): units → cm scaling, the zero-distance
and unknown-operation/units guards, sketch + profile resolution (named vs. most
recent; profile_index bounds), the operation-name → FeatureOperations mapping,
and that the distance handed to the API is scaled. The actual feature creation is
captured on a fake ExtrudeFeatures so we can assert the profile/operation/extent
passed in, without a real design.
"""

import json
import types

from conftest import BRepBody, load_tool, _NamedCollection

ex = load_tool("model_extrude")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeProfiles:
    def __init__(self, n, items=None):
        # Opaque tokens by default - a profile carries geometry only where a test reads it (the
        # containment walk behind the 'all' disclosure).
        self._items = list(items) if items is not None else [("profile", i) for i in range(n)]

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeSketchCurves:
    def __init__(self, n):
        self._n = n
    @property
    def count(self):
        return self._n
    def item(self, i):
        return ("curve", i)


class FakeSketch:
    def __init__(self, name, profile_count=1, curve_count=0, profiles=None):
        self.name = name
        self.profiles = FakeProfiles(profile_count, profiles)
        self.sketchCurves = FakeSketchCurves(curve_count)


class FakeSketches:
    def __init__(self, sketches):
        self._items = list(sketches)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def itemByName(self, name):
        for s in self._items:
            if s.name == name:
                return s
        return None


class FakeExtrudeInput:
    def __init__(self, profile, operation):
        self.profile = profile
        self.operation = operation
        self.distance_extent = None     # (isSymmetric, ValueInput) captured
        self.one_side = None
        self.symmetric_extent = None    # (distance, isFullLength, taper) captured
        self.all_extent = None          # direction captured (extent=through_all)
        self.two_sides_distance = None  # (distanceOne, distanceTwo) captured (extent=two_side)
        self.participantBodies = None
        self.isSolid = True             # default solid; surface path sets this False
        # EVERY extent setter is documented "Returns true if successful" - the fake returns the
        # bool as live does, so a handler that ignores it has something to be caught ignoring.
        self.next_result = True

    def setDistanceExtent(self, isSymmetric, distance):
        self.distance_extent = (isSymmetric, distance)
        return self.next_result

    def setOneSideExtent(self, extent, direction, taper=None):
        self.one_side = (extent, direction, taper)
        return self.next_result

    def setSymmetricExtent(self, distance, isFullLength, taper=None):
        self.symmetric_extent = (distance, isFullLength, taper)
        return self.next_result

    def setAllExtent(self, direction):
        self.all_extent = direction
        return self.next_result

    def setTwoSidesDistanceExtent(self, distanceOne, distanceTwo):
        self.two_sides_distance = (distanceOne, distanceTwo)
        return self.next_result


class FakeFeature:
    def __init__(self, name="Extrude1", is_solid=True):
        self.name = name
        self.isSolid = is_solid
        class _Bodies:
            count = 1
            def item(self, i):
                return type("B", (), {"name": "Body1"})()
        self.bodies = _Bodies()


class FakeExtrudeFeatures:
    def __init__(self):
        self.last_input = None
        self.added = False
        self.next_result = True   # propagated onto every extent setter of each new input
        self.on_add = None        # optional callable(inp) - simulates a live body mutation from add()

    def createInput(self, profile, operation):
        self.last_input = FakeExtrudeInput(profile, operation)
        self.last_input.next_result = self.next_result
        return self.last_input

    def add(self, inp):
        self.added = True
        if self.on_add:
            self.on_add(inp)
        # mirror the input's solid/surface mode onto the resulting feature (read back as is_solid)
        return FakeFeature(is_solid=getattr(inp, "isSolid", True))


class FakeRoot:
    def __init__(self, sketches, ef):
        self.sketches = FakeSketches(sketches)
        self.features = type("F", (), {"extrudeFeatures": ef})()
        self.open_profile_calls = []

    def createOpenProfile(self, curves, chain):
        self.open_profile_calls.append((curves, chain))
        return ("open_profile", curves, chain)


class FakeDesign:
    def __init__(self, sketches, ef):
        self.rootComponent = FakeRoot(sketches, ef)


def _install(sketches):
    ef = FakeExtrudeFeatures()
    design = FakeDesign(sketches, ef)
    ex.app = type("A", (), {"activeProduct": design})()
    ex._common.app = ex.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # FeatureOperations.* and ValueInput.createByReal must resolve.
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))

    class _OC:
        def __init__(self): self.items = []
        def add(self, x): self.items.append(x)
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    adsk.fusion.ToEntityExtentDefinition.create = staticmethod(lambda face, chained: ("to", face, chained))
    return ef


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_units(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", distance=5, units="furlongs")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_zero_distance(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", distance=0)
        assert res["isError"] is True and "non-zero 'distance'" in res["message"]

    def test_unknown_operation(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", distance=5, operation="weld")
        assert res["isError"] is True and "Unknown operation" in res["message"]

    def test_no_sketch_named(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="Nope", distance=5)
        assert res["isError"] is True and "No sketch named 'Nope'" in res["message"]

    def test_profile_index_out_of_range(self):
        _install([FakeSketch("S", profile_count=1)])
        res = ex.handler(sketch_name="S", distance=5, profile_index=3)
        assert res["isError"] is True and "out of range" in res["message"]

    def test_no_profile_in_sketch(self):
        # No closed profile AND no curves -> an error that points at the surface path, not a
        # flat "no closed profile" dead-end.
        _install([FakeSketch("S", profile_count=0)])
        res = ex.handler(sketch_name="S", distance=5)
        assert res["isError"] is True and "no curves" in res["message"]


# ── multi-profile selection (one extrude over N profiles, not N calls) ──
# One call with profile_index='all' (or a list) extrudes N profiles together, instead of N calls.

class TestProfileIndexResolution:
    def test_single_int(self):
        assert ex._resolve_profile_indices(2, 5) == ([2], None)

    def test_default_zero(self):
        assert ex._resolve_profile_indices(0, 1) == ([0], None)

    def test_all_keyword(self):
        assert ex._resolve_profile_indices("all", 6) == ([0, 1, 2, 3, 4, 5], None)

    def test_list(self):
        assert ex._resolve_profile_indices([3, 1, 1], 6) == ([1, 3], None)   # sorted + de-duped

    def test_comma_string(self):
        assert ex._resolve_profile_indices("0,2,4", 6) == ([0, 2, 4], None)

    def test_out_of_range_in_list_reports(self):
        idxs, err = ex._resolve_profile_indices([0, 9], 6)
        assert idxs is None and "out of range" in err and "9" in err

    def test_garbage_string(self):
        idxs, err = ex._resolve_profile_indices("xyz", 6)
        assert idxs is None and "not an int" in err


class TestProfileHandle:
    """profile_index may carry a profile HANDLE (entityToken from sketch_get) — _looks_like_handle
    routes it to ProfileRef instead of the index path. (The on-face disambiguation, done right.)"""

    def test_composite_handle_is_a_handle(self):
        assert ex._looks_like_handle("sometoken|@profile:0.4,0.2,0.0") is True

    def test_long_bare_token_is_a_handle(self):
        assert ex._looks_like_handle("/v4BAAAARlJLZXkAH4sIAAAA" + "x" * 40) is True

    def test_index_selectors_are_not_handles(self):
        assert ex._looks_like_handle(0) is False
        assert ex._looks_like_handle("0,2,3") is False
        assert ex._looks_like_handle("all") is False
        assert ex._looks_like_handle([0, 1]) is False


class TestAllIncludesEnclosedRegions:
    """'all' takes every closed region with no containment analysis, so a region ENCLOSED by another
    selected one is extruded too - measured: a frame sketch's 5 bays between the members filled in and
    the frame came out a solid plate, reported as plain success. The result cannot show that, so the
    note discloses it and points at the per-region selection."""

    def test_all_over_several_profiles_discloses_the_enclosed_regions(self):
        _install([FakeSketch("S", profile_count=6)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        note = out["note"]
        assert "'all' selected every closed region in this sketch (6)" in note
        assert "INCLUDING any region enclosed by another selected one" in note
        assert "this new acted on them as well" in note
        assert "sketch_get" in note and "handle" in note and "index list" in note

    def test_the_disclosure_never_claims_what_a_cut_did_to_those_regions(self):
        # a bay that FILLS on a 'new' extrude is material REMOVED on a cut - the sentence names the
        # operation that ran instead of asserting the new-body outcome for all four
        _install([FakeSketch("S", profile_count=6)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all", operation="cut"))
        disclosure = out["note"].split("'all' selected")[1]
        assert "this cut acted on them as well" in disclosure
        assert "solid" not in disclosure and "fill" not in disclosure

    def test_a_single_profile_sketch_is_not_lectured(self):
        # one region cannot enclose another - the sentence would be noise on every simple extrude
        _install([FakeSketch("S", profile_count=1)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "enclosed" not in out["note"]

    def test_an_explicit_index_list_is_not_lectured(self):
        # the caller named the regions one by one - it is 'all' that selects sight-unseen
        _install([FakeSketch("S", profile_count=6)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index=[0, 2, 4]))
        assert "enclosed" not in out["note"]

    def test_the_star_spelling_the_resolver_accepts_is_disclosed_too(self):
        _install([FakeSketch("S", profile_count=3)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="*"))
        assert "enclosed" in out["note"] and out["profiles_extruded"] == 3


class _Pt:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Box:
    """A BoundingBox3D as the containment walk reads it: minPoint / maxPoint."""
    def __init__(self, x0, y0, x1, y1, z0=0.0, z1=0.0):
        self.minPoint, self.maxPoint = _Pt(x0, y0, z0), _Pt(x1, y1, z1)


def _vbox(x0, z0, x1, z1):
    """A box on a VERTICAL sketch plane (XZ): every region in such a sketch shares one constant y,
    so containment there is decided by x and z."""
    return _Box(x0, 0.0, x1, 0.0, z0, z1)


class _Loop:
    """A ProfileLoop: isOuter plus the curves whose boxes give the loop its extent (a loop carries
    no bounding box of its own, so several curves each cover part of it)."""
    def __init__(self, *boxes, is_outer=False):
        self.isOuter = is_outer
        self.profileCurves = _NamedCollection(
            [type("PC", (), {"boundingBox": b})() for b in boxes])


class _Profile:
    def __init__(self, box, loops=()):
        self.boundingBox = box
        self.profileLoops = _NamedCollection(loops)


def _frame(*bays):
    """A frame outline whose material profile carries ONE inner loop per bay, plus the bay profiles
    themselves - the sketch topology a frame drawn with double lines produces. Profile 0 is the
    frame; profiles 1..N are the bays, in the order given."""
    frame = _Profile(_Box(0, 0, 100, 50), [_Loop(_Box(0, 0, 100, 50), is_outer=True)]
                     + [_Loop(_Box(*b)) for b in bays])
    return [frame] + [_Profile(_Box(*b), [_Loop(_Box(*b), is_outer=True)]) for b in bays]


def _vframe(*bays):
    """The same frame drawn on a VERTICAL plane: constant y, the regions spread over x and z. Each
    bay is given as (x0, z0, x1, z1)."""
    frame = _Profile(_vbox(0, 0, 100, 50), [_Loop(_vbox(0, 0, 100, 50), is_outer=True)]
                     + [_Loop(_vbox(*b)) for b in bays])
    return [frame] + [_Profile(_vbox(*b), [_Loop(_vbox(*b), is_outer=True)]) for b in bays]


class TestEnclosedRegionCount:
    """The 'all' disclosure COUNTS the regions that fill a hole of another selected region, so a
    frame extruded solid says how many bays it swallowed instead of only that it might have."""

    def test_the_note_counts_and_names_the_regions_that_filled_a_hole(self):
        _install([FakeSketch("S", profiles=_frame((10, 10, 40, 40), (60, 10, 90, 40)))])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING 2 region(s) enclosed by another selected one (profile index 1, 2)" in out["note"]
        assert out["enclosed_profile_indices"] == [1, 2]

    def test_regions_that_enclose_nothing_are_told_apart_from_an_unknown_count(self):
        # three side-by-side regions, no holes anywhere: the honest answer is 'none', not a warning
        side_by_side = [_Profile(_Box(x, 0, x + 5, 5), [_Loop(_Box(x, 0, x + 5, 5), is_outer=True)])
                        for x in (0, 10, 20)]
        _install([FakeSketch("S", profiles=side_by_side)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "none of them sits inside another selected region" in out["note"]
        assert "INCLUDING" not in out["note"] and "enclosed_profile_indices" not in out

    def test_an_outer_loop_is_not_a_hole(self):
        # the frame's OUTER loop spans every bay; counting it as an opening would call each bay
        # enclosed even for a sketch with no openings at all
        no_holes = [_Profile(_Box(0, 0, 100, 50), [_Loop(_Box(0, 0, 100, 50), is_outer=True)]),
                    _Profile(_Box(10, 10, 40, 40), [_Loop(_Box(10, 10, 40, 40), is_outer=True)])]
        _install([FakeSketch("S", profiles=no_holes)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "none of them sits inside another selected region" in out["note"]

    def test_a_region_that_overhangs_the_opening_is_not_counted(self):
        # each overhang leaves the opening on ONE side: a containment test missing that axis (or
        # that end of it) would report a region enclosed that sticks out of the frame
        for box in ((10, 10, 60, 40), (10, 10, 40, 45), (5, 10, 40, 40), (10, 5, 40, 40)):
            overhang = _frame((10, 10, 40, 40))
            overhang[1] = _Profile(_Box(*box), [_Loop(_Box(*box), is_outer=True)])
            _install([FakeSketch("S", profiles=overhang)])
            out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
            assert "none of them sits inside another selected region" in out["note"], box

    def test_an_opening_drawn_as_several_curves_takes_their_whole_extent(self):
        # a loop is a chain of curves, each covering part of the opening - reading one of them as
        # the hole's extent shrinks the opening and loses the bay that fills it
        split_loop = _Profile(_Box(0, 0, 100, 50),
                              [_Loop(_Box(0, 0, 100, 50), is_outer=True),
                               _Loop(_Box(10, 10, 25, 40), _Box(25, 10, 40, 40))])
        bay = _Profile(_Box(10, 10, 40, 40), [_Loop(_Box(10, 10, 40, 40), is_outer=True)])
        _install([FakeSketch("S", profiles=[split_loop, bay])])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING 1 region(s) enclosed by another selected one (profile index 1)" in out["note"]

    def test_a_bay_matching_its_opening_exactly_still_counts(self):
        # a bay's extent and the hole it fills are the SAME curves - an exclusive comparison would
        # count zero on every real frame
        same = (10.0, 10.0, 0.0, 40.0, 40.0, 0.0)
        assert ex._within(same, same) is True

    def test_a_profile_whose_geometry_cannot_be_read_yields_no_count(self):
        # opaque profiles: the note must fall back to what it could not rule out, never to 'none'
        _install([FakeSketch("S", profile_count=6)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING any region enclosed by another selected one" in out["note"]
        assert "none of them sits inside" not in out["note"]

    def test_one_unreadable_profile_withdraws_the_verdict_for_the_whole_sketch(self):
        mixed = _frame((10, 10, 40, 40))
        mixed.append(("profile", 2))            # a region whose extent cannot be read
        _install([FakeSketch("S", profiles=mixed)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING any region enclosed by another selected one" in out["note"]

    def test_a_vertical_plane_sketch_is_judged_on_all_three_axes(self):
        # every region of a vertical-plane sketch shares one constant coordinate, so a containment
        # test that skips an axis matches on the remaining interval alone: the tab parked far above
        # the frame shares the bay's x span and would be called enclosed
        vertical = _vframe((10, 10, 40, 40))
        vertical.append(_Profile(_vbox(10, 150, 40, 180),
                                 [_Loop(_vbox(10, 150, 40, 180), is_outer=True)]))
        _install([FakeSketch("S", profiles=vertical)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING 1 region(s) enclosed by another selected one (profile index 1)" in out["note"]
        assert out["enclosed_profile_indices"] == [1]

    def test_an_unreadable_is_outer_withdraws_the_verdict(self):
        # isOuter decides whether a loop IS an opening: read it as 'outer' on a raise and the hole
        # vanishes, so the payload would claim nothing is enclosed while a bay sits in one
        class _RaisingOuter:
            profileCurves = _NamedCollection(
                [type("PC", (), {"boundingBox": _Box(10, 10, 40, 40)})()])

            @property
            def isOuter(self):
                raise RuntimeError("3 : bad index parameter")

        frame = _Profile(_Box(0, 0, 100, 50), [_Loop(_Box(0, 0, 100, 50), is_outer=True),
                                               _RaisingOuter()])
        bay = _Profile(_Box(10, 10, 40, 40), [_Loop(_Box(10, 10, 40, 40), is_outer=True)])
        _install([FakeSketch("S", profiles=[frame, bay])])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING any region enclosed by another selected one" in out["note"]
        assert "none of them sits inside" not in out["note"]
        assert "enclosed_profile_indices" not in out

    def test_a_loop_the_collection_will_not_hand_over_withdraws_the_verdict(self):
        # the loop that raises IS the opening: skipping it drops the hole, and the payload would
        # tell a caller nothing is enclosed while the bay sits inside that very loop
        class _RaisingLoops:
            def __init__(self, items):
                self._items = list(items)

            @property
            def count(self):
                return len(self._items)

            def item(self, i):
                if i == 1:
                    raise RuntimeError("3 : bad index parameter")
                return self._items[i]

        frame = _Profile(_Box(0, 0, 100, 50))
        frame.profileLoops = _RaisingLoops([_Loop(_Box(0, 0, 100, 50), is_outer=True),
                                            _Loop(_Box(10, 10, 40, 40))])
        bay = _Profile(_Box(10, 10, 40, 40), [_Loop(_Box(10, 10, 40, 40), is_outer=True)])
        _install([FakeSketch("S", profiles=[frame, bay])])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING any region enclosed by another selected one" in out["note"]
        assert "none of them sits inside" not in out["note"]
        assert "enclosed_profile_indices" not in out

    def test_an_unreadable_curve_box_withdraws_the_verdict(self):
        # a hole measured from the curves that happened to read is a SHRUNK hole - it would report
        # the bay that fills it as enclosed by nothing
        class _RaisingCurve:
            @property
            def boundingBox(self):
                raise RuntimeError("3 : bad index parameter")

        hole = _Loop(_Box(10, 10, 25, 40))
        hole.profileCurves = _NamedCollection(
            [type("PC", (), {"boundingBox": _Box(10, 10, 25, 40)})(), _RaisingCurve()])
        frame = _Profile(_Box(0, 0, 100, 50), [_Loop(_Box(0, 0, 100, 50), is_outer=True), hole])
        bay = _Profile(_Box(10, 10, 40, 40), [_Loop(_Box(10, 10, 40, 40), is_outer=True)])
        _install([FakeSketch("S", profiles=[frame, bay])])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING any region enclosed by another selected one" in out["note"]
        assert "none of them sits inside" not in out["note"]

    def test_a_hole_whose_curves_do_not_read_withdraws_the_verdict(self):
        # no curve of the loop answers, so the opening has no measurable extent at all - dropping
        # the hole here is the same false 'nothing is enclosed' as shrinking it
        blind_hole = _Loop(_Box(10, 10, 40, 40))
        blind_hole.profileCurves = _NamedCollection([])
        frame = _Profile(_Box(0, 0, 100, 50),
                         [_Loop(_Box(0, 0, 100, 50), is_outer=True), blind_hole])
        bay = _Profile(_Box(10, 10, 40, 40), [_Loop(_Box(10, 10, 40, 40), is_outer=True)])
        _install([FakeSketch("S", profiles=[frame, bay])])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING any region enclosed by another selected one" in out["note"]
        assert "none of them sits inside" not in out["note"]

    def test_a_coordinate_that_is_not_a_number_withdraws_the_verdict(self):
        # an unmodelled/absent coordinate reads as something that is not a number; treating it as a
        # zero would place the region at the origin and invent a containment answer
        bad = _Box(10, 10, 40, 40)
        bad.minPoint = type("P", (), {"x": "10", "y": 10.0, "z": 0.0})()
        profiles = _frame((10, 10, 40, 40))
        profiles[1] = _Profile(bad, [_Loop(_Box(10, 10, 40, 40), is_outer=True)])
        _install([FakeSketch("S", profiles=profiles)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert "INCLUDING any region enclosed by another selected one" in out["note"]
        assert "enclosed_profile_indices" not in out

    def test_a_hole_read_that_raises_cannot_sink_the_extrude(self):
        class _Exploding:
            @property
            def boundingBox(self):
                raise RuntimeError("3 : bad index parameter")

            @property
            def profileLoops(self):
                raise RuntimeError("3 : bad index parameter")

        _install([FakeSketch("S", profiles=[_Exploding(), _Exploding()])])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert out["extruded"] is True and out["profiles_extruded"] == 2

    def test_the_count_is_over_the_selection_not_the_whole_sketch(self):
        # the two bays alone enclose nothing - the profile whose holes they fill was NOT selected,
        # so a walk over every profile in the sketch would over-report them
        sketch = FakeSketch("S", profiles=_frame((10, 10, 40, 40), (60, 10, 90, 40)))
        _install([sketch])
        assert ex._enclosed_regions(sketch.profiles, [1, 2]) == []
        assert ex._enclosed_regions(sketch.profiles, [0, 1, 2]) == [1, 2]


class TestMultiProfileExtrude:
    def test_all_profiles_extruded_in_one_call(self):
        _install([FakeSketch("S", profile_count=4)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert out["profiles_extruded"] == 4
        assert out["profile_index"] == [0, 1, 2, 3]

    def test_list_of_profiles(self):
        _install([FakeSketch("S", profile_count=6)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index=[1, 3, 5]))
        assert out["profiles_extruded"] == 3 and out["profile_index"] == [1, 3, 5]

    def test_single_still_reports_scalar(self):
        _install([FakeSketch("S", profile_count=3)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index=2))
        assert out["profiles_extruded"] == 1 and out["profile_index"] == 2


# ── behaviour ────────────────────────────────────────────────────────────────

class TestExtrude:
    def test_basic_new_body_scales_distance_to_cm(self):
        ef = _install([FakeSketch("Base")])
        out = _payload(ex.handler(sketch_name="Base", distance=6, units="mm", operation="new"))
        assert out["extruded"] is True
        assert out["operation"] == "new"
        assert out["result_bodies"] == ["Body1"]
        # 6 mm -> 0.6 cm handed to the API
        sym, dist = ef.last_input.distance_extent
        assert dist[0] == "real" and abs(dist[1] - 0.6) < 1e-9
        assert sym is False
        assert ef.last_input.operation == "NewBodyFeatureOperation"

    def test_inch_scaling(self):
        ef = _install([FakeSketch("Base")])
        _payload(ex.handler(sketch_name="Base", distance=1, units="in"))
        _, dist = ef.last_input.distance_extent
        assert dist == ("real", 2.54)

    def test_most_recent_sketch_when_unnamed(self):
        ef = _install([FakeSketch("First"), FakeSketch("Last")])
        out = _payload(ex.handler(distance=5))
        assert out["sketch"] == "Last"     # most recent

    def test_operation_mapping_cut(self):
        ef = _install([FakeSketch("S")])
        _payload(ex.handler(sketch_name="S", distance=5, operation="cut"))
        assert ef.last_input.operation == "CutFeatureOperation"

    def test_symmetric_flag_passed(self):
        ef = _install([FakeSketch("S")])
        _payload(ex.handler(sketch_name="S", distance=5, symmetric=True))
        sym, _ = ef.last_input.distance_extent
        assert sym is True

    def test_negative_distance_allowed(self):
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", distance=-4, units="mm"))
        _, dist = ef.last_input.distance_extent
        assert dist[0] == "real" and abs(dist[1] - (-0.4)) < 1e-9
        assert out["distance"] == -4


# ── taper (draft angle) ─────────────────────────────────────────────────────────────────────────
# A one-sided extrude with taper_deg builds a DistanceExtentDefinition + a 'N deg' taper ValueInput and
# calls setOneSideExtent(extent, dir, taper). A SYMMETRIC extrude with taper needs setSymmetricExtent
# (which carries a taper) - setDistanceExtent cannot, so the taper would otherwise be silently dropped.
# Pinned: the one-sided taper path, the distance still scaled onto the DistanceExtentDefinition, and that
# symmetric+taper applies the taper via setSymmetricExtent instead of dropping it.

class TestTaper:
    def test_taper_uses_one_side_extent_with_deg_string(self):
        ef = _install([FakeSketch("S")])
        import adsk.fusion
        adsk.fusion.DistanceExtentDefinition.create = staticmethod(lambda v: ("dist_ext", v))
        out = _payload(ex.handler(sketch_name="S", distance=6, units="mm", taper_deg=3))
        # taper path: one_side set, NOT the plain distance extent
        assert ef.last_input.one_side is not None
        assert ef.last_input.distance_extent is None
        extent, direction, taper = ef.last_input.one_side
        # distance still scaled to cm onto the DistanceExtentDefinition (6mm -> 0.6cm)
        assert extent[0] == "dist_ext" and extent[1][0] == "real" and abs(extent[1][1] - 0.6) < 1e-9
        # taper passed as a 'N deg' string ValueInput
        assert taper[0] == "str" and "3" in taper[1] and "deg" in taper[1]
        assert out["taper_deg"] == 3.0

    def test_symmetric_with_taper_uses_symmetric_extent(self):
        # symmetric + taper: setDistanceExtent carries no taper, so the handler must use
        # setSymmetricExtent (which does) - not drop the taper onto a plain distance extent.
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", distance=5, units="mm", taper_deg=10, symmetric=True))
        assert ef.last_input.distance_extent is None      # NOT the taper-dropping path
        dist, is_full, taper = ef.last_input.symmetric_extent
        assert dist[0] == "real" and abs(dist[1] - 0.5) < 1e-9   # 5 mm -> 0.5 cm (per-side half-length)
        assert is_full is False                                  # 'distance' is per-side, matching setDistanceExtent
        assert taper[0] == "str" and "10" in taper[1] and "deg" in taper[1]
        assert out["taper_deg"] == 10.0                   # the reported taper is the one applied

    def test_zero_taper_is_plain_distance(self):
        ef = _install([FakeSketch("S")])
        _payload(ex.handler(sketch_name="S", distance=5, taper_deg=0))
        assert ef.last_input.distance_extent is not None
        assert ef.last_input.one_side is None


# ── as_surface (open-profile extrude into a surface wall) ───────────────────
# The additive surface path: as_surface=True (or an open path with no closed profile) builds via
# createOpenProfile + ExtrudeFeatureInput.isSolid=False, and EVERY result reports is_solid.

class TestAsSurface:
    def test_default_extrude_is_solid_unchanged(self):
        # as_surface defaults False -> today's behavior: closed profile -> solid, is_solid True.
        ef = _install([FakeSketch("S", profile_count=1)])
        out = _payload(ex.handler(sketch_name="S", distance=5))
        assert out["as_surface"] is False
        assert out["is_solid"] is True
        assert ef.last_input.isSolid is True            # never touched the surface path
        assert ef.last_input.distance_extent is not None

    def test_as_surface_true_sets_isSolid_false(self):
        ef = _install([FakeSketch("S", profile_count=1, curve_count=4)])
        out = _payload(ex.handler(sketch_name="S", distance=5, as_surface=True))
        assert out["as_surface"] is True
        assert out["is_solid"] is False
        assert ef.last_input.isSolid is False
        assert "SURFACE" in out["note"]

    def test_open_path_auto_surface_when_no_closed_profile(self):
        # No closed profile but open curves exist -> auto surface, not a dead-end error.
        ef = _install([FakeSketch("S", profile_count=0, curve_count=2)])
        out = _payload(ex.handler(sketch_name="S", distance=5))
        assert out["as_surface"] is True
        assert out["is_solid"] is False
        assert ef.last_input.isSolid is False

    def test_no_profile_and_no_curves_points_at_surface_path(self):
        # No closed profile AND no curves -> error that mentions the surface path / curves.
        _install([FakeSketch("S", profile_count=0, curve_count=0)])
        res = ex.handler(sketch_name="S", distance=5)
        assert res["isError"] is True
        assert "surface" in res["message"].lower() or "open path" in res["message"].lower()


# ── to_object extent (extrude up to a face handle) ──────────────────────────

class _FakeFaceEnt:
    pass


class _FakeBody:
    def __init__(self, name):
        self.name = name


def _install_geom(faces=None, bodies=None):
    """Install + wire the _common seam so to_object faces / target_bodies resolve.

    The handler resolves its design via _common.design() - the SAME seam _inputs uses for handle/body
    resolution - so there must be ONE design fake serving both. So we take _install's rich FakeDesign
    (it has features/sketches the handler needs) and EXTEND its root with findEntityByToken + a
    body-by-name lookup, then point both _common.design and _inputs._common.design at it."""
    ef = _install([FakeSketch("S")])
    import adsk.fusion
    adsk.fusion.BRepFace = _FakeFaceEnt
    adsk.fusion.BRepBody = _FakeBody
    faces = faces or {}
    bodies = bodies or {}
    handle_map = dict(faces); handle_map.update(bodies)
    design = ex.app.activeProduct                 # the rich FakeDesign from _install
    root = design.rootComponent
    root.bRepBodies = type("BB", (), {"itemByName": staticmethod(lambda n: bodies.get(n))})()
    design.findEntityByToken = lambda t, hm=handle_map: ([hm[t]] if t in hm else [])
    ex._common.design = lambda: design
    ex._common.target_component = lambda x: root
    ex._inputs._common.design = lambda: design
    ex._inputs._common.target_component = lambda x: root
    return ef


class TestToObject:
    def test_extrude_to_face_uses_to_entity_extent(self):
        face = _FakeFaceEnt()
        ef = _install_geom(faces={"F": face})
        out = _payload(ex.handler(sketch_name="S", to_object="F"))
        assert out["extent"] == "to_object"
        assert out["distance"] is None
        # a ToEntityExtentDefinition was used (one_side set, distance_extent not)
        assert ef.last_input.one_side is not None
        assert ef.last_input.distance_extent is None

    def test_to_object_overrides_distance(self):
        face = _FakeFaceEnt()
        ef = _install_geom(faces={"F": face})
        out = _payload(ex.handler(sketch_name="S", distance=999, to_object="F"))
        assert out["extent"] == "to_object" and ef.last_input.distance_extent is None

    def test_bad_to_object_handle_errors(self):
        _install_geom(faces={})
        res = ex.handler(sketch_name="S", to_object="missing")
        assert res["isError"] is True
        assert "handle did not resolve" in res["message"]


# ── target_bodies cut scoping (prevents bleed-through) ──────────────────────

class TestTargetBodies:
    def test_cut_scoped_to_bodies(self):
        b = _FakeBody("KeepMe")
        ef = _install_geom(bodies={"KeepMe": b})
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut", target_bodies="KeepMe"))
        assert ef.last_input.participantBodies == [b]
        assert out["scoped_to_bodies"] == ["KeepMe"]

    def test_target_bodies_by_handle(self):
        h = "/v" + "B" * 70
        b = _FakeBody("FromHandle")
        ef = _install_geom(bodies={h: b})
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="join", target_bodies=h))
        assert ef.last_input.participantBodies == [b]

    def test_target_bodies_rejected_on_new(self):
        b = _FakeBody("X")
        _install_geom(bodies={"X": b})
        res = ex.handler(sketch_name="S", distance=5, operation="new", target_bodies="X")
        assert res["isError"] is True and "cut/join/intersect" in res["message"]

    def test_bad_target_body_errors(self):
        _install_geom(bodies={"X": _FakeBody("X")})
        res = ex.handler(sketch_name="S", distance=5, operation="cut", target_bodies="Nope")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_scoped_echo_qualifies_same_named_bodies_by_owning_component(self):
        # Fusion auto-names every body 'Body1' by default, so two DIFFERENT target bodies that
        # happen to share that name must still read as distinguishable entries in
        # 'scoped_to_bodies' - otherwise a cut mis-targeted onto the wrong body's component is
        # invisible in the echo (the live defect this pins).
        h1, h2 = "/v" + "A" * 70, "/v" + "B" * 70
        carrier = _FakeBody("Body1")
        carrier.parentComponent = type("C", (), {"name": "Carrier"})()
        inner_ring = _FakeBody("Body1")
        inner_ring.parentComponent = type("C", (), {"name": "Inner_Ring"})()
        _install_geom(bodies={h1: carrier, h2: inner_ring})
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut",
                                  target_bodies=[h1, h2]))
        assert out["scoped_to_bodies"] == ["Carrier/Body1", "Inner_Ring/Body1"]
        assert out["scoped_to_bodies"][0] != out["scoped_to_bodies"][1]


# ── extent selector guards (cross-extent conflicts) ──────────────────────────
# 'extent' picks the depth style; these pin the cross-extent validation that must fire BEFORE any
# ExtrudeFeatureInput setter runs - an unknown value, 'to_object' paired with an extent that doesn't
# use it, and 'to_face' missing its required 'to_object'.

class TestExtentSetterRefusals:
    """Each extent setter answers "did it take". A false answer left on the floor means add() builds
    the feature on its DEFAULT extent while the payload reports the requested one."""

    def test_a_refused_distance_extent_is_an_error_and_nothing_is_added(self):
        ef = _install([FakeSketch("S")])
        ef.next_result = False
        res = ex.handler(sketch_name="S", profile_index=0, distance=10)
        assert res["isError"] is True and "distance extent" in res["message"]
        assert ef.added is False

    def test_a_refused_symmetric_tapered_extent_is_an_error(self):
        ef = _install([FakeSketch("S")])
        ef.next_result = False
        res = ex.handler(sketch_name="S", profile_index=0, distance=10, symmetric=True, taper_deg=3)
        assert res["isError"] is True and "symmetric tapered extent" in res["message"]
        assert ef.added is False

    def test_a_refused_one_sided_tapered_extent_is_an_error(self):
        ef = _install([FakeSketch("S")])
        ef.next_result = False
        res = ex.handler(sketch_name="S", profile_index=0, distance=10, taper_deg=3)
        assert res["isError"] is True and "one-sided tapered extent" in res["message"]
        assert ef.added is False


class TestExtentGuards:
    def test_unknown_extent_value(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", distance=5, extent="bogus")
        assert res["isError"] is True and "Unknown extent" in res["message"]

    def test_to_object_rejected_with_through_all(self):
        _install_geom(faces={"F": _FakeFaceEnt()})
        res = ex.handler(sketch_name="S", extent="through_all", to_object="F")
        assert res["isError"] is True
        assert "not used with extent='through_all'" in res["message"]

    def test_to_object_rejected_with_two_side(self):
        _install_geom(faces={"F": _FakeFaceEnt()})
        res = ex.handler(sketch_name="S", extent="two_side", distance=1, distance2=1, to_object="F")
        assert res["isError"] is True
        assert "not used with extent='two_side'" in res["message"]

    def test_to_face_without_to_object_errors(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", extent="to_face")
        assert res["isError"] is True and "to_face' needs 'to_object'" in res["message"]

    def test_to_face_alias_behaves_like_to_object(self):
        # extent='to_face' is the explicit spelling of the legacy to_object-alone shorthand - same
        # ToEntityExtentDefinition path, same reported 'extent' value (back-compat).
        face = _FakeFaceEnt()
        ef = _install_geom(faces={"F": face})
        out = _payload(ex.handler(sketch_name="S", extent="to_face", to_object="F"))
        assert out["extent"] == "to_object"
        assert ef.last_input.one_side is not None


# ── through_all extent (setAllExtent) ────────────────────────────────────────
# 'distance' carries no magnitude for through_all - only its SIGN (direction hint); symmetric=true
# goes both ways. Pinned: the direction mapping, the no-taper guard, and the returned-false path.

class TestThroughAll:
    def test_default_direction_is_positive(self):
        import adsk.fusion
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", extent="through_all"))
        assert ef.last_input.all_extent == adsk.fusion.ExtentDirections.PositiveExtentDirection
        assert out["extent"] == "through_all" and out["direction"] == "positive"
        assert out["distance"] is None

    def test_negative_distance_picks_negative_direction(self):
        import adsk.fusion
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", extent="through_all", distance=-5))
        assert ef.last_input.all_extent == adsk.fusion.ExtentDirections.NegativeExtentDirection
        assert out["direction"] == "negative"

    def test_positive_distance_picks_positive_direction(self):
        import adsk.fusion
        ef = _install([FakeSketch("S")])
        _payload(ex.handler(sketch_name="S", extent="through_all", distance=5))
        assert ef.last_input.all_extent == adsk.fusion.ExtentDirections.PositiveExtentDirection

    def test_symmetric_picks_symmetric_direction_regardless_of_distance_sign(self):
        import adsk.fusion
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", extent="through_all", symmetric=True, distance=-9))
        assert ef.last_input.all_extent == adsk.fusion.ExtentDirections.SymmetricExtentDirection
        assert out["direction"] == "symmetric"

    def test_rejects_taper(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", extent="through_all", taper_deg=5)
        assert res["isError"] is True
        assert "through_all" in res["message"] and "taper" in res["message"]

    def test_setAllExtent_false_is_reported(self):
        ef = _install([FakeSketch("S")])
        ef.next_result = False
        res = ex.handler(sketch_name="S", extent="through_all")
        assert res["isError"] is True and "setAllExtent returned false" in res["message"]


# ── through_all CUT/INTERSECT volume read-back (the rung-4 honesty check) ───
# 'All' extends until it exits the geometry (no partial depth), so ANY volume drop on the body(s) it
# acted on proves the cut went all the way through - a zero delta means the extent missed the body
# entirely (a silent no-op the API would otherwise report as a false ok).

class TestThroughAllVolumeCheck:
    def test_target_bodies_scoped_volume_removed_is_reported(self):
        # _install_geom's target_bodies path maps adsk.fusion.BRepBody -> _FakeBody (isinstance-
        # checked by BodyRef); volume/entityToken are just ad-hoc attributes on that same fake.
        b = _FakeBody("KeepMe")
        b.volume, b.entityToken = 50.0, "KeepMe"
        ef = _install_geom(bodies={"KeepMe": b})
        ef.on_add = lambda inp: setattr(b, "volume", 10.0)   # simulate the cut removing material
        out = _payload(ex.handler(sketch_name="S", operation="cut", extent="through_all",
                                  distance=-1, target_bodies="KeepMe"))
        assert out["through_all_volume_removed_cm3"] == {"KeepMe": 40.0}

    def test_solo_body_in_component_is_the_implied_target(self):
        # No target_bodies given, but exactly ONE solid body exists - the unambiguous single-part
        # case model_shell's own default-body resolution mirrors.
        ef = _install([FakeSketch("S")])
        root = ex.app.activeProduct.rootComponent
        body = BRepBody("Box1", volume=100.0)
        root.bRepBodies = _NamedCollection([body])
        ef.on_add = lambda inp: setattr(body, "volume", 40.0)
        out = _payload(ex.handler(sketch_name="S", operation="cut", extent="through_all", distance=-1))
        assert out["through_all_volume_removed_cm3"] == {"Box1": 60.0}

    def test_no_volume_change_is_reported_as_error(self):
        _install([FakeSketch("S")])
        root = ex.app.activeProduct.rootComponent
        body = BRepBody("Box1", volume=100.0)
        root.bRepBodies = _NamedCollection([body])
        # on_add left unset -> body.volume never changes -> the through_all cut silently missed it
        res = ex.handler(sketch_name="S", operation="cut", extent="through_all", distance=1)
        assert res["isError"] is True
        assert "removed no material" in res["message"] and "Box1" in res["message"]

    def test_several_bodies_with_no_target_bodies_skips_the_check(self):
        # Several bodies and no target_bodies -> which one(s) intersect is ambiguous from here, so no
        # guessed target is checked (Fusion's own intersection search still runs the real cut).
        ef = _install([FakeSketch("S")])
        root = ex.app.activeProduct.rootComponent
        root.bRepBodies = _NamedCollection([BRepBody("A", volume=10.0), BRepBody("B", volume=20.0)])
        out = _payload(ex.handler(sketch_name="S", operation="cut", extent="through_all", distance=-1))
        assert "through_all_volume_removed_cm3" not in out

    def test_new_operation_skips_the_check(self):
        # extent=through_all with operation='new' has no participant body to check a REMOVAL on.
        ef = _install([FakeSketch("S")])
        root = ex.app.activeProduct.rootComponent
        root.bRepBodies = _NamedCollection([BRepBody("Box1", volume=100.0)])
        out = _payload(ex.handler(sketch_name="S", extent="through_all"))   # operation defaults 'new'
        assert "through_all_volume_removed_cm3" not in out


# ── two_side extent (setTwoSidesDistanceExtent) ──────────────────────────────
# Independent distances per side, no taper, no 'symmetric' (equal distance/distance2 already
# expresses a symmetric two-sided extrude without a second flag).

class TestTwoSide:
    def test_calls_setTwoSidesDistanceExtent_with_scaled_distances(self):
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", extent="two_side", distance=10, distance2=5, units="mm"))
        d1, d2 = ef.last_input.two_sides_distance
        assert d1[0] == "real" and abs(d1[1] - 1.0) < 1e-9    # 10mm -> 1.0cm
        assert d2[0] == "real" and abs(d2[1] - 0.5) < 1e-9    # 5mm -> 0.5cm
        assert out["extent"] == "two_side"
        assert out["distance"] == 10.0 and out["distance2"] == 5.0

    def test_rejects_taper(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", extent="two_side", distance=10, distance2=5, taper_deg=3)
        assert res["isError"] is True
        assert "two_side" in res["message"] and "taper" in res["message"]

    def test_rejects_symmetric(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", extent="two_side", distance=10, distance2=5, symmetric=True)
        assert res["isError"] is True and "symmetric" in res["message"]

    def test_needs_both_distances_nonzero(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", extent="two_side", distance=10, distance2=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_setTwoSidesDistanceExtent_false_is_reported(self):
        ef = _install([FakeSketch("S")])
        ef.next_result = False
        res = ex.handler(sketch_name="S", extent="two_side", distance=10, distance2=5)
        assert res["isError"] is True
        assert "setTwoSidesDistanceExtent returned false" in res["message"]


# ── parameter-expression distance (createByString) + the model-parameter linkage read-back ──────────
# 'distance' accepts a parameter EXPRESSION string ('StockZ/2', '25 mm') routed through
# ValueInput.createByString (ties the feature to a parameter), validated via the units engine so an
# unresolvable one is refused BY NAME. And every extrude NAMES the model parameters (dNN) it created so
# the param_set retarget path is discoverable without fishing through param_get.

class _FakeUnitsMgr:
    """A units engine whose evaluateExpression only resolves a known set - a bad reference RAISES (the
    live FusionUnitsManager errors on an unresolvable/dimension-incompatible expression)."""
    defaultLengthUnits = "mm"

    def __init__(self, valid=("25 mm", "StockZ/2", "TestLen * 2")):
        self._valid = set(valid)

    def evaluateExpression(self, expr, units=None):
        if expr not in self._valid:
            raise RuntimeError(f"unresolved parameter in '{expr}'")
        return 2.5


class _MP:
    def __init__(self, name):
        self.name = name


class _Ext:
    def __init__(self, dist_name):
        self.distance = _MP(dist_name)


class _ParamFeature(FakeFeature):
    """A feature exposing the extent/taper model PARAMETERS the linkage read-back names."""
    def __init__(self, dist="d1", taper="d2", dist2=None, taper2=None, **kw):
        super().__init__(**kw)
        self.extentOne = _Ext(dist)
        self.taperAngleOne = _MP(taper)
        self.hasTwoExtents = dist2 is not None
        self.extentTwo = _Ext(dist2) if dist2 is not None else None
        self.taperAngleTwo = _MP(taper2) if taper2 is not None else None


def _with_units_mgr(mgr=None):
    """Attach a fake units engine to the installed design so string expressions can evaluate."""
    ex.app.activeProduct.unitsManager = mgr or _FakeUnitsMgr()


class TestExpressionDistance:
    def test_string_expression_uses_createByString_not_scaled_real(self):
        ef = _install([FakeSketch("S")])
        _with_units_mgr()
        out = _payload(ex.handler(sketch_name="S", distance="25 mm", units="mm"))
        sym, dist = ef.last_input.distance_extent
        assert dist == ("str", "25 mm")        # createByString - NOT a scaled createByReal
        assert out["distance"] == "25 mm"      # echoed as the expression, not a rounded number

    def test_expression_references_a_parameter(self):
        ef = _install([FakeSketch("S")])
        _with_units_mgr()
        _payload(ex.handler(sketch_name="S", distance="StockZ/2"))
        _sym, dist = ef.last_input.distance_extent
        assert dist == ("str", "StockZ/2")

    def test_numeric_string_is_a_literal_scaled_via_createByReal(self):
        # a PLAIN numeric string is a literal, not an expression - it still scales through createByReal.
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", distance="6", units="mm"))
        _sym, dist = ef.last_input.distance_extent
        assert dist[0] == "real" and abs(dist[1] - 0.6) < 1e-9   # "6" mm -> 0.6 cm, the literal path
        assert out["distance"] == 6.0

    def test_unresolvable_expression_refused_by_name(self):
        _install([FakeSketch("S")])
        _with_units_mgr()                       # only the known set evaluates; this one does not
        res = ex.handler(sketch_name="S", distance="NoSuchParam * 2")
        assert res["isError"] is True
        assert "NoSuchParam * 2" in res["message"]

    def test_two_side_accepts_expression_per_side(self):
        ef = _install([FakeSketch("S")])
        _with_units_mgr(_FakeUnitsMgr(valid=("StockZ/2", "10 mm")))
        _payload(ex.handler(sketch_name="S", extent="two_side", distance="StockZ/2", distance2="10 mm"))
        d1, d2 = ef.last_input.two_sides_distance
        assert d1 == ("str", "StockZ/2") and d2 == ("str", "10 mm")


class TestModelParameterLinkage:
    def test_names_the_distance_and_taper_model_parameters(self):
        ef = _install([FakeSketch("S")])
        ef.add = lambda inp: _ParamFeature(is_solid=getattr(inp, "isSolid", True))
        out = _payload(ex.handler(sketch_name="S", distance=5))
        assert out["model_parameters"]["distance"] == "d1"
        assert out["model_parameters"]["taper"] == "d2"

    def test_two_side_names_both_side_parameters(self):
        ef = _install([FakeSketch("S")])
        ef.add = lambda inp: _ParamFeature(dist="d1", taper="d2", dist2="d3", taper2="d4",
                                           is_solid=getattr(inp, "isSolid", True))
        out = _payload(ex.handler(sketch_name="S", extent="two_side", distance=10, distance2=5))
        assert out["model_parameters"]["distance"] == "d1"
        assert out["model_parameters"]["distance2"] == "d3"

    def test_note_advertises_the_model_parameters(self):
        ef = _install([FakeSketch("S")])
        ef.add = lambda inp: _ParamFeature(is_solid=getattr(inp, "isSolid", True))
        out = _payload(ex.handler(sketch_name="S", distance=5))
        assert "model_parameters" in out["note"] and "param_set" in out["note"]

    def test_absent_when_no_distance_parameter_exists(self):
        # a plain fake feature (no extentOne/taper params, e.g. a through_all/to_face extent) -> the key
        # is simply omitted, never a null or a crash.
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", distance=5))   # add() returns a bare FakeFeature
        assert "model_parameters" not in out


# ── cross-component cut read-back: the footgun warning + the honest 'component' field ──────────────
# A cut with NO target_bodies bores through EVERY body overlapping the profile sweep - co-located
# bodies in different components share 3D space. feature.bodies returns only the feature's own-component
# result body (confirmed live), so a design-wide pre/post volume snapshot is what reveals which bodies,
# and whose components, actually lost material: it powers both the warning and the 'component' field.

class _VolBody:
    """A solid body whose volume a fake cut mutates - the read-back the snapshot diffs."""
    def __init__(self, name, volume, is_solid=True):
        self.name = name
        self.volume = volume
        self.isSolid = is_solid
        self.entityToken = name


class _MultiComp:
    def __init__(self, name, bodies=(), sketches=(), ef=None):
        self.name = name
        self.bRepBodies = _NamedCollection(list(bodies))
        self.sketches = FakeSketches(list(sketches))
        if ef is not None:
            self.features = type("F", (), {"extrudeFeatures": ef})()


class _MultiDesign:
    def __init__(self, comps, active):
        self._comps = list(comps)
        self.rootComponent = comps[0]
        self.activeComponent = active

    @property
    def allComponents(self):
        return _NamedCollection(self._comps)


def _install_multi(bodyA_after, bodyB_after):
    """A design with two co-located components: the sketch lives in CompA (bodyA); CompB (bodyB) is a
    separate part sharing space. The fake cut sets each body's post-volume, so the snapshot read-back
    sees exactly which bodies lost material. Returns (bodyA, bodyB)."""
    ef = FakeExtrudeFeatures()
    bodyA, bodyB = _VolBody("BodyA", 48.0), _VolBody("BodyB", 48.0)
    sk = FakeSketch("S")
    compA = _MultiComp("CompA", bodies=[bodyA], sketches=[sk], ef=ef)
    compB = _MultiComp("CompB", bodies=[bodyB])
    root = _MultiComp("Root")
    sk.parentComponent = compA
    design = _MultiDesign([root, compA, compB], active=compA)

    def _add(inp):
        bodyA.volume, bodyB.volume = bodyA_after, bodyB_after
        f = FakeFeature()
        f.parentComponent = compA
        return f
    ef.add = _add

    ex.app = type("A", (), {"activeProduct": design})()
    ex._common.app = ex.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _MultiDesign) else None
    adsk.fusion.BRepBody = _VolBody                      # so a target_bodies name resolves via BodyRef
    ex._inputs._common.design = lambda: design           # the dual-seam trap (tests/CLAUDE.md)
    ex._inputs._common.target_component = lambda _d=None: compA
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))

    class _OC:
        def __init__(self): self.items = []
        def add(self, x): self.items.append(x)
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    return bodyA, bodyB


class TestAffectedBodies:
    def test_reports_only_bodies_that_lost_volume(self):
        class B:
            def __init__(self, v): self.volume = v
        a, b = B(10.0), B(10.0)
        snap = [(a, "A", "CompA", 10.0), (b, "B", "CompB", 10.0)]
        a.volume = 4.0                       # A lost material; B untouched
        assert ex._affected_bodies(snap) == [("A", "CompA", 6.0)]

    def test_consumed_body_reported_with_none_removed(self):
        class Dead:
            @property
            def volume(self):
                raise RuntimeError("deleted")   # a body an intersect consumed whole
        snap = [(Dead(), "D", "CompB", 5.0)]
        assert ex._affected_bodies(snap) == [("D", "CompB", None)]


class TestCrossComponentCut:
    def test_unscoped_cut_bleeding_into_other_component_warns(self):
        # Case: unscoped cut, both components visible - material leaves BOTH CompA (sketch owner) and CompB.
        _install_multi(bodyA_after=45.6, bodyB_after=45.6)
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut"))
        assert out["cut_touched_other_components"] == ["CompB"]
        assert out["affected_components"] == ["CompA", "CompB"]
        note = out["note"]
        assert "WARNING" in note and "target_bodies" in note
        # The warning must teach the VERIFIED rule (confirmed live): an unscoped cut takes every
        # VISIBLE intersecting body, and names BOTH levers - pass target_bodies, or hide the bodies
        # that must survive (a named body wins over visibility, i.e. is cut even if hidden).
        assert "VISIBLE" in note and "hidden bodies are spared" in note
        assert "hide the bodies that must be spared" in note
        assert "a named body is cut even if hidden" in note

    def test_component_field_names_where_material_landed_not_sketch_owner(self):
        # Case: the cut removes material ONLY from CompB, though the sketch lives in CompA. The
        # 'component' field must name CompB (where it landed), not the sketch's owning component.
        _install_multi(bodyA_after=48.0, bodyB_after=45.6)   # CompA untouched
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut"))
        assert out["component"] == "CompB"
        assert out["cut_touched_other_components"] == ["CompB"]

    def test_same_component_cut_gets_no_warning(self):
        # A cut confined to the sketch's own component (CompB untouched) warns nobody and names CompA.
        _install_multi(bodyA_after=45.6, bodyB_after=48.0)   # only CompA lost material
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut"))
        assert out["component"] == "CompA"
        assert "cut_touched_other_components" not in out
        assert "affected_components" not in out
        assert "WARNING" not in out["note"]

    def test_scoped_cut_never_warns_even_when_other_component_changed(self):
        # With target_bodies given the agent chose the bodies explicitly, so scoped_to suppresses the
        # warning even if the affected-set read-back still shows a co-located component changed.
        _install_multi(bodyA_after=45.6, bodyB_after=45.6)
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut", target_bodies="BodyA"))
        assert out["scoped_to_bodies"] == ["BodyA"]
        assert "cut_touched_other_components" not in out
        assert "WARNING" not in out["note"]

    def test_hidden_colocated_body_is_spared_so_no_warning(self):
        # THE VISIBILITY RULE (confirmed live): an unscoped cut takes only the VISIBLE intersecting
        # bodies. A hidden co-located body loses no volume, so the volume-diff detection has nothing
        # to flag - the footgun warning must NOT fire off a spared body. At the mock level "hidden and
        # spared" is exactly "volume unchanged", so bodyB keeps its pre-cut volume here.
        _install_multi(bodyA_after=45.6, bodyB_after=48.0)   # CompB hidden -> spared -> unchanged
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut"))
        assert out["component"] == "CompA"
        assert "cut_touched_other_components" not in out
        assert "affected_components" not in out
        assert "WARNING" not in out["note"]

    def test_scoped_cut_to_other_component_body_lands_there_no_warning(self):
        # Probe (c) at the mock level: target_bodies explicitly names the OTHER component's body, so
        # the cut lands on CompB alone (CompA, unnamed, is spared). scoped_to suppresses the warning
        # and 'component' names where the material actually left - CompB, not the sketch's own CompA.
        _install_multi(bodyA_after=48.0, bodyB_after=45.6)   # only CompB (the named body) loses material
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut", target_bodies="CompB"))
        assert out["scoped_to_bodies"] == ["BodyB"]
        assert out["component"] == "CompB"
        assert "cut_touched_other_components" not in out
        assert "WARNING" not in out["note"]


# ── through_all direction teaching (PLATFORM behavior) ──────────────────────────────────────────────
# A sketch ON a body's face has its normal pointing AWAY from the material, so through_all's default
# (and symmetric) direction hits pure air and Fusion raises 'body not found to extrude through'. The
# direction MAPPING is correct - a negative 'distance' cuts into the body - so the error TEACHES the
# flip at the failure moment.

class TestBodySplitDisconnection:
    """A cut/intersect that DISCONNECTS the target leaves it in several pieces. The extruded profile
    removes no bodies, so a NET increase in the design-wide solid count is split-off pieces - warned,
    naming the pieces, so a later op does not silently target the wrong one."""

    def test_cut_that_disconnects_target_warns(self):
        ef = _install([FakeSketch("S")])
        root = ex.app.activeProduct.rootComponent
        bar = BRepBody("Bar", volume=100.0)
        root.bRepBodies = _NamedCollection([bar])
        piece2 = BRepBody("Bar1", volume=40.0)

        def _add(inp):
            root.bRepBodies._items.append(piece2)     # the cut disconnected the bar into a 2nd body
            f = FakeFeature()
            f.bodies = type("BB", (), {"count": 2, "item": staticmethod(lambda i: [bar, piece2][i])})()
            return f
        ef.add = _add
        out = _payload(ex.handler(sketch_name="S", operation="cut", distance=-5))
        assert out["body_split"] == ["Bar", "Bar1"]
        assert "DISCONNECTED" in out["note"]

    def test_blind_cut_that_does_not_disconnect_no_warning(self):
        # a cut that removes material without splitting leaves the solid count unchanged -> no warning.
        ef = _install([FakeSketch("S")])
        root = ex.app.activeProduct.rootComponent
        root.bRepBodies = _NamedCollection([BRepBody("Bar", volume=100.0)])
        out = _payload(ex.handler(sketch_name="S", operation="cut", distance=-5))
        assert "body_split" not in out

    def test_new_operation_never_flagged_as_split(self):
        # a 'new' extrude makes a body by design - the split warning is scoped to cut/intersect.
        ef = _install([FakeSketch("S")])
        root = ex.app.activeProduct.rootComponent
        root.bRepBodies = _NamedCollection([BRepBody("Bar", volume=100.0)])
        out = _payload(ex.handler(sketch_name="S", distance=5))   # operation defaults 'new'
        assert "body_split" not in out


# ── the compute-failed feature and the scoped-cut no-op (the false-ok pair) ─────────────────────────
# add() hands back a truthy feature object for a compute Fusion FAILED, and a cut scoped with
# 'target_bodies' whose profile reaches none of them is exactly that case: measured live, the failure
# read the WARNING health state carrying "No target body!Compute Failed" while no volume moved. Both
# the health state and the design-wide effect evidence gate the result, and the inert feature is
# rolled back with the timeline re-read as the proof.


def _fail_feature(state="warning", message="No target body!Compute Failed", name="Extrude3",
                  timeline=None, delete_ok=True):
    """The feature object a FAILED compute hands back: healthState set to the warning/error member,
    the message beside it, and a deleteMe whose success shows up as a SHRINKING timeline count (what
    the rollback sentence is read back from) - or a decline that leaves the count where it was."""
    import adsk.fusion
    states = adsk.fusion.FeatureHealthStates
    f = FakeFeature(name=name)
    f.healthState = (states.WarningFeatureHealthState if state == "warning"
                     else states.ErrorFeatureHealthState)
    f.errorOrWarningMessage = message

    def _delete():
        if delete_ok and timeline is not None:
            timeline.count -= 1
        return delete_ok
    f.deleteMe = _delete
    return f


def _install_scoped_cut(volume_after=48.0, feature=None, timeline_count=None):
    """A two-component design (the sketch's CompA holds BodyA, CompB holds BodyB) whose cut leaves
    BodyA at `volume_after`, with an optional replacement feature and timeline. Returns bodyA."""
    bodyA, _bodyB = _install_multi(bodyA_after=volume_after, bodyB_after=48.0)
    design = ex.app.activeProduct
    if timeline_count is not None:
        design.timeline = types.SimpleNamespace(count=timeline_count)
    if feature is not None:
        ef = design.activeComponent.features.extrudeFeatures
        prior = ef.add

        def _add(inp):
            prior(inp)                      # keep the canned volume effect
            return feature
        ef.add = _add
    return bodyA


class TestComputeFailedFeature:
    def test_a_compute_failed_extrude_is_an_error_not_an_ok_with_a_warning(self):
        tl = types.SimpleNamespace(count=4)
        _install_scoped_cut(feature=_fail_feature(timeline=tl), timeline_count=4)
        ex.app.activeProduct.timeline = tl
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        msg = res["message"]
        assert "Extrude3" in msg and "FAILED compute" in msg
        assert "No target body!Compute Failed" in msg
        assert "health state: warning" in msg          # the STATE, read - not the message text
        assert "removed nothing" in msg                # backed by the volume/census evidence
        assert "BodyA" in msg                          # the scoped body that was not reached
        assert "rolled back" in msg and tl.count == 3  # the re-read, not deleteMe's own answer

    def test_the_error_health_state_is_caught_too(self):
        tl = types.SimpleNamespace(count=2)
        _install_scoped_cut(feature=_fail_feature(state="error", message="", timeline=tl))
        ex.app.activeProduct.timeline = tl
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        assert "health state: error" in res["message"]
        assert "it reports no message" in res["message"]   # never a fabricated cause

    def test_a_rollback_that_did_not_take_names_what_remains(self):
        tl = types.SimpleNamespace(count=5)
        _install_scoped_cut(feature=_fail_feature(timeline=tl, delete_ok=False))
        ex.app.activeProduct.timeline = tl
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        assert "REMAINS in the timeline" in res["message"]
        assert "design_delete_feature" in res["message"]
        assert tl.count == 5

    def test_an_unreadable_timeline_never_claims_the_rollback_was_confirmed(self):
        # No timeline to re-read (a direct-modelling design): the sentence says the rollback could
        # not be confirmed instead of asserting the design is clean.
        _install_scoped_cut(feature=_fail_feature())
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        assert "could not be re-read to confirm" in res["message"]
        assert "rolled back -" not in res["message"]

    def test_a_failed_compute_that_DID_change_geometry_is_never_rolled_back(self):
        # The rollback is only for a feature the evidence shows landed nothing. With material
        # measurably gone, deleting the feature would delete a real effect - so the error states the
        # effect and leaves the feature standing.
        tl = types.SimpleNamespace(count=4)
        _install_scoped_cut(volume_after=45.6, feature=_fail_feature(timeline=tl))
        ex.app.activeProduct.timeline = tl
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        msg = res["message"]
        assert "Material DID change: BodyA in CompA (-2.4 cm3)" in msg
        assert "LEFT in the timeline" in msg and "design_delete_feature" in msg
        assert "rolled back -" not in msg and "removed nothing" not in msg
        assert tl.count == 4                      # the feature is still there

    def test_a_failed_compute_whose_only_evidence_is_the_census_keeps_the_feature(self):
        # A body gone from the design while its held wrapper still reads the pre-cut volume: no
        # affected row, but the census moved - an effect that is not ruled out, so no rollback.
        tl = types.SimpleNamespace(count=4)
        bodyA = _install_scoped_cut(feature=_fail_feature(timeline=tl))
        ex.app.activeProduct.timeline = tl
        compA = ex.app.activeProduct.activeComponent
        ef = compA.features.extrudeFeatures
        prior = ef.add

        def _add(inp):
            f = prior(inp)
            compA.bRepBodies._items.remove(bodyA)
            return f
        ef.add = _add
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        assert "solid body count changed by -1" in res["message"]
        assert "LEFT in the timeline" in res["message"]
        assert tl.count == 4

    def test_a_failed_new_extrude_claims_no_effect_verdict_and_keeps_the_feature(self):
        # operation='new' takes no volume snapshot, so nothing here can say whether a body landed:
        # the refusal must neither claim a no-op nor delete work it did not measure.
        tl = types.SimpleNamespace(count=2)
        _install_scoped_cut(feature=_fail_feature(timeline=tl))
        ex.app.activeProduct.timeline = tl
        res = ex.handler(sketch_name="S", distance=20, operation="new")
        assert res["isError"] is True
        msg = res["message"]
        assert "Extrude3" in msg and "FAILED compute" in msg
        assert "NOT read for a 'new' extrude" in msg
        assert "LEFT in the timeline" in msg
        assert "removed nothing" not in msg and "rolled back -" not in msg
        assert tl.count == 2

    def test_the_timeline_items_state_is_read_past_a_healthy_feature(self):
        # The failure was MEASURED on the timeline item, so a feature answering HEALTHY (or nothing)
        # for itself must not turn that into a clean success.
        import adsk.fusion
        tl = types.SimpleNamespace(count=4)
        item = _fail_feature(timeline=tl)
        f = FakeFeature(name="Extrude3")
        f.healthState = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
        f.timelineObject = item
        f.deleteMe = item.deleteMe
        _install_scoped_cut(feature=f)
        ex.app.activeProduct.timeline = tl
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        assert "health state: warning" in res["message"]
        assert "No target body!Compute Failed" in res["message"]

    def test_a_healthy_feature_passes_the_health_gate(self):
        import adsk.fusion
        f = FakeFeature()
        f.healthState = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
        _install_scoped_cut(volume_after=45.6, feature=f)
        out = _payload(ex.handler(sketch_name="S", distance=-20, operation="cut",
                                  target_bodies="BodyA"))
        assert out["extruded"] is True and out["component"] == "CompA"

    def test_the_postcondition_never_counts_a_compute_failed_feature(self):
        # features_verified comes from the FeatureHealthy postcondition, which runs only on an ok
        # result - so the handler's own refusal is what keeps a failed feature out of the count.
        tl = types.SimpleNamespace(count=4)
        _install_scoped_cut(feature=_fail_feature(timeline=tl))
        ex.app.activeProduct.timeline = tl
        asserted = ex._assert.wrap(ex.handler, [ex._assert.FeatureHealthy()])
        res = asserted(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        assert "features_verified" not in json.dumps(res)
        assert "feature_warnings" not in json.dumps(res)


class TestScopedCutNoOp:
    def test_a_scoped_cut_that_changed_nothing_is_an_error_naming_the_bodies(self):
        tl = types.SimpleNamespace(count=3)
        _install_scoped_cut()                       # BodyA keeps its 48.0 - nothing was removed
        ex.app.activeProduct.timeline = tl
        res = ex.handler(sketch_name="S", distance=-20, operation="cut", target_bodies="BodyA")
        assert res["isError"] is True
        assert "changed nothing" in res["message"] and "BodyA" in res["message"]
        assert "target_bodies" in res["message"]

    def test_a_scoped_cut_that_removed_material_is_ok(self):
        _install_scoped_cut(volume_after=45.6)
        out = _payload(ex.handler(sketch_name="S", distance=-20, operation="cut",
                                  target_bodies="BodyA"))
        assert out["scoped_to_bodies"] == ["BodyA"] and out["component"] == "CompA"

    def test_a_scoped_cut_that_consumed_the_body_whole_is_not_flagged(self):
        # A consumed body reports no volume DROP (its volume stops reading at all), so the gate must
        # judge on the consumed row too, not on the volume delta alone.
        bodyA = _install_scoped_cut()
        ef = ex.app.activeProduct.activeComponent.features.extrudeFeatures
        prior = ef.add

        def _add(inp):
            f = prior(inp)
            del bodyA.volume            # the cut consumed it whole
            return f
        ef.add = _add
        out = _payload(ex.handler(sketch_name="S", distance=-20, operation="cut",
                                  target_bodies="BodyA"))
        assert out["extruded"] is True

    def test_a_consumed_body_whose_wrapper_still_answers_is_seen_by_the_census(self):
        # A held body wrapper can keep answering its pre-cut volume after the body itself is gone, so
        # the volume diff alone would read a whole-body consumption as 'nothing happened'. The
        # design-wide solid census still sees the body leave, which is why both back the no-op claim.
        bodyA = _install_scoped_cut()
        compA = ex.app.activeProduct.activeComponent
        ef = compA.features.extrudeFeatures
        prior = ef.add

        def _add(inp):
            f = prior(inp)
            compA.bRepBodies._items.remove(bodyA)    # gone, while the wrapper still reads 48.0
            return f
        ef.add = _add
        out = _payload(ex.handler(sketch_name="S", distance=-20, operation="cut",
                                  target_bodies="BodyA"))
        assert out["extruded"] is True

    def test_an_unscoped_cut_that_changed_nothing_stays_ok(self):
        # Unscoped, Fusion refuses a miss itself ("No target body found to cut or intersect!") and
        # that refusal surfaces through the add() handler - so this gate stays scoped to target_bodies.
        _install_scoped_cut()
        out = _payload(ex.handler(sketch_name="S", distance=-20, operation="cut"))
        assert out["extruded"] is True

    def test_a_scoped_join_is_not_gated_on_removed_material(self):
        # 'join' ADDS material - no body is expected to lose any, so the cut/intersect gate is off.
        _install_scoped_cut()
        out = _payload(ex.handler(sketch_name="S", distance=-20, operation="join",
                                  target_bodies="BodyA"))
        assert out["extruded"] is True


class TestThroughAllDirectionTeaching:
    def test_body_not_found_teaches_negative_distance(self):
        ef = _install([FakeSketch("S")])

        def _raise(inp):
            raise RuntimeError("3 : Could not complete Through All Extrude, body not found to "
                               "extrude through.")
        ef.add = _raise
        res = ex.handler(sketch_name="S", operation="cut", extent="through_all")
        assert res["isError"] is True
        msg = res["message"]
        assert "NEGATIVE" in msg and "sketch-plane normal" in msg

    def test_generic_add_failure_keeps_the_plain_hint(self):
        # a non-through_all add() failure is not a direction problem - it keeps the existing hint.
        ef = _install([FakeSketch("S")])

        def _raise(inp):
            raise RuntimeError("some other kernel error")
        ef.add = _raise
        res = ex.handler(sketch_name="S", distance=5, operation="cut")
        assert res["isError"] is True
        assert "existing geometry" in res["message"] and "sketch-plane normal" not in res["message"]
