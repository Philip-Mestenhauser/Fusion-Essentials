# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Typed POSTCONDITION kinds: the state-side mirror of ``_outputs.OutputKind``.

An Edit tool declares ``postconditions=[...]`` at registration (Item.create_tool_item); the kernel
runs capture -> handler -> verify and converts an ok() whose declared effect did not take into an
error - the platform can return success while changing nothing. Severities: "hard" (reason ->
isError) and "soft" (payload marked unconfirmed, for effects that legitimately lag the call).
Evidence a verify reads is folded into the payload via setdefault, so a declared RETURNS key can be
SUPPLIED by its postcondition rather than computed twice. A Postcondition NEVER mutates:
capture/verify are safe() reads, and a verify that cannot read ground truth reports "could not
confirm", never a false "". See tools/CLAUDE.md for the authoring rule.
"""

import json

import adsk.core

from ._common import safe

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


def wrap(handler, postconditions):
    """Wrap an Edit handler with capture -> handler -> verify. Runs verify only on a JSON ok() result;
    error results and non-JSON payloads pass through untouched. Applied INSIDE _write_guard.wrap (the
    guard stamps acted_on on whatever this returns)."""
    posts = list(postconditions or [])
    if not posts:
        return handler

    def asserted(**kwargs):
        before = [safe(lambda p=p: p.capture(kwargs)) for p in posts]
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

        for p, b in zip(posts, before):
            reason, evidence = "", {}
            try:
                reason, evidence = p.verify(kwargs, payload, b)
            except Exception as ex:
                reason, evidence = "", {p.name + "_confirmed": False,
                                        "verify_error": str(ex)[:120]}
            if reason:
                if p.severity == "soft":
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
