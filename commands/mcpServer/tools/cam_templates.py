# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read (cam_get(include=['templates'])) and write (cam_apply_template, cam_save_template,
cam_delete_template) the CAM toolpath template library. cam_save_template always creates a new
template; it does not overwrite an existing one."""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import iter_collection, named_with_remainder, ok, error, safe
# The shared CAM substrate: the ONE bounded library folder walk (and its collect-the-assets
# projection), plus the ONE leafName-or-stem asset matcher every library DELETE addresses its
# target with - the same reads cam_delete_machine resolves on.
from ._cam_common import (asset_key, asset_leaf, asset_leaf_keys, assets_named, get_cam, find_setup,
                          library_assets, library_children, tree_nodes, walk_library_folders)
from . import _inputs

app = adsk.core.Application.get()

# Friendly location name -> the LibraryLocations enum MEMBER name. The member is resolved via getattr
# on the enum (the enum owns the value), never a hand-coded int - matching cam_edit_setup /
# cam_edit_tools / cam_post.
_LOCATION_MEMBERS = {
    "local": "LocalLibraryLocation",
    "cloud": "CloudLibraryLocation",
    "network": "NetworkLibraryLocation",
    "samples": "OnlineSamplesLibraryLocation",
    "external": "ExternalLibraryLocation",
    "fusion": "Fusion360LibraryLocation",
    "hub": "HubLibraryLocation",
}


def _location_enum(key):
    """The LibraryLocations enum member for a friendly location key, or None when the key is unknown
    OR this Fusion build has no such member (getattr on the enum member, not a hand-coded int)."""
    member = _LOCATION_MEMBERS.get((key or "").lower())
    if not member:
        return None
    return getattr(adsk.cam.LibraryLocations, member, None)


# The wire-validated selector for a library location: its enum carries the legal names, so an unknown
# location fails at the schema instead of the handler re-listing them (see honesty contract).
_LOCATION = _inputs.Choice("location", options=list(_LOCATION_MEMBERS), default="cloud",
                           description="Which template library to read/write.")

_MAX_NODES = 1500

_TREE_NOTE = (
    "A row's 'url' is what cam_apply_template(template_url=...) takes. url_basis "
    "'folder_position' means the asset at that template's INDEX in its folder - how the shipped "
    "hole templates are addressed, their leafNames spelling other than their names; alignment "
    "measured on 2705.1.4 (measure_api cam-template-asset-index-alignment). 'templates_collided' "
    "counts arrivals dropped as already listed.")


def _template_library():
    """(templateLibrary, None) or (None, reason) - the library manager lives on the CAMManager
    singleton, not on the CAM product."""
    try:
        mgr = adsk.cam.CAMManager.get()
    except Exception as e:
        return None, f"Could not access CAMManager: {e}"
    if not mgr:
        return None, "CAMManager not available."
    try:
        lib = mgr.libraryManager.templateLibrary
    except Exception as e:
        return None, f"Could not access the template library: {e}"
    if not lib:
        return None, "Template library not available."
    return lib, None


# ---------------------------------------------------------------------------
# template listing engine -> cam_get(include=['templates'])
# ---------------------------------------------------------------------------

def list_cam_templates_handler(location: str = "cloud", url: str = "", max_depth: int = 4) -> dict:
    """Navigate the template library. Start at a location root (or a folder 'url')."""
    lib, err = _template_library()
    if err:
        return error(err)

    # Resolve the starting URL: explicit url wins, else the named location's root.
    start_url = None
    if url.strip():
        start_url = safe(lambda: adsk.core.URL.create(url.strip()))
        if not start_url:
            return error(f"Invalid library URL: '{url}'.")
    else:
        loc_key, lerr = _LOCATION.resolve(location)
        if lerr:
            return error(lerr)
        loc = _location_enum(loc_key)
        if loc is None:
            return error(f"Location '{loc_key}' is not available in this Fusion build.")
        start_url = safe(lambda: lib.urlByLocation(loc))
        if not start_url:
            return error(f"Could not resolve the '{location}' library root "
    "(it may not be configured/available).")

    try:
        depth = max(1, min(int(max_depth), 8))
    except Exception:
        depth = 4

    counter = {"n": 0, "truncated": False}
    try:
        tree = _walk_library(lib, start_url, 0, depth, counter)
    except Exception as e:
        return error(f"Could not read the template library: {e}")

    return ok({
    "location": location if not url.strip() else None,
    "root_url": safe(lambda: start_url.toString()),
    "node_count": counter["n"],
    "truncated": counter["truncated"],
    "tree": tree,
    "note": _TREE_NOTE,
    })


def _asset_index(lib, folder_url):
    """(urls, by_name) for ONE folder's child assets: the url strings in library order, and every
    url each lowercased name answers to - asset_leaf_keys is the shared leafName/stem reading."""
    assets = library_children(lib, folder_url, "childAssetURLs")
    urls = []
    by_name = {}
    for u in assets:
        text = safe(lambda u=u: u.toString())
        if not text:
            continue
        urls.append(text)
        for key in asset_leaf_keys(u):
            by_name.setdefault(key, []).append(text)
    return urls, by_name


def _folder_templates(lib, folder_url, by_name):
    """(rows, collided) for one folder - [(name, template, url_or_None)] keyed on the ASSET a name
    resolves to, so two arrivals of ONE asset become one row (counted in `collided`) while a name
    ONE asset does not answer keeps every arrival."""
    rows, seen, collided = [], set(), 0
    for t in library_children(lib, folder_url, "childTemplates"):
        name = safe(lambda t=t: t.name)
        urls = by_name.get((name or "").lower()) or []
        # One asset answering the name IS this row's identity. Several (or none) leave the arrivals
        # unidentified, and two unidentified arrivals are not shown to be one template.
        url = urls[0] if len(urls) == 1 else None
        if url is not None and url in seen:
            collided += 1
            continue
        if url is not None:
            seen.add(url)
        rows.append((name, t, url))
    return rows, collided


def _walk_library(lib, folder_url, depth, max_depth, counter):
    """Recursively summarize a library folder: its templates + subfolders."""
    node = {
    "folder": safe(lambda: lib.displayName(folder_url)),
    "url": safe(lambda: folder_url.toString()),
    "templates": [],
    "folders": [],
    }

    # A CAMTemplate carries no url of its own, so the folder's child ASSET urls are the only address
    # cam_apply_template(template_url=...) can be given - carried here beside each template.
    asset_urls, by_name = _asset_index(lib, folder_url)
    templates, collided = _folder_templates(lib, folder_url, by_name)
    # Position pairs a template with its asset where NO name in the folder resolved one and the two
    # lists are the same length (measure_api row cam-template-asset-index-alignment).
    by_position = len(templates) == len(asset_urls) and not any(u for _n, _t, u in templates)
    for i, (tname, t, url) in enumerate(templates):
        if counter["n"] >= _MAX_NODES:
            counter["truncated"] = True
            break
        counter["n"] += 1
        positioned = by_position and i < len(asset_urls)
        row = {
        "name": tname,
        "description": safe(lambda t=t: t.description),
        "is_valid": safe(lambda t=t: t.isValidTemplate),
        "is_hole_template": safe(lambda t=t: t.isHoleTemplate),
        "url": asset_urls[i] if positioned else url,
        }
        if positioned:
            row["url_basis"] = "folder_position"
        node["templates"].append(row)
    if collided:
        # Arrivals that resolved to an asset already listed here - dropped as the same template.
        node["templates_collided"] = collided

    # Subfolders.
    if depth + 1 < max_depth:
        try:
            for sub in (lib.childFolderURLs(folder_url) or []):
                if counter["n"] >= _MAX_NODES:
                    counter["truncated"] = True
                    break
                counter["n"] += 1
                node["folders"].append(_walk_library(lib, sub, depth + 1, max_depth, counter))
        except Exception:
            pass
    else:
        try:
            if lib.childFolderURLs(folder_url):
                node["folders_truncated"] = True
        except Exception:
            pass

    return node


# ---------------------------------------------------------------------------
# cam_apply_template
# ---------------------------------------------------------------------------

# AutomaticGenerationModes member per friendly mode. The _GEN Choice validates against these keys,
# so an unrecognized 'generate' is a hard error, not a silent create-ops-generate-nothing.
_GEN_MODES = {
"skip": "SkipGeneration",            # default: create ops, don't generate toolpaths
"generate": "ForceGeneration",       # create AND generate the toolpaths
}
_GEN = _inputs.Choice("generate", options=list(_GEN_MODES), default="skip",
                      description="Toolpath generation after the operations are created.")


def _setup_op_nodes(setup_obj) -> list:
    """The operation nodes under one setup, each carrying the walk's 'Setup / ... / op' breadcrumb."""
    return [n for n in tree_nodes(setup_obj) if n.kind == "operation"]


