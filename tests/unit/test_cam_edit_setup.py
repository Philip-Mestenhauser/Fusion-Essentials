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
        self.warning = ""

    @property
    def error(self):
        # Live signal: an expression referencing a missing parameter is STORED verbatim but its .error
        # reads 'Failed to evaluate expression.' (its .value.value even comes back a finite 0.0, so only
        # .error reveals the failure). Model that by keying off a missing-reference marker.
        if isinstance(self.expression, str) and "NoSuch" in self.expression:
            return "Failed to evaluate expression."
        return ""


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
    monkeypatch.setattr(ces, "_object_collection", _ObjColl.create)
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
    monkeypatch.setattr(ces, "_resolve_bodies", _resolve_bodies)
    cam._bodies = bodies
    # machine resolver seam: a known 'vendor|model' -> a fake Machine, anything else -> a refusal.
    # (Patched so it auto-restores - TestMachineResolver exercises the REAL resolve_machine.)
    known = {"Haas|VF-2": _Machine("Haas VF-2")}
    def resolve_machine(name):
        m = known.get(name)
        if not m:
            return None, None, "no machine '%s'" % name
        return m, m.description, None
    monkeypatch.setattr(ces, "resolve_machine", resolve_machine)
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
    monkeypatch.setattr(ces, "_resolve_wcs", _resolve_wcs)
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


class TestParameterEvaluation:
    """A stock/setup expression that fails to EVALUATE (a typo'd parameter reference) is stored verbatim
    by the platform and reported as success - only .error reveals it. The tool re-reads .error post-set,
    rolls back, and fails. A .warning is NOT an evaluation failure and never gates."""

    def test_unevaluated_expression_rolls_back_and_errors(self, monkeypatch):
        cam = _install(monkeypatch)
        res = ces.handler(setup="Setup1", parameters={"stockZHigh": "NoSuchParamXyz * 2"})
        assert res["isError"] is True
        assert "did not evaluate" in res["message"] and "NoSuchParamXyz" in res["message"]
        # NOT left storing the broken text: rolled back to the prior expression
        assert cam.setups.item(0).parameters.itemByName("stockZHigh").expression == "0.0"

    def test_rollback_is_all_or_nothing(self, monkeypatch):
        # a VALID param set alongside a broken one is ALSO rolled back (params apply before bodies/wcs)
        cam = _install(monkeypatch)
        res = ces.handler(setup="Setup1", parameters={
            "wcs_origin_boxPoint": "'top left'", "stockZHigh": "NoSuchParamXyz"})
        assert res["isError"] is True
        sp = cam.setups.item(0).parameters
        assert sp.itemByName("wcs_origin_boxPoint").expression == "'top center'"   # rolled back
        assert sp.itemByName("stockZHigh").expression == "0.0"                     # rolled back

    def test_valid_expression_still_passes_and_surfaces_warning(self, monkeypatch):
        # a platform warning ('stock less than model width') fires on VALID expressions - it must NOT
        # gate, but it IS surfaced on the changed record.
        cam = _install(monkeypatch)
        cam.setups.item(0).parameters.itemByName("stockZHigh").warning = "stock less than model width"
        out = _payload(ces.handler(setup="Setup1", parameters={"stockZHigh": "2.5"}))
        assert out["updated_count"] == 1
        rec = out["changed"][0]
        assert rec["after"] == "2.5" and rec["warning"] == "stock less than model width"


class _StuckSetupParam(_Param):
    """Accepts an expression assignment and keeps the one it already holds - the swallowed write the
    platform reports as success, with a clean .error channel."""
    def __setattr__(self, key, value):
        if key == "expression" and "expression" in self.__dict__:
            return
        object.__setattr__(self, key, value)


class _ThirdValueSetupParam(_Param):
    """Takes the assignment and stores a value of its OWN - neither the expression it held nor the
    one written. A gate keyed on 'it kept the prior expression' passes this one."""
    def __setattr__(self, key, value):
        if key == "expression" and "expression" in self.__dict__:
            object.__setattr__(self, key, "999")
            return
        object.__setattr__(self, key, value)


