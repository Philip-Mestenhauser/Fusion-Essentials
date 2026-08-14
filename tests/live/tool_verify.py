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
fails the run), NC posted. Cameo fixtures for families with no home on the mechanism ride the
same document.

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
guard check whose error must name the offense), ``_refused("fragment", ...)`` - the same
deliberate refusal, with each fragment required IN the error text (a guard that starts refusing
for a different reason is a FAIL, not a silent pass) - or a callable(payload) -> bool - a VALUE
PREDICATE run on an ok result (falsy = FAIL); that is how grip contact and machine assignment
are asserted, not just call success. Extend coverage by adding rows, not code.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

BASE = "http://127.0.0.1:27182"
MCP = BASE + "/mcp"
SERVER_NAME = "Fusion-Essentials MCP Server"
DOC_PREFIX = "EVAL_sweep"

# The machine cam_create_machine stores lives in the LOCAL machine library, outside the document the
# sweep discards - and this server has no tool that removes a machine, so a fixed name would be
# refused as a duplicate by every run after the first. One run stamp names it. Each run therefore
# leaves one 'SweepMach3Axis <stamp>' (vendor SweepCo) machine behind, and that residue is the price
# of the beat.
MACHINE_NAME = "SweepMach3Axis " + time.strftime("%Y%m%d-%H%M%S")

# How much of a failing step's payload the ledger keeps. A FAIL row is read to DIAGNOSE, and the
# keys that carry the diagnosis (a measured extent, a change list) sit late in a payload - at 160
# characters they were cut off, which costs a whole live re-run to recover. A passing refusal is
# read only as confirmation, so its note stays short.
NOTE_MAX = 480
REFUSAL_NOTE_MAX = 80

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


class _Refusal:
    """expect=_refused("...", ...) - a deliberate refusal whose MESSAGE must carry every fragment.

    A bare "refused" passes on ANY error, so a guard that starts refusing for a different reason
    (or a call that fails upstream of the guard) still reads green. Where the refusal's own words
    are the measured fact - the build the platform refuses on, the offending value it names - the
    fragments are what make the row assert it. Substring match, ASCII as it crosses the wire."""

    def __init__(self, fragments):
        self.fragments = fragments

    def missing(self, text):
        return [f for f in self.fragments if f not in text]


def _refused(*fragments):
    return _Refusal(fragments)


# A portable scratch dir for the export-to-disk tools (design_export/mesh_export/cam_post) so the
# sweep writes NC/CAD/mesh files somewhere writable on any machine, not a session-specific path.
EXPORT_DIR = os.path.join(tempfile.gettempdir(), "eval_sweep_exports").replace("\\", "/")
os.makedirs(EXPORT_DIR, exist_ok=True)

# The one fixture the harness AUTHORS rather than builds through a tool: no tool writes an SVG and
# there is no SVG exporter to round-trip through the way mesh_export -> mesh_insert does. 36 x 16 SVG
# user units - importSVG ignores the file's width/height and viewBox, so at scale=3.7795 (1 user unit
# = 1 mm) this art lands about 36 x 16 mm, which is the band the insert beat measures.
SVG_PATH = EXPORT_DIR + "/eval_logo.svg"
with open(SVG_PATH, "w", encoding="utf-8") as _svg_fixture:
    _svg_fixture.write('<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="20mm" '
                       'viewBox="0 0 40 20"><rect x="2" y="2" width="36" height="16"/></svg>')

# The SK-5 closure fixture: a 96-user-unit square at the SVG origin. At 1/96 inch per user unit and
# scale=1 that is exactly one inch, and the art lands y-DOWN from the sketch origin - so the sketch's
# measured min y is -25.4 mm and nothing else can produce that number. ([F30]/[F51c] measured the raw
# entry point; this fixture carries the same landing through the TOOL.)
# NO width/height/viewBox: the probe those facts came from carried none, and a square that exactly
# FILLS a viewBox is the one shape that cannot tell a top-left anchor from a bottom-left one - so
# stating them here would make the fixture disagree with the measurement it exists to close.
SVG96_PATH = EXPORT_DIR + "/eval_square96.svg"
with open(SVG96_PATH, "w", encoding="utf-8") as _svg96_fixture:
    _svg96_fixture.write('<svg xmlns="http://www.w3.org/2000/svg">'
                         '<rect x="0" y="0" width="96" height="96"/></svg>')


# save-extractors: pull a handle/profile off a step's payload into ctx for a later args-callable.
def _fg(key):
    return (key, lambda p: p["matches"][0]["handle"])          # find_geometry -> first handle


def _fgn(key):
    return (key, lambda p: [m["handle"] for m in p["matches"]])  # find_geometry -> all handles


def _prof(key):
    return (key, lambda p: p["profiles"][0]["handle"])          # sketch_get -> first profile handle


# build_path's published label - "N edge(s) from 1 seed handle" / "N edge(s) from K handles, used
# exactly". N is read off the BUILT adsk Path, so it is the only witness to what was actually swept.
_PATH_LABEL = re.compile(r"^(\d+) edge\(s\) from (\d+) (?:seed handle|handles, used exactly)$")


def _path_count(label, seeds):
    """The built edge count off a path label, or -1 when the label is not that shape or names a
    different seed count - so a beat asserting the number also asserts the wording it came out of."""
    m = _PATH_LABEL.match(str(label or ""))
    if not m or int(m.group(2)) != seeds:
        return -1
    return int(m.group(1))


# A predicate that RAISES names the numbers it read, and run_steps puts that short sentence in the
# ledger instead of the payload - which truncates at 160 characters, well before a measured extent
# or a change list. Use this shape where a miss has to be diagnosable from the ledger alone.
def _measured(label, got, ok_):
    if not ok_:
        raise AssertionError(f"{label}: measured {got}")
    return True


# The band the one-inch square is measured against. The nominal is exactly 25.4 mm, but the sketch's
# bounding box spans the imported PAINT, so it carries half the rect's stroke on each side plus the
# importer's own rounding - measured live at min.y -25.41 / height 25.42, a hundredth or two over.
# The band is wide enough to absorb that and far too narrow to admit any other unit reading.
_SVG96_MM = 25.4
_SVG96_TOL = 0.05


def _svg96_extent(p):
    """The 96-user-unit square at scale 1: one inch square, landing Y-DOWN from the sketch origin
    ([F30]/[F51c] measured the raw entry point at y [-2.54 cm, 0]; the live sweep confirms the sign
    and the size through the tool)."""
    e = p.get("sketch_extent") or {}
    got = {"min": e.get("min"), "width": e.get("width"), "height": e.get("height"),
           "units": e.get("units")}
    y = (e.get("min") or {}).get("y")
    return _measured(f"svg96 extent (want min.y {-_SVG96_MM}, height {_SVG96_MM} mm "
                     f"+/-{_SVG96_TOL})", got,
                     y is not None and abs(y + _SVG96_MM) < _SVG96_TOL
                     and e.get("height") is not None
                     and abs(e["height"] - _SVG96_MM) < _SVG96_TOL)


def _repair_no_op(p):
    """A repair that found nothing of its kind: 'changed' empty AND the note saying so."""
    return _measured("stitch_and_remove was expected to be a no-op the second time",
                     {"changed": p.get("changed"), "note": (p.get("note") or "")[:60]},
                     p.get("repaired") is True and p.get("changed") == []
                     and "found nothing of its kind to fix" in (p.get("note") or ""))


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
    # the read stamp is for DOCUMENT reads: a tool that answers off the registry rather than the
    # active design carries no 'active_document' key at all (design_get's own beat in the FINALE is
    # the other half of this pair).
    ("sys_find_tool", {"query": "revolve"},
     lambda p: "active_document" not in p and p.get("tool_count", 0) > 0, None),
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
    # PREFERENCES: read the application's own configuration, round-trip ONE invisible member, put it
    # back. The sweep leaves the application exactly as it found it, so the restore is an ASSERTION
    # (previous/now inside its own payload), not cleanup - it runs whether or not the bump asserted.
    # recoverSaveScanFrequency is the round-trip member: integer-exact, invisible to a watching
    # operator, and it perturbs no other beat's formatting.
    ("sys_get_preferences", {},
     lambda p: (p["preferences"]["display"]["generalPrecision"]["value"] is not None
                and p["preferences"]["general"]["isAutomaticVersioningEnabled"]["tier"] == "W"
                and p["preferences"]["products"]["Design"]["isFirstComponentGroundToParent"]["value"]
                is not None), None),
    # the enum FAMILY names are string literals inside the member table, so a typo degrades to a bare
    # int that no offline test can see - only a live decode of two known members catches it.
    ("sys_get_preferences", {"include": ["display", "general"]},
     lambda p: (p["preferences"]["display"]["materialDisplayUnit"].get("enum")
                == "MetricStandardDisplayUnits"
                and p["preferences"]["general"]["defaultModelingOrientation"].get("enum")
                == "ZUpModelingOrientation"), None),
    ("sys_get_preferences", {"include": ["compatibility"]},
     lambda p: p["preferences"]["compatibility"]["recoverSaveScanFrequency"]["value"] > 0,
     ("pref_scan",
      lambda p: p["preferences"]["compatibility"]["recoverSaveScanFrequency"]["value"])),
    # the members that RAISE on read are published as null + named in 'unreadable', never dropped -
    # all THREE of the raising members the [F21] census found on this build, and none of them
    # miscategorised as a member the build does not carry ('unknown_members' must be absent).
    ("sys_get_preferences", {"include": ["graphics"]},
     lambda p: ("graphicsPreset" in p["preferences"]["graphics"]
                and set(p.get("unreadable") or []) >= {"graphics.autoThrottleEffects",
                                                       "graphics.degradedSelectionDisplayStyle",
                                                       "graphics.isLimitEffectsDuringNavigation"}
                and "unknown_members" not in p), None),
    ("sys_set_preferences", lambda c: {"member": "compatibility.recoverSaveScanFrequency",
                                       "value": _ctx_get(c, "pref_scan", "the scan frequency") + 1},
     lambda p: p["now"] == p["previous"] + 1, None),
    ("sys_set_preferences", lambda c: {"member": "compatibility.recoverSaveScanFrequency",
                                       "value": _ctx_get(c, "pref_scan", "the scan frequency")},
     lambda p: p["now"] == p["previous"] - 1, None),   # RESTORED - asserted, not cleanup
    # the documented "greater than 0" bound is refused BEFORE the assignment, so nothing is written
    # and there is nothing to restore.
    ("sys_set_preferences", {"member": "compatibility.recoverSaveScanFrequency", "value": 0},
     "refused", None),
    # a tier-R member names the member and the reason, with nothing written.
    ("sys_set_preferences", {"member": "network.proxyHost", "value": "127.0.0.1"}, "refused", None),
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
    # 'curves_added' is the LINE collection's own delta, so a single line reads 1 - the floor the
    # composite kinds (rectangle 4, polygon 6, slot 3) are counted against.
    ("sketch_add_geometry", {"kind": "line", "x1": -70, "y1": 0, "x2": 70, "y2": 0,
                             "sketch_name": "Skeleton", "is_construction": True},
     lambda p: p.get("curves_added") == 1, None),
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
    # a rectangle is built BY a SketchLines factory, so its four sides are the delta counted.
    ("sketch_add_geometry", {"kind": "rectangle", "x1": -50, "y1": -8, "x2": 50, "y2": 8, "sketch_name": "CarrierSketch"},
     lambda p: p.get("curves_added") == 4, None),
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
    # symmetric extrudes 'distance' EACH WAY, so this is a 56 mm shaft reaching |x| = 28. It must
    # stop SHORT of InnerBoreR (30): a 3 mm-radius shaft ending exactly on the bore reaches
    # sqrt(30^2 + 3^2) at its corners and bites 2.1 mm3 into the inner ring - a journal that
    # interferes with the ring it is meant to turn inside.
    ("model_extrude", {"sketch_name": "ShaftSketch", "profile_index": 0, "distance": 28, "symmetric": True}, "ok", None),
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
    # PMI: authoring is EXTENSION-GATED on this build - pmi_create raises "Manufacturing or Design
    # Extension is required" on a session without one, so the two create beats assert THAT refusal
    # (the deterministic live behavior) on the real geometry a note would carry: the frame's flat
    # plate face and a carrier bolt-circle bore. The read still runs for real, and with no PMI
    # authored the edit/delete beats assert the honest name lookup instead of a fixture - it lists
    # what IS available ("none") rather than acting on something else.
    ("find_geometry", {"target": "Frame", "kind": "planar_face", "max_results": 1}, "ok", _fg("frame_flat")),
    ("pmi_create", lambda c: {"kind": "note", "geometry": [_ctx_get(c, "frame_flat", "frame flat face")], "text": "{flatness}0.05", "name": "PmiFlat"}, "refused", None),
    ("find_geometry", {"target": "Carrier", "kind": "cylinder_face", "radius": 1.5, "max_results": 1}, "ok", _fg("carrier_bore")),
    ("pmi_create", lambda c: {"kind": "hole_note", "geometry": [_ctx_get(c, "carrier_bore", "carrier bolt-circle bore")]}, "refused", None),
    ("pmi_get", {"include": ["segments", "detail"]}, "ok", None),
    # an over-cap 'max_results' is CLAMPED, not refused - pmi_get's own contract, since every record
    # it returns crosses the wire whole. The answer still comes back with its census keys.
    ("pmi_get", {"max_results": 99999},
     lambda p: isinstance(p.get("annotations"), list) and "total" in p, None),
    ("pmi_edit", {"action": "set_text", "annotation": "PmiFlat", "text": "{perpendicularity}0.03"}, "refused", None),
    # the blank name is its own guard, ahead of any lookup.
    ("pmi_edit", {"action": "hide", "annotation": ""}, "refused", None),
    ("pmi_delete", {"annotation": "PmiFlat"}, "refused", None),
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
    # the three additive placement modes. Each act re-acquires its own edge: the center act below
    # consumes the 4 mm rim by drilling an 8 mm bore concentric with it, so a handle captured once
    # and reused would be pointing at geometry that no longer exists.
    ("find_geometry", {"target": "FeatureCameo", "kind": "circular_edge", "radius": 2,
                       "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_hole_edge")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "center", "edge": _ctx_get(c, "fc_hole_edge", "hole rim"),
                              "diameter": "8 mm", "extent": "blind", "depth": "3 mm"},
     lambda p: p.get("placement") == "center" and p.get("holes_verified") is True, None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "nearest_to": [220, 0, 20],
                       "max_results": 1}, "ok", _fg("fc_edge")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "on_edge", "edge": _ctx_get(c, "fc_edge", "pad edge"),
                              "edge_position": "middle", "diameter": "3 mm",
                              "extent": "blind", "depth": "3 mm"},
     lambda p: p.get("placement") == "on_edge", None),
    # on_edge at the edge's START vertex - RE-ACQUIRED first: the middle-hole above SPLITS the
    # pad edge (measured: a handle captured before that hole resolves to 2 sub-edges and is
    # refused as stale), so every act re-acquires its own edge.
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "nearest_to": [220, 0, 20],
                       "max_results": 1}, "ok", _fg("fc_edge2")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "on_edge", "edge": _ctx_get(c, "fc_edge2", "pad edge"),
                              "edge_position": "start", "diameter": "3 mm",
                              "extent": "blind", "depth": "3 mm"}, "ok", None),
    # plane_offsets measures from STRAIGHT edges - a circular one is refused by name
    ("find_geometry", {"target": "FeatureCameo", "kind": "circular_edge",
                       "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_rim2")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "plane_offsets", "point": [215, 15, 20],
                              "offset_edge_one": _ctx_get(c, "fc_rim2", "a round rim"),
                              "offset_one": "5 mm", "diameter": "3 mm", "extent": "blind",
                              "depth": "3 mm"}, "refused", None),
    # re-acquired again: the start-vertex hole above can notch this edge the same way. The query
    # aims at the LONG remaining stretch of the pad's bottom boundary (x~232) - the notch cuts
    # mint short edges near the hole sites that are NOT parallel to the hole plane, and Fusion
    # refuses a non-parallel reference edge (measured).
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "nearest_to": [232, 0, 20],
                       "max_results": 1}, "ok", _fg("fc_edge3")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "plane_offsets", "point": [215, 15, 20],
                              "offset_edge_one": _ctx_get(c, "fc_edge3", "pad edge"),
                              "offset_one": "6 mm", "diameter": "3 mm", "extent": "blind",
                              "depth": "3 mm"},
     lambda p: p.get("placement") == "plane_offsets", None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_body")),
    ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "plane": "yz"}, "ok", None),
    ("model_pattern_rectangular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "quantity_one": 2, "spacing_one": 60, "direction_one": "y"}, "ok", None),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "quantity": 3, "total_angle_deg": 360, "axis": "z"}, "ok", None),
    # pattern the cameo along one of its own line edges - the count is a read-back, never an echo.
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "max_results": 1}, "ok", _fg("dc_edge")),
    ("model_pattern_path", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "path": [_ctx_get(c, "dc_edge", "path edge")], "quantity": 3, "distance": 12, "distance_type": "spacing"},
     lambda p: p.get("patterned") is True and p.get("quantity") == 3, None),
    # the pattern axis as a DATUM: the cameo's own bore defines a construction axis, whose published
    # handle is what the pattern turns about. The axis label is read back off the resolved entity, so
    # the datum's name proves the handle reached the datum and not a world axis fallback.
    ("find_geometry", {"target": "FeatureCameo", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("fc_cyl")),
    ("model_construction", lambda c: {"kind": "axis", "mode": "circular_face",
                                      "face": _ctx_get(c, "fc_cyl", "a cameo bore face"),
                                      "name": "CameoSpin"},
     lambda p: bool(p.get("handle")), ("cam_axis", lambda p: p["handle"])),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 4, "total_angle_deg": 360,
                                          "axis": _ctx_get(c, "cam_axis", "the datum axis handle")},
     lambda p: p.get("quantity") == 4 and p.get("axis") == "CameoSpin", None),
    # the same axis reached BY NAME while its component is active - the handle-free route.
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 3, "total_angle_deg": 180,
                                          "axis": "CameoSpin"},
     lambda p: p.get("quantity") == 3 and p.get("axis") == "CameoSpin", None),
    # the CYLINDRICAL FACE itself as the axis: off-origin, the case a direction vector cannot
    # express, and the label reads the resolved entity's type because a face carries no name.
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 3,
                                          "axis": _ctx_get(c, "fc_cyl", "a cameo bore face")},
     lambda p: p.get("quantity") == 3 and p.get("axis") == "BRepFace", None),
    # a datum NAME reaches only the ACTIVE component, so the same name from the root is refused
    # rather than resolved to something else. (A second same-named axis is never ambiguous - Fusion
    # dedupes the name itself.) The activation is put back so the cameo tree is unchanged.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 3, "axis": "CameoSpin"}, "refused", None),
    ("design_activate_component", {"occurrence": "FeatureCameo:1"}, "ok", None),
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
    # the same body reached through TWO SEPARATE handles must be refused as its own tool: a
    # resolution hands back a fresh proxy each time, so an identity-only guard lets it through
    # and Fusion is asked to join a body to itself.
    ("find_geometry", {"target": "CombineCameo", "kind": "planar_face", "nearest_to": [215, 115, 0], "max_results": 1}, "ok", _fg("cc_a2")),
    ("model_combine", lambda c: {"target": _ctx_get(c, "cc_a", "combine target"),
                                 "tools": [_ctx_get(c, "cc_a2", "the same body again")],
                                 "operation": "join"}, "refused", None),
    ("model_combine", lambda c: {"target": _ctx_get(c, "cc_a", "combine target"), "tools": [_ctx_get(c, "cc_b", "combine tool")], "operation": "join"}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the revolve axis as a CYLINDRICAL FACE, off the origin - the case a world key cannot express.
    # A face mapped to a direction VECTOR keeps the direction and DROPS the location, so the ring is
    # turned about the world axis through the ORIGIN and reported as success: the label is checked
    # AND the geometry measured. Live: a cylinder at x=30, a 2x3 mm profile at x 36-38 on the XZ
    # plane, ring bbox x 22..38. The cameo sits at z=100, clear of the gyroscope and the other cameos.
    ("model_create_component", {"name": "RevolveCameo", "activate": True}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 100, "name": "RevAxisPlane"}, "ok", None),
    ("sketch_create", {"plane": "RevAxisPlane", "name": "RevAxisS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 30, "cy": 0, "radius": 5, "sketch_name": "RevAxisS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RevAxisS", "profile_index": 0, "distance": 20}, "ok", None),
    ("find_geometry", {"target": "RevolveCameo", "kind": "cylinder_face", "radius": 5, "max_results": 1}, "ok", _fg("rv_cyl")),
    ("sketch_create", {"plane": "xz", "name": "RevRingS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 36, "y1": 100, "x2": 38, "y2": 103,
                             "sketch_name": "RevRingS"}, "ok", None),
    ("model_revolve", lambda c: {"sketch_name": "RevRingS", "profile_index": 0,
                                 "axis": _ctx_get(c, "rv_cyl", "the off-origin cylinder face"),
                                 "angle_deg": 360},
     lambda p: p.get("axis") == "BRepFace", None),
    # the label alone cannot see wrong geometry: the ring must stand AROUND x=30, never around the
    # origin (about the world z axis this profile spans x -38..38, so a positive min x is the tell).
    ("model_inspect", {"target": "RevolveCameo:1"},
     lambda p: (p["min_point"]["x"] >= 20 and p["max_point"]["x"] <= 40
                and p["min_point"]["x"] > 0), None),
    # a PLANAR face carries a normal, not an axis - refused by name (only a cylindrical/conical/
    # toroidal face defines one) instead of turned into a direction the caller never asked for.
    ("find_geometry", {"target": "RevolveCameo", "kind": "planar_face", "nearest_to": [30, 0, 120],
                       "max_results": 1}, "ok", _fg("rv_flat")),
    ("model_revolve", lambda c: {"sketch_name": "RevRingS", "profile_index": 0,
                                 "axis": _ctx_get(c, "rv_flat", "a planar cap face"),
                                 "angle_deg": 360}, "refused", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # 'all' takes EVERY closed region, and the bays inside a frame outline are closed regions too -
    # so this extrude fills them with material. The payload cannot show a solid bay, so the enclosed
    # regions are NAMED: an outer rectangle plus three bays reports the three that sit inside another.
    ("model_create_component", {"name": "BayCameo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 900, "x2": 290, "y2": 960, "sketch_name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 210, "y1": 910, "x2": 230, "y2": 950, "sketch_name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 240, "y1": 910, "x2": 260, "y2": 950, "sketch_name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 270, "y1": 910, "x2": 285, "y2": 950, "sketch_name": "BayS"}, "ok", None),
    ("model_extrude", {"sketch_name": "BayS", "profile_index": "all", "distance": 8},
     lambda p: (isinstance(p.get("enclosed_profile_indices"), list)
                and len(p["enclosed_profile_indices"]) > 0
                and "enclosed" in p.get("note", "")), None),
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
    # THE MOTION VOCABULARY at the geometry seam - a ball on a real SPHERE face, an explicit frame
    # axis, and rigid. Each beat takes its own free cameo pair, chained as a TREE (sphere - post -
    # post), so no beat closes a loop on another's joint. The sphere is built through the surface
    # family the same way the fill cameo builds one: a half-disc arc revolved into a closed sheet and
    # sealed solid, which is what gives this beat a genuine SphereSurfaceType face to joint at (a
    # sphere face takes ONLY CenterKeyPoint - the rule inside _joints.build_joint_geometry).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "BallSphere", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "BallProf"}, "ok", None),
    # the arc's endpoints must sit ON the revolve axis (x=0) or the revolved surface is an open tube
    # enclosing nothing; on an xz sketch +Y maps to world -Z, so this sphere sits alone at z=+300.
    ("sketch_add_geometry", {"kind": "arc", "cx": 0, "cy": -300, "x1": 0, "y1": -294,
                             "sweep_deg": 180, "sketch_name": "BallProf"}, "ok", None),
    ("surface_revolve", {"sketch_name": "BallProf", "axis": "z", "angle_deg": 360}, "ok", None),
    ("surface_fill", {"tools": ["BallSphere"], "operation": "new"},
     lambda p: p.get("all_solid") is True, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "BallPost", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "BallPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 0, "radius": 5,
                             "sketch_name": "BallPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "BallPostS", "profile_index": 0, "distance": 20}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AxisPost", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "AxisPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 60, "radius": 5,
                             "sketch_name": "AxisPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AxisPostS", "profile_index": 0, "distance": 20}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "RigidPost", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "RigidPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 120, "radius": 5,
                             "sketch_name": "RigidPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RigidPostS", "profile_index": 0, "distance": 20}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("BallPost:1"),
    ("find_geometry", {"target": "BallSphere", "kind": "sphere_face", "max_results": 1}, "ok",
     _fg("ball_face")),
    ("find_geometry", {"target": "BallPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("ball_post_cyl")),
    ("find_geometry", {"target": "AxisPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("axis_post_cyl")),
    ("find_geometry", {"target": "RigidPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("rigid_post_cyl")),
    # the ball seats the sphere's CENTRE on the post's own key point, and the label proves which
    # key point the sphere face resolved to. A ball joint reads no axis at all, so 'axis' is null
    # and the note carries NO axis sentence - not the frame-axis caveat, not the derived-axis one.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_face", "the sphere face"),
                                     "handle_two": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "motion": "ball", "name": "BallSeat"},
     lambda p: p.get("jointed") is True and p.get("geometry_one") == "sphere_face@center"
     and p.get("axis") is None
     and "FRAME's" not in (p.get("note") or "") and "world_axis=" not in (p.get("note") or "")
     and "derived the motion axis" not in (p.get("note") or ""), None),
    # an EXPLICIT axis is frame-relative, and the note says so in the words that stop a caller
    # reading it as a world axis - plus the one tool that does set a true world axis.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "handle_two": _ctx_get(c, "axis_post_cyl", "the axis post wall"),
                                     "motion": "revolute", "axis": "y", "name": "FrameAxisSpin"},
     lambda p: "FRAME's y axis, NOT world y" in (p.get("note") or "")
     and "joint_edit(world_axis=" in (p.get("note") or ""), None),
    # an axis outside the Choice is refused by name, listing what the input carries - nothing built.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "handle_two": _ctx_get(c, "axis_post_cyl", "the axis post wall"),
                                     "motion": "revolute", "axis": "diagonal"}, "refused", None),
    # rigid has no motion to aim, so it too publishes a null axis and an axis-free note.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "handle_two": _ctx_get(c, "rigid_post_cyl", "the rigid post wall"),
                                     "motion": "rigid", "name": "PostLock"},
     lambda p: p.get("jointed") is True and p.get("axis") is None
     and "FRAME's" not in (p.get("note") or "")
     and "derived the motion axis" not in (p.get("note") or ""), None),
    # NEW-1: the TORUS keypoint gate. createByNonPlanarFace(torus, CenterKeyPoint) is measured
    # correct on a PARAMETRIC torus and silently WRONG inside a base feature (it hands back the
    # owning component's origin with no error), so the tool compares the keypoint against the
    # torus's own centre in the same world frame. This beat is the parametric side: the joint lands
    # and the payload names the key point it resolved to. (The two base-feature halves need a torus
    # built INSIDE a base feature; no tool on this surface builds one unattended - see STORY.)
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TorusRing", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "TorusProf"}, "ok", None),
    # on an xz sketch +Y maps to world -Z: this circle sits at world (40, 0, 400) and revolving it
    # about z sweeps a torus of major radius 40 centred on the z axis at z=400, alone up there.
    ("sketch_add_geometry", {"kind": "circle", "cx": 40, "cy": -400, "radius": 6,
                             "sketch_name": "TorusProf"}, "ok", None),
    ("model_revolve", {"sketch_name": "TorusProf", "profile_index": 0, "axis": "z",
                       "angle_deg": 360}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TorusPost", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "TorusPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 180, "radius": 5,
                             "sketch_name": "TorusPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TorusPostS", "profile_index": 0, "distance": 20}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", {"target": "TorusRing", "kind": "torus_face", "max_results": 1}, "ok",
     _fg("torus_face")),
    ("find_geometry", {"target": "TorusPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("torus_post_cyl")),
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "torus_face", "the torus face"),
                                     "handle_two": _ctx_get(c, "torus_post_cyl",
                                                            "the torus post wall"),
                                     "motion": "rigid", "name": "TorusSeat"},
     lambda p: p.get("jointed") is True and p.get("geometry_one") == "torus_face@center", None),
    # A NON-RIGID as-built joint, on its own far-grid pair: an as-built joint moves nothing, so the
    # plate is built already seated on the pin's top face (z=20) and jointed where it stands. The
    # anchor is that shared face, reached by the pin's 'top' snap.
    ("model_create_component", {"name": "AsbPin", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "AsbPinS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 800, "y1": 300, "x2": 820, "y2": 320,
                             "sketch_name": "AsbPinS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AsbPinS", "profile_index": 0, "distance": 20}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AsbPlate", "activate": True}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 20, "name": "AsbSeat"}, "ok", None),
    ("sketch_create", {"plane": "AsbSeat", "name": "AsbPlateS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 790, "y1": 290, "x2": 830, "y2": 330,
                             "sketch_name": "AsbPlateS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AsbPlateS", "profile_index": 0, "distance": 10}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("AsbPlate:1"),
    # the motion is read back off the CREATED joint, and the resolved anchor is named in the payload.
    # 'name' rides the same create: AsBuiltJoints.createInput/add take no name, so it is applied
    # post-create and READ BACK - 'joint' is what the browser shows, never an echo.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "geometry": "AsbPin:1:top", "joint_type": "revolute", "axis": "z",
                               "name": "AsbNamed"},
     lambda p: p.get("joint_type") == "revolute" and bool(p.get("geometry"))
     and p.get("joint") == "AsbNamed",
     ("asb_joint", lambda p: p["joint"])),
    # an INDEPENDENT read of the same joint: the tool's own read-back is not the only witness.
    ("assembly_get", {},
     lambda p: any(j.get("type") == "revolute"
                   and {j.get("occurrence_one"), j.get("occurrence_two")} == {"AsbPin:1", "AsbPlate:1"}
                   for j in (p.get("joints") or [])), None),
    # the beat the read-backs cannot fake: a motion that reads back but cannot be DRIVEN is no DOF.
    ("joint_drive", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                               "angle_deg": 30},
     lambda p: abs(p.get("value_now", {}).get("angle_deg", 0) - 30) < 0.5, None),
    ("joint_drive", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                               "angle_deg": 0}, "ok", None),
    # Fusion refuses a non-rigid as-built joint with a null geometry, so the tool names the missing
    # anchor instead of letting add() raise.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "joint_type": "revolute"}, "refused", None),
    # a rigid as-built joint IGNORES a geometry it is handed, so the pairing is refused rather than
    # accepted and dropped.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "joint_type": "rigid", "geometry": "AsbPin:1:top"}, "refused", None),
    # an AsBuiltJoint exposes NO offset/angle ModelParameter for ANY motion - both parametric-drive
    # refusals name AS-BUILT and route to joint_create instead of the dead-end generic wording.
    ("joint_edit", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                              "offset": 5}, _refused("AS-BUILT", "joint_create"), None),
    ("joint_edit", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                              "angle": 30}, _refused("AS-BUILT", "joint_create"), None),
    # a SECOND as-built joint on an already-jointed pair is refused by the platform at add()
    # ("System will be over constrained") - measured; the tool surfaces it, never a false ok.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "joint_type": "rigid"},
     _refused("over constrained"), None),
    # COUPLE the crank to the rotor spin at ratio 2 - the DOF-fix step - across independent chains.
    ("joint_motion_link", {"joint_one": "CrankAxis", "joint_two": "Spin", "ratio": 2}, "ok", None),
    ("assembly_get", {}, "ok", None),
    # the relations LIFECYCLE on the story's own relations: list, suppress round-trip, re-value the
    # crank link (was_reversed disclosed), the measured set_occurrences refusal, and a delete with
    # the survivor re-list - on a scratch group so the story keeps its Frame/Carrier lock.
    # motion-link auto-names are SESSION-GLOBAL (the story's first link can be 'Motion Link 9'),
    # so both names come from the relations read, never hardcoded.
    ("assembly_get", {"include": ["relations"]},
     lambda p: p.get("relation_counts", {}).get("rigid_groups", 0) >= 1
     and p.get("relation_counts", {}).get("motion_links", 0) >= 1,
     ("rel_names", lambda p: {"rg": p["relations"]["rigid_groups"][0]["name"],
                              "ml": p["relations"]["motion_links"][0]["name"]})),
    ("assembly_edit_relations", lambda c: {"kind": "rigid_group", "name": _ctx_get(c, "rel_names", "relation names")["rg"], "action": "suppress"},
     lambda p: p.get("is_suppressed") is True, None),
    ("assembly_edit_relations", lambda c: {"kind": "rigid_group", "name": _ctx_get(c, "rel_names", "relation names")["rg"], "action": "unsuppress"},
     lambda p: p.get("is_suppressed") is False, None),
    ("assembly_edit_relations", lambda c: {"kind": "motion_link", "name": _ctx_get(c, "rel_names", "relation names")["ml"], "action": "set_values", "ratio": 3},
     lambda p: p.get("ratio") == 3.0 and "was_reversed" in p, None),
    ("assembly_edit_relations", lambda c: {"kind": "motion_link", "name": _ctx_get(c, "rel_names", "relation names")["ml"], "action": "set_values", "ratio": 2}, "ok", None),
    # the measured set_occurrences refusal, asserted in the WORDS that make it a fact: the build it
    # was measured on and the platform sentence it would raise. Nothing is written, so the group
    # still holds the two members assembly_rigid_group gave it - read back on the next row.
    ("assembly_edit_relations", lambda c: {"kind": "rigid_group", "name": _ctx_get(c, "rel_names", "relation names")["rg"], "action": "set_occurrences", "occurrences": ["Frame:1"]},
     _refused("2705.0.87", "Cannot be edited before rolling back"), None),
    # the same group the refusal named (rel_names read it from this same first row) still counts the
    # two occurrences assembly_rigid_group built it from - the refusal wrote nothing.
    ("assembly_get", {"include": ["relations"]},
     lambda p: p["relations"]["rigid_groups"][0]["occurrence_count"] == 2, None),
    ("assembly_edit_relations", {"kind": "rigid_group", "name": "NoSuchGroup", "action": "delete"}, "refused", None),
    # contact sets: the design-level lifecycle on a scratch set built from the story's own parts -
    # create (>=2 distinct members), the single-member refusal, re-member, rename reading the LANDED
    # name back, a suppress round-trip, the two analysis flags (restored), and delete + re-list.
    ("assembly_edit_contacts", {"action": "create", "members": ["Frame:1", "Carrier:1"]},
     lambda p: p.get("member_count") == 2 and bool(p.get("contact_set")),
     ("contact_set", lambda p: p["contact_set"])),
    ("assembly_edit_contacts", {"action": "create", "members": ["Frame:1"]}, "refused", None),
    ("assembly_get", {"include": ["contacts"]},
     lambda p: p.get("contact_count", 0) >= 1 and "enabled" in p.get("contact_analysis", {}), None),
    ("assembly_edit_contacts", lambda c: {"action": "set_members", "name": _ctx_get(c, "contact_set", "contact set name"), "members": ["Frame:1", "Pedestal:1"]},
     lambda p: p.get("member_count") == 2, None),
    ("assembly_edit_contacts", lambda c: {"action": "rename", "name": _ctx_get(c, "contact_set", "contact set name"), "new_name": "SweepContacts"},
     lambda p: str(p.get("contact_set", "")).startswith("SweepContacts"),
     ("contact_set", lambda p: p["contact_set"])),
    ("assembly_edit_contacts", lambda c: {"action": "suppress", "name": _ctx_get(c, "contact_set", "contact set name")},
     lambda p: p.get("is_suppressed") is True, None),
    ("assembly_edit_contacts", lambda c: {"action": "unsuppress", "name": _ctx_get(c, "contact_set", "contact set name")},
     lambda p: p.get("is_suppressed") is False, None),
    # scope is REFUSED while contact analysis is off - the platform raises '3 : Contact analysis is
    # disabled.' on the write - so the enable comes first and the design is left as it was found.
    ("assembly_edit_contacts", {"action": "set_analysis_scope", "scope": "contact_sets"}, "refused", None),
    ("assembly_edit_contacts", {"action": "enable_analysis"}, lambda p: p.get("analysis_enabled") is True, None),
    ("assembly_edit_contacts", {"action": "set_analysis_scope", "scope": "contact_sets"},
     lambda p: p.get("scope") == "contact_sets", None),
    # put the scope back to the design's own all_bodies BEFORE disabling: the flag is retained under
    # a disable and comes back on the next enable, so skipping this would leave the story document
    # carrying a contact_sets scope it never had.
    ("assembly_edit_contacts", {"action": "set_analysis_scope", "scope": "all_bodies"},
     lambda p: p.get("scope") == "all_bodies", None),
    ("assembly_edit_contacts", {"action": "disable_analysis"},
     lambda p: p.get("analysis_enabled") is False and p.get("scope") == "all_bodies", None),
    ("assembly_edit_contacts", {"action": "delete", "name": "NoSuchContactSet"}, "refused", None),
    ("assembly_edit_contacts", lambda c: {"action": "delete", "name": _ctx_get(c, "contact_set", "contact set name")},
     lambda p: p.get("deleted") is True, None),
    # DRIVE THE AXES ON CAMERA: yaw proves the no-take gate; both ring pivots and the crank ->
    # rotor 2:1 drive for real. Yaw CANNOT move in this scene - Carrier:1 rides the rigid group
    # with Frame:1 and the chain closes through Pedestal:1, so the solver holds it at 0 (measured:
    # it stays 0 even with Frame:1's parent lock released) - so its drive must REFUSE as a
    # no-take.
    ("joint_drive", {"joint_name": "Yaw", "angle_deg": 30}, "refused", None),
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
    # the pending pose is transient until captured: status sees it armed with no captured markers
    # yet (this document has captured none), and discard_pending throws it away - the pending flag
    # clears while the captured-marker collection is left exactly as it was.
    ("assembly_capture_position", {"action": "status"},
     lambda p: p.get("has_pending") is True and p.get("snapshot_count") == 0, None),
    ("assembly_capture_position", {"action": "discard_pending"},
     lambda p: p.get("discarded") is True and p.get("has_pending") is False
     and p.get("snapshot_count") == 0, None),
    # re-arm the move the capture below records - the discard consumed the first one.
    ("assembly_move", {"occurrence": "PoseCameo:1", "dx": 40}, "ok", None),
    ("assembly_capture_position", {"action": "capture"}, "ok", None),
]
_MOTION += _box("ConA", ox=360) + _box("ConB", ox=360) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_constrain", {"snap_one": "ConA:1:bottom", "snap_two": "ConB:1:top", "flipped": True}, "ok", None),
    ("design_recompute", {}, "ok", None),
    # RESTRUCTURE: two more crank instances (they share the Crank component's geometry), one of them
    # re-parented under the frame, then both removed so the mechanism is left as it was found. Fusion
    # numbers an instance from a per-component counter, so every path here is READ back, never a
    # predicted ':2'.
    ("design_add_instance", {"component": "Crank", "x": 60, "y": -60, "units": "mm"},
     lambda p: p.get("created") is True and p.get("component") == "Crank"
     and str(p.get("full_path", "")).startswith("Crank:"),
     ("crank_b", lambda p: p["full_path"])),
    # the SECOND call names the same component while two instances of it exist - the bare name that
    # would otherwise be ambiguous resolves because every candidate is an instance of ONE component.
    ("design_add_instance", {"component": "Crank", "x": 90, "y": -60, "units": "mm"},
     lambda p: p.get("created") is True
     and p.get("full_path") not in ("Crank:1", None), ("crank_c", lambda p: p["full_path"])),
    ("design_get", {"include": ["tree"]}, "ok", None),
    # the re-parent: the browser path changes, the world position does not.
    ("design_move_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_b", "the second crank"),
                                          "into_component": "Frame:1"},
     lambda p: p.get("changed") is True and str(p.get("full_path", "")).startswith("Frame:1+")
     and p.get("world_position_preserved") is True, ("crank_b", lambda p: p["full_path"])),
    # there is no root target: the API moves an occurrence into another OCCURRENCE, so the direction
    # is refused by name instead of being attempted and failing inside Fusion.
    ("design_move_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_b", "the second crank"),
                                          "into_component": "root"}, "refused", None),
    # a component may not hold an instance of itself, on either tool.
    ("design_add_instance", {"component": "Frame", "into_component": "Frame:1"}, "refused", None),
    ("design_move_occurrence", {"occurrence": "Frame:1", "into_component": "Frame:1"},
     "refused", None),
    ("design_delete_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_b", "the second crank")},
     "ok", None),
    ("design_delete_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_c", "the third crank")},
     "ok", None),
]

