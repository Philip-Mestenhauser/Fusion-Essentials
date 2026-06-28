# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read-only MCP building blocks for inspecting CAM (Manufacture) data in the open document.

  cam_get_setups      -> setups: name, type, machine, selected models/fixtures/stock,
                         and operation counts
  cam_get_operations  -> operations per setup (or one setup): name, strategy, tool used,
                         and state (suppressed / has toolpath / valid)
  cam_get_references -> per setup, the EXTERNALLY REFERENCED (X-ref) components among
                         its models/fixtures/stock, resolved to their source document
                         UID + name + openable URL (feed those UIDs to doc_open)

Grounded in adsk.cam:
  - The CAM product is reachable WITHOUT switching to the Manufacture workspace:
    document.products.itemByProductType('CAMProductType') -> CAM
  - CAM.setups (iterable, .count, .item) -> Setup(.name, .operationType, .machine,
    .models, .fixtures, .stockSolids, .operations, .allOperations)
  - Setup.operations / Operation(.name via OperationBase, .tool, .operationState,
    .hasToolpath, .isToolpathValid, .isSuppressed)
  - Operation.tool -> Tool(.description; .parameters for structured values)

Read-only. Handlers run on the main thread (the default) because they touch adsk.*.
"""

import adsk.core
import adsk.cam
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()

# Cap how many models/operations we enumerate per container, defensively.
_MAX_ITEMS = 1000


def _get_cam():
    """Return the CAM product for the active document, or (None, reason).

    Works regardless of the active workspace. Returns (cam, None) on success or
    (None, message) if there is no document or no CAM data.
    """
    doc = None
    try:
        doc = app.activeDocument
    except Exception:
        doc = None
    if not doc:
        return None, "No active document."

    try:
        products = doc.products
    except Exception as e:
        return None, f"Could not access document products: {e}"

    try:
        cam = adsk.cam.CAM.cast(products.itemByProductType('CAMProductType'))
    except Exception as e:
        return None, f"Could not access CAM product: {e}"

    if not cam:
        return None, ("This document has no CAM (Manufacture) data. Open a document "
                      "with setups, or create them in the Manufacture workspace.")
    return cam, None


def _operation_type_name(op_type) -> str:
    """Map an OperationTypes enum value to a readable name, defensively."""
    mapping = {
        getattr(adsk.cam.OperationTypes, n, object()): n
        for n in ("MillingOperation", "TurningOperation", "JetOperation",
                  "AdditiveOperation")
    }
    return mapping.get(op_type, str(op_type))


def _model_names(collection) -> list:
    """Readable names of an ObjectCollection of models (Occurrence/BRepBody/MeshBody)."""
    names = []
    try:
        for i, m in enumerate(collection):
            if i >= _MAX_ITEMS:
                break
            names.append(_safe(lambda: m.name, "(unnamed)"))
    except Exception:
        pass
    return names


# ---------------------------------------------------------------------------
# cam_get_setups
# ---------------------------------------------------------------------------

def get_cam_setups_handler() -> dict:
    cam, err = _get_cam()
    if err:
        return _error(err)

    setups = []
    try:
        for i in range(cam.setups.count):
            if i >= _MAX_ITEMS:
                break
            s = cam.setups.item(i)
            setups.append({
                "name": _safe(lambda: s.name),
                "operation_type": _operation_type_name(_safe(lambda: s.operationType)),
                "is_active": _safe(lambda: s.isActive),
                "machine": _safe(lambda: s.machine.name) if _safe(lambda: s.machine) else None,
                "selected_models": _model_names(_safe(lambda: s.models, [])),
                "fixtures": _model_names(_safe(lambda: s.fixtures, [])),
                "stock_solids": _model_names(_safe(lambda: s.stockSolids, [])),
                "operation_count": _safe(lambda: s.operations.count, 0),
                "total_operation_count": _safe(lambda: s.allOperations.count, 0),
            })
    except Exception as e:
        return _error(f"Could not read setups: {e}")

    return _ok({"setup_count": len(setups), "setups": setups})


# ---------------------------------------------------------------------------
# cam_get_operations
# ---------------------------------------------------------------------------

def get_cam_operations_handler(setup: str = "") -> dict:
    """Operations across all setups, or just the named setup (`setup`)."""
    cam, err = _get_cam()
    if err:
        return _error(err)

    want = (setup or "").strip().lower()
    result_setups = []
    available = []
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            s_name = _safe(lambda: s.name)
            available.append(s_name)
            if want and (s_name or "").lower() != want:
                continue
            result_setups.append({
                "setup": s_name,
                "operations": _operations_in(s),
            })
    except Exception as e:
        return _error(f"Could not read operations: {e}")

    if want and not result_setups:
        return _error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available if n) or '(none)'}")

    # Also summarize the distinct tools used across the returned operations.
    tools_used = {}
    for rs in result_setups:
        for op in rs["operations"]:
            t = op.get("tool")
            if t:
                tools_used[t] = tools_used.get(t, 0) + 1

    return _ok({
        "setup_count": len(result_setups),
        "setups": result_setups,
        "tools_used": [{"tool": k, "operation_count": v} for k, v in tools_used.items()],
    })


def _operations_in(setup_obj) -> list:
    """Summarize the immediate operations of a setup (folders/patterns flattened)."""
    ops = []
    try:
        coll = setup_obj.allOperations  # includes nested folders/patterns
        for i, op in enumerate(coll):
            if i >= _MAX_ITEMS:
                break
            # Only real operations have a tool; folders/patterns are skipped by the
            # cast returning None.
            operation = adsk.cam.Operation.cast(op)
            if not operation:
                continue
            ops.append(_operation_summary(operation))
    except Exception:
        pass
    return ops


_OP_STATE_NAMES = {0: "valid", 1: "invalid", 2: "suppressed", 3: "no_toolpath"}


def _operation_summary(op) -> dict:
    tool_desc = None
    try:
        t = op.tool
        if t:
            tool_desc = t.description
    except Exception:
        tool_desc = None

    state = _safe(lambda: op.operationState)
    has_warn = bool(_safe(lambda: op.hasWarning, False))
    has_err = bool(_safe(lambda: op.hasError, False))
    summary = {
        "name": _safe(lambda: op.name),
        "tool": tool_desc,
        "strategy": _safe(lambda: op.strategy),
        # operationState is the authoritative roll-up; valid==generated & up to date.
        "state": _OP_STATE_NAMES.get(state, state),
        "has_toolpath": _safe(lambda: op.hasToolpath),
        "toolpath_valid": _safe(lambda: op.isToolpathValid),
        "is_generating": _safe(lambda: op.isGenerating),
        "is_suppressed": _safe(lambda: op.isSuppressed),
        "is_optional": _safe(lambda: op.isOptional),
        "has_warning": has_warn,
        "has_error": has_err,
    }
    # Surface the actual message text — a machinist reviewing toolpaths needs the content
    # (e.g. "Spindle speed is larger than supported", "empty toolpath"), not just a bool.
    if has_warn:
        summary["warning"] = (_safe(lambda: op.warning) or "").strip()
    # 'out of date' = it has (or should have) a toolpath but that toolpath is not valid,
    # and it isn't intentionally suppressed. This is what generate(skip_valid=true) will redo.
    summary["is_out_of_date"] = bool(
        state in (1, 3) and not summary["is_suppressed"]
    )
    if has_err:
        summary["error"] = _safe(lambda: op.error)
    return summary


# ---------------------------------------------------------------------------
# cam_get_references
# ---------------------------------------------------------------------------

def get_setup_references_handler(setup: str = "") -> dict:
    """Resolve each setup's externally-referenced (X-ref) components to source docs.

    For every model/fixture/stock occurrence in a setup that is an external
    reference, returns its source DataFile id (UID), name, version, and
    fusionWebURL — so the caller can `doc_open` the referenced fixture/part.
    """
    cam, err = _get_cam()
    if err:
        return _error(err)

    want = (setup or "").strip().lower()
    out_setups = []
    available = []
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            s_name = _safe(lambda: s.name)
            available.append(s_name)
            if want and (s_name or "").lower() != want:
                continue

            refs = []
            seen_ids = set()
            for role, coll in (("model", _safe(lambda: s.models, [])),
                               ("fixture", _safe(lambda: s.fixtures, [])),
                               ("stock", _safe(lambda: s.stockSolids, []))):
                for ref in _references_in(coll, role):
                    key = ref.get("source_id")
                    # De-dupe identical references that appear in multiple roles.
                    if key and key in seen_ids:
                        continue
                    if key:
                        seen_ids.add(key)
                    refs.append(ref)

            out_setups.append({"setup": s_name, "reference_count": len(refs),
                               "references": refs})
    except Exception as e:
        return _error(f"Could not read setup references: {e}")

    if want and not out_setups:
        return _error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available if n) or '(none)'}")

    return _ok({"setup_count": len(out_setups), "setups": out_setups})


def _references_in(collection, role: str) -> list:
    """Yield resolved external-reference info for occurrences in an ObjectCollection."""
    found = []
    try:
        for i, item in enumerate(collection):
            if i >= _MAX_ITEMS:
                break
            occ = adsk.fusion.Occurrence.cast(item)
            if not occ:
                # Not an occurrence (could be a BRepBody/MeshBody) -> no external ref.
                continue
            if not _safe(lambda: occ.isReferencedComponent, False):
                continue
            info = {"role": role, "occurrence_name": _safe(lambda: occ.name),
                    "source_id": None, "source_name": None, "version": None,
                    "fusion_web_url": None, "is_out_of_date": None}
            try:
                docref = occ.documentReference
                if docref:
                    df = _safe(lambda: docref.dataFile)
                    info["version"] = _safe(lambda: docref.version)
                    info["is_out_of_date"] = _safe(lambda: docref.isOutOfDate)
                    if df:
                        info["source_id"] = _safe(lambda: df.id)
                        info["source_name"] = _safe(lambda: df.name)
                        info["fusion_web_url"] = _safe(lambda: df.fusionWebURL)
            except Exception:
                pass
            found.append(info)
    except Exception:
        pass
    return found


# ---------------------------------------------------------------------------
# cam_activate_setup
# ---------------------------------------------------------------------------

def activate_setup_handler(setup: str = "") -> dict:
    """Activate a CAM setup by name (and fit the view), for review/screenshots."""
    want = (setup or "").strip()
    if not want:
        return _error("Provide 'setup' — the name of the setup to activate.")
    cam, err = _get_cam()
    if err:
        return _error(err)

    available = []
    target = None
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            nm = _safe(lambda: s.name)
            available.append(nm)
            if (nm or "").lower() == want.lower():
                target = s
                break
    except Exception as e:
        return _error(f"Could not read setups: {e}")

    if not target:
        return _error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available if n) or '(none)'}")

    try:
        target.activate()
    except Exception as e:
        return _error(f"Failed to activate '{want}': {e}")
    # Fit the view so a subsequent view_screenshot frames the setup.
    try:
        vp = app.activeViewport
        if vp:
            vp.fit()
    except Exception:
        pass
    return _ok({"activated": _safe(lambda: target.name),
                "note": "Setup activated and view fit. Use view_screenshot to capture it."})


# ---------------------------------------------------------------------------
# sys_get_tool_list
# ---------------------------------------------------------------------------

def get_tool_list_handler() -> dict:
    """Distinct cutting tools used across the document, with the ops that use each."""
    cam, err = _get_cam()
    if err:
        return _error(err)

    tools = {}  # description -> {"operations": [...], "setups": set()}
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            s_name = _safe(lambda: s.name)
            for op in _safe(lambda: s.allOperations, []):
                operation = adsk.cam.Operation.cast(op)
                if not operation:
                    continue
                desc = None
                try:
                    t = operation.tool
                    if t:
                        desc = t.description
                except Exception:
                    desc = None
                if not desc:
                    continue
                entry = tools.setdefault(desc, {"operations": [], "setups": set()})
                entry["operations"].append(_safe(lambda: operation.name))
                if s_name:
                    entry["setups"].add(s_name)
    except Exception as e:
        return _error(f"Could not read tools: {e}")

    tool_list = [{
        "tool": desc,
        "operation_count": len(info["operations"]),
        "operations": info["operations"],
        "setups": sorted(info["setups"]),
    } for desc, info in tools.items()]
    # Most-used first.
    tool_list.sort(key=lambda t: t["operation_count"], reverse=True)

    return _ok({"distinct_tool_count": len(tool_list), "tools": tool_list})


# ---------------------------------------------------------------------------
# cam_get_time
# ---------------------------------------------------------------------------

def get_machining_time_handler(setup: str = "") -> dict:
    """Estimated machining time for the whole doc, or one setup (`setup`)."""
    cam, err = _get_cam()
    if err:
        return _error(err)

    # Defaults match the Fusion dialog: 100% feed scale, ~250 in/min rapid, 1.5s tool change.
    feed_scale = 1.0
    rapid_feed = 1000.0   # cm/min equivalent default used by the API examples
    tool_change = 1.5

    targets = []  # (label, object)
    try:
        if (setup or "").strip():
            want = setup.strip().lower()
            for i in range(cam.setups.count):
                s = cam.setups.item(i)
                if (_safe(lambda: s.name) or "").lower() == want:
                    targets.append((s.name, s))
                    break
            if not targets:
                avail = [_safe(lambda: cam.setups.item(j).name) for j in range(cam.setups.count)]
                return _error(f"Setup not found: '{setup}'. "
                              f"Available: {', '.join(n for n in avail if n) or '(none)'}")
        else:
            for i in range(cam.setups.count):
                s = cam.setups.item(i)
                targets.append((_safe(lambda: s.name), s))
    except Exception as e:
        return _error(f"Could not read setups: {e}")

    results = []
    grand = 0.0
    for label, obj in targets:
        try:
            mt = cam.getMachiningTime(obj, feed_scale, rapid_feed, tool_change)
            secs = _safe(lambda: mt.machiningTime, 0.0) or 0.0
            grand += secs
            results.append({
                "setup": label,
                "machining_time_seconds": round(secs, 1),
                "machining_time_hms": _hms(secs),
                "feed_time_seconds": round(_safe(lambda: mt.totalFeedTime, 0.0) or 0.0, 1),
                "rapid_time_seconds": round(_safe(lambda: mt.totalRapidTime, 0.0) or 0.0, 1),
                "tool_changes": _safe(lambda: mt.toolChangeCount, 0),
            })
        except Exception as e:
            results.append({"setup": label, "error": str(e)})

    return _ok({
        "setup_count": len(results),
        "total_machining_time_seconds": round(grand, 1),
        "total_machining_time_hms": _hms(grand),
        "setups": results,
        "note": "Estimate using Fusion's default rapid/tool-change assumptions.",
    })


def _hms(seconds) -> str:
    try:
        s = int(round(seconds))
    except Exception:
        return "0:00:00"
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# list_nc_programs
# ---------------------------------------------------------------------------

def get_nc_programs_handler() -> dict:
    """List the document's NC programs with their reliably-readable details.

    Note: the human "Name / Number / Comment / Output folder" fields seen in the UI
    are NOT exposed as readable post parameters on the NCProgram API (verified live —
    postParameters typically only contains post options like 'metric'). So rather than
    fabricate those fields, we report what IS available: name, machine, post config,
    operation count, and the actual post parameters present (title + expression).
    """
    cam, err = _get_cam()
    if err:
        return _error(err)

    programs = []
    try:
        ncs = cam.ncPrograms
        for i in range(ncs.count):
            nc = ncs.item(i)
            entry = {
                "name": _safe(lambda: nc.name),
                "operation_count": None,
                "machine": _safe(lambda: nc.machine.name) if _safe(lambda: nc.machine) else None,
                "post": _safe(lambda: nc.postConfiguration.description) if _safe(lambda: nc.postConfiguration) else None,
                "post_parameters": [],
            }
            try:
                entry["operation_count"] = len(nc.operations)
            except Exception:
                pass
            # Report the actual post parameters as-is (whatever the post exposes).
            params = _safe(lambda: nc.postParameters)
            if params is not None:
                try:
                    for j in range(params.count):
                        p = params.item(j)
                        entry["post_parameters"].append({
                            "name": _safe(lambda: p.name),
                            "title": _safe(lambda: p.title),
                            "expression": _safe(lambda: p.expression),
                        })
                except Exception:
                    pass
            programs.append(entry)
    except Exception as e:
        return _error(f"Could not read NC programs: {e}")

    return _ok({"nc_program_count": len(programs), "nc_programs": programs})


# ---------------------------------------------------------------------------
# cam_compare_operations
# ---------------------------------------------------------------------------

def compare_operations_handler(operation_a: str = "", operation_b: str = "") -> dict:
    """Diff the CAM parameters of two operations (by name) to show what differs."""
    if not (operation_a or "").strip() or not (operation_b or "").strip():
        return _error("Provide both 'operation_a' and 'operation_b' (operation names).")
    cam, err = _get_cam()
    if err:
        return _error(err)

    op_a = _find_operation_by_name(cam, operation_a.strip())
    op_b = _find_operation_by_name(cam, operation_b.strip())
    if not op_a:
        return _error(f"Operation not found: '{operation_a}'.")
    if not op_b:
        return _error(f"Operation not found: '{operation_b}'.")

    params_a = _operation_params(op_a)
    params_b = _operation_params(op_b)

    all_keys = sorted(set(params_a) | set(params_b))
    differences = []
    same_count = 0
    for k in all_keys:
        a = params_a.get(k)
        b = params_b.get(k)
        if a == b:
            same_count += 1
        else:
            differences.append({"parameter": k,
                                "operation_a": a if k in params_a else "(not present)",
                                "operation_b": b if k in params_b else "(not present)"})

    return _ok({
        "operation_a": _safe(lambda: op_a.name),
        "operation_b": _safe(lambda: op_b.name),
        "tool_a": _op_tool_desc(op_a),
        "tool_b": _op_tool_desc(op_b),
        "same_parameter_count": same_count,
        "difference_count": len(differences),
        "differences": differences,
    })


def _find_operation_by_name(cam, name):
    want = name.lower()
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            for op in _safe(lambda: s.allOperations, []):
                operation = adsk.cam.Operation.cast(op)
                if operation and (_safe(lambda: operation.name) or "").lower() == want:
                    return operation
    except Exception:
        pass
    return None


def _operation_params(op) -> dict:
    """Read an operation's CAM parameters as {title-or-name: expression}."""
    out = {}
    try:
        params = op.parameters
        for i in range(params.count):
            p = params.item(i)
            key = _safe(lambda: p.title) or _safe(lambda: p.name)
            if not key:
                continue
            out[key] = _safe(lambda: p.expression)
    except Exception:
        pass
    return out


