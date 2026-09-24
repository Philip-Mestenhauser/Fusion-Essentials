# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the solids, the details that cut them, and the parametric resize.

The modelling spine of the story: the sketches turn into the bracket, its edges are broken, the
cameo bodies carry the feature verbs the part has no home for, and the one driving length is
re-driven so every feature follows it.
"""

import math

from verify_core import (
    _RECALL, _axis_aligned_face_at, _box, _chamfered, _component_metadata, _ctx_get, _cut_a_chain,
    _cut_exactly, _cut_on_its_pivot, _marker_parked_after, _datum, _datum_plane,
    _document_closed, _drafted, _drafted_on_its_pivot, _drafted_symmetric, _drilled,
    _extent_measured, _extruded, _face_up_at, _fg, _fgn,
    _filleted, _full_rounded, _gap_measured, _holes_recognized, _holes_windowed, _home_address,
    _home_document, _interference_measured, _joined,
    _joint_origin_at, _joint_origins_listed, _lofted, _made_component, _made_component_inactive,
    _material_assigned, _matched, _measured, _metadata_set, _mirrored, _moved, _moved_occurrence,
    _captured, _near, _needs,
    _new_document, _offset_faces, _param_added, _param_deleted, _param_read, _param_set_to,
    _param_traced, _path_count, _patterned, _piped, _pocket_boss, _pockets_recognized, _prof,
    _recall, _recognized_cbore_walls, _recognized_pocket_floor, _refused, _relation_measured,
    _relation_passes, _relation_read, _replaced_on_its_pivot, _revolved, _shelled,
    _sits_on_a_face, _swept, _unless, _watch)
from verify_acts_cam import MACHINING_EXTENSION
from verify_layout import _px, _py


_RADIUS_DIAMETER_TOL_MM = 0.0005

_HOLE_HOST = "HoleHostB"
_HOLE_ACTIVE = "ActiveA"
_HOLE_X0 = 2600.0
_HOLE_ACTIVE_X0 = 2650.0
_HOLE_VOLUME = 125.663706


def _section_created(other_key=None):
    """Require one generated section name, distinct from an earlier cut when supplied."""
    def check(p):
        name = p.get("section")
        other = _RECALL.get(other_key) if other_key else None
        return _measured("generated section identity", {"section": name, "other": other},
                         isinstance(name, str) and bool(name)
                         and (other_key is None or name != other))
    return check


def _section_census(keys, visible):
    """Require the complete generated-name and visibility census for the retained sections."""
    def check(p):
        rows = p.get("sections") or []
        expected = [_RECALL.get(key) for key in keys]
        got = [row.get("name") for row in rows]
        bulbs = [row.get("visible") for row in rows]
        return _measured("section analysis census",
                         {"count": p.get("count"), "names": got, "visible": bulbs},
                         all(isinstance(name, str) and name for name in expected)
                         and p.get("count") == len(rows) == len(expected)
                         and got == expected and bulbs == list(visible))
    return check


def _section_named_clear(removed_key):
    """Require one exact generated section removed with bounded count facts."""
    def check(p):
        removed = _RECALL.get(removed_key)
        valid = (p.get("section") == removed and p.get("removed") == [removed]
                 and p.get("removed_count") == 1 and p.get("sections_before") == 2
                 and p.get("sections_after") == 1 and "remaining" not in p)
        return _measured("selected section deletion",
                         {"removed": p.get("removed"), "before": p.get("sections_before"),
                          "after": p.get("sections_after")},
                         valid)
    return check


def _section_camera(p):
    """Return one complete workspace camera record for later equality."""
    view = p.get("view") or {}
    if (view.get("projection") not in ("orthographic", "perspective")
            or not isinstance(view.get("eye"), dict) or not isinstance(view.get("target"), dict)):
        raise AssertionError(f"section camera is incomplete: {view!r}")
    return view


def _section_camera_unchanged(key):
    """Require the workspace camera to equal the retained pre-cut record."""
    def check(p):
        current, before = _section_camera(p), _RECALL.get(key)
        return _measured("section clear camera preservation",
                         {"before": before, "after": current}, current == before)
    return check


def _placed_xyz(component, xyz):
    """One authored point after the layout pass places its component."""
    return [_px(component, xyz[0]), _py(component, xyz[1]), xyz[2]]


def _hole_body_state(component, low, high, volume):
    """A body has the exact measured bounds and volume for one hole-participant beat."""
    def check(p):
        got_low, got_high = p.get("min_point") or {}, p.get("max_point") or {}
        want_low, want_high = _placed_xyz(component, low), _placed_xyz(component, high)
        got_volume = (p.get("mass") or {}).get("volume")
        bounds_ok = all(
            _near(got.get(axis), want, 0.01)
            for got, wanted in ((got_low, want_low), (got_high, want_high))
            for axis, want in zip(("x", "y", "z"), wanted))
        return _measured(
            f"{component} body volume and bounds",
            {"kind": p.get("kind"), "units": p.get("units"),
             "accuracy": (p.get("mass") or {}).get("accuracy_used"),
             "volume": got_volume, "min": got_low, "max": got_high},
            p.get("kind") == "body" and p.get("units") == "mm"
            and (p.get("mass") or {}).get("accuracy_used") == "very_high"
            and _near(got_volume, volume, 0.001) and bounds_ok)
    return check


def _hole_cylinders(component, positions):
    """The complete radius-2 cylinder census at the expected placed positions."""
    def check(p):
        matches = p.get("matches") or []
        wanted = sorted(tuple(round(v, 3) for v in _placed_xyz(component, pos))
                        for pos in positions)
        got = sorted(tuple(round(v, 3) for v in m.get("position", [])) for m in matches)
        exact_faces = all(
            m.get("kind") == "cylinder_face" and _near(m.get("radius"), 2.0, 0.001)
            for m in matches)
        return _measured(
            f"{component} radius-2 cylinder census",
            {"match_count": p.get("match_count"), "returned": p.get("returned"),
             "positions": got, "units": p.get("units"), "truncated": p.get("truncated")},
            p.get("units") == "mm"
            and p.get("match_count") == p.get("returned") == len(wanted)
            and len(matches) == len(wanted) and p.get("truncated") is not True
            and exact_faces and got == wanted)
    return check


def _hole_face_bounds(p):
    """The fresh scoped bore handle resolves to the measured 4 x 4 x 10 mm face."""
    low = _placed_xyz(_HOLE_HOST, (_HOLE_X0 + 8, 8, 0))
    high = _placed_xyz(_HOLE_HOST, (_HOLE_X0 + 12, 12, 10))
    center = _placed_xyz(_HOLE_HOST, (_HOLE_X0 + 10, 10, 5))
    got_low, got_high = p.get("min_point") or {}, p.get("max_point") or {}
    got_center = p.get("center") or {}
    return _measured(
        "fresh scoped bore face bounds",
        {"kind": p.get("kind"), "units": p.get("units"),
         "x": p.get("x"), "y": p.get("y"), "z": p.get("z"),
         "min": got_low, "max": got_high, "center": got_center},
        p.get("kind") == "face" and p.get("units") == "mm"
        and _near(p.get("x"), 4.0, 0.01) and _near(p.get("y"), 4.0, 0.01)
        and _near(p.get("z"), 10.0, 0.01)
        and all(_near(got_low.get(axis), want, 0.01)
                for axis, want in zip(("x", "y", "z"), low))
        and all(_near(got_high.get(axis), want, 0.01)
                for axis, want in zip(("x", "y", "z"), high))
        and all(_near(got_center.get(axis), want, 0.01)
                for axis, want in zip(("x", "y", "z"), center)))


def _hole_host_tree(p):
    """The component-rooted tree names both B bodies and publishes consumable handles."""
    tree = p.get("tree") or {}
    node = tree.get("tree") or {}
    bodies = node.get("bodies") or []
    return _measured(
        "HoleHostB component-rooted body tree",
        {"root": tree.get("root"), "component": node.get("component"),
         "body_count": node.get("body_count"),
         "bodies": [(b.get("name"), bool(b.get("handle"))) for b in bodies]},
        tree.get("root") == _HOLE_HOST and tree.get("truncated") is False
        and node.get("component") == _HOLE_HOST and node.get("body_count") == 2
        and [b.get("name") for b in bodies] == ["Body1", "Body2"]
        and all(isinstance(b.get("handle"), str) and b["handle"]
                and b.get("is_solid") is True for b in bodies))


def _hole_upper_handle(p):
    """The exact upper-body handle from the component-rooted tree."""
    bodies = ((p.get("tree") or {}).get("tree") or {}).get("bodies") or []
    hit = [b.get("handle") for b in bodies if b.get("name") == "Body1"]
    if len(hit) != 1 or not isinstance(hit[0], str) or not hit[0]:
        raise AssertionError(f"HoleHostB Body1 handle is not unique: {hit!r}")
    return hit[0]


def _hole_lower_handle(p):
    """The exact lower-body handle from the component-rooted tree."""
    bodies = ((p.get("tree") or {}).get("tree") or {}).get("bodies") or []
    hit = [b.get("handle") for b in bodies if b.get("name") == "Body2"]
    if len(hit) != 1 or not isinstance(hit[0], str) or not hit[0]:
        raise AssertionError(f"HoleHostB Body2 handle is not unique: {hit!r}")
    return hit[0]


def _hole_active_tree(p):
    """The component-rooted tree names A's sole body and its consumable handle."""
    tree = p.get("tree") or {}
    node = tree.get("tree") or {}
    bodies = node.get("bodies") or []
    return _measured(
        "ActiveA component-rooted body tree",
        {"root": tree.get("root"), "component": node.get("component"),
         "body_count": node.get("body_count"),
         "bodies": [(b.get("name"), bool(b.get("handle"))) for b in bodies]},
        tree.get("root") == _HOLE_ACTIVE and tree.get("truncated") is False
        and node.get("component") == _HOLE_ACTIVE and node.get("body_count") == 1
        and [b.get("name") for b in bodies] == ["Body1"]
        and isinstance(bodies[0].get("handle"), str) and bodies[0]["handle"]
        and bodies[0].get("is_solid") is True)


def _hole_active_handle(p):
    """The exact A-body handle from the component-rooted tree."""
    bodies = ((p.get("tree") or {}).get("tree") or {}).get("bodies") or []
    hit = [b.get("handle") for b in bodies if b.get("name") == "Body1"]
    if len(hit) != 1 or not isinstance(hit[0], str) or not hit[0]:
        raise AssertionError(f"ActiveA Body1 handle is not unique: {hit!r}")
    return hit[0]


def _hole_inspect_args(ctx, key):
    """Mass-and-bounds arguments consuming one retained body handle."""
    return {"target": _ctx_get(ctx, key, "hole fixture body"), "include": ["default", "mass"],
            "units": "mm", "accuracy": "very_high"}


def _hole_cylinder_args(ctx, key):
    """Cylinder-census arguments consuming one retained body handle."""
    return {"target": _ctx_get(ctx, key, "hole fixture body"), "kind": "cylinder_face",
            "radius": 2, "units": "mm", "max_results": 10}


def _hole_point_while_active_a(x, y, z):
    """A B-authored point before the layout wrapper adds active A's offset."""
    return [_px(_HOLE_HOST, x) - _px(_HOLE_ACTIVE, 0.0),
            _py(_HOLE_HOST, y) - _py(_HOLE_ACTIVE, 0.0), z]


def _scoped_top_args(ctx):
    """The upper-face acquisition at the scoped bore point."""
    return {"target": _ctx_get(ctx, "hh_upper", "HoleHostB upper body"),
            "kind": "planar_face",
            "nearest_to": _hole_point_while_active_a(_HOLE_X0 + 10, 10, 10),
            "units": "mm", "max_results": 1}


def _control_top_args(ctx):
    """The upper-face acquisition at the positive-control bore point."""
    return {"target": _ctx_get(ctx, "hh_upper", "HoleHostB upper body"),
            "kind": "planar_face",
            "nearest_to": _hole_point_while_active_a(_HOLE_X0 + 5, 5, 10),
            "units": "mm", "max_results": 1}


def _scoped_bore_args(ctx):
    """The complete post-write bore census on the retained upper-body handle."""
    return {"target": _ctx_get(ctx, "hh_upper", "HoleHostB upper body"),
            "kind": "cylinder_face", "radius": 2, "units": "mm", "max_results": 1}


def _scoped_hole_args(ctx):
    """The upper-only hole arguments while ActiveA remains the runtime edit target."""
    return {"face": _ctx_get(ctx, "hh_top", "HoleHostB upper top face"),
            "hole_type": "simple", "diameter": "4 mm", "extent": "through",
            "points_space": "world", "units": "mm",
            "points": [_hole_point_while_active_a(_HOLE_X0 + 10, 10, 10)],
            "target_bodies": [_ctx_get(ctx, "hh_upper", "HoleHostB upper body")]}


def _unscoped_hole_args(ctx):
    """The positive-control hole arguments with target_bodies intentionally absent."""
    return {"face": _ctx_get(ctx, "hh_top_control", "HoleHostB upper top face"),
            "hole_type": "simple", "diameter": "4 mm", "extent": "through",
            "points_space": "world", "units": "mm",
            "points": [_hole_point_while_active_a(_HOLE_X0 + 5, 5, 10)]}


def _hole_created(scoped):
    """model_hole reports the face host and the exact participant mode."""
    def check(p):
        scope = p.get("scoped_to_bodies")
        scope_ok = (scope == [f"{_HOLE_HOST}:1:Body1"] if scoped
                    else "scoped_to_bodies" not in p)
        suffix = "1" if scoped else "2"
        return _measured(
            "scoped hole" if scoped else "unscoped positive-control hole",
            {"feature": p.get("feature"), "host": p.get("host_component"),
             "sketch": p.get("placement_sketch"), "scope": scope},
            p.get("holes") == 1 and p.get("holes_verified") is True
            and p.get("host_from_face") is True and p.get("host_verified") is True
            and p.get("host_component") == _HOLE_HOST
            and p.get("world_lift_component") == _HOLE_HOST
            and p.get("points_space") == "world" and p.get("feature") == f"Hole{suffix}"
            and p.get("placement_sketch") == f"Sketch{int(suffix) + 2}" and scope_ok)
    return check


def _hole_timeline(control):
    """The complete bounded timeline locates each hole and placement sketch in B."""
    def check(p):
        timeline = p.get("timeline") or {}
        rows = timeline.get("timeline") or []
        required = {("Sketch3", "Sketch", _HOLE_HOST),
                    ("Hole1", "HoleFeature", _HOLE_HOST)}
        if control:
            required |= {("Sketch4", "Sketch", _HOLE_HOST),
                         ("Hole2", "HoleFeature", _HOLE_HOST)}
        got = {(r.get("name"), r.get("type"), r.get("component")) for r in rows}
        active_a_holes = [r.get("name") for r in rows
                          if r.get("component") == _HOLE_ACTIVE
                          and r.get("type") == "HoleFeature"]
        count, returned = timeline.get("count"), timeline.get("returned")
        return _measured(
            "complete bounded hole-host timeline",
            {"count": count, "returned": returned, "required": sorted(required - got),
             "active_a_holes": active_a_holes, "truncated": timeline.get("truncated")},
            isinstance(count, int) and count <= 2000 and returned == count == len(rows)
            and timeline.get("truncated") is not True and required <= got
            and not active_a_holes)
    return check


def _radius_body_size(label, diameter_mm):
    """A 10 mm cylinder whose diameter is precise enough to settle the radius boundary."""
    def check(p):
        return _measured(
            label,
            {"x": p.get("x"), "y": p.get("y"), "z": p.get("z")},
            _near(p.get("x"), diameter_mm, _RADIUS_DIAMETER_TOL_MM)
            and _near(p.get("y"), diameter_mm, _RADIUS_DIAMETER_TOL_MM)
            and _near(p.get("z"), 10.0, 0.01))
    return check


def _radius_filtered_handle(ctx, filtered_key, index):
    """One actual radius-filtered handle for independent body-size readback."""
    filtered = _ctx_get(ctx, filtered_key, "the radius-filtered handles")
    if not isinstance(filtered, list):
        raise AssertionError(f"filtered handles {filtered!r} are not a list")
    if not 0 <= index < len(filtered):
        raise AssertionError(f"filtered handle index {index} is outside {len(filtered)} handles")
    return {"target": filtered[index], "units": "mm"}


def _interference_pin(ctx, args):
    """Add the exact owned-document pin to one interference-fixture write."""
    return {**args, "expect_document": _ctx_get(
        ctx, "interference_scratch", "the interference scratch document")}


def _interference_pair_named(p):
    """assembly_inspect_interference on the Peg/BlockA/BlockB rig: each of Peg's two instances
    interferes with its OWN block, named exactly - no '(or N more instance(s))' guess."""
    rows = (p.get("measured") or {}).get("interferences") or []
    by_pair = {tuple(sorted([r.get("occurrence_one"), r.get("occurrence_two")])): r for r in rows}
    a = by_pair.get(tuple(sorted(["Peg:1", "BlockA:1"])))
    b = by_pair.get(tuple(sorted(["Peg:2", "BlockB:1"])))
    no_candidates = not any("candidates" in k for r in rows for k in r)
    return _measured(
        "Peg:1 x BlockA:1 and Peg:2 x BlockB:1 named exactly, each with its own volume",
        {"pairs": sorted(by_pair), "vol_a": a and a.get("overlap_volume_cm3"),
         "vol_b": b and b.get("overlap_volume_cm3")},
        a is not None and b is not None and no_candidates
        and isinstance(a.get("overlap_volume_cm3"), (int, float)) and a["overlap_volume_cm3"] > 0
        and isinstance(b.get("overlap_volume_cm3"), (int, float)) and b["overlap_volume_cm3"] > 0)


def _combine_story_address(p):
    """Return the story handle while recording the open-document baseline."""
    _RECALL["combine_open_before"] = p.get("open_count")
    return _home_address(p)


def _combine_owned_active(key):
    """The new owned document is the sole active row and opened beside the story."""
    def check(p):
        active = p.get("active") or {}
        rows = [r for r in (p.get("open_documents") or []) if r.get("is_active")]
        return _measured(
            "owned combine document active at its exact handle",
            {"active": active.get("document_handle"), "owned": _RECALL.get(key),
             "open_count": p.get("open_count"), "before": _RECALL.get("combine_open_before")},
            len(rows) == 1 and active.get("document_handle") == _RECALL.get(key)
            and rows[0].get("document_handle") == _RECALL.get(key)
            and p.get("open_count") == _RECALL.get("combine_open_before") + 1)
    return check


def _combine_story_restored(p):
    """The owned document is gone and the original story handle is active again."""
    active = p.get("active") or {}
    rows = [r for r in (p.get("open_documents") or []) if r.get("is_active")]
    return _measured(
        "combine scratch closed and story document restored",
        {"active": active.get("document_handle"), "story": _RECALL.get("combine_story"),
         "open_count": p.get("open_count"), "before": _RECALL.get("combine_open_before")},
        len(rows) == 1 and active.get("document_handle") == _RECALL.get("combine_story")
        and rows[0].get("document_handle") == _RECALL.get("combine_story")
        and p.get("open_count") == _RECALL.get("combine_open_before"))


def _combine_census(label, bodies, low, high, volume, kind="design"):
    """A complete body census with exact names, lump counts, total volume and bounds."""
    expected = sorted((name, body_volume, 1) for name, body_volume, _lo, _hi in bodies)

    def check(p):
        mass = p.get("mass") or {}
        rows = mass.get("per_body") or []
        got = sorted((r.get("body"), r.get("volume"), r.get("lump_count")) for r in rows)
        bounds = ((p.get("min_point") or {}), (p.get("max_point") or {}))
        bounds_ok = all(_near(point.get(axis), want, 0.01)
                        for point, wanted in zip(bounds, (low, high))
                        for axis, want in zip(("x", "y", "z"), wanted))
        return _measured(
            label + " complete body census",
            {"count": mass.get("per_body_count"), "returned": len(rows),
             "truncated": mass.get("per_body_truncated"), "bodies": got,
             "volume": mass.get("volume"), "min": bounds[0], "max": bounds[1]},
            p.get("kind") == kind and p.get("units") == "mm"
            and mass.get("accuracy_used") == "very_high"
            and mass.get("per_body_count") == len(expected) == len(rows)
            and mass.get("per_body_truncated") is False and got == expected
            and _near(mass.get("volume"), volume, 0.001) and bounds_ok)
    return check


def _combine_body(label, low, high, volume):
    """One result or restored body has exact bounds, volume and one connected lump."""
    def check(p):
        mass = p.get("mass") or {}
        bounds = ((p.get("min_point") or {}), (p.get("max_point") or {}))
        bounds_ok = all(_near(point.get(axis), want, 0.01)
                        for point, wanted in zip(bounds, (low, high))
                        for axis, want in zip(("x", "y", "z"), wanted))
        return _measured(
            label + " body bounds and volume",
            {"kind": p.get("kind"), "units": p.get("units"), "lumps": p.get("lump_count"),
             "volume": mass.get("volume"), "min": bounds[0], "max": bounds[1]},
            p.get("kind") == "body" and p.get("units") == "mm"
            and mass.get("accuracy_used") == "very_high" and p.get("lump_count") == 1
            and _near(mass.get("volume"), volume, 0.001) and bounds_ok)
    return check


def _combine_join_outcome(outcome, result_bodies, result_lumps):
    """The combine response reports one complete result/lump classification."""
    def check(p):
        note = p.get("note") or ""
        note_ok = ("partially fused" in note and "fused NOTHING" not in note
                   if outcome == "partial" else
                   ("fused NOTHING" in note if outcome == "none" else "WARNING" not in note))
        return _measured(
            outcome + " join result/lump report",
            {"outcome": p.get("fusion_outcome"), "fused": p.get("fused"),
             "body_count": p.get("result_body_count"),
             "bodies_complete": p.get("result_bodies_complete"),
             "input_lumps": p.get("input_lump_total"),
             "result_lumps": p.get("result_lump_total"), "feature": p.get("feature")},
            p.get("fusion_outcome") == outcome
            and p.get("fused") is (outcome != "none")
            and p.get("result_body_count") == result_bodies
            and p.get("result_bodies_complete") is True
            and p.get("input_lump_total") == 3 and p.get("result_lump_total") == result_lumps
            and p.get("feature") == "Combine1" and note_ok)
    return check


def _combine_timeline(prefix, joined, component=None):
    """The bounded timeline is exactly the authored host, bodies and optional combine."""
    wanted = [(f"{prefix}{i}", "Sketch", component) if j % 2 == 0
              else (f"Extrude{i + 1}", "ExtrudeFeature", component)
              for i in range(3) for j in range(2)]
    if component:
        wanted.insert(0, (" " + component + ":1", "Occurrence", None))
    if joined:
        wanted.append(("Combine1", "CombineFeature", component))

    def check(p):
        timeline = p.get("timeline") or {}
        rows = timeline.get("timeline") or []
        got = [(r.get("name"), r.get("type"), r.get("component") if component else None)
               for r in rows]
        return _measured(
            ("complete combine timeline" if joined else
             ("restored combine timeline" if component else "restored six-row timeline")),
            {"count": timeline.get("count"), "returned": timeline.get("returned"),
             "truncated": timeline.get("truncated"), "rows": got},
            timeline.get("count") == timeline.get("returned") == len(wanted) == len(rows)
            and timeline.get("truncated") is not True and got == wanted)
    return check


def _combine_deleted(p, index=6):
    """Only the authored Combine1 feature at the expected timeline index was deleted."""
    return _measured(
        "Combine1 deleted by exact feature name",
        {"deleted": p.get("deleted"), "feature": p.get("feature"),
         "index": p.get("index"), "entity_type": p.get("entity_type")},
        p.get("deleted") is True and p.get("feature") == "Combine1"
        and p.get("index") == index and p.get("entity_type") == "CombineFeature")


def _combine_nonroot_component(p):
    """The non-root host reports its transformed occurrence and active component."""
    position = p.get("position") or {}
    return _measured(
        "transformed non-root combine host",
        {"occurrence": p.get("occurrence"), "component": p.get("component"),
         "position": position, "rotate_deg": p.get("rotate_deg"),
         "rotate_axis": p.get("rotate_axis"), "activated": p.get("activated")},
        p.get("created") is True and p.get("occurrence") == "JoinProxyHost:1"
        and p.get("component") == "JoinProxyHost"
        and p.get("full_path") == "JoinProxyHost:1" and p.get("units") == "mm"
        and [position.get(axis) for axis in ("x", "y", "z")] == [100, 40, 30]
        and _near(p.get("rotate_deg"), 90, 0.001) and p.get("rotate_axis") == "z"
        and p.get("activated") is True)


def _combine_face(position):
    """One planar-face handle was acquired at the expected world position."""
    def check(p):
        matches = p.get("matches") or []
        got = matches[0] if len(matches) == 1 else {}
        return _measured(
            "qualified non-root planar face",
            {"count": p.get("match_count"), "returned": p.get("returned"),
             "kind": got.get("kind"), "position": got.get("position")},
            p.get("units") == "mm" and p.get("match_count") == 6
            and p.get("returned") == len(matches) == 1
            and got.get("kind") == "planar_face"
            and isinstance(got.get("handle"), str) and bool(got["handle"])
            and len(got.get("position") or []) == len(position)
            and all(_near(a, b, 0.01) for a, b in zip(got["position"], position))
            and len(got.get("normal") or []) == 3
            and all(_near(a, b, 0.001) for a, b in zip(got["normal"], (0, 0, 1))))
    return check


