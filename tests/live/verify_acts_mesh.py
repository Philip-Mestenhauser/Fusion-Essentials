# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the surface-prep, mesh and arrange cameos.

Families with no home on the mechanism ride scratch fixtures in the SAME document - surface bodies
built, trimmed and stitched, a mesh round-tripped through export and insert, and the arrange solver
nesting last, because it restructures what it nests.
"""

from verify_core import (
    EXPORT_DIR, _RECALL, _activated, _arranged, _base_feature_open, _box, _captured, _ctx_get,
    _datum_plane, _document_closed, _document_read, _drilled, _dwell, _extent_measured, _extruded,
    _fg, _fgn, _holder_computed, _made_component, _matched, _measured, _mesh_round_trip,
    _needs, _new_document, _num, _packed, _prof, _rebuilt, _recall, _refused, _repair_no_op,
    _same_face_area, _shelled, _split_bodies, _stitched, _trim_scoped_to_target, _unless,
    _unstitched, _watch)
from verify_acts_cam import MACHINING_EXTENSION
from verify_layout import _px, _py


def _base_feature_state(p):
    """The complete mode and timeline values used around a base-feature scope."""
    mode = p.get("mode_detail") or {}
    timeline = p.get("timeline") or {}
    rows = timeline.get("timeline") or []
    return {
        "design_type": mode.get("design_type"),
        "has_timeline": mode.get("has_timeline"),
        "timeline_feature_count": mode.get("timeline_feature_count"),
        "base_feature_count": mode.get("base_feature_count"),
        "timeline_count": timeline.get("count"),
        "returned": timeline.get("returned"),
        "truncated": timeline.get("truncated") is True,
        "rows": [[row.get("index"), row.get("name"), row.get("type")] for row in rows],
    }


def _base_feature_parametric(p):
    """A complete parametric mode/timeline baseline."""
    state = _base_feature_state(p)
    count = state["timeline_count"]
    complete = (isinstance(count, int) and state["returned"] == count
                and len(state["rows"]) == count and not state["truncated"])
    return _measured(
        "complete parametric base-feature baseline",
        state,
        state["design_type"] == "parametric" and state["has_timeline"] is True
        and state["timeline_feature_count"] == count
        and isinstance(state["base_feature_count"], int) and complete)


def _base_feature_direct(p):
    """An independent mode read while a base-feature scope is open."""
    mode = p.get("mode_detail") or {}
    facts = {key: mode.get(key) for key in
             ("design_type", "has_timeline", "timeline_feature_count")}
    return _measured(
        "open base-feature mode",
        facts,
        facts["design_type"] == "direct" and facts["has_timeline"] is False
        and facts["timeline_feature_count"] is None)


def _base_feature_same_name(name_key):
    """An opened scope whose adopted name equals the first document's adopted name."""
    def check(p):
        _base_feature_open(p)
        expected = _RECALL[name_key]
        actual = p.get("base_feature")
        return _measured(
            "same adopted base-feature name before cross-document finish",
            {"expected": expected, "actual": actual}, actual == expected)
    return check


def _base_feature_added(before_key, name_key):
    """The complete parametric timeline after one named base feature was added."""
    def check(p):
        before = _RECALL[before_key]
        after = _base_feature_state(p)
        remaining = list(after["rows"])
        preserved = True
        for row in before["rows"]:
            if row in remaining:
                remaining.remove(row)
            else:
                preserved = False
        expected_name = _RECALL[name_key]
        added = (len(remaining) == 1 and remaining[0][1] == expected_name
                 and remaining[0][2] == "BaseFeature")
        complete = (after["returned"] == after["timeline_count"] == len(after["rows"])
                    and not after["truncated"])
        valid = (
            after["design_type"] == "parametric" and after["has_timeline"] is True
            and after["timeline_feature_count"] == after["timeline_count"]
            and after["timeline_count"] == before["timeline_count"] + 1
            and after["base_feature_count"] == before["base_feature_count"] + 1
            and preserved and added and complete)
        return _measured(
            f"one BaseFeature named {expected_name} added to the complete timeline",
            {"before_count": before["timeline_count"], "after": after,
             "added_rows": remaining, "expected_name": expected_name},
            valid)
    return check


def _opposite_rim_first(p):
    """The rim's four handles with the two edges running along x first - two edges sharing no
    vertex, so the list as handed over does not chain end to end."""
    ms = p["matches"]
    along_x = [m["handle"] for m in ms if abs((m.get("direction") or [0.0])[0]) > 0.5]
    if len(along_x) != 2:
        raise ValueError(f"expected 2 of the {len(ms)} rim edges along x, read {len(along_x)}")
    return along_x + [m["handle"] for m in ms if m["handle"] not in along_x]


def _domed_patch(flat_mm2):
    """find_geometry on the tangent patch body: its faces read, none is planar, and their area
    sum is over the flat patch the opening would take by 1 % - the patch that ignores its
    continuity is one flat face, the group-tangent one a dome."""
    def check(p):
        faces = [m for m in p.get("matches") or [] if str(m.get("kind")).endswith("face")]
        areas = [m.get("area") for m in faces]
        good = (bool(faces) and all(m.get("kind") != "planar_face" for m in faces)
                and all(_num(a) for a in areas) and sum(areas) > 1.01 * flat_mm2)
        return _measured(f"a domed patch over the flat {flat_mm2} mm2",
                         {"kinds": [m.get("kind") for m in faces], "areas": areas}, good)
    return check


def _seams_read(label, test, jumps=lambda js: True):
    """model_measure_continuity: every edge's normal angle passes `test`, the jump list `jumps`."""
    def check(p):
        rows = p.get("edges") or []
        got = {"angles": [r.get("max_normal_angle_deg") for r in rows],
               "modes": [r.get("mode") for r in rows],
               "jumps": [r.get("max_curvature_jump") for r in rows]}
        return _measured(label, got, bool(rows) and all(_num(a) and test(a)
                                                        for a in got["angles"])
                         and jumps(got["jumps"]))
    return check


def _g2_start_g1_end(js):
    """[smooth rim, tangent rim] jumps: the tangent seam's is non-zero and 10x the smooth one's."""
    return len(js) == 2 and all(_num(j) for j in js) and js[1] > 0 and js[0] * 10 < js[1]


def _edges_of(p):
    """find_geometry: the handles of every edge match - the faces the same query lists left out."""
    return [m["handle"] for m in p.get("matches") or [] if "edge" in str(m.get("kind"))]


def _blend_lofted(start, end):
    """model_loft between two rims: ends read back as asked, edge sections, a weight per end."""
    def check(p):
        params = p.get("model_parameters") or {}
        weighted = [side for side, cond in (("start", start), ("end", end)) if cond != "free"]
        got = {"ends": p.get("ends"), "kinds": p.get("section_kinds"), "weights": params,
               "is_solid": p.get("is_solid")}
        return _measured(f"a {start}-to-{end} edge loft", got,
                         p.get("ends") == {"start": start, "end": end}
                         and p.get("section_kinds") == ["edge", "edge"]
                         and set(params) == {f"{s}_weight" for s in weighted}
                         and p.get("is_solid") is False)
    return check


def _blend_lofts_healthy(count):
    """design_get(include=['timeline']): the Blend component's `count` loft rows all read healthy."""
    def check(p):
        rows = [r for r in (p.get("timeline") or {}).get("timeline") or []
                if r.get("component") == "Blend" and r.get("type") == "LoftFeature"]
        got = {r.get("name"): r.get("health", "healthy") for r in rows}
        return _measured(f"{count} healthy Blend loft(s)", got,
                         len(rows) == count and all(h == "healthy" for h in got.values()))
    return check


def _lofted_as(ends, kinds, params, **more):
    """model_loft: the ends, section kinds and parameter names read off the feature, and `more`."""
    def check(p):
        got = {"ends": p.get("ends"), "kinds": p.get("section_kinds"),
               "params": sorted(p.get("model_parameters") or {}),
               **{k: p.get(k) for k in more}}
        return _measured(f"a {kinds} loft ending {ends}", got,
                         got["ends"] == ends and got["kinds"] == kinds
                         and got["params"] == sorted(params) and "unverified" not in p
                         and all(got[k] == v for k, v in more.items()))
    return check


def _seam_jump_past(key, factor):
    """model_measure_continuity: a seam under 0.01 deg, its curvature jump past factor x the twin's."""
    def check(p):
        row = (p.get("edges") or [{}])[0]
        got = {"angle": row.get("max_normal_angle_deg"), "jump": row.get("max_curvature_jump"),
               "twin_jump": _RECALL.get(key)}
        return _measured(f"a seam bending over {factor}x its twin's", got,
                         _num(got["angle"]) and got["angle"] < 0.01 and _num(got["jump"])
                         and _num(got["twin_jump"]) and got["jump"] > factor * got["twin_jump"])
    return check


def _box_x(lo, hi):
    """model_inspect: the body's x extent lies in [lo, hi] mm."""
    return lambda p: _measured(f"an x extent in [{lo}, {hi}] mm", p.get("x"),
                               _num(p.get("x")) and lo <= p["x"] <= hi)


def _timeline_rows_suppressed(*keys):
    """design_get(include=['timeline']): the rows the recalled Cascade features name all read
    is_suppressed true - read off the timeline, not off the suppress reply."""
    def check(p):
        rows = (p.get("timeline") or {}).get("timeline") or []
        want = [_RECALL[k] for k in keys]
        got = {r.get("name"): r.get("is_suppressed") for r in rows
               if r.get("component") == "Cascade" and r.get("name") in want}
        return _measured("cascade rows suppressed on the timeline", got,
                         all(got.get(n) is True for n in want))
    return check


def _also_named(key, *keys):
    """The suppress/unsuppress reply names every recalled Cascade dependent under `key`."""
    def check(p):
        also = p.get(key)
        want = [_RECALL[k] for k in keys]
        return _measured(f"{key} names the dependents", {key: also, "want": want},
                         isinstance(also, list) and all(n in also for n in want))
    return check


def _same_volume(key):
    """model_inspect: the volume reads what it read before the suppress/unsuppress pair."""
    def check(p):
        got = (p.get("mass") or {}).get("volume")
        return _measured("volume restored", {"volume": got, "before": _RECALL.get(key)},
                         _num(got) and abs(got - _RECALL[key]) < 0.01)
    return check


# Pinned at the cameo's own sizes, not scaled from the 200 mm probes; measure_api's
# form-crease-fillet-two-sizes re-measures the fillet figure by raw-API script at both sizes.
_FORM_BOX_CM3 = 7.225184674         # the 3x3x3 box cage, 20 mm
_FORM_CREASE_CM3 = 7.465881190      # the same cage, its top rim creased
_FORM_FILLET_CM3 = -0.018444933     # the four creased edges filleted at 1 mm


def _near_rel(got, want, rel=1e-4):
    return _num(got) and abs(got - want) <= rel * abs(want)


def _inspected_mm3(label, want_cm3):
    """model_inspect in mm: the body's own volume within 1e-4 of the measured one."""
    def check(p):
        got = (p.get("mass") or {}).get("volume")
        return _measured(label, {"volume_mm3": got, "want_mm3": round(want_cm3 * 1000, 6)},
                         _near_rel(got, want_cm3 * 1000))
    return check


def _form_made(volume_cm3, sharp):
    """form_create: a solid six-face Form whose own read-back verified, at the measured volume, with
    `sharp` sharp edges read off its seams."""
    def check(p):
        facts = {k: p.get(k) for k in ("created", "is_solid", "brep_faces", "readback",
                                       "volume_cm3")}
        facts["sharp_edges"] = len(p.get("sharp_edges") or [])
        return _measured(f"a verified Form at {volume_cm3} cm3", facts,
                         p.get("created") is True and p.get("is_solid") is True
                         and p.get("brep_faces") == 6 and p.get("readback") == "exact"
                         and facts["sharp_edges"] == sharp
                         and _near_rel(p.get("volume_cm3"), volume_cm3))
    return check


def _form_row_added(p):
    """design_get after the create: the timeline is one row longer and that row is a FormFeature
    in FormDemo carrying the Form's name - read off the timeline, not off the create's reply."""
    tl = p.get("timeline") or {}
    name = _RECALL["form_box"].split("/", 1)[1]
    hits = [r for r in tl.get("timeline") or [] if r.get("name") == name
            and r.get("type") == "FormFeature" and r.get("component") == "FormDemo"]
    return _measured("one FormFeature row added", {"count": tl.get("count"),
                                                   "before": _RECALL["form_tl0"], "rows": hits},
                     tl.get("count") == _RECALL["form_tl0"] + 1 and len(hits) == 1)


def _form_cage_read(p):
    """form_get(include=['cage']): the census Fusion's own read-back gives - 54 quads, 56 points,
    closed, the eight valence-3 corners - and the stored record still matching it."""
    census = p.get("census") or {}
    got = {k: census.get(k) for k in ("faces", "vertices", "closed", "stars")}
    got["record_matches_cage"] = p.get("record_matches_cage")
    return _measured("the box Form's cage read back", got,
                     got["faces"] == 54 and got["vertices"] == 56 and got["closed"] is True
                     and got["stars"] == {"3": 8} and got["record_matches_cage"] is True
                     and isinstance(p.get("cage"), dict))


def _creased_rim_cage(ctx):
    """form_create args: the box's own cage moved 30 mm along x, its 12 top-rim edges creased -
    every cage edge whose two ends sit at the top and on one outer side."""
    cage = _ctx_get(ctx, "form_box_cage", "the box Form's cage")
    verts = cage["vertices"]
    top = max(v[2] for v in verts)
    sides = [(axis, pick(v[axis] for v in verts)) for axis in (0, 1) for pick in (min, max)]

    def at(v, axis, value):
        return abs(verts[v][axis] - value) < 1e-6
    rim = set()
    for f in cage["faces"]:
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            if at(a, 2, top) and at(b, 2, top) and any(at(a, ax, val) and at(b, ax, val)
                                                       for ax, val in sides):
                rim.add(tuple(sorted((a, b))))
    return {"cage": {"vertices": [[v[0] + 30.0, v[1], v[2]] for v in verts],
                     "faces": cage["faces"], "creases": [list(e) for e in sorted(rim)]},
            "name": "FormCrease", "component": "FormDemo:1"}


def _crease_made(p):
    """The creased Form's address, saved and recalled; its sharp-edge handles parked for the
    fillet that takes them."""
    _RECALL["form_crease_edges"] = [row["edge"] for row in p["sharp_edges"]]
    _RECALL["form_crease"] = p["form"]
    return p["form"]


def _forms_modified(p):
    """form_get: the filleted Form reads modified downstream and the untouched box does not."""
    rows = {r.get("form"): r.get("modified_downstream") for r in p.get("forms") or []}
    got = {"box": rows.get(_RECALL["form_box"]), "crease": rows.get(_RECALL["form_crease"])}
    return _measured("modified_downstream off the creation record", got,
                     got["crease"] is True and got["box"] is False)


def _named_exactly(key, recall_key):
    """The suppress/delete reply names exactly the recalled fillet under `key`."""
    def check(p):
        want = [_RECALL[recall_key]]
        return _measured(f"{key} names the fillet alone", {key: p.get(key), "want": want},
                         p.get(key) == want)
    return check


def _form_pair_suppressed(p):
    """design_get: the Form and its fillet both read suppressed on the timeline itself."""
    names = [_RECALL["form_crease"].split("/", 1)[1], _RECALL["form_fillet"]]
    got = {r.get("name"): r.get("is_suppressed")
           for r in (p.get("timeline") or {}).get("timeline") or []
           if r.get("component") == "FormDemo" and r.get("name") in names}
    return _measured("the Form and its fillet suppressed", got,
                     len(got) == 2 and all(v is True for v in got.values()))


