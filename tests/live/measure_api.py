# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Live API measurement: every API fact the unit-test fakes need, measured from LIVE Fusion.

The unit suite proves tool logic against fakes. This tool is where the fakes' API facts COME
FROM: each measurement row checks a claim against a running Fusion, and a fully-PASSING run
generates ``tests/live_api_facts.py`` - enum values, behavior flags, and the version stamp -
which conftest imports to populate the mock adsk modules and the shared fakes. The mocks are fed
by measurement, not by hand; VERIFIED_API_FACTS.md is the human-readable ledger of the same run.

Run:  py -3 tests/live/measure_api.py          (Fusion running + add-in enabled +
                                                allow_execute_api_script on)
      py -3 tests/live/measure_api.py --check  (no measuring: exit 1 when the stamp does not
                                                match the installed Fusion or any row is
                                                not PASS)
      py -3 tests/live/measure_api.py --json   (also archive results to tests/live/results/)

ROWS are DATA: id, claim, encoded_in (the fake carrying the claim), a script body, and an
expectation. A row's script may print ``FACT <dotted.key> <json>`` lines - measured values the
generator folds into live_api_facts.py; a row whose misuse kills its own script instead declares
``facts_on_pass`` and the runner records those when the row passes. Every row runs as its OWN
script because a raise can escape try/except entirely and kill the whole Python.Run invocation,
eating its printed output (out-of-range item() does exactly that, observed live) - so one row's
abort can never swallow another row's result. expect="raise_or_abort" rows assert a misuse that
never returns a value: a caught raise prints PASS, and a script-level error ALSO confirms the
claim. Rows with needs="cam" run LAST: on the first one, the runner stands up a CAM world (a box,
MeasureSetup, two face ops, MeasureFolder holding the second op) through the server's own tools;
if that build fails, every cam row reports ERROR with the failing step instead of crashing the
run. Extend coverage by adding rows, not code.
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tool_verify import call, health_gate, registered_tools  # noqa: E402  shared HTTP plumbing

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERIFIED_API_FACTS.md")
FACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "live_api_facts.py")
TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "commands", "mcpServer", "tools")

# Every adsk enum FAMILY the tools reference (…Types/…States/…Modes/…Directions), scraped from the
# tool sources so the sweep tracks the codebase - a new enum a tool starts using is measured
# automatically, no row edit. The value-pinning rows below still assert the specific members
# production branches on; this sweep is additive coverage for every family, pinned or not.
# NOT swept: …Options families - those are factory-OBJECT classes (…Options.create()), not int
# enums, so they carry no members to dump. A family is only counted from a REAL reference, never a
# comment (a stale name in a comment must not drive a live measurement).
_ENUM_FAMILY_RE = re.compile(r"adsk\.(core|fusion|cam)\.([A-Za-z]*(?:Types|States|Modes|Directions))\b")


def referenced_enum_families():
    fams = set()
    for fn in os.listdir(TOOLS_DIR):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(TOOLS_DIR, fn), encoding="utf-8") as fh:
            for line in fh:
                code = line.split("#", 1)[0]   # strip comments - a name in prose is not a reference
                for ns, cls in _ENUM_FAMILY_RE.findall(code):
                    fams.add(ns + "." + cls)
    return sorted(fams)


def _all_enums_body():
    """Script body for the enum-sweep row: dump every referenced family, then PASS iff each
    resolved to at least one int member (a family that dumps nothing is a stale/renamed reference)."""
    fams = referenced_enum_families()
    lines = ["    fams = ["]
    for f in fams:
        # Resolve each family through getattr chains so a scraped name that is NOT a live class
        # (a typo, or a non-enum) yields None here instead of aborting the whole script.
        ns, cls = f.split(".", 1)
        lines.append('        ("{0}", getattr(getattr(adsk, "{1}", None), "{2}", None)),'.format(f, ns, cls))
    lines.append("    ]")
    lines.append("    empty = []")
    lines.append("    for label, cls in fams:")
    lines.append("        n = 0")
    lines.append("        for name in (dir(cls) if cls is not None else []):")
    lines.append("            v = getattr(cls, name)")
    lines.append("            if not name.startswith('_') and isinstance(v, int):")
    lines.append("                print('FACT enums.' + label + '.' + name + ' ' + str(v))")
    lines.append("                n += 1")
    lines.append("        if n == 0:")
    lines.append("            empty.append(label)")
    lines.append("    emit(not empty, 'enum-sweep: ' + str(len(fams) - len(empty)) + '/'"
                 " + str(len(fams)) + ' families dumped'"
                 " + (' EMPTY: ' + ','.join(empty) if empty else ''))")
    return "\n".join(lines) + "\n"

# Each row script is self-contained: emit() prints one verdict line per check, and make_box()
# builds a 10 mm cube (1.0 in Fusion's internal cm) for rows that need real geometry. Rows that set
# need_box get it bound to `body` before their own lines run.
_TEMPLATE = '''import adsk.core, adsk.fusion, adsk.cam

def emit(ok, detail):
    print(("PASS " if ok else "FAIL ") + detail)

def dump_enum(label, cls):
    for n in dir(cls):
        v = getattr(cls, n)
        if not n.startswith("_") and isinstance(v, int):
            print("FACT enums." + label + "." + n + " " + str(v))

def dump_shape(label, obj):
    names = sorted(n for n in dir(obj) if not n.startswith("_"))
    for i in range(0, len(names), 20):
        print("SHAPE " + label + " " + " ".join(names[i:i + 20]))
    return len(names)

def dump_shape(label, obj):
    names = sorted(n for n in dir(obj) if not n.startswith("_"))
    for i in range(0, len(names), 20):
        print("SHAPE " + label + " " + " ".join(names[i:i + 20]))
    return len(names)

def make_box(des, name):
    root = des.rootComponent
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(1.0, 1.0, 0.0))
    prof = sk.profiles.item(0)
    ext = root.features.extrudeFeatures.addSimple(
        prof, adsk.core.ValueInput.createByReal(1.0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    b = ext.bodies.item(0)
    b.name = name
    return b

def run(context):
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
{box_line}{body}'''


