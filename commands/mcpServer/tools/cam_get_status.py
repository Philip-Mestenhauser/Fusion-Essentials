# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read CAM toolpath generation progress. Generation runs in the background at its own pace; this
tool only READS progress. A cam_generate launch mints a Future the handle path scopes to; an op
generated inline or in the UI has none."""

import time
from collections import OrderedDict

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _cam_common   # the shared CAM substrate: live_readiness (the single job-health source)
from ._write_guard import _active_identity, document_key

# The registry and the handles it mints live in _cam_common - the ONE registration path every
# launch goes through. These names are those same objects.
_GENERATIONS = _cam_common._GENERATIONS
_HANDLE_SEQ = _cam_common._HANDLE_SEQ

_RELEASED_CAP = 20
# Bounded so a long session's completed handles cannot grow this table without limit; the oldest
# release is evicted first, since a poller has no reason to ask about one that old.
_RELEASED = OrderedDict()


def _release(key: str, entry: dict, elapsed: float, live=None) -> None:
    """Move `key` out of `_GENERATIONS` into the bounded `_RELEASED` table with what its completed
    read published, so the NEXT poll of it can say why it misses instead of a bare unknown handle."""
    _GENERATIONS.pop(key, None)
    _RELEASED[key] = {"target": entry.get("target"), "target_name": entry.get("target_name"),
                      "doc_name": entry.get("doc_name"), "doc_key": entry.get("doc_key"),
                      "elapsed_seconds": elapsed, "tally": dict(live) if live else None}
    if len(_RELEASED) > _RELEASED_CAP:
        _RELEASED.popitem(last=False)


def _released_miss(key: str, released: dict) -> str:
    """The error for a handle `_RELEASED` already answered once: what it completed as and which
    document, then how to read the same scope live - or, when a different document is active now,
    that a live-read remedy here would not reach the one that launched it."""
    tally = released.get("tally")
    doc_name = released.get("doc_name")
    where = f" on document '{doc_name}'" if doc_name else ""
    counts = (f" ({released.get('target')}: {tally['valid']} valid, {tally['errored']} errored)"
              if tally and released.get("target") else "")
    name = (released.get("target_name") or "").strip()
    switched = bool(released.get("doc_key")) and document_key() != released.get("doc_key")
    if switched:
        tail = (f"'{doc_name}' is NOT the active document - its live state cannot be read from "
                f"here. doc_activate '{doc_name}' first.")
    else:
        remedy = f"cam_get_status(target='{name}')" if name else "cam_get_status()"
        tail = f"{remedy} reads the live state."
    return (f"Handle '{key}' completed at {released.get('elapsed_seconds')} s{counts}{where} and "
            f"is released - {tail}")


# Keyed on _cam_common.is_rail_driven - the same parameter cam_select_geometry feeds a rail pair
# into, so a rename cannot leave this promise pointing at operations that no longer answer it.
_RAIL_TRIAGE = (
    "A rail-driven toolpath that generates VALID but EMPTY ('No passes to link.') names no wrong "
    "input - check otherSide, then the rail order (lower-rail-first is the order that produced "
    "passes), then the tool's flute length. 'Invalid contours.' instead means the feed is "
    "structurally wrong: one rail, closed rails, or two rails resolved as one chain.")

# The two strings an orientation fault announces itself with; neither names the setup as the thing
# to change, which is what the triage below adds. The error text is matched against THESE, not
# against a loose phrase another fault could carry.
_ORIENTATION_ERRORS = ("Cylindrical face not in tool orientation!",
                       "Selected face may not be safe for cutting at current tool orientation!")


def _is_orientation_error(text) -> bool:
    """Whether one errored operation's message is one of the two measured orientation faults."""
    said = (text or "").strip().lower()
    return any(measured.lower() in said for measured in _ORIENTATION_ERRORS)


_ORIENTATION_TRIAGE = (
    "A drilling cycle cuts along the SETUP's Z unless its own tool orientation overrides it "
    "(overrideToolView). A hole off that Z errored 'Cylindrical face not in tool orientation!'; "
    "binding Z to that face alone gave 'Selected face may not be safe for cutting at current tool "
    "orientation!'. Both clear with cam_edit_setup(wcs={'z_axis': <hole face>}), then "
    "wcs_orientation_flipZ='true'.")

_EMPTY_TRIAGE = ("A toolpath that generates VALID but EMPTY (hasToolpath False) cut nothing - "
                 + _cam_common.EMPTY_TOOLPATH_REMEDY)

