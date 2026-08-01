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

    def test_resolver_error_surfaces(self, rig):
        rig.monkeypatch.setattr(pd._pmi, "find_annotation",
                                lambda d, n, c="": (None, None, "No PMI named 'X'."))
        assert "No PMI named" in error_message(pd.handler(annotation="X"))

    def test_no_active_design_is_an_error(self, rig):
        rig.monkeypatch.setattr(pd._common, "design", lambda: None)
        assert "No active design" in error_message(pd.handler(annotation="N"))
