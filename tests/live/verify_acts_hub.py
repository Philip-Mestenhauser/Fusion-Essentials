# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the CAM competence hub - a turned flange drawn by constraint, and the job it carries.

The world the CAM competence beats address, built through the tools instead of kept as a saved
document. Its three dimensioned sketches read is_fully_constrained true before a feature consumes
them, which is the sketch recipes' own bar; the two setups at the bottom are the names a machining
beat addresses. The hub is authored a metre out along X on the XZ plane, and a chunk holding an XZ
sketch is left exactly where it was written - see verify_layout._place_slots.
"""

from verify_acts_cam import _TURN_MACHINE, _types_offered
from verify_core import (
    _RECALL, _ctx_get, _datum, _drilled, _extruded, _face_up_at, _fg, _filleted, _made_component,
    _matched, _measured, _near, _num, _prof, _recall, _revolved, _watch)

# THE NAMES THE HUB BUILDS UNDER, in one place for the beats that address it.
HUB_COMP = "Hub"                 # the turned flange - the model both setups machine
HUB_PROFILE = "HubProfile"       # the revolved outline, on the axis plane
HUB_HOLES = "HubHoles"           # the bolt-circle reference sketch on the flange top
HUB_KEYWAY = "HubKeyway"         # the keyway cut into the shaft
HUB_WEDGE = "HubWedge"           # the inclined flat, on the 30 deg datum
HUB_TILT = "TiltPlane"
HUB_FLANGE_TOP = "HubFlangeTop"  # the pocket outline plus its drawn label
HUB_FLANGE_POCKET = "HubFlangePocket"
HUB_MILL_SETUP = "MillTop"       # the milling job, on the flange top
HUB_TURN_SETUP = "HubTurn"       # the turned job, on the mill-turn machine

# The hub's axis in world X. The field the layout pass packs ends at x 1100, so a chunk pinned out
# here shares ground with nothing - which is what a chunk holding an XZ sketch needs, since it
# cannot be carried in Y at all.
_HUB_X = 1200.0

# The profile, as the ledger measured it: a flange, a shaft with a groove, a stub with a chamfer.
# Radii across, depths down the shaft - the sketch's +Y is world -Z, so a depth here is a NEGATIVE
# world z (the flange top sits at z 0 and the part ends at z -90).
_FLANGE_R, _FLANGE_T = 40.0, 20.0
_SHAFT_R = 25.0
_GROOVE_R, _GROOVE_TOP, _GROOVE_BOT = 22.0, 45.0, 50.0
_SHAFT_END = 70.0
_STUB_R, _STUB_END = 15.0, 88.0
_CHAMFER_R, _PART_END = 13.0, 90.0
_AXIS_LEN = 100.0
_BOLT_R = 32.0                   # the bolt circle the four counterbored holes sit on
_SECTOR_HALF = 6.0               # the flange pocket's half width, on that same circle

# The closed outline, walked from the axis at the flange top round to the axis at the part end.
_HUB_POINTS = [[_HUB_X, 0.0], [_HUB_X + _FLANGE_R, 0.0], [_HUB_X + _FLANGE_R, _FLANGE_T],
               [_HUB_X + _SHAFT_R, _FLANGE_T], [_HUB_X + _SHAFT_R, _GROOVE_TOP],
               [_HUB_X + _GROOVE_R, _GROOVE_TOP], [_HUB_X + _GROOVE_R, _GROOVE_BOT],
               [_HUB_X + _SHAFT_R, _GROOVE_BOT], [_HUB_X + _SHAFT_R, _SHAFT_END],
               [_HUB_X + _STUB_R, _SHAFT_END], [_HUB_X + _STUB_R, _STUB_END],
               [_HUB_X + _CHAMFER_R, _PART_END], [_HUB_X, _PART_END]]

# Which of the outline's thirteen lines run across and which run down. line:0 is the construction
# axis, so the outline starts at line:1 and closes on line:13.
_HUB_ACROSS = (1, 3, 5, 7, 9, 12)
_HUB_DOWN = (2, 4, 6, 8, 10, 13)
# The radii and the depths, each measured from line:1:start - the vertex on the axis at the flange
# top, which every other vertex is placed against.
_HUB_RADII = (("line:1:end", _FLANGE_R), ("line:4:start", _SHAFT_R), ("line:6:start", _GROOVE_R),
              ("line:10:start", _STUB_R), ("line:11:end", _CHAMFER_R))
_HUB_DEPTHS = (("line:2:end", _FLANGE_T), ("line:4:end", _GROOVE_TOP),
               ("line:6:end", _GROOVE_BOT), ("line:8:end", _SHAFT_END),
               ("line:10:end", _STUB_END), ("line:12:start", _PART_END))

# The keyway rectangle, on the same axis plane: it starts 3 mm inside the shaft wall and runs 16 mm
# down, cut 3 mm either side of that plane - so the slot lands 6 mm wide and 3 mm deep.
_KEY_R, _KEY_TOP, _KEY_WIDE, _KEY_LONG, _KEY_HALF = 22.0, 52.0, 6.0, 16.0, 3.0

# The inclined flat's triangle, in the tilted sketch's own coordinates: +X there is world Z, so the
# first number is a depth down the shaft, and the second is measured out from the hub axis, whose
# place in that sketch is read off its frame (see _tilt_v).
_WEDGE_NEAR, _WEDGE_FAR = 10.0, 25.0
_WEDGE_CUT = 12.0

# The shop set the hub's job is cut with, in the order the adds land - the order a create row picks
# a cutter by. (from_type, diameter in mm, or None to keep the sample's own).
_HUB_TOOLS = (("face mill", 50.0), ("flat end mill", 10.0), ("ball end mill", 6.0),
              ("chamfer mill", 10.0), ("thread mill", 12.0), ("slot mill", 10.0),
              ("turning general", None), ("turning grooving", None), ("turning threading", None))


def _fully_constrained(name, constraints, dimensions):
    """sketch_get: the sketch closed every degree of freedom, and the counts it took to do it.
    Constraints outnumbering dimensions is the recipe's own bar; a count that drifts says a row
    landed on geometry this act did not mean."""
    def check(p):
        return _measured(f"'{name}' fully constrained by {constraints} constraints and "
                         f"{dimensions} dimensions",
                         {"is_fully_constrained": p.get("is_fully_constrained"),
                          "constraint_count": p.get("constraint_count"),
                          "dimension_count": p.get("dimension_count"),
                          "profile_count": p.get("profile_count")},
                         p.get("is_fully_constrained") is True
                         and p.get("constraint_count") == constraints
                         and p.get("dimension_count") == dimensions)
    return check


def _profile_frame(name):
    """sketch_create on XZ: the name it landed under, and the frame's own +Y - which runs along
    world -Z, so every depth below is authored positive and the part hangs under the flange top."""
    def check(p):
        y = (p.get("frame") or {}).get("y_world")
        return _measured(f"'{name}' on a frame whose +Y runs along world -Z",
                         {"sketch_name": p.get("sketch_name"), "plane": p.get("plane"),
                          "y_world": y},
                         p.get("sketch_name") == name and isinstance(y, list) and len(y) == 3
                         and _near(y[2], -1.0, 1e-3))
    return check


def _drew(count):
    """sketch_add_geometry: the collection's own delta - what actually landed, not what was asked
    for (a closed_path repeats its first point, so thirteen vertices draw thirteen segments)."""
    def check(p):
        return _measured(f"{count} curve(s) drawn",
                         {"curves_added": p.get("curves_added"), "drawn": p.get("drawn")},
                         p.get("curves_added") == count)
    return check


def _rim_edge(x, y, z, radius, tol=0.1):
    """find_geometry(kind='circular_edge'): the ONE edge found, told apart by its own centre. Two
    edges of this radius bound the flange and 'nearest_to' only ORDERS them, so the centre is what
    says the top one answered - the bottom rim would round the wrong corner."""
    def check(p):
        ms = p.get("matches") or []
        m = ms[0] if ms else {}
        pos = m.get("position")
        return _measured(f"one r{radius} circular edge centred at {[x, y, z]}",
                         {"count": len(ms), "position": pos, "radius": m.get("radius"),
                          "kind": m.get("kind")},
                         len(ms) == 1 and m.get("kind") == "circular_edge"
                         and _near(m.get("radius"), radius, tol)
                         and isinstance(pos, list) and len(pos) == 3
                         and all(_near(v, w, tol) for v, w in zip(pos, (x, y, z))))
    return check


def _tilt_frame(p):
    """sketch_create on the 30 deg datum: the frame it landed with, judged on the three readings the
    wedge's own coordinates stand on. Its +X runs along world Z while the origin and +Y contribute
    NOTHING to Z, so a wedge point's first coordinate IS its world z - which is why those are
    written as depths; and the normal is the XZ plane's own, swung 30 degrees about the stub axis."""
    f = p.get("frame") or {}
    o, x, y, n = f.get("origin_mm"), f.get("x_world"), f.get("y_world"), f.get("normal")
    triple = [v for v in (o, x, y, n) if isinstance(v, list) and len(v) == 3]
    return _measured("the tilt sketch maps its +X onto world Z alone, normal swung 30 deg off XZ",
                     {"origin_mm": o, "x_world": x, "y_world": y, "normal": n},
                     len(triple) == 4 and _near(x[2], 1.0, 1e-3)
                     and _near(o[2], 0.0, 1e-3) and _near(y[2], 0.0, 1e-3)
                     and _near(n[0], -0.5, 1e-3) and _near(n[1], 0.866025, 1e-3))


