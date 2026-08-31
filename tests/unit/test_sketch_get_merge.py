"""Unit tests for sketch_get's routing between its two depths, and the summary's design-wide walk.

sketch_get is ONE tool switched by specificity: no sketch_name -> summary list; a sketch_name ->
full detail (delegated to the _sketch_detail engine). The return is always about sketches; only the
depth changes. The routing tests pin BOTH halves of each branch - the engine that must run, and the
other one, monkeypatched to a sentinel that fails the test if it is invoked at all (routing to both
depths costs a caller the heavy read they did not ask for, and reads green on a
'the right one ran' assertion alone). The walk tests pin that the summary reaches sketches in
SUB-components and tags each row with its owner.
"""

import json
import sys
import types

from conftest import load_tool, make_occurrence

sketches = load_tool("sketch_core")


class _Coll:
    def __init__(self, items):
        self._items = list(items)
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


def _never(what):
    """A stand-in for the depth that must NOT run on this call: invoked at all, it fails the test
    by name instead of quietly returning a result the assertions would never look at."""
    def _refuse(*args, **kwargs):
        raise AssertionError(f"{what} was invoked - sketch_get routed to the wrong depth")
    return _refuse


def _no_detail_engine(monkeypatch):
    """Install a _sketch_detail engine whose HANDLER refuses to be called. Same module-table seam
    the delegation test installs its stub through, so it intercepts the same lookup.

    'scope_components' still answers: the engine owns the component-scope vocabulary for BOTH
    depths, so the summary depth legitimately calls it - only the one-sketch handler is off limits
    here."""
    monkeypatch.setitem(sys.modules, "mcpServer.tools._sketch_detail",
                        types.SimpleNamespace(handler=_never("the _sketch_detail engine"),
                                              scope_components=lambda d, raw: ([], None)))


class TestSketchGetRouting:
    def test_no_name_lists_summary(self, monkeypatch):
        called = {}

        def fake_summary(component=""):
            called["summary"] = True
            return {"isError": False}
        monkeypatch.setattr(sketches, "get_sketches_handler", fake_summary)
        _no_detail_engine(monkeypatch)          # the detail slice must NOT be reached
        res = sketches.sketch_get_handler(sketch_name="")
        assert called.get("summary") is True
        assert res["isError"] is False

    def test_no_name_threads_the_component_scope_into_the_summary(self, monkeypatch):
        # Both depths take the scope; a router that dropped it on this branch would silently list
        # the WHOLE design when one component was asked for.
        seen = {}

        def fake_summary(component=""):
            seen["component"] = component
            return {"isError": False}
        monkeypatch.setattr(sketches, "get_sketches_handler", fake_summary)
        _no_detail_engine(monkeypatch)
        sketches.sketch_get_handler(sketch_name="", component="Frame")
        assert seen.get("component") == "Frame"

    def test_name_delegates_to_detail_engine(self, monkeypatch):
        seen = {}

        class FakeDetail:
            @staticmethod
            def handler(sketch_name="", include_entities=False, units="mm", component=""):
                seen["name"] = sketch_name
                seen["include_entities"] = include_entities
                seen["units"] = units
                seen["component"] = component
                return {"isError": False, "content": [{"type": "text", "text": "{}"}]}

        # The handler resolves the engine by NAME in the module table on every call, so installing
        # the stub there routes it - whatever else has already imported the real engine. Binding the
        # engine through the package attribute instead makes this test pass or fail on load order.
        monkeypatch.setitem(sys.modules, "mcpServer.tools._sketch_detail", FakeDetail)
        # the reciprocal negative: a named sketch must not ALSO run the design-wide summary walk
        monkeypatch.setattr(sketches, "get_sketches_handler", _never("the summary walk"))

        res = sketches.sketch_get_handler(sketch_name="Emblem", include_entities=True, units="in",
                                          component="Frame")
        assert seen.get("name") == "Emblem"     # routed to the detail engine with the name
        assert seen.get("include_entities") is True   # the zoom flag is threaded through
        assert seen.get("units") == "in"        # the units param is threaded through too
        # the scope reaches the engine that resolves it - dropped here, a scoped read of a shared
        # name would come back refused as ambiguous with the scope the caller gave ignored
        assert seen.get("component") == "Frame"
        assert res["isError"] is False

    def test_whitespace_name_treated_as_no_name(self, monkeypatch):
        called = {}

        def fake_summary(component=""):
            called["summary"] = True
            return {"isError": False}
        monkeypatch.setattr(sketches, "get_sketches_handler", fake_summary)
        _no_detail_engine(monkeypatch)          # a blank-ish name is not a name to look up
        sketches.sketch_get_handler(sketch_name="   ")
        assert called.get("summary") is True     # blank-ish name -> summary, not detail


