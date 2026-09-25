"""Unit tests for ``cam_create_setup.py`` — create a CAM (Manufacture) setup on a part.

A freshly imported bare part has no CAM job; this tool creates the first setup so the other CAM
authoring tools have something to work in. Covers operation-type dispatch (milling/turning),
model selection (handles / names / all-bodies default), naming, and the no-design / no-bodies
guards. No live Fusion - fakes mimic adsk.cam.CAM.setups.
"""

import json
from types import SimpleNamespace

import adsk.cam
import adsk.fusion

from conftest import (ADDITIVE_SETUP_SEEDS, BRepBody, FakePrintSetting, FakeSetup, FakeSetups,
                      MakeComp, install, load_tool, make_design)

cs = load_tool("cam_create_setup")


class _StubbornSetup(FakeSetup):
    """A setup whose name write is accepted and dropped - the rename that never took."""

    @property
    def name(self):
        return "Setup1"

    @name.setter
    def name(self, value):
        pass


def _install(monkeypatch, bodies=None, has_cam=True, setups=None):
    """Wire a design holding `bodies` and a CAM product whose setups collection is `setups`."""
    comp = MakeComp(bodies=[BRepBody("Body1")] if bodies is None else bodies)
    design = make_design(comp=comp)
    cam = SimpleNamespace(setups=setups if setups is not None else FakeSetups()) if has_cam else None

    # the models input discriminates a BRepBody by isinstance, so the shared fake IS the class
    monkeypatch.setattr(adsk.fusion, "BRepBody", BRepBody, raising=False)
    monkeypatch.setattr(cs, "get_cam", lambda: ((cam, None) if cam else (None, "no CAM")))
    # models is a TargetRefList -> resolves through _common.design()/target_component()
    install(cs, design)
    return design, cam, comp


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── operation type ───────────────────────────────────────────────────────────

class TestOperationType:
    def test_default_is_milling(self, monkeypatch):
        _, cam, _ = _install(monkeypatch)
        out = _payload(cs.handler())
        assert cam.setups._added[-1].operationType == adsk.cam.OperationTypes.MillingOperation
        assert out["created"] is True

    def test_turning(self, monkeypatch):
        _, cam, _ = _install(monkeypatch)
        _payload(cs.handler(operation_type="turning"))
        assert cam.setups._added[-1].operationType == adsk.cam.OperationTypes.TurningOperation

    def test_phantom_setup_that_never_lands_bites(self, monkeypatch):
        # add() returns a setup object but it never appears in the re-listed collection -> error
        _, cam, _ = _install(monkeypatch)
        cam.setups.add = lambda inp: FakeSetup("Setup1")   # returned, never appended
        res = cs.handler()
        assert res["isError"] is True
        assert "did not land" in res["message"]

    def test_unknown_type_errors(self, monkeypatch):
        _install(monkeypatch)
        res = cs.handler(operation_type="welding")
        assert res["isError"] is True and "operation_type" in res["message"]


# ── model selection ──────────────────────────────────────────────────────────

