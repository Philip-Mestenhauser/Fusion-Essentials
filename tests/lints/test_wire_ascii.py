"""Lint: every agent-facing wire string is pure ASCII and uses Fusion's own vocabulary
(CLAUDE.md "Tool descriptions").

A tool's description, its per-input descriptions, and any runtime note/error are the only things a
connected agent ever reads about a tool. Two rules police that text, over one set of surfaces:

  1. ASCII. The strings cross the wire JSON-encoded with ``ensure_ascii``, so a non-ASCII character
     (an em dash, a curly quote, a degree sign, ...) turns into a 6-character ``\\uXXXX`` escape that
     costs tokens every turn and reads worse than the plain-ASCII spelling (" - " for an em dash,
     "..." for an ellipsis, "->" for an arrow, "deg" for a degree sign).
  2. VOCABULARY. Fusion never calls a component a "container"; this codebase does not either. An
     invented noun on the wire teaches a connected agent a word Fusion's own UI never uses, so the
     agent can't map it back to what it sees.

Both sweep the same three places a wire string is authored, which is why they share the walkers
below:
  1. the LIVE registry - every registered tool's description and every input property's
     description (recursively, for nested array/object schemas);
  2. the SOURCE constants - a module-level constant in the tool sources, whether or not it ends up
     wired to a tool today (catching a dead-but-about-to-be-reused constant before it goes
     non-ASCII);
  3. the RUNTIME payloads - every string literal inside an ``ok(...)`` / ``error(...)`` call in
     the tool sources (notes, error text, payload keys/values - all of it crosses the wire).

The two rules read surface 2 at different widths, and that asymmetry is deliberate. ASCII takes
EVERY module-level constant, because a payload can carry its sentence by NAME - ``ok({"note":
_OPERATIONS_NOTE})``, or a dict of notes keyed by mode - which puts that text out of reach of
surfaces 1 and 3, and whether a given constant reaches an agent is not decidable from its
assignment. Scanning them all is what makes that judgment unnecessary. The price is that a constant
which never crosses the wire (an internal marker, a regex source, an abstraction-map blurb) is held
to the same ASCII spelling: that costs nothing while those constants spell their text in ASCII, and
one that genuinely needs a non-ASCII character takes an ``_ASCII_EXEMPT`` entry with a reason.
VOCABULARY stays on the ``*_DESCRIPTION`` constants, because it judges the words chosen for a
reader - an internal marker or a Fusion API name is not prose this repo wrote for one.

Comments and internal identifiers are outside both sweeps on purpose (box-drawing dividers in ``#``
comments and module docstrings included): a comment never serializes onto the wire. The vocabulary
rule reaches one surface the ASCII rule does not - the skill files under .claude/skills/, which an
executing agent reads verbatim.

A banned term is matched case-insensitively on a word boundary (so "container"/"Container"/
"containers" all trip; "self-contained" does not). To permit a genuinely unavoidable use, add a
``(surface_id, term)`` entry to ``_ALLOWLIST`` with a concrete reason - empty today, because every
current wire string uses Fusion's own vocabulary.
"""

import ast
import os
import re
import sys

import _corpus
from conftest import TOOLS_DIR, register_all_tools


def _tool_files():
    """(filename, path) for every module under tools/ - the source half of both sweeps."""
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if fn.endswith(".py"):
            yield fn, os.path.join(TOOLS_DIR, fn)


def _walk_property_descriptions(props, path, out):
    """Recurse into a JSON-schema properties dict, collecting (path, description) pairs (nested
    arrays/objects too)."""
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


def _input_descriptions(d):
    """(path, description) for every input property of one tool's to_dict()."""
    found = []
    _walk_property_descriptions((d.get("inputSchema") or {}).get("properties", {}) or {},
                                d.get("name"), found)
    return found


# A module-level constant whose name ends in DESCRIPTION - the naming convention every tool uses for
# its wire description (TOOL_DESCRIPTION, and a couple of per-verb variants on action-dispatched tools).
_DESCRIPTION_NAME = re.compile(r".*DESCRIPTION$")


