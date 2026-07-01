# Working in Fusion-Essentials (MCP server)

Agent guide for extending the MCP server under `commands/mcpServer/`. Read
[CONTRIBUTING.md](CONTRIBUTING.md) for the full architecture; this file is the short list of
expectations that are easy to violate. The code is the source of truth — match the nearest existing
tool when in doubt.

## Adding a tool

- One tool per `tools/<name>.py`, exposing `handler(...)`, a description, a `Tool`, an `Item`, and
  `register_tool()`. Registration is auto-discovered (a pkgutil sweep) — **just drop the file in**; do
  not edit `tools/__init__.py` or `entry.py`. `_`-prefixed modules are shared helpers, never tools.
  (An **Edit** tool is one verb per file; a **Read** is one domain per file with many slices — see
  "Read vs Edit" below.)
- **Reuse before you write.** The shared `_`-helpers carry the conventions (listed in the generated map
  below). Before adding a helper/resolver, **grep those modules**; before adding a tool/input, use
  `sys_find_tool <kw>`. Don't duplicate what exists — extend it.
- Set `write=` on the `Item` honestly: `"read"`, `"write"`, or `"destructive"`. It is linted.
- Run on the main thread (the default) — anything touching `adsk.*` off the main thread can crash
  Fusion. Never block: no `sleep`, polling, or synchronous network in a handler.

## Read vs Edit — the two kinds (and the read shapes)

There are **two real kinds**, split by the one thing that's machine-checkable: does the tool change
state? This is Command-Query Separation, and it *is* the `write=` flag.

| Kind | Does | `write=` | Examples |
|---|---|---|---|
| **Read** | Return information, change nothing. Safe to call blind. | read | `cam_get`, `find_geometry`, `model_measure_between`, `workspace_orient` |
| **Edit** | Act: mutate the model/data, or run an async operation. Gets the write guard; must verify its effect. | write / destructive | `model_extrude`, `joint_create`, `doc_save_as`, `cam_edit_tools` |

