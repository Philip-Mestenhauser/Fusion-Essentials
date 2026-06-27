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
        # (e.g. execute_api_script) — timing those out would report a false failure for a change
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
                         enforce_timeout: bool = True) -> 'Item':
        return cls(primitive=tool, handler=handler, run_on_main_thread=run_on_main_thread,
                   enforce_timeout=enforce_timeout)

    @classmethod
    def create_resource_item(cls, resource: Resource, handler: callable, run_on_main_thread: bool = True) -> 'Item':
        return cls(primitive=resource, handler=handler, run_on_main_thread=run_on_main_thread)

    @classmethod
    def create_prompt_item(cls, prompt: Prompt, handler: callable, run_on_main_thread: bool = True) -> 'Item':
        return cls(primitive=prompt, handler=handler, run_on_main_thread=run_on_main_thread)
