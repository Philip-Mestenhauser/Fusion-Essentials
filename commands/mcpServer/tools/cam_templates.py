# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for CAM toolpath templates.

  cam_list_templates    -> navigate the template library (cloud / local / Fusion):
                           folders and the templates they contain, by URL. Read-only.
  cam_apply_template -> instantiate a template into a named CAM setup, recreating
                           its operations there. WRITES to the document.

Grounded in adsk.cam:
  - adsk.cam.CAMManager.get().libraryManager.templateLibrary -> CAMTemplateLibrary
    (the library manager is on the CAMManager SINGLETON, not the CAM product)
  - CAMLibrary.urlByLocation(LibraryLocations.*) -> root URL per location
    (LocalLibraryLocation=0, CloudLibraryLocation=1, Fusion360LibraryLocation=5, ...)
  - CAMLibrary.childFolderURLs(url) / displayName(url); CAMTemplateLibrary.childTemplates(url)
    -> CAMTemplate(.name, .description, .isValidTemplate); templateAtURL(url)
  - Setup.createFromCAMTemplate2(CreateFromCAMTemplateInput) with .camTemplate set,
    .mode = AutomaticGenerationModes.* (default Skip Generation)
  - adsk.core.URL.create(str) / .toString()

Handlers run on the main thread. Saving/overwriting templates back to the library
(importTemplate / updateTemplate) is intentionally a separate, later building block.
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()

# Friendly location names -> LibraryLocations enum value.
_LOCATIONS = {
    "local": 0,
    "cloud": 1,
    "network": 2,
    "samples": 3,
    "external": 4,
    "fusion": 5,
    "fusion360": 5,
    "hub": 6,
}

_MAX_NODES = 1500


def _get_cam():
    """Return (cam, None) or (None, reason). Works regardless of active workspace."""
    try:
        doc = app.activeDocument
    except Exception:
        doc = None
    if not doc:
        return None, "No active document."
    try:
        cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
    except Exception as e:
        return None, f"Could not access CAM product: {e}"
    if not cam:
        return None, "This document has no CAM (Manufacture) data."
    return cam, None


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
# cam_list_templates
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
        loc = _LOCATIONS.get(location.strip().lower())
        if loc is None:
            return error(f"Unknown location '{location}'. Valid: {', '.join(_LOCATIONS)}")
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

# AutomaticGenerationModes: ForceGeneration=0, SkipGeneration=1, UserPreference=2.
_GEN_MODES = {
"skip": "SkipGeneration",            # default: create ops, don't generate toolpaths
"generate": "ForceGeneration",       # create AND generate toolpaths
"force": "ForceGeneration",
"user_preference": "UserPreference",
}


def apply_template_to_setup_handler(setup: str = "", template_url: str = "",
                                    template_name: str = "", location: str = "cloud",
                                    generate: str = "skip") -> dict:
    """Apply a CAM template to a setup, recreating its operations there. WRITES.

    Identify the template by 'template_url' (precise) or 'template_name' (searched
    within 'location'). 'generate' controls toolpath generation (skip/regenerate/
    generate_new); default 'skip' just creates the operations.
    """
    if not (setup or "").strip():
        return error("Provide 'setup' — the name of the setup to apply the template to.")
    if not (template_url.strip() or template_name.strip()):
        return error("Provide 'template_url' or 'template_name'.")

    cam, err = _get_cam()
    if err:
        return error(err)
    lib, err = _template_library()
    if err:
        return error(err)

    # Find the target setup.
    target_setup = None
    available_setups = []
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            nm = safe(lambda: s.name)
            available_setups.append(nm)
            if (nm or "").lower() == setup.strip().lower():
                target_setup = s
                break
    except Exception as e:
        return error(f"Could not read setups: {e}")
    if not target_setup:
        return error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available_setups if n) or '(none)'}")

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
        template, where = _find_template_by_name(lib, location, template_name.strip())
        if not template:
            return error(f"Template named '{template_name}' not found under '{location}'. "
                          + (where or ""))

    if not safe(lambda: template.isValidTemplate, True):
        return error(f"Template '{safe(lambda: template.name)}' is not in a valid state to apply.")

    # Build the input + apply.
    try:
        ti = adsk.cam.CreateFromCAMTemplateInput.create()
        ti.camTemplate = template
        mode_name = _GEN_MODES.get((generate or "skip").lower(), "SkipGeneration")
        mode_val = safe(lambda: getattr(adsk.cam.AutomaticGenerationModes, mode_name))
        if mode_val is not None:
            ti.mode = mode_val
        created = target_setup.createFromCAMTemplate2(ti)
    except Exception as e:
        return error(f"Failed to apply template: {e}")

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
        "generation_mode": (generate or "skip"),
        "created_count": len(created_names),
        "created_operations": created_names,
        "note": ("Operations were added to the setup. If generation_mode was 'skip', "
            "the toolpaths are not yet generated. Use cam_get_operations or "
            "view_screenshot to verify, and cam_compare_operations to check settings."),
    })


