# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Typed POSTCONDITION kinds: the state-side mirror of ``_outputs.OutputKind``.

An Edit tool declares ``postconditions=[...]`` at registration (Item.create_tool_item); the kernel
runs capture -> handler -> verify and converts an ok() whose declared effect did not take into an
error - the platform can return success while changing nothing. Severities: "hard" (reason ->
isError; and a capture/verify that RAISES fails CLOSED - the honest "mutation may have succeeded,
verification could not run" error, never a possible no-op passed as ok) and "soft" (payload marked
unconfirmed, for effects that legitimately lag the call). Evidence a verify reads is folded into
the payload via setdefault, so a declared RETURNS key can be SUPPLIED by its postcondition rather
than computed twice. A Postcondition NEVER mutates: capture/verify are safe() reads. See
tools/CLAUDE.md for the authoring rule.
"""

import json

import adsk.core

from ._common import measured, safe

app = adsk.core.Application.get()

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("POSTCONDITION kinds (VersionAdvanced/ReferencesFresh/FileLanded/...) - declare an Edit "
             "tool's verify-the-effect once; wired via Item.create_tool_item(postconditions=[...])")


class Postcondition:
    """One declared 'this must be true after the mutation'. Subclasses implement capture()/verify();
    ``name`` keys the evidence and the lint inventory; ``severity`` is 'hard' (fail the call) or
    'soft' (mark unconfirmed - for async cloud effects that may lag the call)."""

    name = "postcondition"
    severity = "hard"
    # Handler PARAMETER names this kind reads out of kwargs. wrap() checks them against the
    # handler's own signature, so a kind pointed at a parameter the handler does not take fails at
    # registration instead of silently reading None and verifying the wrong thing.
    input_keys = ()
    # The MCP read tool that re-reads THIS postcondition's ground truth, if one exists. Named in the
    # honest error when verification itself could not run (capture/verify raised) so the caller knows
    # where to re-check the state. None = no single read tool reveals it (e.g. a file on disk).
    read_tool = None

    def capture(self, kwargs):
        """Ground truth BEFORE the handler runs (kwargs = the handler's arguments). Return any value;
        it is handed back to verify(). Default: nothing to capture."""
        return None

    def verify(self, kwargs, payload, before):
        """Re-read ground truth AFTER an ok() result. Returns (reason, evidence): reason '' = the
        effect is confirmed; a non-empty reason names the observed fact that contradicts success.
        evidence is a dict folded into the payload (setdefault - the handler's own values win)."""
        return "", {}

    def describe(self) -> str:
        """One line for generated docs / the lint inventory."""
        return self.name


class VersionAdvanced(Postcondition):
    """After a save: the active document is no longer modified. Catches the observed platform lie
    where Document.save() returns True while versioning NOTHING (document demoted to non-top-level -
    e.g. a design open behind its drawing, or a stale duplicate instance)."""

    name = "version_advanced"
    read_tool = "doc_get"        # active.is_modified + include=['versions'] re-read the save state

    def capture(self, kwargs):
        return bool(safe(lambda: app.activeDocument.isModified, False))

    def verify(self, kwargs, payload, before):
        if payload.get("already_current"):
            return "", {}                    # clean-doc no-op: nothing was supposed to change
        still = safe(lambda: app.activeDocument.isModified)
        if still is None:
            return "", {"version_confirmed": False}
        if still:
            return ("save reported success but the document is STILL modified - Fusion created no "
                    "version. This happens when the document is open as another document's reference "
                    "(e.g. a design open behind its drawing) or a stale duplicate instance is open. "
                    "Close the referencing/duplicate document, reopen this one top-level, then save."), {}
        return "", {"version_confirmed": True}


class ReferencesFresh(Postcondition):
    """After a reference refresh: no DocumentReference on the active document is still out of date.
    Gates on the per-reference isOutOfDate walk - DrawingDocument.isUpToDate reports True even while
    a reference is stale (observed live), so it is deliberately not consulted."""

    name = "references_fresh"
    read_tool = "doc_get"        # include=['xref_tree'] re-reads per-reference freshness/stale_count

    def _stale_count(self):
        refs = safe(lambda: app.activeDocument.documentReferences)
        if refs is None:
            return None
        count = safe(lambda: refs.count, 0) or 0
        stale = 0
        for i in range(count):
            if safe(lambda k=i: refs.item(k).isOutOfDate):
                stale += 1
        return stale

    def capture(self, kwargs):
        return self._stale_count()

    def verify(self, kwargs, payload, before):
        stale = self._stale_count()
        if stale is None:
            return "", {"references_confirmed": False}
        if stale:
            return (f"refresh ran but {stale} reference(s) are STILL out of date - the references did "
                    "not fully update."), {}
        return "", {"stale_references_after": 0}


class FileLanded(Postcondition):
    """After an export: a non-empty file exists at the payload's path key. execute()/postProcess()
    returning true is NOT proof a file was written (observed live) - the file on disk is. Supplies
    size_bytes/file_exists as evidence so handlers don't re-stat."""

    name = "file_landed"

    def __init__(self, key="file_path"):
        self.key = key

    def verify(self, kwargs, payload, before):
        path = payload.get(self.key)
        if not path:
            return f"no '{self.key}' in the result to verify a written file against.", {}
        from . import _export
        size, verr = _export.verify_written(path)
        if verr:
            return (f"the tool reported success but {verr}. The API returned true but produced "
                    "nothing on disk."), {}
        return "", {"file_exists": True, "size_bytes": size}

    def describe(self) -> str:
        return f"{self.name}({self.key})"


class DeliverablesExist(Postcondition):
    """REDUNDANT gate for export tools whose handler builds its own deliverables list: every file the
    payload CLAIMS - each entry of a 'files' list (by its path_key) or a single 'file_path' - must
    exist non-empty on disk. The handler's inline verification stays (it constructs the payload);
    this catches a claim that drifted from reality (a handler bug, a path typo between stat and
    payload, a file gone between). An ok payload claiming NEITHER key is itself a failure - an export
    that reports success with no deliverable claim is exactly a false success."""

    name = "deliverables_exist"

    def __init__(self, list_key="files", path_key="file_path", single_key="file_path"):
        self.list_key = list_key
        self.path_key = path_key
        self.single_key = single_key

    def verify(self, kwargs, payload, before):
        from . import _export
        paths = []
        entries = payload.get(self.list_key)
        if isinstance(entries, list):
            for i, it in enumerate(entries):
                p = it.get(self.path_key) if isinstance(it, dict) else None
                if not p:
                    return (f"'{self.list_key}[{i}]' claims a deliverable but carries no "
                            f"'{self.path_key}' to verify."), {}
                paths.append(p)
        elif payload.get(self.single_key):
            paths.append(payload[self.single_key])
        else:
            return (f"the result claims success but names no deliverable ('{self.list_key}' or "
                    f"'{self.single_key}') to verify on disk."), {}
        for p in paths:
            _size, verr = _export.verify_written(p)
            if verr:
                return f"claimed deliverable '{p}': {verr}.", {}
        return "", {"deliverables_verified": len(paths)}

    def describe(self) -> str:
        return f"{self.name}({self.list_key}|{self.single_key})"


class FeatureHealthy(Postcondition):
    """After a feature-creating Edit: every timeline item the handler ADDED computed cleanly. A
    feature can be add()ed successfully - a truthy feature object returned - yet FAIL to compute
    (healthState error: the timeline's yellow/red mark), so the returned object is not proof. Walks
    only the items added between capture and verify. A design with no timeline (direct modeling) or
    a call that added no timeline items is skipped, not failed; a compute WARNING is folded as
    evidence rather than failing the call."""

    name = "feature_healthy"
    read_tool = "design_get"      # the default projection carries the timeline health rollup

    _ERROR, _WARNING = 2, 1        # timeline healthState convention (same values design_get labels)

    def _timeline(self):
        from ._common import design
        d = design()
        return safe(lambda: d.timeline) if d else None

    def capture(self, kwargs):
        tl = self._timeline()
        return safe(lambda: tl.count) if tl is not None else None

    def verify(self, kwargs, payload, before):
        tl = self._timeline()
        if tl is None or before is None:
            return "", {}                    # no timeline to walk (e.g. direct modeling)
        count = safe(lambda: tl.count)
        if count is None or count <= before:
            return "", {}                    # nothing new on the timeline - nothing to gate
        warnings = []
        for i in range(before, count):
            item = safe(lambda k=i: tl.item(k))
            if item is None:
                continue
            hs = safe(lambda: item.healthState)
            nm = safe(lambda: item.name) or "the created feature"
            msg = safe(lambda: item.errorOrWarningMessage) or ""
            if hs == self._ERROR:
                return ((f"'{nm}' was created but FAILED to compute. " + msg).strip()[:300]
                        + " It remains in the timeline - fix its inputs or remove it with "
                          "design_delete_feature."), {}
            if hs == self._WARNING:
                warnings.append((nm + ": " + msg).strip().rstrip(":")[:160])
        evidence = {"features_verified": count - before}
        if warnings:
            evidence["feature_warnings"] = warnings
        return "", evidence


def _xyz(point):
    """(x, y, z) in cm, rounded - or None when any component is unreadable (never a zero corner)."""
    out = []
    for axis in ("x", "y", "z"):
        v = safe(lambda axis=axis: getattr(point, axis))
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
        out.append(round(float(v), 7))
    return tuple(out)


def entity_position(entity):
    """A position fingerprint for ONE sketch entity: its bounding-box corners in cm PLUS its
    start/end sketch-point coordinates when it has them. Measured: a sketch point carries a
    (degenerate) boundingBox too, so the box alone covers every sketch entity - but a box is
    INVARIANT under any symmetry of the thing it bounds, so a line rotated 180 degrees about its own
    midpoint reads identical while Fusion really did move it. The endpoints break exactly that tie
    (they swap), which is why both go into the fingerprint. This is the one sampler a sketch-move's
    own coordinate read-back and SketchCurvesChanged's fingerprint share, so the handler's verdict
    and the declared postcondition read the same ground truth. None when nothing can be read.
    A READ - it never mutates."""
    marks = []
    bb = safe(lambda: entity.boundingBox)
    for get_pt in (lambda: bb.minPoint, lambda: bb.maxPoint):
        p = safe(get_pt) if bb is not None else None
        marks.append(_xyz(p) if p is not None else None)
    for get_pt in (lambda: entity.startSketchPoint.geometry,
                   lambda: entity.endSketchPoint.geometry,
                   lambda: entity.geometry):
        # a curve carries start/end; a SketchPoint carries only .geometry; a circle/ellipse neither
        p = safe(get_pt)
        marks.append(_xyz(p) if p is not None else None)
    return tuple(marks) if any(m is not None for m in marks) else None


class SketchCurvesChanged(Postcondition):
    """After a sketch edit: the target sketch's entity set differs. Keyed entityToken ->
    (length in cm, position), over the sketch's CURVES and its POINTS - a point-only edit (moving
    'point:0') touches no curve at all, so a curves-only walk would call it a no-op. An in-place
    extend (same curve, longer) registers as readily as an add or a delete, a spline split registers
    even though the count is unchanged, and so does a pure TRANSLATION, which changes neither the
    token nor the length. ``keys`` names the handler kwargs holding the sketch to read, in priority
    order: a copy into another sketch must verify its TARGET, since the source it copied FROM is left
    untouched. Resolves through the same name-or-most-recent contract the handler uses. NEVER
    mutates - capture/verify are safe() reads."""

    name = "sketch_curves_changed"
    read_tool = "sketch_get"

    def __init__(self, keys=("sketch_name",)):
        self.keys = tuple(keys)
        # the handler parameters this kind reads; wrap() refuses a name the handler does not take,
        # so a typo'd key cannot silently fall through to the most-recent sketch.
        self.input_keys = self.keys

    def _fingerprint(self, kwargs):
        from ._common import design, resolve_or_recent_sketch
        d = design()
        if d is None:
            return None
        wanted = ""
        for key in self.keys:
            wanted = (kwargs.get(key) or "").strip()
            if wanted:
                break
        sketch, _requested = resolve_or_recent_sketch(d, wanted)
        if sketch is None:
            return None
        marks = {}
        curves = safe(lambda: sketch.sketchCurves)
        n = (safe(lambda: curves.count, 0) or 0) if curves is not None else 0
        for i in range(n):
            c = safe(lambda i=i: curves.item(i))
            if c is None:
                continue
            marks[("curve", safe(lambda c=c: c.entityToken) or f"#{i}")] = (
                measured(lambda c=c: c.length, 1.0, 7), entity_position(c))
        points = safe(lambda: sketch.sketchPoints)
        m = (safe(lambda: points.count, 0) or 0) if points is not None else 0
        for i in range(m):
            p = safe(lambda i=i: points.item(i))
            if p is None:
                continue
            # a SketchPoint carries no .length (measured), so position is its whole fingerprint
            marks[("point", safe(lambda p=p: p.entityToken) or f"#{i}")] = (None, entity_position(p))
        return {"marks": marks, "curves": n}

    def describe(self) -> str:
        return f"{self.name}({'|'.join(self.keys)})"

    def capture(self, kwargs):
        return self._fingerprint(kwargs)

    def verify(self, kwargs, payload, before):
        after = self._fingerprint(kwargs)
        if before is None or after is None:
            return "", {}                    # no sketch to read - nothing to gate
        if after["marks"] == before["marks"]:
            return ("the edit reported success but the sketch's entities are unchanged - nothing was "
                    "added, removed, shortened, lengthened or moved."), {}
        return "", {"curve_count_after": after["curves"]}


class ChildGeometryMoved(Postcondition):
    """After a joint mutation: a part the joint REPOSITIONED carried its NESTED geometry with it. A
    transform is a CLAIM; a body vertex/bbox corner is EVIDENCE. Per top-level occurrence, this captures
    its transform translation plus a WORLD-space point on its DEEPEST owned body (the nested child if
    one exists); after the mutation, an occurrence whose transform TRANSLATED while that geometry point
    stayed frozen is a reposition that did not propagate into the nested geometry - failed, naming the
    direct-':origin'-snap workaround. A part that did not move (expected-zero) passes trivially, so this
    is a no-op on the overwhelming majority of joints. Reads geometry back, never a status flag or the
    transform itself. NEVER mutates - capture/verify are safe() reads."""

    name = "child_geometry_moved"
    read_tool = "find_geometry"          # re-read the GEOMETRY (a vertex/bbox), not assembly_get's transform

    _MOVE_TOL_CM = 0.01                   # 0.1 mm - below this a "move" is joint-solver noise

    def _translation(self, occ):
        m = safe(lambda: occ.transform)
        t = safe(lambda: m.translation) if m is not None else None
        if t is None:
            return None
        return (safe(lambda: t.x, 0.0) or 0.0, safe(lambda: t.y, 0.0) or 0.0,
                safe(lambda: t.z, 0.0) or 0.0)

    def _deep_body_point(self, occ):
        """A WORLD-space bbox-min corner of a body on occ's DEEPEST descendant occurrence that owns one
        (else occ's own first body). The nested child is the propagation the observed defect dropped, so
        the deepest owned body is the discriminating sample; a world bbox corner registers a translation
        OR a rotation as movement, so a legitimate reposition never reads frozen. None if no body is
        reachable (nothing to gate)."""
        best, best_depth = None, -1
        stack = [(occ, 0)]
        while stack:
            cur, depth = stack.pop()
            bodies = safe(lambda cur=cur: cur.bRepBodies)
            bcount = (safe(lambda: bodies.count, 0) or 0) if bodies is not None else 0
            if bcount and depth > best_depth:
                best, best_depth = cur, depth
            children = safe(lambda cur=cur: cur.childOccurrences)
            ccount = (safe(lambda: children.count, 0) or 0) if children is not None else 0
            for i in range(ccount):
                ch = safe(lambda i=i, children=children: children.item(i))
                if ch is not None:
                    stack.append((ch, depth + 1))
        if best is None:
            return None
        body = safe(lambda: best.bRepBodies.item(0))
        bb = safe(lambda: body.boundingBox) if body is not None else None
        mn = safe(lambda: bb.minPoint) if bb is not None else None
        if mn is None:
            return None
        return (safe(lambda: mn.x, 0.0) or 0.0, safe(lambda: mn.y, 0.0) or 0.0,
                safe(lambda: mn.z, 0.0) or 0.0)

    def _top_occurrences(self):
        from ._common import design
        d = design()
        root = safe(lambda: d.rootComponent) if d else None
        occs = safe(lambda: root.occurrences) if root is not None else None
        n = (safe(lambda: occs.count, 0) or 0) if occs is not None else 0
        return [safe(lambda i=i: occs.item(i)) for i in range(n)]

    @staticmethod
    def _dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

    def capture(self, kwargs):
        entries = []
        for occ in self._top_occurrences():
            if occ is None:
                continue
            tr = self._translation(occ)
            pt = self._deep_body_point(occ)
            if tr is None or pt is None:
                continue
            entries.append((occ, tr, pt))
        return entries

    def verify(self, kwargs, payload, before):
        if not before:
            return "", {}                     # no occurrence carried a body to gate
        tol = self._MOVE_TOL_CM
        for occ, tr0, pt0 in before:
            tr1 = self._translation(occ)
            pt1 = self._deep_body_point(occ)
            if tr1 is None or pt1 is None:
                continue                       # cannot re-read this one - inconclusive, skip
            parent_moved = self._dist(tr0, tr1)
            child_moved = self._dist(pt0, pt1)
            if parent_moved > tol and child_moved <= tol:
                nm = safe(lambda: occ.name) or "a repositioned part"
                return (f"the joint reported success and repositioned '{nm}' by "
                        f"{round(parent_moved * 10.0, 3)} mm (its transform moved) but its nested body "
                        "geometry did NOT move - the reposition did not propagate into the nested "
                        "occurrence (trigger: a nested occurrence left FREE/unconstrained inside the "
                        "referenced design does not ride the wrapper's move; a timeline-locked one "
                        "does). The transform is a CLAIM; the child body point is the EVIDENCE. The "
                        "joint REMAINS in the timeline - delete it, then LOCK every nested free "
                        "occurrence first (assembly_ground each ground_to_parent=true, deepest "
                        "included), joint the WRAPPER, and recompute. Jointing the nested occurrence "
                        "directly does not work - joint_create repositions the top-most free "
                        "ancestor, stranding deeper geometry."), {}
        return "", {"child_geometry_move_verified": True}


def _verification_failed(post, ex):
    """The HONEST fail-closed result when a HARD postcondition's capture or verify RAISED, so the
    effect could not be checked. The mutation may have taken; its VERIFICATION did not run - reported
    as an error (never a possible no-op passed as ok), naming the exception and, when the kind knows
    one, the read tool that re-reads the state."""
    detail = str(ex)[:160]
    tool = getattr(post, "read_tool", None)
    reread = (f"Re-read with {tool} and retry only if the change did not take."
              if tool else "Re-read the affected state and retry only if the change did not take.")
    note = (f"The mutation may have succeeded, but its verification could not run ({detail}). "
            f"Reporting failure rather than a possible no-op passed as success. " + reread)
    return {"content": [{"type": "text", "text": json.dumps(
                {"postcondition": post.name, "verification_error": detail, "note": note}, indent=2)}],
            "isError": True,
            "message": f"{post.name}: verification could not run - {detail}"}


def _check_input_keys(handler, posts):
    """Refuse a postcondition pointed at a handler parameter that does not exist. Such a key reads
    None out of kwargs and the kind falls back to its own default target - here, the most recently
    created sketch - so the call would verify the WRONG state and still report success. Raised at
    registration (import time), where it is a loud wiring bug rather than a silent wrong verdict."""
    import inspect
    try:
        params = set(inspect.signature(handler).parameters)
    except (TypeError, ValueError):
        return                       # an unintrospectable callable - nothing to check against
    for post in posts:
        unknown = [k for k in getattr(post, "input_keys", ()) or () if k not in params]
        if unknown:
            raise ValueError(
                f"postcondition {post.name} reads handler argument(s) {', '.join(unknown)}, which "
                f"{getattr(handler, '__name__', 'the handler')} does not take "
                f"({', '.join(sorted(params)) or 'no parameters'}). It would verify the wrong state.")


def wrap(handler, postconditions):
    """Wrap an Edit handler with capture -> handler -> verify. Runs verify only on a JSON ok() result;
    error results and non-JSON payloads pass through untouched. Applied INSIDE _write_guard.wrap (the
    guard stamps acted_on on whatever this returns).

    Fail-CLOSED for HARD postconditions: a capture that RAISED (so the before/after baseline is gone)
    or a verify that RAISED (so ground truth is unreadable) means verification is IMPOSSIBLE - the call
    returns isError with honest wording, never a possible no-op passed as ok. SOFT postconditions keep
    the non-fatal annotation. The mutation is never rolled back - the error reports the uncertainty
    honestly and points at the re-read instead."""
    posts = list(postconditions or [])
    if not posts:
        return handler
    _check_input_keys(handler, posts)

    def _capture(p, kwargs):
        try:
            return p.capture(kwargs), None
        except Exception as ex:     # a raising capture leaves no baseline - recorded, not swallowed
            return None, ex

    def asserted(**kwargs):
        before = [_capture(p, kwargs) for p in posts]
        result = handler(**kwargs)
        if not isinstance(result, dict) or result.get("isError"):
            return result
        content = result.get("content")
        if not (isinstance(content, list) and content and isinstance(content[0], dict)
                and content[0].get("type") == "text"):
            return result
        try:
            payload = json.loads(content[0]["text"])
        except Exception:
            return result
        if not isinstance(payload, dict):
            return result

        for p, (b, cap_ex) in zip(posts, before):
            soft = (p.severity == "soft")
            # A capture that raised makes verification impossible; a HARD one fails closed.
            if cap_ex is not None:
                if soft:
                    payload.setdefault(p.name + "_confirmed", False)
                    payload.setdefault("verify_error", ("capture failed: " + str(cap_ex))[:120])
                    continue
                return _verification_failed(p, cap_ex)
            try:
                reason, evidence = p.verify(kwargs, payload, b)
            except Exception as ex:
                # A raising verify cannot read ground truth: HARD fails closed, SOFT annotates.
                if soft:
                    payload.setdefault(p.name + "_confirmed", False)
                    payload.setdefault("verify_error", str(ex)[:120])
                    continue
                return _verification_failed(p, ex)
            if reason:
                if soft:
                    payload.setdefault("verified", {})[p.name] = {"confirmed": False,
                                                                  "reason": reason}
                    continue
                return {"content": [{"type": "text", "text": json.dumps(
                            {"postcondition": p.name, "note": reason}, indent=2)}],
                        "isError": True,
                        "message": f"{p.name}: {reason}"}
            for k, v in (evidence or {}).items():
                payload.setdefault(k, v)
        content[0]["text"] = json.dumps(payload, indent=2)
        return result

    asserted.__name__ = getattr(handler, "__name__", "asserted")
    asserted.__wrapped__ = handler                   # tests/introspection reach the original
    asserted.__assert_postconditions__ = posts       # the declaration lint reads this
    return asserted
