"""Unit tests for ``data_hubs.py`` — list Autodesk data hubs and switch the active one.

Switching hubs is a real need (templates/parts live on different TeamHubs) and was previously a
manual Fusion-UI action. The Fusion API exposes app.data.dataHubs + a settable app.data.activeHub —
this tool wraps both. Covers: list (with is_active flag), switch by name + by id, the unknown-hub
guard, the already-active no-op, and that switch reports the disruptive close-docs warning.
No live Fusion — fakes mimic app.data.dataHubs / activeHub.
"""

import json
from conftest import load_tool

dh = load_tool("data_hubs")


class FakeHub:
    def __init__(self, name, id):
        self.name = name
        self.id = id


class FakeHubs:
    def __init__(self, hubs):
        self._l = hubs
    @property
    def count(self):
        return len(self._l)
    def item(self, i):
        return self._l[i]


class FakeData:
    def __init__(self, hubs, active):
        self.dataHubs = FakeHubs(hubs)
        self._active = active
        self.set_calls = []
    @property
    def activeHub(self):
        return self._active
    @activeHub.setter
    def activeHub(self, h):
        self.set_calls.append(h)
        self._active = h


def _install(hubs=None, active_idx=0):
    hubs = hubs or [FakeHub("Acme Robotics", "a.acme"), FakeHub("Personal", "a.personal"),
                    FakeHub("Contoso Machining", "a.contoso")]
    data = FakeData(hubs, hubs[active_idx])
    dh.app = type("A", (), {"data": data})()
    return data, hubs


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── list ──────────────────────────────────────────────────────────────────────

class TestList:
    def test_lists_all_hubs_with_active_flag(self):
        _install(active_idx=1)
        out = _payload(dh.handler(action="list"))
        names = [h["name"] for h in out["hubs"]]
        assert names == ["Acme Robotics", "Personal", "Contoso Machining"]
        active = [h for h in out["hubs"] if h["is_active"]]
        assert len(active) == 1 and active[0]["name"] == "Personal"

    def test_default_action_is_list(self):
        _install()
        out = _payload(dh.handler())
        assert "hubs" in out and out["active_hub"]["name"] == "Acme Robotics"


# ── switch ────────────────────────────────────────────────────────────────────

class TestSwitch:
    def test_switch_by_name(self):
        data, _ = _install(active_idx=0)
        out = _payload(dh.handler(action="switch", hub="Contoso Machining"))
        assert out["switched"] is True
        assert data.activeHub.name == "Contoso Machining"
        assert out["active_hub"]["name"] == "Contoso Machining"
        # must warn it closes documents
        assert "close" in out["note"].lower() or "document" in out["note"].lower()

    def test_switch_by_id(self):
        data, _ = _install(active_idx=0)
        _payload(dh.handler(action="switch", hub="a.personal"))
        assert data.activeHub.id == "a.personal"

    def test_switch_case_insensitive_name(self):
        data, _ = _install(active_idx=0)
        _payload(dh.handler(action="switch", hub="  contoso MACHINING "))
        assert data.activeHub.name == "Contoso Machining"

    def test_already_active_is_noop(self):
        data, _ = _install(active_idx=0)
        out = _payload(dh.handler(action="switch", hub="Acme Robotics"))
        assert out["switched"] is False and out["already_active"] is True
        assert data.set_calls == []        # never reassigned

    def test_unknown_hub_errors_and_lists_available(self):
        _install()
        res = dh.handler(action="switch", hub="Nope")
        assert res["isError"] is True
        assert "Nope" in res["message"]
        # should help by naming available hubs
        assert "Acme Robotics" in res["message"]

    def test_switch_requires_hub(self):
        _install()
        res = dh.handler(action="switch")
        assert res["isError"] is True and "hub" in res["message"]

    def test_unknown_action_errors(self):
        _install()
        res = dh.handler(action="teleport")
        assert res["isError"] is True and "action" in res["message"]
