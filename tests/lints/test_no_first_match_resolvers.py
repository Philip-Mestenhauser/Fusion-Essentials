"""Lint: the substring-first-match smell is banned across EVERY tool module, not just the ones
already fixed (see ``test_occurrence_ref_lint.py`` for the resolver this smell should route through
instead).

An occurrence's ``name`` is only LOCALLY unique - two different sub-assemblies can each contain an
occurrence named "Bolt:1". A tool that resolves a single target by "does the search term
(lowercased) appear inside this candidate's .name (lowercased)" silently returns whichever candidate
happened to come first, instead of refusing the ambiguity. ``test_occurrence_ref_lint.py`` polices a
FROZEN list of tools already migrated to the shared resolver; this test widens the same smell check
to every module under ``tools/`` so a NEW tool can't reintroduce it.

A module with a genuine reason to do a substring/lower() name match (a multi-match read, or matching
something that is not an occurrence) is named in ``_ALLOWLIST`` with a plain-English reason.

The second check is a SHAPE no line regex can see: a loop over candidates that RETURNS the first one
whose name matches EXACTLY, or a `[0]` taken off every candidate a name COLLECTED - a comprehension
filtered on the name, or a call to one of the shared collect-by-name helpers (``find_*_by_name``,
``*_named``). Each reads as legitimate line by line - the comparison is an exact match, the helper
is the sanctioned one - and each is first-match all the same. They are matched over the AST, and the
exemptions live in ``_FIRST_MATCH_ALLOWLIST``, keyed per FUNCTION because one module can hold both a
name space that is unique and one that is not.
"""

import ast
import operator
import os
import re

from conftest import TOOLS_DIR

# WHAT THIS CATCHES (and deliberately does NOT). The wrong-instance risk is a single-target resolver
# that returns the FIRST candidate whose NAME loosely matches - fine when names are unique, WRONG when
# they are not (two sub-assemblies each holding a "Bolt:1"). A regex cannot know whether a name space
# is unique, so it catches only the shapes that are a smell REGARDLESS of uniqueness: SUBSTRING
# containment and INDEXED-first-of-many. It intentionally does NOT flag a bare `x.lower() == want`
# exact match - that is the CORRECT shape for a scope-unique name (find_setup/find_operation), and
# flagging it would shame the canonical resolver into an allowlist. The "refuse ambiguity vs
# first-match" judgment for the exact-match case lives in CLAUDE.md, not here.

# Substring containment against a name-ish operand, either direction:
#   `want.lower() in nm.lower()`  (operand is a local `nm`, not just `x.name`)
#   `x.lower() in cand.name`
_SUBSTRING_NAME = re.compile(r"\.lower\(\)\s*in\s+.*(?:\.name|\.lower\(\)|\bnm\b|\bname\b)", re.I)

# The REVERSED shape: an if/elif whose leading test is `<term> in <name-expr>.lower()` - a single-target
# resolver returning the FIRST substring hit. Anchored to if/elif with an IDENTIFIER operand so it does
# not fire on a literal membership check (`if "http" in x.lower()`) or a multi-match list-comprehension
# collector (which does not start with if/elif).
_SUBSTRING_NAME_REV = re.compile(r"^\s*(?:el)?if\s+\w+\s+in\s+.*\.lower\(\)")

# `.find(...)` used as a containment test against a name (`nm.find(want) >= 0` / `!= -1`) - the same
# substring smell wearing str.find instead of `in`.
_FIND_NAME = re.compile(r"\.find\([^)]*\)\s*(?:>=\s*0|!=\s*-?1|>\s*-1)", re.I)

# First-of-a-name-comprehension: `[c for c in ... if <term> in c.name...][0]` / `next(... )` first-hit -
# collects every loose match then silently takes ONE. The `[0]`/next on a name-filtered comprehension
# is the tell.
_FIRST_OF_COMPREHENSION = re.compile(
    r"\[[^\]]*\bfor\b[^\]]*\b(?:name|nm)\b[^\]]*\]\s*\[\s*0\s*\]", re.I)


def _smells(line):
    return bool(_SUBSTRING_NAME.search(line)
                or _SUBSTRING_NAME_REV.search(line)
                or _FIND_NAME.search(line)
                or _FIRST_OF_COMPREHENSION.search(line))


