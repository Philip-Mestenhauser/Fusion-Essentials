"""Lint: every registered tool must declare a write-status annotation.

A tool's annotations carry ``readOnlyHint`` (read-only vs write) and, for writes,
``destructiveHint`` (hard to reverse). These are machine-checkable and reported by the server, so the
write-status is structured data rather than a ``WRITES.`` / ``Read-only.`` sentence in the description.
This lint owns exactly one check: every tool DECLARES a hint at registration time. What the hints
look like ON THE WIRE - annotations present in every tools/list entry, readOnlyHint serialized, and
the read-only/destructive contradiction - is asserted in ``test_wire_shape.py``, which drives the
real ``SimpleMCPServer._handle_tools_list`` rather than re-reading the registry.
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