class _QuotedStoreSetupParam(_Param):
    """Takes the assignment and stores the SINGLE-QUOTED form of it - the shape a CAM string
    parameter's stored expression carries (receipt cam-parameter-expressions, whose probe reads
    'context' and 'strategy' back starting with a quote). A caller sends either spelling."""
    def __setattr__(self, key, value):
        if key == "expression" and "expression" in self.__dict__:
            object.__setattr__(self, key, "'" + str(value) + "'")
            return
        object.__setattr__(self, key, value)


class _UnreadableSetupParam(_Param):
    """Reads its prior expression, takes the assignment, and will not read one back afterwards -
    the write is neither a landed change nor a no-take, because nothing answered either way."""
    def __init__(self, name, expr):
        object.__setattr__(self, "_writes", 0)
        super().__init__(name, expr)

    @property
    def expression(self):
        if self._writes > 1:
            raise RuntimeError("expression is unreadable")
        return self._expr

    @expression.setter
    def expression(self, v):
        object.__setattr__(self, "_expr", v)
        object.__setattr__(self, "_writes", self._writes + 1)


class TestParameterNoTake:
    """The evaluation channel is one swallowed write; a parameter whose read-back is not the
    expression written is the other, and .error says nothing about it. Publishing before beside
    after is not enough on its own - updated_count with an unchanged 'after' reads as a success."""

    def test_a_parameter_that_keeps_its_prior_expression_is_an_error(self, monkeypatch):
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["stockZHigh"] = _StuckSetupParam("stockZHigh", "0.0")
        res = ces.handler(setup="Setup1", parameters={"stockZHigh": "2.5"})
        assert res["isError"] is True
        assert "the assignment did not take" in res["message"]
        assert "it reads back '0.0'" in res["message"]
        # and it is NOT reported as the other failure - .error said nothing here
        assert "did not evaluate" not in res["message"]

    def test_a_parameter_that_lands_as_a_THIRD_value_is_an_error_naming_it(self, monkeypatch):
        # the gate is "the read-back is the expression written" (live-verified receipt
        # cam-parameter-expressions), so a store that keeps neither the prior expression nor the
        # request is caught too - and the message states the value it actually reads.
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["stockZHigh"] = _ThirdValueSetupParam("stockZHigh", "0.0")
        res = ces.handler(setup="Setup1", parameters={"stockZHigh": "2.5"})
        assert res["isError"] is True
        assert "the assignment did not take" in res["message"]
        assert "it reads back '999'" in res["message"]

    def test_a_parameter_that_cannot_be_read_back_is_unconfirmed_not_edited(self, monkeypatch):
        # nothing read after the write, so nothing was observed to report - and 'edited: true' with
        # an after of null is the swallowed mutation this arm exists to refuse.
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["stockZHigh"] = _UnreadableSetupParam("stockZHigh", "0.0")
        res = ces.handler(setup="Setup1", parameters={"stockZHigh": "2.5"})
        assert res["isError"] is True
        assert "UNCONFIRMED" in res["message"] and "stockZHigh" in res["message"]
        # and NOT worded as a no-take: no expression was read to say what it still holds
        assert "reads back" not in res["message"]

    def test_an_unreadable_parameter_rolls_the_siblings_back_too(self, monkeypatch):
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["stockZHigh"] = _UnreadableSetupParam("stockZHigh", "0.0")
        res = ces.handler(setup="Setup1", parameters={
            "wcs_origin_boxPoint": "'top left'", "stockZHigh": "2.5"})
        assert res["isError"] is True and "UNCONFIRMED" in res["message"]
        assert sp.itemByName("wcs_origin_boxPoint").expression == "'top center'"   # rolled back

    def test_a_no_take_rolls_the_sibling_parameters_back_too(self, monkeypatch):
        # params apply before bodies/machine/wcs, so the whole call can still leave the setup as
        # found - a valid write beside a swallowed one must not be left standing.
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["stockZHigh"] = _StuckSetupParam("stockZHigh", "0.0")
        res = ces.handler(setup="Setup1", parameters={
            "wcs_origin_boxPoint": "'top left'", "stockZHigh": "2.5"})
        assert res["isError"] is True
        assert sp.itemByName("wcs_origin_boxPoint").expression == "'top center'"   # rolled back
        assert "Rolled back all 2 parameter(s)" in res["message"]

    def test_a_store_that_quotes_the_request_is_not_a_no_take(self, monkeypatch):
        # What the receipt measured is two-sided: a numeric parameter's expression reads back the
        # text written, and a STRING parameter's stored expression is single-quoted.
        # wcs_origin_boxPoint is a string parameter and a caller may send its value unquoted, so the
        # compare goes through the shared codec - a byte compare would roll this whole call back and
        # call a landed write a no-take.
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["wcs_origin_boxPoint"] = _QuotedStoreSetupParam("wcs_origin_boxPoint", "'top center'")
        out = _payload(ces.handler(setup="Setup1",
                                   parameters={"wcs_origin_boxPoint": "top left"}))
        assert out["updated_count"] == 1
        # 'changed' publishes the parameter's OWN read-back, wrapper and all
        assert out["changed"][0]["after"] == "'top left'"

    def test_a_quoting_store_that_keeps_its_prior_expression_is_still_an_error(self, monkeypatch):
        # The codec strips the wrapper, not the comparison: a store that quotes AND keeps what it
        # already held is the swallowed write, and it stays convicted.
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["wcs_origin_boxPoint"] = _StuckSetupParam("wcs_origin_boxPoint", "'top center'")
        res = ces.handler(setup="Setup1", parameters={"wcs_origin_boxPoint": "top left"})
        assert res["isError"] is True
        assert "the assignment did not take" in res["message"] and "top center" in res["message"]

    def test_setting_a_parameter_to_the_value_it_already_reads_is_not_a_no_take(self, monkeypatch):
        # the gate is "the read-back is the expression written": a caller re-asserting the value the
        # setup already carries reads it back, which is the state they asked for.
        cam = _install(monkeypatch)
        sp = cam.setups.item(0).parameters
        sp._d["stockZHigh"] = _StuckSetupParam("stockZHigh", "0.0")
        out = _payload(ces.handler(setup="Setup1", parameters={"stockZHigh": "0.0"}))
        assert out["updated_count"] == 1 and out["changed"][0]["after"] == "0.0"


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
        monkeypatch.setattr(ces, "resolve_machine",
                            lambda name: (_Machine("Haas VF-2"), "Haas VF-2", None))
        res = ces.handler(setup="Setup1", machine="Haas|VF-2")
        assert res["isError"] is True and "did not take" in res["message"].lower()

    def test_machine_counts_as_something_to_do(self, monkeypatch):
        _install(monkeypatch)
        # machine-only edit is NOT 'nothing to do'
        out = _payload(ces.handler(setup="Setup1", machine="Haas|VF-2"))
        assert out["machine_set"] == "Haas VF-2"


