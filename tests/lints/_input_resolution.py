# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Resolve, per tool module, which local variables hold a FeatureInput and of what class.

Shared by the two lints that need it: test_input_property_names (is this property real?) and
test_bool_returns_checked (was this setter's answer read?). One home, because both depend on the
same tricky scoping - sibling handlers reuse the name 'inp', and the work happens in a nested
closure that must still see the collection bound in its enclosing handler.
"""

import ast
import os

import api_surface

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "commands", "mcpServer", "tools")

# A collection attribute reads `<lowerCamel>Features`/`<lowerCamel>s`; its class is the same name
# with an initial capital. Only fusion/cam collections are resolved - a core factory is reached the
# same way and falls through to the same lookup.
_MODULES = ("fusion", "cam", "core", "drawing")


# 'module.Collection' for every collection that declares a factory returning a FeatureInput.
_COLLECTIONS = frozenset(k.rsplit(".", 1)[0] for k in api_surface.FACTORIES)


def _collection_class(attr_name):
    """'meshCombineFeatures' -> the 'module.MeshCombineFeatures' key api_surface knows, or None."""
    cls = attr_name[:1].upper() + attr_name[1:]
    for mod in _MODULES:
        key = f"{mod}.{cls}"
        if key in _COLLECTIONS:
            return key
    return None


def _unwrap(node):
    """The expression inside `safe(lambda: <expr>)` / `safe(lambda: <expr>, default)`, else node.
    Feature collections are routinely fetched through safe(), and the class is still knowable."""
    while isinstance(node, ast.Call):
        fname = node.func.attr if isinstance(node.func, ast.Attribute) else \
            getattr(node.func, "id", None)
        if fname != "safe" or not node.args:
            break
        first = node.args[0]
        node = first.body if isinstance(first, ast.Lambda) else first
    return node


def _collection_vars(nodes):
    """variable -> 'module.Collection' for every `x = <...>.<someFeatures>` in ONE scope. Scoped,
    not module-wide: two handlers in one file both call their collection 'feats'."""
    out = {}
    for node in nodes:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        value = _unwrap(node.value)
        if isinstance(value, ast.Attribute):
            coll = _collection_class(value.attr)
            if coll:
                out[node.targets[0].id] = coll
    return out


def _factory_target(call, coll_vars=None):
    """The 'module.Class' a `<collection>.createInput(...)` call returns, or None. The collection is
    reached either inline (`comp.features.meshRepairFeatures.createInput`) or through a local
    variable bound earlier in the module (`feats = comp.features.meshRepairFeatures`)."""
    call = _unwrap(call)
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
        return None
    method = call.func.attr
    owner = call.func.value
    coll = None
    if isinstance(owner, ast.Attribute):
        coll = _collection_class(owner.attr)
    elif isinstance(owner, ast.Name) and coll_vars:
        coll = coll_vars.get(owner.id)
    if coll is None:
        return None
    return api_surface.FACTORIES.get(f"{coll}.{method}")


def _iter_tool_files():
    for name in sorted(os.listdir(TOOLS_DIR)):
        if name.endswith(".py") and name != "__init__.py":
            yield name, os.path.join(TOOLS_DIR, name)


# The name-by-string forms: (callable, index of the object arg, index of the property-name arg).
# set_verified is the repo's preferred way to assign an input property, and it passes the name as a
# STRING - so a gate that only walked attribute assignments would be blind to exactly the code that
# follows the convention.
_STRING_FORMS = {"set_verified": (0, 1), "setattr": (0, 1)}


def _named_assignments_in(nodes):
    """[(lineno, var, prop)] over BOTH forms: `var.prop = ...` and set_verified/setattr(var, 'prop')."""
    out = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name):
                    out.append((tgt.lineno, tgt.value.id, tgt.attr))
        elif isinstance(node, ast.Call):
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", None)
            spec = _STRING_FORMS.get(fname)
            if spec is None:
                continue
            obj_i, prop_i = spec
            if len(node.args) <= max(obj_i, prop_i):
                continue
            obj, prop = node.args[obj_i], node.args[prop_i]
            if isinstance(obj, ast.Name) and isinstance(prop, ast.Constant) \
                    and isinstance(prop.value, str):
                out.append((node.lineno, obj.id, prop.value))
    return out


def _scopes(tree):
    """[(nodes, enclosing_nodes)] - one entry per function body plus the module's, each body
    EXCLUDING functions nested inside it (they become entries of their own) but carrying everything
    lexically enclosing it.

    Both halves are load-bearing. Sibling handlers in one file reuse the same local name ('inp',
    'feats') for a DIFFERENT object, so a module-wide map checks against whichever class the walk
    saw last. And a handler routinely binds the collection then does the work in a nested closure,
    so a scope that could not see outward would resolve nothing at all."""
    scopes = []

    def collect(body, enclosing):
        nodes, nested = [], []
        for stmt in body:
            stack = [stmt]
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nested.append(node)
                    continue          # its body belongs to its own scope
                nodes.append(node)
                stack.extend(ast.iter_child_nodes(node))
        scopes.append((nodes, enclosing))
        for fn in nested:
            collect(fn.body, enclosing + nodes)

    collect(tree.body, [])
    return scopes


def _bindings(nodes, coll_vars):
    bound = {}
    for node in nodes:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = _factory_target(node.value, coll_vars)
            if target:
                bound[node.targets[0].id] = target
    return bound


def _offenders_in(path):
    """[(line, var, prop, class)] for every property assigned onto a resolved input whose name is
    not a real member of that input's class."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    offenders = []
    for nodes, enclosing in _scopes(tree):
        visible = enclosing + nodes
        bound = _bindings(visible, _collection_vars(visible))
        if not bound:
            continue
        for lineno, var, prop in _named_assignments_in(nodes):
            cls = bound.get(var)
            if cls is None:
                continue
            members = api_surface.PROPERTIES.get(cls)
            if not members:
                # An input class the table does not carry would make every property on it
                # unverifiable. Report it rather than skip: silence here is the whole failure mode.
                offenders.append((lineno, var, prop, cls))
                continue
            if prop not in members:
                offenders.append((lineno, var, prop, cls))
    return offenders