# ── the exact-name FIRST-MATCH PICK ───────────────────────────────────────────
#
# `for c in walk: if c.name == want: return c` is first-match wearing an exact comparison: the
# candidates are walked, ONE of however many carry the name is returned, and nothing says another
# did. Line by line it looks like the correct scope-unique shape above, so it is matched over the
# AST instead. Four spellings of the one shape are matched, because each is a keystroke from the
# others and a check that saw only the first would be turned off by a refactor of the code it flags:
#
#   1. a loop that RETURNS ITS OWN CANDIDATE VARIABLE from an `==` test on that candidate's `.name`
#      - `for c in ...`, `for i, c in enumerate(...)` (a TUPLE target binds candidates too), and
#      `while (c := ...)` alike;
#   2. the same loop written inside-out: `if c.name != want: continue` above a `return c`;
#   3. `next((c for c in ... if c.name == want), None)`;
#   4. EVERY candidate carrying the name, collected and then SUBSCRIPTED with [0] - on one line or
#      across two - unless the code first asked a `len(...)` question that SEPARATES ONE FROM MANY,
#      in the same function, about the binding that actually reaches the subscript. The collection
#      is either a name-filtered comprehension, or a CALL to one of the shared collect-by-name
#      helpers (`find_*_by_name`, `*_named`, `*_named_in`): the helper's whole contract is that it
#      returns a LIST because the name space is not unique, so `matches = find_x_by_name(...)` then
#      `matches[0]` is the comprehension form with the filtering moved behind a call.
#
# `nm = c.name` hoisted above the comparison counts in all four - the read moved, the shape did not.
#
# KNOWN BLIND SPOTS, deliberate - three shapes this check cannot see, so a reviewer has to:
#   1. A name read through a CALL in the LOOP form (`if comp_label(c) == want:`). Whether a helper
#      reads `.name` is not decidable here, and guessing from the call's name would fire on any
#      predicate.
#   2. Asking the discriminating question and IGNORING the answer (`if len(hits) > 1: log(...)`
#      then `return hits[0]`). The `len()` question is asked, so the pass is earned by the letter
#      of the rule while the answer changes nothing. Separating "asked and acted" from "asked and
#      ignored" needs dataflow, not an AST shape.
#   3. A collect-by-name call whose NAME does not say what it collects (`hits = candidates(d, want)`
#      then `hits[0]`), a list arriving as a PARAMETER (`def pick(matches): return matches[0]`), or
#      one passed through another variable before the subscript. Spelling 4 is anchored to the
#      collect-by-name VOCABULARY because that is the only thing decidable from a call site - the
#      same reason blind spot 1 exists - and a rule that flagged every `call(...)[0]` would fire on
#      104 sites in this tree, nearly all of them coordinate, tuple and split unpackings.
# None of the three is the natural way to write a resolver, which is why they are documented rather
# than chased - unlike the shapes this check DOES catch, where the most obvious spelling slips
# straight past a weaker rule.
#
# The sanctioned exact-match resolvers are NOT this shape and are not flagged: _cam_common's
# find_operation COLLECTS every match (`[n for n in nodes if n.name.lower() == want]`) and then
# asks `len(matches)`, which is what lets it refuse a duplicate. _export.find_component collects the
# same way. Collect-then-COUNT is both the way out of this check and the way to refuse; collecting
# every match and then taking `[0]` off a truthiness test still returns one of several, which is why
# the `len(...)` question - not the collection - is what earns the pass. And only a question that
# answers differently for one and for two: `len(hits) > 0` is `if hits:` wearing a len(), so it
# earns nothing. The question must also be THIS function's, about the binding that reaches the
# subscript - a `len(hits)` in a neighbouring function, a nested closure, or before a rebinding of
# the same name is a different question about a different list, and `hits`/`matches`/`found` are
# common enough that a looser rule gets satisfied by accident rather than by intent.


def _reads_a_name(node):
    return any(isinstance(n, ast.Attribute) and n.attr in ("name", "fullPathName")
               for n in ast.walk(node))


def _name_locals(loop):
    """Loop-body variables assigned FROM a `.name` read - `nm = p.name` hoists the read out of the
    comparison below it, and a comparison against `nm` is then the same comparison."""
    out = set()
    for n in ast.walk(loop):
        if isinstance(n, ast.Assign) and n.value is not None and _reads_a_name(n.value):
            out.update(t.id for t in n.targets if isinstance(t, ast.Name))
    return out


def _exact_name_test(test, name_locals=(), op=ast.Eq):
    """True when `test` compares something that came from a candidate's name with `op` (`==`, or
    `!=` for the skip-the-others spelling of the same loop)."""
    for n in ast.walk(test):
        if not (isinstance(n, ast.Compare) and any(isinstance(o, op) for o in n.ops)):
            continue
        if _reads_a_name(n):
            return True
        if any(isinstance(x, ast.Name) and x.id in name_locals for x in ast.walk(n)):
            return True
    return False


