# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""HOLE-HOST-1 predicates and their composed layout/runner arguments."""

import math
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import verify_acts_model as acts  # noqa: E402
import verify_program  # noqa: E402


def _closes_over(fn, target, seen=None):
    """True when a composed callable retains target in its closure chain."""
    if fn is target:
        return True
    seen = set() if seen is None else seen
    if id(fn) in seen:
        return False
    seen.add(id(fn))
    for cell in getattr(fn, "__closure__", ()) or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if callable(value) and _closes_over(value, target, seen):
            return True
    for value in getattr(fn, "__defaults__", ()) or ():
        if callable(value) and _closes_over(value, target, seen):
            return True
    return False


def _composed_row(tool, target):
    """The unique final ACTS row built from one authored argument function."""
    solids = next(rows for name, _pre, rows, _fb in verify_program.ACTS
                  if name == "ACT 2 - SOLIDS")
    hits = [(i, row) for i, row in enumerate(solids)
            if row[0] == tool and callable(row[1])
            and _closes_over(row[1], target)]
    assert len(hits) == 1
    return solids, hits[0]


def test_composed_program_places_b_points_once_while_a_is_active():
    solids, (scoped_top_at, scoped_top_row) = _composed_row(
        "find_geometry", acts._scoped_top_args)
    _rows, (control_top_at, control_top_row) = _composed_row(
        "find_geometry", acts._control_top_args)
    _rows, (bore_at, bore_row) = _composed_row("find_geometry", acts._scoped_bore_args)
    _rows, (scoped_at, scoped_row) = _composed_row("model_hole", acts._scoped_hole_args)
    _rows, (control_at, control_row) = _composed_row("model_hole", acts._unscoped_hole_args)

    scoped_top = scoped_top_row[1]({"hh_upper": "body-token"})
    control_top = control_top_row[1]({"hh_upper": "body-token"})
    bore = bore_row[1]({"hh_upper": "body-token"})
    scoped = scoped_row[1]({"hh_top": "face-token", "hh_upper": "body-token"})
    control = control_row[1]({"hh_top_control": "control-face-token"})

    assert scoped_top["nearest_to"] == [acts._px(acts._HOLE_HOST, acts._HOLE_X0 + 10),
                                         acts._py(acts._HOLE_HOST, 10), 10]
    assert scoped_top["target"] == "body-token"
    assert control_top["nearest_to"] == [acts._px(acts._HOLE_HOST, acts._HOLE_X0 + 5),
                                          acts._py(acts._HOLE_HOST, 5), 10]
    assert control_top["target"] == "body-token"
    assert bore == {"target": "body-token", "kind": "cylinder_face", "radius": 2,
                    "units": "mm", "max_results": 1}
    assert "nearest_to" not in bore
    assert scoped["points"] == [[acts._px(acts._HOLE_HOST, acts._HOLE_X0 + 10),
                                  acts._py(acts._HOLE_HOST, 10), 10]]
    assert scoped["face"] == "face-token"
    assert scoped["target_bodies"] == ["body-token"]
    assert control["points"] == [[acts._px(acts._HOLE_HOST, acts._HOLE_X0 + 5),
                                   acts._py(acts._HOLE_HOST, 5), 10]]
    assert control["face"] == "control-face-token"
    assert "target_bodies" not in control

    for row_at in (scoped_top_at, scoped_at, bore_at, control_top_at, control_at):
        prior_activations = [r for r in solids[:row_at] if r[0] == "design_activate_component"]
        assert prior_activations[-1][1]["occurrence"] == acts._HOLE_ACTIVE + ":1"

    assert not [row for row in acts._SOLIDS if isinstance(row[1], dict)
                and row[1].get("target") == acts._HOLE_HOST + ":Body1"]