def _tilt_v(p):
    """Where the hub's axis sits along the tilted sketch's own +Y, computed from the frame that
    sketch landed with. The datum's parametric origin is a long way from the part, so the wedge's
    coordinates are measured from here rather than written as literals."""
    f = p["frame"]
    o, y = f["origin_mm"], f["y_world"]
    return sum((a - b) * c for a, b, c in zip((_HUB_X, 0.0, 0.0), o, y))


def _hub_box(p):
    """model_inspect on the finished hub: the flange's own diameter across both axes and the whole
    part's length down Z - the one read that says the revolve, the cuts and the chamfer all landed
    where the profile put them."""
    return _measured(f"hub bbox {2 * _FLANGE_R} x {2 * _FLANGE_R} x {_PART_END} mm",
                     {"x": p.get("x"), "y": p.get("y"), "z": p.get("z"),
                      "center": p.get("center"), "units": p.get("units")},
                     _near(p.get("x"), 2 * _FLANGE_R, 0.5) and _near(p.get("y"), 2 * _FLANGE_R, 0.5)
                     and _near(p.get("z"), _PART_END, 0.5))


def _threaded(designation, length):
    """model_thread: the call-out and the extent read back off the feature, with 'internal' derived
    from the face's own out-of-material normal rather than echoed - a stub reads external."""
    def check(p):
        return _measured(f"a {designation} external thread {length} mm long",
                         {"designation": p.get("designation"), "length": p.get("length"),
                          "internal": p.get("internal"), "location": p.get("location"),
                          "thread_type": p.get("thread_type")},
                         p.get("designation") == designation and p.get("length") == length
                         and p.get("internal") is False and bool(p.get("thread_type")))
    return check


