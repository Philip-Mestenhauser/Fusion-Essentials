# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Edit a CAM setup: any named parameter (WCS orientation/origin, stock size, ...) and/or its
model/fixture/stock body collections. Parameters are validated before any is applied, so a typo
can't half-edit the setup."""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, find_setup
from . import _inputs
# Reuse the operation editor's parameter-parsing engine (single source of truth for {name:expr} / string).
from .cam_edit_operation import _parse_parameters

app = adsk.core.Application.get()

# the three editable body collections: input arg -> (Setup attribute, result key)
_BODY_COLLECTIONS = {
    "models": ("models", "models_set"),
    "fixtures": ("fixtures", "fixtures_set"),
    "stock": ("stockSolids", "stock_set"),
}

# strict body-list input kind (handles or names; solid/mesh/surface aware) - reused for all three.
_BODIES = _inputs.BodyRefList("bodies", required=False)

# Non-network machine library locations searched for a machine by vendor/model (Fusion360 = the
# bundled sample machines; Local = the user's saved ones). The cloud/network locations are skipped so
# a headless assignment never blocks on a fetch.
_MACHINE_LOCATIONS = ("LocalLibraryLocation", "Fusion360LibraryLocation")


def _object_collection():
    return adsk.core.ObjectCollection.create()


def _machine_label(m):
    """Readable machine label: .description, else 'vendor model'. adsk.cam.Machine has no .name."""
    if not m:
        return None
    desc = safe(lambda: m.description)
    if desc:
        return desc
    label = ((safe(lambda: m.vendor) or "") + " " + (safe(lambda: m.model) or "")).strip()
    return label or "(unnamed machine)"


def _resolve_machine(machine):
    """Resolve a 'machine' string (vendor|model, vendor/model, or a bare model) to a single Machine
    from the machine library. Returns (machine, label, None), or (None, None, error) when nothing
    matches or the match is ambiguous - it refuses to guess between two machines."""
    machine = (machine or "").strip()
    sep = "|" if "|" in machine else ("/" if "/" in machine else "")
    if sep:
        vendor, model = (p.strip() for p in machine.split(sep, 1))
    else:
        vendor, model = "", machine
    try:
        lib = adsk.cam.CAMManager.get().libraryManager.machineLibrary
    except Exception as e:
        return None, None, f"Could not access the machine library: {e}"

    found, labels = [], []
    for loc_name in _MACHINE_LOCATIONS:
        loc = getattr(adsk.cam.LibraryLocations, loc_name, None)
        if loc is None:
            continue
        try:
            q = lib.createQuery(loc, vendor, model)
            matches = q.execute() or []
        except Exception:
            continue
        for m in matches:
            lb = _machine_label(m)
            if lb not in labels:      # dedupe identical machines that appear in more than one location
                labels.append(lb)
                found.append(m)
        if found:
            break                     # prefer the first location that yields any match
    if not found:
        return None, None, (f"No machine matches '{machine}' (vendor='{vendor}', model='{model}') in the "
                            "Local or Fusion360 machine libraries. Use 'vendor|model' from the machine "
                            "you see in the Manufacture machine library.")
    if len(found) > 1:
        return None, None, (f"Ambiguous machine '{machine}' - {len(found)} matches: "
                            f"{', '.join(labels[:8])}. Narrow it with 'vendor|model'.")
    return found[0], labels[0], None


def _resolve_bodies(names):
    """Resolve a list of body handles/names to body objects via the strict BodyRefList kind.
    Returns (bodies, None) or (None, error)."""
    return _BODIES.resolve(names)


