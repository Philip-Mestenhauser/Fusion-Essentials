# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The tool_verify receipt: the source hash + VERIFIED_TOOLS.md stamp binding a green live run to the
exact tool source it exercised.

``source_hash()`` must be OS-portable - relative paths hashed with '/' separators, CRLF
normalized to LF - because the receipt is written on one machine and checked on another (and by
git checkouts with different line-ending config). ``check()`` is the offline gate check_all
runs: red when the receipt is missing, stampless, or the source has moved since the stamp.
"""

import ast
import hashlib
import json
import os
import sys
import urllib.request

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import tool_verify  # noqa: E402
import verify_runner  # noqa: E402  source_hash reads SRC_ROOT/_HERE off ITS namespace, not the facade


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


class TestHarnessSideOfTheHash:
    """Which tests/live modules the receipt binds: the SWEEP's, and only those."""

    @staticmethod
    def _rig(tmp_path, monkeypatch):
        src = _tree(tmp_path / "src", {"a.py": b"x = 1\n"})
        live = tmp_path / "live"
        live.mkdir()
        (live / "measure_api.py").write_bytes(b"ROWS = []\n")
        (live / "verify_core.py").write_bytes(b"EXCLUDED = {}\n")
        monkeypatch.setattr(verify_runner, "SRC_ROOT", src)
        monkeypatch.setattr(verify_runner, "_HERE", str(live))
        return src, live

    def test_a_facts_harness_edit_leaves_the_hash_where_a_predicate_edit_moves_it(
            self, tmp_path, monkeypatch):
        # measure_api.py judges no step of the sweep, so a row-only edit to it must not force a
        # seven-minute re-run; verify_core.py holds the predicates the receipt exists to bind.
        src, live = self._rig(tmp_path, monkeypatch)
        before = tool_verify.source_hash(src)
        (live / "measure_api.py").write_bytes(b"ROWS = [{'id': 'new-row'}]\n")
        assert tool_verify.source_hash(src) == before
        (live / "verify_core.py").write_bytes(b"EXCLUDED = {'model_loft': 'skipped'}\n")
        assert tool_verify.source_hash(src) != before

    def test_no_module_the_sweep_imports_is_left_out_of_the_hash(self):
        # The excluded names are safe only while the sweep does not read them: a predicate reached
        # through one of these would then ride under a green receipt that never saw it change.
        skipped = {fn[:-3] for fn in verify_runner._NOT_THE_SWEEP}
        importers = []
        for fn in sorted(os.listdir(tool_verify._HERE)):
            if not fn.endswith(".py") or fn in verify_runner._NOT_THE_SWEEP:
                continue
            with open(os.path.join(tool_verify._HERE, fn), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=fn)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                importers += [f"{fn} imports {n}" for n in names if n in skipped]
        assert not importers, ("the receipt skips a module the sweep reads: "
                               + ", ".join(importers))


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

    def test_judged_steps_and_run_steps_agree_on_every_step_kind(self, monkeypatch):
        # The by-position pairing needs these two lists to be the same length, and each decides
        # what yields a row through _leaves_no_row. A second row-less step kind taught to one site
        # alone would shift the pairing again with every other test here still green.
        monkeypatch.setattr(tool_verify, "call", lambda tool, args: (False, {"n": 1}))
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        steps = [
            ("bare_ok", {}, "ok", None),
            tool_verify._dwell(0),
            ("value_pred", {}, lambda p: p["n"] == 1, None),
            ("refusal", {}, "refused", None),
            ("blocked", lambda ctx: {"x": ctx["missing"]}, "ok", None),
            ("failing", {}, lambda p: p["n"] == 99, None),
            ("with_save", {}, "ok", ("k", lambda p: p["n"])),
            tool_verify._dwell(0),
        ]
        assert len(tool_verify.judged_steps(steps)) == len(tool_verify.run_steps(steps, {}))

    def test_a_dwell_does_not_shift_predicate_attribution(self, monkeypatch):
        # A _dwell is judged by nothing and leaves no row, so it is the one step that can break the
        # by-position pairing: run over the act's raw list and every expectation after a dwell is
        # credited to a LATER step's tool - a bare "ok" reads as covered and a value predicate is
        # lost. judged_steps is what the pairing runs over.
        monkeypatch.setattr(tool_verify, "call", lambda tool, args: (False, {"n": 1}))
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        steps = [
            ("a_get", {}, "ok", None),
            tool_verify._dwell(0),
            ("b_get", {}, "ok", None),
            ("c_get", {}, lambda p: p["n"] == 1, None),
        ]
        rows = tool_verify.run_steps(steps, {})
        paired = [(row[0], tool_verify.predicate_kind(step[2]))
                  for step, row in zip(tool_verify.judged_steps(steps), rows)]
        assert paired == [("a_get", "call"), ("b_get", "call"), ("c_get", "value")]

    def test_run_credits_covered_to_the_tool_whose_own_step_read_a_value(self, monkeypatch):
        # The pairing AT ITS CALL SITE, over a one-act program holding the shape that breaks it: a
        # dwell between the bare-ok rows and the value predicate. Whichever list run() pairs with
        # the rows decides the ledger, so this is what a revert to zip(steps, ...) has to fail.
        ledger = {}

        def fake_write(rows, version, date, src_hash, notes=None, act_modes=None):
            ledger.update(rows)
            return "VERIFIED_TOOLS.md"

        monkeypatch.setattr(tool_verify, "call", lambda tool, args: (False, {"n": 1}))
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        monkeypatch.setattr(tool_verify, "health_gate", lambda: {"server": "ok", "version": "t"})
        monkeypatch.setattr(tool_verify, "registered_tools", lambda: ["a_get", "b_get", "c_get"])
        monkeypatch.setattr(tool_verify, "source_hash", lambda *a, **k: "0" * 64)
        monkeypatch.setattr(tool_verify, "write_verified", fake_write)
        monkeypatch.setattr(tool_verify, "POLL_AFTER", {})
        monkeypatch.setattr(tool_verify, "EXCLUDED", {})
        monkeypatch.setattr(tool_verify, "STORY", {})
        monkeypatch.setattr(tool_verify, "ACTS", [("ACT T", None, [
            ("a_get", {}, "ok", None),
            tool_verify._dwell(0),
            ("b_get", {}, "ok", None),
            ("c_get", {}, lambda p: p["n"] == 1, None),
        ], None)])

        assert tool_verify.run(write_json=False) == 0
        assert ledger == {"a_get": "called", "b_get": "called", "c_get": "covered"}

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


