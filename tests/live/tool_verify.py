# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Live tool verification: every registered tool called at least once against LIVE Fusion.

A deterministic script of direct tools/call requests (no LLM, no SDK) walking a dependency DAG
that builds its own world in a scratch document and tears it down. The gate: a ledger with zero
unexplained rows - every tool is pass / expected-refusal / skipped(reason).

The sweep is the END-TO-END STORY the evals grade agents on, in one document: a parametric
gyroscope is cast, turned solid, jointed and driven on every axis, detailed, resized, then
REDUCED to its one machinable part (the Carrier bar), a self-centering VISE is modeled around
it (sliders, motion link at ratio -1, grip proven by measure), and a real milling job runs on
the REAL part in the REAL fixture - four operations, generated to completion (an empty toolpath
fails the run), NC posted. Optional SPATIAL value-checks read the scene through the
fusion-spatial add-in's raw TCP protocol (port 8767; a closed port skips them, never fails).
Cameo fixtures for families with no home on the mechanism ride the same document.

A run with zero FAIL/blocked steps writes ``tests/live/VERIFIED_TOOLS.md`` - the tracked receipt: the
per-tool ledger stamped with a SHA-256 of the ``commands/mcpServer/`` source tree, binding that
run to the exact tool source it exercised. ``--check`` recomputes the hash offline (no Fusion
needed) and fails on any difference, so a green suite cannot ride on a live run that never saw
the current code. The hash is of the WORKING TREE while Fusion runs its LOADED copy of the
add-in: after editing source, reload the add-in before re-running, or the receipt stamps code
the session never executed.

Run:  py -3 tests/live/tool_verify.py            (requires Fusion running + the add-in enabled)
      py -3 tests/live/tool_verify.py --check    (no Fusion: exit 1 when VERIFIED_TOOLS.md is missing
                                                  or its source hash differs from the tree)
      py -3 tests/live/tool_verify.py --json     (also write tests/live/results/verify-<ts>.json)
      py -3 tests/live/tool_verify.py --keep-open  (leave the story document open for inspection)

Steps are DATA (see STEPS): each row is (tool, args, expect) where args may be a dict or a
callable(ctx) reading what earlier steps stored, and expect is "ok", "refused" (a deliberate
guard check whose error must name the offense), or a callable(payload) -> bool - a VALUE
PREDICATE run on an ok result (falsy = FAIL); that is how grip contact and machine assignment
are asserted, not just call success. Extend coverage by adding rows, not code.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.request

BASE = "http://127.0.0.1:27182"
MCP = BASE + "/mcp"
SERVER_NAME = "Fusion-Essentials MCP Server"
DOC_PREFIX = "EVAL_sweep"

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
SRC_ROOT = os.path.join(REPO_ROOT, "commands", "mcpServer")
VERIFIED = os.path.join(_HERE, "VERIFIED_TOOLS.md")


def _post(payload):
    req = urllib.request.Request(
        MCP, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call(tool, arguments):
    """One tools/call. Returns (is_error, payload_or_text)."""
    out = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": tool, "arguments": arguments}})
    if "error" in out:
        return True, out["error"].get("message", str(out["error"]))
    result = out["result"]
    text = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    if result.get("isError"):
        return True, text
    try:
        return False, json.loads(text)
    except (ValueError, TypeError):
        return False, text


def health_gate():
    with urllib.request.urlopen(BASE + "/health", timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("server") != SERVER_NAME:
        sys.exit(f"Refusing to run: {BASE} is answering as {data.get('server')!r}, "
                 f"not {SERVER_NAME!r}. Is Autodesk's built-in server on this port?")
    return data


def registered_tools():
    out = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    return sorted(t["name"] for t in out["result"]["tools"])


# â”€â”€ the DAG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ctx keys written by steps (via "save") and read by later args-callables.

def _ctx_get(ctx, key, what):
    if key not in ctx:
        raise KeyError(f"needs ctx[{key!r}] ({what}) from an earlier step")
    return ctx[key]


# A portable scratch dir for the export-to-disk tools (design_export/mesh_export/cam_post) so the
# sweep writes NC/CAD/mesh files somewhere writable on any machine, not a session-specific path.
EXPORT_DIR = os.path.join(tempfile.gettempdir(), "eval_sweep_exports").replace("\\", "/")
os.makedirs(EXPORT_DIR, exist_ok=True)


# save-extractors: pull a handle/profile off a step's payload into ctx for a later args-callable.
def _fg(key):
    return (key, lambda p: p["matches"][0]["handle"])          # find_geometry -> first handle


def _fgn(key):
    return (key, lambda p: [m["handle"] for m in p["matches"]])  # find_geometry -> all handles


def _prof(key):
    return (key, lambda p: p["profiles"][0]["handle"])          # sketch_get -> first profile handle


def _box(name, ox=0, oy=0):
    """Four steps building a fresh free component 'name' holding one 20x20x10 solid box (offset
    ox/oy in the world grid - every cameo gets its own slot so nothing builds on top of the
    gyroscope or another cameo). The reusable free occurrence the joint/assembly steps mate."""
    return [
        ("model_create_component", {"name": name, "activate": True}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": name + "S"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": ox, "y1": oy, "x2": ox + 20, "y2": oy + 20,
                                 "sketch_name": name + "S"}, "ok", None),
        ("model_extrude", {"sketch_name": name + "S", "profile_index": 0, "distance": 10}, "ok", None),
    ]


def _watch(occurrence):
    """A camera row: orient iso and FIT to the occurrence just built, so a viewer watching the run
    sees each feature appear instead of an empty corner of the world."""
    return ("view_set", {"action": "orient", "orientation": "iso-top-right", "focus": occurrence},
            "ok", None)


# â”€â”€ the build, as ACTS: one recognizable gyroscope, end to end, in one unsaved document â”€â”€â”€â”€â”€â”€
# The sweep is a STORY, not a scratch pile: a three-axis gyroscope is cast (skeleton + parameters),
# turned solid (rings, rotor, frame, crank), jointed and DRIVEN on every axis, detailed, machined,
# resized parametrically, and discarded. Every covered tool's receipt step is woven into that story
# where it fits; where it does not, a CAMEO fixture rides inside the SAME document.
#
# Each ACT is a dict: name, precondition, narrative, fallback.
#   precondition: (tool, args) - a live read gating the narrative (the geometry it consumes exists),
#                 or None (an opening act with nothing upstream to depend on).
#   narrative:    the steps weaving the act's tools into the gyroscope story.
#   fallback:     self-contained SCRATCH steps covering the SAME tools if the precondition read
#                 fails (a cascade from an upstream act that could not build) - or None for a
#                 same-doc cameo that depends on nothing. A fallback row is marked "(fallback
#                 fixture)" in the ledger so a narrative regression shows in the diff.
# A step is (tool, args, expect, save): args a dict or callable(ctx); expect "ok"/"refused"; save an
# extractor pair or None. The STORY map below gives each covered tool its ledger shot-list note.

# --- ACT 0: OVERTURE - orient, then open the one document the whole gyroscope lives in ---------
_OVERTURE = [
    ("doc_new", {}, "ok", None),
    ("workspace_orient", {}, "ok", ("fusion_version", lambda p: p["fusion_version"])),
    ("sys_capability_map", {}, "ok", None),
    ("sys_find_tool", {"query": "revolve"}, "ok", None),
    ("sys_get_api_doc", {"searchPattern": "RevolveFeatures", "max_results": 3}, "ok", None),
    ("view_list_workspaces", {}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    # camera projection: a perspective orient carries the angle through to the camera and reads it
    # back; the follow-up orient returns the projection to orthographic for the rest of the story.
    ("view_set", {"action": "orient", "orientation": "iso-top-right", "projection": "perspective",
                  "perspective_angle_deg": 45},
     lambda p: p.get("applied", {}).get("perspective_angle_deg") == 45.0, None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right", "projection": "orthographic"},
     "ok", None),
    # capture options: the transparent + anti-aliased overload at an explicit size produces an image.
    ("view_screenshot", {"width": 320, "height": 240, "transparent_background": True,
                         "anti_aliased": True}, "ok", None),
    ("sys_get_selection", {}, "refused", None),   # nothing picked yet - the expected empty-selection refusal
]

# --- ACT 1: SKELETON + PARAMETERS - parametric skeleton, sketch-only (mirrors scenario S1) -----
_SKELETON = [
    # one driving diameter; every ring/rotor radius derives from it so ACT 6's resize propagates.
    ("param_add", {"name": "GimbalDia", "expression": "120 mm"}, "ok", None),
    ("param_add", {"name": "RotorR", "expression": "GimbalDia / 5"}, "ok", None),
    ("param_add", {"name": "InnerBoreR", "expression": "GimbalDia / 4"}, "ok", None),
    ("param_add", {"name": "InnerOD", "expression": "GimbalDia * 0.3"}, "ok", None),
    ("param_add", {"name": "OuterBoreR", "expression": "GimbalDia * 0.35"}, "ok", None),
    ("param_add", {"name": "OuterOD", "expression": "GimbalDia * 0.4"}, "ok", None),
    ("param_add", {"name": "FrameOpenR", "expression": "GimbalDia * 0.45"}, "ok", None),
    ("param_add", {"name": "FramePlateR", "expression": "GimbalDia * 0.55"}, "ok", None),
    ("param_set_favorite", {"name": "GimbalDia", "favorite": True}, "ok", None),
    ("param_get", {}, "ok", None),
    # the eight-part cast, each its own component; Pedestal NESTED inside the Frame.
    ("model_create_component", {"name": "Frame", "activate": True}, "ok", None),
    ("model_create_component", {"name": "Pedestal", "parent": "Frame", "activate": True}, "ok", None),
    ("model_create_component", {"name": "Carrier", "activate": True}, "ok", None),
    ("model_create_component", {"name": "OuterRing", "activate": True}, "ok", None),
    ("model_create_component", {"name": "InnerRing", "activate": True}, "ok", None),
    ("model_create_component", {"name": "Rotor", "activate": True}, "ok", None),
    ("model_create_component", {"name": "RotorShaft", "activate": True}, "ok", None),
    ("model_create_component", {"name": "Crank", "activate": True}, "ok", None),
    # the SHARED SKELETON on the root: two in-plane axes (construction) + the yaw axis as a 3D line.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "Skeleton"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": -70, "y1": 0, "x2": 70, "y2": 0,
                             "sketch_name": "Skeleton", "is_construction": True}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 0, "y1": -70, "x2": 0, "y2": 70,
                             "sketch_name": "Skeleton", "is_construction": True}, "ok", None),
    ("sketch_add_3d_line", {"x1": 0, "y1": 0, "z1": -70, "x2": 0, "y2": 0, "z2": 70,
                            "sketch_name": "Skeleton"}, "ok", None),
    ("sketch_constrain", {"constraint": "horizontal", "entity_one": "line:0",
                          "sketch_name": "Skeleton"}, "ok", None),
    ("sketch_dimension", {"dim_type": "distance", "entity_one": "point:0", "entity_two": "point:1",
                          "sketch_name": "Skeleton", "value": "GimbalDia"}, "ok", None),
    ("sketch_get", {"sketch_name": "Skeleton"}, "ok", None),
    # draw a helper constraint then delete it - the count drop is the read-back.
    ("sketch_delete_entity", {"sketch_name": "Skeleton", "target": "constraint:0"}, "ok", None),
    # the carrier hub's plane sits BELOW the rotor sweep (vertical zoning) - construction proves here.
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -40, "name": "CarrierHubPlane"}, "ok", None),
    # concentric ring bands, each dimensioned to a PARAMETER so the resize walks them.
    ("design_activate_component", {"occurrence": "OuterRing:1"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "OuterRingSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 48, "sketch_name": "OuterRingSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 42, "sketch_name": "OuterRingSketch"}, "ok", None),
    ("sketch_dimension", {"dim_type": "radius", "entity_one": "circle:0", "sketch_name": "OuterRingSketch", "value": "OuterOD"}, "ok", None),
    ("sketch_dimension", {"dim_type": "radius", "entity_one": "circle:1", "sketch_name": "OuterRingSketch", "value": "OuterBoreR"}, "ok", None),
    ("design_activate_component", {"occurrence": "InnerRing:1"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "InnerRingSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 36, "sketch_name": "InnerRingSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 30, "sketch_name": "InnerRingSketch"}, "ok", None),
    ("sketch_dimension", {"dim_type": "radius", "entity_one": "circle:0", "sketch_name": "InnerRingSketch", "value": "InnerOD"}, "ok", None),
    ("sketch_dimension", {"dim_type": "radius", "entity_one": "circle:1", "sketch_name": "InnerRingSketch", "value": "InnerBoreR"}, "ok", None),
    # the frame plate with an OPEN central opening the rings nest inside.
    ("design_activate_component", {"occurrence": "Frame:1"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "FrameSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 66, "sketch_name": "FrameSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 54, "sketch_name": "FrameSketch"}, "ok", None),
    ("sketch_dimension", {"dim_type": "radius", "entity_one": "circle:0", "sketch_name": "FrameSketch", "value": "FramePlateR"}, "ok", None),
    ("sketch_dimension", {"dim_type": "radius", "entity_one": "circle:1", "sketch_name": "FrameSketch", "value": "FrameOpenR"}, "ok", None),
    # the rotor EDGE-ON: a half-section on a plane containing the spin axis (X), for a revolve.
    ("design_activate_component", {"occurrence": "Rotor:1"}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "RotorSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": -2, "y1": 0, "x2": 2, "y2": 24, "sketch_name": "RotorSketch"}, "ok", None),
    ("sketch_dimension", {"dim_type": "vertical_distance", "entity_one": "point:0", "entity_two": "point:2", "sketch_name": "RotorSketch", "value": "RotorR"}, "ok", None),
    # the rotor shaft along the spin axis.
    ("design_activate_component", {"occurrence": "RotorShaft:1"}, "ok", None),
    ("sketch_create", {"plane": "yz", "name": "ShaftSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 3, "sketch_name": "ShaftSketch"}, "ok", None),
    # the carrier yoke, BELOW the rotor's swing (the rotor disc reaches z=-24): a bar plus a round
    # hub that carries the machinable detail (center bore, bolt circle, end pivot bores - cut in
    # ACT 2). Sitting at z=-32..-26 it clears the rings (z=+/-2) and the spinning rotor.
    ("design_activate_component", {"occurrence": "Carrier:1"}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -32, "name": "CarrierPlane"}, "ok", None),
    ("sketch_create", {"plane": "CarrierPlane", "name": "CarrierSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": -50, "y1": -8, "x2": 50, "y2": 8, "sketch_name": "CarrierSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 14, "sketch_name": "CarrierSketch"}, "ok", None),
    # the pedestal base + a smaller top profile on an offset plane, for a base-to-post LOFT -
    # entirely BELOW the carrier (top at z=-32 meets the carrier's underside).
    ("design_activate_component", {"occurrence": "Pedestal:1"}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -60, "name": "PedBasePlane"}, "ok", None),
    ("sketch_create", {"plane": "PedBasePlane", "name": "PedBase"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 15, "sketch_name": "PedBase"}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -32, "name": "PedTopPlane"}, "ok", None),
    ("sketch_create", {"plane": "PedTopPlane", "name": "PedTop"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 8, "sketch_name": "PedTop"}, "ok", None),
    # the crank: a path + a profile for a swept handle.
    ("design_activate_component", {"occurrence": "Crank:1"}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "CrankPath"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 60, "y1": 0, "x2": 60, "y2": 40, "sketch_name": "CrankPath"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "CrankProf"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 60, "cy": 0, "radius": 4, "sketch_name": "CrankProf"}, "ok", None),
    # the engraved nameplate cameo.
    ("design_activate_component", {"occurrence": "Frame:1"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "NamePlate"}, "ok", None),
    ("sketch_set_text", {"text": "FUSION ESSENTIALS", "sketch_name": "NamePlate", "create": True,
                         "height": 6, "x": -60, "y": 72}, "ok", None),
]

