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
and a Fake/Make prefix removed) matches a SHAPES key is mapped automatically, any other fake states
its live type in its own ``@fusion_fake`` declaration, and a fake-shaped conftest class that maps to
nothing FAILS - a new shared fake cannot dodge the sweep by simply not declaring.

Those declarations are also the inventory this file publishes: ``declarations()`` and
``_conftest_functions()`` are what test_api_fact_provenance.py runs its provenance rules over, so
the fakes, the defs that construct them and the live types they stand for are enumerated ONCE.
"""

import ast
import os

import _corpus
import conftest
import live_api_facts

_CONFTEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "conftest.py")


def _conftest_classes():
    """class name -> its ClassDef, for every class at conftest.py's module scope. Every check here
    starts from this map, and _corpus parses that (large) file once for the whole run."""
    return {n.name: n for n in _corpus.tree(_CONFTEST).body if isinstance(n, ast.ClassDef)}


def _conftest_functions():
    """function name -> its FunctionDef, for every def at conftest.py's module scope - the defs
    that construct the fakes above."""
    return {n.name: n for n in _corpus.tree(_CONFTEST).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _own_declaration(obj):
    """The @fusion_fake declaration an object carries ITSELF, or None.

    ``vars(obj)`` rather than a getattr is the load-bearing part: the attribute is INHERITED, so a
    subclass that declares nothing (FakeSetup extends FakeCAMFolder) would otherwise answer its
    base's declaration - swept against the base's live type, on provenance it never stated.
    """
    return vars(obj).get("__fusion_fake__")


def declarations():
    """{name: its @fusion_fake declaration} for every conftest class/def carrying one of its OWN.

    Read off the objects, not the source: the decorator records structured data, so there is
    nothing to parse.
    """
    found = {}
    for name in list(_conftest_classes()) + list(_conftest_functions()):
        obj = getattr(conftest, name, None)
        declaration = _own_declaration(obj) if obj is not None else None
        if declaration is not None:
            found[name] = declaration
    return found


def _declared_map(decls=None):
    """{fake: the SHAPES key its declaration names} - the map's manual half, carried by the fakes
    themselves rather than by a table beside them."""
    decls = declarations() if decls is None else decls
    return {name: d["live_type"] for name, d in decls.items() if d.get("live_type")}

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
    """The full sweep map: auto-derived entries plus every declared live type (a declaration wins
    on overlap)."""
    mapping = _auto_mapped(class_names, live_api_facts.SHAPES)
    mapping.update(_declared_map())
    return mapping


class TestSharedFakeShapesExist:
    def test_every_shared_fake_attribute_exists_live(self):
        classes = _conftest_classes()
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
        # mock - it must map (rename it so the stripped name hits a SHAPES key, declare its live
        # type, or measure the missing live type), never just be left out.
        classes = _conftest_classes()
        unmapped = _unmapped_fakes(classes, live_api_facts.SHAPES, _declared_map(), _UNMAPPED_OK,
                                   live_names=_live_type_names())
        assert not unmapped, (
            "fake-shaped conftest classes the shape sweep would silently skip - map each to a "
            "SHAPES key (auto: name it after the live type; or declare @fusion_fake(live_type=...) "
            "on it; or shape-dump the live type), or add a reasoned _UNMAPPED_OK entry:\n  "
            + "\n  ".join(unmapped))

    def test_the_sweep_map_carries_the_declared_fakes(self):
        # The map is two halves and the MERGE is load-bearing: a fake whose stripped name is no
        # SHAPES key reaches the sweep ONLY through its declaration, so a map that dropped the
        # declared half would quietly stop sweeping every one of them, and no other check would
        # report it.
        classes = _conftest_classes()
        mapping = _effective_map(classes)
        auto = _auto_mapped(classes, live_api_facts.SHAPES)
        for fake, live in (("FakePoint", "Point3D"), ("MakeComp", "Component"),
                           ("_NamedCollection", "BRepBodies")):
            assert fake not in auto, f"{fake} auto-maps now - this check needs a declared-ONLY fake"
            assert mapping.get(fake) == live, (
                f"{fake} is swept only because its declaration names {live} - the declared half of "
                "the map is not merged in")

    def test_unmapped_ok_entries_still_trip(self):
        classes = _conftest_classes()
        stale = []
        for name, reason in _UNMAPPED_OK.items():
            assert reason.strip(), f"{name} _UNMAPPED_OK entry needs a plain-English reason"
            if name not in classes:
                stale.append(f"{name}: no such conftest class - remove the entry")
            elif _stripped(name) in live_api_facts.SHAPES:
                stale.append(f"{name}: '{_stripped(name)}' now has a SHAPES dump - the auto-map "
                             "sweeps it; remove the entry")
            elif name in _declared_map():
                stale.append(f"{name}: declares a @fusion_fake live_type - remove the entry")
        assert not stale, "stale _UNMAPPED_OK entries:\n  " + "\n  ".join(stale)

    def test_the_completeness_gate_bites(self):
        shapes = {"Widget": ["name"], "BoundingBox3D": ["minPoint"]}
        # a new Fake-prefixed class with no matching SHAPES key and no entry MUST be flagged...
        assert _unmapped_fakes(["FakeGizmo"], shapes, {}, {}) == ["FakeGizmo"]
        # ...auto-map catches the stripped-name matches (Fake/_Fake/Make prefixes, underscore)...
        assert _unmapped_fakes(["FakeWidget", "_FakeWidget", "MakeWidget", "FakeBoundingBox3D"],
                               shapes, {}, {}) == []
        assert _auto_mapped(["FakeBoundingBox3D"], shapes) == {"FakeBoundingBox3D": "BoundingBox3D"}
        # ...a declared live type (the map's manual half) or an allowlist entry excuses, an
        # unrelated helper never trips.
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

    def test_a_declaration_is_never_inherited(self, monkeypatch):
        # The reader's load-bearing choice, driven through declarations() itself. A subclass that
        # declares nothing must be ABSENT from the inventory: reading the attribute with a getattr
        # would answer the BASE's declaration for it, and the subclass would then be swept against
        # the base's live type, on provenance it never stated, while reading green.
        import test_fake_shapes_exist as this_module

        class _UndeclaredChild(conftest.FakeSetup):
            pass

        monkeypatch.setattr(conftest, "_UndeclaredChild", _UndeclaredChild, raising=False)
        classes = dict(_conftest_classes())
        classes["_UndeclaredChild"] = classes["FakeSetup"]   # the reader's AST half, stood in for
        monkeypatch.setattr(this_module, "_conftest_classes", lambda: classes)
        decls = declarations()
        assert decls["FakeSetup"]["live_type"] == "Setup", "the declaring base must still be read"
        assert "_UndeclaredChild" not in decls, (
            "declarations() answered a subclass with its BASE's declaration - it must read the "
            "declaration an object carries ITSELF, never an inherited one")

    def test_allowlist_entries_still_trip(self):
        classes = _conftest_classes()
        stale = []
        for key, reason in _ALLOWLIST.items():
            assert reason.strip(), f"{key} allowlist entry needs a plain-English reason"
            fake, attr = key.split(".", 1)
            if fake not in classes or attr not in _public_surface(classes[fake]):
                stale.append(f"{key}: the fake no longer exposes it - remove the entry")
            elif attr in live_api_facts.SHAPES.get(_effective_map(classes).get(fake, ""), ()):
                stale.append(f"{key}: the attribute exists live - remove the entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)
