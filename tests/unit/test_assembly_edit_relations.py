"""Unit tests for assembly_edit_relations - the lifecycle of the three assembly relations.

Pinned: the kind/action matrix, name resolution (case-insensitive EXACT, a repeated name refused),
and every verification gate - a suppress re-read, a delete re-listed, setOccurrences bracketed by
the timeline roll and its member read-back, and setMotionData confirmed off valueOne/valueTwo.

RigidGroup / MotionLink / AssemblyConstraint have no shared conftest fake (no live SHAPES dump), so
the relation objects are local; the design/component/occurrence skeleton comes from the shared
make_design / MakeComp / install.
"""

import types

import pytest

import adsk.core
import adsk.fusion

from conftest import _NamedCollection, error_message, install, load_tool, make_design, MakeComp, payload

rel = load_tool("assembly_edit_relations")

JMT = adsk.fusion.JointMotionTypes
REVOLUTE_DOF = JMT.RevoluteJointRotateMotionType
SLIDER_DOF = JMT.SliderJointSlideMotionType


# ── the relation objects (the surface measured on the installed bindings) ───────────────────────

class _Relation:
    """What all three kinds share: name, entityToken, isSuppressed, deleteMe() -> bool. `home` is
    the collection a successful delete removes it from, so a re-list actually stops finding it."""

    def __init__(self, name, token=None, suppressed=False, delete_ok=True):
        self.name = name
        self.entityToken = token or f"TOK_{name}"
        self.isSuppressed = suppressed
        self.delete_ok = delete_ok
        self.home = None

    def deleteMe(self):
        if self.delete_ok and self.home is not None:
            self.home._items.remove(self)
        return self.delete_ok


class _StickySuppress(_Relation):
    """A relation whose isSuppressed assignment is SWALLOWED - the platform accepting a set that
    never takes, which the read-back gate exists to catch."""

    @property
    def isSuppressed(self):
        return self._suppressed

    @isSuppressed.setter
    def isSuppressed(self, value):
        self._suppressed = getattr(self, "_suppressed", False)


class _BlindSuppress(_Relation):
    """A relation whose isSuppressed accepts a write and RAISES on read - a flag that yields no
    verdict, which bool() would coerce into a confirmed False."""

    @property
    def isSuppressed(self):
        raise RuntimeError("3 : An API Object refers to a deleted Object")

    @isSuppressed.setter
    def isSuppressed(self, value):
        self._written = value


class _IntFlag(_Relation):
    """A relation whose isSuppressed reads back as an INT rather than a bool - a truthy reading that
    is not True."""

    @property
    def isSuppressed(self):
        return int(self._flag)

    @isSuppressed.setter
    def isSuppressed(self, value):
        self._flag = bool(value)


class _RigidGroup(_Relation):
    """setOccurrences RAISES, as measured on Fusion 2705.0.87: '3 : Cannot be edited before rolling
    back', with the members reading back unchanged after it. set_calls records any attempt so a test
    can prove the tool refuses WITHOUT reaching the platform."""

    def __init__(self, name, members=(), **kw):
        super().__init__(name, **kw)
        self.occurrences = _NamedCollection(list(members))
        self.timelineObject = _TimelineObject()
        self.set_calls = []

    def setOccurrences(self, collection, include_children):
        self.set_calls.append((list(collection), include_children,
                               self.timelineObject.rolled_before))
        raise RuntimeError("3 : Cannot be edited before rolling back")


