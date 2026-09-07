# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The dump-post reader: Autodesk's dump.cps writes every post event to a .dmp, and this turns one
into the post's parameters plus its motion rows - then judges WHERE the toolpath cut.

Numbers are the dump's own, unconverted; DumpFile.units() hands back the unit rows it states.
read_dump/parse_dump build a DumpFile; envelope, floor, tilt and axis_band are the four verdicts
over its rows, each returning (ok, facts). Pure Python - no adsk, so a test reads a posted file."""

import math
import re

MAP_BLURB = ("the dump-post reader: read_dump/parse_dump turn a dump.cps .dmp into parameters and "
             "motion rows; envelope/floor/tilt/axis_band are the (ok, facts) verdicts over those "
             "rows.")

# One post event per line: "<n>: onName(args)". The dump also writes unnumbered state lines
# (currentSection.*, STATE, tool.holder[i]) between the events - this reader takes the numbered
# lines only, and names every event kind it does not read in DumpFile.skipped.
_EVENT = re.compile(r"^\s*(-?\d+):\s*([A-Za-z0-9_]+)\((.*)\)\s*$")

# wire event -> (kind, xyz start, tool-axis start or None, feed index or None, arc-centre start or
# None), measured: onLinear5D(x, y, z, i, j, k, feed, _) eighth argument unread, onRapid5D, onLinear,
# onRapid, onCircular(clockwise, cx, cy, cz, x, y, z, feed) direction flag unread.
_MOTION = {
    "onRapid5D": ("rapid5d", 0, 3, None, None),
    "onLinear5D": ("linear5d", 0, 3, 6, None),
    "onRapid": ("rapid", 0, None, None, None),
    "onLinear": ("linear", 0, None, 3, None),
    "onCircular": ("circular", 4, None, 7, 1),
}

# The rows envelope and floor judge: a cut has to stay inside the stock and above the floor, while
# a rapid is free to sit above both. An arc is a cut.
CUTTING_KINDS = ("linear5d", "linear", "circular")


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
    """One motion row, or None where the position, tool axis or arc centre did not read as
    numbers."""
    kind, point_at, axis_at, feed_at, centre_at = spec
    starts = [n for n in (point_at, axis_at, centre_at) if n is not None]
    if len(args) < max(starts) + 3:
        return None
    row = {"kind": kind}
    for keys, start in (("xyz", point_at), ("ijk", axis_at), (("cx", "cy", "cz"), centre_at)):
        if start is None:
            continue
        values = [_num(a) for a in args[start:start + 3]]
        if None in values:
            return None
        row.update(dict(zip(keys, values)))
    row["feed"] = _num(args[feed_at]) if feed_at is not None and len(args) > feed_at else None
    return row


def _has_axis(row):
    """True where the row states a tool axis - a 3-axis event states none."""
    return all(key in row for key in "ijk")


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


def _inside(row, box, tol, up_tol):
    """True where a row's point sits within `tol` of the box, `up_tol` above its TOP face alone."""
    lower, upper = box
    point = (row["x"], row["y"], row["z"])
    return all(lower[n] - tol <= v <= upper[n] + (up_tol if n == 2 else tol)
               for n, v in enumerate(point))


def _axis_angle(row):
    """The angle in degrees between a row's tool axis and +Z, or None where the axis has no
    length."""
    length = math.sqrt(sum(row[k] * row[k] for k in "ijk"))
    if not length:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, row["k"] / length))))


def envelope(dump, tol=0.0, up_tol=None):
    """Verdict: every cut sits inside the stock box the dump states, up_tol (default tol) the slack
    above its TOP face alone - all in the dump's units. An arc is judged at its ENDPOINT, so an
    excursion between two endpoints is not seen."""
    box = dump.box("stock")
    up_tol = tol if up_tol is None else up_tol
    rows = [r for r in dump.rows if r["kind"] in CUTTING_KINDS]
    outside = [r for r in rows if not _inside(r, box, tol, up_tol)] if box else []
    facts = {"stock_box": box, "cutting_rows": len(rows), "tol": tol, "up_tol": up_tol,
             "outside_count": len(outside), "first_outside": outside[0] if outside else None}
    return bool(box) and bool(rows) and not outside, facts


def floor(dump, floor_z, tol=0.0):
    """Verdict: the lowest cutting Z is at or above floor_z - floor_z and tol in the dump's units.
    An arc is judged at its ENDPOINT, so a dip between two endpoints is not seen."""
    depths = [r["z"] for r in dump.rows if r["kind"] in CUTTING_KINDS]
    lowest = min(depths) if depths else None
    facts = {"floor_z": floor_z, "tol": tol, "cutting_rows": len(depths), "lowest_z": lowest}
    return lowest is not None and lowest >= floor_z - tol, facts


def tilt(dump, limit_deg=90.0):
    """Verdict: every tool axis the file states lies within limit_deg (degrees) of +Z."""
    angles = [_axis_angle(r) for r in dump.rows if _has_axis(r)]
    measured = [a for a in angles if a is not None]
    # A 3-axis event states no tool axis, so a file holding only those reads 'axis_rows' 0 - a
    # caller that needs the file to BE five-axis reads that count, not the verdict.
    over = [a for a in measured if a > limit_deg]
    facts = {"limit_deg": limit_deg, "axis_rows": len(angles),
             "unreadable_axes": len(angles) - len(measured),
             "max_angle_deg": max(measured) if measured else None}
    return not over and len(measured) == len(angles), facts


def axis_band(dump, target_deg, tol):
    """Verdict: every row states a tool axis, and every one lies within tol of target_deg off +Z."""
    flat = [r for r in dump.rows if not _has_axis(r)]
    angles = [_axis_angle(r) for r in dump.rows if _has_axis(r)]
    measured = [a for a in angles if a is not None]
    facts = {"target_deg": target_deg, "tol": tol, "axis_rows": len(angles),
             "unreadable_axes": len(angles) - len(measured), "rows_without_an_axis": len(flat),
             "angle_span_deg": (min(measured), max(measured)) if measured else None}
    return (bool(angles) and not flat and len(measured) == len(angles)
            and all(abs(a - target_deg) <= tol for a in measured)), facts
