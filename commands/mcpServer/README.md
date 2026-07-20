# Fusion-Essentials MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that
lets an AI agent (Claude, or any MCP client) interact with your live Fusion session —
read what's in your projects, open files, screenshot the viewport, and (optionally)
run Fusion API scripts.

It is **off by default** and runs only on your own machine (loopback).

## Enabling it

1. Open **Utilities → Add-Ins**, run Fusion-Essentials.
2. Open **Fusion-Essentials Settings**, and on the feature-enablement tab tick
   **Enable MCP Server**.
3. Reload Fusion-Essentials (Add-Ins dialog → Stop, then Run). The setting takes
   effect on reload.
4. The server starts on `http://127.0.0.1:27182/mcp`. Confirm with a browser:
   `http://127.0.0.1:27182/health` should return `{"status": "healthy", ...}`.

Optional tool **families** (`appearance`, `cam`, `data`, `drawing`, `mesh`, `save`, `surface`) can each
be disabled with a checkbox under **Settings → MCP Server**, to shrink the tool surface an agent has
to load when you don't need that domain. All families are enabled by default. Like the other MCP
settings, a disabled family takes effect on reload — its tools are not registered, and
`workspace_orient` / `sys_capability_map` / `sys_find_tool` then reflect only what's enabled.

### Port note

`27182` is Fusion's own well-known MCP port. The Fusion-Essentials server and
Fusion's **built-in** MCP server cannot both use it at once — whichever starts first
wins. If Fusion's built-in server is on and holds the port, Fusion-Essentials detects
this and shows a dialog asking you to turn off **Preferences → Fusion MCP Server** and
reload. You do not need to configure Fusion's built-in server; just leave it off.

## Connecting a client

The server speaks the **Streamable HTTP** MCP transport, so clients that support an
HTTP transport can connect directly — no `mcp-remote`/Node bridge needed.

**Claude Code** (project-scoped `.mcp.json` at the repo root):

```json
{
  "mcpServers": {
    "fusion-essentials": {
      "type": "http",
      "url": "http://127.0.0.1:27182/mcp"
    }
  }
}
```

Approve the server when your client prompts you. (A `.mcp.json` is included in this
repo.)

## How to drive Fusion well (read this first)

Fusion is many environments glued together (CAD, assemblies, the parametric timeline, CAM, the cloud
data model). The biggest source of wasted/blind tool calls is acting before understanding the state.
Token efficiency is a first-class constraint of this surface — light default reads, family gating,
and pointer-bearing payloads exist so an agent spends context on decisions, not dumps. Work by
**progressive disclosure**, not by dumping whole documents:

1. **Orient** — `workspace_orient` first: one cheap read that reports the active document and
   where it lives, its health, units/mode, the major pieces, whether CAM data exists, and
   *pointers* to the right narrow tool for each area. The workspace/product decides
   which environment you're in and which deep read is meaningful.
2. **Read state with the right tool** before reasoning — `assembly_get` (kinematics: positions,
   ground flags, joint wiring), `design_get` (mode/tree/timeline/health/configs in one read — default
   for orientation, `include=['timeline']` for build intent), `cam_get` (CAM setups/operations/tools
   in one read), `param_get` (the parametric skeleton), `sketch_get` (one sketch's
   structure). These give STRUCTURED STATE.
3. **Verify with numbers, not pixels.** A screenshot of an assembly is the least reliable input —
   parts overlap at the origin and the active component greys the rest out. Reach for it last, and
   only on a single **isolated, oriented** component (`view_set` snapshot → isolate → orient →
   `view_screenshot` → restore). Re-read the state tool after any structural change.

**Know the blind spots** — places a read looks authoritative but isn't, so you draw a silent wrong
conclusion: CAM validity flags are stale until the Manufacture workspace has been entered;
`is_fully_constrained` is sketch-only (assembly freedom comes from joint motion types); grounding
is a two-flag trap (`isGrounded` vs `ground_to_parent`); the open-documents list is a superset of
the visible tabs; a bounding-box center is not the modelling origin; saves and opens are
asynchronous (confirm with a fresh read, never assume). When a read result is ambiguous, re-read
with the narrower tool named in the payload's `pointers`.

