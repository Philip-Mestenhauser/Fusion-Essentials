"""Unit tests for the typed INPUT KINDS framework (_inputs.py).

This is the meta-layer that keeps tools from re-inventing (and mis-shaping) their inputs. The value
is that ONE declaration drives resolution + validation + schema + contract, and that a GeometryHandle
input can ONLY be a real handle constrained to the required kind (the guardrail against a hand-rolled
or wrong-kind reference resolving to the wrong entity).
Pinned: handle resolution + the require-predicate enforcement, the units/Distance scaling chain, the
schema/contract auto-generation, and resolve_inputs end-to-end.
"""

import types

import pytest

from conftest import (load_tool, make_design, _make_object_collection, _NamedCollection, BRepBody,
                      FakePoint, FakeVector3D, MakeComp, body_proxy, entity_proxy)

inp = load_tool("_inputs")


# ── fakes for entity resolution ─────────────────────────────────────────────

class FakePlanarFace:
    def __init__(self):
        import adsk.core
        self.geometry = type("G", (), {"surfaceType": adsk.core.SurfaceTypes.PlaneSurfaceType})()


class FakeCylFace:
    def __init__(self):
        import adsk.core
        self.geometry = type("G", (), {"surfaceType": adsk.core.SurfaceTypes.CylinderSurfaceType})()


class FakeEdge:
    pass


def _install(token_map):
    """Install a fake design whose findEntityByToken resolves tokens from token_map, and wire the
    adsk isinstance types the kinds check against (SurfaceTypes ints come seeded)."""
    import adsk.fusion
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace)   # isinstance check covers both fakes
    adsk.fusion.BRepEdge = FakeEdge
    adsk.fusion.BRepVertex = type("V", (), {})

    class FakeDesign:
        def findEntityByToken(self, h):
            e = token_map.get(h)
            return [e] if e is not None else []
    # _inputs calls _common.design(); patch it
    inp._common.design = lambda: FakeDesign()
    # rebuild the requirement predicates that captured surfaceType at import (they read live each call,
    # so just ensuring the enum values match is enough)


# ── GeometryHandle: the guardrail ───────────────────────────────────────────

class TestGeometryHandle:
    def test_resolves_planar_face(self):
        f = FakePlanarFace()
        _install({"TOK": f})
        k = inp.GeometryHandle("on_face", require="planar_face")
        val, err = k.resolve("TOK")
        assert err is None and val is f

    def test_rejects_wrong_geometry_kind(self):
        # a cylinder face handed to a planar_face input -> clear error, not a crash
        _install({"TOK": FakeCylFace()})
        k = inp.GeometryHandle("on_face", require="planar_face")
        val, err = k.resolve("TOK")
        assert val is None and "must be a PLANAR face" in err

    def test_stale_handle_error(self):
        _install({})   # token not in map -> doesn't resolve
        k = inp.GeometryHandle("h", require="any", required=True)
        val, err = k.resolve("GONE")
        assert val is None and "stale" in err.lower()
        # The error must steer the agent to RE-FIND. With the self-healing composite handle, the
        # entityToken AND its geometry-locator fallback both failed here, so the message reports the
        # locator-recovery failure (not just a dead token) before pointing back at find_geometry.
        assert "find_geometry" in err
        assert "locator" in err.lower()

    def test_contract_note_names_the_required_kind(self):
        k = inp.GeometryHandle("on_face", require="planar_face")
        note = k.contract_note()
        # The note must name the required kind and point at find_geometry (the handle source).
        assert "planar" in note.lower() and "find_geometry" in note

    def test_schema_includes_contract_note(self):
        k = inp.GeometryHandle("on_face", require="cylinder_face", description="The pin face.")
        sch = k.schema()
        assert sch["type"] == "string"
        assert "CYLINDRICAL" in sch["description"] and "The pin face." in sch["description"]


# ── self-healing composite handle: stale token recovers via the kind+position locator ──────────────
# Live failure mode: find_geometry returns N handles; an older one's entityToken goes stale
# (Fusion mints a different token per query) and findEntityByToken returns nothing — even with NO model
# edit. The composite handle '<token>|@<kind>:<x>,<y>,<z>' lets resolution re-find the SAME geometry by
# its kind+position when the token is dead, so the caller never has to re-query.

class _Pt:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z


class _HealFace(FakePlanarFace):
    def __init__(self, centroid):
        super().__init__()
        self.centroid = _Pt(*centroid)


def _install_with_bodies(faces, token_map):
    """A design whose findEntityByToken uses token_map AND whose rootComponent carries bodies/faces so
    _refind_by_locator can scan them (the locator-fallback path)."""
    import adsk.fusion
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace, _HealFace)
    adsk.fusion.BRepEdge = FakeEdge
    adsk.fusion.BRepVertex = type("V", (), {})

    class _Coll:
        def __init__(self, items): self._i = list(items)
        @property
        def count(self): return len(self._i)
        def item(self, i): return self._i[i]

    body = type("Body", (), {"faces": _Coll(faces), "edges": _Coll([]), "vertices": _Coll([])})()
    root = type("Root", (), {"bRepBodies": _Coll([body]), "allOccurrences": []})()

    class FakeDesign:
        rootComponent = root
        def findEntityByToken(self, h):
            e = token_map.get(h)
            return [e] if e is not None else []
    inp._common.design = lambda: FakeDesign()


class TestIsHandle:
    """is_handle distinguishes a handle (entityToken) from an int/index/'all' for dual-accept inputs."""
    def test_composite_handle(self):
        assert inp.is_handle(f"tok{inp._HANDLE_SEP}profile:0.4,0.2,0.0") is True

    def test_long_bare_token(self):
        assert inp.is_handle("/v4BAAAARlJLZXk" + "Z" * 40) is True

    def test_int_and_index_selectors_are_not_handles(self):
        assert inp.is_handle(0) is False
        assert inp.is_handle("0,2,3") is False
        assert inp.is_handle("all") is False
        assert inp.is_handle([0, 1]) is False
        assert inp.is_handle("") is False


class TestSelfHealingHandle:
    def test_live_token_resolves_via_fast_path(self):
        f = _HealFace((1.0, 2.0, 3.0))
        _install_with_bodies([f], token_map={"TOK": f})
        handle = f"TOK{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert err is None and val is f          # token resolved directly

    def test_stale_token_recovers_via_locator(self):
        # token 'DEAD' is NOT in the map (stale), but a face sits exactly at the locator position ->
        # _refind_by_locator finds it and resolution succeeds WITHOUT re-querying.
        f = _HealFace((1.0, 2.0, 3.0))
        _install_with_bodies([f], token_map={})   # no token resolves
        handle = f"DEAD{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert err is None and val is f          # recovered by geometry locator

    def test_stale_token_no_matching_geometry_errors(self):
        # token dead AND no face near the locator -> honest failure (don't bind the wrong entity).
        f = _HealFace((50.0, 50.0, 50.0))         # far from the locator
        _install_with_bodies([f], token_map={})
        handle = f"DEAD{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert val is None and "find_geometry" in err

    def test_bare_token_still_works(self):
        # backward-compat: a handle with NO locator suffix resolves exactly as before.
        f = FakePlanarFace()
        _install({"BARE": f})
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve("BARE")
        assert err is None and val is f

    def test_make_handle_stamps_body_revision(self):
        # BRep entities carry their body's revisionId so locator recovery can verify identity.
        ent = type("E", (), {"entityToken": "TOK",
                             "body": type("B", (), {"revisionId": "REV7"})()})()
        h = inp.make_handle(ent, "cylinder_face", (1.0, 2.0, 3.0))
        assert h.endswith(";rv=REV7") and "|@cylinder_face:1.000000,2.000000,3.000000" in h

    def test_split_handle_parses_revision_and_legacy(self):
        tok, loc = inp._split_handle(f"T{inp._HANDLE_SEP}cylinder_face:1.0,2.0,3.0;rv=REV7")
        assert tok == "T" and loc == ("cylinder_face", 1.0, 2.0, 3.0, "REV7")
        tok, loc = inp._split_handle(f"T{inp._HANDLE_SEP}cylinder_face:1.0,2.0,3.0")
        assert tok == "T" and loc == ("cylinder_face", 1.0, 2.0, 3.0, None)

    def test_locator_recovery_with_matching_revision_succeeds(self):
        # token dead, geometry AND its body revision unchanged -> benign token rotation, recover.
        f = _HealFace((1.0, 2.0, 3.0))
        f.body = type("B", (), {"revisionId": "REV7"})()
        _install_with_bodies([f], token_map={})
        handle = f"DEAD{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0;rv=REV7"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert err is None and val is f

    def test_locator_recovery_refuses_on_changed_revision(self):
        # A delete-rebuild can put DIFFERENT geometry exactly at the recorded position (live-proven:
        # a rotated cylinder's record point landed on the deleted one's, and a relation read then
        # certified a comparison that never happened). A changed body revision = refuse, never guess.
        f = _HealFace((1.0, 2.0, 3.0))
        f.body = type("B", (), {"revisionId": "REV8-DIFFERENT"})()
        _install_with_bodies([f], token_map={})
        handle = f"DEAD{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0;rv=REV7"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert val is None
        assert "changed since" in err.lower() or "model changed" in err.lower()
        assert "find_geometry" in err


# ── ONE token, SEVERAL entities: the pick is the locator's, or the handle is refused ─────────────
#
# findEntityByToken answers with a VECTOR. Measured live: splitting a face made the pre-split token
# resolve to BOTH survivors (equal halves at different centroids), and one native token resolves to a
# proxy per occurrence. Those candidates sit in different places, so returning the first silently
# acts on geometry the caller never picked.

class _SplitFace(FakePlanarFace):
    """A planar face at a known centroid - a survivor of a split, or one instance's proxy. `context`
    is the assembly path an ambiguity refusal names each candidate by."""

    def __init__(self, centroid, context=None):
        super().__init__()
        self.centroid = _Pt(*centroid)
        if context is not None:
            self.assemblyContext = types.SimpleNamespace(fullPathName=context)


@pytest.fixture
def token_env(monkeypatch):
    """A design whose findEntityByToken answers a token from a map - a LIST value models the several
    entities one token can resolve to."""
    def build(tokens):
        import adsk.fusion
        monkeypatch.setattr(adsk.fusion, "BRepFace", _SplitFace, raising=False)
        design = make_design(tokens=tokens)
        monkeypatch.setattr(inp._common, "design", lambda: design)
        monkeypatch.setattr(inp._common, "target_component", lambda _d=None: design.rootComponent)
        return design
    return build


class TestTokenResolvingToSeveralEntities:
    def test_the_locator_picks_the_member_it_names_not_the_first(self, token_env):
        left, right = _SplitFace((2.5, 0.0, 0.0)), _SplitFace((7.5, 0.0, 0.0))
        token_env({"TOK": [left, right]})
        handle = f"TOK{inp._HANDLE_SEP}planar_face:7.5,0.0,0.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert err is None
        assert val is right and val is not left      # the locator's member, never found[0]

    def test_a_locator_matching_none_of_them_is_refused_naming_the_count(self, token_env):
        # The live case: the composite handle holds the PRE-split centroid, which matches neither
        # survivor - so the handle is stale/ambiguous and must be refused, not silently first-matched.
        left, right = _SplitFace((2.5, 0.0, 0.0)), _SplitFace((7.5, 0.0, 0.0))
        token_env({"TOK": [left, right]})
        handle = f"TOK{inp._HANDLE_SEP}planar_face:5.0,0.0,0.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert val is None
        assert "2 entities" in err                   # names HOW MANY it resolved to
        assert "find_geometry" in err                # and points at the way out

    def test_a_bare_token_resolving_to_several_is_refused_saying_it_has_no_locator(self, token_env):
        token_env({"TOK": [_SplitFace((2.5, 0.0, 0.0)), _SplitFace((7.5, 0.0, 0.0))]})
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve("TOK")
        assert val is None
        assert "2 entities" in err and "no position locator" in err

    def test_the_refusal_names_each_candidate_by_its_assembly_path(self, token_env):
        # What tells the candidates of one token apart is WHERE each is placed, so the refusal
        # prints each assembly context rather than a bare count.
        token_env({"TOK": [_SplitFace((0.0, 0.0, 0.0), context="Arm:1"),
                           _SplitFace((9.0, 0.0, 0.0), context="Arm:2")]})
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve("TOK")
        assert val is None and "Arm:1" in err and "Arm:2" in err

    def test_a_candidate_exactly_at_the_locator_tolerance_is_still_the_match(self, token_env):
        # EXACT boundary of the 1-micron gate: at the tolerance the candidate IS that geometry.
        far, edge = _SplitFace((9.0, 0.0, 0.0)), _SplitFace((inp._LOCATOR_TOL_CM, 0.0, 0.0))
        token_env({"TOK": [far, edge]})
        handle = f"TOK{inp._HANDLE_SEP}planar_face:0.0,0.0,0.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert err is None and val is edge

    def test_a_candidate_just_past_the_tolerance_is_refused(self, token_env):
        far = _SplitFace((9.0, 0.0, 0.0))
        beyond = _SplitFace((inp._LOCATOR_TOL_CM * 1.01, 0.0, 0.0))
        token_env({"TOK": [far, beyond]})
        handle = f"TOK{inp._HANDLE_SEP}planar_face:0.0,0.0,0.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert val is None and "2 entities" in err

    def test_two_CO_LOCATED_candidates_are_refused_never_first_matched(self, token_env):
        # measured live: a CONCENTRIC face split puts BOTH survivors at the identical centroid,
        # and the hit order is not stable across runs - so a distance tie inside the tolerance
        # must refuse naming the count; "nearest" between equals is a coin flip on someone's
        # geometry.
        a, b = _SplitFace((5.0, 0.0, 0.0)), _SplitFace((5.0, 0.0, 0.0))
        token_env({"TOK": [a, b]})
        handle = f"TOK{inp._HANDLE_SEP}planar_face:5.0,0.0,0.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert val is None
        assert "2 entities" in err and "co-located" in err

    def test_one_entity_still_resolves_with_no_locator_involved(self, token_env):
        # The ordinary case must be untouched: a token naming exactly one entity resolves as before.
        only = _SplitFace((3.0, 0.0, 0.0))
        token_env({"TOK": [only]})
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve("TOK")
        assert err is None and val is only

    def test_a_name_fallback_kind_reports_the_handle_refusal_instead_of_a_miss(self, token_env):
        # TargetRef falls through to name lookups when the handle does not resolve; its miss would
        # otherwise tell the caller the string named nothing, sending it back to names when the real
        # answer is that the HANDLE names several entities.
        token_env({"TOK": [_SplitFace((0.0, 0.0, 0.0)), _SplitFace((9.0, 0.0, 0.0))]})
        val, err = inp.TargetRef("target").resolve("TOK")
        assert val is None
        assert "2 entities" in err and "find_geometry" in err


# ── GeometryHandleList: the 'these specific edges/bodies' shape ─────────────

class TestGeometryHandleList:
    def test_resolves_list_of_edge_handles(self):
        import adsk.fusion
        e1, e2 = FakeEdge(), FakeEdge()
        _install({"E1": e1, "E2": e2})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(["E1", "E2"])
        assert err is None and ents == [e1, e2]

    def test_accepts_comma_string(self):
        e1, e2 = FakeEdge(), FakeEdge()
        _install({"E1": e1, "E2": e2})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve("E1, E2")
        assert err is None and len(ents) == 2

    def test_one_bad_handle_fails_with_index(self):
        _install({"E1": FakeEdge()})       # E2 missing
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(["E1", "E2"])
        assert ents is None and "[1]" in err

    def test_wrong_kind_in_list_rejected(self):
        _install({"E1": FakeEdge(), "F1": FakePlanarFace()})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(["E1", "F1"])
        assert ents is None and "must be an edge" in err

    def test_empty_optional_returns_empty_list(self):
        _install({})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(None)
        assert err is None and ents == []

    def test_schema_is_array(self):
        k = inp.GeometryHandleList("edges", require="edge")
        sch = k.schema()
        assert sch["type"] == "array" and sch["items"]["type"] == "string"


# ── EdgeLoopRef: edge handles as a BOUNDARY, with the closed/open loop contract ─────────────


def _edges_on_one_body(count, token="Surface1"):
    """`count` FakeEdges of ONE body, each holding its OWN proxy - the measured shape of edge.body
    (see conftest entity_proxy). Sharing one object between edges cannot tell an entityToken dedupe
    from an id() one, which is how a per-edge body over-count hides."""
    body = BRepBody(name=token, entity_token=token)
    edges = []
    for _ in range(count):
        e = FakeEdge()
        e.body = entity_proxy(body)
        edges.append(e)
    return edges


def _install_loop(token_map):
    """Edge handles plus a real ObjectCollection.create, so EdgeLoopRef can assemble the boundary
    collection it hands to Patch/Extend."""
    import adsk.core
    _install(token_map)
    adsk.core.ObjectCollection.create = _make_object_collection


class TestEdgeLoopRef:
    def test_required_empty_errors(self):
        _install_loop({})
        k = inp.EdgeLoopRef("boundary", closed=True, required=True)
        val, err = k.resolve(None)
        assert val is None and "edge" in err and "find_geometry" in err

    def test_single_edge_is_a_legal_boundary(self):
        # a lone edge is allowed - Fusion auto-finds the connected loop from it
        e = FakeEdge()
        _install_loop({"E1": e})
        (coll, meta), err = inp.EdgeLoopRef("boundary", closed=True).resolve(["E1"])
        assert err is None
        assert meta["entities"] == [e]
        assert coll.count == 1 and coll.item(0) is e

    def test_open_chain_across_two_bodies_is_refused(self):
        # closed=False (extend / open-extrude): every edge must come from ONE surface body -
        # a multi-body chain is rejected before any mutation runs.
        e1, = _edges_on_one_body(1, token="BodyA")
        e2, = _edges_on_one_body(1, token="BodyB")
        _install_loop({"E1": e1, "E2": e2})
        val, err = inp.EdgeLoopRef("edges", closed=False).resolve(["E1", "E2"])
        assert val is None and "ONE surface body" in err

    def test_open_chain_one_body_resolves_with_body_count(self):
        # Each edge holds its OWN proxy of the one body - the measured shape of edge.body (three
        # edges of one open surface body: three python ids, ONE entityToken). Counting by identity
        # would read two bodies here and REFUSE a legal single-body chain.
        e1, e2 = _edges_on_one_body(2)
        _install_loop({"E1": e1, "E2": e2})
        (coll, meta), err = inp.EdgeLoopRef("edges", closed=False).resolve(["E1", "E2"])
        assert err is None and meta["body_count"] == 1 and coll.count == 2

    def test_open_chain_of_many_edges_on_one_body_reports_one_body(self):
        # body_count is a BODY count, not an edge count: three edges of one body report 1.
        e1, e2, e3 = _edges_on_one_body(3)
        _install_loop({"E1": e1, "E2": e2, "E3": e3})
        (coll, meta), err = inp.EdgeLoopRef("edges", closed=False).resolve(["E1", "E2", "E3"])
        assert err is None and meta["body_count"] == 1 and coll.count == 3

    def test_closed_loop_may_span_bodies_and_reports_body_count(self):
        # the single-body rule gates OPEN chains only; a closed boundary resolves, and meta
        # reports how many bodies the edges touch.
        e1, = _edges_on_one_body(1, token="BodyA")
        e2, = _edges_on_one_body(1, token="BodyB")
        _install_loop({"E1": e1, "E2": e2})
        (coll, meta), err = inp.EdgeLoopRef("boundary", closed=True).resolve(["E1", "E2"])
        assert err is None and meta["body_count"] == 2

    def test_contract_note_states_closed_vs_open(self):
        closed = inp.EdgeLoopRef("boundary", closed=True).contract_note()
        opened = inp.EdgeLoopRef("edges", closed=False).contract_note()
        assert "CLOSED loop" in closed and "single edge" in closed
        assert "OPEN chain" in opened and "ONE surface body" in opened