class _MotionLink(_Relation):
    def __init__(self, name, reversed_=False, motion_one=REVOLUTE_DOF, motion_two=REVOLUTE_DOF,
                 value_one=1.0, value_two=1.0, set_ok=True, sticky_reverse=False, **kw):
        super().__init__(name, **kw)
        self.isReversed = reversed_
        self.motionOne = motion_one
        self.motionTwo = motion_two
        self.valueOne = types.SimpleNamespace(value=value_one)
        self.valueTwo = types.SimpleNamespace(value=value_two)
        self.jointOne = types.SimpleNamespace(name="CrankAxis")
        self.jointTwo = types.SimpleNamespace(name="Spin")
        self.set_ok = set_ok
        self.sticky_reverse = sticky_reverse
        self.motion_data = None

    def setMotionData(self, m1, v1, m2, v2, is_reversed):
        # ValueInput.createByReal is patched to echo ('real', number) - the fake stores what the
        # platform would: the two link parameters and the direction flag.
        self.motion_data = {"m1": m1, "v1": v1, "m2": m2, "v2": v2, "reversed": is_reversed}
        if not self.set_ok:
            return False
        self.valueOne = types.SimpleNamespace(value=v1[1])
        self.valueTwo = types.SimpleNamespace(value=v2[1])
        if not self.sticky_reverse:
            self.isReversed = is_reversed
        return True


class _BlindReverse(_MotionLink):
    """A motion link whose isReversed reads until it is WRITTEN and RAISES afterwards - the write
    lands where no read-back can confirm it."""

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self._blind = False

    @property
    def isReversed(self):
        if self._blind:
            raise RuntimeError("3 : An API Object refers to a deleted Object")
        return self._rev

    @isReversed.setter
    def isReversed(self, value):
        self._rev = value
        self._blind = True


class _Constraint(_Relation):
    def __init__(self, name, relationships=2, **kw):
        super().__init__(name, **kw)
        self.geometricRelationships = _NamedCollection([object()] * relationships)


class _TimelineObject:
    """A relation's timelineObject. rollTo records the marker move so a test can prove the refused
    action never disturbs the timeline."""

    def __init__(self):
        self.rolled_before = False

    def rollTo(self, roll_before):
        self.rolled_before = bool(roll_before)
        return True


class _Timeline:
    """The design timeline: the health walk reads count/item; moveToEnd counts marker restores so a
    test can prove none happened."""

    def __init__(self, items=()):
        self._items = list(items)
        self.moved_to_end = 0

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def moveToEnd(self):
        self.moved_to_end += 1
        return True


def _feature(name, health=0):
    return types.SimpleNamespace(name=name, healthState=health, errorOrWarningMessage="")


def _occ(path):
    return types.SimpleNamespace(name=path.split("+")[-1], fullPathName=path)


def _component(name, occurrences=(), rigid=(), links=(), constraints=()):
    comp = MakeComp(name=name, occurrences=list(occurrences))
    for coll_attr, members in (("rigidGroups", rigid), ("motionLinks", links),
                               ("assemblyConstraints", constraints)):
        coll = _NamedCollection(list(members))
        for m in members:
            m.home = coll
        setattr(comp, coll_attr, coll)
    return comp


def _world(monkeypatch, rigid=(), links=(), constraints=(), occurrences=(), subs=(),
           timeline=None):
    """A design whose ROOT carries the given relations, plus optional sub-components. install()
    wires both design seams (the tool's own and _inputs') - a hand-patched single seam would leave
    OccurrenceRefList resolving against no design."""
    root = _component("Root", occurrences=occurrences, rigid=rigid, links=links,
                      constraints=constraints)
    design = make_design(comp=root, all_components=[root] + list(subs))
    design.timeline = timeline if timeline is not None else _Timeline()
    install(rel, design)
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal", staticmethod(lambda v: ("real", v)))
    return design


@pytest.fixture
def world(monkeypatch):
    def _build(**kw):
        return _world(monkeypatch, **kw)
    return _build


# ── the walk + the resolver (tools/_relations.py, shared with assembly_get) ──────────────────────

