# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
"""Event handler wiring that keeps each handler referenced while its event can fire."""

import sys
from typing import Callable, Optional

import adsk.core

from .general_utils import handle_error

__all__ = ['add_handler', 'clear_handlers']

_kept: list = []


def _handler_base(event: adsk.core.Event) -> type:
    """Return the handler class that event.add() declares, resolved in the event's own module."""
    declared = event.add.__annotations__['handler']
    if isinstance(declared, str):
        return getattr(sys.modules[event.__module__], declared)
    return declared


def add_handler(event: adsk.core.Event, callback: Callable, *, name: Optional[str] = None,
                local_handlers: Optional[list] = None):
    """Subscribe callback to event and return the handler, kept in local_handlers or the module list."""
    base = _handler_base(event)
    label = name or base.__name__

    class _Relay(base):
        def notify(self, args):
            try:
                callback(args)
            except BaseException:
                handle_error(label)

    relay = _Relay()
    (_kept if local_handlers is None else local_handlers).append(relay)
    event.add(relay)
    return relay


def clear_handlers():
    """Drop every handler that add_handler kept in the module list."""
    _kept.clear()