# MEASURED (a 2D contour round a vise-clamped part): valid and EMPTY with this exact platform
# warning; clean (valid, no warning) once doLeadIn and doLeadOut were both false at the launch.
_LEAD_COLLISION_WARNING = "lead parameters would cause a collision"

_LEAD_COLLISION_TRIAGE = (
    "set doLeadIn and doLeadOut false (cam_edit_operation parameters) and regenerate - the leads, "
    "not the contour, collide.")


def _is_lead_collision_warning(text) -> bool:
    """Whether one warned operation's message is the measured turning lead-collision warning."""
    return _LEAD_COLLISION_WARNING in (text or "").strip().lower()


def _collect_op_health(ops, labels=None):
    """{"warnings": [{name, warning}], "errors": [{name, error}], "empty": [name],
    "empty_rail": [name], "nonfinite": [name]} over THESE operations, each row named by `labels`
    where given (an operation name is unique only within a setup)."""
    out = {"warnings": [], "errors": [], "empty": [], "empty_rail": [], "nonfinite": []}
    labels = list(labels or [])
    cam, _cerr = _cam_common.get_cam()
    for i, o in enumerate(ops or []):
        facts = _cam_common.op_state_facts(o, cam)
        name = labels[i] if i < len(labels) else facts["name"]
        if facts["has_error"]:
            out["errors"].append({"name": name, "error": (safe(lambda o=o: o.error) or "").strip()})
        if _cam_common.counts_as_warning(facts):
            out["warnings"].append({"name": name,
                                    "warning": (safe(lambda o=o: o.warning) or "").strip()})
        if facts["nonfinite_toolpath"]:
            out["nonfinite"].append(name)
        # An additive build op carries no toolpath by construction - the same exclusion the
        # operations reader applies, read off this op's OWN setup, not the caller's scope.
        additive = _cam_common.is_additive_setup(safe(lambda o=o: o.parentSetup))
        if _cam_common.is_empty_toolpath(facts, additive=additive):
            out["empty"].append(name)
            if _cam_common.is_rail_driven(o):
                out["empty_rail"].append(name)
    return out


def _document_ops():
    """Every operation NODE in the ACTIVE document - passed uncalled beside live_readiness's tally
    and walked only where the health lists are attached, so the two share a scope, not an instant."""
    cam, err = _cam_common.get_cam()
    return [] if err else _cam_common.operation_nodes(cam)


def _incomplete_note(live: dict) -> str:
    """The note for a still-generating status read over a live_readiness-shaped `live`: an errored
    op/setup/program never finishes, so that reads as a BLOCKER rather than as progress."""
    # The verdict sentence and the errored sample already ride as 'readiness' and
    # live_states.samples, each carrying an operation name and its error text - inlining either one
    # here is what leaves this note unbounded, so it POINTS at them the way the completed note does.
    blocked = bool(live.get("errored") or live.get("setups_errored") or live.get("programs_errored"))
    if blocked:
        return ("Not complete, and BLOCKED - 'readiness' carries the verdict and "
                "live_states.samples the first errored item. Waiting will NOT complete an errored "
                "item: fix it, then re-run cam_generate. cam_get(include=['operations']) lists "
                "every errored item with its full text.")
    if _cam_common.unsettled_count(live) == 0 and live.get("out_of_date", 0) > 0:
        return ("Not complete. WARNING: nothing is actively generating yet out-of-date ops remain "
                "- 'readiness' names what this installation will not generate at all. "
                "cam_get(include=['operations']) shows why.")
    return "Still generating in the background - check again later."


# `completed` is a GENERATION-lifecycle flag: a document reading "0 of 34 active ops valid" still
# polls completed:true once nothing is generating. The readiness pointer is a SEPARATE sentence, so
# the paths reporting a Future alone can state the flag without promising a verdict they lack.
_COMPLETED_MEANS = ("completed=true means no operation in scope has generating left to do - not a "
                    "success verdict. 'readiness' is the verdict.")

_COUNT_IS_INSTANTANEOUS = (
    " operations_completed is numberOfCompleted at THIS read - it can fall between reads and read 0 "
    "once complete.")