class TestRelationWalk:
    def test_empty_design_lists_nothing(self, world):
        world()
        assert rel._relations.all_relations(rel._common.design(), "rigid_group") == []
        assert rel._relations.relation_names(rel._common.design(), "motion_link") == []

    def test_one_two_and_many_are_all_listed(self, world):
        for n in (1, 2, 5):
            design = world(rigid=[_RigidGroup(f"RG{i}") for i in range(n)])
            assert rel._relations.relation_names(design, "rigid_group") == [f"RG{i}" for i in range(n)]

    def test_subcomponent_relations_are_found(self, world):
        # a rigid group created inside a sub-assembly lives on THAT component - a root-only walk
        # would report it missing and the edit would refuse a relation that exists.
        sub = _component("Tower", rigid=[_RigidGroup("SubGroup")])
        design = world(rigid=[_RigidGroup("RootGroup")], subs=[sub])
        assert sorted(rel._relations.relation_names(design, "rigid_group")) == ["RootGroup", "SubGroup"]

    def test_root_reached_twice_is_counted_once(self, world):
        # design.allComponents includes the root, so the root's relations are walked twice; the
        # entityToken de-dup must not double-list them.
        design = world(rigid=[_RigidGroup("RG1")])
        assert rel._relations.relation_names(design, "rigid_group") == ["RG1"]

    def test_root_reached_through_a_distinct_proxy_is_counted_once(self, world):
        # live, allComponents returns the root as a proxy that is NOT identical to
        # design.rootComponent, so identity de-dup misses it and every root relation is listed
        # twice. The two proxies' relations share an entityToken, which is what de-dup keys on.
        world()
        design = make_design(comp=_component("Root", rigid=[_RigidGroup("RG1", token="T")]),
                             all_components=[_component("Root", rigid=[_RigidGroup("RG1", token="T")])])
        install(rel, design)
        assert rel._relations.relation_names(design, "rigid_group") == ["RG1"]

    def test_kinds_do_not_bleed_into_each_other(self, world):
        design = world(rigid=[_RigidGroup("RG1")], links=[_MotionLink("ML1")],
                       constraints=[_Constraint("AC1")])
        assert rel._relations.relation_names(design, "rigid_group") == ["RG1"]
        assert rel._relations.relation_names(design, "motion_link") == ["ML1"]
        assert rel._relations.relation_names(design, "constraint") == ["AC1"]

    def test_name_match_is_exact_not_a_prefix(self, world):
        # a substring/prefix resolver would hand back RigidGroup1 for 'Rigid' - the wrong-entity bug.
        design = world(rigid=[_RigidGroup("RigidGroup1")])
        obj, _comp, err = rel._relations.find_relation(design, "rigid_group", "Rigid")
        assert obj is None and "No rigid group named 'Rigid'" in err and "RigidGroup1" in err

    def test_name_match_is_case_insensitive(self, world):
        design = world(rigid=[_RigidGroup("RigidGroup1")])
        obj, _comp, err = rel._relations.find_relation(design, "rigid_group", "rigidgroup1")
        assert err is None and obj.name == "RigidGroup1"

    def test_rigid_group_members_are_bounded_and_report_the_true_total(self, world):
        rg = _RigidGroup("RG1", members=[_occ(f"P{i}:1") for i in range(20)])
        world(rigid=[rg])
        names, total = rel._relations.rigid_group_members(rg, 12)
        assert total == 20 and len(names) == 12 and names[0] == "P0:1"


