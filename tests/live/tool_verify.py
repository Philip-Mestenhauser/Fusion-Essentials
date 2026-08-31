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

The expectation's SHAPE is also what buckets the tool in the receipt: a passing value predicate
earns 'covered', a passing bare "ok" earns only 'called' (see ``predicate_kind``), so the count
line separates the tools whose effect was read back from the tools that merely did not fail.
"""

import argparse
import dis
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

# Two things this run stores OUTSIDE the document it discards: a machine in the LOCAL machine
# library and a template in the LOCAL template library. Each is taken back out by its own teardown
# beat at the end of the CAM deliverables act, in the same branch that created it and judged on
# that delete's own read-backs. Each name carries a run stamp, which guards the window while the
# asset EXISTS: two overlapping runs, or a run that died before its teardown, must not collide on
# one name - the machine create refuses a duplicate, and cam_save_template always writes a NEW
# template, so a repeated template name leaves one more asset in the library per run.
MACHINE_NAME = "SweepMach3Axis " + time.strftime("%Y%m%d-%H%M%S")
TEMPLATE_NAME = "GyroTmpl " + time.strftime("%Y%m%d-%H%M%S")

# How much of a failing step's payload the ledger keeps. A FAIL row is read to DIAGNOSE, and the
# keys that carry the diagnosis (a measured extent, a change list) sit late in a payload - at 160
# characters they were cut off, which costs a whole live re-run to recover. A passing refusal is
# read only as confirmation, so its note stays short.
NOTE_MAX = 480
REFUSAL_NOTE_MAX = 80
# Pause between steps. UNDER MEASUREMENT: at 0.1 it was 121s of a 402s run - 30% of the budget -
# with no recorded reason, against a file whose own doctrine says an async wait is a bounded poll
# "never by a sleep inside the call". Held at 0.0 while three consecutive runs decide whether it
# was hiding a main-thread race; if they are clean the sleep goes, if one flakes the step that
# flaked gets its own bounded poll rather than a blanket pause.
STEP_SLEEP_S = 0.0

# The wall-clock the sweep has to finish inside, and the shell ceiling it is drawn from. Every agent
# runs this file through a shell tool that is killed at 600 s, and a kill leaves no receipt at all -
# so the run fails ITSELF at the lower number, with its act and tool breakdowns printed, while there
# is still room to say so.
_SHELL_TIMEOUT_S = 600.0
_RUNTIME_BUDGET_S = 540.0

# The pseudo-tool a showcase beat uses to hold a view on screen. Never dispatched to the server and
# never counted as coverage - STEPS filters it out.
_DWELL = "_dwell"

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


# --- the DAG ----------------------------------------------------------------------------------
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


class Parked:
    """expect=Parked("reason") - a step deliberately left at a bare "ok", with the reason rendered.

    A parked row's reason otherwise lives only in a source comment, so the receipt shows a plain
    'called' and the reader cannot tell a not-yet-written predicate from a deliberately held one.
    Wrapping the expectation carries the reason onto the ledger ('called (reason)') while the
    expectation itself is judged exactly as if it were passed bare. The wrapper is OPTIONAL: a step
    that gains a real predicate drops it, and ``Parked(reason, predicate)`` keeps the reason on a
    step whose expectation is something other than "ok"."""

    def __init__(self, reason, expect="ok"):
        self.reason = reason
        self.expect = expect


def _unparked(expect):
    """The expectation a step is actually judged by - a Parked wrapper's inner expectation, or the
    expectation itself."""
    return expect.expect if isinstance(expect, Parked) else expect


# Bytecode classes for the value-predicate guard below. A PUSH leaves the argument's fate to a later
# instruction; an INSPECT reads INTO the argument (an attribute or a subscript); anything else
# consumes it without looking inside - a truthiness test, an identity compare, a bare call.
_PUSH_OPS = ("LOAD_CONST", "LOAD_SMALL_INT", "LOAD_GLOBAL", "LOAD_FAST", "LOAD_DEREF", "LOAD_NAME",
             "LOAD_CLOSURE", "PUSH_NULL", "MAKE_FUNCTION", "COPY", "NOP", "RESUME", "CACHE",
             "EXTENDED_ARG")
_INSPECT_OPS = ("LOAD_ATTR", "LOAD_METHOD", "BINARY_SUBSCR")


def _is_inspect(ins):
    """True for an instruction that reads INTO the value on top of the stack. 3.11 emits
    LOAD_METHOD where 3.12+ emits LOAD_ATTR, and 3.14 folds the subscript into BINARY_OP - dis
    renders that oparg as '[]', which is what identifies it there."""
    return (ins.opname.startswith(_INSPECT_OPS)
            or (ins.opname == "BINARY_OP" and ins.argrepr == "[]"))


_ARG_LOAD_OPS = ("LOAD_FAST", "LOAD_DEREF")


def _arg_load_sites(instructions, arg):
    """Indexes where `arg` is pushed and is the value left on top. LOAD_FAST carries the name in
    argval; the fused 3.13+ forms (LOAD_FAST_LOAD_FAST) carry a TUPLE of names, and 3.14 renames
    some to LOAD_FAST_BORROW - so the test is on the opname PREFIX and on either argval shape. A
    predicate whose own inner comprehension closes over the payload makes the argument a CELL, and
    every read of it then compiles to LOAD_DEREF, so that form counts too. A fused push that leaves
    a DIFFERENT name on top is skipped: what the next instruction does describes that other value,
    not this one."""
    sites = []
    for i, ins in enumerate(instructions):
        if not ins.opname.startswith(_ARG_LOAD_OPS):
            continue
        val = ins.argval
        if val == arg or (isinstance(val, tuple) and val and val[-1] == arg):
            sites.append(i)
    return sites


# Calls that consume a value while reading only its TRUTHINESS - handing the payload to one of
# these inspects no more of it than `if payload:` would, so it is not a content read.
_TRUTHY_ONLY_CALLS = {"bool"}


def _callee_name(instructions, i):
    """The NAME a call consuming the argument loaded at `i` is calling - the nearest name pushed
    before the argument - or None when the callee is not a plain name (a lambda built inline, a
    subscripted callable)."""
    for j in range(i - 1, -1, -1):
        ins = instructions[j]
        if ins.opname.startswith(("LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF", "LOAD_CLOSURE",
                                  "LOAD_ATTR", "LOAD_METHOD")):
            return ins.argval
        if not ins.opname.startswith(_PUSH_OPS):
            return None
    return None


def _inspects_argument(code, arg):
    """True when the bytecode reads the CONTENT of `arg`: the argument is pushed and the first
    instruction that consumes it reads an attribute off it, subscripts it, or hands it to a call
    that is not one of the truthiness-only builtins. Any one such load site is enough."""
    instructions = list(dis.get_instructions(code))
    for i in _arg_load_sites(instructions, arg):
        for nxt in instructions[i + 1:]:
            if nxt.opname.startswith(_PUSH_OPS):
                continue
            if _is_inspect(nxt):
                return True
            if (nxt.opname.startswith("CALL")
                    and _callee_name(instructions, i) not in _TRUTHY_ONLY_CALLS):
                return True
            break
    return False


def _inspects_payload(expect):
    """True when a callable expectation READS the payload it is handed.

    LOADING the argument is not enough: ``lambda p: p and True``, ``bool(p)`` and
    ``p is not None`` all touch the argument while reading NOTHING out of it, and each would
    otherwise be counted 'covered' while proving no more than a bare "ok". So the argument must be
    subscripted, have an attribute read off it, or be handed to a call that reads its content
    (``str(p)``, ``_helper(p)`` - but not ``bool(p)``, which reads only truthiness). A callable
    with no inspectable code object (a builtin, a C callable) is taken at its word."""
    code = getattr(expect, "__code__", None)
    if code is None:
        return True
    if code.co_argcount < 1:
        return False
    return _inspects_argument(code, code.co_varnames[0])


def predicate_kind(expect):
    """Which BUCKET a step's expectation earns its tool: 'value', 'call' or 'refusal'.

    - 'value'   - a callable predicate that reads keys off the ok payload, so a pass is evidence
                  the effect landed (the ledger's 'covered').
    - 'call'    - the bare "ok" string (or a callable that ignores its payload): the call returned
                  without isError and NOTHING in the payload was read (the ledger's 'called').
    - 'refusal' - "refused" / ``_refused(...)``: a guard beat, which produces no effect to read.

    This is what splits the receipt's two honest buckets apart, so it classifies the step's
    expectation OBJECT - never the tool, the args or the run's outcome. A Parked wrapper is
    transparent here: the reason it carries changes the ledger's wording, never the bucket."""
    expect = _unparked(expect)
    if isinstance(expect, _Refusal) or expect == "refused":
        return "refusal"
    if callable(expect):
        return "value" if _inspects_payload(expect) else "call"
    return "call"


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


# Values a LATER step's predicate has to compare against. A predicate is handed the payload alone,
# deliberately, so it cannot drift into reading run state - but a before/after check genuinely needs
# the before, and re-reading it at assert time would only compare the model to itself.
_RECALL = {}


def _recall(key, pick):
    """A save-slot extractor that also parks its value under 'key' for a later predicate."""
    def take(payload):
        _RECALL[key] = pick(payload)
        return _RECALL[key]
    return take


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


# --- model_* value predicates -----------------------------------------------------------------
# Each reads keys the tool PUBLISHES on the call shape its step uses (taken from the tool source),
# so a pass is evidence the effect landed rather than evidence the call returned. Where a step's
# shape carries no honest value to read, the step stays a bare "ok" and its tool lands in the
# receipt's 'called' bucket - the queue for the next tranche. Each check reports the values it read
# through _measured, so a miss is diagnosable from the ledger line alone.

def _made_component(p):
    """model_create_component(activate=True): the new occurrence answers its own name and full path
    back, and 'activated' is what Occurrence.activate() RETURNED, not what the step asked for."""
    return _measured("component create read-back",
                     {"occurrence": p.get("occurrence"), "full_path": p.get("full_path"),
                      "activated": p.get("activated")},
                     bool(p.get("occurrence")) and bool(p.get("full_path"))
                     and p.get("activated") is True)


def _made_component_inactive(p):
    """model_create_component(activate=False): the same read-back, with activate() not called - so
    'activated' must be False rather than merely falsy."""
    return _measured("component create read-back (not activated)",
                     {"occurrence": p.get("occurrence"), "full_path": p.get("full_path"),
                      "activated": p.get("activated")},
                     bool(p.get("occurrence")) and bool(p.get("full_path"))
                     and p.get("activated") is False)


# which component of a datum plane's normal must be the unit one, per base plane
_PLANE_NORMAL_AXIS = {"xy": 2, "xz": 1, "yz": 0}


def _datum_plane(base):
    """model_construction(kind='plane', mode offset from a world plane): the CREATED datum's own
    normal, read back off the object (not echoed), points along the base plane's axis. Sign is not
    asserted - an offset plane's normal may face either way."""
    i = _PLANE_NORMAL_AXIS[base]

    def check(p):
        n = (p.get("geometry") or {}).get("normal")
        aligned = (isinstance(n, list) and len(n) == 3
                   and abs(n[i]) > 0.999
                   and all(abs(v) < 0.001 for j, v in enumerate(n) if j != i))
        return _measured(f"datum plane normal along the {base} plane's axis",
                         {"name": p.get("name"), "normal": n},
                         bool(p.get("name")) and aligned)
    return check


def _dim_measures(mm, tol=0.05):
    """sketch_dimension: the number the dimension MEASURED, in mm. A dimension that attached to the
    neighbouring entity, or read the right one in centimetres, still returns ok - the measured value
    is the only thing that tells those apart. Translation-invariant, so the layout pass moving the
    bench cannot change it."""
    def check(p):
        parts = (p.get("value") or "").split()
        try:
            got = float(parts[0])
        except (IndexError, ValueError):
            got = None
        return _measured(f"dimension measures {mm} mm",
                         {"dim_type": p.get("dim_type"), "value": p.get("value")},
                         got is not None and abs(got - mm) < tol)
    return check


def _datum(kind):
    """model_construction in any of its geometry-driven build modes: the datum LANDED. It carries
    the kind that was asked for, an entityToken the next call can point AT, and a geometry read off
    the CREATED object - not an echo of the request, which every mode would satisfy identically."""
    def check(p):
        g = p.get("geometry") or {}
        return _measured(f"{kind} datum landed",
                         {"mode": p.get("mode"), "name": p.get("name"), "geometry": g},
                         p.get("created") is True and p.get("kind") == kind
                         and bool(p.get("handle")) and bool(g))
    return check


def _result_bodies(label):
    """The shared feature-landed read for the solid builders: the feature named itself and
    'result_bodies' is the walk over the bodies that feature actually produced."""
    def check(p):
        return _measured(label + " result", {"feature": p.get("feature"),
                                             "result_bodies": p.get("result_bodies")},
                         bool(p.get("feature")) and bool(p.get("result_bodies")))
    return check


_extruded = _result_bodies("extrude")
_revolved = _result_bodies("revolve")
_swept = _result_bodies("sweep")
_lofted = _result_bodies("loft")


def _material_assigned(p):
    """model_set_material: every applied row carries the material name READ BACK off the body after
    the assignment (a silent no-op leaves a name that does not match and lands in 'failed')."""
    rows = p.get("applied_to") or []
    return _measured("material read-back per body",
                     {"applied_to": rows[:3], "failed": p.get("failed")},
                     bool(rows) and all(r.get("material") for r in rows) and not p.get("failed"))


def _gap_measured(p):
    """model_measure_between (distance mode): a real number came back off measureMinimumDistance -
    the tool refuses a non-numeric value rather than publishing 0, so a number here is the
    measurement. The VALUE is not asserted: the gap is whatever the model holds."""
    d = p.get("distance")
    return _measured("minimum distance",
                     {"mode": p.get("mode"), "distance": d,
                      "closest_point_on_a": p.get("closest_point_on_a")},
                     p.get("mode") == "distance" and isinstance(d, (int, float))
                     and not isinstance(d, bool))


def _relation_measured(relation):
    """model_measure_relation: the named relation's own measurement came back as a number and the
    verdict as a boolean. The verdict VALUE is not asserted where the geometry decides it."""
    def check(p):
        ang = (p.get("measured") or {}).get("angle_deg")
        return _measured(f"{relation} measurement",
                         {"relation": p.get("relation"), "measured": p.get("measured"),
                          "passed": p.get("passed")},
                         p.get("relation") == relation and isinstance(p.get("passed"), bool)
                         and isinstance(ang, (int, float)) and not isinstance(ang, bool))
    return check


def _rebuilt(method, density):
    """mesh_repair(repair_type='rebuild'): the method that ran and the density it ran at, the
    latter read off the feature's own ModelParameter. A method the API silently swapped, or a
    density it clamped, differs here - the request alone would agree with itself."""
    def check(p):
        return _measured(f"rebuild ran {method} at density {density}",
                         {"rebuild_method": p.get("rebuild_method"), "density": p.get("density"),
                          "unverified": p.get("density_unverified")},
                         p.get("repaired") is True and p.get("rebuild_method") == method
                         and p.get("density") == density and "density_unverified" not in p)
    return check


def _relation_read(relation, key):
    """model_measure_relation for the relations that do NOT report an angle. Each relation measures
    its own quantity - an angle for the alignments, a distance for the fits - so the key it has to
    publish is named per relation instead of assumed, and a relation answering with someone else's
    measurement fails here."""
    def check(p):
        got = (p.get("measured") or {}).get(key)
        return _measured(f"{relation} measured {key}",
                         {"relation": p.get("relation"), "measured": p.get("measured"),
                          "passed": p.get("passed")},
                         p.get("relation") == relation and isinstance(p.get("passed"), bool)
                         and isinstance(got, (int, float)) and not isinstance(got, bool))
    return check


def _relation_passes(relation):
    """The same read where the step's own geometry settles the verdict (an entity compared with
    ITSELF is parallel to itself), so the boolean is asserted too."""
    inner = _relation_measured(relation)

    def check(p):
        return inner(p) and _measured(f"{relation} verdict", {"passed": p.get("passed"),
                                                              "note": (p.get("note") or "")[:80]},
                                      p.get("passed") is True)
    return check


def _extent_measured(p):
    """model_inspect (default bbox): the three extents came back as POSITIVE numbers off the
    target's bodies-only AABB - a target with no measurable solid reads null instead."""
    x, y, z = p.get("x"), p.get("y"), p.get("z")
    return _measured("bounding-box extents", {"x": x, "y": y, "z": z, "units": p.get("units")},
                     all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
                         for v in (x, y, z)))


def _drafted(p):
    """model_draft: 'faces_drafted' is feature.faces.count - the count the FEATURE reports, null
    when it could not be read."""
    n = p.get("faces_drafted")
    return _measured("faces the draft feature took",
                     {"faces_drafted": n, "faces_requested": p.get("faces_requested")},
                     isinstance(n, int) and not isinstance(n, bool) and n >= 1)


def _drilled(count):
    """model_hole: 'holes_verified' says the DRILL AXES were counted off the created feature (the
    read that catches a point which cut nothing), and 'holes' is that verified count."""
    def check(p):
        return _measured(f"holes drilled (want {count}, axis-verified)",
                         {"holes": p.get("holes"), "holes_verified": p.get("holes_verified"),
                          "points": p.get("points")},
                         p.get("holes_verified") is True and p.get("holes") == count)
    return check


def _mirrored(p):
    """model_mirror: the feature's own result bodies, plus the effect signal the tool measured -
    a body added to the host census, or a volume that moved."""
    return _measured("mirror effect",
                     {"result_bodies": p.get("result_bodies"),
                      "bodies_added": p.get("bodies_added"),
                      "volume_change_cm3": p.get("volume_change_cm3")},
                     bool(p.get("result_bodies"))
                     and bool(p.get("bodies_added") or p.get("volume_change_cm3")))


def _patterned(key, count):
    """model_pattern_rectangular / _circular: the instance count the tool publishes, which is
    feature.patternElements.count when that read answered."""
    def check(p):
        return _measured(f"pattern {key} (want {count})",
                         {key: p.get(key), "feature": p.get("feature"), "type": p.get("type")},
                         p.get(key) == count and bool(p.get("feature")))
    return check


def _joined(p):
    """model_combine(join): the result body's lump count, read FRESH off the joined body - one lump
    is what fusing two touching solids produces, and the tool flags a disjoint join instead."""
    return _measured("join fused into one lump",
                     {"lump_count": p.get("lump_count"), "disjoint_join": p.get("disjoint_join"),
                      "bodies_remaining": p.get("bodies_remaining")},
                     p.get("lump_count") == 1 and not p.get("disjoint_join"))


def _edge_feature(kind):
    """model_fillet / model_chamfer: 'faces_created' is read from the created feature (corner
    patches count too, so it can exceed the edges requested)."""
    def check(p):
        n = p.get("faces_created")
        return _measured(f"{kind} faces created",
                         {"faces_created": n, "edges_requested": p.get("edges_requested"),
                          "feature": p.get("feature")},
                         isinstance(n, int) and not isinstance(n, bool) and n >= 1
                         and bool(p.get("feature")))
    return check


_filleted = _edge_feature("fillet")
_chamfered = _edge_feature("chamfer")


def _shelled(p):
    """model_shell: the before/after volume difference over the shelled body - a hollowing removes
    material, and the key is published only where both reads answered."""
    v = p.get("volume_removed_cm3")
    return _measured("volume the shell removed",
                     {"volume_removed_cm3": v, "faces_delta": p.get("faces_delta"),
                      "result_bodies": p.get("result_bodies")},
                     isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0)


def _offset_faces(p):
    """model_offset_face: the affected body's volume delta - the tool refuses an unchanged volume,
    so a published non-zero delta is the push/pull that landed."""
    v = p.get("volume_delta_cm3")
    return _measured("volume the face offset moved",
                     {"volume_delta_cm3": v, "bodies": p.get("bodies")},
                     isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0)


def _moved(p):
    """model_move: 'displacement' is measured from the bodies' own points before and after, not
    from the request."""
    d = p.get("displacement")
    return _measured("measured displacement",
                     {"displacement": d, "units": p.get("units"), "mode": p.get("mode")},
                     isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0)


def _split_bodies(p):
    """model_split(body): the piece count - the tool errors below 2, and this reads the number."""
    n = p.get("result_count")
    return _measured("pieces the split produced",
                     {"result_count": n, "result_bodies": p.get("result_bodies")},
                     isinstance(n, int) and not isinstance(n, bool) and n >= 2)


def _unstitched(p):
    """model_unstitch: the surface bodies the explode produced, counted off the feature's own
    result, beside the host census before/after."""
    n = p.get("surface_body_count")
    return _measured("surface bodies the unstitch produced",
                     {"surface_body_count": n, "bodies_before": p.get("bodies_before"),
                      "bodies_after": p.get("bodies_after")},
                     isinstance(n, int) and not isinstance(n, bool) and n >= 2)


def _stitched(p):
    """model_stitch: 'became_solid' is the isSolid read-back over the result bodies - a BOOLEAN
    means it was read (null means it would not answer). Whether two faces close into a solid is
    the geometry's business, so the value is not asserted."""
    return _measured("stitch result read-back",
                     {"became_solid": p.get("became_solid"), "is_solid": p.get("is_solid"),
                      "result_bodies": p.get("result_bodies"),
                      "input_body_count": p.get("input_body_count")},
                     isinstance(p.get("became_solid"), bool) and bool(p.get("result_bodies")))


def _base_feature_open(p):
    """model_base_feature(start): the scope reports itself open and registered."""
    return _measured("base-feature scope opened",
                     {"editing": p.get("editing"), "open_scope_count": p.get("open_scope_count"),
                      "base_feature": p.get("base_feature")},
                     p.get("editing") is True and bool(p.get("base_feature"))
                     and isinstance(p.get("open_scope_count"), int) and p["open_scope_count"] >= 1)


def _base_feature_closed(p):
    """model_base_feature(finish): the scope closed - 'editing' False (null is an UNCONFIRMED
    close) and at least one scope named in 'closed_scopes'."""
    return _measured("base-feature scope closed",
                     {"editing": p.get("editing"), "closed_scopes": p.get("closed_scopes"),
                      "open_scope_count": p.get("open_scope_count")},
                     p.get("editing") is False and bool(p.get("closed_scopes")))


def _arranged(count):
    """model_arrange: the solver's effect read back - which named inputs MOVED, or the occurrences
    it added (it restructures parts under Envelope occurrences and can mint copies)."""
    def check(p):
        return _measured(f"arrange effect (want {count} shapes)",
                         {"arranged_count": p.get("arranged_count"), "moved": p.get("moved"),
                          "new_occurrence_count": p.get("new_occurrence_count")},
                         p.get("arranged_count") == count
                         and bool(p.get("moved") or p.get("new_occurrences")))
    return check


def _holder_computed(p):
    """model_compute_holder: the profile reduced to a stack of bands - the segment count and the
    bands themselves."""
    segs = p.get("segments_mm")
    return _measured("holder profile segments",
                     {"segment_count": p.get("segment_count"), "name": p.get("name")},
                     isinstance(segs, list) and len(segs) >= 1
                     and p.get("segment_count") == len(segs))


def _piped(p):
    """model_pipe: the bodies the pipe produced, plus the section size read back off the feature
    (feature.sectionSize.value), not the request echoed."""
    return _measured("pipe result",
                     {"result_bodies": p.get("result_bodies"),
                      "section_size_measured": p.get("section_size_measured"),
                      "section_size": p.get("section_size")},
                     bool(p.get("result_bodies"))
                     and isinstance(p.get("section_size_measured"), (int, float))
                     and not isinstance(p.get("section_size_measured"), bool))


# --- joint_* / assembly_* / param_* / doc_* value predicates -----------------------------------
# Same rule as the model_* block above: each reads keys the tool PUBLISHES on the call shape its
# step uses, and each reports what it read through _measured so a miss is diagnosable from the
# ledger line alone.

def _num(v):
    """True for a real number - a bool is an int in Python and is never a measurement."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _near(v, want, tol):
    return _num(v) and abs(v - want) < tol


def _mod360(a, b):
    """The smaller of the two ways round between two angles in degrees - a revolute's stored value
    ACCUMULATES full turns, so 720 and 0 are one pose (the same test joint_drive's own no-take gate
    applies before it will call a drive landed)."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _jointed(name):
    """joint_create: 'healthy' is the created joint's own compute state, read off the joint or its
    timeline item - null when NEITHER answered, which is not a 'yes' - and 'joint_name' is what
    apply_rename read BACK off the joint rather than the name requested."""
    def check(p):
        return _measured(f"joint create read-back ('{name}')",
                         {"created": p.get("created"), "healthy": p.get("healthy"),
                          "joint_name": p.get("joint_name"), "joint_type": p.get("joint_type"),
                          "input_one": p.get("input_one"), "input_two": p.get("input_two")},
                         p.get("created") is True and p.get("healthy") is True
                         and p.get("joint_name") == name)
    return check


def _mesh_round_trip(width_mm, height_mm, tol=0.5):
    """model_inspect on a re-imported mesh: it comes back the SIZE of the mesh that was written.

    mesh_export defaults stl_units to mm and mesh_insert defaults units to mm, so a file written and
    re-imported with neither naming a unit agrees end to end. The two defaults are set in separate
    tools, and a mismatch is silent: read at the wrong unit, every coordinate is divided (or
    multiplied) by 25.4 and the mesh arrives a fortieth of its size and a fortieth of its distance
    from the origin, which puts it inside whatever is parked there. Only a size check catches that;
    the import returns ok either way.

    A SIZE, not a position: the layout pass moves the bench, and 25.4 is the only thing being looked
    for. 'is_oriented' and 'volume' ride in the evidence unasserted - the round trip comes back
    un-oriented with zero signed volume, and pinning a value here would pin that rather than
    describe it."""
    def check(p):
        b = p.get("bbox") or {}
        return _measured(f"re-imported mesh measures {width_mm} x {height_mm} mm",
                         {"x": b.get("x"), "y": b.get("y"), "z": b.get("z"),
                          "is_closed": p.get("is_closed"), "is_oriented": p.get("is_oriented"),
                          "volume": p.get("volume")},
                         _near(b.get("x"), width_mm, tol) and _near(b.get("y"), width_mm, tol)
                         and _near(b.get("z"), height_mm, tol))
    return check


def _joint_origin_landed(name, z=10.0):
    """joint_create_origin for a bench station. The X/Y it was asked for travel with the layout, and
    a predicate is not shifted with the step it belongs to - so this asserts the axis the layout
    never touches (Z, the height up the base) plus the name and units read back off the created
    origin. Where it sits ALONG the base is proven by the arms landing apart, not by a literal."""
    def check(p):
        loc = p.get("offset_parameters") or {}
        return _measured(f"joint origin '{name}' landed at z={z} mm",
                         {"created": p.get("created"),
                          "joint_origin_name": p.get("joint_origin_name"),
                          "offset_parameters": loc},
                         p.get("created") is True and p.get("joint_origin_name") == name
                         and loc.get("units") == "mm" and _near(loc.get("z"), z, 1e-4))
    return check


def _joint_origin_at(name, x=0.0, y=0.0, z=0.0):
    """joint_create_origin(anchor='coordinates'): 'offset_parameters' holds the created JO's own
    offsetX/Y/Z VALUES read back off it (the tool deletes the origin and errors when they differ
    from what was asked), so this reads where the frame LANDED, not where it was aimed."""
    def check(p):
        loc = p.get("offset_parameters") or {}
        vals = [loc.get(k) for k in ("x", "y", "z")]
        return _measured(f"joint origin '{name}' offsets (want {[x, y, z]} mm)",
                         {"created": p.get("created"),
                          "joint_origin_name": p.get("joint_origin_name"),
                          "offset_parameters": loc,
                          "joint_origin_count": p.get("joint_origin_count")},
                         p.get("created") is True and p.get("joint_origin_name") == name
                         and loc.get("units") == "mm"
                         and all(_near(v, w, 1e-4) for v, w in zip(vals, (x, y, z))))
    return check


def _joint_origin_computed(name):
    """joint_create_origin(a COMPUTED anchor): 'origin_readback' is the created JO's own
    geometry.origin read AFTER the fact, and 'computed_anchor' the point the tool derived from the
    target's measured box - equal within the tool's own 0.01 mm band is the frame landing on the
    anchor it computed. Both keys absent would mean that verification never ran."""
    def check(p):
        want, landed = p.get("computed_anchor") or {}, p.get("origin_readback") or {}
        pairs = [(want.get(k), landed.get(k)) for k in ("x", "y", "z")]
        return _measured(f"joint origin '{name}' landed on its computed anchor",
                         {"computed_anchor": want, "origin_readback": landed,
                          "frame_axes": p.get("frame_axes")},
                         p.get("created") is True and p.get("joint_origin_name") == name
                         and want.get("units") == landed.get("units")
                         and all(_num(a) and _near(b, a, 0.01) for a, b in pairs))
    return check


def _joint_limits(name, **want):
    """joint_edit(limits): every published limit is the value READ BACK off the live JointLimits in
    the caller's own units - a limit whose read-back could not be taken publishes null and is named
    in 'limits_unverified', so an empty 'limits_unverified' beside real numbers is the write
    confirmed."""
    def check(p):
        good = (p.get("edited") is True and p.get("joint_name") == name
                and p.get("recomputed") is True and not p.get("limits_unverified")
                and all(_near(p.get(k), v, 1e-3) for k, v in want.items()))
        return _measured(f"joint '{name}' limits read back {sorted(want)}",
                         {"edited": p.get("edited"), "joint_name": p.get("joint_name"),
                          "changes": p.get("changes"), "recomputed": p.get("recomputed"),
                          "limits_unverified": p.get("limits_unverified")}, good)
    return check


def _motion_linked(one, two, reversed_):
    """joint_motion_link: 'motion_link' is the name read off the CREATED link, 'ratio_applied' says
    setMotionData answered true (a ratio that would not apply rolls the link back and errors), and
    joint_one/joint_two are read off the two joints the names resolved to."""
    def check(p):
        return _measured(f"motion link '{one}' -> '{two}'",
                         {"linked": p.get("linked"), "motion_link": p.get("motion_link"),
                          "joint_one": p.get("joint_one"), "joint_two": p.get("joint_two"),
                          "ratio": p.get("ratio"), "reversed": p.get("reversed")},
                         p.get("linked") is True and p.get("ratio_applied") is True
                         and bool(p.get("motion_link"))
                         and p.get("joint_one") == one and p.get("joint_two") == two
                         and p.get("reversed") is reversed_)
    return check


def _driven_angle(want_deg):
    """joint_drive: 'value_now.angle_deg' is jointMotion.rotationValue read BACK off the joint after
    the drive, compared the way the tool's own no-take gate compares it (modulo 360). 'moved' names
    the member whose placement changed and is reported, not asserted - which member moves is the
    mechanism's business."""
    def check(p):
        v = (p.get("value_now") or {}).get("angle_deg")
        return _measured(f"drive read-back (want {want_deg} deg)",
                         {"driven": p.get("driven"), "joint": p.get("joint"),
                          "value_now": p.get("value_now"), "moved": p.get("moved"),
                          "motion_link_partner": p.get("motion_link_partner")},
                         p.get("driven") is True and _num(v) and _mod360(v, want_deg) < 0.5)
    return check


def _driven_slide(want_mm):
    """joint_drive (a slider): 'value_now.distance_mm' is jointMotion.slideValue read back off the
    joint in mm - the tool errors when it disagrees with the command, so the number here IS the
    pose the slide took."""
    def check(p):
        v = (p.get("value_now") or {}).get("distance_mm")
        return _measured(f"slide read-back (want {want_mm} mm)",
                         {"driven": p.get("driven"), "joint": p.get("joint"),
                          "value_now": p.get("value_now"), "moved": p.get("moved")},
                         p.get("driven") is True and _near(v, want_mm, 1e-3))
    return check


def _as_built(p):
    """joint_create_as_built: 'joint' is the name read off the CREATED AsBuiltJoint (a rename that
    did not land is refused rather than published), and the two occurrence paths are read off the
    occurrences the inputs resolved to."""
    return _measured("as-built joint read-back",
                     {"created": p.get("created"), "joint": p.get("joint"),
                      "occurrence_one": p.get("occurrence_one"),
                      "occurrence_two": p.get("occurrence_two"),
                      "joint_type": p.get("joint_type"),
                      "anchor_warning": p.get("anchor_warning")},
                     p.get("created") is True and bool(p.get("joint"))
                     and bool(p.get("occurrence_one")) and bool(p.get("occurrence_two")))


def _jointed_at_geometry(p):
    """joint_at_geometry: 'healthy' is the created joint's compute state - the payload's own
    authoritative flag, false when the joint was added but could not solve - beside the key points
    the two handles resolved to."""
    return _measured("joint-at-geometry read-back",
                     {"jointed": p.get("jointed"), "healthy": p.get("healthy"),
                      "health_state": p.get("health_state"), "joint_name": p.get("joint_name"),
                      "geometry_one": p.get("geometry_one"),
                      "geometry_two": p.get("geometry_two")},
                     p.get("jointed") is True and p.get("healthy") is True
                     and bool(p.get("geometry_one")) and bool(p.get("geometry_two")))


def _grounded(p):
    """assembly_ground(ground_to_parent=true): 'isGroundToParent' is the flag READ BACK off the
    occurrence after the set - the tool errors on a flag that will not read or does not match, so a
    true here is the lock in force. 'isGrounded' is the separate UI flag this tool never writes
    (read_flag: null means unread)."""
    return _measured("parent lock read-back",
                     {"occurrence": p.get("occurrence"),
                      "isGroundToParent": p.get("isGroundToParent"),
                      "isGrounded": p.get("isGrounded"),
                      "position_reset": p.get("position_reset")},
                     p.get("isGroundToParent") is True and bool(p.get("occurrence")))


def _moved_occurrence(x_mm=None):
    """assembly_move: 'position' is the occurrence's transform2 translation READ BACK after the
    compose, in the caller's units - the tool errors when the transform reads unchanged or will not
    read at all, so these are the coordinates the part actually holds. x is asserted only where the
    step is the FIRST move of a fresh occurrence, whose transform starts at the identity."""
    def check(p):
        pos = p.get("position") or {}
        vals = [pos.get(k) for k in ("x", "y", "z")]
        good = (p.get("moved") is True and bool(p.get("occurrence"))
                and all(_num(v) for v in vals))
        if good and x_mm is not None:
            good = _near(pos.get("x"), x_mm, 1e-3)
        return _measured("moved occurrence position"
                         + ("" if x_mm is None else f" (want x {x_mm})"),
                         {"moved": p.get("moved"), "occurrence": p.get("occurrence"),
                          "position": pos, "units": p.get("units")}, good)
    return check


def _rigid_grouped(count):
    """assembly_rigid_group: 'member_count' is rigidGroup.occurrences.count read off the CREATED
    group - the tool errors when it comes back short of the occurrences it resolved."""
    def check(p):
        return _measured(f"rigid group members (want {count})",
                         {"assembly_rigid_group": p.get("assembly_rigid_group"),
                          "member_count": p.get("member_count"), "grouped": p.get("grouped")},
                         p.get("member_count") == count
                         and bool(p.get("assembly_rigid_group"))
                         and len(p.get("grouped") or []) == count)
    return check


def _constrained(p):
    """assembly_constrain: 'relationship_count' is the count read off the CREATED constraint (null
    when it would not read), 'constraint' its name read back, and 'moved' is the world-transform
    diff sampled either side of the add - a LIST (empty is a real answer: nothing moved) when the
    parts were sampled on both sides, null when whether anything moved is unknown."""
    n = p.get("relationship_count")
    return _measured("assembly constraint read-back",
                     {"created": p.get("created"), "constraint": p.get("constraint"),
                      "relationship_count": n,
                      "relationships_submitted": p.get("relationships_submitted"),
                      "moved": p.get("moved"), "occurrences": p.get("occurrences")},
                     p.get("created") is True and bool(p.get("constraint"))
                     and _num(n) and n >= 1 and isinstance(p.get("moved"), list))


def _captured(p):
    """assembly_capture_position(capture): 'snapshot_count' is the collection's own re-read (the
    tool errors when it did not advance), 'snapshot' the marker's name, and 'pose_held' is True only
    where a transform was sampled on BOTH sides of the add - a capture that REVERTED the pending
    move instead of recording it is an error, and an unsampled one publishes null."""
    n = p.get("snapshot_count")
    return _measured("captured position read-back",
                     {"captured": p.get("captured"), "snapshot": p.get("snapshot"),
                      "snapshot_count": n, "pose_held": p.get("pose_held")},
                     p.get("captured") is True and bool(p.get("snapshot"))
                     and _num(n) and n >= 1 and p.get("pose_held") is True)


def _joint_is(name, kind):
    """assembly_get: one joint's motion type, read off the DESIGN's own joint walk. joint_edit
    publishes the type it was ASKED for, so a retype that silently did not land reads back correct
    from the writer and wrong from here - which is the whole point of asking someone else."""
    def check(p):
        row = next((j for j in (p.get("joints") or []) if j.get("name") == name), None)
        return _measured(f"{name} reads back as {kind}",
                         {"joint": row and {"name": row.get("name"), "type": row.get("type")}},
                         bool(row) and row.get("type") == kind)
    return check


def _joints_listed(minimum, poses=None):
    """assembly_get (default read): 'joint_count' is the design-wide joint walk's own total, and a
    named joint's 'value_now.angle_deg' is its jointMotion's current driven value read straight off
    the joint - an INDEPENDENT witness to a pose joint_drive reported for itself."""
    poses = poses or {}

    def check(p):
        rows = {j.get("name"): j for j in (p.get("joints") or [])}
        angles = {n: (rows.get(n, {}).get("value_now") or {}).get("angle_deg") for n in poses}
        good = (_num(p.get("joint_count")) and p.get("joint_count") >= minimum
                and isinstance(p.get("is_healthy"), bool)
                and all(_num(angles[n]) and _mod360(angles[n], w) < 0.5
                        for n, w in poses.items()))
        return _measured(f"assembly joints (want >= {minimum}, poses {poses})",
                         {"joint_count": p.get("joint_count"), "is_healthy": p.get("is_healthy"),
                          "value_now": angles, "broken_joints": p.get("broken_joints")}, good)
    return check


def _joint_origins_listed(*names):
    """assembly_get(include=['joint_origins']): an INDEPENDENT read of the frames
    joint_create_origin built - each named JO present in the design-wide JO walk, carrying the
    handle that round-trips back into a joint or a CAM WCS input."""
    def check(p):
        rows = {r.get("name"): r for r in (p.get("joint_origins") or [])}
        missing = [n for n in names if n not in rows]
        return _measured("joint origins listed " + str(list(names)),
                         {"joint_origin_count": p.get("joint_origin_count"),
                          "names": sorted(n for n in rows if n)[:12], "missing": missing},
                         _num(p.get("joint_origin_count"))
                         and p.get("joint_origin_count") >= len(names) and not missing
                         and all(rows[n].get("handle") for n in names))
    return check


def _interference_measured(p):
    """assembly_inspect_interference: the census the check actually ran - how many occurrences and
    root bodies it compared, and the rows it found, with the count agreeing with the list. The
    VERDICT is not asserted: whether parts overlap is the model's business."""
    m = p.get("measured") or {}
    items = m.get("interferences")
    return _measured("interference census",
                     {"relation": p.get("relation"), "passed": p.get("passed"),
                      "interference_count": m.get("interference_count"),
                      "occurrences_checked": m.get("occurrences_checked"),
                      "root_bodies_checked": m.get("root_bodies_checked")},
                     p.get("relation") == "interference_free"
                     and isinstance(p.get("passed"), bool) and isinstance(items, list)
                     and m.get("interference_count") == len(items)
                     and _num(m.get("occurrences_checked")) and m.get("occurrences_checked") >= 1)


# A parameter's value read back through unitsManager.convert lands on the nominal to well within
# this; the band is far too narrow to admit a different unit or a different expression's result.
_PARAM_TOL = 1e-4


def _param_added(name, value, units="mm"):
    """param_add: 'parameter' is the created Parameter re-read - the name it answers to and the
    value Fusion EVALUATED its expression to, in that parameter's own unit ('value_units'), never
    the request echoed. A derived expression's value is therefore the design's own arithmetic."""
    def check(p):
        par = p.get("parameter") or {}
        return _measured(f"param add read-back '{name}' (want {value} '{units}')",
                         {"added": p.get("added"), "name": par.get("name"),
                          "expression": par.get("expression"), "value": par.get("value"),
                          "value_units": par.get("value_units"),
                          "timeline_warnings": p.get("timeline_warnings")},
                         p.get("added") is True and par.get("name") == name
                         and par.get("value_units") == units
                         and _near(par.get("value"), value, _PARAM_TOL))
    return check


def _param_set_to(name, value):
    """param_set: 'before'/'after' are the SAME parameter read either side of the assignment (the
    tool errors when the two are identical while the expression asked for a change), so the moved
    value is the edit landing rather than the expression being accepted."""
    def check(p):
        before, after = p.get("before") or {}, p.get("after") or {}
        return _measured(f"param set read-back '{name}' (want {value})",
                         {"set": p.get("set"), "created": p.get("created"), "name": p.get("name"),
                          "before": {"expression": before.get("expression"),
                                     "value": before.get("value")},
                          "after": {"expression": after.get("expression"),
                                    "value": after.get("value")}},
                         p.get("set") is True and p.get("created") is False
                         and p.get("name") == name
                         and _near(after.get("value"), value, _PARAM_TOL)
                         and before.get("value") != after.get("value"))
    return check


def _param_deleted(name):
    """param_delete: 'deleted' is what Parameter.deleteMe() RETURNED - the tool errors on a false
    and on a delete that introduced a timeline error, so a true here is the parameter gone with the
    timeline still walking clean."""
    def check(p):
        return _measured(f"param delete read-back '{name}'",
                         {"deleted": p.get("deleted"), "name": p.get("name"),
                          "note": (p.get("note") or "")[:60]},
                         p.get("deleted") is True and p.get("name") == name)
    return check


def _params_listed(*names):
    """param_get (collection): the user parameters the design answered with - the count is len()
    over the rows actually read, so the two cannot disagree, and every named parameter is present
    carrying the number Fusion evaluated for it."""
    def check(p):
        rows = p.get("user_parameters") or []
        by_name = {r.get("name"): r for r in rows}
        missing = [n for n in names if n not in by_name]
        return _measured(f"user parameters listed (want {list(names)})",
                         {"user_parameter_count": p.get("user_parameter_count"),
                          "rows_read": len(rows), "missing": missing,
                          "names": sorted(n for n in by_name if n)[:12]},
                         p.get("user_parameter_count") == len(rows) and not missing
                         and all(_num(by_name[n].get("value")) for n in names))
    return check


def _param_read(name, value, units="mm"):
    """param_get (one name): the parameter's evaluated value in its own unit, read off the design
    long after the add - the same number the adding step measured, now on the read side."""
    def check(p):
        par = p.get("parameter") or {}
        return _measured(f"param read '{name}' (want {value} '{units}')",
                         {"name": par.get("name"), "expression": par.get("expression"),
                          "value": par.get("value"), "value_units": par.get("value_units")},
                         par.get("name") == name and par.get("value_units") == units
                         and _near(par.get("value"), value, _PARAM_TOL))
    return check


def _param_favorited(name, favorite=True):
    """param_set_favorite: 'favorite' is Parameter.isFavorite READ BACK after the set (null when the
    read declined), so it is the flag the parameter holds, not the flag requested."""
    def check(p):
        return _measured(f"favorite read-back '{name}' (want {favorite})",
                         {"name": p.get("name"), "favorite": p.get("favorite")},
                         p.get("name") == name and p.get("favorite") is favorite)
    return check


def _new_document(p):
    """doc_new: 'is_active' compares app.activeDocument with the new document BY HANDLE after the
    add, so it says the new document really took the foreground - not that documents.add returned
    something."""
    return _measured("new document read-back",
                     {"created": p.get("created"), "document_name": p.get("document_name"),
                      "is_active": p.get("is_active"), "is_saved": p.get("is_saved")},
                     p.get("created") is True and p.get("is_active") is True
                     and bool(p.get("document_name")))


def _document_read(p):
    """doc_get (default read): the ACTIVE document's own record, and the session list it has to
    appear in. This sweep's document is created by doc_new and never saved, so 'has_data_file'
    false is that state read back off the document."""
    active = p.get("active") or {}
    names = [r.get("name") for r in (p.get("open_documents") or [])]
    return _measured("active document in the session list",
                     {"name": active.get("name"), "has_data_file": active.get("has_data_file"),
                      "is_modified": active.get("is_modified"), "open_count": p.get("open_count"),
                      "open_documents": names[:6]},
                     bool(active.get("name")) and active.get("has_data_file") is False
                     and _num(p.get("open_count")) and p.get("open_count") >= 1
                     and active.get("name") in names)


def _document_closed(p):
    """doc_close: 'closed' names the document Document.close() answered true for, 'acted_on' is the
    identity read BEFORE the close (afterwards neither name nor URN reads back), and 'errors' holds
    the targets that refused."""
    return _measured("document closed",
                     {"closed": p.get("closed"), "closed_count": p.get("closed_count"),
                      "errors": p.get("errors"), "skipped_invalid": p.get("skipped_invalid"),
                      "remaining_open": p.get("remaining_open"), "acted_on": p.get("acted_on")},
                     p.get("closed_count") == 1 and bool(p.get("closed"))
                     and not p.get("errors") and p.get("save_changes") is False
                     and bool((p.get("acted_on") or {}).get("name")))


def _imported(p):
    """doc_insert_import (a solid format into a component): 'objects_created' counts what
    importToTarget2 RETURNED, and bodies_added/occurrences_added are the target component's own
    census differenced across the import."""
    n = p.get("objects_created")
    return _measured("import result",
                     {"objects_created": n, "bodies_added": p.get("bodies_added"),
                      "occurrences_added": p.get("occurrences_added"), "into": p.get("into"),
                      "created": (p.get("created") or [])[:2]},
                     p.get("imported") is True and _num(n) and n >= 1
                     and bool(p.get("created")))


def _imported_sketches(p):
    """doc_insert_import, DXF branch: one sketch per DXF layer carrying 2D geometry. The receiving
    component's own sketch census, differenced across the import, is the receipt - importToTarget2
    returns nothing for a DXF."""
    n = p.get("sketches_added")
    return _measured("DXF sketches landed",
                     {"sketches_added": n, "plane": p.get("plane"),
                      "created": (p.get("created") or [])[:2]},
                     p.get("imported") is True and _num(n) and n >= 1)


def _imported_curves(p):
    """doc_insert_import, SVG branch: the curves land in an EXISTING sketch, so the receipt is that
    sketch's own curve count differenced across the import."""
    n = p.get("curves_added")
    return _measured("SVG curves landed",
                     {"curves_added": n, "objects_created": p.get("objects_created"),
                      "into": p.get("into")},
                     p.get("imported") is True and _num(n) and n >= 1)


def _exported_bytes(p):
    """design_export: the file LANDED with content. The API's own success bool is not enough - a
    build missing a format's factory, or an export that writes an empty file, reports success and
    leaves nothing to open, so the size on disk is what the row stands on."""
    n = p.get("size_bytes")
    return _measured("exported file on disk",
                     {"format": p.get("format"), "file_path": p.get("file_path"), "size_bytes": n},
                     p.get("exported") is True and _num(n) and n > 0)


def _box(name, ox=0, oy=0, tint="", shape="box"):
    """Steps building a fresh free component 'name' holding one small solid (offset ox/oy in the
    world grid - every cameo gets its own slot so nothing builds on top of the gyroscope or another
    cameo). The reusable free occurrence the joint/assembly steps mate.

    'shape' and 'tint' make the two halves of a jointed PAIR tell apart. Two identical grey 20 mm
    cubes mated together show nothing: which one moved, and which way, is exactly what a viewer is
    trying to read off the joint."""
    if shape == "disc":
        draw = ("sketch_add_geometry", {"kind": "circle", "cx": ox + 10, "cy": oy + 10, "radius": 10,
                                        "sketch_name": name + "S"}, "ok", None)
        height = 16
    elif shape == "bar":
        draw = ("sketch_add_geometry", {"kind": "rectangle", "x1": ox, "y1": oy + 5,
                                        "x2": ox + 34, "y2": oy + 15,
                                        "sketch_name": name + "S"}, "ok", None)
        height = 8
    else:
        draw = ("sketch_add_geometry", {"kind": "rectangle", "x1": ox, "y1": oy,
                                        "x2": ox + 20, "y2": oy + 20,
                                        "sketch_name": name + "S"}, "ok", None)
        height = 10
    return [
        ("model_create_component", {"name": name, "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xy", "name": name + "S"}, "ok", None),
        draw,
        ("model_extrude", {"sketch_name": name + "S", "profile_index": 0, "distance": height},
         _extruded, None),
    ] + ([("appearance_set", {"target": name, "color": tint}, "ok", None)] if tint else [])


# The joint bench's stations: (tag, motion, rotation axis, slide axis, drive kwarg, drive value).
# A drive of None is a motion with no single driven parameter - rigid has no freedom at all, and
# ball/planar carry several at once, so joint_drive has nothing to set and the station stands as a
# created joint. Axes are FRAME-relative; every part here is built in world coordinates on a
# world-aligned frame, so they read as world axes too.
# (tag, motion, rotation axis, slide axis, [(drive kwarg, value), ...], pose, tint).
# EVERY motion with a degree of freedom moves. The two ways of using one are different tools, and
# which one applies is a property of the motion, not a preference:
#   drives - joint_drive sets a joint's own driven VALUE, and takes revolute / slider / cylindrical
#            only (measured; it refuses the rest by name). Cylindrical carries BOTH freedoms, so it
#            is driven twice - rotation then slide - and shows them one at a time.
#   pose   - the multi-freedom motions have no single value to set, so they are POSED with
#            assembly_move within the freedom their joint allows: pin_slot slides and turns, ball
#            turns about three axes, planar slides in its plane and spins in it. joint_drive's own
#            refusal points at assembly_move for exactly this.
# Rigid is the one station that must NOT move; it is the control the others are read against.
_JOINT_STATIONS = (
    ("Rev", "revolute",    "z", "",  [("angle_deg", 90.0)],                  None,
     "#E5533C"),
    ("Sld", "slider",      "x", "",  [("distance", 22.0)],                   None,
     "#1E88E5"),
    ("Cyl", "cylindrical", "z", "",  [("angle_deg", 60.0), ("distance", 14.0)], None,
     "#43A047"),
    ("Pin", "pin_slot",    "z", "x", [], {"dx": 18.0, "rotate_z": 35.0},     "#FB8C00"),
    ("Bal", "ball",        "z", "",  [], {"rotate_x": 25.0, "rotate_z": 40.0}, "#8E24AA"),
    ("Pla", "planar",      "z", "",  [], {"dx": 15.0, "dy": 12.0, "rotate_z": 20.0}, "#00ACC1"),
    ("Rig", "rigid",       "z", "",  [], None,                               "#9E9E9E"),
)
# Where each station sits along the base, and where its arm is drawn before the joint carries it
# there. Both are authored; the layout pass moves the whole bench as one welded group.
_STN_X0, _STN_PITCH, _STN_Y, _STN_Z = 890.0, 45.0, 320.0, 10.0
# How long one motion is held before the next starts. Ten of these is the price of the seven motions
# reading as seven rather than as one blur; it is the largest dwell budget in the sweep, and the
# sweep runs against a 600 s ceiling, so it stays under a second.
_JOINT_BEAT = 0.8


def _pose_took(tag):
    """model_inspect on a POSED arm: its world footprint is no longer the 34 x 10 mm bar it was drawn
    as. Every pose on this bench TURNS the arm as well as sliding it, so an axis-aligned box still
    measuring 34 x 10 says the pose did not take. assembly_move's own report cannot say that - it
    describes the move it asked for - and a transform that ticked is not a part that moved.

    The two extents are judged TOGETHER, because a turn does not change them evenly: measured, a
    35 deg pose takes a 34 x 10 bar to 33.59 x 27.69, so the long axis barely moves while the short
    one nearly triples. Either extent alone would call that pose a no-op."""
    def check(p):
        return _measured(f"Ind{tag} is off its 34 x 10 mm rest footprint",
                         {"x": p.get("x"), "y": p.get("y"), "z": p.get("z")},
                         _num(p.get("x")) and _num(p.get("y"))
                         and abs(p["x"] - 34.0) + abs(p["y"] - 10.0) > 5.0)
    return check


def _joint_bench():
    """A station per motion type: a Joint Origin ON the base at that station, an indicator arm drawn
    clear of it, and the joint that carries the arm to the station. Then the drive pass, so the
    motions that HAVE a degree of freedom are seen using it.

    The stations are Joint Origins rather than a snap onto the base's top face because a face snap
    resolves to that face's centre - every arm would mate at the same point and the seven motions
    would end up in one pile, which is the opposite of a demonstration."""
    rows = []
    # EVERY ARM FIRST, in a row clear of the base, and only then the frame that holds the whole
    # bench. The ASSEMBLY is the thing worth watching here - seven arms hopping onto seven stations -
    # and it cannot be watched while the camera is still fitted to a bare base, or while it cuts to
    # each arm as that arm is drawn. Building the parts before the joints is what makes one frame
    # cover the whole of it.
    for i, (tag, _m, _ax, _sl, _dk, _dv, tint) in enumerate(_JOINT_STATIONS):
        rows += _box("Ind" + tag, ox=_STN_X0 + i * _STN_PITCH, oy=_STN_Y + 60.0,
                     tint=tint, shape="bar")
    rows.append(("design_activate_component", {"occurrence": "root"}, "ok", None))
    rows.append(_watch(["JointBase:1"] + ["Ind" + st[0] + ":1" for st in _JOINT_STATIONS]))
    for i, (tag, motion, axis, slide, _dk, _dv, _t) in enumerate(_JOINT_STATIONS):
        x = _STN_X0 + i * _STN_PITCH
        # FLIPPED, and the reason is measured: an arm's bottom face points -Z while the station's
        # frame points +Z, so a flush mate opposes the two normals and rotates the arm 180 degrees -
        # it hangs DOWN from the station and ends up inside the base rather than standing on it
        # (measured: an 8 mm arm landing at z 2..10 inside a base spanning z 0..10, invisible).
        # Flipping seats the arm proud of the base, which is the only place a driven motion reads -
        # measured on a finished run, all seven land at z 10..18 on a base spanning z 0..10.
        joint = {"occurrence_one": "Ind" + tag + ":1:bottom", "occurrence_two": "Stn" + tag,
                 "joint_type": motion, "axis": axis, "flip": True, "name": "J" + tag}
        if slide:
            joint["slide_axis"] = slide
        rows += [
            ("design_activate_component", {"occurrence": "JointBase:1"}, "ok", None),
            ("joint_create_origin", {"anchor": "coordinates", "x": x, "y": _STN_Y, "z": _STN_Z,
                                     "name": "Stn" + tag},
             _joint_origin_landed("Stn" + tag), None),
            ("design_activate_component", {"occurrence": "root"}, "ok", None),
            ("joint_create", joint, _jointed("J" + tag), None),
        ]
    # THE MOTION PASS. Everything that CAN move is moved before anything is put back, so one frame
    # of the bench shows six arms displaced at once against the rigid station that cannot be - which
    # is the only way a motion type reads as a motion rather than a label.
    # Each motion gets its OWN beat. Back to back they read as one blur - a viewer cannot tell which
    # arm moved for which joint, which is the whole point of a bench with one station per type. The
    # beat is a dwell rather than a camera row: the bench is already framed as a whole, and moving
    # the camera per station would cost seven zooms to show seven small displacements.
    for tag, _m, _ax, _sl, drives, _pose, _t in _JOINT_STATIONS:
        for kwarg, value in drives:
            check = _driven_angle(value) if kwarg == "angle_deg" else _driven_slide(value)
            rows.append(("joint_drive", {"joint_name": "J" + tag, kwarg: value}, check, None))
            rows.append(_dwell(_JOINT_BEAT))
    # the multi-freedom motions, POSED - each within what its own joint allows. assembly_move on a
    # jointed occurrence is a TRANSIENT pose and says so in 'jointed_warning'; it is discarded below
    # rather than captured, because a pending uncaptured move makes the next joint create refuse.
    for tag, _m, _ax, _sl, _d, pose, _t in _JOINT_STATIONS:
        if not pose:
            continue
        rows.append(("assembly_move", dict(occurrence="Ind" + tag + ":1", **pose),
                     _moved_occurrence(), None))
        rows.append(("model_inspect", {"target": "Ind" + tag + ":1"}, _pose_took(tag), None))
        rows.append(_dwell(_JOINT_BEAT))
    # THE DISPLACED FRAME, captured before anything is put back: six arms off their rest pose against
    # the one rigid station that cannot leave its. 'current' shoots what the camera already frames -
    # a named view would refit to the whole model and lose the bench.
    rows.append(_dwell(2.5))
    rows.append(("view_screenshot", {"view": "current", "width": 640, "height": 460}, "ok", None))
    # the motions joint_drive will NOT take, each refused by name rather than answered with a value
    # it does not have: rigid has no freedom at all, ball carries three rotations at once, and
    # pin_slot is outside the drivable set even though it has freedom in two. The refusal is what
    # sends a caller to assembly_move, which is what the poses above use.
    for tag in ("Rig", "Bal", "Pin"):
        rows.append(("joint_drive", {"joint_name": "J" + tag, "angle_deg": 30.0}, "refused", None))
    # HOME AGAIN: the transient poses reverted in one call, then every driven value back to zero, so
    # the bench is left as it was built and the next act starts from a known pose.
    rows.append(("assembly_capture_position", {"action": "discard_pending"},
                 lambda p: p.get("discarded") is True and p.get("has_pending") is False, None))
    for tag, _m, _ax, _sl, drives, _pose, _t in _JOINT_STATIONS:
        for kwarg, _value in drives:
            check = _driven_angle(0.0) if kwarg == "angle_deg" else _driven_slide(0.0)
            rows.append(("joint_drive", {"joint_name": "J" + tag, kwarg: 0.0}, check, None))
    return rows


def _group_of(name):
    """The tool-group a cameo or scratch sketch belongs to - its name with any trailing index,
    suffix letter or role word stripped. SlotA..SlotH are one group, EditTrim/EditSplit/EditCorner
    another, PipeRun/PipeHalf/PipeCut another. Grouping is what lets the camera move once per family
    of related operations instead of once per entity, and frame the family together."""
    stem = re.sub(r"(:\d+)$", "", name)
    stem = re.sub(r"(Sketch|Path|Prof|Block|Post|Cameo|Comp|Part|Run|S)$", "", stem) or stem
    stem = re.sub(r"[A-Z]?\d*$", "", stem) or stem
    return stem or name


def _watch(occurrence):
    """A camera row: orient and FRAME the occurrence, so a viewer watching the run sees the chunk
    about to be exercised fill the viewport instead of sitting as a speck in a corner. The story
    world is metres wide (every cameo gets its own grid slot) while the parts are tens of
    millimetres, so an unframed orient shows nothing legible - one of these opens each chunk that
    works on its own geometry, not each step.

    Sketch-only subjects are shot from the TOP, not iso: a flat XY sketch seen from iso-top-right is
    foreshortened to near nothing, and text on it cannot be read at all. Anything with a body in it
    keeps iso, where a solid reads as a solid."""
    names = occurrence if isinstance(occurrence, list) else [occurrence]
    flat = not any(str(n).endswith(":1") for n in names)
    view = "iso-top-right"
    if flat:
        planes = {_SKETCH_PLANE.get(str(n)) for n in names}
        planes.discard(None)
        # one shared plane: look straight down its normal. Mixed planes have no such view, so iso
        # at least shows all of them at an angle rather than one of them edge-on.
        view = _PLANE_VIEW.get(planes.pop(), "top") if len(planes) == 1 else "iso-top-right"
    return ("view_set", {"action": "orient", "orientation": view, "focus": occurrence}, "ok", None)


def _dwell(seconds):
    """Hold the current view for a beat. The ONLY sleep in the sweep and a deliberate one: a
    showcase step exists to be watched, and a toolpath that flicks on and off inside one frame
    shows nothing. It is not a wait for an async result - those are bounded polls - so it never
    guards a correctness step, and _DWELL is filtered out of STEPS so it cannot pass as a tool."""
    return (_DWELL, {"seconds": seconds}, "ok", None)


def _leaves_no_row(step):
    """True for a step run_steps judges nothing for and appends NO row - today only a _dwell.

    The ONE definition of that class, because two sites depend on it agreeing: run_steps skips on
    it, and judged_steps drops it to keep the by-position pairing honest. A second row-less step
    kind taught to one site alone would silently shift that pairing again."""
    return step[0] == _DWELL


def _watch_all():
    """A camera row with NO focus: fit the whole model. Correct only while the world IS the subject
    - the parametric skeleton, before the first cameo lands in its own grid slot - and wrong once
    the grid spreads the world over a metre, which is what _watch(occurrence) is for."""
    return ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None)


