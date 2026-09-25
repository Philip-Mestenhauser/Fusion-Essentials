---
id: C4_CAM-Recognition-Rest-Templates
fixture: none
---

## Prompt

The CAM fixture is the newest document whose name starts with CAM-Fixture- in folder {{FOLDER}} of
project {{PROJECT}} (the project and folder named in tests/live/cloud_config.local.json, filled in
by the proctor or by the operator). Your first act: copy it, in that same folder, to a new document
named C4-Recognition-<stamp> (<stamp> = today's date as YYYYMMDD plus a two-character run tag),
open the copy, and do all the work there. Never save the fixture itself.

GOAL - feature RECOGNITION driving the drilling and pocketing of the PLATE, REST machining on the
CUP, and the job packaged in FOLDERS, ORDERED, and saved as a TEMPLATE.

Read from the orientation whether the Manufacturing Extension is present and say so. The corner
rest family, the boss-aware pocket recognition and the surface-group selection are gated;
unentitled, each must fail gracefully: record the refusal and go on.

- Setup 1: the plate alone, a 3-axis mill from the library (state it), box stock 1 mm all round,
  tools from the document library by number.
- Holes by recognition: recognize the plate's holes as groups a machinist would name (the 4 mm
  grid, the 6.5 mm row, the counterbores, the blind holes, the tapped holes) with their per-
  segment faces, once with partly enclosed holes included and once without, and once through a
  diameter window that keeps only the 4 mm group. For each group one drilling cycle with the drill
  that matches the group's top diameter (4, 5, 5.5 or 6.5 mm), a bore or circular pass on the
  counterbores with the 6 mm mill (its hole selection is spelled differently from a drill's - read
  back which parameter took it), a spot or chamfer where you judge one is due, and a tapping cycle
  on the M5 holes with the tap; the blind holes drilled to their depth, the through holes through.
  Every cycle aimed at the recognized faces, none re-drilling an open hole; once, aim a drill at
  the recognized faces through a diameter window that keeps only the right ones. Vary per family:
  the cycle type and peck depth, dwell, retract, feed.
- Pockets by recognition: recognize the plate's pockets down the setup's Z, then again including
  bosses, and say what changed; then once along a different direction (a scratch setup whose Z is
  bound to one side face) and say what that direction finds. For each pocket a 2D adaptive or 2D
  pocket with a cutter that fits its corner radius (the 4 mm for 3 mm corners), aimed at the
  pocket's FLOOR face - a wall face is refused (quote it) - the island respected, the slot by the
  slot family, the open pocket with its open side, the through pocket through; a chamfer or trace
  on the pocket rims. Vary per family: stepover, tolerance, lead-in, the pattern. Do not attempt
  the native pocket-recognition selection; it is disabled on this server and stays so.
- Surface groups: on the plate's facing pass put the top face on a surface group of its own with
  machining over holes on, read the group back, then the same with it off; a group that reads back
  fewer faces than given is rolled back - say what you read.
- The off-axis drill: in the bracket setup below, before re-aiming the template's cycles, bind the
  setup's Z to a side face so it runs perpendicular to the holes, aim one drill at a hole, and
  record the orientation error it reports (a Z merely flipped away from the holes still drills
  from the far side and errors nothing - try that too and say so); then bind the setup's Z to that
  hole's face, flip Z as needed, and see the error clear.
- Setup 2: the cup alone; 3D roughing four ways - 3D adaptive (vary its load and its stepdown),
  pocket clearing in a setup of its OWN first (behind other families in one setup it computes
  empty - measure both and say), flat and horizontal on the floor - then REST machining with the
  4 mm and 3 mm tools: adaptive rest from the previous operation; and the CORNER rest family with
  rest from the job, its other rest inputs read for whether they take a write. Vary: the rest
  source (the previous operation, a reference tool diameter), the minimum radius, stepover.
- Folders and order: in setup 1 three folders - Drilling, Pockets, Finish - a nested folder inside
  Pockets, every operation moved into its folder, Drilling first and Pockets second in the tree;
  rename one folder; generate one folder on its own once, and show one folder on its own in a
  screenshot. Reorder once each: an operation, a folder, a setup, and an operation against a
  folder (a cross-kind pair - read what the order read says about it).
- Setups: rename setup 1 onto setup 2's name (refused), onto its own name (nothing written), onto
  a free name (read back).
- Template: save the Drilling folder's operations as a NEW template in a local library folder of
  your naming; apply it to a THIRD setup on the bracket (its 5 mm and 5.5 mm holes) by name and
  again by url with the name cross-checked (a url and a name that disagree are refused - try it);
  once creating only, once creating and generating; apply the shipped hole bundle too and read its
  operations arriving tool-less, then tool them one by one; re-aim the applied cycles at the
  bracket's recognized holes, generate, and read what landed; attempt to apply the template onto a
  folder as the target and quote the refusal; if the account has a cloud template library, save a
  copy there and read it back; list the template locations this build offers; finally delete the
  local template with its name confirmed and show it gone.
