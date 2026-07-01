# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared helpers for the cloud data-model tools (split out of the former data_management.py).

data_management.py grew to ~1400 lines holding ~14 tools. It was split into two cohesive modules -
data_model_ops.py (projects / folders / upload / delete-folder) and doc_lifecycle.py (new / save /
save_as / copy / close / activate / list-open / delete-file). The helpers BOTH groups use live here so
neither module owns them and there's no duplication.

Grounded in adsk.core: app.data (DataHub) -> dataProjects / DataProject.rootFolder / DataFolder tree.
"""

import adsk.core

from ._common import safe

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = "cloud data-model helpers shared by data_model_ops + doc_lifecycle (hub/project/folder/URN)"

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
    want = name.strip().lower()
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
