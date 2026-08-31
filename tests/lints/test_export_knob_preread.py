# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: an export-options knob is written through ``_export.applied_pair``, never a bare
set-then-read-back.

``setattr(opts, prop, val)`` followed by ``getattr(opts, prop) == val`` is not a verification
whenever the requested value can already be sitting there, and on an export-options object it
always can. MEASURED live on three separate properties, each colliding on the MOST LIKELY request:

  * ``STLExportOptions.unitType`` reads 0 unset, and ``MillimeterDistanceUnits`` IS 0 - so the
    property reads 0 both when untouched (which writes INCHES) and when explicitly mm (which writes
    MM): one read, two different files;
  * ``meshRefinement`` reads 1 unset, and ``MeshRefinementMedium`` IS 1 - and medium is the default;
  * ``isBinaryFormat`` reads True unset, and True is the common request.

``_export.applied_pair`` reads the property BEFORE the set as well, so equality is evidence only
where the value was not already there, and it answers (landed value, changed).
``test_helper_duplication.py`` keys on that symbol's NAME, which stops a REDEFINITION and cannot see
a third tool writing its own three-line version. This lint keys on the SHAPE instead.

What the lint forces is the PRE-READ, and nothing beyond it. What a caller may CONCLUDE from the
pre-read is a per-property judgment backed by that property's own measurement - meshRefinement's and
isBinaryFormat's read values are measured to DETERMINE the written file (untouched and
explicit-medium are byte-identical; untouched and explicit-True are byte-identical), unitType's is
measured NOT to. ``applied_pair`` carries that asymmetry and a DO-NOT-HARMONISE note; this lint
never reads a property name, so it cannot push the three toward consistency.

The shape, in one scope (a module, or one function - lambdas are transparent, since the house idiom
writes ``safe(lambda: setattr(opts, prop, val))``):

1. a WRITE of one property on an export-options object - ``setattr(o, p, v)`` or ``o.p = v``;
2. a READ-BACK of that same property afterwards, CONSULTED - it reaches a comparison, or the test of
   an ``if``/``while``/conditional expression - directly or through a local assigned that read;
3. and NO read of that property before the write, anywhere in the scope.

An export-options object is recognized two ways: by the naming convention the Fusion API and this
repo share (a name ending in ``opts``/``options``, which covers ``opts``, ``stl_opts``,
``export_options``), and by assignment from a ``create*Options(...)`` factory call in the same
scope. Reading the object back and PUBLISHING what it holds is untouched: that value is the object's,
whoever put it there, and stating it is honest. Only consulting it as evidence needs the pre-read.

The scope is what keeps this at zero false positives over the corpus, and it is deliberate. A
set-then-read-back on something that is NOT an export-options object is a different and correct
thing: ``_common.set_verified`` reads a FeatureInput property back because a SWIG proxy ACCEPTS an
assignment to a name it does not define, so the read-back is what catches a misspelled property -
and every other consumer of that idiom (a rename, a visibility bulb, a CAM field) is answering a
question about the property's value rather than about its own assignment.

It has one known FALSE-POSITIVE direction: every scope is judged on its OWN body, so a knob writer
NESTED inside the function that took the pre-read is REPORTED, not excused - the enclosing frame's
pre-read is not read from the nested one. That site is sound and needs an ``_ALLOWLIST`` entry naming
where its pre-read lives. Widening to the enclosing chain is not attempted: a pre-read one frame up
is only evidence when the nested writer is reached exactly once per pre-read, which no rule reading
syntax can establish, and crediting it wrongly turns the lint silent on the shape it exists for.

What it still does NOT see, and what stays a reader's judgment - every entry below is a MISS, not a
report:

  * an export-options object named something this convention does not reach (``eo``, ``so``) and not
    assigned from a ``create*Options`` call in the same scope;
  * a read-back consulted somewhere other than a comparison or a branch test - published into a
    payload, returned, or passed to a helper that compares it;
  * a discarded pre-read that reads the property and drops the value, which satisfies the rule
    without informing anything;
  * a write and a read-back split across two scopes - a tool's own two-line set/read helper called
    from the scope that compares;
  * an options object reached through an attribute rather than a bare name (``ctx.opts.unitType =
    v``), and a write spelled ``opts.__setattr__(prop, v)`` rather than ``setattr``.

