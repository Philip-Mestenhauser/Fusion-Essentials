# Fusion API notes for MCP tool authors

Behavior the MCP tools depend on that the official Fusion API reference does not spell out. Ground
every `adsk.*` call in the official reference first; use these notes for the gotchas it omits.

See [../CONTRIBUTING.md](../CONTRIBUTING.md) for the add-in/tool conventions and the
main-thread rules, and `commands/mcpServer/README.md` for the user-facing docs.

## Tool authoring conventions

The tools follow a deliberately uniform pattern — match it when adding one so any tool file is
predictable from any other.

- **`run_on_main_thread=True` for anything touching `adsk.*`.** This is the default and should
  essentially always be set. The server marshals the handler onto Fusion's main thread via
  TaskManager; calling the Fusion API off the main thread can crash Fusion.
- **Never block in a handler.** No `time.sleep`/polling loops, no synchronous network. The
  handler runs on the UI thread. If an operation is async (e.g. `documents.open`), report
  status honestly and tell the agent to confirm with another tool rather than waiting.
- **Result shape is always** `{"content": [...], "isError": bool}`. Text payloads go in a
  `{"type":"text","text": json.dumps(...)}` block; images in
  `{"type":"image","data": b64,"mimeType":"image/png"}`. Use the `ok` / `error` helpers from
  `_common`.
- **Guard every individual `adsk.*` access.** Cloud/CAM/data calls fail in surprising ways. Wrap
  per-field reads in `safe(getter, default)` (from `_common`) so one bad field does not fail the whole
  call — but only for PROBING; let an actual mutation raise (a swallowed mutation is a false success).
  Cap enumeration of large collections and flag truncation.
- **Accept name OR id.** Read tools that target a thing (project, setup, workspace) should take
  both a human name and the precise id, and on no match return an error listing what IS
  available — forgiving for an agent that only has a name.