# --- ACT 4: DETAILS - fillet/chamfer the rings, section through the gimbal center (mirrors S4) -
_DETAILS = [
    ("find_geometry", {"target": "OuterRing", "kind": "circular_edge", "max_results": 1}, "ok", _fg("or_edge")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "or_edge", "outer ring edge")], "radius": 1}, "ok", None),
    ("find_geometry", {"target": "Frame", "kind": "circular_edge", "max_results": 1}, "ok", _fg("fr_edge")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "fr_edge", "frame edge")], "distance": 1}, "ok", None),
    # the distance-and-angle definition with an explicit corner type. Both assertions are on values
    # READ BACK off the created feature - a corner type the platform silently ignored builds an
    # identical face count, so an echoed payload would sail through this predicate.
    ("find_geometry", {"target": "Frame", "kind": "circular_edge", "max_results": 4}, "ok",
     ("fr_edge2", lambda p: p["matches"][-1]["handle"])),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "fr_edge2", "second frame edge")],
                                 "distance": 1, "angle_deg": 30, "corner_type": "miter"},
     lambda p: p.get("corner_type") == "miter" and abs((p.get("angle_deg") or 0) - 30) < 1e-6
     and not p.get("corner_type_unverified") and not p.get("angle_deg_unverified") and not p.get("chamfer_type_unverified"), None),
    # a shell cameo cap, a wart feature added and deleted (timeline health diff), a scratch occurrence.
    ("model_create_component", {"name": "ShellCap", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ShellS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 400, "y1": 0, "x2": 430, "y2": 30, "sketch_name": "ShellS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ShellS", "profile_index": 0, "distance": 20}, "ok", None),
    _watch("ShellCap:1"),
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [415, 15, 20], "max_results": 1}, "ok", _fg("shell_top")),
    ("model_shell", lambda c: {"body_name": "ShellCap", "remove_faces": [_ctx_get(c, "shell_top", "shell top")], "thickness": 2}, "ok", None),
    # THE WartPlane ROW carries the offset_from predicate - the sweep's only offset-plane call, so
    # it is where 'offset_from' gets read once against a real resolved origin plane: the payload
    # must name the PLANE ('XY'), never echo the 'xy' request token. The unit fake spells the name
    # uppercase from the sibling convention; this beat is what MEASURES the live casing, so a
    # failure on the string alone means the FAKE is what is wrong, never the tool.
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 12, "name": "WartPlane"},
     lambda p: p.get("offset_from") == "XY", None),
    ("design_delete_feature", {"feature": "WartPlane"}, "ok", None),
    # the three datum modes with no other route in the API: a plane rotated about a curved face's
    # own inferred axis, a plane pinned through a vertex, and a plane/point at a ratio along a path.
    # RotorShaft is the cylinder sketched on yz at (0,0), so its axis is X through the origin and
    # every plane built about it has its origin ON that axis (y = z = 0, measured behaviour).
    ("find_geometry", {"target": "RotorShaft", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("cd_cyl")),
    ("model_construction", lambda c: {"kind": "plane", "mode": "at_angle_on_face",
                                      "face": _ctx_get(c, "cd_cyl", "shaft face"),
                                      "plane": "xz", "angle": 30, "name": "ShaftAnglePlane"},
     lambda p: p.get("contains_face_axis") is True and p.get("angle_deg") == 30
     and abs(p["geometry"]["origin"]["y"]) < 1e-6 and abs(p["geometry"]["origin"]["z"]) < 1e-6, None),
    ("find_geometry", {"target": "ShellCap", "kind": "vertex", "max_results": 1}, "ok", _fg("cd_vert")),
    ("model_construction", lambda c: {"kind": "plane", "mode": "offset_through_point", "plane": "xy",
                                      "points": [_ctx_get(c, "cd_vert", "shell vertex")],
                                      "name": "ThroughVertexPlane"},
     lambda p: p.get("passes_through_point") is True, None),
    # the bottom front edge specifically: (400,0,0) -> (430,0,0), midpoint (415,0,0).
    ("find_geometry", {"target": "ShellCap", "kind": "line_edge", "nearest_to": [415, 0, 0], "max_results": 1},
     "ok", _fg("cd_edge")),
    # The on-path placements all ride ONE known path: the cap's bottom front edge, 30 mm long from
    # (400,0,0) to (430,0,0). A PROPORTIONAL placement reads 'at' as a unitless ratio and publishes
    # it back as the ratio it landed on - no path extent at all, because a ratio cannot leave the
    # path. Every absolute beat below is measured against that same 30 mm.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 0.5, "name": "MidPathPlane"},
     lambda p: p.get("at_ratio") == 0.5
     and abs(p["geometry"]["origin"]["x"] - 415) < 1e-3
     and abs(p["geometry"]["origin"]["y"]) < 1e-3 and abs(p["geometry"]["origin"]["z"]) < 1e-3
     and p.get("landed", {}).get("distance") == "0.5"
     and "path_length" not in p and "beyond_path" not in p
     and "not clamped" not in (p.get("note") or ""), None),
    # a quarter along the SAME edge: 7.5 mm from the midpoint whichever way the edge runs.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 0.25, "name": "QuarterPathPoint"},
     lambda p: p.get("at_ratio") == 0.25
     and abs(abs(p["geometry"]["at"]["x"] - 415) - 7.5) < 1e-3
     and abs(p["geometry"]["at"]["y"]) < 1e-3 and abs(p["geometry"]["at"]["z"]) < 1e-3, None),
    # setByPath RAISES on a proportional value outside 0-1 and the raise rolls back the whole
    # transaction, so the range is refused before the call - and the next call still answers.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"), "at": 1.5},
     "refused", None),
    # ABSOLUTE: 'at' is a LENGTH from the path start, so the payload swaps the ratio for the pair
    # that can be compared - the measured path length and where this datum sits along it. 12 mm is
    # inside the 30 mm edge, so beyond_path is false and the note warns of nothing.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 12, "distance_type": "absolute",
                                      "name": "AbsInsidePlane"},
     lambda p: p.get("distance_type") == "absolute" and "at_ratio" not in p
     and isinstance(p.get("landed", {}).get("distance"), str)
     and abs(p.get("path_length", 0) - 30.0) < 1e-3
     and abs(p.get("along_path", -1) - 12.0) < 1e-3
     and p.get("beyond_path") is False and "OFF the path" not in (p.get("note") or ""), None),
    # An absolute distance is not clamped at EITHER end: a NEGATIVE one places the datum before the
    # path start, along the tangent, with a healthy feature. That is a legal placement the platform
    # accepts, so it is reported with its measured numbers - and the note says it landed off.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": -5, "distance_type": "absolute",
                                      "name": "BeforeStartPlane"},
     lambda p: p.get("beyond_path") is True and abs(p.get("along_path", 0) + 5.0) < 1e-3
     and "OFF the path" in (p.get("note") or ""), None),
    # the far end of the same range, on the point kind: 500 mm along a 30 mm edge.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 500, "distance_type": "absolute",
                                      "name": "PastEndPoint"},
     lambda p: p.get("beyond_path") is True and abs(p.get("path_length", 0) - 30.0) < 1e-3, None),
    # the boundary itself: a datum exactly AT the path length is ON the path, not beyond it.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 30, "distance_type": "absolute",
                                      "name": "AtEndPlane"},
     lambda p: p.get("beyond_path") is False
     and abs(p.get("along_path", -1) - p.get("path_length", 0)) < 1e-6, None),
    # an EXPRESSION placement is measured exactly as a literal one - the expression is what the
    # datum's own model parameter carries, and that parameter's name is what param_set retargets.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": "22 mm", "distance_type": "absolute",
                                      "name": "ExprPathPoint"},
     lambda p: "22" in (p.get("landed", {}).get("distance") or "")
     and str(p.get("model_parameters", {}).get("distance", "")).startswith("d"), None),
    # proportional on the plane kind reads back as the bare ratio, with no extent published - the
    # pair that separates the two distance types in one payload.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 0.5, "distance_type": "proportional",
                                      "name": "RatioPlane"},
     lambda p: p.get("landed", {}).get("distance") == "0.5"
     and "path_length" not in p and "beyond_path" not in p, None),
    # to_object: the plane lands at a VERTEX's own along-path position PLUS a signed offset, and the
    # two arrive as SEPARATE model parameters. 40 mm past either end of a 30 mm edge is off the path
    # whichever vertex the edge starts at, so this one carries the same off-path disclosure.
    ("find_geometry", {"target": "ShellCap", "kind": "vertex", "nearest_to": [430, 0, 0],
                       "max_results": 1}, "ok", _fg("cd_vert_end")),
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "to_object": _ctx_get(c, "cd_vert_end", "a path end vertex"),
                                      "offset": 40, "name": "ToObjectFarPlane"},
     lambda p: p.get("to_object") is True
     and set(p.get("landed", {})) == {"distance", "offset"}
     and set(p.get("model_parameters", {})) == {"distance", "offset"}
     and abs(p.get("path_length", 0) - 30.0) < 1e-3
     and p.get("along_path", 0) > p.get("path_length", 0)
     and p.get("beyond_path") is True and "OFF the path" in (p.get("note") or ""), None),
    # the same shape landing INSIDE, so the disclosure is proven to discriminate: the cap's bottom
    # front and right edges chain into a 60 mm path whose shared vertex sits at 30 mm, and 5 mm past
    # that is still on the path.
    ("find_geometry", {"target": "ShellCap", "kind": "line_edge", "nearest_to": [430, 15, 0],
                       "max_results": 1}, "ok", _fg("cd_edge2")),
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": [_ctx_get(c, "cd_edge", "datum path edge"),
                                               _ctx_get(c, "cd_edge2", "the connected second edge")],
                                      "to_object": _ctx_get(c, "cd_vert_end", "the shared vertex"),
                                      "offset": 5, "name": "ToObjectMidPlane"},
     lambda p: p.get("beyond_path") is False and abs(p.get("along_path", 0) - 35.0) < 1e-3
     and "OFF the path" not in (p.get("note") or ""), None),
    # ConstructionPointInput carries setByPath but NOT setByPathToObject, so the point kind refuses
    # 'to_object' by name instead of dropping it.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "to_object": _ctx_get(c, "cd_vert_end", "a path end vertex")},
     "refused", None),
    # a CHAINED path is measured whole: 45 mm is past the first edge but inside the 60 mm total, so
    # the datum is on the path and 'path_length' is the SUM, not the seed edge's own length.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": [_ctx_get(c, "cd_edge", "datum path edge"),
                                               _ctx_get(c, "cd_edge2", "the connected second edge")],
                                      "at": 45, "distance_type": "absolute",
                                      "name": "ChainedPathPlane"},
     lambda p: p.get("beyond_path") is False and abs(p.get("path_length", 0) - 60.0) < 1e-3
     and abs(p.get("along_path", 0) - 45.0) < 1e-3, None),
    # sketch_project's two new actions, on the cap the datum beats already measured. The section
    # sketch sits on MidPathPlane (x=415, normal along X), which crosses the cap cleanly; the cap's
    # x=400 side face is PARALLEL to that plane, so it is the same-context source that contributes
    # nothing and the partial path must name it.
    ("sketch_create", {"plane": "MidPathPlane", "name": "SecS"}, "ok", None),
    # the section of the HOLLOW cap is outer + inner rectangles - at least 8 curves. Attribution is
    # honestly SUPPRESSED whenever any created curve fails to match back, so the beat asserts the
    # census, not per_source.
    ("sketch_project", {"action": "intersect", "sketch_name": "SecS", "bodies": ["ShellCap"]},
     lambda p: p.get("created_count", 0) >= 8 and len(p.get("entity_refs") or []) >= 8, None),
    # the cap's x=400 side face is PARALLEL to the section plane: zero curves created is the
    # measured silent-empty, which the tool's own gate converts into an error naming the source.
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [400, 15, 10], "max_results": 1}, "ok", _fg("sp_far")),
    ("sketch_project", lambda c: {"action": "intersect", "sketch_name": "SecS",
                                  "entities": [_ctx_get(c, "sp_far", "cap far side face")]},
     "refused", None),
    # to_surface: ShellS's own lines projected onto the cap's top face, received by a THIRD sketch -
    # the source sketch must differ from the receiver (measured same-sketch refusal), the curves
    # land ON the face (off the receiver's plane), and the linkage is read back per curve.
    ("sketch_create", {"plane": "xy", "name": "ProjS"}, "ok", None),
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [415, 15, 20], "max_results": 1}, "ok", _fg("sp_top")),
    ("sketch_project", lambda c: {"action": "to_surface", "sketch_name": "ProjS",
                                  "target_faces": [_ctx_get(c, "sp_top", "cap top face")],
                                  "source_sketch": "ShellS", "curve_refs": ["line:0"]},
     lambda p: p.get("created_count", 0) >= 1
     and (p.get("off_plane_count") == p.get("created_count") or "census alone" in p.get("note", ""))
     and ("REFERENCE curves" in p.get("note", "") or "census alone" in p.get("note", "")), None),
    ("sketch_project", {"action": "to_surface", "sketch_name": "ProjS", "target_faces": [],
                        "source_sketch": "ProjS", "curve_refs": ["line:0"]}, "refused", None),
    ("sketch_project", lambda c: {"action": "to_surface", "sketch_name": "ProjS",
                                  "target_faces": [_ctx_get(c, "sp_top", "cap top face")],
                                  "source_sketch": "ShellS", "curve_refs": ["line:0"],
                                  "project_type": "along_vector"}, "refused", None),
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
    # a solid body's volume IS readable, so the verdict is the measured ratio and the skip flag is
    # absent - its presence would mean the check fell back to "the geometry moved".
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": 2},
     lambda p: p.get("scale_check") == "volume_ratio"
     and abs(p.get("volume_ratio", 0) - 8.0) < 1e-6 and "volume_check_skipped" not in p, None),
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
    ("model_move", {"bodies": ["ScaleBlock"], "dx": 10},
     lambda p: abs(p.get("displacement", 0) - 10.0) < 1e-3, None),
    ("model_move", {"mode": "along_entity", "bodies": ["ScaleBlock"], "axis": "y",
                    "distance": 5}, lambda p: abs(p.get("displacement", 0) - 5.0) < 1e-3, None),
    ("model_move", {"mode": "rotate", "bodies": ["ScaleBlock"], "axis": "z", "angle_deg": 15},
     "ok", None),
    ("model_move", lambda c: {"mode": "along_entity", "bodies": ["ScaleBlock"],
                              "axis": _ctx_get(c, "scale_top", "block top"), "distance": 5},
     "refused", None),
    ("model_move", lambda c: {"bodies": ["ScaleBlock"],
                              "faces": [_ctx_get(c, "scale_top", "block top")], "dx": 5},
     "refused", None),
    # SINGLE vs DOUBLE placement: the along_entity beats above moved a body in a component placed
    # ONCE (the axis is proxied into that one occurrence). The same call on a component placed TWICE
    # must refuse naming BOTH paths - each instance holds that body somewhere else, and the
    # displacement read-back cannot tell a right instance from a wrong one.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TwicePlaced", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "TwicePlacedS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1900, "y1": 200, "x2": 1920, "y2": 220,
                             "sketch_name": "TwicePlacedS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwicePlacedS", "profile_index": 0, "distance": 10},
     "ok", None),
    # [F75]/NEW-16: name the body uniquely, then resolve it BARE while the component is still the
    # ACTIVE component and placed ONCE - the walk reaches it both natively (active-comp scope) and
    # as the occurrence proxy, whose entityTokens DIFFER; grouping by native token collapses the
    # pair to ONE candidate (a bare-token key refused this as 'names 2 bodies').
    ("find_geometry", {"target": "TwicePlaced", "kind": "planar_face",
                       "nearest_to": [1910, 210, 10], "max_results": 1}, "ok",
     _fg("twice_face")),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "twice_face", "the TwicePlaced body"),
                                   "new_name": "TwiceBody"},
     lambda p: p.get("name") == "TwiceBody", None),
    ("model_inspect", {"target": "TwiceBody"}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("design_add_instance", {"component": "TwicePlaced", "x": 1960, "y": 200, "units": "mm"},
     lambda p: p.get("created") is True, ("twice_b", lambda p: p["full_path"])),
    ("model_move", {"mode": "along_entity", "bodies": ["TwicePlaced"], "axis": "y", "distance": 5},
     _refused("placed 2 times", "TwicePlaced:1"), None),
    # placed TWICE the same bare name is two world placements - the refusal lists BOTH
    # instance-qualified forms (the dropped native spelling is not offered), and the qualified
    # form is the way out.
    ("model_inspect", {"target": "TwiceBody"},
     _refused("TwicePlaced:1", "TwicePlaced:2"), None),
    ("model_inspect", {"target": "TwicePlaced:2:TwiceBody"}, "ok", None),
    # back to the component that was active before this cameo, so the ones after it nest as before.
    ("design_activate_component", {"occurrence": "ScaleBlock:1"}, "ok", None),
    # point_to_point: the tool refuses a travel that does not equal the two vertices' own
    # separation, so a plain ok here IS the distance check
    ("find_geometry", {"target": "ScaleBlock", "kind": "vertex", "max_results": 8}, "ok",
     _fgn("mv_verts")),
    ("model_move", lambda c: {"mode": "point_to_point", "bodies": ["ScaleBlock"],
                              "from_point": _ctx_get(c, "mv_verts", "block vertices")[0],
                              "to_point": _ctx_get(c, "mv_verts", "block vertices")[1]},
     lambda p: p.get("moved") is True and p.get("displacement", 0) > 0, None),
    ("model_create_component", {"name": "ThreadPost", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ThreadS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 530, "cy": 10, "radius": 5,
                             "sketch_name": "ThreadS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadS", "profile_index": 0, "distance": 25}, "ok", None),
    ("find_geometry", {"target": "ThreadPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("post_wall")),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M99x9"}, "refused", None),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M10x1.5", "offset": 2}, "refused", None),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M10x1.5", "length": 12, "offset": 2},
     lambda p: p.get("internal") is False and p.get("designation") == "M10x1.5"
     and p.get("right_handed") is True and p.get("length") == 12, None),
    # the partial extent is read back off the feature, and a metric call-out sits in several
    # standards, so the alternatives ride along
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M10x1.5", "length": 12, "offset": 2,
                                "thread_type": "ISO Metric profile"},
     lambda p: p.get("thread_type") == "ISO Metric profile"
     and len(p.get("thread_type_alternatives") or []) > 1, None),
    # a modeled designation far too big for the post grows the body instead of cutting it
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M30x3.5", "modeled": True}, "refused", None),
    # a modeled thread that FITS must cut real material - the rung-4 gate's own regression net
    ("model_create_component", {"name": "ThreadPost2", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ThreadS2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 570, "cy": 10, "radius": 5,
                             "sketch_name": "ThreadS2"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadS2", "profile_index": 0, "distance": 25}, "ok", None),
    ("find_geometry", {"target": "ThreadPost2", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("post2_wall")),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post2_wall", "second post wall")],
                                "designation": "M10x1.5", "modeled": True},
     lambda p: p.get("modeled") is True and p.get("volume_delta_cm3", 0) < 0, None),
    # the INTERNAL side of the same tool, on a real bore: 'internal' is derived from the face's own
    # out-of-material normal (never echoed), and the ThreadInfo it built is checked against the face
    # at add() - so an 'internal' that disagreed with the geometry would have raised. Then a PARTIAL
    # thread measured from the LOW end, with the end it was measured from read back off the feature.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "ThreadBore", "activate": True}, "ok", None),
    # the bore is CUT, not left as the inner loop of a two-circle profile: which region a
    # multi-profile sketch calls its last one is the platform's to decide, and picking the disc
    # there builds a plain rod whose radius-4 wall is EXTERNAL - the thread then lands external and
    # the beat asserts nothing about bores. A solid rod plus a through cut is unambiguous.
    ("sketch_create", {"plane": "xy", "name": "ThreadBoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 610, "cy": 10, "radius": 12,
                             "sketch_name": "ThreadBoreS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadBoreS", "profile_index": 0, "distance": 25},
     "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ThreadBoreCut"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 610, "cy": 10, "radius": 4,
                             "sketch_name": "ThreadBoreCut"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadBoreCut", "profile_index": 0, "distance": 25,
                       "operation": "cut"}, "ok", None),
    ("find_geometry", {"target": "ThreadBore", "kind": "cylinder_face", "radius": 4,
                       "max_results": 1}, "ok", _fg("bore_wall")),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "bore_wall", "the bore wall")],
                                "designation": "M8x1.25"},
     lambda p: p.get("internal") is True and p.get("designation") == "M8x1.25", None),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "bore_wall", "the bore wall")],
                                "designation": "M8x1.25", "length": 10, "location": "low"},
     lambda p: p.get("internal") is True and p.get("location") == "low"
     and p.get("length") == 10, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # sketch_edit_curve: one sketch per action, so no edit can perturb the next.
    ("sketch_create", {"plane": "xy", "name": "EditTrim"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 0, "x2": 700, "y2": 0,
                             "sketch_name": "EditTrim"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 650, "y1": -50, "x2": 650, "y2": 50,
                             "sketch_name": "EditTrim"}, "ok", None),
    ("sketch_edit_curve", {"sketch_name": "EditTrim", "action": "trim", "entity_one": "line:0",
                           "x1": 610, "y1": 0},
     lambda p: [r.get("length") for r in p.get("resulting", [])] == [50.0], None),
    ("sketch_create", {"plane": "xy", "name": "EditExt"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 10, "x2": 630, "y2": 10,
                             "sketch_name": "EditExt"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 680, "y1": -20, "x2": 680, "y2": 40,
                             "sketch_name": "EditExt"}, "ok", None),
    # extend returns an EMPTY collection on success, so the verdict is the curve's own length:
    # 30 mm reaching the crossing line at x=680 makes it 80.
    ("sketch_edit_curve", {"sketch_name": "EditExt", "action": "extend", "entity_one": "line:0",
                           "x1": 628, "y1": 10},
     lambda p: [r.get("length") for r in p.get("resulting", [])] == [80.0], None),
    ("sketch_create", {"plane": "xy", "name": "EditSplit"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 0, "x2": 700, "y2": 0,
                             "sketch_name": "EditSplit"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 650, "y1": -50, "x2": 650, "y2": 50,
                             "sketch_name": "EditSplit"}, "ok", None),
    # both halves are 50 long and must carry DISTINCT ids - the two pieces share one entityToken,
    # so an id resolved by token would report the same curve twice.
    ("sketch_edit_curve", {"sketch_name": "EditSplit", "action": "split", "entity_one": "line:0",
                           "x1": 650, "y1": 0},
     lambda p: [r.get("length") for r in p.get("resulting", [])] == [50.0, 50.0]
     and len({r.get("id") for r in p.get("resulting", [])}) == 2, None),
    ("sketch_create", {"plane": "xy", "name": "EditCorner"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 0, "x2": 660, "y2": 0,
                             "sketch_name": "EditCorner"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 660, "y1": 0, "x2": 660, "y2": 40,
                             "sketch_name": "EditCorner"}, "ok", None),
    ("sketch_edit_curve", {"sketch_name": "EditCorner", "action": "fillet", "entity_one": "line:0",
                           "x1": 655, "y1": 0, "entity_two": "line:1", "x2": 660, "y2": 5,
                           "radius": 10},
     lambda p: abs((p.get("resulting") or [{}])[0].get("length", 0) - 15.708) < 0.01, None),
    ("sketch_create", {"plane": "xy", "name": "EditChamfer"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 0, "x2": 660, "y2": 0,
                             "sketch_name": "EditChamfer"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 660, "y1": 0, "x2": 660, "y2": 40,
                             "sketch_name": "EditChamfer"}, "ok", None),
    ("sketch_edit_curve", {"sketch_name": "EditChamfer", "action": "chamfer",
                           "entity_one": "line:0", "x1": 655, "y1": 0, "entity_two": "line:1",
                           "x2": 660, "y2": 5, "distance": 8},
     lambda p: abs((p.get("resulting") or [{}])[0].get("length", 0) - 11.3137) < 0.01, None),
    ("sketch_create", {"plane": "xy", "name": "EditOffset"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 0, "x2": 700, "y2": 0,
                             "sketch_name": "EditOffset"}, "ok", None),
    # the direction point picks the side: above the line offsets to +y
    ("sketch_edit_curve", {"sketch_name": "EditOffset", "action": "offset", "entity_one": "line:0",
                           "x1": 650, "y1": 20, "distance": 15},
     lambda p: p.get("curve_count_after") == 2, None),
    ("sketch_edit_curve", {"sketch_name": "EditOffset", "action": "chamfer", "entity_one": "line:0",
                           "x1": 650, "y1": 0, "entity_two": "line:1", "x2": 650, "y2": 15,
                           "distance": 5}, "refused", None),
    # sketch_move / sketch_copy: a transform is verified by COORDINATES, never by the API's bool -
    # Sketch.move returns true for an entity a constraint held still. A two-line chain on its own
    # clear band carries every beat below.
    ("sketch_create", {"plane": "xy", "name": "XformSrc"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 500, "x2": 650, "y2": 500,
                             "sketch_name": "XformSrc"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 650, "y1": 500, "x2": 650, "y2": 540,
                             "sketch_name": "XformSrc"}, "ok", None),
    # the copied collection counts the copied ENDPOINTS as well as the curves - 6 entities for a
    # two-line chain - so the target's own curve count is the honest read-back, and the new refs
    # are identity-matched against the target's collections.
    ("sketch_copy", {"sketch_name": "XformSrc", "entities": "line:0,line:1", "dx": 100},
     lambda p: p.get("curve_count_after", 0) - p.get("curve_count_before", 0) == 2
     and p.get("new_curves") == ["line:2", "line:3"]
     and p.get("returned_entity_count") == 6, None),
    # WHERE they landed: a count-only check passes a copy dropped on top of the original, so the
    # copy is read back at its offset position (x 600 -> 700).
    ("sketch_get", {"sketch_name": "XformSrc", "include_entities": True},
     lambda p: any(abs((e.get("start") or {}).get("x", 0) - 700) < 0.01
                   for e in (p.get("entities") or []) if e.get("type") == "line"), None),
    # move the ORIGINAL: line:0 runs 600->650 at y=500 and must land at 620->670, y=530.
    ("sketch_move", {"sketch_name": "XformSrc", "entities": "line:0", "dx": 20, "dy": 30},
     lambda p: p.get("moved_entities") == ["line:0"] and not p.get("unmoved_entities"), None),
    ("sketch_get", {"sketch_name": "XformSrc", "include_entities": True},
     lambda p: any(abs((e.get("start") or {}).get("x", 0) - 620) < 0.01
                   and abs((e.get("start") or {}).get("y", 0) - 530) < 0.01
                   for e in (p.get("entities") or []) if e.get("type") == "line"), None),
    # 180 deg about the line's OWN midpoint: the bounding box is identical afterwards and only the
    # endpoints swap, so the move is seen by the endpoint fingerprint and by nothing coarser.
    ("sketch_move", {"sketch_name": "XformSrc", "entities": "line:0", "rotation_deg": 180,
                     "center_x": 645, "center_y": 530},
     lambda p: p.get("moved_entities") == ["line:0"], None),
    # a cross-sketch copy lands in the TARGET, whose count rises from zero.
    ("sketch_create", {"plane": "xy", "name": "XformDst"}, "ok", None),
    # ONE curve across into an empty target - and the note states the id rule that holds for a COPY:
    # an added curve APPENDS, so the ids already in use keep their entities. Removing a curve is what
    # RENUMBERS (sketch_edit_curve's rule), and saying so here would be wrong for this call.
    ("sketch_copy", {"sketch_name": "XformSrc", "target_sketch": "XformDst",
                     "entities": "line:1", "dx": 0, "dy": -60},
     lambda p: p.get("target_sketch") == "XformDst" and p.get("curve_count_before") == 0
     and p.get("curve_count_after") == 1
     and "APPENDS" in (p.get("note") or "") and "RENUMBER" not in (p.get("note") or ""), None),
    # a mirror asked for as a negative scale, and a transform that asks for nothing: neither runs.
    ("sketch_move", {"sketch_name": "XformDst", "entities": "line:0", "scale_factor": -1},
     "refused", None),
    ("sketch_move", {"sketch_name": "XformDst", "entities": "line:0"}, "refused", None),
    # the same copy inside a COMPONENT sketch - the occurrence-proxy seam. Sketch.copy hands back
    # assembly-context proxies whose own tokens are NOT the landed curves', so a ref can only be
    # named through each proxy's native entity: an empty 'new_curves' beside a count that rose is
    # the seam breaking, and 'new_curves_complete' appears only when refs went missing.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "CopyComp", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "CompCopyS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1400, "y1": 0, "x2": 1450, "y2": 0,
                             "sketch_name": "CompCopyS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1450, "y1": 0, "x2": 1450, "y2": 40,
                             "sketch_name": "CompCopyS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1450, "y1": 40, "x2": 1400, "y2": 40,
                             "sketch_name": "CompCopyS"}, "ok", None),
    ("sketch_copy", {"sketch_name": "CompCopyS", "entities": "line:0,line:1,line:2", "dy": 60},
     lambda p: p.get("new_curves") == ["line:3", "line:4", "line:5"]
     and p.get("curve_count_after", 0) - p.get("curve_count_before", 0) == 3
     and "new_curves_complete" not in p, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # autoConstrain takes the whole sketch: a loose rectangle flips is_fully_constrained to true,
    # and the added counts are read off the sketch's own collections.
    ("sketch_create", {"plane": "xy", "name": "AutoCon"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 600, "y1": 560, "x2": 700, "y2": 600,
                             "sketch_name": "AutoCon"}, "ok", None),
    ("sketch_get", {"sketch_name": "AutoCon"},
     lambda p: p.get("is_fully_constrained") is False, None),
    ("sketch_constrain", {"constraint": "auto", "sketch_name": "AutoCon"},
     lambda p: p.get("added_constraints", 0) + p.get("added_dimensions", 0) > 0
     and p.get("is_fully_constrained") is True, None),
    # a re-run on the constrained sketch adds nothing and is a clean no-op, not an error.
    ("sketch_constrain", {"constraint": "auto", "sketch_name": "AutoCon"},
     lambda p: p.get("added_dimensions") == 0 and p.get("added_constraints") == 0
     and p.get("is_fully_constrained") is True, None),
    # rectangular_pattern with distance_type='extent': 'distance' is the pattern's TOTAL span, so
    # three instances 90 mm across sit at x 600 / 645 / 690 - the landed centre is the verdict, and
    # a spacing read in centimetres would put the last one at 609.
    ("sketch_create", {"plane": "xy", "name": "PatExtent"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 600, "cy": 640, "radius": 5,
                             "sketch_name": "PatExtent"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 660, "x2": 700, "y2": 660,
                             "sketch_name": "PatExtent"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 660, "x2": 600, "y2": 760,
                             "sketch_name": "PatExtent"}, "ok", None),
    ("sketch_constrain", {"constraint": "rectangular_pattern", "sketch_name": "PatExtent",
                          "entities": "circle:0", "entity_one": "line:0", "entity_two": "line:1",
                          "quantity": 3, "distance": 90, "quantity_two": 1, "distance_two": 10,
                          "distance_type": "extent"},
     lambda p: p.get("distance_type") == "extent" and p.get("created_count", 0) == 2, None),
    ("sketch_get", {"sketch_name": "PatExtent", "include_entities": True},
     lambda p: abs(max((e.get("center") or {}).get("x", 0) for e in (p.get("entities") or [])
                       if e.get("type") == "circle") - 690.0) < 0.01, None),
    # per-instance suppression: a 3x2 pattern of one circle has 5 SUPPRESSIBLE instances (the
    # original does not count), and the flags are read back off the CREATED constraint. A suppressed
    # instance draws no curve, so 5 instances less 2 suppressed is 3 new curves - the count and the
    # landed flags together are what an echoed payload cannot fake.
    ("sketch_create", {"plane": "xy", "name": "PatSupp"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 600, "cy": 800, "radius": 4,
                             "sketch_name": "PatSupp"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 820, "x2": 700, "y2": 820,
                             "sketch_name": "PatSupp"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 820, "x2": 600, "y2": 920,
                             "sketch_name": "PatSupp"}, "ok", None),
    ("sketch_constrain", {"constraint": "rectangular_pattern", "sketch_name": "PatSupp",
                          "entities": "circle:0", "entity_one": "line:0", "entity_two": "line:1",
                          "quantity": 3, "quantity_two": 2, "distance": 20, "distance_two": 20,
                          "suppressed": [False, True, False, True, False]},
     lambda p: p.get("suppressed_applied") == [False, True, False, True, False]
     and p.get("created_count") == 3, None),
    # the N-1 length IS the input's contract: 6 flags for a 3x2 counts the original, and the guard
    # refuses it naming expected against got, before anything is created.
    ("sketch_constrain", {"constraint": "rectangular_pattern", "sketch_name": "PatSupp",
                          "entities": "circle:0", "entity_one": "line:0", "entity_two": "line:1",
                          "quantity": 3, "quantity_two": 2, "distance": 20, "distance_two": 20,
                          "suppressed": [False] * 6}, "refused", None),
    # a knob whose input object exists on ONE constraint only is refused elsewhere, never dropped.
    ("sketch_constrain", {"constraint": "horizontal", "sketch_name": "PatSupp",
                          "entity_one": "line:0", "dimension_strategy": "chain"}, "refused", None),
    # autoConstrain's four dimensioning-strategy setters are UNAVAILABLE on this build - the input
    # object refuses the assignment ("This API is not currently available") - so the request is
    # refused NAMING the knob that would not take, before autoConstrain runs and before anything is
    # constrained. Plain constraint='auto' still solves (the beats above), and this beat is the one
    # that fails loudly if a strategy is ever quietly dropped instead.
    ("sketch_create", {"plane": "xy", "name": "AutoStrat"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 740, "y1": 800, "x2": 840, "y2": 850,
                             "sketch_name": "AutoStrat"}, "ok", None),
    ("sketch_constrain", {"constraint": "auto", "sketch_name": "AutoStrat",
                          "dimension_strategy": "baseline", "linear_diameter_dims": "avoid"},
     "refused", None),
    # sketch_insert_svg: art from local disk into a FRESH sketch. importSVG ignores the file's own
    # width/height and viewBox - 1 SVG user unit lands as 1/96 inch times 'scale' - so the 36-unit
    # rectangle measures ~36 mm at scale=3.7795 and nothing else can produce that width. The art is
    # placed AT the sketch origin, so this empty-before sketch's own box IS the art's size.
    ("sketch_create", {"plane": "xy", "name": "SvgTarget"}, "ok", None),
    ("sketch_insert_svg", {"file_path": SVG_PATH, "sketch_name": "SvgTarget", "scale": 3.7795},
     lambda p: p.get("curves_added", 0) > 0 and p.get("sketch") == "SvgTarget"
     and 30 < (p.get("sketch_extent") or {}).get("width", 0) < 42, None),
    # SK-5's closure, through the TOOL: the 96-user-unit square at scale 1 is exactly one inch, and
    # the art lands Y-DOWN from the sketch origin - so this empty-before sketch measures 25.4 mm
    # square with its min y at -25.4. A y-up landing (or any scale drift) moves that number.
    ("sketch_create", {"plane": "xy", "name": "Svg96"}, "ok", None),
    ("sketch_insert_svg", {"file_path": SVG96_PATH, "sketch_name": "Svg96", "scale": 1},
     _svg96_extent, None),
    # importSVG RAISES on a path that is not a file and that raise rolls back the whole surrounding
    # transaction, so the miss is named before Fusion is touched.
    ("sketch_insert_svg", {"file_path": EXPORT_DIR + "/no_such_logo.svg",
                           "sketch_name": "SvgTarget"}, "refused", None),
    # Wave-3 sketch surface: the new curve kinds + dimension types, each asserting a read-back
    # the payload could not echo (the degree clamp, the wedge rule, the offset rotation, and the
    # API's own self-naming parallelism raises - all measured contracts).
    ("sketch_create", {"plane": "xy", "name": "W3Curves"}, "ok", None),
    # degree 5 over 3 control points: the API silently CLAMPS to n-1, and the payload publishes
    # the BUILT degree read off the spline - 2 here is a live read-back, not an echo of the 5.
    ("sketch_add_geometry", {"kind": "cv_spline", "points": [[740, 0], [760, 20], [780, 0]],
                             "degree": 5, "sketch_name": "W3Curves"},
     lambda p: p.get("degree") == 2, None),
    ("sketch_add_geometry", {"kind": "cv_spline",
                             "points": [[740, -40], [750, -20], [760, -40],
                                        [770, -20], [780, -40], [790, -20]],
                             "degree": 5, "sketch_name": "W3Curves"},
     lambda p: p.get("degree") == 5, None),
    # 'minor' omitted: the label reports the EFFECTIVE minor radius (major/2), never None.
    ("sketch_add_geometry", {"kind": "ellipse", "cx": 830, "cy": 0, "radius": 20,
                             "sketch_name": "W3Curves"},
     lambda p: "minor=10" in (p.get("drawn") or ""), None),
    # conic closed by its chord forms a profile that extrudes - the missing ref token is a
    # reference gap only, and this proves the note's modelling claim end to end.
    ("sketch_create", {"plane": "xy", "name": "W3Conic"}, "ok", None),
    ("sketch_add_geometry", {"kind": "conic", "x1": 740, "y1": 60, "x2": 780, "y2": 60,
                             "cx": 760, "cy": 90, "rho": 0.6, "sketch_name": "W3Conic"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 740, "y1": 60, "x2": 780, "y2": 60,
                             "sketch_name": "W3Conic"}, "ok", None),
    ("model_extrude", {"sketch_name": "W3Conic", "profile_index": 0, "distance": 5}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "W3Earc"}, "ok", None),
    ("sketch_add_geometry", {"kind": "elliptical_arc", "cx": 840, "cy": 80, "radius": 30,
                             "minor": 15, "sweep_deg": 180, "sketch_name": "W3Earc"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 870, "y1": 80, "x2": 810, "y2": 80,
                             "sketch_name": "W3Earc"}, "ok", None),
    ("model_extrude", {"sketch_name": "W3Earc", "profile_index": 0, "distance": 5}, "ok", None),
    # the angular wedge rule: a horizontal and a 60 deg line crossing far from the origin. The
    # measured contract dims the wedge FACING THE SKETCH ORIGIN - 60 deg, not the 120 supplement.
    ("sketch_create", {"plane": "xy", "name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 900, "y1": 100, "x2": 920, "y2": 100,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 905, "y1": 91.34, "x2": 915, "y2": 108.66,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", {"dim_type": "angle", "entity_one": "line:0", "entity_two": "line:1",
                          "sketch_name": "W3Dims"},
     lambda p: "deg" in (p.get("value") or "")
     and abs(float((p.get("value") or "0 x").split()[0]) - 60) < 0.1, None),
    # offset with a NON-parallel second line: the constraint ROTATES it parallel (geometry moves,
    # no raise) and the note says so.
    ("sketch_add_geometry", {"kind": "line", "x1": 940, "y1": 100, "x2": 960, "y2": 100,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 940, "y1": 110, "x2": 960, "y2": 113,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", {"dim_type": "offset", "entity_one": "line:2", "entity_two": "line:3",
                          "sketch_name": "W3Dims"},
     lambda p: "ROTAT" in (p.get("note") or ""), None),
    # linear_diameter with the same shape REFUSES - the API's own parallelism sentence surfaces.
    ("sketch_add_geometry", {"kind": "line", "x1": 980, "y1": 100, "x2": 1000, "y2": 100,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 980, "y1": 110, "x2": 1000, "y2": 114,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", {"dim_type": "linear_diameter", "entity_one": "line:4",
                          "entity_two": "line:5", "sketch_name": "W3Dims"}, "refused", None),
    # line/point vs a MODEL face: the ShellCap outer -X wall sits on the x=400 plane, 620 mm from
    # a line at x=1020. The value is read back off the parameter; the surface label is the
    # RESOLVED entity, so 'BRepFace' proves the payload is not echoing the handle string.
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [400, 15, 10],
                       "max_results": 1}, "ok", _fg("w3_wall")),
    ("sketch_add_geometry", {"kind": "line", "x1": 1020, "y1": 100, "x2": 1020, "y2": 140,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", lambda c: {"dim_type": "line_to_surface", "entity_one": "line:6",
                                    "surface": _ctx_get(c, "w3_wall", "shell wall"),
                                    "sketch_name": "W3Dims"},
     lambda p: "mm" in (p.get("value") or "")
     and abs(float((p.get("value") or "0 x").split()[0]) - 620) < 0.1
     and p.get("surface") == "BRepFace", None),
    # a line NOT parallel to that wall refuses with the API's self-naming error.
    ("sketch_add_geometry", {"kind": "line", "x1": 1040, "y1": 100, "x2": 1060, "y2": 100,
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", lambda c: {"dim_type": "line_to_surface", "entity_one": "line:7",
                                    "surface": _ctx_get(c, "w3_wall", "shell wall"),
                                    "sketch_name": "W3Dims"}, "refused", None),
    # anchored on line:7 (undimensioned - the refused line_to_surface left it free): dimensioning
    # line:6's own endpoint against the same wall it is dimensioned to over-constrains the sketch.
    ("sketch_dimension", lambda c: {"dim_type": "point_to_surface", "entity_one": "line:7:start",
                                    "surface": _ctx_get(c, "w3_wall", "shell wall"),
                                    "sketch_name": "W3Dims"},
     lambda p: p.get("surface") == "BRepFace", None),
    # shared-resolver regression: the point dim's resolver allows curved faces; the constrain
    # tool rides the same _inputs.resolve_surface, so a cylinder accepted here and refused for
    # line_on_surface pins the allow_curved split. Fresh sketch: the origin point is point:0,
    # so the drawn point is point:1.
    ("sketch_create", {"plane": "xy", "name": "W3Pt"}, "ok", None),
    ("sketch_add_geometry", {"kind": "point", "cx": 1080, "cy": 100,
                             "sketch_name": "W3Pt"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1080, "y1": 120, "x2": 1100, "y2": 120,
                             "sketch_name": "W3Pt"}, "ok", None),
    ("sketch_constrain", lambda c: {"constraint": "coincident_to_surface", "entity_one": "point:1",
                                    "surface": _ctx_get(c, "post_wall", "thread post wall"),
                                    "sketch_name": "W3Pt"},
     lambda p: p.get("surface") == "BRepFace", None),
    ("sketch_constrain", lambda c: {"constraint": "line_on_surface", "entity_one": "line:0",
                                    "surface": _ctx_get(c, "post_wall", "thread post wall"),
                                    "sketch_name": "W3Pt"}, "refused", None),
    # the coincident TRAP, on the success path where the caller who meant "centre this here" is:
    # addCoincident(point, circle) succeeds and lands the point ON the rim, so the note has to say
    # so - and it must NOT say so when the operand was a POINT, where the point really is centred.
    ("sketch_create", {"plane": "xy", "name": "CoincTrap"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1500, "cy": 0, "radius": 20,
                             "sketch_name": "CoincTrap"}, "ok", None),
    ("sketch_add_geometry", {"kind": "point", "cx": 1560, "cy": 0, "sketch_name": "CoincTrap"},
     "ok", None),
    ("sketch_add_geometry", {"kind": "point", "cx": 1560, "cy": 30, "sketch_name": "CoincTrap"},
     "ok", None),
    ("sketch_constrain", {"constraint": "coincident", "entity_one": "point:2",
                          "entity_two": "circle:0", "sketch_name": "CoincTrap"},
     lambda p: "ON that curve" in (p.get("note") or ""), None),
    ("sketch_constrain", {"constraint": "coincident", "entity_one": "point:3",
                          "entity_two": "point:1", "sketch_name": "CoincTrap"},
     lambda p: "ON that curve" not in (p.get("note") or ""), None),
    # sketch_set_text's PATH layouts: one scratch sketch holding a line and a closed circle, then
    # text laid ALONG each and FITTED to the line. 'definition_type' is the created text's own
    # objectType and 'mode_verified' says whether it matches the mode asked for, so a text that
    # landed in another layout cannot pass as this one.
    ("sketch_create", {"plane": "xy", "name": "TextPaths"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1200, "y1": 0, "x2": 1300, "y2": 0,
                             "sketch_name": "TextPaths"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1250, "cy": 60, "radius": 25,
                             "sketch_name": "TextPaths"}, "ok", None),
    ("sketch_set_text", {"text": "ALONG", "sketch_name": "TextPaths", "create": True,
                         "mode": "along_path", "path": "line:0", "height": 5},
     lambda p: p.get("mode_verified") is True
     and str(p.get("definition_type") or "").endswith("AlongPathTextDefinition"), None),
    # a CLOSED circle wraps the text right around the hole it marks - the headline path case.
    ("sketch_set_text", {"text": "M8 CLEARANCE", "sketch_name": "TextPaths", "create": True,
                         "mode": "along_path", "path": "circle:0", "align": "center", "height": 4},
     lambda p: p.get("mode_verified") is True, None),
    # fit_on_path spaces the characters over the whole path itself, so the payload carries neither
    # 'align' nor 'character_spacing' - its definition object has no slot for either.
    ("sketch_set_text", {"text": "FIT", "sketch_name": "TextPaths", "create": True,
                         "mode": "fit_on_path", "path": "line:0", "height": 5},
     lambda p: str(p.get("definition_type") or "").endswith("FitOnPathTextDefintion")
     and "align" not in p and "character_spacing" not in p, None),
    # a model EDGE handle as the path: the placement call accepts it and Fusion then rejects the
    # add, so the guard refuses it up front and points at sketch_project.
    ("sketch_set_text", lambda c: {"text": "EDGE", "sketch_name": "TextPaths", "create": True,
                                   "mode": "along_path",
                                   "path": _ctx_get(c, "cd_edge", "a model edge handle")},
     "refused", None),
    # cross-mode inputs: each is refused BY NAME rather than silently dropped, and nothing is created.
    ("sketch_set_text", {"text": "X", "sketch_name": "TextPaths", "create": True,
                         "mode": "fit_on_path", "path": "line:0", "align": "center"}, "refused", None),
    ("sketch_set_text", {"text": "X", "sketch_name": "TextPaths", "create": True,
                         "mode": "along_path", "path": "line:0", "x": 10}, "refused", None),
    ("sketch_set_text", {"text": "X", "sketch_name": "TextPaths", "create": True,
                         "mode": "multi_line", "path": "line:0"}, "refused", None),
    # the layout inputs shape NEW text only: passing one to an EDIT is refused, never ignored.
    ("sketch_set_text", {"text": "FIT", "sketch_name": "TextPaths", "angle_deg": 15},
     "refused", None),
    # FONT: the one input that reaches the API twice - onto the INPUT before a create, onto the
    # SketchText itself on an edit. No API lists or validates the legal names, so Fusion's own
    # "invalid input font name" raise IS the whole check, and each refusal hands that sentence on
    # with the name it was given. Its own scratch sketch carries the whole run.
    ("sketch_create", {"plane": "xy", "name": "FontProbe"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1200, "y1": 200, "x2": 1300, "y2": 200,
                             "sketch_name": "FontProbe"}, "ok", None),
    # the font is read back off the LANDED text, not off the input, so 'font' here is what the
    # created text reports - and nothing sits in 'requested', which is where an unread value goes.
    ("sketch_set_text", {"text": "FONT", "sketch_name": "FontProbe", "create": True,
                         "x": 1200, "y": 160, "height": 6, "font_name": "Arial"},
     lambda p: p.get("font") == "Arial" and p.get("sketch_text_count") == 1
     and "requested" not in p, None),
    # the same font through a setAs* PLACEMENT: it is applied to the input before the placement
    # call and survives it, which is what makes the along-path text report it too.
    ("sketch_set_text", {"text": "ALONGFONT", "sketch_name": "FontProbe", "create": True,
                         "mode": "along_path", "path": "line:0", "height": 5,
                         "font_name": "Arial"},
     lambda p: p.get("font") == "Arial" and p.get("sketch_text_count") == 2, None),
    # a font this machine does not carry: the create raises at add() and the refusal names it.
    ("sketch_set_text", {"text": "NOFONT", "sketch_name": "FontProbe", "create": True,
                         "x": 1200, "y": 140, "height": 6,
                         "font_name": "ZzNoSuchFont_MCP_Probe"}, "refused", None),
    # font names are CASE-SENSITIVE at the API, so 'arial' is as unknown as any other miss.
    ("sketch_set_text", {"text": "NOFONT", "sketch_name": "FontProbe", "create": True,
                         "x": 1200, "y": 140, "height": 6, "font_name": "arial"}, "refused", None),
    # the count is the proof the two refusals created nothing: this is the THIRD text in the
    # sketch. It carries the no-font regression too - omit 'font_name' and no 'font' key is
    # published at all, on the payload or on a record.
    ("sketch_set_text", {"text": "NOFONTKEY", "sketch_name": "FontProbe", "create": True,
                         "x": 1200, "y": 120, "height": 6},
     lambda p: p.get("sketch_text_count") == 3 and "font" not in p, None),
    # EDITING one text: the font goes on FIRST and each changed record carries the font that text
    # reports back beside the string that landed.
    ("sketch_set_text", {"text": "FONT2", "sketch_name": "FontProbe", "index": 0,
                         "font_name": "Arial"},
     lambda p: p["changed"][0].get("font") == "Arial"
     and p["changed"][0].get("after") == "FONT2", None),
    # the unknown name on an EDIT: because the font is applied ahead of the string, the refusal
    # leaves this text's string untouched as well.
    ("sketch_set_text", {"text": "FONT3", "sketch_name": "FontProbe", "index": 0,
                         "font_name": "ZzNoSuchFont_MCP_Probe"}, "refused", None),
    # the next edit answers normally, and its 'before' is what proves the refused call wrote
    # nothing - the string is still the one the successful edit left.
    ("sketch_set_text", {"text": "FONT4", "sketch_name": "FontProbe", "index": 0},
     lambda p: p["changed"][0].get("before") == "FONT2"
     and p["changed"][0].get("after") == "FONT4" and "font" not in p["changed"][0], None),
    # sketch text is deleted by the SAME index sketch_set_text edits by: one text in its own sketch,
    # deleted as 'text:0'. The deleted string and the collection count read back off the sketch are
    # the verdict - a delete that removed nothing is an error, never a false ok.
    ("sketch_create", {"plane": "xy", "name": "TextDel"}, "ok", None),
    ("sketch_set_text", {"text": "SCRAP", "sketch_name": "TextDel", "create": True,
                         "x": 1200, "y": 100, "height": 5}, "ok", None),
    # SketchTexts.add APPENDS, so the SECOND text is 'text:1' - and deleting that index has to take
    # the second one, never the first. The deleted STRING is what separates the two.
    ("sketch_set_text", {"text": "SCRAP2", "sketch_name": "TextDel", "create": True,
                         "x": 1200, "y": 80, "height": 5}, "ok", None),
    ("sketch_delete_entity", {"sketch_name": "TextDel", "target": "text:1"},
     lambda p: p.get("text") == "SCRAP2" and p.get("texts_before") == 2
     and p.get("texts_after") == 1, None),
    ("sketch_delete_entity", {"sketch_name": "TextDel", "target": "text:0"},
     lambda p: p.get("text") == "SCRAP" and p.get("texts_before") == 1
     and p.get("texts_after") == 0, None),
    # the emptied sketch has no text at that index any more - the refusal names the index and count.
    ("sketch_delete_entity", {"sketch_name": "TextDel", "target": "text:0"}, "refused", None),
    # THE TEXT READ-BACK (the S6 gap): sketch_get's X-ray lists each SketchText at its text:<i>
    # address with the string, the FONT (fontName is API-readable), the height in display units,
    # and a sketch-space bounding box. FontProbe's final state pins all three record shapes at
    # once: text:0 was edited to FONT4 (its Arial ride-along from the FONT2 edit stays), text:1 is
    # the along-path ALONGFONT, text:2 was created with NO font and reads font None.
    ("sketch_get", {"sketch_name": "FontProbe"},
     lambda p: (p.get("counts") or {}).get("texts") == 3 and "entities" not in p, None),
    # text:2 was created with NO font_name and still reads a real font (measured: the platform
    # gives every text the app default) - so 'font' is a non-empty string on all three records.
    ("sketch_get", {"sketch_name": "FontProbe", "include_entities": True},
     lambda p: (lambda t: [r["id"] for r in t] == ["text:0", "text:1", "text:2"]
                and t[0].get("text") == "FONT4" and t[0].get("font") == "Arial"
                and isinstance(t[0].get("height"), (int, float)) and t[0]["height"] > 0
                and t[1].get("text") == "ALONGFONT"
                and t[2].get("text") == "NOFONTKEY"
                and isinstance(t[2].get("font"), str) and t[2]["font"]
                and "min" in (t[0].get("bounding_box") or {}))
     ([e for e in p.get("entities", []) if e.get("type") == "text"]), None),
    # THE SLOT FAMILY, one scratch sketch per shape in a clear band so every count is absolute.
    # 'radius' is the HALF width throughout (the label carries the full width), each tailed
    # constructor takes its tail POSITIONALLY, and the ladders differ per kind - which is what the
    # cross-kind refusals below pin.
    ("sketch_create", {"plane": "xy", "name": "SlotA"}, "ok", None),
    # a three-point arc slot is built entirely out of SketchArcs - five of them, and its closed
    # outline forms a profile. The only dimension it can create is the width one.
    ("sketch_add_geometry", {"kind": "three_point_arc_slot", "x1": 1700, "y1": 900,
                             "x2": 1800, "y2": 900, "cx": 1750, "cy": 930, "radius": 5,
                             "create_width_dimension": True, "sketch_name": "SlotA"},
     lambda p: p.get("curves_added") == 5 and p["sketch"]["arc_count"] == 5
     and p["sketch"]["profile_count"] >= 1
     and "three_point_arc_slot" in (p.get("drawn") or "") and "w=10" in (p.get("drawn") or ""), None),
    # its arc is fixed by its three points, so it has no radius or angle to dimension - the flag
    # that belongs to the centre-point kind is refused by name and points there.
    ("sketch_add_geometry", {"kind": "three_point_arc_slot", "x1": 1700, "y1": 900,
                             "x2": 1800, "y2": 900, "cx": 1750, "cy": 930, "radius": 5,
                             "create_angle_dimension": True, "sketch_name": "SlotA"},
     "refused", None),
    # the centre-point arc slot in its four-argument form: centre, start, end, width.
    ("sketch_create", {"plane": "xy", "name": "SlotB"}, "ok", None),
    ("sketch_add_geometry", {"kind": "center_point_arc_slot", "cx": 1700, "cy": 1000,
                             "x1": 1750, "y1": 1000, "x2": 1700, "y2": 1050, "radius": 5,
                             "sketch_name": "SlotB"},
     lambda p: p.get("curves_added") == 5, None),
    # the full ladder: a supplied arc_radius overrides the centre-to-start distance and the angle
    # takes a unit-bearing expression, then each of the three flags gates its OWN dimension - so
    # width + angle asked for and radius declined must land exactly two dimensions.
    ("sketch_create", {"plane": "xy", "name": "SlotC"}, "ok", None),
    ("sketch_add_geometry", {"kind": "center_point_arc_slot", "cx": 1700, "cy": 1100,
                             "x1": 1750, "y1": 1100, "x2": 1700, "y2": 1150, "radius": 5,
                             "arc_radius": 30, "angle_deg": 45, "create_width_dimension": True,
                             "create_radius_dimension": False, "create_angle_dimension": True,
                             "sketch_name": "SlotC"},
     lambda p: p.get("curves_added") == 5, None),
    ("sketch_get", {"sketch_name": "SlotC", "include_entities": True},
     lambda p: p.get("dimension_count") == 2
     and any("diameter" in (d.get("type") or "") for d in p["dimensions"])
     and any("angular" in (d.get("type") or "") for d in p["dimensions"])
     and not any("radial" in (d.get("type") or "") for d in p["dimensions"]), None),
    # an OVERALL slot measures tip to tip: its two points are the outer extremes, so the cap arc
    # centres land inset by the half width - 60 mm tip to tip from centres 52 mm apart.
    ("sketch_create", {"plane": "xy", "name": "SlotD"}, "ok", None),
    ("sketch_add_geometry", {"kind": "overall_slot", "x1": 1700, "y1": 1200, "x2": 1760, "y2": 1200,
                             "radius": 4, "sketch_name": "SlotD"},
     lambda p: p.get("curves_added") == 3 and "w=8" in (p.get("drawn") or ""), None),
    ("sketch_get", {"sketch_name": "SlotD", "include_entities": True},
     lambda p: sorted(round(e["center"]["x"], 3) for e in p["entities"] if e["type"] == "arc")
     == [1704.0, 1756.0], None),
    # the length and the angle are VALUES, not flags: passing either creates its own dimension and
    # adds the fourth line, while the width dimension stays gated on its flag.
    ("sketch_create", {"plane": "xy", "name": "SlotE"}, "ok", None),
    ("sketch_add_geometry", {"kind": "overall_slot", "x1": 1700, "y1": 1300, "x2": 1760, "y2": 1300,
                             "radius": 4, "slot_length": 40, "angle_deg": 30,
                             "create_width_dimension": True, "sketch_name": "SlotE"},
     lambda p: p.get("curves_added") == 4, None),
    ("sketch_get", {"sketch_name": "SlotE", "include_entities": True},
     lambda p: p.get("dimension_count") == 3
     and any("diameter" in (d.get("type") or "") for d in p["dimensions"])
     and any("linear" in (d.get("type") or "") and abs((d.get("value") or 0) - 40.0) < 1e-3
             for d in p["dimensions"])
     and any("angular" in (d.get("type") or "") and "30" in (d.get("expression") or "")
             for d in p["dimensions"]), None),
    # the flag ALONE, with no tail: three lines and exactly the one dimension it asked for.
    ("sketch_create", {"plane": "xy", "name": "SlotF"}, "ok", None),
    ("sketch_add_geometry", {"kind": "overall_slot", "x1": 1700, "y1": 1400, "x2": 1760, "y2": 1400,
                             "radius": 4, "create_width_dimension": True, "sketch_name": "SlotF"},
     lambda p: p.get("curves_added") == 3, None),
    ("sketch_get", {"sketch_name": "SlotF", "include_entities": True},
     lambda p: p.get("dimension_count") == 1, None),
    # a CENTRE-point slot's length is the HALF length, centre to cap centre, and it is forwarded
    # unhalved - so a cap centre lands exactly on the second point and the dimension reads 25.
    ("sketch_create", {"plane": "xy", "name": "SlotG"}, "ok", None),
    ("sketch_add_geometry", {"kind": "center_point_slot", "x1": 1700, "y1": 1500,
                             "x2": 1725, "y2": 1500, "radius": 3, "slot_length": 25,
                             "sketch_name": "SlotG"},
     lambda p: "half_len=25" in (p.get("drawn") or ""), None),
    ("sketch_get", {"sketch_name": "SlotG", "include_entities": True},
     lambda p: any(abs(e["center"]["x"] - 1725) < 1e-3 and abs(e["center"]["y"] - 1500) < 1e-3
                   for e in p["entities"] if e["type"] == "arc")
     and any("linear" in (d.get("type") or "") and abs((d.get("value") or 0) - 25.0) < 1e-3
             for d in p["dimensions"]), None),
    # an angle with no length has nothing to sit on: refused naming both.
    ("sketch_add_geometry", {"kind": "overall_slot", "x1": 1700, "y1": 1400, "x2": 1760, "y2": 1400,
                             "radius": 4, "angle_deg": 30, "sketch_name": "SlotF"}, "refused", None),
    # the linear kinds have no angle FLAG - the angular dimension comes from angle_deg itself.
    ("sketch_add_geometry", {"kind": "overall_slot", "x1": 1700, "y1": 1400, "x2": 1760, "y2": 1400,
                             "radius": 4, "slot_length": 40, "create_angle_dimension": True,
                             "sketch_name": "SlotF"}, "refused", None),
    # each cross-kind input is refused pointing at the kind that DOES carry it: arc_radius belongs
    # to the centre-point ARC slot, slot_length to the straight ones - and the three-point arc slot
    # has no radius argument at all, so its refusal must not offer arc_radius as the remedy.
    ("sketch_add_geometry", {"kind": "center_point_slot", "x1": 1700, "y1": 1500,
                             "x2": 1725, "y2": 1500, "radius": 3, "arc_radius": 30,
                             "sketch_name": "SlotG"}, "refused", None),
    ("sketch_add_geometry", {"kind": "center_point_arc_slot", "cx": 1700, "cy": 1000,
                             "x1": 1750, "y1": 1000, "x2": 1700, "y2": 1050, "radius": 5,
                             "slot_length": 40, "sketch_name": "SlotB"}, "refused", None),
    ("sketch_add_geometry", {"kind": "three_point_arc_slot", "x1": 1700, "y1": 900,
                             "x2": 1800, "y2": 900, "cx": 1750, "cy": 930, "radius": 5,
                             "slot_length": 40, "sketch_name": "SlotA"}, "refused", None),
    # the legacy centre-to-centre slot takes two centres and a width and nothing else: a tail would
    # be dropped, so it is refused - and the bare call still draws.
    ("sketch_create", {"plane": "xy", "name": "SlotH"}, "ok", None),
    ("sketch_add_geometry", {"kind": "slot", "x1": 1700, "y1": 1600, "x2": 1760, "y2": 1600,
                             "radius": 4, "slot_length": 40, "sketch_name": "SlotH"},
     "refused", None),
    # the legacy form's own census: addCenterToCenterSlot lands 5 curves - 2 solid lines, 1
    # CONSTRUCTION line (the centre-to-centre one) and 2 arc caps - and 'curves_added' counts the
    # LINE collection's delta, so it reads 3. The note has to say which 5, because the number alone
    # reads like a 3-curve slot.
    ("sketch_add_geometry", {"kind": "slot", "x1": 1700, "y1": 1600, "x2": 1760, "y2": 1600,
                             "radius": 4, "sketch_name": "SlotH"},
     lambda p: p.get("curves_added") == 3
     and "2 solid SketchLines" in (p.get("note") or "")
     and "1 CONSTRUCTION SketchLine" in (p.get("note") or "")
     and "2 SketchArc end caps" in (p.get("note") or ""), None),
    # the independent read: three lines of which EXACTLY ONE is construction, plus the two arc caps.
    ("sketch_get", {"sketch_name": "SlotH", "include_entities": True},
     lambda p: len([e for e in p["entities"] if e["type"] == "line"]) == 3
     and len([e for e in p["entities"] if e["type"] == "line" and e.get("construction")]) == 1
     and len([e for e in p["entities"] if e["type"] == "arc"]) == 2, None),
    # a POLYGON is built by the same SketchLines factory, so its side count is the delta.
    ("sketch_create", {"plane": "xy", "name": "PolyHex"}, "ok", None),
    ("sketch_add_geometry", {"kind": "polygon", "cx": 1700, "cy": 1700, "radius": 20, "sides": 6,
                             "sketch_name": "PolyHex"},
     lambda p: p.get("curves_added") == 6, None),
    # design_remove_feature: cast a scratch body, remove it (the census is the verdict), then
    # delete the Remove feature - the body comes back, which is the reversibility the note claims.
    ("model_create_component", {"name": "RmScratch", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "RmS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1100, "y1": 0, "x2": 1120, "y2": 20,
                             "sketch_name": "RmS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RmS", "profile_index": 0, "distance": 5}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("design_remove_feature", {"body": "RmScratch"},
     lambda p: p.get("target_kind") == "body" and p.get("feature_read_back") is True,
     ("rm_feat", lambda p: p["feature"])),
    ("design_delete_feature", lambda c: {"feature": _ctx_get(c, "rm_feat", "remove feature")},
     "ok", None),
    # the body is back: a component with no body has no faces, so exactly one match discriminates.
    ("find_geometry", {"target": "RmScratch", "kind": "planar_face", "max_results": 1},
     lambda p: len(p.get("matches") or []) == 1, None),
    # the occurrence variant of the same round trip.
    ("design_remove_feature", {"occurrence": "RmScratch:1"},
     lambda p: p.get("target_kind") == "occurrence" and p.get("feature_read_back") is True,
     ("rm_feat2", lambda p: p["feature"])),
    ("design_delete_feature", lambda c: {"feature": _ctx_get(c, "rm_feat2", "remove feature")},
     "ok", None),
    ("model_inspect", {"target": "RmScratch:1"}, "ok", None),
    # model_emboss: a raise then an engrave on one scratch block's top face. The profiles are drawn
    # on a datum plane COINCIDENT with that face, so each sketch holds exactly one profile (an
    # on-face sketch auto-projects the face boundary and would offer two). The verdict is the
    # measured volume direction: 'mode' echoes the sign of the depth and the call is refused when
    # the material moved the other way.
    *_box("EmbossBlock", ox=600, oy=100),
    _watch("EmbossBlock:1"),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 10, "name": "EmbPlane"},
     "ok", None),
    ("find_geometry", {"target": "EmbossBlock", "kind": "planar_face", "nearest_to": [610, 110, 10],
                       "max_results": 1}, "ok", _fg("emb_top")),
    ("sketch_create", {"plane": "EmbPlane", "name": "EmbRaise"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 605, "cy": 105, "radius": 3,
                             "sketch_name": "EmbRaise"}, "ok", None),
    ("sketch_get", {"sketch_name": "EmbRaise"}, "ok", _prof("emb_prof_up")),
    ("model_emboss", lambda c: {"profiles": [_ctx_get(c, "emb_prof_up", "emboss profile")],
                                "faces": [_ctx_get(c, "emb_top", "emboss block top")], "depth": 2},
     lambda p: p.get("mode") == "raise" and p.get("volume_delta_cm3", 0) > 0,
     ("emb_feat", lambda p: p["feature"])),
    # the top face was re-cut by the raise, so the engrave takes a FRESH handle for it.
    ("find_geometry", {"target": "EmbossBlock", "kind": "planar_face", "nearest_to": [610, 110, 10],
                       "max_results": 1}, "ok", _fg("emb_top2")),
    ("sketch_create", {"plane": "EmbPlane", "name": "EmbCut"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 615, "cy": 115, "radius": 3,
                             "sketch_name": "EmbCut"}, "ok", None),
    ("sketch_get", {"sketch_name": "EmbCut"}, "ok", _prof("emb_prof_down")),
    ("model_emboss", lambda c: {"profiles": [_ctx_get(c, "emb_prof_down", "engrave profile")],
                                "faces": [_ctx_get(c, "emb_top2", "emboss block top")], "depth": -2},
     lambda p: p.get("mode") == "engrave" and p.get("volume_delta_cm3", 0) < 0, None),
    ("model_emboss", lambda c: {"profiles": [_ctx_get(c, "emb_prof_up", "emboss profile")],
                                "faces": [_ctx_get(c, "emb_top2", "emboss block top")], "depth": 0},
     "refused", None),
    # model_mirror's FEATURE mode, on the emboss the beat above just made - an EmbossFeature is the
    # class MEASURED as accepted by the input collection. The mirror plane runs down the block's own
    # middle so the mirrored emboss lands back ON the block; the body/volume census is the verdict,
    # since MirrorFeature.resultFeatures.count reads None even when the mirror mints geometry.
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": 610, "name": "EmbMirrorPlane"},
     "ok", None),
    ("model_mirror", lambda c: {"features": [_ctx_get(c, "emb_feat", "the emboss feature")],
                                "plane": "EmbMirrorPlane"},
     lambda p: p.get("mode") == "features"
     and (p.get("bodies_added") or p.get("volume_change_cm3")), None),
    # join is a BODY-mode setting and 'joined' is read off the created feature, never echoed. The
    # body is named through a FRESH face handle: the mirrored emboss re-cut the one taken above.
    ("find_geometry", {"target": "EmbossBlock", "kind": "planar_face", "nearest_to": [610, 110, 10],
                       "max_results": 1}, "ok", _fg("emb_body")),
    ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "emb_body", "the emboss block body")],
                                "plane": "xz", "join": True},
     lambda p: p.get("joined") is True, None),
    ("model_mirror", lambda c: {"features": [_ctx_get(c, "emb_feat", "the emboss feature")],
                                "plane": "EmbMirrorPlane", "join": True}, "refused", None),
    # model_replace_face: a scratch block whose top face is replaced by an OPEN sheet sitting above
    # it. The sheet is sloped, so the body's measured volume has to move - a replace that changed
    # nothing is an error, not a quiet ok.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "ReplBlock", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "ReplS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 660, "y1": 0, "x2": 690, "y2": 30,
                             "sketch_name": "ReplS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ReplS", "profile_index": 0, "distance": 20}, "ok", None),
    _watch("ReplBlock:1"),
    # the replacement rides its own component so one find_geometry names it without ambiguity, and
    # surface_extrude reads is_solid=false back off the result - which is what makes it a legal
    # target. Symmetric, so the sheet spans the block whichever way the extrude runs.
    ("model_create_component", {"name": "ReplRoof", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "ReplRoofS"}, "ok", None),
    # on the xz plane sketch +Y maps to world -Z, so y=-26 puts the sheet at world z=+26 - a FLAT
    # plane ABOVE the z0-20 block, the measured-computable replace shape (a tilted or below-the-
    # block sheet fails compute with ASM_REPL_FACE_FAILED).
    ("sketch_add_geometry", {"kind": "line", "x1": 655, "y1": -26, "x2": 695, "y2": -26,
                             "sketch_name": "ReplRoofS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "ReplRoofS", "distance": 80, "symmetric": True},
     lambda p: p.get("is_solid") is False, None),
    ("find_geometry", {"target": "ReplRoof", "kind": "planar_face", "max_results": 1}, "ok",
     _fg("repl_sheet")),
    # build the feature on the component that owns the BODY, not the one holding the sheet.
    ("design_activate_component", {"occurrence": "ReplBlock:1"}, "ok", None),
    ("find_geometry", {"target": "ReplBlock", "kind": "planar_face", "nearest_to": [675, 15, 20],
                       "max_results": 1}, "ok", _fg("repl_top")),
    # a clean parametric replace has a feature to read AND a measured volume move, so the payload
    # carries no 'effect_unverified' hedge - that key appears only when neither could be read.
    ("model_replace_face", lambda c: {"faces": [_ctx_get(c, "repl_top", "block top")],
                                      "target": _ctx_get(c, "repl_sheet", "the open roof sheet")},
     lambda p: p.get("replaced") is True and p.get("volume_delta_cm3") not in (None, 0)
     and "effect_unverified" not in p, None),
    # a SOLID face as the replacement target is refused: the target must be a surface face or body.
    ("find_geometry", {"target": "ReplBlock", "kind": "planar_face", "nearest_to": [675, 15, 0],
                       "max_results": 1}, "ok", _fg("repl_bottom")),
    ("find_geometry", {"target": "ReplBlock", "kind": "planar_face", "nearest_to": [660, 15, 10],
                       "max_results": 1}, "ok", _fg("repl_side")),
    ("model_replace_face", lambda c: {"faces": [_ctx_get(c, "repl_side", "block side")],
                                      "target": _ctx_get(c, "repl_bottom", "a solid face")},
     "refused", None),
    # model_pipe: a HOLLOW pipe on its own path (the wall is read back off the created feature),
    # then a HALF-path pipe whose bounding box proves path_fraction is a FRACTION - 20 mm of pipe on
    # a 40 mm path, not 0.5 mm - and finally the reverse extent refused on an OPEN path, which the
    # platform would otherwise swallow in silence.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeRun", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "PipeRunPath"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 720, "y1": 0, "x2": 720, "y2": 40,
                             "sketch_name": "PipeRunPath"}, "ok", None),
    # PipeFeature.startFaces/endFaces/sideFaces all read EMPTY on a freshly added hollow pipe, so
    # nothing on this build can say whether the ends are capped - and the payload publishes no
    # 'capped_ends' key rather than a guess dressed as a read.
    ("model_pipe", {"path": "sketch:PipeRunPath", "section_size": 10, "wall_thickness": 1.5},
     lambda p: p.get("hollow") is True and abs((p.get("wall_thickness") or 0) - 1.5) < 1e-6
     and "capped_ends" not in p, None),
    _watch("PipeRun:1"),
    ("model_create_component", {"name": "PipeHalf", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "PipeHalfPath"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 760, "y1": 0, "x2": 760, "y2": 40,
                             "sketch_name": "PipeHalfPath"}, "ok", None),
    ("model_pipe", {"path": "sketch:PipeHalfPath", "section_size": 6, "section_type": "square",
                    "path_fraction": 0.5}, "ok", None),
    # the occurrence bounding box counts BODIES only, so the path sketch cannot inflate this read.
    ("model_inspect", {"target": "PipeHalf:1"}, lambda p: abs(p.get("z", 0) - 20.0) < 1.0, None),
    ("model_pipe", {"path": "sketch:PipeRunPath", "section_size": 6, "path_fraction": 0.5,
                    "path_fraction_reverse": 0.4}, "refused", None),
    # a CUT scoped to named bodies: participantBodies is write-only on the created feature, so the
    # scope cannot be read back - the note says the list is what was REQUESTED rather than dressing
    # an echo up as a read-back. The block is built around the path so the cut has material to take.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeCut", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "PipeCutS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 800, "y1": -20, "x2": 840, "y2": 20,
                             "sketch_name": "PipeCutS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PipeCutS", "profile_index": 0, "distance": 20}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "PipeCutPath"}, "ok", None),
    # on an xz sketch +Y maps to world -Z, so this line runs through the block at world z=10, y=0,
    # entering and leaving it - a cut that removes real material.
    ("sketch_add_geometry", {"kind": "line", "x1": 790, "y1": -10, "x2": 850, "y2": -10,
                             "sketch_name": "PipeCutPath"}, "ok", None),
    ("model_pipe", {"path": "sketch:PipeCutPath", "section_size": 8, "operation": "cut",
                    "target_bodies": ["PipeCut"]},
     lambda p: "REQUESTED" in (p.get("note") or "") and p.get("scoped_to_bodies"), None),
    # BUILD_PATH's measured chaining rule, on two fixtures of its own. ONE seed handle is not one
    # edge: chaining follows TANGENT CONTINUITY and stops where that continuity breaks - a sharp
    # corner ends an open run, while a genuinely tangent loop chains the whole way round ([F52a],
    # and [F68] which corrected [F52b]: the earlier no-chaining reading came from a rig whose
    # junctions ran through fillet corner PATCHES, not from the loop being closed). Neither open
    # nor closed predicts the number, so the label's count is read off the BUILT path - these two
    # beats are what keep the wording honest for every consumer of the shared resolver (sweep /
    # pipe / path pattern / on-path datum).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TangentRun", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "TangentRunS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1900, "y1": 0, "x2": 1960, "y2": 60,
                             "sketch_name": "TangentRunS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TangentRunS", "profile_index": 0, "distance": 20},
     "ok", None),
    # ONE vertical edge rounded: the top rim reads line - arc - line, bounded by sharp corners.
    ("find_geometry", {"target": "TangentRun", "kind": "line_edge", "nearest_to": [1900, 0, 10],
                       "max_results": 1}, "ok", _fg("tr_corner")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "tr_corner", "the box corner edge")],
                                "radius": 8}, "ok", None),
    # a fillet's rim is an ARC (Arc3D), not a full circle - find_geometry keys its edge kinds off
    # the curve type, so 'circular_edge' does not match it and 'arc_edge' is the pick.
    ("find_geometry", {"target": "TangentRun", "kind": "arc_edge", "nearest_to": [1900, 0, 20],
                       "max_results": 1}, "ok", _fg("tr_arc")),
    ("find_geometry", {"target": "TangentRun", "kind": "line_edge", "nearest_to": [1940, 0, 20],
                       "max_results": 1}, "ok", _fg("tr_line")),
    # TWO handles are used EXACTLY - no chaining at all - and the label says which of the two rules
    # ran, so a list quietly chained into more edges could not report this.
    ("find_geometry", {"target": "TangentRun", "kind": "planar_face", "nearest_to": [1930, 30, 20],
                       "max_results": 1}, "ok", _fg("tr_body")),
    ("model_pattern_path", lambda c: {"bodies": [_ctx_get(c, "tr_body", "the tangent-run body")],
                                      "path": [_ctx_get(c, "tr_arc", "the fillet arc"),
                                               _ctx_get(c, "tr_line", "the tangent-adjacent line")],
                                      "quantity": 2, "distance": 6, "distance_type": "spacing"},
     lambda p: p.get("path") == "2 edge(s) from 2 handles, used exactly", None),
    # ONE seed on the OPEN run: the arc chains across both tangent connections, so the built path
    # holds MORE than the seed.
    ("model_pipe", lambda c: {"path": _ctx_get(c, "tr_arc", "the fillet arc"), "section_size": 3},
     lambda p: _path_count(p.get("path"), 1) > 1, None),
    # the CLOSED tangent loop: all four verticals rounded, so the top rim is 4 lines + 4 arcs, every
    # junction tangent. One seed chains the WHOLE loop - all 8 edges - and the built path reports
    # itself closed. Rounding the VERTICALS is what makes the junctions tangent: rounding the top
    # edges instead puts a corner patch at each junction and the chain stops there ([F68]).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TangentLoop", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "TangentLoopS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 2000, "y1": 0, "x2": 2060, "y2": 60,
                             "sketch_name": "TangentLoopS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TangentLoopS", "profile_index": 0, "distance": 20},
     "ok", None),
    # each vertical edge is the nearest line edge to its own corner at mid-height (10 mm away from
    # the two horizontals meeting there), so the four picks are unambiguous.
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2000, 0, 10],
                       "max_results": 1}, "ok", _fg("tl_e1")),
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2060, 0, 10],
                       "max_results": 1}, "ok", _fg("tl_e2")),
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2060, 60, 10],
                       "max_results": 1}, "ok", _fg("tl_e3")),
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2000, 60, 10],
                       "max_results": 1}, "ok", _fg("tl_e4")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "tl_e1", "loop corner 1"),
                                          _ctx_get(c, "tl_e2", "loop corner 2"),
                                          _ctx_get(c, "tl_e3", "loop corner 3"),
                                          _ctx_get(c, "tl_e4", "loop corner 4")],
                                "radius": 8}, "ok", None),
    ("find_geometry", {"target": "TangentLoop", "kind": "arc_edge",
                       "nearest_to": [2000, 0, 20], "max_results": 1}, "ok", _fg("tl_arc")),
    ("model_pipe", lambda c: {"path": _ctx_get(c, "tl_arc", "one arc of the closed tangent loop"),
                              "section_size": 3},
     lambda p: _measured("closed tangent loop: want 8 edges from 1 seed, closed",
                         {"path": p.get("path"), "path_closed": p.get("path_closed")},
                         _path_count(p.get("path"), 1) == 8
                         and p.get("path_closed") is True), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # Section view: cut through the gimbal center, then clear.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    ("view_section", {"action": "cut", "plane": "yz", "offset": 0}, "ok", None),
    ("view_screenshot", {"view": "front", "width": 500, "height": 400}, "ok", None),
    ("view_section", {"action": "clear"}, "ok", None),
    ("view_screenshot_multi", {"views": ["front", "top"], "width": 400, "height": 300}, "ok", None),
    # THE RASTER WRITER (NEW-13): file_path also writes the rendered PNG to disk - the extension is
    # appended, the landed file is verified non-zero, and path + size are published beside the
    # inline image. The fleet's only raster writer, which is what feeds drawing_insert_image.
    ("view_screenshot", {"view": "iso-top-right", "width": 400, "height": 300,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot"},
     lambda p: "w4_shot.png" in str(p) and "size_bytes=" in str(p), None),
    # a second write to the SAME path lands without a refusal - the overwrite behaviour that makes
    # this tool write-kind (and puts it behind the write guard below).
    ("view_screenshot", {"view": "iso-top-right", "width": 200, "height": 150,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot"},
     lambda p: "w4_shot.png" in str(p) and "size_bytes=" in str(p), None),
    ("view_screenshot", {"width": 200, "height": 150,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot",
                         "expect_document": "ZzNoSuchDocument"},
     _refused("active_document_changed"), None),
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
    # TIMELINE: roll back over the assembly, group a range, suppress and restore, then return the
    # marker to the end. Every beat reads the marker back, and the blast-radius refusal is exercised
    # WITHOUT the confirmation so nothing is discarded from the story.
    ("design_edit_timeline", {"action": "roll", "to": "previous"},
     lambda p: p.get("marker_position") == p.get("marker_position_before", -1) - 1, None),
    ("design_edit_timeline", {"action": "delete_after_marker"}, "refused", None),
    ("design_edit_timeline", {"action": "roll", "to": "end"},
     lambda p: p.get("rolled_back") == 0, None),
    ("design_edit_timeline", {"action": "roll", "feature": "NoSuchFeature"}, "refused", None),
    ("design_edit_timeline", {"action": "group", "feature": "ScratchDim",
                              "end_feature": "ScratchDim"}, "refused", None),
    # ATTRIBUTES: tag a real timeline feature (the carrier hub plane the skeleton built), re-tag it,
    # then remove the tag. An attribute reached through the timeline lives on the ENTITY the item
    # wraps, and every beat reads back both the value on that entity and the design-wide census of
    # the group/name pair - which is what turns the delete into a verdict instead of a claim.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "CarrierHubPlane",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note",
                              "attribute_value": "beat-1"},
     lambda p: p.get("value") == "beat-1" and p.get("design_matches", 0) >= 1
     and "previous_value" not in p, None),
    # add() on an existing group/name UPDATES in place, so the value it replaced is readable only
    # before the call - and it is disclosed rather than lost.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "CarrierHubPlane",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note",
                              "attribute_value": "beat-2"},
     lambda p: p.get("previous_value") == "beat-1" and p.get("value") == "beat-2", None),
    # this tool's own wire bound on the value, refused with the length that broke it.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "CarrierHubPlane",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note",
                              "attribute_value": "x" * 10001}, "refused", None),
    # a leading 're:' turns the attribute search into a REGULAR EXPRESSION instead of naming this
    # literal group, so it is refused rather than silently matching something else.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "CarrierHubPlane",
                              "attribute_group": "re:sweep", "attribute_name": "note",
                              "attribute_value": "beat-3"}, "refused", None),
    ("design_edit_timeline", {"action": "delete_attribute", "feature": "CarrierHubPlane",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note"},
     lambda p: p.get("attribute_deleted") is True and p.get("deleted_value") == "beat-2"
     and p.get("design_matches") == 0, None),
    # the same delete again has nothing to remove, and says so naming the group/name pair.
    ("design_edit_timeline", {"action": "delete_attribute", "feature": "CarrierHubPlane",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note"},
     "refused", None),
    # THE 'name@index' FORM, the one a FeatureRef refusal hands back when a name is ambiguous. It is
    # resolved by reading each timeline object's OWN .index - the same number design_get publishes -
    # never by position in a list, so the index taken from this read is the index that must resolve.
    # the slice is a DICT (marker_position / count / summary / groups / timeline) and the ordered
    # rows sit under its own 'timeline' key - each a terse {index, name, type}.
    ("design_get", {"include": ["timeline"]},
     lambda p: any(r.get("name") == "CarrierHubPlane" for r in p["timeline"]["timeline"]),
     ("hub_index", lambda p: next(r["index"] for r in p["timeline"]["timeline"]
                                  if r["name"] == "CarrierHubPlane"))),
    ("design_edit_timeline", lambda c: {
        "action": "set_attribute",
        "feature": "CarrierHubPlane@{0}".format(_ctx_get(c, "hub_index", "the hub plane's index")),
        "attribute_group": "sweep_w1d", "attribute_name": "at", "attribute_value": "by-index"},
     lambda p: p.get("value") == "by-index" and p.get("feature") == "CarrierHubPlane", None),
    ("design_edit_timeline", lambda c: {
        "action": "delete_attribute",
        "feature": "CarrierHubPlane@{0}".format(_ctx_get(c, "hub_index", "the hub plane's index")),
        "attribute_group": "sweep_w1d", "attribute_name": "at"},
     lambda p: p.get("attribute_deleted") is True, None),
    # the NEIGHBOURING index carries the same name and misses: the pair must agree, so an off-by-one
    # is a refusal naming the miss rather than the feature next door.
    ("design_edit_timeline", lambda c: {
        "action": "set_attribute",
        "feature": "CarrierHubPlane@{0}".format(_ctx_get(c, "hub_index",
                                                         "the hub plane's index") + 1),
        "attribute_group": "sweep_w1d", "attribute_name": "at", "attribute_value": "x"},
     _refused("no timeline feature named"), None),
]

# --- FINALE: back to the design, beauty shots, then DISCARD the document on camera --------------
_FINALE = [
    ("model_extrude", {"sketch_name": "NoSuchSketch", "distance": 5}, "refused", None),   # guard probe
    ("param_set", {"name": "", "expression": "1"}, "refused", None),                      # guard probe
    ("view_switch_workspace", {"workspace": "design"}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    ("view_screenshot_multi", {"views": ["iso-top-right", "front"], "width": 500, "height": 400}, "ok", None),
    # THE RENAMES, last: a rename invalidates every row that names its target, so they run once the
    # build is done. A two-body cameo carries the dedupe beat - it needs a SIBLING pair, and the
    # machined part is a component of one body.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TwinCameo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "TwinA"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1400, "y1": 200, "x2": 1430, "y2": 230,
                             "sketch_name": "TwinA"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwinA", "profile_index": 0, "distance": 10}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "TwinB"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1450, "y1": 200, "x2": 1480, "y2": 230,
                             "sketch_name": "TwinB"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwinB", "profile_index": 0, "distance": 10}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", {"target": "TwinCameo", "kind": "planar_face", "nearest_to": [1415, 215, 10],
                       "max_results": 1}, "ok", _fg("twin_a")),
    ("find_geometry", {"target": "TwinCameo", "kind": "planar_face", "nearest_to": [1465, 215, 10],
                       "max_results": 1}, "ok", _fg("twin_b")),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "twin_a", "the first twin body"),
                                   "new_name": "TwinPlate"},
     lambda p: p.get("name") == "TwinPlate" and p.get("kind") == "body"
     and p.get("deduped") is False, None),
    # the name its sibling already holds: Fusion dedupes it to 'TwinPlate (1)' and THAT is the name
    # the payload has to publish - a payload echoing the request would read 'TwinPlate' here.
    ("design_set_name", lambda c: {"target": _ctx_get(c, "twin_b", "the second twin body"),
                                   "new_name": "TwinPlate"},
     lambda p: p.get("deduped") is True and p.get("name") == "TwinPlate (1)", None),
    # an OCCURRENCE target renames the COMPONENT behind it, and the instance name follows.
    ("design_set_name", {"target": "TwinCameo:1", "new_name": "TwinAssy"},
     lambda p: p.get("kind") == "component" and p.get("occurrence_name") == "TwinAssy:1", None),
    # THE OCCURRENCE FAN-OUT, in the two-step shape that discriminates ([F64]): colour ONE body
    # directly (colour A), then write the OCCURRENCE in a different colour (colour B). The
    # occurrence's own read-back agrees with the write whether or not a body took it, so the bodies
    # are re-read: the body holding its own override kept colour A and must come back under
    # 'bodies_not_reached' - NOT under applied_to. Both colours are minted from one base asset, so
    # they share an Appearance.id and differ only by NAME - an id-only comparison lists the
    # overridden body as reached, which is exactly the defect this beat stands on.
    ("appearance_set", {"target": "TwinPlate", "color": "#C2185B"},
     lambda p: p.get("kind") == "body" and p.get("applied_to") == ["TwinPlate"], None),
    ("appearance_set", {"target": "TwinAssy:1", "color": "#00897B"},
     lambda p: any(o.get("body") == "TwinPlate" for o in (p.get("bodies_not_reached") or []))
     and "TwinPlate" not in (p.get("applied_to") or [])
     and "TwinPlate (1)" in (p.get("applied_to") or []), None),
    # the same shape where the overridden body is the occurrence's ONLY one: nothing was reached, so
    # the call is a refusal naming the body to colour directly - never an ok on the occurrence's own
    # agreeable read-back.
    ("model_create_component", {"name": "SoloColor", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SoloS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1500, "y1": 200, "x2": 1530, "y2": 230,
                             "sketch_name": "SoloS"}, "ok", None),
    ("model_extrude", {"sketch_name": "SoloS", "profile_index": 0, "distance": 10}, "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", {"target": "SoloColor", "kind": "planar_face", "nearest_to": [1515, 215, 10],
                       "max_results": 1}, "ok", _fg("solo_face")),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "solo_face", "the solo body"),
                                   "new_name": "SoloBody"},
     lambda p: p.get("kind") == "body" and p.get("name") == "SoloBody", None),
    ("appearance_set", {"target": "SoloBody", "color": "#C2185B"},
     lambda p: p.get("kind") == "body", None),
    ("appearance_set", {"target": "SoloColor:1", "color": "#00897B"},
     _refused("reached NONE", "SoloBody"), None),
    # the machined part, then re-found through the name that landed - the rename reaches the browser
    # name every other tool addresses it by. The STEP round-trip of the deliverables act leaves a
    # SECOND Carrier component in the tree ('Carrier (1)'), so the bare name is ambiguous by now and
    # the original is addressed by its fullPathName - the unambiguous key the refusal points at.
    ("design_set_name", {"target": "Carrier:1", "new_name": "CarrierBar"},
     lambda p: p.get("name") == "CarrierBar" and p.get("previous_name") == "Carrier"
     and p.get("kind") == "component", None),
    ("find_geometry", {"target": "CarrierBar", "kind": "planar_face", "max_results": 1}, "ok", None),
    # re-asking for the name it already holds mutates nothing and says so.
    ("design_set_name", {"target": "CarrierBar", "new_name": "CarrierBar"},
     lambda p: p.get("changed") is False, None),
    ("design_set_name", {"target": "", "new_name": "X"}, "refused", None),
    # the ROOT component is refused UP FRONT: its name is the document's, and the platform's own
    # raise would abort the transaction around it. The root name is read off the tree, never guessed.
    ("design_get", {"include": ["tree"]}, "ok", ("root_name", lambda p: p["tree"]["root"])),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "root_name", "the root component name"),
                                   "new_name": "RootRename"}, "refused", None),
    # every DOCUMENT read is stamped with the document it read from, so two tallies taken in two
    # documents are distinguishable. (sys_find_tool, the registry read in the overture, carries no
    # such key - it never touched the design.)
    ("design_get", {},
     lambda p: bool((p.get("active_document") or {}).get("name")), None),
    # the assignable catalog, at both zoom levels: the document's own entries plus a count-only
    # census of every loaded library, then ONE library paged by name_filter/max_results. A library
    # name that is not loaded is refused with the loaded names listed.
    ("design_get", {"include": ["materials"]}, "ok", None),
    ("design_get", {"include": ["appearances"], "library": "Fusion Appearance Library",
                    "name_filter": "paint", "max_results": 5}, "ok", None),
    ("design_get", {"include": ["appearances"], "library": "NoSuchLibrary"}, "refused", None),
    # DRAWING GUARDS: every one of these is settled before the tool looks for a cloud source, so
    # they run on the story document exactly as they would on a saved one, and each refuses for the
    # reason it names with no drawing created. The creation path itself is cloud-tier (it needs a
    # saved source design) and stays out of the default sweep.
    # adsk.drawing carries no plain shaded member - shading pairs with hidden or with visible edges.
    ("drawing_create", {"view_style": "shaded"}, "refused", None),
    # center_line / center_mark have NO enum family to reach on this build (the namespace carries
    # CenterLineOptions / CenterMarkOptions classes instead), so a non-default request is refused
    # rather than dropped by a best-effort setter.
    ("drawing_create", {"center_line": "holes"}, "refused", None),
    ("drawing_create", {"center_mark": "fillets"}, "refused", None),
    ("drawing_create", {"tangent_edges": "partial"}, "refused", None),
    # Fusion gates manual creation on a template carrying view-placeholder information, and the
    # failure escapes an enclosing try/except - so the mode is refused up front instead of called
    # into, and the session is still healthy afterwards.
    ("drawing_create", {"creation_mode": "manual"}, "refused", None),
    ("workspace_orient", {}, "ok", None),
    # the API silently IGNORES a sheet size from the other standard, so the pairing is guarded here.
    ("drawing_create", {"standard": "asme", "sheet_size": "a2"}, "refused", None),
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
    # a CLOSED revolved sphere surface encloses one cell; surface_fill must seal it to a SOLID at
    # the enclosed volume (r=6mm -> 904.78 mm3) MEASURED off the result, never predicted.
    ("model_create_component", {"name": "FillDemo", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "FillProf"}, "ok", None),
    # the arc's endpoints must sit ON the revolve axis (x=0) or the revolved surface is an open
    # tube enclosing nothing, and surface_fill refuses it (live-measured).
    ("sketch_add_geometry", {"kind": "arc", "cx": 0, "cy": 150, "x1": 0, "y1": 156,
                             "sweep_deg": 180, "sketch_name": "FillProf"}, "ok", None),
    ("surface_revolve", {"sketch_name": "FillProf", "axis": "z", "angle_deg": 360}, "ok", None),
    # the closed sphere sheet encloses exactly ONE cell, so index 1 is one past the end: refused
    # NAMING the index and the range that exists, never clamped onto a neighbouring cell. The parse
    # happens before any cell is kept, so the sheet is untouched and the fill below is still its
    # first feature.
    ("surface_fill", {"tools": ["FillDemo"], "operation": "new", "cells": [1]},
     _refused("does not exist", "0..0"), None),
    ("surface_fill", {"tools": ["FillDemo"], "operation": "new"},
     lambda p: p.get("filled") is True and p.get("all_solid") is True
     and abs(p.get("result_volume", 0) - 904.78) < 10
     and p.get("tools_unclassified", 0) == 0, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
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
    # THREE edges of ONE body, not one: edge.body hands back a fresh proxy per read, so a
    # body-set walk keyed on object identity counts this single body three times and refuses a
    # legal call as multi-body. A single-edge beat cannot catch that - one edge never disagrees
    # with itself. nearest_to pins the pick to the TOP rim (z=15): three of that rim's four edges
    # connect at endpoints into one open chain, while an arbitrary pick mixes top and bottom rims
    # into a disconnected set the platform rejects as an invalid extend input.
    ("find_geometry", {"target": "Surf", "kind": "line_edge", "nearest_to": [220, 215, 15], "max_results": 3}, "ok", _fgn("surf_edges")),
    ("surface_extend", lambda c: {"edges": _ctx_get(c, "surf_edges", "surface edges"), "distance": 2},
     # no extend_alignment given: the key is ABSENT from the payload, so nothing was written and
     # the API's own default stands - an echoed key here would be a claim about an unwritten value.
     lambda p: p.get("body_count", 1) == 1 and "extend_alignment" not in p, None),
    ("find_geometry", {"target": "Surf", "kind": "planar_face", "max_results": 1}, "ok", _fg("surf_body")),
    ("surface_reverse_normal", lambda c: {"bodies": [_ctx_get(c, "surf_body", "surface body")]}, "ok", None),
    # extend_alignment + thicken_type, on their own sheet so the read-backs never ride on edges an
    # earlier extend already moved. Both properties are written through set_verified, so a value the
    # platform drops comes back as an error rather than an echoed success. A rectangle extruded as a
    # surface gives a four-walled sheet, which is the convex corner set 'rounded' needs to mean
    # anything - a single flat face has no corner to round.
    ("model_create_component", {"name": "SAlign", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SAlignS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 700, "y1": 200, "x2": 740, "y2": 230,
                             "sketch_name": "SAlignS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SAlignS", "distance": 15}, "ok", None),
    _watch("SAlign:1"),
    ("find_geometry", {"target": "SAlign", "kind": "line_edge", "nearest_to": [720, 215, 15],
                       "max_results": 3}, "ok", _fgn("salign_edges")),
    ("surface_extend", lambda c: {"edges": _ctx_get(c, "salign_edges", "aligned sheet edges"),
                                  "distance": 2, "extend_alignment": "align_edges"},
     lambda p: p.get("extend_alignment") == "align_edges", None),
    ("find_geometry", {"target": "SAlign", "kind": "planar_face", "max_results": 1}, "ok",
     _fg("salign_face")),
    ("surface_thicken", lambda c: {"faces": [_ctx_get(c, "salign_face", "aligned sheet face")],
                                   "thickness": 1, "thicken_type": "rounded"},
     lambda p: p.get("thicken_type") == "rounded", None),
    # back to the component that was active before this cameo, so the ones after it nest as before.
    ("design_activate_component", {"occurrence": "Surf:1"}, "ok", None),
    ("model_create_component", {"name": "SDel", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SD1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 300, "y1": 200, "x2": 320, "y2": 220, "sketch_name": "SD1"}, "ok", None),
    ("model_extrude", {"sketch_name": "SD1", "profile_index": 0, "distance": 10}, "ok", None),
    _watch("SDel:1"),
    ("find_geometry", {"target": "SDel", "kind": "planar_face", "nearest_to": [310, 210, 10], "max_results": 1}, "ok", _fg("sdel_top")),
    ("surface_delete_face", lambda c: {"faces": [_ctx_get(c, "sdel_top", "top face")], "heal": False}, "ok", None),
    ("find_geometry", {"target": "SDel", "kind": "line_edge", "nearest_to": [310, 210, 10], "max_results": 4}, "ok", _fgn("sdel_rim")),
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim", "rim edges")}, "ok", None),
    # a patch with operation 'new' leaves the opened body untouched, so the SAME rim carries the two
    # option beats below. 'continuity' is written through set_verified, so a value the platform
    # dropped is an error - the payload cannot echo a continuity the patch is not running.
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim", "rim edges"),
                                 "continuity": "tangent"},
     lambda p: p.get("continuity") == "tangent", None),
    # an interior RAIL the patch surface must pass through: a sheet standing in the opening, whose
    # top edge crosses it end to end with both ends landing on the rim. 'interior_rail_count' is the
    # count PatchFeatureInput.interiorRailsAndPoints reads back, not the number of handles passed.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PatchRail", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "PatchRailS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 300, "y1": 210, "x2": 320, "y2": 210,
                             "sketch_name": "PatchRailS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "PatchRailS", "distance": 10}, "ok", None),
    ("find_geometry", {"target": "PatchRail", "kind": "line_edge", "nearest_to": [310, 210, 10],
                       "max_results": 1}, "ok", _fg("patch_rail")),
    ("design_activate_component", {"occurrence": "SDel:1"}, "ok", None),
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim", "rim edges"),
                                 "interior_rails": [_ctx_get(c, "patch_rail", "the interior rail")]},
     lambda p: p.get("interior_rail_count") == 1, None),
    # rails fit ONE patch surface, so pairing them with the multi-loop 'boundaries' is refused
    # BEFORE any patch runs - every loop would otherwise be handed the same rails.
    ("surface_patch", lambda c: {"boundaries": [_ctx_get(c, "sdel_rim", "rim edges")],
                                 "interior_rails": [_ctx_get(c, "patch_rail", "the interior rail")]},
     "refused", None),
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
    # RULED surfaces off a rim edge. A line extruded as a surface gives a vertical sheet whose top
    # rim (z=30) is the seed every ruled beat leaves from; each type builds a DIFFERENT surface off
    # that same edge, so the payload's own ruled_type/direction is what separates them.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "Ruled", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "RuledS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 900, "y1": 0, "x2": 940, "y2": 0,
                             "sketch_name": "RuledS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "RuledS", "distance": 30}, "ok", None),
    _watch("Ruled:1"),
    ("find_geometry", {"target": "Ruled", "kind": "line_edge", "nearest_to": [920, 0, 30],
                       "max_results": 1}, "ok", _fg("ruled_edge")),
    # tangent continues the parent face's own plane past the rim, landing ONE new open body.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "tangent"},
     lambda p: p.get("is_solid") is False and len(p.get("result_bodies", [])) == 1
     and p.get("ruled_type") == "tangent", None),
    # normal stands perpendicular to that same face - a different surface off the same seed edge.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "normal"},
     lambda p: p.get("is_solid") is False and p.get("ruled_type") == "normal", None),
    # direction sweeps along an ENTITY: a world axis resolves to the component's origin axis, which
    # is what createInput consumes (a direction VECTOR cannot be handed to it).
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "direction", "direction": "z"},
     lambda p: p.get("direction") == "z-axis" and p.get("is_solid") is False, None),
    # angle_deg reads back in DEGREES off the feature's own ModelParameter (radians at the API).
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "angle_deg": 20},
     lambda p: abs(p.get("angle_deg", 0) - 20) < 1e-6, None),
    # a direction entity with a type that ignores it is refused: the payload could otherwise claim
    # tangent while a Direction surface was built.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "tangent", "direction": "z"},
     "refused", None),
    # the Direction type with no entity: Fusion itself refuses to build the input.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "direction"}, "refused", None),
    # ruling off a SOLID box edge - the draft-check case. The feature's body collection holds the
    # box AND the new sheet, so a handler publishing that collection raw would report the box as
    # created and read is_solid=true off it: the new sheet alone is the result.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "RuledSolid", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "RuledSolidS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 980, "y1": 200, "x2": 1020, "y2": 240,
                             "sketch_name": "RuledSolidS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RuledSolidS", "profile_index": 0, "distance": 20}, "ok", None),
    _watch("RuledSolid:1"),
    ("find_geometry", {"target": "RuledSolid", "kind": "line_edge", "nearest_to": [1000, 200, 20],
                       "max_results": 1}, "ok", _fg("ruled_solid_edge")),
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_solid_edge", "a box top edge")],
                                        "distance": 15, "ruled_type": "tangent"},
     lambda p: p.get("is_solid") is False and len(p.get("result_bodies", [])) == 1
     and "Body1" not in p.get("result_bodies", []), None),
    # back to the component that was active before this cameo, so the ones after it nest as before.
    ("design_activate_component", {"occurrence": "SHole:1"}, "ok", None),
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
    # a parametric mesh write reports the MODE it ran in and the base feature it opened to run
    # there: the scope is what a parametric design requires, and the payload names both.
    ("mesh_combine", {"target": "ME", "tools": ["MF"], "operation": "join"},
     lambda p: p.get("design_mode") == "parametric" and bool(p.get("base_feature")), None),
    # a pristine scratch mesh deleted with the survivor check: the payload's own claim is the
    # re-scan. A mesh another feature already transformed (e.g. the plane-cut MD) carries a
    # different lineage; this beat exercises the plain-delete contract.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MDEL",
                                "quality": "low"}, "ok", None),
    ("mesh_delete", {"mesh": "MDEL"}, "ok", None),
    ("mesh_export", {"target": "MA", "file_path": EXPORT_DIR + "/eval_mesh", "format": "stl"}, "ok", None),
    ("mesh_insert", {"file_path": EXPORT_DIR + "/eval_mesh.stl", "name": "MshIns"}, "ok", None),
    # repair on a HEALTHY mesh: nothing of that kind to fix is an honest success, not a failure, and
    # the payload must say so rather than claim a repair. Then a rebuild, whose density is read back
    # off the feature's own parameter - the request is never echoed.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MFIX",
                                "quality": "low"}, "ok", None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "one_touch_fix"},
     lambda p: p.get("repaired") is True and p.get("watertight") is True, None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "fast",
                     "density": 32},
     lambda p: p.get("density") == 32.0 and "density_unverified" not in p, None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "close_holes", "density": 32}, "refused", None),
    # A repair that finds nothing of its kind is an honest success, and the payload has to say so
    # rather than claim a repair - 'changed' empty is that statement. A mesh straight out of
    # save_as_mesh is NOT that fixture: the first stitch_and_remove on it measurably moves the
    # counts (it has duplicate vertices to weld), so the no-op case is the SECOND call, once the
    # first has done the welding. That also makes the beat an idempotence check.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSTITCH",
                                "quality": "low"}, "ok", None),
    ("mesh_repair", {"mesh": "MSTITCH", "repair_type": "stitch_and_remove"},
     lambda p: p.get("repaired") is True, None),
    ("mesh_repair", {"mesh": "MSTITCH", "repair_type": "stitch_and_remove"}, _repair_no_op, None),
    # mesh_shell hollows the SAME body in place and re-triangulates it: the payload's before/after
    # counts and the volume DROP are the verdict, and the thickness is read off the feature.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSHL",
                                "quality": "low"}, "ok", None),
    # 'hollowed' needs BOTH a volume drop and a body that still reads watertight - a shell that lost
    # the closure reports the same drop for the opposite reason, so the flag pair is the verdict.
    ("mesh_shell", {"mesh": "MSHL", "thickness": 2, "units": "mm"},
     lambda p: p.get("hollowed") is True and abs(p.get("thickness", 0) - 2.0) < 1e-6
     and p.get("watertight") is True
     and p.get("volume_change", 0) < 0 and "volume" in (p.get("changed") or []), None),
    ("mesh_shell", {"mesh": "MSHL", "thickness": -2}, "refused", None),
    # a thickness thicker than half the thinnest wall does NOT quietly cut through: the platform
    # refuses the shell outright ([F50] - measured on a closed cube), and the tool hands that
    # compute failure on by name instead of reporting a hollow that never happened. The box is
    # 10 mm through its thinnest axis, so 6 mm is past the half-wall.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSHL2",
                                "quality": "low"}, "ok", None),
    ("mesh_shell", {"mesh": "MSHL2", "thickness": 6, "units": "mm"},
     _refused("MESH_FAILED_HOLLOW"), None),
    # mesh_smooth: the node coordinates move. nodes_moved > 0 is the gate - the counts holding
    # still is measured on a 12-triangle box only, so it is NOT asserted here.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "cyl_body", "cyl body"), "name": "MSMO",
                                "quality": "high"}, "ok", None),
    ("mesh_smooth", {"mesh": "MSMO", "smoothness": 0.05},
     lambda p: p.get("nodes_moved", 0) > 0 and abs(p.get("smoothness", 0) - 0.05) < 1e-6, None),
    ("mesh_smooth", {"mesh": "MSMO", "smoothness": 1.5}, "refused", None),
    # A 'merge' combine of two DISJOINT meshes yields ONE body holding TWO shells, which the
    # separate then takes apart: measured 24 triangles in, two 12-triangle pieces out.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSEPA",
                                "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "cyl_body", "cyl body"), "name": "MSEPB",
                                "quality": "low"}, "ok", None),
    ("mesh_combine", {"target": "MSEPA", "tools": ["MSEPB"], "operation": "merge"}, "ok", None),
    ("mesh_separate", {"mesh": "MSEPA"},
     lambda p: p.get("piece_count", 0) >= 2 and len(p.get("pieces") or []) >= 2, None),
    # mesh_reverse_normal: the signed volume changes sign; is_closed / is_oriented do not move.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MREV",
                                "quality": "low"}, "ok", None),
    ("mesh_reverse_normal", {"mesh": "MREV"},
     lambda p: p.get("reversed") is True
     and (p.get("volume_sign_flipped") is True or p.get("normals_negated") is True), None),
    # MeshBody.name IS settable ([F37]): the rename lands on the mesh kind, and a FRESH fetch of the
    # component's meshes - not the wrapper the write held - is what proves it stuck.
    ("design_set_name", {"target": "MREV", "new_name": "MeshRenamed"},
     lambda p: p.get("kind") == "mesh" and p.get("name") == "MeshRenamed"
     and p.get("previous_name") == "MREV", None),
    ("mesh_get", {"target": "Msh", "max_results": 100},
     lambda p: any(m.get("name") == "MeshRenamed" for m in (p.get("meshes") or [])), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
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
    # a scratch sketch inside the Carrier footprint, drawn while Design is still the active
    # workspace: the geometry the 'sketch' selection takes, which is a WHOLE sketch (SketchSelection
    # accepts sketches, not their curves and not their profiles).
    ("sketch_create", {"plane": "xy", "name": "CamContourSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 10,
                             "sketch_name": "CamContourSketch"}, "ok", None),
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
    # the vocabulary comes from the sample libraries themselves, so the census is a read: the whole
    # spread is offered (well past ten kinds), the everyday mill is in it, and so are the two that
    # live in only one library each.
    ("cam_edit_tools", {"action": "list_types", "scope": "document"},
     lambda p: p.get("type_count", 0) >= 10 and "flat end mill" in p["types"]
     and "center drill" in p["types"] and "turning general" in p["types"], None),
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
    # A MACHINE OF OUR OWN before the library one: built from a template into the Local library and
    # then proven usable three ways - the create's own re-resolve, the catalog it must list in, and a
    # real assignment. The name carries the run stamp because the library keeps it (see MACHINE_NAME).
    ("cam_create_machine", {"name": MACHINE_NAME, "template": "generic_3_axis",
                            "vendor": "SweepCo"},
     lambda p: p["created"] is True and p["name"] == MACHINE_NAME and bool(p["url"])
     and "milling" in (p["kind"] or []), None),
    # the catalog is the independent witness: the assignment surface lists it under its own vendor.
    ("cam_get", {"include": ["machines"], "vendor": "SweepCo"},
     lambda p: any(m["name"] == MACHINE_NAME for m in p["machines"]["machines"]), None),
    ("cam_edit_setup", {"setup": "DemoSetup", "machine": MACHINE_NAME},
     lambda p: p.get("machine_set") == MACHINE_NAME, None),
    # the name is now how the library reaches a machine, so a second create is refused naming the
    # machine it collides with and the library holding it.
    ("cam_create_machine", {"name": MACHINE_NAME, "template": "generic_3_axis"}, "refused", None),
    # a real machine, and the assignment the post and setup sheet run on: simulation-ready machines
    # refuse direct assignment (API limitation); machine_strip_simulation is the one working path.
    # Asserted by read-back, not call success.
    ("cam_edit_setup", {"setup": "DemoSetup", "machine": "Haas VF-2",
                        "machine_strip_simulation": True},
     lambda p: p.get("machine_set") == "Haas VF-2", None),
    ("cam_get", {"include": ["machines"], "vendor": "Haas", "machine_type": "milling"},
     lambda p: p.get("machines", {}).get("count", 0) > 0, None),
    # four operations: an explicit FACE selection, an ADAPTIVE with real stock margin to clear,
    # a zero-handle SILHOUETTE, and a DRILL on the hub's patterned bolt circle. The default name of
    # a created operation is the PLATFORM'S to pick, so the adaptive's name is taken from what the
    # create PUBLISHED and every later row addresses it through ctx - a literal would be a guess.
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "face",
                              "tool_scope": "document", "tool_index": 0, "generate": False}, "ok", None),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "adaptive",
                              "tool_scope": "document", "tool_index": 0, "generate": False},
     "ok", ("adaptive_op", lambda p: p["operation"])),
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
    # zero-handle silhouette: applies against the setup's model (live-verified mechanism), and says
    # so - the setup-model flag is set explicitly, never left to a default.
    ("cam_select_geometry", {"operation": "2D Contour1", "selection": "silhouette",
                             "generate": False},
     lambda p: p["setup_models_selected"] is True, None),
    # the drill: the bolt-circle bores selected by handle + diameter filter (3mm +/- 0.1).
    ("find_geometry", {"target": "Carrier", "kind": "cylinder_face", "radius": 1.5,
                       "max_results": 8}, "ok", _fgn("bolt_bores")),
    ("cam_select_geometry", lambda c: {"operation": "Drill1", "selection": "holes",
                                       "handles": _ctx_get(c, "bolt_bores", "bolt-circle bores"),
                                       "min_diameter": 2.9, "max_diameter": 3.1,
                                       "generate": False}, "ok", None),
    # SKETCH: the scratch circle drawn at the top of this act, asserted on the entity set the applied
    # selection reports rather than on outputGeometry (a curve path count on an ungenerated op is not
    # a measured claim).
    ("cam_select_geometry", {"operation": "2D Contour1", "selection": "sketch",
                             "sketches": ["CamContourSketch"], "generate": False},
     lambda p: p["resolved"]["entities"] >= 1, None),
    # No pocket_recognition beat: running that selection here coincides with the Fusion process
    # terminating, and a routine sweep must not risk the host. The other selection kinds above and
    # below carry cam_select_geometry's coverage.
    # REFUSED: a knob whose property does not exist on that selection's class - dropping it silently
    # would leave the caller believing an option applied. The guard fires before any CAM read.
    ("cam_select_geometry", lambda c: {"operation": "Drill1", "selection": "holes",
                                       "handles": _ctx_get(c, "bolt_bores", "bolt-circle bores"),
                                       "loop_type": "outside"}, "refused", None),
    # REFUSED: the geometry offered through the wrong input - silhouette machines BODIES, and the
    # error names the input to move them to.
    ("cam_select_geometry", lambda c: {"operation": "2D Contour1", "selection": "silhouette",
                                       "handles": [_ctx_get(c, "stock_top", "stock top")]},
     "refused", None),
    # REFUSED: an edge where a pocket floor face belongs. Three gates can catch it - the handle kind,
    # Fusion's own rejection channel, or the 0-selections check - and the ledger note records which
    # message came back. generate stays false so an unexpected pass cannot launch a toolpath off it.
    ("find_geometry", {"target": "Carrier", "kind": "circular_edge", "max_results": 1}, "ok",
     _fg("carrier_edge")),
    ("cam_select_geometry", lambda c: {"operation": "2D Contour1", "selection": "pocket",
                                       "handles": [_ctx_get(c, "carrier_edge", "a Carrier edge")],
                                       "generate": False}, "refused", None),
    # and back to the silhouette this contour is generated from, now through NAMED bodies - the
    # branch that does NOT ride the setup's own models, and the last selection the generate acts on.
    ("cam_select_geometry", {"operation": "2D Contour1", "selection": "silhouette",
                             "bodies": ["Carrier"], "generate": False},
     lambda p: p["setup_models_selected"] is False, None),
    # INSPECTION: the recorded probing results on a job nothing has ever probed. The platform holds
    # this two ways - inspectionResults reads None on some documents, an EMPTY collection on others
    # (this story doc measures the latter) - and both are a real zero-measure answer, never a bare
    # error. The predicate pins the zero, not which of the two shapes carried it.
    ("cam_get", {"include": ["inspection"]},
     lambda p: p["inspection"]["measure_count"] == 0 and p["inspection"]["measures"] == [], None),
    # A scoped read on a document with zero measures is REFUSED naming the count - on this doc the
    # collection exists and empty, so the out-of-range gate answers (the absent-state ok is the
    # None-collection documents' answer).
    ("cam_get", {"include": ["inspection"], "measure": "0"}, "refused", None),
    # REFUSED: the slice's units guard - the one refusal that fires whether or not results exist,
    # since every length in a point row crosses the wire scaled out of CM.
    ("cam_get", {"include": ["inspection"], "units": "furlongs"}, "refused", None),
    ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}}, "ok", None),
    ("cam_reorder", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op"),
                               "position": "before", "reference": "Face1"}, "ok", None),
    ("cam_activate_setup", {"setup": "DemoSetup"}, "ok", None),
    ("cam_compare_operations", lambda c: {"operation_a": "Face1",
                                          "operation_b": _ctx_get(c, "adaptive_op",
                                                                  "the created adaptive op")},
     "ok", None),
    # FOLDERS: organize the job the way a shop sheet reads - milling vs drilling.
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Milling"}, "ok", None),
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Drilling"}, "ok", None),
    ("cam_edit_folders", lambda c: {"action": "move", "setup": "DemoSetup", "folder": "Milling",
                                    "operations": ["Face1",
                                                   _ctx_get(c, "adaptive_op",
                                                            "the created adaptive op"),
                                                   "2D Contour1"]}, "ok", None),
    ("cam_edit_folders", {"action": "move", "setup": "DemoSetup", "folder": "Drilling",
                          "operations": ["Drill1"]}, "ok", None),
    ("cam_show_toolpath", {"action": "list"}, "ok", None),
    # the validity verdict BEFORE generation: false, with the not-yet-generated ops named; a scoped
    # check resolves through the shared resolver and a bogus scope is refused listing what exists.
    ("cam_inspect_toolpaths", {},
     lambda p: p["passed"] is False and len(p["measured"]["not_valid"]) > 0, None),
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"}, lambda p: p["passed"] is False, None),
    ("cam_inspect_toolpaths", {"scope": "NoSuchScopeXyz"}, "refused", None),
    # max_results cannot lift the tool's own ceiling: every not_valid row crosses the wire, so an
    # over-cap request is CLAMPED to it (200) rather than answered with a flood.
    ("cam_inspect_toolpaths", {"max_results": 10000},
     lambda p: len(p["measured"]["not_valid"]) <= 200, None),
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
    # the sheet file must LAND (the API's bool answers before the async write completes)
    ("cam_generate_setup_sheet", {"scope": "DemoSetup", "output_folder": EXPORT_DIR + "/sheets"},
     lambda p: p.get("generated") is True and p.get("size_bytes", 0) > 0, None),
    ("cam_set_nc_comment", {"comment": "GYRO sweep"}, "ok", None),
    ("cam_save_template", {"template_name": "GyroTmpl", "setup": "DemoSetup",
                           "operations": "Face1", "location": "local"}, "ok", None),
    ("cam_create_setup", {"models": ["Carrier"], "name": "Setup2"}, "ok", None),
    ("cam_apply_template", {"setup": "Setup2", "template_name": "GyroTmpl",
                            "location": "local", "generate": "skip"}, "ok", None),
    ("cam_delete", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op")},
     "ok", None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export",
                       "target": "Carrier"}, "ok", None),
    # the SPLIT path writes one file per top-level occurrence, each through its OWN options object -
    # so the format knob has to be read back per file and published, not dropped because the export
    # took a different branch. 'options_applied' is the value that LANDED on the first file.
    ("design_export", {"format": "stl", "file_path": EXPORT_DIR + "/gyro_split",
                       "split_by_component": True, "stl_binary": True},
     lambda p: p.get("exported") is True and p.get("split_by_component") is True
     and p.get("options_applied", {}).get("stl_binary") is True, None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/gyro_export.step"}, "ok", None),
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
            rows.append(("cam_get_status", "FAIL", str(payload)[:NOTE_MAX]))
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
        # the adaptive's default name comes from the platform, so it rides ctx here too.
        ("cam_create_operation", {"setup": "Setup1", "strategy": "adaptive", "tool_scope": "document", "tool_index": 0, "generate": False},
         "ok", ("adaptive_op", lambda p: p["operation"])),
        ("cam_get", {"include": ["operations"], "setup": "Setup1"}, "ok", None),
        ("find_geometry", {"target": "GyroStock", "kind": "planar_face", "nearest_to": [710, 10, 10], "max_results": 1}, "ok", _fg("cam_top")),
        ("cam_select_geometry", lambda c: {"operation": "Face1", "selection": "face", "handles": [_ctx_get(c, "cam_top", "cam top face")], "generate": False}, "ok", None),
        ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}}, "ok", None),
        ("cam_edit_setup", {"setup": "Setup1", "models": ["GyroStock"]}, "ok", None),
        ("cam_edit_folders", {"action": "create", "setup": "Setup1", "name": "Folder1"}, "ok", None),
        ("cam_reorder", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op"),
                                   "position": "before", "reference": "Face1"}, "ok", None),
        ("cam_activate_setup", {"setup": "Setup1"}, "ok", None),
        ("cam_compare_operations", lambda c: {"operation_a": "Face1",
                                              "operation_b": _ctx_get(c, "adaptive_op",
                                                                      "the created adaptive op")},
         "ok", None),
        ("cam_show_toolpath", {"action": "list"}, "ok", None),
        ("cam_generate", {"target": "Setup1", "skip_valid": False}, "ok", None),
        ("cam_get_status", {"target": "Setup1"}, "ok", None),
    ]
)

