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


class _CadVal:
    """A CadObjectParameterValue fake: .value is a list of bound entities, mutated in place."""
    def __init__(self):
        self.value = []


class _Param:
    def __init__(self, name, expr, cad=False):
        self.name = name
        self.expression = expr
        # a WCS geometry param carries a CadObjectParameterValue; a plain one an expression value.
        self.value = _CadVal() if cad else _Val(expr)


class _Params:
    def __init__(self, d, cad_params=()):
        self._d = {k: _Param(k, v) for k, v in d.items()}
        for k in cad_params:
            self._d[k] = _Param(k, "", cad=True)
        # choice-mode params the WCS bind sets by expression
        for k in ("wcs_origin_mode", "wcs_orientation_mode"):
            self._d.setdefault(k, _Param(k, "'stockPoint'"))
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


class _Machine:
    def __init__(self, description):
        self.description = description
        self.vendor = ""
        self.model = ""


class _Setup:
    # WCS geometry params carry a CadObjectParameterValue (mutated in place); the rest are expressions.
    _CAD_PARAMS = ("wcs_origin_point", "wcs_orientation_axisZ", "wcs_orientation_axisX")

    def __init__(self, name, params, machine_sticks=True, require_enable=True):
        self.name = name
        self.parameters = _Params(params, cad_params=self._CAD_PARAMS)
        self._models = _ObjColl()
        self._fixtures = _ObjColl()
        self._stock = _ObjColl()
        self._machine = None
        self.machine_sticks = machine_sticks   # False models an assignment that silently doesn't take
        # Fusion refuses stockSolids unless stockMode==SolidStock, and fixtures unless fixtureEnabled.
        self.require_enable = require_enable
        self.stockMode = 1                     # RelativeBoxStock (matches the live default)
        self.fixtureEnabled = False
    # Setup.machine takes a transient copy; the tool reads it back to confirm.
    @property
    def machine(self):
        return self._machine
    @machine.setter
    def machine(self, m):
        if self.machine_sticks:
            self._machine = m
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
        if self.require_enable and not self.fixtureEnabled:
            raise RuntimeError("fixtures need fixtureEnabled set first")
        self._fixtures = coll
    @property
    def stockSolids(self):
        return self._stock
    @stockSolids.setter
    def stockSolids(self, coll):
        # SolidStock == 6 in adsk.cam.SetupStockModes
        if self.require_enable and self.stockMode != 6:
            raise RuntimeError("stockSolids need stockMode=SolidStock")
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


