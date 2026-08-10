"""Unit tests for ``find_geometry.py`` — query geometry, return stable handles.

This is the QUERY half of geometry-as-values: it must return each match's handle (entityToken),
kind, position, and shape data, and filter by kind / radius / nearest_to. Pinned here (no live
Fusion): the units scaling on positions/radii, the kind filter, the radius filter (5% tol), the
nearest_to sort, that every match carries a handle, and the omit-when-default visibility signal
(a hidden body's matches carry hidden:true; visible bodies' records omit the key).
"""

import json

from conftest import MeshBody, load_tool, _NamedCollection

fg = load_tool("find_geometry")


# ── fakes mimicking adsk BRep faces/edges ───────────────────────────────────

class _Pt:
    def __init__(self, x, y, z):
        self.x = x; self.y = y; self.z = z


class _CylGeo:
    def __init__(self, r, axis=(1, 0, 0)):
        self.surfaceType = "CYL"
        self.radius = r
        self.axis = _Pt(*axis)


class _PlaneGeo:
    surfaceType = "PLANE"


class _Eval:
    """Stands in for a BRepFace SurfaceEvaluator. getNormalAtPoint returns (success, normal) - the
    Python shape of a bool-return + output-normal API. raises=True mimics an off-surface sample point."""
    def __init__(self, normal=None, raises=False):
        self._normal = normal
        self._raises = raises

    def getNormalAtPoint(self, point):
        if self._raises:
            raise RuntimeError("point is off the face surface")
        return (True, _Pt(*self._normal))


class _LineGeo:
    def __init__(self, start, end):
        self.curveType = "LINE"
        self.startPoint = _Pt(*start)
        self.endPoint = _Pt(*end)


class FakeEdge:
    def __init__(self, token, geo, point_on_edge, length=5.0):
        self.entityToken = token
        self.geometry = geo
        self.pointOnEdge = _Pt(*point_on_edge)
        self.length = length


class FakeFace:
    def __init__(self, token, geo, centroid, area=10.0, evaluator=None):
        self.entityToken = token
        self.geometry = geo
        self.centroid = _Pt(*centroid)
        self.area = area
        # None => _face_normal degrades to no 'normal' field (same as a face lacking an evaluator).
        self.evaluator = evaluator


class FakeBody:
    """A BRep body. `name`/`token` matter to the by-name target path (the shared body resolver walks
    every occurrence's bRepBodies by name and de-duplicates on entityToken); `parentComponent` and
    `assemblyContext` are what a candidate's '<occurrence-or-component>:<body>' label is built from."""
    def __init__(self, faces=(), edges=(), vertices=(), visible=True, name="Body1", token=None):
        self.faces = list(faces)
        self.edges = list(edges)
        self.vertices = list(vertices)
        # BRepBody.isVisible is the EFFECTIVE state (own bulb AND ancestor occurrence bulbs rolled up).
        self.isVisible = visible
        self.name = name
        self.entityToken = token or f"BTOK::{name}::{id(self)}"
        self.parentComponent = None
        self.assemblyContext = None


class FakeOcc:
    def __init__(self, name, comp, bodies, full_path=None):
        self.name = name
        # fullPathName is the unambiguous key; defaults to name for flat (single-level) assemblies.
        self.fullPathName = full_path or name
        self.component = type("C", (), {"name": comp})()
        # Counted+named collection (the live protocol): find_geometry iterates it, the by-name body
        # resolver reads count/item/itemByName off the same object.
        self.bRepBodies = _NamedCollection(bodies)
        for b in bodies:
            b.parentComponent = self.component
            b.assemblyContext = self


