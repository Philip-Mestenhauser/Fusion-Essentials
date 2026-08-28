# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The tool_verify receipt: the source hash + VERIFIED_TOOLS.md stamp binding a green live run to the
exact tool source it exercised.

``source_hash()`` must be OS-portable - relative paths hashed with '/' separators, CRLF
normalized to LF - because the receipt is written on one machine and checked on another (and by
git checkouts with different line-ending config). ``check()`` is the offline gate check_all
runs: red when the receipt is missing, stampless, or the source has moved since the stamp.
"""

import hashlib
import os
import sys

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import tool_verify  # noqa: E402


def _tree(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return str(tmp_path)


class TestSourceHash:
    def test_same_tree_hashes_identically(self, tmp_path):
        root = _tree(tmp_path, {"a.py": b"x = 1\n", "sub/b.py": b"y = 2\n"})
        assert tool_verify.source_hash(root) == tool_verify.source_hash(root)

    def test_content_change_changes_hash(self, tmp_path):
        root = _tree(tmp_path, {"a.py": b"x = 1\n"})
        before = tool_verify.source_hash(root)
        (tmp_path / "a.py").write_bytes(b"x = 2\n")
        assert tool_verify.source_hash(root) != before

    def test_rename_changes_hash(self, tmp_path):
        a = _tree(tmp_path / "one", {"a.py": b"x = 1\n"})
        b = _tree(tmp_path / "two", {"b.py": b"x = 1\n"})
        assert tool_verify.source_hash(a) != tool_verify.source_hash(b)

    def test_crlf_and_lf_hash_identically(self, tmp_path):
        crlf = _tree(tmp_path / "crlf", {"a.py": b"x = 1\r\nif x:\r\n    pass\r\n"})
        lf = _tree(tmp_path / "lf", {"a.py": b"x = 1\nif x:\n    pass\n"})
        assert tool_verify.source_hash(crlf) == tool_verify.source_hash(lf)

    def test_non_py_and_pycache_are_ignored(self, tmp_path):
        root = _tree(tmp_path, {"a.py": b"x = 1\n"})
        before = tool_verify.source_hash(root)
        _tree(tmp_path, {"notes.md": b"prose\n", "data.json": b"{}\n",
                         "__pycache__/a.cpython-312.pyc": b"\x00",
                         "sub/__pycache__/b.py": b"cached = True\n"})
        assert tool_verify.source_hash(root) == before

    def test_paths_hash_with_forward_slashes(self, tmp_path):
        # Pins the separator normalization: the digest must be computable with '/' joined
        # relative paths regardless of the OS the receipt was written on.
        content = b"z = 3\n"
        root = _tree(tmp_path, {"sub/deep/c.py": content})
        expected = hashlib.sha256(b"sub/deep/c.py" + b"\0" + content + b"\0").hexdigest()
        assert tool_verify.source_hash(root) == expected


class TestVerifiedReceipt:
    _LEDGER = [("appearance_set", "covered"),
               ("doc_open", "skipped: opens cloud files"),
               ("model_loft", "PENDING (no step yet)")]

    def test_round_trip_write_then_check_is_current(self, tmp_path, capsys):
        root = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        receipt = str(tmp_path / "VERIFIED_TOOLS.md")
        tool_verify.write_verified(self._LEDGER, "2704.1.23", "2026-07-11",
                                   tool_verify.source_hash(root), path=receipt)
        assert tool_verify.check(root=root, verified_path=receipt) == 0
        assert "2026-07-11" in capsys.readouterr().out

    def test_check_goes_red_when_source_changes_after_stamp(self, tmp_path, capsys):
        root = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        receipt = str(tmp_path / "VERIFIED_TOOLS.md")
        tool_verify.write_verified(self._LEDGER, "2704.1.23", "2026-07-11",
                                   tool_verify.source_hash(root), path=receipt)
        (tmp_path / "src" / "a.py").write_bytes(b"x = 2\n")
        assert tool_verify.check(root=root, verified_path=receipt) == 1
        out = capsys.readouterr().out
        assert "changed since" in out and "tool_verify.py" in out

    def test_check_goes_red_without_a_receipt(self, tmp_path, capsys):
        root = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        assert tool_verify.check(root=root, verified_path=str(tmp_path / "VERIFIED_TOOLS.md")) == 1
        assert "tool_verify.py" in capsys.readouterr().out

    def test_check_goes_red_on_a_stampless_receipt(self, tmp_path, capsys):
        root = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        receipt = tmp_path / "VERIFIED_TOOLS.md"
        receipt.write_text("# Live tool verification\n\nno stamp here\n", encoding="utf-8")
        assert tool_verify.check(root=root, verified_path=str(receipt)) == 1
        assert "stamp" in capsys.readouterr().out

    def test_receipt_carries_stamp_counts_and_ledger_rows(self, tmp_path):
        root = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        receipt = str(tmp_path / "VERIFIED_TOOLS.md")
        src_hash = tool_verify.source_hash(root)
        tool_verify.write_verified(self._LEDGER, "2704.1.23", "2026-07-11", src_hash, path=receipt)
        with open(receipt, encoding="utf-8") as fh:
            text = fh.read()
        m = tool_verify._STAMP_RE.search(text)
        assert m and m.groups() == (src_hash, "2704.1.23", "2026-07-11")
        assert "1 covered / 0 called / 0 refusals-only / 1 skipped(reason) / 1 pending" in text
        assert "| appearance_set | covered |" in text
        assert "| doc_open | skipped: opens cloud files |" in text
        assert "| model_loft | PENDING (no step yet) |" in text

    def test_bare_ok_row_lands_in_called_and_a_value_row_in_covered(self, tmp_path):
        # The split the receipt exists to make visible: 'covered' counts only the tools whose step
        # read a value off the payload; a bare-ok tool is counted and labelled separately.
        root = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        receipt = str(tmp_path / "VERIFIED_TOOLS.md")
        tool_verify.write_verified([("model_extrude", "covered"), ("model_create_component", "called")],
                                   "2704.1.23", "2026-07-11", tool_verify.source_hash(root),
                                   path=receipt)
        with open(receipt, encoding="utf-8") as fh:
            text = fh.read()
        assert "1 covered / 1 called / 0 refusals-only / 0 skipped(reason) / 0 pending" in text
        assert "| model_extrude | covered |" in text
        assert "| model_create_component | called |" in text
        # both terms are defined in the header, or the numbers are unreadable
        assert "- covered:" in text and "- called:" in text

    def test_a_parked_row_carries_its_reason_and_still_counts_as_called(self, tmp_path):
        # A parked step's reason otherwise lives only in a source comment: the receipt would show a
        # plain 'called' and no reader could tell a missing predicate from a deliberately held one.
        # The reason is RENDERING, not a third bucket - the count line must not drift from it.
        root = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        receipt = str(tmp_path / "VERIFIED_TOOLS.md")
        tool_verify.write_verified([("model_extrude", "covered"),
                                    ("model_draft", "called (effect read unreadable - DR-1)")],
                                   "2704.1.23", "2026-07-11", tool_verify.source_hash(root),
                                   path=receipt)
        with open(receipt, encoding="utf-8") as fh:
            text = fh.read()
        assert "1 covered / 1 called / 0 refusals-only / 0 skipped(reason) / 0 pending" in text
        assert "| model_draft | called (effect read unreadable - DR-1) |" in text


class TestPredicateKind:
    """The classifier behind the split: it reads the step's EXPECTATION object, nothing else."""

    def test_bare_ok_string_is_a_call(self):
        assert tool_verify.predicate_kind("ok") == "call"

    def test_a_lambda_reading_the_payload_is_a_value(self):
        assert tool_verify.predicate_kind(lambda p: p.get("result_bodies")) == "value"

    def test_a_named_predicate_function_is_a_value(self):
        assert tool_verify.predicate_kind(tool_verify._repair_no_op) == "value"

    def test_a_constant_lambda_is_a_call_not_a_value(self):
        # 'lambda p: True' wears a predicate's shape and inspects nothing - counting it as covered
        # is exactly the inflation this split exists to prevent.
        assert tool_verify.predicate_kind(lambda p: True) == "call"

    def test_a_lambda_reading_a_closure_but_not_the_payload_is_a_call(self):
        want = 3
        assert tool_verify.predicate_kind(lambda p: want > 2) == "call"

    def test_refusal_strings_and_fragment_refusals_are_refusals(self):
        assert tool_verify.predicate_kind("refused") == "refusal"
        assert tool_verify.predicate_kind(tool_verify._refused("no such body")) == "refusal"

    def test_run_steps_returns_one_row_per_step_in_order(self, monkeypatch):
        # The receipt's covered/called split pairs each result row back with the EXPECTATION that
        # judged it by position, so a step that produced no row (or two) would attribute a value
        # predicate to the wrong tool. A blocked step still gets its row.
        monkeypatch.setattr(tool_verify, "call", lambda tool, args: (False, {"n": 1}))
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        steps = [
            ("a_get", {}, "ok", None),
            ("b_get", {}, lambda p: p.get("n") == 1, None),
            ("c_get", lambda ctx: {"x": ctx["missing"]}, "ok", None),   # blocked: no ctx key
            ("d_get", {}, lambda p: p.get("n") == 2, None),             # predicate fails
        ]
        rows = tool_verify.run_steps(steps, {})
        assert [r[0] for r in rows] == ["a_get", "b_get", "c_get", "d_get"]
        assert [r[1] for r in rows] == ["pass", "pass", "blocked", "FAIL"]

    def test_every_steps_row_classifies_and_no_constant_predicate_hides_in_them(self):
        # A callable in STEPS that never READS INTO its payload would be counted 'covered' on this
        # run while proving nothing; there are none, and this is what keeps it that way. Every
        # callable expectation in STEPS therefore has to satisfy the guard below.
        kinds = [tool_verify.predicate_kind(step[2]) for step in tool_verify.STEPS]
        assert set(kinds) <= {"value", "call", "refusal"}
        constants = [step[0] for step in tool_verify.STEPS
                     if callable(tool_verify._unparked(step[2]))
                     and not isinstance(tool_verify._unparked(step[2]), tool_verify._Refusal)
                     and tool_verify.predicate_kind(step[2]) == "call"]
        assert not constants, ("STEPS rows whose predicate never reads the payload: "
                               + ", ".join(sorted(set(constants))))


