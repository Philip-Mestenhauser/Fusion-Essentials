# The CAM completeness chain - C0 to C7

Eight briefs under scenarios/ (C0_CAM-Fixture through C7_CAM-Job-End-to-End) drive every CAM tool
family on this server through one small fixture set at representative depth: every toolpath
family, each with its settings varied, on parts small enough that a toolpath computes in seconds,
and every one of the 98 live checks of the coverage map (section 2) placed in
exactly one brief's task list. The proctor (proctor.py, README.md) runs a brief blind; the operator's
foreign agent runs the same text by hand. Runs are saved; the operator grades. This file is the run
order, the start every later brief shares, the coverage per brief, the results convention, and
the rules of the road.

## The fixture, run once

C0_CAM-Fixture is a MODELLING brief: it builds the six demo parts and the document tool library
and saves the document as CAM-Fixture-<stamp> into the chain's folder. It runs ONCE per chain.
Every later brief starts by copying the newest CAM-Fixture-* in that folder to its own working
document (C1-Milling2D-<stamp>, C2-3DMultiAxis-<stamp>, C3-Turning-<stamp>, C4-Recognition-<stamp>,
C5-Additive-<stamp>, C6-Arrange-<stamp>, C7-Job-<stamp>), opening the copy and working there, and
never saves the fixture. Nothing is distributed with the repository: a user stands the chain up by
running C0 into a folder of their own. A defective fixture is fixed by running C0 again into the
same folder; later briefs take the newest stamp.

## The folder

Every brief carries {{PROJECT}} and {{FOLDER}}, the project and folder named in
tests/live/cloud_config.local.json (untracked; README.md gives its shape). The proctor fills them
with the project and its set folder, Eval-<date>-<set> under the configured folder; an operator
handing a brief to an agent by hand fills them in the text before sending it, with the SAME two
names in every brief of one chain. On a later day pass the proctor the full set name
(`--set Eval-<date>-CAM`): a bare `--set CAM` makes a fresh dated folder holding no fixture.

## Run order and the extension deadline

The chain runs with or without the Manufacturing Extension, and the gated rows say so; "the lapse"
below is the switch from a run with the extension to a run without it. Run the gated briefs first,
the base ones after, and the gated ones again once the extension is gone:

| order | brief | needs the extension | what it grades |
|---|---|---|---|
| 1 | C0_CAM-Fixture | no | the fixture: parts as dimensioned, the tool library |
| 2 | C2_CAM-3D-and-MultiAxis | yes: swarf, advanced swarf, multi-axis, rotary, 3+2, steep-and-shallow, deburr, probe geometry, inspect surface | the 3D and multi-axis families with settings varied, probing, the 3-axis post refusal |
| 3 | C5_CAM-Additive | yes: metal additive (which members is unmeasured) | additive setups, arrange, orientation, support |
| 4 | C6_Design-Arrange | the 3D solver (2D unmeasured) | the arrange solvers and their settings |
| 5 | C4_CAM-Recognition-Rest-Templates | corner rest, boss-aware pocket recognition, surface groups | recognition, rest machining, folders, order, templates |
| 6 | C1_CAM-Milling-2D | no | the setup probed, the 2D families with settings varied, the selection kinds |
| 7 | C3_CAM-Turning | no | the turning families, the stock modes, milling on rest stock, the turning post |
| 8 | C7_CAM-Job-End-to-End | no | generation every way, sheets, posts, readiness, inspection, the closing statement |
| after the lapse | C2, C5, C6, C4 again, then C7 as the base-licence control, plus the rerun group below | - | graceful failure: every gated step refused by name with an alternative, nothing created for it, the base families still valid |

The same brief under two licences is the A/B this chain exists for. No brief changes for the
rerun; each gated brief already tells the agent to try the gated step, quote the refusal and go
on, so the refusal list is the graded deliverable then. Grade the refusal text against the
graceful-failure shape (the extension named, the evidence, a measured alternative, no silent
fallback), and record a refusal that guesses as a finding.

## Coverage - where each of the 98 checks lands

The map's section 2 numbers its checks A1..L6; every brief's grader notes carry a Coverage bullet
naming the ones it holds, so a grade can say which checks a run reached and not only whether the
part came out. The placement, 88 checks in the briefs and 10 in this file:

| brief | checks | count |
|---|---|---|
| C0_CAM-Fixture | A1, A2, A3, A4, A6, A8, A9, A10 | 8 |
| C1_CAM-Milling-2D | A5, A7, B1, B6, B7, B9, B10, B12, C1, C3, C4, C5, C15, D1, D8, D11, D13, E7 | 18 |
| C2_CAM-3D-and-MultiAxis | B11, C6, C7, C8, C11, C12, C13, C17, D4, D5, D6, D9, E3, G5 | 14 |
| C3_CAM-Turning | B2, B4, B5, B8, C10, D7, G4, G7 | 8 |
| C4_CAM-Recognition-Rest-Templates | B13, C2, C9, C14, C16, C18, C19, D2, D3, H1, H2, H4, I1, I2, I3 | 15 |
| C5_CAM-Additive | J1, J2, J3, J4, J5, J7 | 6 |
| C6_Design-Arrange | K1 | 1 |
| C7_CAM-Job-End-to-End | B3, B14, D10, D12, E1, E2, E4, E6, E8, E9, F1, G1, G2, G3, G6, G8, G9, G10 | 18 |
| this file, the rerun group | E5, L1, L2, L3, L4, L5, L6 | 7 |
| this file, unreachable through the wire | F2, H3, J6 | 3 |