ROWS = [
    {
        "id": "units-cm",
        "claim": "Lengths cross the API in cm - a 10 mm sketch square extruded 1.0 unit has bbox extent 1.0",
        "encoded_in": "tests/conftest.py bbox fixture; every test asserting a scale() factor",
        "need_box": True,
        "facts_on_pass": {"behavior.internal_length_unit_is_cm": True},
        "body": """
    dx = body.boundingBox.maxPoint.x - body.boundingBox.minPoint.x
    emit(abs(dx - 1.0) < 1e-6, "units-cm: bbox dx=" + str(dx) + " (expect 1.0)")
""",
    },
    {
        "id": "point3d-vectorto",
        "claim": "Point3D.vectorTo(other) == other - self",
        "encoded_in": "tests/conftest.py FakePoint.vectorTo",
        "body": """
    a = adsk.core.Point3D.create(1.0, 2.0, 3.0)
    b = adsk.core.Point3D.create(4.0, 6.0, 8.0)
    v = a.vectorTo(b)
    emit(v.x == 3.0 and v.y == 4.0 and v.z == 5.0,
         "point3d-vectorto: (" + str(v.x) + "," + str(v.y) + "," + str(v.z) + ") expect (3,4,5)")
""",
    },
    {
        "id": "vector3d-normalize-zero",
        "claim": "Vector3D.normalize() returns True even for a (near-)zero vector and leaves the components untouched - the return value is not a zero guard",
        "encoded_in": "tests/conftest.py FakeVector3D.normalize; commands/mcpServer/tools/sys_selection.py _unit",
        "body": """
    z = adsk.core.Vector3D.create(0.0, 0.0, 0.0)
    rz = z.normalize()
    print("FACT behavior.vector3d_normalize_true_on_zero " + ("true" if rz else "false"))
    untouched = (z.x == 0.0 and z.y == 0.0 and z.z == 0.0)
    t = adsk.core.Vector3D.create(1e-15, 0.0, 0.0)
    rt = t.normalize()
    emit(bool(rz) and untouched and bool(rt),
         "vector3d-normalize-zero: zero->" + repr(rz) + " untouched=" + repr(untouched)
         + " tiny->" + repr(rt))
""",
    },
    {
        "id": "design-cast",
        "claim": "Design.cast passes the active design through; a non-design casts to None",
        "encoded_in": "tests/conftest.py install() cast_design + install_mock_adsk Design.cast",
        "body": """
    d = adsk.fusion.Design.cast(app.activeProduct)
    n = adsk.fusion.Design.cast(adsk.core.Point3D.create(0.0, 0.0, 0.0))
    emit(d is not None and n is None,
         "design-cast: design->" + type(d).__name__ + " point->" + repr(n))
""",
    },
    {
        "id": "cam-operation-cast",
        "claim": "adsk.cam.Operation.cast(non-operation) returns None (tools filter on it)",
        "encoded_in": "tests/conftest.py install_mock_adsk cam.Operation.cast",
        "body": """
    n = adsk.cam.Operation.cast(adsk.core.Point3D.create(0.0, 0.0, 0.0))
    emit(n is None, "cam-operation-cast: non-op casts to " + repr(n))
""",
    },
    {
        "id": "find-entity-token-shape",
        "claim": "findEntityByToken returns a SWIG BaseVector - list-like (len/index/iterate) but NOT a Python list",
        "encoded_in": "tests/conftest.py MakeDesign.findEntityByToken",
        "need_box": True,
        "body": """
    hit = des.findEntityByToken(body.entityToken)
    tname = type(hit).__name__
    n = len(hit)
    first = type(hit[0]).__name__
    cnt = 0
    for x in hit:
        cnt += 1
    emit(tname != "list" and n == 1 and first == "BRepBody" and cnt == 1,
         "find-entity-token-shape: type=" + tname + " len=" + str(n) + " [0]=" + first
         + " iterated=" + str(cnt))
""",
    },
    {
        "id": "find-entity-token-miss",
        "claim": "A stale token, a garbage string, a plain name, and a truncated token each return an EMPTY falsy vector (len 0) - never a raise",
        "encoded_in": "tests/conftest.py MakeDesign.findEntityByToken; commands/mcpServer/tools/_inputs.py _resolve_token_entity fallthrough",
        "need_box": True,
        "facts_on_pass": {"behavior.find_entity_token_empty_on_miss": True},
        "body": """
    root = des.rootComponent
    sk = root.sketches.add(root.xYConstructionPlane)
    stale_tok = sk.entityToken
    sk.deleteMe()
    ok = True
    parts = []
    for label, s in (("stale", stale_tok), ("garbage", "bogus-token"),
                     ("name", body.name), ("truncated", body.entityToken[:30])):
        r = des.findEntityByToken(s)
        good = (len(r) == 0 and not bool(r))
        ok = ok and good
        parts.append(label + "=" + ("empty" if good else "NON-EMPTY len " + str(len(r))))
    emit(ok, "find-entity-token-miss: " + ", ".join(parts))
""",
    },
    {
        "id": "allcomponents-design-only",
        "claim": "allComponents lives on Design (Component has none) and is counted AND iterable",
        "encoded_in": "tests/conftest.py MakeDesign.allComponents",
        "facts_on_pass": {"behavior.allcomponents_on_design_only": True},
        "body": """
    comp_has = hasattr(des.rootComponent, "allComponents")
    ac = des.allComponents
    n = ac.count
    cnt = 0
    for c in ac:
        cnt += 1
    emit((not comp_has) and n == cnt and n >= 1,
         "allcomponents-design-only: on Component=" + str(comp_has) + " count=" + str(n)
         + " iterated=" + str(cnt))
""",
    },
    {
        "id": "brepbodies-protocol",
        "claim": "BRepBodies supports count / item(i) / itemByName (None on a miss) / iteration",
        "encoded_in": "tests/conftest.py _NamedCollection",
        "need_box": True,
        "facts_on_pass": {"behavior.item_by_name_none_on_miss": True},
        "body": """
    bb = des.rootComponent.bRepBodies
    hit = bb.itemByName(body.name)
    miss = bb.itemByName("NoSuchBody")
    names = [x.name for x in bb]
    emit(bb.count >= 1 and hit is not None and miss is None and body.name in names
         and bb.item(0) is not None,
         "brepbodies-protocol: count=" + str(bb.count) + " hit=" + str(hit is not None)
         + " miss=" + repr(miss) + " iterated=" + str(len(names)))
""",
    },
    {
        "id": "item-oor-brepbodies",
        "claim": "BRepBodies.item(out-of-range) never returns None - it raises RuntimeError, and the raise can escape try/except and abort the script (catchability varies by session)",
        "encoded_in": "tests/conftest.py _NamedCollection.item",
        "need_box": True,
        "expect": "raise_or_abort",
        "facts_on_pass": {"behavior.collection_item_out_of_range_raises": True},
        "body": """
    try:
        r = des.rootComponent.bRepBodies.item(9999)
        emit(False, "item-oor-brepbodies: returned " + repr(r) + " with no raise")
    except Exception as e:
        emit(type(e).__name__ == "RuntimeError",
             "item-oor-brepbodies: raised catchably " + type(e).__name__ + ": " + str(e)[:60])
""",
    },
    {
        "id": "item-oor-sketches",
        "claim": "Sketches.item(out-of-range) never returns None - it raises, and the raise can escape try/except and abort the script (catchability varies by session)",
        "encoded_in": "tests/conftest.py _NamedCollection.item",
        "expect": "raise_or_abort",
        "facts_on_pass": {"behavior.collection_item_out_of_range_raises": True},
        "body": """
    try:
        r = des.rootComponent.sketches.item(9999)
        emit(False, "item-oor-sketches: returned " + repr(r) + " with no raise")
    except Exception as e:
        emit(type(e).__name__ == "RuntimeError",
             "item-oor-sketches: raised catchably " + type(e).__name__)
""",
    },
    {
        "id": "objectcollection-protocol",
        "claim": "ObjectCollection.create() yields add / count / item(i) / iteration",
        "encoded_in": "tests/conftest.py _FakeObjectCollection",
        "need_box": True,
        "body": """
    oc = adsk.core.ObjectCollection.create()
    oc.add(body)
    cnt = 0
    for x in oc:
        cnt += 1
    emit(oc.count == 1 and oc.item(0) is not None and cnt == 1,
         "objectcollection-protocol: count=" + str(oc.count) + " iterated=" + str(cnt))
""",
    },
    {
        "id": "meshbodies-no-itembyname",
        "claim": "A component's meshBodies collection has count/item but NO itemByName (unlike bRepBodies, which has all three) - a mesh must be resolved by iterate-and-match, never itemByName",
        "encoded_in": "tests/unit/test_mesh_export.py + test_inputs.py (omit it, correct); test_surface_ops.py (wrongly provides it)",
        "facts_on_pass": {"behavior.meshbodies_has_itembyname": False},
        "body": """
    root = adsk.fusion.Design.cast(app.activeProduct).rootComponent
    mb = root.meshBodies
    bb = root.bRepBodies
    emit(hasattr(mb, "count") and hasattr(mb, "item") and not hasattr(mb, "itemByName")
         and hasattr(bb, "itemByName"),
         "meshbodies-no-itembyname: mesh count/item/itemByName="
         + str(hasattr(mb, "count")) + "/" + str(hasattr(mb, "item")) + "/"
         + str(hasattr(mb, "itemByName")) + " brep.itemByName=" + str(hasattr(bb, "itemByName")))
""",
    },
    {
        "id": "enum-cam-operation-states",
        "claim": "OperationStates ints: IsValid=0, IsInvalid=1 (surfaced as out_of_date by the CAM layer), Suppressed=2, NoToolpath=3",
        "encoded_in": "tests/unit/test__cam_common.py state-label map; _cam_common.py operationState reads",
        "body": """
    S = adsk.cam.OperationStates
    dump_enum("cam.OperationStates", S)
    emit(S.IsValidOperationState == 0 and S.IsInvalidOperationState == 1
         and S.SuppressedOperationState == 2 and S.NoToolpathOperationState == 3,
         "enum-cam-operation-states: valid=" + str(S.IsValidOperationState)
         + " invalid=" + str(S.IsInvalidOperationState)
         + " suppressed=" + str(S.SuppressedOperationState)
         + " no_toolpath=" + str(S.NoToolpathOperationState))
""",
    },
    {
        "id": "enum-setup-stock-modes",
        "claim": "SetupStockModes.SolidStock == 6 (the literal the stock-assignment gate keys on)",
        "encoded_in": "tests/unit/test_cam_edit_setup.py; cam_edit_setup.py SetupStockModes.SolidStock",
        "body": """
    dump_enum("cam.SetupStockModes", adsk.cam.SetupStockModes)
    emit(adsk.cam.SetupStockModes.SolidStock == 6,
         "enum-setup-stock-modes: SolidStock=" + str(adsk.cam.SetupStockModes.SolidStock))
""",
    },
    {
        "id": "enum-design-types",
        "claim": "DesignTypes ints: DirectDesignType=0, ParametricDesignType=1",
        "encoded_in": "tests/unit/test_design_mode.py; _inputs.py current_design_type/ModeGuard",
        "body": """
    D = adsk.fusion.DesignTypes
    dump_enum("fusion.DesignTypes", D)
    emit(D.DirectDesignType == 0 and D.ParametricDesignType == 1,
         "enum-design-types: direct=" + str(D.DirectDesignType)
         + " parametric=" + str(D.ParametricDesignType))
""",
    },
    {
        "id": "enum-joint-types",
        "claim": "JointTypes ints: Rigid=0 Revolute=1 Slider=2 Cylindrical=3 PinSlot=4 Planar=5 Ball=6 (Inferred=7 also exists)",
        "encoded_in": "tests/unit/test_assembly_get.py joint-type labels",
        "body": """
    J = adsk.fusion.JointTypes
    dump_enum("fusion.JointTypes", J)
    emit(J.RigidJointType == 0 and J.RevoluteJointType == 1 and J.SliderJointType == 2
         and J.CylindricalJointType == 3 and J.PinSlotJointType == 4
         and J.PlanarJointType == 5 and J.BallJointType == 6,
         "enum-joint-types: rigid=" + str(J.RigidJointType) + " ... ball=" + str(J.BallJointType)
         + " inferred=" + str(J.InferredJointType))
""",
    },
    {
        "id": "enum-joint-motion-types",
        "claim": "JointMotionTypes (the per-DOF motion enum setMotionData wants, DISTINCT from JointTypes) ints: RevoluteJointRotateMotionType=10, SliderJointSlideMotionType=11, CylindricalJointRotateMotionType=3, CylindricalJointSlideMotionType=4 - jointMotion.jointType returns a JointTypes value (Revolute==1), which setMotionData REJECTS as BAD_JOINT_DOF",
        "encoded_in": "tests/unit/test_joint_motion_link.py; _joints.py motion_link_dof map; joint_motion_link.py",
        "body": """
    M = adsk.fusion.JointMotionTypes
    dump_enum("fusion.JointMotionTypes", M)
    emit(M.RevoluteJointRotateMotionType == 10 and M.SliderJointSlideMotionType == 11
         and M.CylindricalJointRotateMotionType == 3 and M.CylindricalJointSlideMotionType == 4,
         "enum-joint-motion-types: revolute_rotate=" + str(M.RevoluteJointRotateMotionType)
         + " slider_slide=" + str(M.SliderJointSlideMotionType)
         + " cyl_rotate=" + str(M.CylindricalJointRotateMotionType)
         + " cyl_slide=" + str(M.CylindricalJointSlideMotionType))
""",
    },
    {
        "id": "enum-joint-directions",
        "claim": "JointDirections ints: XAxis=0, YAxis=1, ZAxis=2, Custom=3",
        "encoded_in": "tests/unit/test_edit_joint.py; _joints.py JointDirections mapping",
        "body": """
    D = adsk.fusion.JointDirections
    dump_enum("fusion.JointDirections", D)
    emit(D.XAxisJointDirection == 0 and D.YAxisJointDirection == 1
         and D.ZAxisJointDirection == 2 and D.CustomJointDirection == 3,
         "enum-joint-directions: x=" + str(D.XAxisJointDirection)
         + " y=" + str(D.YAxisJointDirection) + " z=" + str(D.ZAxisJointDirection)
         + " custom=" + str(D.CustomJointDirection))
""",
    },
    {
        "id": "enum-feature-health-states",
        "claim": "FeatureHealthStates ints: Healthy=0, Warning=1, Error=2, Suppressed=3 (RolledBack=4, Unknown=5 exist and are ignored by the rollups)",
        "encoded_in": "tests/unit/test_assembly_get.py; _common.timeline_health; assembly_get.py health thresholds",
        "body": """
    H = adsk.fusion.FeatureHealthStates
    dump_enum("fusion.FeatureHealthStates", H)
    emit(H.HealthyFeatureHealthState == 0 and H.WarningFeatureHealthState == 1
         and H.ErrorFeatureHealthState == 2 and H.SuppressedFeatureHealthState == 3,
         "enum-feature-health-states: healthy=" + str(H.HealthyFeatureHealthState)
         + " warning=" + str(H.WarningFeatureHealthState)
         + " error=" + str(H.ErrorFeatureHealthState)
         + " suppressed=" + str(H.SuppressedFeatureHealthState))
""",
    },
    {
        "id": "enum-upload-states",
        "claim": "UploadStates ints: UploadProcessing=0, UploadFinished=1, UploadFailed=2",
        "encoded_in": "tests/unit/test_data_get_upload_status.py; data_get_upload_status.py uploadState read",
        "body": """
    U = adsk.core.UploadStates
    dump_enum("core.UploadStates", U)
    emit(U.UploadProcessing == 0 and U.UploadFinished == 1 and U.UploadFailed == 2,
         "enum-upload-states: processing=" + str(U.UploadProcessing)
         + " finished=" + str(U.UploadFinished) + " failed=" + str(U.UploadFailed))
""",
    },
    {
        "id": "enum-sweep",
        "claim": "Every adsk enum family the tools reference resolves to its live integer members - the catch-all that measures all families into live_api_facts.ENUMS, not just the value-pinned few. FAILs if a referenced family dumps no members (a stale/renamed enum reference in a tool)",
        "encoded_in": "commands/mcpServer/tools/*.py enum references; tests/conftest.py seeds ENUMS onto the mocks",
        "body_fn": _all_enums_body,
    },
    {
        "id": "enum-joint-keypoint-types",
        "claim": "JointKeyPointTypes ints: Start=0, Middle=1, End=2, Center=3",
        "encoded_in": "tests/unit/test_joint_at_geometry.py sentinel installer; _joints.py keypoint factory",
        "body": """
    K = adsk.fusion.JointKeyPointTypes
    dump_enum("fusion.JointKeyPointTypes", K)
    emit(K.StartKeyPoint == 0 and K.MiddleKeyPoint == 1 and K.EndKeyPoint == 2
         and K.CenterKeyPoint == 3,
         "enum-joint-keypoint-types: start=" + str(K.StartKeyPoint)
         + " middle=" + str(K.MiddleKeyPoint) + " end=" + str(K.EndKeyPoint)
         + " center=" + str(K.CenterKeyPoint))
""",
    },
    {
        "id": "enum-surface-types",
        "claim": "SurfaceTypes ints: Plane=0, Cylinder=1, Cone=2, Sphere=3, Torus=4 (Nurbs=7 also exists)",
        "encoded_in": "tests/unit/test_joint_at_geometry.py sentinel installer; _joints.py surface branch",
        "body": """
    S = adsk.core.SurfaceTypes
    dump_enum("core.SurfaceTypes", S)
    emit(S.PlaneSurfaceType == 0 and S.CylinderSurfaceType == 1 and S.ConeSurfaceType == 2
         and S.SphereSurfaceType == 3 and S.TorusSurfaceType == 4,
         "enum-surface-types: plane=" + str(S.PlaneSurfaceType)
         + " cylinder=" + str(S.CylinderSurfaceType) + " cone=" + str(S.ConeSurfaceType)
         + " sphere=" + str(S.SphereSurfaceType) + " torus=" + str(S.TorusSurfaceType))
""",
    },
    {
        "id": "enum-curve3d-types",
        "claim": "Curve3DTypes ints: Line=0, Arc=1, Circle=2 (Ellipse=3.. Polyline=7 also exist)",
        "encoded_in": "tests/unit/test_joint_at_geometry.py sentinel installer; _inputs.py axis curveType checks",
        "body": """
    C = adsk.core.Curve3DTypes
    dump_enum("core.Curve3DTypes", C)
    emit(C.Line3DCurveType == 0 and C.Arc3DCurveType == 1 and C.Circle3DCurveType == 2,
         "enum-curve3d-types: line=" + str(C.Line3DCurveType) + " arc=" + str(C.Arc3DCurveType)
         + " circle=" + str(C.Circle3DCurveType))
""",
    },
    {
        "id": "camera-returns-copy",
        "claim": "Viewport.camera returns a COPY - mutating it moves nothing until viewport.camera is reassigned",
        "encoded_in": "tests/unit/test_view_set.py FakeViewport/FakeCamera (models a shared mutable object, the opposite, so only this row checks the real semantics)",
        "facts_on_pass": {"behavior.viewport_camera_returns_copy": True},
        "body": """
    vp = app.activeViewport
    cam = vp.camera
    e0 = cam.eye
    start_x = e0.x
    cam.eye = adsk.core.Point3D.create(start_x + 5.0, e0.y, e0.z)
    mid_x = vp.camera.eye.x
    unchanged = abs(mid_x - start_x) < 1e-9
    cam.isSmoothTransition = False
    vp.camera = cam
    applied = abs(vp.camera.eye.x - (start_x + 5.0)) < 1e-9
    emit(unchanged and applied,
         "camera-returns-copy: x after mutate=" + str(mid_x) + " (start " + str(start_x)
         + "), applied after reassign=" + str(applied))
""",
    },
    {
        "id": "basefeature-edit-scope",
        "claim": "An open base-feature edit scope is INVISIBLE: baseFeatures.count reads 0 and Design.timeline raises while open; finishEdit makes it appear (count 1)",
        "encoded_in": "tests/unit/test_design_mode.py; design_mode.py _OPEN_BASE_FEATURES comment",
        "facts_on_pass": {"behavior.open_base_feature_hidden": True},
        "body": """
    root = des.rootComponent
    n0 = root.features.baseFeatures.count
    bf = root.features.baseFeatures.add()
    started = bf.startEdit()
    try:
        open_count = root.features.baseFeatures.count
        tl_raises = False
        try:
            n = des.timeline.count
        except Exception:
            tl_raises = True
    finally:
        bf.finishEdit()
    closed_count = root.features.baseFeatures.count
    emit(bool(started) and open_count == n0 and tl_raises and closed_count == n0 + 1,
         "basefeature-edit-scope: started=" + str(started) + " open_count=" + str(open_count)
         + " (before add: " + str(n0) + ") timeline_raises=" + str(tl_raises)
         + " closed_count=" + str(closed_count))
""",
    },
    {
        "id": "export-arg-orders",
        "claim": "ExportManager arg orders differ by format: createSTLExportOptions(geometry, path) vs createSTEPExportOptions(path) - both land a file on execute()",
        "encoded_in": "tests/unit/test_design_export.py; design_export.py/_export.py",
        "need_box": True,
        "body": """
    import os
    em = des.exportManager
    base = os.path.join(os.getenv("TEMP") or "", "fe_measure_api")
    stl_path = base + ".stl"
    step_path = base + ".step"
    for p in (stl_path, step_path):
        if os.path.exists(p):
            os.remove(p)
    ok_stl = bool(em.execute(em.createSTLExportOptions(body, stl_path))) and os.path.exists(stl_path)
    ok_step = bool(em.execute(em.createSTEPExportOptions(step_path))) and os.path.exists(step_path)
    detail = "export-arg-orders: stl_landed=" + str(ok_stl) + " step_landed=" + str(ok_step)
    for p in (stl_path, step_path):
        if os.path.exists(p):
            os.remove(p)
    emit(ok_stl and ok_step, detail)
""",
    },
    {
        "id": "shape-dump-design-world",
        "claim": "Every design-side adsk type a SHARED fake impersonates exposes its live public attribute set (dir() membership) - the fake-shape lint checks fakes against these",
        "encoded_in": "tests/conftest.py shared fakes (BRepBody/BRepFace/BRepEdge/MakeComp/MakeDesign/FakeVector3D/FakePoint/...)",
        "need_box": True,
        "body": """
    root = des.rootComponent
    counts = []
    counts.append(dump_shape("Design", des))
    counts.append(dump_shape("Component", root))
    counts.append(dump_shape("BRepBodies", root.bRepBodies))
    counts.append(dump_shape("BRepBody", body))
    face = body.faces.item(0)
    counts.append(dump_shape("BRepFace", face))
    counts.append(dump_shape("Plane", face.geometry))
    edge = body.edges.item(0)
    counts.append(dump_shape("BRepEdge", edge))
    counts.append(dump_shape("Line3D", edge.geometry))
    sk = root.sketches.item(0)
    counts.append(dump_shape("Sketch", sk))
    counts.append(dump_shape("Profile", sk.profiles.item(0)))
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    counts.append(dump_shape("Occurrence", occ))
    counts.append(dump_shape("Vector3D", adsk.core.Vector3D.create(1.0, 0.0, 0.0)))
    counts.append(dump_shape("Point3D", adsk.core.Point3D.create(0.0, 0.0, 0.0)))
    counts.append(dump_shape("BoundingBox3D", body.boundingBox))
    counts.append(dump_shape("ObjectCollection", adsk.core.ObjectCollection.create()))
    vp = app.activeViewport
    counts.append(dump_shape("Viewport", vp))
    counts.append(dump_shape("Camera", vp.camera))
    sk2 = root.sketches.add(root.xYConstructionPlane)
    sk2.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(6.0, 0.0, 0.0), 0.5)
    cyl = root.features.extrudeFeatures.addSimple(
        sk2.profiles.item(0), adsk.core.ValueInput.createByReal(1.0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation).bodies.item(0)
    cyl_face = None
    for i in range(cyl.faces.count):
        if type(cyl.faces.item(i).geometry).__name__ == "Cylinder":
            cyl_face = cyl.faces.item(i)
    counts.append(dump_shape("Cylinder", cyl_face.geometry))
    circ_edge = None
    for i in range(cyl.edges.count):
        if type(cyl.edges.item(i).geometry).__name__ == "Circle3D":
            circ_edge = cyl.edges.item(i)
    counts.append(dump_shape("Circle3D", circ_edge.geometry))
    counts.append(dump_shape("Cone", adsk.core.Cone.create(
        adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Vector3D.create(0.0, 0.0, 1.0),
        0.5, 0.3)))
    emit(len(counts) == 20 and all(c > 0 for c in counts),
         "shape-dump-design-world: " + str(len(counts)) + " types, min attrs " + str(min(counts)))
""",
    },
    {
        "id": "cam-alloperations-shape",
        "claim": "Setup.allOperations FLATTENS folder-nested ops into the collection and DROPS the folder objects; counted and iterable. setup.operations holds only top-level ops; folders hang off setup.folders",
        "encoded_in": "tests/unit/test_cam_delete.py (matches); test_cam_show_toolpath.py + test_cam_edit_folders.py (contradictory encodings); _cam_common.walk_operations",
        "needs": "cam",
        "facts_on_pass": {"behavior.alloperations_flattens_folder_children": True,
                          "behavior.alloperations_drops_folder_objects": True},
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    setup = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            setup = cam.setups.item(i)
    top_names = [setup.operations.item(i).name for i in range(setup.operations.count)]
    folder = setup.folders.item(0)
    folder_names = [folder.operations.item(i).name for i in range(folder.operations.count)]
    allops = setup.allOperations
    kinds, names, casted_ok = [], [], True
    for i in range(allops.count):
        x = allops.item(i)
        kinds.append(type(x).__name__)
        names.append(str(getattr(x, "name", None)))
        if adsk.cam.Operation.cast(x) is None:
            casted_ok = False
    it_count = 0
    for x in allops:
        it_count += 1
    ok = (allops.count == 2 and casted_ok and sorted(names) == ["Face1", "Face2"]
          and top_names == ["Face1"] and folder_names == ["Face2"]
          and it_count == allops.count and kinds == ["Operation", "Operation"])
    emit(ok, "cam-alloperations-shape: allOperations=" + ",".join(names)
         + " kinds=" + ",".join(sorted(set(kinds))) + " top=" + ",".join(top_names)
         + " folder=" + ",".join(folder_names) + " iterated=" + str(it_count))
""",
    },
    {
        "id": "cam-parameter-expressions",
        "claim": "op.parameters.itemByName(name).expression is readable AND settable (readback returns what was written); string params carry single-quoted expressions",
        "encoded_in": "tests/unit/test_cam_edit_operation.py, test_cam_edit_setup.py, test_cam_set_nc_comment.py, test_cam_post.py, test_cam_edit_tools.py",
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    op = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            op = adsk.cam.Operation.cast(cam.setups.item(i).allOperations.item(0))
    p = op.parameters.itemByName("tool_feedCutting")
    readable = p is not None and isinstance(p.expression, str)
    old = p.expression
    p.expression = "777 mm/min"
    wrote = p.expression == "777 mm/min"
    p.expression = old
    restored = p.expression == old
    ctx = op.parameters.itemByName("context")
    strat = op.parameters.itemByName("strategy")
    quoted = (ctx is not None and str(ctx.expression).startswith("'")
              and strat is not None and str(strat.expression).startswith("'"))
    emit(readable and wrote and restored and quoted,
         "cam-parameter-expressions: read=" + repr(old) + " write-readback=" + str(wrote)
         + " restored=" + str(restored)
         + " quoted(context)=" + repr(None if ctx is None else ctx.expression))
""",
    },
    {
        "id": "cam-machining-time-knobs",
        "claim": "getMachiningTime on a generated op returns a positive estimate decomposing as totalFeedTime + totalRapidTime + totalToolChangeTime; the feedScale/rapidFeed/toolChangeTime arguments are INERT on this build (identical result across values) despite the API doc's percent / cm-per-s / s units",
        "encoded_in": "_cam_common.py get_machining_time_handler comment + constants; tests/unit/test__cam_common.py",
        "needs": "cam",
        "facts_on_pass": {"behavior.machining_time_args_inert": True},
        "body": """
    import time as _t
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    op = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            op = adsk.cam.Operation.cast(cam.setups.item(i).allOperations.item(0))
    future = cam.generateToolpath(op)
    waited = 0
    while not future.isGenerationCompleted and waited < 600:
        adsk.doEvents()
        _t.sleep(0.1)
        waited += 1
    if not future.isGenerationCompleted:
        emit(False, "cam-machining-time-knobs: generation did not complete in 60s - inconclusive, rerun")
        return
    m = cam.getMachiningTime(op, 100.0, 10.58, 1.5)
    t100 = m.machiningTime
    parts = m.totalFeedTime + m.totalRapidTime + m.totalToolChangeTime
    t50 = cam.getMachiningTime(op, 50.0, 10.58, 1.5).machiningTime
    t_slow_rapid = cam.getMachiningTime(op, 100.0, 0.1, 1.5).machiningTime
    inert = (abs(t50 - t100) < 1e-6 and abs(t_slow_rapid - t100) < 1e-6)
    emit(t100 > 0 and abs(t100 - parts) < 0.1 and inert,
         "cam-machining-time-knobs: t=" + str(round(t100, 2)) + "s parts-sum="
         + str(round(parts, 2)) + "s knobs-inert=" + str(inert))
""",
    },
    {
        "id": "shape-dump-cam-world",
        "claim": "Every CAM-side adsk type a fake impersonates exposes its live public attribute set (dir() membership)",
        "encoded_in": "tests/unit CAM fakes (FakeSetup/CAMFolder/op fakes) via the fake-shape lint's shared-fake scope",
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    counts = [dump_shape("CAM", cam)]
    setup = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            setup = cam.setups.item(i)
    counts.append(dump_shape("Setup", setup))
    op = adsk.cam.Operation.cast(setup.allOperations.item(0))
    counts.append(dump_shape("Operation", op))
    counts.append(dump_shape("CAMFolder", setup.folders.item(0)))
    emit(len(counts) == 4 and all(c > 0 for c in counts),
         "shape-dump-cam-world: " + str(len(counts)) + " types, min attrs " + str(min(counts)))
""",
    },
    {
        "id": "cam-children-tree",
        "claim": "Setup.children interleaves top-level Operations and folder objects whose type name is 'CAMFolder'; folder.allOperations and folder.children expose the folder's contents",
        "encoded_in": "tests/unit/test_cam_show_toolpath.py FakeSetup/CAMFolder; cam_show_toolpath._find_folder_ops type-name branch",
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    setup = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            setup = cam.setups.item(i)
    kids = setup.children
    kinds = []
    for i in range(kids.count):
        x = kids.item(i)
        kinds.append(type(x).__name__ + ":" + str(getattr(x, "name", None)))
    folder = setup.folders.item(0)
    ok = (kids.count == 2 and "Operation:Face1" in kinds and "CAMFolder:MeasureFolder" in kinds
          and type(folder).__name__ == "CAMFolder" and folder.allOperations.count == 1
          and folder.children.count == 1)
    emit(ok, "cam-children-tree: children=" + ", ".join(kinds) + " folder_type="
         + type(folder).__name__ + " folder_allops=" + str(folder.allOperations.count))
""",
    },
    {
        "id": "cam-generate-future",
        "claim": "A fresh op reads operationState NoToolpath (3) and hasToolpath False; isGenerationCompleted is the completion signal and the op then reads IsValid (0); numberOfOperations populates but numberOfCompleted is NOT a completion signal (observed 0 after a completed single-op generation)",
        "encoded_in": "tests/unit/test_cam_generate.py, test_cam_create_operation.py; cam_generate.py, cam_get_status.py",
        "needs": "cam",
        "facts_on_pass": {"behavior.number_of_completed_is_completion_signal": False},
        "body": """
    import time as _t
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    op2 = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            for x in cam.setups.item(i).allOperations:
                o = adsk.cam.Operation.cast(x)
                if o is not None and o.name == "Face2":
                    op2 = o
    pre_state = op2.operationState
    pre_path = op2.hasToolpath
    future = cam.generateToolpath(op2)
    n_ops = future.numberOfOperations
    waited = 0
    while not future.isGenerationCompleted and waited < 600:
        adsk.doEvents()
        _t.sleep(0.1)
        waited += 1
    if not future.isGenerationCompleted:
        emit(False, "cam-generate-future: generation did not complete in 60s - inconclusive, rerun")
        return
    n_done = future.numberOfCompleted
    post_state = op2.operationState
    emit(pre_state == 3 and pre_path is False and n_ops == 1 and post_state == 0,
         "cam-generate-future: pre_state=" + str(pre_state) + " pre_path=" + str(pre_path)
         + " ops=" + str(n_ops) + " numberOfCompleted=" + str(n_done)
         + " (informational only) post_state=" + str(post_state))
""",
    },
]


