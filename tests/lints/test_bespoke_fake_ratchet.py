# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Ratchet: bespoke per-file fake classes in tests/unit/ do not grow.

Most test files predate the shared fakes in conftest.py (BRepFace/BRepEdge/Plane/Cylinder/Line3D/
Circle3D/FakePoint/FakeVector3D, ...) and instead hand-roll their own - see tests/CLAUDE.md "The
legacy bespoke pattern exists in most files". A re-rolled fake drifts from the live API silently (it
is never SHAPES-swept the way a mapped conftest fake is in test_fake_shapes_exist.py), and the same
geometry shape gets reinvented file after file. This counts every top-level class in tests/unit/*.py
whose name MATCHES the fake shape - starts with Fake/_Fake, OR shadows a conftest shared-fake name
(a local redefinition instead of an import), OR matches a live adsk type name in
live_api_facts.SHAPES (e.g. a bare local ``class BRepBody:``) - and blocks the count from rising.

It is deliberately a RATCHET (count <= _BASELINE), not a zero gate: cleaning up the existing count
is a dedicated migration nobody has green-lit, so this only blocks REGRESSION - a new test builds
from the shared fakes (extending them in conftest.py is fine when the attribute is real, i.e.
present in live_api_facts.SHAPES) instead of adding another bespoke class.
"""

import ast
import os

import live_api_facts

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/lints/ -> tests/
UNIT_DIR = os.path.join(TESTS_DIR, "unit")
CONFTEST_PATH = os.path.join(TESTS_DIR, "conftest.py")

# Count of top-level tests/unit/*.py classes currently matching the bespoke-fake shape. Ratchet DOWN
# only - this is a measured legacy count (tests/CLAUDE.md's "legacy bespoke pattern"), not an
# accepted target. A genuinely novel test surface may bump this with a one-line reason comment.
# +5: model_create_component parent-nesting fakes (the createForAssemblyContext occurrence-proxy graph,
# a surface conftest's geometry fakes do not model) + the extrude parameter-expression units engine.
_BASELINE = 556


def _top_level_classes(path):
    """[(name, lineno)] for every top-level class definition in the .py file at `path`."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=str(path))
    return [(node.name, node.lineno) for node in tree.body if isinstance(node, ast.ClassDef)]


def _conftest_fake_names():
    """Every top-level class name conftest.py defines - the shared fakes a local class must not
    shadow with its OWN redefinition (an import of the same name is not a definition, so it never
    trips this)."""
    return {name for name, _ in _top_level_classes(CONFTEST_PATH)}


def _offenders_in_file(path, conftest_names, shape_names):
    """[(name, lineno)] for every top-level class in `path` matching the bespoke-fake shape:
    Fake/_Fake-prefixed, OR shadows a conftest shared-fake name, OR matches a live adsk type name
    in live_api_facts.SHAPES."""
    out = []
    for name, lineno in _top_level_classes(path):
        if (name.startswith("Fake") or name.startswith("_Fake")
                or name in conftest_names or name in shape_names):
            out.append((name, lineno))
    return out


def _all_offenders():
    conftest_names = _conftest_fake_names()
    shape_names = set(live_api_facts.SHAPES)
    offenders = []
    for fn in sorted(os.listdir(UNIT_DIR)):
        if not fn.endswith(".py"):
            continue
        for name, lineno in _offenders_in_file(os.path.join(UNIT_DIR, fn), conftest_names, shape_names):
            offenders.append(f"{fn}:{lineno}: class {name}")
    return offenders


class TestBespokeFakeRatchet:
    def test_bespoke_fake_class_count_does_not_regress(self):
        offenders = _all_offenders()
        assert len(offenders) <= _BASELINE, (
            f"bespoke fake-shaped classes in tests/unit: {len(offenders)} (baseline {_BASELINE}). "
            "Build from conftest's shared fakes - BRepFace/BRepEdge/Plane/Cylinder/Line3D/Circle3D/"
            "FakePoint/FakeVector3D - or extend them in conftest (only an attribute that is real, "
            "i.e. present in live_api_facts.SHAPES); a genuinely novel surface may bump _BASELINE "
            "with a one-line reason comment. New offenders:\n  " + "\n  ".join(offenders))

    def test_the_lint_bites(self, tmp_path):
        conftest_names = _conftest_fake_names()
        shape_names = set(live_api_facts.SHAPES)
        hot = tmp_path / "hot.py"
        hot.write_text("class FakeWidget:\n    pass\n", encoding="utf-8")
        cool = tmp_path / "cool.py"
        cool.write_text("class WidgetHelper:\n    pass\n", encoding="utf-8")
        assert _offenders_in_file(hot, conftest_names, shape_names), (
            "a Fake-prefixed top-level class must trip the scan")
        assert not _offenders_in_file(cool, conftest_names, shape_names), (
            "an unrelated class name must NOT trip the scan")
        # a bare local redefinition of a live adsk type name (no Fake prefix) trips it too.
        shadow = tmp_path / "shadow.py"
        shadow.write_text("class BRepBody:\n    pass\n", encoding="utf-8")
        assert _offenders_in_file(shadow, conftest_names, shape_names), (
            "a local class shadowing a conftest/live type name must trip the scan")
