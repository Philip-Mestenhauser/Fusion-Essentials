---
id: surface_thicken_bodies
tier: smoke
fixture: fresh empty design (staged with doc_new before you start; do NOT create/switch documents)
budget:
  max_tool_calls: 35
  max_tokens: 90000
expected_refusals: none
---

# Eval: surface to solid, report the result bodies

**You are executing a live eval.** You are the ONLY agent on a single live Fusion thread - never
spawn or delegate to another agent. Use only `mcp__fusion-essentials__*` tools (deferred; load
schemas with ToolSearch). Start with `sys_capability_map` then `workspace_orient`. Work in the
ACTIVE design; do not create, open, or switch documents. Do the Task below, then verify EACH
postcondition with your OWN fresh read call and report a truthful PASS/FAIL/SKIP verdict quoting the
values you read - a claim that does not match a read is a FAIL. Full contract: `../README.md`.

Drive the surface tools to a solid, then report the EXACT result body the feature produced (read off
the model, not the body you think you made).

## Task

> In the active empty Fusion design:
> 1. Create an open SURFACE: a flat 50 mm x 30 mm rectangular surface patch (a zero-thickness
>    surface body, not a solid).
> 2. Thicken that surface by 3 mm to turn it into a SOLID body.
>
> Then tell me the NAME of every solid body that now exists and confirm the thickened result is a
> solid (not still a surface).

## Postconditions (verify each with a direct read; report the value you read)

- `model_inspect(target='')` -> at least 1 solid body, bounding box ~50 x 30 x 3 mm (within 2%). A
  bbox far from 50x30x3 means the surface was not a flat 50x30 patch - report the actual numbers, do
  not call it done.
- `find_geometry(kind='planar_face')` -> the flat faces of the thickened solid are present.
- the body name you report exists on the design (cross-check the name against model_inspect).

## report_truthful

Your reported body name(s) must MATCH the bodies the read finds - the result-body read-back check. A
claimed name that isn't on the model FAILS, as does calling the result a solid when the bbox shows it
isn't the intended plate.

## Diagnostics (recorded, never gating)

Expected tools: a surface-create tool (patch / extrude-as-surface) and surface_thicken - both report
their result bodies by reading them off the produced feature, so the reported names are ground truth.
The same read-back backs the offset / trim / untrim / reverse-normal surface tools and the mesh
combine/cut tools. Call count vs budget; whether you verified the result was a solid AND the right
size before reporting.
