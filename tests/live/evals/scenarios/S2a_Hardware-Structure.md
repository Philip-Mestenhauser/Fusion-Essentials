---
id: S2a_Hardware-Structure
tier: pipeline
fixture: P1-Gimbal (the S1 artifact - the topology-A sketch-only cast) OPENED as the active
  document by the orchestrator from the Pipeline-v1 tree BY URN, active hub PINNED first. The
  agent models the eight primary bodies and saves AS A NEW document (P2a-Gimbal); P1-Gimbal's
  cloud version must remain untouched. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 110
  max_tokens: 128000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S2a - Hardware I: the zoned structure becomes solid

Goal-shaped, first half of the hardware stage (split so one blind run fits the harness's
task-time cap). The eight primary bodies only - frame, pedestal+post, carrier, both rings,
rotor, shaft, crank - built to the skeleton's zones with ZERO mutual contact. No pins, no
bores: the pivot interfaces are the NEXT stage. Grades region selection, feature ownership,
zone discipline, and volume honesty. The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: the saved document "P1-Gimbal" - a three-axis gyroscope
foundation as sketch-only components built around a shared skeleton (one center point, three
mutually perpendicular pivot construction lines): frame with a pedestal sub-component, a carrier,
two coplanar nested rings, rotor, rotor shaft, and a frame crank. Work IN the active design; do
not create, open, or switch documents. The final save must write the modeled parts AS A NEW
document and leave the P1-Gimbal cloud artifact at the version you found it. If the save-target
project does not exist, STOP and report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. LOOK at your work with screenshots as you
go and read them against fresh numbers.

GOAL - model the gyroscope's PRIMARY BODIES, each owned by its OWN component. The skeleton is
the law, and so are the ZONES: every part occupies its own space and NO TWO BODIES may touch or
overlap - the pivot pins that bridge the parts are the NEXT stage's job, so at
the end of THIS stage the cast is completely contact-free. Parts STAY AT THEIR SKETCHED
POSITIONS on the skeleton: achieve clearance by your choice of extrude direction and extents
(the pedestal may grow DOWNWARD, away from the ring plane), never by moving occurrences - the
ring bands stay centered on the skeleton's ring plane through the shared center.

- The FRAME as a plate with its central opening OPEN (not a filled disc), and the PEDESTAL
  sub-component as a solid base with its mounting POST rising along the yaw axis, stopping
  clear of the ring plane's parts.
- The CARRIER as a solid yoke: seated on the post, reaching TOWARD the outer ring's two pivot
  points on their shared axis - approaching, never touching.
- BOTH RINGS as hollow BANDS (not discs), extruded SYMMETRIC about the shared ring plane so the
  two rings stay coplanar - nested with clearance, never stacked, never touching.
- The ROTOR as a solid disc on its ROTOR SHAFT, the disc sized to spin inside the inner ring with
  clearance. The shaft runs along the spin axis but STOPS SHORT of the inner ring on both ends (a
  short shaft) - do not span the shaft into the ring band; the sketched shaft that reaches the ring
  would touch it, so build it short and leave clearance rather than moving any occurrence.
- The CRANK as a solid on the frame side, clear of everything.
- Do NOT model pivot pins or drill pivot bores, and do NOT create joints - later stages do.
- RETENTION IS OUT OF SCOPE for the whole hardware chain, not just this stage: nothing in this cast
  (or the pin stage that follows) holds a part against axial escape. Do NOT add a retaining feature
  (shoulder, cap, clip, flange) to bridge the zero-contact gap - the contact-free cast is the
  correct end state, and the parts stay in place by their sketched positions on the skeleton.

Finally save AS A NEW document: P2a-Gimbal into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}
(create the folder path if missing; never a project).

You choose profiles, extents, and order. Grading is on STATE and HONESTY, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report actual values WITH units.

- every component (frame, pedestal, carrier, both rings, rotor, shaft, crank) holds exactly its
  own solid body, owned by the RIGHT component (a fresh tree read shows it).
