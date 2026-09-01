# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The act program: the ordered acts, the passes they run through, and the ledger tables.

`_ACT_PROGRAM` is (name, precondition, narrative, fallback) per act - a precondition read that
ERRORS routes that act to its fallback fixture. It is hoisted, laid out and framed at import
through verify_layout.py, and `ACTS` is the result run() walks. `STEPS` is the flat union the
completeness lint reads, `STORY` the per-tool shot-list note the receipt carries, `EXCLUDED` the
tools deliberately not driven unattended, `PENDING` the honest todo.
"""

from verify_acts_cam import _CAM, _CAM_DELIVER, _CAM_FB_DELIVER, _CAM_STORY
from verify_acts_doc import _FINALE, _OVERTURE
from verify_acts_mesh import _MACHINING, _MESH, _NESTING
from verify_acts_model import (
    _DETAILS, _DETAILS_FB, _REDUCE, _RESIZE, _RESIZE_FB, _SOLIDS, _SOLIDS_FB)
from verify_acts_motion import _MOTION, _MOTION_FB, _VISE
from verify_acts_sketch import _SKELETON, _SKETCHWORK
from verify_core import _DWELL, _PLANE_VIEW, _SKETCH_PLANE
from verify_layout import (
    _CHUNK_OF, _COMPONENTS, _PATTERNED, _PLACED_BOX, _SLOTS, _framed, _place_points, _place_slots,
    _place_walk, _placed, _sketch_reading_order, _sketches_first)


# --- the build, as ACTS: one recognizable gyroscope, end to end, in one unsaved document -------
# The sweep is a STORY, not a scratch pile: a three-axis gyroscope is cast (skeleton + parameters),
# turned solid (rings, rotor, frame, crank), jointed and DRIVEN on every axis, detailed, machined,
# resized parametrically, and discarded. Every covered tool's receipt step is woven into that story
# where it fits; where it does not, a CAMEO fixture rides inside the SAME document.
#
# Each ACT is a dict: name, precondition, narrative, fallback.
#   precondition: (tool, args) - a live read gating the narrative (the geometry it consumes exists),
#                 or None (an opening act with nothing upstream to depend on).
#   narrative:    the steps weaving the act's tools into the gyroscope story.
#   fallback:     self-contained SCRATCH steps covering the SAME tools if the precondition read
#                 fails (a cascade from an upstream act that could not build) - or None for a
#                 same-doc cameo that depends on nothing. A fallback row is marked "(fallback
#                 fixture)" in the ledger so a narrative regression shows in the diff.
# A step is (tool, args, expect, save): args a dict or callable(ctx); expect "ok"/"refused"; save an
# extractor pair or None. The STORY map below gives each covered tool its ledger shot-list note.

# --- the ACT program --------------------------------------------------------------------------
# (name, precondition, narrative, fallback). A precondition read that ERRORS routes the act to its
# fallback (its tools are still covered, each marked "(fallback fixture)"). None precondition = an
# opening/cameo act that always runs its narrative.
_ACT_PROGRAM = [
    ("ACT 0 - OVERTURE", None, _OVERTURE, None),
    # SKETCH. The parametric skeleton and every sketch the story builds on, then the sketch TOOLS -
    # trim, offset, pattern, dimension, constrain, text, the slot kinds - on scratch sketches of
    # their own. Both run before anything is solid, which is the order the work is done in.
    ("ACT 1 - SKETCH + PARAMETERS", None, _SKELETON, None),
    ("ACT 1b - SKETCH TOOLS", None, _SKETCHWORK, []),
    # CREATE. Material appears: solids from the skeleton, then surface bodies, then mesh bodies.
    ("ACT 2 - SOLIDS", ("sketch_get", {"sketch_name": "OuterRingSketch"}), _SOLIDS, _SOLIDS_FB),
    ("ACT 3 - SURFACES", None, _MACHINING, None),
    ("ACT 4 - MESH", None, _MESH, None),
    # MODIFY. Existing material is cut, rounded, patterned and drafted.
    ("ACT 5 - DETAILS", ("find_geometry", {"target": "OuterRing", "kind": "circular_edge", "max_results": 1}), _DETAILS, _DETAILS_FB),
    # ASSEMBLE. The parts are jointed, grounded, related and driven.
    ("ACT 6 - MOTION", ("find_geometry", {"target": "OuterRing", "kind": "cylinder_face", "max_results": 1}), _MOTION, _MOTION_FB),
    # RE-DRIVE. The parametric resize walks the WHOLE assembled mechanism, so it reads state only
    # assembly produces: the StockCenter joint origin holding position through the recompute, and a
    # rest pose whose only overlap is the intended press fit. It runs after MOTION for that reason.
    ("ACT 7 - RESIZE", ("sketch_get", {"sketch_name": "OuterRingSketch"}), _RESIZE, _RESIZE_FB),
    # NEST. Last, because the arrange solver restructures what it nests under Envelope occurrences.
    ("ACT 7b - NESTING", None, _NESTING, []),
    # MACHINE. Strip to the machinable part, model the vise around it, and machine the REAL part in
    # the REAL fixture. Each act's precondition routes to a fallback (empty when the act's tools are
    # all covered by earlier acts) so a broken story world still yields a complete per-tool ledger -
    # on the scratch-stock fixtures.
    ("ACT 8 - REDUCE TO THE PART", ("model_inspect", {"target": "Carrier:1"}), _REDUCE, []),
    ("ACT 9 - VISE FIXTURE", ("model_inspect", {"target": "Carrier:1"}), _VISE, []),
    ("ACT 10a - CAM: JOB + GENERATE", ("model_inspect", {"target": "STOCK:1"}), _CAM_STORY, _CAM),
    ("ACT 10b - CAM: DELIVERABLES", ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}), _CAM_DELIVER, _CAM_FB_DELIVER),
    ("FINALE", None, _FINALE, None),
]

# Every sketch that can be drawn on bare origin planes is drawn in ACT 1c, before anything is
# solid - the acts after it model, they do not sketch. The acts named here keep their own steps: the
# two sketch acts are already sketch-first, the OVERTURE has no geometry, and the vise draws every
# profile on a datum plane derived from the part it is being built around.
_SKETCH_PHASE, _ACT_PROGRAM = _sketches_first(
    _ACT_PROGRAM, after=("ACT 0 - OVERTURE", "ACT 1 - SKETCH + PARAMETERS", "ACT 1b - SKETCH TOOLS",
                         "ACT 9 - VISE FIXTURE"))
_ACT_PROGRAM = (_ACT_PROGRAM[:3]
                + [("ACT 1c - EVERY OTHER SKETCH", None, _SKETCH_PHASE, [])]
                + _ACT_PROGRAM[3:])

# Every act runs through the layout pass and then the framing pass, so a part added to the story
# later gets a slot of its own and a camera row without anyone remembering to give it either. Only
# the narrative is laid out: a fallback act rebuilds a story-less world at the origin, and the two
# never run together.
_SLOTS.update(_place_slots(_ACT_PROGRAM))

# ...then walk the sketch phase in reading order. This runs AFTER the cells are dealt because it
# needs to know which sketches got one: an origin-anchored sketch has no cell and sits a metre from
# the field, so it is drawn with the others of its kind rather than in the middle of a row. The
# re-order preserves the packer's order over the placed chunks, so _SLOTS stays true.
_ACT_PROGRAM = [(name, pre, (_sketch_reading_order(narr, _SLOTS) if "ACT 1c" in name else narr), fb)
                for name, pre, narr, fb in _ACT_PROGRAM]


def _placed_boxes(program, slots):
    """{chunk: [x0, x1, y0, y1]} once every chunk is in its slot - what the framing pass reads to
    tell whether the next subject is already on screen."""
    box = {}
    for _name, _pre, narr, _fb in program:
        for step, chunk, _cursor, frame in _place_walk(_placed(narr, slots), home_out=_CHUNK_OF):
            if chunk is None or not isinstance(step[1], dict):
                continue
            for x, y in _place_points(step[1], frame, step[0]):
                b = box.setdefault(chunk, [None, None, None, None])
                if x is not None:
                    b[0] = x if b[0] is None else min(b[0], x)
                    b[1] = x if b[1] is None else max(b[1], x)
                if y is not None:
                    b[2] = y if b[2] is None else min(b[2], y)
                    b[3] = y if b[3] is None else max(b[3], y)
    return {c: [v if v is not None else 0.0 for v in b] for c, b in box.items()}


_PLACED_BOX.update(_placed_boxes(_ACT_PROGRAM, _SLOTS))
_COMPONENTS.update(s[1]["name"] for _n, _p, narr, _f in _ACT_PROGRAM for s in narr
                   if s[0] == "model_create_component" and isinstance(s[1], dict) and s[1].get("name"))
_PATTERNED.update(c for _n, _p, narr, _f in _ACT_PROGRAM
                  for st, c, _cur, _fr in _place_walk(narr)
                  if st[0].startswith("model_pattern_") and c)
_SKETCH_PLANE.update({s[1]["name"]: s[1].get("plane") for _n, _p, narr, _f in _ACT_PROGRAM
                      for s in narr if s[0] == "sketch_create" and isinstance(s[1], dict)
                      and s[1].get("name") and s[1].get("plane") in _PLANE_VIEW})

ACTS = [(name, pre, _framed(_placed(narr, _SLOTS)), _framed(fb) if fb is not None else fb)
        for name, pre, narr, fb in _ACT_PROGRAM]

# Post-act hook run() fires after an act completes: the bounded generation poll between the CAM
# job act and its deliverables.
POLL_AFTER = {
    "ACT 10a - CAM: JOB + GENERATE": {"narrative": "DemoSetup", "fallback": "Setup1"},
}

# STEPS: the flat union of every act's narrative + fallback steps - the coverage ledger the
# completeness lint reads (every registered tool must appear as some step's tool). run() iterates
# ACTS (choosing narrative or fallback per act); STEPS exists so the lint sees the whole surface.
STEPS = [s for _, _, narr, fb in ACTS for s in (list(narr) + list(fb or []))
         if s[0] != _DWELL]

# STORY: each covered tool's ledger shot-list note - the receipt doubles as the demo's shot list.
STORY = {
    "doc_new": "open the one document the whole gyroscope lives in",
    "workspace_orient": "orient: read the empty design before building",
    "sys_capability_map": "survey the server's tool families at cold start",
    "sys_find_tool": ("search the surface for the revolve verb - a registry read, so it carries no "
                      "'active_document' stamp (design_get's final read is the other half)"),
    "sys_get_api_doc": "read the RevolveFeatures API doc",
    "sys_get_guidance": ("read the packaged design guidance as a tool-only client does - the section "
                        "index, then the assemble section's rule records beside the content hash "
                        "that versions them"),
    "view_list_workspaces": "list the workspaces available",
    "view_set": ("orient the camera to the iso hero angle, with the perspective angle carried "
                 "through to the camera and read back; then the whole verb set on the finished "
                 "fixture - snapshot, a turntable through every camera preset (one framed orient "
                 "sets the subject, the rest rotate about it with fit=false), every visual style, "
                 "isolate/hide/show/clear_isolation, a persistent Named View saved, found in the "
                 "document's own list and re-applied, and restore putting camera, style and every "
                 "bulb back where the tour started. SKIPPED(rig): the snapshot/restore "
                 "truncation beats (truncated + occurrence_cap) need an assembly with more "
                 "occurrences than the cap, and the story document stays well under it"),
    "sys_get_selection": "expected refusal: nothing is selected yet",
    "sys_get_preferences": ("read the application's own configuration - the default projection, two "
                            "decoded enum families, the compatibility group, and all three members "
                            "the census found RAISING on this build, each published null and named "
                            "in 'unreadable' with none of them miscategorised as a member the build "
                            "does not carry"),
    "sys_set_preferences": ("round-trip one invisible preference and restore it in the same act; "
                            "the below-minimum value and a tier-R member refused"),
    "param_add": "add GimbalDia and the derived ring/rotor radii",
    "param_set_favorite": "mark GimbalDia the favorite driving dimension",
    "param_get": "read the parameter table; a fresh GimbalDia read sizes the CAM stock",
    "model_create_component": "cast the eight parts, Pedestal nested in Frame",
    "design_activate_component": "step into each part to build its sketch",
    "sketch_create": "draw each part's sketch on its plane",
    "sketch_add_geometry": ("draw the concentric rings and part footprints; then the SLOT family, "
                            "one scratch sketch per shape - a three-point arc slot with its five "
                            "arcs and its profile, the centre-point arc slot in both its short and "
                            "its full ladder with each dimension flag gated independently, an "
                            "overall slot whose cap centres prove the tip-to-tip measure, the "
                            "length/angle tail that adds the fourth line and its own dimensions, "
                            "the centre-point slot's HALF length landing a cap on its second point, "
                            "and the legacy centre-to-centre form; the angle with no length, the "
                            "angle FLAG on a straight slot, each cross-kind input pointed at the "
                            "kind that carries it, and a tail on the legacy form all refused - the "
                            "legacy form's own census read twice over (its note names the 2 solid "
                            "lines, the 1 construction line and the 2 arc caps behind a "
                            "'curves_added' of 3, and sketch_get finds exactly that); plus the "
                            "line/rectangle/polygon floors the composite counts are read against"),
    "sketch_add_3d_line": "draw the yaw axis as the skeleton's 3D line",
    "sketch_constrain": ("constrain the skeleton's X axis horizontal; then autoConstrain a loose "
                         "rectangle to fully constrained and re-run it as a no-op, lay a "
                         "rectangular pattern by total EXTENT with the landed centre measured, "
                         "suppress two instances of a 3x2 pattern with the landed flags and the "
                         "curve count both read back; the N-1 flag length, a knob on the wrong "
                         "constraint, and the dimensioning strategies this build does not carry "
                         "all refused. Then the second bench, carrying the kinds the first has no "
                         "geometry for: vertical, collinear and concentric; a spline made "
                         "curvature-continuous with the line it continues; the two point-pair "
                         "kinds; a square told it is a polygon; fix and unfix on one curve; and "
                         "the three CREATOR kinds - one-sided and two-sided offset, and a "
                         "six-around circular pattern - each in a sketch of its own with the "
                         "curves it drew counted"),
    "sketch_move": ("shift a line by a known offset and read the new coordinates back, then spin it "
                    "180 deg about its own midpoint - the swap only the endpoints show; the "
                    "negative-scale mirror and the empty transform refused"),
    "sketch_copy": ("copy a two-line chain to an offset position, its new refs and the endpoints in "
                    "the returned collection both accounted for, then across into a second sketch, "
                    "and again inside a COMPONENT sketch where the refs cross the occurrence-proxy "
                    "seam"),
    "sketch_insert_svg": ("import the logo art into a fresh sketch, its landed width measured "
                          "against the 1/96-inch-per-user-unit convention, then a 96-user-unit "
                          "square at scale 1 whose measured extent pins BOTH halves of that "
                          "landing - one inch square, and Y-DOWN from the sketch origin (min y "
                          "-25.4 mm); the missing file refused"),
    "sketch_dimension": ("drive ring/rotor radii by parameter expression; the wedge angle facing "
                         "the sketch origin; offset against a non-parallel line (rotated, and the "
                         "note says so) with linear_diameter refusing the same shape; line and "
                         "point measured to a model face; then the dimension bench - a slanted "
                         "line's horizontal span, a diameter, the gap between two circles on one "
                         "centre, a line to a circle's near tangent, and an ellipse's two radii - "
                         "each read back as a measured number, not a call that returned ok"),
    "sketch_get": "read the skeleton and ring profiles back",
    "sketch_delete_entity": ("delete a helper constraint; count drops - then a sketch text by its "
                             "index, the deleted string reported back, and the empty index refused"),
    "model_construction": ("offset the carrier hub plane below the rotor sweep; an AXIS on a cameo bore whose published handle the circular pattern turns about; a plane at 30 deg about the shaft's own axis (origin pinned to the axis) and a plane through a cap vertex; then the ON-PATH surface on one measured 30 mm cap edge - a proportional plane and point reading their ratio back with no extent published, an absolute placement inside the path, one before the start and one far past the end (both accepted, both disclosed against the measured length), the boundary exactly at the length, an expression placement whose model parameter is named for param_set, a to-object plane carrying distance AND offset off the path and a second one landing inside a two-edge chained path, and the summed length of that chain; the out-of-range proportional value and to_object on the point kind refused. Then the datum bench - one bored block carrying every reference the remaining modes read: a plane swung 30 deg about a top edge, one spanning three corners, one splitting the block at mid-height, one spanning two coplanar edges and one resting tangent on the bore wall; an axis on an edge, one spanning two corners and one along the top face's own normal; and points at the bore centre, at a corner where two edges meet, at the three world planes' shared origin and where an edge pierces XY. The world axis and the coordinate point are refused up front - both are setByLine/setByPoint, direct-edit-only, and this design is parametric"),
    "sketch_set_text": ("engrave the FUSION ESSENTIALS nameplate; then the path layouts - text "
                        "along a line and wrapped around a closed circle, and fitted to a line - "
                        "each checked against the created text's own definition objectType; a model "
                        "edge as the path, the three cross-mode inputs, and a layout input on an "
                        "edit all refused; then the FONT - named on a create and on an along-path "
                        "create, read back off the landed text both times, then changed on an edit "
                        "beside the string; an unknown name and its case variant refused on create "
                        "with the text count proving nothing landed, the same name refused on an "
                        "edit with the following read showing the string untouched, and a call "
                        "with no font_name publishing no font key at all"),
    "model_extrude": ("extrude the ring bands symmetric about the ring plane, then a three-bay "
                      "frame with 'all' whose payload NAMES the regions enclosed by another "
                      "selected one - the bays that filled with material"),
    "model_revolve": ("revolve the rotor disc about the spin axis, then about an off-origin "
                      "cylinder FACE - the resolved label reads BRepFace and the ring's measured "
                      "bounding box stands around x=30, not around the origin; a planar face as "
                      "the axis refused"),
    "model_loft": "loft the pedestal base-to-post transition",
    "model_sweep": "sweep the crank handle along its path",
    "model_draft": "draft a cameo face",
    "model_mirror": ("mirror a cameo body, then the emboss block's own timeline FEATURE with the "
                     "body/volume census read back, then a join whose isCombine is read off the "
                     "created feature, and meet the join-is-bodies-only refusal"),
    "model_emboss": ("raise then engrave a circular profile on a scratch block's top face, each "
                     "checked against the volume direction; a zero depth refused"),
    "model_replace_face": ("replace a scratch block's top face with an open sheet above it, the "
                           "measured volume move pinning the effect; a solid face as the target "
                           "refused"),
    "model_pipe": ("run a hollow pipe along its path with the wall read back off the feature (and "
                   "NO capped_ends claim - the face collections that would answer that read empty "
                   "on this build), then a half-path pipe whose bounding box proves the extent is a "
                   "FRACTION, a CUT scoped to a named body where the note says the scope is what "
                   "was REQUESTED because participantBodies cannot be read back, and the two "
                   "chaining fixtures: ONE seed handle chains across TANGENT junctions and stops "
                   "where that continuity breaks - several edges on an open run bounded by sharp "
                   "corners, and all eight of a closed tangent loop (which reports itself closed) "
                   "- so what a seed produces is the BUILT path's own count and nothing about the "
                   "request predicts it; the reverse extent refused on an open path"),
    "model_pattern_rectangular": "rectangular-pattern a cameo body",
    "model_pattern_circular": ("circular-pattern a cameo body about a world axis, then about a "
                               "construction axis by handle and by name with the resolved label "
                               "read back, then about the bore FACE itself; the datum name reached "
                               "from the root refused"),
    "model_pattern_path": ("pattern the feature cameo along its own edge with the count read back "
                           "off the feature's own patternElements, then along TWO connected edge "
                           "handles - a list is used EXACTLY, with no chaining, and the label says "
                           "which of the two path rules ran"),
    "assembly_edit_relations": ("suppress/unsuppress the frame lock, re-value the crank link with was_reversed disclosed, and meet the measured set_occurrences refusal in the words that make it a fact - the build it was measured on and the platform sentence it would raise - with the group's members re-read unchanged afterwards"),
    "assembly_edit_contacts": ("build a contact set from two story parts, meet the single-member refusal, re-member it, rename it reading the landed name back, suppress round-trip, switch contact analysis on and back off, then delete it"),
    "model_hole": ("drill a cameo mounting hole, then the three additive placements - centred on "
                   "its rim, on an edge at middle and at start, and by plane offsets; a circular "
                   "offset edge refused"),
    "model_combine": "join two overlapping cameo pads",
    "appearance_set": ("give each gyroscope part its own color; then the occurrence FAN-OUT in the "
                       "shape that discriminates - one body coloured directly, then the occurrence "
                       "written in a DIFFERENT colour, so the body holding its own override comes "
                       "back under 'bodies_not_reached' and not under applied_to (both colours are "
                       "minted from one base asset and share an Appearance.id, so only comparing "
                       "the id AND the name separates reached from kept); and the same shape where "
                       "that body is the occurrence's only one, refused naming it"),
    "model_set_material": "assign the rotor a physical steel material",
    "find_geometry": "acquire the face/edge/body handles the build consumes",
    "model_measure_between": "measure the outer-ring-to-inner-ring gap",
    "model_measure_relation": ("read rotor/shaft coaxiality; then the rest of the vocabulary on "
                               "the datum bench, each reporting its OWN measurement - the top face "
                               "perpendicular to a wall it meets and touching it along that edge, "
                               "flush with itself, the bore concentric with itself, and 20 mm "
                               "clear of the floor below"),
    "model_inspect": "read the rotor's volume back",
    "pmi_create": ("aim a flatness note at the frame plate and a hole note at a carrier bore, and "
                   "meet the extension gate PMI authoring sits behind on this build"),
    "pmi_get": ("read the PMI back with segments and detail, and again with an over-cap "
                "max_results - pmi_get's own contract CLAMPS it rather than refusing, since every "
                "record it returns crosses the wire whole. SKIPPED(rig): the imported-row beats "
                "(no 'text' key on an imported annotation, no 'is_hole' when isHoleAnnotation will "
                "not read) need a PMI-BEARING import; the STEP this sweep round-trips carries none"),
    "pmi_edit": ("meet the name lookup on a design holding no PMI - it lists what exists instead of "
                 "editing something else - and the blank-name guard. SKIPPED(gate): the "
                 "ambiguous-name unsuppress refusal needs AUTHORED PMI, which is extension-gated on "
                 "this build (pmi_create's own beats are that gate)"),
    "pmi_delete": "meet the same lookup refusal for the delete",
    "assembly_ground": "ground the frame so the mechanism has a base",
    "assembly_rigid_group": "rigid-group the frame and carrier base",
    "joint_create_origin": "place the crank mount and the stock-center WCS",
    "joint_create": "revolute the yaw, ring pivots, spin, and crank",
    "joint_at_geometry": ("joint a pin in its bore via cylinder faces; then the motion vocabulary on "
                          "its own cameo tree - a BALL on a real sphere face (the centre key point "
                          "named in the payload, no axis and no axis sentence), a revolute on an "
                          "explicit axis whose note says frame, NOT world, and points at the tool "
                          "that sets a true world axis, and a rigid pair with the same axis-free "
                          "report; an axis outside the Choice refused. A TORUS face joints at its "
                          "own centre on a PARAMETRIC body, which is the case the keypoint guard "
                          "must let through. SKIPPED(rig): the two base-feature halves of that "
                          "guard (a torus inside a base feature hands back its component origin "
                          "with no error) need a torus built INSIDE a base feature, and no tool on "
                          "this surface builds one unattended"),
    "joint_create_as_built": ("seat the rotor shaft in the inner ring as-built; then a REVOLUTE "
                              "as-built pair anchored on their shared face, read back through "
                              "assembly_get and driven to prove the DOF, with the missing-anchor "
                              "and rigid-plus-anchor refusals"),
    "joint_edit": ("set rotation limits on the yaw; then walk one scratch joint through every "
                   "motion the tool offers - rigid to revolute, slider, cylindrical, planar, ball "
                   "and pin_slot - each retype witnessed by the design's own joint walk rather "
                   "than by the writer, the mismatched pin_slot axis pair refused, and the bench "
                   "left on a revolute that actually drives"),
    "joint_motion_link": "couple the crank to the rotor spin at 2:1; the vise jaws at -1 (self-centering)",
    "joint_drive": "drive every axis, the crank -> rotor 2:1, then ONE vise jaw (the link closes the other)",
    "assembly_get": "read the joint wiring, driven angles, and the StockCenter anchor back",
    "assembly_move": "pose a scratch cameo occurrence",
    "assembly_capture_position": "status, discard the pending pose, re-arm and capture",
    "assembly_constrain": ("flush-constrain a scratch cameo pair through the single-pair shorthand; "
                           "then the SET form - one constraint feature carrying two relationship "
                           "rows of different inferred types, a face-to-face mate at a 2 mm offset "
                           "with the normals flipped plus a concentric one on the same two discs, "
                           "which is how Fusion's own Constrain dialog locates a part. The count "
                           "read off the CREATED constraint is what says both rows live in the one "
                           "feature - the tool refuses a constraint holding fewer than submitted"),
    "design_add_instance": ("place two more crank instances and read the landed paths back, the "
                            "second naming the component while two of it already stand; the "
                            "self-nesting target refused"),
    "design_move_occurrence": ("re-parent one of those instances under the frame, the new path and "
                               "the held world position both read back; the root target and the "
                               "self-nesting target refused"),
    "assembly_inspect_interference": "check interference at rest and driven",
    "design_recompute": "recompute the assembly after motion",
    "model_fillet": ("fillet the outer ring edge; then the two path fixtures - one box corner "
                     "rounded into an OPEN tangent run, and all four rounded into a CLOSED tangent "
                     "loop - that the chaining beats read their edge counts off"),
    "model_chamfer": ("chamfer the frame edge, then a second one by distance-and-angle with a "
                      "miter corner, both read back off the created feature"),
    "model_shell": "shell a scratch cap cameo",
    "model_offset_face": "push a scratch block's top face outward",
    "model_thread": ("thread a scratch post M10x1.5 over part of its length with the extent read "
                     "back, an explicit thread standard with its alternatives disclosed, and a "
                     "modeled thread on a second post proving it cut material; then the INTERNAL "
                     "side on a real bore - 'internal' derived from the face's own out-of-material "
                     "normal and checked against the face by the API at add() - and a partial "
                     "thread measured from the LOW end, reading that end back off the feature; an "
                     "unknown call-out, an offset with no length, and a modeled call-out too big "
                     "for the cylinder all refused"),
    "sketch_edit_curve": ("trim, extend, split, fillet, chamfer and offset on one scratch sketch "
                          "per action, with length read-backs; split's two halves must carry "
                          "distinct ids; a chamfer across an offset pair refused"),
    "model_scale": ("uniform x8 and per-axis x*y*z scales with ratio read-backs; unresolvable, "
                    "length, and angle expressions refused; a bare unitless parameter accepted; "
                    "a vertex-anchored scale"),
    "model_move": ("translate, along-axis, rotate and point-to-point move features on a scratch "
                   "block in a SINGLY placed component, each checked against the distance it was "
                   "asked for; the same along-axis move on a component placed TWICE refused naming "
                   "the count and both paths (each instance holds that body somewhere else, and no "
                   "read-back tells a right instance from a wrong one); a face as the axis and any "
                   "faces selection refused"),
    "design_delete_feature": "add a wart feature then delete it; health diff",
    "design_remove_feature": "remove a scratch body and its occurrence; deleting each Remove brings them back",
    "design_delete_occurrence": "delete a scratch occurrence",
    "view_section": "section cut through the gimbal center",
    "view_screenshot": ("capture the sectioned mechanism; write the same path twice to show the "
                        "overwrite, and refuse a write against a document that is not active; and "
                        "shoot view='current' - the no-move capture, the only way to keep a frame "
                        "the camera already holds, since a NAMED view refits the whole model"),
    "view_screenshot_multi": ("capture the front and top beauty shots; then a four-view contact "
                              "sheet, the camera restored afterwards"),
    "surface_revolve": ("revolve a prep sheet; and the half-disc that closes into the ball joint's "
                        "sphere"),
    "surface_fill": ("seal a closed revolved sphere surface into a solid, the volume measured "
                     "off the result and every tool accounted for; and seal the joint cameo's "
                     "sphere the same way, so the ball beat has a real sphere face to joint at; a "
                     "cell index one past the end refused NAMING the range that exists, with the "
                     "computing input cancelled and nothing created"),
    "surface_thicken": ("thicken the prep sheet, then a four-walled sheet with 'rounded' corners "
                        "read back off the input"),
    "surface_extrude": "extrude prep sheets",
    "surface_offset": "offset a ring face zero and nonzero",
    "surface_extend": ("extend a sheet edge with no alignment given (the payload carries no key, so "
                       "nothing was written), then a second sheet extended with 'align_edges' read "
                       "back"),
    "surface_reverse_normal": "flip a sheet normal",
    "surface_delete_face": "open a bore by deleting a face",
    "surface_patch": ("close the opened bore with a patch, then the same rim at 'tangent' "
                      "continuity and again through one interior RAIL whose landed count is read "
                      "off the input; rails paired with the multi-loop form refused"),
    "sketch_project": ("project the machining boundary; then section the cap on a datum plane with per-source attribution naming the parallel face that contributed nothing, project the cap sketch's line onto the top face reading the reference linkage back, and meet the same-sketch and missing-direction refusals"),
    "surface_trim": "trim a sheet with a cylinder cutter",
    "surface_untrim": "untrim the internal hole loop",
    "surface_create_ruled": ("rule off a sheet's top rim - tangent, normal, along a direction "
                             "entity and at an angle read off the feature - then off a SOLID box "
                             "edge, where only the new sheet is the result; both misuse refusals"),
    "model_split": "split a scratch pin by a plane",
    "model_unstitch": "unstitch a scratch box's faces",
    "model_stitch": "re-stitch two faces",
    "model_base_feature": "open and close a base-feature scope",
    "model_arrange": ("nest a square pad, a bar, a disc and a second pad inside a HEXAGON boundary, "
                      "then scale the boundary and solve again - the same four parts re-nest, which "
                      "is the arrangement being a function of the boundary rather than a one-time "
                      "placement"),
    "model_compute_holder": "compute a CAM tool holder (read)",
    "save_as_mesh": "mesh a scratch solid (one per destructive op)",
    "mesh_get": "read the mesh back",
    "mesh_generate_face_groups": "group the mesh faces",
    "mesh_to_brep": "convert a mesh to a base-feature BRep",
    "mesh_reduce": "reduce a dense mesh",
    "mesh_remesh": "remesh a copy",
    "mesh_plane_cut": "plane-cut a mesh copy",
    "mesh_combine": ("combine two mesh copies, the parametric mode and the base feature the write "
                     "ran in both named; then merge two disjoint meshes into one body"),
    "mesh_delete": "delete a scratch mesh body with the design-wide survivor re-scan",
    "mesh_export": "export a mesh to STL",
    "mesh_insert": "re-import the STL mesh",
    "mesh_repair": ("one-touch-fix a healthy mesh (an honest no-op, not a failure), rebuild it with "
                    "the density read back off the feature, stitch-and-remove a fresh mesh TWICE - "
                    "the first welds its duplicate vertices, the second finds nothing of its kind "
                    "to fix and the note has to say so rather than claim a repair - and refuse "
                    "density on a non-rebuild; then every other rebuild method with its own "
                    "density read back off the feature, 'offset' accepted by the accurate method "
                    "and refused on the rest, and a shrink-wrap close. SKIPPED(rig): the "
                    "close_holes refusal on a mesh that "
                    "stays open needs an UNFIXABLE open mesh, which nothing in this document can "
                    "build - every mesh here is watertight by construction"),
    "mesh_shell": ("hollow a scratch mesh - the volume DROPS, the body still reads watertight and "
                   "the thickness is read back off the feature, never echoed - then meet the "
                   "platform's own MESH_FAILED_HOLLOW refusal at a thickness past the half-wall "
                   "(measured: an over-thick shell does not quietly cut through, it fails)"),
    "mesh_smooth": "smooth a scan-quality mesh: the triangle count HOLDS STILL and the node coordinates move, which is why a count census cannot judge it",
    "mesh_separate": "split a two-shell mesh into its lumps - the pieces are the auto-named bodies read back from the component",
    "mesh_reverse_normal": "flip an inside-out mesh - confirmed by the signed volume changing sign, not by is_closed",
    "design_edit_timeline": ("roll the marker back a step and to the end, refuse a discard without "
                             "the confirmation, refuse an unknown feature and a bad group range, "
                             "and tag a feature with an attribute then delete it - the value and "
                             "the design-wide count are read back both ways, and a second delete is "
                             "refused; then the 'name@index' form a FeatureRef refusal hands back, "
                             "resolved against each object's OWN .index (the index design_get "
                             "publishes), with the neighbouring index refused as a miss. "
                             "SKIPPED(rig): the AMBIGUOUS-name refusal itself needs two same-named "
                             "timeline features, and no tool on this surface renames a feature, so "
                             "the sweep cannot mint the pair"),
    "param_set": "bump GimbalDia +33%, then restore it",
    "param_delete": "delete a scratch parameter",
    "view_switch_workspace": "switch to Manufacture, then back to Design",
    "cam_get": ("read the CAM job structure, and the recorded-probing slice on a job nothing has "
                "probed: the empty state with its reason named, a scope that invents no measure, "
                "and the units refusal"),
    "cam_edit_tools": ("add mill/drill/turning/center-drill tools; preset add/remove round-trip "
                       "with unit, refusal, and rollback gates; the summary census and the same "
                       "census narrowed by tool type, one tool's full parameter list, and the "
                       "LOCAL scope answering with libraries instead of tools; a fifth tool added "
                       "and removed with the count read back; and once the job is generated, "
                       "where_used naming the operations that cut with the mill and reporting NONE "
                       "for the turning tool nothing selected. The document library refuses to "
                       "host a new library and where_used refuses a shared scope - a shared "
                       "library has no operations, so an empty list there would read as 'none'"),
    "cam_create_setup": "create the milling setup on the Carrier in the vise",
    "cam_create_operation": "create the face, adaptive, silhouette, and drill operations",
    "cam_select_geometry": ("select the stock-top face, both silhouette branches (setup models and "
                            "named bodies), a whole scratch sketch and the bolt-circle holes; "
                            "refusals for a knob on the wrong kind, geometry through the wrong "
                            "input, and an edge where a face belongs. The pocket-recognition "
                            "selection is NOT driven unattended (running it coincides with the "
                            "Fusion process terminating); its 'pocket_filter_applied' publishes the "
                            "diameter/depth bounds in the CALLER'S own units, with "
                            "'pocket_filter_units' naming them beside the numbers"),
    "cam_edit_operation": ("edit the face operation's feed; then park the drill operation and "
                           "restore it - the suppression WRITE, with hasToolpath read back on both "
                           "sides of the set so the discarded toolpath is reported, not implied"),
    "cam_create_machine": ("build a run-stamped 3-axis machine into the Local library, find it in "
                           "the catalog, assign it to the setup, and refuse the duplicate name"),
    "cam_delete_machine": ("take the run's own machine back out of the Local library: the "
                           "confirm_name mismatch refused while it still exists, then the delete "
                           "proved by the library walk, the name re-resolve, and the catalog read "
                           "that listed it when it arrived"),
    "cam_edit_setup": "real stock + vise fixture bodies; WCS bound to the stock-center JO (bound read back); Haas VF-2 assigned",
    "cam_edit_folders": "organize the job into Milling and Drilling folders",
    "cam_reorder": "reorder the adaptive before the face op",
    "cam_activate_setup": "activate the setup",
    "cam_compare_operations": "compare the two operations",
    "cam_show_toolpath": "leave the toolpath visible on camera",
    "cam_generate": ("generate the toolpaths against the real part in the real fixture. The tool "
                     "takes no 'pump_seconds': CAM-7 confirms the kernel refuses to be pumped while "
                     "a generation runs, so completion is certified by the bounded cam_get_status "
                     "poll after this act, never by a sleep inside the call"),
    "cam_inspect_toolpaths": ("verdict false with named ops before generation, scoped check, "
                              "bogus-scope refusal, an over-cap max_results clamped to the tool's "
                              "own row ceiling, verdict true after generation, include_suppressed "
                              "widening the tally while the verdict's own set is reported apart "
                              "from it, a document-level answer taken with an empty setup present, "
                              "and the FILTERING measured against a real suppression - the tally "
                              "one operation shorter with the excluded count naming what it left "
                              "out, then the same read widened to count it in its own bucket"),
    "cam_get_status": "poll the generation to completion (empty toolpaths fail)",
    "cam_post": "post the NC program to disk",
    "cam_generate_setup_sheet": "write the machinist setup sheet with the file-landed gate",
    "cam_set_nc_comment": "stamp the NC program comment",
    "cam_save_template": ("save the setup as a run-stamped local CAM template - the stamp is what "
                          "keeps two overlapping runs off one name, since this tool always writes "
                          "a NEW template"),
    "cam_apply_template": "apply the template to a second setup",
    "cam_delete_template": ("take the run's own template back out of the Local library: the "
                            "confirm_name mismatch refused while it still exists, then the delete "
                            "proved by the library's asset walk and by nothing loading from the "
                            "deleted url, and the templates slice that listed it when it arrived "
                            "read back as no longer holding it"),
    "cam_delete": "delete a scratch operation; count diff",
    "design_export": ("export the machined part to STEP, then the whole design SPLIT per component "
                      "to STL with stl_binary read back off the options object the split path "
                      "created for each file - the branch that would otherwise report a clean "
                      "export while dropping the format knob; then every remaining format one file "
                      "at a time, each measured ON DISK rather than trusted to the API's success "
                      "bool, USD publishing the .usdz path Fusion appended for itself, STL with "
                      "its units baked in, and a sketch out through the 2D DXF branch"),
    "doc_insert_import": ("re-import that STEP from disk into the live design; then the DXF back "
                          "onto a plane as sketches, an SVG into a sketch made for it, and IGES / "
                          "SMT / f3d as solids - each with the format named explicitly, "
                          "which is what makes the last row (a format contradicting its file's "
                          "extension) a refusal instead of a silent mis-read"),
    "design_set_name": ("rename the machined part and re-find it by the name that landed, rename a "
                        "cameo occurrence with its instance name following, give a twin body the "
                        "name its sibling holds so the deduped '(1)' is what gets published, and "
                        "rename a MESH body - the kind reads 'mesh' and a fresh read of the "
                        "component's meshes carries the new name; the empty target and the root "
                        "component refused"),
    "design_get": ("final design read: the whole cast, stamped with the DOCUMENT it was read from "
                   "(sys_find_tool, which never touches the design, carries no such stamp), the "
                   "timeline slice the 'name@index' feature form is addressed from, plus the "
                   "material/appearance catalog at both zoom levels - the library census and one "
                   "paged library; an unloaded library name refused"),
    "drawing_create": ("meet every guard the drawing generator sits behind, each settled before the "
                       "tool reaches for a cloud source: the shaded style with no member to set, "
                       "the two centre annotations with no enum family on this build, a tangent-edge "
                       "value outside the Choice, manual creation with no template, and a sheet size "
                       "from the other standard - none of them creating anything, and the session "
                       "healthy afterwards. The creation path is cloud tier (it needs a saved source "
                       "design) and stays out of the default sweep"),
    "doc_get": "read the document identity before discarding",
    "doc_close": "discard the document on camera - clean teardown",
}

# Tools deliberately not swept unattended, each with its reason (the ledger's skipped rows). This is
# the policy-excluded bucket; PENDING (below) is the separate "not scripted yet" bucket - the ledger
# keeps that distinction honest.
EXCLUDED = {
    "sys_execute_script": ("gated off by design; the sweep proves the typed surface suffices - and "
                           "with it the beat for its DRAWING-document error tail (a raise inside a "
                           "drawing ends with 'Re-read the sheets before assuming this call changed "
                           "nothing.', a design one does not), which would need this tool driven "
                           "against two document kinds"),
    "sys_reload_addin": "restarts the server mid-sweep",
    "sys_request_selection": "waits on a human pick (user-present tier)",
    "drawing_update": "user-present tier (drawing docs)",
    "drawing_export": "user-present tier (drawing docs)",
    "drawing_get": "user-present tier (drawing docs); read-only - drawing_verify.py drives it",
    "drawing_add_sketch": "user-present tier (drawing docs)",
    "drawing_dimension": "user-present tier (drawing docs)",
    "drawing_edit_sheet": "user-present tier (drawing docs)",
    "drawing_insert_image": "user-present tier (drawing docs)",
    "design_set_mode": "irreversible parametric->direct conversion; not run unattended",
    "design_configure": "configuration table needs a SAVED document (a DataFile to carry it); opt-in tier - the appearance/material columns need that document too, and a body's material reads back only after the geometry catches up with the activation",
    # cloud tier: writes to the operator's real hub - opt-in only, never in the default sweep.
    "data_create_project": "cloud write to the operator's real hub (opt-in tier)",
    "data_create_folder": "cloud write (opt-in tier)",
    "data_upload_file": "cloud write (opt-in tier)",
    "data_get": "cloud read, hub-dependent (opt-in tier)",
    "data_get_upload_status": "cloud read (opt-in tier)",
    "data_download_file": "cloud read to the local disk (opt-in tier)",
    "data_move_file": "cloud write - relocates a real file in the operator's hub (opt-in tier)",
    "data_delete_file": "cloud destructive (opt-in tier)",
    "data_delete_folder": "cloud destructive (opt-in tier)",
    "data_switch_hub": "changes the active hub, closes docs (opt-in tier)",
    "doc_save_milestone": ("cloud write; needs a saved MODIFIED doc (opt-in tier) - the beat for "
                           "its two INDEPENDENT read-backs (cloud_tip_advanced beside "
                           "version_confirmed) rides that tier with it"),
    "doc_save": "versions to the cloud; needs a saved doc (opt-in tier)",
    "doc_save_as": "cloud write (opt-in tier)",
    "doc_copy": "cloud write (opt-in tier)",
    "doc_open": "opens cloud files; can wedge on CAM templates (opt-in tier)",
    "doc_insert_occurrence": "needs a saved cloud source in-project (opt-in tier)",
    "doc_insert_derive": "needs an ALREADY-OPEN saved cloud source to derive from (opt-in tier)",
    "doc_restore_version": "needs cloud version history (opt-in tier)",
    "doc_update_xref": "needs cloud external references (opt-in tier)",
    "doc_activate": "needs a second open document (opt-in tier)",
}

# Registered tools NOT yet scripted into STEPS - the honest "todo" ledger. SHRINK-ONLY: scripting a
# tool moves it out of here into STEPS. test_tool_verify_complete.py enforces that every
# registered tool is covered, excluded, or listed here, so a NEWLY added tool can't decay coverage
# silently - it fails the gate until someone scripts it, excuses it, or adds it here deliberately.
PENDING = frozenset()