class TestCapabilityProbe:
    """The machining_extension probe reads workspace_orient's own entitlement block. Entitled means
    every sentinel flag answered TRUE; anything it could not read is None, which routes as unmet."""

    @staticmethod
    def _wire(monkeypatch, answer):
        monkeypatch.setattr(tool_verify, "call", lambda tool, args: answer)

    def test_all_four_sentinels_true_is_entitled(self, monkeypatch):
        self._wire(monkeypatch, (False, {"machining_capabilities": {"observed_generation": {
            "steep_and_shallow": True, "multiaxis_finishing": True,
            "swarf": True, "probe_geometry": True}}}))
        assert tool_verify._machining_extension_probe() is True

    def test_one_sentinel_false_is_not_entitled(self, monkeypatch):
        self._wire(monkeypatch, (False, {"machining_capabilities": {"observed_generation": {
            "steep_and_shallow": True, "multiaxis_finishing": True,
            "swarf": False, "probe_geometry": True}}}))
        assert tool_verify._machining_extension_probe() is False

    def test_a_null_flag_is_unreadable_not_entitled(self, monkeypatch):
        # null is 'the probe could not read it'. Reading it as False would report an entitlement
        # verdict nothing measured; reading it as True would run rows this licence cannot generate.
        self._wire(monkeypatch, (False, {"machining_capabilities": {"observed_generation": {
            "steep_and_shallow": True, "multiaxis_finishing": None,
            "swarf": True, "probe_geometry": True}}}))
        assert tool_verify._machining_extension_probe() is None

    def test_a_missing_block_and_a_failed_read_are_both_unreadable(self, monkeypatch):
        self._wire(monkeypatch, (False, {"document": {"name": "X"}}))
        assert tool_verify._machining_extension_probe() is None
        self._wire(monkeypatch, (True, "no active document"))
        assert tool_verify._machining_extension_probe() is None