def _same_document(entry: dict):
    """True/False/None for "this generation's launch document IS the active one", compared on
    document_key and then on the launch document HANDLE, since a document saved mid-generation can
    answer a new key while being the same one. None means no identity was readable to compare."""
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
    """The refusal 'latest' returns when it cannot confirm the launch document is the active one -
    a different sentence for `same` False (identified, and another document) and None (no identity
    readable to compare)."""
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
    """Attach the per-op warning/error TEXT lists over the SAME `nodes` live_states was tallied
    over, name that scope in the payload as health_scope, and return the note clause stating it."""
    labels = _cam_common.op_labels(nodes)
    health = _collect_op_health([n.obj for n in nodes], labels)
    payload["operations_with_warnings"] = health["warnings"]   # [{name, warning}]
    payload["operations_with_errors"] = health["errors"]       # [{name, error}]
    payload["empty_toolpaths"] = health["empty"]               # generated but 0 toolpath length
    payload["nonfinite_toolpaths"] = health["nonfinite"]       # generated, motion not a number
    payload["counts"] = {"with_warnings": len(health["warnings"]),
                         "with_errors": len(health["errors"]),
                         "empty_toolpaths": len(health["empty"]),
                         "nonfinite_toolpaths": len(health["nonfinite"])}
    payload["health_scope"] = scope_label      # WHICH operations the three lists above describe
    shared = (" Repeated names show as 'Setup / op' paths, then by position."
              if any(label != n.name for label, n in zip(labels, nodes)) else "")
    # A triage rides the disclosure of the thing it triages, and only over the operations whose
    # own reading names it. The note names the KEYS; the rows beside them carry the counts.
    triage = []
    # A rail-driven empty op already earns the MORE SPECIFIC rail_triage below; this one is for
    # whatever empty row that triage does not cover, so the two never double up on one operation.
    if len(health["empty"]) > len(health["empty_rail"]):
        payload["empty_triage"] = _EMPTY_TRIAGE
        triage.append("empty_triage")
    if health["empty_rail"]:
        payload["empty_rail_toolpaths"] = health["empty_rail"]
        payload["rail_triage"] = _RAIL_TRIAGE
        triage.append("rail_triage")
    oriented = [row["name"] for row in health["errors"]
                if _is_orientation_error(row.get("error"))]
    if oriented:
        payload["operations_with_orientation_errors"] = oriented
        payload["orientation_triage"] = _ORIENTATION_TRIAGE
        triage.append("orientation_triage")
    lead_collided = [row["name"] for row in health["warnings"]
                     if _is_lead_collision_warning(row.get("warning"))]
    if lead_collided:
        payload["operations_with_lead_collisions"] = lead_collided
        payload["lead_collision_triage"] = _LEAD_COLLISION_TRIAGE
        triage.append("lead_collision_triage")
    return shared + (" Triage: " + ", ".join(triage) + "." if triage else "")


def handler(handle: str = "", target: str = "", include_operations: bool = True) -> dict:
    """Read toolpath generation progress: scoped to a cam_generate handle (or 'latest'), or to a
    setup/operation `target` name, or - both omitted - to the active document's live state."""
    key = (handle or "").strip()
    want_target = (target or "").strip()

    # 1. An explicit target picks the live path (no handle needed) - read that setup/op by name.
    if want_target:
        return _status_live(want_target, include_operations)

    # 2. An explicit handle (not 'latest') scopes to that launched generation's Future.
    if key and key.lower() != "latest":
        entry = _GENERATIONS.get(key)
        if not entry:
            released = _RELEASED.get(key)
            if released:
                return error(_released_miss(key, released))
            return error(
                f"No generation with handle '{handle}'. Active handles: "
                f"{', '.join(_GENERATIONS.keys()) or '(none)'}. Omit 'handle' to read live document "
                "state, or pass 'target' (a setup/operation name) to read an inline generation by name.")
        return _status_future(entry, key, include_operations)

    # 3. 'latest' - the most recent launched generation. It is a POSITIONAL pick naming neither a
    #    handle nor a DOCUMENT, so every way it can fail to identify a job is refused rather than
    #    answered from a stale entry that would read as a verdict on whatever is open now.
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

    # 4. handle AND target omitted: the ACTIVE DOCUMENT's live state. A registered handle does not
    #    capture this call - a bare read asks what is open now.
    return _status_live("document", include_operations)


def _handle_scope_state(entry: dict):
    """(live_dict, basis_label, health_ops, err) for THIS handle's OWN operations: a scoped launch
    settles on its target's ops, since a document-wide generating count keeps it incomplete while
    another job runs; a target that no longer resolves falls back to the document tally."""
    name = (entry.get("target_name") or "").strip()
    scoped = bool(name) and (entry.get("scope") or "document") in ("setup", "folder", "operation")
    if scoped:
        cam, cerr = _cam_common.get_cam(sync=True)
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


