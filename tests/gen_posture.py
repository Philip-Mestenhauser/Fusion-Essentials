# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Generate a PERMISSION-POSTURE artifact from the LIVE registry's own write-status truth.

Every registered tool carries a write= kind (read / write / destructive), and the MCP
``readOnlyHint`` / ``destructiveHint`` annotations derive from it. That single machine-checked
fact is exactly what a Claude Code operator needs to decide which tools may auto-run: a read
changes nothing (safe to auto-allow), a write mutates the model (ask), a destructive write is
hard to reverse (ask/deny), and ``sys_execute_script`` is the arbitrary-code hatch that must
NEVER be auto-approved. This script maps every tool to its posture bucket and emits ready-to-paste
``settings.json`` permission presets for two postures - all GENERATED from the registry, so the
guidance cannot drift from what the server actually exposes.

It is the permission-posture counterpart to ``gen_manifest.py`` (same registry walk, reused here):
the manifest answers "what tools exist"; this answers "which are safe to let run unattended".

Run from the repo root:

    py -3 tests/gen_posture.py          # writes tests/generated/PERMISSION_POSTURE.md
    py -3 tests/gen_posture.py --check  # exit 1 if PERMISSION_POSTURE.md is stale
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# gen_manifest owns the registry walk (collect()); reuse it rather than re-rolling a second walk that
# could drift. Importing it also installs the mocked adsk at its module top, exactly as running
# gen_manifest standalone does - so gen_posture works standalone and under gen_all alike.
import gen_manifest  # noqa: E402

POSTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated", "PERMISSION_POSTURE.md")

# The MCP wire name every Claude Code permission rule uses: mcp__<server>__<tool>. The server is
# registered as "fusion-essentials", so a rule targets e.g. mcp__fusion-essentials__cam_get.
WIRE_PREFIX = "mcp__fusion-essentials__"

# The arbitrary-code hatch: a destructive-kind tool that runs operator-supplied Fusion API scripts.
# It gets its OWN posture row and is denied auto-approval in every preset - no bucket rule may ever
# route it into an allow list (test_permission_posture.py bites on exactly that).
SCRIPT_HATCH = "sys_execute_script"

# Families whose writes touch the CLOUD / document lifecycle (data management, save/open/close/derive)
# rather than local model geometry. The modeling posture routes these to ASK while auto-allowing local
# model writes. Classified by family (the registry's own naming schema), never per-tool by hand.
CLOUD_FAMILIES = ("data", "doc")


def _family(name):
    return name.split("_", 1)[0]


def bucket(tool):
    """The posture bucket for one tool record ({'name','write',...}). The script hatch is its own
    bucket; everything else is its write kind verbatim (read / write / destructive)."""
    if tool["name"] == SCRIPT_HATCH:
        return "script"
    return tool["write"]        # 'read' | 'write' | 'destructive'


def buckets(tools):
    """{bucket -> sorted [tool name]} over every registered tool."""
    out = {"read": [], "write": [], "destructive": [], "script": []}
    for t in tools:
        out[bucket(t)].append(t["name"])
    for names in out.values():
        names.sort()
    return out


def _wire(names):
    return [WIRE_PREFIX + n for n in names]


def build_presets(tools):
    """The generated settings.json presets, as data (name -> {'allow','ask','deny'} of WIRE names).

    Both presets are derived PURELY from each tool's bucket (and, for modeling, its family) - never a
    hand-listed tool. Two invariants hold by construction and are enforced by test_permission_posture.py:
    no destructive-kind tool ever lands in an allow list, and the script hatch is denied everywhere.

      conservative  - auto-allow reads only; writes ask; destructive + the script hatch are denied.
      modeling      - auto-allow reads and LOCAL model writes; cloud/document writes and every
                      destructive write ask; the script hatch is denied.
    """
    b = buckets(tools)
    reads, writes, destructive = b["read"], b["write"], b["destructive"]

    conservative = {
        "allow": _wire(reads),
        "ask": _wire(writes),
        "deny": _wire(destructive + [SCRIPT_HATCH]),
    }

    local_writes = [n for n in writes if _family(n) not in CLOUD_FAMILIES]
    cloud_writes = [n for n in writes if _family(n) in CLOUD_FAMILIES]
    modeling = {
        "allow": _wire(reads + local_writes),
        "ask": _wire(sorted(cloud_writes + destructive)),
        "deny": _wire([SCRIPT_HATCH]),
    }
    return {"conservative": conservative, "modeling": modeling}


