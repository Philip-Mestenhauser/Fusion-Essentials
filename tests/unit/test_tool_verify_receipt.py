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
        assert "1 covered / 0 refusals-only / 1 skipped(reason) / 1 pending" in text
        assert "| appearance_set | covered |" in text
        assert "| doc_open | skipped: opens cloud files |" in text
        assert "| model_loft | PENDING (no step yet) |" in text