def handler(setup: str = "", parameters=None, models=None, fixtures=None, stock=None,
            machine: str = "") -> dict:
    """Edit a CAM setup's parameters, its model/fixture/stock bodies, and/or its machine.

    setup: setup name (from cam_get). parameters: {name: expression} (or 'name=value,...') - set
    any setup parameter incl. the wcs_* (WCS) and stock* controls. models/fixtures/stock: lists of body
    handles/names to REPLACE that collection with. machine: a machine library entry ('vendor|model')
    to assign - the setup-level prerequisite for posting. Pass what you want to change; omit the rest.
    WRITES.
    """
    if not (setup or "").strip():
        return error("Provide 'setup' - the CAM setup name (see cam_get).")

    # parse parameters (may be empty)
    wanted = {}
    if parameters not in (None, "", {}):
        wanted, perr = _parse_parameters(parameters)
        if perr:
            return error(perr)

    body_args = {k: v for k, v in (("models", models), ("fixtures", fixtures), ("stock", stock))
                 if v not in (None, "", [])}
    want_machine = (machine or "").strip()

    if not wanted and not body_args and not want_machine:
        return error("Nothing to do. Provide 'parameters' {name: expression}, "
                     "'models'/'fixtures'/'stock' body lists, and/or a 'machine'.")

    cam, cerr = get_cam()
    if cerr:
        return error(cerr)
    target, available = find_setup(cam, setup)
    if not target:
        return error(f"No setup named '{setup}'. Setups: {', '.join(str(n) for n in available)}.")

    # ── validate EVERYTHING before applying anything (no half-edited setup) ──
    sp = safe(lambda: target.parameters)
    resolved_params = {}
    missing = []
    for name in wanted:
        p = safe(lambda name=name: sp.itemByName(name)) if sp else None
        if p is None:
            missing.append(name)
        else:
            resolved_params[name] = p
    if missing:
        return error(f"Setup '{setup}' has no parameter(s): {', '.join(missing)}. "
                     "(Read the setup's parameter names first; only existing ones are settable.)")

    resolved_bodies = {}
    for arg, names in body_args.items():
        bodies, berr = _resolve_bodies(names)
        if berr:
            return error(f"{arg}: {berr}")
        resolved_bodies[arg] = bodies

    resolved_machine = None
    if want_machine:
        m_obj, m_label, m_err = _resolve_machine(want_machine)
        if m_err:
            return error(m_err)
        resolved_machine = (m_obj, m_label)

    # ── apply: parameters first, then body collections ──
    changed = []
    for name, expr in wanted.items():
        p = resolved_params[name]
        before = safe(lambda p=p: p.expression)
        try:
            p.expression = str(expr)
        except Exception as e:
            return error(f"Could not set '{name}' = '{expr}' on setup '{setup}': {e}. "
                         f"(Already applied: {', '.join(c['name'] for c in changed) or 'none'}.)")
        changed.append({"name": name, "before": before, "after": safe(lambda p=p: p.expression)})

    result = {
        "edited": True,
        "setup": safe(lambda: target.name),
        "updated_count": len(changed),
        "changed": changed,
    }

    for arg, bodies in resolved_bodies.items():
        attr, key = _BODY_COLLECTIONS[arg]
        coll = _object_collection()
        for b in bodies:
            coll.add(b)
        try:
            setattr(target, attr, coll)
        except Exception as e:
            return error(f"Could not set {arg} on setup '{setup}': {e}. "
                         "(Fixtures need fixtures enabled; solid stock needs stockMode='SolidStock'.)")
        result[key] = safe(lambda target=target, attr=attr: getattr(target, attr).count, len(bodies))

    if resolved_machine is not None:
        m_obj, m_label = resolved_machine
        try:
            target.machine = m_obj                       # Setup.machine takes a transient copy
        except Exception as e:
            return error(f"Could not assign machine '{want_machine}' to setup '{setup}': {e}.")
        # Read Setup.machine back to CONFIRM the assignment took - a swallowed no-op must not report ok.
        applied = _machine_label(safe(lambda: target.machine))
        if not applied or applied != m_label:
            return error(f"Machine assignment did not take on setup '{setup}': set '{m_label}' but the "
                         f"setup now reports '{applied}'.")
        result["machine_set"] = applied

    result["note"] = ("Setup edited. Existing toolpaths are now OUT OF DATE - regenerate with "
                      "cam_generate. The WCS is steered via the wcs_* parameters (the matrix itself is "
                      "read-only).")
    return ok(result)


TOOL_DESCRIPTION = (
    "Edit a CAM SETUP - its parameters and/or its model/fixture/stock bodies (the setup-level companion "
    "to cam_edit_operation). 'setup' = setup name. 'parameters' = {name: expression} (or 'name=value,...') "
    "to set ANY setup parameter - this is how you configure the WCS (wcs_orientation_mode, wcs_origin_mode, "
    "wcs_origin_boxPoint, wcs_orientation_axisZ/flipZ, ...) and stock size (stockXLow/High, stockZHigh, ...); "
    "the WCS matrix itself is read-only. 'models'/'fixtures'/'stock' = body lists (find_geometry handles or "
    "names) that REPLACE that collection. 'machine' = a machine library entry ('vendor|model', e.g. "
    "'Haas|VF-2') to assign to the setup - the setup-level prerequisite a job needs before posting; it is "
    "read back to confirm the assignment took. Parameters are validated all-before-any (a typo can't "
    "half-edit). After editing, regenerate toolpaths with cam_generate. WRITES CAM data."
)

tool = (
    Tool.create_simple(name="cam_edit_setup", description=TOOL_DESCRIPTION)
    .add_input_property("setup", {"type": "string", "description": "Setup name (from cam_get)."})
    .add_input_property("parameters", {"type": "object",
            "description": "Setup parameters to set: {name: expression} (or 'name=value,...'). e.g. {'wcs_origin_boxPoint': \"'top center'\", 'stockZHigh': '2.5'}."})
    .add_input_property("models", {"type": "array", "items": {"type": "string"},
            "description": "Bodies to machine (handles/names) - REPLACES the model set."})
    .add_input_property("fixtures", {"type": "array", "items": {"type": "string"},
            "description": "Fixture bodies (handles/names) - REPLACES the fixture set."})
    .add_input_property("stock", {"type": "array", "items": {"type": "string"},
            "description": "Solid stock bodies (handles/names) - REPLACES the stock set."})
    .add_input_property("machine", {"type": "string",
            "description": "Machine to assign: 'vendor|model' (or a bare model) from the machine library."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
