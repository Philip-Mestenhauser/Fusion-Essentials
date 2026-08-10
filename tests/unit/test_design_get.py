"""Tests for `design_get` - the first RICH READ (one tool, default slice + include= deeper slices).

The rich-read shape tests/CLAUDE.md names as the canonical one to copy.

Pinned: the DEFAULT call returns only the orientation slice (mode summary + health + tree_summary) and
NONE of the heavy slices; each include= adds exactly its slice; the default note advertises the
remaining slices; unknown include errors; no-active-design guards. What each slice reads out of Fusion
is proven by live validation, not re-mocked here.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, error_message, _NamedCollection

dg = load_tool("design_get")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── ROUTER composition: stub the slice SEAMS (not the source-tool internals) ────────────────────────
#
# design_get's slices DELEGATE to the 5 source handlers — so the router's own job (compose the default,
# add include= slices, advertise the rest, degrade gracefully) is what the unit tests pin. We stub the
# _slice_* functions to fixed payloads and assert the COMPOSITION. The real slice→source-handler
# delegation is proven by live validation (the honest test of cross-tool wiring), not by mocking 5
# handlers' internals (which would recreate the bespoke-fake problem this convention exists to avoid).

@pytest.fixture
def stub_slices(monkeypatch):
    monkeypatch.setattr(dg._common, "design", lambda: object())   # a non-None design
    monkeypatch.setattr(dg, "_slice_mode", lambda d: (
        {"design_type": "parametric", "has_timeline": True, "timeline_feature_count": 4,
         "in_base_feature_edit": False, "can": {"timeline_ops": True}}, None))
    monkeypatch.setattr(dg, "_slice_health", lambda d: (
        {"healthy": True, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []}, None))
    monkeypatch.setattr(dg, "_fingerprint", lambda d: {"bodies": 2, "sketches": 3})
    monkeypatch.setattr(dg, "_slice_tree", lambda d, max_depth, component: (
        {"root": "Root", "max_depth": max_depth, "children": []}, None))
    monkeypatch.setattr(dg, "_slice_timeline", lambda d, include_suppressed, group: (
        {"count": 4, "timeline": []}, None))
    monkeypatch.setattr(dg, "_slice_configurations", lambda d: ({"table_name": "Configs"}, None))
    monkeypatch.setattr(dg, "_slice_materials", lambda d, library, name_filter, max_results: (
        {"kind": "materials", "library": library, "name_filter": name_filter,
         "max_results": max_results, "document": {"count": 1}, "libraries": []}, None))
    monkeypatch.setattr(dg, "_slice_appearances", lambda d, library, name_filter, max_results: (
        {"kind": "appearances", "library": library, "document": {"count": 1}, "libraries": []}, None))


# ── default orientation slice is DENSE + bounded (the core rich-read contract) ──────────────────────

class TestDefaultSlice:
    def test_default_is_the_dense_orientation(self, stub_slices):
        out = _payload(dg.handler())                   # no include=
        # the headline: design_type + feature_count + TIMELINE_healthy + a CONTENT fingerprint.
        # The name is scoped: timeline_healthy is timeline-only (NOT stale refs — that's a tree node's
        # is_out_of_date, and the doc-wide verdict is workspace_orient.is_healthy).
        assert out["design_type"] == "parametric" and out["feature_count"] == 4
        assert out["timeline_healthy"] is True and "healthy" not in out
        assert out["contents"] == {"bodies": 2, "sketches": 3}   # "what IS this model"
        # the HEAVY slices must be absent by default (the anti-flood contract)
        assert "tree" not in out and "timeline" not in out and "configurations" not in out
        assert "mode_detail" not in out                # the full can{} map is opt-in only
        assert "materials" not in out and "appearances" not in out   # the catalog is opt-in too

    def test_default_omits_noise_when_healthy(self, stub_slices):
        # a healthy design carries no health DETAIL block + no in_base_feature_edit:false
        out = _payload(dg.handler())
        assert "health" not in out                     # 'timeline_healthy: True' says it all
        assert "in_base_feature_edit" not in out       # only present when True

    def test_default_emits_pointers_for_hidden_content(self, monkeypatch, stub_slices):
        # a design WITH parameters gets a pointers block naming param_get/param_set - the inbound
        # breadcrumb to the otherwise-undiscoverable param family.
        monkeypatch.setattr(dg, "_fingerprint", lambda d: {"parameters": 40, "bodies": 2})
        out = _payload(dg.handler())
        assert "param_get" in out["pointers"]["parameters"]

    def test_default_no_pointers_when_only_obvious_content(self, monkeypatch, stub_slices):
        # stub_slices' fingerprint is bodies+sketches only (obvious), and no CAM -> no pointers block.
        monkeypatch.setattr(dg, "_has_cam", lambda d: False)
        out = _payload(dg.handler())
        assert "pointers" not in out

    def test_default_points_at_cam_when_cam_present(self, monkeypatch, stub_slices):
        # a CAM document (design_get is blind to its machining state) gets a cam_get breadcrumb.
        monkeypatch.setattr(dg, "_has_cam", lambda d: True)
        out = _payload(dg.handler())
        assert "cam_get" in out["pointers"]["cam"]

    def test_default_surfaces_health_detail_when_unhealthy(self, monkeypatch, stub_slices):
        monkeypatch.setattr(dg, "_slice_health", lambda d: (
            {"healthy": False, "error_count": 1, "warning_count": 0,
             "errors": ["Extrude3"], "warnings": []}, None))
        out = _payload(dg.handler())
        assert out["timeline_healthy"] is False and out["health"]["errors"] == ["Extrude3"]

    def test_default_note_advertises_remaining_slices(self, stub_slices):
        out = _payload(dg.handler())
        assert "include=" in out["note"]               # un-named flags are invisible — must advertise


# ── each include= adds exactly its slice ────────────────────────────────────────────────────────────

class TestIncludeSlices:
    @pytest.mark.parametrize("slice_name,key", [
        ("tree", "tree"),
        ("timeline", "timeline"),
        ("mode", "mode_detail"),
        ("configurations", "configurations"),
        ("materials", "materials"),
        ("appearances", "appearances"),
    ])
    def test_include_adds_the_slice(self, stub_slices, slice_name, key):
        out = _payload(dg.handler(include=[slice_name]))
        assert key in out

    def test_include_mode_adds_full_capability_map(self, stub_slices):
        out = _payload(dg.handler(include=["mode"]))
        assert "can" in out["mode_detail"]             # the full map, vs the summary in the default

    def test_multiple_includes(self, stub_slices):
        out = _payload(dg.handler(include=["tree", "timeline"]))
        assert "tree" in out and "timeline" in out

    def test_catalog_slices_are_independent(self, stub_slices):
        # materials and appearances are two projections of one walk but two separate slices:
        # asking for one must not pull the other.
        out = _payload(dg.handler(include=["materials"]))
        assert out["materials"]["kind"] == "materials" and "appearances" not in out


class TestCatalogSlices:
    """The materials/appearances slices - the catalog an agent picks a name FROM before assigning it.
    Both delegate to the one bounded walk in _materials; the router's job is passing the scope flags
    through, keeping the two kinds apart, and advertising the flags."""

    def test_scope_flags_reach_the_slice(self, stub_slices):
        out = _payload(dg.handler(include=["materials"], library="Fusion Material Library",
                                  name_filter="steel", max_results=10))
        assert out["materials"]["library"] == "Fusion Material Library"
        assert out["materials"]["name_filter"] == "steel" and out["materials"]["max_results"] == 10

    def test_default_note_advertises_the_catalog_slices_and_flags(self, stub_slices):
        note = _payload(dg.handler())["note"]
        assert "materials" in note and "appearances" in note
        assert "library" in note and "name_filter" in note   # un-named flags are invisible

    def test_slice_error_fails_the_read(self, monkeypatch, stub_slices):
        monkeypatch.setattr(dg, "_slice_materials",
                            lambda d, library, name_filter, max_results: (None, dg.error("no such library")))
        res = dg.handler(include=["materials"])
        assert res["isError"] and "no such library" in error_message(res)

    def test_each_slice_browses_its_own_kind(self, monkeypatch):
        # the delegation itself: two slices, one walk, DIFFERENT kind - passing 'materials' for both
        # would return the wrong catalog under the right label.
        seen = []
        monkeypatch.setattr(dg._materials, "browse",
                            lambda design, kind, library, name_filter, max_results: (
                                seen.append(kind) or ({"kind": kind}, None)))
        dg._slice_materials(object(), "", "", 0)
        dg._slice_appearances(object(), "", "", 0)
        assert seen == ["materials", "appearances"]


class TestFingerprint:
    """The content fingerprint (`_fingerprint`) - the 'what IS this model' counts in the default slice."""

    def _design(self, bodies=0, sketches=0, comps=0, joints=0, asbuilt=0, params=0):
        from types import SimpleNamespace
        c = lambda n: SimpleNamespace(count=n)
        root = SimpleNamespace(bRepBodies=c(bodies), sketches=c(sketches),
                               allOccurrences=c(comps), joints=c(joints), asBuiltJoints=c(asbuilt))
        return SimpleNamespace(rootComponent=root, userParameters=c(params))

    def test_counts_both_joint_collections(self):
        # joints and asBuiltJoints are separate collections; the count must include both.
        fp = dg._fingerprint(self._design(joints=2, asbuilt=3))
        assert fp["joints"] == 5

    def test_as_built_only_still_counts(self):
        # a design whose ONLY joints are as-built must not report 0 joints.
        fp = dg._fingerprint(self._design(asbuilt=1))
        assert fp["joints"] == 1

    def test_counts_user_parameters(self):
        # parameters must appear in the fingerprint - an invisible parameter count is the whole reason
        # the param_* family would have no inbound breadcrumb.
        fp = dg._fingerprint(self._design(params=40))
        assert fp["parameters"] == 40

    def test_zero_counts_omitted(self):
        fp = dg._fingerprint(self._design(bodies=1))
        assert fp == {"bodies": 1}                 # no joints/sketches/components/parameters when zero

    def test_bodies_and_sketches_are_design_wide_not_root_only(self):
        # bodies/sketches must be summed across every component (via the shared
        # _common.design_wide_counts, the same one workspace_orient uses), not read off the root
        # alone - a design whose geometry lives in sub-components would otherwise under-report (a
        # sketch-only-in-sub-components doc reading sketches:0 despite having 2).
        c = lambda n: SimpleNamespace(count=n)
        root = SimpleNamespace(bRepBodies=c(0), sketches=c(0),
                               allOccurrences=c(2), joints=c(0), asBuiltJoints=c(0))
        subs = [SimpleNamespace(bRepBodies=c(1), sketches=c(1)),
                SimpleNamespace(bRepBodies=c(2), sketches=c(1))]
        items = [root] + subs
        design = SimpleNamespace(rootComponent=root, userParameters=c(0),
                                 allComponents=SimpleNamespace(count=len(items), item=lambda i: items[i]))
        fp = dg._fingerprint(design)
        assert fp["bodies"] == 3      # 0 (root) + 1 + 2, NOT the root-only 0
        assert fp["sketches"] == 2    # 0 (root) + 1 + 1, NOT the root-only 0


class TestContentPointers:
    """`_content_pointers` - the inbound breadcrumb: a present content class names the tool acting on it."""

    def test_parameters_present_points_at_param_tools(self):
        p = dg._content_pointers({"parameters": 40, "bodies": 2})
        assert "parameters" in p and "param_get" in p["parameters"] and "param_set" in p["parameters"]

    def test_obvious_classes_get_no_pointer(self):
        # bodies/sketches are omitted - every agent already knows model_*/sketch_*; only the hidden
        # families (parameters/joints/components) get a breadcrumb.
        p = dg._content_pointers({"bodies": 5, "sketches": 3})
        assert p == {}

    def test_only_present_classes_pointed(self):
        p = dg._content_pointers({"joints": 3})
        assert set(p) == {"joints"} and "assembly_get" in p["joints"]

    def test_empty_contents_no_pointers(self):
        assert dg._content_pointers({}) == {} and dg._content_pointers(None) == {}


class TestHasCam:
    """`_has_cam` - a CAM document's machining state is invisible to design_get, so it needs a cam_get
    breadcrumb. It delegates to the shared _cam_common.get_cam() (the active document's CAM product)."""

    def test_cam_present(self, monkeypatch):
        monkeypatch.setattr(dg._cam_common, "get_cam", lambda: (object(), None))
        assert dg._has_cam(None) is True

    def test_no_cam(self, monkeypatch):
        monkeypatch.setattr(dg._cam_common, "get_cam", lambda: (None, "no CAM data"))
        assert dg._has_cam(None) is False


class TestTimelineSlice:
    """Timeline slice logic moved into design_get (_entity_type / _object_summary / _slice_timeline)."""

    def _tlobj(self, index=0, name="Extrude1", is_group=False, suppressed=False, rolled_back=False,
               parent_group=None, health=0, message=None, entity_name="ExtrudeFeature"):
        from types import SimpleNamespace
        _Ent = type(entity_name, (), {})
        pg = SimpleNamespace(name=parent_group) if parent_group is not None else None
        return SimpleNamespace(index=index, name=name, isGroup=is_group, isSuppressed=suppressed,
                               isRolledBack=rolled_back, parentGroup=pg, healthState=health,
                               errorOrWarningMessage=message, entity=(None if is_group else _Ent()))

    def test_entity_type_group(self):
        from types import SimpleNamespace
        assert dg._entity_type(SimpleNamespace(isGroup=True)) == "TimelineGroup"

    def test_entity_type_class_name(self):
        assert dg._entity_type(self._tlobj()) == "ExtrudeFeature"

    def test_object_summary_maps_health_label(self):
        assert dg._object_summary(self._tlobj(health=2))["health"] == "error"

    def test_object_summary_message_only_when_present(self):
        assert dg._object_summary(self._tlobj(message="x"))["message"] == "x"
        assert "message" not in dg._object_summary(self._tlobj(message=None))

    def _design_with(self, items, marker=0, groups=()):
        from types import SimpleNamespace
        tl = SimpleNamespace(_items=list(items), markerPosition=marker, timelineGroups=list(groups),
                             count=len(items), item=lambda i, _it=list(items): _it[i])
        return SimpleNamespace(timeline=tl)

    def test_slice_returns_all_with_marker_count(self):
        d = self._design_with([self._tlobj(0, "A"), self._tlobj(1, "B")], marker=2)
        out, err = dg._slice_timeline(d, include_suppressed=True, group="")
        assert err is None and out["count"] == 2 and out["marker_position"] == 2
        assert [o["name"] for o in out["timeline"]] == ["A", "B"]

    def test_uncountable_timeline_refuses_instead_of_reading_empty(self):
        # timeline.count raising means the timeline could not be read AT ALL. Answering with an
        # empty list + count 0 would read as "this design has no history" - a false answer a caller
        # gates on. The refusal carries the platform's reason.
        from types import SimpleNamespace
        class _Uncountable:
            markerPosition = 0
            timelineGroups = []
            @property
            def count(self): raise RuntimeError("timeline is mid-recompute")
            def item(self, i): raise AssertionError("must not be reached")
        out, err = dg._slice_timeline(SimpleNamespace(timeline=_Uncountable()),
                                      include_suppressed=True, group="")
        assert out is None and err["isError"] is True
        assert "mid-recompute" in err["message"]

    def test_slice_include_suppressed_false(self):
        d = self._design_with([self._tlobj(0, "Live"), self._tlobj(1, "Hid", suppressed=True)])
        out, _ = dg._slice_timeline(d, include_suppressed=False, group="")
        assert out["returned"] == 1 and out["count"] == 2

    def test_slice_group_filter(self):
        d = self._design_with([self._tlobj(0, "A", parent_group="W"),
                               self._tlobj(1, "B", parent_group="F")])
        out, _ = dg._slice_timeline(d, include_suppressed=True, group="W")
        assert [o["name"] for o in out["timeline"]] == ["A"]

    def test_slice_summary_states_and_exceptions(self):
        # the timeline slice leads with a states tally + exceptions = the FAILED features
        # (error/warning health). A suppressed (intentional) feature is NOT an exception.
        d = self._design_with([self._tlobj(0, "Good", health=0),
                               self._tlobj(1, "Bad", health=2),                    # error
                               self._tlobj(2, "Warned", health=1),                 # warning
                               self._tlobj(3, "Off", suppressed=True, health=3)])  # suppressed
        out, _ = dg._slice_timeline(d, include_suppressed=True, group="")
        s = out["summary"]
        assert s["states"]["error"] == 1 and s["states"]["warning"] == 1 and s["states"]["healthy"] == 1
        names = {e["name"] for e in s["exceptions"]}
        assert names == {"Bad", "Warned"}                # the failures; suppressed excluded

    def test_slice_no_timeline_errors(self):
        from types import SimpleNamespace
        class _NoTL:
            @property
            def timeline(self): raise RuntimeError("direct design")
        out, err = dg._slice_timeline(_NoTL(), include_suppressed=True, group="")
        assert out is None and err["isError"] is True


class TestTimelineRazor:
    """Keeping rows terse: a healthy timeline row drops its boring-default fields; an abnormal row keeps
    them and stands out. Tests _terse directly (pure, no design needed)."""

    def test_healthy_row_drops_noise(self):
        row = {"index": 1, "name": "Extrude1", "type": "ExtrudeFeature", "is_group": False,
               "is_suppressed": False, "is_rolled_back": False, "parent_group": None, "health": "healthy"}
        out = dg.terse(row, dg._TIMELINE_NOISE)
        assert out == {"index": 1, "name": "Extrude1", "type": "ExtrudeFeature"}

    def test_abnormal_row_keeps_its_flags(self):
        row = {"index": 2, "name": "Extrude2", "type": "ExtrudeFeature", "is_suppressed": True,
               "is_rolled_back": False, "health": "error"}
        out = dg.terse(row, dg._TIMELINE_NOISE)
        assert out["is_suppressed"] is True and out["health"] == "error"   # the interesting bits pop
        assert "is_rolled_back" not in out                                  # the boring one still dropped

    def test_tree_scope_params_pass_through(self, stub_slices):
        out = _payload(dg.handler(include=["tree"], max_depth=5))
        assert out["tree"]["max_depth"] == 5           # filter args reach the slice

    def test_configurations_degrades_for_non_configured_design(self, monkeypatch, stub_slices):
        # a non-configured design errors in the source -> design_get degrades to a marker, not a failure
        monkeypatch.setattr(dg, "_slice_configurations",
                            lambda d: (None, {"isError": True, "message": "not a configured design"}))
        out = _payload(dg.handler(include=["configurations"]))
        assert out["configurations"]["configured"] is False


# ── guards ──────────────────────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_include_errors(self, monkeypatch):
        monkeypatch.setattr(dg._common, "design", lambda: object())   # a design, so we reach the check
        res = dg.handler(include=["bogus"])
        msg = error_message(res)
        assert "bogus" in msg.lower() or "unknown" in msg.lower()

    def test_no_active_design_guard(self, monkeypatch):
        monkeypatch.setattr(dg._common, "design", lambda: None)
        res = dg.handler()
        assert error_message(res)


# ── include normalization (pure, no design needed) ─────────────────────────────────────────────────

class TestNormalizeInclude:
    def test_none_empty(self):
        assert dg._normalize_include(None) == [] and dg._normalize_include("") == []

    def test_comma_string(self):
        assert dg._normalize_include("tree, timeline") == ["tree", "timeline"]

    def test_list_lowercased(self):
        assert dg._normalize_include(["Tree", "TIMELINE"]) == ["tree", "timeline"]


# ── tree scoping (_find_occurrence_by_name): component-name scope + ambiguity refusal ───────────────
#
# Tree scoping resolves a name to the occurrence to root at: an occurrence name/fullPathName goes
# through the shared ambiguity-refusing resolver (a name shared by several instances is refused, not
# matched to the first), while a COMPONENT name roots at its first instance (all instances share one
# structure).

class _Occ:
    def __init__(self, name, comp_name, full=None):
        self.name = name
        self.fullPathName = full or name
        self.component = SimpleNamespace(name=comp_name)
        self.childOccurrences = []


def _wire_tree(monkeypatch, occs):
    root = SimpleNamespace(occurrences=list(occs), allOccurrences=list(occs))
    design = SimpleNamespace(rootComponent=root)
    monkeypatch.setattr(dg._common, "design", lambda: design)
    return root


class TestFindOccurrenceByName:
    def test_component_name_roots_at_first_instance(self, monkeypatch):
        # a bare component name is unambiguous for a READ: every instance shows the same structure.
        occs = [_Occ("Bracket:1", "Bracket"), _Occ("Bracket:2", "Bracket")]
        root = _wire_tree(monkeypatch, occs)
        found, err = dg._find_occurrence_by_name(root, "Bracket")
        assert err is None
        assert found is not None and found.component.name == "Bracket"

    def test_exact_occurrence_name_resolves(self, monkeypatch):
        occs = [_Occ("Gear:1", "Gear"), _Occ("Gear:2", "Gear")]
        root = _wire_tree(monkeypatch, occs)
        found, err = dg._find_occurrence_by_name(root, "Gear:2")
        assert err is None and found is not None and found.name == "Gear:2"

    def test_ambiguous_occurrence_name_is_refused_not_first_matched(self, monkeypatch):
        # 'Bolt' substring-matches two DIFFERENT-component instances - must refuse (list candidates),
        # never silently root at the first.
        occs = [_Occ("M6-Bolt:1", "M6-Bolt"), _Occ("M8-Bolt:1", "M8-Bolt")]
        root = _wire_tree(monkeypatch, occs)
        found, err = dg._find_occurrence_by_name(root, "Bolt")
        assert found is None
        assert err and "ambiguous" in err.lower()

    def test_miss_returns_no_error(self, monkeypatch):
        occs = [_Occ("Gear:1", "Gear")]
        root = _wire_tree(monkeypatch, occs)
        found, err = dg._find_occurrence_by_name(root, "Nonexistent")
        assert found is None and err is None


class TestRootBodies:
    """The tree walks occurrences, so bodies directly in the ROOT component would be invisible
    without _root_body_names (a root body isn't a jointable occurrence — the agent must be told it
    exists). design_get(tree) must include root-level bodies."""

    def test_root_body_names_lists_direct_bodies(self):
        from types import SimpleNamespace

        def _coll(items):
            return SimpleNamespace(count=len(items), item=lambda i: items[i])

        body = SimpleNamespace(name="RootBlock")
        root = SimpleNamespace(bRepBodies=_coll([body]))
        assert dg._root_body_names(root) == ["RootBlock"]

    def test_no_root_bodies_returns_empty(self):
        from types import SimpleNamespace
        root = SimpleNamespace(bRepBodies=SimpleNamespace(count=0, item=lambda i: None))
        assert dg._root_body_names(root) == []


# ── the tree slice itself (_slice_tree / _walk_occurrence) — depth/cap bounds + node shape ──────────
#
# The router tests above stub _slice_tree; these pin the slice's OWN behavior: the depth clamp, the
# node cap with its truncated flag, per-node counts, reference metadata, and the component scope.

def _tocc(name, comp=None, kids=(), bodies=0, is_ref=False, docref=None):
    """A tree-walkable occurrence: named collections (count + iterable) for bodies/children."""
    return SimpleNamespace(
        name=name, fullPathName=name,
        component=SimpleNamespace(name=comp or name.split(":")[0]),
        isReferencedComponent=is_ref,
        bRepBodies=_NamedCollection([SimpleNamespace(name=f"B{i+1}") for i in range(bodies)]),
        childOccurrences=_NamedCollection(kids),
        documentReference=docref,
    )


def _tree_design(occs=(), root_bodies=0, root_name="RootComp"):
    bodies = [SimpleNamespace(name=f"RootBody{i+1}") for i in range(root_bodies)]
    root = SimpleNamespace(name=root_name, occurrences=_NamedCollection(occs),
                           allOccurrences=list(occs), bRepBodies=_NamedCollection(bodies))
    return SimpleNamespace(rootComponent=root)


class TestSliceTree:
    def test_unscoped_walk_lists_children_with_counts(self):
        kid = _tocc("Pin:1")
        design = _tree_design([_tocc("Bracket:1", kids=[kid], bodies=2), _tocc("Gear:1")])
        out, err = dg._slice_tree(design, 3, "")
        assert err is None
        assert out["root"] == "RootComp" and out["node_count"] == 3   # 2 top-level + 1 child
        assert [c["name"] for c in out["children"]] == ["Bracket:1", "Gear:1"]
        bracket = out["children"][0]
        assert bracket["body_count"] == 2 and bracket["child_count"] == 1
        assert [k["name"] for k in bracket["children"]] == ["Pin:1"]
        assert out["truncated"] is False and "root_bodies" not in out

    def test_depth_clamped_to_max(self):
        out, _ = dg._slice_tree(_tree_design([_tocc("Gear:1")]), 99, "")
        assert out["max_depth"] == dg._TREE_MAX_DEPTH

    def test_unparseable_depth_falls_back_to_default(self):
        out, _ = dg._slice_tree(_tree_design([_tocc("Gear:1")]), "junk", "")
        assert out["max_depth"] == dg._TREE_DEFAULT_DEPTH

    def test_max_depth_cuts_off_children_and_flags_it(self):
        # at the depth limit a node with children carries children_truncated instead of the children -
        # an agent must be able to tell "no children" from "not walked".
        design = _tree_design([_tocc("Bracket:1", kids=[_tocc("Pin:1")])])
        out, _ = dg._slice_tree(design, 1, "")
        node = out["children"][0]
        assert node["children_truncated"] is True and "children" not in node

    def test_node_cap_truncates_the_walk(self, monkeypatch):
        monkeypatch.setattr(dg, "_TREE_MAX_NODES", 2)
        design = _tree_design([_tocc(f"P{i}:1") for i in range(3)])
        out, _ = dg._slice_tree(design, 3, "")
        assert out["truncated"] is True and len(out["children"]) == 2

    def test_root_bodies_surface_with_promote_note(self):
        # bodies directly in root are invisible to the occurrence walk - the slice must list them and
        # say how to make one jointable.
        design = _tree_design([_tocc("Gear:1")], root_bodies=1)
        out, _ = dg._slice_tree(design, 3, "")
        assert out["root_bodies"] == ["RootBody1"]
        assert "model_create_component" in out["root_bodies_note"]

    def test_component_scope_roots_the_tree_there(self):
        kid = _tocc("Pin:1")
        design = _tree_design([_tocc("Bracket:1", comp="Bracket", kids=[kid]),
                               _tocc("Gear:1", comp="Gear")])
        out, err = dg._slice_tree(design, 3, "Bracket")
        assert err is None
        assert out["root"] == "Bracket" and out["tree"]["name"] == "Bracket:1"
        assert [k["name"] for k in out["tree"]["children"]] == ["Pin:1"]
        assert "children" not in out                       # scoped: one rooted tree, not the root list

    def test_component_scope_miss_errors_naming_it(self, monkeypatch):
        root = _wire_tree(monkeypatch, [_Occ("Gear:1", "Gear")])
        out, err = dg._slice_tree(SimpleNamespace(rootComponent=root), 3, "Ghost")
        assert out is None and "Ghost" in error_message(err)

    def test_no_root_component_errors(self):
        out, err = dg._slice_tree(SimpleNamespace(rootComponent=None), 3, "")
        assert out is None and "root" in error_message(err).lower()


class TestWalkOccurrenceReference:
    """An xref node carries its source identity (version/staleness/file) so an agent can see WHERE a
    referenced component comes from and whether it is out of date."""

    def test_reference_node_carries_source_metadata(self):
        df = SimpleNamespace(id="urn:adsk:123", name="LibPart", fusionWebURL="https://autodesk/x")
        dr = SimpleNamespace(version=7, isOutOfDate=True, dataFile=df)
        node = dg._walk_occurrence(_tocc("Lib:1", is_ref=True, docref=dr), 0, 3,
                                   {"n": 0, "truncated": False})
        assert node["is_reference"] is True
        assert node["source_version"] == 7 and node["is_out_of_date"] is True
        assert node["source_id"] == "urn:adsk:123" and node["source_name"] == "LibPart"
        assert node["source_url"] == "https://autodesk/x"

    def test_local_node_has_no_source_fields(self):
        node = dg._walk_occurrence(_tocc("Gear:1"), 0, 3, {"n": 0, "truncated": False})
        assert node["is_reference"] is False and "source_version" not in node


# ── the configurations slice (_slice_configurations) ───────────────────────────────────────────────

class TestSliceConfigurations:
    def _row(self, name, rid, idx):
        return SimpleNamespace(name=name, id=rid, index=idx)

    def _design(self, rows, active=None, cols=()):
        table = SimpleNamespace(name="Table1", id="t1", activeRow=active,
                                rows=list(rows), columns=list(cols))
        return SimpleNamespace(configurationTopTable=table)

    def test_not_configured_design_errors(self):
        out, err = dg._slice_configurations(SimpleNamespace(configurationTopTable=None))
        assert out is None and "configured design" in error_message(err).lower()

    def test_rows_columns_and_active_flag(self):
        r1, r2 = self._row("Variant A", "r1", 0), self._row("Variant B", "r2", 1)
        col = SimpleNamespace(title="Length", id="c1", index=0)
        out, err = dg._slice_configurations(self._design([r1, r2], active=r2, cols=[col]))
        assert err is None
        assert out["active_configuration"] == "Variant B"
        assert [r["is_active"] for r in out["configurations"]] == [False, True]
        assert out["configuration_count"] == 2 and "truncated" not in out
        assert out["columns"][0]["title"] == "Length"   # ConfigurationColumn exposes .title, not .name

    def test_row_cap_truncates(self, monkeypatch):
        monkeypatch.setattr(dg, "_CONFIG_MAX_ROWS", 1)
        out, _ = dg._slice_configurations(self._design([self._row("A", "r1", 0),
                                                        self._row("B", "r2", 1)]))
        assert out["truncated"] is True and out["configuration_count"] == 1


# ── router error propagation + _unwrap ─────────────────────────────────────────────────────────────

class TestRouterErrorPropagation:
    def test_tree_slice_error_fails_the_read(self, monkeypatch, stub_slices):
        monkeypatch.setattr(dg, "_slice_tree", lambda d, md, c: (None, dg.error("no tree here")))
        res = dg.handler(include=["tree"])
        assert res["isError"] and "no tree here" in error_message(res)

    def test_timeline_slice_error_fails_the_read(self, monkeypatch, stub_slices):
        monkeypatch.setattr(dg, "_slice_timeline",
                            lambda d, s, g: (None, dg.error("direct-modeling design")))
        res = dg.handler(include=["timeline"])
        assert res["isError"] and "direct-modeling" in error_message(res)

    def test_mode_error_fails_the_default(self, monkeypatch, stub_slices):
        monkeypatch.setattr(dg, "_slice_mode", lambda d: (None, dg.error("mode read failed")))
        res = dg.handler()
        assert res["isError"] and "mode read failed" in error_message(res)

    def test_in_base_feature_edit_surfaces_only_when_true(self, monkeypatch, stub_slices):
        monkeypatch.setattr(dg, "_slice_mode", lambda d: (
            {"design_type": "parametric", "timeline_feature_count": 1,
             "in_base_feature_edit": True}, None))
        out = _payload(dg.handler())
        assert out["in_base_feature_edit"] is True


class TestUnwrap:
    def test_ok_payload_decodes(self):
        assert dg._unwrap(dg.ok({"a": 1})) == ({"a": 1}, None)

    def test_error_passes_through(self):
        res = dg.error("boom")
        assert dg._unwrap(res) == (None, res)

    def test_undecodable_ok_returns_the_result_as_error(self):
        res = {"isError": False, "content": [{"text": "not json{"}]}
        assert dg._unwrap(res) == (None, res)