# ── bind the WCS to geometry (the associative, from-selection WCS) ──────────

# ── machine RESOLUTION: exact-match-first beats a shared prefix (the real resolve_machine) ─────────
#
# The tests above patch the resolve_machine seam; these exercise the REAL resolver against a
# SimpleNamespace machine library whose query PREFIX-matches the model (the live behaviour: 'VF-2'
# returns the plain machine AND its 'VF-2 with TRT100/160' variants).

from types import SimpleNamespace


def _machine(vendor, model, description=None):
    return SimpleNamespace(vendor=vendor, model=model, description=description)


def _machine_lib(machines, f360=()):
    """Fake MachineLibrary: a Local pool + a Fusion360 pool behind createQuery."""
    local_pool, f360_pool = list(machines), list(f360)
    def create_query(loc, vendor, model):
        pool = local_pool if loc == "LOCAL" else (f360_pool if loc == "F360" else [])
        matched = [m for m in pool
                   if (not vendor or (m.vendor or "").lower() == vendor.lower())
                   and (not model or (m.model or "").lower().startswith(model.lower()))]
        return SimpleNamespace(execute=lambda: matched)
    return SimpleNamespace(createQuery=create_query, _local_pool=local_pool)


def _install_machine_lib(monkeypatch, machines, f360=()):
    lib = _machine_lib(machines, f360)
    holder = SimpleNamespace(libraryManager=SimpleNamespace(machineLibrary=lib))
    monkeypatch.setattr(ces.adsk.cam.CAMManager, "get", staticmethod(lambda: holder), raising=False)
    monkeypatch.setattr(ces.adsk.cam.LibraryLocations, "LocalLibraryLocation", "LOCAL", raising=False)
    monkeypatch.setattr(ces.adsk.cam.LibraryLocations, "Fusion360LibraryLocation", "F360", raising=False)
    return lib


