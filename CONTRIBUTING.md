# Contributing to Fusion-Essentials

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
- `tools/` — one file per tool ("building block"). Each has a `handler(...)` (the logic; its
  parameters are the tool inputs), a `TOOL_DESCRIPTION`, a `tool = Tool.create_...`, an
  `item = Item.create_tool_item(...)`, and a `register_tool()` (or self-`register()`).
- `entry.py` — starts/stops the server, registers tools, gates `sys_execute_script`.
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
  `tools/data_model.py`) read the Data API (`app.data.dataProjects`, `rootFolder`);
  `doc_open` opens by UID (`app.data.findFileById`); `view_screenshot` captures the
  viewport (and restores the camera); `sys_execute_script` runs arbitrary Python (gated, off
  by default); `sys_reload_addin` restarts the add-in.
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
as long as the module is wired into `tools/__init__.py` (import) and `entry._collect_items()`
(register_tool call) before the reload, no manual Stop/Run is needed. A manual Stop/Run in
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
   `register_tool()` (use a plain `register(item)` at import only for always-on safe tools).
2. Import it in `tools/__init__.py`; call its `register_tool()` from `entry._collect_items()`.
3. Ground every `adsk.*` call in the Fusion API reference rather than guessing signatures —
   the Fusion API is niche and easy to get wrong. See [docs/fusion-api-notes.md](docs/fusion-api-notes.md)
   for hard-won API facts that are not obvious from the reference alone.
4. Verify against a stubbed `adsk` (see the testing note above), then `sys_reload_addin` and
   smoke-test live.

See [commands/mcpServer/README.md](commands/mcpServer/README.md) for the user-facing setup,
the full tool list, and the security model.