# CAM world-building scripts (run by _build_cam_world, not as measurement rows).
_CAM_BOX_SCRIPT = '''import adsk.core, adsk.fusion

def run(context):
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(4.0, 3.0, 0.0))
    prof = sk.profiles.item(0)
    ext = root.features.extrudeFeatures.addSimple(
        prof, adsk.core.ValueInput.createByReal(1.5),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.bodies.item(0).name = "CamMeasureBox"
    print("box made")
'''

_CAM_LIBURL_SCRIPT = '''import adsk.core, adsk.cam

def run(context):
    libs = adsk.cam.CAMManager.get().libraryManager.toolLibraries
    root = libs.urlByLocation(adsk.cam.LibraryLocations.Fusion360LibraryLocation)
    for a in libs.childAssetURLs(root):
        if "Milling Tools (Metric)" in a.leafName:
            print("LIBURL " + a.toString())
            return
    print("no matching sample library")
'''


def _build_cam_world():
    """Stand up MeasureSetup (box, Face1 top-level, Face2 inside MeasureFolder) in the CURRENT scratch
    doc via the server's own tools. Returns None on success, else the failing step's error."""
    is_error, payload = call("sys_execute_script", {"script": _CAM_BOX_SCRIPT})
    if is_error:
        return "box: " + str(payload)[:120]
    is_error, payload = call("view_switch_workspace", {"workspace": "manufacture"})
    if is_error:
        return "manufacture: " + str(payload)[:120]
    is_error, payload = call("sys_execute_script", {"script": _CAM_LIBURL_SCRIPT})
    lib_url = None
    if not is_error and isinstance(payload, str):
        for ln in payload.splitlines():
            if ln.startswith("LIBURL "):
                lib_url = ln[7:].strip()
    if not lib_url:
        return "sample tool library not found: " + str(payload)[:120]
    is_error, payload = call("cam_create_setup", {"operation_type": "milling", "name": "MeasureSetup"})
    if is_error:
        return "setup: " + str(payload)[:120]
    op_names = []
    for _ in range(2):
        is_error, payload = call("cam_create_operation", {
            "setup": "MeasureSetup", "strategy": "face", "tool_library_url": lib_url,
            "tool_index": 0, "generate": False})
        if is_error:
            return "operation: " + str(payload)[:120]
        op_names.append(payload.get("operation") if isinstance(payload, dict) else None)
    is_error, payload = call("cam_edit_folders",
                             {"action": "create", "setup": "MeasureSetup", "name": "MeasureFolder"})
    if is_error:
        return "folder: " + str(payload)[:120]
    is_error, payload = call("cam_edit_folders",
                             {"action": "move", "setup": "MeasureSetup", "folder": "MeasureFolder",
                              "operations": [op_names[1] or "Face2"]})
    if is_error:
        return "move: " + str(payload)[:120]
    return None


