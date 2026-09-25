---
id: C1_CAM-Milling-2D
fixture: none
---

## Prompt

The CAM fixture is the newest document whose name starts with CAM-Fixture- in folder {{FOLDER}} of
project {{PROJECT}} (the project and folder named in tests/live/cloud_config.local.json, filled in
by the proctor or by the operator). Your first act: copy it, in that same folder, to a new document
named C1-Milling2D-<stamp> (<stamp> = today's date as YYYYMMDD plus a two-character run tag), open
the copy, and do all the work there. Never save the fixture itself.

GOAL - the 2D MILLING FAMILIES on the BRACKET: the setup stood up and probed for what it publishes,
each family once as a baseline and then through its settings, on one small part so every toolpath
computes in seconds.

The setup:
- One milling setup on the bracket alone: the bracket COMPONENT is the model, nothing else; read
  back that it holds zero operations and names the bracket. Then replace the model selection with
  the bracket's body, read it, and put the component back.
- A 3-axis mill from the machine library (state it), assigned three ways in turn - by vendor and
  model, by its description, by its listing line - each read back; an ambiguous name is refused
  (quote it); a simulation-ready machine is refused, then assigned without its simulation model,
  and its spindle and travel limits read the same both ways.
- Box stock with 1 mm on every side and 1 mm on top; the work origin bound to a joint origin you
  create at the stock's top centre, its Z to the bracket's top face and its X to a long edge, each
  binding read back; then move that joint origin (a translation and a 30 deg turn), read what the
  setup publishes of its frame afterwards, and say what it does NOT publish. Try binding the joint
  origin as an axis and quote the refusal.
- Write one setup parameter the setup marks not editable; it is refused by name with nothing
  applied - quote it.

The families, each aimed at the bracket's own geometry (no new geometry to give yourself a
target): a facing pass on the top; 2D adaptive clearing and 2D pocketing on pocket A (around the
island) and on pocket B; 2D contour around the outside profile and around the boss; a bore or
circular pass on the counterbores with the 6 mm mill; drilling of the four 5 mm holes and the two
5.5 mm holes with a drill that matches each diameter; a chamfer pass on the top chamfer; a trace
along pocket A's rim; the engraving pass on the FE sketch; a 2D contour with several roughing
passes; a rest-machining contour after a larger cutter; and a waterjet profile around the outline
with a waterjet tool you add to the library for it, inside this same milling setup. Tools come from
the fixture's document library; state each by its number and size.

Geometry, read back each time as the operation carries it:
- The face selection on pocket A's floor with each loop choice (outside, inside, all) and each
  side choice, reading the resolved curve paths per choice.
- The silhouette from the setup's own models (no bodies named) and from the bracket's body named
  with its component; then a bare body name that several tray components share (their first
  body), which is refused naming the candidates - a body name unique in the design resolves on
  its own, so say which case you met.
- The engraving sketch selected by its name with its component; the fixture's sketch may carry the
  face's projected edges beside the text, so read what the selection took (paths and segments) and
  try the sketch's text object on its own; report whether a text-only engraving was reachable.
- Chains: one closed loop as a flat list of its edges; edges of two separate loops in one flat
  list, refused and told to group them; the same two loops as explicit groups; and a flat list
  that Fusion resolves onto another loop, refused with the previous selection restored - read
  back that the resolved contour holds the selected edges where it landed.
- Heights: set a top or bottom height mode to a value outside its choice set (the refusal lists
  the choices), then to a valid one with an offset, read back.
- Feeds and speeds three ways on one operation: a feed written directly on the operation, the ALU
  preset assigned, and the library tool's own rows; the tool read on that operation names the
  preset it RUNS, and the three must agree on what cuts - say how they do.
- Edit the flute length of a library tool that an operation already runs; read that operation's
  tool dimensions and say whether the edit reached it (one store, or a copy) - measured, not
  assumed.
- Where-used on a used tool, on an unused tool, and at a shared library scope (refused).
- One variant on a preset whose spindle speed exceeds the machine's maximum; the operations read
  flags it and its summary counts it.

Representative depth: for each family generate a BASELINE at a relaxed tolerance (0.05 mm), then
VARIANTS that change ONE setting each, every one generated. Read the parameter names for
tolerance and lead-in/lead-out off the operation's own parameter listing before you write them.
Across the set cover: tolerance (0.01 and 0.1), stepover and stepdown, lead-in and lead-out (on
and off, the radius), the linking policy (ramp type and angle, retract height), several roughing
passes and their count, stock contours on, rest machining from the previous operation and its
cutter diameter, compensation, feeds and speeds, the top and bottom heights, the cutting pattern
where the family offers one, climb against conventional. At least two variants per family.

After EVERY generate: poll to completion, read the operation's machining time and the empty-
toolpath census, screenshot the isolated toolpath (all hidden, this one shown, its setup active)
to a file with one sentence placing the path on its feature and inside the stock, and compare the
variant against its baseline with the comparison read, so the difference is stated as the
operation carries it and not as you remember typing it. Run that comparison once while an
operation is still generating and quote the refusal. Show one folder on its own and hide all once,
reading the visibility list back. A healthy compute with zero machining time is a defect to fix or
report, never a pass. Keep the tree grouped by family, a folder per family, in the order a
machinist would run them.

