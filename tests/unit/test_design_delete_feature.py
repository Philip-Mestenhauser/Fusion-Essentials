"""Unit tests for ``design_delete_feature.py`` — delete one timeline feature by name.

The logic pinned here, no live Fusion: name matching (exact first, then substring; ambiguity REFUSED
with candidates), the GROUP guard (a timeline group has no deletable entity), the no-entity guard,
the actual ``entity.deleteMe()`` call (captured so a wrong method name regresses here), the
deleteMe-returns-false path, the before/after timeline-health guard (a delete that breaks a
downstream feature is reported, the deletion still standing), and the occurrence-remove reroute (a
Remove feature's timeline entity is the removed OCCURRENCE, so the delete goes to the RemoveFeature
resolved by the same name).
"""

import json
import types

import adsk.fusion
import pytest

from conftest import MakeComp, load_tool, make_design

df = load_tool("design_delete_feature")


# ── fakes ────────────────────────────────────────────────────────────────────

def FakeEntity(type_name="ExtrudeFeature", delete_returns=True, breaks=None):
    """Build a fake feature entity whose CLASS NAME is `type_name`, so the handler's
    type(entity).__name__ reports the right entity_type (mirrors how the real API names features)."""
    def deleteMe(self):
        self._deleted = True
        if self._breaks is not None:
            self._breaks._items.append(FakeTLObject("BrokenChild", 99, health=2))
        return self._delete_returns

    cls = type(type_name, (), {"deleteMe": deleteMe})
    inst = cls()
    inst._delete_returns = delete_returns
    inst._deleted = False
    inst._breaks = breaks
    return inst


class FakeTLObject:
    def __init__(self, name, index, is_group=False, entity="auto", health=0,
                 entity_type="ExtrudeFeature", delete_returns=True):
        self.name = name
        self.index = index
        self.isGroup = is_group
        self.healthState = health
        if is_group:
            self.entity = None
        elif entity == "auto":
            self.entity = FakeEntity(entity_type, delete_returns)
        else:
            self.entity = entity


class FakeTimeline:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeDesign:
    def __init__(self, timeline):
        self.timeline = timeline


def _install(items, has_timeline=True):
    tl = FakeTimeline(items) if has_timeline else None
    design = FakeDesign(tl)
    df._common.design = lambda: design
    return design, tl


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _obj(tl, name):
    return next(o for o in tl._items if o.name == name)


# ── helpers ──────────────────────────────────────────────────────────────────

class TestHealthHelper:
    def test_rolls_up_errors_and_warnings(self):
        tl = FakeTimeline([FakeTLObject("A", 0, health=0), FakeTLObject("B", 1, health=2),
                           FakeTLObject("C", 2, health=1)])
        errors, warnings, total = df._timeline_health(type("D", (), {"timeline": tl})())
        assert total == 3 and errors == ["B"] and warnings == ["C"]

    def test_none_timeline_empty(self):
        assert df._timeline_health(type("D", (), {"timeline": None})()) == ([], [], 0)


class TestFindByName:
    def test_exact_match_preferred_over_substring(self):
        tl = FakeTimeline([FakeTLObject("Fillet1", 0), FakeTLObject("Fillet10", 1)])
        hits = df._find_objects_by_name(tl, "Fillet1")
        assert [o.name for o in hits] == ["Fillet1"]      # exact only, not Fillet10


