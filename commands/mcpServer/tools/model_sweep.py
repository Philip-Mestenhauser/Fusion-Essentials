# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: sweep a sketch profile along a path into a solid (or surface).

  model_sweep -> drive a cross-section profile along a path (model edges or a path sketch) to make a
                 3D body: pipes, handrails, cables, moulding, extrusions that follow a curve. Solid by
                 default; an open-curve profile makes a SURFACE (no end caps). WRITES.

Companion to model_extrude / model_revolve - sweep follows an arbitrary 3D path instead of a straight
distance or a spin.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component, root_body_advisory, build_path
from . import _common
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

# profile may be a stable profile HANDLE (entityToken) or a {sketch, profile_index} selector.
_PROFILE = _inputs.ProfileRef("profile", required=True)
# target_bodies: scope a cut/intersect to these bodies so the sweep can't bleed through others.
_TARGET_BODIES = _inputs.BodyRefList("target_bodies", required=False,
    description="Bodies a cut/intersect may affect (prevents cut bleed-through into other bodies).")

# orientation keyword -> adsk.fusion.SweepOrientationTypes attribute (confirmed-live enum).
_ORIENTATIONS = {
    "perpendicular": "PerpendicularOrientationType",
    "parallel": "ParallelOrientationType",
}

# What this tool RETURNS: the resulting body names (a consumer key) + the SOLID/SURFACE verdict read
# BACK off the feature (never assumed) so a caller knows whether it must stitch the result.
RETURNS = [
    _outputs.ReturnsValue("result_bodies", "the names of the bodies the sweep created/modified"),
    _outputs.ReturnsValue("is_solid", "whether the sweep produced a SOLID (vs an open surface)"),
    _outputs.ReturnsValue("path_curves", "how many curves the built path actually holds"),
]


def _sketch_for_open(comp, profile_raw):
    """The sketch to build an OPEN profile from when the profile selector has no closed region. Only a
    {sketch, profile_index} selector names a sketch; a bare handle can't (it points at a closed
    Profile). Returns the sketch or None."""
    if isinstance(profile_raw, dict):
        nm = profile_raw.get("sketch", profile_raw.get("sketch_name", ""))
        sk, _ = _common.target_sketch(comp, nm)
        return sk
    return None


def _path_sketch_curves(host, path_raw):
    """How many SWEEPABLE curves the sketch a 'sketch:<name>' path names carries - None for any other
    path form or an unreadable sketch. Chaining from that sketch follows TANGENT CONTINUITY, so the
    built path can hold FEWER curves than the sketch does; publishing both is what makes a short
    chain visible.

    sketchCurves counts CONSTRUCTION geometry too, and a construction line is not part of any path -
    counting it would report a shortfall on a path that chained everything there was to chain, and
    blame tangency for it. Only real curves are counted; a curve whose isConstruction will not read
    counts as real (the conservative side: it can only shrink a warning, never invent one)."""
    if not (isinstance(path_raw, str) and path_raw.strip().lower().startswith("sketch:")):
        return None
    sk, _ = _common.target_sketch(host, path_raw.split(":", 1)[1].strip())
    if sk is None:
        return None
    curves = safe(lambda: sk.sketchCurves)
    if _common.counted(lambda: curves.count) is None:
        return None
    return sum(1 for c in _common.iter_collection(curves)
               if not bool(safe(lambda c=c: c.isConstruction, False)))


