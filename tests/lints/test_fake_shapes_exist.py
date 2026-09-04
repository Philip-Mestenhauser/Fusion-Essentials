# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every public attribute a SHARED fake exposes exists on its live adsk counterpart, and
every shared fake declares the live type it stands for plus the MEASURED rows behind it.
A fake-shaped conftest class maps to a SHAPES key (by name or by its own @fusion_fake declaration)
or fails; a mapped fake cites row ids that resolve against measure_api.ROWS."""

import ast
import os
import sys

import _corpus
import conftest
import live_api_facts

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFTEST = os.path.join(TESTS_DIR, "conftest.py")

sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import measure_api  # noqa: E402  the claim-id registry - ROWS, one dict per row, keyed 'id'


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
    """Fake/_Fake-prefixed, the Make prefix conftest's builder fakes use, or the bare name of a
    live adsk type."""
    return (name.startswith("Fake") or name.startswith("_Fake") or name.startswith("Make")
            or name in live_names)


def _live_type_names():
    """Every live adsk class name a bare conftest class could be standing in for: the MEASURED
    SHAPES keys plus every class in the generated api_surface dump. Without the api_surface half a
    fake named exactly like a live type that has no shape dump YET is invisible to the completeness
    gate. Only the DISCRIMINATOR widens - the auto-map still keys on SHAPES."""
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
        live = _live_type_names()
        # the discriminator's two name sources: narrow it to either half alone and a bare shadow of
        # a live type drops out of this gate with nothing to report.
        assert set(live_api_facts.SHAPES) <= live and "MeshRepairFeature" in live
        unmapped = _unmapped_fakes(classes, live_api_facts.SHAPES, _declared_map(), _UNMAPPED_OK,
                                   live_names=live)
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


# The provenance arm, over the same inventory: a declaration is STRUCTURED DATA on the fake
# (@fusion_fake(live_type=..., facts=(...))), never prose. It states exactly one kind, its facts
# resolve against measure_api.ROWS, a mapped fake declares a live type and at least one row, and a
# def that CONSTRUCTS a declared fake says which one.

_KINDS = ("live_type", "factory_for", "scenario_double")


def claim_ids():
    """Every claim id the live measurement registry defines."""
    return {row["id"] for row in measure_api.ROWS}


def _kind_problems(decls):
    out = []
    for name, declaration in sorted(decls.items()):
        # a blank string is not a classification: the scenario_double kind IS its reason
        kinds = [k for k in _KINDS if str(declaration.get(k) or "").strip()]
        if len(kinds) != 1:
            out.append(f"{name}: a declaration is exactly one of {'/'.join(_KINDS)} - this one is "
                       f"{kinds or 'none of them'} (a scenario_double needs its reason string)")
            continue
        if declaration.get("scenario_double") and declaration.get("facts"):
            out.append(f"{name}: classified as standing for no live type, so it can cite no "
                       "measurement row - put the facts on the fake they measure")
        target = declaration.get("factory_for")
        if target and target not in decls:
            out.append(f"{name}: factory_for names '{target}', which carries no @fusion_fake "
                       "declaration of its own - it names the declared fake this def constructs")
    return out


def _unresolved_facts(decls, known):
    """An id that never entered the registry and one whose row was renamed read the same here."""
    return [f"{name}: '{fact}' is no row id in measure_api.ROWS"
            for name, declaration in sorted(decls.items())
            for fact in declaration.get("facts", ()) if fact not in known]


def _unbacked_mapped(mapping, auto, decls):
    """A mapped fake that declares nothing, declares a live type its own auto-mapped NAME
    contradicts, or names no measurement row. A declaration wins the merge, so the contradiction
    is judged against `auto`, not against `mapping`."""
    out = []
    for fake, live in sorted(mapping.items()):
        declaration = decls.get(fake)
        if declaration is None:
            out.append(f"{fake}: the shape sweep maps it onto live {live}, but it declares no "
                       "@fusion_fake - name the live type and the row(s) that measured it")
        elif declaration.get("live_type") != auto.get(fake, declaration.get("live_type")):
            out.append(f"{fake}: declares live_type {declaration.get('live_type')!r} while its "
                       f"NAME maps it onto {auto[fake]!r} - one of the two is wrong")
        elif not declaration.get("facts"):
            out.append(f"{fake}: declares live {live} and no measurement row - cite the row that "
                       "measured what it encodes")
    return out


def _constructed(node, names):
    return {call.func.id for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id in names}


def _undeclared_factories(functions, decls):
    fakes = {n for n, d in decls.items() if d.get("live_type") or d.get("scenario_double")}
    out = []
    for name, node in sorted(functions.items()):
        built = _constructed(node, fakes)
        if built and name not in decls:
            out.append(f"{name}: constructs {', '.join(sorted(built))} - declare "
                       "@fusion_fake(factory_for=...) naming the fake it constructs")
    return out


class TestApiFactProvenance:
    def test_every_declaration_is_well_formed(self):
        bad = _kind_problems(declarations())
        assert not bad, (
            "@fusion_fake declarations in conftest.py that state no single classification:\n  "
            + "\n  ".join(bad))

    def test_every_declared_fact_resolves_against_the_registry(self):
        bad = _unresolved_facts(declarations(), claim_ids())
        assert not bad, (
            "conftest.py fakes cite claim ids measure_api.py does not carry, so nothing fails when "
            "the claim outlives the measurement. Cite the row that backs it "
            "(VERIFIED_API_FACTS.md's 'encoded in' column usually names the fake) - or, when no row "
            "carries the claim, add a measurement row and measure it live (py -3 "
            "tests/live/measure_api.py with Fusion up):\n  " + "\n  ".join(bad))

    def test_every_mapped_fake_declares_its_live_type_and_a_row(self):
        classes = _conftest_classes()
        bad = _unbacked_mapped(_effective_map(classes),
                               _auto_mapped(classes, live_api_facts.SHAPES),
                               declarations())
        assert not bad, (
            "shared fakes the shape sweep maps onto a live type without saying what measured "
            "them:\n  " + "\n  ".join(bad))

    def test_every_factory_of_a_declared_fake_is_declared(self):
        bad = _undeclared_factories(_conftest_functions(), declarations())
        assert not bad, (
            "conftest.py defs construct a declared fake without naming it, so they sit outside the "
            "inventory the shape sweep and the rules above run on:\n  " + "\n  ".join(bad))

    def test_the_declarations_read_the_real_conftest_fakes(self):
        # a reader that found nothing, or read some other attribute, would pass every rule above
        # green over any conftest at all.
        decls = declarations()
        assert decls, "no conftest.py fake carries a @fusion_fake declaration - the reader is dead"
        assert decls["FakeOccurrence"]["live_type"] == "Occurrence"
        assert "shape-dump-design-world" in decls["FakeOccurrence"]["facts"]
        assert decls["body_proxy"]["factory_for"] == "_OccurrenceProxy"
        assert decls["_EntityProxy"]["scenario_double"].strip()
