"""Generate docs/tool-wiring.md - the BREADCRUMB WIRING map, from the live registry + tool source.

Every tool speaks to the agent through three wire surfaces: its DESCRIPTION (always present, the
manual), and its runtime NOTE / ERROR strings (situational, the results). When one of those strings
names another tool, that is a BREADCRUMB - a tip steering the agent to a next step. This script reads
those references out of the code and renders one reviewable map, so the team can engineer the wiring
deliberately: see what tips we give in what cases, where breadcrumbs are missing (orphans), where a
guard is duplicated across the surface (a shared-helper candidate), and where a tip points at a name
that no longer exists (a dead reference).

It is the wiring counterpart to MANIFEST (what tools exist) and SPEC (what they're pinned to do):

    py -3 tests/gen_wiring.py          # writes docs/tool-wiring.md
    py -3 tests/gen_wiring.py --check  # exit 1 if docs/tool-wiring.md is stale (for CI)

The reference edges are read from the CODE (AST), attributed per tool via its handler function, split
by SURFACE (description = the manual, note/error = the situational tip). sys_capability_map is excluded
from the "workflow" view - it names every family's entry tool by design, so it is a catalog, not a
breadcrumb.
"""

import argparse
import ast
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conftest  # noqa: E402
conftest.install_mock_adsk()
from conftest import load_tool, TOOLS_DIR, COMMANDS_DIR  # noqa: E402
if COMMANDS_DIR not in sys.path:
    sys.path.insert(0, COMMANDS_DIR)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(os.path.dirname(TESTS_DIR), "docs")
WIRING_PATH = os.path.join(DOCS_DIR, "tool-wiring.md")

_FAMILY_PREFIXES = [
    ("model_", "model"), ("surface_", "surface"), ("mesh_", "mesh"), ("sketch_", "sketch"),
    ("cam_", "cam"), ("assembly_", "assembly"), ("joint_", "joint"), ("design_", "design"),
    ("doc_", "doc"), ("data_", "data"), ("param_", "param"), ("view_", "view"),
    ("find_", "find"), ("workspace_", "workspace"), ("appearance_", "appearance"),
    ("save_", "save"), ("sys_", "sys"),
]
_DOMAINS = sorted({lab for _, lab in _FAMILY_PREFIXES})
# sys_capability_map names every family's entry tool by design - a catalog, not a workflow tip.
_CATALOG = {"sys_capability_map", "sys_find_tool"}


def _family(name):
    for pre, lab in _FAMILY_PREFIXES:
        if name.startswith(pre):
            return lab
    return "other"


def _tool_modules():
    return [fn[:-3] for fn in sorted(os.listdir(TOOLS_DIR))
            if fn.endswith(".py") and not fn.startswith("_") and fn != "__init__.py"]


# ── string extraction (AST): non-docstring literals, and note/error literals specifically ───────────

def _non_doc_strings(node):
    doc_ids = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_ids.add(id(body[0].value))
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids]