class TestGuards:
    def test_unknown_kind_is_refused(self, world):
        world()
        assert "kind" in error_message(rel.handler(kind="bolt", name="X", action="delete"))

    def test_unknown_action_is_refused(self, world):
        world(rigid=[_RigidGroup("RG1")])
        assert "action" in error_message(rel.handler(kind="rigid_group", name="RG1", action="melt"))

    def test_action_not_supported_by_the_kind_names_what_is(self, world):
        # set_occurrences has no meaning for a motion link; the refusal must list the real actions
        # rather than fall through and act on the wrong property.
        world(links=[_MotionLink("ML1")])
        msg = error_message(rel.handler(kind="motion_link", name="ML1", action="set_occurrences"))
        assert "does not apply to a motion link" in msg and "set_values" in msg

    def test_constraint_refuses_the_motion_link_actions(self, world):
        world(constraints=[_Constraint("AC1")])
        msg = error_message(rel.handler(kind="constraint", name="AC1", action="reverse"))
        assert "assembly constraint" in msg and "delete" in msg

    def test_missing_name_is_refused(self, world):
        world(rigid=[_RigidGroup("RG1")])
        msg = error_message(rel.handler(kind="rigid_group", name="", action="delete"))
        assert "'name' is required" in msg

    def test_unknown_name_lists_what_exists(self, world):
        world(rigid=[_RigidGroup("RG1"), _RigidGroup("RG2")])
        msg = error_message(rel.handler(kind="rigid_group", name="Ghost", action="delete"))
        assert "Ghost" in msg and "RG1" in msg and "RG2" in msg

    def test_duplicate_name_across_components_is_refused_unmutated(self, world):
        # two sub-assemblies can each hold a 'RigidGroup1'; deleting the first hit would destroy the
        # wrong assembly's group.
        a, b = _RigidGroup("RigidGroup1", token="A"), _RigidGroup("RigidGroup1", token="B")
        sub = _component("Tower", rigid=[b])
        world(rigid=[a], subs=[sub])
        msg = error_message(rel.handler(kind="rigid_group", name="RigidGroup1", action="delete"))
        assert "names 2 rigid groups" in msg and "Root" in msg and "Tower" in msg
        assert a.home.count == 1 and b.home.count == 1        # neither was deleted

    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(rel._common, "design", lambda: None)
        monkeypatch.setattr(rel._inputs._common, "design", lambda: None)
        msg = error_message(rel.handler(kind="rigid_group", name="RG1", action="delete"))
        assert "design" in msg.lower()


class TestFlagContract:
    """_flag is the single reader behind all three read-back gates: it answers a real bool, or None
    when the flag gave no verdict. Both arms are load-bearing - None is what the gates refuse on,
    and the bool is what reaches the wire."""

    def test_a_flag_that_reads_none_is_no_verdict(self):
        # a member that READS None never raises, so a raise-only sentinel would call it a False.
        assert rel._flag(types.SimpleNamespace(isSuppressed=None), "isSuppressed") is None

    def test_a_flag_that_raises_is_no_verdict(self):
        assert rel._flag(_BlindSuppress("RG1"), "isSuppressed") is None

    def test_a_readable_flag_answers_a_real_bool(self):
        assert rel._flag(types.SimpleNamespace(isSuppressed=False), "isSuppressed") is False

    def test_a_truthy_non_bool_reading_lands_as_true(self, world):
        # a flag read back as 1 must publish true, not 1: the payload is the caller's boolean, and
        # an unnormalized reading also invites a spurious mismatch against the requested value.
        rg = _IntFlag("RG1", suppressed=True)
        world(rigid=[rg])
        out = payload(rel.handler(kind="rigid_group", name="RG1", action="suppress"))
        assert out["is_suppressed"] is True and out["was_suppressed"] is True