class TestModelSelection:
    def test_all_root_bodies_when_omitted(self, monkeypatch):
        _, cam, _ = _install(monkeypatch, bodies=[BRepBody("A"), BRepBody("B")])
        _payload(cs.handler())
        models = cam.setups._added[-1].models
        assert {m.name for m in models} == {"A", "B"}

    def test_default_set_is_every_root_body_including_a_surface_one(self, monkeypatch):
        # The default machining set is every root BRep body, solid AND surface - what the walk does
        # and what the tool now claims. An isSolid filter here would silently drop 'Skin'.
        _, cam, _ = _install(monkeypatch, bodies=[BRepBody("Plate"), BRepBody("Skin", is_solid=False)])
        _payload(cs.handler())
        assert {m.name for m in cam.setups._added[-1].models} == {"Plate", "Skin"}

    def test_named_body(self, monkeypatch):
        _, cam, _ = _install(monkeypatch, bodies=[BRepBody("Widget"), BRepBody("Other")])
        _payload(cs.handler(models="Widget"))
        models = cam.setups._added[-1].models
        assert [m.name for m in models] == ["Widget"]

    def test_body_by_handle(self, monkeypatch):
        design, cam, _ = _install(monkeypatch, bodies=[BRepBody("Body1")])
        h = "/v" + "Z" * 70
        design._tokens[h] = BRepBody("FromHandle")
        _payload(cs.handler(models=h))
        assert cam.setups._added[-1].models[0].name == "FromHandle"

    def test_missing_named_model_errors(self, monkeypatch):
        _install(monkeypatch, bodies=[BRepBody("Body1")])
        res = cs.handler(models="Nope")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_no_bodies_at_all_errors(self, monkeypatch):
        _install(monkeypatch, bodies=[])
        res = cs.handler()
        assert res["isError"] is True and "body" in res["message"].lower()

    def test_the_refusal_describes_the_set_the_walk_actually_takes(self, monkeypatch):
        # The default set is every root BRep body, so a refusal claiming SOLID bodies would send a
        # caller looking for a filter the tool does not apply. It names the way out instead.
        _install(monkeypatch, bodies=[])
        msg = cs.handler()["message"]
        assert "solid" not in msg.lower()
        assert "'models'" in msg and "sub-component" in msg

    def test_the_description_claims_the_same_default_set_as_the_walk(self):
        # The wire claim and the walk are one fact - a wire string promising SOLID bodies while the
        # walk returns every BRep body is the mismatch a caller cannot see. The claim rides on the
        # 'models' input, the value it describes.
        assert "= every root-component body" in cs._MODELS.schema()["description"]
        assert "solid" not in cs._MODELS.schema()["description"].lower()
        assert "solid" not in cs.TOOL_DESCRIPTION.lower()


# ── naming + guards ──────────────────────────────────────────────────────────

class TestNamingAndGuards:
    def test_custom_name(self, monkeypatch):
        _, cam, _ = _install(monkeypatch)
        out = _payload(cs.handler(name="Op10 Mill"))
        assert cam.setups.item(0).name == "Op10 Mill"
        assert out["setup_name"] == "Op10 Mill"

    def test_blank_name_not_assigned(self, monkeypatch):
        # a whitespace-only name is no rename at all, so the setup keeps the auto-name the add
        # gave it ("Setup1") - it is NOT set to "   ".
        _, cam, _ = _install(monkeypatch)
        out = _payload(cs.handler(name="   "))
        assert cam.setups.item(0).name == "Setup1"
        assert out["setup_name"] == "Setup1"

    def test_a_name_a_setup_already_answers_to_is_refused_before_the_add(self, monkeypatch):
        # Measured: Setup.name dedupes like Operation.name - 'LegSetup' with one taken landed
        # 'LegSetup1'. The name is refused before the add so the caller never gets a setup under a
        # name it did not ask for.
        _, cam, _ = _install(monkeypatch)
        _payload(cs.handler(name="LegSetup"))                 # the first one takes the name
        res = cs.handler(name="LegSetup")
        assert res["isError"] is True
        assert "already answer to 'LegSetup'" in res["message"]
        assert "dedupes rather than refusing" in res["message"]
        assert cam.setups.count == 1                          # the second one was never added

    def test_a_free_setup_name_still_creates(self, monkeypatch):
        # the other side of the clash gate: a name no setup carries is not refused
        _, cam, _ = _install(monkeypatch)
        out = _payload(cs.handler(name="LegSetup"))
        assert out["setup_name"] == "LegSetup" and cam.setups.count == 1

    def test_a_declined_name_is_disclosed_not_published_as_requested(self, monkeypatch):
        # The setup LANDED, so a name the platform declines (or dedupes) is a disclosure, not a
        # failed create - and the payload publishes the name Setup.name reads back.
        _install(monkeypatch, setups=FakeSetups(new_setup=_StubbornSetup("Setup1")))
        out = _payload(cs.handler(name="Op10 Mill"))
        assert out["setup_name"] == "Setup1"
        assert "Op10 Mill" in out["rename_warning"] and "did not take" in out["rename_warning"]

    def test_no_cam_product_errors(self, monkeypatch):
        _install(monkeypatch, has_cam=False)
        res = cs.handler()
        assert res["isError"] is True and "CAM" in res["message"]

    def test_no_cam_product_is_reported_before_any_library_is_read(self, monkeypatch):
        # The machine and print-setting resolves are LIBRARY reads (seconds against a catalog of
        # hundreds). A document with no CAM product cannot use their answer, so it meets the CAM
        # refusal instead of paying for them.
        reads = []
        _install(monkeypatch, has_cam=False)
        monkeypatch.setattr(cs, "resolve_machine",
                            lambda req: reads.append(req) or (None, None, "no machine"))
        monkeypatch.setattr(cs, "resolve_print_setting",
                            lambda req: reads.append(req) or (None, None, "no setting"))
        res = cs.handler(operation_type="additive", machine="EOS|M 290",
                         print_setting="316L_040_FlexM291 1.00")
        assert res["isError"] is True and "CAM" in res["message"]
        assert reads == []                        # neither library was walked

    def test_a_subtractive_machine_is_refused_without_reading_a_library(self, monkeypatch):
        # the same ordering on the cheap side: a cross-FIELD refusal needs no catalog either.
        reads = []
        _install(monkeypatch, has_cam=False)
        monkeypatch.setattr(cs, "resolve_machine",
                            lambda req: reads.append(req) or (None, None, "no machine"))
        res = cs.handler(operation_type="milling", machine="EOS|M 290")
        assert res["isError"] is True and "cam_edit_setup" in res["message"]
        assert reads == []


