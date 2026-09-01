"""Unit tests for ``param_ops.py`` pure logic (the param_* tools).

Targets: ``_param_summary`` (numeric value vs. text-parameter ``textValue``
fallback), ``_find_parameter`` (user-params-first lookup, then full search,
boundaries 0/1/match), ``set_handler`` input validation — including the
subtle carve-out that an expression of ``"0"`` is NOT treated as "empty" — and
the ``_timeline_health`` helper the add/delete health-guard uses. (The
design_recompute is tested in test_design_ops.py.)
"""

from types import SimpleNamespace

from conftest import load_tool

params = load_tool("param_ops")


# ── _param_summary: numeric vs text value ──────────────────────────────────

class _UM:
    """A units manager: internal units are cm/radians, convert() scales into the target unit."""
    internalUnits = "cm"
    _FACTOR = {"mm": 10.0, "cm": 1.0, "in": 1 / 2.54, "deg": 180.0 / 3.141592653589793}

    def convert(self, value, from_unit, to_unit):
        return value * self._FACTOR[to_unit]


class TestParamSummary:
    def test_value_is_reported_in_the_parameters_OWN_unit(self):
        # Parameter.value is in DATABASE units: a "50 mm" length reads 5.0. Reporting 5.0 beside
        # unit='mm' is a 10x error for any caller doing arithmetic on it.
        p = SimpleNamespace(name="StockX", expression="50 mm", unit="mm",
                            comment="", value=5.0, textValue="ignored")
        out = params._param_summary(p, units_manager=_UM())
        assert out["value"] == 50.0                  # mm, matching out["unit"]
        assert out["value_units"] == "mm"
        assert out["value_internal"] == 5.0          # the raw db-unit number, clearly named
        assert out["name"] == "StockX"
        assert out["expression"] == "50 mm"

    def test_an_angle_is_converted_out_of_radians(self):
        # The same trap with a bigger factor: "90 deg" reads 1.5708 in db units (~57x off).
        p = SimpleNamespace(name="Draft", expression="90 deg", unit="deg",
                            comment="", value=3.141592653589793 / 2, textValue="")
        out = params._param_summary(p, units_manager=_UM())
        assert round(out["value"], 6) == 90.0
        assert out["value_units"] == "deg"

    def test_unitless_parameter_needs_no_conversion(self):
        # A count/ratio has no display unit, so its db number IS its value - labelling it as an
        # internal cm/radian figure would be a different kind of wrong.
        p = SimpleNamespace(name="Ratio", expression="3", unit="",
                            comment="", value=3.0, textValue="")
        out = params._param_summary(p, units_manager=_UM())
        assert out["value"] == 3.0 and out["value_units"] == ""

    def test_unconvertible_value_is_labelled_internal_not_mislabelled(self):
        # No units manager to convert with -> report the raw number AND say which frame it is in,
        # rather than presenting a db-unit number under the parameter's display unit.
        p = SimpleNamespace(name="StockX", expression="50 mm", unit="mm",
                            comment="", value=5.0, textValue="")
        out = params._param_summary(p, units_manager=None)
        assert out["value"] == 5.0
        assert out["value_units"] == "internal (cm/radians)"

    def test_text_param_falls_back_to_textValue(self):
        # A text parameter: .value raises, so summary must use .textValue.
        class TextParam:
            name = "Label"
            expression = "'Roughing'"
            unit = ""
            comment = ""
            textValue = "Roughing"

            @property
            def value(self):
                raise RuntimeError("text parameter has no numeric value")

        out = params._param_summary(TextParam())
        assert out["value"] == "Roughing"


# ── _find_parameter: lookup order + boundaries ─────────────────────────────

class _Params:
    def __init__(self, items):
        self._items = items

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None

    def __iter__(self):
        return iter(self._items)


def _design_with(user_params=(), all_params=()):
    return SimpleNamespace(
        userParameters=_Params(list(user_params)),
        allParameters=list(all_params),
    )


class TestFindParameter:
    def test_found_in_user_parameters_first(self):
        up = SimpleNamespace(name="StockX")
        design = _design_with(user_params=[up], all_params=[])
        assert params._find_parameter(design, "StockX") is up

    def test_falls_back_to_all_parameters(self):
        mp = SimpleNamespace(name="d1")
        design = _design_with(user_params=[], all_params=[mp])
        assert params._find_parameter(design, "d1") is mp

    def test_missing_returns_none(self):
        design = _design_with(user_params=[], all_params=[SimpleNamespace(name="other")])
        assert params._find_parameter(design, "StockX") is None


