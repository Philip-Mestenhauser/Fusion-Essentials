# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Launch CAM toolpath generation asynchronously (cam_generate, returns a handle) and read its
progress (cam_get_status). Generation runs in the background at its own pace once launched. The
live GenerateToolpathFuture must stay referenced across calls - see _cam_common.register_future -
or Fusion abandons the in-progress generation."""

import time

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, told_apart
from . import _outputs
from . import _cam_common   # the shared CAM substrate: live_readiness (the single job-health source)
from ._write_guard import _active_identity, document_key   # the one active-document identity read,
                                                           # and the one key a launch is bound to

# What this tool RETURNS: an async generation handle the agent checks with cam_get_status.
RETURNS = [
    _outputs.ReturnsValue("handle", "a generation handle - check cam_get_status(handle) until "
                          "completed", consumers=["cam_get_status"]),
]


# The live-generation registry and the handle it mints live in _cam_common (register_future) - the
# ONE registration path every launch goes through, whether that launch is this tool, an inline
# cam_select_geometry generate, or cam_create_operation(generate=true). These two names are the same
# objects; a status read below is reading exactly what those launches wrote.
_GENERATIONS = _cam_common._GENERATIONS
_HANDLE_SEQ = _cam_common._HANDLE_SEQ
register_future = _cam_common.register_future


def _collect_op_health(ops, labels=None):
    """Read warnings / errors from THESE operations, with the message text.

    `ops` is the operations of the SCOPE the tally published beside these lists covers - a scoped
    read hands that target's own operations, a document read the whole document's - so the lists and
    that tally can never describe different scopes.

    `labels` is what each row is NAMED by, parallel to `ops` (see _op_labels): an operation name is
    unique only within a setup, so a document-scope list can otherwise carry one name twice with
    nothing separating the rows. Omitted, every row is named by the operation's own name.

    Returns {"warnings": [{name, warning}], "errors": [{name, error}], "empty": [name]}.
    - a warning row is gated by _cam_common.counts_as_warning, the ONE predicate every readiness
      surface counts and samples through - the same one behind live_states.warnings, so the tally
      and this list select the same operations rather than two sets the payload claims are one. A
      warning on an ERRORED op is left to that op's error row (which already demotes the verdict),
      and a SUPPRESSED op carries no toolpath - suppression DISCARDS it (measured, measure_api
      cam-suppress-discards-toolpath). What a POST does with either is not measured and is not
      claimed here. The text comes from OperationBase.warning; the case the machinist most wants is
      a spindle speed over the machine limit (often acceptable).
    - 'empty' is the shared state read (_cam_common.is_empty_toolpath): an op that generated and
      produced no toolpath, told from the flags rather than from warning text.
      """
    out = {"warnings": [], "errors": [], "empty": []}
    labels = list(labels or [])
    for i, o in enumerate(ops or []):
        facts = _cam_common.op_state_facts(o)
        name = labels[i] if i < len(labels) else facts["name"]
        if facts["has_error"]:
            out["errors"].append({"name": name, "error": (safe(lambda o=o: o.error) or "").strip()})
        if _cam_common.counts_as_warning(facts):
            out["warnings"].append({"name": name,
                                    "warning": (safe(lambda o=o: o.warning) or "").strip()})
        if _cam_common.is_empty_toolpath(facts):
            out["empty"].append(name)
    return out


def _document_ops():
    """Every operation NODE in the ACTIVE document - the health-list counterpart to live_readiness,
    whose tally is the whole document's too. It is passed UNCALLED alongside that tally and
    evaluated only where the lists are actually attached, so a still-generating poll never pays for
    the walk.

    Nodes rather than bare Operations: the whole document is the scope where one operation name
    legitimately belongs to several operations, and the node's 'Setup / op' path is the only thing
    that separates them (see _op_labels).

    That laziness is why the document path re-walks: live_readiness took its own walk earlier in the
    call, and this is a SECOND one. The two therefore share a scope, not an instant - an operation
    that changed state in between lands in the lists under its later reading."""
    cam, err = _cam_common.get_cam()
    return [] if err else _cam_common.operation_nodes(cam)


def _op_labels(nodes):
    """What each operation row is NAMED by: the operation's own name, or - where several operations
    in the SAME list carry that name - its 'Setup / ... / op' path, plus the row's POSITION in this
    list wherever that path repeats too.

    An operation name is unique only WITHIN a setup, so a document-scope list carries one name twice
    unless the row is named by its path. The path is read off the walk that produced the node,
    beside the name, so it describes THAT operation; the walk builds it one level at a time, which
    is why a folder-nested operation carries its folder in the middle. The substitution is
    _common.told_apart, the one rule every listing here follows: a name that already identifies one
    row is left alone.

    A PATH can repeat as well - it is the container's address joined with the operation's own name,
    so two rows agreeing on both agree on the whole string and the substitution would print one
    address for two operations, which is the count restated and nothing else. Such a row takes the
    position it holds in THIS list beside its path: the same discriminator
    workspace_orient._empty_labels spends on the same shape, so the two lists name a repeated
    address one way rather than two. It is spent only where the path failed, and it addresses
    nothing outside this payload - no tool takes it as input.

    What Fusion permits here is not settled. An operation CREATED with a sibling's name under one
    setup is reported to be stored under a different name, but that has no ledger row - and a RENAME
    onto a sibling's name, and whether two setups may share a name (which would join two containers
    to one address), are unread either way. PROBE NEEDED (CAM-34) covers all three. The repair does
    not rest on that answer:
    it reads the (name, path) pairs this list actually holds, so a repeat is separated whatever
    produced it, and an unrepeated path is rendered exactly as before."""
    per_path = {}
    for n in nodes:
        per_path[n.path] = per_path.get(n.path, 0) + 1
    rows = []
    for position, n in enumerate(nodes, 1):
        disc = n.path
        if disc and per_path[disc] > 1:
            disc = f"{disc} (operation {position})"
        rows.append((n.name, disc))
    return told_apart(rows)


# ---------------------------------------------------------------------------
# cam_generate  (launch; returns immediately)
# ---------------------------------------------------------------------------

def generate_handler(target: str = "", skip_valid: bool = True) -> dict:
    """Launch toolpath (re)generation; return immediately with a poll handle.

    target: omit (or 'all'/'document') to generate across the whole document; otherwise the exact
    NAME of a setup, folder, or operation. skip_valid: when true (default) only regenerate
    out-of-date operations; when false, regenerate everything in scope. WRITES (mutates toolpaths).
    Does NOT wait - poll with cam_get_status(handle).
    """
    cam, err = _cam_common.get_cam()
    if err:
        return error(err)

    want = (target or "").strip()
    scope = "document"
    resolved_name = ""          # the scoped launch's own target name (empty for a document launch)
    try:
        if not want or want.lower() in ("all", "document", "*"):
            future = cam.generateAllToolpaths(bool(skip_valid))
            scope = "document"
            target_desc = "all setups"
        else:
            node, rerr = _cam_common.resolve_cam_node(
                cam, want, kinds=("setup", "folder", "operation"), label="setup/folder/operation")
            if rerr:
                return error(rerr + " Omit 'target' to generate the whole document.")
            tgt, kind = node.obj, node.kind
            # generateToolpath has no skip_valid flag; it regenerates the given target. When the
            # caller asked to skip valid and this single target is already valid+current, short out.
            if skip_valid and kind == "operation" and safe(lambda: tgt.operationState) == 0:
                return ok({"launched": False, "skipped": True, "target": want,
        "reason": "operation already valid and up to date (skip_valid=true).",
        "hint": "Pass skip_valid=false to force-regenerate it."})
            future = cam.generateToolpath(tgt)
            scope = kind or "target"
            resolved_name = node.name or want
            target_desc = f"{scope} '{want}'"
    except Exception as e:
        return error(f"Failed to launch generation for {scope}: {e}")

    if not future:
        return error("Generation launch returned no future (nothing to generate?).")

    handle, total = register_future(future, target_desc, scope, skip_valid,
                                    target_name=resolved_name)

    # NOTE: future.numberOfOperations raises "Generation not started" if read on this same launch
    # tick - the count only populates once generation has spun up. safe() above already turned that
    # into None; surface it as "pending" rather than implying nothing will generate.
    return ok({
        "launched": True,
        "handle": handle,
        "target": target_desc,
        "skip_valid": bool(skip_valid),
        "operations_to_generate": (total if total is not None else "pending (read on first status check)"),
        "note": ("Generation is launched and runs in the background at its own pace - the compute "
            "is often minutes. Check cam_get_status(handle) at whatever cadence you need the "
            "progress, until completed=true. The op count/progress populate on the first check."),
    })


# ---------------------------------------------------------------------------
# cam_get_status  (a plain progress read)
# ---------------------------------------------------------------------------
#
# Generation runs in the background at its own pace once launched (live-verified: an unattended
# 34-op job kept completing operations across minutes with zero status calls) - this tool only
# READS progress; it does not advance anything. Two ways to read: a cam_generate call mints a
# Future we scope to (the handle path); an op generated INLINE (cam_create_operation(generate=true),
# cam_select_geometry, or the UI) has no self-minted Future - the no-handle path reads live op
# state directly (via live_readiness for the document, or a scoped op walk for a named target).


def _incomplete_note(live: dict) -> str:
    """The note for a still-generating status read (shared by both paths). live is
    live_readiness-shaped. An errored op/setup/program will NEVER finish, so flag the BLOCKER and
    tell the caller to STOP waiting; otherwise report the plain 'still generating' (with the
    nothing-generating-yet stall warning)."""
    readiness = live.get("readiness", "")
    samples = live.get("samples") or {}
    blocked = bool(live.get("errored") or live.get("setups_errored") or live.get("programs_errored"))
    if blocked:
        note = (readiness + " Waiting will NOT complete the errored items - fix them, "
                "then re-run cam_generate. ")
        samp = samples.get("op") or samples.get("setup") or samples.get("program") or {}
        if samp.get("name"):
            note += f"e.g. '{samp['name']}': {samp.get('error', '')}. "
        note += "cam_get(include=['operations']) for every errored item + full text."
    elif live.get("generating", 0) == 0 and live.get("out_of_date", 0) > 0:
        note = ("Not complete. WARNING: nothing is actively generating yet out-of-date ops "
                "remain - they may be failing (broken input geometry / a mis-posed fixture or "
                "stock). cam_get(include=['operations']) shows why.")
    else:
        note = "Still generating in the background - check again later."
    return note


# What `completed` claims, stated wherever it is published as true. It is a GENERATION-lifecycle
# flag, not a result: measured on a document reading "0 of 34 active ops valid", the poll still
# reported completed:true because nothing was generating any more. This sentence stops the flag
# being read as a result. The pointer to the health verdict is a SEPARATE sentence, because the two
# paths that report a Future alone (a foreign active document, an unreadable tally) have no
# readiness line to point at - promising one there and withdrawing it a clause later is worse than
# either fact alone.
_COMPLETED_MEANS = ("completed=true means nothing in scope is still generating - it is not a "
                    "success verdict.")
_READINESS_IS_THE_VERDICT = " The readiness line beside it is the health verdict."

# What operations_completed IS, stated wherever the Future's counter is published. The number is
# GenerateToolpathFuture.numberOfCompleted at that instant and nothing more. No running maximum is
# published in its place: a high-water mark would go on claiming progress the platform's own counter
# has stopped standing behind, and a regeneration inside this handle's scope legitimately starts the
# count over, so a clamp could only lie about completion. Nothing here reads the number as a
# verdict either - `completed` is gated on isGenerationCompleted plus the live per-op tally - so
# disclosing the figure as instantaneous costs the read nothing it was using.
_COUNT_IS_INSTANTANEOUS = (
    " operations_completed is the generation Future's own numberOfCompleted at THIS read - an "
    "instantaneous figure, not a monotonic progress count: it has been observed to FALL between two "
    "reads of one generation (24 -> 23 -> 25), and it reads 0 after a completed single-operation "
    "generation (measured). Read progress from 'completed' and the readiness line, never from this "
    "number rising.")


def _same_document(entry: dict):
    """Whether this generation's launch document IS the active one - the ONE comparison the status
    read gates on, so the tally path and the handle-routing path can never disagree about it.

    Three answers, because two of them are not the same fact. True and False are IDENTITY, read off
    the document_key the launch recorded and the one the active document answers now - the lineage
    URN for a saved document, and for a never-saved one a token minted per document INSTANCE and
    matched by document handle. That key is why a never-saved document is comparable at all: its
    NAME never was, since two open never-saved documents both answer 'Untitled' (measured), so a
    name match is not evidence that they are one document.

    None is "no identity was readable, so the two cannot be compared" - no document read when the
    generation was launched, or none reads now. Callers gate on `is True` - a None read as truthy
    attaches another document's tallies, and read as plain False it reports a document as inactive
    that may be the active one.

    A key that no longer matches is not yet a different document: the key is DERIVED from what
    reads on the document, and document_key prefers a data-file id which does not arrive settled -
    so a launch document saved mid-generation can answer a new key MORE THAN ONCE while being the
    same open document, and this comparison has to survive each of them. The launch document
    HANDLE is compared before that mismatch is reported as one - handle equality is the comparison
    document_key itself matches a never-saved document on, and a closed or foreign document
    compares unequal there rather than raising. It can only turn a mismatch into a match: a key
    that still matches is already the answer.
    """
    launched = entry.get("doc_key")
    if not launched:
        return None
    active = document_key()
    if active is None:
        return None
    if launched == active:
        return True
    doc = entry.get("doc")
    if doc is None:
        return False
    # Read through the same application object register_future stamped the handle from, so the two
    # halves of this comparison cannot come from different seams.
    live = safe(lambda: _cam_common.app.activeDocument)
    if live is None:
        return False
    return bool(safe(lambda: doc == live, False))


def _latest_refusal(latest: str, entry: dict, same) -> str:
    """The refusal 'latest' returns when it cannot name the job the caller meant.

    'latest' is a POSITIONAL pick naming neither a handle nor a document, so it may only answer over
    a launch document it can CONFIRM is the active one. The two ways it cannot are different facts
    and get different sentences: `same` is False when the launch document is identified and is not
    this one, None when no identity was readable on one side or the other to compare at all."""
    doc_name = entry.get("doc_name")
    ways_out = (f"Pass handle='{latest}' to read that generation deliberately, or omit 'handle' for "
                "the ACTIVE document's live state.")
    if same is None:
        return (f"'latest' is generation '{latest}', and no document identity could be read to "
                "compare it against the active one - either no document read when it was launched, "
                f"or none reads now. Its name ({doc_name!r}) is not an identity - two open "
                "never-saved documents answer the same one - so this read cannot confirm the "
                f"generation belongs to the document open now. {ways_out}")
    return (f"'latest' is generation '{latest}' of document '{doc_name}', which is not the active "
            f"document ({_active_identity()[0]!r}). It names no document of its own, so this read "
            f"would report another document's job. {ways_out} Or doc_activate '{doc_name}' first "
            "for its per-operation tallies.")


def _attach_op_health(payload: dict, nodes, scope_label: str) -> str:
    """Attach the per-op warning/error TEXT lists for the final review (the texture a machinist
    reads), read over the SAME SCOPE live_states was tallied over, and NAME that scope in the
    payload. Returns the note clause stating it.

    Lists and counts are both built from `nodes`, so a scoped read cannot report six operations in
    its tally beside thirty-four warnings drawn from every setup in the document - a mixed pair a
    reader has no way to tell apart. health_scope is what says which of the two a given payload
    holds.

    Each row is named through _op_labels, so a name two operations in this scope share is replaced
    by the path that separates them - and the note SAYS so when that happened, since a reader
    meeting 'Setup1 / Rough' in a name field otherwise has to guess why."""
    labels = _op_labels(nodes)
    health = _collect_op_health([n.obj for n in nodes], labels)
    payload["operations_with_warnings"] = health["warnings"]   # [{name, warning}]
    payload["operations_with_errors"] = health["errors"]       # [{name, error}]
    payload["empty_toolpaths"] = health["empty"]               # generated but 0 toolpath length
    payload["counts"] = {"with_warnings": len(health["warnings"]),
                         "with_errors": len(health["errors"]),
                         "empty_toolpaths": len(health["empty"])}
    payload["health_scope"] = scope_label      # WHICH operations the three lists above describe
    # The walk names each level it descends, so the path is 'Setup / op' for a top-level operation
    # and carries the folder(s) in between for a nested one - the clause says it the way the walk
    # builds it rather than promising the two-part form.
    shared = (" Operations sharing a name here are named by their setup path instead - "
              "'Setup / op', with any folders between - and where two rows carry that same path "
              "too, by the position they hold in this list."
              if any(label != n.name for label, n in zip(labels, nodes)) else "")
    return f" The warning/error/empty lists and their counts cover: {scope_label}.{shared}"


def status_handler(handle: str = "", target: str = "", include_operations: bool = True) -> dict:
    """Read toolpath generation progress. handle is OPTIONAL: pass the id from cam_generate (or
    'latest') to scope to that launched generation; OR omit it (and pass a setup/operation NAME as
    target, or nothing for the whole document) to read a generation launched inline -
    cam_create_operation(generate=true), cam_select_geometry, or the Fusion UI - with no handle.
    include_operations: when complete, also report each op's final state + warnings/errors."""
    key = (handle or "").strip()
    want_target = (target or "").strip()

    # 1. An explicit target picks the live path (no handle needed) - read that setup/op by name.
    if want_target:
        return _status_live(want_target, include_operations)

    # 2. An explicit handle (not 'latest') scopes to that launched generation's Future.
    if key and key.lower() != "latest":
        entry = _GENERATIONS.get(key)
        if not entry:
            return error(
                f"No generation with handle '{handle}'. Active handles: "
                f"{', '.join(_GENERATIONS.keys()) or '(none)'}. Omit 'handle' to read live document "
                "state, or pass 'target' (a setup/operation name) to read an inline generation by name.")
        return _status_future(entry, key, include_operations)

    # 3. 'latest' - the most recent launched generation. It is a POSITIONAL pick naming neither a
    #    handle nor a DOCUMENT, so every way it can fail to identify a job is refused rather than
    #    quietly answered from something else: a handle that is no longer registered, one whose
    #    launch document is identified and is not active (a stale entry from a closed document
    #    answers with completed:true about THAT job, which reads as a verdict on whatever is open
    #    now), and one whose launch document carries no identity to compare at all.
    if key:
        latest = f"gen{_HANDLE_SEQ[0]}"
        entry = _GENERATIONS.get(latest)
        if not entry:
            return error(
                f"'latest' resolves to handle '{latest}', which is not registered - a generation "
                "is dropped from the registry once it completes. Active handles: "
                f"{', '.join(_GENERATIONS.keys()) or '(none)'}. Omit 'handle' to read the ACTIVE "
                "document's live state, or pass 'target' (a setup/operation name) to read an "
                "inline generation by name.")
        same = _same_document(entry)
        if same is not True:
            return error(_latest_refusal(latest, entry, same))
        return _status_future(entry, latest, include_operations)

    # 4. handle AND target omitted: read live state of the ACTIVE DOCUMENT. A registered handle does
    #    NOT capture this call - a bare read is a question about what is open now, and answering it
    #    from a launch registry certifies a document the caller never named. 'latest' above is the
    #    way to ask about the most recent launch.
    return _status_live("document", include_operations)