It catches the shapes that have been written, not every shape that could be - each round only closes
the spellings someone thought of.
"""

import ast
import os

import _corpus
from conftest import TOOLS_DIR


# (module basename without .py, enclosing function) -> why this site is exempt. A reason must state
# a fact about THIS site. Kept honest by test_allowlist_entries_still_trip; the table only shrinks.
_ALLOWLIST = {}

# A name ending in one of these is an export-options object, by the convention the Fusion API and
# this repo share: opts, stl_opts, options, export_options, exportOptions.
_OPTIONS_SUFFIXES = ("opts", "options")


def _call_name(node):
    """The called name, whether written bare or through a module: `setattr(...)` and
    `builtins.setattr(...)` both answer 'setattr'."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else None


def _pos(node):
    """(line, column) - the source order two nodes in one scope are compared in."""
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _scope_nodes(scope):
    """Every node under `scope` EXCEPT the bodies of nested def/class statements, which are their own
    scopes. A Lambda is deliberately transparent: the house idiom wraps every options write and read
    in one (`safe(lambda: setattr(opts, prop, val))`), so a lambda boundary is not a scope boundary
    for this rule."""
    out = []

    def rec(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            out.append(child)
            rec(child)
    rec(scope)
    return out


def _scopes(tree):
    """(name, node) for the module and every function in it, each judged on its OWN body."""
    yield "<module>", tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _prop_key(node):
    """The comparable identity of a property NAME as written - a string literal or the variable
    holding it. `opts.unitType`, `setattr(opts, "unitType", v)` and `getattr(opts, "unitType")` all
    answer the same key, and so do two sites naming the same `prop` variable."""
    return ast.dump(node)


def _writes(nodes):
    """[(object name, property key, statement node)] for every property write in the scope, in both
    spellings: setattr(o, p, v) and o.p = v. The VALUE written is deliberately not read: requiring
    the read-back to be compared against the same expression would answer a differently-spelled
    comparison of the same knob with silence, and the pre-read is owed either way."""
    out = []
    for node in nodes:
        if (_call_name(node) == "setattr" and len(node.args) == 3
                and isinstance(node.args[0], ast.Name)):
            out.append((node.args[0].id, _prop_key(node.args[1]), node))
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                out.append((target.value.id, _prop_key(ast.Constant(value=target.attr)), node))
    return out


def _reads_of(node, obj, prop):
    """Every READ of obj.prop in this subtree, in both spellings - getattr(o, p) and o.p. A Store
    target (`o.p = v`) is not a read, so the write itself never counts as its own pre-read."""
    hits = []
    for child in ast.walk(node):
        if (_call_name(child) == "getattr" and len(child.args) >= 2
                and isinstance(child.args[0], ast.Name) and child.args[0].id == obj
                and _prop_key(child.args[1]) == prop):
            hits.append(child)
        if (isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load)
                and isinstance(child.value, ast.Name) and child.value.id == obj
                and _prop_key(ast.Constant(value=child.attr)) == prop):
            hits.append(child)
    return hits


def _options_objects(nodes):
    """The names in this scope assigned from a create*Options(...) factory - the export-options
    object a site named something the suffix convention does not reach."""
    made = set()
    for node in nodes:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            name = _call_name(node.value)
            if (isinstance(target, ast.Name) and name
                    and name.startswith("create") and name.endswith("Options")):
                made.add(target.id)
    return made


def _is_export_options(obj, made):
    return obj in made or any(obj.lower().endswith(s) for s in _OPTIONS_SUFFIXES)


def _consulting_contexts(nodes):
    """The expressions in this scope where a value is CONSULTED as evidence: every comparison, and
    the test of every if/while/conditional expression. A read-back that only reaches a payload key or
    a return value is not here - publishing what the object holds is honest whoever put it there."""
    out = []
    for node in nodes:
        if isinstance(node, ast.Compare):
            out.append(node)
        elif isinstance(node, (ast.If, ast.IfExp, ast.While)):
            out.append(node.test)
    return out