def _combine_pin(ctx, key, args):
    """Add the exact owned-document pin to one write."""
    return {**args, "expect_document": _ctx_get(ctx, key, "the combine scratch document")}


def _combine_inspect(target=""):
    """The canonical bounds, mass and complete per-body read arguments."""
    args = {"include": ["default", "mass"], "units": "mm", "accuracy": "very_high"}
    if target:
        args["target"] = target
    else:
        args["per_body"] = True
    return args


def _combine_case_rows(case, rectangles, before, after):
    """Build one isolated root-parametric complete/partial/none combine control."""
    owned = "combine_" + case + "_doc"
    prefix = "Join" + case.title()
    low = tuple(min(body[2][i] for body in before) for i in range(3))
    high = tuple(max(body[3][i] for body in before) for i in range(3))
    before_volume = sum(body[1] for body in before)
    after_low = tuple(min(body[2][i] for body in after) for i in range(3))
    after_high = tuple(max(body[3][i] for body in after) for i in range(3))
    after_volume = sum(body[1] for body in after)
    rows = [
        ("doc_new",
         lambda c: {"expect_document": _ctx_get(c, "combine_story", "the story document")},
         _new_document, (owned, _recall(owned, lambda p: p["document_handle"]))),
        ("doc_get", {}, _combine_owned_active(owned), None),
    ]
    if case == "complete":
        # This scratch document never enters Manufacture, so the CAM-product refusal and the
        # library listing that needs no such product make a discriminating PAIR on it: the machine
        # catalog hangs off CAMManager.libraryManager, which the document does not own.
        rows += [
            ("cam_get", {}, _refused("no CAM"), None),
            ("cam_get", {"include": ["machines"], "vendor": "Haas", "machine_type": "milling"},
             lambda p: p.get("machines", {}).get("count", 0) > 0 and "setups" not in p, None),
        ]
    for i, (x1, x2) in enumerate(rectangles):
        rows += [
            ("sketch_create",
             lambda c, name=f"{prefix}{i}": _combine_pin(
                 c, owned, {"plane": "xy", "name": name}), "ok", None),
            ("sketch_add_geometry",
             lambda c, name=f"{prefix}{i}", x1=x1, x2=x2: _combine_pin(
                 c, owned, {"geometry": [{"kind": "rectangle", "x1": x1, "y1": 0,
                                          "x2": x2, "y2": 10}],
                            "sketch_name": name}), "ok", None),
            ("model_extrude",
             lambda c, name=f"{prefix}{i}": _combine_pin(
                 c, owned, {"sketch_name": name, "profile_index": 0,
                            "distance": 10, "operation": "new"}), _extruded, None),
        ]
    if case == "complete":
        rows += [
            ("find_geometry", {"target": "Body1", "kind": "planar_face",
                               "nearest_to": [5, 5, 10], "max_results": 1},
             "ok", _fg("combine_self_a")),
            ("find_geometry", {"target": "Body1", "kind": "planar_face",
                               "nearest_to": [5, 5, 0], "max_results": 1},
             "ok", _fg("combine_self_b")),
            ("model_combine",
             lambda c: _combine_pin(
                 c, owned, {"target": _ctx_get(c, "combine_self_a", "combine target"),
                            "tools": [_ctx_get(c, "combine_self_b", "the same body again")],
                            "operation": "join"}),
             _refused("same as the target", "distinct bodies"), None),
        ]
    rows += [
        ("model_inspect", _combine_inspect(),
         _combine_census(case + " before", before, low, high, before_volume), None),
    ]
    rows += [("model_inspect", _combine_inspect(name),
              _combine_body(case + " before " + name, body_low, body_high, volume), None)
             for name, volume, body_low, body_high in before]
    rows += [
        ("model_combine",
         lambda c: _combine_pin(c, owned, {"target": "Body1", "tools": ["Body2", "Body3"],
                                                   "operation": "join", "keep_tools": False,
                                                   "new_component": False}),
         _combine_join_outcome(case, len(after), len(after)), None),
        ("model_inspect", _combine_inspect(),
         _combine_census(case + " after", after, after_low, after_high, after_volume), None),
    ]
    rows += [("model_inspect", _combine_inspect(name),
              _combine_body(case + " after " + name, body_low, body_high, volume), None)
             for name, volume, body_low, body_high in after]
    rows += [
        ("design_get", {"include": ["timeline"], "max_results": 7},
         _combine_timeline(prefix, True), None),
        ("design_delete_feature",
         lambda c: _combine_pin(c, owned, {"feature": "Combine1"}), _combine_deleted, None),
        ("model_inspect", _combine_inspect(),
         _combine_census(case + " restored", before, low, high, before_volume), None),
    ]
    rows += [("model_inspect", _combine_inspect(name),
              _combine_body(case + " restored " + name, body_low, body_high, volume), None)
             for name, volume, body_low, body_high in before]
    rows += [
        ("design_get", {"include": ["timeline"], "max_results": 6},
         _combine_timeline(prefix, False), None),
        ("doc_activate",
         lambda c: {"name": _ctx_get(c, "combine_story", "the story document"),
                    "expect_document": _ctx_get(c, owned, "the combine scratch document")},
         "ok", None),
        ("doc_close",
         lambda c: {"name": _ctx_get(c, owned, "the combine scratch document"),
                    "save_changes": False,
                    "expect_document": _ctx_get(c, "combine_story", "the story document")},
         _document_closed, None),
        ("doc_get", {}, _combine_story_restored, None),
    ]
    return rows


_COMBINE_COMPLETE = _combine_case_rows(
    "complete", ((0, 20), (10, 30), (20, 40)),
    (("Body1", 2000.0, (0, 0, 0), (20, 10, 10)),
     ("Body2", 2000.0, (10, 0, 0), (30, 10, 10)),
     ("Body3", 2000.0, (20, 0, 0), (40, 10, 10))),
    (("Body1", 4000.0, (0, 0, 0), (40, 10, 10)),))

_COMBINE_PARTIAL = _combine_case_rows(
    "partial", ((0, 20), (10, 30), (50, 60)),
    (("Body1", 2000.0, (0, 0, 0), (20, 10, 10)),
     ("Body2", 2000.0, (10, 0, 0), (30, 10, 10)),
     ("Body3", 1000.0, (50, 0, 0), (60, 10, 10))),
    (("Body1", 3000.0, (0, 0, 0), (30, 10, 10)),
     ("Body4", 1000.0, (50, 0, 0), (60, 10, 10))))

_COMBINE_NONE = _combine_case_rows(
    "none", ((0, 20), (30, 50), (60, 80)),
    (("Body1", 2000.0, (0, 0, 0), (20, 10, 10)),
     ("Body2", 2000.0, (30, 0, 0), (50, 10, 10)),
     ("Body3", 2000.0, (60, 0, 0), (80, 10, 10))),
    (("Body1", 2000.0, (0, 0, 0), (20, 10, 10)),
     ("Body4", 2000.0, (30, 0, 0), (50, 10, 10)),
     ("Body5", 2000.0, (60, 0, 0), (80, 10, 10))))


def _combine_nonroot_partial_rows():
    """Build one transformed non-root partial join with proxy-face tool handles."""
    owned = "combine_nonroot_partial_doc"
    component = "JoinProxyHost"
    occurrence = component + ":1"
    before = (
        ("Body1", 2000.0, (90, 40, 30), (100, 60, 40)),
        ("Body2", 2000.0, (90, 50, 30), (100, 70, 40)),
        ("Body3", 1000.0, (90, 90, 30), (100, 100, 40)),
    )
    after = (
        ("Body1", 3000.0, (90, 40, 30), (100, 70, 40)),
        ("Body4", 1000.0, (90, 90, 30), (100, 100, 40)),
    )
    rows = [
        ("doc_new",
         lambda c: {"expect_document": _ctx_get(c, "combine_story", "the story document")},
         _new_document, (owned, _recall(owned, lambda p: p["document_handle"]))),
        ("doc_get", {}, _combine_owned_active(owned), None),
        ("model_create_component",
         lambda c: _combine_pin(
             c, owned, {"name": component, "x": 100, "y": 40, "z": 30,
                        "rotate_deg": 90, "rotate_axis": "z", "activate": True}),
         _combine_nonroot_component, None),
    ]
    for i, (x1, x2) in enumerate(((0, 20), (10, 30), (50, 60))):
        rows += [
            ("sketch_create",
             lambda c, name=f"ProxyBox{i}": _combine_pin(
                 c, owned, {"plane": "xy", "name": name}), "ok", None),
            ("sketch_add_geometry",
             lambda c, name=f"ProxyBox{i}", x1=x1, x2=x2: _combine_pin(
                 c, owned, {"geometry": [{"kind": "rectangle", "x1": x1, "y1": 0,
                                          "x2": x2, "y2": 10}],
                            "sketch_name": name}), "ok", None),
            ("model_extrude",
             lambda c, name=f"ProxyBox{i}": _combine_pin(
                 c, owned, {"sketch_name": name, "profile_index": 0,
                            "distance": 10, "operation": "new"}), _extruded, None),
        ]
    rows += [
        ("model_inspect", {**_combine_inspect(occurrence), "per_body": True},
         _combine_census("non-root partial before", before,
                         (90, 40, 30), (100, 100, 40), 5000.0, "occurrence"), None),
    ]
    rows += [
        ("model_inspect", _combine_inspect(occurrence + ":" + name),
         _combine_body("non-root partial before " + name, low, high, volume), None)
        for name, volume, low, high in before
    ]
    rows += [
        ("find_geometry", {"target": occurrence + ":Body2", "kind": "planar_face",
                           "nearest_to": [95, 60, 40], "max_results": 1},
         _combine_face((95, 60, 40)), _fg("combine_nonroot_tool_a")),
        ("find_geometry", {"target": occurrence + ":Body3", "kind": "planar_face",
                           "nearest_to": [95, 95, 40], "max_results": 1},
         _combine_face((95, 95, 40)), _fg("combine_nonroot_tool_b")),
        ("model_combine",
         lambda c: _combine_pin(
             c, owned, {"target": occurrence + ":Body1",
                        "tools": [_ctx_get(c, "combine_nonroot_tool_a", "Body2 planar face"),
                                  _ctx_get(c, "combine_nonroot_tool_b", "Body3 planar face")],
                        "operation": "join", "keep_tools": False, "new_component": False}),
         _combine_join_outcome("partial", 2, 2), None),
        ("model_inspect", {**_combine_inspect(occurrence), "per_body": True},
         _combine_census("non-root partial after", after,
                         (90, 40, 30), (100, 100, 40), 4000.0, "occurrence"), None),
    ]
    rows += [
        ("model_inspect", _combine_inspect(occurrence + ":" + name),
         _combine_body("non-root partial after " + name, low, high, volume), None)
        for name, volume, low, high in after
    ]
    rows += [
        ("design_get", {"include": ["timeline"], "max_results": 8},
         _combine_timeline("ProxyBox", True, component), None),
        ("design_delete_feature",
         lambda c: _combine_pin(c, owned, {"feature": "Combine1"}),
         lambda p: _combine_deleted(p, 7), None),
        ("model_inspect", {**_combine_inspect(occurrence), "per_body": True},
         _combine_census("non-root partial restored", before,
                         (90, 40, 30), (100, 100, 40), 5000.0, "occurrence"), None),
    ]
    rows += [
        ("model_inspect", _combine_inspect(occurrence + ":" + name),
         _combine_body("non-root partial restored " + name, low, high, volume), None)
        for name, volume, low, high in before
    ]
    rows += [
        ("design_get", {"include": ["timeline"], "max_results": 7},
         _combine_timeline("ProxyBox", False, component), None),
        ("doc_activate",
         lambda c: {"name": _ctx_get(c, "combine_story", "the story document"),
                    "expect_document": _ctx_get(c, owned, "the combine scratch document")},
         "ok", None),
        ("doc_close",
         lambda c: {"name": _ctx_get(c, owned, "the combine scratch document"),
                    "save_changes": False,
                    "expect_document": _ctx_get(c, "combine_story", "the story document")},
         _document_closed, None),
        ("doc_get", {}, _combine_story_restored, None),
    ]
    return rows


_COMBINE_NONROOT_PARTIAL = _combine_nonroot_partial_rows()

_REVOLVE_HOST = "RevolveProof"
_REVOLVE_OCCURRENCE = _REVOLVE_HOST + ":1"
_REVOLVE_BASELINE = (
    ("Body1", 1280 * math.pi, (-10, -10, 0), (10, 10, 20)),
    ("Body2", 500 * math.pi, (-5, -5, 0), (5, 5, 20)),
    ("Body3", 80 * math.pi, (28, -2, 0), (32, 2, 20)),
)


def _revolve_story_address(p):
    """Return the story handle while recording the open-document baseline."""
    _RECALL["revolve_open_before"] = p.get("open_count")
    return _home_address(p)


def _revolve_owned_active(p):
    """Require the new revolve document beside the exact story document."""
    active = p.get("active") or {}
    rows = [row for row in (p.get("open_documents") or []) if row.get("is_active")]
    return _measured(
        "owned revolve document active at its exact handle",
        {"active": active.get("document_handle"), "owned": _RECALL.get("revolve_participant_doc"),
         "open_count": p.get("open_count"), "before": _RECALL.get("revolve_open_before")},
        len(rows) == 1
        and active.get("document_handle") == _RECALL.get("revolve_participant_doc")
        and rows[0].get("document_handle") == _RECALL.get("revolve_participant_doc")
        and p.get("open_count") == _RECALL.get("revolve_open_before") + 1)


def _revolve_story_restored(p):
    """Require the revolve document gone and the exact story document active."""
    active = p.get("active") or {}
    rows = [row for row in (p.get("open_documents") or []) if row.get("is_active")]
    return _measured(
        "revolve scratch closed and story document restored",
        {"active": active.get("document_handle"), "story": _RECALL.get("revolve_story"),
         "open_count": p.get("open_count"), "before": _RECALL.get("revolve_open_before")},
        len(rows) == 1 and active.get("document_handle") == _RECALL.get("revolve_story")
        and rows[0].get("document_handle") == _RECALL.get("revolve_story")
        and p.get("open_count") == _RECALL.get("revolve_open_before"))


def _revolve_pin(ctx, args):
    """Add the exact owned-document pin to one revolve-fixture write."""
    return {**args, "expect_document": _ctx_get(
        ctx, "revolve_participant_doc", "the revolve scratch document")}


def _revolve_census(label, bodies):
    """Require one complete participant census with analytical volume and world bounds."""
    expected = {name: (volume, low, high) for name, volume, low, high in bodies}
    low = tuple(min(body[2][i] for body in bodies) for i in range(3))
    high = tuple(max(body[3][i] for body in bodies) for i in range(3))

    def check(p):
        mass = p.get("mass") or {}
        rows = mass.get("per_body") or []
        got = {row.get("body"): row for row in rows}
        bounds = (p.get("min_point") or {}, p.get("max_point") or {})
        bounds_ok = all(_near(point.get(axis), want, 0.01)
                        for point, wanted in zip(bounds, (low, high))
                        for axis, want in zip(("x", "y", "z"), wanted))
        bodies_ok = (
            set(got) == set(expected) and len(rows) == len(expected)
            and all(row.get("is_solid") is True and row.get("lump_count") == 1
                    and _near(row.get("volume"), expected[name][0], 0.001)
                    for name, row in got.items()))
        return _measured(
            label + " revolve participant census",
            {"count": mass.get("per_body_count"), "returned": len(rows),
             "truncated": mass.get("per_body_truncated"),
             "bodies": sorted((name, row.get("volume"), row.get("lump_count"))
                              for name, row in got.items()),
             "volume": mass.get("volume"), "min": bounds[0], "max": bounds[1]},
            p.get("kind") == "occurrence" and p.get("units") == "mm"
            and mass.get("accuracy_used") == "very_high"
            and mass.get("per_body_count") == len(expected) == len(rows)
            and mass.get("per_body_truncated") is False and bodies_ok
            and _near(mass.get("volume"), sum(body[1] for body in bodies), 0.001)
            and bounds_ok)
    return check


def _revolve_cut(profile, feature, result_bodies, scoped, volume_delta_cm3):
    """Require the reported cut scope/results and its material-effect mode."""
    def check(p):
        scope = p.get("scoped_to_bodies")
        scope_ok = (scope == [_REVOLVE_OCCURRENCE + ":Body1"] if scoped
                    else "scoped_to_bodies" not in p)
        if volume_delta_cm3 is None:
            effect_ok = ("volume_delta_cm3" not in p
                         and "body count changed" in (p.get("note") or ""))
        else:
            effect_ok = _near(p.get("volume_delta_cm3"), volume_delta_cm3, 0.000001)
        return _measured(
            profile + " revolve cut result",
            {"feature": p.get("feature"), "operation": p.get("operation"),
             "results": p.get("result_bodies"), "scope": scope,
             "volume_delta_cm3": p.get("volume_delta_cm3")},
            p.get("revolved") is True and p.get("feature") == feature
            and p.get("operation") == "cut" and p.get("sketch") == profile
            and p.get("component") == _REVOLVE_HOST and p.get("axis") == "z-axis"
            and _near(p.get("angle_deg"), 360.0, 0.000001)
            and p.get("result_bodies") == list(result_bodies) and scope_ok and effect_ok)
    return check


def _revolve_deleted(feature):
    """Require deletion of the exact temporary RevolveFeature."""
    def check(p):
        return _measured(
            feature + " deleted",
            {"deleted": p.get("deleted"), "feature": p.get("feature"),
             "index": p.get("index"), "entity_type": p.get("entity_type")},
            p.get("deleted") is True and p.get("feature") == feature
            and p.get("index") == 9 and p.get("entity_type") == "RevolveFeature")
    return check


def _revolve_groove_face(p):
    """Require the new radius-9 groove face at world z=7.5 mm."""
    matches = p.get("matches") or []
    groove = [row for row in matches
              if _near(row.get("radius"), 9.0, 0.001)
              and len(row.get("position") or []) == 3
              and _near(row["position"][2], 7.5, 0.01)]
    return _measured(
        "radius-9 groove face at its axial position",
        {"count": p.get("match_count"), "returned": p.get("returned"),
         "groove": [(row.get("radius"), row.get("position"), row.get("area"))
                    for row in groove], "truncated": p.get("truncated")},
        p.get("units") == "mm" and p.get("match_count") == p.get("returned") == len(matches)
        and p.get("truncated") is not True and len(groove) == 1
        and _near(groove[0].get("area"), 90 * math.pi, 0.001))


def _revolve_body_rows(label, bodies):
    """Build separate mass-and-bounds reads for every expected participant body."""
    return [
        ("model_inspect", _combine_inspect(_REVOLVE_OCCURRENCE + ":" + name),
         _combine_body(label + " " + name, low, high, volume), None)
        for name, volume, low, high in bodies
    ]


def _revolve_participant_rows():
    """Build one isolated scoped, unscoped and splitting revolve-cut proof."""
    scoped = (
        ("Body1", 1055 * math.pi, (-10, -10, 0), (10, 10, 20)),
        _REVOLVE_BASELINE[1], _REVOLVE_BASELINE[2])
    unscoped = (
        scoped[0],
        ("Body2", 420 * math.pi, (-5, -5, 0), (5, 5, 20)),
        _REVOLVE_BASELINE[2])
    split = (
        ("Body1", 320 * math.pi, (-10, -10, 0), (10, 10, 5)),
        _REVOLVE_BASELINE[1], _REVOLVE_BASELINE[2],
        ("Body4", 640 * math.pi, (-10, -10, 10), (10, 10, 20)))
    rows = [
        ("doc_get", {}, _home_document,
         ("revolve_story", _recall("revolve_story", _revolve_story_address))),
        ("doc_new",
         lambda c: {"expect_document": _ctx_get(c, "revolve_story", "the story document")},
         _new_document, ("revolve_participant_doc", _recall(
             "revolve_participant_doc", lambda p: p["document_handle"]))),
        ("doc_get", {}, _revolve_owned_active, None),
        ("model_create_component",
         lambda c: _revolve_pin(c, {"name": _REVOLVE_HOST, "activate": True}),
         _made_component, None),
        ("sketch_create",
         lambda c: _revolve_pin(c, {"name": "RingProfile", "plane": "xz"}), "ok", None),
        ("sketch_add_geometry",
         lambda c: _revolve_pin(c, {
             "sketch_name": "RingProfile",
             "geometry": [{"kind": "rectangle", "x1": 6, "y1": -20, "x2": 10, "y2": 0}]}),
         "ok", None),
        ("model_revolve",
         lambda c: _revolve_pin(c, {
             "sketch_name": "RingProfile", "axis": "z", "angle_deg": 360}),
         _revolved, None),
        ("sketch_create",
         lambda c: _revolve_pin(c, {"name": "InnerProfile", "plane": "xy"}), "ok", None),
        ("sketch_add_geometry",
         lambda c: _revolve_pin(c, {
             "sketch_name": "InnerProfile",
             "geometry": [{"kind": "circle", "cx": 0, "cy": 0, "radius": 5}]}),
         "ok", None),
        ("model_extrude",
         lambda c: _revolve_pin(c, {
             "sketch_name": "InnerProfile", "distance": 20, "operation": "new"}),
         _extruded, None),
        ("sketch_create",
         lambda c: _revolve_pin(c, {"name": "DecoyProfile", "plane": "xy"}), "ok", None),
        ("sketch_add_geometry",
         lambda c: _revolve_pin(c, {
             "sketch_name": "DecoyProfile",
             "geometry": [{"kind": "circle", "cx": 30, "cy": 0, "radius": 2}]}),
         "ok", None),
        ("model_extrude",
         lambda c: _revolve_pin(c, {
             "sketch_name": "DecoyProfile", "distance": 20, "operation": "new"}),
         _extruded, None),
        ("sketch_create",
         lambda c: _revolve_pin(c, {"name": "GrooveProfile", "plane": "xz"}), "ok", None),
        ("sketch_add_geometry",
         lambda c: _revolve_pin(c, {
             "sketch_name": "GrooveProfile",
             "geometry": [{"kind": "rectangle", "x1": 3, "y1": -10, "x2": 9, "y2": -5}]}),
         "ok", None),
        ("sketch_create",
         lambda c: _revolve_pin(c, {"name": "BandProfile", "plane": "xz"}), "ok", None),
        ("sketch_add_geometry",
         lambda c: _revolve_pin(c, {
             "sketch_name": "BandProfile",
             "geometry": [{"kind": "rectangle", "x1": 0, "y1": -10, "x2": 11, "y2": -5}]}),
         "ok", None),
        ("model_inspect", {**_combine_inspect(_REVOLVE_OCCURRENCE), "per_body": True},
         _revolve_census("baseline", _REVOLVE_BASELINE), None),
    ]
    rows += _revolve_body_rows("baseline", _REVOLVE_BASELINE)
    cases = (
        ("scoped", "GrooveProfile", "Revolve2", ("Body1",), True,
         -225 * math.pi / 1000, scoped),
        ("unscoped", "GrooveProfile", "Revolve3", ("Body1", "Body2"), False,
         -305 * math.pi / 1000, unscoped),
        ("split", "BandProfile", "Revolve4", ("Body1", "Body4"), True, None, split),
    )
    for label, profile, feature, result_bodies, is_scoped, delta, after in cases:
        def cut_args(c, profile=profile, is_scoped=is_scoped):
            args = {"sketch_name": profile, "component": _REVOLVE_HOST, "axis": "z",
                    "operation": "cut", "angle_deg": 360}
            if is_scoped:
                args["target_bodies"] = [_REVOLVE_HOST + ":Body1"]
            return _revolve_pin(c, args)

        rows += [
            ("model_revolve", cut_args,
             _revolve_cut(profile, feature, result_bodies, is_scoped, delta), None),
            ("model_inspect", {**_combine_inspect(_REVOLVE_OCCURRENCE), "per_body": True},
             _revolve_census(label, after), None),
        ]
        rows += _revolve_body_rows(label, after)
        if profile == "GrooveProfile":
            rows += [
                ("find_geometry", {"target": _REVOLVE_OCCURRENCE + ":Body1",
                                   "kind": "cylinder_face", "units": "mm", "max_results": 100},
                 _revolve_groove_face, None),
            ]
        rows += [
            ("design_delete_feature",
             lambda c, feature=feature: _revolve_pin(c, {"feature": feature}),
             _revolve_deleted(feature), None),
            ("model_inspect", {**_combine_inspect(_REVOLVE_OCCURRENCE), "per_body": True},
             _revolve_census(label + " restored", _REVOLVE_BASELINE), None),
        ]
        rows += _revolve_body_rows(label + " restored", _REVOLVE_BASELINE)
    rows += [
        ("doc_activate",
         lambda c: {"name": _ctx_get(c, "revolve_story", "the story document"),
                    "expect_document": _ctx_get(
                        c, "revolve_participant_doc", "the revolve scratch document")},
         "ok", None),
        ("doc_close",
         lambda c: {"name": _ctx_get(c, "revolve_participant_doc",
                                     "the revolve scratch document"),
                    "save_changes": False,
                    "expect_document": _ctx_get(c, "revolve_story", "the story document")},
         _document_closed, None),
        ("doc_get", {}, _revolve_story_restored, None),
    ]
    return rows


