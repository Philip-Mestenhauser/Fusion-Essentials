"""Unit tests for ``model_sweep.py`` - sweep a profile along a path into a solid/surface.

Pinned: the solid happy path, the open-profile -> surface fallback (and as_surface forcing a surface
off a closed profile), the path builders (a path sketch vs model edges), operation + orientation
handling, target_bodies scoping, the guards (missing profile, bad sketch/path/operation/orientation,
no design), and the honesty contract (an API no-op that creates no body must report isError, and a
swallowed configure/add failure must surface).
"""

import adsk.fusion

from conftest import (load_tool, make_design, install, payload as _payload,
                      error_message, assert_no_active_design, BRepBody, BRepEdge, Line3D,
                      _NamedCollection)

sw = load_tool("model_sweep")


# ── small sweep-shaped fakes ────────────────────────────────────────────────

class FakeProfile:
    pass


class FakeOpenProfile:
    pass


class FakeSketch:
    def __init__(self, name, profiles=(), curves=0):
        self.name = name
        self.profiles = _NamedCollection(list(profiles))
        self.sketchCurves = _NamedCollection([object() for _ in range(curves)])


class FakeSweepInput:
    def __init__(self, profile, path, op):
        self.profile = profile
        self.path = path
        self.operation = op
        self.isSolid = True
        self.orientation = None
        self.participantBodies = None


class FakeSweepFeature:
    def __init__(self, bodies_names=("Body1",), is_solid=True):
        self.name = "Sweep1"
        self.isSolid = is_solid
        self.bodies = _NamedCollection([BRepBody(n) for n in bodies_names])


class FakeSweepFeatures:
    def __init__(self, body_names=("Body1",)):
        self.last = None
        self.body_names = tuple(body_names)
        self.create_raises = False
        self.add_returns_none = False
        self.add_raises = False

    def createInput(self, profile, path, op):
        if self.create_raises:
            raise RuntimeError("createInput boom")
        self.last = FakeSweepInput(profile, path, op)
        return self.last

    def add(self, inp):
        if self.add_raises:
            raise RuntimeError("add boom")
        if self.add_returns_none:
            return None
        # Echo the requested isSolid back off the feature, as the live API does.
        return FakeSweepFeature(bodies_names=self.body_names, is_solid=inp.isSolid)


class FakeFeatures:
    def __init__(self, sweepfeatures):
        self.sweepFeatures = sweepfeatures
        self.path_calls = []
        self.path_returns = ("PATH",)   # truthy fake Path (index 0 -> first return)

    def createPath(self, seed, is_chain):
        self.path_calls.append((seed, is_chain))
        return self.path_returns


def _install(*, closed_profiles=1, open_curves=0, body_names=("Body1",), tokens=None,
             extra_sketches=()):
    """Build a root component carrying the sweep surface (sketches + features + createOpenProfile) and
    wire it in via conftest's make_design/install (both seams). Returns (module-features, design)."""
    from conftest import MakeComp
    sf = FakeSweepFeatures(body_names=body_names)
    comp = MakeComp(name="Root", bodies=())
    comp.features = FakeFeatures(sf)
    comp.createOpenProfile = lambda coll, chain: FakeOpenProfile()

    sketches = [
        FakeSketch("Prof", profiles=[FakeProfile() for _ in range(closed_profiles)],
                   curves=open_curves),
        FakeSketch("PathSketch", curves=3),
    ]
    sketches.extend(extra_sketches)
    comp.sketches = _NamedCollection(sketches)

    design = make_design(comp=comp, tokens=tokens or {})
    install(sw, design)
    return sf, design


# ── the solid happy path ────────────────────────────────────────────────────

