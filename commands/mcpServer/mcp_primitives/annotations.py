# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP Annotations schema for resource metadata."""

import json
from typing import List, Optional, Union
from datetime import datetime


class Annotations:
    """MCP Annotations schema; converts to JSON-friendly dicts."""

    def __init__(
        self,
        audience: Optional[List[str]] = None,
        priority: Optional[float] = None,
        last_modified: Optional[Union[str, datetime]] = None
    ):
        self.audience = audience or []
        self.priority = priority
        self.last_modified = last_modified

    def set_audience(self, *audiences: str) -> 'Annotations':
        self.audience = list(audiences)
        return self

    def add_audience(self, audience: str) -> 'Annotations':
        if audience not in self.audience:
            self.audience.append(audience)
        return self

    def set_priority(self, priority: float) -> 'Annotations':
        if not 0.0 <= priority <= 1.0:
            raise ValueError("Priority must be between 0.0 and 1.0")
        self.priority = priority
        return self

    def set_last_modified(self, last_modified: Union[str, datetime]) -> 'Annotations':
        if isinstance(last_modified, datetime):
            self.last_modified = last_modified.isoformat() + 'Z'
        else:
            self.last_modified = last_modified
        return self

    def to_dict(self) -> dict:
        result = {}
        if self.audience:
            result['audience'] = self.audience
        if self.priority is not None:
            result['priority'] = self.priority
        if self.last_modified:
            result['lastModified'] = self.last_modified
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def __str__(self) -> str:
        return f"Annotations({self.to_dict()})"

    def __repr__(self) -> str:
        return (f"Annotations(audience={self.audience}, priority={self.priority}, "
                f"last_modified={self.last_modified})")

    @classmethod
    def for_user(cls) -> 'Annotations':
        return cls().set_audience("user")

    @classmethod
    def for_assistant(cls) -> 'Annotations':
        return cls().set_audience("assistant")

    @classmethod
    def for_both(cls) -> 'Annotations':
        return cls().set_audience("user", "assistant")

    @classmethod
    def high_priority(cls) -> 'Annotations':
        return cls().set_priority(1.0)

    @classmethod
    def low_priority(cls) -> 'Annotations':
        return cls().set_priority(0.0)

    @classmethod
    def now_modified(cls) -> 'Annotations':
        return cls().set_last_modified(datetime.utcnow())
