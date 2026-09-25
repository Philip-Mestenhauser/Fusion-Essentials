---
id: C6_Design-Arrange
fixture: none
---

## Prompt

The CAM fixture is the newest document whose name starts with CAM-Fixture- in folder {{FOLDER}} of
project {{PROJECT}} (the project and folder named in tests/live/cloud_config.local.json, filled in
by the proctor or by the operator). Your first act: copy it, in that same folder, to a new document
named C6-Arrange-<stamp> (<stamp> = today's date as YYYYMMDD plus a two-character run tag), open
the copy, and do all the work there. Never save the fixture itself.

GOAL - the design-side ARRANGE solvers on the TRAY family and on the machined parts: 2D true-shape,
2D rectangular and 3D, with the envelope, spacing, rotation and quantity settings varied, and a
screenshot per solve.

Read from the orientation whether the Manufacturing Extension is present and say so. The 3D solver
is an extension feature (whether the 2D ones are is unmeasured); unentitled, each solve must fail
gracefully with its refusal recorded, and the report says which solvers still ran.

- Envelopes: a plane envelope on XY, 120 x 100 mm; a boundary sketch you draw yourself (a 90 mm
  circle on XY); a 3D box 100 x 100 x 60 mm.
- Solves, each read back (arranged and unarranged counts, the result envelope's extent, which
  inputs moved and which were copied, the settings as the solve read them back) and screenshotted
  from the top (and from an isometric for 3D) to a file. The 2D solvers need a planar face on
  every part and the sphere has none, so solve 1 is the refusal on the eight and solves 2 to 11
  run on the SEVEN planar tray parts (all but the sphere); the 3D solves take all eight:
  1. true-shape into the plane envelope at 2 mm spacing, all rotations, on all eight TRAY parts -
     quote the refusal it meets, then the same on the seven planar parts (the baseline);
  2. the same at 5 mm spacing;
  3. rotations limited to 90 and 270 deg, then none;
  4. a frame (margin) of 8 mm inside the envelope;
  5. two copies of each part;
  6. part-in-part off (the ring's hole no longer takes a part);
  7. the rectangular solver at 2 mm spacing;
  8. the boundary sketch envelope, true-shape;
  9. a deliberately small envelope (60 x 40 mm) with partial placement allowed, so some parts are
     left out - say which;
  10. the same small envelope without partial placement - report whether it refuses or places a
      subset, quoting what it says either way;
  11. moving the originals instead of copying, once, reading the positions that changed (if it
      refuses, quote the refusal and say what the refused parts read for grounding);
  12. the five machined parts (bracket, shaft, saddle, cup, plate) as a second set: true-shape into
      a 250 x 200 mm plane envelope at 5 mm spacing;
  13. 3D: the eight tray parts into the box envelope at 3 mm spacing with a 2 mm placement
      clearance and a 5 mm ceiling clearance; then the same box at 10 mm spacing.
- Between solves remove the arrangement you just made (its feature), unless you are stacking on
  purpose, so each solve starts from the same parts; say when you did not.
- Read the statistics each solve reports and put the arranged and unarranged counts and the
  envelope extents in the report beside the settings as they were read back, never as sent.

Save the working document at the end; screenshots to one output folder named after it; list the
paths.

Report: the extension verdict; a table of the thirteen solves - solver, envelope, the settings as
read back, arranged and unarranged, extent, moved or copied, screenshot path; the refusals quoted,
and for each solver whether it ran on this licence; what the 3D solver did with the sphere and the
wedge.

## Grader notes

- A good result, opened in Fusion: thirteen screenshots in which the nest visibly changes with
  spacing, rotation and the frame, the ring's hole holding a small part only while part-in-part is
  on, the small envelope leaving parts out under partial placement and refusing without it, the 3D
  solve stacking the parts inside the box with the stated clearances (the grader reads the extent),
  and a table whose settings column is the read-back. Unentitled: the gated solver(s) refused by
  name, and the report saying which of the 2D solvers still ran.
- What a weak agent does: re-runs the same arrange and stacks copies without saying so; reports
  the settings it sent instead of those read back; skips the refusal case; forgets the second set;
  reads "nothing happened" as success; leaves thirteen arrange features stacked in the document.
- Axis this discriminates: MCP tooling. model_arrange's solver, envelope_plane with
  envelope_length / envelope_width / envelope_height, boundary_sketch, spacing, rotation, quantity,
  part_in_part, frame_width, partial, move_originals, placement_clearance and ceiling_clearance,
  each read back into 'settings'; the arranged and unarranged statistics; the copies-versus-moves
  disclosure; design_delete_feature between solves; the arrange refusal's wording unentitled.
- First A/B to run: this brief before and after the lapse (which solvers the extension gates is
  unmeasured; only 3D is gated in the sweep today); then
  `--deny mcp__fusion-essentials__design_delete_feature`.
- Coverage (the coverage map, section 2): K1 - one check, its 3D half
  extension-gated, which is why this brief runs before the lapse; its unentitled half (which
  solvers refuse, so the 'try rectangular' remedy is measured or dropped) is the rerun's, and the
  thirteen solves are what turn one check into a graded settings table.
- Measured before any run: the 2D and 3D solvers all ran entitled; the solver can leave the named
  occurrences unmoved and mint copies under new Envelope occurrences, and an identical rerun stacks
  another coincident copy set; an arrange that moves nothing and adds nothing is rolled back and
  reported; unplaced components without partial are an error naming both counts; a result
  envelope's extent is read in the envelope's own frame; statistics carry cm and cm2; the
  unentitled refusal today fires only when the platform raise says extension, entitle, license or
  subscrib, and suggests the rectangular solver as a guessed remedy (the extension map's gap 12),
  so grade its wording against what the lapse shows.
