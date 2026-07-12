# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every public attribute a SHARED fake exposes exists on its live adsk counterpart.

The shared fakes in conftest.py impersonate live types by name; a fake attribute the live type
does not have teaches tool code an API that will AttributeError in Fusion, and a live rename or
removal (a Fusion update) must surface as a red test, not a silently-wrong mock. SHAPES in the
generated live_api_facts.py is the measured dir() membership of each type; this lint checks each
mapped fake's public surface (class attrs, methods, and self.X assignments in __init__) against
it. Bespoke per-test fakes are deliberately NOT swept - migrating them to the shared fakes is
what makes them safer.
"""

import ast
import os

import live_api_facts

_CONFTEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "conftest.py")

# conftest fake class -> the live type (a SHAPES key) it impersonates. Grows with the fakes.
_FAKE_TO_LIVE = {
    "BRepBody": "BRepBody",
    "BRepFace": "BRepFace",
    "BRepEdge": "BRepEdge",
    "MakeComp": "Component",
    "MakeDesign": "Design",
    "FakeVector3D": "Vector3D",
    "FakePoint": "Point3D",
    "FakeBoundingBox3D": "BoundingBox3D",
    "_FakeObjectCollection": "ObjectCollection",
    "_NamedCollection": "BRepBodies",
    "Plane": "Plane",
    "Cylinder": "Cylinder",
    "Line3D": "Line3D",
    "Circle3D": "Circle3D",
}

# fake.attr -> one-line reason a live-absent attribute is tolerated. Shrink-only.
_ALLOWLIST = {}


def _public_surface(cls_node):
    """Public attribute names a fake class exposes: methods, class-level assigns, and
    self.<name> assignments anywhere in its methods."""
    names = set()
    for node in cls_node.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    names.add(t.id)
    for node in ast.walk(cls_node):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self" and not t.attr.startswith("_")):
                    names.add(t.attr)
        elif isinstance(node, ast.Tuple):
            for el in node.elts:
                if (isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name)
                        and el.value.id == "self" and not el.attr.startswith("_")):
                    names.add(el.attr)
    return names


class TestSharedFakeShapesExist:
    def test_every_shared_fake_attribute_exists_live(self):
        tree = ast.parse(open(_CONFTEST, encoding="utf-8").read())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        offenders = []
        for fake, live in sorted(_FAKE_TO_LIVE.items()):
            assert fake in classes, f"mapped fake {fake} not found in conftest.py"
            shape = live_api_facts.SHAPES.get(live)
            assert shape, (
                f"SHAPES has no '{live}' - add it to a shape-dump measurement row and regenerate "
                "(py -3 tests/live/measure_api.py with Fusion up)")
            for attr in sorted(_public_surface(classes[fake])):
                if attr in shape or f"{fake}.{attr}" in _ALLOWLIST:
                    continue
                offenders.append(f"{fake}.{attr} does not exist on live {live}")
        assert not offenders, (
            "A shared fake exposes attributes its live type does not have - the fake teaches an "
            "API that will AttributeError in Fusion. Rename/remove the attribute, or if the live "
            "surface genuinely changed, re-run the probes and commit the regenerated facts:\n  "
            + "\n  ".join(offenders))

    def test_allowlist_entries_still_trip(self):
        tree = ast.parse(open(_CONFTEST, encoding="utf-8").read())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        stale = []
        for key, reason in _ALLOWLIST.items():
            assert reason.strip(), f"{key} allowlist entry needs a plain-English reason"
            fake, attr = key.split(".", 1)
            if fake not in classes or attr not in _public_surface(classes[fake]):
                stale.append(f"{key}: the fake no longer exposes it - remove the entry")
            elif attr in live_api_facts.SHAPES.get(_FAKE_TO_LIVE.get(fake, ""), ()):
                stale.append(f"{key}: the attribute exists live - remove the entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)
