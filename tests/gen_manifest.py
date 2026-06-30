"""Generate a tool + input-kind MANIFEST from the LIVE registry.

The registry IS the inventory: every ``tools/<name>.py`` self-registers a Tool with a name, a
write-status, a description, and an input schema. This script imports each tool module (against the
test harness's mocked ``adsk``), pulls those facts off the registered primitives, also collects the
typed ``InputKind`` subclasses from ``_inputs.py``, and renders ``tests/MANIFEST.md`` — one grouped
"what tools + kinds exist" reference.

Why generate instead of hand-write: a static index goes stale the instant a tool is added (a prior
``TOOL_INDEX`` was removed for exactly that). This regenerates from the registry, and ``--check`` fails
CI when the committed ``MANIFEST.md`` drifts — so the inventory cannot lie. It is the GENERATIVE
counterpart to the live ``sys_find_tool`` lookup (same data, batch form): a reviewer or a cold-booting
agent gets the whole map in one file, and the build guarantees it is current.

Run from the repo root:

    py -3 tests/gen_manifest.py          # writes tests/MANIFEST.md
    py -3 tests/gen_manifest.py --check  # exit 1 if MANIFEST.md is stale (for CI)
"""

import argparse
import inspect
import os
import sys

# This script imports the tool modules, which do ``import adsk`` at module top. When run standalone
# (not under pytest) the mocked adsk isn't installed yet — install it the way conftest does, BEFORE any
# tool import. Under pytest, conftest has already installed it; install_mock_adsk is idempotent.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conftest  # noqa: E402
conftest.install_mock_adsk()

from conftest import load_tool, TOOLS_DIR, COMMANDS_DIR  # noqa: E402

# load_tool puts COMMANDS_DIR on sys.path lazily (first call); collect() imports the registry up front,
# so ensure the path now — same seam load_tool uses, so the registry object is the one tools register into.
if COMMANDS_DIR not in sys.path:
    sys.path.insert(0, COMMANDS_DIR)

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MANIFEST.md")

# Tool name prefix -> family label. The order here is the manifest's section order; a tool falls into
# the FIRST prefix it matches (so 'design_get_tree' -> design, 'sys_find_tool' -> sys). A tool matching
# none lands in "other" — which the family test asserts stays empty-or-accounted-for by total count.
_FAMILY_PREFIXES = [
    ("model_", "model"), ("surface_", "surface"), ("mesh_", "mesh"), ("sketch_", "sketch"),
    ("cam_", "cam"), ("assembly_", "assembly"), ("joint_", "joint"), ("design_", "design"),
    ("doc_", "doc"), ("data_", "data"), ("param_", "param"), ("view_", "view"),
    ("find_", "find"), ("workspace_", "workspace"), ("appearance_", "appearance"),
    ("save_", "save"), ("sys_", "sys"),
]


def _first_sentence(text: str, limit: int = 160) -> str:
    """First sentence of a description (mirrors sys_find_tool's summary), trimmed."""
    if not text:
        return ""
    s = text.split(". ")[0].strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def _write_status(item) -> str:
    """'read' / 'write' / 'destructive' from the tool's annotation hints (the same source the server
    reports). Defaults to 'write' if a tool somehow declared nothing (test_write_status enforces one)."""
    d = item.to_dict()
    ann = d.get("annotations") or {}
    if ann.get("readOnlyHint"):
        return "read"
    if ann.get("destructiveHint"):
        return "destructive"
    return "write"


def _tool_modules():
    """Every importable tools/*.py module name (skips the _-prefixed shared helpers and __init__)."""
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if fn.endswith(".py") and not fn.startswith("_") and fn != "__init__.py":
            yield fn[:-3]


def collect():
    """Walk the registry for tool records + the _inputs.py kinds. Returns {'tools': [...], 'kinds': [...]}.

    Each tool module is imported (registering its Item) then read off a FRESH registry so we attribute
    each registered tool to nothing but itself. (A module may register more than one tool — we take all
    of them.)

    ISOLATION: collect() imports EVERY tool module (some for the first time) and churns the shared
    registry singleton. Under pytest that would leak into the next test (a populated registry, or an
    adsk-mock attribute a module reassigns at import time). So we snapshot the registry singleton and
    the shared adsk mock dicts up front and restore BOTH in a finally — collect() leaves the process
    exactly as it found it. (Standalone, restore is a harmless no-op.)"""
    from mcpServer.mcp_primitives import registry

    saved_registry = registry._registry_instance
    saved_adsk = conftest._snapshot_adsk_dicts()
    try:
        return _collect_unguarded(registry)
    finally:
        registry._registry_instance = saved_registry
        conftest._restore_adsk_dicts(saved_adsk)


