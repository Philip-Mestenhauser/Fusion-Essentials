# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: data_get - the CLOUD data model (hub -> projects -> folders -> files) in one read.

Every call is a NETWORK round-trip (slow, can fail offline / signed-out / with no active hub) -
deliberately separate from doc_get, which reads the in-memory session. Delegates to the
data_read/data_ops/data_switch_hub handlers so the cloud-error guards and caps live in one place.
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
            include=None, max_depth: int = 4, file: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    inc = _normalize_include(include)
    bad = [s for s in inc if s not in _SLICES]
    if bad:
        return error(f"Unknown include {bad}. Valid: {', '.join(_SLICES)}.")

    have_project = bool(project or project_id)

    # ── scoped to ONE file ───────────────────────────────────────────────────
    if (file or "").strip():
        if inc:
            return error(f"include={inc} does not apply to the 'file' scope (it reads one file's "
                         "record in full). Drop 'file' to use include, or drop include.")
        from . import _data_read as data_read
        out, e = _unwrap(data_read.file_facts_handler(file=file, project=project,
                                                      project_id=project_id, folder=folder))
        if e:
            return e
        out["scope"] = "file"
        out["note"] = (
            "One file's record: metadata, version state and LINK state. Dates are UNIX epoch seconds "
            "(the API's own form) with the UTC ISO string beside each. 'file_extension' is the "
            "DataFile property and is unreliable for a non-CAD upload (an uploaded .txt reads 'sql') "
            "- the file NAME carries the true extension. Link state is read-only here: CREATING a "
            "share or a public link is deliberately not offered by this server, so an unshared file "
            "reports public_link.available=false rather than making one. Next: data_download_file "
            "(non-Fusion files; a design leaves through design_export), data_move_file, doc_open."
        )
        if out.get("name_scope_truncated"):
            out["note"] += (" The name was matched inside a CAPPED listing - files beyond the cap "
                            "were never compared, so another file there could share this name. Pass "
                            "the lineage URN (or a 'folder') to be exact.")
        return ok(out)

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
            if out.get("truncated"):
                out["note"] += (" The walk hit its folder budget (each folder is a slow cloud fetch "
                                "on Fusion's main thread): nodes flagged folders_truncated were not "
                                "descended. Lower max_depth, or list one subtree's files directly "
                                "with 'folder'=<path>.")
            if out.get("time_truncated"):
                out["note"] += (f" The walk stopped after its {int(data_ops._TIME_BUDGET_S)}s time "
                                "budget (a network stall, not the fetch-count cap) - results are "
                                "PARTIAL. Retry, or list one subtree with 'folder'=<path>.")
            return ok(out)
        out, e = _unwrap(data_read.list_project_files_handler(project=project, project_id=project_id,
                                                              folder=folder, recursive=recursive))
        if e:
            return e
        out["scope"] = "files"
        out["note"] = ("Files in the project (each with its lineage URN + openable fusionWebURL). "
                       "'folder'=<path> scopes to one folder; include=['folders'] shows the folder tree "
                       "instead; 'file'=<name|URN> reads ONE file's full record (dates, authors, "
                       "version and link state). (Cloud read - see 'truncated'.)")
        if out.get("time_truncated"):
            at = out.get("time_truncated_at") or "(project root)"
            out["note"] += (f" The walk stopped after its {int(data_read._TIME_BUDGET_S)}s time "
                            f"budget at folder '{at}' (a network stall, not the file/folder cap) - "
                            "results are PARTIAL. Narrow with 'folder'=<path>, or retry.")
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
                   "scope, or include=['folders'] for the tree); 'file'=<name|URN> reads ONE file's full "
                   "record. include=['hubs'] lists all hubs. This is the CLOUD data model (networked); "
                   "for the open-document SESSION see doc_get.")
    if out.get("time_truncated"):
        out["note"] += (f" The listing stopped after its {int(data_read._TIME_BUDGET_S)}s time budget "
                        "(a network stall, not a size cap) - results are PARTIAL. Retry.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Read the CLOUD data model (Autodesk/Fusion Team) in one call, by scope. No 'project': the active "
    "hub + its projects. project=<name|id>: that project's FILES (name, lineage URN, version, openable "
    "fusionWebURL); 'folder'=<path> scopes to one folder, 'recursive' descends or not. "
    "include=['folders'] (with a project): the folder TREE instead. include=['hubs']: all hubs. "
    "'file'=<lineage URN or a name plus its project>: ONE file's full record - dates, authors, version "
    "state, and read-only share/public-link state (creating a share is not offered). Every "
    "call is a NETWORK read (can be slow / fail offline); results are capped (see 'truncated'). For the "
    "in-memory open-document SESSION use doc_get instead."
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
    .add_input_property("file", {"type": "string",
            "description": "ONE file: its lineage URN (or Fusion web URL), or its name - a name needs "
                           "'project' and is refused if several files there share it."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
