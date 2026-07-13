"""Generate a human-readable behavior spec from the test suite.

The test names ARE the spec: each ``test_<thing>`` documents one contract the
corresponding tool promises. This script collects them (no execution needed) and
renders TEST_SPEC.md — a per-tool checklist of behaviors that are pinned by a test.

Run from the repo root:

    py -3 tests/gen_spec.py        # writes tests/generated/TEST_SPEC.md
    py -3 tests/gen_spec.py --check  # exit 1 if TEST_SPEC.md is stale

Use it to review scope ("what behaviors do I actually guarantee?") and to spot
gaps ("this tool has a test file but nothing covers the error path").
"""

import argparse
import ast
import os
import re
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.join(TESTS_DIR, "generated", "TEST_SPEC.md")


def _humanize(test_name: str) -> str:
    """test_picks_largest_body_by_volume -> 'picks largest body by volume'."""
    return re.sub(r"^test_", "", test_name).replace("_", " ")


def _module_doc_summary(tree: ast.Module) -> str:
    """First sentence of the test module's docstring, if any."""
    doc = ast.get_docstring(tree) or ""
    first = doc.strip().split("\n\n")[0].replace("\n", " ").strip()
    return first


def collect():
    """Return {test_file: (summary, [(group, behavior), ...])} for every test file.

    Recurses TESTS_DIR - test files live in tests/unit/ and tests/lints/ (and none under
    tests/live/, which holds Fusion-driven scripts, not test_*.py). Keyed by basename (unique),
    ordered alphabetically so the spec groups by tool regardless of bucket."""
    paths = []
    for root, _dirs, files in os.walk(TESTS_DIR):
        for fname in files:
            if fname.startswith("test_") and fname.endswith(".py"):
                paths.append(os.path.join(root, fname))

    out = {}
    for path in sorted(paths, key=os.path.basename):
        fname = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=fname)

        behaviors = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                group = re.sub(r"^Test", "", node.name)
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                        behaviors.append((group, _humanize(item.name)))
            elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                # module-level test function (not in a class)
                if not any(node in ast.walk(c) for c in tree.body if isinstance(c, ast.ClassDef)):
                    behaviors.append(("", _humanize(node.name)))
        out[fname] = (_module_doc_summary(tree), behaviors)
    return out


def render(data) -> str:
    lines = [
        "# Behavior Spec (generated)",
        "",
        "_Auto-generated from the test suite by `tests/gen_spec.py`. Do not edit by",
        "hand — every line below is pinned by a passing test. Re-run the generator",
        "after changing tests._",
        "",
        f"**Tools with a test file:** {len(data)}  |  "
        f"**Behaviors pinned:** {sum(len(b) for _, b in data.values())}",
        "",
    ]
    for fname, (summary, behaviors) in data.items():
        tool = re.sub(r"^test_|\.py$", "", fname)
        lines.append(f"## `{tool}`")
        if summary:
            lines.append("")
            lines.append(f"> {summary}")
        lines.append("")
        current_group = None
        for group, behavior in behaviors:
            if group != current_group:
                if group:
                    lines.append(f"**{group}**")
                current_group = group
            lines.append(f"- {behavior}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if TEST_SPEC.md is out of date (does not write).")
    args = parser.parse_args()

    rendered = render(collect())

    if args.check:
        existing = ""
        if os.path.exists(SPEC_PATH):
            with open(SPEC_PATH, encoding="utf-8") as fh:
                existing = fh.read()
        if existing.strip() != rendered.strip():
            print("TEST_SPEC.md is stale — run `py -3 tests/gen_spec.py` and commit.", file=sys.stderr)
            sys.exit(1)
        print("TEST_SPEC.md is up to date.")
        return

    with open(SPEC_PATH, "w", encoding="utf-8") as fh:
        fh.write(rendered + "\n")
    print(f"Wrote {SPEC_PATH} ({sum(len(b) for _, b in collect().values())} behaviors).")


if __name__ == "__main__":
    main()