def test_body_state_requires_independent_mass_and_exact_bounds(monkeypatch):
    monkeypatch.setitem(verify_program._SLOTS, acts._HOLE_HOST, (12.0, 34.0))
    predicate = acts._hole_body_state(
        acts._HOLE_HOST, (100.0, 10.0, 0.0), (120.0, 30.0, 10.0), 4000.0)
    payload = {
        "kind": "body",
        "units": "mm",
        "min_point": {"x": 112.0, "y": 44.0, "z": 0.0},
        "max_point": {"x": 132.0, "y": 64.0, "z": 10.0},
        "mass": {"volume": 4000.0, "accuracy_used": "very_high"},
    }
    assert predicate(payload) is True
    with pytest.raises(AssertionError, match="volume and bounds"):
        predicate({**payload, "mass": {"volume": 8000.0}})
    with pytest.raises(AssertionError, match="volume and bounds"):
        predicate({k: v for k, v in payload.items() if k != "mass"})


def test_cylinder_census_requires_count_radius_and_world_position(monkeypatch):
    monkeypatch.setitem(verify_program._SLOTS, acts._HOLE_HOST, (7.0, 9.0))
    predicate = acts._hole_cylinders(acts._HOLE_HOST, ((100.0, 10.0, 5.0),))
    payload = {
        "units": "mm",
        "match_count": 1,
        "returned": 1,
        "matches": [{"kind": "cylinder_face", "radius": 2.0,
                     "position": [107.0, 19.0, 5.0]}],
    }
    assert predicate(payload) is True
    with pytest.raises(AssertionError, match="cylinder census"):
        predicate({**payload, "matches": [{**payload["matches"][0], "radius": 3.0}]})
    with pytest.raises(AssertionError, match="cylinder census"):
        predicate({**payload, "matches": [{**payload["matches"][0],
                                            "position": [108.0, 19.0, 5.0]}]})
    with pytest.raises(AssertionError, match="cylinder census"):
        predicate({**payload, "truncated": True})


def test_component_tree_yields_one_exact_upper_body_handle():
    payload = {"tree": {
        "root": acts._HOLE_HOST,
        "truncated": False,
        "tree": {
            "component": acts._HOLE_HOST,
            "body_count": 2,
            "bodies": [
                {"name": "Body1", "handle": "upper-token", "is_solid": True},
                {"name": "Body2", "handle": "lower-token", "is_solid": True},
            ],
        },
    }}
    assert acts._hole_host_tree(payload) is True
    assert acts._hole_upper_handle(payload) == "upper-token"
    assert acts._hole_lower_handle(payload) == "lower-token"
    duplicate = {"tree": {**payload["tree"], "tree": {
        **payload["tree"]["tree"],
        "bodies": payload["tree"]["tree"]["bodies"]
        + [{"name": "Body1", "handle": "other-token", "is_solid": True}],
    }}}
    with pytest.raises(AssertionError, match="not unique"):
        acts._hole_upper_handle(duplicate)


def test_timeline_requires_complete_host_owned_rows_and_no_active_a_hole():
    rows = [
        {"name": "Sketch3", "type": "Sketch", "component": acts._HOLE_HOST},
        {"name": "Hole1", "type": "HoleFeature", "component": acts._HOLE_HOST},
        {"name": "Sketch4", "type": "Sketch", "component": acts._HOLE_HOST},
        {"name": "Hole2", "type": "HoleFeature", "component": acts._HOLE_HOST},
    ]
    predicate = acts._hole_timeline(True)
    assert predicate({"timeline": {"count": 4, "returned": 4, "timeline": rows}}) is True
    with pytest.raises(AssertionError, match="hole-host timeline"):
        predicate({"timeline": {"count": 5, "returned": 4, "truncated": True,
                                "timeline": rows}})
    with pytest.raises(AssertionError, match="hole-host timeline"):
        predicate({"timeline": {"count": 5, "returned": 5, "timeline": rows}})
    misowned = [{**row, "component": acts._HOLE_ACTIVE}
                if row["name"] == "Hole2" else row for row in rows]
    with pytest.raises(AssertionError, match="hole-host timeline"):
        predicate({"timeline": {"count": 4, "returned": 4, "timeline": misowned}})


