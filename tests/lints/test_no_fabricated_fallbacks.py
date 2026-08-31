# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""A failed read may not fall back to a number, or to the request that asked for it.

`safe(read, default)` turns an unreadable value into `default`, and the payload then publishes it as
though it were measured. Two shapes are always wrong:

- the default is the handler's own PARAMETER - the request becomes its own read-back, so a tool
  reports the value it was asked for whenever the confirmation read fails;
- the default is a NUMBER on a measured quantity - an unreadable distance reports 0.0, which in a
  measurement payload does not mean "unknown", it means touching.

The honest fallback is None: the key is present and null, and the caller can tell a real zero from
an unmeasurable one. A count that is genuinely absent may still default to 0 - that is a tally, not
a measurement, and `_COUNTISH` names are exempt.
"""

import ast
import os

import _corpus
from conftest import TOOLS_DIR

# Attributes whose value IS a measurement: a fabricated number here is a false reading, not a
# missing one. Read off the final attribute in the safe() lambda.
_MEASURED = frozenset({
    "value", "mass", "volume", "area", "density", "length", "radius", "diameter",
    "angle", "distance", "perimeter", "thickness", "offset", "depth", "height", "width",
})

# Tallies. A collection that cannot be read holding "0 items" is a defensible reading, and the
# codebase leans on it heavily for adsk collections that are absent rather than empty.
_COUNTISH = frozenset({"count", "len", "quantity", "numberOfFaces", "triangleCount", "nodeCount"})


# Sites where a numeric fallback is NOT a fabricated measurement. Each needs a reason naming why
# the number is defensible - the value must not reach a payload as a measurement. Shrink-only.
_ALLOWED = {
    "_assembly_detail.py:366": "a joint origin's offsetX genuinely defaults to 0 (live-verified: a "
                               "face/sketch-anchored JO reports geometry.origin as-is)",
    "_assembly_detail.py:367": "offsetY, same contract as offsetX",
    "_assembly_detail.py:368": "offsetZ, same contract as offsetX",
    "_inputs.py:1967": "a degeneracy GUARD - an unreadable vector length is treated as zero so the "
                      "direction is REFUSED, which is the safe direction",
    "cam_edit_tools.py:435": "a generic CAM-parameter reader whose 'default' is the CALLER's chosen "
                             "value for an absent parameter, not the tool's own request",
    "joint_create_edit.py:277": "picking the LARGEST face - an unreadable area sorts last and is "
                                "never published",
    "sketch_dimension.py:137": "a text-placement offset, immediately replaced by 1.0 when it is not "
                               "positive; never published",
    "surface_edit.py:112": "cell areas compared against each other to pick a cell; not published",
    "surface_untrim.py:46": "an area SUM compared before/after to prove the untrim moved something",
}


def _iter_tool_files():
    for name in sorted(os.listdir(TOOLS_DIR)):
        if name.endswith(".py") and name != "__init__.py":
            yield name, os.path.join(TOOLS_DIR, name)


def _final_attr(node):
    """The last attribute name a lambda body reads ('mr.value' -> 'value'), or None."""
    while isinstance(node, ast.Call):
        node = node.func
    return node.attr if isinstance(node, ast.Attribute) else None


def _params(fn):
    a = fn.args
    names = [p.arg for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return set(names)


def _offenders_in(path):
    """[(lineno, kind, detail)] for every safe() whose fallback fabricates a value.

    The tree is _corpus's, shared with the other lints over this corpus; this walk only reads it."""
    out = []
    tree = _corpus.tree(path)

    def walk(node, params):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = params | _params(node)
        if isinstance(node, ast.Call):
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", None)
            if fname == "safe" and len(node.args) >= 2:
                body = node.args[0]
                body = body.body if isinstance(body, ast.Lambda) else body
                attr = _final_attr(body)
                default = node.args[1]
                # -1 parses as UnaryOp(USub, Constant(1)) - unwrap the sign so a negative literal
                # fallback is the same number-for-a-measurement hit a positive one is.
                if (isinstance(default, ast.UnaryOp)
                        and isinstance(default.op, (ast.USub, ast.UAdd))
                        and isinstance(default.operand, ast.Constant)):
                    default = default.operand
                if isinstance(default, ast.Name) and default.id in params:
                    out.append((node.lineno, "request-as-fallback",
                                f"falls back to the parameter '{default.id}'"))
                elif (isinstance(default, ast.Constant)
                      and isinstance(default.value, (int, float))
                      and not isinstance(default.value, bool)
                      and attr in _MEASURED and attr not in _COUNTISH):
                    out.append((node.lineno, "number-for-a-measurement",
                                f"reads .{attr} and falls back to {default.value!r}"))
        for child in ast.iter_child_nodes(node):
            walk(child, params)

    walk(tree, set())
    return out


class TestNoFabricatedFallbacks:
    def test_no_read_falls_back_to_a_number_or_to_the_request(self):
        offenders = []
        for name, path in _iter_tool_files():
            for lineno, kind, detail in _offenders_in(path):
                if f"{name}:{lineno}" in _ALLOWED:
                    continue
                offenders.append(f"{name}:{lineno}: [{kind}] safe(...) {detail}")
        assert not offenders, (
            "a failed read must be None, so the caller can tell an unmeasurable value from a real "
            "one - never the request, and never a fabricated number:\n  " + "\n  ".join(offenders))

    def test_every_allowlisted_site_still_exists(self):
        """An allowlist entry whose line has moved silently exempts whatever now sits there."""
        live = set()
        for name, path in _iter_tool_files():
            for lineno, _kind, _detail in _offenders_in(path):
                live.add(f"{name}:{lineno}")
        stale = sorted(set(_ALLOWED) - live)
        assert not stale, (
            "these allowlist entries no longer name a fabricated fallback - the code moved or was "
            "fixed, so the entry now exempts a line nobody audited. Remove them:\n  "
            + "\n  ".join(stale))

    def test_the_gate_inspects_a_real_number_of_safe_calls(self):
        """The name `safe` still appears at scale in the fleet - a floor on the raw material the
        detector works over, NOT proof the detector's own walk works (the allowlist-still-exists
        test is what goes red when _offenders_in breaks, and the bite test below exercises both
        offender kinds directly)."""
        seen = 0
        for _name, path in _iter_tool_files():
            for node in ast.walk(_corpus.tree(path)):
                if isinstance(node, ast.Call):
                    fname = node.func.attr if isinstance(node.func, ast.Attribute) else \
                        getattr(node.func, "id", None)
                    if fname == "safe":
                        seen += 1
        assert seen >= 500, f"only {seen} safe() calls found - the fleet shape has changed."

    def test_the_detector_bites_on_both_kinds(self, tmp_path):
        # a request echoed as its own fallback, and a NEGATIVE numeric fallback for a measurement -
        # the sign unwrap is load-bearing: -1 parses as UnaryOp and slipped the net before.
        src = tmp_path / "t.py"
        src.write_text(
            "def handler(distance):\n"
            "    a = safe(lambda: feat.distance.value, distance)\n"
            "    b = safe(lambda: body.volume, -1.0)\n",
            encoding="utf-8")
        kinds = sorted(k for _l, k, _d in _offenders_in(str(src)))
        assert kinds == ["number-for-a-measurement", "request-as-fallback"], kinds