def _collect_unguarded(registry):
    tools = []
    seen = set()
    for mod_name in _tool_modules():
        mod = load_tool(mod_name)
        rt = getattr(mod, "register_tool", None)
        if not callable(rt):
            continue
        registry.reset_registry()
        rt()
        for item in registry.get_tools():
            name = item.get_name()
            if name in seen:
                continue
            seen.add(name)
            d = item.to_dict()
            props = list((d.get("inputSchema") or {}).get("properties", {}).keys())
            tools.append({
                "name": name,
                "module": mod_name,
                "write": _write_status(item),
                "summary": _first_sentence(d.get("description", "")),
                "inputs": props,
            })
    tools.sort(key=lambda t: t["name"])

    return {"tools": tools, "kinds": _collect_kinds(), "helpers": _collect_helpers()}


def _collect_kinds():
    """The typed InputKind subclasses in _inputs.py (name + first docstring line) — the 'what already
    exists to REFERENCE geometry/profile/body/etc.' half, so the manifest doubles as the anti-drift
    catalog sys_find_tool searches."""
    _inputs = load_tool("_inputs")
    base = getattr(_inputs, "InputKind", None)
    out = []
    if base is None:
        return out
    for cname, cls in inspect.getmembers(_inputs, inspect.isclass):
        if cls is base or not issubclass(cls, base):
            continue
        doc = (inspect.getdoc(cls) or "").strip().split("\n")[0]
        # MAP_HINT (a curated one-liner ON the kind) drives the terse CLAUDE.md map; the docstring's
        # first line is the fuller summary for MANIFEST.md. A blank MAP_HINT shows up blank in the map
        # — the signal to a kind-author to fill it in.
        out.append({"kind": cname, "summary": doc[:160], "hint": getattr(cls, "MAP_HINT", "")})
    out.sort(key=lambda k: k["kind"])
    return out


# The shared `_`-prefixed helper modules to surface in the "reuse before you write" list. Listed
# explicitly (not by globbing tools/_*.py) so a NEW helper is a deliberate one-line add here AND a
# MAP_BLURB on the module — the same self-disclosing pattern as a kind's MAP_HINT. (test conftest's
# load_tool is the importer; _data_common etc. import cleanly under mocked adsk.)
_HELPER_MODULES = ("_common", "_inputs", "_outputs", "_holder", "_data_common")


def _collect_helpers():
    """(module, blurb) for each shared helper — blurb from the module's MAP_BLURB string (a curated
    terse 'what to reuse from here'), falling back to the first docstring line. A helper with neither
    shows up blank — the signal to add a MAP_BLURB."""
    out = []
    for name in _HELPER_MODULES:
        mod = load_tool(name)
        blurb = getattr(mod, "MAP_BLURB", "")
        if not blurb:
            doc = (mod.__doc__ or "").strip().split("\n")[0]
            blurb = doc[:120]
        out.append({"module": name, "blurb": blurb})
    return out


def families(tools):
    """Group tool records by name-prefix family (first match wins); leftovers go to 'other'."""
    groups = {}
    for t in tools:
        label = "other"
        for prefix, name in _FAMILY_PREFIXES:
            if t["name"].startswith(prefix):
                label = name
                break
        groups.setdefault(label, []).append(t)
    return groups


_MARK = {"read": "·", "write": "✎", "destructive": "⚠"}


def render(data) -> str:
    tools = data["tools"]
    kinds = data["kinds"]
    fam = families(tools)

    lines = [
        "# Tool & Input-Kind Manifest (generated)",
        "",
        "_Auto-generated from the live registry by `tests/gen_manifest.py`. Do not edit by hand —"
        " re-run the generator after adding/renaming a tool or kind. `--check` fails CI if this is"
        " stale. This is the batch form of the `sys_find_tool` live lookup: the one place to see what"
        " already exists before building it._",
        "",
        f"**Tools:** {len(tools)}  |  **Input-kinds:** {len(kinds)}  |  "
        "write-status: `·` read · `✎` write · `⚠` destructive",
        "",
        "## Input kinds — reference EXISTING geometry/structure with these (don't hand-roll a name/index)",
        "",
        "Before adding a tool input that points at a face/edge/body/plane/axis/profile/occurrence, use"
        " one of these (extend the kind if it's close). See `CLAUDE.md` 'Input kinds'.",
        "",
        "| Kind | What it references |",
        "|---|---|",
    ]
    for k in kinds:
        lines.append(f"| `{k['kind']}` | {k['summary']} |")
    lines.append("")
    lines.append("## Tools by family")
    lines.append("")

    # Section order = _FAMILY_PREFIXES order, then any leftover families, then 'other'.
    order = [name for _, name in _FAMILY_PREFIXES]
    for extra in sorted(fam):
        if extra not in order:
            order.append(extra)
    for label in order:
        items = fam.get(label)
        if not items:
            continue
        lines.append(f"### {label}")
        lines.append("")
        lines.append("| | Tool | Summary |")
        lines.append("|---|---|---|")
        for t in sorted(items, key=lambda x: x["name"]):
            lines.append(f"| {_MARK[t['write']]} | `{t['name']}` | {t['summary']} |")
        lines.append("")
    return "\n".join(lines)


