"""Unit tests for ``param_delete.py`` - the reference guard and the timeline-health regression.

delete refuses when another expression references the name (matched on WORD boundaries), reports a
deleteMe() that answered false, and reports a health regression afterwards.
"""

import json

from conftest import load_tool

params = load_tool("param_delete")


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

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None


class FakeParamsDesign:
    def __init__(self, user_params, timeline, all_params=None):
        self.userParameters = user_params
        self.timeline = timeline
        self.allParameters = list(all_params if all_params is not None else user_params._items)


def _stub_design(monkeypatch, design):
    monkeypatch.setattr(params._common, "design", lambda: design)


class TestDeleteHandler:
    def test_delete_refuses_if_referenced(self, monkeypatch):
        part = FakeParam("PartX", "10 mm")
        user = FakeParam("Half", "PartX / 2")          # references PartX
        up = FakeUserParams([part, user])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[part, user])
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX")
        assert res["isError"] is True
        assert "referenced by: Half" in res["message"]
        assert part._deleted is False                  # not deleted

    def test_reference_match_is_word_boundary(self, monkeypatch):
        # 'PartX' must NOT be considered referenced by 'PartXY' (substring, not a
        # whole token) - the regex uses word boundaries.
        part = FakeParam("PartX", "10 mm")
        other = FakeParam("Calc", "PartXY + 1")        # different token
        up = FakeUserParams([part, other])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[part, other])
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="PartX"))
        assert out["deleted"] is True
        assert part._deleted is True

    def test_delete_unknown_param_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="Ghost")
        assert res["isError"] is True and "No USER parameter" in res["message"]


class TestDeleteHandlerExtra:
    def test_delete_me_false_reported(self, monkeypatch):
        class Stubborn(FakeParam):
            def deleteMe(self):
                return False
        p = Stubborn("PartX", "10 mm")
        up = FakeUserParams([p])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX")
        assert res["isError"] is True and "refused to delete" in res["message"]

    def test_timeline_error_after_delete_reported(self, monkeypatch):
        tl = FakeTimeline([FakeTimelineItem("A", 0)])

        class Breaking(FakeParam):
            def deleteMe(self):
                tl._items.append(FakeTimelineItem("BrokenChild", health=2))
                if self in self._owner._items:
                    self._owner._items.remove(self)
                return True
        p = Breaking("PartX", "10 mm")
        up = FakeUserParams([p])
        design = FakeParamsDesign(up, tl, all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX")
        assert res["isError"] is True
        assert "introduced a timeline error" in res["message"]

    def test_empty_name_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="")
        assert res["isError"] is True
        assert "Provide 'name'" in res["message"]
