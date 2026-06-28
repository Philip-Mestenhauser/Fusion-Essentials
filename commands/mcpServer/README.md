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

## How to drive Fusion well (read this first)

Fusion is many environments glued together (CAD, assemblies, the parametric timeline, CAM, the cloud
data model). The biggest source of wasted/blind tool calls is acting before understanding the state.
Work by **progressive disclosure**, not by dumping whole documents:

1. **Orient** — `sys_get_session` first (active document, **workspace/product**, units). The
   workspace decides which environment you're in and which deep read is meaningful.
2. **Read state with the right tool** before reasoning — `assembly_probe` (kinematics: positions,
   ground flags, joint wiring), `design_get_timeline` (build intent), `cam_get_setups`/
   `cam_get_operations` (CAM), `param_get` (the parametric skeleton), `sketch_get` (one sketch's
   structure). These give STRUCTURED STATE.
3. **Verify with numbers, not pixels.** A screenshot of an assembly is the least reliable input —
   parts overlap at the origin and the active component greys the rest out. Reach for it last, and
   only on a single **isolated, oriented** component (`view_inspect` snapshot → isolate → orient →
   `view_screenshot` → restore). Re-read the state tool after any structural change.

**Know the blind spots** — places a read looks authoritative but isn't, so you draw a silent wrong
conclusion (CAM validity is stale outside Manufacture; `is_fully_constrained` is sketch-only with no
DOF count; grounding is a two-flag trap; `doc_list_open` is a superset of tabs; bbox-center ≠
modelling origin; saves/opens are async). The full per-environment reference is
[`docs/reading-fusion-state.md`](../../docs/reading-fusion-state.md) — consult it on a first-contact
session or whenever a read result is ambiguous.

## Tools

