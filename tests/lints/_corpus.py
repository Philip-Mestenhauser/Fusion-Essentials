# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read each corpus file once, parse it once - the shared I/O behind the lints in this directory.

Most lints here scan the SAME corpus (commands/mcpServer/ + tests/), and several parse every one of
its ~420 modules with ast.parse. That reading and parsing is identical work whoever asks for it, so
it lives here memoized; the rules, the tables and the assertions stay in each lint.

The cache is keyed by path and lives for ONE pytest process. That is sound because the lints only
READ the corpus - nothing rewrites a file mid-run, so a cached text or tree cannot go stale against
its file. Two consequences to respect:

  - Never reuse this from a watch-mode or otherwise long-lived process that outlives an edit.
  - A test that WRITES a file and then scans it must write a path it scans once (a fresh tmp_path
    file per case), never rewrite one it already scanned through here.

``tree()`` hands out ONE AST object per file, shared by every caller, so a consumer must walk it
READ-ONLY: a mutated node would be seen by every later reader.
"""

import ast
import os
from functools import lru_cache
from pathlib import Path


def text(path):
    """The file's source, decoded UTF-8. Accepts a str or a Path (the same file either way)."""
    return _text(os.fspath(path))


def tree(path):
    """The file's parsed AST - shared, so walk it read-only (see the module docstring)."""
    return _tree(os.fspath(path))


def py_files(root):
    """Every .py file under `root` as Paths, __pycache__ excluded. A tuple, so a caller sorting or
    filtering it cannot disturb the cached listing."""
    return _py_files(os.fspath(root))


@lru_cache(maxsize=None)
def _text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@lru_cache(maxsize=None)
def _tree(path):
    return ast.parse(_text(path), filename=path)


@lru_cache(maxsize=None)
def _py_files(root):
    return tuple(p for p in Path(root).rglob("*.py") if "__pycache__" not in p.parts)
