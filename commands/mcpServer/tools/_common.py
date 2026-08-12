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
MAP_BLURB = "ok/error/safe, measured (a scaled number or None - the honest counterpart to safe(read, 0.0) for anything a caller treats as a MEASUREMENT, where 0 is an answer) + read_flag (the same honesty for a BOOLEAN: True/False/None, never a coerced False - the ONE unreadable-flag read every set-then-read-back gate and every published flag goes through) + counted (the same honesty for an INTEGER COUNT: the int or None, never a coerced 0/1 - for a count an absent read does NOT make zero, like a body's lumps or a built path's entities; safe(read, 0) stays right for a TALLY over a collection that may be absent), design/target_component, resolve_sketch + resolve_or_recent_sketch (the name-or-most-recent sketch contract), resolve_entity_ref + resolve_entity_refs (the ONE '<type>:<index>' sketch-entity resolver and the comma-separated list parser over it), SKETCH_ANCHORS + parse_anchor_ref + anchor_point (the ONE entity-anchored position grammar - a ref's optional third segment ':start/:end/:mid/:center' naming WHICH point of the entity is meant, and the resolve to that SketchPoint; sketch_dimension and sketch_constrain read the same forms through it, and 'mid' CREATES a midpoint-constrained point where the others only read one), most_recent_body + resolve_body_or_recent (the ONE 'that body, or the most recent one' resolution every whole-body edit runs: a given handle/name goes through the caller's own BodyRef, empty falls back to most_recent_body, and the caller words the no-body error), NO_VOLUME_CHANGE_CM3 (the ONE band a before/after volume difference counts as no change at all - every material-changing feature judges its silent no-op against it), result_bodies + body_facts (the feature-result walk and the per-body {name, is_solid} projection it is published with), open_profile_from_sketch, scale, timeline_health (the shared before/after edit guard), set_verified (the set-then-read-back every FeatureInput property assignment needs - a SWIG proxy accepts an unknown name silently), apply_rename (the ONE create-flow rename-with-disclosure: sets entity.name, reads it back, returns (final_name, warning-or-None) - a declined or deduped rename is DISCLOSED in the payload, never swallowed and never an error on a create that succeeded), cancel_input (the ONE abort for a partial-computing createInput transaction - trim/boundary fill - that reports a refused cancel instead of swallowing it), direct_feature_absence + no_feature_error + failed_effect_remedy + DIRECT_FEATURE_NOTE (the one mode gate for a Features.*.add() that returns nothing: measured per-class in DIRECT designs while the edit LANDS, so a site with a feature-independent effect check falls through to it, a site without one refuses honestly, and a wrong-effect error ends with the remedy that actually exists in that mode) + null_feature_note (the ONE sentence a payload appends for a null feature - DIRECT mode, or the base-feature edit scope that suppressed it - so no site re-rolls the branch or infers a design mode from the missing object), census_host + body_count (the resolve-the-collection-ONCE-before-the-mutation body census a feature-free effect check counts on - measured: the pieces land in the TARGET's parentComponent, not the active component), same_component (the ONE same-component test - component wrappers are measured NEVER identity-stable, so `a is b` between two component references is always False and must never carry the comparison), iter_collection (the ONE count/item(i) walk over a Fusion collection - every present item, empty when the collection is absent), native_token (the ONE physical-body identity read - (nativeObject or self).entityToken, safe at both steps - the key that collapses a native body and its occurrence proxies to one body; a local safe(lambda: b.entityToken) re-roll is how a de-dup counts one body twice), all_occurrences + occurrence_paths + component_contains (the ONE assembly-context occurrence walk - root.allOccurrences, the only source of true fullPathNames - plus the path census a structural edit diffs to read its effect back, and the cycle test a re-parent/instance refuses on), build_path (the ONE feature-path resolver every sweep/pipe/path-pattern/on-path datum builds its adsk.fusion.Path with: 'sketch:<name>' chains a path sketch's curves, ONE find_geometry edge handle chains from that seed across TANGENT connections - a sharp corner stops the chain, so the built Path's count is the truth - and a JSON list of edge handles is used exactly and must connect) - the response+resolve substrate"

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


_UNREADABLE = object()      # a getter that RAISED - distinct from one that returned None


def read_flag(getter):
    """A BOOLEAN flag read: True, False, or None when the getter raised or the flag itself read None.

    The counterpart to ``measured`` for a flag. ``safe(read, False)`` turns an unreadable getter into a
    confident False, and False is an answer ("suppressed: no", "analysis: off"); worse, at a
    set-then-read-back site it lets a swallowed write pass a ``now != wanted`` gate whenever the wanted
    value is False and publishes that as a confirmed state. None says the flag is unknown, which is the
    only thing an unreadable getter supports."""
    v = safe(getter, _UNREADABLE)
    return None if (v is _UNREADABLE or v is None) else bool(v)


