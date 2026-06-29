"""Unit tests for ``configurations.py`` pure logic.

Targets: ``_row_summary`` (the ``is_active`` comparison and its None-safety),
``_find_row`` (name-first-then-id lookup), and ``_collect``'s row-cap
truncation boundary.
"""

from types import SimpleNamespace

from conftest import load_tool

cfg = load_tool("design_get_configurations")


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


# ── _column_summary: title (NOT name) + type ───────────────────────────────

class TestColumnSummary:
    def test_reads_title_and_type(self):
        col = SimpleNamespace(title="Variant", id="c1", index=0)
        # the class name flows into 'type' (the tool uses type(col).__name__)
        out = cfg._column_summary(col)
        assert out["title"] == "Variant"
        assert out["id"] == "c1"
        assert out["index"] == 0
        assert out["type"] == "SimpleNamespace"


# ── _collect: active flagging + columns ────────────────────────────────────

class TestCollectActiveAndColumns:
    def test_marks_the_active_row_and_names_it(self):
        rows = [_row("Small", "r1"), _row("Large", "r2")]
        active = rows[1]
        cols = [SimpleNamespace(title="Size", id="c1", index=0)]
        table = SimpleNamespace(activeRow=active, rows=rows, columns=cols, name="Sizes", id="t1")
        out = cfg._collect(table)
        assert out["active_configuration"] == "Large"
        assert out["configuration_count"] == 2
        flags = {r["name"]: r["is_active"] for r in out["configurations"]}
        assert flags == {"Small": False, "Large": True}
        assert out["columns"][0]["title"] == "Size"
        assert out["table_name"] == "Sizes"

    def test_no_active_row_leaves_active_null(self):
        table = SimpleNamespace(activeRow=None, rows=[_row("A", "a")], columns=[],
                                name="T", id="t1")
        out = cfg._collect(table)
        assert out["active_configuration"] is None
        assert out["configurations"][0]["is_active"] is False


# ── handler: read / activate / guards ──────────────────────────────────────

import json


class _Row:
    def __init__(self, name, rid, activate_returns=True):
        self.name = name
        self.id = rid
        self.index = 0
        self._activate_returns = activate_returns
        self.activated = False

    def activate(self):
        self.activated = True
        return self._activate_returns


class _Top:
    def __init__(self, rows, active=None, name="Table", tid="t1"):
        self.rows = rows
        self.columns = []
        self.activeRow = active
        self.name = name
        self.id = tid


class _Design:
    def __init__(self, table):
        self.configurationTopTable = table


def _install(monkeypatch, table):
    design = _Design(table) if table is not None else SimpleNamespace(configurationTopTable=None)
    monkeypatch.setattr(cfg._common, "design", lambda: design)
    return design


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestHandler:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(cfg._common, "design", lambda: None)
        res = cfg.handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_not_a_configured_design(self, monkeypatch):
        _install(monkeypatch, None)            # configurationTopTable is None
        res = cfg.handler()
        assert res["isError"] is True and "not a Configured Design" in res["message"]

    def test_read_returns_table(self, monkeypatch):
        rows = [_Row("Small", "r1"), _Row("Large", "r2")]
        _install(monkeypatch, _Top(rows, active=rows[0]))
        out = _payload(cfg.handler())
        assert out["configuration_count"] == 2
        assert out["active_configuration"] == "Small"

    def test_activate_switches_and_reports(self, monkeypatch):
        rows = [_Row("Small", "r1"), _Row("Large", "r2")]
        table = _Top(rows, active=rows[0])
        _install(monkeypatch, table)
        out = _payload(cfg.handler(activate="Large"))
        assert out["activated"] is True
        assert out["requested"] == "Large"
        assert out["previous_active"] == "Small"
        assert rows[1].activated is True

    def test_activate_unknown_lists_available(self, monkeypatch):
        rows = [_Row("Small", "r1"), _Row("Large", "r2")]
        _install(monkeypatch, _Top(rows, active=rows[0]))
        res = cfg.handler(activate="Huge")
        assert res["isError"] is True
        assert "Huge" in res["message"]
        assert "Small" in res["message"] and "Large" in res["message"]

    def test_activate_returns_false_is_an_error(self, monkeypatch):
        rows = [_Row("Small", "r1"), _Row("Large", "r2", activate_returns=False)]
        _install(monkeypatch, _Top(rows, active=rows[0]))
        res = cfg.handler(activate="Large")
        assert res["isError"] is True and "returned false" in res["message"]