_REVOLVE_PARTICIPANTS = _revolve_participant_rows()

# --- ACT 2: SOLIDS - the parts turn solid, each part its own color (mirrors scenario S2) -------
# The hero solids ride on ACT 1's parametric sketches; the multi-body feature tools that have no
# natural home on the part (draft/mirror/patterns/combine) ride cameo bodies in the SAME
# document, so every one is exercised without contorting the mechanism.
_SOLIDS = [
    # The sketch acts have already drawn the whole scratch field by now, so a whole-model fit is a
    # metre of scenery with the part a speck in it. Frame the profiles the features below consume,
    # and the bracket appears inside the shot instead of off the edge of one.
    _watch(["BracketBody", "BracketStep", "BracketPocket"]),
    ("design_activate_component", {"occurrence": "Bracket:1"}, "ok", None),
    # THE BLOCK, THE STEP AND THE BOSS - three extrudes off the three parametric profiles, each
    # taking its depth from the parameter that states it, so the resize walks the solid as well as
    # the sketches.
    ("model_extrude", {"sketch_name": "BracketBody", "profile_index": 0,
                       "distance": "PartHt - StepDrop"}, _extruded, None),
    # THE BLOCK MEASURED WHERE IT IS BUILT: the two span dimensions are the only thing standing
    # between the driver and the solid, and a dimension that anchored the wrong point leaves a
    # block of the wrong size that nothing downstream would name until the resize act.
    ("model_inspect", {"target": "Bracket:1"},
     lambda p: _measured("the block is PartLen x PartWid x (PartHt - StepDrop)",
                         {"x": p.get("x"), "y": p.get("y"), "z": p.get("z")},
                         _near(p.get("x"), 120.0, 0.1) and _near(p.get("y"), 80.0, 0.1)
                         and _near(p.get("z"), 30.0, 0.1)), None),
    ("model_extrude", {"sketch_name": "BracketStep", "profile_index": 0, "distance": "StepDrop",
                       "operation": "join"}, _extruded, None),
    ("model_extrude", {"sketch_name": "BracketBoss", "profile_index": 0, "distance": "PartHt / 8",
                       "operation": "join"}, _extruded, None),
    # WHAT A PARAMETER CHANGE TOUCHES, read instead of simulated: PartHt drives StepDrop and BoreDia
    # directly, reaches the boss diameter's own sketch dimension a hop further out, and the trace
    # names the extrudes that consume those sketches - the recompute an agent would otherwise have
    # to drive to find out. trace_depth=3 asks for the deep rows the default summarizes.
    ("param_get", {"name": "PartHt", "trace": True, "trace_depth": 3},
     _param_traced("PartHt", direct=("StepDrop", "BoreDia")), None),
    # THE POCKET, cut UP from its own floor and out through the step top - the direction with a
    # body in it. It runs one edge break PAST that face so the cut opens the pocket instead of
    # ending coincident with it.
    ("model_extrude", {"sketch_name": "BracketPocket", "profile_index": 0,
                       "distance": "PocketDepth + EdgeBreak", "operation": "cut"}, _extruded, None),
    # A MATERIAL-REMOVAL FEATURE IS JUDGED BY THE MATERIAL: the cut opened a floor 16 mm under the
    # step top, so the pocket floor is a planar face at z=14 that did not exist a step ago. A cut
    # that ran the wrong way, or found no body, leaves no such face.
    ("find_geometry", {"target": "Bracket", "kind": "planar_face", "nearest_to": [-35, 0, 14],
                       "max_results": 1}, _face_up_at(-35, 0, 14, tol=2.0), None),
    # THE POCKET'S CORNER RADII, at the parameter itself: model_fillet takes a radius EXPRESSION,
    # so the created feature holds 'PocketRad' rather than the number that expression evaluates to -
    # which is what lets the recompute in the resize act carry it.
    ("find_geometry", {"target": "Bracket", "kind": "line_edge", "nearest_to": [-50, -22, 22],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("pk_c1")),
    ("find_geometry", {"target": "Bracket", "kind": "line_edge", "nearest_to": [-20, -22, 22],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("pk_c2")),
    ("find_geometry", {"target": "Bracket", "kind": "line_edge", "nearest_to": [-20, 22, 22],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("pk_c3")),
    ("find_geometry", {"target": "Bracket", "kind": "line_edge", "nearest_to": [-50, 22, 22],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("pk_c4")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "pk_c1", "pocket corner one"),
                                          _ctx_get(c, "pk_c2", "pocket corner two"),
                                          _ctx_get(c, "pk_c3", "pocket corner three"),
                                          _ctx_get(c, "pk_c4", "pocket corner four")],
                                "radius": "PocketRad"}, _filleted, None),
    # THE TWO THROUGH BORES, one of them up the boss. model_hole's diameter is an EXPRESSION, so
    # these follow the part's own section where a fillet radius cannot.
    # EVERY hole below places its points in WORLD space. The default 'sketch' frame is the frame of
    # the placement sketch model_hole lays on the chosen face, and that frame is the face's, not the
    # world's; the tool converts a world point through the sketch's own converter and REFUSES one
    # that does not lie on the face, so the z coordinate is the face's own height.
    # Each face is MEASURED before it is drilled: a hole through the wrong face is a hole every
    # read after it still calls a hole. 'nearest_to' ranks by distance to each face's own CENTROID,
    # not to the nearest point on it - measured: a probe sitting ON this top face but off toward
    # its edge lost to the block's L-shaped +Y side wall, whose centroid was nearer - so every
    # probe below is aimed at the centroid the face it wants will have.
    # The raised half spans x[-10,60] and its middle is x=25; the boss standing on it takes a
    # circular bite that pulls the centroid about 1.2 mm back along -x, which the band absorbs.
    ("find_geometry", {"target": "Bracket", "kind": "planar_face", "nearest_to": [25, 0, 40],
                       "max_results": 1}, _face_up_at(25, 0, 40, tol=2.0), _fg("step_top")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "step_top", "the high half of the top"),
                              "hole_type": "simple", "diameter": "BoreDia", "extent": "through",
                              "points_space": "world", "points": [[20, 0, 40]]}, _drilled(1), None),
    ("find_geometry", {"target": "Bracket", "kind": "planar_face", "nearest_to": [45, 0, 45],
                       "max_results": 1}, _face_up_at(45, 0, 45, tol=0.5), _fg("boss_top")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "boss_top", "the boss top"),
                              "hole_type": "simple", "diameter": "BoreDia", "extent": "through",
                              "points_space": "world", "points": [[45, 0, 45]]}, _drilled(1), None),
    # THE MOUNTING PATTERN: four COUNTERBORED holes through the low half of the top in one call -
    # 'holes_verified' counts the drill axes off the created feature, so four here is four.
    # the low half spans x[-60,-10]: its middle is x=-35, and the pocket it lost is centred there
    # too, so removing that opening leaves the centroid where it was.
    ("find_geometry", {"target": "Bracket", "kind": "planar_face", "nearest_to": [-35, 0, 30],
                       "max_results": 1}, _face_up_at(-35, 0, 30, tol=2.0), _fg("low_top")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "low_top", "the low half of the top"),
                              "hole_type": "counterbore", "diameter": "MountDia",
                              "cbore_diameter": "MountDia * 1.8", "cbore_depth": "EdgeBreak",
                              "extent": "through", "points_space": "world",
                              "points": [[-45, 30, 30], [-25, 30, 30],
                                         [-45, -30, 30], [-25, -30, 30]]},
     _drilled(4), None),
    # THE HANDLED EDGES, and the split between the two tools: a fillet RADIUS may be a parameter
    # expression, so the rounded edge carries 'EdgeBreak' itself; a chamfer DISTANCE is judged
    # against the number it was given, so it stays a literal READ from that same parameter.
    ("param_get", {"name": "EdgeBreak"}, _param_read("EdgeBreak", 3),
     ("edge_break", lambda p: p["parameter"]["value"])),
    ("find_geometry", {"target": "Bracket", "kind": "line_edge", "nearest_to": [-10, 0, 40],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("step_lead")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "step_lead", "the step's leading edge")],
                                "radius": "EdgeBreak"}, _filleted, None),
    ("find_geometry", {"target": "Bracket", "kind": "line_edge", "nearest_to": [60, 0, 40],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("step_out")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "step_out", "the step's outboard edge")],
                                 "distance": _ctx_get(c, "edge_break", "the edge break")},
     _chamfered, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # The WCS anchor the CAM setup binds to, at the part's own origin: parametric, so it holds
    # position through the recompute the resize act drives.
    ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "StockCenter"},
     _joint_origin_at("StockCenter", 0, 0, 0), None),
    # THE TWO SOLID BUILDERS THE BRACKET HAS NO HOME FOR, each on a cameo of its own out on the
    # field: a base-to-post loft and a swept boss.
    # a default create with no 'part_number' input: the new component's own partNumber is WRITTEN
    # to its name (the timestamp Fusion mints it with otherwise), read back off the receipt.
    ("model_create_component", {"name": "LoftCameo", "activate": True},
     lambda p: _made_component(p) and _measured("part_number defaults to the component's own name",
                                                 {"part_number": p.get("part_number"),
                                                  "component": p.get("component")},
                                                 p.get("part_number") == p.get("component")), None),
    ("sketch_create", {"plane": "xy", "name": "LoftBase"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 250, "cy": 0, "radius": 16}],
                             "sketch_name": "LoftBase"}, "ok", None),
    ("sketch_get", {"sketch_name": "LoftBase"}, "ok", _prof("loft_base")),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 40, "name": "LoftTopPlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "LoftTopPlane", "name": "LoftTop"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 250, "cy": 0, "radius": 8}],
                             "sketch_name": "LoftTop"}, "ok", None),
    ("sketch_get", {"sketch_name": "LoftTop"}, "ok", _prof("loft_top")),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "loft_base", "the loft's base profile"),
                                           _ctx_get(c, "loft_top", "the loft's top profile")]},
     _lofted, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "SweepCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "SweepPath"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 250, "y1": 0,
                                           "x2": 250, "y2": -50}],
                             "sketch_name": "SweepPath"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SweepProf"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 250, "cy": 0, "radius": 5}],
                             "sketch_name": "SweepProf"}, "ok", None),
    ("model_sweep", {"profile": {"sketch": "SweepProf", "profile_index": 0},
                     "path": "sketch:SweepPath"}, _swept, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # THE GUIDE SCOPE: a sketch name is only unique INSIDE a component, so two components each hold
    # one called 'Spine' and the loft below has to be told which one its rail lives in.
    ("model_create_component", {"name": "GuideRail", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "GuideBase"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 420, "cy": 0, "radius": 16}],
                             "sketch_name": "GuideBase"}, "ok", None),
    ("sketch_get", {"sketch_name": "GuideBase"}, "ok", _prof("guide_base")),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 40, "name": "GuideTopPlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "GuideTopPlane", "name": "GuideTop"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 420, "cy": 0, "radius": 8}],
                             "sketch_name": "GuideTop"}, "ok", None),
    ("sketch_get", {"sketch_name": "GuideTop"}, "ok", _prof("guide_top")),
    # the rail climbs from the base circle's rim to the top circle's: z runs along the xy sketch's
    # own normal, which is where the offset plane above put the top section.
    ("sketch_create", {"plane": "xy", "name": "Spine"}, "ok", None),
    ("sketch_add_3d_line", {"sketch_name": "Spine", "component": "GuideRail",
                            "x1": 436, "y1": 0, "z1": 0, "x2": 428, "y2": 0, "z2": 40},
     "ok", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "GuideDupe", "activate": True}, _made_component, None),
    # The DRAWN sketch carries a name of its own: the framing pass writes a camera row per sketch it
    # sees drawn, and view_set refuses a 'focus' that two sketches answer to.
    ("sketch_create", {"plane": "xy", "name": "DupeMark"}, "ok", None),
    ("sketch_add_3d_line", {"sketch_name": "DupeMark", "component": "GuideDupe",
                            "x1": 520, "y1": 0, "z1": 0, "x2": 520, "y2": 20, "z2": 10},
     "ok", None),
    # the SAME name in the second component, left EMPTY - nothing draws on it, so it is no framing
    # subject. Asserted: a name Fusion had deduped would leave every refusal below testing nothing.
    ("sketch_create", {"plane": "xy", "name": "Spine"},
     lambda p: _measured("the duplicate guide name survived the create",
                         {"sketch_name": p.get("sketch_name"),
                          "rename_warning": p.get("rename_warning")},
                         p.get("sketch_name") == "Spine" and "rename_warning" not in p), None),
    ("design_activate_component", {"occurrence": "GuideRail:1"}, "ok", None),
    # un-scoped, the rail's sketch name answers to two sketches and the loft refuses rather than
    # picking one - naming the scope that narrows the GUIDES, which is the one this call can pass.
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "guide_base", "the guide loft's base"),
                                           _ctx_get(c, "guide_top", "the guide loft's top")],
                              "rails": ["Spine/line:0"]},
     _refused("2 sketches are named 'Spine'", "'guide_component'"), None),
    # a scope that owns no sketch of that name refuses NAMING the scope - 'GuideBase' answers to
    # exactly one sketch design-wide, so dropping the scope would loft along another component's
    # curve and say nothing.
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "guide_base", "the guide loft's base"),
                                           _ctx_get(c, "guide_top", "the guide loft's top")],
                              "rails": ["GuideBase/circle:0"], "guide_component": "GuideDupe"},
     _refused("GuideDupe", "no sketch named 'GuideBase'", "GuideRail"), None),
    # scoped, the same call builds: the rail resolves inside GuideRail, whose 'Spine' is the line
    # that touches both sections (GuideDupe's runs off sideways and would fail the add).
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "guide_base", "the guide loft's base"),
                                           _ctx_get(c, "guide_top", "the guide loft's top")],
                              "rails": ["Spine/line:0"], "guide_component": "GuideRail"},
     lambda p: _measured("loft along the component-scoped guide",
                         {"feature": p.get("feature"), "result_bodies": p.get("result_bodies"),
                          "rails_count": p.get("rails_count")},
                         bool(p.get("feature")) and bool(p.get("result_bodies"))
                         and p.get("rails_count") == 1), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the part is whole: frame IT for the colouring beats, not the metre-wide cameo grid.
    _watch("Bracket:1"),
    ("appearance_set", {"target": "Bracket", "color": "#5E6AD2"}, "ok", None),
    ("appearance_set", {"target": "LoftCameo", "color": "#8A94A6"}, "ok", None),
    ("appearance_set", {"target": "SweepCameo", "color": "#F5A623"}, "ok", None),
    ("model_set_material", {"target": "Bracket:1", "material": "Steel"}, _material_assigned, None),
    # a miss whose catalog carries the actual entry ('Steel AISI 4130 259 QT') alongside several
    # same-prefix decoys ('Steel AISI 1060/1061/...') - the CONTAINING name is what the Nearest
    # list has to lead with, not the decoys difflib's edit distance would rank ahead of it.
    ("model_set_material", {"target": "Bracket:1", "material": "Steel AISI 4130"},
     _refused("Nearest: 'Steel AISI 4130 259 QT'"), None),
    # honest reads on the real part: the boss wall to its own bore, the two of them coaxial, and
    # the part's measured extent.
    ("find_geometry", {"target": "Bracket", "kind": "cylinder_face", "radius": 10,
                       "max_results": 1}, _matched(1, "cylinder_face"), _fg("boss_wall")),
    # the bore INSIDE that boss, pinned by position: the part carries two of this diameter, and the
    # coaxial read below is only a claim about the boss if it picked the boss's own.
    ("find_geometry", {"target": "Bracket", "kind": "cylinder_face", "radius": 6,
                       "nearest_to": [45, 0, 42], "max_results": 1},
     _matched(1, "cylinder_face"), _fg("bore_wall")),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "boss_wall", "the boss wall"),
                                         "b": _ctx_get(c, "bore_wall", "the bore wall")},
     _gap_measured, None),
    ("model_measure_relation", lambda c: {"relation": "coaxial",
                                          "entity_a": _ctx_get(c, "boss_wall", "the boss wall"),
                                          "entity_b": _ctx_get(c, "bore_wall", "the bore wall")},
     _relation_measured("coaxial"), None),
    ("model_inspect", {"target": "Bracket:1"}, _extent_measured, None),
    # THE PART'S ENGINEERING IDENTITY: Fusion mints a part number of its own for every component, so
    # the read comes first and shows what is there; the set then replaces it with the shop's, and
    # the second read is the independent confirmation that the value is the component's own.
    ("design_get", {"include": ["metadata"]}, _component_metadata("Bracket"), None),
    ("design_set_metadata", {"target": "Bracket", "part_number": "FE-BRACKET-001",
                             "description": "Sweep bracket"},
     _metadata_set("Bracket", "FE-BRACKET-001", "Sweep bracket"), None),
    ("design_get", {"include": ["metadata"], "name_filter": "Bracket"},
     _component_metadata("Bracket", part_number="FE-BRACKET-001", description="Sweep bracket"),
     None),
    # PMI CONTENT writes need the Design or Manufacturing Extension (the platform raises
    # "Manufacturing or Design Extension is required" without one - measured on the lapsed install),
    # so the four write rows run entitled and assert the created/edited/deleted values read back off
    # the annotations, while the unentitled variants assert the refusal names the extension and the
    # next step. Reads and the blank-name guards run on either licence.
    ("find_geometry", {"target": "Bracket", "kind": "planar_face", "nearest_to": [-35, 0, 14],
                       "max_results": 1}, _face_up_at(-35, 0, 14, tol=2.0), _fg("pmi_floor")),
    ("pmi_create", lambda c: {"kind": "note", "geometry": [_ctx_get(c, "pmi_floor", "the pocket floor")], "text": "{flatness}0.05", "name": "PmiFlat"},
     _needs(MACHINING_EXTENSION, lambda p: p.get("annotation") == "PmiFlat" and p.get("markup") == "{flatness}0.05" and p.get("kind") == "note"), None),
    ("pmi_create", lambda c: {"kind": "note", "geometry": [_ctx_get(c, "pmi_floor", "the pocket floor")], "text": "{flatness}0.05", "name": "PmiFlat"},
     _unless(MACHINING_EXTENSION, _refused("Extension is required", "pmi_get")), None),
    ("find_geometry", {"target": "Bracket", "kind": "cylinder_face", "radius": 3, "max_results": 1},
     _matched(1, "cylinder_face"), _fg("mount_bore")),
    ("pmi_create", lambda c: {"kind": "hole_note", "geometry": [_ctx_get(c, "mount_bore", "a mounting bore")]},
     _needs(MACHINING_EXTENSION, lambda p: p.get("kind") == "hole_note" and bool(p.get("annotation")) and "<HDIA>" in str(p.get("markup"))), None),
    ("pmi_create", lambda c: {"kind": "hole_note", "geometry": [_ctx_get(c, "mount_bore", "a mounting bore")]},
     _unless(MACHINING_EXTENSION, _refused("Extension is required", "pmi_get")), None),
    ("pmi_get", {"include": ["segments", "detail"]}, "ok", None),
    # an over-cap 'max_results' is CLAMPED, not refused - pmi_get's own contract, since every record
    # it returns crosses the wire whole. The answer still comes back with its census keys.
    ("pmi_get", {"max_results": 99999},
     lambda p: isinstance(p.get("annotations"), list) and "total" in p, None),
    ("pmi_edit", {"action": "set_text", "annotation": "PmiFlat", "text": "{perpendicularity}0.03"},
     _needs(MACHINING_EXTENSION, lambda p: p.get("name") == "PmiFlat" and p.get("markup") == "{perpendicularity}0.03"), None),
    # the blank name is its own guard, ahead of any lookup - on either licence.
    ("pmi_edit", {"action": "hide", "annotation": ""}, "refused", None),
    ("pmi_delete", {"annotation": ""}, "refused", None),
    ("pmi_delete", {"annotation": "PmiFlat"},
     _needs(MACHINING_EXTENSION, lambda p: p.get("deleted") == "PmiFlat" and isinstance(p.get("remaining_pmi"), int)), None),
    # THE DATUM BENCH: one bored block, and every way the API knows of hanging a plane, an axis or a
    # point off it. The modes divide by what they READ, so the bench has to carry all of it - six
    # faces, the linear edges where they meet, the vertices where those meet, and a bore for the
    # curved-face and circular-edge modes. Each row's receipt is the created datum's OWN geometry:
    # every mode returns an object, and only the read-back distinguishes one that landed where the
    # mode says from one that landed anywhere at all.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "DatumBench", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "DBPad"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 200, "y1": 0,
                                           "x2": 260, "y2": 40}],
                             "sketch_name": "DBPad"}, "ok", None),
    ("model_extrude", {"sketch_name": "DBPad", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_top")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "db_top", "bench top face"), "hole_type": "simple",
                              "diameter": "10 mm", "extent": "through", "points": [[230, 20, 0]]},
     _drilled(1), None),
    # the reference set every mode below draws from, acquired once the bore exists so no handle is
    # stale: the top face, the bore, its rim, two coplanar top edges, one vertical edge that MEETS
    # one of them, and three top-face corners that form a triangle.
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_top2")),
    ("find_geometry", {"target": "DatumBench", "kind": "cylinder_face", "nearest_to": [230, 20, 10],
                       "max_results": 1}, "ok", _fg("db_bore")),
    ("find_geometry", {"target": "DatumBench", "kind": "circular_edge", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_rim")),
    ("find_geometry", {"target": "DatumBench", "kind": "line_edge", "nearest_to": [230, 0, 20],
                       "max_results": 1}, "ok", _fg("db_front")),
    ("find_geometry", {"target": "DatumBench", "kind": "line_edge", "nearest_to": [230, 40, 20],
                       "max_results": 1}, "ok", _fg("db_back")),
    ("find_geometry", {"target": "DatumBench", "kind": "line_edge", "nearest_to": [200, 0, 10],
                       "max_results": 1}, "ok", _fg("db_post")),
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [200, 0, 20],
                       "max_results": 1}, "ok", _fg("db_c1")),
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [260, 0, 20],
                       "max_results": 1}, "ok", _fg("db_c2")),
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [200, 40, 20],
                       "max_results": 1}, "ok", _fg("db_c3")),
    # the bore's seam vertex - a point that sits ON the cylindrical face, which is what a tangent
    # plane needs. A box corner is not on the bore, and the tangency has nowhere to land.
    ("find_geometry", {"target": "DatumBench", "kind": "vertex", "nearest_to": [230, 20, 20],
                       "max_results": 1}, "ok", _fg("db_seam")),
    # PLANES. at_angle swings the XY plane about a top edge; three_points spans the three corners;
    # midplane splits XY and the top face, landing halfway up the block; two_edges spans the two
    # coplanar top edges; tangent_at_point rests on the bore wall.
    ("model_construction", lambda c: {"kind": "plane", "mode": "at_angle", "plane": "xy",
                                      "edges": [_ctx_get(c, "db_front", "bench front top edge")],
                                      "angle": 30, "name": "DBAngle"},
     lambda p: _datum("plane")(p) and _measured("swung off the base plane's normal",
                                                {"normal_changed": p.get("normal_changed")},
                                                p.get("normal_changed") is True), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "three_points",
                                      "points": [_ctx_get(c, "db_c1", "bench corner 1"),
                                                 _ctx_get(c, "db_c2", "bench corner 2"),
                                                 _ctx_get(c, "db_c3", "bench corner 3")],
                                      "name": "DBTri"}, _datum("plane"), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "midplane", "plane": "xy",
                                      "plane2": _ctx_get(c, "db_top2", "bench top face"),
                                      "name": "DBMid"}, _datum("plane"), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "two_edges",
                                      "edges": [_ctx_get(c, "db_front", "bench front top edge"),
                                                _ctx_get(c, "db_back", "bench back top edge")],
                                      "name": "DBSpan"}, _datum("plane"), None),
    ("model_construction", lambda c: {"kind": "plane", "mode": "tangent_at_point",
                                      "face": _ctx_get(c, "db_bore", "bench bore"),
                                      "points": [_ctx_get(c, "db_seam", "bench bore seam vertex")],
                                      "name": "DBTangent"}, _datum("plane"), None),
    # AXES. An edge IS an axis; two corners span one; the bore's own centreline; and the normal of
    # the top face taken at a corner sitting on it.
    ("model_construction", lambda c: {"kind": "axis", "mode": "edge",
                                      "axis": _ctx_get(c, "db_front", "bench front top edge"),
                                      "name": "DBEdgeAxis"}, _datum("axis"), None),
    ("model_construction", lambda c: {"kind": "axis", "mode": "two_points",
                                      "points": [_ctx_get(c, "db_c1", "bench corner 1"),
                                                 _ctx_get(c, "db_c3", "bench corner 3")],
                                      "name": "DBSpanAxis"}, _datum("axis"), None),
    ("model_construction", lambda c: {"kind": "axis", "mode": "perpendicular_at_point",
                                      "face": _ctx_get(c, "db_top2", "bench top face"),
                                      "points": [_ctx_get(c, "db_c2", "bench corner 2")],
                                      "name": "DBNormalAxis"},
     lambda p: _datum("axis")(p) and _measured("axis along the face normal",
                                               {"aligned": p.get("aligned_to_face_normal")},
                                               p.get("aligned_to_face_normal") is True), None),
    # a world axis through a coordinate is setByLine, which is DIRECT-edit-only - this design is
    # parametric, so the mode is refused up front with the parametric routes named.
    ("model_construction", {"kind": "axis", "mode": "world", "axis": "x", "x": 200, "y": 0, "z": 0,
                            "name": "DBWorldAxis"}, "refused", None),
    # POINTS. The bore rim's centre; the corner where a top edge meets the post below it; the origin
    # the three world planes share; and where the post pierces XY.
    ("model_construction", lambda c: {"kind": "point", "mode": "circle_center",
                                      "edges": [_ctx_get(c, "db_rim", "bench bore rim")],
                                      "name": "DBBoreCentre"}, _datum("point"), None),
    ("model_construction", lambda c: {"kind": "point", "mode": "two_edges",
                                      "edges": [_ctx_get(c, "db_front", "bench front top edge"),
                                                _ctx_get(c, "db_post", "bench corner post")],
                                      "name": "DBCorner"}, _datum("point"), None),
    ("model_construction", {"kind": "point", "mode": "three_planes", "plane": "xy", "plane2": "xz",
                            "plane3": "yz", "name": "DBOrigin"}, _datum("point"), None),
    ("model_construction", lambda c: {"kind": "point", "mode": "edge_plane",
                                      "edges": [_ctx_get(c, "db_post", "bench corner post")],
                                      "plane": "xy", "name": "DBFoot"}, _datum("point"), None),
    # the coordinate point is setByPoint - direct-edit-only for the same reason as the world axis.
    ("model_construction", {"kind": "point", "mode": "coordinate", "x": 230, "y": 20, "z": 40,
                            "name": "DBCoord"}, "refused", None),
    # THE RELATION VOCABULARY, on the same block. Each relation reports a DIFFERENT measurement -
    # an angle for the alignments, a distance for the fits - and the pairs here are chosen so the
    # geometry, not the tool, settles the verdict: a face is flush with itself, a bore concentric
    # with itself, the top face perpendicular to a wall it meets and touching it along that edge,
    # and 20 mm clear of the floor below it.
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [200, 20, 10],
                       "max_results": 1}, "ok", _fg("db_wall")),
    ("find_geometry", {"target": "DatumBench", "kind": "planar_face", "nearest_to": [230, 20, 0],
                       "max_results": 1}, "ok", _fg("db_floor")),
    ("model_measure_relation", lambda c: {"relation": "perpendicular",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_wall", "bench wall")},
     _relation_passes("perpendicular"), None),
    ("model_measure_relation", lambda c: {"relation": "flush",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_top2", "bench top face")},
     _relation_read("flush", "normal_angle_deg"), None),
    ("model_measure_relation", lambda c: {"relation": "concentric",
                                          "entity_a": _ctx_get(c, "db_bore", "bench bore"),
                                          "entity_b": _ctx_get(c, "db_bore", "bench bore")},
     _relation_read("concentric", "center_distance"), None),
    ("model_measure_relation", lambda c: {"relation": "touching",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_wall", "bench wall")},
     _relation_read("touching", "min_distance"), None),
    ("model_measure_relation", lambda c: {"relation": "clearance",
                                          "entity_a": _ctx_get(c, "db_top2", "bench top face"),
                                          "entity_b": _ctx_get(c, "db_floor", "bench floor")},
     _relation_read("clearance", "min_distance"), None),
    # HOLE-HOST-1: two drillable B solids share z=0, while a separate A plate stays active. The
    # layout pass gives B and A different cells; the hole callables compensate for A's wrapper
    # offset so the world points land in B exactly once while every acquired handle stays unchanged.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": _HOLE_HOST, "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "Plate-upper"}, "ok", None),
    ("sketch_add_geometry",
     {"geometry": [{"kind": "rectangle", "x1": _HOLE_X0, "y1": 0,
                    "x2": _HOLE_X0 + 20, "y2": 20}], "sketch_name": "Plate-upper"}, "ok", None),
    ("model_extrude", {"sketch_name": "Plate-upper", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "Plate-lower"}, "ok", None),
    ("sketch_add_geometry",
     {"geometry": [{"kind": "rectangle", "x1": _HOLE_X0, "y1": 0,
                    "x2": _HOLE_X0 + 20, "y2": 20}], "sketch_name": "Plate-lower"}, "ok", None),
    ("model_extrude", {"sketch_name": "Plate-lower", "profile_index": 0, "distance": -10},
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": _HOLE_ACTIVE, "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "Active-A-Plate"}, "ok", None),
    ("sketch_add_geometry",
     {"geometry": [{"kind": "rectangle", "x1": _HOLE_ACTIVE_X0, "y1": 0,
                    "x2": _HOLE_ACTIVE_X0 + 20, "y2": 20}],
      "sketch_name": "Active-A-Plate"}, "ok", None),
    ("model_extrude", {"sketch_name": "Active-A-Plate", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("design_activate_component", {"occurrence": _HOLE_ACTIVE + ":1"},
     lambda p: (p.get("activated") == _HOLE_ACTIVE + ":1"
                and p.get("active_occurrence") == _HOLE_ACTIVE + ":1"
                and p.get("active_component") == _HOLE_ACTIVE), None),
    # The participant is a fresh body handle from a component-rooted tree read. Component scopes
    # only the tree; the separate timeline reads below use their own explicit full-list bound.
    ("design_get", {"include": ["tree"], "component": _HOLE_HOST,
                    "tree_bodies": True, "max_results": 10},
     _hole_host_tree, ("hh_upper", _hole_upper_handle)),
    ("design_get", {"include": ["tree"], "component": _HOLE_HOST,
                    "tree_bodies": True, "max_results": 10},
     _hole_host_tree, ("hh_lower", _hole_lower_handle)),
    ("design_get", {"include": ["tree"], "component": _HOLE_ACTIVE,
                    "tree_bodies": True, "max_results": 10},
     _hole_active_tree, ("hh_active", _hole_active_handle)),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_upper"),
     _hole_body_state(_HOLE_HOST, (_HOLE_X0, 0, 0), (_HOLE_X0 + 20, 20, 10), 4000.0),
     None),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_lower"),
     _hole_body_state(_HOLE_HOST, (_HOLE_X0, 0, -10), (_HOLE_X0 + 20, 20, 0), 4000.0),
     None),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_active"),
     _hole_body_state(_HOLE_ACTIVE, (_HOLE_ACTIVE_X0, 0, 0),
                      (_HOLE_ACTIVE_X0 + 20, 20, 10), 4000.0), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_upper"),
     _hole_cylinders(_HOLE_HOST, ()), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_lower"),
     _hole_cylinders(_HOLE_HOST, ()), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_active"),
     _hole_cylinders(_HOLE_ACTIVE, ()), None),
    ("find_geometry", _scoped_top_args,
     lambda p: _face_up_at(_px(_HOLE_HOST, _HOLE_X0 + 10),
                           _py(_HOLE_HOST, 10), 10)(p), _fg("hh_top")),
    ("model_hole", _scoped_hole_args, _hole_created(True), None),
    # The scoped cut removes exactly one 4 mm x 10 mm bore from upper B. Lower B and active A keep
    # their original bounds, volume and empty cylinder censuses.
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_upper"),
     _hole_body_state(_HOLE_HOST, (_HOLE_X0, 0, 0), (_HOLE_X0 + 20, 20, 10),
                      4000.0 - _HOLE_VOLUME), None),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_lower"),
     _hole_body_state(_HOLE_HOST, (_HOLE_X0, 0, -10), (_HOLE_X0 + 20, 20, 0), 4000.0),
     None),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_active"),
     _hole_body_state(_HOLE_ACTIVE, (_HOLE_ACTIVE_X0, 0, 0),
                      (_HOLE_ACTIVE_X0 + 20, 20, 10), 4000.0), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_upper"),
     _hole_cylinders(_HOLE_HOST, ((_HOLE_X0 + 10, 10, 5),)), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_lower"),
     _hole_cylinders(_HOLE_HOST, ()), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_active"),
     _hole_cylinders(_HOLE_ACTIVE, ()), None),
    # Reacquire the new bore after the write and consume its real handle. Its 4 x 4 x 10 mm bounds
    # prove radius, depth and world location independently of model_hole's participant echo.
    ("find_geometry", _scoped_bore_args,
     _hole_cylinders(_HOLE_HOST, ((_HOLE_X0 + 10, 10, 5),)), _fg("hh_bore")),
    ("model_inspect", lambda c: {"target": _ctx_get(c, "hh_bore", "fresh scoped bore face"),
                                 "units": "mm"}, _hole_face_bounds, None),
    ("design_get", {"include": ["timeline"], "max_results": 2000},
     _hole_timeline(False), None),
    # Positive control: A is explicitly active again and target_bodies is omitted. Both stacked B
    # bodies lose one bore, proving the lower body was drillable; A still does not change.
    ("design_activate_component", {"occurrence": _HOLE_ACTIVE + ":1"},
     lambda p: (p.get("activated") == _HOLE_ACTIVE + ":1"
                and p.get("active_occurrence") == _HOLE_ACTIVE + ":1"
                and p.get("active_component") == _HOLE_ACTIVE), None),
    ("find_geometry", _control_top_args,
     lambda p: _face_up_at(_px(_HOLE_HOST, _HOLE_X0 + 10),
                           _py(_HOLE_HOST, 10), 10, tol=2.0)(p), _fg("hh_top_control")),
    ("model_hole", _unscoped_hole_args, _hole_created(False), None),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_upper"),
     _hole_body_state(_HOLE_HOST, (_HOLE_X0, 0, 0), (_HOLE_X0 + 20, 20, 10),
                      4000.0 - 2 * _HOLE_VOLUME), None),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_lower"),
     _hole_body_state(_HOLE_HOST, (_HOLE_X0, 0, -10), (_HOLE_X0 + 20, 20, 0),
                      4000.0 - _HOLE_VOLUME), None),
    ("model_inspect", lambda c: _hole_inspect_args(c, "hh_active"),
     _hole_body_state(_HOLE_ACTIVE, (_HOLE_ACTIVE_X0, 0, 0),
                      (_HOLE_ACTIVE_X0 + 20, 20, 10), 4000.0), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_upper"),
     _hole_cylinders(_HOLE_HOST, ((_HOLE_X0 + 5, 5, 5),
                                  (_HOLE_X0 + 10, 10, 5))), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_lower"),
     _hole_cylinders(_HOLE_HOST, ((_HOLE_X0 + 5, 5, -5),)), None),
    ("find_geometry", lambda c: _hole_cylinder_args(c, "hh_active"),
     _hole_cylinders(_HOLE_ACTIVE, ()), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000},
     _hole_timeline(True), None),
    # feature cameos on same-doc scratch bodies (no natural home on the part for these verbs).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "FeatureCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "FCPad"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 200, "y1": 0,
                                           "x2": 240, "y2": 40}],
                             "sketch_name": "FCPad"}, "ok", None),
    ("model_extrude", {"sketch_name": "FCPad", "profile_index": 0, "distance": 20}, _extruded, None),
    _watch("FeatureCameo:1"),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [200, 20, 10], "max_results": 1}, "ok", _fg("fc_side")),
    ("model_draft", lambda c: {"faces": [_ctx_get(c, "fc_side", "cameo side face")], "pull_direction": "xy", "angle_deg": 3},
     _drafted, None),
    # The three PIVOT gates: a write whose pull plane / replacement surface / edge set meets the
    # geometry at its MIDDLE adds exactly what it cuts, so the volume holds to the last bit. Each
    # of these was a false refusal until the boundary read joined the material one.
    ("model_create_component", {"name": "PivotCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PvL"}, "ok", None),
    ("sketch_add_geometry", {"sketch_name": "PvL", "geometry": [
        {"kind": "closed_path", "points": [[300, 0], [340, 0], [340, 20], [320, 20], [320, 40],
                                           [300, 40]]}]}, "ok", None),
    ("model_extrude", {"sketch_name": "PvL", "profile_index": 0, "distance": 30}, _extruded, None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 15, "name": "PvMid"},
     _datum_plane("xy"), None),
    # Each gate gets its OWN face, so none disturbs another and nothing has to be deleted: a fillet
    # standing on a drafted face eats a strip off it, which both spoils the pivot (measured: the
    # volume moves -0.000615 cm3) and consumes the input wrapper (faces_compared 0).
    # ONE-SIDED about a plane CROSSING the face: it pivots there, tilting the face while the volume
    # holds to the last bit - the case the material gate alone refused.
    ("find_geometry", {"target": "PivotCameo", "kind": "planar_face", "nearest_to": [300, 20, 15],
                       "max_results": 1}, "ok", _fg("pv_face")),
    ("model_draft", lambda c: {"faces": [_ctx_get(c, "pv_face", "the face the plane crosses")],
                               "pull_direction": "PvMid", "angle_deg": 5, "tangent_chain": False},
     _drafted_on_its_pivot, None),
    # SYMMETRIC, on the next face round: it SPLITS that face at the plane (faces_drafted 2 from one
    # requested) and, measured here, tapers both halves the same way - so the volume MOVES. This
    # branch carries its own verdict, not an exemption.
    ("find_geometry", {"target": "PivotCameo", "kind": "planar_face", "nearest_to": [320, 0, 15],
                       "max_results": 1}, "ok", _fg("pv_sym_face")),
    ("model_draft", lambda c: {"faces": [_ctx_get(c, "pv_sym_face", "the y=0 face")],
                               "pull_direction": "PvMid", "angle_deg": 5, "symmetric": True,
                               "tangent_chain": False}, _drafted_symmetric, None),
    # one CONVEX and one CONCAVE vertical edge, same length and radius, both on faces no draft
    # touched: the wedge cut off the first is the wedge filled into the second, so only the surface
    # area moves.
    ("find_geometry", {"target": "PivotCameo", "kind": "line_edge", "nearest_to": [340, 20, 15],
                       "max_results": 1}, "ok", _fg("pv_convex")),
    ("find_geometry", {"target": "PivotCameo", "kind": "line_edge", "nearest_to": [320, 20, 15],
                       "max_results": 1}, "ok", _fg("pv_concave")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "pv_convex", "the outer corner"),
                                          _ctx_get(c, "pv_concave", "the inner corner")],
                                "radius": 5, "tangent_chain": False}, _cut_on_its_pivot, None),
    # A sketch on a FACE has no construction plane to name. Both the write that makes it and the
    # read that returns to it name the BODY and carry that face's own handle instead of plane null;
    # the read gets there by rolling the marker to the sketch and putting it back, because
    # referencePlane answers a BRepFace only from there.
    ("find_geometry", {"target": "PivotCameo", "kind": "planar_face", "nearest_to": [310, 30, 30],
                       "max_results": 1}, "ok", _fg("pv_lid")),
    ("sketch_create", lambda c: {"on_face": _ctx_get(c, "pv_lid", "the L-prism lid"),
                                 "name": "PvOnFace"}, _sits_on_a_face, None),
    ("sketch_get", {"sketch_name": "PvOnFace"},
     lambda p: _sits_on_a_face(p) and "timeline_marker_unrestored" not in p,
     ("pv_face_handle", lambda p: p["on_face"]["handle"])),
    # the handle that read CONSUMED: fed straight back as a face handle, it pushes that same face.
    ("model_offset_face", lambda c: {"faces": [_ctx_get(c, "pv_face_handle", "the sketch's face")],
                                     "distance": 2}, _offset_faces, None),
    # the third pivot gate, in this same component so the block and the sheet share one cell: a
    # replacement surface TILTED about the middle of the face it replaces adds exactly what it cuts,
    # so volume AND face count both hold and only the area moves.
    ("sketch_create", {"plane": "xy", "name": "PvBlkS"}, "ok", None),
    ("sketch_add_geometry", {"sketch_name": "PvBlkS", "geometry": [
        {"kind": "rectangle", "x1": 360, "y1": 0, "x2": 390, "y2": 30}]}, "ok", None),
    ("model_extrude", {"sketch_name": "PvBlkS", "profile_index": 0, "distance": 20},
     _extruded, None),
    ("sketch_create", {"plane": "xz", "name": "PvRoofS"}, "ok", None),
    # sketch +Y maps to world -Z here, so these two points put the sheet at world z 16 -> 24,
    # crossing the block's z=20 top at x=375 - the middle of that face.
    ("sketch_add_geometry", {"sketch_name": "PvRoofS", "geometry": [
        {"kind": "line", "x1": 355, "y1": -16, "x2": 395, "y2": -24}]}, "ok", None),
    ("surface_extrude", {"sketch_name": "PvRoofS", "distance": 80, "symmetric": True},
     lambda p: p.get("is_solid") is False, ("pv_sheet_body", lambda p: p["result_bodies"][0])),
    ("find_geometry", {"target": "PivotCameo", "kind": "planar_face", "nearest_to": [375, 15, 20],
                       "max_results": 1}, "ok", _fg("pv_top")),
    ("model_replace_face", lambda c: {"faces": [_ctx_get(c, "pv_top", "the block top")],
                                      "target": _ctx_get(c, "pv_sheet_body", "the tilted sheet")},
     _replaced_on_its_pivot, None),
    ("design_activate_component", {"occurrence": "FeatureCameo:1"}, "ok", None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_top")),
    # hole points are in the FACE'S LOCAL frame, whose origin for this face is the MODEL origin
    # projected onto its plane - so on-pad coordinates are the world x,y. ([5,5] here drilled at
    # world (5,5), a point off this pad entirely: the hole silently cut whatever body sat near
    # the origin, and the axis-count read-back cannot see a wrong-body cut. Measured live.)
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[220, 20, 0]]}, _drilled(1), None),
    # the three additive placement modes. Each act re-acquires its own edge: the center act below
    # consumes the 4 mm rim by drilling an 8 mm bore concentric with it, so a handle captured once
    # and reused would be pointing at geometry that no longer exists.
    ("find_geometry", {"target": "FeatureCameo", "kind": "circular_edge", "radius": 2,
                       "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_hole_edge")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "center", "edge": _ctx_get(c, "fc_hole_edge", "hole rim"),
                              "diameter": "8 mm", "extent": "blind", "depth": "3 mm"},
     lambda p: p.get("placement") == "center" and p.get("holes_verified") is True, None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "nearest_to": [220, 0, 20],
                       "max_results": 1}, "ok", _fg("fc_edge")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "on_edge", "edge": _ctx_get(c, "fc_edge", "pad edge"),
                              "edge_position": "middle", "diameter": "3 mm",
                              "extent": "blind", "depth": "3 mm"},
     lambda p: p.get("placement") == "on_edge", None),
    # on_edge at the edge's START vertex - RE-ACQUIRED first: the middle-hole above SPLITS the
    # pad edge (measured: a handle captured before that hole resolves to 2 sub-edges and is
    # refused as stale), so every act re-acquires its own edge.
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "nearest_to": [220, 0, 20],
                       "max_results": 1}, "ok", _fg("fc_edge2")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "on_edge", "edge": _ctx_get(c, "fc_edge2", "pad edge"),
                              "edge_position": "start", "diameter": "3 mm",
                              "extent": "blind", "depth": "3 mm"}, _drilled(1), None),
    # plane_offsets measures from STRAIGHT edges - a circular one is refused by name
    ("find_geometry", {"target": "FeatureCameo", "kind": "circular_edge",
                       "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_rim2")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "plane_offsets", "point": [215, 15, 20],
                              "offset_edge_one": _ctx_get(c, "fc_rim2", "a round rim"),
                              "offset_one": "5 mm", "diameter": "3 mm", "extent": "blind",
                              "depth": "3 mm"}, "refused", None),
    # re-acquired again: the start-vertex hole above can notch this edge the same way. The query
    # aims at the LONG remaining stretch of the pad's bottom boundary (x~232) - the notch cuts
    # mint short edges near the hole sites that are NOT parallel to the hole plane, and Fusion
    # refuses a non-parallel reference edge (measured).
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "nearest_to": [232, 0, 20],
                       "max_results": 1}, "ok", _fg("fc_edge3")),
    ("model_hole", lambda c: {"face": _ctx_get(c, "fc_top", "cameo top face"),
                              "placement": "plane_offsets", "point": [215, 15, 20],
                              "offset_edge_one": _ctx_get(c, "fc_edge3", "pad edge"),
                              "offset_one": "6 mm", "diameter": "3 mm", "extent": "blind",
                              "depth": "3 mm"},
     lambda p: p.get("placement") == "plane_offsets", None),
    ("find_geometry", {"target": "FeatureCameo", "kind": "planar_face", "nearest_to": [220, 20, 20], "max_results": 1}, "ok", _fg("fc_body")),
    ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "plane": "yz"}, _mirrored, None),
    # a real GRID, not a row: two directions at once, spaced wider than the 40 mm pad so the
    # instances stand clear of each other. 3 x 2 is also the read-back that catches a tool
    # multiplying the directions wrongly - a row of 3 and a row of 6 both pass a bare "more than 1".
    ("model_pattern_rectangular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                             "quantity_one": 3, "spacing_one": 75, "direction_one": "x",
                                             "quantity_two": 2, "spacing_two": 70, "direction_two": "y"},
     _patterned("total_instances", 6), None),
    # An axis OUTSIDE the part, so the pattern reads as an orbit rather than a body spun in place:
    # two origin planes offset to cross 30 mm clear of the pad's -X edge, and their INTERSECTION is
    # the axis. The world z axis would do the same job 200 mm away, swinging the copies across the
    # whole scene and through the machined part.
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": 170, "name": "OrbitYZ"},
     _datum_plane("yz"), None),
    ("model_construction", {"kind": "plane", "plane": "xz", "offset": 20, "name": "OrbitXZ"},
     _datum_plane("xz"), None),
    ("model_construction", {"kind": "axis", "mode": "two_planes", "plane": "OrbitYZ",
                            "plane2": "OrbitXZ", "name": "CameoOrbit"},
     lambda p: bool(p.get("handle")), None),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 4, "total_angle_deg": 360,
                                          "axis": "CameoOrbit"},
     lambda p: p.get("quantity") == 4 and p.get("axis") == "CameoOrbit", None),
    # pattern the cameo along one of its own line edges - the count is a read-back, never an echo.
    ("find_geometry", {"target": "FeatureCameo", "kind": "line_edge", "max_results": 1}, "ok", _fg("dc_edge")),
    ("model_pattern_path", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")], "path": [_ctx_get(c, "dc_edge", "path edge")], "quantity": 3, "distance": 55, "distance_type": "spacing"},
     lambda p: p.get("patterned") is True and p.get("quantity") == 3, None),
    # the pattern axis as a DATUM: the cameo's own bore defines a construction axis, whose published
    # handle is what the pattern turns about. The axis label is read back off the resolved entity, so
    # the datum's name proves the handle reached the datum and not a world axis fallback.
    ("find_geometry", {"target": "FeatureCameo", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("fc_cyl")),
    ("model_construction", lambda c: {"kind": "axis", "mode": "circular_face",
                                      "face": _ctx_get(c, "fc_cyl", "a cameo bore face"),
                                      "name": "CameoSpin"},
     lambda p: bool(p.get("handle")), ("cam_axis", lambda p: p["handle"])),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 4, "total_angle_deg": 360,
                                          "axis": _ctx_get(c, "cam_axis", "the datum axis handle")},
     lambda p: p.get("quantity") == 4 and p.get("axis") == "CameoSpin", None),
    # the same axis reached BY NAME while its component is active - the handle-free route.
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 3, "total_angle_deg": 180,
                                          "axis": "CameoSpin"},
     lambda p: p.get("quantity") == 3 and p.get("axis") == "CameoSpin", None),
    # the CYLINDRICAL FACE itself as the axis: off-origin, the case a direction vector cannot
    # express, and the label reads the resolved entity's type because a face carries no name.
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 3,
                                          "axis": _ctx_get(c, "fc_cyl", "a cameo bore face")},
     lambda p: p.get("quantity") == 3 and p.get("axis") == "BRepFace", None),
    # a circular_face axis inside a ROTATED occurrence: ConstructionAxis.geometry stays COMPONENT-
    # LOCAL even through a face proxy, so the local axis must be LIFTED through the occurrence's own
    # transform2 to read as world. A YZ-plane rod's local axis is world X before the rotation; a
    # -90 deg Z turn carries it to world (0,-1,0).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # placed at x=1300 mm: clear of the story's stock and bracket in the interference census
    ("model_create_component", {"name": "AxisFrameRig", "activate": True, "x": 1300},
     _made_component, None),
    ("sketch_create", {"plane": "yz", "name": "AxisFrameRigS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 0, "cy": 0, "radius": 5}],
                             "sketch_name": "AxisFrameRigS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AxisFrameRigS", "profile_index": 0, "distance": 20},
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_move", {"occurrence": "AxisFrameRig:1", "rotate_deg": -90, "rotate_axis": "z"},
     _moved_occurrence(), None),
    # captured, or the add below recomputes the design and the uncaptured pose of this grounded
    # occurrence reverts to identity (measured burn82) - the row would then pass on no rotation.
    ("assembly_capture_position", {"action": "capture"}, _captured, None),
    ("design_activate_component", {"occurrence": "AxisFrameRig:1"}, "ok", None),
    ("find_geometry", {"target": "AxisFrameRig", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("afr_cyl")),
    ("model_construction", lambda c: {"kind": "axis", "mode": "circular_face",
                                      "face": _ctx_get(c, "afr_cyl", "the rotated rod's face"),
                                      "name": "AxisFrameSpin"},
     lambda p: _measured("a rotated occurrence's local axis lifts to the face proxy's world reading",
                         {"frame": p.get("frame"),
                          "direction": (p.get("geometry") or {}).get("direction"),
                          "aligned": p.get("aligned_to_face_axis")},
                         p.get("frame") == "world" and p.get("aligned_to_face_axis") is True
                         and _near(abs(((p.get("geometry") or {}).get("direction") or [0, 0, 0])[1]),
                                   1.0, 1e-3)), None),
    # a rotate move BY NAME about that same construction axis, still inside a rotated, captured
    # occurrence: the axis's owning component equals the move's host, which a proxy lift must not
    # skip just because "nothing foreign" - the body it turns is what proves the axis landed.
    ("model_move", {"mode": "rotate", "bodies": ["AxisFrameRig"], "axis": "AxisFrameSpin",
                    "angle_deg": 30},
     lambda p: _measured("rotate by NAME about a construction axis owned by the rotated occurrence "
                         "itself",
                         {"mode": p.get("mode"), "moved": p.get("moved"),
                          "displacement": p.get("displacement")},
                         p.get("moved") is True and p.get("mode") == "rotate"
                         and isinstance(p.get("displacement"), (int, float))
                         and p.get("displacement") > 0), None),
    # a datum NAME reaches only the ACTIVE component, so the same name from the root is refused
    # rather than resolved to something else. (A second same-named axis is never ambiguous - Fusion
    # dedupes the name itself.) The activation is put back so the cameo tree is unchanged.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fc_body", "cameo body")],
                                          "quantity": 3, "axis": "CameoSpin"}, "refused", None),
    ("design_activate_component", {"occurrence": "FeatureCameo:1"}, "ok", None),
    # Three isolated root-parametric joins carry the accepted complete, partial and disjoint
    # controls. Each owns and closes its scratch document, then returns the story to root.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("doc_get", {}, _home_document,
     ("combine_story", _recall("combine_story", _combine_story_address))),
] + _COMBINE_COMPLETE + _COMBINE_PARTIAL + _COMBINE_NONE + _COMBINE_NONROOT_PARTIAL + _REVOLVE_PARTICIPANTS + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),    # the revolve axis as a CYLINDRICAL FACE, off the origin - the case a world key cannot express.
    # A face mapped to a direction VECTOR keeps the direction and DROPS the location, so the ring is
    # turned about the world axis through the ORIGIN and reported as success: the label is checked
    # AND the geometry measured. Live: a cylinder at x=30, a 2x3 mm profile at x 36-38 on the XZ
    # plane, ring bbox x 22..38. The cameo sits at z=100, clear of the part and the other cameos.
    ("model_create_component", {"name": "RevolveCameo", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 100, "name": "RevAxisPlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "RevAxisPlane", "name": "RevAxisS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 30, "cy": 0, "radius": 5}],
                             "sketch_name": "RevAxisS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RevAxisS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "RevolveCameo", "kind": "cylinder_face", "radius": 5, "max_results": 1}, "ok", _fg("rv_cyl")),
    ("sketch_create", {"plane": "xz", "name": "RevRingS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 36, "y1": 100,
                                           "x2": 38, "y2": 103}],
                             "sketch_name": "RevRingS"}, "ok", None),
    ("model_revolve", lambda c: {"sketch_name": "RevRingS", "profile_index": 0,
                                 "axis": _ctx_get(c, "rv_cyl", "the off-origin cylinder face"),
                                 "angle_deg": 360},
     lambda p: p.get("axis") == "BRepFace", None),
    # the label alone cannot see wrong geometry: the ring must stand AROUND x=30, never around the
    # origin (about the world z axis this profile spans x -38..38, so a positive min x is the tell).
    ("model_inspect", {"target": "RevolveCameo:1"},
     lambda p: (p["min_point"]["x"] >= 20 and p["max_point"]["x"] <= 40
                and p["min_point"]["x"] > 0), None),
    # a PLANAR face carries a normal, not an axis - refused by name (only a cylindrical/conical/
    # toroidal face defines one) instead of turned into a direction the caller never asked for.
    ("find_geometry", {"target": "RevolveCameo", "kind": "planar_face", "nearest_to": [30, 0, 120],
                       "max_results": 1}, "ok", _fg("rv_flat")),
    ("model_revolve", lambda c: {"sketch_name": "RevRingS", "profile_index": 0,
                                 "axis": _ctx_get(c, "rv_flat", "a planar cap face"),
                                 "angle_deg": 360}, "refused", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # 'all' takes EVERY closed region, and the bays inside a frame outline are closed regions too -
    # so this extrude fills them with material. The payload cannot show a solid bay, so the enclosed
    # regions are NAMED: an outer rectangle plus three bays reports the three that sit inside another.
    ("model_create_component", {"name": "BayCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 200, "y1": 900,
                                           "x2": 290, "y2": 960}],
                             "sketch_name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 210, "y1": 910,
                                           "x2": 230, "y2": 950}],
                             "sketch_name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 240, "y1": 910,
                                           "x2": 260, "y2": 950}],
                             "sketch_name": "BayS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 270, "y1": 910,
                                           "x2": 285, "y2": 950}],
                             "sketch_name": "BayS"}, "ok", None),
    ("model_extrude", {"sketch_name": "BayS", "profile_index": "all", "distance": 8},
     lambda p: (isinstance(p.get("enclosed_profile_indices"), list)
                and len(p["enclosed_profile_indices"]) > 0
                and "enclosed" in p.get("note", "")), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # Radius filtering uses the full measurement while the returned record stays at three decimals.
    # Two separately measured cylinders make the target/decoy decision observable in every unit.
    ("model_create_component", {"name": "RadiusFilterBench", "activate": True},
     _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RadiusTargetS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 2300, "cy": 0,
                                           "radius": 2.68224}],
                             "sketch_name": "RadiusTargetS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RadiusTargetS", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "RadiusDecoyS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 2320, "cy": 0,
                                           "radius": 5}],
                             "sketch_name": "RadiusDecoyS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RadiusDecoyS", "profile_index": 0, "distance": 10},
     _extruded, None),
    # Position-only acquisition keeps the size proof independent of the radius filter. The target
    # face set has one cylindrical face; the target edge set has both circular rims, selected ahead
    # of the decoy by distance to their midpoint.
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "nearest_to": [2300, 0, 5], "max_results": 1},
     _matched(1, "cylinder_face"), _fgn("radius_target_faces")),
    ("model_inspect", lambda c: {"target": _ctx_get(
        c, "radius_target_faces", "the independently acquired target face")[0], "units": "mm"},
     _radius_body_size("independent target face is a 2.68224 mm radius cylinder", 5.36448),
     None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "nearest_to": [2320, 0, 5], "max_results": 1},
     _matched(1, "cylinder_face"), _fg("radius_decoy_face")),
    ("model_inspect", lambda c: {"target": _ctx_get(
        c, "radius_decoy_face", "the independently acquired decoy face"), "units": "mm"},
     _radius_body_size("independent decoy face is a 5 mm radius cylinder", 10.0), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "circular_edge",
                       "nearest_to": [2300, 0, 5], "max_results": 2},
     _matched(2, "circular_edge"), _fgn("radius_target_edges")),
    ("model_inspect", lambda c: {"target": _ctx_get(
        c, "radius_target_edges", "the independently acquired target edges")[0], "units": "mm"},
     _radius_body_size(
         "independent target edge resolves to its 2.68224 mm radius owning body", 5.36448),
     None),
    # The inside query must include the target and the 0.00124 mm farther query must exclude it.
    # Each positive call retains every handle. The following model_inspect calls consume the actual
    # filtered handles; the tight body-size readback proves each one belongs to the target cylinder.
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "radius": 2.55524, "units": "mm"},
     _matched(1, "cylinder_face"), _fgn("radius_face_mm")),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_face_mm", 0),
     _radius_body_size("mm-filtered face is the 2.68224 mm radius target", 5.36448), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "radius": 2.554, "units": "mm"},
     _matched(0, "cylinder_face"), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "radius": 0.255524, "units": "cm"},
     _matched(1, "cylinder_face"), _fgn("radius_face_cm")),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_face_cm", 0),
     _radius_body_size("cm-filtered face is the 2.68224 mm radius target", 5.36448), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "radius": 0.2554, "units": "cm"},
     _matched(0, "cylinder_face"), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "radius": 0.1006, "units": "in"},
     _matched(1, "cylinder_face"), _fgn("radius_face_in")),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_face_in", 0),
     _radius_body_size("inch-filtered face is the 2.68224 mm radius target", 5.36448), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "cylinder_face",
                       "radius": 0.1005511811023622, "units": "in"},
     _matched(0, "cylinder_face"), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "circular_edge",
                       "radius": 2.55524, "units": "mm"},
     _matched(2, "circular_edge"), _fgn("radius_edges_mm")),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_edges_mm", 0),
     _radius_body_size("first mm-filtered edge owns the 2.68224 mm radius target", 5.36448), None),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_edges_mm", 1),
     _radius_body_size("second mm-filtered edge owns the 2.68224 mm radius target", 5.36448), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "circular_edge",
                       "radius": 2.554, "units": "mm"},
     _matched(0, "circular_edge"), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "circular_edge",
                       "radius": 0.255524, "units": "cm"},
     _matched(2, "circular_edge"), _fgn("radius_edges_cm")),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_edges_cm", 0),
     _radius_body_size("first cm-filtered edge owns the 2.68224 mm radius target", 5.36448), None),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_edges_cm", 1),
     _radius_body_size("second cm-filtered edge owns the 2.68224 mm radius target", 5.36448), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "circular_edge",
                       "radius": 0.2554, "units": "cm"},
     _matched(0, "circular_edge"), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "circular_edge",
                       "radius": 0.1006, "units": "in"},
     _matched(2, "circular_edge"), _fgn("radius_edges_in")),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_edges_in", 0),
     _radius_body_size(
         "first inch-filtered edge owns the 2.68224 mm radius target", 5.36448), None),
    ("model_inspect", lambda c: _radius_filtered_handle(
        c, "radius_edges_in", 1),
     _radius_body_size(
         "second inch-filtered edge owns the 2.68224 mm radius target", 5.36448), None),
    ("find_geometry", {"target": "RadiusFilterBench", "kind": "circular_edge",
                       "radius": 0.1005511811023622, "units": "in"},
     _matched(0, "circular_edge"), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
]


