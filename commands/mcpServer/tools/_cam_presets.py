# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""CAM tool PRESETS: one library tool's named cutting data - what a {name, spindle_speed, feed}
spec may say, which parameter each value drives, and the name/index reads a preset change is
proved by. Not a tool; cam_edit_tools builds and verifies presets through these, on the creation
path of add and on add_preset / remove_preset.
"""

import re

from ._common import iter_collection, safe
from ._cam_common import expression_error

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it.
MAP_BLURB = (
    "the ToolPreset half of cam_edit_tools - reach for one only from its preset paths. "
    "_preset_spec_error - why a {name?, spindle_speed?, feed?} spec is unusable; an unrecognised "
    "key would otherwise apply nothing and still report success; _preset_param_of/"
    "_set_preset_param/_apply_preset_values - the parameter a spec value drives (candidates in "
    "order: the cutting-data names vary by tool CLASS), the set-and-prove-it-landed, and the whole "
    "spec applied with no silent skip; _preset_names/_presets_named/_persisted_preset_names - the "
    "INDEX-ORDER name list preset_count and presets.remove(index) read against (an unreadable "
    "preset holds its slot as a null), the EXACT-name lookup, and the same list off a re-fetched "
    "tool; _preset_tool - the guards both preset actions share; _persist_preset_change - the "
    "commit-then-re-read that says whether the change reached the library; updateTool returning "
    "true proves nothing, and no preset change moves the tool COUNT; _plain_number - a bare "
    "number, a units-carrying expression being stored as the converted one")


# A preset's cutting-data parameter names vary by tool CLASS. A mill preset carries
# 'tool_feedCutting'; a drill / hole-making preset does not (it exposes plunge/drilling feeds
# instead). A mill and a turning THREADING preset carry 'tool_spindleSpeed', while a general /
# grooving / boring turning preset cuts at constant surface speed and carries 'tool_surfaceSpeed'
# with no spindle speed at all. So a spec value maps to the FIRST candidate the preset actually
# carries, and a miss names what IS there rather than asserting one class's parameter.
_FEED_PARAM_CANDIDATES = ("tool_feedCutting", "tool_feedPlunge", "tool_feedRamp",
                          "tool_feedRetract", "tool_feedEntry", "tool_feedTransition")
_SPEED_PARAM_CANDIDATES = ("tool_spindleSpeed",)

# A BARE number is a fixed unit whatever the document works in (live-verified): feed 900 reads back
# 900.0 mm/min in a millimetre document AND in an inch-units one, and spindle_speed 12000 reads back
# 12000.0 rpm unscaled. Only a units-carrying expression converts - '35in/min' stores verbatim and
# evaluates to 889.0 mm/min - so a string value is passed straight through as the expression.
# (spec key, the substring naming that parameter family, the bare number's unit, the candidates)
_PRESET_VALUE_FIELDS = (
    ("spindle_speed", "speed", "rpm", _SPEED_PARAM_CANDIDATES),
    ("feed", "feed", "mm/min", _FEED_PARAM_CANDIDATES),
)
_PRESET_SPEC_KEYS = ("name", "spindle_speed", "feed")
_NUMERIC_LEAD = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)")


def _preset_param_of(preset, candidates, word):
    """The preset parameter a spec value drives, or (None, the parameters whose name contains
    `word`). Tries `candidates` in order, so a mill keeps its tool_feedCutting and a drill falls
    through to its plunge feed; a miss reports what the preset DOES carry."""
    params = safe(lambda: preset.parameters)
    if params is None:
        return None, []
    for nm in candidates:
        p = safe(lambda nm=nm: params.itemByName(nm))
        if p is not None:
            return p, None
    present = [safe(lambda p=p: p.name) or "" for p in iter_collection(params)]
    return None, [nm for nm in present if word in nm.lower()]


def _plain_number(value):
    """`value` as a float when it is a number or a bare numeric string, else None (a units-carrying
    expression like '35in/min' is None - its stored value is the converted number, not the text). A
    bool is None too: the float is taken off str(value), so True reads as 'True' and refuses rather
    than as the 1.0 float(True) gives."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _preset_spec_error(spec):
    """Why a {name?, spindle_speed?, feed?} preset spec is unusable, or None. Nothing else validates
    a nested object, so an unrecognised key (a 'spindel_speed' typo) would otherwise apply nothing
    and still report success, and a value the parameter store cannot evaluate would reach it as text."""
    if not isinstance(spec, dict):
        return f"'preset' must be an object carrying a 'name'; got {spec!r}."
    unknown = sorted(k for k in spec if k not in _PRESET_SPEC_KEYS)
    if unknown:
        return (f"Unknown preset key(s): {', '.join(unknown)}. A preset takes only "
                f"{', '.join(_PRESET_SPEC_KEYS)}.")
    for key, _word, unit, _candidates in _PRESET_VALUE_FIELDS:
        value = spec.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return f"'{key}' must be a number ({unit}) or an expression string; got {value!r}."
        if isinstance(value, str) and not _NUMERIC_LEAD.match(value.strip()):
            return (f"'{key}' = {value!r} does not open with a number - pass a number ({unit}) or an "
                    "expression that carries its own units, like '35in/min'.")
    return None


