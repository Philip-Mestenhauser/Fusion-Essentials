# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Ratchet: how many of the tool surface's declared CHOICES the live sweep actually sends.

Tool-level coverage is already gated (test_tool_verify_complete.py: every registered tool is
scripted, excused or pending). That gate is satisfied by calling a tool ONCE, which says nothing
about the choices inside it - a `mode` with nineteen values and one exercised value reads as fully
covered there. A choice nothing ever sends is a choice nothing has ever proven the tool accepts.

So this pins the COUNT, the way test_wire_budget.py pins per-tool wire weight: it may rise freely
and may only fall deliberately, by editing the number below and saying why in the same diff. The
figure is a LOWER bound on what the sweep really sends - a step whose arguments are computed from
run context contributes only the string constants readable in its code object - so treat it as a
direction, not a percentage.

Not every gap is worth closing. A choice reachable only through a cloud tier, an interactive UI
pick, or an API this build does not carry cannot be exercised honestly here, and padding the sweep
with rows that refuse for an unrelated reason would raise this number while proving nothing.
"""

from conftest import load_tool_verify as _load_verify, register_all_tools

# Choices the live sweep sends, measured by this test. Raise it when coverage grows; lowering it is
# a deliberate edit that says which coverage went away and why.
# 328 -> 324: view_screenshot_multi fits the camera once PER VIEW, so a seven-view contact sheet and
# an 'all' sheet behind it cost seventeen zoom-outs at the end of the run, which is what the sweep
# looks like to someone watching it. The sheet is now four views and the 'all' shorthand is gone;
# 'all', 'right', 'iso-top-left' and 'iso-bottom-right' went with them. view_set still sends every
# orientation - its turntable rotates with fit=false and costs nothing on screen.
# 324 -> 321: doc_insert_import lands geometry at the coordinates the FILE carries, so re-importing
# the machined part into the design it was exported from stacks a copy exactly on the original -
# measured on the live document as five coincident Carriers, one per format. The iges/smt/f3d
# re-imports are gone; the STEP round trip stays as the visible proof a written file reads back.
# 326 -> 327: model_extrude's extent='two_side' reaches the sweep - the two-sided cameo drives the
# per-side depth compare, which no other step's extent style exercises.
# 327 -> 328: the template teardown's witness step reads cam_get(template_location='local'), and
# this measure is a per-TOOL string bag - 'local' is also a value of cam_get's own 'scope' enum, so
# the credit lands there. Named rather than absorbed: no step drives cam_get(scope='local') yet, so
# this one point is the heuristic's coarseness and not new scope coverage.
# 328 -> 329: the overture reads the packaged guidance, and its section= call sends 'assemble' - one
# of the seven section ids the tool declares. The other six ride the same closed set and the same
# code path, so one section per sweep is the beat, not a gap to fill with six more reads.
CHOICES_EXERCISED = 329


def _strings(value, seen=None):
    """Every string `value` could put on the wire.

    Steps carry their arguments either as a plain dict or as a callable built at run time, and the
    layout pass WRAPS a callable in a second one - passing the original as a default argument, not
    as a closure cell. So the walk follows constants, defaults AND closure cells; reading only
    co_consts sees a wrapped step as sending nothing at all.
    """
    seen = set() if seen is None else seen
    if id(value) in seen:
        return []
    seen.add(id(value))
    out = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out += _strings(v, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            out += _strings(v, seen)
    elif callable(value) and hasattr(value, "__code__"):
        out += _strings(value.__code__.co_consts or (), seen)
        out += _strings(value.__defaults__ or (), seen)
        out += _strings(tuple((value.__kwdefaults__ or {}).values()), seen)
        for cell in (value.__closure__ or ()):
            try:
                out += _strings(cell.cell_contents, seen)
            except ValueError:      # a cell not yet filled - nothing to read
                pass
    return out


def _declared_choices(covered):
    """{tool: {input: [values]}} for every enum on a tool the sweep calls. An array input carries
    its enum on 'items', which is the same vocabulary one level down."""
    out = {}
    for item in register_all_tools():
        name = item.primitive.name
        if name not in covered:
            continue
        for prop, spec in (item.primitive.input_schema.get("properties") or {}).items():
            values = spec.get("enum") or (spec.get("items") or {}).get("enum")
            if values:
                out.setdefault(name, {})[prop] = list(values)
    return out


def measure():
    """(exercised, declared, [(tool, input, missing), ...]) over the live sweep's steps."""
    verify = _load_verify()
    sent, covered = {}, set()
    for step in verify.STEPS:
        covered.add(step[0])
        sent.setdefault(step[0], set()).update(_strings(step[1]))

    exercised = declared = 0
    gaps = []
    for tool, inputs in _declared_choices(covered).items():
        for prop, values in inputs.items():
            hit = sent.get(tool, set()) & set(values)
            declared += len(values)
            exercised += len(hit)
            missing = [v for v in values if v not in hit]
            if missing:
                gaps.append((tool, prop, missing))
    return exercised, declared, gaps


class TestChoiceCoverage:
    def test_choice_coverage_does_not_fall(self):
        exercised, declared, gaps = measure()
        worst = sorted(gaps, key=lambda g: -len(g[2]))[:8]
        report = "\n  ".join(f"{t}.{p}: {len(m)} untouched ({', '.join(m[:6])})" for t, p, m in worst)
        assert exercised >= CHOICES_EXERCISED, (
            f"The live sweep now sends {exercised} of {declared} declared choices, down from the "
            f"pinned {CHOICES_EXERCISED}. Coverage was removed - restore the steps, or lower "
            "CHOICES_EXERCISED in this file and say in the same diff which coverage went away and "
            f"why.\nLargest gaps now:\n  {report}")

    def test_pin_is_not_stale(self):
        # a pin left behind after coverage grew stops ratcheting: it can no longer catch a removal
        # that only gives back what was added since. Raising it is one line, so it is not optional.
        exercised, declared, _ = measure()
        assert exercised <= CHOICES_EXERCISED, (
            f"The sweep sends {exercised} of {declared} declared choices, above the pinned "
            f"{CHOICES_EXERCISED}. Raise CHOICES_EXERCISED to {exercised} so the ratchet keeps "
            "biting at the level coverage actually reached.")