class TestAtIndexForm:
    """'name@index' - the disambiguation target the ambiguity error advertises (e.g. 'Extrude1@9') -
    parses and resolves to the object at that exact timeline index (index == list position, live)."""

    def test_at_index_targets_that_timeline_index(self):
        tl = FakeTimeline([FakeTLObject("Extrude1", 0), FakeTLObject("Sketch1", 1),
                           FakeTLObject("Extrude1", 2)])
        hits = df._find_objects_by_name(tl, "Extrude1@2")
        assert len(hits) == 1 and hits[0].index == 2      # the SECOND Extrude1, not the first

    def test_at_index_out_of_range_refused(self):
        tl = FakeTimeline([FakeTLObject("Extrude1", 0)])
        assert df._find_objects_by_name(tl, "Extrude1@5") == []

    def test_at_index_name_mismatch_refused(self):
        # index 0 is Sketch1, not Extrude1 - a stale pairing is refused, never widened to a name match
        tl = FakeTimeline([FakeTLObject("Sketch1", 0), FakeTLObject("Extrude1", 1)])
        assert df._find_objects_by_name(tl, "Extrude1@0") == []

    def test_handler_deletes_the_indexed_duplicate(self):
        # end-to-end: two features share a name; the @index form deletes exactly the RIGHT one
        a = FakeTLObject("Extrude1", 0, entity_type="ExtrudeFeature")
        s = FakeTLObject("Sketch1", 1)
        b = FakeTLObject("Extrude1", 2, entity_type="ExtrudeFeature")
        _install([a, s, b])
        out = _payload(df.handler(feature="Extrude1@2"))
        assert out["deleted"] is True and out["index"] == 2
        assert b.entity._deleted is True and a.entity._deleted is False


# ── happy path ───────────────────────────────────────────────────────────────

class TestDelete:
    def test_deletes_named_feature(self):
        _, tl = _install([FakeTLObject("Rectangular Pattern1", 5, entity_type="RectangularPatternFeature")])
        out = _payload(df.handler(feature="Rectangular Pattern1"))
        assert out["deleted"] is True
        assert out["feature"] == "Rectangular Pattern1"
        assert out["index"] == 5
        assert out["entity_type"] == "RectangularPatternFeature"
        assert _obj(tl, "Rectangular Pattern1").entity._deleted is True   # deleteMe actually called

    def test_substring_match(self):
        _, tl = _install([FakeTLObject("Mirror1", 3, entity_type="MirrorFeature")])
        out = _payload(df.handler(feature="mirror"))
        assert out["feature"] == "Mirror1"
        assert tl._items[0].entity._deleted is True


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_empty_feature_errors(self):
        _install([FakeTLObject("X", 0)])
        res = df.handler(feature="")
        assert res["isError"] is True and "provide 'feature'" in res["message"].lower()

    def test_no_active_design_errors(self):
        df._common.design = lambda: None
        res = df.handler(feature="X")
        assert res["isError"] is True and "no active design" in res["message"].lower()

    def test_direct_design_no_timeline_errors(self):
        _install([], has_timeline=False)
        res = df.handler(feature="X")
        assert res["isError"] is True and "no timeline" in res["message"].lower()

    def test_missing_feature_errors(self):
        _install([FakeTLObject("Extrude1", 0)])
        res = df.handler(feature="Ghost")
        assert res["isError"] is True and "no timeline feature matching" in res["message"].lower()

    def test_ambiguous_name_refused(self):
        # two timeline objects share the substring — refuse, listing candidates with indices
        _install([FakeTLObject("Joint1", 4), FakeTLObject("Joint2", 7)])
        res = df.handler(feature="Joint")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Joint1@4" in res["message"] and "Joint2@7" in res["message"]

    def test_group_refused(self):
        _, tl = _install([FakeTLObject("Group1", 2, is_group=True)])
        res = df.handler(feature="Group1")
        assert res["isError"] is True and "group" in res["message"].lower()

    def test_delete_me_false_reported(self):
        _, tl = _install([FakeTLObject("Stubborn1", 1, delete_returns=False)])
        res = df.handler(feature="Stubborn1")
        assert res["isError"] is True and "declined" in res["message"].lower()

    def test_no_entity_guard(self):
        # a non-group object with no associated entity is refused (nothing to delete)
        obj = FakeTLObject("Weird1", 3, is_group=False, entity=None)
        _install([obj])
        res = df.handler(feature="Weird1")
        assert res["isError"] is True and "no associated entity" in res["message"].lower()

    def test_preexisting_warnings_surface_without_new_error(self):
        # deleting succeeds; the timeline already carries a WARNING (health 1) -> reported under
        # timeline_warnings (the elif branch), distinct from a NEW error.
        warn = FakeTLObject("WarnFeature", 1, health=1)
        target = FakeTLObject("Extrude1", 0, health=0)
        _, tl = _install([target, warn])
        out = _payload(df.handler(feature="Extrude1"))
        assert out["deleted"] is True
        assert "timeline_warning" not in out          # no NEW error
        assert out["timeline_warnings"] == ["WarnFeature"]

    def test_a_body_remove_deletes_its_timeline_entity_directly(self, monkeypatch):
        # a body-remove timeline object's entity IS the RemoveFeature: no re-resolution, and the
        # entity the timeline handed over is what gets deleted.
        monkeypatch.setattr(adsk.fusion, "Occurrence", types.SimpleNamespace)
        ent = FakeEntity("RemoveFeature")
        design = FakeDesign(FakeTimeline([FakeTLObject("RemoveBody-Body1", 2, entity=ent)]))
        monkeypatch.setattr(df._common, "design", lambda: design)
        out = _payload(df.handler(feature="RemoveBody-Body1"))
        assert out["deleted"] is True and out["entity_type"] == "RemoveFeature"
        assert ent._deleted is True

    def test_downstream_error_after_delete_reported(self):
        # deleting a feature whose geometry a later feature consumed leaves a new error: the delete
        # stands, but it's surfaced.
        breaks_into = FakeTimeline([])      # placeholder; replaced below
        ent = FakeEntity("ExtrudeFeature", delete_returns=True)
        obj = FakeTLObject("Extrude1", 0, entity=ent, health=0)
        _, tl = _install([obj])
        ent._breaks = tl                    # deleting injects a downstream error into THIS timeline
        out = _payload(df.handler(feature="Extrude1"))
        assert out["deleted"] is True
        assert "timeline_warning" in out
        assert "BrokenChild" in out["timeline_warning"]