def _handle_scope_state(entry: dict):
    """(live_dict, basis_label, health_ops, err) for THIS handle's OWN operations - the tally its
    completion settles on, the name of whose operations that is, the zero-arg callable handing back
    those same operations for the health lists, and why no tally could be read.

    A handle launched against ONE setup/folder/operation settles on THAT target's ops (the scoped walk
    _scope_state already does for the no-handle path). Gating it on the DOCUMENT-wide generating count
    starves it under concurrent generation: a second job's ops keep the count above zero, so the first
    handle reports incomplete long after its own work finished. A document-scope launch IS the whole
    document, so it keeps the document tally. If the scoped target no longer resolves (renamed or
    deleted mid-generation) the read falls back to the document tally and the basis label says so.

    When even that read fails, err carries the reason and the basis names the Future alone - a
    verdict must never be published under a tally that was never read."""
    name = (entry.get("target_name") or "").strip()
    scoped = bool(name) and (entry.get("scope") or "document") in ("setup", "folder", "operation")
    if scoped:
        cam, cerr = _cam_common.get_cam()
        if not cerr:
            live, label, health_ops, serr = _scope_state(cam, name)
            if not serr and live is not None:
                return live, label, health_ops, None
    live, lerr = _cam_common.live_readiness()
    if lerr or live is None:
        reason = lerr or "the read returned no tally"
        return ({}, f"this generation's Future alone (no per-op tally could be read: {reason})",
                None, reason)
    if scoped:
        return live, (f"document (the launch target '{name}' could not be re-resolved - "
                      "renamed, deleted, or now ambiguous)"), _document_ops, None
    return live, "document", _document_ops, None