_VF2_FAMILY = [
    _machine("Haas", "VF-2"),
    _machine("Haas", "VF-2 with TRT100"),
    _machine("Haas", "VF-2 with TRT160"),
]


class TestMachineResolver:
    def test_exact_model_beats_prefix_siblings(self, monkeypatch):
        # 'Haas|VF-2' prefix-matches all three; exact-match-first must select the model that is
        # exactly 'VF-2', NOT refuse as ambiguous. (Flip the exact pass off and this goes red.)
        _install_machine_lib(monkeypatch, _VF2_FAMILY)
        m, label, err = ces.resolve_machine("Haas|VF-2")
        assert err is None
        assert m.model == "VF-2" and label == "Haas VF-2"

    def test_bare_label_is_selectable(self, monkeypatch):
        # The name an agent SEES is the label 'Haas VF-2'; passed bare it must resolve (label-recovery
        # re-splits it to vendor|model), not miss because no model literally starts with 'Haas VF-2'.
        _install_machine_lib(monkeypatch, _VF2_FAMILY)
        m, label, err = ces.resolve_machine("Haas VF-2")
        assert err is None and m.model == "VF-2"

    def test_doubled_vendor_label_is_selectable(self, monkeypatch):
        # 'Haas|Haas VF-2' (vendor accidentally repeated in the model) resolves via the same recovery.
        _install_machine_lib(monkeypatch, _VF2_FAMILY)
        m, label, err = ces.resolve_machine("Haas|Haas VF-2")
        assert err is None and m.model == "VF-2"

    def test_no_exact_is_still_ambiguous(self, monkeypatch):
        # A prefix that matches several with NO exact winner stays refused, listing the distinct LABELS.
        _install_machine_lib(monkeypatch, _VF2_FAMILY)
        m, label, err = ces.resolve_machine("Haas|VF")
        assert m is None and "Ambiguous" in err
        assert "Haas VF-2 with TRT100" in err and "exact names" in err

    def test_same_model_variants_select_by_description(self, monkeypatch):
        # The LIVE shape: three machines share vendor|model 'HAAS|VF-2' and differ ONLY by description.
        # The exact full DESCRIPTION selects one; the bare vendor|model stays ambiguous, listing them.
        fam = [_machine("Haas", "VF-2", "Haas VF-2"),
               _machine("Haas", "VF-2", "Haas VF-2 with TRT100"),
               _machine("Haas", "VF-2", "Haas VF-2 with TRT160")]
        _install_machine_lib(monkeypatch, fam)
        m, label, err = ces.resolve_machine("Haas VF-2 with TRT100")
        assert err is None and label == "Haas VF-2 with TRT100"       # the exact variant, not the base
        # the base description also resolves to the base, not a TRT variant
        m2, label2, err2 = ces.resolve_machine("Haas VF-2")
        assert err2 is None and label2 == "Haas VF-2"
        # vendor|model alone can't pick one -> ambiguous, listing the three descriptions
        m3, _l3, err3 = ces.resolve_machine("Haas|VF-2")
        assert m3 is None and "Haas VF-2 with TRT160" in err3

    def test_unique_prefix_resolves(self, monkeypatch):
        # A single match needs no exact tie-break.
        _install_machine_lib(monkeypatch, [_machine("Tormach", "1100MX")])
        m, label, err = ces.resolve_machine("Tormach|1100")
        assert err is None and m.model == "1100MX"

    def test_no_match_names_the_input(self, monkeypatch):
        _install_machine_lib(monkeypatch, _VF2_FAMILY)
        m, label, err = ces.resolve_machine("Okuma|Genos")
        assert m is None and "No machine matches" in err


