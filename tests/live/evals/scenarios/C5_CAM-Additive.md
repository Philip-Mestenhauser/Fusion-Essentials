---
id: C5_CAM-Additive
fixture: none
---

## Prompt

The CAM fixture is the newest document whose name starts with CAM-Fixture- in folder {{FOLDER}} of
project {{PROJECT}} (the project and folder named in tests/live/cloud_config.local.json, filled in
by the proctor or by the operator). Your first act: copy it, in that same folder, to a new document
named C5-Additive-<stamp> (<stamp> = today's date as YYYYMMDD plus a two-character run tag), open
the copy, and do all the work there. Never save the fixture itself.

GOAL - ADDITIVE setups on the TRAY family: a metal machine and an FFF machine, each with a print
setting, the family arranged on each platform with the packing settings varied, oriented
automatically, and supported.

Read from the orientation whether the Manufacturing Extension is present and say so. The metal
additive members are extension features; unentitled, every step must fail gracefully and its
refusal be recorded, and the FFF side is still attempted.

- The catalogs first: list the additive machines by vendor; list the print settings of the metal
  technology you will use, completely; then list the settings of the technology whose shipped
  settings share one name (two settings alike on every member but their description) and show that
  the listing tells them apart and marks them shared.
- Two additive setups, both taking the eight TRAY parts as their models: one on a metal powder-bed
  printer from the machine library with a print setting of a matching technology (state both
  exactly as the library names them); one on an FFF printer from the library (build one from a
  template if the library has none) with an FFF print setting. Before the metal setup lands, try
  three creates that must be refused and quote each: one with no machine at all, one on a milling
  machine, and one naming the shared-name print setting without its description qualifier; then
  create it with the qualifier and read back the description that landed. Read what each setup
  was seeded with before you add anything, and say so - a seeded operation is the platform's, not
  yours.
- Read each setup's offered families with their allowed flag; say which read not allowed, and note
  whether any refusal for a not-allowed family blames the extension while the extension is
  present.
- Arrange: on each setup an automatic arrangement of the eight parts on the platform, created with
  no cutting tool (a cutting tool handed to any additive family is refused - try it once on a
  support family and quote it; the family named 'additive individual strategies' is not an
  operation - try it and quote what the count says); generate the arrangement, then screenshot
  the platform from above and from an isometric to files. Then VARY the packing: the spacing
  between parts (two values), the arrangement type or strategy the operation offers, the platform
  margin, and the number of copies if the operation takes one - each a regenerate and a pair of
  screenshots, so the pictures show the packing change with the settings. Read the arrange
  operation's parameter names off its own listing before writing them. Where any read gives a
  part's position after the arrange, read it back per solve so the change is a number as well as
  a picture; if no read gives it, say the picture is the only evidence.
- Orientation: an automatic orientation of the sphere and of the L bracket; read what it decided.
- Support: every support family the setup offers, tried on the sphere - the solid volume support
  and a bar support at least; for each, whether it generated and what it produced, and what the
  platform refused and why.
- Read every additive operation's state after generating; say which generated, which the platform
  refused and why, and what the operations list says about tools on operations that carry none
  (they carry none by design - an operations read demanding a tool of one is a read defect, not
  yours; report it as such).
- Machining time and toolpath language do not apply here; report what the additive operations read
  instead (their state, their warnings, the generated data they carry).

Save the working document at the end; screenshots to one output folder named after it; list the
paths.

Report: the extension verdict; the catalog reads (machines, the complete metal listing, the
shared-name pair told apart); the three refused creates quoted; both setups (printer, print
setting, technology, description, the seeded contents); the offered families with their flags;
the arrangements, with each packing setting's before and after and the part positions read back
per solve where a read gave them; the orientation result; every support family's result; the
tool-demand finding on the operations read; every refusal quoted; the screenshot paths.

## Grader notes

- A good result, opened in Fusion: two additive setups each reading a printer and a print setting,
  the three refusals quoted and the qualifier picking the right twin, the eight parts arranged on
  both platforms in screenshots that visibly change between spacing values (the grader compares
  the top views pairwise, and the read-back positions where they exist), an orientation, every
  offered support family tried with its result or its quoted refusal, and a report that names
  what the setup was seeded with rather than counting it as its own and calls the tool-demand row
  a read defect. Known gap until it is built: an orientation or support operation lands in the
  setup's own Orientations or Supports container, where the per-operation tools (geometry
  targeting, the operations read, status by name, show, delete) cannot yet find it - grade those
  steps on what the agent reports about the refusal, not on the target landing.
- What a weak agent does: builds a milling setup on the tray; picks a print setting by id or by a
  name two settings share; reports the arrangement changed without a second screenshot or a
  position read; assigns a cutting tool to an additive strategy; treats 'tool unselected' on an
  additive row as an error to fix; skips the support families after the first refusal; or,
  unentitled, declares everything blocked without trying the FFF side.
- Axis this discriminates: MCP tooling on a fresh surface. cam_get(include=['machines'],
  machine_type='additive', vendor) and include=['print_settings'] with 'technology' and
  max_results (name_shared and description on colliding rows), cam_create_setup(operation_type=
  'additive', machine, print_setting, print_setting_description) and its three refusals, the
  tool-less cam_create_operation on additive_arrange, automatic_orientation, solid_volume_support
  and the other support families cam_get(include=['strategies'], setup=...) lists, its refusal of
  a tool on an additive family and its landing gate on additive_individual_strategies,
  cam_edit_operation on the arrange parameters, cam_generate over an additive setup, cam_get(
  include=['operations']) on additive rows, cam_create_machine(template='generic_fff') if the
  library has no FFF printer.
- First A/B to run: this brief before and after the lapse (which additive members the extension
  gates is unmeasured); then `--deny mcp__fusion-essentials__cam_create_machine`.
- Coverage (the coverage map, section 2): J1, J2, J3, J4, J5, J7 - six checks,
  every one extension-gated per member, which is why this brief runs before the lapse; J6 (sub-
  nests, build export, process simulation) is unreachable through this wire and is listed in
  CAM-CHAIN.md, not here. J1-J3 hold a stamped sweep row (the hub's additive act); J4, J5 and
  every generation here are the first measurement - grade them as findings, and record the
  parameter names the agent reads.
- Measured before any run: an additive setup needs a machine at create (a machineless input raised
  'Setup creation failed') and the machine must read isAdditiveSupported (a Haas mill is refused);
  the shipped library holds 411 print settings, and two FORMLABS_SLS settings collide on every
  member but description, so that name is refused until print_setting_description picks one; the
  EOS M 290 with an SLM setting created and offered additive_arrange, automatic_orientation and
  solid_volume_support, each created tool-less, the last two landing in the setup's own
  Orientations and Supports containers; bar_support with a tool is refused before the add;
  additive_individual_strategies answers a name while the count stays; GENERATION of any additive
  operation, the arrange parameters (Autodesk's sample names arrange_arrangement_type and
  arrange_object_spacing) and the support families are UNMEASURED here - this run measures them;
  cam_get(include=['operations']) reports tool_unselected on additive rows - a defect of the
  read, not the agent's; lateral_support read not allowed
  with the extension present, and the refusal must not blame the extension for it.