def _status_future(entry: dict, key: str, include_operations: bool) -> dict:
    """The handle path: scope to a cam_generate-launched Future and report its progress.

    CRITICAL: holding the GenerateToolpathFuture (in _GENERATIONS) keeps the background work alive.

    The per-op tallies read the ACTIVE document - so they are only attached when the generating
    document is CONFIRMED to be the active one. Otherwise the Future's own counters still report
    progress, the payload says whose generation this is, and completion falls back to the Future
    alone (a wrong-document tally must never gate it). 'Otherwise' covers both a document that is
    identified and is not this one and a launch whose identity was never readable - see
    _same_document, whose None the completion_basis and note tell apart."""
    future = entry["future"]

    total = safe(lambda: future.numberOfOperations, entry.get("total"))
    done_count = safe(lambda: future.numberOfCompleted, None)
    future_done = bool(safe(lambda: future.isGenerationCompleted, False))
    elapsed = round(time.time() - entry["started_at"], 1)

    doc_urn, doc_name = entry.get("doc_urn"), entry.get("doc_name")
    same_doc = _same_document(entry)
    # Said only where the number is actually published: a Future whose counter did not read carries
    # operations_completed null, and a caveat about a figure that is not there teaches nothing.
    count_caveat = _COUNT_IS_INSTANTANEOUS if done_count is not None else ""

    payload = {
    "handle": key,
    "target": entry["target"],
    "generating_document": {"name": doc_name, "document_id": doc_urn},
    "operations_total": total,
    "operations_completed": done_count,
    "elapsed_seconds": elapsed,
    }

    if same_doc is not True:
        # The tallies read the ACTIVE document, so they are attached only over a launch document
        # this call can CONFIRM is that one. A DIFFERENT document and an UNCONFIRMABLE one are
        # separate facts and get separate sentences - reporting the second as the first tells the
        # caller their document is not active when it may be the one they are looking at.
        payload["completed"] = future_done
        if same_doc is None:
            payload["completion_basis"] = ("this generation's Future alone (no document identity "
                                           "could be read to compare it with the active one)")
            # The launch document is NAMED only where a name actually read. This branch is reached
            # two ways - a launch that read no document at all, whose name is None too, and a read
            # taken while none reads now - and an interpolated None is a document name nothing saw.
            named = f" for '{doc_name}'" if doc_name else ""
            why = (f" No document identity could be read{named} - either none read when this "
                   "generation was launched, or none reads now - and a name is not an identity "
                   "(two open never-saved documents answer the same one). Per-op tallies and "
                   "warnings were skipped rather than read off a document this call cannot confirm "
                   "is the right one, so no readiness line could be read at all.")
        else:
            payload["completion_basis"] = ("this generation's Future alone (its document is not "
                                           "active)")
            why = (f" The generating document '{doc_name}' is NOT the active document - per-op "
                   "tallies and warnings were skipped (they read the active document), so no "
                   f"readiness line could be read at all. doc_activate '{doc_name}' for the full "
                   "read.")
        payload["note"] = (
            (f"Generation complete ({done_count} of {total} operations). {_COMPLETED_MEANS}"
             if future_done else
             "Still generating in the background - check again later.") + why + count_caveat)
        if future_done:
            _GENERATIONS.pop(key, None)
        return ok(payload)

    # Health/readiness is NOT re-derived here - it is the _cam_common domain (the single CAM-health
    # source cam_get exposes). The scope read walks ops + (document scope) setup/NC-program errors and
    # returns the tally + a ready-made readiness verdict. This path owns the progress delta on top.
    live, basis, health_ops, tally_err = _handle_scope_state(entry)

    if tally_err:
        # No tally was read at all, so nothing can corroborate the Future - report the Future-alone
        # verdict WITH the reason (the wrong-document branch above words this the same way), and
        # attach no live_states: an empty tally read as "nothing is generating" is the false done.
        payload["completed"] = future_done
        payload["completion_basis"] = basis
        payload["note"] = (
            (f"Generation complete ({done_count} of {total} operations). {_COMPLETED_MEANS}"
             if future_done else
             "Still generating in the background - check again later.")
            + f" The per-op tallies could not be read ({tally_err}), so this rests on the "
            "generation Future alone - cam_get for the job's health." + count_caveat)
        if future_done:
            _GENERATIONS.pop(key, None)
        return ok(payload)

    # The Future flips isGenerationCompleted a beat BEFORE live op state settles (observed:
    # completed while live_states still showed generating=3). Gate completed on BOTH agreeing - the
    # Future is done AND nothing in THIS HANDLE'S OWN scope is still generating - so the caller never
    # reads a premature done, and a second concurrent generation's ops never keep this handle waiting.
    # An errored op is its own bucket (never counted as generating), so this can't hang on a fault.
    completed = future_done and (live.get("generating", 0) == 0)
    payload["completed"] = completed
    payload["completion_basis"] = basis   # whose operations settled this verdict
    payload["live_states"] = live  # valid/out_of_date/errored/generating/suppressed (+ setup/program for document)

    if not completed:
        payload["note"] = _incomplete_note(live) + count_caveat
        return ok(payload)

    # The health lists cover the scope the tally above settled on - `basis` names that same scope,
    # so the payload cannot pair a scoped tally with document-wide warning rows.
    health_note = _attach_op_health(payload, health_ops(), basis) if include_operations else ""
    payload["note"] = (f"Generation complete. {_COMPLETED_MEANS}{_READINESS_IS_THE_VERDICT} "
                       f"{live.get('readiness', '')} "
                       "cam_get(include=['operations']) for the per-op detail."
                       + health_note + count_caveat)

    # Generation finished - drop the registry entry so it does not leak across the session.
    _GENERATIONS.pop(key, None)
    return ok(payload)


