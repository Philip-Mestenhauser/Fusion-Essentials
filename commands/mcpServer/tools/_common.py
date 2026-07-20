# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The response/resolve substrate every tool module imports: ``ok``/``error``/``safe``, the active
``design()``/``target_component()`` resolvers, ``resolve_sketch``, and cm-based unit ``scale``/
``UNIT_TO_CM``. See ``tools/CLAUDE.md`` for the full helper map.

Tests patch the seam on this module (``monkeypatch.setattr(mod._common, "design", ...)``); a tool that
also resolves through ``_inputs`` needs that seam patched too (``tests/CLAUDE.md`` "the dual-seam
trap")."""

import json

import adsk.core
import adsk.fusion

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = "ok/error/safe, design/target_component, resolve_sketch, scale, timeline_health (the shared before/after edit guard) - the response+resolve substrate"

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
    is a property of the DESIGN (Component has no such attribute - reading it there silently degrades
    this walk to root-only); it includes the root. Falls back to just the root when the collection is
    unavailable. The basis for a design-wide by-name lookup that does NOT assume the root component."""
    root = safe(lambda: d.rootComponent)
    if root is None:
        return []
    comps = safe(lambda: d.allComponents)
    if comps is None:
        return [root]
    n = safe(lambda: comps.count, 0)
    out = [safe(lambda i=i: comps.item(i)) for i in range(n)]
    out = [c for c in out if c is not None]
    return out or [root]


def design_wide_counts(d):
    """(bodies, sketches) summed across every component (root + sub-components) via ``all_components``.
    ``bRepBodies``/``sketches`` are per-COMPONENT collections: reading them off the root alone reports
    only root-component geometry, so a design whose bodies/sketches live in sub-components (the normal
    multi-part workflow) under-reports - a sketch-only-in-sub-components doc would read sketches:0. The
    ONE design-wide body/sketch count every summary read shares (workspace_orient, design_get) so they
    agree on one design instead of one counting root-only and the other walking every component."""
    bodies = sketches = 0
    for comp in all_components(d):
        bodies += safe(lambda c=comp: c.bRepBodies.count, 0) or 0
        sketches += safe(lambda c=comp: c.sketches.count, 0) or 0
    return bodies, sketches


def result_bodies(feature):
    """The bodies a parametric feature produced, as a list of FRESH body references read straight off
    the feature - the input references a caller passed in can go invalid once the feature rebuilds, so
    read the result set back here. None-filtered; empty when the feature (or its bodies) is unreadable
    or the feature is None. Callers project what they need per body (name / isSolid / area / faces /
    entityToken)."""
    fb = safe(lambda: feature.bodies)
    n = int(safe(lambda: fb.count, 0) or 0) if fb else 0
    out = []
    for i in range(n):
        b = safe(lambda i=i: fb.item(i))
        if b is not None:
            out.append(b)
    return out


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


def timeline_marker(design):
    """(marker_position, count) for the parametric timeline, or (None, None) for a direct-modelling
    design. marker_position < count means the features AFTER the marker are ROLLED BACK - NOT in the
    current model (they revert to home) - the state an in-place edit that failed to restore the marker
    leaves behind, which a health read must surface rather than report a rolled-back model as healthy."""
    tl = safe(lambda: design.timeline)
    if tl is None:
        return None, None
    return safe(lambda: tl.markerPosition), (safe(lambda: tl.count, 0) or 0)


def timeline_health(design):
    """(error_names, warning_names, total) over the parametric timeline by healthState (2=error,
    1=warning) - the shared before/after guard for edits that can break downstream features, so a
    change that corrupts the model is reported instead of swallowed. A direct-modelling design
    (no timeline) yields empty lists."""
    errors, warnings, total = [], [], 0
    tl = safe(lambda: design.timeline)
    if tl is None:
        return errors, warnings, total
    for i in range(safe(lambda: tl.count, 0) or 0):
        it = tl.item(i)
        total += 1
        hs = safe(lambda it=it: it.healthState)
        if hs == 2:
            errors.append(safe(lambda it=it: it.name) or f"#{i}")
        elif hs == 1:
            warnings.append(safe(lambda it=it: it.name) or f"#{i}")
    return errors, warnings, total


# ── unit scaling (Fusion's internal length unit is cm) ──────────────────────

UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
CM_TO_UNIT = {u: 1.0 / f for u, f in UNIT_TO_CM.items()}


def scale(units: str):
    """cm-per-unit factor for ``units`` (mm/cm/in), or None if the unit is unknown."""
    return UNIT_TO_CM.get((units or "mm").strip().lower())


def ptxyz(p, f):
    """{x, y, z} for a Point3D ``p``, each scaled by ``f`` and rounded to 6 decimals; None if ``p``
    is None."""
    if p is None:
        return None
    return {"x": round(safe(lambda: p.x, 0.0) * f, 6),
            "y": round(safe(lambda: p.y, 0.0) * f, 6),
            "z": round(safe(lambda: p.z, 0.0) * f, 6)}


# ── measurement (the one measureMinimumDistance core both measure tools share) ────────────────────

def min_distance(entity_a, entity_b):
    """The minimum distance between two entities via ``app.measureManager.measureMinimumDistance``,
    with the shared failure handling ``model_measure_between`` and ``model_measure_relation`` both need.
    Returns ``(MeasureResults, None)`` on success or ``(None, error_result)`` on any failure - the
    measurement is a READ, so a failure is surfaced, never swallowed. The result's ``.value`` is in cm;
    ``.positionOne``/``.positionTwo`` are the closest points (cm)."""
    mgr = safe(lambda: app.measureManager)
    if not mgr:
        return None, error("MeasureManager unavailable.")
    try:
        mr = mgr.measureMinimumDistance(entity_a, entity_b)
    except Exception as e:
        return None, error(f"Distance measurement failed: {e}. (Target a specific body/face - an "
                           "occurrence whose bodies are proxies can be rejected; a find_geometry "
                           "face/body handle is the precise input.)")
    if not mr:
        return None, error("measureMinimumDistance returned nothing for these two targets.")
    return mr, None


# ── sketch entity / feature-operation resolution ────────────────────────────

def target_sketch(comp, name):
    """The sketch named ``name`` in ``comp``, or its most recently created sketch when ``name`` is
    empty. Returns (sketch-or-None, the requested name)."""
    coll = safe(lambda: comp.sketches)
    nm = (name or "").strip()
    if coll is None:
        return None, nm
    if nm:
        return safe(lambda: coll.itemByName(nm)), nm
    n = safe(lambda: coll.count, 0)
    return (coll.item(n - 1) if n else None), nm


def resolve_entity_ref(sketch, ref):
    """A sketch entity from a '<type>:<index>' ref (type = line/arc/circle/point), indexing that
    curve/point collection in creation order. Returns the entity, or None."""
    s = (ref or "").strip().lower()
    if ":" not in s:
        return None
    kind, _, idx = s.rpartition(":")
    try:
        i = int(idx)
    except Exception:
        return None
    curves = safe(lambda: sketch.sketchCurves)
    coll = None
    if kind == "line":
        coll = safe(lambda: curves.sketchLines)
    elif kind == "arc":
        coll = safe(lambda: curves.sketchArcs)
    elif kind == "circle":
        coll = safe(lambda: curves.sketchCircles)
    elif kind == "point":
        coll = safe(lambda: sketch.sketchPoints)
    if coll is None:
        return None
    if i < 0 or i >= safe(lambda: coll.count, 0):
        return None
    return safe(lambda: coll.item(i))


# Operation name -> adsk.fusion.FeatureOperations attribute (extrude/revolve/sweep/loft-style features).
OPERATIONS = {
    "new": "NewBodyFeatureOperation",
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
    "new_component": "NewComponentFeatureOperation",
}

