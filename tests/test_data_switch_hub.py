"""Unit tests for ``data_switch_hub.py`` — list Autodesk data hubs and switch the active one.

Switching hubs is a real need (templates/parts live on different TeamHubs). The Fusion API exposes
app.data.dataHubs (reliable) but Data.activeHub is GETTER-ONLY, so 'switch' is best-effort: it
attempts the assignment, verifies the active hub actually changed, and returns an honest error if
not. Covers: list (with is_active flag), switch by name + by id when the setter works, the
unknown-hub guard, the already-active no-op, the close-docs warning, AND the getter-only reality
(silent-noop + raising setters must yield an honest error, never a false switched:True).
No live Fusion — fakes mimic app.data.dataHubs / activeHub in each shape.
"""

import json
from conftest import load_tool

dh = load_tool("data_switch_hub")


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

    def test_unnamed_hub_gets_placeholder(self):
        # a hub whose .name is None/empty is reported as "(unnamed)", not null.
        hubs = [FakeHub(None, "a.x"), FakeHub("Named", "a.y")]
        _install(hubs=hubs, active_idx=1)
        out = _payload(dh.handler(action="list"))
        names = [h["name"] for h in out["hubs"]]
        assert names == ["(unnamed)", "Named"]
        assert out["hub_count"] == 2

    def test_single_hub_is_active(self):
        _install(hubs=[FakeHub("Solo", "a.solo")], active_idx=0)
        out = _payload(dh.handler(action="list"))
        assert out["hub_count"] == 1
        assert out["hubs"][0]["is_active"] is True


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


# ── the getter-only reality (the audit's confirmed bug) ─────────────────────────────────────────
#
# Data.activeHub is documented GETTER-ONLY. On a real Fusion the assignment either raises or silently
# no-ops, so the active hub never actually changes. The tool must NOT report switched:True in that
# case — it must verify the change and, if it didn't take, return an honest, actionable error. These
# simulate both getter-only shapes.

class _NoopSetterData:
    """activeHub assignment is silently ignored (the no-op getter-only shape)."""
    def __init__(self, hubs, active):
        self.dataHubs = FakeHubs(hubs)
        self._active = active
    @property
    def activeHub(self):
        return self._active
    @activeHub.setter
    def activeHub(self, h):
        pass   # silently ignored — the active hub does not change


class _RaisingSetterData:
    """activeHub assignment raises (the read-only-property shape)."""
    def __init__(self, hubs, active):
        self.dataHubs = FakeHubs(hubs)
        self._active = active
    @property
    def activeHub(self):
        return self._active
    @activeHub.setter
    def activeHub(self, h):
        raise RuntimeError("property 'activeHub' of 'Data' object has no setter")


class TestSwitchGetterOnly:
    def _install_data(self, data_cls):
        hubs = [FakeHub("Acme Robotics", "a.acme"), FakeHub("Contoso Machining", "a.contoso")]
        data = data_cls(hubs, hubs[0])
        dh.app = type("A", (), {"data": data})()
        return data

    def test_silent_noop_setter_reports_honest_error_not_false_success(self):
        self._install_data(_NoopSetterData)
        res = dh.handler(action="switch", hub="Contoso Machining")
        assert res["isError"] is True, "must NOT claim switched:True when the hub never changed"
        assert "read-only" in res["message"] and "data panel" in res["message"]

    def test_raising_setter_reports_honest_error(self):
        self._install_data(_RaisingSetterData)
        res = dh.handler(action="switch", hub="Contoso Machining")
        assert res["isError"] is True
        assert "read-only" in res["message"]