def _op_tally(ops) -> dict:
    """A live_readiness-shaped tally scoped to just these ops, via the shared _cam_common.op_state_tally
    (the ONE per-op walk live_readiness's whole-document scan also uses) - this scoped poll just adds
    the setups_errored/programs_errored/samples shape a document-level poll carries (always 0/None
    here: a single setup/operation target has no setup- or program-level error of its own to report)."""
    t = _cam_common.op_state_tally(ops)
    # warnings + warning_sample travel with the tally: they are what stops this SCOPED verdict
    # reading plainly ready over a job the document-level one would demote.
    return {"valid": t["valid"], "out_of_date": t["out_of_date"], "errored": t["errored"],
            "generating": t["generating"], "suppressed": t["suppressed"],
            "warnings": t["warnings"], "total": t["total"],
            "active": t["active"], "setups_errored": 0, "programs_errored": 0,
            # the OWNING setup's blocked_by, filled by _scope_state - a scoped verdict reads the
            # same setup-level prerequisites the document-level one does.
            "setups_blocked": [],
            "samples": {"op": t["op_sample"], "setup": None, "program": None,
                        "warning": t["warning_sample"]}}


def _scope_readiness(t: dict) -> str:
    """The scoped readiness verdict for an _op_tally. The postable sentence itself is
    _cam_common.ready_verdict - the ONE builder live_readiness and cam_get's summary also end on -
    so a scoped poll cannot say 'ready to post' over warnings, or over a blocked owning setup,
    that the document poll would name."""
    active_total = t["valid"] + t["out_of_date"] + t["errored"]
    if t["errored"]:
        return (f"BLOCKER: {t['errored']} operation(s) have errors - "
                "the job will not post until fixed.")
    if active_total and t["valid"] == active_total:
        return _cam_common.ready_verdict(f"{t['valid']} of {active_total} active ops valid",
                                         t.get("warnings", 0),
                                         (t.get("samples") or {}).get("warning"),
                                         t.get("setups_blocked"))
    if active_total:
        return f"{t['valid']} of {active_total} active ops valid - run cam_generate to finish the rest."
    return "no active operations to assess."


