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

# target-list input kind: bodies (handle/name) OR container occurrences/components - reused for all
# three collections. A CONTAINER occurrence keeps the setup's selection when contents are swapped
# (Setup.models/fixtures/stockSolids accept Occurrence, BRepBody, or MeshBody).
_TARGETS = _inputs.TargetRefList("bodies", required=False)

# WCS geometry-binding: each key drives one CadObjectParameterValue plus the choice-mode it needs. A
# selected entity means the WCS follows that geometry, so a design edit that moves it invalidates the
# ops (the associativity the shop-template vision needs). {key: (mode_param, mode_value, cad_param,
# handle-requirement)}. origin accepts any point-ish geometry; the axes want a face (normal) or edge.
_WCS_BINDINGS = {
    "origin":  ("wcs_origin_mode",      "'point'",  "wcs_origin_point",         "any"),
    "z_axis":  ("wcs_orientation_mode", "'axesZX'", "wcs_orientation_axisZ",    "any"),
    "x_axis":  ("wcs_orientation_mode", "'axesZX'", "wcs_orientation_axisX",    "any"),
}
# origin resolves through a single-handle kind; the axes too. One handle each.
_WCS_HANDLE = _inputs.GeometryHandle("wcs_handle", require="any")

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
    """Resolve a list of body handles/names OR container occurrence/component names to entities the
    CAM API accepts (Occurrence / BRepBody / MeshBody) via the shared TargetRefList kind.
    Returns (entities, None) or (None, error)."""
    return _TARGETS.resolve(names)


def _resolve_wcs(wcs):
    """Resolve a {origin/z_axis/x_axis: handle} WCS request to {key: entity}. Each value is one
    find_geometry handle. Returns (resolved, None) or (None, error). Empty -> ({}, None)."""
    if not isinstance(wcs, dict):
        return None, ("'wcs' must be an object like {'origin': <handle>, 'z_axis': <handle>} - a "
                      "find_geometry handle per axis you want to bind.")
    unknown = [k for k in wcs if k not in _WCS_BINDINGS]
    if unknown:
        return None, (f"'wcs' has unknown key(s): {', '.join(unknown)}. "
                      f"Bindable: {', '.join(_WCS_BINDINGS)}.")
    resolved = {}
    for key, handle in wcs.items():
        if handle in (None, "", []):
            continue
        ent, herr = _WCS_HANDLE.resolve(handle)
        if herr:
            return None, f"wcs.{key}: {herr}"
        resolved[key] = ent
    return resolved, None


def _bind_cad_param(setup, cad_param_name, entity):
    """Bind one CadObjectParameterValue to a geometry entity IN PLACE (its .value takes a list; the
    CAMParameter.value itself has no setter). Returns the bound-entity count read back, or (None, err)."""
    p = safe(lambda: setup.parameters.itemByName(cad_param_name))
    if p is None:
        return None, f"setup has no parameter '{cad_param_name}'."
    cad = safe(lambda: p.value)
    if cad is None:
        return None, f"could not read '{cad_param_name}' value object."
    try:
        cad.value = [entity]                     # MUTATION - in place; do NOT reassign p.value
    except Exception as e:
        return None, f"could not bind '{cad_param_name}' to the geometry: {e}"
    after = safe(lambda: setup.parameters.itemByName(cad_param_name).value.value)
    return (len(list(after)) if after else 0), None


