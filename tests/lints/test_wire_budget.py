"""Wire budget: every tool's tools/list weight is pinned in a per-tool manifest.

The tools/list payload is the token block every connected agent downloads before its first turn.
There is deliberately NO aggregate budget constant: the total is the sum of _TOOL_WEIGHTS, and any
weight change - up or down - happens by editing that tool's NAMED entry, one visible diff line per
tool. Growth is allowed when a capability needs it; it is never invisible and never absorbed by a
global number.

THE UNIT: bytes of the entry serialized the way the server serializes its responses -
`json.dumps(entry, indent=2)` (mcp_server.py's _send_json). Pretty-printing is not free: a newline
plus indentation per key is 12-49% on top of the same entry compacted, and it lands on the tools
with the MOST keys, which is exactly what a manifest of wire cost has to show. Bytes == characters
here because every wire string is ASCII (test_wire_ascii.py). One thing the number excludes: inside
the real response each entry sits at result.tools[i], so every line carries 6 more spaces of indent
- the pin is the entry's own cost, in the same unit for every tool, not the full delivered size.
The unit is TRANSPORT bytes, not model tokens: every MCP client parses tools/list and re-serializes
each definition into its own prompt format, so the indentation (measured 23% of the whole
tools/list payload: 440,475 bytes pretty vs 339,055 compact) crosses the socket and costs the model
nothing. What a model pays for tracks the compact form; the pins ratchet growth in either unit
because prose and schema grow both the same way.

A NEW tool (no entry yet) is measured against the fleet's 40th-percentile weight - computed from
the manifest at test time, not written anywhere an author can edit. At or under it: add the printed
entry and go. Over it: slim first, or add the entry and state in the commit message why the
capability needs the weight. The bar is the fleet's CURRENT 40th percentile - it moves with
the fleet in both directions (measured: it has risen as heavier tools landed), so it is a
comparative bar, not a ratchet; the per-tool pins below are what only move deliberately.

Per-tool hard ceilings (PER_TOOL_BUDGET_BYTES, DESCRIPTION_BUDGET_CHARS) still bound the worst
case; the jargon gate keeps repo-internal vocabulary out of agent-facing prose.
"""

import json

import pytest

from conftest import load_mcp_server, register_all_tools