def _labelled(text, height):
    """sketch_set_text(create=True): the count re-read off sketchTexts, and the width the created
    text's own boundingBox measured - which is what says a string landed in the sketch rather than
    a call returning ok. Nothing cuts this text; it is drawn on the flange-top outline sketch."""
    def check(p):
        return _measured(f"'{text}' drawn {height} mm high in the flange-top sketch",
                         {"created": p.get("created"), "text": p.get("text"),
                          "sketch_text_count": p.get("sketch_text_count"),
                          "measured_width": p.get("measured_width")},
                         p.get("created") is True and p.get("text") == text
                         and p.get("sketch_text_count") == 1
                         and _num(p.get("measured_width")) and p["measured_width"] > 0)
    return check


def _pocket_cut(p):
    """model_extrude(profile_index='all') on the flange top: BOTH regions cut, and neither one
    inside the other - an 'enclosed_profile_indices' key would mean the square landed in the
    sector's own band, where its cut leaves nothing to see."""
    return _measured("the sector and the square cut as two separate regions",
                     {"profiles_extruded": p.get("profiles_extruded"),
                      "profile_index": p.get("profile_index"),
                      "enclosed_profile_indices": p.get("enclosed_profile_indices"),
                      "result_bodies": p.get("result_bodies")},
                     p.get("profiles_extruded") == 2 and "enclosed_profile_indices" not in p
                     and bool(p.get("result_bodies")))


def _hub_tools_landed(p):
    """cam_edit_tools(action='list'): the cutters this act added, read back off the library in the
    order they were handed in - the order is the contract a create row selects a cutter by, so it
    is asserted as the slice starting where the library's own count said the first one landed."""
    base = _RECALL.get("hub_tool_base")
    want = list(_HUB_TOOLS)
    rows = p.get("tools") or []
    got = [(r.get("type"), r.get("diameter_mm"))
           for r in rows[base:base + len(want)]] if base is not None else []
    ok_ = len(got) == len(want) and all(
        g[0] == w[0] and (w[1] is None or _near(g[1], w[1], 0.01)) for g, w in zip(got, want))
    return _measured(f"{len(want)} hub cutters at index {base}, in the order they were added",
                     {"tool_count": p.get("tool_count"), "base": base, "landed": got}, ok_)


