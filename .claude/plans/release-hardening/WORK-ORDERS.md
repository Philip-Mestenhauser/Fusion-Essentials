# Release-hardening work orders

Paste-ready prompts for cheap/mid-tier agents. Each is SELF-CONTAINED (a fresh agent has
no conversation context). Consume with FINDINGS.md in this directory — prompts reference
its class sections instead of restating sites.

## How to run this plan

**Sequencing** (write-agents must not overlap on files — run one WO at a time unless
marked parallel-safe):

- Phase 0 (you, 5 min): confirm the three decisions in FINDINGS Class I (#1 write= policy,
  #3 docstrings, #4 one-tool-per-file). The proposed defaults are written there; WO-8 and
  WO-11 consume them.
- Phase 1: WO-1 (mutations) -> WO-2 (debris) -> WO-5 (bounded reads). Sequential: they
  touch overlapping files.
- Phase 2: WO-3 (resolvers), then WO-4 (bugs; has one live-Fusion step).
- Phase 3: WO-6 (helper extractions) — run its sub-orders one at a time.
- Phase 4: WO-7 (lints) and WO-10 (test gaps) — parallel-safe with each other.
- Phase 5: WO-8 (docstring scrub; per-family agents parallel-safe) then WO-11 (docs
  refactor, LAST so it describes reality). WO-9 (Choice kinds) anytime after Phase 1.

**Model routing** — matched to task shape, not prestige:

| Tier | Use for | Why it works |
|---|---|---|
| Cheap (Haiku-tier) | WO-2 debris, WO-8 scrub | Site list + banned-phrase grep + mechanical acceptance = no judgment needed |
| Mid (Sonnet-tier) | WO-1, WO-3, WO-5, WO-7, WO-9, WO-10 | One pattern taught deeply, applied N times, pytest-verifiable |
| Strong (Opus/Fable) | WO-4, WO-6, WO-11 | Cross-file design, live verification, policy writing |

**Prompt-craft rules that made the review work (reuse for any future fleet):**
1. Fragment by DEFECT CLASS, not by directory — teach one pattern deeply; the agent
   applies it 10x. Cheap models excel at narrow+repetitive, fail at open-ended judgment.
2. Self-contained: name the exact files to read FIRST (CLAUDE.md, the FINDINGS section).
   Never assume the agent knows anything from your session.
3. Closed scope: explicit file list + "do not touch anything else; if a fix requires it,
   STOP and report". Parallel write-agents need provably disjoint file sets.
4. Include a worked before/after example for the pattern (cheap models copy shapes).
5. Mechanical acceptance: exact commands the agent must run and paste results from
   (`py -3 -m pytest -q`, specific greps). No "make sure it's good".
6. Escape hatch: "if a site resists the pattern, skip it and report why" — prevents
   improvisation (improvisation is how the joint-scripting failure happened).
7. Require line-cited evidence in the report + a strict output format.
8. Verify sites before editing — FINDINGS line numbers can drift.

**How many agents:** read-only review scales wide (8 parallel worked). Write work: 1 agent
per WO, <=3 parallel only with disjoint files, each sized <=10 sites or <=6 files.

---

## WO-1 — Mutation sweep [mid tier, ~15 sites]

```
You are fixing honesty-contract violations in the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials. Read FIRST: CLAUDE.md (the "Honesty contract" section) and
.claude/plans/release-hardening/FINDINGS.md section "Class A".

THE PATTERN: a mutation wrapped in safe() or try/except:pass can fail silently while the
tool reports success. Fix each Class A site one of two ways:
(a) let the mutation raise — remove the safe()/try-pass so the handler's error path
    reports it (this is the default fix); or
(b) verify after the fact and report honestly — read the value back and emit a warning
    field when it didn't take (copy model_create_component's rename read-back, around
    line 74-81, or mesh_export's file-exists gate, ~299-314).
Worked example — WRONG:
    try: mi.isCombine = join
    except Exception: pass
    ...
    return ok({"joined": bool(join)})
RIGHT:
    mi.isCombine = join   # let a failure raise into the handler's error path
or, when the API can silently no-op:
    applied = safe(lambda: feature.isCombine)
    out["joined"] = bool(applied)
    if bool(applied) != bool(join): out["join_warning"] = "isCombine did not take"

RULES: verify each site's line numbers before editing (code may have drifted). Update the
tool's test in the SAME commit — including tests that currently enshrine the false
success (FINDINGS names test_show_toolpath TestFit as one). Do not change behavior beyond
the honesty fix. Skip-and-report any site where the right fix isn't clear. Touch nothing
outside Class A files.

ACCEPTANCE (run and paste): py -3 -m pytest -q (all green), py -3 tests/gen_spec.py.
REPORT: per site — file:line, fix chosen (a/b), test updated (name), or SKIPPED + why.
```

## WO-2 — Debris sweep [cheap tier, mechanical]

```
You are removing public-embarrassment debris from the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials, which is about to go public. Read FIRST:
.claude/plans/release-hardening/FINDINGS.md section "Class E" — it lists every site.

DO, per Class E: fix the mojibake test file (rewrite its corrupted docstring/comments in
plain ASCII; do not touch test logic); correct stale tool/file names in wire strings and
test docstrings; delete dead code, dead TOOL_DESCRIPTIONs, and empty section headers;
remove review-artifact labels ("Bug A", "Bug B", "THE FIX", "Option-B wrapper", audit
narratives) — keep the factual sentence about what the code does, drop the history;
remove roadmap promises from wire strings; fix the false wire claims (capability-map
"post", cam_edit_tools create_library omission, cam_get include slices, data_switch_hub
default, model_combine/model_mirror stale claims); fix sys_reload_addin's contradictory
purge NOTE to describe what the code does; regenerate tests/tool-wiring.md via
py -3 tests/gen_wiring.py.

RULES: text/comment/dead-code changes ONLY — if a fix would change runtime behavior,
SKIP it and report. Wire strings (descriptions/notes/errors) must stay pure ASCII.
Verify each site before editing.

ACCEPTANCE (run and paste): py -3 -m pytest -q; py -3 tests/gen_wiring.py --check;
py -3 tests/gen_manifest.py (then confirm CLAUDE.md map no longer says data_model_ops);
grep -rn "Bug A\|Bug B\|THE FIX\|Option-B\|set_sketch_text\|data_model_ops\|cam_read" commands/ tests/ returns nothing (or only justified hits, listed).
REPORT: per site — file:line, done/SKIPPED+why.
```

## WO-3 — Resolver adoption [mid tier, ~10 sites]

```
You are replacing hand-rolled name resolvers with the typed input kinds in the
Fusion-Essentials MCP server at c:\source\Fusion-Essentials. Read FIRST: CLAUDE.md
(the "Input kinds" section), commands/mcpServer/tools/_inputs.py (the kind classes:
OccurrenceRef, OccurrenceRefList, BodyRef, AxisRef), and
.claude/plans/release-hardening/FINDINGS.md section "Class B".

THE PATTERN: a tool resolves an occurrence/body/axis by first-match or substring name
lookup — the wrong-instance risk the kinds exist to refuse. For each Class B site:
declare the input with the kind (tool.add_input_property(*kind.as_property()) or the
kind's schema), resolve through the kind (it refuses ambiguity with a candidate list),
and delete the local resolver. The reference implementation to copy is
design_delete_occurrence.py (uses _inputs._resolve_occurrence correctly); view_section.py
shows PlaneRef+shared occurrence resolution together.

RULES: one file at a time; run that file's tests before moving on. Where the kind lacks a
selector the tool needs, EXTEND THE KIND in _inputs.py (and add a test in test_inputs.py)
— do not fork a local copy. Site B10 (find_geometry aggregation) is a judgment call:
document the aggregate behavior in the description OR make it refuse ambiguity — pick one
and say why. Update each tool's tests in the same commit (ambiguity now errors — tests
asserting first-match must flip). Skip-and-report anything unclear.

ACCEPTANCE (run and paste): py -3 -m pytest -q -p randomly; py -3 tests/gen_manifest.py
(kinds map may change); py -3 tests/gen_spec.py.
REPORT: per site — file, kind adopted, kind extended? (what), tests changed, or SKIPPED+why.
```

## WO-4 — Confirmed bugs + the joint contradiction [strong tier; ONE live-Fusion step]

```
You are fixing confirmed behavioral bugs in the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials. Read FIRST: CLAUDE.md, then
.claude/plans/release-hardening/FINDINGS.md section "Class C" (11 items).

Fix C1 (TargetRef discards ambiguity error — propagate the sub-resolver's error and fix
the stale comment), C2 (_write_guard acted_on stamps pre-handler doc — re-read identity
after the handler runs), C4/C5 (folder-nested op lookup — copy the recursive walk
cam_delete/cam_reorder use; note the test at test_cam_edit_operation.py:184-194 encodes
the bug and must flip), C6 (snapshot key collision — key by document id, fall back to
name), C7 (tolerance misreport — convert the default into caller units), C8/C9 (silent
axis/unit fallbacks — error like joint_create does), C10 (add truncated flag), C11
(fix the stale CLAUDE.md cam_generate note to describe cam_get_status).

C3 (joint_drive vs joint_edit rotationValue contradiction) needs LIVE verification:
after unit tests pass, sys_reload_addin, wait for 127.0.0.1:27182/health, open a scratch
document, build a two-part revolute joint, call joint_drive, and observe whether the MCP
connection survives. If it survives: soften joint_edit's refusal to redirect to
joint_drive (not assembly_move) and record the finding in docs/fusion-api-notes.md. If it
drops: gut joint_drive's rotationValue path to the same refusal + redirect. Either way
both wire descriptions must end up telling the same story. Use a throwaway document only.

RULES: every fix gets a test in the same commit (C1 in test_inputs.py, C2 in
test_write_guard.py, ...). Skip-and-report anything that resists.
ACCEPTANCE (run and paste): py -3 -m pytest -q -p randomly; the live C3 transcript.
REPORT: per item — fix summary, test name, C3 verdict + evidence.
```

## WO-5 — Bounded reads [mid tier, 7 sites]

```
You are enforcing the bounded-read rule in the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials. Read FIRST: CLAUDE.md ("Reads disclose progressively" -
the "Bound it" rule) and .claude/plans/release-hardening/FINDINGS.md section "Class D".

THE PATTERN: any list that grows with the model takes a cap and reports "truncated".
Copy find_geometry (max_results + match_count vs returned) or workspace_orient
(_DIGEST_LIMIT=25). For each Class D site: add a default cap (pick a generous one - 50
occurrences, 100 joints, 200 diff rows; judgment allowed), an optional max_results-style
knob where the tool already has inputs, and a truncated flag. ALSO fix assembly_probe's
description: it claims "every occurrence" but reports top-level only - either walk
allOccurrences (capped) or say "top-level occurrences". D6 is flags-only (caps exist).

RULES: caps must not change small-model behavior (results under the cap are identical).
Update tests in the same commit; add one at-the-cap test per site. Skip-and-report.
ACCEPTANCE (run and paste): py -3 -m pytest -q; py -3 tests/gen_spec.py.
REPORT: per site - cap chosen, knob added?, truncated flag, test name.
```

## WO-6 — Helper extractions [strong tier; run sub-orders ONE AT A TIME]

Sub-orders, each its own agent run (disjoint files, but sequential is safer for tests):
6a `_get_cam` consolidation (13 files -> _cam_common.get_cam; tests monkeypatch the shared
seam); 6b `_joints.py` (keypoint table x3, _apply_motion x2, _find_joint fork — pull FROM
joint_create_edit so it shrinks; joint_at_geometry becomes a thin consumer — evaluate the
MERGE-into-joint_create verdict while there); 6c `_view_common.py` (ONE camera-orientation
table — reconcile the three sign conventions with a screenshot-verified live check per
view); 6d `_export.py` (design_export + mesh_export substrate; also gate design_export
success on file existence if WO-1 hasn't); 6e `_data_common` absorbs the URN decoder +
_data_read's duplicate resolvers; 6f small `_common` moves (_target_sketch/_OPERATIONS,
_CM_TO_UNIT/_ptxyz, _resolve_entity; point sketch_core/surface_create at resolve_sketch).

```
You are extracting a shared helper in the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials. Read FIRST: CLAUDE.md ("Reuse before you write" + the
helper list), .claude/plans/release-hardening/FINDINGS.md section "Class G", and every
file named in sub-order <6x> below.

TASK <paste the 6x line from WORK-ORDERS.md>. Method: (1) diff the duplicate
implementations - if they diverge, determine which behavior is correct (check tests +
docs/fusion-api-notes.md; for camera vectors, verify live with view_screenshot per view);
(2) create/extend the helper with the correct version + a module-level one-line purpose
comment (no essay); (3) point every consumer at it and DELETE the copies; (4) update
tests to patch the shared seam (monkeypatch, not module pokes); (5) update the MAP_BLURB
so gen_manifest advertises the helper.

RULES: behavior-preserving except where FINDINGS names a divergence to resolve (say which
side won and why). _-prefixed helpers are never tools (no register_tool). One sub-order
only; touch nothing else.
ACCEPTANCE (run and paste): py -3 -m pytest -q -p randomly; py -3 tests/gen_manifest.py;
grep proving zero remaining local copies (e.g. grep -rn "def _get_cam" commands/mcpServer/tools/ shows only _cam_common).
REPORT: helper created, consumers rewired (count), divergences resolved (which won), tests changed.
```

## WO-7 — New lints (the CI substitute) [mid tier]

```
You are adding lint tests to the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials - the repo enforces conventions as pytest lints instead of
CI. Read FIRST: CLAUDE.md, tests/test_tool_naming.py and
tests/test_write_status_annotations.py (the house lint style: registry-driven, each test
names the convention it enforces and the bug it prevents).

ADD four lints:
1. test_wire_ascii.py - every registered tool's description, input descriptions, and any
   module-level *_DESCRIPTION constant is pure ASCII (ord(c) < 128). Sweep via the
   registry like test_write_status_annotations does.
2. test_generated_docs_current.py - shells `py -3 tests/gen_spec.py --check`,
   `gen_manifest.py --check`, `gen_wiring.py --check`; fails with the regen command in
   the message. (This replaces CI for doc freshness - agents run pytest constantly.)
3. test_helper_duplication.py - a DENYLIST lint: known-shared symbols may only be defined
   in their home module (start with: def _get_cam -> _cam_common; def _b64url_decode ->
   _data_common; extend as WO-6 lands). Failure message must name the import to use -
   the error message is the teaching surface.
4. test_no_first_match_resolvers.py - extend test_occurrence_ref_lint.py's frozen
   _ROUTED_TOOLS list to sweep ALL tool modules for the itemByName-first-match smell it
   already detects, with an explicit allowlist for justified cases (comment why, per entry).
RULES: lints must pass on the CURRENT tree (add allowlist entries for not-yet-fixed sites,
referencing FINDINGS class letters, so the list burns down as WOs land). House style:
plain pytest, no new deps.
ACCEPTANCE (run and paste): py -3 -m pytest -q -p randomly; each new lint's failure mode
demonstrated once (break something, show the message, restore).
REPORT: four lints + allowlist counts.
```

## WO-8 — Docstring policy scrub [cheap tier; one agent per family, parallel-safe]

Precondition: Class I #3 decision confirmed (proposed: short factual module docstrings
allowed; war-story/migration/review narrative banned; API-grounding blocks move to
docs/fusion-api-notes.md). Update CLAUDE.md's rule text FIRST (one manual edit), then run
per-family agents (model / sketch+surface / cam / joints+assembly / doc+data /
design+mesh / view+sys+param):

