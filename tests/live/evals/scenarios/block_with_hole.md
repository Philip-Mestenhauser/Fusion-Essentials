---
id: block_with_hole
tier: smoke
fixture: fresh empty design (staged with doc_new before you start; do NOT create/switch documents)
budget:
  max_tool_calls: 25
  max_tokens: 60000
expected_refusals: none
---

# Eval: block with a centered through-hole

**You are executing a live eval.** You are the ONLY agent on a single live Fusion thread - never
spawn or delegate to another agent. Use only `mcp__fusion-essentials__*` tools (deferred; load
schemas with ToolSearch). Start with `sys_capability_map` then `workspace_orient`. Work in the
ACTIVE design; do not create, open, or switch documents. Do the Task below, then verify EACH
postcondition with your OWN fresh read call and report a truthful PASS/FAIL/SKIP verdict quoting the
values you read - a claim that does not match a read is a FAIL. Full contract: `../README.md`.

The canonical create-and-verify task: go from a plain user request to correct geometry using only
the tool descriptions, and verify your own work.

## Task

> In the active empty Fusion design, model a rectangular block 40 mm long, 20 mm wide, and 10 mm
> tall, with a single 6 mm diameter hole drilled all the way through it, centered on the top face.
> When you're done, tell me the block's dimensions and confirm the hole goes through.

## Postconditions (verify each with a direct read; report the value you read)

- `model_inspect(target='')` -> exactly 1 solid body.
- bounding box within 2% of 40 x 20 x 10 mm.
- `find_geometry(kind='cylinder_face', radius=3)` -> at least one match (the 6 mm hole wall).
- body volume within 2% of 7717 mm^3 (40*20*10 minus a 3 mm-radius x 10 mm cylinder).

## report_truthful

State the block is ~40 x 20 x 10 mm and that the hole goes all the way through. Claiming a
through-hole while the read finds a blind hole (or none) FAILS - the verify-after-write check.

## Diagnostics (recorded, never gating)

Call count vs budget; whether you oriented before building; whether you re-read state after the cut;
any isError calls.