Within **Read** there are three *shapes* — same kind, different job (they help you pick the tool's form,
not its permission):
- **Orient** (`orient`) — one cheap CROSS-domain read to situate ("where am I / what's broken / where
  next"). Call first, never floods. `workspace_orient`.
- **Disclose** (`get`) — progressively disclose ONE domain's structure: light default + `include=`/scope.
  The rich-read shape. `cam_get`, `design_get`, `doc_get`, `data_get`.
- **Acquire** (`find`/`measure`/`probe`/`inspect`/`compare`/`screenshot`/`section`/`compute`/`request`) —
  a read whose OUTPUT feeds an Edit: a handle, a measurement, an image, a user-pick, a diff. This is the
  seam the handle architecture runs on (`find_geometry` returns a handle, `joint_at_geometry` consumes it
  — see `tools/_inputs.py`). Its one architectural rule: **an Acquire stays a SEPARATE tool from a
  Disclose read** — it has its own query params + returns handles, so don't fold it into a rich read.

**Naming schema (lint-enforced by `tests/test_tool_naming.py`):** every tool is `<domain>_<verb>[_<noun>]`,
`<verb>` from a closed vocabulary, and **the verb's kind must agree with `write=`** — a read-verb
(`get`/`find`/`probe`/…) MUST be read-only; an edit-verb MUST NOT be. So the name *is* the type: `cam_get`
reads, `model_compute_holder` acquires (no mutation), `cam_edit_tools` writes. A name that disagrees with
`write=` is a mislabeled tool — fix whichever is wrong. Adding a new verb means extending the vocabulary in
that test deliberately, not sneaking a synonym. (`cam_generate` is an async Edit that returns `write="read"`
only because its own call has no immediate side effect — the poll does; the write guard still applies to
what it kicks off.)

- **Edit packaging.** Default to one verb per file. An `action=`-dispatched tool (`cam_edit_tools`,
  `data_switch_hub`) is a packaging style for an Edit family whose verbs share a target/resolver — not a
  mandate to make every verb action-dispatched.

## Ground every `adsk.*` call in the real API

The Fusion API is niche and easy to get wrong, and a mocked test cannot catch a wrong signature. So:

- Confirm signatures/properties against the live API (`sys_get_api_doc`) **before** writing them. Do
  not guess a property exists — verify it. (Real example: deleting an occurrence is
  `Occurrence.deleteMe()`; there is no "is this a pattern child" property — detect that case from the
  `deleteMe()==False` result instead.)
- Record non-obvious API facts in [docs/fusion-api-notes.md](docs/fusion-api-notes.md), not in a tool
  description.

## Honesty contract (the rule that matters most)

- Use `ok(...)` / `error(...)` from `_common`. Wrap per-field READS in `safe(getter, default)` so one
  bad field doesn't sink the call — but let an actual MUTATION raise. A swallowed mutation that reports
  success is the cardinal sin: a failed delete/edit must return `isError`, never a false `ok`.
- After a write, verify the effect and report it. If the API returns success but nothing changed
  (it happens), treat that as failure. Surface partial success explicitly (what was done, what wasn't).
- Guard inputs and report *why* a precondition failed, naming the offending value. Resolve references
  (occurrences, geometry) through the typed kinds in `_inputs.py` — they refuse ambiguity instead of
  grabbing the wrong instance; don't hand-roll a `name: str`.

## Input kinds — use one BEFORE hand-rolling a `name`/`index` reference

To reference existing geometry/structure (face, edge, body, plane, axis, profile, occurrence, length,
fixed choice), use a typed kind from [`tools/_inputs.py`](commands/mcpServer/tools/_inputs.py); don't
write a `name`/`index` resolver. Map below (generated by `py -3 tests/gen_manifest.py` — don't edit
between the markers):

<!-- BEGIN GENERATED MAP (py -3 tests/gen_manifest.py) -->
| Kind | References (use this — don't hand-roll a name/index) |
|---|---|
| `AxisRef` | a direction: world x/y/z OR a straight-edge handle |
| `BodyRef` | a body by handle (precise) or name; kind=solid/surface/mesh |
| `BodyRefList` | several bodies (handles or names) |
| `Choice` | one of a fixed set -> JSON enum |
| `Distance` | a length in display units (pair with one UnitField) |
| `EdgeLoopRef` | a closed/open edge-loop boundary from edge handles |
| `GeometryHandle` | one face/edge/vertex by find_geometry handle (require=face/edge/...), not a coordinate |
| `GeometryHandleList` | several faces/edges by handles (fillet/drill THESE) |
| `NameRef` | a plain by-name ref; prefer a handle/fullPathName kind if one exists |
| `OccurrenceRef` | an assembly occurrence by fullPathName (refuses ambiguous names) |
| `OccurrenceRefList` | several occurrences (fullPathNames/names) |
| `PlaneRef` | a plane: xy/xz/yz alias, construction-plane name, OR planar-face handle |
| `ProfileRef` | a sketch profile by stable handle, not sketch_name+profile_index |
| `ProfileRefList` | an ORDERED list of profiles (loft - order is load-bearing) |
| `TargetRef` | a thing to measure/colour: handle (body/face/mesh) OR occurrence/component/body name; ''=whole design |
| `UnitField` | the 'units' selector (mm/cm/in enum) for a Distance |

**Tool families** (121 tools — `sys_find_tool <kw>` to search, `tests/MANIFEST.md` for the full list): `model`(19) `surface`(7) `mesh`(9) `sketch`(7) `cam`(18) `assembly`(7) `joint`(7) `design`(8) `doc`(10) `data`(7) `param`(5) `view`(6) `find`(1) `workspace`(1) `appearance`(1) `save`(1) `sys`(7)

**Shared helpers** (reuse/extend — grep before writing a resolver): `_common` (ok/error/safe, design/target_component, resolve_sketch, scale - the response+resolve substrate); `_inputs` (the typed reference kinds - see the kinds table above; resolve_inputs/apply_to_tool); `_outputs` (RETURNS kinds (ReturnsHandle/Urn/Name/Value) - declare a tool's stable outputs once); `_holder` (holder geometry: get_axis, get_tool_profile, build_holder_data, get_tooling_libraries); `_data_common` (cloud data-model helpers shared by data_model_ops + doc_lifecycle (hub/project/folder/URN))
<!-- END GENERATED MAP -->

Wire a kind with `tool.add_input_property(*kind.as_property())`; resolve via `_inputs.resolve_inputs(...)`.
If a kind is close but missing a selector, **extend the kind**, not one tool's local copy. Deeper:
`sys_find_tool <kw>` (live search) or `tests/MANIFEST.md` (full per-tool inventory).

## Tool descriptions — verified claims only (the surface an agent reads every turn)

A tool's **description** + the **`note`/`pointers`** it returns are the ONLY thing a connected agent
knows about the tool — it can't see this repo. So that prose is a contract, and its failure mode is a
confident claim that nothing verifies.

**The rule:** every claim about an input's legal values or behavior must be **backed by something that
fails when it's false** — a schema `enum`/`Choice`, a typed kind's `resolve()`/`validate()`, or a guard.
If you can't back it, you don't know it's true — so don't assert it; type the input instead (prose
explaining an input's values is a missed `Choice`/kind). A description then holds only what the schema
*can't*: **what the tool is for, and the next-step pointer** ("pair with view_screenshot"). Don't
restate the schema (the agent sees the JSON), and never write `CONTRACT:` / `OPEN QUESTIONS:` tutorials.

**What actually crosses the wire** — a connected agent receives exactly these per tool (from `to_dict()`),
plus the runtime result. Three are free prose (the leak surfaces); the rest are structural and self-true:

| Surface | Source | Build rule |
|---|---|---|
| `name` | `create_simple(name=)` | structural — unique, verb-shaped |
| `annotations` | `write="read"/"write"/"destructive"` | structural — set it honestly (linted) |
| `inputSchema` `type`/`enum` | a `Choice`/kind, or a typed property | structural — **prefer a kind so values are machine-checked** |
| **`description`** | `TOOL_DESCRIPTION` | **prose** — purpose + next-step pointer ONLY |
| **per-input `description`** | `add_input_property(name, {…"description"})` | **prose** — a long one = a missing kind; type the input |
| **`note` / `error`** in the result | `ok({"note":…})` / `error(...)` | **prose** — state the **observed** fact, never a guessed CAUSE ("no body at point", not "handle is STALE") |

- **Default to NO docstring.** A tool's purpose is its (wire-facing) `description`; the code should
  read self-evidently. Add a SHORT `#` comment only for a non-obvious API gotcha or a load-bearing
  *why* the code can't show on its own. Do not write "why this exists" essays, design-history, or
  before/after narrative anywhere — not even in free docstrings/comments. A hard-won lesson belongs in
  your own memory, not the repo.
- **Agent-facing strings are pure ASCII.** A description/`note`/`error` is JSON-serialized with
  `ensure_ascii`, so a `—`/`…`/`•`/`°` becomes a 6-char `\uXXXX` blob on the wire — noise that loads
  every turn and reads worse for the model. Write ` - ` not `—`, `...` not `…`, `->` not `→`, `deg` not
  `°`. (Box-drawing `─` dividers in `#`-comments are fine — they never cross the wire.)

## Reads disclose progressively — broad context + pointers, not a flood

A Fusion document has **many levels of nested detail** (workspace → component → sketch → profiles →
constraints → entities → points; plus parameters, timeline, browser, CAM). A read that flattens a whole
level dumps detail the agent didn't ask for — and the per-item JSON (every line/point/constraint as its
own record) is the heavy part, not any single handle. So a READ tool returns the **lightest layer that's
actionable, plus a pointer to the next level of detail** — never the deepest level unprompted. The
exemplars to copy: `workspace_orient` (counts + health + `pointers` to the narrow tool per area),
`find_geometry` (light match records + handle, narrowed by `kind`/`radius`/`nearest_to`, capped by
`max_results`), `design_get` / `cam_get` (a light default; `include=[…]` and a scope filter pull one
deeper level at a time).

Build rules for a read:
- **Bound it.** A list that can grow with the model takes a cap (`max_results`/`max_depth`) and reports
  `truncated`. Never emit an unbounded array.
- **Filter before dumping.** Offer the narrowing the agent would want (`near=[x,y]`, `kind=`, a name
  scope) so it pulls the few it needs, not all.
- **Point, don't inline.** When a deeper level exists (a sketch's full entity/constraint X-ray, a
  component's bodies), return a count + a `pointer` to the tool that drills it — don't fold that depth
  into this payload. One zoom level per call; each names the next.
- **Handles are light; records are heavy.** A 200-char handle the agent will *use* is fine; twenty
  full entity records it must skim to find one profile is the waste.

## Reads are RICH — one `<domain>_get` per domain, not a tool per slice (the target architecture)

The read layer is **one rich read per domain** — GraphQL-shaped: a light default projection +
`include=`/filter to fetch more (`design_get`, `cam_get`, `doc_get`, `data_get`, `model_inspect`).
(This is the **Read** kind — the `<domain>_get` verb. The default projection situates the agent WITHIN
that domain; the **Orient** kind — `workspace_orient` — situates ACROSS domains. Don't conflate them.)
**Build new reads this way; don't add another narrow `_get_x` tool.** A good exemplar is `sketch_get`
(default = light overview + `profiles`; `include_entities=true` = the heavy X-ray).

A rich read has three knobs:
- **default projection** = the *orientation slice*: cheap, essential, often-ephemeral facts ("where am
  I / what changed" — active target, health, counts, the major pieces). Safe to call blind; never floods.
- **`include=[...]`** = widen to a deeper slice the default omits (`include=timeline`, `include_entities`).
- **`filter`/`scope`** = narrow (`setup=`, `project=`, `near=[x,y]`, `max_depth=`, a `target` handle).

Hard rules:
- The default is **always bounded and safe blind**. Flags only ADD cost the agent opted into.
- The default's `note`/`pointers` MUST **name its own `include=` slices** — a flag is invisible unless
  advertised (the `sketch_get` note says "call again with include_entities=true"). This is load-bearing:
  un-advertised, a fold *hides* capability.
- The handler is a **thin router** over small `_slice_xxx(...)` helpers — one per slice — so the file
  stays readable-whole and each slice is independently testable. No 600-line god-handler.
- Fold only **passive structure reads** (the Read kind). Async pollers (`cam_get_status`), Edits, and
  **Acquire** tools (`find_geometry`, `sys_get_selection`) stay separate — an Acquire's output feeds an Edit,
  so it is never a slice of a Disclose read (see "Read vs Edit").
- Migrate **build-alongside-then-delete**: ship the rich read, live-validate, THEN delete the absorbed
  narrow tools (redirect prose refs, delete tool+test, regenerate manifest/spec/map).

## Tests (write them with the tool, before moving on)

One `tests/test_<tool>.py` beside the suite. `conftest.load_tool("<tool>")` loads a module in isolation
against a mocked `adsk`; assert on the returned JSON and the exact `adsk.*` calls the fake captured.
Cover the guards (bad units, no active design, missing/ambiguous target), not just the happy path.

**Copy a canonical example; set state with `monkeypatch`/a `@pytest.fixture`, never an imperative
`mod.app = …` poke.** [tests/README.md](tests/README.md) "The canonical examples" names the three to
copy: `test_design_get.py` (a rich read - stub the `_slice_*` seams, assert the router composition),
`test_model_inspect.py` (a logic tool - shared `make_design`/`install` fakes), `test_quoting.py` (a pure
function). conftest ships `make_design(...)` / `MakeComp` / `MakeDesign` and `install(mod, design)` -
`install` wires BOTH seams a tool reads through (its own `_common` AND `_inputs._common`, plus
`adsk.fusion.Design.cast`). A `monkeypatch`/fixture setup undoes itself after the test, so a leaked seam
is structurally impossible. The older tests roll a **bespoke `_install()` called imperatively** + their
own `Fake*` hierarchy - that leaks into the next test and is why conftest carries a big snapshot/restore
fixture to compensate. Do NOT copy that. If `MakeComp`/`MakeDesign` lack a surface your tool needs,
**extend the shared fake**, don't fork a bespoke one (the "extend the kind" rule, applied to test fakes).

When the shared fakes genuinely don't fit (a tool needs an object shape `MakeComp` can't model): a
handler that branches on `type(x).__name__` needs a fake whose class is literally named that
(`type("RealClassName", (), {...})()`, not `FakeFoo`); and if you must patch seams by hand, patch BOTH
`_common.design()` AND `_inputs._common.design()` to the SAME design (the dual-seam trap), inside a
fixture so it's torn down.

`py -3 -m pytest -q` must stay green (and stay green under `-p randomly` — order-independence is the
bar); run `py -3 tests/gen_spec.py` after adding/renaming tests.

## Verify on a real document before claiming done

Mocks prove logic, not API correctness. After tests pass: `sys_reload_addin` (auto-discovers new
modules), wait for `127.0.0.1:27182/health`, then exercise the handler on a live document. The MCP
client caches the tool list/schema until it reconnects — a changed schema may not show client-side
until then, though the server handler is live.

## Destructive / outward actions

Deleting or overwriting is hard to reverse. Guard it, and when testing live against a model you didn't
create, prefer a throwaway you made (a scratch component/sketch) over the user's real geometry — or
ask first.