_DETAILS = [
    # THE HOLES AS THE MACHINE SEES THEM, read FIRST: the chamfers below break two of these rims,
    # and a broken rim is a hole of a different shape, so the recognizer's grouping is read while
    # the drilled pattern is still as model_hole left it. The saved handles drive the CAM act's
    # recognized-hole drill; BoreDia is PartHt * 0.3 = 12 mm.
    # Recognition needs the Manufacturing Extension (measured on the lapsed install: "Requires the
    # Manufacturing Extension to be active"), so the recognizer rows ride the tier and the
    # unentitled variant asserts the refusal names the extension and the by-hand route.
    ("cam_find_holes", {"bodies": ["Bracket:1"]},
     _needs(MACHINING_EXTENSION, _holes_recognized(2, 4, 12.0)),
     _recognized_cbore_walls("recognized_cbore_walls", count_key="holes_group_count")),
    ("cam_find_holes", {"bodies": ["Bracket:1"]},
     _unless(MACHINING_EXTENSION, _refused("Manufacturing Extension", "find_geometry")), None),
    # The window, on the same part: at 11 mm every BoreDia group falls out (the step bore and the
    # longer boss bore are separate groups - the recognizer groups by identical length) and the
    # 10.8 mm counterbores stay; kept plus dropped is the unwindowed total, so nothing is lost.
    ("cam_find_holes", {"bodies": ["Bracket:1"], "max_diameter": 11},
     _needs(MACHINING_EXTENSION, _holes_windowed(12.0, "holes_group_count")), None),
    # THE POCKET AS THE MACHINE SEES IT, down the same axis a 3-axis setup attacks: PocketDepth is
    # PartHt * 0.4 = 16 mm, so the floor sits that far under the low top the cut opened, and the
    # four PocketRad corners round its single boundary loop. The saver takes that pocket's FLOOR
    # handle - the face whose locator sits lowest along the attack - which drives a 2D pocket of its
    # own in the CAM act; its count and its loop size are what the two rows after it read back.
    # Plain pocket recognition runs on the base licence (measured on the lapsed install: 8 pockets
    # read back); only the boss-aware route below rides the tier.
    ("cam_find_pockets", {"bodies": ["Bracket:1"]}, _pockets_recognized(16.0),
     _recognized_pocket_floor("pocket_floor", 16.0, count_key="pockets_plain_count",
                              loop_key="pocket_loop_segments")),
    # ...and the boss, which only the boss-aware route reports: it comes back as a pocket with an
    # ISLAND and no boundary - the one shape that separates a boss from a recess. The row rides the
    # extension tier, so a base licence skips it rather than reddening the run.
    ("cam_find_pockets", {"bodies": ["Bracket:1"], "include_bosses": True},
     _needs(MACHINING_EXTENSION, _pocket_boss("pockets_plain_count")), None),
    # The rims the part is handled by, each asked for by RADIUS - the one query that keeps naming
    # the same edge after the driver changes. A fillet's own tangent circle is not a corner, and
    # handing one back to model_fillet answers FILLET_NO_EDGE_FOUND, so every beat below picks a
    # rim no earlier feature has already rounded.
    # Every one of these queries FEEDS the next step, so each asserts the count it found: a radius
    # filter that matches nothing still returns ok, and the ledger then reports a bare pass on a
    # query whose handle the following row cannot take.
    ("find_geometry", {"target": "Bracket", "kind": "circular_edge", "radius": 10,
                       "max_results": 1}, _matched(1, "circular_edge"), _fg("boss_rim")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "boss_rim", "the boss rim")],
                                "radius": 1.5}, _filleted, None),
    ("find_geometry", {"target": "Bracket", "kind": "circular_edge", "radius": 6,
                       "max_results": 1}, _matched(1, "circular_edge"), _fg("bore_rim")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "bore_rim", "a through-bore rim")], "distance": 1}, _chamfered, None),
    # the distance-and-angle definition with an explicit corner type. Both assertions are on values
    # READ BACK off the created feature - a corner type the platform silently ignored builds an
    # identical face count, so an echoed payload would sail through this predicate.
    ("find_geometry", {"target": "Bracket", "kind": "circular_edge", "radius": 3,
                       "max_results": 4}, _matched(4, "circular_edge"),
     ("mount_rim", lambda p: p["matches"][-1]["handle"])),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "mount_rim", "a mounting-bore rim")],
                                 "distance": 1, "angle_deg": 30, "corner_type": "miter"},
     lambda p: p.get("corner_type") == "miter" and abs((p.get("angle_deg") or 0) - 30) < 1e-6
     and not p.get("corner_type_unverified") and not p.get("angle_deg_unverified") and not p.get("chamfer_type_unverified"), None),
    # a shell cameo cap, a wart feature added and deleted (timeline health diff), a scratch occurrence.
    ("model_create_component", {"name": "ShellCap", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ShellS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 400, "y1": 0,
                                           "x2": 430, "y2": 30}],
                             "sketch_name": "ShellS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ShellS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [415, 15, 20], "max_results": 1}, "ok", _fg("shell_top")),
    ("model_shell", lambda c: {"body_name": "ShellCap", "remove_faces": [_ctx_get(c, "shell_top", "shell top")], "thickness": 2}, _shelled, None),
    # THE TWO-SIDED EXTENT, in its own component so nothing else's body census moves. Side one lands
    # on extentOne and side two on extentTwo, each reporting the depth THAT side asked for, and the
    # tool now compares them side by side - so an UNEQUAL pair is the shape that tells the compare
    # apart from one reading a single number twice. The payload alone cannot show where the material
    # went, hence the inspect below.
    ("model_create_component", {"name": "TwoSideCap", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TwoSideS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 470, "y1": 0,
                                           "x2": 500, "y2": 30}],
                             "sketch_name": "TwoSideS"}, "ok", None),
    # 'model_parameters' carrying BOTH distance and distance2 is the feature really holding two
    # extents; an EMPTY unverified disclosure is the compare having judged both sides rather than
    # skipping one (it names any side it could not judge).
    ("model_extrude", {"sketch_name": "TwoSideS", "profile_index": 0, "extent": "two_side",
                       "distance": 12, "distance2": 8},
     lambda p: (p.get("extent") == "two_side" and p.get("distance") == 12.0
                and p.get("distance2") == 8.0 and bool(p.get("result_bodies"))
                and "distance" in (p.get("model_parameters") or {})
                and "distance2" in (p.get("model_parameters") or {})
                and "not depth-verified" not in (p.get("note") or "")), None),
    # Where the material actually went: the sketch sits on z=0, so a correct two-sided extrude
    # STRADDLES it 20 mm deep and UNEQUALLY. A symmetric 12/12 or 8/8 fails the inequality, a
    # one-sided 20 fails the straddle, and a swap only changes which face is which - all three are
    # payloads that would read identically above.
    ("model_inspect", {"target": "TwoSideCap:1"},
     lambda p: (p["min_point"]["z"] < -0.001 and p["max_point"]["z"] > 0.001
                and abs((p["max_point"]["z"] - p["min_point"]["z"]) - 20) < 0.05
                and abs(abs(p["max_point"]["z"]) - abs(p["min_point"]["z"])) > 3), None),
    # back to the shell cameo's component, so every step after this lands where it did before.
    ("design_activate_component", {"occurrence": "ShellCap:1"}, "ok", None),
    # THE WartPlane ROW carries the offset_from predicate - the sweep's only offset-plane call, so
    # it is where 'offset_from' gets read once against a real resolved origin plane: the payload
    # must name the PLANE ('XY'), never echo the 'xy' request token. The unit fake spells the name
    # uppercase from the sibling convention; this beat is what MEASURES the live casing, so a
    # failure on the string alone means the FAKE is what is wrong, never the tool.
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 12, "name": "WartPlane"},
     lambda p: p.get("offset_from") == "XY", None),
    # 'deleted' is true only where the timeline name census read on BOTH sides of the delete; null is
    # an absence nothing could prove, and a delete that broke a downstream feature says so.
    ("design_delete_feature", {"feature": "WartPlane"},
     lambda p: (p["deleted"] is True and p["feature"] == "WartPlane"
                and "timeline_warning" not in p), None),
    # the three datum modes with no other route in the API: a plane rotated about a curved face's
    # own inferred axis, a plane pinned through a vertex, and a plane/point at a ratio along a path.
    # The angled plane is built on the DATUM BENCH's bore, not on the machined part. A datum
    # plane is an infinite visual object and the shaft sits at the world origin - which is where the
    # vise is later built around the machined part, so a plane hung there leans across the fixture
    # for the rest of the run. The bench is out on the field with its own cell and its own frame.
    ("model_construction", lambda c: {"kind": "plane", "mode": "at_angle_on_face",
                                      "face": _ctx_get(c, "db_bore", "bench bore"),
                                      "plane": "xz", "angle": 30, "name": "BenchAnglePlane"},
     # the bore's axis is Z through the bench's own centre, so the plane contains that axis and its
     # origin sits ON it - asked through _px/_py because the layout moves the bench.
     lambda p: p.get("contains_face_axis") is True and p.get("angle_deg") == 30
     and _near(p["geometry"]["origin"]["x"], _px("DatumBench", 230.0), 1e-3)
     and _near(p["geometry"]["origin"]["y"], _py("DatumBench", 20.0), 1e-3), None),
    ("find_geometry", {"target": "ShellCap", "kind": "vertex", "max_results": 1}, "ok", _fg("cd_vert")),
    ("model_construction", lambda c: {"kind": "plane", "mode": "offset_through_point", "plane": "xy",
                                      "points": [_ctx_get(c, "cd_vert", "shell vertex")],
                                      "name": "ThroughVertexPlane"},
     lambda p: p.get("passes_through_point") is True, None),
    # the bottom front edge specifically: (400,0,0) -> (430,0,0), midpoint (415,0,0).
    ("find_geometry", {"target": "ShellCap", "kind": "line_edge", "nearest_to": [415, 0, 0], "max_results": 1},
     "ok", _fg("cd_edge")),
    # The on-path placements all ride ONE known path: the cap's bottom front edge, 30 mm long from
    # (400,0,0) to (430,0,0). A PROPORTIONAL placement reads 'at' as a unitless ratio and publishes
    # it back as the ratio it landed on - no path extent at all, because a ratio cannot leave the
    # path. Every absolute beat below is measured against that same 30 mm.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 0.5, "name": "MidPathPlane"},
     lambda p: p.get("at_ratio") == 0.5
     and abs(p["geometry"]["origin"]["x"] - _px("ShellCap", 415)) < 1e-3
     and abs(p["geometry"]["origin"]["y"] - _py("ShellCap", 0)) < 1e-3
     and abs(p["geometry"]["origin"]["z"]) < 1e-3
     and p.get("landed", {}).get("distance") == "0.5"
     and "path_length" not in p and "beyond_path" not in p
     and "not clamped" not in (p.get("note") or ""), None),
    # a quarter along the SAME edge: 7.5 mm from the midpoint whichever way the edge runs.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 0.25, "name": "QuarterPathPoint"},
     lambda p: p.get("at_ratio") == 0.25
     and abs(abs(p["geometry"]["at"]["x"] - _px("ShellCap", 415)) - 7.5) < 1e-3
     and abs(p["geometry"]["at"]["y"] - _py("ShellCap", 0)) < 1e-3
     and abs(p["geometry"]["at"]["z"]) < 1e-3, None),
    # setByPath RAISES on a proportional value outside 0-1 and the raise rolls back the whole
    # transaction, so the range is refused before the call - and the next call still answers.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"), "at": 1.5},
     "refused", None),
    # ABSOLUTE: 'at' is a LENGTH from the path start, so the payload swaps the ratio for the pair
    # that can be compared - the measured path length and where this datum sits along it. 12 mm is
    # inside the 30 mm edge, so beyond_path is false and the note warns of nothing.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 12, "distance_type": "absolute",
                                      "name": "AbsInsidePlane"},
     lambda p: p.get("distance_type") == "absolute" and "at_ratio" not in p
     and isinstance(p.get("landed", {}).get("distance"), str)
     and abs(p.get("path_length", 0) - 30.0) < 1e-3
     and abs(p.get("along_path", -1) - 12.0) < 1e-3
     and p.get("beyond_path") is False and "OFF the path" not in (p.get("note") or ""), None),
    # An absolute distance is not clamped at EITHER end: a NEGATIVE one places the datum before the
    # path start, along the tangent, with a healthy feature. That is a legal placement the platform
    # accepts, so it is reported with its measured numbers - and the note says it landed off.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": -5, "distance_type": "absolute",
                                      "name": "BeforeStartPlane"},
     lambda p: p.get("beyond_path") is True and abs(p.get("along_path", 0) + 5.0) < 1e-3
     and "OFF the path" in (p.get("note") or ""), None),
    # the far end of the same range, on the point kind: 500 mm along a 30 mm edge.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 500, "distance_type": "absolute",
                                      "name": "PastEndPoint"},
     lambda p: p.get("beyond_path") is True and abs(p.get("path_length", 0) - 30.0) < 1e-3, None),
    # the boundary itself: a datum exactly AT the path length is ON the path, not beyond it.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 30, "distance_type": "absolute",
                                      "name": "AtEndPlane"},
     lambda p: p.get("beyond_path") is False
     and abs(p.get("along_path", -1) - p.get("path_length", 0)) < 1e-6, None),
    # an EXPRESSION placement is measured exactly as a literal one - the expression is what the
    # datum's own model parameter carries, and that parameter's name is what param_set retargets.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": "22 mm", "distance_type": "absolute",
                                      "name": "ExprPathPoint"},
     lambda p: "22" in (p.get("landed", {}).get("distance") or "")
     and str(p.get("model_parameters", {}).get("distance", "")).startswith("d"), None),
    # proportional on the plane kind reads back as the bare ratio, with no extent published - the
    # pair that separates the two distance types in one payload.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "at": 0.5, "distance_type": "proportional",
                                      "name": "RatioPlane"},
     lambda p: p.get("landed", {}).get("distance") == "0.5"
     and "path_length" not in p and "beyond_path" not in p, None),
    # to_object: the plane lands at a VERTEX's own along-path position PLUS a signed offset, and the
    # two arrive as SEPARATE model parameters. 40 mm past either end of a 30 mm edge is off the path
    # whichever vertex the edge starts at, so this one carries the same off-path disclosure.
    ("find_geometry", {"target": "ShellCap", "kind": "vertex", "nearest_to": [430, 0, 0],
                       "max_results": 1}, "ok", _fg("cd_vert_end")),
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "to_object": _ctx_get(c, "cd_vert_end", "a path end vertex"),
                                      "offset": 40, "name": "ToObjectFarPlane"},
     lambda p: p.get("to_object") is True
     and set(p.get("landed", {})) == {"distance", "offset"}
     and set(p.get("model_parameters", {})) == {"distance", "offset"}
     and abs(p.get("path_length", 0) - 30.0) < 1e-3
     and p.get("along_path", 0) > p.get("path_length", 0)
     and p.get("beyond_path") is True and "OFF the path" in (p.get("note") or ""), None),
    # the same shape landing INSIDE, so the disclosure is proven to discriminate: the cap's bottom
    # front and right edges chain into a 60 mm path whose shared vertex sits at 30 mm, and 5 mm past
    # that is still on the path.
    ("find_geometry", {"target": "ShellCap", "kind": "line_edge", "nearest_to": [430, 15, 0],
                       "max_results": 1}, "ok", _fg("cd_edge2")),
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": [_ctx_get(c, "cd_edge", "datum path edge"),
                                               _ctx_get(c, "cd_edge2", "the connected second edge")],
                                      "to_object": _ctx_get(c, "cd_vert_end", "the shared vertex"),
                                      "offset": 5, "name": "ToObjectMidPlane"},
     lambda p: p.get("beyond_path") is False and abs(p.get("along_path", 0) - 35.0) < 1e-3
     and "OFF the path" not in (p.get("note") or ""), None),
    # ConstructionPointInput carries setByPath but NOT setByPathToObject, so the point kind refuses
    # 'to_object' by name instead of dropping it.
    ("model_construction", lambda c: {"kind": "point", "mode": "on_path",
                                      "path": _ctx_get(c, "cd_edge", "datum path edge"),
                                      "to_object": _ctx_get(c, "cd_vert_end", "a path end vertex")},
     "refused", None),
    # a CHAINED path is measured whole: 45 mm is past the first edge but inside the 60 mm total, so
    # the datum is on the path and 'path_length' is the SUM, not the seed edge's own length.
    ("model_construction", lambda c: {"kind": "plane", "mode": "on_path",
                                      "path": [_ctx_get(c, "cd_edge", "datum path edge"),
                                               _ctx_get(c, "cd_edge2", "the connected second edge")],
                                      "at": 45, "distance_type": "absolute",
                                      "name": "ChainedPathPlane"},
     lambda p: p.get("beyond_path") is False and abs(p.get("path_length", 0) - 60.0) < 1e-3
     and abs(p.get("along_path", 0) - 45.0) < 1e-3, None),
    # sketch_project's two new actions, on the cap the datum beats already measured. The section
    # sketch sits on MidPathPlane (x=415, normal along X), which crosses the cap cleanly; the cap's
    # x=400 side face is PARALLEL to that plane, so it is the same-context source that contributes
    # nothing and the partial path must name it.
    ("sketch_create", {"plane": "MidPathPlane", "name": "SecS"}, "ok", None),
    # the section of the HOLLOW cap is outer + inner rectangles - at least 8 curves. Attribution is
    # honestly SUPPRESSED whenever any created curve fails to match back, so the beat asserts the
    # census, not per_source.
    ("sketch_project", {"action": "intersect", "sketch_name": "SecS", "bodies": ["ShellCap"]},
     lambda p: p.get("created_count", 0) >= 8 and len(p.get("entity_refs") or []) >= 8, None),
    # the cap's x=400 side face is PARALLEL to the section plane: zero curves created is the
    # measured silent-empty, which the tool's own gate converts into an error naming the source.
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [400, 15, 10], "max_results": 1}, "ok", _fg("sp_far")),
    ("sketch_project", lambda c: {"action": "intersect", "sketch_name": "SecS",
                                  "entities": [_ctx_get(c, "sp_far", "cap far side face")]},
     "refused", None),
    # to_surface: ShellS's own lines projected onto the cap's top face, received by a THIRD sketch -
    # the source sketch must differ from the receiver (measured same-sketch refusal), the curves
    # land ON the face (off the receiver's plane), and the linkage is read back per curve.
    ("sketch_create", {"plane": "xy", "name": "ProjS"}, "ok", None),
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [415, 15, 20], "max_results": 1}, "ok", _fg("sp_top")),
    ("sketch_project", lambda c: {"action": "to_surface", "sketch_name": "ProjS",
                                  "target_faces": [_ctx_get(c, "sp_top", "cap top face")],
                                  "source_sketch": "ShellS", "curve_refs": ["line:0"]},
     lambda p: p.get("created_count", 0) >= 1
     and (p.get("off_plane_count") == p.get("created_count") or "census alone" in p.get("note", ""))
     and ("REFERENCE curves" in p.get("note", "") or "census alone" in p.get("note", "")), None),
    ("sketch_project", {"action": "to_surface", "sketch_name": "ProjS", "target_faces": [],
                        "source_sketch": "ProjS", "curve_refs": ["line:0"]}, "refused", None),
    ("sketch_project", lambda c: {"action": "to_surface", "sketch_name": "ProjS",
                                  "target_faces": [_ctx_get(c, "sp_top", "cap top face")],
                                  "source_sketch": "ShellS", "curve_refs": ["line:0"],
                                  "project_type": "along_vector"}, "refused", None),
    ("model_create_component", {"name": "ScratchOcc", "activate": False}, _made_component_inactive, None),
    # 'deleted' is true only where the assembly path census carried this occurrence BEFORE the delete
    # and not after; null is an unverified absence the payload publishes as a successful call.
    ("design_delete_occurrence", {"occurrence": "ScratchOcc:1"},
     lambda p: (p["deleted"] is True and p["occurrence"] == "ScratchOcc:1"
                and "timeline_warning" not in p), None),
    # scale + offset-face beats on a scratch block: push a face and read the volume move, then the
    # scale contract - uniform f^3, per-axis x*y*z, the three refusal shapes (unresolvable /
    # length-carrying / angle-carrying expression), a bare unitless parameter accepted, and a
    # vertex-anchored scale.
    ("model_create_component", {"name": "ScaleBlock", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ScaleS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 470, "y1": 0,
                                           "x2": 490, "y2": 20}],
                             "sketch_name": "ScaleS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ScaleS", "profile_index": 0, "distance": 20}, _extruded, None),
    _watch("ScaleBlock:1"),
    ("find_geometry", {"target": "ScaleBlock", "kind": "planar_face", "nearest_to": [480, 10, 20],
                       "max_results": 1}, "ok", _fg("scale_top")),
    ("model_offset_face", lambda c: {"faces": [_ctx_get(c, "scale_top", "block top")],
                                     "distance": 2}, _offset_faces, None),
    # a UNITLESS and an ANGLE parameter: 'value_units' is the parameter's OWN unit read back, which
    # is what says the angle came out of the internal radians into degrees.
    ("param_add", {"name": "ShrinkProbe", "expression": "0.5", "unit": ""},
     _param_added("ShrinkProbe", 0.5, units=""), None),
    ("param_add", {"name": "TiltProbe", "expression": "30 deg", "unit": "deg"},
     _param_added("TiltProbe", 30, units="deg"), None),
    # a solid body's volume IS readable, so the verdict is the measured ratio and the skip flag is
    # absent - its presence would mean the check fell back to "the geometry moved".
    # A SCALE IS ORIGIN-RELATIVE: it multiplies coordinates measured from the world origin, so a
    # block authored at x=470 doubles to x=940 - it grows AND travels, right out of the frame it was
    # framed in. Every scale that moves it is followed by a fresh frame, or the operation the viewer
    # came to watch happens off screen.
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": 2},
     lambda p: p.get("scale_check") == "volume_ratio"
     and abs(p.get("volume_ratio", 0) - 8.0) < 1e-6 and "volume_check_skipped" not in p, None),
    _watch("ScaleBlock:1"),
    ("model_scale", {"bodies": ["ScaleBlock"], "x_factor": 3, "y_factor": 2, "z_factor": 1},
     lambda p: abs(p.get("expected_volume_ratio", 0) - 6.0) < 1e-6, None),
    _watch("ScaleBlock:1"),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "NoSuchParamXyz * 2"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "5 mm"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "TiltProbe"}, "refused", None),
    ("model_scale", {"bodies": ["ScaleBlock"], "factor": "ShrinkProbe"},
     lambda p: abs(p.get("expected_volume_ratio", 0) - 0.125) < 1e-6, None),
    _watch("ScaleBlock:1"),
    ("find_geometry", {"target": "ScaleBlock", "kind": "vertex", "max_results": 1}, "ok",
     _fg("scale_vtx")),
    ("model_scale", lambda c: {"bodies": ["ScaleBlock"], "factor": 1.5,
                               "anchor": _ctx_get(c, "scale_vtx", "block vertex")},
     lambda p: abs(p.get("volume_ratio", 0) - 3.375) < 1e-6, None),
    ("param_delete", {"name": "ShrinkProbe"}, _param_deleted("ShrinkProbe"), None),
    ("param_delete", {"name": "TiltProbe"}, _param_deleted("TiltProbe"), None),
    ("model_move", {"bodies": ["ScaleBlock"], "dx": 10},
     lambda p: abs(p.get("displacement", 0) - 10.0) < 1e-3, None),
    ("model_move", {"mode": "along_entity", "bodies": ["ScaleBlock"], "axis": "y",
                    "distance": 5}, lambda p: abs(p.get("displacement", 0) - 5.0) < 1e-3, None),
    ("model_move", {"mode": "rotate", "bodies": ["ScaleBlock"], "axis": "z", "angle_deg": 15},
     _moved, None),
    ("model_move", lambda c: {"mode": "along_entity", "bodies": ["ScaleBlock"],
                              "axis": _ctx_get(c, "scale_top", "block top"), "distance": 5},
     "refused", None),
    ("model_move", lambda c: {"bodies": ["ScaleBlock"],
                              "faces": [_ctx_get(c, "scale_top", "block top")], "dx": 5},
     "refused", None),
    # SINGLE vs DOUBLE placement: the along_entity beats above moved a body in a component placed
    # ONCE (the axis is proxied into that one occurrence). The same call on a component placed TWICE
    # must refuse naming BOTH paths - each instance holds that body somewhere else, and the
    # displacement read-back cannot tell a right instance from a wrong one.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TwicePlaced", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TwicePlacedS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1900, "y1": 200,
                                           "x2": 1920, "y2": 220}],
                             "sketch_name": "TwicePlacedS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwicePlacedS", "profile_index": 0, "distance": 10},
     _extruded, None),
    # [F75]/NEW-16: name the body uniquely, then resolve it BARE while the component is still the
    # ACTIVE component and placed ONCE - the walk reaches it both natively (active-comp scope) and
    # as the occurrence proxy, whose entityTokens DIFFER; grouping by native token collapses the
    # pair to ONE candidate (a bare-token key refused this as 'names 2 bodies').
    ("find_geometry", {"target": "TwicePlaced", "kind": "planar_face",
                       "nearest_to": [1910, 210, 10], "max_results": 1}, "ok",
     _fg("twice_face")),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "twice_face", "the TwicePlaced body"),
                                   "new_name": "TwiceBody"},
     lambda p: p.get("name") == "TwiceBody", None),
    ("model_inspect", {"target": "TwiceBody"}, _extent_measured, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("design_add_instance", {"component": "TwicePlaced", "x": 40, "y": 0, "units": "mm"},
     lambda p: p.get("created") is True, ("twice_b", lambda p: p["full_path"])),
    ("model_move", {"mode": "along_entity", "bodies": ["TwicePlaced"], "axis": "y", "distance": 5},
     _refused("placed 2 times", "TwicePlaced:1"), None),
    # placed TWICE the same bare name is two world placements - the refusal lists BOTH
    # instance-qualified forms (the dropped native spelling is not offered), and the qualified
    # form is the way out.
    ("model_inspect", {"target": "TwiceBody"},
     _refused("TwicePlaced:1", "TwicePlaced:2"), None),
    ("model_inspect", {"target": "TwicePlaced:2:TwiceBody"}, _extent_measured, None),
    # back to the component that was active before this cameo, so the ones after it nest as before.
    ("design_activate_component", {"occurrence": "ScaleBlock:1"}, "ok", None),
    # point_to_point: the tool refuses a travel that does not equal the two vertices' own
    # separation, so a plain ok here IS the distance check
    ("find_geometry", {"target": "ScaleBlock", "kind": "vertex", "max_results": 8}, "ok",
     _fgn("mv_verts")),
    ("model_move", lambda c: {"mode": "point_to_point", "bodies": ["ScaleBlock"],
                              "from_point": _ctx_get(c, "mv_verts", "block vertices")[0],
                              "to_point": _ctx_get(c, "mv_verts", "block vertices")[1]},
     lambda p: p.get("moved") is True and p.get("displacement", 0) > 0, None),
    ("model_create_component", {"name": "ThreadPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ThreadS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 530, "cy": 10, "radius": 5}],
                             "sketch_name": "ThreadS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadS", "profile_index": 0, "distance": 25}, _extruded, None),
    ("find_geometry", {"target": "ThreadPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("post_wall")),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M99x9"}, "refused", None),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M10x1.5", "offset": 2}, "refused", None),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M10x1.5", "length": 12, "offset": 2},
     lambda p: p.get("internal") is False and p.get("designation") == "M10x1.5"
     and p.get("right_handed") is True and p.get("length") == 12, None),
    # the partial extent is read back off the feature, and a metric call-out sits in several
    # standards, so the alternatives ride along
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M10x1.5", "length": 12, "offset": 2,
                                "thread_type": "ISO Metric profile"},
     lambda p: p.get("thread_type") == "ISO Metric profile"
     and len(p.get("thread_type_alternatives") or []) > 1, None),
    # a modeled designation far too big for the post grows the body instead of cutting it
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post_wall", "post wall")],
                                "designation": "M30x3.5", "modeled": True}, "refused", None),
    # a modeled thread that FITS must cut real material - the rung-4 gate's own regression net
    ("model_create_component", {"name": "ThreadPost2", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ThreadS2"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 570, "cy": 10, "radius": 5}],
                             "sketch_name": "ThreadS2"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadS2", "profile_index": 0, "distance": 25}, _extruded, None),
    ("find_geometry", {"target": "ThreadPost2", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("post2_wall")),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "post2_wall", "second post wall")],
                                "designation": "M10x1.5", "modeled": True},
     lambda p: p.get("modeled") is True and p.get("volume_delta_cm3", 0) < 0, None),
    # the INTERNAL side of the same tool, on a real bore: 'internal' is derived from the face's own
    # out-of-material normal (never echoed), and the ThreadInfo it built is checked against the face
    # at add() - so an 'internal' that disagreed with the geometry would have raised. Then a PARTIAL
    # thread measured from the LOW end, with the end it was measured from read back off the feature.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "ThreadBore", "activate": True}, _made_component, None),
    # the bore is CUT, not left as the inner loop of a two-circle profile: which region a
    # multi-profile sketch calls its last one is the platform's to decide, and picking the disc
    # there builds a plain rod whose radius-4 wall is EXTERNAL - the thread then lands external and
    # the beat asserts nothing about bores. A solid rod plus a through cut is unambiguous.
    ("sketch_create", {"plane": "xy", "name": "ThreadBoreS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 610, "cy": 10, "radius": 12}],
                             "sketch_name": "ThreadBoreS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadBoreS", "profile_index": 0, "distance": 25},
     _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "ThreadBoreCut"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 610, "cy": 10, "radius": 4}],
                             "sketch_name": "ThreadBoreCut"}, "ok", None),
    ("model_extrude", {"sketch_name": "ThreadBoreCut", "profile_index": 0, "distance": 25,
                       "operation": "cut"}, _extruded, None),
    ("find_geometry", {"target": "ThreadBore", "kind": "cylinder_face", "radius": 4,
                       "max_results": 1}, "ok", _fg("bore_wall")),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "bore_wall", "the bore wall")],
                                "designation": "M8x1.25"},
     lambda p: p.get("internal") is True and p.get("designation") == "M8x1.25", None),
    ("model_thread", lambda c: {"faces": [_ctx_get(c, "bore_wall", "the bore wall")],
                                "designation": "M8x1.25", "length": 10, "location": "low"},
     lambda p: p.get("internal") is True and p.get("location") == "low"
     and p.get("length") == 10, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # a model EDGE handle as the path: the placement call accepts it and Fusion then rejects the
    # add, so the guard refuses it up front and points at sketch_project.
    ("sketch_set_text", lambda c: {"text": "EDGE", "sketch_name": "TextPaths", "create": True,
                                   "mode": "along_path",
                                   "path": _ctx_get(c, "cd_edge", "a model edge handle")},
     "refused", None),
    # design_remove_feature: cast a scratch body, remove it (the census is the verdict), then
    # delete the Remove feature - the body comes back, which is the reversibility the note claims.
    ("model_create_component", {"name": "RmScratch", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RmS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1100, "y1": 0,
                                           "x2": 1120, "y2": 20}],
                             "sketch_name": "RmS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RmS", "profile_index": 0, "distance": 5}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("design_remove_feature", {"body": "RmScratch"},
     lambda p: p.get("target_kind") == "body" and p.get("feature_read_back") is True,
     ("rm_feat", lambda p: p["feature"])),
    ("design_delete_feature", lambda c: {"feature": _ctx_get(c, "rm_feat", "remove feature")},
     "ok", None),
    # the body is back: a component with no body has no faces, so exactly one match discriminates.
    ("find_geometry", {"target": "RmScratch", "kind": "planar_face", "max_results": 1},
     lambda p: len(p.get("matches") or []) == 1, None),
    # the occurrence variant of the same round trip.
    ("design_remove_feature", {"occurrence": "RmScratch:1"},
     lambda p: p.get("target_kind") == "occurrence" and p.get("feature_read_back") is True,
     ("rm_feat2", lambda p: p["feature"])),
    ("design_delete_feature", lambda c: {"feature": _ctx_get(c, "rm_feat2", "remove feature")},
     "ok", None),
    ("model_inspect", {"target": "RmScratch:1"}, _extent_measured, None),
    # model_emboss: a raise then an engrave on one scratch block's top face. The profiles are drawn
    # on a datum plane COINCIDENT with that face, so each sketch holds exactly one profile (an
    # on-face sketch auto-projects the face boundary and would offer two). The verdict is the
    # measured volume direction: 'mode' echoes the sign of the depth and the call is refused when
    # the material moved the other way.
    *_box("EmbossBlock", ox=600, oy=100),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 10, "name": "EmbPlane"},
     _datum_plane("xy"), None),
    ("find_geometry", {"target": "EmbossBlock", "kind": "planar_face", "nearest_to": [610, 110, 10],
                       "max_results": 1}, "ok", _fg("emb_top")),
    ("sketch_create", {"plane": "EmbPlane", "name": "EmbRaise"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 605, "cy": 105, "radius": 3}],
                             "sketch_name": "EmbRaise"}, "ok", None),
    ("sketch_get", {"sketch_name": "EmbRaise"}, "ok", _prof("emb_prof_up")),
    ("model_emboss", lambda c: {"profiles": [_ctx_get(c, "emb_prof_up", "emboss profile")],
                                "faces": [_ctx_get(c, "emb_top", "emboss block top")], "depth": 2},
     lambda p: p.get("mode") == "raise" and p.get("volume_delta_cm3", 0) > 0,
     ("emb_feat", lambda p: p["feature"])),
    # the top face was re-cut by the raise, so the engrave takes a FRESH handle for it.
    ("find_geometry", {"target": "EmbossBlock", "kind": "planar_face", "nearest_to": [610, 110, 10],
                       "max_results": 1}, "ok", _fg("emb_top2")),
    ("sketch_create", {"plane": "EmbPlane", "name": "EmbCut"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 615, "cy": 115, "radius": 3}],
                             "sketch_name": "EmbCut"}, "ok", None),
    ("sketch_get", {"sketch_name": "EmbCut"}, "ok", _prof("emb_prof_down")),
    ("model_emboss", lambda c: {"profiles": [_ctx_get(c, "emb_prof_down", "engrave profile")],
                                "faces": [_ctx_get(c, "emb_top2", "emboss block top")], "depth": -2},
     lambda p: p.get("mode") == "engrave" and p.get("volume_delta_cm3", 0) < 0, None),
    ("model_emboss", lambda c: {"profiles": [_ctx_get(c, "emb_prof_up", "emboss profile")],
                                "faces": [_ctx_get(c, "emb_top2", "emboss block top")], "depth": 0},
     "refused", None),
    # model_mirror's FEATURE mode, on the emboss the beat above just made - an EmbossFeature is the
    # class MEASURED as accepted by the input collection. The mirror plane runs down the block's own
    # middle so the mirrored emboss lands back ON the block; the body/volume census is the verdict,
    # since MirrorFeature.resultFeatures.count reads None even when the mirror mints geometry.
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": 610, "name": "EmbMirrorPlane"},
     _datum_plane("yz"), None),
    ("model_mirror", lambda c: {"features": [_ctx_get(c, "emb_feat", "the emboss feature")],
                                "plane": "EmbMirrorPlane"},
     lambda p: p.get("mode") == "features"
     and (p.get("bodies_added") or p.get("volume_change_cm3")), None),
    # join is a BODY-mode setting and 'joined' is read off the created feature, never echoed. The
    # body is named through a FRESH face handle: the mirrored emboss re-cut the one taken above.
    # A JOIN NEEDS THE TWO HALVES TO TOUCH, and the mirror plane is what decides whether they do.
    # About an ORIGIN plane this block's reflection lands 200 mm away with nothing between them:
    # Fusion keeps the feature, makes a second body and marks it "Could not join, multiple bodies
    # created" - a timeline WARNING left in the finished document, which the 'joined' flag alone does
    # not catch because the SETTING was honoured even though the join was not. Mirroring about the
    # block's own +X face gives the reflection that face to fuse across, and the volume the tool
    # measures for itself is what says it fused rather than landing beside it.
    ("model_construction", {"kind": "plane", "plane": "yz", "offset": 620, "name": "EmbJoinPlane"},
     _datum_plane("yz"), None),
    ("find_geometry", {"target": "EmbossBlock", "kind": "planar_face", "nearest_to": [610, 110, 10],
                       "max_results": 1}, "ok", _fg("emb_body")),
    ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "emb_body", "the emboss block body")],
                                "plane": "EmbJoinPlane", "join": True},
     lambda p: p.get("joined") is True and _mirrored(p), None),
    ("model_mirror", lambda c: {"features": [_ctx_get(c, "emb_feat", "the emboss feature")],
                                "plane": "EmbMirrorPlane", "join": True}, "refused", None),
    # model_replace_face: a scratch block whose top face is replaced by an OPEN sheet sitting above
    # it. The sheet is sloped, so the body's measured volume has to move - a replace that changed
    # nothing is an error, not a quiet ok.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "ReplBlock", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ReplS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 660, "y1": 0,
                                           "x2": 690, "y2": 30}],
                             "sketch_name": "ReplS"}, "ok", None),
    ("model_extrude", {"sketch_name": "ReplS", "profile_index": 0, "distance": 20}, _extruded, None),
    # the replacement rides its own component so one find_geometry names it without ambiguity, and
    # surface_extrude reads is_solid=false back off the result - which is what makes it a legal
    # target. Symmetric, so the sheet spans the block whichever way the extrude runs.
    ("model_create_component", {"name": "ReplRoof", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "ReplRoofS"}, "ok", None),
    # on the xz plane sketch +Y maps to world -Z, so y=-26 puts the sheet at world z=+26 - a FLAT
    # plane ABOVE the z0-20 block, the measured-computable replace shape (a tilted or below-the-
    # block sheet fails compute with ASM_REPL_FACE_FAILED).
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 655, "y1": -26,
                                           "x2": 695, "y2": -26}],
                             "sketch_name": "ReplRoofS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "ReplRoofS", "distance": 80, "symmetric": True},
     lambda p: p.get("is_solid") is False, None),
    ("find_geometry", {"target": "ReplRoof", "kind": "planar_face", "max_results": 1}, "ok",
     _fg("repl_sheet")),
    # build the feature on the component that owns the BODY, not the one holding the sheet.
    ("design_activate_component", {"occurrence": "ReplBlock:1"}, "ok", None),
    ("find_geometry", {"target": "ReplBlock", "kind": "planar_face", "nearest_to": [675, 15, 20],
                       "max_results": 1}, "ok", _fg("repl_top")),
    # a clean parametric replace has a feature to read AND a measured volume move, so the payload
    # carries no 'effect_unverified' hedge - that key appears only when neither could be read.
    ("model_replace_face", lambda c: {"faces": [_ctx_get(c, "repl_top", "block top")],
                                      "target": _ctx_get(c, "repl_sheet", "the open roof sheet")},
     lambda p: p.get("replaced") is True and p.get("volume_delta_cm3") not in (None, 0)
     and "effect_unverified" not in p, None),
    # a SOLID face as the replacement target is refused: the target must be a surface face or body.
    ("find_geometry", {"target": "ReplBlock", "kind": "planar_face", "nearest_to": [675, 15, 0],
                       "max_results": 1}, "ok", _fg("repl_bottom")),
    ("find_geometry", {"target": "ReplBlock", "kind": "planar_face", "nearest_to": [660, 15, 10],
                       "max_results": 1}, "ok", _fg("repl_side")),
    ("model_replace_face", lambda c: {"faces": [_ctx_get(c, "repl_side", "block side")],
                                      "target": _ctx_get(c, "repl_bottom", "a solid face")},
     "refused", None),
    # model_pipe: a HOLLOW pipe on its own path (the wall is read back off the created feature),
    # then a HALF-path pipe whose bounding box proves path_fraction is a FRACTION - 20 mm of pipe on
    # a 40 mm path, not 0.5 mm - and finally the reverse extent refused on an OPEN path, which the
    # platform would otherwise swallow in silence.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeRun", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "PipeRunPath"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 720, "y1": 0,
                                           "x2": 720, "y2": 40}],
                             "sketch_name": "PipeRunPath"}, "ok", None),
    # PipeFeature.startFaces/endFaces/sideFaces all read EMPTY on a freshly added hollow pipe, so
    # nothing on this build can say whether the ends are capped - and the payload publishes no
    # 'capped_ends' key rather than a guess dressed as a read.
    ("model_pipe", {"path": "sketch:PipeRunPath", "section_size": 10, "wall_thickness": 1.5},
     lambda p: p.get("hollow") is True and abs((p.get("wall_thickness") or 0) - 1.5) < 1e-6
     and "capped_ends" not in p, None),
    # a five-curve TANGENT stadium (line, arc, line, arc, line), each joint coincident-constrained
    # but the curves' own SketchPoints unmerged - the showcase's hoop as agents draw it. Curves
    # that merely touch by coordinate are not a path at all (Path.create raises, measured burn83);
    # constrained, the collection build takes every curve, so the pipe follows the whole 102.83 mm
    # loop and its volume is the d10 section area times that length, not the first leg.
    ("model_create_component", {"name": "PipeChain", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PipeChainPath"}, "ok", None),
    # (x >= 900: clear of the story's stock and bracket, which the interference census walks)
    ("sketch_add_geometry", {"geometry": [
        {"kind": "line", "x1": 905, "y1": 0, "x2": 920, "y2": 0},
        {"kind": "arc", "cx": 920, "cy": 10, "x1": 920, "y1": 0, "sweep_deg": 180},
        {"kind": "line", "x1": 920, "y1": 20, "x2": 900, "y2": 20},
        {"kind": "arc", "cx": 900, "cy": 10, "x1": 900, "y1": 20, "sweep_deg": 180},
        {"kind": "line", "x1": 900, "y1": 0, "x2": 905, "y2": 0}],
                             "sketch_name": "PipeChainPath"}, "ok", None),
    ("sketch_constrain", {"constraints": [
        {"constraint": "coincident", "entity_one": "line:0:end", "entity_two": "arc:0:start"},
        {"constraint": "coincident", "entity_one": "arc:0:end", "entity_two": "line:1:start"},
        {"constraint": "coincident", "entity_one": "line:1:end", "entity_two": "arc:1:start"},
        {"constraint": "coincident", "entity_one": "arc:1:end", "entity_two": "line:2:start"},
        {"constraint": "coincident", "entity_one": "line:2:end", "entity_two": "line:0:start"}],
                          "sketch_name": "PipeChainPath"}, "ok", None),
    ("model_pipe", {"path": "sketch:PipeChainPath", "section_size": 10},
     lambda p: _measured("5-curve tangent stadium pipes the whole 102.83 mm loop at d10",
                         {"path_curves": p.get("path_curves"),
                          "path_sketch_curves": p.get("path_sketch_curves"),
                          "volume_cm3": p.get("volume_cm3")},
                         p.get("path_curves") == 5 and p.get("path_sketch_curves") == 5
                         and _near(p.get("volume_cm3"), 8.076396, 0.1)), None),
    ("model_create_component", {"name": "PipeHalf", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "PipeHalfPath"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 760, "y1": 0,
                                           "x2": 760, "y2": 40}],
                             "sketch_name": "PipeHalfPath"}, "ok", None),
    ("model_pipe", {"path": "sketch:PipeHalfPath", "section_size": 6, "section_type": "square",
                    "path_fraction": 0.5}, _piped, None),
    # the occurrence bounding box counts BODIES only, so the path sketch cannot inflate this read.
    ("model_inspect", {"target": "PipeHalf:1"}, lambda p: abs(p.get("z", 0) - 20.0) < 1.0, None),
    ("model_pipe", {"path": "sketch:PipeRunPath", "section_size": 6, "path_fraction": 0.5,
                    "path_fraction_reverse": 0.4}, "refused", None),
    # a CUT scoped to named bodies: participantBodies is write-only on the created feature, so the
    # scope cannot be read back - the note says the list is what was REQUESTED rather than dressing
    # an echo up as a read-back. The block is built around the path so the cut has material to take.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeCut", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PipeCutS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 800, "y1": -20,
                                           "x2": 840, "y2": 20}],
                             "sketch_name": "PipeCutS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PipeCutS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("sketch_create", {"plane": "xz", "name": "PipeCutPath"}, "ok", None),
    # on an xz sketch +Y maps to world -Z, so this line runs through the block at world z=10, y=0,
    # entering and leaving it - a cut that removes real material.
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 790, "y1": -10,
                                           "x2": 850, "y2": -10}],
                             "sketch_name": "PipeCutPath"}, "ok", None),
    ("model_pipe", {"path": "sketch:PipeCutPath", "section_size": 8, "operation": "cut",
                    "target_bodies": ["PipeCut"]},
     lambda p: "REQUESTED" in (p.get("note") or "") and p.get("scoped_to_bodies"), None),
    # a pipe JOIN whose path touches nothing: the tube lands as a body of its own, so the error
    # names the new orphan body and points at model_combine(join) instead of just "nothing changed".
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeJoinMiss", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PipeJoinTargetS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1000, "y1": 0,
                                           "x2": 1020, "y2": 20}],
                             "sketch_name": "PipeJoinTargetS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PipeJoinTargetS", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "PipeJoinMissPath"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 1050, "y1": 0,
                                           "x2": 1050, "y2": 40}],
                             "sketch_name": "PipeJoinMissPath"}, "ok", None),
    # the miss as a CUT first, scoped to the box (the component's ONE body before the join lands
    # its orphan; an unscoped miss raises NO_TARGET_SWEEP_BODY and leaves nothing - measured): the
    # feature lands with no effect, retained at WARNING severity ("No target body!") - the boundary
    # FSAE-0922-FAILED-FEATURE-SEVERITY-1 keeps apart from an ERROR, which alone breaks is_healthy.
    ("model_pipe", {"path": "sketch:PipeJoinMissPath", "section_size": 6, "operation": "cut",
                    "target_bodies": ["PipeJoinMiss"]},
     _refused("no body's volume changed", "remains in the timeline"), None),
    # then the same path as a JOIN lands an orphan tube, which the error names
    ("model_pipe", {"path": "sketch:PipeJoinMissPath", "section_size": 6, "operation": "join"},
     _refused("no body's volume changed", "operation='join' landed a NEW body (",
              "model_combine(join) merges them"), None),
    # is_healthy must stay TRUE over a retained WARNING feature - only an ERROR breaks it.
    ("assembly_get", {},
     lambda p: _measured("a retained WARNING-severity pipe feature reads healthy on assembly_get",
                         {"is_healthy": p.get("is_healthy"),
                          "timeline_problems": p.get("timeline_problems"),
                          "timeline_warnings": p.get("timeline_warnings")},
                         p.get("is_healthy") is True and p.get("timeline_problems") == []
                         and any("Pipe" in (w.get("name") or "")
                                for w in (p.get("timeline_warnings") or []))), None),
    # the SAME state through workspace_orient's health counts (uncapped, unlike the timeline
    # slice's row list) - a warning, no error, is_healthy true: the severity must agree.
    ("workspace_orient", {},
     lambda p: _measured("workspace_orient counts the retained pipe as a warning, not an error",
                         {"health": p.get("health")},
                         (p.get("health") or {}).get("timeline_errors") == 0
                         and ((p.get("health") or {}).get("timeline_warnings") or 0) >= 1
                         and (p.get("health") or {}).get("is_healthy") is True), None),
    # a d70 CUT across a 100 x 30 x 8 plate: the tube is wider than the plate, so it DISCONNECTS
    # the plate into two bodies - the split is named, not left for 'result_bodies' alone to imply.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeSplit", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PipeSplitS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1100, "y1": 0,
                                           "x2": 1200, "y2": 30}],
                             "sketch_name": "PipeSplitS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PipeSplitS", "profile_index": 0, "distance": 8},
     _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "PipeSplitPath"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 1150, "y1": -20,
                                           "x2": 1150, "y2": 50}],
                             "sketch_name": "PipeSplitPath"}, "ok", None),
    ("model_pipe", {"path": "sketch:PipeSplitPath", "section_size": 70, "operation": "cut",
                    "target_bodies": ["PipeSplit"]},
     lambda p: _measured("a d70 cut across a 100x30x8 plate DISCONNECTS it into 2 bodies",
                         {"body_split": p.get("body_split"), "note": (p.get("note") or "")[:200]},
                         isinstance(p.get("body_split"), list) and len(p["body_split"]) == 2
                         and "DISCONNECTED" in (p.get("note") or "")), None),
    # sketch_add_3d_spline: a 2-turn helix (r20 mm, pitch30 mm) as a FITTED spline pipes at d4 to
    # the analytic tube volume (pi*r_section^2*helix_length = 3.2477 cm3, measured within 0.04%);
    # the SAME helix as a CONTROL-POINT spline mints construction lines beside it (one per
    # control-polygon segment) and still pipes as ONE path curve - a pipe ignores construction geometry.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PipeSpline", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PipeSplineFitted"}, "ok", None),
    ("sketch_add_3d_spline", {"sketch_name": "PipeSplineFitted", "kind": "fitted",
                              "helix": {"axis": "z", "center": [2800, 0, 0], "radius": 20,
                                        "pitch": 30, "turns": 2}},
     lambda p: p.get("kind") == "fitted" and p.get("point_count") == 49
     and p.get("off_plane") is True, None),
    ("model_pipe", {"path": "sketch:PipeSplineFitted", "section_size": 4},
     lambda p: _measured("a 2-turn r20/pitch30 helix pipes at d4 to the analytic tube volume",
                         {"path_curves": p.get("path_curves"), "volume_cm3": p.get("volume_cm3")},
                         p.get("path_curves") == 1 and _near(p.get("volume_cm3"), 3.2477, 0.033)),
     None),
    ("sketch_create", {"plane": "xy", "name": "PipeSplineControl"}, "ok", None),
    ("sketch_add_3d_spline", {"sketch_name": "PipeSplineControl", "kind": "control", "degree": "3",
                              "helix": {"axis": "z", "center": [2900, 0, 0], "radius": 20,
                                        "pitch": 30, "turns": 2}},
     lambda p: p.get("kind") == "control"
     and (p.get("construction_lines_added") or 0) > 0, None),
    ("model_pipe", {"path": "sketch:PipeSplineControl", "section_size": 4},
     lambda p: p.get("path_curves") == 1, None),
    # BUILD_PATH's measured chaining rule, on two fixtures of its own. ONE seed handle is not one
    # edge: chaining follows TANGENT CONTINUITY and stops where that continuity breaks - a sharp
    # corner ends an open run, while a genuinely tangent loop chains the whole way round ([F52a],
    # and [F68] which corrected [F52b]: the earlier no-chaining reading came from a rig whose
    # junctions ran through fillet corner PATCHES, not from the loop being closed). Neither open
    # nor closed predicts the number, so the label's count is read off the BUILT path - these two
    # beats are what keep the wording honest for every consumer of the shared resolver (sweep /
    # pipe / path pattern / on-path datum).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TangentRun", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TangentRunS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1900, "y1": 0,
                                           "x2": 1960, "y2": 60}],
                             "sketch_name": "TangentRunS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TangentRunS", "profile_index": 0, "distance": 20},
     _extruded, None),
    # ONE vertical edge rounded: the top rim reads line - arc - line, bounded by sharp corners.
    ("find_geometry", {"target": "TangentRun", "kind": "line_edge", "nearest_to": [1900, 0, 10],
                       "max_results": 1}, "ok", _fg("tr_corner")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "tr_corner", "the box corner edge")],
                                "radius": 8}, _filleted, None),
    # a fillet's rim is an ARC (Arc3D), not a full circle - find_geometry keys its edge kinds off
    # the curve type, so 'circular_edge' does not match it and 'arc_edge' is the pick.
    ("find_geometry", {"target": "TangentRun", "kind": "arc_edge", "nearest_to": [1900, 0, 20],
                       "max_results": 1}, "ok", _fg("tr_arc")),
    ("find_geometry", {"target": "TangentRun", "kind": "line_edge", "nearest_to": [1940, 0, 20],
                       "max_results": 1}, "ok", _fg("tr_line")),
    # TWO handles are used EXACTLY - no chaining at all - and the label says which of the two rules
    # ran, so a list quietly chained into more edges could not report this.
    ("find_geometry", {"target": "TangentRun", "kind": "planar_face", "nearest_to": [1930, 30, 20],
                       "max_results": 1}, "ok", _fg("tr_body")),
    ("model_pattern_path", lambda c: {"bodies": [_ctx_get(c, "tr_body", "the tangent-run body")],
                                      "path": [_ctx_get(c, "tr_arc", "the fillet arc"),
                                               _ctx_get(c, "tr_line", "the tangent-adjacent line")],
                                      "quantity": 2, "distance": 6, "distance_type": "spacing"},
     lambda p: p.get("path") == "2 edge(s) from 2 handles, used exactly", None),
    # ONE seed on the OPEN run: the arc chains across both tangent connections, so the built path
    # holds MORE than the seed.
    ("model_pipe", lambda c: {"path": _ctx_get(c, "tr_arc", "the fillet arc"), "section_size": 3},
     lambda p: _path_count(p.get("path"), 1) > 1, None),
    # the CLOSED tangent loop: all four verticals rounded, so the top rim is 4 lines + 4 arcs, every
    # junction tangent. One seed chains the WHOLE loop - all 8 edges - and the built path reports
    # itself closed. Rounding the VERTICALS is what makes the junctions tangent: rounding the top
    # edges instead puts a corner patch at each junction and the chain stops there ([F68]).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TangentLoop", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TangentLoopS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 2000, "y1": 0,
                                           "x2": 2060, "y2": 60}],
                             "sketch_name": "TangentLoopS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TangentLoopS", "profile_index": 0, "distance": 20},
     _extruded, None),
    # each vertical edge is the nearest line edge to its own corner at mid-height (10 mm away from
    # the two horizontals meeting there), so the four picks are unambiguous.
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2000, 0, 10],
                       "max_results": 1}, "ok", _fg("tl_e1")),
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2060, 0, 10],
                       "max_results": 1}, "ok", _fg("tl_e2")),
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2060, 60, 10],
                       "max_results": 1}, "ok", _fg("tl_e3")),
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2000, 60, 10],
                       "max_results": 1}, "ok", _fg("tl_e4")),
    ("model_fillet", lambda c: {"edges": [_ctx_get(c, "tl_e1", "loop corner 1"),
                                          _ctx_get(c, "tl_e2", "loop corner 2"),
                                          _ctx_get(c, "tl_e3", "loop corner 3"),
                                          _ctx_get(c, "tl_e4", "loop corner 4")],
                                "radius": 8}, _filleted, None),
    ("find_geometry", {"target": "TangentLoop", "kind": "arc_edge",
                       "nearest_to": [2000, 0, 20], "max_results": 1}, "ok", _fg("tl_arc")),
    ("model_pipe", lambda c: {"path": _ctx_get(c, "tl_arc", "one arc of the closed tangent loop"),
                              "section_size": 3},
     lambda p: _measured("closed tangent loop: want 8 edges from 1 seed, closed",
                         {"path": p.get("path"), "path_closed": p.get("path_closed")},
                         _path_count(p.get("path"), 1) == 8
                         and p.get("path_closed") is True), None),
    # THE SAME CLOSED RIM, now as an edge TREATMENT target: one handle on the front top line, cut
    # with tangent_chain=false. edges_cut is read off the feature's own edge set, and the rim
    # OPPOSITE is then read independently - a chain that reached it would put a 45 deg bevel face
    # centred exactly where the query looks.
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2030, 0, 20],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("tl_rim_front")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "tl_rim_front", "the front top rim line")],
                                 "distance": 1, "tangent_chain": False},
     _cut_exactly("chamfer", 1), None),
    ("find_geometry", {"target": "TangentLoop", "kind": "planar_face",
                       "nearest_to": [2030, 59.5, 19.5], "max_results": 1},
     _axis_aligned_face_at(2030, 59.5, 19.5), None),
    # and the DEFAULT on the back rim line: the seeds chain, so the feature cuts more edges than the
    # one handle it was given. The number is read off the built feature - the beat above broke the
    # rim's tangency at the front, so how far this chain reaches is not something to predict.
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2030, 60, 20],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("tl_rim_back")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "tl_rim_back", "the back top rim line")],
                                 "distance": 1}, _cut_a_chain("chamfer", 1), None),
    # A PARKED MARKER, which reading the resolved edge set has to move and put back. Rolling to the
    # previous step parks it before the chamfer above; the cut below lands at the marker, and the
    # rolled-out feature has to still be rolled out afterwards. The seed is the front BOTTOM rim,
    # which no earlier beat has touched.
    # the parked INDEX is saved off the roll's own receipt: it is where the cut below lands, and a
    # feature name cannot say that - names are component-local and the Bracket has chamfers of its
    # own carrying the same ones.
    ("design_edit_timeline", {"action": "roll", "to": "previous"},
     lambda p: _measured("one feature rolled out", {"rolled_back": p.get("rolled_back"),
                                                    "marker_position": p.get("marker_position")},
                         p.get("rolled") is True and p.get("rolled_back") == 1),
     ("parked_at", _recall("parked_at", lambda p: p["marker_position"]))),
    ("find_geometry", {"target": "TangentLoop", "kind": "line_edge", "nearest_to": [2030, 0, 0],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("tl_rim_low")),
    ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "tl_rim_low", "the front bottom rim line")],
                                 "distance": 1, "tangent_chain": False},
     _cut_exactly("chamfer", 1),
     ("parked_chamfer", _recall("parked_chamfer", lambda p: p["feature"]))),
    # the INDEPENDENT read: the design's own timeline, not the chamfer's receipt.
    ("design_get", {"include": ["timeline"], "max_results": 2000},
     _marker_parked_after("parked_at", "parked_chamfer"), None),
    ("design_edit_timeline", {"action": "roll", "to": "end"},
     lambda p: _measured("nothing left rolled out", {"rolled_back": p.get("rolled_back")},
                         p.get("rolled") is True and p.get("rolled_back") == 0), None),
    # THE BLANKET call, on a box of its own: every one of a cuboid's 12 edges is a sharp corner with
    # no tangent junction to chain across, so the resolved set is the swept set.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "BlanketBox", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "BlanketS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 2200, "y1": 0,
                                           "x2": 2220, "y2": 20}],
                             "sketch_name": "BlanketS"}, "ok", None),
    ("model_extrude", {"sketch_name": "BlanketS", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_chamfer", {"body_name": "BlanketBox", "edge_filter": "all", "distance": 1},
     _cut_exactly("chamfer", 12, chain=True), None),
    # FULL ROUND, on a slab of its own: one face replaced by a round tangent to the two beside it,
    # so the radius comes from the geometry and 'radius' does not drive it at all.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "FullRound", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "FullRoundS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 2100, "y1": 0,
                                           "x2": 2180, "y2": 40}],
                             "sketch_name": "FullRoundS"}, "ok", None),
    ("model_extrude", {"sketch_name": "FullRoundS", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("find_geometry", {"target": "FullRound", "kind": "planar_face", "nearest_to": [2140, 0, 5],
                       "max_results": 1}, _matched(1, "planar_face"), _fg("fr_side")),
    ("find_geometry", {"target": "FullRound", "kind": "planar_face", "nearest_to": [2140, 20, 0],
                       "max_results": 1}, _matched(1, "planar_face"), _fg("fr_bottom")),
    ("find_geometry", {"target": "FullRound", "kind": "planar_face", "nearest_to": [2140, 20, 10],
                       "max_results": 1}, _matched(1, "planar_face"), _fg("fr_top")),
    ("model_fillet", lambda c: {"fillet_type": "full_round",
                                "center_face": _ctx_get(c, "fr_side", "the face to round away"),
                                "faces": [_ctx_get(c, "fr_bottom", "the slab's underside")],
                                "second_faces": [_ctx_get(c, "fr_top", "the slab's top")]},
     _full_rounded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # Section lifecycle: preserve the camera, retain both generated names, remove one exact cut,
    # and independently read the surviving decoy before the existing clear-all cleanup.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("Bracket:1"),
    ("workspace_orient", {}, lambda p: bool(_section_camera(p)),
     ("section_camera_before", _recall("section_camera_before", _section_camera))),
    ("view_section", {"action": "cut", "plane": "xz", "offset": 0, "auto_view": False},
     _section_created(), ("section_one", _recall("section_one", lambda p: p["section"]))),
    ("view_section", {"action": "cut", "plane": "xy", "offset": 0, "auto_view": False},
     _section_created("section_one"),
     ("section_two", _recall("section_two", lambda p: p["section"]))),
    ("view_section", {"action": "list"},
     _section_census(("section_one", "section_two"), (False, True)), None),
    ("view_section", {"action": "clear", "section": "SweepNoSuchSection"},
     _refused("no section named", "Available"), None),
    ("view_section", {"action": "list"},
     _section_census(("section_one", "section_two"), (False, True)), None),
    ("view_section", lambda c: {"action": "clear",
                                "section": _ctx_get(c, "section_one", "the first generated cut")},
     _section_named_clear("section_one"), None),
    ("view_section", {"action": "list"},
     _section_census(("section_two",), (True,)), None),
    ("workspace_orient", {}, _section_camera_unchanged("section_camera_before"), None),
    ("view_screenshot", {"width": 500, "height": 400}, "ok", None),
    ("view_section", {"action": "clear"},
     lambda p: (p["removed"] == [_RECALL.get("section_two")]
                and p["removed_count"] == p["sections_before"] == 1
                and p["sections_after"] == 0), None),
    ("view_screenshot_multi", {"views": ["front", "top"], "width": 400, "height": 300}, "ok", None),
    # THE RASTER WRITER (NEW-13): file_path also writes the rendered PNG to disk - the extension is
    # appended, the landed file is verified non-zero, and path + size are published beside the
    # inline image. The fleet's only raster writer, which is what feeds drawing_insert_image.
    ("view_screenshot", {"width": 400, "height": 300,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot"},
     lambda p: "w4_shot.png" in str(p) and "size_bytes=" in str(p), None),
    # a second write to the SAME path lands without a refusal - the overwrite behaviour that makes
    # this tool write-kind (and puts it behind the write guard below).
    ("view_screenshot", {"width": 200, "height": 150,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot"},
     lambda p: "w4_shot.png" in str(p) and "size_bytes=" in str(p), None),
    ("view_screenshot", {"width": 200, "height": 150,
                         "file_path": r"C:\Users\phili\AppData\Local\Temp\eval_sweep_exports\w4_shot",
                         "expect_document": "ZzNoSuchDocument"},
     _refused("active_document_changed"), None),
    # The dimension/constraint beats that measure TO a model face sit at the end of the act,
    # not in the middle of the modelling: they are sketch work, and they are here only
    # because a face is what they measure against.
    # the angular wedge rule: a horizontal and a 60 deg line crossing far from the origin. The
    # measured contract dims the wedge FACING THE SKETCH ORIGIN - 60 deg, not the 120 supplement.
    ("sketch_create", {"plane": "xy", "name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 900, "y1": 100,
                                           "x2": 920, "y2": 100}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 905, "y1": 91.34,
                                           "x2": 915, "y2": 108.66}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", {"dimensions": [{"dim_type": "angle", "entity_one": "line:0",
                                          "entity_two": "line:1"}],
                          "sketch_name": "W3Dims"},
     lambda p: "deg" in (p["results"][0].get("value") or "")
     and abs(float((p["results"][0].get("value") or "0 x").split()[0]) - 60) < 0.1, None),
    # offset with a NON-parallel second line: the constraint ROTATES it parallel (geometry moves,
    # no raise) and the note says so.
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 940, "y1": 100,
                                           "x2": 960, "y2": 100}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 940, "y1": 110,
                                           "x2": 960, "y2": 113}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", {"dimensions": [{"dim_type": "offset", "entity_one": "line:2",
                                          "entity_two": "line:3"}],
                          "sketch_name": "W3Dims"},
     lambda p: "ROTAT" in (p["results"][0].get("note") or ""), None),
    # linear_diameter with the same shape REFUSES - the API's own parallelism sentence surfaces.
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 980, "y1": 100,
                                           "x2": 1000, "y2": 100}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 980, "y1": 110,
                                           "x2": 1000, "y2": 114}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", {"dimensions": [{"dim_type": "linear_diameter", "entity_one": "line:4",
                                          "entity_two": "line:5"}],
                          "sketch_name": "W3Dims"}, "refused", None),
    # line/point vs a MODEL face: the ShellCap outer -X wall sits on the x=400 plane, 620 mm from
    # a line at x=1020. The value is read back off the parameter; the surface label is the
    # RESOLVED entity, so 'BRepFace' proves the payload is not echoing the handle string.
    ("find_geometry", {"target": "ShellCap", "kind": "planar_face", "nearest_to": [400, 15, 10],
                       "max_results": 1}, "ok", _fg("w3_wall")),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 1020, "y1": 100,
                                           "x2": 1020, "y2": 140}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", lambda c: {"dimensions": [{
        "dim_type": "line_to_surface", "entity_one": "line:6",
        "surface": _ctx_get(c, "w3_wall", "shell wall")}],
        "sketch_name": "W3Dims"},
     lambda p: "mm" in (p["results"][0].get("value") or "")
     and abs(float((p["results"][0].get("value") or "0 x").split()[0])
             - abs(_px("W3Dims", 1020) - _px("ShellCap", 400))) < 0.1
     and p["results"][0].get("surface") == "BRepFace", None),
    # a line NOT parallel to that wall refuses with the API's self-naming error.
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 1040, "y1": 100,
                                           "x2": 1060, "y2": 100}],
                             "sketch_name": "W3Dims"}, "ok", None),
    ("sketch_dimension", lambda c: {"dimensions": [{
        "dim_type": "line_to_surface", "entity_one": "line:7",
        "surface": _ctx_get(c, "w3_wall", "shell wall")}],
        "sketch_name": "W3Dims"}, "refused", None),
    # anchored on line:7 (undimensioned - the refused line_to_surface left it free): dimensioning
    # line:6's own endpoint against the same wall it is dimensioned to over-constrains the sketch.
    ("sketch_dimension", lambda c: {"dimensions": [{
        "dim_type": "point_to_surface", "entity_one": "line:7:start",
        "surface": _ctx_get(c, "w3_wall", "shell wall")}],
        "sketch_name": "W3Dims"},
     lambda p: p["results"][0].get("surface") == "BRepFace", None),
    # shared-resolver regression: the point dim's resolver allows curved faces; the constrain
    # tool rides the same _inputs.resolve_surface, so a cylinder accepted here and refused for
    # line_on_surface pins the allow_curved split. Fresh sketch: the origin point is point:0,
    # so the drawn point is point:1.
    ("sketch_create", {"plane": "xy", "name": "W3Pt"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "point", "cx": 1080, "cy": 100}],
                             "sketch_name": "W3Pt"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 1080, "y1": 120,
                                           "x2": 1100, "y2": 120}],
                             "sketch_name": "W3Pt"}, "ok", None),
    ("sketch_constrain", lambda c: {"constraints": [{
        "constraint": "coincident_to_surface", "entity_one": "point:1",
        "surface": _ctx_get(c, "post_wall", "thread post wall")}],
        "sketch_name": "W3Pt"},
     lambda p: p["results"][0].get("surface") == "BRepFace", None),
    ("sketch_constrain", lambda c: {"constraints": [{
        "constraint": "line_on_surface", "entity_one": "line:0",
        "surface": _ctx_get(c, "post_wall", "thread post wall")}],
        "sketch_name": "W3Pt"}, "refused", None),
]

