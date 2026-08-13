# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read (cam_get(include=['templates'])) and write (cam_apply_template, cam_save_template) the CAM
toolpath template library. cam_save_template always creates a new template; it does not overwrite
an existing one."""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import iter_collection, ok, error, safe
from ._cam_common import get_cam, find_setup, library_children, walk_library_folders
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


def _template_library():
    """Return (templateLibrary, None) or (None, reason).

    The library manager lives on the CAMManager singleton (CAMManager.get()), NOT on
    the CAM product object.
    """
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
    })


def _walk_library(lib, folder_url, depth, max_depth, counter):
    """Recursively summarize a library folder: its templates + subfolders."""
    node = {
    "folder": safe(lambda: lib.displayName(folder_url)),
    "url": safe(lambda: folder_url.toString()),
    "templates": [],
    "folders": [],
    }

    # Templates directly in this folder. Pair each with its asset URL (from
    # childAssetURLs) so callers can address it precisely (apply / future overwrite).
    asset_urls = []
    try:
        asset_urls = [u.toString() for u in (lib.childAssetURLs(folder_url) or [])]
    except Exception:
        asset_urls = []

    def _asset_url_for(name):
        if not name:
            return None
        # Asset URLs look like "<folder>/<name>.f3dhsm-template"; match by the name part.
        for au in asset_urls:
            base = au.rstrip("/").rsplit("/", 1)[-1]
            stem = base.rsplit(".", 1)[0] if "." in base else base
            if stem == name:
                return au
        return None

    try:
        for t in (lib.childTemplates(folder_url) or []):
            if counter["n"] >= _MAX_NODES:
                counter["truncated"] = True
                break
            counter["n"] += 1
            tname = safe(lambda: t.name)
            node["templates"].append({
            "name": tname,
            "description": safe(lambda: t.description),
            "is_valid": safe(lambda: t.isValidTemplate),
            "is_hole_template": safe(lambda: t.isHoleTemplate),
            "url": _asset_url_for(tname),
            })
    except Exception:
        pass

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


def apply_template_to_setup_handler(setup: str = "", template_url: str = "",
                                    template_name: str = "", location: str = "cloud",
                                    generate: str = "skip") -> dict:
    """Apply a CAM template to a setup, recreating its operations there. WRITES.

    Identify the template by 'template_url' (precise) or 'template_name' (searched
    within 'location'). 'generate' is skip (default - just create the operations) or
    generate (also compute the toolpaths).
    """
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

    return ok({
        "applied": True,
        "template": safe(lambda: template.name),
        "setup": safe(lambda: target_setup.name),
        "generation_mode": gen_key,
        "created_count": len(created_names),
        "created_operations": created_names,
        "operations_added": ((ops_after - ops_before)
                             if (ops_before is not None and ops_after is not None) else None),
        "note": ("Operations were added to the setup. If generation_mode was 'skip', "
            "the toolpaths are not yet generated. Use cam_get(include=['operations']) or "
            "view_screenshot to verify, and cam_compare_operations to check settings."),
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
        for t in library_children(lib, folder_url, "childTemplates"):
            tn = safe(lambda t=t: t.name)
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
    """Normalise createFromOperations' result to a CAMTemplate (or None).

    The live API annotation is list[Operation] but the docstring claims a CAMTemplate - so be robust:
      - already a CAMTemplate (or casts to one) -> use it directly;
      - a list/collection -> try its single element, else CAMTemplate.cast on the collection;
      - anything else -> None (caller reports it honestly rather than crashing on .name later).
      """
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
    """Bundle a subset of a setup's operations into a NEW library template. WRITES.

    'operations' is a comma-separated list of operation names (within 'setup'). The
    template is saved into 'folder' (a top-level folder name under 'location'), which
    is created if it doesn't exist. Overwriting an existing template is not supported
    (see note); this always creates a new template.
    """
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

    # Find the setup.
    # The resolver's own refusal is returned verbatim: it is the one place that knows whether the
    # name was ABSENT or AMBIGUOUS, and only it can say which.
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

    # Build the template from the operations. NOTE the live API is self-contradictory here:
    # CAMTemplate.createFromOperations' docstring says "Returns the newly created template" but its
    # type annotation says -> list[Operation]. So the result may be EITHER a CAMTemplate OR a list -
    # normalise to a real CAMTemplate before set .name / importTemplate, or a list-return breaks the save.
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


# --- tool definitions ---
# The template LISTING (list_cam_templates_handler) is a passive read - surfaced as
# cam_get(include=['templates']) (it calls this handler). The two WRITES below stay here as tools.

_apply_tool = (
    Tool.create_with_string_input(
        name="cam_apply_template",
        description=(
            "Apply a CAM toolpath template to a setup, recreating the template's operations "
            "in that setup. Identify the template by 'template_url' (the precise asset URL "
            "from cam_get(include=['templates'])) or 'template_name' (searched under 'location'). "
            "'generate' controls toolpath generation: 'skip' (default - just create "
            "operations) or 'generate' (also compute the toolpaths), adding operations to the "
            "setup. Note: with generate='generate' a large template can exceed the 30s call "
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
    tool=_apply_tool, write="write", handler=apply_template_to_setup_handler, run_on_main_thread=True
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
    tool=_save_tool, write="write", handler=save_operations_as_template_handler, run_on_main_thread=True
)


def register_tool():
    register(apply_template_to_setup_item)
    register(save_operations_as_template_item)