# ── output fields ────────────────────────────────────────────────────────────

class TestOutputFields:
    def test_model_count_and_names_reported(self, monkeypatch):
        _install(monkeypatch, bodies=[BRepBody("A"), BRepBody("B"), BRepBody("C")])
        out = _payload(cs.handler())
        assert out["model_count"] == 3
        assert set(out["models"]) == {"A", "B", "C"}
        assert out["operation_count"] == 0          # fresh setup has no operations
        assert out["operation_type"] == "milling"

    def test_single_body_model_count_one(self, monkeypatch):
        _install(monkeypatch, bodies=[BRepBody("Solo")])
        out = _payload(cs.handler())
        assert out["model_count"] == 1
        assert out["models"] == ["Solo"]

    def test_setup_creation_failure_reported(self, monkeypatch):
        _, cam, _ = _install(monkeypatch)
        def boom(_inp):
            raise RuntimeError("kaboom")
        cam.setups.add = boom
        res = cs.handler()
        assert res["isError"] is True
        assert "kaboom" in res["message"] and "milling" in res["message"]


# ── additive: the machine + print setting arm ────────────────────────────────
# Measured on 2705.1.15 (EOS M 290 + '316L_040_FlexM291 1.00'): the setup lands carrying both, and
# an input with NO machine raises '3 : Setup creation failed' with the count unmoved.

def _swallowing_add(cam, landed):
    """Make setups.add land `landed` AS GIVEN - the input's assignments never copied onto it, which
    is the swallowed-assignment shape the read-backs exist to catch."""
    def add(setup_input):
        cam.setups._added.append(setup_input)
        cam.setups._setups.append(landed)
        return landed
    cam.setups.add = add


def _additive(monkeypatch, kinds=("additive",), machine_err=None, setting_err=None,
              setting=None, **kwargs):
    """The additive arm wired: a resolvable printer, a resolvable setting, and the capability read.
    The setups collection SEEDS the two operations the platform puts in an additive setup, unless
    the caller brought its own."""
    kwargs.setdefault("setups", FakeSetups(seeds=ADDITIVE_SETUP_SEEDS))
    design, cam, comp = _install(monkeypatch, **kwargs)
    machine = SimpleNamespace(description="EOS M 290")
    monkeypatch.setattr(cs, "resolve_machine",
                        lambda req: ((None, None, machine_err) if machine_err
                                     else (machine, "EOS M 290", None)))
    monkeypatch.setattr(cs, "machine_kinds", lambda m: list(kinds))
    monkeypatch.setattr(cs, "machine_label", lambda m: getattr(m, "description", None))
    chosen = setting if setting is not None else FakePrintSetting("316L_040_FlexM291 1.00")
    monkeypatch.setattr(cs, "resolve_print_setting",
                        lambda req, desc="": ((None, None, setting_err) if setting_err
                                              else (chosen, chosen.name, None)))
    return design, cam, machine, chosen