def _candidate_vars(loop):
    """The variables `loop` binds to ONE candidate per pass - what a first-match pick returns.

    A `for` target is a Name, or a TUPLE holding one (`for i, c in enumerate(walk)`): a check that
    read only the Name form would be switched off by adding an index to the very loop it flags. A
    `while` binds its candidate with a walrus in the test."""
    if isinstance(loop, ast.For):
        target = loop.target
        elts = target.elts if isinstance(target, ast.Tuple) else [target]
        return {e.id for e in elts if isinstance(e, ast.Name)}
    return {n.target.id for n in ast.walk(loop.test)
            if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name)}


def _returns_the_candidate(body, var):
    """True when `body` returns the loop variable ITSELF (bare, or as a tuple element) - returning
    `c.name` or a count is a read ABOUT the candidates, not a pick of one."""
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Return) and n.value is not None:
                vals = n.value.elts if isinstance(n.value, ast.Tuple) else [n.value]
                if any(isinstance(v, ast.Name) and v.id == var for v in vals):
                    return True
    return False


def _skips_the_others(loop, name_locals, cvars):
    """True when the loop `continue`s past every candidate whose name does NOT match and returns a
    candidate below that - `if c.name != want: continue` ... `return c`, which is the loop above
    written inside-out."""
    for st in ast.walk(loop):
        if not (isinstance(st, ast.If) and _exact_name_test(st.test, name_locals, op=ast.NotEq)):
            continue
        if not any(isinstance(n, ast.Continue) for s in st.body for n in ast.walk(s)):
            continue
        if any(_returns_the_candidate(loop.body, v) for v in cvars):
            return True
    return False


def _is_name_filtered(node):
    """True for `[c for c in ... if c.name == want]` - a collection filtered on a candidate's name."""
    return isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)) and any(
        _exact_name_test(cond) for gen in node.generators for cond in gen.ifs)


# The shared collect-by-name helpers' spelling: `find_joints_by_name`, `find_joint_origins_by_name`,
# `_collect_bodies_by_name`, `_files_in_folder_by_name`, `_planes_named_in`, `_presets_named`. Each
# returns a LIST precisely because the name space is not unique, so its `[0]` is the same pick the
# comprehension form makes - the filtering just moved behind the call. `_named` is anchored at the
# END (or before `_in`) because a resolve-ONE whose name merely CONTAINS it - `_named_scope`, which
# hands back an (entity, error) PAIR - has a `[0]` that is a tuple element, not one of many.
_NAME_LOOKUP_CALL = re.compile(r"by_name|_named(?:_in)?$", re.I)


def _is_name_lookup_call(node):
    """True for a call to a collect-by-name helper, read off the CALLEE's identifier - the only thing
    a call site decides on its own (see blind spot 3: whether some other function filters on a name
    is not decidable here, so the check is anchored to the vocabulary the helpers are written in)."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    ident = (fn.id if isinstance(fn, ast.Name)
             else fn.attr if isinstance(fn, ast.Attribute) else "")
    return bool(_NAME_LOOKUP_CALL.search(ident))


def _collects_every_name_match(node):
    """True for a value holding EVERY candidate that carries a name - a name-filtered comprehension
    OR a collect-by-name call. A `[0]` off either returns one of however many, which is why both
    reach the same `len()`-question exemption below."""
    return _is_name_filtered(node) or _is_name_lookup_call(node)


def _own_nodes(scope):
    """Every node under `scope`'s own body, NOT descending into a nested function or class: those
    are scopes of their own, and a `len(hits)` inside one is a different question about a different
    `hits`. Walking through them is how an unrelated count comes to excuse this scope's subscript."""
    out, stack = [], list(getattr(scope, "body", []))
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(c for c in ast.iter_child_nodes(node)
                     if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    return out


def _assignments(scope):
    """{variable: [(line, did it collect every name match)]} for the plain `v = ...` bindings in
    `scope` - enough to ask WHICH binding reaches a later subscript."""
    out = {}
    for n in _own_nodes(scope):
        if (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)):
            out.setdefault(n.targets[0].id, []).append(
                (n.lineno, _collects_every_name_match(n.value)))
    return out


