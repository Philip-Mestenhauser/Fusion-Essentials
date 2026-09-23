# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_activate_setup: the name guard, miss-lists-available, the activation read-back (an
activate() the platform accepts but that does not take - or that cannot be read back at all - must
surface as an error), and the view fit, whose outcome the note states rather than assumes."""

from unittest.mock import Mock

import pytest

from conftest import FakeSetup, Viewport, load_tool, payload, error_message

mod = load_tool("cam_activate_setup")


class UnreadableActiveSetup(FakeSetup):
    """activate() runs and isActive cannot be READ afterwards - the state a fail-open gate that only
    refuses an explicit False lets through as activated."""

    @property
    def isActive(self):
        raise RuntimeError("isActive is unreadable")

    @isActive.setter
    def isActive(self, _value):
        pass


class _RaisingCameraViewport(Viewport):
    """The camera ASSIGNMENT itself raises - the platform refusing the write outright."""

    def __init__(self, message):
        super().__init__()
        self._message = message

    @property
    def camera(self):
        return Viewport.camera.fget(self)

    @camera.setter
    def camera(self, value):
        raise RuntimeError(self._message)


@pytest.fixture
def viewport(monkeypatch):
    """Point the module's app at one viewport, or at none: a plain one, one whose camera
    ASSIGNMENT raises, or no viewport."""
    def _viewport(raises=None, absent=False):
        vp = None
        if not absent:
            vp = _RaisingCameraViewport(raises) if raises else Viewport()
        monkeypatch.setattr(mod, "app", Mock(activeViewport=vp))
        return vp
    return _viewport


@pytest.fixture
def wire(monkeypatch):
    """Point the module's CAM seams at fakes: get_cam yields a product (or the given error),
    find_setup resolves to (setup, available_names, refusal) - the refusal is the resolver's own
    ready-to-return text on a miss, which the handler passes through verbatim."""
    def _wire(setup=None, available=(), cam_error=None, refusal=None):
        monkeypatch.setattr(
            mod, "get_cam",
            lambda: (None, cam_error) if cam_error else (object(), None))
        monkeypatch.setattr(mod, "find_setup",
                            lambda cam, want: (setup, list(available), refusal))
        return setup
    return _wire


def test_blank_setup_name_is_refused(wire):
    wire()
    assert "Provide 'setup'" in error_message(mod.activate_setup_handler(setup="  "))


def test_cam_gate_error_is_surfaced(wire):
    wire(cam_error="No Manufacture product")
    assert "No Manufacture product" in error_message(mod.activate_setup_handler(setup="Op10"))


def test_a_miss_returns_the_resolvers_own_refusal(wire):
    # the handler must not re-word it: only the resolver knows whether the name was absent or
    # AMBIGUOUS, so a local "not found" prefix would assert absence about a duplicated name.
    wire(setup=None, available=["Top", "Bottom"],
         refusal="No setup named 'Nope'. Available: Top, Bottom.")
    assert error_message(mod.activate_setup_handler(setup="Nope")) == (
        "No setup named 'Nope'. Available: Top, Bottom.")


def test_an_ambiguous_name_is_not_reported_as_missing(wire):
    # the refusal is resolve_cam_node's own text: two same-named setups are told apart by the
    # '<name>#<n>' address the same 'setup' input takes back (test__cam_common asserts it against
    # the resolver itself).
    wire(setup=None, available=["Dup", "Dup"],
         refusal="'Dup' is ambiguous - 2 CAM items share that name: Dup#1 (2 operations), "
                 "Dup#2 (1 operation). Retry with one of those '<name>#<n>' addresses; the number "
                 "counts the items of that name in the order listed here.")
    msg = error_message(mod.activate_setup_handler(setup="Dup"))
    assert "is ambiguous" in msg
    assert "not found" not in msg.lower()


def test_activates_and_reports_the_setup_name(wire):
    setup = wire(setup=FakeSetup(name="Op10"))
    out = payload(mod.activate_setup_handler(setup="Op10"))
    assert setup._activate_calls == 1
    assert out["activated"] == "Op10"


def test_activate_that_does_not_take_is_an_error(wire):
    wire(setup=FakeSetup(name="Op10", activate_lies=True))
    assert "isActive=false" in error_message(mod.activate_setup_handler(setup="Op10"))


def test_activate_raising_is_surfaced_not_swallowed(wire):
    setup = wire(setup=FakeSetup(name="Op10"))

    def boom():
        raise RuntimeError("kaput")

    setup.activate = boom
    msg = error_message(mod.activate_setup_handler(setup="Op10"))
    assert "Failed to activate 'Op10'" in msg
    assert "kaput" in msg


def test_an_unreadable_isActive_is_unconfirmed_not_activated(wire):
    # the gate is 'is not True', not 'is False': a flag that does not READ has not told this call
    # the setup became active, and 'activated' is the one thing the payload exists to state.
    wire(setup=UnreadableActiveSetup(name="Op10"))
    msg = error_message(mod.activate_setup_handler(setup="Op10"))
    assert "UNCONFIRMED" in msg and "isActive cannot be read" in msg
    # and it must NOT be reported as the OTHER failure - a read that did not answer is not a false
    assert "isActive=false" not in msg


class TestViewFit:
    """The fit is a second effect, and a swallowed one is invisible to the caller: the note may only
    say the view was fit when the fit actually ran."""

    def test_a_successful_fit_is_run_once_and_claimed(self, wire, viewport):
        # isFitView/isSmoothTransition are CONSUMED on assignment (2705.1.25) - the gate is
        # apply_camera not raising, never a flag read back off the assigned camera.
        wire(setup=FakeSetup(name="Op10"))
        vp = viewport()
        out = payload(mod.activate_setup_handler(setup="Op10"))
        assert vp._fit_calls == 0
        assert vp._assigned                    # the assignment reached the viewport
        assert out["view_fit"] is True and "view fit" in out["note"]
        assert "fit_error" not in out

    def test_a_fit_that_raises_is_reported_and_the_activation_still_stands(self, wire, viewport):
        wire(setup=FakeSetup(name="Op10"))
        viewport(raises="no graphics context")
        out = payload(mod.activate_setup_handler(setup="Op10"))
        assert out["activated"] == "Op10"          # the setup IS active - only the fit failed
        assert out["view_fit"] is False
        assert "no graphics context" in out["fit_error"]
        assert "did not complete" in out["note"] and "and view fit" not in out["note"]

    def test_no_active_viewport_is_reported_not_claimed(self, wire, viewport):
        wire(setup=FakeSetup(name="Op10"))
        viewport(absent=True)
        out = payload(mod.activate_setup_handler(setup="Op10"))
        assert out["view_fit"] is False and "no active viewport" in out["fit_error"]

    def test_the_shared_viewport_fake_satisfies_the_gate(self, wire, monkeypatch):
        # conftest's Viewport is the fake other suites hand this handler; its assignment does not
        # raise, so the gate reports success without reading isFitView back.
        wire(setup=FakeSetup(name="Op10"))
        vp = Viewport()
        monkeypatch.setattr(mod, "app", Mock(activeViewport=vp))
        out = payload(mod.activate_setup_handler(setup="Op10"))
        assert vp._fit_calls == 0
        assert out["view_fit"] is True and "fit_error" not in out