- **Make structure visible, then address by path.** Folder/data tools accept nested paths
  (`Fixtures/Vises`, split on `/` or `\`); `data_list_folders` reveals the tree; `data_list_files`
  stamps each file with its `folder_path`. Creation tools do `mkdir -p` (auto-create missing
  parents); when a required path is missing, the error lists the folders that DO exist at the
  failure point. Duplicate guards are scoped to the resolved path, not the whole tree.
- **Write `TOOL_DESCRIPTION` for an LLM.** State when to use it, the inputs, and whether it is
  read-only or has side effects — the description is the only thing the calling agent sees, so
  it is the tool's real API. Be explicit about gotchas (e.g. "async", "works without switching
  workspaces").

## CAM templates and the container pattern

Modern Fusion CAM templates select **Component Containers** as a setup's model/fixture/stock,
not raw geometry, so the setup keeps its selection when contents are swapped. The real
workholding (clamping unit + vise, the machined part, a WCS cube) lives *inside* the container
as **external references**. Therefore:

- To see what a setup actually holds, descend into the selected occurrence's
  `childOccurrences` — a top-level read shows only the container. `design_get_tree` does this
  (depth-bounded, resolving each X-ref to its source UID). `cam_get_references` only resolves
  top-level refs.
- A lone cube referenced by a setup is very likely a **WCS-defining component**, not a
  placeholder — descend and report rather than assuming.
- The tools that cover the common machinist flow without `sys_execute_script`:
  `design_get_tree`, `cam_activate_setup` (+ `view_screenshot` to review), `sys_get_tool_list`,
  `cam_get_time`, `cam_get_setups` / `cam_get_operations`. Reach for `sys_execute_script`
  only for genuine one-offs — if you run the same kind of script twice, it probably wants to be
  a tool.

## CAM API

- **CAM data is reachable without switching to the Manufacture workspace:**
  `app.activeDocument.products.itemByProductType('CAMProductType')` → `adsk.cam.CAM`. Then
  `cam.setups` (iterable, `.count`, `.item(i)`); `Setup.operations` / `Setup.allOperations`;
  `Operation.tool.description` for the tool string (including number). Use
  `adsk.cam.Operation.cast(x)` to skip folders/patterns when iterating `allOperations`.
- **Setup geometry selections:** `Setup.models` (selected model bodies/occurrences),
  `.fixtures`, `.stockSolids` — each an ObjectCollection of items with a `.name`. These are
  usually container Occurrences; descend `Occurrence.childOccurrences` for the real parts.
- **External reference → source doc:** `Occurrence.isReferencedComponent` →
  `Occurrence.documentReference.dataFile` → `.id` / `.name` / `.fusionWebURL`. Walk the assembly
  via `Design.rootComponent.occurrences` + `Occurrence.childOccurrences`.
- **Tool sheet:** `Operation.tool.description` is a reliable readable tool string (number +
  type + geometry). Aggregate by it for a distinct-tool list — more robust than digging into
  `Tool.parameters` internal names.
- **Cycle time:** `CAM.getMachiningTime(target, feedScale, rapidFeed, toolChangeTime)` →
  `MachiningTime(.machiningTime / .totalFeedTime / .totalRapidTime / .toolChangeCount)`, all in
  seconds. `target` can be a Setup/Operation/Folder/collection. Requires generated toolpaths.
- **Operation parameters:** every `Operation` has `.parameters` (CAMParameters, iterable,
  `.itemByName`). Each `CAMParameter` has `.name` (internal), `.title` (UI label), and
  `.expression` (the reliable human-readable value — prefer it over the typed `.value`
  subclasses). `cam_compare_operations` diffs two ops' parameters and reports EXACT expressions
  (including float jitter like `38.10000000000001`) on purpose — do not round/filter; let the
  agent reason about precision.
- **NC programs:** `CAM.ncPrograms` → `NCProgram(.name, .operations, .machine,
  .postConfiguration.description, .postParameters)`. The UI's Name/Number/Comment/Output-folder
  fields are NOT exposed as readable post parameters — `postParameters` only holds post
  *options* (e.g. `metric`, probing/format settings). Report the actual post parameters rather
  than fabricating those fields.

## Toolpath templates

The library manager is on the **CAMManager singleton**, not the CAM product:
`adsk.cam.CAMManager.get().libraryManager.templateLibrary` → `CAMTemplateLibrary`.

- Navigate with `urlByLocation(LibraryLocations.*)` (Local=0, Cloud=1, Fusion360=5, …),
  `childFolderURLs(url)`, `childTemplates(url)`, `displayName(url)`.
- Apply with `Setup.createFromCAMTemplate2(CreateFromCAMTemplateInput.create())` after setting
  `.camTemplate` and `.mode` (AutomaticGenerationModes: ForceGeneration=0, SkipGeneration=1
  [default], UserPreference=2).
- **Asset URLs:** `childTemplates(folderUrl)` returns CAMTemplate *objects* but NOT their URLs.
  To get a template's addressable asset URL, use **`childAssetURLs(folderUrl)`** — these look
  like `cloud://<folder>/<name>.f3dhsm-template`. `templateAtURL(assetUrl)` round-trips them,
  and `importTemplate` *returns* the new asset URL. Addressing a template by URL works, but you
  need a real asset URL (from `childAssetURLs` or an `importTemplate` return), not a
  constructed folder+name string.
- **Save new:** `CAMTemplate.createFromOperations([Operation, ...])` → set `.name` /
  `.description` → `library.importTemplate(template, FOLDER_url)` (destination is a FOLDER url;
  create the folder with `library.createFolder(parentUrl, name)` if needed). A saved template
  also spawns an `_XRef_` subfolder — that is normal Fusion template structure.
- **Overwrite:** `library.updateTemplate(template, ASSET_url)` where `ASSET_url` comes from
  `childAssetURLs`. It replaces an existing template, so build the inputs carefully.

## Design parameters

`Design.userParameters` (user) / `Design.allParameters` (all). `Parameter(.name,
.expression [settable], .value [numeric, db units], .unit, .comment, .textValue)`. `.expression`
is the human-facing field. `param_get` reads; `param_set` writes `.expression`
(changing a driver cascades to dependents, e.g. `StockY = StockX`). Setting model/feature
params can raise — surface the error rather than crashing.

**Fusion expression-language syntax** (matters when authoring `param_set` expressions):
function ARGS are separated by **`;`**, not `,` — e.g. `if(cond; then; else)`, `max(a; b)`,
`min(a; b)`. Conditionals nest: `if(StockX>=2 in; if(StockY/2>=13 mm; 10 mm; 5 mm); 5 mm)`.
Units mix freely in one expression (`StockX + Wall_Taper_Width_Min*2`, in + mm) and the result
carries the parameter's own unit. Round-up-to-increment idiom: `ceil(x/inc)*inc`. Text params
take a QUOTED string expression: `'text'` (unit shows as "Text"). References can be negated
(`-d242`). A common template idiom is a user param aliasing a computed one
(`StockX = Calc_StockX`) so the value auto-computes but can be overtyped to break the link.

## Assembly positioning (move vs. parametric features)

- **A free `Occurrence.transform` move is silently clobbered by a parametric pattern/mirror on the
  next recompute.** `assembly_move` writes the occurrence transform directly (a free move, no
  relationship). A `RectangularPatternFeature` / `MirrorFeature` / `CircularPatternFeature` is a
  *timeline feature* that re-derives its instances' placement from the base body/occurrence every time
  the timeline recomputes — and ANY later edit (a fillet, a parameter change, `design_recompute`)
  triggers that recompute. When it does, the feature overwrites the free move and the patterned parts
  snap back to where the feature thinks they belong. Symptom: parts that looked correct in an early
  screenshot are scattered after an unrelated later edit; `model_measure_bbox` shows the occurrence
  centre at the pre-move location. This cost a full assembly rebuild during the tractor build.
- **The robust pattern: bake position into geometry, don't move-then-pattern.** Build each part's
  geometry at its FINAL position inside an origin-placed component (offset `model_construction` plane for
  an off-plane axis, e.g. a wheel centred away from the sketch plane), then `model_mirror` the *bodies*
  across an origin plane for left/right symmetry. Body mirror/pattern features are stable under recompute
  because the geometry itself carries the position; only *occurrence* placement fights the timeline.
- **Occurrence placement double-offsets world-coord sketch geometry.** If you place a component with
  `model_create_component(x=…, y=…, z=…)` AND then sketch geometry at world coordinates inside it, the
  occurrence transform applies on top of the world coords — the part lands at (placement + world). Pick
  one: place the component at the origin and draw at world coords, OR place the occurrence and draw at
  local (component-relative) coords. `model_measure_bbox` on the occurrence confirms the true location.

## Occurrence delete

- **`Occurrence.deleteMe() -> bool`** removes one instance; if it was the last instance referencing its
  component, the component is deleted too (per the API docstring, confirmed live). It returns **False
  without raising** for an instance Fusion won't remove on its own — most often a pattern/mirror CHILD,
  which can only be removed by deleting (or reducing the count of) its owning timeline feature. There is
  **no Occurrence-level "is a pattern child" property** (`isClonedComponent` does NOT exist on
  Occurrence; only `sourceComponent` / `isReferencedComponent` / `isValid` do), so detect the
  feature-owned case from the `deleteMe() == False` result rather than a pre-check. `design_delete_occurrence`
  does this and reports it with a pointer to the owning feature.
- Deleting an occurrence silently drops the joints it participated in (`Occurrence.joints`). Read and
  report those names BEFORE the delete so the loss is visible, and re-check timeline health after (a
  downstream feature may have referenced the removed geometry).

## Mesh bodies

- **A parametric mesh write must run inside a base-feature scope.** `MeshBodies.add` /
  `addByTriangleMeshData` fail in a parametric design unless wrapped in
  `BaseFeature.startEdit()`/`finishEdit()`. While that scope is OPEN the design reports
  `designType == DirectDesignType`, the base feature is HIDDEN from its collection
  (`baseFeatures.count` drops, `itemByName` returns None), and `Design.timeline` raises — so the only
  handle to an open scope is the `BaseFeature` object `add()` returned. Capture it and finish through
  it; do not try to re-find an open scope by name. (Direct designs need no scope.)
- **Tessellation emits unwelded vertices.** `body.meshManager.createMeshCalculator().calculate()`
  returns one node per triangle corner — a box yields 24 nodes for 8 real vertices — so feeding its
  `nodeCoordinatesAsDouble`/`nodeIndices` straight to `addByTriangleMeshData` produces a topologically
  OPEN mesh: `MeshBody.isClosed` is False even for a watertight solid, and `mesh_to_brep` then refuses
  it. Merge coincident vertices (dedupe coordinates at a tight tolerance, remap the indices) before the
  add; the normals stay per-corner and geometry is unchanged. (`save_as_mesh` does this; see `_weld`.)

## Workspaces

`app.userInterface.workspaces` (iterable) → `Workspace(.id, .name, .isActive, .productType,
.activate())`. Design id `FusionSolidEnvironment`, Manufacture id `CAMEnvironment`. `activate()`
can legitimately fail (returns False) — e.g. with no document open — so handle it.

## Data model

- `app.data.dataProjects` / `DataProject.rootFolder` / recurse `dataFiles` + `dataFolders`;
  `DataFile.id` (lineage UID), `.versionId`, `.fusionWebURL` (openable).
- Open by UID with `app.data.findFileById(id)` →
  `app.documents.openUsingContext(df, FileOpenContext.create(), True)` (async — see the
  no-blocking rule). Prefer `openUsingContext` over `open` — it handles both normal and
  configured designs (below). `findFileById` takes a URN, not a URL.
- **Accepting pasted identifiers:** `fusionWebURL` / `source_url` are browser URLs, not URNs —
  `findFileById` rejects them. The lineage URN is embedded in the URL as a base64url path
  segment (`…/data/<folderURN_b64>/<fileURN_b64>`); decode each long segment and keep the one
  that decodes to `urn:adsk…`. `doc_open` does this, so it accepts a lineage/version URN, a
  `source_id`, OR a web URL interchangeably (`_urn_candidates` / `_b64url_decode`).

## Configured designs

Open them with `openUsingContext`, NOT `open`.

- `documents.open(df)` raises `2 : InternalValidationError : doc` when `df.isConfiguredDesign`
  is True. `documents.openUsingContext(df, FileOpenContext.create(), True)` opens it cleanly. A
  default (empty-timestamp) context is enough — you do not need to select a configuration row;
  it opens at the active config. `openUsingContext` also works for normal designs, so
  `doc_open` uses it as the primary path and only falls back to `open()` if
  `openUsingContext` is unavailable.
- Once open, read configurations from the open design's **`Design.configurationTopTable`** (NOT
  `Design.configurationTable`, which does not exist). It exposes `.name`, `.rows` (each
  `ConfigurationRow`: `.name` / `.id` / `.index`, plus `.activate()` to switch the live
  configuration and `.generate()`), `.columns` (`ConfigurationPropertyColumn` /
  `ConfigurationThemeColumn` subtypes — `.name` raises on some column subtypes; use the
  subtype-specific accessor), and `.activeRow`.
