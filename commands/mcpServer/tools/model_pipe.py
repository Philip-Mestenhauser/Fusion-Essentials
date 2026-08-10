# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: a pipe/tube along a path in ONE feature - section shape, section size, and an
optional hollow wall.

  model_pipe -> drive a circular/square/triangular section along a path (model edges or a path
                sketch) into a solid or HOLLOW tube: plumbing, conduit, tube frames. WRITES.

PipeFeatureInput.isHollow and .sectionThickness set each other (isHollow=true defaults the thickness
to 0.1 cm; a thickness turns isHollow on), so the two are set hollow-first, thickness-last and the
result is read back off the created feature.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component, build_path
from . import _common
from . import _assert
from . import _geom
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

# section keyword -> adsk.fusion.PipeSectionTypes attribute. The member is looked up through
# set_verified, so a name this Fusion version does not carry is reported rather than silently kept
# at the default section.
_SECTION_TYPES = {
    "circular": "CircularPipeSectionType",
    "square": "SquarePipeSectionType",
    "triangular": "TriangularPipeSectionType",
}

# A wall this thin (cm) is the platform's zero - see _hollow_verdict for what a zero wall means.
_WALL_EPS = 1e-9

_OPERATION = _inputs.boolean_op(default="new")
_SECTION_TYPE = _inputs.Choice("section_type", tuple(_SECTION_TYPES), default="circular",
    description="Cross-section shape driven along the path.")
_SECTION_SIZE = _inputs.Distance("section_size", allow_zero=False, allow_negative=False,
    required=True, description="Size of the cross-section.")
_WALL_THICKNESS = _inputs.Distance("wall_thickness", allow_zero=False, allow_negative=False,
    required=False, description="Wall thickness; giving it makes the pipe hollow. Omit it with "
                                "hollow=true to take the API's own wall, reported back measured.")
# path model-edge handles resolve inside the shared build_path; a path SKETCH is named by the
# 'sketch:<name>' form instead (sketch curves are not find_geometry handles).
_TARGET_BODIES = _inputs.BodyRefList("target_bodies", required=False,
    description="Bodies a cut/intersect may affect (prevents cut bleed-through into other bodies).")

# What this tool RETURNS: the resulting body names (a consumer key), the HOLLOW verdict read back off
# the feature (never the request), and the timeline feature name a delete keys off.
RETURNS = [
    _outputs.ReturnsName("feature", of="feature", consumers=["design_delete_feature"],
                         absent_when="no_timeline_feature"),
    _outputs.ReturnsValue("result_bodies", "the names of the bodies the pipe created/modified"),
    _outputs.ReturnsValue("hollow", "whether the CREATED pipe reads back hollow",
                          absent_when="no_timeline_feature"),
]


def _fraction(raw, label):
    """(fraction of the path, error) for a distanceOne/distanceTwo input. 0 < v <= 1, where 1 (the
    API's own default) is the whole path; None when not given. These are RATIOS of the path length,
    not lengths - 0.5 on a 10 cm path is measured to make exactly 5 cm of pipe - so 'units' does not
    apply to them."""
    if raw is None or raw == "":
        return None, None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None, f"'{label}' must be a number greater than 0 and at most 1, got {raw!r}."
    if not 0.0 < v <= 1.0:
        return None, (f"'{label}' must be greater than 0 and at most 1 - it is the FRACTION of the "
                      f"path the pipe covers (1 = the whole path), not a length - got {v}.")
    return v, None


def _watched_bodies(host, participants=()):
    """(bodies, {id: volume}, count) captured BEFORE the mutation - the census a cut/join/intersect is
    judged by, and the count a direct-mode 'new' body has to raise. Resolved once here so the same
    objects are read at both ends.

    The PARTICIPANTS are unioned in: a target body can live in another component, and a census scoped
    to the host's own collection would miss its volume change entirely - reporting a successful cut as
    'no volume changed'. De-duplicated by entityToken, never by identity: one physical body reached
    through two collection paths hands back two wrappers, and counting it twice doubles the delta."""
    bodies, seen = [], set()
    for b in list(_common.iter_collection(safe(lambda: host.bRepBodies))) + list(participants or []):
        key = safe(lambda b=b: b.entityToken) or id(b)
        if key in seen:
            continue
        seen.add(key)
        bodies.append(b)
    return bodies, _geom.volumes(bodies), _common.body_count(host)


