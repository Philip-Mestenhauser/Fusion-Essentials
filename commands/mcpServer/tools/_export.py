# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Export-to-disk substrate: filename sanitizing, a component-by-name resolver, a file-landed
verifier, and the one-file-per-top-level-occurrence split orchestration."""

import os

from ._common import safe, all_components

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("sanitize/component_by_name/verify_written/split_by_occurrence - the export-to-disk "
             "substrate shared by design_export + mesh_export")


def sanitize(name):
    """Make an occurrence name safe for a filename (drop the ':1' instance suffix, swap path/illegal
    chars for '_')."""
    base = (name or "part").split(":")[0]
    out = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in base)
    return out or "part"


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
