---
id: C7_CAM-Job-End-to-End
fixture: none
---

## Prompt

The CAM fixture is the newest document whose name starts with CAM-Fixture- in folder {{FOLDER}} of
project {{PROJECT}} (the project and folder named in tests/live/cloud_config.local.json, filled in
by the proctor or by the operator). Your first act: copy it, in that same folder, to a new document
named C7-Job-<stamp> (<stamp> = today's date as YYYYMMDD plus a two-character run tag), open the
copy, and do all the work there. Never save the fixture itself.

GOAL - one complete JOB on the BRACKET, sequenced like a machinist's and delivered: two milling
setups, a probe, generation read every way it can be, NC comments, NC programs, setup sheets,
posted files, and a closing statement that separates "valid" from "reached the geometry".

The setups:
- Setup 1 (top): the bracket alone, a 3-axis mill from the machine library (state it), box stock
  with 1 mm all round and 2 mm on top, the work origin at the stock's top centre. Declare the
  plate as a fixture body of this setup (a stand-in for a fixture plate) and read the fixture
  count back; try to clear the fixtures with an empty list and quote the refusal; switch the
  stock to a solid once (the bracket's own body) and back to the box, reading the mode each time.
- Setup 2 (flip): the bracket flipped, its stock what setup 1 left (rest stock from the previous
  setup), a facing pass to thickness and a chamfer on the bottom edge if you cut one; nothing
  cutting air.
- References: insert the fixture document itself into this design as a linked reference beside
  the bracket, make a scratch setup that selects the referenced component, read the setup's
  references (its count and the source it points at, and the census sentence that says what was
  counted), then delete the scratch setup and the reference.

The operations, each aimed at the bracket's own geometry, each with a lead-in, a linking policy
and a preset you state: a probe cycle first, with the library's probe, on the stock top to set the
work offset; then facing, 2D adaptive on both pockets (the island respected), 2D contour around the
boss and around the outside, drilling of every hole with matching drills, the counterbores bored,
the chamfer; a Manual NC entry with a message for the operator (it carries no machining time -
read what its time row answers); and, for the closing statement, a SECOND contour aimed at the
WRONG face on purpose (pocket B's floor instead of the boss). Try to create the families named
'hole recognition' and 'folder' as operations and quote what the landing count says. Patterns:
this platform offers no way to create a mirror, linear or rotary pattern of operations through
this wire - say so; if the document holds one, read its children, reorder it and delete it.

Generation, read every way:
- Generate the whole document with valid operations skipped, then again without skipping, then
  one setup alone; read the launch reasons each time (valid-forced, out-of-date, never generated,
  errored) and note that a scoped launch regenerates its whole target whatever the skip flag says.
- Poll by handle until complete; poll by the operation's name (as one launched in the UI would be
  read); if another document is open, activate it, ask for the latest generation, quote the
  refusal, and come back.
- Read the machining time per operation and per setup, the empty-toolpath census, the readiness
  verdict and the validity verdict for the document, then for one setup, then with suppressed
  operations counted; screenshot each setup's toolpaths (all shown) and each operation isolated,
  to files.
- Suppress one operation: read what the active counts and the post counts drop; make one operation
  error on purpose (from-WCS heights that put its bottom above its top - the contour-relative
  kinds stay valid), read the readiness (a BLOCKER, never ready), then fix it and restore the
  suppressed one.
- Invalidate three ways and read the reason each time: pocket B 1 mm deeper through its parameter,
  a cutting dimension edited on a library tool the job runs, and a machine change on setup 1;
  after each, regenerate only what is out of date and read the reason list clear; put each change
  back. If a change does not read as out of date, or a restore relaunches fewer operations than
  the change did, report exactly what the reads said - that is a finding, not a fault of yours.

Deliver:
- A comment on every NC program naming the part and the setup, one program renamed through the
  same write, and a blank comment attempted and refused (quote it).
- One NC program per setup posted through the local 'haas' post into an output folder you choose,
  with a numeric program name; setup 1's program posted by the post's name and again by its full
  file path; a post named by a path that does not exist, refused; one program holding BOTH setups;
  reconfiguring that program to one setup, refused naming what to omit; the same program posted
  AS STORED without reconfiguring it; then reconfigured with overwrite allowed; setup 1's program
  posted once in inch and once in millimetres, with a program comment, the unit read back off the
  program list and the comment off the write's read-back (no tool here reads a posted file's
  contents, so say that rather than quoting a header you cannot see); two library tools
  renumbered alike, a post attempted and its refusal quoted, the numbers restored.
- The wrong-face contour: it reads exactly as ready as the right one; post both alone through the
  same post and report each file's size as the post reads it back; the contrast rests on the
  positions of the boss and of pocket B that you measured, the heights each contour reads, its
  tool size against the feature, and its isolated picture. No tool here reads a file's X/Y/Z
  extents - state that plainly instead of computing extents you did not read.
- A setup sheet per setup (HTML), each into a folder of its own so neither overwrites the other,
  then one for the whole document, then a second sheet into a folder already holding one (it
  overwrites - read that back); an Excel sheet once if this platform is Windows; report every
  sheet's path and size as the generator reads them back (no tool here reads a sheet's contents;
  the CAM reads for the stock, the work offset and the tools stand in for what the sheet lists).
- Read the program list back with each program's item count, operation count, posted count, empty
  toolpaths, unit and post.
- Inspection: read the inspection results for the probe cycle and state plainly what an unrun
  probe reports; ask for measure '0' and for a unit that does not exist and quote both refusals.