_COMPARISONS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def _separates_one_from_many(op, const, len_on_left):
    """True when `len(v) <op> const` ANSWERS DIFFERENTLY for one item and for two.

    This is the whole question a refusal is built on, so it is asked by evaluating the comparison
    rather than by listing operators: `== 1`, `!= 1`, `> 1`, `< 2` all separate them; `> 0` and
    `>= 1` do not - they are `if v:` spelled with len(), which is exactly the shape being caught."""
    fn = _COMPARISONS.get(type(op))
    if fn is None or not isinstance(const, int) or isinstance(const, bool):
        return False
    pair = ((fn(1, const), fn(2, const)) if len_on_left else (fn(const, 1), fn(const, 2)))
    return pair[0] != pair[1]


def _counting_lines(scope, var):
    """The lines in `scope` that ask how many `var` holds in a way that separates one from many."""
    lines = []
    for n in _own_nodes(scope):
        if not isinstance(n, ast.Compare) or len(n.ops) != 1:
            continue
        left, right, op = n.left, n.comparators[0], n.ops[0]
        for side, other, on_left in ((left, right, True), (right, left, False)):
            if (isinstance(side, ast.Call) and isinstance(side.func, ast.Name)
                    and side.func.id == "len" and side.args
                    and isinstance(side.args[0], ast.Name) and side.args[0].id == var
                    and isinstance(other, ast.Constant)
                    and _separates_one_from_many(op, other.value, on_left)):
                lines.append(n.lineno)
    return lines


def _takes_the_first(scope):
    """Each `[0]` taken off a collection of EVERY name match in ONE function - directly, or through a
    variable whose reaching binding is such a collection and which nothing counted in between.

    Scoped to the function and to the binding on purpose: a module-wide, name-keyed lookup lets any
    `len(hits)` anywhere in the file - or a `hits` rebound to something else - excuse a `hits[0]`
    that nothing actually guards, and `hits`/`matches`/`found` are everywhere."""
    assigns = _assignments(scope)
    out = []
    for n in _own_nodes(scope):
        if not (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                and n.slice.value == 0):
            continue
        val = n.value
        if _collects_every_name_match(val):
            out.append(n)                       # the one-line form: nothing was named to count
            continue
        if not isinstance(val, ast.Name):
            continue
        earlier = [b for b in assigns.get(val.id, []) if b[0] <= n.lineno]
        if not earlier:
            continue
        bound_at, from_a_name_match = max(earlier)
        if not from_a_name_match:
            continue                            # a later rebinding reaches here, not the collection
        if not any(bound_at < ln <= n.lineno for ln in _counting_lines(scope, val.id)):
            out.append(n)
    return out


