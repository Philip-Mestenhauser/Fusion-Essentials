# Backlog

The tracked ledger of findings and deferred work. Rules: every review or audit finding lands
here, never only in chat; publish/privacy/history items are never below major; entries leave by
being done or explicitly declined, not by aging out.

## Publish blockers (before ANY push to a public remote)

- **major** - Squash or rebase this branch first: commits prior to the current tree contain a
  deleted notes doc whose blobs carry the team hub name and library names. A branch push
  publishes history, not just the tree. Keep the fork private until this is done.

## Next build

- **major** - Agent eval harness per `docs/eval-harness-spec.md` (outcome-only scoring, 3 runs,
  isolated agent config, planted-defect acceptance test). Delete the spec file when the
  harness's own README supersedes it.
- **minor** - `_cam_common.live_readiness` is document-wide only; `cam_get_status`'s by-name
  path and `cam_post` each mirror op-classification locally. Add an optional setup/operation
  scope so all three read one source (divergence risk, not lint-caught).

## Capability roadmap (from the 7-agent coverage audit; unshipped remainder)

- Verification gaps still open: sketch degrees-of-freedom count; surface-continuity measurement
  (patch continuity is settable but unmeasurable); mesh-quality diagnostics beyond is_closed;
  CAM stock-remaining / gouge / collision checks; a world-delta report after joint_drive.
- Semantic labels on created geometry: handles are ephemeral and the position-based self-heal
  can pick the wrong twin in symmetric/patterned geometry; a forward-declarable name at create
  time closes it.
- Mine `.claude/skills/insert-into-template` for tool candidates: it fakes two missing tools
  inline via scripts (a bbox-oriented-frame constructor and a container/placeholder classifier).
- Absent domains, deliberately unbuilt until a real need: sheet metal, Form/T-splines.

## Known dead ends (verified live - do not attempt)

- Drawing CONTENT is unreachable: no view/dimension/title-block read or edit surface exists;
  `Sheets.count` raises unimplemented; `CustomTables.add()` returns a phantom that never
  renders (a drawing-tables tool cannot work). The viable surface is exactly
  create -> update -> export(PDF).
- Hub team tool-library CREATE is UI-only (the API import path fails); populate-existing works.
- Manual drawing view placement is unsupported by the API; only automatic generation exists.

## Public-readiness minors (from the gold-standard review; all verified findings)

- **minor** - "confirmed live"/"verified live" phrasing in ~19 comments across ~16 tool files;
  state the fact without the provenance flourish.
- **minor** - Tracker-style labels in test comments: `Bug #N` (tests/test_inputs.py x3,
  tests/test_joint_create_edit.py, tests/test_handle_resolution_uniform.py); keep the technical
  explanation, drop the numbering. Optional: harden the evergreen lint (multiline phrases,
  digit-suffixed bug refs).
- **minor** - `Fusion-Essentials.manifest` has an empty "version" field.
- **minor** - Over-indented `_DESC = (` continuation blocks: surface_edit.py x3,
  surface_create.py x2, sketch_core.py x3, model_pattern.py, sys_selection.py.
- **minor** - model_fillet_chamfer.py docstring lacks the "WRITES." tag its siblings carry.
- **minor** - commands/mcpServer/__init__.py (empty) lacks the license header (1 of 121 files).
- **minor** - sys_execute_script error tracebacks embed the local temp path (includes the
  username). Loopback-only, so accepted risk unless posture changes.
- **minor** - No docs/ index; decide whether docs/tool-wiring.md deserves a pointer from the
  root README for fork readers.
- **decide** - Uniform instructional "we" voice in module docstrings: keep as house style or
  rewrite; em-dash density in CONTRIBUTING.md and commands/mcpServer/README.md; LICENSE files
  name the original author while source headers say "contributors" (legally fine, stylistically
  inconsistent).

## Decided (do not re-raise without new information)

- shared_state.py / timer.py keep their original third-party copyright header as-is.
- Git commit authorship (real name/email in history) is accepted for publication.
- Intent/composite workflow tools stay OUT of the MCP server permanently; that layer lives in
  skills. One tool = one Fusion capability.
- Eval scoring is outcome-only (part, truthful report, budget); process metrics are diagnostics.
- Plan documents are session scaffolding: gitignored via .claude/, deleted when work ships.
