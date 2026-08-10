# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Ratchet: bespoke fake classes in tests/unit/ do not grow - per file, shrink-only.

Most test files predate the shared fakes in conftest.py (BRepFace/BRepEdge/Plane/Cylinder/Line3D/
Circle3D/FakePoint/FakeVector3D, ...) and instead hand-roll their own - see tests/CLAUDE.md "The
legacy bespoke pattern exists in most files". A re-rolled fake drifts from the live API silently (it
is never SHAPES-swept the way a mapped conftest fake is in test_fake_shapes_exist.py), and the same
geometry shape gets reinvented file after file. This counts every class definition in
tests/unit/*.py - module-level AND nested (ast.walk), so a fake tucked inside a function or class
body counts the same as a top-level one - whose name MATCHES the fake shape: starts with Fake/_Fake,
OR shadows a conftest shared-fake name (a local redefinition instead of an import), OR matches a
live adsk type name in live_api_facts.SHAPES (e.g. a bare local ``class BRepBody:``).

The baseline is PER FILE and must match measured reality exactly, moving only downward:

- A file ABOVE its ``_PER_FILE_BASELINE`` entry - or a NEW file at any count, since a new test
  builds from conftest's shared fakes (tests/CLAUDE.md) - fails, naming the file, the delta, and
  the offending classes.
- An entry ABOVE reality (the file's count dropped, or the file went away) is STALE: the lint fails
  and prints the corrected dict ready to paste over ``_PER_FILE_BASELINE``. Locking a win in
  immediately means a deleted legacy fake in one file can never fund a new bespoke fake in another.

Cleaning up the existing counts is a dedicated migration nobody has green-lit, so the entries are
measured legacy reality, not accepted targets. The total across files is printed for information
only - it gates nothing.

Two different things move a number here, and they are kept apart:

- WIDENING THE DETECTION (counting nested classes, counting adsk-named classes) re-measures every
  file at once. That is not a growth of bespoke fakes, so ``_PER_FILE_BASELINE`` is re-pasted
  wholesale from the failure message's paste-ready dict - many entries rise in one diff, and no
  per-file reason exists to give.
- ADDING A BESPOKE FAKE to a file is a growth, and it goes in ``_RAISED``, where the reason is a
  required string the lint reads rather than a comment it cannot (the shape
  test_helper_duplication's ``_ALLOWLIST`` uses). ``_PER_FILE_BASELINE`` stays at the measured
  count, so the exemption is visible as its own line and collapses the moment the fake goes away.
"""

import ast
import os

import live_api_facts

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/lints/ -> tests/
UNIT_DIR = os.path.join(TESTS_DIR, "unit")
CONFTEST_PATH = os.path.join(TESTS_DIR, "conftest.py")

# Measured bespoke-fake class count per tests/unit file (module-level + nested). Shrink-only:
# a file may only match or drop below its entry; a stale (higher) entry fails with a paste-ready
# replacement. Files without an entry must have zero. A genuinely novel test surface goes in
# _RAISED below, never in this table.
_PER_FILE_BASELINE = {
    "test__cam_common.py": 1,
    # FakeEmbossFeatures: records createInput/add ordering with a per-call raise hook - the
    # transaction shape MakeComp's feature collections cannot express.
    "test_model_emboss.py": 1,
    "test__data_read.py": 5,
    "test__geom.py": 2,
    "test__sketch_detail.py": 17,   # widened detection: a class named like an api_surface adsk class is counted whatever its prefix
    "test__view_common.py": 3,
    "test_appearance_set.py": 11,
    "test_assembly_get.py": 5,
    # The interference pipeline (input -> results -> per-pair bodies) incl. the input variant that
    # REFUSES coincident bodies - a createInput transaction surface no conftest fake models.
    "test_assembly_inspect_interference.py": 11,
    "test_assembly_joints_advanced.py": 10,
    "test_assembly_transform.py": 8,
    "test_assert_kinds.py": 2,
    "test_cam_generate_setup_sheet.py": 2,   # the async-landing CAM fake (bool True before the file exists) conftest cannot model
    "test_cam_activate_setup.py": 1,
    "test_cam_compare.py": 7,
    "test_cam_create_setup.py": 8,
    "test_cam_delete.py": 3,
    "test_cam_edit_operation.py": 9,
    "test_cam_generate.py": 1,
    "test_cam_post.py": 1,
    "test_cam_reorder.py": 3,
    "test_cam_set_nc_comment.py": 5,
    "test_cam_show_toolpath.py": 6,
    "test_cam_templates.py": 2,
    # 1: a design whose rootComponent read returns a FRESH wrapper each time - the measured platform
    # behaviour that root_body_advisory's same-component test exists for. MakeDesign holds it as a
    # plain attribute, so the shared fake cannot express it.
    "test_common.py": 1,
    "test_data_management.py": 11,
    "test_data_switch_hub.py": 3,
    "test_design_configure.py": 4,   # widened detection: a class named like an api_surface adsk class is counted whatever its prefix
    "test_design_delete_feature.py": 4,   # widened detection: a class named like an api_surface adsk class is counted whatever its prefix
    "test_design_delete_occurrence.py": 6,
    "test_design_export.py": 15,   # FakeOptions: an *ExportOptions attribute bag with a poison .units property - no conftest fake models an options object
    "test_design_mode.py": 3,
    "test_design_ops.py": 3,
    "test_doc_insert_derive.py": 17,
    "test_doc_insert_occurrence.py": 6,
    "test_doc_lifecycle.py": 7,
    "test_doc_open.py": 1,
    "test_doc_update_xref.py": 9,
    "test_drawing_create.py": 6,
    "test_drawing_edit_sheet.py": 1,   # FakeSheet: the surface under test IS its properties - width/height derived read-only from size+orientation, size/orientation setters that raise the way Fusion does, and tidyUp, a property whose READ tidies; a namespace cannot express them
    "test_drawing_export.py": 3,
    "test_drawing_update.py": 4,
    "test_edit_joint.py": 9,
    "test_family_gating.py": 3,
    "test_find_geometry.py": 6,
    "test_inputs.py": 41,
    "test_joint_at_geometry.py": 7,   # +1 AsBuiltJoint: is_as_built_joint isinstance-checks adsk.fusion.AsBuiltJoint, a class conftest does not model
    "test_joint_create_edit.py": 4,
    "test_joint_create_origin.py": 14,
    "test_joint_drive.py": 6,
    "test_joint_motion_link.py": 6,
    "test_mesh_combine.py": 4,   # widened detection: a class named like an api_surface adsk class is counted whatever its prefix
    "test_mesh_delete.py": 5,   # meshBodies + MeshRemoveFeatures: an object graph the conftest solid-body fakes do not model
    "test_mesh_edit.py": 7,   # MeshBody/PolygonMesh/TriangleMesh are in SHAPES, so the local mesh fakes count
    "test_mesh_export.py": 17,   # same SHAPES-named mesh fakes as test_mesh_edit
    "test_mesh_ops.py": 9,   # same SHAPES-named mesh fakes; its OWN MeshBody fake is a deliberate local one
    "test_model_arrange.py": 10,
    "test_model_combine.py": 7,
    "test_model_compute_holder.py": 2,
    "test_model_construction.py": 2,
    "test_model_create_component.py": 11,
    "test_model_draft.py": 4,
    "test_model_extrude.py": 12,
    "test_model_fillet_chamfer.py": 16,   # + FakeRuleFilletInput - conftest models no fillet-input surface
    "test_design_edit_timeline.py": 6,   # the Timeline/TimelineObject/TimelineGroup(s) graph plus the entity Attributes collection and the AttributeVector findAttributes returns (len()/[i], .count raises) - no conftest fake models them, and this is the only tool that reads entity.attributes
    "test_model_hole.py": 5,
    "test_model_move.py": 3,   # MoveFeatures createInput2/define*/add graph - no conftest fake models a feature-collection input factory (bodies/faces/bounding boxes come from the shared fakes)
    "test_model_offset_face.py": 5,   # OffsetFacesFeatures createInput/add transaction graph - no conftest fake models a feature-collection input factory
    "test_model_scale.py": 3,   # ScaleFeatures createInput/setToNonUniform/add graph - no conftest fake models a feature-collection input factory (bodies/bounding boxes come from the shared fakes)
    "test_model_pattern.py": 9,
    "test_model_revolve.py": 9,
    "test_model_shell.py": 5,
    "test_model_split.py": 5,
    "test_model_sweep.py": 7,
    "test_model_thread.py": 5,   # ThreadFeatures createInput/add plus the thread-table query graph - no conftest fake models a feature-collection input factory or a thread library
    "test_param_ops.py": 5,
    # PMI annotations/collections are an object graph no conftest shared fake models (not in
    # live_api_facts.SHAPES); the fakes stay minimal until a PMI shape measurement lands.
    "test_pmi_create.py": 2,
    "test_pmi_delete.py": 1,
    "test_pmi_edit.py": 1,
    "test_polyline.py": 6,
    "test_sketch_constrain.py": 15,   # the offset/pattern input+result object graphs conftest cannot model
    "test_sketch_core.py": 3,
    "test_sketch_delete_entity.py": 6,
    "test_sketch_dimension.py": 15,   # ellipse + fitted-spline dimension operands, plus the arc and bare-SketchPoint operands the new dim types take: sketch-entity shapes the conftest solid fakes do not model
    "test_sketch_get_merge.py": 1,
    "test_sketch_project.py": 1,
    "test_sketch_set_text.py": 8,
    "test_surface_create.py": 17,
    "test_surface_edit.py": 20,
    "test_surface_ops.py": 16,
    "test_sys_api_doc.py": 4,   # widened detection: a class named like an api_surface adsk class is counted whatever its prefix
    "test_view_screenshot.py": 3,
    "test_view_screenshot_multi.py": 3,
    "test_view_section.py": 12,
    "test_view_set.py": 12,
    "test_workspace_orient.py": 16,
    "test_write_guard.py": 4,
}