# ── the occurrence-remove reroute ────────────────────────────────────────────
# An Occurrence .entity does not say which kind of timeline object reported it: an occurrence-remove
# object reports the REMOVED occurrence (deleteMe raises InternalValidationError), an occurrence
# CREATE reports the LIVE instance (deleteMe succeeds). The name lookup in removeFeatures is the
# discriminator. The RemoveFeature class name is load-bearing - the payload's entity_type reports it.

def _removed_occurrence(path="Scrap:1"):
    """The entity an occurrence-REMOVE timeline object reports. deleteMe() raises the way Fusion's
    does on a removed occurrence, so a handler that deletes the entity directly cannot pass. Built
    as a SimpleNamespace - the type the fixture points adsk.fusion.Occurrence at."""
    def deleteMe():
        raise RuntimeError("2 : InternalValidationError : Xl::Utils::findObjectPath(this, objPath)")

    return types.SimpleNamespace(name=path, fullPathName=path, deleteMe=deleteMe)


def _live_occurrence(path="InsProbe:1"):
    """The entity an occurrence-CREATE timeline object reports: the LIVE instance, whose deleteMe()
    succeeds and removes it. Same type as the removed one, so only the name lookup can tell them
    apart."""
    ns = types.SimpleNamespace(name=path, fullPathName=path, deleted=False)

    def deleteMe():
        ns.deleted = True
        return True

    ns.deleteMe = deleteMe
    return ns


_TL_INDEX = 3      # the timeline index _design() places its object at


class RemoveFeature:
    """timelineObject.index is the feature's own place in the timeline - the reroute accepts the
    feature only when that index is the one the caller resolved, so a same-named feature elsewhere
    in the timeline cannot stand in for the object named."""

    def __init__(self, name, delete_returns=True, restores_to=None, restored_path="Scrap:1",
                 timeline_index=_TL_INDEX):
        self.name = name
        self.deleted = False
        self.timelineObject = types.SimpleNamespace(index=timeline_index)
        self._delete_returns = delete_returns
        self._restores_to = restores_to      # the allOccurrences list the occurrence comes back into
        self._restored_path = restored_path

    def deleteMe(self):
        self.deleted = True
        if self._delete_returns and self._restores_to is not None:
            self._restores_to.append(types.SimpleNamespace(fullPathName=self._restored_path))
        return self._delete_returns


