"""Lint: every WRITE/DESTRUCTIVE tool declares HOW its effect is proven - postconditions=[...] for a
detachable effect, else verification=Verification(kind=...) from the closed set (item.py). Every
reference it carries RESOLVES: an evidence_test node id pytest would collect and no other tool
claims, a registered read poller, an observing receipt row, an OPEN ledger row. Gaps only shrink."""

import ast
import fnmatch
import os
import re

import pytest

from conftest import is_write_tool, load_tool, register_all_tools

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _postconditions_of(item):
    """Walk the handler wrapper chain (write-guard -> assert) to the declared postconditions."""
    h = item.handler
    seen = set()
    while h is not None and id(h) not in seen:
        seen.add(id(h))
        posts = getattr(h, "__assert_postconditions__", None)
        if posts is not None:
            return posts
        h = getattr(h, "__wrapped__", None)
    return None


# The measured count of kind="gap" declarations - a tool whose success no read-back can confirm.
# Shrink-only: closing a gap (an inline gate or a kernel kind lands) lowers it; raising it means a
# NEW tool shipped with no effective read-back, which is a deliberate decision this number makes
# visible instead of a quiet reclassification. The ceiling is an alarm that UN-RINGS itself:
# the shrink-only half of the test below forces the number back down the moment a gap closes, so
# a tool parked here while its evidence is unrecorded cannot quietly stay parked.
_GAP_CEILING = 2


def _verification_of(item):
    """The registration's verification classification, or None (mcp_primitives/item.py)."""
    return getattr(item, "verification", None)


def _gap_tools(items):
    """Every tool whose declaration says its effect cannot be verified."""
    return sorted(it.get_name() for it in items
                  if _verification_of(it) is not None and _verification_of(it).kind == "gap")


class TestPostconditionsDeclared:
    def test_the_gap_count_only_shrinks(self):
        gaps = _gap_tools(register_all_tools())
        assert len(gaps) <= _GAP_CEILING, (
            f"{len(gaps)} gap entries exceed the ceiling of {_GAP_CEILING}. A gap is a tool whose "
            "success cannot be verified at all - adding one is a deliberate decision: raise "
            "_GAP_CEILING in the same diff with the new tool's named defect, or give the tool a "
            "real read-back.\n  " + "\n  ".join(gaps))
        if len(gaps) < _GAP_CEILING:
            raise AssertionError(
                f"only {len(gaps)} gap entries remain - lower _GAP_CEILING to {len(gaps)} to "
                "lock the win in:\n  " + "\n  ".join(gaps))

    def test_every_write_tool_declares_how_its_effect_is_verified(self):
        missing = []
        for it in register_all_tools():
            if not is_write_tool(it):
                continue                      # read tools mutate nothing to verify
            if _postconditions_of(it):
                continue
            if _verification_of(it) is not None:
                continue
            missing.append(it.get_name())
        assert not missing, (
            "a write tool declares how its effect is proven: postconditions=[...] for a detachable "
            "effect, else verification=Verification(kind=...) - one of inline / effect / deferred / "
            "external / dynamic / gap, each carrying the reference this lint resolves (see the "
            "Verification kinds in item.py). These declare neither:\n  " + "\n  ".join(sorted(missing)))

    def test_declared_postconditions_are_postcondition_kinds(self):
        kernel = load_tool("_assert")
        for it in register_all_tools():
            posts = _postconditions_of(it)
            for p in posts or []:
                assert isinstance(p, kernel.Postcondition), (
                    f"{it.get_name()}: postconditions must be _assert.Postcondition kinds, got {type(p)}")


# A node id is resolved by PARSING its file with ast - neither importing the test module nor
# running pytest's collection, so a reference costs one parse and a broken one cannot take the
# lint down with it.

_DYNAMIC_TOOL = "sys_execute_script"          # the one caller-authored effect (the script hatch)
_NODE_ID = re.compile(r"^tests/[\w/]+\.py(?:::\w+){1,2}$")
_RECEIPT_REF = re.compile(r"^tests/live/[\w.]+\.md#\w+$")
_DEFECT_ID = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
# A receipt bucket that records the ABSENCE of an observation. A reference to one of these names a
# row that exists but proves nothing, which is what a gap is for (the receipt's own header classes
# a skipped row "Not verified - excused").
_EMPTY_BUCKETS = ("skipped", "pending")
# An OPEN row of the defect ledger: an unticked checkbox opening the line, then the id.
_OPEN_ROW = r"^- \[ \] {id}\b"
# The defect ledger's filename under plans/. The file is UNTRACKED (the plans tree is gitignored),
# so it is present on a working machine and absent from a clean checkout - which is why the gap-id
# check skips rather than passes when it cannot find it.
_LEDGER_NAME = "fix-backlog.md"