def _install(monkeypatch, setups=("Setup1",), require_enable=True):
    cam = _CAM([_Setup(n, dict(_DEFAULT_PARAMS), require_enable=require_enable) for n in setups])
    monkeypatch.setattr(ces, "get_cam", lambda: (cam, None))
    ces._object_collection = _ObjColl.create
    # the SolidStock enum member the handler reads must equal 6 (the live value) for the fake's
    # stockMode gate to accept the switch.
    monkeypatch.setattr(ces.adsk.cam.SetupStockModes, "SolidStock", 6, raising=False)
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
    # machine resolver seam: a known 'vendor|model' -> a fake Machine, anything else -> a refusal.
    known = {"Haas|VF-2": _Machine("Haas VF-2")}
    def _resolve_machine(name):
        m = known.get(name)
        if not m:
            return None, None, "no machine '%s'" % name
        return m, m.description, None
    ces._resolve_machine = _resolve_machine
    cam._machines = known
    # WCS handle resolver seam: a known handle string -> a fake entity; unknown -> a refusal. The
    # apply logic (mode-set + in-place bind + read-back) still runs against the fake setup params.
    entities = {"vtx-1": object(), "face-Z": object(), "edge-X": object()}
    def _resolve_wcs(wcs):
        if not isinstance(wcs, dict):
            return None, "wcs must be an object"
        out = {}
        for k, h in wcs.items():
            if k not in ces._WCS_BINDINGS:
                return None, "unknown wcs key '%s'" % k
            if h in (None, "", []):
                continue
            if h not in entities:
                return None, "wcs.%s: no entity '%s'" % (k, h)
            out[k] = entities[h]
        return out, None
    ces._resolve_wcs = _resolve_wcs
    cam._wcs_entities = entities
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_cam(self, monkeypatch):
        monkeypatch.setattr(ces, "get_cam", lambda: (None, "no CAM data"))
        res = ces.handler(setup="Setup1", parameters={"stockZHigh": "1"})
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_setup_not_found(self, monkeypatch):
        _install(monkeypatch, setups=("Setup1",))
        res = ces.handler(setup="Ghost", parameters={"stockZHigh": "1"})
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_nothing_to_do(self, monkeypatch):
        _install(monkeypatch)
        res = ces.handler(setup="Setup1")
        assert res["isError"] is True and ("parameters" in res["message"].lower()
                                           or "models" in res["message"].lower())

    def test_unknown_parameter_fails_before_applying(self, monkeypatch):
        cam = _install(monkeypatch)
        res = ces.handler(setup="Setup1",
                          parameters={"stockZHigh": "5", "not_a_param": "9"})
        assert res["isError"] is True and "not_a_param" in res["message"]
        # validate-all-first: the VALID one must NOT have been applied
        assert cam.setups.item(0).parameters.itemByName("stockZHigh").expression == "0.0"

    def test_bad_body_ref(self, monkeypatch):
        _install(monkeypatch)
        res = ces.handler(setup="Setup1", models=["NoSuchBody"])
        assert res["isError"] is True and "NoSuchBody" in res["message"]


# ── set parameters (WCS / stock / anything) ─────────────────────────────────

class TestParameters:
    def test_sets_wcs_and_stock_params(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", parameters={
            "wcs_origin_boxPoint": "'top center'", "stockZHigh": "2.5"}))
        sp = cam.setups.item(0).parameters
        assert sp.itemByName("stockZHigh").expression == "2.5"
        assert out["updated_count"] == 2
        # before/after captured for each
        names = {c["name"] for c in out["changed"]}
        assert names == {"wcs_origin_boxPoint", "stockZHigh"}

    def test_parameters_accept_string_form(self, monkeypatch):
        cam = _install(monkeypatch)
        _payload(ces.handler(setup="Setup1", parameters="stockZHigh=3, wcs_orientation_mode='axesXZ'"))
        sp = cam.setups.item(0).parameters
        assert sp.itemByName("stockZHigh").expression == "3"
        assert sp.itemByName("wcs_orientation_mode").expression == "'axesXZ'"


# ── set body collections (models / fixtures / stock) ────────────────────────

