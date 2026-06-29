"""Lint + behavioural anchor: handle resolution is UNIFORM across every InputKind.

PR-review bug #1: AxisRef resolved an edge handle with a raw `des.findEntityByToken(s)` instead of the
shared `_resolve_token_entity(des, s)`. But find_geometry mints COMPOSITE handles
('<token>|@<kind>:x,y,z'); passing one to findEntityByToken corrupts the token (the '|@locator' suffix)
and there's no self-heal — so a fresh handle straight from find_geometry was rejected. Every other kind
(GeometryHandle/BodyRef/PlaneRef/ProfileRef) routes through `_resolve_token_entity`, which `_split_handle`s
off the locator and falls back to it when the token is stale.

This locks the invariant two ways:
  1. SOURCE LINT — `findEntityByToken(` is CALLED in _inputs.py only inside `_resolve_token_entity`
     (the single sanctioned resolution path). A new kind that hand-rolls findEntityByToken trips this.
  2. BEHAVIOUR — each handle-taking kind round-trips a COMPOSITE handle whose bare token is the only key
     in the map. If a kind passes the whole composite string to findEntityByToken, it won't resolve and
     the test fails. This is the check that would have caught #1.
"""

import os
import re

from conftest import load_tool, TOOLS_DIR

inp = load_tool("_inputs")


# ── 1) source lint ──────────────────────────────────────────────────────────

class TestFindEntityByTokenIsCentralised:
    def test_findentitybytoken_called_only_inside_resolve_token_entity(self):
        src = open(os.path.join(TOOLS_DIR, "_inputs.py"), encoding="utf-8").read()
        # Find every line that CALLS findEntityByToken (an open-paren after it), ignoring prose/comments.
        offenders = []
        in_resolver = False
        resolver_indent = None
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("def "):
                in_resolver = stripped.startswith("def _resolve_token_entity")
                resolver_indent = len(line) - len(stripped)
            # a call (not a comment) to findEntityByToken(
            if "findEntityByToken(" in line and not stripped.startswith("#"):
                if not in_resolver:
                    offenders.append((i, line.strip()))
        assert not offenders, (
            "findEntityByToken(...) must be called ONLY inside _resolve_token_entity (the self-healing, "
            "composite-handle-aware path). Route handle resolution through _resolve_token_entity instead. "
            f"Offending call(s): {offenders}")

    def test_no_tool_guesses_handle_vs_name_by_length(self):
        # PR-review #7: the `len(name) > 60` heuristic mis-routes a long body/component NAME into the
        # handle path. _resolve_token_entity replaced it (ask findEntityByToken; a non-token returns
        # nothing and the caller falls through to the name lookup). No tool may reintroduce the guess.
        # _inputs.py is exempt: it owns the resolver and references the OLD heuristic only in prose.
        length_guess = re.compile(r"len\([^)]*\)\s*>\s*60")
        offenders = []
        for fn in sorted(os.listdir(TOOLS_DIR)):
            if not fn.endswith(".py") or fn == "_inputs.py":
                continue
            src = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8")
            for i, line in enumerate(src, 1):
                if length_guess.search(line) and not line.lstrip().startswith("#"):
                    offenders.append((fn, i, line.strip()))
            src.close()
        assert not offenders, (
            "a tool guesses handle-vs-name by string length (`len(...) > 60`). Resolve through "
            "_inputs._resolve_token_entity instead (a non-token name returns None and you fall through "
            f"to the name lookup). Offending line(s): {offenders}")


# ── 2) behavioural round-trip per handle kind ───────────────────────────────
#
# Each kind gets a fake entity of the type it accepts, registered under a BARE token, and is handed the
# COMPOSITE handle. Resolution must succeed — proving the kind splits the '|@locator' off before lookup.

_SEP = inp._HANDLE_SEP


class _PlanarFace:
    def __init__(self):
        self.geometry = type("G", (), {"surfaceType": "PLANE"})()


class _LinearEdge:
    def __init__(self):
        self.geometry = type("G", (), {"curveType": "LINE"})()


class _Body:
    def __init__(self, name="B"):
        self.name = name


def _wire(handle_map, *, faces=False, edges=False, bodies=False):
    """Install a design resolving the BARE token (not the composite) + the adsk type wiring each kind
    isinstance-checks against."""
    import adsk.fusion, adsk.core
    if faces:
        adsk.fusion.BRepFace = _PlanarFace
        adsk.core.SurfaceTypes.PlaneSurfaceType = "PLANE"
    if edges:
        adsk.fusion.BRepEdge = _LinearEdge
        adsk.core.Curve3DTypes.Line3DCurveType = "LINE"
    if bodies:
        adsk.fusion.BRepBody = _Body

    class _D:
        def findEntityByToken(self, t):
            e = handle_map.get(t)
            return [e] if e is not None else []
        rootComponent = type("R", (), {"bRepBodies": type("BB", (), {"itemByName": staticmethod(lambda n: None)})()})()
    d = _D()
    inp._common.design = lambda: d
    inp._common.target_component = lambda x: d.rootComponent
    return d


class TestEveryHandleKindAcceptsACompositeHandle:
    def test_geometry_handle(self):
        f = _PlanarFace()
        _wire({"T": f}, faces=True)
        val, err = inp.GeometryHandle("h", require="planar_face").resolve(f"T{_SEP}planar_face:0,0,0")
        assert err is None and val is f

    def test_body_ref(self):
        b = _Body("Blk")
        _wire({"T": b}, bodies=True)
        val, err = inp.BodyRef("h").resolve(f"T{_SEP}body:1,2,3")
        assert err is None and val is b

    def test_plane_ref(self):
        f = _PlanarFace()
        _wire({"T": f}, faces=True)
        val, err = inp.PlaneRef("h").resolve(f"T{_SEP}planar_face:0,0,0")
        assert err is None and val is f

    def test_axis_ref(self):
        # the kind that was broken (#1)
        e = _LinearEdge()
        _wire({"T": e}, edges=True)
        val, err = inp.AxisRef("h").resolve(f"T{_SEP}edge:0,0,0")
        assert err is None and val == ("edge", e)
