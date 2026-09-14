# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: CAM hole recognition over solid bodies - GROUPS of similar holes, each
hole's segments, and the face handles cam_select_geometry(selection='holes') consumes."""

import math

import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import CM_TO_UNIT, counted, error, measured, ok, read_flag, safe
from ._cam_common import _face_handle, clamp_rows, solid_census
from . import _common
from . import _geom
from . import _inputs
from . import _outputs

RETURNS = [
    _outputs.ReturnsHandle("faces", require="face", in_list=True,
                           consumers=["cam_select_geometry"]),
]

_HOLES_PER_GROUP_DEFAULT = 50   # hole rows per group when the caller names no cap
_HOLES_PER_GROUP_MAX = 200      # hard ceiling on those rows; each crosses the wire whole
_DIAMETER_TOL = 1e-6            # the band a diameter filter compares within, in the caller's units

# friendly key -> the adsk.cam.HoleSegmentType member named 'HoleSegmentType<Key>', resolved by
# NAME at runtime.
_SEGMENT_KINDS = ("cylinder", "cone", "flat", "torus")
_UNREAD = "unread"

_DIAMETER_DESC = "The GROUP's top diameter, in 'units'."
BODIES = _inputs.BodyRefList("bodies", kind="solid")
MIN_DIAMETER = _inputs.Distance("min_diameter", allow_negative=False, description=_DIAMETER_DESC)
MAX_DIAMETER = _inputs.Distance("max_diameter", allow_negative=False, description=_DIAMETER_DESC)
_SPEC = [BODIES, MIN_DIAMETER, MAX_DIAMETER, _inputs.UNITS]


def _segment_kind(seg):
    """The friendly name of one segment's holeSegmentType, or 'unread' when nothing resolved it."""
    raw = safe(lambda: seg.holeSegmentType)
    family = getattr(adsk.cam, "HoleSegmentType", None)
    if raw is None or family is None:
        return _UNREAD
    for key in _SEGMENT_KINDS:
        member = getattr(family, "HoleSegmentType" + key.capitalize(), None)
        if member is not None and raw == member:
            return key
    return _UNREAD


def _segment_record(seg, inv_k):
    """One segment's shape: what it is, the two diameters, its height and its half angle."""
    return {"type": _segment_kind(seg),
            "top_diameter": measured(lambda: seg.topDiameter, inv_k, 4),
            "bottom_diameter": measured(lambda: seg.bottomDiameter, inv_k, 4),
            "height": measured(lambda: seg.height, inv_k, 4),
            # halfAngle is radians; every other length here is cm.
            "half_angle_deg": measured(lambda: math.degrees(seg.halfAngle), 1.0, 3)}


def _hole_segments(hole):
    """Every segment of one hole in segment(i) order, or None when segmentCount did not read."""
    n = counted(lambda: hole.segmentCount)
    if n is None:
        return None
    return [s for s in (safe(lambda i=i: hole.segment(i)) for i in range(n)) if s is not None]


def _segment_faces(seg):
    """The handles for one segment's faces - seg.faces is a vector, walked by iteration."""
    faces = safe(lambda: list(seg.faces)) or []
    return [h for h in (_face_handle(f) for f in faces) if h]


def _hole_row(hole, inv_k):
    """One hole: where it sits, its faces per segment, and the flags the API reports on it."""
    segs = _hole_segments(hole)
    return {"top": _common.ptxyz(safe(lambda: hole.top), inv_k),
            "bottom": _common.ptxyz(safe(lambda: hole.bottom), inv_k),
            "axis": _geom.unit_vector(safe(lambda: hole.axis)),
            # One inner list PER SEGMENT, in the group's 'segments' order: a segment can own more
            # than one face (a through hole a notch severs), so a flat list maps to nothing.
            "faces": None if segs is None else [_segment_faces(s) for s in segs],
            "has_errors": read_flag(lambda: hole.hasErrors),
            "has_warnings": read_flag(lambda: hole.hasWarnings)}


def _lead_facts(lead, inv_k):
    """The geometry a group's holes share, read off the one hole standing for them."""
    if lead is None:
        return {"top_diameter": None, "bottom_diameter": None, "total_length": None,
                "is_through": None, "is_threaded": None}
    return {"top_diameter": measured(lambda: lead.topDiameter, inv_k, 4),
            "bottom_diameter": measured(lambda: lead.bottomDiameter, inv_k, 4),
            "total_length": measured(lambda: lead.totalLength, inv_k, 4),
            # isThrough is the API's own flag: a bottomDiameter of 0 is a drill point, not a floor.
            "is_through": read_flag(lambda: lead.isThrough),
            "is_threaded": read_flag(lambda: lead.isThreaded)}


def _group_row(group, index, inv_k, cap):
    """One group: the shape its holes share, then a row per hole up to `cap`."""
    holes = list(_common.iter_collection(group))
    lead = holes[0] if holes else None
    segs = _hole_segments(lead) if lead is not None else None
    row = {"index": index, "hole_count": len(holes)}
    row.update(_lead_facts(lead, inv_k))
    row["segments"] = None if segs is None else [_segment_record(s, inv_k) for s in segs]
    row["holes"] = [_hole_row(h, inv_k) for h in holes[:cap]]
    if len(holes) > cap:
        row["truncated"] = True
    return row


