# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The measurement registry's shape: every ROW composes into a script Fusion can run.

A row's body is source text that only ever executes inside live Fusion, so a syntax error in one
reaches the run as an ERROR row hours later - and a duplicate id silently overwrites a claim's
ledger cell. Both are decidable offline, against the same _compose the runner calls. A row that
touches the operator's own cloud files or session state is read here as data for the same reason.
"""

import ast
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live"))
import measure_api  # noqa: E402


def _run_body(row_id):
    """One row's own composed statements - the `run` function _compose builds around its body, so
    the shared template's helpers are outside what these tests read."""
    row = next(r for r in measure_api.ROWS if r["id"] == row_id)
    tree = ast.parse(measure_api._compose(row))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "run")


class TestRowRegistry:
    def test_every_row_composes_into_compilable_python(self):
        broken = []
        for row in measure_api.ROWS:
            try:
                compile(measure_api._compose(row), row["id"], "exec")
            except SyntaxError as e:
                broken.append(f"{row['id']}: line {e.lineno}: {e.msg}")
        assert not broken, (
            "measurement row bodies that do not compile - they would reach live Fusion as ERROR "
            "rows:\n  " + "\n  ".join(broken))

    def test_row_ids_are_unique(self):
        seen, dupes = set(), []
        for row in measure_api.ROWS:
            if row["id"] in seen:
                dupes.append(row["id"])
            seen.add(row["id"])
        assert not dupes, "duplicate measurement row ids: " + ", ".join(sorted(set(dupes)))

    def test_every_row_carries_a_claim_and_an_encoding(self):
        # the two columns write_ledger publishes - a row missing either lands a blank ledger cell
        thin = [row["id"] for row in measure_api.ROWS
                if not str(row.get("claim", "")).strip()
                or not str(row.get("encoded_in", "")).strip()]
        assert not thin, "rows with an empty claim or encoded_in: " + ", ".join(thin)


class TestTheDrawingRowDeletesOnlyWhatItSaved:
    """The row saves a file into the operator's OWN cloud project, so a delete keyed on the shared
    name would take whatever else answers to that name - another run's file, or the operator's."""

    ROW = "shape-dump-drawing-world"

    def test_the_only_delete_is_keyed_on_the_id_this_run_saved(self):
        run = _run_body(self.ROW)
        calls = [n for n in ast.walk(run) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "deleteMe"]
        assert len(calls) == 1, [ast.unparse(c) for c in calls]
        target = calls[0].func.value
        assert isinstance(target, ast.Name), ast.unparse(calls[0])
        assigned = [ast.unparse(a.value) for a in ast.walk(run) if isinstance(a, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == target.id for t in a.targets)]
        assert assigned == ["file_by_id(urn)"], assigned

    def test_the_lookup_matches_the_cloud_id_and_never_the_name(self):
        fn = next(n for n in ast.walk(_run_body(self.ROW))
                  if isinstance(n, ast.FunctionDef) and n.name == "file_by_id")
        read = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        assert "id" in read and "name" not in read, sorted(read)

    def test_an_id_that_never_settled_deletes_nothing(self):
        # THE loop that deletes, not any loop in the row: the gate has to sit on the one whose body
        # calls deleteMe, or a settle pump wearing the same gate would pass this.
        deleting = [w for w in ast.walk(_run_body(self.ROW)) if isinstance(w, ast.While)
                    and "deleteMe" in ast.unparse(w)]
        assert len(deleting) == 1, [ast.unparse(w.test) for w in deleting]
        assert "urn is not None" in ast.unparse(deleting[0].test)


class TestTheStickyStlRowRestoresTheSession:
    """The row ASSIGNS the sticky STL unit, which every later export in that Fusion session
    inherits - so a leg that raises must not leave the session on inches."""

    ROW = "stl-export-unittype-is-sticky-session-state"

    def _restoring_try(self):
        tries = [t for t in ast.walk(_run_body(self.ROW)) if isinstance(t, ast.Try)
                 and any("restore-mm" in ast.unparse(n) for n in t.finalbody)]
        assert len(tries) == 1, "the restore is not in exactly one finally"
        return tries[0]

    def test_the_legs_that_move_the_unit_sit_inside_the_restoring_try(self):
        # A finally around nothing restores nothing: the assigning legs have to be in its try.
        body = "\n".join(ast.unparse(n) for n in self._restoring_try().body)
        assert "set-inch" in body and "set-cm" in body, body

    def test_the_files_the_legs_wrote_are_removed_in_the_same_finally(self):
        # Over the list the legs COLLECTED, not any list: `for p in []: os.remove(p)` removes
        # nothing while reading like a cleanup.
        loops = [n for n in self._restoring_try().finalbody if isinstance(n, ast.For)]
        assert len(loops) == 1, [ast.unparse(n) for n in self._restoring_try().finalbody]
        assert ast.unparse(loops[0].iter) == "written"
        assert "os.remove(p)" in ast.unparse(loops[0])
        leg = next(n for n in ast.walk(_run_body(self.ROW))
                   if isinstance(n, ast.FunctionDef) and n.name == "leg")
        assert "written.append(p)" in ast.unparse(leg)