# --- ACT 2: SOLIDS - the parts turn solid, each part its own color (mirrors scenario S2) -------
# The hero solids ride on ACT 1's parametric sketches; the multi-body feature tools that have no
# single natural gyroscope home (draft/mirror/patterns/hole/combine) ride cameo bodies in the SAME
# document, so every one is exercised without contorting the mechanism.
_SOLIDS = [
    # ring bands: extrude the ANNULUS profile (smallest-area region) symmetric about the ring plane.
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", ("or_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "OuterRingSketch", "profile_index": _ctx_get(c, "or_ring", "outer ring annulus"), "distance": 4, "symmetric": True}, "ok", None),
    ("sketch_get", {"sketch_name": "InnerRingSketch"}, "ok", ("ir_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "InnerRingSketch", "profile_index": _ctx_get(c, "ir_ring", "inner ring annulus"), "distance": 4, "symmetric": True}, "ok", None),
    ("sketch_get", {"sketch_name": "FrameSketch"}, "ok", ("fr_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "FrameSketch", "profile_index": _ctx_get(c, "fr_ring", "frame plate ring"), "distance": 4, "symmetric": True}, "ok", None),
    # the rotor disc, REVOLVED about the spin axis; the shaft and carrier extruded.
    ("model_revolve", {"sketch_name": "RotorSketch", "profile_index": 0, "axis": "x", "angle_deg": 360}, "ok", None),
    ("model_extrude", {"sketch_name": "ShaftSketch", "profile_index": 0, "distance": 30, "symmetric": True}, "ok", None),
    # the carrier: bar + hub in one extrude (all regions), then the machinable detail cut
    # through - a 5mm center bore, a 6x 3mm bolt circle on R9, and 4mm pivot bores at the ends.
    ("design_activate_component", {"occurrence": "Carrier:1"}, "ok", None),
    ("model_extrude", {"sketch_name": "CarrierSketch", "profile_index": "all", "distance": 6}, "ok", None),
    ("sketch_create", {"plane": "CarrierPlane", "name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 2.5, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 9, "cy": 0, "radius": 1.5, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 4.5, "cy": 7.794, "radius": 1.5, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": -4.5, "cy": 7.794, "radius": 1.5, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": -9, "cy": 0, "radius": 1.5, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": -4.5, "cy": -7.794, "radius": 1.5, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 4.5, "cy": -7.794, "radius": 1.5, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 45, "cy": 0, "radius": 2, "sketch_name": "CarrierHoles"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": -45, "cy": 0, "radius": 2, "sketch_name": "CarrierHoles"}, "ok", None),
    ("model_extrude", {"sketch_name": "CarrierHoles", "profile_index": "all", "distance": 6, "operation": "cut"}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the pedestal base-to-post transition, LOFTED between the two profiles - built INTO the
    # Pedestal component (the loft lands in the ACTIVE component, not the profiles' owner).
    ("design_activate_component", {"occurrence": "Pedestal:1"}, "ok", None),
    ("sketch_get", {"sketch_name": "PedBase"}, "ok", ("ped_base", lambda p: p["profiles"][0]["handle"])),
    ("sketch_get", {"sketch_name": "PedTop"}, "ok", ("ped_top", lambda p: p["profiles"][0]["handle"])),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "ped_base", "pedestal base"), _ctx_get(c, "ped_top", "pedestal top")]}, "ok", None),
    # the crank handle, SWEPT along its path - into the Crank component for the same reason.
    ("design_activate_component", {"occurrence": "Crank:1"}, "ok", None),
    ("model_sweep", {"profile": {"sketch": "CrankProf", "profile_index": 0}, "path": "sketch:CrankPath"}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    # EACH PART ITS OWN COLOR - the recording's signature look - and a physical material on the rotor.
    ("appearance_set", {"target": "Frame", "color": "#5E6AD2"}, "ok", None),
    ("appearance_set", {"target": "Pedestal", "color": "#8A94A6"}, "ok", None),
    ("appearance_set", {"target": "Carrier", "color": "#1E88E5"}, "ok", None),
    ("appearance_set", {"target": "OuterRing", "color": "#E5533C"}, "ok", None),
    ("appearance_set", {"target": "InnerRing", "color": "#F5A623"}, "ok", None),
    ("appearance_set", {"target": "Rotor:1", "color": "#2FB170"}, "ok", None),
    ("appearance_set", {"target": "RotorShaft:1", "color": "#B0BEC5"}, "ok", None),
    ("appearance_set", {"target": "Crank:1", "color": "#9C27B0"}, "ok", None),
    ("model_set_material", {"target": "Rotor:1", "material": "Steel"}, "ok", None),
    # honest reads on the real mechanism: ring-to-ring gap, rotor/shaft coaxiality, rotor volume.
    ("find_geometry", {"target": "OuterRing", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("or_cyl")),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "or_cyl", "outer ring face"), "b": "InnerRing"}, "ok", None),
    ("find_geometry", {"target": "Rotor:1", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("rotor_cyl")),
    ("find_geometry", {"target": "RotorShaft", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("shaft_cyl")),
    ("model_measure_relation", lambda c: {"relation": "coaxial", "entity_a": _ctx_get(c, "rotor_cyl", "rotor face"), "entity_b": _ctx_get(c, "shaft_cyl", "shaft face")}, "ok", None),
    ("model_inspect", {"target": "Rotor:1"}, "ok", None),
    # PMI: a flatness note on the frame's flat plate face, and a hole/thread note read off a real
    # carrier bolt-circle bore - two DIFFERENT leader points (a shared point errors the second note).
    ("find_geometry", {"target": "Frame", "kind": "planar_face", "max_results": 1}, "ok", _fg("frame_flat")),
    ("pmi_create", lambda c: {"kind": "note", "geometry": [_ctx_get(c, "frame_flat", "frame flat face")], "text": "{flatness}0.05", "name": "PmiFlat"}, "ok", None),
    ("find_geometry", {"target": "Carrier", "kind": "cylinder_face", "radius": 1.5, "max_results": 1}, "ok", _fg("carrier_bore")),
    ("pmi_create", lambda c: {"kind": "hole_note", "geometry": [_ctx_get(c, "carrier_bore", "carrier bolt-circle bore")]}, "ok", None),
    ("pmi_get", {"include": ["segments", "detail"]}, "ok", None),
    ("pmi_edit", {"action": "set_text", "annotation": "PmiFlat", "text": "{perpendicularity}0.03"}, "ok", None),
    ("pmi_edit", {"action": "hide", "annotation": "PmiFlat"}, "ok", None),
    ("pmi_edit", {"action": "show", "annotation": "PmiFlat"}, "ok", None),
    ("pmi_delete", {"annotation": "PmiFlat"}, "ok", None),
    # feature cameos on same-doc scratch bodies (no single natural gyroscope home for these verbs).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "FeatureCameo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "FCPad"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 0, "x2": 240, "y2": 40, "sketch_name": "FCPad"}, "ok", None),
    ("model_extrude", {"sketch_name": "FCPad", "profile_index": 0, "distance": 20}, "ok", None),
    _watch("FeatureCameo:1"),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [200, 20, 10], "max_results": 1}, "ok", _fg("fc_side")),
    ("model_draft", lambda c: {"faces": [_ctx_get(c, "fc_side", "cameo side face")], "pull_direction": "xy", "angle_deg": 3}, "ok", None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_top")),
    # hole points are in the FACE'S LOCAL frame, whose origin for this face is the MODEL origin
    # projected onto its plane - so on-pad coordinates are the world x,y. ([5,5] here drilled at
    # world (5,5), a point off this pad entirely: the hole silently cut whatever body sat near
    # the origin, and the axis-count read-back cannot see a wrong-body cut. Measured live.)
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[220, 20, 0]]}, "ok", None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_body")),
    ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "plane": "yz"}, "ok", None),
    ("model_pattern_rectangular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "quantity_one": 2, "spacing_one": 60, "direction_one": "y"}, "ok", None),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "quantity": 3, "total_angle_deg": 360, "axis": "z"}, "ok", None),
    # a join cameo: two overlapping pads become one body.
    ("model_create_component", {"name": "CombineCameo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "CC1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 100, "x2": 230, "y2": 130, "sketch_name": "CC1"}, "ok", None),
    ("model_extrude", {"sketch_name": "CC1", "profile_index": 0, "distance": 10}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "CC2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 220, "y1": 120, "x2": 250, "y2": 150, "sketch_name": "CC2"}, "ok", None),
    ("model_extrude", {"sketch_name": "CC2", "profile_index": 0, "distance": 10}, "ok", None),
    _watch("CombineCameo:1"),
    ("find_geometry", {"target": "CombineCameo", "kind": "planar_face", "nearest_to": [215, 115, 10], "max_results": 1}, "ok", _fg("cc_a")),
    ("find_geometry", {"target": "CombineCameo", "kind": "planar_face", "nearest_to": [235, 135, 10], "max_results": 1}, "ok", _fg("cc_b")),
    ("model_combine", lambda c: {"target": _ctx_get(c, "cc_a", "combine target"), "tools": [_ctx_get(c, "cc_b", "combine tool")], "operation": "join"}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
]

# --- ACT 3: MOTION - four gimbal axes + a crank->rotor link, driven live (mirrors scenario S3) -
# Joints on the real gyroscope parts via origin snaps (no teleport). assembly_move/capture/constrain
# pose scratch cameos so the mechanism itself is not disturbed.
_MOTION = [
    ("assembly_ground", {"occurrence": "Frame:1", "ground_to_parent": True}, "ok", None),
    ("assembly_rigid_group", {"occurrences": ["Frame:1", "Carrier:1"]}, "ok", None),
    # a coordinate Joint Origin the crank mounts on, and the stock-center JO CAM binds its WCS to.
    ("joint_create_origin", {"anchor": "coordinates", "x": 60, "y": 0, "z": 0, "name": "CrankMount"}, "ok", None),
    ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "StockCenter"}, "ok", None),
    # the four gimbal revolutes, each origin-snapped so parts stay seated.
    ("joint_create", {"occurrence_one": "Carrier:1:origin", "occurrence_two": "Pedestal:1:origin", "joint_type": "revolute", "axis": "z", "name": "Yaw"}, "ok", None),
    ("joint_create", {"occurrence_one": "OuterRing:1:origin", "occurrence_two": "Carrier:1:origin", "joint_type": "revolute", "axis": "x", "name": "PivotOuter"}, "ok", None),
    # the OTHER ring pivot via a joint origin snap on the inner ring (the second creation path).
    ("joint_create", {"occurrence_one": "InnerRing:1:origin", "occurrence_two": "OuterRing:1:origin", "joint_type": "revolute", "axis": "y", "name": "PivotInner"}, "ok", None),
    ("joint_create", {"occurrence_one": "Rotor:1:origin", "occurrence_two": "RotorShaft:1:origin", "joint_type": "revolute", "axis": "x", "name": "Spin"}, "ok", None),
    # the shaft rides in the inner ring as-built (its current seated position).
    ("joint_create_as_built", {"occurrence_one": "RotorShaft:1", "occurrence_two": "InnerRing:1"}, "ok", None),
    # the crank on its frame mount, then LIMITS on the yaw.
    ("joint_create", {"occurrence_one": "Crank:1:origin", "occurrence_two": "CrankMount", "joint_type": "revolute", "axis": "z", "name": "CrankAxis"}, "ok", None),
    ("joint_edit", {"joint_name": "Yaw", "min_deg": -45, "max_deg": 45}, "ok", None),
    # a SECOND creation path AND a real cylinder-face joint on a scratch pin/bore cameo pair.
    ("model_create_component", {"name": "PinCameo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "PinS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 5, "sketch_name": "PinS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PinS", "profile_index": 0, "distance": 20}, "ok", None),
    ("model_create_component", {"name": "BoreCameo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "BoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 8, "sketch_name": "BoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 5.5, "sketch_name": "BoreS"}, "ok", None),
    ("sketch_get", {"sketch_name": "BoreS"}, "ok", ("bore_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "BoreS", "profile_index": _ctx_get(c, "bore_ring", "bore ring"), "distance": 20}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("BoreCameo:1"),
    ("find_geometry", {"target": "PinCameo", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("pin_cyl")),
    ("find_geometry", {"target": "BoreCameo", "kind": "cylinder_face", "radius": 5.5, "max_results": 1}, "ok", _fg("bore_cyl")),
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "pin_cyl", "pin face"), "handle_two": _ctx_get(c, "bore_cyl", "bore face"), "motion": "revolute"}, "ok", None),
    # COUPLE the crank to the rotor spin at ratio 2 - the DOF-fix step - across independent chains.
    ("joint_motion_link", {"joint_one": "CrankAxis", "joint_two": "Spin", "ratio": 2}, "ok", None),
    ("assembly_get", {}, "ok", None),
    # DRIVE EVERY AXIS ON CAMERA: yaw, both ring pivots, then the crank -> rotor at 2:1.
    ("joint_drive", {"joint_name": "Yaw", "angle_deg": 30}, "ok", None),
    ("joint_drive", {"joint_name": "PivotOuter", "angle_deg": 20}, "ok", None),
    ("joint_drive", {"joint_name": "PivotInner", "angle_deg": 25}, "ok", None),
    ("joint_drive", {"joint_name": "CrankAxis", "angle_deg": 30}, "ok", None),
    # CrankAxis (a link member) is now in the session drive registry, so driving its partner Spin
    # must REFUSE (the second-member guard). This is also the live proof that
    # MotionLink.jointOne/jointTwo resolve: a wrong property name would leave the partner lookup
    # blind and this drive would wrongly succeed, failing the row.
    ("joint_drive", {"joint_name": "Spin", "angle_deg": 5}, "refused", None),
    ("assembly_get", {}, "ok", None),   # the crank->rotor 2:1 link read (Spin should read 60 deg)
    ("assembly_inspect_interference", {}, "ok", None),   # driven pose
    ("joint_drive", {"joint_name": "Yaw", "angle_deg": 0}, "ok", None),
    ("joint_drive", {"joint_name": "PivotOuter", "angle_deg": 0}, "ok", None),
    ("joint_drive", {"joint_name": "PivotInner", "angle_deg": 0}, "ok", None),
    ("joint_drive", {"joint_name": "CrankAxis", "angle_deg": 0}, "ok", None),
    ("assembly_inspect_interference", {}, "ok", None),   # rest pose
    # pose + constrain cameos (do not disturb the jointed mechanism).
    ("model_create_component", {"name": "PoseCameo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "PoseS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 300, "y1": 100, "x2": 320, "y2": 120, "sketch_name": "PoseS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PoseS", "profile_index": 0, "distance": 10}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_move", {"occurrence": "PoseCameo:1", "dx": 40}, "ok", None),
    ("assembly_capture_position", {"action": "capture"}, "ok", None),
]
_MOTION += _box("ConA", ox=360) + _box("ConB", ox=360) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_constrain", {"snap_one": "ConA:1:bottom", "snap_two": "ConB:1:top", "flipped": True}, "ok", None),
    ("design_recompute", {}, "ok", None),
]

# --- ACT 4: DETAILS - fillet/chamfer the rings, section through the gimbal center (mirrors S4) -
_DETAILS = [
    ("find_geometry", {"target": "OuterRing", "kind": "circular_edge", "max_results": 1}, "ok", _fg("or_edge")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "or_edge", "outer ring edge")], "radius": 1}, "ok", None),
    ("find_geometry", {"target": "Frame", "kind": "circular_edge", "max_results": 1}, "ok", _fg("fr_edge")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "fr_edge", "frame edge")], "distance": 1}, "ok", None),
    # a shell cameo cap, a wart feature added and deleted (timeline health diff), a scratch occurrence.
    ("model_create_component", {"name": "ShellCap", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ShellS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 400, "y1": 0, "x2": 430, "y2": 30, "sketch_name": "ShellS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ShellS", "profile_index": 0, "distance": 20}, "ok", None),
    _watch("ShellCap:1"),
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [415, 15, 20], "max_results": 1}, "ok", _fg("shell_top")),
    ("model_shell", lambda c: {"body_name": "ShellCap", "remove_faces": [_ctx_get(c, "shell_top", "shell top")], "thickness": 2}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 12, "name": "WartPlane"}, "ok", None),
    ("design_delete_feature", {"feature": "WartPlane"}, "ok", None),
    ("model_create_component", {"name": "ScratchOcc", "activate": False}, "ok", None),
    ("design_delete_occurrence", {"occurrence": "ScratchOcc:1"}, "ok", None),
    # scale + offset-face beats on a scratch block: push a face and read the volume move, then the
    # scale contract - uniform f^3, per-axis x*y*z, the three refusal shapes (unresolvable /
    # length-carrying / angle-carrying expression), a bare unitless parameter accepted, and a
    # vertex-anchored scale.
    ("model_create_component", {"name": "ScaleBlock", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ScaleS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 470, "y1": 0, "x2": 490, "y2": 20,
                             "sketch_name": "ScaleS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ScaleS", "profile_index": 0, "distance": 20}, "ok", None),
    ("find_geometry", {"target": "ScaleBlock", "kind": "planar_face", "nearest_to": [480, 10, 20],
                       "max_results": 1}, "ok", _fg("scale_top")),
    ("model_offset_face", lambda c: {"faces": [_ctx_get(c, "scale_top", "block top")],
                                     "distance": 2}, "ok", None),
    ("param_add", {"name": "ShrinkProbe", "expression": "0.5", "unit": ""}, "ok", None),
    ("param_add", {"name": "TiltProbe", "expression": "30 deg", "unit": "deg"}, "ok", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": 2},
     lambda p: p.get("scale_check") == "volume_ratio"
     and abs(p.get("volume_ratio", 0) - 8.0) < 1e-6, None),
    ("model_scale", {"bodies": ["ScaleBlock"], "x_factor": 3, "y_factor": 2, "z_factor": 1},
     lambda p: abs(p.get("expected_volume_ratio", 0) - 6.0) < 1e-6, None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "NoSuchParamXyz * 2"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "5 mm"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "TiltProbe"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "ShrinkProbe"},
     lambda p: abs(p.get("expected_volume_ratio", 0) - 0.125) < 1e-6, None),
    ("find_geometry", {"target": "ScaleBlock", "kind": "vertex", "max_results": 1}, "ok",
     _fg("scale_vtx")),
    ("model_scale", lambda c: {"bodies": ["ScaleBlock"], "factor": 1.5,
                               "anchor": _ctx_get(c, "scale_vtx", "block vertex")},
     lambda p: abs(p.get("volume_ratio", 0) - 3.375) < 1e-6, None),
    ("param_delete", {"name": "ShrinkProbe"}, "ok", None),
    ("param_delete", {"name": "TiltProbe"}, "ok", None),
    # Section view: cut through the gimbal center, then clear.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    ("view_section", {"action": "cut", "plane": "yz", "offset": 0}, "ok", None),
    ("view_screenshot", {"view": "front", "width": 500, "height": 400}, "ok", None),
    ("view_section", {"action": "clear"}, "ok", None),
    ("view_screenshot_multi", {"views": ["front", "top"], "width": 400, "height": 300}, "ok", None),
]

