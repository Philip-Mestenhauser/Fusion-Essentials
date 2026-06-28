# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create a joint at two GEOMETRY HANDLES (the consume half of geometry-as-values).

  joint_at_geometry -> joint two parts AT specific geometry — a crank pin's cylindrical face to a
                       rod's bore, a hole edge to a pin — given the two HANDLES that find_geometry
                       returned. Motion: rigid / revolute / slider / cylindrical / ball, with an
                       axis. The joint lands AT the real geometry (the offset pin, the bore center),
                       NOT collapsed to the part origins. WRITES.

WHY THIS EXISTS — and what it bakes in (the design point): jointing at a precise offset point used
to require tribal knowledge an agent only learned by crashing:
  * the '<occ>:cylinder' snap is ambiguous on a multi-cylinder part (picks the wrong face / fails);
  * '<occ>:origin' is reliable but COLLAPSES both parts to (0,0,0) — zero offset, a degenerate
    mechanism that won't move;
  * a construction-point datum is REJECTED in assembly/edit-in-place context ("Environment is not
    supported");
  * JointGeometry.createByNonPlanarFace works on a cylinder face, BUT JointKeyPointTypes.CenterKeyPoint
    is INVALID on a cylinder/cone face ("Key point type should not be CenterKeyPoint ...") — you must
    use a Middle/Start keypoint instead.
This tool encapsulates the proven path and ALL of those runtime rules: it resolves each handle,
proxies it into its occurrence, builds the right JointGeometry for the entity kind, and picks a VALID
keypoint by face type — so the caller passes two handles + a motion and gets a joint at the real
geometry, with the gotchas handled internally. The runtime rule lives in the tool, not in the agent.

Grounded in adsk.fusion (paths confirmed live):
  - Design.findEntityByToken(handle) -> [entity] ; entity.assemblyContext = its occurrence
  - JointGeometry.createByNonPlanarFace(cylFace, JointKeyPointTypes.MiddleKeyPoint)  [cyl/cone]
    JointGeometry.createByPlanarFace(face, edge?, CenterKeyPoint)                     [planar]
    JointGeometry.createByCurve(edge, keypoint) / createByPoint(vertex|point)
  - Joints.createInput(g1, g2) ; input.setAs<Motion>JointMotion(JointDirections.<X/Y/Z>) ; Joints.add
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()

_MOTIONS = {"rigid", "revolute", "slider", "cylindrical", "ball"}
_AXIS_DIR = {
    "x": "XAxisJointDirection",
    "y": "YAxisJointDirection",
    "z": "ZAxisJointDirection",
}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _resolve_handle(design, handle):
    """entityToken handle -> the entity (or None). Returns the first match."""
    h = (handle or "").strip()
    if not h:
        return None
    found = _safe(lambda: design.findEntityByToken(h))
    if found and len(found):
        return found[0]
    return None


def _joint_geometry_for(entity):
    """Build a JointGeometry for an entity, picking a VALID keypoint for its kind. Returns
    (geometry, label, error). This is where the runtime rules are encoded."""
    KP = adsk.fusion.JointKeyPointTypes
    JG = adsk.fusion.JointGeometry
    # BRepFace
    if isinstance(entity, adsk.fusion.BRepFace):
        st = _safe(lambda: entity.geometry.surfaceType)
        if st == adsk.core.SurfaceTypes.PlaneSurfaceType:
            g = _safe(lambda: JG.createByPlanarFace(entity, None, KP.CenterKeyPoint))
            return g, "planar_face@center", None if g else "createByPlanarFace failed"
        if st in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
            # RULE: CenterKeyPoint is INVALID on a cylinder/cone — use MiddleKeyPoint (axis midpoint).
            g = _safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
            return g, "cylinder_face@middle", None if g else "createByNonPlanarFace failed"
        # other non-planar (sphere/torus): try non-planar with middle keypoint
        g = _safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
        return g, "nonplanar_face@middle", None if g else "unsupported face geometry for a joint"
    # BRepEdge (circular -> center; linear -> midpoint)
    if isinstance(entity, adsk.fusion.BRepEdge):
        ct = _safe(lambda: entity.geometry.curveType)
        kp = KP.CenterKeyPoint if ct == adsk.core.Curve3DTypes.Circle3DCurveType else KP.MiddleKeyPoint
        g = _safe(lambda: JG.createByCurve(entity, kp))
        return g, "edge", None if g else "createByCurve failed for this edge"
    # BRepVertex / construction point
    if isinstance(entity, (adsk.fusion.BRepVertex, adsk.fusion.ConstructionPoint)):
        g = _safe(lambda: JG.createByPoint(entity))
        return g, "point", None if g else "createByPoint failed"
    # SketchPoint
    if isinstance(entity, adsk.fusion.SketchPoint):
        g = _safe(lambda: JG.createByPoint(entity))
        return g, "sketch_point", None if g else "createByPoint failed"
    return None, None, f"entity kind {type(entity).__name__} is not a supported joint geometry"


def _axis_entity(entity):
    """If 'entity' is a cylinder/cone face (or a circular edge), return it as an entity that can
    define the joint's rotation/slide axis (its own axis). Else None. This is the FIX: a pin's
    joint must move about the PIN'S axis, not a world axis the caller guessed — passing a world axis
    that doesn't match the geometry over-constrains the assembly ('Compute Failed')."""
    if isinstance(entity, adsk.fusion.BRepFace):
        st = _safe(lambda: entity.geometry.surfaceType)
        if st in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
            return entity
    if isinstance(entity, adsk.fusion.BRepEdge):
        if _safe(lambda: entity.geometry.curveType) == adsk.core.Curve3DTypes.Circle3DCurveType:
            return entity
    return None


def _apply_motion(ji, motion, axis, axis_ent):
    """Set the motion on the JointInput. If axis_ent is given (a cylinder face/circular edge) and
    axis is 'auto', use CustomJointDirection from that entity's own axis. Returns (ok, error)."""
    JD = adsk.fusion.JointDirections
    a = (axis or "auto").strip().lower()
    use_custom = (a == "auto") and (axis_ent is not None)
    direction = JD.CustomJointDirection if use_custom else getattr(
        JD, _AXIS_DIR.get(a if a in _AXIS_DIR else "z", "ZAxisJointDirection"))
    try:
        if motion == "rigid":
            return ji.setAsRigidJointMotion(), None
        if motion == "revolute":
            if use_custom:
                return ji.setAsRevoluteJointMotion(direction, axis_ent), None
            return ji.setAsRevoluteJointMotion(direction), None
        if motion == "slider":
            if use_custom:
                return ji.setAsSliderJointMotion(direction, axis_ent), None
            return ji.setAsSliderJointMotion(direction), None
        if motion == "cylindrical":
            if use_custom:
                return ji.setAsCylindricalJointMotion(direction, axis_ent), None
            return ji.setAsCylindricalJointMotion(direction), None
        if motion == "ball":
            return ji.setAsBallJointMotion(JD.ZAxisJointDirection, JD.XAxisJointDirection), None
    except Exception as e:
        return False, str(e)
    return False, f"unknown motion '{motion}'"


def handler(handle_one: str = "", handle_two: str = "", motion: str = "revolute",
            axis: str = "auto", name: str = "") -> dict:
    """Joint two parts at two geometry handles (from find_geometry).

    handle_one / handle_two: the entity-token handles to joint AT (e.g. a rod bore face and a crank
    pin face). motion: rigid | revolute | slider | cylindrical | ball. axis: 'auto' (default —
    derive the rotation/slide axis FROM the geometry's own axis, e.g. a cylinder face's axis; this
    is what you want for a pin so it moves about the PIN, not a world axis) or x | y | z to force a
    world axis. name: optional joint name. The joint lands at the real geometry; keypoint/proxy/axis
    rules are handled internally. WRITES.
    """
    mot = (motion or "revolute").strip().lower()
    if mot not in _MOTIONS:
        return _error(f"Unknown motion '{motion}'. Use: {', '.join(sorted(_MOTIONS))}.")

    design = _design()
    if not design:
        return _error("No active design.")

    e1 = _resolve_handle(design, handle_one)
    if not e1:
        return _error(f"handle_one did not resolve to an entity. Pass a 'handle' from find_geometry "
                      "(an entity token). It may be stale if the geometry was rebuilt.")
    e2 = _resolve_handle(design, handle_two)
    if not e2:
        return _error("handle_two did not resolve to an entity. Pass a 'handle' from find_geometry.")

    g1, l1, err1 = _joint_geometry_for(e1)
    if err1:
        return _error(f"handle_one: {err1}")
    g2, l2, err2 = _joint_geometry_for(e2)
    if err2:
        return _error(f"handle_two: {err2}")

    root = design.rootComponent
    try:
        ji = root.joints.createInput(g1, g2)
    except Exception as e:
        return _error(f"Could not create joint input from the two geometries: {e}")

    # axis='auto' (default): derive the motion axis from the geometry itself (a cylinder face / round
    # edge), so a pin rotates about the PIN's axis — not a guessed world axis that would over-constrain
    # the assembly. Prefer whichever input carries a usable axis.
    axis_ent = _axis_entity(e1) or _axis_entity(e2)
    ok, merr = _apply_motion(ji, mot, axis, axis_ent)
    if merr or not ok:
        return _error(f"Could not set {mot} motion: {merr or 'rejected'}. "
                      "(For a world axis pass axis=x/y/z; 'auto' needs a cylinder face / round edge "
                      "to derive the axis from.)")

    try:
        joint = root.joints.add(ji)
    except Exception as e:
        return _error(f"Joint creation failed: {e}. (The two geometries may be incompatible, or one "
                      "part may be over-constrained.)")
    if not joint:
        return _error("Joint creation returned nothing.")

    nm = (name or "").strip()
    if nm:
        _safe(lambda: setattr(joint, "name", nm))

    # report the joint's resulting occurrences so the caller can verify the wiring
    o1 = _safe(lambda: joint.occurrenceOne.name)
    o2 = _safe(lambda: joint.occurrenceTwo.name)
    # CHECK HEALTH at the source: a joint can be ADDED yet fail to COMPUTE (over-constrained) — the
    # 'Compute Failed' the user sees first. Surface it here so the caller doesn't trust a broken joint.
    hs = _safe(lambda: joint.healthState)
    healthy = (hs is None) or (hs == 0)
    out = {
        "jointed": True,
        "joint_name": _safe(lambda: joint.name),
        "motion": mot,
        "axis": ("auto(geometry)" if (axis or "auto").strip().lower() == "auto" and axis_ent
                 else (axis or "auto").strip().lower()) if mot != "rigid" else None,
        "healthy": healthy,
        "geometry_one": l1,
        "geometry_two": l2,
        "occurrence_one": o1,
        "occurrence_two": o2,
        "note": "Joint created AT the geometry. axis='auto' derived the motion axis from the "
                "geometry itself. Verify with assembly_probe (is_healthy + positions).",
    }
    if not healthy:
        msg = (_safe(lambda: joint.errorOrWarningMessage) or "").split("Compute Failed")[0].strip()
        out["health_warning"] = ("This joint FAILED TO COMPUTE (likely over-constrained): "
                                 + (msg[:200] or "conflicts with assembly relationships"))
    return _ok(out)


TOOL_DESCRIPTION = (
    "Joint two parts AT specific geometry (the offset pin / bore center), NOT collapsed to the part "
    "origins the way an ':origin' snap does. The consume half of geometry-as-values: it takes two "
    "'handle's and bakes in the runtime rules (proxy-into-occurrence, valid keypoint per face type, "
    "axis-from-geometry, post-create health check) so you don't rediscover them by trial and error.\n"
    "\n"
    "CONTRACT (resolve these BEFORE calling, so you pre-provide correct inputs):\n"
    "• HANDLE ORDER MATTERS (the #1 live failure): the tool MOVES handle_one's occurrence to mate "
    "with handle_two. So handle_one MUST be the part that is FREE to move and handle_two the part "
    "that stays put — typically handle_one = the moving part's geometry, handle_two = the geometry "
    "on the grounded/fixed part. Passing a GROUNDED part as handle_one fails: 'First component to "
    "move is grounded, it can not move'. Check ground flags with assembly_probe first.\n"
    "• CONSUMES: handle_one + handle_two = entity-token 'handle's from find_geometry (NOT names, NOT "
    "snap-strings). Query the exact mating geometry (e.g. find_geometry the pin's cylinder_face by "
    "radius+nearest_to, and the rod's bore face). Handles go STALE if geometry is rebuilt — re-find "
    "after any model edit (grounding/recompute does NOT rebuild geometry, so handles survive that).\n"
    "• OPEN QUESTIONS to settle first: (1) which part is the MOVER (→ handle_one) vs the fixed one "
    "(→ handle_two)? (2) which two faces/edges are the real mating pair? (3) motion type — revolute "
    "(pin) / slider (piston-in-bore) / cylindrical / ball / rigid?\n"
    "• FAILS / DEGRADES IF: handle_one is a grounded part (see above); a handle is stale or names a "
    "non-jointable entity; the two geometries are incompatible; OR the joint can't solve in the "
    "current part poses → it is ADDED but returns healthy=false + health_warning. A joint that "
    "'created' is NOT necessarily working — ALWAYS verify is_healthy via assembly_probe afterward.\n"
    "• axis: leave 'auto' (derives from the geometry's own axis — correct default). For two coaxial "
    "cylinder faces the axis is fixed by the geometry regardless, so this rarely matters; pass x/y/z "
    "only to force a world axis on geometry that doesn't define one.\n"
    "• PRODUCES: the joint name, the two occurrences wired, the resolved geometry/keypoint, and a "
    "'healthy' flag. WRITES."
)

joint_at_tool = (
    Tool.create_simple(name="joint_at_geometry", description=TOOL_DESCRIPTION)
    .add_input_property("handle_one", {"type": "string", "description": "Entity-token handle (from find_geometry) for the first geometry."})
    .add_input_property("handle_two", {"type": "string", "description": "Entity-token handle (from find_geometry) for the second geometry."})
    .add_input_property("motion", {"type": "string", "description": "rigid | revolute | slider | cylindrical | ball (default revolute)."})
    .add_input_property("axis", {"type": "string", "description": "auto (default — derive axis from the geometry, e.g. a cylinder face's axis) | x | y | z (force a world axis)."})
    .add_input_property("name", {"type": "string", "description": "Optional joint name."})
    .strict_schema()
)
joint_at_item = Item.create_tool_item(tool=joint_at_tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(joint_at_item)