# ── BodyRef: name OR handle, dispatched WITHOUT a length heuristic ──────────────────────────────
# Resolution is by what RESOLVES, not by string length: try the token first, then fall back to the
# name. (A length heuristic would mis-route a long body NAME to findEntityByToken as a stale handle.)

class FakeBody:
    def __init__(self, name):
        self.name = name


def _install_bodies(named=None, handle_map=None, components=None):
    import adsk.fusion
    adsk.fusion.BRepBody = FakeBody
    named = named or {}
    handle_map = handle_map or {}
    components = components or {}          # component name -> list of its bodies

    class FakeBodies:
        def itemByName(self, n):
            return named.get(n)

    class FakeComp:
        bRepBodies = FakeBodies()

    class _Coll:
        # count/item is the measured live collection protocol; iteration kept for older consumers.
        def __init__(self, items):
            self._i = list(items)
        @property
        def count(self):
            return len(self._i)
        def item(self, i):
            return self._i[i]
        def __iter__(self):
            return iter(self._i)

    class FakeNamedComp:
        def __init__(self, name, bodies):
            self.name = name
            self.bRepBodies = _Coll(bodies)
            self.meshBodies = _Coll([])

    comp_objs = [FakeNamedComp(n, bs) for n, bs in components.items()]

    class FakeDesign:
        rootComponent = FakeComp()
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
        @property
        def allComponents(self):
            # allComponents is a COUNTED collection (count/item) on the live API even when there are
            # no named sub-components - never a bare list.
            return _Coll(comp_objs)
    comp = FakeComp()
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d: comp
    return comp


class TestBodyRef:
    def test_component_name_with_single_body_resolves_to_it(self):
        b = FakeBody("Body1")
        _install_bodies(components={"Frame": [b]})
        body, err = inp.BodyRef("body_name").resolve("Frame")
        assert err is None and body is b

    def test_occurrence_suffix_resolves_to_the_component_body(self):
        b = FakeBody("Body1")
        _install_bodies(components={"Frame": [b]})
        body, err = inp.BodyRef("body_name").resolve("Frame:1")
        assert err is None and body is b

    def test_multi_body_component_name_refuses_with_body_names(self):
        _install_bodies(components={"Frame": [FakeBody("Body1"), FakeBody("Body2")]})
        body, err = inp.BodyRef("body_name").resolve("Frame")
        assert body is None
        assert "2 bodies" in err and "Body1" in err and "Body2" in err

    def test_body_name_wins_over_component_name(self):
        direct = FakeBody("Frame")            # a BODY literally named Frame
        _install_bodies(named={"Frame": direct}, components={"Frame": [FakeBody("Body1")]})
        body, err = inp.BodyRef("body_name").resolve("Frame")
        assert err is None and body is direct

    def test_resolves_a_handle(self):
        b = FakeBody("B")
        _install_bodies(handle_map={"/vTOKEN": b})
        val, err = inp.BodyRef("body").resolve("/vTOKEN")
        assert err is None and val is b

    def test_face_handle_walks_to_its_owning_body(self):
        # find_geometry mints no BODY handle (only face/edge/vertex), and a body in an instanced
        # component is ambiguous by name - so a face handle MUST resolve to its owning body, which
        # is what makes the ambiguity error's "pass a find_geometry handle" advice actually true.
        owner = FakeBody("Body1")
        FakeFace = type("FakeFace", (), {"body": owner})
        _install_bodies(handle_map={"/vFACE": FakeFace()})
        val, err = inp.BodyRef("body").resolve("/vFACE")
        assert err is None and val is owner

    def test_resolves_a_short_name(self):
        b = FakeBody("Body1")
        _install_bodies(named={"Body1": b})
        val, err = inp.BodyRef("body").resolve("Body1")
        assert err is None and val is b

    def test_long_name_is_NOT_mistaken_for_a_handle(self):
        # a 61-char body name must resolve by NAME, not error as a stale handle
        long_name = "Left-Hand-Bracket-Assembly-Revision-C-DO-NOT-MACHINE-final-v2"
        assert len(long_name) > 60
        b = FakeBody(long_name)
        _install_bodies(named={long_name: b})           # NOT in handle_map
        val, err = inp.BodyRef("body").resolve(long_name)
        assert err is None and val is b

    def test_unresolvable_reports_name_guidance(self):
        _install_bodies()
        val, err = inp.BodyRef("body").resolve("Ghost")
        assert val is None and "Ghost" in err


# ── PlaneRef: the MULTI-SOURCE kind (origin alias | construction name | handle) ─────────────────

class FakeConstructionPlane:
    pass


class _CP:
    """A ConstructionPlane fake: its name, the component that owns it, and a
    createForAssemblyContext that returns a DISTINCT proxy tagged with its occurrence - so a test can
    tell a proxy from the native and confirm WHICH occurrence it was lifted into."""

    def __init__(self, name, component=None):
        self.name = name
        self.component = component

    def createForAssemblyContext(self, occ):
        p = _CP(self.name, self.component)
        p.native = self
        p.context = occ
        return p


def _install_planes(named=None, handle_map=None, subs=(), active=None):
    """Install a fake design exposing origin planes, construction planes on the ROOT and on each
    sub-component, and a findEntityByToken for handle resolution. PlaneRef resolves via
    _common.design()/target_component().

    `named`: {name: the object the test expects back} for the ROOT component's planes.
    `subs`: [(component name, [plane names], [occurrence fullPathNames placing it])].
    `active`: the component name PlaneRef should treat as active (default: the root).
    Returns the design; its components are reachable by name through allComponents.itemByName."""
    import adsk.fusion
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace)
    adsk.fusion.ConstructionPlane = FakeConstructionPlane
    named = named or {}
    handle_map = handle_map or {}

    class FakeConsPlanes(_NamedCollection):
        """The count/item(i)/itemByName collection the design-wide plane walk reads."""

    class FakeComp:
        xYConstructionPlane = ("origin", "xy")
        xZConstructionPlane = ("origin", "xz")
        yZConstructionPlane = ("origin", "yz")

        def __init__(self, name, planes=()):
            self.name = name
            self.constructionPlanes = FakeConsPlanes(planes)

    class FakeDesign:
        def __init__(self, comps, occs):
            self.rootComponent = comps[0]
            self.allComponents = _NamedCollection(comps)
            self.activeComponent = self.allComponents.itemByName(active) or comps[0]
            self.rootComponent.allOccurrences = list(occs)
            self.rootComponent.allOccurrencesByComponent = lambda c: _NamedCollection(
                [o for o in occs if o.component is c])

        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []

    root = FakeComp("Root")
    for nm, cp in named.items():
        cp.name, cp.component = nm, root       # the walk matches on the plane's OWN name
    root.constructionPlanes = FakeConsPlanes(list(named.values()))
    comps, occs = [root], []
    for comp_name, plane_names, paths in subs:
        sub = FakeComp(comp_name)
        sub.constructionPlanes = FakeConsPlanes([_CP(n, sub) for n in plane_names])
        comps.append(sub)
        occs += [types.SimpleNamespace(fullPathName=p, name=p, component=sub) for p in paths]
    design = FakeDesign(comps, occs)
    inp._common.design = lambda: design
    inp._common.target_component = lambda d: d.activeComponent
    return design


def _sub_plane(design, comp_name, plane_name):
    """The NATIVE construction plane a sub-component of the installed design owns."""
    return design.allComponents.itemByName(comp_name).constructionPlanes.itemByName(plane_name)