def measured(getter, scale=1.0, places=6):
    """A measured number, scaled and rounded - or None when it cannot be read.

    The counterpart to safe() for anything a caller will treat as a MEASUREMENT. safe(read, 0.0)
    turns an unreadable property into a confident zero, and zero is an answer: "no gap", "no mass",
    "parallel". None says the value is unknown, which is the only honest thing an unreadable
    property can say. safe(read, 0) stays right for a TALLY over a collection that may be ABSENT -
    absent really does hold none; a COUNT whose unreadability is not a zero goes through counted()."""
    v = safe(getter)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return round(v * scale, places)


def counted(getter):
    """An integer COUNT read: the int, or None when it cannot be read.

    The integer sibling of ``measured`` (a number) and ``read_flag`` (a flag), and deliberately NOT
    ``safe(read, 0)``: that default is right for a TALLY over a collection that may be ABSENT, where
    absent really does hold none. This is for a count where an unreadable read is neither 0 nor 1 -
    a body's lumps, a built path's entities - and where the caller goes on to compare the number. A
    bool and a non-int both read as unknown: an adsk mock (and an unmodeled live property) hands back
    a truthy child object, and letting one through turns `n > 1` into a TypeError or worse a
    comparison against something that is not a count at all."""
    n = safe(getter)
    return int(n) if isinstance(n, int) and not isinstance(n, bool) else None


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


def same_component(a, b) -> bool:
    """True when `a` and `b` denote the SAME component.

    MEASURED: component identity is NEVER stable. Two reads of design.rootComponent return DIFFERENT
    Python objects (`c1 is c2` is False), and so do rootComponent vs body.parentComponent vs
    edge.body.parentComponent - all four sharing ONE entityToken. So `component_a is component_b` is
    effectively ALWAYS FALSE and cannot carry a same-component test: written that way it silently
    takes the "different component" branch every time.

    Compares by entityToken (exact - every wrapper of one component reports the same one), falling
    back to name (the hedge _joints.jo_assembly_proxy and assembly_get already use) when a token
    cannot be read, and keeping the identity check only as a free short-circuit."""
    if a is None or b is None:
        return False
    if a is b:
        return True
    ta, tb = safe(lambda: a.entityToken), safe(lambda: b.entityToken)
    if ta and tb:
        return ta == tb
    na = safe(lambda: a.name)
    return bool(na) and na == safe(lambda: b.name)


def native_token(entity):
    """The entityToken of the entity a wrapper STANDS FOR - ``(nativeObject or self).entityToken`` -
    or None when neither reads. One value per PHYSICAL entity, which the wrapper's own token is not.

    MEASURED: a body and its occurrence PROXY carry DIFFERENT entityTokens (each stable across
    re-fetches of that wrapper), while ``nativeObject`` reads None on a native and hands back the
    native on a proxy. So a comparison keyed on the wrapper's own token sees ONE body as two: a de-dup
    counts it twice, and a same-body guard never fires on a native-vs-proxy pair of the same body.
    Read this wherever two body references are compared or de-duplicated. It is NOT the key for a
    HANDLE a tool publishes - a handle is the wrapper's own token, and resolving it back to a
    context-carrying proxy is the point.

    ``nativeObject`` is read through ``safe``: a wrapper kind that does not answer it at all still has
    its own token to key on."""
    native = safe(lambda: entity.nativeObject) or entity
    return safe(lambda: native.entityToken)


def root_body_advisory(d, comp):
    """A note (or '') for a build tool to append when it just built into ROOT with no component active.

    Best practice is one component per part - and it is not just tidiness: promoting a root body into a
    component LATER re-serializes the body in the internal data model (every entity handle/token on it is
    re-minted, invalidating handles you hold) and clutters the root timeline. Modelling the FIRST body
    straight into a component avoids that. This fires only when it is still cheap to switch (root has <=1
    solid body and no sub-components), so it advises at the point of the decision, not as nagging."""
    # same_component, not `is`: component wrappers are never identity-stable, so `comp is not
    # d.rootComponent` reads True even AT the root and this advisory would never fire at all.
    if comp is None or not same_component(comp, safe(lambda: d.rootComponent)):
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


def all_occurrences(d, cap=None):
    """Every occurrence in the design, in ASSEMBLY context, as a flat list. ``allOccurrences`` is a
    property of the ROOT COMPONENT and is the only walk that reports true assembly fullPathNames: an
    occurrence handle taken from ``<component>.occurrences`` reads a COMPONENT-LOCAL path (measured).
    The occurrence counterpart of ``all_components`` - the basis of the by-name resolver
    (``_inputs._resolve_occurrence``) and of every before/after assembly census. ``cap`` bounds the
    list for a caller that only renders/labels (a view tool never needs an unbounded walk)."""
    root = safe(lambda: d.rootComponent) if d else None
    occs = list(safe(lambda: root.allOccurrences) or []) if root is not None else []
    return occs[:cap] if cap is not None else occs


