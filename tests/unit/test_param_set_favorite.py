"""Unit tests for ``param_set_favorite.py`` - the flag is published as the parameter reads it back."""

import json

from conftest import load_tool

params = load_tool("param_set_favorite")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


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


class TestFavoriteHandler:
    def test_sets_favorite_flag(self, monkeypatch):
        p = FakeParam("PartX", "10 mm")
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="PartX", favorite=True))
        assert out["favorite"] is True
        assert p.isFavorite is True

    def test_unknown_param_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="Ghost")
        assert res["isError"] is True and "No USER parameter" in res["message"]

    def test_favorite_set_failure_surfaces(self, monkeypatch):
        class Stubborn(FakeParam):
            def __setattr__(self, k, v):
                if k == "isFavorite" and getattr(self, "_built", False):
                    raise RuntimeError("read-only")
                object.__setattr__(self, k, v)
        p = Stubborn("PartX", "10 mm")
        p._built = True
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX", favorite=True)
        assert res["isError"] is True and "Could not set favorite" in res["message"]

    def test_empty_name_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="")
        assert res["isError"] is True
        assert "Provide 'name'" in res["message"]

    def test_a_stuck_flag_is_published_as_it_reads_not_as_asked(self, monkeypatch):
        # The payload's 'favorite' IS the post-write re-read, so an assignment the platform
        # swallows shows up as the flag that is actually there. A payload echoing the REQUEST
        # would report true over a parameter nothing was set on.
        class StuckFavorite(FakeParam):
            @property
            def isFavorite(self):
                return False

            @isFavorite.setter
            def isFavorite(self, v):
                pass                                    # silently ignores the assignment

        p = StuckFavorite("PartX", "10 mm")
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="PartX", favorite=True))
        assert out["favorite"] is False                 # the no-op is visible in the payload
        assert out["name"] == "PartX"
