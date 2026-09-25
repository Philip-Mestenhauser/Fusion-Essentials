"""Unit tests for ``cam_set_nc_comment.handler`` — the empty-input guard, the two NAMES and
multi-program behaviour.

``comment`` defaults to ``""`` (never ``None``). The handler must refuse when there is genuinely
nothing to write rather than writing an empty comment to every NC program. ``set_name`` moves
NCProgram.name (the listing name); ``set_number`` moves the nc_program_name PARAMETER.
"""

import json

from conftest import (FakeCAMParameter, FakeCAMParameters, _NamedCollection, load_tool, make_cam)

nc = load_tool("cam_set_nc_comment")


# NCProgram has no live SHAPES dump, so it keeps a local fake; its parameters and the ncPrograms
# walk are the shared ones.

class FakeNCP:
    """An NC program: the name a target match reads, and the two string parameters a write goes to."""
    def __init__(self, name, comment="'old'", editable=True, parameters=None):
        self.name = name
        self.parameters = parameters if parameters is not None else FakeCAMParameters([
            FakeCAMParameter("nc_program_comment", comment, editable=editable),
            FakeCAMParameter("nc_program_name", "'" + name + "'", editable=editable),
        ])


class _UnnamedNCP(FakeNCP):
    """A program whose NCProgram.name will not READ - the name a rename could not be confirmed
    against, and the one a listing must show as an unnamed slot rather than drop."""

    @property
    def name(self):
        raise RuntimeError("name is locked")

    @name.setter
    def name(self, _v):
        pass


def _install(monkeypatch, programs):
    cam = make_cam()
    cam.ncPrograms = _NamedCollection(list(programs))
    monkeypatch.setattr(nc, "get_cam", lambda: (cam, None))
    return cam


def _payload(res):
    return json.loads(res["content"][0]["text"]) if not res.get("isError") else None


# ── the guard ───────────────────────────────────────────────────────────────