# ── set_handler: input validation ──────────────────────────────────────────

class TestSetValidation:
    def test_empty_name_is_error(self):
        res = params.set_handler(name="", expression="5")
        assert res["isError"] is True
        assert "Provide 'name'" in res["message"]

    def test_empty_expression_is_error(self):
        res = params.set_handler(name="StockX", expression="")
        assert res["isError"] is True
        assert "Provide 'expression'" in res["message"]

    def test_zero_expression_passes_the_empty_guard(self, monkeypatch):
        # "0" is a legitimate value and must NOT trip the empty-expression guard
        # (note the explicit `expression != "0"` carve-out in the source). Stub
        # _design to a known failure so we can prove we got PAST validation to a
        # different, later error — not the "Provide 'expression'" rejection.
        monkeypatch.setattr(params._common, "design", lambda: None)
        result = params.set_handler(name="StockX", expression="0")
        assert result["isError"] is True
        assert "Provide 'expression'" not in result["message"]
        assert "active design" in result["message"]   # reached the _design() check


# ── timeline health + guarded add/delete/favorite ──────────────────────────
#
# These handlers guard WRITES against breaking the parametric timeline: add
# rolls back a parameter that introduces a NEW timeline error; delete refuses
# when another expression references the name, and reports a health regression
# afterwards. The fakes below model a tiny timeline (items with healthState) and
# a userParameters collection that supports add/itemByName/deleteMe.

import json


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class FakeTimelineItem:
    def __init__(self, name, health=0):
        self.name = name
        self.healthState = health


class FakeTimeline:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeParam:
    def __init__(self, name, expression="", owner=None):
        self.name = name
        self.expression = expression
        self.isFavorite = False
        self.unit = "mm"
        self.comment = ""
        self.value = 1.0
        self._owner = owner
        self._deleted = False

    def deleteMe(self):
        self._deleted = True
        if self._owner is not None and self in self._owner._items:
            self._owner._items.remove(self)
        return True


class FakeUserParams:
    def __init__(self, items=()):
        self._items = list(items)
        for it in self._items:
            it._owner = self
        # add() can be told to inject a downstream error into the timeline.
        self.on_add_breaks_timeline = None   # a FakeTimeline to mutate, or None

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None

    def add(self, name, _value_input, _unit, _comment):
        p = FakeParam(name, owner=self)
        self._items.append(p)
        if self.on_add_breaks_timeline is not None:
            self.on_add_breaks_timeline._items.append(FakeTimelineItem("BrokenFeature", health=2))
        return p


class FakeParamsDesign:
    def __init__(self, user_params, timeline, all_params=None):
        self.userParameters = user_params
        self.timeline = timeline
        self.allParameters = list(all_params if all_params is not None else user_params._items)


def _stub_design(monkeypatch, design):
    monkeypatch.setattr(params._common, "design", lambda: design)
    # add_handler uses adsk.core.ValueInput.createByString — make it benign.
    import adsk.core
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("VI", s))


class TestTimelineHealth:
    # the LOCAL _timeline_health helper that add/delete use for their rollback guard
    # (health_handler is exercised via test_design_get.py + test_design_ops.py)
    def test_rolls_up_errors_and_warnings(self):
        tl = FakeTimeline([FakeTimelineItem("A", 0), FakeTimelineItem("B", 2),
                           FakeTimelineItem("C", 1), FakeTimelineItem("D", 2)])
        design = FakeParamsDesign(FakeUserParams(), tl)
        errors, warnings, total = params._timeline_health(design)
        assert total == 4
        assert errors == ["B", "D"]
        assert warnings == ["C"]


