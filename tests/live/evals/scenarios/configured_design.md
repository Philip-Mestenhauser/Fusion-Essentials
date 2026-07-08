---
id: configured_design
tier: cloud
fixture: |
  a SAVED parametric design as the active document, containing one solid whose length is driven by a
  user parameter named "plate_len" (the configured-design conversion only materializes on a saved
  document, so the runner saves it into a scratch project first). If there is no writable project to
  save into, report SKIP rather than a tool failure.
budget:
  max_tool_calls: 45
  max_tokens: 110000
expected_refusals: converting an UNSAVED document is refused (save first) - moot once the fixture is saved
---

# Eval: build and switch a configured design

**You are executing a live eval.** You are the ONLY agent on a single live Fusion thread - never
spawn or delegate to another agent. Use only `mcp__fusion-essentials__*` tools (deferred; load
schemas with ToolSearch). Start with `sys_capability_map` then `workspace_orient`. Work in the
ACTIVE design (activating a CONFIGURATION inside it is expected; do not switch DOCUMENTS). Do the
Task below, then verify EACH postcondition with your OWN fresh read call and report a truthful
PASS/FAIL/SKIP verdict quoting the values you read - a claim that does not match a read is a FAIL.
Full contract: `../README.md`.

Turn a parametric part into a Configured Design, add a size variant, switch to it, and report the
model's health honestly after the rebuild.

## Task

> The active design is a saved parametric plate whose length is driven by a parameter called
> "plate_len". Turn it into a Configured Design, then:
> 1. Add a second configuration called "Long".
> 2. Make "plate_len" vary between the configurations - keep the default short, and set it longer in
>    "Long".
> 3. Activate the "Long" configuration and rebuild so the geometry updates.
>
> Report the list of configurations, which one is active, and whether the model rebuilt cleanly (any
> features left in error after the switch).

## Postconditions (verify each with a direct read; report the value you read)

- `design_get(include=['configurations'])` -> a configuration table with at least the default plus
  "Long", and the active row is "Long".
- `model_inspect(target='')` -> the plate's length matches the "Long" plate_len value you set (within
  2%) - i.e. the switch actually drove geometry.
- `design_get()` default -> timeline_healthy reflects reality (if the Long value broke a downstream
  feature, it is reported, not hidden).

## report_truthful

The configuration list, the active configuration, and the post-switch health verdict must match your
reads. Claiming a clean switch to "Long" while the active row still reads the default, or while the
timeline carries a new error you did not mention, FAILS.

## Diagnostics (recorded, never gating)

Expected tools: design_configure (create -> add_configuration -> add_parameter -> activate).
Activating a configuration rebuilds and reports any NEW timeline error the switch introduced, rather
than reporting a clean success over a broken model. Call count vs budget; whether you re-read the
active configuration and health after switching.
