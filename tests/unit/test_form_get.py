# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""form_get: the Form list judged against the creation record, the cage slice, and the open-edit
answer that says the list is incomplete."""

import json
import types

import adsk.fusion
import pytest

from conftest import (BRepBody, FakeApplication, FakeFeatures, FakeFormFeature, FakeFormFeatures,
                      FakeTimeline, FakeTimelineObject, FakeTSplineBodies, FakeTSplineBody,
                      FakeUserInterface, MakeComp, MakeDesign, _NamedCollection, error_message,
                      install, load_tool, make_bbox, payload)

fg = load_tool("form_get")
tsm = load_tool("_tsm")
fcommon = load_tool("_form_common")


def _form(name, comp, body_faces=6, volume=5.8, text=None, index=0):
    """A finished Form holding one T-spline body (its TSM `text`) and one B-Rep body."""
    ff = FakeFormFeature(name, component=comp)
    ff.tSplineBodies = FakeTSplineBodies([FakeTSplineBody("Body1", text or "", ff)], owner=ff)
    ff.bodies = _NamedCollection([BRepBody(name="Body1", bbox=make_bbox((-1, -1, -1), (1, 1, 1)),
                                           volume=volume, face_count=body_faces, area=24.0)])
    ff.timelineObject = FakeTimelineObject(name=name, index=index, entity=ff)
    return ff


def _record(**over):
    rec = {"cage_hash": "x", "brep_faces": 6, "volume_cm3": 5.8, "area_cm2": None,
           "extent_cm": [2.0, 2.0, 2.0]}
    rec.update(over)
    return rec


@pytest.fixture
def rig(monkeypatch):
    """Two components each holding Forms on one timeline; the Design workspace active."""
    root, other = MakeComp(name="Root"), MakeComp(name="Other")
    timeline = FakeTimeline([])
    for comp in (root, other):
        comp.features = FakeFeatures()
        comp.features.formFeatures = FakeFormFeatures(component=comp)
    design = install(fg, MakeDesign(comp=root, design_type=1, timeline=timeline,
                                    all_components=[root, other]))
    ui = FakeUserInterface(active_workspace=types.SimpleNamespace(id="FusionSolidEnvironment"))
    monkeypatch.setattr(fg._common, "app", FakeApplication(active_product=design,
                                                           user_interface=ui))

    def add(comp, ff):
        comp.features.formFeatures._forms.append(ff)
        timeline._items.append(ff.timelineObject)
        return ff
    return types.SimpleNamespace(design=design, root=root, other=other, ui=ui, add=add)


class TestFormGet:
    def test_an_open_form_edit_reads_incomplete(self, rig):
        rig.design.designType = 0
        rig.ui.activeWorkspace = types.SimpleNamespace(id="TSplineEnvironment")
        out = payload(fg.handler())
        assert out["in_form_edit"] is True and out["complete"] is False

    def test_an_ambiguous_form_name_is_refused(self, rig):
        rig.add(rig.root, _form("Form1", rig.root, index=0))
        rig.add(rig.other, _form("Form1", rig.other, index=1))
        msg = error_message(fg.handler(form="Form1", include=["cage"]))
        assert "Root/Form1" in msg and "Other/Form1" in msg

    def test_modified_downstream_comes_from_the_record_never_from_the_bodies(self, rig):
        untouched = rig.add(rig.root, _form("Form1", rig.root, index=0))
        shelled = rig.add(rig.root, _form("Form2", rig.root, body_faces=10, volume=1.5, index=1))
        unrecorded = rig.add(rig.root, _form("Form3", rig.root, index=2))
        for ff in (untouched, shelled):
            assert fcommon.store_record(ff, _record())
        out = payload(fg.handler())
        rows = {r["form"]: r for r in out["forms"]}
        assert out["note"].startswith("record null")
        assert rows["Root/Form1"]["modified_downstream"] is False
        assert rows["Root/Form2"]["modified_downstream"] is True
        assert rows["Root/Form3"]["modified_downstream"] is None
        assert rows["Root/Form3"]["record"] is None and unrecorded._deletes == 0

    def test_a_rolled_back_form_or_an_amountless_record_reads_modified_null(self, rig):
        rolled = rig.add(rig.root, _form("Form1", rig.root, index=0))
        rolled.healthState = adsk.fusion.FeatureHealthStates.RolledBackFeatureHealthState
        rolled.bodies = _NamedCollection([])
        amountless = rig.add(rig.root, _form("Form2", rig.root, index=1))
        assert fcommon.store_record(rolled, _record())
        assert fcommon.store_record(amountless, _record(volume_cm3=None))
        out = payload(fg.handler())
        assert [r["modified_downstream"] for r in out["forms"]] == [None, None]
        assert "record null" not in out["note"]

    def test_a_form_holding_no_tspline_body_says_so(self, rig):
        ff = rig.add(rig.root, _form("Form1", rig.root))
        ff.tSplineBodies = FakeTSplineBodies([], owner=ff)
        assert "holds no T-spline body" in error_message(fg.handler(form="Form1", include=["cage"]))

    def test_record_matches_cage_is_false_after_a_cage_change(self, rig):
        cage = tsm.box([2.0, 2.0, 2.0], [1, 1, 1])
        ff = rig.add(rig.root, _form("Form1", rig.root, text=tsm.emit(cage)))
        assert fcommon.store_record(ff, _record(cage_hash=tsm.canonical_hash(cage)))
        out = payload(fg.handler(form="Form1", include=["cage"], units="cm"))
        assert out["record_matches_cage"] is True and out["cage"]["faces"] == cage["faces"]
        moved = dict(cage, vertices=[[x + 0.5, y, z] for x, y, z in cage["vertices"]])
        ff.tSplineBodies.item(0)._tsm = tsm.emit(moved)
        assert payload(fg.handler(form="Form1", include=["cage"]))["record_matches_cage"] is False

    def test_a_form_the_timeline_hides_in_a_collapsed_group_resolves_by_its_label(self, rig):
        cage = tsm.box([2.0, 2.0, 2.0], [1, 1, 1])
        rig.add(rig.root, _form("Form1", rig.root, text=tsm.emit(cage), index=0))
        rig.add(rig.root, _form("Form2", rig.root, index=1))
        # a collapsed group stands in the timeline walk for the Form it holds
        rig.design.timeline._items[0] = FakeTimelineObject(name="Group1", index=0, is_group=True)
        out = payload(fg.handler(form="Root/Form1", include=["cage"], units="cm"))
        assert out["form"] == "Root/Form1" and out["cage"]["faces"] == cage["faces"]

    @pytest.mark.parametrize("form,said", [
        ("", "include=['cage'] reads one Form and the design holds 2"),
        ("Extrude1", "no Form is named 'Extrude1' - the Forms: Root/Form1, Other/Form2"),
        ("1", "'Extrude1' is a ExtrudeFeature, not a Form")])
    def test_a_cage_read_that_names_no_single_form_is_refused(self, rig, form, said):
        rig.add(rig.root, _form("Form1", rig.root, index=0))
        extrude = types.SimpleNamespace(name="Extrude1", objectType="adsk::fusion::ExtrudeFeature",
                                        parentComponent=rig.root)
        rig.design.timeline._items.append(FakeTimelineObject(name="Extrude1", index=1,
                                                             entity=extrude))
        rig.add(rig.other, _form("Form2", rig.other, index=2))
        assert said in error_message(fg.handler(form=form, include=["cage"]))

    def test_body_picks_one_of_a_forms_two_tspline_bodies(self, rig):
        one, two = tsm.box([2.0, 2.0, 2.0], [1, 1, 1]), tsm.box([4.0, 4.0, 4.0], [1, 1, 1])
        ff = rig.add(rig.root, _form("Form1", rig.root, text=tsm.emit(one)))
        ff.tSplineBodies._bodies.append(FakeTSplineBody("Body2", tsm.emit(two), ff))
        assert "holds 2 T-spline bodies ['Body1', 'Body2']" in error_message(
            fg.handler(form="Form1", include=["cage"]))
        out = payload(fg.handler(form="Form1", body="Body2", include=["cage"], units="cm"))
        assert out["tspline_body"] == "Body2" and out["cage"]["vertices"] == two["vertices"]
        assert "holds no T-spline body named 'Body3'" in error_message(
            fg.handler(form="Form1", body="Body3", include=["cage"]))

    def test_a_cage_past_the_character_cap_is_withheld_and_one_at_it_returned(self, rig,
                                                                               monkeypatch):
        cage = tsm.box([2.0, 2.0, 2.0], [1, 1, 1])
        rig.add(rig.root, _form("Form1", rig.root, text=tsm.emit(cage)))
        size = len(json.dumps(payload(fg.handler(form="Form1", include=["cage"], units="cm"))[
            "cage"], separators=(",", ":")))
        monkeypatch.setattr(fg, "_CAGE_MAX_CHARS", size)
        assert payload(fg.handler(form="Form1", include=["cage"], units="cm"))["cage"]
        monkeypatch.setattr(fg, "_CAGE_MAX_CHARS", size - 1)
        out = payload(fg.handler(form="Form1", include=["cage"], units="cm"))
        assert out["cage"] is None and f"{size} characters" in out["cage_unrepresentable"][0]

    def test_a_list_past_max_results_says_it_is_truncated(self, rig):
        for i in range(3):
            rig.add(rig.root, _form(f"Form{i + 1}", rig.root, index=i))
        out = payload(fg.handler(max_results=2))
        assert len(out["forms"]) == 2 and out["count"] == 3 and out["truncated"] is True

    @pytest.mark.parametrize("record,lo,hi", [
        (_record(box_cm=[[-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]]), (4, -1, -1), (6, 1, 1)),
        (_record(), (-1.5, -1, -1), (1.5, 1, 1))])
    def test_a_body_moved_or_stretched_downstream_reads_modified(self, rig, record, lo, hi):
        ff = rig.add(rig.root, _form("Form1", rig.root))
        assert fcommon.store_record(ff, record)
        ff.bodies.item(0).boundingBox = make_bbox(lo, hi)
        row = payload(fg.handler())["forms"][0]
        assert row["modified_downstream"] is True and "box_cm" not in row["record"]