def _first_match_loops(src):
    """[(enclosing function, line number, line)] for every exact-name first-match PICK in `src` -
    the four spellings described above."""
    tree = ast.parse(src)
    owner = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                owner.setdefault(id(n), fn.name)
    lines = src.splitlines()
    out = []

    def record(node, lineno):
        out.append((owner.get(id(node), "<module>"), lineno, lines[lineno - 1].strip()))

    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            cvars = _candidate_vars(node)
            if not cvars:
                continue
            name_locals = _name_locals(node)
            for st in ast.walk(node):
                if (isinstance(st, ast.If) and _exact_name_test(st.test, name_locals)
                        and any(_returns_the_candidate(st.body, v) for v in cvars)):
                    record(node, st.lineno)
            if _skips_the_others(node, name_locals, cvars):
                record(node, node.lineno)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "next" and node.args
                and isinstance(node.args[0], ast.GeneratorExp)):
            gen = node.args[0]
            if any(_exact_name_test(cond) for g in gen.generators for cond in g.ifs):
                record(node, node.lineno)
    # Per FUNCTION, never per module: which binding reaches a subscript, and whether anything
    # counted it, are questions about one function's body. A module-wide sweep answers them with
    # some other function's `len(hits)`.
    scopes = [fn for fn in ast.walk(tree)
              if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes + [tree]:      # every function on its own, then the module's own body
        for node in _takes_the_first(scope):
            record(node, node.lineno)
    return sorted(set(out), key=lambda r: r[1])


# module.function -> why THIS resolver's name space makes an exact first match correct. Each reason
# must describe the code's own behavior, not point at a review or work item.
_FIRST_MATCH_ALLOWLIST = {
    # A design's parameter names are the identifiers its expressions reference, so two parameters
    # cannot carry one name. The loop widens the search to allParameters (model parameters, not just
    # user ones) after the API's own userParameters.itemByName misses; both name spaces are the
    # same one an expression resolves against.
    "param_ops._find_parameter":
        "parameter names are the identifiers expressions reference - unique by construction",
    # These two search the CLOUD data model. The FOLDER name space is measured unique; the PROJECT
    # one is not established, and that resolver returns the first sibling carrying the name.
    "_data_common._find_project":
        "one hub's projects, matched case-insensitively; whether a hub can hold two projects of "
        "one name is not established here",
    "_data_common._child_folder_by_name":
        "one folder's immediate subfolders, a name space measured UNIQUE: adding a subfolder under "
        "a name a sibling already carries raises '3 : CB_NAE - Another object with the same name "
        "already exists in this container', so a folder cannot hold two of one name and the first "
        "match is the only match",
}


# module (no .py) -> why this one is allowed to keep the pattern. Each reason must describe the
# module's own behavior, not point at a review or work item.
_ALLOWLIST = {}


def _all_tool_files():
    return [fn for fn in sorted(os.listdir(TOOLS_DIR))
            if fn.endswith(".py") and fn != "__init__.py"]


class TestNoFirstMatchResolverAnywhere:
    def test_no_tool_hand_rolls_a_substring_name_match(self):
        offenders = []
        for fn in _all_tool_files():
            mod_name = fn[:-3]
            if mod_name in _ALLOWLIST:
                continue
            src = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read()
            for i, line in enumerate(src.splitlines(), 1):
                if _smells(line):
                    offenders.append(f"{fn}:{i}: {line.strip()}")
        assert not offenders, (
            "these lines resolve a single target by a lower()-cased substring match against .name - "
            "the wrong-instance risk _inputs._resolve_occurrence (or the OccurrenceRef/"
            "OccurrenceRefList kind) exists to refuse instead of guessing. Route through the shared "
            "resolver, or add a plain-English allowlist entry naming why this one is different:\n"
            + "\n".join(offenders)
        )

    def test_allowlist_entries_still_exist_and_still_trip_the_smell(self):
        # An allowlist entry that no longer matches anything (the code moved on) is dead weight that
        # hides a regression check; keep the list honest by requiring every entry to still be real.
        stale = []
        for mod_name, reason in _ALLOWLIST.items():
            assert reason.strip(), f"{mod_name} allowlist entry needs a plain-English reason"
            path = os.path.join(TOOLS_DIR, mod_name + ".py")
            if not os.path.exists(path):
                stale.append(f"{mod_name}: no such module")
                continue
            src = open(path, encoding="utf-8").read()
            if not any(_smells(line) for line in src.splitlines()):
                stale.append(f"{mod_name}: no longer matches the smell - remove the allowlist entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)

    def test_reversed_pattern_bites(self):
        # prove the reversed-form regex catches the shape it targets, and skips the safe look-alikes.
        assert _smells('    if want in occ.name.lower():')
        assert _smells('        elif term in nm.lower():')
        assert not _smells('    if "http" in url.lower():')          # literal operand, not a name
        assert not _smells('    near = [n for n in names if w in n.lower()]')  # collector, not if-led
        assert not _smells('    if occ_err and "x" in occ_err.lower():')       # membership + boolean, not <id> in

    def test_containment_and_indexed_shapes_bite(self):
        # Substring containment and indexed-first-of-many are a smell regardless of how the operand
        # is named - a local `nm`, a `.find()` guard, or a `[0]` off a name-filtered comprehension.
        assert _smells('        elif contains is None and want and want.lower() in nm.lower():')
        assert _smells('        if nm.find(want) >= 0:')                    # str.find containment
        assert _smells('        if nm.find(want) != -1:')                   # str.find, other guard form
        assert _smells('    op = [o for o in ops if term in o.name][0]')    # first-of-name-comprehension

    def test_no_tool_returns_the_first_exact_name_hit_from_a_loop(self):
        offenders = []
        for fn in _all_tool_files():
            mod_name = fn[:-3]
            src = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read()
            for func, lineno, line in _first_match_loops(src):
                if f"{mod_name}.{func}" in _FIRST_MATCH_ALLOWLIST:
                    continue
                offenders.append(f"{fn}:{lineno} (in {func}): {line}")
        assert not offenders, (
            "these return the FIRST candidate whose name matches exactly - a pick of one of however "
            "many carry that name, with nothing said about the rest. Collect every match and ASK "
            "HOW MANY: len(matches) == 1 resolves, more REFUSES naming the candidates (_export."
            "find_component / _cam_common.find_operation / _joints.find_joint). Collecting alone is "
            "not the way out - a [0] off a name-filtered comprehension, or off a collect-by-name "
            "helper's list, is this same pick. Or add a '<module>.<function>' entry to "
            "_FIRST_MATCH_ALLOWLIST saying why that name space cannot hold two:\n"
            + "\n".join(offenders)
        )

    def test_first_match_allowlist_entries_still_exist_and_still_trip(self):
        stale = []
        for key, reason in _FIRST_MATCH_ALLOWLIST.items():
            assert reason.strip(), f"{key} allowlist entry needs a plain-English reason"
            mod_name, _, func = key.partition(".")
            path = os.path.join(TOOLS_DIR, mod_name + ".py")
            if not os.path.exists(path):
                stale.append(f"{key}: no such module")
                continue
            src = open(path, encoding="utf-8").read()
            if not any(f == func for f, _ln, _line in _first_match_loops(src)):
                stale.append(f"{key}: no longer a first-match loop - remove the allowlist entry")
        assert not stale, "stale first-match allowlist entries:\n  " + "\n  ".join(stale)

    def test_the_exact_name_loop_shape_bites(self):
        # the shape the regexes above read as legitimate: an exact comparison, returning the first
        # candidate that answers to it.
        src = ("def component_by_name(design, name):\n"
               "    for c in all_components(design):\n"
               "        if (safe(lambda c=c: c.name) or '') == name:\n"
               "            return c\n"
               "    return None\n")
        assert [f for f, _l, _t in _first_match_loops(src)] == ["component_by_name"]
        # the same loop with the name read HOISTED into a local, and returning a tuple
        hoisted = ("def find(data, name):\n"
                   "    seen = []\n"
                   "    for p in data.projects:\n"
                   "        nm = p.name\n"
                   "        if nm == name:\n"
                   "            return p, seen\n"
                   "    return None, seen\n")
        assert [f for f, _l, _t in _first_match_loops(hoisted)] == ["find"]

    def test_a_tuple_loop_target_is_still_a_candidate(self):
        # adding an index to the flagged loop is a pure refactor - it must not switch the check off
        src = ("def find(design, name):\n"
               "    for i, c in enumerate(all_components(design)):\n"
               "        if c.name == name:\n"
               "            return c\n"
               "    return None\n")
        assert [f for f, _l, _t in _first_match_loops(src)] == ["find"]

    def test_the_negated_guard_spelling_bites(self):
        # the same loop written inside-out: skip everything that does not match, return what is left
        src = ("def find(design, name):\n"
               "    for c in all_components(design):\n"
               "        if c.name != name:\n"
               "            continue\n"
               "        return c\n"
               "    return None\n")
        assert [f for f, _l, _t in _first_match_loops(src)] == ["find"]

    def test_next_over_a_genexp_bites(self):
        src = ("def find(design, name):\n"
               "    return next((c for c in all_components(design) if c.name == name), None)\n")
        assert [f for f, _l, _t in _first_match_loops(src)] == ["find"]

    def test_collect_then_take_the_first_bites_on_one_line_and_on_two(self):
        # collecting every match and then taking [0] still hands back one of several
        one = ("def find(design, name):\n"
               "    return [c for c in all_components(design) if c.name == name][0]\n")
        assert [f for f, _l, _t in _first_match_loops(one)] == ["find"]
        two = ("def find(design, name):\n"
               "    hits = [c for c in all_components(design) if c.name == name]\n"
               "    return hits[0] if hits else None\n")
        assert [f for f, _l, _t in _first_match_loops(two)] == ["find"]

    def test_a_collect_by_name_HELPER_CALL_then_the_first_bites(self):
        # the shape the collect-then-count rule has to see through: the filtering moved behind a
        # shared collect-by-name helper, so no comprehension is left at the subscript
        two = ("def frame_axes(design, frame_name):\n"
               "    matches = _joints.find_joint_origins_by_name(design, frame_name)\n"
               "    if not matches:\n"
               "        return None\n"
               "    return matches[0][0]\n")
        assert [f for f, _l, _t in _first_match_loops(two)] == ["frame_axes"]
        one = ("def pick(design, want):\n"
               "    return _joints.find_joints_by_name(design, want)[0]\n")
        assert [f for f, _l, _t in _first_match_loops(one)] == ["pick"]
        # the `*_named` / `*_named_in` spelling of the same helper, called bare
        named = ("def pick(design, want):\n"
                 "    hits = _planes_named_in(design, want)\n"
                 "    return hits[0]\n")
        assert [f for f, _l, _t in _first_match_loops(named)] == ["pick"]

    def test_a_counted_collect_by_name_call_earns_the_pass_and_a_NON_lookup_call_is_not_its_business(self):
        # the resolve-one every collect-by-name helper is built for: count, THEN take the one
        counted = ("def find_joint(design, name):\n"
                   "    hits = find_joints_by_name(design, name)\n"
                   "    if len(hits) == 1:\n"
                   "        return hits[0], None\n"
                   "    return None, refuse(hits)\n")
        assert _first_match_loops(counted) == []
        # a call that is not a by-name lookup is ordinary unpacking, not this check's business -
        # flagging it would fire on the coordinate/tuple/split sites all over the tools tree
        unpack = ("def origin(entity):\n"
                  "    xyz = world_point(entity)\n"
                  "    return xyz[0]\n")
        assert _first_match_loops(unpack) == []
        # a resolve-ONE that merely CONTAINS 'named' returns an (entity, error) PAIR, so its [0] is a
        # tuple element - _inputs._qualified_body reads _named_scope(des, spec)[0] exactly this way
        pair = ("def qualified(des, spec):\n"
                "    if _named_scope(des, spec)[0] is not None:\n"
                "        return None, None\n"
                "    return resolve(des, spec)\n")
        assert _first_match_loops(pair) == []
        # and a rebinding after the lookup reaches the subscript instead, same as the comprehension
        rebound = ("def find(design, name):\n"
                   "    hits = find_joints_by_name(design, name)\n"
                   "    hits = fallback(design)\n"
                   "    return hits[0]\n")
        assert _first_match_loops(rebound) == []

    def test_a_len_that_only_asks_NON_EMPTY_does_not_launder_the_subscript(self):
        # `len(hits) > 0` is `if hits:` spelled with len(): it does not separate one from many, so
        # it cannot be what earns the pass. Same for `>= 1`.
        for guard in ("len(hits) > 0", "len(hits) >= 1", "0 < len(hits)"):
            src = ("def find(design, name):\n"
                   "    hits = [c for c in all_components(design) if c.name == name]\n"
                   f"    if {guard}:\n"
                   "        return hits[0]\n"
                   "    return None\n")
            assert [f for f, _l, _t in _first_match_loops(src)] == ["find"], guard

    def test_every_len_question_that_DOES_separate_one_from_many_earns_the_pass(self):
        for guard in ("len(hits) == 1", "len(hits) != 1", "len(hits) > 1", "len(hits) < 2",
                      "1 == len(hits)"):
            src = ("def find(design, name):\n"
                   "    hits = [c for c in all_components(design) if c.name == name]\n"
                   f"    if {guard}:\n"
                   "        return hits[0]\n"
                   "    return None\n")
            assert _first_match_loops(src) == [], guard

    def test_another_functions_len_cannot_launder_this_ones_subscript(self):
        # keyed on the bare variable name across a whole module, any `len(hits)` in a 700-line file
        # excuses every `hits[0]` in it - `hits`/`matches`/`found` are everywhere
        src = ("def count_them(xs):\n"
               "    hits = [x for x in xs]\n"
               "    if len(hits) == 1:\n"
               "        return True\n"
               "    return False\n"
               "\n"
               "\n"
               "def find(design, name):\n"
               "    hits = [c for c in all_components(design) if c.name == name]\n"
               "    return hits[0] if hits else None\n")
        assert [f for f, _l, _t in _first_match_loops(src)] == ["find"]

    def test_a_len_of_an_EARLIER_binding_of_the_same_name_does_not_launder(self):
        # the count asked about a different list that happened to share the variable name; the
        # binding that reaches the subscript was never counted
        src = ("def find(design, name):\n"
               "    hits = [n for n in known_names(design)]\n"
               "    if len(hits) == 1:\n"
               "        report(hits)\n"
               "    hits = [c for c in all_components(design) if c.name == name]\n"
               "    return hits[0] if hits else None\n")
        assert [f for f, _l, _t in _first_match_loops(src)] == ["find"]

    def test_a_NESTED_functions_len_cannot_launder_the_outer_subscript(self):
        # a closure that counts something of its own sits INSIDE the outer function and between its
        # filter and its subscript, so only a scope walk that stops at the nested def keeps the two
        # questions apart
        src = ("def find(design, name):\n"
               "    hits = [c for c in all_components(design) if c.name == name]\n"
               "\n"
               "    def tally(xs):\n"
               "        return len(hits) == 1\n"
               "\n"
               "    return hits[0] if hits else None\n")
        assert "find" in [f for f, _l, _t in _first_match_loops(src)]

    def test_a_rebinding_after_the_filter_is_not_this_checks_business(self):
        # what reaches the subscript is the LATER binding, which is not a name-filtered collection -
        # flagging it would be a false positive on code this check says nothing about
        src = ("def find(design, name):\n"
               "    hits = [c for c in all_components(design) if c.name == name]\n"
               "    hits = fallback(design)\n"
               "    return hits[0]\n")
        assert _first_match_loops(src) == []

    def test_a_while_walrus_loop_bites(self):
        src = ("def find(comps, name):\n"
               "    i = 0\n"
               "    while (c := comps.item(i)) is not None:\n"
               "        if c.name == name:\n"
               "            return c\n"
               "        i += 1\n"
               "    return None\n")
        assert [f for f, _l, _t in _first_match_loops(src)] == ["find"]

    def test_a_name_read_through_a_helper_is_the_documented_blind_spot(self):
        # pinned so the module docstring and the code cannot drift: whether comp_label() reads a
        # name is not decidable here, so this shape passes and needs a reviewer instead
        src = ("def find(design, name):\n"
               "    for c in all_components(design):\n"
               "        if comp_label(c) == name:\n"
               "            return c\n"
               "    return None\n")
        assert _first_match_loops(src) == []

    def test_a_name_collector_that_does_not_SAY_so_is_the_documented_blind_spot(self):
        # pinned so the module docstring's blind-spot 3 and the code cannot drift: the callee's
        # identifier is the only tell a call site carries, so a collector named anything outside the
        # by_name/_named vocabulary passes, as does a list arriving as a parameter
        src = ("def find(design, name):\n"
               "    hits = candidates(design, name)\n"
               "    return hits[0]\n")
        assert _first_match_loops(src) == []
        param = ("def pick(matches):\n"
                 "    return matches[0]\n")
        assert _first_match_loops(param) == []

    def test_collect_then_decide_is_not_flagged(self):
        # find_operation's shape - every match collected, THEN one decision - is the correct
        # resolver, so it must not read as the defect. Nor may a loop that returns something ABOUT
        # the candidates rather than picking one.
        # the `[0]` here is the SAME subscript the check flags above - what earns the pass is the
        # len() question in front of it, which is what refusing a duplicate is made of
        collect = ("def find_operation(cam, name):\n"
                   "    matches = [n for n in nodes if (n.name or '').lower() == want]\n"
                   "    if len(matches) == 1:\n"
                   "        return matches[0].obj, []\n"
                   "    return None, []\n")
        assert _first_match_loops(collect) == []
        # a loop that COLLECTS every match is not a pick, whatever it does with them afterwards
        gather = ("def all_named(design, name):\n"
                  "    out = []\n"
                  "    for c in all_components(design):\n"
                  "        if c.name == name:\n"
                  "            out.append(c)\n"
                  "    return out\n")
        assert _first_match_loops(gather) == []
        about = ("def group_holding(timeline, low_name):\n"
                 "    for g in groups(timeline):\n"
                 "        for m in members(g):\n"
                 "            if m.name == low_name:\n"
                 "                return g.name\n"
                 "    return None\n")
        assert _first_match_loops(about) == []
        not_a_name = ("def key_for(value):\n"
                      "    for key in PROJECTIONS:\n"
                      "        if camera_type(key) == value:\n"
                      "            return key\n"
                      "    return None\n")
        assert _first_match_loops(not_a_name) == []

    def test_the_fixed_resolver_no_longer_trips(self):
        # _export.find_component is the site this check was written for; it must be clean now, or
        # the check is passing for the wrong reason.
        src = open(os.path.join(TOOLS_DIR, "_export.py"), encoding="utf-8").read()
        assert _first_match_loops(src) == []

    def test_correct_exact_match_is_not_flagged(self):
        # The deliberate NON-catch: an exact case-insensitive match on a SCOPE-UNIQUE name is the
        # correct resolver shape (find_setup/find_operation). Flagging it would shame the canonical
        # helper into an allowlist - the very rot that neutered this lint before. Judgment about
        # non-unique names lives in CLAUDE.md, not in this regex.
        assert not _smells('        if (nm or "").lower() == want:')        # find_setup:75 / find_operation:103
        assert not _smells('            if want and (s_name or "").lower() != want:')  # a FILTER, not a resolver
        assert not _smells('    n = value.find("x")')                       # .find without a containment guard
