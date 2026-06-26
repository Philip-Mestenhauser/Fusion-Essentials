# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create a Joint between two joint inputs.

  joint -> create a Joint (timeline feature) between two inputs, with a chosen motion type
           (rigid / revolute / slider / cylindrical / planar / ball / pin-slot) and optional
           offset / angle / flip. WRITES to the design.

This is the API equivalent of the Joint command. A joint is defined by TWO inputs and a motion
type — the tool just builds the feature; it does not assume what the inputs represent or why you
are joining them. Fusion's Joints.createInput accepts a JointGeometry OR a JointOrigin for each
side; this block resolves each input by JOINT-ORIGIN NAME (the common, unambiguous case). Other
input kinds (a face/edge → JointGeometry) can be added to the same resolver later without
changing the tool's shape.

Grounded in adsk.core / adsk.fusion:
  - Component.joints (Joints).createInput(inputOne, inputTwo) -> JointInput  (each input is a
    JointGeometry or JointOrigin); JointInput.setAs<Type>JointMotion(...); .offset/.angle/.isFlipped
  - Joints.add(jointInput) -> Joint
  - JointDirections (X=0,Y=1,Z=2,Custom=3) for motion axes; ValueInput.createByReal(cm/rad)
  - Design.rootComponent.allJoints / jointOrigins.itemByName for resolution/reporting
