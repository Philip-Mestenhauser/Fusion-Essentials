# Tests

Unit tests for the MCP tools in `commands/mcpServer/tools/`. They run **outside
Fusion** against a mocked `adsk` layer, so they're fast (~0.1s for the whole
suite) and need no live Fusion session.

```bash
py -3 -m pytest            # run everything
py -3 -m pytest tests/test_selection.py -v   # one tool, verbose
py -3 tests/gen_spec.py    # regenerate SPEC.md (the behavior spec)
```

> Requires `pytest` (`py -3 -m pip install pytest`). Config lives in
> `pytest.ini` at the repo root — it sets `testpaths`/`pythonpath` so no env
> vars are needed.

## What these test (and what they don't)

The tools are written for a *live* Fusion session, but most of their **bug
surface is pure logic** that runs before/around the Fusion calls: unit
conversions, string/path/URN parsing, the 0/1/N-item branches, entity
classification, and the `_ok`/`_error` result contract every tool returns. That
logic breaks **silently** — a wrong unit factor or a dropped error path returns
subtly wrong JSON to the agent, with no exception. That's what we pin down.

We deliberately **do not** unit-test tools whose only job is to forward data
to/from the Fusion API (`get_session_info`, `get_screenshot`, `reload_addin`,
`execute_api_script`, `workspaces`, …). Mocking `adsk` just to assert "it copied
`app.version` into a field" tests the mock, not the code. Those belong to the
in-Fusion integration layer (driven via the Fusion MCP server), not here.

### Triage when deciding whether a tool needs tests

- **Tier 1 — test thoroughly.** Real logic: unit math, parsing, classification,
  path/name resolution, state tallies. (measure_bounding_box, selection,
  cam_info, data_management, parameters, configurations, joint, joint_origin,
  cam_templates, sketches.)
- **Tier 2 — test the one or two real helpers.** Mostly Fusion orchestration
  with a pure helper or two worth pinning. (open_document URN parsing, quoting
  helpers, component_tree/visibility lookups, timeline/update_xref/
  generate_toolpaths helpers.)
- **Tier 3 — skip.** Fusion pass-throughs with no pure logic. Skipping is
  correct, not lazy.

## How the harness works (`conftest.py`)

Two problems make these tools awkward to import in a test, both solved in
`conftest.py`:

1. **Module-top `adsk` access.** Each tool does `app =
   adsk.core.Application.get()` at import time. So `install_mock_adsk()` injects
   mock `adsk` / `adsk.core` / `adsk.fusion` / `adsk.cam` into `sys.modules`
   **before** any tool is imported (it's called at collection time).
2. **Importing one tool pulls in all ~30.** `tools/__init__.py` imports every
   tool (most need Fusion) and `commands/__init__.py` builds UI panels.
   `load_tool("measure_bounding_box")` sidesteps both: it puts `commands/` on the
   path, imports only the cheap adsk-free packages, stubs `mcpServer.tools`, then
   spec-loads the single requested module so its `from ..mcp_primitives ...`
   relative imports still resolve.

```python
from conftest import load_tool
mbb = load_tool("measure_bounding_box")   # at module level
```

### The one rule the mocks impose: assert on concrete values

`adsk.core` / `adsk.fusion` / `adsk.cam` are `unittest.mock.Mock` objects.
Unmodeled attribute access returns a **truthy child Mock**, not `None` and not
an error. So:

- `assert result is not None` is almost always true and proves nothing.
- `assert payload["x"] == 50.0` catches a real bug.

A few `app.*` reads are pre-seeded with real values (`app.activeDocument.name =
"TestDoc"`, `app.version`) and a few `.cast` methods are pass-throughs
(`Design.cast`, `Operation.cast`) so that tools which filter on a cast result
behave correctly. If a tool reads some `adsk` attribute that returns a stray
Mock and pollutes a JSON payload, fix it **in the harness** (model that
attribute) rather than in each test.

### Fakes

Small classes named to match Fusion's runtime type names (because tools branch
on `type(x).__name__`): `BRepFace`, `BRepEdge`, `Plane`, `Cylinder`, `Line3D`,
`Circle3D`, `BRepBody`, `Component`, `FakeVector3D`, `FakePoint`, etc. They
implement only the interface a tool actually reads. **Extend them as needed** —
that's the intended workflow, the same "grow the mock as you go" model the
bootstrap kit's `mock_adsk.py` uses, adapted to this project's layout.

## Adding tests for a new tool

1. Read the tool. List its `_helper` functions and the `handler`. Find the pure
   logic: unit math, parsing, the 0/1/N branches, validation gates, the
   `_ok`/`_error` shape.
2. `tool = load_tool("<module_name>")` at the top of `tests/test_<tool>.py`.
3. Write **one test per specific, plausible bug**, not one per function. The
   name should read like a spec line (`test_picks_largest_body_by_volume`), it
   ends up in `SPEC.md`.
4. Assert on concrete values. Cover sizes **0, 1, 2, N** for anything taking a
   collection. Round-trip any encode/decode pair (see `test_quoting.py`).
5. Add fakes to `conftest.py` only if more than one test file needs them;
   otherwise keep them local to the test file.
6. If a tool needs an `adsk` attribute that isn't modelled, add it to
   `install_mock_adsk()` (or a `.cast` pass-through) — once, in the harness.

### Prove a test actually bites

A test that can't fail is decoration. After writing one, sanity-check it by
temporarily breaking the code it covers (flip a comparison, change a constant)
and confirming the right test goes red — then restore. Examples done this way:
the "largest body by volume" tiebreak and the `_scale` mm factor.

> Note: the harness sets `sys.dont_write_bytecode = True`. The tool loader
> spec-loads source files, and mtime-keyed `.pyc` caches can otherwise go stale
> when you edit-then-restore a tool quickly during a regression check, masking
> the restored source.

## Updating tests as behavior changes

**The test changes in the same commit as the behavior it describes.** A red test
after a code change is the suite telling you a promise changed — confirm you
meant it, then update the test. Never edit a test purely to make it pass without
understanding why it broke (refactor that changed behavior → fix the code; test
asserting an implementation detail → fix the test).

## The behavior spec (`SPEC.md`)

`tests/SPEC.md` is **generated** from the test names by `gen_spec.py` — a
per-tool checklist of every behavior currently pinned by a test. Use it to
review scope ("what do my tools actually guarantee?") and to spot gaps. Don't
edit it by hand; regenerate after changing tests:

```bash
py -3 tests/gen_spec.py           # rewrite SPEC.md
py -3 tests/gen_spec.py --check   # exit 1 if stale (for CI)
```