def _compose(row):
    box_line = ""
    if row.get("need_box"):
        box_line = '    body = make_box(des, "PB_{0}")\n'.format(row["id"].replace("-", "_"))
    body = row["body_fn"]() if "body_fn" in row else row["body"]
    return _TEMPLATE.format(box_line=box_line, body=body.strip("\n") + "\n")


def _verdict_lines(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith(("PASS ", "FAIL "))]


def _fact_lines(payload):
    """Parse 'FACT <dotted.key> <json-value>' lines a row script printed."""
    text = payload if isinstance(payload, str) else ""
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("FACT "):
            continue
        try:
            _, key, raw = ln.split(" ", 2)
            out.append((key, json.loads(raw)))
        except (ValueError, TypeError):
            pass
    return out


def _shape_lines(payload):
    """Parse 'SHAPE <TypeName> <attr attr ...>' lines into {type: set(attrs)}."""
    text = payload if isinstance(payload, str) else ""
    shapes = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("SHAPE "):
            continue
        parts = ln.split()
        if len(parts) >= 3:
            shapes.setdefault(parts[1], set()).update(parts[2:])
    return shapes


def _judge(row, is_error, payload):
    """One row's verdict: (status, detail). status is PASS / FAIL / ERROR."""
    if is_error:
        if row.get("expect") == "raise_or_abort":
            return "PASS", "script aborted at the misuse (the raise escaped try/except)"
        return "ERROR", str(payload)[:160]
    lines = _verdict_lines(payload)
    fails = [ln[5:] for ln in lines if ln.startswith("FAIL ")]
    if fails:
        return "FAIL", "; ".join(fails)[:200]
    if not lines:
        return "ERROR", "no verdict output"
    return "PASS", "; ".join(ln[5:] for ln in lines)[:200]


