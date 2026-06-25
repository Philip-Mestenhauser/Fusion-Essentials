# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP Prompt schema. Defined for completeness; prompts are not served yet."""

import json
from typing import List, Optional


class Prompt:
    """MCP Prompt schema with a fluent builder interface."""

    def __init__(
        self,
        name: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        arguments: Optional[List[dict]] = None
    ):
        self.name = name
        self.title = title
        self.description = description
        self.arguments = arguments or []

    def set_title(self, title: str) -> 'Prompt':
        self.title = title
        return self

    def set_description(self, description: str) -> 'Prompt':
        self.description = description
        return self

    def set_arguments(self, arguments: List[dict]) -> 'Prompt':
        self.arguments = arguments
        return self

    def add_argument(self, name: str, description: str, required: bool = False, **kwargs) -> 'Prompt':
        self.arguments.append({"name": name, "description": description, "required": required, **kwargs})
        return self

    def add_string_argument(self, name: str, description: str, required: bool = False, **kwargs) -> 'Prompt':
        return self.add_argument(name=name, description=description, required=required, type="string", **kwargs)

    def add_number_argument(self, name: str, description: str, required: bool = False, **kwargs) -> 'Prompt':
        return self.add_argument(name=name, description=description, required=required, type="number", **kwargs)

    def add_boolean_argument(self, name: str, description: str, required: bool = False, **kwargs) -> 'Prompt':
        return self.add_argument(name=name, description=description, required=required, type="boolean", **kwargs)

    def to_dict(self) -> dict:
        result = {'name': self.name}
        if self.title:
            result['title'] = self.title
        if self.description:
            result['description'] = self.description
        if self.arguments:
            result['arguments'] = self.arguments
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def __str__(self) -> str:
        return f"Prompt(name='{self.name}', title='{self.title}')"

    def __repr__(self) -> str:
        return (f"Prompt(name='{self.name}', title='{self.title}', description='{self.description}')")

    @classmethod
    def create_simple(cls, name: str, description: str, **kwargs) -> 'Prompt':
        return cls(name=name, description=description, **kwargs)

    @classmethod
    def create_with_string_arg(
        cls,
        name: str,
        description: str,
        arg_name: str = "input",
        arg_description: str = "Input parameter",
        required: bool = False,
        **kwargs
    ) -> 'Prompt':
        prompt = cls.create_simple(name, description, **kwargs)
        prompt.add_string_argument(arg_name, arg_description, required)
        return prompt