# --- ACT 6: THE RESIZE - the parametric resize check (mirrors scenario S6) ---------------------
# Bump the one driving diameter; the whole gyroscope grows. Read the rings back before and after.
_RESIZE = [
    ("view_screenshot", {"view": "iso-top-right", "width": 400, "height": 300}, "ok", None),
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", None),   # before
    ("param_set", {"name": "GimbalDia", "expression": "160 mm"}, "ok", None),
    ("design_recompute", {}, "ok", None),
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", None),   # after - rings grew
    ("sketch_get", {"sketch_name": "InnerRingSketch"}, "ok", None),
    # the StockCenter JO (ACT 3, the CAM WCS anchor) read back after the resize: parametrically
    # anchored at the shared center, it HOLDS position through the recompute - the anchor CAM binds.
    ("assembly_get", {"include": ["joint_origins"]}, "ok", None),
    ("view_screenshot", {"view": "iso-top-right", "width": 400, "height": 300}, "ok", None),
    ("param_set", {"name": "GimbalDia", "expression": "120 mm"}, "ok", None),   # restore
    ("design_recompute", {}, "ok", None),
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", None),   # restored
    ("param_add", {"name": "ScratchDim", "expression": "5 mm"}, "ok", None),
    ("param_delete", {"name": "ScratchDim"}, "ok", None),
    # REST-POSE HONESTY over the gyroscope parts: only the intended shaft-in-rotor press fit may
    # overlap; any other gyro-pair collision fails the story (cameo snap-fits are out of scope).
    ("assembly_inspect_interference", {}, lambda p: _gyro_rest_clean(p), None),
]

# --- FINALE: back to the design, beauty shots, then DISCARD the document on camera --------------
_FINALE = [
    ("model_extrude", {"sketch_name": "NoSuchSketch", "distance": 5}, "refused", None),   # guard probe
    ("param_set", {"name": "", "expression": "1"}, "refused", None),                      # guard probe
    ("view_switch_workspace", {"workspace": "design"}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    ("view_screenshot_multi", {"views": ["iso-top-right", "front"], "width": 500, "height": 400}, "ok", None),
    ("design_get", {}, "ok", None),
    ("doc_get", {}, "ok", None),
    ("doc_close", {"save_changes": False}, "ok", None),
]

# â”€â”€ the retained SCRATCH fixtures - the precondition fallbacks (today's proven step bodies) â”€â”€â”€â”€â”€
# When an act's precondition read fails (an upstream act could not build the geometry it consumes),
# the act runs one of these instead, so its tools are still covered - each row marked "(fallback
# fixture)". These are the minimal self-contained scratch fixtures the sweep has always used.

_SOLIDS_FB = (
    _box("FbSolid")
    + [
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fb_body")),
        ("appearance_set", {"target": "FbSolid", "color": "#1E8E3E"}, "ok", None),
        ("model_set_material", {"target": "FbSolid", "material": "Steel"}, "ok", None),
        ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "plane": "yz"}, "ok", None),
        ("model_pattern_rectangular", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "quantity_one": 2, "spacing_one": 60, "direction_one": "y"}, "ok", None),
        ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "quantity": 3, "total_angle_deg": 360, "axis": "z"}, "ok", None),
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fb_top")),
        ("model_hole", lambda c: {"face": _ctx_get(c, "fb_top", "top face"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[5, 5, 0]]}, "ok", None),
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [0, 10, 5], "max_results": 1}, "ok", _fg("fb_side")),
        ("model_draft", lambda c: {"faces": [_ctx_get(c, "fb_side", "side face")], "pull_direction": "xy", "angle_deg": 3}, "ok", None),
        ("model_measure_between", lambda c: {"a": _ctx_get(c, "fb_body", "body"), "b": "FbSolid"}, "ok", None),
        ("model_measure_relation", lambda c: {"relation": "parallel", "entity_a": _ctx_get(c, "fb_top", "top face"), "entity_b": _ctx_get(c, "fb_top", "top face")}, "ok", None),
        ("model_inspect", {"target": "FbSolid"}, "ok", None),
        ("model_create_component", {"name": "FbRev", "activate": True}, "ok", None),
        ("sketch_create", {"plane": "xz", "name": "FbRevS"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": 10, "y1": 0, "x2": 20, "y2": 30, "sketch_name": "FbRevS"}, "ok", None),
        ("model_revolve", {"sketch_name": "FbRevS", "profile_index": 0, "axis": "z", "angle_deg": 360}, "ok", None),
        ("model_create_component", {"name": "FbSwp", "activate": True}, "ok", None),
        ("sketch_create", {"plane": "xz", "name": "FbPath"}, "ok", None),
        ("sketch_add_geometry", {"kind": "line", "x1": 0, "y1": 0, "x2": 0, "y2": 40, "sketch_name": "FbPath"}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": "FbProf"}, "ok", None),
        ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 5, "sketch_name": "FbProf"}, "ok", None),
        ("model_sweep", {"profile": {"sketch": "FbProf", "profile_index": 0}, "path": "sketch:FbPath"}, "ok", None),
        ("model_create_component", {"name": "FbLft", "activate": True}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": "FbLb"}, "ok", None),
        ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 10, "sketch_name": "FbLb"}, "ok", None),
        ("sketch_get", {"sketch_name": "FbLb"}, "ok", _prof("fb_lb")),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": 40, "name": "FbTop"}, "ok", None),
        ("sketch_create", {"plane": "FbTop", "name": "FbLt"}, "ok", None),
        ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 5, "sketch_name": "FbLt"}, "ok", None),
        ("sketch_get", {"sketch_name": "FbLt"}, "ok", _prof("fb_lt")),
        ("model_loft", lambda c: {"profiles": [_ctx_get(c, "fb_lb", "loft bottom"), _ctx_get(c, "fb_lt", "loft top")]}, "ok", None),
        ("model_create_component", {"name": "FbCmb", "activate": True}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": "FbCb1"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 30, "y2": 30, "sketch_name": "FbCb1"}, "ok", None),
        ("model_extrude", {"sketch_name": "FbCb1", "profile_index": 0, "distance": 10}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": "FbCb2"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": 20, "y1": 20, "x2": 50, "y2": 50, "sketch_name": "FbCb2"}, "ok", None),
        ("model_extrude", {"sketch_name": "FbCb2", "profile_index": 0, "distance": 10}, "ok", None),
        ("find_geometry", {"target": "FbCmb", "kind": "planar_face", "nearest_to": [5, 5, 10], "max_results": 1}, "ok", _fg("fb_c1")),
        ("find_geometry", {"target": "FbCmb", "kind": "planar_face", "nearest_to": [45, 45, 10], "max_results": 1}, "ok", _fg("fb_c2")),
        ("model_combine", lambda c: {"target": _ctx_get(c, "fb_c1", "combine target"), "tools": [_ctx_get(c, "fb_c2", "combine tool")], "operation": "join"}, "ok", None),
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ]
)

