# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The shared world fakes, each driven through the seam a tool reads it through."""

import math

import pytest

import live_api_facts as _api_facts
from conftest import (FakeApplication, FakeBaseFeature, FakeBaseFeatures, FakeCAMParameter,
                      FakeDataFile, FakeDataFolder,
                      FakeDocumentReference, FakeFeature, FakeFeatures, FakeJoints, FakeMachine,
                      FakeMotionLink, FakeMotionLinks, FakeRigidGroup, FakeRigidGroups,
                      FakeSelection, FakeSetups, FakeTimeline, FakeTimelineObject, FakeTool,
                      FakeUserParameter,
                      FakeUserParameters, _MotionLimits, make_cam_parameters, make_data_tree,
                      make_design, make_joint, make_document_world, make_sketch, make_sketch_curve,
                      make_timeline)


class TestDocumentWorld:
    def test_the_active_documents_design_product_reaches_the_root_component(self):
        # The hop every design tool makes: activeDocument -> products -> the design -> its root.
        design = make_design(bodies=["Body1"])
        app = make_document_world(design=design, name="Bracket",
                                  selections=[FakeSelection(entity="face")])
        product = app.activeDocument.products.itemByProductType("DesignProductType")
        assert product.rootComponent.bRepBodies.itemByName("Body1") is not None
        assert app.activeDocument.products.itemByProductType("CAMProductType") is None
        assert app.userInterface.activeSelections.item(0).entity == "face"

    def test_a_closed_document_reads_invalid_and_its_name_raises(self):
        app = make_document_world(design=make_design(), name="Bracket")
        doc = app.activeDocument
        assert doc.name == "Bracket" and doc.isValid is True
        assert doc.close(False) is True
        assert doc.isValid is False
        with pytest.raises(RuntimeError, match="deleted Object"):
            doc.name

    def test_a_save_that_lands_advances_the_data_files_version(self):
        data_file = FakeDataFile("Bracket", version=3)
        app = make_document_world(design=make_design(), data_file=data_file)
        assert app.activeDocument.save("checkpoint") is True
        assert data_file.versionNumber == 4 and app.activeDocument.isModified is False

    def test_a_refreshed_reference_lands_the_sources_latest_version(self):
        source = FakeDataFile("Sub", version=2, latest_version=5)
        ref = FakeDocumentReference(data_file=source, version=2, out_of_date=True)
        assert ref.getLatestVersion() is True
        assert ref.version == 5 and ref.isOutOfDate is False

    def test_the_two_refusing_refresh_states_are_reachable(self):
        # The platform lie a refresh must re-check for, and the raise a derive link answers with.
        source = FakeDataFile("Sub", version=2, latest_version=5)
        lying = FakeDocumentReference(data_file=source, out_of_date=True, stays_out_of_date=True)
        assert lying.getLatestVersion() is True and lying.isOutOfDate is True
        derive = FakeDocumentReference(data_file=source, version=1, out_of_date=True,
                                       latest_raises="InternalValidationError")
        with pytest.raises(RuntimeError, match="InternalValidationError"):
            derive.getLatestVersion()
        derive.version = source.latestVersionNumber
        assert derive.version == 5

    def test_a_document_added_to_the_walk_is_the_one_handed_back(self):
        app = FakeApplication()
        made = app.documents.add("FusionDesignDocumentType")
        assert app.documents.count == 1 and app.documents.item(0) is made


class TestTimelineWorld:
    def test_a_marker_move_past_either_end_answers_true_and_leaves_the_marker(self):
        # Measured: the bool is no signal that the marker moved - only markerPosition says that.
        timeline = make_timeline("Sketch1", "Extrude1", marker=1)
        assert timeline.moveToEnd() is True and timeline.markerPosition == 2
        # The exact boundary: the marker may sit AT count, and one step further answers True
        # while landing nowhere.
        assert timeline.movetoNextStep() is True and timeline.markerPosition == 2
        assert timeline.moveToPreviousStep() is True and timeline.markerPosition == 1
        assert timeline.moveToBeginning() is True and timeline.markerPosition == 0
        assert timeline.moveToPreviousStep() is True and timeline.markerPosition == 0

    def test_a_move_the_platform_refuses_answers_false(self):
        # the only False left: move_ok models the refusal, distinct from a move that lands nowhere.
        stuck = FakeTimeline(move_ok=False, marker=1)
        assert stuck.moveToBeginning() is False and stuck.markerPosition == 1

    def test_a_refused_roll_leaves_the_entry_where_it_was(self):
        stubborn = FakeTimelineObject("Extrude1", roll_ok=False)
        assert stubborn.rollTo(True) is False and stubborn.isRolledBack is False
        assert make_timeline("Extrude1").item(0).rollTo(True) is True

    def test_an_open_base_feature_is_invisible_while_its_siblings_are_not(self):
        features = FakeFeatures(features=[FakeFeature("Extrude1")])
        base = features.baseFeatures.add()
        assert features.baseFeatures.count == 1
        assert base.startEdit() is True
        assert features.baseFeatures.count == 0
        assert features.baseFeatures.itemByName(base.name) is None
        assert features.itemByName("Extrude1") is not None
        assert base.finishEdit() is True
        assert features.baseFeatures.itemByName(base.name) is base

    def test_a_scope_never_opened_reads_apart_from_one_opened_and_closed(self):
        # _open alone cannot tell the two apart - it is False before the first startEdit and False
        # again after finishEdit, which is why the counts exist.
        base = FakeBaseFeature()
        untouched = FakeBaseFeature()
        assert base.startEdit() is True and base.finishEdit() is True
        assert (base._starts, base._finishes) == (1, 1)
        assert (untouched._starts, untouched._finishes) == (0, 0)
        assert base._open is untouched._open is False

    def test_the_base_feature_add_hands_back_is_the_one_the_caller_supplied(self):
        held = FakeBaseFeature("Held")
        collection = FakeBaseFeatures(made=held)
        assert collection.count == 0            # nothing is in the walk until add() runs
        assert collection.add() is held
        assert collection.count == 1 and collection.itemByName("Held") is held
        # a supplied feature is governed by the same invisibility rule as a self-made one
        assert held.startEdit() is True and collection.count == 0

    def test_a_timeline_read_inside_an_open_scope_raises(self):
        design = make_design(timeline=make_timeline("Extrude1", raises="scope is open"))
        with pytest.raises(RuntimeError, match="scope is open"):
            design.timeline.count