class TestSuppress:
    def test_suppress_sets_and_reports_both_states(self, world):
        rg = _RigidGroup("RG1")
        world(rigid=[rg])
        out = payload(rel.handler(kind="rigid_group", name="RG1", action="suppress"))
        assert rg.isSuppressed is True
        assert out["is_suppressed"] is True and out["was_suppressed"] is False

    def test_unsuppress_clears_it(self, world):
        ml = _MotionLink("ML1", suppressed=True)
        world(links=[ml])
        out = payload(rel.handler(kind="motion_link", name="ML1", action="unsuppress"))
        assert ml.isSuppressed is False
        assert out["is_suppressed"] is False and out["was_suppressed"] is True

    def test_a_swallowed_suppress_is_an_error_not_a_false_ok(self, world):
        world(constraints=[_StickySuppress("AC1")])
        msg = error_message(rel.handler(kind="constraint", name="AC1", action="suppress"))
        assert "did not take" in msg

    def test_a_suppress_that_breaks_a_feature_reports_it(self, world):
        # timeline health is diffed around the set: a downstream feature newly in error is named,
        # instead of an ok that hides the breakage.
        clean = _Timeline([_feature("Extrude1", 0)])
        rg = _RigidGroup("RG1")
        world(rigid=[rg], timeline=clean)

        def break_it(value):
            clean._items[0] = _feature("Extrude1", 2)
        rg.__class__.isSuppressed = property(lambda self: self._flag,
                                             lambda self, v: (setattr(self, "_flag", v), break_it(v))[0])
        rg._flag = False
        try:
            out = payload(rel.handler(kind="rigid_group", name="RG1", action="suppress"))
        finally:
            del rg.__class__.isSuppressed
        assert out["timeline_errors_after"] == ["Extrude1"]
        assert "Extrude1" in out["note"]


    def test_an_unreadable_prior_flag_reports_null_not_false(self, world):
        # False would claim the relation WAS unsuppressed; null says the prior state is unknown.
        rg = _RigidGroup("RG1")
        del rg.isSuppressed
        world(rigid=[rg])
        out = payload(rel.handler(kind="rigid_group", name="RG1", action="suppress"))
        assert out["was_suppressed"] is None and out["is_suppressed"] is True

    def test_an_unreadable_flag_after_unsuppress_is_not_a_confirmed_false(self, world):
        # unsuppress asks for False, and bool(unreadable) is False too - so a flag that cannot be
        # read passes a bool() gate and publishes "isSuppressed now reads False" as a measurement.
        world(rigid=[_BlindSuppress("RG1")])
        msg = error_message(rel.handler(kind="rigid_group", name="RG1", action="unsuppress"))
        assert "isSuppressed cannot be read" in msg and "UNCONFIRMED" in msg

    def test_an_unreadable_flag_after_suppress_is_refused_too(self, world):
        world(constraints=[_BlindSuppress("AC1")])
        msg = error_message(rel.handler(kind="constraint", name="AC1", action="suppress"))
        assert "isSuppressed cannot be read" in msg and "UNCONFIRMED" in msg

    def test_the_note_is_per_kind_and_claims_only_what_was_observed(self, world):
        # a motion link COUPLES MOTION - it holds no parts - and what suppression does to an
        # assembly constraint's relationships is unmeasured, so neither note may assert it.
        world(rigid=[_RigidGroup("RG1")], links=[_MotionLink("ML1")],
              constraints=[_Constraint("AC1")])
        rg_note = payload(rel.handler(kind="rigid_group", name="RG1", action="suppress"))["note"]
        ml_note = payload(rel.handler(kind="motion_link", name="ML1", action="suppress"))["note"]
        ac_note = payload(rel.handler(kind="constraint", name="AC1", action="suppress"))["note"]
        assert len({rg_note, ml_note, ac_note}) == 3
        assert "parts" not in ml_note and "couples" in ml_note
        assert "not measured" in ac_note
        assert all("isSuppressed now reads True" in n for n in (rg_note, ml_note, ac_note))


