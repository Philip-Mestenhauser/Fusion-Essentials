"""Unit tests for ``design_delete_occurrence.py`` — delete one component occurrence.

The logic pinned here, no live Fusion: occurrence resolution via the shared OccurrenceRef path
(exact fullPathName, then name, ambiguity REFUSED — never the wrong instance), the actual
``Occurrence.deleteMe()`` call, the joints-removed warning (deleting an occurrence drops its joints),
the deleteMe-returns-false path (a pattern/mirror child Fusion won't delete on its own), and the
before/after timeline-health guard (a delete that introduces a new error is reported, the deletion
still standing). Fakes expose just the read/write surface the handler touches and CAPTURE the
deleteMe call so a regression to a wrong method name fails here.
"""

import json

from conftest import load_tool

dd = load_tool("design_delete_occurrence")


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeJointColl:
    def __init__(self, names):
        self._names = list(names)

    @property
    def count(self):
        return len(self._names)

    def item(self, i):
        return type("J", (), {"name": self._names[i]})()


class FakeOcc:
    def __init__(self, name, full_path=None, joints=(), grounded=False, delete_returns=True):
        self.name = name
        self.fullPathName = full_path or name
        self.joints = _FakeJointColl(joints)
        self.isGrounded = grounded
        self._delete_returns = delete_returns
        self._deleted = False
        # if set, deleting injects a downstream timeline error (models a feature that referenced it)
        self.on_delete_breaks_timeline = None

    def deleteMe(self):
        self._deleted = True
        if self.on_delete_breaks_timeline is not None:
            self.on_delete_breaks_timeline._items.append(FakeTimelineItem("BrokenFeature", health=2))
        return self._delete_returns


class FakeTimelineItem:
    def __init__(self, name, health=0):
        self.name = name
        self.healthState = health


class FakeTimeline:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeRoot:
    def __init__(self, occurrences):
        self.allOccurrences = list(occurrences)


class FakeDesign:
    def __init__(self, occurrences, timeline=None):
        self.rootComponent = FakeRoot(occurrences)
        self.timeline = timeline if timeline is not None else FakeTimeline([])


def _install(occs, timeline=None):
    """Point BOTH design seams (the tool's and the shared _inputs resolver's) at one fake design."""
    design = FakeDesign(occs, timeline)
    dd._common.design = lambda: design
    dd._inputs._common.design = lambda: design
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _occ(occs, name):
    return next(o for o in occs if o.name == name)


# ── helpers ──────────────────────────────────────────────────────────────────

class TestTimelineHealthHelper:
    def test_rolls_up_errors_and_warnings(self):
        design = FakeDesign([], FakeTimeline(
            [FakeTimelineItem("A", 0), FakeTimelineItem("B", 2),
             FakeTimelineItem("C", 1), FakeTimelineItem("D", 2)]))
        errors, warnings, total = dd._timeline_health(design)
        assert total == 4
        assert errors == ["B", "D"]
        assert warnings == ["C"]

    def test_no_timeline_is_empty(self):
        # a direct-modelling design has no timeline -> empty, not a crash
        design = FakeDesign([])
        design.timeline = None
        assert dd._timeline_health(design) == ([], [], 0)


class TestJointNamesHelper:
    def test_lists_joint_names(self):
        occ = FakeOcc("Wheel:1", joints=["Axle_Rev", "Rigid2"])
        assert dd._joint_names(occ) == ["Axle_Rev", "Rigid2"]

    def test_none_when_no_joints(self):
        assert dd._joint_names(FakeOcc("Block:1")) == []


# ── happy path ───────────────────────────────────────────────────────────────