def _hub_setups(p):
    """cam_get's setups slice: the hub's two setups, read off the Setups themselves. The milling one
    carries no machine (its blocker names that) on the stock-point WCS a top job is set from; the
    turning one carries the mill-turn machine on the turning WCS, which is the pair the competence
    beats address."""
    rows = {s.get("name"): s for s in (p.get("setups") or [])}
    mill, turn = rows.get(HUB_MILL_SETUP) or {}, rows.get(HUB_TURN_SETUP) or {}
    mwcs, twcs = mill.get("wcs") or {}, turn.get("wcs") or {}
    return _measured(f"'{HUB_MILL_SETUP}' milling and '{HUB_TURN_SETUP}' turning, both on the hub",
                     {"mill": mill, "turn": turn},
                     mill.get("operation_type") == "MillingOperation"
                     and mill.get("machine") is None
                     and mill.get("blocked_by") == ["no_machine_selected"]
                     and mwcs.get("origin_mode") == "stockPoint"
                     and mwcs.get("orientation_mode") == "modelOrientation"
                     and turn.get("operation_type") == "TurningOperation"
                     and turn.get("machine") == _TURN_MACHINE
                     and twcs.get("origin_mode") == "turningOrigin"
                     and twcs.get("orientation_mode") == "axesXZ"
                     and mill.get("selected_models") == [HUB_COMP + ":1"]
                     and turn.get("selected_models") == [HUB_COMP + ":1"])


def _setup_created(name, operation_type):
    """cam_create_setup: the name read back off the created Setup, its operation type, and a count
    of zero operations - a setup that arrives holding some is not the one this row made."""
    def check(p):
        return _measured(f"'{name}' created as a {operation_type} setup on the hub",
                         {"created": p.get("created"), "setup_name": p.get("setup_name"),
                          "operation_type": p.get("operation_type"), "models": p.get("models"),
                          "operation_count": p.get("operation_count")},
                         p.get("created") is True and p.get("setup_name") == name
                         and p.get("operation_type") == operation_type
                         and p.get("models") == [HUB_COMP + ":1"]
                         and p.get("operation_count") == 0)
    return check


def _one_line(sketch, constraint, lines):
    """One single-entity geometric-constraint row per line index."""
    return [("sketch_constrain", {"constraint": constraint, "sketch_name": sketch,
                                  "entity_one": f"line:{i}"}, "ok", None) for i in lines]


def _spans(sketch, dim_type, anchor, rows):
    """One dimension row per (entity ref, value), each measured from the same anchor point."""
    return [("sketch_dimension", {"dim_type": dim_type, "sketch_name": sketch,
                                  "entity_one": anchor, "entity_two": ref,
                                  "value": f"{value:g} mm"}, "ok", None) for ref, value in rows]


