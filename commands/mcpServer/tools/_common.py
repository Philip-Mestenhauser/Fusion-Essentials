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
MAP_BLURB = "ok/error/safe, measured (a scaled number or None - the honest counterpart to safe(read, 0.0) for anything a caller treats as a MEASUREMENT, where 0 is an answer) + read_flag (the same honesty for a BOOLEAN: True/False/None, never a coerced False - the ONE unreadable-flag read every set-then-read-back gate and every published flag goes through) + counted (the same honesty for an INTEGER COUNT: the int or None, never a coerced 0/1 - for a count an absent read does NOT make zero, like a body's lumps or a built path's entities; safe(read, 0) stays right for a TALLY over a collection that may be absent), design/target_component, find_sketches_by_name + find_sketch + resolve_sketch + find_or_recent_sketch + resolve_or_recent_sketch (the ONE design-wide by-name sketch walk - every component asked for its OWN sketch of that name, de-duplicating nothing, so two components sharing one entityToken cannot merge and silently drop a hit - its resolve-one, which REFUSES a name SEVERAL sketches carry naming each owning component instead of first-matching, and the name-or-most-recent contract over it: find_or_recent_sketch hands the refusal back as a third value so a caller with a wire string NAMES the duplicate, resolve_* drop it for a caller with nowhere to put it) + placements_holding_sketch (the occurrence paths a shared-owner-name refusal names its owners by - an occurrence whose OWN component answers to the name AND hands back a sketch of it, both read off ONE occurrence, so each row is true on its own where no read attributes a placement to one of two components wearing one name), named_with_remainder + told_apart (the ONE capped wire list - the remainder COUNTED, never silently dropped - and the ONE substitution a listing makes when a name REPEATS: a name only one row carries renders as that name, a name several rows carry renders as that row's own discriminator (an occurrence path, a 'Setup / op' breadcrumb), and a row with none keeps its name; a listing that prints one name twice has restated its own count and said nothing else), resolve_entity_ref + resolve_entity_refs (the ONE '<type>:<index>' sketch-entity resolver and the comma-separated list parser over it), SKETCH_ANCHORS + parse_anchor_ref + anchor_point (the ONE entity-anchored position grammar - a ref's optional third segment ':start/:end/:mid/:center' naming WHICH point of the entity is meant, and the resolve to that SketchPoint; sketch_dimension and sketch_constrain read the same forms through it, and 'mid' CREATES a midpoint-constrained point where the others only read one), most_recent_body + resolve_body_or_recent (the ONE 'that body, or the most recent one' resolution every whole-body edit runs: a given handle/name goes through the caller's own BodyRef, empty falls back to most_recent_body, and the caller words the no-body error), NO_VOLUME_CHANGE_CM3 (the ONE band a before/after volume difference counts as no change at all - every material-changing feature judges its silent no-op against it), result_bodies + body_facts (the feature-result walk and the per-body {name, is_solid} projection it is published with), landed_extent_cm + landed_extent2_cm + EXTENT_MATCH_TOL_CM (the ONE read of the depth an extrude-family feature REPORTS, per SIDE - extentOne.distance.value in internal cm, and extentTwo's for the second side of a two-sided extent, MEASURED to keep the requested sign, to answer the per-SIDE number a symmetric extent was asked for, to be unperturbed by taper, to stay the REQUESTED distance on a cut that bottomed inside material, and to carry each two-sided side's own magnitude with no swap - and the band a landed depth may differ from the request by and still be the same length; every solid or surface extrude compares its request against these reads, so two of them cannot disagree about what the feature landed), open_profile_from_sketch, scale, timeline_health (the shared before/after edit guard), set_verified (the set-then-read-back every FeatureInput property assignment needs - a SWIG proxy accepts an unknown name silently), apply_rename (the ONE create-flow rename-with-disclosure: sets entity.name, reads it back, returns (final_name, warning-or-None) - a declined or deduped rename is DISCLOSED in the payload, never swallowed and never an error on a create that succeeded), cancel_input (the ONE abort for a partial-computing createInput transaction - trim/boundary fill - that reports a refused cancel instead of swallowing it), direct_feature_absence + no_feature_error + failed_effect_remedy + DIRECT_FEATURE_NOTE (the one mode gate for a Features.*.add() that returns nothing: measured per-class in DIRECT designs while the edit LANDS, so a site with a feature-independent effect check falls through to it, a site without one refuses honestly, and a wrong-effect error ends with the remedy that actually exists in that mode) + null_feature_note (the ONE sentence a payload appends for a null feature - DIRECT mode, or the base-feature edit scope that suppressed it - so no site re-rolls the branch or infers a design mode from the missing object), census_host + body_count (the resolve-the-collection-ONCE-before-the-mutation body census a feature-free effect check counts on - measured: the pieces land in the TARGET's parentComponent, not the active component), same_component (the ONE same-component test, TRI-STATE - component wrappers are measured NEVER identity-stable, so `a is b` between two component references is always False and must never carry the comparison; the verdict runs on native_identity, never on a bare entityToken - a token is DOCUMENT-LOCAL and every document's ROOT COMPONENT answers one shared token, so a token compare calls a component an x-ref brought in the host's own root - and answers None where a missing operand or an unreadable identity means no comparison was made, since a NAME is not the component: every caller branches `is True` / `is False` / `is None`, because a bare `if same_component(...)` reads the unknown as the 'different component' answer nothing was read to support), iter_collection (the ONE count/item(i) walk over a Fusion collection - every present item, empty when the collection is absent), native_token + native_identity (the ONE physical-entity identity, for BODIES and COMPONENTS alike: native_token is (nativeObject or self).entityToken, safe at both steps, which collapses a native body and its occurrence proxies onto one value but is DOCUMENT-LOCAL - two bodies in two x-ref'd documents answer the same one, and every document's ROOT COMPONENT answers one shared token - and native_identity is the KEY every comparison and de-dup runs on, that token paired with the source document's lineage urn, read through parentComponent.parentDesign for an entity a component OWNS or parentDesign for a COMPONENT (measured: exactly one of the two chains reads per kind; None for a never-saved document), None when the token does not read; a local safe(lambda: b.entityToken) re-roll is how a de-dup counts one body twice, and a bare-token key is how it MERGES two different bodies - or seven root components into one), occurrence_walk + all_occurrences + occurrence_paths + component_contains (the ONE design-wide occurrence census and its projections: root.allOccurrences is the fast path and the only source of true fullPathNames, but the PROPERTY ACCESS ITSELF raises on a design holding an unresolved external reference, so occurrence_walk falls back to a component.occurrences recursion and publishes WHICH walk answered (occurrences_walk: allOccurrences / recursed / unreadable), its usable rows, its unresolved rows and a total that is None - never 0 - when nothing enumerated; all_occurrences is the usable-rows-only list, occurrence_paths the path census a structural edit diffs to read its effect back, and component_contains the TRI-STATE cycle test a re-parent/instance refuses on - True/False, and None whenever the subtree was not fully enumerated, one occurrence's component identity would not read, or the census holds ANY unresolved-reference row (a broken row leaves complete=True, so that flag alone answers 'this edit is legal' on the very design an unresolved reference makes unreadable), since a False there is a positive claim and the caller must refuse instead of reading None as 'no cycle') + broken_reference (the ONE unresolved-external-reference detector: occ.component RAISING AT ALL is the gate and the raise text is published verbatim as the detail - isReferencedComponent, documentReference, isValid and isLightBulbOn were each measured LYING on a real broken reference), build_path (the ONE feature-path resolver every sweep/pipe/path-pattern/on-path datum builds its adsk.fusion.Path with: 'sketch:<name>' chains a path sketch's curves, ONE find_geometry edge handle chains from that seed across TANGENT connections - a sharp corner stops the chain, so the built Path's count is the truth - and a JSON list of edge handles is used exactly and must connect) - the response+resolve substrate"

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