# The steps that first put a BODY in a freshly created component - the moment there is something to
# frame. model_construction is deliberately NOT one: a datum plane is not geometry a viewer can see,
# and the body it is drawn for arrives a few steps later.
# The steps that first put geometry ON a sketch - the moment it becomes something to look at.
_SKETCH_MAKERS = ("sketch_add_geometry", "sketch_add_3d_line", "sketch_set_text",
                  "sketch_insert_svg", "sketch_project")

_BODY_MAKERS = ("model_extrude", "model_revolve", "model_loft", "model_sweep", "model_pipe",
                "model_base_feature", "surface_extrude", "surface_revolve", "surface_patch",
                "mesh_insert")

# view_set frames a subject at this multiple of its own size. Modelled here so the sweep can tell
# what the standing frame already shows and leave the camera alone when the answer is "this".
_FRAME_MARGIN = 5.0
# How much bigger than its subject a frame reaches for context, and the floor under that for a
# subject with no measurable size. Proportional, not absolute: a constant neighbourhood frames a
# small sketch at a few percent of the viewport and a large assembly too tight.
_FRAME_CONTEXT = 3.5
_FRAME_MIN_SPAN = 260.0
# The most a single neighbour may stretch that neighbourhood. Without a ceiling one distant part
# drags the frame out to the whole field, where every named subject reads as a speck.
_FRAME_STRETCH = 1.4
# How many recent neighbours to lend a subject whose own position is unknown, purely for scale.
_FRAME_FALLBACK_NEIGHBOURS = 3

# The most subjects one frame may name. The boxes this pass reasons with are built from AUTHORED
# SKETCH COORDINATES, not from geometry: a ring whose sketch is a point at the origin is 160 mm of
# real body, and a revolve or a pattern reaches further still. So every span computed here is a
# lower bound, and a long focus list quietly frames far more than the arithmetic predicts - graded
# by eye, every single-subject frame read well and the 5-to-8 subject frames were whole-field
# photographs. Keeping the list short is the guard that does not depend on the model being right.
_FRAME_MAX_SUBJECTS = 6
# Sketches are watched a HANDFUL at a time rather than one by one: a sketch command is quick, and a
# camera move per command is more motion than the work is worth. The span cap is what keeps that
# honest - five neighbouring sketches in one row share a frame, five scattered ones do not.
_FRAME_SKETCH_GROUP = 10
_FRAME_SKETCH_SPAN = 560.0
# How much wider a JOINT frame reaches than a build frame - a mate is watched, not inspected.
_FRAME_RELATION_WIDEN = 2.2

# The sketch planes that exist from the start. A sketch on one of these can be drawn at any time; a
# sketch on a named datum or a face cannot exist before the body that datum is derived from.
_ORIGIN_PLANES = ("xy", "xz", "yz")

# Sketch tools whose result depends on what the sketch holds when they run, so a sketch any of them
# touches cannot be drawn up front. A dimension or a constraint is NOT one of these: it names its
# operands, and they travel with the curves.
_SKETCH_ORDER_BOUND = ("sketch_edit_curve", "sketch_copy", "sketch_move", "sketch_set_text",
                       "sketch_delete_entity", "sketch_project", "sketch_insert_svg")


def _sketches_first(program, after):
    """Move every sketch that CAN be drawn before anything is solid into one phase, and return
    (that phase, the program without it).

    A profile has to exist before the feature that consumes it - but nothing says it has to be drawn
    JUST before, and drawing each one where its solid is needed is what makes the modelling acts
    look like they are still doing sketch work. A sketch qualifies when it sits on an origin plane
    and its geometry is written out rather than read from run context; what stays behind is the
    sketches that CANNOT come early - one on a datum plane or a face that a later body defines, and
    one whose curves are projected off a solid.

    A component holding a hoisted sketch moves with it (a sketch needs its parent), and a
    'design_activate_component' takes its place in the narrative so everything after it still builds
    where it did."""
    hoisted, kept_acts, phase_active = [], [], None
    for name, pre, narr, fb in program:
        if name in after:
            kept_acts.append((name, pre, narr, fb))
            continue
        move, comps, owner_of = set(), {}, {}
        active = None
        for i, step in enumerate(narr):
            args = step[1] if isinstance(step[1], dict) else None
            if args and step[0] == "model_create_component" and args.get("activate"):
                active = args.get("name")
            elif args and step[0] == "design_activate_component" and args.get("occurrence"):
                occ = re.sub(r":\d+$", "", args["occurrence"])
                active = None if occ == "root" else occ
            if step[0] != "sketch_create" or not args or not args.get("name"):
                continue
            if args.get("plane") not in _ORIGIN_PLANES:
                continue
            sk = args["name"]
            # A sketch moves early unless a later step EDITS it. A dimension, a constraint or a
            # read works on the curves wherever they were drawn, and those come with it; trimming,
            # copying, moving, texting or projecting all depend on what the sketch holds AT THAT
            # MOMENT, and hoisting every curve up front changes that.
            if any(s[0] in _SKETCH_ORDER_BOUND and isinstance(s[1], dict)
                   and s[1].get("sketch_name") == sk for s in narr):
                continue
            owner_of[i] = active
            # A sketch that later RECEIVES a projection cannot come early: projecting a body's face
            # into a sketch that predates the body is a circular timeline dependency, and Fusion
            # refuses it by name (CIRCULAR_DEPENDENCY).
            if any(s[0] == "sketch_project" and isinstance(s[1], dict)
                   and s[1].get("sketch_name") == sk for s in narr):
                continue
            end = next((j for j in range(i + 1, len(narr))
                        if narr[j][0] in ("sketch_create", "model_create_component")), len(narr))
            draws = [k for k in range(i + 1, end)
                     if narr[k][0] in _SKETCH_MAKERS and isinstance(narr[k][1], dict)
                     and narr[k][1].get("sketch_name") == sk]
            if not draws:
                continue
            # A sketch that READS run context cannot come early. Callable args mean a handle saved by
            # an earlier step - and a handle is a face or an edge on a body, so the sketch is being
            # dimensioned or constrained against geometry a LATER feature defines. Hoisting it puts
            # the sketch before that body in the timeline and the reference points backwards, which
            # Fusion rejects at validation (measured: 'InternalValidationError : rSurface3D' on a
            # line_to_surface dimension whose sketch had been hoisted ahead of the shell it measures).
            if any(not isinstance(narr[k][1], dict) for k in range(i + 1, end)):
                continue
            move.add(i)
            move.update(draws)
            owner = next((j for j in range(i - 1, -1, -1)
                          if narr[j][0] == "model_create_component"), None)
            if owner is not None and isinstance(narr[owner][1], dict) \
                    and narr[owner][1].get("activate") and narr[owner][1].get("name"):
                comps[owner] = narr[owner][1]["name"]

        kept = []
        for i, step in enumerate(narr):
            if i in comps:
                hoisted.append(step)
                phase_active = comps[i]
                kept.append(("design_activate_component",
                             {"occurrence": comps[i] + ":1"}, "ok", None))
            elif i in move:
                # The sketch phase runs the creates out of their original order, so whichever
                # component happens to be open is NOT the one this sketch was authored in. Put the
                # right one back first, or a root-level sketch lands inside an unrelated component
                # and is carried off to that component's slot.
                want = owner_of.get(i, phase_active)
                if i in owner_of and want != phase_active:
                    hoisted.append(("design_activate_component",
                                    {"occurrence": (want + ":1") if want else "root"}, "ok", None))
                    phase_active = want
                hoisted.append(step)
            else:
                kept.append(step)
        kept_acts.append((name, pre, kept, fb))

    if hoisted:
        hoisted.append(("design_activate_component", {"occurrence": "root"}, "ok", None))
    return hoisted, kept_acts

def _sketch_reading_order(phase, slots):
    """The sketch phase re-ordered so the camera reads the field once instead of commuting.

    The packer already deals cells left to right in the order the sketches are drawn, so the field
    IS in reading order - rows marching across and stepping down. What breaks the walk is that some
    sketches cannot be dealt a cell at all: one anchored to the world origin (a scale or a mirror is
    origin-relative, and an angular dimension's contract is stated against the sketch origin) stays
    in the origin band while the field sits a metre away. Interleaved with the placed ones, every
    such sketch costs a round trip out to the origin and back.

    So the pinned ones are drawn together, ahead of the field. Relative order is preserved inside
    each group, which is what keeps this safe to run AFTER the cells are dealt: the packer's order
    over the PLACED chunks is untouched, so 'slots' stays true.

    A block is one sketch: its create, the component create hoisted with it, and its drawing steps.
    Blocks move whole - a sketch separated from the component it belongs to lands in the wrong one.
    """
    # The owner is READ OFF the phase as built, never re-derived: a sketch whose component was
    # created outside this phase has no create to look at, and guessing 'root' for it drops the
    # sketch into the root component - where the geometry it feeds is then missing by name.
    blocks, pending, owner, owners = [], [], None, {}
    for step in phase:
        args = step[1] if isinstance(step[1], dict) else {}
        if step[0] == "design_activate_component":
            occ = str(args.get("occurrence") or "")
            owner = None if occ in ("", "root") else re.sub(r":\d+$", "", occ)
            continue                       # regenerated below from each block's recorded owner
        if step[0] == "model_create_component":
            pending.append(step)           # travels with the sketch it was hoisted for
            if args.get("activate") and args.get("name"):
                owner = args["name"]
            continue
        if step[0] == "sketch_create":
            blocks.append(pending + [step])
            owners[id(blocks[-1])] = owner
            pending = []
            continue
        if blocks:
            blocks[-1].append(step)

    def owner_of(block):
        return owners.get(id(block))

    def chunk_of(block):
        made = next((s for s in block if s[0] == "model_create_component"), None)
        if made:
            return made[1]["name"]
        held = owner_of(block)
        if held:
            return held
        create = next(s for s in block if s[0] == "sketch_create")
        return create[1].get("name")

    pinned = [b for b in blocks if chunk_of(b) not in slots]
    placed = [b for b in blocks if chunk_of(b) in slots]

    out, active = [], None
    for block in pinned + placed:
        want = owner_of(block)
        # a block that CREATES its component activates it on the way in; anything else has to say
        # where it belongs, or a root sketch lands inside whichever component was left open.
        if not any(s[0] == "model_create_component" for s in block) and want != active:
            out.append(("design_activate_component",
                        {"occurrence": (want + ":1") if want else "root"}, "ok", None))
        out.extend(block)
        active = want
    if out:
        out.append(("design_activate_component", {"occurrence": "root"}, "ok", None))
    return out


# Tools that ACT on parts already built - no creation step marks the moment, so they are framed on
# their own operands or the assembly act plays out wherever the camera was last left.
_RELATION_TOOLS = ("joint_create", "joint_create_as_built", "joint_edit", "joint_drive",
                   "joint_motion_link", "assembly_ground", "assembly_move", "assembly_rigid_group",
                   "assembly_constrain", "assembly_capture_position")

# {chunk: [x0, x1, y0, y1]} in the PLACED world, and {entity name: its chunk} - both filled beside
# _SLOTS, once the acts are defined.
_PLACED_BOX = {}
_CHUNK_OF = {}
# The chunk names that are COMPONENTS - a body in one is framed as an occurrence ('Name:1'), a body
# at the root as the sketch that drew it.
_COMPONENTS = set()
# Chunks a pattern acts on. Their bodies reach well outside the sketch that drew them, and the frame
# is sized from that sketch - measured, a 70 mm pad carrying a 3x2 grid at 60 mm framed at 154 mm and
# cropped. The step's own spacings cannot be read (its arguments resolve a body from run context and
# are a callable), so the frame widens by a factor rather than by a measurement.
_PATTERNED = set()
_FRAME_PATTERN_WIDEN = 3.0
# {sketch name: the origin plane it was drawn on} - a sketch is shot NORMAL to its own plane, or it
# is edge-on and renders as nothing. Shooting every sketch from the top assumes every sketch is on
# XY; an XZ one photographed from above is a blank frame.
_SKETCH_PLANE = {}
_PLANE_VIEW = {"xy": "top", "xz": "front", "yz": "right"}