def _form_count_is(key, delta):
    """design_get: the timeline count reads the recalled count plus `delta`."""
    def check(p):
        count = (p.get("timeline") or {}).get("count")
        return _measured(f"timeline count {delta:+d}", {"count": count, "before": _RECALL[key]},
                         _num(count) and count == _RECALL[key] + delta)
    return check


def _crease_handles_found(p):
    """find_geometry on the creased Form: every sharp-edge handle form_create returned is one of its
    own edge handles - the same occurrence proxy at the same world point."""
    def key(h):
        return str(h).split(";rv=")[0]
    got = {key(h) for h in _edges_of(p)}
    want = [key(h) for h in _RECALL["form_crease_edges"]]
    return _measured("the crease handles are find_geometry's", {"want": want, "found": sorted(got)},
                     len(want) == 4 and all(h in got for h in want))


def _form_name(key):
    """The Form name of a recalled '<component>/<name>' label."""
    label = _RECALL[key] if isinstance(_RECALL[key], str) else _RECALL[key]["form"]
    return label.split("/", 1)[1]


def _cyl_made(p):
    """form_create behind the marker: a solid six-face capped cylinder, read back exactly, with no
    sharp edge, naming the rolled-back box Form as the item after it."""
    facts = {k: p.get(k) for k in ("created", "is_solid", "brep_faces", "readback", "volume_cm3",
                                   "inserted_before", "timeline_index")}
    facts["sharp_edges"] = p.get("sharp_edges")
    return _measured("a capped cylinder Form before the box", facts,
                     p.get("created") is True and p.get("is_solid") is True
                     and p.get("brep_faces") == 6 and p.get("readback") == "exact"
                     and p.get("sharp_edges") == [] and _num(p.get("volume_cm3"))
                     and p.get("inserted_before") == _form_name("form_box"))


def _cyl_before_box(p):
    """design_get: the cylinder Form's row is followed by the box Form's - the order form_create's
    inserted_before named, read off the timeline."""
    rows = {r.get("index"): r for r in (p.get("timeline") or {}).get("timeline") or []}
    at = [i for i, r in rows.items() if r.get("name") == _form_name("form_cyl")
          and r.get("component") == "FormDemo" and r.get("type") == "FormFeature"]
    after = rows.get(at[0] + 1, {}).get("name") if len(at) == 1 else None
    return _measured("the box Form follows the cylinder", {"cylinder_at": at, "next": after},
                     after == _RECALL["form_cyl"]["inserted_before"])


def _cyl_inspected(p):
    """model_inspect: the cylinder's own volume is form_create's, inside its 20 x 20 x 40 mm cage."""
    got = {"volume_mm3": (p.get("mass") or {}).get("volume"), "extent": [p.get(a) for a in "xyz"]}
    want = _RECALL["form_cyl"]["volume_cm3"] * 1000
    return _measured(f"the cylinder at {want} mm3", got,
                     _near_rel(got["volume_mm3"], want)
                     and all(_num(e) and 0 < e <= c + 1e-3
                             for e, c in zip(got["extent"], (20, 20, 40))))


def _group_collapsed(name):
    """design_get: the named timeline group reads collapsed with its two Forms in it."""
    def check(p):
        rows = [r for r in (p.get("timeline") or {}).get("timeline") or []
                if r.get("name") == name and r.get("is_group") is True]
        got = [(r.get("is_collapsed"), r.get("member_count")) for r in rows]
        return _measured(f"group {name} collapsed", got, got == [(True, 2)])
    return check


def _cyl_cage_read(p):
    """form_get(include=['cage']) on the grouped cylinder: its own 40-quad, 42-point cage."""
    census = p.get("census") or {}
    got = {k: census.get(k) for k in ("faces", "vertices", "closed", "stars")}
    got["record_matches_cage"] = p.get("record_matches_cage")
    return _measured("the grouped cylinder's cage read back", got,
                     got["faces"] == 40 and got["vertices"] == 42 and got["closed"] is True
                     and got["stars"] == {"3": 8} and got["record_matches_cage"] is True)


def _tube_made(p):
    """form_create on an uncapped cylinder: an open surface Form read back exactly, its area
    published in place of a volume, no seam read sharp or unread."""
    facts = {k: p.get(k) for k in ("created", "is_solid", "readback", "area_cm2", "volume_cm3",
                                   "seams_unread")}
    facts["sharp_edges"] = p.get("sharp_edges")
    return _measured("an open tube Form", facts,
                     p.get("created") is True and p.get("is_solid") is False
                     and p.get("readback") == "exact" and _num(p.get("area_cm2"))
                     and "volume_cm3" not in p and p.get("sharp_edges") == [])


def _tube_area(p):
    """find_geometry on the tube: its faces' areas sum to the area form_create published."""
    areas = [m.get("area") for m in p.get("matches") or [] if str(m.get("kind")).endswith("face")]
    want = _RECALL["form_tube"]["area_cm2"] * 100
    return _measured(f"the tube's faces at {want} mm2", {"areas": areas},
                     bool(areas) and all(_num(a) for a in areas) and _near_rel(sum(areas), want))


def _form_bodies_solid(p):
    """model_inspect per_body on FormDemo: the cylinder reads solid and the tube open."""
    rows = {r.get("body"): r.get("is_solid") for r in (p.get("mass") or {}).get("per_body") or []}
    got = {k: rows.get(k) for k in ("FormCyl", "FormTube")}
    return _measured("the cylinder solid and the tube open", got,
                     got == {"FormCyl": True, "FormTube": False})


def _tube_raised(p):
    """model_inspect: the tube's lowest point sits 10 mm above where it was before the move."""
    z = (p.get("min_point") or {}).get("z")
    return _measured("the tube raised 10 mm", {"z": z, "before": _RECALL["form_tube_z"]},
                     _num(z) and abs(z - (_RECALL["form_tube_z"] + 10.0)) < 1e-3)


def _moved_form_modified(p):
    """form_get: the moved tube reads modified downstream; the cylinder and the box do not."""
    rows = {r.get("form"): r.get("modified_downstream") for r in p.get("forms") or []}
    got = {k: rows.get(_RECALL[k] if isinstance(_RECALL[k], str) else _RECALL[k]["form"])
           for k in ("form_tube", "form_cyl", "form_box")}
    return _measured("modified_downstream after a move", got,
                     got == {"form_tube": True, "form_cyl": False, "form_box": False})


def _cascade_counted(p):
    """design_get after the delete: what the reply named, plus its unnamed count, is exactly what
    left the timeline besides the target - both counts read off the timeline itself."""
    reply = _RECALL["cascade_delete"]
    also = reply.get("also_deleted")
    before, after = _RECALL["cascade_count"], (p.get("timeline") or {}).get("count")
    named = (reply.get("also_deleted_count", len(also)) + reply.get("also_deleted_unnamed", 0)
             if isinstance(also, list) else None)
    return _measured("the delete names every item that left with it",
                     {"also_deleted": also, "unnamed": reply.get("also_deleted_unnamed"),
                      "count_before": before, "count_after": after},
                     named is not None and _num(after) and named == before - after - 1)


def _cascade_group_collapsed(p):
    """design_get(include=['timeline']): one CascadeG row, collapsed, standing for 2 members that
    are not listed beside it - the state the census caveat reports on."""
    rows = (p.get("timeline") or {}).get("timeline") or []
    group = [r for r in rows if r.get("name") == "CascadeG"]
    members = [r.get("name") for r in rows if r.get("component") == "Cascade"
               and r.get("name") in (_RECALL["cascade_extrude"], _RECALL["cascade_shell"])]
    return _measured("CascadeG reads collapsed over 2 unlisted members",
                     {"group_rows": group, "members_listed": members},
                     len(group) == 1 and group[0].get("is_collapsed") is True
                     and group[0].get("member_count") == 2 and not members)


def _caveat_names_groups(n):
    """The suppress reply's census_caveat counts `n` collapsed group(s); also_suppressed rides along."""
    def check(p):
        caveat = p.get("census_caveat")
        return _measured(f"census_caveat names {n} group(s)",
                         {"census_caveat": caveat, "also_suppressed": p.get("also_suppressed")},
                         isinstance(caveat, str)
                         and caveat.startswith(f"{n} collapsed timeline group(s)"))
    return check


# --- the CAMEO acts: surface-prep, mesh, and CAM families ride scratch fixtures in the SAME doc -
# These families have no natural home on the mechanism itself, so the spec places them as cameos.