def _futures(entry: dict) -> list:
    """Every live Future ONE handle covers - a launch split across operations registers several,
    and the handle's progress is the sum of them."""
    return entry.get("futures") or [entry["future"]]


def _owned_future_readiness(live: dict, key: str, future_done: bool) -> str:
    """The readiness line for a handle that owns the Future being read."""
    if future_done:
        return live.get("readiness", "")
    blocked = bool(live.get("errored") or live.get("setups_errored")
                   or live.get("programs_errored"))
    # A blocked scope's own verdict IS the remedy (fix it, then re-run cam_generate) - a poll-again
    # line beside it reads as advice to keep waiting on an item that will never complete.
    if blocked:
        return live.get("readiness") or ""
    return (f"Generation Future for handle '{key}' is incomplete; poll "
            f"cam_get_status(handle='{key}') before launching generation again.")


def _status_future(entry: dict, key: str, include_operations: bool) -> dict:
    """The handle path: scope to a cam_generate-launched Future and report its progress. The per-op
    tallies read the ACTIVE document, so they ride only where the generating document is CONFIRMED
    to be that one; otherwise completion falls back to the Future alone."""
    futures = _futures(entry)

    total = _cam_common.futures_count(futures, "numberOfOperations")
    if total is None:
        total = entry.get("total")
    done_count = _cam_common.futures_count(futures, "numberOfCompleted")
    future_done = all(bool(safe(lambda f=f: f.isGenerationCompleted, False)) for f in futures)
    elapsed = round(time.time() - entry["started_at"], 1)

    doc_urn, doc_name = entry.get("doc_urn"), entry.get("doc_name")
    same_doc = _same_document(entry)
    # Said only where the number is published: an unread counter carries operations_completed null.
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
        # A DIFFERENT document and an UNCONFIRMABLE one are separate facts and get separate
        # sentences.
        payload["completed"] = future_done
        if same_doc is None:
            payload["completion_basis"] = ("this generation's Future alone (no document identity "
                                           "could be read to compare it with the active one)")
            # Named only where a name actually read: an interpolated None is a name nothing saw.
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
            _release(key, entry, elapsed)
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
            _release(key, entry, elapsed)
        return ok(payload)

    # The Future flips isGenerationCompleted a beat BEFORE live op state settles, so completed needs
    # BOTH: the Future done AND this handle's own scope settled - read off the OPERATIONS' states,
    # since isGenerating stays true over an operation already reading valid.
    completed = future_done and (_cam_common.unsettled_count(live) == 0)
    payload["completed"] = completed
    payload["completion_basis"] = basis   # whose operations settled this verdict
    owned_readiness = _owned_future_readiness(live, key, future_done)
    live = dict(live)
    live["readiness"] = owned_readiness
    payload["live_states"] = live  # valid/out_of_date/errored/generating/suppressed (+ setup/program for document)
    # A handle owns its incomplete Future, so the top-level and nested verdict are the same next step.
    payload["readiness"] = owned_readiness

    if not completed:
        blocked = bool(live.get("errored") or live.get("setups_errored")
                       or live.get("programs_errored"))
        if not future_done and not blocked:
            payload["note"] = (f"Generation Future for handle '{key}' is still incomplete - "
                               f"check again later with cam_get_status(handle='{key}'). "
                               "'readiness' carries the next step." + count_caveat)
        else:
            payload["note"] = _incomplete_note(live) + count_caveat
        return ok(payload)

    # The health lists cover the scope the tally above settled on - `basis` names that same scope,
    # so the payload cannot pair a scoped tally with document-wide warning rows.
    health_note = _attach_op_health(payload, health_ops(), basis) if include_operations else ""
    payload["note"] = ("Generation complete. " + _COMPLETED_MEANS
                       + " cam_get(include=['operations']) for detail."
                       + health_note + count_caveat + _cam_common.settled_clause(live))

    # Generation finished - release the entry so a later poll of this handle explains why it misses.
    _release(key, entry, elapsed, live)
    return ok(payload)