def _hollow_verdict(feature, want_hollow, wall_cm):
    """The hollow read-back check, off the CREATED feature. Returns (is_hollow, thickness_cm, error).

    MEASURED (live sweep): a SOLID-requested pipe reads back isHollow TRUE with sectionThickness
    0.0 - the platform reports a solid section as a ZERO-WALL hollow one. So the wall carries the
    verdict and isHollow alone cannot: solid is a zero (or absent) wall, hollow needs a wall above
    zero that isHollow does not deny. A request that disagrees with what the feature reports is an
    error - the input-side isHollow/sectionThickness pair overwrite each other, so a wrong wall is a
    plausible outcome that must never be reported as success."""
    raw_hollow = safe(lambda: feature.isHollow)
    thickness_cm = _common.measured(lambda: feature.sectionThickness.value)
    has_wall = thickness_cm is not None and thickness_cm > _WALL_EPS
    is_hollow = has_wall and raw_hollow is not False
    if want_hollow:
        if not is_hollow:
            wall = "no section thickness" if thickness_cm is None else f"wall {round(thickness_cm, 6)} cm"
            return is_hollow, thickness_cm, (
                f"A hollow pipe was requested but the created pipe reads back SOLID ({wall}).")
        if wall_cm is not None and abs(thickness_cm - wall_cm) > max(1e-6, abs(wall_cm) * 1e-6):
            return is_hollow, thickness_cm, (
                f"The pipe was created with a wall of {round(thickness_cm, 6)} cm, not the "
                f"{round(wall_cm, 6)} cm asked for.")
    elif is_hollow:
        return is_hollow, thickness_cm, (
            "A solid pipe was requested but the created pipe reads back HOLLOW "
            f"(wall {round(thickness_cm, 6)} cm).")
    return is_hollow, thickness_cm, None