def occurrence_paths(d):
    """Every occurrence fullPathName in the design, as a set - the before/after census a structural
    edit (instance, re-parent) diffs to read back WHICH paths it actually added."""
    return {(safe(lambda o=o: o.fullPathName) or "") for o in all_occurrences(d)}


def component_contains(outer, inner):
    """True when component `inner` IS `outer` or sits anywhere inside it - the cycle test a structural
    edit refuses on (a component cannot hold an instance of itself). Walks ``Component.allOccurrences``
    (the component's own subtree) and compares through ``same_component``, since component wrappers are
    never identity-stable."""
    if same_component(outer, inner):
        return True
    inside = safe(lambda: outer.allOccurrences) if outer is not None else None
    return any(same_component(safe(lambda o=o: o.component), inner) for o in list(inside or []))


def all_meshes(d):
    """Every MeshBody in the design, as (component, mesh) pairs - walks EVERY component's meshBodies
    (root + every sub-component, via ``all_components``), regardless of which component is ACTIVE.

    Reaching meshes through COMPONENTS rather than occurrences is what makes the walk design-wide
    without depending on the assembly structure: a component with no occurrence anywhere is still
    walked. This is the ONE design-wide mesh traversal both mesh name-resolution
    (``_inputs.MeshBodyRef``) and a post-delete survivor check (``mesh_delete``) build their
    name-match leaf op on top of, so a mesh in a child component resolves the same way it
    survivor-checks."""
    out = []
    for comp in all_components(d):
        for m in iter_collection(safe(lambda c=comp: c.meshBodies)):
            out.append((comp, m))
    return out


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


def body_facts(bodies):
    """[{name, is_solid}] read LIVE per body - the per-body projection a feature result reports its
    bodies with. Kept beside result_bodies because the two are always used together: the walk gets
    the fresh references, this reads what a caller publishes about each one. A caller that needs a
    single verdict takes all()/any() over is_solid rather than collapsing the list here, so a MIXED
    result can still name the body that is not solid."""
    return [{"name": safe(lambda b=b: b.name), "is_solid": bool(safe(lambda b=b: b.isSolid))}
            for b in bodies]


def most_recent_body(comp):
    """The most recently created body in a component - the blank-input fallback for a tool that
    defaults to 'the body you just made'. Returns the body or None (the caller words its own hint)."""
    bodies = safe(lambda: comp.bRepBodies)
    n = safe(lambda: bodies.count, 0) if bodies else 0
    return bodies.item(n - 1) if n else None


def resolve_body_or_recent(body_ref, comp, raw, no_body_error):
    """(body, error) for a 'that body, or the most recent one' input - the ONE resolution every
    whole-body edit (shell, fillet/chamfer) runs.

    A GIVEN value - a find_geometry handle OR a name - resolves through the caller's own BodyRef
    kind, which type-checks it and refuses an ambiguous name; empty falls back to ``most_recent_body``
    in ``comp``. ``no_body_error`` is the sentence for a component holding no body at all, because
    each tool points at its OWN alternative input there ('edges', 'remove_faces')."""
    if raw in (None, "", []):
        body = most_recent_body(comp)
        return (body, None) if body else (None, no_body_error)
    return body_ref.resolve(raw)


def open_profile_from_sketch(comp, sketch, verb, no_curves_error=None):
    """Build an OPEN profile from a sketch's unclosed curves via Component.createOpenProfile
    (chainCurves=True follows the open chain), so an open path (a line/arc/spline) can become a
    SURFACE. Returns (open_profile, error). 'verb' finishes the failure sentence ("for a surface
    extrude", "from the sketch"); 'no_curves_error' is the caller's empty-sketch hint.
    createOpenProfile wants an ObjectCollection of the individual curve entities, NOT the
    SketchCurves collection object (passing that raises "invalid input curves")."""
    curves = safe(lambda: sketch.sketchCurves)
    n = safe(lambda: curves.count, 0) if curves is not None else 0
    if not n:
        return None, (no_curves_error or "Sketch has no curves to build an open profile from.")
    coll = adsk.core.ObjectCollection.create()
    for i in range(n):
        c = safe(lambda i=i: curves.item(i))
        if c is not None:
            coll.add(c)
    try:
        prof = comp.createOpenProfile(coll, True)
    except Exception as e:
        return None, f"Could not build an open profile {verb}: {e}"
    if not prof:
        return None, "Could not build an open profile from the sketch's curves (createOpenProfile returned nothing)."
    return prof, None


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
    # Active component first, then root, then the rest - de-duplicated by entityToken (component
    # wrappers are NEVER identity-stable - see same_component - so `c not in ordered` can never
    # dedupe; a component whose token will not read is walked again, which only costs a re-read).
    ordered, seen = [], set()
    for c in [target_component(d), safe(lambda: d.rootComponent)] + all_components(d):
        if c is None:
            continue
        tok = safe(lambda c=c: c.entityToken)
        if tok is not None and tok in seen:
            continue
        if tok is not None:
            seen.add(tok)
        ordered.append(c)
    for comp in ordered:
        sk = safe(lambda c=comp: c.sketches.itemByName(nm))
        if sk:
            return sk
    return None


