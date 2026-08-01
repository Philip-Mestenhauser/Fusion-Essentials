# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP Item wrapper that bundles a primitive (Tool) with its handler."""

from .tool import Tool


class Item:
    """Bundles an MCP primitive with the callable that fulfills it.

    run_on_main_thread defaults to True: any handler that touches the Fusion API
    MUST run on Fusion's main thread (marshalled via TaskManager). Only set this
    False for handlers that are pure Python and never call adsk.*.
    """

    def __init__(self, primitive: Tool, handler: callable, run_on_main_thread: bool = True,
                 enforce_timeout: bool = True):
        if not isinstance(primitive, Tool):
            raise ValueError("Primitive must be a Tool instance")
        if not callable(handler):
            raise ValueError("Handler must be a callable function")
        self.name = primitive.name
        self.primitive = primitive
        self.handler = handler
        self.run_on_main_thread = run_on_main_thread
        # enforce_timeout=False exempts a tool from the server's main-thread task timeout. Use it
        # ONLY for tools whose work cannot be interrupted AND would still commit if we "timed out"
        # (e.g. sys_execute_script) - timing those out would report a false failure for a change
        # that actually applied. Default True keeps the safety timeout for everything else.
        self.enforce_timeout = enforce_timeout

    def get_name(self) -> str:
        return self.primitive.name

    def get_type(self) -> str:
        return "tool"

    def to_dict(self) -> dict:
        return self.primitive.to_dict()

    def __str__(self) -> str:
        return f"Item(type='{self.get_type()}', name='{self.get_name()}')"

    def __repr__(self) -> str:
        return f"Item(primitive={self.primitive}, handler={self.handler})"

    @classmethod
    def create_tool_item(cls, tool: Tool, handler: callable, run_on_main_thread: bool = True,
                         enforce_timeout: bool = True, write: str = None,
                         postconditions: list = None) -> 'Item':
        """Build a tool Item. ``write`` declares the tool's write-status, applied to the tool's
        annotations (readOnlyHint / destructiveHint) so the server reports it as structured data:
          'read'        -> read-only (does not modify state)
          'write'       -> modifies state
          'destructive' -> a hard-to-reverse write (delete, history-discarding conversion, close doc)
        Every tool must pass one (enforced by test_write_status_annotations.py)."""
        if write == "read":
            tool.reads()
        elif write == "write":
            tool.writes()
        elif write == "destructive":
            tool.writes(destructive=True)
        elif write is not None:
            raise ValueError(f"write must be 'read'/'write'/'destructive', got {write!r}")
        # WRITE-DOCUMENT BINDING (the concurrency guard): a write can land on the WRONG document if the
        # active doc moved since the agent's read (async open / a human switching tabs). Wrap every
        # write/destructive handler with the shared guard - it accepts an optional expect_document
        # (REFUSE on mismatch) and stamps acted_on on the result. One seam covers all write tools; read
        # tools are untouched. (Lazy import: item.py is a primitive; the guard lives in tools/.)
        if write in ("write", "destructive"):
            # POSTCONDITION KERNEL (verify-the-effect): wrapped INSIDE the write guard so the guard
            # stamps acted_on on the verified result. Runs capture -> handler -> verify and converts a
            # success whose declared effect did not take into an error (see tools/_assert.py). A read
            # tool never verifies (nothing mutated); postconditions on a read are a wiring mistake.
            if postconditions:
                from ..tools import _assert
                handler = _assert.wrap(handler, postconditions)
            from ..tools import _write_guard
            handler = _write_guard.wrap(handler)
            tool.add_input_property(*_write_guard.EXPECT_DOCUMENT_PROP)
        elif postconditions:
            raise ValueError(f"postconditions declared on a non-write tool '{tool.name}' - a read "
                             "mutates nothing to verify")
        # READ-DOCUMENT STAMP: every read result reports the document it read from
        # ('active_document') so a read taken against the wrong active document is visible in the
        # payload, mirroring the write side's acted_on. Main-thread tools only - the identity read
        # touches adsk, which a pure-Python off-thread handler must never do.
        if write == "read" and run_on_main_thread:
            from ..tools import _write_guard
            handler = _write_guard.wrap_read(handler)
        return cls(primitive=tool, handler=handler, run_on_main_thread=run_on_main_thread,
                   enforce_timeout=enforce_timeout)
