---
id: C2_CAM-3D-and-MultiAxis
fixture: none
---

## Prompt

The CAM fixture is the newest document whose name starts with CAM-Fixture- in folder {{FOLDER}} of
project {{PROJECT}} (the project and folder named in tests/live/cloud_config.local.json, filled in
by the proctor or by the operator). Your first act: copy it, in that same folder, to a new document
named C2-3DMultiAxis-<stamp> (<stamp> = today's date as YYYYMMDD plus a two-character run tag),
open the copy, and do all the work there. Never save the fixture itself.

GOAL - the 3D and MULTI-AXIS families on the SADDLE, the rotary families on the SHAFT, and the
probing cycles, the extension-gated ones included, each as a baseline plus setting variants.

Before anything else, read from the orientation whether this installation's Manufacturing
Extension is present, and say so in the report. If it is absent, every gated family below must
fail GRACEFULLY: try it anyway, record the exact refusal and the alternative it names, and go on;
the report's refusal list is then a graded deliverable, not a failure.

- Setup 1: the saddle alone, a 5-axis mill from the machine library (state it; build one from a
  template if the library has none), box stock with 1 mm all round, the work origin at the stock's
  top centre, tools from the document library by number. Read the machine's limits: its spindle
  range, its linear travels in mm and its rotary travels in degrees, and its tool stations. List
  the families this setup offers with their allowed flag and say which read not allowed on this
  licence.
- The 3D families on the saddle's top: 3D adaptive clearing (roughing, then a rest pass from it
  with the smaller ball), parallel, scallop, 3D contour, pencil, horizontal, spiral, radial, morphed
  spiral, ramp, project (a chain), morph (a PAIR of curves - and once as one selection, reporting
  what that does), flow on a set of drive surfaces (the drive-surface flow, not the flow family
  that reads not allowed on this installation), geodesic,
  blend between two curves (report what it does), and steep and shallow. On one 3D finishing
  family put a chain on the machining boundary so the toolpath stays inside it, and show that in
  its screenshot.
- The multi-axis families on the drafted walls: swarf as a rail pair, lower rail first, reading
  back that the rails are open and the rail mode is engaged, and reporting whether that baseline
  cuts or comes out empty (on this fixture's drafted walls it has generated valid and EMPTY until a
  sideways tilt of a few degrees was set - vary the tilt and report the first setting that cuts);
  then one rail alone (refused) and the pair upper rail first, which generates valid and EMPTY -
  the status read's triage must name it;
  advanced swarf on the wall surfaces; multi-axis contour on the top edge chain; multi-axis
  finishing and multi-axis roughing on the top surfaces; 3+2 on one drafted wall (the wall's normal
  becomes the tool axis, the axis mode reading manual); and deburr on the top edges, its multiple
  passes and their count switched on in ONE write. On the surface-driven families exercise every
  surface role the operation offers - drive, floor, wall, ceiling, swarf - and the check role,
  which is refused (quote it).
- Setup 2: the shaft alone in a rotary setup on a 4-axis machine (state it), the setup's Z along
  the shaft's axis: rotary contour, rotary finishing (rotary parallel) and rotary pocket on the
  flats.
- Probing, on the saddle setup: a probe WCS cycle with the library's probe on the stock top; a
  probe geometry cycle on one top face; an inspect-surface cycle attempted (its first point can
  only be placed in the Fusion UI - report what the wire says); a probing cycle handed a cutting
  tool, refused before anything lands (quote it). Post the two probe cycles alone through the
  local 'haas' post into an output folder you choose and say whether macro cycles appear in the
  file.
- Selecting geometry with generation requested in the same call: once, and read back the handle
  it launched.
- Representative depth: each family a baseline at 0.05 mm tolerance, then variants of one setting
  each, generated. Across the set cover: tolerance, stepover (a wide and a fine one) and stepdown,
  the boundary overlap, the machining boundary (silhouette against a selected boundary), machine
  steep or shallow areas only, direction and order (climb, bottom-up), the tool-axis mode where
  the family has one (vertical, tilted, lead and lean angles), linking (stay-down, retract,
  smoothing), lead-in and lead-out, rest machining from the previous operation, the load on the
  adaptive families, feeds and speeds. Read the names of the 5-axis collision-avoidance rows off a
  multi-axis operation's parameter listing and vary one. At least two variants per family; ramp
  is the long one - size its poll accordingly.
- After EVERY generate: poll to completion, read the machining time and the empty-toolpath census,
  screenshot the isolated toolpath to a file, and compare the variant against its baseline with
  the comparison read. A valid operation with zero machining time is a defect to fix or report,
  and the one you made on purpose (the upper-first rails) must appear in every read that lists
  empties: the operations rows, the time read, the validity verdict and the status read.
- Read each operation's tool axis as the operation carries it (the machining type and the axis
  mode), so the report separates indexed 3+2 from simultaneous multi-axis by what was read, not by
  the family's name.