def _scope_state(cam, target: str):
    """(live_dict, scope_label, health_ops, err). Document/all scope REUSES
    _cam_common.live_readiness; a named setup/folder/operation is tallied by a scoped op walk.
    live_dict is live_readiness-shaped so the same note/payload code serves both poll paths.

    health_ops is a zero-arg callable handing back the operation NODES of the scope this tally
    covers - the per-op warning/error/empty lists are built from it, which is what keeps them and the
    tally describing one scope. A named target closes over the list already walked here; the document
    branch re-walks at attach time (see _document_ops), so what the two share there is the scope,
    not the instant. It stays a callable so a still-generating poll, which publishes no lists, never
    walks the document a second time."""
    want = (target or "").strip()
    if not want or want.lower() in ("all", "document", "*"):
        live, err = _cam_common.live_readiness()
        return (live or {}), "document", _document_ops, err
    node, rerr = _cam_common.resolve_cam_node(
        cam, want, kinds=("setup", "folder", "operation"), label="setup/folder/operation")
    if rerr:
        return None, None, None, rerr + " Omit 'target' to poll the whole document."
    kind = node.kind
    # Nodes, not bare Operations: the health lists name what they hold, and only the node carries
    # the 'Setup / op' path that separates two operations of one name (see _op_labels).
    nodes = [node] if kind == "operation" else _cam_common.operation_nodes_under(node)
    tally = _op_tally([n.obj for n in nodes])
    # The setup this target sits under, read off the walk's own parent chain (owning_setup) - a
    # folder or operation is posted through its setup, so that setup's blocked_by gates this
    # verdict exactly as it gates the document-level one.
    owner = _cam_common.owning_setup(node)
    tally["setups_blocked"] = _cam_common.blocked_setup_records([owner] if owner is not None else [])
    tally["readiness"] = _scope_readiness(tally)
    return tally, f"{kind} '{node.name or want}'", (lambda: nodes), None


