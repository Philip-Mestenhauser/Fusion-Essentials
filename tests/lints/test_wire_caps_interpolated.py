# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a cap/limit a wire description STATES is interpolated from the constant that ENFORCES it.

A tool's description and its per-input descriptions are the only thing a connected agent reads
about a cap. When the description spells the number as a digit and a module constant holds the
number the handler actually clamps to, the two are free to disagree: nothing reads both, so a
constant raised in the handler leaves the description quietly lying about the cap.

An f-string closes it by construction - ``f"(default {_ROWS_CAP})"`` has no digit to go stale, so
the wire sentence cannot say anything but what the code enforces. That interpolation is what this
lint asks for, and the reason the lint can be written at all: an interpolated cap carries no digit
literal to flag.

The rule: inside a wire description string literal, a run of digits equal to a module-level int
constant declared in the SAME file is flagged, where the constant's NAME carries CAP, CEILING,
MAX, DEFAULT, LIMIT or DEPTH.

That name filter is what makes the rule quiet enough to be a gate. Without it the rule also fires
where a description's digits collide with an unrelated constant by coincidence: an alpha constant
of 255 against the ``0-255`` byte RANGE a colour input accepts, and a zone-count constant of 2
against the ``2`` in "a 2D drawing". Neither is a cap and neither can drift; both name constants
that are not cap-shaped, so requiring the cap vocabulary in the name keeps them out while keeping
every real cap in.

Four authoring positions are swept, the four places a wire description is written:
  1. a module-level ``*_DESC`` / ``*_DESCRIPTION`` constant, walked piece by piece so an
     implicitly-concatenated or ``+``-built description is checked one literal at a time;
  2. a ``"description"`` value in a JSON-schema dict - the ``add_input_property`` per-input text;
  3. a ``description=`` KEYWORD ARGUMENT - ``Tool.create_simple(description=...)`` and the typed
     kinds' own ``description=``, where the text is written straight into the call rather than
     bound to a constant first;
  4. ``input_param_description=`` on ``Tool.create_with_string_input`` - 27 call sites across 19
     tool modules. It is a per-input description under another argument name: ``tool.py`` writes
     its value into the single input's JSON-schema ``"description"``, so it lands on the same
     surface position 2 governs.

A literal reached by more than one position is counted once: the positions overlap where a
``*_DESC`` constant is itself built from a call or a dict, and one sentence is one finding.

