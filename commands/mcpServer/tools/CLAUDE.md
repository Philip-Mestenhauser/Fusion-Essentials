# Adding or editing a tool here

This is the tool-authoring recipe for `commands/mcpServer/tools/`. Read the root
[CLAUDE.md](../../../CLAUDE.md) first for the constitution (kinds, naming schema, honesty contract,
input kinds); this file is the how-to that sits under it. See
[CONTRIBUTING.md](../../../CONTRIBUTING.md) for the concepts behind these conventions and
[tests/CLAUDE.md](../../../tests/CLAUDE.md) for how to test what you add here.

## Adding a tool

- One tool per `tools/<name>.py`, exposing `handler(...)`, a description, a `Tool`, an `Item`, and
  `register_tool()`. Registration is auto-discovered (a pkgutil sweep) — **just drop the file in**;
  do not edit `tools/__init__.py` or `entry.py`. `_`-prefixed modules are shared helpers, never tools.
- **Reuse before you write.** Grep the helper map below before adding a resolver; use `sys_find_tool
  <kw>` before adding a tool/input. Don't duplicate what exists — extend it.
- Set `write=` on the `Item` honestly: `"read"`, `"write"`, or `"destructive"`. It is linted.
- Run on the main thread (the default) — anything touching `adsk.*` off the main thread can crash
  Fusion. Never block: no `sleep`, polling, or synchronous network in a handler.
- Confirm signatures/properties against the live API (`sys_get_api_doc`) **before** writing them. Do
  not guess a property exists — verify it. A non-obvious API fact lives where it is load-bearing: a
  typed kind or guard when it constrains an input, the tool description when the calling agent needs
  it, or a short comment at the point of use. `sys_get_api_doc` is the reference for raw signatures.

## Helper map (grep the module before writing a resolver)