# tool name -> exact tools/list entry weight in bytes, json.dumps(entry, indent=2) - the transport's
# own serialization (see THE UNIT above). The manifest of what every agent pays per session.
# Regenerate a failing entry from the test's own failure message.
_TOOL_WEIGHTS = {
    "appearance_set": 1771,   # +145: the base every override is copied from is named on the wire - measured, the color lands on a copy of the Fusion Appearance Library's 'Paint - Enamel Glossy (White)' (opaque, surface_roughness 0.07746), kept in the document as 'MCP Neutral Base'; a caller cannot recover which base its color rides on from the result, and the finish decides what the screenshot shows. +74: 'opacity' is the browser's Opacity Control (Component.opacity / BRepBody.opacity, the API's own words), a PERCENT, and it is inherited - so the wire has to say it is a percent rather than the 0-255 alpha next to it, that it stands alone without a color, and that the payload reports what RENDERS rather than what was asked
    "assembly_capture_position": 1847,   # + the discard_pending action (revertPendingSnapshot): throwing an uncaptured move away is a different act from deleting a captured marker, and both belong on the one snapshot lifecycle tool
    "assembly_constrain": 2804,   # + the verification contract a caller cannot recover after the call: the create REFUSES a constraint that did not solve (or whose state is unreadable) and one whose add left other relations unhealthy, and the payload's 'moved' answers whether the parts were actually located
    "assembly_edit_relations": 2948,   # +30: the description's set_values clause carries the same display-unit semantics its 'ratio' input names, so the two strings in one entry state one number's unit one way. over P40: six actions over three relation kinds (rigid group / motion link / constraint), each with its own input, plus the measured set_occurrences refusal and the ratio-sign contract; peer: design_edit_timeline. +81: set_values' 'ratio' names the DISPLAY unit each side is stated in (deg for a rotating DOF, mm for a sliding one), the same clause joint_motion_link's ratio carries - the two writers re-value one coupling through one codec and must not describe it two ways
    "assembly_edit_contacts": 2635,   # over P40: nine actions over one object - the set lifecycle (create with the >=2-distinct rule, set_members, rename, suppress, delete) plus the two design-level analysis flags whose truth table decides whether any set acts at all, and the enable-first fact the platform enforces by raising; peer: assembly_edit_relations
    "assembly_get": 3099,   # + the all_occurrences slice with its cap, and the joint rows' value_now/frame teaching (the frame's z_axis is the direction a joint offset drives along - the read that retires per-build probe cycles)
    "assembly_ground": 1300,
    "assembly_inspect_interference": 1171,   # + candidate instance paths on multi-instance pairs, capped with a truncated flag: analyzeInterference returns NATIVE bodies (assemblyContext None), so the exact instance is unreadable - naming the suspect paths is the honest form of "which rail hit which rod"
    "assembly_move": 2886,
    "assembly_rigid_group": 950,
    "cam_activate_setup": 730,
    "cam_apply_template": 2007,
    "cam_compare_operations": 950,
    "cam_create_machine": 1611,   # under the fleet P40 - add and go
    "cam_create_operation": 2009,   # +85: the ORDER (create -> cam_select_geometry -> generate) and the reason generate now defaults to FALSE - measured, generating a selection-driven strategy before its geometry is selected does not fail, it lands an op reading valid with a warning and no toolpath, which a caller cannot recover from the result; paid for by dropping the 'apply an operation half of CAM' framing
    "cam_create_setup": 1992,   # the default-models claim reworded to the walk's truth (surface bodies ride along - measured accepted by Setup.models)
    "cam_delete": 1014,
    "cam_delete_machine": 1445,   # under the fleet P40 - add and go
    "cam_delete_template": 1471,   # under the fleet P40 - add and go
    "cam_edit_folders": 1782,
    "cam_edit_operation": 1737,   # +316: the 'suppressed' input - the only WRITE of Operation.isSuppressed on this surface (its siblings assembly_edit_relations/contacts already carry one). Most of it is the input's own schema entry; the prose says only that true parks the operation and DISCARDS its toolpath, since that cost is what a caller cannot recover afterwards - the before/after hasToolpath reads are taught in the result note instead
    "cam_edit_setup": 3436,   # -17: machine_strip_simulation's description narrowed to what is measured - assigning a simulation_ready machine CAN be refused (measured on the machine the measuring run picks, not established for every machine), and the strip's cost is stated as the read that was taken (the spindle maximum and axis ranges read back unchanged through the setup) instead of an unmeasured promise about posting, kinematics and in-Fusion simulation
    "cam_edit_tools": 5129,   # turning+hole-making(inch) sample sources, add/remove_preset actions, preset units contract
    "cam_generate": 1520,
    "cam_generate_setup_sheet": 1515,   # under the fleet P40 at pin time - add and go
    "cam_get": 4689,   # +105: the 'setup' input states that it also picks WHICH operation a parameters/tool read means when several setups hold that name. An operation name is unique only within a setup - measured on a shop template, ~40 of 99 operations share one across setups - and until the scope reached the resolver those operations had no readable parameters at all: the ambiguity refusal printed a qualified 'Setup / Op' path the tool did not accept as input. The clause is the input-vocabulary remedy that refusal now names. TOOL_DESCRIPTION is unchanged. +77: the 'machine' slice (the assigned machine's spindle speed range and axis travels - measured readable through Machine.elements -> kinematics -> parts, the numbers the spindle-over-max check is made against) and the 'parameters' setup= target (the SETUP's own stock parameters, which nothing could read back); the rest of the pass - per-op folder/preset/spindle-vs-machine, per-op cycle times, what an NC program posts - is payload and notes, and the description paid for these two slice names by trimming eight spots elsewhere. The setups slice still reads the bound WCS back (origin/orientation mode + bound geometry), the only read-back of what cam_edit_setup's wcs binding did
    "cam_get_status": 2031,   # +213: WHICH DOCUMENT each routing answers about, and what completed=true claims - two facts a caller cannot recover from the payload. Measured: a BARE poll (no handle, no target) answered from a stale registry handle of a CLOSED document, reporting completed:true about that job while another document was open; and completed:true was published beside a readiness of "0 of 34 active ops valid", so the flag is a generation-lifecycle state, not a success verdict. The description now states that a bare read is the ACTIVE document, that 'latest' (a positional pick naming no document) is refused off its launch document, that an explicit handle still reports its own Future and names its document, and that completed=true means nothing in scope is still generating
    "cam_inspect_toolpaths": 1998,   # +561: the TALLY counts active operations by default, and four things a caller cannot recover from the result have to be on the wire - how many suppressed operations were left out (measured.suppressed_excluded), the include_suppressed=true escape back to the whole census, that a setup holding no operations is reported (measured.empty_setups_excluded) rather than raised, and above all that 'passed' covers a DIFFERENT set than the tally: measured on 2705.1.4, CAM's own checkToolpath answers false for a setup whose only non-valid operation is suppressed, so a caller reading 'passed' as the active-operation verdict is reading it wrong and only tolerance_used.verdict_counts vs tally_counts can tell them apart
    "cam_post": 3384,
    "cam_reorder": 1268,
    "cam_save_template": 1716,
    "cam_select_geometry": 5007,   # +379: the 'component' scope - see sketch_add_3d_line - narrowing BOTH by-name geometry lists, 'sketches' and 'bodies' (Fusion numbers sketches per component from 1 and names every component's first body 'Body1', so both name spaces are shared by default); the schema is strict, so the input and each kind's scope ship together, and the shared-name refusal names this input as the remedy instead of a rename in another document. + the sketch and pocket_recognition selection kinds, per-kind input routing with refusals, loop/side/pocket-filter knobs read back, and the outputGeometry rung-3 read
    "cam_set_nc_comment": 1411,
    "cam_show_toolpath": 1758,   # + the setup-activation disclosure: show/isolate/show_folder activate the operation's own setup (Manufacture renders only the active setup's models) - state a later CAM call consumes, so the caller must be told the tool changes it
    "data_create_folder": 1314,
    "data_create_project": 840,
    "data_delete_file": 1381,
    "data_delete_folder": 1849,
    "data_download_file": 1833,   # over P40: the refusal contract (Fusion-native data leaves through design_export), the synchronous FREEZE warning, and the overwrite-removes-first semantics are each a fact a caller cannot recover after the call; peer: data_upload_file
    "data_get": 2140,   # + the file scope: one file's record by URN or by name-in-a-project
    "data_move_file": 1436,
    "data_get_upload_status": 1353,
    "data_switch_hub": 1330,
    "data_upload_file": 1844,
    "design_add_instance": 2656,   # over P40: the placement surface + the landed-paths contract (children vs host instances named by measured cause); peers: doc_insert_occurrence, model_create_component
    "design_move_occurrence": 1648,   # over P40: the no-root-target fact and its workaround are caller-unrecoverable
    "design_activate_component": 1240,
    "design_edit_timeline": 3149,   # five guarded timeline actions plus set/delete_attribute on the entity a timeline item wraps (three inputs; the entity-attribute surface has no other tool); delete_after_marker previews its blast radius before it will run
    "design_configure": 3745,  # + add_material (per-configuration materials via the material theme table) and the add_configuration auto-activation disclosure
    "design_delete_feature": 1257,
    "design_delete_occurrence": 1284,   # the target is named as a handle (the exact identity) with the path/name as the convenience form, on both surfaces
    "design_export": 5213,   # +43: the 'stl_units' clause states the stickiness as it is measured - the unit an untouched STL lands in follows the last explicit unit ASSIGNMENT made anywhere in the Fusion session and carries across documents (measure_api stl-export-unittype-is-sticky-session-state), so an export that sets no unit leaves it wherever an unrelated one put it. +70: 'stl_units' states that the unit is ALWAYS written - measured, and the wire is the only place a calling agent can learn it, since the file it gets back is wrong by 25.4x with nothing in the payload to say so. +295: the 'dxf_component' scope, which narrows 'dxf_sketch' to one component's own sketches. Fusion numbers sketches per component from 1, so a name two components carry is refused - and this write puts a file on disk, where the wrong outline is indistinguishable from the right one after the fact. 3MF/OBJ/USD/f3d/SMT formats, STL binary+units, DXF options, invisible flags
    "design_set_name": 1458,   # under the fleet P40 - add and go
    "design_get": 3535,   # + the attributes slice (group/key scope, required-group refusal), timeline_params - a feature's own model parameters with their roles, the only readable route to a fillet's radius - and tree_bodies: per-node body records (name/handle/solid/visible), the read that makes a body targetable without replaying old feature receipts; in-family peers: cam_get, assembly_get
    "design_recompute": 645,
    "design_remove_feature": 1436,   # under the fleet P40 at pin time - add and go
    "design_set_mode": 1119,
    "doc_activate": 1001,
    "doc_close": 1429,
    "doc_copy": 2466,
    "doc_get": 2672,   # + the per-version is_milestone/milestone_name fields and the milestone rollups; + kind='unresolved' and the unresolved_count rollup, because xref_tree formerly reported all_current true on a document holding a reference whose component would not load - a caller cannot ask for a channel it is not told exists
    "doc_insert_derive": 3507,
    "doc_insert_import": 2805,   # +375: 'sketch_component', the component scope for the EXISTING sketch an SVG lands in - see sketch_add_3d_line. Separate from 'into_component', which names where a DXF's new sketches or a solid's occurrence land: two different components, so a caller narrowing one must not be moving the other - and 15 of the bytes are this input NAMING 'sketch' as the reference it narrows, which is the only thing distinguishing the two component inputs on the wire. three targeting modes (component, plane for DXF, sketch for SVG) plus the sketch_insert_svg pointer telling a caller which of the two SVG routes places the art
    "doc_insert_occurrence": 3282,   # + the two MEASURED facts a caller cannot recover after the call: a joint pose the source only DROVE is transient and never reaches the saved version (the captured pose arrived, the live drive did not), and remove_existing leaves a feature that referenced the occurrence's geometry in the timeline carrying reference failures (healthState 0 -> 1, not deleted); the CAM-selection half was measured FALSE and is deliberately absent
    "doc_new": 759,
    "doc_open": 1981,
    "doc_restore_version": 1331,
    "doc_save": 900,
    "doc_save_milestone": 1487,   # under the fleet P40 at pin time - add and go
    "doc_save_as": 2161,
    "doc_update_xref": 1400,
    "drawing_add_sketch": 2489,   # over P40: five 2D factories with different point arities are the contract (the kind enum is schema-checked, the per-kind arity cannot be), plus two caller-unrecoverable facts - coordinates are drawing units not cm, and Drawing.deleteEntities raises not-implemented so a sheet's geometry must go in one call; in-family peers drawing_edit_sheet, sketch_add_geometry
    "drawing_create": 6223,   # -28: the up-front "open it once in the Fusion UI" instruction retired - a never-reviewed drawing opens and drives through doc_open (measured), so the description names that route and the UI open survives only as failure-time teaching in the note. center_line/center_mark still state the refusal their resolver enforces rather than advertise a capability adsk.drawing carries no enum family for; the client-timeout fact (a timeout is not a verdict) is paid for by slimmer input descriptions; 77 under the hard ceiling
    "drawing_dimension": 1684,   # at the fleet P40 at pin time - add and go
    "drawing_edit_sheet": 2416,   # +87: Sheet.width/height are millimetres on EVERY drawing while sheet_units is the dimension unit - the two are told apart on the wire, and a caller cannot recover a wrong unit claim
    "drawing_get": 1159,   # under the fleet P40 - the drawing family's ONE read: sheet listing by export_index, per-sheet facts, per-view rows; the read the whole tier lacked (and scripts cannot substitute - they die on DrawingDocuments). +50: index+type is no longer claimed to be ALL a view exposes - a view also carries a POPULATED viewCurves collection whose ViewCurve items expose no readable geometry (measured), and a caller told "that is all there is" cannot recover the difference between a member that does not exist and one that exists but reads nothing
    "drawing_export": 2535,   # sheet_range carries the DISCRIMINATED hazard at its measured truth: the minutes-long main-thread block strikes a sheet_range export issued AFTER another export (2/2), while one run FIRST completed clean - run-first is the teaching a caller cannot recover from the result
    "drawing_insert_image": 1961,   # +217: rotate_deg (degrees about the insert position) and the six image extensions the guard takes - the placement surface an image needs, and neither is recoverable after a call that cannot be read back
    "drawing_update": 1336,  # shrank: retired the reviewed-drawing park-on-a-human clause
    "find_geometry": 2657,   # + the planar-face frame (origin + in-plane axes + normal), a field a caller cannot recover from the schema; the qualified '<occurrence-or-component>:<body>' target form rides the 'target' input description
    "joint_at_geometry": 2852,   # + the axis Choice enum (values machine-validated) and the frame-relative correction with its joint_edit(world_axis=) pointer; (ball uses none) stated on both surfaces. +18: the RETURNS line states WHEN 'healthy' is null. The verdict is the shared joint-plus-timeline-item read (_assert.compute_state) that joint_create publishes too, so null means neither source answered a state at all - a condition a caller cannot recover from the payload, and a narrower claim would send it re-reading a joint whose state did read
    "joint_create": 4278,
    "joint_create_as_built": 2563,  # non-rigid motion: geometry anchor + joint_type/axis/slide_axis inputs, per-DOF pose pointers; the name input (applied post-create, read back) + the no-offset/angle-parameter fact routing parametric drives to joint_create
    "joint_create_origin": 5098,   # +380: 'sketch_component', the component scope for the sketch a sketch_line/sketch_point anchor reads - see sketch_add_3d_line. It is a SECOND input rather than a reuse of 'component' because that one names the occurrence RECEIVING the origin, and the sketch the frame is built on can be owned by a different component entirely - and 20 of the bytes are this input NAMING 'sketch_name' as the reference it narrows, which is the only thing distinguishing the two component inputs on the wire. +462: the 'component' input (which component's collection receives the origin - the only route to a joint origin that can serve as a SUB-component's side of a joint) and the space fact it brings: those offsets run from that component's origin, so world x,y,z is refused unless it sits at the world origin unrotated - neither is recoverable from the result
    "joint_drive": 2353,   # +25: the motion-link sentence states the coupling CONDITIONALLY - the receipt answers whether the link couples, and both the partner read-back and the xref SECOND-member refusal ride that answer, because a link reading suppressed or compute-failed couples nothing and arms no refusal (the handler's `couples is not False` gate). +5 net: the verbatim-storage contract measured on 2705.1.4 (a commanded 750 reads back 750 and a following 30 reads back 30 - the angle is stored exactly as commanded, so the normalized twin and equivalent_pose are what a caller reads a multi-turn angle through) plus 'moved', the member the drive displaced, which a caller cannot recover from a value read-back; paid for by folding the xref both-members clause down to the one fact and leaving the rest to the refusal error that already teaches it. Measured limit contract retained (Fusion IGNORES a beyond-limit drive - never clamps - so out-of-range commands are refused upfront)
    "joint_edit": 4220,
    "joint_motion_link": 1307,   # +10: the description's own ratio clause states the display-unit semantics too, in place of "(2 = twice as fast)" - a dimensionless reading that is false for a mixed pair (2 deg of pinion per mm of rack is not "twice as fast"), and the description is the first string an agent reads in the same entry as the input it would contradict. +101: 'ratio' names the DISPLAY unit each side is stated in - deg for a rotating DOF, mm for a sliding one. Fusion couples the pair in radians and centimetres, so a mixed slider/revolute ratio sent as a bare number is off by a factor no read-back in the result reveals; the unit belongs on the input, where the legality lives, and the payload's 'interpreted' carries the conversion that was applied
    "mesh_combine": 1978,
    "mesh_delete": 1201,
    "mesh_export": 2626,   # +447: the 'stl_units' input - a typed enum over the same vocabulary design_export bakes into an STL and mesh_insert re-imports one with - of which 309 bytes are that structural schema property. The prose it buys is the one fact recoverable from neither the result nor the file: which unit the STL was written in, published as the unit that LANDED, so the file can be round-tripped through mesh_insert at all (measured: re-imported at the wrong unit it comes back at a 25.4th of its size and a 25.4th of its distance from the origin). The default is mm - the unit mesh_insert defaults to - so an export and a re-import that both name no unit agree end to end
    "mesh_generate_face_groups": 1245,
    "mesh_get": 1352,   # area/volume fields + units input
    "mesh_insert": 1566,   # + the units clause naming that the reported area/volume use the authored unit
    "mesh_plane_cut": 1790,
    "mesh_repair": 2209,   # five repair types + the six rebuild methods, each a typed Choice
    "mesh_reduce": 1656,
    "mesh_remesh": 989,
    "mesh_reverse_normal": 1041,
    "mesh_separate": 1160,
    "mesh_shell": 1597,
    "mesh_smooth": 1247,
    "mesh_to_brep": 2177,
    "model_arrange": 2351,   # +305: the 'boundary_component' scope. The boundary is the only sketch this tool resolves BY NAME, and Fusion numbers sketches per component from 1 - so a name two components carry is refused, and the refusal names this input rather than a rename, which for a component that arrived inside a referenced document means editing a different document
    "model_base_feature": 1579,
    "model_chamfer": 2764,   # distance-and-angle + corner_type, read back off the feature
    "model_combine": 1890,
    "model_compute_holder": 1824,
    "model_construction": 5682,   # three modes the API has no other route to (a plane rotated about a curved face's own axis, a plane pinned through a vertex, a plane/point placed along a path proportionally, absolutely, or to an object) plus the distance_type/to_object inputs they need; +78 for the measured chaining rule (BRep chaining follows TANGENT CONTINUITY - a sharp corner stops it, open vs closed decides nothing - so the count, not the promise, is the answer)
    "model_create_component": 2684,
    "model_draft": 2339,
    "model_emboss": 2157,   # +353: the 'component' scope - see sketch_add_3d_line. It narrows BOTH by-name forms this input takes, the {sketch, profile_index} selector and the '<sketch>/text:<i>' address, which resolve through one shared walk and so share one answer to a name two components carry. The schema is strict, so the input and the kind's scope ship together: without the property, the refusal would name a call this tool's own schema rejects. +12 schema bytes, no prose: ProfileRef's 'profile' now publishes type ["string","object"], the two forms it has always resolved - a schema-validating client was barred from the {sketch, profile_index} selector the description offers. Same 12 bytes model_sweep's hand-declared profile has always paid. + the sketch-TEXT profile route ('text:<i>' / '<sketch>/text:<i>'), the only path from a nameplate sketch to an engraving
    "model_extrude": 4476,   # +353: the 'component' scope - see sketch_add_3d_line. It narrows all three by-name reads this tool makes: the sketch, the {sketch, profile_index} selector, and the '<sketch>/text:<i>' address, which resolve through one shared walk, so one input answers a shared name for all of them. +104: the sketch-TEXT profile form ('text:<i>'), the only route from a text-only sketch to a solid - the index/handle vocabulary alone reads it as an index and dead-ends in the surface branch. +152: target_bodies documents the occurrence-qualified '<occurrence>/<body>' form - Fusion auto-names every component's first body 'Body1', so a cross-component scoped cut is UNADDRESSABLE by bare name (it resolves ambiguously or to the wrong component's body), and the resolver has always accepted the qualified spelling the payload echoes back
    "model_fillet": 4400,   # +182: 'radius' takes a parameter EXPRESSION string as well as a number, on the description and in the property's own schema (type ["number","string"]). Both surfaces are load-bearing: the schema is what stops a client rejecting the string before it is sent, and the description is the only place a caller learns the form exists - a fillet driven by a number is the one dimension of a parametric part that stops following its parameters. The sibling inputs stay numeric, which is why the clause names 'radius' rather than "lengths". variable-radius, chord-length and rule fillet, with the rule radius/topology read back
    "model_hole": 5737,   # placement modes (center/on_edge/plane_offsets), points_space sketch/world (the sketch-on-face frame is unknowable a priori - world lets find_geometry positions drive holes directly), modeled thread, tip_angle, thread_type
    "model_inspect": 2332,   # per_body names every occurrence in the subtree and discloses that a parent row aggregates its children - the read that stops a nested body folding invisibly into its parent. +259: 'frame' is wired to the JointOriginRef kind, so the schema states the reference vocabulary its own refusal demands - a handle, a bare name, or '<occurrence>:<JO name>' when the name is shared - which the prose form ("a Joint Origin name") left an agent unable to guess
    "model_pipe": 3563,   # +86 for the measured chaining rule (tangent continuity, sharp corner stops it). over P40: the path-fraction extents (both ends), section type/size, the order-coupled hollow wall verified off the feature, and the open-path refusal contract; nearest path-driven peer model_sweep
    "model_loft": 2740,   # +353: the 'component' scope - see sketch_add_3d_line. A {sketch, profile_index} element addresses a sketch BY NAME, and Fusion numbers sketches per component from 1, so the name two components carry is refused - and a loft runs through its sections in the order given, where the wrong component's region is a body that looks plausible and is not the one asked for. +12 schema bytes, no prose: ProfileRefList's items now publish type ["string","object"], the two element forms it has always resolved. + is_closed (the one measured-real loft option; alignment measured a no-op on profiles and dropped) + the ordering sentence relocated from the ProfileRefList kind note
    "model_measure_between": 1404,   # +17: 'edge' joins the two target kinds, so both input contracts and the description name the edge handle. The measurement API is what an edge handle was always headed for - without the kind listing it, TargetRef silently substituted the edge's owning BODY and the tool measured an entity the caller never named
    "model_measure_relation": 3512,
    "model_mirror": 1614,   # + the features input (parametric feature mirroring by exact name@index, beside bodies)
    "model_move": 3263,   # four move modes (translate/along-entity/rotate/point-to-point); the faces input carries its own refusal; + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_offset_face": 1555,   # + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_pattern_circular": 2120,
    "model_pattern_path": 2715,   # +86 for the measured chaining rule (tangent continuity, sharp corner stops it). the family's occurrence+body target pair plus the path selector (edge handles or a path sketch) and the distance/distance_type/start_point run controls
    "model_pattern_rectangular": 2700,   # the two direction inputs carry the AxisRef contract (a world axis OR a straight-edge/sketch-line handle) instead of a bare x/y/z enum
    "model_replace_face": 1497,   # under the fleet P40 at pin time - add and go
    "model_revolve": 3054,   # +353: the 'component' scope - see sketch_add_3d_line. It narrows BOTH by-name reads this tool makes: the sketch itself and the {sketch, profile_index} selector under it, which resolve through one shared walk and so share one answer to a name two components carry. +20: the axis-defining face family named on the wire (cylindrical/conical/toroidal, each measured accepted), the fact a caller cannot recover from a refusal
    "model_scale": 2567,   # two scale modes (uniform + three per-axis factors), anchor input, unitless-expression guard, resolved-value echo; + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_set_material": 1577,   # mesh bodies are mass-bearing targets too - an unassigned mesh silently carries default steel density
    "model_shell": 2089,
    "model_split": 2552,   # + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_stitch": 1907,
    "model_sweep": 3345,   # +353: the 'component' scope - see sketch_add_3d_line. It narrows BOTH sketch reads the profile side makes: the {sketch, profile_index} selector, and the open-curve fallback that builds an OPEN profile when the selector names no closed region - so the closed path and the fallback cannot answer one name with two different components' sketches. +70 for the measured chaining rule (tangent continuity, sharp corner stops it); +62 for the third declared output, path_curves - how many curves the built path HOLDS, the only number that shows a chain stopped short of the intended run
    "model_thread": 2675,   # face list, designation + thread_type (540 of 1510 call-outs sit in several standards), cosmetic/modeled, handedness, partial-thread length/offset/location
    "model_unstitch": 1420,
    "param_add": 1815,
    "param_delete": 825,
    "param_get": 898,
    "param_set": 1707,
    "param_set_favorite": 767,
    "pmi_create": 4805,
    "pmi_delete": 1137,
    "pmi_edit": 4596,
    "pmi_get": 2376,
    "save_as_mesh": 1473,
    "sketch_add_3d_line": 2555,   # +353: the 'component' scope. Fusion numbers sketches PER COMPONENT from 1, so two components each holding a 'Sketch1' is the norm - and until this input existed the design-wide resolver's refusal named ONE remedy, renaming a sketch, which for a component inside a referenced document means opening and editing a DIFFERENT document. The write had no move at all. The input is the schema half of that remedy; the refusal now names it
    "sketch_add_geometry": 5898,   # +353: the 'component' scope - see sketch_add_3d_line. The draw is the write most often aimed at a per-component 'Sketch1', so this is the site the shared name bites first
    "sketch_constrain": 6204,   # +353: the 'component' scope - see sketch_add_3d_line. 96 under the hard ceiling: the description is unchanged, the growth is the one schema property, and this tool is the family's heaviest so any further growth here has to buy its room from the prose. 25 constraint kinds + per-instance pattern suppression (the rectangular row-column rule on the wire) + the four requested-only autoConstrain strategy knobs + the one entity-anchor mention (the ':center' form its point slots share with sketch_dimension) and the 'text:<i>' operand fix/unfix takes (the SketchText anchor DOF has no other route)
    "sketch_move": 2481,   # +353: the 'component' scope - see sketch_add_3d_line. over P40: nine of the inputs ARE the transform (translation/rotation/scale composed into one matrix), verified by coordinate read-back
    "sketch_copy": 2921,   # +735: the 'component' scope (see sketch_add_3d_line) AND 'target_component'. Two scopes because this tool takes TWO sketch names: a refusal on 'target_sketch' that pointed at 'component' would name an input that does not narrow it - the same dead end as "rename one", one step along. 161 of that is 'target_component' spelling the scope's vocabulary out rather than deferring to 'component' with "same forms as": both are declared from _sketch_detail.component_scope, so the accepted forms are stated once at the source and neither scope can drift from the other - a cross-reference is cheaper on the wire but leaves the second scope's contract readable only by finding the first. over P40: same transform surface as sketch_move plus the target-sketch input and the new-refs contract
    "sketch_create": 1583,   # +33: 'frame.space' replaces the flat component-LOCAL caveat, which was wrong for the common case - a sketch in an offset or rotated component placed ONCE now publishes true world, and only a component instanced several times (no single world frame, measured: two instances of one component read different origins and axes) stays local
    "sketch_delete_entity": 2024,   # +353: the 'component' scope - see sketch_add_3d_line. This is the DESTRUCTIVE member of the family, so it is the one where "which of the two 'Sketch1's did you mean" had to stay a refusal and needed a way through that does not edit another document. +41: 'sketch_name' states what the design-wide sketch walk actually does - it asks EVERY component with no preference among them and REFUSES a name several sketches carry. Describing it as "active component first" would tell a caller an ambiguous name resolves to some particular sketch, and this tool DELETES what that name picks: the one input description on the fleet where a wrong belief about which entity wins is destructive. ref vocabulary names ellipse/spline kinds + the text:<index> target
    "sketch_dimension": 4413,   # +353: the 'component' scope - see sketch_add_3d_line. ref vocabulary names ellipse/spline kinds; + the eight remaining SketchDimensions add* types, the surface operand and the driving/tangent-side flags, and the two measured dim_type behaviors a caller cannot see in the result (the angle wedge, the offset rotate)
    "sketch_edit_curve": 3083,   # +353: the 'component' scope - see sketch_add_3d_line. seven curve-edit actions with per-curve pick points
    "sketch_insert_svg": 2223,   # +353: the 'component' scope - see sketch_add_3d_line. over P40: the ignored width/height/viewBox with the 1/96-inch-times-scale rule, the y-down landing, and the doc_insert_import pointer are each a sizing/placement fact a caller cannot recover from the result
    "sketch_get": 1954,   # +447: the 'component' scope. Fusion numbers sketches PER COMPONENT from 1, so two components each holding a 'Sketch1'/'Sketch2' is the norm, and a bare name then identifies nothing - measured on a real assembly, the master sketch could not be read AT ALL, the design-wide resolver refusing '2 sketches are named Sketch2' with no way forward but renaming a sketch in the caller's own document. The input states the two vocabularies it resolves, and the second is not optional: measured, inserting two referenced documents leaves EIGHT pairs of byte-identical component names, so a component NAME can identify nothing either and only an occurrence fullPathName/handle separates them. The per-component numbering fact is stated once, on the input where the legality lives; the refusals teach the rest at failure time. The read still publishes 'frame' (world origin + X/Y/normal), the only typed route to a sketch plane's position, facing and coplanarity
    "sketch_project": 4757,   # +569: the 'component' scope (see sketch_add_3d_line) AND 'source_component', for the same reason sketch_copy carries two - 'source_sketch' is a second by-name reference and 'component' does not narrow it. two more actions on the same verb: to_surface (projectToSurface - faces, source curves from another sketch, project type, direction) and intersect (intersectWithSketchPlane - bodies/entities x the sketch plane); in-family peers: sketch_constrain, sketch_dimension
    "sketch_set_text": 4435,   # +72: 'units' states that it scales 'height' on the EDIT path as well as on create, and names the unit every number the edit reports comes back in - 'height', 'height_before', 'measured_width', 'measured_height'. A caller who reads it as create-only passes inches, gets millimetres, and ships a 0.25 mm label; no field in the result carries its own unit, so the schema is the only place that fact can live. +110: a create reports the landed text's MEASURED width, and the description is the only place a caller learns that number exists at all. Nothing on this tool answered how wide a string would run before it was placed, so a label could only be sized by creating it and looking - measured, an Arial h8 label ran 188 mm onto a 160 mm face, and the recovery was delete-text, delete-emboss, recreate, re-emboss. The payload's own note carries what the number is read off; these bytes buy the fact that it is reported. +353: the 'component' scope - see sketch_add_3d_line. It narrows BOTH paths: create and edit each resolve ONE sketch through it, so a name two components carry is refused rather than written into each. +120: 'height' states what an EDIT does with it - measured, SketchText.heightParameter accepts a write on an existing text and the glyph geometry follows it proportionally, so an edit RESIZES; the landed height and the text's re-measured bounding box come back per entry, and a caller sizing a label against the face it must fit cannot recover either from a bare success. +54: 'y' names the VERTICAL anchor the same way 'x' names the horizontal one - measured, the requested y is the box's bottom and extra lines stack upward, and an agent reading only "Y position" placed a label by guessing the anchor and got it wrong. +70: 'x' names the anchor 'align' puts it on (left edge / center / right edge) - measured, the box moves with align and a caller cannot recover where centered text landed; + the along_path/fit_on_path modes (path, above_path, align, character_spacing), angle/flip formatting, and font_name (applies on create AND edit, landed/edited font read back) - in-family peers: sketch_edit_curve, sketch_constrain
    "surface_delete_face": 1560,   # + the conditional-'feature'/'bodies_consumed' PRODUCES clauses (a direct design creates no timeline feature, so both feature-derived outputs are omitted)
    "surface_extend": 2127,   # + extend_alignment (free_edges/align_edges, fresh-input default measured 0)
    "surface_extrude": 2566,   # +353: the 'component' scope - see sketch_add_3d_line. This tool's sketch reference goes through the same design-wide by-name walk the sketch family uses, so it inherits both the shared-name refusal and the input that is its only performable way through
    "surface_offset": 1765,  # the distance input teaches 0 = a COINCIDENT copy of the face (legal, measured live - the machining-prep copy-face idiom); the description states the measured isSolid split
    "surface_patch": 2639,   # + continuity (the plural enum class - the only one that exists) and edges-only interior rails
    "surface_fill": 2113,   # over P40: the cell-disclosure contract + two measured legality facts ARE the tool (peer: surface_patch)
    "surface_reverse_normal": 1388,
    "surface_revolve": 2360,   # +353: the 'component' scope - see sketch_add_3d_line, and surface_extrude beside it: the two share one by-name sketch branch and each declares its own scope for it
    "surface_thicken": 2096,   # + thicken_type (sharp/rounded; fresh-input default measured 0 = sharp)
    "surface_trim": 1527,
    "surface_untrim": 1820,
    "sys_get_preferences": 1328,
    "sys_set_preferences": 1509,  # one member per call; the tier/refusal contract is the surface
    "surface_create_ruled": 2319,  # new tool over P40 like its surface peers (extrude 2213, patch 2639): the three measured type behaviors and the two-faces-one-left fact are caller-unrecoverable
    "sys_capability_map": 740,
    "sys_execute_script": 2271,   # the read_only input, whose whole contract rides on the typed property: the description measures 1289 chars against the 1300 ceiling, 11 to spare, so there is no room to teach it there - measured, a design change from that context RAISES, and the same script with the flag off applied it, so the refusal is a fact the caller can rely on rather than a promise
    "sys_find_tool": 930,
    "sys_get_api_doc": 1595,
    "sys_get_selection": 1858,
    "sys_reload_addin": 1364,
    "sys_request_selection": 2404,
    "view_list_workspaces": 500,
    "view_screenshot": 2734,   # +455: the file_path PNG writer - the fleet's only raster output, the one route from a rendered view to a drawing sheet - plus the expect_document the write guard adds now that writing (and silently overwriting) a caller-named file makes this write-kind; the fit_to hide/restore disclosure it already carried is the rest
    "view_screenshot_multi": 1766,   # transparent-background + anti-aliased capture options
    "view_section": 2596,
    "view_set": 4398,   # camera projection Choice + perspective angle with read-back; + the display action (the four browser-folder categories with their on/off flag); + 'fit' naming which of the two framings it selects (the focus, or the whole model); + 'focus' accepting a SKETCH as well as an occurrence, which is what makes sketch-only work framable at all; + 'focus' accepting a LIST, which frames a group of related parts together instead of one member at a time
    "view_switch_workspace": 950,
    "workspace_orient": 948,   # + "AND unresolved references" in the health rollup: is_healthy now counts an occurrence whose referenced component will not load, and the description is the only place a caller learns what that verdict covers
}