def _resolve_profile(comp, profile_raw, as_surface):
    """Resolve the sweep profile. Returns (profile_arg, want_solid, open_profile, host, error).

    Closed profile resolved -> that profile; want_solid = not as_surface (a closed profile swept with
    isSolid=False is an open surface tube, no end caps). No closed profile, but the selector names a
    sketch with open curves -> an OPEN profile is built and want_solid is forced False (an open profile
    cannot make a solid) - the open-path -> surface fallback.

    host is the component the sweep FEATURE must be built on: the profile's OWNING component. Handing
    another component's native profile to features.createInput raises 'InternalValidationError : bSet'
   , so the feature - and any open profile built here - is hosted on the sketch's owner, not the
    active component."""
    if profile_raw in (None, "", []):
        return None, None, None, None, ("'profile' is required (a profile handle from sketch_get, or a "
                                        "{sketch, profile_index} selector).")
    prof, err = _PROFILE.resolve(profile_raw)
    if prof is not None:
        host = _inputs.profile_host_component(prof, None, comp)
        return prof, (not bool(as_surface)), False, host, None
    # No closed profile. If the selector names a sketch with open curves, build an OPEN profile on the
    # sketch's OWNING component (the same host the feature is built on).
    sk = _sketch_for_open(comp, profile_raw)
    if sk is not None:
        host = _common.safe(lambda: sk.parentComponent) or comp
        open_prof, operr = _common.open_profile_from_sketch(
            host, sk, "for a surface sweep",
            no_curves_error=(f"Profile sketch '{safe(lambda: sk.name)}' has no curves to sweep. "
                             "Draw an open path (a line/arc/spline) or a closed region first."))
        if open_prof is not None:
            return open_prof, False, True, host, None
        return None, None, None, None, operr or err
    return None, None, None, None, err


