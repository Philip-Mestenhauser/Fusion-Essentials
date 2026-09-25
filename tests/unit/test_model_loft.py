"""Unit tests for model_loft.py - ordered sections of every kind, guides, ends and cut evidence."""

import json
import re
import types

import adsk.core
import adsk.fusion
import pytest

from conftest import (load_tool, make_design, install, register_all_tools, MakeComp, BRepBody,
                      BRepEdge, BRepFace, FakeFeature, FakeFeatures, FakeSketchPoint, Profile,
                      _NamedCollection, error_message, make_sketch, make_sketch_curve, payload)

so = load_tool("model_loft")


class _FakeFeature(FakeFeature):
    """The shared feature plus isSolid - the flag loft reads off the FEATURE, not off the body."""
    def __init__(self, name, result_bodies, is_solid=None):
        super().__init__(name=name, bodies=result_bodies)
        if is_solid is not None:
            self.isSolid = is_solid


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
        self._result_bodies = (result_bodies if result_bodies is not None
                               else [BRepBody("Body1", is_solid=True)])
    def createInput(self, op):
        self.last_input = self._input_cls(op)
        return self.last_input
    def add(self, inp):
        return _FakeFeature("Loft1", self._result_bodies, is_solid=self._result_is_solid)


class _FakeFeatures(FakeFeatures):
    """comp.features: a per-kind collection is present only when the test wires one."""
    def __init__(self, loft=None, stitch=None, unstitch=None):
        super().__init__()
        if loft is not None:
            self.loftFeatures = loft
        if stitch is not None:
            self.stitchFeatures = stitch
        if unstitch is not None:
            self.unstitchFeatures = unstitch


@pytest.fixture(autouse=True)
def _types(monkeypatch):
    monkeypatch.setattr(adsk.fusion, "BRepBody", BRepBody, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepFace", BRepFace, raising=False)
    monkeypatch.setattr(adsk.fusion, "Profile", Profile, raising=False)
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal",
                        staticmethod(lambda v: ("real", v)), raising=False)


