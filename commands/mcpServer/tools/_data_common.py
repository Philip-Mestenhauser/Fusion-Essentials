# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared helpers for the cloud data-model tools: hub/project/folder resolution, path splitting,
and URN/web-URL identifier decoding. Used by data_ops.py, doc_lifecycle.py, _data_read.py,
doc_open.py, and doc_insert_occurrence.py. See docs/fusion-api-notes.md ("Data model") for the
URN/URL decoding rule.
"""

import base64
import re

import adsk.core

from ._common import safe

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = "cloud data-model helpers shared by data_ops, doc_lifecycle, _data_read, doc_open, doc_insert_occurrence (hub/project/folder/URN)"

app = adsk.core.Application.get()

# Every save made through this server is authored by an AI agent, not a human. Document.save/saveAs
# has no author field, so the version description carries the attribution. _agent_description() is the
# single chokepoint - reuse it wherever a version description is written so the marker is never lost.
AI_AGENT_SAVE_MARKER = "[AI agent]"


def _agent_description(description: str = "") -> str:
    """Prefix a version description with the AI-agent marker (idempotent)."""
    desc = (description or "").strip()
    if desc.startswith(AI_AGENT_SAVE_MARKER):
        return desc
    return f"{AI_AGENT_SAVE_MARKER} {desc}".strip()


def _data():
    d = app.data
    if not d:
        raise RuntimeError("Data not available (not signed in?).")
    return d


def _find_project(data, name=None, project_id=None):
    """Find a project by id or (case-insensitive) name. Returns (project, available_names)."""
    available = []
    for p in data.dataProjects.asArray():
        nm = None
        try:
            nm = p.name
        except Exception:
            pass
        if nm:
            available.append(nm)
        try:
            if project_id and p.id == project_id:
                return p, available
            if name and nm and nm.strip().lower() == name.strip().lower():
                return p, available
        except Exception:
            continue
    return None, available


def _split_path(path):
    """Split a folder path into clean segments, tolerant of / or \\ and stray slashes."""
    if not path:
        return []
    norm = path.replace("\\", "/")
    return [seg.strip() for seg in norm.split("/") if seg.strip()]


def _child_folder_by_name(folder, name):
    """Return the immediate child folder matching name (case-insensitive), or None."""
    want = (name or "").strip().lower()
    try:
        for f in folder.dataFolders.asArray():
            if (safe(lambda: f.name) or "").lower() == want:
                return f
    except Exception:
        pass
    return None


def _resolve_folder_path(root, segments):
    """Walk an existing folder path from `root`. Returns (folder, None) or (None, missing_segment).
    Does NOT create anything. Empty `segments` resolves to `root` itself."""
    cur = root
    for seg in segments:
        nxt = _child_folder_by_name(cur, seg)
        if not nxt:
            return None, seg
        cur = nxt
    return cur, None


def _ensure_folder_path(root, segments):
    """Walk a folder path from `root`, creating any missing segments (mkdir -p).
    Returns (deepest_folder, created_names_list) or raises on failure."""
    cur = root
    created = []
    for seg in segments:
        nxt = _child_folder_by_name(cur, seg)
        if not nxt:
            nxt = cur.dataFolders.add(seg)
            if not nxt:
                raise RuntimeError(f"Failed to create folder segment '{seg}'.")
            created.append(seg)
        cur = nxt
    return cur, created


def _folder_path_string(folder):
    """Build a human-readable path for a folder by walking parentFolder up to root."""
    parts = []
    cur = folder
    seen = 0
    try:
        while cur and seen < 64:
            seen += 1
            if safe(lambda: cur.isRoot, False):
                break
            nm = safe(lambda: cur.name)
            if nm:
                parts.append(nm)
            cur = safe(lambda: cur.parentFolder)
            if not cur:
                break
    except Exception:
        pass
    return "/".join(reversed(parts))


def _b64url_decode(segment):
    """Decode a base64url path segment to text, or None if it isn't valid base64url."""
    s = segment.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)  # restore padding
    try:
        return base64.b64decode(s).decode('utf-8', 'strict')
    except Exception:
        return None


def _urn_candidates(raw):
    """List URN candidates to try for a raw identifier (a URN, or a Fusion web URL).

    For a web URL, the lineage URN is one of the path segments, base64url-encoded
    (e.g. '.../data/<folderURN_b64>/<fileURN_b64>'). Each segment is decoded and any that
    decode to a 'urn:adsk...' string are kept. The raw value itself is always tried first.
    """
    raw = (raw or "").strip()
    seen = []

    def add(c):
        if c and c not in seen:
            seen.append(c)

    # 1) The value as given (covers a plain URN, possibly with a ?version=... suffix).
    add(raw)

    # 2) If it's a URL, decode each path segment and keep decoded 'urn:adsk...' strings.
    if '://' in raw or raw.lower().startswith('http'):
        for seg in re.split(r'[/?#&=]+', raw):
            if len(seg) < 16:
                continue
            decoded = _b64url_decode(seg)
            if decoded and decoded.startswith('urn:adsk'):
                add(decoded)

    # 3) As a last resort, pull any inline 'urn:adsk...' substring out of the raw text.
    for m in re.findall(r'urn:adsk[\w\.\:\-]+', raw):
        add(m)

    return seen


def _resolve_data_file(raw):
    """Resolve a raw identifier (URN or web URL) to (DataFile, resolved_urn, candidates_tried)."""
    candidates = _urn_candidates(raw)
    for cand in candidates:
        df = safe(lambda c=cand: app.data.findFileById(c))
        if df:
            return df, cand, candidates
    return None, None, candidates