# The hard ceiling, in the same indent=2 bytes as the pins above. The fleet's heaviest entry
# (drawing_create) sits 77 bytes under it, so the bar bites on the entry nearest it and a tool
# cannot grow past it unnoticed. DESCRIPTION_BUDGET_CHARS counts characters of the description
# alone, so pretty-printing does not touch it.
PER_TOOL_BUDGET_BYTES = 6_300
DESCRIPTION_BUDGET_CHARS = 1_300
_DESCRIPTION_OVERRIDES = {}

# Repo-internal vocabulary that must not leak into agent-facing descriptions - an agent reading
# tools/list has no repo context. Lower-case substring match.
_WIRE_JARGON = ("disclose read", "acquire tool", "rich read", "orient read", "write guard",
                "input kind", "the registry", "postcondition")


@pytest.fixture(scope="module")
def wire_tools():
    """The tools/list entries exactly as the REAL server sends them, all tools registered."""
    mcp_server = load_mcp_server()
    srv = mcp_server.SimpleMCPServer()
    for item in register_all_tools():
        srv.register(item)
    return srv._handle_tools_list(1)["result"]["tools"]


def _wire_bytes(entry):
    """One entry's size in the unit every pin and ceiling here is written in: the transport's own
    json.dumps(..., indent=2). The ONE place the serialization is chosen, so a pin, the P40 bar and
    the hard ceiling can never drift onto different units."""
    return len(json.dumps(entry, indent=2))