def _install(features, bodies_by_name=None, handle_map=None):
    comp = MakeComp(name="Comp", bodies=list((bodies_by_name or {}).values()), mesh_bodies=())
    comp.features = features
    return install(so, make_design(comp=comp, tokens=handle_map))


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestLoft:

    def _profiles_design(self, result_is_solid=True, result_bodies=None):
        lf = _FakeLoftFeatures(result_is_solid=result_is_solid, result_bodies=result_bodies)
        # three profile handles -> live Profile entities (order P0, P1, P2)
        p0, p1, p2 = (Profile("0"), Profile("1"), Profile("2"))
        handles = {"H0": p0, "H1": p1, "H2": p2}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        return lf, (p0, p1, p2)

    def _cut_design(self, bodies, moves=()):
        """A loft over 3 profiles on a component holding `bodies`; `moves` are (body, new_volume)
        pairs the add applies - the material effect a real cut has across the mutation."""
        lf = _FakeLoftFeatures()
        handles = {"H0": Profile("0"), "H1": Profile("1"), "H2": Profile("2")}
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

    def test_a_join_landing_a_new_body_names_model_combine(self):
        lf = _FakeLoftFeatures()
        _install(_FakeFeatures(loft=lf), bodies_by_name={"Bar": BRepBody("Bar")},
                 handle_map={"H0": Profile("0"), "H1": Profile("1")})
        out = _payload(so.handler(profiles=["H0", "H1"], operation="join"))
        assert "landed a NEW body (Body1)" in out["note"]
        assert "model_combine(join)" in out["note"]

    def test_a_join_that_grew_the_body_already_there_appends_nothing(self):
        # the feature's result body IS the one the component held - the join fused, say nothing
        lf = _FakeLoftFeatures()
        _install(_FakeFeatures(loft=lf), bodies_by_name={"Body1": BRepBody("Body1")},
                 handle_map={"H0": Profile("0"), "H1": Profile("1")})
        out = _payload(so.handler(profiles=["H0", "H1"], operation="join"))
        assert "NEW body" not in out["note"]

    @pytest.mark.parametrize("operation, told", [("new", False), ("cut", True)])
    def test_the_no_target_body_clause_rides_only_a_cut_or_intersect(self, operation, told):
        lf, _profiles = self._profiles_design()

        def _refuse(inp):
            raise RuntimeError("3 : loft sections are invalid")
        lf.add = _refuse
        res = so.handler(profiles=["H0", "H1"], operation=operation)
        assert res["isError"] is True and "loft sections are invalid" in res["message"]
        assert ("No target body" in res["message"]) is told

    def test_a_section_object_with_a_curve_key_is_refused_naming_the_curve_form(self):
        self._profiles_design()
        res = so.handler(profiles=[{"sketch": "S", "curve": "ellipse:0"}, "H1"], as_surface=True)
        assert res["isError"] is True
        assert "'curve' is not read" in res["message"]
        assert "'<sketch>/<type>:<index>'" in res["message"]

    def test_reports_is_solid_read_back(self):
        self._profiles_design(result_is_solid=True)
        out = _payload(so.handler(profiles=["H0", "H1", "H2"]))
        assert out["is_solid"] is True

    def test_surface_loft_reports_not_solid(self):
        self._profiles_design(result_is_solid=False,
                              result_bodies=[BRepBody("Srf1", is_solid=False)])
        out = _payload(so.handler(profiles=["H0", "H1"], as_surface=True))
        assert out["is_solid"] is False
        assert "SURFACE" in out["note"]

    def test_every_tool_the_note_names_is_a_registered_tool(self):
        # the note is the only next step an agent gets, and a name no tool answers to is a dead end -
        # the registered thickener is 'surface_thicken'.
        registered = {it.to_dict().get("name") for it in register_all_tools()}
        families = {n.split("_", 1)[0] for n in registered}
        self._profiles_design(result_is_solid=False,
                              result_bodies=[BRepBody("Srf1", is_solid=False)])
        notes = [_payload(so.handler(profiles=["H0", "H1"], as_surface=True))["note"]]
        _install(_FakeFeatures(loft=_FakeLoftFeatures()),
                 bodies_by_name={"Bar": BRepBody("Bar")},
                 handle_map={"H0": Profile("0"), "H1": Profile("1")})
        notes.append(_payload(so.handler(profiles=["H0", "H1"], operation="join"))["note"])
        cited = {t for note in notes for t in re.findall(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", note)
                 if t.split("_", 1)[0] in families}
        assert cited, notes                       # a scan that finds no name proves nothing
        assert not cited - registered, sorted(cited - registered)

    def test_as_surface_sets_isSolid_false_on_input(self):
        lf, _ = self._profiles_design()
        _payload(so.handler(profiles=["H0", "H1"], as_surface=True))
        assert lf.last_input.isSolid is False

    def test_fewer_than_two_rejected(self):
        self._profiles_design()
        res = so.handler(profiles=["H0"])
        assert res["isError"] is True
        assert "at least 2 sections" in res["message"]

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
        handles = {"H0": Profile("0"), "H1": Profile("1")}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        res = so.handler(profiles=["H0", "H1"], is_closed=True)
        assert res["isError"] is True
        assert "is_closed=True" in res["message"] and "reads back unchanged" in res["message"]

    def test_rails_and_centerline_both_rejected(self):
        lf = _FakeLoftFeatures()
        rail_ent = object()
        center_ent = object()
        handles = {"H0": Profile("0"), "H1": Profile("1"),
                   "R": rail_ent, "C": center_ent}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        res = so.handler(profiles=["H0", "H1"], rails=["R"], centerline="C")
        assert res["isError"] is True
        assert "centerline OR rails" in res["message"] or "not both" in res["message"]

    def test_centerline_set_on_input(self):
        lf = _FakeLoftFeatures()
        center_ent = object()
        handles = {"H0": Profile("0"), "H1": Profile("1"), "C": center_ent}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        out = _payload(so.handler(profiles=["H0", "H1"], centerline="C"))
        assert lf.last_input.centerLineOrRails.centerlines == [center_ent]
        assert out["has_centerline"] is True

    def test_rails_added_and_counted(self):
        lf = _FakeLoftFeatures()
        r1, r2 = object(), object()
        handles = {"H0": Profile("0"), "H1": Profile("1"), "R1": r1, "R2": r2}
        _install(_FakeFeatures(loft=lf), handle_map=handles)
        out = _payload(so.handler(profiles=["H0", "H1"], rails=["R1", "R2"]))
        assert lf.last_input.centerLineOrRails.rails == [r1, r2]
        assert out["rails_count"] == 2
        assert out["has_centerline"] is False

    def _sketch_rail_design(self, sketch_name="Spine"):
        """A loft design whose component also holds a SKETCH carrying one arc - the spine case:
        at first-loft time no body exists, so find_geometry can mint no handle for it."""
        lf = _FakeLoftFeatures()
        arc = object()
        sk = make_sketch(name=sketch_name, arcs=[arc])
        comp = MakeComp(name="Comp", bodies=(), mesh_bodies=(), sketches=[sk])
        comp.features = _FakeFeatures(loft=lf)
        install(so, make_design(comp=comp,
                                tokens={"H0": Profile("0"), "H1": Profile("1")}))
        return lf, arc

    def test_a_rail_can_be_a_SKETCH_CURVE_ref(self):
        # MEASURED: LoftCenterLineOrRails.addRail(SketchArc) is accepted. find_geometry acquires
        # BRep faces/edges/vertices only, so this second spelling is the only way to hand a loft a
        # spine drawn before any body exists.
        lf, arc = self._sketch_rail_design()
        out = _payload(so.handler(profiles=["H0", "H1"], rails=["Spine/arc:0"]))
        assert lf.last_input.centerLineOrRails.rails == [arc]
        assert out["rails_count"] == 1

    def test_a_centerline_can_be_a_sketch_curve_ref(self):
        lf, arc = self._sketch_rail_design()
        out = _payload(so.handler(profiles=["H0", "H1"], centerline="Spine/arc:0"))
        assert lf.last_input.centerLineOrRails.centerlines == [arc]
        assert out["has_centerline"] is True

    def test_an_unknown_sketch_in_a_rail_ref_is_named(self):
        self._sketch_rail_design()
        res = so.handler(profiles=["H0", "H1"], rails=["NoSuch/arc:0"])
        assert res["isError"] is True
        assert "NoSuch" in res["message"] and "Spine" in res["message"]

    def test_a_curve_the_sketch_does_not_hold_is_named(self):
        self._sketch_rail_design()
        res = so.handler(profiles=["H0", "H1"], rails=["Spine/arc:7"])
        assert res["isError"] is True
        assert "arc:7" in res["message"] and "sketch_get" in res["message"]

    def _guide_components_design(self, guide_b_sketch="Spine"):
        """A loft host beside GuideA and GuideB, each holding one guide sketch carrying one arc.
        Sketch names are only component-locally unique, so 'Spine' in both is the live shape."""
        lf = _FakeLoftFeatures()
        arc_a, arc_b = object(), object()
        host = MakeComp(name="Comp", bodies=(), mesh_bodies=())
        host.features = _FakeFeatures(loft=lf)
        guide_a = MakeComp(name="GuideA", sketches=[make_sketch(name="Spine", arcs=[arc_a])])
        guide_b = MakeComp(name="GuideB", sketches=[make_sketch(name=guide_b_sketch, arcs=[arc_b])])
        install(so, make_design(comp=host, tokens={"H0": Profile("0"), "H1": Profile("1")},
                                all_components=[host, guide_a, guide_b]))
        return lf, arc_a, arc_b

    def test_a_rail_whose_sketch_name_is_shared_resolves_inside_the_guide_scope(self):
        # A name two components carry needs the scope that narrows the GUIDES to pick one.
        lf, arc_a, _arc_b = self._guide_components_design()
        out = _payload(so.handler(profiles=["H0", "H1"], rails=["Spine/arc:0"],
                                  guide_component="GuideA"))
        assert lf.last_input.centerLineOrRails.rails == [arc_a]
        assert out["rails_count"] == 1

    def test_a_centerline_whose_sketch_name_is_shared_resolves_inside_the_guide_scope(self):
        # the OTHER component's curve, so a scope that resolved by position rather than by name
        # would hand back GuideA's arc here
        lf, _arc_a, arc_b = self._guide_components_design()
        out = _payload(so.handler(profiles=["H0", "H1"], centerline="Spine/arc:0",
                                  guide_component="GuideB"))
        assert lf.last_input.centerLineOrRails.centerlines == [arc_b]
        assert out["has_centerline"] is True

    def test_a_shared_guide_name_with_no_scope_is_still_refused(self):
        lf, _arc_a, _arc_b = self._guide_components_design()
        res = so.handler(profiles=["H0", "H1"], rails=["Spine/arc:0"])
        assert res["isError"] is True
        assert "2 sketches are named 'Spine'" in res["message"]
        # the remedy names the input that narrows the GUIDES, which is the one this call can pass
        assert "'guide_component'" in res["message"]
        assert lf.last_input is None

    def test_a_guide_scope_holding_no_sketch_of_that_name_refuses_naming_it(self):
        # GuideB holds no 'Spine' and design-wide exactly one sketch does, so a dropped scope would
        # loft along ANOTHER component's curve without saying so.
        lf, _arc_a, _arc_b = self._guide_components_design(guide_b_sketch="Rib")
        res = so.handler(profiles=["H0", "H1"], rails=["Spine/arc:0"], guide_component="GuideB")
        assert res["isError"] is True
        # the exact spelling the live row matches on: the scope, the name, and the real owner
        assert "'GuideB'" in res["message"] and "no sketch named 'Spine'" in res["message"]
        assert "'GuideA'" in res["message"]
        assert "'guide_component'" in res["message"]
        assert lf.last_input is None

    def test_the_guides_scope_is_separate_from_the_profiles_scope(self):
        # MEASURED: a loft hosted on its profiles' component accepts a rail owned by a sketch in
        # ANOTHER component. One scope over both inputs makes that build unreachable - each
        # refusal's remedy produces the other refusal.
        lf = _FakeLoftFeatures()
        line = object()
        host = MakeComp(name="SecComp", bodies=(), mesh_bodies=())
        host.features = _FakeFeatures(loft=lf)
        p0 = Profile("0", parent_sketch=make_sketch(name="SecA", parent_component=host))
        p1 = Profile("1", parent_sketch=make_sketch(name="SecB", parent_component=host))
        guide = MakeComp(name="GuideB", sketches=[make_sketch(name="Spine", lines=[line])])
        install(so, make_design(comp=host, tokens={"H0": p0, "H1": p1},
                                all_components=[host, guide]))
        out = _payload(so.handler(profiles=["H0", "H1"], rails=["Spine/line:0"],
                                  component="SecComp", guide_component="GuideB"))
        assert lf.last_input.centerLineOrRails.rails == [line]
        assert out["rails_count"] == 1 and out["profiles_count"] == 2

    def test_unknown_operation_rejected(self):
        self._profiles_design()
        res = so.handler(profiles=["H0", "H1"], operation="weld")
        assert res["isError"] is True
        assert "new, join, cut, intersect" in res["message"]

    def test_cut_that_moves_no_volume_is_an_error(self):
        self._cut_design([BRepBody("Bar", is_solid=True, volume=12.0)])
        res = so.handler(profiles=["H0", "H1", "H2"], operation="cut")
        assert res["isError"] is True
        assert "changed nothing" in res["message"] and "'Comp'" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_cut_that_removed_material_publishes_the_delta(self):
        bar = BRepBody("Bar", is_solid=True, volume=12.0)
        self._cut_design([bar], moves=[(bar, 9.5)])
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["volume_delta_cm3"] == -2.5      # signed: material LEFT the body

    def test_a_consumed_body_is_not_read_as_a_no_op(self):
        eaten = BRepBody("Eaten", is_solid=True, volume=4.0)
        kept = BRepBody("Kept", is_solid=True, volume=8.0)
        self._cut_design([eaten, kept], moves=[(eaten, None)])
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["lofted"] is True

    def test_a_new_body_loft_is_never_volume_gated(self):
        self._cut_design([BRepBody("Bar", is_solid=True, volume=12.0)])
        out = _payload(so.handler(profiles=["H0", "H1", "H2"]))
        assert out["lofted"] is True and "volume_delta_cm3" not in out

    def test_a_surface_body_is_not_sampled(self):
        # Only SOLIDS carry the volume a cut moves; sampling an open surface body (whose volume does
        # not read) would make the census unreadable and silently drop the gate.
        surf = BRepBody("Skin", is_solid=False, volume=None)
        bar = BRepBody("Bar", is_solid=True, volume=12.0)
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
        self._profiles_design(result_bodies=[BRepBody("Body1", is_solid=True)])
        out = _payload(so.handler(profiles=["H0", "H1"]))
        assert out["result_bodies"] == ["Body1"]

    def test_a_cut_that_consumed_its_target_is_not_refused_for_an_empty_result(self):
        # a cut/intersect that ate the body outright leaves no result body, and the volume gate
        # above has already proven material moved - refusing there would call a real cut a failure
        eaten = BRepBody("Eaten", is_solid=True, volume=4.0)
        lf = self._cut_design([eaten], moves=[(eaten, None)])
        lf._result_bodies = []
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["result_bodies"] == []

    def test_a_cut_whose_census_never_read_does_not_buy_the_empty_result_carve_out(self):
        # The census was SAMPLED but no volume read at either end, so the no-op gate above stayed
        # silent and nothing about this cut is proven. Keying the carve-out on "a census exists"
        # rather than on measured movement lets an empty result set through as a success.
        blind = BRepBody("Blind", is_solid=True, volume=None)
        lf = self._cut_design([blind])          # no moves: the volume is None at both ends
        lf._result_bodies = []
        res = so.handler(profiles=["H0", "H1", "H2"], operation="cut")
        assert res["isError"] is True
        assert "owns no result body" in res["message"]

    def test_a_measurable_cut_at_the_no_change_band_keeps_the_carve_out(self):
        # delta exactly AT the band is the smallest movement the volume gate does not refuse, so it
        # is the boundary the carve-out must accept - the >= / > edge of `moved`
        band = so._common.NO_VOLUME_CHANGE_CM3
        bar = BRepBody("Bar", is_solid=True, volume=0.0)
        lf = self._cut_design([bar], moves=[(bar, band)])
        lf._result_bodies = []
        out = _payload(so.handler(profiles=["H0", "H1", "H2"], operation="cut"))
        assert out["result_bodies"] == []
        assert out["volume_delta_cm3"] == round(band, 6)

    def test_a_cut_just_inside_the_no_change_band_is_still_refused_as_a_no_op(self):
        # one notch below the band: the volume gate owns this refusal, and the carve-out must not
        # rescue it
        band = so._common.NO_VOLUME_CHANGE_CM3
        bar = BRepBody("Bar", is_solid=True, volume=0.0)
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
                 handle_map={"H0": Profile("0"), "H1": Profile("1")})
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
        owner = MakeComp(name="Owner")
        owner.features = _FakeFeatures(loft=owner_lf)
        # each profile's parentSketch.parentComponent points at the owner (the live Profile chain)
        p0 = Profile("0", parent_sketch=make_sketch(name="Sk0", parent_component=owner))
        p1 = Profile("1", parent_sketch=make_sketch(name="Sk1", parent_component=owner))
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
        # the guides answer to a scope of their OWN, declared beside it - a refusal may only name an
        # input this strict schema takes.
        assert so.tool.input_schema["properties"]["guide_component"] == so._GUIDE_SCOPE[1]
        assert "rails/centerline" in so._GUIDE_SCOPE[1]["description"]

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