| Helper | Provides |
|---|---|
| `_common` | `ok`/`error`/`safe`, `design()`/`target_component()`, `resolve_sketch`, unit `scale`/`UNIT_TO_CM` — the response+resolve substrate every tool imports. |
| `_inputs` | The typed input kinds (`GeometryHandle`, `BodyRef`, `OccurrenceRef`, `PlaneRef`, `AxisRef`, `Choice`, …) and `resolve_inputs(...)` — schema + resolve + validate + contract line for one declared input. |
| `_outputs` | The typed output kinds (`ReturnsHandle`/`ReturnsUrn`/`ReturnsName`/`ReturnsValue`/`ReturnsVerdict`) — a tool declares `RETURNS = [...]` once and a test asserts the id is actually minted. |
| `_cam_common` | `get_cam()` — the one CAM-product resolver every CAM tool calls — plus the shared job-health/readiness signal. |
| `_data_common` | Cloud data-model helpers shared by `data_ops`, `doc_lifecycle`, `_data_read`, `doc_open`, `doc_insert_occurrence`: hub/project/folder resolution, URN/web-URL decoding, the `[AI agent]` save-description marker. |
| `_data_read` | The cloud READ cores behind `data_get` (project list, a project's file listing) — depth/count-capped folder recursion in one place. |
| `_export` | Export-to-disk substrate shared by `design_export` + `mesh_export`: filename `sanitize`, `component_by_name`, `verify_written` (file-landed proof), split-by-occurrence orchestration. |
| `_holder` | Tool-holder geometry: `get_axis`, `get_tool_profile`, `build_holder_data`, `get_tooling_libraries` — the headless core behind `model_compute_holder`. |
| `_joints` | `build_joint_geometry` (keypoint factory per entity kind), `apply_motion` (motion-type dispatch, frame-relative or a custom direction entity), `current_joint_type`, `find_joint` (walks joints AND asBuiltJoints, root and every sub-component) — shared by `joint_create_edit`, `joint_at_geometry`, `joint_create_origin`, `joint_motion_link`, `joint_drive`. |
| `_view_common` | The camera-orientation table for the standard named views: `view_direction`/`look_direction` (opposite-sign consumers) + `up_vector` + `is_ortho_face` — shared by `view_screenshot`, `view_inspect`, `view_section`. |

## Postconditions — an Edit tool declares verify-the-effect (the third kind system)

`_inputs` types what a tool is GIVEN, `_outputs` what it RETURNS, `_assert` what it DID. A WRITE tool
passes `postconditions=[...]` to `Item.create_tool_item` — the kernel runs capture -> handler ->
verify and converts a success whose declared effect did not take into an error (the platform DOES
return success while changing nothing: `Document.save()` versioning nothing and
`updateAllReferences()` leaving a stale ref were both observed live). Rules:

- Reuse a kind from `_assert` (`VersionAdvanced`, `ReferencesFresh`, `FileLanded`, ...) before writing
  one; a new kind must capture/verify through `safe()` reads and NEVER mutate.
- Evidence a postcondition reads (size_bytes, stale counts) is folded into the payload via setdefault —
  declare such keys in RETURNS and let the kernel supply them instead of computing them twice.
- A write tool with NO postconditions needs an entry in `tests/test_postconditions_declared.py`'s
  `_EXEMPT` table with a one-line audited reason, prefixed by its class: `inline:` (the verify
  constructs payload fields or error text, so it stays in the handler), `effect:` (the payload's
  claim IS a live read-back of the mutated state), or `gap:` (no effective read-back exists - a
  named defect awaiting a fix, not an accepted state). The table only shrinks; a silent omission
  is not an option.
- Verify-the-effect logic that is intrinsically entangled with payload assembly, per-file loops, or
  compensation/rollback may stay in the handler — name that in the exemption reason.

## Named exemplars — copy the nearest one before inventing a new shape

- **Rich read** (one `<domain>_get` per domain, light default + `include=`): `design_get`, `data_get`,
  `model_inspect`.
- **Honesty** (mutation verified or let to raise, never swallowed): `model_create_component` (rename
  read-back), `cam_delete` (`deleteMe()==False` reported, not swallowed), `mesh_export` (file-exists
  gate after `execute()`), `surface_edit` (commit-or-cancel a `createInput` transaction — never leave
  it open), `data_switch_hub` (assign, then re-read `activeHub` to confirm it actually changed),
  `joint_motion_link` (rolls the created link back with `deleteMe()` if the ratio can't be applied,
  rather than leaving a silent default 1:1 link).
- **Guards**: `doc_open` (a required declare-intent flag refuses the API path for a document class
  that's known to crash Fusion, rather than silently taking the crash-prone path), `data_ops`
  `data_delete_folder` (requires `confirm_name` to match exactly, and previews the full recursive
  blast radius before a non-empty-folder delete).
- **Acquire**: `find_geometry`. **Orient**: `workspace_orient`.
- **Resolver adoption**: `design_delete_occurrence` (resolves its target through
  `_inputs._resolve_occurrence`, which refuses an ambiguous name instead of guessing).

## Module docstrings: short and factual, or none

A tool module may carry a docstring of at most a few lines: what the tool(s) do, plus at most one
load-bearing API gotcha. That is the ceiling — no design-history, migration, or review narrative
anywhere (a lint enforces this), and no before/after essays. API facts beyond that one gotcha live
in the code that uses them (a guard, a typed kind, a point-of-use comment); `sys_get_api_doc` is the
always-current reference for raw signatures. A handler docstring that only restates the wire
`description` should be one line or omitted. Add a SHORT `#` comment only for a non-obvious constraint the code can't show on its own.
A hard-won lesson belongs in your own memory, not the repo.

## One tool per file — and the grandfathered exceptions

Default for a NEW tool: one verb per file (an Edit), or one domain with many slices (a Read, e.g.
`design_get`). The following files predate that rule and bundle several registered tools; they are
grandfathered rather than split retroactively (verified tool counts per file): `doc_lifecycle.py` (7),
`mesh_ops.py` (5), `param_ops.py` (5), `data_ops.py` (4), `design_mode.py` (3),
`assembly_joints_advanced.py` (3), `assembly_transform.py` (3), `view_workspaces.py` (2),
`sys_selection.py` (2). Split one opportunistically when you already have the file open for an
unrelated fix — there is no dedicated split effort.

## `write=` policy for a read-style verb whose actions still mutate state

A poller that only advances an already-authorized async operation (`cam_get_status`) stays
`write="read"` — the write guard on the call that kicked it off already covers what it advances. A
read-style-verb tool whose own actions instead mutate or delete PERSISTENT state must be
`write="write"` instead, via an explicit exemption in `tests/test_tool_naming.py`'s
`_WRITE_VERB_EXEMPT` (not a silent mislabeling). Today's exemptions: `view_inspect` (`save_view`
persists/overwrites a Named View; other actions mutate camera/visibility state), `view_section`
(`clear` deletes user-created section analyses), `sys_request_selection` (clears the user's current
Fusion selection). Adding a new one means adding an entry there with a one-line reason naming the
observed mutation, not just flipping `write=` locally.

## What actually crosses the wire (the surface an agent reads every turn)

| Surface | Source | Build rule |
|---|---|---|
| `name` | `create_simple(name=)` | structural — unique, verb-shaped |
| `annotations` | `write="read"/"write"/"destructive"` | structural — set it honestly (linted) |
| `inputSchema` `type`/`enum` | a `Choice`/kind, or a typed property | structural — prefer a kind so values are machine-checked |
| **`description`** | `TOOL_DESCRIPTION` | **prose** — purpose + next-step pointer ONLY |
| **per-input `description`** | `add_input_property(name, {…"description"})` | **prose** — a long one = a missing kind; type the input |
| **`note` / `error`** in the result | `ok({"note":…})` / `error(...)` | **prose** — state the observed fact, never a guessed cause |

Don't restate the schema (the agent sees the JSON), and never write a tutorial-style comment block
into a description. Agent-facing strings are pure ASCII (see root CLAUDE.md).

## Reads disclose progressively — broad context + pointers, not a flood

A Fusion document has many levels of nested detail (workspace -> component -> sketch -> profiles ->
constraints -> entities -> points; plus parameters, timeline, browser, CAM). A read that flattens a
whole level dumps detail the agent didn't ask for. So a READ tool returns the lightest layer that's
actionable, plus a pointer to the next level of detail — never the deepest level unprompted. Exemplars
to copy: `workspace_orient` (counts + health + `pointers` to the narrow tool per area), `find_geometry`
(light match records + handle, narrowed by `kind`/`radius`/`nearest_to`, capped by `max_results`),
`design_get` / `cam_get` (a light default; `include=[…]` and a scope filter pull one deeper level at a
time).

Build rules for a read:
- **Bound it.** A list that can grow with the model takes a cap (`max_results`/`max_depth`) and reports
  `truncated`. Never emit an unbounded array.
- **Filter before dumping.** Offer the narrowing the agent would want (`near=[x,y]`, `kind=`, a name
  scope) so it pulls the few it needs, not all.
- **Point, don't inline.** When a deeper level exists, return a count + a pointer to the tool that
  drills it — don't fold that depth into this payload. One zoom level per call.
- **Handles are light; records are heavy.** A 200-char handle the agent will *use* is fine; twenty full
  entity records it must skim to find one profile is the waste.

## Reads are RICH — one `<domain>_get` per domain, not a tool per slice

The read layer is one rich read per domain: a light default projection + `include=`/filter to fetch
more (`design_get`, `cam_get`, `doc_get`, `data_get`, `model_inspect`). Build new reads this way; don't
add another narrow `_get_x` tool.

A rich read has three knobs:
- **default projection** = the orientation slice: cheap, essential, often-ephemeral facts ("where am I
  / what changed"). Safe to call blind; never floods.
- **`include=[...]`** = widen to a deeper slice the default omits.
- **`filter`/`scope`** = narrow (`setup=`, `project=`, `near=[x,y]`, `max_depth=`, a `target` handle).

Hard rules:
- The default is always bounded and safe blind. Flags only ADD cost the agent opted into.
- The default's `note`/`pointers` MUST name its own `include=` slices — a flag is invisible unless
  advertised.
- The handler is a thin router over small `_slice_xxx(...)` helpers — one per slice — so the file
  stays readable-whole and each slice is independently testable. No 600-line god-handler.
- Fold only passive structure reads. Async pollers (`cam_get_status`), Edits, and Acquire tools
  (`find_geometry`, `sys_get_selection`) stay separate — an Acquire's output feeds an Edit, so it is
  never a slice of a Disclose read.

## The lints will catch (run `py -3 -m pytest -q` before you call it done)

- `test_tool_naming.py` — the naming schema: verb vocabulary + verb-kind/`write=` agreement.
- `test_write_status_annotations.py` — every registered tool declares a write-status annotation.
- `test_wire_ascii.py` — every description/note/`*_DESCRIPTION` is pure ASCII.
- `test_generated_docs_current.py` — shells `gen_spec.py`/`gen_manifest.py`/`gen_wiring.py --check`;
  fails with the regen command when a doc has gone stale.
- `test_helper_duplication.py` — a denylist: a known shared symbol (`get_cam`, `sanitize`,
  `target_sketch`, …) may only be DEFINED in its home helper module; re-implementing it locally fails.
- `test_no_first_match_resolvers.py` — bans the substring/`.lower() in name.lower()` first-match smell
  across every tool module (an allowlist entry needs a plain-English reason).
- `test_evergreen_no_baggage.py` — bans history/plan-narrative language in comments, docstrings, and
  strings under `commands/mcpServer/` and `tests/` (this file included).

## Verify on a real document before claiming done

Mocks prove logic, not API correctness. After tests pass: `sys_reload_addin` (auto-discovers new
modules), wait for `127.0.0.1:27182/health`, then exercise the handler on a live document. The MCP
client caches the tool list/schema until it reconnects — a changed schema may not show client-side
until then, though the server handler is live.

## Destructive / outward actions

Deleting or overwriting is hard to reverse. Guard it, and when testing live against a model you didn't
create, prefer a throwaway you made (a scratch component/sketch) over the user's real geometry — or
ask first.