class TestCapabilityTier:
    """The tier itself: what a declaration means, and which receipt bucket an unmet one lands in."""

    _FAKE = {"yes": lambda: True, "no": lambda: False, "dunno": lambda: None}

    def test_each_declared_capability_is_probed_once(self):
        calls = []
        probes = {"yes": lambda: calls.append("yes") or True}
        assert tool_verify.probe_capabilities(["yes", "yes"], probes) == {"yes": True}
        assert calls == ["yes"]

    def test_an_unregistered_capability_answers_unreadable(self):
        # a typo in a declaration must not read as entitled - there is no probe to say it is.
        assert tool_verify.probe_capabilities(["nosuch"], self._FAKE) == {"nosuch": None}

    def test_only_a_true_probe_is_met(self):
        ent = tool_verify.probe_capabilities(["yes", "no", "dunno"], self._FAKE)
        assert tool_verify.capability_met(ent, "yes") is True
        assert tool_verify.capability_met(ent, "no") is False
        assert tool_verify.capability_met(ent, "dunno") is False
        assert tool_verify.capability_met(ent, None) is True

    def test_the_skip_reason_separates_not_entitled_from_unreadable(self):
        ent = {"no": False, "dunno": None}
        assert tool_verify.capability_skip_reason("no", ent) == "no not entitled"
        assert "probe did not read" in tool_verify.capability_skip_reason("dunno", ent)

    def test_the_declaration_is_read_through_both_wrappers(self):
        needs = tool_verify._needs("yes", lambda p: p.get("n"))
        assert tool_verify.step_capability(needs) == "yes"
        assert tool_verify.step_capability(tool_verify.Parked("held", needs)) == "yes"
        assert tool_verify.step_capability(tool_verify.Parked("held")) is None
        assert tool_verify.step_capability("ok") is None

    def test_a_gate_changes_the_bucket_and_never_the_judgement(self):
        # Needs carries ledger routing only: the expectation inside is what judges the step, so the
        # covered/called/refusal split must read straight through it.
        assert tool_verify.predicate_kind(tool_verify._needs("yes")) == "call"
        assert tool_verify.predicate_kind(tool_verify._needs("yes", lambda p: p["n"])) == "value"
        assert tool_verify.predicate_kind(tool_verify._needs("yes", "refused")) == "refusal"
        assert tool_verify.predicate_kind(
            tool_verify._needs("yes", tool_verify.Parked("held", lambda p: p["n"]))) == "value"

    def test_a_parked_reason_survives_a_gate_around_it(self):
        assert tool_verify.parked_reason(
            tool_verify._needs("yes", tool_verify.Parked("held at a bare ok"))) == "held at a bare ok"
        assert tool_verify.parked_reason("ok") is None

    @staticmethod
    def _run(monkeypatch, acts, act_needs, entitled, tools, poll_after=None):
        """run() over a stubbed wire, returning (ledger, the tools the wire was actually called
        with, the act modes)."""
        out = {}
        seen = []

        def fake_write(rows, version, date, src_hash, notes=None, act_modes=None):
            out["ledger"] = dict(rows)
            out["act_modes"] = list(act_modes or [])
            return "VERIFIED_TOOLS.md"

        def call(tool, args):
            seen.append(tool)
            if tool == "workspace_orient":
                flags = {n: entitled for n in ("steep_and_shallow", "multiaxis_finishing",
                                               "swarf", "probe_geometry")}
                return False, {"machining_capabilities": {"observed_generation": flags}}
            return False, {"n": 1}

        monkeypatch.setattr(tool_verify, "call", call)
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        monkeypatch.setattr(tool_verify, "health_gate", lambda: {"server": "ok", "version": "t"})
        monkeypatch.setattr(tool_verify, "registered_tools", lambda: sorted(tools))
        monkeypatch.setattr(tool_verify, "source_hash", lambda *a, **k: "0" * 64)
        monkeypatch.setattr(tool_verify, "write_verified", fake_write)
        # the post-run reload beat is another act's business; stubbed so 'seen' is this tier's
        monkeypatch.setattr(tool_verify, "reload_smoke",
                            lambda rows, notes, valued=None, **kw: None)
        monkeypatch.setattr(tool_verify, "POLL_AFTER", poll_after or {})
        monkeypatch.setattr(tool_verify, "EXCLUDED", {})
        monkeypatch.setattr(tool_verify, "STORY", {})
        monkeypatch.setattr(tool_verify, "ACT_NEEDS", act_needs)
        monkeypatch.setattr(tool_verify, "ACTS", acts)
        assert tool_verify.run(write_json=False) == 0
        return out["ledger"], seen, out["act_modes"]

    def test_a_gated_act_runs_nothing_and_banks_the_capability_bucket(self, monkeypatch):
        # BOTH lanes bank the bucket: neither ran, so a tool the fallback alone drives is skipped
        # for the same reason - reported PENDING, it would read as never scripted.
        acts = [("ACT E", ("pre_get", {}), [("a_get", {}, lambda p: p["n"] == 1, None)],
                 [("fb_get", {}, "ok", None)])]
        ledger, seen, modes = self._run(
            monkeypatch, acts, {"ACT E": "machining_extension"}, entitled=False,
            tools=["a_get", "fb_get", "pre_get"])
        assert ledger["a_get"] == "skipped: machining_extension not entitled"
        assert ledger["fb_get"] == "skipped: machining_extension not entitled"
        # not even the act's PRECONDITION read fires - it decides between two lanes neither of
        # which may run, and a wire call for that is a call for nothing.
        assert seen == ["workspace_orient"]
        assert modes == [("ACT E", "skipped(machining_extension not entitled)")]

    def test_the_same_act_runs_where_the_capability_is_entitled(self, monkeypatch):
        acts = [("ACT E", None, [("a_get", {}, lambda p: p["n"] == 1, None)], [])]
        ledger, seen, modes = self._run(
            monkeypatch, acts, {"ACT E": "machining_extension"}, entitled=True, tools=["a_get"])
        assert ledger["a_get"] == "covered"
        assert seen == ["workspace_orient", "a_get"]
        assert modes == [("ACT E", "narrative")]

    def test_a_gated_step_is_dropped_without_shifting_the_rows_after_it(self, monkeypatch):
        # The bug this exists to catch is silent and green: a gated step that still yielded a row
        # would pair every later expectation with the wrong tool, so a bare "ok" would read as
        # covered and a value predicate would be lost.
        acts = [("ACT T", None, [
            ("a_get", {}, "ok", None),
            ("gated_get", {}, tool_verify._needs("machining_extension", lambda p: p["n"] == 1), None),
            ("c_get", {}, lambda p: p["n"] == 1, None),
        ], [])]
        ledger, seen, _modes = self._run(
            monkeypatch, acts, {}, entitled=False, tools=["a_get", "gated_get", "c_get"])
        assert ledger == {"a_get": "called",
                          "gated_get": "skipped: machining_extension not entitled",
                          "c_get": "covered"}
        assert seen == ["workspace_orient", "a_get", "c_get"]

    def test_the_probe_waits_for_the_first_act_that_declares_a_capability(self, monkeypatch):
        # workspace_orient errors with no document open, so a probe taken before the opening act
        # reads unreadable and skips every gated row on an ENTITLED machine. The first act declares
        # nothing: its steps run first, and the probe lands between them and the gated act's.
        acts = [("ACT OPEN", None, [("doc_new", {}, "ok", None)], []),
                ("ACT E", None, [("a_get", {}, lambda p: p["n"] == 1, None)], [])]
        ledger, seen, _modes = self._run(
            monkeypatch, acts, {"ACT E": "machining_extension"}, entitled=True,
            tools=["a_get", "doc_new"])
        assert seen == ["doc_new", "workspace_orient", "a_get"]
        assert ledger["a_get"] == "covered"

    def test_a_gated_act_polls_nothing(self, monkeypatch):
        # the boundary poll certifies a generation the act launched; a held-back act launched none,
        # and polling for one would fail the run on an act that deliberately did not happen.
        polled = []
        monkeypatch.setattr(tool_verify, "poll_generation",
                            lambda rows, notes, setup, valued=None: polled.append(setup))
        acts = [("ACT E", None, [("a_get", {}, "ok", None)], [])]
        self._run(monkeypatch, acts, {"ACT E": "machining_extension"}, entitled=False,
                  tools=["a_get", "workspace_orient"],
                  poll_after={"ACT E": {"narrative": "SwarfSetup", "fallback": "SwarfSetup"}})
        assert polled == []

    def test_a_poll_target_list_certifies_each_setup_in_turn(self, monkeypatch):
        # one act can leave SEVERAL setups generating; a list that only polled its first element
        # would leave the rest uncertified while the run still read green.
        polled = []
        monkeypatch.setattr(tool_verify, "poll_generation",
                            lambda rows, notes, setup, valued=None: polled.append(setup))
        acts = [("ACT G", None, [("a_get", {}, "ok", None)], [])]
        self._run(monkeypatch, acts, {}, entitled=True, tools=["a_get", "workspace_orient"],
                  poll_after={"ACT G": {"narrative": ["One", "Two"], "fallback": ["One", "Two"]}})
        assert polled == ["One", "Two"]

    def test_an_unreadable_probe_holds_the_steps_back_and_says_so(self, monkeypatch):
        def call(tool, args):
            return (False, {}) if tool == "workspace_orient" else (False, {"n": 1})
        monkeypatch.setattr(tool_verify, "call", call)
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        monkeypatch.setattr(tool_verify, "health_gate", lambda: {"server": "ok", "version": "t"})
        monkeypatch.setattr(tool_verify, "registered_tools", lambda: ["a_get", "workspace_orient"])
        monkeypatch.setattr(tool_verify, "source_hash", lambda *a, **k: "0" * 64)
        ledger = {}
        monkeypatch.setattr(tool_verify, "write_verified",
                            lambda rows, v, d, h, notes=None, act_modes=None: ledger.update(rows))
        monkeypatch.setattr(tool_verify, "POLL_AFTER", {})
        monkeypatch.setattr(tool_verify, "EXCLUDED", {})
        monkeypatch.setattr(tool_verify, "STORY", {})
        monkeypatch.setattr(tool_verify, "ACT_NEEDS", {"ACT E": "machining_extension"})
        monkeypatch.setattr(tool_verify, "ACTS",
                            [("ACT E", None, [("a_get", {}, "ok", None)], [])])
        assert tool_verify.run(write_json=False) == 0
        assert ledger["a_get"] == ("skipped: machining_extension not entitled - the capability "
                                   "probe did not read")

    def test_every_declared_capability_in_the_real_program_has_a_probe(self):
        # a declaration with no probe routes as unmet forever, so the acts it gates would never run
        # again on any installation - and nothing else in the harness would say why.
        declared = set(tool_verify.ACT_NEEDS.values()) | {
            cap for step in tool_verify.STEPS
            for cap in [tool_verify.step_capability(step[2])] if cap}
        assert declared, "no capability is declared - the tier has no consumer"
        assert declared <= set(tool_verify.CAPABILITY_PROBES), (
            "capabilities declared with no probe registered: "
            + ", ".join(sorted(declared - set(tool_verify.CAPABILITY_PROBES))))

    def test_every_gated_act_name_is_a_real_act(self):
        names = {name for name, _p, _n, _f in tool_verify.ACTS}
        assert set(tool_verify.ACT_NEEDS) <= names, (
            "ACT_NEEDS names acts the program does not run: "
            + ", ".join(sorted(set(tool_verify.ACT_NEEDS) - names)))

    def test_every_extension_only_act_keeps_a_base_licence_variant(self):
        # the tier's whole point: a gated act may not take a TOOL's only step with it. Every tool
        # the gated acts and steps drive must also be driven by a step no capability gates, or an
        # unentitled installation loses that tool's coverage rather than one strategy's.
        gated_acts = set(tool_verify.ACT_NEEDS)
        held, free = set(), set()
        for name, _pre, narr, fb in tool_verify.ACTS:
            for step in (list(narr) + list(fb or [])):
                if step[0] == tool_verify._DWELL:
                    continue
                if name in gated_acts or tool_verify.step_capability(step[2]):
                    held.add(step[0])
                else:
                    free.add(step[0])
        assert not (held - free), (
            "tools whose every step rides the capability tier: " + ", ".join(sorted(held - free)))


