# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP primitives package: schema classes and the shared registry."""

from .annotations import Annotations
from .tool import Tool
from .resource import Resource
from .prompt import Prompt
from .item import Item
from .registry import (
    Registry,
    get_registry,
    reset_registry,
    register,
    has_tool,
    has_resource,
    get_tools,
    get_resources,
    get_prompts,
    clear_registry,
    registry_count,
)

__all__ = [
    'Annotations', 'Tool', 'Resource', 'Prompt', 'Item', 'Registry',
    'get_registry', 'reset_registry', 'register', 'has_tool', 'has_resource',
    'get_tools', 'get_resources', 'get_prompts', 'clear_registry', 'registry_count',
]