def handler(profile=None, path=None, operation: str = "new", orientation: str = "perpendicular",
            as_surface: bool = False, target_bodies=None) -> dict:
    """See TOOL_DESCRIPTION."""
    op_key = (operation or "new").strip().lower()
    if op_key not in _common.OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")
    orient_key = (orientation or "perpendicular").strip().lower()
    if orient_key not in _ORIENTATIONS:
        return error(f"Unknown orientation '{orientation}'. Use: perpendicular, parallel.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    profile_arg, want_solid, open_profile, host, perr = _resolve_profile(comp, profile, as_surface)
    if perr:
        return error(perr)

    # Build the path AND the feature on the profile's OWNING component (host) - a profile-consuming
    # feature created on the active component raises bSet when the profile is owned elsewhere.
    sweep_path, path_label, patherr = build_path(host, path)
    if patherr:
        return error(patherr)
    # What the built Path HOLDS, beside what the request named: the profile is driven over these
    # curves and no others, so a chain that stopped short sweeps a stub of the intended run.
    path_curves = _common.counted(lambda: sweep_path.count)
    sketch_curves = _path_sketch_curves(host, path)

    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        sweep_input = host.features.sweepFeatures.createInput(profile_arg, sweep_path, op)
    except Exception as e:
        return error(f"Could not start sweep: {e}. (The path must geometrically connect and the "
                     "profile should sit on/near the path start.)")

    try:
        sweep_input.isSolid = bool(want_solid) # False -> open surface, no end caps
        sweep_input.orientation = getattr(adsk.fusion.SweepOrientationTypes, _ORIENTATIONS[orient_key])
    except Exception as e:
        return error(f"Could not configure the sweep: {e}")

    # target_bodies: scope a cut/intersect to specific bodies so it can't bleed through others.
    scoped_to = None
    if target_bodies not in (None, "", []):
        if op_key == "new":
            return error("'target_bodies' only applies to cut/join/intersect (a 'new' body has no "
                         "participants). Remove it, or change the operation.")
        bodies_ents, berr = _TARGET_BODIES.resolve(target_bodies)
        if berr:
            return error(berr)
        try:
            sweep_input.participantBodies = list(bodies_ents)
            scoped_to = [safe(lambda b=b: b.name) for b in bodies_ents]
        except Exception as e:
            return error(f"Could not scope to target_bodies: {e}")

    try:
        feature = host.features.sweepFeatures.add(sweep_input)
    except Exception as e:
        return error(f"Sweep failed: {e}. (A 'cut'/'intersect' needs existing geometry to act on; "
                     "the profile and path must form a valid sweep.)")
    if not feature:
        return error(_common.no_feature_error(design, "Sweep"))

    body_names = [f["name"] for f in _common.body_facts(_common.result_bodies(feature))]

    # An operation that reports success but produced no body is a silent no-op - fail it honestly.
    if op_key == "new" and not body_names:
        return error("Sweep reported success but created no body. Check that the profile sits on the "
                     "path and the path forms a valid, connected sweep.")

    # SOLID/SURFACE verdict read BACK off the feature - never assumed from the request.
    is_solid = safe(lambda: feature.isSolid)
    if open_profile or is_solid is False:
        note = ("Swept into a SURFACE (no end caps) - pair with model_stitch to close several "
                "surfaces into a solid.")
    else:
        note = "Profile swept into a solid along the path. Pair with view_screenshot (iso) to view it."
    if op_key == "new":
        adv = root_body_advisory(design, host)
        if adv:
            note += " " + adv
    if path_curves is not None and sketch_curves is not None and path_curves < sketch_curves:
        note += (f" WARNING: the path chained {path_curves} of the sketch's {sketch_curves} curves, so "
                 "the sweep covers only that run - chaining follows tangent continuity and a sharp "
                 "corner stops it. Make the junction tangent, or sweep each run separately.")

    payload = {
        "swept": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "component": safe(lambda: feature.parentComponent.name),
        "path": path_label,
        "path_curves": path_curves,
        "orientation": orient_key,
        "as_surface": bool(open_profile or is_solid is False),
        "open_profile": bool(open_profile),
        "is_solid": is_solid,
        "scoped_to_bodies": scoped_to,
        "result_bodies": body_names,
        "note": note,
    }
    # Only a 'sketch:<name>' path has a source curve count to compare against; for an edge path there
    # is no such number, and publishing a null would read as an unreadable sketch.
    if sketch_curves is not None:
        payload["path_sketch_curves"] = sketch_curves
    return ok(payload)


TOOL_DESCRIPTION = (
"Sweep a sketch profile along a path into a 3D solid - a cross-section driven along a curve (pipes, "
"handrails, cables, moulding, path-following extrusions). Companion to model_extrude / model_revolve. "
"See 'profile' and 'path' for their accepted forms. "
"'operation': new | join | cut | intersect - cut/intersect act on existing bodies. 'orientation': "
"perpendicular (default) | parallel to the path. 'as_surface' forces an open (no-caps) surface. "
"Every result reports 'is_solid' and the resulting body names.\n\n"
+ _outputs.produces_block(RETURNS)
)

sweep_tool = (
    Tool.create_simple(name="model_sweep", description=TOOL_DESCRIPTION)
    .add_input_property("profile", {"type": ["string", "object"],
            "description": "The cross-section: a profile 'handle' from sketch_get (robust), or a {sketch, profile_index} selector. An open-curve sketch (no closed region) sweeps into a SURFACE."})
    .add_input_property("path", {"type": ["string", "array"], "items": {"type": "string"},
            "description": "The sweep path: a find_geometry edge 'handle' (a single handle chains across TANGENT connections; a sharp corner stops the chain - the 'path' count is the truth), a JSON list of edge handles (used exactly - they must connect into one path), OR 'sketch:<name>' to chain a path sketch's curves."})
    .add_input_property(*_inputs.boolean_op(default="new").as_property())
    .add_input_property("orientation", {"type": "string", "enum": ["perpendicular", "parallel"],
            "description": "How the profile is oriented along the path: perpendicular (default) keeps it normal to the path; parallel keeps it parallel to its start plane."})
    .add_input_property("as_surface", {"type": "boolean",
            "description": "Sweep into an open SURFACE (isSolid=False, no end caps) instead of a solid (default false). Auto-applied when the profile sketch has only an open path. Every result reports 'is_solid'."})
    .add_input_property("target_bodies", _TARGET_BODIES.schema())
    .strict_schema()
)
sweep_item = Item.create_tool_item(tool=sweep_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(sweep_item)
