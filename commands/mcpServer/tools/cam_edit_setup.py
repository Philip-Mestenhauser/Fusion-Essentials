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
from ._cam_common import get_cam, find_setup, expression_error
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

# target-list input kind: bodies (handle/name) OR component occurrences - reused for all
# three collections. A COMPONENT occurrence keeps the setup's selection when contents are swapped
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
# origin resolves through a single-handle kind; the axes too. One handle each. A WCS value may ALSO be a
# JOINT ORIGIN - the self-centering frame a template ships for exactly this (bind the WCS to it so it
# re-centres when stock size changes) - by the handle assembly_get(include=['joint_origins']) mints OR by
# name. JointOriginRef recognises both (and refuses an ambiguous name).
_WCS_HANDLE = _inputs.GeometryHandle("wcs_handle", require="any")
_WCS_JO = _inputs.JointOriginRef("wcs_jo")

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


def _machine_ident(m):
    """(label, vendor, model) for a Machine - label is the readable name (description or 'vendor model')."""
    return _machine_label(m), (safe(lambda: m.vendor) or ""), (safe(lambda: m.model) or "")


def _query_machines(lib, vendor, model):
    """Run the machine-library query for (vendor, model) across the Local + bundled Fusion360 locations,
    deduped by label. Returns a list of (machine, label, vendor, model); the FIRST location that yields
    any match wins (Local before Fusion360)."""
    found, labels = [], set()
    for loc_name in _MACHINE_LOCATIONS:
        loc = getattr(adsk.cam.LibraryLocations, loc_name, None)
        if loc is None:
            continue
        try:
            matches = lib.createQuery(loc, vendor, model).execute() or []
        except Exception:
            continue
        for m in matches:
            label, v, mo = _machine_ident(m)
            if label in labels:       # dedupe identical machines that appear in more than one location
                continue
            labels.add(label)
            found.append((m, label, v, mo))
        if found:
            break                     # prefer the first location that yields any match
    return found


def _exact_machine(cands, machine, vendor, model):
    """Exact-match, MOST-SPECIFIC first: a unique full-LABEL match wins over a unique 'vendor model'
    match, which wins over a unique model match. Prioritizing the label is what makes same-model
    variants selectable - a Haas library ships three machines that all report vendor|model 'HAAS|VF-2'
    and differ ONLY by description ('Haas VF-2', 'Haas VF-2 with TRT100', ...), so matching the model
    alone can't pick one, but the exact description can. Returns the single candidate at the first
    priority yielding exactly one hit, else None (still ambiguous)."""
    ml = (model or "").strip().lower()
    ven = (vendor or "").strip().lower()
    full = (machine or "").strip().lower()

    def _unique(pred):
        hits, seen = [], set()
        for tup in cands:
            _m, label, v, mo = tup
            if pred(label, v, mo):
                key = (label or "").lower()
                if key not in seen:
                    seen.add(key)
                    hits.append(tup)
        return hits[0] if len(hits) == 1 else None

    return (_unique(lambda label, v, mo: (label or "").lower() == full)                       # label
            or _unique(lambda label, v, mo: ((v or "") + " " + (mo or "")).strip().lower() == full)  # vendor model
            or _unique(lambda label, v, mo: bool(ml) and (mo or "").lower() == ml             # model (+vendor)
                       and (not ven or (v or "").lower() == ven)))