def _status_live(target: str, include_operations: bool) -> dict:
    """The no-handle path: report the live generation state of the target (a setup/operation name)
    or the whole document - reading op state DIRECTLY off the ACTIVE document, so an op generated
    inline (cam_create_operation(generate=true) / the UI) is readable with no cam_generate handle.
    completed=true only when nothing in scope is still generating."""
    cam, err = _cam_common.get_cam()
    if err:
        return error(err)

    live, scope_label, health_ops, serr = _scope_state(cam, target)
    if serr:
        return error(serr)

    live = live or {}
    completed = live.get("generating", 0) == 0
    payload = {
    "handle": None,                # live read: no self-minted handle needed
    "target": scope_label,
    "completed": completed,
    "operations_total": live.get("total"),
    "live_states": live,           # valid/out_of_date/errored/generating/suppressed (+ setup/program for document)
    }

    if not completed:
        payload["note"] = _incomplete_note(live)
        return ok(payload)

    health_note = _attach_op_health(payload, health_ops(), scope_label) if include_operations else ""
    payload["note"] = (f"No operations are still generating in scope. {_COMPLETED_MEANS}"
                       f"{_READINESS_IS_THE_VERDICT} {live.get('readiness', '')} "
                       "cam_get(include=['operations']) for the per-op detail." + health_note)
    return ok(payload)