```
You are normalizing docstrings in the <FAMILY> tool files of the Fusion-Essentials MCP
server at c:\source\Fusion-Essentials. Read FIRST: CLAUDE.md's docstring rule (just
updated - follow it exactly) and docs/fusion-api-notes.md (where API facts go).

For each file in <FILE LIST>: (1) module docstring shrinks to <=6 lines - what the
tool(s) do + at most one load-bearing API gotcha; (2) MOVE any real API-grounding facts
("Grounded in adsk...", verified signatures/behaviors) into docs/fusion-api-notes.md
under the matching section - do not lose them, do not duplicate what's already there;
(3) DELETE design-history/war-story/migration narrative ("WHY THIS EXISTS", "closes the
gap", "was tried but rejected", before/after tales) - no replacement; (4) handler
docstrings that restate the wire description shrink to one line or nothing; (5) keep
short # comments that state a non-obvious constraint.

RULES: docstrings/comments ONLY - zero code or wire-string changes (wire strings =
TOOL_DESCRIPTION/notes/errors; if one needs fixing, report, don't touch). Validation is
mechanical, not judgment: the greps below.
ACCEPTANCE (run and paste): py -3 -m pytest -q;
grep -n "WHY THIS EXISTS\|closes the gap\|Grounded in adsk\|was tried\|design point" <files> returns nothing;
per-file line count of moved-to-api-notes facts.
REPORT: per file - docstring before/after line counts, facts moved (bullet list).
```

