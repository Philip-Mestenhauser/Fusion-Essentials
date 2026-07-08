---
id: insert_saved_part
tier: cloud
fixture: |
  a fresh active design as the host AND a saved source document reachable in the hub, named
  "<SOURCE_PART_NAME>" (the runner saves/points to one and fills the name into the Task). If no saved
  source part is reachable, report SKIP rather than a tool failure.
budget:
  max_tool_calls: 30
  max_tokens: 80000
expected_refusals: none
---

# Eval: insert a saved part, placed and rotated

**You are executing a live eval.** You are the ONLY agent on a single live Fusion thread - never
spawn or delegate to another agent. Use only `mcp__fusion-essentials__*` tools (deferred; load
schemas with ToolSearch). Start with `sys_capability_map` then `workspace_orient`. Work in the
ACTIVE design; do not create, open, or switch documents. Do the Task below, then verify EACH
postcondition with your OWN fresh read call and report a truthful PASS/FAIL/SKIP verdict quoting the
values you read - a claim that does not match a read is a FAIL. Full contract: `../README.md`.

Pull a saved cloud document in as a component, positioned and oriented. Looking up the source by name
(data_get) and inserting it is expected; just do not change which document is ACTIVE. Replace
`<SOURCE_PART_NAME>` with the source name the runner gives you.

## Task

> Insert the saved document named "<SOURCE_PART_NAME>" into the active design as a new component.
> Place it 20 mm along +X from the origin and rotate it 90 degrees about the vertical (Z) axis.
> Report the name of the inserted occurrence and confirm its placement.

## Postconditions (verify each with a direct read; report the value you read)

- `design_get(include=['tree'])` -> one new occurrence for the inserted part, `is_reference` true,
  with a source_id / source_name pointing at the source document (a live external reference, not a
  fresh empty component of the same name).
- that occurrence's world transform: X translation ~2.0 cm (20 mm, within 2%) and a ~90 degree
  rotation about Z (`assembly_probe` basis: x_axis ~ [0,1,0], y_axis ~ [-1,0,0], z_axis ~ [0,0,1]).

## report_truthful

Name the inserted occurrence and state it is placed ~20 mm +X and rotated 90 deg about Z, matching
your read. A "placed at the origin" or unrotated claim that disagrees with the transform FAILS.

## Diagnostics (recorded, never gating)

Expected tools: data_get (resolve the source by name -> its lineage URN), doc_insert_occurrence
(inserts as a source-tracking external reference; places at x/y/z and rotates about a world axis).
Whether you verified the placement after inserting; any isError calls that were environment (source
not reachable -> SKIP) vs. logic.