def _fillet_expression_followed(expression, mm):
    """design_get(include=['timeline'], timeline_params=True): the row whose Radius parameter is
    the EXPRESSION the fillet was built with, and the value that expression now evaluates to.

    A radius passed as a parameter name is stored as that name, so the recompute re-evaluates it -
    a radius baked to a number would read back as the number it was built at. Rows carry their
    value in internal cm, and the feature is found by its own expression rather than by a
    'Fillet<n>' name the platform picks."""
    def check(p):
        rows = (p.get("timeline") or {}).get("timeline") or []
        hit = next(((r, q) for r in rows for q in (r.get("params") or [])
                    if q.get("expression") == expression), (None, None))
        row, param = hit
        return _measured(f"the fillet built at '{expression}' now measures {mm} mm",
                         {"feature": row and row.get("name"), "param": param},
                         param is not None and _near(param.get("value"), mm / 10.0, 1e-3))
    return check


# --- ACT 6: THE RESIZE - the parametric resize check (mirrors scenario S6) ---------------------
# Bump the one driving length; the whole bracket grows. It runs BEFORE the billet and the vise are
# built, which is the order a shop works in: the part is settled, then the stock is sized from it
# and the jaws close on that.
_RESIZE = [
    # the resize walks the whole part - frame it so the features are seen to move.
    _watch("Bracket:1"),
    ("view_screenshot", {"width": 400, "height": 300}, "ok", None),
    ("sketch_get", {"sketch_name": "BracketBody"}, "ok", None),   # before
    ("param_set", {"name": "PartLen", "expression": "160 mm"},
     _param_set_to("PartLen", 160), None),
    # 'new_errors' names the features that came back broken from THIS rebuild (the walk before it is
    # what makes that a difference) - a resize that breaks a downstream feature returns ok.
    ("design_recompute", {},
     lambda p: (p["recomputed"] is True and isinstance(p["error_count"], int)
                and "new_errors" not in p), None),
    ("sketch_get", {"sketch_name": "BracketBody"}, "ok", None),   # after - the block grew
    ("sketch_get", {"sketch_name": "BracketPocket"}, "ok", None),
    # THE MEASURED PROOF, on all three axes: the driver is the part's LENGTH, so x follows it a
    # third longer while the width and the height - their own parameters - hold exactly where they
    # were. A resize that moved every axis is a sketch anchored on the wrong point, and only
    # reading the two that must NOT move says so.
    ("model_inspect", {"target": "Bracket:1"},
     lambda p: _measured("only the length followed the driver (want x 160, y 80, z 45)",
                         {"x": p.get("x"), "y": p.get("y"), "z": p.get("z")},
                         _near(p.get("x"), 160.0, 0.1) and _near(p.get("y"), 80.0, 0.1)
                         and _near(p.get("z"), 45.0, 0.1)), None),
    # ...and the FEATURE side of the same recompute: the pocket's corner fillet was built at the
    # expression 'PocketRad', so the timeline row holds that name and the value it evaluates to now
    # (PartLen/15 at the driven 160 mm), not the 8 mm the feature was created at.
    ("design_get", {"include": ["timeline"], "timeline_params": True},
     _fillet_expression_followed("PocketRad", 160.0 / 15.0), None),
    # the StockCenter JO (the CAM WCS anchor) read back after the resize: parametrically anchored at
    # the part's own origin, it HOLDS position through the recompute.
    ("assembly_get", {"include": ["joint_origins"]}, _joint_origins_listed("StockCenter"), None),
    ("view_screenshot", {"width": 400, "height": 300}, "ok", None),
    ("param_set", {"name": "PartLen", "expression": "120 mm"},
     _param_set_to("PartLen", 120), None),   # restore
    ("design_recompute", {}, "ok", None),
    ("sketch_get", {"sketch_name": "BracketBody"}, "ok", None),   # restored
    ("param_add", {"name": "ScratchDim", "expression": "5 mm"}, _param_added("ScratchDim", 5), None),
    ("param_delete", {"name": "ScratchDim"}, _param_deleted("ScratchDim"), None),
    # the design at rest after the rebuild: the census the check ran, with the rows it found.
    ("assembly_inspect_interference", {}, _interference_measured, None),
    # FSAE-0922-INTERFERENCE-INSTANCE-1: a component instanced twice must be named EXACTLY, never
    # guessed from candidates - and two occurrences with no body must refuse, never pass. Isolated
    # in its own scratch document so the census is exactly what this rig places.
    ("doc_get", {}, _home_document,
     ("interference_story", _recall("interference_story", _home_address))),
    ("doc_new",
     lambda c: {"expect_document": _ctx_get(c, "interference_story", "the story document")},
     _new_document,
     ("interference_scratch", _recall("interference_scratch", lambda p: p["document_handle"]))),
    ("model_create_component",
     lambda c: _interference_pin(c, {"name": "EmptyA", "activate": True}), _made_component, None),
    ("design_activate_component",
     lambda c: _interference_pin(c, {"occurrence": "root"}), "ok", None),
    ("model_create_component",
     lambda c: _interference_pin(c, {"name": "EmptyB", "activate": True}), _made_component, None),
    ("design_activate_component",
     lambda c: _interference_pin(c, {"occurrence": "root"}), "ok", None),
    # two occurrences with ZERO solid bodies between them - the false pass this row exists to catch.
    ("assembly_inspect_interference", {}, _refused("EmptyA:1", "EmptyB:1", "NOT a pass"), None),
    ("model_create_component",
     lambda c: _interference_pin(c, {"name": "BlockA", "activate": True}), _made_component, None),
    ("sketch_create",
     lambda c: _interference_pin(c, {"plane": "xy", "name": "BlockAS"}), "ok", None),
    ("sketch_add_geometry",
     lambda c: _interference_pin(c, {"geometry": [{"kind": "rectangle", "x1": 0, "y1": 0,
                                                    "x2": 10, "y2": 10}], "sketch_name": "BlockAS"}),
     "ok", None),
    ("model_extrude",
     lambda c: _interference_pin(c, {"sketch_name": "BlockAS", "profile_index": 0, "distance": 10}),
     _extruded, None),
    ("design_activate_component",
     lambda c: _interference_pin(c, {"occurrence": "root"}), "ok", None),
    ("model_create_component",
     lambda c: _interference_pin(c, {"name": "BlockB", "activate": True, "x": 50}),
     _made_component, None),
    ("sketch_create",
     lambda c: _interference_pin(c, {"plane": "xy", "name": "BlockBS"}), "ok", None),
    ("sketch_add_geometry",
     lambda c: _interference_pin(c, {"geometry": [{"kind": "rectangle", "x1": 0, "y1": 0,
                                                    "x2": 10, "y2": 10}], "sketch_name": "BlockBS"}),
     "ok", None),
    ("model_extrude",
     lambda c: _interference_pin(c, {"sketch_name": "BlockBS", "profile_index": 0, "distance": 10}),
     _extruded, None),
    ("design_activate_component",
     lambda c: _interference_pin(c, {"occurrence": "root"}), "ok", None),
    ("model_create_component",
     lambda c: _interference_pin(c, {"name": "Peg", "activate": True}), _made_component, None),
    ("sketch_create",
     lambda c: _interference_pin(c, {"plane": "xy", "name": "PegS"}), "ok", None),
    ("sketch_add_geometry",
     lambda c: _interference_pin(c, {"geometry": [{"kind": "circle", "cx": 5, "cy": 5, "radius": 2}],
                                     "sketch_name": "PegS"}), "ok", None),
    ("model_extrude",
     lambda c: _interference_pin(c, {"sketch_name": "PegS", "profile_index": 0, "distance": 10}),
     _extruded, None),
    ("design_activate_component",
     lambda c: _interference_pin(c, {"occurrence": "root"}), "ok", None),
    # a second instance of the SAME component, overlapping the OTHER block - the multi-instance case
    # analyzeInterference alone cannot name (native bodies carry no assemblyContext).
    ("design_add_instance",
     lambda c: _interference_pin(c, {"component": "Peg", "x": 50}),
     lambda p: p.get("created") is True, None),
    ("assembly_inspect_interference", {}, _interference_pair_named, None),
    ("doc_activate",
     lambda c: {"name": _ctx_get(c, "interference_story", "the story document"),
                "expect_document": _ctx_get(c, "interference_scratch",
                                            "the interference scratch document")},
     "ok", None),
    ("doc_close",
     lambda c: {"name": _ctx_get(c, "interference_scratch", "the interference scratch document"),
                "save_changes": False,
                "expect_document": _ctx_get(c, "interference_story", "the story document")},
     _document_closed, None),
    # TIMELINE: roll back over the assembly, group a range, suppress and restore, then return the
    # marker to the end. Every beat reads the marker back, and the blast-radius refusal is exercised
    # WITHOUT the confirmation so nothing is discarded from the story.
    ("design_edit_timeline", {"action": "roll", "to": "previous"},
     lambda p: p.get("marker_position") == p.get("marker_position_before", -1) - 1, None),
    ("design_edit_timeline", {"action": "delete_after_marker"}, "refused", None),
    ("design_edit_timeline", {"action": "roll", "to": "end"},
     lambda p: p.get("rolled_back") == 0, None),
    ("design_edit_timeline", {"action": "roll", "feature": "NoSuchFeature"}, "refused", None),
    ("design_edit_timeline", {"action": "group", "feature": "ScratchDim",
                              "end_feature": "ScratchDim"}, "refused", None),
    # ATTRIBUTES: tag a real timeline feature (the part's own step floor), re-tag it,
    # then remove the tag. An attribute reached through the timeline lives on the ENTITY the item
    # wraps, and every beat reads back both the value on that entity and the design-wide census of
    # the group/name pair - which is what turns the delete into a verdict instead of a claim.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "StepFloor",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note",
                              "attribute_value": "beat-1"},
     lambda p: p.get("value") == "beat-1" and p.get("design_matches", 0) >= 1
     and "previous_value" not in p, None),
    # add() on an existing group/name UPDATES in place, so the value it replaced is readable only
    # before the call - and it is disclosed rather than lost.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "StepFloor",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note",
                              "attribute_value": "beat-2"},
     lambda p: p.get("previous_value") == "beat-1" and p.get("value") == "beat-2", None),
    # this tool's own wire bound on the value, refused with the length that broke it.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "StepFloor",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note",
                              "attribute_value": "x" * 10001}, "refused", None),
    # a leading 're:' turns the attribute search into a REGULAR EXPRESSION instead of naming this
    # literal group, so it is refused rather than silently matching something else.
    ("design_edit_timeline", {"action": "set_attribute", "feature": "StepFloor",
                              "attribute_group": "re:sweep", "attribute_name": "note",
                              "attribute_value": "beat-3"}, "refused", None),
    ("design_edit_timeline", {"action": "delete_attribute", "feature": "StepFloor",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note"},
     lambda p: p.get("attribute_deleted") is True and p.get("deleted_value") == "beat-2"
     and p.get("design_matches") == 0, None),
    # the same delete again has nothing to remove, and says so naming the group/name pair.
    ("design_edit_timeline", {"action": "delete_attribute", "feature": "StepFloor",
                              "attribute_group": "sweep_w11_8", "attribute_name": "note"},
     "refused", None),
    # THE 'name@index' FORM - a valid address in its own right, resolved by reading each timeline
    # object's OWN .index - the same number design_get publishes - never by position in a list, so
    # the index taken from this read is the index that must resolve.
    # the slice is a DICT (marker_position / count / summary / groups / timeline) and the ordered
    # rows sit under its own 'timeline' key - each a terse {index, name, type}.
    ("design_get", {"include": ["timeline"]},
     lambda p: any(r.get("name") == "StepFloor" for r in p["timeline"]["timeline"]),
     ("hub_index", lambda p: next(r["index"] for r in p["timeline"]["timeline"]
                                  if r["name"] == "StepFloor"))),
    ("design_edit_timeline", lambda c: {
        "action": "set_attribute",
        "feature": "StepFloor@{0}".format(_ctx_get(c, "hub_index", "the step floor's index")),
        "attribute_group": "sweep_w1d", "attribute_name": "at", "attribute_value": "by-index"},
     lambda p: p.get("value") == "by-index" and p.get("feature") == "StepFloor", None),
    ("design_edit_timeline", lambda c: {
        "action": "delete_attribute",
        "feature": "StepFloor@{0}".format(_ctx_get(c, "hub_index", "the step floor's index")),
        "attribute_group": "sweep_w1d", "attribute_name": "at"},
     lambda p: p.get("attribute_deleted") is True, None),
    # the NEIGHBOURING index: the pair must agree, so an off-by-one is refused - and the refusal
    # says what sits at the index asked for and which index 'StepFloor' answers to now, rather than
    # editing the feature next door.
    ("design_edit_timeline", lambda c: {
        "action": "set_attribute",
        "feature": "StepFloor@{0}".format(_ctx_get(c, "hub_index",
                                                         "the step floor's index") + 1),
        "attribute_group": "sweep_w1d", "attribute_name": "at", "attribute_value": "x"},
     _refused("'StepFloor' is at index", "design_get(include=['timeline'])"), None),
    # THE '<component>/<name>' FORM: two components each hold a plane named 'AddrPlane', so the bare
    # name is refused naming BOTH qualified candidates; the qualified form deletes the right one, and
    # the bare name resolving alone afterwards is the independent proof it was the right one that went.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AddrCompA", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "AddrPlane"},
     _datum_plane("xy"), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AddrCompB", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "AddrPlane"},
     _datum_plane("xy"), None),
    ("design_delete_feature", {"feature": "AddrPlane"},
     _refused("AddrCompA/AddrPlane", "AddrCompB/AddrPlane", "matches 2 timeline objects"), None),
    ("design_delete_feature", {"feature": "AddrCompA/AddrPlane"},
     lambda p: p["deleted"] is True and p["feature"] == "AddrPlane", None),
    ("design_delete_feature", {"feature": "AddrPlane"},
     lambda p: p["deleted"] is True and p["feature"] == "AddrPlane", None),
]