# ── the token-efficient MAP spliced into CLAUDE.md (for a tool-AUTHOR agent at session start) ──────
#
# MANIFEST.md (above) is the full browse-everything file. The CLAUDE.md map is the OPPOSITE: the shape
# of the space, tiny enough to sit in a code-author agent's context the moment they open the repo —
# the kinds catalog (the invisible abstraction they must not re-invent) + family names+counts (so they
# know roughly where to point sys_find_tool / which MANIFEST.md section to read). Spliced between
# markers so the surrounding hand-written prose is untouched and the block can't rot.

CLAUDE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CLAUDE.md")
_MAP_BEGIN = "<!-- BEGIN GENERATED MAP (py -3 tests/gen_manifest.py) -->"
_MAP_END = "<!-- END GENERATED MAP -->"


def render_claude_map(data) -> str:
    """The compact kinds-catalog + family-skeleton block for CLAUDE.md (between the markers)."""
    kinds = data["kinds"]
    fam = families(data["tools"])
    lines = [_MAP_BEGIN,
             "| Kind | References (use this — don't hand-roll a name/index) |",
             "|---|---|"]
    for k in kinds:
        # escape any '|' in the hint so it can't break the markdown table column.
        hint = (k["hint"] or k["summary"]).replace("|", "\\|")
        lines.append(f"| `{k['kind']}` | {hint} |")
    lines.append("")
    # Families: name(count), in section order — a one-line index of where tools live.
    order = [name for _, name in _FAMILY_PREFIXES]
    for extra in sorted(fam):
        if extra not in order:
            order.append(extra)
    fam_bits = [f"`{name}`({len(fam[name])})" for name in order if fam.get(name)]
    total = sum(len(v) for v in fam.values())
    lines.append(f"**Tool families** ({total} tools — `sys_find_tool <kw>` to search, "
                 "`tests/MANIFEST.md` for the full list): " + " ".join(fam_bits))
    # Shared helpers — the "reuse before you write, grep these" list (generated, so it can't drift).
    helper_bits = "; ".join(f"`{h['module']}` ({h['blurb']})" if h["blurb"] else f"`{h['module']}`"
                            for h in data.get("helpers", []))
    lines.append("")
    lines.append("**Shared helpers** (reuse/extend — grep before writing a resolver): " + helper_bits)
    lines.append(_MAP_END)
    return "\n".join(lines)


def splice_claude(map_block, *, check=False):
    """Replace the marked region of CLAUDE.md with map_block. Returns True if already current.
    With check=True, does not write — just reports whether it would change."""
    with open(CLAUDE_PATH, encoding="utf-8") as fh:
        text = fh.read()
    if _MAP_BEGIN not in text or _MAP_END not in text:
        raise SystemExit(f"CLAUDE.md is missing the map markers {_MAP_BEGIN!r}/{_MAP_END!r} — add them "
                         "where the generated kinds/families block should live.")
    pre, rest = text.split(_MAP_BEGIN, 1)
    _, post = rest.split(_MAP_END, 1)
    new = pre + map_block + post
    if new == text:
        return True
    if not check:
        with open(CLAUDE_PATH, "w", encoding="utf-8") as fh:
            fh.write(new)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if MANIFEST.md or the CLAUDE.md map block is out of date (no write).")
    args = parser.parse_args()

    data = collect()
    rendered = render(data)
    map_block = render_claude_map(data)

    if args.check:
        stale = []
        existing = ""
        if os.path.exists(MANIFEST_PATH):
            with open(MANIFEST_PATH, encoding="utf-8") as fh:
                existing = fh.read()
        if existing.strip() != rendered.strip():
            stale.append("tests/MANIFEST.md")
        if not splice_claude(map_block, check=True):
            stale.append("CLAUDE.md (generated map block)")
        if stale:
            print("Stale — run `py -3 tests/gen_manifest.py` and commit: " + ", ".join(stale),
                  file=sys.stderr)
            sys.exit(1)
        print("MANIFEST.md and the CLAUDE.md map are up to date.")
        return

    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        fh.write(rendered + "\n")
    splice_claude(map_block)
    print(f"Wrote {MANIFEST_PATH} and spliced the CLAUDE.md map "
          f"({len(data['tools'])} tools, {len(data['kinds'])} kinds).")


if __name__ == "__main__":
    main()
