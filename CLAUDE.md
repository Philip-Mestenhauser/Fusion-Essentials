# Working in Fusion-Essentials (MCP server)

This is the constitution for the MCP server under `commands/mcpServer/`: the kinds, the naming
schema, and the honesty contract every tool holds, no matter which file it lives in. Read
[CONTRIBUTING.md](CONTRIBUTING.md) for the full architecture and concepts. For the tool-authoring
recipe (adding a tool, the abstraction catalog, exemplars, the lints), read
[commands/mcpServer/tools/CLAUDE.md](commands/mcpServer/tools/CLAUDE.md) — it loads automatically
when you work in that directory. For writing a test, read [tests/CLAUDE.md](tests/CLAUDE.md). The
code is the source of truth — match the nearest existing tool when in doubt.

## Read vs Edit — the two kinds (and the read shapes)

There are **two real kinds**, split by the one thing that's machine-checkable: does the tool change
state? This is Command-Query Separation, and it *is* the `write=` flag.

| Kind | Does | `write=` | Examples |
|---|---|---|---|
| **Read** | Return information, change nothing. Safe to call blind. | read | `cam_get`, `find_geometry`, `model_measure_between`, `workspace_orient` |
| **Edit** | Act: mutate the model/data, or run an async operation. Gets the write guard; must verify its effect. | write / destructive | `model_extrude`, `joint_edit`, `doc_save`, `cam_edit_tools` |

Within **Read** there are three *shapes* — same kind, different job (they help you pick the tool's form,
not its permission):
- **Orient** (`orient`) — one cheap CROSS-domain read to situate ("where am I / what's broken / where
  next"). Call first, never floods. `workspace_orient`.
- **Disclose** (`get`) — progressively disclose ONE domain's structure: light default + `include=`/scope.
  The rich-read shape. `cam_get`, `design_get`, `doc_get`, `data_get`.
- **Acquire** (`find`/`measure`/`probe`/`inspect`/`compare`/`screenshot`/`section`/`compute`/`request`) —
  a read whose OUTPUT feeds an Edit: a handle, a measurement, an image, a user-pick, a diff. This is the
  seam the handle architecture runs on (`find_geometry` returns a handle, `joint_at_geometry` consumes it
  — see `_inputs.py`). Its one architectural rule: **an Acquire stays a SEPARATE tool from a
  Disclose read** — it has its own query params + returns handles, so don't fold it into a rich read.

## Naming schema

Every tool is `<domain>_<verb>[_<noun>]`, `<verb>` from a closed vocabulary, and **the verb's kind must
agree with `write=`** — a read-verb (`get`/`find`/`probe`/…) is read-only; an edit-verb mutates. So the
name *is* the type: `cam_get` reads, `model_compute_holder` acquires, `cam_edit_tools` writes; a name
that disagrees with `write=` is a mislabeled tool. Enforced by `test_tool_naming.py` (also the source of
the verb vocabulary). The exemptions (a poller like `cam_get_status`; a read-verb tool that still mutates,
e.g. `view_section`) and Edit packaging (`action=` dispatch, one-verb-per-file) live in
[commands/mcpServer/tools/CLAUDE.md](commands/mcpServer/tools/CLAUDE.md).

## Honesty contract (the rule that matters most)

- Use `ok(...)` / `error(...)` from `_common`. Wrap per-field READS in `safe(getter, default)` so one
  bad field doesn't sink the call — but let an actual MUTATION raise. A swallowed mutation that reports
  success is the cardinal sin: a failed delete/edit must return `isError`, never a false `ok`.
- After a write, verify the effect and report it. If the API returns success but nothing changed
  (it happens), treat that as failure. Surface partial success explicitly (what was done, what wasn't).
- A static read of `adsk.*` code cannot tell you what the API actually does. Before "fixing" a
  geometry/matrix/API bug you spotted by reading, reproduce it live (`sys_execute_script` against a
  scratch doc, or drive the tool and read the result back) — a plausible-looking bug is often correct
  code whose API contract you misread, and the "fix" is the regression. Confirm the defect exists, then
  confirm the fix, both against a live document.
- Guard inputs and report *why* a precondition failed, naming the offending value. Resolve references
  (occurrences, geometry) through the typed kinds in `_inputs.py` — they refuse ambiguity instead of
  grabbing the wrong instance; don't hand-roll a `name: str`.
- Resolving a name yourself: whether first-match is a bug depends on whether the name space is unique.
  A **scope-unique** name (a CAM setup/operation) resolves correctly by case-insensitive EXACT match,
  returning the available names on a miss (`_cam_common.find_operation`). A **non-unique** name (two
  sub-assemblies each holding a "Bolt:1") must **refuse** the ambiguity, never return the first
  substring/`.find()`/`[0]` hit — that silently targets the wrong entity. `test_no_first_match_resolvers`
  catches the always-wrong shapes (substring, indexed-first), but the unique-vs-not judgment is yours.

## Input kinds — use one BEFORE hand-rolling a `name`/`index` reference

To reference existing geometry/structure (face, edge, body, plane, axis, profile, occurrence, length,
fixed choice), use a typed kind from [`_inputs.py`](commands/mcpServer/tools/_inputs.py) — they refuse
ambiguity instead of grabbing the wrong instance; don't hand-roll a `name`/`index` resolver. Wire one
with `tool.add_input_property(*kind.as_property())`, resolve via `_inputs.resolve_inputs(...)`; if a
kind is close but missing a selector, extend the kind, not one tool's local copy. The full catalog —
every kind, plus the shared helpers to reuse — is the generated map in
[commands/mcpServer/tools/CLAUDE.md](commands/mcpServer/tools/CLAUDE.md), loaded when you author a tool.

<!-- BEGIN GENERATED FAMILIES (py -3 tests/gen_manifest.py) -->
**Tool families** (139 tools — `sys_find_tool <kw>` to search, `TOOL_MANIFEST.md` for the full list): `model`(25) `surface`(10) `mesh`(9) `sketch`(9) `cam`(19) `assembly`(7) `joint`(7) `design`(8) `doc`(12) `data`(8) `drawing`(3) `param`(5) `view`(6) `find`(1) `workspace`(1) `appearance`(1) `save`(1) `sys`(7)
<!-- END GENERATED FAMILIES -->

## Tool descriptions and agent-facing strings — pure ASCII, verified claims only

A tool's **description** + the `note`/`error` it returns are the ONLY thing a connected agent knows
about it, and they cross the wire JSON-serialized with `ensure_ascii` — so keep them pure ASCII (` - `
not `—`, `...` not `…`, `->` not `→`, `deg` not `°`), enforced by `test_wire_ascii.py`. Every claim about
an input's legal values must be backed by something that fails when it's false (a `Choice`/enum, a typed
kind's `resolve()`, a guard) — if you can't back it, type the input instead of asserting it. Full rule
(what crosses the wire, the docstring policy, the read-shape build rules):
[commands/mcpServer/tools/CLAUDE.md](commands/mcpServer/tools/CLAUDE.md).
