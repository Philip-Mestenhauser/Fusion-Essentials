# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a de-dup/comparison key over Fusion entities is ``_common.native_identity``, never a bare
``entityToken`` - keyed, or compared with ``==``.

``safe(lambda b=b: b.entityToken) or id(b)`` is wrong in BOTH directions at once, and each direction
is measured:

  * an entityToken is DOCUMENT-LOCAL - two bodies reached through two x-refs of one design read
    byte-identical tokens, and every document's ROOT COMPONENT reads one shared token (measured on a
    CAM job assembled from 7 source documents) - so keyed on it DISTINCT entities MERGE and all but
    one drop out of the walk with no trace;
  * a WRAPPER's own token differs from its native's - a body and its occurrence proxy read two
    tokens - so one physical body SPLITS into two entries and every per-body sum counts it twice.

``_common.native_identity`` pairs the native's token with its source document's lineage urn and
answers both; ``test_helper_duplication.py`` keys on that symbol's NAME. This lint keys on the
SHAPE instead, so a copy that names nothing shared is still caught. Three shapes:

1. **the fallback form** - an ``or`` expression mentioning ``entityToken`` on one side and calling
   ``id()`` on the other, anywhere under ``tools/``;
2. **the mapping-key form** - a token used as a dict/set KEY (``d[tok] = ...``, ``d.get(tok)``,
   ``tok in d``, ``s.add(tok)``, ``d.setdefault(tok, ...)``), directly or through a local variable
   or an ``or <fallback>``, where the token is read off an entity the function walked out of a
   collection of BODIES or COMPONENTS - plus the SET/DICT-key/generator comprehension that gathers
   such tokens, where the collection built IS the key set and no subscript ever appears. A LIST
   comprehension of tokens is left alone: that is how a tool publishes HANDLES, which is the wrapper
   token's proper job.
3. **the equality-compare form** - an ``==`` / ``!=`` whose BOTH sides are bare tokens, written
   inline or held in locals the frame read a token into (``ta, tb = safe(lambda: a.entityToken),
   safe(lambda: b.entityToken)`` then ``ta == tb``). This is the same MERGE as shape 2 with no
   mapping in sight: the verdict "these two are one entity" is exactly what a document-local value
   cannot support, and every ``is True`` branch downstream of it takes the wrong side on a
   cross-document pair. The scope of shape 2 is deliberately NOT applied - a comparison names its
   two operands, so nothing has to be inferred about what was walked, and where the identity
   degenerates to the bare token (an OCCURRENCE, measured to carry neither chain)
   ``native_identity`` answers the same verdict, so satisfying the rule costs that site nothing. A
   token compared against a WIRE STRING (a handle round-trip) has one non-token side and is not this
   shape.

All three shapes read a bare token in any of the three spellings this repo writes it in: the attribute,
the ``getattr`` name string, and a call to ``_common.native_token`` - the sanctioned reader, which
closes the native-vs-proxy SPLIT and does nothing about the cross-document MERGE.

Shape 2 is scoped to body/component collections deliberately, and the scope is what makes it
land at ZERO false positives over the whole corpus. Keying a token is only a defect for a kind whose
identity carries MORE than the token, which is any kind carrying one of the two members
``_source_document_urn`` walks. Measured (SHAPES): ``BRepBody``, ``MeshBody`` AND ``Sketch`` carry
parentComponent; ``Component`` carries parentDesign, and test_common.py pins that side as an
exhaustive census (carriers == ['Component']) - the parentComponent side has no such census, so it is
a list of what was measured, not of what exists.

An OCCURRENCE is measured to carry NEITHER, so its identity degenerates to the bare token and the
corpus's before/after occurrence census keyed on one is EQUIVALENT to the identity key, not a defect.
A SKETCH is the opposite case: it does reach a source document, so a token key over sketches merges
across x-refs exactly as one over bodies does, and its absence from shape 2 is an UNCLOSED SPELLING
- the collection recognizer does not know a sketches collection - not an equivalence. Shape 3 needs
no such recognizer, so a sketch COMPARISON is caught by its own two operands. The collection is recognized by the naming
convention the Fusion API and this repo share - a name ending in Bodies/_bodies or
Components/_components - searched through the wrapper idiom
(``iter_collection(safe(lambda: host.bRepBodies))``).