def resolve_or_recent_sketch(d, name):
    """The ONE name-or-default sketch contract: a NAME resolves DESIGN-WIDE via ``resolve_sketch``
    (active component first, then root, then the rest - a root master sketch stays reachable from an
    activated sub-component); an EMPTY name means the most recently created sketch in the ACTIVE
    component. Returns (sketch-or-None, the stripped requested name or None when blank) - the caller
    words its own not-found error off the requested name."""
    nm = (name or "").strip()
    if nm:
        return resolve_sketch(d, nm), nm
    coll = safe(lambda: target_component(d).sketches)
    n = safe(lambda: coll.count, 0) if coll is not None else 0
    return (coll.item(n - 1) if n else None), None


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


def set_verified(obj, prop, value, label, owner_name):
    """Set `prop` on a FeatureInput and CONFIRM it took. Returns an error string, or '' on success.

    LIVE-VERIFIED: a SWIG proxy ACCEPTS an assignment to a name it does not define - the value lands
    on a dead Python attribute while the object silently keeps its API default - so a misspelled
    property or a fabricated enum member CANNOT raise, and neither a bare assignment nor a
    try/except catches it. Reading the value back is the only thing that does.

    `value` None means the enum member/value was unavailable, which is reported rather than set."""
    if value is None:
        return f"'{label}' is not available on this Fusion version."
    try:
        setattr(obj, prop, value)
    except Exception as e:
        return f"Could not set {label}: {e}"
    if safe(lambda: getattr(obj, prop)) != value:
        return (f"Setting {label} did not take - {owner_name}.{prop} reads back unchanged, so the "
                "operation would run on its default settings.")
    return ""


def apply_rename(entity, new_name):
    """Apply a requested rename to a just-CREATED entity and read it back - the ONE
    rename-with-disclosure every create-flow 'name' option runs through. Returns
    (final_name, warning_or_None).

    The create itself succeeded, so a declined rename is a DISCLOSURE, never an error (contrast
    design_set_name, the dedicated rename tool, where a miss IS the failure). The platform can
    decline silently (a swallowed setattr) or land a DEDUPED variant ('Foo' -> 'Foo(1)'), so the
    read-back is what the payload publishes, and the warning names what the entity actually holds.
    An empty/omitted request renames nothing and warns nothing."""
    want = (new_name or "").strip()
    if not want:
        return safe(lambda: entity.name), None
    try:
        entity.name = want
    except Exception as e:
        got = safe(lambda: entity.name)
        return got, f"created, but the rename to '{want}' failed: {e} (the name is '{got}')."
    got = safe(lambda: entity.name)
    if got != want:
        return got, f"created, but the requested name '{want}' did not take - it is named '{got}'."
    return got, None


def cancel_input(inp, what):
    """Abort an OPEN feature-input transaction, reporting a refusal to cancel.

    A createInput that partial-computes (trimFeatures, boundaryFillFeatures) STARTS a feature
    transaction that only add() or cancel() ends; the API's own doc says leaving it open "leaves
    Fusion in a bad state and there will be undo problems and possibly a crash". cancel() answers
    whether the abort took: a false leaves Fusion holding the open compute, which the tool cannot
    fix, so it is surfaced instead of swallowed.

    Returns "" (nothing to cancel, or the cancel took) or a sentence to APPEND to the error the
    caller is already returning. `what` names the transaction in it ("trim", "boundary-fill")."""
    if inp is None:
        return ""
    if safe(lambda: inp.cancel()):
        return ""
    return (f" The open {what} transaction could NOT be cancelled - Fusion may be left mid-compute; "
            "undo in Fusion before continuing.")


# ── the DIRECT-mode no-feature shape (a Features.*.add() that returns nothing) ──────────────