def _framed(steps):
    """Insert camera rows: one when the next thing to look at is NOT already on screen, framing it
    together with the neighbours around it.

    Two rules, both learned from watching the run. A camera row per family moved the camera ~126
    times and bounced between a cameo and the gyroscope every time the story alternated, so a
    subject already inside the standing frame gets no row at all. And a family is one compact cell,
    so framing it alone fills the screen with a 20 mm sketch; the focus list grows outward through
    the nearest already-built neighbours until it spans the caller's target, which is what puts the
    work in context instead of under a microscope.

    A component or sketch the narrative already frames by hand keeps its own row, and one that
    never gets geometry before the next entity starts is skipped - there is nothing to frame yet.
    """
    ready = {}                       # group -> (last step index it is ready at, [member names])
    hand_framed = set()
    for k, step in enumerate(steps):
        a = steps[k][1] if isinstance(steps[k][1], dict) else {}
        if steps[k][0] == "view_set" and a.get("focus"):
            f = a["focus"]
            for nm in (f if isinstance(f, list) else [f]):
                hand_framed.add(_group_of(str(nm)))

    def note(name, at, member):
        g = _group_of(name)
        if g in hand_framed:
            return
        cur = ready.get(g)
        members = (cur[1] if cur else [])
        if member not in members:
            members = members + [member]
        ready[g] = (max(at, cur[0]) if cur else at, members)

    for i, step in enumerate(steps):
        args = step[1] if isinstance(step[1], dict) else {}
        if step[0] == "sketch_create" and args.get("name"):
            name = args["name"]
            end = next((j for j in range(i + 1, len(steps))
                        if steps[j][0] in ("sketch_create", "model_create_component")), len(steps))
            drawn = next((k for k in range(i + 1, end) if steps[k][0] in _SKETCH_MAKERS
                          and isinstance(steps[k][1], dict)
                          and steps[k][1].get("sketch_name", name) == name), None)
            # A profile drawn inside a component only exists to be extruded a step later: the body
            # frame covers it, and framing the rectangle first is what makes a modelling act look
            # like it is doing sketch work. A sketch at the ROOT is the sketch tools' own subject
            # and keeps its frame - and so does one whose body is built in a LATER act, or the
            # sketch phase would draw everything off camera with nothing following to frame it.
            covered = any(s[0] in _BODY_MAKERS and isinstance(s[1], dict)
                          and s[1].get("sketch_name") == name for s in steps)
            fixture = covered and _CHUNK_OF.get(name, name) != name
            if drawn is not None and not fixture:
                note(name, drawn, name)
        elif step[0] == "model_create_component" and args.get("activate") and args.get("name"):
            comp = args["name"]
            # the chunk runs to the NEXT create: a later part's body is not this part's body, and
            # framing this occurrence on it would aim the camera at the wrong slot.
            end = next((j for j in range(i + 1, len(steps))
                        if steps[j][0] == "model_create_component"), len(steps))
            body = next((k for k in range(i + 1, end) if steps[k][0] in _BODY_MAKERS), None)
            if body is not None:
                note(comp, body, comp + ":1")
        elif step[0] in _BODY_MAKERS and args.get("sketch_name") in _CHUNK_OF:
            # The moment a solid appears, whether or not this act is where its component and sketch
            # were made. Once the sketch phase hoists those away, a creation-only trigger leaves a
            # whole act - the finale among them - with no camera row at all, playing out at
            # whatever zoom the previous act left behind.
            owner = _CHUNK_OF.get(args["sketch_name"], args["sketch_name"])
            note(owner, i, owner + ":1" if owner in _COMPONENTS else owner)

    inserts = {}
    for _g, (at, members) in ready.items():
        inserts.setdefault(at, []).extend(members)

    # A joint, a ground, a rigid group or a drive ACTS on parts that already exist, so no creation
    # step marks the moment - and the parts it mates sit in whichever slots they were built in.
    # Without a row of their own the whole assembly act plays out wherever the camera happened to be
    # left. These frame on their own operands, at the step that does the work.
    relation_at = set()
    for i, step in enumerate(steps):
        if step[0] not in _RELATION_TOOLS or not isinstance(step[1], dict):
            continue
        operands = []
        for key in ("occurrence", "occurrences", "occurrence_one", "occurrence_two"):
            v = step[1].get(key)
            for nm in (v if isinstance(v, list) else [v]):
                # an occurrence may be named through a sub-entity ('Carrier:1:origin') - the frame
                # wants the occurrence itself.
                if isinstance(nm, str) and nm and nm != "origin":
                    m = re.match(r"^([^:]+:\d+)", nm)
                    if m and m.group(1) not in operands:
                        operands.append(m.group(1))
        if operands:
            at = i - 1 if i else 0
            inserts.setdefault(at, []).extend(operands)
            relation_at.add(at)

    # A neighbour is only worth framing while it still answers to the name it was built under: the
    # story renames and deletes as it goes, and view_set refuses a focus it cannot resolve.
    retired = {}
    for k, step in enumerate(steps):
        a = step[1] if isinstance(step[1], dict) else {}
        if step[0] in ("design_set_name", "design_delete_occurrence", "design_move_occurrence",
                       "design_delete_feature", "mesh_delete", "cam_delete"):
            for key in ("target", "occurrence", "name"):
                if isinstance(a.get(key), str):
                    retired.setdefault(re.sub(r":\d+$", "", a[key]), k)

    out, frame, built = [], None, []
    for i, step in enumerate(steps):
        out.append(step)
        members = inserts.get(i)
        if not members:
            continue
        built = [b for b in built
                 if all(retired.get(re.sub(r":\d+$", "", str(n)), len(steps)) > i for n in b[0])]
        # A relation's operands are NOT trimmed: a joint between two parts is only legible with BOTH
        # of them in shot, so the frame widens to hold them however far apart they were built.
        # A SKETCH has almost no visual area, so a frame spanning two of them is sized by the gap
        # between them rather than by either one - graded by eye, sketch-only frames naming two or
        # more scored 1 good in 27. One at a time; a solid is big enough to share a shot.
        solid = any(str(n).endswith(":1") for n in members)
        if i not in relation_at:
            keep = _FRAME_MAX_SUBJECTS if solid else _FRAME_SKETCH_GROUP
            members = _frame_cluster(members, None if solid else _FRAME_SKETCH_SPAN)[-keep:]
        subject = _frame_box(members)
        built.append((members, subject))
        if subject and frame and _inside(subject, frame):
            continue                     # already on screen - moving would only jog the view
        # How far the frame reaches is set by the SUBJECT's own size, never by a constant: a 20 mm
        # sketch inside a fixed 220 mm neighbourhood renders in a 440 mm view, which is 4% of the
        # frame - graded by eye, every one of those read as a speck. A joint is the exception that
        # wants space around it, because a mate is watched rather than inspected.
        # from the LARGEST single subject, never the union: a union of two far-apart parts is a
        # measure of their SEPARATION, and zooming to 2.2x that is the whole-field photograph again.
        each = [_frame_box([m]) for m in members]
        own = max((max(b[1] - b[0], b[3] - b[2]) for b in each if b), default=0.0)
        if any(_CHUNK_OF.get(str(m), str(m)).rstrip(":1") in _PATTERNED
               or _CHUNK_OF.get(str(m), str(m)) in _PATTERNED for m in members):
            own *= _FRAME_PATTERN_WIDEN
        span = max(own * _FRAME_CONTEXT, _FRAME_MIN_SPAN)
        if i in relation_at:
            span *= _FRAME_RELATION_WIDEN
        # A SKETCH frame reaches for its neighbours on purpose: sketch commands are quick, and a
        # camera move for each one is more motion than the work is worth. The span cap is what keeps
        # it honest - a handful of sketches from the SAME row share a frame, scattered ones do not,
        # and the group is what the earlier one-at-a-time rule was over-correcting for.
        if not solid and subject is not None:
            span, cap = _FRAME_SKETCH_SPAN, _FRAME_SKETCH_GROUP
        else:
            cap = _FRAME_MAX_SUBJECTS
        focus, box = _frame_neighbourhood(members, subject, built, span, cap)
        frame = _expand(box, _FRAME_MARGIN) if box else None
        out.append(_watch(focus))
    return out


def _frame_box(names):
    """The world box the named entities occupy, or None when none of them is placed. A name is
    resolved through _CHUNK_OF first: a sketch rides on the body that owns it, and it is that body's
    box the camera will see."""
    stems = [re.sub(r":\d+$", "", str(n)) for n in names]
    got = [_PLACED_BOX[c] for c in (_CHUNK_OF.get(s, s) for s in stems) if c in _PLACED_BOX]
    if not got:
        return None
    return [min(b[0] for b in got), max(b[1] for b in got),
            min(b[2] for b in got), max(b[3] for b in got)]


def _frame_cluster(members, limit=None):
    """The members that actually fit in one shot together, keeping the ones nearest the LAST one
    built - the work just done. A family is framed as a family, but a family whose members ended up
    in different rows of the field (SlotA..SlotH, or a profile pinned at the origin beside the post
    it revolved) does not fit in any one frame, and stretching to cover both leaves every member a
    speck."""
    if len(members) < 2 or _frame_box(members) is None:
        return members
    anchor = _frame_box([members[-1]]) or _frame_box(members)
    # what the group may grow to is set by the anchor's OWN size, so a family of small sketches
    # stays tight and a family of large parts is allowed the room it needs
    own = max(anchor[1] - anchor[0], anchor[3] - anchor[2])
    limit = limit or max(own * _FRAME_CONTEXT, _FRAME_MIN_SPAN) * _FRAME_STRETCH
    kept, box = [], list(anchor)
    for name in members:
        b = _frame_box([name])
        if b is None:
            # Where this one sits is unknown, so it cannot be shown to fit - and keeping it anyway
            # is what let a member at the far end of the field back into a trimmed frame.
            continue
        grown = [min(box[0], b[0]), max(box[1], b[1]), min(box[2], b[2]), max(box[3], b[3])]
        if max(grown[1] - grown[0], grown[3] - grown[2]) > limit:
            continue
        kept.append(name)
        box = grown
    return kept or members


def _expand(box, factor):
    cx, cy = (box[0] + box[1]) / 2.0, (box[2] + box[3]) / 2.0
    hw, hh = (box[1] - box[0]) * factor / 2.0, (box[3] - box[2]) * factor / 2.0
    return [cx - hw, cx + hw, cy - hh, cy + hh]


def _inside(box, outer):
    return (outer[0] <= box[0] and box[1] <= outer[1]
            and outer[2] <= box[2] and box[3] <= outer[3])


def _frame_neighbourhood(members, subject, built, target=None, cap=None):
    """The focus list to hand view_set: the subject, widened through the nearest already-built
    neighbours until it spans 'target' (_FRAME_MIN_SPAN by default). Only things already
    built can be framed - a
    sketch that does not exist yet cannot be resolved - so the frame trails backwards, which reads
    as the new work arriving beside what it followed."""
    target, cap = target or _FRAME_MIN_SPAN, cap or _FRAME_MAX_SUBJECTS
    known = [(names, b) for names, b in built if b is not None]
    if subject is None:
        # Nothing is known about where this subject is - a sketch with no coordinates of its own, an
        # SVG import. Framing it alone lets the camera fit whatever degenerate extent it has and dive
        # into the origin construction geometry, so hand it the last few neighbours for scale.
        focus, box = list(members), None
        for names, other in reversed(known[-_FRAME_FALLBACK_NEIGHBOURS:]):
            grown = list(other) if box is None else [
                min(box[0], other[0]), max(box[1], other[1]),
                min(box[2], other[2]), max(box[3], other[3])]
            # the ceiling applies here too - lending a subject three neighbours spread over a metre
            # buys it scale by making everything in shot a speck.
            if max(grown[1] - grown[0], grown[3] - grown[2]) > target * _FRAME_STRETCH:
                continue
            focus += [n for n in names if n not in focus]
            box = grown
        return focus, box

    focus, box = list(members), list(subject)

    def span(b):
        return max(b[1] - b[0], b[3] - b[2])

    def reach(b):
        return max(abs((b[0] + b[1]) / 2.0 - (subject[0] + subject[1]) / 2.0),
                   abs((b[2] + b[3]) / 2.0 - (subject[2] + subject[3]) / 2.0))

    for names, other in sorted(known, key=lambda nb: reach(nb[1])):
        if span(box) >= target or len(focus) >= cap:
            break
        if all(n in focus for n in names):
            continue
        grown = [min(box[0], other[0]), max(box[1], other[1]),
                 min(box[2], other[2]), max(box[3], other[3])]
        # A neighbour is only context while it stays in shot WITH the subject. Testing the union
        # AFTER adding is what let one distant part drag the frame out to the whole 1500 mm field,
        # where every named subject reads as a speck.
        if span(grown) > target * _FRAME_STRETCH:
            continue
        focus += [n for n in names if n not in focus]
        box = grown
    return focus, box


# --- physical layout: one slot per scratch chunk, laid out in narrative order --------------------
# A chunk's coordinates as written are LOCAL to that chunk: two chunks may be authored on the same
# patch of the XY plane, and this pass translates each one into a cell of its own, in the order the
# acts build them. So a camera framed on a chunk contains its subject, and the neighbours in shot
# are the steps that ran just before and just after it.
#
# Rigid translation is what makes it safe: a chunk's internal offsets, sizes and probe points all
# move with it, so nothing inside a chunk can be broken by the move. What could break is a world
# point one chunk aims at another - so the chunk is the COMPONENT, which is the unit those points
# are shared within, and the gyroscope/vise/stock world at the origin never moves at all. A world
# point that DOES cross a component boundary fails the layout gate in test_tool_verify_complete.py
# rather than drifting quietly: put the geometry in one component, or pin it.

# Key PAIRS that pin a step to a PLACE. A key holding a delta (dx, dy, distance, spacing) or a size
# (radius, slot_length, depth) is deliberately absent - a rigid translation leaves those alone. Both
# halves of a pair must be present: a lone 'x' is a cross-mode input a refusal step passes to be
# rejected, not a place, and reading it as one would drag its whole chunk across the world.
_PLACE_PAIRS = (("x", "y"), ("x1", "y1"), ("x2", "y2"), ("x3", "y3"), ("cx", "cy"),
                ("center_x", "center_y"))
# Tools whose x/y is a TRANSFORM relative to the entity's own origin, not a place in the world.
# design_add_instance sets an occurrence's translation, and the component's geometry already carries
# wherever the layout put it - so shifting the transform too applies the offset TWICE and throws the
# instance a whole field away from the original it is meant to sit beside.
_PLACE_DELTA_TOOLS = ("design_add_instance",)

_PLACE_XYZ = ("nearest_to", "point")        # one [x, y, z], always world
_PLACE_POINTS = ("points",)                 # a list of [x, y] or [x, y, z], always world

# Keys naming the entity a step addresses, most specific first. A step that names one belongs to
# that entity's chunk wherever it sits in the narrative; only a step naming none inherits the chunk
# being built around it. 'name' is absent on purpose - on a creating step it names the thing being
# made, which belongs to the chunk around it (a datum plane that split off on its own name would be
# left behind by the body it cuts).
_PLACE_NAMES = ("sketch_name", "target", "occurrence", "component", "body", "bodies")

# A sketch's coordinates are in ITS plane's frame, not the world's: on XZ a 'cy' is a world Z, on YZ
# a 'cx' is a world Y. Each entry maps the sketch's (u, v) onto the world axes a translation moves,
# with None for the axis the plane does not span. Reading an XZ sketch as if it were XY moves a
# revolve profile off its axis, which turns a sphere into a torus and passes every count check.
_PLACE_FRAMES = {"xy": ("x", "y"), "xz": ("x", None), "yz": ("y", None)}

# Chunks the layout cannot see are one rigid body. Both are components in their own right - which is
# what lets one find_geometry name each without ambiguity - but they are stacked on purpose, and the
# call that joins them reaches for both through run-time handles no static read can follow. Sharing
# ground is NOT the test: half the scratch field is authored on the same patch of XY by chance.
_PLACE_WITH = {
    "ReplBlock": "ReplRoof",     # the open sheet that replaces the block's top face sits above it
}

# Chunks a JOINT or an assembly CONSTRAINT co-locates. Both move a part onto the other's geometry,
# so the
# cell the layout dealt the mover is abandoned the instant the joint lands, and the mover arrives in
# the PARTNER's cell - on top of whatever the packer had already put there. Measured on the finished
# document: the ball/axis/rigid post chain piled five bodies into one cell and spilled into the next,
# which is what a viewer sees as cameos sitting inside older ones.
# Each group is dealt ONE cell and every member takes the SAME offset, so the group travels as the
# rigid assembly it is about to become and keeps its authored relative positions. The joint then
# moves parts WITHIN that cell, which is the only place it was ever going to move them.
_JOINT_GROUPS = (
    ("BallSphere", "BallPost", "AxisPost", "RigidPost"),
    ("TorusRing", "TorusPost"),
    ("AsbPin", "AsbPlate"),
    ("ConA", "ConB"),            # the single-relationship constrain pair
    ("MateSeat", "MateArm"),     # the multi-relationship one - a seat AND a turn in one feature
    # the joint bench: the base and every indicator arm its stations carry into place
    ("JointBase",) + tuple("Ind" + s[0] for s in _JOINT_STATIONS),
)
_JOINT_FAMILY = {c: g[0] for g in _JOINT_GROUPS for c in g}

# Chunks whose READ-BACK is relative to the world origin, so moving them changes the answer. The
# angular-dimension contract is "the wedge FACING THE SKETCH ORIGIN", which flips to the supplement
# once the crossing point moves to the other side of it - a 60 degree beat silently becomes 120.
_PLACE_ANCHORED = ("W3Dims",)

# Tools whose RESULT is measured from the world origin, so the further out the chunk sits the
# further its result is thrown: a mirror about an origin plane reflects to the far side (joined,
# that is ONE body spanning both), and a scale multiplies the distance along with the size. Measured
# on a placed field: EmbossBlock mirrored+joined at y 720 became a single body 1480 mm long, and
# ScaleBlock scaled at y 720 ended up at y 1594. A chunk either of these acts on stays put.
_ORIGIN_RELATIVE_TOOLS = ("model_mirror", "model_scale")

_PIN_HALF = 160.0        # half-width of the origin neighbourhood, which never moves
_FIELD_X0 = 200.0        # the scratch field starts clear of it
# ...and starts ABOVE the band the anchored chunks occupy (every one of them sits at y <= 140), so
# the packer never has to step around an obstacle. Stepping around one is what made the sequence
# jump: a row would skip a gap, and the eye loses the order the acts were built in.
_FIELD_Y0 = 220.0
_FIELD_WIDTH = 900.0     # a row wraps past here - narrow keeps the field a BLOCK, not a long strip
# Empty millimetres between one chunk's cell and the next. This is ALSO the packer's only margin for
# error, and it needs one: a cell is measured from the coordinates the steps are WRITTEN with, which
# under-counts the body that grows from them - a circle contributes its centre, not its radius, and
# an extrude contributes nothing at all in the third axis. So the real geometry routinely reaches
# past its cell by a radius or two, and at 18 mm that reach landed in the neighbour. Measured on the
# finished document: widening this from 18 to 60 is what separates the cameos that were touching.
# The cost is a taller field, which nothing pays for - every chunk is framed on itself.
_FIELD_GUTTER = 60.0


def _place_owner(args):
    """The entity a step addresses, or None when it names none. An occurrence path is reduced to
    its component - instance :1 and :2 of a component are the same chunk of the world."""
    for key in _PLACE_NAMES:
        v = args.get(key)
        if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
            v = v[0]
        if isinstance(v, str) and v:
            return re.sub(r":\d+$", "", v.split("+")[0].strip())
    return None


def _place_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _place_points(args, frame="xy", tool=""):
    """Every world position the step's arguments pin, as (x, y) with None on an axis the argument
    does not constrain. Pair keys are read in 'frame' - the plane the sketch was drawn on - so an
    XZ sketch pins only X. frame None is a plane derived from geometry that travels with its chunk,
    which anchors nothing."""
    num, out = _place_num, []
    axes = _PLACE_FRAMES.get(frame or "", (None, None))
    pairs = tuple(p for p in _PLACE_PAIRS
                  if not (p == ("x", "y") and tool in _PLACE_DELTA_TOOLS))
    for kx, ky in pairs:
        if not (num(args.get(kx)) and num(args.get(ky))):
            continue
        # a circle or polygon is authored as a CENTRE plus a radius, so the centre alone measures it
        # as a point and the chunk's cell comes out far too small to hold it
        r = args.get("radius") if (kx, ky) == ("cx", "cy") else None
        r = r if num(r) else 0
        for dx, dy in (((0, 0),) if not r else ((-r, -r), (r, r))):
            world = dict(zip(axes, (args[kx] + dx, args[ky] + dy)))
            world.pop(None, None)
            if world:
                out.append((world.get("x"), world.get("y")))
    if args.get("kind") == "plane" and num(args.get("offset")):
        # an origin plane's offset is a world position along its NORMAL: an XZ plane sits at that Y,
        # so a body it splits moves out from under it unless the offset moves too.
        normal = {"xz": "y", "yz": "x"}.get(args.get("plane"))
        if normal == "x":
            out.append((args["offset"], None))
        elif normal == "y":
            out.append((None, args["offset"]))
    for key in _PLACE_XYZ:
        v = args.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 2 and num(v[0]) and num(v[1]):
            out.append((v[0], v[1]))
    for key in _PLACE_POINTS:
        v = args.get(key)
        if isinstance(v, (list, tuple)):
            for p in v:
                if isinstance(p, (list, tuple)) and len(p) >= 2 and num(p[0]) and num(p[1]):
                    out.append((p[0], p[1]))
    return out


def _place_walk(steps, home_out=None):
    """Yield (step, chunk, cursor, frame) for every step. chunk is the rigid body the step
    addresses: the ACTIVE COMPONENT when one is open, otherwise the root-level sketch. A component
    is the natural unit - CC1 and CC2 are two sketches inside CombineCameo whose solids must keep
    overlapping, and they do because the component moves as one - while root sketches each get their
    own cell, so SlotA..SlotH lie side by side and the framing pass still frames all eight together.

    chunk is None for a step that pins nothing to the world. cursor is the body being built around
    the step, which couples a probe to the pad it reads back. frame is the plane the step's pair
    keys are read in. home_out, when given, collects {entity name: its chunk} - the map that turns a
    sketch name back into the body it rides on."""
    active, plane, cursor = None, {}, None
    home = home_out if home_out is not None else {}
    for step in steps:
        args = step[1]
        frame = "xy"
        if isinstance(args, dict):
            if step[0] == "model_create_component" and args.get("name"):
                if args.get("activate"):
                    active = args["name"]
                home[args["name"]] = args["name"]
            elif step[0] == "design_activate_component" and args.get("occurrence"):
                occ = re.sub(r":\d+$", "", args["occurrence"])
                active = None if occ == "root" else occ
            elif step[0] == "model_construction" and args.get("kind") == "plane" and args.get("name"):
                # a datum offset from an origin plane keeps that plane's frame; one built on a path
                # or a face gets its frame from geometry, and nothing drawn on it is world-anchored.
                plane[args["name"]] = args.get("plane") if args.get("plane") in _PLACE_FRAMES else None
            elif step[0] == "sketch_create" and args.get("name"):
                home[args["name"]] = active or args["name"]
                p = args.get("plane")
                plane[args["name"]] = p if p in _PLACE_FRAMES else plane.get(p)
            if step[0] == "design_activate_component":
                # back at the root nothing is being built around the steps that follow, so the
                # cursor goes with it - leaving it set attributes the next loose coordinate to
                # whatever component happened to be open last.
                cursor = active
            elif active:
                cursor = active
            elif args.get("sketch_name") in home:
                cursor = home[args["sketch_name"]]
            elif step[0] == "sketch_create" and args.get("name"):
                cursor = home[args["name"]]
            if step[0].startswith("sketch_"):
                sk = args.get("sketch_name") or (args.get("name") if step[0] == "sketch_create" else None)
                frame = plane.get(sk, "xy") if sk else "xy"
            if _place_points(args, frame, step[0]):
                own = _place_owner(args)
                yield step, (home.get(own, own) if own else cursor), cursor, frame
                continue
        # a callable builds its arguments from run context and cannot be read here, so it belongs
        # to the body being built around it - which is where any world point it bakes in came from.
        yield step, (cursor if callable(args) else None), cursor, frame


def _place_shift(args, dx, dy, frame="xy", tool=""):
    """args translated by (dx, dy): every pinned position moves, everything else is untouched. The
    pair keys move by the world delta resolved into 'frame's axes, so an XZ sketch shifts only the
    coordinate that is a world X and leaves the one that is a world Z alone."""
    num = _place_num
    delta = {"x": dx, "y": dy, None: 0.0}
    du, dv = (delta[a] for a in _PLACE_FRAMES.get(frame or "", (None, None)))

    def shift_seq(v):
        if not isinstance(v, (list, tuple)) or len(v) < 2 or not (num(v[0]) and num(v[1])):
            return v
        return [v[0] + dx, v[1] + dy] + list(v[2:])

    if callable(args):
        return lambda ctx, _f=args: _place_shift(_f(ctx), dx, dy, frame, tool)
    out = dict(args)
    for kx, ky in _PLACE_PAIRS:
        if (kx, ky) == ("x", "y") and tool in _PLACE_DELTA_TOOLS:
            continue
        if num(out.get(kx)) and num(out.get(ky)):
            out[kx], out[ky] = out[kx] + du, out[ky] + dv
    if out.get("kind") == "plane" and num(out.get("offset")):
        out["offset"] += {"xz": dy, "yz": dx}.get(out.get("plane"), 0.0)
    for key in _PLACE_XYZ:
        if key in out:
            out[key] = shift_seq(out[key])
    for key in _PLACE_POINTS:
        v = out.get(key)
        if isinstance(v, (list, tuple)):
            out[key] = [shift_seq(p) for p in v]
    return out


def _place_slots(program):
    """Measure every chunk, then deal each movable one a cell of its own; return {chunk: (dx, dy)}.

    Cells are dealt left to right in the order the acts build them, wrapping into a new row past
    _FIELD_WIDTH, so the camera walks the field in the order the story runs.

    Anything reaching into the origin neighbourhood stays exactly where it is: that is the
    gyroscope, the vise built around it, the CAM stock and the fallback world, which are addressed
    by scripts and by each other."""
    box, order, locked = {}, [], set()
    for _name, _pre, narr, _fb in program:
        for step, chunk, _cursor, frame in _place_walk(narr):
            args = step[1]
            if chunk is None or not isinstance(args, dict):
                continue
            # A sketch on a GLOBAL origin plane is nailed to that plane: an XZ sketch is at y=0 and
            # cannot be carried in y, so a chunk holding one can only move within the plane it is
            # drawn on. Rather than translate it into a plane it does not live on - which is how a
            # pipe cut ends up passing through empty space - such a chunk stays where it was authored.
            if frame in ("xz", "yz") and any(
                    _place_num(args.get(kx)) and _place_num(args.get(ky))
                    for kx, ky in _PLACE_PAIRS):
                locked.add(chunk)
            for x, y in _place_points(args, frame, step[0]):
                if chunk not in box:
                    box[chunk] = [None, None, None, None]
                    order.append(chunk)
                b = box[chunk]
                if x is not None:
                    b[0] = x if b[0] is None else min(b[0], x)
                    b[1] = x if b[1] is None else max(b[1], x)
                if y is not None:
                    b[2] = y if b[2] is None else min(b[2], y)
                    b[3] = y if b[3] is None else max(b[3], y)
    # a chunk drawn only on an edge-on plane constrains one axis; the other reads as zero extent at
    # the origin, which is where that plane sits.
    for b in box.values():
        for i in (0, 1, 2, 3):
            if b[i] is None:
                b[i] = 0.0

    # Cells are dealt a FAMILY at a time - SlotA..SlotH together - and a family that will not fit in
    # what is left of the row starts the next one, so the frame _framed puts around a family never
    # straddles a row break.
    locked.update(c for c in _PLACE_ANCHORED if c in box)
    for _name, _pre, narr, _fb in program:
        for step, _chunk, cursor, _frame in _place_walk(narr):
            if step[0] in _ORIGIN_RELATIVE_TOOLS:
                named = [n for n in ((step[1].get("bodies") or []) if isinstance(step[1], dict)
                                     else []) if isinstance(n, str)]
                for c in [_CHUNK_OF.get(n, n) for n in named] + ([cursor] if cursor else []):
                    if c in box:
                        locked.add(c)
    for chunk, (x0, x1, y0, y1) in box.items():
        if x0 <= _PIN_HALF and -_PIN_HALF <= x1 and y0 <= _PIN_HALF and -_PIN_HALF <= y1:
            locked.add(chunk)
    for a, b in _PLACE_WITH.items():
        if a in locked or b in locked:
            locked |= {a, b}

    families, welded = {}, {}
    for chunk in order:
        if chunk in locked:
            continue
        # a joint group is ONE cell: its members are collapsed to a single pseudo-chunk here and
        # handed the same offset below, so the packer never deals a cell to a part a joint is about
        # to move out of it.
        lead = _JOINT_FAMILY.get(chunk)
        if lead:
            # the pseudo-chunk is prefixed so it can never collide with a real chunk name - the
            # group's leader IS a real chunk, and reusing its name would make the group's cell and
            # the leader's own cell the same entry.
            weld = "~" + lead
            welded.setdefault(weld, []).append(chunk)
            if weld in box:
                continue
            box[weld] = list(box[chunk])
            families.setdefault(_group_of(lead), []).append(weld)
            continue
        families.setdefault(_group_of(chunk), []).append(chunk)
    # the welded cell has to hold every member's authored ground, or the joint group overflows it
    for weld, members in welded.items():
        for c in members:
            b = box[c]
            w = box[weld]
            box[weld] = [min(w[0], b[0]), max(w[1], b[1]), min(w[2], b[2]), max(w[3], b[3])]

    # The field starts beside the gyroscope and steps AROUND what cannot move, rather than being
    # exiled to a band of its own - a scene twice as tall is not easier to watch. The obstacles are
    # small and clustered (the origin world, and the strip of chunks nailed to an origin plane), so
    # stepping past one costs a gap in a row, not a row.
    blocked = [(box[c][0] - _FIELD_GUTTER, box[c][1] + _FIELD_GUTTER,
                box[c][2] - _FIELD_GUTTER, box[c][3] + _FIELD_GUTTER) for c in locked]

    def clear(cx, cy, w, h):
        """The leftmost x at or after cx where a w x h cell at cy hits nothing, or None past the
        row's end."""
        while cx + w <= _FIELD_X0 + _FIELD_WIDTH:
            hit = next((b for b in blocked
                        if cx <= b[1] and b[0] <= cx + w and cy <= b[3] and b[2] <= cy + h), None)
            if hit is None:
                return cx
            # a zero-width obstacle sits exactly at cx, so step past it, never onto it
            cx = max(hit[1], cx + _FIELD_GUTTER)
        return None

    offsets, x, y, row_h = {}, _FIELD_X0, _FIELD_Y0, 0.0
    for members in families.values():
        span = sum(box[c][1] - box[c][0] + _FIELD_GUTTER for c in members) - _FIELD_GUTTER
        if x > _FIELD_X0 and x + min(span, _FIELD_WIDTH) > _FIELD_X0 + _FIELD_WIDTH:
            x, y, row_h = _FIELD_X0, y + row_h + _FIELD_GUTTER, 0.0
        for chunk in members:
            x0, x1, y0, y1 = box[chunk]
            w, h = x1 - x0, y1 - y0
            at = clear(x, y, w, h)
            if at is None:
                x, y, row_h = _FIELD_X0, y + row_h + _FIELD_GUTTER, 0.0
                at = clear(x, y, w, h) or x
            shift = (at - x0, y - y0)
            # one offset for the whole welded group - the members keep their authored relative
            # positions, so the assembly arrives in its cell already put together.
            for c in welded.get(chunk, [chunk]):
                offsets[c] = shift
            x = at + w + _FIELD_GUTTER
            row_h = max(row_h, h)
    return offsets


def _placed(steps, offsets):
    """steps with every chunk translated into the slot _place_slots gave it."""
    out = []
    for step, chunk, _cursor, frame in _place_walk(steps):
        off = offsets.get(chunk)
        if off and (off[0] or off[1]):
            step = (step[0], _place_shift(step[1], off[0], off[1], frame, step[0])) + tuple(step[2:])
        out.append(step)
    return out


def _px(chunk, x):
    """The world X an authored X ends up at, once 'chunk' is placed. A read-back that asserts WHERE
    geometry landed - the copy that must sit 100 mm right of the original, the slot whose arc
    centres must be 52 mm apart at a known place - has to ask, because the chunk moved. Asserting a
    bare authored number instead is how a layout change turns a real check into a false failure."""
    return x + _SLOTS.get(chunk, (0.0, 0.0))[0]


def _py(chunk, y):
    """The world Y an authored Y ends up at, once 'chunk' is placed. See _px."""
    return y + _SLOTS.get(chunk, (0.0, 0.0))[1]