class TestParameterWorld:
    def test_a_deleted_parameter_leaves_the_walk_and_a_miss_answers_none(self):
        width = FakeUserParameter("width", expression="50 mm", value=5.0)
        params = FakeUserParameters([width, FakeUserParameter("depth")])
        assert params.itemByName("nope") is None
        assert params.count == 2
        assert width.deleteMe() is True
        assert params.count == 1 and params.itemByName("width") is None

    def test_a_parameter_that_refuses_deletion_stays_in_the_walk(self):
        stuck = FakeUserParameter("width", delete_ok=False)
        params = FakeUserParameters([stuck])
        assert stuck.deleteMe() is False
        assert params.itemByName("width") is stuck

    def test_an_added_parameter_joins_the_design_walk(self):
        design = make_design(user_parameters=FakeUserParameters())
        made = design.userParameters.add("width", None, "mm", "note")
        assert design.userParameters.itemByName("width") is made
        assert made.unit == "mm" and made.comment == "note"


class TestJointMotionWorld:
    def test_a_rotation_beyond_an_enabled_limit_is_ignored_and_one_at_the_bound_lands(self):
        limits = _MotionLimits(minimum=math.radians(-10.0), maximum=math.radians(10.0))
        joint = make_joint(kind="revolute", rotation_limits=limits)
        motion = joint.jointMotion
        motion.rotationValue = math.radians(5.0)
        parked = motion.rotationValue
        motion.rotationValue = math.radians(45.0)
        assert motion.rotationValue == parked
        # The exact boundary: AT the enabled bound the assignment lands.
        motion.rotationValue = math.radians(10.0)
        assert round(math.degrees(motion.rotationValue), 4) == 10.0

    def test_a_stored_rotation_is_verbatim_and_lands_on_the_measured_store_grid(self, monkeypatch):
        motion = make_joint(kind="revolute").jointMotion
        motion.rotationValue = math.radians(750.0)
        # Verbatim: neither normalized into [0,360) nor accumulated onto the turn just made.
        assert round(math.degrees(motion.rotationValue), 6) == 750.0
        monkeypatch.setitem(_api_facts.BEHAVIOR, "joint_revolute_store_grid_deg", 0.1)
        motion.rotationValue = math.radians(12.34)
        assert round(math.degrees(motion.rotationValue), 6) == 12.3

    def test_a_cylindrical_motion_answers_no_slide_direction_vector(self):
        motion = make_joint(kind="cylindrical", slide=1.0).jointMotion
        assert motion.slideValue == 1.0
        assert not hasattr(motion, "slideDirectionVector")

    def test_a_rolled_back_motion_link_leaves_the_collection(self):
        joints = FakeJoints(joints=[make_joint(name="Rev1"), make_joint(name="Sld1", kind="slider")])
        links = FakeMotionLinks(new_link=FakeMotionLink(joint_one=joints.itemByName("Rev1")))
        link = links.add(links.createInput(joints.item(0), joints.item(1)))
        assert links.count == 1
        assert link.deleteMe() is True and links.count == 0

    def test_a_coupling_write_lands_all_five_arguments_and_a_refused_one_lands_none(self):
        # The live five-argument call: a DOF and a ValueInput per side plus the direction. The
        # re-value read-back is ml.valueOne.value, so the number has to land inside the parameter.
        link = FakeMotionLink(value_one=1.0, value_two=1.0)
        assert link.setMotionData("rotate", 1.0, "slide", 4.0, True) is True
        assert (link.valueOne.value, link.valueTwo.value) == (1.0, 4.0)
        assert link.motionOne == "rotate" and link.motionTwo == "slide"
        assert link.isReversed is True
        refusing = FakeMotionLink(value_one=1.0, value_two=1.0, set_motion_ok=False)
        assert refusing.setMotionData("rotate", 1.0, "slide", 9.0, True) is False
        assert refusing.valueTwo.value == 1.0 and refusing.isReversed is False

    def test_a_link_reporting_neither_coupled_motion_is_its_own_state(self):
        assert not hasattr(FakeMotionLink(), "motionOne")
        assert FakeMotionLink(motion_one="rotate", motion_two="slide").motionOne == "rotate"

    def test_a_rigid_group_reads_back_the_membership_setoccurrences_landed(self):
        groups = FakeRigidGroups()
        group = groups.add(["occ1", "occ2"])
        assert group.occurrences.count == 2
        assert group.setOccurrences(["occ1"]) is True
        assert groups.itemByName(group.name).occurrences.count == 1
        stubborn = FakeRigidGroup(occurrences=["occ1"], set_ok=False)
        assert stubborn.setOccurrences([]) is False and stubborn.occurrences.count == 1