## WO-9 — Choice-kind conversions [mid tier]

```
You are converting free-string enum inputs to the typed Choice kind in the
Fusion-Essentials MCP server at c:\source\Fusion-Essentials. Read FIRST: CLAUDE.md
("Input kinds"), the Choice class in commands/mcpServer/tools/_inputs.py, and
.claude/plans/release-hardening/FINDINGS.md section "Class F".

For each Class F input: replace the hand-typed {"type":"string"} property with a Choice
(values = exactly what the runtime guard accepts today), keep the runtime guard (defense
in depth), and DELETE the value list from the prose description (the schema now carries
it; keep only meaning notes the enum can't express, e.g. snap semantics). joint_drive's
inline units enum switches to _inputs.UNITS.

RULES: the value set must not change - if prose/guard/code disagree on legal values,
skip-and-report that input. The MCP client sees a schema change; note that in your report.
ACCEPTANCE (run and paste): py -3 -m pytest -q; py -3 tests/gen_manifest.py.
REPORT: per input - tool, values enum'd, prose removed y/n, or SKIPPED+why.
```

## WO-10 — Test gaps + leak fixes [mid tier]

```
You are closing test gaps in the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials. Read FIRST: tests/README.md (canonical patterns - copy
test_design_get/test_model_inspect/test_quoting shapes; monkeypatch/fixtures ONLY, never
imperative module pokes) and .claude/plans/release-hardening/FINDINGS.md section "Class H".

DO: (1) write the missing tests - cam_compare diff logic; _cam_common's
_invalidation_reasons/_op_primary_state/_hms/machining-time math (it had a 100x unit bug
- pin the constant); joint_create_origin handler-level guards; cam_edit_folders
moveInto-False; sys_reload_addin._purge_addin_modules. Also unify _cam_common's state-1
naming ("invalid" vs "out_of_date" - pick out_of_date, it matches the op-level name).
(2) fix the six named leaks in H6 - convert the raw assignments to monkeypatch inside
fixtures so teardown is structural.
RULES: new tests follow the canonical pattern exclusively. One behavior per test, named
like a spec line. Prove each new test bites (break, observe red, restore - per
tests/README.md).
ACCEPTANCE (run and paste): py -3 -m pytest -q -p randomly (twice, different seeds);
py -3 tests/gen_spec.py.
REPORT: tests added (names), leaks fixed, the state-naming unification diff.
```

