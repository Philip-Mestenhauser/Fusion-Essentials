# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Export-to-disk substrate: filename sanitizing, a component-by-name resolver, a file-landed
verifier, the bounded doEvents wait an asynchronous write lands under, and the
one-file-per-top-level-occurrence split orchestration."""

import os
import time

import adsk.core
import adsk.fusion

from ._common import (safe, counted, all_components, all_occurrences, same_component,
                      named_with_remainder, spelled_as_read, _component_is_named)

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("the export-to-disk substrate design_export and mesh_export share. sanitize/"
             "prepare_out_path - a filename-safe occurrence name, and the output-path prep (strip, "
             "append the format's extension, create the directory); find_component/instance_paths "
             "- the ONE design-wide by-name component resolve, (component, error), matched through "
             "_common._component_is_named so a spelling that resolves in one tool cannot miss in "
             "the next: a shared name is REFUSED naming the occurrence fullPathNames that place "
             "it, and a miss is (None, None) the caller words itself; top_level_occurrences/"
             "split_by_occurrence/failure_detail - the root census a split export writes one file "
             "per (None, never [], when the census could not be taken), the split itself, and its "
             "bounded failure formatter; snapshot + verify_written(before=) - the ONE "
             "prove-THIS-call-wrote-the-file pair: capture (exists, size, mtime) before the write, "
             "refuse a byte-identical pre-existing file after; STL_UNIT_MEMBERS/stl_unit_enum - "
             "the unit key -> DistanceUnits map an STL writer bakes unitType from (NOT MeshUnits, "
             "whose mm/cm ints are swapped); applied_pair/NOT_APPLIED - the ONE export-options "
             "knob writer, which pre-reads the property and answers (landed value, changed); a "
             "bare set-then-getattr cannot bite; pump_until - the CLOCK-BOUNDED doEvents wait for "
             "an asynchronous write, the caller passing its own probe (a TRY-COUNT-bounded pump "
             "stays local)")


def sanitize(name):
    """Make an occurrence name safe for a filename (drop the ':1' instance suffix, swap path/illegal
    chars for '_')."""
    base = (name or "part").split(":")[0]
    out = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in base)
    return out or "part"


def prepare_out_path(file_path, ext):
    """The local output path an export writes to: (path, error). An empty request is (None, None) -
    the caller decides whether that is a refusal or an omitted option.

    Two steps every writer here repeats: surrounding whitespace and quotes come off, `ext` is
    APPENDED when the path does not already end with it (the file's real format is what the name
    must say), and the output directory is created. Call it BEFORE the export runs, so an unusable
    destination refuses without the work."""
    path = (file_path or "").strip().strip('"')
    if not path:
        return None, None
    if ext and not path.lower().endswith(ext.lower()):
        path = path + ext
    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return None, f"Could not create output directory '{out_dir}': {e}"
    return path, None


# The unit vocabulary an STL is written in, and its adsk.fusion.DistanceUnits member. Every writer of
# an STL resolves 'stl_units' through this ONE map: STLExportOptions.unitType takes DistanceUnits, NOT
# MeshUnits (live-verified) - the two enums have their mm/cm ints SWAPPED, so a MeshUnits value here
# silently writes 10x-wrong geometry for the two commonest units.
STL_UNIT_MEMBERS = {
    "mm": "MillimeterDistanceUnits", "cm": "CentimeterDistanceUnits", "m": "MeterDistanceUnits",
    "in": "InchDistanceUnits", "ft": "FootDistanceUnits",
}


def stl_unit_enum(key):
    """The DistanceUnits member for a unit key, or None on a build carrying neither the family nor
    that member - the caller records that as a refusal rather than assigning None."""
    du = safe(lambda: adsk.fusion.DistanceUnits)
    member = STL_UNIT_MEMBERS.get(key)
    if du is None or not member:
        return None
    return safe(lambda: getattr(du, member))


# The (value, changed) pair for a knob nothing was applied for: no landed value, and nothing
# observed. Every caller of applied_pair unpacks this shape.
NOT_APPLIED = (None, False)


def applied_pair(opts, prop, val, key):
    """Set ONE export-options property and read it back, reading it BEFORE the set as well. Returns
    (key, changed) when the property reads the requested value afterwards, else NOT_APPLIED. Every
    writer of an export-options knob goes through this: a bare set-then-getattr is the shape that
    cannot bite.

    'changed' says whether the ASSIGNMENT is what put the value there. A property ALREADY reading
    the requested value answers the same whether the assignment took or was dropped, and the
    pre-read is what tells those apart - without this code knowing any member's int. MEASURED live,
    every knob here has a value that collides with the factory value, and none is the one a reader
    would guess: unitType reads 0 unset and MillimeterDistanceUnits IS 0; meshRefinement reads 1
    unset and MeshRefinementMedium IS 1; isBinaryFormat reads True unset. A BOOLEAN collides on
    whichever of its two values the factory holds, so half its requests cannot be told apart here.

    What 'changed' is WORTH is per property. It hangs on ONE question - does the property's READ
    VALUE determine the written file? - and each knob below answers it from its OWN measurement;
    one knob's answer says nothing about the next. DO NOT make the knobs consistent; the asymmetry
    is what was measured:
      - meshRefinement's read DOES determine it, on STL and OBJ: measured on both, an untouched
        export and an explicit-MEDIUM one are byte-identical, high and low each write their own
        distinct file, and re-exporting the same settings reproduces the same bytes. On 3MF the
        same test cannot be run - see verify_written - though its size response to high and low
        shows refinement takes effect there.
      - isBinaryFormat's read DOES determine it: measured on STL, its factory value is True,
        untouched and explicit-True are byte-identical, and explicit-False writes a distinct,
        larger file with an ASCII 'solid ' header. Re-running True reproduces the same bytes.
      - unitType's read does NOT, and it can stop answering altogether. Measured on STL, the unit an
        UNTOUCHED export writes is STICKY SESSION STATE: it follows the LAST EXPLICIT unitType
        assignment made anywhere in the Fusion session, and it crosses DOCUMENTS - so a writer that
        does not SET unitType inherits the unit of an unrelated earlier export. Across every leg of that measurement the property's read BEFORE the
        assignment was 0, whatever unit the file was written in; the read-back AFTER an assignment
        does return the assigned member, so mm is the one request that collides with the factory
        value (measure_api stl-export-unittype-is-sticky-session-state,
        enum-distance-units-collides-with-factory). The
        read can also stop answering: assigning Design.fusionUnitsManager.distanceDisplayUnits
        POISONS it for that document - unitType then raises RuntimeError '3 : unexpected document
        units' and does not recover when the display units are put back (measure_api
        stl-unittype-read-poisoned-by-units-toggle). A read that names no unit, and that can raise
        instead of answering, cannot be this knob's verification - so 'changed' is.
    Where the read DETERMINES the file, reading the requested value back answers what the caller
    asked whoever put it there, so the caller drops 'changed' (mesh_export's _apply_refinement,
    design_export's _READ_DETERMINES_FILE). Where it does not, 'changed' IS that knob's
    verification and travels on the wire. A knob whose read has NOT been measured against its file
    publishes 'changed' too: claiming a landed value with no evidence is the defect this helper
    exists to close.

    Never raises and never fails the export over a knob that did not stick; the caller reports what
    landed."""
    before = safe(lambda: getattr(opts, prop))
    safe(lambda: setattr(opts, prop, val))
    if safe(lambda: getattr(opts, prop)) != val:
        return NOT_APPLIED               # the read-back disagreed: nothing landed
    return key, before != val


def instance_paths(design, comps):
    """The fullPathName of every occurrence that PLACES one of `comps`, as a list.

    A component name that several components answer to identifies none of them; an occurrence's
    fullPathName identifies ONE placement, and it is the spelling the occurrence vocabularies
    (_inputs._resolve_occurrence, OccurrenceRef) resolve - so this is the candidate list a
    same-name refusal offers instead of asking for a rename. Empty when nothing placed them: a
    component can exist with no occurrence anywhere, and a caller must not be told to pass a
    spelling that was not found. Falls back to an occurrence's plain name where fullPathName does
    not read, so a partial census still names what it saw."""
    out = []
    for o in all_occurrences(design):
        c = safe(lambda o=o: o.component)
        # `is True`, never a bare truth test: same_component answers None where an identity did not
        # read, and every path listed here is offered as a spelling that REACHES one of `comps` - an
        # unproven match would send the caller at an occurrence placing something else.
        if c is None or not any(same_component(c, h) is True for h in comps):
            continue
        p = safe(lambda o=o: o.fullPathName) or safe(lambda o=o: o.name)
        if p:
            out.append(p)
    return out


def find_component(design, name):
    """Resolve ONE Component by name design-wide: (component, error_or_None). The match is EXACT but
    case-insensitive and surrounding whitespace is ignored - _common._component_is_named, the same
    comparison every component SCOPE runs on, so one spelling cannot resolve in one tool and miss
    in the next. That widening cannot COST a resolve: where it hits several, one hit spelled exactly
    as asked for is the answer (_resolve_any_body narrows the same way), so a design holding 'Beta'
    and 'BETA' still addresses each by its own spelling and refuses only 'beta', which names both.

    A name exactly ONE component carries resolves. A name SEVERAL carry is REFUSED, naming the
    occurrence fullPathNames that place them - nothing in this walk makes a component name an
    identifier, so returning the first hit hands back one of several without saying so (an export
    then writes the wrong geometry to disk). The refusal states the collision and its candidates
    ONLY; the remedy belongs to the caller, which is the one that knows which of its own
    vocabularies (a handle, an occurrence path, an active component) still resolves - the same
    split find_setup uses. It never asks for a rename: components arriving from an inserted or
    x-ref'd document duplicate names wholesale, and renaming one means editing a different
    document.

    A name NO component carries is (None, None), so each caller keeps wording its own not-found
    error. A BLANK name matches nothing: a component whose name does not READ reads as "" here, and
    a blank query would otherwise resolve to it.

    Walks _common.all_components (root + all sub-components, root fallback when the collection is
    unreadable) so every by-name component lookup shares the one design-wide walk."""
    want = (name or "").strip()
    if not want:
        return None, None
    hits = [c for c in all_components(design) if _component_is_named(c, want)]
    if len(hits) > 1:
        # Case-insensitive matching WIDENS the hit list, and a widened list must not manufacture an
        # ambiguity: when exactly one hit also matches the spelling asked for, that one is the answer.
        cased = [c for c in hits if (safe(lambda c=c: c.name) or "") == want]
        if len(cased) == 1:
            hits = cased
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, None
    # The names as READ, not the query: a case-insensitive match means the hits can be spelled
    # differently from what was asked for, and those spellings are what tells them apart. Read
    # through _common.spelled_as_read, the ONE de-duplicated capped spelling clause, so this refusal
    # and a sketch scope's cannot report the same set of components with different spellings.
    spelled = spelled_as_read(hits, want)
    paths = named_with_remainder(["'" + p + "'" for p in instance_paths(design, hits)])
    where = f" Instances found: {paths}." if paths else ""
    return None, (f"{len(hits)} components match '{want}'{spelled}, so the name does not identify "
                  f"one of them.{where}")


def snapshot(path):
    """(exists, size_bytes, mtime_ns) for path RIGHT NOW - the baseline a write is proven against.
    Take it BEFORE the export runs and hand it to verify_written; a target that does not exist yet is
    (False, 0, 0)."""
    exists = bool(safe(lambda: os.path.isfile(path), False))
    if not exists:
        return False, 0, 0
    st = safe(lambda: os.stat(path))
    if st is None:
        return True, 0, 0
    return True, safe(lambda: st.st_size, 0) or 0, safe(lambda: st.st_mtime_ns, 0) or 0


def verify_written(path, before=None):
    """Confirm THIS call wrote a non-empty file at path. Returns (size_bytes, note): note is None when
    the file is there, non-empty, and provably not the one that was already there.

    execute() returning true is not proof a file was written - and a file EXISTING is not proof either
    when a stale one from an earlier export sits at the same path. `before` is that path's snapshot()
    taken before the write: when the target already existed and BOTH its size and its modification
    time are unchanged, this call produced nothing and the stale file is reported as the failure it
    is. Passing no `before` keeps the weaker exists-and-non-empty check, for a caller that has no
    before-state to offer (a redundant re-stat after the handler already proved the write). The
    unchanged test errs toward refusing: a re-export whose bytes AND timestamp both land identical
    reads as "wrote nothing" rather than confirming a write it cannot see.

    3MF IS NOT REPRODUCIBLE, in SIZE and not only in content (measured: three exports of one body at
    identical settings came back 1643, 1647 and 1646 bytes - it is a zip container and zips embed
    timestamps). That is what makes this check safe on 3MF ON ITS OWN TERMS, since it compares size
    and mtime rather than content: the size moves, so a 3MF re-export never reads as "wrote nothing"
    even on a coarse-timestamp filesystem. The same fact is a trap the other way: any test, sweep
    predicate or postcondition comparing TWO 3MF exports by size or hash will flake, and a size or
    byte difference between two 3MF files is never evidence that a setting took."""
    exists, size, mtime = snapshot(path)
    if not exists or not size:
        return 0, f"no file was written to '{path}' (file_exists={exists}, size_bytes={size})"
    if before is not None and before[0] and (size, mtime) == (before[1], before[2]):
        return 0, (f"the file at '{path}' is the one that was already there before this call - its "
                   f"size ({size} bytes) and modification time are both unchanged, so this export "
                   f"wrote nothing. (On a coarse-timestamp filesystem - FAT/exFAT, some network "
                   f"shares - a byte-identical re-export can read the same way; if that is this "
                   f"case, delete the target file and export again)")
    return size, None


def pump_until(probe, timeout_s, poll_sleep):
    """Pump the main thread until probe() reports its signal settled, bounded by timeout_s.

    An asynchronous Fusion write (a setup sheet, an exported drawing, a cloud version) only advances
    while the main thread is pumped, so the wait pumps adsk.doEvents rather than sleeping through it.
    probe() -> (settled, reading): the caller's OWN signal and whatever it just read. Returns
    (settled, reading) with the LAST reading either way, so a caller words its give-up from what it
    actually read. probe() runs BEFORE the first pump and once more after every pump; the bound is
    checked between the two, so a probe that is already settled costs no pump at all."""
    deadline = time.monotonic() + timeout_s
    while True:
        settled, reading = probe()
        if settled:
            return True, reading
        if time.monotonic() >= deadline:
            return False, reading
        safe(lambda: adsk.doEvents())
        time.sleep(poll_sleep)


def top_level_occurrences(design):
    """The root component's top-level occurrences as a plain list, or None when the census could not
    be taken - the collection, its count, or one of its items did not read.

    [] and None are DIFFERENT answers: [] is a read that succeeded and found no occurrence, None is
    "which occurrences exist is unknown". A split export folds the two together at its own cost - it
    would report a clean zero-file result over a design whose components it simply could not see -
    so the caller refuses on None instead of exporting a short (or empty) file set."""
    root = safe(lambda: design.rootComponent)
    occs = safe(lambda: root.occurrences) if root is not None else None
    n = counted(lambda: occs.count) if occs is not None else None
    if n is None:
        return None
    out = []
    for i in range(n):
        occ = safe(lambda i=i: occs.item(i))
        if occ is None:
            return None       # a hole in the census: N-1 files would read as the whole design
        out.append(occ)
    return out


_MAX_DETAILED_FAILURES = 5


def failure_detail(errors, limit=_MAX_DETAILED_FAILURES):
    """The per-occurrence reasons from split_by_occurrence's error list, as one line for an error
    message. Bounded: a 200-part design whose every write failed reports a readable handful plus the
    remaining count, not a wall of identical text.

    It renders its own cap rather than going through _common.named_with_remainder because each row
    here is not a NAME but 'occurrence: <free-text reason>' - whatever write_one handed back, a
    string that may itself contain the ', ' that helper joins on, in which case nothing in the line
    marks where one row ends. '; ' is the separator that still does. The rule that matters is kept:
    the remainder is COUNTED, never silently dropped."""
    shown = [f"{e.get('occurrence') or '(unnamed occurrence)'}: {e.get('error')}"
             for e in errors[:limit]]
    more = len(errors) - len(shown)
    return "; ".join(shown) + (f" (+{more} more)" if more > 0 else "")


def split_by_occurrence(occs, out_dir, ext, write_one):
    """Write one file per occurrence in occs via write_one(occ, path) -> (size_bytes_or_None,
    error_or_None). Filenames are sanitized occurrence names with ext appended, de-duplicated when
    two occurrences sanitize to the same stem. Returns (files, errors): lightweight per-occurrence
    records for whichever list its write landed in."""
    files, errors, used = [], [], {}
    for occ in occs:
        name = safe(lambda occ=occ: occ.name)
        stem = sanitize(name)
        used[stem] = used.get(stem, 0) + 1
        if used[stem] > 1:
            stem = f"{stem}_{used[stem]}"
        path = os.path.join(out_dir, stem + ext)
        size, eerr = write_one(occ, path)
        if eerr:
            errors.append({"occurrence": name, "error": eerr})
        else:
            files.append({"occurrence": name, "file_path": path, "size_bytes": size})
    return files, errors