def _reads_a_key(payload):
    """A module-level helper that DOES read the payload - the callee side of `lambda p: helper(p)`."""
    return payload.get("result_bodies")


class TestValuePredicateGuard:
    """Loading the argument is not the same as reading it. Each shape below TOUCHES the payload and
    inspects nothing in it, and each would otherwise be bucketed 'covered' while proving no more
    than a bare "ok" - the exact inflation the covered/called split exists to prevent."""

    def test_a_truthiness_test_on_the_payload_is_a_call(self):
        assert tool_verify.predicate_kind(lambda p: p and True) == "call"

    def test_bool_of_the_payload_is_a_call(self):
        assert tool_verify.predicate_kind(lambda p: bool(p)) == "call"

    def test_a_none_check_on_the_payload_is_a_call(self):
        assert tool_verify.predicate_kind(lambda p: p is not None) == "call"

    def test_an_attribute_read_off_the_payload_is_a_value(self):
        assert tool_verify.predicate_kind(lambda p: p.get("moved") is True) == "value"

    def test_a_subscript_of_the_payload_is_a_value(self):
        assert tool_verify.predicate_kind(lambda p: p["joints"][0]["name"] == "Yaw") == "value"

    def test_handing_the_payload_to_a_reading_helper_is_a_value(self):
        assert tool_verify.predicate_kind(lambda p: _reads_a_key(p)) == "value"

    def test_a_payload_captured_by_an_inner_comprehension_is_still_a_value(self):
        # Closing over the payload inside the predicate makes it a CELL, so every read of it
        # compiles to LOAD_DEREF instead of LOAD_FAST - a real read either way.
        assert tool_verify.predicate_kind(
            lambda p: all(r.get("name") for r in (p.get("joints") or []))) == "value"


class TestParkedSteps:
    """Parked carries a ledger REASON, never a judgement."""

    def test_predicate_kind_reads_through_the_marker(self):
        assert tool_verify.predicate_kind(tool_verify.Parked("held")) == "call"
        assert tool_verify.predicate_kind(
            tool_verify.Parked("held", lambda p: p.get("n"))) == "value"
        assert tool_verify.predicate_kind(tool_verify.Parked("held", "refused")) == "refusal"

    def test_a_parked_step_is_judged_by_the_expectation_inside_it(self, monkeypatch):
        monkeypatch.setattr(tool_verify, "call", lambda tool, args: (False, {"n": 1}))
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        rows = tool_verify.run_steps([
            ("a_get", {}, tool_verify.Parked("held at a bare ok"), None),
            ("b_get", {}, tool_verify.Parked("held", lambda p: p.get("n") == 2), None),
        ], {})
        assert [r[1] for r in rows] == ["pass", "FAIL"]
