# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP Item wrapper that bundles a primitive (Tool/Resource/Prompt) with its handler."""

from typing import Any, Union
from .tool import Tool
from .resource import Resource
from .prompt import Prompt


class Item:
    """Bundles an MCP primitive with the callable that fulfills it.

    run_on_main_thread defaults to True: any handler that touches the Fusion API
    MUST run on Fusion's main thread (marshalled via TaskManager). Only set this
    False for handlers that are pure Python and never call adsk.*.
    """

    def __init__(self, primitive: Union[Tool, Resource, Prompt], handler: callable, run_on_main_thread: bool = True,
                 enforce_timeout: bool = True):
        if not isinstance(primitive, (Tool, Resource, Prompt)):
            raise ValueError("Primitive must be a Tool, Resource, or Prompt instance")
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
        if isinstance(self.primitive, Tool):
            return "tool"
        elif isinstance(self.primitive, Resource):
            return "resource"
        elif isinstance(self.primitive, Prompt):
            return "prompt"
        return "unknown"

    def to_dict(self) -> dict:
        return self.primitive.to_dict()

    def to_json(self) -> str:
        return self.primitive.to_json()

    def call_handler(self, kwargs: dict) -> Any:
        return self.handler(**kwargs)

    def __str__(self) -> str:
        return f"Item(type='{self.get_type()}', name='{self.get_name()}')"

    def __repr__(self) -> str:
        return f"Item(primitive={self.primitive}, handler={self.handler})"

    @classmethod
    def create_tool_item(cls, tool: Tool, handler: callable, run_on_main_thread: bool = True,
                         enforce_timeout: bool = True, write: str = None) -> 'Item':
        """Build a tool Item. ``write`` declares the tool's write-status, applied to the tool's
        annotations (readOnlyHint / destructiveHint) so the server reports it as structured data:
          'read'        -> read-only (does not modify state)
          'write'       -> modifies state
          'destructive' -> a hard-to-reverse write (delete, history-discarding conversion, close doc)
        Every tool must pass one (enforced by tests/test_write_status_annotations.py)."""
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
            from ..tools import _write_guard
            handler = _write_guard.wrap(handler)
            tool.add_input_property(*_write_guard.EXPECT_DOCUMENT_PROP)
        return cls(primitive=tool, handler=handler, run_on_main_thread=run_on_main_thread,
                   enforce_timeout=enforce_timeout)

    @classmethod
    def create_resource_item(cls, resource: Resource, handler: callable, run_on_main_thread: bool = True) -> 'Item':
        return cls(primitive=resource, handler=handler, run_on_main_thread=run_on_main_thread)

    @classmethod
    def create_prompt_item(cls, prompt: Prompt, handler: callable, run_on_main_thread: bool = True) -> 'Item':
        return cls(primitive=prompt, handler=handler, run_on_main_thread=run_on_main_thread)
