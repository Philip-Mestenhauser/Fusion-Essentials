# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every shared fake declares the live type it stands for and the MEASURED rows behind it.

The shared fakes in conftest.py encode live API facts, and a provenance claim needs something that
fails when it stops holding: a renamed row, a re-measured fact, and a claim that never entered the
registry all read identically to a human reader. So the claim is STRUCTURED DATA on the fake
itself - ``@fusion_fake(live_type="Occurrence", facts=("shape-dump-design-world",))``. Nothing here
reads English: not a docstring, not a capitalization, not a source token. A declaration is data or
it does not exist, and prose around one moves no verdict.

The registry is measure_api.py. Its module-level ``ROWS`` is a list of measurement-row dicts, and
each row's ``id`` key IS the claim id - the same string VERIFIED_API_FACTS.md prints in its "claim
id" column and ``--only`` selects a row by. This lint IMPORTS that list rather than scraping the
source, so an id renamed in the registry turns every fake that cites it red.

Four rules, over the inventory test_fake_shapes_exist.py already identifies:

  1. WELL-FORMED - a declaration is exactly ONE of: the live type it impersonates (``live_type``),
     the declared fake it CONSTRUCTS (``factory_for``), or a reasoned ``scenario_double``
     classification for a double that impersonates no live type at all.
  2. RESOLVED - every id in ``facts`` is a row id in ``ROWS``.
  3. BACKED - a fake the shape sweep MAPS declares that same live type and at least one row. There
     is no way around this one: a fake-shaped class must map (automatically by name, or through its
     own declaration) or carry a reasoned _UNMAPPED_OK entry, and mapping obliges it to cite.
  4. OWNED - a top-level def that CONSTRUCTS a declared fake declares which fake that is, so no def
     building declared fakes stands beside the inventory instead of inside it.

Rule 4 triggers on the construction, never on the name: a helper that assembles an attribute bag
out of no declared fake has no member surface to sweep and no row to cite. A fake whose live type
has no SHAPES dump at all is classified in test_fake_shapes_exist.py's _UNMAPPED_OK, with the
reason that table already requires - this lint does not ask for a second copy of it.
"""

import ast
import os
import sys

import conftest
import live_api_facts
import test_fake_shapes_exist as inventory

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import measure_api  # noqa: E402  the claim-id registry - ROWS, one dict per row, keyed 'id'

# The three kinds a declaration can be. Exactly one, so every fake is either measured, owned by a
# fake that is, or classified as standing for no live type - and none of those can be silent.
_KINDS = ("live_type", "factory_for", "scenario_double")


def claim_ids():
    """Every claim id the live measurement registry defines."""
    return {row["id"] for row in measure_api.ROWS}


def _kind_problems(decls):
    """[complaint] for every declaration that is not exactly one kind, that cites rows while
    claiming to stand for no live type, or whose ``factory_for`` names something undeclared."""
    out = []
    for name, declaration in sorted(decls.items()):
        # a blank string is not a classification: the scenario_double kind IS its reason, so an
        # empty one would classify a double while saying nothing about it
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
    """[complaint] for every declared id the registry does not carry. An id that never entered the
    registry and one whose row was renamed or dropped read the same here, which is the point."""
    return [f"{name}: '{fact}' is no row id in measure_api.ROWS"
            for name, declaration in sorted(decls.items())
            for fact in declaration.get("facts", ()) if fact not in known]


def _unbacked_mapped(mapping, auto, decls):
    """[complaint] for every fake the shape sweep maps that declares nothing, declares a live type
    its own auto-mapped name contradicts, or names no measurement row.

    `mapping` is the effective sweep map, `auto` the name-derived half of it (a declaration wins the
    merge, so a fake named after one live type while declaring another would otherwise be swept
    against the declared one with the contradiction unreported)."""
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
    """The declared fakes a def CONSTRUCTS: every call in its body whose callee is one of `names`."""
    return {call.func.id for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id in names}


def _undeclared_factories(functions, decls):
    """[complaint] for every top-level def that CONSTRUCTS a declared fake without declaring which
    one. `functions` is {name: FunctionDef}; a def that constructs no declared fake is not one."""
    fakes = {n for n, d in decls.items() if d.get("live_type") or d.get("scenario_double")}
    out = []
    for name, node in sorted(functions.items()):
        built = _constructed(node, fakes)
        if built and name not in decls:
            out.append(f"{name}: constructs {', '.join(sorted(built))} - declare "
                       "@fusion_fake(factory_for=...) naming the fake it constructs")
    return out


def _declared(**kwargs):
    """One declaration dict, recorded by the REAL decorator - the bite tests below run on doctored
    data, and building it any other way would let this file's idea of the shape drift from
    conftest's."""
    return vars(conftest.fusion_fake(**kwargs)(type("_Probe", (), {})))["__fusion_fake__"]