# ── edge, point and open-curve sections; end and rail conditions ──────────────────────────────

class _Paths:
    """Path.create with isClosed looked up in `closed` - a local double: Path has no shape dump."""
    def __init__(self, closed):
        self.closed, self.made = closed, []

    def create(self, ent, option):
        self.made.append((ent, option))
        return types.SimpleNamespace(entity=ent, count=1, isClosed=self.closed.get(id(ent), True))


class _Section:
    """An input LoftSection recording its end setters - a local double: no shape dump."""
    def __init__(self, entity, answer=True):
        self.entity, self.calls, self._answer = entity, [], answer

    def __getattr__(self, name):
        if name.startswith("set") and name.endswith("EndCondition"):
            return lambda *args: (self.calls.append((name, args)), self._answer)[1]
        raise AttributeError(name)


class _EndInput:
    """A LoftFeatureInput whose sections are _Sections and whose rails keep their edgeCondition."""
    def __init__(self, op, answer=True):
        self.operation = op
        self.isSolid = True
        self._answer = answer
        self.loftSections = types.SimpleNamespace(added=[])
        self.loftSections.add = lambda ent: self._add(ent)
        self.centerLineOrRails = types.SimpleNamespace(rails=[], addCenterLine=lambda c: None)
        self.centerLineOrRails.addRail = lambda r: self._rail(r)

    def _add(self, ent):
        self.loftSections.added.append(_Section(ent, self._answer))
        return self.loftSections.added[-1]

    def _rail(self, r):
        self.centerLineOrRails.rails.append(types.SimpleNamespace(entity=r, edgeCondition=None))
        return self.centerLineOrRails.rails[-1]