class TestCamJobWorld:
    def test_a_setup_created_through_the_input_joins_the_walk_with_what_it_was_given(self):
        setups = FakeSetups()
        machine = FakeMachine(description="Haas VF-2", vendor="Haas", model="VF-2")
        job = setups.createInput("MillingOperation")
        job.name = "Op1 Setup"
        job.models = ["Body1"]
        job.machine = machine
        made = setups.add(job)
        assert setups.itemByName("Op1 Setup") is made
        assert setups._added[0].machine is machine and setups._added[0].models == ["Body1"]

    def test_a_tool_parameter_publishes_its_payload_through_the_second_value_hop(self):
        tool = FakeTool(description="10mm flat",
                        parameters=make_cam_parameters(("tool_diameter", "10 mm", 1.0)))
        diameter = tool.parameters.itemByName("tool_diameter")
        assert diameter.expression == "10 mm" and diameter.value.value == 1.0
        assert tool.parameters.itemByName("tool_taperAngle") is None

    def test_a_write_to_a_locked_parameter_lands(self):
        # The state that makes cam_edit_operation's refuse-BEFORE-write load-bearing: a locked
        # parameter takes the assignment (measured), so a write made first is a real edit the UI
        # never offers, not a no-op the caller could shrug off.
        params = make_cam_parameters(("contours", "'a'"), ("strategy", "'swarf'"))
        locked = params.itemByName("strategy")
        locked.isEditable = False
        locked.expression = "'adaptive'"
        assert locked.expression == "'adaptive'"
        settable = params.itemByName("contours")
        settable.expression = "'b'"
        assert settable.expression == "'b'"
        assert FakeCAMParameter("context", "'part'").isEditable is True


class TestDataWorld:
    def test_a_file_in_the_root_folder_answers_both_of_its_parents(self):
        project = make_data_tree(name="MCP Test", files=["Part v1"])
        part = project.rootFolder.dataFiles.itemByName("Part v1")
        assert part.parentFolder is project.rootFolder
        assert part.parentProject.name == "MCP Test"

    def test_a_deleted_file_leaves_the_folder_walk_and_a_refused_delete_stays(self):
        project = make_data_tree(files=["Part v1", FakeDataFile("Stuck", delete_ok=False)])
        root = project.rootFolder
        assert root.dataFiles.count == 2
        assert root.dataFiles.itemByName("Part v1").deleteMe() is True
        assert root.dataFiles.count == 1 and root.dataFiles.itemByName("Part v1") is None
        assert root.dataFiles.itemByName("Stuck").deleteMe() is False
        assert root.dataFiles.itemByName("Stuck") is not None

    def test_a_move_that_the_platform_refuses_leaves_the_file_where_it_was(self):
        project = make_data_tree(files=[FakeDataFile("Part v1", move_ok=False)])
        root = project.rootFolder
        part = root.dataFiles.item(0)
        assert part.move(FakeDataFolder("Archive")) is False
        assert part.parentFolder is root


class TestSketchWorld:
    def test_a_curve_lands_in_both_the_flat_walk_and_its_own_kind(self):
        # A '<type>:<index>' ref indexes the per-kind sub-collection while a count read-back walks
        # the flat one, so a curve missing from either side reads as a draw that never landed.
        line, circle = make_sketch_curve("L0"), make_sketch_curve("C0")
        sketch = make_sketch("Plate", lines=[line], circles=[circle])
        assert sketch.sketchCurves.count == 2
        assert sketch.sketchCurves.sketchLines.item(0) is line
        assert sketch.sketchCurves.sketchCircles.item(0) is circle
        assert sketch.sketchCurves.sketchArcs.count == 0

    def test_a_deferred_sketch_still_answers_the_profiles_it_held(self):
        # Measured (sketch-profiles-under-compute-deferred): the flag does not empty profiles, it
        # freezes them - so a stale count reads exactly like a fresh one and the flag is the tell.
        sketch = make_sketch(profiles=[object(), object()], is_compute_deferred=True)
        assert sketch.isComputeDeferred is True and sketch.profiles.count == 2
