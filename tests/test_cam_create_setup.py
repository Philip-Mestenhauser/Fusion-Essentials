"""Unit tests for ``cam_create_setup.py`` — create a CAM (Manufacture) setup on a part.

This closes the gap where CAM authoring tools all assumed a setup already existed: a freshly
imported bare part had no tool-only path to a CAM job. Covers operation-type dispatch
(milling/turning), model selection (handles / names / all-bodies default), naming, and the
no-design / no-bodies guards. No live Fusion — fakes mimic adsk.cam.CAM.setups.
"""

import json
from conftest import load_tool

cs = load_tool("cam_create_setup")


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeBody:
    def __init__(self, name):
        self.name = name


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


def _install(bodies=None, has_cam=True):
    bodies = bodies if bodies is not None else [FakeBody("Body1")]
    comp = FakeComp(bodies)
    design = FakeDesign(comp)
    cam = FakeCAM() if has_cam else None

    import adsk.cam, adsk.fusion
    for n in ("MillingOperation", "TurningOperation"):
        setattr(adsk.cam.OperationTypes, n, n)
    adsk.fusion.BRepBody = FakeBody
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None

    # the tool reads CAM via _get_cam (active doc products) and design via _design
    cs._get_cam = lambda: ((cam, None) if cam else (None, "no CAM"))
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
    def test_default_is_milling(self):
        _, cam, _ = _install()
        out = _payload(cs.handler())
        assert cam.setups.added[-1].operationType == "MillingOperation"
        assert out["created"] is True

    def test_turning(self):
        _, cam, _ = _install()
        _payload(cs.handler(operation_type="turning"))
        assert cam.setups.added[-1].operationType == "TurningOperation"

    def test_unknown_type_errors(self):
        _install()
        res = cs.handler(operation_type="welding")
        assert res["isError"] is True and "operation_type" in res["message"]


# ── model selection ──────────────────────────────────────────────────────────

class TestModelSelection:
    def test_all_root_bodies_when_omitted(self):
        _, cam, _ = _install(bodies=[FakeBody("A"), FakeBody("B")])
        _payload(cs.handler())
        models = cam.setups.added[-1].models
        assert {m.name for m in models} == {"A", "B"}

    def test_named_body(self):
        _, cam, _ = _install(bodies=[FakeBody("Widget"), FakeBody("Other")])
        _payload(cs.handler(models="Widget"))
        models = cam.setups.added[-1].models
        assert [m.name for m in models] == ["Widget"]

    def test_body_by_handle(self):
        design, cam, _ = _install(bodies=[FakeBody("Body1")])
        h = "/v" + "Z" * 70
        design._tokens[h] = FakeBody("FromHandle")
        _payload(cs.handler(models=h))
        assert cam.setups.added[-1].models[0].name == "FromHandle"

    def test_missing_named_model_errors(self):
        _install(bodies=[FakeBody("Body1")])
        res = cs.handler(models="Nope")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_no_bodies_at_all_errors(self):
        _install(bodies=[])
        res = cs.handler()
        assert res["isError"] is True and "body" in res["message"].lower()


# ── naming + guards ──────────────────────────────────────────────────────────

class TestNamingAndGuards:
    def test_custom_name(self):
        _, cam, _ = _install()
        out = _payload(cs.handler(name="Op10 Mill"))
        assert cam.setups.added[-1].name == "Op10 Mill"
        assert out["setup_name"] == "Op10 Mill"

    def test_no_cam_product_errors(self):
        _install(has_cam=False)
        res = cs.handler()
        assert res["isError"] is True and "CAM" in res["message"]