_PRESET_BLURB = {
    "conservative": "Auto-allow reads only. Every write asks; destructive writes and the "
                    "arbitrary-code hatch are denied. The safest default.",
    "modeling": "Auto-allow reads and LOCAL model writes (extrude, joint, sketch, ...). "
                "Cloud/document writes and every destructive write ask; the script hatch is denied.",
}


def _preset_block(name, preset):
    """One ```json settings.json block, ready to paste under .claude/settings.json."""
    body = json.dumps({"permissions": preset}, indent=2, ensure_ascii=True)
    return [
        f"### Preset: {name}",
        "",
        _PRESET_BLURB[name],
        "",
        "```json",
        body,
        "```",
        "",
    ]


def render(tools):
    b = buckets(tools)
    presets = build_presets(tools)
    total = len(tools)

    lines = [
        "# Permission Posture (generated)",
        "",
        "_Auto-generated from the live registry by `tests/gen_posture.py`. Do not edit by hand -"
        " re-run the generator after adding/renaming a tool or changing its write= kind. `--check`"
        " (via `tests/gen_all.py --check`) fails the suite if this is stale._",
        "",
        "Every tool declares a write= kind (read / write / destructive); the MCP readOnlyHint /"
        " destructiveHint annotations derive from it. That machine-checked fact decides which tools"
        " are safe to auto-run under Claude Code. This file maps every tool to a posture bucket and"
        " emits ready-to-paste `settings.json` presets. Rules target the MCP wire name"
        f" `{WIRE_PREFIX}<tool>`.",
        "",
        f"**Tools:** {total}  |  read: {len(b['read'])}  |  write: {len(b['write'])}  |  "
        f"destructive: {len(b['destructive'])}  |  script-hatch: {len(b['script'])}",
        "",
        "## Posture buckets",
        "",
        "| Bucket | write= kind | Recommended handling |",
        "|---|---|---|",
        "| read | read | Safe to auto-allow - changes nothing. |",
        "| write | write | Ask - mutates the model or runs an async operation. |",
        "| destructive | destructive | Ask or deny - hard-to-reverse (delete, close, history-discarding). |",
        f"| script-hatch | destructive | NEVER auto-approve - `{SCRIPT_HATCH}` runs arbitrary Fusion API"
        " code. Deny by default. |",
        "",
    ]

    # The script hatch, called out on its own row before the destructive list it also belongs to.
    lines += [
        "## The arbitrary-code hatch (never auto-approved)",
        "",
        f"`{SCRIPT_HATCH}` executes operator-supplied Fusion API scripts - it can do anything any"
        " other tool can, and more. No posture in this file ever places it in an allow list; it is"
        " denied in every preset below. Enable it only for a trusted, supervised session.",
        "",
    ]

    section_order = [
        ("read", "read - safe to auto-allow"),
        ("write", "write - ask"),
        ("destructive", "destructive - ask / deny"),
    ]
    lines.append("## Every tool by bucket")
    lines.append("")
    for key, heading in section_order:
        names = b[key]
        lines.append(f"### {heading} ({len(names)})")
        lines.append("")
        for n in names:
            marker = "  (script hatch - deny)" if n == SCRIPT_HATCH else ""
            lines.append(f"- `{WIRE_PREFIX}{n}`{marker}")
        lines.append("")

    lines += [
        "## Ready-to-paste settings.json presets",
        "",
        "Paste one `permissions` block into `.claude/settings.json` (or merge its arrays into an"
        " existing one). Both presets are generated from the registry, so they stay complete as tools"
        " are added. Anything not listed falls through to Claude Code's default prompt.",
        "",
    ]
    for name in ("conservative", "modeling"):
        lines += _preset_block(name, presets[name])

    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if PERMISSION_POSTURE.md is out of date (no write).")
    args = parser.parse_args()

    data = gen_manifest.collect()
    rendered = render(data["tools"])

    if args.check:
        existing = ""
        if os.path.exists(POSTURE_PATH):
            with open(POSTURE_PATH, encoding="utf-8") as fh:
                existing = fh.read()
        if existing.strip() != rendered.strip():
            print("Stale - run `py -3 tests/gen_posture.py` and commit: "
                  "tests/generated/PERMISSION_POSTURE.md", file=sys.stderr)
            sys.exit(1)
        print("PERMISSION_POSTURE.md is up to date.")
        return

    with open(POSTURE_PATH, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    print(f"Wrote {POSTURE_PATH} ({len(data['tools'])} tools).")


if __name__ == "__main__":
    main()