A ``for`` over such a collection marks its loop variable walked whether the target is one name
(``for b in comp.bRepBodies``) or a TUPLE of them (``for b, nm in zip(tool_bodies, names)``) - the
zip target binds a body exactly as the bare one does, so a token key inside that loop is seen.

What it still does NOT see, and what stays a reader's judgment: a token key over a body or component
the function did not itself walk out of such a collection - one handed in as a parameter, read off a
single entity held in a variable, or bound by a target this walk reads no name from (a starred
element, an attribute or a subscript); and a key whose two halves are assembled across separate
statements. It catches the shapes that have been written, not every shape that could be - each round
only closes the spellings someone thought of.

It also has one known FALSE-POSITIVE direction, with no corpus site: a GeneratorExp of tokens over a
body collection consumed by ``list(...)`` is handle publication, which the LIST comprehension rule
allows and the generator rule flags. Reading the consumer is not attempted - that guard would have to
recognize every consumer that IS a key set (``set``, ``dict``, ``frozenset``, a membership test) to
stay sound, and getting it wrong blinds the rule instead of noising it. The failure direction here is
a red lint with an allowlist path, which a reader can resolve; a silent miss is not.
"""

import ast
import os

import _corpus
import live_api_facts
from conftest import TOOLS_DIR


# (module basename without .py, enclosing function) -> why this site is exempt. A reason must state
# a fact about THIS site. An entry prefixed 'gap:' is NOT an exemption-as-correct: it is a named
# open defect nobody has fixed yet (the convention test_postconditions_declared._EXEMPT uses), and
# it only stays legal while the reason names what was measured. Kept honest by
# test_allowlist_entries_still_trip; the table only shrinks.
_ALLOWLIST = {}


def _mentions_entity_token(node):
    """True when a BARE document-local token is read anywhere in this subtree, in any of the three
    spellings this repo writes it in: the attribute (`b.entityToken`), the getattr name string, and
    a call to `_common.native_token(...)`.

    native_token counts because it IS the sanctioned reader for a bare token - promoted in
    _common.MAP_BLURB, denylisted in test_helper_duplication.py - which makes it the likeliest
    spelling of the next copy, not an unlikely one. It resolves a proxy to its native, so it closes
    the SPLIT half of the defect; it does nothing about the MERGE half, because the value it returns
    is still document-local. Only native_identity answers both. (test_helper_duplication bans
    REDEFINING native_token, never calling it - so nothing else covers this.)"""
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr == "entityToken":
            return True
        if isinstance(n, ast.Constant) and n.value == "entityToken":
            return True
        if isinstance(n, ast.Call) and _func_name(n) == "native_token":
            return True
    return False


def _func_name(call):
    """The called name, whether written bare or through a module: `native_token(x)` and
    `_common.native_token(x)` both answer 'native_token'."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else None


def _calls_id(node):
    """True when this subtree calls the builtin `id`."""
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "id"
               for n in ast.walk(node))


def _is_token_or_id_key(node):
    """True for `<... entityToken ...> or id(...)` - either operand order, any wrapping (a bare
    attribute read, a safe(lambda) around it, a getattr with a default)."""
    if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
        return False
    return (any(_mentions_entity_token(v) for v in node.values)
            and any(_calls_id(v) for v in node.values))


# The mapping methods whose FIRST argument is a key (or a set member).
_KEYED_CALLS = ("get", "add", "setdefault", "pop")


def _key_positions(node):
    """Every expression this node uses as a MAPPING KEY - a dict/set lookup, store, membership test
    or insertion. The VALUE side is deliberately not read: publishing a token as a dict VALUE (a
    handle a tool hands back) is the wrapper token's proper job."""
    if isinstance(node, ast.Subscript):
        return [node.slice]
    if isinstance(node, ast.Compare):
        return [node.left for op in node.ops if isinstance(op, (ast.In, ast.NotIn))]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _KEYED_CALLS and node.args):
        return [node.args[0]]
    return []