# --- the build, as ACTS: one recognizable gyroscope, end to end, in one unsaved document -------
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
    ("doc_new", {}, _new_document, None),
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
    # Each derived parameter's expected value is what the DESIGN evaluates the expression to off the
    # 120 mm driver above it - so the read-back proves Fusion resolved the reference, not that the
    # expression text was accepted (the CAM parameter store accepts a bogus name unevaluated).
    ("param_add", {"name": "GimbalDia", "expression": "120 mm",
                   "comment": "The one driver: every ring, bore and pin below derives from it"},
     _param_added("GimbalDia", 120), None),
    ("param_add", {"name": "RotorR", "expression": "GimbalDia / 5"}, _param_added("RotorR", 24), None),
    # A RULE, not a ratio: the radial band each ring is cut from scales with the gimbal but stops
    # at a floor, so shrinking the driver thins the rings only until they reach a width that can
    # still be made and still hold a pin bore. Every bore below is the ring's OD less this band, so
    # the section is stated once and the two rings cannot drift to different widths.
    ("param_add", {"name": "RingBand", "expression": "max(GimbalDia * 0.05; 4 mm)",
                   "comment": "Radial width of a gimbal ring - floored so it stays makeable"},
     _param_added("RingBand", 6), None),
    ("param_add", {"name": "InnerOD", "expression": "GimbalDia * 0.3"}, _param_added("InnerOD", 36), None),
    ("param_add", {"name": "InnerBoreR", "expression": "InnerOD - RingBand",
                   "comment": "Inner ring bore - its OD less one band"},
     _param_added("InnerBoreR", 30), None),
    ("param_add", {"name": "OuterOD", "expression": "GimbalDia * 0.4"}, _param_added("OuterOD", 48), None),
    ("param_add", {"name": "OuterBoreR", "expression": "OuterOD - RingBand",
                   "comment": "Outer ring bore - its OD less one band"},
     _param_added("OuterBoreR", 42), None),
    ("param_add", {"name": "FrameOpenR", "expression": "GimbalDia * 0.45"}, _param_added("FrameOpenR", 54), None),
    ("param_add", {"name": "FramePlateR", "expression": "GimbalDia * 0.55"}, _param_added("FramePlateR", 66), None),
    ("param_set_favorite", {"name": "GimbalDia", "favorite": True}, _param_favorited("GimbalDia"), None),
    ("param_get", {}, _params_listed("GimbalDia", "RotorR", "RingBand", "InnerBoreR", "InnerOD",
                                     "OuterBoreR", "OuterOD", "FrameOpenR", "FramePlateR"), None),
    # THE EIGHT-PART CAST, BUILT AS THE NESTED CHAIN IT IS. A stage owns its own parts: the pedestal
    # belongs to the frame, the inner ring hangs inside the outer, the shaft is carried by the inner
    # ring and the rotor spins on the shaft. Nesting at CREATION rather than re-parenting afterwards
    # costs nothing and moves nothing - a child created with no placement of its own inherits its
    # parent's frame, and every part here is built on the world origin - while a flat row of eight
    # siblings would say the mechanism has no stages at all.
    # It also collapses what ACT 8 has to strip: deleting InnerRing:1 takes the shaft and the rotor
    # with it, and Frame:1 takes the pedestal.
    # Carrier stays at the ROOT deliberately - it is the part the vise grips and the one survivor of
    # ACT 8, so it must not be inside anything that gets deleted.
    ("model_create_component", {"name": "Frame", "activate": True}, _made_component, None),
    ("model_create_component", {"name": "Pedestal", "parent": "Frame", "activate": True},
     _made_component, None),
    ("model_create_component", {"name": "Carrier", "activate": True}, _made_component, None),
    ("model_create_component", {"name": "OuterRing", "activate": True}, _made_component, None),
    ("model_create_component", {"name": "InnerRing", "activate": True}, _made_component, None),
    # A JOINT YOU INTEND TO DRIVE WANTS TWO SIBLINGS. Measured twice on this mechanism: nesting the
    # rotor inside the shaft it turns on left CrankAxis reading -0.0 against a commanded 30 deg
    # through its motion link, and nesting the inner ring inside the outer left PivotInner reading
    # back 25 at the drive and 0 at the next assembly_get - the solver put it back. So the rings stay
    # siblings and the inner ring keeps its own parts (rotor and shaft) instead.
    ("model_create_component", {"name": "RotorShaft", "parent": "InnerRing", "activate": True},
     _made_component, None),
    ("model_create_component", {"name": "Rotor", "parent": "InnerRing", "activate": True},
     _made_component, None),
    ("model_create_component", {"name": "Crank", "activate": True}, _made_component, None),
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
    # the camera has been fitted to an EMPTY document since the overture - the skeleton is the
    # first thing in the world worth looking at.
    _watch_all(),
    ("sketch_constrain", {"constraint": "horizontal", "entity_one": "line:0",
                          "sketch_name": "Skeleton"}, "ok", None),
    ("sketch_dimension", {"dim_type": "distance", "entity_one": "point:0", "entity_two": "point:1",
                          "sketch_name": "Skeleton", "value": "GimbalDia"}, "ok", None),
    ("sketch_get", {"sketch_name": "Skeleton"}, "ok", None),
    # draw a helper constraint then delete it - the count drop is the read-back.
    ("sketch_delete_entity", {"sketch_name": "Skeleton", "target": "constraint:0"}, "ok", None),
    # the carrier hub's plane sits BELOW the rotor sweep (vertical zoning) - construction proves here.
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -40, "name": "CarrierHubPlane"},
     _datum_plane("xy"), None),
    # Concentric ring bands on ONE plane, which is what a gimbal looks like AT REST - the rings
    # only leave that plane when a pivot is driven. What makes them a gimbal rather than a stack of
    # washers is the PINS below: each ring hangs from its parent on an axis lying IN the ring plane,
    # and the two pin axes are perpendicular (X for the outer, Y for the inner). A prior eval's
    # visual review found rings that could not nest, and the cause was VERTICAL pins - the inner
    # ring swung in-plane like a door instead of tilting - not the shared plane.
    # Each band is dimensioned to a PARAMETER so the resize walks them.
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
    _watch_all(),                       # the three concentric ring bands are drawn
    # THE PIVOT PINS - what turns three concentric bands into a gimbal. Each pair lies IN the ring
    # plane and bridges the gap between one band's OD and its parent's ID, and the two pairs are
    # PERPENDICULAR: X carries the outer ring in the frame, Y carries the inner ring in the outer.
    # Sketched on the plane NORMAL to their own axis (yz for an X pin, xz for a Y pin) and extruded
    # along it, so the pin is a rod on the axis its joint turns about - a pin standing along Z
    # instead lets a ring swing in-plane like a door, which is the failure a prior eval's review
    # traced. Each spans its gap EXACTLY, band face to band face: a pin that reaches INTO the bands
    # is how a graded model seats one, but that needs a bore in each, and undrilled bands would
    # simply read as interference.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PinOuterXpos", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": "OuterOD", "name": "PinOXpPlane"},
     _datum_plane("yz"), None),
    ("sketch_create", {"plane": "PinOXpPlane", "name": "PinOXpS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 3,
                             "sketch_name": "PinOXpS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PinOXpS", "profile_index": 0,
                   "distance": "FrameOpenR - OuterOD"}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PinOuterXneg", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": "-OuterOD", "name": "PinOXnPlane"},
     _datum_plane("yz"), None),
    ("sketch_create", {"plane": "PinOXnPlane", "name": "PinOXnS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 3,
                             "sketch_name": "PinOXnS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PinOXnS", "profile_index": 0,
                   "distance": "-(FrameOpenR - OuterOD)"}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PinInnerYpos", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xz", "offset": "InnerOD", "name": "PinIYpPlane"},
     _datum_plane("xz"), None),
    ("sketch_create", {"plane": "PinIYpPlane", "name": "PinIYpS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 3,
                             "sketch_name": "PinIYpS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PinIYpS", "profile_index": 0,
                   "distance": "OuterBoreR - InnerOD"}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PinInnerYneg", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xz", "offset": "-InnerOD", "name": "PinIYnPlane"},
     _datum_plane("xz"), None),
    ("sketch_create", {"plane": "PinIYnPlane", "name": "PinIYnS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 3,
                             "sketch_name": "PinIYnS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PinIYnS", "profile_index": 0,
                   "distance": "-(OuterBoreR - InnerOD)"}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch(["PinOuterXpos:1", "PinInnerYpos:1"]),
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
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -32, "name": "CarrierPlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "CarrierPlane", "name": "CarrierSketch"}, "ok", None),
    # a rectangle is built BY a SketchLines factory, so its four sides are the delta counted.
    ("sketch_add_geometry", {"kind": "rectangle", "x1": -50, "y1": -8, "x2": 50, "y2": 8, "sketch_name": "CarrierSketch"},
     lambda p: p.get("curves_added") == 4, None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 14, "sketch_name": "CarrierSketch"}, "ok", None),
    # the pedestal base + a smaller top profile on an offset plane, for a base-to-post LOFT -
    # entirely BELOW the carrier (top at z=-32 meets the carrier's underside).
    ("design_activate_component", {"occurrence": "Pedestal:1"}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -60, "name": "PedBasePlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "PedBasePlane", "name": "PedBase"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 15, "sketch_name": "PedBase"}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": -32, "name": "PedTopPlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "PedTopPlane", "name": "PedTop"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 8, "sketch_name": "PedTop"}, "ok", None),
    # the crank: a path + a profile for a swept handle.
    ("design_activate_component", {"occurrence": "Crank:1"}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "CrankPath"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 60, "y1": 0, "x2": 60, "y2": 40, "sketch_name": "CrankPath"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "CrankProf"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 60, "cy": 0, "radius": 4, "sketch_name": "CrankProf"}, "ok", None),
    _watch_all(),                       # the skeleton is complete, crank and pedestal included
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
    # The sketch acts have already drawn the whole scratch field by now, so a whole-model fit is a
    # metre of scenery with the gyroscope a speck in it. Frame the ring sketches the extrudes below
    # consume, and the rings appear inside the shot instead of off the edge of one.
    _watch(["OuterRingSketch", "InnerRingSketch", "FrameSketch"]),
    # ring bands: extrude the ANNULUS profile (smallest-area region) symmetric about the ring plane.
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", ("or_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "OuterRingSketch", "profile_index": _ctx_get(c, "or_ring", "outer ring annulus"), "distance": 4, "symmetric": True}, _extruded, None),
    ("sketch_get", {"sketch_name": "InnerRingSketch"}, "ok", ("ir_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "InnerRingSketch", "profile_index": _ctx_get(c, "ir_ring", "inner ring annulus"), "distance": 4, "symmetric": True}, _extruded, None),
    ("sketch_get", {"sketch_name": "FrameSketch"}, "ok", ("fr_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "FrameSketch", "profile_index": _ctx_get(c, "fr_ring", "frame plate ring"), "distance": 4, "symmetric": True}, _extruded, None),
    # EDGE TREATMENT ON THE RINGS. A gimbal ring is handled, and a band left with four square rims
    # reads as a washer however well it is jointed. Each ring is broken on the rim a hand reaches
    # first: the outer ring's OD, the inner ring's, and the frame's opening - a radius scaled from
    # the band so it stays proportionate when the driver moves, floored well under half the band so
    # it can never eat the section. find_geometry picks the rim by RADIUS, the one query that keeps
    # naming the same edge after the driver changes.
    ("param_add", {"name": "RingBreak", "expression": "RingBand / 6",
                   "comment": "Rim break on a gimbal ring - proportional to the band it cuts"},
     _param_added("RingBreak", 1), None),
    # the rim radii come from the PARAMETERS that drove them, not from the numbers they happen to
    # hold - the same three expressions the rings were built from, so the query cannot go stale.
    ("param_get", {"name": "OuterOD"}, _param_read("OuterOD", 48),
     ("outer_od", lambda p: p["parameter"]["value"])),
    ("param_get", {"name": "InnerOD"}, _param_read("InnerOD", 36),
     ("inner_od", lambda p: p["parameter"]["value"])),
    ("param_get", {"name": "FrameOpenR"}, _param_read("FrameOpenR", 54),
     ("frame_open", lambda p: p["parameter"]["value"])),
    # model_fillet/model_chamfer take a NUMBER, not an expression, so the break is READ from the
    # parameter that defines it rather than written twice - the parameter still states the rule and
    # the feature still gets the value that rule produced.
    ("param_get", {"name": "RingBreak"}, _param_read("RingBreak", 1),
     ("ring_break", lambda p: p["parameter"]["value"])),
    ("find_geometry", lambda c: {"target": "OuterRing", "kind": "circular_edge",
                                 "radius": _ctx_get(c, "outer_od", "outer ring OD"),
                                 "max_results": 2}, "ok", _fgn("or_rim")),
    ("model_fillet", lambda c: {"edges": _ctx_get(c, "or_rim", "outer ring rim"),
                                "radius": _ctx_get(c, "ring_break", "the ring rim break")},
     _filleted, None),
    ("find_geometry", lambda c: {"target": "InnerRing", "kind": "circular_edge",
                                 "radius": _ctx_get(c, "inner_od", "inner ring OD"),
                                 "max_results": 2}, "ok", _fgn("ir_rim")),
    ("model_fillet", lambda c: {"edges": _ctx_get(c, "ir_rim", "inner ring rim"),
                                "radius": _ctx_get(c, "ring_break", "the ring rim break")},
     _filleted, None),
    ("find_geometry", lambda c: {"target": "Frame", "kind": "circular_edge",
                                 "radius": _ctx_get(c, "frame_open", "frame opening"),
                                 "max_results": 2}, "ok", _fgn("fr_rim")),
    ("model_chamfer", lambda c: {"edges": _ctx_get(c, "fr_rim", "frame opening rim"),
                                 "distance": _ctx_get(c, "ring_break", "the ring rim break")},
     _chamfered, None),
    # the rotor disc, REVOLVED about the spin axis; the shaft and carrier extruded.
    ("model_revolve", {"sketch_name": "RotorSketch", "profile_index": 0, "axis": "x", "angle_deg": 360},
     _revolved, None),
    # symmetric extrudes 'distance' EACH WAY, so this is a 56 mm shaft reaching |x| = 28. It must
    # stop SHORT of InnerBoreR (30): a 3 mm-radius shaft ending exactly on the bore reaches
    # sqrt(30^2 + 3^2) at its corners and bites 2.1 mm3 into the inner ring - a journal that
    # interferes with the ring it is meant to turn inside.
    ("model_extrude", {"sketch_name": "ShaftSketch", "profile_index": 0, "distance": 28, "symmetric": True},
     _extruded, None),
    # the carrier: bar + hub in one extrude (all regions), then the machinable detail cut
    # through - a 5mm center bore, a 6x 3mm bolt circle on R9, and 4mm pivot bores at the ends.
    ("design_activate_component", {"occurrence": "Carrier:1"}, "ok", None),
    ("model_extrude", {"sketch_name": "CarrierSketch", "profile_index": "all", "distance": 6},
     _extruded, None),
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
    ("model_extrude", {"sketch_name": "CarrierHoles", "profile_index": "all", "distance": 6, "operation": "cut"},
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the pedestal base-to-post transition, LOFTED between the two profiles - built INTO the
    # Pedestal component (the loft lands in the ACTIVE component, not the profiles' owner).
    ("design_activate_component", {"occurrence": "Pedestal:1"}, "ok", None),
    ("sketch_get", {"sketch_name": "PedBase"}, "ok", ("ped_base", lambda p: p["profiles"][0]["handle"])),
    ("sketch_get", {"sketch_name": "PedTop"}, "ok", ("ped_top", lambda p: p["profiles"][0]["handle"])),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "ped_base", "pedestal base"), _ctx_get(c, "ped_top", "pedestal top")]}, _lofted, None),
    # the crank handle, SWEPT along its path - into the Crank component for the same reason.
    ("design_activate_component", {"occurrence": "Crank:1"}, "ok", None),
    ("model_sweep", {"profile": {"sketch": "CrankProf", "profile_index": 0}, "path": "sketch:CrankPath"},
     _swept, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the machine is whole: frame IT for the colouring beats, not the metre-wide cameo grid.
    _watch("Frame:1"),
    # EACH PART ITS OWN COLOR - the recording's signature look - and a physical material on the rotor.
    ("appearance_set", {"target": "Frame", "color": "#5E6AD2"}, "ok", None),
    ("appearance_set", {"target": "Pedestal", "color": "#8A94A6"}, "ok", None),
    ("appearance_set", {"target": "Carrier", "color": "#1E88E5"}, "ok", None),
    ("appearance_set", {"target": "OuterRing", "color": "#E5533C"}, "ok", None),
    ("appearance_set", {"target": "InnerRing", "color": "#F5A623"}, "ok", None),
    ("appearance_set", {"target": "Rotor:1", "color": "#2FB170"}, "ok", None),
    ("appearance_set", {"target": "RotorShaft:1", "color": "#B0BEC5"}, "ok", None),
    ("appearance_set", {"target": "Crank:1", "color": "#9C27B0"}, "ok", None),
    ("model_set_material", {"target": "Rotor:1", "material": "Steel"}, _material_assigned, None),
    # honest reads on the real mechanism: ring-to-ring gap, rotor/shaft coaxiality, rotor volume.
    ("find_geometry", {"target": "OuterRing", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("or_cyl")),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "or_cyl", "outer ring face"), "b": "InnerRing"}, _gap_measured, None),
    ("find_geometry", {"target": "Rotor:1", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("rotor_cyl")),
    ("find_geometry", {"target": "RotorShaft", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("shaft_cyl")),
    ("model_measure_relation", lambda c: {"relation": "coaxial", "entity_a": _ctx_get(c, "rotor_cyl", "rotor face"), "entity_b": _ctx_get(c, "shaft_cyl", "shaft face")}, _relation_measured("coaxial"), None),
    ("model_inspect", {"target": "Rotor:1"}, _extent_measured, None),
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
    # THE DATUM BENCH: one bored block, and every way the API knows of hanging a plane, an axis or a
    # point off it. The modes divide by what they READ, so the bench has to carry all of it - six
    # faces, the linear edges where they meet, the vertices where those meet, and a bore for the
    # curved-face and circular-edge modes. Each row's receipt is the created datum's OWN geometry:
    # every mode returns an object, and only the read-back distinguishes one that landed where the
    # mode says from one that landed anywhere at all.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "DatumBench", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "DBPad"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 0, "x2": 260, "y2": 40,
                             "sketch_name": "DBPad"}, "ok", None),
    ("model_extrude", {"sketch_name": "DBPad", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_top")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "db_top", "bench top face"), "hole_type": "simple",
                              "diameter": "10 mm", "extent": "through", "points": [[230, 20, 0]]},
     _drilled(1), None),
    # the reference set every mode below draws from, acquired once the bore exists so no handle is
    # stale: the top face, the bore, its rim, two coplanar top edges, one vertical edge that MEETS
    # one of them, and three top-face corners that form a triangle.
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_top2")),
    ("find_geometry", {"target": "DatumBench", "kind": "cylinder_face", "nearest_to": [230, 20, 10],
                       "max_results": 1}, "ok", _fg("db_bore")),
    ("find_geometry", {"target": "DatumBench", "kind": "circular_edge", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_rim")),
    ("find_geometry", {"target": "DatumBench", "kind": "line_edge", "nearest_to": [230, 0, 20],
                       "max_results": 1}, "ok", _fg("db_front")),
    ("find_geometry", {"target": "DatumBench", "kind": "line_edge", "nearest_to": [230, 40, 20],
                       "max_results": 1}, "ok", _fg("db_back")),
    ("find_geometry", {"target": "DatumBench", "kind": "line_edge", "nearest_to": [200, 0, 10],
                       "max_results": 1}, "ok", _fg("db_post")),
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [200, 0, 20],
                       "max_results": 1}, "ok", _fg("db_c1")),
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [260, 0, 20],
                       "max_results": 1}, "ok", _fg("db_c2")),
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [200, 40, 20],
                       "max_results": 1}, "ok", _fg("db_c3")),
    # the bore's seam vertex - a point that sits ON the cylindrical face, which is what a tangent
    # plane needs. A box corner is not on the bore, and the tangency has nowhere to land.
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_seam")),
    # PLANES. at_angle swings the XY plane about a top edge; three_points spans the three corners;
    # midplane splits XY and the top face, landing halfway up the block; two_edges spans the two
    # coplanar top edges; tangent_at_point rests on the bore wall.
    ("model_construction", lambda c: {"kind": "plane", "mode": "at_angle", "plane": "xy",
                                      "edges": [_ctx_get(c, "db_front", "bench front top edge")],
                                      "angle": 30, "name": "DBAngle"},
     lambda p: _datum("plane")(p) and _measured("swung off the base plane's normal",
                                                {"normal_changed": p.get("normal_changed")},
                                                p.get("normal_changed") is True), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "three_points",
                                      "points": [_ctx_get(c, "db_c1", "bench corner 1"),
                                                 _ctx_get(c, "db_c2", "bench corner 2"),
                                                 _ctx_get(c, "db_c3", "bench corner 3")],
                                      "name": "DBTri"}, _datum("plane"), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "midplane", "plane": "xy",
                                      "plane2": _ctx_get(c, "db_top2", "bench top face"),
                                      "name": "DBMid"}, _datum("plane"), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "two_edges",
                                      "edges": [_ctx_get(c, "db_front", "bench front top edge"),
                                                _ctx_get(c, "db_back", "bench back top edge")],
                                      "name": "DBSpan"}, _datum("plane"), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "tangent_at_point",
                                      "face": _ctx_get(c, "db_bore", "bench bore"),
                                      "points": [_ctx_get(c, "db_seam", "bench bore seam vertex")],
                                      "name": "DBTangent"}, _datum("plane"), None),
    # AXES. An edge IS an axis; two corners span one; the bore's own centreline; and the normal of
    # the top face taken at a corner sitting on it.
    ("model_construction", lambda c: {"kind": "axis", "mode": "edge",
                                      "axis": _ctx_get(c, "db_front", "bench front top edge"),
                                      "name": "DBEdgeAxis"}, _datum("axis"), None),
    ("model_construction", lambda c: {"kind": "axis", "mode": "two_points",
                                      "points": [_ctx_get(c, "db_c1", "bench corner 1"),
                                                 _ctx_get(c, "db_c3", "bench corner 3")],
                                      "name": "DBSpanAxis"}, _datum("axis"), None),
    ("model_construction", lambda c: {"kind": "axis", "mode": "perpendicular_at_point",
                                      "face": _ctx_get(c, "db_top2", "bench top face"),
                                      "points": [_ctx_get(c, "db_c2", "bench corner 2")],
                                      "name": "DBNormalAxis"},
     lambda p: _datum("axis")(p) and _measured("axis along the face normal",
                                               {"aligned": p.get("aligned_to_face_normal")},
                                               p.get("aligned_to_face_normal") is True), None),
    # a world axis through a coordinate is setByLine, which is DIRECT-edit-only - this design is
    # parametric, so the mode is refused up front with the parametric routes named.
    ("model_construction", {"kind": "axis", "mode": "world", "axis": "x", "x": 200, "y": 0, "z": 0,
                            "name": "DBWorldAxis"}, "refused", None),
    # POINTS. The bore rim's centre; the corner where a top edge meets the post below it; the origin
    # the three world planes share; and where the post pierces XY.
    ("model_construction", lambda c: {"kind": "point", "mode": "circle_center",
                                      "edges": [_ctx_get(c, "db_rim", "bench bore rim")],
                                      "name": "DBBoreCentre"}, _datum("point"), None),
    ("model_construction", lambda c: {"kind": "point", "mode": "two_edges",
                                      "edges": [_ctx_get(c, "db_front", "bench front top edge"),
                                                _ctx_get(c, "db_post", "bench corner post")],
                                      "name": "DBCorner"}, _datum("point"), None),
    ("model_construction", {"kind": "point", "mode": "three_planes", "plane": "xy", "plane2": "xz",
                            "plane3": "yz", "name": "DBOrigin"}, _datum("point"), None),
    ("model_construction", lambda c: {"kind": "point", "mode": "edge_plane",
                                      "edges": [_ctx_get(c, "db_post", "bench corner post")],
                                      "plane": "xy", "name": "DBFoot"}, _datum("point"), None),
    # the coordinate point is setByPoint - direct-edit-only for the same reason as the world axis.
    ("model_construction", {"kind": "point", "mode": "coordinate", "x": 230, "y": 20, "z": 40,
                            "name": "DBCoord"}, "refused", None),
    # THE RELATION VOCABULARY, on the same block. Each relation reports a DIFFERENT measurement -
    # an angle for the alignments, a distance for the fits - and the pairs here are chosen so the
    # geometry, not the tool, settles the verdict: a face is flush with itself, a bore concentric
    # with itself, the top face perpendicular to a wall it meets and touching it along that edge,
    # and 20 mm clear of the floor below it.
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [200, 20, 10],
                       "max_results": 1}, "ok", _fg("db_wall")),
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [230, 20, 0],
                       "max_results": 1}, "ok", _fg("db_floor")),
    ("model_measure_relation", lambda c: {"relation": "perpendicular",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_wall", "bench wall")},
     _relation_passes("perpendicular"), None),
    ("model_measure_relation", lambda c: {"relation": "flush",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_top2", "bench top face")},
     _relation_read("flush", "normal_angle_deg"), None),
    ("model_measure_relation", lambda c: {"relation": "concentric",
                                          "entity_a": _ctx_get(c, "db_bore", "bench bore"),
                                          "entity_b": _ctx_get(c, "db_bore", "bench bore")},
     _relation_read("concentric", "center_distance"), None),
    ("model_measure_relation", lambda c: {"relation": "touching",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_wall", "bench wall")},
     _relation_read("touching", "min_distance"), None),
    ("model_measure_relation", lambda c: {"relation": "clearance",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_floor", "bench floor")},
     _relation_read("clearance", "min_distance"), None),
    # feature cameos on same-doc scratch bodies (no single natural gyroscope home for these verbs).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "FeatureCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "FCPad"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 0, "x2": 240, "y2": 40, "sketch_name": "FCPad"}, "ok", None),
    ("model_extrude", {"sketch_name": "FCPad", "profile_index": 0, "distance": 20}, _extruded, None),
    _watch("FeatureCameo:1"),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [200, 20, 10], "max_results": 1}, "ok", _fg("fc_side")),
    ("model_draft", lambda c: {"faces": [_ctx_get(c, "fc_side", "cameo side face")], "pull_direction": "xy", "angle_deg": 3},
     _drafted, None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_top")),
    # hole points are in the FACE'S LOCAL frame, whose origin for this face is the MODEL origin
    # projected onto its plane - so on-pad coordinates are the world x,y. ([5,5] here drilled at
    # world (5,5), a point off this pad entirely: the hole silently cut whatever body sat near
    # the origin, and the axis-count read-back cannot see a wrong-body cut. Measured live.)
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[220, 20, 0]]}, _drilled(1), None),
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
                              "extent": "blind", "depth": "3 mm"}, _drilled(1), None),
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
    ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "plane": "yz"}, _mirrored, None),
    # a real GRID, not a row: two directions at once, spaced wider than the 40 mm pad so the
    # instances stand clear of each other. 3 x 2 is also the read-back that catches a tool
    # multiplying the directions wrongly - a row of 3 and a row of 6 both pass a bare "more than 1".
    ("model_pattern_rectangular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                             "quantity_one": 3, "spacing_one": 75, "direction_one": "x",
                                             "quantity_two": 2, "spacing_two": 70, "direction_two": "y"},
     _patterned("total_instances", 6), None),
    # An axis OUTSIDE the part, so the pattern reads as an orbit rather than a body spun in place:
    # two origin planes offset to cross 30 mm clear of the pad's -X edge, and their INTERSECTION is
    # the axis. The world z axis would do the same job 200 mm away, swinging the copies across the
    # whole scene and through the gyroscope.
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": 170, "name": "OrbitYZ"},
     _datum_plane("yz"), None),
    ("model_construction", {"kind": "plane", "plane": "xz", "offset": 20, "name": "OrbitXZ"},
     _datum_plane("xz"), None),
    ("model_construction", {"kind": "axis", "mode": "two_planes", "plane": "OrbitYZ",
                            "plane2": "OrbitXZ", "name": "CameoOrbit"},
     lambda p: bool(p.get("handle")), None),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 4, "total_angle_deg": 360,
                                          "axis": "CameoOrbit"},
     lambda p: p.get("quantity") == 4 and p.get("axis") == "CameoOrbit", None),
    # pattern the cameo along one of its own line edges - the count is a read-back, never an echo.
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "max_results": 1}, "ok", _fg("dc_edge")),
    ("model_pattern_path", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "path": [_ctx_get(c, "dc_edge", "path edge")], "quantity": 3, "distance": 55, "distance_type": "spacing"},
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
    ("model_create_component", {"name": "CombineCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "CC1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 100, "x2": 230, "y2": 130, "sketch_name": "CC1"}, "ok", None),
    ("model_extrude", {"sketch_name": "CC1", "profile_index": 0, "distance": 10}, _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "CC2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 220, "y1": 120, "x2": 250, "y2": 150, "sketch_name": "CC2"}, "ok", None),
    ("model_extrude", {"sketch_name": "CC2", "profile_index": 0, "distance": 10}, _extruded, None),
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
    ("model_combine", lambda c: {"target": _ctx_get(c, "cc_a", "combine target"), "tools": [_ctx_get(c, "cc_b", "combine tool")], "operation": "join"}, _joined, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the revolve axis as a CYLINDRICAL FACE, off the origin - the case a world key cannot express.
    # A face mapped to a direction VECTOR keeps the direction and DROPS the location, so the ring is
    # turned about the world axis through the ORIGIN and reported as success: the label is checked
    # AND the geometry measured. Live: a cylinder at x=30, a 2x3 mm profile at x 36-38 on the XZ
    # plane, ring bbox x 22..38. The cameo sits at z=100, clear of the gyroscope and the other cameos.
    ("model_create_component", {"name": "RevolveCameo", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 100, "name": "RevAxisPlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "RevAxisPlane", "name": "RevAxisS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 30, "cy": 0, "radius": 5, "sketch_name": "RevAxisS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RevAxisS", "profile_index": 0, "distance": 20}, _extruded, None),
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
    ("model_create_component", {"name": "BayCameo", "activate": True}, _made_component, None),
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
    ("assembly_ground", {"occurrence": "Frame:1", "ground_to_parent": True}, _grounded, None),
    ("assembly_rigid_group", {"occurrences": ["Frame:1", "Carrier:1"]}, _rigid_grouped(2), None),
    # a coordinate Joint Origin the crank mounts on, and the stock-center JO CAM binds its WCS to.
    ("joint_create_origin", {"anchor": "coordinates", "x": 60, "y": 0, "z": 0, "name": "CrankMount"},
     _joint_origin_at("CrankMount", 60, 0, 0), None),
    ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "StockCenter"},
     _joint_origin_at("StockCenter", 0, 0, 0), None),
    # the four gimbal revolutes, each origin-snapped so parts stay seated.
    ("joint_create", {"occurrence_one": "Carrier:1:origin", "occurrence_two": "Pedestal:1:origin", "joint_type": "revolute", "axis": "z", "name": "Yaw"}, _jointed("Yaw"), None),
    ("joint_create", {"occurrence_one": "OuterRing:1:origin", "occurrence_two": "Carrier:1:origin", "joint_type": "revolute", "axis": "x", "name": "PivotOuter"}, _jointed("PivotOuter"), None),
    # the OTHER ring pivot via a joint origin snap on the inner ring (the second creation path).
    ("joint_create", {"occurrence_one": "InnerRing:1:origin", "occurrence_two": "OuterRing:1:origin", "joint_type": "revolute", "axis": "y", "name": "PivotInner"}, _jointed("PivotInner"), None),
    ("joint_create", {"occurrence_one": "Rotor:1:origin", "occurrence_two": "RotorShaft:1:origin", "joint_type": "revolute", "axis": "x", "name": "Spin"}, _jointed("Spin"), None),
    # the shaft rides in the inner ring as-built (its current seated position).
    ("joint_create_as_built", {"occurrence_one": "RotorShaft:1", "occurrence_two": "InnerRing:1"}, _as_built, None),
    # the crank on its frame mount, then LIMITS on the yaw.
    ("joint_create", {"occurrence_one": "Crank:1:origin", "occurrence_two": "CrankMount", "joint_type": "revolute", "axis": "z", "name": "CrankAxis"}, _jointed("CrankAxis"), None),
    ("joint_edit", {"joint_name": "Yaw", "min_deg": -45, "max_deg": 45},
     _joint_limits("Yaw", min_deg=-45, max_deg=45), None),
    # a SECOND creation path AND a real cylinder-face joint on a scratch pin/bore cameo pair.
    ("model_create_component", {"name": "PinCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PinS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 5, "sketch_name": "PinS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PinS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("model_create_component", {"name": "BoreCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "BoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 8, "sketch_name": "BoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 5.5, "sketch_name": "BoreS"}, "ok", None),
    ("sketch_get", {"sketch_name": "BoreS"}, "ok", ("bore_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "BoreS", "profile_index": _ctx_get(c, "bore_ring", "bore ring"), "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("BoreCameo:1"),
    ("find_geometry", {"target": "PinCameo", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("pin_cyl")),
    ("find_geometry", {"target": "BoreCameo", "kind": "cylinder_face", "radius": 5.5, "max_results": 1}, "ok", _fg("bore_cyl")),
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "pin_cyl", "pin face"), "handle_two": _ctx_get(c, "bore_cyl", "bore face"), "motion": "revolute"}, _jointed_at_geometry, None),
    # THE MOTION VOCABULARY at the geometry seam - a ball on a real SPHERE face, an explicit frame
    # axis, and rigid. Each beat takes its own free cameo pair, chained as a TREE (sphere - post -
    # post), so no beat closes a loop on another's joint. The sphere is built through the surface
    # family the same way the fill cameo builds one: a half-disc arc revolved into a closed sheet and
    # sealed solid, which is what gives this beat a genuine SphereSurfaceType face to joint at (a
    # sphere face takes ONLY CenterKeyPoint - the rule inside _joints.build_joint_geometry).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "BallSphere", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "BallProf"}, "ok", None),
    # the arc's endpoints must sit ON the revolve axis (x=0) or the revolved surface is an open tube
    # enclosing nothing; on an xz sketch +Y maps to world -Z, so this sphere sits alone at z=+300.
    ("sketch_add_geometry", {"kind": "arc", "cx": 0, "cy": -300, "x1": 0, "y1": -294,
                             "sweep_deg": 180, "sketch_name": "BallProf"}, "ok", None),
    ("surface_revolve", {"sketch_name": "BallProf", "axis": "z", "angle_deg": 360}, "ok", None),
    ("surface_fill", {"tools": ["BallSphere"], "operation": "new"},
     lambda p: p.get("all_solid") is True, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "BallPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "BallPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 0, "radius": 5,
                             "sketch_name": "BallPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "BallPostS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AxisPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "AxisPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 60, "radius": 5,
                             "sketch_name": "AxisPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AxisPostS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "RigidPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RigidPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 120, "radius": 5,
                             "sketch_name": "RigidPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RigidPostS", "profile_index": 0, "distance": 20}, _extruded, None),
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
    ("model_create_component", {"name": "TorusRing", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "TorusProf"}, "ok", None),
    # on an xz sketch +Y maps to world -Z: this circle sits at world (40, 0, 400) and revolving it
    # about z sweeps a torus of major radius 40 centred on the z axis at z=400, alone up there.
    ("sketch_add_geometry", {"kind": "circle", "cx": 40, "cy": -400, "radius": 6,
                             "sketch_name": "TorusProf"}, "ok", None),
    ("model_revolve", {"sketch_name": "TorusProf", "profile_index": 0, "axis": "z",
                       "angle_deg": 360}, _revolved, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TorusPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TorusPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 180, "radius": 5,
                             "sketch_name": "TorusPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TorusPostS", "profile_index": 0, "distance": 20}, _extruded, None),
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
    ("model_create_component", {"name": "AsbPin", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "AsbPinS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 800, "y1": 300, "x2": 820, "y2": 320,
                             "sketch_name": "AsbPinS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AsbPinS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AsbPlate", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 20, "name": "AsbSeat"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "AsbSeat", "name": "AsbPlateS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 790, "y1": 290, "x2": 830, "y2": 330,
                             "sketch_name": "AsbPlateS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AsbPlateS", "profile_index": 0, "distance": 10}, _extruded, None),
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
                               "angle_deg": 0}, _driven_angle(0), None),
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
    # THE JOINT BENCH: one grounded base, a STATION for every motion type spaced along it, and a
    # flag-shaped indicator arm at each. The arm shape is the point - a disc turning about its own
    # axis shows nothing, so every station carries a bar whose far end reads its position at a
    # glance, and the stations are spread along the base so the seven motions stand side by side
    # instead of on top of each other. Each arm is drawn OFF the base and its joint carries it to
    # its station, so the mate itself is visible; the drive pass below then moves the ones that
    # have a degree of freedom, which is the only way a motion type can be told from a label.
    ("model_create_component", {"name": "JointBase", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "JBaseS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 860, "y1": 300, "x2": 1190, "y2": 340,
                             "sketch_name": "JBaseS"}, "ok", None),
    ("model_extrude", {"sketch_name": "JBaseS", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("appearance_set", {"target": "JointBase", "color": "#37474F"}, "ok", None),
    # GROUNDED TO PARENT - the base is the fixed frame every station's motion is read against. An
    # arm that moved because the base drifted would read as the joint working.
    ("assembly_ground", {"occurrence": "JointBase:1", "ground_to_parent": True}, _grounded, None),
] + _joint_bench() + [
    # THE RETYPE, on a station that is already visible: one joint walked through every motion the
    # tool carries, each retype witnessed by the design's own joint walk rather than by the writer,
    # which publishes the type it was ASKED for. It ends back on the revolute it started as.
    ("joint_edit", {"joint_name": "JRig", "joint_type": "revolute", "axis": "z"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "revolute"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "slider", "axis": "x"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "slider"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "cylindrical", "axis": "z"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "cylindrical"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "planar", "axis": "z"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "planar"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "ball"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "ball"), None),
    # pin_slot alone takes TWO frame directions - it rotates about one and slides along another, so
    # the pair must differ.
    ("joint_edit", {"joint_name": "JRig", "joint_type": "pin_slot", "axis": "z",
                    "slide_axis": "y"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "pin_slot"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "pin_slot", "axis": "y",
                    "slide_axis": "y"}, "refused", None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "rigid"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "rigid"), None),
    # COUPLE the crank to the rotor spin at ratio 2 - the DOF-fix step - across independent chains.
    ("joint_motion_link", {"joint_one": "CrankAxis", "joint_two": "Spin", "ratio": 2},
     _motion_linked("CrankAxis", "Spin", False), None),
    # every joint built above, counted off the design-wide walk by an independent read.
    ("assembly_get", {}, _joints_listed(10), None),
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
    # back to the 2:1 the story drives on, read back the same way the re-value above was.
    ("assembly_edit_relations", lambda c: {"kind": "motion_link", "name": _ctx_get(c, "rel_names", "relation names")["ml"], "action": "set_values", "ratio": 2},
     lambda p: p.get("ratio") == 2.0 and "was_reversed" in p, None),
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
    _watch("Frame:1"),
    # DRIVE THE AXES ON CAMERA: yaw proves the no-take gate; both ring pivots and the crank ->
    # rotor 2:1 drive for real. Yaw CANNOT move in this scene - Carrier:1 rides the rigid group
    # with Frame:1 and the chain closes through Pedestal:1, so the solver holds it at 0 (measured:
    # it stays 0 even with Frame:1's parent lock released) - so its drive must REFUSE as a
    # no-take.
    ("joint_drive", {"joint_name": "Yaw", "angle_deg": 30}, "refused", None),
    ("joint_drive", {"joint_name": "PivotOuter", "angle_deg": 20}, _driven_angle(20), None),
    ("joint_drive", {"joint_name": "PivotInner", "angle_deg": 25}, _driven_angle(25), None),
    ("joint_drive", {"joint_name": "CrankAxis", "angle_deg": 30}, _driven_angle(30), None),
    # CrankAxis (a link member) is now in the session drive registry, so driving its partner Spin
    # must REFUSE (the second-member guard). This is also the live proof that
    # MotionLink.jointOne/jointTwo resolve: a wrong property name would leave the partner lookup
    # blind and this drive would wrongly succeed, failing the row.
    ("joint_drive", {"joint_name": "Spin", "angle_deg": 5}, "refused", None),
    # the driven pose read back off the JOINTS themselves - the three drives above reported their
    # own value_now, and this is the second witness to the same three. Spin (the crank's 2:1 link
    # partner) is REPORTED in the ledger line, not asserted: this run is the first read of what the
    # link leaves in the partner's stored value.
    ("assembly_get", {},
     _joints_listed(10, {"PivotOuter": 20, "PivotInner": 25, "CrankAxis": 30, "Yaw": 0}), None),
    ("assembly_inspect_interference", {}, _interference_measured, None),   # driven pose
    ("joint_drive", {"joint_name": "Yaw", "angle_deg": 0}, _driven_angle(0), None),
    ("joint_drive", {"joint_name": "PivotOuter", "angle_deg": 0}, _driven_angle(0), None),
    ("joint_drive", {"joint_name": "PivotInner", "angle_deg": 0}, _driven_angle(0), None),
    ("joint_drive", {"joint_name": "CrankAxis", "angle_deg": 0}, _driven_angle(0), None),
    ("assembly_inspect_interference", {}, _interference_measured, None),   # rest pose
    # pose + constrain cameos (do not disturb the jointed mechanism).
    ("model_create_component", {"name": "PoseCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PoseS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 300, "y1": 100, "x2": 320, "y2": 120, "sketch_name": "PoseS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PoseS", "profile_index": 0, "distance": 10}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the cameo's first move, off a fresh occurrence still at the identity transform - so the
    # position read back off transform2 is the 40 mm this call composed onto it.
    ("assembly_move", {"occurrence": "PoseCameo:1", "dx": 40}, _moved_occurrence(40), None),
    # the pending pose is transient until captured: status sees it armed with no captured markers
    # yet (this document has captured none), and discard_pending throws it away - the pending flag
    # clears while the captured-marker collection is left exactly as it was.
    ("assembly_capture_position", {"action": "status"},
     lambda p: p.get("has_pending") is True and p.get("snapshot_count") == 0, None),
    ("assembly_capture_position", {"action": "discard_pending"},
     lambda p: p.get("discarded") is True and p.get("has_pending") is False
     and p.get("snapshot_count") == 0, None),
    # re-arm the move the capture below records - the discard consumed the first one. Where the
    # discard left the part is what this run measures, so only the read-back itself is asserted.
    ("assembly_move", {"occurrence": "PoseCameo:1", "dx": 40}, _moved_occurrence(), None),
    ("assembly_capture_position", {"action": "capture"}, _captured, None),
]
_MOTION += _box("ConA", ox=360, tint="#E5533C") + _box("ConB", ox=360, tint="#1E88E5", shape="disc") + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_constrain", {"snap_one": "ConA:1:bottom", "snap_two": "ConB:1:top", "flipped": True}, _constrained, None),
    ("design_recompute", {}, "ok", None),
] + _box("MateSeat", ox=460, tint="#6A1B9A") + _box("MateArm", ox=520, tint="#FDD835", shape="bar") + [
    # ONE CONSTRAINT, SEVERAL RELATIONSHIPS - the table in Fusion's own Constrain Components dialog,
    # where a single constraint FEATURE carries a row per geometry pair, each row with its own type,
    # offset and angle. That is how Fusion actually locates a part: a set solved TOGETHER, because one
    # face pair almost never fixes anything. The row above is the single-pair shorthand; this is the
    # set form.
    # The two rows take DIFFERENT freedoms, which is what makes the set solvable: a SEAT (the arm's
    # underside onto the seat block's top face, 2 mm proud, flipped so the two faces oppose) and a
    # TURN about it (30 deg between the two front faces). Two rows reaching for the SAME freedom
    # over-constrain instead - measured on a live document: a face-to-face mate plus a concentric on
    # one pair of discs computes with a WARNING, with and without the offset, and the tool refuses
    # rather than leave a warned constraint in the design.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_constrain", {"relationships": [
        {"snap_one": "MateArm:1:bottom", "snap_two": "MateSeat:1:top", "flip": True, "offset": 2},
        {"snap_one": "MateArm:1:front", "snap_two": "MateSeat:1:front", "angle_deg": 30},
    ]},
     # BOTH ROWS IN ONE NUMBER. The count is read off the CREATED constraint, never echoed - a
     # constraint holding FEWER rows than were submitted is refused by the tool - and the rotation the
     # tool measures for itself is 180 - 30: the seat row's flip and the turn row's angle composed.
     # Neither row on its own produces 150.
     lambda p: _constrained(p) and _measured(
         "both relationship rows landed in ONE constraint, and both acted",
         {"relationship_count": p.get("relationship_count"),
          "relationships_submitted": p.get("relationships_submitted"),
          "moved": p.get("moved")},
         p.get("relationships_submitted") == 2 and p.get("relationship_count") >= 2
         and any(_near(m.get("rotation_deg"), 150.0, 0.5)
                 for m in (p.get("moved") or []))), None),
    ("design_recompute", {}, "ok", None),
    # THE TWO ROWS PROVEN BY THEIR EFFECTS, which is the only honest way to tell them apart: no read
    # publishes a per-row TYPE (the dialog's Type column has no counterpart on the wire), so a count
    # of two says two rows landed and nothing about what each one did. The arm's own bounding box
    # says both. Its underside sits 2 mm above the seat block's 10 mm top face - the seat row, read on
    # Z, the one axis the layout pass never moves - and a 34 x 10 mm bar turned 30 deg measures
    # 34.45 x 25.66 across the world axes, which is the turn row and nothing else.
    ("model_inspect", {"target": "MateArm:1"},
     lambda p: _measured("the seat row holds the arm 2 mm proud of a 10 mm block, the turn row has "
                         "it 30 deg off the world axes",
                         {"min_z": (p.get("min_point") or {}).get("z"),
                          "x": p.get("x"), "y": p.get("y")},
                         _near((p.get("min_point") or {}).get("z"), 12.0, 0.05)
                         and _near(p.get("x"), 34.445, 0.05)
                         and _near(p.get("y"), 25.660, 0.05)), None),
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
# The sketch TOOLS, as their own act: every sketch-tool beat that needs no solid, so the run
# draws before it builds. What is missing from here is only what cannot be sketched in an empty
# document - a dimension MEASURED to a model face, a text path REFUSED a model edge - and those
# few beats stay in _DETAILS, next to the bodies they read.
_SKETCHWORK = [
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
     lambda p: any(abs((e.get("start") or {}).get("x", 0) - _px("XformSrc", 700)) < 0.01
                   for e in (p.get("entities") or []) if e.get("type") == "line"), None),
    # move the ORIGINAL: line:0 runs 600->650 at y=500 and must land at 620->670, y=530.
    ("sketch_move", {"sketch_name": "XformSrc", "entities": "line:0", "dx": 20, "dy": 30},
     lambda p: p.get("moved_entities") == ["line:0"] and not p.get("unmoved_entities"), None),
    ("sketch_get", {"sketch_name": "XformSrc", "include_entities": True},
     lambda p: any(abs((e.get("start") or {}).get("x", 0) - _px("XformSrc", 620)) < 0.01
                   and abs((e.get("start") or {}).get("y", 0) - _py("XformSrc", 530)) < 0.01
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
    ("model_create_component", {"name": "CopyComp", "activate": True}, _made_component, None),
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
    # THE CONSTRAINT BENCH - one scratch sketch carrying a spread of constraint kinds, several of
    # them taking TWO operands, each verified by the sketch's own constrained-state read rather than
    # by the call returning ok. Geometric constraints are what make a sketch a MODEL rather than a
    # picture, and a surface that only ever ran 'auto' has never shown that.
    ("sketch_create", {"plane": "xy", "name": "ConBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 480, "x2": 680, "y2": 486,
                             "sketch_name": "ConBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 500, "x2": 676, "y2": 512,
                             "sketch_name": "ConBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 700, "y1": 480, "x2": 706, "y2": 530,
                             "sketch_name": "ConBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 640, "cy": 545, "radius": 14,
                             "sketch_name": "ConBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 676, "cy": 545, "radius": 9,
                             "sketch_name": "ConBench"}, "ok", None),
    # HORIZONTAL takes one operand; the rest below each take two.
    ("sketch_constrain", {"constraint": "horizontal", "entity_one": "line:0",
                          "sketch_name": "ConBench"},
     lambda p: p.get("applied") == "horizontal", None),
    ("sketch_constrain", {"constraint": "parallel", "entity_one": "line:1",
                          "entity_two": "line:0", "sketch_name": "ConBench"},
     lambda p: p.get("applied") == "parallel", None),
    ("sketch_constrain", {"constraint": "perpendicular", "entity_one": "line:2",
                          "entity_two": "line:0", "sketch_name": "ConBench"},
     lambda p: p.get("applied") == "perpendicular", None),
    ("sketch_constrain", {"constraint": "equal", "entity_one": "circle:0",
                          "entity_two": "circle:1", "sketch_name": "ConBench"},
     lambda p: p.get("applied") == "equal", None),
    # SYMMETRY takes three: the pair, and the line they mirror across.
    ("sketch_constrain", {"constraint": "symmetry", "entity_one": "circle:0",
                          "entity_two": "circle:1", "symmetry_line": "line:2",
                          "sketch_name": "ConBench"},
     lambda p: p.get("applied") == "symmetry" and p.get("symmetry_line") == "line:2", None),
    # MIDPOINT puts a line's own end at the middle of another - a point-and-curve pair.
    ("sketch_constrain", {"constraint": "midpoint", "entity_one": "line:1:start",
                          "entity_two": "line:0", "sketch_name": "ConBench"},
     lambda p: p.get("applied") == "midpoint", None),
    # the state read is the verdict: the sketch is MORE constrained than it was, and the tool's
    # own count of what it added is what says so - a call that returned ok and added nothing is a
    # silent no-op, which is the failure this beat exists to catch.
    ("sketch_get", {"sketch_name": "ConBench"},
     lambda p: p.get("constraint_count", 0) >= 6, None),
    # a constraint the geometry cannot satisfy is REFUSED by name, not silently dropped.
    ("sketch_constrain", {"constraint": "tangent", "entity_one": "line:0",
                          "entity_two": "line:1", "sketch_name": "ConBench"}, "refused", None),
    # THE SECOND BENCH: the constraint kinds the first has no geometry for. Deliberately its own
    # sketch - every curve on ConBench is already pinned by the constraints above, and stacking more
    # onto them refuses as over-constrained instead of proving anything. Two ordering facts drive
    # the refs: a fresh sketch owns its ORIGIN as point:0, so the first drawn point is point:1; and
    # the kinds that CREATE curves run in sketches of their own, below, because a new curve renumbers
    # every ref written after it.
    ("sketch_create", {"plane": "xy", "name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 860, "y1": 480, "x2": 930, "y2": 486,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 945, "y1": 489, "x2": 1000, "y2": 495,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 860, "y1": 505, "x2": 866, "y2": 560,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 880, "y1": 505, "x2": 920, "y2": 511,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 880, "y1": 590, "x2": 920, "y2": 602,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 940, "y1": 505, "x2": 980, "y2": 545,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 880, "cy": 660, "radius": 20,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 884, "cy": 664, "radius": 10,
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "spline", "points": [[920, 602], [950, 616], [980, 600]],
                             "sketch_name": "ConBench2"}, "ok", None),
    ("sketch_constrain", {"constraint": "vertical", "entity_one": "line:2",
                          "sketch_name": "ConBench2"},
     lambda p: p.get("applied") == "vertical", None),
    ("sketch_constrain", {"constraint": "collinear", "entity_one": "line:1",
                          "entity_two": "line:0", "sketch_name": "ConBench2"},
     lambda p: p.get("applied") == "collinear", None),
    ("sketch_constrain", {"constraint": "concentric", "entity_one": "circle:1",
                          "entity_two": "circle:0", "sketch_name": "ConBench2"},
     lambda p: p.get("applied") == "concentric", None),
    # the spline continues the line it was drawn from, sharing that endpoint, and 'smooth' makes the
    # join curvature-continuous - the one kind that needs a spline on at least one side.
    ("sketch_constrain", {"constraint": "smooth", "entity_one": "spline:0",
                          "entity_two": "line:4", "sketch_name": "ConBench2"},
     lambda p: p.get("applied") == "smooth", None),
    # the square's four lines told they ARE a polygon - equal lengths and equal angles in one
    # constraint instead of six.
    ("sketch_constrain", {"constraint": "polygon", "entities": "line:5,line:6,line:7,line:8",
                          "sketch_name": "ConBench2"},
     lambda p: p.get("applied") == "polygon", None),
    # fix pins a curve where it sits; unfix releases the same one, so the pair is checkable as a
    # pair - a 'fix' that silently did nothing leaves nothing for 'unfix' to find.
    ("sketch_constrain", {"constraint": "fix", "entity_one": "line:3",
                          "sketch_name": "ConBench2"},
     lambda p: p.get("applied") == "fix", None),
    ("sketch_constrain", {"constraint": "unfix", "entity_one": "line:3",
                          "sketch_name": "ConBench2"},
     lambda p: p.get("applied") == "unfix", None),
    # THE POINT-PAIR KINDS, on four free stubs of their own. They take POINTS, and a bare 'point:N'
    # counts every point the sketch owns - a line's own endpoints included - so 'point:1' on a
    # sketch holding curves is the first line's start, not the first point drawn. Anchored refs name
    # the endpoint directly, which is the whole reason the anchor form exists.
    ("sketch_create", {"plane": "xy", "name": "PtBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1060, "y1": 300, "x2": 1090, "y2": 306,
                             "sketch_name": "PtBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1110, "y1": 296, "x2": 1140, "y2": 304,
                             "sketch_name": "PtBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1060, "y1": 340, "x2": 1090, "y2": 348,
                             "sketch_name": "PtBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1066, "y1": 380, "x2": 1096, "y2": 388,
                             "sketch_name": "PtBench"}, "ok", None),
    # two stubs made LEVEL by their start points, and two made PLUMB by theirs - no curve is
    # constrained either time, which is what separates these from plain horizontal/vertical.
    ("sketch_constrain", {"constraint": "horizontal_points", "entity_one": "line:0:start",
                          "entity_two": "line:1:start", "sketch_name": "PtBench"},
     lambda p: p.get("applied") == "horizontal_points", None),
    ("sketch_constrain", {"constraint": "vertical_points", "entity_one": "line:2:start",
                          "entity_two": "line:3:start", "sketch_name": "PtBench"},
     lambda p: p.get("applied") == "vertical_points", None),
    # THE CREATOR KINDS. Each gets its own sketch: an offset lands NEW curves, so a second one
    # written against the same sketch would be aimed at refs the first has already renumbered. The
    # verdict is 'created_count' - a constraint that returned ok and drew nothing is the silent
    # no-op these rows exist to catch.
    ("sketch_create", {"plane": "xy", "name": "OffOne"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 860, "y1": 730, "x2": 940, "y2": 730,
                             "sketch_name": "OffOne"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 940, "y1": 730, "x2": 940, "y2": 790,
                             "sketch_name": "OffOne"}, "ok", None),
    ("sketch_constrain", {"constraint": "offset", "entities": "line:0,line:1", "distance": 8,
                          "sketch_name": "OffOne"},
     lambda p: p.get("applied") == "offset" and p.get("created_count", 0) >= 2, None),
    ("sketch_create", {"plane": "xy", "name": "OffTwo"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 960, "y1": 730, "x2": 1040, "y2": 730,
                             "sketch_name": "OffTwo"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1040, "y1": 730, "x2": 1040, "y2": 790,
                             "sketch_name": "OffTwo"}, "ok", None),
    # the two-sided form draws the offset on BOTH sides of the source, so it lands twice the curves.
    ("sketch_constrain", {"constraint": "offset_two_sides", "entities": "line:0,line:1",
                          "distance": 8, "sketch_name": "OffTwo"},
     lambda p: p.get("applied") == "offset_two_sides" and p.get("created_count", 0) >= 4, None),
    ("sketch_create", {"plane": "xy", "name": "CircPat"}, "ok", None),
    ("sketch_add_geometry", {"kind": "point", "cx": 900, "cy": 880, "sketch_name": "CircPat"},
     "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 940, "cy": 880, "radius": 6,
                             "sketch_name": "CircPat"}, "ok", None),
    # six around the drawn centre point: five NEW circles, the original not counted.
    ("sketch_constrain", {"constraint": "circular_pattern", "entities": "circle:0",
                          "entity_one": "point:1", "quantity": 6, "angle": 360,
                          "sketch_name": "CircPat"},
     lambda p: p.get("applied") == "circular_pattern" and p.get("created_count") == 5, None),
    # THE DIMENSION BENCH: the sizing kinds the story's own sketches never need. Each one names a
    # different pair of operand types, so each needs its own geometry - a circle pair sharing a
    # centre, a line and a circle for the tangent measure, and an ellipse for the two radius kinds.
    # Every row reads the LANDED value back: a dimension that attached to the wrong entity still
    # returns ok, and only its measured number says so.
    ("sketch_create", {"plane": "xy", "name": "DimBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1060, "y1": 480, "x2": 1120, "y2": 500,
                             "sketch_name": "DimBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1090, "cy": 560, "radius": 24,
                             "sketch_name": "DimBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1090, "cy": 560, "radius": 12,
                             "sketch_name": "DimBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 1140, "y1": 530, "x2": 1140, "y2": 590,
                             "sketch_name": "DimBench"}, "ok", None),
    ("sketch_add_geometry", {"kind": "ellipse", "cx": 1090, "cy": 650, "radius": 30, "minor": 16,
                             "sketch_name": "DimBench"}, "ok", None),
    # the horizontal component of a slanted line's own span: 60 mm across, not its 63.2 mm length.
    ("sketch_dimension", {"dim_type": "horizontal_distance", "entity_one": "line:0:start",
                          "entity_two": "line:0:end", "sketch_name": "DimBench"},
     _dim_measures(60.0), None),
    ("sketch_dimension", {"dim_type": "diameter", "entity_one": "circle:0",
                          "sketch_name": "DimBench"}, _dim_measures(48.0), None),
    # the gap between two circles on one centre: 24 mm outer less 12 mm inner.
    ("sketch_dimension", {"dim_type": "concentric_circle", "entity_one": "circle:0",
                          "entity_two": "circle:1", "sketch_name": "DimBench"},
     _dim_measures(12.0), None),
    # line to the NEAR tangent of the circle: the line stands at x 1140, the circle's near side at
    # 1090+24, so 26 mm.
    ("sketch_dimension", {"dim_type": "tangent_distance", "entity_one": "line:1",
                          "entity_two": "circle:0", "sketch_name": "DimBench"},
     _dim_measures(26.0), None),
    ("sketch_dimension", {"dim_type": "ellipse_major_radius", "entity_one": "ellipse:0",
                          "sketch_name": "DimBench"}, _dim_measures(30.0), None),
    ("sketch_dimension", {"dim_type": "ellipse_minor_radius", "entity_one": "ellipse:0",
                          "sketch_name": "DimBench"}, _dim_measures(16.0), None),
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
    # the SECOND solver option, on a sketch of its own - option1 above is the default and the one
    # the payload names, so a request for the other has to come back named as the other or the knob
    # was dropped on the way in.
    ("sketch_create", {"plane": "xy", "name": "AutoCon2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 720, "y1": 560, "x2": 820, "y2": 600,
                             "sketch_name": "AutoCon2"}, "ok", None),
    ("sketch_constrain", {"constraint": "auto", "sketch_name": "AutoCon2",
                          "result_option": "option2"},
     lambda p: p.get("result_option_requested") == "option2"
     and p.get("added_constraints", 0) + p.get("added_dimensions", 0) > 0
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
                       if e.get("type") == "circle") - _px("PatExtent", 690.0)) < 0.01, None),
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
    ("model_extrude", {"sketch_name": "W3Conic", "profile_index": 0, "distance": 5}, _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "W3Earc"}, "ok", None),
    ("sketch_add_geometry", {"kind": "elliptical_arc", "cx": 840, "cy": 80, "radius": 30,
                             "minor": 15, "sweep_deg": 180, "sketch_name": "W3Earc"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 870, "y1": 80, "x2": 810, "y2": 80,
                             "sketch_name": "W3Earc"}, "ok", None),
    ("model_extrude", {"sketch_name": "W3Earc", "profile_index": 0, "distance": 5}, _extruded, None),
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
    # THE EDIT-PATH RESIZE, before those deletes and leaving their inputs untouched: 'height' on an
    # EDIT writes SketchText.heightParameter and the glyph geometry follows it. The same string goes
    # back in, so the string and the count the deletes below read are exactly what they were. The
    # tool REFUSES a resize whose value landed while the box stayed put, so a green step here is
    # itself the geometry-followed proof - what the predicate reads is the numbers it published.
    ("sketch_set_text", {"text": "SCRAP2", "sketch_name": "TextDel", "index": 1, "height": 3},
     lambda p: p.get("changed_count") == 1 and (lambda c:
         c.get("height") == 3.0 and c.get("height_before") == 5.0
         and c.get("before") == "SCRAP2" and c.get("after") == "SCRAP2"
         and isinstance(c.get("measured_width"), (int, float)) and c["measured_width"] > 0
         and isinstance(c.get("measured_height"), (int, float)) and c["measured_height"] > 0
     )(p["changed"][0]), None),
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
     == [_px("SlotD", 1704.0), _px("SlotD", 1756.0)], None),
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
     lambda p: any(abs(e["center"]["x"] - _px("SlotG", 1725)) < 1e-3
                   and abs(e["center"]["y"] - _py("SlotG", 1500)) < 1e-3
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
]


