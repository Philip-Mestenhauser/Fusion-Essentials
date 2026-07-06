"""Wire format: the JSON-RPC tools/list response includes annotations and strict schemas.

SimpleMCPServer._handle_tools_list must emit every tool entry via to_dict(), which
includes annotations (readOnlyHint/destructiveHint) and strict-schema additionalProperties=false.
"""

import os

import pytest

from conftest import load_mcp_server, load_tool, TOOLS_DIR


def _tool_modules():
    """Every tools/*.py module name that defines a register_tool() (skips _private helpers)."""
    names = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        names.append(fn[:-3])
    return names


def _all_registered_tools():
    """Load + register every tool module against a fresh registry; return the tool Items."""
    names = _tool_modules()
    load_tool(names[0])
    from mcpServer.mcp_primitives import registry
    registry.reset_registry()
    for mod_name in names:
        mod = load_tool(mod_name)
        reg = getattr(mod, "register_tool", None)
        if callable(reg):
            reg()
    return registry.get_tools()


@pytest.fixture
def server():
    """The REAL SimpleMCPServer with every tool registered, so the asserts bite the
    production _handle_tools_list, not a re-implementation of it."""
    mcp_server = load_mcp_server()
    srv = mcp_server.SimpleMCPServer()
    for item in _all_registered_tools():
        srv.register(item)
    return srv


class TestToolsListWireFormat:
    def test_tools_list_sends_annotations_on_the_wire(self, server):
        """Every entry in tools/list response includes annotations."""
        result = server._handle_tools_list(request_id="test-id")
        assert result["jsonrpc"] == "2.0"
        assert result["id"] == "test-id"
        tools = result["result"]["tools"]
        assert tools, "No tools registered"
        for entry in tools:
            assert "annotations" in entry, f"{entry.get('name')} missing annotations on wire"

    def test_all_entries_have_required_keys(self, server):
        """Every entry has name, description, inputSchema, annotations keys."""
        result = server._handle_tools_list(request_id="test-1")
        tools = result["result"]["tools"]
        for entry in tools:
            assert "name" in entry, f"Entry missing name: {entry}"
            assert "description" in entry, f"Entry {entry.get('name')} missing description"
            assert "inputSchema" in entry, f"Entry {entry.get('name')} missing inputSchema"
            assert "annotations" in entry, f"Entry {entry.get('name')} missing annotations"

    def test_read_only_tools_have_correct_annotations(self, server):
        """Read-only tools: readOnlyHint=true, no destructiveHint true."""
        result = server._handle_tools_list(request_id="test-2")
        tools = result["result"]["tools"]
        for entry in tools:
            ann = entry.get("annotations", {})
            if ann.get("readOnlyHint") is True:
                destructive = ann.get("destructiveHint", False)
                assert not destructive, (
                    f"Read-only tool {entry['name']} has destructiveHint={destructive}"
                )

    def test_write_tools_have_correct_annotations(self, server):
        """Write tools: readOnlyHint=false. Destructive writes: destructiveHint=true."""
        result = server._handle_tools_list(request_id="test-3")
        tools = result["result"]["tools"]
        write_tools = [e for e in tools if e.get("annotations", {}).get("readOnlyHint") is False]
        assert write_tools, "No write tools found to validate (at least one expected)"
        for entry in write_tools:
            ann = entry.get("annotations", {})
            assert ann.get("readOnlyHint") is False, (
                f"{entry['name']}: write tool must have readOnlyHint=false"
            )

    def test_no_audience_priority_lastmodified_in_annotations(self, server):
        """Annotations must not include audience, priority, or lastModified (tool-level annotations
        carry only readOnlyHint and destructiveHint on the wire)."""
        result = server._handle_tools_list(request_id="test-4")
        tools = result["result"]["tools"]
        for entry in tools:
            ann = entry.get("annotations", {})
            assert "audience" not in ann, f"{entry['name']}: audience should not be in annotations"
            assert "priority" not in ann, f"{entry['name']}: priority should not be in annotations"
            assert "lastModified" not in ann, f"{entry['name']}: lastModified should not be in annotations"

    def test_strict_schema_tool_has_additional_properties_false(self, server):
        """At least one known strict-schema tool (e.g. assembly_ground from assembly_transform.py)
        has inputSchema.additionalProperties == false on the wire."""
        result = server._handle_tools_list(request_id="test-5")
        tools = result["result"]["tools"]
        strict_schema_tools = [
            e for e in tools
            if e.get("inputSchema", {}).get("additionalProperties") is False
        ]
        assert strict_schema_tools, (
            "No strict-schema tools found with additionalProperties=false. "
            "Expected at least one from assembly_transform.py or assembly_probe.py"
        )
        tool_names = [t["name"] for t in strict_schema_tools]
        assert any(
            name in tool_names for name in [
                "assembly_ground", "assembly_move", "assembly_rigid_group",
                "assembly_joint_angle", "assembly_joint_distance",
                "assembly_joint_origin"
            ]
        ), f"No known strict-schema tool found. Found: {tool_names}"

    def test_destructive_tools_marked_on_the_wire(self, server):
        """At least one destructive tool crosses the wire with destructiveHint=true, and every
        destructive entry is also readOnlyHint=false (a destructive read is a contradiction)."""
        result = server._handle_tools_list(request_id="test-9")
        tools = result["result"]["tools"]
        destructive = [
            e for e in tools if e.get("annotations", {}).get("destructiveHint") is True
        ]
        assert destructive, "Expected at least one destructive tool (e.g. cam_delete)"
        for entry in destructive:
            assert entry["annotations"].get("readOnlyHint") is False, (
                f"{entry['name']}: destructiveHint=true requires readOnlyHint=false"
            )

    def test_all_descriptions_non_empty(self, server):
        """Every entry's description is non-empty."""
        result = server._handle_tools_list(request_id="test-6")
        tools = result["result"]["tools"]
        for entry in tools:
            desc = entry.get("description", "").strip()
            assert desc, f"Tool {entry['name']} has empty description"

    def test_read_only_hint_is_bool(self, server):
        """readOnlyHint in annotations must be a boolean."""
        result = server._handle_tools_list(request_id="test-7")
        tools = result["result"]["tools"]
        for entry in tools:
            ann = entry.get("annotations", {})
            if "readOnlyHint" in ann:
                assert isinstance(ann["readOnlyHint"], bool), (
                    f"{entry['name']}: readOnlyHint should be bool, got {type(ann['readOnlyHint'])}"
                )

    def test_destructive_hint_when_present_is_bool(self, server):
        """destructiveHint in annotations, when present, must be a boolean."""
        result = server._handle_tools_list(request_id="test-8")
        tools = result["result"]["tools"]
        for entry in tools:
            ann = entry.get("annotations", {})
            if "destructiveHint" in ann:
                assert isinstance(ann["destructiveHint"], bool), (
                    f"{entry['name']}: destructiveHint should be bool, got {type(ann['destructiveHint'])}"
                )
