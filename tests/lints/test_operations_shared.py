# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: the operation-keyword -> FeatureOperations map is _common.OPERATIONS, never a local copy.

`_common.OPERATIONS` maps `new`/`new_body`/`join`/`cut`/`intersect` to the `FeatureOperations` enum
member NAMES (a tool does `getattr(adsk.fusion.FeatureOperations, OPERATIONS[op_key])`). A tool that
keeps its own such dict - `_OPERATIONS`, `_SURFACE_OPS`, `_OFFSET_OPS`, ... - diverges the moment the
shared map changes: local copies drift to different key subsets. `test_helper_duplication` matches
only the exact name `OPERATIONS`, so a leading-`_` copy slips past it; this lint catches those by
VALUE SHAPE: any dict literal outside _common whose values name a `FeatureOperations` member
(`...FeatureOperation`).
"""

import ast
import os

import _corpus
from conftest import TOOLS_DIR

_HOME = "_common.py"


def _names_a_feature_operation(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str) \
        and node.value.endswith("FeatureOperation")


class TestOperationsShared:
    def test_no_local_feature_operations_map(self):
        offenders = []
        for fn in sorted(os.listdir(TOOLS_DIR)):
            if not fn.endswith(".py") or fn == _HOME:
                continue
            for node in ast.walk(_corpus.tree(os.path.join(TOOLS_DIR, fn))):
                if isinstance(node, ast.Dict) and any(_names_a_feature_operation(v) for v in node.values):
                    offenders.append(f"{fn}:{getattr(node, 'lineno', '?')}")
        assert not offenders, (
            "a local operation->FeatureOperations map diverges from _common.OPERATIONS - import and use "
            "it (`getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])`):\n  "
            + "\n  ".join(offenders))

    def test_the_lint_bites(self):
        # prove the value-shape check catches a dict naming a FeatureOperations member and skips one
        # that merely maps to an arbitrary short string (the shape _common.OPERATIONS itself uses).
        hot = ast.parse("_OPS = {'join': 'JoinFeatureOperation'}").body[0].value
        cool = ast.parse('_OPS = {"join": "x"}').body[0].value
        assert isinstance(hot, ast.Dict) and any(_names_a_feature_operation(v) for v in hot.values)
        assert not (isinstance(cool, ast.Dict) and any(_names_a_feature_operation(v) for v in cool.values))
