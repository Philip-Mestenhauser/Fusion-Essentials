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
| `open_document` | Open a document from any data-model identifier — a lineage/version UID, a `source_id`, or a Fusion web URL (`fusionWebURL`/`source_url`, whose embedded id is decoded automatically). Opens Configured Designs too (via `openUsingContext`) | switches active doc |
| `get_component_tree` | Walk the active design's assembly tree to a bounded depth, flagging external references and resolving each to its source document UID/URL | no |
| `get_parameters` | The design's user (and optionally model) parameters: name, expression, value, unit, comment | no |
| `get_timeline` | The parametric design's timeline: each feature/sketch/joint/occurrence's index, name, type, suppressed/rolled-back/group state, and health — understand how a template is built and spot alternate-config branches | no |
| `set_parameter` | Set a parameter's expression (value); reports before/after. Drives geometry/stock/suppression downstream | **modifies the design** |
| `create_project` | Create a new project in the active hub | **writes to cloud** |
| `create_folder` | Create a folder; `parent_folder` accepts a nested path (`Fixtures/Vises`) and creates missing parents (`mkdir -p`) | **writes to cloud** |
| `upload_file` | Upload a local CAD file into a project/nested folder path; neutral formats (STEP/IGES/…) are translated to a Fusion `.f3d` during processing (async). `create_path=true` makes missing destination folders | **writes to cloud** |
| `save_document_as` | Save the ACTIVE (possibly never-saved) document into a project/folder via `Document.saveAs` — captures the live session, unlike `upload_file` (local file) or `copy_document` (existing saved file). Async: confirm the lineage URN afterward with `get_active_document_id` | **writes to cloud** |
| `copy_document` | Copy an existing saved cloud document (by lineage URN, or name + source project) into a project/folder; external references are preserved as pointers to their original source files. Generic cloud-to-cloud copy (does not touch the session) | **writes to cloud** |
| `delete_document` | Delete a cloud document by URN, guarded: requires a matching `confirm_name`; refuses a file that is open or referenced by others (would orphan them) unless `force=true`. Irreversible | **deletes from cloud** |
| `delete_folder` | Delete a data-model folder by id, guarded: requires a matching `confirm_name`; never a project root; refuses a non-empty folder unless `force=true`. Irreversible | **deletes from cloud** |

**Workspaces & viewport**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `list_workspaces` | All workspaces + which is active | no |
| `switch_workspace` | Activate a workspace by id/name/alias (design/manufacture/…) | switches workspace |
| `get_screenshot` | Capture the viewport as a PNG (optional camera view); restores your camera afterward | no |
| `set_visibility` | Isolate / show / hide / clear-isolation on component occurrences (by name or full path) so a screenshot shows just what matters; reports before/after so you can restore. View state only — not geometry | changes what's visible |

**CAM / manufacturing**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `get_cam_setups` | Setups: name, type, machine, selected models/fixtures/stock, op counts | no |
| `get_cam_operations` | Operations per setup, the tool each uses, and a tools-used summary | no |
| `get_setup_references` | Resolve a setup's external (X-ref) selections to their source document UID/URL | no |
| `get_tool_list` | Distinct cutting tools across the document (number/type/geometry), usage-ranked, with the ops/setups that use each — the tool sheet | no |
| `get_machining_time` | Estimated cycle time per setup and total (feed/rapid/tool-changes) | no |
| `get_nc_programs` | NC programs: name, machine, post configuration, operation count, and the post parameters each exposes | no |
| `compare_operations` | Diff two operations' CAM parameters to show exactly what differs (and the value on each side) — understand a machining strategy | no |
| `list_cam_templates` | Navigate the toolpath template library (cloud/local/fusion/…): folders + templates (name, description, validity) | no |
| `apply_template_to_setup` | Apply a toolpath template to a setup, recreating its operations there (by template name or URL; optional toolpath generation) | **modifies the document** |
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