def _set_preset_param(p, key, value, unit):
    """Set ONE preset parameter and prove the value landed; returns an error string or None. A CAM
    parameter STORES an expression it cannot evaluate verbatim and still reads a finite 0.0 back, so
    .error (expression_error) is what reveals it. A bare number is additionally checked against the
    value read back; a units-carrying expression is not, since its stored value is the converted
    number rather than the text."""
    try:
        p.expression = str(value)
    except Exception as e:
        return f"Could not set '{key}' = {value!r} on the preset: {e}."
    eerr, _ = expression_error(p)
    if eerr:
        return f"Set '{key}' = {value!r} on the preset but the expression failed to evaluate: {eerr}."
    landed = safe(lambda: p.value.value)
    if isinstance(landed, bool) or not isinstance(landed, (int, float)):
        return (f"Set '{key}' = {value!r} on the preset but it read back {landed!r} - no numeric "
                "value was stored.")
    want = _plain_number(value)
    if want is not None and abs(float(landed) - want) > 1e-9:
        return (f"Set '{key}' = {value!r} on the preset but it read back {landed!r} {unit} - the "
                "value did not land.")
    return None


def _apply_preset_values(preset, spec):
    """Populate ONE preset from a {name?, spindle_speed?, feed?} spec. Returns an error string, or
    None when every requested value landed - a value that cannot be applied is never a silent skip.
    Shared by the creation-time presets of add_tools[] and by action='add_preset'."""
    name = spec.get("name")
    if name is not None:
        name = str(name)
        # The assigned name is read back: a name the library did not store is an error here, never a
        # silently differently-named preset.
        try:
            preset.name = name
        except Exception as e:
            return f"Could not set the preset name to '{name}': {e}."
        landed = safe(lambda: preset.name)
        if landed != name:
            return f"Set the preset name to '{name}' but it read back {landed!r} - the name did not land."
    for key, word, unit, candidates in _PRESET_VALUE_FIELDS:
        value = spec.get(key)
        if value is None:
            continue
        p, avail = _preset_param_of(preset, candidates, word)
        if p is None:
            have = ", ".join(avail) if avail else "(none)"
            return (f"This preset has no {'/'.join(candidates)} parameter, so '{key}' cannot apply. "
                    f"Its {word} parameters: {have}.")
        verr = _set_preset_param(p, key, value, unit)
        if verr:
            return verr
    return None


def _preset_names(presets):
    """Every preset's name, in index order - a positional walk, not the shared iter_collection one:
    the published list is read against preset_count and against the INDEX _presets_named hands to
    presets.remove(), so an unreadable preset has to hold its slot (as a null) rather than shrink
    the list and slide every later name onto the wrong index."""
    return [safe(lambda i=i: presets.item(i).name)
            for i in range(safe(lambda: presets.count, 0) or 0)]


def _presets_named(presets, name):
    """[(index, preset)] for every preset named exactly `name`, compared case-insensitively.
    ToolPresets.remove addresses a preset BY INDEX, which itemsByName's hits do not carry, and
    itemsByName reads '*' and '?' in the name as wild-cards - which a preset NAME must not be - so
    the match walks the collection instead."""
    want = (name or "").strip().lower()
    out = []
    for i in range(safe(lambda: presets.count, 0) or 0):
        p = safe(lambda i=i: presets.item(i))
        if p is not None and (safe(lambda p=p: p.name) or "").strip().lower() == want:
            out.append((i, p))
    return out


def _persisted_preset_names(tool):
    """The preset names of a tool re-fetched from a library, or None when the tool or its presets
    could not be re-fetched (the caller then reports nothing rather than a false mismatch)."""
    presets = safe(lambda: tool.presets) if tool is not None else None
    return _preset_names(presets) if presets is not None else None


def _preset_tool(target, tool_index, spec):
    """(tool, presets, name, None) for a preset action, or (None, None, None, error): the guards both
    preset actions share - a valid tool index, a spec carrying a name, and a tool exposing presets."""
    tools = target.tools
    if tool_index is None or not (0 <= tool_index < len(tools)):
        return None, None, None, f"Provide a valid 'tool' index (0..{len(tools) - 1})."
    serr = _preset_spec_error(spec)
    if serr:
        return None, None, None, serr
    name = str(spec.get("name") or "").strip()
    if not name:
        return None, None, None, "Provide 'preset' with a 'name' - the preset to add or remove."
    tool = tools[tool_index]
    presets = safe(lambda: tool.presets)
    if presets is None:
        return None, None, None, f"The tool at index {tool_index} exposes no presets collection."
    return tool, presets, name, None


def _persist_preset_change(target, tool, tool_index, name, expect_present):
    """Commit a preset add/remove the way this target commits any tool change, then re-read the
    tool's presets back from the library. Returns (names_or_None, error_or_None)."""
    if target.is_document:
        target.update_tool(tool)
    else:
        target.persist()
    stored = target.reread_preset_names(tool_index)
    if stored is None:
        return None, None
    present = any((s or "").strip().lower() == name.lower() for s in stored)
    if expect_present and not present:
        return stored, (f"Added preset '{name}' but the tool re-read from the library holds presets "
                        f"{stored} - the add did not reach the library.")
    if present and not expect_present:
        return stored, (f"Removed preset '{name}' but the tool re-read from the library still holds "
                        f"it (presets {stored}) - the removal did not reach the library.")
    return stored, None