def _op_tool_desc(op):
    try:
        t = op.tool
        return t.description if t else None
    except Exception:
        return None


# --- result helpers (shared shape) ---


# --- tool definitions ---

_setups_tool = Tool.create_simple(
    name="cam_get_setups",
    description=(
        "Get the CAM (Manufacture) setups in the active document: each setup's name, "
        "operation type (milling/turning/etc.), machine, the models/fixtures/stock it "
        "has selected, and how many operations it contains. Works even if the active "
        "workspace is Design (no need to switch to Manufacture). Read-only. Returns an "
        "error if the document has no CAM data."
    ),
).strict_schema()
get_cam_setups_item = Item.create_tool_item(
    tool=_setups_tool, handler=get_cam_setups_handler, run_on_main_thread=True
)

_ops_tool = (
    Tool.create_simple(
        name="cam_get_operations",
        description=(
            "Get the CAM operations in the active document, grouped by setup. Each "
            "operation reports its name, the tool used (description incl. tool number), "
            "and state (has toolpath / valid / suppressed / optional / out-of-date). Also "
            "summarizes the distinct tools used. Pass 'setup' (a setup name) to limit to one "
            "setup, or omit it for all setups. Read-only. CAVEAT: the names/tools read fine "
            "anywhere, but the VALIDITY / is_out_of_date state is only trustworthy once the "
            "MANUFACTURE workspace has been entered — the CAM model does not re-evaluate against "
            "changed geometry (e.g. a freshly inserted/swapped part) until Manufacture is active. "
            "From the Design workspace these flags can be STALE (an op may read valid/up-to-date "
            "when it is really out of date). Switch to Manufacture before trusting them."
        ),
    )
    .add_input_property("setup", {"type": "string",
                                  "description": "Optional setup name to limit results to one setup."})
    .strict_schema()
)
get_cam_operations_item = Item.create_tool_item(
    tool=_ops_tool, handler=get_cam_operations_handler, run_on_main_thread=True
)