def same_component(a, b):
    """True when `a` and `b` denote the SAME component, False when they denote DIFFERENT ones, and
    None when the comparison COULD NOT BE MADE.

    MEASURED: component identity is NEVER stable. Two reads of design.rootComponent return DIFFERENT
    Python objects (`c1 is c2` is False), and so do rootComponent vs body.parentComponent vs
    edge.body.parentComponent - all four sharing ONE entityToken. So `component_a is component_b` is
    effectively ALWAYS FALSE and cannot carry a same-component test: written that way it silently
    takes the "different component" branch every time.

    The comparison runs on ``native_identity`` - the token paired with the source document's lineage
    urn - with the identity check kept as a free short-circuit. The token ALONE cannot carry it:
    entityToken is DOCUMENT-LOCAL, and every document's ROOT COMPONENT answers one shared token
    ('/v4BAAEAAwAAAAAAAAAAAAAA', measured across the root of a host and the roots each x-ref brought
    in). A component reached through an x-ref IS that source document's root, so a token compare
    answers True for two demonstrably different components and every `is True` branch - "nothing to
    lift", "this JO is on the root", "the write landed where it was asked" - takes the wrong side on
    exactly the cross-document pair it was written to separate. The urn half is what tells them
    apart.

    A missing operand, or an identity that will not read on either side, answers None: a NAME is not
    the component - two components may carry one name and one component answers a different name
    after a rename - so an unreadable identity supports no verdict, and the tri-state is what lets
    each caller pick between refusing and disclosing.

    Every caller branches on the three states explicitly (`is True` / `is False` / `is None`). A bare
    `if same_component(...)` reads None as False, which is the "different component" answer that
    nothing was read to support."""
    if a is None or b is None:
        return None
    if a is b:
        return True
    ia, ib = native_identity(a), native_identity(b)
    # Both sides through the None gate first: `ia == ib` alone would call two components whose
    # identities BOTH failed to read the same one, which is the merge this pair exists to refuse.
    if ia is None or ib is None:
        return None
    return ia == ib


def _native_of(entity):
    """The entity a wrapper STANDS FOR: its ``nativeObject`` when one reads, else the wrapper itself.

    ``nativeObject`` is read through ``safe``: a wrapper kind that does not answer it at all is its
    own native."""
    return safe(lambda: entity.nativeObject) or entity


def native_token(entity):
    """The entityToken of the entity a wrapper STANDS FOR - ``(nativeObject or self).entityToken`` -
    or None when neither reads.

    DOCUMENT-LOCAL, which is half an identity: the token names the entity WITHIN one document, and
    two entities living in two DIFFERENT documents can answer the same token. MEASURED on a host
    holding two x-refs of one design: the two 'Frame' bodies read byte-identical tokens. So this is
    never the key a comparison or a de-dup runs on - ``native_identity`` is, and it pairs this with
    the document the entity comes from.

    MEASURED: a body and its occurrence PROXY carry DIFFERENT entityTokens (each stable across
    re-fetches of that wrapper), while ``nativeObject`` reads None on a native and hands back the
    native on a proxy. So the WRAPPER's own token sees ONE body as two: keyed on it a de-dup counts
    one body twice, and a same-body guard never fires on a native-vs-proxy pair of the same body.
    That is why the identity resolves to the native first. It is NOT the key for a HANDLE a tool
    publishes - a handle is the wrapper's own token, and resolving it back to a context-carrying
    proxy is the point."""
    return safe(lambda: _native_of(entity).entityToken)


def _source_document_urn(native):
    """The lineage id of the document ``native`` LIVES in, or None when it does not read.

    Two chains, because the entity kinds an identity is taken for hang off the design at DIFFERENT
    depths: an entity a component OWNS reaches the design through ``parentComponent``, while a
    COMPONENT is itself what a design owns and answers ``parentDesign`` directly. MEASURED
    (live_api_facts.SHAPES, pinned by test_common.py's shape test): BRepBody and MeshBody carry
    ``parentComponent`` and NOT ``parentDesign``; Component carries ``parentDesign`` and NOT
    ``parentComponent``. So exactly ONE chain reads on each kind and the other answers None - the
    second is a different entity depth, never a looser guess at the first.

    A never-saved document carries no ``dataFile``, so the id reads None there. That is an answer,
    not a hole: an unsaved document cannot be x-ref'd into another one."""
    design = (safe(lambda: native.parentComponent.parentDesign)
              or safe(lambda: native.parentDesign))
    return safe(lambda: design.parentDocument.dataFile.id)


def native_identity(entity):
    """The PHYSICAL-entity key any two entity references are compared or de-duplicated on: the pair
    ``(native_token, source-document lineage urn)``, or None when the token does not read. Bodies and
    COMPONENTS both answer it - see ``_source_document_urn`` for the two chains the urn is read
    through.

    Both halves are needed, and each one alone is wrong in a different direction:

    * the TOKEN collapses a body and its occurrence proxies onto one value (they resolve to one
      native), which the wrapper's own token cannot;
    * the URN - the lineage id of the document the native LIVES in - separates two entities that
      merely share a document-local token. MEASURED on a host holding two x-refs of one design:
      'Frame's body read one token through both x-refs while the two urns differed. MEASURED again
      on a CAM job assembled from 7 source documents: the 7 ROOT COMPONENTS read one byte-identical
      token between them, and all 7 urns read DISTINCT - each root answering its own source
      document, the one matching the host being the host's own root rather than a collision. So a
      token-only key collapses those 7 to 1 while this pair keeps them apart. Keyed on the token
      alone such entities compare equal, so a de-dup drops one silently and a same-body guard
      refuses a legitimate pair.

    A never-saved document has no ``dataFile``, so the urn half reads None. That is an answer, not a
    hole: an unsaved document cannot be x-ref'd into another one, so a None urn belongs to the HOST's
    own entities, where the token is already unique. Nothing is substituted for it.

    None when the token reads empty or not at all: there is then no identity to compare, and a caller
    keys on something it can defend (a name plus a scope) or refuses."""
    token = native_token(entity)
    if not token:
        return None
    return (token, _source_document_urn(_native_of(entity)))


