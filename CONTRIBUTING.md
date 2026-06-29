# Contributing to Fusion-Essentials

This guide is for **developers** extending the add-in (mainly its MCP server). It opens with the
*concepts* — what the construct is and the ideas that make it work — then the *operational* how-to
(layout, hard rules, the dev loop, adding a tool). For **users**, see the [README](README.md) and the
[MCP Server README](commands/mcpServer/README.md). The code is the authoritative description of
behavior; this doc explains the *why* and points at where each idea lives, rather than transcribing it.

---

## Understand the construct (the concepts)

**What MCP is here.** The add-in hosts a local [Model Context Protocol](https://modelcontextprotocol.io)
server inside Fusion. An AI agent connects over loopback and receives a list of *tools* — each a
`{name, description, inputSchema}`. The agent never sees the implementation; **the schema and
description ARE the API contract.** It picks a tool, calls it, and the server runs the tool's handler on
Fusion's main thread and returns a JSON result. That's the whole loop: discovery is the schema,
execution is a marshalled handler. (Launch/registration lives in `commands/mcpServer/entry.py`; the
primitives in `commands/mcpServer/mcp_primitives/`.)

A handful of ideas give the tool set its character. Each is a convention you should follow when adding
a tool — they are what make an agent able to drive Fusion *deterministically*, with few calls and few
blind spots.

- **Tools are building blocks; skills compose them.** A tool is one verb (`model_extrude`,
  `assembly_probe`). A *skill* (`.claude/skills/`) is a markdown procedure that chains tools into a
  repeatable workflow — the sentence built from the verbs. Reliability comes from each step being a
  tested tool with its own guards, not from a brittle macro. If a workflow needs a capability no tool
  provides, that is the signal to **add a tool**, not to hand-roll `sys_execute_script` inside a skill.

- **Progressive disclosure — orient broadly, then drill cheaply.** An agent arrives at a document blind,
  and reading an entire large design does not scale. So the posture is: one cheap broad read first
  (`workspace_orient` — what's open, its health, whether CAM exists, the major pieces, and *pointers* to
  the right narrow tool), then scoped refinement on demand (`design_get_tree(component=…)`,
  `find_geometry(target=…)`, `assembly_probe`). A new read tool should fit this shape: cheap and broad,
  or scoped and deep — and say which.

- **Geometry-as-values.** An agent has no eyes, so selecting geometry by a magic snap-string is
  ambiguous the moment a part has two cylinders. Instead, `find_geometry` returns each face/edge/vertex
  as a stable `entityToken` *handle* (filterable by radius/proximity — numbers, not pixels), and
  consumers take that handle via the `GeometryHandle` input kind. Geometry becomes a first-class **value
  that flows between calls** rather than tribal knowledge re-derived each time. `assembly_probe` is the
  same idea for kinematic state (positions/grounding/joints as JSON, not a cluttered render).

- **Return the IDs the next call needs — unprompted.** A tool that creates or identifies something
  should put its stable id in the result even when not asked: `find_geometry` returns a handle,
  `doc_get_active_id` a data-model URN, `assembly_probe` exact occurrence names, `model_measure_bbox`
  measured extents. This is what makes a chain deterministic — the next target is an id the previous step
  *minted*, not a name the agent hopes resolves.

- **The code guides correct use; the description carries the contract.** Because the agent only sees the
  schema, dependencies and failure modes must be legible up front. Two mechanisms in
  `commands/mcpServer/tools/_inputs.py` do this in code rather than prose: a typed **input kind**
  (`GeometryHandle`/`BodyRef`/`PlaneRef`/`AxisRef`/`Choice`/…) bundles schema + resolution + validation +
  an auto-generated contract line, so an input that needs a face can *only* take a handle, never a bare
  coordinate; and a **`ModeGuard`** derives its error from the requirement, so a precondition rejection
  can't point the wrong way. Prefer these over hand-rolling a `name: str` — that is how blind spots get
  reintroduced. The **producer-side mirror** is `tools/_outputs.py`: a tool declares `RETURNS = [...]`
  of typed **output kinds** (`ReturnsHandle`/`ReturnsUrn`/`ReturnsName`/`ReturnsValue`), which generate the
  description's `PRODUCES:` line AND back a test that asserts the handler actually mints the declared key —
  so a renamed id field fails CI instead of silently lying to every consumer that reads it. A mutation
  should never hide behind `safe()` (that turns a swallowed failure into a false success); read tools probe
  with `safe`, write tools let failures raise and report honestly.

- **The live add-in is a development surface.** `sys_reload_addin` hot-reloads without restarting Fusion,
  and `sys_get_api_doc` + `sys_execute_script` let you prototype an `adsk.*` call against the live API
  before committing it as a tool. A team can keep their own tools and skills on a fork — version
  controlled together, hot-reloadable, gated per-tool where the blast radius warrants.

**The layering** (innermost first): the response/value substrate (`tools/_common.py` — the `ok`/`error`
contract, `safe`, the design/component/sketch resolvers, unit scaling); the typed input/output kinds
(`tools/_inputs.py` + `tools/_outputs.py`); the MCP primitives (`mcp_primitives/` — the `Tool` builder, the `Item` that binds
a primitive to its handler + execution metadata, the registry); and the tool module itself
(`tools/<domain_verb>.py`), which holds only domain logic. Read any one tool file (e.g.
`tools/workspace_orient.py` or `tools/find_geometry.py`) to see the whole pattern in one place.

> Most of these conventions are now *enforced* rather than merely encouraged: write-status is a structured
> annotation (linted), enum inputs are typed `Choice`/`UnitField` kinds, occurrence/geometry references are
> typed kinds that refuse ambiguity, and outputs are declared via `_outputs.py` with a CI assertion that
> the ids are actually minted.

---

## This is a Fusion add-in (no standalone "run" / test harness)

- The code runs **inside Fusion's embedded Python**, not a standalone interpreter, so
  `import adsk` only works from within Fusion. To static-check or unit-test logic outside
  Fusion, stub `adsk.*` and the repo packages (`config`, `lib.fusion360utils`,
  `shared_state`) in `sys.modules` — build a faithful temp-package tree under a fake add-in
  root so imports resolve.
- For syntax/AST checks, use Fusion's bundled Python:
  `C:\Users\<user>\AppData\Local\Autodesk\webdeploy\production\<hash>\Python\python.exe`
  (the path varies by Fusion build).
- The MCP server work (`commands/mcpServer/`) is intended to merge upstream into
  `Kings-Mountain-Labs/Fusion-Essentials`. Keep it modular under `commands/mcpServer/`,
  dual-license new files (MIT/Apache headers), follow the existing conventions below, and
  keep the security posture defensible.

## Add-in command convention (how features are structured)

- Each feature is a `commands/<name>/` package with an `entry.py` exposing module-level
  `CMD_ID`, `CMD_NAME`, and `start()` / `stop()`. The `__init__.py` files are empty; the
  real code lives in `entry.py`.
- Register a feature by adding it to the `commands` list in `commands/__init__.py`.
- IDs follow `f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_...'` (COMPANY_NAME = `GTF`).
- Settings: a module calls `shared_state.load_settings_init(GROUP_ID, name, DEFAULTS, icon)`
  to get its own Settings tab, and `shared_state.load_settings(GROUP_ID)` to read it back.
  The settings UI renders **every key in a group as a checkbox/dropdown** — there is no
  hidden-field concept, so do not store internal/bookkeeping state in a settings group.
- Enablement and settings changes take effect on **reload**, not live (the live-toggle code
  in `commands/settings/entry.py` is commented out upstream).

## MCP server (`commands/mcpServer/`)

### Layout

- `server/mcp_server.py` — HTTP + JSON-RPC server, **Streamable HTTP** transport (2025-03-26).
- `server/task_manager.py` — marshals work onto Fusion's **main thread** via a custom event.
- `mcp_primitives/` — Tool / Resource / Item schema classes plus the registry.
- `tools/` — one file per tool ("building block"), named `<family>_<verb>.py`. Each has a
  `handler(...)` (the logic; its parameters are the tool inputs), a `TOOL_DESCRIPTION`, a
  `tool = Tool.create_...`, an `item = Item.create_tool_item(...)`, and a `register_tool()`.
  Modules are **auto-discovered** by a `pkgutil` sweep — drop the file in, no registry edits.
  `_`-prefixed modules (`_common`, `_inputs`, `_outputs`, `_data_common`) are shared helpers.
- `entry.py` — starts/stops the server, runs the tool-discovery sweep, gates `sys_execute_script`.
- `README.md` (in that folder) — the **user-facing** doc (setup, security, platform).

### Hard rules (violating these causes crashes or hangs)

- **Anything touching `adsk.*` must run on Fusion's main thread.** Tools do this by setting
  `run_on_main_thread=True` on their `Item` (the default); the server marshals via
  TaskManager. Calling the Fusion API from a request/worker thread can crash Fusion.
- **Never block the main thread.** No `time.sleep`/polling loops and no synchronous HTTP in
  `entry.start()` or in a tool handler — they run on the UI thread. (The port self-check runs
  on a background thread for exactly this reason; `doc_open` deliberately does not poll.)

### Behavior that isn't obvious from the code

- The server binds **`127.0.0.1:27182`**, path **`/mcp`** — Fusion's own well-known MCP port.
  Whoever binds first wins; if Fusion's built-in MCP server holds it, the add-in detects this
  and warns the user.
- `sys_get_session` returns the live document; `data_list_projects` / `data_list_files` (in
  `tools/data_read.py`, with shared helpers in `tools/_data_common.py`) read the Data API
  (`app.data.dataProjects`, `rootFolder`); `doc_open` opens by UID (`app.data.findFileById`);
  `view_screenshot` captures the viewport (and restores the camera); `sys_execute_script` runs
  arbitrary Python (gated, off by default); `sys_reload_addin` restarts the add-in.
- `doc_open` is **async** — `documents.open()` returns before the document is active.
- `sys_execute_script` uses Fusion's `Python.Run` text command. It is **Windows-tested only**;
  the temp-path handling normalizes `\`→`/` for cross-platform use but is **unverified on
  macOS**.
- **Never compare Fusion API objects with `is`.** The API returns fresh wrapper objects for the
  same underlying entity, so `occurrence.component is someComponent` silently fails. Match by
  `.name` (or compare entity tokens). This bit the `joint_create` resolver (live-verified fix).
- **A Joint Origin inside a referenced/child occurrence must be joined via its assembly-context
  proxy**, not the native JO: `jo.createForAssemblyContext(occurrence)`. Passing the native JO
  yields "Provided input paths for joint are not valid". The `joint_create` tool resolves this
  automatically (root JOs are used as-is; sub-component JOs are proxied through the occurrence
  that instances them, matched by component name).

### The development loop (iterating without manual Fusion steps)

The `sys_reload_addin` tool restarts the add-in so a connected agent can pick up code edits.
Workflow: edit a `tools/*.py` file → call `sys_reload_addin` (deferred: it responds, then the
server restarts in ~0.5s) → poll `GET http://127.0.0.1:27182/health` until it is back →
`tools/list` to confirm. `sys_reload_addin` picks up **brand-new** tool modules too (verified) —
the `pkgutil` sweep discovers any `tools/*.py` that exposes `register_tool()` on reload, so just
having dropped the file in is enough; no manual Stop/Run is needed. A manual Stop/Run in
Fusion's Add-Ins dialog is only required if the add-in failed to start (so no server is
running to call `sys_reload_addin` against). Note: an MCP **client** may cache the tool list, so
a newly registered tool can be invisible to the client until it reconnects — reconnect the
server in your client (`/mcp` in Claude Code) to refresh, or drive it over raw HTTP meanwhile.

### Driving the server from outside Fusion (for testing)

POST JSON-RPC to `http://127.0.0.1:27182/mcp` with header
`Accept: application/json, text/event-stream`. Example tool call:
`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"data_list_projects","arguments":{}}}`.
Diagnostics: `GET /health`, `GET /tools`.

### Adding a new tool (the pattern)

1. Create `tools/<name>.py` with `handler(...)`, `TOOL_DESCRIPTION`, `tool`, `item`, and
   `register_tool()`. **That's all the wiring there is** — registration is auto-discovered (a
   `pkgutil` sweep of `tools/` calls each module's `register_tool()`), so you do **not** edit
   `tools/__init__.py` or `entry.py`. `_`-prefixed modules are treated as shared helpers and
   skipped. (`test_tool_autodiscovery.py` enforces this contract.)
2. Resolve any input that refers to existing geometry/occurrences/bodies through a typed kind in
   `tools/_inputs.py` (`GeometryHandle`/`BodyRef`/`OccurrenceRef`/`PlaneRef`/`AxisRef`/`Choice`/…)
   rather than a hand-rolled `name: str` — the kinds refuse ambiguity and self-heal stale handles.
3. Ground every `adsk.*` call in the Fusion API reference rather than guessing signatures —
   the Fusion API is niche and easy to get wrong. See [docs/fusion-api-notes.md](docs/fusion-api-notes.md)
   for hard-won API facts that are not obvious from the reference alone.
4. Write its test (see **Testing a tool** below), then `sys_reload_addin` and smoke-test live.

### Testing a tool

Each tool has a `tests/test_<tool>.py` beside the suite (one test file per tool). The tools run in a
live Fusion session, so the suite **mocks `adsk`** to exercise pure handler logic outside Fusion — and
because mocks can't catch a wrong `adsk.*` signature, geometry-touching tools are also **live-validated**
(`sys_reload_addin`, then call the handler on a real document).

- **Harness.** `tests/conftest.py` injects a lightweight mock `adsk` into `sys.modules` before any tool
  is imported, then `load_tool("<tool>")` spec-loads that one module in isolation and returns it, so a
  test can call `module.handler(...)` and its private helpers directly. The mocks implement only what a
  tool touches — extend them per tool as you go.
- **The fake pattern.** A test builds small `Fake*` objects that model just the read/write surface the
  handler uses (e.g. a `FakeDesign` with the `rootComponent`/`timeline` the handler reads), installs them
  on the module's `app`, and points `adsk.fusion.Design.cast` at them. Assert on the JSON payload the
  handler returns (decode `result["content"][0]["text"]`) and on the exact `adsk.*` calls the fake
  captured (so a regression to a wrong method name / argument fails here).
- **Cover the guards too** — unknown-units / no-active-design / out-of-range inputs, not just the happy
  path. The error contract (`isError`, `message`) is part of the tool's behavior.
- **Run it:** `py -3 -m pytest tests/test_<tool>.py -q` (or the whole suite with `py -3 -m pytest -q`).
- **Regenerate the spec:** `py -3 tests/gen_spec.py` rebuilds `tests/SPEC.md` from the test names (each
  `test_<behavior>` is one documented contract); run it after adding/renaming tests. `--check` fails if
  `SPEC.md` is stale.

See [commands/mcpServer/README.md](commands/mcpServer/README.md) for the user-facing setup,
the full tool list, and the security model.