def _out_of_range(top_diameter, min_d, max_d):
    """Whether a group's top diameter sits outside the requested window - a diameter that did not
    read is never excluded, since nothing measured it."""
    if top_diameter is None:
        return False
    return ((min_d is not None and top_diameter < min_d - _DIAMETER_TOL)
            or (max_d is not None and top_diameter > max_d + _DIAMETER_TOL))


def _recognize_groups(bodies, include_partial):
    """(the RecognizedHoleGroups the recognizer answers for `bodies`, error) - the one adsk.cam
    entry point, so what it returns is read in one place."""
    try:
        inp = adsk.cam.RecognizedHolesInput.create()
    except Exception as e:
        return None, f"adsk.cam.RecognizedHolesInput.create() raised: {e}"
    if inp is None:
        return None, "RecognizedHolesInput.create() returned nothing, so no recognition ran."
    inp.filterPartialHoles = not include_partial
    try:
        groups = adsk.cam.RecognizedHoleGroup.recognizeHoleGroupsWithInput(list(bodies), inp)
    except Exception as e:
        return None, f"Hole recognition raised on {len(bodies)} body/bodies: {e}"
    if groups is None:
        return None, f"Hole recognition returned nothing for {len(bodies)} body/bodies."
    return groups, None


def handler(bodies=None, include_partial: bool = False, min_diameter: float = None,
            max_diameter: float = None, units: str = "mm",
            max_results: int = _HOLES_PER_GROUP_DEFAULT) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design (open or create a document first).")
    values, verr = _inputs.resolve_inputs(
        _SPEC, {"bodies": bodies, "min_diameter": min_diameter,
                "max_diameter": max_diameter, "units": units})
    if verr:
        return verr
    units_key = (values["units"] or "mm").strip().lower()
    inv_k = CM_TO_UNIT[units_key]
    # Both bounds come back in cm (the Distance kind scales them); every diameter below is
    # published in the caller's units, so the window is converted once to match.
    min_d = None if values["min_diameter"] is None else values["min_diameter"] * inv_k
    max_d = None if values["max_diameter"] is None else values["max_diameter"] * inv_k

    solids, skipped = solid_census(design, values["bodies"])
    if not solids:
        return error(f"No solid body to recognize holes on: {skipped['surface_bodies_skipped']} "
                     f"surface, {skipped['mesh_bodies_skipped']} mesh and "
                     f"{skipped['unreadable_bodies_skipped']} unreadable body/bodies were skipped. "
                     "Name a solid body in 'bodies' (find_geometry / design_get(include=['tree'])).")
    groups, rerr = _recognize_groups(solids, include_partial)
    if rerr:
        return error(rerr)

    cap = clamp_rows(max_results, _HOLES_PER_GROUP_DEFAULT, _HOLES_PER_GROUP_MAX)
    rows, out_of_range = [], 0
    for i, item in enumerate(_common.iter_collection(groups)):
        row = _group_row(item, i, inv_k, cap)
        if _out_of_range(row["top_diameter"], min_d, max_d):
            out_of_range += 1
            continue
        rows.append(row)
    payload = {"group_count": len(rows), "hole_count": sum(r["hole_count"] for r in rows),
               "groups": rows, "bodies_scanned": len(solids), "units": units_key,
               "surface_bodies_skipped": skipped["surface_bodies_skipped"],
               "mesh_bodies_skipped": skipped["mesh_bodies_skipped"],
               "note": "Omitted 'bodies' scans every solid body. Each hole's 'faces' lists handles "
                       "PER SEGMENT, in the group's 'segments' "
                       "order - pass handles to cam_select_geometry(selection='holes', "
                       "handles=[...]), which takes a cylinder wall and refuses a flat floor. A "
                       "group's diameters, total_length and flags are read off the first of its "
                       "holes; model_hole is the design-side tool that makes a hole."}
    if skipped["unreadable_bodies_skipped"]:
        payload["unreadable_bodies_skipped"] = skipped["unreadable_bodies_skipped"]
    if min_d is not None or max_d is not None:
        payload["groups_out_of_range"] = out_of_range
    return ok(payload)


TOOL_DESCRIPTION = (
    "Recognize solid bodies' holes, GROUPED by similar geometry, for "
    "cam_select_geometry(selection='holes').\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="cam_find_holes", description=TOOL_DESCRIPTION)
    .add_input_property(*BODIES.as_property())
    .add_input_property("include_partial", {"type": "boolean",
            "description": "Include partly enclosed holes."})
    .add_input_property(*MIN_DIAMETER.as_property(brief=True))
    .add_input_property(*MAX_DIAMETER.as_property(brief=True))
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("max_results", {"type": "integer",
            "description": f"Hole rows per group, max {_HOLES_PER_GROUP_MAX}."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
