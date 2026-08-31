# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the jointed mechanism, the joint bench, and the vise fixture.

The gimbal axes and the crank->rotor link, driven live; the bench that gives every joint motion a
station of its own; and the self-centering vise built around the machinable part, whose grip is
proven by measurement rather than by the joint calls returning ok.
"""

from verify_core import (
    _as_built, _box, _captured, _constrained, _ctx_get, _datum_plane, _driven_angle,
    _driven_slide, _extruded, _fg, _grounded, _interference_measured, _joint_bench, _joint_is,
    _joint_limits, _joint_origin_at, _jointed, _jointed_at_geometry, _joints_listed,
    _made_component, _measured, _motion_linked, _moved_occurrence, _near, _refused, _revolved,
    _rigid_grouped, _watch)


# --- ACT 3: MOTION - four gimbal axes + a crank->rotor link, driven live (mirrors scenario S3) -
# Joints on the real gyroscope parts via origin snaps (no teleport). assembly_move/capture/constrain
# pose scratch cameos so the mechanism itself is not disturbed.
_MOTION = [
    ("assembly_ground", {"occurrence": "Frame:1", "ground_to_parent": True}, _grounded, None),
    ("assembly_rigid_group", {"occurrences": ["Frame:1", "Carrier:1"]}, _rigid_grouped(2), None),
    # a coordinate Joint Origin the crank mounts on, and the stock-center JO CAM binds its WCS to.
    ("joint_create_origin", {"anchor": "coordinates", "x": 60, "y": 0, "z": 0, "name": "CrankMount"},
     _joint_origin_at("CrankMount", 60, 0, 0), None),
    ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "StockCenter"},
     _joint_origin_at("StockCenter", 0, 0, 0), None),
    # the four gimbal revolutes, each origin-snapped so parts stay seated.
    ("joint_create", {"occurrence_one": "Carrier:1:origin", "occurrence_two": "Pedestal:1:origin", "joint_type": "revolute", "axis": "z", "name": "Yaw"}, _jointed("Yaw"), None),
    ("joint_create", {"occurrence_one": "OuterRing:1:origin", "occurrence_two": "Carrier:1:origin", "joint_type": "revolute", "axis": "x", "name": "PivotOuter"}, _jointed("PivotOuter"), None),
    # the OTHER ring pivot via a joint origin snap on the inner ring (the second creation path).
    ("joint_create", {"occurrence_one": "InnerRing:1:origin", "occurrence_two": "OuterRing:1:origin", "joint_type": "revolute", "axis": "y", "name": "PivotInner"}, _jointed("PivotInner"), None),
    ("joint_create", {"occurrence_one": "Rotor:1:origin", "occurrence_two": "RotorShaft:1:origin", "joint_type": "revolute", "axis": "x", "name": "Spin"}, _jointed("Spin"), None),
    # the shaft rides in the inner ring as-built (its current seated position).
    ("joint_create_as_built", {"occurrence_one": "RotorShaft:1", "occurrence_two": "InnerRing:1"}, _as_built, None),
    # the crank on its frame mount, then LIMITS on the yaw.
    ("joint_create", {"occurrence_one": "Crank:1:origin", "occurrence_two": "CrankMount", "joint_type": "revolute", "axis": "z", "name": "CrankAxis"}, _jointed("CrankAxis"), None),
    ("joint_edit", {"joint_name": "Yaw", "min_deg": -45, "max_deg": 45},
     _joint_limits("Yaw", min_deg=-45, max_deg=45), None),
    # a SECOND creation path AND a real cylinder-face joint on a scratch pin/bore cameo pair.
    ("model_create_component", {"name": "PinCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PinS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 5, "sketch_name": "PinS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PinS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("model_create_component", {"name": "BoreCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "BoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 8, "sketch_name": "BoreS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 300, "cy": 0, "radius": 5.5, "sketch_name": "BoreS"}, "ok", None),
    ("sketch_get", {"sketch_name": "BoreS"}, "ok", ("bore_ring", lambda p: p["profiles"][-1]["handle"])),
    ("model_extrude", lambda c: {"sketch_name": "BoreS", "profile_index": _ctx_get(c, "bore_ring", "bore ring"), "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("BoreCameo:1"),
    ("find_geometry", {"target": "PinCameo", "kind": "cylinder_face", "max_results": 1}, "ok", _fg("pin_cyl")),
    ("find_geometry", {"target": "BoreCameo", "kind": "cylinder_face", "radius": 5.5, "max_results": 1}, "ok", _fg("bore_cyl")),
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "pin_cyl", "pin face"), "handle_two": _ctx_get(c, "bore_cyl", "bore face"), "motion": "revolute"}, _jointed_at_geometry, None),
    # THE MOTION VOCABULARY at the geometry seam - a ball on a real SPHERE face, an explicit frame
    # axis, and rigid. Each beat takes its own free cameo pair, chained as a TREE (sphere - post -
    # post), so no beat closes a loop on another's joint. The sphere is built through the surface
    # family the same way the fill cameo builds one: a half-disc arc revolved into a closed sheet and
    # sealed solid, which is what gives this beat a genuine SphereSurfaceType face to joint at (a
    # sphere face takes ONLY CenterKeyPoint - the rule inside _joints.build_joint_geometry).
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "BallSphere", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "BallProf"}, "ok", None),
    # the arc's endpoints must sit ON the revolve axis (x=0) or the revolved surface is an open tube
    # enclosing nothing; on an xz sketch +Y maps to world -Z, so this sphere sits alone at z=+300.
    ("sketch_add_geometry", {"kind": "arc", "cx": 0, "cy": -300, "x1": 0, "y1": -294,
                             "sweep_deg": 180, "sketch_name": "BallProf"}, "ok", None),
    ("surface_revolve", {"sketch_name": "BallProf", "axis": "z", "angle_deg": 360}, "ok", None),
    ("surface_fill", {"tools": ["BallSphere"], "operation": "new"},
     lambda p: p.get("all_solid") is True, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "BallPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "BallPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 0, "radius": 5,
                             "sketch_name": "BallPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "BallPostS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AxisPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "AxisPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 60, "radius": 5,
                             "sketch_name": "AxisPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AxisPostS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "RigidPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "RigidPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 120, "radius": 5,
                             "sketch_name": "RigidPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "RigidPostS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("BallPost:1"),
    ("find_geometry", {"target": "BallSphere", "kind": "sphere_face", "max_results": 1}, "ok",
     _fg("ball_face")),
    ("find_geometry", {"target": "BallPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("ball_post_cyl")),
    ("find_geometry", {"target": "AxisPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("axis_post_cyl")),
    ("find_geometry", {"target": "RigidPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("rigid_post_cyl")),
    # the ball seats the sphere's CENTRE on the post's own key point, and the label proves which
    # key point the sphere face resolved to. A ball joint reads no axis at all, so 'axis' is null
    # and the note carries NO axis sentence - not the frame-axis caveat, not the derived-axis one.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_face", "the sphere face"),
                                     "handle_two": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "motion": "ball", "name": "BallSeat"},
     lambda p: p.get("jointed") is True and p.get("geometry_one") == "sphere_face@center"
     and p.get("axis") is None
     and "FRAME's" not in (p.get("note") or "") and "world_axis=" not in (p.get("note") or "")
     and "derived the motion axis" not in (p.get("note") or ""), None),
    # an EXPLICIT axis is frame-relative, and the note says so in the words that stop a caller
    # reading it as a world axis - plus the one tool that does set a true world axis.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "handle_two": _ctx_get(c, "axis_post_cyl", "the axis post wall"),
                                     "motion": "revolute", "axis": "y", "name": "FrameAxisSpin"},
     lambda p: "FRAME's y axis, NOT world y" in (p.get("note") or "")
     and "joint_edit(world_axis=" in (p.get("note") or ""), None),
    # an axis outside the Choice is refused by name, listing what the input carries - nothing built.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "handle_two": _ctx_get(c, "axis_post_cyl", "the axis post wall"),
                                     "motion": "revolute", "axis": "diagonal"}, "refused", None),
    # rigid has no motion to aim, so it too publishes a null axis and an axis-free note.
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "ball_post_cyl", "the ball post wall"),
                                     "handle_two": _ctx_get(c, "rigid_post_cyl", "the rigid post wall"),
                                     "motion": "rigid", "name": "PostLock"},
     lambda p: p.get("jointed") is True and p.get("axis") is None
     and "FRAME's" not in (p.get("note") or "")
     and "derived the motion axis" not in (p.get("note") or ""), None),
    # NEW-1: the TORUS keypoint gate. createByNonPlanarFace(torus, CenterKeyPoint) is measured
    # correct on a PARAMETRIC torus and silently WRONG inside a base feature (it hands back the
    # owning component's origin with no error), so the tool compares the keypoint against the
    # torus's own centre in the same world frame. This beat is the parametric side: the joint lands
    # and the payload names the key point it resolved to. (The two base-feature halves need a torus
    # built INSIDE a base feature; no tool on this surface builds one unattended - see STORY.)
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TorusRing", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xz", "name": "TorusProf"}, "ok", None),
    # on an xz sketch +Y maps to world -Z: this circle sits at world (40, 0, 400) and revolving it
    # about z sweeps a torus of major radius 40 centred on the z axis at z=400, alone up there.
    ("sketch_add_geometry", {"kind": "circle", "cx": 40, "cy": -400, "radius": 6,
                             "sketch_name": "TorusProf"}, "ok", None),
    ("model_revolve", {"sketch_name": "TorusProf", "profile_index": 0, "axis": "z",
                       "angle_deg": 360}, _revolved, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TorusPost", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TorusPostS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 1700, "cy": 180, "radius": 5,
                             "sketch_name": "TorusPostS"}, "ok", None),
    ("model_extrude", {"sketch_name": "TorusPostS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", {"target": "TorusRing", "kind": "torus_face", "max_results": 1}, "ok",
     _fg("torus_face")),
    ("find_geometry", {"target": "TorusPost", "kind": "cylinder_face", "max_results": 1}, "ok",
     _fg("torus_post_cyl")),
    ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "torus_face", "the torus face"),
                                     "handle_two": _ctx_get(c, "torus_post_cyl",
                                                            "the torus post wall"),
                                     "motion": "rigid", "name": "TorusSeat"},
     lambda p: p.get("jointed") is True and p.get("geometry_one") == "torus_face@center", None),
    # A NON-RIGID as-built joint, on its own far-grid pair: an as-built joint moves nothing, so the
    # plate is built already seated on the pin's top face (z=20) and jointed where it stands. The
    # anchor is that shared face, reached by the pin's 'top' snap.
    ("model_create_component", {"name": "AsbPin", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "AsbPinS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 800, "y1": 300, "x2": 820, "y2": 320,
                             "sketch_name": "AsbPinS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AsbPinS", "profile_index": 0, "distance": 20}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "AsbPlate", "activate": True}, _made_component, None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 20, "name": "AsbSeat"},
     _datum_plane("xy"), None),
    ("sketch_create", {"plane": "AsbSeat", "name": "AsbPlateS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 790, "y1": 290, "x2": 830, "y2": 330,
                             "sketch_name": "AsbPlateS"}, "ok", None),
    ("model_extrude", {"sketch_name": "AsbPlateS", "profile_index": 0, "distance": 10}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    _watch("AsbPlate:1"),
    # the motion is read back off the CREATED joint, and the resolved anchor is named in the payload.
    # 'name' rides the same create: AsBuiltJoints.createInput/add take no name, so it is applied
    # post-create and READ BACK - 'joint' is what the browser shows, never an echo.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "geometry": "AsbPin:1:top", "joint_type": "revolute", "axis": "z",
                               "name": "AsbNamed"},
     lambda p: p.get("joint_type") == "revolute" and bool(p.get("geometry"))
     and p.get("joint") == "AsbNamed",
     ("asb_joint", lambda p: p["joint"])),
    # an INDEPENDENT read of the same joint: the tool's own read-back is not the only witness.
    ("assembly_get", {},
     lambda p: any(j.get("type") == "revolute"
                   and {j.get("occurrence_one"), j.get("occurrence_two")} == {"AsbPin:1", "AsbPlate:1"}
                   for j in (p.get("joints") or [])), None),
    # the beat the read-backs cannot fake: a motion that reads back but cannot be DRIVEN is no DOF.
    ("joint_drive", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                               "angle_deg": 30},
     lambda p: abs(p.get("value_now", {}).get("angle_deg", 0) - 30) < 0.5, None),
    ("joint_drive", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                               "angle_deg": 0}, _driven_angle(0), None),
    # Fusion refuses a non-rigid as-built joint with a null geometry, so the tool names the missing
    # anchor instead of letting add() raise.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "joint_type": "revolute"}, "refused", None),
    # a rigid as-built joint IGNORES a geometry it is handed, so the pairing is refused rather than
    # accepted and dropped.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "joint_type": "rigid", "geometry": "AsbPin:1:top"}, "refused", None),
    # an AsBuiltJoint exposes NO offset/angle ModelParameter for ANY motion - both parametric-drive
    # refusals name AS-BUILT and route to joint_create instead of the dead-end generic wording.
    ("joint_edit", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                              "offset": 5}, _refused("AS-BUILT", "joint_create"), None),
    ("joint_edit", lambda c: {"joint_name": _ctx_get(c, "asb_joint", "the as-built revolute"),
                              "angle": 30}, _refused("AS-BUILT", "joint_create"), None),
    # a SECOND as-built joint on an already-jointed pair is refused by the platform at add()
    # ("System will be over constrained") - measured; the tool surfaces it, never a false ok.
    ("joint_create_as_built", {"occurrence_one": "AsbPin:1", "occurrence_two": "AsbPlate:1",
                               "joint_type": "rigid"},
     _refused("over constrained"), None),
    # THE JOINT BENCH: one grounded base, a STATION for every motion type spaced along it, and a
    # flag-shaped indicator arm at each. The arm shape is the point - a disc turning about its own
    # axis shows nothing, so every station carries a bar whose far end reads its position at a
    # glance, and the stations are spread along the base so the seven motions stand side by side
    # instead of on top of each other. Each arm is drawn OFF the base and its joint carries it to
    # its station, so the mate itself is visible; the drive pass below then moves the ones that
    # have a degree of freedom, which is the only way a motion type can be told from a label.
    ("model_create_component", {"name": "JointBase", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "JBaseS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 860, "y1": 300, "x2": 1190, "y2": 340,
                             "sketch_name": "JBaseS"}, "ok", None),
    ("model_extrude", {"sketch_name": "JBaseS", "profile_index": 0, "distance": 10},
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("appearance_set", {"target": "JointBase", "color": "#37474F"}, "ok", None),
    # GROUNDED TO PARENT - the base is the fixed frame every station's motion is read against. An
    # arm that moved because the base drifted would read as the joint working.
    ("assembly_ground", {"occurrence": "JointBase:1", "ground_to_parent": True}, _grounded, None),
] + _joint_bench() + [
    # THE RETYPE, on a station that is already visible: one joint walked through every motion the
    # tool carries, each retype witnessed by the design's own joint walk rather than by the writer,
    # which publishes the type it was ASKED for. It ends back on the revolute it started as.
    ("joint_edit", {"joint_name": "JRig", "joint_type": "revolute", "axis": "z"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "revolute"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "slider", "axis": "x"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "slider"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "cylindrical", "axis": "z"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "cylindrical"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "planar", "axis": "z"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "planar"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "ball"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "ball"), None),
    # pin_slot alone takes TWO frame directions - it rotates about one and slides along another, so
    # the pair must differ.
    ("joint_edit", {"joint_name": "JRig", "joint_type": "pin_slot", "axis": "z",
                    "slide_axis": "y"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "pin_slot"), None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "pin_slot", "axis": "y",
                    "slide_axis": "y"}, "refused", None),
    ("joint_edit", {"joint_name": "JRig", "joint_type": "rigid"}, "ok", None),
    ("assembly_get", {}, _joint_is("JRig", "rigid"), None),
    # COUPLE the crank to the rotor spin at ratio 2 - the DOF-fix step - across independent chains.
    ("joint_motion_link", {"joint_one": "CrankAxis", "joint_two": "Spin", "ratio": 2},
     _motion_linked("CrankAxis", "Spin", False), None),
    # every joint built above, counted off the design-wide walk by an independent read.
    ("assembly_get", {}, _joints_listed(10), None),
    # the relations LIFECYCLE on the story's own relations: list, suppress round-trip, re-value the
    # crank link (was_reversed disclosed), the measured set_occurrences refusal, and a delete with
    # the survivor re-list - on a scratch group so the story keeps its Frame/Carrier lock.
    # motion-link auto-names are SESSION-GLOBAL (the story's first link can be 'Motion Link 9'),
    # so both names come from the relations read, never hardcoded.
    ("assembly_get", {"include": ["relations"]},
     lambda p: p.get("relation_counts", {}).get("rigid_groups", 0) >= 1
     and p.get("relation_counts", {}).get("motion_links", 0) >= 1,
     ("rel_names", lambda p: {"rg": p["relations"]["rigid_groups"][0]["name"],
                              "ml": p["relations"]["motion_links"][0]["name"]})),
    ("assembly_edit_relations", lambda c: {"kind": "rigid_group", "name": _ctx_get(c, "rel_names", "relation names")["rg"], "action": "suppress"},
     lambda p: p.get("is_suppressed") is True, None),
    ("assembly_edit_relations", lambda c: {"kind": "rigid_group", "name": _ctx_get(c, "rel_names", "relation names")["rg"], "action": "unsuppress"},
     lambda p: p.get("is_suppressed") is False, None),
    ("assembly_edit_relations", lambda c: {"kind": "motion_link", "name": _ctx_get(c, "rel_names", "relation names")["ml"], "action": "set_values", "ratio": 3},
     lambda p: p.get("ratio") == 3.0 and "was_reversed" in p, None),
    # back to the 2:1 the story drives on, read back the same way the re-value above was.
    ("assembly_edit_relations", lambda c: {"kind": "motion_link", "name": _ctx_get(c, "rel_names", "relation names")["ml"], "action": "set_values", "ratio": 2},
     lambda p: p.get("ratio") == 2.0 and "was_reversed" in p, None),
    # the measured set_occurrences refusal, asserted in the WORDS that make it a fact: the build it
    # was measured on and the platform sentence it would raise. Nothing is written, so the group
    # still holds the two members assembly_rigid_group gave it - read back on the next row.
    ("assembly_edit_relations", lambda c: {"kind": "rigid_group", "name": _ctx_get(c, "rel_names", "relation names")["rg"], "action": "set_occurrences", "occurrences": ["Frame:1"]},
     _refused("2705.0.87", "Cannot be edited before rolling back"), None),
    # the same group the refusal named (rel_names read it from this same first row) still counts the
    # two occurrences assembly_rigid_group built it from - the refusal wrote nothing.
    ("assembly_get", {"include": ["relations"]},
     lambda p: p["relations"]["rigid_groups"][0]["occurrence_count"] == 2, None),
    ("assembly_edit_relations", {"kind": "rigid_group", "name": "NoSuchGroup", "action": "delete"}, "refused", None),
    # contact sets: the design-level lifecycle on a scratch set built from the story's own parts -
    # create (>=2 distinct members), the single-member refusal, re-member, rename reading the LANDED
    # name back, a suppress round-trip, the two analysis flags (restored), and delete + re-list.
    ("assembly_edit_contacts", {"action": "create", "members": ["Frame:1", "Carrier:1"]},
     lambda p: p.get("member_count") == 2 and bool(p.get("contact_set")),
     ("contact_set", lambda p: p["contact_set"])),
    ("assembly_edit_contacts", {"action": "create", "members": ["Frame:1"]}, "refused", None),
    ("assembly_get", {"include": ["contacts"]},
     lambda p: p.get("contact_count", 0) >= 1 and "enabled" in p.get("contact_analysis", {}), None),
    ("assembly_edit_contacts", lambda c: {"action": "set_members", "name": _ctx_get(c, "contact_set", "contact set name"), "members": ["Frame:1", "Pedestal:1"]},
     lambda p: p.get("member_count") == 2, None),
    ("assembly_edit_contacts", lambda c: {"action": "rename", "name": _ctx_get(c, "contact_set", "contact set name"), "new_name": "SweepContacts"},
     lambda p: str(p.get("contact_set", "")).startswith("SweepContacts"),
     ("contact_set", lambda p: p["contact_set"])),
    ("assembly_edit_contacts", lambda c: {"action": "suppress", "name": _ctx_get(c, "contact_set", "contact set name")},
     lambda p: p.get("is_suppressed") is True, None),
    ("assembly_edit_contacts", lambda c: {"action": "unsuppress", "name": _ctx_get(c, "contact_set", "contact set name")},
     lambda p: p.get("is_suppressed") is False, None),
    # scope is REFUSED while contact analysis is off - the platform raises '3 : Contact analysis is
    # disabled.' on the write - so the enable comes first and the design is left as it was found.
    ("assembly_edit_contacts", {"action": "set_analysis_scope", "scope": "contact_sets"}, "refused", None),
    ("assembly_edit_contacts", {"action": "enable_analysis"}, lambda p: p.get("analysis_enabled") is True, None),
    ("assembly_edit_contacts", {"action": "set_analysis_scope", "scope": "contact_sets"},
     lambda p: p.get("scope") == "contact_sets", None),
    # put the scope back to the design's own all_bodies BEFORE disabling: the flag is retained under
    # a disable and comes back on the next enable, so skipping this would leave the story document
    # carrying a contact_sets scope it never had.
    ("assembly_edit_contacts", {"action": "set_analysis_scope", "scope": "all_bodies"},
     lambda p: p.get("scope") == "all_bodies", None),
    ("assembly_edit_contacts", {"action": "disable_analysis"},
     lambda p: p.get("analysis_enabled") is False and p.get("scope") == "all_bodies", None),
    ("assembly_edit_contacts", {"action": "delete", "name": "NoSuchContactSet"}, "refused", None),
    ("assembly_edit_contacts", lambda c: {"action": "delete", "name": _ctx_get(c, "contact_set", "contact set name")},
     lambda p: p.get("deleted") is True, None),
    _watch("Frame:1"),
    # DRIVE THE AXES ON CAMERA: yaw proves the no-take gate; both ring pivots and the crank ->
    # rotor 2:1 drive for real. Yaw CANNOT move in this scene - Carrier:1 rides the rigid group
    # with Frame:1 and the chain closes through Pedestal:1, so the solver holds it at 0 (measured:
    # it stays 0 even with Frame:1's parent lock released) - so its drive must REFUSE as a
    # no-take.
    ("joint_drive", {"joint_name": "Yaw", "angle_deg": 30}, "refused", None),
    ("joint_drive", {"joint_name": "PivotOuter", "angle_deg": 20}, _driven_angle(20), None),
    ("joint_drive", {"joint_name": "PivotInner", "angle_deg": 25}, _driven_angle(25), None),
    ("joint_drive", {"joint_name": "CrankAxis", "angle_deg": 30}, _driven_angle(30), None),
    # CrankAxis (a link member) is now in the session drive registry, so driving its partner Spin
    # must REFUSE (the second-member guard). This is also the live proof that
    # MotionLink.jointOne/jointTwo resolve: a wrong property name would leave the partner lookup
    # blind and this drive would wrongly succeed, failing the row.
    ("joint_drive", {"joint_name": "Spin", "angle_deg": 5}, "refused", None),
    # the driven pose read back off the JOINTS themselves - the three drives above reported their
    # own value_now, and this is the second witness to the same three. Spin (the crank's 2:1 link
    # partner) is REPORTED in the ledger line, not asserted: this run is the first read of what the
    # link leaves in the partner's stored value.
    ("assembly_get", {},
     _joints_listed(10, {"PivotOuter": 20, "PivotInner": 25, "CrankAxis": 30, "Yaw": 0}), None),
    ("assembly_inspect_interference", {}, _interference_measured, None),   # driven pose
    ("joint_drive", {"joint_name": "Yaw", "angle_deg": 0}, _driven_angle(0), None),
    ("joint_drive", {"joint_name": "PivotOuter", "angle_deg": 0}, _driven_angle(0), None),
    ("joint_drive", {"joint_name": "PivotInner", "angle_deg": 0}, _driven_angle(0), None),
    ("joint_drive", {"joint_name": "CrankAxis", "angle_deg": 0}, _driven_angle(0), None),
    ("assembly_inspect_interference", {}, _interference_measured, None),   # rest pose
    # pose + constrain cameos (do not disturb the jointed mechanism).
    ("model_create_component", {"name": "PoseCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "PoseS"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 300, "y1": 100, "x2": 320, "y2": 120, "sketch_name": "PoseS"}, "ok", None),
    ("model_extrude", {"sketch_name": "PoseS", "profile_index": 0, "distance": 10}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    # the cameo's first move, off a fresh occurrence still at the identity transform - so the
    # position read back off transform2 is the 40 mm this call composed onto it.
    ("assembly_move", {"occurrence": "PoseCameo:1", "dx": 40}, _moved_occurrence(40), None),
    # the pending pose is transient until captured: status sees it armed with no captured markers
    # yet (this document has captured none), and discard_pending throws it away - the pending flag
    # clears while the captured-marker collection is left exactly as it was.
    ("assembly_capture_position", {"action": "status"},
     lambda p: p.get("has_pending") is True and p.get("snapshot_count") == 0, None),
    ("assembly_capture_position", {"action": "discard_pending"},
     lambda p: p.get("discarded") is True and p.get("has_pending") is False
     and p.get("snapshot_count") == 0, None),
    # re-arm the move the capture below records - the discard consumed the first one. Where the
    # discard left the part is what this run measures, so only the read-back itself is asserted.
    ("assembly_move", {"occurrence": "PoseCameo:1", "dx": 40}, _moved_occurrence(), None),
    ("assembly_capture_position", {"action": "capture"}, _captured, None),
]
_MOTION += _box("ConA", ox=360, tint="#E5533C") + _box("ConB", ox=360, tint="#1E88E5", shape="disc") + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_constrain", {"snap_one": "ConA:1:bottom", "snap_two": "ConB:1:top", "flipped": True}, _constrained, None),
    ("design_recompute", {}, "ok", None),
] + _box("MateSeat", ox=460, tint="#6A1B9A") + _box("MateArm", ox=520, tint="#FDD835", shape="bar") + [
    # ONE CONSTRAINT, SEVERAL RELATIONSHIPS - the table in Fusion's own Constrain Components dialog,
    # where a single constraint FEATURE carries a row per geometry pair, each row with its own type,
    # offset and angle. That is how Fusion actually locates a part: a set solved TOGETHER, because one
    # face pair almost never fixes anything. The row above is the single-pair shorthand; this is the
    # set form.
    # The two rows take DIFFERENT freedoms, which is what makes the set solvable: a SEAT (the arm's
    # underside onto the seat block's top face, 2 mm proud, flipped so the two faces oppose) and a
    # TURN about it (30 deg between the two front faces). Two rows reaching for the SAME freedom
    # over-constrain instead - measured on a live document: a face-to-face mate plus a concentric on
    # one pair of discs computes with a WARNING, with and without the offset, and the tool refuses
    # rather than leave a warned constraint in the design.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("assembly_constrain", {"relationships": [
        {"snap_one": "MateArm:1:bottom", "snap_two": "MateSeat:1:top", "flip": True, "offset": 2},
        {"snap_one": "MateArm:1:front", "snap_two": "MateSeat:1:front", "angle_deg": 30},
    ]},
     # BOTH ROWS IN ONE NUMBER. The count is read off the CREATED constraint, never echoed - a
     # constraint holding FEWER rows than were submitted is refused by the tool - and the rotation the
     # tool measures for itself is 180 - 30: the seat row's flip and the turn row's angle composed.
     # Neither row on its own produces 150.
     lambda p: _constrained(p) and _measured(
         "both relationship rows landed in ONE constraint, and both acted",
         {"relationship_count": p.get("relationship_count"),
          "relationships_submitted": p.get("relationships_submitted"),
          "moved": p.get("moved")},
         p.get("relationships_submitted") == 2 and p.get("relationship_count") >= 2
         and any(_near(m.get("rotation_deg"), 150.0, 0.5)
                 for m in (p.get("moved") or []))), None),
    ("design_recompute", {}, "ok", None),
    # THE TWO ROWS PROVEN BY THEIR EFFECTS, which is the only honest way to tell them apart: no read
    # publishes a per-row TYPE (the dialog's Type column has no counterpart on the wire), so a count
    # of two says two rows landed and nothing about what each one did. The arm's own bounding box
    # says both. Its underside sits 2 mm above the seat block's 10 mm top face - the seat row, read on
    # Z, the one axis the layout pass never moves - and a 34 x 10 mm bar turned 30 deg measures
    # 34.45 x 25.66 across the world axes, which is the turn row and nothing else.
    ("model_inspect", {"target": "MateArm:1"},
     lambda p: _measured("the seat row holds the arm 2 mm proud of a 10 mm block, the turn row has "
                         "it 30 deg off the world axes",
                         {"min_z": (p.get("min_point") or {}).get("z"),
                          "x": p.get("x"), "y": p.get("y")},
                         _near((p.get("min_point") or {}).get("z"), 12.0, 0.05)
                         and _near(p.get("x"), 34.445, 0.05)
                         and _near(p.get("y"), 25.660, 0.05)), None),
    # RESTRUCTURE: two more crank instances (they share the Crank component's geometry), one of them
    # re-parented under the frame, then both removed so the mechanism is left as it was found. Fusion
    # numbers an instance from a per-component counter, so every path here is READ back, never a
    # predicted ':2'.
    ("design_add_instance", {"component": "Crank", "x": 60, "y": -60, "units": "mm"},
     lambda p: p.get("created") is True and p.get("component") == "Crank"
     and str(p.get("full_path", "")).startswith("Crank:"),
     ("crank_b", lambda p: p["full_path"])),
    # the SECOND call names the same component while two instances of it exist - the bare name that
    # would otherwise be ambiguous resolves because every candidate is an instance of ONE component.
    ("design_add_instance", {"component": "Crank", "x": 90, "y": -60, "units": "mm"},
     lambda p: p.get("created") is True
     and p.get("full_path") not in ("Crank:1", None), ("crank_c", lambda p: p["full_path"])),
    ("design_get", {"include": ["tree"]}, "ok", None),
    # the re-parent: the browser path changes, the world position does not.
    ("design_move_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_b", "the second crank"),
                                          "into_component": "Frame:1"},
     lambda p: p.get("changed") is True and str(p.get("full_path", "")).startswith("Frame:1+")
     and p.get("world_position_preserved") is True, ("crank_b", lambda p: p["full_path"])),
    # there is no root target: the API moves an occurrence into another OCCURRENCE, so the direction
    # is refused by name instead of being attempted and failing inside Fusion.
    ("design_move_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_b", "the second crank"),
                                          "into_component": "root"}, "refused", None),
    # a component may not hold an instance of itself, on either tool.
    ("design_add_instance", {"component": "Frame", "into_component": "Frame:1"}, "refused", None),
    ("design_move_occurrence", {"occurrence": "Frame:1", "into_component": "Frame:1"},
     "refused", None),
    ("design_delete_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_b", "the second crank")},
     "ok", None),
    ("design_delete_occurrence", lambda c: {"occurrence": _ctx_get(c, "crank_c", "the third crank")},
     "ok", None),
]

# ACT 3 fallback: the proven 12-box joint/assembly fixture.
_MOTION_FB = (
    [("design_activate_component", {"occurrence": "root"}, "ok", None)]
    + _box("JA", tint="#E5533C") + _box("JB", tint="#1E88E5", shape="disc") + _box("JC", ox=100, tint="#E5533C") + _box("JD", ox=100, tint="#1E88E5", shape="disc") + _box("JE", tint="#E5533C") + _box("JF", tint="#1E88E5", shape="disc")
    + _box("JG", tint="#E5533C") + _box("JH", tint="#1E88E5", shape="disc") + _box("JK", tint="#E5533C") + _box("JL", tint="#1E88E5", shape="disc") + _box("JM", tint="#E5533C") + _box("JN", tint="#1E88E5", shape="disc")
    + _box("JP", tint="#E5533C") + _box("JQ", tint="#1E88E5", shape="disc")
    + [
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("assembly_ground", {"occurrence": "JB:1", "ground_to_parent": True}, _grounded, None),
        ("assembly_ground", {"occurrence": "JD:1", "ground_to_parent": True}, _grounded, None),
        ("assembly_ground", {"occurrence": "JN:1", "ground_to_parent": True}, _grounded, None),
        ("joint_create_origin", {"anchor": "coordinates", "x": 10, "y": 0, "z": 0, "name": "JOc"},
         _joint_origin_at("JOc", 10, 0, 0), None),
        ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "StockCenter"},
         _joint_origin_at("StockCenter", 0, 0, 0), None),
        ("joint_create", {"occurrence_one": "JA:1:top", "occurrence_two": "JB:1:top", "joint_type": "revolute", "axis": "z", "name": "RevJoint"}, _jointed("RevJoint"), None),
        ("joint_create", {"occurrence_one": "JC:1:top", "occurrence_two": "JD:1:top", "joint_type": "slider", "axis": "x", "name": "SlideJoint"}, _jointed("SlideJoint"), None),
        ("joint_drive", {"joint_name": "RevJoint", "angle_deg": 30}, _driven_angle(30), None),
        ("joint_drive", {"joint_name": "RevJoint", "angle_deg": 0}, _driven_angle(0), None),
        ("joint_edit", {"joint_name": "RevJoint", "min_deg": -45, "max_deg": 45},
         _joint_limits("RevJoint", min_deg=-45, max_deg=45), None),
        ("joint_create", {"occurrence_one": "JM:1:top", "occurrence_two": "JN:1:top", "joint_type": "revolute", "axis": "z", "name": "RevJoint2"}, _jointed("RevJoint2"), None),
        ("joint_motion_link", {"joint_one": "RevJoint", "joint_two": "RevJoint2", "ratio": 2},
         _motion_linked("RevJoint", "RevJoint2", False), None),
        # RevJoint was driven above, so driving its NEW link partner must refuse (the second-member
        # guard). This row also proves MotionLink.jointOne/jointTwo resolve live: a wrong property
        # name would make the partner lookup blind and this drive would wrongly succeed.
        ("joint_drive", {"joint_name": "RevJoint2", "angle_deg": 10}, "refused", None),
        ("joint_create_as_built", {"occurrence_one": "JE:1", "occurrence_two": "JF:1"}, _as_built, None),
        ("find_geometry", {"target": "JG", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("jg_face")),
        ("find_geometry", {"target": "JH", "kind": "planar_face", "nearest_to": [10, 10, 10], "max_results": 1}, "ok", _fg("jh_face")),
        ("joint_at_geometry", lambda c: {"handle_one": _ctx_get(c, "jg_face", "joint face a"), "handle_two": _ctx_get(c, "jh_face", "joint face b"), "motion": "rigid"}, _jointed_at_geometry, None),
        # the five joints this fixture built (three parametric, one as-built, one at geometry).
        ("assembly_get", {}, _joints_listed(5, {"RevJoint": 0}), None),
        # JK:1 is a fresh box occurrence at the identity transform, so its read-back position IS the
        # 80 mm this move composed.
        ("assembly_move", {"occurrence": "JK:1", "dx": 80}, _moved_occurrence(80), None),
        ("assembly_capture_position", {"action": "capture"}, _captured, None),
        ("assembly_rigid_group", {"occurrences": ["JP:1", "JQ:1"]}, _rigid_grouped(2), None),
        ("assembly_constrain", {"snap_one": "JK:1:bottom", "snap_two": "JL:1:top", "flipped": True}, _constrained, None),
        ("assembly_inspect_interference", {}, _interference_measured, None),
        ("design_recompute", {}, "ok", None),
    ]
)


# ACT 9: VISE FIXTURE - the eval-proven self-centering vise modeled around the Carrier.
# Geometry contract (all mm, Carrier occupies x[-50,50] hub y[-14,14] z[-32,-26]):
#   STOCK    x[-55,55] y[-18,18] z[-41,-23]  - real margin all around (the adaptive's material)
#   ViseBase x[-70,70] y[-52,52] z[-69,-49]
#   Jaw seat z=-49..-41 (the stock's underside rests level with the seat ledge)
#   Jaw grip faces OPEN at y=-/+21; stock sides at y=-/+18 -> each jaw closes 3mm to contact
#   Jaw lips top out at z=-34, and the CARRIER inside the stock spans z[-32,-26] - so the part
#   being machined stands entirely ABOVE the jaws. That is the whole reason the stock is 18 mm
#   thick rather than 12: a cutter reaching a part level with the jaw tops fouls the fixture, so
#   the grip is taken low on the billet and every machined surface is clear above it.

def _plate(comp, sketch, z_offset, x1, y1, x2, y2, height):
    """Component + its own build plane at z_offset + one rectangle, extruded up by height.
    The plane lives INSIDE the component: sketch_create resolves construction-plane names in
    the ACTIVE component, so a root-level plane is invisible after activate."""
    plane = comp + "Floor"
    return [
        ("model_create_component", {"name": comp, "activate": True}, _made_component, None),
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": z_offset,
                                "name": plane}, _datum_plane("xy"), None),
        ("sketch_create", {"plane": plane, "name": sketch}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                 "sketch_name": sketch}, "ok", None),
        ("model_extrude", {"sketch_name": sketch, "profile_index": 0, "distance": height},
         _extruded, None),
    ]


def _second_plate(comp, sketch, z_offset, x1, y1, x2, y2, height):
    """A second stacked extrude inside the ACTIVE component (the jaw's upper gripping lip)."""
    plane = comp + "LipPlane"
    return [
        ("model_construction", {"kind": "plane", "plane": "xy", "offset": z_offset,
                                "name": plane}, _datum_plane("xy"), None),
        ("sketch_create", {"plane": plane, "name": sketch}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                 "sketch_name": sketch}, "ok", None),
        ("model_extrude", {"sketch_name": sketch, "profile_index": 0, "distance": height},
         _extruded, None),
    ]