def _combine_mass_payload(bodies, low, high, volume, kind="design"):
    """A model_inspect census payload for one combine control."""
    return {
        "kind": kind,
        "units": "mm",
        "min_point": dict(zip(("x", "y", "z"), low)),
        "max_point": dict(zip(("x", "y", "z"), high)),
        "mass": {
            "accuracy_used": "very_high",
            "volume": volume,
            "per_body_count": len(bodies),
            "per_body_truncated": False,
            "per_body": [
                {"body": name, "volume": body_volume, "lump_count": 1}
                for name, body_volume, _body_low, _body_high in bodies
            ],
        },
    }


def _combine_body_payload(low, high, volume):
    """A model_inspect body payload for one combine control."""
    return {
        "kind": "body",
        "units": "mm",
        "lump_count": 1,
        "min_point": dict(zip(("x", "y", "z"), low)),
        "max_point": dict(zip(("x", "y", "z"), high)),
        "mass": {"accuracy_used": "very_high", "volume": volume},
    }


def test_combine_outcome_predicates_require_complete_lump_evidence():
    cases = [
        ("complete", 1, 1, "Bodies combined."),
        ("partial", 2, 2, "WARNING: this join partially fused the inputs."),
        ("none", 3, 3, "WARNING: this join fused NOTHING."),
    ]
    for outcome, body_count, lump_count, note in cases:
        predicate = acts._combine_join_outcome(outcome, body_count, lump_count)
        payload = {
            "fusion_outcome": outcome,
            "fused": outcome != "none",
            "result_body_count": body_count,
            "result_bodies_complete": True,
            "input_lump_total": 3,
            "result_lump_total": lump_count,
            "feature": "Combine1",
            "note": note,
        }
        assert predicate(payload) is True
        with pytest.raises(AssertionError, match="join result/lump report"):
            predicate({**payload, "result_bodies_complete": False})
        with pytest.raises(AssertionError, match="join result/lump report"):
            predicate({**payload, "result_lump_total": lump_count + 1})


def test_combine_geometry_predicates_reject_missing_or_wrong_readback():
    bodies = (
        ("Body1", 3000.0, (0, 0, 0), (30, 10, 10)),
        ("Body4", 1000.0, (50, 0, 0), (60, 10, 10)),
    )
    census = acts._combine_census(
        "partial after", bodies, (0, 0, 0), (60, 10, 10), 4000.0)
    payload = _combine_mass_payload(bodies, (0, 0, 0), (60, 10, 10), 4000.0)
    assert census(payload) is True
    with pytest.raises(AssertionError, match="complete body census"):
        census({**payload, "mass": {**payload["mass"], "per_body_truncated": True}})
    with pytest.raises(AssertionError, match="complete body census"):
        census({**payload, "mass": {**payload["mass"], "per_body": payload["mass"]["per_body"][:1]}})

    transformed = acts._combine_census(
        "non-root partial after", bodies, (90, 40, 30), (100, 100, 40),
        4000.0, "occurrence")
    transformed_payload = _combine_mass_payload(
        bodies, (90, 40, 30), (100, 100, 40), 4000.0, "occurrence")
    assert transformed(transformed_payload) is True
    with pytest.raises(AssertionError, match="complete body census"):
        transformed({**transformed_payload, "kind": "design"})

    body = acts._combine_body("partial connected", (0, 0, 0), (30, 10, 10), 3000.0)
    body_payload = _combine_body_payload((0, 0, 0), (30, 10, 10), 3000.0)
    assert body(body_payload) is True
    with pytest.raises(AssertionError, match="body bounds and volume"):
        body({**body_payload, "mass": {**body_payload["mass"], "volume": 1000.0}})
    with pytest.raises(AssertionError, match="body bounds and volume"):
        body({**body_payload, "max_point": {"x": 60, "y": 10, "z": 10}})