def _note_error_strings(node):
    """Strings the SERVER REPORTS at runtime: error(...) args, {'note'/'warning'/'readiness': X} values,
    and note=/result['note']= assignments."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "error" and n.args:
            out += _str_parts(n.args[0])
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value in ("note", "warning", "readiness", "hint"):
                    out += _str_parts(v)
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and t.slice.value in ("note", "warning"):
                    out += _str_parts(n.value)
                if isinstance(t, ast.Name) and t.id in ("note", "_note"):
                    out += _str_parts(n.value)
    return out


def _str_parts(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _module_functions(mod_name):
    src = open(os.path.join(TOOLS_DIR, mod_name + ".py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fns = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    top_desc = [s for node in tree.body
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for s in _non_doc_strings(node)]
    return fns, top_desc


def _attribute(mod_name, tool_name):
    """(note_string_LIST, extra_desc_strings) belonging to a tool - its handler fn (matched by name
    stem) plus module-level DESC constants."""
    fns, top_desc = _module_functions(mod_name)
    stem = tool_name.split("_", 1)[1] if "_" in tool_name else tool_name
    stem_parts = stem.split("_")
    note = []
    for fn_name, node in fns.items():
        fl = fn_name.lower()
        if stem in fl or any(p in fl for p in stem_parts):
            note += _note_error_strings(node)
    return note, "\n".join(top_desc)


# Smell heuristics for the guidance audit. Each maps a regex to a one-word tag surfaced next to the
# string so a reviewer can scan for rot instead of reading 1000 lines.
_SMELLS = [
    ("war-story", re.compile(r"\b(used to|previously|historically|once advised|copy-pasted|epidemic|"
                             r"war stor|legacy behaviou?r|we (?:used|had))\b", re.I)),
    ("cause-guess", re.compile(r"\b(almost always|probably|might be|likely (?:owned|because)|"
                               r"is likely|presumably|i think|we think|seems)\b", re.I)),
    ("hedge", re.compile(r"\b(should (?:probably|maybe)|may or may not|not sure|possibly)\b", re.I)),
]


def _smells(text):
    return sorted({tag for tag, rx in _SMELLS if rx.search(text)})


# ── collect: per-tool {desc, note_text, family, readonly} + reference edges by surface ──────────────

def collect():
    from mcpServer.mcp_primitives import registry
    records = {}
    input_names = set()
    for mod_name in _tool_modules():
        mod = load_tool(mod_name)
        rt = getattr(mod, "register_tool", None)
        if not callable(rt):
            continue
        registry.reset_registry()
        rt()
        for item in registry.get_tools():
            name = item.get_name()
            d = item.to_dict()
            ann = d.get("annotations") or {}
            props = list((d.get("inputSchema") or {}).get("properties", {}).keys())
            input_names.update(props)
            note_list, top_desc = _attribute(mod_name, name)
            records[name] = {
                "module": mod_name, "family": _family(name),
                "readonly": bool(ann.get("readOnlyHint", False)),
                "description": (d.get("description") or ""),
                "desc": (d.get("description") or "") + "\n" + top_desc,
                "note": "\n".join(note_list),
                "note_list": [s.strip() for s in note_list if len(s.strip()) > 20],
                "inputs": props,
            }
    names = set(records)

    def refs(text, self_name):
        hits = set()
        for b in names:
            if b == self_name or b in _CATALOG:
                continue
            if re.search(r"(?<![\w])" + re.escape(b) + r"(?![\w])", text):
                hits.add(b)
        return hits

    # edges by surface
    desc_edges, note_edges = {}, {}
    for n, r in records.items():
        desc_edges[n] = refs(r["desc"], n)
        note_edges[n] = refs(r["note"], n)

    # dead references (a domain_verb call token, not a real tool, not an input param)
    ghost_re = re.compile(r"\b((?:" + "|".join(_DOMAINS) + r")_[a-z][a-z_]*)\(")
    ghosts = defaultdict(set)
    for n, r in records.items():
        for tok in ghost_re.findall(r["desc"] + "\n" + r["note"]):
            if tok not in names and tok not in input_names:
                ghosts[tok].add(n)

    # duplicated guard strings (a note/error literal appearing verbatim in many tools = shared-helper
    # candidate). Count exact note/error strings across the surface.
    guard_counts = Counter()
    guard_where = defaultdict(set)
    for mod_name in {r["module"] for r in records.values()}:
        fns, _ = _module_functions(mod_name)
        for node in fns.values():
            for s in _note_error_strings(node):
                s2 = s.strip()
                if len(s2) > 25:                      # ignore trivial fragments
                    guard_counts[s2] += 1
                    guard_where[s2].add(mod_name)

    return {"records": records, "desc_edges": desc_edges, "note_edges": note_edges,
            "ghosts": dict(ghosts), "guards": guard_counts, "guard_where": guard_where}


# ── render ──────────────────────────────────────────────────────────────────────────────────────────

def _indeg(edges):
    d = Counter()
    for outs in edges.values():
        for dst in outs:
            d[dst] += 1
    return d


def _mermaid(edges, records, title):
    by_fam = defaultdict(list)
    connected = {s for s, o in edges.items() if o} | {d for o in edges.values() for d in o}
    for n in records:
        if n in connected:
            by_fam[records[n]["family"]].append(n)
    lines = [f"### {title}", "", "```mermaid", "flowchart LR"]
    for fam in sorted(by_fam):
        lines.append(f"  subgraph {fam}")
        for n in sorted(by_fam[fam]):
            lines.append(f'    {n}["{n}"]')
        lines.append("  end")
    lines.append("")
    for s in sorted(edges):
        for dst in sorted(edges[s]):
            lines.append(f"  {s} --> {dst}")
    lines.append("```")
    return lines


def render(data):
    records, desc_e, note_e = data["records"], data["desc_edges"], data["note_edges"]
    ghosts, guards, gwhere = data["ghosts"], data["guards"], data["guard_where"]
    desc_in, note_in = _indeg(desc_e), _indeg(note_e)
    combined_in = Counter()
    for n in records:
        combined_in[n] = desc_in[n] + note_in[n]

    L = [
        "# Tool wiring (generated)",
        "",
        "_Auto-generated from the tool source by `tests/gen_wiring.py`. Do not edit by hand._ The",
        "breadcrumb map: where each tool's agent-facing text (its **description** = the manual, and its",
        "runtime **note/error** = the situational tip) names ANOTHER tool, steering the agent onward.",
        "Use it to engineer the wiring: close orphans (a tool nothing leads to), fix dead references,",
        "and factor duplicated guards into shared helpers.",
        "",
        f"**Tools:** {len(records)}  |  **description breadcrumbs:** {sum(len(v) for v in desc_e.values())}"
        f"  |  **note/error breadcrumbs:** {sum(len(v) for v in note_e.values())}",
        "",
        "## Blindspots to engineer",
        "",
        "### Dead references (a tip names something that is not a tool - FIX THESE)",
    ]
    if ghosts:
        for tok in sorted(ghosts):
            L.append(f"- `{tok}(` named by: {', '.join(sorted(ghosts[tok]))}")
    else:
        L.append("- none - every named breadcrumb resolves to a real tool.")

    # orphans: no breadcrumb (desc OR note) leads here
    any_in = {n for n in records if combined_in[n] > 0}
    orphans = sorted(n for n in records if n not in any_in and n not in _CATALOG)
    read_orph = [n for n in orphans if records[n]["readonly"]]
    edit_orph = [n for n in orphans if not records[n]["readonly"]]
    L += ["",
          "### Orphans (no breadcrumb leads here - reachable only via workspace_orient / search)",
          f"**Read/Acquire ({len(read_orph)})** - higher concern, a check-your-work tool nothing points to:",
          "  " + (", ".join(f"`{n}`" for n in read_orph) or "(none)"),
          f"\n**Edit ({len(edit_orph)})** - usually leaf actions, scan for genuine gaps:",
          "  " + (", ".join(f"`{n}`" for n in edit_orph) or "(none)")]

    # duplicated guards - shared-helper candidates. Sort by (-count, string) for a STABLE order (ties
    # in count must not reorder run-to-run, or --check reports false staleness).
    dupes = sorted(((s, c) for s, c in guards.items() if c >= 4), key=lambda sc: (-sc[1], sc[0]))
    L += ["", "### Duplicated guard strings (>=4 copies = factor into a shared _common helper)"]
    if dupes:
        for s, c in dupes:
            short = (s[:88] + "...") if len(s) > 88 else s
            L.append(f"- **{c}x** across {len(gwhere[s])} module(s): \"{short}\"")
    else:
        L.append("- none over the threshold.")

    # hubs - stable order: by (-count, name) so equal-count hubs don't reorder run-to-run.
    L += ["", "### Hubs (most breadcrumbs lead here - the connective tissue)"]
    for n in sorted(records, key=lambda x: (-combined_in[x], x))[:12]:
        L.append(f"- `{n}`  <- {combined_in[n]}  (desc {desc_in[n]}, note {note_in[n]})")

    # ── the full guidance surface, legible in one place ────────────────────────────────────────────
    smell_total = 0
    audit = ["", "## The guidance surface (every note the agent can be told)", "",
             "Every runtime **note/warning** string a tool can return, per tool - the guidance we give,",
             "in one place, to judge: is it there, consistent, teaching a REAL best-practice, or a stale",
             "war story? Smells are auto-tagged: `war-story` (narrates history), `cause-guess` (asserts an",
             "unverified cause), `hedge` (waffles). (Pure error-validation strings - 'must be a number' -",
             "are omitted; this is the GUIDANCE layer, not input validation.)", ""]
    for n in sorted(records):
        notes = records[n]["note_list"]
        if not notes:
            continue
        # de-dup identical strings within a tool, keep order
        seen, uniq = set(), []
        for s in notes:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        tagged = [(s, _smells(s)) for s in uniq]
        if not any(t for _, t in tagged) and all(len(s) < 25 for s, _ in tagged):
            continue
        audit.append(f"### `{n}`")
        for s, tags in tagged:
            flag = ("  " + " ".join(f"**[{t}]**" for t in tags)) if tags else ""
            smell_total += len(tags)
            short = s if len(s) <= 200 else s[:197] + "..."
            audit.append(f"- {short}{flag}")
        audit.append("")
    # headline the smell count up top
    L[9] = L[9] + f"  |  **guidance smells flagged:** {smell_total}"
    L += audit

    # the two graphs
    L += ["", "## The graphs", "",
          "Two surfaces, two graphs. The **description** graph is the manual (what a tool teaches up",
          "front); the **note/error** graph is situational (what the server tells you FROM a result).",
          ""]
    L += _mermaid(desc_e, records, "Description breadcrumbs (the manual)")
    L.append("")
    L += _mermaid(note_e, records, "Note / error breadcrumbs (situational, from results)")
    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if docs/tool-wiring.md is out of date (does not write).")
    args = parser.parse_args()
    rendered = render(collect())
    if args.check:
        existing = open(WIRING_PATH, encoding="utf-8").read() if os.path.exists(WIRING_PATH) else ""
        if existing.strip() != rendered.strip():
            print("tool-wiring.md is stale - run `py -3 tests/gen_wiring.py` and commit.", file=sys.stderr)
            sys.exit(1)
        print("tool-wiring.md is up to date.")
        return
    with open(WIRING_PATH, "w", encoding="utf-8") as fh:
        fh.write(rendered + "\n")
    print(f"Wrote {WIRING_PATH}")


if __name__ == "__main__":
    main()