THE OTHER HALF - the handler must READ the constant too. Interpolating the description binds the
sentence to the constant and leaves the handler free: a parameter default spelled as a digit
(``width: int = 800``) beside ``_WIDTH_DEFAULT = 800`` puts the number in two places again, and no
test above notices, because both legs still say 800 until one of them moves. So a second rule
(``bare_cap_defaults``) flags a PARAMETER DEFAULT written as a digit equal to a cap constant in the
same file. Measured across every tool module at four narrowing steps: any int literal anywhere
flags 17 (almost all coincidence - a rounding place, an enum int); restricting to a parameter
DEFAULT flags 2, both genuine; narrowing further to handlers, or to cap-shaped parameter names,
flags the same 2 and buys nothing. The parameter-default position is therefore where this rule
sits - a cap-shaped-NAME filter would also drop the ``width``/``height`` case, which is the one the
rule exists for.
"""

import ast
import os
import re

import _corpus
from conftest import TOOLS_DIR

# The naming convention every wire description constant uses: TOOL_DESCRIPTION, plus the per-verb
# _GET_DESC / _REQUEST_DESC variants an action-dispatched or multi-tool file carries.
_DESCRIPTION_NAME = re.compile(r".*_DESC(RIPTION)?$")

# Any run of digits, so a bound written flush against other characters ("1-4096", "800x600") is
# still read. A word-boundary match would miss those.
_DIGITS = re.compile(r"\d+")

# The cap vocabulary a constant's name must carry for its value to be treated as an enforced bound.
_CAP_NAME = re.compile(r"CAP|CEILING|MAX|DEFAULT|LIMIT|DEPTH")

# The keyword arguments whose value reaches the wire as a description. 'description' is the tool's
# own; 'input_param_description' is create_with_string_input's, which tool.py writes into the single
# input's JSON-schema "description" - the same surface an agent reads, under another argument name.
_DESCRIPTION_KWARGS = ("description", "input_param_description")


def _module_int_constants(tree):
    """{NAME: value} for every module-level ``NAME = <int literal>`` whose name is upper-case."""
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Constant):
            continue
        if not isinstance(value.value, int) or isinstance(value.value, bool):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.upper() == target.id:
                out[target.id] = value.value
    return out


def _description_literals(tree):
    """(label, lineno, text) for every plain string literal in a wire description position.

    Only ``ast.Constant`` strings are collected. The static pieces of an f-string are collected
    too - an f-string that still hardcodes a digit is a finding - while the interpolated value
    itself is not a literal, which is what exempts a correctly interpolated cap.

    A literal the positions reach twice is reported once, keyed by its exact source span: a
    ``*_DESC`` constant built from a call or a dict is read by two positions and is one sentence.
    """
    out = []
    seen = set()

    def collect(label, node):
        for n in ast.walk(node):
            if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
                continue
            span = (n.lineno, n.col_offset, n.end_lineno, n.end_col_offset)
            if span in seen:
                continue
            seen.add(span)
            out.append((label, n.lineno, n.value))

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and _DESCRIPTION_NAME.match(target.id):
                collect(target.id, node.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "description":
                    collect("description", value)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _DESCRIPTION_KWARGS:
                    collect(f"{kw.arg}=", kw.value)
    return out


def hardcoded_caps(src, filename="<src>", narrow=True):
    """(label, lineno, [constant names], value, text) per digit run matching an in-file constant.

    ``narrow=False`` drops the cap-name filter - the raw rule, kept reachable so the filter's
    effect is measurable from a test rather than asserted in prose.
    """
    tree = ast.parse(src, filename=filename)
    constants = _module_int_constants(tree)
    if narrow:
        constants = {k: v for k, v in constants.items() if _CAP_NAME.search(k.upper())}
    by_value = {}
    for name, value in constants.items():
        by_value.setdefault(value, []).append(name)
    hits = []
    for label, lineno, text in _description_literals(tree):
        for match in _DIGITS.finditer(text):
            value = int(match.group())
            if value in by_value:
                hits.append((label, lineno, sorted(by_value[value]), value, text))
    return hits


def _cap_constants(tree):
    """{value: [names]} for the module constants whose name carries the cap vocabulary."""
    by_value = {}
    for name, value in _module_int_constants(tree).items():
        if _CAP_NAME.search(name.upper()):
            by_value.setdefault(value, []).append(name)
    return by_value


def _parameter_defaults(tree):
    """(function, parameter, node) per int literal written as a PARAMETER DEFAULT. A constant's own
    module-level assignment is not one of these, so a declaration is never read as a copy of itself."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        positional = args.posonlyargs + args.args
        pairs = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
        pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
        for arg, default in pairs:
            for n in ast.walk(default):
                if isinstance(n, ast.Constant) and isinstance(n.value, int) \
                        and not isinstance(n.value, bool):
                    out.append((node.name, arg.arg, n))
    return out


def bare_cap_defaults(src, filename="<src>"):
    """(function, parameter, lineno, [constant names], value) per PARAMETER DEFAULT spelled as a
    digit while a cap constant declared in the same file already holds that number."""
    tree = ast.parse(src, filename=filename)
    by_value = _cap_constants(tree)
    hits = [(func, param, node.lineno, sorted(by_value[node.value]), node.value)
            for func, param, node in _parameter_defaults(tree) if node.value in by_value]
    return sorted(hits, key=lambda h: (h[2], h[1]))


# (file, parameter) -> the audited reason the default is still a digit. Entries only leave this
# table; a new one is a defect, not a style choice.
_BARE_DEFAULT_ALLOWED = {
    ("design_get.py", "max_depth"):
        "handler default 3 beside _TREE_DEFAULT_DEPTH - open, owned by design_get.py",
    ("find_geometry.py", "max_results"):
        "handler default 20 beside _MAX_RESULTS_DEFAULT - open, owned by find_geometry.py",
}


