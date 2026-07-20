"""Lint: no BANNED VOCABULARY word appears in any agent-facing wire string.

The owner's rule (2026-07-16): "we use the term container, as humans, when describing a component -
Fusion does not call these things containers, and neither should our codebase." An invented noun on
the wire teaches a connected agent a word Fusion's own UI never uses, so the agent can't map it back
to what it sees. This lint keeps that word (and any future banned term) out of the ONLY strings an
agent reads about a tool: its description, its per-input descriptions, and any module-level
``*_DESCRIPTION`` constant.

Same surfaces as ``test_wire_ascii`` - the live registry (every tool's description + every input
property's description, recursively) and the SOURCE constants - but checking WHICH WORDS are used
rather than that the bytes are ASCII. Comments and internal identifiers are out of scope on purpose:
they never serialize onto the wire.

A term is matched case-insensitively on a word boundary (so "container"/"Container"/"containers" all
trip; "self-contained" does not). To permit a genuinely unavoidable use, add a
``(surface_id, term)`` entry to ``_ALLOWLIST`` with a concrete reason - empty today, because every
current wire string uses Fusion's own vocabulary.
"""

import ast
import os
import re

from conftest import TOOLS_DIR, register_all_tools

# The banned words, each a term Fusion's own UI does not use for the thing our text describes.
BANNED_TERMS = ("container",)

# Per-surface exceptions: {(surface_id, term): reason}. A surface_id is the tool/input path or the
# "<file>:<CONST>" of a description constant. EMPTY today - a new entry needs a real reason a
# downstream agent benefits from, not just to silence the lint.
_ALLOWLIST = {}

# \b...s? so the singular and a trailing-s plural both trip, but an embedded use ("self-contained")
# does not (the leading \b fails inside a word).
_BANNED_RE = re.compile(r"\b(" + "|".join(BANNED_TERMS) + r")s?\b", re.IGNORECASE)


def _banned_hits(text):
    """The distinct banned terms (lowercased) that appear as whole words in `text`."""
    return sorted({m.group(1).lower() for m in _BANNED_RE.finditer(text or "")})


def _report(surface_id, hits):
    """An offender line for each hit not excused by the allowlist, else []."""
    return [f"{surface_id}: uses banned wire word '{term}' - use Fusion's own vocabulary "
            f"(a component occurrence / a parent setup or folder / a folder or project)"
            for term in hits if (surface_id, term) not in _ALLOWLIST]


def _walk_property_descriptions(props, path, out):
    """Recurse a JSON-schema properties dict, collecting (path, description) pairs (nested arrays/objects too)."""
    for name, schema in props.items():
        if not isinstance(schema, dict):
            continue
        desc = schema.get("description")
        if isinstance(desc, str):
            out.append((f"{path}.{name}", desc))
        nested = schema.get("properties")
        if isinstance(nested, dict):
            _walk_property_descriptions(nested, f"{path}.{name}", out)
        items = schema.get("items")
        if isinstance(items, dict) and isinstance(items.get("properties"), dict):
            _walk_property_descriptions(items["properties"], f"{path}.{name}[]", out)


class TestNoBannedVocabularyOnTheWire:
    def test_no_tool_description_uses_a_banned_word(self):
        offenders = []
        for it in register_all_tools():
            d = it.to_dict()
            offenders += _report(f"{d.get('name')} (description)", _banned_hits(d.get("description") or ""))
        assert not offenders, "banned wire word in tool description(s):\n  " + "\n  ".join(offenders)

    def test_no_input_description_uses_a_banned_word(self):
        offenders = []
        for it in register_all_tools():
            d = it.to_dict()
            name = d.get("name")
            props = (d.get("inputSchema") or {}).get("properties", {}) or {}
            found = []
            _walk_property_descriptions(props, name, found)
            for path, desc in found:
                offenders += _report(path, _banned_hits(desc))
        assert not offenders, "banned wire word in input description(s):\n  " + "\n  ".join(offenders)


# A module-level constant whose name ends in DESCRIPTION - the naming convention every tool uses for
# its wire description (TOOL_DESCRIPTION, and per-verb variants on action-dispatched tools).
_DESCRIPTION_NAME = re.compile(r".*DESCRIPTION$")


