"""Unit tests for ``data_switch_hub.py`` - list Autodesk data hubs and switch the active one.

Switching hubs is a real need (templates/parts live on different TeamHubs). The Fusion API exposes
app.data.dataHubs (reliable) but Data.activeHub is GETTER-ONLY, so 'switch' is best-effort: it
attempts the assignment, verifies the active hub actually changed, and returns an honest error if
not. Covers: list (with is_active flag), switch by name + by id when the setter works, the
unknown-hub guard, the already-active no-op, the close-docs warning, AND the getter-only reality
(silent-noop + raising setters must yield an honest error, never a false switched:True).
"""

import json
from types import SimpleNamespace

import pytest

from conftest import FakeApplication, FakeData, load_tool

dh = load_tool("data_switch_hub")


def _hub(name, hub_id):
    """One DataHub as the list read walks it: its name and id."""
    return SimpleNamespace(name=name, id=hub_id)


@pytest.fixture
def cloud(monkeypatch):
    """Point the tool's session seam at a hub list, `active_idx` of them active. `data_cls` swaps in
    a scenario Data whose activeHub assignment refuses."""
    def _use(hubs=None, active_idx=0, data_cls=FakeData):
        hubs = hubs or [_hub("Acme Robotics", "a.acme"), _hub("Personal", "a.personal"),
                        _hub("Contoso Machining", "a.contoso")]
        data = data_cls(hubs=hubs, active_hub=hubs[active_idx])
        monkeypatch.setattr(dh, "app", FakeApplication(data=data))
        return data, hubs
    return _use


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── list ──────────────────────────────────────────────────────────────────────

class TestList:
    def test_lists_all_hubs_with_active_flag(self, cloud):
        cloud(active_idx=1)
        out = _payload(dh.handler(action="list"))
        names = [h["name"] for h in out["hubs"]]
        assert names == ["Acme Robotics", "Personal", "Contoso Machining"]
        active = [h for h in out["hubs"] if h["is_active"]]
        assert len(active) == 1 and active[0]["name"] == "Personal"

    def test_default_action_is_list(self, cloud):
        cloud()
        out = _payload(dh.handler())
        assert "hubs" in out and out["active_hub"]["name"] == "Acme Robotics"

    def test_unnamed_hub_gets_placeholder(self, cloud):
        # a hub whose .name is None/empty is reported as "(unnamed)", not null.
        cloud(hubs=[_hub(None, "a.x"), _hub("Named", "a.y")], active_idx=1)
        out = _payload(dh.handler(action="list"))
        names = [h["name"] for h in out["hubs"]]
        assert names == ["(unnamed)", "Named"]
        assert out["hub_count"] == 2

    def test_single_hub_is_active(self, cloud):
        cloud(hubs=[_hub("Solo", "a.solo")], active_idx=0)
        out = _payload(dh.handler(action="list"))
        assert out["hub_count"] == 1
        assert out["hubs"][0]["is_active"] is True


# ── switch ────────────────────────────────────────────────────────────────────

class TestSwitch:
    def test_switch_by_name(self, cloud):
        data, _ = cloud(active_idx=0)
        out = _payload(dh.handler(action="switch", hub="Contoso Machining"))
        assert out["switched"] is True
        assert data.activeHub.name == "Contoso Machining"
        assert out["active_hub"]["name"] == "Contoso Machining"
        # must warn it closes documents
        assert "close" in out["note"].lower() or "document" in out["note"].lower()

    def test_switch_by_id(self, cloud):
        data, _ = cloud(active_idx=0)
        _payload(dh.handler(action="switch", hub="a.personal"))
        assert data.activeHub.id == "a.personal"

    def test_switch_case_insensitive_name(self, cloud):
        data, _ = cloud(active_idx=0)
        _payload(dh.handler(action="switch", hub="  contoso MACHINING "))
        assert data.activeHub.name == "Contoso Machining"

    def test_already_active_is_noop(self, cloud):
        data, _ = cloud(active_idx=0)
        out = _payload(dh.handler(action="switch", hub="Acme Robotics"))
        assert out["switched"] is False and out["already_active"] is True
        assert data._hub_sets == []        # never reassigned

    def test_unknown_hub_errors_and_lists_available(self, cloud):
        cloud()
        res = dh.handler(action="switch", hub="Nope")
        assert res["isError"] is True
        assert "Nope" in res["message"]
        # should help by naming available hubs
        assert "Acme Robotics" in res["message"]

    def test_switch_requires_hub(self, cloud):
        cloud()
        res = dh.handler(action="switch")
        assert res["isError"] is True and "hub" in res["message"]

    def test_unknown_action_errors(self, cloud):
        cloud()
        res = dh.handler(action="teleport")
        assert res["isError"] is True and "action" in res["message"]


# ── the getter-only reality ─────────────────────────────────────────────────────────────────────
#
# Data.activeHub is documented GETTER-ONLY. On a real Fusion the assignment either raises or silently
# no-ops, so the active hub never actually changes. The tool must NOT report switched:True in that
# case - it must verify the change and, if it didn't take, return an honest, actionable error. These
# simulate both getter-only shapes.

class _IgnoresTheAssignment(FakeData):
    """A Data whose activeHub assignment is accepted and changes nothing - the silent no-op shape."""

    @FakeData.activeHub.setter
    def activeHub(self, hub):
        self._hub_sets.append(hub)


class _RefusesTheAssignment(FakeData):
    """A Data whose activeHub assignment RAISES - the read-only-property shape."""

    @FakeData.activeHub.setter
    def activeHub(self, hub):
        self._hub_sets.append(hub)
        raise RuntimeError("property 'activeHub' of 'Data' object has no setter")


class TestSwitchGetterOnly:
    def test_silent_noop_setter_reports_honest_error_not_false_success(self, cloud):
        cloud(active_idx=0, data_cls=_IgnoresTheAssignment)
        res = dh.handler(action="switch", hub="Contoso Machining")
        assert res["isError"] is True, "must NOT claim switched:True when the hub never changed"
        assert "read-only" in res["message"] and "data panel" in res["message"]

    def test_raising_setter_reports_honest_error(self, cloud):
        cloud(active_idx=0, data_cls=_RefusesTheAssignment)
        res = dh.handler(action="switch", hub="Contoso Machining")
        assert res["isError"] is True
        assert "read-only" in res["message"]