# ACT 5: MACHINING PREP - surfaces, sheet ops, split/stitch/arrange/base-feature, holder read.
_MACHINING = [
    # the surface/split/stitch cameos live on a GRID (y=200 row, plus a z-lifted revolve) so each
    # builds in clear space a viewer can see, never on top of the part or another cameo.
    ("model_create_component", {"name": "SRev", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "SRevS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 10, "y1": 60,
                                           "x2": 10, "y2": 90}],
                             "sketch_name": "SRevS"}, "ok", None),
    # 'is_solid' is isSolid read off the created body: a profile that closed into a SOLID, and a
    # body whose flag would not read at all (named in 'unverified'), both return ok.
    ("surface_revolve", {"sketch_name": "SRevS", "axis": "z", "angle_deg": 360},
     lambda p: p["is_solid"] is False and bool(p["result_bodies"]) and "unverified" not in p, None),
    # ONE open fitted spline beside a construction line on the axis: the construction line stays
    # out of the profile, so the spline alone revolves into a sheet whose area is measured. The
    # bench sits out at x=1360 through its component placement.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "SRevSpline", "activate": True, "x": 1360},
     _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SRevSplineS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 0, "y1": 0, "x2": 0, "y2": 30,
                                           "is_construction": True},
                                          {"kind": "spline", "points": [[6, 0], [12, 15], [6, 30]]}],
                             "sketch_name": "SRevSplineS"}, "ok", None),
    ("surface_revolve", {"sketch_name": "SRevSplineS", "axis": "y", "angle_deg": 360},
     lambda p: _measured("an open spline revolves alone into a measured sheet",
                         {"is_solid": p.get("is_solid"), "area_added_cm2": p.get("area_added_cm2")},
                         p.get("is_solid") is False and _num(p.get("area_added_cm2"))
                         and p["area_added_cm2"] > 0), None),
    ("design_activate_component", {"occurrence": "SRev:1"}, "ok", None),
    # a CLOSED revolved sphere surface encloses one cell; surface_fill must seal it to a SOLID at
    # the enclosed volume (r=6mm -> 904.78 mm3) MEASURED off the result, never predicted.
    ("model_create_component", {"name": "FillDemo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "FillProf"}, "ok", None),
    # the arc's endpoints must sit ON the revolve axis (x=0) or the revolved surface is an open
    # tube enclosing nothing, and surface_fill refuses it (live-measured).
    ("sketch_add_geometry", {"geometry": [{"kind": "arc", "cx": 0, "cy": 150, "x1": 0, "y1": 156,
                                           "sweep_deg": 180}],
                             "sketch_name": "FillProf"}, "ok", None),
    ("surface_revolve", {"sketch_name": "FillProf", "axis": "z", "angle_deg": 360}, "ok", None),
    # the closed sphere sheet encloses exactly ONE cell, so index 1 is one past the end: refused
    # NAMING the index and the range that exists, never clamped onto a neighbouring cell. The parse
    # happens before any cell is kept, so the sheet is untouched and the fill below is still its
    # first feature.
    ("surface_fill", {"tools": ["FillDemo"], "operation": "new", "cells": [1]},
     _refused("does not exist", "0..0"), None),
    ("surface_fill", {"tools": ["FillDemo"], "operation": "new"},
     lambda p: p.get("filled") is True and p.get("all_solid") is True
     and abs(p.get("result_volume", 0) - 904.78) < 10
     and p.get("tools_unclassified", 0) == 0, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", {"target": "SRev", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("srev_face")),
    ("surface_thicken", lambda c: {"faces": [_ctx_get(c, "srev_face", "surface face")], "thickness": 2}, "ok", None),
    ("model_create_component", {"name": "Surf", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SurfS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 200, "y1": 200,
                                           "x2": 240, "y2": 230}],
                             "sketch_name": "SurfS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SurfS", "distance": 15}, "ok", None),
    ("find_geometry", {"target": "Surf", "kind": "planar_face", "max_results": 1}, "ok", _fg("surf_face")),
    # 'faces_offset' is counted off the CREATED surface, so it disagrees with the one face requested
    # when chaining widened the selection - and reads null (in 'unverified') when nothing was read.
    ("surface_offset", lambda c: {"faces": [_ctx_get(c, "surf_face", "surface face")], "distance": 3},
     lambda p: (p["faces_offset"] == p["faces_requested"] == 1 and bool(p["result_bodies"])
                and "unverified" not in p), None),
    ("surface_offset", lambda c: {"faces": [_ctx_get(c, "surf_face", "surface face")], "distance": 0}, "ok", None),
    # THREE edges of ONE body, not one: edge.body hands back a fresh proxy per read, so a
    # body-set walk keyed on object identity counts this single body three times and refuses a
    # legal call as multi-body. A single-edge beat cannot catch that - one edge never disagrees
    # with itself. nearest_to pins the pick to the TOP rim (z=15): three of that rim's four edges
    # connect at endpoints into one open chain, while an arbitrary pick mixes top and bottom rims
    # into a disconnected set the platform rejects as an invalid extend input.
    ("find_geometry", {"target": "Surf", "kind": "line_edge", "nearest_to": [220, 215, 15], "max_results": 3}, "ok", _fgn("surf_edges")),
    ("surface_extend", lambda c: {"edges": _ctx_get(c, "surf_edges", "surface edges"), "distance": 2},
     # no extend_alignment given: the key is ABSENT from the payload, so nothing was written and
     # the API's own default stands - an echoed key here would be a claim about an unwritten value.
     lambda p: p.get("body_count", 1) == 1 and "extend_alignment" not in p, None),
    ("find_geometry", {"target": "Surf", "kind": "planar_face", "max_results": 1}, "ok", _fg("surf_body")),
    # the flip is invisible in every other read, so the row asserts the tool's own isParamReversed
    # read-back: 'reversed_confirmed' is false whenever the after-count does not match the flip.
    ("surface_reverse_normal", lambda c: {"bodies": [_ctx_get(c, "surf_body", "surface body")]},
     lambda p: p.get("reversed_confirmed") is True and p.get("faces_total", 0) > 0, None),
    # extend_alignment + thicken_type, on their own sheet so the read-backs never ride on edges an
    # earlier extend already moved. Both properties are written through set_verified, so a value the
    # platform drops comes back as an error rather than an echoed success. A rectangle extruded as a
    # surface gives a four-walled sheet, which is the convex corner set 'rounded' needs to mean
    # anything - a single flat face has no corner to round.
    ("model_create_component", {"name": "SAlign", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SAlignS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 700, "y1": 200,
                                           "x2": 740, "y2": 230}],
                             "sketch_name": "SAlignS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SAlignS", "distance": 15}, "ok", None),
    ("find_geometry", {"target": "SAlign", "kind": "line_edge", "nearest_to": [720, 215, 15],
                       "max_results": 3}, "ok", _fgn("salign_edges")),
    ("surface_extend", lambda c: {"edges": _ctx_get(c, "salign_edges", "aligned sheet edges"),
                                  "distance": 2, "extend_alignment": "align_edges"},
     lambda p: p.get("extend_alignment") == "align_edges", None),
    ("find_geometry", {"target": "SAlign", "kind": "planar_face", "max_results": 1}, "ok",
     _fg("salign_face")),
    ("surface_thicken", lambda c: {"faces": [_ctx_get(c, "salign_face", "aligned sheet face")],
                                   "thickness": 1, "thicken_type": "rounded"},
     lambda p: p.get("thicken_type") == "rounded", None),
    # symmetric thicken: 'thickness' stays the PER-SIDE number asked for, and 'wall_total' - the
    # raw feature readback, published only when symmetric - is the wall that actually landed (2x).
    ("model_create_component", {"name": "ThkSym", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ThkSymS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 760, "y1": 200,
                                           "x2": 790, "y2": 220}],
                             "sketch_name": "ThkSymS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "ThkSymS", "distance": 15}, "ok", None),
    ("find_geometry", {"target": "ThkSym", "kind": "planar_face", "max_results": 1}, "ok",
     _fg("thksym_face")),
    ("surface_thicken", lambda c: {"faces": [_ctx_get(c, "thksym_face", "symmetric sheet face")],
                                   "thickness": 4, "symmetric": True},
     lambda p: _measured("symmetric thicken: wall_total is 2x the per-side thickness",
                         {"thickness": p.get("thickness"), "wall_total": p.get("wall_total")},
                         p.get("thickness") == 4.0 and p.get("wall_total") == 8.0), None),
    # back to the component that was active before this cameo, so the ones after it nest as before.
    ("design_activate_component", {"occurrence": "Surf:1"}, "ok", None),
    ("model_create_component", {"name": "SDel", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SD1"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 300, "y1": 200,
                                           "x2": 320, "y2": 220}],
                             "sketch_name": "SD1"}, "ok", None),
    ("model_extrude", {"sketch_name": "SD1", "profile_index": 0, "distance": 10}, _extruded, None),
    ("find_geometry", {"target": "SDel", "kind": "planar_face", "nearest_to": [310, 210, 10], "max_results": 1}, "ok", _fg("sdel_top")),
    # one face off a six-faced box: 'faces_delta' is measured across the result bodies' own counts,
    # and a delete that RAISED the count or ate a whole body is published with a warning, not an error.
    ("surface_delete_face", lambda c: {"faces": [_ctx_get(c, "sdel_top", "top face")], "heal": False},
     lambda p: p["faces_delta"] == -1 and p["bodies_consumed"] == 0, None),
    ("find_geometry", {"target": "SDel", "kind": "line_edge", "nearest_to": [310, 210, 10], "max_results": 4}, "ok", _fgn("sdel_rim")),
    # read while the open box's rim is the only line edges near that point: a patch body's own
    # boundary edges sit on the rim and would tie them in the nearest_to sort.
    ("find_geometry", {"target": "SDel", "kind": "line_edge", "nearest_to": [310, 210, 10],
                       "max_results": 4}, _matched(4, "line_edge"),
     ("sdel_rim_scrambled", _opposite_rim_first)),
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim", "rim edges")}, "ok",
     ("sdel_flat_body", lambda p: "SDel:1:" + p["result_body"])),
    # a patch with operation 'new' leaves the opened body untouched, so the SAME rim carries the
    # option beats below. The tangent beat hands the rim over with its two opposite edges first, an
    # order that does not chain, and 'continuity' is the FEATURE's groupContinuity read back.
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim_scrambled",
                                                      "the rim, opposite edges first"),
                                 "continuity": "tangent"},
     lambda p: p.get("continuity") == "tangent" and "unverified" not in p,
     ("sdel_tangent_body", lambda p: "SDel:1:" + p["result_body"])),
    # the body itself, apart from the reply: a patch that ignores its continuity is ONE flat face
    # over the 20 x 20 mm opening, and a tangent one bulges into a dome with more area than that.
    ("find_geometry", lambda c: {"target": _ctx_get(c, "sdel_tangent_body", "the tangent patch")},
     _domed_patch(400.0), None),
    # the seam itself, read edge by edge between the walls and each patch: the tangent patch meets
    # them within half a degree, the connected one (a flat lid on vertical walls) far from it - so a
    # read that always answers 0 fails the second row.
    ("model_measure_continuity",
     lambda c: {"edges": _ctx_get(c, "sdel_rim", "rim edges"),
                "against": _ctx_get(c, "sdel_tangent_body", "the tangent patch")},
     _seams_read("the tangent patch meets the walls within 0.5 deg", lambda a: a < 0.5), None),
    ("model_measure_continuity",
     lambda c: {"edges": _ctx_get(c, "sdel_rim", "rim edges"),
                "against": _ctx_get(c, "sdel_flat_body", "the connected patch")},
     _seams_read("the connected patch creases the walls past 45 deg", lambda a: a > 45.0), None),
    # an interior RAIL the patch surface must pass through: a sheet standing in the opening, whose
    # top edge crosses it end to end with both ends landing on the rim. 'interior_rail_count' is the
    # count PatchFeatureInput.interiorRailsAndPoints reads back, not the number of handles passed.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "PatchRail", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PatchRailS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 300, "y1": 210,
                                           "x2": 320, "y2": 210}],
                             "sketch_name": "PatchRailS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "PatchRailS", "distance": 10}, "ok", None),
    ("find_geometry", {"target": "PatchRail", "kind": "line_edge", "nearest_to": [310, 210, 10],
                       "max_results": 1}, "ok", _fg("patch_rail")),
    ("design_activate_component", {"occurrence": "SDel:1"}, "ok", None),
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim", "rim edges"),
                                 "interior_rails": [_ctx_get(c, "patch_rail", "the interior rail")]},
     lambda p: p.get("interior_rail_count") == 1, None),
    # three of the four rim edges are no closed loop: refused by the vertex walk before Fusion
    # sees them, the timeline unchanged.
    ("design_get", {"include": ["timeline"], "max_results": 2000}, "ok",
     ("sdel_tl", _recall("sdel_tl", lambda p: p["timeline"]["count"]))),
    ("surface_patch", lambda c: {"boundary": _ctx_get(c, "sdel_rim", "rim edges")[:3]},
     _refused("'boundary'", "not one closed loop"), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _form_count_is("sdel_tl", 0),
     None),
    # rails fit ONE patch surface, so pairing them with the multi-loop 'boundaries' is refused
    # BEFORE any patch runs - every loop would otherwise be handed the same rails.
    ("surface_patch", lambda c: {"boundaries": [_ctx_get(c, "sdel_rim", "rim edges")],
                                 "interior_rails": [_ctx_get(c, "patch_rail", "the interior rail")]},
     "refused", None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # THE CASCADE, in a scratch document of its own: a sketch, an extrude on it and a shell on the
    # extrude. Suppressing the sketch switches the other two off, and deleting the extrude takes
    # what depends on it - each reply names what else it changed, checked against separate reads.
    ("doc_get", {}, _document_read,
     ("cascade_home", _recall("cascade_home", lambda p: p["active"]["document_handle"]))),
    ("doc_new", lambda c: {"expect_document": _ctx_get(c, "cascade_home", "the sweep document")},
     _new_document, ("cascade_doc", _recall("cascade_doc", lambda p: p["document_handle"]))),
    ("model_create_component", {"name": "Cascade", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "CascadeS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1100, "y1": 200,
                                           "x2": 1130, "y2": 230}],
                             "sketch_name": "CascadeS"}, "ok", None),
    ("model_extrude", {"sketch_name": "CascadeS", "profile_index": 0, "distance": 20}, _extruded,
     ("cascade_extrude", _recall("cascade_extrude", lambda p: p["feature"]))),
    ("find_geometry", {"target": "Cascade", "kind": "planar_face", "nearest_to": [1115, 215, 20],
                       "max_results": 1}, "ok", _fg("cascade_top")),
    ("model_shell", lambda c: {"body_name": "Cascade", "thickness": 2,
                               "remove_faces": [_ctx_get(c, "cascade_top", "the cascade top")]},
     _shelled, ("cascade_shell", _recall("cascade_shell", lambda p: p["feature"]))),
    ("model_inspect", {"target": "Cascade:1", "include": ["default", "mass"], "units": "mm"},
     lambda p: _num((p.get("mass") or {}).get("volume")),
     ("cascade_volume", _recall("cascade_volume", lambda p: p["mass"]["volume"]))),
    ("design_edit_timeline", {"action": "suppress", "feature": "CascadeS"},
     _also_named("also_suppressed", "cascade_extrude", "cascade_shell"), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000},
     _timeline_rows_suppressed("cascade_extrude", "cascade_shell"), None),
    ("design_edit_timeline", {"action": "suppress", "feature": "CascadeS", "suppressed": False},
     _also_named("also_unsuppressed", "cascade_extrude", "cascade_shell"), None),
    ("model_inspect", {"target": "Cascade:1", "include": ["default", "mass"], "units": "mm"},
     _same_volume("cascade_volume"), None),
    # A collapsed group hides the extrude and shell from the suppress census: the reply says so in
    # census_caveat, and the group= listing shows the hidden members did switch off.
    ("design_edit_timeline",
     lambda c: {"action": "group", "name": "CascadeG",
                "feature": "Cascade/" + _ctx_get(c, "cascade_extrude", "the cascade extrude"),
                "end_feature": "Cascade/" + _ctx_get(c, "cascade_shell", "the cascade shell")},
     lambda p: p.get("grouped") is True and p.get("group") == "CascadeG", None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _cascade_group_collapsed, None),
    ("design_edit_timeline", {"action": "suppress", "feature": "CascadeS"},
     _caveat_names_groups(1), None),
    ("design_get", {"include": ["timeline"], "group": "CascadeG", "max_results": 2000},
     _timeline_rows_suppressed("cascade_extrude", "cascade_shell"), None),
    ("design_edit_timeline", {"action": "suppress", "feature": "CascadeS", "suppressed": False},
     lambda p: p.get("is_suppressed") is False, None),
    ("model_inspect", {"target": "Cascade:1", "include": ["default", "mass"], "units": "mm"},
     _same_volume("cascade_volume"), None),
    # a member of the collapsed group is one timeline item with it: a write naming the member is
    # refused naming the group and the ungroup call, and nothing is suppressed or deleted.
    ("design_edit_timeline",
     lambda c: {"action": "suppress",
                "feature": _ctx_get(c, "cascade_shell", "the cascade shell")},
     _refused("'CascadeG'", "design_edit_timeline(action='ungroup', feature='CascadeG')"), None),
    ("design_delete_feature",
     lambda c: {"feature": _ctx_get(c, "cascade_shell", "the cascade shell")},
     _refused("'CascadeG'", "design_edit_timeline(action='ungroup', feature='CascadeG')"), None),
    ("model_inspect", {"target": "Cascade:1", "include": ["default", "mass"], "units": "mm"},
     _same_volume("cascade_volume"), None),
    ("design_edit_timeline", {"action": "ungroup", "feature": "CascadeG"},
     lambda p: p.get("ungrouped") is True, None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, "ok",
     ("cascade_count", _recall("cascade_count", lambda p: p["timeline"]["count"]))),
    ("design_delete_feature",
     lambda c: {"feature": "Cascade/" + _ctx_get(c, "cascade_extrude", "the cascade extrude")},
     lambda p: p.get("deleted") is True and isinstance(p.get("also_deleted"), list),
     ("cascade_delete", _recall("cascade_delete", lambda p: p))),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _cascade_counted, None),
    ("doc_activate",
     lambda c: {"name": _ctx_get(c, "cascade_home", "the sweep document"),
                "expect_document": _ctx_get(c, "cascade_doc", "the cascade document")},
     _activated(), None),
    ("doc_close",
     lambda c: {"name": _ctx_get(c, "cascade_doc", "the cascade document"),
                "save_changes": False,
                "expect_document": _ctx_get(c, "cascade_home", "the sweep document")},
     _document_closed, None),
    # THE FORM: a T-spline box built from a cage, its cage read back and re-created with the top
    # rim creased, the four sharp edges that makes filleted, then the rebuild loop's suppress and
    # delete naming the fillet they take - each against a separate read.
    ("model_create_component", {"name": "FormDemo", "activate": True}, _made_component, None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, "ok",
     ("form_tl0", _recall("form_tl0", lambda p: p["timeline"]["count"]))),
    ("form_create", {"primitive": {"shape": "box", "size": [20, 20, 20], "spans": [3, 3, 3]},
                     "name": "FormBox", "component": "FormDemo:1", "origin": [1300, 200, 10]},
     _form_made(_FORM_BOX_CM3, 0), ("form_box", _recall("form_box", lambda p: p["form"]))),
    _watch("FormDemo:1"),
    ("model_inspect", {"target": "FormBox", "include": ["default", "mass"], "units": "mm"},
     _inspected_mm3("FormBox volume", _FORM_BOX_CM3), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _form_row_added, None),
    # the Form's faces are NURBS surfaces, which kind='nurbs_face' selects.
    ("find_geometry", {"target": "FormBox", "kind": "nurbs_face"},
     lambda p: _measured("the box Form's NURBS faces", {"match_count": p.get("match_count")},
                         p.get("match_count") == 6), None),
    # every seam of the uncreased box reads smooth, measured apart from form_create's own gate.
    ("find_geometry", {"target": "FormBox", "max_results": 50},
     lambda p: _measured("the box Form's edges", {"edges": len(_edges_of(p))},
                         0 < len(_edges_of(p)) <= 20),
     ("form_box_edges", _edges_of)),
    ("model_measure_continuity",
     lambda c: {"edges": _ctx_get(c, "form_box_edges", "the box Form's edges")},
     _seams_read("the uncreased box Form's seams read smooth", lambda a: a < 0.01), None),
    ("workspace_orient", {}, lambda p: _measured("the workspace after form_create",
                                                 p.get("workspace"), p.get("workspace") == "Design"),
     None),
    ("form_get", lambda c: {"form": _ctx_get(c, "form_box", "the box Form"), "include": ["cage"]},
     _form_cage_read, ("form_box_cage", lambda p: p["cage"])),
    ("form_create", _creased_rim_cage, _form_made(_FORM_CREASE_CM3, 4),
     ("form_crease", _crease_made)),
    ("find_geometry", {"target": "FormCrease", "max_results": 50}, _crease_handles_found, None),
    # the four sharp edges form_create returned read as the right-angle crease they are, before
    # the fillet below rounds them away.
    ("model_measure_continuity", lambda c: {"edges": _RECALL["form_crease_edges"]},
     _seams_read("the creased rim reads 90 deg", lambda a: abs(a - 90.0) < 0.1), None),
    ("model_inspect", {"target": "FormCrease", "include": ["default", "mass"], "units": "mm"},
     _inspected_mm3("FormCrease volume", _FORM_CREASE_CM3), None),
    ("model_fillet", lambda c: {"edges": _RECALL["form_crease_edges"], "radius": 1},
     lambda p: _measured("the creased rim filleted", {k: p.get(k) for k in
                                                      ("edges_cut", "volume_delta_cm3")},
                         p.get("edges_cut") == 4
                         and _near_rel(p.get("volume_delta_cm3"), _FORM_FILLET_CM3)),
     ("form_fillet", _recall("form_fillet", lambda p: p["feature"]))),
    ("model_inspect", {"target": "FormCrease", "include": ["default", "mass"], "units": "mm"},
     _inspected_mm3("the filleted FormCrease volume", _FORM_CREASE_CM3 + _FORM_FILLET_CM3),
     ("form_crease_volume", _recall("form_crease_volume", lambda p: p["mass"]["volume"]))),
    ("form_get", {}, _forms_modified, None),
    ("design_edit_timeline", lambda c: {"action": "suppress",
                                        "feature": _ctx_get(c, "form_crease", "the creased Form")},
     _named_exactly("also_suppressed", "form_fillet"), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _form_pair_suppressed, None),
    ("design_edit_timeline", lambda c: {"action": "suppress", "suppressed": False,
                                        "feature": _ctx_get(c, "form_crease", "the creased Form")},
     _named_exactly("also_unsuppressed", "form_fillet"), None),
    ("model_inspect", {"target": "FormCrease", "include": ["default", "mass"], "units": "mm"},
     _same_volume("form_crease_volume"), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, "ok",
     ("form_tl_pre", _recall("form_tl_pre", lambda p: p["timeline"]["count"]))),
    ("design_delete_feature", lambda c: {"feature": _ctx_get(c, "form_crease", "the creased Form")},
     _named_exactly("also_deleted", "form_fillet"), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _form_count_is("form_tl_pre", -2),
     ("form_tl_post", _recall("form_tl_post", lambda p: p["timeline"]["count"]))),
    # an index past the eight vertices - a face naming a grip that does not exist is refused
    # before Fusion sees it, and the timeline reads unchanged.
    ("form_create", {"cage": {"vertices": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
                                           [0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10]],
                              "faces": [[0, 1, 2, 99]]}, "component": "FormDemo:1"},
     _refused("face 0", "99"), None),
    ("form_create", {"primitive": {"shape": "box", "size": [10, 10, 10], "spans": [1, 1, 1]},
                     "cage": {"vertices": [], "faces": []}, "component": "FormDemo:1"},
     _refused("primitive", "cage"), None),
    # A cage whose surface passes through itself passes the cage check, finishEdit raises, and the
    # Form edit stays OPEN for the user to close (measured: measure_api's last row), so that refusal
    # runs there, never in this story document.
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _form_count_is("form_tl_post", 0),
     None),
    # A capped cylinder built with the marker rolled back before the box Form: it lands before the
    # box, names it, and its grouped cage still reads by the Form's own address.
    ("design_edit_timeline", lambda c: {"action": "roll", "to": "before",
                                        "feature": _ctx_get(c, "form_box", "the box Form")},
     lambda p: p.get("rolled") is True, None),
    ("form_create", {"primitive": {"shape": "cylinder", "size": [20, 40], "spans": [4]},
                     "name": "FormCyl", "component": "FormDemo:1", "origin": [1270, 200, 20]},
     _cyl_made, ("form_cyl", _recall("form_cyl", lambda p: p))),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _cyl_before_box, None),
    ("design_edit_timeline", {"action": "roll", "to": "end"},
     lambda p: p.get("rolled") is True and p.get("rolled_back") == 0, None),
    ("model_inspect", {"target": "FormCyl", "include": ["default", "mass"], "units": "mm"},
     _cyl_inspected, None),
    ("design_edit_timeline", lambda c: {"action": "group", "name": "FormPair",
                                        "feature": _RECALL["form_cyl"]["form"],
                                        "end_feature": _ctx_get(c, "form_box", "the box Form")},
     lambda p: p.get("grouped") is True and p.get("group") == "FormPair", None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _group_collapsed("FormPair"),
     None),
    ("form_get", lambda c: {"form": _RECALL["form_cyl"]["form"], "include": ["cage"]},
     _cyl_cage_read, None),
    ("design_edit_timeline", {"action": "ungroup", "feature": "FormPair"},
     lambda p: p.get("ungrouped") is True, None),
    # An uncapped cylinder is an open surface Form: its area stands in for a volume, and a
    # downstream move is what its record's corners catch.
    ("form_create", {"primitive": {"shape": "cylinder", "size": [20, 40], "spans": [4],
                                   "capped": False},
                     "name": "FormTube", "component": "FormDemo:1", "origin": [1360, 200, 20]},
     _tube_made, ("form_tube", _recall("form_tube", lambda p: p))),
    _watch("FormDemo:1"),
    ("find_geometry", {"target": "FormTube", "max_results": 20}, _tube_area, None),
    ("model_inspect", {"target": "FormDemo:1", "include": ["mass"], "per_body": True},
     _form_bodies_solid, None),
    ("model_inspect", {"target": "FormTube", "units": "mm"}, "ok",
     ("form_tube_z", _recall("form_tube_z", lambda p: p["min_point"]["z"]))),
    ("model_move", {"bodies": ["FormTube"], "dz": 10}, "ok", None),
    ("model_inspect", {"target": "FormTube", "units": "mm"}, _tube_raised, None),
    ("form_get", {}, _moved_form_modified, None),
    # THE BLEND: an edge loft with smooth and tangent ends; a free-ended loft over the same rims
    # is the crease an always-0 seam read fails.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "Blend", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "BlendA"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 1500, "cy": 200, "radius": 14}],
                             "sketch_name": "BlendA"}, "ok", None),
    ("surface_extrude", {"sketch_name": "BlendA", "distance": 50}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 90, "name": "BlendTopPlane"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "BlendTopPlane", "name": "BlendB"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 1508, "cy": 200, "radius": 10}],
                             "sketch_name": "BlendB"}, "ok", None),
    ("surface_extrude", {"sketch_name": "BlendB", "distance": 50}, "ok", None),
    ("find_geometry", {"target": "Blend", "kind": "circular_edge", "radius": 14,
                       "nearest_to": [1500, 200, 50], "max_results": 1},
     _matched(1, "circular_edge"), _fg("blend_rim_a")),
    ("find_geometry", {"target": "Blend", "kind": "circular_edge", "radius": 10,
                       "nearest_to": [1508, 200, 90], "max_results": 1},
     _matched(1, "circular_edge"), _fg("blend_rim_b")),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "blend_rim_a", "tube A's rim"),
                                           _ctx_get(c, "blend_rim_b", "tube B's rim")],
                              "as_surface": True, "start": "smooth", "end": "tangent"},
     _blend_lofted("smooth", "tangent"),
     ("blend_smooth", _recall("blend_smooth", lambda p: {
         "body": "Blend:1:" + p["result_bodies"][0],
         "start_weight": p["model_parameters"]["start_weight"]}))),
    ("model_measure_continuity",
     lambda c: {"edges": [_ctx_get(c, "blend_rim_a", "tube A's rim"),
                          _ctx_get(c, "blend_rim_b", "tube B's rim")],
                "against": _RECALL["blend_smooth"]["body"]},
     _seams_read("both seams under 0.01 deg, the tangent one's curvature jump 10x the smooth one's",
                 lambda a: a < 0.01, _g2_start_g1_end), None),
    ("param_set", lambda c: {"name": _RECALL["blend_smooth"]["start_weight"], "expression": "1.5"},
     lambda p: _measured("the start weight moved to 1.5",
                         {"set": p.get("set"), "after": (p.get("after") or {}).get("expression")},
                         p.get("set") is True and (p.get("after") or {}).get("expression") == "1.5"),
     None),
    ("design_recompute", {},
     lambda p: p.get("recomputed") is True and _num(p.get("error_count")), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _blend_lofts_healthy(1), None),
    ("model_measure_continuity",
     lambda c: {"edges": [_ctx_get(c, "blend_rim_a", "tube A's rim")],
                "against": _RECALL["blend_smooth"]["body"]},
     _seams_read("the reweighted smooth seam still reads under 0.01 deg", lambda a: a < 0.01),
     None),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "blend_rim_a", "tube A's rim"),
                                           _ctx_get(c, "blend_rim_b", "tube B's rim")],
                              "as_surface": True},
     _blend_lofted("free", "free"),
     ("blend_free", _recall("blend_free", lambda p: "Blend:1:" + p["result_bodies"][0]))),
    ("model_measure_continuity",
     lambda c: {"edges": [_ctx_get(c, "blend_rim_a", "tube A's rim")],
                "against": _RECALL["blend_free"]},
     _seams_read("the free end creases the seam past 2 deg", lambda a: a > 2.0), None),
    # a tangent end on a PROFILE section: refused before Fusion sees it, the timeline unchanged.
    ("sketch_get", {"sketch_name": "BlendA"}, "ok", _prof("blend_a_profile")),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, "ok",
     ("blend_tl", _recall("blend_tl", lambda p: p["timeline"]["count"]))),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "blend_a_profile", "tube A's profile"),
                                           _ctx_get(c, "blend_rim_b", "tube B's rim")],
                              "as_surface": True, "start": "tangent"},
     _refused("tangent", "edge"), None),
    ("design_get", {"include": ["timeline"], "max_results": 2000}, _form_count_is("blend_tl", 0),
     None),
    # a NOSE from tube B's free rim to a sketch point: the point_tangent tip is a dome, so the
    # rim seam bends harder than under the point_sharp twin's cone - both seams stay tangent.
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 160,
                            "name": "BlendNosePlane"}, _datum_plane("xy"), None),
    ("sketch_create", {"plane": "BlendNosePlane", "name": "BlendNose"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "point", "cx": 1508, "cy": 200}],
                             "sketch_name": "BlendNose"}, "ok", None),
    ("find_geometry", {"target": "Blend", "kind": "circular_edge", "radius": 10,
                       "nearest_to": [1508, 200, 140], "max_results": 1},
     _matched(1, "circular_edge"), _fg("blend_rim_top")),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "blend_rim_top", "tube B's free rim"),
                                           "BlendNose/point:1"],
                              "as_surface": True, "start": "tangent", "end": "point_sharp"},
     _lofted_as({"start": "tangent", "end": "point_sharp"}, ["edge", "point"], ["start_weight"]),
     ("blend_cone", _recall("blend_cone", lambda p: "Blend:1:" + p["result_bodies"][0]))),
    ("model_loft", lambda c: {"profiles": [_ctx_get(c, "blend_rim_top", "tube B's free rim"),
                                           "BlendNose/point:1"],
                              "as_surface": True, "start": "tangent", "end": "point_tangent"},
     _lofted_as({"start": "tangent", "end": "point_tangent"}, ["edge", "point"],
                ["start_weight", "end_weight"]),
     ("blend_dome", _recall("blend_dome", lambda p: "Blend:1:" + p["result_bodies"][0]))),
    ("model_measure_continuity",
     lambda c: {"edges": [_ctx_get(c, "blend_rim_top", "tube B's free rim")],
                "against": _RECALL["blend_cone"]},
     _seams_read("the cone's rim seam under 0.01 deg", lambda a: a < 0.01),
     ("blend_cone_jump", _recall("blend_cone_jump",
                                 lambda p: p["edges"][0]["max_curvature_jump"]))),
    ("model_measure_continuity",
     lambda c: {"edges": [_ctx_get(c, "blend_rim_top", "tube B's free rim")],
                "against": _RECALL["blend_dome"]},
     _seam_jump_past("blend_cone_jump", 1.5), None),
    # OPEN sketch curves with a direction start: at the 0 deg default the surface stays over the
    # 100 mm-wide sections, and the start_angle parameter at 30 deg pushes its box past them.
    ("sketch_create", {"plane": "xy", "name": "BlendOpenA"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "spline",
                                           "points": [[1600, 200], [1650, 220], [1700, 200]]}],
                             "sketch_name": "BlendOpenA"}, "ok", None),
    ("sketch_create", {"plane": "BlendTopPlane", "name": "BlendOpenB"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "spline",
                                           "points": [[1600, 200], [1650, 190], [1700, 205]]}],
                             "sketch_name": "BlendOpenB"}, "ok", None),
    ("model_loft", {"profiles": ["BlendOpenA/spline:0", "BlendOpenB/spline:0"],
                    "as_surface": True, "start": "direction"},
     _lofted_as({"start": "direction", "end": "free"}, ["curve", "curve"],
                ["start_weight", "start_angle"]),
     ("blend_open", _recall("blend_open", lambda p: {
         "body": "Blend:1:" + p["result_bodies"][0],
         "angle": p["model_parameters"]["start_angle"]}))),
    ("model_inspect", lambda c: {"target": _RECALL["blend_open"]["body"]},
     _box_x(99.99, 100.01), None),
    ("param_set", lambda c: {"name": _RECALL["blend_open"]["angle"], "expression": "30 deg"},
     lambda p: p.get("set") is True, None),
    ("model_inspect", lambda c: {"target": _RECALL["blend_open"]["body"]},
     _box_x(105.0, 130.0), None),
    # a section OBJECT carrying a key the selector does not read is refused, naming the string
    # form a sketch curve takes - never resolved as that sketch's profile 0.
    ("model_loft", {"profiles": [{"sketch": "BlendOpenA", "curve": "spline:0"},
                                 "BlendOpenB/spline:0"], "as_surface": True},
     _refused("'curve' is not read", "'<sketch>/<type>:<index>'"), None),
    # the same direction start on two PROFILES: 32 x 28 mm over the rims at 0 deg, wider at 30.
    ("model_loft", {"profiles": [{"sketch": "BlendA", "profile_index": 0},
                                 {"sketch": "BlendB", "profile_index": 0}],
                    "as_surface": True, "start": "direction"},
     _lofted_as({"start": "direction", "end": "free"}, ["profile", "profile"],
                ["start_weight", "start_angle"]),
     ("blend_prof_dir", _recall("blend_prof_dir", lambda p: {
         "body": "Blend:1:" + p["result_bodies"][0],
         "angle": p["model_parameters"]["start_angle"]}))),
    ("model_inspect", lambda c: {"target": _RECALL["blend_prof_dir"]["body"]},
     _box_x(31.99, 32.01), None),
    ("param_set", lambda c: {"name": _RECALL["blend_prof_dir"]["angle"], "expression": "30 deg"},
     lambda p: p.get("set") is True, None),
    ("model_inspect", lambda c: {"target": _RECALL["blend_prof_dir"]["body"]},
     _box_x(35.0, 60.0), None),
    # RAILS: two strips' inner edges guide a loft between open splines. rail_continuity g1 meets
    # the strip tangent along the rail; the g0 twin creases it.
    ("sketch_create", {"plane": "xy", "name": "BlendStripL"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 1800, "y1": 200,
                                           "x2": 1760, "y2": 180}],
                             "sketch_name": "BlendStripL"}, "ok", None),
    ("surface_extrude", {"sketch_name": "BlendStripL", "distance": 90}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "BlendStripR"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 1840, "y1": 200,
                                           "x2": 1880, "y2": 180}],
                             "sketch_name": "BlendStripR"}, "ok", None),
    ("surface_extrude", {"sketch_name": "BlendStripR", "distance": 90}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "BlendRailA"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "spline",
                                           "points": [[1800, 200], [1820, 202], [1840, 200]]}],
                             "sketch_name": "BlendRailA"}, "ok", None),
    ("sketch_create", {"plane": "BlendTopPlane", "name": "BlendRailB"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "spline",
                                           "points": [[1800, 200], [1820, 202], [1840, 200]]}],
                             "sketch_name": "BlendRailB"}, "ok", None),
    ("find_geometry", {"target": "Blend", "kind": "line_edge", "nearest_to": [1800, 200, 45],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("blend_rail_l")),
    ("find_geometry", {"target": "Blend", "kind": "line_edge", "nearest_to": [1840, 200, 45],
                       "max_results": 1}, _matched(1, "line_edge"), _fg("blend_rail_r")),
    ("model_loft", lambda c: {"profiles": ["BlendRailA/spline:0", "BlendRailB/spline:0"],
                              "rails": [_ctx_get(c, "blend_rail_l", "the left strip's edge"),
                                        _ctx_get(c, "blend_rail_r", "the right strip's edge")],
                              "as_surface": True, "rail_continuity": "g1"},
     _lofted_as({"start": "free", "end": "free"}, ["curve", "curve"], [],
                rail_continuity="g1", rails_count=2),
     ("blend_rail_g1", _recall("blend_rail_g1", lambda p: "Blend:1:" + p["result_bodies"][0]))),
    ("model_loft", lambda c: {"profiles": ["BlendRailA/spline:0", "BlendRailB/spline:0"],
                              "rails": [_ctx_get(c, "blend_rail_l", "the left strip's edge"),
                                        _ctx_get(c, "blend_rail_r", "the right strip's edge")],
                              "as_surface": True, "rail_continuity": "g0"},
     _lofted_as({"start": "free", "end": "free"}, ["curve", "curve"], [],
                rail_continuity="g0", rails_count=2),
     ("blend_rail_g0", _recall("blend_rail_g0", lambda p: "Blend:1:" + p["result_bodies"][0]))),
    ("model_measure_continuity",
     lambda c: {"edges": [_ctx_get(c, "blend_rail_l", "the left strip's edge")],
                "against": _RECALL["blend_rail_g1"]},
     _seams_read("the g1 rail seam under 0.01 deg", lambda a: a < 0.01), None),
    ("model_measure_continuity",
     lambda c: {"edges": [_ctx_get(c, "blend_rail_l", "the left strip's edge")],
                "against": _RECALL["blend_rail_g0"]},
     _seams_read("the g0 twin creases the rail seam past 10 deg", lambda a: a > 10.0), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "Proj"}, "ok", None),
    ("find_geometry", {"target": "SDel", "kind": "planar_face", "nearest_to": [310, 210, 0], "max_results": 1}, "ok", _fg("proj_face")),
    ("sketch_project", lambda c: {"entities": [_ctx_get(c, "proj_face", "project face")], "sketch_name": "Proj"}, "ok", None),
    # surface_trim + surface_untrim on intersecting sheets in clear space, with a COPLANAR decoy
    # sheet the same cutter crosses: its cells enter the same compute, so the trim below is only
    # correct if it removes the cells its target owns and leaves the decoy's alone.
    ("model_create_component", {"name": "SHole", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SH1"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 600, "y1": 0,
                                           "x2": 640, "y2": 0}],
                             "sketch_name": "SH1"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SH1", "distance": 40}, "ok", None),
    ("sketch_create", {"plane": "xz", "name": "SH2"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 620, "cy": -20, "radius": 5}],
                             "sketch_name": "SH2"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SH2", "distance": 10, "symmetric": True}, "ok", None),
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_sheet")),
    ("find_geometry", {"target": "SHole", "kind": "cylinder_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_cutter")),
    # the decoy: a second sheet in the same plane, starting ON the cutter's axis so the cylinder
    # cuts a half-disc out of it too. It stays VISIBLE through the trim.
    ("sketch_create", {"plane": "xy", "name": "SH3"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 620, "y1": 0,
                                           "x2": 660, "y2": 0}],
                             "sketch_name": "SH3"}, "ok", None),
    ("surface_extrude", {"sketch_name": "SH3", "distance": 40},
     lambda p: p["is_solid"] is False and len(p["result_bodies"]) == 1,
     ("sh_decoy_body", _recall("sh_decoy_body", lambda p: p["result_bodies"][0]))),
    # the decoy's area BEFORE the trim - the number the read after it is compared against.
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [650, 0, 20],
                       "max_results": 1}, _matched(1, "planar_face"),
     ("sh_decoy_area", _recall("sh_decoy_area", lambda p: p["matches"][0]["area"]))),
    ("view_set", {"action": "snapshot"}, "ok", None),
    ("view_set", {"action": "isolate", "target": ["SHole:1"]},
     lambda p: p.get("action") == "isolate" and p.get("affected") == ["SHole:1"], None),
    # REFUSED: a second occurrence alongside it. Fusion holds one isolation at a time, so the pair
    # cannot both be isolated and the refusal hands over the two ways round it. Nothing is written -
    # the isolation above is still the one in force for the trim beats below.
    ("view_set", {"action": "isolate", "target": ["SHole:1", "SDel:1"]},
     _refused("one isolation at a time", "PARENT occurrence", "snapshot"), None),
    ("view_set", lambda c: {"action": "hide",
                            "target": [_ctx_get(c, "sh_cutter", "cylinder cutter")]},
     lambda p: p.get("action") == "hide" and bool(p.get("bodies"))
     and all(row.get("visible") is False for row in p["bodies"]), None),
    # the cell bookkeeping is the read-back: at least one cell REMOVED (a trim that removed none
    # kept the whole sheet), a kept area the phantom-cell gate could measure, and the decoy's cells
    # named as another body's and left unselected.
    ("surface_trim", lambda c: {"surface": _ctx_get(c, "sh_sheet", "sheet face"), "trim_tool": _ctx_get(c, "sh_cutter", "cylinder cutter")},
     _trim_scoped_to_target("sh_decoy_body"), None),
    # the decoy measured again, INDEPENDENTLY of the trim's own foreign_bodies_unchanged read.
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [650, 0, 20],
                       "max_results": 1}, _same_face_area("sh_decoy_area"), None),
    ("find_geometry", {"target": "SHole", "kind": "planar_face", "nearest_to": [620, 0, 20], "max_results": 1}, "ok", _fg("sh_trimmed")),
    # removing the hole loop FILLS it, so the created faces' area sum is the read-back that the
    # extent grew; an untrim that created nothing leaves area_after at or below area_before.
    ("surface_untrim", lambda c: {"faces": [_ctx_get(c, "sh_trimmed", "trimmed sheet face")], "loop_type": "internal"},
     lambda p: p.get("extent_grew") is True and p.get("faces_created", 0) >= 1
     and p["area_after"] > p["area_before"], None),
    # Body bulbs are outside snapshot/restore, so show the cutter before restoring occurrences/camera.
    ("view_set", lambda c: {"action": "show",
                            "target": [_ctx_get(c, "sh_cutter", "cylinder cutter")]},
     lambda p: p.get("action") == "show" and bool(p.get("bodies"))
     and all(row.get("visible") is True for row in p["bodies"]), None),
    ("view_set", {"action": "restore"},
     lambda p: p.get("camera_restored") is True and p.get("visual_style_restored") is True
     and p.get("missing_occurrences") == 0 and p.get("restored_occurrences", 0) >= 1
     and p.get("snapshot_kept") is False, None),
    # RULED surfaces off a rim edge. A line extruded as a surface gives a vertical sheet whose top
    # rim (z=30) is the seed every ruled beat leaves from; each type builds a DIFFERENT surface off
    # that same edge, so the payload's own ruled_type/direction is what separates them.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "Ruled", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RuledS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "line", "x1": 900, "y1": 0,
                                           "x2": 940, "y2": 0}],
                             "sketch_name": "RuledS"}, "ok", None),
    ("surface_extrude", {"sketch_name": "RuledS", "distance": 30}, "ok", None),
    ("find_geometry", {"target": "Ruled", "kind": "line_edge", "nearest_to": [920, 0, 30],
                       "max_results": 1}, "ok", _fg("ruled_edge")),
    # tangent continues the parent face's own plane past the rim, landing ONE new open body.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "tangent"},
     lambda p: p.get("is_solid") is False and len(p.get("result_bodies", [])) == 1
     and p.get("ruled_type") == "tangent", None),
    # normal stands perpendicular to that same face - a different surface off the same seed edge.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "normal"},
     lambda p: p.get("is_solid") is False and p.get("ruled_type") == "normal", None),
    # direction sweeps along an ENTITY: a world axis resolves to the component's origin axis, which
    # is what createInput consumes (a direction VECTOR cannot be handed to it).
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "direction", "direction": "z"},
     lambda p: p.get("direction") == "z-axis" and p.get("is_solid") is False, None),
    # angle_deg reads back in DEGREES off the feature's own ModelParameter (radians at the API).
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "angle_deg": 20},
     lambda p: abs(p.get("angle_deg", 0) - 20) < 1e-6, None),
    # a direction entity with a type that ignores it is refused: the payload could otherwise claim
    # tangent while a Direction surface was built.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "tangent", "direction": "z"},
     "refused", None),
    # the Direction type with no entity: Fusion itself refuses to build the input.
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_edge", "the rim edge")],
                                        "distance": 15, "ruled_type": "direction"}, "refused", None),
    # ruling off a SOLID box edge - the draft-check case. The feature's body collection holds the
    # box AND the new sheet, so a handler publishing that collection raw would report the box as
    # created and read is_solid=true off it: the new sheet alone is the result.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "RuledSolid", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RuledSolidS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 980, "y1": 200,
                                           "x2": 1020, "y2": 240}],
                             "sketch_name": "RuledSolidS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RuledSolidS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "RuledSolid", "kind": "line_edge", "nearest_to": [1000, 200, 20],
                       "max_results": 1}, "ok", _fg("ruled_solid_edge")),
    ("surface_create_ruled", lambda c: {"edges": [_ctx_get(c, "ruled_solid_edge", "a box top edge")],
                                        "distance": 15, "ruled_type": "tangent"},
     lambda p: p.get("is_solid") is False and len(p.get("result_bodies", [])) == 1
     and "Body1" not in p.get("result_bodies", []), None),
    # back to the component that was active before this cameo, so the ones after it nest as before.
    ("design_activate_component", {"occurrence": "SHole:1"}, "ok", None),
    # split / unstitch / stitch / base-feature / arrange / compute-holder.
    ("model_create_component", {"name": "Spl", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "Sp1"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 400, "y1": 200,
                                           "x2": 440, "y2": 240}],
                             "sketch_name": "Sp1"}, "ok", None),
    ("model_extrude", {"sketch_name": "Sp1", "profile_index": 0, "distance": 20}, _extruded, None),
    ("model_construction", {"kind": "plane", "plane": "xz", "offset": 220, "name": "SplMid"},
     _datum_plane("xz"), None),
    ("find_geometry", {"target": "Spl", "kind": "planar_face", "nearest_to": [420, 220, 20], "max_results": 1}, "ok", _fg("spl_body")),
    ("model_split", lambda c: {"split": "body", "target": _ctx_get(c, "spl_body", "split body"), "split_plane": "SplMid"}, _split_bodies, None),
    ("model_create_component", {"name": "Stc", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "St1"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 500, "y1": 200,
                                           "x2": 520, "y2": 220}],
                             "sketch_name": "St1"}, "ok", None),
    ("model_extrude", {"sketch_name": "St1", "profile_index": 0, "distance": 10}, _extruded, None),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [510, 210, 10], "max_results": 1}, "ok", _fg("stc_body")),
    ("model_unstitch", lambda c: {"target": _ctx_get(c, "stc_body", "unstitch body"), "chain": False}, _unstitched, None),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [510, 210, 0], "max_results": 1}, "ok", _fg("stc_f1")),
    ("find_geometry", {"target": "Stc", "kind": "planar_face", "nearest_to": [500, 210, 5], "max_results": 1}, "ok", _fg("stc_f2")),
    ("model_stitch", lambda c: {"bodies": [_ctx_get(c, "stc_f1", "stitch a"), _ctx_get(c, "stc_f2", "stitch b")]}, _stitched, None),
    # Preserve the full composed timeline, prove a wrong name leaves BF1 open, then finish by the
    # actual adopted name and independently read the one-row delta.
    ("doc_get", {}, _document_read,
     ("bf_story_doc", _recall("bf_story_doc", lambda p: p["active"]["document_handle"]))),
    ("design_get", {"include": ["mode", "timeline"], "max_results": 2000},
     _base_feature_parametric,
     ("bf_story_before", _recall("bf_story_before", _base_feature_state))),
    ("model_base_feature",
     lambda c: {"action": "start", "base_feature": "BF1",
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     _base_feature_open,
     ("bf_story_name", _recall("bf_story_name", lambda p: p["base_feature"]))),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("model_base_feature",
     lambda c: {"action": "finish", "base_feature": "BF1Missing",
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     _refused("No captured or existing base feature named 'BF1Missing'",
              "active document", "exact base_feature returned by start"), None),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("model_base_feature",
     lambda c: {"action": "finish",
                "base_feature": _ctx_get(c, "bf_story_name", "the adopted BF1 name"),
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     lambda p: p.get("editing") is False and len(p.get("closed_scopes") or []) == 1
     and p.get("named_finished") == _RECALL["bf_story_name"], None),
    ("design_get", {"include": ["mode", "timeline"], "max_results": 2000},
     _base_feature_added("bf_story_before", "bf_story_name"),
     ("bf_story_after", _recall("bf_story_after", _base_feature_state))),

    # Named round: open the same adopted name in the sweep document and one owned scratch document.
    # Finishing B must leave A direct/no-timeline until A is activated and explicitly finished.
    ("model_base_feature",
     lambda c: {"action": "start", "base_feature": "BFCrossNamed",
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     _base_feature_open,
     ("bf_cross_named_a", _recall("bf_cross_named_a", lambda p: p["base_feature"]))),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("doc_new",
     lambda c: {"expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     _new_document,
     ("bf_extra_doc", _recall("bf_extra_doc", lambda p: p["document_handle"]))),
    ("design_get", {"include": ["mode", "timeline"], "max_results": 200},
     _base_feature_parametric,
     ("bf_extra_before", _recall("bf_extra_before", _base_feature_state))),
    ("model_base_feature",
     lambda c: {"action": "start",
                "base_feature": _ctx_get(c, "bf_cross_named_a", "the shared scope name"),
                "expect_document": _ctx_get(c, "bf_extra_doc", "the extra document")},
     _base_feature_same_name("bf_cross_named_a"),
     ("bf_cross_named_b", _recall("bf_cross_named_b", lambda p: p["base_feature"]))),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("model_base_feature",
     lambda c: {"action": "finish",
                "base_feature": _ctx_get(c, "bf_cross_named_b", "the extra document scope"),
                "expect_document": _ctx_get(c, "bf_extra_doc", "the extra document")},
     lambda p: p.get("editing") is False and len(p.get("closed_scopes") or []) == 1,
     None),
    ("design_get", {"include": ["mode", "timeline"], "max_results": 200},
     _base_feature_added("bf_extra_before", "bf_cross_named_b"),
     ("bf_extra_named_after", _recall("bf_extra_named_after", _base_feature_state))),
    ("doc_activate",
     lambda c: {"name": _ctx_get(c, "bf_story_doc", "the sweep document"),
                "expect_document": _ctx_get(c, "bf_extra_doc", "the extra document")},
     _activated(), None),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("model_base_feature",
     lambda c: {"action": "finish",
                "base_feature": _ctx_get(c, "bf_cross_named_a", "the sweep document scope"),
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     lambda p: p.get("editing") is False and len(p.get("closed_scopes") or []) == 1,
     None),
    ("design_get", {"include": ["mode", "timeline"], "max_results": 2000},
     _base_feature_added("bf_story_after", "bf_cross_named_a"),
     ("bf_story_named_after", _recall("bf_story_named_after", _base_feature_state))),

    # Unnamed round on the same two documents proves the active-document filter also governs the
    # legacy finish route. Each document remains independently readable before the other closes.
    ("model_base_feature",
     lambda c: {"action": "start", "base_feature": "BFCrossUnnamed",
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     _base_feature_open,
     ("bf_cross_unnamed_a", _recall("bf_cross_unnamed_a", lambda p: p["base_feature"]))),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("doc_activate",
     lambda c: {"name": _ctx_get(c, "bf_extra_doc", "the extra document"),
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     _activated(), None),
    ("model_base_feature",
     lambda c: {"action": "start",
                "base_feature": _ctx_get(c, "bf_cross_unnamed_a", "the shared scope name"),
                "expect_document": _ctx_get(c, "bf_extra_doc", "the extra document")},
     _base_feature_same_name("bf_cross_unnamed_a"),
     ("bf_cross_unnamed_b", _recall("bf_cross_unnamed_b", lambda p: p["base_feature"]))),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("model_base_feature",
     lambda c: {"action": "finish",
                "expect_document": _ctx_get(c, "bf_extra_doc", "the extra document")},
     lambda p: p.get("editing") is False and len(p.get("closed_scopes") or []) == 1,
     None),
    ("design_get", {"include": ["mode", "timeline"], "max_results": 200},
     _base_feature_added("bf_extra_named_after", "bf_cross_unnamed_b"), None),
    ("doc_activate",
     lambda c: {"name": _ctx_get(c, "bf_story_doc", "the sweep document"),
                "expect_document": _ctx_get(c, "bf_extra_doc", "the extra document")},
     _activated(), None),
    ("design_get", {"include": ["mode"]}, _base_feature_direct, None),
    ("model_base_feature",
     lambda c: {"action": "finish",
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     lambda p: p.get("editing") is False and len(p.get("closed_scopes") or []) == 1,
     None),
    ("design_get", {"include": ["mode", "timeline"], "max_results": 2000},
     _base_feature_added("bf_story_named_after", "bf_cross_unnamed_a"), None),
    ("doc_close",
     lambda c: {"name": _ctx_get(c, "bf_extra_doc", "the extra document"),
                "save_changes": False,
                "expect_document": _ctx_get(c, "bf_story_doc", "the sweep document")},
     _document_closed, None),
    ("design_activate_component", {"occurrence": "Stc:1"}, "ok", None),
] + [
    # compute_holder needs a body + a cyl-face axis + a planar end-datum.
    ("model_create_component", {"name": "HolderPart", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "HP1"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 600, "y1": 200,
                                           "x2": 640, "y2": 220}],
                             "sketch_name": "HP1"}, "ok", None),
    ("model_extrude", {"sketch_name": "HP1", "profile_index": 0, "distance": 10}, _extruded, None),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "nearest_to": [620, 210, 10], "max_results": 1}, "ok", _fg("hp_top")),
    # hole points ride the face's LOCAL frame = the model origin projected onto the face, so
    # on-pad coordinates are the world x,y (same measured fact as the FeatureCameo hole).
    ("model_hole", lambda c: {"face": _ctx_get(c, "hp_top", "holder top"), "hole_type": "simple", "diameter": "4 mm", "extent": "blind", "depth": "8 mm", "points": [[605, 205, 0]]}, _drilled(1), None),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "max_results": 1}, "ok", _fg("hp_body")),
    ("find_geometry", {"target": "HolderPart", "kind": "cylinder_face", "radius": 2, "max_results": 1}, "ok", _fg("hp_axis")),
    ("find_geometry", {"target": "HolderPart", "kind": "planar_face", "nearest_to": [620, 210, 10], "max_results": 1}, "ok", _fg("hp_datum")),
    ("model_compute_holder", lambda c: {"body": _ctx_get(c, "hp_body", "holder body"), "axis": _ctx_get(c, "hp_axis", "holder axis"), "end_datum": _ctx_get(c, "hp_datum", "holder datum")}, _holder_computed, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
]

# ACT 7b: NESTING - model_arrange as a FUNCTION of its boundary. It runs after the
# parametric resize on purpose: the solver restructures the parts it nests under new
# Envelope occurrences, and that is not a thing to hand to an act that recomputes the
# whole assembly.
_NESTING = _box("ArrP1", ox=200, oy=350) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # THREE DIFFERENT shapes to nest, not two of the same box: a square pad, a long bar and a disc.
    # A nest that only ever sees one footprint proves nothing about the solver.
    ("model_create_component", {"name": "ArrP2", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ArrP2S"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 260, "y1": 350,
                                           "x2": 320, "y2": 368}],
                             "sketch_name": "ArrP2S"}, "ok", None),
    ("model_extrude", {"sketch_name": "ArrP2S", "profile_index": 0, "distance": 10}, _extruded, None),
    ("model_create_component", {"name": "ArrP3", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "ArrP3S"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 350, "cy": 359, "radius": 16}],
                             "sketch_name": "ArrP3S"}, "ok", None),
    ("model_extrude", {"sketch_name": "ArrP3S", "profile_index": 0, "distance": 10}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # a SECOND bar, so the nest carries repeats as well as variety - four shapes from three
    # components. The instance number is Fusion's to pick, so it is read back, never predicted.
    ("design_add_instance", {"component": "ArrP2", "x": 0, "y": -35, "units": "mm"},
     lambda p: p.get("created") is True and str(p.get("full_path", "")).startswith("ArrP2:"),
     ("arr_bar2", lambda p: p["full_path"])),
    # The boundary is a HEXAGON, not a rectangle: a true-shape nest against a slanted wall is the
    # case a box boundary cannot show. Its six lines are line:0..line:5, which is what the reshape
    # below scales.
    ("sketch_create", {"plane": "xy", "name": "ArrB"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "polygon", "cx": 300, "cy": 500,
                                           "radius": 120, "sides": 6}],
                             "sketch_name": "ArrB"},
     lambda p: p["results"][0].get("curves_added") == 6, None),
    _watch(["ArrB", "ArrP1:1", "ArrP2:1", "ArrP3:1"]),
    # THE PINNED SHAPE: a shape reading isGroundToParent True is what the solver calls "Pinned"
    # (ARRANGE_ITEM_GROUNDED, measured), so an in-place arrange refuses it BEFORE the feature, naming
    # the shape and the release; the phrase asserted is the pre-flight's own, not the platform's.
    ("assembly_ground", {"occurrence": "ArrP1:1", "ground_to_parent": True},
     lambda p: p.get("isGroundToParent") is True and bool(p.get("occurrence")), None),
    ("model_arrange", {"boundary_sketch": "ArrB", "shapes": ["ArrP1:1"], "move_originals": True},
     _refused("ArrP1:1", "read isGroundToParent True", "ground_to_parent=false"), None),
    ("assembly_ground", {"occurrence": "ArrP1:1", "ground_to_parent": False},
     lambda p: p.get("isGroundToParent") is False and bool(p.get("occurrence")), None),
    # THE ACTIVE EDIT TARGET: measured on both solvers, an arrange whose shapes include the active
    # component fails with a bare '3 :' - so the shape is refused before the feature, and the same
    # call lands once root is active again (the nest below it).
    ("design_activate_component", {"occurrence": "ArrP1:1"}, "ok", None),
    ("model_arrange", {"boundary_sketch": "ArrB", "shapes": ["ArrP1:1"]},
     _refused("ArrP1:1", "ACTIVE edit target", "design_activate_component('root')"), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # TRUE-SHAPE, because the boundary is a hexagon: the rectangular solver nests bounding boxes and
    # refuses a non-rectangular envelope outright (ARRANGE_ERROR_ENVELOPE_INVALIDRECTANGULAR), which
    # is exactly what a slanted wall is for. The solver places COPIES under an Envelope occurrence
    # and leaves the named inputs where they were, so the nested part is picked out of what the call
    # PUBLISHED - and it is that copy the reshape below is measured on.
    # Every solver runs on the base licence with a boundary or an envelope and a spacing (measured
    # on the lapsed install: this nest placed 4, the sheet 3, the box 3); the platform's "Cannot
    # set extension value" is an OPTION the extension owns, which none of these rows pass.
    # The spacing is 1/64 in: 0.0396875 cm carries more decimals than the 6-place read-back.
    ("model_arrange", lambda c: {"boundary_sketch": "ArrB",
                                 "shapes": ["ArrP1:1", "ArrP2:1",
                                            _ctx_get(c, "arr_bar2", "the second bar"), "ArrP3:1"],
                                 "solver": "true_shape", "spacing": 0.015625, "units": "in"},
     lambda p: _arranged(4)(p) and _measured(
         "the 1/64 in spacing read back", {"settings": p.get("settings"), "units": p.get("units")},
         p.get("units") == "in" and (p.get("settings") or {}).get("spacing") == 0.015625),
     ("nest_disc", lambda p: next(o for o in p["new_occurrences"] if "+ArrP3:" in o))),
    ("model_inspect", lambda c: {"target": _ctx_get(c, "nest_disc", "the nested disc")},
     _extent_measured, ("nest_y0", _recall("nest_y0", lambda p: p["center"]["y"]))),
    _dwell(2.0),
    # RESHAPE the boundary: the Arrange feature RECOMPUTES off its boundary sketch, so the nest is a
    # FUNCTION of the envelope rather than a one-time placement. Solving again would not show this -
    # a second identical arrange stacks another coincident copy set (measured, and the tool says so).
    # The proof is the nested disc having MOVED, read back off its own bounding box.
    ("sketch_move", {"sketch_name": "ArrB",
                     "entities": "line:0,line:1,line:2,line:3,line:4,line:5",
                     "scale_factor": 0.65, "center_x": 300, "center_y": 500},
     lambda p: len(p.get("moved_entities") or []) == 6 and not p.get("unmoved_entities"), None),
    ("model_inspect", lambda c: {"target": _ctx_get(c, "nest_disc", "the nested disc")},
     lambda p: _measured("the nest re-solved off the smaller boundary",
                         {"y_before": _RECALL.get("nest_y0"),
                          "y_now": p.get("center", {}).get("y")},
                         abs(p["center"]["y"] - _RECALL["nest_y0"]) > 1.0), None),
    _dwell(2.0),
    # The envelope forms that need NO sketch: a sized rectangle on a plane, then the 3D packer on a
    # box. Both are judged on the feature's own arrangeStatistics - what the solver placed and what
    # it left out - which the profile nest above cannot show. A sized envelope is anchored on its
    # plane's origin, so both are offset into clear ground rather than onto the vise and the CAM
    # stock, and each nest is deleted again: these rows are about the solver, not about the field.
    # The sized rectangular sheet passes rotation and quantity, and one of those is the
    # extension's (measured on the lapsed install: "Cannot set extension value" here while the
    # boundary nest above and the 3D box below, which pass neither, place their shapes), so this
    # row rides the tier and its unentitled variant asserts the refusal names the candidates.
    ("model_arrange", {"shapes": ["ArrP1:1", "ArrP2:1", "ArrP3:1"], "solver": "rectangular",
                       "envelope_plane": "xy", "envelope_length": 300, "envelope_width": 200,
                       "envelope_origin": [600, 400], "rotation": "none", "quantity": 1,
                       "spacing": 5},
     _needs(MACHINING_EXTENSION, _packed(3, extent=(300, 200))),
     ("arr_sheet", lambda p: p["feature"])),
    ("model_arrange", {"shapes": ["ArrP1:1", "ArrP2:1", "ArrP3:1"], "solver": "rectangular",
                       "envelope_plane": "xy", "envelope_length": 300, "envelope_width": 200,
                       "envelope_origin": [600, 400], "rotation": "none", "quantity": 1,
                       "spacing": 5},
     _unless(MACHINING_EXTENSION, _refused("extension-only setting", "rotation")), None),
    ("design_delete_feature",
     lambda c: {"feature": _ctx_get(c, "arr_sheet", "the plane-envelope nest")},
     _needs(MACHINING_EXTENSION, "ok"), None),
    ("model_arrange", {"shapes": ["ArrP1:1", "ArrP2:1", "ArrP3:1"], "solver": "3d",
                       "envelope_plane": "xy", "envelope_length": 200, "envelope_width": 200,
                       "envelope_height": 100, "envelope_origin": [600, 400], "spacing": 5},
     _packed(3, extent=(200, 200, 100)), ("arr_box", lambda p: p["feature"])),
    ("design_delete_feature",
     lambda c: {"feature": _ctx_get(c, "arr_box", "the 3D nest")}, "ok", None),
    _dwell(2.0),
]


