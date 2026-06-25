# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""MCP Resource schema for defining resource metadata."""

import json
from typing import Optional


class Resource:
    """MCP Resource schema with a fluent builder interface."""

    def __init__(
        self,
        uri: str,
        name: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        mime_type: Optional[str] = None,
        size: Optional[int] = None,
        uri_template: Optional[str] = None
    ):
        self.uri = uri
        self.name = name
        self.title = title
        self.description = description
        self.mime_type = mime_type
        self.size = size
        self.uri_template = uri_template

    def set_name(self, name: str) -> 'Resource':
        self.name = name
        return self

    def set_title(self, title: str) -> 'Resource':
        self.title = title
        return self

    def set_description(self, description: str) -> 'Resource':
        self.description = description
        return self

    def set_mime_type(self, mime_type: str) -> 'Resource':
        self.mime_type = mime_type
        return self

    def set_size(self, size: int) -> 'Resource':
        if size is not None and size < 0:
            raise ValueError("Size must be non-negative")
        self.size = size
        return self

    def set_uri_template(self, template: str) -> 'Resource':
        self.uri_template = template
        return self

    def to_dict(self) -> dict:
        result = {}
        if self.uri:
            result['uri'] = self.uri
        elif self.uri_template:
            result['uriTemplate'] = self.uri_template
        if self.name:
            result['name'] = self.name
        if self.title:
            result['title'] = self.title
        if self.description:
            result['description'] = self.description
        if self.mime_type:
            result['mimeType'] = self.mime_type
        if self.size is not None:
            result['size'] = self.size
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def __str__(self) -> str:
        return f"Resource(uri='{self.uri}', name='{self.name}')"

    def __repr__(self) -> str:
        return (f"Resource(uri='{self.uri}', name='{self.name}', "
                f"title='{self.title}', description='{self.description}')")

    @classmethod
    def create_simple(cls, uri: str, name: str, **kwargs) -> 'Resource':
        return cls(uri=uri, name=name, **kwargs)

    @classmethod
    def create_text_resource(cls, uri: str, name: str, description: Optional[str] = None, **kwargs) -> 'Resource':
        return cls(uri=uri, name=name, description=description, mime_type="text/plain", **kwargs)

    @classmethod
    def create_json_resource(cls, uri: str, name: str, description: Optional[str] = None, **kwargs) -> 'Resource':
        return cls(uri=uri, name=name, description=description, mime_type="application/json", **kwargs)

    @classmethod
    def create_image_resource(cls, uri: str, name: str, description: Optional[str] = None, **kwargs) -> 'Resource':
        return cls(uri=uri, name=name, description=description, mime_type="image/png", **kwargs)