def _path_census(nodes) -> dict:
    """How many of these operation nodes each breadcrumb holds. The PATH is the census key: the
    walk visits a parent's own operations before its folders, so crediting a held 'Drill1' in a
    folder to a new 'Drill1' under the setup would report the held operation as the applied one."""
    census = {}
    for n in nodes:
        census[n.path] = census.get(n.path, 0) + 1
    return census


def _added_operations(setup_obj, before):
    """(the operations a setup GAINED, the names its own walk cannot separate) - the post-apply
    breadcrumbs with the ones it already held taken off, never the template's operations nor what
    the apply call handed back. A breadcrumb that HELD an operation and now carries more cannot say
    WHICH of them landed, so every operation at it is withheld and its name reported instead."""
    nodes = _setup_op_nodes(setup_obj)
    after = _path_census(nodes)
    remaining = dict(before)
    added, collisions = [], []
    for n in nodes:
        held = before.get(n.path, 0)
        if held and after[n.path] > held:
            if n.name not in collisions:
                collisions.append(n.name)
            continue          # nothing here separates the held operation from the one that landed
        if remaining.get(n.path):
            remaining[n.path] -= 1
            continue
        added.append(n.obj)
    return added, collisions


def _applied_rows(ops):
    """(rows, the names carrying no tool) - one {name, strategy, tool} row per applied operation,
    'tool' being what that operation's own Operation.tool describes itself as."""
    rows, unselected = [], []
    for op in ops:
        name = safe(lambda op=op: op.name)
        t = safe(lambda op=op: op.tool)
        row = {"name": name, "strategy": safe(lambda op=op: op.strategy),
               "tool": safe(lambda t=t: t.description) if t is not None else None}
        if t is None:
            unselected.append(name)
        elif row["tool"] is None:
            # A tool IS assigned here and only its description did not read - not an unselected one.
            row["tool_description_unread"] = True
        rows.append(row)
    return rows, unselected


