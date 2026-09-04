"""Unit tests for ``_param_common.py`` - the parameter row every param_* tool publishes.

Targets: ``_param_summary`` (numeric value vs. text-parameter ``textValue`` fallback, and the
DATABASE-unit conversion), ``_owner_facts`` (a model parameter's maker), and ``_find_parameter``
(user-params-first lookup, then full search, boundaries 0/1/match).
"""

import json
from types import SimpleNamespace

from conftest import load_tool

params = load_tool("_param_common")


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