- the rings are HOLLOW BANDS and the frame opening is OPEN: prove with volume reads against the
  filled-shape estimate you compute from measured extents (report both numbers) - AND the two
  ring bodies are COPLANAR ON THE SKELETON: fresh reads show both bands centered on the
  SKELETON's ring plane - the plane through the shared center perpendicular to the yaw axis
  (report each band's mid-plane position AND the shared center's position; they coincide).
- the rotor sits on its shaft along the spin axis (a fresh coaxiality read), and the disc clears
  the inner ring bore (report disc radius vs bore radius).
- ZONE CLEARANCE - the defining check of this stage: a fresh interference check over the whole
  cast reports ZERO interfering pairs (report the checker's actual output; any contact is a
  defect you fix before saving).
- timeline is healthy (a fresh health read: no errors).
- doc_get -> active document saved as "P2a-Gimbal", real URN, version >= 1, in MCP Test Project /
  Pipeline-v1/{{RUN_FOLDER}} - AND a fresh cloud read shows P1-Gimbal still at the version you
  found it.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read, with units>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <screenshots vs numbers - name any disagreement, or "consistent">
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <TOOL:<name> - <defect> | WIRE:<name> - <gap> | CAPABILITY - <missing step> |
  EVAL - <scenario weakness> | CLEAN - <nothing to fix>>
NOTES: <short. Discoveries a description should have carried; every pushback + recovery.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: multi-profile region picking, feature ownership across the 8-part cast
  incl. the NESTED sub-component, symmetric extents, ZONE DISCIPLINE (the zero-contact cast),
  and volume-level self-verification. The pivot interfaces (pins, bores, clearances) belong to
  S2b - an executor that builds them here has over-reached the goal.
- WHY THE SPLIT: an unsplit blind S2 measures ~100+ min wall-clock, over the harness's ~60-min
  task cap (rule: split the task, never dodge the cap). S2a+S2b each fit with margin.
- WHY ZERO-CONTACT: bulk overlaps carried in P2 ambush S3's interference grade.
  Structure-stage clearance is graded BEFORE any legitimate contact
  (pins) exists, so the check is binary: zero pairs or defect.
- WHY RETENTION IS OUT OF SCOPE (design decision, not a permitted-contact carve-out): the whole
  value of this stage is the BINARY zero-contact grade, so retention (a feature that necessarily
  touches a neighbor to capture it) cannot be permitted here without reintroducing the contact
  ambiguity the split was built to remove. The honest formulation is therefore "retention is not
  modeled anywhere in this chain" - the assembly is held kinematically by pins in clearance bores
  (S2b), and true axial capture is simply absent. An executor that adds a retaining feature to
  close a clearance gap has broken the zero-contact law, not saved the part; grade it a defect.
- WHY THE SHORT SHAFT: the S1 skeleton sketches the rotor shaft spanning to the inner ring, so a
  literal build touches it. The buildable zero-contact resolution is a short shaft that stops clear
  of the ring on both ends - blessed here so the grader does not read a short shaft as an
  incomplete build.
- WHY STAY-PUT + THE SKELETON-ANCHORED PLANE: with zero-contact demanded
  and no stay-put line, a blind executor can legally assembly_move the ring system to Z=85 to
  clear the pedestal post (S1's flat-sketched plan collides vertically at the shared center),
  which silently forfeits the skeleton - S2b's pins can never be collinear with construction
  lines 85mm away. The buildable resolution is extrude-direction zoning (pedestal downward).
- Handoff: P2a carries the eight primary bodies, contact-free. S2b adds pins + bores and is
  the stage S3's fixture consumes (P2-Gimbal).
- Staging: doc_open the S1 artifact BY URN (force_api_open), confirm active; run the block.
- Budget: measured PASS run + 25% (96 calls /
  102.5k output tokens / 20.8 min - S1's vertical zoning is what keeps collision thrash out
  of that number).
