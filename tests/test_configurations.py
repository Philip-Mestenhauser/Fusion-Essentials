"""Unit tests for ``configurations.py`` pure logic.

Targets: ``_row_summary`` (the ``is_active`` comparison and its None-safety),
``_find_row`` (name-first-then-id lookup), and ``_collect``'s row-cap
truncation boundary.
"""

from types import SimpleNamespace

from conftest import load_tool

cfg = load_tool("configurations")


# ── _row_summary: is_active flag ───────────────────────────────────────────

class TestRowSummary:
    def test_marks_active_row(self):
        row = SimpleNamespace(name="Large", id="row-2", index=1)
        out = cfg._row_summary(row, active_id="row-2")
        assert out["is_active"] is True
        assert out["name"] == "Large"

    def test_non_active_row(self):
        row = SimpleNamespace(name="Small", id="row-1", index=0)
        out = cfg._row_summary(row, active_id="row-2")
        assert out["is_active"] is False

    def test_none_id_is_never_active(self):
        # A row with no id must not match a None active_id (the `rid is not None`
        # guard) — otherwise an unidentified row would falsely read as active.
        row = SimpleNamespace(name="?", id=None, index=0)
        out = cfg._row_summary(row, active_id=None)
        assert out["is_active"] is False


# ── _find_row: name first, then id ─────────────────────────────────────────

class _Table:
    def __init__(self, rows):
        self.rows = rows


def _row(name, rid):
    return SimpleNamespace(name=name, id=rid)


class TestFindRow:
    def test_match_by_name(self):
        table = _Table([_row("Large", "r1"), _row("Small", "r2")])
        assert cfg._find_row(table, "Small").id == "r2"

    def test_match_by_id_when_no_name_matches(self):
        table = _Table([_row("Large", "r1"), _row("Small", "r2")])
        assert cfg._find_row(table, "r1").name == "Large"

    def test_name_wins_over_id_collision(self):
        # If a target string matches one row's name and another row's id, the
        # name match takes precedence (name loop runs first).
        table = _Table([_row("r2", "rX"), _row("Small", "r2")])
        found = cfg._find_row(table, "r2")
        assert found.id == "rX"   # the row whose *name* is "r2"

    def test_no_match_returns_none(self):
        table = _Table([_row("Large", "r1")])
        assert cfg._find_row(table, "nope") is None


# ── _collect: truncation boundary ──────────────────────────────────────────

class TestCollectTruncation:
    def test_truncates_at_row_cap(self, monkeypatch):
        monkeypatch.setattr(cfg, "_MAX_ROWS", 3)
        rows = [_row(f"cfg{i}", f"id{i}") for i in range(10)]
        table = SimpleNamespace(
            activeRow=None, rows=rows, columns=[],
            name="T", id="t1",
        )
        out = cfg._collect(table)
        assert out["configuration_count"] == 3        # capped
        assert out["truncated"] is True

    def test_no_truncation_under_cap(self, monkeypatch):
        monkeypatch.setattr(cfg, "_MAX_ROWS", 100)
        rows = [_row(f"cfg{i}", f"id{i}") for i in range(4)]
        table = SimpleNamespace(
            activeRow=None, rows=rows, columns=[],
            name="T", id="t1",
        )
        out = cfg._collect(table)
        assert out["configuration_count"] == 4
        # The 'truncated' key is only emitted when truncation occurred.
        assert out.get("truncated", False) is False
