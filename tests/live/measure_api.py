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
_ENUM_FAMILY_RE = re.compile(r"adsk\.(core|fusion|cam|drawing)\.([A-Za-z]*(?:Types|States?|Modes|Directions|Locations|Positions|Alignments?|Sizes|Formats))\b")
# A family reached only through _drawing_common.enum_value("<Family>", "<member>") never appears as
# a literal adsk.drawing.<Family> in tool source, so the textual scan above cannot see it - the
# STRING argument is the real reference. enum_value resolves exclusively against adsk.drawing.
_ENUM_BY_NAME_RE = re.compile(r"""enum_value\(\s*["']([A-Za-z]+)["']""")


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
                for cls in _ENUM_BY_NAME_RE.findall(code):
                    fams.add("drawing." + cls)
    return sorted(fams)


_FACT_BEHAVIOR_PRINT = re.compile(r"FACT behavior\.([a-z0-9_]+)")


def emitted_behavior_keys():
    """Every behavior flag key a measurement row can emit - ``facts_on_pass`` entries plus
    ``FACT behavior.*`` prints inside row script bodies. The reverse gate in
    tests/lints/test_enum_families_measured.py consumes this: each emitted key must exist in the
    generated live_api_facts.BEHAVIOR, so a key rename here goes red until a live regen."""
    keys = set()
    for row in ROWS:
        for key in (row.get("facts_on_pass") or {}):
            if key.startswith("behavior."):
                keys.add(key[len("behavior."):])
        body = row["body_fn"]() if "body_fn" in row else row.get("body", "")
        keys |= set(_FACT_BEHAVIOR_PRINT.findall(body))
    return sorted(keys)


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
_TEMPLATE = '''import adsk.core, adsk.fusion, adsk.cam, adsk.drawing

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
        "id": "save-image-options-defaults",
        "claim": ("SaveImageFileOptions.create(path) initializes width/height to 0 and "
                  "isBackgroundTransparent False / isAntiAliased True"),
        "encoded_in": "test__view_common.py fake options initial values; _view_common._write_image size assignment",
        "facts_on_pass": {"behavior.save_image_options_defaults": True},
        "body": """
    opts = adsk.core.SaveImageFileOptions.create("probe_never_written.png")
    emit(opts.width == 0 and opts.height == 0
         and opts.isBackgroundTransparent is False and opts.isAntiAliased is True,
         "save-image-options-defaults: w=" + str(opts.width) + " h=" + str(opts.height)
         + " transparent=" + repr(opts.isBackgroundTransparent)
         + " aa=" + repr(opts.isAntiAliased))
""",
    },
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
        "id": "joint-limit-out-of-range-ignored",
        "claim": ("Assigning a rotationValue/slideValue STRICTLY beyond an enabled joint limit is "
                  "IGNORED - the value stays where it was, Fusion never clamps; assigning exactly "
                  "AT an enabled bound lands on it"),
        "encoded_in": ("commands/mcpServer/tools/joint_drive.py _limit_refusal (refuse before "
                       "assigning); test_joint_drive.py refusal tests"),
        "body": """
    root = des.rootComponent
    tr = adsk.core.Matrix3D.create()
    occs = []
    for nm in ("LimA", "LimB", "LimC"):
        occ = root.occurrences.addNewComponent(tr)
        c = occ.component
        c.name = nm
        sk = c.sketches.add(c.xYConstructionPlane)
        sk.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0.0, 0.0, 0.0), 0.5)
        c.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(0.5),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        occs.append(occ)
    geo = adsk.fusion.JointGeometry.createByPoint(
        occs[1].component.originConstructionPoint.createForAssemblyContext(occs[1]))
    ji = root.asBuiltJoints.createInput(occs[0], occs[1], geo)
    ji.setAsRevoluteJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
    rj = root.asBuiltJoints.add(ji)
    rl = rj.jointMotion.rotationLimits
    rl.isMinimumValueEnabled = True
    rl.minimumValue = -0.17453293
    rl.isMaximumValueEnabled = True
    rl.maximumValue = 0.17453293
    rj.jointMotion.rotationValue = 0.08726646
    parked = rj.jointMotion.rotationValue
    rj.jointMotion.rotationValue = 0.78539816
    beyond = rj.jointMotion.rotationValue
    rj.jointMotion.rotationValue = 0.17453293
    at_bound = rj.jointMotion.rotationValue
    geo2 = adsk.fusion.JointGeometry.createByPoint(
        occs[2].component.originConstructionPoint.createForAssemblyContext(occs[2]))
    si = root.asBuiltJoints.createInput(occs[0], occs[2], geo2)
    si.setAsSliderJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
    sj = root.asBuiltJoints.add(si)
    sl = sj.jointMotion.slideLimits
    sl.isMinimumValueEnabled = True
    sl.minimumValue = -1.0
    sl.isMaximumValueEnabled = True
    sl.maximumValue = 1.0
    sj.jointMotion.slideValue = 4.5
    s_beyond = sj.jointMotion.slideValue
    ok_rot = (abs(parked - 0.08726646) < 1e-5 and abs(beyond - parked) < 1e-6
              and abs(at_bound - 0.17453293) < 1e-5)
    ok_sld = abs(s_beyond) < 1e-6
    print("FACT behavior.joint_limit_out_of_range_ignored "
          + ("true" if (ok_rot and ok_sld) else "false"))
    emit(ok_rot and ok_sld,
         "joint-limit-out-of-range-ignored: parked=" + str(parked) + " beyond=" + str(beyond)
         + " at_bound=" + str(at_bound) + " slide_beyond=" + str(s_beyond)
         + " (expect 0.08727 / unchanged / 0.17453 / 0)")
""",
    },
    {
        "id": "asbuilt-rigidgroup-health-via-timeline",
        "claim": ("AsBuiltJoint and RigidGroup raise AttributeError on BOTH healthState and "
                  "errorOrWarningMessage while each one's timelineObject answers both - health "
                  "for these two types is readable only through the timeline item"),
        "encoded_in": ("_assert.py compute_state / compute_failure safe-guarded entity reads; "
                       "tests/unit/test_assembly_get.py TimelineObject-answers comment; "
                       "tests/unit/test_assembly_joints_advanced.py poison-read comment"),
        "body": """
    root = des.rootComponent
    tr = adsk.core.Matrix3D.create()
    occs = []
    for nm in ("HlA", "HlB", "HlC", "HlD"):
        occ = root.occurrences.addNewComponent(tr)
        c = occ.component
        c.name = nm
        sk = c.sketches.add(c.xYConstructionPlane)
        sk.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0.0, 0.0, 0.0), 0.5)
        c.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(0.5),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        occs.append(occ)
    geo = adsk.fusion.JointGeometry.createByPoint(
        occs[1].component.originConstructionPoint.createForAssemblyContext(occs[1]))
    ji = root.asBuiltJoints.createInput(occs[0], occs[1], geo)
    ji.setAsRevoluteJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
    abj = root.asBuiltJoints.add(ji)
    # The rigid group takes the OTHER pair: grouping the jointed pair raises
    # "A joint in system exists for the provided input. System will be over constrained".
    coll = adsk.core.ObjectCollection.create()
    coll.add(occs[2])
    coll.add(occs[3])
    rg = root.rigidGroups.add(coll, True)
    def health_probe(ent):
        raises = 0
        for attr in ("healthState", "errorOrWarningMessage"):
            try:
                getattr(ent, attr)
            except AttributeError:
                raises += 1
        t = ent.timelineObject
        hs = t.healthState
        msg = t.errorOrWarningMessage
        return raises, isinstance(hs, int) and not isinstance(hs, bool), isinstance(msg, str)
    j_raises, j_hs, j_msg = health_probe(abj)
    g_raises, g_hs, g_msg = health_probe(rg)
    emit(j_raises == 2 and g_raises == 2 and j_hs and j_msg and g_hs and g_msg,
         "asbuilt-rigidgroup-health-via-timeline: asbuilt raises=" + str(j_raises)
         + "/2 timeline hs_int=" + str(j_hs) + " msg_str=" + str(j_msg)
         + "; rigidgroup raises=" + str(g_raises) + "/2 timeline hs_int="
         + str(g_hs) + " msg_str=" + str(g_msg))
""",
    },
    {
        "id": "evaluateexpression-returns-internal-cm",
        "claim": ("UnitsManager.evaluateExpression returns the value in INTERNAL units (cm) "
                  "regardless of the units argument - that argument only names the unit a BARE "
                  "number in the expression is read in, never the output unit"),
        "encoded_in": ("_inputs.length_value_input value_cm (the read-back compare's unit "
                       "convention); tests/unit/test_inputs.py + test_model_fillet_chamfer.py "
                       "expression fakes, which answer in cm"),
        "body": """
    um = des.unitsManager
    a = um.evaluateExpression("13 mm", "mm")
    b = um.evaluateExpression("1 cm", "mm")
    c = um.evaluateExpression("2", "mm")
    emit(abs(a - 1.3) < 1e-9 and abs(b - 1.0) < 1e-9 and abs(c - 0.2) < 1e-9,
         "evaluateexpression-returns-internal-cm: '13 mm'->" + str(a) + " (expect 1.3) '1 cm'->"
         + str(b) + " (expect 1.0) bare '2' under a mm units arg->" + str(c) + " (expect 0.2)")
""",
    },
    {
        "id": "extrude-extent-distance-reads-requested-cm",
        "claim": ("An extrude's extentOne.distance.value reads back the REQUESTED distance in "
                  "internal cm across the extent forms model_extrude sets: symmetric+taper "
                  "(SymmetricExtentDefinition, the per-side number), one-sided+taper, two_side "
                  "(extentOne carries side one, extentTwo side two, both positive, no swap), a "
                  "cut that bottoms out inside its target (the requested value, never clipped), "
                  "and a NEGATIVE distance (the requested SIGN kept, -15 mm reads -1.5)"),
        "encoded_in": ("model_extrude's distance read-back compare (want_distance_cm vs "
                       "extentOne.distance.value); surface_create._landed_depth, the same contract"),
        "need_box": True,
        "body": """
    root = des.rootComponent
    feats = root.features.extrudeFeatures
    F = adsk.fusion.FeatureOperations
    V = adsk.core.ValueInput
    def profile_at(cx):
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(cx, 0.0, 0.0), 0.4)
        return sk.profiles.item(0)
    i1 = feats.createInput(profile_at(3.0), F.NewBodyFeatureOperation)
    i1.setSymmetricExtent(V.createByReal(1.0), False, V.createByString("3 deg"))
    f1 = feats.add(i1)
    sym_type = type(f1.extentOne).__name__
    sym_v = f1.extentOne.distance.value
    i2 = feats.createInput(profile_at(6.0), F.NewBodyFeatureOperation)
    i2.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(V.createByReal(1.0)),
                        adsk.fusion.ExtentDirections.PositiveExtentDirection,
                        V.createByString("3 deg"))
    f2 = feats.add(i2)
    one_v = f2.extentOne.distance.value
    i3 = feats.createInput(profile_at(9.0), F.NewBodyFeatureOperation)
    i3.setTwoSidesDistanceExtent(V.createByReal(1.0), V.createByReal(0.5))
    f3 = feats.add(i3)
    two_a = f3.extentOne.distance.value
    two_b = f3.extentTwo.distance.value
    skc = root.sketches.add(root.xYConstructionPlane)
    skc.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(0.5, 0.5, 0.0), 0.2)
    i4 = feats.createInput(skc.profiles.item(0), F.CutFeatureOperation)
    i4.setDistanceExtent(False, V.createByReal(0.3))
    f4 = feats.add(i4)
    cut_v = f4.extentOne.distance.value
    i5 = feats.createInput(profile_at(12.0), F.NewBodyFeatureOperation)
    i5.setDistanceExtent(False, V.createByReal(-1.5))
    f5 = feats.add(i5)
    neg_v = f5.extentOne.distance.value
    ok_all = (sym_type == "SymmetricExtentDefinition" and abs(sym_v - 1.0) < 1e-9
              and abs(one_v - 1.0) < 1e-9 and abs(two_a - 1.0) < 1e-9
              and abs(two_b - 0.5) < 1e-9 and abs(cut_v - 0.3) < 1e-9
              and abs(neg_v + 1.5) < 1e-9)
    emit(ok_all, "extrude-extent-distance-reads-requested-cm: sym(" + sym_type + ")="
         + str(sym_v) + " oneT=" + str(one_v) + " two=" + str(two_a) + "/" + str(two_b)
         + " cut_into_box=" + str(cut_v) + " neg=" + str(neg_v)
         + " (expect 1.0 / 1.0 / 1.0,0.5 / 0.3 / -1.5)")
""",
    },
    {
        "id": "joint-drive-moves-occurrence-one",
        "claim": ("Driving an as-built slider displaces occurrenceONE: with occurrenceTwo locked to "
                  "its parent, occurrenceOne's transform2 translation moves by +the commanded value "
                  "along the joint's slideDirectionVector and occurrenceTwo does not move at all"),
        "encoded_in": ("commands/mcpServer/tools/joint_drive.py the 'moved' placement read-back; "
                       "tests/unit/test_joint_drive.py TestMovedMember"),
        "facts_on_pass": {"behavior.joint_drive_moves_occurrence_one": True},
        "body": """
    root = des.rootComponent
    anchor = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    anchor.component.name = "DrvOneAnchor"
    off = adsk.core.Matrix3D.create()
    off.translation = adsk.core.Vector3D.create(4.0, 0.0, 0.0)
    mover = root.occurrences.addNewComponent(off)
    mover.component.name = "DrvOneMover"
    anchor.isGroundToParent = True
    geo = adsk.fusion.JointGeometry.createByPoint(
        mover.component.originConstructionPoint.createForAssemblyContext(mover))
    ji = root.asBuiltJoints.createInput(mover, anchor, geo)
    ji.setAsSliderJointMotion(adsk.fusion.JointDirections.XAxisJointDirection)
    j = root.asBuiltJoints.add(ji)
    v = j.jointMotion.slideDirectionVector
    b1, b2 = mover.transform2.translation, anchor.transform2.translation
    before_one = (b1.x, b1.y, b1.z)
    before_two = (b2.x, b2.y, b2.z)
    j.jointMotion.slideValue = 2.5
    a1, a2 = mover.transform2.translation, anchor.transform2.translation
    d_one = (a1.x - before_one[0], a1.y - before_one[1], a1.z - before_one[2])
    d_two = (a2.x - before_two[0], a2.y - before_two[1], a2.z - before_two[2])
    one_moved = max(abs(d_one[i] - 2.5 * (v.x, v.y, v.z)[i]) for i in range(3)) < 1e-6
    two_still = max(abs(c) for c in d_two) < 1e-6
    emit(one_moved and two_still,
         "joint-drive-moves-occurrence-one: one delta=" + str(d_one) + " two delta=" + str(d_two)
         + " slideDirectionVector=(" + str(v.x) + "," + str(v.y) + "," + str(v.z) + ")"
         + " (expect one = +2.5 along the vector, two = 0)")
""",
    },
    {
        "id": "joint-drive-sign-follows-slide-direction-vector",
        "claim": ("A slider drive displaces the moving member ALONG jointMotion.slideDirectionVector "
                  "- a joint built on the frame Y axis moves the part in +Y, not +X - and the sign "
                  "follows the commanded value: a negative command lands the part on the other side"),
        "encoded_in": ("commands/mcpServer/tools/joint_drive.py publishes that vector as "
                       "'slide_direction' beside the measured 'moved' delta; tests/unit/"
                       "test_joint_drive.py TestDriveDirection"),
        "facts_on_pass": {"behavior.joint_drive_sign_follows_slide_direction_vector": True},
        "body": """
    root = des.rootComponent
    anchor = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    anchor.component.name = "DrvSignAnchor"
    off = adsk.core.Matrix3D.create()
    off.translation = adsk.core.Vector3D.create(0.0, 4.0, 0.0)
    mover = root.occurrences.addNewComponent(off)
    mover.component.name = "DrvSignMover"
    anchor.isGroundToParent = True
    geo = adsk.fusion.JointGeometry.createByPoint(
        mover.component.originConstructionPoint.createForAssemblyContext(mover))
    ji = root.asBuiltJoints.createInput(mover, anchor, geo)
    ji.setAsSliderJointMotion(adsk.fusion.JointDirections.YAxisJointDirection)
    j = root.asBuiltJoints.add(ji)
    v = j.jointMotion.slideDirectionVector
    vec = (v.x, v.y, v.z)
    h = mover.transform2.translation
    home = (h.x, h.y, h.z)
    j.jointMotion.slideValue = 2.5
    p = mover.transform2.translation
    d_pos = (p.x - home[0], p.y - home[1], p.z - home[2])
    j.jointMotion.slideValue = -1.0
    n = mover.transform2.translation
    d_neg = (n.x - home[0], n.y - home[1], n.z - home[2])
    on_y = abs(vec[1]) > 0.999 and abs(vec[0]) < 1e-6 and abs(vec[2]) < 1e-6
    pos_ok = max(abs(d_pos[i] - 2.5 * vec[i]) for i in range(3)) < 1e-6
    neg_ok = max(abs(d_neg[i] + 1.0 * vec[i]) for i in range(3)) < 1e-6
    emit(on_y and pos_ok and neg_ok,
         "joint-drive-sign-follows-slide-direction-vector: vector=" + str(vec)
         + " delta(+2.5)=" + str(d_pos) + " delta(-1.0)=" + str(d_neg)
         + " (expect the vector on Y, then +2.5 and -1.0 along it)")
""",
    },
    {
        "id": "joint-drive-anchored-side-flips-mover",
        "claim": ("Which member moves is decided by which side is ANCHORED, not by the member order: "
                  "with occurrenceONE locked to its parent, the same positive slide command displaces "
                  "occurrenceTWO by MINUS the value along the same slideDirectionVector"),
        "encoded_in": ("commands/mcpServer/tools/joint_drive.py reports the member that moved "
                       "instead of naming one from the joint's member order; tests/unit/"
                       "test_joint_drive.py TestMovedMember"),
        "facts_on_pass": {"behavior.joint_drive_anchored_side_flips_mover": True},
        "body": """
    root = des.rootComponent
    anchor = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    anchor.component.name = "DrvFlipAnchor"
    off = adsk.core.Matrix3D.create()
    off.translation = adsk.core.Vector3D.create(-4.0, 0.0, 0.0)
    free = root.occurrences.addNewComponent(off)
    free.component.name = "DrvFlipFree"
    anchor.isGroundToParent = True
    geo = adsk.fusion.JointGeometry.createByPoint(
        free.component.originConstructionPoint.createForAssemblyContext(free))
    ji = root.asBuiltJoints.createInput(anchor, free, geo)
    ji.setAsSliderJointMotion(adsk.fusion.JointDirections.XAxisJointDirection)
    j = root.asBuiltJoints.add(ji)
    v = j.jointMotion.slideDirectionVector
    b1, b2 = anchor.transform2.translation, free.transform2.translation
    before_one = (b1.x, b1.y, b1.z)
    before_two = (b2.x, b2.y, b2.z)
    j.jointMotion.slideValue = 2.5
    a1, a2 = anchor.transform2.translation, free.transform2.translation
    d_one = (a1.x - before_one[0], a1.y - before_one[1], a1.z - before_one[2])
    d_two = (a2.x - before_two[0], a2.y - before_two[1], a2.z - before_two[2])
    one_still = max(abs(c) for c in d_one) < 1e-6
    two_flipped = max(abs(d_two[i] + 2.5 * (v.x, v.y, v.z)[i]) for i in range(3)) < 1e-6
    emit(one_still and two_flipped,
         "joint-drive-anchored-side-flips-mover: one(anchored) delta=" + str(d_one)
         + " two delta=" + str(d_two) + " slideDirectionVector=(" + str(v.x) + "," + str(v.y)
         + "," + str(v.z) + ") (expect one = 0, two = -2.5 along the vector)")
""",
    },
    {
        "id": "joint-revolute-value-stored-verbatim",
        "claim": ("A revolute jointMotion.rotationValue stores the angle it is GIVEN, verbatim: 750 "
                  "deg reads back 750, a following 30 deg reads back 30, 100 reads 100 and 390 reads "
                  "390 - the value neither accumulates the turn it just made nor normalizes into "
                  "[0,360)"),
        "encoded_in": ("commands/mcpServer/tools/joint_drive.py value_now + its "
                       "angle_deg_normalized twin and the equivalent_pose gate; tests/unit/"
                       "test_joint_drive.py TestEquivalentPose"),
        "facts_on_pass": {"behavior.joint_revolute_value_stored_verbatim": True},
        "body": """
    import math
    root = des.rootComponent
    anchor = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    anchor.component.name = "DrvSpinAnchor"
    off = adsk.core.Matrix3D.create()
    off.translation = adsk.core.Vector3D.create(0.0, -4.0, 0.0)
    spinner = root.occurrences.addNewComponent(off)
    spinner.component.name = "DrvSpinRotor"
    anchor.isGroundToParent = True
    geo = adsk.fusion.JointGeometry.createByPoint(
        spinner.component.originConstructionPoint.createForAssemblyContext(spinner))
    ji = root.asBuiltJoints.createInput(spinner, anchor, geo)
    ji.setAsRevoluteJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
    j = root.asBuiltJoints.add(ji)
    reads = []
    for want in (750.0, 30.0, 100.0, 390.0):
        j.jointMotion.rotationValue = math.radians(want)
        reads.append(round(math.degrees(j.jointMotion.rotationValue), 6))
    verbatim = all(abs(reads[i] - w) < 1e-4
                   for i, w in enumerate((750.0, 30.0, 100.0, 390.0)))
    emit(verbatim,
         "joint-revolute-value-stored-verbatim: commanded 750/30/100/390 read back "
         + str(reads) + " (expect the same four values)")
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
        "id": "find-entity-token-multi",
        "claim": ("ONE token can name SEVERAL entities: splitting a face makes the PRE-split token "
                  "resolve to a vector of BOTH survivors, so taking [0] acts on geometry the caller "
                  "never picked"),
        "encoded_in": ("tests/unit/test_inputs.py token_env / _SplitFace (a LIST value models the "
                       "several entities one token answers with); commands/mcpServer/tools/"
                       "_inputs.py the locator pick-or-refuse over a multi-entity token"),
        "need_box": True,
        "facts_on_pass": {"behavior.find_entity_token_multi_after_face_split": True},
        "body": """
    root = des.rootComponent
    top = None
    for i in range(body.faces.count):
        f = body.faces.item(i)
        if top is None or f.pointOnFace.z > top.pointOnFace.z:
            top = f
    tok = top.entityToken
    before = len(des.findEntityByToken(tok))
    pi = root.constructionPlanes.createInput()
    pi.setByOffset(root.xZConstructionPlane, adsk.core.ValueInput.createByReal(0.5))
    plane = root.constructionPlanes.add(pi)
    faces = adsk.core.ObjectCollection.create()
    faces.add(top)
    si = root.features.splitFaceFeatures.createInput(faces, plane, True)
    root.features.splitFaceFeatures.add(si)
    after = des.findEntityByToken(tok)
    kinds = []
    for x in after:
        kinds.append(type(x).__name__)
    emit(before == 1 and len(after) > 1 and set(kinds) == set(["BRepFace"]),
         "find-entity-token-multi: pre_split=" + str(before) + " post_split="
         + str(len(after)) + " kinds=" + ",".join(kinds) + " (expect 1 then >1 BRepFace)")
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
        "encoded_in": "tests/unit/test_mesh_export.py + test_inputs.py + test_surface_ops.py (all omit it, correct)",
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
        "id": "camera-viewextents-is-linear-not-area",
        "claim": "Camera.viewExtents is a LINEAR extent, not an area: with the LIMITING screen axis held fixed, a model N times taller fits to a value ~N times larger, not ~N squared. The SDK words it 'the area of the view', which is why this is measured rather than read",
        "encoded_in": "view_set.py _frame_ratio (the ratio multiplies viewExtents unsquared) and its point-of-use comment; view_screenshot.py's zoom, which scales the same property; test_view_set.py TestFocusFraming ratio expectations",
        "facts_on_pass": {"behavior.camera_view_extents_is_linear": True},
        "body": """
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        des = adsk.fusion.Design.cast(tmp.products.itemByProductType("DesignProductType"))
        root = des.rootComponent
        vp = app.activeViewport

        # A 1 cm square post of a given height. Growing only the HEIGHT, on a front camera, on a
        # viewport wider than it is tall, keeps BOTH fits limited by the same screen axis - which
        # is the whole point: a first attempt at this row left the camera wherever the document had
        # it, so an iso-ish view spread the growth across both screen axes and the ratio came back
        # 5.22, neither linear (10.98) nor area (120.6). A ratio is only meaningful between two fits
        # limited by the SAME axis (which the sibling row measures).
        def post(height):
            sk = root.sketches.add(root.xYConstructionPlane)
            sk.sketchCurves.sketchLines.addTwoPointRectangle(
                adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(1.0, 1.0, 0.0))
            root.features.extrudeFeatures.addSimple(
                sk.profiles.item(0), adsk.core.ValueInput.createByReal(height),
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

        def spans():
            bb = root.boundingBox
            return (bb.maxPoint.x - bb.minPoint.x, bb.maxPoint.z - bb.minPoint.z,
                    (bb.maxPoint.z + bb.minPoint.z) / 2.0)

        def fit_front():
            across, up, mid = spans()
            cam = vp.camera                      # front: x across the screen, z up it
            cam.upVector = adsk.core.Vector3D.create(0.0, 0.0, 1.0)
            cam.target = adsk.core.Point3D.create(0.5, 0.0, mid)
            cam.eye = adsk.core.Point3D.create(0.5, -100.0, mid)
            cam.isFitView = True
            vp.camera = cam
            return vp.camera.viewExtents

        post(2.0)
        w0, h0, _ = spans()
        e0 = fit_front()
        post(20.0)                               # same 1 cm footprint, ten times taller
        w1, h1, _ = spans()
        e1 = fit_front()
        aspect = float(vp.width) / vp.height
        model_ratio = h1 / h0
        ext_ratio = e1 / e0
        # both fits must be HEIGHT-limited or the comparison is meaningless - assert it, do not
        # assume it, since that assumption is exactly what the first version of this row got wrong
        height_limited = (w0 / h0) < aspect and (w1 / h1) < aspect
        emit(height_limited and abs(ext_ratio - model_ratio) < model_ratio * 0.2,
             "camera-viewextents-is-linear-not-area: height " + ("%.3f" % h0) + "->" + ("%.3f" % h1)
             + " (x" + ("%.2f" % model_ratio) + ") extents " + ("%.3f" % e0) + "->" + ("%.3f" % e1)
             + " (x" + ("%.2f" % ext_ratio) + ") area_would_be x"
             + ("%.1f" % (model_ratio * model_ratio))
             + " height_limited=" + repr(height_limited) + " aspect=" + ("%.3f" % aspect))
    finally:
        tmp.close(False)
""",
    },
    {
        "id": "camera-viewextents-follows-limiting-axis",
        "claim": "Camera.viewExtents after a fit tracks whichever SCREEN AXIS limited that fit - the height for a tall model, the width for a wide one - so it is not the view width and a framing ratio cannot be taken against a world box's own spans",
        "encoded_in": "view_set.py _frame_ratio, which reconstructs the FRAME from the viewport aspect (vp.width/vp.height) instead of dividing by the world box; test_view_set.py test_a_flat_world_is_measured_against_the_frame_not_its_own_height",
        "facts_on_pass": {"behavior.camera_view_extents_follows_limiting_axis": True},
        "body": """
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        des = adsk.fusion.Design.cast(tmp.products.itemByProductType("DesignProductType"))
        root = des.rootComponent
        vp = app.activeViewport
        # a tall, thin post: 1 cm across, 10 cm up - so the FIT is limited by the height, and a
        # value that tracked the width could not possibly hold the model
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(1.0, 1.0, 0.0))
        root.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(10.0),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        cam = vp.camera                       # front: x runs across the screen, z up it
        cam.eye = adsk.core.Point3D.create(0.5, -50.0, 5.0)
        cam.target = adsk.core.Point3D.create(0.5, 0.0, 5.0)
        cam.upVector = adsk.core.Vector3D.create(0.0, 0.0, 1.0)
        cam.isFitView = True
        vp.camera = cam
        ext = vp.camera.viewExtents
        bb = root.boundingBox
        across = bb.maxPoint.x - bb.minPoint.x
        up = bb.maxPoint.z - bb.minPoint.z
        emit(abs(ext - up) < up * 0.3 and ext > across * 2.0,
             "camera-viewextents-follows-limiting-axis: across=" + ("%.3f" % across)
             + " up=" + ("%.3f" % up) + " extents=" + ("%.3f" % ext)
             + " (tracks the taller axis, not the width)")
    finally:
        tmp.close(False)
""",
    },
    {
        "id": "meshbody-volume-open-returns-zero",
        "claim": "MeshBody.volume on a mesh that is NOT closed RETURNS 0.0 - it does not raise; a null volume in a payload therefore means the field could not be read at all, never 'the mesh is open'",
        "encoded_in": "mesh_ops.py _mesh_summary/mesh_get note+description; mesh_shell.py _closed (the reason the closure flag is sampled at both ends); tests/conftest.py's shared MeshBody fake, which reads this BEHAVIOR flag rather than hard-coding it; test_mesh_ops.py's own MeshBody",
        "facts_on_pass": {"behavior.meshbody_volume_open_raises": False},
        "body": """
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        des = adsk.fusion.Design.cast(tmp.products.itemByProductType("DesignProductType"))
        des.designType = adsk.fusion.DesignTypes.DirectDesignType
        mb = des.rootComponent.meshBodies.addByTriangleMeshData(
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], [0, 1, 2], [], [])
        v = mb.volume
        emit((mb.isClosed is False) and isinstance(v, float) and v == 0.0,
             "meshbody-volume-open-returns-zero: isClosed=" + str(mb.isClosed)
             + " volume=" + repr(v))
    finally:
        tmp.close(False)
""",
    },
    {
        "id": "shape-dump-mesh-world",
        "claim": "MeshBody and its PolygonMesh dump non-empty member lists, the latter carrying both nodeCoordinatesAsDouble and normalVectorsAsDouble - the arrays a smooth's coordinate diff and a reverse's normal negation are judged on. displayMesh is a TriangleMesh, dumped alongside so the count fake is swept too. Totals are not pinned: they vary by a member or two across rigs and builds, and the SHAPE lines are the product",
        "encoded_in": "tests/conftest.py shared MeshBody / _FakePolygonMesh / _FakeTriangleMesh fakes; tests/lints/test_fake_shapes_exist.py sweeps them against these dumps",
        "body": """
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        des = adsk.fusion.Design.cast(tmp.products.itemByProductType("DesignProductType"))
        des.designType = adsk.fusion.DesignTypes.DirectDesignType
        mb = des.rootComponent.meshBodies.addByTriangleMeshData(
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], [0, 1, 2], [], [])
        n_body = dump_shape("MeshBody", mb)
        n_poly = dump_shape("PolygonMesh", mb.mesh)
        n_tri = dump_shape("TriangleMesh", mb.displayMesh)
        arrays = [n for n in dir(mb.mesh) if not n.startswith("_")]
        emit(n_body > 0 and n_poly > 0 and n_tri > 0
             and "nodeCoordinatesAsDouble" in arrays and "normalVectorsAsDouble" in arrays,
             "shape-dump-mesh-world: MeshBody " + str(n_body) + " PolygonMesh " + str(n_poly)
             + " TriangleMesh " + str(n_tri) + " arrays_present="
             + str("nodeCoordinatesAsDouble" in arrays and "normalVectorsAsDouble" in arrays))
    finally:
        tmp.close(False)
""",
    },
    {
        "id": "mesh-repair-density-default",
        "claim": "MeshRepairFeatures rebuild with density LEFT UNSET creates a feature whose density reads 128.0 (the wire's '(default 128)' is measured, not assumed), and MeshRepairFeature carries NO 'parameters' collection - density comes back off feat.density, a ModelParameter, or not at all",
        "encoded_in": "mesh_repair.py's density input description and its off-the-feature read-back; tests/unit/test_mesh_repair.py",
        "facts_on_pass": {"behavior.mesh_repair_density_default": 128.0,
                          "behavior.mesh_repair_feature_has_parameters": False},
        "body": """
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        des = adsk.fusion.Design.cast(tmp.products.itemByProductType("DesignProductType"))
        root = des.rootComponent
        bf = root.features.baseFeatures.add()
        bf.startEdit()
        root.meshBodies.addByTriangleMeshData(
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            [0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3], [], [])
        bf.finishEdit()
        mb = root.meshBodies.item(0)
        feats = root.features.meshRepairFeatures
        inp = feats.createInput(mb)
        inp.meshRepairType = adsk.fusion.MeshRepairTypes.RebuildMeshRepairType
        feat = feats.add(inp)
        d = feat.density
        val = d.value if hasattr(d, "value") else d
        has_params = hasattr(feat, "parameters")
        emit(abs(float(val) - 128.0) < 1e-9 and not has_params,
             "mesh-repair-density-default: density=" + repr(val)
             + " has_parameters=" + str(has_params))
    finally:
        tmp.close(False)
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
        "id": "enum-cam-machine-template",
        "claim": "MachineTemplate carries EXACTLY seven int members: GenericLathe=0 (a FALSY member, so a `not member` guard rejects the one valid lathe template), Generic3Axis=1, Generic4Axis=2, Generic5AxisHeadHead=3, Generic5AxisHeadTable=4, Generic5AxisTableTable=5, GenericFFF=6. The automatic enum sweep cannot see this family - its name ends in none of the ...Types/...States/...Modes suffixes the scrape matches - so it is pinned here or nowhere",
        "encoded_in": "cam_create_machine.py _TEMPLATES (wire value -> member name); tests/unit/test_cam_create_machine.py's MachineTemplate namespace",
        "body": """
    T = adsk.cam.MachineTemplate
    dump_enum("cam.MachineTemplate", T)
    members = [n for n in dir(T) if not n.startswith("_") and isinstance(getattr(T, n), int)]
    emit(len(members) == 7 and T.GenericLathe == 0 and T.Generic3Axis == 1
         and T.Generic4Axis == 2 and T.Generic5AxisHeadHead == 3
         and T.Generic5AxisHeadTable == 4 and T.Generic5AxisTableTable == 5
         and T.GenericFFF == 6,
         "enum-cam-machine-template: " + str(len(members)) + " members lathe="
         + str(T.GenericLathe) + " fff=" + str(T.GenericFFF))
""",
    },
    {
        "id": "enum-distance-units-collides-with-factory",
        "claim": "DistanceUnits.MillimeterDistanceUnits == 0 AND a freshly created STLExportOptions reads unitType == 0 - the mm member IS the factory value, so a set-then-read-back of unitType cannot tell an assignment that took from one that never happened, and only for that member. What the file is actually written in is a SEPARATE row this one asserts nothing about: stl-export-unittype-is-sticky-session-state (this body creates an options object and never exports). The automatic enum sweep cannot see this family - its name ends in none of the ...Types/...States/...Modes suffixes the scrape matches - so it is pinned here or nowhere",
        "encoded_in": "_export.py STL_UNIT_MEMBERS + applied_pair (the pre-read this collision forces); mesh_export.py _apply_stl_units; design_export.py _configure_export_options; tests/unit/test_mesh_export.py + test_design_export.py collision fakes",
        "body": """
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        des = adsk.fusion.Design.cast(tmp.products.itemByProductType("DesignProductType"))
        root = des.rootComponent
        U = adsk.fusion.DistanceUnits
        dump_enum("fusion.DistanceUnits", U)
        # An options object needs real geometry to be created against; 1 cm cube, never exported.
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(1.0, 1.0, 0.0))
        root.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(1.0),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        # createSTLExportOptions validates the DIRECTORY at creation (a bare filename raises
        # "3 : The selected folder does not exist"), so the path must sit in a real folder.
        import os, tempfile
        opts = des.exportManager.createSTLExportOptions(
            root, os.path.join(tempfile.gettempdir(), "unused_measure_units.stl"))
        factory = opts.unitType
        emit(U.MillimeterDistanceUnits == 0 and factory == U.MillimeterDistanceUnits
             and factory != U.InchDistanceUnits,
             "enum-distance-units-collides-with-factory: mm=" + str(U.MillimeterDistanceUnits)
             + " in=" + str(U.InchDistanceUnits)
             + " factory unitType=" + str(factory))
    finally:
        tmp.close(False)
""",
    },
    {
        "id": "enum-mesh-refinement-collides-with-factory",
        "claim": "MeshRefinementSettings.MeshRefinementMedium == 1 AND a freshly created STLExportOptions reads meshRefinement == 1 - the MEDIUM member is the factory value, and medium is mesh_export's DEFAULT refinement, so a set-then-read-back cannot bite on the most-travelled request. MeshRefinementHigh == 0 is a FALSY member, so a `not member` guard rejects the highest density. Unlike unitType this read DOES determine the written file (measured on STL and OBJ: untouched and explicit-medium byte-identical and reproducible, high and low each distinct), which is why refinement publishes no verification flag. The automatic enum sweep cannot see this family - its name ends in none of the suffixes the scrape matches - so it is pinned here or nowhere",
        "encoded_in": "mesh_export.py _REFINEMENTS + _apply_refinement (which drops the pair's 'changed' half on the strength of this); _export.py applied_pair's per-knob note; tests/unit/test_mesh_export.py _refine_member",
        "body": """
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        des = adsk.fusion.Design.cast(tmp.products.itemByProductType("DesignProductType"))
        root = des.rootComponent
        R = adsk.fusion.MeshRefinementSettings
        dump_enum("fusion.MeshRefinementSettings", R)
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(1.0, 1.0, 0.0))
        root.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(1.0),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        import os, tempfile
        opts = des.exportManager.createSTLExportOptions(
            root, os.path.join(tempfile.gettempdir(), "unused_measure_refinement.stl"))
        factory = opts.meshRefinement
        emit(R.MeshRefinementMedium == 1 and R.MeshRefinementHigh == 0
             and factory == R.MeshRefinementMedium,
             "enum-mesh-refinement-collides-with-factory: medium="
             + str(R.MeshRefinementMedium) + " high=" + str(R.MeshRefinementHigh)
             + " low=" + str(R.MeshRefinementLow)
             + " factory meshRefinement=" + str(factory))
    finally:
        tmp.close(False)
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
    counts.append(dump_shape("Matrix3D", adsk.core.Matrix3D.create()))
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
    emit(len(counts) == 21 and all(c > 0 for c in counts),
         "shape-dump-design-world: " + str(len(counts)) + " types, min attrs " + str(min(counts)))
""",
    },
    {
        "id": "matrix3d-invert-singular-answers-true-and-corrupts",
        "claim": ("Matrix3D.invert() on a SINGULAR matrix returns True and leaves the matrix "
                  "corrupted (nan/inf entries) - the False return every careful caller gates on "
                  "is a decline the singular case never produces, so the gate is defensive, not "
                  "a singularity detector; rigid occurrence transforms cannot be singular, which "
                  "is why the callers' math stays sound"),
        "encoded_in": ("model_inspect._measuring_axes and model_hole._world_lift invert() gates; "
                       "tests/conftest.py FakeMatrix3D invertible=False contract"),
        "body": """
    import math
    m = adsk.core.Matrix3D.create()
    ok_set = m.setWithArray([1.0, 0.0, 0.0, 2.0,
                             0.0, 1.0, 0.0, 3.0,
                             0.0, 0.0, 0.0, 1.0,
                             0.0, 0.0, 0.0, 1.0])
    before = list(m.asArray())
    r = m.invert()
    after = list(m.asArray())
    corrupted = (before != after) and any(
        (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) for v in after)
    emit(bool(ok_set) and r is True and corrupted,
         "matrix3d-invert-singular-answers-true-and-corrupts: setWithArray=" + str(ok_set)
         + " invert_returned=" + str(r) + " corrupted_to_nan_inf=" + str(corrupted))
""",
    },
    {
        "id": "closed-document-wrapper-reads",
        "claim": ("Two never-saved documents both answer name 'Untitled' (nothing disambiguates "
                  "them), their root components answer BYTE-IDENTICAL entityTokens, and Document "
                  "itself exposes no entityToken; a CLOSED document's held wrapper still reads "
                  "isValid False and compares == False against a live document without raising - "
                  "the exact reads _write_guard.document_key prunes and matches by"),
        "encoded_in": ("_write_guard.py document_key / prune_closed_documents docstrings; "
                       "tests/unit/test_view_set.py _DocWrapper + _SHARED_ROOT_TOKEN; "
                       "cam_generate.py same-name wire note"),
        "body": """
    DT = adsk.core.DocumentTypes.FusionDesignDocumentType
    d1 = app.documents.add(DT)
    d2 = app.documents.add(DT)
    try:
        same_name = (d1.name == d2.name)
        t1 = adsk.fusion.FusionDocument.cast(d1).design.rootComponent.entityToken
        t2 = adsk.fusion.FusionDocument.cast(d2).design.rootComponent.entityToken
        no_ent = not hasattr(d1, "entityToken")
        d1.close(False)
        v = d1.isValid
        eq = (d1 == app.activeDocument)
        emit(same_name and t1 == t2 and no_ent and v is False and eq is False,
             "closed-document-wrapper-reads: names_same=" + str(same_name)
             + " root_tokens_identical=" + str(t1 == t2) + " token=" + repr(t1)
             + " document_has_entityToken=" + str(not no_ent)
             + " closed_isValid=" + repr(v) + " closed_eq_active=" + repr(eq))
    finally:
        try:
            d2.close(False)
        except Exception:
            pass
""",
    },
    {
        "id": "closed-document-name-raises",
        "claim": ("Reading .name on a CLOSED document's held wrapper raises RuntimeError "
                  "'An API Object refers to a deleted Object' - and the raise can escape "
                  "try/except and abort the script (catchability varies by session)"),
        "encoded_in": ("_write_guard.py name-read comment in document_key; "
                       "tests/unit/test_view_set.py _DocWrapper raises contract"),
        "expect": "raise_or_abort",
        "body": """
    DT = adsk.core.DocumentTypes.FusionDesignDocumentType
    d1 = app.documents.add(DT)
    d1.close(False)
    try:
        n = d1.name
        emit(False, "closed-document-name-raises: answered " + repr(n) + " with no raise")
    except Exception as e:
        emit(type(e).__name__ == "RuntimeError" and "deleted Object" in str(e),
             "closed-document-name-raises: raised catchably " + type(e).__name__
             + ": " + str(e)[:60])
""",
    },
    {
        "id": "sketchtext-heightparameter-settable-geometry-follows",
        "claim": ("SketchText.heightParameter.value accepts a WRITE (0.8 set to 0.4 reads back "
                  "0.4) and the glyph geometry FOLLOWS proportionally (the boundingBox width "
                  "halves with the height) - so an edit-path resize / fit-to-width is "
                  "measure-and-rescale on the live text, not a delete-and-recreate"),
        "encoded_in": ("sketch_set_text's edit path (the _CREATE_ONLY contract this measurement "
                       "unblocks rewording); _sketch_detail's heightParameter read"),
        "body": """
    root = des.rootComponent
    sk = root.sketches.add(root.xYConstructionPlane)
    ipt = sk.sketchTexts.createInput2("Fit Me", 0.8)
    placed = ipt.setAsMultiLine(
        adsk.core.Point3D.create(0.0, 0.0, 0.0),
        adsk.core.Point3D.create(4.8, 0.8, 0.0),
        adsk.core.HorizontalAlignments.LeftHorizontalAlignment,
        adsk.core.VerticalAlignments.BottomVerticalAlignment, 0)
    st = sk.sketchTexts.add(ipt)
    w_before = st.boundingBox.maxPoint.x - st.boundingBox.minPoint.x
    st.heightParameter.value = 0.4
    v = st.heightParameter.value
    w_after = st.boundingBox.maxPoint.x - st.boundingBox.minPoint.x
    ratio = (w_after / w_before) if w_before else 0.0
    emit(bool(placed) and abs(v - 0.4) < 1e-9 and abs(ratio - 0.5) < 0.02,
         "sketchtext-heightparameter-settable-geometry-follows: height 0.8->set 0.4 reads "
         + str(v) + " width " + str(round(w_before, 4)) + "->" + str(round(w_after, 4))
         + " ratio " + str(round(ratio, 4)) + " (expect ~0.5)")
""",
    },
    {
        "id": "stl-export-unittype-is-sticky-session-state",
        "claim": ("The unit an STL export lands in is STICKY SESSION STATE, not a property of the "
                  "document and not readable anywhere: an export that leaves unitType UNTOUCHED "
                  "writes the unit of the LAST EXPLICIT unitType assignment made in the Fusion "
                  "session, and that carries ACROSS DOCUMENTS (a brand-new document inherits it). "
                  "unitType reads 0 BEFORE every assignment regardless of what the file will be "
                  "written in, so no read of a fresh options object names the unit. The read-BACK "
                  "after an assignment is a different read and it DOES return the assigned member "
                  "(inch reads 3, cm reads 1), which is what makes applied_pair's post-read gate "
                  "able to confirm a non-mm request - only the mm member collides with the "
                  "factory 0. NOT measured by this row's legs, and recorded as an OBSERVATION "
                  "rather than an assertion, because a script cannot restart Fusion to test it: "
                  "the value read mm on the first export after a restart, which is how this "
                  "masquerades as 'the document's units' or as a fixed inch default depending on "
                  "what ran earlier in the session. Measured on a 1 cm cube: explicit "
                  "mm -> 10.0, untouched after it -> 10.0, explicit inch -> 0.393701, untouched "
                  "after it -> 0.393701. Both files are the same BYTE LENGTH (a binary STL of a "
                  "fixed triangle count always is), so a size comparison cannot tell them apart "
                  "while every coordinate differs by 25.4x. Consequence: a writer that does not "
                  "SET unitType inherits the unit from an unrelated earlier export"),
        "encoded_in": ("_export.STL_UNIT_MEMBERS / stl_unit_enum and every STL writer that bakes "
                       "unitType; mesh_export's stl_units note"),
        "need_box": True,
        "body": """
    import struct, tempfile, os
    um = des.unitsManager
    em = des.exportManager
    mm = um.convert(1.0, "cm", "mm")
    inch = um.convert(1.0, "cm", "in")

    # Every leg SETS before it observes, so the row proves the stickiness rather than inheriting
    # it: a row that merely read the untouched export would report whatever an earlier export in
    # this Fusion session happened to leave behind, and would pass or fail by luck of ordering.
    # Both reads are captured because they answer DIFFERENT questions: the read on a fresh options
    # object never names the unit the file will get, while the read-BACK after an assignment does
    # return the member assigned - which is what lets applied_pair's post-read gate confirm a
    # non-mm request. Reporting only the first would read as a claim about both.
    backreads = []

    def leg(label, unit_member):
        p = os.path.join(tempfile.gettempdir(), "measure_sticky_" + label + ".stl")
        o = em.createSTLExportOptions(body, p)
        try:
            r = o.unitType
        except Exception as exc:
            r = "unreadable(" + type(exc).__name__ + ")"
        if unit_member is not None:
            o.unitType = unit_member
            backreads.append((label, unit_member, safe_read(o)))
        em.execute(o)
        raw = open(p, "rb").read()
        os.remove(p)
        tris = struct.unpack("<I", raw[80:84])[0]
        big = 0.0
        pos = 84
        for _ in range(tris):
            vals = struct.unpack("<12fH", raw[pos:pos+50])
            pos += 50
            for v in vals[3:12]:
                if abs(v) > big:
                    big = abs(v)
        return r, big, len(raw)

    def safe_read(o):
        try:
            return o.unitType
        except Exception as exc:
            return "unreadable(" + type(exc).__name__ + ")"

    U = adsk.fusion.DistanceUnits
    r1, set_mm, size_mm = leg("set-mm", U.MillimeterDistanceUnits)
    r2, after_mm, _ = leg("untouched-after-mm", None)
    r3, set_in, size_in = leg("set-inch", U.InchDistanceUnits)
    r4, after_in, _ = leg("untouched-after-inch", None)
    # Left at mm deliberately: this row MUTATES session state every other STL export inherits, so
    # it restores the post-restart default rather than leaving the session on inches.
    leg("restore-mm", U.MillimeterDistanceUnits)

    follows = abs(after_mm - mm) < 1e-3 and abs(after_in - inch) < 1e-3
    assigns = abs(set_mm - mm) < 1e-3 and abs(set_in - inch) < 1e-3
    reads_zero = r1 == 0 and r2 == 0 and r3 == 0 and r4 == 0
    # The read-BACK returns what was assigned - the inch leg is the one that matters, since mm
    # collides with the factory 0 and so proves nothing on its own.
    backs_match = all(got == want for _lbl, want, got in backreads)
    inch_back = [got for lbl, _w, got in backreads if lbl == "set-inch"]
    emit(follows and assigns and reads_zero and backs_match and size_mm == size_in,
         "stl-export-unittype-is-sticky-session-state:"
         + " set_mm=" + str(round(set_mm, 6)) + " untouched_after_mm=" + str(round(after_mm, 6))
         + " (both expect " + str(round(mm, 6)) + ")"
         + " set_inch=" + str(round(set_in, 6)) + " untouched_after_inch=" + str(round(after_in, 6))
         + " (both expect " + str(round(inch, 6)) + ")"
         + " reads_before_assign=" + str([r1, r2, r3, r4]) + " (all expect 0)"
         + " read_back_after_assign_matches=" + str(backs_match)
         + " inch_reads_back=" + str(inch_back) + " (expect [3], NOT 0)"
         + " same_byte_length=" + str(size_mm == size_in) + " at " + str(size_mm))
""",
    },
    {
        "id": "stl-unittype-read-poisoned-by-units-toggle",
        "claim": ("Assigning Design.fusionUnitsManager.distanceDisplayUnits POISONS "
                  "STLExportOptions.unitType's READ for that document: the read then raises "
                  "RuntimeError '3 : unexpected document units' and does NOT recover when the "
                  "display units are put back to what they were. A document that was never "
                  "toggled reads 0. So unitType's read can fail outright, not merely mislead - "
                  "any code reading it needs safe(), and no writer may infer the file's unit "
                  "from it"),
        "encoded_in": ("_export.applied_pair's unitType handling and mesh_export's "
                       "_apply_stl_unit read-back"),
        "body": """
    # Its OWN document, closed at the end: the toggle is not reversible within a document, so
    # doing it in the runner's shared document would poison every later row that reads unitType.
    import tempfile, os
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        d2 = adsk.fusion.Design.cast(app.activeProduct)
        b = make_box(d2, "PoisonProbe")
        em = d2.exportManager
        path = os.path.join(tempfile.gettempdir(), "measure_poison.stl")
        before = em.createSTLExportOptions(b, path).unitType
        um = d2.fusionUnitsManager
        was = um.distanceDisplayUnits
        um.distanceDisplayUnits = adsk.fusion.DistanceUnits.InchDistanceUnits
        um.distanceDisplayUnits = was
        restored = um.distanceDisplayUnits == was
        try:
            after = em.createSTLExportOptions(b, path).unitType
            raised = ""
        except Exception as exc:
            after = None
            raised = type(exc).__name__ + ": " + str(exc)[:80]
        emit(before == 0 and after is None and "unexpected document units" in raised
             and restored,
             "stl-unittype-read-poisoned-by-units-toggle: read_before_toggle=" + str(before)
             + " (expect 0) display_units_restored=" + str(restored)
             + " read_after_restore=" + (repr(raised) if raised else str(after))
             + " (expect a raise naming unexpected document units)")
    finally:
        doc.close(False)
""",
    },
    {
        "id": "cam-machine-library-deleteasset",
        "claim": ("machineLibrary.importMachine stores a loaded machine into the Local location "
                  "under a new name and machineAtURL loads it back; the stored asset's "
                  "URL.leafName carries the FILE EXTENSION ('MeasureDeleteMe.mch') while the "
                  "machine name does not - the shape the delete matcher's stem compare exists "
                  "for; deleteAsset(url) then returns True and a re-walk of childAssetURLs no "
                  "longer lists the asset. Self-cleaning: the machine this row imports is the "
                  "one it deletes"),
        "encoded_in": ("cam_create_machine.py handler importMachine/machineAtURL gates + "
                       "cam_delete_machine.py handler deleteAsset read-backs"),
        "body": """
    libs = adsk.cam.CAMManager.get().libraryManager
    lib = libs.machineLibrary
    f360 = adsk.cam.LibraryLocations.Fusion360LibraryLocation
    local_loc = adsk.cam.LibraryLocations.LocalLibraryLocation
    local = lib.urlByLocation(local_loc)
    src = (lib.createQuery(f360, "", "").execute() or [None])[0]
    if src is None:
        emit(False, "cam-machine-library-deleteasset: no bundled machine to import - inconclusive")
        return
    url = lib.importMachine(src, local, "MeasureDeleteMe")
    stored = bool(url)
    leaf = url.leafName if stored else ""
    leaf_has_ext = leaf == "MeasureDeleteMe.mch"
    loaded = stored and (lib.machineAtURL(url) is not None)
    ok = stored and lib.deleteAsset(url)
    still = stored and any(
        u.leafName == leaf for u in lib.childAssetURLs(local))
    emit(stored and leaf_has_ext and loaded and ok is True and not still,
         "cam-machine-library-deleteasset: stored=" + str(stored) + " leafName=" + repr(leaf)
         + " (expect the .mch extension) loaded_back=" + str(loaded)
         + " deleteAsset=" + str(ok) + " still_listed=" + str(still))
""",
    },
    {
        "id": "cam-machine-query-keyed-on-model",
        "claim": ("The machine-library query is keyed on vendor/model and CANNOT reach a stored "
                  "machine by a description that is not its model - query by the description "
                  "returns 0 hits while query by the model returns the machine and the full local "
                  "walk contains it. A post-delete re-resolve by such a label therefore proves "
                  "nothing; the asset walk is the load-bearing read-back. Self-cleaning: the "
                  "machine this row imports is the one it deletes"),
        "encoded_in": ("cam_delete_machine.py handler's no-evidence sentence and point-of-use "
                       "comment; cam_create_machine.py's create side writing the name onto model"),
        "body": """
    libs = adsk.cam.CAMManager.get().libraryManager
    lib = libs.machineLibrary
    f360 = adsk.cam.LibraryLocations.Fusion360LibraryLocation
    local_loc = adsk.cam.LibraryLocations.LocalLibraryLocation
    local = lib.urlByLocation(local_loc)
    src = (lib.createQuery(f360, "", "").execute() or [None])[0]
    if src is None:
        emit(False, "cam-machine-query-keyed-on-model: no bundled machine - inconclusive")
        return
    src.description = "MeasureQKey"
    # model/vendor left as the source's: the description is deliberately NOT the model
    url = lib.importMachine(src, local, "MeasureQKey")
    try:
        stored = lib.machineAtURL(url)
        by_desc = len(lib.createQuery(local_loc, "", "MeasureQKey").execute() or [])
        by_model = lib.createQuery(local_loc, "", stored.model).execute() or []
        model_reaches = any(m.description == "MeasureQKey" for m in by_model)
        walk_has = any(m.description == "MeasureQKey"
                       for m in (lib.createQuery(local_loc, "", "").execute() or []))
        emit(by_desc == 0 and model_reaches and walk_has,
             "cam-machine-query-keyed-on-model: by_description_hits=" + str(by_desc)
             + " (expect 0) by_model_reaches=" + str(model_reaches)
             + " full_walk_contains=" + str(walk_has))
    finally:
        lib.deleteAsset(url)
""",
    },
    {
        "id": "construction-offset-value-reads-signed-cm",
        "claim": ("A ConstructionPlaneOffsetDefinition's offset ModelParameter .value reads the "
                  "requested offset in SIGNED internal cm (-12 mm requested reads -1.2) - the "
                  "read a construction-offset read-back compare gates on"),
        "encoded_in": ("model_construction's offset read-back compare; the MODEL-2 measurement "
                       "trail (the on_path form stores distance and offset as separate "
                       "parameters)"),
        "body": """
    root = des.rootComponent
    pi = root.constructionPlanes.createInput()
    ok_set = pi.setByOffset(root.xYConstructionPlane, adsk.core.ValueInput.createByReal(-1.2))
    plane = root.constructionPlanes.add(pi)
    d = plane.definition
    v = d.offset.value
    emit(bool(ok_set) and type(d).__name__ == "ConstructionPlaneOffsetDefinition"
         and abs(v + 1.2) < 1e-9,
         "construction-offset-value-reads-signed-cm: definition=" + type(d).__name__
         + " offset.value=" + repr(v) + " (expect -1.2 for a -12 mm request)")
""",
    },
    {
        "id": "shape-dump-torus",
        "claim": "core.Torus - the surface a toroidal BRepFace carries - exposes origin (the torus CENTRE), axis, majorRadius, minorRadius and copy(). origin is the point the torus keypoint gate transforms into world and compares a JointGeometry against, and copy() is what lets it do that without moving the live surface",
        "encoded_in": "tests/unit/test_joint_at_geometry.py's torus-face fakes (face.geometry.origin); _joints.py's torus keypoint gate; _holder.py's Torus.cast reads",
        "body": """
    t = adsk.core.Torus.create(
        adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Vector3D.create(0.0, 0.0, 1.0),
        1.0, 0.25)
    n = dump_shape("Torus", t)
    names = [x for x in dir(t) if not x.startswith("_")]
    emit(n > 0 and "origin" in names and "axis" in names and "majorRadius" in names
         and "minorRadius" in names and "copy" in names,
         "shape-dump-torus: " + str(n) + " attrs origin="
         + repr((t.origin.x, t.origin.y, t.origin.z))
         + " major=" + repr(t.majorRadius) + " minor=" + repr(t.minorRadius))
""",
    },
    {
        "id": "fillet-tangent-chain-loop-faces",
        "claim": "A fillet driven from ONE edge of an 8-edge tangent top loop (4 lines + 4 arcs, isTangentChain=True) lands FilletFeature.faces.count == 8 - the chain expands across every tangent neighbour, so the number of edges HANDED IN predicts nothing about what got filleted",
        "encoded_in": "model_fillet_chamfer.py's tangent-chain wording and its off-the-feature face read-back",
        "need_box": True,
        "body": """
    root = des.rootComponent
    bname = body.name
    verticals = adsk.core.ObjectCollection.create()
    for i in range(body.edges.count):
        e = body.edges.item(i)
        g = e.geometry
        if type(g).__name__ != "Line3D":
            continue
        d = g.startPoint.vectorTo(g.endPoint)
        if abs(d.x) < 1e-9 and abs(d.y) < 1e-9 and abs(d.z) > 1e-9:
            verticals.add(e)
    fi = root.features.filletFeatures.createInput()
    fi.addConstantRadiusEdgeSet(verticals, adsk.core.ValueInput.createByReal(0.2), False)
    root.features.filletFeatures.add(fi)
    b = root.bRepBodies.itemByName(bname)
    top = None
    for i in range(b.faces.count):
        f = b.faces.item(i)
        if type(f.geometry).__name__ == "Plane" and f.geometry.normal.z > 0.9:
            top = f
    loop_edges = top.edges.count
    seed = adsk.core.ObjectCollection.create()
    seed.add(top.edges.item(0))
    fi2 = root.features.filletFeatures.createInput()
    fi2.addConstantRadiusEdgeSet(seed, adsk.core.ValueInput.createByReal(0.1), True)
    feat = root.features.filletFeatures.add(fi2)
    emit(loop_edges == 8 and feat.faces.count == 8,
         "fillet-tangent-chain-loop-faces: verticals=" + str(verticals.count)
         + " top loop edges=" + str(loop_edges)
         + " seeded=1 fillet faces=" + str(feat.faces.count))
""",
    },
    {
        "id": "fillet-feature-has-no-edges",
        "claim": "FilletFeature exposes NO 'edges' member: it is absent from dir() and reading it raises AttributeError, so the edges a fillet consumed cannot be read back off the feature - only its faces can",
        "encoded_in": "model_fillet_chamfer.py's face-based read-back (the reason a fillet payload never names the filleted edges)",
        "need_box": True,
        "facts_on_pass": {"behavior.fillet_feature_has_edges": False},
        "body": """
    root = des.rootComponent
    seed = adsk.core.ObjectCollection.create()
    seed.add(body.edges.item(0))
    fi = root.features.filletFeatures.createInput()
    fi.addConstantRadiusEdgeSet(seed, adsk.core.ValueInput.createByReal(0.1), False)
    feat = root.features.filletFeatures.add(fi)
    listed = "edges" in [n for n in dir(feat) if not n.startswith("_")]
    try:
        feat.edges
        read = "no raise"
    except AttributeError:
        read = "AttributeError"
    except Exception as e:
        read = type(e).__name__
    emit((not listed) and read == "AttributeError",
         "fillet-feature-has-no-edges: in_dir=" + str(listed) + " read=" + read
         + " faces=" + str(feat.faces.count))
""",
    },
    {
        "id": "thread-designation-multi-type-identity",
        "claim": "A thread DESIGNATION carried by several thread types is the SAME thread in each: M5x0.8, M10x1.5 and M6x1 are each carried by three metric types (ANSI Metric M Profile / GB Metric profile / ISO Metric profile) and every geometric scalar - majorDiameter, minorDiameter, pitchDiameter, threadPitch, threadAngle - is equal across the carriers at a shared class, differing only in threadType itself; 1/4-20 UNC has exactly ONE carrier. This is what makes _threads.resolve_thread_info's library-order first pick safe instead of an ambiguity it must refuse",
        "encoded_in": "_threads.py resolve_thread_info's first-pick comment; model_hole tap / model_thread wire prose; tests/unit/test_model_thread.py's thread-table fakes",
        "facts_on_pass": {"behavior.thread_same_designation_types_identical": True},
        "body": """
    tf = des.rootComponent.features.threadFeatures
    tdq = tf.threadDataQuery

    def carriers(desig):
        hits = []
        for t in tdq.allThreadTypes:
            for s in tdq.allSizes(t):
                if desig in tdq.allDesignations(t, s):
                    hits.append(t)
                    break
        return hits

    def scalars(ti):
        return (round(ti.majorDiameter, 6), round(ti.minorDiameter, 6),
                round(ti.pitchDiameter, 6), round(ti.threadPitch, 6),
                round(ti.threadAngle, 6))

    same = True
    detail = []
    for desig in ("M5x0.8", "M10x1.5", "M6x1"):
        hits = carriers(desig)
        shared = set()
        for t in hits:
            cls = set(tdq.allClasses(False, t, desig))
            shared = cls if not shared else (shared & cls)
        if len(hits) != 3 or not shared:
            same = False
            detail.append(desig + ": " + str(len(hits)) + " types, shared classes "
                          + str(len(shared)))
            continue
        pick = sorted(shared)[0]
        vals = set()
        for t in hits:
            vals.add(scalars(tf.createThreadInfo(False, t, desig, pick)))
        same = same and len(vals) == 1
        detail.append(desig + ": 3 types class=" + pick + " distinct scalar sets="
                      + str(len(vals)))
    unc = carriers("1/4-20 UNC")
    emit(same and len(unc) == 1,
         "thread-designation-multi-type-identity: " + "; ".join(detail)
         + "; 1/4-20 UNC carriers=" + str(len(unc)))
""",
    },
    {
        "id": "min-distance-parallel-planes-is-plane-separation",
        "claim": ("measureMinimumDistance between two PARALLEL PLANAR faces that do not overlap "
                  "laterally returns the separation between their PLANES, not the minimum between "
                  "the bounded faces (2.0 cm reported where the faces' nearest points are "
                  "sqrt(13) cm apart); a Point3D lying on a planar face's plane but outside its "
                  "boundary reads 0.0 from that face; a PERPENDICULAR pair and a COPLANAR pair "
                  "both return the bounded minimum. Plus the two co-space facts the correction "
                  "rests on: a face's boundingBox brackets its own plane origin along the normal "
                  "axis, and that holds for assembly-context PROXY faces of DIFFERENT occurrences, "
                  "whose plane separation reproduces the API's own number"),
        "encoded_in": ("_geom.py parallel_plane_facts (the bounded-gap measure and its read-back "
                       "gate) and _comparable_boxes (the one-space precondition); test__geom.py "
                       "TestParallelPlaneFacts + TestBoxComparabilityPrecondition; the "
                       "parallel-pair tests in test_model_measure_between.py and "
                       "test_model_measure_relation.py"),
        "body": """
    root = des.rootComponent
    mm = app.measureManager

    def plate(x0, x1, height, name):
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(x0, 0.0, 0.0), adsk.core.Point3D.create(x1, 1.0, 0.0))
        ext = root.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(height),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        b = ext.bodies.item(0)
        b.name = name
        return b

    def cube(comp, size):
        sk = comp.sketches.add(comp.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(size, size, 0.0))
        comp.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(size),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    def face_where(body, axis, coord):
        # Sign-agnostic, like lowest_planar and brackets below. Measured on 2705.1.4 on this rig:
        # the four SIDE faces read OUTWARD normals (x=4 reads -1, x=5 reads +1), while the two
        # faces parallel to the originating SKETCH PLANE both read that plane's normal (0,0,+1),
        # the bottom one included. Only the AXIS the normal runs along holds in both cases. The
        # plane origin's coordinate on that axis is what picks between the two parallel faces,
        # since every point of a plane x = k has x = k; the sign never carried the selection.
        for i in range(body.faces.count):
            f = body.faces.item(i)
            g = f.geometry
            if g.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
                continue
            if abs(abs(getattr(g.normal, axis)) - 1.0) < 1e-9 and abs(getattr(g.origin, axis) - coord) < 1e-9:
                return f
        return None

    def lowest_planar(body):
        pick = None
        for i in range(body.faces.count):
            f = body.faces.item(i)
            g = f.geometry
            if g.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType or abs(g.normal.z) < 0.5:
                continue
            if pick is None or g.origin.z < pick.geometry.origin.z:
                pick = f
        return pick

    def brackets(face):
        # The co-space test the bounded measure needs: a face's own box must contain its own plane
        # origin along the normal axis, or the two reads are not in one frame.
        g = face.geometry
        bb = face.boundingBox
        axis = "x" if abs(g.normal.x) > 0.5 else ("y" if abs(g.normal.y) > 0.5 else "z")
        lo = getattr(bb.minPoint, axis)
        hi = getattr(bb.maxPoint, axis)
        o = getattr(g.origin, axis)
        return lo - 1e-6 <= o <= hi + 1e-6

    # Plate A spans x 0..1, z 0..1; B spans x 4..5, z 0..3; C spans x 8..9, z 0..1. A's and B's TOP
    # faces sit on parallel planes 2 cm apart and 3 cm apart along x, so the bounded minimum is
    # sqrt(3^2 + 2^2) = 3.6056. A's and C's tops are COPLANAR 7 cm apart - the case the API gets
    # RIGHT, so a probe of coplanar faces alone REFUTES this defect.
    a = plate(0.0, 1.0, 1.0, "MeasPlateA")
    b = plate(4.0, 5.0, 3.0, "MeasPlateB")
    c = plate(8.0, 9.0, 1.0, "MeasPlateC")
    top_a = face_where(a, "z", 1.0)
    top_b = face_where(b, "z", 3.0)
    top_c = face_where(c, "z", 1.0)
    side_b = face_where(b, "x", 4.0)
    if top_a is None or top_b is None or top_c is None or side_b is None:
        emit(False, "face pick failed: a=" + repr(top_a is not None) + " b=" + repr(top_b is not None)
             + " c=" + repr(top_c is not None) + " side=" + repr(side_b is not None))
    else:
        par = mm.measureMinimumDistance(top_a, top_b).value
        perp = mm.measureMinimumDistance(top_a, side_b).value
        cop = mm.measureMinimumDistance(top_a, top_c).value
        pt = mm.measureMinimumDistance(adsk.core.Point3D.create(10.0, 0.5, 1.0), top_a).value
        emit(abs(par - 2.0) < 1e-6, "parallel pair: API " + str(par) + " = plane separation 2.0, "
             "bounded minimum " + str((13.0) ** 0.5))
        emit(abs(perp - 3.0) < 1e-6, "perpendicular pair: API " + str(perp) + " = bounded (3.0)")
        emit(abs(cop - 7.0) < 1e-6, "COPLANAR pair: API " + str(cop) + " = bounded (7.0)")
        emit(abs(pt) < 1e-6, "point on the plane 9 cm outside the boundary: " + str(pt) + " (0.0)")
        emit(brackets(top_a), "native face: its box brackets its own plane origin")

    m1 = adsk.core.Matrix3D.create()
    o1 = root.occurrences.addNewComponent(m1)
    m2 = adsk.core.Matrix3D.create()
    m2.translation = adsk.core.Vector3D.create(10.0, 4.0, 2.0)
    o2 = root.occurrences.addNewComponent(m2)
    cube(o1.component, 2.0)
    cube(o2.component, 2.0)
    p1 = lowest_planar(o1.bRepBodies.item(0))
    p2 = lowest_planar(o2.bRepBodies.item(0))
    if p1 is None or p2 is None:
        emit(False, "proxy face pick failed")
    else:
        sep = abs(p2.geometry.origin.z - p1.geometry.origin.z)
        api = mm.measureMinimumDistance(p1, p2).value
        emit(brackets(p1) and brackets(p2),
             "proxy faces of DIFFERENT occurrences: each box brackets its own plane origin")
        emit(abs(api - sep) < 1e-6, "two proxies: plane separation " + str(sep)
             + " reproduces the API's " + str(api) + " - one coordinate space")
""",
    },
    {
        "id": "min-distance-position-order-parallel-faces",
        "claim": ("MeasureResults.positionOne is documented as the point on the FIRST entity, and "
                  "for two PARALLEL PLANAR faces it comes back on the SECOND: measured on a 1 cm "
                  "box, a = top (z=1) and b = bottom (z=0) reads positionOne z=0 and positionTwo "
                  "z=1, and swapping the two arguments swaps the pair with them. A NON-PARALLEL "
                  "pair that is APART keeps the documented order in BOTH argument orders, so the "
                  "order alone cannot carry the labels. The non-parallel leg is what discriminates: "
                  "a perpendicular pair of ONE body TOUCHES, and a 0-distance pair returns the same "
                  "point twice, which agrees with either order and proves nothing"),
        "encoded_in": ("model_measure_between._points_on, which labels the pair by MEASURING each "
                       "point against 'a' instead of trusting positionOne/positionTwo order; "
                       "test_model_measure_between.py TestClosestPointLabelBinding"),
        "need_box": True,
        "body": """
    root = des.rootComponent
    mm = app.measureManager

    def plate(x0, x1, height, name):
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(x0, 0.0, 0.0), adsk.core.Point3D.create(x1, 1.0, 0.0))
        ext = root.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByReal(height),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        b = ext.bodies.item(0)
        b.name = name
        return b

    def face_where(body, axis, coord):
        # Sign-agnostic: measured, the two faces parallel to the originating SKETCH PLANE both read
        # that plane's normal (0,0,+1), the BOTTOM one included - so only the AXIS the normal runs
        # along holds. The plane origin's coordinate on that axis picks between the two.
        for i in range(body.faces.count):
            f = body.faces.item(i)
            g = adsk.core.Plane.cast(f.geometry)
            if g is None:
                continue
            n = g.normal
            on_axis = abs(getattr(n, axis)) > 0.5
            if on_axis and abs(getattr(g.origin, axis) - coord) < 1e-6:
                return f
        return None

    other = plate(4.0, 5.0, 2.0, "OrderPlate")
    top = face_where(body, "z", 1.0)
    bottom = face_where(body, "z", 0.0)
    side = face_where(other, "x", 4.0)
    if top is None or bottom is None or side is None:
        emit(False, "face pick failed: top=" + repr(top is not None)
             + " bottom=" + repr(bottom is not None) + " side=" + repr(side is not None))
    else:
        # PARALLEL, 1 cm apart: the point labelled positionOne must land on the SECOND argument.
        p = mm.measureMinimumDistance(top, bottom)
        q = mm.measureMinimumDistance(bottom, top)
        emit(abs(p.positionOne.z - 0.0) < 1e-6 and abs(p.positionTwo.z - 1.0) < 1e-6,
             "parallel a=top b=bottom: positionOne z=" + str(p.positionOne.z)
             + " (expect 0.0, on b) positionTwo z=" + str(p.positionTwo.z) + " (expect 1.0, on a)")
        emit(abs(q.positionOne.z - 1.0) < 1e-6 and abs(q.positionTwo.z - 0.0) < 1e-6,
             "parallel a=bottom b=top: positionOne z=" + str(q.positionOne.z)
             + " (expect 1.0, on b) positionTwo z=" + str(q.positionTwo.z) + " (expect 0.0, on a)")
        # NON-PARALLEL and APART (3 cm along x): the documented order HOLDS in both orders. Keyed on
        # x, because both nearest points share z here and z would not tell the two faces apart.
        r = mm.measureMinimumDistance(top, side)
        s = mm.measureMinimumDistance(side, top)
        emit(abs(r.positionOne.x - 1.0) < 1e-6 and abs(r.positionTwo.x - 4.0) < 1e-6,
             "non-parallel a=top b=side: positionOne x=" + str(r.positionOne.x)
             + " (expect 1.0, on a) positionTwo x=" + str(r.positionTwo.x) + " (expect 4.0, on b)")
        emit(abs(s.positionOne.x - 4.0) < 1e-6 and abs(s.positionTwo.x - 1.0) < 1e-6,
             "non-parallel a=side b=top: positionOne x=" + str(s.positionOne.x)
             + " (expect 4.0, on a) positionTwo x=" + str(s.positionTwo.x) + " (expect 1.0, on b)")
        emit(r.value > 1e-6 and s.value > 1e-6,
             "non-parallel pair is APART: " + str(r.value) + " - a 0-distance pair returns one "
             "point twice and would agree with either order")
""",
    },
    {
        "id": "profile-centroid-component-local",
        "claim": "Profile.areaProperties().centroid reads in the profile's own COMPONENT-LOCAL frame, not world: on a component placed at x=5.0 cm, a 2.0x1.0 cm rectangle at that component's sketch origin reports centroid (1.0, 0.5, 0) - world would read 6.0. The assembly-context PROXY (Sketch.createForAssemblyContext) reports the IDENTICAL point, so a native sketch and a proxied one put their centroids in one space",
        "encoded_in": "_sketch_detail._profiles - the published 'centroid' and the composite handle's locator; _inputs._refind_profile, whose design-wide scan matches a locator centroid against this same local read; test__sketch_detail.py test_profile_centroid_scales_linearly",
        "body": """
    root = des.rootComponent
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(5.0, 0.0, 0.0)
    occ = root.occurrences.addNewComponent(m)
    placed_x = occ.transform2.translation.x
    comp = occ.component
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(0.0, 0.0, 0.0), adsk.core.Point3D.create(2.0, 1.0, 0.0))
    native = sk.profiles.item(0).areaProperties().centroid
    proxy = sk.createForAssemblyContext(occ).profiles.item(0).areaProperties().centroid
    # The placement read-back first: an occurrence that did NOT move would make (1.0, 0.5, 0) the
    # world answer too, and the row would pass without discriminating the two frames.
    placed = abs(placed_x - 5.0) < 1e-6
    local = (abs(native.x - 1.0) < 1e-6 and abs(native.y - 0.5) < 1e-6 and abs(native.z) < 1e-6)
    same = (abs(proxy.x - native.x) < 1e-6 and abs(proxy.y - native.y) < 1e-6
            and abs(proxy.z - native.z) < 1e-6)
    emit(placed and local and same, "profile-centroid-component-local: occurrence at x="
         + str(placed_x) + " native (" + str(native.x) + "," + str(native.y) + ","
         + str(native.z) + ") proxy (" + str(proxy.x) + "," + str(proxy.y) + ","
         + str(proxy.z) + ") - world would read x=6.0")
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
        "encoded_in": "_cam_read.py get_machining_time_handler comment + constants; tests/unit/test__cam_common.py",
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
    {
        "id": "cam-checktoolpath-raises-on-empty-setup",
        "claim": ("CAM.checkToolpath RAISES ('3 : The operations are not CAM objects') on a Setup "
                  "whose operations.count is 0, and returns a bool for a populated setup in the "
                  "same session - emptiness, not the document, is the discriminator"),
        "encoded_in": "cam_inspect_toolpaths.py _document_verdict/_scoped_verdict empty-setup split; tests/unit/test_cam_inspect_toolpaths.py TestEmptySetups",
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    populated = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            populated = cam.setups.item(i)
    # A fresh empty setup in the same document; removed again below so no later row sees it.
    si = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    empty = cam.setups.add(si)
    empty.name = "MeasureEmpty"
    try:
        raised = None
        try:
            cam.checkToolpath(empty)
            raised = False
        except Exception as e:
            raised = True
            msg = str(e)
        populated_answered = isinstance(cam.checkToolpath(populated), bool)
        emit(raised is True and populated_answered,
             "cam-checktoolpath-raises-on-empty-setup: empty raised=" + str(raised)
             + (" msg=" + msg[:60] if raised else "")
             + " populated_answers_bool=" + str(populated_answered))
    finally:
        empty.deleteMe()
""",
    },
    {
        "id": "cam-suppress-discards-toolpath",
        "claim": ("Setting Operation.isSuppressed True flips hasToolpath True -> False on a "
                  "generated op - suppressing DISCARDS the toolpath rather than hiding it, which is "
                  "why a suppressed op cannot post"),
        "encoded_in": "cam_inspect_toolpaths.py _split_suppressed + the three wire sentences on the ACTIVE default",
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    op = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            for x in cam.setups.item(i).allOperations:
                o = adsk.cam.Operation.cast(x)
                if o is not None and o.name == "Face2":
                    op = o
    before = op.hasToolpath
    op.isSuppressed = True
    try:
        during = op.hasToolpath
    finally:
        op.isSuppressed = False
    after_flag = op.isSuppressed
    emit(before is True and during is False and after_flag is False,
         "cam-suppress-discards-toolpath: hasToolpath before=" + str(before)
         + " suppressed=" + str(during) + " unsuppressed_flag_restored=" + str(after_flag)
         + " (the toolpath itself may need regeneration after unsuppression - not asserted)")
""",
    },
    {
        "id": "cam-errored-op-state-pair",
        "claim": ("An op whose generation FAULTS (top height below bottom height) reads hasError "
                  "True with operationState NoToolpath (3), hasToolpath False and isValid True - "
                  "hasError True beside operationState 0 was NOT observed; the same op regenerated "
                  "clean reads hasError False, operationState IsValid (0) and a toolpath"),
        "encoded_in": ("tests/unit/test__cam_common.py TestErroredOpNeverReadsValid, whose fake "
                       "carries hasError True with operationState 0; _cam_common.op_primary_state, "
                       "which classifies an errored op before it reads operationState"),
        "needs": "cam",
        "body": """
    import time as _t
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    op = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            for x in cam.setups.item(i).allOperations:
                o = adsk.cam.Operation.cast(x)
                if o is not None and o.name == "Face1":
                    op = o
    def _generate(o):
        f = cam.generateToolpath(o)
        n = 0
        while not f.isGenerationCompleted and n < 600:
            adsk.doEvents()
            _t.sleep(0.1)
            n += 1
        return f.isGenerationCompleted
    # Baseline: the same op generated clean, so the errored reads below are a DIFFERENCE, not a
    # first look at an op of unknown history.
    op.parameters.itemByName("bottomHeight_offset").expression = "0 mm"
    if not _generate(op):
        emit(False, "cam-errored-op-state-pair: baseline generation did not complete in 60s"
             " - inconclusive, rerun")
        return
    ok_base = (op.operationState == 0 and op.hasError is False and op.hasToolpath is True)
    base = ("base=(state " + str(op.operationState) + ", hasError " + repr(op.hasError)
            + ", hasToolpath " + repr(op.hasToolpath) + ")")
    # A bottom offset ABOVE the top height is a parameter fault the generator rejects.
    op.parameters.itemByName("bottomHeight_offset").expression = "50 mm"
    if not _generate(op):
        emit(False, "cam-errored-op-state-pair: fault generation did not complete in 60s"
             " - inconclusive, rerun")
        return
    lines = (op.error or "").strip().splitlines()
    ok_err = (op.hasError is True and op.operationState == 3 and op.hasToolpath is False)
    emit(ok_base and ok_err,
         "cam-errored-op-state-pair: " + base + " errored=(hasError " + repr(op.hasError)
         + ", operationState " + str(op.operationState) + ", hasToolpath " + repr(op.hasToolpath)
         + ", isValid " + repr(op.isValid) + ") error=" + repr(lines[0] if lines else ""))
""",
    },
    {
        "id": "cam-machine-spindle-max-readable",
        "claim": ("A machine's spindle maximum and axis travels are readable through the SUPPORTED "
                  "route Machine.elements -> defaultItemByType(KinematicsMachineElement."
                  "staticTypeId()) -> parts (a tree of MachinePart, each with an optional .axis / "
                  ".spindle / .toolStation): part.spindle.maxSpeed is rpm, and "
                  "part.axis.physicalRange.min/.max is CM on a LinearMachineAxisType axis. "
                  "Assigning that machine to Setup.machine needs clearSimulationModel() on the "
                  "resolved copy first whenever its hasSimulationModel reads True, and the same "
                  "numbers then read back through Setup.machine"),
        "encoded_in": ("_cam_common.kinematics_parts / machine_limits / machine_spindle_max and "
                       "tests/unit/test__cam_common.py TestMachineLimits, whose fake machine "
                       "carries 12000 rpm and 762/406/508 mm travels; the strip-then-assign path "
                       "in cam_edit_setup and tests/unit/test_cam_edit_setup.py "
                       "TestMachineStripSimulation"),
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    lib = adsk.cam.CAMManager.get().libraryManager.machineLibrary
    type_id = adsk.cam.KinematicsMachineElement.staticTypeId()

    def limits(m):
        # the supported route only - Machine.kinematics is flagged not officially supported
        el = m.elements.defaultItemByType(type_id)
        if el is None:
            found = m.elements.itemsByType(type_id)
            el = found[0] if found else None
        if el is None:
            return None, []
        parts, stack = [], [(el.parts, 0)]
        while stack:
            coll, depth = stack.pop()
            for i in range(coll.count):
                p = coll.item(i)
                parts.append(p)
                if depth < 8:
                    stack.append((p.children, depth + 1))
        rpm, axes = None, []
        for p in parts:
            if p.spindle is not None and p.spindle.maxSpeed > 0:
                rpm = p.spindle.maxSpeed if rpm is None else max(rpm, p.spindle.maxSpeed)
            if p.axis is not None:
                r = p.axis.physicalRange
                axes.append((p.axis.name,
                             p.axis.axisType == adsk.cam.MachineAxisTypes.LinearMachineAxisType,
                             r.isInfinite, r.min, r.max))
        return rpm, axes

    candidates = []
    for loc in (adsk.cam.LibraryLocations.LocalLibraryLocation,
                adsk.cam.LibraryLocations.Fusion360LibraryLocation):
        for vendor in ("Haas", ""):
            try:
                candidates.extend(lib.createQuery(loc, vendor, "").execute() or [])
            except Exception:
                pass
        if candidates:
            break
    picked, rpm, axes = None, None, []
    for m in candidates[:250]:
        rpm, axes = limits(m)
        if rpm:
            picked = m
            break
    if picked is None:
        emit(False, "cam-machine-spindle-max-readable: no library machine in "
             + str(len(candidates)) + " carried a kinematics spindle - inconclusive")
        return
    linear = [a for a in axes if a[1] and not a[2]]
    setup = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            setup = cam.setups.item(i)
    # Setup.machine refuses a machine that still carries a simulation model, so the model is
    # cleared from the RESOLVED COPY first - the one assignment path cam_edit_setup drives. The
    # kinematics comparison below is what says whether clearing it costs the numbers.
    sim = picked.hasSimulationModel
    if sim:
        picked.clearSimulationModel()
    stripped = picked.hasSimulationModel
    setup.machine = picked
    through = setup.machine        # a TRANSIENT COPY per the API doc, not the object just set
    assigned = through is not None and through.description == picked.description
    rpm2, axes2 = limits(through)
    emit(rpm > 0 and len(linear) >= 1 and stripped is False and assigned and rpm2 == rpm
         and len(axes2) == len(axes),
         "cam-machine-spindle-max-readable: " + str(picked.description) + " maxSpeed=" + str(rpm)
         + " rpm, axes=" + ", ".join(a[0] + ("(linear " + str(round((a[4] - a[3]) * 10.0, 1))
                                             + "mm)" if a[1] and not a[2] else
                                             "(infinite)" if a[2] else "(rotary)") for a in axes)
         + " hasSimulationModel " + repr(sim) + " -> " + repr(stripped)
         + " assigned=" + repr(assigned) + " through-setup maxSpeed=" + str(rpm2))
""",
    },
    {
        "id": "cam-toolpreset-per-operation",
        "claim": ("Operation.toolPreset reads the preset the operation USES (null when it runs "
                  "none), and ToolPreset.name / ToolPreset.id both read as non-empty strings on "
                  "it. With the operation's tool BOUND to a variable, that tool's preset LIST "
                  "reads: .presets answers .count as an int and .item(i).name as a string - no "
                  "tool library lookup in between"),
        "encoded_in": ("cam_get._slice_tool's active_preset + preset_names and "
                       "_cam_read._operation_summary's per-row preset; "
                       "tests/unit/test_cam_get.py TestToolSlicePresets, "
                       "tests/unit/test__cam_common.py TestOperationRowContext"),
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    op = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            op = adsk.cam.Operation.cast(cam.setups.item(i).allOperations.item(0))
    active = op.toolPreset                      # the preset it RUNS, and legitimately null
    active_name = None if active is None else active.name
    active_id = None if active is None else active.id
    # An op that runs no preset leaves nothing to read .name/.id off, so that half reports as
    # untested rather than being asserted against a null.
    names_read = (None if active is None else
                  (isinstance(active_name, str) and isinstance(active_id, str)
                   and len(active_id) > 0))
    # The LIST the op can choose from - the read cam_get publishes as preset_names. The tool is
    # BOUND to a variable for the whole read, the shape cam_get._slice_tool uses; item(0).name is
    # read because a count alone does not prove the rows read.
    tool = op.tool
    presets = tool.presets
    n = presets.count
    first = presets.item(0).name if n else None
    # The same collection off an UNBOUND temporary is NOT read here, and must not be: it raises
    # '3 : Invalid transient tool', and that raise takes the whole Python.Run command down even
    # inside try/except - the traceback comes back with no frame from this script at all, only
    # executeTextCommand's. A try/except around it does not make it observable; it makes the row
    # unrunnable. Measure the difference from OUTSIDE a script if it is ever wanted.
    emit(isinstance(n, int) and not isinstance(n, bool)
         and (n == 0 or isinstance(first, str))
         and names_read is not False,
         "cam-toolpreset-per-operation: op.toolPreset=" + repr(active_name)
         + " name/id=" + repr(names_read)
         + "; bound: count=" + repr(n) + " item0=" + repr(first)
         + ("" if active is not None else " (op runs no preset - name/id untested)")
         + ("" if n else " (tool has no presets - that half untested)"))
""",
    },
    {
        "id": "cam-op-spindle-speed-vs-machine-max",
        "claim": ("An operation's tool_spindleSpeed CAMParameter reads as a NUMBER through "
                  ".value.value, in the same rpm unit as MachineSpindle.maxSpeed: setting the "
                  "expression to 24999 reads back 24999.0. With a machine assigned, that machine's "
                  "kinematics spindle maximum reads as a number off Setup.machine, so the two "
                  "sides of the over-max comparison are both readable numbers - WHICH WAY the "
                  "comparison lands is a property of the machine in the library, not of the API, "
                  "and is reported rather than asserted"),
        "encoded_in": ("_cam_common.op_spindle_speed / spindle_check, whose "
                       "spindle_over_machine_max flag publishes the comparison as fact; "
                       "tests/unit/test__cam_common.py TestSpindleCheck"),
        "needs": "cam",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    setup, op = None, None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            setup = cam.setups.item(i)
            op = adsk.cam.Operation.cast(setup.allOperations.item(0))
    p = op.parameters.itemByName("tool_spindleSpeed")
    if p is None:
        emit(False, "cam-op-spindle-speed-vs-machine-max: the op carries no tool_spindleSpeed"
             " parameter - inconclusive")
        return
    before = p.expression
    native = p.value.value
    native_is_number = isinstance(native, float) or isinstance(native, int)
    now, rpm, has_machine = None, None, False
    try:
        p.expression = "24999"
        now = p.value.value
        # the machine's own maximum, off the SAME kinematics route the comparison uses
        m = setup.machine
        has_machine = m is not None
        if m is not None:
            el = m.elements.defaultItemByType(adsk.cam.KinematicsMachineElement.staticTypeId())
            if el is None:
                found = m.elements.itemsByType(adsk.cam.KinematicsMachineElement.staticTypeId())
                el = found[0] if found else None
            stack = [(el.parts, 0)] if el is not None else []
            while stack:
                coll, depth = stack.pop()
                for i in range(coll.count):
                    part = coll.item(i)
                    if part.spindle is not None and part.spindle.maxSpeed > 0:
                        rpm = (part.spindle.maxSpeed if rpm is None
                               else max(rpm, part.spindle.maxSpeed))
                    if depth < 8:
                        stack.append((part.children, depth + 1))
    finally:
        # the traversal above can raise; every later row reads this same operation, so the
        # 24999 this row wrote is undone whether or not the measurement completed
        p.expression = before
    reads_back = native_is_number and now is not None and abs(now - 24999.0) < 1e-6
    restored = p.expression == before
    over = None if rpm is None else (now > rpm)
    # A machine IS assigned, so its maximum has to read as a number - that, not the direction of
    # the comparison, is what makes spindle_over_machine_max computable at all. Asserting the
    # direction would assert a fact about whichever library machine got picked.
    comparable = isinstance(rpm, (int, float)) and not isinstance(rpm, bool)
    emit(reads_back and restored and (not has_machine or comparable),
         "cam-op-spindle-speed-vs-machine-max: native=" + repr(native) + " expression="
         + repr(before) + " set-24999 read .value.value=" + repr(now) + " machine max="
         + repr(rpm) + " over=" + repr(over) + " restored=" + repr(restored)
         + ("" if comparable else
            " (a machine is assigned but its kinematics carries no spindle maximum)"
            if has_machine else " (no machine assigned - the comparison half is untested)"))
""",
    },
    {
        "id": "cam-machining-time-excludes-suppressed",
        "claim": ("CAM.getMachiningTime over an ObjectCollection of NON-suppressed generated "
                  "operations returns a time, and the SAME collection with a suppressed operation "
                  "added fails ('Machining time could not be calculated') - the suppressed "
                  "operation, not an empty toolpath, is what breaks the call"),
        "encoded_in": ("_cam_read._timeable_ops / get_machining_time_handler and "
                       "tests/unit/test__cam_common.py TestMachiningTimeExcludesSuppressed"),
        "needs": "cam",
        "expect": "raise_or_abort",
        "body": """
    import time as _t
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    ops = []
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            for x in cam.setups.item(i).allOperations:
                o = adsk.cam.Operation.cast(x)
                if o is not None:
                    ops.append(o)

    def _generate(o):
        f = cam.generateToolpath(o)
        n = 0
        while not f.isGenerationCompleted and n < 600:
            adsk.doEvents()
            _t.sleep(0.1)
            n += 1
        return f.isGenerationCompleted

    for o in ops:
        o.isSuppressed = False
        if not _generate(o):
            emit(False, "cam-machining-time-excludes-suppressed: generation did not complete in"
                 " 60s - inconclusive, rerun")
            return
    clean = adsk.core.ObjectCollection.create()
    for o in ops:
        clean.add(o)
    good = cam.getMachiningTime(clean, 100.0, 10.58, 1.5).machiningTime
    emit(good > 0, "cam-machining-time-excludes-suppressed: " + str(len(ops))
         + " unsuppressed ops timed at " + str(round(good, 2)) + "s")
    # Now the misuse: the SAME operations with one suppressed. The failure arrives through
    # Fusion's text-command channel and can take the whole script invocation down, so the
    # verdict above is printed first and a script-level abort confirms the claim too. The
    # unsuppress runs in a finally: a raise from the call must not leave a parked operation
    # behind for the rest of the sweep, and neither must an abort that unwinds through here.
    ops[-1].isSuppressed = True
    try:
        mixed = adsk.core.ObjectCollection.create()
        for o in ops:
            mixed.add(o)
        try:
            bad = cam.getMachiningTime(mixed, 100.0, 10.58, 1.5).machiningTime
            emit(False, "cam-machining-time-excludes-suppressed: a suppressed op in the collection"
                 " still returned " + str(round(bad, 2)) + "s - the exclusion is unnecessary")
        except Exception as exc:
            emit(True, "cam-machining-time-excludes-suppressed: suppressed op in the collection ->"
                 " " + str(exc).strip().splitlines()[0])
    finally:
        ops[-1].isSuppressed = False
""",
    },
    {
        "id": "cam-template-library-deleteasset",
        "claim": ("templateLibrary.importTemplate stores a template built from live operations "
                  "into the Local location under a leafName whose STEM is the template's name; "
                  "templateAtURL loads it back; deleteAsset(url) returns True and a re-walk of "
                  "childAssetURLs no longer lists it - the ASSET WALK is what confirms the "
                  "delete, and it is the read cam_delete_template rests its claim on. "
                  "templateAtURL on the DELETED address is not part of that proof: it raises "
                  "rather than answering null (cam-templateaturl-raises-on-deleted-url). "
                  "Self-cleaning: the template this row imports is the one it deletes"),
        "encoded_in": ("cam_templates.py delete_template_handler read-backs and its "
                       "sibling-inference comment; save-side importTemplate gates"),
        "needs": "cam",
        "body": """
    # Each step names itself before it runs: the runner truncates a traceback's inner frame, so a
    # bare raise here reports a line number that cannot be resolved back to a step.
    stage = "start"
    try:
        stage = "resolve-cam"
        cam = adsk.cam.CAM.cast(
            app.activeDocument.products.itemByProductType("CAMProductType"))
        stage = "find-setup"
        setup = None
        for i in range(cam.setups.count):
            if cam.setups.item(i).name == "MeasureSetup":
                setup = cam.setups.item(i)
        stage = "collect-ops"
        ops = [adsk.cam.Operation.cast(x) for x in setup.allOperations]
        ops = [o for o in ops if o is not None][:1]
        stage = "createFromOperations(" + str(len(ops)) + " ops)"
        result = adsk.cam.CAMTemplate.createFromOperations(ops)
        # The live API is self-contradictory here: the docstring says a CAMTemplate returns, the
        # annotation says list[Operation] - so the return is DESCRIBED and type-GATED rather than
        # assumed. Handing importTemplate a non-CAMTemplate aborts the whole invocation, taking
        # every printed verdict with it, so the shape is proven before it is passed on.
        shape = type(result).__name__
        if isinstance(result, list):
            shape += "[" + str(len(result)) + "]"
            shape += " of " + (type(result[0]).__name__ if result else "-")
        tmpl = adsk.cam.CAMTemplate.cast(result)
        if tmpl is None and isinstance(result, list) and result:
            tmpl = adsk.cam.CAMTemplate.cast(result[0])
        if tmpl is None:
            emit(False, "cam-template-library-deleteasset: createFromOperations returned "
                 + shape + " - no CAMTemplate casts out of it, so the import is not attempted")
            return
        stage = "set-name (returned " + shape + ")"
        tmpl.name = "MeasureTmplDel"
        stage = "library"
        lib = adsk.cam.CAMManager.get().libraryManager.templateLibrary
        local = lib.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
        stage = "importTemplate"
        url = lib.importTemplate(tmpl, local)
        stored = bool(url)
        leaf = url.leafName if stored else ""
        stem = leaf.rpartition(".")[0] if "." in leaf else leaf
        stem_matches = stem == "MeasureTmplDel"
        stage = "templateAtURL"
        loaded = stored and (lib.templateAtURL(url) is not None)
        stage = "deleteAsset"
        ok = stored and lib.deleteAsset(url)
        stage = "re-walk"
        still = stored and any(u.leafName == leaf for u in lib.childAssetURLs(local))
        # templateAtURL is NOT called on the deleted url here: it raises rather than answering
        # null, and that raise aborts the whole invocation - the separate
        # cam-templateaturl-raises-on-deleted-url row proves it. The asset WALK is the read this
        # delete is confirmed by, which is the same read cam_delete_template rests its claim on.
        emit(stored and stem_matches and loaded and ok is True and not still,
             "cam-template-library-deleteasset: stored=" + str(stored) + " leaf=" + repr(leaf)
             + " stem_matches_name=" + str(stem_matches) + " loaded_back=" + str(loaded)
             + " deleteAsset=" + str(ok) + " still_listed=" + str(still))
    except Exception as exc:
        emit(False, "cam-template-library-deleteasset: RAISED at stage '" + stage + "': "
             + type(exc).__name__ + ": " + str(exc)[:160])
""",
    },
    {
        "id": "cam-templateaturl-raises-on-deleted-url",
        "claim": ("CAMTemplateLibrary.templateAtURL does NOT honour its own docstring's 'Returns "
                  "null if the specified template does not exist' for a url whose asset was just "
                  "deleted: it raises RuntimeError '3 : Given URL does not point to a template', "
                  "and the raise can escape try/except and abort the script. So a delete's "
                  "read-back cannot ask this to confirm absence - the asset WALK must - and any "
                  "caller reading it needs safe()"),
        "encoded_in": ("cam_templates.py delete_template_handler's safe()-wrapped loads_after "
                       "read and the comment naming the asset walk as the load-bearing leg"),
        "needs": "cam",
        "expect": "raise_or_abort",
        "body": """
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    setup = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            setup = cam.setups.item(i)
    ops = [adsk.cam.Operation.cast(x) for x in setup.allOperations]
    ops = [o for o in ops if o is not None][:1]
    result = adsk.cam.CAMTemplate.createFromOperations(ops)
    tmpl = adsk.cam.CAMTemplate.cast(result)
    if tmpl is None and isinstance(result, list) and result:
        tmpl = adsk.cam.CAMTemplate.cast(result[0])
    if tmpl is None:
        emit(False, "cam-templateaturl-raises-on-deleted-url: no CAMTemplate to import")
        return
    tmpl.name = "MeasureTmplRaise"
    lib = adsk.cam.CAMManager.get().libraryManager.templateLibrary
    local = lib.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
    url = lib.importTemplate(tmpl, local)
    if not url or not lib.deleteAsset(url):
        emit(False, "cam-templateaturl-raises-on-deleted-url: could not stage a deleted url")
        return
    # The misuse: the asset at this url is gone. A null return here would REFUTE the claim.
    try:
        r = lib.templateAtURL(url)
        emit(False, "cam-templateaturl-raises-on-deleted-url: returned " + repr(r)
             + " with no raise - the docstring's null contract holds after all")
    except Exception as e:
        emit(type(e).__name__ == "RuntimeError",
             "cam-templateaturl-raises-on-deleted-url: raised catchably "
             + type(e).__name__ + ": " + str(e)[:80])
""",
    },
    {
        "id": "cam-ncprogram-filtered-ops-tie-by-operationid",
        "claim": ("An adsk.cam.Operation carries NO entityToken (the read raises AttributeError) "
                  "- but an Operation from NCProgram.filteredOperations still ties to the same "
                  "operation reached through the setup walk: operationId matches and == is True "
                  "for every filtered op paired by name with its walked twin, while wrapper "
                  "identity 'is' does not carry it"),
        "encoded_in": ("_cam_read._program_posted_ops position note - filteredOperations hands "
                       "back Operations with no walk, and operationId is the measured tie a "
                       "breadcrumb beside the position discriminator would run on"),
        "needs": "cam",
        "body": """
    import time as _t
    cam = adsk.cam.CAM.cast(app.activeDocument.products.itemByProductType("CAMProductType"))
    setup = None
    for i in range(cam.setups.count):
        if cam.setups.item(i).name == "MeasureSetup":
            setup = cam.setups.item(i)
    walked = [adsk.cam.Operation.cast(x) for x in setup.allOperations]
    walked = [o for o in walked if o is not None]
    def _generate(o):
        f = cam.generateToolpath(o)
        n = 0
        while not f.isGenerationCompleted and n < 600:
            adsk.doEvents()
            _t.sleep(0.1)
            n += 1
        return f.isGenerationCompleted
    for o in walked:
        o.isSuppressed = False
        if not o.hasToolpath and not _generate(o):
            emit(False, "cam-ncprogram-filtered-ops-tie-by-operationid: generation did not"
                 " complete in 60s - inconclusive, rerun")
            return
    no_token = 0
    for o in walked:
        try:
            o.entityToken
        except AttributeError:
            no_token += 1
    nc_input = cam.ncPrograms.createInput()
    nc_input.displayName = "MeasureNC"
    # NCProgramInput.operations is a vector<OperationBase> setter: a plain Python LIST,
    # never an ObjectCollection (which raises) - same fact cam_post._operations_collection pins.
    nc_input.operations = list(walked)
    prog = cam.ncPrograms.add(nc_input)
    try:
        filtered = [adsk.cam.Operation.cast(x) for x in prog.filteredOperations]
        filtered = [o for o in filtered if o is not None]
        pairs = 0
        id_ties = 0
        eq_ties = 0
        is_ties = 0
        for f in filtered:
            for w in walked:
                if w.name == f.name:
                    pairs += 1
                    if f.operationId == w.operationId:
                        id_ties += 1
                    if f == w:
                        eq_ties += 1
                    if f is w:
                        is_ties += 1
        emit(no_token == len(walked) and pairs > 0 and id_ties == pairs and eq_ties == pairs,
             "cam-ncprogram-filtered-ops-tie-by-operationid: no-entityToken=" + str(no_token)
             + "/" + str(len(walked)) + " name-paired=" + str(pairs) + " operationId-ties="
             + str(id_ties) + " eq-ties=" + str(eq_ties) + " wrapper-is-ties=" + str(is_ties))
    finally:
        prog.deleteMe()
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
        # NOT truncated: this is a traceback, and the frame that names the defect is the LAST one.
        # The console line slices for width on its own; --json keeps the whole thing, which is the
        # only way to read why a row raised without re-running it by hand.
        return "ERROR", str(payload)
    lines = _verdict_lines(payload)
    fails = [ln[5:] for ln in lines if ln.startswith("FAIL ")]
    if fails:
        return "FAIL", "; ".join(fails)[:200]
    if not lines:
        return "ERROR", "no verdict output"
    return "PASS", "; ".join(ln[5:] for ln in lines)[:200]


# --- scratch-document bookkeeping -------------------------------------------------------------
# The run's own document is addressed by its 'open:N' index (doc_get's open_index), never by "the
# active document": measured live on 2705.1.4 - an uncaught raise inside a row script does NOT close
# a document the script had already added, the document stays open AND ACTIVE, and a bare
# doc_close then closes THAT (or whatever else came forward) instead of the scratch.

_ACTIVATE_TRIES = 10
_ACTIVATE_SLEEP = 0.2


def _open_doc_rows():
    """doc_get's 'open_documents' rows, each carrying the open_index that addresses it. An
    unreadable session yields [] - every caller treats that as "cannot address anything" and
    refuses to close, rather than falling back to the active document."""
    is_error, payload = call("doc_get", {"max_results": 200})
    if is_error or not isinstance(payload, dict):
        return []
    rows = payload.get("open_documents")
    return rows if isinstance(rows, list) else []


def _active_open_index(rows):
    """The open_index of the ACTIVE document in an open_documents list, or None."""
    for r in rows:
        if r.get("is_active"):
            return r.get("open_index")
    return None


def _stray_indices(rows, scratch_index):
    """Open indices ABOVE the scratch - documents that appeared during the run - HIGHEST FIRST.
    Closing in that order leaves the scratch's own 'open:N' address intact, since only indices
    above a closed document shift."""
    return sorted((r["open_index"] for r in rows
                   if isinstance(r.get("open_index"), int) and r["open_index"] > scratch_index),
                  reverse=True)


def _scratch_still_unsaved(rows, scratch_index):
    """True only when the document at scratch_index is present and NEVER SAVED. The scratch is
    never saved, so a saved document at that index means the index stopped addressing it and the
    close must be refused rather than aimed at a real file. doc_get prunes is_saved from a healthy
    SAVED row, so never-saved is the explicit False - a missing key is not it."""
    for r in rows:
        if r.get("open_index") == scratch_index:
            return r.get("is_saved") is False
    return False


def _reclaim_scratch(scratch_index):
    """Close every document that appeared above the scratch, then bring the scratch back to the
    foreground so the next row measures it. Returns how many strays were closed."""
    rows = _open_doc_rows()
    strays = _stray_indices(rows, scratch_index)
    for idx in strays:
        call("doc_close", {"name": "open:{0}".format(idx), "save_changes": False})
    if not strays and _active_open_index(rows) == scratch_index:
        return 0
    # doc_activate is ASYNC - it reports "pending" until the foreground catches up - so wait for the
    # switch to READ back before handing the session to the next row.
    call("doc_activate", {"name": "open:{0}".format(scratch_index)})
    for _ in range(_ACTIVATE_TRIES):
        if _active_open_index(_open_doc_rows()) == scratch_index:
            break
        time.sleep(_ACTIVATE_SLEEP)
    return len(strays)


def _close_scratch(scratch_index):
    """Close the run's own document, and nothing else."""
    _reclaim_scratch(scratch_index)
    rows = _open_doc_rows()
    if _scratch_still_unsaved(rows, scratch_index):
        call("doc_close", {"name": "open:{0}".format(scratch_index), "save_changes": False})
        return
    print("NOT closing open:{0}: it no longer reads as an unsaved document, so that index has "
          "stopped addressing this run's scratch. Close the leftover by hand.".format(scratch_index))


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
        "Each row is a CLAIM about the live adsk API, measured against a running Fusion by",
        "`measure_api.py`, beside the fakes and tool code that lean on it. PASS means the PLATFORM",
        "behaved as the claim states on that run. It does NOT mean every fake named in 'encoded in'",
        "agrees with the claim: a fixture may encode the OPPOSITE on purpose, to keep a consumer",
        "that must not depend on the real semantics under stress - camera-returns-copy names a fake",
        "modelling a shared mutable camera, and cam-alloperations-shape names two CAM test files",
        "whose encodings contradict each other. Each such cell says so in its own words, so the",
        "'encoded in' text is what tells you which kind of row you are reading.",
        "",
        "A non-PASS row means the CLAIM no longer holds: update the fakes and their consumers, then",
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


def run_measurements(write_json, only=None):
    fusion_version = _fusion_version()
    if "sys_execute_script" not in registered_tools():
        sys.exit("sys_execute_script is not registered - enable allow_execute_api_script in the "
                 "mcpServer settings and reload the add-in, then re-run.")
    is_error, payload = call("doc_new", {})
    if is_error:
        sys.exit("doc_new refused: {0}".format(payload))
    scratch = _active_open_index(_open_doc_rows())
    if scratch is None:
        sys.exit("doc_new made a document the session would not address (doc_get published no "
                 "active open_index) - refusing to measure, because the teardown could then only "
                 "close 'the active document', which is how a run closes someone else's. Close the "
                 "new Untitled document by hand and re-run.")
    results = []
    facts = {}
    shapes = {}
    cam_world = {"built": False, "err": None}
    try:
        for row in ROWS:
            if only and row["id"] not in only:
                continue
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
            strays = _reclaim_scratch(scratch)
            if strays:
                print("  {0:6} {1:28} {2}".format("", "", "reclaimed {0} document(s) the row left "
                                                  "open".format(strays)))
            time.sleep(0.1)
    finally:
        _close_scratch(scratch)
    stamp_date = time.strftime("%Y-%m-%d")
    # The ledger and live_api_facts.py describe ONE run and are written on the same condition:
    # a partial or failing run leaves both at the last complete run, so the stamp never claims
    # rows a dead script channel prevented from executing.
    if only:
        # A subset run measures a subset. Writing the ledger from it would republish the whole
        # file from the handful of rows that ran, deleting every row the filter skipped - the
        # all-PASS gate cannot see the difference between "skipped" and "absent".
        print("\n{0} and live_api_facts.py NOT rewritten - this was a --only run of {1} row(s). "
              "Run the full sweep to republish.".format(os.path.basename(LEDGER), len(results)))
    elif all(s == "PASS" for _, s, _ in results):
        write_ledger(results, fusion_version, stamp_date)
        print("\nwrote {0} (stamp: Fusion {1}, {2})".format(LEDGER, fusion_version, stamp_date))
        print("wrote {0} ({1} enum families, {2} behavior flags, {3} shaped types)".format(
            write_api_facts(facts, fusion_version, stamp_date, shapes),
            sum(1 for k in facts if k.startswith("enums.")) and len(
                {k.rsplit(".", 1)[0] for k in facts if k.startswith("enums.")}),
            sum(1 for k in facts if k.startswith("behavior.")),
            len(shapes)))
    else:
        failed = [r["id"] for r, s, _ in results if s != "PASS"]
        print("\n{0} and live_api_facts.py NOT rewritten - {1} non-PASS row(s): {2}".format(
            os.path.basename(LEDGER), len(failed), ", ".join(failed[:12])
            + (", ..." if len(failed) > 12 else "")))
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
    ap.add_argument("--only", metavar="ID", action="append",
                    help="Run only the row(s) with this id; repeatable. A row's full traceback is "
                         "truncated in the sweep's summary line, so this is how you read one. A "
                         "partial run NEVER rewrites the ledger - the all-PASS gate sees the "
                         "skipped rows as absent, not as passing.")
    args = ap.parse_args()
    sys.exit(check() if args.check
             else run_measurements(args.json, only=set(args.only) if args.only else None))