_comp_serial = iter(range(1, 10_000))


class TestSketchSummaryWalk:
    @staticmethod
    def _comp(name, sketch_names=()):
        # Like the live Component: no allComponents attribute (that collection is a Design
        # property), so reading it here raises AttributeError exactly as adsk does - and a DISTINCT
        # entityToken, which every live component has and which is the only thing separating two
        # components that wear one name.
        sks = [type("Sk", (), {"name": n})() for n in sketch_names]
        return type("C", (), {"name": name, "sketches": _Coll(sks),
                              "entityToken": f"comp-{name}-{next(_comp_serial)}"})()

    _occ = staticmethod(make_occurrence)         # the shared conftest occurrence fake

    def test_lists_sub_component_sketches_tagged_with_their_owner(self, monkeypatch):
        # A multi-part doc keeps each part's sketch in its own component; a walk that only reaches
        # the root reports sketch_count 0 for the whole design (observed live on a 3-component doc).
        root = self._comp("Root")
        frame = self._comp("Frame", ["FrameSketch"])
        ring = self._comp("OuterRing", ["OuterRingSketch"])
        d = type("D", (), {"rootComponent": root, "activeComponent": root,
                           "allComponents": _Coll([root, frame, ring])})()
        monkeypatch.setattr(sketches._common, "design", lambda: d)
        res = sketches.get_sketches_handler()
        payload = json.loads(res["content"][0]["text"])
        assert payload["sketch_count"] == 2
        assert {(r["name"], r["component"]) for r in payload["sketches"]} == {
            ("FrameSketch", "Frame"), ("OuterRingSketch", "OuterRing")}

    def _two_component_design(self, monkeypatch):
        root = self._comp("Root")
        frame = self._comp("Frame", ["Sketch1"])
        ring = self._comp("OuterRing", ["Sketch1"])
        d = type("D", (), {"rootComponent": root, "activeComponent": root,
                           "allComponents": _Coll([root, frame, ring])})()
        monkeypatch.setattr(sketches._common, "design", lambda: d)

    def test_the_component_scope_narrows_the_list_to_that_component(self, monkeypatch):
        # Both components hold a "Sketch1" (Fusion numbers per component from 1), so the OWNER is
        # the only thing separating the rows - and the scope is how a caller asks for one.
        self._two_component_design(monkeypatch)
        res = sketches.get_sketches_handler("OuterRing")
        payload = json.loads(res["content"][0]["text"])
        assert payload["sketch_count"] == 1
        assert [(r["name"], r["component"]) for r in payload["sketches"]] == [("Sketch1", "OuterRing")]

    def test_an_unknown_component_is_refused_rather_than_listed_as_empty(self, monkeypatch):
        # An empty list would read as "that component has no sketches" - the opposite of what was
        # read, and it hides the typo.
        self._two_component_design(monkeypatch)
        res = sketches.get_sketches_handler("Ghost")
        assert res["isError"] is True
        assert "No component named 'Ghost'" in res["message"]
        assert "Frame" in res["message"] and "OuterRing" in res["message"]

    def test_no_scope_still_lists_every_component(self, monkeypatch):
        # The other side of the branch: the scope is opt-in, so a blank one must not narrow.
        self._two_component_design(monkeypatch)
        payload = json.loads(sketches.get_sketches_handler("")["content"][0]["text"])
        assert payload["sketch_count"] == 2

    def test_rows_of_uniquely_named_components_carry_no_paths(self, monkeypatch):
        # The common design has nothing to tell apart, so no row pays for a path field - and these
        # components ARE placed by occurrences, so a path exists to publish and the NAME being
        # unique is the only reason it is withheld.
        frame = self._comp("Frame", ["Sketch1"])
        ring = self._comp("OuterRing", ["Sketch1"])
        root = type("R", (), {"name": "Root", "sketches": _Coll([]), "entityToken": "comp-root",
                              "allOccurrences": [self._occ("Frame:1", frame),
                                                 self._occ("OuterRing:1", ring)]})()
        d = type("D", (), {"rootComponent": root, "activeComponent": root,
                           "allComponents": _Coll([root, frame, ring])})()
        monkeypatch.setattr(sketches._common, "design", lambda: d)
        payload = json.loads(sketches.get_sketches_handler("")["content"][0]["text"])
        assert payload["sketch_count"] == 2
        assert all("component_paths" not in r for r in payload["sketches"])
        assert "note" not in payload

    # ONE token for two distinct components - measured on a host holding two inserted references.
    _XREF_TOKEN = "/v4BAAEAegEAAAAAAAAAAAAA"

    def _two_frames(self, monkeypatch):
        """Two components both named 'Frame', each with its own 'Frame_Ring', SHARING one
        entityToken - the measured shape after inserting two referenced documents. The shared token
        is what makes any identity-keyed grouping report each component's paths as both."""
        a = self._comp("Frame", ["Frame_Ring"])
        b = self._comp("Frame", ["Frame_Ring"])
        a.__class__.entityToken = self._XREF_TOKEN
        b.__class__.entityToken = self._XREF_TOKEN
        root = type("R", (), {"name": "Root", "sketches": _Coll([]),
                              "entityToken": "comp-root",
                              "allOccurrences": [self._occ("P2a-Gimbal:1+Frame:1", a),
                                                 self._occ("P3-Gimbal:1+Frame:1", b)]})()
        d = type("D", (), {"rootComponent": root, "activeComponent": root,
                           "allComponents": _Coll([root, a, b])})()
        monkeypatch.setattr(sketches._common, "design", lambda: d)
        assert a.entityToken == b.entityToken        # the fixture models the collision, or it lies

    def test_an_ambiguous_scope_publishes_each_placement_exactly_once(self, monkeypatch):
        # Measured defect: BOTH rows carried BOTH paths, so a row's paths did not identify that
        # row's component - the one thing they existed to do. The placement block states only what
        # the walk read: these paths exist, and each places a component of this name.
        self._two_frames(monkeypatch)
        payload = json.loads(sketches.get_sketches_handler("Frame")["content"][0]["text"])
        assert payload["sketch_count"] == 2
        assert payload["placements"] == [
            {"path": "P2a-Gimbal:1+Frame:1", "component": "Frame"},
            {"path": "P3-Gimbal:1+Frame:1", "component": "Frame"}]

    def test_no_row_claims_a_path_it_cannot_back(self, monkeypatch):
        # The rows are indistinguishable and the payload must not pretend otherwise.
        self._two_frames(monkeypatch)
        payload = json.loads(sketches.get_sketches_handler("Frame")["content"][0]["text"])
        assert all("component_paths" not in r for r in payload["sketches"])

    def test_the_note_names_the_ambiguous_name_and_what_to_pass_next(self, monkeypatch):
        self._two_frames(monkeypatch)
        payload = json.loads(sketches.get_sketches_handler("Frame")["content"][0]["text"])
        assert "Frame" in payload["note"] and "'placements'" in payload["note"]
        assert "'component'" in payload["note"]

    def _two_ambiguous_names(self, monkeypatch):
        """Two shared names - Frame x2 and Carrier x2 - each pair sharing a token, the shape of a
        host holding two inserted references. A call scoped to one of them must not be answered with
        the other's placements."""
        frame_a = self._comp("Frame", ["Frame_Ring"])
        frame_b = self._comp("Frame", ["Frame_Ring"])
        frame_a.__class__.entityToken = self._XREF_TOKEN
        frame_b.__class__.entityToken = self._XREF_TOKEN
        car_a = self._comp("Carrier", ["Carrier_Ring"])
        car_b = self._comp("Carrier", ["Carrier_Ring"])
        car_a.__class__.entityToken = self._XREF_TOKEN
        car_b.__class__.entityToken = self._XREF_TOKEN
        root = type("R", (), {"name": "Root", "sketches": _Coll([]), "entityToken": "comp-root",
                              "allOccurrences": [self._occ("P2a-Gimbal:1+Frame:1", frame_a),
                                                 self._occ("P2a-Gimbal:1+Carrier:1", car_a),
                                                 self._occ("P3-Gimbal:1+Frame:1", frame_b),
                                                 self._occ("P3-Gimbal:1+Carrier:1", car_b)]})()
        d = type("D", (), {"rootComponent": root, "activeComponent": root,
                           "allComponents": _Coll([root, frame_a, frame_b, car_a, car_b])})()
        monkeypatch.setattr(sketches._common, "design", lambda: d)

    def test_a_scoped_call_gets_only_ITS_names_placements(self, monkeypatch):
        # MEASURED defect: asking about 'Frame' returned 16 placements - every placement of every
        # shared name in the design (Carrier, Pedestal, OuterRing, Shaft, Rotor, Crank ...). The
        # block has to answer the QUERY, not enumerate the design.
        self._two_ambiguous_names(monkeypatch)
        payload = json.loads(sketches.get_sketches_handler("Frame")["content"][0]["text"])
        assert payload["sketch_count"] == 2
        assert [p["path"] for p in payload["placements"]] == [
            "P2a-Gimbal:1+Frame:1", "P3-Gimbal:1+Frame:1"]
        assert all(p["component"] == "Frame" for p in payload["placements"])

    def test_the_note_states_only_what_this_walk_READ(self, monkeypatch):
        # The note asserted "because those components report the same internal id" - a cause this
        # handler never checked: _shared_component_names counts NAMES and nothing here reads an
        # entityToken. Same class as a refusal claiming the query is a name some component carries.
        self._two_ambiguous_names(monkeypatch)
        note = json.loads(sketches.get_sketches_handler("Frame")["content"][0]["text"])["note"]
        assert "internal id" not in note and "entityToken" not in note and "token" not in note
        assert "does not tell those rows apart" in note      # what it DID do

    def test_the_note_names_only_the_names_the_block_covers(self, monkeypatch):
        # the note said "one of those names", which a reader takes as the name in THIS result - it
        # has to stay true of what actually ships
        self._two_ambiguous_names(monkeypatch)
        payload = json.loads(sketches.get_sketches_handler("Frame")["content"][0]["text"])
        assert "(Frame)" in payload["note"] and "Carrier" not in payload["note"]

    def test_an_unscoped_call_still_covers_every_ambiguous_name(self, monkeypatch):
        # the other side: with no scope every row is in play, so design-wide IS the answer
        self._two_ambiguous_names(monkeypatch)
        payload = json.loads(sketches.get_sketches_handler("")["content"][0]["text"])
        assert payload["sketch_count"] == 4
        assert [p["path"] for p in payload["placements"]] == [
            "P2a-Gimbal:1+Frame:1", "P2a-Gimbal:1+Carrier:1",
            "P3-Gimbal:1+Frame:1", "P3-Gimbal:1+Carrier:1"]
        assert "Carrier, Frame" in payload["note"]

    def test_placements_covers_only_the_names_that_need_it(self, monkeypatch):
        # A uniquely-named component is placed too, but its name already identifies it - listing its
        # path would be noise in a block whose only job is separating names that collide.
        a = self._comp("Frame", ["Frame_Ring"])
        b = self._comp("Frame", ["Frame_Ring"])
        a.__class__.entityToken = self._XREF_TOKEN
        b.__class__.entityToken = self._XREF_TOKEN
        bolt = self._comp("Bolt", ["BoltProfile"])
        root = type("R", (), {"name": "Root", "sketches": _Coll([]), "entityToken": "comp-root",
                              "allOccurrences": [self._occ("P2a-Gimbal:1+Frame:1", a),
                                                 self._occ("P3-Gimbal:1+Frame:1", b),
                                                 self._occ("Bolt:1", bolt)]})()
        d = type("D", (), {"rootComponent": root, "activeComponent": root,
                           "allComponents": _Coll([root, a, b, bolt])})()
        monkeypatch.setattr(sketches._common, "design", lambda: d)
        payload = json.loads(sketches.get_sketches_handler("")["content"][0]["text"])
        assert payload["sketch_count"] == 3
        assert [p["path"] for p in payload["placements"]] == [
            "P2a-Gimbal:1+Frame:1", "P3-Gimbal:1+Frame:1"]
        assert "Bolt" not in payload["note"]

    def test_an_occurrence_path_narrows_the_list_to_one_of_them(self, monkeypatch):
        # the loop closes: the path the list published is a scope the same input accepts, and it
        # resolves to ONE component even though both report the same internal id
        self._two_frames(monkeypatch)
        payload = json.loads(
            sketches.get_sketches_handler("P3-Gimbal:1+Frame:1")["content"][0]["text"])
        assert payload["sketch_count"] == 1
        assert payload["sketches"][0]["component"] == "Frame"