# file -> (ceiling, why this file needs MORE bespoke fakes than its measured entry above). Where a
# NEW bespoke fake lands: the reason is DATA the lint reads, not a comment it cannot, so adding one
# without stating why is impossible rather than discouraged (the shape test_helper_duplication's
# allowlist uses). _raise_offenders keeps each entry real - a ceiling at or below the file's
# baseline entry has nothing to justify and must collapse back into it.
_RAISED = {
    "test__sketch_detail.py": (
        20,
        "the three spline SketchCurves collections (fitted / cv / fixed), each carrying its own "
        "per-kind shape properties - the conftest solid-body fakes model no sketch-curve "
        "collection at all"),
}


def _ceilings(baseline=None, raised=None):
    """The effective per-file ceiling: the measured baseline, lifted only where a _RAISED entry
    states a reason for the rise. The tables default to the live ones and are passed in by the
    test that exercises the merge on data."""
    baseline = _PER_FILE_BASELINE if baseline is None else baseline
    raised = _RAISED if raised is None else raised
    return dict(baseline, **{fn: count for fn, (count, _why) in raised.items()})


def _raise_offenders(raised, baseline):
    """[complaint] for every _RAISED entry that states no reason, or whose ceiling does not sit
    above the file's baseline entry (an exemption that exempts nothing, hiding the next growth)."""
    out = []
    for fn, (count, why) in sorted(raised.items()):
        if not (why or "").strip():
            out.append(f"{fn}: a raised ceiling needs a plain-English reason, not an empty string")
        if count <= baseline.get(fn, 0):
            out.append(f"{fn}: ceiling {count} is not above its baseline entry "
                       f"{baseline.get(fn, 0)} - delete the _RAISED entry")
    return out


