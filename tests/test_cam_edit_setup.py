"""Unit tests for ``cam_edit_setup`` — generalized editing of a CAM setup.

The adsk.cam API is mocked; what we pin is the tool's OWN logic, generalized over the broad setup
surface: setting named setup PARAMETERS by expression (the same validate-ALL-before-applying-ANY engine
as cam_edit_operation — so a typo can't half-edit), and replacing the model / fixture / stock body
COLLECTIONS (resolved strictly through the _inputs BodyRefList kind). Plus guards (no CAM, setup not
found, unknown parameter, nothing to do, a bad body ref).

Body resolution goes through _inputs (BodyRefList), which reads `_inputs._common.design()`. The tool
also builds adsk.core.ObjectCollection — both seams are patched.
"""

import json

from conftest import load_tool

ces = load_tool("cam_edit_setup")


# ── fakes: setup params + body collections ──────────────────────────────────

class _Val:
    def __init__(self, v):
        self.value = v


class _Param:
    def __init__(self, name, expr):
        self.name = name
        self.expression = expr
        self.value = _Val(expr)


class _Params:
    def __init__(self, d):
        self._d = {k: _Param(k, v) for k, v in d.items()}
    def itemByName(self, name):
        return self._d.get(name)


class _ObjColl:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x); return True
    @property
    def count(self):
        return len(self.items)
    @classmethod
    def create(cls):
        return cls()


class _Setup:
    def __init__(self, name, params):
        self.name = name
        self.parameters = _Params(params)
        self._models = _ObjColl()
        self._fixtures = _ObjColl()
        self._stock = _ObjColl()
    # models / fixtures / stockSolids are get/set ObjectCollections
    @property
    def models(self):
        return self._models
    @models.setter
    def models(self, coll):
        self._models = coll
    @property
    def fixtures(self):
        return self._fixtures
    @fixtures.setter
    def fixtures(self, coll):
        self._fixtures = coll
    @property
    def stockSolids(self):
        return self._stock
    @stockSolids.setter
    def stockSolids(self, coll):
        self._stock = coll


class _Setups:
    def __init__(self, setups):
        self._s = setups
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]


class _CAM:
    def __init__(self, setups):
        self.setups = _Setups(setups)


_DEFAULT_PARAMS = {
    "wcs_origin_boxPoint": "'top center'",
    "wcs_orientation_mode": "'modelOrientation'",
    "stockZHigh": "0.0",
}


def _install(setups=("Setup1",)):
    cam = _CAM([_Setup(n, dict(_DEFAULT_PARAMS)) for n in setups])
    ces._get_cam = lambda: (cam, None)
    ces._object_collection = _ObjColl.create
    # body resolver seam: name -> a fake body (the tool calls this instead of _inputs directly in tests)
    bodies = {"Stock": object(), "Vise": object(), "Plate": object()}
    def _resolve_bodies(names):
        out = []
        for n in names:
            if n not in bodies:
                return None, "no body '%s'" % n
            out.append(bodies[n])
        return out, None
    ces._resolve_bodies = _resolve_bodies
    cam._bodies = bodies
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_cam(self):
        ces._get_cam = lambda: (None, "no CAM data")
        res = ces.handler(setup="Setup1", parameters={"stockZHigh": "1"})
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_setup_not_found(self):
        _install(setups=("Setup1",))
        res = ces.handler(setup="Ghost", parameters={"stockZHigh": "1"})
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_nothing_to_do(self):
        _install()
        res = ces.handler(setup="Setup1")
        assert res["isError"] is True and ("parameters" in res["message"].lower()
                                           or "models" in res["message"].lower())

    def test_unknown_parameter_fails_before_applying(self):
        cam = _install()
        res = ces.handler(setup="Setup1",
                          parameters={"stockZHigh": "5", "not_a_param": "9"})
        assert res["isError"] is True and "not_a_param" in res["message"]
        # validate-all-first: the VALID one must NOT have been applied
        assert cam.setups.item(0).parameters.itemByName("stockZHigh").expression == "0.0"

    def test_bad_body_ref(self):
        _install()
        res = ces.handler(setup="Setup1", models=["NoSuchBody"])
        assert res["isError"] is True and "NoSuchBody" in res["message"]


# ── set parameters (WCS / stock / anything) ─────────────────────────────────

class TestParameters:
    def test_sets_wcs_and_stock_params(self):
        cam = _install()
        out = _payload(ces.handler(setup="Setup1", parameters={
            "wcs_origin_boxPoint": "'top center'", "stockZHigh": "2.5"}))
        sp = cam.setups.item(0).parameters
        assert sp.itemByName("stockZHigh").expression == "2.5"
        assert out["updated_count"] == 2
        # before/after captured for each
        names = {c["name"] for c in out["changed"]}
        assert names == {"wcs_origin_boxPoint", "stockZHigh"}

    def test_parameters_accept_string_form(self):
        cam = _install()
        _payload(ces.handler(setup="Setup1", parameters="stockZHigh=3, wcs_orientation_mode='axesXZ'"))
        sp = cam.setups.item(0).parameters
        assert sp.itemByName("stockZHigh").expression == "3"
        assert sp.itemByName("wcs_orientation_mode").expression == "'axesXZ'"


# ── set body collections (models / fixtures / stock) ────────────────────────

class TestBodies:
    def test_sets_models(self):
        cam = _install()
        out = _payload(ces.handler(setup="Setup1", models=["Stock"]))
        assert cam.setups.item(0).models.count == 1
        assert out["models_set"] == 1

    def test_sets_fixtures_and_stock(self):
        cam = _install()
        out = _payload(ces.handler(setup="Setup1", fixtures=["Vise"], stock=["Plate"]))
        assert cam.setups.item(0).fixtures.count == 1
        assert cam.setups.item(0).stockSolids.count == 1
        assert out["fixtures_set"] == 1 and out["stock_set"] == 1

    def test_params_and_bodies_together(self):
        cam = _install()
        out = _payload(ces.handler(setup="Setup1",
                                   parameters={"stockZHigh": "1"}, models=["Stock"]))
        assert out["updated_count"] == 1 and out["models_set"] == 1
