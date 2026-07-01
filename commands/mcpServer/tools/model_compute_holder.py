# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: turn a solid holder model into a CAM TOOL HOLDER profile (the headless,
agent-drivable form of the "Add Tool Holder" command).

  model_compute_holder -> reduce a body of revolution (the holder geometry) to a stack of
                    (height, lower-diameter, upper-diameter) segments and the holder library JSON,
                    from three find_geometry handles: the body, its axis, and an end datum on that
                    axis. Read-only (computes + returns; does NOT write to a tool library).

Why this exists: the create-holder geometry is the repo's signature feature, but it lived only behind
a Fusion dialog (click body + axis + end face). This exposes the SAME extracted core (tools/_holder.py)
to an agent, so it can batch-convert holders it uploads - geometry-as-values: pass three handles from
find_geometry, get the profile back. The interactive command still serves one-at-a-time users; both
call the one core.

SCOPE (deliberate): this returns the computed holder JSON + segment profile. It does NOT add the
holder to a CAM tool library - library WRITES wait for the dedicated library building-block family,
because a tool brought into a document is a hard FORK of the library data (not a live link), and that
semantics deserves its own correct tools. Take this tool's `holder_json` and add it to a library by
hand (or with those tools when they land).

INPUTS (geometry-as-values - all find_geometry handles, not names/coords):
  - body: a SOLID body handle (the holder model).
  - axis: a CYLINDRICAL/CONICAL face handle OR a straight EDGE handle - the axis of rotation.
  - end_datum: a PLANAR face / edge / vertex handle NORMAL to the axis - fixes z=0 along the axis.

Grounded in tools/_holder.py (the extracted command core) + adsk.fusion (handle entities).
Handler runs on the main thread; read-only (builds no feature, writes no library).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs
from . import _holder

app = adsk.core.Application.get()

# body = a SOLID body (handle preferred - holder bodies are auto-named); axis + end_datum are raw
# geometry handles resolved to live entities, then handed to the _holder axis/datum routines (which
# do the per-kind validation: a cyl/cone/edge axis, a normal planar/edge/vertex datum).
_BODY = _inputs.BodyRef("body", kind="solid", required=True,
                        description="The solid holder body to profile.")
_AXIS = _inputs.GeometryHandle("axis", require="any", required=True,
                               description="The axis of rotation: a CYLINDRICAL/CONICAL face or a straight EDGE handle.")
_END = _inputs.GeometryHandle("end_datum", require="any", required=True,
                              description="An end datum on the axis: a PLANAR face / edge / vertex handle NORMAL to the axis (sets z=0).")


def handler(body: str = "", axis: str = "", end_datum: str = "",
            name: str = "", product_id: str = "", product_link: str = "", vendor: str = "") -> dict:
    """Compute a CAM tool-holder profile + library JSON from three geometry handles. Read-only.

    body / axis / end_datum: find_geometry handles (the holder solid, its axis face/edge, and a
    normal end datum). name/product_id/product_link/vendor: optional metadata stamped into the holder
    JSON (name defaults to the active document name). Returns the (height, lower/upper-diameter)
    segments in mm and the full holder JSON to add to a tool library yourself. Does NOT write a library.
    """
    design = _common.design()
    if not design:
        return error("No active design. Open the holder model first (see doc_open).")

    body_ent, berr = _BODY.resolve(body)
    if berr:
        return error(berr)
    axis_ent, aerr = _AXIS.resolve(axis)
    if aerr:
        return error(aerr)
    end_ent, eerr = _END.resolve(end_datum)
    if eerr:
        return error(eerr)

    # axis entity -> an InfiniteLine3D (the _holder routine validates the kind: cyl/cone face or
    # straight edge). A handle that resolves to something else returns None -> a precise error.
    axis_line = safe(lambda: _holder.get_axis(axis_ent))
    if axis_line is None:
        return error("'axis' must be a CYLINDRICAL or CONICAL face, or a straight EDGE - that handle "
                     "doesn't define an axis of rotation. Use find_geometry(kind=cylinder_face / "
                     "line_edge) on the holder.")

    # end datum -> the point where it meets the axis (must be normal/perpendicular to the axis).
    plane_intersect = safe(lambda: _holder.is_valid_axial_datum(end_ent, axis_line))
    if plane_intersect is None:
        return error("'end_datum' must be a PLANAR face (or edge/vertex) NORMAL to the axis - that "
                     "handle isn't a valid end datum for this axis. Pick the flat end face of the holder.")

    try:
        profile = _holder.get_tool_profile(body_ent, axis_line, plane_intersect)
    except Exception as e:
        return error(f"Could not reduce the body to a holder profile: {e}. (The body should be a "
                     "solid of revolution about the chosen axis.)")
    if not profile:
        return error("No holder profile could be derived - no coaxial faces reduced to segments. "
                     "Check that 'axis' is the true axis of revolution and the body is a turned holder.")

    holder_name = (name or "").strip() or (safe(lambda: app.activeDocument.name) or "Holder")
    holder_json = _holder.build_holder_data(profile, holder_name, product_id, product_link, vendor)
    segments = holder_json["segments"]

    return ok({
        "computed": True,
        "name": holder_name,
        "segment_count": len(segments),
        # the human-legible profile (mm) - what the holder will look like as a stack of bands
        "segments_mm": segments,
        # the full library JSON (type='holder'); add it to a tool library yourself (see note)
        "holder_json": holder_json,
        "note": "Holder profile computed (segments in mm: height, lower/upper diameter). This does "
        "NOT write to a tool library - take 'holder_json' and add it to a library (a dedicated "
        "library tool family is coming; a holder in a document is a FORK of library data, not a link). "
        "Pair with view_screenshot to confirm the body is the holder you meant.",
    })


TOOL_DESCRIPTION = (
    "Turn a solid HOLDER model into a CAM tool-holder profile - the headless form of the Add Tool "
    "Holder command. Pass three find_geometry handles: 'body' (the solid), 'axis' (a cylindrical/"
    "conical face or straight edge = the axis of rotation), and 'end_datum' (a planar face/edge/vertex "
    "NORMAL to the axis, fixing z=0). Returns the holder as (height, lower/upper-diameter) segments in "
    "mm plus the full library JSON in 'holder_json'. Optional name/product_id/product_link/vendor stamp "
    "the JSON. READ-ONLY: it computes + returns; it does NOT add the holder to a tool library (do that "
    "yourself with the JSON - a holder in a document is a fork of library data, not a live link)."
)

tool = (
    Tool.create_simple(name="model_compute_holder", description=TOOL_DESCRIPTION)
    .add_input_property(*_BODY.as_property())
    .add_input_property(*_AXIS.as_property())
    .add_input_property(*_END.as_property())
    .add_input_property("name", {"type": "string",
            "description": "Holder name for the JSON (default: the active document name)."})
    .add_input_property("product_id", {"type": "string", "description": "Optional product ID metadata."})
    .add_input_property("product_link", {"type": "string", "description": "Optional product page URL metadata."})
    .add_input_property("vendor", {"type": "string", "description": "Optional vendor metadata."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