Save the working document at the end. Write every screenshot to a file under one output folder
you choose for this run (name it after the working document) and list the paths.

Report: the setup (machine and the three assignment reads, the stock extents as read back, the
work origin bindings and the frame read after the move, the refusals quoted); the operation list
by folder - for each operation its family, tool number, the one setting changed from its baseline
with the expression before and after as the comparison read gives it, its state, machining time
and any warning; the geometry read-backs per selection kind; the feeds-three-ways finding and the
flute-length finding; the empty-toolpath census; the screenshot paths with their sentences;
anything else the wire refused, quoted.

## Grader notes

- A good result, opened in Fusion: one setup on the bracket with a library machine and a WCS bound
  to geometry, a folder per family holding a baseline and two or three variants, every operation
  valid with a nonzero time, drills matched to their holes (a 5 mm drill in a 5 mm hole), the
  counterbores bored to 9 mm with the 6 mm mill, the engraving on the FE sketch, the waterjet
  profile computing in the milling setup, and a settings table that reads off the operations (the
  grader spot-checks three rows with cam_compare_operations and re-reads three machining times
  with cam_get(include=['time'])). Look at the isolated screenshots side by side: a variant's path
  differs from its baseline's where the setting shapes the path (stepover, pattern, lead-in) and
  matches it where the setting changes only speed.
- What a weak agent does: builds one operation per family and calls that depth; reports the value
  it sent instead of the read-back; drills every hole with one drill; leaves the island out of
  pocket A; passes an empty toolpath because its state read valid; adds a sketch to give itself a
  target; describes the variants from memory instead of the comparison read; skips the refusal
  cases because they are refusals.
- Axis this discriminates: MCP tooling. cam_create_setup(models=[<occurrence>]) then
  cam_edit_setup(models, machine, machine_strip_simulation, wcs={origin, z_axis, x_axis},
  parameters on a locked row), cam_get(include=['machine']), joint_create_origin plus
  assembly_move for the frame probe, cam_edit_operation(parameters=...) against the operation's own
  parameter names (cam_get(include=['parameters'], operation=...) lists them, with 'choices' on an
  enumeration row and the hidden rows behind a switch), cam_select_geometry per family (face with
  loop_type and side_type, silhouette with bodies and component, sketch with sketches and
  component, chain with chain_groups and the resolved_contains_selected check, holes, pocket,
  top_mode / top_offset / bottom_mode / bottom_offset), cam_edit_operation(preset=...) with
  cam_get(include=['tool'], operation=..., preset=...), cam_edit_tools(action='edit') then
  cam_get(include=['tool']) for the flute-length question, cam_edit_tools(action='where_used'),
  cam_compare_operations (and its refusal while generating), cam_get(include=['time']) and the
  census in cam_get_status, cam_edit_folders and cam_reorder for the tree, cam_show_toolpath
  (isolate, show_folder, hide_all, list).
- First A/B to run: `--deny mcp__fusion-essentials__cam_compare_operations` (does the settings
  table stay honest without the diff), then `--deny mcp__fusion-essentials__cam_find_pockets`
  together with `--deny mcp__fusion-essentials__cam_find_holes` (does find_geometry still get the
  agent to the pocket floors and hole walls).
- Coverage (the coverage map, section 2): A5, A7, B1, B6, B7, B9, B10, B12,
  C1, C3, C4, C5, C15, D1, D8, D11, D13, E7 - eighteen checks. B7, C15 and the side_type half of
  C1 have no sweep row today; A5 settles whether an operation's tool is the library row itself or
  a copy of it, either way; grade B7 from the frame read
  the report quotes, not from what the agent expected.
- Measured before any run: every family here is proven in STRATEGY_COMPETENCE.md on the geometry
  kind named there (face on a face, adaptive2d and pocket2d on a pocket floor, contour2d on a
  silhouette or chain, bore and circular on circularFaces, drill on holeFaces, chamfer2d on a
  face, trace on a chain, engrave on a sketch, profile2d with a waterjet tool inside a milling
  setup); pocket_clearing rest-machines from the operations before it and read a nonzero time only
  in a setup of its own; a 2D contour's rest sibling differs by useRestMachining and
  restMaterialCutterDiameter, its multiple-passes sibling by doRoughingPasses and
  maximumRoughingSteps, its trimmed sibling by useStockContours; a CAM string parameter stores
  its expression single-quoted and the edit tool matches the quoting; a row behind a switch reads
  isEnabled false until the switch lands in the same call; a locked setup row takes a write
  silently, so the tool refuses it first; a Joint Origin binds the WCS origin only (an axis
  binding raises); the setup publishes its Z direction and not its origin or X; the
  machining-time read fails whole for a setup holding an errored operation; a
  compare while an operation generates once ended the Fusion process, which is why the tool
  refuses it; a rename of a bore, circular or thread carrying no faces parks Fusion behind a
  dialog, so names go on at create.