class TestSolid:
    def test_solid_sweep_along_path_sketch(self):
        sf, _ = _install()
        out = _payload(sw.handler(profile={"sketch": "Prof", "profile_index": 0},
                                  path="sketch:PathSketch"))
        assert out["swept"] is True
        assert out["is_solid"] is True
        assert out["as_surface"] is False
        assert out["open_profile"] is False
        assert out["path"] == "sketch:PathSketch"
        assert out["result_bodies"] == ["Body1"]
        # A closed profile with the default (solid) sets isSolid True on the input.
        assert sf.last.isSolid is True

    def test_path_sketch_seeds_createpath_with_chain(self):
        sf, design = _install()
        _payload(sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch"))
        feats = design.rootComponent.features
        # The path sketch has curves -> createPath is called with (seed, isChain=True).
        assert len(feats.path_calls) == 1 and feats.path_calls[0][1] is True

    def test_multiple_result_bodies_collected(self):
        _install(body_names=("R0", "R1", "R2"))
        out = _payload(sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch"))
        assert out["result_bodies"] == ["R0", "R1", "R2"]


# ── surface fallback + forcing ──────────────────────────────────────────────

class TestSurface:
    def test_open_profile_falls_back_to_surface(self):
        # Profile sketch has NO closed region but open curves -> an OPEN profile / SURFACE sweep.
        sf, _ = _install(closed_profiles=0, open_curves=2)
        out = _payload(sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch"))
        assert out["open_profile"] is True
        assert out["as_surface"] is True
        assert out["is_solid"] is False
        assert "SURFACE" in out["note"]
        assert sf.last.isSolid is False

    def test_as_surface_forces_surface_off_closed_profile(self):
        # A CLOSED profile, but as_surface=True -> isSolid False (open tube), open_profile stays False.
        sf, _ = _install(closed_profiles=1)
        out = _payload(sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch",
                                  as_surface=True))
        assert out["is_solid"] is False
        assert out["as_surface"] is True
        assert out["open_profile"] is False
        assert sf.last.isSolid is False


# ── path from model edges ───────────────────────────────────────────────────

class TestEdgePath:
    def test_single_edge_path_chains_from_seed(self):
        adsk.fusion.BRepEdge = BRepEdge
        edge = BRepEdge(curve=Line3D())
        sf, design = _install(tokens={"EDGE1": edge})
        out = _payload(sw.handler(profile={"sketch": "Prof"}, path=["EDGE1"]))
        assert out["path"] == "1 edge(s)"
        feats = design.rootComponent.features
        # A single edge is passed to createPath (auto-chain), not Path.create.
        assert feats.path_calls and feats.path_calls[0][0] is edge

    def test_bad_edge_handle_errors(self):
        adsk.fusion.BRepEdge = BRepEdge
        _install(tokens={})
        res = sw.handler(profile={"sketch": "Prof"}, path=["NOPE"])
        assert res["isError"] is True
        assert "handle did not resolve" in error_message(res)


# ── operation + orientation + target_bodies ─────────────────────────────────

class TestOptions:
    def test_operation_echoed(self):
        _install()
        out = _payload(sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch",
                                  operation="join"))
        assert out["operation"] == "join"

    def test_orientation_parallel_applied(self):
        # Pin that the parallel keyword actually reaches the input via the SweepOrientationTypes enum.
        adsk.fusion.SweepOrientationTypes.ParallelOrientationType = "PARALLEL"
        sf, _ = _install()
        out = _payload(sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch",
                                  orientation="parallel"))
        assert out["orientation"] == "parallel"
        assert sf.last.orientation == "PARALLEL"

    def test_target_bodies_rejected_on_new(self):
        _install()
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch",
                         target_bodies=["Body1"])
        assert res["isError"] is True and "target_bodies" in res["message"]

    def test_target_bodies_unresolved_errors_on_cut(self):
        _install()
        # A cut allows target_bodies, but an unresolvable name is a clean BodyRefList error - the
        # mutation never runs, so a wrong scope can't silently pass through.
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch",
                         operation="cut", target_bodies=["Missing"])
        assert res["isError"] is True and "Missing" in res["message"]


# ── guards ──────────────────────────────────────────────────────────────────