# MEASURED (Fusion 2704.1.39, direct-mode scratch document). SIX classes returned None WHILE the
# edit landed: combineFeatures.add (a join took 2 bodies to 1), moveFeatures.add (the translate
# applied), splitBodyFeatures.add (7 bodies to 8), scaleFeatures.add (a x2 factor multiplied volume
# by 8), offsetFacesFeatures.add (a loft frustum read 117.248 after a +0.2 side-face offset) and
# deleteFaceFeatures.add (a healed fillet face took a box from 7 faces to 6). ELEVEN returned real
# feature objects in that same direct design: extrudeFeatures.addSimple, filletFeatures.add,
# shellFeatures.add, chamferFeatures.add, rectangularPatternFeatures.add, mirrorFeatures.add,
# holeFeatures.add, revolveFeatures.add, thickenFeatures.add, loftFeatures.add and
# patchFeatures.add. So the None is PER-CLASS, not family-wide - which is why this is a MODE gate
# and not a per-class table: a class that does return a feature never reaches it, and a class nobody
# has measured is covered either way.
#
# A None is never itself proof of an effect: a direct combine of two NON-TOUCHING bodies returned
# None with the body count AND the target volume both unmoved. The caller's own effect read decides.

DIRECT_FEATURE_NOTE = ("This design is in DIRECT mode, where this operation creates no timeline "
                       "feature object to name - what is reported here is read back off the model.")


def direct_feature_absence(design, feature) -> bool:
    """True when a falsy Features.*.add() return is the DIRECT-mode shape rather than a failure.

    It says only that the RETURN carries no information about success in this design. The caller
    must still confirm its own effect (a census / volume / count read-back of the model, never the
    feature) before reporting ok, and must not publish a feature name it does not have - see
    DIRECT_FEATURE_NOTE. In a PARAMETRIC design this is always False: a None feature there is
    unmeasured as a success and stays an honest error."""
    # _inputs imports _common, so the ONE mode reader (current_design_type, shared with ModeGuard
    # and design_get's mode slice) is bound at call time rather than at import.
    from . import _inputs
    return not feature and _inputs.current_design_type(design) == _inputs.MODE_DIRECT


def null_feature_note(design, feature, base_feature_name, what) -> str:
    """The sentence a payload appends when a write's add() came back without a feature object.

    A DIRECT design gets DIRECT_FEATURE_NOTE. A design that is NOT direct ran the add inside a
    base-feature edit scope, and THAT scope - not the design's mode - is why there is no feature to
    name, so the scope is what the note names. `what` names the operation ("plane cut"). It never
    claims the write succeeded: the caller's own effect read-back decides that, exactly as with
    direct_feature_absence."""
    if direct_feature_absence(design, feature):
        return DIRECT_FEATURE_NOTE
    if base_feature_name:
        return (f"'feature' is null: this {what} ran inside the base-feature edit scope "
                f"'{base_feature_name}' that a parametric mesh write requires, and the add returned "
                "no feature there - the result is read back off the model.")
    return (f"'feature' is null and no base-feature scope was opened - the {what} result is read "
            "back off the model.")


def census_host(entity, fallback):
    """The component whose body collection a before/after census must be counted on: the target
    entity's OWN parentComponent, falling back to `fallback` when it cannot be read.

    MEASURED: a direct splitBodyFeatures.add put the new piece in the TARGET's parentComponent
    (SplitHost 1 -> 2) while the ACTIVE component held still (root 3 -> 3), and a direct combine
    whose target AND tool both live in a sub-component moves nothing the active component can see
    (root 3 -> 3 throughout). So a census scoped to target_component(design) is BLIND to either.

    Resolve this ONCE, BEFORE the mutation, and count the SAME object twice: re-deriving it
    afterwards lets a proxy that stops answering parentComponent swing the count onto a different
    collection, and the difference of two unrelated counts is a fabricated number, not a verdict."""
    return safe(lambda: entity.parentComponent) or fallback


def body_count(host):
    """How many BRep bodies `host` holds, or None if the collection cannot be read."""
    return safe(lambda: host.bRepBodies.count)


def failed_effect_remedy(design, feature) -> str:
    """The remediation sentence a "the call ran but its effect is wrong" error ends with.

    A PARAMETRIC design leaves a timeline entry to delete. On the direct-mode no-feature path there
    is neither a feature nor a timeline, so naming design_delete_feature would send the caller after
    something that does not exist - the honest remedy there is Fusion's own undo."""
    if direct_feature_absence(design, feature):
        return ("This design is in DIRECT mode: there is no timeline feature to remove, so whatever "
                "did change is already in the model - undo in Fusion, or re-run with corrected "
                "inputs.")
    return "The feature remains in the timeline; remove it with design_delete_feature."