class TestDelete:
    def test_deletes_named_occurrence(self):
        occs = [FakeOcc("Block:1")]
        _install(occs)
        out = _payload(dd.handler(occurrence="Block:1"))
        assert out["deleted"] is True
        assert out["occurrence"] == "Block:1"
        assert _occ(occs, "Block:1")._deleted is True       # deleteMe actually called

    def test_substring_match(self):
        occs = [FakeOcc("Wheel_RL:1")]
        _install(occs)
        out = _payload(dd.handler(occurrence="wheel"))
        assert out["occurrence"] == "Wheel_RL:1"
        assert occs[0]._deleted is True

    def test_reports_removed_joints(self):
        # deleting a jointed occurrence drops its joints — the result must NAME them.
        occs = [FakeOcc("Arm:1", joints=["Loader_Pivot", "Rigid3"])]
        _install(occs)
        out = _payload(dd.handler(occurrence="Arm:1"))
        assert out["removed_joints"] == ["Loader_Pivot", "Rigid3"]
        assert "Loader_Pivot" in out["joints_warning"]

    def test_no_joints_warning_when_unjointed(self):
        _install([FakeOcc("Block:1")])
        out = _payload(dd.handler(occurrence="Block:1"))
        assert out["removed_joints"] == []
        assert "joints_warning" not in out

    def test_reports_grounded_state(self):
        _install([FakeOcc("Base:1", grounded=True)])
        out = _payload(dd.handler(occurrence="Base:1"))
        assert out["was_grounded"] is True


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_active_design_errors(self):
        dd._common.design = lambda: None
        dd._inputs._common.design = lambda: None
        res = dd.handler(occurrence="Block:1")
        assert res["isError"] is True and "no active design" in res["message"].lower()

    def test_missing_occurrence_errors(self):
        _install([FakeOcc("Block:1")])
        res = dd.handler(occurrence="Ghost")
        assert res["isError"] is True and "no occurrence matching" in res["message"].lower()

    def test_empty_occurrence_errors(self):
        _install([FakeOcc("Block:1")])
        res = dd.handler(occurrence="")
        assert res["isError"] is True and "required" in res["message"].lower()

    def test_ambiguous_name_refused_not_wrong_instance(self):
        # two instances share local name "Bolt:1" under different sub-assemblies — a bare "Bolt"
        # substring must ERROR (naming both fullPathNames), NOT delete the first one.
        a = FakeOcc("Bolt:1", full_path="Sub-A:1+Bolt:1")
        b = FakeOcc("Bolt:1", full_path="Sub-B:1+Bolt:1")
        _install([a, b])
        res = dd.handler(occurrence="Bolt")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Sub-A:1+Bolt:1" in res["message"] and "Sub-B:1+Bolt:1" in res["message"]
        assert a._deleted is False and b._deleted is False  # neither deleted

    def test_exact_full_path_targets_right_instance(self):
        a = FakeOcc("Bolt:1", full_path="Sub-A:1+Bolt:1")
        b = FakeOcc("Bolt:1", full_path="Sub-B:1+Bolt:1")
        _install([a, b])
        out = _payload(dd.handler(occurrence="Sub-B:1+Bolt:1"))
        assert out["occurrence"] == "Bolt:1"
        assert b._deleted is True and a._deleted is False   # the RIGHT one

    def test_delete_me_false_reports_pattern_child(self):
        # deleteMe returns false (no exception) for a feature-owned (pattern/mirror) instance — report
        # it as a refusal with a pointer to the owning feature, not a false success.
        occs = [FakeOcc("Wheel:2", delete_returns=False)]
        _install(occs)
        res = dd.handler(occurrence="Wheel:2")
        assert res["isError"] is True
        assert "pattern" in res["message"].lower() or "mirror" in res["message"].lower()

    def test_timeline_error_after_delete_is_reported(self):
        # a downstream feature referenced the removed geometry: the delete stands, but it's surfaced.
        tl = FakeTimeline([FakeTimelineItem("Sketch1", 0)])
        occ = FakeOcc("Block:1")
        occ.on_delete_breaks_timeline = tl
        _install([occ], tl)
        out = _payload(dd.handler(occurrence="Block:1"))
        assert out["deleted"] is True                       # deletion stands
        assert "timeline_warning" in out
        assert "BrokenFeature" in out["timeline_warning"]