# The asymmetric occurrence-qualified mesh fixture. Its authored box is moved by the layout pass
# before the occurrence transform is applied, so every expected world bound reads through _px/_py.
_MESH_POSE = "MeshPose"
_MESH_POSE_AUTHORED = (180.0, 180.0)
_MESH_POSE_MOVE = (100.0, 40.0, 30.0)
_MESH_POSE_TOL = 0.01


def _mesh_pose_transform(bounds):
    """Bounds after Rz(90) and the fixture translation."""
    mn, mx = bounds
    points = [(x, y, z) for x in (mn[0], mx[0])
              for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]
    moved = [(100.0 - y, 40.0 + x, 30.0 + z) for x, y, z in points]
    return (tuple(min(p[i] for p in moved) for i in range(3)),
            tuple(max(p[i] for p in moved) for i in range(3)))


def _mesh_pose_bounds():
    """The fixture's identity, once-transformed and double-transformed world bounds."""
    x0, y0 = _MESH_POSE_AUTHORED
    identity = ((_px(_MESH_POSE, x0), _py(_MESH_POSE, y0), 0.0),
                (_px(_MESH_POSE, x0 + 20.0), _py(_MESH_POSE, y0 + 10.0), 5.0))
    once = _mesh_pose_transform(identity)
    return identity, once, _mesh_pose_transform(once)