class TestAddHandler:
    def test_add_rejects_duplicate(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.add_handler(name="PartX", expression="5 mm")
        assert res["isError"] is True and "already exists" in res["message"]

    def test_add_succeeds_when_timeline_stays_healthy(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(name="NewP", expression="3 mm"))
        assert out["added"] is True
        assert up.itemByName("NewP") is not None      # it stuck

    def test_add_rolls_back_on_new_timeline_error(self, monkeypatch):
        tl = FakeTimeline([FakeTimelineItem("A", 0)])
        up = FakeUserParams([])
        up.on_add_breaks_timeline = tl                # adding will inject an error
        design = FakeParamsDesign(up, tl)
        _stub_design(monkeypatch, design)
        res = params.add_handler(name="BadP", expression="oops")
        assert res["isError"] is True
        assert "rolled back" in res["message"]
        assert up.itemByName("BadP") is None          # removed again

    def test_add_requires_name_and_expression(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams(), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res1 = params.add_handler(name="", expression="5")
        assert res1["isError"] is True
        assert "Missing 'name'" in res1["message"]
        res2 = params.add_handler(name="X", expression="")
        assert res2["isError"] is True
        assert "missing 'expression'" in res2["message"]


class TestAddBatch:
    # Adding N parameters is ONE batch call, not N separate calls.
    def test_batch_adds_all(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(params=[
            {"name": "WheelDia", "expression": "350 mm"},
            {"name": "AxleDia", "expression": "14 mm", "favorite": True},
            {"name": "CrankLen", "expression": "125 mm", "comment": "arm"},
        ]))
        assert out["added_count"] == 3
        assert {r["parameter"]["name"] for r in out["results"]} == {"WheelDia", "AxleDia", "CrankLen"}
        for nm in ("WheelDia", "AxleDia", "CrankLen"):
            assert up.itemByName(nm) is not None

    def test_batch_stops_and_reports_the_failing_entry(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        # 2nd entry is missing an expression -> that entry errors, the batch reports which index
        res = params.add_handler(params=[
            {"name": "Good", "expression": "1 mm"},
            {"name": "Bad", "expression": ""},
        ])
        assert res["isError"] is True
        assert "Bad" in res["message"] and "[1]" in res["message"]
        assert up.itemByName("Good") is not None        # the earlier good one is kept

    def test_single_param_path_still_works(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(name="Solo", expression="9 mm"))
        assert out["added"] is True and up.itemByName("Solo") is not None


class TestSetCreateOrUpdate:
    def test_set_existing_updates(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.set_handler(name="PartX", expression="20 mm"))
        assert out["set"] is True and out["created"] is False

    def test_silent_no_op_assignment_bites(self, monkeypatch):
        # the assignment raises nothing but the parameter still reads the same expression -> error

        class StuckParam(FakeParam):
            @property
            def expression(self):
                return "10 mm"

            @expression.setter
            def expression(self, v):
                pass                                     # silently ignores the assignment

        up = FakeUserParams([StuckParam("PartX")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.set_handler(name="PartX", expression="20 mm")
        assert res["isError"] is True
        assert "did not take" in res["message"]

    def test_setting_the_current_expression_is_already_current(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.set_handler(name="PartX", expression="10 mm"))
        assert out["set"] is True and out["already_current"] is True

    def test_set_missing_without_create_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.set_handler(name="Ghost", expression="5 mm")
        assert res["isError"] is True and "create=true" in res["message"]

    def test_set_missing_with_create_makes_user_param(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.set_handler(name="NewP", expression="3 mm", create=True))
        assert out["set"] is True and out["created"] is True
        assert out["before"] is None
        assert up.itemByName("NewP") is not None        # it was created


class TestDeleteHandler:
    def test_delete_refuses_if_referenced(self, monkeypatch):
        part = FakeParam("PartX", "10 mm")
        user = FakeParam("Half", "PartX / 2")          # references PartX
        up = FakeUserParams([part, user])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[part, user])
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="PartX")
        assert res["isError"] is True
        assert "referenced by: Half" in res["message"]
        assert part._deleted is False                  # not deleted

    def test_reference_match_is_word_boundary(self, monkeypatch):
        # 'PartX' must NOT be considered referenced by 'PartXY' (substring, not a
        # whole token) — the regex uses word boundaries.
        part = FakeParam("PartX", "10 mm")
        other = FakeParam("Calc", "PartXY + 1")        # different token
        up = FakeUserParams([part, other])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[part, other])
        _stub_design(monkeypatch, design)
        out = _payload(params.delete_handler(name="PartX"))
        assert out["deleted"] is True
        assert part._deleted is True

    def test_delete_unknown_param_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="Ghost")
        assert res["isError"] is True and "No USER parameter" in res["message"]


class TestFavoriteHandler:
    def test_sets_favorite_flag(self, monkeypatch):
        p = FakeParam("PartX", "10 mm")
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.favorite_handler(name="PartX", favorite=True))
        assert out["favorite"] is True
        assert p.isFavorite is True

    def test_unknown_param_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.favorite_handler(name="Ghost")
        assert res["isError"] is True and "No USER parameter" in res["message"]

    def test_favorite_set_failure_surfaces(self, monkeypatch):
        class Stubborn(FakeParam):
            def __setattr__(self, k, v):
                if k == "isFavorite" and getattr(self, "_built", False):
                    raise RuntimeError("read-only")
                object.__setattr__(self, k, v)
        p = Stubborn("PartX", "10 mm")
        p._built = True
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.favorite_handler(name="PartX", favorite=True)
        assert res["isError"] is True and "Could not set favorite" in res["message"]

    def test_empty_name_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.favorite_handler(name="")
        assert res["isError"] is True
        assert "Provide 'name'" in res["message"]

    def test_a_stuck_flag_is_published_as_it_reads_not_as_asked(self, monkeypatch):
        # The payload's 'favorite' IS the post-write re-read, so an assignment the platform
        # swallows shows up as the flag that is actually there. A payload echoing the REQUEST
        # would report true over a parameter nothing was set on.
        class StuckFavorite(FakeParam):
            @property
            def isFavorite(self):
                return False

            @isFavorite.setter
            def isFavorite(self, v):
                pass                                    # silently ignores the assignment

        p = StuckFavorite("PartX", "10 mm")
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.favorite_handler(name="PartX", favorite=True))
        assert out["favorite"] is False                 # the no-op is visible in the payload
        assert out["name"] == "PartX"


