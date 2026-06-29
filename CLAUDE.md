# Working in Fusion-Essentials (MCP server)

Agent guide for extending the MCP server under `commands/mcpServer/`. Read
[CONTRIBUTING.md](CONTRIBUTING.md) for the full architecture; this file is the short list of
expectations that are easy to violate. The code is the source of truth — match the nearest existing
tool when in doubt.

## Adding a tool

- One tool = one verb in `tools/<name>.py` exposing `handler(...)`, a description, a `Tool`, an
  `Item`, and `register_tool()`. Registration is auto-discovered (a pkgutil sweep) — **just drop the
  file in**; do not edit `tools/__init__.py` or `entry.py`. `_`-prefixed modules are shared helpers,
  never tools.
- Set `write=` on the `Item` honestly: `"read"`, `"write"`, or `"destructive"`. It is linted.
- Run on the main thread (the default) — anything touching `adsk.*` off the main thread can crash
  Fusion. Never block: no `sleep`, polling, or synchronous network in a handler.

## Ground every `adsk.*` call in the real API

The Fusion API is niche and easy to get wrong, and a mocked test cannot catch a wrong signature. So:

- Confirm signatures/properties against the live API (`sys_get_api_doc`) **before** writing them. Do
  not guess a property exists — verify it. (Real example: deleting an occurrence is
  `Occurrence.deleteMe()`; there is no "is this a pattern child" property — detect that case from the
  `deleteMe()==False` result instead.)
- Record non-obvious API facts in [docs/fusion-api-notes.md](docs/fusion-api-notes.md), not in a tool
  description.

## Honesty contract (the rule that matters most)

- Use `ok(...)` / `error(...)` from `_common`. Wrap per-field READS in `safe(getter, default)` so one
  bad field doesn't sink the call — but let an actual MUTATION raise. A swallowed mutation that reports
  success is the cardinal sin: a failed delete/edit must return `isError`, never a false `ok`.
- After a write, verify the effect and report it. If the API returns success but nothing changed
  (it happens), treat that as failure. Surface partial success explicitly (what was done, what wasn't).
- Guard inputs and report *why* a precondition failed, naming the offending value. Resolve references
  (occurrences, geometry) through the typed kinds in `_inputs.py` — they refuse ambiguity instead of
  grabbing the wrong instance; don't hand-roll a `name: str`.

## Token economy (descriptions & notes are loaded every turn)

- A tool's **description** and the **`note`/`pointers`** it returns are sent to the calling agent's
  context. Keep them tight: state what the tool does, its inputs, and the one non-obvious gotcha.
  Do NOT enumerate the return schema (the agent sees the JSON), restate rationale, or write a tutorial
  with `CONTRACT:` / `OPEN QUESTIONS:` sections.
- Put the *why*, design notes, and war stories in **docstrings and `#` comments** — those never reach
  the agent's context, so be as thorough as you like there.

## Tests (write them with the tool, before moving on)

- One `tests/test_<tool>.py` beside the suite. `conftest.load_tool("<tool>")` loads a module in
  isolation against a mocked `adsk`. Build small `Fake*` objects modelling only the read/write surface
  the handler touches; assert on the returned JSON and on the exact `adsk.*` calls the fake captured.
- Cover the guards (bad units, no active design, missing/ambiguous target), not just the happy path.
- A handler that reads `type(x).__name__` needs a fake whose class is literally named that — build it
  with `type("RealClassName", (), {...})()`, not a `FakeFoo` class.
- A tool resolving occurrences/geometry via `_inputs` reads `_inputs._common.design()`. Patch BOTH
  that seam and the tool's own `_common.design()` to the same fake design.
- `py -3 -m pytest -q` must stay green; run `py -3 tests/gen_spec.py` after adding/renaming tests.

## Verify on a real document before claiming done

Mocks prove logic, not API correctness. After tests pass: `sys_reload_addin` (auto-discovers new
modules), wait for `127.0.0.1:27182/health`, then exercise the handler on a live document. The MCP
client caches the tool list/schema until it reconnects — a changed schema may not show client-side
until then, though the server handler is live.

## Destructive / outward actions

Deleting or overwriting is hard to reverse. Guard it, and when testing live against a model you didn't
create, prefer a throwaway you made (a scratch component/sketch) over the user's real geometry — or
ask first.
