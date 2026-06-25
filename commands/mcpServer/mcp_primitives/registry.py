# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""Registry for MCP Item collections, with a singleton for shared use.

Tools self-register at import time by calling register(); the server then pulls
the registered items via get_tools()/get_resources(). The singleton is reset on
each server start so a reload does not accumulate duplicate registrations.
"""

from typing import Dict, List
from .item import Item


class Registry:
    """Holds Tool/Resource/Prompt Items keyed by name within each type."""

    def __init__(self):
        self._tools: Dict[str, Item] = {}
        self._resources: Dict[str, Item] = {}
        self._prompts: Dict[str, Item] = {}

    def register(self, item: Item) -> None:
        if not isinstance(item, Item):
            raise ValueError("Can only register Item instances")
        name = item.get_name()
        item_type = item.get_type()
        if item_type == "tool":
            if name in self._tools:
                raise ValueError(f"Tool with name '{name}' already registered")
            self._tools[name] = item
        elif item_type == "resource":
            if name in self._resources:
                raise ValueError(f"Resource with name '{name}' already registered")
            self._resources[name] = item
        elif item_type == "prompt":
            if name in self._prompts:
                raise ValueError(f"Prompt with name '{name}' already registered")
            self._prompts[name] = item
        else:
            raise ValueError(f"Unknown item type: {item_type}")

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def has_resource(self, name: str) -> bool:
        return name in self._resources

    def get_tools(self) -> List[Item]:
        return list(self._tools.values())

    def get_resources(self) -> List[Item]:
        return list(self._resources.values())

    def get_prompts(self) -> List[Item]:
        return list(self._prompts.values())

    def clear(self) -> None:
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()

    def count(self) -> int:
        return len(self._tools) + len(self._resources) + len(self._prompts)

    def count_by_type(self) -> Dict[str, int]:
        return {"tool": len(self._tools), "resource": len(self._resources), "prompt": len(self._prompts)}

    def __len__(self) -> int:
        return self.count()

    def __str__(self) -> str:
        c = self.count_by_type()
        return f"Registry(tools={c['tool']}, resources={c['resource']}, prompts={c['prompt']})"


_registry_instance: Registry = None


def get_registry() -> Registry:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = Registry()
    return _registry_instance


def reset_registry() -> None:
    global _registry_instance
    _registry_instance = None


def register(item: Item) -> None:
    get_registry().register(item)


def has_tool(name: str) -> bool:
    return get_registry().has_tool(name)


def has_resource(name: str) -> bool:
    return get_registry().has_resource(name)


def get_tools() -> List[Item]:
    return get_registry().get_tools()


def get_resources() -> List[Item]:
    return get_registry().get_resources()


def get_prompts() -> List[Item]:
    return get_registry().get_prompts()


def clear_registry() -> None:
    get_registry().clear()


def registry_count() -> int:
    return get_registry().count()