def _built_end(cls, weight=None):
    """A built section whose endCondition reads the class `cls` and, when given, a weight parameter."""
    cond = types.SimpleNamespace(objectType="adsk::fusion::" + cls)
    if weight:
        cond.weight = types.SimpleNamespace(name=weight)
    return types.SimpleNamespace(endCondition=cond)


class _EndLofts:
    """loftFeatures whose built loft reads `ends` and `rails` back (rails default to the input's)."""
    def __init__(self, ends=None, rails=None, answer=True):
        self.last_input, self._ends, self._rails, self._answer = None, ends, rails, answer

    def createInput(self, op):
        self.last_input = _EndInput(op, self._answer)
        return self.last_input

    def add(self, inp):
        feature = FakeFeature(name="Loft1", bodies=[BRepBody("Blend1", is_solid=False)])
        feature.isSolid = False
        feature.loftSections = _NamedCollection(list(self._ends))
        rails = self._rails if self._rails is not None else [
            r.edgeCondition for r in inp.centerLineOrRails.rails]
        feature.centerLineOrRails = _NamedCollection(
            [types.SimpleNamespace(edgeCondition=v) for v in rails])
        return feature


class TestSectionsAndEnds:

    @pytest.fixture
    def rig(self, monkeypatch):
        """Closed rims E0/E1, open edge OPEN, profile P, a 'Nose' point and 'Sec' splines."""
        monkeypatch.setattr(adsk.fusion, "BRepEdge", BRepEdge, raising=False)
        monkeypatch.setattr(adsk.core.ValueInput, "createByString",
                            staticmethod(lambda s: ("string", s)), raising=False)
        edges = {n: BRepEdge(None) for n in ("E0", "E1", "OPEN")}
        open_curve, closed_curve = make_sketch_curve("s0"), make_sketch_curve("s1", is_closed=True)
        paths = _Paths({id(edges["OPEN"]): False, id(open_curve): False, id(closed_curve): True})
        monkeypatch.setattr(adsk.fusion, "Path", paths, raising=False)

        def build(lofts, tokens=None):
            comp = MakeComp(name="Blend", bodies=(), mesh_bodies=(), sketches=[
                make_sketch(name="Nose", points=[FakeSketchPoint()]),
                make_sketch(name="Sec", splines=[open_curve, closed_curve])])
            comp.features = _FakeFeatures(loft=lofts)
            install(so, make_design(comp=comp, tokens={**edges, "P": Profile("0"),
                                                       **(tokens or {})}))
            return lofts
        return types.SimpleNamespace(build=build, edges=edges, paths=paths,
                                     open_curve=open_curve)

    def test_edge_sections_take_smooth_and_tangent_ends_read_back_off_the_feature(self, rig):
        lofts = rig.build(_EndLofts([_built_end("LoftSmoothEndCondition", "d17"),
                                     _built_end("LoftTangentEndCondition", "d18")]))
        out = payload(so.handler(profiles=["E0", "E1"], as_surface=True, start="smooth",
                                 end="tangent"))
        assert [s.entity.entity for s in lofts.last_input.loftSections.added] == [
            rig.edges["E0"], rig.edges["E1"]]
        assert rig.paths.made[0][1] == adsk.fusion.ChainedCurveOptions.noChainedCurves
        first, last = lofts.last_input.loftSections.added
        assert first.calls == [("setSmoothEndCondition", (("real", 1.0),))]
        assert last.calls == [("setTangentEndCondition", (("real", 1.0),))]
        assert out["ends"] == {"start": "smooth", "end": "tangent"}
        assert out["section_kinds"] == ["edge", "edge"]
        assert out["model_parameters"] == {"start_weight": "d17", "end_weight": "d18"}

    def test_a_smooth_end_the_feature_reads_free_is_an_error(self, rig):
        rig.build(_EndLofts([_built_end("LoftFreeEndCondition"), _built_end("LoftFreeEndCondition")]))
        msg = error_message(so.handler(profiles=["E0", "E1"], start="smooth"))
        assert "start section reads a free end, not the smooth end asked for" in msg
        assert "design_delete_feature" in msg

    def test_an_end_that_does_not_read_is_unverified_not_confirmed(self, rig):
        rig.build(_EndLofts([types.SimpleNamespace(), _built_end("LoftFreeEndCondition")]))
        out = payload(so.handler(profiles=["E0", "E1"], start="smooth"))
        assert out["ends"]["start"] is None and out["unverified"] == ["start_end"]

    def test_a_tangent_end_on_a_profile_is_refused_before_the_loft_starts(self, rig):
        lofts = rig.build(_EndLofts([]))
        msg = error_message(so.handler(profiles=["P", "E1"], start="tangent"))
        assert "a tangent end needs a closed edge section, but section 0 is a profile" in msg
        assert lofts.last_input is None

    def test_an_open_edge_is_refused_as_a_section(self, rig):
        lofts = rig.build(_EndLofts([]))
        msg = error_message(so.handler(profiles=["E0", "OPEN"]))
        assert "'profiles'[1]: an edge section is ONE closed edge" in msg
        assert lofts.last_input is None

    def test_a_section_that_resolves_to_nothing_names_both_remedies(self, rig):
        lofts = rig.build(_EndLofts([]), tokens={"SPLIT": [BRepEdge(None), BRepEdge(None)]})
        msg = error_message(so.handler(profiles=["E0", "GONE"]))
        assert "'profiles'[1]: 'GONE' did not resolve" in msg
        assert "find_geometry" in msg and "{sketch, profile_index}" in msg
        msg = error_message(so.handler(profiles=["E0", "SPLIT"]))
        assert "resolved to 2 entities" in msg and "find_geometry" in msg
        assert lofts.last_input is None

    def test_a_sketch_point_is_a_first_or_last_section_only(self, rig):
        lofts = rig.build(_EndLofts([_built_end("LoftTangentEndCondition", "d17"),
                                     _built_end("LoftPointTangentEndCondition", "d18")]))
        msg = error_message(so.handler(profiles=["E0", "Nose/point:0", "E1"]))
        assert "'profiles'[1]: a sketch point is a loft's first or last section" in msg
        out = payload(so.handler(profiles=["E0", "Nose/point:0"], start="tangent",
                                 end="point_tangent"))
        assert out["section_kinds"] == ["edge", "point"]
        assert out["ends"] == {"start": "tangent", "end": "point_tangent"}

    def test_an_open_curve_takes_a_direction_end_and_a_closed_one_is_refused(self, rig):
        lofts = rig.build(_EndLofts([_built_end("LoftDirectionEndCondition", "d20"),
                                     _built_end("LoftFreeEndCondition")]))
        out = payload(so.handler(profiles=["Sec/spline:0", "E1"], start="direction"))
        first = lofts.last_input.loftSections.added[0]
        assert first.entity.entity is rig.open_curve
        assert first.calls == [("setDirectionEndCondition", (("string", "0 deg"), ("real", 1.0)))]
        assert out["ends"]["start"] == "direction"
        msg = error_message(so.handler(profiles=["Sec/spline:1", "E1"]))
        assert "'Sec/spline:1' is a closed curve" in msg

    def test_a_curve_section_lofts_on_its_sketchs_component(self, rig):
        # the Path handed to the loft carries no sketch of its own, so the host is read off the
        # curve it was built over - the owner, as for a profile, not the active component
        owner_lofts, active_lofts = _EndLofts([_built_end("LoftFreeEndCondition")] * 2), _EndLofts([])
        owner = MakeComp(name="Rails", bodies=(), mesh_bodies=())
        owner.features = _FakeFeatures(loft=owner_lofts)
        made = []
        owner.features.createPath = lambda ent, chain: (
            made.append((ent, chain)), types.SimpleNamespace(entity=ent, count=1, isClosed=False))[1]
        sketch = make_sketch(name="Sec", splines=[rig.open_curve], parent_component=owner)
        rig.open_curve.parentSketch = sketch
        active = MakeComp(name="Blend", bodies=(), mesh_bodies=(), sketches=[])
        active.features = _FakeFeatures(loft=active_lofts)
        owner.sketches = _NamedCollection([sketch])
        install(so, make_design(comp=active, tokens=dict(rig.edges),
                                all_components=[active, owner]))
        payload(so.handler(profiles=["Sec/spline:0", "E1"]))
        assert owner_lofts.last_input is not None and active_lofts.last_input is None
        # Path.create raises on a component's native sketch curve, so its owner builds the path
        assert made == [(rig.open_curve, False)]
        assert [ent for ent, _opt in rig.paths.made] == [rig.edges["E1"]]

    def test_an_end_setter_answering_false_is_an_error(self, rig):
        lofts = rig.build(_EndLofts([], answer=False))
        msg = error_message(so.handler(profiles=["E0", "E1"], end="smooth"))
        assert "The end section's smooth end setter answered False" in msg
        assert lofts.last_input is not None

    def test_ends_on_a_closed_loft_are_refused(self, rig):
        rig.build(_EndLofts([]))
        msg = error_message(so.handler(profiles=["E0", "E1"], is_closed=True, end="tangent"))
        assert "is_closed=true leaves this one no end" in msg

    def test_rail_continuity_is_written_on_edge_rails_and_read_back(self, rig):
        rail = BRepEdge(None)
        g1 = adsk.fusion.LoftRailEdgeConditions.G1LoftRailEdgeCondition
        lofts = rig.build(_EndLofts([_built_end("LoftFreeEndCondition")] * 2), tokens={"R": rail})
        out = payload(so.handler(profiles=["E0", "E1"], rails=["R"], rail_continuity="g1"))
        assert lofts.last_input.centerLineOrRails.rails[0].edgeCondition is g1
        assert out["rail_continuity"] == "g1"

    def test_a_rail_the_feature_reads_at_another_continuity_is_an_error(self, rig):
        other = adsk.fusion.LoftRailEdgeConditions.G0LoftRailEdgeCondition
        rig.build(_EndLofts([_built_end("LoftFreeEndCondition")] * 2, rails=[other]),
                  tokens={"R": BRepEdge(None)})
        msg = error_message(so.handler(profiles=["E0", "E1"], rails=["R"], rail_continuity="g2"))
        assert "rail 0 reads edgeCondition" in msg and "not the g2 asked for" in msg

    def test_a_rail_whose_continuity_does_not_read_is_null_and_unverified(self, rig):
        rig.build(_EndLofts([_built_end("LoftFreeEndCondition")] * 2, rails=[None]),
                  tokens={"R": BRepEdge(None)})
        out = payload(so.handler(profiles=["E0", "E1"], rails=["R"], rail_continuity="g1"))
        assert out["rail_continuity"] is None and out["unverified"] == ["rail_continuity"]

    def test_rail_continuity_on_a_sketch_curve_rail_is_refused(self, rig):
        lofts = rig.build(_EndLofts([]))
        msg = error_message(so.handler(profiles=["E0", "E1"], rails=["Sec/spline:0"],
                                       rail_continuity="g1"))
        assert "rails[0] is a" in msg and "find_geometry edge handles" in msg
        assert lofts.last_input is None


class TestLoftEndCondition:
    def test_each_condition_is_offered_only_on_its_measured_section_kind(self):
        kind = so._LOFT_END
        assert kind.legal_on("point_sharp", "point", 1) == ""
        assert "needs a sketch point section, but section 1 is a closed edge" in kind.legal_on(
            "point_tangent", "edge", 1)
        assert kind.legal_on("direction", "profile", 1) == ""
        assert "needs a profile or an open sketch curve section" in kind.legal_on(
            "direction", "edge", 1)
        assert kind.legal_on("free", "point", 1) != ""

    def test_a_sketch_entity_ref_parses_at_any_length_and_a_handle_never_does(self):
        ref = so._inputs.sketch_entity_ref
        long_name = "Seat stay master curves (left side)/spline:3"
        assert ref(long_name) == ("Seat stay master curves (left side)", "spline:3")
        assert ref("Sec/spline:x") is None
        assert ref("abc/def+ghi" * 5) is None
        assert ref("tok|@vertex:1,2/point:1") is None