class TestApiFactProvenance:
    def test_every_declaration_is_well_formed(self):
        bad = _kind_problems(inventory.declarations())
        assert not bad, (
            "@fusion_fake declarations in conftest.py that state no single classification:\n  "
            + "\n  ".join(bad))

    def test_every_declared_fact_resolves_against_the_registry(self):
        bad = _unresolved_facts(inventory.declarations(), claim_ids())
        assert not bad, (
            "conftest.py fakes cite claim ids measure_api.py does not carry, so nothing fails when "
            "the claim outlives the measurement. Cite the row that backs it "
            "(VERIFIED_API_FACTS.md's 'encoded in' column usually names the fake) - or, when no row "
            "carries the claim, add a measurement row and measure it live (py -3 "
            "tests/live/measure_api.py with Fusion up):\n  " + "\n  ".join(bad))

    def test_every_mapped_fake_declares_its_live_type_and_a_row(self):
        classes = inventory._conftest_classes()
        bad = _unbacked_mapped(inventory._effective_map(classes),
                               inventory._auto_mapped(classes, live_api_facts.SHAPES),
                               inventory.declarations())
        assert not bad, (
            "shared fakes the shape sweep maps onto a live type without saying what measured "
            "them:\n  " + "\n  ".join(bad))

    def test_every_factory_of_a_declared_fake_is_declared(self):
        bad = _undeclared_factories(inventory._conftest_functions(), inventory.declarations())
        assert not bad, (
            "conftest.py defs construct a declared fake without naming it, so they sit outside the "
            "inventory the shape sweep and the rules above run on:\n  " + "\n  ".join(bad))

    def test_the_declarations_read_the_real_conftest_fakes(self):
        # The rules are only as good as the reader feeding them: a reader that found nothing, or
        # that read some other attribute, would pass this lint green over any conftest at all.
        decls = inventory.declarations()
        assert decls, "no conftest.py fake carries a @fusion_fake declaration - the reader is dead"
        assert decls["FakeOccurrence"]["live_type"] == "Occurrence"
        assert "shape-dump-design-world" in decls["FakeOccurrence"]["facts"]
        assert decls["body_proxy"]["factory_for"] == "_OccurrenceProxy"
        assert decls["_EntityProxy"]["scenario_double"].strip()

    def test_the_id_gate_bites(self):
        known = {"point3d-vectorto", "find-entity-token-multi"}
        real = _declared(live_type="Point3D", facts=("point3d-vectorto",))
        assert _unresolved_facts({"FakeThing": real}, known) == []
        # an id no row carries is reported, naming it...
        assert _unresolved_facts({"FakeThing": _declared(live_type="Point3D",
                                                         facts=("point3d-vectortoo",))},
                                 known) == [
            "FakeThing: 'point3d-vectortoo' is no row id in measure_api.ROWS"]
        # ...and a STALE id reads exactly the same way: the row was renamed or dropped from the
        # registry, and the fake still cites the string it had.
        assert _unresolved_facts({"FakeThing": real}, {"point3d-vector-to"}) == [
            "FakeThing: 'point3d-vectorto' is no row id in measure_api.ROWS"]
        # a longer id built on a known one is a different id, not a citation of it
        assert _unresolved_facts({"FakeThing": _declared(facts=("point3d-vectorto-and-back",),
                                                         live_type="Point3D")}, known)
        # every declaration is checked, and the order is deterministic
        assert _unresolved_facts({"B": _declared(live_type="X", facts=("nope",)),
                                  "A": _declared(live_type="X", facts=("nope",))}, known) == [
            "A: 'nope' is no row id in measure_api.ROWS",
            "B: 'nope' is no row id in measure_api.ROWS"]

    def test_the_well_formed_gate_bites(self):
        # a declaration that states no classification, or two at once, is not a classification
        assert _kind_problems({"FakeThing": _declared(facts=("point3d-vectorto",))})
        assert _kind_problems({"FakeThing": _declared(live_type="Point3D",
                                                      scenario_double="a reason")})
        # an explicitly classified scenario double passes, and an empty reason is not one
        assert _kind_problems({"_Probe": _declared(scenario_double="stands for no live type")}) == []
        assert _kind_problems({"_Probe": _declared(scenario_double="   ")})
        # ...but it cannot carry measurement rows: a double that stands for no live type has
        # nothing measured about it, and the rows belong on the fake they were measured against
        assert _kind_problems({"_Probe": _declared(scenario_double="a reason",
                                                   facts=("point3d-vectorto",))})
        # factory_for must name a fake that is itself declared - the escape hatch this closes is a
        # def pointing at nothing, or at another factory
        fake = _declared(live_type="Point3D", facts=("point3d-vectorto",))
        assert _kind_problems({"FakeThing": fake,
                               "make_thing": _declared(factory_for="FakeThing")}) == []
        assert _kind_problems({"make_thing": _declared(factory_for="FakeGone")}) == [
            "make_thing: factory_for names 'FakeGone', which carries no @fusion_fake declaration "
            "of its own - it names the declared fake this def constructs"]

    def test_the_mapped_backing_gate_bites(self):
        auto = {"FakeGizmo": "Widget"}
        mapping = {"FakeGizmo": "Widget"}
        # a NEW fake-shaped class the sweep maps, carrying no declaration at all, must be reported
        assert _unbacked_mapped(mapping, auto, {}) == [
            "FakeGizmo: the shape sweep maps it onto live Widget, but it declares no @fusion_fake "
            "- name the live type and the row(s) that measured it"]
        # a declaration with no fact is not provenance either
        assert _unbacked_mapped(mapping, auto, {"FakeGizmo": _declared(live_type="Widget")}) == [
            "FakeGizmo: declares live Widget and no measurement row - cite the row that measured "
            "what it encodes"]
        # a declared live type that contradicts the fake's own name is caught, even though the
        # declaration wins the sweep map and so can never disagree with `mapping` itself
        assert _unbacked_mapped({"FakeGizmo": "Gadget"}, auto,
                                {"FakeGizmo": _declared(live_type="Gadget", facts=("row",))}) == [
            "FakeGizmo: declares live_type 'Gadget' while its NAME maps it onto 'Widget' - one of "
            "the two is wrong"]
        # the whole rule is satisfied by a live type plus one row
        assert _unbacked_mapped(mapping, auto,
                                {"FakeGizmo": _declared(live_type="Widget",
                                                        facts=("row",))}) == []
        # a fake the sweep does not map is this rule's business only through the sweep's own
        # completeness gate (test_fake_shapes_exist), never silently here
        assert _unbacked_mapped({}, auto, {"FakeGizmo": _declared(scenario_double="r")}) == []

    def test_the_factory_gate_bites(self):
        mod = ast.parse("def make_gizmo():\n"
                        "    return FakeGizmo('a')\n"
                        "def wrap_gizmo(g):\n"
                        "    return _GizmoProxy(g)\n"
                        "def helper(x):\n"
                        "    return {'n': x}\n"
                        "def mentions_one(g):\n"
                        "    return isinstance(g, FakeGizmo)\n")
        functions = {n.name: n for n in mod.body if isinstance(n, ast.FunctionDef)}
        decls = {"FakeGizmo": _declared(live_type="Widget", facts=("row",)),
                 "_GizmoProxy": _declared(scenario_double="stands for no live type")}
        # a def constructing a declared fake - measured OR classified - must declare which one
        assert _undeclared_factories(functions, decls) == [
            "make_gizmo: constructs FakeGizmo - declare @fusion_fake(factory_for=...) naming the "
            "fake it constructs",
            "wrap_gizmo: constructs _GizmoProxy - declare @fusion_fake(factory_for=...) naming the "
            "fake it constructs"]
        # declaring them clears it; a helper that constructs no declared fake was never one, and
        # neither is a def that merely MENTIONS one (an isinstance check is not a construction)
        declared = dict(decls, make_gizmo=_declared(factory_for="FakeGizmo"),
                        wrap_gizmo=_declared(factory_for="_GizmoProxy"))
        assert _undeclared_factories(functions, declared) == []
        assert _undeclared_factories({"helper": functions["helper"],
                                      "mentions_one": functions["mentions_one"]}, decls) == []

    def test_prose_and_capitalization_move_no_verdict(self, monkeypatch):
        # The rule this file replaced read docstrings, so a shouted word was load-bearing. Nothing
        # below reads prose - and the direction that has to be covered is a FAILING declaration: a
        # checker that let a shouted docstring EXCUSE one would still answer [] for both probes if
        # every case here were clean. So each case breaks one rule, the two probes differ only in
        # the docstring the name resolves to, and the verdicts must be equal AND non-empty.
        known = {"point3d-vectorto"}
        broken = {
            "two kinds at once": _declared(live_type="Point3D", scenario_double="a reason"),
            "an id no row carries": _declared(live_type="Point3D", facts=("no-such-row",)),
            "a mapped fake citing no row": _declared(live_type="Point3D"),
        }
        for label, declaration in broken.items():
            verdicts = []
            for doc in ("MEASURED: LIVE-MEASURED, point3d-vectorto.",
                        "it answers a measured value, or none at all."):
                monkeypatch.setattr(conftest, "_Probe", type("_Probe", (), {"__doc__": doc}),
                                    raising=False)
                decls = {"_Probe": declaration}
                verdicts.append((_kind_problems(decls), _unresolved_facts(decls, known),
                                 _unbacked_mapped({"_Probe": "Point3D"}, {}, decls)))
            assert any(verdicts[0]), f"{label}: the probe declaration must fail a rule at all"
            assert verdicts[0] == verdicts[1], (
                f"{label}: the shouted docstring changed the verdict - {verdicts}")