# ---------------------------------------------------------------------------
# tool definitions
# ---------------------------------------------------------------------------

GENERATE_DESCRIPTION = (
    "Launch CAM toolpath (re)generation and return IMMEDIATELY with a handle; generation runs in "
    "the background at its own pace (often minutes) - check cam_get_status(handle) at any cadence "
    "until completed=true. 'target': omit/'document' for the whole "
    "document, or a setup/folder/operation NAME. 'skip_valid' (default true) regenerates only "
    "out-of-date ops; false forces all in scope. Be in the MANUFACTURE workspace first: "
    "out-of-date state isn't re-evaluated against changed geometry until Manufacture is active, so from "
    "Design skip_valid=true can wrongly skip stale ops - after swapping a part, enter Manufacture or "
    "pass skip_valid=false.\n"
    + _outputs.produces_block(RETURNS)
)

generate_tool = (
    Tool.create_simple(name="cam_generate", description=GENERATE_DESCRIPTION)
    .add_input_property("target", {"type": "string",
            "description": "Setup/folder/operation NAME to generate; omit (or 'document') for the whole document."})
    .add_input_property("skip_valid", {"type": "boolean",
            "description": "Only regenerate out-of-date operations (default true); false forces all in scope."})
    .strict_schema()
)
generate_item = Item.create_tool_item(
    tool=generate_tool, write="write", handler=generate_handler, run_on_main_thread=True,
    verification=Verification(
        kind="deferred", poller="cam_get_status",
        evidence_test="tests/unit/test_cam_generate.py::TestLaunchHandsOffToTheStatusRead"
                      "::test_the_launch_claims_no_completion_and_names_the_poller"))