# --- the retained SCRATCH fixtures - the precondition fallbacks (today's proven step bodies) ---
# When an act's precondition read fails (an upstream act could not build the geometry it consumes),
# the act runs one of these instead, so its tools are still covered - each row marked "(fallback
# fixture)". These are the minimal self-contained scratch fixtures the sweep has always used.

_SOLIDS_FB = (
    _box("FbSolid")
    + [
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fb_body")),
        ("appearance_set", {"target": "FbSolid", "color": "#1E8E3E"}, "ok", None),
        ("model_set_material", {"target": "FbSolid", "material": "Steel"}, _material_assigned, None),
        ("model_mirror", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "plane": "yz"}, _mirrored, None),
        ("model_pattern_rectangular", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "quantity_one": 2, "spacing_one": 60, "direction_one": "y"}, _patterned("total_instances", 2), None),
        ("model_pattern_circular", lambda c: {"bodies": [_ctx_get(c, "fb_body", "body handle")], "quantity": 3, "total_angle_deg": 360, "axis": "z"}, _patterned("quantity", 3), None),
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fb_top")),
        ("model_hole", lambda c: {"face": _ctx_get(c, "fb_top", "top face"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[5, 5, 0]]}, _drilled(1), None),
        ("find_geometry", {"target": "FbSolid", "kind": "planar_face", "nearest_to": [0, 10, 5], "max_results": 1}, "ok", _fg("fb_side")),
        ("model_draft", lambda c: {"faces": [_ctx_get(c, "fb_side", "side face")], "pull_direction": "xy", "angle_deg": 3},
         _drafted, None),
        ("model_measure_between", lambda c: {"a": _ctx_get(c, "fb_body", "body"), "b": "FbSolid"}, _gap_measured, None),
        # a face compared with ITSELF is parallel to itself, so this beat asserts the verdict too.
        ("model_measure_relation", lambda c: {"relation": "parallel", "entity_a": _ctx_get(c, "fb_top", "top face"), "entity_b": _ctx_get(c, "fb_top", "top face")}, _relation_passes("parallel"), None),
        ("model_inspect", {"target": "FbSolid"}, _extent_measured, None),
        ("model_create_component", {"name": "FbRev", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xz", "name": "FbRevS"}, "ok", None),
        ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 10, "y1": 0,
                                               "x2": 20, "y2": 30}],
                                 "sketch_name": "FbRevS"}, "ok", None),
        ("model_revolve", {"sketch_name": "FbRevS", "profile_index": 0, "axis": "z", "angle_deg": 360}, _revolved, None),
        ("model_create_component", {"name": "FbSwp", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xz", "name": "FbPath"}, "ok", None),
        ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 0, "y1": 0,
                                               "x2": 0, "y2": 40}],
                                 "sketch_name": "FbPath"}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": "FbProf"}, "ok", None),
        ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 0, "cy": 0, "radius": 5}],
                                 "sketch_name": "FbProf"}, "ok", None),
        ("model_sweep", {"profile": {"sketch": "FbProf", "profile_index": 0}, "path": "sketch:FbPath"}, _swept, None),
        ("model_create_component", {"name": "FbLft", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xy", "name": "FbLb"}, "ok", None),
        ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 0, "cy": 0, "radius": 10}],
                                 "sketch_name": "FbLb"}, "ok", None),
        ("sketch_get", {"sketch_name": "FbLb"}, "ok", _prof("fb_lb")),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": 40, "name": "FbTop"},
         _datum_plane("xy"), None),
        ("sketch_create", {"plane": "FbTop", "name": "FbLt"}, "ok", None),
        ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 0, "cy": 0, "radius": 5}],
                                 "sketch_name": "FbLt"}, "ok", None),
        ("sketch_get", {"sketch_name": "FbLt"}, "ok", _prof("fb_lt")),
        ("model_loft", lambda c: {"profiles": [_ctx_get(c, "fb_lb", "loft bottom"), _ctx_get(c, "fb_lt", "loft top")]}, _lofted, None),
        ("model_create_component", {"name": "FbCmb", "activate": True}, _made_component, None),
        ("sketch_create", {"plane": "xy", "name": "FbCb1"}, "ok", None),
        ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 0, "y1": 0,
                                               "x2": 30, "y2": 30}],
                                 "sketch_name": "FbCb1"}, "ok", None),
        ("model_extrude", {"sketch_name": "FbCb1", "profile_index": 0, "distance": 10}, _extruded, None),
        ("sketch_create", {"plane": "xy", "name": "FbCb2"}, "ok", None),
        ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 20, "y1": 20,
                                               "x2": 50, "y2": 50}],
                                 "sketch_name": "FbCb2"}, "ok", None),
        ("model_extrude", {"sketch_name": "FbCb2", "profile_index": 0, "distance": 10}, _extruded, None),
        ("find_geometry", {"target": "FbCmb", "kind": "planar_face", "nearest_to": [5, 5, 10], "max_results": 1}, "ok", _fg("fb_c1")),
        ("find_geometry", {"target": "FbCmb", "kind": "planar_face", "nearest_to": [45, 45, 10], "max_results": 1}, "ok", _fg("fb_c2")),
        ("model_combine", lambda c: {"target": _ctx_get(c, "fb_c1", "combine target"), "tools": [_ctx_get(c, "fb_c2", "combine tool")], "operation": "join"}, _joined, None),
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        # the WCS anchor by the name the CAM acts bind to, so a fallback world still carries one.
        ("joint_create_origin", {"anchor": "coordinates", "target": "origin",
                                 "name": "StockCenter"},
         _joint_origin_at("StockCenter", 0, 0, 0), None),
    ]
)

