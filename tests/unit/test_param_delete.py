"""Unit tests for ``param_delete.py`` - the reference guard and the timeline-health regression.

delete refuses when another expression references the name (matched on WORD boundaries), reports a
deleteMe() that answered false, and reports a health regression afterwards.
"""

import live_api_facts
from conftest import (FakeTimelineObject, FakeUserParameter, FakeUserParameters, MakeDesign,
                      load_tool, make_timeline, payload as _payload)

params = load_tool("param_delete")

_ERROR = live_api_facts.ENUMS["fusion.FeatureHealthStates"]["ErrorFeatureHealthState"]


class BreakingDelete(FakeUserParameter):
    """A delete that SUCCEEDS and lands a broken feature in `timeline` - the downstream regression
    the guard reports after the deletion already stands."""

    def __init__(self, timeline, **kwargs):
        super().__init__(**kwargs)
        self._timeline = timeline

    def deleteMe(self):
        did = super().deleteMe()
        self._timeline._items.append(FakeTimelineObject(name="BrokenChild", health=_ERROR))
        return did


def _design(user_params, timeline, all_params=()):
    """A design carrying the two collections the param write path walks."""
    return MakeDesign(user_parameters=user_params, timeline=timeline,
                      all_parameters=list(all_params))


def _stub_design(monkeypatch, design):
    monkeypatch.setattr(params._common, "design", lambda: design)


class TestDeleteHandler:
    def test_delete_refuses_if_referenced(self, monkeypatch):
        part = FakeUserParameter(name="PartX", expression="10 mm")
        user = FakeUserParameter(name="Half", expression="PartX / 2")   # references PartX
        up = FakeUserParameters([part, user])
        design = _design(up, make_timeline(), all_params=[part, user])
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX")
        assert res["isError"] is True
        assert "referenced by: Half" in res["message"]
        assert part._deleted is False                  # not deleted

    def test_reference_match_is_word_boundary(self, monkeypatch):
        # 'PartX' must NOT be considered referenced by 'PartXY' (substring, not a
        # whole token) - the regex uses word boundaries.
        part = FakeUserParameter(name="PartX", expression="10 mm")
        other = FakeUserParameter(name="Calc", expression="PartXY + 1")  # different token
        up = FakeUserParameters([part, other])
        design = _design(up, make_timeline(), all_params=[part, other])
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="PartX"))
        assert out["deleted"] is True
        assert part._deleted is True

    def test_delete_unknown_param_errors(self, monkeypatch):
        design = _design(FakeUserParameters([]), make_timeline())
        _stub_design(monkeypatch, design)
        res = params.handler(name="Ghost")
        assert res["isError"] is True and "No USER parameter" in res["message"]


class TestDeleteHandlerExtra:
    def test_delete_me_false_reported(self, monkeypatch):
        p = FakeUserParameter(name="PartX", expression="10 mm", delete_ok=False)
        up = FakeUserParameters([p])
        design = _design(up, make_timeline(), all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX")
        assert res["isError"] is True and "refused to delete" in res["message"]
        assert up.itemByName("PartX") is p              # and it is still there

    def test_timeline_error_after_delete_reported(self, monkeypatch):
        tl = make_timeline("A")
        p = BreakingDelete(tl, name="PartX", expression="10 mm")
        up = FakeUserParameters([p])
        design = _design(up, tl, all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX")
        assert res["isError"] is True
        assert "introduced a timeline error" in res["message"]

    def test_empty_name_errors(self, monkeypatch):
        design = _design(FakeUserParameters([]), make_timeline())
        _stub_design(monkeypatch, design)
        res = params.handler(name="")
        assert res["isError"] is True
        assert "Provide 'name'" in res["message"]
