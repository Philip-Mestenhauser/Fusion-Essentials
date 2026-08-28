# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: revolve a sketch profile about an axis into a solid.

  model_revolve -> spin a closed sketch profile around an axis to make a solid of revolution
                   (shafts, pistons, pulleys, bottles, anything turned). Choose the feature
                   operation, the angle (full 360 or partial), and symmetry. WRITES.

The companion to model_extrude - revolve sweeps a profile around an axis instead of extruding it
straight.
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component, root_body_advisory
from . import _common
from . import _geom
from . import _inputs
from . import _assert

app = adsk.core.Application.get()

# profile_index may carry a profile HANDLE (entityToken from sketch_get) - resolved via ProfileRef.
_PROFILE = _inputs.ProfileRef("profile_index")

_VEC_TO_KEY = {(1, 0, 0): "x", (0, 1, 0): "y", (0, 0, 1): "z"}

# The revolve axis. RevolveFeatures.createInput's axis takes the entity that DEFINES the axis - "a
# sketch line, construction axis, linear edge or a face that defines an axis (cylinder, cone, torus,
# etc.)" (the installed API's own doc) - so face_entity hands the FACE itself through. A face resolved
# to a direction VECTOR drops the axis POSITION: a cylinder face at x=30 would revolve about the world
# axis through the ORIGIN, which is silent wrong geometry.
_AXIS = _inputs.AxisRef("axis", face_entity=True, default="z")


def _in_context(ent, comp, design):
    """(entity, error) - the axis entity in a form the revolve's component can consume. Mirrors
    model_pattern._in_context, the sibling running the same face_entity AxisRef.

    MEASURED: a NATIVE entity (assemblyContext None) owned by ANOTHER component kills the call
    outright - the cross-component face took the whole script host down, not a catchable raise -
    while the same face proxied into the occurrence that carries it is accepted and revolves about
    the correct off-origin axis. An entity that already has an assembly context, or one owned by
    `comp` itself, passes untouched.

    The walk itself - and its refusal of a component placed SEVERAL times, which for a revolve means
    a healthy-looking body turning about the wrong line - is _inputs.single_placement; the leaf op
    here is the proxy revolveFeatures.createInput takes, worded to complete the caller's "Could not
    resolve axis '<x>': ..." sentence."""
    occ, err = _inputs.single_placement("it", ent, comp, design)
    if err:
        return None, err
    if occ is None:
        return ent, None
    proxy = safe(lambda: ent.createForAssemblyContext(occ))
    if proxy is None:
        path = safe(lambda: occ.fullPathName) or "its one occurrence"
        return None, (f"it could not be brought into the revolve's assembly context ({path}). Pass "
                      "a handle at geometry in the sketch's own component, or a world axis (x/y/z).")
    return proxy, None


def _cut_check_bodies(comp):
    """The solid bodies a cut/intersect revolve can act on: every solid directly in the feature's
    host component. A revolve takes no participant-body scoping, so there is no narrower sample.

    Resolved ONCE, before the mutation, and the same objects re-read afterwards - that is the id()
    keying precondition _geom.volumes documents."""
    return [b for b in _common.iter_collection(safe(lambda: comp.bRepBodies))
            if safe(lambda b=b: b.isSolid)]


def _axis_entity(design, comp, sketch, axis):
    """Resolve the revolve axis to an entity: world x/y/z -> that origin ConstructionAxis; a
    find_geometry/sketch 'handle' -> the resolved straight edge / sketch line / construction axis, or
    the axis-defining FACE itself (via the shared AxisRef kind); or 'line:<index>' -> a line by
    position in the profile's OWN sketch - the one selector AxisRef can't express generically, since
    it has no notion of "this profile's sketch".
    Returns (axis_entity, label) on success, or (None, error_detail) on failure."""
    a = (axis or "z").strip().lower()
    if a.startswith("line:"):
        try:
            idx = int(a.split(":", 1)[1])
        except Exception:
            return None, f"'{axis}' is not a valid line selector (want 'line:<index>')."
        lines = safe(lambda: sketch.sketchCurves.sketchLines)
        n = safe(lambda: lines.count, 0) if lines else 0
        if lines is None or not (0 <= idx < n):
            return None, f"line index {idx} out of range - sketch has {n} line(s)."
        return safe(lambda: lines.item(idx)), f"sketch {a}"

    tagged, err = _AXIS.resolve(axis)
    if err:
        return None, err
    kind, val = tagged
    if kind == "world":
        key = _VEC_TO_KEY.get(val)
        ent = _inputs.world_construction_axis(comp, key) if key else None
        return ent, f"{key}-axis"
    # kind == "edge": the resolved axis ENTITY - a straight BRepEdge, sketch line, construction axis
    # or, on this face_entity input, the axis-defining face - brought into `comp`'s assembly context
    # when it is native to another component. Labelled as model_pattern._direction_label labels one:
    # the entity's own NAME when it has one (a construction axis does), else its type (no BRepEdge or
    # BRepFace carries a name) - never a world key the revolve did not use.
    ent, cerr = _in_context(val, comp, design)
    if cerr:
        return None, cerr
    name = safe(lambda: ent.name)
    return ent, (name if isinstance(name, str) and name else type(ent).__name__)


