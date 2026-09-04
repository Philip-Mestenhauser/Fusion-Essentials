"""Unit tests for ``param_get.py`` - the parameter read path and its bounded walk.

The row itself (``_param_summary`` / ``_owner_facts``) is pinned in test__param_common.py, shared
with the param write tools; what is proved here is this tool's own logic: the default user-only
listing, the model-parameter de-dup, the single-name lookup, the _MAX_PARAMS clamp, and the note
that is only published when a row actually carries owner keys.
"""

import json
from types import SimpleNamespace

from conftest import load_tool

params = load_tool("param_get")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


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


class _ExtrudeOwner:
    """A feature owner: it has a name and no parentSketch."""

    name = "Extrude1"


class _DimOwner:
    """A sketch dimension owner: no name of its own, but it knows its sketch."""

    parentSketch = SimpleNamespace(name="Sketch2")


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
