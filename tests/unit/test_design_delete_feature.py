"""Unit tests for ``design_delete_feature.py`` — delete one timeline feature by name.

The logic pinned here, no live Fusion: name matching through the shared timeline resolver (EXACT,
case- and surrounding-whitespace-insensitive, never a substring; a repeated name REFUSED with the
'name@index' candidates), the GROUP guard (a timeline group has no deletable entity), the no-entity guard,
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


def _detaches(tl, obj):
    """Wrap a timeline object's entity so a successful deleteMe takes that OBJECT out of the
    timeline - what the live API does, and the absence the handler re-reads the name census for."""
    ent = getattr(obj, "entity", None)
    inner = getattr(ent, "deleteMe", None)
    if inner is None:
        return

    def wrapped():
        did = inner()
        if did and obj in tl._items:
            tl._items.remove(obj)
        return did

    ent.deleteMe = wrapped


class FakeTimeline:
    def __init__(self, items):
        self._items = list(items)
        self.blind = False        # when set, .count raises - an unreadable collection
        for obj in self._items:
            _detaches(self, obj)

    @property
    def count(self):
        if self.blind:
            raise RuntimeError("timeline unavailable")
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
    def test_a_longer_name_sharing_the_prefix_is_not_a_match(self):
        tl = FakeTimeline([FakeTLObject("Fillet1", 0), FakeTLObject("Fillet10", 1)])
        obj, err = df._find_object(tl, "Fillet1")
        assert err is None and obj.name == "Fillet1"      # exact only, not Fillet10

    def test_a_substring_resolves_nothing_on_a_destructive_tool(self):
        # 'Fillet' names no timeline object. Deleting Fillet12 because it CONTAINS the typed text
        # is a silent wrong-target delete - the shared resolver refuses and lists what is there.
        tl = FakeTimeline([FakeTLObject("Fillet12", 0)])
        obj, err = df._find_object(tl, "Fillet")
        assert obj is None
        assert "no timeline feature named 'Fillet'" in err and "Fillet12" in err

    def test_surrounding_whitespace_is_not_a_distinguishing_feature(self):
        # Fusion names an occurrence-create timeline object with a LEADING SPACE (measured); no
        # caller retypes that, and no listing shows it.
        tl = FakeTimeline([FakeTLObject(" InsProbe:1", 0)])
        obj, err = df._find_object(tl, "InsProbe:1")
        assert err is None and obj.index == 0


class TestAtIndexForm:
    """'name@index' - the disambiguation target the ambiguity error advertises (e.g. 'Extrude1@9') -
    resolves to the object whose OWN .index is that number, confirmed by name."""

    def test_at_index_targets_that_timeline_index(self):
        tl = FakeTimeline([FakeTLObject("Extrude1", 0), FakeTLObject("Sketch1", 1),
                           FakeTLObject("Extrude1", 2)])
        obj, err = df._find_object(tl, "Extrude1@2")
        assert err is None and obj.index == 2             # the SECOND Extrude1, not the first

    def test_at_index_out_of_range_refused(self):
        tl = FakeTimeline([FakeTLObject("Extrude1", 0)])
        obj, err = df._find_object(tl, "Extrude1@5")
        assert obj is None and "Extrude1@5" in err

    def test_at_index_name_mismatch_refused(self):
        # index 0 is Sketch1, not Extrude1 - a stale pairing is refused, never widened to a name match
        tl = FakeTimeline([FakeTLObject("Sketch1", 0), FakeTLObject("Extrude1", 1)])
        obj, err = df._find_object(tl, "Extrude1@0")
        assert obj is None and "Extrude1@0" in err

    def test_at_index_reads_the_objects_own_index_not_its_position(self):
        # A timeline whose .index does NOT equal list position - what design_get publishes and what
        # the ambiguity error prints is o.index, so '@4' must mean the object carrying index 4.
        tl = FakeTimeline([FakeTLObject("Joint1", 4), FakeTLObject("Joint1", 7)])
        obj, err = df._find_object(tl, "Joint1@7")
        assert err is None and obj.index == 7
        # position 1, but no object holds .index 1
        assert df._find_object(tl, "Joint1@1")[0] is None

    def test_the_candidates_the_refusal_prints_resolve_back(self):
        # the ambiguity error advertises 'name@index' pairs; every one it prints must be a string
        # this same tool can resolve, or the refusal names a target the user cannot act on
        _install([FakeTLObject("Joint1", 4), FakeTLObject("Joint1", 7)])
        msg = df.handler(feature="Joint1")["message"]
        for cand in ("Joint1@4", "Joint1@7"):
            assert cand in msg
            _, tl = _install([FakeTLObject("Joint1", 4), FakeTLObject("Joint1", 7)])
            obj, err = df._find_object(tl, cand)
            assert err is None and f"Joint1@{obj.index}" == cand

    def test_resolves_through_the_shared_matcher_not_a_local_copy(self):
        # design_delete_feature, design_edit_timeline and _inputs.FeatureRef answer the SAME wire
        # forms; a local re-roll is how one tool targets a different feature than the others.
        inputs = load_tool("_inputs")
        objs = [FakeTLObject("Extrude1", 4), FakeTLObject("Extrude1", 9)]
        tl = FakeTimeline(objs)
        for want in ("Extrude1@4", "Extrude1@9", "Extrude1@0", "Extrude1@1", "Extrude1", "Extru"):
            theirs = inputs._match_timeline_objects(objs, want)
            obj, err = df._find_object(tl, want)
            if len(theirs) == 1:
                assert err is None and obj is theirs[0], want
            else:
                assert obj is None, want                  # 0 or >1 hits is always a refusal

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
        obj = FakeTLObject("Rectangular Pattern1", 5, entity_type="RectangularPatternFeature")
        _, tl = _install([obj])
        out = _payload(df.handler(feature="Rectangular Pattern1"))
        assert out["deleted"] is True
        assert out["feature"] == "Rectangular Pattern1"
        assert out["index"] == 5
        assert out["entity_type"] == "RectangularPatternFeature"
        assert obj.entity._deleted is True                # deleteMe actually called
        assert tl._items == []                            # and the object left the timeline

    def test_a_name_matches_case_insensitively(self):
        obj = FakeTLObject("Mirror1", 3, entity_type="MirrorFeature")
        _install([obj])
        out = _payload(df.handler(feature="mirror1"))
        assert out["feature"] == "Mirror1"
        assert obj.entity._deleted is True

    def test_a_substring_deletes_nothing(self):
        # the corrected contract: 'mirror' is not 'Mirror1'. A destructive tool never widens a name
        # it was given - the refusal lists what IS there and 'name@index' targets one of them.
        _, tl = _install([FakeTLObject("Mirror1", 3, entity_type="MirrorFeature")])
        res = df.handler(feature="mirror")
        assert res["isError"] is True and "Mirror1" in res["message"]
        assert tl._items[0].entity._deleted is False


# ── absence is proved, never assumed ─────────────────────────────────────────

class TestAbsenceReRead:
    """deleteMe()'s bool is the platform's claim; the name census re-read is the proof. A survivor
    is an error, and a census that could not read is disclosed, never counted as absence."""

    def test_a_survivor_is_an_error_not_a_false_ok(self):
        # deleteMe reports success and the object is still in the timeline: the payload may not
        # publish deleted:true off the bool alone.
        obj = FakeTLObject("Extrude1", 0)
        _install([obj])
        obj.entity.deleteMe = lambda: True          # reports success, removes nothing
        res = df.handler(feature="Extrude1")
        assert res["isError"] is True
        assert "NOT removed" in res["message"]
        assert "Extrude1" in res["message"]

    def test_one_of_two_same_named_leaving_is_proof_enough(self):
        # the census counts the NAME, so the boundary is after < before, not after == 0: deleting
        # 'Extrude1@2' leaves the other Extrude1 standing and that is still a proven removal.
        _, tl = _install([FakeTLObject("Extrude1", 0), FakeTLObject("Extrude1", 2)])
        out = _payload(df.handler(feature="Extrude1@2"))
        assert out["deleted"] is True
        assert [o.index for o in tl._items] == [0]

    def test_a_census_that_stops_reading_is_unverified_not_absence(self):
        # the timeline will not report a size after the delete. The object IS gone, but the check
        # cannot show it - so the flag is null and the note says why, never deleted:true.
        obj = FakeTLObject("Extrude1", 0)
        _, tl = _install([obj])
        inner = obj.entity.deleteMe

        def blinding():
            did = inner()
            tl.blind = True                          # .count now raises: an unreadable collection
            return did

        obj.entity.deleteMe = blinding
        out = _payload(df.handler(feature="Extrude1"))
        assert out["deleted"] is None
        assert "UNVERIFIED" in out["note"] and "could not be read back" in out["note"]
        # the note may not carry the claim the null flag denies
        assert "Timeline feature deleted" not in out["note"]


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
        assert res["isError"] is True and "no timeline feature named" in res["message"].lower()
        assert "Extrude1" in res["message"]                 # what IS there

    def test_a_repeated_name_is_refused_with_the_indexed_candidates(self):
        # two timeline objects carry the name - refuse, listing the 'name@index' form that picks one
        _install([FakeTLObject("Joint1", 4), FakeTLObject("Joint1", 7)])
        res = df.handler(feature="Joint1")
        assert res["isError"] is True
        assert "matches 2 timeline objects" in res["message"]
        assert "Joint1@4" in res["message"] and "Joint1@7" in res["message"]

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
        # a new timeline error after the delete is surfaced, naming the feature that carries it.
        ent = FakeEntity("ExtrudeFeature", delete_returns=True)
        obj = FakeTLObject("Extrude1", 0, entity=ent, health=0)
        _, tl = _install([obj])
        ent._breaks = tl                    # deleting injects a downstream error into THIS timeline
        out = _payload(df.handler(feature="Extrude1"))
        assert out["deleted"] is True
        assert "timeline_warning" in out
        assert "BrokenChild" in out["timeline_warning"]

    def test_the_downstream_warning_states_only_what_was_read(self):
        # Two claims this sentence may not make: WHY the error appeared (nothing here reads what the
        # broken feature consumed) and that "the deletion stands" - 'deleted' is the absence verdict
        # and it can be null on the very same call.
        ent = FakeEntity("ExtrudeFeature", delete_returns=True)
        obj = FakeTLObject("Extrude1", 0, entity=ent, health=0)
        _, tl = _install([obj])
        ent._breaks = tl
        warning = _payload(df.handler(feature="Extrude1"))["timeline_warning"]
        assert "the deletion stands" not in warning
        assert "consumed the removed geometry" not in warning
        assert "Nothing was rolled back" in warning
        assert "'deleted'" in warning                 # where the caller reads the verified fact


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
            # component: a real Occurrence always answers it; the shared census reads it to tell an
            # ordinary occurrence from one whose external reference will not resolve.
            self._restores_to.append(types.SimpleNamespace(
                fullPathName=self._restored_path,
                component=types.SimpleNamespace(name=self._restored_path.split(":")[0])))
        return self._delete_returns


def _remove_feature_detaches(tl, feat):
    """Deleting a RemoveFeature takes its OWN timeline object - the one carrying its index - out of
    the timeline. The reroute deletes the FEATURE, not the timeline object's .entity, so this is the
    absence path the entity wrapper never sees."""
    inner = feat.deleteMe

    def wrapped():
        did = inner()
        if did:
            for obj in list(tl._items):
                if obj.index == feat.timelineObject.index:
                    tl._items.remove(obj)
        return did

    feat.deleteMe = wrapped


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
        for c in comps:
            removes = getattr(getattr(c, "features", None), "removeFeatures", None)
            feat = removes.itemByName(name) if removes is not None else None
            if feat is not None:
                _remove_feature_detaches(design.timeline, feat)
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