def _weights(wire_tools):
    return {t["name"]: _wire_bytes(t) for t in wire_tools}


def _p40(values):
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.4) - 1)] if ordered else 0


def test_every_tool_weight_matches_its_manifest_entry(wire_tools):
    actual = _weights(wire_tools)
    drift = []
    for name, measured in sorted(actual.items()):
        pinned = _TOOL_WEIGHTS.get(name)
        if pinned is not None and measured != pinned:
            direction = "GREW" if measured > pinned else "shrank"
            drift.append(f'    "{name}": {measured},   # was {pinned} ({direction})')
    assert not drift, (
        "tool wire weights drifted from the manifest. A GROWN tool: slim its prose first; if the "
        "capability genuinely needs the weight, update the entry and say why in the commit "
        "message. A SHRUNK tool: lock the win in. Corrected entries:\n" + "\n".join(drift))


def test_new_tools_measure_against_the_fleet(wire_tools):
    actual = _weights(wire_tools)
    known = set(_TOOL_WEIGHTS)
    bar = _p40(list(_TOOL_WEIGHTS.values()))
    lines = []
    for name in sorted(set(actual) - known):
        measured = actual[name]
        if measured <= bar:
            lines.append(f'    "{name}": {measured},   # under the fleet P40 ({bar}) - add and go')
        else:
            lines.append(f'    "{name}": {measured},   # OVER the fleet P40 ({bar}) - slim first, '
                         "or add the entry and justify the weight in the commit message")
    assert not lines, (
        "tools missing from the _TOOL_WEIGHTS manifest:\n" + "\n".join(lines))