def _description_constant_strings(path):
    """(constant_name, [string literals]) for every module-level `*_DESCRIPTION = ...` assignment,
    walking the assigned expression so an f-string/`.format()`/`+`-built description is checked piece by piece."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and _DESCRIPTION_NAME.match(target.id):
                strings = [n.value for n in ast.walk(node.value)
                           if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                out.append((target.id, strings))
    return out


class TestNoBannedVocabularyInDescriptionConstants:
    def test_no_description_constant_uses_a_banned_word(self):
        offenders = []
        for fn in sorted(os.listdir(TOOLS_DIR)):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(TOOLS_DIR, fn)
            for const_name, strings in _description_constant_strings(path):
                for s in strings:
                    offenders += _report(f"{fn}:{const_name}", _banned_hits(s))
        assert not offenders, "banned wire word in description constant(s):\n  " + "\n  ".join(offenders)


# The skill files under .claude/skills/ are agent-facing too: an executing agent reads
# SKILL.md/reference.md verbatim, so the same vocabulary rule applies to them. Quoted EXTERNAL
# phrases are exempt - a citation of a published class title or a literal browser-tree name from a
# shipped template is data we report, not vocabulary we chose - and each is stripped (exact,
# case-sensitive) before the scan so only the quoted form passes; our own prose around it is still
# checked. A new entry needs the same bar as _ALLOWLIST: a phrase we QUOTE, never one we coin.
SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(TOOLS_DIR))),
                          ".claude", "skills")
_SKILL_QUOTED_PHRASES = (
    "Templates, Configurations, and Containers",  # the AU2024 class title (MFG3914) - a citation
    "Component Container",   # the AU class's own name for the slot role - kept as an attributed term
    "Joint Origin Container",  # ditto - the class's JOC concept name
    "Fixture Container",     # literal browser-tree names from shipped templates - quoted data
    "Stock Container",
)


def _strip_quoted(text):
    for phrase in _SKILL_QUOTED_PHRASES:
        text = text.replace(phrase, " ")
    return text


class TestNoBannedVocabularyInSkillFiles:
    def test_no_skill_markdown_uses_a_banned_word(self):
        offenders = []
        for root, _dirs, files in os.walk(SKILLS_DIR):
            for fn in sorted(files):
                if not fn.endswith(".md"):
                    continue
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, SKILLS_DIR).replace(os.sep, "/")
                with open(path, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, 1):
                        for term in _banned_hits(_strip_quoted(line)):
                            offenders.append(
                                f"{rel}:{lineno}: uses banned word '{term}' - use Fusion's own "
                                f"vocabulary (a component occurrence / a parent setup or folder / "
                                f"a folder or project), or quote-and-attribute an external term")
        assert not offenders, "banned word in skill file(s):\n  " + "\n  ".join(offenders)


class TestTheLintBites:
    def test_it_fires_on_a_doctored_string(self):
        # A doctored wire string that names the invented noun MUST trip the checker...
        assert _banned_hits("select the container occurrence, not a body inside it") == ["container"]
        assert _banned_hits("the model/stock CONTAINERS the setup selects") == ["container"]
        # ...while Fusion's own vocabulary passes clean, and an embedded use is not a false positive.
        assert _banned_hits("select the component occurrence, not a body inside it") == []
        assert _banned_hits("a self-contained parent setup or folder") == []

    def test_the_skill_scan_bites_and_the_quoted_phrases_pass(self):
        # A doctored skill line with our own use of the noun MUST trip the checker...
        assert _banned_hits(_strip_quoted("insert the part into the model container")) == ["container"]
        # ...a lowercase/uncited variant of an exempt phrase still trips (only the quoted form passes)...
        assert _banned_hits(_strip_quoted("descend the fixture container occurrence")) == ["container"]
        # ...while the quoted external phrases pass clean.
        assert _banned_hits(_strip_quoted(
            "distilled from the AU2024 class Templates, Configurations, and Containers for "
            "Agile Prototype Machining (MFG3914)")) == []
        assert _banned_hits(_strip_quoted("a `Fixture Container` occurrence beside a `Stock Container`")) == []
        assert _banned_hits(_strip_quoted("the AU class's 'Component Containers' / a Joint Origin Container (JOC)")) == []