_VISE = (
    # THE STOCK FIRST, dressed before any of the fixture exists. It is the billet the vise is built
    # AROUND, and its look has to be settled before the jaws are there to close on it - a stock that
    # changes appearance halfway through the clamping reads as the clamping doing it.
    _plate("STOCK", "StockS", -41, -55, -18, 55, 18, 18)
    + [
        ("design_activate_component", {"occurrence": "root"}, "ok", None),
        ("appearance_set", {"target": "STOCK", "color": "#8D6E63"}, "ok", None),
        # HALF translucent, so the Carrier inside stays visible through the billet it is cut from -
        # the whole point of the fixture shot is the part in the stock in the vise, and an opaque
        # billet hides the part. Opacity here is the browser's Opacity Control (Component.opacity),
        # NOT the appearance's transparency: the two are unrelated, and a fully opaque colour still
        # renders see-through under an opacity override. Read back off what actually RENDERS, since
        # the override is inherited from parent components.
        ("appearance_set", {"target": "STOCK:1", "opacity": 50},
         lambda p: p.get("opacity_rendered") == 50, None),
    ]
    + _plate("ViseBase", "VBase", -69, -70, -52, 70, 52, 20)
    # JawL: lower seat block up to the seat ledge (z=-35), then the gripping lip above it.
    + _plate("JawL", "JLseat", -49, -30, -37, 30, -15, 8)
    + _second_plate("JawL", "JLlip", -41, -30, -37, 30, -21, 7)
    + _plate("JawR", "JRseat", -49, -30, 15, 30, 37, 8)
    + _second_plate("JawR", "JRlip", -41, -30, 21, 30, 37, 7)
) + [
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("appearance_set", {"target": "ViseBase", "color": "#455A64"}, "ok", None),
    ("appearance_set", {"target": "JawL", "color": "#E5533C"}, "ok", None),
    ("appearance_set", {"target": "JawR", "color": "#E5533C"}, "ok", None),
    # the fixture skeleton: base grounded, each jaw a NAMED slider on the base. Every component
    # here has its origin at the WORLD origin (geometry is drawn in world coordinates), so the
    # ':origin' snap aligns already-aligned frames - a positional no-op, no teleport - and the
    # slider axis 'y' rides the world-aligned frame.
    ("assembly_ground", {"occurrence": "ViseBase:1", "ground_to_parent": True}, _grounded, None),
    ("joint_create", {"occurrence_one": "JawL:1:origin", "occurrence_two": "ViseBase:1:origin",
                      "joint_type": "slider", "axis": "y", "name": "SlideL"}, _jointed("SlideL"), None),
    ("joint_create", {"occurrence_one": "JawR:1:origin", "occurrence_two": "ViseBase:1:origin",
                      "joint_type": "slider", "axis": "y", "name": "SlideR"}, _jointed("SlideR"), None),
    # the part in the stock, as-built rigid - the billet and what will be cut out of it are one
    # piece until the cutter says otherwise. The stock is NOT jointed to the base: a billet welded to
    # the vise body is not being held by anything, and the jaws closing on it would prove nothing.
    # What holds it is the grip, captured below once the jaws have actually closed.
    ("joint_create_as_built", {"occurrence_one": "Carrier:1", "occurrence_two": "STOCK:1"}, _as_built, None),
    # SELF-CENTERING: couple the two sliders at ratio -1 (on real sliders the platform accepts
    # slider-slider links - live-verified). A negative ratio is applied as a REVERSED coupling of
    # magnitude 1, which is what 'reversed' reads back.
    ("joint_motion_link", {"joint_one": "SlideL", "joint_two": "SlideR", "ratio": -1},
     _motion_linked("SlideL", "SlideR", True), None),
    # drive ONE jaw closed by its 3mm approach; the link must bring the OTHER jaw in too.
    ("joint_drive", {"joint_name": "SlideL", "distance": 3}, _driven_slide(3), None),
    # GRIP VERIFIED BY MEASURE, not by trust: each jaw's gripping face touches its stock side.
    ("find_geometry", {"target": "JawL", "kind": "planar_face", "nearest_to": [0, -18, -37],
                       "max_results": 1}, "ok", _fg("jawL_grip")),
    ("find_geometry", {"target": "JawR", "kind": "planar_face", "nearest_to": [0, 18, -37],
                       "max_results": 1}, "ok", _fg("jawR_grip")),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "jawL_grip", "JawL grip face"),
                                         "b": "STOCK"},
     lambda p: p.get("distance", 99) <= 0.1, None),
    ("model_measure_between", lambda c: {"a": _ctx_get(c, "jawR_grip", "JawR grip face"),
                                         "b": "STOCK"},
     lambda p: p.get("distance", 99) <= 0.1, None),
    # A DRIVE LEAVES A TRANSIENT POSE, and a joint create is REFUSED while one is pending (it would
    # silently revert it). So the clamped pose is recorded into the timeline first - which is also
    # the right order physically: the jaws are closed, that closure is what the grip joint captures.
    ("assembly_capture_position", {"action": "capture"}, _captured, None),
    # THE GRIP ITSELF, captured only now that both faces measure closed onto the billet: an as-built
    # joint mates two occurrences WHERE THEY ALREADY ARE, so taking it here records the clamped pose
    # rather than creating it. ONE jaw takes the joint - the slider and its motion link are what
    # bring the other side in, and jointing the stock to both jaws would close the loop twice and be
    # refused as over constrained (measured).
    ("joint_create_as_built", {"occurrence_one": "STOCK:1", "occurrence_two": "JawL:1",
                               "name": "GripL"},
     lambda p: _as_built(p) and _measured("the grip joint names the jaw it was taken on",
                                          {"joint": p.get("joint")}, p.get("joint") == "GripL"), None),
    ("assembly_inspect_interference", {}, _interference_measured, None),
    _watch(["ViseBase:1", "STOCK:1"]),
    ("view_screenshot", {"width": 500, "height": 400}, "ok", None),
]
