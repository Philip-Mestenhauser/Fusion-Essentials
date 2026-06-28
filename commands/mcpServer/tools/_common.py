# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared helpers for MCP tool modules — the common substrate every tool builds on.

These were copy-pasted into 30+ tool files (``_ok`` / ``_error`` / ``_safe`` / ``_design`` /
unit scaling / occurrence + snap resolution). Centralising them here enforces ONE response shape,
ONE error contract, and ONE unit convention by import rather than by discipline, so a new
community tool that does ``from ._common import ok, error, safe, design`` automatically holds the
bar. Tool files keep their own domain logic; only this boilerplate lives here.

Import style (in a tool module):
    from ._common import ok, error, safe, design, target_component, scale, UNIT_TO_CM

Names are exported WITHOUT a leading underscore (they're the public helper API of this package);
the historical ``_ok`` / ``_error`` spellings are kept as aliases so existing modules migrate by
changing only their import line, not every call site.
"""

import json

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()


# ── response builders (the MCP tool-result contract) ────────────────────────

def ok(payload: dict) -> dict:
    """A successful tool result: JSON-encodes ``payload`` as the text content."""
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def error(text: str) -> dict:
    """A failed tool result. ``message`` mirrors the text so callers can read either field."""
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


# ── safe getter ─────────────────────────────────────────────────────────────

def safe(getter, default=None):
    """Call ``getter()`` and swallow any exception, returning ``default``. Lets a tool probe the
    Fusion object model (where a missing property raises) without try/except at every access."""
    try:
        return getter()
    except Exception:
        return default


# ── design / component resolution ───────────────────────────────────────────

def design():
    """The active Design, or None. Falls back from ``activeProduct`` to the active document's
    DesignProductType so it works even when the active product is e.g. a CAM product."""
    d = adsk.fusion.Design.cast(app.activeProduct)
    if not d:
        d = safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return d


def target_component(d):
    """The component new geometry should be built into: the ACTIVE edit target
    (``design.activeComponent``), falling back to the root component when none is set. So
    model_create_component(activate=true) actually receives the sketch/body; behaviour is unchanged when
    nothing is activated (activeComponent == root)."""
    comp = safe(lambda: d.activeComponent)
    return comp if comp is not None else d.rootComponent


# ── unit scaling (Fusion's internal length unit is cm) ──────────────────────

UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}


def scale(units: str):
    """cm-per-unit factor for ``units`` (mm/cm/in), or None if the unit is unknown."""
    return UNIT_TO_CM.get((units or "mm").strip().lower())


# Underscore aliases — let modules migrate by changing only the import line.
_ok = ok
_error = error
_safe = safe
_design = design
_target_component = target_component
_scale = scale
_UNIT_TO_CM = UNIT_TO_CM