def _sweep_bare_defaults():
    offenders = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py"):
            continue
        src = _corpus.text(os.path.join(TOOLS_DIR, fn))
        for func, param, lineno, names, value in bare_cap_defaults(src, fn):
            if (fn, param) in _BARE_DEFAULT_ALLOWED:
                continue
            # Several constants can hold one value; the message names all of them and picks none -
            # only the code knows which one this parameter's description interpolates.
            offenders.append(f"{fn}:{lineno}: {func}({param}=...) spells {value} as a digit while "
                             f"{'/'.join(names)} holds it - write that constant as the default, so "
                             "the handler reads the number the description interpolates")
    return offenders


def _sweep(narrow=True):
    offenders = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(TOOLS_DIR, fn)
        for label, lineno, names, value, _text in hardcoded_caps(_corpus.text(path), path,
                                                                 narrow=narrow):
            offenders.append(f"{fn}:{lineno}: {label} spells {value} as a digit while "
                             f"{'/'.join(names)} holds it - interpolate it "
                             f"(f\"... {{{names[0]}}} ...\") so the sentence cannot outlive the value")
    return offenders


class TestWireCapsAreInterpolated:
    def test_no_description_hardcodes_a_cap_constant(self):
        offenders = _sweep()
        assert not offenders, (
            "wire description(s) hardcoding a cap a constant in the same file already holds:\n  "
            + "\n  ".join(offenders))


class TestHandlerDefaultsReadTheConstant:
    def test_no_parameter_default_hardcodes_a_cap_constant(self):
        offenders = _sweep_bare_defaults()
        assert not offenders, (
            "parameter default(s) hardcoding a cap a constant in the same file already holds:\n  "
            + "\n  ".join(offenders))

    def test_every_allowlist_entry_still_names_a_live_offender(self):
        # the table only shrinks: an entry whose defect was fixed must be deleted, not left to
        # silence a future one at the same address.
        live = set()
        for fn in sorted(os.listdir(TOOLS_DIR)):
            if not fn.endswith(".py"):
                continue
            for _func, param, *_rest in bare_cap_defaults(_corpus.text(os.path.join(TOOLS_DIR, fn)),
                                                          fn):
                live.add((fn, param))
        stale = sorted(set(_BARE_DEFAULT_ALLOWED) - live)
        assert not stale, f"allowlist entries whose offender is gone - delete them: {stale}"


class TestTheBareDefaultRuleBites:
    _BARE = ("_WIDTH_DEFAULT = 800\n"
             "def handler(view: str = 'current', width: int = 800):\n"
             "    return width\n")

    def test_a_parameter_default_repeating_a_cap_constant_is_flagged(self):
        # the shape the description rule cannot see: the sentence interpolates, the signature does
        # not, and both read 800 until one of them moves.
        assert [(h[1], h[3], h[4]) for h in bare_cap_defaults(self._BARE)] == \
            [("width", ["_WIDTH_DEFAULT"], 800)]

    def test_naming_the_constant_in_the_signature_clears_it(self):
        assert bare_cap_defaults(self._BARE.replace("width: int = 800",
                                                    "width: int = _WIDTH_DEFAULT")) == []

    def test_a_default_matching_no_cap_constant_is_left_alone(self):
        assert bare_cap_defaults("_WIDTH_DEFAULT = 800\ndef h(zoom: int = 1):\n    return zoom\n") == []

    def test_a_non_cap_named_constant_does_not_make_a_default_an_offence(self):
        # the same name filter the description rule uses: a constant that is not cap-shaped cannot
        # drift into a cap, so a default colliding with it is coincidence.
        src = "_CUSTOM_ZONES = 2\ndef h(sides: int = 2):\n    return sides\n"
        assert bare_cap_defaults(src) == []

    def test_a_literal_outside_a_parameter_default_is_out_of_scope(self):
        # a rounding place or an enum int is not a second copy of a cap - measured, admitting them
        # takes the rule from 2 hits to 17 across the tool modules.
        src = "_TREE_DEFAULT_DEPTH = 3\ndef h(x):\n    return round(x, 3)\n"
        assert bare_cap_defaults(src) == []

    def test_a_keyword_only_default_is_swept_too(self):
        src = ("_ROWS_CAP = 25\n"
               "def h(target, *, max_results: int = 25):\n"
               "    return max_results\n")
        assert [(h[1], h[4]) for h in bare_cap_defaults(src)] == [("max_results", 25)]