_refs_tool = (
    Tool.create_simple(
        name="cam_get_references",
        description=(
            "For each CAM setup, resolve its externally referenced (X-ref) components — "
            "among its selected models, fixtures, and stock — to the SOURCE document they "
            "come from. Each reference reports the source document id (UID), name, version, "
            "and an openable fusionWebURL. Feed a source_id to doc_open to open that "
            "referenced fixture/part. Pass 'setup' to limit to one setup. Read-only; works "
            "without switching to Manufacture."
        ),
    )
    .add_input_property("setup", {"type": "string",
                                  "description": "Optional setup name to limit results to one setup."})
    .strict_schema()
)
get_setup_references_item = Item.create_tool_item(
    tool=_refs_tool, handler=get_setup_references_handler, run_on_main_thread=True
)


_activate_tool = Tool.create_with_string_input(
    name="cam_activate_setup",
    description=(
        "Activate a CAM setup by name and fit the view, so you can then capture it with "
        "view_screenshot. Use this to review each setup in turn. Changes the active setup."
    ),
    input_param_name="setup",
    input_param_description="The setup name to activate.",
)
activate_setup_item = Item.create_tool_item(
    tool=_activate_tool, handler=activate_setup_handler, run_on_main_thread=True
)

_tool_list_tool = Tool.create_simple(
    name="sys_get_tool_list",
    description=(
        "List the distinct cutting tools used across the document's CAM operations — each "
        "tool's description (includes tool number, type, and geometry), how many operations "
        "use it, which operations, and which setups. Sorted by most-used. This is the "
        "machinist's tool sheet. Read-only; works without switching to Manufacture."
    ),
).strict_schema()
get_tool_list_item = Item.create_tool_item(
    tool=_tool_list_tool, handler=get_tool_list_handler, run_on_main_thread=True
)

