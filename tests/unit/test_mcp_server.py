"""Wire-level tests for SimpleMCPServer's JSON-RPC protocol behavior.

Covers protocol version negotiation on initialize; the non-object-body guard (a batch ARRAY or any
other non-dict body -> one clean -32600 error object, never a raise); and the tools/call contract:
an unknown tool name is a JSON-RPC protocol error, but an unknown/missing argument, an out-of-enum
argument value, or a handler exception comes back as a normal result with isError=true (per the MCP
spec, a tool EXECUTION failure is not a protocol-level failure) so the calling agent can read it
and self-correct.
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
    # strict: the arg gate only rejects unknown keys where the schema DECLARES strictness, so the
    # fixture declares it; leniency has its own dedicated test.
    tool.strict_schema()
    return Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=run_on_main_thread)


def _bare_tool_item(name, handler, run_on_main_thread=False):
    """A tool Item built from a Tool with NO input_schema at all (properties/required absent)."""
    from mcpServer.mcp_primitives.item import Item
    from mcpServer.mcp_primitives.tool import Tool

    tool = Tool(name=name, description="synthetic bare tool")
    return Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=run_on_main_thread)


def _ok():
    return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def _enum_tool_item(name, handler, run_on_main_thread=False):
    """A tool with one scalar enum prop ('kind'), one array-of-enum prop ('include'), and one free
    string prop ('target') - the three shapes the server-side enum gate distinguishes."""
    from mcpServer.mcp_primitives.item import Item
    from mcpServer.mcp_primitives.tool import Tool

    tool = Tool.create_simple(name=name, description="synthetic enum tool")
    tool.add_input_property("kind", {"type": "string", "enum": ["cylinder_face", "planar_face"]})
    tool.add_input_property("include", {"type": "array",
                                        "items": {"type": "string", "enum": ["versions", "xref_tree"]}})
    tool.add_input_property("target", {"type": "string"})
    tool.strict_schema()
    return Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=run_on_main_thread)


def _call(server, name, arguments):
    return asyncio.run(server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }))


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


class TestNonObjectRequestBody:
    def test_batch_array_body_returns_clean_32600_not_a_raise(self, server):
        # a JSON-RPC batch (a list of requests) is not implemented; it must come back as ONE
        # well-formed error object, not an AttributeError -> HTTP 500.
        response = asyncio.run(server.handle_request([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]))
        assert response["error"]["code"] == -32600
        assert response["id"] is None                       # a batch has no single id to echo
        assert "batch" in response["error"]["message"].lower()
        assert "result" not in response

    def test_empty_array_body_returns_clean_32600(self, server):
        response = asyncio.run(server.handle_request([]))
        assert response["error"]["code"] == -32600
        assert response["id"] is None

    def test_non_dict_scalar_body_returns_clean_32600(self, server):
        response = asyncio.run(server.handle_request("ping"))
        assert response["error"]["code"] == -32600
        assert response["id"] is None
        assert "request object" in response["error"]["message"]

    def test_dict_body_is_unaffected_by_the_guard(self, server):
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 7, "method": "ping",
        }))
        assert response == {"jsonrpc": "2.0", "id": 7, "result": {}}


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
        assert result["content"][0]["text"] == result["message"]   # the field a real client reads
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
        assert result["content"][0]["text"] == result["message"]   # the field a real client reads
        assert calls["n"] == 0

    def test_lenient_schema_passes_unknown_keys_to_handler(self, server):
        # a tool that never declared additionalProperties=false stays lenient on the wire AND at
        # the gate - the schema must not promise leniency the server then refuses.
        seen = {}

        def handler(**kwargs):
            seen.update(kwargs)
            return _ok()

        item = _make_tool_item("lenient", handler)
        item.primitive.additional_properties = None    # undeclared = lenient, the wire default
        server.register(item)
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "lenient", "arguments": {"a": "1", "c": "extra"}},
        }))
        assert response["result"]["isError"] is False
        assert seen.get("c") == "extra"

    def test_schema_omitted_arg_reaches_the_handler(self, server, mcp_server_module, monkeypatch):
        # a strict tool may deliberately accept an off-schema kwarg (the handler answers with a
        # targeted redirect) - the gate lets a listed key through instead of shadowing it.
        seen = {}

        def handler(**kwargs):
            seen.update(kwargs)
            return _ok()

        monkeypatch.setattr(mcp_server_module, "_SCHEMA_OMITTED_ARGS",
                            {"x": frozenset({"c"})})
        server.register(_make_tool_item("x", handler))
        response = asyncio.run(server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "x", "arguments": {"a": "1", "c": "redirect-me"}},
        }))
        assert response["result"]["isError"] is False
        assert seen.get("c") == "redirect-me"

    def test_omitted_args_table_matches_reality(self, mcp_server_module):
        # every _SCHEMA_OMITTED_ARGS entry names a REAL registered tool whose real handler truly
        # accepts the kwarg - a stale entry (tool renamed, kwarg dropped) fails here.
        import inspect
        from conftest import register_all_tools
        items = {i.primitive.name: i for i in register_all_tools()}
        for tool_name, extras in mcp_server_module._SCHEMA_OMITTED_ARGS.items():
            assert tool_name in items, f"omitted-args entry for unknown tool '{tool_name}'"
            h = items[tool_name].handler
            while getattr(h, "__wrapped__", None) is not None:
                h = h.__wrapped__
            params = inspect.signature(h).parameters
            for extra in extras:
                assert extra in params, (
                    f"'{tool_name}' handler does not accept '{extra}' - stale omitted-args entry")

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


class TestToolsCallEnumValidation:
    def test_out_of_enum_value_is_a_named_error_and_handler_not_called(self, server):
        # a permissive client sends kind="faces" (not in the enum) and, without this gate, gets
        # silent wrong behavior (match_count:0) instead of a correction.
        calls = {"n": 0}

        def handler(**kwargs):
            calls["n"] += 1
            return _ok()

        server.register(_enum_tool_item("x", handler))
        result = _call(server, "x", {"kind": "faces"})["result"]
        assert result["isError"] is True
        assert calls["n"] == 0                              # a doomed call never dispatches
        assert "'kind'" in result["message"]                # names the property
        assert "'faces'" in result["message"]               # names the offending value
        assert "cylinder_face" in result["message"] and "planar_face" in result["message"]
        assert result["content"][0]["text"] == result["message"]

    def test_valid_enum_value_passes_through(self, server):
        seen = {}
        server.register(_enum_tool_item("x", lambda **kw: seen.update(kw) or _ok()))
        result = _call(server, "x", {"kind": "planar_face"})["result"]
        assert result["isError"] is False
        assert seen == {"kind": "planar_face"}

    def test_array_of_enum_prop_rejects_a_bad_element(self, server):
        server.register(_enum_tool_item("x", lambda **kw: _ok()))
        result = _call(server, "x", {"include": ["versions", "bogus_slice"]})["result"]
        assert result["isError"] is True
        assert "'include'" in result["message"] and "'bogus_slice'" in result["message"]
        assert "versions" in result["message"]              # the valid values are listed

    def test_array_of_enum_prop_accepts_all_valid_elements(self, server):
        server.register(_enum_tool_item("x", lambda **kw: _ok()))
        assert _call(server, "x", {"include": ["versions", "xref_tree"]})["result"]["isError"] is False

    def test_non_enum_property_is_not_gated(self, server):
        server.register(_enum_tool_item("x", lambda **kw: _ok()))
        assert _call(server, "x", {"target": "anything at all"})["result"]["isError"] is False

    def test_tool_without_enums_is_unaffected(self, server):
        # the default fixture tool declares no enum anywhere; every value passes the gate.
        server.register(_make_tool_item("plain", lambda **kw: _ok()))
        assert _call(server, "plain", {"a": "faces"})["result"]["isError"] is False

    def test_none_for_an_optional_enum_prop_is_not_rejected(self, server):
        # an explicit null = unset; the handler's default applies, same as omitting the key.
        server.register(_enum_tool_item("x", lambda **kw: _ok()))
        assert _call(server, "x", {"kind": None})["result"]["isError"] is False

    def test_unhashable_value_for_an_enum_prop_is_a_named_error_not_a_typeerror(self, server):
        server.register(_enum_tool_item("x", lambda **kw: _ok()))
        result = _call(server, "x", {"kind": ["planar_face"]})["result"]
        assert result["isError"] is True
        assert "'kind'" in result["message"]

    def test_enum_specs_are_precomputed_at_registration(self, server):
        # per-call cost is a dict lookup: the specs exist before any call is made.
        server.register(_enum_tool_item("x", lambda **kw: _ok()))
        specs = server._enum_specs["x"]
        assert specs["kind"]["set"] == frozenset({"cylinder_face", "planar_face"})
        assert specs["include"]["array"] is True
        assert "target" not in specs                        # free strings carry no enum spec


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
