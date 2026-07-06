"""Wire-level tests for SimpleMCPServer's JSON-RPC protocol behavior.

Covers protocol version negotiation on initialize, and the tools/call contract: an unknown
tool name is a JSON-RPC protocol error, but an unknown/missing argument or a handler exception
comes back as a normal result with isError=true (per the MCP spec, a tool EXECUTION failure is
not a protocol-level failure) so the calling agent can read it and self-correct.
"""

import asyncio

import pytest

from conftest import load_mcp_server


@pytest.fixture
def mcp_server_module():
    """The real server module (mcp_server.py), loaded once for the whole test session."""
    return load_mcp_server()


@pytest.fixture
def server(mcp_server_module):
    """A fresh SimpleMCPServer with no tools registered."""
    return mcp_server_module.SimpleMCPServer()


def _make_tool_item(name, handler, *, required=("a",), optional=("b",), run_on_main_thread=False):
    """Build a synthetic tool Item via the real Tool/Item classes.

    `required` properties are added to the schema's required list; `optional` are schema
    properties that may be omitted. Defaults give one of each, matching the fixture tools this
    file drives most tests with.
    """
    from mcpServer.mcp_primitives.item import Item
    from mcpServer.mcp_primitives.tool import Tool

    tool = Tool.create_simple(name=name, description="synthetic test tool")
    for prop in required:
        tool.add_input_property(prop, {"type": "string"}).add_required_input(prop)
    for prop in optional:
        tool.add_input_property(prop, {"type": "string"})
    return Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=run_on_main_thread)


def _bare_tool_item(name, handler, run_on_main_thread=False):
    """A tool Item built from a Tool with NO input_schema at all (properties/required absent)."""
    from mcpServer.mcp_primitives.item import Item
    from mcpServer.mcp_primitives.tool import Tool

    tool = Tool(name=name, description="synthetic bare tool")
    return Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=run_on_main_thread)


def _ok():
    return {"content": [{"type": "text", "text": "ok"}], "isError": False}


class TestInitializeProtocolVersionNegotiation:
    def test_initialize_with_supported_version_echoes_it(self, server):
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }))
        assert response["result"]["protocolVersion"] == "2025-03-26"

    def test_initialize_with_unsupported_version_responds_with_supported_version(self, server):
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }))
        assert response["result"]["protocolVersion"] == "2025-03-26"

    def test_initialize_with_no_protocol_version_responds_with_default(self, server):
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        }))
        assert response["result"]["protocolVersion"] == "2025-03-26"


class TestToolsCallUnknownTool:
    def test_unknown_tool_is_a_protocol_error_with_code_32602(self, server):
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "does_not_exist", "arguments": {}},
        }))
        assert "result" not in response
        assert response["error"]["code"] == -32602


class TestToolsCallArgumentValidation:
    def test_unknown_argument_is_an_error_result_and_handler_not_called(self, server):
        calls = {"n": 0}

        def handler(**kwargs):
            calls["n"] += 1
            return _ok()

        server.register(_make_tool_item("x", handler))
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "x", "arguments": {"a": "1", "c": "surprise"}},
        }))
        result = response["result"]
        assert result["isError"] is True
        assert "'c'" in result["message"]
        assert "a" in result["message"] and "b" in result["message"]
        assert calls["n"] == 0

    def test_missing_required_argument_is_an_error_result_and_handler_not_called(self, server):
        calls = {"n": 0}

        def handler(**kwargs):
            calls["n"] += 1
            return _ok()

        server.register(_make_tool_item("x", handler))
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "x", "arguments": {"b": "optional-only"}},
        }))
        result = response["result"]
        assert result["isError"] is True
        assert "'a'" in result["message"]
        assert calls["n"] == 0

    def test_valid_call_with_optional_argument_omitted_succeeds(self, server):
        seen = {}
        server.register(_make_tool_item("x", lambda **kw: seen.update(kw) or _ok()))
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "x", "arguments": {"a": "1"}},
        }))
        assert response["result"]["isError"] is False
        assert seen == {"a": "1"}

    def test_empty_schema_tool_with_no_arguments_succeeds(self, server):
        calls = {"n": 0}
        server.register(_bare_tool_item("noop", lambda **kw: calls.update(n=calls["n"] + 1) or _ok()))
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "noop", "arguments": {}},
        }))
        assert response["result"]["isError"] is False
        assert calls["n"] == 1

    def test_omitted_arguments_default_to_empty_dict(self, server):
        seen = {"kwargs": None}
        server.register(_bare_tool_item("noop", lambda **kw: seen.update(kwargs=kw) or _ok()))
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "noop"},
        }))
        assert response["result"]["isError"] is False
        assert seen["kwargs"] == {}


class TestToolsCallHandlerExceptions:
    def test_handler_exception_becomes_iserror_result_not_protocol_error(self, server):
        def handler(**kwargs):
            raise ValueError("boom")

        server.register(_make_tool_item("x", handler))
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "x", "arguments": {"a": "1"}},
        }))
        assert "error" not in response
        result = response["result"]
        assert result["isError"] is True
        assert "Tool 'x' failed: boom" in result["content"][0]["text"]
        assert "Tool 'x' failed: boom" in result["message"]


class TestNotificationsAndPing:
    def test_notification_without_id_returns_none(self, server):
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        }))
        assert response is None

    def test_ping_returns_empty_result(self, server):
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "ping",
        }))
        assert response["result"] == {}