# pytest's COLLECTION rules, which are what make a node id spendable: python_files
# (test_*.py / *_test.py), python_classes (Test*) and python_functions (test*) are its defaults,
# and pytest.ini overrides none of them. A class carrying __init__ is skipped with a collection
# warning rather than instantiated, so its methods never run either. Existing under a
# test-shaped id is not the same as running: a fixture, a module helper, a private method, a
# plain class's method and anything at all in conftest.py all parse and none of them is a test.
_PYTEST_FILE_GLOBS = ("test_*.py", "*_test.py")
_PYTEST_CLASS_PREFIX = "Test"
_PYTEST_FUNC_PREFIX = "test"


def _is_def(node, name):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name


def _resolve_node_id(node_id):
    """'' when the node id names a test pytest would COLLECT, else why it does not.

    Resolves '<file>.py::test_x' and '<file>.py::TestClass::test_x' by PARSING the file: the class
    is looked up at module level and the test function inside it, so a renamed or deleted test is
    a miss rather than a claim that still reads well. Every part is also held to the collection
    rules above, so a symbol that exists but never runs is a miss too - the obligation a
    declaration points at has to be one someone can spend."""
    if not _NODE_ID.match(node_id):
        return "not a 'tests/<file>.py::[Class::]test_name' node id"
    parts = node_id.split("::")
    base = os.path.basename(parts[0])
    if not any(fnmatch.fnmatch(base, glob) for glob in _PYTEST_FILE_GLOBS):
        return (f"{base} is not a file pytest collects "
                f"({' / '.join(_PYTEST_FILE_GLOBS)}) - nothing in it runs as a test")
    path = os.path.join(REPO_ROOT, *parts[0].split("/"))
    if not os.path.isfile(path):
        return f"no such test file: {parts[0]}"
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    body, where = tree.body, parts[0]
    if len(parts) == 3:
        cls = next((n for n in tree.body
                    if isinstance(n, ast.ClassDef) and n.name == parts[1]), None)
        if cls is None:
            return f"{parts[0]} defines no class {parts[1]}"
        if not cls.name.startswith(_PYTEST_CLASS_PREFIX):
            return (f"{parts[0]}'s {cls.name} is not a class pytest collects "
                    f"({_PYTEST_CLASS_PREFIX}*)")
        if any(_is_def(n, "__init__") for n in cls.body):
            return (f"{parts[0]}'s {cls.name} defines __init__, so pytest skips the class and "
                    "none of its methods run")
        body, where = cls.body, f"{parts[0]}::{parts[1]}"
    fn = parts[-1]
    if not fn.startswith(_PYTEST_FUNC_PREFIX):
        return (f"{fn} is not a name pytest collects ({_PYTEST_FUNC_PREFIX}*) - a fixture or "
                "helper of that name is never run as this tool's proof")
    if not any(_is_def(n, fn) for n in body):
        return f"{where} defines no test named {fn}"
    return ""


def _resolve_receipt(ref):
    """'' when the reference names a receipt row that records an OBSERVATION, else why it does not.

    The row must be a real TABLE row - the tool named in the first cell, not merely mentioned in
    the file's prose - and its bucket cell must not be one of _EMPTY_BUCKETS: a skipped or pending
    row says the tool was not driven, so pointing a verification at it would cite the absence of
    evidence as evidence."""
    if not _RECEIPT_REF.match(ref):
        return "not a 'tests/live/<receipt>.md#<tool>' reference"
    rel, _, anchor = ref.partition("#")
    path = os.path.join(REPO_ROOT, *rel.split("/"))
    if not os.path.isfile(path):
        return f"no such receipt: {rel}"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    row = re.search(r"^\|\s*" + re.escape(anchor) + r"\s*\|([^|]*)\|", text, re.M)
    if row is None:
        return f"{rel} carries no row for '{anchor}'"
    bucket = row.group(1).strip().lower()
    if bucket.startswith(_EMPTY_BUCKETS):
        return (f"{rel}'s row for '{anchor}' reads '{row.group(1).strip()}' - it records no "
                "observation, so it is not evidence of anything")
    return ""