_TOOL_REMEDY = ("cam_edit_operation(operation=<name>, tool_scope='document', tool_index=<n>) "
                "assigns one per operation; cam_edit_tools lists this document's tools, and adds "
                "one when it holds none.")


def _named(names) -> str:
    """A wire list of operation names, a name that did not read MARKED rather than printed as null."""
    return named_with_remainder([n or "(name unread)" for n in names])


def _apply_note(rows, unselected, collisions, added_count, gen_key) -> str:
    """What the post-apply read of the SETUP observed, and the call that follows from it."""
    if collisions:
        return (f"{_named(collisions)} names more than one operation in one container here, so "
                "which of them this apply landed is not established: they are left out of "
                "'operations' and 'ready' is withheld. cam_get(include=['operations'], setup=...) "
                "lists every operation with its path.")
    if unselected:
        return (f"{len(unselected)} of {len(rows)} applied operations carry no tool that could be "
                f"read ({_named(unselected)}), so 'ready' reads false. {_TOOL_REMEDY}")
    if not rows or (added_count is not None and len(rows) != added_count):
        return (f"'operations' carries {len(rows)} row(s) read as new to the setup and does not "
                "account for operations_added, so 'ready' is withheld. "
                "cam_get(include=['operations'], setup=...) lists every operation it holds.")
    return (f"All {len(rows)} applied operations read a tool back."
            + (" Their toolpaths are not generated yet - run cam_generate."
               if gen_key == "skip" else " cam_get(include=['operations']) reads their state."))