_DETAILS = [
    # 1 mm fillets and chamfers on the rings - invisible at anything but ring scale.
    # The BORE rim, asked for by the parameter that defines it. The OD rims took their break back in
    # ACT 2, and a fillet's own tangent circle is not a corner - handing one back to model_fillet
    # answers FILLET_NO_EDGE_FOUND - so an unfiltered 'first circular edge' on this ring now lands on
    # geometry that cannot be filleted at all.
    ("param_get", {"name": "OuterBoreR"}, _param_read("OuterBoreR", 42),
     ("outer_bore", lambda p: p["parameter"]["value"])),
    ("find_geometry", lambda c: {"target": "OuterRing", "kind": "circular_edge",
                                 "radius": _ctx_get(c, "outer_bore", "the outer ring bore"),
                                 "max_results": 1}, "ok", _fg("or_edge")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "or_edge", "outer ring bore edge")],
                                "radius": 1}, _filleted, None),
    ("find_geometry", {"target": "Frame", "kind": "circular_edge", "max_results": 1}, "ok", _fg("fr_edge")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "fr_edge", "frame edge")], "distance": 1}, _chamfered, None),
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
    ("model_create_component", {"name": "ShellCap", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ShellS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 400, "y1": 0, "x2": 430, "y2": 30, "sketch_name": "ShellS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ShellS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [415, 15, 20], "max_results": 1}, "ok", _fg("shell_top")),
    ("model_shell", lambda c: {"body_name": "ShellCap", "remove_faces": [_ctx_get(c, "shell_top", "shell top")], "thickness": 2}, _shelled, None),
    # THE TWO-SIDED EXTENT, in its own component so nothing else's body census moves. Side one lands
    # on extentOne and side two on extentTwo, each reporting the depth THAT side asked for, and the
    # tool now compares them side by side - so an UNEQUAL pair is the shape that tells the compare
    # apart from one reading a single number twice. The payload alone cannot show where the material
    # went, hence the inspect below.
    ("model_create_component", {"name": "TwoSideCap", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TwoSideS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 470, "y1": 0, "x2": 500, "y2": 30,
                             "sketch_name": "TwoSideS"}, "ok", None),
    # 'model_parameters' carrying BOTH distance and distance2 is the feature really holding two
    # extents; an EMPTY unverified disclosure is the compare having judged both sides rather than
    # skipping one (it names any side it could not judge).
    ("model_extrude", {"sketch_name": "TwoSideS", "profile_index": 0, "extent": "two_side",
                       "distance": 12, "distance2": 8},
     lambda p: (p.get("extent") == "two_side" and p.get("distance") == 12.0
                and p.get("distance2") == 8.0 and bool(p.get("result_bodies"))
                and "distance" in (p.get("model_parameters") or {})
                and "distance2" in (p.get("model_parameters") or {})
                and "not depth-verified" not in (p.get("note") or "")), None),
    # Where the material actually went: the sketch sits on z=0, so a correct two-sided extrude
    # STRADDLES it 20 mm deep and UNEQUALLY. A symmetric 12/12 or 8/8 fails the inequality, a
    # one-sided 20 fails the straddle, and a swap only changes which face is which - all three are
    # payloads that would read identically above.
    ("model_inspect", {"target": "TwoSideCap:1"},
     lambda p: (p["min_point"]["z"] < -0.001 and p["max_point"]["z"] > 0.001
                and abs((p["max_point"]["z"] - p["min_point"]["z"]) - 20) < 0.05
                and abs(abs(p["max_point"]["z"]) - abs(p["min_point"]["z"])) > 3), None),
    # back to the shell cameo's component, so every step after this lands where it did before.
    ("design_activate_component", {"occurrence": "ShellCap:1"}, "ok", None),
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
    # The angled plane is built on the DATUM BENCH's bore, not on the gyroscope's shaft. A datum
    # plane is an infinite visual object and the shaft sits at the world origin - which is where the
    # vise is later built around the machined part, so a plane hung there leans across the fixture
    # for the rest of the run. The bench is out on the field with its own cell and its own frame.
    ("model_construction", lambda c: {"kind": "plane", "mode": "at_angle_on_face",
                                      "face": _ctx_get(c, "db_bore", "bench bore"),
                                      "plane": "xz", "angle": 30, "name": "BenchAnglePlane"},
     # the bore's axis is Z through the bench's own centre, so the plane contains that axis and its
     # origin sits ON it - asked through _px/_py because the layout moves the bench.
     lambda p: p.get("contains_face_axis") is True and p.get("angle_deg") == 30
     and _near(p["geometry"]["origin"]["x"], _px("DatumBench", 230.0), 1e-3)
     and _near(p["geometry"]["origin"]["y"], _py("DatumBench", 20.0), 1e-3), None),
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
     and abs(p["geometry"]["origin"]["x"] - _px("ShellCap", 415)) < 1e-3
     and abs(p["geometry"]["origin"]["y"] - _py("ShellCap", 0)) < 1e-3
     and abs(p["geometry"]["origin"]["z"]) < 1e-3
     and p.get("landed", {}).get("distance") == "0.5"
     and "path_length" not in p and "beyond_path" not in p
     and "not clamped" not in (p.get("note") or ""), None),
    # a quarter along the SAME edge: 7.5 mm from the midpoint whichever way the edge runs.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 0.25, "name": "QuarterPathPoint"},
     lambda p: p.get("at_ratio") == 0.25
     and abs(abs(p["geometry"]["at"]["x"] - _px("ShellCap", 415)) - 7.5) < 1e-3
     and abs(p["geometry"]["at"]["y"] - _py("ShellCap", 0)) < 1e-3
     and abs(p["geometry"]["at"]["z"]) < 1e-3, None),
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
    ("model_create_component", {"name": "ScratchOcc", "activate": False}, _made_component_inactive, None),
    ("design_delete_occurrence", {"occurrence": "ScratchOcc:1"}, "ok", None),
    # scale + offset-face beats on a scratch block: push a face and read the volume move, then the
    # scale contract - uniform f^3, per-axis x*y*z, the three refusal shapes (unresolvable /
    # length-carrying / angle-carrying expression), a bare unitless parameter accepted, and a
    # vertex-anchored scale.
    ("model_create_component", {"name": "ScaleBlock", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ScaleS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 470, "y1": 0, "x2": 490, "y2": 20,
                             "sketch_name": "ScaleS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ScaleS", "profile_index": 0, "distance": 20}, _extruded, None),
    _watch("ScaleBlock:1"),
    ("find_geometry", {"target": "ScaleBlock", "kind": "planar_face", "nearest_to": [480, 10, 20],
                       "max_results": 1}, "ok", _fg("scale_top")),
    ("model_offset_face", lambda c: {"faces": [_ctx_get(c, "scale_top", "block top")],
                                     "distance": 2}, _offset_faces, None),
    # a UNITLESS and an ANGLE parameter: 'value_units' is the parameter's OWN unit read back, which
    # is what says the angle came out of the internal radians into degrees.
    ("param_add", {"name": "ShrinkProbe", "expression": "0.5", "unit": ""},
     _param_added("ShrinkProbe", 0.5, units=""), None),
    ("param_add", {"name": "TiltProbe", "expression": "30 deg", "unit": "deg"},
     _param_added("TiltProbe", 30, units="deg"), None),
    # a solid body's volume IS readable, so the verdict is the measured ratio and the skip flag is
    # absent - its presence would mean the check fell back to "the geometry moved".
    # A SCALE IS ORIGIN-RELATIVE: it multiplies coordinates measured from the world origin, so a
    # block authored at x=470 doubles to x=940 - it grows AND travels, right out of the frame it was
    # framed in. Every scale that moves it is followed by a fresh frame, or the operation the viewer
    # came to watch happens off screen.
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": 2},
     lambda p: p.get("scale_check") == "volume_ratio"
     and abs(p.get("volume_ratio", 0) - 8.0) < 1e-6 and "volume_check_skipped" not in p, None),
    _watch("ScaleBlock:1"),
    ("model_scale", {"bodies": ["ScaleBlock"], "x_factor": 3, "y_factor": 2, "z_factor": 1},
     lambda p: abs(p.get("expected_volume_ratio", 0) - 6.0) < 1e-6, None),
    _watch("ScaleBlock:1"),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "NoSuchParamXyz * 2"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "5 mm"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "TiltProbe"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "ShrinkProbe"},
     lambda p: abs(p.get("expected_volume_ratio", 0) - 0.125) < 1e-6, None),
    _watch("ScaleBlock:1"),
    ("find_geometry", {"target": "ScaleBlock", "kind": "vertex", "max_results": 1}, "ok",
     _fg("scale_vtx")),
    ("model_scale", lambda c: {"bodies": ["ScaleBlock"], "factor": 1.5,
                               "anchor": _ctx_get(c, "scale_vtx", "block vertex")},
     lambda p: abs(p.get("volume_ratio", 0) - 3.375) < 1e-6, None),
    ("param_delete", {"name": "ShrinkProbe"}, _param_deleted("ShrinkProbe"), None),
    ("param_delete", {"name": "TiltProbe"}, _param_deleted("TiltProbe"), None),
    ("model_move", {"bodies": ["ScaleBlock"], "dx": 10},
     lambda p: abs(p.get("displacement", 0) - 10.0) < 1e-3, None),
    ("model_move", {"mode": "along_entity", "bodies": ["ScaleBlock"], "axis": "y",
                    "distance": 5}, lambda p: abs(p.get("displacement", 0) - 5.0) < 1e-3, None),
    ("model_move", {"mode": "rotate", "bodies": ["ScaleBlock"], "axis": "z", "angle_deg": 15},
     _moved, None),
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
    ("model_create_component", {"name": "TwicePlaced", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TwicePlacedS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1900, "y1": 200, "x2": 1920, "y2": 220,
                             "sketch_name": "TwicePlacedS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwicePlacedS", "profile_index": 0, "distance": 10},
     _extruded, None),
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
    ("model_inspect", {"target": "TwiceBody"}, _extent_measured, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("design_add_instance", {"component": "TwicePlaced", "x": 40, "y": 0, "units": "mm"},
     lambda p: p.get("created") is True, ("twice_b", lambda p: p["full_path"])),
    ("model_move", {"mode": "along_entity", "bodies": ["TwicePlaced"], "axis": "y", "distance": 5},
     _refused("placed 2 times", "TwicePlaced:1"), None),
    # placed TWICE the same bare name is two world placements - the refusal lists BOTH
    # instance-qualified forms (the dropped native spelling is not offered), and the qualified
    # form is the way out.
    ("model_inspect", {"target": "TwiceBody"},
     _refused("TwicePlaced:1", "TwicePlaced:2"), None),
    ("model_inspect", {"target": "TwicePlaced:2:TwiceBody"}, _extent_measured, None),
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
    ("model_create_component", {"name": "ThreadPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ThreadS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 530, "cy": 10, "radius": 5,
                             "sketch_name": "ThreadS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadS", "profile_index": 0, "distance": 25}, _extruded, None),
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
    ("model_create_component", {"name": "ThreadPost2", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ThreadS2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 570, "cy": 10, "radius": 5,
                             "sketch_name": "ThreadS2"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadS2", "profile_index": 0, "distance": 25}, _extruded, None),
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
    ("model_create_component", {"name": "ThreadBore", "activate": True}, _made_component, None),
    # the bore is CUT, not left as the inner loop of a two-circle profile: which region a
    # multi-profile sketch calls its last one is the platform's to decide, and picking the disc
    # there builds a plain rod whose radius-4 wall is EXTERNAL - the thread then lands external and
    # the beat asserts nothing about bores. A solid rod plus a through cut is unambiguous.
    ("sketch_create", {"plane": "xy", "name": "ThreadBoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 610, "cy": 10, "radius": 12,
                             "sketch_name": "ThreadBoreS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadBoreS", "profile_index": 0, "distance": 25},
     _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "ThreadBoreCut"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 610, "cy": 10, "radius": 4,
                             "sketch_name": "ThreadBoreCut"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadBoreCut", "profile_index": 0, "distance": 25,
                       "operation": "cut"}, _extruded, None),
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
    # a model EDGE handle as the path: the placement call accepts it and Fusion then rejects the
    # add, so the guard refuses it up front and points at sketch_project.
    ("sketch_set_text", lambda c: {"text": "EDGE", "sketch_name": "TextPaths", "create": True,
                                   "mode": "along_path",
                                   "path": _ctx_get(c, "cd_edge", "a model edge handle")},
     "refused", None),
    # design_remove_feature: cast a scratch body, remove it (the census is the verdict), then
    # delete the Remove feature - the body comes back, which is the reversibility the note claims.
    ("model_create_component", {"name": "RmScratch", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RmS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1100, "y1": 0, "x2": 1120, "y2": 20,
                             "sketch_name": "RmS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RmS", "profile_index": 0, "distance": 5}, _extruded, None),
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
    ("model_inspect", {"target": "RmScratch:1"}, _extent_measured, None),
    # model_emboss: a raise then an engrave on one scratch block's top face. The profiles are drawn
    # on a datum plane COINCIDENT with that face, so each sketch holds exactly one profile (an
    # on-face sketch auto-projects the face boundary and would offer two). The verdict is the
    # measured volume direction: 'mode' echoes the sign of the depth and the call is refused when
    # the material moved the other way.
    *_box("EmbossBlock", ox=600, oy=100),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 10, "name": "EmbPlane"},
     _datum_plane("xy"), None),
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
     _datum_plane("yz"), None),
    ("model_mirror", lambda c: {"features": [_ctx_get(c, "emb_feat", "the emboss feature")],
                                "plane": "EmbMirrorPlane"},
     lambda p: p.get("mode") == "features"
     and (p.get("bodies_added") or p.get("volume_change_cm3")), None),
    # join is a BODY-mode setting and 'joined' is read off the created feature, never echoed. The
    # body is named through a FRESH face handle: the mirrored emboss re-cut the one taken above.
    # A JOIN NEEDS THE TWO HALVES TO TOUCH, and the mirror plane is what decides whether they do.
    # About an ORIGIN plane this block's reflection lands 200 mm away with nothing between them:
    # Fusion keeps the feature, makes a second body and marks it "Could not join, multiple bodies
    # created" - a timeline WARNING left in the finished document, which the 'joined' flag alone does
    # not catch because the SETTING was honoured even though the join was not. Mirroring about the
    # block's own +X face gives the reflection that face to fuse across, and the volume the tool
    # measures for itself is what says it fused rather than landing beside it.
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": 620, "name": "EmbJoinPlane"},
     _datum_plane("yz"), None),
    ("find_geometry", {"target": "EmbossBlock", "kind": "planar_face", "nearest_to": [610, 110, 10],
                       "max_results": 1}, "ok", _fg("emb_body")),
    ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "emb_body", "the emboss block body")],
                                "plane": "EmbJoinPlane", "join": True},
     lambda p: p.get("joined") is True and _mirrored(p), None),
    ("model_mirror", lambda c: {"features": [_ctx_get(c, "emb_feat", "the emboss feature")],
                                "plane": "EmbMirrorPlane", "join": True}, "refused", None),
    # model_replace_face: a scratch block whose top face is replaced by an OPEN sheet sitting above
    # it. The sheet is sloped, so the body's measured volume has to move - a replace that changed
    # nothing is an error, not a quiet ok.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "ReplBlock", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ReplS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 660, "y1": 0, "x2": 690, "y2": 30,
                             "sketch_name": "ReplS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ReplS", "profile_index": 0, "distance": 20}, _extruded, None),
    # the replacement rides its own component so one find_geometry names it without ambiguity, and
    # surface_extrude reads is_solid=false back off the result - which is what makes it a legal
    # target. Symmetric, so the sheet spans the block whichever way the extrude runs.
    ("model_create_component", {"name": "ReplRoof", "activate": True}, _made_component, None),
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
    ("model_create_component", {"name": "PipeRun", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "PipeRunPath"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 720, "y1": 0, "x2": 720, "y2": 40,
                             "sketch_name": "PipeRunPath"}, "ok", None),
    # PipeFeature.startFaces/endFaces/sideFaces all read EMPTY on a freshly added hollow pipe, so
    # nothing on this build can say whether the ends are capped - and the payload publishes no
    # 'capped_ends' key rather than a guess dressed as a read.
    ("model_pipe", {"path": "sketch:PipeRunPath", "section_size": 10, "wall_thickness": 1.5},
     lambda p: p.get("hollow") is True and abs((p.get("wall_thickness") or 0) - 1.5) < 1e-6
     and "capped_ends" not in p, None),
    ("model_create_component", {"name": "PipeHalf", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "PipeHalfPath"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 760, "y1": 0, "x2": 760, "y2": 40,
                             "sketch_name": "PipeHalfPath"}, "ok", None),
    ("model_pipe", {"path": "sketch:PipeHalfPath", "section_size": 6, "section_type": "square",
                    "path_fraction": 0.5}, _piped, None),
    # the occurrence bounding box counts BODIES only, so the path sketch cannot inflate this read.
    ("model_inspect", {"target": "PipeHalf:1"}, lambda p: abs(p.get("z", 0) - 20.0) < 1.0, None),
    ("model_pipe", {"path": "sketch:PipeRunPath", "section_size": 6, "path_fraction": 0.5,
                    "path_fraction_reverse": 0.4}, "refused", None),
    # a CUT scoped to named bodies: participantBodies is write-only on the created feature, so the
    # scope cannot be read back - the note says the list is what was REQUESTED rather than dressing
    # an echo up as a read-back. The block is built around the path so the cut has material to take.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeCut", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PipeCutS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 800, "y1": -20, "x2": 840, "y2": 20,
                             "sketch_name": "PipeCutS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PipeCutS", "profile_index": 0, "distance": 20}, _extruded, None),
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
    ("model_create_component", {"name": "TangentRun", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TangentRunS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1900, "y1": 0, "x2": 1960, "y2": 60,
                             "sketch_name": "TangentRunS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TangentRunS", "profile_index": 0, "distance": 20},
     _extruded, None),
    # ONE vertical edge rounded: the top rim reads line - arc - line, bounded by sharp corners.
    ("find_geometry", {"target": "TangentRun", "kind": "line_edge", "nearest_to": [1900, 0, 10],
                       "max_results": 1}, "ok", _fg("tr_corner")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "tr_corner", "the box corner edge")],
                                "radius": 8}, _filleted, None),
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
    ("model_create_component", {"name": "TangentLoop", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TangentLoopS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 2000, "y1": 0, "x2": 2060, "y2": 60,
                             "sketch_name": "TangentLoopS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TangentLoopS", "profile_index": 0, "distance": 20},
     _extruded, None),
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
                                "radius": 8}, _filleted, None),
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
    _watch("Frame:1"),
    ("view_section", {"action": "cut", "plane": "yz", "offset": 0}, "ok", None),
    ("view_screenshot", {"width": 500, "height": 400}, "ok", None),
    ("view_section", {"action": "clear"}, "ok", None),
    ("view_screenshot_multi", {"views": ["front", "top"], "width": 400, "height": 300}, "ok", None),
    # THE RASTER WRITER (NEW-13): file_path also writes the rendered PNG to disk - the extension is
    # appended, the landed file is verified non-zero, and path + size are published beside the
    # inline image. The fleet's only raster writer, which is what feeds drawing_insert_image.
    ("view_screenshot", {"width": 400, "height": 300,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot"},
     lambda p: "w4_shot.png" in str(p) and "size_bytes=" in str(p), None),
    # a second write to the SAME path lands without a refusal - the overwrite behaviour that makes
    # this tool write-kind (and puts it behind the write guard below).
    ("view_screenshot", {"width": 200, "height": 150,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot"},
     lambda p: "w4_shot.png" in str(p) and "size_bytes=" in str(p), None),
    ("view_screenshot", {"width": 200, "height": 150,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot",
                         "expect_document": "ZzNoSuchDocument"},
     _refused("active_document_changed"), None),
    # The dimension/constraint beats that measure TO a model face sit at the end of the act,
    # not in the middle of the modelling: they are sketch work, and they are here only
    # because a face is what they measure against.
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
     and abs(float((p.get("value") or "0 x").split()[0])
             - abs(_px("W3Dims", 1020) - _px("ShellCap", 400))) < 0.1
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
]

# --- ACT 6: THE RESIZE - the parametric resize check (mirrors scenario S6) ---------------------
# Bump the one driving diameter; the whole gyroscope grows. Read the rings back before and after.
_RESIZE = [
    # the resize walks the whole mechanism - frame it so the parts are seen to move.
    _watch("Frame:1"),
    ("view_screenshot", {"width": 400, "height": 300}, "ok", None),
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", None),   # before
    ("param_set", {"name": "GimbalDia", "expression": "160 mm"},
     _param_set_to("GimbalDia", 160), None),
    ("design_recompute", {}, "ok", None),
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", None),   # after - rings grew
    ("sketch_get", {"sketch_name": "InnerRingSketch"}, "ok", None),
    # the StockCenter JO (ACT 3, the CAM WCS anchor) read back after the resize: parametrically
    # anchored at the shared center, it HOLDS position through the recompute - the anchor CAM binds.
    # Either ACT 3 path (narrative or scratch fallback) builds a JO by that name.
    ("assembly_get", {"include": ["joint_origins"]}, _joint_origins_listed("StockCenter"), None),
    ("view_screenshot", {"width": 400, "height": 300}, "ok", None),
    ("param_set", {"name": "GimbalDia", "expression": "120 mm"},
     _param_set_to("GimbalDia", 120), None),   # restore
    ("design_recompute", {}, "ok", None),
    ("sketch_get", {"sketch_name": "OuterRingSketch"}, "ok", None),   # restored
    ("param_add", {"name": "ScratchDim", "expression": "5 mm"}, _param_added("ScratchDim", 5), None),
    ("param_delete", {"name": "ScratchDim"}, _param_deleted("ScratchDim"), None),
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
    # CAM hid the sketch folders for the machining movement; the design is a sketch-bearing
    # story again from here, and the FINALE always runs, so this is where they come back.
    ("view_set", {"action": "display", "categories": ["sketches"], "visible": True},
     lambda p: p.get("visible") is True, None),
    # the machined part in its fixture - the gyroscope itself was stripped away in ACT 8, so the
    # end state IS the vise holding the stock the Carrier was cut from
    _watch(["ViseBase:1", "STOCK:1"]),
    ("view_screenshot_multi", {"views": ["iso-top-right", "front"], "width": 500, "height": 400}, "ok", None),
    # THE VIEW VERBS, all on the finished fixture. ONE framed orient sets the subject; every preset
    # after it carries fit=false and no focus, so the camera ROTATES about what is already framed
    # instead of re-fitting per preset. That is the difference between a turntable and ten separate
    # zoom-outs - the vise stays the same size in the same place and only the angle changes. It also
    # keeps the tour silent: the runner shoots a frame for a camera row that names a focus, so ten
    # focused orients would write ten near-identical screenshots.
    # The tour opens with a snapshot and closes on 'restore', which is what makes it checkable -
    # camera, style and every visibility bulb come back to the state the tour started from.
    ("view_set", {"action": "snapshot"}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "front", "focus": ["ViseBase:1", "STOCK:1"]},
     "ok", None),
    ("view_set", {"action": "orient", "orientation": "back", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "left", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "right", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "top", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "bottom", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-left", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-bottom-right", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-bottom-left", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right", "fit": False}, "ok", None),
    # every visual style the tool offers, held on the one hero angle. 'current' on view_screenshot is
    # the no-move capture - the only way to shoot what the camera already frames, since a NAMED view
    # refits the whole model.
    ("view_set", {"action": "style", "style": "wireframe"}, "ok", None),
    ("view_set", {"action": "style", "style": "wireframe-edges"}, "ok", None),
    ("view_set", {"action": "style", "style": "wireframe-hidden-edges"}, "ok", None),
    ("view_set", {"action": "style", "style": "shaded-hidden-edges"}, "ok", None),
    ("view_set", {"action": "style", "style": "shaded"}, "ok", None),
    ("view_screenshot", {"view": "current", "width": 500, "height": 400}, "ok", None),
    ("view_set", {"action": "style", "style": "shaded-edges"}, "ok", None),
    # visibility, in the order that leaves nothing hidden behind: isolate the stock, hide one jaw,
    # show it again, then drop the isolation. Each verb reports what it reached.
    ("view_set", {"action": "isolate", "target": "STOCK:1"}, "ok", None),
    ("view_set", {"action": "clear_isolation"}, "ok", None),
    ("view_set", {"action": "hide", "target": "JawL:1"}, "ok", None),
    ("view_set", {"action": "show", "target": "JawL:1"}, "ok", None),
    # a persistent Named View: parked, listed among the document's own, and re-applied.
    ("view_set", {"action": "save_view", "view_name": "SweepHero"}, "ok", None),
    # the camera has to LEAVE the saved view for re-applying it to prove anything - in place, so the
    # proof does not cost a fit-to-whole-model on the way out and another on the way back.
    ("view_set", {"action": "orient", "orientation": "bottom", "fit": False}, "ok", None),
    ("view_set", {"action": "apply_view", "view_name": "SweepHero"}, "ok", None),
    ("view_set", {"action": "list_views"},
     lambda p: "SweepHero" in [v.get("name") for v in (p.get("named_views") or [])], None),
    ("view_set", {"action": "restore"}, "ok", None),
    # one contact sheet, four presets. This tool walks the camera per view and fits each one, so its
    # cost on screen is one zoom-out per view in the list - a seven-view sheet and an 'all' sheet
    # behind it read as the camera coming loose right at the end of the run. Four is enough to show
    # the sheet is a sheet; the orientation vocabulary is already covered by the turntable above,
    # which pays nothing to do it.
    ("view_screenshot_multi", {"views": ["back", "bottom", "left", "iso-bottom-left"],
                               "width": 300, "height": 240}, "ok", None),
    # THE RENAMES, last: a rename invalidates every row that names its target, so they run once the
    # build is done. A two-body cameo carries the dedupe beat - it needs a SIBLING pair, and the
    # machined part is a component of one body.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TwinCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TwinA"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1400, "y1": 200, "x2": 1430, "y2": 230,
                             "sketch_name": "TwinA"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwinA", "profile_index": 0, "distance": 10}, _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "TwinB"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1450, "y1": 200, "x2": 1480, "y2": 230,
                             "sketch_name": "TwinB"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwinB", "profile_index": 0, "distance": 10}, _extruded, None),
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
    ("model_create_component", {"name": "SoloColor", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SoloS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1500, "y1": 200, "x2": 1530, "y2": 230,
                             "sketch_name": "SoloS"}, "ok", None),
    ("model_extrude", {"sketch_name": "SoloS", "profile_index": 0, "distance": 10}, _extruded, None),
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
    # EVERY neutral-CAD format the exporter declares, one file per factory, each measured ON DISK -
    # a build missing a factory, or one that reports success and writes nothing, fails here rather
    # than at whoever opens the file. The formats ImportManager can read then come straight back in
    # with the format named EXPLICITLY: doc_insert_import refuses a format that contradicts the
    # file's extension, so naming it checks that the extension the exporter chose is the one the
    # importer expects.
    ("design_export", {"format": "iges", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    # SAT is deliberately NOT here. Measured on this build: the FIRST createSATExportOptions export
    # in a Fusion session writes its file, and every one after it returns false having written
    # nothing - on any target, in a fresh document holding one box, and into a directory no .sat has
    # ever been written to, while IGES and SMT through the same call shape keep working in that same
    # session. The tool reports the failure honestly, which is the behaviour that matters; what the
    # sweep cannot do is assert an outcome that depends on whether anything exported SAT earlier.
    ("design_export", {"format": "smt", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    ("design_export", {"format": "f3d", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    ("design_export", {"format": "obj", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    ("design_export", {"format": "3mf", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    # USD lands as .usdz whatever extension the path carries - Fusion appends its own - so the tool
    # publishes the path it actually wrote.
    ("design_export", {"format": "usd", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"},
     lambda p: _exported_bytes(p) is True and str(p.get("file_path", "")).endswith(".usdz"), None),
    # STL with the units baked in: the one format carrying its own unit, so the knob is set and read
    # back off the options object that LANDED. The single-file path publishes 'options_applied' -
    # the split path's 'options_requested' is the per-file split's own key, and reading that one
    # here would assert nothing about this file.
    ("design_export", {"format": "stl", "file_path": EXPORT_DIR + "/fmt_carrier_in",
                       "target": "CarrierBar", "stl_units": "in", "stl_binary": False},
     lambda p: _exported_bytes(p) is True
     and (p.get("options_applied") or {}).get("stl_units") == "in"
     and (p.get("options_applied") or {}).get("stl_binary") is False, None),
    # the 2D branch: a sketch written as DXF, then read back onto a named plane as sketches.
    ("design_export", {"format": "dxf", "file_path": EXPORT_DIR + "/fmt_twin",
                       "dxf_sketch": "TwinA"}, _exported_bytes, None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/fmt_twin.dxf", "format": "dxf",
                           "plane": "xy"}, _imported_sketches, None),
    # SVG lands in an EXISTING sketch (there is no component-level SVG import), so one is made for it.
    ("sketch_create", {"plane": "xy", "name": "SvgImport"}, "ok", None),
    ("doc_insert_import", {"file_path": SVG_PATH, "format": "svg", "sketch": "SvgImport"},
     _imported_curves, None),
    # NO further solid re-imports. An import lands its geometry at the coordinates the FILE carries,
    # so re-importing a part into the design it came from drops a second copy exactly on top of the
    # original - measured: one per format left FIVE coincident Carriers on the machined part, which
    # is the one thing the CAM shot is of. The STEP round trip above is the story's visible proof
    # that a written file reads back; every other format is proven by its own measured bytes on
    # disk, which costs the scene nothing. Restoring an import here means giving it somewhere to
    # land that is not on top of the part.
    # the contradiction the explicit format exists to catch, on a file that is certainly there.
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/fmt_carrier.smt", "format": "step"},
     "refused", None),
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
    ("doc_get", {}, _document_read, None),
    ("doc_close", {"save_changes": False}, _document_closed, None),
]

# --- the retained SCRATCH fixtures - the precondition fallbacks (today's proven step bodies) ---
# When an act's precondition read fails (an upstream act could not build the geometry it consumes),
# the act runs one of these instead, so its tools are still covered - each row marked "(fallback
# fixture)". These are the minimal self-contained scratch fixtures the sweep has always used.

_SOLIDS_FB = (
    _box("FbSolid")
    + [
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fb_body")),
        ("appearance_set", {"target": "FbSolid", "color": "#1E8E3E"}, "ok", None),
        ("model_set_material", {"target": "FbSolid", "material": "Steel"}, _material_assigned, None),
        ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "plane": "yz"}, _mirrored, None),
        ("model_pattern_rectangular", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "quantity_one": 2, "spacing_one": 60, "direction_one": "y"}, _patterned("total_instances", 2), None),
        ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "quantity": 3, "total_angle_deg": 360, "axis": "z"}, _patterned("quantity", 3), None),
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fb_top")),
        ("model_hole", lambda c: {"face": _ctx_get(c, "fb_top", "top face"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[5, 5, 0]]}, _drilled(1), None),
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [0, 10, 5], "max_results": 1}, "ok", _fg("fb_side")),
        ("model_draft", lambda c: {"faces": [_ctx_get(c, "fb_side", "side face")], "pull_direction": "xy", "angle_deg": 3},
         _drafted, None),
        ("model_measure_between", lambda c: {"a": _ctx_get(c, "fb_body", "body"), "b": "FbSolid"}, _gap_measured, None),
        # a face compared with ITSELF is parallel to itself, so this beat asserts the verdict too.
        ("model_measure_relation", lambda c: {"relation": "parallel", "entity_a": _ctx_get(c, "fb_top", "top face"), "entity_b": _ctx_get(c, "fb_top", "top face")}, _relation_passes("parallel"), None),
        ("model_inspect", {"target": "FbSolid"}, _extent_measured, None),
        ("model_create_component", {"name": "FbRev", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xz", "name": "FbRevS"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": 10, "y1": 0, "x2": 20, "y2": 30, "sketch_name": "FbRevS"}, "ok", None),
        ("model_revolve", {"sketch_name": "FbRevS", "profile_index": 0, "axis": "z", "angle_deg": 360}, _revolved, None),
        ("model_create_component", {"name": "FbSwp", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xz", "name": "FbPath"}, "ok", None),
        ("sketch_add_geometry", {"kind": "line", "x1": 0, "y1": 0, "x2": 0, "y2": 40, "sketch_name": "FbPath"}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": "FbProf"}, "ok", None),
        ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 5, "sketch_name": "FbProf"}, "ok", None),
        ("model_sweep", {"profile": {"sketch": "FbProf", "profile_index": 0}, "path": "sketch:FbPath"}, _swept, None),
        ("model_create_component", {"name": "FbLft", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xy", "name": "FbLb"}, "ok", None),
        ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 10, "sketch_name": "FbLb"}, "ok", None),
        ("sketch_get", {"sketch_name": "FbLb"}, "ok", _prof("fb_lb")),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": 40, "name": "FbTop"},
         _datum_plane("xy"), None),
        ("sketch_create", {"plane": "FbTop", "name": "FbLt"}, "ok", None),
        ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 5, "sketch_name": "FbLt"}, "ok", None),
        ("sketch_get", {"sketch_name": "FbLt"}, "ok", _prof("fb_lt")),
        ("model_loft", lambda c: {"profiles": [_ctx_get(c, "fb_lb", "loft bottom"), _ctx_get(c, "fb_lt", "loft top")]}, _lofted, None),
        ("model_create_component", {"name": "FbCmb", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xy", "name": "FbCb1"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 30, "y2": 30, "sketch_name": "FbCb1"}, "ok", None),
        ("model_extrude", {"sketch_name": "FbCb1", "profile_index": 0, "distance": 10}, _extruded, None),
        ("sketch_create", {"plane": "xy", "name": "FbCb2"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": 20, "y1": 20, "x2": 50, "y2": 50, "sketch_name": "FbCb2"}, "ok", None),
        ("model_extrude", {"sketch_name": "FbCb2", "profile_index": 0, "distance": 10}, _extruded, None),
        ("find_geometry", {"target": "FbCmb", "kind": "planar_face", "nearest_to": [5, 5, 10], "max_results": 1}, "ok", _fg("fb_c1")),
        ("find_geometry", {"target": "FbCmb", "kind": "planar_face", "nearest_to": [45, 45, 10], "max_results": 1}, "ok", _fg("fb_c2")),
        ("model_combine", lambda c: {"target": _ctx_get(c, "fb_c1", "combine target"), "tools": [_ctx_get(c, "fb_c2", "combine tool")], "operation": "join"}, _joined, None),
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ]
)

# ACT 3 fallback: the proven 12-box joint/assembly fixture.
_MOTION_FB = (
    [("design_activate_component", {"occurrence": "root"}, "ok", None)]
    + _box("JA", tint="#E5533C") + _box("JB", tint="#1E88E5", shape="disc") + _box("JC", ox=100, tint="#E5533C") + _box("JD", ox=100, tint="#1E88E5", shape="disc") + _box("JE", tint="#E5533C") + _box("JF", tint="#1E88E5", shape="disc")
    + _box("JG", tint="#E5533C") + _box("JH", tint="#1E88E5", shape="disc") + _box("JK", tint="#E5533C") + _box("JL", tint="#1E88E5", shape="disc") + _box("JM", tint="#E5533C") + _box("JN", tint="#1E88E5", shape="disc")
    + _box("JP", tint="#E5533C") + _box("JQ", tint="#1E88E5", shape="disc")
    + [
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("assembly_ground", {"occurrence": "JB:1", "ground_to_parent": True}, _grounded, None),
        ("assembly_ground", {"occurrence": "JD:1", "ground_to_parent": True}, _grounded, None),
        ("assembly_ground", {"occurrence": "JN:1", "ground_to_parent": True}, _grounded, None),
        ("joint_create_origin", {"anchor": "coordinates", "x": 10, "y": 0, "z": 0, "name": "JOc"},
         _joint_origin_at("JOc", 10, 0, 0), None),
        ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "StockCenter"},
         _joint_origin_at("StockCenter", 0, 0, 0), None),
        ("joint_create", {"occurrence_one": "JA:1:top", "occurrence_two": "JB:1:top", "joint_type": "revolute", "axis": "z", "name": "RevJoint"}, _jointed("RevJoint"), None),
        ("joint_create", {"occurrence_one": "JC:1:top", "occurrence_two": "JD:1:top", "joint_type": "slider", "axis": "x", "name": "SlideJoint"}, _jointed("SlideJoint"), None),
        ("joint_drive", {"joint_name": "RevJoint", "angle_deg": 30}, _driven_angle(30), None),
        ("joint_drive", {"joint_name": "RevJoint", "angle_deg": 0}, _driven_angle(0), None),
        ("joint_edit", {"joint_name": "RevJoint", "min_deg": -45, "max_deg": 45},
         _joint_limits("RevJoint", min_deg=-45, max_deg=45), None),
        ("joint_create", {"occurrence_one": "JM:1:top", "occurrence_two": "JN:1:top", "joint_type": "revolute", "axis": "z", "name": "RevJoint2"}, _jointed("RevJoint2"), None),
        ("joint_motion_link", {"joint_one": "RevJoint", "joint_two": "RevJoint2", "ratio": 2},
         _motion_linked("RevJoint", "RevJoint2", False), None),
        # RevJoint was driven above, so driving its NEW link partner must refuse (the second-member
        # guard). This row also proves MotionLink.jointOne/jointTwo resolve live: a wrong property
        # name would make the partner lookup blind and this drive would wrongly succeed.
        ("joint_drive", {"joint_name": "RevJoint2", "angle_deg": 10}, "refused", None),
        ("joint_create_as_built", {"occurrence_one": "JE:1", "occurrence_two": "JF:1"}, _as_built, None),
        ("find_geometry", {"target": "JG", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("jg_face")),
        ("find_geometry", {"target": "JH", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("jh_face")),
        ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "jg_face", "joint face a"), "handle_two": _ctx_get(c, "jh_face", "joint face b"), "motion": "rigid"}, _jointed_at_geometry, None),
        # the five joints this fixture built (three parametric, one as-built, one at geometry).
        ("assembly_get", {}, _joints_listed(5, {"RevJoint": 0}), None),
        # JK:1 is a fresh box occurrence at the identity transform, so its read-back position IS the
        # 80 mm this move composed.
        ("assembly_move", {"occurrence": "JK:1", "dx": 80}, _moved_occurrence(80), None),
        ("assembly_capture_position", {"action": "capture"}, _captured, None),
        ("assembly_rigid_group", {"occurrences": ["JP:1", "JQ:1"]}, _rigid_grouped(2), None),
        ("assembly_constrain", {"snap_one": "JK:1:bottom", "snap_two": "JL:1:top", "flipped": True}, _constrained, None),
        ("assembly_inspect_interference", {}, _interference_measured, None),
        ("design_recompute", {}, "ok", None),
    ]
)

# ACT 4 fallback: a scratch details fixture.
_DETAILS_FB = (
    _box("FbDet")
    + [
        ("find_geometry", {"target": "FbDet", "kind": "line_edge", "max_results": 1}, "ok", _fg("fd_edge")),
        ("model_fillet", lambda c: {"edges": [_ctx_get(c, "fd_edge", "edge")], "radius": 1}, _filleted, None),
        ("find_geometry", {"target": "FbDet", "kind": "line_edge", "max_results": 1}, "ok", _fg("fd_edge2")),
        ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "fd_edge2", "edge")], "distance": 0.5}, _chamfered, None),
        ("find_geometry", {"target": "FbDet", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fd_top")),
        ("model_shell", lambda c: {"body_name": "FbDet", "remove_faces": [_ctx_get(c, "fd_top", "top")], "thickness": 2}, _shelled, None),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "FbWart"},
         _datum_plane("xy"), None),
        ("design_delete_feature", {"feature": "FbWart"}, "ok", None),
        ("model_create_component", {"name": "FbJunk", "activate": False}, _made_component_inactive, None),
        ("design_delete_occurrence", {"occurrence": "FbJunk:1"}, "ok", None),
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("view_section", {"action": "cut", "plane": "xy", "offset": 5}, "ok", None),
        ("view_screenshot", {"width": 400, "height": 300}, "ok", None),
        ("view_section", {"action": "clear"}, "ok", None),
        ("view_screenshot_multi", {"views": ["front", "top"], "width": 300, "height": 250}, "ok", None),
    ]
)

