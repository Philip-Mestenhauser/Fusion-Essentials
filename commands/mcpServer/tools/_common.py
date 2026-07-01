# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared helpers for MCP tool modules - the common substrate every tool builds on.

The response builders / safe getter / unit scaling / component resolution live here so every tool
holds ONE response shape, ONE error contract, and ONE unit convention by import rather than by
discipline. A new tool that does ``from ._common import ok, error, safe`` automatically holds the bar;
tool files keep their own domain logic, only this boilerplate lives here.

Import style (in a tool module):
    from ._common import ok, error, safe, target_component, scale, UNIT_TO_CM

Names are the package's public helper API - a single spelling, no leading underscore. The active-design
resolver ``design()`` lives here too: tool modules call ``_common.design()`` (via ``from . import
_common``) rather than copy a local ``_design()``. Tests patch the seam on the substrate module
(``monkeypatch.setattr(mod._common, "design", ...)``) so the handler resolves through the one shared
object.
"""

import json

import adsk.core
import adsk.fusion

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = "ok/error/safe, design/target_component, resolve_sketch, scale - the response+resolve substrate"

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


def root_body_advisory(d, comp):
    """A note (or '') for a build tool to append when it just built into ROOT with no component active.

    Best practice is one component per part - and it is not just tidiness: promoting a root body into a
    component LATER re-serializes the body in the internal data model (every entity handle/token on it is
    re-minted, invalidating handles you hold) and clutters the root timeline. Modelling the FIRST body
    straight into a component avoids that. This fires only when it is still cheap to switch (root has <=1
    solid body and no sub-components), so it advises at the point of the decision, not as nagging."""
    if comp is None or comp is not safe(lambda: d.rootComponent):
        return ""                                  # a component IS active - the good path, say nothing
    body_n = safe(lambda: comp.bRepBodies.count, 0) or 0
    occ_n = safe(lambda: d.rootComponent.occurrences.count, 0) or 0
    if body_n > 1 or occ_n > 0:
        return ""                                  # past the early window - advising now would just nag
    return ("Built into the ROOT component (no component was active). Best practice is one component per "
            "part: create it with model_create_component(activate=true) FIRST, then build. Promoting a "
            "root body into a component later re-serializes it (invalidating held handles) and clutters "
            "the root timeline - cheap to switch now, costly later.")


def all_components(d):
    """Every component in the design (root + all sub-components), as a flat list. ``allComponents``
    includes the root; falls back to just the root when the collection is unavailable. The basis for
    a design-wide by-name lookup that does NOT assume the root component."""
    root = safe(lambda: d.rootComponent)
    if root is None:
        return []
    comps = safe(lambda: root.allComponents)
    if comps is None:
        return [root]
    n = safe(lambda: comps.count, 0)
    out = [safe(lambda i=i: comps.item(i)) for i in range(n)]
    out = [c for c in out if c is not None]
    return out or [root]


def resolve_sketch(d, name):
    """Resolve a sketch BY NAME across the whole design - the ONE true resolver every by-name sketch
    tool should use. Search order: the ACTIVE edit component first (where model_create_component(
    activate=true) + sketch_create just put it - the common assembly case), then the root component,
    then every other component. Returns the live Sketch or None.

    A plain ``design.rootComponent.sketches.itemByName`` only finds sketches in the ROOT component, so
    a sketch drawn in an activated SUB-component (the normal multi-part workflow) would be invisible to
    a by-name sketch op even though model_extrude/revolve (which use target_component) can see it. This
    one resolver searches the active component first, so every by-name sketch op stays consistent.
    """
    nm = (name or "").strip()
    if not nm:
        return None
    # Active component first, then root, then the rest - de-duplicated, order-preserving.
    ordered = []
    for c in [target_component(d), safe(lambda: d.rootComponent)] + all_components(d):
        if c is not None and c not in ordered:
            ordered.append(c)
    for comp in ordered:
        sk = safe(lambda c=comp: c.sketches.itemByName(nm))
        if sk:
            return sk
    return None


def all_sketch_names(d):
    """Every sketch name across the design (all components), for 'Available: ...' error messages -
    so a not-found message lists sketches wherever they live, not just in the root component."""
    names = []
    for comp in all_components(d):
        coll = safe(lambda c=comp: c.sketches)
        for i in range(safe(lambda: coll.count, 0) if coll else 0):
            nm = safe(lambda i=i, cl=coll: cl.item(i).name)
            if nm:
                names.append(nm)
    return names


# ── terse: drop default-valued fields from a repeated record ────────────────

def terse(rec: dict, noise: dict) -> dict:
    """A copy of ``rec`` with any key whose value equals its default in ``noise`` removed.

    Use this to keep a list of similar records readable: when a record is in its normal state, its
    routine fields are dropped so it shows only what identifies it; when a record is unusual, the
    field that differs from the default stays and stands out. The surrounding payload still carries
    the counts, and a dropped field simply means "this record has the default value". ``noise`` maps
    each droppable key to its default (e.g. {"is_suppressed": False, "health": "healthy"}).

    Example - a healthy CAM operation row keeps just {name, tool, strategy, state}; a suppressed or
    errored one additionally shows is_suppressed=True / has_error=True, so the problem rows are easy
    to spot in an otherwise-uniform list."""
    return {k: v for k, v in rec.items() if not (k in noise and v == noise[k])}


# ── unit scaling (Fusion's internal length unit is cm) ──────────────────────

UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}


def scale(units: str):
    """cm-per-unit factor for ``units`` (mm/cm/in), or None if the unit is unknown."""
    return UNIT_TO_CM.get((units or "mm").strip().lower())


