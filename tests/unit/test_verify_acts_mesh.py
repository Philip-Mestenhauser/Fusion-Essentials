# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Mesh placement predicates and composed sweep arguments."""

import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import verify_acts_mesh as acts  # noqa: E402
import verify_layout  # noqa: E402
import verify_program  # noqa: E402


def _mesh_rows():
    """The final composed ACT 4 rows."""
    return next(rows for name, _pre, rows, _fb in verify_program.ACTS
                if name == "ACT 4 - MESH")


def _surface_rows():
    """The final composed ACT 3 rows."""
    return next(rows for name, _pre, rows, _fb in verify_program.ACTS
                if name == "ACT 3 - SURFACES")


def _mode_timeline(rows, base_count=0):
    """A complete design_get mode/timeline payload."""
    return {
        "mode_detail": {
            "design_type": "parametric",
            "has_timeline": True,
            "timeline_feature_count": len(rows),
            "base_feature_count": base_count,
        },
        "timeline": {
            "count": len(rows),
            "returned": len(rows),
            "timeline": [
                {"index": index, "name": name, "type": kind}
                for index, name, kind in rows
            ],
        },
    }


def _point(values):
    """A model_inspect point object."""
    return dict(zip(("x", "y", "z"), values))


def _body_payload(bounds):
    """A source body at bounds with independently read box properties."""
    return {
        "kind": "body",
        "units": "mm",
        "min_point": _point(bounds[0]),
        "max_point": _point(bounds[1]),
        "mass": {"volume": 1000.0, "area": 700.0, "accuracy_used": "very_high"},
    }


def _mesh_payload(bounds):
    """A closed, oriented 12-triangle mesh at bounds."""
    return {
        "kind": "mesh",
        "units": "mm",
        "bbox": {"min_point": _point(bounds[0]), "max_point": _point(bounds[1])},
        "volume": 1000.0,
        "area": 700.0,
        "triangle_count": 12,
        "node_count": 8,
        "is_closed": True,
        "is_oriented": True,
    }


def test_composed_fixture_places_authored_box_once_and_keeps_pose_deltas():
    rows = _mesh_rows()
    authored_x, authored_y = acts._MESH_POSE_AUTHORED
    assert authored_x > verify_layout._PIN_HALF
    assert authored_y > verify_layout._PIN_HALF
    slot_x, slot_y = verify_program._SLOTS[acts._MESH_POSE]
    sketch = next(row for row in rows if row[0] == "sketch_add_geometry"
                  and isinstance(row[1], dict)
                  and row[1].get("sketch_name") == acts._MESH_POSE + "S")
    rectangle = sketch[1]["geometry"][0]
    assert [rectangle[k] for k in ("x1", "y1", "x2", "y2")] == [
        authored_x + slot_x, authored_y + slot_y,
        authored_x + slot_x + 20.0, authored_y + slot_y + 10.0]
    identity, once, twice = acts._mesh_pose_bounds()
    assert identity == ((rectangle["x1"], rectangle["y1"], 0.0),
                        (rectangle["x2"], rectangle["y2"], 5.0))
    assert once == acts._mesh_pose_transform(identity)
    assert twice == acts._mesh_pose_transform(once)

    ctx = {"mesh_pose_first": "MeshPose:1", "mesh_pose_body": "body-handle"}
    moves = [row[1](ctx) for row in rows if row[0] == "assembly_move"
             and callable(row[1])
             and row[1](ctx).get("occurrence") == "MeshPose:1"]
    assert moves == [
        {"occurrence": "MeshPose:1", "rotate_deg": 90,
         "rotate_axis": "z", "units": "mm"},
        {"occurrence": "MeshPose:1", "dx": 100.0, "dy": 40.0, "dz": 30.0,
         "units": "mm"},
    ]

    instance_index = next(i for i, row in enumerate(rows)
                          if row[0] == "design_add_instance" and callable(row[1]))
    ground_index = next(i for i, row in enumerate(rows) if row[0] == "assembly_ground"
                        and callable(row[1])
                        and row[1](ctx).get("occurrence") == "MeshPose:1")
    m0_index = next(i for i, row in enumerate(rows) if isinstance(row[3], tuple)
                    and row[3][0] == "mesh_pose_m0_identity")
    assert m0_index < instance_index < ground_index
    assert rows[instance_index][1](ctx) == {"component": "MeshPose:1"}

    qualified = next(row for row in rows if row[0] == "save_as_mesh"
                     and isinstance(row[3], tuple) and row[3][0] == "mesh_pose_m1")
    assert qualified[1](ctx) == {
        "body": "MeshPose:1:Body1", "name": "MeshPoseM1", "quality": "low"}