class TestAdditive:
    def test_additive_setup_carries_the_machine_and_print_setting(self, monkeypatch):
        _, cam, machine, setting = _additive(monkeypatch)
        out = _payload(cs.handler(operation_type="additive", machine="EOS|M 290",
                                  print_setting="316L_040_FlexM291 1.00"))
        landed = cam.setups._added[-1]
        assert landed.operationType == adsk.cam.OperationTypes.AdditiveOperation
        assert landed.machine is machine and landed.printSetting is setting
        assert out["operation_type"] == "additive"
        assert out["machine"] == "EOS M 290"
        assert out["print_setting"] == "316L_040_FlexM291 1.00"
        assert out["print_setting_technology"] == "SLM"

    def test_additive_without_a_machine_is_refused_before_the_add(self, monkeypatch):
        # Measured: setups.add of a machineless additive input RAISES and no setup lands, so the
        # ask happens here rather than surfacing that raise.
        _, cam, _m, _s = _additive(monkeypatch)
        res = cs.handler(operation_type="additive")
        assert res["isError"] is True
        assert "Setup creation failed" in res["message"]
        assert cam.setups.count == 0

    def test_a_machine_that_does_not_print_is_refused(self, monkeypatch):
        _, cam, _m, _s = _additive(monkeypatch, kinds=("milling", "turning"))
        res = cs.handler(operation_type="additive", machine="Haas|VF-2")
        assert res["isError"] is True
        assert "isAdditiveSupported false" in res["message"]
        assert "milling, turning" in res["message"]
        assert cam.setups.count == 0

    def test_machine_on_a_milling_setup_is_refused(self, monkeypatch):
        _, cam, _m, _s = _additive(monkeypatch)
        res = cs.handler(operation_type="milling", machine="EOS|M 290")
        assert res["isError"] is True
        assert "'machine'" in res["message"] and "cam_edit_setup" in res["message"]
        assert cam.setups.count == 0

    def test_print_setting_on_a_turning_setup_is_refused(self, monkeypatch):
        _, cam, _m, _s = _additive(monkeypatch)
        res = cs.handler(operation_type="turning", print_setting="ABS (Direct Drive)")
        assert res["isError"] is True and "'print_setting'" in res["message"]
        assert cam.setups.count == 0
        # The machine remedy belongs to the MACHINE field: cam_edit_setup takes no print setting,
        # so naming it here would send a caller to a call that has no such input.
        assert "cam_edit_setup" not in res["message"]

    def test_the_machine_field_keeps_the_remedy_the_others_drop(self, monkeypatch):
        # the other side of the same per-field split - the machine refusal still names the route.
        _, _cam, _m, _s = _additive(monkeypatch)
        for field, kwargs, expect in (
                ("machine", {"machine": "EOS|M 290"}, True),
                ("print_setting", {"print_setting": "ABS"}, False),
                ("print_setting_description", {"print_setting_description": "Fuse"}, False)):
            msg = cs.handler(operation_type="milling", **kwargs)["message"]
            assert f"'{field}'" in msg
            assert ("cam_edit_setup(machine=...)" in msg) is expect, field

    def test_the_description_qualifier_reaches_the_resolver_and_reads_back(self, monkeypatch):
        # The qualifier is the only route to one of two shipped settings sharing a name, so the
        # payload publishes the description the SETUP carries - the read that says which landed.
        seen = {}
        _, cam, _m, _s = _additive(
            monkeypatch, setting=FakePrintSetting("Formlabs SLS", "FORMLABS_SLS"))
        inner = cs.resolve_print_setting
        monkeypatch.setattr(cs, "resolve_print_setting",
                            lambda req, desc="": seen.update(req=req, desc=desc) or inner(req, desc))
        out = _payload(cs.handler(operation_type="additive", machine="EOS|M 290",
                                  print_setting="Formlabs SLS",
                                  print_setting_description="Fuse 1+ 30 W"))
        assert seen == {"req": "Formlabs SLS", "desc": "Fuse 1+ 30 W"}
        assert out["print_setting_description"] == "a print setting"

    def test_a_setting_that_reads_back_another_DESCRIPTION_is_an_error(self, monkeypatch):
        # The name matches on both twins, so a name read-back alone cannot catch the wrong one -
        # only the description can, which is why it is compared and not merely published.
        _, cam, machine, _s = _additive(
            monkeypatch, setting=FakePrintSetting("Formlabs SLS", "FORMLABS_SLS"))
        landed = FakeSetup("Setup1", machine=machine)
        landed.printSetting = FakePrintSetting("Formlabs SLS", "FORMLABS_SLS")
        landed.printSetting.description = "Generic Print Setting for Formlabs Fuse 1 machines."
        _swallowing_add(cam, landed)
        res = cs.handler(operation_type="additive", machine="EOS|M 290",
                         print_setting="Formlabs SLS",
                         print_setting_description="Fuse 1+ 30 W")
        assert res["isError"] is True and "is described" in res["message"]
        assert "the one that landed is not the one picked" in res["message"]

    def test_the_qualifier_without_a_name_is_refused(self, monkeypatch):
        _, cam, _m, _s = _additive(monkeypatch)
        res = cs.handler(operation_type="additive", machine="EOS|M 290",
                         print_setting_description="Fuse 1+ 30 W")
        assert res["isError"] is True and "qualifies 'print_setting'" in res["message"]
        assert cam.setups.count == 0

    def test_the_qualifier_on_a_milling_setup_is_refused(self, monkeypatch):
        _, cam, _m, _s = _additive(monkeypatch)
        res = cs.handler(operation_type="milling", print_setting_description="Fuse 1+ 30 W")
        assert res["isError"] is True and "'print_setting_description'" in res["message"]
        assert cam.setups.count == 0

    def test_an_unresolvable_print_setting_refuses_before_the_add(self, monkeypatch):
        _, cam, _m, _s = _additive(monkeypatch, setting_err="names 2 print settings")
        res = cs.handler(operation_type="additive", machine="EOS|M 290", print_setting="Nylon")
        assert res["isError"] is True and "names 2 print settings" in res["message"]
        assert cam.setups.count == 0

    def test_additive_without_a_print_setting_still_creates(self, monkeypatch):
        _, cam, _m, _s = _additive(monkeypatch)
        out = _payload(cs.handler(operation_type="additive", machine="EOS|M 290"))
        assert cam.setups._added[-1].printSetting is None
        assert out["machine"] == "EOS M 290" and "print_setting" not in out

    def test_a_machine_that_does_not_read_back_is_an_error_not_a_note(self, monkeypatch):
        # A swallowed machine assignment must not report created=true: the setup would print on a
        # printer nothing asked for.
        _, cam, _m, _s = _additive(monkeypatch)
        _swallowing_add(cam, FakeSetup("Setup1", machine=None))
        res = cs.handler(operation_type="additive", machine="EOS|M 290")
        assert res["isError"] is True and "Setup.machine reads" in res["message"]

    def test_a_print_setting_that_reads_back_as_another_is_an_error(self, monkeypatch):
        _, cam, machine, _s = _additive(monkeypatch)
        landed = FakeSetup("Setup1", machine=machine)
        landed.printSetting = FakePrintSetting("ABS (Direct Drive)", "FFF")
        _swallowing_add(cam, landed)
        res = cs.handler(operation_type="additive", machine="EOS|M 290",
                         print_setting="316L_040_FlexM291 1.00")
        assert res["isError"] is True and "Setup.printSetting reads" in res["message"]

    def test_a_setup_landing_as_another_operation_type_is_an_error(self, monkeypatch):
        # createInput took AdditiveOperation; a setup reading back MillingOperation is not what was
        # asked for, and reporting created=true would hand back a job of the wrong kind.
        _, cam, machine, _s = _additive(monkeypatch)
        landed = FakeSetup("Setup1", machine=machine)
        landed.operationType = adsk.cam.OperationTypes.MillingOperation
        _swallowing_add(cam, landed)
        res = cs.handler(operation_type="additive", machine="EOS|M 290")
        assert res["isError"] is True and "Setup.operationType reads back" in res["message"]

    def test_the_seeded_operations_are_counted_and_disclosed(self, monkeypatch):
        # MEASURED: an additive setup lands already holding 'Body Preset1' and 'Additive Toolpath1',
        # so the milling sentence "no operations yet" would be false and the count is not this
        # call's own work.
        _additive(monkeypatch)
        out = _payload(cs.handler(operation_type="additive", machine="EOS|M 290"))
        assert out["operation_count"] == 2
        assert "no operations yet" not in out["note"]
        assert "It is not empty" in out["note"]

    def test_a_setup_that_lands_empty_is_not_called_non_empty(self, monkeypatch):
        # the boundary of the same clause: a count that read 0 gets no "not empty" sentence, which
        # would otherwise describe a setup the payload itself reports as holding nothing.
        _additive(monkeypatch, setups=FakeSetups())          # no seeds
        out = _payload(cs.handler(operation_type="additive", machine="EOS|M 290"))
        assert out["operation_count"] == 0
        assert "It is not empty" not in out["note"]