# ── the machine CATALOG read (cam_get include=['machines'] delegates here) ──────────────────────────

class TestReadMachines:
    def test_lists_both_locations_with_sim_flag(self, monkeypatch):
        sim = _machine("Haas", "VF-3", "Haas VF-3")
        sim.hasSimulationModel = True
        _install_machine_lib(monkeypatch, [_machine("Haas", "VF-2", "Haas VF-2 (local)")], [sim])
        out = _payload(ces.read_machines())
        assert out["count"] == 2 and out["truncated"] is False
        by_name = {r["name"]: r for r in out["machines"]}
        assert by_name["Haas VF-2 (local)"]["location"] == "local"
        assert by_name["Haas VF-3"]["location"] == "fusion360"
        assert by_name["Haas VF-3"]["simulation_ready"] is True
        assert by_name["Haas VF-2 (local)"]["simulation_ready"] is False

    def test_vendor_filter(self, monkeypatch):
        _install_machine_lib(monkeypatch, [_machine("Haas", "VF-2"), _machine("DMG", "DMU 50")])
        out = _payload(ces.read_machines(vendor="Haas"))
        assert out["count"] == 1 and out["machines"][0]["vendor"] == "Haas"

    def test_truncation_reports(self, monkeypatch):
        _install_machine_lib(monkeypatch, [_machine("Haas", "VF-%d" % i) for i in range(3)])
        out = _payload(ces.read_machines(max_results=2))
        assert out["count"] == 2 and out["truncated"] is True

    def test_machine_type_filters_by_capability_and_rows_carry_kind(self, monkeypatch):
        # The bundled library is mostly additive printers; machine_type='milling' must keep only
        # capability-flagged mills, and filtered-out rows must not count toward truncation.
        mill = _machine("Haas", "VF-2", "Haas VF-2")
        mill.capabilities = SimpleNamespace(isMillingSupported=True)
        printers = [_machine("Anet", "A%d" % i) for i in range(5)]
        for p in printers:
            p.capabilities = SimpleNamespace(isAdditiveSupported=True)
        _install_machine_lib(monkeypatch, [], [mill] + printers)
        out = _payload(ces.read_machines(machine_type="milling", max_results=2))
        assert out["count"] == 1 and out["truncated"] is False
        assert out["machines"][0]["name"] == "Haas VF-2"
        assert out["machines"][0]["kind"] == ["milling"]

    def test_unknown_machine_type_is_error(self, monkeypatch):
        _install_machine_lib(monkeypatch, [])
        res = ces.read_machines(machine_type="waterjet")
        assert res["isError"] is True and "waterjet" in res["message"]
        assert "milling" in res["message"]      # the refusal names the valid vocabulary

    def test_library_unavailable_is_error(self, monkeypatch):
        def _boom():
            raise RuntimeError("no library manager")
        monkeypatch.setattr(ces.adsk.cam.CAMManager, "get", staticmethod(_boom), raising=False)
        res = ces.read_machines()
        assert res["isError"] is True and "machine library" in res["message"]

    def test_note_carries_the_measured_strip_clause(self, monkeypatch):
        # The note's strip advice states what the strip is MEASURED to preserve - the spindle
        # maximum and axis ranges reading back through Setup.machine. The two absent phrases are
        # the blanket claims no measurement backs: that the API refuses EVERY simulation_ready
        # machine, and that posting/kinematics are unaffected.
        _install_machine_lib(monkeypatch, [_machine("Haas", "VF-2")])
        note = _payload(ces.read_machines())["note"]
        assert "read back unchanged through Setup.machine" in note
        assert "refuses assigning any" not in note
        assert "posting/kinematics" not in note