class FakeRoot:
    def __init__(self, occs, root_bodies=(), all_occs=None, meshes=()):
        # `occurrences` is the TOP-LEVEL collection; `allOccurrences` is the FLATTENED, recursive list.
        # For a flat assembly they're equal; nested tests pass all_occs ⊋ occs so a top-level-only scan
        # genuinely can't reach the nested occurrence (that's what makes the recursion test bite).
        self.name = "Root"
        self.occurrences = _NamedCollection(occs)
        self.allOccurrences = list(all_occs) if all_occs is not None else list(occs)
        self.bRepBodies = _NamedCollection(root_bodies)
        # Meshes are reached through the COMPONENTS (reading meshBodies off an occurrence raises).
        self.meshBodies = _NamedCollection(meshes)
        for b in root_bodies:
            b.parentComponent = self


class FakeDesign:
    def __init__(self, occs, root_bodies=(), all_occs=None, meshes=()):
        self.rootComponent = FakeRoot(occs, root_bodies, all_occs, meshes)


import pytest


@pytest.fixture(autouse=True)
def _enum_sentinels(monkeypatch):
    # SurfaceTypes/Curve3DTypes live on the SHARED adsk mock that other test modules' tools also
    # read - monkeypatch scopes the string sentinels to this file so they restore after each test.
    import adsk.core
    st = adsk.core.SurfaceTypes
    monkeypatch.setattr(st, "CylinderSurfaceType", "CYL", raising=False)
    monkeypatch.setattr(st, "PlaneSurfaceType", "PLANE", raising=False)
    monkeypatch.setattr(st, "ConeSurfaceType", "CONE", raising=False)
    monkeypatch.setattr(st, "SphereSurfaceType", "SPHERE", raising=False)
    monkeypatch.setattr(st, "TorusSurfaceType", "TORUS", raising=False)
    ct = adsk.core.Curve3DTypes
    monkeypatch.setattr(ct, "Circle3DCurveType", "CIRCLE", raising=False)
    monkeypatch.setattr(ct, "Line3DCurveType", "LINE", raising=False)
    monkeypatch.setattr(ct, "Arc3DCurveType", "ARC", raising=False)


def _install(occs, root_bodies=(), all_occs=None, meshes=()):
    design = FakeDesign(occs, root_bodies, all_occs, meshes)
    fg.app = type("A", (), {"activeProduct": design})()
    fg._common.app = fg.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # The by-name body path resolves through _inputs, which reads the design off the SAME _common
    # module object - and discriminates mesh from BRep by isinstance, so both types are modelled.
    adsk.fusion.BRepBody = FakeBody
    adsk.fusion.MeshBody = MeshBody
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _cyl(token, r, centroid, axis=(1, 0, 0)):
    return FakeFace(token, _CylGeo(r, axis), centroid)


def _plane(token, centroid):
    return FakeFace(token, _PlaneGeo(), centroid)


