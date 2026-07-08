# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: the world-axis x/y/z -> unit-vector map is _inputs._AXIS_VECS, never a local copy.

The AxisRef kind resolves x/y/z (and edge/face handles) to a direction, backed by
`_inputs._AXIS_VECS = {"x": (1,0,0), "y": (0,1,0), "z": (0,0,1)}`. A tool that keeps its own
`_AXES = {"x": (1,0,0), ...}` re-rolls that constant (and forgoes handle support). This catches the
copy by VALUE SHAPE: a dict mapping the string keys x, y AND z to unit-axis vectors, anywhere outside
the home module - distinct from a view-direction table (front/top/... keys) or the construction-axis
name maps (string values), which this deliberately does not touch.
"""

import ast
import os

from conftest import TOOLS_DIR

_HOME = "_inputs.py"        # where _AXIS_VECS is defined


def _is_unit_axis_vector(node):
    """True for a (1,0,0)/(0,1,0)/(0,0,1)-shaped literal: 3 numeric elements, exactly one 1 and two 0s."""
    if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) != 3:
        return False
    vals = []
    for e in node.elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
            vals.append(e.value)
        else:
            return False
    return sorted(vals) == [0, 0, 1]


def _is_xyz_vector_map(node):
    """True for a dict literal mapping the keys 'x', 'y' AND 'z' to unit-axis vectors - the re-roll."""
    if not isinstance(node, ast.Dict):
        return False
    keyed = set()
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str) and _is_unit_axis_vector(v):
            keyed.add(k.value.lower())
    return {"x", "y", "z"}.issubset(keyed)


class TestAxisVectorsShared:
    def test_no_local_world_axis_vector_map(self):
        offenders = []
        for fn in sorted(os.listdir(TOOLS_DIR)):
            if not fn.endswith(".py") or fn == _HOME:
                continue
            tree = ast.parse(open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read())
            for node in ast.walk(tree):
                if _is_xyz_vector_map(node):
                    offenders.append(f"{fn}:{getattr(node, 'lineno', '?')}")
        assert not offenders, (
            "a local world-axis x/y/z -> unit-vector map re-rolls _inputs._AXIS_VECS - import and use it "
            "(or the AxisRef kind, which also accepts an edge/face handle):\n  " + "\n  ".join(offenders))

    def test_the_lint_bites(self):
        # a copy of the map is caught; the look-alikes (view directions, construction-axis names) are not.
        assert _is_xyz_vector_map(ast.parse('{"x": (1,0,0), "y": (0,1,0), "z": (0,0,1)}').body[0].value)
        assert not _is_xyz_vector_map(ast.parse('{"front": (0,-1,0), "top": (0,0,1)}').body[0].value)
        assert not _is_xyz_vector_map(
            ast.parse('{"x": "xConstructionAxis", "y": "yConstructionAxis"}').body[0].value)
