"""Tests for the write-document binding guard (_write_guard) - the concurrency targeting fix.

The guard wraps every WRITE handler: an optional expect_document REFUSES the write if the active doc
moved (active_document_changed), and every successful write result is stamped with acted_on={name,urn}.
Read tools are untouched. We patch the guard's _active_identity seam to a known (name, urn).
"""

import json

from conftest import load_tool

wg = load_tool("_write_guard")


def _set_active(name, urn):
    wg._active_identity = lambda: (name, urn)


def _ok(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _decode(result):
    return json.loads(result["content"][0]["text"])


class TestActedOnStamp:
    def test_successful_write_is_stamped(self):
        _set_active("Bracket", "urn:lineage:abc")
        h = wg.wrap(lambda **kw: _ok({"created": True}))
        out = _decode(h())
        assert out["created"] is True
        assert out["acted_on"] == {"name": "Bracket", "document_id": "urn:lineage:abc"}

    def test_error_result_is_not_stamped(self):
        _set_active("Bracket", "urn:abc")
        h = wg.wrap(lambda **kw: {"content": [{"type": "text", "text": "boom"}],
                                  "isError": True, "message": "boom"})
        out = h()
        assert out["isError"] is True and "acted_on" not in out["content"][0]["text"]

    def test_handler_does_not_see_expect_document(self):
        _set_active("Bracket", "urn:abc")
        seen = {}
        h = wg.wrap(lambda **kw: seen.update(kw) or _ok({"ok": True}))
        h(expect_document="Bracket", distance=5)
        assert "expect_document" not in seen and seen == {"distance": 5}   # consumed by the guard


class TestExpectDocumentGuard:
    def test_match_by_name_proceeds(self):
        _set_active("Bracket", "urn:abc")
        called = {"n": 0}
        h = wg.wrap(lambda **kw: called.update(n=1) or _ok({"created": True}))
        out = _decode(h(expect_document="Bracket"))
        assert called["n"] == 1 and out["created"] is True

    def test_match_by_urn_proceeds(self):
        _set_active("Bracket", "urn:lineage:abc")
        h = wg.wrap(lambda **kw: _ok({"created": True}))
        out = _decode(h(expect_document="urn:lineage:abc"))
        assert out["created"] is True

    def test_mismatch_refuses_without_calling_handler(self):
        _set_active("OtherDoc", "urn:other")
        called = {"n": 0}
        h = wg.wrap(lambda **kw: called.update(n=1) or _ok({"created": True}))
        res = h(expect_document="Bracket")
        assert called["n"] == 0                                  # the handler NEVER ran (no mutation)
        assert res["isError"] is True
        payload = _decode(res)
        assert payload["blocked_by"] == ["active_document_changed"]
        assert payload["expected"] == "Bracket"
        assert payload["actual"] == {"name": "OtherDoc", "document_id": "urn:other"}
        assert payload["requires"]["tool"] == "doc_activate"

    def test_omitted_expect_document_proceeds(self):
        _set_active("Whatever", "urn:x")
        h = wg.wrap(lambda **kw: _ok({"created": True}))
        out = _decode(h())                                       # no expect_document -> unchanged behavior
        assert out["created"] is True and out["acted_on"]["name"] == "Whatever"


class TestIntegrationThroughItem:
    def test_write_tool_gains_expect_document_read_does_not(self):
        # create_tool_item wraps write handlers + adds the arg; read tools are untouched.
        ex = load_tool("model_extrude")
        assert "expect_document" in ex.extrude_tool.to_dict()["inputSchema"]["properties"]
        dg = load_tool("design_get")
        assert "expect_document" not in dg.tool.to_dict()["inputSchema"]["properties"]
