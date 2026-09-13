"""Unit tests for ``design_set_metadata.py`` - a component's part number and description.

Pinned here, no live Fusion: the set is judged by the values READ BACK off the component, so a
value that landed different from the one requested is an error rather than a false ok; an
occurrence target writes its COMPONENT; and only the fields that were given are touched.

The fake models what the shared component fake does not: a metadata setter the platform normalizes
or silently refuses, which is what the read-back exists to catch.
"""

import os
import sys

import pytest

from conftest import (BRepBody, FakeOccurrence, MakeComp, MakeDesign, _NamedCollection,
                      error_message, install, load_tool, payload)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "live"))
import verify_core  # noqa: E402  the live sweep's predicate over this tool's payload

sm = load_tool("design_set_metadata")


class _Rewriting(MakeComp):
    """A component whose partNumber setter lands something else - `rewrite` is what the assignment
    turns into (the dedupe suffix Fusion appends to a taken name, 'FE-1 (1)', is this shape), and
    `refuse` the assignment that is accepted and changes nothing."""

    def __init__(self, name="Bracket", part_number="", rewrite=None, refuse=False, **kw):
        super().__init__(name=name, **kw)
        self._part_number = part_number
        self._rewrite = rewrite
        self._refuse = refuse

    @property
    def partNumber(self):
        return self._part_number

    @partNumber.setter
    def partNumber(self, value):
        if self._refuse:
            return
        self._part_number = self._rewrite if self._rewrite is not None else value


@pytest.fixture
def wire(monkeypatch):
    """Build a design around `comp` and patch BOTH design seams (the tool's own ``_common`` and the
    one ``_inputs`` resolves the target through)."""
    def _make(comp=None, occurrences=(), root_name="Assembly"):
        comp = MakeComp(name="Bracket", entity_token="T:bracket") if comp is None else comp
        root = MakeComp(name=root_name, entity_token="T:root", occurrences=list(occurrences))
        design = MakeDesign(comp=root, all_components=[root, comp])
        install(sm, design)
        return design, comp
    return _make


class TestSet:
    def test_publishes_the_values_read_back_off_the_component(self, wire):
        _design, comp = wire(MakeComp(name="Bracket", entity_token="T:b", part_number="",
                                      description=""))
        out = payload(sm.handler(target="Bracket", part_number="FE-BRACKET-001",
                                 description="Sweep bracket"))
        assert out["set"] is True and out["kind"] == "component"
        assert out["part_number"] == "FE-BRACKET-001" and out["description"] == "Sweep bracket"
        assert out["previous_part_number"] == "" and out["component"] == "Bracket"
        assert comp.partNumber == "FE-BRACKET-001"      # the model, not just the payload

    def test_declared_outputs_present(self, wire):
        wire(MakeComp(name="Bracket", entity_token="T:b", part_number="", description=""))
        out = payload(sm.handler(target="Bracket", part_number="FE-1"))
        for r in sm.RETURNS:
            assert r.assert_present(out) == "", r.key

    def test_only_the_field_given_is_written(self, wire):
        # the other field is LEFT ALONE: an omitted input means "unchanged", never "set it empty".
        _design, comp = wire(MakeComp(name="Bracket", entity_token="T:b", part_number="OLD-1",
                                      description="keep me"))
        out = payload(sm.handler(target="Bracket", description="new text"))
        assert comp.partNumber == "OLD-1" and comp.description == "new text"
        assert out["part_number"] == "OLD-1" and "part_number_requested" not in out
        assert out["description_requested"] == "new text"

    def test_an_occurrence_target_writes_its_component(self, wire):
        comp = MakeComp(name="Bracket", entity_token="T:b", part_number="", description="")
        occ = FakeOccurrence(path="Bracket:1", component=comp)
        wire(comp, occurrences=[occ])
        out = payload(sm.handler(target="Bracket:1", part_number="FE-BRACKET-001"))
        assert out["kind"] == "component" and comp.partNumber == "FE-BRACKET-001"

    def test_the_live_sweep_predicate_reads_this_payload(self, wire):
        # the seam no offline gate sees: the sweep row asserts on THESE keys, so a key renamed here
        # reaches the live run as a red on a working write.
        wire(MakeComp(name="Bracket", entity_token="T:b", part_number="MINTED-01",
                      description=""))
        out = payload(sm.handler(target="Bracket", part_number="FE-BRACKET-001",
                                 description="Sweep bracket"))
        assert verify_core._metadata_set(
            "Bracket", "FE-BRACKET-001", "Sweep bracket")(out) is True

    def test_an_empty_part_number_is_refused_with_what_was_measured(self, wire):
        # MEASURED: assigning '' leaves the number the component already holds - so the refusal
        # names that, rather than letting a read-back mismatch report it as a failed write.
        _design, comp = wire(MakeComp(name="Bracket", entity_token="T:b", part_number="FE-1",
                                      description=""))
        msg = error_message(sm.handler(target="Bracket", part_number=""))
        assert "ignored by Fusion" in msg and "keeps the number" in msg
        assert comp.partNumber == "FE-1"                # refused BEFORE anything was assigned

    def test_an_empty_description_is_still_a_request(self, wire):
        # only the part number carries the measured refusal: clearing a description is a write like
        # any other, judged by the read-back.
        _design, comp = wire(MakeComp(name="Bracket", entity_token="T:b", part_number="FE-1",
                                      description="old text"))
        out = payload(sm.handler(target="Bracket", description=""))
        assert out["description"] == "" and comp.description == ""