# ACT 10b fallback: deliverables on the scratch job.
_CAM_FB_DELIVER = [
    ("cam_post", {"scope": "Setup1", "post": "haas", "post_scope": "local", "output_folder": EXPORT_DIR + "/nc", "program_name": "1001"}, "ok", None),
    ("cam_generate_setup_sheet", {"scope": "Setup1", "output_folder": EXPORT_DIR + "/sheets"},
     lambda p: p.get("generated") is True and p.get("size_bytes", 0) > 0, None),
    ("cam_set_nc_comment", {"comment": "GYRO sweep"}, "ok", None),
    ("cam_save_template", {"template_name": "GyroTmpl", "setup": "Setup1", "operations": "Face1", "location": "local"}, "ok", None),
    ("cam_create_setup", {"models": ["GyroStock"], "name": "Setup2"}, "ok", None),
    ("cam_apply_template", {"setup": "Setup2", "template_name": "GyroTmpl", "location": "local", "generate": "skip"}, "ok", None),
    ("cam_delete", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op")},
     "ok", None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export", "target": "GyroStock"}, "ok", None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/gyro_export.step"}, "ok", None),
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

# Post-act hook run() fires after an act completes: the bounded generation poll between the CAM
# job act and its deliverables.
POLL_AFTER = {
    "ACT 10a - CAM: JOB + GENERATE": {"narrative": "DemoSetup", "fallback": "Setup1"},
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
    "sys_find_tool": ("search the surface for the revolve verb - a registry read, so it carries no "
                      "'active_document' stamp (design_get's final read is the other half)"),
    "sys_get_api_doc": "read the RevolveFeatures API doc",
    "view_list_workspaces": "list the workspaces available",
    "view_set": ("orient the camera to the iso hero angle, with the perspective angle carried "
                 "through to the camera and read back. SKIPPED(rig): the snapshot/restore "
                 "truncation beats (truncated + occurrence_cap) need an assembly with more "
                 "occurrences than the cap, and the story document stays well under it"),
    "sys_get_selection": "expected refusal: nothing is selected yet",
    "sys_get_preferences": ("read the application's own configuration - the default projection, two "
                            "decoded enum families, the compatibility group, and all three members "
                            "the census found RAISING on this build, each published null and named "
                            "in 'unreadable' with none of them miscategorised as a member the build "
                            "does not carry"),
    "sys_set_preferences": ("round-trip one invisible preference and restore it in the same act; "
                            "the below-minimum value and a tier-R member refused"),
    "param_add": "add GimbalDia and the derived ring/rotor radii",
    "param_set_favorite": "mark GimbalDia the favorite driving dimension",
    "param_get": "read the parameter table; a fresh GimbalDia read sizes the CAM stock",
    "model_create_component": "cast the eight parts, Pedestal nested in Frame",
    "design_activate_component": "step into each part to build its sketch",
    "sketch_create": "draw each part's sketch on its plane",
    "sketch_add_geometry": ("draw the concentric rings and part footprints; then the SLOT family, "
                            "one scratch sketch per shape - a three-point arc slot with its five "
                            "arcs and its profile, the centre-point arc slot in both its short and "
                            "its full ladder with each dimension flag gated independently, an "
                            "overall slot whose cap centres prove the tip-to-tip measure, the "
                            "length/angle tail that adds the fourth line and its own dimensions, "
                            "the centre-point slot's HALF length landing a cap on its second point, "
                            "and the legacy centre-to-centre form; the angle with no length, the "
                            "angle FLAG on a straight slot, each cross-kind input pointed at the "
                            "kind that carries it, and a tail on the legacy form all refused - the "
                            "legacy form's own census read twice over (its note names the 2 solid "
                            "lines, the 1 construction line and the 2 arc caps behind a "
                            "'curves_added' of 3, and sketch_get finds exactly that); plus the "
                            "line/rectangle/polygon floors the composite counts are read against"),
    "sketch_add_3d_line": "draw the yaw axis as the skeleton's 3D line",
    "sketch_constrain": ("constrain the skeleton's X axis horizontal; then autoConstrain a loose "
                         "rectangle to fully constrained and re-run it as a no-op, lay a "
                         "rectangular pattern by total EXTENT with the landed centre measured, "
                         "suppress two instances of a 3x2 pattern with the landed flags and the "
                         "curve count both read back; the N-1 flag length, a knob on the wrong "
                         "constraint, and the dimensioning strategies this build does not carry "
                         "all refused"),
    "sketch_move": ("shift a line by a known offset and read the new coordinates back, then spin it "
                    "180 deg about its own midpoint - the swap only the endpoints show; the "
                    "negative-scale mirror and the empty transform refused"),
    "sketch_copy": ("copy a two-line chain to an offset position, its new refs and the endpoints in "
                    "the returned collection both accounted for, then across into a second sketch, "
                    "and again inside a COMPONENT sketch where the refs cross the occurrence-proxy "
                    "seam"),
    "sketch_insert_svg": ("import the logo art into a fresh sketch, its landed width measured "
                          "against the 1/96-inch-per-user-unit convention, then a 96-user-unit "
                          "square at scale 1 whose measured extent pins BOTH halves of that "
                          "landing - one inch square, and Y-DOWN from the sketch origin (min y "
                          "-25.4 mm); the missing file refused"),
    "sketch_dimension": "drive ring/rotor radii by parameter expression",
    "sketch_get": "read the skeleton and ring profiles back",
    "sketch_delete_entity": ("delete a helper constraint; count drops - then a sketch text by its "
                             "index, the deleted string reported back, and the empty index refused"),
    "model_construction": ("offset the carrier hub plane below the rotor sweep; an AXIS on a cameo bore whose published handle the circular pattern turns about; a plane at 30 deg about the shaft's own axis (origin pinned to the axis) and a plane through a cap vertex; then the ON-PATH surface on one measured 30 mm cap edge - a proportional plane and point reading their ratio back with no extent published, an absolute placement inside the path, one before the start and one far past the end (both accepted, both disclosed against the measured length), the boundary exactly at the length, an expression placement whose model parameter is named for param_set, a to-object plane carrying distance AND offset off the path and a second one landing inside a two-edge chained path, and the summed length of that chain; the out-of-range proportional value and to_object on the point kind refused"),
    "sketch_set_text": ("engrave the FUSION ESSENTIALS nameplate; then the path layouts - text "
                        "along a line and wrapped around a closed circle, and fitted to a line - "
                        "each checked against the created text's own definition objectType; a model "
                        "edge as the path, the three cross-mode inputs, and a layout input on an "
                        "edit all refused; then the FONT - named on a create and on an along-path "
                        "create, read back off the landed text both times, then changed on an edit "
                        "beside the string; an unknown name and its case variant refused on create "
                        "with the text count proving nothing landed, the same name refused on an "
                        "edit with the following read showing the string untouched, and a call "
                        "with no font_name publishing no font key at all"),
    "model_extrude": ("extrude the ring bands symmetric about the ring plane, then a three-bay "
                      "frame with 'all' whose payload NAMES the regions enclosed by another "
                      "selected one - the bays that filled with material"),
    "model_revolve": ("revolve the rotor disc about the spin axis, then about an off-origin "
                      "cylinder FACE - the resolved label reads BRepFace and the ring's measured "
                      "bounding box stands around x=30, not around the origin; a planar face as "
                      "the axis refused"),
    "model_loft": "loft the pedestal base-to-post transition",
    "model_sweep": "sweep the crank handle along its path",
    "model_draft": "draft a cameo face",
    "model_mirror": ("mirror a cameo body, then the emboss block's own timeline FEATURE with the "
                     "body/volume census read back, then a join whose isCombine is read off the "
                     "created feature, and meet the join-is-bodies-only refusal"),
    "model_emboss": ("raise then engrave a circular profile on a scratch block's top face, each "
                     "checked against the volume direction; a zero depth refused"),
    "model_replace_face": ("replace a scratch block's top face with an open sheet above it, the "
                           "measured volume move pinning the effect; a solid face as the target "
                           "refused"),
    "model_pipe": ("run a hollow pipe along its path with the wall read back off the feature (and "
                   "NO capped_ends claim - the face collections that would answer that read empty "
                   "on this build), then a half-path pipe whose bounding box proves the extent is a "
                   "FRACTION, a CUT scoped to a named body where the note says the scope is what "
                   "was REQUESTED because participantBodies cannot be read back, and the two "
                   "chaining fixtures: ONE seed handle chains across TANGENT junctions and stops "
                   "where that continuity breaks - several edges on an open run bounded by sharp "
                   "corners, and all eight of a closed tangent loop (which reports itself closed) "
                   "- so what a seed produces is the BUILT path's own count and nothing about the "
                   "request predicts it; the reverse extent refused on an open path"),
    "model_pattern_rectangular": "rectangular-pattern a cameo body",
    "model_pattern_circular": ("circular-pattern a cameo body about a world axis, then about a "
                               "construction axis by handle and by name with the resolved label "
                               "read back, then about the bore FACE itself; the datum name reached "
                               "from the root refused"),
    "model_pattern_path": ("pattern the feature cameo along its own edge with the count read back "
                           "off the feature's own patternElements, then along TWO connected edge "
                           "handles - a list is used EXACTLY, with no chaining, and the label says "
                           "which of the two path rules ran"),
    "assembly_edit_relations": ("suppress/unsuppress the frame lock, re-value the crank link with was_reversed disclosed, and meet the measured set_occurrences refusal in the words that make it a fact - the build it was measured on and the platform sentence it would raise - with the group's members re-read unchanged afterwards"),
    "assembly_edit_contacts": ("build a contact set from two story parts, meet the single-member refusal, re-member it, rename it reading the landed name back, suppress round-trip, switch contact analysis on and back off, then delete it"),
    "model_hole": ("drill a cameo mounting hole, then the three additive placements - centred on "
                   "its rim, on an edge at middle and at start, and by plane offsets; a circular "
                   "offset edge refused"),
    "model_combine": "join two overlapping cameo pads",
    "appearance_set": ("give each gyroscope part its own color; then the occurrence FAN-OUT in the "
                       "shape that discriminates - one body coloured directly, then the occurrence "
                       "written in a DIFFERENT colour, so the body holding its own override comes "
                       "back under 'bodies_not_reached' and not under applied_to (both colours are "
                       "minted from one base asset and share an Appearance.id, so only comparing "
                       "the id AND the name separates reached from kept); and the same shape where "
                       "that body is the occurrence's only one, refused naming it"),
    "model_set_material": "assign the rotor a physical steel material",
    "find_geometry": "acquire the face/edge/body handles the build consumes",
    "model_measure_between": "measure the outer-ring-to-inner-ring gap",
    "model_measure_relation": "read rotor/shaft coaxiality",
    "model_inspect": "read the rotor's volume back",
    "pmi_create": ("aim a flatness note at the frame plate and a hole note at a carrier bore, and "
                   "meet the extension gate PMI authoring sits behind on this build"),
    "pmi_get": ("read the PMI back with segments and detail, and again with an over-cap "
                "max_results - pmi_get's own contract CLAMPS it rather than refusing, since every "
                "record it returns crosses the wire whole. SKIPPED(rig): the imported-row beats "
                "(no 'text' key on an imported annotation, no 'is_hole' when isHoleAnnotation will "
                "not read) need a PMI-BEARING import; the STEP this sweep round-trips carries none"),
    "pmi_edit": ("meet the name lookup on a design holding no PMI - it lists what exists instead of "
                 "editing something else - and the blank-name guard. SKIPPED(gate): the "
                 "ambiguous-name unsuppress refusal needs AUTHORED PMI, which is extension-gated on "
                 "this build (pmi_create's own beats are that gate)"),
    "pmi_delete": "meet the same lookup refusal for the delete",
    "assembly_ground": "ground the frame so the mechanism has a base",
    "assembly_rigid_group": "rigid-group the frame and carrier base",
    "joint_create_origin": "place the crank mount and the stock-center WCS",
    "joint_create": "revolute the yaw, ring pivots, spin, and crank",
    "joint_at_geometry": ("joint a pin in its bore via cylinder faces; then the motion vocabulary on "
                          "its own cameo tree - a BALL on a real sphere face (the centre key point "
                          "named in the payload, no axis and no axis sentence), a revolute on an "
                          "explicit axis whose note says frame, NOT world, and points at the tool "
                          "that sets a true world axis, and a rigid pair with the same axis-free "
                          "report; an axis outside the Choice refused. A TORUS face joints at its "
                          "own centre on a PARAMETRIC body, which is the case the keypoint guard "
                          "must let through. SKIPPED(rig): the two base-feature halves of that "
                          "guard (a torus inside a base feature hands back its component origin "
                          "with no error) need a torus built INSIDE a base feature, and no tool on "
                          "this surface builds one unattended"),
    "joint_create_as_built": ("seat the rotor shaft in the inner ring as-built; then a REVOLUTE "
                              "as-built pair anchored on their shared face, read back through "
                              "assembly_get and driven to prove the DOF, with the missing-anchor "
                              "and rigid-plus-anchor refusals"),
    "joint_edit": "set rotation limits on the yaw",
    "joint_motion_link": "couple the crank to the rotor spin at 2:1; the vise jaws at -1 (self-centering)",
    "joint_drive": "drive every axis, the crank -> rotor 2:1, then ONE vise jaw (the link closes the other)",
    "assembly_get": "read the joint wiring, driven angles, and the StockCenter anchor back",
    "assembly_move": "pose a scratch cameo occurrence",
    "assembly_capture_position": "status, discard the pending pose, re-arm and capture",
    "assembly_constrain": "flush-constrain a scratch cameo pair",
    "design_add_instance": ("place two more crank instances and read the landed paths back, the "
                            "second naming the component while two of it already stand; the "
                            "self-nesting target refused"),
    "design_move_occurrence": ("re-parent one of those instances under the frame, the new path and "
                               "the held world position both read back; the root target and the "
                               "self-nesting target refused"),
    "assembly_inspect_interference": "check interference at rest and driven",
    "design_recompute": "recompute the assembly after motion",
    "model_fillet": ("fillet the outer ring edge; then the two path fixtures - one box corner "
                     "rounded into an OPEN tangent run, and all four rounded into a CLOSED tangent "
                     "loop - that the chaining beats read their edge counts off"),
    "model_chamfer": ("chamfer the frame edge, then a second one by distance-and-angle with a "
                      "miter corner, both read back off the created feature"),
    "model_shell": "shell a scratch cap cameo",
    "model_offset_face": "push a scratch block's top face outward",
    "model_thread": ("thread a scratch post M10x1.5 over part of its length with the extent read "
                     "back, an explicit thread standard with its alternatives disclosed, and a "
                     "modeled thread on a second post proving it cut material; then the INTERNAL "
                     "side on a real bore - 'internal' derived from the face's own out-of-material "
                     "normal and checked against the face by the API at add() - and a partial "
                     "thread measured from the LOW end, reading that end back off the feature; an "
                     "unknown call-out, an offset with no length, and a modeled call-out too big "
                     "for the cylinder all refused"),
    "sketch_edit_curve": ("trim, extend, split, fillet, chamfer and offset on one scratch sketch "
                          "per action, with length read-backs; split's two halves must carry "
                          "distinct ids; a chamfer across an offset pair refused"),
    "model_scale": ("uniform x8 and per-axis x*y*z scales with ratio read-backs; unresolvable, "
                    "length, and angle expressions refused; a bare unitless parameter accepted; "
                    "a vertex-anchored scale"),
    "model_move": ("translate, along-axis, rotate and point-to-point move features on a scratch "
                   "block in a SINGLY placed component, each checked against the distance it was "
                   "asked for; the same along-axis move on a component placed TWICE refused naming "
                   "the count and both paths (each instance holds that body somewhere else, and no "
                   "read-back tells a right instance from a wrong one); a face as the axis and any "
                   "faces selection refused"),
    "design_delete_feature": "add a wart feature then delete it; health diff",
    "design_remove_feature": "remove a scratch body and its occurrence; deleting each Remove brings them back",
    "design_delete_occurrence": "delete a scratch occurrence",
    "view_section": "section cut through the gimbal center",
    "view_screenshot": "capture the sectioned mechanism",
    "view_screenshot_multi": "capture the front and top beauty shots",
    "surface_revolve": ("revolve a prep sheet; and the half-disc that closes into the ball joint's "
                        "sphere"),
    "surface_fill": ("seal a closed revolved sphere surface into a solid, the volume measured "
                     "off the result and every tool accounted for; and seal the joint cameo's "
                     "sphere the same way, so the ball beat has a real sphere face to joint at; a "
                     "cell index one past the end refused NAMING the range that exists, with the "
                     "computing input cancelled and nothing created"),
    "surface_thicken": ("thicken the prep sheet, then a four-walled sheet with 'rounded' corners "
                        "read back off the input"),
    "surface_extrude": "extrude prep sheets",
    "surface_offset": "offset a ring face zero and nonzero",
    "surface_extend": ("extend a sheet edge with no alignment given (the payload carries no key, so "
                       "nothing was written), then a second sheet extended with 'align_edges' read "
                       "back"),
    "surface_reverse_normal": "flip a sheet normal",
    "surface_delete_face": "open a bore by deleting a face",
    "surface_patch": ("close the opened bore with a patch, then the same rim at 'tangent' "
                      "continuity and again through one interior RAIL whose landed count is read "
                      "off the input; rails paired with the multi-loop form refused"),
    "sketch_project": ("project the machining boundary; then section the cap on a datum plane with per-source attribution naming the parallel face that contributed nothing, project the cap sketch's line onto the top face reading the reference linkage back, and meet the same-sketch and missing-direction refusals"),
    "surface_trim": "trim a sheet with a cylinder cutter",
    "surface_untrim": "untrim the internal hole loop",
    "surface_create_ruled": ("rule off a sheet's top rim - tangent, normal, along a direction "
                             "entity and at an angle read off the feature - then off a SOLID box "
                             "edge, where only the new sheet is the result; both misuse refusals"),
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
    "mesh_combine": ("combine two mesh copies, the parametric mode and the base feature the write "
                     "ran in both named; then merge two disjoint meshes into one body"),
    "mesh_delete": "delete a scratch mesh body with the design-wide survivor re-scan",
    "mesh_export": "export a mesh to STL",
    "mesh_insert": "re-import the STL mesh",
    "mesh_repair": ("one-touch-fix a healthy mesh (an honest no-op, not a failure), rebuild it with "
                    "the density read back off the feature, stitch-and-remove a fresh mesh TWICE - "
                    "the first welds its duplicate vertices, the second finds nothing of its kind "
                    "to fix and the note has to say so rather than claim a repair - and refuse "
                    "density on a non-rebuild. SKIPPED(rig): the close_holes refusal on a mesh that "
                    "stays open needs an UNFIXABLE open mesh, which nothing in this document can "
                    "build - every mesh here is watertight by construction"),
    "mesh_shell": ("hollow a scratch mesh - the volume DROPS, the body still reads watertight and "
                   "the thickness is read back off the feature, never echoed - then meet the "
                   "platform's own MESH_FAILED_HOLLOW refusal at a thickness past the half-wall "
                   "(measured: an over-thick shell does not quietly cut through, it fails)"),
    "mesh_smooth": "smooth a scan-quality mesh: the triangle count HOLDS STILL and the node coordinates move, which is why a count census cannot judge it",
    "mesh_separate": "split a two-shell mesh into its lumps - the pieces are the auto-named bodies read back from the component",
    "mesh_reverse_normal": "flip an inside-out mesh - confirmed by the signed volume changing sign, not by is_closed",
    "design_edit_timeline": ("roll the marker back a step and to the end, refuse a discard without "
                             "the confirmation, refuse an unknown feature and a bad group range, "
                             "and tag a feature with an attribute then delete it - the value and "
                             "the design-wide count are read back both ways, and a second delete is "
                             "refused; then the 'name@index' form a FeatureRef refusal hands back, "
                             "resolved against each object's OWN .index (the index design_get "
                             "publishes), with the neighbouring index refused as a miss. "
                             "SKIPPED(rig): the AMBIGUOUS-name refusal itself needs two same-named "
                             "timeline features, and no tool on this surface renames a feature, so "
                             "the sweep cannot mint the pair"),
    "param_set": "bump GimbalDia +33%, then restore it",
    "param_delete": "delete a scratch parameter",
    "view_switch_workspace": "switch to Manufacture, then back to Design",
    "cam_get": ("read the CAM job structure, and the recorded-probing slice on a job nothing has "
                "probed: the empty state with its reason named, a scope that invents no measure, "
                "and the units refusal"),
    "cam_edit_tools": ("add mill/drill/turning/center-drill tools; preset add/remove round-trip "
                       "with unit, refusal, and rollback gates"),
    "cam_create_setup": "create the milling setup on the Carrier in the vise",
    "cam_create_operation": "create the face, adaptive, silhouette, and drill operations",
    "cam_select_geometry": ("select the stock-top face, both silhouette branches (setup models and "
                            "named bodies), a whole scratch sketch and the bolt-circle holes; "
                            "refusals for a knob on the wrong kind, geometry through the wrong "
                            "input, and an edge where a face belongs. The pocket-recognition "
                            "selection is NOT driven unattended (running it coincides with the "
                            "Fusion process terminating); its 'pocket_filter_applied' publishes the "
                            "diameter/depth bounds in the CALLER'S own units, with "
                            "'pocket_filter_units' naming them beside the numbers"),
    "cam_edit_operation": "edit the face operation's feed",
    "cam_create_machine": ("build a run-stamped 3-axis machine into the Local library, find it in "
                           "the catalog, assign it to the setup, and refuse the duplicate name"),
    "cam_edit_setup": "real stock + vise fixture bodies; WCS bound to the stock-center JO (bound read back); Haas VF-2 assigned",
    "cam_edit_folders": "organize the job into Milling and Drilling folders",
    "cam_reorder": "reorder the adaptive before the face op",
    "cam_activate_setup": "activate the setup",
    "cam_compare_operations": "compare the two operations",
    "cam_show_toolpath": "leave the toolpath visible on camera",
    "cam_generate": ("generate the toolpaths against the real part in the real fixture. The tool "
                     "takes no 'pump_seconds': CAM-7 confirms the kernel refuses to be pumped while "
                     "a generation runs, so completion is certified by the bounded cam_get_status "
                     "poll after this act, never by a sleep inside the call"),
    "cam_inspect_toolpaths": ("verdict false with named ops before generation, scoped check, "
                              "bogus-scope refusal, an over-cap max_results clamped to the tool's "
                              "own row ceiling, verdict true after generation"),
    "cam_get_status": "poll the generation to completion (empty toolpaths fail)",
    "cam_post": "post the NC program to disk",
    "cam_generate_setup_sheet": "write the machinist setup sheet with the file-landed gate",
    "cam_set_nc_comment": "stamp the NC program comment",
    "cam_save_template": "save the setup as a local CAM template",
    "cam_apply_template": "apply the template to a second setup",
    "cam_delete": "delete a scratch operation; count diff",
    "design_export": ("export the machined part to STEP, then the whole design SPLIT per component "
                      "to STL with stl_binary read back off the options object the split path "
                      "created for each file - the branch that would otherwise report a clean "
                      "export while dropping the format knob"),
    "doc_insert_import": "re-import that STEP from disk into the live design",
    "design_set_name": ("rename the machined part and re-find it by the name that landed, rename a "
                        "cameo occurrence with its instance name following, give a twin body the "
                        "name its sibling holds so the deduped '(1)' is what gets published, and "
                        "rename a MESH body - the kind reads 'mesh' and a fresh read of the "
                        "component's meshes carries the new name; the empty target and the root "
                        "component refused"),
    "design_get": ("final design read: the whole cast, stamped with the DOCUMENT it was read from "
                   "(sys_find_tool, which never touches the design, carries no such stamp), the "
                   "timeline slice the 'name@index' feature form is addressed from, plus the "
                   "material/appearance catalog at both zoom levels - the library census and one "
                   "paged library; an unloaded library name refused"),
    "drawing_create": ("meet every guard the drawing generator sits behind, each settled before the "
                       "tool reaches for a cloud source: the shaded style with no member to set, "
                       "the two centre annotations with no enum family on this build, a tangent-edge "
                       "value outside the Choice, manual creation with no template, and a sheet size "
                       "from the other standard - none of them creating anything, and the session "
                       "healthy afterwards. The creation path is cloud tier (it needs a saved source "
                       "design) and stays out of the default sweep"),
    "doc_get": "read the document identity before discarding",
    "doc_close": "discard the document on camera - clean teardown",
}