The tool-library checks sit in C0 because the operator put the library in the fixture eval; A5 and A7
need an operation to exist and sit in C1. The 18 extension-gated checks (C7, C12, C14, C17, C19,
D5, D6, D9, E5, G5, J1-J7, K1) all sit in briefs ordered 2 to 5 or in the rerun group, never in a
brief ordered after the lapse. Corner rides C4's rest set although the map files it under D5.

The rerun group, run after the lapse by the operator with any agent, on a fresh copy of the
fixture unless a check says otherwise, and recorded like any run:
- L1: the orientation reads the extension absent and the capability map names the gate.
- L2: a create on each of the 17 extension strategies the A/B measured (advanced_swarf, corner,
  deburr, feature_construction, hole_recognition, inspect_surface, multi_axis_contour,
  multi_axis_morph, multiaxis_finishing, multiaxis_roughing, probe_geometry, rotary_contour,
  rotary_finishing, rotary_pocket, steep_and_shallow, swarf, three_plus_two) is refused before
  anything lands, names the Manufacturing Extension and an allowed alternative, and the operation
  count does not move.
- L3 and E5: copy the PRE-LAPSE C2 working document (the one that holds extension operations) and
  generate over its saddle setup: the extension operations are excluded and named, the rest launch
  under one handle; a scope holding only extension operations answers that nothing was launched;
  a selection with generation requested on one of them lands the selection and withholds the
  launch. This is the one post-lapse step that needs a document saved before the lapse.
- L4: probe (Probe WCS), flow2 and geodesic still generate on the base licence - their extension
  marks in STRATEGY_COMPETENCE.md and the sweep gates then fall.
- L5: hole recognition, pocket recognition plain and with bosses, 2D and 3D arrange, and a PMI
  write, each attempted unentitled and its refusal (or its success) recorded, so each tool's
  refusal text can be the measured one.
- L6: lateral_support still reads not allowed and its refusal must not blame the extension for it
  (it read not allowed WITH the extension present).
- J5's post-lapse half rides the C5 rerun: which of the offered additive families the extension
  gates. K1's rides the C6 rerun: which arrange solvers refuse.

Unreachable through this wire, so no brief asks for them: F2 (inspection results from a probed
part need a machine run or an operator-supplied document), H3 (hole-template authoring has no tool),
J6 (sub-nests, build export and process-simulation decks have no tool). They stay on the map as
probe-first rows; an agent that reports "no route" for one of them is right.

## Running a brief

Through the proctor:

```
py -3 tests/live/evals/proctor.py C0_CAM-Fixture --set Eval-<date>-CAM
py -3 tests/live/evals/proctor.py C2_CAM-3D-and-MultiAxis --set Eval-<date>-CAM
```

By hand: the operator prepends preamble.md, fills the two tokens, hands the text to the agent
verbatim (nothing added, no tool names - the brief and the preamble are all it gets), and says
when the run starts and when it ends. A brief that stalls or runs out of turns is run again into
the same set; the grade covers what landed.

The single-driver rule: while a run is in progress no other agent, session or script drives
Fusion - not a sweep, not a probe, not a second eval, not a click in the UI (a dialog left open
parks the pump). The operator declares the start and the end of a run; between them the executing
agent is the only driver. No tool source is edited during a run, and the add-in is not reloaded.

Every generate in these briefs is followed by a poll to completion, a machining-time read, the
empty-toolpath census, an isolated screenshot to a file and a comparison read of the variant
against its baseline; every arrange solve by a read-back and a screenshot; every refusal case is
quoted, never worked around. The briefs say so in plain language and name no tool beyond the
preamble's two; the grader notes name the tools, which is where the `--deny` A/Bs come from.

## What a run saves (the results convention)

`results/<set>/<scenario>_<nn>/` is the proctor's layout (README.md): prompt.txt, transcript.jsonl,
report.txt, iso.png, run.json, and one row in results/index.md; the proctor also saves the active
document as <scenario>_<nn> in the set folder. A hand run uses the same directory and the same
row: the operator writes prompt.txt (the text as sent) and report.txt (the agent's closing report,
verbatim), copies the agent's output folder (screenshots, NC files, setup sheets) in as files/,
and adds run.md holding the date, the agent and its model, the start and end times, the extension
state as the agent read it, the tool-call count if the agent's transcript gives one, and the
working document's name and link; the index row carries the agent's model, harness "hand", calls
and out tokens as "-" when unknown, ended "report", and the document link.

The graded document is the proctor's <scenario>_<nn> save for a proctor run and the agent's own
<stem>-<stamp> working document for a hand run; a proctor run leaves both in the folder. The
operator grades from the saved document, the report and the screenshots against the brief's grader
notes - the Coverage bullet says which checks to look for - and writes grade.md beside run.json as
the earlier sets do; the proctor grades nothing. A finding a grade surfaces is a lead, measured
live before it is recorded as a defect.