def _resolve_defect(defect_id):
    """'' when the defect id is well shaped AND opens a row of the defect ledger, else why it does not.

    Returns None - not a verdict - when the ledger file is absent, which the caller turns into a
    visible skip. A well-shaped id that no ledger carries is exactly the shape this exists to
    catch, so answering '' on a missing file would pass the mutant it was written for."""
    if not _DEFECT_ID.match(defect_id or ""):
        return f"{defect_id!r} is not a ledger id like 'DRAW-1'"
    path = os.path.join(REPO_ROOT, "plans", _LEDGER_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not re.search(_OPEN_ROW.format(id=re.escape(defect_id)), text, re.M):
        return f"the defect ledger carries no OPEN row for {defect_id}"
    return ""


def _poller_problem(items, poller_name):
    """Why a deferred declaration's named poller cannot confirm the effect, or ''."""
    poller = items.get(poller_name)
    if poller is None:
        return f"poller '{poller_name}' is not a registered tool"
    if is_write_tool(poller):
        return f"poller '{poller_name}' is a write, not a read that confirms"
    return ""


def _duplicate_evidence_claims(items):
    """node id -> the tools claiming it, for every node id claimed more than once."""
    claimed = {}
    for it in items:
        v = _verification_of(it)
        if v is None or not v.evidence_test:
            continue
        claimed.setdefault(v.evidence_test, []).append(it.get_name())
    return {node: sorted(tools) for node, tools in claimed.items() if len(tools) > 1}


class TestVerificationDeclarations:
    """The declaration side: a closed kind whose every reference resolves."""

    def test_every_declaration_is_a_verification_kind(self):
        items = register_all_tools()        # also bootstraps the mcpServer package path
        from mcpServer.mcp_primitives.item import Verification
        wrong = []
        for it in items:
            v = _verification_of(it)
            if v is None:
                continue
            if not isinstance(v, Verification):
                wrong.append(f"{it.get_name()}: {type(v)}")
            elif v.kind not in Verification.KINDS:
                wrong.append(f"{it.get_name()}: kind {v.kind!r}")
        assert not wrong, ("verification= must be a Verification kind from the closed set "
                           f"{list(Verification.KINDS)}:\n  " + "\n  ".join(wrong))

    def test_every_declared_evidence_test_resolves(self):
        broken = []
        for it in register_all_tools():
            v = _verification_of(it)
            if v is None or not v.evidence_test:
                continue
            why = _resolve_node_id(v.evidence_test)
            if why:
                broken.append(f"{it.get_name()} -> {v.evidence_test}: {why}")
        assert not broken, (
            "these tools name an evidence_test that does not resolve - the test was renamed, moved "
            "or deleted, so the declaration claims a proof nobody can run. Point the declaration at "
            "the test that now carries the obligation, or write one:\n  " + "\n  ".join(broken))

    def test_no_evidence_test_is_claimed_by_two_tools(self):
        shared = _duplicate_evidence_claims(register_all_tools())
        assert not shared, (
            "one test cannot carry two tools' obligations - each needs its own biting proof:\n  "
            + "\n  ".join(f"{node}: {', '.join(t)}" for node, t in sorted(shared.items())))

    def test_a_deferred_declaration_names_a_registered_read_tool_as_its_poller(self):
        items = {it.get_name(): it for it in register_all_tools()}
        bad = []
        for name, it in items.items():
            v = _verification_of(it)
            if v is None or v.kind != "deferred":
                continue
            why = _poller_problem(items, v.poller)
            if why:
                bad.append(f"{name} -> {why}")
        assert not bad, ("a deferred effect is confirmed by a NAMED read tool the payload sends "
                         "the caller to:\n  " + "\n  ".join(sorted(bad)))

    def test_an_external_receipt_names_a_row_of_a_live_receipt(self):
        broken = []
        for it in register_all_tools():
            v = _verification_of(it)
            if v is None or not v.evidence_receipt:
                continue
            why = _resolve_receipt(v.evidence_receipt)
            if why:
                broken.append(f"{it.get_name()} -> {v.evidence_receipt}: {why}")
        assert not broken, ("an evidence_receipt points at the receipt row that carries the tool's "
                            "live evidence:\n  " + "\n  ".join(broken))

    def test_dynamic_reaches_only_the_script_hatch(self):
        others = sorted(it.get_name() for it in register_all_tools()
                        if (_verification_of(it) is not None
                            and _verification_of(it).kind == "dynamic"
                            and it.get_name() != _DYNAMIC_TOOL))
        assert not others, (
            f"kind='dynamic' says the requested effect is CALLER-AUTHORED, which is true of "
            f"{_DYNAMIC_TOOL} alone - every other tool declares its own effect and can be held to "
            "it. A second consumer is a redesign decision, not a classification:\n  "
            + "\n  ".join(others))

    def test_a_gap_declaration_resolves_to_an_open_ledger_row(self):
        bad, unresolvable = [], []
        for it in register_all_tools():
            v = _verification_of(it)
            if v is None or v.kind != "gap":
                continue
            why = _resolve_defect(v.defect_id)
            if why is None:
                unresolvable.append(f"{it.get_name()} -> {v.defect_id}")
            elif why:
                bad.append(f"{it.get_name()} -> {why}")
        assert not bad, ("a gap names the id of a defect the defect ledger still carries OPEN, so an "
                         "unverifiable tool is tracked where it can be closed:\n  "
                         + "\n  ".join(sorted(bad)))
        if unresolvable:
            pytest.skip(
                "the defect ledger is not in this checkout, so these gap ids could not be resolved: "
                + ", ".join(sorted(unresolvable)) + ". The ledger is untracked (the plans tree is "
                "gitignored) and lives on the working machine, so a clean checkout cannot see it - "
                "this check SKIPS visibly there rather than passing on a file it never opened. Run "
                "it where the ledger is present.")