# ACT 8b: THE HUB - the competence world, drawn through the sketch recipes and turned solid.
_HUB = (
    [
        ("model_create_component", {"name": HUB_COMP, "activate": True}, _made_component, None),
        # THE TURNED OUTLINE, on the plane the part is symmetric about. The construction line is
        # the axis the revolve turns about; the outline closes on it, and the recipe's own shape
        # follows - constraints carry the rectilinear intent, dimensions carry the sizes.
        ("sketch_create", {"plane": "xz", "name": HUB_PROFILE}, _profile_frame(HUB_PROFILE), None),
        ("sketch_add_geometry", {"kind": "line", "x1": _HUB_X, "y1": 0.0, "x2": _HUB_X,
                                 "y2": _AXIS_LEN, "sketch_name": HUB_PROFILE,
                                 "is_construction": True}, _drew(1), None),
        ("sketch_add_geometry", {"kind": "closed_path", "points": _HUB_POINTS,
                                 "sketch_name": HUB_PROFILE}, _drew(13), None),
        # a closed_path REPEATS its first point rather than constraining the two, so the last
        # vertex is a free point until this coincident lands it on the first.
        ("sketch_constrain", {"constraint": "coincident", "sketch_name": HUB_PROFILE,
                              "entity_one": "line:13:end", "entity_two": "line:1:start"},
         "ok", None),
    ]
    + _one_line(HUB_PROFILE, "horizontal", _HUB_ACROSS)
    + _one_line(HUB_PROFILE, "vertical", _HUB_DOWN)
    + [
        # the shaft is ONE diameter above and below the groove, said as a relation rather than as
        # the same number typed twice.
        ("sketch_constrain", {"constraint": "collinear", "sketch_name": HUB_PROFILE,
                              "entity_one": "line:4", "entity_two": "line:8"}, "ok", None),
        # the flange top sits ON the sketch's own X axis: a relation to the origin, not a zero
        # dimension, which is what puts the part's datum face at world z 0.
        ("sketch_constrain", {"constraint": "horizontal_points", "sketch_name": HUB_PROFILE,
                              "entity_one": "point:0", "entity_two": "line:1:start"}, "ok", None),
        ("sketch_constrain", {"constraint": "coincident", "sketch_name": HUB_PROFILE,
                              "entity_one": "line:0:start", "entity_two": "line:1:start"},
         "ok", None),
        ("sketch_constrain", {"constraint": "vertical", "sketch_name": HUB_PROFILE,
                              "entity_one": "line:0"}, "ok", None),
        # the one typed coordinate the sketch carries: where the hub's axis stands in the field.
        ("sketch_dimension", {"dim_type": "horizontal_distance", "sketch_name": HUB_PROFILE,
                              "entity_one": "point:0", "entity_two": "line:1:start",
                              "value": f"{_HUB_X:g} mm"}, "ok", None),
    ]
    + _spans(HUB_PROFILE, "horizontal_distance", "line:1:start", _HUB_RADII)
    + _spans(HUB_PROFILE, "vertical_distance", "line:1:start", _HUB_DEPTHS)
    + [
        ("sketch_dimension", {"dim_type": "vertical_distance", "sketch_name": HUB_PROFILE,
                              "entity_one": "line:0:start", "entity_two": "line:0:end",
                              "value": f"{_AXIS_LEN:g} mm"}, "ok", None),
        # THE PROOF, before anything consumes the profile: every freedom closed, and the region the
        # revolve turns handed on as a handle rather than as a guessed index.
        ("sketch_get", {"sketch_name": HUB_PROFILE}, _fully_constrained(HUB_PROFILE, 17, 13),
         _prof("hub_profile")),
        ("model_revolve", lambda c: {"sketch_name": HUB_PROFILE,
                                     "profile_index": _ctx_get(c, "hub_profile",
                                                               "the hub's outline"),
                                     "axis": "line:0", "angle_deg": 360}, _revolved, None),
        # the framing pass has been watching a flat sketch from the front; from here the subject is
        # a solid, and every cut below lands inside this one frame.
        _watch(HUB_COMP + ":1"),
        # THE BOLT CIRCLE, on the flange top plane. The annulus and the four positions are drawn as
        # the job's own reference: the drill below is aimed at the same coordinates, and this sketch
        # is what says they are a symmetric pattern rather than four typed points.
        ("sketch_create", {"plane": "xy", "name": HUB_HOLES}, "ok", None),
        ("sketch_add_geometry", {"kind": "point", "cx": _HUB_X + _BOLT_R, "cy": 0.0,
                                 "sketch_name": HUB_HOLES}, _drew(1), None),
        ("sketch_add_geometry", {"kind": "point", "cx": _HUB_X - _BOLT_R, "cy": 0.0,
                                 "sketch_name": HUB_HOLES}, _drew(1), None),
        ("sketch_add_geometry", {"kind": "point", "cx": _HUB_X, "cy": _BOLT_R,
                                 "sketch_name": HUB_HOLES}, _drew(1), None),
        ("sketch_add_geometry", {"kind": "point", "cx": _HUB_X, "cy": -_BOLT_R,
                                 "sketch_name": HUB_HOLES}, _drew(1), None),
        ("sketch_add_geometry", {"kind": "line", "x1": _HUB_X - _FLANGE_R, "y1": 0.0,
                                 "x2": _HUB_X + _FLANGE_R, "y2": 0.0, "sketch_name": HUB_HOLES,
                                 "is_construction": True}, _drew(1), None),
        ("sketch_add_geometry", {"kind": "line", "x1": _HUB_X, "y1": -_FLANGE_R, "x2": _HUB_X,
                                 "y2": _FLANGE_R, "sketch_name": HUB_HOLES,
                                 "is_construction": True}, _drew(1), None),
        ("sketch_add_geometry", {"kind": "circle", "cx": _HUB_X, "cy": 0.0, "radius": _FLANGE_R,
                                 "sketch_name": HUB_HOLES, "is_construction": True},
         _drew(1), None),
        ("sketch_add_geometry", {"kind": "circle", "cx": _HUB_X, "cy": 0.0, "radius": _SHAFT_R,
                                 "sketch_name": HUB_HOLES, "is_construction": True},
         _drew(1), None),
        ("sketch_constrain", {"constraint": "horizontal", "sketch_name": HUB_HOLES,
                              "entity_one": "line:0"}, "ok", None),
        ("sketch_constrain", {"constraint": "vertical", "sketch_name": HUB_HOLES,
                              "entity_one": "line:1"}, "ok", None),
        ("sketch_constrain", {"constraint": "midpoint", "sketch_name": HUB_HOLES,
                              "entity_one": "circle:0:center", "entity_two": "line:0"},
         "ok", None),
        ("sketch_constrain", {"constraint": "midpoint", "sketch_name": HUB_HOLES,
                              "entity_one": "circle:0:center", "entity_two": "line:1"},
         "ok", None),
        ("sketch_constrain", {"constraint": "equal", "sketch_name": HUB_HOLES,
                              "entity_one": "line:0", "entity_two": "line:1"}, "ok", None),
        ("sketch_constrain", {"constraint": "concentric", "sketch_name": HUB_HOLES,
                              "entity_one": "circle:1", "entity_two": "circle:0"}, "ok", None),
        ("sketch_constrain", {"constraint": "horizontal_points", "sketch_name": HUB_HOLES,
                              "entity_one": "point:0", "entity_two": "circle:0:center"},
         "ok", None),
        ("sketch_constrain", {"constraint": "coincident", "sketch_name": HUB_HOLES,
                              "entity_one": "point:1", "entity_two": "line:0"}, "ok", None),
        ("sketch_constrain", {"constraint": "coincident", "sketch_name": HUB_HOLES,
                              "entity_one": "point:2", "entity_two": "line:0"}, "ok", None),
        ("sketch_constrain", {"constraint": "coincident", "sketch_name": HUB_HOLES,
                              "entity_one": "point:3", "entity_two": "line:1"}, "ok", None),
        ("sketch_constrain", {"constraint": "coincident", "sketch_name": HUB_HOLES,
                              "entity_one": "point:4", "entity_two": "line:1"}, "ok", None),
        # each opposite pair mirrors about the other axis, so ONE distance drives two holes.
        ("sketch_constrain", {"constraint": "symmetry", "sketch_name": HUB_HOLES,
                              "entity_one": "point:1", "entity_two": "point:2",
                              "symmetry_line": "line:1"}, "ok", None),
        ("sketch_constrain", {"constraint": "symmetry", "sketch_name": HUB_HOLES,
                              "entity_one": "point:3", "entity_two": "point:4",
                              "symmetry_line": "line:0"}, "ok", None),
        ("sketch_dimension", {"dim_type": "horizontal_distance", "sketch_name": HUB_HOLES,
                              "entity_one": "point:0", "entity_two": "circle:0:center",
                              "value": f"{_HUB_X:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "horizontal_distance", "sketch_name": HUB_HOLES,
                              "entity_one": "line:0:start", "entity_two": "line:0:end",
                              "value": f"{2 * _FLANGE_R:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "diameter", "sketch_name": HUB_HOLES,
                              "entity_one": "circle:0",
                              "value": f"{2 * _FLANGE_R:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "diameter", "sketch_name": HUB_HOLES,
                              "entity_one": "circle:1",
                              "value": f"{2 * _SHAFT_R:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "horizontal_distance", "sketch_name": HUB_HOLES,
                              "entity_one": "circle:0:center", "entity_two": "point:1",
                              "value": f"{_BOLT_R:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "vertical_distance", "sketch_name": HUB_HOLES,
                              "entity_one": "circle:0:center", "entity_two": "point:3",
                              "value": f"{_BOLT_R:g} mm"}, "ok", None),
        ("sketch_get", {"sketch_name": HUB_HOLES}, _fully_constrained(HUB_HOLES, 13, 6), None),
        # the flange top, MEASURED before it is drilled: one planar face at the axis facing +Z,
        # which is what separates it from the flange's underside at the same centroid in x and y.
        ("find_geometry", {"target": HUB_COMP, "kind": "planar_face",
                           "nearest_to": [_HUB_X, 0, 0], "max_results": 1},
         _face_up_at(_HUB_X, 0, 0), _fg("hub_top")),
        ("model_hole", lambda c: {"hole_type": "counterbore",
                                  "face": _ctx_get(c, "hub_top", "the flange top"),
                                  "points": [[_HUB_X + _BOLT_R, 0, 0], [_HUB_X - _BOLT_R, 0, 0],
                                             [_HUB_X, _BOLT_R, 0], [_HUB_X, -_BOLT_R, 0]],
                                  "points_space": "world", "diameter": "6.6 mm",
                                  "cbore_diameter": "11 mm", "cbore_depth": "6.5 mm",
                                  "extent": "through", "tip_angle": "118 deg"},
         _drilled(4), None),
        ("find_geometry", {"target": HUB_COMP, "kind": "circular_edge", "radius": _FLANGE_R,
                           "nearest_to": [_HUB_X, 0, 0], "max_results": 1},
         _rim_edge(_HUB_X, 0, 0, _FLANGE_R), _fg("hub_rim")),
        ("model_fillet", lambda c: {"edges": [_ctx_get(c, "hub_rim", "the flange rim")],
                                    "radius": 1.5}, _filleted, None),
        # THE KEYWAY, on the same axis plane the outline is drawn on. A rectangle arrives with no
        # constraints at all here, so its four sides are squared up before the four dimensions.
        ("sketch_create", {"plane": "xz", "name": HUB_KEYWAY}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": _HUB_X + _KEY_R, "y1": _KEY_TOP,
                                 "x2": _HUB_X + _KEY_R + _KEY_WIDE, "y2": _KEY_TOP + _KEY_LONG,
                                 "sketch_name": HUB_KEYWAY}, _drew(4), None),
    ]
    + _one_line(HUB_KEYWAY, "horizontal", (0, 2))
    + _one_line(HUB_KEYWAY, "vertical", (1, 3))
    + [
        ("sketch_dimension", {"dim_type": "horizontal_distance", "sketch_name": HUB_KEYWAY,
                              "entity_one": "point:0", "entity_two": "line:0:start",
                              "value": f"{_HUB_X + _KEY_R:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "vertical_distance", "sketch_name": HUB_KEYWAY,
                              "entity_one": "point:0", "entity_two": "line:0:start",
                              "value": f"{_KEY_TOP:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "horizontal_distance", "sketch_name": HUB_KEYWAY,
                              "entity_one": "line:0:start", "entity_two": "line:0:end",
                              "value": f"{_KEY_WIDE:g} mm"}, "ok", None),
        ("sketch_dimension", {"dim_type": "vertical_distance", "sketch_name": HUB_KEYWAY,
                              "entity_one": "line:1:start", "entity_two": "line:1:end",
                              "value": f"{_KEY_LONG:g} mm"}, "ok", None),
        ("sketch_get", {"sketch_name": HUB_KEYWAY}, _fully_constrained(HUB_KEYWAY, 4, 4), None),
        ("model_extrude", {"sketch_name": HUB_KEYWAY, "profile_index": 0, "distance": _KEY_HALF,
                           "symmetric": True, "operation": "cut"}, _extruded, None),
        # THE INCLINED FLAT. The datum swings the axis plane 30 degrees about the stub's OWN axis,
        # which is what puts a wedge on the stub rather than beside it.
        ("find_geometry", {"target": HUB_COMP, "kind": "cylinder_face", "radius": _STUB_R,
                           "max_results": 4}, _matched(1, "cylinder_face"), _fg("hub_stub")),
        ("model_construction", lambda c: {"kind": "plane", "mode": "at_angle_on_face",
                                          "face": _ctx_get(c, "hub_stub", "the stub wall"),
                                          "plane": "xz", "angle": 30, "name": HUB_TILT},
         _datum("plane"), None),
        ("sketch_create", {"plane": HUB_TILT, "name": HUB_WEDGE}, _tilt_frame,
         ("hub_tilt_v", _recall("hub_tilt_v", _tilt_v))),
        ("sketch_add_geometry",
         lambda c: {"kind": "closed_path", "sketch_name": HUB_WEDGE,
                    "points": [[-_STUB_END,
                                _ctx_get(c, "hub_tilt_v", "the axis in tilt coords") + _WEDGE_NEAR],
                               [-_STUB_END,
                                _ctx_get(c, "hub_tilt_v", "the axis in tilt coords") + _WEDGE_FAR],
                               [-_SHAFT_END,
                                _ctx_get(c, "hub_tilt_v", "the axis in tilt coords") + _WEDGE_FAR]]},
         _drew(3), None),
        ("model_extrude", {"sketch_name": HUB_WEDGE, "profile_index": 0, "distance": _WEDGE_CUT,
                           "symmetric": True, "operation": "cut"}, _extruded, None),
        # THE FLANGE POCKET, drawn twice over as the shop drawing has it: the outline plus its
        # label, then the sketch that is actually cut.
        ("sketch_create", {"plane": "xy", "name": HUB_FLANGE_TOP}, "ok", None),
        ("sketch_add_geometry", {"kind": "center_point_arc_slot", "sketch_name": HUB_FLANGE_TOP,
                                 "cx": _HUB_X, "cy": 0.0, "x1": _HUB_X + _BOLT_R, "y1": 0.0,
                                 "x2": _HUB_X, "y2": _BOLT_R, "radius": _SECTOR_HALF,
                                 "is_construction": True}, _drew(5), None),
        ("sketch_set_text", {"text": "HUB", "sketch_name": HUB_FLANGE_TOP, "create": True,
                             "height": 3.5, "x": _HUB_X + 27, "y": 17, "angle_deg": 122},
         _labelled("HUB", 3.5), None),
        ("sketch_create", {"plane": "xy", "name": HUB_FLANGE_POCKET}, "ok", None),
        ("sketch_add_geometry", {"kind": "center_point_arc_slot", "sketch_name": HUB_FLANGE_POCKET,
                                 "cx": _HUB_X, "cy": 0.0, "x1": _HUB_X + _BOLT_R, "y1": 0.0,
                                 "x2": _HUB_X, "y2": _BOLT_R, "radius": _SECTOR_HALF},
         _drew(5), None),
        # the square pocket sits in the quadrant the sector does NOT sweep, so both regions cut
        # something a viewer can see.
        ("sketch_add_geometry", {"kind": "rectangle", "sketch_name": HUB_FLANGE_POCKET,
                                 "x1": _HUB_X - 20, "y1": 24, "x2": _HUB_X - 14, "y2": 30},
         _drew(4), None),
        ("model_extrude", {"sketch_name": HUB_FLANGE_POCKET, "profile_index": "all",
                           "distance": -4, "operation": "cut"}, _pocket_cut, None),
        ("find_geometry", {"target": HUB_COMP, "kind": "cylinder_face", "radius": _STUB_R,
                           "max_results": 4}, _matched(1, "cylinder_face"), _fg("hub_thread_face")),
        ("model_thread", lambda c: {"faces": [_ctx_get(c, "hub_thread_face", "the stub wall")],
                                    "designation": "M30x2", "length": 15, "location": "low"},
         _threaded("M30x2", 15.0), None),
        ("model_inspect", {"target": HUB_COMP + ":1"}, _hub_box, None),
        # the whole design's timeline, read once the last hub feature has landed: nothing in it
        # computed into an error or a warning.
        ("design_get", {}, lambda p: p["timeline_healthy"] is True, None),
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ]
)