class TestGuards:
    def test_neither_field_is_refused_naming_both(self, wire):
        wire()
        msg = error_message(sm.handler(target="Bracket"))
        assert "part_number" in msg and "description" in msg

    def test_a_read_back_that_does_not_match_the_request_is_an_error(self, wire):
        # the cardinal sin: the assignment is accepted and the component reads something else. The
        # error names both values and the payload never claims the request landed.
        comp = _Rewriting(name="Bracket", entity_token="T:b", part_number="FE-1",
                          rewrite="FE-1 (1)", description="")
        wire(comp)
        msg = error_message(sm.handler(target="Bracket", part_number="FE-2"))
        assert "'FE-1 (1)'" in msg and "'FE-2'" in msg and "LANDED" in msg
        assert comp.partNumber == "FE-1 (1)"

    def test_a_silently_refused_set_is_an_error_not_a_false_ok(self, wire):
        comp = _Rewriting(name="Bracket", entity_token="T:b", part_number="FE-1", refuse=True,
                          description="")
        wire(comp)
        msg = error_message(sm.handler(target="Bracket", part_number="FE-2"))
        assert "FE-1" in msg and "FE-2" in msg

    def test_a_raising_set_is_reported_with_its_reason(self, wire):
        class _ReadOnly(MakeComp):
            """A component whose metadata the platform declines out loud."""
            @property
            def partNumber(self):
                return "FE-1"

            @partNumber.setter
            def partNumber(self, value):
                raise RuntimeError("the part number is read-only in this context")

        wire(_ReadOnly(name="Bracket", entity_token="T:b", description=""))
        msg = error_message(sm.handler(target="Bracket", part_number="FE-2"))
        assert "Could not set metadata" in msg and "read-only in this context" in msg

    def test_an_unreadable_read_back_is_unverified_not_a_success(self, wire):
        class _WriteOnly(MakeComp):
            """A component that takes the assignment and will not answer the read."""
            @property
            def partNumber(self):
                raise RuntimeError("part number unavailable")

            @partNumber.setter
            def partNumber(self, value):
                pass

            @property
            def description(self):
                return ""

        wire(_WriteOnly(name="Bracket", entity_token="T:b"))
        msg = error_message(sm.handler(target="Bracket", part_number="FE-2"))
        assert "could not be read back" in msg and "unverified" in msg

    def test_a_body_target_is_refused(self, wire):
        # a body carries neither field; the kinds are the component and the occurrence placing it.
        _design, comp = wire()
        design = MakeDesign(comp=MakeComp(name="Assembly", entity_token="T:root",
                                          bodies=[BRepBody("Body1")]),
                            all_components=[comp])
        install(sm, design)
        msg = error_message(sm.handler(target="Body1", part_number="FE-1"))
        assert "is a body" in msg and "occurrence, component" in msg

    def test_unknown_target_refused(self, wire):
        wire()
        assert "did not resolve" in error_message(sm.handler(target="Ghost", part_number="FE-1"))

    def test_no_active_design_refused(self, monkeypatch):
        monkeypatch.setattr(sm._common, "design", lambda: None)
        monkeypatch.setattr(sm._inputs._common, "design", lambda: None)
        assert "No active design" in error_message(sm.handler(target="Bracket", part_number="FE-1"))

    def test_ambiguous_component_name_is_refused_without_writing_either(self, wire):
        one = MakeComp(name="Bolt", entity_token="T:1", part_number="A")
        two = MakeComp(name="Bolt", entity_token="T:2", part_number="B")
        root = MakeComp(name="Assembly", entity_token="T:root")
        root.occurrences = _NamedCollection([])
        install(sm, MakeDesign(comp=root, all_components=[root, one, two]))
        msg = error_message(sm.handler(target="Bolt", part_number="FE-1"))
        assert "2 components match 'Bolt'" in msg
        assert (one.partNumber, two.partNumber) == ("A", "B")