def _mesh_pose_points(payload):
    """The target's min/max points, normalized across BRep and mesh inspect payloads."""
    box = payload.get("bbox") if payload.get("kind") == "mesh" else payload
    box = box or {}
    out = []
    for key in ("min_point", "max_point"):
        point = box.get(key) or {}
        out.append(tuple(point.get(axis) for axis in ("x", "y", "z")))
    return tuple(out)


def _mesh_pose_bounds_match(got, expected):
    """Whether two min/max triples agree within the mesh fixture tolerance."""
    return all(_num(a) and abs(a - b) <= _MESH_POSE_TOL
               for ga, ea in zip(got, expected) for a, b in zip(ga, ea))


def _mesh_pose_body_signature(payload):
    """The source-body values that must hold across mesh conversion."""
    mass = payload.get("mass") or {}
    return (_mesh_pose_points(payload), mass.get("volume"), mass.get("area"))


def _mesh_pose_mesh_signature(payload):
    """The mesh values that must hold across occurrence and instance reads."""
    return (_mesh_pose_points(payload), payload.get("volume"), payload.get("area"),
            payload.get("triangle_count"), payload.get("node_count"),
            payload.get("is_closed"), payload.get("is_oriented"))


def _mesh_pose_body(expected, same_as=None):
    """A source-body oracle requiring exact bounds and independently read physical properties."""
    def check(p):
        mass = p.get("mass") or {}
        got = _mesh_pose_points(p)
        state = _mesh_pose_body_signature(p)
        good = (p.get("kind") == "body" and p.get("units") == "mm"
                and _mesh_pose_bounds_match(got, expected)
                and _num(mass.get("volume")) and abs(mass["volume"] - 1000.0) <= 0.01
                and _num(mass.get("area")) and abs(mass["area"] - 700.0) <= 0.01)
        if same_as:
            good = good and state == _RECALL.get(same_as)
        return _measured("MeshPose source volume, area and world bounds",
                         {"state": state, "expected_bounds": expected,
                          "same_as": same_as and _RECALL.get(same_as)}, good)
    return check


