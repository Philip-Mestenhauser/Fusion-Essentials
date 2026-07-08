"""Lint: every agent-facing wire string is pure ASCII (CLAUDE.md "Tool descriptions").

A tool's description, its per-input descriptions, and any runtime note/error are the only things a
connected agent ever reads about a tool - and they cross the wire JSON-encoded with
``ensure_ascii``. A non-ASCII character (an em dash, a curly quote, a degree sign, ...) turns into a
6-character ``\\uXXXX`` escape that costs tokens every turn and reads worse than the plain-ASCII
spelling (" - " for an em dash, "..." for an ellipsis, "->" for an arrow, "deg" for a degree sign).

This sweeps two places a wire string is authored:
  1. the LIVE registry - every registered tool's description and every input property's
     description (recursively, for nested array/object schemas);
  2. the SOURCE - any module-level ``*_DESCRIPTION`` constant, whether or not it ends up wired to a
     tool today (catching a dead-but-about-to-be-reused constant before it goes non-ASCII).

Box-drawing dividers in ``#`` comments (module docstrings) are outside this sweep on purpose - a
comment never serializes onto the wire.
"""

import ast
import os
import re

from conftest import load_tool, TOOLS_DIR


def _tool_modules():
    return [fn[:-3] for fn in sorted(os.listdir(TOOLS_DIR))
            if fn.endswith(".py") and not fn.startswith("_") and fn != "__init__.py"]


def _all_registered_tools():
    names = _tool_modules()
    load_tool(names[0])              # first load sets up sys.path + the mcpServer.tools stub
    from mcpServer.mcp_primitives import registry
    registry.reset_registry()
    for mod_name in names:
        mod = load_tool(mod_name)
        reg = getattr(mod, "register_tool", None)
        if callable(reg):
            reg()
    return registry.get_tools()


def _non_ascii(text):
    return [(c, hex(ord(c))) for c in text if ord(c) > 127]


def _walk_property_descriptions(props, path, out):
    """Recurse into a JSON-schema properties dict, collecting (path, description) pairs."""
    for name, schema in props.items():
        if not isinstance(schema, dict):
            continue
        desc = schema.get("description")
        if isinstance(desc, str):
            out.append((f"{path}.{name}", desc))
        nested = schema.get("properties")
        if isinstance(nested, dict):
            _walk_property_descriptions(nested, f"{path}.{name}", out)
        items = schema.get("items")
        if isinstance(items, dict) and isinstance(items.get("properties"), dict):
            _walk_property_descriptions(items["properties"], f"{path}.{name}[]", out)


class TestToolDescriptionsAreAscii:
    def test_every_tool_description_is_ascii(self):
        offenders = []
        for it in _all_registered_tools():
            d = it.to_dict()
            desc = d.get("description") or ""
            bad = _non_ascii(desc)
            if bad:
                offenders.append(f"{d.get('name')}: description has {bad} - "
                                  f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII tool description(s):\n  " + "\n  ".join(offenders)

    def test_every_input_description_is_ascii(self):
        offenders = []
        for it in _all_registered_tools():
            d = it.to_dict()
            name = d.get("name")
            props = (d.get("inputSchema") or {}).get("properties", {}) or {}
            found = []
            _walk_property_descriptions(props, name, found)
            for path, desc in found:
                bad = _non_ascii(desc)
                if bad:
                    offenders.append(f"{path}: input description has {bad} - "
                                      f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII input description(s):\n  " + "\n  ".join(offenders)


# A module-level constant whose name ends in DESCRIPTION - the naming convention every tool uses for
# its wire description (TOOL_DESCRIPTION, and a couple of per-verb variants on action-dispatched tools).
_DESCRIPTION_NAME = re.compile(r".*DESCRIPTION$")


def _description_constant_strings(path):
    """(constant_name, [string literals in its assigned value]) for every module-level `*_DESCRIPTION =
    ...` assignment in the file at `path` - walking the assigned expression instead of literal_eval'ing
    it, so an f-string/`.format()`/`+`-built description is still checked piece by piece."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and _DESCRIPTION_NAME.match(target.id):
                strings = [n.value for n in ast.walk(node.value)
                           if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                out.append((target.id, strings))
    return out


class TestDescriptionConstantsAreAscii:
    def test_every_description_constant_is_ascii(self):
        offenders = []
        for fn in sorted(os.listdir(TOOLS_DIR)):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(TOOLS_DIR, fn)
            for const_name, strings in _description_constant_strings(path):
                for s in strings:
                    bad = _non_ascii(s)
                    if bad:
                        offenders.append(f"{fn}: {const_name} has {bad} - "
                                          f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII description constant(s):\n  " + "\n  ".join(offenders)
