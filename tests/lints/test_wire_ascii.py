"""Lint: every agent-facing wire string is pure ASCII (CLAUDE.md "Tool descriptions").

A tool's description, its per-input descriptions, and any runtime note/error are the only things a
connected agent ever reads about a tool - and they cross the wire JSON-encoded with
``ensure_ascii``. A non-ASCII character (an em dash, a curly quote, a degree sign, ...) turns into a
6-character ``\\uXXXX`` escape that costs tokens every turn and reads worse than the plain-ASCII
spelling (" - " for an em dash, "..." for an ellipsis, "->" for an arrow, "deg" for a degree sign).

This sweeps three places a wire string is authored:
  1. the LIVE registry - every registered tool's description and every input property's
     description (recursively, for nested array/object schemas);
  2. the SOURCE - any module-level ``*_DESCRIPTION`` constant, whether or not it ends up wired to a
     tool today (catching a dead-but-about-to-be-reused constant before it goes non-ASCII);
  3. the RUNTIME payloads - every string literal inside an ``ok(...)`` / ``error(...)`` call in
     the tool sources (notes, error text, payload keys/values - all of it crosses the wire).

Box-drawing dividers in ``#`` comments (module docstrings) are outside this sweep on purpose - a
comment never serializes onto the wire.
"""

import ast
import os
import re

from conftest import TOOLS_DIR, register_all_tools


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
        for it in register_all_tools():
            d = it.to_dict()
            desc = d.get("description") or ""
            bad = _non_ascii(desc)
            if bad:
                offenders.append(f"{d.get('name')}: description has {bad} - "
                                  f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII tool description(s):\n  " + "\n  ".join(offenders)

    def test_every_input_description_is_ascii(self):
        offenders = []
        for it in register_all_tools():
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


def _ok_error_call_strings(src, filename="<src>"):
    """(call_name, lineno, [string literals]) for every ``ok(...)`` / ``error(...)`` call in the
    source text - the runtime payload authoring sites (``_common.ok``/``_common.error`` attribute
    calls too). Every string literal in the call subtree is collected (f-string pieces, nested
    dict keys/values, defaults handed to safe()): each is text that crosses the wire JSON-encoded."""
    tree = ast.parse(src, filename=filename)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if name not in ("ok", "error"):
            continue
        strings = [n.value for n in ast.walk(node)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if strings:
            out.append((name, node.lineno, strings))
    return out


class TestRuntimePayloadStringsAreAscii:
    def test_every_ok_error_literal_is_ascii(self):
        offenders = []
        for fn in sorted(os.listdir(TOOLS_DIR)):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(TOOLS_DIR, fn)
            src = open(path, encoding="utf-8").read()
            for call_name, lineno, strings in _ok_error_call_strings(src, path):
                for s in strings:
                    bad = _non_ascii(s)
                    if bad:
                        offenders.append(f"{fn}:{lineno}: {call_name}(...) literal has {bad} - "
                                          f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII ok()/error() payload literal(s):\n  " + "\n  ".join(offenders)

    def test_the_runtime_sweep_bites(self):
        # A doctored error() payload with a degree sign MUST be flagged...
        hits = _ok_error_call_strings('def h():\n    return error("tilt is 5° too far")\n')
        assert hits and any(_non_ascii(s) for _, _, strings in hits for s in strings)
        # ...an ok() note through the attribute form too...
        hits = _ok_error_call_strings(
            'def h():\n    return _common.ok({"note": "a → b"})\n')
        assert hits and any(_non_ascii(s) for _, _, strings in hits for s in strings)
        # ...an f-string piece inside the call is collected...
        hits = _ok_error_call_strings('def h():\n    return error(f"bad °: {x}")\n')
        assert hits and any(_non_ascii(s) for _, _, strings in hits for s in strings)
        # ...while a non-wire call is out of scope, and a clean payload has no non-ASCII hit.
        assert _ok_error_call_strings('def h():\n    log("° in a log line")\n') == []
        hits = _ok_error_call_strings('def h():\n    return ok({"note": "5 deg off"})\n')
        assert hits and not any(_non_ascii(s) for _, _, strings in hits for s in strings)