# ACT 10c4: the hub's own CAM job - the shop's tool set and the two setups, created where the
# document library's own count says the first of them lands.
_HUB_JOB = [
    _watch(HUB_COMP + ":1"),
    # the from_type vocabulary, read before the adds: a spelling this installation does not carry
    # reds HERE, naming it, instead of inside the add that used it.
    ("cam_edit_tools", {"action": "list_types", "scope": "document"},
     _types_offered(*[t for t, _d in _HUB_TOOLS]), None),
    ("cam_edit_tools", {"action": "list", "scope": "document"},
     lambda p: _num(p.get("tool_count")) and p["tool_count"] >= 1,
     ("hub_tool_base", _recall("hub_tool_base", lambda p: p["tool_count"]))),
    ("cam_edit_tools", {"action": "add", "scope": "document",
                        "add_tools": [dict({"from_type": t},
                                           **({"diameter": f"{d:g} mm"} if d else {}))
                                      for t, d in _HUB_TOOLS]},
     lambda p: p.get("added") == len(_HUB_TOOLS)
     and p.get("tool_count") == _RECALL.get("hub_tool_base") + len(_HUB_TOOLS), None),
    ("cam_edit_tools", {"action": "list", "scope": "document"}, _hub_tools_landed, None),
    ("cam_create_setup", {"models": [HUB_COMP + ":1"], "name": HUB_MILL_SETUP},
     _setup_created(HUB_MILL_SETUP, "milling"), None),
    ("cam_create_setup", {"models": [HUB_COMP + ":1"], "name": HUB_TURN_SETUP,
                          "operation_type": "turning"},
     _setup_created(HUB_TURN_SETUP, "turning"), None),
    ("cam_edit_setup", {"setup": HUB_TURN_SETUP, "machine": _TURN_MACHINE,
                        "machine_strip_simulation": True},
     lambda p: p.get("machine_set") == _TURN_MACHINE, None),
    ("cam_get", {}, _hub_setups, None),
]