_time_tool = (
    Tool.create_simple(
        name="cam_get_time",
        description=(
            "Estimate machining (cycle) time for the document's CAM program: per setup and "
            "total, with feed time, rapid time, and tool-change count. Uses Fusion's default "
            "rapid/tool-change assumptions. Pass 'setup' to limit to one setup. Read-only. "
            "Note: requires generated toolpaths to be meaningful."
        ),
    )
    .add_input_property("setup", {"type": "string",
                                  "description": "Optional setup name to limit to one setup."})
    .strict_schema()
)
get_machining_time_item = Item.create_tool_item(
    tool=_time_tool, handler=get_machining_time_handler, run_on_main_thread=True
)

_nc_tool = Tool.create_simple(
    name="cam_get_nc_programs",
    description=(
        "List the document's NC programs (post/output jobs): each program's name, machine, "
        "post configuration, operation count, and the post parameters it exposes. Read-only — "
        "for reviewing how programs are set up. Works without switching to Manufacture. "
        "(Note: the UI's Name/Number/Comment/Output-folder fields are not exposed as readable "
        "API parameters, so they are not reported here.)"
    ),
).strict_schema()
get_nc_programs_item = Item.create_tool_item(
    tool=_nc_tool, handler=get_nc_programs_handler, run_on_main_thread=True
)

_compare_tool = (
    Tool.create_with_string_input(
        name="cam_compare_operations",
        description=(
            "Compare two CAM operations (by name) and report exactly which of their "
            "parameters differ — and the value on each side. Use this to understand what "
            "makes one machining strategy different from a similar one. Also reports the "
            "tool each uses and how many parameters match. Read-only."
        ),
        input_param_name="operation_a",
        input_param_description="Name of the first operation.",
    )
    .add_input_property("operation_b", {"type": "string", "description": "Name of the second operation."})
)
compare_operations_item = Item.create_tool_item(
    tool=_compare_tool, handler=compare_operations_handler, run_on_main_thread=True
)


def register_tool():
    register(get_cam_setups_item)
    register(get_cam_operations_item)
    register(get_setup_references_item)
    register(activate_setup_item)
    register(get_tool_list_item)
    register(get_machining_time_item)
    register(get_nc_programs_item)
    register(compare_operations_item)