def handler(path=None, section_size=None, section_type: str = "circular", operation: str = "new",
            hollow=None, wall_thickness=None, path_fraction=None, path_fraction_reverse=None,
            target_bodies=None, units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    op_key, operr = _OPERATION.resolve(operation)
    if operr:
        return error(operr)
    st_key, sterr = _SECTION_TYPE.resolve(section_type)
    if sterr:
        return error(sterr)
    scale_factor, uerr = _inputs.UNITS.resolve(units)
    if uerr:
        return error(uerr)
    size_cm, szerr = _SECTION_SIZE.resolve_scaled(section_size, scale_factor)
    if szerr:
        return error(szerr)
    wall_cm, werr = _WALL_THICKNESS.resolve_scaled(wall_thickness, scale_factor)
    if werr:
        return error(werr)
    frac_one, f1err = _fraction(path_fraction, "path_fraction")
    if f1err:
        return error(f1err)
    frac_two, f2err = _fraction(path_fraction_reverse, "path_fraction_reverse")
    if f2err:
        return error(f2err)

    if hollow is False and wall_cm is not None:
        return error(f"'hollow' is false but 'wall_thickness' is {wall_thickness} - a wall thickness "
                     "only exists on a hollow pipe. Drop one of the two.")
    want_hollow = bool(hollow) or wall_cm is not None
    # distanceTwo is the extent in the REVERSE direction and the API requires distanceOne to be set
    # before it; together they cannot cover more than the whole path.
    if frac_two is not None and frac_one is None:
        return error(f"'path_fraction_reverse' is {frac_two} but 'path_fraction' is not set - the "
                     "forward extent must be given before the reverse one.")
    if frac_two is not None and frac_one + frac_two > 1.0 + 1e-9:
        return error(f"'path_fraction' ({frac_one}) plus 'path_fraction_reverse' ({frac_two}) is "
                     f"{round(frac_one + frac_two, 6)} - the two directions together cannot cover "
                     "more than the whole path (1.0).")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    pipe_path, path_label, patherr = build_path(comp, path)
    if patherr:
        return error(patherr)
    path_closed = safe(lambda: pipe_path.isClosed)
    if frac_two is not None and path_closed is not True:
        if path_closed is None:
            return error("'path_fraction_reverse' needs a CLOSED path and this path did not report "
                         "whether it is closed, so the reverse extent could not be verified. Re-run "
                         "without 'path_fraction_reverse'.")
        return error(f"'path_fraction_reverse' is {frac_two} but this path is OPEN. Fusion IGNORES "
                     "the reverse extent on an open path, so it is refused here instead of reported "
                     "as applied - use 'path_fraction' alone, or close the path.")

    # Resolved BEFORE the feature transaction opens, so a bad scope refuses without a half-built pipe.
    participants, scoped_to = None, None
    if target_bodies not in (None, "", []):
        if op_key == "new":
            return error("'target_bodies' only applies to cut/join/intersect (a 'new' body has no "
                         "participants). Remove it, or change the operation.")
        participants, berr = _TARGET_BODIES.resolve(target_bodies)
        if berr:
            return error(berr)
    # The census is counted on the TARGET's own component when there is one - a participant in another
    # component is where the volume actually moves - and on the pipe's component otherwise.
    census = _common.census_host(participants[0], comp) if participants else comp
    watched, vol_before, count_before = _watched_bodies(census, participants)

    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        pin = comp.features.pipeFeatures.createInput(pipe_path, op)
    except Exception as e:
        return error(f"Could not start the pipe: {e}. (The path must form one connected chain of "
                     "edges or sketch curves.)")
    if not pin:
        return error("Pipe createInput returned nothing, so no pipe was created.")

    st_member = safe(lambda: getattr(adsk.fusion.PipeSectionTypes, _SECTION_TYPES[st_key]))
    seterr = _common.set_verified(pin, "sectionType", st_member, f"section_type={st_key}",
                                  "PipeFeatureInput")
    if seterr:
        return error(f"{seterr} No pipe was created.")
    try:
        # The ValueInput properties are set bare: the input hands back a ValueInput rather than the
        # number, so they are verified off the created FEATURE (a ModelParameter) further down.
        pin.sectionSize = adsk.core.ValueInput.createByReal(size_cm)
    except Exception as e:
        return error(f"Could not set section_size: {e}")

    if want_hollow:
        # isHollow FIRST, thickness LAST: isHollow=true resets the thickness to the API default,
        # while setting a thickness also turns isHollow on - so this order keeps both.
        herr = _common.set_verified(pin, "isHollow", True, "hollow=true", "PipeFeatureInput")
        if herr:
            return error(f"{herr} No pipe was created.")
        if wall_cm is not None:
            try:
                pin.sectionThickness = adsk.core.ValueInput.createByReal(wall_cm)
            except Exception as e:
                return error(f"Could not set wall_thickness: {e}")
            if safe(lambda: pin.isHollow) is False:
                return error("Setting 'wall_thickness' switched the pipe input back to SOLID, so a "
                             "hollow pipe cannot be built from these inputs. No pipe was created.")

    for prop, value, label in (("distanceOne", frac_one, "path_fraction"),
                               ("distanceTwo", frac_two, "path_fraction_reverse")):
        if value is None:
            continue
        try:
            setattr(pin, prop, adsk.core.ValueInput.createByReal(value))
        except Exception as e:
            return error(f"Could not set {label}: {e}")

    if participants is not None:
        # The scope is ASSIGNED but UNVERIFIABLE. MEASURED: participantBodies is write-only - the
        # assignment succeeds and reading the property back raises AttributeError - so no read-back
        # of any kind (set_verified, token comparison) is possible. Nor does the volume gate below
        # stand in for one: it sums ONE total delta over the whole watched set, and a scope the
        # platform dropped moves that total MORE, not less. 'scoped_to' is what was requested.
        try:
            pin.participantBodies = list(participants)  # a Python list, not an ObjectCollection
            scoped_to = [safe(lambda b=b: b.name) for b in participants]
        except Exception as e:
            return error(f"Could not scope to target_bodies: {e}")

    try:
        feature = comp.features.pipeFeatures.add(pin)
    except Exception as e:
        return error(f"Pipe failed: {e}. (A 'cut'/'intersect' needs existing geometry to act on; the "
                     "section must fit around the path's corners.)")
    direct_no_feature = _common.direct_feature_absence(design, feature)
    if not feature and not direct_no_feature:
        return error(_common.no_feature_error(design, "Pipe"))

    # ── the effect gate: a body landed with volume, or some body's volume moved ──
    result = _common.result_bodies(feature) if feature else []
    body_names = [f["name"] for f in _common.body_facts(result)]
    volume_new = None
    delta_total = None
    if op_key == "new":
        if feature and not result:
            return error("Pipe reported success but created no body. Check that the path is one "
                         "connected chain and the section size fits around its corners.")
        if not feature:
            count_after = _common.body_count(census)
            if count_before is None or count_after is None or count_after <= count_before:
                return error("Pipe ran in a DIRECT design, which returns no feature object, and the "
                             "component's body count did not rise - so no pipe body can be shown to "
                             "exist. Read the model back with design_get before retrying.")
            made = _common.most_recent_body(census)
            body_names = [safe(lambda: made.name)] if made is not None else []
            volume_new = _geom.signed_volume(made) if made is not None else None
        else:
            vols = [v for v in _geom.volumes(result).values() if isinstance(v, (int, float))]
            volume_new = sum(vols) if vols else None
        if volume_new is not None and volume_new <= _common.NO_VOLUME_CHANGE_CM3:
            return error("Pipe created a body with no volume, so nothing usable was built. "
                         + _common.failed_effect_remedy(design, feature))
    else:
        delta_total, readable = _geom.volume_delta(watched, vol_before)
        if not readable:
            if not feature:
                return error(f"Pipe ran in a DIRECT design, which returns no feature object, and no "
                             f"body's volume could be read back - so whether the {op_key} changed "
                             "anything is UNVERIFIED. Re-read the bodies with model_inspect.")
            delta_total = None
        elif abs(delta_total) < _common.NO_VOLUME_CHANGE_CM3:
            return error(f"Pipe reported success but no body's volume changed, so the {op_key} "
                         "affected nothing. " + _common.failed_effect_remedy(design, feature))

    inv = 1.0 / scale_factor
    payload = {
        "piped": True,
        "operation": op_key,
        "component": safe(lambda: comp.name),
        "path": path_label,
        "path_closed": bool(path_closed) if path_closed is not None else None,
        "section_type": st_key,
        "section_size": round(float(section_size), 6),
        "units": units,
        "result_bodies": body_names,
        "scoped_to_bodies": scoped_to,
        "note": "Pipe built along the path. Pair with view_screenshot (iso) to view it.",
    }
    if scoped_to:
        payload["note"] += (" 'scoped_to_bodies' is what was REQUESTED: participantBodies is a "
                            "write-only property, so which bodies the pipe actually acted on cannot "
                            "be read back - check the affected bodies with model_inspect.")
    if frac_one is not None:
        payload["path_fraction"] = frac_one
    if frac_two is not None:
        payload["path_fraction_reverse"] = frac_two
    if volume_new is not None:
        payload["volume_cm3"] = round(volume_new, 6)
    if delta_total is not None:
        payload["volume_delta_cm3"] = round(delta_total, 6)

    if feature:
        is_hollow, thickness_cm, herr = _hollow_verdict(feature, want_hollow, wall_cm)
        if herr:
            return error(herr + " " + _common.failed_effect_remedy(design, feature))
        payload["feature"] = safe(lambda: feature.name)
        payload["hollow"] = bool(is_hollow)
        # A solid pipe's feature reads a 0.0 wall (measured), which is not a wall - publish the key
        # only when the pipe is actually hollow, so nobody reads a zero as a measurement.
        if is_hollow:
            payload["wall_thickness"] = round(thickness_cm * inv, 6)
        payload["section_size_measured"] = _common.measured(lambda: feature.sectionSize.value, inv)
        # No cap read-back is published. MEASURED on a freshly added hollow solid pipe:
        # startFaces, endFaces AND sideFaces all read as an EMPTY BRepFaces collection (count 0) -
        # never None, and never populated - so on this Fusion build those collections cannot tell a
        # capped end from an uncapped one, and any capped/uncapped number derived from them would be
        # a fabricated reading. Section the body with view_section to see the ends.
    else:
        # Direct mode: path/distanceOne/distanceTwo/sectionThickness all read null off a
        # non-parametric feature, so the hollow wall genuinely cannot be verified here.
        payload["no_timeline_feature"] = True
        payload["hollow_requested"] = want_hollow
        payload["note"] += " " + _common.DIRECT_FEATURE_NOTE
        if want_hollow:
            payload["note"] += (" The wall is UNVERIFIED in this mode - section the body with "
                                "view_section to confirm it.")
    return ok(payload)


TOOL_DESCRIPTION = (
"Build a pipe/tube along a path in one feature: a circular, square, or triangular section, solid or "
"HOLLOW with a wall thickness - model_sweep is the drawn-profile version. 'operation': new | join | "
"cut | intersect; cut/intersect act on existing bodies. The two path_fraction inputs are FRACTIONS "
"of the path, not lengths. A parametric result reports the hollow verdict and the wall read back off "
"the created pipe; a DIRECT-mode design has no feature to read them from, and the result says so.\n\n"
+ _outputs.produces_block(RETURNS)
)

pipe_tool = (
    Tool.create_simple(name="model_pipe", description=TOOL_DESCRIPTION)
    .add_input_property("path", {"type": ["string", "array"], "items": {"type": "string"},
            "description": "The curve to follow: a find_geometry edge 'handle' (a single handle chains across TANGENT connections; a sharp corner stops the chain - the 'path' count is the truth), a JSON list of edge handles (used exactly - they must connect into one path), OR 'sketch:<name>' for a path sketch."})
    .add_input_property(*_SECTION_SIZE.as_property())
    .add_input_property(*_SECTION_TYPE.as_property())
    .add_input_property(*_OPERATION.as_property())
    .add_input_property("hollow", {"type": "boolean",
            "description": "Make the pipe a hollow tube (default false). Implied by 'wall_thickness'."})
    .add_input_property(*_WALL_THICKNESS.as_property())
    .add_input_property("path_fraction", {"type": "number",
            "description": "Fraction of the path covered from its start, over 0 and at most 1 (default: all of it). A ratio - 'units' does not apply."})
    .add_input_property("path_fraction_reverse", {"type": "number",
            "description": "Fraction covered the OTHER way round a CLOSED path; needs 'path_fraction' and at most 1 minus it."})
    .add_input_property("target_bodies", _TARGET_BODIES.schema())
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
pipe_item = Item.create_tool_item(tool=pipe_tool, write="write", handler=handler,
                                  run_on_main_thread=True,
                                  postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(pipe_item)