def test_body_oracle_requires_exact_composed_bounds_volume_and_area():
    identity, once, _twice = acts._mesh_pose_bounds()
    predicate = acts._mesh_pose_body(once)
    payload = _body_payload(once)
    assert identity == ((acts._px(acts._MESH_POSE, acts._MESH_POSE_AUTHORED[0]),
                         acts._py(acts._MESH_POSE, acts._MESH_POSE_AUTHORED[1]), 0.0),
                        (acts._px(acts._MESH_POSE, acts._MESH_POSE_AUTHORED[0] + 20.0),
                         acts._py(acts._MESH_POSE, acts._MESH_POSE_AUTHORED[1] + 10.0), 5.0))
    assert predicate(payload) is True
    with pytest.raises(AssertionError, match="volume, area and world bounds"):
        predicate({**payload, "mass": {"volume": 1000.0, "area": 699.0}})
    shifted = ((once[0][0] + 1.0, *once[0][1:]), once[1])
    with pytest.raises(AssertionError, match="volume, area and world bounds"):
        predicate(_body_payload(shifted))


def test_mesh_oracle_rejects_double_transform_and_requires_vector_state():
    _identity, once, twice = acts._mesh_pose_bounds()
    predicate = acts._mesh_pose_mesh(once, reject_bounds=twice)
    payload = _mesh_payload(once)
    assert predicate(payload) is True
    with pytest.raises(AssertionError, match="mesh placement and geometry"):
        predicate(_mesh_payload(twice))
    with pytest.raises(AssertionError, match="mesh placement and geometry"):
        predicate({**payload, "is_oriented": False})


