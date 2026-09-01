# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Generate the Claude skill from the CANONICAL guidance JSON.

``commands/mcpServer/guidance/parametric_cad_design.json`` is the one authored source of CAD
design practice. A rule carries an ``id``, its ``section``, the ``scenarios`` it applies to, and
``when`` / ``do`` / ``except`` / ``prove`` - the proof steps naming the tool each claim is read
back through. This script validates that data against the LIVE tool registry and renders
``.claude/skills/parametric-cad-design/SKILL.md`` from it, so the client skill cannot state
anything the canonical data does not, and a proof step cannot name a tool the server does not
register.

The BODY is rendered by the shipped package (``render.body``), which is the same text the server
serves over ``resources/read``; this script adds only the frontmatter a skill loader needs. So the
skill and the resource are one render, not two that agree today.

The structural limits are here, not in a lint: the kernel is capped at five rules and 300 rendered
words, the whole skill at 750 rendered words excluding frontmatter, and a conditional playbook
section carries at most one example. Validation is authoring-time gating - it decides what may be
committed, never what the server answers - so it lives with the generator. Whether a rule's wording
is honest is a review judgment; this script checks shape, routing, and size.

Run from the repo root:

    py -3 tests/gen_guidance.py          # writes .claude/skills/parametric-cad-design/SKILL.md
    py -3 tests/gen_guidance.py --check  # exit 1 if that skill is stale or the JSON is invalid
"""

import argparse
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# gen_manifest owns the registry walk (collect()) and installs the mocked adsk at its module top;
# reuse it rather than rolling a second walk that could disagree about what is registered. It also
# puts commands/ on sys.path, which is what makes the shipped guidance package importable below.
import gen_manifest  # noqa: E402

# The shipped package renders the body and declares the section order - what holds everywhere, then
# the build in the order it happens. A document declaring anything else is rejected below rather
# than reordered, so the skill is a function of the file alone; importing the render (instead of
# holding a second copy) is what keeps the skill and the served resource one text.
from mcpServer.guidance.loader import SECTION_IDS  # noqa: E402
from mcpServer.guidance.render import SAFETY_INVARIANT, body, section_text  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDANCE_PATH = os.path.join(REPO_ROOT, "commands", "mcpServer", "guidance",
                             "parametric_cad_design.json")
SKILL_PATH = os.path.join(REPO_ROOT, ".claude", "skills", "parametric-cad-design", "SKILL.md")

KERNEL = "kernel"

RULE_FIELDS = ("id", "section", "scenarios", "when", "do", "except", "prove")

KERNEL_MAX_RULES = 5
KERNEL_MAX_WORDS = 300
SKILL_MAX_WORDS = 750
MAX_EXAMPLES_PER_SECTION = 1

_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_WRAP_WIDTH = 96


# ── reading the canonical data ────────────────────────────────────────────────

def load(path=GUIDANCE_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rules(doc):
    """Every rule, in the document's own section-then-authored order."""
    return [r for sec in (doc.get("sections") or []) for r in (sec.get("rules") or [])]


def scenario_ids(doc):
    """The closed scenario enum: the cases a rule may declare it applies to."""
    return list(doc.get("scenarios") or [])


def rules_for(doc, scenario):
    """The rules that DECLARE `scenario` - the routing a client filters by. A rule reaches a
    scenario only by naming it, so an inapplicable rule is one that named a case it does not fit,
    which is a data defect, not something to infer from the prose."""
    return [r for r in rules(doc) if scenario in (r.get("scenarios") or [])]


# ── rendering: the frontmatter this script adds to the shipped render ─────────

def word_count(text):
    """Rendered words - what the size limits are stated in."""
    return len(text.split())


def frontmatter(doc):
    """The client loader's own header: the name it is invoked by and the description telling an
    agent WHEN to reach for it. Excluded from the word limit - it is not guidance."""
    lines = ["---", f"name: {doc['name']}", "description: >-"]
    lines += textwrap.wrap(doc["description"], width=_WRAP_WIDTH,
                           initial_indent="  ", subsequent_indent="  ")
    lines.append("---")
    return "\n".join(lines) + "\n"


