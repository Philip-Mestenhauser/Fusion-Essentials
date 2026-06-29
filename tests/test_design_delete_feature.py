"""Unit tests for ``design_delete_feature.py`` — delete one timeline feature by name.

The logic pinned here, no live Fusion: name matching (exact first, then substring; ambiguity REFUSED
with candidates), the GROUP guard (a timeline group has no deletable entity), the no-entity guard,
the actual ``entity.deleteMe()`` call (captured so a wrong method name regresses here), the
deleteMe-returns-false path, and the before/after timeline-health guard (a delete that breaks a
downstream feature is reported, the deletion still standing).
"""

import json

from conftest import load_tool

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
        errors, warnings, total = df._health(tl)
        assert total == 3 and errors == ["B"] and warnings == ["C"]

    def test_none_timeline_empty(self):
        assert df._health(None) == ([], [], 0)


class TestFindByName:
    def test_exact_match_preferred_over_substring(self):
        tl = FakeTimeline([FakeTLObject("Fillet1", 0), FakeTLObject("Fillet10", 1)])
        hits = df._find_objects_by_name(tl, "Fillet1")
        assert [o.name for o in hits] == ["Fillet1"]      # exact only, not Fillet10


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