class TestEmptyInputGuard:
    def test_empty_comment_and_no_set_name_is_refused(self, monkeypatch):
        # the wipe-everything case: comment defaults to "", so an empty comment with no
        # set_name must still be refused rather than blanking every program's comment.
        cam = _install(monkeypatch, [FakeNCP("P1", "'keep me'")])
        res = nc.handler(comment="", program="", set_name="")
        assert res["isError"] is True
        # and it must NOT have touched the existing comment
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'keep me'"

    def test_whitespace_only_comment_no_set_name_refused(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1", "'keep me'")])
        res = nc.handler(comment="   ", program="", set_name="")
        assert res["isError"] is True
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'keep me'"

    def test_real_comment_goes_through(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1")])
        res = nc.handler(comment="Job 42", program="P1")
        assert res["isError"] is False
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'Job 42'"

    def test_set_name_only_is_allowed(self, monkeypatch):
        # no comment, but renaming IS a valid intent
        cam = _install(monkeypatch, [FakeNCP("P1")])
        res = nc.handler(comment="", program="P1", set_name="NewName")
        assert res["isError"] is False
        assert cam.ncPrograms.item(0).name == "NewName"

    def test_the_wire_requires_no_single_input(self):
        # MEASURED on a receipt run: a schema requiring 'comment' refused a rename-only call before
        # the handler saw it. Any one of the three fields is a legal call on its own.
        schema = nc.tool.to_dict()["inputSchema"]
        assert not schema.get("required")
        assert {"comment", "program", "set_name", "set_number"} <= set(schema["properties"])

    def test_set_number_only_is_allowed(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1")])
        res = nc.handler(comment="", program="P1", set_number="2002")
        assert res["isError"] is False
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_name").expression == "'2002'"


# ── multi-program pre-validation (rollback concern) ─────────────────────────

class TestMultiProgramPreValidation:
    def test_uneditable_program_aborts_before_any_write(self, monkeypatch):
        # P2's comment is locked. The loop must NOT mutate P1 and then fail on P2 —
        # it pre-checks editability so nothing is half-applied.
        cam = _install(monkeypatch, [FakeNCP("P1", "'a'"), FakeNCP("P2", "'b'", editable=False)])
        res = nc.handler(comment="STAMP", program="")   # all programs
        assert res["isError"] is True
        # P1 must be untouched (no partial application)
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'a'"

    def test_all_editable_applies_to_all(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1"), FakeNCP("P2")])
        res = nc.handler(comment="STAMP", program="")
        p = _payload(res)
        assert res["isError"] is False
        assert p["programs_changed"] == 2
        for i in (0, 1):
            assert cam.ncPrograms.item(i).parameters.itemByName("nc_program_comment").expression == "'STAMP'"


# ── quote / unquote helpers ──────────────────────────────────────────────────

class TestQuoting:
    def test_quote_wraps_in_single_quotes(self):
        assert nc._quote("Job 7") == "'Job 7'"

    def test_quote_escapes_embedded_apostrophe(self):
        assert nc._quote("O'Brien") == "'O\\'Brien'"

    def test_unquote_strips_matching_quotes(self):
        assert nc._unquote("'Job 7'") == "Job 7"
        assert nc._unquote('"Job 7"') == "Job 7"

    def test_unquote_leaves_unquoted_string(self):
        assert nc._unquote("bare") == "bare"

    def test_unquote_none_is_none(self):
        assert nc._unquote(None) is None

    def test_quote_unquote_round_trip(self):
        # _unquote does NOT un-escape, but a plain (apostrophe-free) value round-trips.
        for v in ("Part 1234", "left-right", ""):
            assert nc._unquote(nc._quote(v)) == v


# ── program targeting + reporting ────────────────────────────────────────────

class TestProgramTargeting:
    def test_targets_only_named_program(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1", "'a'"), FakeNCP("P2", "'b'")])
        out = _payload(nc.handler(comment="NEW", program="P2"))
        assert out["programs_changed"] == 1
        assert out["programs"][0]["program"] == "P2"
        # P1 untouched
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'a'"
        assert cam.ncPrograms.item(1).parameters.itemByName("nc_program_comment").expression == "'NEW'"

    def test_before_after_reported_unquoted(self, monkeypatch):
        _install(monkeypatch, [FakeNCP("P1", "'old job'")])
        out = _payload(nc.handler(comment="new job", program="P1"))
        rec = out["programs"][0]
        assert rec["comment_before"] == "old job"     # unquoted in the report
        assert rec["comment_after"] == "new job"

    def test_unknown_program_lists_available(self, monkeypatch):
        _install(monkeypatch, [FakeNCP("P1"), FakeNCP("P2")])
        res = nc.handler(comment="X", program="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"]
        assert "P1" in res["message"] and "P2" in res["message"]

    def test_a_program_whose_name_cannot_be_read_reaches_available_as_none(self, monkeypatch):
        # the available list is what the user picks their next 'program' from, so a program that
        # exists but whose name is unreadable must show up as an unnamed slot - dropping it would
        # claim the document holds fewer programs than it does
        _install(monkeypatch, [FakeNCP("P1"), _UnnamedNCP("P2")])
        res = nc.handler(comment="X", program="Ghost")
        assert res["isError"] is True
        assert "P1" in res["message"] and "None" in res["message"]

    def test_no_nc_programs_errors(self, monkeypatch):
        _install(monkeypatch, [])
        res = nc.handler(comment="X")
        assert res["isError"] is True and "no nc programs" in res["message"].lower()

    def test_comment_and_name_both_set(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1", "'oldc'")])
        out = _payload(nc.handler(comment="C", program="P1", set_name="Renamed"))
        rec = out["programs"][0]
        assert rec["comment_after"] == "C"
        assert rec["name_after"] == "Renamed"
        assert out["set_name"] == "Renamed"
        assert cam.ncPrograms.item(0).name == "Renamed"

    def test_uneditable_number_aborts_before_any_write(self, monkeypatch):
        # set_number targets nc_program_name; if it's locked, abort before changing the comment.
        ncp = FakeNCP("P1", "'keepc'")
        ncp.parameters.itemByName("nc_program_name").isEditable = False
        _install(monkeypatch, [ncp])
        res = nc.handler(comment="C", program="P1", set_number="9")
        assert res["isError"] is True
        # comment must be untouched (aborted in the pre-validation pass)
        assert ncp.parameters.itemByName("nc_program_comment").expression == "'keepc'"


# ── the re-read is COMPARED, not just published ──────────────────────────────
#
# A parameter that accepts the assignment and keeps its old expression is the platform shape this
# tool's read-back exists for. Publishing the re-read is not enough on its own: set:true with a
# comment_after that never changed is a swallowed write reported as a success, so the compare is
# what turns it into an error.


class _StuckParam(FakeCAMParameter):
    """Accepts an expression assignment and keeps the one it holds."""
    @FakeCAMParameter.expression.setter
    def expression(self, value):
        pass


class _UnreadableParam(FakeCAMParameter):
    """Takes the assignment; its expression cannot be READ afterwards - the write is unconfirmed,
    which is not the same as landed."""
    _reads = 0

    @property
    def expression(self):
        # the pre-write read answers (so 'before' is real); every later read raises
        self._reads += 1
        if self._reads > 1:
            raise RuntimeError("expression is unreadable")
        return self._expression

    @expression.setter
    def expression(self, value):
        self._expression = value


class _StuckNCP(FakeNCP):
    """A program whose comment and NUMBER parameters both keep the expression they already hold."""
    def __init__(self, name, comment="'old'"):
        super().__init__(name, comment, parameters=FakeCAMParameters([
            _StuckParam("nc_program_comment", comment),
            _StuckParam("nc_program_name", "'" + name + "'"),
        ]))


class _StuckNameNCP(FakeNCP):
    """NCProgram.name accepts the assignment and keeps the name it holds - the declined rename."""

    @property
    def name(self):
        return self._n

    @name.setter
    def name(self, value):
        if not hasattr(self, "_n"):
            self._n = value               # born under its own name; every later write is swallowed


class _SuffixingNCP(FakeNCP):
    """NCProgram.name lands a VARIANT of what was written - the setter whose result, whatever made
    it differ, is the program's new address."""

    @property
    def name(self):
        return self._n

    @name.setter
    def name(self, value):
        self._n = str(value) + "1" if hasattr(self, "_n") else str(value)


class TestStuckParameter:
    def test_a_stuck_comment_is_an_error_not_a_reported_success(self, monkeypatch):
        cam = _install(monkeypatch, [_StuckNCP("P1", "'keep me'")])
        res = nc.handler(comment="Job 42", program="P1")
        assert res["isError"] is True
        # the message names BOTH values, so the caller can see it is a no-take and not a typo
        assert "did not take" in res["message"]
        assert "keep me" in res["message"] and "Job 42" in res["message"]
        assert cam.ncPrograms.item(0).parameters.itemByName(
            "nc_program_comment").expression == "'keep me'"

    def test_a_stuck_number_is_an_error_not_a_reported_success(self, monkeypatch):
        _install(monkeypatch, [_StuckNCP("P1", "'c'")])
        res = nc.handler(comment="", program="P1", set_number="2002")
        assert res["isError"] is True
        assert "did not take" in res["message"] and "2002" in res["message"]

    def test_a_stuck_number_after_a_landed_comment_says_the_comment_remains(self, monkeypatch):
        # the comment write already landed on this program and this call does not undo it - an
        # isError the caller reads as "nothing happened" would be the false part.
        ncp = FakeNCP("P1", "'old'")
        ncp.parameters = FakeCAMParameters([FakeCAMParameter("nc_program_comment", "'old'"),
                                            _StuckParam("nc_program_name", "'P1'")])
        _install(monkeypatch, [ncp])
        res = nc.handler(comment="Job 42", program="P1", set_number="2002")
        assert res["isError"] is True
        assert "The comment on 'P1' reads 'Job 42' and remains." in res["message"]
        assert ncp.parameters.itemByName("nc_program_comment").expression == "'Job 42'"

    def test_a_stuck_number_after_a_landed_name_says_the_name_remains(self, monkeypatch):
        # the rename landed and is not undone, so the program now answers to the NEW name - a
        # caller told only that the call failed would address a name nothing answers to.
        ncp = FakeNCP("P1", "'c'")
        ncp.parameters = FakeCAMParameters([FakeCAMParameter("nc_program_comment", "'c'"),
                                            _StuckParam("nc_program_name", "'P1'")])
        cam = _install(monkeypatch, [ncp])
        res = nc.handler(comment="", program="P1", set_name="Renamed", set_number="2002")
        assert res["isError"] is True
        assert "The name reads 'Renamed' and remains." in res["message"]
        assert cam.ncPrograms.item(0).name == "Renamed"

    def test_a_comment_that_cannot_be_read_back_is_unconfirmed_not_ok(self, monkeypatch):
        # a write whose effect cannot be READ is unconfirmed; reporting set:true would state a
        # landing this call never observed.
        ncp = FakeNCP("P1")
        ncp.parameters = FakeCAMParameters([_UnreadableParam("nc_program_comment", "'old'"),
                                            FakeCAMParameter("nc_program_name", "'P1'")])
        _install(monkeypatch, [ncp])
        res = nc.handler(comment="Job 42", program="P1")
        assert res["isError"] is True and "UNCONFIRMED" in res["message"]

    def test_an_apostrophe_that_round_trips_is_not_read_as_a_no_take(self, monkeypatch):
        # _quote escapes the apostrophe and _unquote does not un-escape it, so comparing the raw
        # request against the unquoted read-back would convict every landed write carrying one.
        cam = _install(monkeypatch, [FakeNCP("P1", "'old'")])
        res = nc.handler(comment="O'Brien", program="P1")
        assert res["isError"] is False
        assert cam.ncPrograms.item(0).parameters.itemByName(
            "nc_program_comment").expression == "'O\\'Brien'"


# ── the two names: NCProgram.name is the LISTING, nc_program_name the program NUMBER ──────────
#
# They are different fields: writing the parameter moves what a report reads while the listing does
# not follow, and cam_post's 'program_name' looks a program up by the LISTING. The fresh ncPrograms
# walk is the second, independent channel that catches the disagreement.


class TestSetNameAndNumber:
    def test_a_rename_lands_on_the_program_name_and_the_listing_carries_it(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("1001")])
        out = _payload(nc.handler(comment="", program="1001", set_name="BRACKET-1001"))
        rec = out["programs"][0]
        assert rec["name_before"] == "1001" and rec["name_after"] == "BRACKET-1001"
        assert rec["name_listed"] is True and "name_differs_from_request" not in rec
        assert [p.name for p in cam.ncPrograms] == ["BRACKET-1001"]
        # the NUMBER parameter is a different field and this call did not touch it
        assert cam.ncPrograms.item(0).parameters.itemByName(
            "nc_program_name").expression == "'1001'"

    def test_a_name_that_does_not_move_is_an_error(self, monkeypatch):
        cam = _install(monkeypatch, [_StuckNameNCP("1001")])
        res = nc.handler(comment="", program="1001", set_name="BRACKET-1001")
        assert res["isError"] is True
        assert "still reads '1001'" in res["message"]
        assert cam.ncPrograms.item(0).name == "1001"

    def test_a_landed_name_the_listing_does_not_carry_is_an_error(self, monkeypatch):
        # the held program reads the name that landed while the ncPrograms walk answers another
        _install(monkeypatch, [FakeNCP("1001")])
        monkeypatch.setattr(nc, "_listing_names", lambda cam: ["1001"])
        res = nc.handler(comment="", program="1001", set_name="BRACKET-1001")
        assert res["isError"] is True
        assert "fresh ncPrograms walk lists 1001" in res["message"]

    def test_set_number_writes_the_parameter_and_reads_it_back(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("1001")])
        out = _payload(nc.handler(comment="", program="1001", set_number="2002"))
        rec = out["programs"][0]
        assert rec["number_before"] == "1001" and rec["number_after"] == "2002"
        assert out["set_number"] == "2002"
        assert cam.ncPrograms.item(0).name == "1001"          # the listing name did not move

    def test_both_names_move_independently_in_one_call(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("1001")])
        out = _payload(nc.handler(comment="Job 42", program="1001",
                                  set_name="BRACKET-1001", set_number="2002"))
        rec = out["programs"][0]
        assert rec["comment_after"] == "Job 42"
        assert rec["name_after"] == "BRACKET-1001" and rec["number_after"] == "2002"
        assert cam.ncPrograms.item(0).name == "BRACKET-1001"
        assert cam.ncPrograms.item(0).parameters.itemByName(
            "nc_program_name").expression == "'2002'"

    def test_a_landed_name_that_differs_from_the_request_is_flagged_as_the_new_address(
            self, monkeypatch):
        # the setter can land a variant; the payload states THAT the landed name differs and is the
        # address to use from here, without asserting a cause no read backs.
        cam = _install(monkeypatch, [_SuffixingNCP("1001")])
        out = _payload(nc.handler(comment="", program="1001", set_name="BRACKET"))
        rec = out["programs"][0]
        assert rec["name_after"] == "BRACKET1" and rec["name_differs_from_request"] is True
        assert cam.ncPrograms.item(0).name == "BRACKET1"


class TestOneValueFields:
    """'set_name' and 'set_number' address ONE program; writing either to every match stamps one
    value on all of them, the same class as the empty-comment wipe-all."""

    def test_set_name_across_several_programs_is_refused_before_any_write(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1"), FakeNCP("P2"), FakeNCP("P3")])
        res = nc.handler(comment="", set_name="BRACKET")
        assert res["isError"] is True
        assert "all 3 matched NC programs" in res["message"]
        assert "Name one program with 'program'." in res["message"]
        assert [p.name for p in cam.ncPrograms] == ["P1", "P2", "P3"]

    def test_set_number_across_several_programs_is_refused_too(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1"), FakeNCP("P2")])
        res = nc.handler(comment="STAMP", set_number="2002")
        assert res["isError"] is True and "'set_number'" in res["message"]
        # the comment is the deliberate broadcast, and this refusal fires before it too
        assert cam.ncPrograms.item(0).parameters.itemByName(
            "nc_program_comment").expression == "'old'"

    def test_one_named_program_still_takes_both(self, monkeypatch):
        cam = _install(monkeypatch, [FakeNCP("P1"), FakeNCP("P2")])
        out = _payload(nc.handler(comment="", program="P2", set_name="X", set_number="7"))
        assert out["programs_changed"] == 1
        assert cam.ncPrograms.item(1).name == "X" and cam.ncPrograms.item(0).name == "P1"


class TestUnreadableName:
    def test_a_rename_on_a_program_whose_name_did_not_read_is_refused_before_any_write(
            self, monkeypatch):
        # the target's name is kept as READ (None), so this guard can see it; coercing it to ''
        # let the rename run against a name nothing could be compared with.
        ncp = _UnnamedNCP("P1", "'keep'")
        _install(monkeypatch, [ncp])
        res = nc.handler(comment="C", set_name="Renamed")
        assert res["isError"] is True and "does not read" in res["message"]
        assert ncp.parameters.itemByName("nc_program_comment").expression == "'keep'"

    def test_a_comment_on_that_program_publishes_the_name_as_null(self, monkeypatch):
        _install(monkeypatch, [_UnnamedNCP("P1", "'old'")])
        out = _payload(nc.handler(comment="Job 42"))
        rec = out["programs"][0]
        assert rec["program"] is None and rec["comment_after"] == "Job 42"
