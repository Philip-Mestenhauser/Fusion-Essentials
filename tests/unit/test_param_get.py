"""Unit tests for ``param_get.py`` - the parameter read path and its bounded walk.

The row itself (``_param_summary`` / ``_owner_facts``) is pinned in test__param_common.py, shared
with the param write tools; what is proved here is this tool's own logic: the default user-only
listing, the model-parameter de-dup, the single-name lookup, the _MAX_PARAMS clamp, and the note
that is only published when a row actually carries owner keys.
"""

import os
import sys
from types import SimpleNamespace

from conftest import (FakeFeature, FakeModelParameter, FakeTimeline, FakeTimelineObject,
                      FakeUserParameter, FakeUserParameters, MakeComp, MakeDesign, Profile, Sketch,
                      _NamedCollection, body_proxy, error_message, load_tool, payload as _payload)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "live"))
import verify_core  # noqa: E402  the live sweep's predicate over this tool's payload

params = load_tool("param_get")


def _p(name, expression="1 mm", value=1.0, unit="mm"):
    """One user parameter row, at the defaults this file's listing assertions read past."""
    return FakeUserParameter(name=name, expression=expression, value=value, unit=unit)


def _design(user_parameters, all_params):
    return MakeDesign(user_parameters=user_parameters, all_parameters=list(all_params))


class _DimOwner:
    """A sketch dimension owner: no name of its own, but it knows its sketch."""

    parentSketch = SimpleNamespace(name="Sketch2")