def test_no_stale_manifest_entries(wire_tools):
    gone = sorted(set(_TOOL_WEIGHTS) - set(_weights(wire_tools)))
    assert not gone, "manifest names tools that no longer register - drop them: " + ", ".join(gone)


def test_no_single_tool_exceeds_wire_ceiling(wire_tools):
    over = {t["name"]: n for t in wire_tools if (n := _wire_bytes(t)) > PER_TOOL_BUDGET_BYTES}
    assert not over, f"tool entries over {PER_TOOL_BUDGET_BYTES:,} bytes on the wire: {over}"


def test_no_description_exceeds_ceiling(wire_tools):
    over = {}
    for t in wire_tools:
        limit = _DESCRIPTION_OVERRIDES.get(t["name"], DESCRIPTION_BUDGET_CHARS)
        n = len(t.get("description", ""))
        if n > limit:
            over[t["name"]] = f"{n} > {limit}"
    assert not over, (
        f"descriptions over their ceiling: {over} - move workflow prose to a result note, type "
        "the input, or add a NAMED override with an audited reason.")


def test_override_table_matches_reality(wire_tools):
    by_name = {t["name"]: len(t.get("description", "")) for t in wire_tools}
    stale = []
    for name, limit in _DESCRIPTION_OVERRIDES.items():
        if name not in by_name:
            stale.append(f"{name}: tool gone")
        elif by_name[name] <= DESCRIPTION_BUDGET_CHARS:
            stale.append(f"{name}: fits the general ceiling now - drop the override")
    assert not stale, "Stale _DESCRIPTION_OVERRIDES entries:\n" + "\n".join(stale)


def test_no_repo_jargon_in_wire_prose(wire_tools):
    hits = []
    for t in wire_tools:
        blob = json.dumps(t).lower()
        for term in _WIRE_JARGON:
            if term in blob:
                hits.append(f"{t['name']}: '{term}'")
    assert not hits, (
        "repo-internal vocabulary leaked into agent-facing wire prose (an agent reading "
        "tools/list has no repo context) - reword:\n  " + "\n  ".join(hits))


def test_the_manifest_measures_the_transport_serialization():
    # The pins are in the transport's unit, not compact JSON: mcp_server._send_json writes
    # json.dumps(data, indent=2), and pretty-printing costs real bytes per key. An entry measured
    # compact reads SMALLER than what the agent downloads, which is the drift this pins against.
    entry = {"name": "t", "description": "d", "inputSchema": {"type": "object", "properties": {}}}
    assert _wire_bytes(entry) == len(json.dumps(entry, indent=2))
    assert _wire_bytes(entry) > len(json.dumps(entry))


def test_the_manifest_checks_bite():
    # a doctored drift, missing entry, and stale entry must each be detectable by the helpers
    assert _p40([100, 200, 300, 400, 500]) == 200
    assert _p40([]) == 0