def _fusion_version():
    health_gate()
    is_error, payload = call("workspace_orient", {})
    if is_error or not isinstance(payload, dict) or "fusion_version" not in payload:
        sys.exit("workspace_orient did not return fusion_version - is the add-in current?")
    return payload["fusion_version"]


_STAMP_RE = re.compile(r"^Stamp: Fusion (\S+) \| verified (\S+)$", re.M)


def write_api_facts(facts, fusion_version, stamp_date, shapes=None):
    """Generate tests/live_api_facts.py from a fully-PASSING run: 'enums.*' keys become ENUMS,
    'behavior.*' keys become BEHAVIOR, dumped shapes become SHAPES. conftest imports the module
    to populate the mocks; the fake-shape lint checks SHARED fakes against SHAPES."""
    enums, behavior = {}, {}
    for key, value in facts.items():
        if key.startswith("enums."):
            family, member = key[len("enums."):].rsplit(".", 1)
            enums.setdefault(family, {})[member] = value
        elif key.startswith("behavior."):
            behavior[key[len("behavior."):]] = value
    lines = [
        "# GENERATED by tests/live/measure_api.py against live Fusion - DO NOT EDIT.",
        "# Regenerate: py -3 tests/live/measure_api.py (a fully-PASSING run rewrites this file).",
        '"""Measured adsk API facts. conftest populates the mock adsk modules and the shared fakes',
        'from these values, so the mocks carry measured data, not hand-typed claims. Each value is',
        'owned by the measurement row of the same name in tests/live/VERIFIED_API_FACTS.md."""',
        "",
        'FUSION_VERSION = "{0}"'.format(fusion_version),
        'VERIFIED_ON = "{0}"'.format(stamp_date),
        "",
        "# '<adsk namespace>.<Class>' -> {member: int} - seeded onto the mock adsk modules.",
        "ENUMS = {",
    ]
    for family in sorted(enums):
        lines.append('    "{0}": {{'.format(family))
        for member, value in sorted(enums[family].items(), key=lambda kv: (kv[1], kv[0])):
            lines.append('        "{0}": {1},'.format(member, value))
        lines.append("    },")
    lines += [
        "}",
        "",
        "# Behavior flags the shared fakes consume.",
        "BEHAVIOR = {",
    ]
    for key in sorted(behavior):
        lines.append('    "{0}": {1},'.format(key, behavior[key]))
    lines += [
        "}",
        "",
        "# Live public attribute membership per adsk type (dir() of a real object) - the",
        "# fake-shape lint requires every SHARED fake attribute to exist here.",
        "SHAPES = {",
    ]
    for tname in sorted(shapes or {}):
        lines.append('    "{0}": ['.format(tname))
        attrs = sorted(shapes[tname])
        for i in range(0, len(attrs), 6):
            lines.append("        " + " ".join('"{0}",'.format(a) for a in attrs[i:i + 6]))
        lines.append("    ],")
    lines += ["}", ""]
    with open(FACTS, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    return FACTS


def write_ledger(results, fusion_version, stamp_date):
    """Regenerate VERIFIED_API_FACTS.md from measurement results. results rows are (row, status, detail)."""
    lines = [
        "# Live-verified mock contracts (generated by measure_api.py - do not edit)",
        "",
        "Each row is a claim a unit-test fake encodes about the live adsk API, checked against a",
        "running Fusion by `measure_api.py`. A non-PASS row means the fake (and every test",
        "leaning on it) does not match the platform: update the fake and its consumers, then",
        "re-run to refresh the stamp. `--check` fails when the stamp differs from the installed",
        "Fusion or any row is not PASS.",
        "",
        "Stamp: Fusion {0} | verified {1}".format(fusion_version, stamp_date),
        "",
        "| result | claim id | claim | encoded in |",
        "|---|---|---|---|",
    ]
    for row, status, detail in results:
        result = status if status == "PASS" else "{0}: {1}".format(status, detail)
        lines.append("| {0} | {1} | {2} | {3} |".format(
            result.replace("|", "/"), row["id"], row["claim"].replace("|", "/"),
            row["encoded_in"].replace("|", "/")))
    with open(LEDGER, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def check():
    """Stamp-vs-installed-Fusion gate; measures nothing. Exit 0 = current, 1 = stale or non-PASS."""
    if not os.path.exists(LEDGER):
        print("VERIFIED_API_FACTS.md does not exist - run measure_api.py once against live Fusion.")
        return 1
    with open(LEDGER, encoding="utf-8") as fh:
        text = fh.read()
    m = _STAMP_RE.search(text)
    if not m:
        print("VERIFIED_API_FACTS.md has no stamp line - regenerate it (run measure_api.py).")
        return 1
    stamped_version, stamped_date = m.group(1), m.group(2)
    problems = [
        "non-PASS row: " + ln
        for ln in text.splitlines()
        if ln.startswith("|") and not ln.startswith(("| result", "|---", "| PASS "))
    ]
    live = _fusion_version()
    if live != stamped_version:
        problems.append("stamp is Fusion {0} (verified {1}) but the installed Fusion is {2} - "
                        "re-run the measurements to refresh the stamp".format(
                            stamped_version, stamped_date, live))
    if problems:
        print("\n".join(problems))
        return 1
    print("contracts current: Fusion {0}, verified {1}, all rows PASS".format(live, stamped_date))
    return 0


def run_measurements(write_json):
    fusion_version = _fusion_version()
    if "sys_execute_script" not in registered_tools():
        sys.exit("sys_execute_script is not registered - enable allow_execute_api_script in the "
                 "mcpServer settings and reload the add-in, then re-run.")
    is_error, payload = call("doc_new", {})
    if is_error:
        sys.exit("doc_new refused: {0}".format(payload))
    results = []
    facts = {}
    shapes = {}
    cam_world = {"built": False, "err": None}
    try:
        for row in ROWS:
            if row.get("needs") == "cam" and not cam_world["built"]:
                cam_world["err"] = _build_cam_world()
                cam_world["built"] = True
            if row.get("needs") == "cam" and cam_world["err"]:
                results.append((row, "ERROR", "cam world: " + cam_world["err"]))
                print("  {0:6} {1:28} {2}".format("ERROR", row["id"], "cam world: " + cam_world["err"][:70]))
                continue
            is_error, payload = call("sys_execute_script", {"script": _compose(row)})
            status, detail = _judge(row, is_error, payload)
            if status == "PASS":
                facts.update(row.get("facts_on_pass") or {})
                facts.update(_fact_lines(payload))
                for tname, attrs in _shape_lines(payload).items():
                    shapes.setdefault(tname, set()).update(attrs)
            results.append((row, status, detail))
            print("  {0:6} {1:28} {2}".format(status, row["id"], detail[:90]))
            time.sleep(0.1)
    finally:
        call("doc_close", {"save_changes": False})
    stamp_date = time.strftime("%Y-%m-%d")
    write_ledger(results, fusion_version, stamp_date)
    print("\nwrote {0} (stamp: Fusion {1}, {2})".format(LEDGER, fusion_version, stamp_date))
    if all(s == "PASS" for _, s, _ in results):
        print("wrote {0} ({1} enum families, {2} behavior flags, {3} shaped types)".format(
            write_api_facts(facts, fusion_version, stamp_date, shapes),
            sum(1 for k in facts if k.startswith("enums.")) and len(
                {k.rsplit(".", 1)[0] for k in facts if k.startswith("enums.")}),
            sum(1 for k in facts if k.startswith("behavior.")),
            len(shapes)))
    else:
        print("live_api_facts.py NOT rewritten - resolve the non-PASS rows first.")
    if write_json:
        results_dir = os.path.join(os.path.dirname(LEDGER), "results")
        os.makedirs(results_dir, exist_ok=True)
        path = os.path.join(results_dir, "contracts-{0}.json".format(time.strftime("%Y%m%d-%H%M%S")))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"fusion_version": fusion_version, "date": stamp_date,
                       "rows": [{"id": r["id"], "status": s, "detail": d}
                                for r, s, d in results]}, fh, indent=2)
        print("wrote {0}".format(path))
    return 1 if any(s != "PASS" for _, s, _ in results) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    sys.exit(check() if args.check else run_measurements(args.json))