def render(doc):
    return frontmatter(doc) + "\n" + body(doc)


# ── validation ────────────────────────────────────────────────────────────────

def registered_tool_names():
    """Every tool name the live registry carries - the inventory a prove step must name."""
    return frozenset(t["name"] for t in gen_manifest.collect()["tools"])


def _non_ascii_problems(doc):
    """Every string carrying a character that would cross the wire as a \\uXXXX escape."""
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, str):
            bad = [hex(ord(c)) for c in node if ord(c) > 127]
            if bad:
                problems.append(f"{path} carries non-ASCII {bad} - use a plain-ASCII spelling "
                                "(' - ', '...', '->')")

    walk(doc, "guidance")
    return problems


def _rule_problems(rule, section_id, known_scenarios, tool_names):
    problems = []
    rule_id = rule.get("id")
    label = f"rule '{rule_id}'" if isinstance(rule_id, str) else f"a rule in section '{section_id}'"

    for field in RULE_FIELDS:
        if field not in rule:
            problems.append(f"{label} is missing '{field}'")

    if isinstance(rule_id, str) and (not rule_id or set(rule_id) - _ID_CHARS):
        problems.append(f"{label} id must be lowercase letters, digits and '-'")

    for field in ("when", "do", "except"):
        if field in rule and not (isinstance(rule[field], str) and rule[field].strip()):
            problems.append(f"{label} '{field}' must be a non-empty string")

    if "section" in rule and rule["section"] != section_id:
        problems.append(f"{label} declares section '{rule['section']}' but sits in '{section_id}'")

    if "scenarios" in rule:
        declared = rule["scenarios"]
        if not (isinstance(declared, list) and declared):
            problems.append(f"{label} needs a non-empty 'scenarios' list")
        else:
            unknown = sorted(s for s in declared if s not in known_scenarios)
            if unknown:
                problems.append(f"{label} declares unknown scenario(s) {unknown}")
            if declared != sorted(set(declared)):
                problems.append(f"{label} 'scenarios' must be sorted and carry no duplicate")

    if "prove" in rule:
        prove = rule["prove"]
        if not (isinstance(prove, list) and prove):
            problems.append(f"{label} needs at least one 'prove' step")
        else:
            for step in prove:
                if not isinstance(step, dict):
                    problems.append(f"{label} prove step must carry 'tool' and 'observe'")
                    continue
                tool, observe = step.get("tool"), step.get("observe")
                if not (isinstance(observe, str) and observe.strip()):
                    problems.append(f"{label} prove step '{tool}' needs a non-empty 'observe'")
                if tool not in tool_names:
                    problems.append(f"{label} proves through '{tool}', which is not a registered "
                                    "tool - a client told to call it gets nothing")

    kind = rule.get("kind")
    if kind is not None and kind != SAFETY_INVARIANT:
        problems.append(f"{label} declares kind '{kind}' - the only kind is '{SAFETY_INVARIANT}'")

    example = rule.get("example")
    if example is not None and not (isinstance(example, str) and example.strip()):
        problems.append(f"{label} 'example' must be a non-empty string when present")

    return problems