def handler(sketch_name: str = "", profile_index=0, axis: str = "z",
            angle_deg: float = 360.0, operation: str = "new", symmetric: bool = False,
            second_angle_deg: float = 0.0) -> dict:
    """See TOOL_DESCRIPTION."""
    op_key = (operation or "new").strip().lower()
    if op_key not in _common.OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")
    try:
        ang = float(angle_deg)
    except Exception:
        return error("angle_deg must be a number (degrees).")
    if ang == 0:
        return error("Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    sketch, requested, ambiguous = _common.find_or_recent_sketch(design, sketch_name)
    if ambiguous:
        return error(ambiguous)
    if not sketch:
        if requested:
            names = _common.all_sketch_names(design)
            avail = f" Available: {', '.join(names)}." if names else ""
            return error(f"No sketch named '{requested}'.{avail} Use sketch_get or sketch_create.")
        return error("No sketch to revolve. Create one and draw a closed profile first.")

    profiles = safe(lambda: sketch.profiles)
    pcount = safe(lambda: profiles.count, 0) if profiles else 0
    # HANDLE path: a profile entityToken from sketch_get (a ProfileRef) targets the exact region -
    # the robust pick on a multi-profile / on-face sketch, where a blind index is ambiguous.
    if _inputs.is_handle(profile_index):
        profile, perr = _PROFILE.resolve(profile_index)
        if perr:
            return error(perr)
        idx = "handle"
    else:
        if pcount == 0:
            return error(f"Sketch '{safe(lambda: sketch.name)}' has no closed profile to revolve.")
        try:
            idx = int(profile_index)
        except Exception:
            idx = 0
        if idx < 0 or idx >= pcount:
            return error(f"profile_index {idx} out of range - sketch has {pcount} profile(s).")
        profile = profiles.item(idx)

    # Host the feature on the sketch's OWNING component (see profile_host_component) and take origin
    # axes from that same component - a revolve input mixes contexts otherwise.
    host = _inputs.profile_host_component(profile, sketch, comp)
    axis_entity, axis_label = _axis_entity(design, host, sketch, axis)
    if not axis_entity:
        return error(f"Could not resolve axis '{axis}': {axis_label or 'use x | y | z, a straight-edge/sketch handle, or line:<index>.'}")

    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        rev_input = host.features.revolveFeatures.createInput(profile, axis_entity, op)
    except Exception as e:
        return error(f"Could not start revolve: {e}. (The axis must not pass through the profile "
    "in a way that self-intersects.)")

    angle_val = adsk.core.ValueInput.createByReal(math.radians(ang))
    try:
        second = float(second_angle_deg or 0.0)
        if second and not symmetric:
            # asymmetric two-sided revolve: 'ang' one way, 'second' the other.
            second_val = adsk.core.ValueInput.createByReal(math.radians(second))
            if not rev_input.setTwoSideAngleExtent(angle_val, second_val):
                return error(f"Fusion refused a two-sided revolve extent ({angle_deg} / "
                             f"{second_angle_deg} deg), so nothing was revolved.")
        else:
            if not rev_input.setAngleExtent(bool(symmetric), angle_val):
                return error(f"Fusion refused a {'symmetric ' if symmetric else ''}revolve extent "
                             f"of {angle_deg} deg, so nothing was revolved.")
    except Exception as e:
        return error(f"Could not set revolve angle: {e}")

    # cut/intersect MATERIAL evidence: the volumes the operation must move, sampled BEFORE the add.
    # A revolve reports a healthy feature for a profile that sweeps through empty air, so the feature
    # object alone cannot say material changed - only this before/after pair can.
    check_bodies = _cut_check_bodies(host) if op_key in ("cut", "intersect") else []
    vol_before = _geom.volumes(check_bodies)

    try:
        feature = host.features.revolveFeatures.add(rev_input)
    except Exception as e:
        # No coplanarity claim: the API projects an axis that is not in the profile's plane ONTO that
        # plane, so out-of-plane is not itself a cause. A profile CROSSING the axis is.
        return error(f"Revolve failed: {e}. (A 'cut'/'intersect' needs existing geometry to act on. "
    "An axis outside the profile's plane is projected onto it, so that is not the cause; a profile "
    "that CROSSES the axis is refused.)")
    if not feature:
        return error(_common.no_feature_error(design, "Revolve"))

    volume_delta_cm3 = None
    if check_bodies:
        delta, readable = _geom.volume_delta(check_bodies, vol_before)
        # A body whose volume read BEFORE and reads unreadable now was consumed whole - a real effect
        # that contributes no delta, so it must not be counted as "nothing moved".
        consumed = [b for b in check_bodies
                    if vol_before.get(id(b)) is not None and _geom.signed_volume(b) is None]
        if readable:
            volume_delta_cm3 = round(delta, 6)
        if readable and not consumed and abs(delta) < _common.NO_VOLUME_CHANGE_CM3:
            where = safe(lambda: host.name) or "the host component"
            return error(f"Revolve reported success but this {op_key} changed nothing - every solid "
                         f"body in '{where}' measures the volume it had before and none was "
                         "consumed, so the revolved shape does not overlap any of them. Check that "
                         "the profile and axis put the swept solid inside the target body (an "
                         "'intersect' whose target lies entirely INSIDE the swept solid also reads "
                         "this way). " + _common.failed_effect_remedy(design, feature))

    body_names = [f["name"] for f in _common.body_facts(_common.result_bodies(feature))]

    payload = {
        "revolved": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "sketch": safe(lambda: sketch.name),
        "component": safe(lambda: feature.parentComponent.name),
        "profile_index": idx,
        "axis": axis_label,
        "angle_deg": round(ang, 6),
        "second_angle_deg": round(float(second_angle_deg or 0.0), 6),
        "symmetric": bool(symmetric),
        "result_bodies": body_names,
        "note": ("Profile revolved into a solid. Pair with view_screenshot (iso) to view it."
                 + ((" " + _adv) if (op_key == "new" and (_adv := root_body_advisory(design, host))) else "")),
    }
    # Published only where the before/after pair was READABLE: a null here would read as "no material
    # moved" rather than "the measurement could not be taken", so the key is simply absent instead.
    if volume_delta_cm3 is not None:
        payload["volume_delta_cm3"] = volume_delta_cm3
    return ok(payload)


TOOL_DESCRIPTION = (
"Revolve a closed sketch profile about an axis into a 3D solid (a turned/lathe part). The companion "
"to model_extrude. 'sketch_name' selects the sketch "
"(omit = most recent); 'profile_index' picks the region (0-based index, OR a sketch_get profile "
"'handle' for a multi-profile sketch). A find_geometry handle at a CYLINDRICAL, conical or toroidal "
"face turns about that face's OWN axis line, so an off-origin axis works. "
"The profile must NOT CROSS the axis - a full-width section self-intersects and is refused; sketch "
"one half and revolve that. 'angle_deg' is "
"the sweep (360 = full revolve). 'operation': new | join | cut | "
"intersect. 'symmetric' splits the angle both ways. The feature/body land in "
"the sketch's OWNING component (reported as 'component'). Returns the resulting body names."
)

revolve_tool = (
    Tool.create_simple(name="model_revolve", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch holding the profile (omit = most recent sketch)."})
    .add_input_property("profile_index", {"type": ["integer", "string"],
            "description": "Which region to revolve: a 0-based index (default 0), OR a profile 'handle' from sketch_get (targets one region of a multi-profile sketch)."})
    .add_input_property("axis", {"type": "string",
            "description": _AXIS.schema()["description"] + " Or 'line:<index>' for a straight line "
            "in the profile's own sketch."})
    .add_input_property("angle_deg", {"type": "number",
            "description": "Revolve angle in degrees (360 = full revolve, default)."})
    .add_input_property("second_angle_deg", {"type": "number",
            "description": "Also revolve this many degrees the OTHER direction (asymmetric two-sided revolve; ignored when symmetric)."})
    .add_input_property(*_inputs.boolean_op(default="new").as_property())
    .add_input_property("symmetric", {"type": "boolean",
            "description": "Split the angle both ways about the profile plane (default false)."})
    .strict_schema()
)
revolve_item = Item.create_tool_item(tool=revolve_tool, write="write", handler=handler, run_on_main_thread=True,
                                     postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(revolve_item)