# ACT 3 fallback: the proven 12-box joint/assembly fixture.
_MOTION_FB = (
    [("design_activate_component", {"occurrence": "root"}, "ok", None)]
    + _box("JA") + _box("JB") + _box("JC", ox=100) + _box("JD", ox=100) + _box("JE") + _box("JF")
    + _box("JG") + _box("JH") + _box("JK") + _box("JL") + _box("JM") + _box("JN")
    + _box("JP") + _box("JQ")
    + [
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("assembly_ground", {"occurrence": "JB:1", "ground_to_parent": True}, "ok", None),
        ("assembly_ground", {"occurrence": "JD:1", "ground_to_parent": True}, "ok", None),
        ("assembly_ground", {"occurrence": "JN:1", "ground_to_parent": True}, "ok", None),
        ("joint_create_origin", {"anchor": "coordinates", "x": 10, "y": 0, "z": 0, "name": "JOc"}, "ok", None),
        ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "StockCenter"}, "ok", None),
        ("joint_create", {"occurrence_one": "JA:1:top", "occurrence_two": "JB:1:top", "joint_type": "revolute", "axis": "z", "name": "RevJoint"}, "ok", None),
        ("joint_create", {"occurrence_one": "JC:1:top", "occurrence_two": "JD:1:top", "joint_type": "slider", "axis": "x", "name": "SlideJoint"}, "ok", None),
        ("joint_drive", {"joint_name": "RevJoint", "angle_deg": 30}, "ok", None),
        ("joint_drive", {"joint_name": "RevJoint", "angle_deg": 0}, "ok", None),
        ("joint_edit", {"joint_name": "RevJoint", "min_deg": -45, "max_deg": 45}, "ok", None),
        ("joint_create", {"occurrence_one": "JM:1:top", "occurrence_two": "JN:1:top", "joint_type": "revolute", "axis": "z", "name": "RevJoint2"}, "ok", None),
        ("joint_motion_link", {"joint_one": "RevJoint", "joint_two": "RevJoint2", "ratio": 2}, "ok", None),
        # RevJoint was driven above, so driving its NEW link partner must refuse (the second-member
        # guard). This row also proves MotionLink.jointOne/jointTwo resolve live: a wrong property
        # name would make the partner lookup blind and this drive would wrongly succeed.
        ("joint_drive", {"joint_name": "RevJoint2", "angle_deg": 10}, "refused", None),
        ("joint_create_as_built", {"occurrence_one": "JE:1", "occurrence_two": "JF:1"}, "ok", None),
        ("find_geometry", {"target": "JG", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("jg_face")),
        ("find_geometry", {"target": "JH", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("jh_face")),
        ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "jg_face", "joint face a"), "handle_two": _ctx_get(c, "jh_face", "joint face b"), "motion": "rigid"}, "ok", None),
        ("assembly_get", {}, "ok", None),
        ("assembly_move", {"occurrence": "JK:1", "dx": 80}, "ok", None),
        ("assembly_capture_position", {"action": "capture"}, "ok", None),
        ("assembly_rigid_group", {"occurrences": ["JP:1", "JQ:1"]}, "ok", None),
        ("assembly_constrain", {"snap_one": "JK:1:bottom", "snap_two": "JL:1:top", "flipped": True}, "ok", None),
        ("assembly_inspect_interference", {}, "ok", None),
        ("design_recompute", {}, "ok", None),
    ]
)

# ACT 4 fallback: a scratch details fixture.
_DETAILS_FB = (
    _box("FbDet")
    + [
        ("find_geometry", {"target": "FbDet", "kind": "line_edge", "max_results": 1}, "ok", _fg("fd_edge")),
        ("model_fillet", lambda c: {"edges": [_ctx_get(c, "fd_edge", "edge")], "radius": 1}, "ok", None),
        ("find_geometry", {"target": "FbDet", "kind": "line_edge", "max_results": 1}, "ok", _fg("fd_edge2")),
        ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "fd_edge2", "edge")], "distance": 0.5}, "ok", None),
        ("find_geometry", {"target": "FbDet", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fd_top")),
        ("model_shell", lambda c: {"body_name": "FbDet", "remove_faces": [_ctx_get(c, "fd_top", "top")], "thickness": 2}, "ok", None),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "FbWart"}, "ok", None),
        ("design_delete_feature", {"feature": "FbWart"}, "ok", None),
        ("model_create_component", {"name": "FbJunk", "activate": False}, "ok", None),
        ("design_delete_occurrence", {"occurrence": "FbJunk:1"}, "ok", None),
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("view_section", {"action": "cut", "plane": "xy", "offset": 5}, "ok", None),
        ("view_screenshot", {"view": "front", "width": 400, "height": 300}, "ok", None),
        ("view_section", {"action": "clear"}, "ok", None),
        ("view_screenshot_multi", {"views": ["front", "top"], "width": 300, "height": 250}, "ok", None),
    ]
)

# ACT 6 fallback: a scratch parameter set/delete.
_RESIZE_FB = [
    ("param_add", {"name": "FbParam", "expression": "12 mm"}, "ok", None),
    ("param_set", {"name": "FbParam", "expression": "14 mm"}, "ok", None),
    ("design_recompute", {}, "ok", None),
    ("param_delete", {"name": "FbParam"}, "ok", None),
]

# â”€â”€ the CAMEO acts: surface-prep, mesh, and CAM families ride scratch fixtures in the SAME doc â”€â”€
# These families have no natural home on the mechanism itself, so the spec places them as cameos.

# ACT 5: MACHINING PREP - surfaces, sheet ops, split/stitch/arrange/base-feature, holder read.
_MACHINING = [
    # the surface/split/stitch cameos live on a GRID (y=200 row, plus a z-lifted revolve) so each
    # builds in clear space a viewer can see, never on top of the gyroscope or another cameo.
    ("model_create_component", {"name": "SRev", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "SRevS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 10, "y1": 60, "x2": 10, "y2": 90, "sketch_name": "SRevS"}, "ok", None),
    ("surface_revolve", {"sketch_name": "SRevS", "axis": "z", "angle_deg": 360}, "ok", None),
    ("find_geometry", {"target": "SRev", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("srev_face")),
    ("surface_thicken", lambda c: {"faces": [_ctx_get(c, "srev_face", "surface face")], "thickness": 2}, "ok", None),
    _watch("SRev:1"),
    ("model_create_component", {"name": "Surf", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SurfS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 200, "x2": 240, "y2": 230, "sketch_name": "SurfS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SurfS", "distance": 15}, "ok", None),
    _watch("Surf:1"),
    ("find_geometry", {"target": "Surf", "kind": "planar_face", "max_results": 1}, "ok", _fg("surf_face")),
    ("surface_offset", lambda c: {"faces": [_ctx_get(c, "surf_face", "surface face")], "distance": 3}, "ok", None),
    ("surface_offset", lambda c: {"faces": [_ctx_get(c, "surf_face", "surface face")], "distance": 0}, "ok", None),
    ("find_geometry", {"target": "Surf", "kind": "line_edge", "max_results": 1}, "ok", _fg("surf_edge")),
    ("surface_extend", lambda c: {"edges": [_ctx_get(c, "surf_edge", "surface edge")], "distance": 2}, "ok", None),
    ("find_geometry", {"target": "Surf", "kind": "planar_face", "max_results": 1}, "ok", _fg("surf_body")),
    ("surface_reverse_normal", lambda c: {"bodies": [_ctx_get(c, "surf_body", "surface body")]}, "ok", None),
    ("model_create_component", {"name": "SDel", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SD1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 300, "y1": 200, "x2": 320, "y2": 220, "sketch_name": "SD1"}, "ok", None),
    ("model_extrude", {"sketch_name": "SD1", "profile_index": 0, "distance": 10}, "ok", None),
    _watch("SDel:1"),
    ("find_geometry", {"target": "SDel", "kind": "planar_face", "nearest_to": [310, 210, 10], "max_results": 1}, "ok", _fg("sdel_top")),
    ("surface_delete_face", lambda c: {"faces": [_ctx_get(c, "sdel_top", "top face")], "heal": False}, "ok", None),
    ("find_geometry", {"target": "SDel", "kind": "line_edge", "nearest_to": [310, 210, 10], "max_results": 4}, "ok", _fgn("sdel_rim")),
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim", "rim edges")}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "Proj"}, "ok", None),
    ("find_geometry", {"target": "SDel", "kind": "planar_face", "nearest_to": [310, 210, 0], "max_results": 1}, "ok", _fg("proj_face")),
    ("sketch_project", lambda c: {"entities": [_ctx_get(c, "proj_face", "project face")], "sketch_name": "Proj"}, "ok", None),
    # surface_trim + surface_untrim on an intersecting-sheet topology. Staged in clear space (X=600)
    # so no other body's surface intersects the sheet - only its own cutter divides it, keeping the
    # trim deterministic (a coincident surface adds phantom cells and the trim keeps the wrong one).
    ("model_create_component", {"name": "SHole", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SH1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 0, "x2": 640, "y2": 0, "sketch_name": "SH1"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SH1", "distance": 40}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "SH2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 620, "cy": -20, "radius": 5, "sketch_name": "SH2"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SH2", "distance": 10, "symmetric": True}, "ok", None),
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_sheet")),
    ("find_geometry", {"target": "SHole", "kind": "cylinder_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_cutter")),
    ("surface_trim", lambda c: {"surface": _ctx_get(c, "sh_sheet", "sheet face"), "trim_tool": _ctx_get(c, "sh_cutter", "cylinder cutter")}, "ok", None),
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_trimmed")),
    ("surface_untrim", lambda c: {"faces": [_ctx_get(c, "sh_trimmed", "trimmed sheet face")], "loop_type": "internal"}, "ok", None),
    # split / unstitch / stitch / base-feature / arrange / compute-holder.
    ("model_create_component", {"name": "Spl", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "Sp1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 400, "y1": 200, "x2": 440, "y2": 240, "sketch_name": "Sp1"}, "ok", None),
    ("model_extrude", {"sketch_name": "Sp1", "profile_index": 0, "distance": 20}, "ok", None),
    _watch("Spl:1"),
    ("model_construction", {"kind": "plane", "plane": "xz", "offset": 220, "name": "SplMid"}, "ok", None),
    ("find_geometry", {"target": "Spl", "kind": "planar_face", "nearest_to": [420, 220, 20], "max_results": 1}, "ok", _fg("spl_body")),
    ("model_split", lambda c: {"split": "body", "target": _ctx_get(c, "spl_body", "split body"), "split_plane": "SplMid"}, "ok", None),
    ("model_create_component", {"name": "Stc", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "St1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 500, "y1": 200, "x2": 520, "y2": 220, "sketch_name": "St1"}, "ok", None),
    ("model_extrude", {"sketch_name": "St1", "profile_index": 0, "distance": 10}, "ok", None),
    _watch("Stc:1"),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [510, 210, 10], "max_results": 1}, "ok", _fg("stc_body")),
    ("model_unstitch", lambda c: {"target": _ctx_get(c, "stc_body", "unstitch body"), "chain": False}, "ok", None),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [510, 210, 0], "max_results": 1}, "ok", _fg("stc_f1")),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [500, 210, 5], "max_results": 1}, "ok", _fg("stc_f2")),
    ("model_stitch", lambda c: {"bodies": [_ctx_get(c, "stc_f1", "stitch a"), _ctx_get(c, "stc_f2", "stitch b")]}, "ok", None),
    ("model_base_feature", {"action": "start", "base_feature": "BF1"}, "ok", None),
    ("model_base_feature", {"action": "finish", "base_feature": "BF1"}, "ok", None),
] + _box("ArrP1", ox=200, oy=350) + _box("ArrP2", ox=260, oy=350) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ArrB"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 100, "y1": 400, "x2": 500, "y2": 600, "sketch_name": "ArrB"}, "ok", None),
    ("model_arrange", {"boundary_sketch": "ArrB", "shapes": ["ArrP1:1", "ArrP2:1"], "solver": "rectangular", "spacing": 5}, "ok", None),
    # compute_holder needs a body + a cyl-face axis + a planar end-datum.
    ("model_create_component", {"name": "HolderPart", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "HP1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 600, "y1": 200, "x2": 640, "y2": 220, "sketch_name": "HP1"}, "ok", None),
    ("model_extrude", {"sketch_name": "HP1", "profile_index": 0, "distance": 10}, "ok", None),
    _watch("HolderPart:1"),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "nearest_to": [620, 210, 10], "max_results": 1}, "ok", _fg("hp_top")),
    # hole points ride the face's LOCAL frame = the model origin projected onto the face, so
    # on-pad coordinates are the world x,y (same measured fact as the FeatureCameo hole).
    ("model_hole", lambda c: {"face": _ctx_get(c, "hp_top", "holder top"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[605, 205, 0]]}, "ok", None),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "max_results": 1}, "ok", _fg("hp_body")),
    ("find_geometry", {"target": "HolderPart", "kind": "cylinder_face", "radius": 2, "max_results": 1}, "ok", _fg("hp_axis")),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "nearest_to": [620, 210, 10], "max_results": 1}, "ok", _fg("hp_datum")),
    ("model_compute_holder", lambda c: {"body": _ctx_get(c, "hp_body", "holder body"), "axis": _ctx_get(c, "hp_axis", "holder axis"), "end_datum": _ctx_get(c, "hp_datum", "holder datum")}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
]