class TestDelete:
    def test_delete_removes_it_and_confirms_by_re_listing(self, world):
        rg = _RigidGroup("RG1")
        design = world(rigid=[rg, _RigidGroup("RG2")])
        out = payload(rel.handler(kind="rigid_group", name="RG1", action="delete"))
        assert out["deleted"] is True and out["remaining"] == 1
        assert rel._relations.relation_names(design, "rigid_group") == ["RG2"]

    def test_a_declined_delete_is_an_error(self, world):
        rg = _RigidGroup("RG1", delete_ok=False)
        world(rigid=[rg])
        msg = error_message(rel.handler(kind="rigid_group", name="RG1", action="delete"))
        assert "declined" in msg and "deleteMe returned false" in msg

    def test_a_survivor_after_a_true_delete_is_an_error(self, world):
        # deleteMe() returning true while the relation is still listed is the swallowed no-op the
        # re-list exists to catch.
        rg = _RigidGroup("RG1")
        rg.deleteMe = lambda: True                 # says yes, removes nothing
        world(rigid=[rg])
        msg = error_message(rel.handler(kind="rigid_group", name="RG1", action="delete"))
        assert "still listed" in msg

    def test_motion_link_and_constraint_delete_the_same_way(self, world):
        ml, ac = _MotionLink("ML1"), _Constraint("AC1")
        design = world(links=[ml], constraints=[ac])
        assert payload(rel.handler(kind="motion_link", name="ML1", action="delete"))["deleted"] is True
        assert payload(rel.handler(kind="constraint", name="AC1", action="delete"))["deleted"] is True
        assert rel._relations.relation_names(design, "motion_link") == []
        assert rel._relations.relation_names(design, "constraint") == []

    def test_delete_reports_a_feature_it_broke(self, world):
        tl = _Timeline([_feature("Joint1", 0)])
        rg = _RigidGroup("RG1")
        original = rg.deleteMe

        def delete_and_break():
            tl._items[0] = _feature("Joint1", 2)
            return original()
        rg.deleteMe = delete_and_break
        world(rigid=[rg], timeline=tl)
        out = payload(rel.handler(kind="rigid_group", name="RG1", action="delete"))
        assert out["timeline_errors_after"] == ["Joint1"]
        assert "Joint1" in out["note"]


    def test_the_note_points_at_the_kinds_own_create_tool(self, world):
        world(rigid=[_RigidGroup("RG1")], links=[_MotionLink("ML1")],
              constraints=[_Constraint("AC1")])
        for kind, name, tool_name in (("rigid_group", "RG1", "assembly_rigid_group"),
                                      ("motion_link", "ML1", "joint_motion_link"),
                                      ("constraint", "AC1", "assembly_constrain")):
            out = payload(rel.handler(kind=kind, name=name, action="delete"))
            assert tool_name in out["note"] and "no longer listed" in out["note"]
            assert "readable" in out["note"]        # 'remaining' counts readable names only


class TestSetOccurrencesIsRefused:
    """setOccurrences is unusable on this Fusion build, so the action answers with the measured
    failure and the path that works - and must reach NEITHER the platform call nor the timeline on
    its way there."""

    def _group(self):
        return _RigidGroup("RG1", members=[_occ("Frame:1"), _occ("Carrier:1")])

    def test_refused_without_touching_the_platform_or_the_timeline(self, world):
        rg = self._group()
        tl = _Timeline()
        world(rigid=[rg], occurrences=[_occ("Frame:1"), _occ("Carrier:1"), _occ("Pedestal:1")],
              timeline=tl)
        error_message(rel.handler(kind="rigid_group", name="RG1", action="set_occurrences",
                                  occurrences=["Frame:1", "Pedestal:1"]))
        assert rg.set_calls == []                        # the platform call is never attempted
        assert rg.timelineObject.rolled_before is False  # nothing rolls
        assert tl.moved_to_end == 0                      # and no marker restore is needed
        assert [o.fullPathName for o in rg.occurrences] == ["Frame:1", "Carrier:1"]

    def test_the_refusal_quotes_the_measured_failure_and_pins_it_to_the_build(self, world):
        # The refusal is a platform fact with a build attached: quoting a raise measured on an older
        # build as if it were this one's is the provenance defect. The message names the build it was
        # measured on and the sentence that build actually raises.
        world(rigid=[self._group()], occurrences=[_occ("Frame:1"), _occ("Carrier:1")])
        msg = error_message(rel.handler(kind="rigid_group", name="RG1", action="set_occurrences",
                                        occurrences=["Frame:1", "Carrier:1"]))
        assert "Cannot be edited before rolling back" in msg
        assert "2705.0.87" in msg
        assert "2704" not in msg
        assert "members read back unchanged" in msg

    def test_the_refusal_names_the_workaround(self, world):
        world(rigid=[self._group()], occurrences=[_occ("Frame:1"), _occ("Carrier:1")])
        msg = error_message(rel.handler(kind="rigid_group", name="RG1", action="set_occurrences",
                                        occurrences=["Frame:1", "Carrier:1"]))
        assert "action='delete'" in msg and "assembly_rigid_group" in msg
        assert "Nothing was changed" in msg

    def test_refused_before_the_group_or_its_occurrences_are_resolved(self, world):
        # neither an unknown group nor an unresolvable occurrence changes the answer - resolving
        # first would only put a second failure in front of the teaching.
        world(rigid=[self._group()], occurrences=[_occ("Frame:1")])
        for name, occs in (("Ghost", ["Frame:1", "Carrier:1"]), ("RG1", ["Ghost:1"]), ("RG1", None)):
            msg = error_message(rel.handler(kind="rigid_group", name=name,
                                            action="set_occurrences", occurrences=occs))
            assert "set_occurrences' is refused" in msg

    def test_the_action_is_still_offered_so_the_agent_is_taught(self, world):
        # keeping it in the kind's action set is what turns a dead end into a teaching moment: the
        # kind/action refusal for a motion link must still LIST it for a rigid group.
        world(links=[_MotionLink("ML1")])
        msg = error_message(rel.handler(kind="motion_link", name="ML1", action="set_occurrences"))
        assert "does not apply to a motion link" in msg
        assert "set_occurrences" in rel._KIND_ACTIONS["rigid_group"]