class TestFacadeLateBinding:
    """The call-time facade() sites with no other offline pin: each must see what a consumer
    stubs ON tool_verify at CALL time. An import-time binding of its own copy leaves the stub
    unread while every other offline test stays green, so these assertions are each site's one
    offline bite."""

    def test_precondition_holds_reads_the_stubbed_wire(self, monkeypatch):
        seen = []
        monkeypatch.setattr(tool_verify, "call",
                            lambda tool, args: (seen.append((tool, args)), (False, {}))[1])
        assert tool_verify._precondition_holds(("sketch_get", {"sketch_name": "X"})) is True
        assert seen == [("sketch_get", {"sketch_name": "X"})]
        monkeypatch.setattr(tool_verify, "call", lambda tool, args: (True, "down"))
        assert tool_verify._precondition_holds(("sketch_get", {})) is False

    def test_shoot_reads_the_stubbed_wire_and_names_the_file(self, monkeypatch, tmp_path):
        seen = []
        monkeypatch.setattr(tool_verify, "call",
                            lambda tool, args: (seen.append((tool, args)), (False, {}))[1])
        path = tool_verify._shoot("Frame:1", str(tmp_path), 3)
        assert seen and seen[0][0] == "view_screenshot"
        assert path == seen[0][1]["file_path"] and "0003_Frame_1" in path
        monkeypatch.setattr(tool_verify, "call", lambda tool, args: (True, "no viewport"))
        assert tool_verify._shoot("x", str(tmp_path), 4) is None

    def test_poll_generation_reads_the_stubbed_wire_and_story(self, monkeypatch):
        monkeypatch.setattr(tool_verify, "call",
                            lambda tool, args: (False, {"completed": True,
                                                        "live_states": {"valid": 4},
                                                        "empty_toolpaths": []}))
        monkeypatch.setattr(tool_verify, "STORY", {"cam_get_status": "stubbed story line"})
        rows, notes, valued = [], {}, set()
        tool_verify.poll_generation(rows, notes, "DemoSetup", valued=valued)
        assert rows == [("cam_get_status", "pass", "4 valid, non-empty toolpaths")]
        assert notes["cam_get_status"] == "stubbed story line"
        assert valued == {"cam_get_status"}

    def test_check_reads_the_stubbed_source_hash(self, monkeypatch, tmp_path):
        receipt = tmp_path / "VERIFIED_TOOLS.md"
        stamp = "Stamp: source " + "ab" * 32 + " | Fusion 2705.1.4 | verified 2026-08-31" + chr(10)
        receipt.write_text(stamp, encoding="utf-8")
        monkeypatch.setattr(tool_verify, "source_hash", lambda root=None: "ab" * 32)
        assert tool_verify.check(verified_path=str(receipt)) == 0
        monkeypatch.setattr(tool_verify, "source_hash", lambda root=None: "cd" * 32)
        assert tool_verify.check(verified_path=str(receipt)) == 1


