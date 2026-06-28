# Reading Fusion state — the essential reads and their blind spots

Fusion is many environments glued together (CAD modelling, assemblies, parametric timeline, CAM,
the cloud data model, sim, electrical). Reading an *entire* document is unreasonable at scale, so a
good pair-programmer agent works by **progressive disclosure**: run a cheap state read first, and
only zoom in (isolate/screenshot/deep-read) on the part that read flags as ambiguous.

This doc is the map for that: for each environment, **the one read tool to start with, what it
reliably tells you, and the BLIND SPOTS** — the places where a read returns something that looks
authoritative but isn't, so you draw a silent wrong conclusion. The blind spots are the whole point:
they are where agents go wrong without any error to warn them.

> Rule of thumb: **read state before you reason; reason before you act; verify with numbers, not a
> screenshot.** A screenshot of an assembly is the LEAST reliable input (parts overlap at the
> origin, the active component greys the rest out, depth is ambiguous) — reach for it last, and only
> on a single isolated, oriented component.

---

## 0. Always start here — orient

| Read | Tells you | Then branch to |
|---|---|---|
| `sys_get_session` | active document, **workspace**, **product type**, units, root name, occurrence count | the environment-specific read below |

The active **workspace/product** decides which environment you're in and therefore which deep read
is meaningful. Don't assume — a doc can hold Design + CAM + sim products at once.

---

## 1. Assembly / components — `assembly_probe`

**Reliably tells you:** every occurrence's world position (origin + bbox center/size), its ground
flags (`grounded` / `ground_to_parent`), and the joints it participates in; plus a joint list (type,
DOF, the two occurrences each connects) and which occurrences are grounded.

**Use it instead of a screenshot** to answer "is the right part fixed?", "did the joint connect the
right two parts?", "where is each piston?".

**BLIND SPOTS:**
- **Grounding is a two-flag trap.** `grounded` (pinned in space) and `ground_to_parent` (the default
  rigid-to-parent lock) are DIFFERENT. A common failure: the intended fixed frame (e.g. an engine
  block) has `grounded=false` while another part has `ground_to_parent=true` — so when you drive a
  joint, the WRONG part moves. Verify `grounded_occurrences` lists your fixed frame, and that moving
  parts have `ground_to_parent=false`. (Observed live: a whole engine with `grounded:[]`.)
- **The INVISIBLE first-component trap (no tool call causes it).** A Fusion USER PREFERENCE
  ("ground to parent", on by default for many users) implicitly sets `ground_to_parent=true` on the
  FIRST component created in a document — and only the first. So if you build the crankshaft first
  and the block second, the *crankshaft* is silently locked-to-parent and the block is free → drive
  a joint and the block moves while the crank stays put. NOTHING in your tool calls reveals this;
  only `assembly_probe` does. **After creating components, ALWAYS probe ground flags and explicitly
  set them** — free everything (`ground_to_parent=false`) then `grounded=true` on your one intended
  fixed frame — rather than trusting the implicit default. (Hit twice live: both the V4 and the
  inline-4 had the first-created part auto-grounded.)
- **Occurrence origin ≠ part geometry center.** `origin` is the occurrence transform; `bbox_center`
  is where the geometry actually sits. They differ whenever a part was modelled off its own origin.
- `assembly_probe` reports joints but not their *current driven value* — to see motion, drive with
  `assembly_move` + `assembly_capture_position` and re-probe positions.

`design_get_tree` complements it (reference/x-ref resolution, child structure) but does NOT give
position or ground flags — use `assembly_probe` for kinematic state.

---

## 2. Parametric build / timeline — `design_get_timeline` + `design_get_timeline_health`

**Reliably tells you:** the ordered features/sketches/joints that build the design, each one's type
and whether it's suppressed/rolled-back/grouped, plus an error/warning health rollup. This is how
you understand HOW something was built and spot suppressed alternate branches.