# ACT 7: MESH - a scratch solid becomes a mesh, then the mesh family works it (one mesh per op).
_MESH = [
    ("model_create_component", {"name": "Msh", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "MshS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 300, "x2": 220, "y2": 320, "sketch_name": "MshS"}, "ok", None),
    ("model_extrude", {"sketch_name": "MshS", "profile_index": 0, "distance": 10}, "ok", None),
    _watch("Msh:1"),
    ("find_geometry", {"target": "Msh", "kind": "planar_face", "nearest_to": [210, 310, 10], "max_results": 1}, "ok", _fg("msh_body")),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "MshMid"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "MshCyl"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 260, "cy": 360, "radius": 15, "sketch_name": "MshCyl"}, "ok", None),
    ("model_extrude", {"sketch_name": "MshCyl", "profile_index": 0, "distance": 20}, "ok", None),
    ("find_geometry", {"target": "Msh", "kind": "cylinder_face", "nearest_to": [260, 360, 10], "max_results": 1}, "ok", _fg("cyl_body")),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "cyl_body", "cyl body"), "name": "MRED", "quality": "high"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MA", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MC", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MD", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "ME", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MF", "quality": "low"}, "ok", None),
    ("mesh_get", {"target": "Msh"}, "ok", None),
    ("mesh_generate_face_groups", {"mesh": "MA", "method": "fast"}, "ok", None),
    ("mesh_to_brep", {"mesh": "MA", "method": "faceted", "operation": "base_feature"}, "ok", None),
    ("mesh_reduce", {"mesh": "MRED", "target": "proportion", "value": 50}, "ok", None),
    ("mesh_remesh", {"mesh": "MC", "density": 1}, "ok", None),
    ("mesh_plane_cut", {"mesh": "MD", "plane": "MshMid", "cut_type": "trim"}, "ok", None),
    ("mesh_combine", {"target": "ME", "tools": ["MF"], "operation": "join"}, "ok", None),
    # a pristine scratch mesh deleted with the survivor check: the payload's own claim is the
    # re-scan. A mesh another feature already transformed (e.g. the plane-cut MD) carries a
    # different lineage; this beat exercises the plain-delete contract.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MDEL",
                                "quality": "low"}, "ok", None),
    ("mesh_delete", {"mesh": "MDEL"}, "ok", None),
    ("mesh_export", {"target": "MA", "file_path": EXPORT_DIR + "/eval_mesh", "format": "stl"}, "ok", None),
    ("mesh_insert", {"file_path": EXPORT_DIR + "/eval_mesh.stl", "name": "MshIns"}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
]

# â”€â”€ the SPATIAL value-checks: the fusion-spatial add-in's raw TCP protocol (stdlib only) â”€â”€â”€â”€â”€â”€â”€â”€
# A closed port 8767 SKIPS a spatial phase (one skipped row, never a FAIL); a check that returns
# false is a FAIL row - it means the STORY's geometry is wrong, which blocks the receipt.
SPATIAL_PORT = 8767


def spatial_rpc(sock, method, params=None):
    sock.sendall((json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n")
                 .encode("utf-8"))
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise RuntimeError("spatial add-in closed the connection mid-reply")
        buf += chunk
    reply = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    if "error" in reply:
        raise RuntimeError(reply["error"].get("message", "spatial error"))
    return reply["result"]


def spatial_phase(name, checks, rows):
    """Run spatial value-checks against the live scene. checks = [(label, fn(bodies) -> bool)]."""
    print(f"\n-- {name} --")
    try:
        sock = socket.create_connection(("127.0.0.1", SPATIAL_PORT), timeout=2)
    except OSError:
        rows.append((name, "skipped", f"spatial add-in port {SPATIAL_PORT} closed"))
        print(f"  skipped(spatial): port {SPATIAL_PORT} closed")
        return
    try:
        inventory = spatial_rpc(sock, "space.bodies", {"scope": "all"})
        bodies = inventory.get("bodies", [])
        print(f"  space.bodies: {len(bodies)} bodies, units={inventory.get('units')}, "
              f"up={inventory.get('upAxis')}")
        for label, fn in checks:
            try:
                passed = bool(fn(bodies))
            except Exception as e:
                rows.append((f"{name}:{label}", "FAIL", f"check raised: {e}"))
                continue
            rows.append((f"{name}:{label}", "pass" if passed else "FAIL",
                         "" if passed else "spatial check returned false"))
    except Exception as e:
        rows.append((name, "FAIL", str(e)[:160]))
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _solids(bodies):
    return [b for b in bodies if b.get("kind") == "solid"]


def _named(bodies, fragment):
    frag = fragment.lower()
    return [b for b in bodies if frag in ((b.get("layer") or "") + (b.get("name") or "")).lower()]


def _xy_within(inner, outer, slack=0.5):
    """inner's world XY footprint sits inside outer's (radial nesting, seen from bboxes)."""
    bi, bo = inner["bbox"], outer["bbox"]
    return (bi["min"][0] >= bo["min"][0] - slack and bi["max"][0] <= bo["max"][0] + slack
            and bi["min"][1] >= bo["min"][1] - slack and bi["max"][1] <= bo["max"][1] + slack)


def _one(bodies, frag):
    """One body by occurrence path: EXACT layer match first ('Frame:1' must not resolve to the
    nested 'Frame:1/Pedestal:1' body via substring), fragment match as the fallback."""
    exact = [b for b in bodies if (b.get("layer") or "") == frag]
    if exact:
        return exact[0]
    hits = _named(bodies, frag)
    return hits[0] if hits else None


GYRO_CHECKS = [
    # the eight-part cast (plus cameos) is standing: a thin inventory floor, not an exact count.
    ("solid_count>=8", lambda bodies: len(_solids(bodies)) >= 8),
    # the rotor is a revolved disc R=GimbalDia/5=24mm, 4mm thick: pi*24^2*4 ~ 7238 mm^3.
    ("rotor_volume_band", lambda bodies: any(
        b.get("volume") and 6000 <= b["volume"] <= 8500 for b in _named(bodies, "rotor"))),
    # every solid reports a world bbox the engine could mesh.
    ("bboxes_present", lambda bodies: all(b.get("bbox") for b in _solids(bodies))),
    # the gimbal NESTS - rotor inside the inner ring, inner inside outer, outer inside the frame.
    ("gimbal_nesting_chain", lambda bodies: _xy_within(_one(bodies, "Rotor:1"), _one(bodies, "InnerRing"))
        and _xy_within(_one(bodies, "InnerRing"), _one(bodies, "OuterRing"))
        and _xy_within(_one(bodies, "OuterRing"), _one(bodies, "Frame:1"))),
    # the carrier rides BELOW the rotor's swing (the disc reaches z=-24) - no sweep collision.
    ("carrier_clear_of_rotor", lambda bodies:
        _one(bodies, "Carrier")["bbox"]["max"][2] <= _one(bodies, "Rotor:1")["bbox"]["min"][2] + 0.01),
    # the pedestal sits entirely below the carrier (the stack: pedestal -> carrier -> gimbal).
    ("pedestal_below_carrier", lambda bodies:
        _one(bodies, "Pedestal")["bbox"]["max"][2] <= _one(bodies, "Carrier")["bbox"]["min"][2] + 0.01),
]

FIXTURE_CHECKS = [
    # the gripped stack is standing: stock + two jaws + base + the carrier inside.
    ("fixture_parts_present", lambda bodies: all(
        _named(bodies, frag) for frag in ("STOCK", "JawL", "JawR", "ViseBase", "Carrier"))),
    # SELF-CENTERING, seen from independent spatial data: after driving ONE jaw, the two jaws
    # sit mirrored about y=0 (JawL's near face at -y equals JawR's near face at +y).
    ("jaws_mirror_about_center", lambda bodies: abs(
        _named(bodies, "JawL")[0]["bbox"]["max"][1]
        + _named(bodies, "JawR")[0]["bbox"]["min"][1]) <= 0.1),
    ("jaw_travel_happened", lambda bodies:
        _named(bodies, "JawL")[0]["bbox"]["max"][1] > -13.5),  # built at -15; driven +3 -> -12
    # machinist seating: the stock rides PROUD of the jaw tops (a cutter can reach the part).
    ("stock_proud_of_jaws", lambda bodies: _named(bodies, "STOCK")[0]["bbox"]["max"][2]
        > _named(bodies, "JawL")[0]["bbox"]["max"][2]),
]


# Rest-pose interference gate over the GYROSCOPE parts (the _RESIZE predicate): only the intended
# shaft-in-rotor press-fit pair may overlap. Cameo pairs (a pin seated in its bore, the constrained
# boxes) are joint-snapped fits by design and are out of scope.
_GYRO_PARTS = {"Frame:1", "Pedestal:1", "Carrier:1", "OuterRing:1", "InnerRing:1",
               "Rotor:1", "RotorShaft:1", "Crank:1"}


def _gyro_rest_clean(p):
    for i in p.get("measured", {}).get("interferences", []):
        a = (i.get("occurrence_one") or "").split("/")[-1]
        b = (i.get("occurrence_two") or "").split("/")[-1]
        if a in _GYRO_PARTS and b in _GYRO_PARTS and sorted([a, b]) != ["Rotor:1", "RotorShaft:1"]:
            return False
    return True


# ACT 8: REDUCE - the gyroscope is stripped to its ONE machinable part (the Carrier bar). The
# mechanism and the near-origin cameos go; the survivor stays parametric. The far-grid cameo
# fixtures (surface/mesh families) are out of frame and stay.
_REDUCE = [
    ("view_switch_workspace", {"workspace": "design"}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
] + [
    ("design_delete_occurrence", {"occurrence": occ}, "ok", None)
    for occ in ("Crank:1", "RotorShaft:1", "Rotor:1", "InnerRing:1", "OuterRing:1",
                "Frame:1",           # takes the nested Pedestal with it
                "FeatureCameo:1", "CombineCameo:1", "PinCameo:1", "BoreCameo:1",
                "PoseCameo:1", "ConA:1", "ConB:1", "ShellCap:1")
] + [
    # the root Skeleton sketch (the construction axis crosses) goes too - the fixture scene
    # shows the PART, not the build scaffolding.
    ("design_delete_feature", {"feature": "Skeleton"}, "ok", None),
    # the survivor, alone and still parametric.
    ("model_inspect", {"target": "Carrier:1"}, "ok", None),
    ("workspace_orient", {}, "ok", None),
    _watch("Carrier:1"),
]


# ACT 9: VISE FIXTURE - the eval-proven self-centering vise modeled around the Carrier.
# Geometry contract (all mm, Carrier occupies x[-50,50] hub y[-14,14] z[-32,-26]):
#   STOCK    x[-55,55] y[-18,18] z[-35,-23]  - real margin all around (the adaptive's material)
#   ViseBase x[-70,70] y[-52,52] z[-69,-49]
#   Jaw seat z=-35 (the stock's underside rests level with the seat ledge)
#   Jaw grip faces OPEN at y=-/+21; stock sides at y=-/+18 -> each jaw closes 3mm to contact
#   Jaw lips top out at z=-27 -> the stock rides 4mm PROUD of the jaws (machinist seating)

def _plate(comp, sketch, z_offset, x1, y1, x2, y2, height):
    """Component + its own build plane at z_offset + one rectangle, extruded up by height.
    The plane lives INSIDE the component: sketch_create resolves construction-plane names in
    the ACTIVE component, so a root-level plane is invisible after activate."""
    plane = comp + "Floor"
    return [
        ("model_create_component", {"name": comp, "activate": True}, "ok", None),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": z_offset,
                                "name": plane}, "ok", None),
        ("sketch_create", {"plane": plane, "name": sketch}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                 "sketch_name": sketch}, "ok", None),
        ("model_extrude", {"sketch_name": sketch, "profile_index": 0, "distance": height},
         "ok", None),
    ]


def _second_plate(comp, sketch, z_offset, x1, y1, x2, y2, height):
    """A second stacked extrude inside the ACTIVE component (the jaw's upper gripping lip)."""
    plane = comp + "LipPlane"
    return [
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": z_offset,
                                "name": plane}, "ok", None),
        ("sketch_create", {"plane": plane, "name": sketch}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                 "sketch_name": sketch}, "ok", None),
        ("model_extrude", {"sketch_name": sketch, "profile_index": 0, "distance": height},
         "ok", None),
    ]


