# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Render the packaged guidance document as Markdown - one authored render, two consumers.

``resources/read`` serves this text to a client that reads MCP resources, and ``gen_guidance.py``
writes the same text into the checked-in Claude skill below the frontmatter only a skill loader
needs. The render lives here, beside the document it renders, so the served body and the committed
skill cannot say different things. Whether the DATA is fit to render at all - the size caps, the
registry check behind every proof step - is authoring-time gating and stays with the generator.
"""

from . import loader

# The one declared rule kind: a deterministic safety/API rule says so and is marked as one in the
# render; every other rule is strategy.
SAFETY_INVARIANT = "safety_invariant"


def _rule_lines(rule):
    """One rule's lines - its id, the four clauses in reading order, and its example if it has one."""
    head = f"**{rule['id']}**"
    if rule.get("kind") == SAFETY_INVARIANT:
        head += " (safety invariant)"
    prove = "; ".join(f"`{s['tool']}`: {s['observe']}" for s in rule["prove"])
    lines = [head,
             f"When {rule['when']}: {rule['do']}. Except {rule['except']}. Prove {prove}."]
    if rule.get("example"):
        lines.append(f"Example: {rule['example']}.")
    return lines


def section_text(doc, section_id):
    """One section's rendered Markdown, heading included - the unit the kernel size limit measures.

    KeyError when the document carries no section with that id: the ids are a closed set the caller
    already chose from, so a miss is a defect in the data rather than an empty section to render."""
    sec = loader.find_section(doc, section_id)
    if sec is None:
        raise KeyError(section_id)
    lines = [f"## {sec['title']}", ""]
    for rule in (sec.get("rules") or []):
        lines += _rule_lines(rule)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def body(doc):
    """The whole document as Markdown, in the reading order the package declares.

    This is what ``resources/read`` returns, and what the generated skill holds under its
    frontmatter - the frontmatter is a client loader's own header, so it is not part of the body."""
    parts = [f"# {doc['title']}", "", doc["summary"], ""]
    for section_id in loader.SECTION_IDS:
        parts.append(section_text(doc, section_id).rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
