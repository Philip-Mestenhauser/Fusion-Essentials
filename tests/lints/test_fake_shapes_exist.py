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

The map is COMPLETE by construction: any conftest class whose stripped name (leading underscore
and a Fake/Make prefix removed) matches a SHAPES key is mapped automatically, and a fake-shaped
conftest class that maps to nothing FAILS - a new shared fake cannot dodge the sweep by simply
not registering in _FAKE_TO_LIVE.
"""

import ast
import os

import live_api_facts

_CONFTEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "conftest.py")

# conftest fake class -> the live type (a SHAPES key) it impersonates. Only needed when the
# stripped class name does not literally match its SHAPES key - everything else auto-maps.
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
    "Cone": "Cone",
    "Line3D": "Line3D",
    "Circle3D": "Circle3D",
}

# fake.attr -> one-line reason a live-absent attribute is tolerated. Shrink-only.
_ALLOWLIST = {}

# Fake-shaped conftest classes with NO live SHAPES dump to sweep against yet. Shrink-only: the
# staleness check fails the moment a dump lands (auto-map then takes over) or the class goes.
_UNMAPPED_OK = {
    "FakeUnitsManager": "UnitsManager has no SHAPES dump yet - add it to a shape-dump measurement "
                        "row and regenerate (py -3 tests/live/measure_api.py with Fusion up)",
    "FakeInfiniteLine3D": "InfiniteLine3D has no SHAPES dump; every member the fake carries "
                          "(create/origin/direction/isColinearTo) is exercised live by the shipped "
                          "Add Holder command code that _holder.py keeps verbatim",
}


def _stripped(name):
    """The class name with a leading underscore and a Fake/Make prefix removed - the name the
    fake impersonates (_FakeObjectCollection -> ObjectCollection, MakeDesign -> Design)."""
    base = name.lstrip("_")
    for prefix in ("Fake", "Make"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    return base


def _auto_mapped(class_names, shapes):
    """{conftest class: SHAPES key} for every class whose stripped name IS a SHAPES key."""
    return {n: _stripped(n) for n in class_names if _stripped(n) in shapes}


def _is_fake_shaped(name, live_names):
    """test_bespoke_fake_ratchet's fake-shape discriminator, mirrored (that file is owned
    separately): Fake/_Fake-prefixed or the bare name of a live adsk type - plus the Make prefix
    conftest's builder fakes use."""
    return (name.startswith("Fake") or name.startswith("_Fake") or name.startswith("Make")
            or name in live_names)


def _live_type_names():
    """Every live adsk class name a bare conftest class could be standing in for: the MEASURED
    SHAPES keys plus every class in the generated api_surface dump - the ratchet's widened
    discriminator, mirrored. Without the api_surface half, a conftest fake named exactly like a
    live type that has no shape dump YET is invisible to the completeness gate, which is the one
    case that most needs it. Only the DISCRIMINATOR widens: the auto-map still keys on SHAPES,
    since a fake can only be swept against a type that has been measured."""
    import api_surface
    names = set(live_api_facts.SHAPES)
    for table in (api_surface.PROPERTIES, api_surface.FACTORIES):
        for key in table:
            names.add(key.rsplit(".", 1)[-1])
    return names


def _unmapped_fakes(class_names, shapes, manual, allowlist, live_names=None):
    """Fake-shaped conftest classes the sweep would silently skip: neither manually mapped, nor
    auto-mapped, nor excused by the allowlist. `live_names` is the discriminator's name set
    (defaults to `shapes`); the auto-map always keys on `shapes`."""
    auto = _auto_mapped(class_names, shapes)
    live_names = shapes if live_names is None else live_names
    return [n for n in sorted(class_names)
            if _is_fake_shaped(n, live_names)
            and n not in manual and n not in auto and n not in allowlist]


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


def _effective_map(class_names):
    """The full sweep map: auto-derived entries plus the manual table (manual wins on overlap)."""
    mapping = _auto_mapped(class_names, live_api_facts.SHAPES)
    mapping.update(_FAKE_TO_LIVE)
    return mapping