def no_feature_error(design, what, hint="") -> str:
    """The refusal text for a falsy add() at a site whose ONLY evidence was the feature object.

    In a parametric design that is a plain failure. In a direct design this call cannot tell a
    no-op from a landed-but-unnamed edit, so it says so and points at the read that settles it,
    instead of asserting a failure it cannot prove. `what` names the operation ("Extrude")."""
    text = f"{what} returned no feature."
    if hint:
        text = f"{text} {hint}"
    if direct_feature_absence(design, None):
        text += (" This design is in DIRECT mode, where some feature classes return no feature "
                 "object even though the edit LANDED, so this result cannot tell a no-op from a "
                 "silent success. Read the model back with design_get before retrying - a retry "
                 "would repeat an edit that may already be in the model.")
    return text


def timeline_health(design, limit=None):
    """(error_names, warning_names, total) over the parametric timeline by healthState (2=error,
    1=warning) - the shared before/after guard for edits that can break downstream features, so a
    change that corrupts the model is reported instead of swallowed. A direct-modelling design
    (no timeline) yields empty lists.

    ``limit`` bounds the walk to the FIRST n items: a CREATE that only wants the damage it did to
    PRE-EXISTING features passes the total this returned before its mutation, which keeps the walk off
    the entry it just added. That matters beyond tidiness - a freshly added assembly constraint's own
    TimelineObject.healthState RAISES '1 : Unknown exception' (measured, Fusion 2705.0.87), and the
    same caught error inside a Python.Run script context rolled the whole transaction back."""
    errors, warnings, total = [], [], 0
    tl = safe(lambda: design.timeline)
    if tl is None:
        return errors, warnings, total
    count = safe(lambda: tl.count, 0) or 0
    if limit is not None:
        count = min(count, max(int(limit), 0))
    for i in range(count):
        it = tl.item(i)
        total += 1
        hs = safe(lambda it=it: it.healthState)
        if hs == 2:
            errors.append(safe(lambda it=it: it.name) or f"#{i}")
        elif hs == 1:
            warnings.append(safe(lambda it=it: it.name) or f"#{i}")
    return errors, warnings, total


# A before/after volume difference (cm3) smaller than this is NO CHANGE - the ONE band every
# material-changing feature judges "the API reported success but nothing moved" against. One home so
# a fillet, an extrude, a thread and a pipe cannot disagree about what a zero is; a site whose signal
# is not a volume (a bounding-box extent, a displacement) keeps its own named tolerance.
NO_VOLUME_CHANGE_CM3 = 1e-9


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


# The '<type>:<index>' ref kinds resolve_entity_ref addresses - the single source of truth for
# "which kinds exist"; sketch tools defer to this tuple, never a local copy that can drift.
# spline = fitted, cv_spline = control-point, fixed_spline = fixed/NURBS-referenced: three distinct
# SketchCurves collections, each with its own creation-order index space.
ENTITY_REF_KINDS = ("line", "arc", "circle", "ellipse", "point", "spline", "cv_spline", "fixed_spline")

# kind -> the SketchCurves sub-collection attribute it indexes ('point' is handled separately below,
# it lives on the sketch itself, not under sketchCurves).
_ENTITY_REF_CURVE_ATTR = {
    "line": "sketchLines",
    "arc": "sketchArcs",
    "circle": "sketchCircles",
    "ellipse": "sketchEllipses",
    "spline": "sketchFittedSplines",
    "cv_spline": "sketchControlPointSplines",
    "fixed_spline": "sketchFixedSplines",
}


def entity_collection(sketch, kind):
    """The raw collection a '<type>:<index>' ref of this ``kind`` (one of ENTITY_REF_KINDS) indexes,
    or None if the kind is unknown or the collection is unavailable. The one place that knows which
    SketchCurves sub-collection (or sketchPoints) each ref token names - reuse this instead of
    re-deriving the mapping (e.g. for a before/after count read-back)."""
    if kind == "point":
        return safe(lambda: sketch.sketchPoints)
    attr = _ENTITY_REF_CURVE_ATTR.get(kind)
    if attr is None:
        return None
    curves = safe(lambda: sketch.sketchCurves)
    if curves is None:
        return None
    return safe(lambda: getattr(curves, attr))


def resolve_entity_ref(sketch, ref):
    """A sketch entity from a '<type>:<index>' ref, indexing that curve/point collection in creation
    order. type is one of ENTITY_REF_KINDS. Returns the entity, or None."""
    s = (ref or "").strip().lower()
    if ":" not in s:
        return None
    kind, _, idx = s.rpartition(":")
    try:
        i = int(idx)
    except Exception:
        return None
    if kind not in ENTITY_REF_KINDS:
        return None
    coll = entity_collection(sketch, kind)
    if coll is None:
        return None
    if i < 0 or i >= safe(lambda: coll.count, 0):
        return None
    return safe(lambda: coll.item(i))


