# Tests

Unit tests for the MCP tools in `commands/mcpServer/tools/`. They run **outside
Fusion** against a mocked `adsk` layer, so they're fast (~0.1s for the whole
suite) and need no live Fusion session.

```bash
py -3 -m pytest            # run everything
py -3 -m pytest tests/test_sys_selection.py -v   # one tool, verbose
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
to/from the Fusion API (`view_screenshot`, `sys_reload_addin`,
`sys_execute_script`, `workspaces`, …). Mocking `adsk` just to assert "it copied
`app.version` into a field" tests the mock, not the code. Those belong to the
in-Fusion integration layer (driven via the Fusion MCP server), not here.

### Triage when deciding whether a tool needs tests

- **Tier 1 — test thoroughly.** Real logic: unit math, parsing, classification,
  path/name resolution, state tallies. (model_inspect, sys_selection,
  cam_read, data_ops/doc_lifecycle, param_ops, design_configure,
  joint_create_edit, joint_create_origin, cam_templates, sketch_core.)
- **Tier 2 — test the one or two real helpers.** Mostly Fusion orchestration
  with a pure helper or two worth pinning. (doc_open URN parsing, quoting
  helpers, design_get tree/timeline slices,
  doc_update_xref/cam_generate helpers.)
- **Tier 3 — skip.** Fusion pass-throughs with no pure logic. Skipping is
  correct, not lazy.

## How the harness works (`conftest.py`)

Two problems make these tools awkward to import in a test, both solved in
`conftest.py`:

1. **Module-top `adsk` access.** Each tool does `app =
   adsk.core.Application.get()` at import time. So `install_mock_adsk()` injects
   mock `adsk` / `adsk.core` / `adsk.fusion` / `adsk.cam` into `sys.modules`
   **before** any tool is imported (it's called at collection time).
2. **Importing the package pulls in Fusion-dependent code.** `entry.py`
   auto-discovers and imports every tool (most need Fusion) and
   `commands/__init__.py` builds UI panels. `load_tool("model_inspect")`
   sidesteps both: it puts `commands/` on the path, imports only the cheap
   adsk-free packages, stubs `mcpServer.tools`, then spec-loads the single
   requested module so its `from ..mcp_primitives ...` relative imports resolve.

```python
from conftest import load_tool
mi = load_tool("model_inspect")   # at module level
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

### Fakes — extend the shared ones; don't fork a bespoke hierarchy

`conftest.py` ships the shared fakes: `make_design` / `MakeComp` / `MakeDesign`
build a design, and `install(mod, design)` wires it into a tool. Smaller classes
named to match Fusion's runtime type names (tools branch on `type(x).__name__`):
`BRepFace`, `BRepEdge`, `Plane`, `Cylinder`, `Line3D`, `Circle3D`, `BRepBody`,
`Component`, `FakeVector3D`, `FakePoint`. They implement only the interface a tool
actually reads.

**When `MakeComp`/`MakeDesign` lack a surface your tool needs, extend the shared
fake — do not fork a bespoke `Fake*` hierarchy into your test file.** Most of the
older tests roll their own `Fake*` classes plus an imperative `_install()` that
pokes module-level seams (`mod.app = ...`); that pattern leaks state into the next
test and is the reason `conftest.py` carries a large snapshot/restore fixture to
compensate. It's the anti-pattern, not the model. (Same "extend the kind, don't
copy it" rule the tools themselves follow.)

## Adding tests for a new tool

### The canonical examples — copy one of these

Pick the closest and copy its shape. Each is kept clean on purpose; a new test
should be indistinguishable in structure from its model.

- **A rich read (`<domain>_get`)** → copy **`test_design_get.py`**. A
  `@pytest.fixture` stubs the `_slice_*` seams with `monkeypatch`; tests assert the
  router's composition (default = orientation slice only; each `include=` adds its
  slice; the note advertises the rest). `test_cam_get.py` is the same shape.
- **A logic tool (geometry / resolution / state)** → copy **`test_model_inspect.py`**.
  It uses the shared `make_design`/`MakeComp` fakes via `install`, all set up with
  `monkeypatch` — no local `Fake*` classes, no imperative seam-poking.
- **A pure function (parse / encode / convert)** → copy **`test_quoting.py`**.
  No Fusion at all; just call it and round-trip the result.

Then:

1. Read the tool. List its `_helper` functions and the `handler`. Find the pure
   logic: unit math, parsing, the 0/1/N branches, validation gates, the
   `_ok`/`_error` shape.
2. `tool = load_tool("<module_name>")` at the top of `tests/test_<tool>.py`.
3. Write **one test per specific, plausible bug**, not one per function. The
   name should read like a spec line (`test_picks_largest_body_by_volume`), it
   ends up in `SPEC.md`.
4. Assert on concrete values. Cover sizes **0, 1, 2, N** for anything taking a
   collection. Round-trip any encode/decode pair (see `test_quoting.py`).
5. **Set up state with `monkeypatch` or a `@pytest.fixture`, never an imperative
   `mod.app = …` poke.** Both undo themselves after the test, so no state leaks
   into the next one. Use the shared `make_design`/`install` fakes; if they lack a
   surface, **extend them in `conftest.py`** rather than forking a local `Fake*`
   hierarchy (see "Fakes" above).
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