## Tools

**The authoritative tool inventory is [`tests/generated/TOOL_MANIFEST.md`](../../tests/generated/TOOL_MANIFEST.md)** —
generated from the live registry (every tool, each with its inputs and write level) — or ask a
connected client for its tool list (`tools/list`). Each tool's own `TOOL_DESCRIPTION` is the
contract the agent sees; this README does not restate it (a second copy only drifts). Tool names
are predictable: `<family>_<verb>`, so the family prefix tells you the area —

| Prefix | Area | Examples |
|--------|------|----------|
| `sys_` | session / introspection / the script escape hatch | `sys_find_tool`, `sys_get_api_doc`, `sys_execute_script` |
| `data_` | the cloud data model (projects, folders, files) | `data_get`, `data_upload_file`, `data_delete_file` |
| `doc_` | document lifecycle (open / save / copy / insert) | `doc_open`, `doc_save_as`, `doc_insert_occurrence` |
| `design_` | the active design as a whole (tree, timeline, mode) | `design_get`, `design_configure`, `design_recompute` |
| `sketch_` | 2D sketching | `sketch_create`, `sketch_add_geometry`, `sketch_constrain` |
| `model_` | solid features | `model_extrude`, `model_revolve`, `model_fillet`, `model_pattern_circular` |
| `joint_` | joints & joint origins | `joint_create`, `joint_at_geometry`, `joint_create_origin` |
| `assembly_` | positioning & kinematic state | `assembly_get`, `assembly_ground`, `assembly_move` |
| `param_` | parameters | `param_get`, `param_set`, `param_add` |
| `find_` | geometry queries returning handles | `find_geometry` |
| `view_` | workspace & viewport (screenshots, isolate, section) | `view_screenshot`, `view_set`, `view_section` |
| `cam_` | manufacturing (setups, operations, toolpaths) | `cam_get`, `cam_generate`, `cam_get_status` |
| `appearance_` / `mesh_` / `surface_` | colour, mesh bodies, surface modelling | `appearance_set`, `mesh_export`, `surface_thicken` |
| `drawing_` / `workspace_` / `save_` | 2D drawings, orientation, save-as-mesh | `drawing_create`, `workspace_orient`, `save_as_mesh` |

Every tool's result declares whether it **mutates** (read / writes the design / writes to
the cloud / destructive) — the `write` level is part of each tool's definition and is
surfaced to the client.

### A few core tools (the ones a session leans on)

These are the load-bearing reads that the design philosophy (above) is built around — start
here when learning the surface:

- **`workspace_orient`** — the cold-boot read. One call reports what's open, its health,
  whether CAM data exists, the major pieces, and *pointers* to the right narrow tool next.
  Call it first.
- **`assembly_get`** — kinematic state as JSON: every occurrence's world position, ground
  flags, and joint wiring. The numbers you reason about instead of a cluttered screenshot.
- **`find_geometry`** → **`joint_at_geometry`** — the geometry-as-values pair. `find_geometry`
  returns stable *handles* to faces/edges (filterable by radius/proximity); you pass a handle
  to a consumer like `joint_at_geometry`, which lands the joint AT that exact geometry.
- **`view_set`** + **`view_screenshot`** — the agent's "eyes": isolate/orient a single
  component, then capture it (a screenshot of a whole assembly is the least reliable input).
- **`sys_execute_script`** — the gated escape hatch: arbitrary Fusion Python, off by default
  (see Security). The typed tool surface exists so this is rarely needed.

### Things that aren't obvious from a tool's name

- The CAM tools read CAM data **without requiring you to switch to the Manufacture
  workspace**.
- `doc_open` / `doc_save_as` are **asynchronous**: the call returns before the document is
  fully active/saved — confirm with `workspace_orient` / `doc_get` afterward.