- The closing statement, per operation: "valid" (the state read), "cuts material" (the time read),
  "reached the intended geometry" (what evidence you hold: the isolated picture against the
  feature, the heights read against the stock and the feature, the tool size against the feature,
  and for the two contours the measured positions against the heights and the tool), and what you
  do NOT know without a machine run or a file reader. No stock simulation exists on this platform;
  say so rather than claiming one.

Save the working document at the end; screenshots, NC files and sheets to one output folder named
after it; list the paths with sizes.

Report: both setups (machine, stock mode string, origin, the fixture and stock reads); the
reference read and its census sentence; the probe cycle; every operation with family, tool,
preset, lead and linking settings as read, state, time and warnings; the Manual NC's time row and
the two not-an-operation refusals; the launch reasons per generate; the poll reads and the
'latest' refusal; the setup and document times; the readiness and validity verdicts in every scope
and state; the suppression and error findings; the three invalidations with their reasons; the
NC programs with their comments, files, sizes and operation counts and every post refusal; the
wrong-face comparison with the extents; the sheet paths and sizes and the overwrite read; the
inspection reads; the closing statement.

## Grader notes

- A good result, opened in Fusion: two setups in order, the flip's stock naming setup 1, a probe
  cycle at the top, every operation valid with a nonzero time except the Manual NC (no time by
  design), the three invalidations read as out-of-date with their reason category then
  regenerated to ready with only the affected operations relaunched, NC programs with comments and
  posted files whose sizes the report quotes from the post's own read-back, every post refusal
  quoted rather than worked around, an as-stored repost that reused the program, sheets in their
  own folders and one overwrite read back, the wrong-face contour caught by the measured positions
  and the pictures and not by any state read (no wire reads a posted file, and the agent must say
  so), and a closing statement that claims nothing a screenshot cannot prove. Read the closing
  statement against the isolated screenshots and the position table: every "reached the geometry"
  claim should point at a picture, a height read, and for the contours the measured position. An
  invalidation that does not read as out of date is a finding the report must state, not a miss.
- What a weak agent does: skips the probe; boxes the flip stock; regenerates everything after an
  edit and calls it targeted; posts a program holding the probe through a post that cannot emit it
  and hides the warning; reads an empty inspection result as a failure; calls valid "correct";
  claims the toolpath was simulated; drops the wrong-face contour once it reads ready; treats the
  refusal cases as failures of its own.
- Axis this discriminates: MCP tooling at the job level. cam_edit_setup(fixtures, stock, an empty
  list refused, machine), doc_insert_occurrence of the fixture and cam_get(include=
  ['references']), cam_create_operation(strategy='probe') with a probe tool and
  cam_select_geometry(selection='probe'), strategy='manual' and the hole_recognition / folder
  landing-gate refusals, cam_edit_setup(stock_mode='previous_setup'), cam_generate(skip_valid,
  target) and its launch_reasons and skip_valid_applied, cam_get_status(handle, target, 'latest'),
  cam_get(include=['time', 'operations', 'nc_programs', 'inspection']) with measure= and units=
  refusals, cam_inspect_toolpaths(scope, include_suppressed), cam_edit_operation(suppressed=,
  parameters on the heights), invalidation_reasons after param_set / cam_edit_tools(action='edit')
  / cam_edit_setup(machine), cam_set_nc_comment(comment, program, set_name = the program's
  listing name, set_number = the post's program number, blank refused),
  cam_post (program_name numeric, post by name and by path, a missing path refused, setups=[A,B],
  the reconfigure refusal, the as-is mode, overwrite=true, units inch and mm, program_comment, the
  duplicate-tool-number refusal), cam_generate_setup_sheet(scope, format html and excel,
  output_folder, overwrote_existing), find_geometry for the boss and pocket B positions.
- First A/B to run: `--deny mcp__fusion-essentials__cam_inspect_toolpaths` (does the readiness
  read alone carry the verdict), then `--skill parametric-cad-design` for the machinist's
  sequencing.
- Coverage (the coverage map, section 2): B3, B14, D10, D12, E1, E2, E4, E6,
  E8, E9, F1, G1, G2, G3, G6, G8, G9, G10 - eighteen checks, none extension-gated. D12 fires only
  if a pattern exists (no API creates one; graded as the statement otherwise); B14, E8, G3, G6, the
  overwrite half of G2, the by-path and missing-path halves of G1, the set_name half of G8, the
  Excel and overwrite halves of G9 and the design and machine categories of E9 have no sweep row
  today; E8's extents table is the finding this run exists to record.
- Measured before any run: hasToolpath reads true on an EMPTY toolpath and a machining time of 0
  is the discriminator; probing generates and posts, and inspection results are empty until a
  machine run; a Manual NC answers 'Machining time could not be calculated' and never appears in
  the empty census; hole_recognition and folder answer a name while the count stays; no API
  creates a CAM pattern; a scoped generate regenerates its whole target whatever skip_valid says;
  completed settles on the operations' own states and 'latest' is refused on another document;
  a suppressed operation is left out of the post's counts and the file; the shipped posts refuse
  multi-axis programs; the setup sheet lands asynchronously, is named after the document, and
  overwrites in a folder already holding one; cam_post's success is the file landing, a '.failed'
  stub is not a deliverable, a post-log line naming the program number means the name must be
  numeric, an as-is post refuses a 'units' change, a reused program whose stored operations differ
  from the requested scope is refused without overwrite=true, and two tools numbered alike are
  refused by the post; a machine change on a setup invalidates only the operations that read
  the machine (two of a nine-operation job), and putting it back relaunches those same two - a
  restore that relaunches fewer than "all" is the expected read, not a miss; the references
  census counts only the entries a setup selects directly;
  no simulation API exists on 2705 (checkToolpath means valid-and-up-to-date); CAM validity
  reads stale until the Manufacture workspace has been entered once.