def apply_template_to_setup_handler(setup: str = "", template_url: str = "",
                                    template_name: str = "", location: str = "cloud",
                                    generate: str = "skip") -> dict:
    """Apply a CAM template to a setup, recreating its operations there - identified by
    'template_url' or by 'template_name' within 'location'."""
    if not (setup or "").strip():
        return error("Provide 'setup' - the name of the setup to apply the template to.")
    if not (template_url.strip() or template_name.strip()):
        return error("Provide 'template_url' or 'template_name'.")
    # Validate the enums up front (fail fast, before any CAM work) - an unknown 'generate' must error,
    # not silently skip generation.
    gen_key, gerr = _GEN.resolve(generate)
    if gerr:
        return error(gerr)
    loc_key, lerr = _LOCATION.resolve(location)
    if lerr:
        return error(lerr)

    cam, err = get_cam()
    if err:
        return error(err)
    lib, err = _template_library()
    if err:
        return error(err)

    # Find the target setup.
    # The resolver's own refusal is returned verbatim: it is the one place that knows whether the
    # name was ABSENT or AMBIGUOUS, and only it can say which.
    target_setup, _names, serr = find_setup(cam, setup)
    if not target_setup:
        return error(serr)

    # Resolve the template.
    template = None
    if template_url.strip():
        u = safe(lambda: adsk.core.URL.create(template_url.strip()))
        if not u:
            return error(f"Invalid template URL: '{template_url}'.")
        template = safe(lambda: lib.templateAtURL(u))
        if not template:
            return error(f"No template found at URL: {template_url}")
        # Both given: the url is an ADDRESS and the name is what the caller believes is there. A
        # url published by folder position is only as good as that pairing, so a disagreement is
        # refused rather than applying whichever the url reached.
        resolved = (safe(lambda: template.name) or "").strip()
        if template_name.strip() and resolved.lower() != template_name.strip().lower():
            return error(f"'template_url' loads the template '{resolved}', but 'template_name' says "
                         f"'{template_name.strip()}' - nothing was applied. Pass the url alone to "
                         f"apply '{resolved}', or the name alone to search for "
                         f"'{template_name.strip()}'.")
    else:
        template, where = _find_template_by_name(lib, loc_key, template_name.strip())
        if not template:
            # An ambiguity hint is already a complete, self-contained message - don't wrap it as
            # 'not found' (it WAS found, in more than one place).
            if where and "ambiguous" in where:
                return error(where)
            return error(f"Template named '{template_name}' not found under '{loc_key}'. "
                          + (where or ""))

    if not safe(lambda: template.isValidTemplate, True):
        return error(f"Template '{safe(lambda: template.name)}' is not in a valid state to apply.")

    # Build the input + apply.
    ops_before = safe(lambda: target_setup.allOperations.count)
    before_census = _path_census(_setup_op_nodes(target_setup))
    try:
        ti = adsk.cam.CreateFromCAMTemplateInput.create()
        ti.camTemplate = template
        mode_name = _GEN_MODES[gen_key]
        mode_val = safe(lambda: getattr(adsk.cam.AutomaticGenerationModes, mode_name))
        if mode_val is not None:
            ti.mode = mode_val
        created = target_setup.createFromCAMTemplate2(ti)
    except Exception as e:
        return error(f"Failed to apply template: {e}")
    ops_after = safe(lambda: target_setup.allOperations.count)
    if ops_before is not None and ops_after is not None and ops_after <= ops_before:
        return error(f"createFromCAMTemplate2 ran but the setup's operation count did not increase "
                     f"({ops_before} before and after) - no operations were added. The template may "
                     "not be compatible with this setup.")

    created_names = []
    try:
        for ob in (created or []):
            created_names.append(safe(lambda: ob.name))
    except Exception:
        pass

    added_count = ((ops_after - ops_before)
                   if (ops_before is not None and ops_after is not None) else None)
    # The per-op status is read off the SETUP's own walk: a template can land operations carrying
    # no tool, and what the apply call returned is not what the setup took.
    added_ops, collisions = _added_operations(target_setup, before_census)
    rows, unselected = _applied_rows(added_ops)
    return ok({
        "applied": True,
        "template": safe(lambda: template.name),
        "setup": safe(lambda: target_setup.name),
        "generation_mode": gen_key,
        "created_count": len(created_names),
        "created_operations": created_names,
        "operations_added": added_count,
        "operations": rows,
        "tool_unselected": unselected,
        # present-and-empty: the names withheld from 'operations' above, so the caller sees what
        # this read could not attribute rather than inferring it from a short list.
        "collisions": collisions,
        "ready": (bool(rows) and not unselected and not collisions
                  and (added_count is None or len(rows) == added_count)),
        "note": _apply_note(rows, unselected, collisions, added_count, gen_key),
    })