def _walks_bodies_or_components(expr):
    """True when an iteration draws from a collection of BODIES or COMPONENTS, by the naming
    convention the Fusion API and this repo share (a name ending Bodies/_bodies or
    Components/_components). The WHOLE expression is searched, since the house idiom wraps the
    collection: iter_collection(safe(lambda: host.bRepBodies)), safe(lambda: c.bRepBodies) or []."""
    for n in ast.walk(expr):
        name = n.attr if isinstance(n, ast.Attribute) else (n.id if isinstance(n, ast.Name) else None)
        if name and (name.lower().endswith("bodies") or name.lower().endswith("components")):
            return True
    return False


def _reads_any(expr, names):
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(expr))


def _target_names(target):
    """The loop variables one `for` target binds: `for b in ...` binds one, `for b, nm in zip(...)`
    binds each Name element of the tuple. Both are the same walk as far as this rule is concerned -
    a body reached through a zip is still a body the function walked out of a collection - and a
    tuple target that bound nothing would make a token key inside that loop invisible. Nested
    tuples unpack the same way; a starred or attribute/subscript element binds no local name here."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {n for element in target.elts for n in _target_names(element)}
    return set()


def _token_gathering_comprehension(node):
    """True for a SET/DICT-key/generator comprehension that gathers bare tokens off a body or
    component collection - `{native_token(b) for b in host.bRepBodies}` and the generator feeding
    one. That set IS the key collection, so no `d[tok]` ever appears for _key_positions to see.

    LIST comprehensions are excluded on purpose: a list of tokens is how a tool publishes HANDLES,
    which is the wrapper token's proper job."""
    if isinstance(node, (ast.SetComp, ast.GeneratorExp)):
        element = node.elt
    elif isinstance(node, ast.DictComp):
        element = node.key
    else:
        return False
    return (_mentions_entity_token(element)
            and any(_walks_bodies_or_components(g.iter) for g in node.generators))


def _token_bindings(assign):
    """The local names one assignment binds to a BARE token VALUE.

    An element-wise tuple assignment is paired up element by element
    (``ta, tb = safe(lambda: a.entityToken), safe(lambda: b.entityToken)``), so a sibling bound to
    something else in the same statement is not marked a token; any other shape marks every name the
    target binds."""
    out = set()
    for target in assign.targets:
        if (isinstance(target, ast.Tuple) and isinstance(assign.value, ast.Tuple)
                and len(target.elts) == len(assign.value.elts)):
            for element, value in zip(target.elts, assign.value.elts):
                if _mentions_entity_token(value):
                    out |= _target_names(element)
        elif _mentions_entity_token(assign.value):
            out |= _target_names(target)
    return out


def _is_token_identity_compare(node, token_vars):
    """True for an `==`/`!=` whose EVERY side is a bare token - read inline, or held in a local this
    frame bound to one. Both sides must be tokens: a token compared against a wire string is a
    handle round-trip, not a claim that two entities are one."""
    if not isinstance(node, ast.Compare) or not node.ops:
        return False
    if not all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
        return False
    sides = [node.left] + list(node.comparators)
    return all(_mentions_entity_token(s) or _reads_any(s, token_vars) for s in sides)


def _offenders(tree):
    """[(enclosing function name, lineno)] for every offending shape in `tree`. The function NAME is
    what an allowlist entry addresses - a line number moves with any edit above it.

    `walked` holds the loop variables bound to a body/component collection in scope, `tokens` the
    locals assigned a token read off one of them, and `token_vars` every local this frame bound to a
    token whatever it was read off - the compare rule names its own operands, so it needs no walk to
    scope it. A nested function's own PARAMETERS shadow all three: without that, one function's
    `for b in comp.bRepBodies` would make every other function's `b` parameter look like a walked
    body."""
    found = []

    def walk(node, fname, walked, tokens, token_vars):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = {a.arg for a in child.args.args + child.args.kwonlyargs}
                walk(child, child.name, walked - params, tokens - params, token_vars - params)
                continue
            if isinstance(child, ast.For) and _walks_bodies_or_components(child.iter):
                walked = walked | _target_names(child.target)
            if isinstance(child, ast.Assign):
                token_vars = token_vars | _token_bindings(child)
                if _mentions_entity_token(child.value) and _reads_any(child.value, walked):
                    tokens = tokens | {t.id for t in child.targets if isinstance(t, ast.Name)}
            if (_is_token_or_id_key(child) or _token_gathering_comprehension(child)
                    or _is_token_identity_compare(child, token_vars)):
                found.append((fname, child.lineno))
            else:
                for key in _key_positions(child):
                    if ((_mentions_entity_token(key) and _reads_any(key, walked))
                            or _reads_any(key, tokens)):
                        found.append((fname, child.lineno))
                        break
            walk(child, fname, walked, tokens, token_vars)

    walk(tree, "<module>", frozenset(), frozenset(), frozenset())
    return found


