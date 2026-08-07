# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""An adsk call that answers "did it work" may not have its answer thrown away.

Every method named in `api_surface.BOOL_METHODS` is declared `-> bool` by the bindings, and each
documents "Returns true if successful". Two ways a tool discards that answer:

- a bare expression statement - `inp.setDistanceExtent(...)` on its own line;
- a bare `safe(lambda: inp.setDistanceExtent(...))` - which additionally swallows any exception, so
  a refusal is invisible twice over. Reading that same call through `if not safe(...)` is fine: the
  bool is acted on.

Either way the handler runs on to `add()` and reports success for a setting the platform declined.
The name list is GENERATED from the installed bindings and narrowed to names that return bool
EVERYWHERE in the API, so a hit is never a same-named method that returns something else.
"""

import ast

import api_surface
from _input_resolution import _bindings, _collection_vars, _iter_tool_files, _scopes

# Calls whose bool is genuinely uninteresting, each with the reason. Shrink-only.
_ALLOWED = {
    "assembly_transform.py:201": "Matrix3D math on a LOCAL matrix - the resulting transform is "
                                 "written to the occurrence and read back, and an unchanged pose "
                                 "after a move is already an error",
    "assembly_transform.py:208": "as above",
    "assembly_transform.py:218": "as above",
    "assembly_transform.py:219": "as above",
    "assembly_transform.py:230": "as above",
    "doc_insert_occurrence.py:87": "Matrix3D math on a local matrix, before it is handed to "
                                   "addExistingComponent; the placed occurrence is read back",
    "model_create_component.py:79": "Matrix3D math on a local matrix that places a new "
                                    "occurrence, whose creation is verified by re-listing",
}


def _bool_call(node):
    """The method name when `node` is a call to an unambiguously bool-returning adsk member."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    name = node.func.attr
    return name if name in api_surface.BOOL_METHODS else None


def _receiver(call):
    """The variable a method call is made ON ('inp.setDistanceExtent(...)' -> 'inp')."""
    owner = call.func.value
    return owner.id if isinstance(owner, ast.Name) else None


def _offenders_in(path):
    """[(lineno, var, method, how)] for every discarded bool from a call on a FEATURE INPUT.

    Scoped to resolved inputs deliberately. A viewport refresh() or fit() returning false is a
    cosmetic no-op; a SETTER on a FeatureInput returning false means the feature is about to be
    built with settings the platform declined, and the handler runs straight on to add()."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    out = []
    for nodes, enclosing in _scopes(tree):
        visible = enclosing + nodes
        bound = _bindings(visible, _collection_vars(visible))
        if not bound:
            continue
        for node in nodes:
            # Only a BARE STATEMENT discards the value. `if not safe(lambda: x.setQuality(q)):`
            # reads the bool through safe() and acts on it, which is what this gate asks for.
            if not isinstance(node, ast.Expr):
                continue
            call, how = node.value, "the result is discarded"
            if isinstance(call, ast.Call):
                fname = call.func.attr if isinstance(call.func, ast.Attribute) else \
                    getattr(call.func, "id", None)
                if fname == "safe" and call.args and isinstance(call.args[0], ast.Lambda):
                    call = call.args[0].body
                    how = "wrapped in a bare safe(), so the bool AND any exception are swallowed"
            name = _bool_call(call)
            if name and _receiver(call) in bound:
                out.append((node.lineno, _receiver(call), name, how))
    return out


class TestBoolReturnsChecked:
    def test_no_documented_bool_return_is_discarded(self):
        offenders = []
        for name, path in _iter_tool_files():
            for lineno, var, method, how in _offenders_in(path):
                if f"{name}:{lineno}" in _ALLOWED:
                    continue
                offenders.append(f"{name}:{lineno}: {var}.{method}() returns bool and {how}")
        assert not offenders, (
            'each of these documents "Returns true if successful" - a false answer means the '
            "setting never took, and the handler runs on to report success anyway:\n  "
            + "\n  ".join(offenders))

    def test_every_allowlisted_site_still_exists(self):
        """An allowlist entry whose line has moved silently exempts whatever now sits there."""
        live = set()
        for name, path in _iter_tool_files():
            for lineno, _v, _m, _h in _offenders_in(path):
                live.add(f"{name}:{lineno}")
        stale = sorted(set(_ALLOWED) - live)
        assert not stale, ("these allowlist entries no longer name a discarded bool - remove "
                           "them:\n  " + "\n  ".join(stale))

    def test_the_generated_name_list_is_populated_and_narrowed(self):
        """An empty or over-wide list would make this gate meaningless in opposite directions."""
        names = api_surface.BOOL_METHODS
        assert len(names) >= 300, f"only {len(names)} bool-returning names - the scrape has broken."
        # ambiguous names must be OUT: Features.add returns a feature, deleteMe varies by class
        for ambiguous in ("add", "deleteMe", "item", "createInput"):
            assert ambiguous not in names, f"'{ambiguous}' is not unambiguously bool-returning"
        # the ones this class is about must be IN
        for expected in ("setDistanceExtent", "setOneSideExtent", "setPositionAtCenter",
                         "finishEdit", "rollTo"):
            assert expected in names, f"'{expected}' should be a known bool-returning member"