def _mesh_pose_mesh(expected, same_as=None, reject_bounds=None):
    """A mesh oracle requiring placement, closure, orientation and scalar geometry."""
    def check(p):
        got = _mesh_pose_points(p)
        state = _mesh_pose_mesh_signature(p)
        good = (p.get("kind") == "mesh" and p.get("units") == "mm"
                and _mesh_pose_bounds_match(got, expected)
                and (reject_bounds is None or not _mesh_pose_bounds_match(got, reject_bounds))
                and _num(p.get("volume")) and abs(p["volume"] - 1000.0) <= 0.01
                and _num(p.get("area")) and abs(p["area"] - 700.0) <= 0.01
                and p.get("triangle_count") == 12 and _num(p.get("node_count"))
                and p["node_count"] > 0 and p.get("is_closed") is True
                and p.get("is_oriented") is True)
        if same_as:
            good = good and state == _RECALL.get(same_as)
        return _measured("MeshPose mesh placement and geometry",
                         {"state": state, "expected_bounds": expected,
                          "rejected_bounds": reject_bounds,
                          "same_as": same_as and _RECALL.get(same_as)}, good)
    return check


def _mesh_pose_pose_signature(payload):
    """Exact pose rows for the minted MeshPose occurrence paths."""
    wanted = [p for p in (_RECALL.get("mesh_pose_first"), _RECALL.get("mesh_pose_second")) if p]
    rows = {r.get("name"): r for r in (payload.get("occurrences") or [])}
    keys = ("component", "origin", "x_axis", "y_axis", "z_axis")
    return {name: {key: rows.get(name, {}).get(key) for key in keys} for name in wanted}