- `DataFile.configurationTopTable` (from the *unopened* DataFile) is limited: properties that
  return a Component or Parameter return null and rows are empty. Pre-open you can still read
  `DataFile.fusionWebURL` and the table `.id`.
- `Document.close(saveChanges=False)` cleanly closes a configured design and discards changes —
  useful for round-trip testing without persisting anything.
- `sys_execute_script` wraps the script in a transaction that ABORTS on any raised exception
  (rolling back file writes too); write diagnostics before the risky call.

## Active document, save, copy, delete

- **Active document → identity:** `app.activeDocument` → `Document(.name, .isSaved, .isModified,
  .version [the Fusion APP version it was saved with, NOT a file version], .dataFile)`.
  `Document.dataFile` is the A360 `DataFile`; for a never-saved doc it is null / raises — guard
  it (`doc_get_active_id` does, and reports `has_data_file=false`).
- **Saving the active doc:** `Document.saveAs(name, DataFolder, description, tag) -> bool` saves
  the LIVE session — including a never-saved doc — distinct from `data_upload_file` (local file) and
  `doc_copy` (existing saved cloud file). Right after `saveAs`, `doc.dataFile.id` is a
  LOCAL pre-upload handle (a temp `.f3d` path), NOT the lineage URN — cloud processing assigns
  the `urn:` id a moment later. So `doc_save_as` returns `document_id=null` unless `.id`
  already `startswith("urn:")`, and tells the caller to confirm via `doc_get_active_id`
  after a short wait. Don't block waiting for it.