class TestReverse:
    def test_reverse_flips_and_reports_both_states(self, world):
        ml = _MotionLink("ML1", reversed_=False)
        world(links=[ml])
        out = payload(rel.handler(kind="motion_link", name="ML1", action="reverse"))
        assert ml.isReversed is True
        assert out["reversed"] is True and out["was_reversed"] is False

    def test_reverse_flips_back(self, world):
        ml = _MotionLink("ML1", reversed_=True)
        world(links=[ml])
        out = payload(rel.handler(kind="motion_link", name="ML1", action="reverse"))
        assert ml.isReversed is False and out["reversed"] is False

    def test_an_unreadable_direction_is_refused(self, world):
        ml = _MotionLink("ML1")
        del ml.isReversed
        world(links=[ml])
        assert "no direction to flip" in error_message(
            rel.handler(kind="motion_link", name="ML1", action="reverse"))

    def test_an_unreadable_direction_after_the_flip_is_not_a_confirmed_forward(self, world):
        # flipping a REVERSED link asks for False, and bool(unreadable) is False - so an unreadable
        # flag agrees with the request and the payload would claim the link now runs forward.
        ml = _BlindReverse("ML1", reversed_=True)
        world(links=[ml])
        msg = error_message(rel.handler(kind="motion_link", name="ML1", action="reverse"))
        assert "isReversed cannot be read" in msg and "UNCONFIRMED" in msg

    def test_a_swallowed_flip_is_an_error(self, world):
        ml = _MotionLink("ML1")
        _MotionLink.isReversed = property(lambda self: False, lambda self, v: None)
        world(links=[ml])
        try:
            msg = error_message(rel.handler(kind="motion_link", name="ML1", action="reverse"))
        finally:
            del _MotionLink.isReversed
        assert "did not take" in msg