- Deletes: delete one operation, the scratch setup, and an empty folder; each gone on re-read.
- After EVERY generate: poll to completion, read the machining time and the empty-toolpath census,
  screenshot the isolated toolpath to a file, and compare each variant with its baseline with the
  comparison read.

Save the working document at the end; screenshots to one output folder named after it; list the
paths.

Report: the extension verdict; the hole groups as recognized (count, top diameter, through or
blind, threaded, faces per segment) with and without partial holes and through the window, and
the pockets as recognized (depth, bottom type, islands) down Z, with bosses, and along the side
direction; every operation with family, tool, folder, the setting varied with its before and
after, state, time and warnings; the surface-group read-backs; the off-axis drill error and its
clearing; the rest-machining findings (what computed empty where, and what fixed it); the folder
listing, the renames and the tree order with the reorder read-backs; the template's location,
each apply's result and the deletion; refusals quoted; the screenshot paths.

## Grader notes

- A good result, opened in Fusion: hole groups matching the plate (six 4 mm through, three 6.5 mm,
  two counterbored, two blind, two tapped), each drilled by its matching drill, the counterbores
  bored not drilled, the blind holes to depth, the pockets recognized with the island and the open
  side honoured, the slot cut by the slot family, the cup roughed four ways then rest-machined with
  the small tools and the corner family cutting the inner floor corner, the tree in three folders
  in machining order with the reorder reads quoted, and a template that re-applies to the bracket
  by name and by url and, once re-aimed, cuts its holes, then is deleted. Unentitled: corner, the
  boss-aware recognition and the surface group refused by name, everything else still valid.
- What a weak agent does: drills the counterbores with a 9 mm drill it invented; drills the tapped
  holes without tapping them; reads the recognized faces and then selects by hand anyway; leaves
  pocket clearing empty in the same setup and calls it valid; drops the corner family because it
  errored on its first generate; saves the template and never applies it; skips the cross-kind
  reorder because it reads unverified.
- Axis this discriminates: MCP tooling. cam_find_holes(include_partial, min_diameter,
  max_diameter) with its per-segment faces into cam_select_geometry(selection='holes',
  min_diameter, max_diameter) on holeFaces and circularFaces, cam_find_pockets(attack_vector,
  include_bosses) into selection='pocket' with the FLOOR face, selection='surface_group' with
  machine_over_holes, cam_edit_setup(wcs={'z_axis'}) and wcs_orientation_flipZ for the off-axis
  drill, corner's restMaterialFromJob through cam_edit_operation, pocket_clearing's own-setup
  need, cam_edit_folders(create, rename, move), cam_reorder on each kind and the cross-kind
  order_unverified note, cam_edit_setup(rename=...) three ways, cam_save_template and
  cam_apply_template(template_name, template_url, generate) with cam_get(include=['templates'],
  template_location), cam_delete_template(confirm_name), cam_delete on an operation, a setup and
  a folder.
- First A/B to run: `--deny mcp__fusion-essentials__cam_find_holes` together with
  `--deny mcp__fusion-essentials__cam_find_pockets` (does find_geometry get the agent there), then
  this brief before and after the lapse for the corner, boss-aware and surface-group rows.
- Coverage (the coverage map, section 2): B13, C2, C9, C14, C16, C18, C19, D2,
  D3, H1, H2, H4, I1, I2, I3 - fifteen checks, the extension-gated ones among them C14 and the
  bosses half of C19 (and corner from D5), which is why this brief runs before the lapse. C16 is
  graded as NOT attempted. C18's partial-holes case, C19's side direction, H2's other locations,
  H4, the setup / folder / cross-kind halves of I2 and the folder half of I3 have no sweep row
  today; D2's off-axis recipe was measured once by hand.
- Measured before any run: hole recognition answers groups with faces per SEGMENT (a segment can
  own several faces) and isThrough is the API's own flag; the plain pocket route skips bosses and
  the boss-aware route adds them as island-only pockets; the selection-based recognition route
  crashed Fusion once and is refused unless allow_pocket_recognition=true (do not grade an agent
  down for leaving it alone); a drill aimed at a hole with the setup's Z PERPENDICULAR to it errors
  'Cylindrical face not in tool orientation!' while a Z merely flipped to the far side still drills
  (3.9 s), and binding Z to that face then flipping it clears the error; corner
  errors 'No valid reference tool nor valid reference stock model' until restMaterialFromJob
  reads true, then cut 4456 s on a pocketed block as the only operation in its setup;
  pocket_clearing read EMPTY behind the whole-model families of one setup and 140 s in a setup of
  its own; a surface group's own default group refuses machineOverHoles and a caller's faces go on
  a group of their own; hole_recognition and folder are not operations (operations.add answers and
  the count stays); every template save adds a library entry only cam_delete_template removes; an
  applied template's operations can land tool-less or un-aimed and the apply says which; a
  template lands on a SETUP only; hole and pocket recognition unentitled are unmeasured - this
  brief's rerun measures them.