STATUS_DESCRIPTION = (
    "Read toolpath generation progress - generation runs in the background on its own once "
    "launched; this is a plain status read, so check at whatever cadence you need the information. "
    "'handle' is OPTIONAL: pass the cam_generate id (or 'latest') to scope to that launched "
    "generation; OR omit it and pass 'target' (a setup/operation NAME, or nothing for the whole "
    "document) to read a generation launched INLINE - cam_create_operation(generate=true), "
    "cam_select_geometry, or the Fusion UI - with NO cam_generate handle. live_states tallies "
    "valid / out_of_date / ERRORED / generating, plus setups_errored / programs_errored for the "
    "document scope: an ERRORED op (parameter/geometry fault) will NEVER finish, and a faulted "
    "SETUP or NC PROGRAM blocks the whole job from posting - the note flags these (with one sample "
    "each) so you stop waiting, and points at cam_get for the full error text + readiness verdict. "
    "Per-op tallies read the ACTIVE document: omit both 'handle' and 'target' to read what is open "
    "NOW; 'latest' is refused when its launch document is not active; an explicit handle reports "
    "that Future's progress and names its document. completed=true means nothing in scope is still "
    "generating - NOT that the ops succeeded; read the readiness line beside it."
)

status_tool = (
    Tool.create_simple(name="cam_get_status", description=STATUS_DESCRIPTION)
    .add_input_property("handle", {"type": "string",
            "description": "Optional generation handle from cam_generate, or 'latest'. Omit to read live state (see target)."})
    .add_input_property("target", {"type": "string",
            "description": "Read an inline/UI generation with no handle: a setup/operation NAME, or omit (or 'document') for the whole document."})
    .add_input_property("include_operations", {"type": "boolean",
            "description": "When complete, include per-operation warnings/errors + empty toolpaths (default true)."})
    .strict_schema()
)
status_item = Item.create_tool_item(tool=status_tool, write="read", handler=status_handler,
                                    run_on_main_thread=True)


def register_tool():
    register(generate_item)
    register(status_item)
