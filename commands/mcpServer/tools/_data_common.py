# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared helpers for the cloud data-model tools: hub/project/folder resolution, path splitting,
URN/web-URL identifier decoding, and the one file reference resolver (URN or name-in-a-project).
Used by data_ops.py, doc_lifecycle.py, _data_read.py, doc_open.py, and doc_insert_occurrence.py.
"""

import base64
import re
import time

import adsk.core

from ._common import safe

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it. The
# mechanism behind a clause lives at the symbol itself, in its test, or in VERIFIED_API_FACTS.md.
MAP_BLURB = (
    "cloud data-model helpers shared by data_ops, doc_lifecycle, _data_read, doc_open, "
    "doc_insert_occurrence (hub/project/folder/URN); resolve_file_reference - the ONE "
    "URN-or-name-in-a-project DataFile resolver, which REFUSES a name matching several files; "
    "navigate_folder_path - the ONE folder-PATH walk from a project root, creating nothing: it "
    "hands back the folder and its cleaned path, or the miss triple each caller words its own "
    "refusal from; FUSION_NATIVE_EXTENSIONS/name_extension - the download-refusal fact that the "
    "NAME carries the true extension, fileExtension does not")

app = adsk.core.Application.get()

_UNREAD = object()      # a collection read that RAISED - distinct from one that came back empty

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
    """Return the immediate child folder matching name (case-insensitive), or None.

    First match is CORRECT here: folder names are UNIQUE within a container, so the first match is
    the only match. `dataFolders.add()` with a name a sibling already carries raises
    `3 : CB_NAE - Another object with the same name already exists in this container` - the
    container itself enforces it, below this server's own pre-check. A FILE name carries no such
    rule: see doc_lifecycle._file_in_folder_by_name, which refuses that ambiguity instead.
    """
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


def navigate_folder_path(root, path):
    """Walk a raw folder PATH string from `root`, creating nothing - the ONE folder-path navigation
    every cloud tool scopes a project read/move through.

    Returns (folder, path_string, miss). On success `folder` is the deepest folder and `path_string`
    its cleaned path ("" for `root` itself), miss None. On a miss `folder`/`path_string` are None and
    `miss` carries the facts a refusal names: {'segment' - the segment that did not resolve, 'at' -
    the path of the deepest folder that DID ("(project root)" for `root`), 'available' - that
    folder's subfolder names, or None when the enumeration RAISED}. Each caller words its own refusal
    from them, so one walk serves the file listing, the by-name file resolver and a move destination
    without their nouns converging.

    available=None and available=[] are DIFFERENT answers and no caller may render them alike: a
    folder whose dataFolders enumeration failed is a hole in the search space (the segment may well
    be there), while an empty list means the walk looked and the folder is genuinely childless. The
    same distinction _walk_folder draws with truncated['unread'].
    """
    cur, cur_path = root, ""
    for seg in _split_path(path):
        nxt = _child_folder_by_name(cur, seg)
        if nxt is None:
            # _child_folder_by_name answers None for BOTH 'no such child' and 'the enumeration
            # raised', so the sibling read is taken here with its own sentinel to tell them apart.
            folders = safe(lambda: cur.dataFolders.asArray(), _UNREAD)
            names = (None if folders is _UNREAD
                     else [n for n in (safe(lambda f=f: f.name) for f in folders) if n])
            return None, None, {"segment": seg, "at": cur_path or "(project root)",
                                "available": names}
        # The folder's OWN name, not the segment as typed: the match is case-insensitive, and the
        # path this returns is published as the folder the walk landed in.
        cur_path = f"{cur_path}/{safe(lambda n=nxt: n.name) or seg}" if cur_path else (
            safe(lambda n=nxt: n.name) or seg)
        cur = nxt
    return cur, cur_path, None


def _ensure_folder_path(root, segments, created_out=None):
    """Walk a folder path from `root`, creating any missing segments (mkdir -p).
    Returns (deepest_folder, created_names_list) or raises on failure.

    Pass `created_out` (a list the caller owns) to receive each created name AS it is created: the
    return value is lost when a later segment raises, and the folders already made are real
    mutations the caller has to disclose."""
    cur = root
    created = created_out if created_out is not None else []
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


# Fusion-NATIVE data. The download binding is explicit: DataFile.download handles only non-Fusion
# data and FAILS for an F3D; a design leaves through design_export, a drawing through drawing_export.
FUSION_NATIVE_EXTENSIONS = frozenset({"f3d", "f2d", "f3z"})

# How many sibling names an ambiguity/miss error lists before it says "and N more".
_NAME_HINT_LIMIT = 12


def name_extension(name):
    """The extension a DataFile's NAME carries, lowercased ('' when it carries none).

    The name is the trustworthy source: DataFile.fileExtension is measured WRONG for a non-CAD
    upload (an uploaded .txt read 'sql'), while its name stayed 'probe_note.txt'.
    """
    base = (name or "").strip()
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _looks_like_identifier(raw):
    """True when `raw` is a URN or a Fusion web URL rather than a file NAME. PREFIX-only: a file NAME
    may legally start with 'http' ('httpd-mount.f3d') or carry '://' anywhere in it, and routing such
    a name down the URN path loses the project-scoped name lookup it needed - the miss then reads as
    'no cloud file resolves from ...' instead of listing the project's names."""
    low = (raw or "").strip().lower()
    return low.startswith("urn:") or low.startswith("http://") or low.startswith("https://")


def _name_hint(names):
    """A bounded, readable rendering of the names available at the point of a miss."""
    kept = [n for n in names if n][:_NAME_HINT_LIMIT]
    if not kept:
        return "(none)"
    more = len(names) - len(kept)
    return ", ".join(kept) + (f", and {more} more" if more > 0 else "")


def resolve_file_reference(raw, project="", project_id="", folder=""):
    """Resolve ONE DataFile from `raw`: a lineage URN / Fusion web URL, or a file NAME scoped to a
    project (optionally to a folder path within it). Returns (data_file, meta, err) - exactly one of
    data_file / err is set; meta carries how it resolved ({matched_by, urn, folder_path}).

    A NAME is NOT unique across a project's folders (two folders may each hold a 'notes.txt'), so a
    name matching several files is REFUSED with every candidate's folder path and URN - never the
    first hit. Matching is case-insensitive EXACT on the whole name; a miss lists what IS there.
    """
    from . import _data_read              # deferred: _data_read imports this module

    ident = (raw or "").strip()
    if not ident:
        return None, None, ("Provide 'file' - a lineage URN (or Fusion web URL), or a file NAME plus "
                            "the 'project' it lives in.")

    if _looks_like_identifier(ident):
        df, resolved, tried = _resolve_data_file(ident)
        if not df:
            return None, None, (f"No cloud file resolves from '{ident}'. Tried: {', '.join(tried)}. "
                                "Get a lineage URN from data_get(project=<name>) ('id' on each file).")
        return df, {"matched_by": "urn", "urn": resolved,
                    "folder_path": _folder_path_string(safe(lambda: df.parentFolder))}, None

    if not (project or project_id):
        return None, None, (f"'{ident}' is a file NAME, which is only unique within a project - pass "
                            "'project' (or 'project_id') to scope it, or pass the file's lineage URN "
                            "instead (data_get(project=<name>) lists both).")

    data = safe(lambda: app.data)
    if not data:
        return None, None, "Data not available (not signed in?)."
    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        return None, None, (f"Project not found: {project_id or project}. Available: "
                            f"{', '.join(available) or '(none)'}")
    root = safe(lambda: proj.rootFolder)
    if root is None:
        return None, None, f"Could not access the root folder of project '{safe(lambda: proj.name)}'."

    start, start_path, miss = navigate_folder_path(root, folder)
    if miss:
        # An UNREAD sibling list makes 'not found' an overclaim - the segment may be sitting in a
        # folder listing that never opened, so the refusal says which of the two happened.
        why = (" - the subfolders of that folder could not be READ, so whether the segment is "
               "there is unknown" if miss["available"] is None else "")
        return None, None, (f"Folder '{folder}' not resolved in project "
                            f"'{safe(lambda: proj.name)}' (missing segment '{miss['segment']}' in "
                            f"'{miss['at']}'{why}). See data_get(project=<name>, "
                            "include=['folders']).")

    # ONE traversal: the same capped walk data_get's file listing uses (_data_read._walk_folder) -
    # this resolver only differs in its leaf op, matching a name over the summaries it collects.
    files, truncated = [], {"value": False}
    _data_read._walk_folder(start, files, truncated, depth=0, folder_path=start_path,
                            deadline=time.monotonic() + _data_read._TIME_BUDGET_S)

    want = ident.lower()
    matches = [f for f in files if (f.get("name") or "").strip().lower() == want]
    scope = f"project '{safe(lambda: proj.name)}'" + (f", folder '{start_path}'" if start_path else "")
    capped = (" The listing hit its cap, so files beyond it were not searched - scope with 'folder'."
              if truncated.get("value") else "")
    # A folder that would not enumerate is a hole in the search space, not an empty folder: a second
    # file of this name could be sitting in it, so neither a miss nor a UNIQUE match may be reported
    # as settled without saying so.
    if truncated.get("unread_count"):
        capped += (f" {truncated['unread_count']} folder(s) could not be read and were not searched"
                   + (f" ({', '.join(truncated.get('unread', []))})" if truncated.get("unread") else "")
                   + " - pass the file's id if this answer looks wrong.")

    if not matches:
        return None, None, (f"No file named '{ident}' in {scope}. Files there: "
                            f"{_name_hint([f.get('name') for f in files])}.{capped}")
    if len(matches) > 1:
        rows = "; ".join(f"{m.get('folder_path')} (id {m.get('id')})" for m in matches)
        return None, None, (f"'{ident}' names {len(matches)} files in {scope} - refusing to guess "
                            f"which: {rows}. Pass one of those ids as 'file', or scope with "
                            f"'folder'.{capped}")

    hit = matches[0]
    df, resolved, tried = _resolve_data_file(hit.get("id") or "")
    if not df:
        return None, None, (f"'{ident}' resolved to id {hit.get('id')} in {scope}, but that id does "
                            f"not open as a cloud file. Tried: {', '.join(tried)}.")
    # One match inside a CAPPED listing is not proof of uniqueness - files past the cap were never
    # compared. The flag travels with the result so every caller can say so instead of implying it.
    return df, {"matched_by": "name", "urn": resolved, "folder_path": hit.get("folder_path"),
                "scope_truncated": bool(truncated.get("value")),
                "folders_unreadable": truncated.get("unread_count", 0)}, None