def resolve_entity_refs(sketch, raw, field="entities"):
    """Every entity named by a COMMA-SEPARATED '<type>:<index>' list, in the order given - the ONE
    parser for the multi-entity sketch selector (``entities``) that sketch_constrain's list kinds and
    sketch_move/sketch_copy both take. Returns (entities, refs, error): ``refs`` is the cleaned token
    list (kept even on failure, so a caller can name what it was given) and the error names the FIRST
    ref that did not resolve, with the legal kinds."""
    refs = [r.strip() for r in (raw or "").split(",") if r.strip()]
    ents = []
    for ref in refs:
        ent = resolve_entity_ref(sketch, ref)
        if ent is None:
            return None, refs, (f"Could not resolve '{ref}' in '{field}' (use '<type>:<index>', "
                                f"type = {'/'.join(ENTITY_REF_KINDS)}) - ids come from "
                                "sketch_get(include_entities=true).")
        ents.append(ent)
    return ents, refs, None


# ── entity-anchored POSITION references ('<type>:<index>:<anchor>') ──────────

# The optional THIRD colon-segment of a sketch entity ref names WHICH point of the entity is meant.
# Pinning a position on the owning entity's own point beats a bare 'point:N', which mis-attaches when
# two entities share coordinates and each mints its own point index. sketch_dimension and
# sketch_constrain read the SAME forms through this parser.
SKETCH_ANCHORS = ("start", "end", "mid", "midpoint", "center")


def parse_anchor_ref(ref):
    """Split '<type>:<index>[:<anchor>]' -> (entity_ref, anchor_or_None, error). start/end/mid apply
    to a line, center to a circle/arc. An unrecognized third segment errors, naming the valid
    anchors, rather than silently mis-resolving to the bare entity."""
    s = (ref or "").strip()
    parts = s.split(":")
    if len(parts) <= 2:
        return s, None, None
    anchor = parts[-1].strip().lower()
    if anchor not in SKETCH_ANCHORS:
        return None, None, (f"'{ref}': unknown anchor '{parts[-1]}'. Valid: "
                            f"{', '.join(SKETCH_ANCHORS)} (e.g. 'line:0:end', 'circle:2:center').")
    return ":".join(parts[:-1]), anchor, None


def _midpoint_sketch_point(sketch, line):
    """A SketchPoint welded to a line's MIDPOINT (created at the geometric midpoint, then constrained
    with addMidPoint so it tracks the line parametrically). Returns (point, None) or (None, error)."""
    sp = safe(lambda: line.startSketchPoint.geometry)
    ep = safe(lambda: line.endSketchPoint.geometry)
    if sp is None or ep is None:
        return None, "anchor 'mid' needs a line with two endpoints."
    mid = adsk.core.Point3D.create((sp.x + ep.x) / 2.0, (sp.y + ep.y) / 2.0,
                                   ((safe(lambda: sp.z, 0.0) or 0.0)
                                    + (safe(lambda: ep.z, 0.0) or 0.0)) / 2.0)
    pt = sketch.sketchPoints.add(mid)               # MUTATION - let a failure raise into the handler
    if pt is None:
        return None, "could not create a midpoint anchor point."
    # The weld is what makes 'mid' PARAMETRIC - an unwelded point sits at today's midpoint and
    # silently stops tracking the line on the next edit. A failed weld rolls the point back and
    # errors, rather than handing back an anchor that looks right and drifts.
    welded = safe(lambda: sketch.geometricConstraints.addMidPoint(pt, line), _UNREADABLE)
    if welded is _UNREADABLE or welded is None:
        rolled = bool(safe(lambda: pt.deleteMe()))
        return None, ("the midpoint weld (addMidPoint) failed, so the anchor would NOT track the "
                      "line - " + ("the anchor point was rolled back." if rolled else
                                   "and the anchor point could not be removed; delete it in the "
                                   "sketch.") + " Anchor to 'start'/'end' instead.")
    return pt, None


def anchor_point(sketch, entity, anchor):
    """The SketchPoint an anchored ref names, as (point, error). start/end need a line's (or arc's)
    endpoint; center needs a circle/arc; mid/midpoint CREATES a point welded to a line's midpoint,
    so it adds geometry where the other anchors only read one."""
    start = safe(lambda: entity.startSketchPoint)
    end = safe(lambda: entity.endSketchPoint)
    center = safe(lambda: entity.centerSketchPoint)
    if anchor == "start":
        return (start, None) if start is not None else (None, "anchor 'start' needs a line or arc.")
    if anchor == "end":
        return (end, None) if end is not None else (None, "anchor 'end' needs a line or arc.")
    if anchor == "center":
        return (center, None) if center is not None else (None, "anchor 'center' needs a circle or arc.")
    # mid / midpoint - a line only (a well-defined addMidPoint target; a circle/arc uses 'center')
    if center is not None or start is None or end is None:
        return None, "anchor 'mid' applies to a LINE (line:N:mid); for a circle/arc use 'center'."
    return _midpoint_sketch_point(sketch, entity)


