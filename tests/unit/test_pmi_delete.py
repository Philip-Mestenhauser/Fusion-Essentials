"""Tests for `pmi_delete` - the deleteMe() gate, the survivor re-check, and the isDeletable
refusal: a delete only claims success when the annotation is actually gone."""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, error_message

pd = load_tool("pmi_delete")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _FakeAnn:
    def __init__(self, name="Note1", deletable=True, delete_result=True):
        self.name = name
        self.objectType = "adsk::fusion::PMILeaderLineNote"
        self.isDeletable = deletable
        self._delete_result = delete_result
        self.deleted = False

    def deleteMe(self):
        self.deleted = self._delete_result
        return self._delete_result


@pytest.fixture
def rig(monkeypatch):
    ann = _FakeAnn()
    comp = SimpleNamespace(name="Root")
    state = SimpleNamespace(ann=ann, comp=comp, monkeypatch=monkeypatch)

    def find(d, name, component=""):
        # after a successful delete the annotation no longer resolves
        if state.ann.deleted:
            return None, None, f"No PMI named '{name}'. Available: none."
        return state.ann, state.comp, None
    monkeypatch.setattr(pd._common, "design", lambda: object())
    monkeypatch.setattr(pd._pmi, "find_annotation", find)
    monkeypatch.setattr(pd._pmi, "walk_annotations", lambda d: iter([]))
    return state


class TestDelete:
    def test_deletes_and_confirms_gone(self, rig):
        out = _payload(pd.handler(annotation="Note1"))
        assert out["deleted"] == "Note1" and out["remaining_pmi"] == 0

    def test_not_deletable_is_refused_without_change(self, rig):
        rig.ann.isDeletable = False
        msg = error_message(pd.handler(annotation="Note1"))
        assert "isDeletable=false" in msg and rig.ann.deleted is False

    def test_a_declined_delete_is_an_error(self, rig):
        rig.ann._delete_result = False
        assert "declined" in error_message(pd.handler(annotation="Note1"))

    def test_a_survivor_after_success_is_an_error(self, rig):
        # deleteMe() lies (returns True) but the annotation still resolves afterwards
        rig.ann.deleteMe = lambda: True                # does NOT flip .deleted
        assert "still resolves" in error_message(pd.handler(annotation="Note1"))

    def test_a_handle_still_reporting_isValid_is_an_error_even_when_the_name_is_gone(self, rig):
        # The two survivor checks are independent: the NAME can stop resolving (a re-resolve
        # after the collection dropped it) while the deleted handle still reads isValid=True.
        # Trusting the name alone reports a delete that did not happen.
        rig.ann.isValid = True
        assert "still resolves" in error_message(pd.handler(annotation="Note1"))

    def test_a_handle_that_went_invalid_confirms_the_delete(self, rig):
        rig.ann.isValid = False
        out = _payload(pd.handler(annotation="Note1"))
        assert out["deleted"] == "Note1"

    def test_an_unreadable_isValid_does_not_block_a_confirmed_delete(self, rig):
        # A deleted proxy commonly refuses every read; that is not evidence of a survivor, and
        # the NAME re-resolve is what carries the confirmation there.
        class Dead(_FakeAnn):
            @property
            def isValid(self):
                raise RuntimeError("object has been deleted")
        dead = Dead()
        rig.ann = dead
        out = _payload(pd.handler(annotation="Note1"))
        assert out["deleted"] == "Note1"

    def test_the_remaining_count_comes_from_the_design_wide_walk(self, rig):
        rig.monkeypatch.setattr(
            pd._pmi, "walk_annotations",
            lambda d: iter([(rig.comp, object()), (rig.comp, object())]))
        assert _payload(pd.handler(annotation="Note1"))["remaining_pmi"] == 2

    def test_resolver_error_surfaces(self, rig):
        rig.monkeypatch.setattr(pd._pmi, "find_annotation",
                                lambda d, n, c="": (None, None, "No PMI named 'X'."))
        assert "No PMI named" in error_message(pd.handler(annotation="X"))

    def test_no_active_design_is_an_error(self, rig):
        rig.monkeypatch.setattr(pd._common, "design", lambda: None)
        assert "No active design" in error_message(pd.handler(annotation="N"))