def _find_template_by_name(lib, location, name):
    """Search a library location (recursively) for a template by name. Returns (template, hint). A
    name that matches in MORE THAN ONE folder is REFUSED (None + an 'ambiguous' hint naming the
    folders) rather than first-DFS-matched - template_url is the precise escape (the resolver idiom
    _cam_common uses for CAM tree names)."""
    if (location or "cloud").lower() not in _LOCATION_MEMBERS:
        return None, f"Unknown location '{location}'."
    loc = _location_enum(location or "cloud")
    if loc is None:
        return None, f"Location '{location}' is not available in this Fusion build."
    root = safe(lambda: lib.urlByLocation(loc))
    if not root:
        return None, f"Could not resolve the '{location}' library root."

    want = name.lower()
    seen_names = []
    matches = []          # (template, containing-folder display name)

    # The shared bounded folder walk; the LEAF op (read this folder's templates, record every name,
    # keep the matches) is this search's own.
    def visit(folder_url):
        # _folder_templates collapses two arrivals of ONE asset, so a listing that hands the same
        # template back twice resolves; a name TWO assets in the folder answer stays two rows here
        # and is refused below.
        _urls, by_name = _asset_index(lib, folder_url)
        for tn, t, _url in _folder_templates(lib, folder_url, by_name)[0]:
            if tn:
                seen_names.append(tn)
            if tn and tn.lower() == want:
                matches.append((t, safe(lambda folder_url=folder_url:
                                        lib.displayName(folder_url)) or "?"))
        return False      # every folder is searched: a duplicate name must be REFUSED, not raced

    walk_library_folders(lib, root, visit, max_folders=_MAX_NODES)
    if len(matches) == 1:
        return matches[0][0], None
    if len(matches) > 1:
        folders = ", ".join(f for _, f in matches)
        return None, (f"'{name}' is ambiguous - {len(matches)} templates share that name (in: "
                      f"{folders}). Pass the precise template_url instead.")
    hint = f"Templates seen: {', '.join(seen_names[:25]) or '(none)'}."
    return None, hint


# ---------------------------------------------------------------------------
# cam_save_template
# ---------------------------------------------------------------------------

def _as_cam_template(result):
    """Normalise createFromOperations' result to a CAMTemplate, or None - its annotation says
    list[Operation] while its own documentation says CAMTemplate, so both shapes are handled."""
    cast = safe(lambda: adsk.cam.CAMTemplate.cast(result))
    if cast:
        return cast
    # list-like? (createFromOperations' annotated return). Try to recover a CAMTemplate from it.
    items = None
    if isinstance(result, (list, tuple)):
        items = list(result)
    else:
        cnt = safe(lambda: result.count)
        if cnt is not None:
            items = list(iter_collection(result))
    if items:
        for it in items:
            t = safe(lambda it=it: adsk.cam.CAMTemplate.cast(it))
            if t:
                return t
    return None


def save_operations_as_template_handler(template_name: str = "", operations: str = "",
                                        setup: str = "", location: str = "cloud",
                                        folder: str = "", description: str = "") -> dict:
    """Bundle a setup's named 'operations' into a NEW library template, saved into 'folder' under
    'location' and created there; this never overwrites an existing template."""
    template_name = (template_name or "").strip()
    if not template_name:
        return error("Provide 'template_name' for the new template.")
    if not (setup or "").strip():
        return error("Provide 'setup' - the setup containing the operations.")
    op_names = [o.strip() for o in (operations or "").split(",") if o.strip()]
    if not op_names:
        return error("Provide 'operations' - a comma-separated list of operation names to bundle.")

    cam, err = get_cam()
    if err:
        return error(err)
    lib, err = _template_library()
    if err:
        return error(err)

    target_setup, _names, serr = find_setup(cam, setup)
    if not target_setup:
        return error(serr)

    # Collect the requested operations (Operation objects only).
    by_name = {}
    available_ops = []
    try:
        for op in safe(lambda: target_setup.allOperations, []):
            operation = adsk.cam.Operation.cast(op)
            if operation:
                nm = safe(lambda: operation.name)
                if nm:
                    by_name[nm.lower()] = operation
                    available_ops.append(nm)
    except Exception as e:
        return error(f"Could not read operations in '{setup}': {e}")

    selected = []
    missing = []
    for n in op_names:
        op = by_name.get(n.lower())
        if op:
            selected.append(op)
        else:
            missing.append(n)
    if missing:
        return error(f"Operations not found in '{setup}': {', '.join(missing)}. "
                      f"Available: {', '.join(available_ops[:25]) or '(none)'}")

    # createFromOperations may hand back EITHER a CAMTemplate or a list - its documentation and its
    # annotation disagree - so the result is normalised before .name or importTemplate touch it.
    try:
        result = adsk.cam.CAMTemplate.createFromOperations(selected)
    except Exception as e:
        return error(f"Could not build template from operations: {e}")
    if not result:
        return error("createFromOperations returned nothing.")
    template = _as_cam_template(result)
    if template is None:
        return error("createFromOperations did not yield a usable CAMTemplate (got "
                     f"{type(result).__name__}). The operation set may not be templatable together, "
                     "or this Fusion build's API returns an unexpected shape - please report.")
    template.name = template_name
    if description.strip():
        template.description = description.strip()
    if not safe(lambda: template.isValidTemplate, True):
        return error("The created template is not in a valid state (the operation set may "
    "not be templatable together).")

    # Resolve the destination FOLDER url (importTemplate wants a folder url).
    loc_key, lerr = _LOCATION.resolve(location)
    if lerr:
        return error(lerr)
    loc = _location_enum(loc_key)
    if loc is None:
        return error(f"Location '{loc_key}' is not available in this Fusion build.")
    root = safe(lambda: lib.urlByLocation(loc))
    if not root:
        return error(f"Could not resolve the '{location}' library root.")

    dest_url = root
    created_folder = None
    if folder.strip():
        # Find an existing top-level folder with this name, else create it.
        existing = None
        try:
            for furl in (lib.childFolderURLs(root) or []):
                if (safe(lambda: lib.displayName(furl)) or "").lower() == folder.strip().lower():
                    existing = furl
                    break
        except Exception:
            pass
        if existing:
            dest_url = existing
        else:
            try:
                dest_url = lib.createFolder(root, folder.strip())
                created_folder = folder.strip()
            except Exception as e:
                return error(f"Could not create destination folder '{folder}': {e}")

    # Import (save) the template into the folder.
    try:
        new_url = lib.importTemplate(template, dest_url)
    except Exception as e:
        return error(f"Failed to save the template: {e}")
    if not new_url:
        return error("importTemplate returned no URL (save may have failed).")
    if safe(lambda: lib.templateAtURL(new_url)) is None:
        return error("importTemplate returned a URL but no template loads back from it - the save "
                     "did not land.")

    return ok({
        "saved": True,
        "template": safe(lambda: template.name),
        "operation_count": len(selected),
        "operations": [safe(lambda: o.name) for o in selected],
        "location": location,
        "folder": (folder or "(library root)"),
        "created_folder": created_folder,
        "template_url": safe(lambda: new_url.toString()),
        "note": ("New template saved. Verify with cam_get(include=['templates']) (which reports each "
            "template's asset URL). This tool always creates a NEW template; "
            "overwriting an existing one is a separate capability."),
    })


