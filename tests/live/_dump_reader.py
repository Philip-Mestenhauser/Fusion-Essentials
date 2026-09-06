# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The dump-post reader: Autodesk's dump.cps writes every post event to a .dmp, and this turns one
into the post's parameters plus its FIVE-AXIS motion rows - then judges WHERE the toolpath cut.

Numbers are the dump's own, unconverted; DumpFile.units() hands back the unit rows it states.
read_dump/parse_dump build a DumpFile; envelope, floor and tilt are the three verdicts over its
rows, each returning (ok, facts). Pure Python - no adsk, so an offline test reads a posted file."""

import math
import re

MAP_BLURB = ("the dump-post reader: read_dump/parse_dump turn a dump.cps .dmp into parameters and "
             "motion rows; envelope/floor/tilt are the three (ok, facts) verdicts over those rows.")

# One post event per line: "<n>: onName(args)". The dump also writes unnumbered state lines
# (currentSection.*, STATE, tool.holder[i]) between the events - this reader takes the numbered
# lines only, and names every event kind it does not read in DumpFile.skipped.
_EVENT = re.compile(r"^\s*(-?\d+):\s*([A-Za-z0-9_]+)\((.*)\)\s*$")

# wire event -> (row kind, index of the feed argument or None), for the shapes a posted five-axis
# dump measured: x/y/z are the first three arguments and a unit tool axis the next three, then
# onLinear5D's feed. Its eighth argument is unread. Every other event lands in DumpFile.skipped.
_MOTION = {
    "onRapid5D": ("rapid5d", None),
    "onLinear5D": ("linear5d", 6),
}

# The rows envelope and floor judge: a cut has to stay inside the stock and above the floor, while
# a rapid is free to sit above both.
CUTTING_KINDS = ("linear5d",)


def _split_args(text):
    """One event's comma-separated arguments, a comma inside a quoted value kept whole."""
    args, buf, quoted = [], [], False
    for ch in text:
        if quoted:
            quoted = ch != "'"
        elif ch == "'":
            quoted = True
        elif ch == ",":
            args.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    return args + [tail] if args or tail else []


def _value(text):
    """One argument as the dump states it: a quoted string unquoted, a number as a number."""
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def _num(text):
    """One argument as a float, or None where it is not a number."""
    try:
        return float(text)
    except ValueError:
        return None


def _row(spec, args):
    """One motion row, or None where the position or the tool axis did not read as numbers."""
    kind, feed_at = spec
    if len(args) < 6:
        return None
    values = [_num(a) for a in args[:6]]
    if None in values:
        return None
    feed = _num(args[feed_at]) if feed_at is not None and len(args) > feed_at else None
    return {"kind": kind, "x": values[0], "y": values[1], "z": values[2],
            "i": values[3], "j": values[4], "k": values[5], "feed": feed}


class DumpFile:
    """One parsed .dmp: the post's parameters, its motion rows, and the events left unread."""

    def __init__(self, parameters, rows, skipped):
        self.parameters = parameters
        self.rows = rows
        self.skipped = skipped

    @property
    def strategy(self):
        """The operation's strategy id as the dump states it, or None."""
        return self.parameters.get("operation-strategy")

    def units(self):
        """The two unit rows the dump states, uninterpreted - what a caller compares before it
        judges any distance this file carries."""
        return {key: self.parameters.get(key)
                for key in ("operation:metric", "operation:tool_unit")}

    def box(self, prefix):
        """The (lower, upper) xyz the dump states under '<prefix>-lower-*'/'<prefix>-upper-*'."""
        corners = []
        for end in ("lower", "upper"):
            values = [self.parameters.get(f"{prefix}-{end}-{axis}") for axis in "xyz"]
            if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values):
                return None
            corners.append(tuple(float(v) for v in values))
        return tuple(corners)


def parse_dump(text):
    """A dump post's text as a DumpFile."""
    parameters, rows, skipped = {}, [], {}
    for line in text.split("\n"):
        match = _EVENT.match(line)
        if match is None:
            continue
        event, args = match.group(2), _split_args(match.group(3))
        row = _row(_MOTION[event], args) if event in _MOTION else None
        if event == "onParameter" and len(args) >= 2:
            parameters[_value(args[0])] = _value(args[1])
        elif row is not None:
            rows.append(row)
        else:
            skipped[event] = skipped.get(event, 0) + 1
    return DumpFile(parameters, rows, skipped)


def read_dump(path):
    """The .dmp at `path` as a DumpFile."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_dump(fh.read())


def _inside(row, box, tol):
    """True where a row's point sits within `tol` of the box - on a face counts as inside."""
    lower, upper = box
    return all(lower[n] - tol <= v <= upper[n] + tol
               for n, v in enumerate((row["x"], row["y"], row["z"])))


def _axis_angle(row):
    """The angle in degrees between a row's tool axis and +Z, or None where the axis has no
    length."""
    length = math.sqrt(sum(row[k] * row[k] for k in "ijk"))
    if not length:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, row["k"] / length))))


def envelope(dump, tol=0.0):
    """Verdict: every cut sits inside the stock box the dump states; tol is in the dump's units."""
    box = dump.box("stock")
    rows = [r for r in dump.rows if r["kind"] in CUTTING_KINDS]
    outside = [r for r in rows if not _inside(r, box, tol)] if box else []
    facts = {"stock_box": box, "cutting_rows": len(rows), "tol": tol,
             "outside_count": len(outside), "first_outside": outside[0] if outside else None}
    return bool(box) and bool(rows) and not outside, facts


def floor(dump, floor_z, tol=0.0):
    """Verdict: the lowest cutting Z is at or above floor_z - floor_z and tol in the dump's units."""
    depths = [r["z"] for r in dump.rows if r["kind"] in CUTTING_KINDS]
    lowest = min(depths) if depths else None
    facts = {"floor_z": floor_z, "tol": tol, "cutting_rows": len(depths), "lowest_z": lowest}
    return lowest is not None and lowest >= floor_z - tol, facts


def tilt(dump, limit_deg=90.0):
    """Verdict: every row's tool axis lies within limit_deg (degrees) of +Z."""
    angles = [_axis_angle(r) for r in dump.rows]
    measured = [a for a in angles if a is not None]
    # Every row this reader parses is a 5D event, so a file with no 5D event at all reads
    # 'axis_rows' 0 - a caller that needs the file to BE five-axis reads that count, not the verdict.
    over = [a for a in measured if a > limit_deg]
    facts = {"limit_deg": limit_deg, "axis_rows": len(angles),
             "unreadable_axes": len(angles) - len(measured),
             "max_angle_deg": max(measured) if measured else None}
    return not over and len(measured) == len(angles), facts