def _tool_modules():
    return [fn for fn in sorted(os.listdir(TOOLS_DIR)) if fn.endswith(".py")]


class TestEntityKeysUseNativeIdentity:
    def test_no_tool_module_keys_on_a_bare_entity_token(self):
        offenders = []
        for fn in _tool_modules():
            for fname, lineno in _offenders(_corpus.tree(os.path.join(TOOLS_DIR, fn))):
                if (fn[:-3], fname) in _ALLOWLIST:
                    continue
                offenders.append(f"{fn}:{lineno} (in {fname})")
        assert not offenders, (
            "an entityToken is DOCUMENT-LOCAL and a wrapper's own token is not its native's, so a "
            "key built on one MERGES distinct entities across documents (two bodies reached through "
            "two x-refs; every document's root component) and SPLITS one body from its own proxy. "
            "Key on _common.native_identity(entity) instead - as the de-dup fallback "
            "'native_identity(b) or id(b)' (keep the id() last resort: it over-counts an "
            "identity-less entity rather than merging it), or as the mapping key itself. A "
            "same-entity COMPARISON runs on _common.same_component (two components) or on two "
            "native_identity values gated so that an unread one answers unknown instead of "
            "comparing equal to the other unread one. A site "
            "keying something native_identity does not describe - or a named open defect - gets an "
            "_ALLOWLIST entry with a reason:\n  " + "\n  ".join(offenders))

    def test_allowlist_entries_still_trip(self):
        # An entry pointing at a site that no longer carries the shape is dead weight hiding the
        # next copy; one pointing at a module or function that is gone is worse.
        stale = []
        for (mod_name, fname), reason in _ALLOWLIST.items():
            assert reason.strip(), f"({mod_name}, {fname}) allowlist entry needs a reason"
            path = os.path.join(TOOLS_DIR, mod_name + ".py")
            if not os.path.exists(path):
                stale.append(f"({mod_name}, {fname}): no such module - remove the entry")
                continue
            if fname not in {n for n, _ in _offenders(_corpus.tree(path))}:
                stale.append(f"({mod_name}, {fname}): no longer keys on a bare entityToken - "
                             "remove the entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)

    def test_the_lint_bites(self):
        def one(src):
            return _offenders(ast.parse(src))

        # the copied shape, in the spellings it has been written in
        assert one("key = safe(lambda b=b: b.entityToken) or id(b)")
        assert one("key = b.entityToken or id(b)")
        assert one('key = getattr(c, "entityToken", None) or id(c)')
        # operand order does not launder it
        assert one("key = id(b) or safe(lambda b=b: b.entityToken)")
        # the correct key, and its legal last resort, are NOT flagged
        assert not one("key = _common.native_identity(b) or id(b)")
        assert not one("key = _common.native_identity(b) or safe(lambda b=b: b.name)")
        # neither is a token published as a HANDLE, or an id() fallback over something else
        assert not one('payload["handle"] = safe(lambda: b.entityToken)')
        assert not one("key = safe(lambda b=b: b.name) or id(b)")
        # the enclosing function is what an allowlist entry addresses
        assert one("def f():\n    return b.entityToken or id(b)\n")[0][0] == "f"
        assert one("k = b.entityToken or id(b)\n")[0][0] == "<module>"

    def test_the_mapping_key_shape_bites(self):
        def one(src):
            return _offenders(ast.parse(src))

        # The two shapes the fallback rule could not see, in the spellings they were written in -
        # this is the regression test for the lint itself.
        assert one("def f(d):\n"
                   "    for comp in _view_common.all_display_components(design):\n"
                   "        ident = safe(lambda comp=comp: comp.entityToken)\n"
                   "        d[ident] = 1\n")                        # via a local variable
        assert one("def f(d):\n"
                   "    for comp in _view_common.all_display_components(design):\n"
                   "        saved = d.get(safe(lambda comp=comp: comp.entityToken) or '')\n")
        # the other keyed forms, and the wrapper idiom around the collection
        assert one("def f(s):\n"
                   "    for b in _common.iter_collection(safe(lambda: host.bRepBodies)):\n"
                   "        s.add(b.entityToken)\n")
        assert one("def f(s):\n"
                   "    for b in comp.meshBodies:\n"
                   "        if b.entityToken in s:\n            pass\n")
        assert one("def f(d):\n"
                   "    for b in _geom.owning_bodies(faces):\n"
                   "        d.setdefault(b.entityToken, []).append(1)\n")
        # ...and the correct key over the same walk is NOT flagged
        assert not one("def f(d):\n"
                       "    for comp in _view_common.all_display_components(design):\n"
                       "        ident = _common.native_identity(comp)\n"
                       "        d[ident] = 1\n")
        assert not one("def f(d):\n"
                       "    for b in comp.bRepBodies:\n"
                       "        d[_common.native_identity(b)] = 1\n")

    def test_native_token_counts_as_a_bare_token(self):
        def one(src):
            return _offenders(ast.parse(src))

        # native_token is the SANCTIONED reader for a bare document-local token, so it is the
        # likeliest spelling of the next copy, not an unlikely one. It resolves a proxy to its
        # native - closing the SPLIT half - but what it returns is still document-local, so it
        # MERGES exactly as the raw attribute does.
        assert one("def f(d):\n"
                   "    for b in comp.bRepBodies:\n"
                   "        tok = _common.native_token(b)\n"
                   "        d[tok] = 1\n")                          # plain local
        assert one("def f():\n"
                   "    for b in _common.iter_collection(safe(lambda: host.bRepBodies)):\n"
                   "        before.add(_common.native_token(b))\n")  # set insertion
        assert one("def f(before):\n"
                   "    for b in comp.bRepBodies:\n"
                   "        if _common.native_token(b) not in before:\n            pass\n")
        # the module-qualified and bare spellings are the same call
        assert one("def f(d):\n"
                   "    for b in comp.bRepBodies:\n"
                   "        d[native_token(b)] = 1\n")
        # and the fallback shape written with it is the same defect
        assert one("key = _common.native_token(b) or id(b)")
        # native_IDENTITY is not native_token - the correct key stays unflagged
        assert not one("def f(d):\n"
                       "    for b in comp.bRepBodies:\n"
                       "        d[_common.native_identity(b)] = 1\n")

    def test_a_comprehension_that_gathers_tokens_is_caught(self):
        def one(src):
            return _offenders(ast.parse(src))

        # A set built OF tokens is itself the key collection - no `d[tok]` ever appears - so the
        # mapping-key walk cannot see it, and _token_gathering_comprehension is what covers it. A
        # before/after census over a join's bodies is the shape that reaches this.
        assert one("def f():\n"
                   "    before = {t for t in (_common.native_token(b) for b in\n"
                   "              _common.iter_collection(safe(lambda: host.bRepBodies))) if t}\n")
        assert one("def f():\n"
                   "    keys = {safe(lambda b=b: b.entityToken) for b in host.bRepBodies}\n")
        assert one("def f():\n"
                   "    d = {b.entityToken: b.name for b in comp.bRepBodies}\n")
        # a LIST of tokens is how a tool publishes HANDLES - the wrapper token's proper job
        assert not one("def f():\n"
                       "    handles = [safe(lambda b=b: b.entityToken) for b in comp.bRepBodies]\n")
        # a dict keyed on something else, with the token as the VALUE, is the same proper job
        assert not one("def f():\n"
                       "    d = {b.name: b.entityToken for b in comp.bRepBodies}\n")
        # the identity key over the same comprehension is correct
        assert not one("def f():\n"
                       "    before = {_common.native_identity(b) for b in comp.bRepBodies}\n")
        # and a comprehension over a NON body/component collection is out of scope, as above
        assert not one("def f():\n"
                       "    d = {c.entityToken for c in sketch.sketchCurves}\n")

    def test_the_scoping_argument_matches_what_was_measured(self):
        # The scope above rests on WHICH kinds can reach a source document, so the claims it cites
        # are pinned here rather than left as prose. Only the parentDesign side is an exhaustive
        # census; the parentComponent side is asserted for the two facts the argument uses - Sketch
        # IS a carrier (which is what makes the sketch exclusion an unclosed spelling rather than an
        # equivalence) and an Occurrence carries neither (which is what makes an occurrence token key
        # equivalent to the identity key).
        shapes = live_api_facts.SHAPES
        assert sorted(k for k, v in shapes.items() if "parentDesign" in v) == ["Component"]
        assert "parentComponent" in shapes["Sketch"]
        assert "parentComponent" in shapes["BRepBody"] and "parentComponent" in shapes["MeshBody"]
        assert "parentComponent" not in shapes["Occurrence"]
        assert "parentDesign" not in shapes["Occurrence"]
        # The sketch-ENTITY kinds carry no measured shape at all, which is why nothing above claims
        # how they read.
        assert not [k for k in shapes if k.startswith("Sketch") and k != "Sketch"]

    def test_the_mapping_key_shape_is_scoped_to_bodies_and_components(self):
        def one(src):
            return _offenders(ast.parse(src))

        # _source_document_urn walks exactly two members - parentComponent.parentDesign and
        # parentDesign - so native_identity's urn half is unreachable for any kind carrying neither,
        # and the pair degenerates to the bare token there. An OCCURRENCE is the measured instance
        # (SHAPES records it with neither member). A sketch CURVE and a sketch POINT are not in
        # SHAPES at all, so nothing here says which way they read; they are out of this rule's scope
        # because the collection recognizer does not name their collections, not because a token key
        # over them was shown equivalent. These are the corpus's real token-keyed maps, and the scope
        # is what keeps this rule landable.
        assert not one("def f(d):\n"
                       "    for c in sketch.sketchCurves:\n"
                       "        d[c.entityToken] = 1\n")
        assert not one("def f(d):\n"
                       "    for o in _common.iter_collection(comp.occurrences):\n"
                       "        d.add(o.entityToken)\n")
        # a token published as a VALUE is the wrapper token doing its proper job (a handle)
        assert not one("def f(d):\n"
                       "    for b in comp.bRepBodies:\n"
                       "        d[b.name] = safe(lambda b=b: b.entityToken)\n")

    def test_an_equality_compare_between_two_bare_tokens_is_caught(self):
        def one(src):
            return _offenders(ast.parse(src))

        # The shape that has to be caught by its OPERANDS rather than by a walk: two components
        # handed in as parameters, their tokens read into locals, the verdict taken off `==`.
        assert one("def same(a, b):\n"
                   "    ta, tb = safe(lambda: a.entityToken), safe(lambda: b.entityToken)\n"
                   "    return ta == tb\n") == [("same", 3)]
        # written inline, and in the other spellings
        assert one("def same(a, b):\n    return a.entityToken == b.entityToken\n")
        assert one("def same(a, b):\n"
                   "    return _common.native_token(a) == _common.native_token(b)\n")
        assert one("def differ(a, b):\n    return a.entityToken != b.entityToken\n")
        # ONE token side is not this shape: a token compared against a wire string is a handle
        # round-trip inside one document, which is what a handle is for.
        assert not one("def f(want, ent):\n    return ent.entityToken == want\n")
        assert not one("def f(ent):\n    return ent.entityToken == '/v4BAAEAAwAAAAAAAAAAAAAA'\n")
        # the correct comparisons
        assert not one("def same(a, b):\n    return _common.same_component(a, b) is True\n")
        assert not one("def same(a, b):\n"
                       "    ia, ib = native_identity(a), native_identity(b)\n"
                       "    return None if (ia is None or ib is None) else ia == ib\n")

    def test_only_the_token_half_of_a_tuple_assignment_is_marked(self):
        # `tok, name = b.entityToken, b.name` binds ONE token: marking `name` too would flag the
        # next `name == other` as an identity merge it is not.
        src = ("def f(a, b):\n"
               "    tok, name = a.entityToken, a.name\n"
               "    other, onm = b.entityToken, b.name\n"
               "    return name == onm\n")
        assert _offenders(ast.parse(src)) == []
        # ...and the token halves of the same two statements still compare as the merge they are
        src = src.replace("return name == onm", "return tok == other")
        assert _offenders(ast.parse(src)) == [("f", 4)]

    def test_a_tuple_loop_target_walks_its_bodies(self):
        def one(src):
            return _offenders(ast.parse(src))

        # `for b, nm in zip(tool_bodies, names)` binds a body exactly as `for b in bodies` does -
        # mesh_combine's apart-check walks a zip like this - so a token key inside that loop is the
        # same defect. A target that binds no name from a body collection stays unwalked.
        assert one("def f(d):\n"
                   "    for b, nm in zip(tool_bodies, tool_names):\n"
                   "        d[b.entityToken] = nm\n") == [("f", 3)]
        assert one("def f(s):\n"
                   "    for nm, b in zip(names, comp.bRepBodies):\n"
                   "        s.add(_common.native_token(b))\n") == [("f", 3)]
        assert one("def f(d):\n"
                   "    for (b, nm), i in zip(zip(tool_bodies, names), counts):\n"
                   "        d.setdefault(b.entityToken, []).append(i)\n") == [("f", 3)]
        # the identity key over the same zip walk is correct, and a zip over a NON body/component
        # collection is out of scope exactly as the bare-target form is
        assert not one("def f(d):\n"
                       "    for b, nm in zip(tool_bodies, tool_names):\n"
                       "        d[_common.native_identity(b)] = nm\n")
        assert not one("def f(d):\n"
                       "    for c, nm in zip(sketch.sketchCurves, names):\n"
                       "        d[c.entityToken] = nm\n")

    def test_a_parameter_shadows_an_enclosing_walk_of_the_same_name(self):
        # A def NESTED in a frame that already walked bodies inherits that frame's `walked` set -
        # that inheritance is the whole point, since a nested helper keying a body the enclosing
        # function walked IS the defect. Its own PARAMETER named `b` is a different entity, so the
        # parameter subtraction is what keeps it out. Removing the subtraction on `walked` reports
        # [('inner', 5)]. A SIBLING def inherits nothing (each frame rebinds `walked`), so only the
        # nested shape exercises the guard at all.
        src = ("def outer(d):\n"
               "    for b in comp.bRepBodies:\n"
               "        d[b.name] = 1\n"
               "    def inner(b, d):\n"
               "        d[safe(lambda b=b: b.entityToken)] = 1\n")
        assert _offenders(ast.parse(src)) == []

        # The token LOCALS a frame collected shadow the same way, and separately. Here `outer` keys a
        # token read off the body its loop left bound (the defect, reported), while `inner`'s `tok`
        # is a PARAMETER holding whatever its caller passed - a key the function did not build, so
        # not this rule's. Shipped this reports [('outer', 5)]; removing the subtraction on `tokens`
        # alone reports [('outer', 5), ('inner', 7)] - the lineno is the offending SUBSCRIPT's, not
        # the enclosing def's.
        # Both statements are DIRECT children of `outer`: a frame's `walked`/`tokens` are what it
        # hands its own children, and a nested block's additions do not travel back up to it.
        src = ("def outer(d):\n"
               "    for b in comp.bRepBodies:\n"
               "        d[b.name] = 1\n"
               "    tok = safe(lambda b=b: b.entityToken)\n"
               "    d[tok] = 1\n"
               "    def inner(tok, d):\n"
               "        d[tok] = 1\n")
        assert _offenders(ast.parse(src)) == [("outer", 5)]