def _offenders(tree):
    """[(enclosing function name, lineno)] for every export-options knob written and read back with
    no pre-read. The function NAME is what an allowlist entry addresses - a line number moves with
    any edit above it."""
    found = []
    for fname, scope in _scopes(tree):
        nodes = _scope_nodes(scope)
        made = _options_objects(nodes)
        contexts = _consulting_contexts(nodes)
        for obj, prop, stmt in _writes(nodes):
            if not _is_export_options(obj, made):
                continue
            where = _pos(stmt)
            if any(_pos(read) < where for node in nodes for read in _reads_of(node, obj, prop)):
                continue                     # the pre-read is there: equality can bite
            # locals that HOLD the read-back, so `landed = getattr(opts, p)` then `if landed != v`
            # reads as one consulted read-back rather than two unrelated statements
            aliases = set()
            for node in nodes:
                if (isinstance(node, ast.Assign) and _pos(node) > where
                        and _reads_of(node.value, obj, prop)):
                    aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            for ctx in contexts:
                if _pos(ctx) < where:
                    continue
                consulted = bool(_reads_of(ctx, obj, prop)) or any(
                    isinstance(n, ast.Name) and n.id in aliases for n in ast.walk(ctx))
                if consulted:
                    found.append((fname, stmt.lineno))
                    break
    return found


def _tool_modules():
    return [fn for fn in sorted(os.listdir(TOOLS_DIR)) if fn.endswith(".py")]