# ACT 6 fallback: a scratch parameter set/delete.
_RESIZE_FB = [
    ("param_add", {"name": "FbParam", "expression": "12 mm"}, _param_added("FbParam", 12), None),
    ("param_set", {"name": "FbParam", "expression": "14 mm"}, _param_set_to("FbParam", 14), None),
    ("design_recompute", {}, "ok", None),
    ("param_delete", {"name": "FbParam"}, _param_deleted("FbParam"), None),
]

# --- the CAMEO acts: surface-prep, mesh, and CAM families ride scratch fixtures in the SAME doc -
# These families have no natural home on the mechanism itself, so the spec places them as cameos.

# ACT 5: MACHINING PREP - surfaces, sheet ops, split/stitch/arrange/base-feature, holder read.
_MACHINING = [
    # the surface/split/stitch cameos live on a GRID (y=200 row, plus a z-lifted revolve) so each
    # builds in clear space a viewer can see, never on top of the gyroscope or another cameo.
    ("model_create_component", {"name": "SRev", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "SRevS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 10, "y1": 60, "x2": 10, "y2": 90, "sketch_name": "SRevS"}, "ok", None),
    ("surface_revolve", {"sketch_name": "SRevS", "axis": "z", "angle_deg": 360}, "ok", None),
    # a CLOSED revolved sphere surface encloses one cell; surface_fill must seal it to a SOLID at
    # the enclosed volume (r=6mm -> 904.78 mm3) MEASURED off the result, never predicted.
    ("model_create_component", {"name": "FillDemo", "activate": True}, _made_component, None),
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
    ("model_create_component", {"name": "Surf", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SurfS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 200, "x2": 240, "y2": 230, "sketch_name": "SurfS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SurfS", "distance": 15}, "ok", None),
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
    # the flip is invisible in every other read, so the row asserts the tool's own isParamReversed
    # read-back: 'reversed_confirmed' is false whenever the after-count does not match the flip.
    ("surface_reverse_normal", lambda c: {"bodies": [_ctx_get(c, "surf_body", "surface body")]},
     lambda p: p.get("reversed_confirmed") is True and p.get("faces_total", 0) > 0, None),
    # extend_alignment + thicken_type, on their own sheet so the read-backs never ride on edges an
    # earlier extend already moved. Both properties are written through set_verified, so a value the
    # platform drops comes back as an error rather than an echoed success. A rectangle extruded as a
    # surface gives a four-walled sheet, which is the convex corner set 'rounded' needs to mean
    # anything - a single flat face has no corner to round.
    ("model_create_component", {"name": "SAlign", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SAlignS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 700, "y1": 200, "x2": 740, "y2": 230,
                             "sketch_name": "SAlignS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SAlignS", "distance": 15}, "ok", None),
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
    ("model_create_component", {"name": "SDel", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SD1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 300, "y1": 200, "x2": 320, "y2": 220, "sketch_name": "SD1"}, "ok", None),
    ("model_extrude", {"sketch_name": "SD1", "profile_index": 0, "distance": 10}, _extruded, None),
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
    ("model_create_component", {"name": "PatchRail", "activate": True}, _made_component, None),
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
    ("model_create_component", {"name": "SHole", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SH1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 600, "y1": 0, "x2": 640, "y2": 0, "sketch_name": "SH1"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SH1", "distance": 40}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "SH2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 620, "cy": -20, "radius": 5, "sketch_name": "SH2"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SH2", "distance": 10, "symmetric": True}, "ok", None),
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_sheet")),
    ("find_geometry", {"target": "SHole", "kind": "cylinder_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_cutter")),
    # the cell bookkeeping is the read-back: at least one cell REMOVED (a trim that removed none
    # kept the whole sheet) and a kept area the phantom-cell gate could measure.
    ("surface_trim", lambda c: {"surface": _ctx_get(c, "sh_sheet", "sheet face"), "trim_tool": _ctx_get(c, "sh_cutter", "cylinder cutter")},
     lambda p: len(p.get("cells_removed") or []) >= 1 and (p.get("kept_area") or 0) > 0
     and bool(p.get("result_bodies")), None),
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_trimmed")),
    # removing the hole loop FILLS it, so the created faces' area sum is the read-back that the
    # extent grew; an untrim that created nothing leaves area_after at or below area_before.
    ("surface_untrim", lambda c: {"faces": [_ctx_get(c, "sh_trimmed", "trimmed sheet face")], "loop_type": "internal"},
     lambda p: p.get("extent_grew") is True and p.get("faces_created", 0) >= 1
     and p["area_after"] > p["area_before"], None),
    # RULED surfaces off a rim edge. A line extruded as a surface gives a vertical sheet whose top
    # rim (z=30) is the seed every ruled beat leaves from; each type builds a DIFFERENT surface off
    # that same edge, so the payload's own ruled_type/direction is what separates them.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "Ruled", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RuledS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "line", "x1": 900, "y1": 0, "x2": 940, "y2": 0,
                             "sketch_name": "RuledS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "RuledS", "distance": 30}, "ok", None),
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
    ("model_create_component", {"name": "RuledSolid", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RuledSolidS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 980, "y1": 200, "x2": 1020, "y2": 240,
                             "sketch_name": "RuledSolidS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RuledSolidS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "RuledSolid", "kind": "line_edge", "nearest_to": [1000, 200, 20],
                       "max_results": 1}, "ok", _fg("ruled_solid_edge")),
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_solid_edge", "a box top edge")],
                                        "distance": 15, "ruled_type": "tangent"},
     lambda p: p.get("is_solid") is False and len(p.get("result_bodies", [])) == 1
     and "Body1" not in p.get("result_bodies", []), None),
    # back to the component that was active before this cameo, so the ones after it nest as before.
    ("design_activate_component", {"occurrence": "SHole:1"}, "ok", None),
    # split / unstitch / stitch / base-feature / arrange / compute-holder.
    ("model_create_component", {"name": "Spl", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "Sp1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 400, "y1": 200, "x2": 440, "y2": 240, "sketch_name": "Sp1"}, "ok", None),
    ("model_extrude", {"sketch_name": "Sp1", "profile_index": 0, "distance": 20}, _extruded, None),
    ("model_construction", {"kind": "plane", "plane": "xz", "offset": 220, "name": "SplMid"},
     _datum_plane("xz"), None),
    ("find_geometry", {"target": "Spl", "kind": "planar_face", "nearest_to": [420, 220, 20], "max_results": 1}, "ok", _fg("spl_body")),
    ("model_split", lambda c: {"split": "body", "target": _ctx_get(c, "spl_body", "split body"), "split_plane": "SplMid"}, _split_bodies, None),
    ("model_create_component", {"name": "Stc", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "St1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 500, "y1": 200, "x2": 520, "y2": 220, "sketch_name": "St1"}, "ok", None),
    ("model_extrude", {"sketch_name": "St1", "profile_index": 0, "distance": 10}, _extruded, None),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [510, 210, 10], "max_results": 1}, "ok", _fg("stc_body")),
    ("model_unstitch", lambda c: {"target": _ctx_get(c, "stc_body", "unstitch body"), "chain": False}, _unstitched, None),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [510, 210, 0], "max_results": 1}, "ok", _fg("stc_f1")),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [500, 210, 5], "max_results": 1}, "ok", _fg("stc_f2")),
    ("model_stitch", lambda c: {"bodies": [_ctx_get(c, "stc_f1", "stitch a"), _ctx_get(c, "stc_f2", "stitch b")]}, _stitched, None),
    ("model_base_feature", {"action": "start", "base_feature": "BF1"}, _base_feature_open, None),
    ("model_base_feature", {"action": "finish", "base_feature": "BF1"}, _base_feature_closed, None),
] + [
    # compute_holder needs a body + a cyl-face axis + a planar end-datum.
    ("model_create_component", {"name": "HolderPart", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "HP1"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 600, "y1": 200, "x2": 640, "y2": 220, "sketch_name": "HP1"}, "ok", None),
    ("model_extrude", {"sketch_name": "HP1", "profile_index": 0, "distance": 10}, _extruded, None),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "nearest_to": [620, 210, 10], "max_results": 1}, "ok", _fg("hp_top")),
    # hole points ride the face's LOCAL frame = the model origin projected onto the face, so
    # on-pad coordinates are the world x,y (same measured fact as the FeatureCameo hole).
    ("model_hole", lambda c: {"face": _ctx_get(c, "hp_top", "holder top"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[605, 205, 0]]}, _drilled(1), None),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "max_results": 1}, "ok", _fg("hp_body")),
    ("find_geometry", {"target": "HolderPart", "kind": "cylinder_face", "radius": 2, "max_results": 1}, "ok", _fg("hp_axis")),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "nearest_to": [620, 210, 10], "max_results": 1}, "ok", _fg("hp_datum")),
    ("model_compute_holder", lambda c: {"body": _ctx_get(c, "hp_body", "holder body"), "axis": _ctx_get(c, "hp_axis", "holder axis"), "end_datum": _ctx_get(c, "hp_datum", "holder datum")}, _holder_computed, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
]

# ACT 7b: NESTING - model_arrange as a FUNCTION of its boundary. It runs after the
# parametric resize on purpose: the solver restructures the parts it nests under new
# Envelope occurrences, and that is not a thing to hand to an act that recomputes the
# whole assembly.
_NESTING = _box("ArrP1", ox=200, oy=350) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # THREE DIFFERENT shapes to nest, not two of the same box: a square pad, a long bar and a disc.
    # A nest that only ever sees one footprint proves nothing about the solver.
    ("model_create_component", {"name": "ArrP2", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ArrP2S"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 260, "y1": 350, "x2": 320, "y2": 368,
                             "sketch_name": "ArrP2S"}, "ok", None),
    ("model_extrude", {"sketch_name": "ArrP2S", "profile_index": 0, "distance": 10}, _extruded, None),
    ("model_create_component", {"name": "ArrP3", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ArrP3S"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 350, "cy": 359, "radius": 16,
                             "sketch_name": "ArrP3S"}, "ok", None),
    ("model_extrude", {"sketch_name": "ArrP3S", "profile_index": 0, "distance": 10}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # a SECOND bar, so the nest carries repeats as well as variety - four shapes from three
    # components. The instance number is Fusion's to pick, so it is read back, never predicted.
    ("design_add_instance", {"component": "ArrP2", "x": 0, "y": -35, "units": "mm"},
     lambda p: p.get("created") is True and str(p.get("full_path", "")).startswith("ArrP2:"),
     ("arr_bar2", lambda p: p["full_path"])),
    # The boundary is a HEXAGON, not a rectangle: a true-shape nest against a slanted wall is the
    # case a box boundary cannot show. Its six lines are line:0..line:5, which is what the reshape
    # below scales.
    ("sketch_create", {"plane": "xy", "name": "ArrB"}, "ok", None),
    ("sketch_add_geometry", {"kind": "polygon", "cx": 300, "cy": 500, "radius": 120, "sides": 6,
                             "sketch_name": "ArrB"},
     lambda p: p.get("curves_added") == 6, None),
    _watch(["ArrB", "ArrP1:1", "ArrP2:1", "ArrP3:1"]),
    # TRUE-SHAPE, because the boundary is a hexagon: the rectangular solver nests bounding boxes and
    # refuses a non-rectangular envelope outright (ARRANGE_ERROR_ENVELOPE_INVALIDRECTANGULAR), which
    # is exactly what a slanted wall is for. The solver places COPIES under an Envelope occurrence
    # and leaves the named inputs where they were, so the nested part is picked out of what the call
    # PUBLISHED - and it is that copy the reshape below is measured on.
    ("model_arrange", lambda c: {"boundary_sketch": "ArrB",
                                 "shapes": ["ArrP1:1", "ArrP2:1",
                                            _ctx_get(c, "arr_bar2", "the second bar"), "ArrP3:1"],
                                 "solver": "true_shape", "spacing": 5},
     _arranged(4), ("nest_disc", lambda p: next(o for o in p["new_occurrences"]
                                                if "+ArrP3:" in o))),
    ("model_inspect", lambda c: {"target": _ctx_get(c, "nest_disc", "the nested disc")},
     _extent_measured, ("nest_y0", _recall("nest_y0", lambda p: p["center"]["y"]))),
    _dwell(2.0),
    # RESHAPE the boundary: the Arrange feature RECOMPUTES off its boundary sketch, so the nest is a
    # FUNCTION of the envelope rather than a one-time placement. Solving again would not show this -
    # a second identical arrange stacks another coincident copy set (measured, and the tool says so).
    # The proof is the nested disc having MOVED, read back off its own bounding box.
    ("sketch_move", {"sketch_name": "ArrB",
                     "entities": "line:0,line:1,line:2,line:3,line:4,line:5",
                     "scale_factor": 0.65, "center_x": 300, "center_y": 500},
     lambda p: len(p.get("moved_entities") or []) == 6 and not p.get("unmoved_entities"), None),
    ("model_inspect", lambda c: {"target": _ctx_get(c, "nest_disc", "the nested disc")},
     lambda p: _measured("the nest re-solved off the smaller boundary",
                         {"y_before": _RECALL.get("nest_y0"),
                          "y_now": p.get("center", {}).get("y")},
                         abs(p["center"]["y"] - _RECALL["nest_y0"]) > 1.0), None),
    _dwell(2.0),
]