# ---------------------------------------------------------------------------
# cam_delete_template
# ---------------------------------------------------------------------------

# How many local asset names a refusal spells out before named_with_remainder counts the rest.
_ASSET_NAMES_CAP = 12


def _local_template_assets(lib):
    """(assets, truncated) under the LOCAL template library root, or (None, None) when that root
    does not resolve. Folders are recursed by the ONE bounded library walk every CAM library read
    uses. A build carrying no LocalLibraryLocation member is refused earlier, by the by-name search
    that resolves the same root."""
    root = safe(lambda: lib.urlByLocation(_location_enum("local")))
    if root is None:
        return None, None
    return library_assets(lib, root)


def delete_template_handler(name: str = "", confirm_name: str = "") -> dict:
    """Delete one template from the LOCAL template library; see DELETE_TOOL_DESCRIPTION for the
    confirm_name gate."""
    name = (name or "").strip()
    confirm_name = (confirm_name or "").strip()
    if not name:
        return error("Provide 'name' - the template to delete, as "
                     "cam_get(include=['templates'], template_location='local') lists it.")
    if not confirm_name:
        return error("Provide 'confirm_name' - the template's exact name again, as a safety "
                     "confirmation. Template deletion is not undoable from this server.")

    lib, err = _template_library()
    if err:
        return error(err)

    # ONE resolve, LOCAL-only, through the same by-name search cam_apply_template runs: a name
    # several templates answer to is REFUSED rather than resolved to the first folder walked.
    template, where = _find_template_by_name(lib, "local", name)
    if template is None:
        # An ambiguity hint is already a complete message - it WAS found, in more than one place.
        if where and "ambiguous" in where:
            return error(where + " Nothing was deleted.")
        return error(f"No template named '{name}' in the LOCAL template library - this tool deletes "
                     f"from the Local library only. {where or ''} Nothing was deleted.")
    label = (safe(lambda: template.name) or "").strip()

    # Case-SENSITIVE confirmation against the RESOLVED name, not the request: the caller confirms
    # the template the search actually reached (only surrounding whitespace is forgiven).
    if label != confirm_name:
        return error(f"Name mismatch - refusing to delete. '{name}' resolves to the template "
                     f"'{label}', but confirm_name was '{confirm_name}'. Pass "
                     f"confirm_name='{label}' if you really mean this template.")

    assets, truncated = _local_template_assets(lib)
    if assets is None:
        return error("Could not resolve the Local template library location, so the template's "
                     "asset cannot be addressed. Nothing was deleted.")
    # The ONE match set for the pre-delete search AND the post-delete read-back: two reads searched
    # with different sets is how a delete reports "gone" against a set it never covered.
    wanted_names = {label.lower()}
    hits = assets_named(assets, wanted_names)
    if not hits:
        listing = named_with_remainder(sorted(asset_leaf(a) for a in assets), cap=_ASSET_NAMES_CAP)
        return error(f"No asset in the Local template library is named '{label}'"
                     + f". Local assets: {listing or '(none)'}."
                     + (" The walk hit its own bound, so this list is incomplete."
                        if truncated else "")
                     + " Nothing was deleted.")
    if len(hits) > 1:
        return error(f"'{label}' names {len(hits)} assets in the Local template library "
                     f"({named_with_remainder([str(asset_key(a)) for a in hits], cap=_ASSET_NAMES_CAP)})"
                     " - refusing to guess which one to delete. Remove the duplicate in Fusion's "
                     "template library first.")
    # An INCOMPLETE walk cannot support the one-asset conclusion above: a second asset of the same
    # name beyond the walk's bound would have been refused, and this delete is irreversible - so it
    # fails CLOSED rather than firing on one of an unknown number.
    if truncated:
        return error(f"The Local template library walk hit its own bound before it finished, so "
                     f"'{label}' cannot be shown to name only ONE asset - a duplicate past the "
                     "bound would not have been seen. Nothing was deleted.")

    url = hits[0]
    # The asset is only deletable as the template the caller confirmed: load it back and compare the
    # name, so an asset whose FILE name matches while it holds another template is refused.
    at_url = safe(lambda: lib.templateAtURL(url))
    if at_url is None:
        return error(f"The Local library asset '{asset_leaf(url)}' does not load a template, so "
                     "what it holds cannot be confirmed. Nothing was deleted.")
    at_name = (safe(lambda: at_url.name) or "").strip()
    if at_name.lower() != label.lower():
        return error(f"The Local library asset '{asset_leaf(url)}' holds the template '{at_name}', "
                     f"not '{label}' - refusing to delete an asset that is not the template that "
                     "was confirmed.")

    # deleteAsset addresses a stored asset by url; importTemplate stores a template under a leafName
    # whose STEM is the template's name. A False is reported as the refusal it is, and the
    # read-backs below are what the claim is made from.
    try:
        did = lib.deleteAsset(url)
    except Exception as e:
        return error(f"Deleting '{label}' from the Local template library failed: {e}.")
    if not did:
        return error(f"Fusion declined to delete '{label}' from the Local template library "
                     "(deleteAsset returned false) - it is still there.")

    # The LOAD-BEARING read-back is the library's own asset walk - the read this delete was
    # addressed through. A walk that could not answer, or did not finish, leaves the delete
    # UNCONFIRMED rather than letting an empty match pass for proof.
    after, after_truncated = _local_template_assets(lib)
    if after is None or after_truncated:
        return error(f"deleteAsset returned true for '{label}', but the Local template library "
                     + ("location no longer resolves" if after is None
                        else "asset walk hit its own bound before finishing")
                     + ", so the delete could not be read back and is UNCONFIRMED. Re-read with "
                       "cam_get(include=['templates'], template_location='local').")
    still_listed = [asset_leaf(a) for a in assets_named(after, wanted_names)]
    if still_listed:
        return error(f"deleteAsset returned true but the Local template library still lists "
                     f"'{still_listed[0]}' - the delete did not take. Re-read with "
                     "cam_get(include=['templates'], template_location='local').")
    # The second leg, on the address the delete was aimed at. The read MUST be safe()-wrapped:
    # templateAtURL RAISES '3 : Given URL does not point to a template' on a url whose asset was
    # just deleted, rather than returning null as its documentation says.
    loads_after = safe(lambda: lib.templateAtURL(url)) is not None
    if loads_after:
        return error(f"deleteAsset returned true and '{label}' is gone from the Local template "
                     "library's asset walk, but a template still loads from its url "
                     f"({asset_key(url)}) - the two reads disagree, so the delete is UNCONFIRMED.")
    return ok({
        "deleted": True,
        "template": label,
        "location": "local",
        "asset_name": asset_leaf(url),
        "url": safe(lambda: url.toString()),
        "loads_after_delete": loads_after,
        "local_assets_remaining": len(after),   # the walk that answered above, not a second read
        "note": (f"Template deleted from the Local template library: its asset '{asset_leaf(url)}' "
                 "is gone from a re-walk of the library's own assets, and nothing loads from its "
                 "url any more. cam_save_template writes a new one; "
                 "cam_get(include=['templates'], template_location='local') lists what is left."),
    })


