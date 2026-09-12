# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Open and close a base-feature edit scope - the multi-call escape hatch tool code reaches for
through _design_common.run_in_base_feature instead. While a scope is OPEN the API hides it, so the
BaseFeature object add() returned is the only handle to it. WRITES."""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, target_component
from . import _common
from . import _inputs
from . import _write_guard

# Base features ONLY exist in a parametric design - a base feature IS a direct-edit scope inside one.
_PARAMETRIC_GUARD = _inputs.ModeGuard(
    _inputs.MODE_PARAMETRIC,
    why="A base feature is a direct-edit scope inside a parametric design.",
    fix_hint=("In a direct design you already edit geometry directly - no base feature is needed; "
        "see design_get(include=['mode'])."))


# The captured (document handle, open scope) pairs. While a base-feature edit scope is open the API
# hides it: baseFeatures reports count==0, itemByName returns None, and Design.timeline raises - so
# the BaseFeature object add() returned and its document identity must both survive until finish().
_OPEN_BASE_FEATURES = []

_ACTIONS = ("start", "finish")
_MISSING_BASE_FEATURE = (
    "No captured or existing base feature named '{name}' in the active document. Open captured "
    "scope names here: {available}. Retry with the exact base_feature returned by start, omit "
    "base_feature to close the active document's captured scopes, or inspect the timeline before "
    "naming a finished feature.")
_UNREADABLE_START_OWNER = (
    "The active document identity is unreadable, so this call cannot safely own a new base-feature "
    "scope. Refresh the active document and retry.")
_UNREADABLE_ACTIVE_OWNER = (
    "The active document identity is unreadable, so this call refused before finishEdit. All "
    "captured handles were kept; refresh the active document and retry.")
_UNREADABLE_CAPTURED_OWNER = (
    "A captured base-feature scope has no readable document owner, so this call refused before "
    "finishEdit. Its only handle was kept, but it cannot be assigned safely; finish that open scope "
    "in Fusion before continuing.")
_NO_ACTIVE_SCOPE = "No captured scope owned by the active document was open to close."
_ACTIVE_SCOPE_REMAINS = (
    "A captured scope owned by the active document remains open; editing is unconfirmed until that "
    "handle closes successfully.")


def _resolve_base_feature(design, comp, name):
    """Find an existing base feature by name across the design (the named comp first, then root, then
    all components). Returns the BaseFeature or None."""
    nm = (name or "").strip()
    if not nm:
        return None
    candidates = []
    if comp is not None:
        candidates.append(comp)
    root = safe(lambda: design.rootComponent)
    # same_component, never `is`/`in`: component wrappers are not identity-stable, so an identity
    # dedupe never fires. Both de-dupes drop a candidate only on a PROVEN match - this is a SEARCH
    # order, and an unproven pair costs one repeated read where dropping it could skip the holder.
    if root is not None and _common.same_component(root, comp) is not True:
        candidates.append(root)
    for c in _common.all_components(design):
        if c is not None and not any(_common.same_component(c, k) is True for k in candidates):
            candidates.append(c)
    for c in candidates:
        bf = safe(lambda c=c: c.features.baseFeatures.itemByName(nm))
        actual = safe(lambda b=bf: b.name) if bf else None
        if isinstance(actual, str) and actual.strip().lower() == nm.lower():
            return bf
    return None


def _captured_named(scopes, eligible, name):
    """Eligible captured scope indices whose readable names exactly match ``name``."""
    want = name.strip().lower()
    matches, readable = [], []
    for index, (_owner, bf) in enumerate(scopes):
        if index not in eligible:
            continue
        actual = safe(lambda b=bf: b.name)
        if not isinstance(actual, str) or not actual.strip():
            continue
        readable.append(actual)
        if actual.strip().lower() == want:
            matches.append(index)
    return matches, readable


def handler(action: str = "start", base_feature: str = "") -> dict:
    """Start or finish a base-feature edit scope - the multi-call escape hatch; tool code takes the
    run_in_base_feature helper instead. WRITES."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    act = (action or "start").strip().lower()
    if act not in _ACTIONS:
        return error(f"'action' must be one of: {', '.join(_ACTIONS)} (got '{action}').")

    comp = target_component(design)

    if act == "start":
        # Mode gate only on START: while a scope is OPEN the design reads DIRECT, so gating 'finish'
        # on MODE_PARAMETRIC would make the tool unable to close the scope it opened.
        good, mode_err = _PARAMETRIC_GUARD.check(design)
        if not good:
            return mode_err
        owner = _write_guard.active_document_handle()
        if owner is None:
            return error(_UNREADABLE_START_OWNER)
        base_features = safe(lambda: comp.features.baseFeatures)
        if base_features is None:
            return error("This component has no baseFeatures collection - cannot create a base "
    "feature here.")
        # add() then startEdit(): do NOT safe()-wrap the mutation; check the bool return explicitly.
        bf = base_features.add()
        if not bf:
            return error("BaseFeatures.add() returned nothing - could not create a base feature.")
        # Name BEFORE startEdit - once the scope is open the feature is invisible to the API, so a
        # rename attempt then would target nothing.
        bf_name, rename_warning = _common.apply_rename(bf, base_feature)
        started = bf.startEdit()
        if started is False:
            safe(lambda: bf.deleteMe())      # the scope will not open - do not leave the orphan
            return error("Could not enter base-feature edit (startEdit returned false).")
        # CAPTURE the open scope's object and the active document it belongs to. A document wrapper
        # is not identity-stable, so the shared opaque session handle is the ownership key.
        _OPEN_BASE_FEATURES.append((owner, bf))
        start_payload = {
        "action": "start",
        "base_feature": bf_name,
        "editing": True,
        "component": safe(lambda: comp.name),
        "open_scope_count": len(_OPEN_BASE_FEATURES),
        "note": ("Base-feature edit OPEN - geometry from subsequent tool calls lands in this scope. "
            "While it is open the design READS as 'direct' and the timeline is inaccessible; "
            "that reverts on finish. ALWAYS pair with model_base_feature(action='finish') - no "
            "name needed, it closes the scope this call opened. For a single mesh/import op use "
            "the mesh_* tools, which open and finish a scope in one call."),
        }
        if rename_warning:
            start_payload["rename_warning"] = rename_warning
        return ok(start_payload)

    # act == "finish" - closing the captured objects directly, with no mode gate, because the design
    # READS direct while a scope is open. finishEdit() returns it to parametric.
    nm = (base_feature or "").strip()
    active_owner = _write_guard.active_document_handle()
    open_scopes = list(_OPEN_BASE_FEATURES)
    if open_scopes and active_owner is None:
        return error(_UNREADABLE_ACTIVE_OWNER)
    if any(owner is None for owner, _bf in open_scopes):
        return error(_UNREADABLE_CAPTURED_OWNER)
    eligible = {
        index for index, (owner, _bf) in enumerate(open_scopes)
        if active_owner is not None and owner is not None and owner == active_owner
    }

    # An explicit name selects before any mutation. An unread name never proves a match, and a miss
    # refuses while every captured handle remains intact.
    named_target = None
    if nm:
        selected, readable = _captured_named(open_scopes, eligible, nm)
        if not selected:
            named_target = _resolve_base_feature(design, comp, nm)
            if named_target is None:
                available = _common.named_with_remainder([f"'{name}'" for name in readable])
                return error(_MISSING_BASE_FEATURE.format(
                    name=nm, available=available or "(none readable)"))
        selected_indexes = set(selected)
    else:
        selected_indexes = eligible

    # Close selected captured scopes in LIFO order. A finishEdit() that raises or returns False
    # leaves the scope NOT PROVEN closed, so its only handle remains available for a retry.
    pending = [(index, bf) for index, (_owner, bf) in enumerate(open_scopes)
               if index in selected_indexes]
    closed, unclosed, closed_indexes = [], [], set()
    for index, bf in reversed(pending):
        try:
            finished = bf.finishEdit()
        except Exception as e:
            unclosed.append({"name": safe(lambda b=bf: b.name), "finished": None, "error": str(e)})
            continue
        if finished is False:
            unclosed.append({"name": safe(lambda b=bf: b.name), "finished": False})
        else:
            closed.append({"name": safe(lambda b=bf: b.name), "finished": True})
            closed_indexes.add(index)
    _OPEN_BASE_FEATURES[:] = [record for index, record in enumerate(open_scopes)
                              if index not in closed_indexes]

    # A selected captured scope is now enumerable, so only a target resolved before the close takes
    # this path; otherwise the same object would receive finishEdit() twice.
    named = next((row["name"] for row in closed if row["name"] is not None), None) if nm else None
    named_error = None
    if named_target is not None:
        try:
            named_target.finishEdit()
        except Exception as e:
            named_error = str(e)
        else:
            named = safe(lambda: named_target.name)

    now_mode = _inputs.current_design_type(design)
    active_retained = any(owner == active_owner for owner, _bf in _OPEN_BASE_FEATURES)
    note = (f"Closed {len(closed)} captured open base-feature scope(s); design is now {now_mode}."
            if closed else "No scope was open in this session to close.")
    if not closed and active_retained:
        note = _ACTIVE_SCOPE_REMAINS
    elif not closed and _OPEN_BASE_FEATURES:
        note = _NO_ACTIVE_SCOPE
    if unclosed:
        names = ", ".join(str(u["name"] or "?") for u in unclosed)
        note = (f"{len(unclosed)} scope(s) did NOT confirm closed ({names}) - each may still be "
                "OPEN, which keeps the design reading direct and the timeline inaccessible. Their "
                "handles are KEPT (an open base feature can be reached no other way), so "
                "model_base_feature(action='finish') retries them.")
    elif not closed and not active_retained and now_mode == _inputs.MODE_DIRECT:
        note += (" Note: a scope opened by a DIFFERENT session/tool cannot be seen while it is open "
                 "(the API hides an in-edit base feature) - only the session that opened it holds "
                 "the object needed to close it.")
    out = {
        "action": "finish",
        # null, not False: an active-document handle still captured leaves the edit state unknown.
        "editing": None if unclosed or active_retained else False,
        "closed_scopes": closed,
        "named_finished": named,
        "design_mode_now": now_mode,
        "open_scope_count": len(_OPEN_BASE_FEATURES),
        "note": note,
    }
    if unclosed:
        out["unclosed_scopes"] = unclosed
    if named_error:
        out["named_finish_error"] = named_error
    return ok(out)


TOOL_DESCRIPTION = ("Open or close a base-feature direct-edit scope; the mesh_* tools open and "
            "finish one per call.")

tool = (
    Tool.create_simple(
        name="model_base_feature",
        description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "enum": list(_ACTIONS)})
    .add_input_property("base_feature", {"type": "string",
            "description": "Names the new scope on 'start'; picks one to finish."})
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        rung="exists",
        evidence_test="tests/unit/test_model_base_feature.py::TestBaseFeature"
                      "::test_start_errors_and_cleans_up_when_startEdit_returns_false"))


def register_tool():
    register(item)