- `cam_get(include=['time'])` needs generated toolpaths to be meaningful.
- `cam_generate` is fire-and-poll: it returns immediately with a handle; poll `cam_get_status`
  until done (it never blocks for the multi-minute compute).

## Demonstrated workflows

Two kinds of runnable demonstration show the surface driving real work end-to-end:

- **Shipped procedures** (Claude Code skills in `.claude/skills/`): `build-tool-from-url` scrapes a
  vendor cutting-tool page and adds the tool to the active document's CAM library via
  `cam_edit_tools`; `insert-into-template` stands up a CAM job for a part — saves the CAD, defines a
  part-space origin, places the shop template, inserts and positions the part, and sizes stock from
  measurements. Both are built entirely from these tools; fork them as patterns for your own shop
  procedures.
- **The eval pipeline** (`tests/live/evals/scenarios/`): goal-shaped scenarios (parametric
  multi-part foundations, joints and motion, detail features, external references, CAM templating)
  that a context-isolated agent runs against a live session holding only this server's wire — the
  standing proof that the tool descriptions alone can carry an agent from a goal to a verified
  result.

## What makes the tools trustworthy (the contracts)

Three typed kind systems make false success structurally hard. They are what to study if you are
forking this as a pattern for your own MCP server:

- **Inputs** ([`tools/_inputs.py`](tools/_inputs.py)) — a tool never takes a bare `name: str` for
  existing geometry. Typed kinds resolve names and handles, REFUSE ambiguity instead of guessing
  an instance, and self-heal a stale geometry handle from its world-position locator.
- **Outputs** ([`tools/_outputs.py`](tools/_outputs.py)) — a tool declares `RETURNS = [...]`
  (handles, URNs, names, verdicts); tests assert the declared keys are actually minted, and an
  assertion read's verdict is a real boolean beside its measured evidence, never prose.
- **Postconditions** ([`tools/_assert.py`](tools/_assert.py)) — a write tool declares
  verify-the-effect kinds; the kernel re-reads ground truth after the mutation and converts a
  "success" that changed nothing into an error. The platform does return success while changing
  nothing; the kernel exists because of it.

The naming schema (`<family>_<verb>`, with the verb's read/write kind linted against the declared
write level), pure-ASCII wire strings, helper deduplication, and doc freshness are all enforced by
lints in `tests/`. The one command is `py -3 tests/check_all.py` (generator checks + the whole
suite + the live gate). Generated inventories live in `tests/generated/`:
[`TOOL_MANIFEST.md`](../../tests/generated/TOOL_MANIFEST.md) (per-tool),
[`TEST_SPEC.md`](../../tests/generated/TEST_SPEC.md) (behavior ledger), and
[`TOOL_POINTER_MAP.md`](../../tests/generated/TOOL_POINTER_MAP.md) (how tools point to each other, plus a
self-audit of the guidance strings). Authoring conventions live in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) and the `CLAUDE.md` files beside the code.

## Security

- **Loopback only.** The server binds `127.0.0.1`; it is not reachable from other
  machines. Requests from non-loopback web origins are rejected.
- **Off by default**, and only runs while the add-in is running.
- **`sys_execute_script` is separately gated.** It lets a connected agent run
  arbitrary Python in your active Fusion session — including modifying or deleting
  your design. It is **disabled by default**; enable it only if you trust the agent
  and the client connecting to the server, via **Settings → MCP Server → "Allow AI to
  execute arbitrary Fusion API scripts"** (then reload). A script's changes are grouped
  into ONE undo step, but a script that raises is **not** guaranteed to roll back —
  partial changes can commit, so verify state afterward and undo manually if needed.

## Platform support

Developed and tested on **Windows**. The add-in targets both Windows and macOS, and
the read/navigation tools use only cross-platform Fusion APIs. `sys_execute_script`
uses Fusion's `Python.Run` text command with a path-normalized temp file; this path
is believed correct on macOS but **has not yet been verified on a Mac**. If you run on
macOS, please test `sys_execute_script` before relying on it and report issues.
