"""Unit tests for model_loft.py - the ordered sections, rails/centerline and the cut evidence."""

import json
from conftest import load_tool

so = load_tool("model_loft")


class _FakeBRepBody:
    """Named to register as adsk.fusion.BRepBody so _BODY_KINDS surface/solid checks fire on isSolid.
    `volume` is the reading a cut/intersect is judged on; None models a body whose volume will not
    read (an unmeasurable body, or one the operation consumed whole)."""
    def __init__(self, name, is_solid=False, volume=None):
        self.name = name
        self.isSolid = is_solid
        self.volume = volume


class _FakeProfile:
    """Named to register as adsk.fusion.Profile so ProfileRef's isinstance check passes."""
    def __init__(self, tag=""):
        self.tag = tag


class _FakeFace:
    def __init__(self, name):
        self.name = name


class _FakeBodies:
    """A result-feature .bodies collection (count/item) of bodies with isSolid flags."""
    def __init__(self, bodies):
        self._b = list(bodies)
    @property
    def count(self):
        return len(self._b)
    def item(self, i):
        return self._b[i]


class _FakeFeature:
    def __init__(self, name, result_bodies, is_solid=None):
        self.name = name
        self.bodies = _FakeBodies(result_bodies)
        # loft/unstitch read feature.isSolid; stitch reads body.isSolid. Provide both.
        if is_solid is not None:
            self.isSolid = is_solid


class _FakeColl:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)
    @property
    def count(self):
        return len(self.items)


class _FakeLoftSections:
    def __init__(self):
        self.added = []      # records ORDER of section adds
    def add(self, section):
        self.added.append(section)
        return section


class _FakeCenterLineOrRails:
    def __init__(self):
        self.centerlines = []
        self.rails = []
    def addCenterLine(self, c):
        self.centerlines.append(c)
    def addRail(self, r):
        self.rails.append(r)


class _FakeLoftInput:
    def __init__(self, op):
        self.operation = op
        self.loftSections = _FakeLoftSections()
        self.centerLineOrRails = _FakeCenterLineOrRails()
        self.isSolid = True


class _SwallowingLoftInput(_FakeLoftInput):
    """A LoftFeatureInput that ACCEPTS the isClosed write and keeps its default anyway - the SWIG
    shape set_verified exists to catch (nothing raises, the loft would just run open)."""
    def __setattr__(self, name, value):
        object.__setattr__(self, name, False if name == "isClosed" else value)


class _FakeLoftFeatures:
    def __init__(self, result_is_solid=True, result_bodies=None, input_cls=_FakeLoftInput):
        self.last_input = None
        self._result_is_solid = result_is_solid
        self._input_cls = input_cls
        self._result_bodies = result_bodies if result_bodies is not None else [_FakeBRepBody("Body1", True)]
    def createInput(self, op):
        self.last_input = self._input_cls(op)
        return self.last_input
    def add(self, inp):
        return _FakeFeature("Loft1", self._result_bodies, is_solid=self._result_is_solid)


class _FakeFeatures:
    def __init__(self, loft=None, stitch=None, unstitch=None):
        if loft is not None:
            self.loftFeatures = loft
        if stitch is not None:
            self.stitchFeatures = stitch
        if unstitch is not None:
            self.unstitchFeatures = unstitch


class _FakeComp:
    def __init__(self, features, bodies_by_name=None):
        self.name = "Comp"
        self.features = features
        self._bodies = bodies_by_name or {}
        comp = self
        # count/item(i) is the live collection protocol a volume census walks; itemByName is what a
        # BodyRef resolves through. Both are real BRepBodies members, so the fake carries both.
        self.bRepBodies = type("BB", (), {
            "itemByName": staticmethod(lambda n: comp._bodies.get(n)),
            "item": staticmethod(lambda i: list(comp._bodies.values())[i]),
            "count": property(lambda s: len(comp._bodies)),
        })()
        # Live meshBodies has count/item but NO itemByName (meshbodies-no-itembyname in
        # tests/live/VERIFIED_API_FACTS.md); this comp holds no meshes.
        self.meshBodies = type("MB", (), {"count": 0, "item": staticmethod(lambda i: None)})()


class _FakeDesign:
    def __init__(self, comp, handle_map=None):
        self.activeComponent = comp
        self.rootComponent = comp
        self._handles = handle_map or {}
    def findEntityByToken(self, t):
        e = self._handles.get(t)
        return [e] if e is not None else []