Handler runs on the main thread; WRITES to the design.
"""

import json
import math

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
_AXES = {"x": 0, "y": 1, "z": 2}  # JointDirections (Custom=3 not exposed here)

# joint_type -> (label, needs_axis). The setter is dispatched in _apply_motion.
_JOINT_TYPES = {
    "rigid": ("rigid", False),
    "revolute": ("revolute", True),
    "slider": ("slider", True),
    "cylindrical": ("cylindrical", True),
    "planar": ("planar", True),
    "ball": ("ball", False),
}


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        try:
            design = adsk.fusion.Design.cast(
                app.activeDocument.products.itemByProductType('DesignProductType'))
        except Exception:
            design = None
    return design


def _find_joint_origin(design, name):
    """Resolve a joint input by joint-origin name. Returns a JointOrigin usable as a joint input.

    A joint origin that lives inside a child/referenced OCCURRENCE must be supplied to the joint
    as its ASSEMBLY-CONTEXT PROXY (jo.createForAssemblyContext(occurrence)), NOT the native JO —
    the native one yields "Provided input paths for joint are not valid". So: a JO on the root
    component is returned as-is; a JO that belongs to a sub-component is resolved through the
    occurrence that brings it into the assembly, as a proxy.
    """
    name = (name or "").strip()
    if not name:
        return None
    root = design.rootComponent

    # On the root component -> native JO is fine (it is already in assembly context).
    jo = _safe(lambda: root.jointOrigins.itemByName(name))
    if jo:
        return jo

    # Otherwise find the native JO's owning component, then the occurrence that instances it,
    # and return the JO's proxy in that occurrence's context.
    native = None
    for c in _safe(lambda: design.allComponents, []) or []:
        cand = _safe(lambda c=c: c.jointOrigins.itemByName(name))
        if cand:
            native = cand
            break
    if not native:
        return None
    owner_name = _safe(lambda: native.parentComponent.name)
    if not owner_name:
        return native
    # Find an occurrence of the owning component and proxy the JO into it. NOTE: match by NAME,
    # not `is` — the Fusion API returns fresh wrapper objects for the same component, so identity
    # comparison (occ.component is owner) is unreliable and silently fails.
    try:
        for occ in root.allOccurrences:
            if (_safe(lambda occ=occ: occ.component.name) or "") == owner_name:
                proxy = _safe(lambda occ=occ: native.createForAssemblyContext(occ))
                if proxy:
                    return proxy
    except Exception:
        pass
    return native  # last resort (will likely error on add, but better than nothing)


def _apply_motion(ji, jtype, axis_idx):
    """Set the joint motion on the JointInput. Returns (ok, error_or_None)."""
    JD = adsk.fusion.JointDirections
    ax = [JD.XAxisJointDirection, JD.YAxisJointDirection, JD.ZAxisJointDirection][axis_idx]
    try:
        if jtype == "rigid":
            return bool(ji.setAsRigidJointMotion()), None
        if jtype == "revolute":
            return bool(ji.setAsRevoluteJointMotion(ax)), None
        if jtype == "slider":
            return bool(ji.setAsSliderJointMotion(ax)), None
        if jtype == "cylindrical":
            return bool(ji.setAsCylindricalJointMotion(ax)), None
        if jtype == "planar":
            return bool(ji.setAsPlanarJointMotion(ax)), None
        if jtype == "ball":
            # Ball needs a pitch + yaw direction; use the two axes other than nothing — default X pitch, Y yaw.
            return bool(ji.setAsBallJointMotion(JD.XAxisJointDirection, JD.YAxisJointDirection)), None
    except Exception as e:
        return False, str(e)
    return False, f"unsupported joint_type '{jtype}'"


def handler(occurrence_one: str = "", occurrence_two: str = "", joint_type: str = "rigid",
            axis: str = "z", offset: float = 0.0, angle: float = 0.0, units: str = "mm",
            flip: bool = False, name: str = "") -> dict:
    """Create a joint between two joint inputs (resolved by joint-origin name).

    occurrence_one / occurrence_two: the two joint inputs — names of Joint Origins to join.
    joint_type: rigid (default) | revolute | slider | cylindrical | planar | ball. axis (x/y/z):
    the motion axis for types that need one. offset (in 'units') and angle (degrees) position the
    joint; flip reverses it. WRITES to the design.
    """
    design = _design()
    if not design:
        return _error("No active design (open a document with assembly geometry).")

    jtype = (joint_type or "rigid").strip().lower()
    if jtype not in _JOINT_TYPES:
        return _error(f"Unknown joint_type '{joint_type}'. Valid: {', '.join(_JOINT_TYPES)}.")

    ax_name = (axis or "z").strip().lower()
    if ax_name not in _AXES:
        return _error(f"Unknown axis '{axis}'. Valid: x, y, z.")

    scale = _UNIT_TO_CM.get((units or "mm").strip().lower())
    if scale is None:
        return _error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    n1, n2 = (occurrence_one or "").strip(), (occurrence_two or "").strip()
    if not n1 or not n2:
        return _error("Provide 'occurrence_one' and 'occurrence_two' — the names of the two Joint "
                      "Origins to join.")

    jo1 = _find_joint_origin(design, n1)
    jo2 = _find_joint_origin(design, n2)
    if not jo1:
        return _error(f"No Joint Origin named '{n1}'. Create one with create_joint_origin, or "
                      "check the name.")
    if not jo2:
        return _error(f"No Joint Origin named '{n2}'. Create one with create_joint_origin, or "
                      "check the name.")

    # Joints live on the root component (a joint between two components is owned there).
    joints = design.rootComponent.joints
    try:
        ji = joints.createInput(jo1, jo2)
    except Exception as e:
        return _error(f"Could not create joint input: {e}")
    if not ji:
        return _error("createInput returned nothing for these inputs.")

    ok, err = _apply_motion(ji, jtype, _AXES[ax_name])
    if not ok:
        return _error(f"Could not set {jtype} motion: {err or 'setter returned false'}.")

    # Optional offset / angle / flip.
    try:
        if offset:
            ji.offset = adsk.core.ValueInput.createByReal(offset * scale)
        if angle:
            ji.angle = adsk.core.ValueInput.createByReal(math.radians(angle))
        if flip:
            ji.isFlipped = True
    except Exception as e:
        return _error(f"Could not apply offset/angle/flip: {e}")

    try:
        joint = joints.add(ji)
    except Exception as e:
        return _error(f"Joint creation failed: {e}")
    if not joint:
        return _error("joints.add returned nothing.")

    new_name = (name or "").strip()
    if new_name:
        try:
            joint.name = new_name
        except Exception:
            pass

    return _ok({
        "created": True,
        "joint_name": _safe(lambda: joint.name),
        "joint_type": jtype,
        "input_one": n1,
        "input_two": n2,
        "axis": (ax_name if _JOINT_TYPES[jtype][1] else None),
        "offset": offset if offset else None,
        "angle_deg": angle if angle else None,
        "flipped": bool(flip),
        "note": "Joint created as a timeline feature. View it with get_screenshot.",
    })


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


TOOL_DESCRIPTION = (
    "Create a Joint (timeline feature) between two joint inputs — the API equivalent of the Joint "
    "command. 'occurrence_one' and 'occurrence_two' are the names of the two Joint Origins to "
    "join (create them with create_joint_origin). 'joint_type' is rigid (default) | revolute | "
    "slider | cylindrical | planar | ball; for the types that need a motion axis, 'axis' is x/y/z. "
    "Optional 'offset' (in 'units' = mm/cm/in) and 'angle' (degrees) position the joint, and "
    "'flip' reverses it. WRITES to the design. The tool just builds the joint feature — it does "
    "not assume what the inputs are or why you join them (assembly mates, fixturing, positioning "
    "an inserted part, etc. are all up to you)."
)

tool = (
    Tool.create_with_string_input(
        name="joint",
        description=TOOL_DESCRIPTION,
        input_param_name="occurrence_one",
        input_param_description="Name of the first Joint Origin to join.",
    )
    .add_input_property("occurrence_two", {"type": "string",
                                           "description": "Name of the second Joint Origin to join."})
    .add_input_property("joint_type", {"type": "string",
                                       "description": "rigid (default) | revolute | slider | cylindrical | planar | ball."})
    .add_input_property("axis", {"type": "string",
                                 "description": "Motion axis for types that need one: x | y | z (default z)."})
    .add_input_property("offset", {"type": "number", "description": "Offset distance (in 'units'; default 0)."})
    .add_input_property("angle", {"type": "number", "description": "Angle in degrees (default 0)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .add_input_property("flip", {"type": "boolean", "description": "Reverse the joint direction (default false)."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the joint."})
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