- **Copying a saved cloud file:** `Data.findFileById(urn).copy(targetFolder) -> DataFile`.
  External references are PRESERVED as pointers to their originals (not re-copied) — read them
  via `DataFile.hasChildReferences` / `childReferences`. Note: `copy` does NOT share lineage, so
  Fusion won't auto-repair joints from the copy; a `Document.saveAs` from a shared ancestor is
  needed for the Save-As-lineage pattern.
- **Deleting, guarded:** `DataFile.deleteMe()` / `DataFolder.deleteMe()`. `DataFile.deleteMe`
  fails on an OPEN or REFERENCED file (Fusion's own guard); the tools add a `confirm_name`
  exact-match check and, for files, a `parentReferences` refusal (force to override).
  `DataFolder.deleteMe` has NO built-in empty/root guard — `data_delete_folder` refuses a project
  root (`folder.isRoot`) and a non-empty folder unless forced. Resolve a folder by id with
  `Data.findFolderById(id)`. Deletion is irreversible.

## Matrix3D: rotation pivot lives in the translation column

`Matrix3D.setToRotation(angle, axis, origin)` rotates about `origin` by baking a pivot-correcting
term (`origin - R·origin`) into the matrix's **translation column**. So `mat.translation = vec` AFTER a
non-origin `setToRotation` **overwrites** that correction — the part then rotates about the WORLD origin
instead of `origin`. To rotate AND translate in one transform, compose the translation as its own
matrix (`t = Matrix3D.create(); t.translation = vec; mat.transformBy(t)`), never assign `mat.translation`
on the rotation matrix. (`assembly_move` does this; fixed 2026-06-29.)

> ⚠️ **PENDING LIVE CHECK (before merge):** the above is confirmed by API-doc + code reading and pinned
> by a unit test against a fake Matrix3D, but NOT yet verified against real Matrix3D semantics. Run
> `assembly_move(rotate_deg=…, rotate_axis=<edge handle>, dx=…)` on a live occurrence and confirm it
> swings about the edge, not the world origin. NB: a transient move also needs
> `assembly_capture_position` to persist past the next timeline feature — don't mistake an uncaptured
> move for a pivot bug.

## Verifying a new tool

1. **Static:** stub `adsk.*` plus the repo packages in `sys.modules` (faithful temp-package
   tree), import the module, register it, and exercise the handler against stub objects —
   including the error paths. Run with Fusion's bundled Python (path in CONTRIBUTING.md).
2. **Live:** `sys_reload_addin` to load it, then drive it via a POST to `127.0.0.1:27182/mcp`
   (`tools/call`). Confirm against a real document.