class TestBodies:
    def test_sets_models(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", models=["Stock"]))
        assert cam.setups.item(0).models.count == 1
        assert out["models_set"] == 1

    def test_sets_fixtures_and_stock(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", fixtures=["Vise"], stock=["Plate"]))
        assert cam.setups.item(0).fixtures.count == 1
        assert cam.setups.item(0).stockSolids.count == 1
        assert out["fixtures_set"] == 1 and out["stock_set"] == 1

    def test_stock_switches_mode_to_solid_before_assigning(self, monkeypatch):
        # The prerequisite: stockSolids is refused unless stockMode==SolidStock. The handler must set
        # the mode FIRST. Without that line the fake's setter raises and this goes red.
        cam = _install(monkeypatch)
        _payload(ces.handler(setup="Setup1", stock=["Plate"]))
        assert cam.setups.item(0).stockMode == 6      # SolidStock

    def test_fixtures_are_enabled_before_assigning(self, monkeypatch):
        # The prerequisite: fixtures is refused unless fixtureEnabled. The handler must enable FIRST.
        cam = _install(monkeypatch)
        _payload(ces.handler(setup="Setup1", fixtures=["Vise"]))
        assert cam.setups.item(0).fixtureEnabled is True

    def test_params_and_bodies_together(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1",
                                   parameters={"stockZHigh": "1"}, models=["Stock"]))
        assert out["updated_count"] == 1 and out["models_set"] == 1


# ── assign a machine (the setup-level prerequisite for posting) ─────────────

class TestMachine:
    def test_assigns_machine_and_reads_it_back(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", machine="Haas|VF-2"))
        assert out["machine_set"] == "Haas VF-2"
        assert cam.setups.item(0).machine.description == "Haas VF-2"

    def test_unknown_machine_is_error(self, monkeypatch):
        _install(monkeypatch)
        res = ces.handler(setup="Setup1", machine="Acme|Nonesuch")
        assert res["isError"] is True and "Nonesuch" in res["message"]

    def test_assignment_that_does_not_take_is_error(self, monkeypatch):
        # Setup.machine setter silently drops the value -> the read-back must turn that into a hard error,
        # never a false ok.
        cam = _CAM([_Setup("Setup1", dict(_DEFAULT_PARAMS), machine_sticks=False)])
        monkeypatch.setattr(ces, "get_cam", lambda: (cam, None))
        ces._resolve_machine = lambda name: (_Machine("Haas VF-2"), "Haas VF-2", None)
        res = ces.handler(setup="Setup1", machine="Haas|VF-2")
        assert res["isError"] is True and "did not take" in res["message"].lower()

    def test_machine_counts_as_something_to_do(self, monkeypatch):
        _install(monkeypatch)
        # machine-only edit is NOT 'nothing to do'
        out = _payload(ces.handler(setup="Setup1", machine="Haas|VF-2"))
        assert out["machine_set"] == "Haas VF-2"


# ── bind the WCS to geometry (the associative, from-selection WCS) ──────────

class TestWCS:
    def test_binds_origin_to_geometry_and_sets_mode(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", wcs={"origin": "vtx-1"}))
        sp = cam.setups.item(0).parameters
        # the mode choice flipped to 'point' and the cad param carries exactly one entity
        assert sp.itemByName("wcs_origin_mode").expression == "'point'"
        assert len(sp.itemByName("wcs_origin_point").value.value) == 1
        assert out["wcs_set"]["origin"]["bound_entities"] == 1

    def test_binds_axes_for_orientation(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", wcs={"z_axis": "face-Z", "x_axis": "edge-X"}))
        sp = cam.setups.item(0).parameters
        assert sp.itemByName("wcs_orientation_mode").expression == "'axesZX'"
        assert len(sp.itemByName("wcs_orientation_axisZ").value.value) == 1
        assert len(sp.itemByName("wcs_orientation_axisX").value.value) == 1
        assert set(out["wcs_set"]) == {"z_axis", "x_axis"}

    def test_unknown_wcs_key_is_error(self, monkeypatch):
        _install(monkeypatch)
        res = ces.handler(setup="Setup1", wcs={"origin": "vtx-1", "bogus": "vtx-1"})
        assert res["isError"] is True and "bogus" in res["message"]

    def test_bad_wcs_handle_is_error(self, monkeypatch):
        _install(monkeypatch)
        res = ces.handler(setup="Setup1", wcs={"origin": "not-a-handle"})
        assert res["isError"] is True and "not-a-handle" in res["message"]

    def test_bind_that_reads_back_empty_is_error(self, monkeypatch):
        # A CadObjectParameterValue whose .value stays empty after the set is a swallowed no-op: a
        # geometry-bound WCS with no geometry. The read-back must make that a hard error.
        cam = _install(monkeypatch)
        class _StuckCad:
            value = []
            def __setattr__(self, k, v):
                pass                          # silently drops the bind
        cam.setups.item(0).parameters._d["wcs_origin_point"].value = _StuckCad()
        res = ces.handler(setup="Setup1", wcs={"origin": "vtx-1"})
        assert res["isError"] is True and "no geometry" in res["message"].lower()

    def test_wcs_counts_as_something_to_do(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", wcs={"origin": "vtx-1"}))
        assert "wcs_set" in out