def _install(features, bodies_by_name=None, handle_map=None):
    comp = _FakeComp(features, bodies_by_name)
    design = _FakeDesign(comp, handle_map)
    so.app = type("A", (), {"activeProduct": design})()
    so._common.app = so.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _FakeDesign) else None
    adsk.fusion.BRepBody = _FakeBRepBody
    adsk.fusion.BRepFace = _FakeFace
    adsk.fusion.Profile = _FakeProfile
    # input-kinds resolve via _inputs._common (the app-reference seam), not so.app.
    so._inputs._common.design = lambda: design
    so._inputs._common.target_component = lambda d: comp
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ObjectCollection.create = staticmethod(lambda: _FakeColl())
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestLoft:

    def _profiles_design(self, result_is_solid=True, result_bodies=None):
        lf = _FakeLoftFeatures(result_is_solid=result_is_solid, result_bodies=result_bodies)
        # three profile handles -> live Profile entities (order P0, P1, P2)
        p0, p1, p2 = (_FakeProfile("0"), _FakeProfile("1"), _FakeProfile("2"))
        handles = {"H0": p0, "H1": p1, "H2": p2}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        return lf, (p0, p1, p2)

    def _cut_design(self, bodies, moves=()):
        """A loft over 3 profiles on a component holding `bodies`; `moves` are (body, new_volume)
        pairs the add applies - the material effect a real cut has across the mutation."""
        lf = _FakeLoftFeatures()
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1"), "H2": _FakeProfile("2")}
        _install(_FakeFeatures(loft=lf), bodies_by_name={b.name: b for b in bodies},
                 handle_map=handles)
        base_add = lf.add

        def _add(inp):
            for body, volume in moves:
                body.volume = volume
            return base_add(inp)
        lf.add = _add
        return lf

    def test_three_profiles_added_in_order(self):
        lf, (p0, p1, p2) = self._profiles_design()
        out = _payload(so.handler(profiles=["H0", "H1", "H2"]))
        assert out["lofted"] is True
        assert out["profiles_count"] == 3
        # ORDER is the whole game: sections added exactly H0,H1,H2.
        assert lf.last_input.loftSections.added == [p0, p1, p2]

    def test_reports_is_solid_read_back(self):
        self._profiles_design(result_is_solid=True)
        out = _payload(so.handler(profiles=["H0", "H1", "H2"]))
        assert out["is_solid"] is True

    def test_surface_loft_reports_not_solid(self):
        self._profiles_design(result_is_solid=False,
                              result_bodies=[_FakeBRepBody("Srf1", False)])
        out = _payload(so.handler(profiles=["H0", "H1"], as_surface=True))
        assert out["is_solid"] is False
        assert "SURFACE" in out["note"]

    def test_as_surface_sets_isSolid_false_on_input(self):
        lf, _ = self._profiles_design()
        _payload(so.handler(profiles=["H0", "H1"], as_surface=True))
        assert lf.last_input.isSolid is False

    def test_fewer_than_two_rejected(self):
        self._profiles_design()
        res = so.handler(profiles=["H0"])
        assert res["isError"] is True
        assert "at least 2 profiles" in res["message"]

    def test_is_closed_set_on_input_and_reported(self):
        lf, _ = self._profiles_design()
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], is_closed=True))
        assert lf.last_input.isClosed is True
        assert out["is_closed"] is True

    def test_is_closed_omitted_writes_nothing_and_reports_nothing(self):
        # an unwritten isClosed keeps the API default; the payload must not claim a value for it
        lf, _ = self._profiles_design()
        out = _payload(so.handler(profiles=["H0", "H1"]))
        assert not hasattr(lf.last_input, "isClosed")
        assert "is_closed" not in out

    def test_is_closed_false_is_written_not_skipped(self):
        # False is a REQUEST, not an omission - `if is_closed:` would silently drop it
        lf, _ = self._profiles_design()
        out = _payload(so.handler(profiles=["H0", "H1"], is_closed=False))
        assert lf.last_input.isClosed is False
        assert out["is_closed"] is False

    def test_is_closed_accepts_two_sections(self):
        # measured: a TWO-section closed loft builds a real feature - there is no >=3 guard
        self._profiles_design()
        out = _payload(so.handler(profiles=["H0", "H1"], is_closed=True))
        assert out["lofted"] is True and out["profiles_count"] == 2

    def test_is_closed_that_does_not_take_is_refused(self):
        lf = _FakeLoftFeatures(input_cls=_SwallowingLoftInput)
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1")}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        res = so.handler(profiles=["H0", "H1"], is_closed=True)
        assert res["isError"] is True
        assert "is_closed=True" in res["message"] and "reads back unchanged" in res["message"]

    def test_rails_and_centerline_both_rejected(self):
        lf = _FakeLoftFeatures()
        rail_ent = object()
        center_ent = object()
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1"),
                   "R": rail_ent, "C": center_ent}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        res = so.handler(profiles=["H0", "H1"], rails=["R"], centerline="C")
        assert res["isError"] is True
        assert "centerline OR rails" in res["message"] or "not both" in res["message"]

    def test_centerline_set_on_input(self):
        lf = _FakeLoftFeatures()
        center_ent = object()
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1"), "C": center_ent}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        out = _payload(so.handler(profiles=["H0", "H1"], centerline="C"))
        assert lf.last_input.centerLineOrRails.centerlines == [center_ent]
        assert out["has_centerline"] is True

    def test_rails_added_and_counted(self):
        lf = _FakeLoftFeatures()
        r1, r2 = object(), object()
        handles = {"H0": _FakeProfile("0"), "H1": _FakeProfile("1"), "R1": r1, "R2": r2}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        out = _payload(so.handler(profiles=["H0", "H1"], rails=["R1", "R2"]))
        assert lf.last_input.centerLineOrRails.rails == [r1, r2]
        assert out["rails_count"] == 2
        assert out["has_centerline"] is False

    def test_unknown_operation_rejected(self):
        self._profiles_design()
        res = so.handler(profiles=["H0", "H1"], operation="weld")
        assert res["isError"] is True
        assert "new, join, cut, intersect" in res["message"]

    def test_cut_that_moves_no_volume_is_an_error(self):
        self._cut_design([_FakeBRepBody("Bar", is_solid=True, volume=12.0)])
        res = so.handler(profiles=["H0", "H1", "H2"], operation="cut")
        assert res["isError"] is True
        assert "changed nothing" in res["message"] and "'Comp'" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_cut_that_removed_material_publishes_the_delta(self):
        bar = _FakeBRepBody("Bar", is_solid=True, volume=12.0)
        self._cut_design([bar], moves=[(bar, 9.5)])
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["volume_delta_cm3"] == -2.5      # signed: material LEFT the body

    def test_a_consumed_body_is_not_read_as_a_no_op(self):
        eaten = _FakeBRepBody("Eaten", is_solid=True, volume=4.0)
        kept = _FakeBRepBody("Kept", is_solid=True, volume=8.0)
        self._cut_design([eaten, kept], moves=[(eaten, None)])
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["lofted"] is True

    def test_a_new_body_loft_is_never_volume_gated(self):
        self._cut_design([_FakeBRepBody("Bar", is_solid=True, volume=12.0)])
        out = _payload(so.handler(profiles=["H0", "H1", "H2"]))
        assert out["lofted"] is True and "volume_delta_cm3" not in out

    def test_a_surface_body_is_not_sampled(self):
        # Only SOLIDS carry the volume a cut moves; sampling an open surface body (whose volume does
        # not read) would make the census unreadable and silently drop the gate.
        surf = _FakeBRepBody("Skin", is_solid=False, volume=None)
        bar = _FakeBRepBody("Bar", is_solid=True, volume=12.0)
        self._cut_design([surf, bar])
        res = so.handler(profiles=["H0", "H1", "H2"], operation="cut")
        assert res["isError"] is True and "changed nothing" in res["message"]

    def test_loft_with_no_result_body_is_an_error(self):
        # 'lofted: true' beside result_bodies [] claims a body the payload cannot show
        self._profiles_design(result_bodies=[])
        res = so.handler(profiles=["H0", "H1"])
        assert res["isError"] is True
        assert "owns no result body" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_one_result_body_is_the_boundary_that_passes(self):
        self._profiles_design(result_bodies=[_FakeBRepBody("Body1", True)])
        out = _payload(so.handler(profiles=["H0", "H1"]))
        assert out["result_bodies"] == ["Body1"]

    def test_a_cut_that_consumed_its_target_is_not_refused_for_an_empty_result(self):
        # a cut/intersect that ate the body outright leaves no result body, and the volume gate
        # above has already proven material moved - refusing there would call a real cut a failure
        eaten = _FakeBRepBody("Eaten", is_solid=True, volume=4.0)
        lf = self._cut_design([eaten], moves=[(eaten, None)])
        lf._result_bodies = []
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["result_bodies"] == []

    def test_a_cut_whose_census_never_read_does_not_buy_the_empty_result_carve_out(self):
        # The census was SAMPLED but no volume read at either end, so the no-op gate above stayed
        # silent and nothing about this cut is proven. Keying the carve-out on "a census exists"
        # rather than on measured movement lets an empty result set through as a success.
        blind = _FakeBRepBody("Blind", is_solid=True, volume=None)
        lf = self._cut_design([blind])          # no moves: the volume is None at both ends
        lf._result_bodies = []
        res = so.handler(profiles=["H0", "H1", "H2"], operation="cut")
        assert res["isError"] is True
        assert "owns no result body" in res["message"]

    def test_a_measurable_cut_at_the_no_change_band_keeps_the_carve_out(self):
        # delta exactly AT the band is the smallest movement the volume gate does not refuse, so it
        # is the boundary the carve-out must accept - the >= / > edge of `moved`
        band = so._common.NO_VOLUME_CHANGE_CM3
        bar = _FakeBRepBody("Bar", is_solid=True, volume=0.0)
        lf = self._cut_design([bar], moves=[(bar, band)])
        lf._result_bodies = []
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["result_bodies"] == []
        assert out["volume_delta_cm3"] == round(band, 6)

    def test_a_cut_just_inside_the_no_change_band_is_still_refused_as_a_no_op(self):
        # one notch below the band: the volume gate owns this refusal, and the carve-out must not
        # rescue it
        band = so._common.NO_VOLUME_CHANGE_CM3
        bar = _FakeBRepBody("Bar", is_solid=True, volume=0.0)
        lf = self._cut_design([bar], moves=[(bar, band / 2)])
        lf._result_bodies = []
        res = so.handler(profiles=["H0", "H1", "H2"], operation="cut")
        assert res["isError"] is True
        assert "changed nothing" in res["message"]

    def test_unreadable_is_solid_is_null_and_narrated_as_unverified(self):
        # feature.isSolid did not read: safe() made it falsy, so the note claimed "Result is a
        # SURFACE" off a flag nobody read
        lf = _FakeLoftFeatures(result_is_solid=None)      # no isSolid attribute at all
        _install(_FakeFeatures(loft=lf),
                 handle_map={"H0": _FakeProfile("0"), "H1": _FakeProfile("1")})
        out = _payload(so.handler(profiles=["H0", "H1"]))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "UNVERIFIED" in out["note"]
        assert "Result is a SURFACE" not in out["note"]

    def test_loft_built_on_the_profiles_owning_component(self):
        # The profiles are OWNED by a sub-component while a DIFFERENT component is active. Handing
        # another component's native profile to the active component's features raises bSet live,
        # so the loft feature must be created on the OWNER's features. The active comp carries its own
        # loftFeatures; the owner carries a SEPARATE one - the test proves the owner's got the call.
        owner_lf = _FakeLoftFeatures()
        owner = type("Owner", (), {"features": _FakeFeatures(loft=owner_lf)})()
        # each profile's parentSketch.parentComponent points at the owner (the live Profile chain)
        p0 = _FakeProfile("0"); p0.parentSketch = type("Sk", (), {"parentComponent": owner})()
        p1 = _FakeProfile("1"); p1.parentSketch = type("Sk", (), {"parentComponent": owner})()
        active_lf = _FakeLoftFeatures()
        _install(_FakeFeatures(loft=active_lf), handle_map={"H0": p0, "H1": p1})
        out = _payload(so.handler(profiles=["H0", "H1"]))
        assert out["lofted"] is True
        assert owner_lf.last_input is not None     # the OWNER built the loft
        assert active_lf.last_input is None        # NOT the active component (the bSet trap)

    def test_the_component_scope_is_declared_beside_the_kinds_scope(self):
        # SKETCH-6: a {sketch, profile_index} element addresses a sketch by name, and the refusal's
        # way forward may only name an input this strict schema takes - so the kind's scope_input
        # and the declared property ship together.
        sd = load_tool("_sketch_detail")
        assert so._LOFT_PROFILES.scope_input == "component"
        assert so.tool.input_schema["properties"]["component"] == sd.COMPONENT_SCOPE[1]

    def test_the_component_scope_reaches_the_profile_resolve(self, monkeypatch):
        # a declared property the resolve never sees is a remedy the tool then ignores.
        seen = {}

        def _resolve(raw, component=""):
            seen["component"] = component
            return None, "refused"

        _install(_FakeFeatures(loft=_FakeLoftFeatures()), handle_map={})
        monkeypatch.setattr(so._LOFT_PROFILES, "resolve", _resolve)
        so.handler(profiles=["H0", "H1"], component="Frame")
        assert seen == {"component": "Frame"}