- Post setup 1 through the local 'haas' post: the simultaneous multi-axis operations are refused
  for a 3-axis post naming the machine configuration; park them, post again, and report what the
  file holds.

Save the working document at the end; screenshots and posted files to one output folder named
after it; list the paths.

Report: the extension verdict as read; the machine limits; the offered families with their allowed
flags; both setups with machine, stock and origin; every operation with its family, tool, the
setting changed from its baseline (before and after off the comparison read), the tool-axis read,
state, machining time and warning; the rail findings; the surface-role read-backs and the check
refusal; the probing results and the two posts; the refusal quoted for each gated family that
would not create or generate; the empty-toolpath census; the screenshot paths.

## Grader notes

- A good result, opened in Fusion, entitled: two setups, every family above present and valid with
  a nonzero time, the swarf and multi-axis contour paths visibly on the drafted walls in their
  isolated screenshots, the boundary-limited finishing pass staying inside its chain, 3+2 reading
  the wall's normal as its axis, the rotary families wrapping the shaft, the upper-first rail pair
  named in every empty-toolpath read, the probe cycles posting, the 3-axis post refusing the
  simultaneous operations by name, and variant pictures that differ where stepover or the boundary
  changes. Unentitled, after the lapse: the base families still valid, and for each gated family a
  quoted refusal naming the Manufacturing Extension and an allowed alternative, with nothing
  created for it.
- What a weak agent does: runs a 2D contour at one depth and calls it swarf; feeds both rails into
  one selection; hides the empty rail pair instead of reading it out; describes the tool axis from
  the family name; creates a gated operation that never generates and reports it healthy; posts
  the whole setup through the 3-axis post and hides the refusal; or, unentitled, gives up on the
  base families too.
- Axis this discriminates: MCP tooling and the entitlement gate. cam_get(include=['machine'])
  for the limits, cam_create_operation's blocked-strategy refusal, its probe-tool refusal and its
  tool_axis read, cam_select_geometry(selection='chain') with the rail pair (rails_open,
  swarf_mode) and with chain_groups on the boundary (boundary_engaged, boundaryMode),
  selection='surfaces' with surface_target drive / floor / wall / ceiling / swarf and the check
  refusal, selection='orientation' for 3+2 (tool_axis_engaged), selection='probe',
  generate=true's handle, cam_edit_operation with doMultiplePasses + numberOfStepovers in one
  call, cam_get_status's rail_triage and empty_toolpaths, cam_inspect_toolpaths' empty census,
  cam_generate's entitlement_blocked list, cam_post's 5-axis refusal, cam_get(include=
  ['strategies']) and workspace_orient's machining_capabilities verdict.
- First A/B to run: this brief before and after the lapse (the same text, two licences); then
  `--deny mcp__fusion-essentials__cam_compare_operations`.
- Coverage (the coverage map, section 2): B11, C6, C7, C8, C11, C12, C13, C17,
  D4, D5, D6, D9, E3, G5 - fourteen checks, the extension-gated ones among them C7, C12, C17, D5,
  D6, D9 and G5, which is why this brief runs before the lapse. C8, the wall and ceiling halves of
  C13, B11's tool stations and the collision-avoidance row of D6 have no sweep row today; grade
  them from the read-backs the report quotes.
- Measured before any run: STRATEGY_COMPETENCE.md and the entitlement A/B put these behind the
  extension - advanced_swarf, deburr, multi_axis_contour, multi_axis_morph, multiaxis_finishing,
  multiaxis_roughing, probe_geometry, inspect_surface, rotary_contour, rotary_finishing,
  rotary_pocket, steep_and_shallow, swarf, three_plus_two - while probe (Probe WCS), flow2 and
  geodesic read allowed on the base licence and chamfer (3D), flow (old), inclined_walls and
  hole_machining read not allowed on both; a swarf rail pair fed as one selection is refused, an
  upper-first pair generates VALID and EMPTY, a lower-first pair on the saddle's drafted walls
  also generated empty until a 5 deg sideways tilt cut (with a linking-gouge warning), and a
  closed 4-rail loop answered 'Invalid surface'; advanced swarf generated empty on the saddle
  with both a 6 mm and a 10 mm cutter and its floor/adjacent groups cannot be filled by the
  surface roles (a known gap, not the agent's); morph wants a curve
  PAIR, one selection each, and one selection answers 'No passes to link'; blend answers
  'Incorrect number of drive curves' and the chain kind lands on its boundary; the check surface
  set reads not editable and assigning to it raises 'Parameter is deprecated'; multi_axis_morph
  ran past 240 s of polling once and ramp cut 2163.8 s; the rotary families read
  axisView_orientation_mode 'axisZ' and turn about the setup's own WCS; inspect_surface's first
  point takes no API write; a probing cycle generates and posts through Renishaw macro cycles; a
  blocked strategy's create is refused before any mutation and a generate excludes blocked
  operations by name; corner belongs to C4; a machine's tool stations have never read non-zero
  on this install.
