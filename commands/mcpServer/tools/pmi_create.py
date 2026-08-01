# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block that CREATES a PMI annotation (a 3D note attached to model geometry). WRITES.
kind='note' is a leader-line note on ONE face/edge/vertex whose text may embed GD&T/modifier
symbols as {symbol} tokens; kind='hole_note' reads its callout (dia/depth/counterbore/thread) off
the hole faces it is attached to. The created annotation is read back (name/text) - a null add()
is an error, never a silent success."""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs
from . import _outputs
from . import _pmi

app = adsk.core.Application.get()

RETURNS = [
    _outputs.ReturnsName("annotation", of="PMI annotation", consumers=["pmi_edit", "pmi_delete"]),
]

_KIND = _inputs.Choice(
    "kind", options=["note", "hole_note"], required=True,
    description="note: leader-line note on one face/edge/vertex. hole_note: hole/thread callout "
                "read off the hole's faces.")
_GEOMETRY = _inputs.GeometryHandleList(
    "geometry", require="any", required=True,
    description="find_geometry handles. note: exactly ONE face/edge/vertex. hole_note: the "
                "face(s) of one or more geometric holes/bosses (e.g. the cylinder face).")


def _resolve_note_entity(ents):
    """The single face/edge/vertex a leader note attaches to, or an error naming the problem."""
    if len(ents) != 1:
        return None, (f"kind='note' takes exactly ONE geometry handle (got {len(ents)}). "
                      "A leader note attaches to a single face/edge/vertex.")
    e = ents[0]
    if type(e).__name__ not in ("BRepFace", "BRepEdge", "BRepVertex"):
        return None, (f"kind='note' needs a face/edge/vertex handle, got {type(e).__name__}. "
                      "Use find_geometry(kind=...) for the right one.")
    return e, None


def handler(kind=None, geometry=None, text="", name="", text_point=None, units="mm") -> dict:
    """See TOOL_DESCRIPTION."""
    d = _common.design()
    if not d:
        return error("No active design. Create or open a document first (see doc_new).")
    kind_v, kerr = _KIND.resolve(kind)
    if kerr:
        return error(kerr)
    ents, gerr = _GEOMETRY.resolve(geometry)
    if gerr:
        return error(gerr)
    f = _common.scale(units)
    if f is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")

    if kind_v == "note":
        ent, eerr = _resolve_note_entity(ents)
        if eerr:
            return error(eerr)
        segs, serr = _pmi.build_segments(text)
        if serr:
            return error(serr)
        comp = safe(lambda: ent.body.parentComponent) or d.rootComponent
        notes = safe(lambda: comp.pmiAnnotations.leaderLineNotes)
        if notes is None:
            return error(f"Component '{safe(lambda: comp.name)}' has no PMI collection - "
                         "this Fusion build may not support PMI authoring.")
        try:
            note_in = notes.createInput(ent)
            # Pin the extension explicitly: an unpinned input can land the note below the
            # platform's own edit-time floor, bricking every later segment edit (live-verified).
            cur = safe(lambda: note_in.leaderLineExtension)
            if cur is None or cur < _pmi.LEADER_EXT_FLOOR:
                note_in.leaderLineExtension = _pmi.LEADER_EXT_DEFAULT
            note_in.segments = segs
            ann = notes.add(note_in)
        except Exception as e:
            return error(f"Leader note creation failed: {e}")
    else:
        non_faces = [type(e).__name__ for e in ents if type(e).__name__ != "BRepFace"]
        if non_faces:
            return error(f"kind='hole_note' takes FACE handles only (got {non_faces}). "
                         "Use find_geometry(kind='cylinder_face') on the hole.")
        if (text or "").strip():
            return error("kind='hole_note' composes its callout from the hole geometry - it does "
                         "not take 'text'. Create it plain, then pmi_edit(action='set_text') to "
                         "append custom segments.")
        comp = safe(lambda: ents[0].body.parentComponent) or d.rootComponent
        notes = safe(lambda: comp.pmiAnnotations.holeThreadNotes)
        if notes is None:
            return error(f"Component '{safe(lambda: comp.name)}' has no PMI collection - "
                         "this Fusion build may not support PMI authoring.")
        try:
            ann = notes.add(notes.createInput(ents))
        except Exception as e:
            return error(f"Hole/thread note creation failed: {e}. The faces must belong to "
                         "geometric holes (cylinder/counterbore/countersink faces) or "
                         "cylindrical bosses.")
    if ann is None:
        return error("The PMI add() returned nothing - no annotation was created. The geometry "
                     "may not support this note kind (e.g. hole_note on a non-hole face).")

    rename_warning = None
    if (name or "").strip():
        try:
            ann.name = name.strip()
        except Exception as e:
            rename_warning = f"created, but the rename to '{name}' failed: {e}"
        if safe(lambda: ann.name) != name.strip() and rename_warning is None:
            rename_warning = f"created, but the name did not take (still '{safe(lambda: ann.name)}')."

    if text_point is not None:
        _pt, pt_err = _pmi.set_text_point(ann, text_point, f)
        if pt_err:
            return error(pt_err + " (the annotation WAS created: "
                         f"'{safe(lambda: ann.name)}' - reposition with pmi_edit)")

    rec = _pmi.annotation_record(comp, ann)
    rec["annotation"] = rec.get("name")
    markup = _pmi.segments_markup(ann)
    if markup is not None:
        rec["markup"] = markup
    if rename_warning:
        rec["rename_warning"] = rename_warning
    rec["note"] = "Verify placement visually with view_screenshot; read all PMI with pmi_get."
    return ok(rec)


TOOL_DESCRIPTION = (
"Create a PMI annotation (a 3D note attached to model geometry, shown in the viewport and "
"exported with the model). kind='note': a leader-line note on ONE face/edge/vertex; 'text' may "
"embed GD&T and modifier symbols as {symbol} tokens (e.g. '{flatness}0.05' or '{diameter}6 H7'; "
"an unknown token's error lists the legal set) and newlines break lines. kind='hole_note': a "
"hole/thread callout that reads dia/depth/counterbore/thread off the hole faces passed in - no "
"'text'. Optional name= renames the annotation; text_point=[x,y,z] (model space, 'units') places "
"the text to avoid overlaps. Read PMI back with pmi_get.\n"
+ _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="pmi_create", description=TOOL_DESCRIPTION)
    .add_input_property("kind", _KIND.schema())
    .add_input_property("geometry", _GEOMETRY.schema())
    .add_input_property("text", {"type": "string",
        "description": "The note content (kind='note' only). {symbol} tokens allowed; newlines break lines."})
    .add_input_property("name", {"type": "string",
        "description": "Optional name for the annotation (default: Fusion's Note1/Hole Note1 numbering)."})
    .add_input_property("text_point", {
        "type": "array", "items": {"type": "number"},
        "description": "Optional [x,y,z] text anchor in model space ('units' scale)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_required_input("kind")
    .add_required_input("geometry")
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