# --- tool definitions ---
# The template LISTING (list_cam_templates_handler) is a passive read - surfaced as
# cam_get(include=['templates']) (it calls this handler). The WRITES below stay here as tools.

_apply_tool = (
    Tool.create_with_string_input(
        name="cam_apply_template",
        description=(
            "Apply a CAM toolpath template to a setup, recreating the template's operations "
            "in that setup. Identify the template by 'template_url' or 'template_name' "
            "(cam_get(include=['templates']) lists both). Returns the operations the setup gained "
            "with the tool each reads back; 'ready' is false while any of them carries none. "
            "Note: with generate='generate' a large template can exceed the 30s call "
            "limit and return a timeout even though the work is still running - do NOT blindly "
            "retry; verify with cam_get(include=['operations']) / view_screenshot first."
        ),
        input_param_name="setup",
        input_param_description="Name of the setup to apply the template to.",
    )
    .add_input_property("template_url", {"type": "string", "description": "Template asset URL (from cam_get(include=['templates'])."})
    .add_input_property("template_name", {"type": "string", "description": "Template name (searched under location)."})
    .add_input_property(*_LOCATION.as_property())
    .add_input_property(*_GEN.as_property())
    .strict_schema()
)
apply_template_to_setup_item = Item.create_tool_item(
    tool=_apply_tool, write="write", handler=apply_template_to_setup_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_cam_templates.py::TestApplyTemplateToolStatus"
                      "::test_an_operation_with_no_tool_is_named_with_its_remedy")
)