# ── param_get handler (read path) ──────────────────────────────────────────

class _GetParam:
    def __init__(self, name, expression="1 mm", value=1.0, unit="mm"):
        self.name = name
        self.expression = expression
        self.value = value
        self.unit = unit
        self.comment = ""
        self.textValue = ""


class _GetUserParams:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None


class _GetDesign:
    def __init__(self, user, all_params):
        self.userParameters = user
        self.allParameters = list(all_params)


class TestGetHandler:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(params._common, "design", lambda: None)
        res = params.handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_lists_user_parameters_only_by_default(self, monkeypatch):
        u1, u2 = _GetParam("PartX"), _GetParam("PartY")
        model_only = _GetParam("d1")
        design = _GetDesign(_GetUserParams([u1, u2]), [u1, u2, model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler())
        assert out["user_parameter_count"] == 2
        assert {p["name"] for p in out["user_parameters"]} == {"PartX", "PartY"}
        assert "model_parameters" not in out

    def test_the_user_parameter_walk_is_clamped_at_max_params(self, monkeypatch):
        # the listing is bounded so a pathological design cannot flood the wire; the clamp is read
        # off the collection's own count, so it holds no matter how many items the walk yields
        monkeypatch.setattr(params, "_MAX_PARAMS", 3)
        ups = _GetUserParams([_GetParam("P%d" % i) for i in range(10)])
        monkeypatch.setattr(params._common, "design", lambda: _GetDesign(ups, []))
        out = _payload(params.handler())
        assert out["user_parameter_count"] == 3
        assert [p["name"] for p in out["user_parameters"]] == ["P0", "P1", "P2"]

    def test_an_uncountable_collection_refuses_instead_of_reporting_zero(self, monkeypatch):
        # userParameters.count raising means the parameters could not be read AT ALL. Reporting
        # "user_parameter_count: 0" would read as "this design has no parameters" - a false answer.
        class _Uncountable(_GetUserParams):
            @property
            def count(self):
                raise RuntimeError("parameter table is locked")
        monkeypatch.setattr(params._common, "design",
                            lambda: _GetDesign(_Uncountable([_GetParam("PartX")]), []))
        res = params.handler()
        assert res["isError"] is True
        assert "could not read user parameters" in res["message"].lower()
        assert "locked" in res["message"]

    def test_include_model_parameters_dedups_user_names(self, monkeypatch):
        u1 = _GetParam("PartX")
        model_only = _GetParam("d1")
        design = _GetDesign(_GetUserParams([u1]), [u1, model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        # PartX is already a user param -> not duplicated into model_parameters
        assert out["model_parameter_count"] == 1
        assert out["model_parameters"][0]["name"] == "d1"

    def test_single_named_user_param(self, monkeypatch):
        u1 = _GetParam("PartX", expression="50 mm", value=5.0)
        design = _GetDesign(_GetUserParams([u1]), [u1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="PartX"))
        assert out["parameter"]["name"] == "PartX"
        assert out["parameter"]["value"] == 5.0

    def test_single_named_model_param_falls_through_to_all(self, monkeypatch):
        model_only = _GetParam("d1", value=2.0)
        design = _GetDesign(_GetUserParams([]), [model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="d1"))
        assert out["parameter"]["name"] == "d1"

    def test_single_named_missing_errors(self, monkeypatch):
        design = _GetDesign(_GetUserParams([]), [])
        monkeypatch.setattr(params._common, "design", lambda: design)
        res = params.handler(name="Ghost")
        assert res["isError"] is True and "not found" in res["message"].lower()


# ── delete_handler: deleteMe-false + timeline-error-after ──────────────────

class TestDeleteHandlerExtra:
    def test_delete_me_false_reported(self, monkeypatch):
        class Stubborn(FakeParam):
            def deleteMe(self):
                return False
        p = Stubborn("PartX", "10 mm")
        up = FakeUserParams([p])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="PartX")
        assert res["isError"] is True and "refused to delete" in res["message"]

    def test_timeline_error_after_delete_reported(self, monkeypatch):
        tl = FakeTimeline([FakeTimelineItem("A", 0)])

        class Breaking(FakeParam):
            def deleteMe(self):
                tl._items.append(FakeTimelineItem("BrokenChild", health=2))
                if self in self._owner._items:
                    self._owner._items.remove(self)
                return True
        p = Breaking("PartX", "10 mm")
        up = FakeUserParams([p])
        design = FakeParamsDesign(up, tl, all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="PartX")
        assert res["isError"] is True
        assert "introduced a timeline error" in res["message"]

    def test_empty_name_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="")
        assert res["isError"] is True
        assert "Provide 'name'" in res["message"]


# ── _owner_facts: the maker a model parameter belongs to ───────────────────
#
# A model parameter's expression is readable but not interpretable on its own: "d24" or
# "( -0.025 / 2 ) * 1 mm" says nothing about which feature or sketch it drives, and a design with
# zero user parameters has this list as its ONLY parameter view. .createdBy + .role are what turn a
# dNN row into a statement about the design.


class _ExtrudeOwner:
    """A feature owner: it has a name and no parentSketch."""

    name = "Extrude1"


class _DimOwner:
    """A sketch dimension owner: no name of its own, but it knows its sketch."""

    parentSketch = SimpleNamespace(name="Sketch2")


class _NamelessOwner:
    """An owner whose name read RAISES - the row must say nothing rather than invent one."""

    @property
    def name(self):
        raise RuntimeError("name is unreadable on this proxy")


class _ModelParam:
    """A model parameter: it carries createdBy/role, which a user parameter does not."""

    def __init__(self, name="d195", owner=None, role="Distance", expression="5 mm",
                 value=0.5, unit="mm", comment=""):
        self.name = name
        self.expression = expression
        self.value = value
        self.unit = unit
        self.comment = comment
        self.role = role
        self._owner = owner

    @property
    def createdBy(self):
        if self._owner is None:
            raise AttributeError("createdBy")     # what a UserParameter does
        return self._owner


class TestOwnerFacts:
    def test_a_feature_owned_parameter_names_the_feature_and_the_role(self):
        # the whole point of the row: d195 alone is opaque, "Extrude1 / Distance" is not
        out = params._param_summary(_ModelParam(owner=_ExtrudeOwner(), role="Distance"),
                                    units_manager=_UM())
        assert out["owner"] == "Extrude1"
        assert out["owner_type"] == "_ExtrudeOwner"
        assert out["role"] == "Distance"
        assert "owner_sketch" not in out          # a feature does not live in a sketch

    def test_a_sketch_dimension_parameter_names_its_sketch(self):
        out = params._param_summary(_ModelParam(name="d27", owner=_DimOwner(), role="Dimension"),
                                    units_manager=_UM())
        assert out["owner_sketch"] == "Sketch2"
        assert out["owner_type"] == "_DimOwner"
        assert "owner" not in out                 # a dimension carries no name of its own

    def test_an_unreadable_owner_name_is_absent_never_fabricated(self):
        # the owner object is there but will not say what it is called: the honest row omits the
        # key. A stand-in ("unknown", the parameter's own name, the type as the name) would read as
        # a measured answer.
        out = params._param_summary(_ModelParam(owner=_NamelessOwner()), units_manager=_UM())
        assert "owner" not in out
        assert out["owner_type"] == "_NamelessOwner"
        assert "unknown" not in json.dumps(out).lower()

    def test_a_parameter_with_no_maker_gets_no_owner_keys(self):
        # a UserParameter has no createdBy at all - the read raises and the row stays flat
        out = params._param_summary(_ModelParam(name="StockX", owner=None), units_manager=_UM())
        assert not [k for k in out if k.startswith("owner")]
        assert "role" not in out

    def test_an_empty_role_is_dropped_rather_than_published_blank(self):
        out = params._param_summary(_ModelParam(owner=_ExtrudeOwner(), role=""),
                                    units_manager=_UM())
        assert "role" not in out

    def test_the_flat_fields_are_untouched_by_the_owner_read(self):
        # pin: the owner keys are ADDITIVE. A future refactor of the summary must not quietly
        # change what the pre-existing keys report while the new ones look right.
        p = _ModelParam(name="d195", owner=_ExtrudeOwner(), role="Distance",
                        expression="( -0.025 / 2 ) * 1 mm", value=-0.00125, unit="mm",
                        comment="half the diametral clearance")
        out = params._param_summary(p, units_manager=_UM())
        assert {k: out[k] for k in ("name", "expression", "unit", "comment", "favorite",
                                    "value", "value_internal", "value_units")} == {
            "name": "d195",
            "expression": "( -0.025 / 2 ) * 1 mm",
            "unit": "mm",
            "comment": "half the diametral clearance",
            "favorite": None,               # model parameters carry no favorite flag
            "value": -0.0125,               # -0.00125 cm -> mm, the parameter's own unit
            "value_internal": -0.00125,
            "value_units": "mm",
        }

    def test_a_comment_an_author_set_is_surfaced(self):
        # the field design intent belongs in: it is read straight off the parameter, so a set
        # comment reaches the wire and an EMPTY one means the parameter carries none.
        out = params._param_summary(_ModelParam(comment="bearing seat, do not round"),
                                    units_manager=_UM())
        assert out["comment"] == "bearing seat, do not round"


class TestOwnerOnTheReadPath:
    def test_model_parameters_carry_their_owner_and_the_note_explains_the_keys(self, monkeypatch):
        u1 = _GetParam("PartX")
        d1 = _ModelParam(name="d1", owner=_ExtrudeOwner(), role="Distance")
        design = _GetDesign(_GetUserParams([u1]), [u1, d1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        row = out["model_parameters"][0]
        assert (row["owner"], row["role"]) == ("Extrude1", "Distance")
        assert "owner_type" in out["note"] and "role" in out["note"]

    def test_user_parameters_gain_no_owner_keys_so_the_default_read_stays_light(self, monkeypatch):
        u1 = _GetParam("PartX")
        design = _GetDesign(_GetUserParams([u1]), [u1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler())
        assert not [k for k in out["user_parameters"][0] if k.startswith("owner")]
        assert "note" not in out

    def test_no_note_when_not_one_model_row_has_an_owner(self, monkeypatch):
        # a note describing owner keys that are not in the payload sends a caller looking for them
        design = _GetDesign(_GetUserParams([]), [_GetParam("d1")])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        assert out["model_parameter_count"] == 1
        assert "note" not in out

    def test_a_single_named_model_parameter_is_answered_with_its_owner(self, monkeypatch):
        # the cheapest form of the read: one parameter, one owner, no list to page through
        d195 = _ModelParam(name="d195", owner=_DimOwner(), role="Dimension")
        design = _GetDesign(_GetUserParams([]), [d195])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="d195"))
        assert out["parameter"]["owner_sketch"] == "Sketch2"
        assert out["parameter"]["role"] == "Dimension"


# ── _add_one favorite flag read-back ───────────────────────────────────────

class TestAddFavorite:
    def test_favorite_reported_from_param_state(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(name="P", expression="5 mm", favorite=True))
        assert out["favorite"] is True
        assert up.itemByName("P").isFavorite is True
