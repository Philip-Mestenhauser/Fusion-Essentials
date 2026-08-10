# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Create a machine in the LOCAL machine library from one of Fusion's machine templates, named so
cam_edit_setup(machine=...) can assign it. Creation lives on adsk.cam.Machine as statics -
MachineLibrary itself exposes no create method."""

import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _inputs
# The catalog read and the by-name resolver are the shared CAM substrate's: this tool checks a new
# name against the SAME rows cam_get publishes and gates on the SAME query an assignment resolves by.
from ._cam_common import machine_catalog, resolve_machine

# Wire value -> the adsk.cam.MachineTemplate member it builds from. The template fixes the new
# machine's kinematics tree; a member this Fusion version does not expose is refused by name.
_TEMPLATES = {
    "generic_3_axis": "Generic3Axis",
    "generic_4_axis": "Generic4Axis",
    "generic_5_axis_head_head": "Generic5AxisHeadHead",
    "generic_5_axis_head_table": "Generic5AxisHeadTable",
    "generic_5_axis_table_table": "Generic5AxisTableTable",
    "generic_fff": "GenericFFF",
    "generic_lathe": "GenericLathe",
}
_TEMPLATE = _inputs.Choice("template", options=list(_TEMPLATES), default="generic_3_axis",
                           description="The machine template the new machine is built from.")

# The catalog read is the collision check's evidence, so it must not come back capped: a row hidden
# past the cap would let a duplicate name through.
_CATALOG_CAP = 5000


def _machine_library():
    """The shared MachineLibrary - it hangs off CAMManager.get().libraryManager, not the document's
    CAM product, so no open CAM job is needed. Returns (library, None) or (None, error)."""
    lib = safe(lambda: adsk.cam.CAMManager.get().libraryManager.machineLibrary)
    if lib is None:
        return None, "Could not access the machine library (CAMManager.libraryManager.machineLibrary)."
    return lib, None


def _catalog_clash(name):
    """The catalog row `name` already reaches and the KEY it reaches it by - read from the same
    Local + Fusion360 catalog an assignment resolves out of. Every key that resolver selects on is
    compared (case-insensitively, EXACT): _cam_common's exact-match rung takes a label, then
    'vendor model', then the model, so a new machine taking any of them as its name retargets an
    assignment string that resolves to a different machine.
    Returns (row, key, None), (None, None, None) when the name is free, or (None, None, error)."""
    rows, truncated, err = machine_catalog("", "", _CATALOG_CAP)
    if err is not None:
        return None, None, f"Could not read the machine catalog to check '{name}': {err}"
    if truncated:
        return None, None, (f"The machine catalog holds more than {_CATALOG_CAP} machines, so the "
                            f"name '{name}' cannot be proven free - refused instead of risking a "
                            "duplicate.")
    want = name.strip().lower()
    for row in (rows or []):
        vendor, model = (row.get("vendor") or ""), (row.get("model") or "")
        for key, value in (("name", row.get("name")), ("model", model),
                           ("vendor model", (vendor + " " + model).strip())):
            if (value or "").strip().lower() == want:
                return row, key, None
    return None, None, None


def _write_field(machine, prop, value):
    """Set one Machine field and read it back. Returns an error string, or '' when the value landed.
    Every field is written BEFORE the machine is stored, so a refusal here leaves the library
    untouched."""
    try:
        setattr(machine, prop, value)
    except Exception as e:
        return f"Could not set Machine.{prop} to '{value}': {e}."
    landed = safe(lambda: getattr(machine, prop))
    if landed != value:
        return (f"Set Machine.{prop} to '{value}' but it reads back '{landed}' - the value did not "
                "land, so nothing was stored in the library.")
    return ""


def handler(name: str = "", template: str = "generic_3_axis", vendor: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' - the new machine's name. It becomes Machine.description, the "
                     "label cam_edit_setup(machine=...) resolves an assignment by.")
    key, terr = _TEMPLATE.resolve(template)
    if terr:
        return error(terr)
    member = getattr(adsk.cam.MachineTemplate, _TEMPLATES[key], None)
    if member is None:
        return error(f"This Fusion version's MachineTemplate has no '{_TEMPLATES[key]}' member.")

    lib, lerr = _machine_library()
    if lerr:
        return error(lerr)

    # Refuse before anything is created when the name is ALREADY how the library reaches some other
    # machine - by its label, its model, or its 'vendor model'. Taking such a name does not just
    # duplicate a label: it retargets an assignment string that resolves to another machine.
    clash, clash_key, cerr = _catalog_clash(name)
    if cerr:
        return error(cerr)
    if clash:
        return error(f"'{name}' is already how the {clash.get('location')} machine library reaches "
                     f"'{clash.get('name')}' (vendor '{clash.get('vendor')}', model "
                     f"'{clash.get('model')}') - it matches that machine's {clash_key}. An "
                     "assignment resolves by those keys, so pick another 'name'.")

    try:
        machine = adsk.cam.Machine.createFromTemplate(member)
    except Exception as e:
        return error(f"Machine.createFromTemplate('{key}') failed: {e}.")
    if machine is None:
        return error(f"Machine.createFromTemplate('{key}') returned nothing - no machine was created.")

    # A machine off a template arrives carrying that template's own description/vendor/model, which
    # every machine built from it shares, so the name REPLACES them. It goes on the MODEL too:
    # _cam_common.resolve_machine widens a failed lookup by re-splitting a label into
    # (vendor, model) precisely BECAUSE a label does not match the model field its query is keyed
    # on - so a machine whose model stays the template default is not reachable by its own name.
    # The gates below prove THIS machine's reachability per call rather than resting on which
    # fields the library query indexes.
    for prop, value in (("description", name), ("vendor", (vendor or "").strip()),
                        ("model", name)):
        if not value:
            continue
        werr = _write_field(machine, prop, value)
        if werr:
            return error(werr)

    root = safe(lambda: lib.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation))
    if root is None:
        return error("Could not resolve the Local machine library location to save into.")
    try:
        url = lib.importMachine(machine, root, name)
    except Exception as e:
        return error(f"Storing machine '{name}' in the Local machine library failed: {e}.")
    if not url:
        return error(f"Storing machine '{name}' in the Local machine library returned no URL - "
                     "the machine was not stored.")
    if safe(lambda: lib.machineAtURL(url)) is None:
        return error(f"importMachine returned a URL for '{name}' but no machine loads back from it "
                     "- the create did not land.")

    # The honesty gate: re-resolve through the SAME query cam_edit_setup assigns from. A machine
    # that cannot be found again by its name is a create the caller cannot use.
    stored_url = safe(lambda: url.toString())
    found, label, rerr = resolve_machine(name)
    if found is None:
        return error(f"Machine '{name}' was stored in the Local machine library ({stored_url}) but "
                     f"it does not resolve back through the query cam_edit_setup assigns from: "
                     f"{rerr} The stored machine is still there.")
    if (label or "").strip().lower() != name.lower():
        return error(f"Machine '{name}' was stored in the Local machine library ({stored_url}) but "
                     f"that name resolves to '{label}' - an assignment would pick a different "
                     "machine. The stored machine is still there.")

    # The catalog cam_get(include=['machines']) publishes is read AGAIN, after the store, and must
    # now list this name - otherwise the payload's catalog claim would be an assumption.
    row, row_key, rowerr = _catalog_clash(name)
    if rowerr:
        return error(rowerr)
    if row is None or row_key != "name":
        return error(f"Machine '{name}' was stored in the Local machine library ({stored_url}) but "
                     "the machine catalog does not list that name - the create did not land where "
                     "an assignment reads. The stored machine is still there.")

    has_sim = bool(safe(lambda: found.hasSimulationModel, False))
    note = ("Machine created, re-resolved through the query cam_edit_setup assigns from, and re-read "
            f"from the cam_get(include=['machines']) catalog. Assign it: cam_edit_setup(setup=..., "
            f"machine='{label}'). It persists in the {row.get('location')} machine library - this "
            "server has no tool that removes a machine.")
    if has_sim:
        note += (" It carries a simulation model, which the assignment refuses - pass "
                 "machine_strip_simulation=true to cam_edit_setup.")
    return ok({
        "created": True,
        "name": label,
        "machine_id": safe(lambda: found.id),
        "template": key,
        "location": row.get("location"),
        "url": stored_url,
        "asset_name": safe(lambda: url.leafName),
        "vendor": safe(lambda: found.vendor),
        "model": safe(lambda: found.model),
        "kind": row.get("kind"),
        "has_post": bool(safe(lambda: found.hasPost, False)),
        "has_simulation_model": has_sim,
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Create a MACHINE in the LOCAL machine library from a Fusion machine template - the answer when "
    "cam_edit_setup(machine=...) finds no match. 'name' becomes the machine's name "
    "(Machine.description) and model, what an assignment resolves by; a name that already reaches a "
    "Local/Fusion360 machine (its name, model, or 'vendor model') is refused up front. Verified "
    "reachable before this reports success; it persists in the Local library. "
    "Next: cam_edit_setup(setup=..., machine='<name>')."
)

tool = (
    Tool.create_simple(name="cam_create_machine", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string",
            "description": "The new machine's name - written to Machine.description and Machine.model."})
    .add_input_property(*_TEMPLATE.as_property())
    .add_input_property("vendor", {"type": "string", "description": "Machine vendor to record (optional)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