# ── machine_strip_simulation: the ONE assignment path for simulation-ready machines ─────────────────
#
# Setup.machine REFUSES a machine whose hasSimulationModel is True - measured (measure_api
# cam-machine-uncleared-simulation-assignment-refused) on the library machine that run picks, and
# only when that machine carries a simulation model. Stripping the simulation model from the
# TRANSIENT resolved copy is what unlocks the assignment, with the spindle maximum and every axis
# range reading back identically through Setup.machine (measure_api
# cam-machine-spindle-max-readable). Whether EVERY simulation-ready machine is refused, and whether
# the platform error's copy-to-local advice works through the API, are NOT measured.

class TestMachineStripSimulation:
    def _setup_with_sim_machine(self, monkeypatch):
        cam = _CAM([_Setup("Setup1", dict(_DEFAULT_PARAMS))])
        monkeypatch.setattr(ces, "get_cam", lambda: (cam, None))
        monkeypatch.setattr(ces, "_object_collection", _ObjColl.create)
        src = _machine("Haas", "VF-2", "Haas VF-2")
        src.hasSimulationModel = True
        src.clearSimulationModel = lambda: setattr(src, "hasSimulationModel", False)
        monkeypatch.setattr(ces, "resolve_machine", lambda name: (src, "Haas VF-2", None))
        return cam, src

    def test_strip_then_assign_reports_both(self, monkeypatch):
        cam, src = self._setup_with_sim_machine(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", machine="Haas VF-2",
                                   machine_strip_simulation=True))
        assert out["machine_simulation_stripped"] is True
        assert out["machine_set"] == "Haas VF-2"
        assert src.hasSimulationModel is False           # the strip actually ran on the copy
        assert cam.setups.item(0).machine is src

    def test_strip_failure_is_error(self, monkeypatch):
        cam, src = self._setup_with_sim_machine(monkeypatch)
        def _boom():
            raise RuntimeError("no simulation model")
        src.clearSimulationModel = _boom
        res = ces.handler(setup="Setup1", machine="Haas VF-2", machine_strip_simulation=True)
        assert res["isError"] is True and "simulation model" in res["message"]

    def test_no_strip_without_the_flag(self, monkeypatch):
        cam, src = self._setup_with_sim_machine(monkeypatch)
        out = _payload(ces.handler(setup="Setup1", machine="Haas VF-2"))
        assert "machine_simulation_stripped" not in out
        assert src.hasSimulationModel is True            # never silently stripped

    def test_sim_ready_refusal_error_names_the_flag(self, monkeypatch):
        # Without the flag, the platform's refusal must TEACH the working escape at failure time -
        # THIS refusal plus the remedy, without generalizing to every simulation-ready machine or
        # claiming what the copy-to-local advice does.
        cam, src = self._setup_with_sim_machine(monkeypatch)
        def _refuse(self, m):
            raise RuntimeError("Setting a simulation ready machine from an external library is "
                               "currently not supported. Try copying this machine to your local "
                               "library.")
        monkeypatch.setattr(_Setup, "machine", property(lambda self: None, _refuse))
        res = ces.handler(setup="Setup1", machine="Haas VF-2")
        assert res["isError"] is True and "machine_strip_simulation=true" in res["message"]
        # the hint carries what the strip is MEASURED to preserve - the spindle maximum and axis
        # ranges reading back through Setup.machine - and no blanket claim about every machine.
        assert "read back unchanged through Setup.machine" in res["message"]
        assert "ANY simulation-ready machine" not in res["message"]

    def test_flag_description_carries_the_measured_clause(self):
        # The input's own prose says the same measured thing as the failure-time hint: the refusal
        # is one that CAN happen, and the strip's cost is the read that was taken. The absent
        # phrases are the blanket claims no measurement backs - every simulation-ready machine
        # refused outright, posting and kinematics unaffected.
        props = ces.tool.to_dict()["inputSchema"]["properties"]
        desc = props["machine_strip_simulation"]["description"]
        assert "read back unchanged through the setup" in desc
        assert "refuses simulation-ready machines outright" not in desc
        assert "posting and kinematics" not in desc

    def test_non_sim_assignment_error_has_no_flag_hint(self, monkeypatch):
        # An unrelated assignment failure must not advertise the strip flag as a cure.
        cam, src = self._setup_with_sim_machine(monkeypatch)
        def _refuse(self, m):
            raise RuntimeError("machine schema version mismatch")
        monkeypatch.setattr(_Setup, "machine", property(lambda self: None, _refuse))
        res = ces.handler(setup="Setup1", machine="Haas VF-2")
        assert res["isError"] is True and "machine_strip_simulation" not in res["message"]