**BLIND SPOTS:**
- **Suppressed ≠ deleted.** Templates carry suppressed alternate operations (e.g. "old adaptive if
  3+2 causes problems"). `include_suppressed` defaults true — don't mistake a suppressed branch for
  the active one.
- **Direct-modeling designs have NO timeline.** If `designType` is direct, the timeline is empty;
  that's not an error. (And direct designs reject parametric construction datums — see `model_construction`.)
- **Health is about features, not fit.** A healthy timeline can still produce a kinematically wrong
  assembly — health ≠ correct.

---

## 3. Sketches — `sketch_get`

**Reliably tells you:** WITHOUT a name, a summary list (counts) of every sketch; WITH a name, one
sketch's full structure — entities by id, constraints (and the entity ids they link), dimensions
(with `driving`), and `is_fully_constrained`.

**BLIND SPOTS:**
- **`is_fully_constrained` is the ONLY DOF signal the API exposes — and it's sketch-scoped.** There
  is no DOF *count* and no over-constrained flag from the API. `false` means free DOF remain (still
  movable/drivable); it does NOT tell you how many, or whether something is over-constrained. For
  over-constrained diagnosis, the in-product sketch view (which colors the geometry) is authoritative.
- A `driving=false` dimension only MEASURES; it does not lock geometry. Don't read a reference
  dimension as a constraint.

---

## 4. CAM / Manufacture — `cam_get_setups`, then `cam_get_operations`

**Reliably tells you:** setups (machine, selected models/fixtures/stock, op counts) and, per setup,
each operation's name, tool, and state.

**BLIND SPOTS (the big one):**
- **Operation validity / `is_out_of_date` is STALE outside the Manufacture workspace.** The CAM model
  does not re-evaluate against changed geometry (e.g. a freshly inserted/swapped part) until
  Manufacture is active. From Design, an op can read `valid` / up-to-date when it is really stale.
  **Switch to Manufacture (`view_switch_workspace`) before trusting validity**, and after swapping a
  part treat ops as out-of-date (`cam_generate` with `skip_valid=false`, or enter Manufacture first).
  Names and tools read fine anywhere; only the *validity* is the trap.
- **Toolpaths render only in Manufacture.** `cam_show_toolpath` + a screenshot show nothing from
  Design.
- `cam_generate` returns immediately with a handle; it does NOT block. Poll `cam_get_status(handle)`
  (each poll PUMPS the generation forward) until `completed=true`. A first `operations_to_generate: 0`
  is the stale-count quirk, not "nothing to do".

---

## 5. Cloud data model — `data_list_projects` / `data_list_files`; `doc_list_open`

**Reliably tells you:** projects, files (lineage id/version/web URL), and the documents loaded in the
session.

**BLIND SPOTS:**
- **`app.documents` (what `doc_list_open` reports) is a SUPERSET of the visible tabs.** Opening an
  assembly loads its referenced components as real Documents too (`isVisible=true` means LOADED, not
  tabbed). Be careful before any close-all.
- **Duplicating a multi-reference CAM template: use open-then-`doc_save_as`, never `doc_copy`.**
  `doc_copy` (`DataFile.copy`) reconciles a closed template's whole reference graph server-side and
  destabilises the session; `doc_save_as` (`Document.saveAs`) writes the already-loaded, already-
  resolved open doc safely and leaves the copy active. A settled `doc_open(force_api_open=true)` of
  such a doc is fine on its own — the hazard was always the cold copy, not the open. (See
  `commands/mcpServer/tools` and the insert-into-template skill's reference.md for the full rationale.)
- **Saves/opens/copies are ASYNC.** `doc_save_as`/`doc_copy` return before the cloud settles
  (`document_id` is often null immediately). Confirm with a follow-up `doc_get_active_id` /
  `data_list_files` read — never assume completion.
- An **unsaved** document has no `document_id` (`has_data_file=false`) — it must be saved before it
  can be addressed by URN or inserted as a reference.

---

## 6. Measurement & datums — `model_measure_bbox`, `sys_get_selection`

**BLIND SPOTS:**
- **Bounding-box center ≠ the modelling origin (0,0,0).** The modelling origin is arbitrary; the
  bbox center is the geometric center. For "center of the part" use the bbox center (and a
  part-space frame, not world, when orientation matters — pass `frame=<joint origin>`).
- **`sys_get_selection` direction can be null** (e.g. a sphere face has no single normal). Validate
  `direction` is non-null before using it as a machining/joint axis.

---

## Building multi-part assemblies — two traps that NUMBERS won't catch

These bit a live engine build hard; both are invisible to `assembly_probe` (a malformed body has the
same bounding box as a clean one) and only an isolated multi-angle LOOK reveals them.

- **Cut operations bleed through OVERLAPPING bodies.** An extrude-`cut` (and combine-`cut`) acts on
  whatever solid geometry occupies the cut volume — not just "the part you think you're editing." If
  you build several components STACKED at the origin (the default — every new component starts there),
  a bore cut in one part can carve a gouge through another part sitting at the same coordinates. (Live
  result: cutting rod/block bores gouged the crankshaft sitting overlapped at the origin.)
  **PREVENTION:** build each part **isolated and ACTIVE** — `model_create_component(activate=true)`,
  and `view_inspect(isolate)` it — so the cut is scoped to that component AND you get a clean view.
  Alternatively build parts spaced FAR apart (hundreds of mm) and only bring them together by joint.
- **Raw-transform placement does NOT survive the joint solve.** Positioning free occurrences with
  `assembly_move` / a transform, then creating a joint, makes the joint solver **snap the still-free
  parts back to their component origin** (they collapse to (0,0,0)). Pre-positioning is throwaway.
  **PREVENTION:** let JOINTS define position — constrain each part by its joints (revolute to a real
  datum/pin, slider in a bore), don't pre-place it. Build construction-point datums at the real
  connection points (`model_construction`, parametric designs only) and joint to those.

> **Numbers verify POSITION / grounding / joint-wiring. Isolated multi-angle images verify SHAPE.**
> After shaping a part (extrude/revolve/cut/fillet), `view_inspect(isolate)` it and image 3–4
> quadrants to confirm the silhouette is right — a gouge, a merged bore, or a failed cut is invisible
> in the bbox but obvious in the picture. Do BOTH; neither alone is enough.

## Verify HEALTH before structure (the user's first signal)

A feature or joint can be created and WIRED correctly yet **fail to compute** — the yellow "Compute
Failed" a user sees in the timeline. That is the FIRST thing a real user notices something is wrong,
*before* any functional test. An agent that only checks structure (joint count, types, positions,
wiring) will happily report a BROKEN assembly as fine. (Lived it: a slider joint with a mis-aligned
axis over-constrained the assembly — `healthState=1`, "conflicts with assembly relationships" — while
the structural probe said everything was great.)

So **check health FIRST**:
- `assembly_probe` reports `is_healthy`, `broken_joints`, `timeline_problems`, and a per-joint
  `healthy` flag — read those before reasoning about positions. `is_healthy=false` means stop and
  fix the named feature/joint; don't proceed to drive/test it.
- `design_get_timeline_health` is the design-wide feature error/warning rollup.
- Each entity exposes `healthState` (0 = healthy) + `errorOrWarningMessage` (the API path).

Rule: a created joint that returns an object is NOT a working joint. Probe its health. A common cause
of a "successful" but broken joint: the **motion axis doesn't match the geometry's real axis** (e.g.
passing world-x for a pin whose axis isn't world-x), which over-constrains the loop.

## The verify discipline (applies everywhere)

1. **Numbers over pixels.** After any structural change (joint, ground, insert, move), re-read the
   relevant state tool (`assembly_probe`, `cam_get_operations`, `sketch_get`) and check the numbers.
   Do not conclude "it worked" from a screenshot.
2. **Isolated screenshots only.** When you do need to see geometry, `view_inspect(snapshot)` →
   `isolate` the one component → `orient` → `view_screenshot` → `restore`. Never reason from the
   translucent active-component soup or origin-stacked parts. Use `view_screenshot_multi` (labelled
   ortho views) over a single isometric when judging 3D layout.
3. **Section to see inside.** `view_section(cut, ...)` reveals bores/cavities/nesting a solid view
   hides; `clear` undoes it.
4. **Probe-after-joint.** Specifically: after grounding/jointing an assembly, run `assembly_probe`
   and confirm the fixed frame is grounded and the intended part is free BEFORE driving motion.
