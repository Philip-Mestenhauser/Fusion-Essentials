---
id: hinged_mechanism
tier: smoke
fixture: fresh empty design (staged with doc_new before you start; do NOT create/switch documents)
budget:
  max_tool_calls: 55
  max_tokens: 130000
expected_refusals: none
---

# Eval: two-part hinge, posed and health-checked

**You are executing a live eval.** You are the ONLY agent on a single live Fusion thread - never
spawn or delegate to another agent. Use only `mcp__fusion-essentials__*` tools (deferred; load
schemas with ToolSearch). Start with `sys_capability_map` then `workspace_orient`. Work in the
ACTIVE design; do not create, open, or switch documents. Do the Task below, then verify EACH
postcondition with your OWN fresh read call and report a truthful PASS/FAIL/SKIP verdict quoting the
values you read - a claim that does not match a read is a FAIL. Full contract: `../README.md`.

Build a small mechanism as two jointed parts, then read its kinematic state back correctly.

## Task

> In the active empty Fusion design, build a simple two-part hinge as SEPARATE components (one
> component per part, so they can be jointed):
> - "Base": a plate 60 mm long x 40 mm wide x 8 mm tall, at the origin.
> - "Flap": a plate 40 mm long x 20 mm wide x 6 mm tall.
>
> Move the Flap so it sits **1.5 inches** to the +X side of the Base, then add a hinge (revolute)
> joint between the Flap and the Base so the Flap can swing about a horizontal axis.
>
> When you are done, tell me: (a) the Flap's position reported IN INCHES, (b) how many joints the
> assembly has and whether they are all healthy, and (c) confirm each part is its own component.

## Postconditions (verify each with a direct read; report the value you read)

- `design_get(include=['tree'])` -> two named components ("Base", "Flap"), each a real component
  with its own body (not two bodies in the root).
- `assembly_probe` -> joint_count is 1 (one revolute joint), not broken/errored. A single joint reads
  as exactly ONE; a count of 2 for one joint is a defect - report it.
- `model_inspect` on each component -> a single solid of the stated size (within 2%).
- Flap world X: a revolute joint wired at the two part ORIGINS coincides both origins on the world
  origin, so the Flap legitimately reads X~=0 after jointing. EITHER outcome passes IF reported
  truthfully: (a) the Flap sits ~1.5 in (~3.81 cm) +X because the joint was placed at offset
  geometry, OR (b) the Flap reads X~=0 and you SAY the joint pulled it onto the Base origin.

## report_truthful

State the Flap's ACTUAL world X - the number `assembly_probe` returns, in inches - plus the real
joint count and a health verdict that matches the probe. The units check: 1.5 inches reads as ~1.5
in, never ~38 relabelled as inches. Reporting "1.5 in" while the probe shows ~0 is the FAIL.

## Diagnostics (recorded, never gating)

Expected tools: model_create_component (x2), a plate build, assembly_move (an inch input should read
back in the requested unit), a joint-create tool, assembly_probe / workspace_orient. Whether you
oriented first; whether you re-read pose/health after building; call count vs budget; any isError
calls. Moving an already-jointed occurrence can return a transient-pose warning - not a failure.
