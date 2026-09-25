---
id: C3_CAM-Turning
fixture: none
---

## Prompt

The CAM fixture is the newest document whose name starts with CAM-Fixture- in folder {{FOLDER}} of
project {{PROJECT}} (the project and folder named in tests/live/cloud_config.local.json, filled in
by the proctor or by the operator). Your first act: copy it, in that same folder, to a new document
named C3-Turning-<stamp> (<stamp> = today's date as YYYYMMDD plus a two-character run tag), open
the copy, and do all the work there. Never save the fixture itself.

GOAL - the TURNING families on the SHAFT, its milled flats on rest stock, the stock modes walked,
and the turning program posted.

- Setup 1 (turning): the shaft alone, a lathe or mill-turn from the machine library that carries
  turning (state it); read back off the created setup that its type is turning. The shaft is held
  at end B in the chuck so that end A, the step, the thread and the groove are reachable; the
  turning Z is flipped and the turning origin placed through the setup's own parameters, both read
  back. Cylinder stock with 1 mm on the radius and 1 mm on the face. Tools from the document
  library (the general, grooving and threading inserts; state each by number).
- The stock modes, walked once on this setup before any toolpath: relative cylinder (the
  baseline), fixed cylinder, relative tube, fixed tube, relative box, fixed box, from a solid (the
  shaft's own body), and from the previous setup (this first setup has none - report whether the
  write is refused or accepted and what the setup then says its stock is); each with its dimension
  rows written and the stock extents read back; end on the relative cylinder.
- The families, each aimed at the shaft's own geometry: facing; profile roughing then profile
  finishing over the step and the chamfer; adaptive roughing; a single groove on the groove
  (selected by its bounding edge; the groove is cylindrical, so there is no cone face to try), then
  groove roughing and groove finishing; a profile groove; a chamfer pass on the end chamfer (its bounding
  edge); threading on the modelled thread's faces; a trace along a chain (report what it does -
  no run has generated one yet); and a part-off last. Read every cycle's warnings.
- Representative depth: each family a baseline, then variants of one setting each, generated.
  Across the set cover: stock to leave (radial and axial), the stepdown, the tolerance, constant
  surface speed against a fixed spindle speed, feed per revolution, lead-in and lead-out on and off
  (a lead-out that gouges is fixed by turning it off or by 0.5 mm of stock to leave - say which you
  used), grooving pecking and its stepdown, the number of thread passes and the infeed, and the
  direction (front or back). At least two variants per family. Compare each variant with its
  baseline with the comparison read.
- Setup 2 (milling on the turned part): a milling setup whose stock is what setup 1 left (rest
  stock from the previous setup, never a fresh box) - the setup read says its extents describe a
  box and not the rest stock; quote that sentence - the two flats cut with the 6 mm mill by a 2D
  pocket or contour aimed at the flat faces, sized from what setup 1 left; every operation must
  cut material.
- After EVERY generate: poll to completion, read the machining time and the empty-toolpath census,
  screenshot the isolated toolpath to a file. Show the turning setup's paths and the milling
  setup's paths in separate screenshots as well.
- Order: turning first, milling second, in the tree as in the report.
- Post the turning setup through a turning post this installation ships, with a numeric program
  name, into an output folder you choose; report the file's path and size from the post's own
  read-back. Then post it once more with a non-numeric name: report the failure as the post
  reports it - the stub it leaves, the log lines it quotes, and whether the program it created was
  removed again. If the account has a cloud or hub post library, post once through a post named
  there as well.

Save the working document at the end; screenshots and posted files to one output folder named
after it; list the paths.

Report: both setups (machine, the setup type read, the stock mode string and allowance as read
back, the stock-mode walk with the extents per mode, the milling setup's stock naming the previous
setup and its extents sentence); every operation with family, tool, the setting changed and its
before and after, state, machining time and warnings; the lead-out finding; the empty-toolpath
census; the posts with paths and sizes and the failed post's report; the screenshot paths;
refusals quoted.

## Grader notes

- A good result, opened in Fusion: turning first, the profile roughing stopping short of the
  chuck, the 3 mm groove cut by the grooving insert with pecking in one variant, the thread cut by
  the threading insert on the modelled thread with two pass counts compared, the part-off last, a
  milling setup on rest stock cutting only the flats, every operation nonzero, a lead-out gouge (if
  one appears) fixed and named, the stock-mode walk reading eight modes back with their extents,
  the turning program posted through a shipped post with a numeric name and the non-numeric post
  reported as a failure with its program gone, and variants whose pictures change with stock to
  leave and stepdown.
- What a weak agent does: mills the groove; grooves with the general insert; boxes the milling
  stock; parts off before the thread; passes an empty toolpath; treats a lead-out gouge warning as
  noise; reports the non-numeric post as posted because a stub appeared.
- Axis this discriminates: MCP tooling. cam_create_setup(operation_type='turning'),
  cam_edit_setup(machine=..., stock_mode= each of fixed_box, relative_box, fixed_cylinder,
  relative_cylinder, fixed_tube, relative_tube, from_solid, previous_setup, parameters=
  {wcs_orientation_flipZ, wcs_origin_turning, the stock dimension rows}), cam_get(include=
  ['parameters'], setup=...) with stock_extents, the turning selections in cam_select_geometry
  (groove -> grooves by EDGE, chamfer -> chamfers by EDGE, thread -> threadFaces, chain on
  turning_trace's modelContour), the strategy-pair read (turningGrooveRoughing carries
  maximumGrooveStepdown and usePecking, turningGrooveFinishing carries doLeadIn and nullPass),
  cam_get(include=['time']) per setup, cam_reorder for the sequence, cam_post(post_scope=
  'fusion') with a numeric and a non-numeric program_name (failed_stubs, post_log, the rollback
  sentence) and post_scope='cloud' or 'hub' where the account has one.
- First A/B to run: `--deny mcp__fusion-essentials__cam_select_geometry` (S11 found the
  geometry-aimed operations weakest there), then `--deny mcp__fusion-essentials__sys_get_guidance`.
- Coverage (the coverage map, section 2): B2, B4, B5, B8, C10, D7, G4, G7 -
  eight checks. The cylinder, tube and relative-box dimension rows of B4, the cloud or hub half of
  G4 and the failing post of G7 have no sweep row today; the parameter names the agent finds for
  the stock rows are a finding for the ledger.
- Measured before any run: every turning family but turning_trace is proven (turning_trace's drive
  is a curve family a chain lands on, never yet generated on an insert; on this shaft one straight
  edge answered 'Multiple disconnected input contours are not supported'); on this shaft the single
  groove and the part-off have generated paths Fusion calls valid whose motion is NaN and whose
  time is the int64 sentinel, which the post then rejects - a report that names them nonfinite is
  right and the status read's failure to say so is a known gap, not the agent's; a chamfer's cone FACE
  raises InternalValidationError where its bounding EDGE lands; bar_pull, subspindle_grab,
  subspindle_return and turning_stock_transfer generate 'Toolpath is not supported for the given
  tool and settings' on this install; a turning lead-out gouge clears with doLeadOut false or
  0.5 mm of stock to leave; the milling setup's stock mode reads PreviousSetupStock and its
  extents row says 'relative box, NOT the rest stock'; the fixed box's rows are
  job_stockFixedX/Y/Z and the other modes' dimension rows are unmeasured; the shipped turning
  posts want a numeric program name (a post-log line naming the program number is the signal) and
  refuse multi-axis programs; a failed post leaves a '.failed' stub the tool keeps apart from
  files and its just-created program is rolled back; the machining-time read fails whole for a
  setup holding an errored operation.