_VISE = (
    _plate("ViseBase", "VBase", -69, -70, -52, 70, 52, 20)
    # JawL: lower seat block up to the seat ledge (z=-35), then the gripping lip above it.
    + _plate("JawL", "JLseat", -49, -30, -37, 30, -15, 14)
    + _second_plate("JawL", "JLlip", -35, -30, -37, 30, -21, 8)
    + _plate("JawR", "JRseat", -49, -30, 15, 30, 37, 14)
    + _second_plate("JawR", "JRlip", -35, -30, 21, 30, 37, 8)
    + _plate("STOCK", "StockS", -35, -55, -18, 55, 18, 12)
) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("appearance_set", {"target": "ViseBase", "color": "#455A64"}, "ok", None),
    ("appearance_set", {"target": "JawL", "color": "#E5533C"}, "ok", None),
    ("appearance_set", {"target": "JawR", "color": "#E5533C"}, "ok", None),
    ("appearance_set", {"target": "STOCK", "color": "#8D6E63"}, "ok", None),
    # the fixture skeleton: base grounded, each jaw a NAMED slider on the base. Every component
    # here has its origin at the WORLD origin (geometry is drawn in world coordinates), so the
    # ':origin' snap aligns already-aligned frames - a positional no-op, no teleport - and the
    # slider axis 'y' rides the world-aligned frame.
    ("assembly_ground", {"occurrence": "ViseBase:1", "ground_to_parent": True}, "ok", None),
    ("joint_create", {"occurrence_one": "JawL:1:origin", "occurrence_two": "ViseBase:1:origin",
                      "joint_type": "slider", "axis": "y", "name": "SlideL"}, "ok", None),
    ("joint_create", {"occurrence_one": "JawR:1:origin", "occurrence_two": "ViseBase:1:origin",
                      "joint_type": "slider", "axis": "y", "name": "SlideR"}, "ok", None),
    # the stock in the vise, the part in the stock - both as-built rigid (no motion).
    ("joint_create_as_built", {"occurrence_one": "STOCK:1", "occurrence_two": "ViseBase:1"}, "ok", None),
    ("joint_create_as_built", {"occurrence_one": "Carrier:1", "occurrence_two": "STOCK:1"}, "ok", None),
    # SELF-CENTERING: couple the two sliders at ratio -1 (on real sliders the platform accepts
    # slider-slider links - live-verified).
    ("joint_motion_link", {"joint_one": "SlideL", "joint_two": "SlideR", "ratio": -1}, "ok", None),
    # drive ONE jaw closed by its 3mm approach; the link must bring the OTHER jaw in too.
    ("joint_drive", {"joint_name": "SlideL", "distance": 3}, "ok", None),
    # GRIP VERIFIED BY MEASURE, not by trust: each jaw's gripping face touches its stock side.
    ("find_geometry", {"target": "JawL", "kind": "planar_face", "nearest_to": [0, -18, -31],
                       "max_results": 1}, "ok", _fg("jawL_grip")),
    ("find_geometry", {"target": "JawR", "kind": "planar_face", "nearest_to": [0, 18, -31],
                       "max_results": 1}, "ok", _fg("jawR_grip")),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "jawL_grip", "JawL grip face"),
                                         "b": "STOCK"},
     lambda p: p.get("distance", 99) <= 0.1, None),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "jawR_grip", "JawR grip face"),
                                         "b": "STOCK"},
     lambda p: p.get("distance", 99) <= 0.1, None),
    ("assembly_inspect_interference", {}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    ("view_screenshot", {"view": "iso-top-right", "width": 500, "height": 400}, "ok", None),
]


# ACT 10a: CAM on the REAL part in the REAL fixture - job built and generated. The scratch-stock
# rows remain as this act's fallback, so the CAM family stays covered when the story world
# could not build.
_CAM_STORY = [
    ("view_switch_workspace", {"workspace": "manufacture"}, "ok", None),
    ("cam_get", {}, "ok", None),
    # two document tools: a mill for the milling ops, a 3mm drill for the bolt circle.
    ("cam_edit_tools", {"action": "add", "scope": "document",
                        "add_tools": [{"from_type": "flat end mill"},
                                      {"from_type": "drill", "diameter": "3 mm"}]}, "ok", None),
    # sample clones keep the sample's tool number; two clones can collide and the post refuses
    # ("Different tools have the same tool number") - assign distinct numbers explicitly.
    ("cam_edit_tools", {"action": "edit", "scope": "document", "tool": 0,
                        "parameters": {"tool_number": "1"}}, "ok", None),
    ("cam_edit_tools", {"action": "edit", "scope": "document", "tool": 1,
                        "parameters": {"tool_number": "2"}}, "ok", None),
    # preset beats: the from_type vocabulary spans all five sample libraries (center drill lives
    # only in Hole Making Tools (Inch)); presets round-trip with read-back, unit, and refusal gates.
    ("cam_edit_tools", {"action": "list_types", "scope": "document"},
     lambda p: "center drill" in p["types"] and "turning general" in p["types"], None),
    ("cam_edit_tools", {"action": "add", "scope": "document",
                        "add_tools": [{"from_type": "turning general"},
                                      {"from_type": "center drill"}]}, "ok", None),
    # a turning-general preset carries surface speed, not spindle speed - the refusal names what
    # the preset actually has instead of applying nothing.
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 2,
                        "preset": {"name": "SweepTurn", "spindle_speed": 400}}, "refused", None),
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "SweepMM", "feed": 900, "spindle_speed": 12000}},
     lambda p: "SweepMM" in p["presets"], None),
    # a units-carrying expression is stored verbatim and evaluated (35in/min -> 889 mm/min).
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "Sweep35", "feed": "35in/min"}},
     lambda p: "Sweep35" in p["presets"], None),
    # a numeric-leading expression that fails evaluation is refused and rolled back, never a
    # silent zero.
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "SweepBad", "spindle_speed": "900 * NoSuchParamXyz"}},
     "refused", None),
    ("cam_edit_tools", {"action": "remove_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "SweepMM"}},
     lambda p: "SweepMM" not in p["presets"] and isinstance(p.get("removed_index"), int), None),
    ("cam_edit_tools", {"action": "remove_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "Sweep35"}},
     lambda p: "Sweep35" not in p["presets"], None),
    ("cam_create_setup", {"models": ["Carrier"], "name": "DemoSetup"}, "ok", None),
    # the REAL stock solid and the REAL fixture bodies - the shop-template selection shape.
    ("cam_edit_setup", {"setup": "DemoSetup", "stock": ["STOCK"],
                        "fixtures": ["ViseBase", "JawL", "JawR"]}, "ok", None),
    # THE ASSOCIATIVE SEAM ON CAMERA: a Joint Origin at the real stock's center becomes the
    # setup WCS; the bound_entities read-back lands in ctx as the receipt's evidence.
    ("joint_create_origin", {"anchor": "bbox_center", "bbox_target": "STOCK:1",
                             "orient_axis": "z", "name": "StockWCS"}, "ok", None),
    ("cam_edit_setup", {"setup": "DemoSetup", "wcs": {"origin": "StockWCS"}}, "ok",
     ("wcs_bound_entities", lambda p: p["wcs_set"]["origin"]["bound_entities"])),
    # a real machine: simulation-ready machines refuse direct assignment (API limitation);
    # machine_strip_simulation is the one working path. Asserted by read-back, not call success.
    ("cam_edit_setup", {"setup": "DemoSetup", "machine": "Haas VF-2",
                        "machine_strip_simulation": True},
     lambda p: p.get("machine_set") == "Haas VF-2", None),
    ("cam_get", {"include": ["machines"], "vendor": "Haas", "machine_type": "milling"},
     lambda p: p.get("machines", {}).get("count", 0) > 0, None),
    # four operations: an explicit FACE selection, an ADAPTIVE with real stock margin to clear,
    # a zero-handle SILHOUETTE, and a DRILL on the hub's patterned bolt circle.
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "face",
                              "tool_scope": "document", "tool_index": 0, "generate": False}, "ok", None),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "adaptive",
                              "tool_scope": "document", "tool_index": 0, "generate": False}, "ok", None),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "contour2d",
                              "tool_scope": "document", "tool_index": 0, "generate": False}, "ok", None),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "drill",
                              "tool_scope": "document", "tool_index": 1, "generate": False}, "ok", None),
    ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}, "ok", None),
    ("find_geometry", {"target": "STOCK", "kind": "planar_face", "nearest_to": [0, 0, -23],
                       "max_results": 1}, "ok", _fg("stock_top")),
    ("cam_select_geometry", lambda c: {"operation": "Face1", "selection": "face",
                                       "handles": [_ctx_get(c, "stock_top", "stock top")],
                                       "generate": False}, "ok", None),
    # zero-handle silhouette: applies against the setup's model (live-verified mechanism).
    ("cam_select_geometry", {"operation": "2D Contour1", "selection": "silhouette",
                             "generate": False}, "ok", None),
    # the drill: the bolt-circle bores selected by handle + diameter filter (3mm +/- 0.1).
    ("find_geometry", {"target": "Carrier", "kind": "cylinder_face", "radius": 1.5,
                       "max_results": 8}, "ok", _fgn("bolt_bores")),
    ("cam_select_geometry", lambda c: {"operation": "Drill1", "selection": "holes",
                                       "handles": _ctx_get(c, "bolt_bores", "bolt-circle bores"),
                                       "min_diameter": 2.9, "max_diameter": 3.1,
                                       "generate": False}, "ok", None),
    ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}}, "ok", None),
    ("cam_reorder", {"entity": "Adaptive1", "position": "before", "reference": "Face1"}, "ok", None),
    ("cam_activate_setup", {"setup": "DemoSetup"}, "ok", None),
    ("cam_compare_operations", {"operation_a": "Face1", "operation_b": "Adaptive1"}, "ok", None),
    # FOLDERS: organize the job the way a shop sheet reads - milling vs drilling.
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Milling"}, "ok", None),
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Drilling"}, "ok", None),
    ("cam_edit_folders", {"action": "move", "setup": "DemoSetup", "folder": "Milling",
                          "operations": ["Face1", "Adaptive1", "2D Contour1"]}, "ok", None),
    ("cam_edit_folders", {"action": "move", "setup": "DemoSetup", "folder": "Drilling",
                          "operations": ["Drill1"]}, "ok", None),
    ("cam_show_toolpath", {"action": "list"}, "ok", None),
    # the validity verdict BEFORE generation: false, with the not-yet-generated ops named; a scoped
    # check resolves through the shared resolver and a bogus scope is refused listing what exists.
    ("cam_inspect_toolpaths", {},
     lambda p: p["passed"] is False and len(p["measured"]["not_valid"]) > 0, None),
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"}, lambda p: p["passed"] is False, None),
    ("cam_inspect_toolpaths", {"scope": "NoSuchScopeXyz"}, "refused", None),
    ("cam_generate", {"target": "DemoSetup", "skip_valid": False}, "ok", None),
    # generation completion is gated by the bounded poll run() performs after this act (an
    # errored op or an EMPTY toolpath - a 'valid' op that cuts nothing - fails the run).
]

# ACT 10b: CAM read-back + deliverables on the generated job - toolpath shown, NC posted,
# template saved and re-applied.
_CAM_DELIVER = [
    # the validity verdict flips true once generation completed (the act boundary's poll certified
    # it); the not-valid breakdown is empty.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"},
     lambda p: p["passed"] is True and p["measured"]["not_valid"] == [], None),
    ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}, "ok", None),
    ("cam_show_toolpath", {"action": "isolate", "operation": "Face1", "fit": True}, "ok", None),
    ("view_screenshot", {"view": "iso-top-right", "width": 500, "height": 400}, "ok", None),
    ("cam_show_toolpath", {"action": "hide_all"}, "ok", None),
    ("cam_post", {"scope": "DemoSetup", "post": "haas", "post_scope": "local",
                  "output_folder": EXPORT_DIR + "/nc", "program_name": "1001"}, "ok", None),
    ("cam_set_nc_comment", {"comment": "GYRO sweep"}, "ok", None),
    ("cam_save_template", {"template_name": "GyroTmpl", "setup": "DemoSetup",
                           "operations": "Face1", "location": "local"}, "ok", None),
    ("cam_create_setup", {"models": ["Carrier"], "name": "Setup2"}, "ok", None),
    ("cam_apply_template", {"setup": "Setup2", "template_name": "GyroTmpl",
                            "location": "local", "generate": "skip"}, "ok", None),
    ("cam_delete", {"entity": "Adaptive1"}, "ok", None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export",
                       "target": "Carrier"}, "ok", None),
]


def poll_generation(rows, notes, setup, max_polls=40):
    """Read cam_get_status until completed (bounded). Generation free-runs in the background and a
    status read returns immediately, so real wall-clock sits between reads. An errored op, an EMPTY
    toolpath (a 'valid' op that cuts nothing - the silent version of wrong), or an exhausted budget
    FAILs."""
    for i in range(max_polls):
        if i:
            time.sleep(5)
        is_error, payload = call("cam_get_status", {"target": setup})
        if is_error:
            rows.append(("cam_get_status", "FAIL", str(payload)[:160]))
            return
        states = payload.get("live_states", {})
        if states.get("errored"):
            rows.append(("cam_get_status", "FAIL",
                         f"{states['errored']} operation(s) errored during generation"))
            return
        if payload.get("completed"):
            empty = payload.get("empty_toolpaths") or []
            if empty:
                rows.append(("cam_get_status", "FAIL",
                             f"empty toolpath(s) - nothing to machine: {', '.join(empty)}"))
            else:
                rows.append(("cam_get_status", "pass",
                             f"{states.get('valid', '?')} valid, non-empty toolpaths"))
                notes["cam_get_status"] = STORY.get("cam_get_status", "")
            return
    rows.append(("cam_get_status", "FAIL", f"not complete after {max_polls} polls"))


