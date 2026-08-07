# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Download ONE non-Fusion cloud file (DataFile) to a local folder.

DataFile.download handles only non-Fusion data and FAILS for an F3D, so Fusion designs are refused
here with a pointer to design_export. The synchronous form used below (handler=None) FREEZES Fusion
until the transfer finishes.
"""

import os

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._data_common import (FUSION_NATIVE_EXTENSIONS, name_extension, resolve_file_reference,
                           _folder_path_string)
from . import _assert
from . import _outputs

# What this tool RETURNS. size_bytes is SUPPLIED by the FileLanded postcondition's evidence (it stats
# the path once), so the handler does not re-stat it.
RETURNS = [
    _outputs.ReturnsValue("file_path", "the local path written"),
    _outputs.ReturnsValue("size_bytes", "the file's size on disk (proof it landed)"),
]


def handler(file: str = "", project: str = "", folder: str = "", destination_folder: str = "",
            file_name: str = "", overwrite: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    dest = (destination_folder or "").strip().strip('"')
    if not dest:
        return error("Provide 'destination_folder' - the LOCAL folder to write the file into.")

    df, meta, err = resolve_file_reference(file, project=project, folder=folder)
    if err:
        return error(err)

    name = safe(lambda: df.name) or ""
    # The two signals disagree in OPPOSITE directions, both measured live, which is why the refusal
    # reads them in this order. A non-CAD upload: name 'probe_note.txt' (true), fileExtension 'sql'
    # (wrong) - so the NAME leads. A Fusion design: name 'Gyroscope' (no extension, no signal at all),
    # fileExtension 'f3d' - so an extensionless name is exactly when fileExtension must be consulted,
    # and that fallback is the branch a design's refusal travels.
    ext = name_extension(name) or (safe(lambda: df.fileExtension) or "").strip().lower()
    if ext in FUSION_NATIVE_EXTENSIONS:
        return error(f"'{name}' is Fusion-native data (.{ext}) and cannot be downloaded: "
                     "DataFile.download handles only non-Fusion files. Open it (doc_open) and export "
                     "instead - design_export for a design, drawing_export for a drawing, mesh_export "
                     "for a mesh.")

    out_name = (file_name or "").strip().strip('"') or name
    if not out_name:
        return error("The cloud file's name could not be read - pass 'file_name' to choose the local "
                     "filename explicitly.")
    if os.path.basename(out_name) != out_name:
        return error(f"'file_name' must be a bare filename, not a path: '{out_name}'. The folder "
                     "comes from 'destination_folder'.")
    path = os.path.join(dest, out_name)

    # A file already at the target makes "it exists on disk" prove nothing: a stale file satisfies the
    # landed check for a download that never wrote. So an existing path is refused, and an authorized
    # overwrite REMOVES it first - either way the landed check stays real evidence.
    removed = False                 # the OBSERVED removal, not the caller's overwrite flag
    if os.path.isfile(path):
        if not overwrite:
            return error(f"'{path}' already exists. Pass overwrite=true to replace it, or set "
                         "'file_name'. (Refusing keeps a stale file from being reported as this "
                         "download's result.)")
        try:
            os.remove(path)
        except Exception as ex:
            return error(f"Could not replace the existing '{path}': {ex}")
        removed = True

    if not os.path.isdir(dest):
        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as ex:
            return error(f"Could not create destination folder '{dest}': {ex}")

    try:
        # handler=None is the SYNCHRONOUS form: this call does not return until the transfer is done
        # or has failed, and Fusion is frozen throughout.
        did = df.download(path, None)
    except Exception as ex:
        return error(f"Download failed for '{name}': {ex}")
    if not did:
        return error(f"DataFile.download returned false for '{name}' - nothing was downloaded. "
                     "Fusion designs cannot be downloaded (use design_export); check the file is "
                     "fully processed (data_get(file=...) reports state.is_complete).")

    note = ("Downloaded synchronously (Fusion was frozen for the transfer) and gated on a non-empty "
            "file landing on disk - see size_bytes.")
    if (meta or {}).get("scope_truncated"):
        note += (" Matched by NAME inside a capped listing - files beyond the cap were never "
                 "compared, so check 'source' is the file you meant; a lineage URN is exact.")

    return ok({
        "downloaded": True,
        "name": name,
        "file_path": path,
        "source": {"project": safe(lambda: df.parentProject.name),
                   "folder_path": _folder_path_string(safe(lambda: df.parentFolder)) or "(project root)",
                   "id": (meta or {}).get("urn")},
        "overwrote_existing": removed,
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Download ONE non-Fusion cloud file to a local folder. Fusion-native data is REFUSED: a design "
    "leaves through design_export, a drawing through drawing_export. The transfer is SYNCHRONOUS and "
    "FREEZES Fusion until it finishes - small files only. The local name defaults to the cloud name; "
    "an existing local file is refused unless overwrite=true (it is removed first, so success means "
    "THIS download landed).\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="data_download_file", description=TOOL_DESCRIPTION)
    .add_input_property("file", {"type": "string",
            "description": "Lineage URN (from data_get), or the file's name - a name needs 'project' "
                           "and is refused if several files there share it."})
    .add_input_property("project", {"type": "string",
            "description": "Project holding the file; required when 'file' is a name."})
    .add_input_property("folder", {"type": "string",
            "description": "Cloud folder path scoping a by-name lookup."})
    .add_input_property("destination_folder", {"type": "string",
            "description": "LOCAL folder to write into; created if missing."})
    .add_input_property("file_name", {"type": "string",
            "description": "Local filename (bare, no path). Default: the cloud name."})
    .add_input_property("overwrite", {"type": "boolean",
            "description": "Replace an existing local file (default false = refuse)."})
    .strict_schema()
)

# enforce_timeout=False: the synchronous download is an uninterruptible main-thread transfer that can
# run past the server's call timeout; the file-landed gate is the real proof, so we wait for it rather
# than false-failing a download that is still writing.
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             enforce_timeout=False,
                             postconditions=[_assert.FileLanded("file_path")])


def register_tool():
    register(item)