def _module_constant_strings(tree):
    """(constant_name, [string literals in its assigned value]) for every module-level assignment in
    a parsed module - one row per assigned NAME, so ``A = B = ...`` and a tuple unpack each name
    themselves. The assigned expression is WALKED instead of literal_eval'd, so an
    f-string/`.format()`/`+`-built value, a dict of notes keyed by mode, and a description handed
    straight to an inline ``Tool(...)`` are all checked piece by piece."""
    out = []
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not targets or node.value is None:
            continue
        strings = [n.value for n in ast.walk(node.value)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if not strings:
            continue
        for target in targets:
            out += [(n.id, strings) for n in ast.walk(target) if isinstance(n, ast.Name)]
    return out


def _constant_strings(src):
    """The same collection over a source STRING - what the bite tests below plant their shapes in."""
    return _module_constant_strings(ast.parse(src))


def _description_constant_strings(tree):
    """The ``*_DESCRIPTION`` rows of _module_constant_strings - the narrower surface the vocabulary
    rule reads (see the module docstring)."""
    return [(name, strings) for name, strings in _module_constant_strings(tree)
            if _DESCRIPTION_NAME.match(name)]


def _ok_error_call_strings(tree):
    """(call_name, lineno, [string literals]) for every ``ok(...)`` / ``error(...)`` call in a parsed
    module - the runtime payload authoring sites (``_common.ok``/``_common.error`` attribute calls
    too). Every string literal in the call subtree is collected (f-string pieces, nested dict
    keys/values, defaults handed to safe()): each is text that crosses the wire JSON-encoded."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if name not in ("ok", "error"):
            continue
        strings = [n.value for n in ast.walk(node)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if strings:
            out.append((name, node.lineno, strings))
    return out


def _payload_strings(src):
    """The same collection over a source STRING - what the bite tests below plant their shapes in."""
    return _ok_error_call_strings(ast.parse(src))


# ── rule 1: pure ASCII ─────────────────────────────────────────────────────────

def _non_ascii(text):
    return [(c, hex(ord(c))) for c in text if ord(c) > 127]


# A constant that must hold a non-ASCII character: {(file, CONSTANT): reason}. Keyed by BOTH, so an
# entry covers the one constant that earned it and no same-named constant in another file. An entry
# states why the character is load-bearing (a Fusion string this repo has to match byte for byte,
# say), never a preference for the prettier glyph - and _stale_exemptions keeps the table
# shrink-only. EMPTY today: every constant under tools/ spells its text in ASCII.
_ASCII_EXEMPT = {}


def _ascii_report(fn, const_name, strings):
    """An offender line for each string in `strings` carrying a non-ASCII character, unless
    (fn, const_name) is exempt."""
    if (fn, const_name) in _ASCII_EXEMPT:
        return []
    return [f"{fn}: {const_name} has {bad} - "
            f"replace with a plain-ASCII spelling (' - ', '...', '->')"
            for bad in (_non_ascii(s) for s in strings) if bad]


def _stale_exemptions():
    """Entries in _ASCII_EXEMPT that no longer earn their place: no reason, no such file, no such
    constant, or a constant whose text is ASCII again. Each one is an entry to delete - which is
    what makes the table shrink-only rather than a place non-ASCII text accumulates."""
    stale = []
    for (fn, const_name), reason in _ASCII_EXEMPT.items():
        if not (reason or "").strip():
            stale.append(f"{fn}:{const_name}: needs a plain-English reason")
            continue
        path = os.path.join(TOOLS_DIR, fn)
        if not os.path.exists(path):
            stale.append(f"{fn}:{const_name}: no such file - drop the entry")
            continue
        strings = [s for name, ss in _module_constant_strings(_corpus.tree(path))
                   if name == const_name for s in ss]
        if not strings:
            stale.append(f"{fn}:{const_name}: no such module-level constant - drop the entry")
        elif not any(_non_ascii(s) for s in strings):
            stale.append(f"{fn}:{const_name}: the constant is ASCII again - drop the entry")
    return stale


class TestToolDescriptionsAreAscii:
    def test_every_tool_description_is_ascii(self):
        offenders = []
        for it in register_all_tools():
            d = it.to_dict()
            desc = d.get("description") or ""
            bad = _non_ascii(desc)
            if bad:
                offenders.append(f"{d.get('name')}: description has {bad} - "
                                  f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII tool description(s):\n  " + "\n  ".join(offenders)

    def test_every_input_description_is_ascii(self):
        offenders = []
        for it in register_all_tools():
            for path, desc in _input_descriptions(it.to_dict()):
                bad = _non_ascii(desc)
                if bad:
                    offenders.append(f"{path}: input description has {bad} - "
                                      f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII input description(s):\n  " + "\n  ".join(offenders)


class TestModuleConstantsAreAscii:
    @staticmethod
    def _flagged(src):
        """The constant names one source's module-level constants are reported under."""
        return [name for name, strings in _constant_strings(src)
                if _ascii_report("probe.py", name, strings)]

    def test_every_module_constant_is_ascii(self):
        offenders = []
        for fn, path in _tool_files():
            for const_name, strings in _module_constant_strings(_corpus.tree(path)):
                offenders += _ascii_report(fn, const_name, strings)
        assert not offenders, "non-ASCII module constant(s):\n  " + "\n  ".join(offenders)

    def test_the_constant_sweep_bites(self):
        # A note constant a payload carries by NAME - the shape surfaces 1 and 3 never see - MUST be
        # flagged however it is written: an implicit concatenation...
        assert self._flagged('_OPERATIONS_NOTE = (\n    "the op sits "\n'
                             '    "5° over")\n') == ["_OPERATIONS_NOTE"]
        # ...a dict of notes keyed by mode...
        assert self._flagged('_NOTES = {"world": "a → b"}\n') == ["_NOTES"]
        # ...an f-string piece...
        assert self._flagged('_DESC = f"bad °: {x}"\n') == ["_DESC"]
        # ...a description handed straight to an inline Tool(...)...
        assert self._flagged('tool = Tool("m", "5° of tilt")\n') == ["tool"]
        # ...and each name of a multi-name assignment.
        assert self._flagged('_A = _B = "5° off"\n') == ["_A", "_B"]
        # The other two assignment shapes the walker takes: an annotated constant, and a tuple
        # unpack - whose names each answer for the whole assignment's text.
        assert self._flagged('_NOTE: str = "5° off"\n') == ["_NOTE"]
        assert self._flagged('_C, _D = "5° off", "x"\n') == ["_C", "_D"]
        # A constant inside a function is not module-level, and an ASCII constant is collected with
        # nothing to report.
        assert _constant_strings('def h():\n    N = "5° off"\n') == []
        assert _constant_strings('_NOTE = "5 deg off"\n') == [("_NOTE", ["5 deg off"])]
        assert self._flagged('_NOTE = "5 deg off"\n') == []

    def test_the_reported_boundary_is_the_last_ascii_code_point(self):
        # the line the check draws: U+007F is the last code point it passes, U+0080 the first it
        # reports.
        assert _ascii_report("probe.py", "_C", ["\x7f"]) == []
        assert _ascii_report("probe.py", "_C", ["\x80"])

    def test_an_exemption_excuses_one_constant_in_one_file(self, monkeypatch):
        monkeypatch.setitem(_ASCII_EXEMPT, ("probe.py", "_GLYPH"), "a stated reason")
        assert _ascii_report("probe.py", "_GLYPH", ["5°"]) == []
        # the key is the PAIR: the same name in another file, and another name in the same file,
        # both still trip.
        assert _ascii_report("other.py", "_GLYPH", ["5°"])
        assert _ascii_report("probe.py", "_OTHER", ["5°"])

    def test_the_exemption_table_only_shrinks(self, monkeypatch):
        assert _stale_exemptions() == []
        # one entry per branch, driven off constants that really exist so a rename in tools/ cannot
        # turn this into a test of its own fixture. What is pinned is each branch's MESSAGE - the
        # repair the maintainer is told to make - and not the count: an entry any branch rejects is
        # rejected by the branches below it too, so a count alone stays green while a branch that
        # stopped firing hides behind the next one reporting the same entry for the wrong reason.
        real = list(dict.fromkeys(
            name for name, _ in
            _module_constant_strings(_corpus.tree(os.path.join(TOOLS_DIR, "_common.py")))))
        monkeypatch.setitem(_ASCII_EXEMPT, ("_common.py", real[0]), "a stated reason")
        monkeypatch.setitem(_ASCII_EXEMPT, ("_common.py", "_NO_SUCH_CONSTANT"), "a stated reason")
        monkeypatch.setitem(_ASCII_EXEMPT, ("_common.py", real[1]), "  ")
        monkeypatch.setitem(_ASCII_EXEMPT, ("no_such_file.py", "_X"), "a stated reason")
        assert _stale_exemptions() == [
            f"_common.py:{real[0]}: the constant is ASCII again - drop the entry",
            "_common.py:_NO_SUCH_CONSTANT: no such module-level constant - drop the entry",
            f"_common.py:{real[1]}: needs a plain-English reason",
            "no_such_file.py:_X: no such file - drop the entry",
        ]

    def test_only_the_reason_is_missing_when_the_glyph_is_real(self, monkeypatch, tmp_path):
        # the one state where the reason branch decides alone: the constant really does carry the
        # character, so the exemption is live and no other branch has anything to report - a
        # wordless entry is stale here or nowhere. It reads a probe file because the lint's own
        # guarantee is that no constant under tools/ is non-ASCII.
        (tmp_path / "probe.py").write_text('_GLYPH = "5° off"\n', encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "TOOLS_DIR", str(tmp_path))
        monkeypatch.setitem(_ASCII_EXEMPT, ("probe.py", "_GLYPH"), "a stated reason")
        assert _stale_exemptions() == []
        # every wordless shape a reason can be written as, the missing value included.
        for wordless in ("  ", "", None):
            monkeypatch.setitem(_ASCII_EXEMPT, ("probe.py", "_GLYPH"), wordless)
            assert _stale_exemptions() == ["probe.py:_GLYPH: needs a plain-English reason"]


class TestRuntimePayloadStringsAreAscii:
    def test_every_ok_error_literal_is_ascii(self):
        offenders = []
        for fn, path in _tool_files():
            for call_name, lineno, strings in _ok_error_call_strings(_corpus.tree(path)):
                for s in strings:
                    bad = _non_ascii(s)
                    if bad:
                        offenders.append(f"{fn}:{lineno}: {call_name}(...) literal has {bad} - "
                                          f"replace with a plain-ASCII spelling (' - ', '...', '->')")
        assert not offenders, "non-ASCII ok()/error() payload literal(s):\n  " + "\n  ".join(offenders)

    def test_the_runtime_sweep_bites(self):
        # A doctored error() payload with a degree sign MUST be flagged...
        hits = _payload_strings('def h():\n    return error("tilt is 5° too far")\n')
        assert hits and any(_non_ascii(s) for _, _, strings in hits for s in strings)
        # ...an ok() note through the attribute form too...
        hits = _payload_strings(
            'def h():\n    return _common.ok({"note": "a → b"})\n')
        assert hits and any(_non_ascii(s) for _, _, strings in hits for s in strings)
        # ...an f-string piece inside the call is collected...
        hits = _payload_strings('def h():\n    return error(f"bad °: {x}")\n')
        assert hits and any(_non_ascii(s) for _, _, strings in hits for s in strings)
        # ...while a non-wire call is out of scope, and a clean payload has no non-ASCII hit.
        assert _payload_strings('def h():\n    log("° in a log line")\n') == []
        hits = _payload_strings('def h():\n    return ok({"note": "5 deg off"})\n')
        assert hits and not any(_non_ascii(s) for _, _, strings in hits for s in strings)


# ── rule 2: Fusion's own vocabulary ────────────────────────────────────────────

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
            for path, desc in _input_descriptions(it.to_dict()):
                offenders += _report(path, _banned_hits(desc))
        assert not offenders, "banned wire word in input description(s):\n  " + "\n  ".join(offenders)


class TestNoBannedVocabularyInDescriptionConstants:
    def test_no_description_constant_uses_a_banned_word(self):
        offenders = []
        for fn, path in _tool_files():
            for const_name, strings in _description_constant_strings(_corpus.tree(path)):
                for s in strings:
                    offenders += _report(f"{fn}:{const_name}", _banned_hits(s))
        assert not offenders, "banned wire word in description constant(s):\n  " + "\n  ".join(offenders)


class TestNoBannedVocabularyInRuntimePayloads:
    def test_no_ok_error_literal_uses_a_banned_word(self):
        offenders = []
        for fn, path in _tool_files():
            for call_name, lineno, strings in _ok_error_call_strings(_corpus.tree(path)):
                for s in strings:
                    offenders += _report(f"{fn}:{lineno} ({call_name} payload)", _banned_hits(s))
        assert not offenders, "banned wire word in ok()/error() payload(s):\n  " + "\n  ".join(offenders)

    def test_the_runtime_sweep_bites(self):
        # A doctored error() message coining the banned noun MUST be flagged...
        hits = _payload_strings('def h():\n    return error("pick the container occurrence")\n')
        assert [t for _, _, strings in hits for s in strings for t in _banned_hits(s)] == ["container"]
        # ...an ok() note through the attribute form too, and inside an f-string piece...
        hits = _payload_strings(
            'def h():\n    return _common.ok({"note": f"moved {n} containers"})\n')
        assert [t for _, _, strings in hits for s in strings for t in _banned_hits(s)] == ["container"]
        # ...while a non-wire call is out of scope, and Fusion's own vocabulary passes clean.
        assert _payload_strings('def h():\n    log("container in a log line")\n') == []
        hits = _payload_strings('def h():\n    return ok({"note": "a component occurrence"})\n')
        assert not [t for _, _, strings in hits for s in strings for t in _banned_hits(s)]


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