def _op_tally(ops, cam=None) -> dict:
    """A live_readiness-shaped tally scoped to just these ops, via the shared _cam_common.op_state_tally
    (the ONE per-op walk live_readiness's whole-document scan also uses) - this scoped poll just adds
    the setups_errored/programs_errored/samples shape a document-level poll carries (always 0/None
    here: a single setup/operation target has no setup- or program-level error of its own to report)."""
    t = _cam_common.op_state_tally(ops, cam)
    # warnings + warning_sample travel with the tally: they are what stops this SCOPED verdict
    # reading plainly ready over a job the document-level one would demote.
    return {"valid": t["valid"], "out_of_date": t["out_of_date"], "errored": t["errored"],
            "generating": t["generating"], "generating_settled": t["generating_settled"],
            "suppressed": t["suppressed"], "unread": t["unread"],
            "nonfinite": t["nonfinite"], "nonfinite_names": t["nonfinite_names"],
            "warnings": t["warnings"], "total": t["total"],
            "active": t["active"], "setups_errored": 0, "programs_errored": 0,
            # the OWNING setup's blocked_by, filled by _scope_state - a scoped verdict reads the
            # same setup-level prerequisites the document-level one does.
            "setups_blocked": [],
            "samples": {"op": t["op_sample"], "setup": None, "program": None,
                        "warning": t["warning_sample"]}}


def _scope_state(cam, target: str):
    """(live_dict, scope_label, health_ops, err) - live_readiness for a document scope, a scoped op
    walk for a named setup/folder/operation, in that same shape. health_ops is a zero-arg callable
    handing back this scope's operation NODES, so a still-generating poll never walks for lists."""
    want = (target or "").strip()
    if not want or want.lower() in ("all", "document", "*"):
        live, err = _cam_common.live_readiness()
        return (live or {}), "document", _document_ops, err
    node, rerr = _cam_common.resolve_cam_node(
        cam, want, kinds=("setup", "folder", "operation"), label="setup/folder/operation")
    if rerr:
        return None, None, None, rerr + " Omit 'target' to poll the whole document."
    kind = node.kind
    # Nodes, not bare Operations: only the node carries the path that separates two operations of
    # one name.
    nodes = [node] if kind == "operation" else _cam_common.operation_nodes_under(node)
    ops = [n.obj for n in nodes]
    tally = _op_tally(ops, cam)
    # A folder or operation is posted through its setup, so that setup's blocked_by gates this
    # verdict too.
    owner = _cam_common.owning_setup(node)
    tally["setups_blocked"] = _cam_common.blocked_setup_records([owner] if owner is not None else [])
    warning_sample = (tally.get("samples") or {}).get("warning")
    tally["readiness"] = _cam_common.readiness_verdict(tally, ops, warning_sample,
                                                       tally["setups_blocked"])
    return tally, f"{kind} '{node.name or want}'", (lambda: nodes), None


def _status_live(target: str, include_operations: bool) -> dict:
    """The no-handle path: report the live generation state of the target (a setup/operation name)
    or the whole document - reading op state DIRECTLY off the ACTIVE document, so an op generated
    inline (cam_create_operation(generate=true) / the UI) is readable with no cam_generate handle.
    completed=true only when nothing in scope is still generating."""
    cam, err = _cam_common.get_cam(sync=True)
    if err:
        return error(err)

    live, scope_label, health_ops, serr = _scope_state(cam, target)
    if serr:
        return error(serr)

    live = live or {}
    # Settled on the operations' own states: the isGenerating flag reads true over operations that
    # already answered valid, and a poll waiting on that flag waits on finished work.
    completed = _cam_common.unsettled_count(live) == 0
    payload = {
    "handle": None,                # live read: no self-minted handle needed
    "target": scope_label,
    "completed": completed,
    "operations_total": live.get("total"),
    "live_states": live,           # valid/out_of_date/errored/generating/suppressed (+ setup/program for document)
    "readiness": live.get("readiness", ""),
    }

    if not completed:
        payload["note"] = _incomplete_note(live)
        return ok(payload)

    health_note = _attach_op_health(payload, health_ops(), scope_label) if include_operations else ""
    # The lead states the verdict this read actually settled on - not "nothing is generating", which
    # is false exactly when settled_clause below names operations whose flag still reads true.
    payload["note"] = ("No operation in scope has generating left to do. " + _COMPLETED_MEANS
                       + " cam_get(include=['operations']) for detail." + health_note
                       + _cam_common.settled_clause(live))
    return ok(payload)


TOOL_DESCRIPTION = (
    "Read toolpath generation progress; 'readiness' carries the verdict. Pass 'handle' for one "
    "cam_generate launch, or 'target' for one launched in the UI."
)

tool = (
    Tool.create_simple(name="cam_get_status", description=TOOL_DESCRIPTION)
    .add_input_property("handle", {"type": "string",
            "description": "From cam_generate, or 'latest'."})
    .add_input_property("target", {"type": "string",
            "description": "Omit for the whole document."})
    .add_input_property("include_operations", {"type": "boolean"})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler,
                             run_on_main_thread=True)


def register_tool():
    register(item)