class TestTheRuleBites:
    # A digit spelled into a description beside the constant holding it - the exact shape the
    # sweep above exists to catch, in each of the swept authoring positions.
    _HARDCODED_CONSTANT = (
        "_ROWS_CAP = 25\n"
        "TOOL_DESCRIPTION = (\n"
        "    'rows are capped by max_results '\n"
        "    '(default 25; truncated flags a hit cap).'\n"
        ")\n"
    )
    _HARDCODED_PROPERTY = (
        "_MAX_DIM = 4096\n"
        "tool = t.add_input_property('width', {'type': 'integer',\n"
        "        'description': 'Width in px (1-4096, default 800).'})\n"
    )

    def test_a_hardcoded_cap_in_a_description_constant_is_flagged(self):
        hits = hardcoded_caps(self._HARDCODED_CONSTANT)
        assert [(h[2], h[3]) for h in hits] == [(["_ROWS_CAP"], 25)]

    def test_a_hardcoded_cap_in_an_input_property_is_flagged(self):
        hits = hardcoded_caps(self._HARDCODED_PROPERTY)
        assert [(h[2], h[3]) for h in hits] == [(["_MAX_DIM"], 4096)]

    _HARDCODED_KWARG = (
        "_DIFFERENCES_CAP = 200\n"
        "tool = Tool.create_with_string_input(\n"
        "    name='cam_compare',\n"
        "    description=('Compare two operations and report which parameters differ '\n"
        "                 '(capped at 200 rows).'),\n"
        ")\n"
    )

    def test_a_hardcoded_cap_in_a_description_keyword_argument_is_flagged(self):
        # the text is written straight into the call, bound to no constant and inside no dict, so
        # neither of the other two positions can reach it.
        hits = hardcoded_caps(self._HARDCODED_KWARG)
        assert [(h[0], h[2], h[3]) for h in hits] == [("description=", ["_DIFFERENCES_CAP"], 200)]

    def test_interpolating_the_keyword_argument_clears_it(self):
        fixed = self._HARDCODED_KWARG.replace("'(capped at 200 rows).'",
                                              "f'(capped at {_DIFFERENCES_CAP} rows).'")
        assert hardcoded_caps(fixed) == []

    _HARDCODED_INPUT_PARAM = (
        "_ROWS_CAP = 25\n"
        "tool = Tool.create_with_string_input(\n"
        "    name='cam_get_status',\n"
        "    description='Poll a generation.',\n"
        "    input_param_description='Which handle to poll (up to 25 are kept).',\n"
        ")\n"
    )

    def test_a_hardcoded_cap_in_an_input_param_description_is_flagged(self):
        # a per-input description under another argument name - tool.py writes it into the input's
        # schema "description", so it reaches the agent exactly as the dict position does.
        hits = hardcoded_caps(self._HARDCODED_INPUT_PARAM)
        assert [(h[0], h[2], h[3]) for h in hits] == \
            [("input_param_description=", ["_ROWS_CAP"], 25)]

    def test_interpolating_the_input_param_description_clears_it(self):
        fixed = self._HARDCODED_INPUT_PARAM.replace(
            "'Which handle to poll (up to 25 are kept).'",
            "f'Which handle to poll (up to {_ROWS_CAP} are kept).'")
        assert hardcoded_caps(fixed) == []

    def test_a_literal_two_positions_both_reach_is_reported_once(self):
        # a _DESC constant BUILT from a call carries its literal under both the constant position
        # and the keyword position; one sentence is one finding, so the digit is not double-counted.
        src = ("_ROWS_CAP = 25\n"
               "_GET_DESC = build(description='rows are capped at 25.')\n")
        hits = hardcoded_caps(src)
        assert [(h[0], h[2], h[3]) for h in hits] == [("_GET_DESC", ["_ROWS_CAP"], 25)]

    def test_a_non_description_keyword_argument_is_out_of_scope(self):
        # only the argument NAMED description is a wire description; a sibling kwarg is not.
        src = ("_ROWS_CAP = 25\n"
               "tool = build(name='t', note='kept 25 rows')\n")
        assert hardcoded_caps(src) == []

    def test_a_hardcoded_cap_in_a_short_form_description_constant_is_flagged(self):
        # A multi-tool file names its per-verb descriptions _GET_DESC / _REQUEST_DESC rather than
        # _DESCRIPTION, and that spelling carries real cap sentences - so the short form is a swept
        # position in its own right, pinned here by its label as well as its value.
        src = ("_SELECTION_CAP = 50\n"
               "_GET_DESC = 'the selections array is capped (default 50).'\n")
        hits = hardcoded_caps(src)
        assert [(h[0], h[2], h[3]) for h in hits] == [("_GET_DESC", ["_SELECTION_CAP"], 50)]

    def test_interpolating_the_constant_clears_both(self):
        # in both positions the digit is gone from the literal, so nothing can drift
        fixed_constant = self._HARDCODED_CONSTANT.replace(
            "'(default 25; truncated flags a hit cap).'",
            "f'(default {_ROWS_CAP}; truncated flags a hit cap).'")
        fixed_property = self._HARDCODED_PROPERTY.replace(
            "'description': 'Width in px (1-4096, default 800).'",
            "'description': f'Width in px (1-{_MAX_DIM}, default 800).'")
        assert hardcoded_caps(fixed_constant) == []
        assert hardcoded_caps(fixed_property) == []

    def test_an_fstring_that_still_hardcodes_the_digit_is_flagged(self):
        # an f-string is not a free pass - only interpolating the constant removes the digit
        src = self._HARDCODED_CONSTANT.replace(
            "'(default 25; truncated flags a hit cap).'",
            "f'(default 25; {extra} flags a hit cap).'")
        assert [(h[2], h[3]) for h in hardcoded_caps(src)] == [(["_ROWS_CAP"], 25)]

    def test_a_digit_matching_no_constant_is_left_alone(self):
        src = "_ROWS_CAP = 25\nTOOL_DESCRIPTION = 'zoom scales the view (default 1).'\n"
        assert hardcoded_caps(src) == []

    def test_a_non_description_string_is_out_of_scope(self):
        # a note, a comment or a handler-local string is not the surface this rule governs
        src = "_ROWS_CAP = 25\ndef h():\n    return ok({'note': 'kept 25 rows'})\n"
        assert hardcoded_caps(src) == []


