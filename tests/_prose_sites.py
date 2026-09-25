# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Which strings in a tool module are agent-facing prose - the one harvest rule two readers share.

A sentence reaches an agent wherever it is written: at the error()/note site, in a module constant
a handler formats, or returned from a helper. test_prose_budget budgets what this returns and
gen_wiring attributes it to a tool's guidance surface, so one rule decides both."""

import ast

# Below this a string is a fragment - a label, a key, a clause joined into a sentence elsewhere.
PROSE_MIN_CHARS = 120


def static_text(node):
    """The STATIC text one expression carries: an f-string's constant pieces with its
    interpolations ignored, a `+` chain's two sides joined, '' for anything else (so a
    produces_block() call appended to a description contributes nothing)."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        return "".join(static_text(v) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return static_text(node.left) + static_text(node.right)
    return ""


def wire_prose(tree):
    """(node, text) for every agent-facing sentence a tool module ships: each outermost string
    expression carrying PROSE_MIN_CHARS+ static characters, minus the docstrings, the sites the
    description/MAP_BLURB budgets own, and the one non-sentence SHAPE (see _is_sentence)."""
    skip = _docstring_ids(tree) | _budgeted_elsewhere(tree)
    nodes = []
    _collect(tree, skip, nodes)
    return [(node, static_text(node)) for node in nodes
            if len(static_text(node)) >= PROSE_MIN_CHARS and _is_sentence(_filled_text(node))]


# What an interpolation stands in as while the shape test parses the text: an embedded script
# carrying a path or a count is still source, and the hole is where its value goes.
_HOLE = "_fe_hole"


def _filled_text(node):
    """The node's text with every interpolation replaced by a placeholder name - what the SHAPE is
    judged on (its size is still the static text, which is all the wire is billed for)."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else _HOLE
    if isinstance(node, ast.JoinedStr):
        return "".join(_filled_text(v) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _filled_text(node.left) + _filled_text(node.right)
    return _HOLE


def _is_sentence(text):
    """Whether a long string is prose. Two shapes are not: a run with no whitespace in it is an
    identifier, a path or a pattern - a pytest node id, a url - and a Python statement suite is
    source the tool EXECUTES, which reaches an interpreter rather than an agent."""
    return any(ch.isspace() for ch in text) and not _is_python_suite(text)


def _is_python_suite(text):
    """Whether the text parses as Python STATEMENTS - an embedded script (a prelude, a loader)."""
    try:
        return bool(ast.parse(text).body)
    except (SyntaxError, ValueError):
        return False


def _is_string_expr(node):
    """Whether an expression is string-shaped: a str constant, an f-string, or a `+` of them."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_expr(node.left) or _is_string_expr(node.right)
    return False


def _collect(node, skip, out, inside=False):
    """Claim each OUTERMOST string expression once, then keep walking it: an interpolation or a call
    argument inside one carries its own sentences, while its own literal pieces are that same
    sentence and are never measured a second time."""
    for child in ast.iter_child_nodes(node):
        if _is_string_expr(child):
            if not inside and id(child) not in skip:
                out.append(child)
            _collect(child, skip, out, inside=True)
        else:
            _collect(child, skip, out)


def _docstring_ids(tree):
    """The ids of the module/class/def docstring literals - prose for a reader of the source."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        doc = node.body[0] if getattr(node, "body", None) else None
        if (isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
                and isinstance(doc.value.value, str)):
            out.add(id(doc.value))
    return out


def _budgeted_elsewhere(tree):
    """The ids of the strings another budget already owns: a module-level *DESCRIPTION / MAP_BLURB
    constant, and each description inside an add_input_property(...) schema."""
    out = set()
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not targets or node.value is None:
            continue
        for name in [n for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]:
            if name.id.endswith("DESCRIPTION") or name.id == "MAP_BLURB":
                out.add(id(node.value))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_input_property"):
            continue
        for arg in node.args:
            for schema in [x for x in ast.walk(arg) if isinstance(x, ast.Dict)]:
                out |= {id(v) for k, v in zip(schema.keys, schema.values)
                        if isinstance(k, ast.Constant) and k.value == "description"}
    return out
