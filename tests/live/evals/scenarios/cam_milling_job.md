---
id: cam_milling_job
tier: cam
fixture: |
  fresh empty design, in an environment where the Manufacture (CAM) workspace is usable and at least
  one CUTTING tool is reachable (a document / local / cloud tool library that contains actual
  cutters, not only holders). If no library with a usable cutter is reachable, report SKIP - this
  scenario cannot run without one.
budget:
  max_tool_calls: 70
  max_tokens: 160000
expected_refusals: an unrecognized toolpath-generation mode on template apply is REFUSED (not silently coerced to skip)
---

# Eval: a small milling job, addressed by name

**You are executing a live eval.** You are the ONLY agent on a single live Fusion thread - never
spawn or delegate to another agent. Use only `mcp__fusion-essentials__*` tools (deferred; load
schemas with ToolSearch). Start with `sys_capability_map` then `workspace_orient`. Work in the
ACTIVE design (entering the Manufacture workspace is expected; do not switch DOCUMENTS). Do the Task
below, then verify EACH postcondition with your OWN fresh read call and report a truthful
PASS/FAIL/SKIP verdict quoting the values you read - a claim that does not match a read is a FAIL.
Full contract: `../README.md`.

Build a body, stand up a Setup with two operations, edit and compare them, and bundle them into a
template - all BY NAME. Whether setup / operation names resolve consistently (case-insensitively)
across every CAM tool is the point.

## Task

> In the active empty Fusion design, first model a solid block 80 x 60 x 20 mm. Then set up a simple
> milling job:
> 1. Create a milling Setup on that block (call it "Setup1").
> 2. Add TWO operations to Setup1 (e.g. a facing pass and a 2D pocket or contour), each using an
>    available tool.
> 3. Change one parameter (e.g. a stepdown or feed) on one of the operations, addressing it by name.
> 4. Compare the two operations and tell me one parameter that differs.
> 5. Bundle both operations into a new library template.
>
> Report the setup name, the operation names, the parameter you changed, one difference between the
> two operations, and that the template was saved.

## Postconditions (verify each with a direct read; report the value you read)

- `cam_get(include=['setups'])` -> a setup named "Setup1".
- `cam_get(include=['operations'])` -> 2 operations under Setup1.
- the parameter you claim you changed reads back its new value on that operation.
- `cam_get(include=['templates'])` -> a template matching the one you report saving.

## report_truthful

Operation names, the changed parameter's new value, and the reported difference must all match what
the reads find. Claiming a saved template that doesn't appear in the library FAILS.

## Diagnostics (recorded, never gating)

Expected tools: a model build; cam_create_setup; cam_create_operation (x2); an operation edit and a
compare (both resolve the operation BY NAME); cam_save_template. Every CAM tool resolves a
setup / operation name the SAME way - case-INSENSITIVE exact - so "setup1" or "SETUP1" resolves
"Setup1". A tool library that holds only tool HOLDERS (no cutters) cannot back an operation; if that
is all that is reachable, report SKIP. If you apply a template, the generation mode is a fixed choice
(skip / generate) - an unrecognized value is refused, not downgraded. Call count vs budget; any
isError calls that were environment (no cutter library -> SKIP) vs. logic.