def _resolve_machine(machine):
    """Resolve a 'machine' string (vendor|model, vendor/model, a bare model, or a full description) to a
    single Machine. Returns (machine, label, None), or (None, None, error) when nothing matches or the
    match is ambiguous - it refuses to guess. Exact match (LABEL first) beats a shared prefix."""
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

    cands = _query_machines(lib, vendor, model)
    # WIDEN when the model as given matches nothing: the library query prefix-matches the MODEL field,
    # but a variant's distinguishing text ('Haas VF-2 with TRT100') lives in its DESCRIPTION, and callers
    # pass the label they SEE ('Haas VF-2', 'Haas|Haas VF-2'). Recover a (vendor, broad-model-token) to
    # fetch the candidate POOL, then LABEL-match it below.
    if not cands:
        v2, broad = vendor, model
        if vendor and model.lower().startswith(vendor.lower() + " "):
            broad = model[len(vendor):].strip()               # 'Haas|Haas VF-2' -> model 'VF-2'
        elif not vendor and " " in machine:
            v2, broad = machine.split(" ", 1)                 # bare 'Haas VF-2...' -> vendor 'Haas'
        broad = broad.split(" ", 1)[0].strip() if broad else broad   # first model token ('VF-2')
        v2 = v2.strip()
        if (v2, broad) != (vendor, model) and (v2 or broad):
            widened = _query_machines(lib, v2, broad)
            if widened:
                cands, vendor, model = widened, v2, broad

    if not cands:
        return None, None, (f"No machine matches '{machine}' (vendor='{vendor}', model='{model}') in the "
                            "Local or Fusion360 machine libraries. Use the machine name (its description) "
                            "you see in the Manufacture machine library.")
    # EXACT match wins BEFORE refusing ambiguity (the house rule).
    exact = _exact_machine(cands, machine, vendor, model)
    if exact is not None:
        return exact[0], exact[1], None
    if len(cands) > 1:
        # List the distinct LABELS (descriptions) - the selectable key, since same-model variants share
        # vendor|model. The agent passes one of these exact names back to pick a specific variant.
        labels, seen = [], set()
        for (_m, lab, _v, _mo) in cands:
            if lab and lab.lower() not in seen:
                seen.add(lab.lower())
                labels.append(lab)
        return None, None, (f"Ambiguous machine '{machine}' - {len(labels)} matches: "
                            f"{', '.join(labels[:8])}. Pass one of these exact names.")
    return cands[0][0], cands[0][1], None


def _resolve_bodies(names):
    """Resolve a list of body handles/names OR component occurrence names to entities the
    CAM API accepts (Occurrence / BRepBody / MeshBody) via the shared TargetRefList kind.
    Returns (entities, None) or (None, error)."""
    return _TARGETS.resolve(names)


def _resolve_wcs_value(value):
    """Resolve one WCS binding value: a JOINT ORIGIN (its handle OR name, via JointOriginRef) OR a
    find_geometry face/edge/vertex handle. JO is tried first (it also recognises a JO entityToken); a
    non-JO handle falls back to the geometry handle. Returns (entity, is_joint_origin, error)."""
    jo, jerr = _WCS_JO.resolve(value)
    if jo is not None:
        return jo, True, None
    ent, herr = _WCS_HANDLE.resolve(value)
    if ent is not None:
        return ent, False, None
    # both failed: surface the more relevant error (the handle error for a token, the JO error for a name)
    return None, False, ((herr if _inputs.is_handle(value) else jerr) or herr or jerr)


def _resolve_wcs(wcs):
    """Resolve a {origin/z_axis/x_axis: value} WCS request to {key: entity}. Each value is a Joint Origin
    (handle or name) or a find_geometry handle. Returns (resolved, None) or (None, error). Empty ->
    ({}, None)."""
    if not isinstance(wcs, dict):
        return None, ("'wcs' must be an object like {'origin': <handle-or-JO>, 'z_axis': <handle>} - a "
                      "find_geometry handle or a Joint Origin per axis you want to bind.")
    unknown = [k for k in wcs if k not in _WCS_BINDINGS]
    if unknown:
        return None, (f"'wcs' has unknown key(s): {', '.join(unknown)}. "
                      f"Bindable: {', '.join(_WCS_BINDINGS)}.")
    resolved = {}
    for key, value in wcs.items():
        if value in (None, "", []):
            continue
        ent, _is_jo, err = _resolve_wcs_value(value)
        if err:
            return None, f"wcs.{key}: {err}"
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
    """See TOOL_DESCRIPTION."""
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

    # ── apply parameters first (before bodies/machine/wcs, so a rollback here leaves the setup as found) ──
    changed = []
    eval_failures = []
    for name, expr in wanted.items():
        p = resolved_params[name]
        before = safe(lambda p=p: p.expression)
        try:
            p.expression = str(expr)
        except Exception as e:
            return error(f"Could not set '{name}' = '{expr}' on setup '{setup}': {e}. "
                         f"(Already applied: {', '.join(c['name'] for c in changed) or 'none'}.)")
        # Read the expression BACK for its evaluation state: the platform stores an unresolvable
        # expression silently (edited==true, .expression echoes the text) - only .error exposes it.
        eval_err, eval_warn = expression_error(p)
        rec = {"name": name, "before": before, "after": safe(lambda p=p: p.expression)}
        if eval_warn:
            rec["warning"] = eval_warn
        changed.append(rec)
        if eval_err:
            eval_failures.append((name, str(expr), eval_err))

    # A stored-but-unevaluated expression is a swallowed no-op the platform reports as success. Roll
    # EVERY parameter we set back to its prior expression - nothing else is touched yet - and fail,
    # naming each offending value and Fusion's own reason, so the setup is left exactly as found.
    if eval_failures:
        for rec in changed:
            safe(lambda rec=rec: setattr(resolved_params[rec["name"]], "expression", rec["before"]))
        detail = "; ".join(f"'{n}' = '{e}' ({why})" for n, e, why in eval_failures)
        return error(f"Setup '{setup}': expression did not evaluate - {detail}. Rolled back all "
                     f"{len(changed)} parameter(s); no change was applied. (A CAM stock/setup expression "
                     "must reference existing parameters and resolve to a value - check names and units.)")

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
                      "cam_generate. A WCS bound via 'wcs' is a LIVE reference to the selected geometry "
                      "or Joint Origin (bound_entities), so the WCS re-derives from it - a self-centering "
                      "Joint Origin keeps the WCS centered as its anchor updates; a design edit that "
                      "moves the reference invalidates the ops.")
    return ok(result)