# ACT 7: MESH - a scratch solid becomes a mesh, then the mesh family works it (one mesh per op).
_MESH = [
    ("model_create_component", {"name": "Msh", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "MshS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 200, "y1": 300, "x2": 220, "y2": 320, "sketch_name": "MshS"}, "ok", None),
    # FRAME BEFORE THE FIRST BODY, on the sketch that is about to become one. The automatic camera
    # row lands on a chunk's first body, which means the body appears while the camera is still on
    # whatever the previous act was doing - and this act's sketch was drawn back in the sketch phase,
    # so nothing has brought the camera here since. Framing the sketch first is what makes the mesh
    # source appear IN shot instead of somewhere off screen.
    _watch("MshS"),
    ("model_extrude", {"sketch_name": "MshS", "profile_index": 0, "distance": 10}, _extruded, None),
    _watch("Msh:1"),
    ("find_geometry", {"target": "Msh", "kind": "planar_face", "nearest_to": [210, 310, 10], "max_results": 1}, "ok", _fg("msh_body")),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "MshMid"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "xy", "name": "MshCyl"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 260, "cy": 360, "radius": 15, "sketch_name": "MshCyl"}, "ok", None),
    ("model_extrude", {"sketch_name": "MshCyl", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "Msh", "kind": "cylinder_face", "nearest_to": [260, 360, 10], "max_results": 1}, "ok", _fg("cyl_body")),
    # Every mesh below is cast from a body inside Msh, so the camera goes there and STAYS there for
    # the whole family - a mesh is created, reduced, remeshed, cut and smoothed without any step
    # that makes a sketch or a component, so nothing in the framing pass would otherwise move it.
    _watch("Msh:1"),
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
    # 'density' is set-then-read-back off the input (a build that drops it refuses), and the
    # before/after triangle counts are read off the model - 'changed' is null when either count
    # could not be read at all, which is a remesh nothing was measured about.
    ("mesh_remesh", {"mesh": "MC", "density": 1},
     lambda p: p.get("density_applied") == 1 and (p["after"]["triangle_count"] or 0) > 0
     and p.get("changed") is not None, None),
    # the cut's own effect evidence, mirrored into the receipt: the triangle count moved, or (a
    # fill that replaces as many triangles as it removed) the mesh's area/volume did.
    _watch("Msh:1"),
    ("mesh_plane_cut", {"mesh": "MD", "plane": "MshMid", "cut_type": "trim"},
     lambda p: p.get("fill") == "minimal" and p.get("triangles_before") and p.get("triangles_after")
     and (p["triangles_after"] != p["triangles_before"]
          or p.get("volume_after_cm3") != p.get("volume_before_cm3")), None),
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
    # execute() answers true while writing nothing, so the file's own size is the read-back - and
    # it is the same file the mesh_insert beat below re-imports.
    ("mesh_export", {"target": "MA", "file_path": EXPORT_DIR + "/eval_mesh", "format": "stl"},
     lambda p: (p.get("size_bytes") or 0) > 0 and p.get("file_exists") is True, None),
    # THE RE-IMPORT GETS ITS OWN COMPONENT, and the mesh act goes back to Msh afterwards.
    # mesh_insert does NOT land the mesh where the file's own geometry sits: measured on the live
    # document, a mesh exported from (770,875) came back at (30,34) - near the world origin, a metre
    # from every other body in Msh. Inside Msh that one stray body stretched the component's
    # bounding box to 815 x 916 mm, so every camera row framing 'Msh:1' fitted THAT instead of the
    # 75 mm of mesh work, and the whole mesh act was watched from the far zoom.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "MshIn", "activate": True}, _made_component, None),
    # units='mm' because that is what the FILE holds: the mesh_export beat above named no unit, and
    # mesh_export's stl_units default is mm. Stated rather than left to mesh_insert's own default,
    # so the round trip below pins mesh_export's default alone - reading it back at mesh_insert's
    # default too would pass on any pair of defaults that happen to match. Importing the file
    # at the wrong unit divides every coordinate by 25.4, and a mesh a fortieth of its size lands a
    # fortieth of its distance from the origin too - measured, near the world origin and inside
    # whatever is parked there, not out on the field where it was exported from. The size read-back
    # below is what makes that a failure instead of a surprise.
    ("mesh_insert", {"file_path": EXPORT_DIR + "/eval_mesh.stl", "name": "MshIns",
                     "units": "mm"}, "ok", None),
    # the round trip measured END TO END: the re-imported mesh is the size of the mesh that was
    # written - measured 74.99 x 74.99 x 20.0 on a finished run. A size, not a position: the layout
    # moves the bench, and 25.4 is the only thing this is looking for.
    ("model_inspect", {"target": "MshIns"}, _mesh_round_trip(75.0, 20.0), None),
    ("design_activate_component", {"occurrence": "Msh:1"}, "ok", None),
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
    # the rest of the rebuild vocabulary on the same mesh - each method re-triangulates it a
    # different way, and each row reads its own density back off the feature's ModelParameter rather
    # than echoing the request, so a method that quietly fell back to another is visible.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild",
                     "rebuild_method": "preserve_sharp_edges", "density": 40},
     _rebuilt("preserve_sharp_edges", 40.0), None),
    # 'offset' is accepted by the accurate method ALONE - it is the deviation that method solves to.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "accurate",
                     "density": 48, "offset": 0.2}, _rebuilt("accurate", 48.0), None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "blocky",
                     "density": 24}, _rebuilt("blocky", 24.0), None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "adaptive",
                     "density": 32}, _rebuilt("adaptive", 32.0), None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild",
                     "rebuild_method": "adaptive_preserve_sharp_edges", "density": 32},
     _rebuilt("adaptive_preserve_sharp_edges", 32.0), None),
    # the offset/method pairing, refused rather than dropped, on the method it does not belong to.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "blocky",
                     "density": 24, "offset": 0.2}, "refused", None),
    # 'wrap' shrink-wraps the mesh closed. It is not a rebuild, so it takes none of the rebuild
    # knobs - handing it one is refused by name.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "wrap"},
     lambda p: p.get("repaired") is True and p.get("repair_type") == "wrap", None),
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
    # Fusion writes a nested path with '+' ("OuterRing:1+InnerRing:1"), so the leaf is what the
    # membership test wants - splitting on '/' alone leaves every nested part unmatched and quietly
    # exempts it from the gate.
    for i in p.get("measured", {}).get("interferences", []):
        a = (i.get("occurrence_one") or "").replace("/", "+").split("+")[-1]
        b = (i.get("occurrence_two") or "").replace("/", "+").split("+")[-1]
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
    # ONLY what the vise would be built THROUGH. The vise occupies x[-70,70] y[-52,52] around the
    # Carrier, so the mechanism sharing that ground has to go; every other cameo sits in a cell of
    # its own out on the field (the layout pass guarantees it, and the layout lint enforces it), so
    # deleting those cleared nothing and only made the run spend a second each on them.
    ("design_delete_occurrence", {"occurrence": occ}, "ok", None)
    for occ in ("Crank:1",
                "InnerRing:1",       # takes the nested RotorShaft and Rotor with it
                "OuterRing:1",
                "Frame:1",           # takes the nested Pedestal with it
                "FeatureCameo:1")
] + [
    # the root Skeleton sketch (the construction axis crosses) goes too - the fixture scene
    # shows the PART, not the build scaffolding.
    ("design_delete_feature", {"feature": "Skeleton"}, "ok", None),
    # the survivor, alone and still parametric.
    ("model_inspect", {"target": "Carrier:1"}, _extent_measured, None),
    ("workspace_orient", {}, "ok", None),
    _watch("Carrier:1"),
]


# ACT 9: VISE FIXTURE - the eval-proven self-centering vise modeled around the Carrier.
# Geometry contract (all mm, Carrier occupies x[-50,50] hub y[-14,14] z[-32,-26]):
#   STOCK    x[-55,55] y[-18,18] z[-41,-23]  - real margin all around (the adaptive's material)
#   ViseBase x[-70,70] y[-52,52] z[-69,-49]
#   Jaw seat z=-49..-41 (the stock's underside rests level with the seat ledge)
#   Jaw grip faces OPEN at y=-/+21; stock sides at y=-/+18 -> each jaw closes 3mm to contact
#   Jaw lips top out at z=-34, and the CARRIER inside the stock spans z[-32,-26] - so the part
#   being machined stands entirely ABOVE the jaws. That is the whole reason the stock is 18 mm
#   thick rather than 12: a cutter reaching a part level with the jaw tops fouls the fixture, so
#   the grip is taken low on the billet and every machined surface is clear above it.

def _plate(comp, sketch, z_offset, x1, y1, x2, y2, height):
    """Component + its own build plane at z_offset + one rectangle, extruded up by height.
    The plane lives INSIDE the component: sketch_create resolves construction-plane names in
    the ACTIVE component, so a root-level plane is invisible after activate."""
    plane = comp + "Floor"
    return [
        ("model_create_component", {"name": comp, "activate": True}, _made_component, None),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": z_offset,
                                "name": plane}, _datum_plane("xy"), None),
        ("sketch_create", {"plane": plane, "name": sketch}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                 "sketch_name": sketch}, "ok", None),
        ("model_extrude", {"sketch_name": sketch, "profile_index": 0, "distance": height},
         _extruded, None),
    ]


def _second_plate(comp, sketch, z_offset, x1, y1, x2, y2, height):
    """A second stacked extrude inside the ACTIVE component (the jaw's upper gripping lip)."""
    plane = comp + "LipPlane"
    return [
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": z_offset,
                                "name": plane}, _datum_plane("xy"), None),
        ("sketch_create", {"plane": plane, "name": sketch}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                 "sketch_name": sketch}, "ok", None),
        ("model_extrude", {"sketch_name": sketch, "profile_index": 0, "distance": height},
         _extruded, None),
    ]


_VISE = (
    # THE STOCK FIRST, dressed before any of the fixture exists. It is the billet the vise is built
    # AROUND, and its look has to be settled before the jaws are there to close on it - a stock that
    # changes appearance halfway through the clamping reads as the clamping doing it.
    _plate("STOCK", "StockS", -41, -55, -18, 55, 18, 18)
    + [
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("appearance_set", {"target": "STOCK", "color": "#8D6E63"}, "ok", None),
        # HALF translucent, so the Carrier inside stays visible through the billet it is cut from -
        # the whole point of the fixture shot is the part in the stock in the vise, and an opaque
        # billet hides the part. Opacity here is the browser's Opacity Control (Component.opacity),
        # NOT the appearance's transparency: the two are unrelated, and a fully opaque colour still
        # renders see-through under an opacity override. Read back off what actually RENDERS, since
        # the override is inherited from parent components.
        ("appearance_set", {"target": "STOCK:1", "opacity": 50},
         lambda p: p.get("opacity_rendered") == 50, None),
    ]
    + _plate("ViseBase", "VBase", -69, -70, -52, 70, 52, 20)
    # JawL: lower seat block up to the seat ledge (z=-35), then the gripping lip above it.
    + _plate("JawL", "JLseat", -49, -30, -37, 30, -15, 8)
    + _second_plate("JawL", "JLlip", -41, -30, -37, 30, -21, 7)
    + _plate("JawR", "JRseat", -49, -30, 15, 30, 37, 8)
    + _second_plate("JawR", "JRlip", -41, -30, 21, 30, 37, 7)
) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("appearance_set", {"target": "ViseBase", "color": "#455A64"}, "ok", None),
    ("appearance_set", {"target": "JawL", "color": "#E5533C"}, "ok", None),
    ("appearance_set", {"target": "JawR", "color": "#E5533C"}, "ok", None),
    # the fixture skeleton: base grounded, each jaw a NAMED slider on the base. Every component
    # here has its origin at the WORLD origin (geometry is drawn in world coordinates), so the
    # ':origin' snap aligns already-aligned frames - a positional no-op, no teleport - and the
    # slider axis 'y' rides the world-aligned frame.
    ("assembly_ground", {"occurrence": "ViseBase:1", "ground_to_parent": True}, _grounded, None),
    ("joint_create", {"occurrence_one": "JawL:1:origin", "occurrence_two": "ViseBase:1:origin",
                      "joint_type": "slider", "axis": "y", "name": "SlideL"}, _jointed("SlideL"), None),
    ("joint_create", {"occurrence_one": "JawR:1:origin", "occurrence_two": "ViseBase:1:origin",
                      "joint_type": "slider", "axis": "y", "name": "SlideR"}, _jointed("SlideR"), None),
    # the part in the stock, as-built rigid - the billet and what will be cut out of it are one
    # piece until the cutter says otherwise. The stock is NOT jointed to the base: a billet welded to
    # the vise body is not being held by anything, and the jaws closing on it would prove nothing.
    # What holds it is the grip, captured below once the jaws have actually closed.
    ("joint_create_as_built", {"occurrence_one": "Carrier:1", "occurrence_two": "STOCK:1"}, _as_built, None),
    # SELF-CENTERING: couple the two sliders at ratio -1 (on real sliders the platform accepts
    # slider-slider links - live-verified). A negative ratio is applied as a REVERSED coupling of
    # magnitude 1, which is what 'reversed' reads back.
    ("joint_motion_link", {"joint_one": "SlideL", "joint_two": "SlideR", "ratio": -1},
     _motion_linked("SlideL", "SlideR", True), None),
    # drive ONE jaw closed by its 3mm approach; the link must bring the OTHER jaw in too.
    ("joint_drive", {"joint_name": "SlideL", "distance": 3}, _driven_slide(3), None),
    # GRIP VERIFIED BY MEASURE, not by trust: each jaw's gripping face touches its stock side.
    ("find_geometry", {"target": "JawL", "kind": "planar_face", "nearest_to": [0, -18, -37],
                       "max_results": 1}, "ok", _fg("jawL_grip")),
    ("find_geometry", {"target": "JawR", "kind": "planar_face", "nearest_to": [0, 18, -37],
                       "max_results": 1}, "ok", _fg("jawR_grip")),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "jawL_grip", "JawL grip face"),
                                         "b": "STOCK"},
     lambda p: p.get("distance", 99) <= 0.1, None),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "jawR_grip", "JawR grip face"),
                                         "b": "STOCK"},
     lambda p: p.get("distance", 99) <= 0.1, None),
    # A DRIVE LEAVES A TRANSIENT POSE, and a joint create is REFUSED while one is pending (it would
    # silently revert it). So the clamped pose is recorded into the timeline first - which is also
    # the right order physically: the jaws are closed, that closure is what the grip joint captures.
    ("assembly_capture_position", {"action": "capture"}, _captured, None),
    # THE GRIP ITSELF, captured only now that both faces measure closed onto the billet: an as-built
    # joint mates two occurrences WHERE THEY ALREADY ARE, so taking it here records the clamped pose
    # rather than creating it. ONE jaw takes the joint - the slider and its motion link are what
    # bring the other side in, and jointing the stock to both jaws would close the loop twice and be
    # refused as over constrained (measured).
    ("joint_create_as_built", {"occurrence_one": "STOCK:1", "occurrence_two": "JawL:1",
                               "name": "GripL"},
     lambda p: _as_built(p) and _measured("the grip joint names the jaw it was taken on",
                                          {"joint": p.get("joint")}, p.get("joint") == "GripL"), None),
    ("assembly_inspect_interference", {}, _interference_measured, None),
    _watch(["ViseBase:1", "STOCK:1"]),
    ("view_screenshot", {"width": 500, "height": 400}, "ok", None),
]


# cam_reorder's ok payload is moved/position/reference - the three arguments the call was handed,
# echoed back. The one thing the call establishes is that Fusion ALLOWED the move (a moveBefore
# returning false is an error), and a bare "ok" carries that in full - so a predicate over those
# keys would add no evidence while moving the tool into the bucket the receipt calls evidence-
# carrying. Reading the order back needs its own cam_get(include=['operations']) step.
_REORDER_PARKED = ("payload echoes the request arguments; the move-allowed gate is what a bare ok "
                   "already proves - the ORDER needs a cam_get read-back step")


def _op_created(setup, strategy):
    """A created CAM operation. ONE key here is a read: 'operation' is op.name off the operation
    the platform added, read after the setup's own count went up. 'setup' and 'strategy' are the
    call's own arguments echoed back, and 'generation_started' is a constant False on this branch -
    they are asserted because a payload that mismatches the request is a wrong payload, not because
    either was measured."""
    def check(p):
        return _measured(f"a '{strategy}' operation created in '{setup}'",
                         {"operation": p.get("operation"), "setup": p.get("setup"),
                          "strategy": p.get("strategy"),
                          "generation_started": p.get("generation_started")},
                         bool(p.get("operation")) and p.get("setup") == setup
                         and p.get("strategy") == strategy
                         and p.get("generation_started") is False)
    return check


def _toolpath_shown(action, key, fit=False):
    """One operation's toolpath displayed: the operation NAME the tool resolved (compared with the
    name the PLATFORM published when the op was created - a literal here would be a guess) and
    whether the camera fit applied ('fit' reads true only after the viewport call returned)."""
    def check(p):
        want = _RECALL.get(key)
        return _measured(f"{action} the operation created as {want!r}",
                         {"action": p.get("action"), "operation": p.get("operation"),
                          "fit": p.get("fit")},
                         p.get("action") == action and want is not None
                         and p.get("operation") == want and p.get("fit") is fit)
    return check


# ACT 10a: CAM on the REAL part in the REAL fixture - job built and generated. The scratch-stock
# rows remain as this act's fallback, so the CAM family stays covered when the story world
# could not build.
_CAM_STORY = [
    # the machining region: the part seated in the vise, which is what every CAM beat acts on.
    _watch("STOCK:1"),
    # a scratch sketch inside the Carrier footprint, drawn while Design is still the active
    # workspace: the geometry the 'sketch' selection takes, which is a WHOLE sketch (SketchSelection
    # accepts sketches, not their curves and not their profiles).
    ("sketch_create", {"plane": "xy", "name": "CamContourSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 10,
                             "sketch_name": "CamContourSketch"}, "ok", None),
    ("view_switch_workspace", {"workspace": "manufacture"}, "ok", None),
    # CAM is looked at, not sketched in: drop the sketch clutter design-wide for the whole
    # machining movement. The FOLDER bulb, so no entity's own visibility is disturbed and a
    # sketch the CAM selection already holds by name is unaffected. Put back in the FINALE.
    ("view_set", {"action": "display", "categories": ["sketches"], "visible": False},
     lambda p: p.get("visible") is False, None),
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
    # THE READ SIDE of the same library: the summary census, the same census NARROWED by tool type
    # (the filter has to actually drop rows, not just be echoed back), and one tool's full parameter
    # list - the deeper read the summary points at.
    ("cam_edit_tools", {"action": "list", "scope": "document"},
     lambda p: p.get("tool_count", 0) >= 4 and "filtered_by_type" not in p,
     ("doc_tool_count", _recall("doc_tool_count", lambda p: p["tool_count"]))),
    ("cam_edit_tools", {"action": "list", "scope": "document", "tool_type": "drill"},
     lambda p: p.get("filtered_by_type") == "drill" and p.get("tool_count", 0) >= 1
     and all("drill" in str(t.get("type") or "").lower() for t in p["tools"]), None),
    ("cam_edit_tools", {"action": "parameters", "scope": "document", "tool": 0},
     lambda p: p.get("tool") == 0 and p.get("parameter_count", 0) > 0
     and all(r.get("name") for r in p["parameters"]), None),
    # the LOCAL scope with no 'library' named lists the libraries THERE, not tools - the same action
    # answering a different question because the scope changed.
    ("cam_edit_tools", {"action": "list", "scope": "local"},
     lambda p: p.get("scope") == "local" and isinstance(p.get("libraries"), list), None),
    # the document library is the document's own and cannot host a NEW one - refused by name rather
    # than quietly creating it somewhere else.
    ("cam_edit_tools", {"action": "create_library", "scope": "document", "library": "SweepLib"},
     "refused", None),
    # a fifth tool added and taken straight back out. 'remove' renumbers everything after the index
    # it takes, so it takes the LAST one - behind every tool the operations below select by number -
    # and the library's own count is what says the removal landed.
    ("cam_edit_tools", {"action": "add", "scope": "document",
                        "add_tools": [{"from_type": "ball end mill"}]}, "ok", None),
    ("cam_edit_tools", lambda c: {"action": "remove", "scope": "document",
                                  "remove_indices": [_ctx_get(c, "doc_tool_count",
                                                              "the document tool count")]},
     lambda p: p.get("removed") == 1, None),
    ("cam_edit_tools", lambda c: {"action": "list", "scope": "document"},
     lambda p: p.get("tool_count") == _RECALL.get("doc_tool_count"), None),
    # the setup lands under the name it was asked for (read back off the created Setup, and it is
    # re-listed before the payload is built), holding the model it was given and no operations yet.
    ("cam_create_setup", {"models": ["Carrier"], "name": "DemoSetup"},
     lambda p: p["created"] is True and p["setup_name"] == "DemoSetup"
     and p["operation_type"] == "milling" and p["model_count"] >= 1
     and p["operation_count"] == 0, None),
    # the REAL stock solid and the REAL fixture bodies - the shop-template selection shape.
    ("cam_edit_setup", {"setup": "DemoSetup", "stock": ["STOCK"],
                        "fixtures": ["ViseBase", "JawL", "JawR"]}, "ok", None),
    # THE ASSOCIATIVE SEAM ON CAMERA: a Joint Origin at the real stock's center becomes the
    # setup WCS; the bound_entities read-back lands in ctx as the receipt's evidence.
    ("joint_create_origin", {"anchor": "bbox_center", "bbox_target": "STOCK:1",
                             "orient_axis": "z", "name": "StockWCS"},
     _joint_origin_computed("StockWCS"), None),
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
                              "tool_scope": "document", "tool_index": 0, "generate": False},
     _op_created("DemoSetup", "face"),
     ("face_op", _recall("face_op", lambda p: p["operation"]))),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "adaptive",
                              "tool_scope": "document", "tool_index": 0, "generate": False},
     _op_created("DemoSetup", "adaptive"),
     ("adaptive_op", _recall("adaptive_op", lambda p: p["operation"]))),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "contour2d",
                              "tool_scope": "document", "tool_index": 0, "generate": False},
     _op_created("DemoSetup", "contour2d"),
     ("contour_op", _recall("contour_op", lambda p: p["operation"]))),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "drill",
                              "tool_scope": "document", "tool_index": 1, "generate": False},
     _op_created("DemoSetup", "drill"),
     ("drill_op", _recall("drill_op", lambda p: p["operation"]))),
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
    # the feed edit is read BACK off the parameter: 'after' is the expression the platform stored,
    # which a set that did not take leaves at the tool's default.
    ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}},
     lambda p: p["edited"] is True and p["updated_count"] == 1
     and p["changed"][0]["name"] == "tool_feedCutting"
     and "1200" in str(p["changed"][0]["after"]), None),
    ("cam_reorder", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op"),
                               "position": "before", "reference": "Face1"},
     Parked(_REORDER_PARKED), None),
    # 'activated' is Setup.name read back AFTER the isActive gate - the tool errors when the setup
    # reads inactive, so the name here is the setup that actually became active.
    ("cam_activate_setup", {"setup": "DemoSetup"},
     lambda p: p["activated"] == "DemoSetup", None),
    # two DIFFERENT strategies must differ somewhere: a zero-difference diff would mean the two
    # names resolved to one operation. Both names are read back off the resolved operations.
    ("cam_compare_operations", lambda c: {"operation_a": "Face1",
                                          "operation_b": _ctx_get(c, "adaptive_op",
                                                                  "the created adaptive op")},
     lambda p: p["operation_a"] == _RECALL.get("face_op")
     and p["operation_b"] == _RECALL.get("adaptive_op")
     and p["difference_count"] >= 1 and all(d["parameter"] for d in p["differences"]), None),
    # FOLDERS: organize the job the way a shop sheet reads - milling vs drilling.
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Milling"},
     lambda p: p["created"] is True and p["folder"] == "Milling" and p["setup"] == "DemoSetup",
     None),
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Drilling"},
     lambda p: p["created"] is True and p["folder"] == "Drilling" and p["setup"] == "DemoSetup",
     None),
    # 'moved' counts only the moveInto calls that returned true - the first one that does not is an
    # error naming what had already moved, so the count IS the operations that landed in the folder.
    ("cam_edit_folders", lambda c: {"action": "move", "setup": "DemoSetup", "folder": "Milling",
                                    "operations": ["Face1",
                                                   _ctx_get(c, "adaptive_op",
                                                            "the created adaptive op"),
                                                   "2D Contour1"]},
     lambda p: p["moved"] == 3 and p["into"] == "Milling" and len(p["operations"]) == 3, None),
    ("cam_edit_folders", {"action": "move", "setup": "DemoSetup", "folder": "Drilling",
                          "operations": ["Drill1"]},
     lambda p: p["moved"] == 1 and p["into"] == "Drilling", None),
    ("cam_show_toolpath", {"action": "list"},
     lambda p: p["action"] == "list" and p["operation_count"] >= 4
     and len(p["operations"]) == p["operation_count"]
     and all(r["op"] for r in p["operations"]), None),
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
    # 'target' is the RESOLVED node's kind beside the name asked for, so it is what says the name
    # reached a setup rather than an operation of the same name; the handle is what the poll below
    # would read, and skip_valid is the flag this launch actually ran under.
    ("cam_generate", {"target": "DemoSetup", "skip_valid": False},
     lambda p: p["launched"] is True and p["target"] == "setup 'DemoSetup'"
     and p["skip_valid"] is False and bool(p["handle"]), None),
    # generation completion is gated by the bounded poll run() performs after this act (an
    # errored op or an EMPTY toolpath - a 'valid' op that cuts nothing - fails the run).
]

def _tmpl_names(node):
    """Every template NAME in a cam_get(include=['templates']) tree, folders recursed - the witness
    a template teardown is read against, since the slice reports a folder tree rather than a flat
    list."""
    names = [t.get("name") for t in (node.get("templates") or [])]
    for sub in (node.get("folders") or []):
        names.extend(_tmpl_names(sub))
    return names


# ACT 10b: CAM read-back + deliverables on the generated job - toolpath shown, NC posted,
# template saved and re-applied.
_CAM_DELIVER = [
    # the validity verdict flips true once generation completed (the act boundary's poll certified
    # it); the not-valid breakdown is empty.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"},
     lambda p: p["passed"] is True and p["measured"]["not_valid"] == [], None),
    # the tally's scope is an INPUT: include_suppressed=true widens it back to every operation.
    # tolerance_used names the tally's set and the VERDICT's set separately because they differ -
    # CAM's own check counts suppressed operations whatever this flag says - so a scoped call whose
    # verdict came from checkToolpath reports the verdict as covering all of them either way.
    # Nothing is suppressed yet, so this pins the flag's plumbing; the FILTERING itself is exercised
    # at the end of this act, where a real suppression exists to filter.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup", "include_suppressed": True},
     lambda p: p["tolerance_used"]["tally_counts"] == "all_operations"
     and p["tolerance_used"]["verdict_counts"] == "all_operations"
     and p["measured"]["suppressed_excluded"] == 0, None),
    ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}, "ok", None),
    # the reverse lookup, which only has an answer once operations exist: which of them use the mill
    # this job was cut with. It is document-scope ONLY - a shared library has no operations - so the
    # local scope is refused rather than answered with an empty list that would read as "none use
    # it". The turning tool, added for the from_type census and never selected, is the other half:
    # its own answer must be zero, or 'where_used' is not looking at operations at all.
    ("cam_edit_tools", {"action": "where_used", "scope": "document", "tool": 0},
     lambda p: p.get("tool") == 0 and p.get("operation_count", 0) >= 1
     and len(p.get("operations") or []) == p["operation_count"], None),
    ("cam_edit_tools", {"action": "where_used", "scope": "document", "tool": 2},
     lambda p: p.get("operation_count") == 0 and "not used" in (p.get("note") or ""), None),
    ("cam_edit_tools", {"action": "where_used", "scope": "local", "tool": 0}, "refused", None),
    # THE TOOLPATH REVEAL. Every path off, then each strategy alone and held long enough to watch -
    # face, adaptive, contour, drill - and finally all four together, left ON. Each is addressed by
    # the name the platform PUBLISHED at create time, through ctx: the default name of an operation
    # is Fusion's to pick, so a literal here would be a guess.
    # Every generated path off first: hidden_count counts the bulbs that read back false, and a
    # bulb that did not take is reported as a toggle_failure instead of being counted.
    ("cam_show_toolpath", {"action": "hide_all"},
     lambda p: p["action"] == "hide_all" and p["hidden_count"] >= 1
     and "toggle_failures" not in p, None),
    _dwell(1.0),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "face_op", "the face op"),
                                     "fit": True}, _toolpath_shown("isolate", "face_op", fit=True), None),
    ("view_screenshot", {"width": 500, "height": 400}, "ok", None),
    _dwell(2.5),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "adaptive_op", "the adaptive op"),
                                     "fit": True}, _toolpath_shown("isolate", "adaptive_op", fit=True), None),
    _dwell(2.5),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "contour_op", "the contour op"),
                                     "fit": True}, _toolpath_shown("isolate", "contour_op", fit=True), None),
    _dwell(2.5),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "drill_op", "the drill op"),
                                     "fit": True}, _toolpath_shown("isolate", "drill_op", fit=True), None),
    _dwell(2.5),
    # all four on together - the machined part as the act leaves it.
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "face_op", "the face op")},
     _toolpath_shown("show", "face_op"), None),
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "adaptive_op", "the adaptive op")},
     _toolpath_shown("show", "adaptive_op"), None),
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "contour_op", "the contour op")},
     _toolpath_shown("show", "contour_op"), None),
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "drill_op", "the drill op")},
     _toolpath_shown("show", "drill_op"), None),
    _dwell(3.0),
    # the deliverable itself: the tool errors unless a non-stub file LANDED, so the payload's file
    # rows are the proof - each one stat'd on disk - and 'scope' is the resolved node's kind.
    ("cam_post", {"scope": "DemoSetup", "post": "haas", "post_scope": "local",
                  "output_folder": EXPORT_DIR + "/nc", "program_name": "1001"},
     lambda p: p["posted"] is True and p["scope"] == "setup" and p["program_name"] == "1001"
     and p["file_count"] == len(p["files"]) and p["file_count"] >= 1
     and all(f["size_bytes"] > 0 for f in p["files"]), None),
    # the sheet file must LAND (the API's bool answers before the async write completes)
    ("cam_generate_setup_sheet", {"scope": "DemoSetup", "output_folder": EXPORT_DIR + "/sheets"},
     lambda p: p.get("generated") is True and p.get("size_bytes", 0) > 0, None),
    # comment_after is the parameter re-read after the write, per program - the value the G-code
    # header will carry, not the value the call was handed.
    ("cam_set_nc_comment", {"comment": "GYRO sweep"},
     lambda p: p["set"] is True and p["programs_changed"] >= 1
     and all(r["comment_after"] == "GYRO sweep" for r in p["programs"]), None),
    # the saved template names the operation it was bundled from (read off the Operation objects the
    # names resolved to) and carries the url a template loaded back from - the tool refuses the save
    # when nothing loads from what importTemplate returned.
    ("cam_save_template", {"template_name": TEMPLATE_NAME, "setup": "DemoSetup",
                           "operations": "Face1", "location": "local"},
     lambda p: p["saved"] is True and p["template"] == TEMPLATE_NAME
     and p["operation_count"] == 1 and p["operations"] == [_RECALL.get("face_op")]
     and bool(p["template_url"]), None),
    ("cam_create_setup", {"models": ["Carrier"], "name": "Setup2"},
     lambda p: p["created"] is True and p["setup_name"] == "Setup2"
     and p["operation_count"] == 0, None),
    # Setup2 holds NO operations at this instant - the template lands on it in the next beat. A
    # setup with zero operations is the one CAM.checkToolpath raises on, so this is the document
    # -level read taken with an empty setup present: it must ANSWER, carrying the disclosure key
    # for what it left out, instead of failing the whole read on the one empty setup. The count
    # itself is not asserted - it is only non-zero when checkAllToolpaths raised and the per-setup
    # fallback ran, which is the document's business, not this beat's.
    ("cam_inspect_toolpaths", {},
     lambda p: isinstance(p["passed"], bool) and "empty_setups_excluded" in p["measured"], None),
    # operations_added is the setup's OWN allOperations count across the apply - the read the tool
    # refuses on when it does not rise - beside the template and setup names read off the resolved
    # objects, which is what says the by-name search reached the template this run saved.
    ("cam_apply_template", {"setup": "Setup2", "template_name": TEMPLATE_NAME,
                            "location": "local", "generate": "skip"},
     lambda p: p["applied"] is True and p["template"] == TEMPLATE_NAME
     and p["setup"] == "Setup2" and (p["operations_added"] or 0) >= 1, None),
    # entity_type is the resolved node's kind: it is what says an OPERATION went, not the setup or
    # folder a shared name could have reached.
    ("cam_delete", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op")},
     lambda p: p["deleted"] is True and p["entity"] == _RECALL.get("adaptive_op")
     and p["entity_type"] == "operation", None),
    # SUPPRESSION, last of the job edits: the flag is a WRITE here, and it is what gives
    # include_suppressed's FILTERING its live reading. It sits after the post, the setup sheet and
    # the template because suppressing DISCARDS the operation's toolpath - here that costs no later
    # beat - and the restore below carries LITERAL arguments and an end-state predicate, so it runs
    # and passes whatever the three steps in between did.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"},
     lambda p: p["measured"]["suppressed_excluded"] == 0
     and p["measured"]["states"]["suppressed"] == 0,
     ("active_ops_before", _recall("active_ops_before",
                                   lambda p: p["measured"]["states"]["total"]))),
    ("cam_edit_operation", {"operation": "Drill1", "suppressed": True},
     lambda p: p["is_suppressed"] is True and p["was_suppressed"] is False
     and p["had_toolpath"] is True and p["has_toolpath"] is False, None),
    # the FILTERED read: one operation fewer in the tally than the baseline counted, the suppressed
    # bucket empty because the suppressed op was left OUT of the tally, and the excluded count
    # naming what it left out.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"},
     lambda p: p["measured"]["suppressed_excluded"] == 1
     and p["measured"]["states"]["suppressed"] == 0
     and p["measured"]["states"]["total"] == _RECALL.get("active_ops_before") - 1
     and p["tolerance_used"]["tally_counts"] == "active_operations", None),
    # the same read WIDENED: every operation back in the tally, the suppressed one counted in its
    # own bucket. The pair is the filter - one flag, two different sets over one job.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup", "include_suppressed": True},
     lambda p: p["measured"]["states"]["total"] == _RECALL.get("active_ops_before")
     and p["measured"]["states"]["suppressed"] == 1
     and p["measured"]["suppressed_excluded"] == 0, None),
    ("cam_edit_operation", {"operation": "Drill1", "suppressed": False},
     lambda p: p["is_suppressed"] is False, None),
    # TEARDOWN of the two things this run leaves outside the document. The guard first, on the asset
    # while it still exists: a confirm_name that does not match the resolved name is refused. Then
    # the delete, judged on its own read-backs, and the library read as the independent witness: the
    # read that lists the asset when it arrives must not list it now. Both sit after every beat that
    # USES them - the machine after the post, the template after cam_apply_template - so a delete
    # that fails costs no earlier step.
    #
    # The TEMPLATE is saved and deleted inside this one list. The MACHINE is not: it is created in
    # ACT 10a's narrative and deleted here in ACT 10b's, and the two acts route on INDEPENDENT
    # preconditions - so a run that takes 10a's narrative and 10b's fallback leaves the machine in
    # the Local library with nothing to remove it. The run stamp BOUNDS that leak rather than
    # closing it: what is left behind is one uniquely-named machine, which no later run collides
    # with. A delete beat in the fallback list cannot close it either - when 10a fell back too,
    # nothing was ever created, so one step would have to be a refusal on one route and a success on
    # the other, and a step written to accept both asserts nothing.
    ("cam_delete_machine", {"name": MACHINE_NAME, "confirm_name": "NotThisMachine"},
     "refused", None),
    ("cam_delete_machine", {"name": MACHINE_NAME, "confirm_name": MACHINE_NAME},
     lambda p: p["deleted"] is True and p["machine"] == MACHINE_NAME
     and p["resolves_after_delete"] is False, None),
    ("cam_get", {"include": ["machines"], "vendor": "SweepCo"},
     lambda p: not any(m["name"] == MACHINE_NAME for m in p["machines"]["machines"]), None),
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": "NotThisTemplate"},
     "refused", None),
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": TEMPLATE_NAME},
     lambda p: p["deleted"] is True and p["template"] == TEMPLATE_NAME
     and p["loads_after_delete"] is False and p["location"] == "local", None),
    ("cam_get", {"include": ["templates"], "template_location": "local"},
     lambda p: TEMPLATE_NAME not in _tmpl_names(p["templates"]["tree"]), None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export",
                       "target": "Carrier"}, "ok", None),
    # the SPLIT path writes one file per top-level occurrence, each through its OWN options object -
    # so the format knob is read back PER FILE: every files[] record carries its own
    # 'options_applied' (the value that LANDED on that file, null when it did not), beside the
    # top-level 'options_requested'. Every file must read the knob back true, not just the first.
    ("design_export", {"format": "stl", "file_path": EXPORT_DIR + "/gyro_split",
                       "split_by_component": True, "stl_binary": True},
     lambda p: p.get("exported") is True and p.get("split_by_component") is True
     and p.get("options_requested", {}).get("stl_binary") is True
     and bool(p.get("files")) and all((f.get("options_applied") or {}).get("stl_binary") is True
                                      for f in p["files"]), None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/gyro_export.step"}, _imported, None),
]


