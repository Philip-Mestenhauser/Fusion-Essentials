"""Unit tests for ``design_set_mode.py`` - the parametric <-> direct conversion.

Pinned: it REFUSES parametric->direct without confirm, SUCCEEDS with confirm, direct->parametric is
free (no confirm), it is an idempotent no-op when already in target, and BOTH published flags ride
on the post-assignment READ-BACK rather than on the request.
"""

import json

from conftest import load_tool

dm = load_tool("design_set_mode")


class _Timeline:
    def __init__(self, count):
        self._count = count

    @property
    def count(self):
        return self._count


class _Comp:
    def __init__(self, name="Comp"):
        self.name = name


class FakeDesign:
    """A design exposing designType (numeric) and an optional timeline. raise_on_set models a real
    assignment failure; ignore_set models the platform lie - accepted, and nothing changes."""
    def __init__(self, design_type=1, timeline_count=0, no_timeline=False,
                 raise_on_set=False, ignore_set=False):
        self.designType = design_type
        self._raise_on_set = raise_on_set
        self._ignore_set = ignore_set
        if not no_timeline:
            self.timeline = _Timeline(timeline_count)
        self.rootComponent = _Comp("Root")
        self.activeComponent = self.rootComponent

    def __setattr__(self, name, value):
        if name == "designType":
            if getattr(self, "_raise_on_set", False):
                raise RuntimeError("designType assignment blew up")
            if getattr(self, "_ignore_set", False):
                return
        super().__setattr__(name, value)


def _install(monkeypatch, design):
    app = type("A", (), {"activeProduct": design})()
    monkeypatch.setattr(dm._common, "app", app)
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion.Design, "cast", lambda x: x if isinstance(x, FakeDesign) else None)
    monkeypatch.setattr(dm._inputs._common, "design", lambda: design)
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestSetMode:
    def test_no_active_design(self, monkeypatch):
        _install(monkeypatch, None)
        res = dm.handler(target="direct", confirm_history_loss=True)
        assert res["isError"] is True and "No active design" in res["message"]

    def test_bad_target(self, monkeypatch):
        _install(monkeypatch, FakeDesign(design_type=1))
        res = dm.handler(target="hologram")
        assert res["isError"] is True and "must be one of" in res["message"]

    def test_parametric_to_direct_refused_without_confirm(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        res = dm.handler(target="direct")            # no confirm
        assert res["isError"] is True
        assert "confirm_history_loss=true" in res["message"]
        assert "DIRECT" in res["message"] and "irreversible" in res["message"].lower()
        # and it must NOT have mutated the design
        assert des.designType == 1

    def test_parametric_to_direct_succeeds_with_confirm(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        out = _payload(dm.handler(target="direct", confirm_history_loss=True))
        assert out["converted"] is True
        assert out["from"] == "parametric" and out["to"] == "direct"
        assert out["history_discarded"] is True
        assert des.designType == 0          # actually flipped to DirectDesignType

    def test_direct_to_parametric_is_free(self, monkeypatch):
        # the asymmetry: no confirm needed, no history discarded
        des = _install(monkeypatch, FakeDesign(design_type=0, no_timeline=True))
        out = _payload(dm.handler(target="parametric"))
        assert out["converted"] is True
        assert out["from"] == "direct" and out["to"] == "parametric"
        assert out["history_discarded"] is False
        assert des.designType == 1

    def test_idempotent_noop_when_already_target(self, monkeypatch):
        des = _install(monkeypatch, FakeDesign(design_type=1))
        out = _payload(dm.handler(target="parametric"))
        assert out["converted"] is False and "Already" in out["note"]
        assert des.designType == 1

    def test_assignment_exception_surfaces_not_swallowed(self, monkeypatch):
        # a real failure on the mutation is surfaced as an error (never safe()-swallowed to a false ok)
        _install(monkeypatch, FakeDesign(design_type=0, no_timeline=True, raise_on_set=True))
        res = dm.handler(target="parametric")
        assert res["isError"] is True and "Could not convert" in res["message"]

    def test_history_discarded_rides_on_the_read_back_not_the_request(self, monkeypatch):
        # the assignment is accepted and changes nothing (the measured platform lie). A conversion
        # that did not happen discarded no timeline - so BOTH flags read false, agreeing with the
        # note. history_discarded=true here would be the request talking, not the design.
        des = _install(monkeypatch, FakeDesign(design_type=1, ignore_set=True))
        out = _payload(dm.handler(target="direct", confirm_history_loss=True))
        assert out["converted"] is False
        assert out["history_discarded"] is False
        assert out["now"] == "parametric" and des.designType == 1
        assert "did not take" in out["note"]

    def test_an_unreadable_mode_publishes_null_flags_not_a_verdict(self, monkeypatch):
        # designType does not decode to either mode after the assignment: whether the conversion
        # took, and so whether the timeline went with it, is UNKNOWN - null, never false or true.
        _install(monkeypatch, FakeDesign(design_type="?", ignore_set=True))
        out = _payload(dm.handler(target="direct", confirm_history_loss=True))
        assert out["converted"] is None
        assert out["history_discarded"] is None
        assert out["now"] == "unknown"
        assert "UNCONFIRMED" in out["note"]

    def test_a_conversion_that_took_still_reports_the_discard(self, monkeypatch):
        # the other side of the same gate: a PROVEN parametric->direct did discard the timeline.
        _install(monkeypatch, FakeDesign(design_type=1))
        out = _payload(dm.handler(target="direct", confirm_history_loss=True))
        assert out["converted"] is True and out["history_discarded"] is True