| Tool | What it does | Mutates? |
|------|--------------|----------|
**Session & data model**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `sys_get_session` | Active document, workspace, units, component count | no |
| `doc_get_active_id` | Resolve the ACTIVE document to its data-model identity: lineage id (URN), version, web URL, and whether it is saved / has unsaved changes — act on "the active document" precisely instead of by name. An unsaved doc has no URN yet | no |
| `data_list_projects` | Projects in the active hub (name + id) | no |
| `data_list_files` | Files in a project: name, id (UID), versionId, extension, version, openable `fusionWebURL`, and `folder_path`. Optional `folder` scopes the listing to one folder path (with `recursive` to descend or list immediate files only) — avoids dumping a whole large project | no |
| `data_list_folders` | The project's folder tree (names, ids, full paths) to a bounded depth — discover structure before navigating | no |
| `doc_new` | Create and open a new, empty Fusion design (becomes the active doc; unsaved until you `doc_save_as`). Start fresh, then `sketch_create` / model | session doc (not cloud until saved) |
| `doc_open` | Open a document from any data-model identifier — a lineage/version UID, a `source_id`, or a Fusion web URL (`fusionWebURL`/`source_url`, whose embedded id is decoded automatically). Opens Configured Designs too (via `openUsingContext`) | switches active doc |
| `doc_save` | Save the ACTIVE document in place — a new cloud version of the same file (plain Save, vs `doc_save_as`). Version description auto-prefixed `[AI agent]` | **writes a new cloud version** |
| `doc_close` | Close an open document (by `name`, the active one, or `close_all`), with `save_changes` to save-or-discard. Note: closes loaded reference/dependency docs too with `close_all` | closes docs (discards edits unless saving) |
| `doc_activate` | Bring an open document to the foreground (make it the active document) | switches active doc |
| `doc_list_open` | List open documents (name/active/visible/saved/modified). Note `app.documents` is a SUPERSET of the user's visible tabs (loaded reference docs included) | no |
| `design_get_tree` | Walk the active design's assembly tree to a bounded depth, flagging external references and resolving each to its source document UID/URL | no |
| `param_get` | The design's user (and optionally model) parameters: name, expression, value, unit, comment | no |
| `param_add` | Add a user parameter (name + expression, unit, comment, favorite). Health-guarded: rolls back if it introduces a timeline error | **modifies the design** |
| `param_delete` | Delete a user parameter, guarded — refuses if referenced (reports consumers) or if it would break the timeline | **modifies the design** |
| `param_set_favorite` | Toggle a user parameter's favorite flag (whether it shows in the favorites list) | **modifies the design** |
| `design_get_timeline_health` | Feature error/warning rollup for the parametric timeline — names of any errored/warning features. Use around risky edits | no |
| `design_recompute` | Force a full recompute (`computeAll`) so downstream features rebuild (e.g. after editing text an emboss consumes); reports health after | **rebuilds features** |
| `design_get_timeline` | The parametric design's timeline: each feature/sketch/joint/occurrence's index, name, type, suppressed/rolled-back/group state, and health — understand how a template is built and spot alternate-config branches | no |
| `design_get_configurations` | A Configured Design's configurations: each one (name/id/active) plus the table's columns; optionally `activate` one by name/id to switch the live design to it (pair with `view_screenshot` to view each) | reading: no · activate: switches active config |
| `sketch_get` | Read sketches at the right depth. WITHOUT `sketch_name`: a summary list of every sketch (name, plane, line/circle/arc/point + profile counts, visibility). WITH `sketch_name`: that one sketch's full structure — every entity (id `<type>:<index>`, type, `isConstruction`, geometry), every geometric constraint (type + the entity ids it links), every dimension (name/value/expression/`driving`), and `is_fully_constrained`. The read tool for understanding a constrained sketch before editing it | no |
| `sketch_create` | Create a sketch on an origin plane (`xy`/`xz`/`yz`, aliases top/front/right) or a named construction plane; optional name | **modifies the design** |
| `sketch_add_geometry` | Draw one entity on a sketch — `line` / `rectangle` / `circle` / `arc` / `polygon` / `polyline` / `closed_path` — coordinates in mm/cm/in (default mm), angles in degrees. `polyline`/`closed_path` take a `points` list and draw a connected chain whose segments SHARE endpoints (coincident) so the shape is parametric (`closed_path` = a custom closed boundary). Targets a named sketch or the most recent | **modifies the design** |
| `sketch_constrain` | Apply a geometric constraint to sketch entities — perpendicular / parallel / tangent / equal / concentric / collinear / midpoint / coincident / horizontal / vertical / symmetry / fix / unfix. Reference entities as `<type>:<index>` (e.g. `line:0`, `arc:1`, `point:2`); two-curve constraints take two, midpoint/coincident take a point + curve, symmetry takes two + an axis line. Makes a sketch parametric | **modifies the design** |
| `sketch_set_text` | Set the displayed string of sketch text entities (e.g. an engraved label/nameplate). Target a sketch by name (e.g. `File_Name`) or update every sketch text; reports before/after | **modifies the design** |
| `sketch_add_3d_line` | Draw a line in 3D where the end may be OFF the sketch plane (non-zero z); optionally lock the start to the sketch origin with a coincident constraint. Reports each endpoint and whether the end is off-plane | **modifies the design** |
| `model_create_component` | Create a new EMPTY component occurrence (optionally named, placed at x/y/z, and `activate`d as the edit target) — the prerequisite for building an assembly of separate, jointable parts (the modelling tools build into the active component) | **modifies the design** |
| `model_extrude` | Extrude a closed sketch profile into a 3D solid — the back half of modelling (pairs with `sketch_create`/`sketch_add_geometry`). Pick the sketch + `profile_index`, a `distance` (mm/cm/in; negative reverses), `operation` (`new`/`join`/`cut`/`intersect`), and optional `symmetric` / `taper_deg`. Returns the resulting body names | **modifies the design** |
| `model_revolve` | Revolve a closed sketch profile about an axis into a solid (turned parts: shafts, pistons, pulleys) — the companion to `model_extrude`. `axis` = `x`/`y`/`z` (component origin axis) or `line:<index>` (a sketch line); `angle_deg` (360 = full); `operation` (`new`/`join`/`cut`/`intersect`); `symmetric` | **modifies the design** |
| `model_combine` | Boolean combine solid BODIES — `target` body + `tools` body name(s), `operation` `join`/`cut`/`intersect` (the body-on-body boolean extrude/revolve can't do: fuse a boss, bore a hole with a cylinder body, intersect volumes). `keep_tools` to retain the tools | **modifies the design** |
| `model_fillet` | Round a body's edges with a constant `radius` — applies to ALL edges of `body_name` (omit = most recent), or `edge_filter` `convex`/`concave` to limit. The deburr/edge-break every real part needs | **modifies the design** |
| `model_chamfer` | Bevel a body's edges with a constant `distance` — all edges of `body_name`, or `edge_filter` `convex`/`concave`. An angled edge break | **modifies the design** |
| `model_construction` | Add a construction datum: `kind` `point` (at `x`/`y`/`z`), `axis` (through `x`/`y`/`z` along `x`/`y`/`z`), or `plane` (`offset` from origin `plane` `xy`/`xz`/`yz`). Reference datums to snap joints/sketches to a precise spot no vertex occupies. (A direct-modeling design may reject these — reported clearly) | **modifies the design** |
| `model_mirror` | Mirror solid `bodies` across an origin `plane` (`xy`/`xz`/`yz`) — the symmetric half (a V-bank other side, a left/right part). `join` combines the mirror with the original | **modifies the design** |
| `model_arrange` | Nest/pack component occurrences within a 2D boundary defined by a sketch profile — `boundary_sketch` + the `shapes` to lay out. `solver` true_shape (actual outlines) or rectangular; `spacing` clearance. The nesting/Arrange feature | **modifies the design** |
| `model_pattern_rectangular` | Duplicate component occurrence(s) in a rectangular grid — `quantity_one`/`spacing_one` along a world axis (`x`/`y`/`z`), plus an optional second direction. Spacing is the distance between instances | **modifies the design** |
| `model_pattern_circular` | Duplicate component occurrence(s) evenly around a world axis (`x`/`y`/`z`) — `quantity` over `total_angle_deg` (360 = full ring), optionally symmetric | **modifies the design** |
| `joint_create_origin` | Place a Joint Origin (reusable coordinate frame / WCS anchor) programmatically. `anchor=coordinates` → world-aligned at x/y/z or the model origin; `anchor=sketch_line` → Z runs ALONG a sketch line for **any orientation** (draw the direction with `sketch_add_3d_line` first); `anchor=sketch_point` → on an existing point. Reports the resulting Z/X/Y axes | **modifies the design** |
| `joint_create` | Create a Joint (timeline feature) between two inputs — each a Joint Origin name OR an **autonomous geometry snap** `<occurrence>:<snap>` (snap = origin/center/top/bottom/left/right/front/back/cylinder, no human pick). `rigid` (default) / revolute / slider / cylindrical / planar / ball, with `axis` (frame-relative) or `world_axis` (true world axis), offset / angle / flip, and rotation (`min_deg`/`max_deg`/`rest_deg`) + linear (`min_mm`/`max_mm`/`rest_mm`) limits | **modifies the design** |
| `joint_edit` | Edit an EXISTING joint in place (no remaking) — re-select snap inputs, change motion type/`axis`/`world_axis`, toggle flip, set offset/angle, set rotation/linear limits. Rolls the timeline marker around the edit as the API requires. (To DRIVE a joint to a value, use `assembly_move` + `assembly_capture_position`) | **modifies the design** |
| `joint_create_as_built` | Rigidly joint two occurrences WHERE THEY ALREADY ARE — no joint origins needed (unlike `joint_create`). Pass the two occurrence names | **modifies the design** |
| `assembly_constrain` | Constrain occurrences' geometry — the Constrain Components relationship (flush / coincident / concentric / at an angle, inferred from geometry). Autonomous via `snap_one`/`snap_two` (`<occurrence>:<snap>`) or a `relationships` list (many snap-pairs solved together in one constraint to fully locate a part); or selection mode (pick entities in Fusion first). Optional `offset`/`angle_deg`/`flip` | **modifies the design** |
| `assembly_ground` | Set an occurrence's ground flags — `grounded` pins it in space (explicit Ground); `ground_to_parent` is the default rigid-to-parent lock (set **false** to free a fresh/patterned occurrence so it can be moved or jointed). Two distinct flags | **modifies the design** |
| `assembly_move` | Free-move an occurrence by editing its transform — `dx`/`dy`/`dz` translate (mm/cm/in) and optional `rotate_deg` about a world axis. A one-shot reposition, no joint/relationship created (the occurrence must be free — see `assembly_ground`) | **modifies the design** |
| `assembly_rigid_group` | Lock two or more occurrences together as one rigid unit; `include_children` to include their children | **modifies the design** |
| `assembly_capture_position` | The flexible-assembly POSITION mechanic — `capture` records the current (moved-but-transient) pose of jointed components into the timeline (valid only when a move is pending), `revert` drops the latest capture, `status` reports pending + captured count | capture/revert **modify**; status no |
| `model_measure_bbox` | Measure a body/component's bounding box (X/Y/Z extents, center, axes) in mm/cm/in. World-axis-aligned by default, or pass `frame=<joint origin name>` to measure in that **part-space frame** — feed the result to `param_set` to drive stock size | no |
| `assembly_probe` | The assembly's KINEMATIC STATE as JSON — per occurrence: world position (origin + bbox center/size), ground flags (`grounded`/`ground_to_parent`), and the joints it's in; plus a joint list (type, DOF, the two occurrences each connects) and which occurrences are grounded. Reason about grounding/positions/joint-wiring from NUMBERS instead of a cluttered screenshot | no |
| `find_geometry` | Query a part's faces/edges/vertices and return stable HANDLES (entity tokens) + each one's kind, world position, and shape data (a cylinder face's radius+axis, a circular edge's radius). Filter by `kind` / `radius` / `nearest_to`. The query half of **geometry-as-values**: pass a handle to `joint_at_geometry` instead of guessing a snap-string | no |
| `joint_at_geometry` | Joint two parts AT specific geometry — pass two `find_geometry` handles (e.g. a crank pin's cylinder face + a rod bore) and the joint lands AT that geometry (the offset pin/bore), NOT collapsed to the part origins. `motion` rigid/revolute/slider/cylindrical/ball + `axis`. BAKES IN the runtime rules (proxies handles into occurrences, picks a valid keypoint by face type — a cylinder needs middle, not center) so the agent never rediscovers them by crashing | **modifies the design** |
| `param_set` | Set a parameter's expression (value); reports before/after. Drives geometry/stock/suppression downstream | **modifies the design** |
| `data_create_project` | Create a new project in the active hub | **writes to cloud** |
| `data_create_folder` | Create a folder; `parent_folder` accepts a nested path (`Fixtures/Vises`) and creates missing parents (`mkdir -p`) | **writes to cloud** |
| `data_upload_file` | Upload a local CAD file into a project/nested folder path; neutral formats (STEP/IGES/…) are translated to a Fusion `.f3d` during processing (async). `create_path=true` makes missing destination folders | **writes to cloud** |
| `doc_save_as` | Save the ACTIVE (possibly never-saved) document into a project/folder via `Document.saveAs` — captures the live session, unlike `data_upload_file` (local file) or `doc_copy` (existing saved file). Async: confirm the lineage URN afterward with `doc_get_active_id` | **writes to cloud** |
| `doc_copy` | Copy an existing saved cloud document (by lineage URN, or name + source project) into a project/folder; external references are preserved as pointers to their original source files. Generic cloud-to-cloud copy (does not touch the session) | **writes to cloud** |
| `doc_insert_occurrence` | Insert a SAVED cloud document (by URN/URL) into the active design as a component occurrence — external reference (default; same-project) or embedded — under the root or a named `into_component`. Optional `remove_existing` deletes a named occurrence (and its joints) first. New occurrence lands at the identity transform; position it with a joint | **modifies the design** |
| `doc_update_xref` | Refresh the active document's external references to their latest cloud version (the API 'Get Latest'). Updates all out-of-date refs by default, or one by `name`; reports version before/after. Use when a referenced part was edited after insertion (e.g. to pull in a joint origin added later) | **modifies the design** |
| `data_delete_file` | Delete a cloud document by URN, guarded: requires a matching `confirm_name`; refuses a file that is open or referenced by others (would orphan them) unless `force=true`. Irreversible | **deletes from cloud** |
| `data_delete_folder` | Delete a data-model folder by id, guarded: requires a matching `confirm_name`; never a project root; refuses a non-empty folder unless `force=true`. Irreversible | **deletes from cloud** |

**Workspaces & viewport**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `view_list_workspaces` | All workspaces + which is active | no |
| `view_switch_workspace` | Activate a workspace by id/name/alias (design/manufacture/…) | switches workspace |
| `view_screenshot` | Capture the viewport as a PNG (optional camera view); restores your camera afterward | no |
| `view_screenshot_multi` | Capture SEVERAL views in ONE call — front/top/right/iso etc. (or `all` six orthographic) as separate labelled images — so you can read a 3D layout reliably instead of guessing from one isometric. Restores the camera afterward | no |
| `view_set_visibility` | Isolate / show / hide / clear-isolation on component occurrences (by name or full path) so a screenshot shows just what matters; reports before/after so you can restore. View state only — not geometry | changes what's visible |
| `view_inspect` | The agent's "eyes": composable view verbs — `snapshot`/`restore` (in-memory push/pop of camera+style+visibility), `orient` (aim the camera by preset front/top/right/iso-… via explicit eye/target/up, and/or fit to a named occurrence), `isolate`/`show`/`hide`/`clear_isolation` (`show` lights the whole ancestor chain so a nested occurrence actually appears), `style` (shaded ↔ wireframe), and `save_view`/`apply_view`/`list_views` (persistent document Named Views — **camera-only** bookmarks). Intuit a design from many angles/states then return to how it was. View state only; pair with `view_screenshot` | changes view state (restorable) |
| `view_section` | Cut the model with a live Section Analysis to see INSIDE — cavities, wall thickness, how a part nests in a fixture, where an internal void sits. `cut` on an origin plane (xy/xz/yz) or `through` a named occurrence's center, with `offset` (mm) and `flip`; `list`; `clear` (remove all cuts, restoring the full view). Non-destructive (a cutaway, not a geometry edit); pair with `view_inspect` + `view_screenshot` | adds/removes a section analysis (restorable) |
| `sys_request_selection` | Hand control to the user to click a face/edge/vertex/body/component in Fusion (clears the prior selection by default). Returns immediately (non-blocking, no Fusion dialog) — the agent presents its own one-click confirmation; pair with `sys_get_selection` | clears selection |
| `sys_get_selection` | Read the user's current Fusion selection and describe each entity — type, owning body/component, geometry hints (face area/centroid/surface type, edge length/endpoints, vertex position, body volume), a `direction` unit vector where meaningful (planar-face normal, cyl/cone axis, linear-edge direction — for defining a machining axis or joint-origin orientation), and click point | no |

**CAM / manufacturing**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `cam_get_setups` | Setups: name, type, machine, selected models/fixtures/stock, op counts | no |
| `cam_get_operations` | Operations per setup: tool, strategy, and per-op health — `state` (valid/invalid/suppressed/no_toolpath), `is_out_of_date`, and the actual warning/error text (e.g. spindle-speed-over-limit, empty toolpath) — plus a tools-used summary | no |
| `cam_get_references` | Resolve a setup's external (X-ref) selections to their source document UID/URL | no |
| `sys_get_tool_list` | Distinct cutting tools across the document (number/type/geometry), usage-ranked, with the ops/setups that use each — the tool sheet | no |
| `cam_get_time` | Estimated cycle time per setup and total (feed/rapid/tool-changes) | no |
| `cam_get_nc_programs` | NC programs: name, machine, post configuration, operation count, and the post parameters each exposes | no |
| `cam_set_nc_comment` | Set the Comment field (and optionally Name) of one NC program or all — what most posts emit atop the G-code. Writes the `nc_program_comment` CAM parameter directly | **modifies CAM data** |
| `cam_compare_operations` | Diff two operations' CAM parameters to show exactly what differs (and the value on each side) — understand a machining strategy | no |
| `cam_list_templates` | Navigate the toolpath template library (cloud/local/fusion/…): folders + templates (name, description, validity) | no |
| `cam_apply_template` | Apply a toolpath template to a setup, recreating its operations there (by template name or URL; optional toolpath generation) | **modifies the document** |
| `cam_generate` | Launch toolpath (re)generation for the document, a setup, a folder, or one operation and return IMMEDIATELY with a handle — never blocks for the (often minutes-long) compute. `skip_valid` (default) regenerates only out-of-date operations | **modifies the document** |
| `cam_get_status` | Poll a launched generation by handle: live op-state tally, the op currently computing + its progress %, and — once complete — each operation's warnings/errors and empty toolpaths. Each poll nudges the main-thread compute forward a bounded burst, so poll repeatedly until done (no long blocking) | no (advances the launched job) |
| `cam_save_template` | Bundle a subset of a setup's operations into a new toolpath template in the library (into a folder, created if missing) | **writes to the template library** |
| `cam_activate_setup` | Activate a setup by name and fit the view (pair with `view_screenshot`) | changes active setup |
| `cam_show_toolpath` | Show/hide individual CAM toolpaths (the displayed blue paths) to study one operation's path at a time — `show`/`hide`/`isolate` an operation, `show_folder`, `hide_all`, `list`. `fit` frames the camera to the operation's toolpath extents. Manufacture-workspace display; toggles `Operation.isLightBulbOn` (no simulation commands) | changes toolpath display |

**Developer / advanced**

| Tool | What it does | Mutates? |
|------|--------------|----------|
| `sys_get_api_doc` | Regex-search the LIVE Fusion API (classes, methods, properties, enum values) for names, signatures, and docstrings — introspected from the `adsk.*` modules in the running process, so the docs always match the installed Fusion version (nothing bundled). Scope with `apiCategory` (class/member/description/all) and `filter` (`adsk.<ns>` or `adsk.<ns>.<Class>`). Use it before writing an `sys_execute_script` to confirm exact signatures | no |
| `sys_reload_addin` | Reload Fusion-Essentials to pick up code changes (developer tool) | restarts add-in |
| `sys_execute_script` | Run arbitrary Fusion API Python in your session | **yes — see Security** |

The CAM tools read CAM data **without requiring you to switch to the Manufacture
workspace**. `doc_open` opens cloud files **asynchronously**: the call returns
before the document is fully active — call `sys_get_session` afterward to confirm it
is active before operating on it. `cam_get_time` needs generated toolpaths to be
meaningful.

## Security

- **Loopback only.** The server binds `127.0.0.1`; it is not reachable from other
  machines. Requests from non-loopback web origins are rejected.
- **Off by default**, and only runs while the add-in is running.
- **`sys_execute_script` is separately gated.** It lets a connected agent run
  arbitrary Python in your active Fusion session — including modifying or deleting
  your design. It is **disabled by default**; enable it only if you trust the agent
  and the client connecting to the server, via **Settings → MCP Server → "Allow AI to
  execute arbitrary Fusion API scripts"** (then reload). Scripts run inside a Fusion
  transaction, so a script that raises is rolled back.

## Platform support

Developed and tested on **Windows**. The add-in targets both Windows and macOS, and
the read/navigation tools use only cross-platform Fusion APIs. `sys_execute_script`
uses Fusion's `Python.Run` text command with a path-normalized temp file; this path
is believed correct on macOS but **has not yet been verified on a Mac**. If you run on
macOS, please test `sys_execute_script` before relying on it and report issues.