_save_tool = (
    Tool.create_with_string_input(
        name="cam_save_template",
        description=(
        "Bundle a subset of a setup's operations into a NEW toolpath template in the "
        "library. 'operations' is a comma-separated list of operation names within "
        "'setup'. Saves into 'folder' (a top-level folder name under 'location', created "
        "if missing). Optional 'description'. Always creates a new template. Verify with "
        "cam_get(include=['templates'])."
        ),
        input_param_name="template_name",
        input_param_description="Name for the new template.",
    )
    .add_input_property("operations", {"type": "string", "description": "Comma-separated operation names to bundle."})
    .add_input_property("setup", {"type": "string", "description": "Setup containing the operations."})
    .add_input_property(*_LOCATION.as_property())
    .add_input_property("folder", {"type": "string", "description": "Top-level destination folder name (created if missing)."})
    .add_input_property("description", {"type": "string", "description": "Optional template description."})
    .strict_schema()
)
save_operations_as_template_item = Item.create_tool_item(
    tool=_save_tool, write="write", handler=save_operations_as_template_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_cam_templates.py::TestSaveTemplateRename"
                      "::test_saved_template_that_does_not_load_back_bites")
)

DELETE_TOOL_DESCRIPTION = (
    "Delete a TEMPLATE from the LOCAL toolpath template library by name - the counterpart to "
    "cam_save_template, and the only tool here that removes one. 'name' is matched EXACTLY "
    "(case-insensitively) against the Local library's templates (see "
    "cam_get(include=['templates'], template_location='local')); a name several templates answer "
    "to is refused, not guessed, and a template in any other location is not reached. GUARDED and "
    "IRREVERSIBLE: 'confirm_name' must EXACTLY match the RESOLVED template name."
)

_delete_tool = (
    Tool.create_simple(name="cam_delete_template", description=DELETE_TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string",
            "description": "The template to delete, as cam_get(include=['templates'], template_location='local') lists it."})
    .add_input_property("confirm_name", {"type": "string",
            "description": "The resolved template name again, case-sensitive (safety confirmation; must match)."})
    .strict_schema()
)
delete_template_item = Item.create_tool_item(
    tool=_delete_tool, write="destructive", handler=delete_template_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_cam_templates.py::TestDeleteTemplateEffect"
                      "::test_an_asset_still_listed_after_a_true_delete_is_an_error")
)


def register_tool():
    register(apply_template_to_setup_item)
    register(save_operations_as_template_item)
    register(delete_template_item)