def root_body_advisory(d, comp):
    """A note (or '') for a build tool to append when it just built into ROOT with no component active.

    Best practice is one component per part - and it is not just tidiness: promoting a root body into a
    component LATER re-serializes the body in the internal data model (every entity handle/token on it is
    re-minted, invalidating handles you hold) and clutters the root timeline. Modelling the FIRST body
    straight into a component avoids that. This fires only when it is still cheap to switch (root has <=1
    solid body and no sub-components), so it advises at the point of the decision, not as nagging."""
    # same_component, not `is`: component wrappers are never identity-stable, so `comp is not
    # d.rootComponent` reads True even AT the root and this advisory would never fire at all. The
    # advisory ASSERTS the build landed in root, so only a proven True fires it - an unknown
    # comparison says nothing rather than advising about a component it could not identify.
    if comp is None or same_component(comp, safe(lambda: d.rootComponent)) is not True:
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


def broken_reference(occ):
    """(is_broken, detail) for ONE occurrence - the ONE unresolved-external-reference detector.

    The gate is ``occ.component`` RAISING AT ALL, and ``detail`` is that exception's text verbatim.
    A component that cannot be read is unusable to every caller whatever the wording, so the gate is
    the raise itself rather than a substring match the platform could reword.

    Every OTHER candidate signal was measured LYING on a real broken reference (an occurrence whose
    source project is archived): ``isReferencedComponent`` reads False where a live xref reads True;
    ``documentReference`` raises the SAME "not referencing an external component" text an ordinary
    local occurrence gives, so it cannot tell the two apart; ``isValid`` and ``isLightBulbOn`` both
    read True. ``name`` still reads, and is the only identity a caller can publish.

    Structurally the broken occurrence is present in ``component.occurrences`` and ABSENT from
    ``childOccurrences`` (its assembly path is invalid), which is why ``occurrence_walk`` scans the
    component-local collection for it."""
    try:
        occ.component
    except Exception as exc:                    # noqa: BLE001 - the raise IS the signal
        return True, (str(exc) or type(exc).__name__)
    return False, None


# The walk that produced an occurrence census, published beside every count it feeds.
WALK_FAST = "allOccurrences"        # root.allOccurrences enumerated
WALK_RECURSED = "recursed"          # it raised; the census was rebuilt from component.occurrences
WALK_UNREADABLE = "unreadable"      # neither path enumerated - the census is UNKNOWN, not empty

_WALK_MAX_DEPTH = 24                # a guard on the recursion, not a modelling limit
_WALK_MAX_NODES = 20000


class OccurrenceWalk:
    """The result of ONE design-wide occurrence census: the usable rows, the unresolved ones, and HOW
    the census was taken.

    ``occurrences`` holds only occurrences whose component READS - the rows a caller can go on to
    measure, name or proxy. ``broken`` holds one record per unresolved reference
    ({name, parent_path, detail}); those rows answer almost nothing (``fullPathName``, ``isVisible``
    and ``childOccurrences.count`` all raise on them), so they are kept apart rather than salted
    into a list every consumer iterates. ``total`` counts both, and is None only when nothing
    enumerated - the state that must reach the wire as null, never as 0 with a truncation flag
    denying anything was lost."""

    __slots__ = ("occurrences", "broken", "broken_occurrences", "method", "complete", "_census")

    def __init__(self, occurrences, broken, method, complete, broken_occurrences=None):
        self.occurrences = occurrences
        self.broken = broken
        # Pinned at construction, BEFORE any cap slices the row list - a cap bounds what is rendered,
        # never what the design was found to hold.
        self._census = len(occurrences) + len(broken)
        # The raw objects behind `broken`, in the same order. `broken` is pure wire data (it is
        # published), so a resolver that must still MATCH an unresolved occurrence by name - and
        # refuse it with the real reason instead of "no such occurrence" - reads them from here.
        self.broken_occurrences = list(broken_occurrences or [])
        self.method = method
        self.complete = complete

    @property
    def readable(self):
        return self.method != WALK_UNREADABLE

    @property
    def total(self):
        """How many occurrences the design holds, unresolved ones included - or None when the census
        could not be taken at all. A count from an INCOMPLETE walk (``complete`` False) is a lower
        bound; the flag, not the number, is what says so."""
        return None if not self.readable else self._census

    def names(self):
        """The unresolved occurrences' names, for a wire string that must NAME them."""
        return [b["name"] for b in self.broken]


def _broken_record(occ, parent_path, detail):
    """One unresolved-reference row. ``name`` is the only identity that reads on such an occurrence;
    when even that raises, the row publishes the parent path and says the name is unreadable rather
    than dropping the row."""
    name = safe(lambda: occ.name)
    return {"name": name if name else "(unreadable name)",
            "name_readable": name is not None,
            "parent_path": parent_path,
            "detail": detail}


def _recursed_walk(root):
    """Rebuild one component's subtree census WITHOUT ``allOccurrences``, which an unresolved
    reference ANYWHERE IN THAT SUBTREE makes raise (measured on both the root component and a
    mid-tree container; a leaf component whose subtree is clean still answers).

    Two collections per node, because neither alone is both correct and complete: assembly-context
    children (``childOccurrences``, and ``root.occurrences`` at the top) carry the true
    fullPathNames but silently DROP an unresolved child, while the component-local collection
    (``component.occurrences``) DOES hold it. So the healthy rows are taken from the first and the
    unresolved rows from the second, each classified by ``broken_reference``.

    Returns (occurrences, broken, complete, broken_occurrences); complete is False when any node's
    collection would not enumerate or a depth/node cap was hit, so a short census is never mistaken
    for the whole design."""
    occs, broken, broken_occs = [], [], []
    state = {"complete": True, "n": 0}

    def record_broken(occ, parent_path, detail):
        broken.append(_broken_record(occ, parent_path, detail))
        broken_occs.append(occ)

    def enumerate_coll(coll):
        """(items, readable) for one collection. The COUNT is read through ``counted``, so a
        collection whose count raises is UNREADABLE - not empty. ``iter_collection`` alone would
        swallow that raise into a zero-length walk, which is the exact defect this walk exists to
        stop happening one level up."""
        if coll is None:
            return [], False
        n = counted(lambda: coll.count)
        if n is None:
            return [], False
        return list(iter_collection(coll)), True

    def scan_broken(comp, parent_path):
        """The unresolved children of one component-local collection."""
        children, readable = enumerate_coll(safe(lambda: comp.occurrences))
        if not readable:
            state["complete"] = False
            return
        for child in children:
            is_broken, detail = broken_reference(child)
            if is_broken:
                record_broken(child, parent_path, detail)

    def descend(occ, path, depth):
        if depth > _WALK_MAX_DEPTH or state["n"] >= _WALK_MAX_NODES:
            state["complete"] = False
            return
        comp = safe(lambda: occ.component)
        if comp is None:
            state["complete"] = False
            return
        scan_broken(comp, path)
        kids, readable = enumerate_coll(safe(lambda: occ.childOccurrences))
        if not readable:
            state["complete"] = False
            return
        walk_children(kids, path, depth)

    def walk_children(children, path, depth):
        for child in children:
            state["n"] += 1
            if state["n"] > _WALK_MAX_NODES:
                state["complete"] = False
                return
            # An unresolved occurrence is not expected in an assembly-context collection (measured:
            # childOccurrences drops it) - if one does surface here it is skipped, because the
            # component-local scan is the ONE place unresolved rows are collected, and collecting it
            # twice would double-count the census.
            if broken_reference(child)[0]:
                continue
            occs.append(child)
            name = safe(lambda c=child: c.name) or "?"
            descend(child, f"{path}+{name}" if path else name, depth + 1)

    top, readable = enumerate_coll(safe(lambda: root.occurrences))
    if not readable:
        # Neither walk answered: the census is UNKNOWN. Returning the unreadable shape is what makes
        # the caller publish null rather than an empty design.
        return [], [], False, []
    # The root's own collection is BOTH the component-local and the assembly-context one, so the
    # unresolved scan runs over it directly rather than through descend().
    for child in top:
        is_broken, detail = broken_reference(child)
        if is_broken:
            record_broken(child, safe(lambda: root.name), detail)
    walk_children(top, "", 0)
    return occs, broken, state["complete"], broken_occs


