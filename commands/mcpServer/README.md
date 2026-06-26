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

## Tools

| Tool | What it does | Mutates? |
|------|--------------|----------|
**Session & data model**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `get_session_info` | Active document, workspace, units, component count | no |
| `get_active_document_id` | Resolve the ACTIVE document to its data-model identity: lineage id (URN), version, web URL, and whether it is saved / has unsaved changes — act on "the active document" precisely instead of by name. An unsaved doc has no URN yet | no |
| `list_projects` | Projects in the active hub (name + id) | no |
| `list_project_files` | Files in a project: name, id (UID), versionId, extension, version, openable `fusionWebURL`, and `folder_path` | no |
| `list_folders` | The project's folder tree (names, ids, full paths) to a bounded depth — discover structure before navigating | no |
| `new_document` | Create and open a new, empty Fusion design (becomes the active doc; unsaved until you `save_document_as`). Start fresh, then `create_sketch` / model | session doc (not cloud until saved) |
| `open_document` | Open a document from any data-model identifier — a lineage/version UID, a `source_id`, or a Fusion web URL (`fusionWebURL`/`source_url`, whose embedded id is decoded automatically). Opens Configured Designs too (via `openUsingContext`) | switches active doc |
| `get_component_tree` | Walk the active design's assembly tree to a bounded depth, flagging external references and resolving each to its source document UID/URL | no |
| `get_parameters` | The design's user (and optionally model) parameters: name, expression, value, unit, comment | no |
| `get_timeline` | The parametric design's timeline: each feature/sketch/joint/occurrence's index, name, type, suppressed/rolled-back/group state, and health — understand how a template is built and spot alternate-config branches | no |
| `get_configurations` | A Configured Design's configurations: each one (name/id/active) plus the table's columns; optionally `activate` one by name/id to switch the live design to it (pair with `get_screenshot` to view each) | reading: no · activate: switches active config |
| `get_sketches` | List the design's sketches: name, plane, line/circle/arc/point and profile counts, visibility | no |
| `create_sketch` | Create a sketch on an origin plane (`xy`/`xz`/`yz`, aliases top/front/right) or a named construction plane; optional name | **modifies the design** |
| `add_sketch_geometry` | Draw one entity on a sketch — `line` / `rectangle` / `circle` / `arc` / `polygon` — coordinates in mm/cm/in (default mm), angles in degrees. Targets a named sketch or the most recent | **modifies the design** |
| `set_sketch_text` | Set the displayed string of sketch text entities (e.g. an engraved label/nameplate). Target a sketch by name (e.g. `File_Name`) or update every sketch text; reports before/after | **modifies the design** |
| `draw_3d_line` | Draw a line in 3D where the end may be OFF the sketch plane (non-zero z); optionally lock the start to the sketch origin with a coincident constraint. Reports each endpoint and whether the end is off-plane | **modifies the design** |
| `create_joint_origin` | Place a Joint Origin (reusable coordinate frame / WCS anchor) programmatically. `anchor=coordinates` → world-aligned at x/y/z or the model origin; `anchor=sketch_line` → Z runs ALONG a sketch line for **any orientation** (draw the direction with `draw_3d_line` first); `anchor=sketch_point` → on an existing point. Reports the resulting Z/X/Y axes | **modifies the design** |
| `joint` | Create a Joint (timeline feature) between two inputs (by Joint Origin name) — `rigid` (default) / revolute / slider / cylindrical / planar / ball, with optional axis / offset / angle / flip. Resolves joint origins inside referenced occurrences via their assembly-context proxy | **modifies the design** |
| `measure_bounding_box` | Measure a body/component's bounding box (X/Y/Z extents, center, axes) in mm/cm/in. World-axis-aligned by default, or pass `frame=<joint origin name>` to measure in that **part-space frame** — feed the result to `set_parameter` to drive stock size | no |
| `set_parameter` | Set a parameter's expression (value); reports before/after. Drives geometry/stock/suppression downstream | **modifies the design** |
| `create_project` | Create a new project in the active hub | **writes to cloud** |
| `create_folder` | Create a folder; `parent_folder` accepts a nested path (`Fixtures/Vises`) and creates missing parents (`mkdir -p`) | **writes to cloud** |
| `upload_file` | Upload a local CAD file into a project/nested folder path; neutral formats (STEP/IGES/…) are translated to a Fusion `.f3d` during processing (async). `create_path=true` makes missing destination folders | **writes to cloud** |
| `save_document_as` | Save the ACTIVE (possibly never-saved) document into a project/folder via `Document.saveAs` — captures the live session, unlike `upload_file` (local file) or `copy_document` (existing saved file). Async: confirm the lineage URN afterward with `get_active_document_id` | **writes to cloud** |
| `copy_document` | Copy an existing saved cloud document (by lineage URN, or name + source project) into a project/folder; external references are preserved as pointers to their original source files. Generic cloud-to-cloud copy (does not touch the session) | **writes to cloud** |
| `insert_occurrence` | Insert a SAVED cloud document (by URN/URL) into the active design as a component occurrence — external reference (default; same-project) or embedded — under the root or a named `into_component`. Optional `remove_existing` deletes a named occurrence (and its joints) first. New occurrence lands at the identity transform; position it with a joint | **modifies the design** |
| `update_xref` | Refresh the active document's external references to their latest cloud version (the API 'Get Latest'). Updates all out-of-date refs by default, or one by `name`; reports version before/after. Use when a referenced part was edited after insertion (e.g. to pull in a joint origin added later) | **modifies the design** |
| `delete_document` | Delete a cloud document by URN, guarded: requires a matching `confirm_name`; refuses a file that is open or referenced by others (would orphan them) unless `force=true`. Irreversible | **deletes from cloud** |
| `delete_folder` | Delete a data-model folder by id, guarded: requires a matching `confirm_name`; never a project root; refuses a non-empty folder unless `force=true`. Irreversible | **deletes from cloud** |