class TestGuards:
    def test_unknown_units(self):
        _install([FakeOcc("P:1", "P", [FakeBody()])])
        res = fg.handler(units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_unresolved_target(self):
        _install([FakeOcc("P:1", "P", [FakeBody()])])
        res = fg.handler(target="Nope")
        assert res["isError"] is True and "Could not resolve target" in res["message"]


class TestFind:
    def test_returns_handles_and_scaled_positions(self):
        # cylinder at world (2.45,2,0)cm -> reported in mm
        f = _cyl("TOK_PIN", 0.8, (2.45, 2.0, 0.0))
        _install([FakeOcc("Crank:1", "Crank", [FakeBody(faces=[f])])])
        out = _payload(fg.handler(target="Crank:1", units="mm"))
        m = out["matches"][0]
        # The handle is the SELF-HEALING composite '<token>|@<kind>:<x>,<y>,<z>' (locator in cm, the
        # API unit) — the token is the fast path, the '@' locator the stale-token fallback.
        assert m["handle"].startswith("TOK_PIN|@")
        assert m["handle"] == "TOK_PIN|@cylinder_face:2.450000,2.000000,0.000000"
        assert m["kind"] == "cylinder_face"
        assert m["position"] == [24.5, 20.0, 0.0]      # cm -> mm
        assert m["radius"] == 8.0                        # 0.8cm -> 8mm
        # The declared output contract holds: the handler actually mints the 'handle' RETURNS declares.
        assert fg.RETURNS[0].assert_present(out) == ""

    def test_kind_filter_cylinder_only(self):
        body = FakeBody(faces=[_cyl("C", 0.8, (0, 0, 0)), _plane("P", (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        out = _payload(fg.handler(target="X:1", kind="cylinder_face"))
        kinds = {m["kind"] for m in out["matches"]}
        assert kinds == {"cylinder_face"}

    def test_radius_filter(self):
        body = FakeBody(faces=[_cyl("PIN", 0.8, (0, 0, 0)), _cyl("JRN", 1.0, (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        out = _payload(fg.handler(target="X:1", kind="cylinder_face", radius=8, units="mm"))
        # only the r8mm pin; handle is the composite '<token>|@...'
        assert out["returned"] == 1 and out["matches"][0]["handle"].startswith("PIN|@")

    def test_nearest_to_sorts(self):
        # faces at world 10cm (FAR) and 1cm (NEAR); nearest_to is in mm.
        body = FakeBody(faces=[_cyl("FAR", 0.8, (10, 0, 0)), _cyl("NEAR", 0.8, (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        # nearest_to=[10,0,0]mm = 1cm -> NEAR (at 1cm) is closest
        out = _payload(fg.handler(target="X:1", nearest_to=[10, 0, 0], units="mm"))
        assert out["matches"][0]["handle"].startswith("NEAR|@")
        # nearest_to=[100,0,0]mm = 10cm -> FAR (at 10cm) is closest
        out2 = _payload(fg.handler(target="X:1", nearest_to=[100, 0, 0], units="mm"))
        assert out2["matches"][0]["handle"].startswith("FAR|@")

    def test_every_match_has_a_handle(self):
        body = FakeBody(faces=[_cyl("A", 0.8, (0, 0, 0)), _plane("B", (1, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        out = _payload(fg.handler(target="X:1"))
        assert all(m.get("handle") for m in out["matches"])

    def test_hidden_body_matches_carry_hidden_true(self):
        # Omit-when-default visibility signal: every match from a body whose isVisible is False (own
        # bulb OR a hidden ancestor - isVisible rolls both up) carries hidden:true, so an agent that
        # hid a body before a trim/cut can CONFIRM it.
        hidden_body = FakeBody(faces=[_cyl("HID", 0.8, (0, 0, 0))],
                               edges=[FakeEdge("HE", _LineGeo((0, 0, 0), (1, 0, 0)), (0.5, 0, 0))],
                               visible=False)
        _install([FakeOcc("X:1", "X", [hidden_body])])
        out = _payload(fg.handler(target="X:1"))
        assert out["matches"], "expected matches from the hidden body"
        assert all(m["hidden"] is True for m in out["matches"])

    def test_visible_body_matches_omit_hidden_field(self):
        # The default (visible) case stays byte-identical: no 'hidden' key at all.
        body = FakeBody(faces=[_cyl("VIS", 0.8, (0, 0, 0))])
        _install([FakeOcc("X:1", "X", [body])])
        out = _payload(fg.handler(target="X:1"))
        assert all("hidden" not in m for m in out["matches"])

    def test_mixed_visibility_flags_only_the_hidden_bodys_matches(self):
        shown = FakeBody(faces=[_cyl("SHOWN", 0.8, (0, 0, 0))])
        hidden = FakeBody(faces=[_cyl("HIDDEN", 0.8, (1, 0, 0))], visible=False)
        _install([FakeOcc("X:1", "X", [shown, hidden])])
        out = _payload(fg.handler(target="X:1"))
        flags = {m["handle"].split("|@")[0]: m.get("hidden") for m in out["matches"]}
        assert flags == {"SHOWN": None, "HIDDEN": True}


# ── NESTED sub-assembly reach (scan allOccurrences, target by fullPathName) ─────────────────────
# find_geometry must scan allOccurrences (not just root.occurrences), so a nested occurrence that
# design_get(tree)/assembly_get report by fullPathName resolves here too, agreeing with the
# self-heal path (_refind_by_locator also scans allOccurrences).

class TestNestedAssembly:
    def test_nested_occurrence_resolved_by_full_path(self):
        # A bracket nested under a sub-assembly: it is NOT in the TOP-LEVEL occurrences, only in the
        # flattened allOccurrences. Targetable by fullPathName. (A top-level-only scan can't see it —
        # that's what this pins.)
        f = _cyl("NESTED_PIN", 0.8, (1.0, 0.0, 0.0))
        parent = FakeOcc("Frame:1", "Frame", [], full_path="Frame:1")
        nested = FakeOcc("Bracket:1", "Bracket", [FakeBody(faces=[f])],
                         full_path="Frame:1/Bracket:1")
        # top-level = [parent] only; allOccurrences = [parent, nested]
        _install([parent], all_occs=[parent, nested])
        out = _payload(fg.handler(target="Frame:1/Bracket:1", units="mm"))
        assert out["returned"] == 1
        assert out["matches"][0]["handle"].startswith("NESTED_PIN|@")

    def test_nested_also_reachable_by_local_name(self):
        f = _cyl("PIN", 0.8, (0, 0, 0))
        parent = FakeOcc("Frame:1", "Frame", [], full_path="Frame:1")
        nested = FakeOcc("Bracket:1", "Bracket", [FakeBody(faces=[f])],
                         full_path="Frame:1/Bracket:1")
        _install([parent], all_occs=[parent, nested])
        out = _payload(fg.handler(target="Bracket:1"))
        assert out["returned"] == 1

    def test_targeting_a_wrapper_includes_its_nested_subtree(self):
        # An inserted xref/derive wraps its geometry one level down: the wrapper occurrence has
        # NO direct bodies; its nested child holds them (real fullPathNames separate with '+').
        # Targeting the wrapper must scan the whole subtree - a direct-bodies-only scan reads 0
        # faces on a wrapper whose child holds them all (live-verified).
        f = _cyl("DEEP", 0.8, (0, 0, 0))
        top = FakeOcc("Model:1", "Model", [], full_path="Model:1")
        wrapper = FakeOcc("P5:1", "P5", [], full_path="Model:1+P5:1")
        child = FakeOcc("Ring:1", "Ring", [FakeBody(faces=[f])],
                        full_path="Model:1+P5:1+Ring:1")
        _install([top], all_occs=[top, wrapper, child])
        out = _payload(fg.handler(target="Model:1+P5:1", units="mm"))
        assert out["returned"] == 1
        assert out["matches"][0]["handle"].startswith("DEEP|@")

    def test_whole_design_includes_nested_and_root_bodies(self):
        # whole-design scan reaches BOTH a root-level body (occurrence None) AND a NESTED occurrence's
        # body — the self-heal path scans both, so the initial query must too.
        root_face = _plane("ROOT_FACE", (0, 0, 0))
        nested_face = _cyl("NESTED", 0.8, (5, 0, 0))
        parent = FakeOcc("Frame:1", "Frame", [], full_path="Frame:1")
        nested = FakeOcc("Bracket:1", "Bracket", [FakeBody(faces=[nested_face])],
                         full_path="Frame:1/Bracket:1")
        _install([parent], all_occs=[parent, nested],
                 root_bodies=[FakeBody(faces=[root_face])])
        out = _payload(fg.handler())
        handles = {m["handle"].split("|@")[0] for m in out["matches"]}
        assert "ROOT_FACE" in handles and "NESTED" in handles


# ── BODY-NAME targets: a body inside ANY component, ambiguity refused, qualified form accepted ──
# 'target' takes a BODY name, and a body almost never lives at the root: the by-name path walks every
# occurrence's bodies, so 'Pin' inside Frame:1 resolves. A body name is only LOCALLY unique, so a name
# two components answer to is REFUSED with each candidate written as '<occurrence-or-component>:<body>'
# - the form that then picks exactly one.

def _named_body(name, token, face_token):
    return FakeBody(faces=[_cyl(face_token, 0.8, (0, 0, 0))], name=name, token=token)


class TestBodyNameTargets:
    def test_body_inside_a_component_resolves_by_name(self):
        pin = _named_body("Pin", "TOK_A", "PIN_FACE")
        _install([FakeOcc("Frame:1", "Frame", [pin])])
        out = _payload(fg.handler(target="Pin"))
        assert out["returned"] == 1
        assert out["matches"][0]["handle"].startswith("PIN_FACE|@")
        # the label names the body that RESOLVED, in the form that resolves back - not the raw input
        assert out["target"] == "body 'Frame:1:Pin'"

    def test_the_label_echoes_the_resolved_body_not_the_raw_input(self):
        # a mis-cased bare name and the qualified form both report the ONE body they reached
        pin = _named_body("Pin", "TOK_A", "PIN_FACE")
        _install([FakeOcc("Frame:1", "Frame", [pin])])
        assert _payload(fg.handler(target="pin"))["target"] == "body 'Frame:1:Pin'"
        assert _payload(fg.handler(target="Frame:Pin"))["target"] == "body 'Frame:1:Pin'"

    def test_root_level_body_still_resolves_by_name(self):
        base = _named_body("Base", "TOK_ROOT", "BASE_FACE")
        _install([], root_bodies=[base])
        out = _payload(fg.handler(target="Base"))
        assert out["returned"] == 1 and out["matches"][0]["handle"].startswith("BASE_FACE|@")

    def test_body_name_is_matched_case_insensitively(self):
        pin = _named_body("Pin", "TOK_A", "PIN_FACE")
        _install([FakeOcc("Frame:1", "Frame", [pin])])
        out = _payload(fg.handler(target="pin"))
        assert out["returned"] == 1 and out["matches"][0]["handle"].startswith("PIN_FACE|@")

    def test_name_in_several_components_is_refused_naming_each(self):
        a = _named_body("Pin", "TOK_A", "A_FACE")
        b = _named_body("Pin", "TOK_B", "B_FACE")
        _install([FakeOcc("A:1", "A", [a]), FakeOcc("B:1", "B", [b])])
        res = fg.handler(target="Pin")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "A:1:Pin" in res["message"] and "B:1:Pin" in res["message"]

    def test_qualified_occurrence_body_form_picks_one(self):
        a = _named_body("Pin", "TOK_A", "A_FACE")
        b = _named_body("Pin", "TOK_B", "B_FACE")
        _install([FakeOcc("A:1", "A", [a]), FakeOcc("B:1", "B", [b])])
        out = _payload(fg.handler(target="B:1:Pin"))
        assert out["returned"] == 1 and out["matches"][0]["handle"].startswith("B_FACE|@")

    def test_qualified_component_body_form_picks_one(self):
        # the component-name prefix (no instance suffix) is accepted too, when it picks exactly one
        a = _named_body("Pin", "TOK_A", "A_FACE")
        b = _named_body("Pin", "TOK_B", "B_FACE")
        _install([FakeOcc("A:1", "A", [a]), FakeOcc("B:1", "B", [b])])
        out = _payload(fg.handler(target="A:Pin"))
        assert out["returned"] == 1 and out["matches"][0]["handle"].startswith("A_FACE|@")

    def test_qualified_form_still_matching_two_instances_is_refused(self):
        # one component instanced twice: '<component>:<body>' names BOTH instances' bodies - refuse,
        # listing the per-instance forms that separate them.
        a = _named_body("Pin", "TOK_A", "A_FACE")
        b = _named_body("Pin", "TOK_B", "B_FACE")
        _install([FakeOcc("Jaw:1", "Jaw", [a]), FakeOcc("Jaw:2", "Jaw", [b])])
        res = fg.handler(target="Jaw:Pin")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "Jaw:1:Pin" in res["message"] and "Jaw:2:Pin" in res["message"]

    def test_a_mistyped_body_in_a_real_scope_is_refused_naming_what_it_holds(self):
        # A typo in the qualified form must not fall through to "that component's single body" - the
        # caller would silently get a body they never named. The scope resolved, so say what it holds.
        pin = _named_body("Pin", "TOK_A", "PIN_FACE")
        _install([FakeOcc("Frame:1", "Frame", [pin])])
        res = fg.handler(target="Frame:Pinn")
        assert res["isError"] is True
        assert "holds no body named 'Pinn'" in res["message"] and "'Pin'" in res["message"]

    def test_a_mesh_body_name_is_refused_with_a_pointer(self):
        # a MeshBody has no BRep faces/edges/vertices to hand back - say so instead of reporting an
        # empty scan. Meshes are reached through the components (an occurrence's meshBodies raises).
        _install([], meshes=[MeshBody(name="Scan1")])
        res = fg.handler(target="Scan1")
        assert res["isError"] is True
        assert "MESH" in res["message"] and "mesh_get" in res["message"]


# ── PERCEPTION FIELDS: face outward normal + linear-edge direction ──────────────────────────────
# A face record carries the outward unit normal at its reported position; a straight edge carries its
# unit direction. Both degrade to an omitted field (never a fabricated value) when unevaluable.

class TestPerception:
    def test_planar_face_reports_outward_normal(self):
        # a planar top face at z=5mm with a +Z evaluator normal -> normal [0,0,1]
        face = FakeFace("TOP", _PlaneGeo(), (0, 0, 0.5), evaluator=_Eval(normal=(0, 0, 1)))
        _install([FakeOcc("X:1", "X", [FakeBody(faces=[face])])])
        out = _payload(fg.handler(target="X:1"))
        assert out["matches"][0]["normal"] == [0.0, 0.0, 1.0]

    def test_normal_omitted_when_evaluator_raises(self):
        # off-surface sample point -> getNormalAtPoint raises -> safe() degrades to NO 'normal' field.
        face = FakeFace("CURVED", _CylGeo(0.8), (0, 0, 0), evaluator=_Eval(raises=True))
        _install([FakeOcc("X:1", "X", [FakeBody(faces=[face])])])
        out = _payload(fg.handler(target="X:1", kind="cylinder_face"))
        assert "normal" not in out["matches"][0]

    def test_normal_omitted_when_face_has_no_evaluator(self):
        # a face lacking an evaluator (evaluator=None) yields no normal, not a crash.
        face = FakeFace("PL", _PlaneGeo(), (0, 0, 0))
        _install([FakeOcc("X:1", "X", [FakeBody(faces=[face])])])
        out = _payload(fg.handler(target="X:1"))
        assert "normal" not in out["matches"][0]

    def test_linear_edge_reports_unit_direction(self):
        # a line edge from (0,0,0) to (3,0,0)cm -> normalized direction [1,0,0]
        edge = FakeEdge("LN", _LineGeo((0, 0, 0), (3, 0, 0)), (1.5, 0, 0), length=3.0)
        _install([FakeOcc("X:1", "X", [FakeBody(edges=[edge])])])
        out = _payload(fg.handler(target="X:1", kind="line_edge"))
        m = out["matches"][0]
        assert m["kind"] == "line_edge"
        assert m["direction"] == [1.0, 0.0, 0.0]

    def test_diagonal_edge_direction_is_normalized(self):
        # a 3-4-0 edge -> unit direction [0.6, 0.8, 0]
        edge = FakeEdge("DIAG", _LineGeo((0, 0, 0), (3, 4, 0)), (1.5, 2, 0), length=5.0)
        _install([FakeOcc("X:1", "X", [FakeBody(edges=[edge])])])
        out = _payload(fg.handler(target="X:1", kind="line_edge"))
        assert out["matches"][0]["direction"] == [0.6, 0.8, 0.0]
