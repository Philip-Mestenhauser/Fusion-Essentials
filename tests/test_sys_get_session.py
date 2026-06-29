"""Unit tests for the ``sys_get_session.py`` MCP tool.

sys_get_session is the read-only proof-of-life tool: it reports the live session
(version / active doc / workspace / product / design units / root / occ count).
Its only logic is best-effort probing (each field guarded so a missing API
property leaves a ``None`` rather than raising) and emitting the standard MCP
result envelope. These tests pin both: the per-field guards and that the result
goes through the shared ``_common.ok`` contract (one response shape, not a
hand-rolled dict) — the latter is what the helper-consolidation pass enforces.
"""

import json

from conftest import load_tool

ses = load_tool("sys_get_session")


def _payload(result):
    """Decode the JSON payload carried in a _common.ok() result envelope."""
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    return json.loads(result["content"][0]["text"])


class _FakeWorkspace:
    def __init__(self, name):
        self.name = name


class _FakeUI:
    def __init__(self, workspace=None):
        self.activeWorkspace = workspace


class _FakeProduct:
    def __init__(self, product_type):
        self.productType = product_type


class _FakeDoc:
    def __init__(self, name):
        self.name = name


class _FakeApp:
    """Only the attributes sys_get_session reads."""

    def __init__(self, version="9.9.9", doc=None, ui=None, product=None):
        self.version = version
        self.activeDocument = doc
        self.userInterface = ui
        self.activeProduct = product


def _install(monkeypatch, **kw):
    app = _FakeApp(**kw)
    monkeypatch.setattr(ses, "app", app)
    return app


class TestEnvelope:
    def test_result_uses_the_common_ok_contract(self, monkeypatch):
        # No live design: every design field stays None, but the envelope is the
        # shared ok() shape — content[text]=json, isError False, no hand-rolled dict.
        _install(monkeypatch, version="2026.1.0")
        res = ses.handler()
        assert set(res.keys()) == {"content", "isError"}
        assert res["isError"] is False
        body = _payload(res)
        assert body["fusion_version"] == "2026.1.0"


class TestReadOnlyProbe:
    def test_reports_active_document_and_workspace(self, monkeypatch):
        _install(
            monkeypatch,
            doc=_FakeDoc("MyPart"),
            ui=_FakeUI(_FakeWorkspace("Design")),
            product=_FakeProduct("DesignProductType"),
        )
        body = _payload(ses.handler())
        assert body["active_document"] == "MyPart"
        assert body["active_workspace"] == "Design"
        assert body["active_product"] == "DesignProductType"

    def test_missing_fields_are_none_not_errors(self, monkeypatch):
        # No active doc, no UI, no product -> graceful Nones, still a success.
        _install(monkeypatch)
        body = _payload(ses.handler())
        assert body["active_document"] is None
        assert body["active_workspace"] is None
        assert body["active_product"] is None
        assert body["design_units"] is None
        assert body["root_component_name"] is None

    def test_a_raising_property_is_swallowed(self, monkeypatch):
        class _Boom:
            @property
            def activeWorkspace(self):
                raise RuntimeError("UI not ready")

        _install(monkeypatch, doc=_FakeDoc("X"), ui=_Boom())
        body = _payload(ses.handler())
        # The workspace probe failed but the doc field still came through.
        assert body["active_document"] == "X"
        assert body["active_workspace"] is None