_SCHEDULED = ("Reload scheduled. Make your next tool call after ~3 seconds - the connection "
              "reconnects automatically.")


def _reload_wire(reload_answer=(False, _SCHEDULED), found=("sys_reload_addin", "doc_get")):
    """The two wire calls the reload beat makes, stubbed: sys_reload_addin's own answer, and the
    registry search the smoke reads."""
    def call(tool, args):
        if tool == "sys_reload_addin":
            if isinstance(reload_answer, Exception):
                raise reload_answer
            return reload_answer
        return False, {"query": args.get("query"), "tool_count": len(found),
                       "tools": [{"tool": name} for name in found]}
    return call


def _health(*states):
    """A stubbed /health probe answering `states` in order and then holding the last one, so a
    poll that keeps asking sees a server that stays where the sequence left it."""
    seq = list(states)
    return lambda timeout=None: seq.pop(0) if len(seq) > 1 else seq[0]


def _urlopen(body=None, raises=None):
    """A stand-in for urllib.request.urlopen: a context manager whose read() answers `body` as a
    JSON document, or a raise standing in for a socket that answers nothing."""
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(body).encode("utf-8")

    def urlopen(url, timeout=None):
        if raises is not None:
            raise raises
        return _Response()
    return urlopen


class TestReloadBeat:
    """The post-run beat that drives sys_reload_addin. It claims a covered row ONLY for a restart
    it watched happen: the reload is deferred, so the server is still answering when the call
    returns, and every path below that cannot see /health go down and come back leaves the tool in
    its skipped bucket instead of banking a row."""

    @staticmethod
    def _drive(monkeypatch, call, answers, story="stubbed reload story"):
        monkeypatch.setattr(tool_verify, "call", call)
        monkeypatch.setattr(tool_verify, "_server_answers", answers)
        monkeypatch.setattr(tool_verify, "STORY", {"sys_reload_addin": story})
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        rows, notes, valued = [], {}, set()
        tool_verify.reload_smoke(rows, notes, valued=valued, down_polls=3, up_polls=3)
        return rows, notes, valued

    def test_a_watched_restart_banks_one_covered_row(self, monkeypatch):
        rows, notes, valued = self._drive(
            monkeypatch, _reload_wire(), _health(True, False, True))
        assert [(t, s) for t, s, _ in rows] == [("sys_reload_addin", "pass")]
        assert "answered again" in rows[0][2] and "sys_reload_addin among them" in rows[0][2]
        assert notes["sys_reload_addin"] == "stubbed reload story"
        assert valued == {"sys_reload_addin"}

    def test_a_server_that_never_goes_down_banks_nothing(self, monkeypatch):
        # The false positive the down-then-up watch exists to refuse: the reload is DEFERRED, so a
        # /health read taken when the call returns answers healthy whether or not the reload ever
        # fires. A beat that only waited for /health to answer would call this a restart.
        rows, notes, valued = self._drive(monkeypatch, _reload_wire(), _health(True))
        assert rows == [] and notes == {} and valued == set()

    def test_a_server_that_does_not_come_back_banks_nothing(self, monkeypatch):
        rows, _notes, valued = self._drive(monkeypatch, _reload_wire(), _health(True, False))
        assert rows == [] and valued == set()

    def test_a_reload_that_was_not_scheduled_banks_nothing(self, monkeypatch):
        # the tool's own refusal path (its deferred-reload event is not installed): nothing was
        # scheduled, so nothing restarts and the beat must not go on to watch for one.
        rows, _notes, valued = self._drive(
            monkeypatch, _reload_wire(reload_answer=(True, "Reload NOT scheduled: ...")),
            _health(True, False, True))
        assert rows == [] and valued == set()

    def test_an_ok_that_does_not_say_scheduled_banks_nothing(self, monkeypatch):
        rows, _notes, valued = self._drive(
            monkeypatch, _reload_wire(reload_answer=(False, "something else entirely")),
            _health(True, False, True))
        assert rows == [] and valued == set()

    def test_a_call_cut_off_by_the_teardown_banks_nothing(self, monkeypatch):
        # the response can be cut mid-flush; the beat reports that rather than raising through run
        rows, _notes, valued = self._drive(
            monkeypatch, _reload_wire(reload_answer=OSError("connection reset")),
            _health(True, False, True))
        assert rows == [] and valued == set()

    def test_a_registry_missing_the_tool_banks_nothing(self, monkeypatch):
        # /health answering is the SERVER being back; the registry answering with the tool is the
        # add-in being back. A restart that loaded no tools passes the first and fails here.
        rows, _notes, valued = self._drive(
            monkeypatch, _reload_wire(found=()), _health(True, False, True))
        assert rows == [] and valued == set()

    def test_the_smoke_read_failing_banks_nothing(self, monkeypatch):
        def call(tool, args):
            return (False, _SCHEDULED) if tool == "sys_reload_addin" else (True, "no such tool")
        rows, _notes, valued = self._drive(monkeypatch, call, _health(True, False, True))
        assert rows == [] and valued == set()

    def test_the_probe_answers_true_only_for_this_server(self, monkeypatch):
        # /health answering is not the add-in being back: another server holding the port answers
        # it too - the state health_gate hard-exits the run on - and reading that as the restart
        # would bank a covered row for a reload nobody observed.
        monkeypatch.setattr(urllib.request, "urlopen",
                            _urlopen({"server": tool_verify.SERVER_NAME, "version": "t"}))
        assert tool_verify._server_answers() is True
        monkeypatch.setattr(urllib.request, "urlopen",
                            _urlopen({"server": "Autodesk Fusion 360"}))
        assert tool_verify._server_answers() is False

    def test_a_probe_that_cannot_be_read_answers_not_up(self, monkeypatch):
        # connection-refused is the state the down-watch waits FOR. Reading a failed probe as 'up'
        # would leave that watch unable to fire, and the beat unable to record anything, silently.
        monkeypatch.setattr(urllib.request, "urlopen",
                            _urlopen(raises=OSError("connection refused")))
        assert tool_verify._server_answers() is False

    def test_poll_health_reads_the_stubbed_probe_and_gives_up_on_its_budget(self, monkeypatch):
        # the facade site: _poll_health must read the probe a consumer stubs ON tool_verify, and a
        # budget that runs out is False - never the state it was waiting for.
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        monkeypatch.setattr(tool_verify, "_server_answers", _health(True, True, False))
        assert tool_verify._poll_health(False, 3) is True
        monkeypatch.setattr(tool_verify, "_server_answers", _health(True))
        assert tool_verify._poll_health(False, 3) is False

    def test_run_fires_the_beat_after_every_act(self, monkeypatch):
        # the call SITE: the beat runs once the acts are done (it restarts the server, so nothing
        # can follow it) and its covered row reaches the ledger.
        ledger = {}

        def fake_write(rows, version, date, src_hash, notes=None, act_modes=None):
            ledger.update(rows)
            return "VERIFIED_TOOLS.md"

        seen = []
        wire = _reload_wire()

        def call(tool, args):
            seen.append(tool)
            return (False, {"n": 1}) if tool == "a_get" else wire(tool, args)

        monkeypatch.setattr(tool_verify, "call", call)
        monkeypatch.setattr(tool_verify, "_server_answers", _health(True, False, True))
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        monkeypatch.setattr(tool_verify, "health_gate", lambda: {"server": "ok", "version": "t"})
        monkeypatch.setattr(tool_verify, "registered_tools",
                            lambda: ["a_get", "sys_reload_addin"])
        monkeypatch.setattr(tool_verify, "source_hash", lambda *a, **k: "0" * 64)
        monkeypatch.setattr(tool_verify, "write_verified", fake_write)
        monkeypatch.setattr(tool_verify, "POLL_AFTER", {})
        monkeypatch.setattr(tool_verify, "EXCLUDED", {"sys_reload_addin": "not confirmed"})
        monkeypatch.setattr(tool_verify, "STORY", {})
        monkeypatch.setattr(tool_verify, "ACTS",
                            [("ACT T", None, [("a_get", {}, lambda p: p["n"] == 1, None)], None)])

        assert tool_verify.run(write_json=False) == 0
        assert ledger == {"a_get": "covered", "sys_reload_addin": "covered"}
        assert seen == ["a_get", "sys_reload_addin", "sys_find_tool"]

    def test_an_unconfirmed_beat_leaves_the_skipped_row_standing(self, monkeypatch):
        # the other half of the same site: the run stays green and the receipt keeps the tool's
        # EXCLUDED row, so an unlanded reconnect never reads as coverage.
        ledger = {}

        def fake_write(rows, version, date, src_hash, notes=None, act_modes=None):
            ledger.update(rows)
            return "VERIFIED_TOOLS.md"

        wire = _reload_wire()
        monkeypatch.setattr(tool_verify, "call",
                            lambda tool, args: ((False, {"n": 1}) if tool == "a_get"
                                                else wire(tool, args)))
        monkeypatch.setattr(tool_verify, "_server_answers", _health(True))   # never goes down
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        monkeypatch.setattr(tool_verify, "health_gate", lambda: {"server": "ok", "version": "t"})
        monkeypatch.setattr(tool_verify, "registered_tools",
                            lambda: ["a_get", "sys_reload_addin"])
        monkeypatch.setattr(tool_verify, "source_hash", lambda *a, **k: "0" * 64)
        monkeypatch.setattr(tool_verify, "write_verified", fake_write)
        monkeypatch.setattr(tool_verify, "POLL_AFTER", {})
        monkeypatch.setattr(tool_verify, "EXCLUDED", {"sys_reload_addin": "not confirmed"})
        monkeypatch.setattr(tool_verify, "STORY", {})
        monkeypatch.setattr(tool_verify, "ACTS",
                            [("ACT T", None, [("a_get", {}, lambda p: p["n"] == 1, None)], None)])

        assert tool_verify.run(write_json=False) == 0
        assert ledger == {"a_get": "covered", "sys_reload_addin": "skipped: not confirmed"}