class TestConstantCollection:
    # What counts as an enforced bound, pinned in one place. A lower-case module-level name is a
    # VARIABLE, not a declared bound. A bool is not a bound either: True is an int to Python, so
    # admitting it would read SOME_MAX = True as the value 1 and flag every '1' in a description.
    _MIXED = ("rows_cap = 25\n"
              "_ROWS_CAP = 25\n"
              "_OTHER_MAX = True\n")

    def test_only_upper_case_int_literals_are_collected(self):
        assert _module_int_constants(ast.parse(self._MIXED)) == {"_ROWS_CAP": 25}


class TestTheCapNameFilter:
    # The two measured collision shapes the name filter exists to hold out: a byte RANGE a colour
    # input accepts, and a digit inside a word. Both are flagged by the raw rule and neither is a
    # cap, so each is asserted in both directions - raw fires, narrowed does not.
    _COLOUR_RANGE = (
        "_COLOR_ALPHA = 255\n"
        "tool = t.add_input_property('color', {'type': 'string',\n"
        "        'description': \"Color as '#RRGGBB', 'RRGGBB', or 'r,g,b' (0-255 each).\"})\n"
    )
    _PROSE_DIGIT = (
        "_CUSTOM_ZONES = 2\n"
        "TOOL_DESCRIPTION = 'Create a 2D drawing from the active design.'\n"
    )

    def test_the_raw_rule_flags_both_collisions(self):
        assert len(hardcoded_caps(self._COLOUR_RANGE, narrow=False)) == 1
        assert len(hardcoded_caps(self._PROSE_DIGIT, narrow=False)) == 1

    def test_the_cap_name_filter_holds_both_out(self):
        assert hardcoded_caps(self._COLOUR_RANGE) == []
        assert hardcoded_caps(self._PROSE_DIGIT) == []

    def test_the_filter_keeps_every_cap_vocabulary_word(self):
        # each word the filter accepts is exercised, so narrowing the vocabulary goes red here
        for word in ("CAP", "CEILING", "MAX", "DEFAULT", "LIMIT", "DEPTH"):
            src = f"_ROWS_{word} = 25\nTOOL_DESCRIPTION = 'capped at 25 rows.'\n"
            assert len(hardcoded_caps(src)) == 1, f"the {word} vocabulary word stopped matching"
