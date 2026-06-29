"""Unit tests for ``cam_set_nc_comment.handler`` — the empty-input guard and
multi-program behaviour.

``comment`` defaults to ``""`` (never ``None``). The handler must refuse when there is genuinely
nothing to write (empty comment AND empty set_name) rather than writing an empty comment to every NC
program; an explicit non-empty comment must still go through.
"""

import json

from conftest import load_tool

nc = load_tool("cam_set_nc_comment")


# ── fakes mimicking adsk.cam NCProgram / CAMParameters ──────────────────────

class FakeParam:
    def __init__(self, expr="'old'", editable=True):
        self.expression = expr
        self.isEditable = editable


class FakeParams:
    def __init__(self, **named):
        self._p = named
    def itemByName(self, name):
        return self._p.get(name)


class FakeNCP:
    def __init__(self, name, comment="'old'", editable=True):
        self.name = name
        self.parameters = FakeParams(
            nc_program_comment=FakeParam(comment, editable),
            nc_program_name=FakeParam("'" + name + "'", editable),
        )


class FakeNCPrograms:
    def __init__(self, programs):
        self._p = list(programs)
    @property
    def count(self):
        return len(self._p)
    def item(self, i):
        return self._p[i]


class FakeCAM:
    def __init__(self, programs):
        self.ncPrograms = FakeNCPrograms(programs)


def _install(programs):
    cam = FakeCAM(programs)
    nc._get_cam = lambda: (cam, None)
    return cam


def _payload(res):
    return json.loads(res["content"][0]["text"]) if not res.get("isError") else None


# ── the guard ───────────────────────────────────────────────────────────────

class TestEmptyInputGuard:
    def test_empty_comment_and_no_set_name_is_refused(self):
        # the wipe-everything case: previously slipped through (comment defaults to "")
        cam = _install([FakeNCP("P1", "'keep me'")])
        res = nc.handler(comment="", program="", set_name="")
        assert res["isError"] is True
        # and it must NOT have touched the existing comment
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'keep me'"

    def test_whitespace_only_comment_no_set_name_refused(self):
        cam = _install([FakeNCP("P1", "'keep me'")])
        res = nc.handler(comment="   ", program="", set_name="")
        assert res["isError"] is True
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'keep me'"

    def test_real_comment_goes_through(self):
        cam = _install([FakeNCP("P1")])
        res = nc.handler(comment="Job 42", program="P1")
        assert res["isError"] is False
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'Job 42'"

    def test_set_name_only_is_allowed(self):
        # no comment, but renaming IS a valid intent
        cam = _install([FakeNCP("P1")])
        res = nc.handler(comment="", program="P1", set_name="NewName")
        assert res["isError"] is False
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_name").expression == "'NewName'"


# ── multi-program pre-validation (rollback concern) ─────────────────────────

class TestMultiProgramPreValidation:
    def test_uneditable_program_aborts_before_any_write(self):
        # P2's comment is locked. The loop must NOT mutate P1 and then fail on P2 —
        # it pre-checks editability so nothing is half-applied.
        cam = _install([FakeNCP("P1", "'a'"), FakeNCP("P2", "'b'", editable=False)])
        res = nc.handler(comment="STAMP", program="")   # all programs
        assert res["isError"] is True
        # P1 must be untouched (no partial application)
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'a'"

    def test_all_editable_applies_to_all(self):
        cam = _install([FakeNCP("P1"), FakeNCP("P2")])
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
    def test_targets_only_named_program(self):
        cam = _install([FakeNCP("P1", "'a'"), FakeNCP("P2", "'b'")])
        out = _payload(nc.handler(comment="NEW", program="P2"))
        assert out["programs_changed"] == 1
        assert out["programs"][0]["program"] == "P2"
        # P1 untouched
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_comment").expression == "'a'"
        assert cam.ncPrograms.item(1).parameters.itemByName("nc_program_comment").expression == "'NEW'"

    def test_before_after_reported_unquoted(self):
        _install([FakeNCP("P1", "'old job'")])
        out = _payload(nc.handler(comment="new job", program="P1"))
        rec = out["programs"][0]
        assert rec["comment_before"] == "old job"     # unquoted in the report
        assert rec["comment_after"] == "new job"

    def test_unknown_program_lists_available(self):
        _install([FakeNCP("P1"), FakeNCP("P2")])
        res = nc.handler(comment="X", program="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"]
        assert "P1" in res["message"] and "P2" in res["message"]

    def test_no_nc_programs_errors(self):
        _install([])
        res = nc.handler(comment="X")
        assert res["isError"] is True and "no nc programs" in res["message"].lower()

    def test_comment_and_name_both_set(self):
        cam = _install([FakeNCP("P1", "'oldc'")])
        out = _payload(nc.handler(comment="C", program="P1", set_name="Renamed"))
        rec = out["programs"][0]
        assert rec["comment_after"] == "C"
        assert rec["name_after"] == "Renamed"
        assert out["set_name"] == "Renamed"
        assert cam.ncPrograms.item(0).parameters.itemByName("nc_program_name").expression == "'Renamed'"

    def test_uneditable_name_aborts_before_any_write(self):
        # set_name targets nc_program_name; if it's locked, abort before changing the comment.
        ncp = FakeNCP("P1", "'keepc'")
        ncp.parameters.itemByName("nc_program_name").isEditable = False
        _install([ncp])
        res = nc.handler(comment="C", program="P1", set_name="X")
        assert res["isError"] is True
        # comment must be untouched (aborted in the pre-validation pass)
        assert ncp.parameters.itemByName("nc_program_comment").expression == "'keepc'"