def _mesh_pose_poses(first_moved=True, same_as=None):
    """A complete occurrence census with exact independent shared-instance placements."""
    def check(p):
        first = _RECALL.get("mesh_pose_first")
        other = _RECALL.get("mesh_pose_second")
        rows = {r.get("name"): r for r in (p.get("occurrences") or [])}
        expected = {
            first: ({"origin": [100.0, 40.0, 30.0],
                     "x_axis": [0.0, 1.0, 0.0], "y_axis": [-1.0, 0.0, 0.0],
                     "z_axis": [0.0, 0.0, 1.0]} if first_moved else
                    {"origin": [0.0, 0.0, 0.0],
                     "x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0],
                     "z_axis": [0.0, 0.0, 1.0]}),
        }
        if other:
            expected[other] = {"origin": [0.0, 0.0, 0.0],
                               "x_axis": [1.0, 0.0, 0.0], "y_axis": [0.0, 1.0, 0.0],
                               "z_axis": [0.0, 0.0, 1.0]}
        signature = _mesh_pose_pose_signature(p)
        good = (first is not None and p.get("occurrences_truncated") is False
                and p.get("occurrence_count") == len(p.get("occurrences") or []))
        for name, pose in expected.items():
            row = rows.get(name) or {}
            good = (good and row.get("component") == _MESH_POSE
                    and all(row.get(key) == value for key, value in pose.items()))
        if same_as:
            good = good and signature == _RECALL.get(same_as)
        return _measured("MeshPose complete occurrence census and exact poses",
                         {"signature": signature, "expected": expected,
                          "count": p.get("occurrence_count"),
                          "returned": len(p.get("occurrences") or []),
                          "truncated": p.get("occurrences_truncated")}, good)
    return check


def _mesh_pose_census(p):
    """The complete two-mesh component census with both minted names."""
    rows = p.get("meshes") or []
    wanted = {_RECALL.get("mesh_pose_m0"), _RECALL.get("mesh_pose_m1")}
    got = {row.get("name") for row in rows}
    good = (p.get("truncated") is False and p.get("count") == len(rows) == 2
            and got == wanted and all(row.get("triangle_count") == 12 for row in rows))
    return _measured("MeshPose complete mesh census",
                     {"count": p.get("count"), "returned": len(rows),
                      "truncated": p.get("truncated"), "names": sorted(got)}, good)


