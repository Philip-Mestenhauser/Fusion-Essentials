"""Unit tests for ``cam_create_setup.py`` — create a CAM (Manufacture) setup on a part.

A freshly imported bare part has no CAM job; this tool creates the first setup so the other CAM
authoring tools have something to work in. Covers operation-type dispatch (milling/turning),
model selection (handles / names / all-bodies default), naming, and the no-design / no-bodies
guards. No live Fusion - fakes mimic adsk.cam.CAM.setups.
"""

import json
from conftest import load_tool

cs = load_tool("cam_create_setup")


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeBody:
    def __init__(self, name, is_solid=True):
        self.name = name
        self.isSolid = is_solid


class FakeBodies:
    def __init__(self, bodies):
        self._list = bodies
        self._by = {b.name: b for b in bodies}
    @property
    def count(self):
        return len(self._list)
    def item(self, i):
        return self._list[i]
    def itemByName(self, n):
        return self._by.get(n)


class FakeComp:
    def __init__(self, bodies):
        self.bRepBodies = FakeBodies(bodies)
        self.occurrences = type("O", (), {"itemByName": lambda self, n: None})()
        self.allOccurrences = []


class FakeSetupInput:
    def __init__(self, op_type):
        self.operationType = op_type
        self.models = []
        self.name = None


class FakeSetup:
    def __init__(self, inp):
        self.name = inp.name or "Setup1"
        self.operationType = inp.operationType
        self.models = inp.models
        self.operations = type("Ops", (), {"count": 0})()


class FakeSetups:
    def __init__(self):
        self.added = []
    @property
    def count(self):
        return len(self.added)
    def item(self, i):
        return self.added[i]
    def createInput(self, op_type):
        return FakeSetupInput(op_type)
    def add(self, inp):
        s = FakeSetup(inp)
        self.added.append(s)
        return s


class FakeCAM:
    def __init__(self):
        self.setups = FakeSetups()


class FakeDesign:
    def __init__(self, comp):
        self.rootComponent = comp
        self._tokens = {}
    def findEntityByToken(self, t):
        e = self._tokens.get(t)
        return [e] if e is not None else []


def _install(monkeypatch, bodies=None, has_cam=True):
    bodies = bodies if bodies is not None else [FakeBody("Body1")]
    comp = FakeComp(bodies)
    design = FakeDesign(comp)
    cam = FakeCAM() if has_cam else None

    import adsk.cam, adsk.fusion
    for n in ("MillingOperation", "TurningOperation"):
        setattr(adsk.cam.OperationTypes, n, n)
    adsk.fusion.BRepBody = FakeBody
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None

    # the tool reads CAM via the shared get_cam resolver and design via _design
    monkeypatch.setattr(cs, "get_cam", lambda: ((cam, None) if cam else (None, "no CAM")))
    cs._design = lambda: design
    # models is a BodyRefList -> resolves via _common.design()/target_component() (the app-ref seam)
    cs._inputs._common.design = lambda: design
    cs._inputs._common.target_component = lambda d: comp
    return design, cam, comp


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── operation type ───────────────────────────────────────────────────────────

class TestOperationType:
    def test_default_is_milling(self, monkeypatch):
        _, cam, _ = _install(monkeypatch)
        out = _payload(cs.handler())
        assert cam.setups.added[-1].operationType == "MillingOperation"
        assert out["created"] is True

    def test_turning(self, monkeypatch):
        _, cam, _ = _install(monkeypatch)
        _payload(cs.handler(operation_type="turning"))
        assert cam.setups.added[-1].operationType == "TurningOperation"

    def test_phantom_setup_that_never_lands_bites(self, monkeypatch):
        # add() returns a setup object but it never appears in the re-listed collection -> error
        _, cam, _ = _install(monkeypatch)
        cam.setups.add = lambda inp: FakeSetup(inp)     # returned, never appended
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
        _, cam, _ = _install(monkeypatch, bodies=[FakeBody("A"), FakeBody("B")])
        _payload(cs.handler())
        models = cam.setups.added[-1].models
        assert {m.name for m in models} == {"A", "B"}

    def test_default_set_is_every_root_body_including_a_surface_one(self, monkeypatch):
        # The default machining set is every root BRep body, solid AND surface - what the walk does
        # and what the tool now claims. An isSolid filter here would silently drop 'Skin'.
        _, cam, _ = _install(monkeypatch, bodies=[FakeBody("Plate"), FakeBody("Skin", is_solid=False)])
        _payload(cs.handler())
        assert {m.name for m in cam.setups.added[-1].models} == {"Plate", "Skin"}

    def test_named_body(self, monkeypatch):
        _, cam, _ = _install(monkeypatch, bodies=[FakeBody("Widget"), FakeBody("Other")])
        _payload(cs.handler(models="Widget"))
        models = cam.setups.added[-1].models
        assert [m.name for m in models] == ["Widget"]

    def test_body_by_handle(self, monkeypatch):
        design, cam, _ = _install(monkeypatch, bodies=[FakeBody("Body1")])
        h = "/v" + "Z" * 70
        design._tokens[h] = FakeBody("FromHandle")
        _payload(cs.handler(models=h))
        assert cam.setups.added[-1].models[0].name == "FromHandle"

    def test_missing_named_model_errors(self, monkeypatch):
        _install(monkeypatch, bodies=[FakeBody("Body1")])
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
        # The wire claim and the walk are one fact - a description promising SOLID bodies while the
        # walk returns every BRep body is the mismatch a caller cannot see.
        assert "omit for EVERY body in the root component" in cs.TOOL_DESCRIPTION
        assert "solid" not in cs.TOOL_DESCRIPTION.lower()


# ── naming + guards ──────────────────────────────────────────────────────────

class TestNamingAndGuards:
    def test_custom_name(self, monkeypatch):
        _, cam, _ = _install(monkeypatch)
        out = _payload(cs.handler(name="Op10 Mill"))
        assert cam.setups.added[-1].name == "Op10 Mill"
        assert out["setup_name"] == "Op10 Mill"

    def test_blank_name_not_assigned(self, monkeypatch):
        # whitespace-only name -> inp.name left at the FakeSetupInput default (None),
        # so the setup keeps its auto-name ("Setup1"), it is NOT set to "   ".
        _, cam, _ = _install(monkeypatch)
        out = _payload(cs.handler(name="   "))
        assert cam.setups.added[-1].name == "Setup1"
        assert out["setup_name"] == "Setup1"

    def test_no_cam_product_errors(self, monkeypatch):
        _install(monkeypatch, has_cam=False)
        res = cs.handler()
        assert res["isError"] is True and "CAM" in res["message"]


# ── output fields ────────────────────────────────────────────────────────────

class TestOutputFields:
    def test_model_count_and_names_reported(self, monkeypatch):
        _install(monkeypatch, bodies=[FakeBody("A"), FakeBody("B"), FakeBody("C")])
        out = _payload(cs.handler())
        assert out["model_count"] == 3
        assert set(out["models"]) == {"A", "B", "C"}
        assert out["operation_count"] == 0          # fresh setup has no operations
        assert out["operation_type"] == "milling"

    def test_single_body_model_count_one(self, monkeypatch):
        _install(monkeypatch, bodies=[FakeBody("Solo")])
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