def test_pose_oracle_requires_identity_then_independent_shared_instance_poses(monkeypatch):
    monkeypatch.setitem(acts._RECALL, "mesh_pose_first", "MeshPose:1")
    monkeypatch.setitem(acts._RECALL, "mesh_pose_second", "MeshPose:2")
    payload = {
        "occurrence_count": 3,
        "occurrences_truncated": False,
        "occurrences": [
            {"name": "Other:1", "component": "Other"},
            {"name": "MeshPose:1", "component": "MeshPose",
             "origin": [100.0, 40.0, 30.0],
             "x_axis": [0.0, 1.0, 0.0], "y_axis": [-1.0, 0.0, 0.0],
             "z_axis": [0.0, 0.0, 1.0]},
            {"name": "MeshPose:2", "component": "MeshPose",
             "origin": [0.0, 0.0, 0.0],
             "x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0],
             "z_axis": [0.0, 0.0, 1.0]},
        ],
    }
    identity = {**payload, "occurrences": [
        ({**row, "origin": [0.0, 0.0, 0.0],
          "x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0]}
         if row["name"] == "MeshPose:1" else row)
        for row in payload["occurrences"]]}
    assert acts._mesh_pose_poses(first_moved=False)(identity) is True
    with pytest.raises(AssertionError, match="complete occurrence census"):
        acts._mesh_pose_poses(first_moved=False)(payload)
    monkeypatch.setitem(acts._RECALL, "pose_before",
                        acts._mesh_pose_pose_signature(payload))
    predicate = acts._mesh_pose_poses(same_as="pose_before")
    assert predicate(payload) is True
    with pytest.raises(AssertionError, match="complete occurrence census"):
        predicate({**payload, "occurrences_truncated": True})
    wrong = {**payload, "occurrences": [
        ({**row, "x_axis": [1.0, 0.0, 0.0]} if row["name"] == "MeshPose:1" else row)
        for row in payload["occurrences"]]}
    with pytest.raises(AssertionError, match="complete occurrence census"):
        predicate(wrong)


def test_mesh_census_requires_both_minted_names_and_complete_rows(monkeypatch):
    monkeypatch.setitem(acts._RECALL, "mesh_pose_m0", "MintedM0")
    monkeypatch.setitem(acts._RECALL, "mesh_pose_m1", "MintedM1")
    payload = {
        "count": 2,
        "truncated": False,
        "meshes": [{"name": "MintedM0", "triangle_count": 12},
                   {"name": "MintedM1", "triangle_count": 12}],
    }
    assert acts._mesh_pose_census(payload) is True
    with pytest.raises(AssertionError, match="complete mesh census"):
        acts._mesh_pose_census({**payload, "truncated": True})
    with pytest.raises(AssertionError, match="complete mesh census"):
        acts._mesh_pose_census({**payload, "meshes": [
            {"name": "MintedM0", "triangle_count": 12},
            {"name": "Wrong", "triangle_count": 12}]})


def test_base_feature_oracles_require_complete_preserved_timeline_and_independent_mode(monkeypatch):
    before_rows = [[0, "SketchA", "Sketch"], [1, "PadA", "ExtrudeFeature"]]
    before = _mode_timeline(before_rows)
    assert acts._base_feature_parametric(before) is True
    monkeypatch.setitem(acts._RECALL, "bf_before", acts._base_feature_state(before))
    monkeypatch.setitem(acts._RECALL, "bf_name", "OwnedScope")
    after = _mode_timeline(before_rows + [[2, "OwnedScope", "BaseFeature"]], base_count=1)
    assert acts._base_feature_added("bf_before", "bf_name")(after) is True
    assert acts._base_feature_direct({"mode_detail": {
        "design_type": "direct", "has_timeline": False,
        "timeline_feature_count": None, "in_base_feature_edit": False,
    }}) is True

    incomplete = {**after, "timeline": {**after["timeline"], "returned": 2}}
    with pytest.raises(AssertionError, match="one BaseFeature"):
        acts._base_feature_added("bf_before", "bf_name")(incomplete)
    missing_prior = _mode_timeline([
        [0, "SketchA", "Sketch"], [1, "Different", "ExtrudeFeature"],
        [2, "OwnedScope", "BaseFeature"]], base_count=1)
    with pytest.raises(AssertionError, match="one BaseFeature"):
        acts._base_feature_added("bf_before", "bf_name")(missing_prior)


def test_composed_act_has_intervening_reads_and_two_document_finish_rounds(monkeypatch):
    rows = _surface_rows()
    story_start = next(i for i, row in enumerate(rows)
                       if row[3] and row[3][0] == "bf_story_name")
    assert [row[0] for row in rows[story_start - 1:story_start + 6]] == [
        "design_get", "model_base_feature", "design_get", "model_base_feature",
        "design_get", "model_base_feature", "design_get",
    ]
    assert rows[story_start - 1][2] is acts._base_feature_parametric
    assert rows[story_start + 1][2] is acts._base_feature_direct
    assert rows[story_start + 3][2] is acts._base_feature_direct
    assert any("BF1Missing" in fragment for fragment in rows[story_start + 2][2].fragments)

    named_start = next(i for i, row in enumerate(rows)
                       if row[3] and row[3][0] == "bf_cross_named_a")
    assert [row[0] for row in rows[named_start:named_start + 13]] == [
        "model_base_feature", "design_get", "doc_new", "design_get",
        "model_base_feature", "design_get", "model_base_feature", "design_get",
        "doc_activate", "design_get", "model_base_feature", "design_get",
        "model_base_feature",
    ]
    assert rows[named_start + 5][2] is acts._base_feature_direct
    assert rows[named_start + 9][2] is acts._base_feature_direct

    unnamed_start = named_start + 12
    assert rows[unnamed_start][3][0] == "bf_cross_unnamed_a"
    assert [row[0] for row in rows[unnamed_start:unnamed_start + 14]] == [
        "model_base_feature", "design_get", "doc_activate", "model_base_feature",
        "design_get", "model_base_feature", "design_get", "doc_activate",
        "design_get", "model_base_feature", "design_get", "doc_close",
        "design_activate_component", "model_create_component",
    ]
    ctx = {
        "bf_story_doc": "session:a",
        "bf_extra_doc": "session:b",
        "bf_cross_named_a": "BFCrossNamed",
        "bf_cross_named_b": "BFCrossNamed",
        "bf_cross_unnamed_a": "BFCrossUnnamed",
        "bf_cross_unnamed_b": "BFCrossUnnamed",
    }
    named_b_finish = rows[named_start + 6][1](ctx)
    named_a_finish = rows[named_start + 10][1](ctx)
    unnamed_b_finish = rows[unnamed_start + 5][1](ctx)
    unnamed_a_finish = rows[unnamed_start + 9][1](ctx)
    assert named_b_finish == {
        "action": "finish", "base_feature": "BFCrossNamed",
        "expect_document": "session:b"}
    assert named_a_finish == {
        "action": "finish", "base_feature": "BFCrossNamed",
        "expect_document": "session:a"}
    assert unnamed_b_finish == {"action": "finish", "expect_document": "session:b"}
    assert unnamed_a_finish == {"action": "finish", "expect_document": "session:a"}

    opened = {"editing": True, "open_scope_count": 2, "base_feature": "Shared"}
    monkeypatch.setitem(acts._RECALL, "bf_cross_named_a", "Shared")
    monkeypatch.setitem(acts._RECALL, "bf_cross_unnamed_a", "Shared")
    assert rows[named_start + 4][2](opened) is True
    assert rows[unnamed_start + 3][2](opened) is True
    distinct = {**opened, "base_feature": "Different"}
    with pytest.raises(AssertionError, match="same adopted base-feature name"):
        rows[named_start + 4][2](distinct)
    with pytest.raises(AssertionError, match="same adopted base-feature name"):
        rows[unnamed_start + 3][2](distinct)