def validate(doc, tool_names):
    """Every problem with `doc`, one line each. An empty list means the data is renderable and
    every claim it makes about the tool surface resolves."""
    problems = []

    for key in ("guidance_id", "name", "title", "description", "summary"):
        if not (isinstance(doc.get(key), str) and doc[key].strip()):
            problems.append(f"top-level '{key}' must be a non-empty string")

    declared = scenario_ids(doc)
    if not declared:
        problems.append("'scenarios' must declare at least one scenario")
    if any(not (isinstance(s, str) and s.strip()) for s in declared):
        problems.append("every entry in 'scenarios' must be a non-empty id string")
    elif declared != sorted(set(declared)):
        problems.append("'scenarios' must be sorted and carry no duplicate, so routing is "
                        "deterministic")
    known = {s for s in declared if isinstance(s, str)}

    sections = doc.get("sections") or []
    present = [s.get("id") for s in sections]
    if present != list(SECTION_IDS):
        problems.append(f"sections must be exactly {list(SECTION_IDS)} in that order, got {present}")

    owner = {}
    routed = set()
    for sec in sections:
        section_id = sec.get("id")
        if not (isinstance(sec.get("title"), str) and sec["title"].strip()):
            problems.append(f"section '{section_id}' needs a title")
        section_rules = sec.get("rules") or []
        if not section_rules:
            problems.append(f"section '{section_id}' has no rules")
        examples = 0
        for rule in section_rules:
            problems += _rule_problems(rule, section_id, known, tool_names)
            rule_id = rule.get("id")
            if isinstance(rule_id, str):
                if rule_id in owner:
                    problems.append(f"duplicate rule id '{rule_id}' - it is a member of both "
                                    f"'{owner[rule_id]}' and '{section_id}'")
                else:
                    owner[rule_id] = section_id
            routed.update(s for s in (rule.get("scenarios") or []) if isinstance(s, str))
            if rule.get("example"):
                examples += 1
        if examples > MAX_EXAMPLES_PER_SECTION:
            problems.append(f"section '{section_id}' carries {examples} examples - at most "
                            f"{MAX_EXAMPLES_PER_SECTION} per conditional playbook")

    for sec in sections:
        if sec.get("id") != KERNEL:
            continue
        kernel_rules = sec.get("rules") or []
        if len(kernel_rules) > KERNEL_MAX_RULES:
            problems.append(f"the kernel holds {len(kernel_rules)} rules - at most "
                            f"{KERNEL_MAX_RULES}")
        for rule in kernel_rules:
            if set(rule.get("scenarios") or []) != known:
                problems.append(f"kernel rule '{rule.get('id')}' must declare every scenario - "
                                "a rule that does not hold everywhere is a playbook rule")
            if rule.get("example"):
                problems.append(f"kernel rule '{rule.get('id')}' carries an example - examples "
                                "belong to the conditional playbooks")

    for scenario in sorted(known - routed):
        problems.append(f"scenario '{scenario}' is declared but no rule applies to it")

    problems += _non_ascii_problems(doc)

    # The size limits are measured on the RENDER, so they are asked only once the data renders.
    if not problems:
        kernel_words = word_count(section_text(doc, KERNEL))
        if kernel_words > KERNEL_MAX_WORDS:
            problems.append(f"the kernel renders {kernel_words} words - at most {KERNEL_MAX_WORDS}")
        skill_words = word_count(body(doc))
        if skill_words > SKILL_MAX_WORDS:
            problems.append(f"the skill renders {skill_words} words excluding frontmatter - at "
                            f"most {SKILL_MAX_WORDS}")

    return problems


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if SKILL.md is stale or the guidance JSON is invalid (no write).")
    args = parser.parse_args()

    doc = load()
    problems = validate(doc, registered_tool_names())
    if problems:
        print("commands/mcpServer/guidance/parametric_cad_design.json is invalid:\n  "
              + "\n  ".join(problems), file=sys.stderr)
        return 1

    rendered = render(doc)
    if args.check:
        existing = ""
        if os.path.exists(SKILL_PATH):
            with open(SKILL_PATH, encoding="utf-8") as fh:
                existing = fh.read()
        if existing != rendered:
            print("Stale - run `py -3 tests/gen_guidance.py` and commit: "
                  ".claude/skills/parametric-cad-design/SKILL.md", file=sys.stderr)
            return 1
        print("SKILL.md is up to date.")
        return 0

    with open(SKILL_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered)
    print(f"Wrote {SKILL_PATH} ({word_count(body(doc))} words, kernel "
          f"{word_count(section_text(doc, KERNEL))}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
