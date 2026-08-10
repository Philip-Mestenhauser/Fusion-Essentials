# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Export-to-disk substrate: filename sanitizing, a component-by-name resolver, a file-landed
verifier, the bounded doEvents wait an asynchronous write lands under, and the
one-file-per-top-level-occurrence split orchestration."""

import os
import time

import adsk.core

from ._common import safe, all_components

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("sanitize/component_by_name/verify_written/split_by_occurrence - the export-to-disk "
             "substrate shared by design_export + mesh_export; prepare_out_path - the ONE "
             "output-path prep (strip, append the format's extension, create the directory); "
             "pump_until - the shared "
             "CLOCK-BOUNDED doEvents-pumping wait for an asynchronous write (the caller passes its "
             "own probe - a file appearing, a size going stable, a version tip advancing - and "
             "words its own give-up); a TRY-COUNT-bounded pump stays local")


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


def component_by_name(design, name):
    """A Component by EXACT name, or None. Walks _common.all_components (root + all sub-components,
    root fallback when the collection is unreadable) so every by-name component lookup shares the
    one design-wide walk."""
    for c in all_components(design):
        if (safe(lambda c=c: c.name) or "") == name:
            return c
    return None


def verify_written(path):
    """Confirm a non-empty file landed at path. Returns (size_bytes, note): note is None when the
    file exists and is non-empty; execute() returning true is not proof a file was written."""
    exists = bool(safe(lambda: os.path.isfile(path), False))
    size = safe(lambda: os.path.getsize(path), 0) if exists else 0
    if not exists or not size:
        return 0, f"no file was written to '{path}' (file_exists={exists}, size_bytes={size})"
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
    """The root component's top-level occurrences, as a plain list (empty if none/unreadable)."""
    root = design.rootComponent
    occs = safe(lambda: root.occurrences)
    if not occs:
        return []
    return [occs.item(i) for i in range(occs.count)]


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