def _find_template_by_name(lib, location, name):
    """Search a library location (recursively) for a template by name. Returns (template, hint)."""
    loc = _LOCATIONS.get((location or "cloud").lower())
    if loc is None:
        return None, f"Unknown location '{location}'."
    root = safe(lambda: lib.urlByLocation(loc))
    if not root:
        return None, f"Could not resolve the '{location}' library root."

    want = name.lower()
    seen_names = []
    stack = [root]
    visited = 0
    while stack and visited < _MAX_NODES:
        folder_url = stack.pop()
        visited += 1
        try:
            for t in (lib.childTemplates(folder_url) or []):
                tn = safe(lambda: t.name)
                if tn:
                    seen_names.append(tn)
                if tn and tn.lower() == want:
                    return t, None
        except Exception:
            pass
        try:
            for sub in (lib.childFolderURLs(folder_url) or []):
                stack.append(sub)
        except Exception:
            pass
    hint = f"Templates seen: {', '.join(seen_names[:25]) or '(none)'}."
    return None, hint


# ---------------------------------------------------------------------------
# cam_save_template
# ---------------------------------------------------------------------------

def _as_cam_template(result):
    """Normalise createFromOperations' result to a CAMTemplate (or None).

    The live API annotation is list[Operation] but the docstring claims a CAMTemplate — so be robust:
      - already a CAMTemplate (or casts to one) -> use it directly;
      - a list/collection -> try its single element, else CAMTemplate.cast on the container;
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
            items = [safe(lambda i=i: result.item(i)) for i in range(cnt)]
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
        return error("Provide 'setup' — the setup containing the operations.")
    op_names = [o.strip() for o in (operations or "").split(",") if o.strip()]
    if not op_names:
        return error("Provide 'operations' — a comma-separated list of operation names to bundle.")

    cam, err = _get_cam()
    if err:
        return error(err)
    lib, err = _template_library()
    if err:
        return error(err)

    # Find the setup.
    target_setup = None
    available_setups = []
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            nm = safe(lambda: s.name)
            available_setups.append(nm)
            if (nm or "").lower() == setup.strip().lower():
                target_setup = s
                break
    except Exception as e:
        return error(f"Could not read setups: {e}")
    if not target_setup:
        return error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available_setups if n) or '(none)'}")

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
    # type annotation says -> list[Operation]. So the result may be EITHER a CAMTemplate OR a list.
    # The old code assumed a single CAMTemplate (set .name, passed straight to importTemplate); if the
    # binding returns a list that silently broke the save. Normalise to a real CAMTemplate first.
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
                     "or this Fusion build's API returns an unexpected shape — please report.")
    try:
        template.name = template_name
        if description.strip():
            template.description = description.strip()
    except Exception:
        pass
    if not safe(lambda: template.isValidTemplate, True):
        return error("The created template is not in a valid state (the operation set may "
    "not be templatable together).")

    # Resolve the destination FOLDER url (importTemplate wants a folder url).
    loc = _LOCATIONS.get(location.strip().lower())
    if loc is None:
        return error(f"Unknown location '{location}'. Valid: {', '.join(_LOCATIONS)}")
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

    return ok({
        "saved": True,
        "template": safe(lambda: template.name),
        "operation_count": len(selected),
        "operations": [safe(lambda: o.name) for o in selected],
        "location": location,
        "folder": (folder or "(library root)"),
        "created_folder": created_folder,
        "template_url": safe(lambda: new_url.toString()),
        "note": ("New template saved. Verify with cam_list_templates (which reports each "
            "template's asset URL). This tool always creates a NEW template; "
            "overwriting an existing one is a separate capability."),
    })


# --- result helpers ---


# --- tool definitions ---

_list_tool = (
    Tool.create_simple(
        name="cam_list_templates",
        description=(
        "Navigate the CAM toolpath template library. Lists folders and the templates "
        "they contain (name, description, validity) for a library 'location' "
        "(cloud, local, fusion, samples, hub, ...) or a specific folder 'url'. Each "
        "template/folder reports its URL so you can apply it with cam_apply_template. "
        " Pass 'max_depth' (default 4)."
        ),
    )
    .add_input_property("location", {"type": "string",
        "description": "Library location: cloud (default), local, fusion, samples, hub."})
    .add_input_property("url", {"type": "string",
        "description": "Optional specific folder URL to start at (overrides location)."})
    .add_input_property("max_depth", {"type": "integer", "description": "Folder depth to walk (default 4)."})
    .strict_schema()
)
list_cam_templates_item = Item.create_tool_item(
    tool=_list_tool, write="read", handler=list_cam_templates_handler, run_on_main_thread=True
)

_apply_tool = (
    Tool.create_with_string_input(
        name="cam_apply_template",
        description=(
            "Apply a CAM toolpath template to a setup, recreating the template's operations "
            "in that setup. Identify the template by 'template_url' (the precise asset URL "
            "from cam_list_templates) or 'template_name' (searched under 'location'). "
            "'generate' controls toolpath generation: 'skip' (default — just create "
            "operations) or 'generate' (also compute the toolpaths). WRITES to the document "
            "(adds operations to the setup). Note: with generate='generate' a large template "
            "can exceed the 30s call limit and return a timeout even though the work is still "
            "running — do NOT blindly retry; verify with cam_get_operations / view_screenshot first."
        ),
        input_param_name="setup",
        input_param_description="Name of the setup to apply the template to.",
    )
    .add_input_property("template_url", {"type": "string", "description": "Template asset URL (from cam_list_templates 'url')."})
    .add_input_property("template_name", {"type": "string", "description": "Template name (searched under location)."})
    .add_input_property("location", {"type": "string", "description": "Library location to search by name (default cloud)."})
    .add_input_property("generate", {"type": "string", "description": "skip (default) | generate."})
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
        "if missing). Optional 'description'. WRITES to the template library. (Overwriting "
        "an existing template is not yet supported — always creates a new template.) "
        "Verify with cam_list_templates."
        ),
        input_param_name="template_name",
        input_param_description="Name for the new template.",
    )
    .add_input_property("operations", {"type": "string", "description": "Comma-separated operation names to bundle."})
    .add_input_property("setup", {"type": "string", "description": "Setup containing the operations."})
    .add_input_property("location", {"type": "string", "description": "Library location (default cloud): cloud, local, ..."})
    .add_input_property("folder", {"type": "string", "description": "Top-level destination folder name (created if missing)."})
    .add_input_property("description", {"type": "string", "description": "Optional template description."})
)
save_operations_as_template_item = Item.create_tool_item(
    tool=_save_tool, write="write", handler=save_operations_as_template_handler, run_on_main_thread=True
)


def register_tool():
    register(list_cam_templates_item)
    register(apply_template_to_setup_item)
    register(save_operations_as_template_item)