def poll_generation(rows, notes, setup, max_polls=40, valued=None):
    """Read cam_get_status until completed (bounded). Generation free-runs in the background and a
    status read returns immediately, so real wall-clock sits between reads. An errored op, an EMPTY
    toolpath (a 'valid' op that cuts nothing - the silent version of wrong), or an exhausted budget
    FAILs.

    Its pass reads VALUES off the payload (completed, live_states, empty_toolpaths) rather than call
    success, so the passing row registers cam_get_status in 'valued' - the receipt's covered bucket -
    the same way a STEPS value predicate does."""
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
                if valued is not None:
                    valued.add("cam_get_status")
            return
    rows.append(("cam_get_status", "FAIL", f"not complete after {max_polls} polls"))


# ACT 10a fallback: the scratch-stock milling job (kept whole so the CAM family stays covered
# when the story world could not build).
_CAM = (
    _box("GyroStock", ox=700)
    + [
        _watch("GyroStock:1"),
        ("view_switch_workspace", {"workspace": "manufacture"}, "ok", None),
        # CAM is looked at, not sketched in: drop the sketch clutter design-wide for the whole
        # machining movement. The FOLDER bulb, so no entity's own visibility is disturbed and a
        # sketch the CAM selection already holds by name is unaffected. Put back in the FINALE.
        ("view_set", {"action": "display", "categories": ["sketches"], "visible": False},
         lambda p: p.get("visible") is False, None),
        ("cam_get", {}, "ok", None),
        ("cam_edit_tools", {"action": "add", "scope": "document", "add_tools": [{"from_type": "flat end mill"}]}, "ok", None),
        ("cam_create_setup", {"models": ["GyroStock"], "name": "Setup1"},
         lambda p: p["created"] is True and p["setup_name"] == "Setup1"
         and p["operation_type"] == "milling" and p["model_count"] >= 1
         and p["operation_count"] == 0, None),
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
        # GimbalDia is created at 120 mm in ACT 1 (which always runs its narrative) and the resize
        # act puts it back to 120 - so this reads the driver at the value the story left it.
        # param_get publishes 'value' already in the parameter's own unit (mm here: 120, not 12 cm),
        # so it is read as-is - a x10 would size the stock at 300 mm for a 30 mm box.
        ("param_get", {"name": "GimbalDia"}, _param_read("GimbalDia", 120),
         ("gimbal_mm", lambda p: p["parameter"]["value"])),
        ("cam_edit_setup", lambda c: {"setup": "Setup1", "parameters": {
            "job_stockMode": "'fixedbox'",
            "job_stockFixedX": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 4),
            "job_stockFixedY": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 4),
            "job_stockFixedZ": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 8)}},
         "ok", None),
        # the face op's name is the platform's to pick here too, and two later beats address it -
        # so it rides ctx exactly as its adaptive sibling does.
        ("cam_create_operation", {"setup": "Setup1", "strategy": "face", "tool_scope": "document", "tool_index": 0, "generate": False},
         _op_created("Setup1", "face"),
         ("face_op", _recall("face_op", lambda p: p["operation"]))),
        # the adaptive's default name comes from the platform, so it rides ctx here too.
        ("cam_create_operation", {"setup": "Setup1", "strategy": "adaptive", "tool_scope": "document", "tool_index": 0, "generate": False},
         _op_created("Setup1", "adaptive"),
         ("adaptive_op", _recall("adaptive_op", lambda p: p["operation"]))),
        ("cam_get", {"include": ["operations"], "setup": "Setup1"}, "ok", None),
        ("find_geometry", {"target": "GyroStock", "kind": "planar_face", "nearest_to": [710, 10, 10], "max_results": 1}, "ok", _fg("cam_top")),
        ("cam_select_geometry", lambda c: {"operation": "Face1", "selection": "face", "handles": [_ctx_get(c, "cam_top", "cam top face")], "generate": False}, "ok", None),
        ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}},
         lambda p: p["edited"] is True and p["updated_count"] == 1
         and p["changed"][0]["name"] == "tool_feedCutting"
         and "1200" in str(p["changed"][0]["after"]), None),
        ("cam_edit_setup", {"setup": "Setup1", "models": ["GyroStock"]}, "ok", None),
        ("cam_edit_folders", {"action": "create", "setup": "Setup1", "name": "Folder1"},
         lambda p: p["created"] is True and p["folder"] == "Folder1"
         and p["setup"] == "Setup1", None),
        ("cam_reorder", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op"),
                                   "position": "before", "reference": "Face1"},
         Parked(_REORDER_PARKED), None),
        ("cam_activate_setup", {"setup": "Setup1"},
         lambda p: p["activated"] == "Setup1", None),
        ("cam_compare_operations", lambda c: {"operation_a": "Face1",
                                              "operation_b": _ctx_get(c, "adaptive_op",
                                                                      "the created adaptive op")},
         lambda p: p["operation_a"] == _RECALL.get("face_op")
         and p["operation_b"] == _RECALL.get("adaptive_op")
         and p["difference_count"] >= 1 and all(d["parameter"] for d in p["differences"]), None),
        ("cam_show_toolpath", {"action": "list"},
         lambda p: p["action"] == "list" and p["operation_count"] >= 2
         and len(p["operations"]) == p["operation_count"]
         and all(r["op"] for r in p["operations"]), None),
        ("cam_generate", {"target": "Setup1", "skip_valid": False},
         lambda p: p["launched"] is True and p["target"] == "setup 'Setup1'"
         and p["skip_valid"] is False and bool(p["handle"]), None),
        ("cam_get_status", {"target": "Setup1"}, "ok", None),
    ]
)

# ACT 10b fallback: deliverables on the scratch job.
_CAM_FB_DELIVER = [
    ("cam_post", {"scope": "Setup1", "post": "haas", "post_scope": "local", "output_folder": EXPORT_DIR + "/nc", "program_name": "1001"},
     lambda p: p["posted"] is True and p["scope"] == "setup" and p["program_name"] == "1001"
     and p["file_count"] == len(p["files"]) and p["file_count"] >= 1
     and all(f["size_bytes"] > 0 for f in p["files"]), None),
    ("cam_generate_setup_sheet", {"scope": "Setup1", "output_folder": EXPORT_DIR + "/sheets"},
     lambda p: p.get("generated") is True and p.get("size_bytes", 0) > 0, None),
    ("cam_set_nc_comment", {"comment": "GYRO sweep"},
     lambda p: p["set"] is True and p["programs_changed"] >= 1
     and all(r["comment_after"] == "GYRO sweep" for r in p["programs"]), None),
    ("cam_save_template", {"template_name": TEMPLATE_NAME, "setup": "Setup1", "operations": "Face1", "location": "local"},
     lambda p: p["saved"] is True and p["template"] == TEMPLATE_NAME
     and p["operation_count"] == 1 and p["operations"] == [_RECALL.get("face_op")]
     and bool(p["template_url"]), None),
    ("cam_create_setup", {"models": ["GyroStock"], "name": "Setup2"},
     lambda p: p["created"] is True and p["setup_name"] == "Setup2"
     and p["operation_count"] == 0, None),
    ("cam_apply_template", {"setup": "Setup2", "template_name": TEMPLATE_NAME, "location": "local", "generate": "skip"},
     lambda p: p["applied"] is True and p["template"] == TEMPLATE_NAME
     and p["setup"] == "Setup2" and (p["operations_added"] or 0) >= 1, None),
    ("cam_delete", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op")},
     lambda p: p["deleted"] is True and p["entity"] == _RECALL.get("adaptive_op")
     and p["entity_type"] == "operation", None),
    # the same teardown as the narrative branch, in the branch that saved the template: the guard
    # while the asset still exists, the delete on its own read-backs, then the library as witness.
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": "NotThisTemplate"},
     "refused", None),
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": TEMPLATE_NAME},
     lambda p: p["deleted"] is True and p["template"] == TEMPLATE_NAME
     and p["loads_after_delete"] is False, None),
    ("cam_get", {"include": ["templates"], "template_location": "local"},
     lambda p: TEMPLATE_NAME not in _tmpl_names(p["templates"]["tree"]), None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export", "target": "GyroStock"}, "ok", None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/gyro_export.step"}, _imported, None),
]

# --- the ACT program --------------------------------------------------------------------------
# (name, precondition, narrative, fallback). A precondition read that ERRORS routes the act to its
# fallback (its tools are still covered, each marked "(fallback fixture)"). None precondition = an
# opening/cameo act that always runs its narrative.
_ACT_PROGRAM = [
    ("ACT 0 - OVERTURE", None, _OVERTURE, None),
    # SKETCH. The parametric skeleton and every sketch the story builds on, then the sketch TOOLS -
    # trim, offset, pattern, dimension, constrain, text, the slot kinds - on scratch sketches of
    # their own. Both run before anything is solid, which is the order the work is done in.
    ("ACT 1 - SKETCH + PARAMETERS", None, _SKELETON, None),
    ("ACT 1b - SKETCH TOOLS", None, _SKETCHWORK, []),
    # CREATE. Material appears: solids from the skeleton, then surface bodies, then mesh bodies.
    ("ACT 2 - SOLIDS", ("sketch_get", {"sketch_name": "OuterRingSketch"}), _SOLIDS, _SOLIDS_FB),
    ("ACT 3 - SURFACES", None, _MACHINING, None),
    ("ACT 4 - MESH", None, _MESH, None),
    # MODIFY. Existing material is cut, rounded, patterned and drafted.
    ("ACT 5 - DETAILS", ("find_geometry", {"target": "OuterRing", "kind": "circular_edge", "max_results": 1}), _DETAILS, _DETAILS_FB),
    # ASSEMBLE. The parts are jointed, grounded, related and driven.
    ("ACT 6 - MOTION", ("find_geometry", {"target": "OuterRing", "kind": "cylinder_face", "max_results": 1}), _MOTION, _MOTION_FB),
    # RE-DRIVE. The parametric resize walks the WHOLE assembled mechanism, so it reads state only
    # assembly produces: the StockCenter joint origin holding position through the recompute, and a
    # rest pose whose only overlap is the intended press fit. It runs after MOTION for that reason.
    ("ACT 7 - RESIZE", ("sketch_get", {"sketch_name": "OuterRingSketch"}), _RESIZE, _RESIZE_FB),
    # NEST. Last, because the arrange solver restructures what it nests under Envelope occurrences.
    ("ACT 7b - NESTING", None, _NESTING, []),
    # MACHINE. Strip to the machinable part, model the vise around it, and machine the REAL part in
    # the REAL fixture. Each act's precondition routes to a fallback (empty when the act's tools are
    # all covered by earlier acts) so a broken story world still yields a complete per-tool ledger -
    # on the scratch-stock fixtures.
    ("ACT 8 - REDUCE TO THE PART", ("model_inspect", {"target": "Carrier:1"}), _REDUCE, []),
    ("ACT 9 - VISE FIXTURE", ("model_inspect", {"target": "Carrier:1"}), _VISE, []),
    ("ACT 10a - CAM: JOB + GENERATE", ("model_inspect", {"target": "STOCK:1"}), _CAM_STORY, _CAM),
    ("ACT 10b - CAM: DELIVERABLES", ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}), _CAM_DELIVER, _CAM_FB_DELIVER),
    ("FINALE", None, _FINALE, None),
]

# Every sketch that can be drawn on bare origin planes is drawn in ACT 1c, before anything is
# solid - the acts after it model, they do not sketch. The acts named here keep their own steps: the
# two sketch acts are already sketch-first, the OVERTURE has no geometry, and the vise draws every
# profile on a datum plane derived from the part it is being built around.
_SKETCH_PHASE, _ACT_PROGRAM = _sketches_first(
    _ACT_PROGRAM, after=("ACT 0 - OVERTURE", "ACT 1 - SKETCH + PARAMETERS", "ACT 1b - SKETCH TOOLS",
                         "ACT 9 - VISE FIXTURE"))
_ACT_PROGRAM = (_ACT_PROGRAM[:3]
                + [("ACT 1c - EVERY OTHER SKETCH", None, _SKETCH_PHASE, [])]
                + _ACT_PROGRAM[3:])

# Every act runs through the layout pass and then the framing pass, so a part added to the story
# later gets a slot of its own and a camera row without anyone remembering to give it either. Only
# the narrative is laid out: a fallback act rebuilds a story-less world at the origin, and the two
# never run together.
_SLOTS = _place_slots(_ACT_PROGRAM)

# ...then walk the sketch phase in reading order. This runs AFTER the cells are dealt because it
# needs to know which sketches got one: an origin-anchored sketch has no cell and sits a metre from
# the field, so it is drawn with the others of its kind rather than in the middle of a row. The
# re-order preserves the packer's order over the placed chunks, so _SLOTS stays true.
_ACT_PROGRAM = [(name, pre, (_sketch_reading_order(narr, _SLOTS) if "ACT 1c" in name else narr), fb)
                for name, pre, narr, fb in _ACT_PROGRAM]


def _placed_boxes(program, slots):
    """{chunk: [x0, x1, y0, y1]} once every chunk is in its slot - what the framing pass reads to
    tell whether the next subject is already on screen."""
    box = {}
    for _name, _pre, narr, _fb in program:
        for step, chunk, _cursor, frame in _place_walk(_placed(narr, slots), home_out=_CHUNK_OF):
            if chunk is None or not isinstance(step[1], dict):
                continue
            for x, y in _place_points(step[1], frame, step[0]):
                b = box.setdefault(chunk, [None, None, None, None])
                if x is not None:
                    b[0] = x if b[0] is None else min(b[0], x)
                    b[1] = x if b[1] is None else max(b[1], x)
                if y is not None:
                    b[2] = y if b[2] is None else min(b[2], y)
                    b[3] = y if b[3] is None else max(b[3], y)
    return {c: [v if v is not None else 0.0 for v in b] for c, b in box.items()}


_PLACED_BOX.update(_placed_boxes(_ACT_PROGRAM, _SLOTS))
_COMPONENTS.update(s[1]["name"] for _n, _p, narr, _f in _ACT_PROGRAM for s in narr
                   if s[0] == "model_create_component" and isinstance(s[1], dict) and s[1].get("name"))
_PATTERNED.update(c for _n, _p, narr, _f in _ACT_PROGRAM
                  for st, c, _cur, _fr in _place_walk(narr)
                  if st[0].startswith("model_pattern_") and c)
_SKETCH_PLANE.update({s[1]["name"]: s[1].get("plane") for _n, _p, narr, _f in _ACT_PROGRAM
                      for s in narr if s[0] == "sketch_create" and isinstance(s[1], dict)
                      and s[1].get("name") and s[1].get("plane") in _PLANE_VIEW})

ACTS = [(name, pre, _framed(_placed(narr, _SLOTS)), _framed(fb) if fb is not None else fb)
        for name, pre, narr, fb in _ACT_PROGRAM]

# Post-act hook run() fires after an act completes: the bounded generation poll between the CAM
# job act and its deliverables.
POLL_AFTER = {
    "ACT 10a - CAM: JOB + GENERATE": {"narrative": "DemoSetup", "fallback": "Setup1"},
}

# STEPS: the flat union of every act's narrative + fallback steps - the coverage ledger the
# completeness lint reads (every registered tool must appear as some step's tool). run() iterates
# ACTS (choosing narrative or fallback per act); STEPS exists so the lint sees the whole surface.
STEPS = [s for _, _, narr, fb in ACTS for s in (list(narr) + list(fb or []))
         if s[0] != _DWELL]

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
                 "through to the camera and read back; then the whole verb set on the finished "
                 "fixture - snapshot, a turntable through every camera preset (one framed orient "
                 "sets the subject, the rest rotate about it with fit=false), every visual style, "
                 "isolate/hide/show/clear_isolation, a persistent Named View saved, found in the "
                 "document's own list and re-applied, and restore putting camera, style and every "
                 "bulb back where the tour started. SKIPPED(rig): the snapshot/restore "
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
                         "all refused. Then the second bench, carrying the kinds the first has no "
                         "geometry for: vertical, collinear and concentric; a spline made "
                         "curvature-continuous with the line it continues; the two point-pair "
                         "kinds; a square told it is a polygon; fix and unfix on one curve; and "
                         "the three CREATOR kinds - one-sided and two-sided offset, and a "
                         "six-around circular pattern - each in a sketch of its own with the "
                         "curves it drew counted"),
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
    "sketch_dimension": ("drive ring/rotor radii by parameter expression; the wedge angle facing "
                         "the sketch origin; offset against a non-parallel line (rotated, and the "
                         "note says so) with linear_diameter refusing the same shape; line and "
                         "point measured to a model face; then the dimension bench - a slanted "
                         "line's horizontal span, a diameter, the gap between two circles on one "
                         "centre, a line to a circle's near tangent, and an ellipse's two radii - "
                         "each read back as a measured number, not a call that returned ok"),
    "sketch_get": "read the skeleton and ring profiles back",
    "sketch_delete_entity": ("delete a helper constraint; count drops - then a sketch text by its "
                             "index, the deleted string reported back, and the empty index refused"),
    "model_construction": ("offset the carrier hub plane below the rotor sweep; an AXIS on a cameo bore whose published handle the circular pattern turns about; a plane at 30 deg about the shaft's own axis (origin pinned to the axis) and a plane through a cap vertex; then the ON-PATH surface on one measured 30 mm cap edge - a proportional plane and point reading their ratio back with no extent published, an absolute placement inside the path, one before the start and one far past the end (both accepted, both disclosed against the measured length), the boundary exactly at the length, an expression placement whose model parameter is named for param_set, a to-object plane carrying distance AND offset off the path and a second one landing inside a two-edge chained path, and the summed length of that chain; the out-of-range proportional value and to_object on the point kind refused. Then the datum bench - one bored block carrying every reference the remaining modes read: a plane swung 30 deg about a top edge, one spanning three corners, one splitting the block at mid-height, one spanning two coplanar edges and one resting tangent on the bore wall; an axis on an edge, one spanning two corners and one along the top face's own normal; and points at the bore centre, at a corner where two edges meet, at the three world planes' shared origin and where an edge pierces XY. The world axis and the coordinate point are refused up front - both are setByLine/setByPoint, direct-edit-only, and this design is parametric"),
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
    "model_measure_relation": ("read rotor/shaft coaxiality; then the rest of the vocabulary on "
                               "the datum bench, each reporting its OWN measurement - the top face "
                               "perpendicular to a wall it meets and touching it along that edge, "
                               "flush with itself, the bore concentric with itself, and 20 mm "
                               "clear of the floor below"),
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
    "joint_edit": ("set rotation limits on the yaw; then walk one scratch joint through every "
                   "motion the tool offers - rigid to revolute, slider, cylindrical, planar, ball "
                   "and pin_slot - each retype witnessed by the design's own joint walk rather "
                   "than by the writer, the mismatched pin_slot axis pair refused, and the bench "
                   "left on a revolute that actually drives"),
    "joint_motion_link": "couple the crank to the rotor spin at 2:1; the vise jaws at -1 (self-centering)",
    "joint_drive": "drive every axis, the crank -> rotor 2:1, then ONE vise jaw (the link closes the other)",
    "assembly_get": "read the joint wiring, driven angles, and the StockCenter anchor back",
    "assembly_move": "pose a scratch cameo occurrence",
    "assembly_capture_position": "status, discard the pending pose, re-arm and capture",
    "assembly_constrain": ("flush-constrain a scratch cameo pair through the single-pair shorthand; "
                           "then the SET form - one constraint feature carrying two relationship "
                           "rows of different inferred types, a face-to-face mate at a 2 mm offset "
                           "with the normals flipped plus a concentric one on the same two discs, "
                           "which is how Fusion's own Constrain dialog locates a part. The count "
                           "read off the CREATED constraint is what says both rows live in the one "
                           "feature - the tool refuses a constraint holding fewer than submitted"),
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
    "view_screenshot": ("capture the sectioned mechanism; write the same path twice to show the "
                        "overwrite, and refuse a write against a document that is not active; and "
                        "shoot view='current' - the no-move capture, the only way to keep a frame "
                        "the camera already holds, since a NAMED view refits the whole model"),
    "view_screenshot_multi": ("capture the front and top beauty shots; then a four-view contact "
                              "sheet, the camera restored afterwards"),
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
    "model_arrange": ("nest a square pad, a bar, a disc and a second pad inside a HEXAGON boundary, "
                      "then scale the boundary and solve again - the same four parts re-nest, which "
                      "is the arrangement being a function of the boundary rather than a one-time "
                      "placement"),
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
                    "density on a non-rebuild; then every other rebuild method with its own "
                    "density read back off the feature, 'offset' accepted by the accurate method "
                    "and refused on the rest, and a shrink-wrap close. SKIPPED(rig): the "
                    "close_holes refusal on a mesh that "
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
                       "with unit, refusal, and rollback gates; the summary census and the same "
                       "census narrowed by tool type, one tool's full parameter list, and the "
                       "LOCAL scope answering with libraries instead of tools; a fifth tool added "
                       "and removed with the count read back; and once the job is generated, "
                       "where_used naming the operations that cut with the mill and reporting NONE "
                       "for the turning tool nothing selected. The document library refuses to "
                       "host a new library and where_used refuses a shared scope - a shared "
                       "library has no operations, so an empty list there would read as 'none'"),
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
    "cam_edit_operation": ("edit the face operation's feed; then park the drill operation and "
                           "restore it - the suppression WRITE, with hasToolpath read back on both "
                           "sides of the set so the discarded toolpath is reported, not implied"),
    "cam_create_machine": ("build a run-stamped 3-axis machine into the Local library, find it in "
                           "the catalog, assign it to the setup, and refuse the duplicate name"),
    "cam_delete_machine": ("take the run's own machine back out of the Local library: the "
                           "confirm_name mismatch refused while it still exists, then the delete "
                           "proved by the library walk, the name re-resolve, and the catalog read "
                           "that listed it when it arrived"),
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
                              "own row ceiling, verdict true after generation, include_suppressed "
                              "widening the tally while the verdict's own set is reported apart "
                              "from it, a document-level answer taken with an empty setup present, "
                              "and the FILTERING measured against a real suppression - the tally "
                              "one operation shorter with the excluded count naming what it left "
                              "out, then the same read widened to count it in its own bucket"),
    "cam_get_status": "poll the generation to completion (empty toolpaths fail)",
    "cam_post": "post the NC program to disk",
    "cam_generate_setup_sheet": "write the machinist setup sheet with the file-landed gate",
    "cam_set_nc_comment": "stamp the NC program comment",
    "cam_save_template": ("save the setup as a run-stamped local CAM template - the stamp is what "
                          "keeps two overlapping runs off one name, since this tool always writes "
                          "a NEW template"),
    "cam_apply_template": "apply the template to a second setup",
    "cam_delete_template": ("take the run's own template back out of the Local library: the "
                            "confirm_name mismatch refused while it still exists, then the delete "
                            "proved by the library's asset walk and by nothing loading from the "
                            "deleted url, and the templates slice that listed it when it arrived "
                            "read back as no longer holding it"),
    "cam_delete": "delete a scratch operation; count diff",
    "design_export": ("export the machined part to STEP, then the whole design SPLIT per component "
                      "to STL with stl_binary read back off the options object the split path "
                      "created for each file - the branch that would otherwise report a clean "
                      "export while dropping the format knob; then every remaining format one file "
                      "at a time, each measured ON DISK rather than trusted to the API's success "
                      "bool, USD publishing the .usdz path Fusion appended for itself, STL with "
                      "its units baked in, and a sketch out through the 2D DXF branch"),
    "doc_insert_import": ("re-import that STEP from disk into the live design; then the DXF back "
                          "onto a plane as sketches, an SVG into a sketch made for it, and IGES / "
                          "SMT / f3d as solids - each with the format named explicitly, "
                          "which is what makes the last row (a format contradicting its file's "
                          "extension) a refusal instead of a silent mis-read"),
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
    maps a driven tool to its shot-list step text (the ledger doubles as the demo's shot list) - a
    third column, empty when absent so the two-column stamp/count contract is unchanged.
    'act_modes' lists (act, narrative|fallback) - a machine-readable column, so a fallback-heavy
    run is visible without reading prose."""
    notes = notes or {}
    n_cov = sum(1 for _, s in ledger if s == "covered")
    # 'called' and 'called (<parked reason>)' are the SAME bucket - the reason is rendering, not a
    # third state, so the count line cannot drift from the table when a step is parked.
    n_called = sum(1 for _, s in ledger if s == "called" or s.startswith("called ("))
    n_pend = sum(1 for _, s in ledger if s.startswith("PENDING"))
    n_ref = sum(1 for _, s in ledger if s.startswith("refusals-only"))
    n_skip = len(ledger) - n_cov - n_called - n_pend - n_ref
    lines = [
        "# Live tool verification (generated by tool_verify.py - do not edit)",
        "",
        "This is a FIVE-BUCKET ledger, not a clean bill of health. It does NOT claim every tool",
        "is verified - the count line below is authoritative, and the per-tool table says which",
        "bucket each tool is in:",
        "",
        "- covered: a live step drove the tool this run and its expectation was a VALUE PREDICATE -",
        "  the step read keys off the ok payload (a delta, a count read back off the feature, a",
        "  verified flag) and those values held. This is the bucket that carries evidence.",
        "- called: every passing step for this tool was a bare 'ok' expectation - the call came back",
        "  without an error and NOTHING in the payload was read. What that proves is whatever the",
        "  tool's own post-write verification refuses on, and no more. Read a called row as",
        "  'called, and did not fail' - it is the queue for the next predicate tranche. A",
        "  'called (reason)' row is PARKED: a step deliberately held at a bare 'ok', the reason",
        "  naming what blocks its value predicate.",
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
        "{0} covered / {1} called / {2} refusals-only / {3} skipped(reason) / {4} pending".format(
            n_cov, n_called, n_ref, n_skip, n_pend),
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


def _shoot(label, out_dir, seq):
    """Capture the CURRENT view to out_dir. Used by --shots after each framing row: the frame ratio
    being arithmetically right is not evidence the view shows the thing - only looking is. Returns
    the path written, or None (a shot that fails is never worth failing the sweep over)."""
    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", label)[:60]
    path = os.path.join(out_dir, f"{seq:04d}_{safe_label}.png")
    is_error, _ = call("view_screenshot", {"width": 640, "height": 460, "file_path": path})
    return None if is_error else path


def run_steps(steps, ctx, trace=False, sleep_s=STEP_SLEEP_S, on_result=None, timings=None,
              shots_dir=None):
    """The ONE (tool, args, expect, save) step engine - every live harness judges its steps here,
    so the status vocabulary cannot fork: pass / pass* / expected-refusal / FAIL / blocked.
    'pass*' means a passing step whose saved-value extraction failed - a payload-shape mismatch
    that BLOCKS a green receipt/run in every consumer, never a quiet pass. A failing step keeps
    NOTE_MAX characters of its payload (the keys that diagnose it sit late), a passing refusal
    REFUSAL_NOTE_MAX. Yields nothing early:
    returns the full row list; 'on_result' (tool, status, note) fires per step for a consumer
    that prints as it goes. 'timings', when given, accumulates {tool: (seconds, calls)} over the
    wire calls - the sweep runs against a 600 s shell ceiling, so where its time goes has to be
    measurable rather than guessed at."""
    rows = []
    for step in steps:
        if _leaves_no_row(step):
            # a showcase beat: hold the view, judge nothing, and leave no row - it is not a tool.
            time.sleep(step[1]["seconds"])
            continue
        tool, args, expect, save = step
        # A Parked wrapper carries a ledger reason, never a judgement: the step is judged by the
        # expectation inside it exactly as if that expectation had been passed bare.
        expect = _unparked(expect)
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
        t0 = time.time()
        is_error, payload = call(tool, arguments)
        if timings is not None:
            spent, count = timings.get(tool, (0.0, 0))
            timings[tool] = (spent + time.time() - t0, count + 1)
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
        if shots_dir and tool == "view_set" and status == "pass" and arguments.get("focus"):
            f = arguments["focus"]
            _shoot("-".join(f) if isinstance(f, list) else str(f), shots_dir, len(rows))
        if sleep_s:
            time.sleep(sleep_s)
    return rows


def judged_steps(steps):
    """The steps run_steps produces a ROW for, in order - which is what pairs positionally with
    those rows. A row-less step (today only a _dwell, which holds the view and is judged by
    nothing) would otherwise shift the pairing: every expectation after it gets credited to a LATER
    step's tool, so a bare "ok" reads as covered and a value predicate is lost."""
    return [s for s in steps if not _leaves_no_row(s)]


def _precondition_holds(pre):
    """Run an act's precondition READ; True when it returns without error (the geometry the act's
    narrative consumes exists). A False routes the act to its scratch fallback."""
    tool, args = pre
    is_error, _ = call(tool, args)
    return not is_error


def run(write_json, keep_open=False, trace=False, shots_dir=None):
    health = health_gate()
    print(f"server ok: {health.get('server')} v{health.get('version', '?')}")
    all_tools = registered_tools()

    ctx, rows, notes, act_modes = {}, [], {}, []
    timings, act_seconds, run_started = {}, [], time.time()
    # the tools whose PASSING step read a value off the payload - run_steps returns exactly one row
    # per step, in order, so a row is paired back with the expectation that judged it.
    valued = set()
    # tool -> the reason a Parked step held it at a bare "ok", for the ledger's status column.
    parked = {}
    for name, pre, narrative, fallback in ACTS:
        mode, steps = "narrative", narrative
        if pre is not None and fallback is not None and not _precondition_holds(pre):
            mode, steps = "fallback", fallback
        act_modes.append((name, mode))
        print(f"\n-- {name} [{mode}] --")
        act_started = time.time()
        if keep_open and name == "FINALE":
            steps = [s for s in steps if s[0] != "doc_close"]
        # judged_steps, not the act's raw list, is what pairs with the rows below - see its
        # docstring for what a dwell does to the pairing.
        for step, (tool, status, note) in zip(judged_steps(steps),
                                              run_steps(steps, ctx, trace=trace, timings=timings,
                                                        shots_dir=shots_dir)):
            rows.append((tool, status, note))
            if status in ("pass", "pass*") and predicate_kind(step[2]) == "value":
                valued.add(tool)
            if status in ("pass", "pass*") and isinstance(step[2], Parked):
                parked[tool] = step[2].reason
            if status in ("pass", "pass*", "expected-refusal"):
                story = STORY.get(tool, "")
                notes[tool] = (story + " (fallback fixture)").strip() if mode == "fallback" else story
        if name in POLL_AFTER:
            poll_generation(rows, notes, POLL_AFTER[name][mode], valued=valued)
        act_seconds.append((name, time.time() - act_started))
    if keep_open:
        print("\n--keep-open: the story document is left open for inspection.")

    # covered demands at least one step that PRODUCED something (pass/pass*): a tool whose every
    # step is an expected-refusal exercised only its guards - no effect existed to read back, so
    # calling that "covered" would let the legend lie. Those rows get their own bucket.
    # covered vs called splits the produced-something set again: covered means a value predicate
    # read the effect off the payload, called means every passing step was a bare "ok".
    passed = {t for t, s, _ in rows if s in ("pass", "pass*")}
    refused_only = {t for t, s, _ in rows if s == "expected-refusal"} - passed
    ledger = []
    for tool in all_tools:
        if tool in valued:
            ledger.append((tool, "covered"))
        elif tool in passed:
            # a parked tool stays in the CALLED bucket and carries why it was held there, so the
            # reason is on the receipt instead of in a source comment only.
            ledger.append((tool, "called ({0})".format(parked[tool]) if tool in parked else "called"))
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
    n_called = sum(1 for _, s in ledger if s == "called" or s.startswith("called ("))
    n_pend = sum(1 for _, s in ledger if s.startswith("PENDING"))
    n_ref = sum(1 for _, s in ledger if s.startswith("refusals-only"))
    n_skip = len(ledger) - n_cov - n_called - n_pend - n_ref
    print(f"\n== ledger: {n_cov}/{len(ledger)} covered, {n_called} called (bare ok), "
          f"{n_ref} refusals-only, {n_skip} skipped(reason), {n_pend} pending")
    for tool, s in ledger:
        if s != "covered":
            print(f"  {tool:32} {s}")

    # WHERE THE SECONDS WENT. The sweep has to finish inside a 600 s shell call, so the run
    # publishes its own budget rather than leaving the next person to guess: the total, the slowest
    # acts, and the tools that spent the most wire time - with a per-call average, which is what
    # separates a tool that is CALLED a lot from a tool that is SLOW.
    elapsed = time.time() - run_started
    print(f"\n== elapsed: {elapsed:.0f}s for {len(rows)} steps "
          f"({STEP_SLEEP_S * len(rows):.0f}s of it the inter-step sleep)")
    for nm, secs in sorted(act_seconds, key=lambda r: -r[1])[:5]:
        print(f"  {secs:7.1f}s  {nm}")
    print("  slowest tools (total / calls / per call):")
    for tool, (secs, count) in sorted(timings.items(), key=lambda kv: -kv[1][0])[:8]:
        print(f"  {secs:7.1f}s  {count:4} x {secs / max(count, 1):5.2f}s  {tool}")
    # THE BUDGET IS PART OF THE RESULT, not a note for the next person. Every agent runs this through
    # a shell tool that is killed at 600 s, so a sweep that creeps past the budget is one nobody can
    # run - and a timeout kills the process without a receipt, which reads as a broken tool rather
    # than a slow sweep. Failing here turns that into a named result with the act breakdown above it.
    over_budget = elapsed > _RUNTIME_BUDGET_S
    if over_budget:
        print(f"\nOVER BUDGET: {elapsed:.0f}s exceeds the {_RUNTIME_BUDGET_S:.0f}s ceiling "
              f"({_SHELL_TIMEOUT_S:.0f}s is where the shell tool kills it). The act and tool "
              f"breakdowns above name where it went - cut there, or drop a dwell.")

    n_narr = sum(1 for _, m in act_modes if m == "narrative")
    n_fb = sum(1 for _, m in act_modes if m == "fallback")
    print(f"\n== acts: {n_narr} narrative / {n_fb} fallback (of {len(act_modes)})")
    for nm, m in act_modes:
        print(f"  {m:10} {nm}")

    # pass* blocks the receipt: the payload did not carry a key the step contract expected - a
    # payload-shape mismatch is a real signal, not a pass.
    fails = [r for r in rows if r[1] in ("FAIL", "blocked", "pass*")]
    if fails or over_budget:
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
    return 1 if (fails or over_budget) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep-open", action="store_true",
                    help="leave the story document open at the end instead of discarding it")
    ap.add_argument("--shots", metavar="DIR", default=None,
                    help="write a PNG of the framed view after every framing row, so the framing "
                         "can be judged by looking instead of by trusting its ratio")
    ap.add_argument("--trace", action="store_true",
                    help="print each step (flushed) before it runs, so a Fusion crash names its killer")
    args = ap.parse_args()
    shots = args.shots
    if shots:
        os.makedirs(shots, exist_ok=True)
    sys.exit(check() if args.check else run(args.json, keep_open=args.keep_open, trace=args.trace,
                                            shots_dir=shots))