class TestExportKnobsPreRead:
    def test_no_tool_module_verifies_an_export_knob_without_a_pre_read(self):
        offenders = []
        for fn in _tool_modules():
            for fname, lineno in _offenders(_corpus.tree(os.path.join(TOOLS_DIR, fn))):
                if (fn[:-3], fname) in _ALLOWLIST:
                    continue
                offenders.append(f"{fn}:{lineno} (in {fname})")
        assert not offenders, (
            "an export-options property can ALREADY read the value being requested - measured, "
            "unitType reads 0 unset and MillimeterDistanceUnits IS 0, meshRefinement reads 1 unset "
            "and MeshRefinementMedium IS 1, isBinaryFormat reads True unset - so reading it back "
            "after the set answers the same whether the assignment took or was dropped, and the "
            "collision lands on the commonest request every time. Write the knob through "
            "_export.applied_pair, which reads the property BEFORE the set as well and answers "
            "(landed value, changed); what that 'changed' is worth is the knob's own measured "
            "question, and applied_pair's note carries the three answers on file:\n  "
            + "\n  ".join(offenders))

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
                stale.append(f"({mod_name}, {fname}): no longer writes an export knob without a "
                             "pre-read - remove the entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)

    def test_the_lint_bites(self):
        def one(src):
            return _offenders(ast.parse(src))

        # the shape both export tools carry when the knob writer is hand-rolled, in the two
        # spellings it is written in - the house safe(lambda:) wrapping, and a bare assignment
        assert one("def f(opts, prop, want):\n"
                   "    safe(lambda: setattr(opts, prop, want))\n"
                   "    if safe(lambda: getattr(opts, prop)) == want:\n        return want\n")
        assert one("def f(opts, val, key):\n"
                   "    safe(lambda: setattr(opts, 'meshRefinement', val))\n"
                   "    return key if safe(lambda: opts.meshRefinement) == val else None\n")
        assert one("def f(opts, val):\n"
                   "    opts.unitType = val\n"
                   "    return opts.unitType == val\n")
        # the enclosing function is what an allowlist entry addresses
        assert one("def knob(opts, prop, want):\n"
                   "    safe(lambda: setattr(opts, prop, want))\n"
                   "    if safe(lambda: getattr(opts, prop)) == want:\n"
                   "        return True\n")[0][0] == "knob"

    def test_the_pre_read_is_what_clears_it(self):
        def one(src):
            return _offenders(ast.parse(src))

        # applied_pair's own shape - the pre-read makes equality evidence, so it stays clean
        assert not one("def applied_pair(opts, prop, val, key):\n"
                       "    before = safe(lambda: getattr(opts, prop))\n"
                       "    safe(lambda: setattr(opts, prop, val))\n"
                       "    if safe(lambda: getattr(opts, prop)) != val:\n"
                       "        return NOT_APPLIED\n"
                       "    return key, before != val\n")
        # the same site with the pre-read removed IS the defect
        assert one("def applied_pair(opts, prop, val, key):\n"
                   "    safe(lambda: setattr(opts, prop, val))\n"
                   "    if safe(lambda: getattr(opts, prop)) != val:\n"
                   "        return NOT_APPLIED\n"
                   "    return key, True\n")
        # a pre-read in the OTHER spelling clears it too
        assert not one("def f(opts, val):\n"
                       "    before = opts.unitType\n"
                       "    safe(lambda: setattr(opts, 'unitType', val))\n"
                       "    return opts.unitType == val and before != val\n")
        # the write is not its own pre-read: `opts.p = v` is a Store, never a read
        assert one("def f(opts, val):\n"
                   "    opts.unitType = val\n"
                   "    if opts.unitType == val:\n        return True\n")

    def test_a_read_back_only_counts_when_it_is_consulted(self):
        def one(src):
            return _offenders(ast.parse(src))

        # PUBLISHING what the options object holds is honest - the value is the object's, whoever
        # put it there - so a read-back that only reaches a payload key is not the defect. Both
        # cases below carry an unrelated branch, so it is the read-back that is being read, not the
        # mere presence of a comparison in the scope.
        assert not one("def f(opts, prop, want, applied, name):\n"
                       "    safe(lambda: setattr(opts, prop, want))\n"
                       "    applied[name] = safe(lambda: getattr(opts, prop))\n"
                       "    if not em.execute(opts):\n        return 'wrote nothing'\n")
        # a write with no read-back at all is out of scope (_write_dxf's three content flags)
        assert not one("def f(opts, want):\n"
                       "    safe(lambda: setattr(opts, 'isPointsExported', bool(want)))\n"
                       "    did = em.execute(opts)\n"
                       "    if did != True:\n        return 'wrote nothing'\n")
        # ...and the SAME write consulted in a branch test is in scope, including the boolean
        # knob's truthiness spelling, which carries no comparison at all
        assert one("def f(opts):\n"
                   "    safe(lambda: setattr(opts, 'isBinaryFormat', True))\n"
                   "    if safe(lambda: getattr(opts, 'isBinaryFormat')):\n        return 'binary'\n")
        # a read-back held in a local and compared there is the same consulted read-back
        assert one("def f(opts, prop, want):\n"
                   "    safe(lambda: setattr(opts, prop, want))\n"
                   "    landed = safe(lambda: getattr(opts, prop))\n"
                   "    if landed != want:\n        return None\n")

    def test_the_export_options_scope_holds(self):
        def one(src):
            return _offenders(ast.parse(src))

        # _common.set_verified is a DIFFERENT and correct use: a SWIG proxy accepts an assignment to
        # a name it does not define, so the read-back catches a misspelled FeatureInput property.
        # Its object is not an export-options object and it is not flagged.
        assert not one("def set_verified(obj, prop, value, label, owner_name):\n"
                       "    setattr(obj, prop, value)\n"
                       "    if safe(lambda: getattr(obj, prop)) != value:\n"
                       "        return 'did not take'\n"
                       "    return ''\n")
        # nor is a rename read-back, nor a visibility bulb put back and checked
        assert not one("def f(ann, want):\n"
                       "    ann.name = want\n"
                       "    if safe(lambda: ann.name) != want:\n        return 'declined'\n")
        assert not one("def restore(o):\n"
                       "    safe(lambda o=o: setattr(o, 'isLightBulbOn', True))\n"
                       "    if safe(lambda o=o: o.isLightBulbOn) is not True:\n        return o\n")
        # the suffix convention reaches the names the repo writes an options object under...
        for name in ("opts", "options", "stl_opts", "export_options", "exportOptions"):
            assert one(f"def f({name}, prop, want):\n"
                       f"    safe(lambda: setattr({name}, prop, want))\n"
                       f"    if safe(lambda: getattr({name}, prop)) == want:\n"
                       "        return True\n"), name
        # ...and a create*Options factory names the object whatever it likes
        assert one("def f(em, geom, path, want):\n"
                   "    eo = em.createSTLExportOptions(geom, path)\n"
                   "    safe(lambda: setattr(eo, 'unitType', want))\n"
                   "    if safe(lambda: getattr(eo, 'unitType')) == want:\n"
                   "        return True\n")

    def test_each_scope_is_judged_on_its_own_body(self):
        # A knob writer nested inside the function holding the options object is judged on its OWN
        # body: the write and the read-back are both in the nested function, so that is where the
        # pre-read is looked for. This is the lint's one false-positive direction - a nested writer
        # whose pre-read sits one frame up is REPORTED, and takes an _ALLOWLIST entry.
        src = ("def outer(opts, prop, want, applied, name):\n"
               "    def knob():\n"
               "        safe(lambda: setattr(opts, prop, want))\n"
               "        if safe(lambda: getattr(opts, prop)) == want:\n"
               "            applied[name] = want\n"
               "    knob()\n")
        assert [f for f, _ in _offenders(ast.parse(src))] == ["knob"]