# ACT 7: MESH - a scratch solid becomes a mesh, then the mesh family works it (one mesh per op).
_MESH = [
    # A fresh asymmetric 20 x 10 x 5 mm source: identity conversion is the control, then the
    # occurrence moves through the reproduced Rz90 + [100,40,30] pose before a qualified conversion.
    ("model_create_component", {"name": _MESH_POSE, "activate": True}, _made_component,
     ("mesh_pose_first", _recall("mesh_pose_first", lambda p: p["full_path"]))),
    ("sketch_create", {"plane": "xy", "name": _MESH_POSE + "S"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle",
                                           "x1": _MESH_POSE_AUTHORED[0],
                                           "y1": _MESH_POSE_AUTHORED[1],
                                           "x2": _MESH_POSE_AUTHORED[0] + 20.0,
                                           "y2": _MESH_POSE_AUTHORED[1] + 10.0}],
                             "sketch_name": _MESH_POSE + "S"}, "ok", None),
    ("model_extrude", {"sketch_name": _MESH_POSE + "S", "profile_index": 0, "distance": 5},
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", lambda c: {"target": _ctx_get(c, "mesh_pose_first", "first occurrence"),
                                 "kind": "planar_face", "max_results": 1},
     "ok", _fg("mesh_pose_body")),
    ("model_inspect", lambda c: {
        "target": _ctx_get(c, "mesh_pose_first", "first occurrence") + ":Body1",
        "include": ["default", "mass"], "accuracy": "very_high", "units": "mm"},
     lambda p: _mesh_pose_body(_mesh_pose_bounds()[0])(p),
     ("mesh_pose_source_identity",
      _recall("mesh_pose_source_identity", _mesh_pose_body_signature))),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "mesh_pose_body", "source body"),
                                "name": "MeshPoseM0", "quality": "low"},
     lambda p: _measured("MeshPose identity conversion",
                         {"name": p.get("name"), "component": p.get("component"),
                          "source_body": p.get("source_body"),
                          "triangle_count": p.get("triangle_count")},
                         p.get("component") == _MESH_POSE and p.get("source_body") == "Body1"
                         and p.get("name") == "MeshPoseM0"
                         and p.get("triangle_count") == 12),
     ("mesh_pose_m0", _recall("mesh_pose_m0", lambda p: p["name"]))),
    ("model_inspect", lambda c: {
        "target": (_ctx_get(c, "mesh_pose_first", "first occurrence") + ":"
                   + _ctx_get(c, "mesh_pose_m0", "identity mesh")),
        "units": "mm"}, lambda p: _mesh_pose_mesh(_mesh_pose_bounds()[0])(p),
     ("mesh_pose_m0_identity",
      _recall("mesh_pose_m0_identity", _mesh_pose_mesh_signature))),
    # Create the shared placement while the first is still at identity. Fusion then keeps the
    # second at identity when only the first occurrence is released and moved.
    ("design_add_instance", lambda c: {
        "component": _ctx_get(c, "mesh_pose_first", "first occurrence")},
     lambda p: p.get("created") is True and p.get("component") == _MESH_POSE,
     ("mesh_pose_second", _recall("mesh_pose_second", lambda p: p["full_path"]))),
    ("assembly_get", {"include": ["poses"], "max_occurrences": 200, "units": "mm"},
     _mesh_pose_poses(first_moved=False), None),
    ("assembly_ground", lambda c: {
        "occurrence": _ctx_get(c, "mesh_pose_first", "first occurrence"),
        "ground_to_parent": False},
     lambda p: p.get("isGroundToParent") is False, None),
    ("assembly_move", lambda c: {
        "occurrence": _ctx_get(c, "mesh_pose_first", "first occurrence"),
        "rotate_deg": 90, "rotate_axis": "z", "units": "mm"},
     lambda p: p.get("moved") is True and p.get("rotate_deg") == 90.0
     and p.get("rotate_axis") == "z", None),
    ("assembly_move", lambda c: {
        "occurrence": _ctx_get(c, "mesh_pose_first", "first occurrence"),
        "dx": _MESH_POSE_MOVE[0], "dy": _MESH_POSE_MOVE[1], "dz": _MESH_POSE_MOVE[2],
        "units": "mm"},
     lambda p: _measured("MeshPose translated occurrence pose",
                         {"position": p.get("position"), "translation": p.get("translation")},
                         p.get("moved") is True
                         and p.get("position") == {"x": 100.0, "y": 40.0, "z": 30.0}),
     None),
    ("assembly_capture_position", {"action": "capture"}, _captured, None),
    ("assembly_get", {"include": ["poses"], "max_occurrences": 200, "units": "mm"},
     _mesh_pose_poses(),
     ("mesh_pose_pose_before",
      _recall("mesh_pose_pose_before", _mesh_pose_pose_signature))),
    ("model_inspect", lambda c: {
        "target": _ctx_get(c, "mesh_pose_first", "first occurrence") + ":Body1",
        "include": ["default", "mass"], "accuracy": "very_high", "units": "mm"},
     lambda p: _mesh_pose_body(_mesh_pose_bounds()[1])(p),
     ("mesh_pose_source_moved",
      _recall("mesh_pose_source_moved", _mesh_pose_body_signature))),
    ("model_inspect", lambda c: {
        "target": (_ctx_get(c, "mesh_pose_first", "first occurrence") + ":"
                   + _ctx_get(c, "mesh_pose_m0", "identity mesh")),
        "units": "mm"}, lambda p: _mesh_pose_mesh(_mesh_pose_bounds()[1])(p),
     ("mesh_pose_m0_moved",
      _recall("mesh_pose_m0_moved", _mesh_pose_mesh_signature))),
    ("save_as_mesh", lambda c: {
        "body": _ctx_get(c, "mesh_pose_first", "first occurrence") + ":Body1",
        "name": "MeshPoseM1", "quality": "low"},
     lambda p: _measured("MeshPose occurrence-qualified conversion",
                         {"name": p.get("name"), "component": p.get("component"),
                          "source_body": p.get("source_body"),
                          "triangle_count": p.get("triangle_count")},
                         p.get("component") == _MESH_POSE and p.get("source_body") == "Body1"
                         and p.get("name") == "MeshPoseM1"
                         and p.get("triangle_count") == 12),
     ("mesh_pose_m1", _recall("mesh_pose_m1", lambda p: p["name"]))),
    ("model_inspect", lambda c: {
        "target": (_ctx_get(c, "mesh_pose_first", "first occurrence") + ":"
                   + _ctx_get(c, "mesh_pose_m1", "qualified mesh")),
        "units": "mm"},
     lambda p: _mesh_pose_mesh(_mesh_pose_bounds()[1],
                               reject_bounds=_mesh_pose_bounds()[2])(p), None),
    ("mesh_get", lambda c: {
        "target": _ctx_get(c, "mesh_pose_first", "first occurrence"),
        "max_results": 10, "units": "mm"}, _mesh_pose_census, None),
    # Fresh independent reads prove conversion did not move the source, M0 or the captured pose.
    ("model_inspect", lambda c: {
        "target": _ctx_get(c, "mesh_pose_first", "first occurrence") + ":Body1",
        "include": ["default", "mass"], "accuracy": "very_high", "units": "mm"},
     lambda p: _mesh_pose_body(_mesh_pose_bounds()[1], "mesh_pose_source_moved")(p), None),
    ("model_inspect", lambda c: {
        "target": (_ctx_get(c, "mesh_pose_first", "first occurrence") + ":"
                   + _ctx_get(c, "mesh_pose_m0", "identity mesh")),
        "units": "mm"},
     lambda p: _mesh_pose_mesh(_mesh_pose_bounds()[1], "mesh_pose_m0_moved")(p), None),
    ("assembly_get", {"include": ["poses"], "max_occurrences": 200, "units": "mm"},
     _mesh_pose_poses(same_as="mesh_pose_pose_before"), None),
    # Mesh bodies belong to the component: the independent second placement sees the same M1 at
    # identity, while the first placement remains at its captured rotated and translated pose.
    ("model_inspect", lambda c: {
        "target": _ctx_get(c, "mesh_pose_second", "second occurrence") + ":Body1",
        "include": ["default", "mass"], "accuracy": "very_high", "units": "mm"},
     lambda p: _mesh_pose_body(_mesh_pose_bounds()[0], "mesh_pose_source_identity")(p), None),
    ("model_inspect", lambda c: {
        "target": (_ctx_get(c, "mesh_pose_second", "second occurrence") + ":"
                   + _ctx_get(c, "mesh_pose_m1", "qualified mesh")),
        "units": "mm"}, lambda p: _mesh_pose_mesh(_mesh_pose_bounds()[0])(p), None),
    ("model_inspect", lambda c: {
        "target": _ctx_get(c, "mesh_pose_first", "first occurrence") + ":Body1",
        "include": ["default", "mass"], "accuracy": "very_high", "units": "mm"},
     lambda p: _mesh_pose_body(_mesh_pose_bounds()[1], "mesh_pose_source_moved")(p), None),
    ("model_inspect", lambda c: {
        "target": (_ctx_get(c, "mesh_pose_first", "first occurrence") + ":"
                   + _ctx_get(c, "mesh_pose_m1", "qualified mesh")),
        "units": "mm"},
     lambda p: _mesh_pose_mesh(_mesh_pose_bounds()[1],
                               reject_bounds=_mesh_pose_bounds()[2])(p), None),
    ("assembly_get", {"include": ["poses"], "max_occurrences": 200, "units": "mm"},
     _mesh_pose_poses(same_as="mesh_pose_pose_before"), None),
    ("model_create_component", {"name": "Msh", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "MshS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 200, "y1": 300,
                                           "x2": 220, "y2": 320}],
                             "sketch_name": "MshS"}, "ok", None),
    # FRAME BEFORE THE FIRST BODY, on the sketch that is about to become one. The automatic camera
    # row lands on a chunk's first body, which means the body appears while the camera is still on
    # whatever the previous act was doing - and this act's sketch was drawn back in the sketch phase,
    # so nothing has brought the camera here since. Framing the sketch first is what makes the mesh
    # source appear IN shot instead of somewhere off screen.
    _watch("MshS"),
    ("model_extrude", {"sketch_name": "MshS", "profile_index": 0, "distance": 10}, _extruded, None),
    # THE ONE FRAME THE WHOLE FAMILY PLAYS IN. Every mesh below is cast from a body inside Msh, and
    # none of those steps makes a sketch or a component, so the framing pass adds no row of its own
    # and nothing moves the camera off this shot.
    _watch("Msh:1"),
    ("find_geometry", {"target": "Msh", "kind": "planar_face", "nearest_to": [210, 310, 10], "max_results": 1}, "ok", _fg("msh_body")),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 5, "name": "MshMid"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "xy", "name": "MshCyl"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "circle", "cx": 260, "cy": 360,
                                           "radius": 15}],
                             "sketch_name": "MshCyl"}, "ok", None),
    ("model_extrude", {"sketch_name": "MshCyl", "profile_index": 0, "distance": 20}, _extruded, None),
    ("find_geometry", {"target": "Msh", "kind": "cylinder_face", "nearest_to": [260, 360, 10], "max_results": 1}, "ok", _fg("cyl_body")),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "cyl_body", "cyl body"), "name": "MRED", "quality": "high"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MA", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MC", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MD", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "ME", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MF", "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MOPEN", "quality": "low"}, "ok", None),
    ("mesh_get", {"target": "Msh"}, "ok", None),
    # MA is a box cast to mesh, measured to segment 1 -> 6 groups under 'fast', so THIS row's
    # generation must move the count - a payload reporting no movement here is a generation that
    # did nothing. (The tool passes no verdict of its own: one flat region segments into one group.)
    ("mesh_generate_face_groups", {"mesh": "MA", "method": "fast"},
     lambda p: (p["generated"] is True and p["changed"] is True
                and p["face_group_count"] > p["face_group_count_before"]), None),
    # the converted bodies are the component's BRep census differenced across the add; a row with no
    # handle is a body the next tool cannot address, and 'design_mode' is read before the scope opens.
    ("mesh_to_brep", {"mesh": "MA", "method": "faceted", "operation": "base_feature"},
     lambda p: (bool(p["brep_bodies"]) and all(b["handle"] for b in p["brep_bodies"])
                and p["design_mode"] == "parametric"), None),
    # THE OPEN HALF: 'none' leaves the cut face unfilled, so the trimmed mesh is not watertight.
    ("mesh_plane_cut", {"mesh": "MOPEN", "plane": "MshMid", "cut_type": "trim", "fill": "none"},
     lambda p: p.get("fill") == "none" and p.get("triangles_before") and p.get("triangles_after")
     and (p["triangles_after"] != p["triangles_before"]
          or p.get("volume_after_cm3") != p.get("volume_before_cm3")), None),
    # the independent read that says WHY the convert below lands a surface rather than a solid.
    ("mesh_get", {"target": "Msh"},
     lambda p: _measured("the trimmed half reads open",
                         {"rows": [[m.get("name"), m.get("is_closed")] for m in p["meshes"]]},
                         any(m.get("name") == "MOPEN" and m.get("is_closed") is False
                             for m in p["meshes"])), None),
    # prismatic on an open mesh is unmeasured, so it still refuses - naming the repair that closes a
    # hole, which is what makes the refusal actionable. It runs first: it mutates nothing.
    ("mesh_to_brep", {"mesh": "MOPEN", "method": "prismatic"},
     _refused("NOT watertight", "mesh_repair(repair_type='close_holes')"), None),
    # MEASURED: the faceted convert of an OPEN mesh builds a SURFACE body - the watertight guard
    # refuses only the two methods that were never measured on one. is_solid and face_count are read
    # off the produced body, so a solid here is the wrong body published under the right count.
    ("mesh_to_brep", {"mesh": "MOPEN", "method": "faceted", "operation": "base_feature"},
     lambda p: _measured("faceted convert of an open mesh",
                         {"brep_bodies": p.get("brep_bodies"), "note": p.get("note")},
                         bool(p["brep_bodies"])
                         and all(b["is_solid"] is False and (b["face_count"] or 0) > 0
                                 for b in p["brep_bodies"])
                         and "SURFACE" in (p.get("note") or "")), None),
    # both triangle counts are read off the model; 'reduced_pct' is published only where both read,
    # so an after-count that would not read is caught here rather than passing as a reduce.
    ("mesh_reduce", {"mesh": "MRED", "target": "proportion", "value": 50},
     lambda p: (p["after"]["triangle_count"] < p["before"]["triangle_count"]
                and (p.get("reduced_pct") or 0) > 0), None),
    # 'density' is set-then-read-back off the input (a build that drops it refuses), and the
    # before/after triangle counts are read off the model - 'changed' is null when either count
    # could not be read at all, which is a remesh nothing was measured about.
    ("mesh_remesh", {"mesh": "MC", "density": 1},
     lambda p: p.get("density_applied") == 1 and (p["after"]["triangle_count"] or 0) > 0
     and p.get("changed") is not None, None),
    # the cut's own effect evidence, mirrored into the receipt: the triangle count moved, or (a
    # fill that replaces as many triangles as it removed) the mesh's area/volume did.
    ("mesh_plane_cut", {"mesh": "MD", "plane": "MshMid", "cut_type": "trim"},
     lambda p: p.get("fill") == "minimal" and p.get("triangles_before") and p.get("triangles_after")
     and (p["triangles_after"] != p["triangles_before"]
          or p.get("volume_after_cm3") != p.get("volume_before_cm3")), None),
    # a parametric mesh write reports the MODE it ran in and the base feature it opened to run
    # there: the scope is what a parametric design requires, and the payload names both.
    ("mesh_combine", {"target": "ME", "tools": ["MF"], "operation": "join"},
     lambda p: p.get("design_mode") == "parametric" and bool(p.get("base_feature")), None),
    # a pristine scratch mesh deleted with the survivor check: the payload's own claim is the
    # re-scan. A mesh another feature already transformed (e.g. the plane-cut MD) carries a
    # different lineage; this beat exercises the plain-delete contract.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MDEL",
                                "quality": "low"}, "ok", None),
    # 'deleted_via' names the branch the design's own mode chose - a parametric delete leaves an
    # undoable MeshRemoveFeature - and 'remaining_meshes' is the design-wide re-scan behind it.
    ("mesh_delete", {"mesh": "MDEL"},
     lambda p: (p["deleted"] == "MDEL" and p["deleted_via"] == "meshRemoveFeatures"
                and isinstance(p["remaining_meshes"], int)), None),
    # execute() answers true while writing nothing, so the file's own size is the read-back - and
    # it is the same file the mesh_insert beat below re-imports.
    ("mesh_export", {"target": "MA", "file_path": EXPORT_DIR + "/eval_mesh", "format": "stl"},
     lambda p: (p.get("size_bytes") or 0) > 0 and p.get("file_exists") is True, None),
    # THE RE-IMPORT GETS ITS OWN COMPONENT, and the mesh act goes back to Msh afterwards.
    # mesh_insert does NOT land the mesh where the file's own geometry sits: measured on the live
    # document, a mesh exported from (770,875) came back at (30,34) - near the world origin, a metre
    # from every other body in Msh. Inside Msh that one stray body stretched the component's
    # bounding box to 815 x 916 mm, so every camera row framing 'Msh:1' fitted THAT instead of the
    # 75 mm of mesh work, and the whole mesh act was watched from the far zoom.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "MshIn", "activate": True}, _made_component, None),
    # units='mm' because that is what the FILE holds: the mesh_export beat above named no unit, and
    # mesh_export's stl_units default is mm. Stated rather than left to mesh_insert's own default,
    # so the round trip below pins mesh_export's default alone - reading it back at mesh_insert's
    # default too would pass on any pair of defaults that happen to match. Importing the file
    # at the wrong unit divides every coordinate by 25.4, and a mesh a fortieth of its size lands a
    # fortieth of its distance from the origin too - measured, near the world origin and inside
    # whatever is parked there, not out on the field where it was exported from. The size read-back
    # below is what makes that a failure instead of a surprise.
    # the row is the imported body read back: the name it answers to (a dedupe leaves the model_inspect
    # below naming a mesh that is not this one) and a triangle count off the mesh itself.
    # target_component is the OCCURRENCE PATH 'MshIn:1', not a bare component name - the typed
    # occurrence kind this input resolves through accepts that spelling every other tool does.
    ("mesh_insert", {"file_path": EXPORT_DIR + "/eval_mesh.stl", "name": "MshIns",
                     "units": "mm", "target_component": "MshIn:1"},
     lambda p: (len(p["bodies"]) == 1 and p["bodies"][0]["name"] == "MshIns"
                and (p["bodies"][0]["triangle_count"] or 0) > 0
                and p.get("name_applied") is True
                and "rename_warning" not in p), None),
    # the round trip measured END TO END: the re-imported mesh is the size of the mesh that was
    # written - measured 74.99 x 74.99 x 20.0 on a finished run. A size, not a position: the layout
    # moves the bench, and 25.4 is the only thing this is looking for.
    ("model_inspect", {"target": "MshIns"}, _mesh_round_trip(75.0, 20.0), None),
    ("design_activate_component", {"occurrence": "Msh:1"}, "ok", None),
    # repair on a HEALTHY mesh: nothing of that kind to fix is an honest success, not a failure, and
    # the payload must say so rather than claim a repair. Then a rebuild, whose density is read back
    # off the feature's own parameter - the request is never echoed.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MFIX",
                                "quality": "low"}, "ok", None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "one_touch_fix"},
     lambda p: p.get("repaired") is True and p.get("watertight") is True, None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "fast",
                     "density": 32},
     lambda p: p.get("density") == 32.0 and "density_unverified" not in p, None),
    # the rest of the rebuild vocabulary on the same mesh - each method re-triangulates it a
    # different way, and each row reads its own density back off the feature's ModelParameter rather
    # than echoing the request, so a method that quietly fell back to another is visible.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild",
                     "rebuild_method": "preserve_sharp_edges", "density": 40},
     _rebuilt("preserve_sharp_edges", 40.0), None),
    # 'offset' is accepted by the accurate method ALONE - it is the deviation that method solves to.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "accurate",
                     "density": 48, "offset": 0.2}, _rebuilt("accurate", 48.0), None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "blocky",
                     "density": 24}, _rebuilt("blocky", 24.0), None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "adaptive",
                     "density": 32}, _rebuilt("adaptive", 32.0), None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild",
                     "rebuild_method": "adaptive_preserve_sharp_edges", "density": 32},
     _rebuilt("adaptive_preserve_sharp_edges", 32.0), None),
    # the offset/method pairing, refused rather than dropped, on the method it does not belong to.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "rebuild", "rebuild_method": "blocky",
                     "density": 24, "offset": 0.2}, "refused", None),
    # 'wrap' shrink-wraps the mesh closed. It is not a rebuild, so it takes none of the rebuild
    # knobs - handing it one is refused by name.
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "wrap"},
     lambda p: p.get("repaired") is True and p.get("repair_type") == "wrap", None),
    ("mesh_repair", {"mesh": "MFIX", "repair_type": "close_holes", "density": 32}, "refused", None),
    # A repair that finds nothing of its kind is an honest success, and the payload has to say so
    # rather than claim a repair - 'changed' empty is that statement. A mesh straight out of
    # save_as_mesh is NOT that fixture: the first stitch_and_remove on it measurably moves the
    # counts (it has duplicate vertices to weld), so the no-op case is the SECOND call, once the
    # first has done the welding. That also makes the beat an idempotence check.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSTITCH",
                                "quality": "low"}, "ok", None),
    ("mesh_repair", {"mesh": "MSTITCH", "repair_type": "stitch_and_remove"},
     lambda p: p.get("repaired") is True, None),
    ("mesh_repair", {"mesh": "MSTITCH", "repair_type": "stitch_and_remove"}, _repair_no_op, None),
    # mesh_shell hollows the SAME body in place and re-triangulates it: the payload's before/after
    # counts and the volume DROP are the verdict, and the thickness is read off the feature.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSHL",
                                "quality": "low"}, "ok", None),
    # 'hollowed' needs BOTH a volume drop and a body that still reads watertight - a shell that lost
    # the closure reports the same drop for the opposite reason, so the flag pair is the verdict.
    ("mesh_shell", {"mesh": "MSHL", "thickness": 2, "units": "mm"},
     lambda p: p.get("hollowed") is True and abs(p.get("thickness", 0) - 2.0) < 1e-6
     and p.get("watertight") is True
     and p.get("volume_change", 0) < 0 and "volume" in (p.get("changed") or []), None),
    ("mesh_shell", {"mesh": "MSHL", "thickness": -2}, "refused", None),
    # a thickness thicker than half the thinnest wall does NOT quietly cut through: the platform
    # refuses the shell outright ([F50] - measured on a closed cube), and the tool hands that
    # compute failure on by name instead of reporting a hollow that never happened. The box is
    # 10 mm through its thinnest axis, so 6 mm is past the half-wall.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSHL2",
                                "quality": "low"}, "ok", None),
    ("mesh_shell", {"mesh": "MSHL2", "thickness": 6, "units": "mm"},
     _refused("MESH_FAILED_HOLLOW"), None),
    # mesh_smooth: the node coordinates move. nodes_moved > 0 is the gate - the counts holding
    # still is measured on a 12-triangle box only, so it is NOT asserted here.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "cyl_body", "cyl body"), "name": "MSMO",
                                "quality": "high"}, "ok", None),
    ("mesh_smooth", {"mesh": "MSMO", "smoothness": 0.05},
     lambda p: p.get("nodes_moved", 0) > 0 and abs(p.get("smoothness", 0) - 0.05) < 1e-6, None),
    ("mesh_smooth", {"mesh": "MSMO", "smoothness": 1.5}, "refused", None),
    # A 'merge' combine of two DISJOINT meshes yields ONE body holding TWO shells, which the
    # separate then takes apart: measured 24 triangles in, two 12-triangle pieces out.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MSEPA",
                                "quality": "low"}, "ok", None),
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "cyl_body", "cyl body"), "name": "MSEPB",
                                "quality": "low"}, "ok", None),
    ("mesh_combine", {"target": "MSEPA", "tools": ["MSEPB"], "operation": "merge"}, "ok", None),
    ("mesh_separate", {"mesh": "MSEPA"},
     lambda p: p.get("piece_count", 0) >= 2 and len(p.get("pieces") or []) >= 2, None),
    # mesh_reverse_normal: the signed volume changes sign; is_closed / is_oriented do not move.
    ("save_as_mesh", lambda c: {"body": _ctx_get(c, "msh_body", "box body"), "name": "MREV",
                                "quality": "low"}, "ok", None),
    ("mesh_reverse_normal", {"mesh": "MREV"},
     lambda p: p.get("reversed") is True
     and (p.get("volume_sign_flipped") is True or p.get("normals_negated") is True), None),
    # MeshBody.name IS settable ([F37]): the rename lands on the mesh kind, and a FRESH fetch of the
    # component's meshes - not the wrapper the write held - is what proves it stuck.
    ("design_set_name", {"target": "MREV", "new_name": "MeshRenamed"},
     lambda p: p.get("kind") == "mesh" and p.get("name") == "MeshRenamed"
     and p.get("previous_name") == "MREV", None),
    ("mesh_get", {"target": "Msh", "max_results": 100},
     lambda p: any(m.get("name") == "MeshRenamed" for m in (p.get("meshes") or [])), None),
    # THE QUALIFIED ADDRESS, on a component this document places ONCE. Fusion names the first body
    # of every component 'Body1', so the bare name reaches several of them and is refused with the
    # candidate list; the '<occurrence>:<body>' spelling that refusal offers picks Msh's own.
    ("save_as_mesh", {"body": "Body1", "name": "MQUAL", "quality": "low"},
     _refused("is ambiguous - it names",
              "qualified '<occurrence-or-component>:<body>' names"), None),
    ("save_as_mesh", {"body": "Msh:1:Body1", "name": "MQUAL", "quality": "low"},
     lambda p: _measured("the qualified source address reached Msh's own body",
                         {"component": p.get("component"), "source_body": p.get("source_body"),
                          "name": p.get("name"), "triangle_count": p.get("triangle_count")},
                         p.get("component") == "Msh" and p.get("source_body") == "Body1"
                         and p.get("name") == "MQUAL" and _num(p.get("triangle_count"))
                         and p["triangle_count"] > 0), None),
    # ...and the MESH under the same spelling. A mesh belongs to the COMPONENT rather than to a
    # placement, so the address is answered through the placed component - and the facts read back
    # are the mesh's own, not the source solid's.
    ("model_inspect", {"target": "Msh:1:MQUAL"},
     lambda p: _measured("the mesh's own facts under its '<occurrence>:<mesh>' address",
                         {"kind": p.get("kind"), "name": p.get("name"),
                          "triangle_count": p.get("triangle_count"), "volume": p.get("volume"),
                          "is_closed": p.get("is_closed")},
                         p.get("kind") == "mesh" and p.get("name") == "MQUAL"
                         and _num(p.get("triangle_count")) and p["triangle_count"] > 0), None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
]