**Workspaces & viewport**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `list_workspaces` | All workspaces + which is active | no |
| `switch_workspace` | Activate a workspace by id/name/alias (design/manufacture/…) | switches workspace |
| `get_screenshot` | Capture the viewport as a PNG (optional camera view); restores your camera afterward | no |
| `set_visibility` | Isolate / show / hide / clear-isolation on component occurrences (by name or full path) so a screenshot shows just what matters; reports before/after so you can restore. View state only — not geometry | changes what's visible |
| `request_user_selection` | Hand control to the user to click a face/edge/vertex/body/component in Fusion (clears the prior selection by default). Returns immediately (non-blocking, no Fusion dialog) — the agent presents its own one-click confirmation; pair with `get_user_selection` | clears selection |
| `get_user_selection` | Read the user's current Fusion selection and describe each entity — type, owning body/component, geometry hints (face area/centroid/surface type, edge length/endpoints, vertex position, body volume), a `direction` unit vector where meaningful (planar-face normal, cyl/cone axis, linear-edge direction — for defining a machining axis or joint-origin orientation), and click point | no |

**CAM / manufacturing**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `get_cam_setups` | Setups: name, type, machine, selected models/fixtures/stock, op counts | no |
| `get_cam_operations` | Operations per setup: tool, strategy, and per-op health — `state` (valid/invalid/suppressed/no_toolpath), `is_out_of_date`, and the actual warning/error text (e.g. spindle-speed-over-limit, empty toolpath) — plus a tools-used summary | no |
| `get_setup_references` | Resolve a setup's external (X-ref) selections to their source document UID/URL | no |
| `get_tool_list` | Distinct cutting tools across the document (number/type/geometry), usage-ranked, with the ops/setups that use each — the tool sheet | no |
| `get_machining_time` | Estimated cycle time per setup and total (feed/rapid/tool-changes) | no |
| `get_nc_programs` | NC programs: name, machine, post configuration, operation count, and the post parameters each exposes | no |
| `set_nc_program_comment` | Set the Comment field (and optionally Name) of one NC program or all — what most posts emit atop the G-code. Writes the `nc_program_comment` CAM parameter directly | **modifies CAM data** |
| `compare_operations` | Diff two operations' CAM parameters to show exactly what differs (and the value on each side) — understand a machining strategy | no |
| `list_cam_templates` | Navigate the toolpath template library (cloud/local/fusion/…): folders + templates (name, description, validity) | no |
| `apply_template_to_setup` | Apply a toolpath template to a setup, recreating its operations there (by template name or URL; optional toolpath generation) | **modifies the document** |
| `generate_toolpaths` | Launch toolpath (re)generation for the document, a setup, a folder, or one operation and return IMMEDIATELY with a handle — never blocks for the (often minutes-long) compute. `skip_valid` (default) regenerates only out-of-date operations | **modifies the document** |
| `get_generation_status` | Poll a launched generation by handle: live op-state tally, the op currently computing + its progress %, and — once complete — each operation's warnings/errors and empty toolpaths. Each poll nudges the main-thread compute forward a bounded burst, so poll repeatedly until done (no long blocking) | no (advances the launched job) |
| `save_operations_as_template` | Bundle a subset of a setup's operations into a new toolpath template in the library (into a folder, created if missing) | **writes to the template library** |
| `activate_setup` | Activate a setup by name and fit the view (pair with `get_screenshot`) | changes active setup |

**Developer / advanced**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `reload_addin` | Reload Fusion-Essentials to pick up code changes (developer tool) | restarts add-in |
| `execute_api_script` | Run arbitrary Fusion API Python in your session | **yes — see Security** |

The CAM tools read CAM data **without requiring you to switch to the Manufacture
workspace**. `open_document` opens cloud files **asynchronously**: the call returns
before the document is fully active — call `get_session_info` afterward to confirm it
is active before operating on it. `get_machining_time` needs generated toolpaths to be
meaningful.

## Security

- **Loopback only.** The server binds `127.0.0.1`; it is not reachable from other
  machines. Requests from non-loopback web origins are rejected.
- **Off by default**, and only runs while the add-in is running.
- **`execute_api_script` is separately gated.** It lets a connected agent run
  arbitrary Python in your active Fusion session — including modifying or deleting
  your design. It is **disabled by default**; enable it only if you trust the agent
  and the client connecting to the server, via **Settings → MCP Server → "Allow AI to
  execute arbitrary Fusion API scripts"** (then reload). Scripts run inside a Fusion
  transaction, so a script that raises is rolled back.

## Platform support

Developed and tested on **Windows**. The add-in targets both Windows and macOS, and
the read/navigation tools use only cross-platform Fusion APIs. `execute_api_script`
uses Fusion's `Python.Run` text command with a path-normalized temp file; this path
is believed correct on macOS but **has not yet been verified on a Mac**. If you run on
macOS, please test `execute_api_script` before relying on it and report issues.