def test_revolve_participant_predicates_require_complete_geometry_and_effects():
    split = (
        ("Body1", 320 * math.pi, (-10, -10, 0), (10, 10, 5)),
        acts._REVOLVE_BASELINE[1], acts._REVOLVE_BASELINE[2],
        ("Body4", 640 * math.pi, (-10, -10, 10), (10, 10, 20)),
    )
    payload = _combine_mass_payload(
        split, (-10, -10, 0), (32, 10, 20), 1540 * math.pi, "occurrence")
    payload["mass"]["per_body"] = [
        {**row, "is_solid": True} for row in payload["mass"]["per_body"]]
    census = acts._revolve_census("split", split)
    assert census(payload) is True
    with pytest.raises(AssertionError, match="participant census"):
        census({**payload, "mass": {**payload["mass"], "per_body_truncated": True}})
    wrong = [{**row, "volume": row["volume"] + 1}
             if row["body"] == "Body2" else row for row in payload["mass"]["per_body"]]
    with pytest.raises(AssertionError, match="participant census"):
        census({**payload, "mass": {**payload["mass"], "per_body": wrong}})

    scoped = acts._revolve_cut(
        "GrooveProfile", "Revolve2", ("Body1",), True, -225 * math.pi / 1000)
    scoped_payload = {
        "revolved": True, "feature": "Revolve2", "operation": "cut",
        "sketch": "GrooveProfile", "component": acts._REVOLVE_HOST,
        "axis": "z-axis", "angle_deg": 360, "result_bodies": ["Body1"],
        "scoped_to_bodies": ["RevolveProof:1:Body1"],
        "volume_delta_cm3": -0.706858,
    }
    assert scoped(scoped_payload) is True
    with pytest.raises(AssertionError, match="revolve cut result"):
        scoped({k: v for k, v in scoped_payload.items() if k != "volume_delta_cm3"})

    split_result = acts._revolve_cut(
        "BandProfile", "Revolve4", ("Body1", "Body4"), True, None)
    split_payload = {
        **{key: value for key, value in scoped_payload.items()
           if key != "volume_delta_cm3"},
        "feature": "Revolve4", "sketch": "BandProfile",
        "result_bodies": ["Body1", "Body4"],
        "note": "The body count changed, so volume_delta_cm3 is omitted.",
    }
    assert split_result(split_payload) is True
    with pytest.raises(AssertionError, match="revolve cut result"):
        split_result({**split_payload, "volume_delta_cm3": 0.0})

    face = {"radius": 9.0, "position": [0.0, 0.0, 7.5], "area": 90 * math.pi}
    face_payload = {
        "units": "mm", "match_count": 1, "returned": 1,
        "truncated": False, "matches": [face],
    }
    assert acts._revolve_groove_face(face_payload) is True
    with pytest.raises(AssertionError, match="groove face"):
        acts._revolve_groove_face({
            **face_payload, "matches": [{**face, "position": [0.0, 0.0, 12.5]}]})


def test_composed_revolve_participant_rows_pin_correct_xz_frame_and_scope():
    rows = acts._REVOLVE_PARTICIPANTS
    solids = next(rows for name, _pre, rows, _fb in verify_program.ACTS
                  if name == "ACT 2 - SOLIDS")
    start = next(i for i, row in enumerate(solids) if row is rows[0])
    assert solids[start:start + len(rows)] == rows
    ctx = {"revolve_participant_doc": "session:revolve"}

    geometry = [row[1](ctx) for row in rows if row[0] == "sketch_add_geometry"]
    by_sketch = {args["sketch_name"]: args["geometry"][0] for args in geometry}
    assert by_sketch["RingProfile"] == {
        "kind": "rectangle", "x1": 6, "y1": -20, "x2": 10, "y2": 0}
    assert by_sketch["GrooveProfile"] == {
        "kind": "rectangle", "x1": 3, "y1": -10, "x2": 9, "y2": -5}
    assert by_sketch["BandProfile"] == {
        "kind": "rectangle", "x1": 0, "y1": -10, "x2": 11, "y2": -5}

    cuts = []
    for row in rows:
        if row[0] == "model_revolve":
            args = row[1](ctx)
            if args.get("operation") == "cut":
                cuts.append(args)
    assert [args["sketch_name"] for args in cuts] == [
        "GrooveProfile", "GrooveProfile", "BandProfile"]
    assert cuts[0]["target_bodies"] == cuts[2]["target_bodies"] == ["RevolveProof:Body1"]
    assert "target_bodies" not in cuts[1]
    assert all(args["expect_document"] == "session:revolve" for args in cuts)
    assert rows[0][0] == "doc_get" and rows[0][3][0] == "revolve_story"
    assert [row[0] for row in rows[-3:]] == ["doc_activate", "doc_close", "doc_get"]