class TestActSelection:
    """--acts walks a SLICE of the story against the document a prior --keep-open run left open."""

    _ACTS = [("ACT 0 - OVERTURE", None, [], []),
             ("ACT 10a - CAM: JOB", None, [], []),
             ("ACT 10b - CAM: DELIVERABLES", None, [], []),
             ("ACT 10b2 - CAM: COMPONENT SCOPE", None, [], []),
             ("FINALE", None, [], [])]

    def test_one_selector_picks_exactly_its_act(self):
        assert tool_verify.select_acts(self._ACTS, "ACT 10a") == ["ACT 10a - CAM: JOB"]
        # a selector is a name prefix ending at a WORD BREAK, so 10b is not also 10b2 - a plain
        # startswith would select both and silently run an act nobody asked for.
        assert tool_verify.select_acts(self._ACTS, "ACT 10b") == ["ACT 10b - CAM: DELIVERABLES"]

    def test_a_comma_list_comes_back_in_acts_order(self):
        assert tool_verify.select_acts(self._ACTS, "FINALE, ACT 0") == ["ACT 0 - OVERTURE", "FINALE"]

    def test_a_range_spans_every_act_between_its_ends(self):
        assert tool_verify.select_acts(self._ACTS, "ACT 10a..ACT 10b2") == [
            "ACT 10a - CAM: JOB", "ACT 10b - CAM: DELIVERABLES", "ACT 10b2 - CAM: COMPONENT SCOPE"]

    def test_an_unknown_selector_and_an_empty_selection_are_refused_naming_the_acts(self):
        with pytest.raises(ValueError) as unknown:
            tool_verify.select_acts(self._ACTS, "ACT 99")
        assert "ACT 99" in str(unknown.value) and "ACT 10b2 - CAM: COMPONENT SCOPE" in str(unknown.value)
        with pytest.raises(ValueError) as empty:
            tool_verify.select_acts(self._ACTS, " ")
        assert "selects no act" in str(empty.value)

    # ACT 0 saves the ctx key ACT 10b's arguments recall, so a slice that skips it lands blocked.
    _WALK = [("ACT 0 - OVERTURE", None, [("a_get", {}, "ok", ("k", lambda p: p["n"]))], []),
             ("ACT 10a - CAM: JOB", None, [("b_get", {}, "ok", None)], []),
             ("ACT 10b - CAM: DELIVERABLES", None,
              [("c_get", lambda ctx: {"x": ctx["k"]}, "ok", None)], []),
             ("FINALE", None, [("doc_close", {}, "ok", None)], [])]

    @staticmethod
    def _run(monkeypatch, tools, **kw):
        """run() over the _WALK program on a stubbed wire -> (tools the wire saw, receipt written?,
        exit code)."""
        seen, wrote = [], []
        monkeypatch.setattr(tool_verify, "call",
                            lambda tool, args: (seen.append(tool), (False, {"n": 1}))[1])
        monkeypatch.setattr(tool_verify.time, "sleep", lambda s: None)
        monkeypatch.setattr(tool_verify, "health_gate", lambda: {"server": "ok", "version": "t"})
        monkeypatch.setattr(tool_verify, "registered_tools", lambda: sorted(tools))
        monkeypatch.setattr(tool_verify, "source_hash", lambda *a, **k: "0" * 64)
        monkeypatch.setattr(tool_verify, "write_verified",
                            lambda *a, **k: wrote.append(True) or "VERIFIED_TOOLS.md")
        monkeypatch.setattr(tool_verify, "reload_smoke",
                            lambda rows, notes, valued=None, **kw2: None)
        monkeypatch.setattr(tool_verify, "POLL_AFTER", {})
        monkeypatch.setattr(tool_verify, "EXCLUDED", {})
        monkeypatch.setattr(tool_verify, "STORY", {})
        monkeypatch.setattr(tool_verify, "ACT_NEEDS", {})
        monkeypatch.setattr(tool_verify, "ACTS", TestActSelection._WALK)
        return seen, wrote, tool_verify.run(write_json=False, **kw)

    def test_the_runner_walks_only_the_selected_acts(self, monkeypatch, capsys):
        seen, _wrote, code = self._run(monkeypatch, ["a_get", "b_get", "c_get", "doc_close"],
                                       acts_spec="ACT 10a")
        assert seen == ["b_get"] and code == 0
        # the acts that did not run are named once, up front
        out = capsys.readouterr().out
        assert "3 act(s) not run - ACT 0 - OVERTURE, ACT 10b - CAM: DELIVERABLES, FINALE" in out

    def test_a_recall_an_unrun_act_would_have_saved_lands_the_step_blocked(self, monkeypatch,
                                                                          capsys):
        seen, _wrote, code = self._run(monkeypatch, ["c_get"], acts_spec="ACT 10b")
        assert seen == [] and code == 1
        assert "blocked" in capsys.readouterr().out

    def test_a_partial_run_writes_no_receipt_and_says_so_in_its_last_line(self, monkeypatch,
                                                                         capsys):
        _seen, wrote, code = self._run(monkeypatch, ["b_get"], acts_spec="ACT 10a")
        assert not wrote and code == 0
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert lines[-1] == ("partial run (--acts ACT 10a): receipt not written; the story "
                             "document is left open")

    def test_the_document_is_left_open_unless_the_finale_is_selected_and_free_to_close_it(
            self, monkeypatch, capsys):
        _seen, _wrote, _code = self._run(monkeypatch, ["b_get", "doc_close"],
                                         acts_spec="ACT 10a,FINALE")
        assert capsys.readouterr().out.splitlines()[-1] == (
            "partial run (--acts ACT 10a,FINALE): receipt not written")
        # --keep-open drops the FINALE's doc_close in a partial run exactly as in a full one
        seen, _wrote, _code = self._run(monkeypatch, ["doc_close"], acts_spec="FINALE",
                                        keep_open=True)
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert seen == [] and lines[-1].endswith("; the story document is left open")
