# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_activate_setup: the name guard, miss-lists-available, and the activation read-back
(an activate() the platform accepts but that does not take must surface as an error)."""

import pytest

from conftest import load_tool, payload, error_message

mod = load_tool("cam_activate_setup")


class FakeSetup:
    def __init__(self, name="Setup1", activates=True):
        self.name = name
        self.isActive = False
        self.activate_calls = 0
        self._activates = activates

    def activate(self):
        self.activate_calls += 1
        if self._activates:
            self.isActive = True


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
    wire(setup=None, available=["Dup", "Dup"],
         refusal="'Dup' is ambiguous - 2 CAM items share that name: Dup, Dup. Rename the target "
                 "so its name is unique, then retry.")
    msg = error_message(mod.activate_setup_handler(setup="Dup"))
    assert "is ambiguous" in msg
    assert "not found" not in msg.lower()


def test_activates_and_reports_the_setup_name(wire):
    setup = wire(setup=FakeSetup(name="Op10"))
    out = payload(mod.activate_setup_handler(setup="Op10"))
    assert setup.activate_calls == 1
    assert out["activated"] == "Op10"


def test_activate_that_does_not_take_is_an_error(wire):
    wire(setup=FakeSetup(name="Op10", activates=False))
    assert "isActive=false" in error_message(mod.activate_setup_handler(setup="Op10"))


def test_activate_raising_is_surfaced_not_swallowed(wire):
    setup = wire(setup=FakeSetup(name="Op10"))

    def boom():
        raise RuntimeError("kaput")

    setup.activate = boom
    msg = error_message(mod.activate_setup_handler(setup="Op10"))
    assert "Failed to activate 'Op10'" in msg
    assert "kaput" in msg