def handler(setup: str = "", parameters=None, models=None, fixtures=None, stock=None,
            machine: str = "", wcs=None) -> dict:
    """Edit a CAM setup's parameters, its model/fixture/stock bodies, its machine, and/or its WCS.

    setup: setup name (from cam_get). parameters: {name: expression} (or 'name=value,...') - set
    any setup parameter incl. the box-point WCS and stock* size controls. models/fixtures/stock: lists
    of body handles/names to REPLACE that collection with (stock switches the mode to from-solid;
    fixtures are auto-enabled). machine: a machine library entry ('vendor|model') to assign. wcs:
    {origin/z_axis/x_axis: find_geometry handle} to BIND the WCS to geometry (associative). Pass what
    you want to change; omit the rest. WRITES.
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
    want_wcs = wcs not in (None, "", {}, [])

    if not wanted and not body_args and not want_machine and not want_wcs:
        return error("Nothing to do. Provide 'parameters' {name: expression}, "
                     "'models'/'fixtures'/'stock' body lists, a 'machine', and/or a 'wcs' binding.")

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

    resolved_wcs = {}
    if want_wcs:
        resolved_wcs, werr = _resolve_wcs(wcs)
        if werr:
            return error(werr)

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
        # Fusion refuses the collection unless its enabling prerequisite is set FIRST: stock solids need
        # stockMode='SolidStock', fixtures need fixtureEnabled=True. Set them here, not in the caller.
        if arg == "stock":
            try:
                target.stockMode = adsk.cam.SetupStockModes.SolidStock
            except Exception as e:
                return error(f"Could not switch setup '{setup}' to from-solid stock (SolidStock mode): {e}.")
        elif arg == "fixtures":
            try:
                target.fixtureEnabled = True
            except Exception as e:
                return error(f"Could not enable fixtures on setup '{setup}': {e}.")
        coll = _object_collection()
        for b in bodies:
            coll.add(b)
        try:
            setattr(target, attr, coll)
        except Exception as e:
            return error(f"Could not set {arg} on setup '{setup}': {e}.")
        got = safe(lambda target=target, attr=attr: getattr(target, attr).count, 0) or 0
        # Read the collection back: setting it and getting 0 is a swallowed no-op, not a success.
        if got != len(bodies):
            return error(f"Set {arg} on setup '{setup}' but it reads back {got} bodies, not "
                         f"{len(bodies)} - the assignment did not take.")
        result[key] = got

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

    if resolved_wcs:
        wcs_set = {}
        for key, entity in resolved_wcs.items():
            mode_param, mode_value, cad_param, _req = _WCS_BINDINGS[key]
            mp = safe(lambda mode_param=mode_param: target.parameters.itemByName(mode_param))
            if mp is None:
                return error(f"Setup '{setup}' has no WCS mode parameter '{mode_param}'.")
            try:
                mp.expression = mode_value        # e.g. wcs_origin_mode -> 'point'
            except Exception as e:
                return error(f"Could not set WCS mode '{mode_param}={mode_value}' on setup '{setup}': {e}.")
            bound, berr = _bind_cad_param(target, cad_param, entity)
            if berr:
                return error(f"wcs.{key}: {berr}")
            # Binding and reading 0 entities back is a swallowed no-op - a geometry-bound WCS with no
            # geometry is not what was asked for.
            if not bound:
                return error(f"wcs.{key} bound no geometry - '{cad_param}' reads back empty after the "
                             "set. The handle may not be a valid WCS reference for this setup.")
            wcs_set[key] = {"mode": safe(lambda mode_param=mode_param:
                                         target.parameters.itemByName(mode_param).value.value),
                            "bound_entities": bound}
        result["wcs_set"] = wcs_set

    result["note"] = ("Setup edited. Existing toolpaths are now OUT OF DATE - regenerate with "
                      "cam_generate. A geometry-bound WCS (via 'wcs') follows the selected geometry, so "
                      "a later design edit that moves it invalidates the ops.")
    return ok(result)


TOOL_DESCRIPTION = (
    "Edit a CAM SETUP - the setup-level companion to cam_edit_operation, one call per concern. 'setup' = "
    "setup name. 'machine' = a machine library entry ('vendor|model', e.g. 'Haas|VF-2') to assign - the "
    "prerequisite a job needs before posting. 'stock'/'fixtures'/'models' = lists of bodies (find_geometry "
    "handles or names) OR container occurrence/component names that REPLACE that collection: 'stock' "
    "switches the setup to from-solid stock and 'fixtures' auto-enables fixtures. Select a CONTAINER (not "
    "the body inside it) so the setup keeps its selection when the container's contents are swapped - the "
    "shop-template pattern. 'wcs' = {origin/z_axis/x_axis: find_geometry handle} to BIND the "
    "WCS to geometry, so a design edit that moves it invalidates the ops (associativity). 'parameters' = "
    "{name: expression} to set any other setup parameter (box-point WCS, stock size stockZHigh/...); "
    "validated all-before-any so a typo can't half-edit. Every write is read back to confirm it took. "
    "After editing, regenerate toolpaths with cam_generate. WRITES CAM data."
)

tool = (
    Tool.create_simple(name="cam_edit_setup", description=TOOL_DESCRIPTION)
    .add_input_property("setup", {"type": "string", "description": "Setup name (from cam_get)."})
    .add_input_property("parameters", {"type": "object",
            "description": "Setup parameters to set: {name: expression} (or 'name=value,...'). e.g. {'wcs_origin_boxPoint': \"'top center'\", 'stockZHigh': '2.5'}."})
    .add_input_property("models", {"type": "array", "items": {"type": "string"},
            "description": "What to machine: bodies (handles/names) or container occurrence/component names - REPLACES the model set."})
    .add_input_property("fixtures", {"type": "array", "items": {"type": "string"},
            "description": "Fixtures: bodies (handles/names) or a fixture container occurrence/component name - REPLACES the fixture set."})
    .add_input_property("stock", {"type": "array", "items": {"type": "string"},
            "description": "Solid stock: bodies (handles/names) or a stock container occurrence/component name - REPLACES the stock set."})
    .add_input_property("machine", {"type": "string",
            "description": "Machine to assign: 'vendor|model' (or a bare model) from the machine library."})
    .add_input_property("wcs", {"type": "object",
            "description": "Bind the WCS to geometry: {origin/z_axis/x_axis: find_geometry handle}. Sets the matching mode and follows that geometry."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