class TestGetHandler:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(params._common, "design", lambda: None)
        res = params.handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_lists_user_parameters_only_by_default(self, monkeypatch):
        u1, u2 = _p("PartX"), _p("PartY")
        model_only = _p("d1")
        design = _design(FakeUserParameters([u1, u2]), [u1, u2, model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler())
        assert out["user_parameter_count"] == 2
        assert {p["name"] for p in out["user_parameters"]} == {"PartX", "PartY"}
        assert "model_parameters" not in out

    def test_the_user_parameter_walk_is_clamped_at_max_params(self, monkeypatch):
        # the listing is bounded so a pathological design cannot flood the wire; the clamp is read
        # off the collection's own count, so it holds no matter how many items the walk yields
        monkeypatch.setattr(params, "_MAX_PARAMS", 3)
        ups = FakeUserParameters([_p("P%d" % i) for i in range(10)])
        monkeypatch.setattr(params._common, "design", lambda: _design(ups, []))
        out = _payload(params.handler())
        # the design's own total stays honest; 'returned' is what the walk actually read
        assert out["user_parameter_count"] == 10 and out["returned"] == 3
        assert [p["name"] for p in out["user_parameters"]] == ["P0", "P1", "P2"]

    def test_a_clamped_walk_says_the_counts_cover_only_what_it_reached(self, monkeypatch):
        # 'matched' and 'generated_skipped' are computed over the WALK, not the table, so a design
        # past the clamp reports tallies about a subset with nothing saying they are one.
        monkeypatch.setattr(params, "_MAX_PARAMS", 3)
        ups = FakeUserParameters([_p("P%d" % i) for i in range(10)])
        monkeypatch.setattr(params._common, "design", lambda: _design(ups, []))
        out = _payload(params.handler())
        assert out["walk_truncated"] is True
        assert "stopped at 3 of 10" in out["note"]

    def test_a_walk_that_reached_every_row_claims_no_gap(self, monkeypatch):
        monkeypatch.setattr(params, "_MAX_PARAMS", 3)
        ups = FakeUserParameters([_p("P%d" % i) for i in range(3)])
        monkeypatch.setattr(params._common, "design", lambda: _design(ups, []))
        out = _payload(params.handler())
        assert "walk_truncated" not in out and "note" not in out

    def test_the_row_page_is_capped_and_the_note_names_the_narrowing(self, monkeypatch):
        # MEASURED: 74 KB of parameter rows on one assembly. A capped page is only usable if the
        # payload says how to ask for less rather than handing back a place to read the rest.
        monkeypatch.setattr(params, "_ROWS_CAP", 2)
        ups = FakeUserParameters([_p("P%d" % i) for i in range(5)])
        monkeypatch.setattr(params._common, "design", lambda: _design(ups, []))
        out = _payload(params.handler())
        assert out["returned"] == 2 and out["matched"] == 5 and out["truncated"] is True
        assert "favorites_only" in out["note"] and "name=" in out["note"]

    def test_a_page_exactly_at_the_cap_is_not_flagged_truncated(self, monkeypatch):
        # the boundary: cap-many matching rows is a COMPLETE answer, and flagging it sends the
        # caller narrowing a list that was never cut.
        monkeypatch.setattr(params, "_ROWS_CAP", 2)
        ups = FakeUserParameters([_p("P0"), _p("P1")])
        monkeypatch.setattr(params._common, "design", lambda: _design(ups, []))
        out = _payload(params.handler())
        assert out["returned"] == 2 and "truncated" not in out


class TestGeneratedParameters:
    """MEASURED on the Airport Seating assembly: 258 of 311 user parameters were adsk_* rows minted
    by inserted standard screws. The authored set is what the modeller drives."""

    def _design_with(self, monkeypatch, names, favorites=()):
        ups = FakeUserParameters([FakeUserParameter(name=n, expression="1 mm", value=1.0,
                                                    unit="mm", favorite=(n in favorites))
                                  for n in names])
        design = _design(ups, [])
        monkeypatch.setattr(params._common, "design", lambda: design)
        return design

    def test_generated_rows_are_counted_not_listed(self, monkeypatch):
        self._design_with(monkeypatch, ["PartLen", "adsk_M6x20_Length", "adsk_M6x20_Pitch"])
        out = _payload(params.handler())
        assert [p["name"] for p in out["user_parameters"]] == ["PartLen"]
        assert out["generated_skipped"] == 2 and out["user_parameter_count"] == 3
        assert "include_generated=true" in out["note"]

    def test_include_generated_lists_them(self, monkeypatch):
        self._design_with(monkeypatch, ["PartLen", "adsk_M6x20_Length"])
        out = _payload(params.handler(include_generated=True))
        assert {p["name"] for p in out["user_parameters"]} == {"PartLen", "adsk_M6x20_Length"}
        assert "generated_skipped" not in out

    def test_the_prefix_match_is_case_insensitive_and_anchored(self, monkeypatch):
        # anchored, not contained: a parameter the modeller named 'my_adsk_ref' is authored, and
        # dropping it would hide a knob nothing else lists.
        self._design_with(monkeypatch, ["ADSK_Bolt_L", "my_adsk_ref"])
        out = _payload(params.handler())
        assert [p["name"] for p in out["user_parameters"]] == ["my_adsk_ref"]
        assert out["generated_skipped"] == 1

    def test_a_design_with_no_generated_rows_says_nothing_about_them(self, monkeypatch):
        self._design_with(monkeypatch, ["PartLen"])
        out = _payload(params.handler())
        assert "generated_skipped" not in out and "note" not in out

    def test_favorites_only_keeps_the_flagged_rows(self, monkeypatch):
        self._design_with(monkeypatch, ["PartLen", "PartWid"], favorites=["PartLen"])
        out = _payload(params.handler(favorites_only=True))
        assert [p["name"] for p in out["user_parameters"]] == ["PartLen"]
        assert out["matched"] == 1 and out["user_parameter_count"] == 2

    def test_favorites_only_off_keeps_every_row(self, monkeypatch):
        self._design_with(monkeypatch, ["PartLen", "PartWid"], favorites=["PartLen"])
        out = _payload(params.handler())
        assert len(out["user_parameters"]) == 2

    def test_the_authored_read_is_a_fraction_of_the_whole_table(self, monkeypatch):
        # MEASURED on the Airport Seating Primary Assembly sample (311 parameters: 53 authored, 258
        # adsk_*): 10.1 KB authored against 49.8 KB for every row, favorites_only 2.2 KB. On the
        # Bench sample all 350 read adsk_*, so the authored read is 239 bytes against 47.4 KB.
        import json
        self._design_with(monkeypatch, [f"Part{i}" for i in range(53)]
                          + [f"adsk_Screw{i}_Len" for i in range(258)])
        size = lambda p: len(json.dumps(p, separators=(",", ":")))
        authored = size(_payload(params.handler()))
        whole = size(_payload(params.handler(include_generated=True)))
        assert authored < 20_000 < whole

    def test_an_uncountable_collection_refuses_instead_of_reporting_zero(self, monkeypatch):
        # userParameters.count raising means the parameters could not be read AT ALL. Reporting
        # "user_parameter_count: 0" would read as "this design has no parameters" - a false answer.
        class _Uncountable(FakeUserParameters):
            @property
            def count(self):
                raise RuntimeError("parameter table is locked")
        monkeypatch.setattr(params._common, "design",
                            lambda: _design(_Uncountable([_p("PartX")]), []))
        res = params.handler()
        assert res["isError"] is True
        assert "could not read user parameters" in res["message"].lower()
        assert "locked" in res["message"]

    def test_include_model_parameters_dedups_user_names(self, monkeypatch):
        u1 = _p("PartX")
        model_only = _p("d1")
        design = _design(FakeUserParameters([u1]), [u1, model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        # PartX is already a user param -> not duplicated into model_parameters
        assert out["model_parameter_count"] == 1
        assert out["model_parameters"][0]["name"] == "d1"

    def test_model_rows_classify_a_user_omitted_by_filters_and_the_user_page(self, monkeypatch):
        monkeypatch.setattr(params, "_MAX_PARAMS", 2)
        generated = _p("adsk_Generated")
        filtered = _p("NotFavorite")
        capped = _p("AfterCap")
        model = _p("d1")
        ups = FakeUserParameters([generated, filtered, capped])
        design = _design(ups, [capped, model])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True, favorites_only=True))
        assert out["model_parameter_count"] == 1
        assert [row["name"] for row in out["model_parameters"]] == ["d1"]

    def test_a_capped_model_walk_discloses_the_observed_subset(self, monkeypatch):
        monkeypatch.setattr(params, "_MAX_PARAMS", 1)
        first, second = _p("d1"), _p("d2")
        design = _design(FakeUserParameters([]), [first, second])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        assert out["model_parameter_count"] == 1 and out["model_walk_truncated"] is True
        assert "observed subset" in out["note"]

    def test_an_unreadable_user_lookup_does_not_label_the_parameter_model(self, monkeypatch):
        class Unclassifiable(FakeUserParameters):
            def itemByName(self, name):
                raise RuntimeError("user parameter table is locked")

        design = _design(Unclassifiable([]), [_p("d1")])
        monkeypatch.setattr(params._common, "design", lambda: design)
        res = params.handler(include_model_parameters=True)
        assert res["isError"] is True and "Could not classify parameter" in res["message"]

    def test_single_named_user_param(self, monkeypatch):
        u1 = _p("PartX", expression="50 mm", value=5.0)
        design = _design(FakeUserParameters([u1]), [u1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="PartX"))
        assert out["parameter"]["name"] == "PartX"
        assert out["parameter"]["value"] == 5.0

    def test_single_named_model_param_falls_through_to_all(self, monkeypatch):
        model_only = _p("d1", value=2.0)
        design = _design(FakeUserParameters([]), [model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="d1"))
        assert out["parameter"]["name"] == "d1"

    def test_single_named_missing_errors(self, monkeypatch):
        design = _design(FakeUserParameters([]), [])
        monkeypatch.setattr(params._common, "design", lambda: design)
        res = params.handler(name="Ghost")
        assert res["isError"] is True and "not found" in res["message"].lower()


class TestOwnerOnTheReadPath:
    def test_model_parameters_carry_their_owner_and_the_note_explains_the_keys(self, monkeypatch):
        u1 = _p("PartX")
        d1 = FakeModelParameter(name="d1", owner=FakeFeature("Extrude1"), role="Distance")
        design = _design(FakeUserParameters([u1]), [u1, d1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        row = out["model_parameters"][0]
        assert (row["owner"], row["role"]) == ("Extrude1", "Distance")
        assert "owner_type" in out["note"] and "role" in out["note"]

    def test_user_parameters_gain_no_owner_keys_so_the_default_read_stays_light(self, monkeypatch):
        u1 = _p("PartX")
        design = _design(FakeUserParameters([u1]), [u1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler())
        assert not [k for k in out["user_parameters"][0] if k.startswith("owner")]
        assert "note" not in out

    def test_no_note_when_not_one_model_row_has_an_owner(self, monkeypatch):
        # a note describing owner keys that are not in the payload sends a caller looking for them.
        # Every model parameter answers a maker (measured), so the payload with no owner key at all
        # is the one carrying no model row: allParameters holding only the user parameter above it.
        u1 = _p("PartX")
        design = _design(FakeUserParameters([u1]), [u1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        assert out["model_parameter_count"] == 0
        assert "note" not in out

    def test_a_single_named_model_parameter_is_answered_with_its_owner(self, monkeypatch):
        # the cheapest form of the read: one parameter, one owner, no list to page through
        d195 = FakeModelParameter(name="d195", owner=_DimOwner(), role="Dimension")
        design = _design(FakeUserParameters([]), [d195])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="d195"))
        assert out["parameter"]["owner_sketch"] == "Sketch2"
        assert out["parameter"]["role"] == "Dimension"


# ── trace=true: what a parameter change touches ──────────────────────────────────────────────────
#
# The trace answers what an agent would otherwise learn by editing the parameter and reading the
# recompute: what the parameter references, every parameter that follows it, and the features the
# traced sketches feed.


class _Extrude(FakeFeature):
    """A feature that consumes one sketch profile - Feature carries no 'profile' of its own."""

    def __init__(self, name, profile, component=None):
        super().__init__(name=name, parent_component=component)
        self.profile = profile


def _wire(monkeypatch, target, extra_params=(), timeline=()):
    """A design holding `target` (plus any model parameters) and a timeline to walk."""
    design = MakeDesign(user_parameters=FakeUserParameters([target]),
                        all_parameters=[target, *extra_params],
                        timeline=FakeTimeline(list(timeline)))
    monkeypatch.setattr(params._common, "design", lambda: design)
    return design


def _traced(out):
    return [(row["name"], row.get("hops")) for row in out["dependents"]]


def _kids(count, driver="Driver"):
    """`count` parameters whose expressions all name `driver` - one ring, every member direct."""
    return [FakeUserParameter(name=f"K{i}", expression=f"{driver} * {i + 1}") for i in range(count)]


def _chain(monkeypatch):
    """PartHt -> StepDrop -> BoreDia as Fusion answers it: BOTH members sit in PartHt's list (it is
    the transitive closure), and only the expressions say which one is the deeper hop."""
    step = FakeUserParameter(name="StepDrop", expression="PartHt / 4")
    bore = FakeUserParameter(name="BoreDia", expression="StepDrop * 1.2")
    _wire(monkeypatch, FakeUserParameter(name="PartHt", expression="40 mm",
                                         dependents=[step, bore]))


class TestTrace:
    def test_the_default_returns_the_direct_hop_and_COUNTS_the_rest(self, monkeypatch):
        # the disclose shape: a frequent read pays for the ring it drives itself, and learns the
        # size of what lies past it from 'reach' rather than from 200 rows of it.
        _chain(monkeypatch)
        out = _payload(params.handler(name="PartHt", trace=True))
        assert out["parameter"]["name"] == "PartHt"          # the summary is kept beside the trace
        assert _traced(out) == [("StepDrop", 1)]
        assert out["dependents"][0]["expression"] == "PartHt / 4"
        assert out["reach"] == {"rows_per_hop": {"1": 1, "2": 1}, "unlinked": 0, "upstream": 0,
                                "downstream": 2, "features": 0, "sketches": 0, "max_hops": 2,
                                "truncated": False}

    def test_a_deep_member_of_the_closure_is_placed_by_its_expression(self, monkeypatch):
        # the discriminating case: BoreDia sits in PartHt's OWN dependents list, so any walk over
        # these lists calls it direct. Only its expression - naming StepDrop, not PartHt - places
        # it a hop further out.
        _chain(monkeypatch)
        out = _payload(params.handler(name="PartHt", trace=True, trace_depth=2))
        assert _traced(out) == [("StepDrop", 1), ("BoreDia", 2)]
        assert out["reach"]["max_hops"] == 2

    def test_the_probe_chain_lands_each_member_at_its_own_depth(self, monkeypatch):
        # the scratch-document rig, offline: chainA -> chainB -> chainC -> chainD with an extrude
        # distance off chainB. chainA's list holds ALL FOUR, and the expressions space them out.
        b = FakeUserParameter(name="chainB", expression="chainA * 2")
        c = FakeUserParameter(name="chainC", expression="chainB + 1 mm")
        d = FakeUserParameter(name="chainD", expression="chainC * 2")
        owner = FakeFeature("Extrude1", timeline_object=FakeTimelineObject(name="Extrude1", index=1))
        d5 = FakeModelParameter(name="d5", owner=owner, role="AlongDistance", expression="chainB")
        _wire(monkeypatch, FakeUserParameter(name="chainA", expression="10 mm",
                                             dependents=[b, c, d, d5]), extra_params=[d5])
        out = _payload(params.handler(name="chainA", trace=True, trace_depth=3))
        entries = [(r["name"], r["hops"]) for r in out["dependents"] if "parameters" not in r]
        entries += [(e["name"], e["hops"]) for r in out["dependents"]
                    for e in r.get("parameters", [])]
        assert sorted(entries) == [("chainB", 1), ("chainC", 2), ("chainD", 3), ("d5", 2)]
        assert out["reach"]["rows_per_hop"] == {"1": 1, "2": 2, "3": 1}
        assert out["reach"]["max_hops"] == 3 and out["reach"]["features"] == 1

    def test_a_member_naming_two_placed_parameters_lands_at_the_NEAREST_hop(self, monkeypatch):
        # the diamond: 'Both' names the target AND a hop-1 member. A placement that let the later
        # pass win would push it to hop 2 - a row the caller would then not see at the default
        # depth, though its own expression names the parameter being changed.
        step = FakeUserParameter(name="StepDrop", expression="PartHt / 4")
        both = FakeUserParameter(name="Both", expression="PartHt + StepDrop")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[step, both]))
        out = _payload(params.handler(name="PartHt", trace=True))
        assert sorted(_traced(out)) == [("Both", 1), ("StepDrop", 1)]
        assert out["reach"]["rows_per_hop"] == {"1": 2}

    def test_a_member_no_expression_link_reached_is_counted_not_placed(self, monkeypatch):
        # a closure member linked some other way gets no fabricated hop: it is counted as unlinked
        # and kept out of the rows, where a made-up hop would read as a driven parameter.
        linked = FakeUserParameter(name="StepDrop", expression="PartHt / 4")
        loose = FakeUserParameter(name="Mystery", expression="12 mm")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[linked, loose]))
        out = _payload(params.handler(name="PartHt", trace=True, trace_depth=9))
        assert _traced(out) == [("StepDrop", 1)]
        assert out["reach"]["unlinked"] == 1 and out["reach"]["rows_per_hop"] == {"1": 1}

    def test_max_results_pages_the_rows_and_says_so(self, monkeypatch):
        _wire(monkeypatch, FakeUserParameter(name="Driver", dependents=_kids(3)))
        out = _payload(params.handler(name="Driver", trace=True, max_results=2))
        assert len(out["dependents"]) == 2 and out["dependents_truncated"] is True
        assert out["reach"]["rows_per_hop"] == {"1": 3}      # the closure still counted every one

    def test_a_page_exactly_at_max_results_is_not_flagged(self, monkeypatch):
        # the boundary: cap-many rows is the COMPLETE page, and flagging it sends the caller paging
        # for rows that were never cut.
        _wire(monkeypatch, FakeUserParameter(name="Driver", dependents=_kids(2)))
        out = _payload(params.handler(name="Driver", trace=True, max_results=2))
        assert len(out["dependents"]) == 2 and "dependents_truncated" not in out

    def test_max_results_is_held_under_its_ceiling(self, monkeypatch):
        # the page size is clamped, so a caller asking for thousands still gets a bounded read.
        monkeypatch.setattr(params, "_TRACE_ROWS_MAX", 2)
        _wire(monkeypatch, FakeUserParameter(name="Driver", dependents=_kids(3)))
        out = _payload(params.handler(name="Driver", trace=True, max_results=500))
        assert len(out["dependents"]) == 2 and out["dependents_truncated"] is True

    def test_the_closure_cap_cuts_the_reach_and_says_so(self, monkeypatch):
        # the OTHER cap: the closure read is bounded, so 'reach' says when it stopped short rather
        # than reporting a count of the design.
        monkeypatch.setattr(params, "_TRACE_CAP", 2)
        _wire(monkeypatch, FakeUserParameter(name="Driver", dependents=_kids(3)))
        out = _payload(params.handler(name="Driver", trace=True))
        assert out["reach"]["truncated"] is True and out["reach"]["rows_per_hop"] == {"1": 2}

    def test_dependencies_are_the_DIRECT_references_and_reach_counts_the_rest(self, monkeypatch):
        # the upstream side is a closure too: the row list is what StepDrop's own expression names,
        # and the ancestors behind PartHt are counted rather than listed.
        part_ht = FakeUserParameter(name="PartHt", expression="Stock - 5 mm")
        stock = FakeUserParameter(name="Stock", expression="50 mm")
        step = FakeUserParameter(name="StepDrop", expression="PartHt / 4",
                                 dependencies=[part_ht, stock])
        _wire(monkeypatch, step)
        out = _payload(params.handler(name="StepDrop", trace=True))
        assert [row["name"] for row in out["dependencies"]] == ["PartHt"]
        assert "hops" not in out["dependencies"][0]          # a direct reference, not a walk
        assert out["reach"]["upstream"] == 2                 # PartHt and the Stock behind it

    def test_a_cut_dependency_scan_says_the_list_is_incomplete(self, monkeypatch):
        # both names are referenced and both are in the closure, so a one-row page is a CUT - and a
        # caller reading it as the whole upstream would edit against a reference it never saw.
        first = FakeUserParameter(name="PartHt", expression="40 mm")
        second = FakeUserParameter(name="Stock", expression="50 mm")
        monkeypatch.setattr(params, "_TRACE_CAP", 1)
        step = FakeUserParameter(name="StepDrop", expression="PartHt + Stock",
                                 dependencies=[first, second])
        _wire(monkeypatch, step)
        out = _payload(params.handler(name="StepDrop", trace=True))
        assert len(out["dependencies"]) == 1 and out["dependencies_truncated"] is True

    def test_a_parameter_nothing_follows_publishes_empty_lists(self, monkeypatch):
        # an empty trace is an ANSWER ("nothing follows this") - an error here would read as a
        # broken parameter.
        _wire(monkeypatch, FakeUserParameter(name="Lonely", expression="5 mm"))
        out = _payload(params.handler(name="Lonely", trace=True))
        assert out["dependencies"] == [] and out["dependents"] == []
        assert out["sketch_consumers"] == [] and "EXPRESSION" in out["note"]
        assert out["reach"] == {"rows_per_hop": {}, "unlinked": 0, "upstream": 0, "downstream": 0,
                                "features": 0, "sketches": 0, "max_hops": 0, "truncated": False}

    def test_a_model_parameter_row_carries_its_owner_role_and_timeline_index(self, monkeypatch):
        # the row an agent acts on: WHICH feature the change moves, and where that feature sits in
        # the timeline - the index design_get(include=['timeline']) addresses it by. The role is
        # the PARAMETER's slot on that maker, so it rides the entry, not the row.
        placed = FakeFeature("Extrude1", timeline_object=FakeTimelineObject(name="Extrude1", index=4))
        d1 = FakeModelParameter(name="d1", owner=placed, role="AlongDistance",
                                expression="PartHt - StepDrop")
        loose = FakeModelParameter(name="d2", owner=FakeFeature("Extrude2"), role="TaperAngle",
                                   expression="PartHt / 8")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[d1, loose]),
              extra_params=[d1, loose])
        rows = _payload(params.handler(name="PartHt", trace=True))["dependents"]
        assert rows[0]["owner"] == "Extrude1" and "owner_type" in rows[0]
        assert rows[0]["owner_timeline_index"] == 4
        assert rows[0]["parameters"] == [{"name": "d1", "hops": 1, "role": "AlongDistance",
                                          "expression": "PartHt - StepDrop"}]
        assert "role" not in rows[0]
        # an owner with no timeline item publishes no index rather than a fabricated one
        assert "owner_timeline_index" not in rows[1]

    def test_one_maker_owning_two_parameters_is_ONE_row(self, monkeypatch):
        # the grouping: a feature whose height and taper both follow the driver is one thing to
        # edit, and a row per parameter pays for the maker's identity twice.
        owner = FakeFeature("Extrude1", timeline_object=FakeTimelineObject(name="Extrude1", index=4))
        d1 = FakeModelParameter(name="d1", owner=owner, role="AlongDistance", expression="PartHt")
        d2 = FakeModelParameter(name="d2", owner=owner, role="TaperAngle", expression="PartHt / 8")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[d1, d2]),
              extra_params=[d1, d2])
        rows = _payload(params.handler(name="PartHt", trace=True))["dependents"]
        assert len(rows) == 1 and rows[0]["owner"] == "Extrude1"
        assert [e["name"] for e in rows[0]["parameters"]] == ["d1", "d2"]
        assert [e["role"] for e in rows[0]["parameters"]] == ["AlongDistance", "TaperAngle"]

    def test_two_makers_nothing_tells_apart_stay_two_rows(self, monkeypatch):
        # the worst case a pooled key would hide: two makers with no name, no token, the same type
        # and the same timeline index. Two rows is honest; ONE row would assert a shared maker.
        def nameless(index):
            return SimpleNamespace(timelineObject=FakeTimelineObject(name="x", index=index))

        one = FakeModelParameter(name="d1", owner=nameless(4), role="AlongDistance",
                                 expression="PartHt")
        two = FakeModelParameter(name="d2", owner=nameless(4), role="TaperAngle",
                                 expression="PartHt / 8")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[one, two]),
              extra_params=[one, two])
        rows = _payload(params.handler(name="PartHt", trace=True))["dependents"]
        assert len(rows) == 2
        assert [e["name"] for r in rows for e in r["parameters"]] == ["d1", "d2"]

    def test_two_makers_with_distinct_tokens_stay_two_rows(self, monkeypatch):
        # the identity path: a token that READS separates the makers before any fallback applies.
        def tokened(token):
            return SimpleNamespace(entityToken=token,
                                   timelineObject=FakeTimelineObject(name="x", index=4))

        one = FakeModelParameter(name="d1", owner=tokened("T:1"), role="AlongDistance",
                                 expression="PartHt")
        two = FakeModelParameter(name="d2", owner=tokened("T:2"), role="TaperAngle",
                                 expression="PartHt / 8")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[one, two]),
              extra_params=[one, two])
        rows = _payload(params.handler(name="PartHt", trace=True))["dependents"]
        assert len(rows) == 2 and _payload(
            params.handler(name="PartHt", trace=True))["reach"]["features"] == 2

    def test_a_deeper_entry_drops_the_expression_the_direct_ring_carries(self, monkeypatch):
        # past the ring the caller drives itself, the row is a CENSUS of what follows - the
        # expression is the weight a deep page exists to avoid.
        owner = FakeFeature("Extrude1", timeline_object=FakeTimelineObject(name="Extrude1", index=4))
        deep = FakeModelParameter(name="d9", owner=owner, role="TaperAngle", expression="StepDrop")
        step = FakeUserParameter(name="StepDrop", expression="PartHt / 4")
        bore = FakeUserParameter(name="BoreDia", expression="StepDrop * 1.2")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[step, bore, deep]),
              extra_params=[deep])
        rows = _payload(params.handler(name="PartHt", trace=True, trace_depth=2))["dependents"]
        by_name = {r.get("name"): r for r in rows if "parameters" not in r}
        assert by_name["StepDrop"]["expression"] == "PartHt / 4"   # the direct ring carries its own
        assert "expression" not in by_name["BoreDia"]              # the deep USER row drops it too
        maker = next(r for r in rows if "parameters" in r)
        assert maker["parameters"] == [{"name": "d9", "hops": 2, "role": "TaperAngle"}]

    def test_a_sketch_dimension_owner_is_indexed_through_its_sketch(self, monkeypatch):
        sketch = Sketch(name="BracketBoss", entity_token="SK1",
                        timeline_object=FakeTimelineObject(name="BracketBoss", index=2))
        dim = FakeModelParameter(name="d7", owner=sketch, role="Diameter", expression="BossDia")
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=[dim]), extra_params=[dim])
        row = _payload(params.handler(name="BossDia", trace=True))["dependents"][0]
        assert row["owner"] == "BracketBoss" and row["owner_timeline_index"] == 2

    def test_an_owner_that_only_knows_its_sketch_is_indexed_through_that_sketch(self, monkeypatch):
        # the second route: an owner that is not itself a timeline item answers parentSketch, and
        # the sketch it lives in carries the index. Without it such a row publishes none.
        sketch = Sketch(name="BracketBoss", entity_token="SK1",
                        timeline_object=FakeTimelineObject(name="BracketBoss", index=2))
        owner = SimpleNamespace(name="Linear Dimension-2", parentSketch=sketch)
        dim = FakeModelParameter(name="d7", owner=owner, role="Dimension", expression="BossDia")
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=[dim]), extra_params=[dim])
        row = _payload(params.handler(name="BossDia", trace=True))["dependents"][0]
        assert row["owner_sketch"] == "BracketBoss" and row["owner_timeline_index"] == 2

    def test_sketch_consumers_name_the_features_that_eat_the_traced_sketch(self, monkeypatch):
        sketch = Sketch(name="BracketBoss", entity_token="SK1")
        comp = MakeComp(name="Bracket")
        feature = _Extrude("Extrude3", Profile(parent_sketch=sketch), component=comp)
        dim = FakeModelParameter(name="d7", owner=sketch, role="Diameter", expression="BossDia")
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=[dim]),
              extra_params=[dim],
              timeline=[FakeTimelineObject(name="Extrude3", index=6, entity=feature)])
        rows = _payload(params.handler(name="BossDia", trace=True))["sketch_consumers"]
        assert [r["sketch"] for r in rows] == ["BracketBoss"]
        assert rows[0]["features"] == [{"name": "Extrude3", "type": "_Extrude",
                                        "component": "Bracket", "timeline_index": 6}]

    def test_tracing_a_sketch_dimension_names_the_features_that_eat_ITS_sketch(self, monkeypatch):
        # the traced parameter's OWN sketch counts: trace the dimension itself and the consumers
        # are the answer - reading only what it REACHES leaves the list empty while the summary
        # beside it names the sketch.
        sketch = Sketch(name="BracketBoss", entity_token="SK1")
        feature = _Extrude("Extrude3", Profile(parent_sketch=sketch),
                           component=MakeComp(name="Bracket"))
        dim = FakeModelParameter(name="d7", owner=sketch, role="Diameter")
        design = MakeDesign(user_parameters=FakeUserParameters([]), all_parameters=[dim],
                            timeline=FakeTimeline([FakeTimelineObject(name="Extrude3", index=6,
                                                                      entity=feature)]))
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="d7", trace=True))
        assert out["dependents"] == []                   # nothing follows the dimension itself
        rows = out["sketch_consumers"]
        assert [r["sketch"] for r in rows] == ["BracketBoss"]
        assert [f["name"] for f in rows[0]["features"]] == ["Extrude3"]

    def test_the_feature_side_is_matched_through_the_proxys_NATIVE_sketch(self, monkeypatch):
        # MEASURED: a parameter's createdBy answers the NATIVE sketch while the feature reaches the
        # same sketch through its occurrence PROXY - the two entityTokens differ and compare False.
        # Matching on either wrapper's own token finds nothing.
        native = Sketch(name="BracketBoss", entity_token="SK1")
        proxy = body_proxy(native, entity_token="SK1-IN-BRACKET:1")
        feature = _Extrude("Extrude3", Profile(parent_sketch=proxy),
                           component=MakeComp(name="Bracket"))
        dim = FakeModelParameter(name="d7", owner=native, role="Diameter", expression="BossDia")
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=[dim]), extra_params=[dim],
              timeline=[FakeTimelineObject(name="Extrude3", index=6, entity=feature)])
        rows = _payload(params.handler(name="BossDia", trace=True))["sketch_consumers"]
        assert proxy.entityToken != native.entityToken
        assert [f["name"] for f in rows[0]["features"]] == ["Extrude3"]

    def test_a_loft_is_reached_through_its_sections_not_a_profile(self, monkeypatch):
        # MEASURED: LoftFeature carries no 'profile' at all - each section's own entity names the
        # sketch, so a loft driven by a traced dimension is invisible without that route.
        sketch = Sketch(name="LoftA", entity_token="SK-A")
        section = SimpleNamespace(entity=Profile(parent_sketch=sketch))
        loft = FakeFeature("Loft1", parent_component=MakeComp(name="LoftCameo"))
        loft.loftSections = _NamedCollection([section])
        dim = FakeModelParameter(name="d9", owner=sketch, role="Diameter", expression="BaseDia")
        _wire(monkeypatch, FakeUserParameter(name="BaseDia", dependents=[dim]), extra_params=[dim],
              timeline=[FakeTimelineObject(name="Loft1", index=3, entity=loft)])
        rows = _payload(params.handler(name="BaseDia", trace=True))["sketch_consumers"]
        assert [r["sketch"] for r in rows] == ["LoftA"]
        assert [f["name"] for f in rows[0]["features"]] == ["Loft1"]

    def test_a_sketch_is_matched_by_identity_not_by_name(self, monkeypatch):
        # two components each hold a sketch named 'Profile'; only ONE owns the traced dimension. A
        # by-name match would hand back the other component's feature as a consumer of this change.
        traced = Sketch(name="Profile", entity_token="SK1")
        other = Sketch(name="Profile", entity_token="SK2")
        mine = _Extrude("Mine", Profile(parent_sketch=traced), component=MakeComp(name="A"))
        theirs = _Extrude("Theirs", Profile(parent_sketch=other), component=MakeComp(name="B"))
        dim = FakeModelParameter(name="d7", owner=traced, role="Diameter", expression="BossDia")
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=[dim]), extra_params=[dim],
              timeline=[FakeTimelineObject(name="Mine", index=1, entity=mine),
                        FakeTimelineObject(name="Theirs", index=2, entity=theirs)])
        rows = _payload(params.handler(name="BossDia", trace=True))["sketch_consumers"]
        assert [f["name"] for f in rows[0]["features"]] == ["Mine"]

    def test_the_sketch_list_is_capped_and_says_so(self, monkeypatch):
        # sketch_consumers is the one array that grows with the design: a driver read by every
        # sketch in an assembly would otherwise publish one row per sketch.
        monkeypatch.setattr(params, "_CONSUMER_SKETCHES_CAP", 1)
        dims = [FakeModelParameter(name=f"d{i}", role="Diameter", expression="BossDia",
                                   owner=Sketch(name=f"S{i}", entity_token=f"SK{i}"))
                for i in range(2)]
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=dims), extra_params=dims)
        out = _payload(params.handler(name="BossDia", trace=True))
        assert len(out["sketch_consumers"]) == 1 and out["sketch_consumers_truncated"] is True

    def test_the_feature_list_under_a_sketch_is_capped_and_says_so(self, monkeypatch):
        monkeypatch.setattr(params, "_CONSUMER_FEATURES_CAP", 1)
        sketch = Sketch(name="BracketBoss", entity_token="SK1")
        dim = FakeModelParameter(name="d7", owner=sketch, role="Diameter", expression="BossDia")
        eaters = [_Extrude(f"Extrude{i}", Profile(parent_sketch=sketch),
                           component=MakeComp(name="Bracket")) for i in range(2)]
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=[dim]), extra_params=[dim],
              timeline=[FakeTimelineObject(name=e.name, index=i, entity=e)
                        for i, e in enumerate(eaters)])
        out = _payload(params.handler(name="BossDia", trace=True))
        assert len(out["sketch_consumers"][0]["features"]) == 1
        assert out["sketch_consumers_truncated"] is True

    def test_a_full_consumer_list_is_not_flagged(self, monkeypatch):
        # the boundary: cap-many sketches and cap-many features is a COMPLETE answer.
        monkeypatch.setattr(params, "_CONSUMER_SKETCHES_CAP", 1)
        monkeypatch.setattr(params, "_CONSUMER_FEATURES_CAP", 1)
        sketch = Sketch(name="BracketBoss", entity_token="SK1")
        dim = FakeModelParameter(name="d7", owner=sketch, role="Diameter", expression="BossDia")
        eater = _Extrude("Extrude1", Profile(parent_sketch=sketch), component=MakeComp(name="B"))
        _wire(monkeypatch, FakeUserParameter(name="BossDia", dependents=[dim]), extra_params=[dim],
              timeline=[FakeTimelineObject(name="Extrude1", index=1, entity=eater)])
        out = _payload(params.handler(name="BossDia", trace=True))
        assert len(out["sketch_consumers"]) == 1 and "sketch_consumers_truncated" not in out

    def test_an_unreadable_closure_is_an_error_not_an_empty_answer(self, monkeypatch):
        # the cardinal sin in a READ: 'dependents: []' IS the answer "nothing follows this", so a
        # list that could not be read must refuse rather than publish that answer.
        target = FakeUserParameter(name="PartHt")
        target.dependentParameters = _NamedCollection([], raises="parameter table is locked")
        _wire(monkeypatch, target)
        msg = error_message(params.handler(name="PartHt", trace=True))
        assert "'PartHt'" in msg and "could not be read" in msg and "without trace" in msg

    def test_a_readable_empty_closure_still_answers(self, monkeypatch):
        # the other side of that guard: a closure that answers zero is an answer, not a refusal.
        _wire(monkeypatch, FakeUserParameter(name="PartHt"))
        out = _payload(params.handler(name="PartHt", trace=True))
        assert out["dependents"] == [] and out["reach"]["downstream"] == 0

    def test_trace_without_a_name_is_refused_naming_both_inputs(self, monkeypatch):
        monkeypatch.setattr(params._common, "design", lambda: _design(FakeUserParameters([]), []))
        msg = error_message(params.handler(trace=True))
        assert "'trace'" in msg and "'name'" in msg

    def test_the_live_sweep_predicate_reads_this_payload(self, monkeypatch):
        # the seam no offline gate sees: the sweep row asserts on THESE keys, so a key renamed here
        # reaches the live run as a red on a working tool. The rig this mirrors: the driver reaches
        # a derived parameter, that one the boss sketch's own dimension, and the extrude eats it.
        sketch = Sketch(name="BracketBoss", entity_token="SK1",
                        timeline_object=FakeTimelineObject(name="BracketBoss", index=2))
        feature = _Extrude("Extrude3", Profile(parent_sketch=sketch),
                           component=MakeComp(name="Bracket"))
        dim = FakeModelParameter(name="d7", owner=sketch, role="Diameter",
                                 expression="BoreDia * 0.5")
        step = FakeUserParameter(name="StepDrop", expression="PartHt / 4")
        bore = FakeUserParameter(name="BoreDia", expression="PartHt * 0.3")
        _wire(monkeypatch, FakeUserParameter(name="PartHt", dependents=[step, bore, dim]),
              extra_params=[dim],
              timeline=[FakeTimelineObject(name="Extrude3", index=6, entity=feature)])
        out = _payload(params.handler(name="PartHt", trace=True, trace_depth=3))
        assert verify_core._param_traced("PartHt", direct=("StepDrop", "BoreDia"))(out) is True

    def test_the_default_read_carries_no_trace(self, monkeypatch):
        # the walk is opt-in cost: a plain single-name read pays for none of it.
        _wire(monkeypatch, FakeUserParameter(name="PartHt",
                                             dependents=[FakeUserParameter(name="StepDrop")]))
        out = _payload(params.handler(name="PartHt"))
        assert "dependents" not in out and "sketch_consumers" not in out