# ACT 10a fallback: the scratch-stock milling job (kept whole so the CAM family stays covered
# when the story world could not build).
_CAM = (
    _box("GyroStock", ox=700)
    + [
        _watch("GyroStock:1"),
        ("view_switch_workspace", {"workspace": "manufacture"}, "ok", None),
        ("cam_get", {}, "ok", None),
        ("cam_edit_tools", {"action": "add", "scope": "document", "add_tools": [{"from_type": "flat end mill"}]}, "ok", None),
        ("cam_create_setup", {"models": ["GyroStock"], "name": "Setup1"}, "ok", None),
        # THE ASSOCIATIVE SEAM ON CAMERA: bind the setup's WCS to the StockCenter Joint Origin (ACT 3
        # created it; either path). The row is hard-gated - cam_edit_setup errors when the JO binds
        # zero entities - and the bound_entities read-back lands in ctx as the receipt's evidence.
        ("cam_edit_setup", {"setup": "Setup1", "wcs": {"origin": "StockCenter"}}, "ok",
         ("wcs_bound_entities", lambda p: p["wcs_set"]["origin"]["bound_entities"])),
        # STOCK SIZED FROM PARAMETERS: GimbalDia is read FRESH and the fixed-box stock dims are
        # COMPUTED from it (GimbalDia/4 square, GimbalDia/8 tall - encloses the 20x20x10 stock part).
        # Computed-numbers-from-a-fresh-read is the verifiable shape: the CAM parameter store accepts
        # any expression TEXT unevaluated (a bogus name stores fine), so a CAD-param expression string
        # cannot be trusted to evaluate - a live-probed fact.
        ("param_get", {"name": "GimbalDia"}, "ok",
         ("gimbal_mm", lambda p: p["parameter"]["value"] * 10)),
        ("cam_edit_setup", lambda c: {"setup": "Setup1", "parameters": {
            "job_stockMode": "'fixedbox'",
            "job_stockFixedX": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 4),
            "job_stockFixedY": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 4),
            "job_stockFixedZ": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 8)}},
         "ok", None),
        ("cam_create_operation", {"setup": "Setup1", "strategy": "face", "tool_scope": "document", "tool_index": 0, "generate": False}, "ok", None),
        ("cam_create_operation", {"setup": "Setup1", "strategy": "adaptive", "tool_scope": "document", "tool_index": 0, "generate": False}, "ok", None),
        ("cam_get", {"include": ["operations"], "setup": "Setup1"}, "ok", None),
        ("find_geometry", {"target": "GyroStock", "kind": "planar_face", "nearest_to": [710, 10, 10], "max_results": 1}, "ok", _fg("cam_top")),
        ("cam_select_geometry", lambda c: {"operation": "Face1", "selection": "face", "handles": [_ctx_get(c, "cam_top", "cam top face")], "generate": False}, "ok", None),
        ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}}, "ok", None),
        ("cam_edit_setup", {"setup": "Setup1", "models": ["GyroStock"]}, "ok", None),
        ("cam_edit_folders", {"action": "create", "setup": "Setup1", "name": "Folder1"}, "ok", None),
        ("cam_reorder", {"entity": "Adaptive1", "position": "before", "reference": "Face1"}, "ok", None),
        ("cam_activate_setup", {"setup": "Setup1"}, "ok", None),
        ("cam_compare_operations", {"operation_a": "Face1", "operation_b": "Adaptive1"}, "ok", None),
        ("cam_show_toolpath", {"action": "list"}, "ok", None),
        ("cam_generate", {"target": "Setup1", "skip_valid": False}, "ok", None),
        ("cam_get_status", {"target": "Setup1"}, "ok", None),
    ]
)

# ACT 10b fallback: deliverables on the scratch job.
_CAM_FB_DELIVER = [
    ("cam_post", {"scope": "Setup1", "post": "haas", "post_scope": "local", "output_folder": EXPORT_DIR + "/nc", "program_name": "1001"}, "ok", None),
    ("cam_set_nc_comment", {"comment": "GYRO sweep"}, "ok", None),
    ("cam_save_template", {"template_name": "GyroTmpl", "setup": "Setup1", "operations": "Face1", "location": "local"}, "ok", None),
    ("cam_create_setup", {"models": ["GyroStock"], "name": "Setup2"}, "ok", None),
    ("cam_apply_template", {"setup": "Setup2", "template_name": "GyroTmpl", "location": "local", "generate": "skip"}, "ok", None),
    ("cam_delete", {"entity": "Adaptive1"}, "ok", None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export", "target": "GyroStock"}, "ok", None),
]

# â”€â”€ the ACT program â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# (name, precondition, narrative, fallback). A precondition read that ERRORS routes the act to its
# fallback (its tools are still covered, each marked "(fallback fixture)"). None precondition = an
# opening/cameo act that always runs its narrative.
ACTS = [
    ("ACT 0 - OVERTURE", None, _OVERTURE, None),
    ("ACT 1 - SKELETON + PARAMETERS", None, _SKELETON, None),
    ("ACT 2 - SOLIDS", ("sketch_get", {"sketch_name": "OuterRingSketch"}), _SOLIDS, _SOLIDS_FB),
    ("ACT 3 - MOTION", ("find_geometry", {"target": "OuterRing", "kind": "cylinder_face", "max_results": 1}), _MOTION, _MOTION_FB),
    ("ACT 4 - DETAILS", ("find_geometry", {"target": "OuterRing", "kind": "circular_edge", "max_results": 1}), _DETAILS, _DETAILS_FB),
    ("ACT 5 - MACHINING PREP", None, _MACHINING, None),
    ("ACT 6 - RESIZE", ("sketch_get", {"sketch_name": "OuterRingSketch"}), _RESIZE, _RESIZE_FB),
    ("ACT 7 - MESH", None, _MESH, None),
    # the story's third movement: strip to the machinable part, model the vise around it, and
    # machine the REAL part in the REAL fixture. Each act's precondition routes to a fallback
    # (empty when the act's tools are all covered by earlier acts) so a broken story world still
    # yields a complete per-tool ledger - on the scratch-stock fixtures.
    ("ACT 8 - REDUCE TO THE PART", ("model_inspect", {"target": "Carrier:1"}), _REDUCE, []),
    ("ACT 9 - VISE FIXTURE", ("model_inspect", {"target": "Carrier:1"}), _VISE, []),
    ("ACT 10a - CAM: JOB + GENERATE", ("model_inspect", {"target": "STOCK:1"}), _CAM_STORY, _CAM),
    ("ACT 10b - CAM: DELIVERABLES", ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}), _CAM_DELIVER, _CAM_FB_DELIVER),
    ("FINALE", None, _FINALE, None),
]

# Post-act hooks run() fires after an act completes: the bounded generation poll between the CAM
# job act and its deliverables, and the optional spatial value-check phases. Spatial phases run
# only in narrative mode (the fallback world has no gyroscope or vise to check).
POLL_AFTER = {
    "ACT 10a - CAM: JOB + GENERATE": {"narrative": "DemoSetup", "fallback": "Setup1"},
}
SPATIAL_AFTER = {
    "ACT 6 - RESIZE": ("SPATIAL (gyroscope)", GYRO_CHECKS),
    "ACT 9 - VISE FIXTURE": ("SPATIAL (fixture grip)", FIXTURE_CHECKS),
}

# STEPS: the flat union of every act's narrative + fallback steps - the coverage ledger the
# completeness lint reads (every registered tool must appear as some step's tool). run() iterates
# ACTS (choosing narrative or fallback per act); STEPS exists so the lint sees the whole surface.
STEPS = [s for _, _, narr, fb in ACTS for s in (list(narr) + list(fb or []))]

# STORY: each covered tool's ledger shot-list note - the receipt doubles as the demo's shot list.
STORY = {
    "doc_new": "open the one document the whole gyroscope lives in",
    "workspace_orient": "orient: read the empty design before building",
    "sys_capability_map": "survey the server's tool families at cold start",
    "sys_find_tool": "search the surface for the revolve verb",
    "sys_get_api_doc": "read the RevolveFeatures API doc",
    "view_list_workspaces": "list the workspaces available",
    "view_set": "orient the camera to the iso hero angle",
    "sys_get_selection": "expected refusal: nothing is selected yet",
    "param_add": "add GimbalDia and the derived ring/rotor radii",
    "param_set_favorite": "mark GimbalDia the favorite driving dimension",
    "param_get": "read the parameter table; a fresh GimbalDia read sizes the CAM stock",
    "model_create_component": "cast the eight parts, Pedestal nested in Frame",
    "design_activate_component": "step into each part to build its sketch",
    "sketch_create": "draw each part's sketch on its plane",
    "sketch_add_geometry": "draw the concentric rings and part footprints",
    "sketch_add_3d_line": "draw the yaw axis as the skeleton's 3D line",
    "sketch_constrain": "constrain the skeleton's X axis horizontal",
    "sketch_dimension": "drive ring/rotor radii by parameter expression",
    "sketch_get": "read the skeleton and ring profiles back",
    "sketch_delete_entity": "delete a helper constraint; count drops",
    "model_construction": "offset the carrier hub plane below the rotor sweep",
    "sketch_set_text": "engrave the FUSION ESSENTIALS nameplate",
    "model_extrude": "extrude the ring bands symmetric about the ring plane",
    "model_revolve": "revolve the rotor disc about the spin axis",
    "model_loft": "loft the pedestal base-to-post transition",
    "model_sweep": "sweep the crank handle along its path",
    "model_draft": "draft a cameo face",
    "model_mirror": "mirror a cameo body",
    "model_pattern_rectangular": "rectangular-pattern a cameo body",
    "model_pattern_circular": "circular-pattern a cameo body",
    "model_hole": "drill a cameo mounting hole",
    "model_combine": "join two overlapping cameo pads",
    "appearance_set": "give each gyroscope part its own color",
    "model_set_material": "assign the rotor a physical steel material",
    "find_geometry": "acquire the face/edge/body handles the build consumes",
    "model_measure_between": "measure the outer-ring-to-inner-ring gap",
    "model_measure_relation": "read rotor/shaft coaxiality",
    "model_inspect": "read the rotor's volume back",
    "pmi_create": "author a flatness note on the frame plate and a hole note on a carrier bore",
    "pmi_get": "read the PMI back with segments and detail",
    "pmi_edit": "restate the flatness note's markup, then hide and show it",
    "pmi_delete": "delete the flatness note; the count read-back confirms it",
    "assembly_ground": "ground the frame so the mechanism has a base",
    "assembly_rigid_group": "rigid-group the frame and carrier base",
    "joint_create_origin": "place the crank mount and the stock-center WCS",
    "joint_create": "revolute the yaw, ring pivots, spin, and crank",
    "joint_at_geometry": "joint a pin in its bore via cylinder faces",
    "joint_create_as_built": "seat the rotor shaft in the inner ring as-built",
    "joint_edit": "set rotation limits on the yaw",
    "joint_motion_link": "couple the crank to the rotor spin at 2:1; the vise jaws at -1 (self-centering)",
    "joint_drive": "drive every axis, the crank -> rotor 2:1, then ONE vise jaw (the link closes the other)",
    "assembly_get": "read the joint wiring, driven angles, and the StockCenter anchor back",
    "assembly_move": "pose a scratch cameo occurrence",
    "assembly_capture_position": "capture the posed snapshot",
    "assembly_constrain": "flush-constrain a scratch cameo pair",
    "assembly_inspect_interference": "check interference at rest and driven",
    "design_recompute": "recompute the assembly after motion",
    "model_fillet": "fillet the outer ring edge",
    "model_chamfer": "chamfer the frame edge",
    "model_shell": "shell a scratch cap cameo",
    "model_offset_face": "push a scratch block's top face outward",
    "model_scale": ("uniform x8 and per-axis x*y*z scales with ratio read-backs; unresolvable, "
                    "length, and angle expressions refused; a bare unitless parameter accepted; "
                    "a vertex-anchored scale"),
    "design_delete_feature": "add a wart feature then delete it; health diff",
    "design_delete_occurrence": "delete a scratch occurrence",
    "view_section": "section cut through the gimbal center",
    "view_screenshot": "capture the sectioned mechanism",
    "view_screenshot_multi": "capture the front and top beauty shots",
    "surface_revolve": "revolve a prep sheet",
    "surface_thicken": "thicken the prep sheet",
    "surface_extrude": "extrude prep sheets",
    "surface_offset": "offset a ring face zero and nonzero",
    "surface_extend": "extend a sheet edge",
    "surface_reverse_normal": "flip a sheet normal",
    "surface_delete_face": "open a bore by deleting a face",
    "surface_patch": "close the opened bore with a patch",
    "sketch_project": "project the machining boundary",
    "surface_trim": "trim a sheet with a cylinder cutter",
    "surface_untrim": "untrim the internal hole loop",
    "model_split": "split a scratch pin by a plane",
    "model_unstitch": "unstitch a scratch box's faces",
    "model_stitch": "re-stitch two faces",
    "model_base_feature": "open and close a base-feature scope",
    "model_arrange": "nest two scratch parts in a boundary",
    "model_compute_holder": "compute a CAM tool holder (read)",
    "save_as_mesh": "mesh a scratch solid (one per destructive op)",
    "mesh_get": "read the mesh back",
    "mesh_generate_face_groups": "group the mesh faces",
    "mesh_to_brep": "convert a mesh to a base-feature BRep",
    "mesh_reduce": "reduce a dense mesh",
    "mesh_remesh": "remesh a copy",
    "mesh_plane_cut": "plane-cut a mesh copy",
    "mesh_combine": "combine two mesh copies",
    "mesh_delete": "delete a scratch mesh body with the design-wide survivor re-scan",
    "mesh_export": "export a mesh to STL",
    "mesh_insert": "re-import the STL mesh",
    "param_set": "bump GimbalDia +33%, then restore it",
    "param_delete": "delete a scratch parameter",
    "view_switch_workspace": "switch to Manufacture, then back to Design",
    "cam_get": "read the CAM job structure",
    "cam_edit_tools": ("add mill/drill/turning/center-drill tools; preset add/remove round-trip "
                       "with unit, refusal, and rollback gates"),
    "cam_create_setup": "create the milling setup on the Carrier in the vise",
    "cam_create_operation": "create the face, adaptive, silhouette, and drill operations",
    "cam_select_geometry": "select stock-top face, zero-handle silhouette, and the bolt-circle holes",
    "cam_edit_operation": "edit the face operation's feed",
    "cam_edit_setup": "real stock + vise fixture bodies; WCS bound to the stock-center JO (bound read back); Haas VF-2 assigned",
    "cam_edit_folders": "organize the job into Milling and Drilling folders",
    "cam_reorder": "reorder the adaptive before the face op",
    "cam_activate_setup": "activate the setup",
    "cam_compare_operations": "compare the two operations",
    "cam_show_toolpath": "leave the toolpath visible on camera",
    "cam_generate": "generate the toolpaths against the real part in the real fixture",
    "cam_inspect_toolpaths": ("verdict false with named ops before generation, scoped check, "
                              "bogus-scope refusal, verdict true after generation"),
    "cam_get_status": "poll the generation to completion (empty toolpaths fail)",
    "cam_post": "post the NC program to disk",
    "cam_set_nc_comment": "stamp the NC program comment",
    "cam_save_template": "save the setup as a local CAM template",
    "cam_apply_template": "apply the template to a second setup",
    "cam_delete": "delete a scratch operation; count diff",
    "design_export": "export the machined part to STEP",
    "design_get": "final design read: the whole cast",
    "doc_get": "read the document identity before discarding",
    "doc_close": "discard the document on camera - clean teardown",
}