TOOL_DESCRIPTION = (
    "Edit a CAM SETUP - the setup-level companion to cam_edit_operation, one call per concern. 'setup' = "
    "setup name. 'machine' = a machine library entry ('vendor|model', e.g. 'Haas|VF-2') to assign (the "
    "prerequisite a job needs before posting; exact name wins over a shared prefix). 'stock'/'fixtures'/"
    "'models' = lists of bodies (find_geometry handles or names) OR component occurrence names "
    "that REPLACE that collection: 'stock' switches to from-solid stock, 'fixtures' auto-enables fixtures. "
    "Select the COMPONENT occurrence (not the body inside) so the setup keeps its selection when contents are swapped "
    "(the shop-template pattern). 'wcs' = {origin/z_axis/x_axis: a find_geometry handle OR a Joint Origin "
    "(handle/name)} BINDS the WCS to it as a live reference - a self-centering Joint Origin keeps the WCS "
    "centered as its anchor updates (associative). 'parameters' = {name: expression} for "
    "any other setup parameter (box-point WCS, stock size); validated all-before-any - a "
    "non-evaluating expression is rejected and rolled back. Every write is read back. Regenerate "
    "toolpaths after with cam_generate. WRITES CAM data."
)

tool = (
    Tool.create_simple(name="cam_edit_setup", description=TOOL_DESCRIPTION)
    .add_input_property("setup", {"type": "string", "description": "Setup name (from cam_get)."})
    .add_input_property("parameters", {"type": "object",
            "description": "Setup parameters to set: {name: expression} (or 'name=value,...'). e.g. {'wcs_origin_boxPoint': \"'top center'\", 'stockZHigh': '2.5'}."})
    .add_input_property("models", {"type": "array", "items": {"type": "string"},
            "description": "What to machine: bodies (handles/names) or component occurrence names - REPLACES the model set."})
    .add_input_property("fixtures", {"type": "array", "items": {"type": "string"},
            "description": "Fixtures: bodies (handles/names) or a fixture component occurrence name - REPLACES the fixture set."})
    .add_input_property("stock", {"type": "array", "items": {"type": "string"},
            "description": "Solid stock: bodies (handles/names) or a stock component occurrence name - REPLACES the stock set."})
    .add_input_property("machine", {"type": "string",
            "description": "Machine to assign: 'vendor|model' (or a bare model) from the machine library."})
    .add_input_property("wcs", {"type": "object",
            "description": "Bind the WCS: {origin/z_axis/x_axis: a find_geometry handle OR a Joint Origin (handle/name from assembly_get)}. Binds as a live reference (bound_entities read back); the WCS re-derives from it (associative)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
