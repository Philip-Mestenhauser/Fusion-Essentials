"""Unit tests for ``param_add.py`` - the single and batch adds, health-guarded.

add rolls back a parameter that introduces a NEW timeline error, and publishes the favorite flag
as the parameter reads it rather than as it was asked for. The fakes below model a tiny timeline
(items with healthState) and a userParameters collection that supports add/itemByName/deleteMe.
"""

import json

from conftest import load_tool

params = load_tool("param_add")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


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


class FakeParam:
    def __init__(self, name, expression="", owner=None):
        self.name = name
        self.expression = expression
        self.isFavorite = False
        self.unit = "mm"
        self.comment = ""
        self.value = 1.0
        self._owner = owner
        self._deleted = False

    def deleteMe(self):
        self._deleted = True
        if self._owner is not None and self in self._owner._items:
            self._owner._items.remove(self)
        return True


class FakeUserParams:
    def __init__(self, items=()):
        self._items = list(items)
        for it in self._items:
            it._owner = self
        # add() can be told to inject a downstream error into the timeline.
        self.on_add_breaks_timeline = None   # a FakeTimeline to mutate, or None

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None

    def add(self, name, _value_input, _unit, _comment):
        p = FakeParam(name, owner=self)
        self._items.append(p)
        if self.on_add_breaks_timeline is not None:
            self.on_add_breaks_timeline._items.append(FakeTimelineItem("BrokenFeature", health=2))
        return p


class FakeParamsDesign:
    def __init__(self, user_params, timeline, all_params=None):
        self.userParameters = user_params
        self.timeline = timeline
        self.allParameters = list(all_params if all_params is not None else user_params._items)


def _stub_design(monkeypatch, design):
    monkeypatch.setattr(params._common, "design", lambda: design)
    # the add path uses adsk.core.ValueInput.createByString - make it benign.
    import adsk.core
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("VI", s))


class TestTimelineHealth:
    # the shared _timeline_health walk the add/delete rollback guard runs
    def test_rolls_up_errors_and_warnings(self):
        tl = FakeTimeline([FakeTimelineItem("A", 0), FakeTimelineItem("B", 2),
                           FakeTimelineItem("C", 1), FakeTimelineItem("D", 2)])
        design = FakeParamsDesign(FakeUserParams(), tl)
        errors, warnings, total = params._timeline_health(design)
        assert total == 4
        assert errors == ["B", "D"]
        assert warnings == ["C"]


class TestAddHandler:
    def test_add_rejects_duplicate(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX", expression="5 mm")
        assert res["isError"] is True and "already exists" in res["message"]

    def test_add_succeeds_when_timeline_stays_healthy(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="NewP", expression="3 mm"))
        assert out["added"] is True
        assert up.itemByName("NewP") is not None      # it stuck

    def test_add_rolls_back_on_new_timeline_error(self, monkeypatch):
        tl = FakeTimeline([FakeTimelineItem("A", 0)])
        up = FakeUserParams([])
        up.on_add_breaks_timeline = tl                # adding will inject an error
        design = FakeParamsDesign(up, tl)
        _stub_design(monkeypatch, design)
        res = params.handler(name="BadP", expression="oops")
        assert res["isError"] is True
        assert "rolled back" in res["message"]
        assert up.itemByName("BadP") is None          # removed again

    def test_add_requires_name_and_expression(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams(), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res1 = params.handler(name="", expression="5")
        assert res1["isError"] is True
        assert "Missing 'name'" in res1["message"]
        res2 = params.handler(name="X", expression="")
        assert res2["isError"] is True
        assert "missing 'expression'" in res2["message"]


class TestAddBatch:
    # Adding N parameters is ONE batch call, not N separate calls.
    def test_batch_adds_all(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(params=[
            {"name": "WheelDia", "expression": "350 mm"},
            {"name": "AxleDia", "expression": "14 mm", "favorite": True},
            {"name": "CrankLen", "expression": "125 mm", "comment": "arm"},
        ]))
        assert out["added_count"] == 3
        assert {r["parameter"]["name"] for r in out["results"]} == {"WheelDia", "AxleDia", "CrankLen"}
        for nm in ("WheelDia", "AxleDia", "CrankLen"):
            assert up.itemByName(nm) is not None

    def test_batch_stops_and_reports_the_failing_entry(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        # 2nd entry is missing an expression -> that entry errors, the batch reports which index
        res = params.handler(params=[
            {"name": "Good", "expression": "1 mm"},
            {"name": "Bad", "expression": ""},
        ])
        assert res["isError"] is True
        assert "Bad" in res["message"] and "[1]" in res["message"]
        assert up.itemByName("Good") is not None        # the earlier good one is kept

    def test_single_param_path_still_works(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="Solo", expression="9 mm"))
        assert out["added"] is True and up.itemByName("Solo") is not None


class TestAddFavorite:
    def test_favorite_reported_from_param_state(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="P", expression="5 mm", favorite=True))
        assert out["favorite"] is True
        assert up.itemByName("P").isFavorite is True

    def test_a_stuck_favorite_is_published_as_the_parameter_reads_it(self, monkeypatch):
        # The add payload is a READ of the parameter that landed, never an echo of the request: the
        # isFavorite assignment here is accepted and changes nothing, so both the flag and the
        # parameter row report the state the parameter actually carries. Echoing the request would
        # report favorite:true over a parameter nothing was set on.
        class StuckFavoriteParam(FakeParam):
            @property
            def isFavorite(self):
                return False

            @isFavorite.setter
            def isFavorite(self, value):
                pass                                  # silently ignores the assignment

        class StuckFavoriteParams(FakeUserParams):
            def add(self, name, _value_input, _unit, _comment):
                p = StuckFavoriteParam(name, owner=self)
                self._items.append(p)
                return p

        up = StuckFavoriteParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="NewP", expression="3 mm", favorite=True))
        assert out["added"] is True
        assert out["favorite"] is False                # as it READS, not as it was asked for
        assert out["parameter"]["favorite"] is False   # the same read, in the parameter row
        assert out["parameter"]["name"] == "NewP"
        assert up.itemByName("NewP").isFavorite is False