# Tools deliberately not swept unattended, each with its reason (the ledger's skipped rows). This is
# the policy-excluded bucket; PENDING (below) is the separate "not scripted yet" bucket - the ledger
# keeps that distinction honest.
EXCLUDED = {
    "sys_execute_script": "gated off by design; the sweep proves the typed surface suffices",
    "sys_reload_addin": "restarts the server mid-sweep",
    "sys_request_selection": "waits on a human pick (user-present tier)",
    "drawing_create": "first-open modal needs a human review (user-present tier)",
    "drawing_update": "user-present tier (drawing docs)",
    "drawing_export": "user-present tier (drawing docs)",
    "design_set_mode": "irreversible parametric->direct conversion; not run unattended",
    "design_configure": "configuration table needs a SAVED document (a DataFile to carry it); opt-in tier",
    # cloud tier: writes to the operator's real hub - opt-in only, never in the default sweep.
    "data_create_project": "cloud write to the operator's real hub (opt-in tier)",
    "data_create_folder": "cloud write (opt-in tier)",
    "data_upload_file": "cloud write (opt-in tier)",
    "data_get": "cloud read, hub-dependent (opt-in tier)",
    "data_get_upload_status": "cloud read (opt-in tier)",
    "data_delete_file": "cloud destructive (opt-in tier)",
    "data_delete_folder": "cloud destructive (opt-in tier)",
    "data_switch_hub": "changes the active hub, closes docs (opt-in tier)",
    "doc_save": "versions to the cloud; needs a saved doc (opt-in tier)",
    "doc_save_as": "cloud write (opt-in tier)",
    "doc_copy": "cloud write (opt-in tier)",
    "doc_open": "opens cloud files; can wedge on CAM templates (opt-in tier)",
    "doc_insert_occurrence": "needs a saved cloud source in-project (opt-in tier)",
    "doc_insert_derive": "needs an ALREADY-OPEN saved cloud source to derive from (opt-in tier)",
    "doc_restore_version": "needs cloud version history (opt-in tier)",
    "doc_update_xref": "needs cloud external references (opt-in tier)",
    "doc_activate": "needs a second open document (opt-in tier)",
}

# Registered tools NOT yet scripted into STEPS - the honest "todo" ledger. SHRINK-ONLY: scripting a
# tool moves it out of here into STEPS. test_tool_verify_complete.py enforces that every
# registered tool is covered, excluded, or listed here, so a NEWLY added tool can't decay coverage
# silently - it fails the gate until someone scripts it, excuses it, or adds it here deliberately.
PENDING = frozenset()


def source_hash(root=None):
    """SHA-256 over every .py under commands/mcpServer/ - the receipt key binding a green run
    to the exact tool source it exercised. Relative paths are normalized to '/' and CRLF to LF
    so the digest is identical across OS and git line-ending config; __pycache__ is skipped."""
    root = root or SRC_ROOT
    rels = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                rels.append(rel.replace(os.sep, "/"))
    hasher = hashlib.sha256()
    for rel in sorted(rels):
        with open(os.path.join(root, rel.replace("/", os.sep)), "rb") as fh:
            content = fh.read().replace(b"\r\n", b"\n")
        hasher.update(rel.encode("utf-8") + b"\0" + content + b"\0")
    return hasher.hexdigest()


_STAMP_RE = re.compile(r"^Stamp: source ([0-9a-f]{64}) \| Fusion (\S+) \| verified (\S+)",
                       re.MULTILINE)


def write_verified(ledger, fusion_version, stamp_date, src_hash, path=None, notes=None):
    """Write the tracked receipt. Called only on a run with zero FAIL/blocked steps. 'notes' maps a
    covered tool to its shot-list step text (the ledger doubles as the demo's shot list) - a third
    column, empty when absent so the two-column stamp/count contract is unchanged."""
    notes = notes or {}
    n_cov = sum(1 for _, s in ledger if s == "covered")
    n_pend = sum(1 for _, s in ledger if s.startswith("PENDING"))
    n_skip = len(ledger) - n_cov - n_pend
    lines = [
        "# Live tool verification (generated by tool_verify.py - do not edit)",
        "",
        "This is a THREE-BUCKET ledger, not a clean bill of health. It does NOT claim every tool",
        "is verified - the count line below is authoritative, and the per-tool table says which",
        "bucket each tool is in:",
        "",
        "- covered: a live step drove the tool this run and its effect was read back.",
        "- skipped(reason): deliberately NOT driven unattended (cloud / interactive / irreversible",
        "  tier), each row naming why. Not verified - excused.",
        "- pending: no step drives it yet. UNVERIFIED, not known-good - it has never run in this",
        "  sweep. Shrinking this bucket means scripting a real step, not relabelling it.",
        "",
        "The stamp's source hash binds this run to the exact `commands/mcpServer/` tree it",
        "exercised: `--check` recomputes the hash and fails on any difference, so a green suite",
        "cannot ride on a live run that never saw the current code. Only a run with zero",
        "FAIL/blocked steps rewrites this file.",
        "",
        "Stamp: source {0} | Fusion {1} | verified {2}".format(src_hash, fusion_version, stamp_date),
        "",
        "{0} covered / {1} skipped(reason) / {2} pending".format(n_cov, n_skip, n_pend),
        "",
        "| tool | status | step (the demo's shot list) |",
        "|---|---|---|",
    ]
    for tool, status in ledger:
        lines.append("| {0} | {1} | {2} |".format(
            tool, status.replace("|", "/"), notes.get(tool, "").replace("|", "/")))
    with open(path or VERIFIED, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path or VERIFIED


def check(root=None, verified_path=None):
    """The receipt gate: drives nothing, needs no Fusion. Exit 0 = the last green live run saw
    exactly this tool source; 1 = no receipt, or the source changed since that run."""
    path = verified_path or VERIFIED
    if not os.path.exists(path):
        print("VERIFIED_TOOLS.md does not exist - run tool_verify.py once against live Fusion.")
        return 1
    with open(path, encoding="utf-8") as fh:
        m = _STAMP_RE.search(fh.read())
    if not m:
        print("VERIFIED_TOOLS.md has no stamp line - regenerate it (run tool_verify.py).")
        return 1
    stamped_hash, stamped_version, stamped_date = m.groups()
    current = source_hash(root)
    if current != stamped_hash:
        print("tool source changed since the last live verification ({0}, Fusion {1}) -"
              .format(stamped_date, stamped_version))
        print("re-run with Fusion up: py -3 tests/live/tool_verify.py")
        return 1
    print("live verification current: source matches the green run of {0} (Fusion {1})"
          .format(stamped_date, stamped_version))
    return 0


def _precondition_holds(pre):
    """Run an act's precondition READ; True when it returns without error (the geometry the act's
    narrative consumes exists). A False routes the act to its scratch fallback."""
    tool, args = pre
    is_error, _ = call(tool, args)
    return not is_error


def run(write_json, keep_open=False):
    health = health_gate()
    print(f"server ok: {health.get('server')} v{health.get('version', '?')}")
    all_tools = registered_tools()

    ctx, rows, notes, act_modes = {}, [], {}, []
    for name, pre, narrative, fallback in ACTS:
        mode, steps = "narrative", narrative
        if pre is not None and fallback is not None and not _precondition_holds(pre):
            mode, steps = "fallback", fallback
        act_modes.append((name, mode))
        print(f"\n-- {name} [{mode}] --")
        if keep_open and name == "FINALE":
            steps = [s for s in steps if s[0] != "doc_close"]
        for tool, args, expect, save in steps:
            try:
                arguments = args(ctx) if callable(args) else dict(args)
            except KeyError as e:
                rows.append((tool, "blocked", str(e)))
                continue
            is_error, payload = call(tool, arguments)
            if callable(expect):
                # a VALUE PREDICATE on an ok result: call success is not enough - the payload
                # must satisfy the check (grip contact, machine assignment, rest-pose honesty).
                if is_error:
                    status, note = "FAIL", str(payload)[:160]
                else:
                    try:
                        good = bool(expect(payload))
                    except Exception as e:
                        good, payload = False, f"predicate raised: {e}"
                    status, note = ("pass", "") if good else ("FAIL", str(payload)[:160])
            elif expect == "ok" and not is_error:
                status, note = "pass", ""
            elif expect == "refused" and is_error:
                status, note = "expected-refusal", str(payload)[:80]
            else:
                status, note = "FAIL", str(payload)[:160]
            if status == "pass" and save is not None and not is_error:
                key, extract = save
                try:
                    ctx[key] = extract(payload)
                except Exception as e:
                    status, note = "pass*", f"saved-value extraction failed: {e}"
            rows.append((tool, status, note))
            if status in ("pass", "pass*", "expected-refusal"):
                story = STORY.get(tool, "")
                notes[tool] = (story + " (fallback fixture)").strip() if mode == "fallback" else story
            time.sleep(0.1)
        if name in POLL_AFTER:
            poll_generation(rows, notes, POLL_AFTER[name][mode])
        if mode == "narrative" and name in SPATIAL_AFTER:
            phase_name, checks = SPATIAL_AFTER[name]
            spatial_phase(phase_name, checks, rows)
    if keep_open:
        print("\n--keep-open: the story document is left open for inspection.")

    covered = {t for t, s, _ in rows if s in ("pass", "pass*", "expected-refusal")}
    ledger = []
    for tool in all_tools:
        if tool in covered:
            ledger.append((tool, "covered"))
        elif tool in EXCLUDED:
            ledger.append((tool, f"skipped: {EXCLUDED[tool]}"))
        else:
            ledger.append((tool, "PENDING (no step yet)"))

    print(f"\n== step results ({len(rows)}):")
    for tool, status, note in rows:
        print(f"  {status:18} {tool:28} {note}")
    n_cov = sum(1 for _, s in ledger if s == "covered")
    n_pend = sum(1 for _, s in ledger if s.startswith("PENDING"))
    n_skip = len(ledger) - n_cov - n_pend
    print(f"\n== ledger: {n_cov}/{len(ledger)} covered, {n_skip} skipped(reason), {n_pend} pending")
    for tool, s in ledger:
        if s != "covered":
            print(f"  {tool:32} {s}")

    n_narr = sum(1 for _, m in act_modes if m == "narrative")
    n_fb = sum(1 for _, m in act_modes if m == "fallback")
    print(f"\n== acts: {n_narr} narrative / {n_fb} fallback (of {len(act_modes)})")
    for nm, m in act_modes:
        print(f"  {m:10} {nm}")

    fails = [r for r in rows if r[1] in ("FAIL", "blocked")]
    if fails:
        print("\nVERIFIED_TOOLS.md NOT rewritten - resolve the FAIL/blocked steps first.")
    else:
        src_hash = source_hash()
        stamp_date = time.strftime("%Y-%m-%d")
        fusion_version = ctx.get("fusion_version", "?")
        print("\nwrote {0} (stamp: source {1}..., Fusion {2}, {3})".format(
            write_verified(ledger, fusion_version, stamp_date, src_hash, notes=notes),
            src_hash[:12], fusion_version, stamp_date))
    if write_json:
        results_dir = os.path.join(_HERE, "results")
        os.makedirs(results_dir, exist_ok=True)
        path = os.path.join(results_dir, f"verify-{time.strftime('%Y%m%d-%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"steps": rows, "ledger": ledger, "acts": act_modes, "server": health}, fh, indent=2)
        print(f"\nwrote {path}")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep-open", action="store_true",
                    help="leave the story document open at the end instead of discarding it")
    args = ap.parse_args()
    sys.exit(check() if args.check else run(args.json, keep_open=args.keep_open))
