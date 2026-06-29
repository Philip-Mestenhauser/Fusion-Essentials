# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP tools package — one tool per ``<domain_verb>.py`` module.

Registration is AUTO-DISCOVERED: ``entry.py::_collect_items()`` sweeps this package with
``pkgutil.iter_modules`` and calls each module's ``register_tool()``. So adding a tool is just dropping
a new ``<name>.py`` with a ``register_tool()`` into this directory — no import list to maintain here and
no parallel ``register_tool()`` call to add in entry.py (the two registries that used to drift).

Conventions the sweep relies on:
  * ``_``-prefixed modules (``_common``, ``_inputs``, ``_outputs``, ``_data_common``) are shared
    helpers, never tools — they are skipped.
  * The GATED ``sys_execute_script`` is skipped by the sweep and registered explicitly in entry.py only
    when the user opts in.
  * ``sys_reload_addin`` registers via the sweep AND has its reload custom event installed explicitly in
    entry.py (a side-effect beyond registration).
"""