def test_composed_combine_controls_keep_owned_document_boundaries_and_pins():
    solids = next(rows for name, _pre, rows, _fb in verify_program.ACTS
                  if name == "ACT 2 - SOLIDS")
    authored = [
        ("complete", acts._COMBINE_COMPLETE),
        ("partial", acts._COMBINE_PARTIAL),
        ("none", acts._COMBINE_NONE),
    ]
    join_indices = []
    for case, rows in authored:
        join = [row for row in rows if row[0] == "model_combine"][-1]
        hits = [(i, row) for i, row in enumerate(solids)
                if row[0] == "model_combine" and row[2] is join[2]]
        assert len(hits) == 1
        join_indices.append(hits[0][0])
        args = hits[0][1][1]({f"combine_{case}_doc": f"session:{case}"})
        assert args == {
            "target": "Body1",
            "tools": ["Body2", "Body3"],
            "operation": "join",
            "keep_tools": False,
            "new_component": False,
            "expect_document": f"session:{case}",
        }

    assert join_indices == [198, 226, 255]
    control_rows = solids[179:270]
    assert control_rows[0][0] == "doc_get" and control_rows[0][3][0] == "combine_story"
    assert [row[3][0] for row in control_rows
            if row[0] == "doc_new"] == [
                "combine_complete_doc", "combine_partial_doc", "combine_none_doc"]
    assert sum(row[0] == "model_combine" for row in control_rows) == 4
    assert sum(row[0] == "design_delete_feature" for row in control_rows) == 3
    assert sum(row[0] == "doc_close" for row in control_rows) == 3
    assert control_rows[-1][0] == "doc_get"

    for case, rows in authored:
        for row in rows:
            if row[0] == "model_extrude":
                assert row[1]({f"combine_{case}_doc": "session:root"})["operation"] == "new"

    nonroot = acts._COMBINE_NONROOT_PARTIAL
    join = next(row for row in nonroot if row[0] == "model_combine")
    hits = [(i, row) for i, row in enumerate(solids)
            if row[0] == "model_combine" and row[2] is join[2]]
    assert len(hits) == 1 and hits[0][0] == 288
    args = hits[0][1][1]({
        "combine_nonroot_partial_doc": "session:nonroot",
        "combine_nonroot_tool_a": "face-a",
        "combine_nonroot_tool_b": "face-b",
    })
    assert args == {
        "target": "JoinProxyHost:1:Body1",
        "tools": ["face-a", "face-b"],
        "operation": "join",
        "keep_tools": False,
        "new_component": False,
        "expect_document": "session:nonroot",
    }
    component = next(row for row in nonroot if row[0] == "model_create_component")
    assert component[1]({"combine_nonroot_partial_doc": "session:nonroot"}) == {
        "name": "JoinProxyHost", "x": 100, "y": 40, "z": 30,
        "rotate_deg": 90, "rotate_axis": "z", "activate": True,
        "expect_document": "session:nonroot",
    }
    component_payload = {
        "created": True, "occurrence": "JoinProxyHost:1",
        "component": "JoinProxyHost", "full_path": "JoinProxyHost:1",
        "units": "mm", "position": {"x": 100, "y": 40, "z": 30},
        "rotate_deg": 90, "rotate_axis": "z", "activated": True,
    }
    assert acts._combine_nonroot_component(component_payload) is True
    with pytest.raises(AssertionError, match="transformed non-root combine host"):
        acts._combine_nonroot_component({
            **component_payload, "position": {"x": 101, "y": 40, "z": 30}})

    extrudes = [row for row in nonroot if row[0] == "model_extrude"]
    assert len(extrudes) == 3
    assert [row[1]({"combine_nonroot_partial_doc": "session:nonroot"})["operation"]
            for row in extrudes] == ["new", "new", "new"]

    finds = [row[1] for row in nonroot if row[0] == "find_geometry"]
    assert [row["target"] for row in finds] == [
        "JoinProxyHost:1:Body2", "JoinProxyHost:1:Body3"]
    assert [row["nearest_to"] for row in finds] == [[95, 60, 40], [95, 95, 40]]
    assert all(row["kind"] == "planar_face" and row["max_results"] == 1 for row in finds)
    assert nonroot[0][0] == "doc_new" and nonroot[-2][0] == "doc_close"
    assert nonroot[-1][0] == "doc_get"
    for case, rows in authored + [("nonroot_partial", nonroot)]:
        assert [row[0] for row in rows[-3:]] == ["doc_activate", "doc_close", "doc_get"]
        ctx = {"combine_story": "session:story", f"combine_{case}_doc": "session:scratch"}
        assert rows[-3][1](ctx) == {
            "name": "session:story", "expect_document": "session:scratch"}
        assert rows[-2][1](ctx) == {
            "name": "session:scratch", "save_changes": False,
            "expect_document": "session:story"}