def _removes(*feats):
    """A component's features.removeFeatures - itemByName is the lookup the reroute resolves on."""
    return types.SimpleNamespace(
        removeFeatures=types.SimpleNamespace(
            itemByName=lambda n: next((f for f in feats if f.name == n), None)))


class TestOccurrenceRemoveReroute:
    @pytest.fixture(autouse=True)
    def occurrence_type(self, monkeypatch):
        """The handler discriminates the removed occurrence by isinstance against
        adsk.fusion.Occurrence, a bare Mock in this harness (isinstance against which raises, so the
        branch would silently never fire). Point it at the type _removed_occurrence builds."""
        monkeypatch.setattr(adsk.fusion, "Occurrence", types.SimpleNamespace)

    def _design(self, monkeypatch, name, comps, entity=None):
        design = make_design(comp=comps[0], all_components=comps)
        entity = _removed_occurrence() if entity is None else entity
        design.timeline = FakeTimeline([FakeTLObject(name, _TL_INDEX, entity=entity)])
        monkeypatch.setattr(df._common, "design", lambda: design)
        return design

    def test_the_delete_is_routed_to_the_remove_feature(self, monkeypatch):
        feat = RemoveFeature("RemoveInstance-Scrap:1")
        comp = MakeComp("Root")
        comp.features = _removes(feat)
        self._design(monkeypatch, "RemoveInstance-Scrap:1", [comp])
        out = _payload(df.handler(feature="RemoveInstance-Scrap:1"))
        assert out["deleted"] is True
        assert out["entity_type"] == "RemoveFeature"   # what was deleted, not what .entity handed over
        assert feat.deleted is True

    def test_the_note_says_the_occurrence_came_back(self, monkeypatch):
        # deleting a Remove feature RESTORES what it removed - the generic note says the opposite
        # ("instances it created go with it") and would contradict design_remove_feature's promise.
        feat = RemoveFeature("RemoveInstance-Scrap:1")
        comp = MakeComp("Root")
        comp.features = _removes(feat)
        self._design(monkeypatch, "RemoveInstance-Scrap:1", [comp])
        out = _payload(df.handler(feature="RemoveInstance-Scrap:1"))
        assert "back in the assembly" in out["note"]
        assert "go with it" not in out["note"]

    def test_the_restored_occurrence_is_read_back(self, monkeypatch):
        comp = MakeComp("Root")
        feat = RemoveFeature("RemoveInstance-Scrap:1", restores_to=comp.allOccurrences,
                             restored_path="Scrap:1")
        comp.features = _removes(feat)
        self._design(monkeypatch, "RemoveInstance-Scrap:1", [comp])
        out = _payload(df.handler(feature="RemoveInstance-Scrap:1"))
        assert out["occurrence_restored"] == "Scrap:1"

    def test_an_unconfirmed_restore_is_omitted_not_denied(self, monkeypatch):
        # the walk does not show the occurrence back: the delete still stands, and the key is simply
        # absent - "not confirmed" is never published as "it did not come back".
        feat = RemoveFeature("RemoveInstance-Scrap:1")      # restores_to=None: nothing comes back
        comp = MakeComp("Root")
        comp.features = _removes(feat)
        self._design(monkeypatch, "RemoveInstance-Scrap:1", [comp])
        out = _payload(df.handler(feature="RemoveInstance-Scrap:1"))
        assert out["deleted"] is True
        assert "occurrence_restored" not in out

    def test_the_feature_is_found_in_a_sub_component(self, monkeypatch):
        # the RemoveFeature lives in the component that owns the instance, not necessarily the root.
        root, sub = MakeComp("Root"), MakeComp("Sub")
        feat = RemoveFeature("RemoveInstance-Bolt:1")
        root.features = _removes()
        sub.features = _removes(feat)
        self._design(monkeypatch, "RemoveInstance-Bolt:1", [root, sub])
        out = _payload(df.handler(feature="RemoveInstance-Bolt:1"))
        assert out["deleted"] is True and feat.deleted is True

    def test_a_live_occurrence_with_no_remove_feature_is_deleted_not_refused(self, monkeypatch):
        # an occurrence-CREATE timeline object also reports an Occurrence entity, and deleting it
        # through the timeline works - so no RemoveFeature by that name means delete the entity as
        # handed over, never refuse. The create object's name carries a leading space on this build.
        occ = _live_occurrence(" InsProbe:1")
        comp = MakeComp("Root")
        comp.features = _removes()
        self._design(monkeypatch, " InsProbe:1", [comp], entity=occ)
        out = _payload(df.handler(feature=" InsProbe:1"))
        assert out["deleted"] is True
        assert occ.deleted is True
        assert "back in the assembly" not in out["note"]   # not a Remove feature - the generic note

    def test_a_removed_occurrence_with_no_feature_reports_the_platform_error(self, monkeypatch):
        # nothing resolves by the name, so the entity is deleted as handed over and Fusion's own
        # refusal is what the agent sees - never a diagnosis this tool never checked.
        comp = MakeComp("Root")
        comp.features = _removes()
        self._design(monkeypatch, "RemoveInstance-Ghost:1", [comp])
        res = df.handler(feature="RemoveInstance-Ghost:1")
        assert res["isError"] is True
        assert "InternalValidationError" in res["message"]
        assert "is a Remove feature" not in res["message"]

    def test_the_name_at_index_form_deletes_the_object_it_names(self, monkeypatch):
        # two timeline objects share the name "Bolt:1": an occurrence CREATE at index 1 and a
        # renamed RemoveFeature at index 0. 'Bolt:1@1' names the CREATE, and the name lookup finds
        # the RemoveFeature - so only the index identity keeps the delete on the object named.
        occ = _live_occurrence("Bolt:1")
        feat = RemoveFeature("Bolt:1", timeline_index=0)
        comp = MakeComp("Root")
        comp.features = _removes(feat)
        design = make_design(comp=comp, all_components=[comp])
        design.timeline = FakeTimeline([
            FakeTLObject("Bolt:1", 0, entity=_removed_occurrence("Bolt:1")),
            FakeTLObject("Bolt:1", 1, entity=occ),
        ])
        monkeypatch.setattr(df._common, "design", lambda: design)
        out = _payload(df.handler(feature="Bolt:1@1"))
        assert out["deleted"] is True
        assert occ.deleted is True                      # the object the caller named
        assert feat.deleted is False                    # the same-named RemoveFeature is untouched
        assert "back in the assembly" not in out["note"]

    def test_the_same_name_in_two_components_is_refused_not_guessed(self, monkeypatch):
        a, b = MakeComp("CompA"), MakeComp("CompB")
        fa, fb = RemoveFeature("RemoveInstance-Bolt:1"), RemoveFeature("RemoveInstance-Bolt:1")
        a.features, b.features = _removes(fa), _removes(fb)
        self._design(monkeypatch, "RemoveInstance-Bolt:1", [a, b])
        res = df.handler(feature="RemoveInstance-Bolt:1")
        assert res["isError"] is True
        assert "CompA" in res["message"] and "CompB" in res["message"]
        assert fa.deleted is False and fb.deleted is False

    def test_a_declining_remove_feature_is_reported_not_claimed(self, monkeypatch):
        feat = RemoveFeature("RemoveInstance-Scrap:1", delete_returns=False)
        comp = MakeComp("Root")
        comp.features = _removes(feat)
        self._design(monkeypatch, "RemoveInstance-Scrap:1", [comp])
        res = df.handler(feature="RemoveInstance-Scrap:1")
        assert res["isError"] is True and "declined" in res["message"].lower()