class TestSetValues:
    def test_ratio_reaches_setmotiondata_with_the_links_own_dofs(self, world):
        # the coupled DOF must be re-passed off the link (motionOne/motionTwo) - guessing one here
        # would silently re-couple a different degree of freedom.
        ml = _MotionLink("ML1", motion_one=REVOLUTE_DOF, motion_two=SLIDER_DOF)
        world(links=[ml])
        out = payload(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=2.0))
        assert ml.motion_data["m1"] == REVOLUTE_DOF and ml.motion_data["m2"] == SLIDER_DOF
        assert ml.motion_data["v1"] == ("real", 1.0) and ml.motion_data["v2"] == ("real", 2.0)
        assert out["ratio"] == 2.0 and out["value_one"] == 1.0 and out["value_two"] == 2.0
        assert out["reversed"] is False

    def test_a_negative_ratio_reverses_with_the_magnitude(self, world):
        ml = _MotionLink("ML1")
        world(links=[ml])
        out = payload(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=-3))
        assert ml.motion_data["v2"] == ("real", 3.0) and ml.motion_data["reversed"] is True
        assert out["reversed"] is True

    def test_zero_and_non_numeric_ratios_are_refused(self, world):
        ml = _MotionLink("ML1")
        world(links=[ml])
        assert "non-zero" in error_message(
            rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=0))
        assert "must be a number" in error_message(
            rel.handler(kind="motion_link", name="ML1", action="set_values", ratio="banana"))
        assert ml.motion_data is None

    def test_unreadable_coupled_motions_are_refused_before_setting(self, world):
        ml = _MotionLink("ML1")
        del ml.motionTwo
        world(links=[ml])
        msg = error_message(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=2))
        assert "coupled motions" in msg and ml.motion_data is None

    def test_a_declined_set_is_an_error(self, world):
        ml = _MotionLink("ML1", set_ok=False)
        world(links=[ml])
        assert "setMotionData returned false" in error_message(
            rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=2))

    def test_a_ratio_that_did_not_take_is_an_error(self, world):
        # true from setMotionData while the parameters still read 1:1 - the read-back gate.
        ml = _MotionLink("ML1")
        ml.setMotionData = lambda m1, v1, m2, v2, r: True      # accepts, changes nothing
        world(links=[ml])
        msg = error_message(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=4))
        assert "did not take" in msg and "1.0:1.0" in msg

    def test_the_ratio_is_compared_not_the_raw_values(self, world):
        # a platform that stores the coupling scaled (2:8 for a requested 4) still expresses the
        # requested RATIO - comparing raw values would reject a correct link.
        ml = _MotionLink("ML1")

        def scaled(m1, v1, m2, v2, r):
            ml.valueOne = types.SimpleNamespace(value=2.0)
            ml.valueTwo = types.SimpleNamespace(value=8.0)
            ml.isReversed = r
            return True
        ml.setMotionData = scaled
        world(links=[ml])
        out = payload(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=4))
        assert out["value_one"] == 2.0 and out["value_two"] == 8.0

    def test_a_zero_first_value_is_an_error(self, world):
        ml = _MotionLink("ML1")

        def zeroed(m1, v1, m2, v2, r):
            ml.valueOne = types.SimpleNamespace(value=0.0)
            ml.valueTwo = types.SimpleNamespace(value=2.0)
            return True
        ml.setMotionData = zeroed
        world(links=[ml])
        assert "no coupling at all" in error_message(
            rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=2))

    def test_unreadable_values_after_the_set_are_an_error(self, world):
        ml = _MotionLink("ML1")

        def blind(m1, v1, m2, v2, r):
            del ml.valueTwo
            return True
        ml.setMotionData = blind
        world(links=[ml])
        assert "nothing confirms" in error_message(
            rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=2))

    def test_a_direction_that_did_not_take_is_an_error(self, world):
        ml = _MotionLink("ML1", sticky_reverse=True)
        world(links=[ml])
        msg = error_message(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=-2))
        assert "isReversed" in msg and "did not take" in msg

    def test_an_unreadable_direction_after_the_re_value_is_an_error(self, world):
        # a positive ratio asks for isReversed=False, which bool(unreadable) matches - the ratio
        # landed, but the direction the sign SETS is unconfirmed and must not be published as False.
        ml = _MotionLink("ML1")

        def blind_direction(m1, v1, m2, v2, r):
            ml.valueOne = types.SimpleNamespace(value=1.0)
            ml.valueTwo = types.SimpleNamespace(value=2.0)
            del ml.isReversed
            return True
        ml.setMotionData = blind_direction
        world(links=[ml])
        msg = error_message(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=2))
        assert "isReversed cannot be read back" in msg and "UNCONFIRMED" in msg

    def test_a_positive_ratio_clears_an_existing_reversal_and_says_so(self, world):
        # the SIGN of ratio SETS the direction, so re-valuing a deliberately reversed link at a
        # positive ratio un-reverses it - the payload must report what it overwrote.
        ml = _MotionLink("ML1", reversed_=True)
        world(links=[ml])
        out = payload(rel.handler(kind="motion_link", name="ML1", action="set_values", ratio=2))
        assert out["was_reversed"] is True and out["reversed"] is False
        assert ml.isReversed is False
        assert "CLEARS" in out["note"] and "action='reverse'" in out["note"]