def _all_classes(path):
    """[(name, lineno)] for EVERY class definition in the .py file at `path` - module-level and
    nested (inside functions, methods, or other classes) alike."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=str(path))
    return [(node.name, node.lineno) for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def _conftest_fake_names():
    """Every top-level class name conftest.py defines - the shared fakes a local class must not
    shadow with its OWN redefinition (an import of the same name is not a definition, so it never
    trips this)."""
    tree = ast.parse(open(CONFTEST_PATH, encoding="utf-8").read(), filename=CONFTEST_PATH)
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def _adsk_class_names():
    """Every bare class name the installed bindings carry, from the generated api_surface dump -
    a test class named exactly like a real adsk class is a stand-in for it whatever its prefix,
    so naming convention alone can no longer hide a bespoke fake from this count."""
    import api_surface
    names = set()
    for table in (api_surface.PROPERTIES, api_surface.FACTORIES):
        for key in table:
            names.add(key.rsplit(".", 1)[-1])
    return names


def _offenders_in_file(path, conftest_names, shape_names):
    """[(name, lineno)] for every class in `path` (module-level or nested) matching the bespoke-fake
    shape: Fake/_Fake-prefixed, OR shadows a conftest shared-fake name, OR named exactly like an
    adsk class (live_api_facts.SHAPES or the api_surface dump)."""
    out = []
    for name, lineno in _all_classes(path):
        if (name.startswith("Fake") or name.startswith("_Fake")
                or name in conftest_names or name in shape_names):
            out.append((name, lineno))
    return out


def _measured_counts():
    """({file: count}, {file: [(name, lineno)]}) for every tests/unit/*.py with at least one
    bespoke fake-shaped class."""
    conftest_names = _conftest_fake_names()
    shape_names = set(live_api_facts.SHAPES) | _adsk_class_names()
    counts, details = {}, {}
    for fn in sorted(os.listdir(UNIT_DIR)):
        if not fn.endswith(".py"):
            continue
        offenders = _offenders_in_file(os.path.join(UNIT_DIR, fn), conftest_names, shape_names)
        if offenders:
            counts[fn] = len(offenders)
            details[fn] = offenders
    return counts, details


def _deltas(measured, baseline):
    """(regressions, stale) between measured per-file counts and the baseline dict.
    regressions: [(file, measured, baseline_entry)] where a file EXCEEDS its entry (a missing entry
    is 0, so a new file with any bespoke fakes regresses). stale: [(file, measured, baseline_entry)]
    where the entry exceeds reality (count dropped or file gone) and must be lowered to match."""
    regressions = [(fn, n, baseline.get(fn, 0)) for fn, n in sorted(measured.items())
                   if n > baseline.get(fn, 0)]
    stale = [(fn, measured.get(fn, 0), b) for fn, b in sorted(baseline.items())
             if measured.get(fn, 0) < b]
    return regressions, stale


def _baseline_literal(counts):
    """The corrected _PER_FILE_BASELINE dict as paste-ready source text."""
    lines = ["_PER_FILE_BASELINE = {"]
    lines += [f'    "{fn}": {counts[fn]},' for fn in sorted(counts)]
    lines.append("}")
    return "\n".join(lines)


class TestBespokeFakeRatchet:
    def test_bespoke_fake_count_per_file_shrink_only(self):
        measured, details = _measured_counts()
        print(f"bespoke fake-shaped classes in tests/unit: {sum(measured.values())} total "
              f"across {len(measured)} files (informational - the gate is per-file)")
        regressions, stale = _deltas(measured, _ceilings())
        if regressions:
            report = []
            for fn, n, base in regressions:
                report.append(f"{fn}: {n} bespoke fake-shaped classes (baseline {base}, "
                              f"+{n - base}):")
                report += [f"  {fn}:{lineno}: class {name}" for name, lineno in details[fn]]
            assert not regressions, (
                "bespoke fake-shaped classes grew in these files. Build from conftest's shared "
                "fakes - BRepFace/BRepEdge/Plane/Cylinder/Line3D/Circle3D/FakePoint/FakeVector3D - "
                "or extend them in conftest (only an attribute that is real, i.e. present in "
                "live_api_facts.SHAPES). A genuinely novel object graph the conftest fakes cannot "
                "model gets a _RAISED entry - the file's new ceiling plus the reason string this "
                "lint reads - and never a bumped _PER_FILE_BASELINE entry, which is the measured "
                "floor.\n" + "\n".join(report))
        if stale:
            drops = ", ".join(f"{fn} {b} -> {n}" for fn, n, b in stale)
            assert not stale, (
                f"_PER_FILE_BASELINE is stale - counts dropped ({drops}). Lock the win in so it "
                "cannot fund a new bespoke fake elsewhere: replace _PER_FILE_BASELINE in "
                "test_bespoke_fake_ratchet.py with:\n" + _baseline_literal(measured))

    def test_a_raised_ceiling_states_its_reason(self):
        # a new bespoke fake must say what object graph the shared fakes cannot model; a raise that
        # no longer sits above the file's measured entry is exempting nothing and hides the next
        # growth, so it must collapse back into the baseline
        bad = _raise_offenders(_RAISED, _PER_FILE_BASELINE)
        assert not bad, "_RAISED entries:\n  " + "\n  ".join(bad)

    def test_the_raise_check_bites(self):
        # the live table is empty most of the time, so the check is exercised on data: an empty
        # reason and a ceiling that is not a rise must each be caught, and a real raise must pass
        baseline = {"a.py": 2, "b.py": 5}
        assert _raise_offenders({"a.py": (3, "")}, baseline) == [
            "a.py: a raised ceiling needs a plain-English reason, not an empty string"]
        assert _raise_offenders({"b.py": (5, "a real reason")}, baseline) == [
            "b.py: ceiling 5 is not above its baseline entry 5 - delete the _RAISED entry"]
        assert len(_raise_offenders({"a.py": (1, "   ")}, baseline)) == 2, (
            "an entry that is both reasonless and not a rise must report both")
        assert _raise_offenders({"a.py": (3, "the transaction graph MakeComp cannot express")},
                                baseline) == []
        assert _raise_offenders({}, baseline) == []

    def test_the_scan_bites(self, tmp_path):
        conftest_names = _conftest_fake_names()
        shape_names = set(live_api_facts.SHAPES)
        hot = tmp_path / "hot.py"
        hot.write_text("class FakeWidget:\n    pass\n", encoding="utf-8")
        cool = tmp_path / "cool.py"
        cool.write_text("class WidgetHelper:\n    pass\n", encoding="utf-8")
        assert _offenders_in_file(hot, conftest_names, shape_names), (
            "a Fake-prefixed top-level class must trip the scan")
        assert not _offenders_in_file(cool, conftest_names, shape_names), (
            "an unrelated class name must NOT trip the scan")
        # a bare local redefinition of a live adsk type name (no Fake prefix) trips it too.
        shadow = tmp_path / "shadow.py"
        shadow.write_text("class BRepBody:\n    pass\n", encoding="utf-8")
        assert _offenders_in_file(shadow, conftest_names, shape_names), (
            "a local class shadowing a conftest/live type name must trip the scan")
        # nesting is not a hiding place: a fake-shaped class inside a function counts.
        nested = tmp_path / "nested.py"
        nested.write_text(
            "def _install():\n"
            "    class FakeTucked:\n"
            "        pass\n"
            "    class BRepBody:\n"
            "        pass\n",
            encoding="utf-8")
        assert len(_offenders_in_file(nested, conftest_names, shape_names)) == 2, (
            "fake-shaped classes nested inside a function must trip the scan")

    def test_the_ratchet_bites(self):
        # growth in a baselined file, any count in a new file, and a stale entry all fail;
        # an exact match is clean.
        regressions, stale = _deltas({"a.py": 3, "new.py": 1}, {"a.py": 2})
        assert regressions == [("a.py", 3, 2), ("new.py", 1, 0)], (
            "a file above its entry and a new file with fakes must both regress")
        regressions, stale = _deltas({"a.py": 1}, {"a.py": 2, "gone.py": 4})
        assert not regressions
        assert stale == [("a.py", 1, 2), ("gone.py", 0, 4)], (
            "a dropped count and a vanished file must both mark the baseline stale")
        assert _deltas({"a.py": 2}, {"a.py": 2}) == ([], []), (
            "an exact match must be clean")
        # a reasoned ceiling lifts that ONE file and nothing else - through the same merge the
        # gate runs on, so a merge that stopped honouring _RAISED fails here
        ceilings = _ceilings({"a.py": 2, "b.py": 1},
                             {"a.py": (3, "the transaction graph MakeComp cannot express")})
        assert ceilings == {"a.py": 3, "b.py": 1}
        assert _deltas({"a.py": 3, "b.py": 2}, ceilings) == ([("b.py", 2, 1)], []), (
            "a raised ceiling must absorb its own file's growth and no other's")
