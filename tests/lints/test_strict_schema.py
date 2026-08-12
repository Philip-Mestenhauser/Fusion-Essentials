"""Lint: every registered tool's inputSchema carries additionalProperties:false.

A tool without ``.strict_schema()`` silently DROPS a misspelled optional argument instead of
refusing it - the caller believes the option was applied while the handler ran on its default.
The write guard, the typed kinds, and every refusal in the fleet assume an argument that reaches
the handler was one the schema admits; ``additionalProperties: false`` is what makes that true at
the wire.

This walks the LIVE registry (the same one the server serves), so the offender list can never go
stale against a grep heuristic: whatever this lint names is missing the call, wherever its wiring
lives (a direct chain, ``create_with_string_input``, or a shared wiring helper).
"""

from conftest import register_all_tools


class TestStrictSchema:
    def test_every_tool_schema_refuses_unknown_arguments(self):
        offenders = []
        for it in register_all_tools():
            prim = getattr(it, "primitive", None)
            name = getattr(prim, "name", None) or "<unnamed>"
            if getattr(prim, "additional_properties", None) is not False:
                offenders.append(name)
        assert not offenders, (
            "tool(s) whose inputSchema does not refuse unknown arguments - append "
            ".strict_schema() to each tool's builder chain:\n  " + "\n  ".join(sorted(offenders)))
