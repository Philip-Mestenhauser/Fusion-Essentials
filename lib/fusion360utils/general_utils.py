# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
"""Add-in logging and error reporting through Fusion's own log channels."""

import traceback

import adsk.core

__all__ = ['log', 'handle_error']

try:
    from ... import config as _config
    _ECHO_ALL = bool(getattr(_config, 'DEBUG', False))
except Exception:
    _ECHO_ALL = False


def log(message: str, level: adsk.core.LogLevels = adsk.core.LogLevels.InfoLogLevel,
        force_console: bool = False):
    """Print message; file errors in Fusion's log; echo to the text palette when forced or in DEBUG."""
    print(message)
    app = adsk.core.Application.get()
    if level == adsk.core.LogLevels.ErrorLogLevel:
        app.log(message, level, adsk.core.LogTypes.FileLogType)
    if force_console or _ECHO_ALL:
        app.log(message, level, adsk.core.LogTypes.ConsoleLogType)


def handle_error(name: str, show_message_box: bool = False):
    """Log the exception being handled, labelled by name, and optionally show it in a message box."""
    report = f'{name}\n{traceback.format_exc()}'
    log(f'Error in {name}', adsk.core.LogLevels.ErrorLogLevel)
    log(report, adsk.core.LogLevels.ErrorLogLevel)
    if show_message_box:
        adsk.core.Application.get().userInterface.messageBox(report)
