"""Lint: every registered tool must declare a write-status annotation.

A tool's annotations carry ``readOnlyHint`` (read-only vs write) and, for writes,
``destructiveHint`` (hard to reverse). These are machine-checkable and reported by the server, so the
write-status is structured data rather than a ``WRITES.`` / ``Read-only.`` sentence in the description.
This test fails listing any tool that hasn't declared one.
"""

from conftest import register_all_tools


class TestWriteStatusDeclared:
    def test_every_tool_declares_read_only_hint(self):
        items = register_all_tools()
        assert items, "no tools registered"
        missing = []
        for it in items:
            ann = it.primitive.annotations
            if ann is None or ann.read_only is None:
                missing.append(it.get_name())
        assert not missing, (
            "tools missing a write-status declaration (call .reads() or .writes() on the Tool): "
            + ", ".join(sorted(missing))
        )

    def test_read_only_tools_are_not_destructive(self):
        # a read-only tool must not also be flagged destructive (contradiction)
        for it in register_all_tools():
            ann = it.primitive.annotations
            if ann and ann.read_only is True:
                assert not ann.destructive, f"{it.get_name()} is read-only but marked destructive"

    def test_hints_serialize_into_the_tool_payload(self):
        # the server emits annotations.readOnlyHint / destructiveHint in to_dict()
        for it in register_all_tools():
            ann = it.primitive.to_dict().get("annotations", {})
            assert "readOnlyHint" in ann, f"{it.get_name()} does not serialize readOnlyHint"