# Operation name -> adsk.fusion.FeatureOperations attribute (extrude/revolve/sweep/loft-style features).
OPERATIONS = {
    "new": "NewBodyFeatureOperation",
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
    "new_component": "NewComponentFeatureOperation",
}


# ── the ONE path resolver (sweep / pipe / path pattern / on-path datum) ──────

def build_path(comp, path_raw):
    """Resolve a feature path input to an adsk.fusion.Path. Returns (path, label, error).

    'sketch:<name>' -> chain the connected curves of that path sketch (Features.createPath, isChain).
    Otherwise a find_geometry EDGE handle (ONE, passed to createPath with chaining requested) or a
    JSON list of edge handles (used exactly, no chaining) -> a model-edge path (Path.create). A path
    built from several edges requires them to geometrically connect into one path.

    Requesting chaining is not the same as getting it (see the count note at the return): the label
    reports the built Path's own count, which is the only statement of what the path holds."""
    # _inputs imports _common, so the edge-handle kind is bound at call time rather than at import.
    from . import _inputs
    if isinstance(path_raw, str) and path_raw.strip().lower().startswith("sketch:"):
        nm = path_raw.split(":", 1)[1].strip()
        sk, _ = target_sketch(comp, nm)
        if not sk:
            return None, None, f"No sketch named '{nm}' for the path. Use sketch_get or sketch_create."
        curves = safe(lambda: sk.sketchCurves)
        cn = safe(lambda: curves.count, 0) if curves else 0
        if not cn:
            return None, None, f"Path sketch '{nm}' has no curves to build a path from."
        seed = safe(lambda: curves.item(0))
        try:
            p = comp.features.createPath(seed, True) # isChain=True: chain the connected curves
        except Exception as e:
            return None, None, f"Could not build a path from sketch '{nm}': {e}"
        if not p:
            return None, None, f"createPath returned nothing for sketch '{nm}'."
        return p, f"sketch:{nm}", None

    # Model-edge path. A single handle is kept whole (a composite handle carries commas in its
    # locator, so it must NOT be comma-split); several must arrive as a JSON list.
    if path_raw in (None, "", []):
        return None, None, ("'path' is required: a find_geometry edge 'handle' (or a JSON list of "
                            "them), or 'sketch:<name>' for a path sketch.")
    handles = [path_raw] if isinstance(path_raw, str) else list(path_raw)
    edges, err = _inputs.GeometryHandleList("path", require="edge").resolve(handles)
    if err:
        return None, None, err
    if len(edges) == 1:
        try:
            p = comp.features.createPath(edges[0], True) # request chaining from the seed edge
        except Exception as e:
            return None, None, f"Could not build a path from the edge: {e}"
    else:
        coll = adsk.core.ObjectCollection.create()
        for e in edges:
            coll.add(e)
        try:
            # Multiple edges: use them exactly (noChainedCurves); they must connect into one path.
            p = adsk.fusion.Path.create(coll, adsk.fusion.ChainedCurveOptions.noChainedCurves)
        except Exception as e:
            return None, None, f"Could not build a path from the {len(edges)} edges: {e}"
    if not p:
        return None, None, "Path build returned nothing (the edges may not connect into one path)."
    # The label publishes the BUILT path's own entity count (Path.count), never the input count.
    # What a single seed handle produces is not predictable from the request: chaining follows
    # TANGENT CONTINUITY and nothing else - an arc seed between two tangent lines built a 3-entity
    # path, a tangent-continuous closed loop chained all 8 of its edges from one seed, and a sharp
    # corner (including a fillet patch that breaks tangency at the junction) stops it. Open vs
    # closed does not decide it. So the count is READ off the built Path and the wording asserts no
    # expansion; only the read number says what was actually swept.
    built = safe(lambda: int(p.count), 0) or 0
    how = ("from 1 seed handle" if len(edges) == 1
           else f"from {len(edges)} handles, used exactly")
    return p, (f"{built} edge(s) {how}" if built else f"{how}; edge count unreadable"), None


def iter_collection(coll):
    """Yield each item of a Fusion count/item(i) collection - the measured live protocol
    (brepbodies-protocol, VERIFIED_API_FACTS.md); the fakes carry the same shape. Yields nothing
    when the collection is absent."""
    for i in range(safe(lambda: coll.count, 0) or 0):
        it = safe(lambda i=i: coll.item(i))
        if it is not None:
            yield it