## WO-11 — Docs refactor [strong tier, LAST]

```
You are restructuring agent-facing docs for the Fusion-Essentials MCP server at
c:\source\Fusion-Essentials, now that release-hardening fixes have landed. Read FIRST:
CLAUDE.md, tests/README.md, docs/fusion-api-notes.md, CONTRIBUTING.md, and
.claude/plans/release-hardening/FINDINGS.md ("Named exemplars" + Class I decisions).

BUILD the progressive-disclosure layering (subdirectory CLAUDE.md files load only when
an agent works under that directory):
1. Root CLAUDE.md - slim to the constitution: kinds table, honesty contract, naming
   schema, Read/Edit kinds, the generated maps. Move tool-authoring how-to detail down a
   level. Fix anything stale (verify every named tool/file still exists).
2. commands/mcpServer/tools/CLAUDE.md (NEW) - the tool-authoring recipe: helper map with
   each family helper's purpose (_common/_inputs/_outputs/_joints/_view_common/_export/
   _cam_common/_data_common/_data_read/_holder), the named exemplars to copy (from
   FINDINGS "Named exemplars"), the decided docstring rule, the one-tool-per-file rule
   with the grandfathered-file list, the write= policy for Acquire tools (Class I #1
   decision), and "the lints will catch" list (so authors know what's machine-enforced).
3. tests/CLAUDE.md (NEW) - the honest harness story: the canonical pattern (mandatory for
   new tests), the legacy bespoke pattern (exists in N files, do not copy, migrate
   opportunistically when touching a file), the dual-seam trap, the prove-it-bites rule.
   Rewrite tests/README.md's adoption claims to match reality (FINDINGS H8 has counts).
RULES: no content invented - everything traceable to existing docs or FINDINGS; every
claim about the code verified against the code. Keep root CLAUDE.md under ~120 lines of
prose (tables excluded). ASCII wire discipline note stays.
ACCEPTANCE: py -3 -m pytest -q (naming/doc lints still green); py -3 tests/gen_manifest.py
splices cleanly into the new root file.
REPORT: the three files' outlines + what moved where + staleness fixed.
```

## Deferred (decided against, or opportunistic only)

- GitHub Actions / CI: owner prefers lints-in-pytest; WO-7's generated-docs meta-test is
  the substitute.
- Test-harness migration sprint (68 bespoke files): NOT worth a dedicated pass -
  order-independence already holds via conftest compensation. Migrate per-file whenever a
  WO touches its tests; tests/CLAUDE.md (WO-11) makes the canonical pattern mandatory for
  new tests.
- One-tool-per-file split sprint: grandfathered (Class I #4). doc_lifecycle may split
  opportunistically during WO-1 #11.
- view_screenshot_multi merge into view_screenshot; joint_at_geometry merge into
  joint_create: evaluate during WO-6b/6c, not standalone.
- show_toolpath.py rename to match its tool: bundle into whichever WO touches it last.