class TestPlaneRef:
    def test_origin_alias(self):
        _install_planes()
        k = inp.PlaneRef("plane", default="yz")
        val, err = k.resolve("xy")
        assert err is None and val == ("origin", "xy")

    def test_alias_front_maps_to_xz(self):
        _install_planes()
        k = inp.PlaneRef("plane")
        val, err = k.resolve("front")
        assert err is None and val == ("origin", "xz")

    def test_construction_plane_by_name(self):
        cp = FakeConstructionPlane()
        _install_planes(named={"MidPlane": cp})
        k = inp.PlaneRef("plane")
        val, err = k.resolve("MidPlane")
        assert err is None and val is cp

    def test_planar_face_handle(self):
        f = FakePlanarFace()
        _install_planes(handle_map={"/v_longtoken_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": f})
        k = inp.PlaneRef("plane")
        val, err = k.resolve("/v_longtoken_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        assert err is None and val is f

    def test_curved_face_handle_rejected(self):
        c = FakeCylFace()
        _install_planes(handle_map={"/v_longtoken_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": c})
        k = inp.PlaneRef("plane")
        val, err = k.resolve("/v_longtoken_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        assert val is None and "not PLANAR" in err

    def test_unknown_string(self):
        _install_planes()
        k = inp.PlaneRef("plane")
        val, err = k.resolve("qq")
        assert val is None and "not an origin alias" in err

    def test_long_construction_plane_name_not_mistaken_for_handle(self):
        # same heuristic bug for planes: a >60-char construction-plane name must resolve by NAME
        long_name = "Mid-Span-Reference-Plane-For-The-Left-Outrigger-Pivot-Datum-A"
        assert len(long_name) > 60
        cp = FakeConstructionPlane()
        _install_planes(named={long_name: cp})          # NOT in handle_map
        val, err = inp.PlaneRef("plane").resolve(long_name)
        assert err is None and val is cp

    def test_contract_note_mentions_all_three_sources(self):
        note = inp.PlaneRef("plane").contract_note()
        assert "origin" in note and "construction" in note.lower() and "find_geometry" in note

    def test_non_string_raw_does_not_crash(self):
        # PlaneRef.resolve must isinstance-guard before `.strip()`: a non-string plane arg returns a
        # clean (None, error), not an AttributeError. Every sibling kind guards this; pin it here.
        _install_planes()
        val, err = inp.PlaneRef("plane", required=True).resolve(["xy"])
        assert val is None and err is not None      # clean rejection, not an AttributeError

    def test_subcomponent_plane_resolves_from_the_root_as_a_proxy(self):
        # the bug this walk exists for: a datum created inside a sub-component is invisible to a
        # root-only lookup, and its NATIVE form is component-local - Fusion refuses it in root
        # context. A design-unique bare name resolves, PROXIED into the occurrence that places it.
        design = _install_planes(subs=[("Tower", ["Datum_A"], ["Tower:1"])])
        val, err = inp.PlaneRef("plane").resolve("Datum_A")
        assert err is None
        assert getattr(val, "native", None) is _sub_plane(design, "Tower", "Datum_A")
        assert val.context.fullPathName == "Tower:1"

    def test_bare_name_shared_by_two_components_is_refused_with_qualified_candidates(self):
        _install_planes(subs=[("A", ["Mid"], ["A:1"]), ("B", ["Mid"], ["B:1"])])
        val, err = inp.PlaneRef("plane").resolve("Mid")
        assert val is None and "ambiguous" in err
        assert "A:1:Mid" in err and "B:1:Mid" in err        # every candidate resolves

    def test_qualified_name_picks_the_named_occurrence(self):
        design = _install_planes(subs=[("A", ["Mid"], ["A:1"]), ("B", ["Mid"], ["B:1"])])
        val, err = inp.PlaneRef("plane").resolve("B:1:Mid")
        assert err is None
        assert getattr(val, "native", None) is _sub_plane(design, "B", "Mid")
        assert val.context.fullPathName == "B:1"

    def test_qualified_name_on_an_occurrence_without_that_plane_is_named(self):
        _install_planes(subs=[("Tower", ["Datum_A"], ["Tower:1"])])
        val, err = inp.PlaneRef("plane").resolve("Tower:1:Nope")
        assert val is None and "no construction plane named 'Nope'" in err

    def test_root_plane_is_handed_back_native(self):
        # a root-owned plane is already in assembly context: it must NOT be lifted into an
        # occurrence, and the presence of sub-components must not change what it resolves to.
        cp = FakeConstructionPlane()
        _install_planes(named={"MidPlane": cp}, subs=[("A", ["Other"], ["A:1"])])
        val, err = inp.PlaneRef("plane").resolve("MidPlane")
        assert err is None and val is cp

    def test_active_component_name_shadows_another_components_plane(self):
        # Fusion default-names the first datum of EVERY component 'Plane1'; the active component's
        # own plane wins (native - it is already the context being built in) instead of a refusal.
        design = _install_planes(subs=[("A", ["Plane1"], ["A:1"]), ("B", ["Plane1"], ["B:1"])],
                                 active="A")
        val, err = inp.PlaneRef("plane").resolve("Plane1")
        assert err is None and val is _sub_plane(design, "A", "Plane1")

    def test_plane_on_a_component_placed_twice_is_refused(self):
        # one NAME, but the owning component is instanced twice - each instance holds the plane
        # somewhere different, so the instance is refused rather than guessed.
        _install_planes(subs=[("Jaw", ["Grip"], ["Jaw:1", "Jaw:2"])])
        val, err = inp.PlaneRef("plane").resolve("Grip")
        assert val is None and "placed 2 times" in err
        assert "Jaw:1:Grip" in err and "Jaw:2:Grip" in err

    @pytest.mark.parametrize("given, origin", [
        ("xyplane", ("origin", "xy")), ("xzplane", ("origin", "xz")),
        ("yzplane", ("origin", "yz")), ("XYPlane", ("origin", "xy")),
        ("  XY Plane ", ("origin", "xy")), ("yz plane", ("origin", "yz"))])
    def test_the_alias_plane_spelling_names_the_same_origin_plane(self, given, origin):
        # '<alias> plane' / '<alias>plane' is the spelling an agent reaches for; it resolves to the
        # very plane the bare alias does, so no consumer needs its own fold.
        _install_planes()
        val, err = inp.PlaneRef("plane").resolve(given)
        assert err is None and val == origin

    def test_a_datum_named_mid_plane_still_reaches_the_name_lookup(self):
        # only the three AXIS aliases take the 'plane' suffix - a construction plane genuinely
        # named 'Mid plane' must not be folded into an origin alias.
        cp = FakeConstructionPlane()
        _install_planes(named={"Mid plane": cp})
        val, err = inp.PlaneRef("plane").resolve("Mid plane")
        assert err is None and val is cp

    def test_an_empty_value_resolves_the_declared_default_to_an_entity(self):
        # the raw default string is not a plane: an empty value must come back as the SAME resolved
        # entity the default's own spelling resolves to, so no consumer hands 'xy' to the API.
        _install_planes()
        k = inp.PlaneRef("plane", default="xy")
        val, err = k.resolve("")
        assert err is None
        assert val == ("origin", "xy") == k.resolve("xy")[0]
        assert val != "xy"

    def test_the_default_resolves_down_the_same_path_a_given_value_takes(self):
        # not an alias-only shortcut: a default naming a construction plane resolves by NAME.
        cp = FakeConstructionPlane()
        _install_planes(named={"Datum1": cp})
        val, err = inp.PlaneRef("plane", default="Datum1").resolve("")
        assert err is None and val is cp

    def test_an_unresolvable_default_returns_the_resolve_error(self):
        _install_planes()
        val, err = inp.PlaneRef("plane", default="qq").resolve("")
        assert val is None and "not an origin alias" in err

    def test_an_empty_value_with_no_default_stays_none(self):
        # a kind with no default has nothing to resolve: empty is still empty, and a required kind
        # still refuses.
        _install_planes()
        assert inp.PlaneRef("plane").resolve("") == (None, None)
        val, err = inp.PlaneRef("plane", required=True).resolve("")
        assert val is None and "is required" in err

    def test_composite_face_handle_resolves(self):
        # PlaneRef already routes through _resolve_token_entity, so a COMPOSITE planar-face handle
        # ('<token>|@planar_face:x,y,z') must resolve by its bare token. Guards against a regression
        # back to a raw findEntityByToken(s) that the locator suffix would corrupt.
        f = FakePlanarFace()
        _install_planes(handle_map={"FACETOK": f})
        handle = f"FACETOK{inp._HANDLE_SEP}planar_face:0.0,0.0,0.0"
        val, err = inp.PlaneRef("plane").resolve(handle)
        assert err is None and val is f


# ── AxisRef: world axis OR edge handle ──────────────────────────────────────

class _FakeLinearEdge:
    def __init__(self):
        import adsk.core
        self.geometry = type("G", (), {"curveType": adsk.core.Curve3DTypes.Line3DCurveType})()


class _FakeArcEdge:
    def __init__(self):
        import adsk.core
        self.geometry = type("G", (), {"curveType": adsk.core.Curve3DTypes.Arc3DCurveType})()


def _install_axis(handle_map=None):
    import adsk.fusion
    adsk.fusion.BRepEdge = (_FakeLinearEdge, _FakeArcEdge)
    handle_map = handle_map or {}

    class FakeDesign:
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    inp._common.design = lambda: FakeDesign()


class TestAxisRef:
    def test_world_axis(self):
        _install_axis()
        k = inp.AxisRef("axis", default="z")
        val, err = k.resolve("x")
        assert err is None and val == ("world", (1, 0, 0))

    def test_edge_handle_axis(self):
        e = _FakeLinearEdge()
        _install_axis(handle_map={"E": e})
        k = inp.AxisRef("axis")
        val, err = k.resolve("E")
        assert err is None and val == ("edge", e)

    def test_sketch_line_handle_axis(self):
        # a SketchLine is straight by construction - no curveType check needed, unlike a BRepEdge.
        import adsk.fusion

        class _FakeSketchLine:
            pass
        adsk.fusion.SketchLine = _FakeSketchLine
        ln = _FakeSketchLine()
        _install_axis(handle_map={"L": ln})
        k = inp.AxisRef("axis")
        val, err = k.resolve("L")
        assert err is None and val == ("edge", ln)

    def test_curved_edge_rejected(self):
        a = _FakeArcEdge()
        _install_axis(handle_map={"A": a})
        k = inp.AxisRef("axis")
        val, err = k.resolve("A")
        assert val is None and "not straight" in err

    def test_unknown_axis_string(self):
        _install_axis()
        k = inp.AxisRef("axis")
        val, err = k.resolve("q")
        assert val is None and "not a world axis" in err

    def test_composite_handle_resolves_via_token(self):
        # AxisRef must accept a COMPOSITE find_geometry handle ('<token>|@<kind>:x,y,z'), like
        # every other handle kind. The handle_map is keyed on the BARE token; passing the composite
        # must still resolve (the '|@locator' suffix is split off before findEntityByToken).
        e = _FakeLinearEdge()
        _install_axis(handle_map={"TOKEN": e})
        handle = f"TOKEN{inp._HANDLE_SEP}edge:1.0,2.0,3.0"
        val, err = inp.AxisRef("axis").resolve(handle)
        assert err is None and val == ("edge", e)

    def test_non_string_raw_does_not_crash(self):
        # AxisRef already guards a non-string raw; pin it (a list/None must yield a clean error/default,
        # never an AttributeError from .strip()).
        _install_axis()
        val, err = inp.AxisRef("axis", required=True).resolve(["x"])
        assert val is None and err is not None      # no crash; a clean rejection


# ── AxisRef face-as-direction: a planar face -> its normal, a cylinder/cone face -> its axis ──────
# A face handle can source a DIRECTION (joint orient_axis / revolve axis). It comes back tagged
# ('world', unit_vec) - the SAME shape a world axis uses - so every AxisRef consumer that handles a
# world direction handles a face-derived one with NO code change (this is why joint_create_origin needs
# none). The existing world-axis / straight-edge / sketch-line paths must keep working (tested above).

class _FakePlanarAxisFace:
    def __init__(self, normal):
        import adsk.core
        self.geometry = type("G", (), {"surfaceType": adsk.core.SurfaceTypes.PlaneSurfaceType,
                                       "normal": _Pt(*normal)})()


class _FakeCylAxisFace:
    def __init__(self, axis):
        import adsk.core
        self.geometry = type("G", (), {"surfaceType": adsk.core.SurfaceTypes.CylinderSurfaceType,
                                       "axis": _Pt(*axis)})()


def _install_axis_face(handle_map):
    """AxisRef reaches the face branch only after the edge/sketch-line isinstance checks, so wire real
    (non-Mock) BRepEdge + SketchLine classes so those checks return False cleanly, plus BRepFace (the
    SurfaceTypes ints come seeded)."""
    import adsk.fusion
    adsk.fusion.BRepFace = (_FakePlanarAxisFace, _FakeCylAxisFace)
    adsk.fusion.BRepEdge = (_FakeLinearEdge, _FakeArcEdge)
    adsk.fusion.SketchLine = type("SL", (), {})

    class FakeDesign:
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    inp._common.design = lambda: FakeDesign()


class TestAxisRefFace:
    def test_planar_face_gives_the_normal_as_direction(self):
        f = _FakePlanarAxisFace((0, 0, 1))
        _install_axis_face({"F": f})
        val, err = inp.AxisRef("axis").resolve("F")
        assert err is None and val == ("world", (0.0, 0.0, 1.0))

    def test_cylinder_face_gives_its_axis_normalized(self):
        f = _FakeCylAxisFace((0, 0, 2))          # non-unit input -> returned as a UNIT vector
        _install_axis_face({"C": f})
        val, err = inp.AxisRef("axis").resolve("C")
        assert err is None and val == ("world", (0.0, 0.0, 1.0))

    def test_composite_face_handle_resolves_via_token(self):
        f = _FakePlanarAxisFace((1, 0, 0))
        _install_axis_face({"FT": f})
        handle = f"FT{inp._HANDLE_SEP}planar_face:0.0,0.0,0.0"
        val, err = inp.AxisRef("axis").resolve(handle)
        assert err is None and val == ("world", (1.0, 0.0, 0.0))

    def test_world_axis_still_resolves_with_face_types_wired(self):
        # backward-compat: adding the face branch must not disturb the world-axis path
        _install_axis_face({})
        val, err = inp.AxisRef("axis").resolve("y")
        assert err is None and val == ("world", (0, 1, 0))


# ── AxisRef: a CONSTRUCTION AXIS, by handle or by name ───────────────────────────────────────────
#
# A construction axis is a linear ENTITY (its .geometry is an InfiniteLine3D), so it comes back
# tagged ('edge', axis) - the same shape a straight edge uses, which is what every consumer that
# feeds a linear entity to a feature input already handles. The NAME path resolves within the ACTIVE
# component only, case-insensitive EXACT, and refuses a name two axes share.

class _FakeConstructionAxis:
    """A construction axis: a NAME plus .geometry, an InfiniteLine3D (origin + direction) - the
    shape that tells a datum axis from a bounded edge's Line3D. Its geometry reads component-LOCAL
    while native and WORLD through the createForAssemblyContext proxy, which is the split a world
    lift exists for. Also stands in for the ConstructionAxis type the TargetRef extension tests bind
    (one fake per live type per file)."""
    def __init__(self, name="Axis1", origin=None, direction=None, component=None, proxy=None,
                 assembly_context=None):
        self.name = name
        self.geometry = types.SimpleNamespace(origin=origin, direction=direction)
        self.component = component
        self.assemblyContext = assembly_context
        self.proxied_into = []
        if proxy is not None:
            def _for_context(occ, p=proxy):
                self.proxied_into.append(occ)
                return p
            self.createForAssemblyContext = _for_context


@pytest.fixture
def axis_env(monkeypatch):
    """A design whose ACTIVE component is NOT the root - so a lookup scoped to the active component
    is told apart from one that walks the root - plus token resolution and occurrence placement.

    Returns a callable: env(axes=[...], tokens={...}, root_axes=[...]) -> a namespace with .active,
    .root, .design and .place(component, *fullPathNames). The adsk types AxisRef isinstance-checks
    must be REAL classes (a bare Mock attribute is not a type)."""
    import adsk.fusion

    def build(axes=(), tokens=None, root_axes=()):
        monkeypatch.setattr(adsk.fusion, "ConstructionAxis", _FakeConstructionAxis, raising=False)
        monkeypatch.setattr(adsk.fusion, "BRepEdge", (_FakeLinearEdge, _FakeArcEdge), raising=False)
        monkeypatch.setattr(adsk.fusion, "SketchLine", type("SL", (), {}), raising=False)
        monkeypatch.setattr(adsk.fusion, "BRepFace", (_FakePlanarAxisFace, _FakeCylAxisFace),
                            raising=False)
        active = MakeComp(name="Active")
        active.constructionAxes = _NamedCollection(list(axes))
        root = MakeComp(name="Root")
        root.constructionAxes = _NamedCollection(list(root_axes))
        design = make_design(comp=root, tokens=dict(tokens or {}))
        placed = {}
        root.allOccurrencesByComponent = lambda c: _NamedCollection(placed.get(id(c), []))
        monkeypatch.setattr(inp._common, "design", lambda: design)
        monkeypatch.setattr(inp._common, "target_component", lambda _d=None: active)

        def place(comp, *full_paths):
            placed[id(comp)] = [types.SimpleNamespace(fullPathName=p) for p in full_paths]

        return types.SimpleNamespace(active=active, root=root, design=design, place=place)

    return build


class TestAxisRefConstructionAxis:
    def test_handle_resolves_to_the_axis_entity(self, axis_env):
        ax = _FakeConstructionAxis("WheelAxis")
        axis_env(axes=[ax], tokens={"CA": ax})
        val, err = inp.AxisRef("axis").resolve("CA")
        assert err is None and val == ("edge", ax)

    def test_name_resolves_in_the_active_component(self, axis_env):
        ax = _FakeConstructionAxis("WheelAxis")
        axis_env(axes=[ax])
        val, err = inp.AxisRef("axis").resolve("WheelAxis")
        assert err is None and val == ("edge", ax)

    def test_name_match_is_case_insensitive_but_exact(self, axis_env):
        ax = _FakeConstructionAxis("WheelAxis")
        axis_env(axes=[ax])
        assert inp.AxisRef("axis").resolve("wheelaxis")[0] == ("edge", ax)
        # EXACT: a prefix is not a match (a substring hit would silently target the wrong axis)
        val, err = inp.AxisRef("axis").resolve("Wheel")
        assert val is None and "WheelAxis" in err        # the miss lists what IS available

    def test_ambiguous_name_is_refused_naming_the_count(self, axis_env):
        axis_env(axes=[_FakeConstructionAxis("Hinge"), _FakeConstructionAxis("Hinge")])
        val, err = inp.AxisRef("axis").resolve("Hinge")
        assert val is None
        assert "2" in err and "Hinge" in err              # refuses, never grabs the first
        assert "handle" in err                            # and names the unambiguous way in

    def test_entity_only_input_still_takes_a_construction_axis(self, axis_env):
        # entity_only refuses a FACE (a direction vector); a construction axis IS a linear entity.
        ax = _FakeConstructionAxis("Spin")
        axis_env(axes=[ax], tokens={"CA": ax})
        assert inp.AxisRef("d", entity_only=True).resolve("CA")[0] == ("edge", ax)
        assert inp.AxisRef("d", entity_only=True).resolve("Spin")[0] == ("edge", ax)

    def test_a_world_key_still_wins_over_the_name_lookup(self, axis_env):
        # regression: the world keys resolve BEFORE any component walk, so an axis named 'x'
        # cannot shadow the world x direction.
        axis_env(axes=[_FakeConstructionAxis("x")])
        assert inp.AxisRef("axis").resolve("x")[0] == ("world", (1, 0, 0))

    def test_component_without_construction_axes_still_reports_the_miss(self, axis_env):
        env = axis_env(axes=[])
        val, err = inp.AxisRef("axis").resolve("Nope")
        assert val is None and "not a world axis" in err
        assert env.active.name in err          # the miss says WHERE it looked

    def test_the_name_lookup_is_scoped_to_the_ACTIVE_component(self, axis_env):
        # An axis owned by the ROOT while another component is active is NOT name-reachable: the
        # lookup walks the active component, so a root-scoped walk would resolve it and be wrong.
        env = axis_env(axes=[], root_axes=[_FakeConstructionAxis("RootSpin")])
        val, err = inp.AxisRef("axis").resolve("RootSpin")
        assert val is None
        assert "active component 'Active'" in err       # the refusal names the component searched
        # the root's axis was never a candidate - the active component is reported as empty
        assert f"'{env.active.name}' has no construction axes" in err


# ── AxisRef(face_entity=True): the face ENTITY, for an input whose API takes the axis-defining face ──

class TestAxisRefFaceEntity:
    def test_cylindrical_face_resolves_to_the_face_itself(self, axis_env):
        f = _FakeCylAxisFace((0, 0, 1))
        axis_env(tokens={"F": f})
        # NOT ('world', vector): the vector throws the axis POSITION away, which is exactly what an
        # off-origin rotation axis needs.
        assert inp.AxisRef("axis", face_entity=True).resolve("F")[0] == ("edge", f)

    def test_planar_face_is_refused(self, axis_env):
        f = _FakePlanarAxisFace((0, 0, 1))
        axis_env(tokens={"F": f})
        val, err = inp.AxisRef("axis", face_entity=True).resolve("F")
        assert val is None and "cylindrical" in err

    def test_world_key_and_construction_axis_unchanged(self, axis_env):
        ax = _FakeConstructionAxis("Spin")
        axis_env(axes=[ax])
        k = inp.AxisRef("axis", face_entity=True)
        assert k.resolve("z")[0] == ("world", (0, 0, 1))
        assert k.resolve("Spin")[0] == ("edge", ax)


# ── axis_line_of: the NUMERIC axis a rotation pivots about must be in WORLD space ────────────────
#
# A ConstructionAxis has no worldGeometry and its .geometry reads component-LOCAL, so a datum in a
# placed component describes a line that is off by the placement - a rotation built from it turns
# about the wrong pivot and reports success. A BRepEdge/SketchLine carries worldGeometry and keeps
# its existing path.

def _datum_in(component, local_origin, world_origin=None, name="Spin"):
    """(axis, its assembly-context proxy) - a datum whose LOCAL geometry differs from what the
    proxy reads, i.e. a component placed away from the origin."""
    proxy = (_FakeConstructionAxis(name, origin=FakePoint(*world_origin),
                                   direction=FakeVector3D(0, 0, 1), assembly_context="OCC")
             if world_origin is not None else None)
    axis = _FakeConstructionAxis(name, origin=FakePoint(*local_origin),
                                 direction=FakeVector3D(0, 0, 1), component=component, proxy=proxy)
    return axis, proxy


class TestAxisLineOfWorldSpace:
    def test_a_datum_in_a_placed_component_is_lifted_through_its_occurrence(self, axis_env):
        env = axis_env()
        wheel = MakeComp(name="Wheel")
        axis, proxy = _datum_in(wheel, (0, 0, 0), world_origin=(5, 0, 0))
        env.place(wheel, "Wheel:1")
        pair, err = inp.axis_line_of("rotate_axis", axis)
        assert err is None
        point, _direction = pair
        # the PROXY's world origin - the component-local (0,0,0) would pivot about the world origin
        assert (point.x, point.y, point.z) == (5, 0, 0)
        assert axis.proxied_into == ["Wheel:1"] or len(axis.proxied_into) == 1

    def test_a_root_owned_datum_is_used_as_is(self, axis_env):
        # The datum's owner and the design's root are DISTINCT wrappers sharing one entityToken -
        # the measured shape, since component references are never identity-stable. An identity test
        # reads False here and sends a perfectly legal ROOT datum down the placed-component lookup,
        # which finds no occurrences and refuses it.
        env = axis_env()
        env.root.entityToken = "TOKEN:Root"
        axis, _ = _datum_in(entity_proxy(env.root), (2, 2, 0))
        pair, err = inp.axis_line_of("rotate_axis", axis)
        assert err is None and (pair[0].x, pair[0].y) == (2, 2)
        assert axis.proxied_into == []            # local IS world on the root - no lift attempted

    def test_a_proxied_datum_is_read_directly_even_where_its_component_is_placed_twice(self, axis_env):
        # A proxy already reads WORLD (and createForAssemblyContext on one RAISES), so the early
        # return is the ONLY route for a datum handed in from a multiply-placed component: without
        # it the ambiguity refusal fires on a reference that names its instance already.
        env = axis_env()
        wheel = MakeComp(name="Wheel")
        axis = _FakeConstructionAxis("Spin", origin=FakePoint(9, 0, 0),
                                     direction=FakeVector3D(0, 0, 1), component=wheel,
                                     assembly_context="Assy:1+Wheel:2")
        env.place(wheel, "Assy:1+Wheel:1", "Assy:1+Wheel:2")
        pair, err = inp.axis_line_of("rotate_axis", axis)
        assert err is None and pair[0].x == 9

    def test_a_datum_already_in_context_is_not_re_proxied(self, axis_env):
        axis_env()
        axis = _FakeConstructionAxis("Spin", origin=FakePoint(7, 0, 0),
                                     direction=FakeVector3D(0, 0, 1), assembly_context="OCC")
        pair, err = inp.axis_line_of("rotate_axis", axis)
        assert err is None and pair[0].x == 7
        assert axis.proxied_into == []

    def test_a_component_placed_twice_is_refused_naming_each_path(self, axis_env):
        env = axis_env()
        wheel = MakeComp(name="Wheel")
        axis, _ = _datum_in(wheel, (0, 0, 0), world_origin=(5, 0, 0))
        env.place(wheel, "Assy:1+Wheel:1", "Assy:1+Wheel:2")
        pair, err = inp.axis_line_of("rotate_axis", axis)
        assert pair is None
        assert "Assy:1+Wheel:1" in err and "Assy:1+Wheel:2" in err

    def test_an_unplaced_component_datum_is_refused(self, axis_env):
        axis_env()
        axis, _ = _datum_in(MakeComp(name="Wheel"), (0, 0, 0), world_origin=(5, 0, 0))
        pair, err = inp.axis_line_of("rotate_axis", axis)
        assert pair is None and "not placed in the assembly" in err

    def test_an_unreadable_root_component_is_refused_naming_the_owner(self, axis_env):
        # With no root there is no placement to look the datum up in, so where it SITS is unknown.
        # Falling back to its own .geometry here would hand back a component-LOCAL line as though it
        # were world - the exact silent wrong-pivot this lift exists to prevent.
        env = axis_env()
        wheel = MakeComp(name="Wheel")
        axis, _ = _datum_in(wheel, (0, 0, 0), world_origin=(5, 0, 0))
        env.place(wheel, "Wheel:1")
        env.design.rootComponent = None
        pair, err = inp.axis_line_of("rotate_axis", axis)
        assert pair is None
        assert "Wheel" in err and "root component could not be read" in err

    def test_an_edge_keeps_its_worldgeometry_path(self, axis_env):
        # regression: only a ConstructionAxis takes the lift; an edge's world line is read directly,
        # and its direction is DERIVED from the two endpoints.
        axis_env()
        edge = _FakeLinearEdge()
        edge.worldGeometry = types.SimpleNamespace(startPoint=FakePoint(1, 0, 0),
                                                   endPoint=FakePoint(4, 0, 0))
        pair, err = inp.axis_line_of("rotate_axis", edge)
        assert err is None
        point, direction = pair
        assert (point.x, point.y, point.z) == (1, 0, 0)
        assert (direction.x, direction.y, direction.z) == (1.0, 0.0, 0.0)   # normalized


# ── Distance + UnitField scaling chain ──────────────────────────────────────

class TestDistanceUnits:
    def test_distance_scaled_by_units(self):
        d = inp.Distance("dist")
        val, err = d.resolve_scaled(6, 0.1)        # 6 mm at scale 0.1 -> 0.6 cm
        assert err is None and abs(val - 0.6) < 1e-9

    def test_distance_nonzero_guard(self):
        d = inp.Distance("dist", allow_zero=False)
        _, err = d.resolve_scaled(0, 0.1)
        assert "non-zero" in err

    def test_unit_field_returns_scale(self):
        u = inp.UnitField()
        sf, err = u.resolve("in")
        assert err is None and abs(sf - 2.54) < 1e-9

    def test_unknown_unit(self):
        u = inp.UnitField()
        _, err = u.resolve("furlong")
        assert "Unknown units" in err

    def test_unit_field_schema_emits_enum(self):
        # units choices live in the schema enum, not re-spelled as "mm | cm | in" prose in 20 tools
        sch = inp.UnitField().schema()
        assert sch["type"] == "string" and sch["enum"] == ["mm", "cm", "in"]
        assert "mm | cm | in" not in sch["description"]


# ── shared singletons + as_property (the dedup mechanism for the enum migration) ─────────────────

class TestSharedInputs:
    def test_as_property_splats_name_and_schema(self):
        name, sch = inp.UNITS.as_property()
        assert name == "units"
        assert sch["enum"] == ["mm", "cm", "in"]

    def test_units_property_factory(self):
        name, sch = inp.units_property(description="Display units.")
        assert name == "units" and sch["enum"] == ["mm", "cm", "in"]
        assert "Display units" in sch["description"]

    def test_boolean_op_subset(self):
        # combine supports only join/cut/intersect — the factory carries exactly that subset as enum
        name, sch = inp.boolean_op(options=("join", "cut", "intersect")).as_property()
        assert name == "operation" and sch["enum"] == ["join", "cut", "intersect"]

    def test_frame_axis(self):
        name, sch = inp.frame_axis(default="x").as_property()
        assert name == "axis" and sch["enum"] == ["x", "y", "z"]
        assert "Default x" in sch["description"]

    def test_joint_motion_full_set(self):
        # joint_create/edit get all six; the shared set means it can't drift from joint_at_geometry's
        name, sch = inp.joint_motion().as_property()
        assert name == "joint_type"
        assert sch["enum"] == ["rigid", "revolute", "slider", "cylindrical", "planar", "ball"]

    def test_joint_motion_subset_preserves_capability_difference(self):
        # joint_at_geometry omits planar — pass the subset explicitly; still structured, still an enum
        name, sch = inp.joint_motion("motion",
                                     options=("rigid", "revolute", "slider", "cylindrical", "ball"),
                                     default="revolute").as_property()
        assert name == "motion" and "planar" not in sch["enum"]
        assert sch["enum"] == ["rigid", "revolute", "slider", "cylindrical", "ball"]


# ── Choice ──────────────────────────────────────────────────────────────────

class TestChoice:
    def test_valid_option(self):
        c = inp.Choice("op", ["new", "join", "cut"], default="new")
        val, err = c.resolve("cut")
        assert err is None and val == "cut"

    def test_invalid_option(self):
        c = inp.Choice("op", ["new", "join"], default="new")
        _, err = c.resolve("weld")
        assert "must be one of" in err

    def test_default_when_empty(self):
        c = inp.Choice("op", ["new", "join"], default="new")
        val, _ = c.resolve("")
        assert val == "new"

    def test_schema_emits_enum(self):
        # the whole point of #2: the legal values live in the JSON-schema `enum`, validated by the
        # client/server, not only described in prose. The description must NOT re-list them (the Choice
        # contract_note would just duplicate the enum).
        c = inp.Choice("op", ["new", "join", "cut"], description="The boolean operation.")
        sch = c.schema()
        assert sch["type"] == "string"
        assert sch["enum"] == ["new", "join", "cut"]
        # the bare option list should not be re-spelled in the description (enum carries it)
        assert "new, join, cut" not in sch["description"]

    def test_schema_enum_with_default_notes_it(self):
        c = inp.Choice("op", ["new", "cut"], default="new")
        sch = c.schema()
        assert sch["enum"] == ["new", "cut"]
        assert "Default new" in sch["description"]


# ── resolve_inputs: end-to-end (units resolved first, distances scaled) ─────

class TestResolveInputs:
    def test_resolves_all_with_unit_dependency(self):
        spec = [inp.UnitField(), inp.Distance("depth", allow_zero=False),
                inp.Choice("op", ["new", "cut"], default="new")]
        vals, err = inp.resolve_inputs(spec, {"units": "in", "depth": 1, "op": "cut"})
        assert err is None
        assert abs(vals["depth"] - 2.54) < 1e-9       # 1 in -> 2.54 cm
        assert vals["op"] == "cut"

    def test_first_failure_short_circuits(self):
        spec = [inp.UnitField(), inp.Distance("depth", allow_zero=False)]
        vals, err = inp.resolve_inputs(spec, {"units": "mm", "depth": 0})
        assert vals is None and err["isError"] is True and "non-zero" in err["message"]


# ── contract_block + apply_to_tool generation ───────────────────────────────

class TestGeneration:
    def test_contract_block_lists_each_input(self):
        spec = [inp.GeometryHandle("on_face", require="planar_face"),
                inp.Choice("op", ["new", "cut"], default="new")]
        block = inp.contract_block(spec)
        assert "INPUTS:" in block
        assert "on_face" in block and "op" in block
        assert "planar" in block.lower()

    def test_apply_to_tool_adds_properties_and_required(self):
        class FakeTool:
            def __init__(self):
                self.props = {}
                self.required = []
            def add_input_property(self, n, s):
                self.props[n] = s; return self
            def add_required_input(self, n):
                self.required.append(n); return self
        t = FakeTool()
        spec = [inp.GeometryHandle("on_face", require="planar_face", required=True),
                inp.Choice("op", ["new"], default="new")]
        inp.apply_to_tool(t, spec)
        assert "on_face" in t.props and "op" in t.props
        assert t.required == ["on_face"]


# ── BodyRef KIND axis: solid | surface | mesh | any (+ redirecting wrong-kind error) ────────────
# A BRepBody can be a SOLID (isSolid True) or an OPEN SURFACE (isSolid False); a MeshBody is a
# SEPARATE type living in meshBodies. The kind axis validates at resolve time and, on the WRONG kind,
# returns a REDIRECTING error (the high-value part) instead of a silent miss / misleading downstream.

class FakeBRep:
    """Stands in for adsk.fusion.BRepBody. isSolid distinguishes solid vs open-surface. entity_token
    stands in for the stable entityToken - two wrappers of the SAME physical body share one token."""
    def __init__(self, name="Body1", is_solid=True, entity_token=None):
        self.name = name
        self.isSolid = is_solid
        if entity_token is not None:
            self.entityToken = entity_token


class FakeMesh:
    """Stands in for adsk.fusion.MeshBody — a DIFFERENT type, lives in meshBodies."""
    def __init__(self, name="Mesh1"):
        self.name = name


def _install_kind_bodies(brep_named=None, mesh_named=None, handle_map=None):
    """Install fakes for the kind axis: BRepBody/MeshBody types wired for isinstance, a component with
    BOTH bRepBodies and meshBodies collections, and a handle resolver."""
    import adsk.fusion
    adsk.fusion.BRepBody = FakeBRep
    adsk.fusion.MeshBody = FakeMesh
    brep_named = brep_named or {}
    mesh_named = mesh_named or {}
    handle_map = handle_map or {}

    class _Coll:
        """bRepBodies-style: HAS itemByName (the real BRepBodies does)."""
        def __init__(self, m):
            self._m = m
        def itemByName(self, n):
            return self._m.get(n)

    class _MeshColl:
        """meshBodies-style: REALISTIC - the live MeshBodies has NO itemByName, only count + item(i).
        Mesh-by-name must iterate."""
        def __init__(self, m):
            self._list = list(m.values())
        @property
        def count(self):
            return len(self._list)
        def item(self, i):
            return self._list[i] if 0 <= i < len(self._list) else None

    class FakeComp:
        bRepBodies = _Coll(brep_named)
        meshBodies = _MeshColl(mesh_named)

    class FakeDesign:
        rootComponent = FakeComp()
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    comp = FakeComp()
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d: comp
    return comp


class TestBodyKind:
    def test_default_kind_is_any_for_backcompat(self):
        # A pre-kind BodyRef accepted ANY BRepBody (no isSolid check). Default 'any' preserves that:
        # a surface body (isSolid False) must STILL resolve under the default.
        surf = FakeBRep("Surf", is_solid=False)
        _install_kind_bodies(handle_map={"H": surf})
        val, err = inp.BodyRef("body").resolve("H")
        assert err is None and val is surf

    def test_solid_kind_resolves_a_solid(self):
        s = FakeBRep("S", is_solid=True)
        _install_kind_bodies(handle_map={"H": s})
        val, err = inp.BodyRef("body", kind="solid").resolve("H")
        assert err is None and val is s

    def test_solid_kind_rejects_a_surface_with_redirect(self):
        surf = FakeBRep("Surf", is_solid=False)
        _install_kind_bodies(handle_map={"H": surf})
        val, err = inp.BodyRef("target", kind="solid").resolve("H")
        assert val is None
        assert "must be a SOLID body" in err and "OPEN SURFACE body" in err

    def test_solid_kind_rejects_a_mesh_with_redirect(self):
        # the headline redirect: solid asked, MESH given -> name the mesh + point at mesh_* / convert
        m = FakeMesh("M")
        _install_kind_bodies(handle_map={"H": m})
        val, err = inp.BodyRef("target", kind="solid").resolve("H")
        assert val is None
        assert "must be a SOLID body" in err and "MESH body" in err and "mesh_to_brep" in err

    def test_surface_kind_resolves_a_surface(self):
        surf = FakeBRep("Surf", is_solid=False)
        _install_kind_bodies(handle_map={"H": surf})
        val, err = inp.SurfaceBodyRef("body").resolve("H")
        assert err is None and val is surf

    def test_surface_kind_rejects_a_solid(self):
        s = FakeBRep("S", is_solid=True)
        _install_kind_bodies(handle_map={"H": s})
        val, err = inp.SurfaceBodyRef("body").resolve("H")
        assert val is None and "must be an OPEN SURFACE body" in err and "SOLID body" in err

    def test_mesh_kind_resolves_a_mesh(self):
        m = FakeMesh("M")
        _install_kind_bodies(handle_map={"H": m})
        val, err = inp.MeshBodyRef("body").resolve("H")
        assert err is None and val is m

    def test_mesh_kind_rejects_a_brep_solid(self):
        # mesh-vs-brep discrimination: a BRep solid handed to a mesh input is redirected, not accepted
        s = FakeBRep("S", is_solid=True)
        _install_kind_bodies(handle_map={"H": s})
        val, err = inp.MeshBodyRef("body").resolve("H")
        assert val is None and "must be a MESH body" in err and "SOLID body" in err

    def test_mesh_resolves_by_name_from_meshBodies(self):
        # name lookup searches meshBodies too - a mesh name must never be an invisible miss
        m = FakeMesh("ScanData")
        _install_kind_bodies(mesh_named={"ScanData": m})
        val, err = inp.MeshBodyRef("body").resolve("ScanData")
        assert err is None and val is m

    def test_a_mesh_in_a_subcomponent_resolves_through_the_component_walk(self):
        # A mesh in a sub-component must resolve by name even though the OCCURRENCE cannot answer for
        # it: reading meshBodies off an occurrence RAISES, so the occurrence pass can never see a
        # mesh. The design-wide component walk is the one path that reaches it - this fake raises on
        # occ.meshBodies exactly as live does, so a resolver that leaned on the occurrence fails here.
        import adsk.fusion
        adsk.fusion.BRepBody = FakeBRep
        adsk.fusion.MeshBody = FakeMesh
        m = FakeMesh("Occ_Scan")

        class _Coll:
            """bRepBodies-style: HAS itemByName."""
            def __init__(self, d):
                self._d = d
            def itemByName(self, n):
                return self._d.get(n)
            @property
            def count(self):
                return len(self._d)
            def item(self, i):
                return list(self._d.values())[i]

        class _MeshColl:
            """meshBodies-style: REALISTIC - no itemByName, only count + item(i)."""
            def __init__(self, d):
                self._list = list(d.values())
            @property
            def count(self):
                return len(self._list)
            def item(self, i):
                return self._list[i] if 0 <= i < len(self._list) else None

        class _SubComp:
            """The sub-COMPONENT that owns the mesh (reachable via design.allComponents)."""
            name = "Scanned"
            bRepBodies = _Coll({})
            meshBodies = _MeshColl({"Occ_Scan": m})

        sub = _SubComp()

        class _Occ:
            """The occurrence of that component: bRepBodies reads fine, meshBodies RAISES."""
            bRepBodies = _Coll({})
            component = sub
            @property
            def meshBodies(self):
                raise AttributeError("MeshBodies is not readable on an Occurrence")

        class _RootComp:
            name = "Root"
            bRepBodies = _Coll({})
            meshBodies = _MeshColl({})
            allOccurrences = [_Occ()]

        root = _RootComp()

        class FakeDesign:
            rootComponent = root
            allComponents = _NamedCollection([root, sub])
            def findEntityByToken(self, h):
                return []   # not a handle -> force the name path

        inp._common.design = lambda: FakeDesign()
        # target_component is the root (which has NO matching mesh) -> resolution must reach the
        # sub-component through the component walk, not through the occurrence.
        inp._common.target_component = lambda d: root
        val, err = inp.MeshBodyRef("body").resolve("Occ_Scan")
        assert err is None and val is m

    def test_any_kind_accepts_solid_surface_and_mesh(self):
        s, surf, m = FakeBRep("S", True), FakeBRep("Surf", False), FakeMesh("M")
        _install_kind_bodies(handle_map={"S": s, "U": surf, "M": m})
        for h, want in (("S", s), ("U", surf), ("M", m)):
            val, err = inp.BodyRef("body", kind="any").resolve(h)
            assert err is None and val is want

    def test_list_kind_checks_every_element_before_returning(self):
        # one wrong-kind element fails the WHOLE list (so no partial mutation downstream), with its index
        s1, m = FakeBRep("S1", True), FakeMesh("M")
        _install_kind_bodies(handle_map={"S1": s1, "M": m})
        val, err = inp.BodyRefList("bodies", kind="solid").resolve(["S1", "M"])
        assert val is None and "[1]" in err and "must be a SOLID body" in err

    def test_list_all_correct_kind_resolves_in_order(self):
        s1, s2 = FakeBRep("S1", True), FakeBRep("S2", True)
        _install_kind_bodies(handle_map={"S1": s1, "S2": s2})
        val, err = inp.BodyRefList("bodies", kind="solid").resolve(["S1", "S2"])
        assert err is None and val == [s1, s2]

    def test_surface_list_alias(self):
        u1, u2 = FakeBRep("U1", False), FakeBRep("U2", False)
        _install_kind_bodies(handle_map={"U1": u1, "U2": u2})
        val, err = inp.SurfaceBodyRefList("bodies").resolve(["U1", "U2"])
        assert err is None and val == [u1, u2]


# ── BodyRef kind='brep': a SOLID or SURFACE BRep body, but NOT a mesh (model_split's target/cutter) ──

class TestBodyBrepKind:
    def test_brep_resolves_a_solid(self):
        s = FakeBRep("S", is_solid=True)
        _install_kind_bodies(handle_map={"H": s})
        val, err = inp.BodyRef("body", kind="brep").resolve("H")
        assert err is None and val is s

    def test_brep_resolves_a_surface(self):
        surf = FakeBRep("Surf", is_solid=False)
        _install_kind_bodies(handle_map={"H": surf})
        val, err = inp.BodyRef("body", kind="brep").resolve("H")
        assert err is None and val is surf

    def test_brep_rejects_a_mesh_with_redirect(self):
        # 'brep' = solid OR surface but EXCLUDES a mesh -> a mesh is redirected, not accepted
        m = FakeMesh("M")
        _install_kind_bodies(handle_map={"H": m})
        val, err = inp.BodyRef("target", kind="brep").resolve("H")
        assert val is None
        assert "must be a SOLID or SURFACE" in err and "MESH body" in err and "mesh_to_brep" in err


# ── BodyRef by-NAME ambiguity refusal: a name matching 2+ bodies is refused, not first-matched ───
# A body's name is only LOCALLY unique (like an occurrence's). Two same-named bodies (e.g. a part
# instanced twice) must ERROR with the candidate list, not silently grab the first. A precise handle is
# never ambiguous. This mirrors OccurrenceRef's _resolve_occurrence house pattern.

def _install_ambiguous_bodies(*, handle_map=None, occ_bodies=()):
    """Wire a design whose ROOT has empty bRepBodies but whose OCCURRENCES each carry a body, so a name
    can match several distinct proxies. `occ_bodies` = list of (name -> body) dicts, one per occurrence.
    handle_map feeds the precise-handle path."""
    import adsk.fusion
    adsk.fusion.BRepBody = FakeBRep
    adsk.fusion.MeshBody = FakeMesh
    handle_map = handle_map or {}

    class _BColl:
        """bRepBodies: itemByName answering the name AS SPELLED, plus the count/item protocol - so a
        case-variant match can only come from the iteration pass, never from the named lookup."""
        def __init__(self, m): self._m = m
        def itemByName(self, n): return self._m.get(n)
        @property
        def count(self): return len(self._m)
        def item(self, i): return list(self._m.values())[i]

    class _Occ:
        def __init__(self, m): self.bRepBodies = _BColl(m)

    occs = [_Occ(m) for m in occ_bodies]

    class _Root:
        bRepBodies = _BColl({})
        allOccurrences = occs

    class FakeDesign:
        rootComponent = _Root()
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    root = _Root()
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d=None: root
    return root


def _install_native_and_proxy(comp_bodies=(), occ_bodies=(), comp_name="Probe"):
    """Wire the design shape a body's TWO reachable wrappers come from: the ACTIVE component owns
    `comp_bodies` natively, and the root's allOccurrences pass hands back `occ_bodies` - a list of
    (occurrence fullPathName, [bodies]) pairs, the proxies. The root itself owns no bodies, so the
    same physical body arrives once per pass and the de-dup key is what decides how many candidates
    a name has."""
    import adsk.fusion
    adsk.fusion.BRepBody = BRepBody
    adsk.fusion.MeshBody = FakeMesh
    comp = types.SimpleNamespace(name=comp_name, bRepBodies=_NamedCollection(comp_bodies))
    for b in comp_bodies:
        b.parentComponent = comp
    occs = [types.SimpleNamespace(name=path, fullPathName=path, bRepBodies=_NamedCollection(bodies))
            for path, bodies in occ_bodies]
    root = types.SimpleNamespace(name="Root", bRepBodies=_NamedCollection([]), allOccurrences=occs)
    design = types.SimpleNamespace(rootComponent=root, allComponents=None,
                                   findEntityByToken=lambda h: [])
    inp._common.design = lambda: design
    inp._common.target_component = lambda d=None: comp
    return comp


class TestBodyNameAmbiguity:
    def test_ambiguous_name_is_refused_with_candidates(self):
        pin_a = FakeBRep("Pin", is_solid=True)
        pin_b = FakeBRep("Pin", is_solid=True)
        pin_a.assemblyContext = type("O", (), {"fullPathName": "Sub-A:1"})()
        pin_b.assemblyContext = type("O", (), {"fullPathName": "Sub-B:1"})()
        _install_ambiguous_bodies(occ_bodies=[{"Pin": pin_a}, {"Pin": pin_b}])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert val is None
        assert "ambiguous" in err.lower()
        assert "Sub-A:1" in err and "Sub-B:1" in err        # both candidate contexts listed

    def test_a_single_named_body_still_resolves(self):
        only = FakeBRep("Pin", is_solid=True)
        _install_ambiguous_bodies(occ_bodies=[{"Pin": only}])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert err is None and val is only

    def test_one_body_reached_by_two_paths_is_not_falsely_ambiguous(self):
        # One physical body is reachable through several collection paths (active component, root, an
        # occurrence proxy) and the API returns a FRESH wrapper object each time. De-dup MUST key on
        # the stable entityToken, not id() - or the same body counts once per path and reports a
        # spurious ambiguity. Two distinct wrappers, one shared token -> resolves as ONE.
        wrap_a = FakeBRep("Pin", is_solid=True, entity_token="TOK-PIN")
        wrap_b = FakeBRep("Pin", is_solid=True, entity_token="TOK-PIN")
        assert wrap_a is not wrap_b                          # genuinely different objects...
        _install_ambiguous_bodies(occ_bodies=[{"Pin": wrap_a}, {"Pin": wrap_b}])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert err is None and val is not None              # ...but the same body -> not ambiguous

    def test_ambiguity_lists_each_candidate_in_the_qualified_form(self):
        # the refusal must hand back the string that RESOLVES - '<occurrence-or-component>:<body>' -
        # not just a prose "in Sub-A:1", so the caller can re-issue without a second lookup.
        pin_a, pin_b = FakeBRep("Pin", is_solid=True), FakeBRep("Pin", is_solid=True)
        pin_a.assemblyContext = type("O", (), {"fullPathName": "Sub-A:1"})()
        pin_b.assemblyContext = type("O", (), {"fullPathName": "Sub-B:1"})()
        _install_ambiguous_bodies(occ_bodies=[{"Pin": pin_a}, {"Pin": pin_b}])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert val is None and "'Sub-A:1:Pin'" in err and "'Sub-B:1:Pin'" in err

    def test_qualified_scope_body_name_picks_one_of_the_candidates(self):
        pin_a, pin_b = FakeBRep("Pin", is_solid=True), FakeBRep("Pin", is_solid=True)
        pin_a.assemblyContext = type("O", (), {"fullPathName": "Sub-A:1"})()
        pin_b.assemblyContext = type("O", (), {"fullPathName": "Sub-B:1"})()
        _install_ambiguous_bodies(occ_bodies=[{"Pin": pin_a}, {"Pin": pin_b}])
        val, err = inp.BodyRef("body").resolve("Sub-B:1:Pin")
        assert err is None and val is pin_b

    def test_one_body_reached_twice_with_an_unreadable_token_is_not_ambiguous(self):
        # itemByName and item(i) each hand back a FRESH wrapper of the same physical body, and both
        # lookups run (one answers the spelling, the other the case variants + meshes). De-dup keys on
        # the entityToken and, when THAT is unreadable, on (name, scope) - never on object identity,
        # which would refuse ONE body as several candidates all printing the same name.
        class _Unreadable:
            name = "Pin"
            isSolid = True
            parentComponent = types.SimpleNamespace(name="Frame")
            assemblyContext = None

            @property
            def entityToken(self):
                raise RuntimeError("3 : entityToken is unavailable")

        class _FreshColl:
            """Every read mints a NEW wrapper of the one body, as the live collection does."""
            @property
            def count(self):
                return 1
            def item(self, i):
                return _Unreadable()
            def itemByName(self, n):
                return _Unreadable() if n == "Pin" else None

        root = types.SimpleNamespace(name="Frame", bRepBodies=_FreshColl(), allOccurrences=[])
        design = types.SimpleNamespace(rootComponent=root, activeComponent=root)
        inp._common.design = lambda: design
        inp._common.target_component = lambda d=None: root
        val, err = inp.BodyRef("body").resolve("Pin")
        assert err is None and val is not None and val.name == "Pin"

    def test_a_slash_qualified_label_resolves_like_the_colon_form(self):
        # model_extrude / model_fillet_chamfer publish their body labels as '<scope>/<body>'; the
        # resolver accepts that spelling too, so a label a tool printed can be handed straight back.
        pin_a, pin_b = FakeBRep("Pin", is_solid=True), FakeBRep("Pin", is_solid=True)
        pin_a.assemblyContext = type("O", (), {"fullPathName": "Sub-A:1"})()
        pin_b.assemblyContext = type("O", (), {"fullPathName": "Sub-B:1"})()
        _install_ambiguous_bodies(occ_bodies=[{"Pin": pin_a}, {"Pin": pin_b}])
        val, err = inp.BodyRef("body").resolve("Sub-B:1/Pin")
        assert err is None and val is pin_b

    def test_a_case_variant_name_resolves(self):
        # the by-name path matches case-insensitively, so an agent that typed 'pin' is not told the
        # body does not exist (the named lookup alone answers only the spelling it was given).
        only = FakeBRep("Pin", is_solid=True)
        _install_ambiguous_bodies(occ_bodies=[{"Pin": only}])
        val, err = inp.BodyRef("body").resolve("pin")
        assert err is None and val is only

    def test_the_exact_spelling_wins_over_a_case_variant(self):
        # widening to case-insensitive must not manufacture an ambiguity: two bodies differing only
        # in case, asked for by exact spelling, resolve to the one spelled that way.
        pin, lower = FakeBRep("Pin", is_solid=True), FakeBRep("pin", is_solid=True)
        pin.assemblyContext = type("O", (), {"fullPathName": "Sub-A:1"})()
        lower.assemblyContext = type("O", (), {"fullPathName": "Sub-B:1"})()
        _install_ambiguous_bodies(occ_bodies=[{"Pin": pin}, {"pin": lower}])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert err is None and val is pin
        val2, err2 = inp.BodyRef("body").resolve("pin")
        assert err2 is None and val2 is lower

    def test_one_body_reached_natively_and_as_its_ONE_proxy_is_ONE_candidate(self):
        # The two-token shape: a body and its occurrence PROXY carry DIFFERENT entityTokens, and
        # _collect_bodies_by_name reaches the SAME physical body twice - natively through the active
        # component, then as a proxy through the allOccurrences pass. Keyed on each wrapper's own
        # token that is two candidates and the name is refused as ambiguous, listing the one body
        # under both contexts. Grouped by the PHYSICAL body it is one candidate: the placement.
        native = BRepBody("Probe", entity_token="TOK-NATIVE")
        proxy = body_proxy(native, types.SimpleNamespace(name="Probe:1", fullPathName="Probe:1"))
        assert proxy.entityToken != native.entityToken     # the measured pair, not a shared token
        assert proxy.nativeObject is native and native.nativeObject is None
        _install_native_and_proxy(comp_bodies=[native], occ_bodies=[("Probe:1", [proxy])])
        val, err = inp.BodyRef("body").resolve("Probe")
        assert err is None, err
        assert val is proxy                                # the PLACEMENT, which carries the context

    def test_a_body_reached_ONLY_as_a_proxy_still_resolves_to_that_proxy(self):
        native = BRepBody("Probe", entity_token="TOK-NATIVE")
        proxy = body_proxy(native, types.SimpleNamespace(name="Probe:1", fullPathName="Probe:1"))
        _install_native_and_proxy(comp_bodies=[], occ_bodies=[("Probe:1", [proxy])])
        val, err = inp.BodyRef("body").resolve("Probe")
        assert err is None and val is proxy

    def test_an_UNPLACED_bodys_native_is_the_candidate(self):
        # The native is dropped only when a placement exists to replace it. A body no occurrence
        # references (a root-level body, or a component nothing instances) has only its native, and
        # dropping that would refuse a body that is not ambiguous at all.
        native = BRepBody("Probe", entity_token="TOK-NATIVE")
        _install_native_and_proxy(comp_bodies=[native], occ_bodies=[])
        val, err = inp.BodyRef("body").resolve("Probe")
        assert err is None and val is native

    def test_a_component_placed_TWICE_still_refuses_the_bare_name(self):
        # Two placements of ONE body are two world positions. The physical body groups to one key, but
        # each placement is its own candidate, so the bare name is refused with both instance-qualified
        # spellings - picking either would target a placement the caller never chose.
        native = BRepBody("Pin", entity_token="TOK-NATIVE")
        one = body_proxy(native, types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1"))
        two = body_proxy(native, types.SimpleNamespace(name="Jaw:2", fullPathName="Jaw:2"))
        _install_native_and_proxy(comp_bodies=[native], comp_name="Jaw",
                                  occ_bodies=[("Jaw:1", [one]), ("Jaw:2", [two])])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert val is None and "ambiguous" in err.lower()
        assert "'Jaw:1:Pin'" in err and "'Jaw:2:Pin'" in err
        assert "'Jaw:Pin'" not in err          # the dropped native is not offered as a candidate

    def test_two_placements_with_unreadable_paths_still_refuse(self):
        # Group members are keyed by each wrapper's OWN token, with the printable context only as a
        # fallback - keyed on the context, two placements whose fullPathName raises read the same
        # "Jaw" string, silently merge, and the ambiguity degrades to a first-placement pick.
        class _RaisingPath:
            def __init__(self, name):
                self.name = name
            @property
            def fullPathName(self):
                raise RuntimeError("4 : An API Object refers to a deleted Object")
        native = BRepBody("Pin", entity_token="TOK-NATIVE")
        one = body_proxy(native, _RaisingPath("Jaw:1"))
        two = body_proxy(native, _RaisingPath("Jaw:2"))
        _install_native_and_proxy(comp_bodies=[native], comp_name="Jaw",
                                  occ_bodies=[("Jaw:1", [one]), ("Jaw:2", [two])])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert val is None and "ambiguous" in err.lower()

    def test_the_COMPONENT_qualified_form_also_refuses_when_placed_twice(self):
        # 'Jaw:Pin' names the component, which both instances answer to - so it is still ambiguous and
        # is refused with the instance-qualified spellings that are not.
        native = BRepBody("Pin", entity_token="TOK-NATIVE")
        one = body_proxy(native, types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1"))
        two = body_proxy(native, types.SimpleNamespace(name="Jaw:2", fullPathName="Jaw:2"))
        _install_native_and_proxy(comp_bodies=[native], comp_name="Jaw",
                                  occ_bodies=[("Jaw:1", [one]), ("Jaw:2", [two])])
        val, err = inp.BodyRef("body").resolve("Jaw:Pin")
        assert val is None and "ambiguous" in err.lower()
        assert "'Jaw:1:Pin'" in err and "'Jaw:2:Pin'" in err

    def test_one_instance_of_a_twice_placed_component_resolves_by_its_own_name(self):
        # The way OUT of that refusal: the instance-qualified spelling the refusal listed resolves.
        native = BRepBody("Pin", entity_token="TOK-NATIVE")
        one = body_proxy(native, types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1"))
        two = body_proxy(native, types.SimpleNamespace(name="Jaw:2", fullPathName="Jaw:2"))
        _install_native_and_proxy(comp_bodies=[native], comp_name="Jaw",
                                  occ_bodies=[("Jaw:1", [one]), ("Jaw:2", [two])])
        val, err = inp.BodyRef("body").resolve("Jaw:2:Pin")
        assert err is None and val is two

    def test_two_DIFFERENT_bodies_sharing_a_name_are_still_refused(self):
        # The grouping is per PHYSICAL body: two bodies with their own native tokens group apart and
        # the ambiguity refusal stands, with both contexts listed.
        a, b = BRepBody("Pin", entity_token="TOK-A"), BRepBody("Pin", entity_token="TOK-B")
        pa = body_proxy(a, types.SimpleNamespace(name="Jaw:1", fullPathName="Jaw:1"))
        pb = body_proxy(b, types.SimpleNamespace(name="Clamp:1", fullPathName="Clamp:1"))
        _install_native_and_proxy(comp_bodies=[], occ_bodies=[("Jaw:1", [pa]), ("Clamp:1", [pb])])
        val, err = inp.BodyRef("body").resolve("Pin")
        assert val is None and "ambiguous" in err.lower()
        assert "'Jaw:1:Pin'" in err and "'Clamp:1:Pin'" in err

    def test_the_key_of_a_proxy_IS_its_natives_token(self):
        native = BRepBody("Probe", entity_token="TOK-NATIVE")
        proxy = body_proxy(native, types.SimpleNamespace(name="Probe:1", fullPathName="Probe:1"))
        assert inp._body_key(proxy) == inp._body_key(native) == "TOK-NATIVE"

    def test_a_wrapper_that_does_not_answer_nativeObject_keys_on_its_OWN_token(self):
        # nativeObject is read through safe(): a wrapper kind that does not answer it at all still
        # has its own token to key on, and must not be dropped to the (name, scope) fallback.
        b = BRepBody("Probe", entity_token="TOK-ONLY")
        del b.nativeObject
        assert inp._body_key(b) == "TOK-ONLY"

    def test_an_unreadable_token_falls_back_to_name_and_scope(self):
        # With no token at either end the key is (name, scope): two fresh wrappers of one body in one
        # scope collapse, and a same-named body in ANOTHER scope stays a separate candidate.
        frame = types.SimpleNamespace(name="Frame")
        one, again = BRepBody("Pin", parent_component=frame), BRepBody("Pin", parent_component=frame)
        other = BRepBody("Pin", parent_component=types.SimpleNamespace(name="Lid"))
        for b in (one, again, other):
            del b.entityToken
        assert inp._body_key(one) == inp._body_key(again) == ("Pin", "Frame")
        assert inp._body_key(other) != inp._body_key(one)

    def test_a_handle_is_never_ambiguous_even_when_name_is_duplicated(self):
        # the precise path: two 'Pin' bodies exist by name, but a HANDLE resolves ONE directly
        pin_a = FakeBRep("Pin", is_solid=True)
        pin_b = FakeBRep("Pin", is_solid=True)
        target = FakeBRep("Pin", is_solid=True)
        _install_ambiguous_bodies(handle_map={"HANDLE": target},
                                  occ_bodies=[{"Pin": pin_a}, {"Pin": pin_b}])
        val, err = inp.BodyRef("body").resolve("HANDLE")
        assert err is None and val is target


# ── ModeGuard: declarative precondition, error DERIVED from the requirement (non-invertible) ─────

class _FakeModeDesign:
    """A design whose designType maps to parametric/direct via the numeric convention (1/0)."""
    def __init__(self, design_type=None, edit_object=None):
        if design_type is not None:
            self.designType = design_type
        self.activeEditObject = edit_object


class _FakeBaseFeature:
    pass


def _install_mode():
    """Wire BaseFeature so current_design_type / _in_base_feature_scope work (the DesignTypes ints
    come seeded from live_api_facts)."""
    import adsk.fusion
    adsk.fusion.BaseFeature = _FakeBaseFeature


class TestModeGuard:
    def test_current_design_type_reads_parametric(self):
        _install_mode()
        assert inp.current_design_type(_FakeModeDesign(design_type=1)) == inp.MODE_PARAMETRIC

    def test_current_design_type_reads_direct(self):
        _install_mode()
        assert inp.current_design_type(_FakeModeDesign(design_type=0)) == inp.MODE_DIRECT

    def test_current_design_type_unknown_when_unreadable(self):
        _install_mode()
        # a design with no designType attribute -> 'unknown', not a crash
        assert inp.current_design_type(_FakeModeDesign(design_type=None)) == "unknown"

    def test_parametric_guard_passes_in_parametric(self):
        _install_mode()
        g = inp.ModeGuard(inp.MODE_PARAMETRIC)
        ok, err = g.check(_FakeModeDesign(design_type=1))
        assert ok is True and err is None

    def test_direct_guard_fails_in_parametric(self):
        _install_mode()
        g = inp.ModeGuard(inp.MODE_DIRECT, why="setByPoint is direct-only.", fix_hint="Switch modes.")
        ok, err = g.check(_FakeModeDesign(design_type=1))
        assert ok is False and err["isError"] is True

    def test_error_names_the_REQUIRED_mode_not_inverted(self):
        # the anti-inversion proof: requiring DIRECT, sitting in PARAMETRIC, the message must say it
        # needs DIRECT (and report the actual PARAMETRIC) — it cannot tell you to switch the wrong way.
        _install_mode()
        g = inp.ModeGuard(inp.MODE_DIRECT)
        ok, err = g.check(_FakeModeDesign(design_type=1))
        msg = err["message"]
        assert f"needs {inp.MODE_DIRECT} mode" in msg
        assert f"in {inp.MODE_PARAMETRIC} mode" in msg

    def test_parametric_guard_error_names_parametric(self):
        # symmetric direction check: requiring PARAMETRIC while DIRECT names PARAMETRIC as the need
        _install_mode()
        g = inp.ModeGuard(inp.MODE_PARAMETRIC)
        ok, err = g.check(_FakeModeDesign(design_type=0))
        assert ok is False and f"needs {inp.MODE_PARAMETRIC} mode" in err["message"]

    def test_base_feature_guard_passes_inside_a_base_feature_scope(self):
        _install_mode()
        des = _FakeModeDesign(design_type=1, edit_object=_FakeBaseFeature())
        g = inp.ModeGuard(inp.MODE_BASE_FEATURE)
        ok, err = g.check(des)
        assert ok is True and err is None

    def test_base_feature_guard_fails_without_scope(self):
        _install_mode()
        des = _FakeModeDesign(design_type=1, edit_object=None)
        g = inp.ModeGuard(inp.MODE_BASE_FEATURE)
        ok, err = g.check(des)
        assert ok is False and "BASE-FEATURE edit scope" in err["message"]

    def test_contract_note(self):
        _install_mode()
        assert inp.ModeGuard(inp.MODE_DIRECT).contract_note() == "Requires direct mode."
        assert "base-feature" in inp.ModeGuard(inp.MODE_BASE_FEATURE).contract_note()


# ── ProfileRef / ProfileRefList: stable handle first, legacy {sketch, index} fallback, ORDER-keeping ─

class FakeProfile:
    def __init__(self, tag):
        self.tag = tag


def _install_profiles(handle_map=None, sketches=None, monkeypatch=None):
    """Wire adsk.fusion.Profile for isinstance, a handle resolver, and a component whose `sketches`
    collection exposes named sketches each owning a `profiles` counted collection.

    `sketches`: ordered list of (name, [FakeProfile, ...]) or (name, [...], text_count). The LAST is
    the 'most recent'. `text_count` populates sketchTexts, the 'text:<i>' address space; each text is
    tagged '<sketch>#<i>' so a test can tell WHICH one resolved."""
    import adsk.fusion
    adsk.fusion.Profile = FakeProfile
    handle_map = handle_map or {}
    sketches = sketches or []

    class _Profiles:
        def __init__(self, items):
            self._items = items
        @property
        def count(self):
            return len(self._items)
        def item(self, i):
            return self._items[i] if 0 <= i < len(self._items) else None

    class _Sketch:
        def __init__(self, name, profs, ntexts=0):
            self.name = name
            self.profiles = _Profiles(profs)
            self.sketchTexts = _Profiles([types.SimpleNamespace(tag=f"{name}#{i}")
                                          for i in range(ntexts)])

    sk_objs = [_Sketch(*row) for row in sketches]

    class _Sketches:
        @property
        def count(self):
            return len(sk_objs)
        def item(self, i):
            return sk_objs[i] if 0 <= i < len(sk_objs) else None
        def itemByName(self, n):
            for s in sk_objs:
                if s.name == n:
                    return s
            return None

    class FakeComp:
        sketches = _Sketches()

    class FakeDesign:
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    comp = FakeComp()
    des = FakeDesign()
    if monkeypatch is not None:
        monkeypatch.setattr(inp._common, "design", lambda: des)
        monkeypatch.setattr(inp._common, "target_component", lambda d: comp)
    else:
        inp._common.design = lambda: des
        inp._common.target_component = lambda d: comp
    return comp


class TestProfileRef:
    def test_resolves_a_handle_first(self):
        p = FakeProfile("P")
        _install_profiles(handle_map={"PROF": p})
        val, err = inp.ProfileRef("profile").resolve("PROF")
        assert err is None and val is p

    def test_handle_to_non_profile_rejected(self):
        notp = object()
        _install_profiles(handle_map={"X": notp})
        val, err = inp.ProfileRef("profile").resolve("X")
        assert val is None and "not a profile" in err

    def test_legacy_selector_by_sketch_and_index(self):
        p0, p1 = FakeProfile("p0"), FakeProfile("p1")
        _install_profiles(sketches=[("Sketch1", [p0, p1])])
        val, err = inp.ProfileRef("profile").resolve({"sketch": "Sketch1", "profile_index": 1})
        assert err is None and val is p1

    def test_legacy_selector_blank_sketch_uses_most_recent(self):
        a, b = FakeProfile("a"), FakeProfile("b")
        _install_profiles(sketches=[("Old", [a]), ("New", [b])])
        val, err = inp.ProfileRef("profile").resolve({"profile_index": 0})
        assert err is None and val is b          # most-recent sketch

    def test_legacy_index_out_of_range(self):
        _install_profiles(sketches=[("S", [FakeProfile("p0")])])
        val, err = inp.ProfileRef("profile").resolve({"sketch": "S", "profile_index": 5})
        assert val is None and "out of range" in err

    def test_legacy_unknown_sketch(self):
        _install_profiles(sketches=[("S", [FakeProfile("p0")])])
        val, err = inp.ProfileRef("profile").resolve({"sketch": "Nope", "profile_index": 0})
        assert val is None and "no sketch named" in err


class FakeAreaProfile(FakeProfile):
    """A profile carrying areaProperties() - what the locator re-find reads."""
    def __init__(self, tag, centroid=(0.0, 0.0, 0.0), area=1.0):
        super().__init__(tag)
        self._c, self._a = centroid, area

    def areaProperties(self):
        c = type("C", (), {})()
        c.x, c.y, c.z = self._c
        return type("AP", (), {"centroid": c, "area": self._a})()


class TestProfileHandleLocator:
    """findEntityByToken returns NOTHING for a sub-component sketch profile's token (live API fact),
    so a profile handle's '|@profile[<sketch>~<area>]:<centroid>' locator is its real resolution
    path - these pin that path."""

    def test_dead_token_resolves_via_sketch_area_locator(self):
        band = FakeAreaProfile("band", centroid=(0.0, 0.0, 0.0), area=27.269)
        _install_profiles(sketches=[("OuterRingSketch", [band])])
        h = "DEADTOKEN|@profile[OuterRingSketch~27.2690]:0.000000,0.000000,0.000000"
        val, err = inp.ProfileRef("profile").resolve(h)
        assert err is None and val is band

    def test_area_disambiguates_same_centroid_profiles(self):
        # An annulus band and its full disk share centroid (0,0,0); only the area tells them apart.
        # A centroid-only match would grab whichever scans first - the wrong-region extrude.
        disk = FakeAreaProfile("disk", centroid=(0.0, 0.0, 0.0), area=78.5398)
        band = FakeAreaProfile("band", centroid=(0.0, 0.0, 0.0), area=27.269)
        _install_profiles(sketches=[("OuterRingSketch", [disk, band])])
        h = "DEADTOKEN|@profile[OuterRingSketch~27.2690]:0.000000,0.000000,0.000000"
        val, err = inp.ProfileRef("profile").resolve(h)
        assert err is None and val is band

    def test_wrong_area_is_a_miss_not_a_nearest_grab(self):
        disk = FakeAreaProfile("disk", centroid=(0.0, 0.0, 0.0), area=78.5398)
        _install_profiles(sketches=[("S", [disk])])
        h = "DEADTOKEN|@profile[S~999.0000]:0.000000,0.000000,0.000000"
        val, err = inp.ProfileRef("profile").resolve(h)
        assert val is None and "did not resolve" in err

    def test_far_centroid_is_a_miss(self):
        p = FakeAreaProfile("p", centroid=(5.0, 0.0, 0.0), area=10.0)
        _install_profiles(sketches=[("S", [p])])
        h = "DEADTOKEN|@profile[S~10.0000]:0.000000,0.000000,0.000000"
        val, err = inp.ProfileRef("profile").resolve(h)
        assert val is None and "did not resolve" in err

    def test_legacy_named_sketch_resolves_design_wide(self):
        # The sketch lives in a SUB-component while root is active: the {sketch, index} selector
        # must reach it (an active-component-scoped lookup reports 'no sketch named' instead).
        import adsk.fusion
        adsk.fusion.Profile = FakeProfile
        p = FakeProfile("sub-profile")

        class _Profiles:
            count = 1
            def item(self, i):
                return p if i == 0 else None

        class _SkColl:
            def __init__(self, sks):
                self._sks = sks
            @property
            def count(self):
                return len(self._sks)
            def item(self, i):
                return self._sks[i]
            def itemByName(self, n):
                for s in self._sks:
                    if s.name == n:
                        return s
                return None

        sub_sketch = type("Sk", (), {"name": "FrameSketch", "profiles": _Profiles()})()
        root = type("C", (), {"name": "Root", "sketches": _SkColl([])})()
        sub = type("C", (), {"name": "Frame", "sketches": _SkColl([sub_sketch])})()

        class _CompColl:
            _l = [root, sub]
            @property
            def count(self):
                return len(self._l)
            def item(self, i):
                return self._l[i]

        class FakeDesign:
            rootComponent = root
            allComponents = _CompColl()
            def findEntityByToken(self, h):
                return []
        inp._common.design = lambda: FakeDesign()
        inp._common.target_component = lambda d: root
        val, err = inp.ProfileRef("profile").resolve({"sketch": "FrameSketch", "profile_index": 0})
        assert err is None and val is p


class TestProfileRefList:
    def test_resolves_handles_in_order(self):
        p0, p1, p2 = FakeProfile("0"), FakeProfile("1"), FakeProfile("2")
        _install_profiles(handle_map={"A": p0, "B": p1, "C": p2})
        val, err = inp.ProfileRefList("profiles").resolve(["A", "B", "C"])
        assert err is None and val == [p0, p1, p2]

    def test_order_is_PRESERVED_not_sorted(self):
        # loft order is load-bearing: a reversed input must come back reversed, no sort/dedupe
        p0, p1, p2 = FakeProfile("0"), FakeProfile("1"), FakeProfile("2")
        _install_profiles(handle_map={"A": p0, "B": p1, "C": p2})
        val, err = inp.ProfileRefList("profiles").resolve(["C", "A", "B"])
        assert err is None and val == [p2, p0, p1]

    def test_duplicates_are_NOT_deduped(self):
        p = FakeProfile("0")
        _install_profiles(handle_map={"A": p})
        val, err = inp.ProfileRefList("profiles").resolve(["A", "A"])
        assert err is None and val == [p, p]      # both kept — loft may revisit a section

    def test_mixed_handles_and_legacy_selectors(self):
        ph = FakeProfile("h")
        pl = FakeProfile("l")
        _install_profiles(handle_map={"H": ph}, sketches=[("S", [pl])])
        val, err = inp.ProfileRefList("profiles").resolve(["H", {"sketch": "S", "profile_index": 0}])
        assert err is None and val == [ph, pl]

    def test_one_bad_element_fails_with_index(self):
        p = FakeProfile("0")
        _install_profiles(handle_map={"A": p})
        val, err = inp.ProfileRefList("profiles").resolve(["A", "MISSING"])
        assert val is None and "[1]" in err


# ── a sketch TEXT as a profile input (allow_text) ────────────────────────────────────────────────
#
# A SketchText carries no Profile of its own, and the emboss/extrude createInput contracts take the
# text itself in the profile slot while sweep/revolve/loft do not - so the address resolves to the
# SketchText object, and only where the kind was declared allow_text.

class TestProfileRefSketchText:
    def test_the_published_text_id_resolves_to_the_sketch_text(self, monkeypatch):
        _install_profiles(sketches=[("Nameplate", [], 1)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("profiles", allow_text=True).resolve("text:0")
        assert err is None and val.tag == "Nameplate#0"

    def test_the_index_picks_that_text_not_the_first(self, monkeypatch):
        _install_profiles(sketches=[("Nameplate", [], 3)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("text:2")
        assert err is None and val.tag == "Nameplate#2"

    def test_a_sketch_qualified_id_targets_that_sketch_not_the_most_recent(self, monkeypatch):
        # blank-sketch resolution takes the LAST sketch, so an ignored '<sketch>/' prefix stamps the
        # wrong sketch's text - silently, since both resolve to a SketchText.
        _install_profiles(sketches=[("Nameplate", [], 1), ("Later", [], 1)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("Nameplate/text:0")
        assert err is None and val.tag == "Nameplate#0"
        bare, berr = inp.ProfileRef("p", allow_text=True).resolve("text:0")
        assert berr is None and bare.tag == "Later#0"

    def test_a_sketch_name_carrying_a_slash_still_addresses_its_text(self, monkeypatch):
        _install_profiles(sketches=[("Plate/Front", [], 1), ("Later", [], 1)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("Plate/Front/text:0")
        assert err is None and val.tag == "Plate/Front#0"

    def test_an_out_of_range_text_names_the_count_and_the_legal_span(self, monkeypatch):
        _install_profiles(sketches=[("Nameplate", [], 2)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("text:2")
        assert val is None
        assert "'text:2'" in err and "2 sketch text(s)" in err and "text:0..text:1" in err

    def test_a_negative_text_index_is_refused_by_the_range_guard(self, monkeypatch):
        # int('-1') parses, so only the lower bound of the range guard stops it; without that bound
        # 'text:-1' would index sketchTexts from the END and stamp the LAST text.
        _install_profiles(sketches=[("Nameplate", [], 2)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("text:-1")
        assert val is None
        assert "'text:-1' is out of range" in err
        assert "2 sketch text(s)" in err and "(text:0..text:1)" in err

    def test_a_malformed_text_index_is_refused_by_the_grammar_not_the_handle_path(self, monkeypatch):
        # 'text:abc' opens with the text grammar, so the refusal must talk about texts - falling
        # through to "did not resolve to a profile handle" is the dead end in miniature.
        _install_profiles(sketches=[("Nameplate", [], 2)], monkeypatch=monkeypatch)
        kind = inp.ProfileRef("p", allow_text=True)
        for raw in ("text:abc", "text:", "text:1.5"):
            val, err = kind.resolve(raw)
            assert val is None, raw
            assert f"'{raw}' carries no whole-number text index" in err
            assert "2 sketch text(s)" in err and "(text:0..text:1)" in err
            assert "profile handle" not in err

    def test_a_malformed_text_index_is_still_a_text_where_text_is_not_allowed(self, monkeypatch):
        # the same string reaches the allow_text refusal, not the handle path.
        _install_profiles(sketches=[("Nameplate", [], 2)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("profile").resolve("text:abc")
        assert val is None and "SKETCH TEXT" in err and "model_emboss" in err

    def test_a_sketch_qualified_malformed_index_names_that_sketch(self, monkeypatch):
        _install_profiles(sketches=[("Nameplate", [], 1), ("Later", [], 3)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("Nameplate/text:x")
        assert val is None
        assert "'Nameplate'" in err and "1 sketch text(s)" in err and "(text:0..text:0)" in err

    def test_a_sketch_holding_no_text_is_refused_by_name(self, monkeypatch):
        _install_profiles(sketches=[("Plain", [FakeProfile("p0")], 0)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("text:0")
        assert val is None and "'Plain' holds none" in err

    def test_a_text_is_REFUSED_where_the_feature_takes_only_profiles(self, monkeypatch):
        # none of sweep/revolve/loft names SketchText in its accepted list, so the kind refuses
        # first rather than handing the API an argument it rejects.
        _install_profiles(sketches=[("Nameplate", [], 1)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("profile").resolve("text:0")
        assert val is None
        assert "'text:0'" in err and "SKETCH TEXT" in err and "model_emboss" in err

    def test_a_non_text_entity_id_is_not_taken_for_a_text(self, monkeypatch):
        # only 'text:<i>' is a text address; 'line:0' must fall through to the handle path, not
        # resolve to whatever sits at sketchTexts[0].
        _install_profiles(sketches=[("Nameplate", [], 1)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRef("p", allow_text=True).resolve("line:0")
        assert val is None and "did not resolve to a profile handle" in err

    def test_a_text_only_sketch_teaches_the_text_route_instead_of_draw_a_region(self, monkeypatch):
        # a nameplate sketch has no closed profile and never will, so "draw one" is a dead end.
        _install_profiles(sketches=[("Nameplate", [], 2)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRefList("profiles", allow_text=True).resolve([{"sketch": "Nameplate"}])
        assert val is None
        assert "2 sketch text(s)" in err and "'text:0'..'text:1'" in err
        assert "Draw a closed region" not in err

    def test_the_same_dead_end_stays_plain_where_text_is_not_allowed(self, monkeypatch):
        _install_profiles(sketches=[("Nameplate", [], 2)], monkeypatch=monkeypatch)
        val, err = inp.ProfileRefList("profiles").resolve([{"sketch": "Nameplate"}])
        assert val is None and "Draw a closed region first." in err and "sketch text" not in err

    def test_a_list_keeps_order_across_a_profile_and_a_text(self, monkeypatch):
        prof = FakeProfile("region")
        _install_profiles(handle_map={"H": prof}, sketches=[("Nameplate", [], 1)],
                          monkeypatch=monkeypatch)
        val, err = inp.ProfileRefList("profiles", allow_text=True).resolve(["text:0", "H"])
        assert err is None
        assert val[0].tag == "Nameplate#0" and val[1] is prof

    def test_the_text_address_is_advertised_only_where_it_is_accepted(self):
        assert "text:<i>" in inp.ProfileRefList("profiles", allow_text=True).contract_note()
        assert "text:<i>" not in inp.ProfileRefList("profiles").contract_note()
        assert "text:<i>" in inp.ProfileRef("profile", allow_text=True).schema()["description"]


class TestIsTextRef:
    """is_text_ref is the ROUTING predicate over the same grammar ProfileRef.allow_text resolves:
    a tool whose one input carries a text address, a profile handle AND an index selector asks it
    before is_handle, which answers False for a short non-numeric string like 'text:0'."""

    def test_both_published_forms_are_text_addresses(self):
        assert inp.is_text_ref("text:0") is True
        assert inp.is_text_ref("Nameplate/text:2") is True

    def test_a_sketch_name_carrying_a_slash_still_reads_as_one(self):
        assert inp.is_text_ref("Plate/Front/text:0") is True

    def test_an_address_with_no_whole_number_index_is_not_one(self):
        # an incomplete address must not route: it carries no text to resolve
        for raw in ("text:", "text:abc", "text:1.5", "Nameplate/text:"):
            assert inp.is_text_ref(raw) is False, raw

    def test_index_selectors_are_not_text_addresses(self):
        for raw in (0, "0", "0,2,3", "all", "*", [0, 1], None, "", "line:0"):
            assert inp.is_text_ref(raw) is False, raw

    def test_a_geometry_handle_is_not_a_text_address(self):
        assert inp.is_text_ref("/v4BAAAARlJLZXkAH4sIAAAA" + "x" * 40) is False
        assert inp.is_text_ref("sometoken|@profile:0.4,0.2,0.0") is False

    def test_the_two_routing_predicates_do_not_overlap(self):
        # the measured defect: is_handle reads 'text:0' as an index selector, so a tool asking only
        # is_handle never reaches ProfileRef with it. The two must classify each address exactly once.
        assert inp.is_handle("text:0") is False
        assert inp.is_text_ref("/v4BAAAARlJLZXkAH4sIAAAA" + "x" * 40) is False


# ── OccurrenceRef: fullPathName-preferring, ambiguity-refusing instance resolution ────────────────

class _FakeOcc:
    """An occurrence as the resolver reads it: both name forms plus the identity facts a collision
    refusal names - its component, whether it is an external reference, and its entityToken."""

    def __init__(self, name, full_path, token="", component="", is_reference=False):
        self.name = name
        self.fullPathName = full_path
        self.entityToken = token
        self.component = types.SimpleNamespace(name=component or name.split(":")[0])
        self.isReferencedComponent = is_reference


def _install_occurrences(*occs, tokens=None):
    """Point _common.design() at a root whose allOccurrences are the given _FakeOcc list, with
    findEntityByToken answering from each occurrence's own entityToken (plus any extra `tokens`
    entries - the non-occurrence entity a handle can point at). adsk.fusion.Occurrence is wired to
    the fake so the resolver's type check is real; the autouse fixture puts it back."""
    import adsk.fusion
    adsk.fusion.Occurrence = _FakeOcc
    by_token = dict(tokens or {})
    for o in occs:
        if getattr(o, "entityToken", ""):
            by_token.setdefault(o.entityToken, o)

    class _Root:
        allOccurrences = list(occs)

    class FakeDesign:
        rootComponent = _Root()

        def findEntityByToken(self, token):
            hit = by_token.get(token)
            return [hit] if hit is not None else []
    inp._common.design = lambda: FakeDesign()
    return list(occs)


class TestOccurrenceRef:
    def test_a_handle_resolves_to_that_exact_instance(self):
        # the token is the identity: it picks one of two instances a shared NAME cannot tell apart.
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1", token="tok-A")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1", token="tok-B")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRef("occ").resolve("tok-B")
        assert err is None and val is b

    def test_a_handle_on_a_non_occurrence_is_refused_naming_the_type(self):
        # silently falling through to the name paths would report "no occurrence matching <token>"
        # and hide what the caller actually passed.
        a = _FakeOcc("Bolt:1", "Bolt:1", token="tok-A")
        _install_occurrences(a, tokens={"tok-body": FakeBRep("Body1")})
        val, err = inp.OccurrenceRef("occ").resolve("tok-body")
        assert val is None
        assert "FakeBRep" in err and "not an occurrence" in err

    def test_an_unknown_handle_is_refused_not_guessed(self):
        # a stale/unknown token resolves to nothing and matches no name either - a refusal, never a
        # fall-back to some instance.
        a = _FakeOcc("Bolt:1", "Bolt:1", token="tok-A")
        _install_occurrences(a)
        val, err = inp.OccurrenceRef("occ").resolve("tok-GONE")
        assert val is None and "no occurrence matching" in err

    def test_a_path_worn_by_two_siblings_is_REFUSED_with_both_handles(self):
        # measured: an xref insert plus an import of the same-named source leave two siblings with
        # ONE fullPathName, one referenced and one not. No name form tells them apart, so the refusal
        # carries what does - the discriminator and the handle that addresses each.
        xref = _FakeOcc("CMG-050:1", "CMG-050:1", token="tok-xref", component="CMG-050",
                        is_reference=True)
        imported = _FakeOcc("CMG-050:1", "CMG-050:1", token="tok-import", component="CMG-050")
        _install_occurrences(xref, imported)
        val, err = inp.OccurrenceRef("occ").resolve("CMG-050:1")
        assert val is None, "a path two occurrences wear must not resolve to one of them"
        assert "tok-xref" in err and "tok-import" in err
        assert "referenced" in err and "local" in err
        assert "CMG-050" in err

    def test_each_collided_sibling_resolves_by_its_own_handle(self):
        # the refusal above must hand back keys that WORK - otherwise the collision is a dead end.
        xref = _FakeOcc("CMG-050:1", "CMG-050:1", token="tok-xref", is_reference=True)
        imported = _FakeOcc("CMG-050:1", "CMG-050:1", token="tok-import")
        _install_occurrences(xref, imported)
        assert inp.OccurrenceRef("occ").resolve("tok-import") == (imported, None)
        assert inp.OccurrenceRef("occ").resolve("tok-xref") == (xref, None)

    def test_exact_fullpathname_wins(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")     # same NAME, different path
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRef("occ").resolve("Sub-B:1+Bolt:1")
        assert err is None and val is b               # picked the RIGHT instance by path

    def test_exact_name_resolves_when_unique(self):
        a = _FakeOcc("Base:1", "Base:1")
        _install_occurrences(a, _FakeOcc("Lid:1", "Lid:1"))
        val, err = inp.OccurrenceRef("occ").resolve("Base:1")
        assert err is None and val is a

    def test_ambiguous_name_is_REFUSED_not_guessed(self):
        # The wrong-instance bug: two instances share the local name. A bare name must ERROR (listing
        # the candidate fullPathNames), NOT silently grab the first.
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRef("occ").resolve("Bolt")   # substring matches both
        assert val is None
        assert "ambiguous" in err.lower()
        assert "Sub-A:1+Bolt:1" in err and "Sub-B:1+Bolt:1" in err

    def test_duplicated_EXACT_name_is_REFUSED_not_guessed(self):
        # A duplicated EXACT name must refuse with the candidate paths, not resolve to one of them -
        # returning a hit here targets an instance the caller did not choose, which for
        # design_delete_occurrence means deleting the wrong one. The substring branch below refuses
        # the looser input already; the precise-looking one needs the same guarantee.
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRef("occ").resolve("Bolt:1")   # EXACT name, matches both
        assert val is None, "a duplicated exact name must not resolve to one of the instances"
        assert "Sub-A:1+Bolt:1" in err and "Sub-B:1+Bolt:1" in err

    def test_duplicated_exact_name_still_resolves_by_its_fullPathName(self):
        # The refusal above must not block the unambiguous key the error tells the caller to use.
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRef("occ").resolve("Sub-A:1+Bolt:1")
        assert err is None and val is a

    def test_unique_substring_resolves(self):
        a = _FakeOcc("LeftBracket:1", "LeftBracket:1")
        _install_occurrences(a, _FakeOcc("Plate:1", "Plate:1"))
        val, err = inp.OccurrenceRef("occ").resolve("bracket")  # case-insensitive, unique
        assert err is None and val is a

    def test_miss_lists_available_paths(self):
        _install_occurrences(_FakeOcc("A:1", "A:1"), _FakeOcc("B:1", "B:1"))
        val, err = inp.OccurrenceRef("occ").resolve("Nope")
        assert val is None and "A:1" in err and "B:1" in err

    def test_required_blank_errors(self):
        val, err = inp.OccurrenceRef("occ", required=True).resolve("")
        assert val is None and "required" in err


class TestOccurrenceRefList:
    def test_a_handle_element_resolves_beside_a_path_element(self):
        a = _FakeOcc("X:1", "A:1+X:1", token="tok-a")
        b = _FakeOcc("X:1", "B:1+X:1", token="tok-b")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRefList("occs").resolve(["tok-b", "A:1+X:1"])
        assert err is None and val == [b, a]

    def test_resolves_each_by_path_in_order(self):
        a = _FakeOcc("X:1", "A:1+X:1")
        b = _FakeOcc("X:1", "B:1+X:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRefList("occs").resolve(["B:1+X:1", "A:1+X:1"])
        assert err is None and val == [b, a]

    def test_comma_string_accepted(self):
        a = _FakeOcc("P:1", "P:1")
        b = _FakeOcc("Q:1", "Q:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRefList("occs").resolve("P:1, Q:1")
        assert err is None and val == [a, b]

    def test_one_ambiguous_element_fails_whole_list(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRefList("occs").resolve(["Sub-A:1+Bolt:1", "Bolt"])
        assert val is None and "ambiguous" in err.lower() and "occs[1]" in err


class TestSharedResolverBehaviour:
    """Behavioural anchors for _resolve_occurrence ITSELF - the canonical resolver
    test_occurrence_ref_lint.py points every routed tool at. OccurrenceRef wraps it, but a tool may
    also call it directly, so the helper's own contract (a handle is the exact identity; fullPathName
    beats a same-named instance; an ambiguous bare name or a collided path errors) is pinned here,
    not only through the kind."""

    def test_a_handle_resolves_directly(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1", token="tok-A")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1", token="tok-B")
        _install_occurrences(a, b)
        occ, err = inp._resolve_occurrence("t", "tok-A")
        assert err is None and occ is a

    def test_a_collided_path_errors_with_the_handles(self):
        one = _FakeOcc("CMG-050:1", "CMG-050:1", token="tok-1", is_reference=True)
        two = _FakeOcc("CMG-050:1", "CMG-050:1", token="tok-2")
        _install_occurrences(one, two)
        occ, err = inp._resolve_occurrence("t", "CMG-050:1")
        assert occ is None and "tok-1" in err and "tok-2" in err

    def test_fullpath_beats_a_same_named_instance(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        occ, err = inp._resolve_occurrence("t", "Sub-B:1+Bolt:1")
        assert err is None and occ is b

    def test_ambiguous_bare_name_errors(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        occ, err = inp._resolve_occurrence("t", "Bolt")
        assert occ is None and "ambiguous" in err.lower()


# ── TargetRef (the polymorphic measure/colour target) ───────────────────────

def _install_target(*, handle_map=None, occurrences=(), components=(), brep_named=None, mesh_named=None):
    """A design wired for every TargetRef path: findEntityByToken (handle), allOccurrences (occurrence),
    allComponents (component), bRepBodies/meshBodies (body-by-name)."""
    import adsk.fusion
    adsk.fusion.BRepBody = FakeBRep
    adsk.fusion.MeshBody = FakeMesh
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace)
    handle_map = handle_map or {}
    brep_named = brep_named or {}
    mesh_named = mesh_named or {}

    class _BColl:
        def __init__(self, m): self._m = m
        def itemByName(self, n): return self._m.get(n)

    class _MColl:
        def __init__(self, m): self._l = list(m.values())
        @property
        def count(self): return len(self._l)
        def item(self, i): return self._l[i] if 0 <= i < len(self._l) else None

    class _Comp:
        def __init__(self, name): self.name = name

    class _CompColl:
        """allComponents is a COUNTED collection (count + item(i)), not a plain list."""
        def __init__(self, items): self._l = list(items)
        @property
        def count(self): return len(self._l)
        def item(self, i): return self._l[i] if 0 <= i < len(self._l) else None
    comp_objs = [_Comp(n) for n in components]
    comp_coll = _CompColl([_Comp("Root")] + comp_objs)
    # component -> its occurrences (for TargetRefList's component->occurrence mapping). A component named "X"
    # maps to the occurrence(s) in `occurrences` whose component.name == "X"; an occurrence carries a
    # .component back-pointer here so allOccurrencesByComponent can match it.
    for occ in occurrences:
        if getattr(occ, "component", None) is None:
            occ.component = _Comp(occ.name.split(":")[0])

    class _Root:
        name = "Root"
        allOccurrences = list(occurrences)
        bRepBodies = _BColl(brep_named)
        meshBodies = _MColl(mesh_named)
        @staticmethod
        def allOccurrencesByComponent(comp):
            return [o for o in occurrences
                    if getattr(getattr(o, "component", None), "name", None) == comp.name]

    class FakeDesign:
        rootComponent = _Root()
        # allComponents lives on the DESIGN in the live API (Component has no such attribute)
        allComponents = comp_coll
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d=None: FakeDesign().rootComponent
    return FakeDesign()


class TestTargetRef:
    def test_empty_is_whole_design(self):
        d = _install_target()
        (ent, kind), err = inp.TargetRef("target").resolve("")
        assert err is None and kind == "design" and ent is d.rootComponent

    def test_handle_to_body(self):
        b = FakeBRep("Body1", is_solid=True)
        _install_target(handle_map={"H": b})
        (ent, kind), err = inp.TargetRef("target").resolve("H")
        assert err is None and kind == "body" and ent is b

    def test_handle_to_face(self):
        f = FakePlanarFace()
        _install_target(handle_map={"H": f})
        (ent, kind), err = inp.TargetRef("target").resolve("H")
        assert err is None and kind == "face"

    def test_handle_to_mesh(self):
        m = FakeMesh("M1")
        _install_target(handle_map={"H": m})
        (ent, kind), err = inp.TargetRef("target").resolve("H")
        assert err is None and kind == "mesh" and ent is m

    def test_occurrence_by_fullpath(self):
        occ = _FakeOcc("Wheel:1", "Chassis:1+Wheel:1")
        _install_target(occurrences=[occ])
        (ent, kind), err = inp.TargetRef("target").resolve("Chassis:1+Wheel:1")
        assert err is None and kind == "occurrence" and ent is occ

    def test_component_by_name(self):
        _install_target(components=["Bracket"])
        (ent, kind), err = inp.TargetRef("target").resolve("Bracket")
        assert err is None and kind == "component" and ent.name == "Bracket"

    def test_body_by_name(self):
        b = FakeBRep("Plate", is_solid=True)
        _install_target(brep_named={"Plate": b})
        (ent, kind), err = inp.TargetRef("target").resolve("Plate")
        assert err is None and kind == "body" and ent is b

    def test_allow_restricts_kind(self):
        # a mesh-only TargetRef refuses a brep-body handle
        b = FakeBRep("Body1", is_solid=True)
        _install_target(handle_map={"H": b})
        res, err = inp.TargetRef("target", allow=("mesh",)).resolve("H")
        assert res is None and "mesh" in err.lower()

    def test_unresolvable_errors(self):
        _install_target()
        res, err = inp.TargetRef("target").resolve("Ghost")
        assert res is None and "Ghost" in err

    def test_ambiguous_occurrence_name_errors_with_candidates(self):
        # An ambiguous occurrence match across DIFFERENT components must propagate
        # _resolve_occurrence's ambiguity error (with the candidate fullPathNames), not fall through
        # to a generic "did not resolve" miss - there is no single answer to give.
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        a.component = type("C", (), {"name": "BoltA", "entityToken": "tA"})()
        b.component = type("C", (), {"name": "BoltB", "entityToken": "tB"})()
        _install_target(occurrences=[a, b])
        res, err = inp.TargetRef("target").resolve("Bolt")
        assert res is None
        assert "ambiguous" in err.lower()
        assert "Sub-A:1+Bolt:1" in err and "Sub-B:1+Bolt:1" in err

    def _instances_of_one_component(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        shared = type("C", (), {"name": "Bolt", "entityToken": "tBolt"})()
        a.component = b.component = shared
        _install_target(occurrences=[a, b])
        return shared

    def test_a_default_target_still_REFUSES_instances_of_one_component(self):
        # The blast radius of a default TargetRef must not widen: for appearance_set / model_inspect /
        # model_set_material, an ambiguous 'Bolt' is still a refusal, not "act on the component"
        # (which would colour or re-material every instance).
        self._instances_of_one_component()
        res, err = inp.TargetRef("target").resolve("Bolt")
        assert res is None and "ambiguous" in err.lower()

    def test_an_OPTED_IN_target_resolves_instances_of_one_component_to_it(self):
        # The instance-only ambiguity, for a caller whose target IS the component: every hit is an
        # instance of the SAME one, so the component is the unambiguous answer. Opt-in only.
        shared = self._instances_of_one_component()
        (ent, kind), err = inp.TargetRef(
            "target", collapse_ambiguous_occurrences=True).resolve("Bolt")
        assert err is None and kind == "component" and ent is shared

    def test_the_opt_in_still_refuses_when_the_caller_takes_no_component(self):
        self._instances_of_one_component()
        res, err = inp.TargetRef("target", allow=("occurrence",),
                                 collapse_ambiguous_occurrences=True).resolve("Bolt")
        assert res is None and "ambiguous" in err.lower()

    def test_the_opt_in_still_refuses_instances_of_DIFFERENT_components(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        a.component = type("C", (), {"name": "BoltA", "entityToken": "tA"})()
        b.component = type("C", (), {"name": "BoltB", "entityToken": "tB"})()
        _install_target(occurrences=[a, b])
        res, err = inp.TargetRef(
            "target", collapse_ambiguous_occurrences=True).resolve("Bolt")
        assert res is None and "ambiguous" in err.lower()

    def test_ambiguous_body_name_errors_with_candidates(self):
        # The same propagation rule for the BODY step: two same-named bodies must surface
        # _resolve_any_body's ambiguity error (with each candidate's context), not fall through
        # to the generic "did not resolve" miss.
        pin_a = FakeBRep("Pin", is_solid=True)
        pin_b = FakeBRep("Pin", is_solid=True)
        pin_a.assemblyContext = type("O", (), {"fullPathName": "Sub-A:1"})()
        pin_b.assemblyContext = type("O", (), {"fullPathName": "Sub-B:1"})()
        _install_ambiguous_bodies(occ_bodies=[{"Pin": pin_a}, {"Pin": pin_b}])
        res, err = inp.TargetRef("target").resolve("Pin")
        assert res is None
        assert "ambiguous" in err.lower()
        assert "Sub-A:1" in err and "Sub-B:1" in err       # both candidate contexts listed
        assert "did not resolve" not in err                # the generic miss must not mask it


class TestTargetRefList:
    """The CAM-setup selector: a list of bodies AND/OR component occurrences/components. A component
    maps to its single occurrence (the CAM API wants the Occurrence, not the Component)."""

    def test_body_handles_pass_through(self):
        b1 = FakeBRep("Body1", is_solid=True)
        b2 = FakeBRep("Body2", is_solid=True)
        _install_target(handle_map={"H1": b1, "H2": b2})
        val, err = inp.TargetRefList("models").resolve(["H1", "H2"])
        assert err is None and val == [b1, b2]

    def test_component_occurrence_selected_as_occurrence(self):
        # the RFA pattern: selecting the COMPONENT occurrence, not a body inside it.
        occ = _FakeOcc("Model Component:1", "Model Component:1")
        _install_target(occurrences=[occ])
        val, err = inp.TargetRefList("models").resolve(["Model Component:1"])
        assert err is None and val == [occ]        # the Occurrence itself, ready for Setup.models

    def test_component_name_maps_to_its_occurrence(self):
        # a bare component name resolves to its single occurrence (CAM wants the Occurrence). The
        # component name deliberately does NOT substring-match the occurrence's own name, so
        # resolution FALLS THROUGH TargetRef's occurrence step to the component step + the mapping.
        occ = _FakeOcc("stockInst:1", "stockInst:1")
        occ.component = type("C", (), {"name": "StockDef"})()
        _install_target(occurrences=[occ], components=["StockDef"])
        val, err = inp.TargetRefList("stock").resolve(["StockDef"])
        assert err is None and val == [occ]        # mapped Component -> its Occurrence

    def test_component_with_no_occurrence_errors(self):
        _install_target(components=["Orphan"])     # a component that is not instanced
        val, err = inp.TargetRefList("models").resolve(["Orphan"])
        assert val is None and "no occurrence" in err.lower()

    def test_component_with_multiple_occurrences_refused(self):
        # two instances of the same component -> ambiguous which to machine; refuse (never guess).
        # The component name ("JawDef") does not substring-match either occurrence name, so
        # resolution reaches the component branch and its multi-occurrence refusal.
        a = _FakeOcc("jawInstA:1", "Vise:1+jawInstA:1")
        b = _FakeOcc("jawInstB:1", "Vise:1+jawInstB:1")
        comp = type("C", (), {"name": "JawDef"})()
        a.component = comp; b.component = comp
        _install_target(occurrences=[a, b], components=["JawDef"])
        val, err = inp.TargetRefList("models").resolve(["JawDef"])
        assert val is None and "ambiguous" in err.lower() and "fullPathName" in err

    def test_mixed_body_and_component_occurrence(self):
        b = FakeBRep("StockBody", is_solid=True)
        occ = _FakeOcc("Model Component:1", "Model Component:1")
        _install_target(handle_map={"H": b}, occurrences=[occ])
        val, err = inp.TargetRefList("models").resolve(["H", "Model Component:1"])
        assert err is None and val == [b, occ]

    def test_empty_optional_is_empty_list(self):
        _install_target()
        val, err = inp.TargetRefList("models", required=False).resolve([])
        assert err is None and val == []

    def test_one_bad_element_fails_whole_list(self):
        occ = _FakeOcc("Good:1", "Good:1")
        _install_target(occurrences=[occ])
        val, err = inp.TargetRefList("models").resolve(["Good:1", "Ghost"])
        assert val is None and "[1]" in err and "Ghost" in err


# ── TargetRef edge + construction geometry (gated by allow=) ─────────────────────────────────────
# TargetRef ALSO resolves a BRepEdge and a ConstructionAxis/ConstructionPlane handle - but ONLY when
# the caller opts in via allow=. A default-allow caller (its allow is the six original kinds) refuses
# them exactly as it refuses any out-of-allow kind, so model_inspect / appearance_set / model_set_material
# are UNAFFECTED. model_measure_relation's concentric relation opts in with allow=(...,'edge').

class _FakeConsPlane:
    pass


def _install_target_ext(handle_map):
    """A design wired for the EXTENDED TargetRef paths: findEntityByToken plus the BRepEdge /
    ConstructionAxis / ConstructionPlane types the new isinstance branches check."""
    import adsk.fusion
    adsk.fusion.BRepBody = FakeBRep
    adsk.fusion.MeshBody = FakeMesh
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace)
    adsk.fusion.BRepEdge = FakeEdge
    adsk.fusion.ConstructionAxis = _FakeConstructionAxis
    adsk.fusion.ConstructionPlane = _FakeConsPlane

    class _Root:
        name = "Root"
        allOccurrences = []

        @property
        def allComponents(self):
            return []

    class FakeDesign:
        rootComponent = _Root()
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d=None: FakeDesign().rootComponent


class TestTargetRefEdgeAndConstruction:
    def test_edge_handle_resolves_when_allowed(self):
        e = FakeEdge()
        _install_target_ext({"H": e})
        (ent, kind), err = inp.TargetRef("t", allow=("edge",)).resolve("H")
        assert err is None and kind == "edge" and ent is e

    def test_edge_handle_refused_under_default_allow(self):
        # the backward-compat guarantee: a default-allow TargetRef does NOT accept an edge
        e = FakeEdge()
        _install_target_ext({"H": e})
        res, err = inp.TargetRef("t").resolve("H")
        assert res is None and "edge" in err.lower()

    def test_construction_axis_resolves_when_allowed(self):
        ax = _FakeConstructionAxis()
        _install_target_ext({"H": ax})
        (ent, kind), err = inp.TargetRef("t", allow=("construction_axis",)).resolve("H")
        assert err is None and kind == "construction_axis" and ent is ax

    def test_construction_plane_resolves_when_allowed(self):
        pl = _FakeConsPlane()
        _install_target_ext({"H": pl})
        (ent, kind), err = inp.TargetRef("t", allow=("construction_plane",)).resolve("H")
        assert err is None and kind == "construction_plane" and ent is pl

    def test_original_body_kind_unaffected_by_the_extension(self):
        # body/face/mesh/occurrence/component/design must resolve EXACTLY as before
        b = FakeBRep("B", is_solid=True)
        _install_target_ext({"H": b})
        (ent, kind), err = inp.TargetRef("t").resolve("H")
        assert err is None and kind == "body" and ent is b


# ── JointOriginRef: a Joint Origin by handle OR name (bare/qualified), ambiguity refused ─────────────
#
# The resolve-one leaf of the JOINT-ORIGIN SEAM. It composes the shared _joints JO walk: a bare name is
# unique-or-refused, a qualified '<occ>:<JO name>' proxies into that occurrence, and a handle round-trips
# a JointOrigin entityToken. Non-unique JO names (two components sharing one) MUST refuse with the
# qualified candidates - the house rule OccurrenceRef enforces for the occurrence name space.

class _JOColl:
    """A JointOrigins collection: count/item (the shared walk) + itemByName (scoped lookups)."""
    def __init__(self, jos):
        self._jos = list(jos)

    @property
    def count(self):
        return len(self._jos)

    def item(self, i):
        return self._jos[i]

    def itemByName(self, name):
        return next((j for j in self._jos if j.name == name), None)


class _JO:
    """A JointOrigin fake: name + a createForAssemblyContext that returns a DISTINCT proxy tagged with
    its occurrence, so a test can tell a proxy apart from the native and confirm the right occurrence."""
    def __init__(self, name, token=None):
        self.name = name
        self.entityToken = token

    def createForAssemblyContext(self, occ):
        p = _JO(self.name)
        p.native = self
        p.context = occ
        return p


class _Comp:
    def __init__(self, name, jos=()):
        self.name = name
        self.jointOrigins = _JOColl(jos)


class _OccJO:
    def __init__(self, full, comp):
        self.fullPathName = full
        self.name = full
        self.component = comp


class _RootJO(_Comp):
    def __init__(self, name="Root", jos=(), occ_by_comp=None):
        super().__init__(name, jos)
        self._occ_by_comp = occ_by_comp or {}

    def allOccurrencesByComponent(self, comp):
        return self._occ_by_comp.get(getattr(comp, "name", None), [])

    @property
    def allOccurrences(self):
        return [o for lst in self._occ_by_comp.values() for o in lst]


class _DesignJO:
    def __init__(self, root, subs=(), token_map=None):
        self.rootComponent = root
        self._subs = list(subs)
        self._tokens = token_map or {}

    @property
    def allComponents(self):
        # like the live API: the root appears here too (as a proxy), so the walk must de-dup it.
        return [self.rootComponent] + self._subs

    def findEntityByToken(self, token):
        e = self._tokens.get(token)
        return [e] if e is not None else []


def _install_jo(design):
    """Point the _inputs design seam at `design` and make adsk.fusion.JointOrigin a real type so
    is_joint_origin() works. JointOriginRef fetches design via _inputs._common.design() then passes it
    explicitly to the _joints walk / _resolve_occurrence, so ONE seam covers it. Returns the ref."""
    import adsk.fusion
    adsk.fusion.JointOrigin = _JO
    inp._common.design = lambda: design
    return inp.JointOriginRef("jo")


class TestJointOriginRef:
    def test_bare_unique_root_name_resolves(self):
        target = _JO("Stock_Center")
        design = _DesignJO(_RootJO(jos=[target]))
        ref = _install_jo(design)
        jo, err = ref.resolve("Stock_Center")
        assert err is None and jo is target

    def test_miss_lists_available_names(self):
        design = _DesignJO(_RootJO(jos=[_JO("Stock_Center"), _JO("Vise_Center")]))
        ref = _install_jo(design)
        jo, err = ref.resolve("Nope")
        assert jo is None
        assert "no Joint Origin named 'Nope'" in err
        assert "Stock_Center" in err and "Vise_Center" in err        # self-correction data

    def test_ambiguous_bare_name_is_refused_with_qualified_candidates(self):
        # two sub-components each carry 'Center' -> a bare 'Center' must REFUSE, listing the qualified
        # '<occ>:Center' forms - never silently grab the first (the non-unique-name-space house rule).
        a, b = _Comp("A", [_JO("Center")]), _Comp("B", [_JO("Center")])
        occ_a, occ_b = _OccJO("A:1", a), _OccJO("B:1", b)
        root = _RootJO(jos=[], occ_by_comp={"A": [occ_a], "B": [occ_b]})
        design = _DesignJO(root, subs=[a, b])
        ref = _install_jo(design)
        jo, err = ref.resolve("Center")
        assert jo is None
        assert "ambiguous" in err.lower()
        assert "A:1:Center" in err and "B:1:Center" in err

    def test_qualified_name_proxies_into_the_named_occurrence(self):
        native = _JO("Center")
        sub = _Comp("Tower", [native])
        occ = _OccJO("Tower:1", sub)
        root = _RootJO(jos=[], occ_by_comp={"Tower": [occ]})
        design = _DesignJO(root, subs=[sub])
        ref = _install_jo(design)
        jo, err = ref.resolve("Tower:1:Center")
        assert err is None
        assert getattr(jo, "native", None) is native      # a PROXY, not the native
        assert jo.context is occ                            # proxied into the RIGHT occurrence

    def test_bare_name_on_single_instance_subcomponent_is_proxied(self):
        # a UNIQUE bare name whose owning component has ONE occurrence resolves - proxied into it.
        native = _JO("Stock_Center")
        sub = _Comp("Stock", [native])
        occ = _OccJO("Stock:1", sub)
        root = _RootJO(jos=[], occ_by_comp={"Stock": [occ]})
        design = _DesignJO(root, subs=[sub])
        ref = _install_jo(design)
        jo, err = ref.resolve("Stock_Center")
        assert err is None and getattr(jo, "native", None) is native and jo.context is occ

    def test_multi_instance_subcomponent_bare_name_is_refused(self):
        # a unique NAME but the owning component is instanced twice -> which instance's frame? Refuse.
        native = _JO("Grip")
        sub = _Comp("Jaw", [native])
        occ1, occ2 = _OccJO("Jaw:1", sub), _OccJO("Jaw:2", sub)
        root = _RootJO(jos=[], occ_by_comp={"Jaw": [occ1, occ2]})
        design = _DesignJO(root, subs=[sub])
        ref = _install_jo(design)
        jo, err = ref.resolve("Grip")
        assert jo is None and "instanced 2 times" in err

    def test_handle_resolves_to_the_joint_origin(self):
        target = _JO("Stock_Center", token="JO_TOKEN")
        design = _DesignJO(_RootJO(jos=[target]), token_map={"JO_TOKEN": target})
        ref = _install_jo(design)
        jo, err = ref.resolve("JO_TOKEN")
        assert err is None and jo is target

    def test_handle_pointing_at_non_jo_is_rejected(self):
        face = FakePlanarFace()
        design = _DesignJO(_RootJO(jos=[]), token_map={"FACE_TOKEN": face})
        ref = _install_jo(design)
        jo, err = ref.resolve("FACE_TOKEN")
        assert jo is None and "not a Joint Origin" in err

    def test_walk_finds_a_subcomponent_jo_the_root_walk_would_miss(self):
        # a JO living ONLY in a sub-component must be found (a root-only walk under-reports).
        # find_joint_origins_by_name is the resolve-one leaf over all_joint_origins.
        native = _JO("Deep_Frame")
        sub = _Comp("Inner", [native])
        root = _RootJO(jos=[], occ_by_comp={"Inner": [_OccJO("Inner:1", sub)]})
        design = _DesignJO(root, subs=[sub])
        _install_jo(design)
        matches = inp._joints.find_joint_origins_by_name(design, "Deep_Frame")
        assert len(matches) == 1 and matches[0][0] is native and matches[0][1] is sub


# ── the shared collection walks: an unreadable member costs its own row, not the census ──

class _WalkColl:
    """A count/item(i) collection. `broken` indices raise on item(i); a None member is a hole."""

    def __init__(self, items, broken=(), count_raises=False):
        self._items = list(items)
        self._broken = set(broken)
        self._count_raises = count_raises

    @property
    def count(self):
        if self._count_raises:
            raise RuntimeError("count unreadable")
        return len(self._items)

    def item(self, i):
        if i in self._broken:
            raise RuntimeError("item unreadable")
        return self._items[i]


def _tl_obj(name, index):
    return types.SimpleNamespace(name=name, index=index, isGroup=False, entity=object())


class TestTimelineObjectsWalk:
    """_timeline_objects is what every FeatureRef resolution reads the timeline through."""

    def test_returns_every_object_in_index_order(self):
        objs = [_tl_obj("Extrude1", 0), _tl_obj("Fillet1", 1)]
        assert inp._timeline_objects(_WalkColl(objs)) == objs

    def test_an_unreadable_object_is_skipped_not_raised(self):
        # Without the safe() guard this raises straight out of resolve(), so ONE bad timeline row
        # makes every feature name in the document unresolvable instead of just its own.
        objs = [_tl_obj("Extrude1", 0), _tl_obj("Broken", 1), _tl_obj("Fillet1", 2)]
        names = [o.name for o in inp._timeline_objects(_WalkColl(objs, broken=(1,)))]
        assert names == ["Extrude1", "Fillet1"]

    def test_an_unreadable_count_is_an_empty_timeline_not_a_raise(self):
        assert inp._timeline_objects(_WalkColl([_tl_obj("X", 0)], count_raises=True)) == []

    def test_the_index_form_reads_the_objects_own_index_not_its_position(self):
        # A skipped row leaves a HOLE, so position 1 holds the object whose own .index is 2. The
        # '@index' number an agent passes came from a candidate list / design_get, which publish
        # o.index - addressing by position would resolve a different object than was named.
        objs = [_tl_obj("Extrude1", 0), _tl_obj("Broken", 1), _tl_obj("Fillet1", 2)]
        walked = inp._timeline_objects(_WalkColl(objs, broken=(1,)))
        assert [o.name for o in inp._match_timeline_objects(walked, "Fillet1@2")] == ["Fillet1"]
        assert inp._match_timeline_objects(walked, "Fillet1@1") == []

    def test_two_same_named_features_across_a_hole_resolve_by_their_own_index(self):
        # THE silent-wrong-target case: two features share the name 'Fillet1' (indices 3 and 4) and
        # an unreadable row sits before them. The ambiguity refusal offers 'Fillet1@3'/'Fillet1@4';
        # under a position-based pick '@4' missed and '@3' quietly resolved the OTHER Fillet1.
        first, second = _tl_obj("Fillet1", 3), _tl_obj("Fillet1", 4)
        objs = [_tl_obj("Extrude1", 0), _tl_obj("Broken", 1), _tl_obj("Chamfer1", 2), first, second]
        walked = inp._timeline_objects(_WalkColl(objs, broken=(1,)))
        assert inp._match_timeline_objects(walked, "Fillet1@4") == [second]
        assert inp._match_timeline_objects(walked, "Fillet1@3") == [first]

    def test_an_index_no_object_carries_is_a_miss(self):
        objs = [_tl_obj("Extrude1", 0), _tl_obj("Fillet1", 1)]
        assert inp._match_timeline_objects(objs, "Fillet1@7") == []

    def test_the_index_form_still_confirms_the_name(self):
        # '@index' is a disambiguator, not an override: naming the wrong feature at a real index
        # must miss rather than resolve whatever sits there.
        objs = [_tl_obj("Extrude1", 0), _tl_obj("Fillet1", 1)]
        assert inp._match_timeline_objects(objs, "Extrude1@1") == []


class TestTimelineNameWhitespace:
    """Surrounding whitespace is not a distinguishing feature on EITHER side of the comparison.

    Fusion names an occurrence-create timeline object with a leading space (probe_fix_campaign.log
    [F74]), which no listing shows and no caller retypes. Both sides are stripped here, at the
    matcher - every tool's own input handling sits above it, so a caller that does not strip its
    own input still resolves the same object as one that does.
    """

    def test_the_OBJECT_side_is_stripped(self):
        objs = [_tl_obj(" InsProbe:1", 0)]
        assert inp._match_timeline_objects(objs, "InsProbe:1") == objs

    def test_the_WANT_side_is_stripped(self):
        # the half no tool exercises today: every caller strips its own input first, so only a
        # direct call reaches this - and the docstring's contract is what a NEW caller relies on.
        objs = [_tl_obj("Extrude1", 0)]
        assert inp._match_timeline_objects(objs, "  Extrude1  ") == objs
        obj, err = inp.resolve_timeline_object(objs, "  Extrude1  ", "'feature'")
        assert err is None and obj is objs[0]

    def test_both_sides_at_once(self):
        objs = [_tl_obj(" InsProbe:1 ", 0)]
        assert inp._match_timeline_objects(objs, "  InsProbe:1  ") == objs

    def test_the_index_form_strips_the_name_half_too(self):
        objs = [_tl_obj(" Extrude1", 4)]
        assert inp._match_timeline_objects(objs, " Extrude1 @4") == objs

    def test_whitespace_is_never_a_disambiguator(self):
        # two objects differing ONLY by padding are one ambiguity, refused - never silently split
        # into two addressable names an agent cannot tell apart in any listing.
        objs = [_tl_obj("Extrude1", 0), _tl_obj(" Extrude1", 1)]
        assert inp._match_timeline_objects(objs, "Extrude1") == objs
        obj, err = inp.resolve_timeline_object(objs, "Extrude1", "'feature'")
        assert obj is None and "matches 2 timeline objects" in err


class TestSketchOwnersWalk:
    """_sketch_owners is the census SketchRef/SketchRefList refuse an ambiguous name from."""

    def _design(self, comps):
        return types.SimpleNamespace(rootComponent=comps[0], activeComponent=comps[0],
                                     allComponents=_WalkColl(comps))

    def _comp(self, name, sketch_names, **kw):
        sketches = [types.SimpleNamespace(name=n) for n in sketch_names]
        return types.SimpleNamespace(name=name, sketches=_WalkColl(sketches, **kw))

    def test_finds_the_name_in_every_component(self):
        d = self._design([self._comp("Root", ["Profile"]), self._comp("Frame", ["Profile"])])
        assert [c for c, _sk in inp._sketch_owners(d, "Profile")] == ["Root", "Frame"]

    def test_match_is_exact_and_case_insensitive(self):
        d = self._design([self._comp("Root", ["Profile", "ProfileOuter"])])
        assert [sk.name for _c, sk in inp._sketch_owners(d, "profile")] == ["Profile"]

    def test_an_unreadable_sketch_does_not_hide_the_rest_of_the_census(self):
        # Losing a component's whole sketch list to one bad row would turn a genuine AMBIGUITY into
        # a confident single hit - the resolver would then act on the wrong sketch.
        bad = self._comp("Frame", [types.SimpleNamespace(name="Profile")], broken=(0,))
        d = self._design([self._comp("Root", ["Profile"]), bad])
        assert [c for c, _sk in inp._sketch_owners(d, "Profile")] == ["Root"]

    def test_a_component_with_no_sketch_collection_is_skipped(self):
        d = self._design([self._comp("Root", ["Profile"]),
                          types.SimpleNamespace(name="Empty", sketches=None)])
        assert [c for c, _sk in inp._sketch_owners(d, "Profile")] == ["Root"]


class TestTargetRefMissHint:
    def test_miss_error_advertises_empty_form_only_when_design_is_allowed(self):
        # design_set_name excludes 'design' from allow= yet the miss error advertised the '' form
        # (measured) - an unreachable suggestion. The hint follows the kind's own allow set.
        _install_target()
        _, err_with = inp.TargetRef("target").resolve("Nope")
        assert "'' (whole design)" in err_with
        _, err_without = inp.TargetRef(
            "target", allow=("body", "mesh", "occurrence", "component")).resolve("Nope")
        assert "whole design" not in err_without
