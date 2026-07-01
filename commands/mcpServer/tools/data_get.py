# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: data_get - the CLOUD data model (hub -> projects -> folders -> files) in one read.

The "rich read" pattern (CLAUDE.md "Reads are RICH"): a light default, then scope/include to go deeper.
data_get reads the CLOUD data model on Autodesk/Fusion Team - so every call is a NETWORK round-trip
(slow, can fail offline / when not signed in / with no active hub). This is deliberately separate from
doc_get, which reads the in-memory SESSION: folding them would hide whether a call is free or a slow
remote query.

The cloud is a hierarchy (hub -> project -> folder -> file) where each level needs its parent's id, so
data_get drills by SCOPE PARAMETER, not by include= alone:
  default (no project)   -> the active hub + its projects. "Where am I, what projects exist."
  project=<name|id>      -> that project's FILES (name, lineage URN, version, openable web URL).
    folder=<path>        -> scope the file listing to one folder ('recursive' to descend or not).
  project + include=['folders']  -> the project's FOLDER TREE instead of files.
  include=['hubs']       -> all hubs (the multi-hub picker; switch from the Fusion data panel).

Each level is bounded (file/folder caps + a 'truncated' flag) so a huge project can't blow the budget.
The handler delegates to the data_read/data_ops/data_switch_hub handlers, so the cloud-error guards and caps
live in one place. Read-only.
"""

import json

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error

_SLICES = ("hubs", "folders")


def _unwrap(result):
    """(payload, None) on ok; (None, error_result) on error - so a cloud failure propagates verbatim."""
    if result.get("isError"):
        return None, result
    try:
        return json.loads(result["content"][0]["text"]), None
    except Exception:
        return None, result


def _normalize_include(include):
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


def handler(project: str = "", project_id: str = "", folder: str = "", recursive: bool = True,
            include=None, max_depth: int = 4) -> dict:
    """Read the cloud data model at the right scope (rich read - networked; CLAUDE.md "Reads are RICH").

    No project: the active hub + its projects. project=<name|id>: that project's files ('folder' scopes
    to one folder; 'recursive' descends or not). include=['folders'] (with a project): the folder tree
    instead of files. include=['hubs']: all hubs. Each level is capped (see 'truncated'). Read-only.
    """
    inc = _normalize_include(include)
    bad = [s for s in inc if s not in _SLICES]
    if bad:
        return error(f"Unknown include {bad}. Valid: {', '.join(_SLICES)}.")

    have_project = bool(project or project_id)

    # ── scoped to a project ──────────────────────────────────────────────────
    if have_project:
        from . import data_ops, _data_read as data_read
        if "folders" in inc:
            out, e = _unwrap(data_ops.list_folders_handler(project=project, project_id=project_id,
                                                           max_depth=max_depth))
            if e:
                return e
            out["scope"] = "folders"
            out["note"] = ("Folder tree of the project. Pass a 'folder' path + drop include=['folders'] "
                           "to list that folder's FILES. (Cloud read - see 'truncated'.)")
            return ok(out)
        out, e = _unwrap(data_read.list_project_files_handler(project=project, project_id=project_id,
                                                              folder=folder, recursive=recursive))
        if e:
            return e
        out["scope"] = "files"
        out["note"] = ("Files in the project (each with its lineage URN + openable fusionWebURL). "
                       "'folder'=<path> scopes to one folder; include=['folders'] shows the folder tree "
                       "instead. (Cloud read - see 'truncated'.)")
        # a file listing's dominant next action is to OPEN one - name doc_open so the breadcrumb from
        # 'here are the files' to 'open this one by id' is explicit (present-only: only when files exist).
        if out.get("files"):
            out["pointers"] = {"open": "doc_open(file_id=<a file's 'id'>, force_api_open=true) to open one."}
        return ok(out)

    # ── no project: orient at the hub level ──────────────────────────────────
    if "hubs" in inc:
        from . import data_switch_hub
        out, e = _unwrap(data_switch_hub.handler(action="list"))
        if e:
            return e
        out["scope"] = "hubs"
        out["note"] = ("All hubs (is_active flags the current one). Switch from the Fusion data panel - "
                       "Data.activeHub is read-only in the API. Then pass project=<name> to list files.")
        return ok(out)

    from . import _data_read as data_read
    out, e = _unwrap(data_read.list_projects_handler())
    if e:
        return e
    out["scope"] = "projects"
    out["note"] = ("Active hub + its projects. Pass project=<name|id> to list its FILES (add 'folder' to "
                   "scope, or include=['folders'] for the tree). include=['hubs'] lists all hubs. This is "
                   "the CLOUD data model (networked); for the open-document SESSION see doc_get.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Read the CLOUD data model (Autodesk/Fusion Team) in one call, by scope. No 'project': the active "
    "hub + its projects. project=<name|id>: that project's FILES (name, lineage URN, version, openable "
    "fusionWebURL); 'folder'=<path> scopes to one folder, 'recursive' descends or not. "
    "include=['folders'] (with a project): the folder TREE instead. include=['hubs']: all hubs. Every "
    "call is a NETWORK read (can be slow / fail offline); results are capped (see 'truncated'). For the "
    "in-memory open-document SESSION use doc_get instead. Read-only."
)

tool = (
    Tool.create_simple(name="data_get", description=TOOL_DESCRIPTION)
    .add_input_property("project", {"type": "string", "description": "Project name (case-insensitive) to scope to."})
    .add_input_property("project_id", {"type": "string", "description": "Project id (alternative to name)."})
    .add_input_property("folder", {"type": "string",
            "description": "With a project: a folder PATH (e.g. 'Parts/Fixtures') to scope the file listing to."})
    .add_input_property("recursive", {"type": "boolean",
            "description": "With 'folder': descend into subfolders (default true) or list only immediate files (false)."})
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper scope: 'hubs' (all hubs) or 'folders' (a project's folder tree, with a project). "
                           "A list or comma-string. Omit for projects (no project) or files (with a project)."})
    .add_input_property("max_depth", {"type": "integer",
            "description": "With include=['folders']: folder-tree depth cap (default 4)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
