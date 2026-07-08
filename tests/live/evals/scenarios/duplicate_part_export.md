---
id: duplicate_part_export
tier: smoke
fixture: fresh empty design (staged with doc_new before you start; do NOT create/switch documents)
budget:
  max_tool_calls: 45
  max_tokens: 110000
expected_refusals: a bare name shared by both instances SHOULD draw an ambiguity refusal naming the two candidate paths
---

# Eval: export the correct one of two identical instances

**You are executing a live eval.** You are the ONLY agent on a single live Fusion thread - never
spawn or delegate to another agent. Use only `mcp__fusion-essentials__*` tools (deferred; load
schemas with ToolSearch). Start with `sys_capability_map` then `workspace_orient`. Work in the
ACTIVE design; do not create, open, or switch documents. Do the Task below, then verify EACH
postcondition with your OWN fresh read call and report a truthful PASS/FAIL/SKIP verdict quoting the
values you read - a claim that does not match a read is a FAIL. Full contract: `../README.md`.

Address a PRECISE instance among identical copies (full path or handle), not whichever comes first,
and read one component's structure back. Replace `<OUTPUT_DIR>` in the Task with the writable export
directory the runner gives you.

## Task

> In the active empty Fusion design, make one component called "Bracket" (a 30 x 30 x 10 mm plate
> with an 8 mm hole through the middle). Then make a SECOND INSTANCE of that SAME component (copy /
> paste or a pattern - so the tree shows Bracket:1 and Bracket:2, two occurrences of one component,
> NOT a second separate component), placed beside the first.
>
> Export the SECOND Bracket instance to a STEP file at `<OUTPUT_DIR>/bracket_2.step`. Then show me a
> tree view scoped to the Bracket component. Tell me exactly which instance you exported and how you
> made sure it was that one and not the other.

## Postconditions (verify each with a direct read; report the value you read)

- `design_get(include=['tree'])` -> two instances of ONE component: Bracket:1 and Bracket:2. If
  instead there are two DISTINCT components (e.g. "Bracket" and "Bracket (1)"), the run built the
  wrong structure and the precise-instance test did not fire - report that as a FAIL of intent.
- exactly ONE STEP file at the path, non-empty (file_exists, size_bytes > 0).
- you addressed a SPECIFIC instance - a fullPathName like Bracket:2, or a find_geometry body handle -
  not a bare name you could not have disambiguated.
- `design_get(include=['tree'], component='Bracket')` returns the Bracket subtree (not an error, not
  the whole design).

## report_truthful

Name which instance you exported and the basis for picking it (full path / handle), and that exactly
one file landed. "Exported the second bracket" with no evidence you distinguished the two - or with
zero / two files on disk - FAILS.

## Diagnostics (recorded, never gating)

Expected tools: model_create_component + model_hole + a copy/pattern for the second instance;
design_export (resolves an occurrence by fullPathName, a component by name, or a body by
find_geometry handle - a NAME shared by several instances is refused with the candidate list, not
first-matched); design_get(include=['tree'], component=...). If you try a bare ambiguous name and get
an ambiguity refusal naming both candidate paths, that refusal is the DESIRED behavior - record it,
it is not a wasted call.