def test_nonroot_combine_timeline_and_delete_require_exact_host():
    rows = [
        {"name": " JoinProxyHost:1", "type": "Occurrence"},
        {"name": "ProxyBox0", "type": "Sketch", "component": "JoinProxyHost"},
        {"name": "Extrude1", "type": "ExtrudeFeature", "component": "JoinProxyHost"},
        {"name": "ProxyBox1", "type": "Sketch", "component": "JoinProxyHost"},
        {"name": "Extrude2", "type": "ExtrudeFeature", "component": "JoinProxyHost"},
        {"name": "ProxyBox2", "type": "Sketch", "component": "JoinProxyHost"},
        {"name": "Extrude3", "type": "ExtrudeFeature", "component": "JoinProxyHost"},
        {"name": "Combine1", "type": "CombineFeature", "component": "JoinProxyHost"},
    ]
    face = acts._combine_face((95, 60, 40))
    face_payload = {
        "units": "mm", "match_count": 6, "returned": 1,
        "matches": [{"handle": "face-token", "kind": "planar_face",
                     "position": [95, 60, 40], "normal": [0, 0, 1]}],
    }
    assert face(face_payload) is True
    with pytest.raises(AssertionError, match="qualified non-root planar face"):
        face({**face_payload, "matches": [{"kind": "planar_face", "position": []}]})

    predicate = acts._combine_timeline("ProxyBox", True, "JoinProxyHost")
    assert predicate({"timeline": {"count": 8, "returned": 8, "timeline": rows}}) is True
    wrong_host = [{**row, "component": "WrongHost"} if row.get("component") else row
                  for row in rows]
    with pytest.raises(AssertionError, match="combine timeline"):
        predicate({"timeline": {"count": 8, "returned": 8, "timeline": wrong_host}})

    restored = rows[:-1]
    restored_predicate = acts._combine_timeline("ProxyBox", False, "JoinProxyHost")
    restored_payload = {"timeline": {
        "count": 7, "returned": 7, "truncated": False, "timeline": restored}}
    assert restored_predicate(restored_payload) is True
    wrong_restored_host = [
        {**row, "component": "WrongHost"} if row.get("component") else row
        for row in restored
    ]
    with pytest.raises(AssertionError, match="restored combine timeline"):
        restored_predicate({"timeline": {
            "count": 7, "returned": 7, "truncated": False,
            "timeline": wrong_restored_host}})
    with pytest.raises(AssertionError, match="restored combine timeline"):
        restored_predicate({"timeline": {
            **restored_payload["timeline"], "count": 8}})

    deleted = {"deleted": True, "feature": "Combine1", "index": 7,
               "entity_type": "CombineFeature"}
    assert acts._combine_deleted(deleted, 7) is True
    with pytest.raises(AssertionError, match="deleted by exact feature"):
        acts._combine_deleted({**deleted, "index": 6}, 7)