def component_walk(comp, cap=None):
    """The ONE occurrence census over a COMPONENT's subtree, and the honest report of how it was taken.

    ``allOccurrences`` is the fast path and, on the ROOT component, the only source of true assembly
    fullPathNames - an occurrence handle taken from ``<component>.occurrences`` reads a
    COMPONENT-LOCAL path (measured). But the PROPERTY ACCESS ITSELF raises
    (``InternalValidationError : occ``, measured) whenever the subtree holds an unresolved external
    reference, and ``safe(read) or []`` there turns "the assembly could not be read" into "the
    assembly is empty" - which consumers then publish as fact. So a raise falls back to
    ``_recursed_walk`` and the result SAYS which walk answered.

    ``cap`` bounds the returned ``occurrences`` list for a caller that only renders/labels; the
    census counts (``total``, ``broken``) stay whole, so a cap never shrinks the reported design."""
    if comp is None:
        return OccurrenceWalk([], [], WALK_UNREADABLE, False)
    flat = safe(lambda: list(comp.allOccurrences))
    if flat is not None:
        occs, broken, broken_occs = [], [], []
        for o in flat:
            # A None slot is an item the collection would not hand over - not an unresolved
            # reference. It stays in the census (as it always has) so the count still reflects the
            # collection's own size; every consumer reads occurrence fields through safe().
            if o is None:
                occs.append(o)
                continue
            is_broken, detail = broken_reference(o)
            if is_broken:
                broken.append(_broken_record(o, None, detail))
                broken_occs.append(o)
            else:
                occs.append(o)
        walk = OccurrenceWalk(occs, broken, WALK_FAST, True, broken_occs)
    else:
        occs, broken, complete, broken_occs = _recursed_walk(comp)
        if not occs and not broken and not complete:
            return OccurrenceWalk([], [], WALK_UNREADABLE, False)
        walk = OccurrenceWalk(occs, broken, WALK_RECURSED, complete, broken_occs)
    if cap is not None:
        walk.occurrences = walk.occurrences[:cap]
    return walk


def occurrence_walk(d, cap=None):
    """The design-wide occurrence census: ``component_walk`` over the ROOT component, which is the
    only scope whose occurrence handles carry true assembly fullPathNames."""
    return component_walk(safe(lambda: d.rootComponent) if d else None, cap=cap)


def all_occurrences(d, cap=None):
    """Every USABLE occurrence in the design, in ASSEMBLY context, as a flat list - the occurrence
    counterpart of ``all_components``, the basis of the by-name resolver
    (``_inputs._resolve_occurrence``) and of every before/after assembly census.

    A thin projection of ``occurrence_walk``: it survives a raising ``allOccurrences`` by falling
    back to the component-local recursion, and it OMITS unresolved references (whose every geometry
    read raises). A caller that publishes a COUNT, a completeness claim, or a health verdict must
    call ``occurrence_walk`` instead - this list alone cannot say whether it is the whole design."""
    return occurrence_walk(d, cap=cap).occurrences


def occurrence_paths(d):
    """Every occurrence fullPathName in the design, as a set - the before/after census a structural
    edit (instance, re-parent) diffs to read back WHICH paths it actually added.

    An occurrence whose path will not read contributes '' rather than being dropped, unresolved
    references included (their fullPathName raises): a caller filters '' out, where a silently
    dropped row would make the diff report a phantom new path."""
    walk = occurrence_walk(d)
    paths = {(safe(lambda o=o: o.fullPathName) or "") for o in walk.occurrences}
    if walk.broken:
        paths.add("")
    return paths