class TestGuards:
    def test_missing_profile(self):
        _install()
        res = sw.handler(profile=None, path="sketch:PathSketch")
        assert res["isError"] is True and "profile" in res["message"].lower()

    def test_unknown_sketch_profile(self):
        _install()
        res = sw.handler(profile={"sketch": "NoSuch"}, path="sketch:PathSketch")
        assert res["isError"] is True and "sketch" in res["message"].lower()

    def test_unknown_path_sketch(self):
        _install()
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:NoPath")
        assert res["isError"] is True and "NoPath" in res["message"]

    def test_bad_operation(self):
        _install()
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch", operation="weld")
        assert res["isError"] is True and "operation" in res["message"].lower()

    def test_bad_orientation(self):
        _install()
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch",
                         orientation="sideways")
        assert res["isError"] is True and "orientation" in res["message"].lower()

    def test_no_active_design(self):
        _install()
        assert_no_active_design(sw, sw.handler,
                                profile={"sketch": "Prof"}, path="sketch:PathSketch")


# ── honesty contract ────────────────────────────────────────────────────────

class TestHonesty:
    def test_no_body_created_is_error(self):
        # add() returns a feature with ZERO bodies on a 'new' op -> a silent no-op; must be isError.
        _install(body_names=())
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch")
        assert res["isError"] is True and "no body" in res["message"].lower()

    def test_add_returning_none_is_error(self):
        sf, _ = _install()
        sf.add_returns_none = True
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch")
        assert res["isError"] is True and "no feature" in res["message"].lower()

    def test_createinput_failure_surfaces(self):
        sf, _ = _install()
        sf.create_raises = True
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch")
        assert res["isError"] is True and "start sweep" in res["message"].lower()

    def test_add_failure_surfaces(self):
        sf, _ = _install()
        sf.add_raises = True
        res = sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch")
        assert res["isError"] is True and "sweep failed" in res["message"].lower()


# ── cross-component hosting (F24 class): the feature lands on the profile's OWNER ────────────────

class _OwnedProfile:
    """A closed profile whose parentSketch.parentComponent names its OWNING component - the chain
    profile_host_component reads to decide where the feature is built (mirrors the live Profile)."""
    def __init__(self, owner):
        self.parentSketch = type("Sk", (), {"parentComponent": owner})()


def _install_two_component(body_names=("Body1",)):
    """Root is the ACTIVE component; a SUB-component 'Frame' owns the profile + the path sketch. Each
    component carries its OWN features surface, so the test can tell WHICH one the sweep was built on.
    Returns (root_features, sub_features, design)."""
    from conftest import MakeComp
    root_sf = FakeSweepFeatures(body_names=body_names)
    root = MakeComp(name="Root", bodies=())
    root.features = FakeFeatures(root_sf)
    root.createOpenProfile = lambda coll, chain: FakeOpenProfile()
    root.sketches = _NamedCollection([])

    sub_sf = FakeSweepFeatures(body_names=body_names)
    sub = MakeComp(name="Frame", bodies=())
    sub.features = FakeFeatures(sub_sf)
    sub.createOpenProfile = lambda coll, chain: FakeOpenProfile()
    # The profile is OWNED by the sub-component; the path sketch lives there too.
    prof_sketch = FakeSketch("Prof", profiles=[_OwnedProfile(sub)])
    sub.sketches = _NamedCollection([prof_sketch, FakeSketch("PathSketch", curves=3)])

    design = make_design(comp=root, all_components=[root, sub])
    install(sw, design)
    return root_sf, sub_sf, design


class TestCrossComponentHost:
    def test_sweep_is_built_on_the_profiles_owning_component(self):
        # The profile is owned by sub-component 'Frame' while ROOT is active. Handing another
        # component's profile to the ACTIVE component's features raises bSet live (F24), so the feature
        # must be created on the OWNER's features - proven here by which features object got the call.
        root_sf, sub_sf, _ = _install_two_component()
        out = _payload(sw.handler(profile={"sketch": "Prof", "profile_index": 0},
                                  path="sketch:PathSketch"))
        assert out["swept"] is True
        assert sub_sf.last is not None       # the OWNER built the sweep
        assert root_sf.last is None          # NOT the active/root component (would be the bSet trap)


# ── declared outputs ────────────────────────────────────────────────────────

def test_declared_returns_present_in_payload():
    _install()
    out = _payload(sw.handler(profile={"sketch": "Prof"}, path="sketch:PathSketch"))
    for spec in sw.RETURNS:
        assert spec.assert_present(out) == "", spec.key