# ACT 4 fallback: a scratch details fixture.
_DETAILS_FB = (
    _box("FbDet")
    + [
        ("find_geometry", {"target": "FbDet", "kind": "line_edge", "max_results": 1}, "ok", _fg("fd_edge")),
        ("model_fillet", lambda c: {"edges": [_ctx_get(c, "fd_edge", "edge")], "radius": 1}, _filleted, None),
        ("find_geometry", {"target": "FbDet", "kind": "line_edge", "max_results": 1}, "ok", _fg("fd_edge2")),
        ("model_chamfer", lambda c: {"edges": [_ctx_get(c, "fd_edge2", "edge")], "distance": 0.5}, _chamfered, None),
        ("find_geometry", {"target": "FbDet", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("fd_top")),
        ("model_shell", lambda c: {"body_name": "FbDet", "remove_faces": [_ctx_get(c, "fd_top", "top")], "thickness": 2}, _shelled, None),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "FbWart"},
         _datum_plane("xy"), None),
        ("design_delete_feature", {"feature": "FbWart"},
         lambda p: (p["deleted"] is True and p["feature"] == "FbWart"
                    and "timeline_warning" not in p), None),
        ("model_create_component", {"name": "FbJunk", "activate": False}, _made_component_inactive, None),
        ("design_delete_occurrence", {"occurrence": "FbJunk:1"},
         lambda p: (p["deleted"] is True and p["occurrence"] == "FbJunk:1"
                    and "timeline_warning" not in p), None),
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("view_section", {"action": "cut", "plane": "xy", "offset": 5,
                          "auto_view": False},
         _section_created(),
         ("fb_section_one", _recall("fb_section_one", lambda p: p["section"]))),
        ("view_section", {"action": "cut", "plane": "yz", "offset": 5,
                          "auto_view": False},
         _section_created("fb_section_one"),
         ("fb_section_two", _recall("fb_section_two", lambda p: p["section"]))),
        ("view_section", {"action": "list"},
         _section_census(("fb_section_one", "fb_section_two"), (False, True)), None),
        ("view_section", lambda c: {"action": "clear",
                                    "section": _ctx_get(c, "fb_section_one",
                                                        "the first fallback section")},
         _section_named_clear("fb_section_one"), None),
        ("view_section", {"action": "list"},
         _section_census(("fb_section_two",), (True,)), None),
        ("view_screenshot", {"width": 400, "height": 300}, "ok", None),
        ("view_section", {"action": "clear"},
         lambda p: (p["removed"] == [_RECALL.get("fb_section_two")]
                    and p["removed_count"] == p["sections_before"] == 1
                    and p["sections_after"] == 0), None),
        ("view_screenshot_multi", {"views": ["front", "top"], "width": 300, "height": 250}, "ok", None),
    ]
)

# ACT 6 fallback: a scratch parameter set/delete.
_RESIZE_FB = [
    ("param_add", {"name": "FbParam", "expression": "12 mm"}, _param_added("FbParam", 12), None),
    ("param_set", {"name": "FbParam", "expression": "14 mm"}, _param_set_to("FbParam", 14), None),
    ("design_recompute", {},
     lambda p: (p["recomputed"] is True and isinstance(p["error_count"], int)
                and "new_errors" not in p), None),
    ("param_delete", {"name": "FbParam"}, _param_deleted("FbParam"), None),
]