class TestSharedFakeShapesExist:
    def test_every_shared_fake_attribute_exists_live(self):
        tree = ast.parse(open(_CONFTEST, encoding="utf-8").read())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        offenders = []
        for fake, live in sorted(_effective_map(classes).items()):
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

    def test_every_fake_shaped_class_is_mapped(self):
        # The completeness gate: a NEW conftest fake that maps to nothing is a silently-unswept
        # mock - it must map (rename it so the stripped name hits a SHAPES key, add a manual
        # entry, or measure the missing live type), never just be left out.
        tree = ast.parse(open(_CONFTEST, encoding="utf-8").read())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        unmapped = _unmapped_fakes(classes, live_api_facts.SHAPES, _FAKE_TO_LIVE, _UNMAPPED_OK,
                                   live_names=_live_type_names())
        assert not unmapped, (
            "fake-shaped conftest classes the shape sweep would silently skip - map each to a "
            "SHAPES key (auto: name it after the live type; or add a _FAKE_TO_LIVE entry; or "
            "shape-dump the live type), or add a reasoned _UNMAPPED_OK entry:\n  "
            + "\n  ".join(unmapped))

    def test_unmapped_ok_entries_still_trip(self):
        tree = ast.parse(open(_CONFTEST, encoding="utf-8").read())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        stale = []
        for name, reason in _UNMAPPED_OK.items():
            assert reason.strip(), f"{name} _UNMAPPED_OK entry needs a plain-English reason"
            if name not in classes:
                stale.append(f"{name}: no such conftest class - remove the entry")
            elif _stripped(name) in live_api_facts.SHAPES:
                stale.append(f"{name}: '{_stripped(name)}' now has a SHAPES dump - the auto-map "
                             "sweeps it; remove the entry")
            elif name in _FAKE_TO_LIVE:
                stale.append(f"{name}: manually mapped in _FAKE_TO_LIVE - remove the entry")
        assert not stale, "stale _UNMAPPED_OK entries:\n  " + "\n  ".join(stale)

    def test_the_completeness_gate_bites(self):
        shapes = {"Widget": ["name"], "BoundingBox3D": ["minPoint"]}
        # a new Fake-prefixed class with no matching SHAPES key and no entry MUST be flagged...
        assert _unmapped_fakes(["FakeGizmo"], shapes, {}, {}) == ["FakeGizmo"]
        # ...auto-map catches the stripped-name matches (Fake/_Fake/Make prefixes, underscore)...
        assert _unmapped_fakes(["FakeWidget", "_FakeWidget", "MakeWidget", "FakeBoundingBox3D"],
                               shapes, {}, {}) == []
        assert _auto_mapped(["FakeBoundingBox3D"], shapes) == {"FakeBoundingBox3D": "BoundingBox3D"}
        # ...a manual entry or an allowlist entry excuses, an unrelated helper never trips.
        assert _unmapped_fakes(["FakeGizmo"], shapes, {"FakeGizmo": "Widget"}, {}) == []
        assert _unmapped_fakes(["FakeGizmo"], shapes, {}, {"FakeGizmo": "reason"}) == []
        assert _unmapped_fakes(["WidgetHelper"], shapes, {}, {}) == []
        # a bare live-type shadow (the ratchet's third shape) is fake-shaped too - and auto-maps.
        assert _is_fake_shaped("Widget", shapes) and _unmapped_fakes(["Widget"], shapes, {}, {}) == []
        # ...and a bare shadow of a live type with NO shape dump is caught by the widened
        # discriminator while the auto-map (SHAPES only) correctly refuses to map it.
        assert _unmapped_fakes(["Gadget"], shapes, {}, {}) == []
        assert _unmapped_fakes(["Gadget"], shapes, {}, {},
                               live_names=set(shapes) | {"Gadget"}) == ["Gadget"]
        # the real name set carries both halves: measured shapes and api_surface-only classes
        live = _live_type_names()
        assert set(live_api_facts.SHAPES) <= live and "MeshRepairFeature" in live

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