def component_contains(outer, inner):
    """True when component `inner` IS `outer` or sits anywhere inside it, False when it provably is
    not, and None when the question COULD NOT BE ANSWERED - the cycle test a structural edit refuses
    on (a component cannot hold an instance of itself). Compares through ``same_component``, since
    component wrappers are never identity-stable.

    Walks the subtree through ``component_walk``, not a bare ``Component.allOccurrences``: that
    property raises whenever the subtree holds an unresolved external reference (measured). A False
    here is a POSITIVE claim - "this edit is legal" - so it is returned only when the whole subtree
    was enumerated AND every comparison in it answered. An unreadable walk, a walk that stopped at a
    cap or an unenumerable node, one occurrence whose component identity would not read, or ANY
    unresolved-reference row in the census all answer None: "no cycle" about a subtree nothing was
    read from is the answer that LETS the illegal edit through. A caller REFUSES on None - it never
    reads it as "no cycle".

    ``walk.broken`` is checked separately from ``walk.complete``, because a census that RECORDED an
    unresolved reference is still a COMPLETE one: the fast path stamps complete=True with broken rows
    beside it, and the recursion's ``walk_children`` skips a broken child without clearing the flag.
    Reading ``complete`` alone therefore answers False - "this edit is legal" - on exactly the design
    an unresolved reference makes unreadable. A broken row's component raises, so what it holds
    beneath it was never enumerated and cannot be ruled out as the cycle."""
    self_match = same_component(outer, inner)
    if self_match is True:
        return True
    walk = component_walk(outer)
    if not walk.readable:
        return None
    unproven = self_match is None or not walk.complete or bool(walk.broken)
    for o in walk.occurrences:
        match = same_component(safe(lambda o=o: o.component), inner)
        if match is True:
            return True
        if match is None:
            unproven = True
    return None if unproven else False


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
    single verdict collapses the list itself rather than here, so a MIXED result can still name the
    body that is not solid.

    is_solid goes through ``read_flag``: True / False / None, never a coerced False - a body whose
    flag will not read is not an open surface, and a plain ``all()``/``any()`` over these values
    would fold that unknown into a confident verdict. The verdict a caller builds must keep the
    three states apart (see surface_edit's any-True / else-False-if-any-False / else-None)."""
    return [{"name": safe(lambda b=b: b.name), "is_solid": read_flag(lambda b=b: b.isSolid)}
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


def find_sketches_by_name(d, name):
    """Every (sketch, owning_component) carrying EXACTLY ``name`` - ONE entry per component that
    holds it, in ``all_components`` order. A LIST, because a sketch name is only component-locally
    unique: two components can each hold a 'Sketch1'. The caller decides - one hit resolves, several
    REFUSE with the owning components; never the first hit, which silently draws on/deletes from an
    arbitrary component's sketch.

    The walk is ``all_components`` and NOTHING ELSE, and it de-duplicates NOTHING. That is the whole
    correctness argument. ``design.allComponents`` is Fusion's own collection and lists each
    component exactly once, so there is nothing to de-duplicate - while any de-dup keyed on component
    identity MERGES two components that share an ``entityToken``, which is measured to happen: two
    'Frame' components out of two inserted references both read '/v4BAAEAegEAAAAAAAAAAAAA',
    byte-identical. Merging them dropped one hit, so a name TWO sketches carried came back as one and
    RESOLVED instead of refusing - measured live, an unscoped read of a name two x-refs shared
    returned one of them with no error and no disclosure. That silent first-match is the exact thing
    this list exists to prevent, so the walk must not be able to lose a component.

    Each component is asked with its own ``sketches.itemByName``, so a sketch in a sub-component is
    found, which a plain ``design.rootComponent.sketches.itemByName`` never sees. Order is the
    collection's own and is NOT load-bearing: the one caller that returns a sketch from this list
    (``find_sketch``) returns one only when there is exactly ONE hit, so ordering can never choose
    among several - it decides only the order a refusal lists owners in.

    Only when that walk finds NOTHING does it fall back to asking the active edit component and the
    root directly. ``design.allComponents`` can fail to read (it degrades to the root alone), and a
    sketch in an activated sub-component would then be unreachable by name. The fallback runs on an
    EMPTY result, so it can never add a second copy of a component the main walk already asked - and
    the two components it asks are de-duplicated by the sketch object itself, because the active
    component IS the root whenever nothing is activated.

    That empty-result trigger leaves ONE residual, and it is worth stating because ``all_components``
    MANUFACTURES the state that produces it rather than it being some remote accident: in the
    degraded state the collection is just ``[root]``, so if BOTH the root and the activated
    sub-component hold that name, the main walk returns the root's hit alone, the fallback never
    runs, and ``find_sketch`` RESOLVES to the root's sketch where a walk that saw both would refuse.
    The trade is deliberate - a trigger that fired on a non-empty result would re-ask components the
    main walk already asked, and every repeat visit is a chance to report one component's sketch as
    several - but in that degraded state this list is not the complete answer it is elsewhere."""
    nm = (name or "").strip()
    if not nm:
        return []
    out = []
    for comp in all_components(d):
        sk = safe(lambda c=comp: c.sketches.itemByName(nm))
        if sk is not None:
            out.append((sk, comp))
    if out:
        return out
    seen = set()
    for comp in (target_component(d), safe(lambda: d.rootComponent)):
        if comp is None:
            continue
        sk = safe(lambda c=comp: c.sketches.itemByName(nm))
        if sk is None or id(sk) in seen:
            continue
        seen.add(id(sk))
        out.append((sk, comp))
    return out


# How many candidates a refusal NAMES before it summarizes the rest. A refusal crosses the wire and
# a name can be shared by dozens of components, so the list is capped - and the cap is DISCLOSED,
# because a silently truncated list reads as the COMPLETE set and a caller picking its next call out
# of it would never learn the entry it wanted was cut.
_MAX_NAMED_CANDIDATES = 8


def named_with_remainder(items, cap=_MAX_NAMED_CANDIDATES):
    """``', '``-joined ``items``, capped, with any remainder COUNTED rather than dropped. The ONE
    place a capped wire list is rendered, so no refusal can truncate one silently."""
    head = ", ".join(items[:cap])
    if len(items) > cap:
        head += f", ... (+{len(items) - cap} more not listed)"
    return head


def told_apart(rows):
    """One rendered label per ``(name, discriminator)`` row, in order and one for one - the ONE
    place a listing decides that a repeated name has to be replaced.

    A name no OTHER row carries renders as that NAME: a discriminator there separates nothing that
    was not already separate, and the plain name is what a caller passes back. A name SEVERAL rows
    carry renders as that row's DISCRIMINATOR instead - the fact read beside the name that tells the
    namesakes apart (an occurrence path, a 'Setup / op' breadcrumb). A row whose discriminator is
    empty keeps its name: a listing renders what was READ, and a blank row addresses nothing.

    Every surface that lists things by name meets this: a listing that prints one name twice has
    told the caller a count it could have stated, and nothing else."""
    counts = {}
    for name, _disc in rows:
        counts[name] = counts.get(name, 0) + 1
    return [disc if (counts[name] > 1 and disc) else name for name, disc in rows]


# The way forward a shared-name refusal names when the CALLER offers none of its own. Renaming is
# the only remedy that needs no input vocabulary, and it is the weakest one: a sketch name is shared
# most often because two REFERENCED documents each brought a component of one name, and renaming
# there means opening and editing a different document. A caller that has a scope input of its own
# passes it as ``remedy`` and this never ships.
_RENAME_REMEDY = ("Rename one so the name resolves to a single sketch, then retry (sketch_get lists "
                  "the sketches).")


def find_sketch(d, name, remedy=None):
    """Resolve ONE sketch by name design-wide. Returns (sketch, error_or_None) - the REFUSING form
    of ``resolve_sketch``, for a caller that can return the refusal.

    A name carried by SEVERAL sketches is refused naming each owning component (capped through
    ``named_with_remainder``, which counts what it left out) - and where the OWNERS share a name
    too, by an occurrence that places one of them instead (``_sketch_owner_rows``), since a
    listing that says "in Frame" twice has restated the count and named nothing. A name exactly one
    sketch carries resolves (search order above); a name NO sketch carries is (None, None), so each
    caller keeps wording its own not-found error off ``all_sketch_names``.

    ``remedy`` REPLACES the refusal's closing sentence with the way forward the CALLER can offer -
    a scope input of its own, spelled in that tool's own vocabulary. The census and the naming stay
    here, so no caller re-rolls the part that reads the design; only the sentence naming what to
    pass back differs, because only the caller knows what it accepts."""
    hits = find_sketches_by_name(d, name)
    if len(hits) == 1:
        return hits[0][0], None
    if not hits:
        return None, None
    nm = (name or "").strip()
    named, placements = _sketch_owner_rows(d, nm, hits)
    # The parenthetical enumerates the HITS, one row each, so it is never longer than the count it
    # sits beside. Placements are counted and listed in their own sentence: a component of a shared
    # name is routinely placed more than once, and four addresses inside "2 sketches are named ..."
    # would read as four sketches.
    rows = named_with_remainder([f"'{nm}' in {owner}" for owner in named])
    where = f" ({rows})" if rows else ""
    tail = ""
    if placements:
        one = len(placements) == 1
        tail = (f" Owners sharing a name are not told apart by it - {len(placements)} occurrence"
                f"{'' if one else 's'} place{'s' if one else ''} one holding this sketch: "
                f"{named_with_remainder(placements)}.")
    return None, (f"{len(hits)} sketches are named '{nm}'{where} - sketch names are only unique "
                  f"within a component.{tail} " + (remedy or _RENAME_REMEDY))


def _sketch_owner_rows(d, nm, hits):
    """(owner-NAME rows, PLACEMENT rows) - the two ways a shared-name refusal names an owner, kept
    apart because they count different things.

    A name row stands for ONE hit, so the name rows are never more numerous than the hit count the
    refusal states. A placement row stands for one OCCURRENCE, and a component wearing a shared name
    is routinely placed several times - four placements of two components is the ordinary assembly
    case - so these are counted separately and never folded into a listing that reads as the hits.

    An owner name only ONE hit carries becomes a name row: the name identifies it. An owner name
    SEVERAL hits share becomes one placement row per occurrence that places a component of that name
    holding a sketch of ``nm`` - each independently true, each a spelling the component scopes
    resolve - plus a bare name row for every hit those placements did not cover. Where NO occurrence
    answers, every row stays the repeated name and the refusal says nothing more: that nothing read
    here tells the owners apart is the honest end of the road, not a blank.

    ``told_apart`` makes the substitution and the partition reads its result, so the rule that a
    repeated name gives way to its discriminator lives in one place. The bare padding rows are also
    what keeps a SINGLE readable placement a substitution rather than the lone row of its name,
    which would render as the name again and drop the one address there was."""
    owners = [safe(lambda c=c: c.name) or "(unnamed component)" for _sk, c in hits]
    counts = {o: owners.count(o) for o in owners}
    rows, expanded = [], set()
    for owner in owners:
        if counts[owner] == 1:
            rows.append((owner, None))
            continue
        if owner in expanded:
            continue
        expanded.add(owner)
        paths = placements_holding_sketch(d, owner, nm)
        rows.extend([(owner, p) for p in paths])
        rows.extend([(owner, None)] * max(0, counts[owner] - len(paths)))
    labels = told_apart(rows)
    named = [label for (owner, _d), label in zip(rows, labels) if label == owner]
    placements = [label for (owner, _d), label in zip(rows, labels) if label != owner]
    return named, placements


def _component_is_named(comp, name):
    """True when ``comp``'s OWN name IS ``name`` - case-insensitive and EXACT. The one comparison
    every component SCOPE below runs on, so "does this component exist" and "is this hit inside it"
    can never drift apart. Never a substring test: a 'Frame' scope must not select 'Frame Bracket'."""
    return (safe(lambda: comp.name) or "").strip().lower() == (name or "").strip().lower()


def components_in_scope(d, name):
    """The component(s) a component SCOPE selects, or the refusal for a name no component carries.
    Returns (components, error_or_None); a BLANK name selects every component (no scope asked for).

    One home for the scope, so a scoped LIST and a scoped by-name read select the same components
    and refuse an unknown one with the same sentence - and that sentence names the offending value
    plus the component names that DO exist, which is the whole vocabulary a caller needs to retry."""
    comps = all_components(d)
    want = (name or "").strip()
    if not want:
        return comps, None
    scoped = [c for c in comps if _component_is_named(c, want)]
    if len(scoped) > 1:
        # Case-insensitive matching WIDENS the hit list, and a widened list must not manufacture an
        # ambiguity: when exactly one hit also matches the SPELLING asked for, that one is the answer
        # (_export.find_component and _resolve_any_body narrow the same way). So a design holding
        # 'Beta' and 'BETA' still addresses each by its own spelling, and only 'beta' - which names
        # neither - is left ambiguous. TWO hits spelled exactly as asked stay ambiguous: narrowing
        # there would pick one of two identical names, which is the first-match this whole scope
        # exists to refuse.
        cased = [c for c in scoped if (safe(lambda c=c: c.name) or "") == want]
        if len(cased) == 1:
            scoped = cased
    if scoped:
        return scoped, None
    known = ", ".join(n for n in (safe(lambda c=c: c.name) for c in comps) if n)
    return None, f"No component named '{want}'. Components: {known or '(none)'}."


def spelled_as_read(comps, want):
    """The clause naming how the matched components are ACTUALLY spelled, or '' when every one of
    them is spelled exactly as asked.

    The comparison behind a component scope is case-insensitive and strip-tolerant
    (``_component_is_named``), so "N components are named '<query>'" is a claim the match never
    checked: a design holding 'Beta' and 'BETA' answers a query of 'beta' with two components, and
    NEITHER is named 'beta'. The spellings AS READ are both the honest form and the discriminating
    information - they are the only thing that tells the hits apart, and the caller needs one of them
    to re-issue. De-duplicated, because nine 'Pin' beside one 'PIN' is two spellings, not ten, and
    capped through ``named_with_remainder`` like every other list that crosses the wire."""
    names = []
    for c in comps:
        n = safe(lambda c=c: c.name)
        if n is not None and n not in names:
            names.append(n)
    if not names or names == [want]:
        return ""
    quoted = ["'" + n + "'" for n in names]
    return f" (named {named_with_remainder(quoted)})"


def component_placements(d):
    """[(occurrence fullPathName, that occurrence's component)] for every occurrence in the design -
    the ONE pairing of a component with WHERE it is placed.

    Both halves come from the SAME occurrence, so the path is known by construction and nothing here
    ever asks whether two components are the same one. That is the whole point of the shape.

    MEASURED on a host holding two inserted references: two DISTINCT components, one out of each
    reference, both named 'Frame', read BYTE-IDENTICAL entityTokens
    ('/v4BAAEAegEAAAAAAAAAAAAA', 24 chars, both read without raising). ``entityToken`` is
    document-LOCAL, so ``same_component`` answers True for that pair and ANY grouping keyed on it
    merges them - which is exactly how each 'Frame' came to claim the other 'Frame''s path. The
    repo-wide identity problem is tracked separately; this pairing simply never poses the question.

    An occurrence whose component or path will not read is omitted: a placement missing either half
    identifies nothing."""
    out = []
    for occ in all_occurrences(d):
        comp = safe(lambda o=occ: o.component)
        path = safe(lambda o=occ: o.fullPathName)
        if comp is not None and path:
            out.append((path, comp))
    return out


def placement_paths_named(d, name):
    """The fullPathName of every occurrence placing a component whose OWN name matches ``name``
    (through ``_component_is_named``, the one component-scope comparison), in walk order.

    This answers "which placements answer to this name" - NOT "which placements hold this exact
    component", which nothing readable can answer while two distinct components share one
    entityToken. Every path is therefore honest on its own: it does place a component of that name,
    and passing it back as a scope resolves to ONE of them. Each occurrence contributes once, so a
    name worn by two components yields two paths, not each component's copy of both."""
    return [p for p, c in component_placements(d) if _component_is_named(c, name)]


def placements_holding_sketch(d, owner_name, sketch_name):
    """The fullPathName of every occurrence whose OWN component answers to ``owner_name`` AND hands
    back a sketch named ``sketch_name`` - the address that separates two components wearing one name.

    Both halves are read off the SAME occurrence, so each path is true on its own: that placement
    does hold a sketch of that name, and it resolves to ONE component. It is deliberately NOT "this
    hit's placement" - nothing readable pairs a hit against the occurrence walk while two distinct
    components report one entityToken (measured - see ``component_placements``), so a path is
    offered only for what it independently answers.

    The sketch half is what keeps the row honest rather than merely plausible: a THIRD component of
    that name holding no such sketch is placed too, and listing it would name an owner that owns
    nothing."""
    want = (sketch_name or "").strip()
    if not want:
        return []
    return [p for p, c in component_placements(d)
            if _component_is_named(c, owner_name)
            and safe(lambda c=c: c.sketches.itemByName(want)) is not None]


def find_sketch_in(d, name, comp, label, input_name="component"):
    """Resolve ONE sketch by name inside ONE ALREADY-RESOLVED component. Returns
    (sketch, error_or_None); every failure is WORDED here, because only this call knows which
    component it looked in. ``label`` is the scope exactly as the caller spelled it, so each refusal
    quotes back the offending value.

    ``input_name`` is the scope INPUT the refusal tells the caller to retry with. A tool taking two
    sketch references carries two scopes, and a tool may spell its scope something else entirely
    ('boundary_component', 'dxf_component') - those tools declare a strict schema and carry no
    'component' input at all, so a hardcoded name is a remedy the schema itself rejects.

    The sketch comes from ``comp``'s OWN collection - the same ``sketches.itemByName`` that
    ``find_sketches_by_name`` asks each component, so the scoped read sees exactly what the
    design-wide one does. It deliberately does NOT filter design-wide hits down by comparing
    components: two distinct components can read one entityToken (measured - see
    ``component_placements``), so an identity filter answers True for the wrong component's hit, and
    a NAME filter cannot separate them either. Asking the resolved component itself removes the
    question: whichever component the scope resolved to is the one whose collection answers.

    The two misses are worded apart on purpose. A name other components DO carry names those
    components - that is the answer to "where is it". A name nothing in the design carries lists
    THIS component's sketches and no other's: answering a scoped miss with a design-wide 'Available'
    list hands the caller sketches it just excluded, which reads as a suggestion to call something
    that will not resolve either."""
    nm = (name or "").strip()
    found = safe(lambda: comp.sketches.itemByName(nm)) if nm else None
    if found is not None:
        return found, None
    hits = find_sketches_by_name(d, nm)
    # Name the component AS READ, not as the caller spelled it: 'label' may be a different casing of
    # the name (the scope match is case-insensitive) or an occurrence PATH, and neither is a name any
    # component carries. The scope is still echoed when it differs, since it is the offending value.
    read_name = safe(lambda: comp.name)
    shown = f"'{read_name}'" if read_name else "the scoped component"
    via = "" if (read_name or "") == label else f" (scope '{label}')"
    if hits:
        owners = named_with_remainder([f"'{safe(lambda c=c: c.name) or '(unnamed component)'}'"
                                       for _sk, c in hits])
        return None, (f"Component {shown}{via} holds no sketch named '{nm}' - that name is in "
                      f"{owners}. Retry with one of those as '{input_name}'.")
    held = [n for n in (safe(lambda s=s: s.name)
                        for s in iter_collection(safe(lambda: comp.sketches))) if n]
    return None, (f"Component {shown}{via} holds no sketch named '{nm}'. Component {shown} holds: "
                  + (named_with_remainder(held) or "(no sketches)") + ".")


def resolve_sketch(d, name):
    """Resolve a sketch BY NAME across the whole design - the ONE true resolver every by-name sketch
    tool should use. Returns the live Sketch, or None both when NO sketch carries the name and when
    SEVERAL do: a shared name is refused, never first-matched. A caller that can surface WHY calls
    ``find_sketch`` instead, which carries the refusal text naming each owning component.

    Scope (``find_sketches_by_name``): EVERY component, each asked for its own sketch of that name -
    so a sketch drawn in an activated SUB-component is reachable, not only one in the root. There is
    no preference order among them, because there is nothing for an order to decide: a name several
    components carry is refused rather than resolved to whichever came first."""
    return find_sketch(d, name)[0]


def find_or_recent_sketch(d, name, remedy=None):
    """The ONE name-or-default sketch contract, in its REFUSING form: a NAME resolves DESIGN-WIDE
    via ``find_sketch`` (every component asked for its own sketch of that name - a root master sketch
    stays reachable from an activated sub-component); an EMPTY name means the most recently created
    sketch in the ACTIVE component. Returns (sketch-or-None, the stripped requested name or None
    when blank, ambiguity_error-or-None).

    The third value is what separates the two ways a NAME fails to identify a sketch: a name NO
    sketch carries comes back (None, name, None) and the caller words its own not-found error, while
    a name SEVERAL sketches carry comes back (None, name, the refusal naming each owning component)
    and the caller returns that text - a caller collapsing both into "no sketch named X" states the
    opposite of what was read.

    ``remedy`` is handed to ``find_sketch`` unchanged - the closing sentence of the shared-name
    refusal, in the calling tool's own input vocabulary."""
    nm = (name or "").strip()
    if nm:
        sk, ambiguous = find_sketch(d, nm, remedy)
        return sk, nm, ambiguous
    coll = safe(lambda: target_component(d).sketches)
    n = safe(lambda: coll.count, 0) if coll is not None else 0
    return (coll.item(n - 1) if n else None), None, None


def resolve_or_recent_sketch(d, name):
    """The same name-or-default contract with the ambiguity refusal DROPPED: a name SEVERAL sketches
    carry answers None like a name none carries, since neither one identifies a sketch. Returns
    (sketch-or-None, the stripped requested name or None when blank).

    For a caller that returns a message, ``find_or_recent_sketch`` above is the form to call - it
    hands back the refusal naming each owning component. This one is for a caller with nowhere to
    put it (a postcondition fingerprint)."""
    sketch, requested, _ambiguous = find_or_recent_sketch(d, name)
    return sketch, requested


def all_sketch_names(d):
    """Every sketch name across the design (all components), for 'Available: ...' error messages -
    so a not-found message lists sketches wherever they live, not just in the root component.

    A name SEVERAL sketches carry is rendered QUALIFIED by its owning component ("Plate (Alpha)",
    "Plate (Beta)"); a name only one sketch carries stays bare - the substitution ``told_apart``
    makes everywhere. Sketch names are unique only within a component, so a repeated bare name reads
    as one sketch listed twice. A component whose own name will not read leaves its entry bare,
    since there is nothing measured to qualify it with. Still a flat list of strings: every caller
    joins it as it already did."""
    pairs = []
    for comp in all_components(d):
        coll = safe(lambda c=comp: c.sketches)
        owner = safe(lambda c=comp: c.name)
        for i in range(safe(lambda: coll.count, 0) if coll else 0):
            nm = safe(lambda i=i, cl=coll: cl.item(i).name)
            if nm:
                pairs.append((nm, owner))
    return told_apart([(nm, f"{nm} ({owner})" if owner else None) for nm, owner in pairs])


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


class _HealthName(str):
    """One unhealthy timeline item's name, carrying the identity a name is not.

    Timeline item names are NOT unique across components - a two-component design carries two
    'Sketch1' and two 'Extrude1' (measured) - so a before/after difference keyed on the bare name
    is blind to damage done to a feature whose name also exists healthy elsewhere, and counts the
    opposite case twice. This reads, formats, joins and JSON-serializes exactly as the name, so
    every consumer keeps printing the human-facing label, while ``==`` (and through it ``in`` and
    set difference) compares the timeline row's ENTITY token whenever both sides carry one. An item
    with no readable token falls back to comparing the name.

    ``__hash__`` stays the NAME's hash: two same-named rows share one bucket and ``__eq__``
    separates them there, which keeps "a == b implies hash(a) == hash(b)" true against a plain
    ``str`` as well, so a set of these and a set of names still interoperate.
    """

    def __new__(cls, name, token=None):
        obj = str.__new__(cls, name)
        obj.token = token or None
        return obj

    def __eq__(self, other):
        mine, theirs = self.token, getattr(other, "token", None)
        if mine and theirs:
            return mine == theirs
        return str.__eq__(self, other)

    def __ne__(self, other):
        same = self.__eq__(other)
        return same if same is NotImplemented else not same

    __hash__ = str.__hash__


def timeline_health(design, limit=None):
    """(error_names, warning_names, total) over the parametric timeline by healthState (2=error,
    1=warning) - the shared before/after guard for edits that can break downstream features, so a
    change that corrupts the model is reported instead of swallowed. A direct-modelling design
    (no timeline) yields empty lists.

    Each returned name is a ``_HealthName``: the human-facing label, compared on the row's entity
    token. The delta every guard runs over these lists - ``[n for n in after if n not in before]``,
    ``set(after) - set(before)`` - therefore separates two features that SHARE a name in different
    components and names only the one that actually broke; a row with no readable token is keyed on
    its name.

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
        if hs not in (1, 2):
            continue
        # A TimelineObject carries no entityToken of its own - the ENTITY it stands for does, and
        # that token reads back identical across a re-read in the same session (measured), which is
        # exactly the before/after window a guard compares over. TimelineObject.entity is None for
        # a TimelineGroup row and for a feature class with no public-API representation, so the
        # read is through safe() and such a row keys on its name.
        label = _HealthName(safe(lambda it=it: it.name) or f"#{i}",
                            safe(lambda it=it: it.entity.entityToken))
        (errors if hs == 2 else warnings).append(label)
    return errors, warnings, total


# A before/after volume difference (cm3) smaller than this is NO CHANGE - the ONE band every
# material-changing feature judges "the API reported success but nothing moved" against. One home so
# a fillet, an extrude, a thread and a pipe cannot disagree about what a zero is; a site whose signal
# is not a volume (a bounding-box extent, a displacement) keeps its own named tolerance.
NO_VOLUME_CHANGE_CM3 = 1e-9


# The band a landed extent may differ from the requested one by and still be the same length: both
# numbers are internal cm - one the units engine produced, one a ModelParameter reports - so the
# band only absorbs their float representation, not a real depth difference.
EXTENT_MATCH_TOL_CM = 1e-6


def landed_extent_cm(feature):
    """The depth an extrude-family feature REPORTS for its first side, in internal cm - or None when
    no number reads, which withholds a comparison rather than judging the feature against zero. The
    ONE such read, so a solid extrude and a surface extrude cannot judge one feature differently.

    MEASURED: extentOne is a DistanceExtentDefinition, and a symmetric extrude's is a
    SymmetricExtentDefinition; both carry .distance as a ModelParameter whose .value reads cm and
    keeps the requested SIGN (-15 mm reads -1.5). A symmetric extent reads the per-SIDE number that
    was requested (10 mm reads 1.0) and a taper does not perturb it. A CUT reads the REQUESTED
    distance, not one clipped to the material consumed (3 mm into a 10 mm cube reads 0.3), so the
    comparison cannot false-refuse a cut that bottomed out inside a body."""
    got = safe(lambda: feature.extentOne.distance.value)
    if isinstance(got, (int, float)) and not isinstance(got, bool):
        return float(got)
    return None


def landed_extent2_cm(feature):
    """The depth an extrude-family feature REPORTS for its SECOND side, in internal cm - or None
    when no number reads, which withholds a comparison rather than judging the feature against
    zero. The sibling of landed_extent_cm, and the ONE such read: a one-sided feature has no
    extentTwo at all, which answers None here rather than raising.

    MEASURED: a two-sided distance extent puts side one on extentOne and side two on extentTwo -
    no swap - and each side's .distance ModelParameter reports the magnitude THAT side was asked
    for (1.0 cm and 0.5 cm requested read 1.0 and 0.5, both positive). That measurement covers
    POSITIVE requests; what these parameters store for a NEGATIVE two-sided request is not
    measured, so a caller comparing against them gates on the requested sign itself."""
    got = safe(lambda: feature.extentTwo.distance.value)
    if isinstance(got, (int, float)) and not isinstance(got, bool):
        return float(got)
    return None


# ── unit scaling (Fusion's internal length unit is cm) ──────────────────────

UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
CM_TO_UNIT = {u: 1.0 / f for u, f in UNIT_TO_CM.items()}


def scale(units: str):
    """cm-per-unit factor for ``units`` (mm/cm/in), or None if the unit is unknown."""
    return UNIT_TO_CM.get((units or "mm").strip().lower())


def ptxyz(p, f):
    """{x, y, z} for a Point3D ``p``, each scaled by ``f`` and rounded to 6 decimals - or None when
    ``p`` is None or ANY of its three components will not read.

    The whole-POINT counterpart of ``measured``: a point is one answer, so a 0.0 stand-in for a
    component that did not read publishes the world origin (or a point one third invented) as a
    measured coordinate, and 0 is an answer here - "on the origin", "on the XY plane". The tri-state
    is per point rather than per component because every consumer navigates to / measures from the
    point as a whole."""
    if p is None:
        return None
    x, y, z = safe(lambda: p.x), safe(lambda: p.y), safe(lambda: p.z)
    if not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in (x, y, z)):
        return None
    return {"x": round(x * f, 6), "y": round(y * f, 6), "z": round(z * f, 6)}


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
