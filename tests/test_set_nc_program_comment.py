"""Unit tests for ``cam_set_nc_comment.handler`` — the empty-input guard and
multi-program behaviour.

The bug this pins (maintainer block #2): ``comment`` defaults to ``""`` (never
``None``), so the original ``if comment is None ...`` guard was DEAD CODE — a call
with ``comment=""`` and no ``program`` silently wrote an empty comment to EVERY NC
program, wiping them all. The guard must refuse when there is genuinely nothing to
write (empty comment AND empty set_name), and an explicit non-empty comment must
still go through.
"""

import json

from conftest import load_tool

nc = load_tool("set_nc_program_comment")


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