class TestWCSResolveJOAxes:
    def test_jo_refused_for_axis_keys_with_recipe(self, monkeypatch):
        # The platform accepts a JO for the ORIGIN binding but InternalValidationErrors on an
        # AXIS bind (verified live) - the resolver must refuse axes-JO up front, naming the
        # working recipe, instead of letting the raw platform error surface.
        monkeypatch.setattr(ces, "_resolve_wcs_value", lambda v: (object(), True, None))
        resolved, err = ces._resolve_wcs({"z_axis": "SomeJO"})
        assert resolved is None
        assert "ORIGIN only" in err and "face" in err
        resolved2, err2 = ces._resolve_wcs({"origin": "SomeJO"})
        assert err2 is None and "origin" in resolved2


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


class TestResolveWcsValue:
    """_resolve_wcs_value: a WCS binding value is a JOINT ORIGIN (handle or name, via JointOriginRef)
    OR a find_geometry handle. JO is tried first (it also recognises a JO token); a non-JO handle falls
    back to the geometry handle. Proven live: a JointOrigin binds into wcs_origin_point (bound_entities=1,
    mode='point') - the associative live-reference binding a self-centering template WCS needs."""

    def test_joint_origin_is_tried_first(self, monkeypatch):
        jo = object()
        monkeypatch.setattr(ces._WCS_JO, "resolve", lambda v: (jo, None))
        ent, is_jo, err = ces._resolve_wcs_value("Stock_Center")
        assert ent is jo and is_jo is True and err is None

    def test_non_jo_falls_back_to_geometry_handle(self, monkeypatch):
        face = object()
        monkeypatch.setattr(ces._WCS_JO, "resolve", lambda v: (None, "not a Joint Origin"))
        monkeypatch.setattr(ces._WCS_HANDLE, "resolve", lambda v: (face, None))
        ent, is_jo, err = ces._resolve_wcs_value("H_FACE")
        assert ent is face and is_jo is False and err is None

    def test_name_shaped_value_surfaces_the_jo_error(self, monkeypatch):
        # a bare NAME that resolves to neither: the JO error (which lists available JOs) is the useful
        # one, not the 'stale handle' error (is_handle('SomeName') is False).
        monkeypatch.setattr(ces._WCS_JO, "resolve", lambda v: (None, "no Joint Origin named 'SomeName'"))
        monkeypatch.setattr(ces._WCS_HANDLE, "resolve", lambda v: (None, "handle did not resolve"))
        ent, is_jo, err = ces._resolve_wcs_value("SomeName")
        assert ent is None and "Joint Origin" in err

    def test_token_shaped_value_surfaces_the_handle_error(self, monkeypatch):
        # a long token-shaped value that fails both: the handle error is the relevant one.
        tok = "/v" + "A" * 60
        monkeypatch.setattr(ces._WCS_JO, "resolve", lambda v: (None, "not a Joint Origin"))
        monkeypatch.setattr(ces._WCS_HANDLE, "resolve", lambda v: (None, "handle is stale"))
        ent, is_jo, err = ces._resolve_wcs_value(tok)
        assert ent is None and "stale" in err

    def test_resolve_wcs_routes_each_value_through_the_combined_resolver(self, monkeypatch):
        # the dict resolver delegates per-value to _resolve_wcs_value (JO OR handle).
        jo = object()
        monkeypatch.setattr(ces, "_resolve_wcs_value", lambda v: (jo, True, None))
        resolved, err = ces._resolve_wcs({"origin": "Stock_Center"})
        assert err is None and resolved == {"origin": jo}
