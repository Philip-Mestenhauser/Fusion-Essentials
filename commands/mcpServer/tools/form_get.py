# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: list the design's T-spline Forms, or return one Form's control cage in the
shape form_create takes. READ-ONLY. FormFeature.bodies is the body at the END of the timeline, so
whether later features changed it is judged against the record form_create stored."""

import json

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _cam_common
from . import _common
from . import _form_common
from . import _inputs
from . import _tsm

_FORM = _inputs.FeatureRef("form")
_ROWS_DEFAULT, _ROWS_CEILING = 50, 500
# This tool's own wire bound on one cage, in characters - past it the cage is not returned.
_CAGE_MAX_CHARS = 100000
# Two reads of one unchanged body agree to far better than this (measured deterministic results).
_SAME_REL = 1e-6

_NOTE = ("form_get(form=..., include=['cage']) returns a Form's cage in the shape form_create "
         "takes.")
_NO_RECORD = ("record null: no creation record is stored on that Form, so modified_downstream is "
              "null. ")


def _same(a, b):
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and abs(a - b) <= _SAME_REL * max(1.0, abs(a), abs(b)))


def _modified(ff, record, suppressed, health):
    """True/False against the creation record; None: no record, suppressed, rolled back, unread."""
    if record is None or suppressed is not False or health == "rolled_back":
        return None
    key, attr = (("volume_cm3", "volume") if record.get("volume_cm3") is not None
                 else ("area_cm2", "area"))
    if record.get(key) is None:
        return None
    bodies = list(_common.iter_collection(safe(lambda: ff.bodies)))
    if not bodies:
        return True if record.get("brep_faces") else None
    faces = [_common.counted(lambda b=b: b.faces.count) for b in bodies]
    amount = [_common.measured(lambda b=b: getattr(b, attr), 1.0, 9) for b in bodies]
    if None in faces or None in amount:
        return None
    changed = sum(faces) != record.get("brep_faces") or not _same(sum(amount), record.get(key))
    box = _form_common.body_box_cm(bodies)
    was = record.get("box_cm")
    if box is None:
        return True if changed else None
    if isinstance(was, list) and len(was) == 2 and all(
            isinstance(c, list) and len(c) == 3 for c in was):
        # The corners catch a downstream move, which keeps the faces, the amount and the extent.
        return changed or not all(_same(n, w) for n, w in zip(box[0] + box[1], was[0] + was[1]))
    extent = record.get("extent_cm") or []
    return changed or not all(_same(box[1][i] - box[0][i], e) for i, e in enumerate(extent[:3]))


def _row(ff):
    """One Form's light record."""
    record = _form_common.read_record(ff)
    suppressed = _common.read_flag(lambda: ff.isSuppressed)
    health = _form_common.health_label(ff)
    return {"form": _form_common.form_label(ff),
            "timeline_index": safe(lambda: ff.timelineObject.index),
            "health": health, "suppressed": suppressed,
            "tspline_bodies": [safe(lambda t=t: t.name) for t in
                               _common.iter_collection(safe(lambda: ff.tSplineBodies))],
            # box_cm stays internal: its corners are the component's own space.
            "record": {k: v for k, v in record.items() if k != "box_cm"} if record else record,
            "modified_downstream": _modified(ff, record, suppressed, health)}


def _tspline_body(ff, label, body):
    """(TSplineBody, error): the one body of the Form, or the one named `body`."""
    bodies = list(_common.iter_collection(safe(lambda: ff.tSplineBodies)))
    names = [safe(lambda t=t: t.name) for t in bodies]
    if body:
        hits = [t for t, n in zip(bodies, names) if n == body]
        if len(hits) == 1:
            return hits[0], None
        return None, f"'{label}' holds no T-spline body named '{body}' - it holds {names}."
    if not bodies:
        return None, f"'{label}' holds no T-spline body, so it has no cage to read."
    if len(bodies) != 1:
        return None, (f"'{label}' holds {len(bodies)} T-spline bodies {names} - name one with "
                      "'body'.")
    return bodies[0], None


def _slice_cage(ff, label, body, inv_k):
    """The one Form's cage slice: census, cage (or why not) and whether the record still matches."""
    tb, terr = _tspline_body(ff, label, body)
    if terr:
        return None, terr
    text = safe(lambda: tb.getTSMDescription())
    if not isinstance(text, str):
        return None, f"'{label}': the T-spline body's TSM did not read."
    cage, census, reasons = _tsm.parse(text)
    out = {"form": label, "tspline_body": safe(lambda: tb.name), "census": census}
    record = _form_common.read_record(ff)
    out["record_matches_cage"] = (None if cage is None or record is None
                                  else record.get("cage_hash") == _tsm.canonical_hash(cage))
    if cage is not None:
        cage = {"vertices": [[round(c * inv_k, 6) for c in p] for p in cage["vertices"]],
                "faces": cage["faces"], "creases": cage["creases"]}
        size = len(json.dumps(cage, separators=(",", ":")))
        if size > _CAGE_MAX_CHARS:
            cage, reasons = None, [f"the cage is {size} characters; this tool returns at most "
                                   f"{_CAGE_MAX_CHARS}"]
    out["cage"] = cage
    if cage is None:
        out["cage_unrepresentable"] = reasons
    return out, None


def handler(form: str = "", body: str = "", include=None, max_results: int = 0,
            units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    k = _common.scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    raw = include.split(",") if isinstance(include, str) else (include or [])
    inc = [str(s).strip().lower() for s in raw if str(s).strip()]
    if any(s != "cage" for s in inc):
        return error(f"Unknown include {inc}. Valid: cage.")
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    if _inputs.in_form_edit(design):
        # MEASURED: inside a Form edit the edited Form is missing from its own collection.
        return ok({"in_form_edit": True, "complete": False, "forms": [],
                   "note": ("A Form edit is open, which hides that Form and the timeline - ask the "
                            "user to click Finish Form, then read again.")})
    forms = [ff for _comp, ff in _form_common.all_forms(design)]
    if form:
        ff, ferr = _form_common.resolve_form(design, _FORM, form)
        if ferr:
            return error(ferr)
        forms = [ff]
    if inc:
        if len(forms) != 1:
            return error(f"include=['cage'] reads one Form and the design holds {len(forms)} - "
                         "name it with 'form'.")
        out, cerr = _slice_cage(forms[0], _form_common.form_label(forms[0]), body, 1.0 / k)
        if cerr:
            return error(cerr)
        out["units"] = units or "mm"
        return ok(out)
    cap = _cam_common.clamp_rows(max_results, _ROWS_DEFAULT, _ROWS_CEILING)
    rows = [_row(ff) for ff in forms[:cap]]
    note = (_NO_RECORD if any(r["record"] is None for r in rows) else "") + _NOTE
    return ok({"forms": rows, "count": len(forms), "truncated": len(forms) > cap, "note": note})


TOOL_DESCRIPTION = (
    "List the T-spline Forms and their creation records; include=['cage'] returns the named "
    "Form's cage for form_create.")

tool = (
    Tool.create_simple(name="form_get", description=TOOL_DESCRIPTION)
    .add_input_property(*_FORM.as_property(brief=True))
    .add_input_property("body", {"type": "string"})
    .add_input_property("include", {"type": "array", "items": {"type": "string", "enum": ["cage"]}})
    .add_input_property("max_results", {"type": "integer"})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
