# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: measure the gap, normal angle and curvature jump along seams. READ."""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _continuity
from . import _inputs

MAX_EDGES = 20
MAX_SAMPLES = 50

_EDGES = _inputs.GeometryHandleList("edges", require="edge", required=True)
_AGAINST = _inputs.BodyRef("against", kind="brep")


def _row(i, mode, facts, k):
    """One edge's published facts, lengths in the call's units and curvature per unit length."""
    return {"edge": i, "mode": mode, "samples": facts["samples"],
            "max_gap": round(facts["max_gap_cm"] / k, 6),
            "max_normal_angle_deg": round(facts["max_normal_angle_deg"], 6),
            "max_curvature_jump": round(facts["max_curvature_jump_per_cm"] * k, 9),
            "worst_at": [round(c / k, 6) for c in facts["worst_at_cm"]],
            "normals_opposed": facts["normals_opposed"],
            "off_face_samples": facts["off_face_samples"]}


def _faces_of(i, edge, other):
    """(mode, face A, face B, error) for the seam along edge i."""
    faces = list(_common.iter_collection(safe(lambda: edge.faces)))
    if len(faces) == 2:
        return "one_body", faces[0], faces[1], None
    if len(faces) != 1:
        return None, None, None, (f"'edges'[{i}] reads {len(faces)} faces; a seam lies between "
                                  "exactly two.")
    if other is None:
        return None, None, None, (f"'edges'[{i}] borders one face, so its partner face is on "
                                  "another body - pass that body as 'against'.")
    partner, perr = _continuity.partner_face(edge, other)
    if perr:
        return None, None, None, f"'edges'[{i}]: {perr}"
    return "two_bodies", faces[0], partner, None


def handler(edges=None, against: str = "", samples: int = 9, units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    k = _common.scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= MAX_SAMPLES:
        return error(f"'samples' is a whole number from 1 to {MAX_SAMPLES} - got {samples!r}.")
    if not _common.design():
        return error("No active design. Create or open a document first (see doc_new).")
    ents, err = _EDGES.resolve(edges)
    if err:
        return error(err)
    if len(ents) > MAX_EDGES:
        return error(f"'edges' takes at most {MAX_EDGES} - got {len(ents)}. Measure them in "
                     "batches.")
    other = None
    if isinstance(against, str) and against.strip():
        other, err = _AGAINST.resolve(against)
        if err:
            return error(err)
    rows = []
    for i, edge in enumerate(ents):
        mode, face_a, face_b, ferr = _faces_of(i, edge, other)
        if ferr:
            return error(ferr)
        facts, serr = _continuity.seam(edge, face_a, face_b, samples,
                                       one_body=mode == "one_body")
        if serr:
            return error(f"'edges'[{i}]: {serr}.")
        rows.append(_row(i, mode, facts, k))
    worst = max(rows, key=lambda r: (round(r["max_normal_angle_deg"], 4), r["max_curvature_jump"]))
    out = {"edges": rows, "worst_edge": worst["edge"],
           "max_normal_angle_deg": max(r["max_normal_angle_deg"] for r in rows),
           "max_curvature_jump": max(r["max_curvature_jump"] for r in rows),
           "max_gap": max(r["max_gap"] for r in rows), "units": units or "mm",
           "note": f"max_curvature_jump is per {units or 'mm'}."}
    off = sum(r["off_face_samples"] for r in rows)
    if off:
        out["note"] += (f" {off} sample(s) read isParameterOnFace false on a face; the maxima "
                        "include them.")
    unused = [r["edge"] for r in rows if r["mode"] == "one_body"]
    if other is not None and unused:
        out["note"] += (f" 'against' was not used for 'edges'{unused}: each borders two faces of "
                        "its own body, and that seam is the one read.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Measure gap, normal angle and curvature jump across each edge's seam; a one-faced edge "
    "reads against the 'against' body."
)

tool = (
    Tool.create_simple(name="model_measure_continuity", description=TOOL_DESCRIPTION)
    .add_input_property("edges", {**_EDGES.schema(), "maxItems": MAX_EDGES})
    .add_input_property(*_AGAINST.as_property())
    .add_input_property("samples", {"type": "integer", "maximum": MAX_SAMPLES})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_required_input("edges")
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