# Tools deliberately not swept unattended, each with its reason (the ledger's skipped rows). This is
# the policy-excluded bucket; PENDING (below) is the separate "not scripted yet" bucket - the ledger
# keeps that distinction honest.
EXCLUDED = {
    "sys_execute_script": ("gated off by design; the sweep proves the typed surface suffices - and "
                           "with it the beat for its DRAWING-document error tail (a raise inside a "
                           "drawing ends with 'Re-read the sheets before assuming this call changed "
                           "nothing.', a design one does not), which would need this tool driven "
                           "against two document kinds"),
    "sys_reload_addin": "restarts the server mid-sweep",
    "sys_request_selection": "waits on a human pick (user-present tier)",
    "drawing_update": "user-present tier (drawing docs)",
    "drawing_export": "user-present tier (drawing docs)",
    "drawing_get": "user-present tier (drawing docs); read-only - drawing_verify.py drives it",
    "drawing_add_sketch": "user-present tier (drawing docs)",
    "drawing_dimension": "user-present tier (drawing docs)",
    "drawing_edit_sheet": "user-present tier (drawing docs)",
    "drawing_insert_image": "user-present tier (drawing docs)",
    "design_set_mode": "irreversible parametric->direct conversion; not run unattended",
    "design_configure": "configuration table needs a SAVED document (a DataFile to carry it); opt-in tier - the appearance/material columns need that document too, and a body's material reads back only after the geometry catches up with the activation",
    # cloud tier: writes to the operator's real hub - opt-in only, never in the default sweep.
    "data_create_project": "cloud write to the operator's real hub (opt-in tier)",
    "data_create_folder": "cloud write (opt-in tier)",
    "data_upload_file": "cloud write (opt-in tier)",
    "data_get": "cloud read, hub-dependent (opt-in tier)",
    "data_get_upload_status": "cloud read (opt-in tier)",
    "data_download_file": "cloud read to the local disk (opt-in tier)",
    "data_move_file": "cloud write - relocates a real file in the operator's hub (opt-in tier)",
    "data_delete_file": "cloud destructive (opt-in tier)",
    "data_delete_folder": "cloud destructive (opt-in tier)",
    "data_switch_hub": "changes the active hub, closes docs (opt-in tier)",
    "doc_save_milestone": ("cloud write; needs a saved MODIFIED doc (opt-in tier) - the beat for "
                           "its two INDEPENDENT read-backs (cloud_tip_advanced beside "
                           "version_confirmed) rides that tier with it"),
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
    """SHA-256 over every .py under commands/mcpServer/ PLUS this harness and its siblings under
    tests/live/ - the receipt key binding a green run to the exact tool source AND the exact
    predicates/exclusions it was judged by (a weakened predicate or a tool quietly moved into
    EXCLUDED must invalidate the receipt, not ride under it). Relative paths are normalized to
    '/' and CRLF to LF so the digest is identical across OS and git line-ending config;
    __pycache__ is skipped."""
    root = root or SRC_ROOT
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                entries.append((rel, full))
    if root == SRC_ROOT:      # a custom root (the offline tests') hashes only itself
        for fn in sorted(os.listdir(_HERE)):
            if fn.endswith(".py"):
                entries.append(("tests_live/" + fn, os.path.join(_HERE, fn)))
    hasher = hashlib.sha256()
    for rel, full in sorted(entries):
        with open(full, "rb") as fh:
            content = fh.read().replace(b"\r\n", b"\n")
        hasher.update(rel.encode("utf-8") + b"\0" + content + b"\0")
    return hasher.hexdigest()


_STAMP_RE = re.compile(r"^Stamp: source ([0-9a-f]{64}) \| Fusion (\S+) \| verified (\S+)",
                       re.MULTILINE)


def write_verified(ledger, fusion_version, stamp_date, src_hash, path=None, notes=None,
                   act_modes=None):
    """Write the tracked receipt. Called only on a run with zero FAIL/blocked/pass* steps. 'notes'
    maps a covered tool to its shot-list step text (the ledger doubles as the demo's shot list) - a
    third column, empty when absent so the two-column stamp/count contract is unchanged.
    'act_modes' lists (act, narrative|fallback) - a machine-readable column, so a fallback-heavy
    run is visible without reading prose."""
    notes = notes or {}
    n_cov = sum(1 for _, s in ledger if s == "covered")
    n_pend = sum(1 for _, s in ledger if s.startswith("PENDING"))
    n_ref = sum(1 for _, s in ledger if s.startswith("refusals-only"))
    n_skip = len(ledger) - n_cov - n_pend - n_ref
    lines = [
        "# Live tool verification (generated by tool_verify.py - do not edit)",
        "",
        "This is a FOUR-BUCKET ledger, not a clean bill of health. It does NOT claim every tool",
        "is verified - the count line below is authoritative, and the per-tool table says which",
        "bucket each tool is in:",
        "",
        "- covered: a live step drove the tool this run and its effect was read back.",
        "- refusals-only: every step that ran was a deliberate guard refusal - the guards are",
        "  proven, but NO effect was produced or read back. Not covered; the create/act path",
        "  still needs a real step or a recorded gate reason.",
        "- skipped(reason): deliberately NOT driven unattended (cloud / interactive / irreversible",
        "  tier), each row naming why. Not verified - excused.",
        "- pending: no step drives it yet. UNVERIFIED, not known-good - it has never run in this",
        "  sweep. Shrinking this bucket means scripting a real step, not relabelling it.",
        "",
        "The stamp's source hash binds this run to the exact `commands/mcpServer/` tree AND the",
        "tests/live/ harness (steps, predicates, exclusions) it was judged by: `--check`",
        "recomputes the hash and fails on any difference, so a green suite cannot ride on a live",
        "run that never saw the current code or a weakened predicate. Only a run with zero",
        "FAIL/blocked/pass* steps rewrites this file.",
        "",
        "Stamp: source {0} | Fusion {1} | verified {2}".format(src_hash, fusion_version, stamp_date),
        "",
        "{0} covered / {1} refusals-only / {2} skipped(reason) / {3} pending".format(
            n_cov, n_ref, n_skip, n_pend),
    ]
    if act_modes:
        lines += ["", "| act | mode |", "|---|---|"]
        lines += ["| {0} | {1} |".format(a, m) for a, m in act_modes]
    lines += [
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


def run_steps(steps, ctx, trace=False, sleep_s=0.1, on_result=None):
    """The ONE (tool, args, expect, save) step engine - every live harness judges its steps here,
    so the status vocabulary cannot fork: pass / pass* / expected-refusal / FAIL / blocked.
    'pass*' means a passing step whose saved-value extraction failed - a payload-shape mismatch
    that BLOCKS a green receipt/run in every consumer, never a quiet pass. A failing step keeps
    NOTE_MAX characters of its payload (the keys that diagnose it sit late), a passing refusal
    REFUSAL_NOTE_MAX. Yields nothing early:
    returns the full row list; 'on_result' (tool, status, note) fires per step for a consumer
    that prints as it goes."""
    rows = []
    for tool, args, expect, save in steps:
        try:
            arguments = args(ctx) if callable(args) else dict(args)
        except KeyError as e:
            rows.append((tool, "blocked", str(e)))
            if on_result:
                on_result(*rows[-1])
            continue
        if trace:
            # flushed per step so a hard Fusion crash still names its killer in the log
            print(f"    -> {tool} {json.dumps(arguments)[:120]}", flush=True)
        is_error, payload = call(tool, arguments)
        if isinstance(expect, _Refusal):
            # a refusal whose WORDS are the assertion: the error must carry every fragment, so a
            # guard refusing for another reason fails the row instead of passing as "refused".
            if not is_error:
                status, note = "FAIL", f"expected a refusal, got ok: {str(payload)[:NOTE_MAX]}"
            else:
                absent = expect.missing(str(payload))
                if absent:
                    status, note = "FAIL", f"refusal missing {absent}: {str(payload)[:NOTE_MAX]}"
                else:
                    status, note = "expected-refusal", str(payload)[:REFUSAL_NOTE_MAX]
        elif callable(expect):
            # a VALUE PREDICATE on an ok result: call success is not enough - the payload
            # must satisfy the check (grip contact, machine assignment, rest-pose honesty).
            if is_error:
                status, note = "FAIL", str(payload)[:NOTE_MAX]
            else:
                try:
                    good = bool(expect(payload))
                except Exception as e:
                    good, payload = False, f"predicate raised: {e}"
                status, note = ("pass", "") if good else ("FAIL", str(payload)[:NOTE_MAX])
        elif expect == "ok" and not is_error:
            status, note = "pass", ""
        elif expect == "refused" and is_error:
            status, note = "expected-refusal", str(payload)[:REFUSAL_NOTE_MAX]
        else:
            status, note = "FAIL", str(payload)[:NOTE_MAX]
        if status == "pass" and save is not None and not is_error:
            key, extract = save
            try:
                ctx[key] = extract(payload)
            except Exception as e:
                status, note = "pass*", f"saved-value extraction failed: {e}"
        rows.append((tool, status, note))
        if on_result:
            on_result(*rows[-1])
        time.sleep(sleep_s)
    return rows


def _precondition_holds(pre):
    """Run an act's precondition READ; True when it returns without error (the geometry the act's
    narrative consumes exists). A False routes the act to its scratch fallback."""
    tool, args = pre
    is_error, _ = call(tool, args)
    return not is_error


def run(write_json, keep_open=False, trace=False):
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
        for tool, status, note in run_steps(steps, ctx, trace=trace):
            rows.append((tool, status, note))
            if status in ("pass", "pass*", "expected-refusal"):
                story = STORY.get(tool, "")
                notes[tool] = (story + " (fallback fixture)").strip() if mode == "fallback" else story
        if name in POLL_AFTER:
            poll_generation(rows, notes, POLL_AFTER[name][mode])
    if keep_open:
        print("\n--keep-open: the story document is left open for inspection.")

    # covered demands at least one step that PRODUCED something (pass/pass*): a tool whose every
    # step is an expected-refusal exercised only its guards - no effect existed to read back, so
    # calling that "covered" would let the legend lie. Those rows get their own bucket.
    passed = {t for t, s, _ in rows if s in ("pass", "pass*")}
    refused_only = {t for t, s, _ in rows if s == "expected-refusal"} - passed
    ledger = []
    for tool in all_tools:
        if tool in passed:
            ledger.append((tool, "covered"))
        elif tool in refused_only:
            ledger.append((tool, "refusals-only: every step is a guard refusal - no effect was "
                                 "produced or read back this run"))
        elif tool in EXCLUDED:
            ledger.append((tool, f"skipped: {EXCLUDED[tool]}"))
        else:
            ledger.append((tool, "PENDING (no step yet)"))

    print(f"\n== step results ({len(rows)}):")
    for tool, status, note in rows:
        print(f"  {status:18} {tool:28} {note}")
    n_cov = sum(1 for _, s in ledger if s == "covered")
    n_pend = sum(1 for _, s in ledger if s.startswith("PENDING"))
    n_ref = sum(1 for _, s in ledger if s.startswith("refusals-only"))
    n_skip = len(ledger) - n_cov - n_pend - n_ref
    print(f"\n== ledger: {n_cov}/{len(ledger)} covered, {n_ref} refusals-only, "
          f"{n_skip} skipped(reason), {n_pend} pending")
    for tool, s in ledger:
        if s != "covered":
            print(f"  {tool:32} {s}")

    n_narr = sum(1 for _, m in act_modes if m == "narrative")
    n_fb = sum(1 for _, m in act_modes if m == "fallback")
    print(f"\n== acts: {n_narr} narrative / {n_fb} fallback (of {len(act_modes)})")
    for nm, m in act_modes:
        print(f"  {m:10} {nm}")

    # pass* blocks the receipt: the payload did not carry a key the step contract expected - a
    # payload-shape mismatch is a real signal, not a pass.
    fails = [r for r in rows if r[1] in ("FAIL", "blocked", "pass*")]
    if fails:
        print("\nVERIFIED_TOOLS.md NOT rewritten - resolve the FAIL/blocked/pass* steps first.")
    else:
        src_hash = source_hash()
        stamp_date = time.strftime("%Y-%m-%d")
        fusion_version = ctx.get("fusion_version", "?")
        print("\nwrote {0} (stamp: source {1}..., Fusion {2}, {3})".format(
            write_verified(ledger, fusion_version, stamp_date, src_hash, notes=notes,
                           act_modes=act_modes),
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
    ap.add_argument("--trace", action="store_true",
                    help="print each step (flushed) before it runs, so a Fusion crash names its killer")
    args = ap.parse_args()
    sys.exit(check() if args.check else run(args.json, keep_open=args.keep_open, trace=args.trace))
