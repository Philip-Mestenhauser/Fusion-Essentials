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
  (`Fixtures/Vises`, split on `/` or `\`); `data_get(include=['folders'])` reveals the tree; `data_get`
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
  `childOccurrences` — a top-level read shows only the container. `design_get(include=['tree'])` does this
  (depth-bounded, resolving each X-ref to its source UID). `cam_get(include=['references'])` only resolves
  top-level refs.
- A lone cube referenced by a setup is very likely a **WCS-defining component**, not a
  placeholder — descend and report rather than assuming.
- The tools that cover the common machinist flow without `sys_execute_script`:
  `design_get(include=['tree'])`, `cam_activate_setup` (+ `view_screenshot` to review), `cam_get(include=['tools'])`,
  `cam_get(include=['time'])`, `cam_get` / `cam_get(include=['operations'])`. Reach for `sys_execute_script`
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
- **Operation geometry selections** (how an op gets its faces/edges/contours to machine — verified
  live on the block AND by dissecting two pro sample docs, "Pier 9 Logo" + "Probing Strategies").
  There are **two distinct selection mechanisms** plus a geometry-driven height pattern:
  - **(A) Curve selections — the CHAIN family**, for milling boundaries: 2D contour `contours`,
    2D pocket `pockets`/`stockContours`, 3D adaptive/parallel **`machiningBoundarySel`** — all are
    `CadContours2dParameterValue`. Flow: `param.value.getCurveSelections()` (→ `CurveSelections`) →
    `cs.clear()` → `cs.createNew*Selection()` → set `sel.inputGeometry = [edges/curves]` →
    `param.value.applyCurveSelections(cs)`. Builders: `createNewChainSelection` (the workhorse —
    seed one or more **BRepEdge OR SketchLine**, Fusion walks the connected chain → `outputGeometry`
    = ONE `Curve3DPath`), `createNewPocketSelection` (seed the pocket-FLOOR BRepFace),
    `createNewFaceContourSelection`, `createNewSilhouetteSelection`, `createNewSketchSelection`,
    `createNewPocketRecognitionSelection`. `ChainSelection` knobs the pros actually use:
    **`isOpen`** (True for open finish profiles that follow a face edge, False for closed
    boundaries), `isReverted` (flip the tool side / climb-vs-conventional), `startExtensionLength`/
    `endExtensionLength`/`extensionType`. Pros feed BRepEdges (dozens at once is fine — they coalesce
    to 1 path) and SketchLines interchangeably.
  - **(B) Direct OBJECT-LIST**, for drill `holeFaces`, probe `probe_selection`, and height
    references — a `CadObjectParameterValue`. Set `param.value.value = [BRepFace / BRepEdge /
    BRepVertex / SketchPoint, ...]` directly (it's a `BaseVector`). Drill selects cylinder faces this
    way; **diameter-range selection = just FILTER the cylinder faces by `face.geometry.radius`
    (cm; *20 for mm-Ø) before assigning** — no extension needed. (`RecognizedHole.recognizeHoles`
    auto-recognition exists but **requires the paid Manufacturing Extension** and wants a *list* of
    bodies, `recognizeHoles([body])`; the face+radius filter is the license-free equivalent.)
  - **HEIGHTS are a 4-part group** per height (`clearance`/`retract`/`feed`/`top`/`bottom`):
    `<name>_mode` (the *from* reference: `'from stock top'`/`'from hole top'`/`'from hole bottom'`/
    `'from contour'`/`'from point'`/…), `<name>_offset` (delta — SET THIS), `<name>_value`
    (resolved result — READ-ONLY, never set), `<name>_ref` (a `CadObjectParameterValue` for
    reference geometry). **Set heights via `_mode` + `_offset` (+ `_ref` for `'from point'`); never
    `_value`.** DRILL needs no manual depth — it defaults `topHeight_mode='from hole top'` /
    `bottomHeight_mode='from hole bottom'`, reading each hole's depth from geometry. A 2D CONTOUR
    defaults BOTH top & bottom to the same Z → **zero depth, valid-but-empty toolpath with NO
    warning/error**; fix by setting `bottomHeight_mode` + `_offset` (the pros set
    `_mode='from point'`, `_ref=[a BRepVertex / SketchPoint]`, `_offset=<depth>` — height driven by
    selected geometry, e.g. contour bottom = `from point`, ref=SketchPoint, offset=`-0.1in`).
    Set a `_mode` via `param.expression = '<unquoted choice expr>'` (NOT `param.value.value =
    '<display name>'`, which throws). The valid choices come from `ChoiceParameterValue.getChoices()`
    → `(True, (display names…), (expr strings…))`; pass the EXPR string without its surrounding quotes
    (`'from stock top'` → set `from stock top`). **A `_mode`'s valid set is CONTEXT-DEPENDENT and
    ORDER-SENSITIVE** — applying a geometry selection can transiently invalidate a mode that was legal
    in the op's settled state (live: setting `bottomHeight_mode` AFTER re-applying the chain threw
    "Invalid enumeration value", but BEFORE it succeeded). So set heights BEFORE the selection.
    `_offset` is robust and order-independent; prefer offset-only when the default mode is acceptable.
  - **Generation is ASYNC — gate on the FUTURE, not the op flag.** `cam.generateToolpath(op)` →
    `GenerateToolpathFuture`. Poll **`future.isGenerationCompleted`** (pumping `adsk.doEvents()`),
    NOT `op.isGenerating` — the latter clears before the toolpath is actually done, so reading
    `op.hasToolpath`/`isToolpathValid` too early gives a FALSE empty/invalid. (`cam_generate` +
    `cam_get_status` already pump correctly; this only bites direct `generateToolpath` callers.)
- **NC programs:** `CAM.ncPrograms` → `NCProgram(.name, .operations, .machine,
  .postConfiguration.description, .postParameters)`. The UI's Name/Number/Comment/Output-folder
  fields are NOT exposed as readable post parameters — `postParameters` only holds post
  *options* (e.g. `metric`, probing/format settings). Report the actual post parameters rather
  than fabricating those fields.
- **Tool libraries (shared, no open CAM job needed):** `adsk.cam.CAMManager.get().libraryManager
  .toolLibraries` → `ToolLibraries`. `urlByLocation(LibraryLocations.Fusion360LibraryLocation /
  LocalLibraryLocation / CloudLibraryLocation / HubLibraryLocation)` → root URL;
  `childAssetURLs(url)` → libraries directly UNDER url; **`childFolderURLs(url)` → sub-folders**.
  Fusion360/Local have libraries right at the root, but **Cloud/Hub libraries are NESTED in folders**
  (Hub root has no direct assets — its libraries sit under a hub-name folder, e.g.
  `hub://Mechio/Haas Vf2.hub`), so you must RECURSE (list assets + descend folders) or Hub/Cloud come
  back empty. `urlByLocation` resolves all locations (`cloud://`, `hub://Mechio`); `toolLibraryAtURL(url)`
  → `ToolLibrary(.count, .item(i) -> Tool)`. A `Tool` has
  `.parameters.itemByName('tool_type' / 'tool_diameter' / 'tool_numberOfFlutes' / 'tool_description'
  / 'tool_unit' / ...).value.value` (182 params) plus `.presets`, `.toJson()`. Diameters come back in
  the cm scale → *10 for mm (a 12mm endmill reads 1.2). The stable tool REFERENCE for downstream use is
  `(library_url, index)` — what `cam_edit_tools` (list action) returns and `cam_create_operation` consumes.
- **Creating a tool library** (cam_edit_tools, action=create_library): `ToolLibrary.createEmpty()` (or
  `createFromJson(json)`) → `.add(tool)` to seed → `ToolLibraries.importToolLibrary(lib, destinationUrl,
  name)` → URL of the new persisted library (numeric suffix if the name exists; throws on a read-only
  destination). **Verified live: Local + Cloud work** (write `…/Name.json`). **Hub does NOT** — import at
  `hub://` fails ("Neither the folder nor its parent exists"); even into the team folder `hub://Mechio`
  (which holds `Holders.hub` / `Haas Vf2.hub`) it raises "Tool library import failed" — Hub team libraries
  use a `.hub` format / different write path the API won't satisfy here (create those in the UI). The
  import raises a HARD error that propagates past a try/except in a spike script — wrap it in the tool.
  Fusion360 samples are read-only (refuse). To create at Hub you must target its child folder, not
  `hub://`. (A Hub library MADE IN THE UI can then be populated via updateToolLibrary — only the *create*
  is UI-only.)
- **Build a tool / preset / holder** (the cam_edit_tools demo): a `Tool` round-trips as JSON —
  `tool.toJson()` / `Tool.createFromJson(json)`. Top-level keys: `type`, `description`, `geometry`,
  `holder`, `start-values`, `presets`, `guid`, vendor info. To MAKE a tool of a given geometry type,
  clone a sample tool's JSON of that type and change `description` (don't hand-author the schema). The
  21 distinct sample geometry types: flat/ball/bull-nose/face/form/radius/slot/lollipop/dovetail/tapered/
  chamfer mills, drill, spot drill, counter bore, counter sink, reamer, tap, thread mill, laser/plasma/
  waterjet. PRESETS: `tool.presets.add()` clones the tool's values into a new `ToolPreset`; set its
  `.parameters.itemByName('tool_spindleSpeed'/...).expression`. HOLDER: it's the `holder` sub-dict in the
  JSON (a Holders-library item IS a holder doc: `{type:'holder', segments:[...], gaugeLength, ...}`) —
  ASSIGN one by setting `toolJson['holder'] = holderJson` before createFromJson.
- **Persisting library writes** (`ToolLibrary.add`/`remove` then `ToolLibraries.updateToolLibrary(url,
  lib)`): works on Local/Cloud/Hub; `updateToolLibrary` returning True is NOT proof — re-read fresh from
  the url to confirm. GOTCHA: writing to a library that is OPEN in the Manufacture UI contends with the
  UI lock and STALLS the script (looks like a timeout) though the write still lands — close the library
  tab before bulk writes. Hub reads/writes are network-backed and slow; read one Hub library at a time
  (reading all at once can time out).
- **Create an operation (proven live end-to-end):** `Setup.operations.compatibleStrategies` → list of
  `OperationStrategy`; each `.name` is the strategy STRING (`face`, `adaptive`, `pocket2d`, `drill`,
  `bore`, `contour2d`, ... 53 for a milling setup). Then `Setup.operations.createInput(strategyName)`
  → `OperationInput`; set `opin.tool = <Tool from a ToolLibrary>` (writes the tool params);
  `Setup.operations.add(opin)` → `Operation`; `CAM.generateToolpath(op)` → `GenerateToolpathFuture`
  (async) and the op then reports `.hasToolpath` / `.isToolpathValid`. The CAM product only exists once
  the doc has CAM data — switch to Manufacture once (or use cam_create_setup) to materialise it.
- **Setup is broadly editable** (cam_edit_setup): `Setup.parameters` (~287 CAMParameters) is the lever
  for almost everything — the WCS is steered via `wcs_orientation_mode` / `wcs_origin_mode` /
  `wcs_origin_boxPoint` / `wcs_orientation_axisZ/X/Y` + `flipZ/X/Y` (the `Setup.workCoordinateSystem`
  Matrix3D itself is READ-ONLY), and stock via `stockXLow/High` / `stockZHigh` / ... `Setup.models` /
  `.fixtures` / `.stockSolids` are get/SET ObjectCollections of Occurrence/BRepBody/MeshBody (empty
  collection clears). All verified live.
- **Folders & patterns** (cam_edit_folders): `Setup.folders` (CAMFolders) `.addFolder(name)` → `CAMFolder`
  (`.name` get/set, `.operations`/`.patterns`/`.folders`, `.deleteMe()`). Move any item with
  `OperationBase.moveInto(container)` (works into setups/folders/patterns) / `moveAfter` / `moveBefore`.
  **PATTERNS (mirror/linear/rotary) CANNOT be created via the API** — `createInput('pattern')` returns
  an input but `operations.add()` raises "Strategy is not exposed to the API" (and there's no
  mirror/linear/rotary strategy, no `CAMPatterns.add`). They CAN be read + their `.parameters` edited
  (a `CAMPattern` has `.parameters` like an operation). Create patterns in the Manufacture UI.
- **CAM delete** (cam_delete): `design_delete_feature`/`_occurrence` only touch the DESIGN timeline —
  CAM entities live in `cam.setups`, not the timeline. Setup/Operation/CAMFolder/CAMPattern each have
  `.deleteMe()` (verified live on an operation + folder). Honour the false-return (Fusion can decline).

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
  screenshot are scattered after an unrelated later edit; `model_inspect` shows the occurrence
  centre at the pre-move location.
- **The robust pattern: bake position into geometry, don't move-then-pattern.** Build each part's
  geometry at its FINAL position inside an origin-placed component (offset `model_construction` plane for
  an off-plane axis, e.g. a wheel centred away from the sketch plane), then `model_mirror` the *bodies*
  across an origin plane for left/right symmetry. Body mirror/pattern features are stable under recompute
  because the geometry itself carries the position; only *occurrence* placement fights the timeline.
- **Occurrence placement double-offsets world-coord sketch geometry.** If you place a component with
  `model_create_component(x=…, y=…, z=…)` AND then sketch geometry at world coordinates inside it, the
  occurrence transform applies on top of the world coords — the part lands at (placement + world). Pick
  one: place the component at the origin and draw at world coords, OR place the occurrence and draw at
  local (component-relative) coords. `model_inspect` on the occurrence confirms the true location.

## Joints across occurrences (assembly-context proxies)

- **`Joints.createInput` rejects a sub-component's NATIVE joint origin** — and its `.geometry` —
  with `RuntimeError: 3 : Provided input paths for joint are not valid`. A joint owned by the root
  component needs each input in the ROOT's assembly context, and a JO fetched via
  `component.jointOrigins.itemByName(...)` is native to that component, not to the assembly. The fix
  is the occurrence proxy: `nativeJO.createForAssemblyContext(<occurrence that instances the
  component>)`. This bites hardest for a JO inside an INSERTED/x-ref part (confirmed live: three
  hand-rolled script variants all failed before the proxy). `joint_create` proxies automatically when
  given a JO name (bare or `'<occurrence>:<JO name>'`) — steer agents to it instead of scripting
  joints.
- **Match the proxying occurrence by component NAME, not identity.** The API returns fresh wrapper
  objects for the same component, so `occ.component is owner` silently fails; compare
  `occ.component.name` (see `_find_joint_origin` in `joint_create_edit.py`).

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

## Holes (`model_hole` / HoleFeatures)

Use the real `HoleFeatures`, not a sketch circle + extrude-cut — the hole then reads as a Hole in the
timeline and carries hole/thread metadata (what fastener/CAM tooling recognises).

- **Three input builders** on `component.features.holeFeatures`: `createSimpleInput(dia)`,
  `createCounterboreInput(dia, cbDia, cbDepth)`, `createCountersinkInput(dia, csDia, csAngle)`.
- **Placement** (on the returned `HoleFeatureInput`): `setPositionBySketchPoint(sketchPoint)` or
  `setPositionBySketchPoints(ObjectCollection of co-planar points)`. The reliable path is to add a
  sketch on the target face and create the points there. `setPositionByPoint(face, Point3D)` is
  finicky — a bare Point3D often fails the hole's `logicalSelection`; prefer sketch points.
- **Extent**: `setDistanceExtent(value)` for blind; `setAllExtent(direction)` for through. **Through
  MUST use `ExtentDirections.PositiveExtentDirection`** — `NegativeExtentDirection` fails with
  `InternalValidationError : logicalSelection` (the hole's natural direction is already INTO the body,
  so positive is the through direction). This one cost real debugging.
- **Tapped holes**: `tf = component.features.threadFeatures` (it's on `Features`, NOT `Design`);
  `ti = tf.createThreadInfo(isInternal, threadType, threadDesignation, threadClass)` then
  `holeInput.setToTappedHole(ti)`. The SIZE is embedded in the designation ("M5x0.8") — it is NOT a
  separate argument to `createThreadInfo`. Query the library via `tf.threadDataQuery`
  (`allThreadTypes` -> `allSizes(type)` -> `allDesignations(type, size)` -> `allClasses(internal,
  type, designation)`). `holeInput.isModeled=False` keeps the thread cosmetic.
- **`holeFeatures.add(input)` RAISES on an inconsistent input, and a raised exception ABORTS the whole
  `sys_execute_script` transaction** (rolling back everything in that run). Validate inputs and resolve
  the ThreadInfo BEFORE calling `add`. Also: a partial/aborted run can leave the doc bodyless — start
  spikes from a clean `doc_new`.
- **Fastener-library bridge (future):** a tapped hole's thread is configurable via
  `ConfigurationColumns.addThreadTypeColumns(holeFeature, ConfigurationThreadColumns....)` /
  `addFeatureAspectColumn(holeFeature, ThreadDesignationFeatureAspectType, ...)` — i.e. thread
  type/size/designation/class can each become a configuration column. This is the hook for
  encoded-hole / fastener-driven configurations.

### Fasteners & clearance holes (live-verified findings)

- **Fastener-BODY insertion is SEMI-automatable (not headless).** The Fastener command is a real,
  triggerable command definition: `ui.commandDefinitions.itemById('FusionFastenersCommand')` (name
  "Insert Fastener"; also `FastenersInsertSimilar`, `InsertMcMasterCarrComponentCommand`).
  `cmd.execute()` returns immediately and OPENS THE MODAL DIALOG — it does NOT block, expose
  commandInputs, or let you set the screw/click OK (the Autodesk Standard Components add-in is closed).
  So you CANNOT complete it headless. BUT you CAN pre-stage it: select the **counterbore CYLINDER
  FACES** (the hole signature — bore axis/dia/depth; a raw rim EDGE gives ambiguous input and the
  command may not recognise it) via `ui.activeSelections`, then `execute()` → the dialog opens
  pre-populated and the user only picks the fastener + OK. The command snaps the screw onto each
  axis/face and adds a fastener feature. (Confirmed live: placed a 'Cheese Head Screw ISO 7048 - M6 x
  20' — one per accepted face.) Building block = a "select-holes-and-launch" helper, not a full inserter.
- **A Fastener OBJECT ≠ a screw-shaped component.** The dynamic, resizable Fastener (in the Fasteners
  browser folder, `FastenerOccurrenceDefinition` + `updateSize` + its locating joint) is created ONLY
  by the Fasteners command — its "fastener-ness" lives in a fastener FEATURE the command authors, not
  in the geometry. So `occurrences.addByInsert(libraryDataFile, t, isReferenced=False)` /
  `addExistingComponent` only ever clone a DUMB STATIC model of the screw (generic OccurrenceDefinition,
  no updateSize, no joint) — NOT a real fastener. There is no API to construct a FastenerOccurrenceDefinition.
- **Fastener objects expose** `FastenerOccurrenceDefinition` (vs the generic `OccurrenceDefinition`
  for normal occurrences) with `isSizeUpToDate` + `updateSize()` (auto-resizes a placed fastener to its
  hole — check isSizeUpToDate first; updateSize isn't a no-op) + `parentOccurrence`.
  `Component.isLibraryItem` flags library components.
- **Stray add-in note:** the bundled `colorHoles` command's `active_selection_changed` handler throws
  `NoneType has no attribute 'parent'` on programmatic `activeSelections` changes — harmless noise in
  script output (the selection still takes), but a real bug in that command worth fixing separately.
- **Clearance holes are HALF-wired.** `HoleFeatureInput.setToClearanceHole(ClearanceHoleInfo)` exists
  (parallel to setToTappedHole) and TAGS the hole with a fastener spec — `ClearanceHoleInfo.create(
  standard, fastenerType, size, fit)`, validated against the live catalog via
  `ClearanceHoleDataQuery.create()` → `allStandards` / `allFastenerTypes(std)` / `allSizes(std,type)`.
  BUT on this version it returns True WITHOUT resizing the geometry, and NEITHER ClearanceHoleInfo NOR
  the data query exposes the resolved DIAMETER. So `model_hole`'s `fastener=` does BOTH: tags via
  setToClearanceHole AND sets the bore from a built-in ISO-273 clearance table (`_CLEARANCE_MM`,
  close/normal/loose). `ConfigurationColumns.addClearanceTypeColumns` config-drives the clearance.
- **Stale face after each hole:** drilling a hole recomputes the body, invalidating a cached BRepFace
  reference — re-find the placement face for EACH hole when drilling several in a loop.
- **Spike in a throwaway doc_new, never the saved working doc** — a partial spike leaves scratch
  bodies/features in the timeline that then have to be excised from the saved part.

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

### Building a configured design (`design_configure`)

The write side. All live-verified on a parametric bracket.

- **`Design.createConfiguredDesign()`** converts the active design and returns a
  `ConfigurationTopTable` with ONE row and NO columns. It works on an unsaved design (the in-memory
  `Design.isConfiguredDesign` flips True), but see the save+reopen rule below — that alone does not
  give the user a configured design.
- **Save + reopen is the real instantiation.** The conversion materializes for the user in three
  steps: (1) `createConfiguredDesign()` builds the table in memory; (2) **saving** commits it —
  `DataFile.isConfiguredDesign` flips True only AFTER the save; (3) **reopening** the document makes
  the UI rebuild and show the Configurations dropdown. An already-open document will NOT retrofit the
  dropdown — Fusion builds that toolbar at open time. So `design_configure(create)` requires a saved
  doc and its note tells the caller to save+reopen. (Right after `saveAs`, the cloud lineage lags:
  `doc.dataFile` may raise `can't fetch table from PIM` and `doc_get` returns a local cache
  path instead of a `urn:` — retry the DataFile read after the async save lands.)
- **Columns live on `table.columns` (`ConfigurationColumns`)**: `addParameterColumn(Parameter)`,
  `addSuppressColumn(feature)`, `addVisibilityColumn(entity)`, `addInsertColumn(occurrence)`. The
  parameter/suppress variants are valid only on the top table or a theme table (they fail elsewhere).
  The `Parameter` must come from THIS design's `allParameters`.
- **Address cells by ROW NAME, not index.** Every column kind exposes `getCell(index)`,
  `getCellByRowId(id)`, and `getCellByRowName(name)`. Prefer `getCellByRowName` — robust against row
  reordering. Cell setters: parameter cell `.expression = "50 mm"`; suppress cell
  `.isSuppressed = True`; visibility cell `.isVisible = False`; appearance cell `.appearance = appObj`.
- **A parameter column only changes geometry if the parameter drives a dimension.** A user parameter
  that nothing consumes will switch value but the model won't resize. After `ConfigurationRow.activate()`
  call `Design.computeAll()` so the geometry rebuilds to the active config.
- **Appearance theme table has an ORDERING trap and a LINKAGE trap.** Get it via
  `table.appearanceTable`. (1) Ordering: call `appearanceTable.columns.add(body)` FIRST — adding the
  body column auto-creates the first theme row; add extra theme rows AFTER (adding theme rows before
  the body column throws `InternalValidationError`). (2) Linkage: each config row is tied to a theme
  row through `appearanceTable.parentTableColumn` (a `ConfigurationThemeColumn`),
  `cell.referencedTableRow = themeRow`. The theme column's **`getCell(index)` does NOT share
  `top.rows` ordering** — addressing it positionally links the wrong configuration (live-caught: the
  colors came out swapped). Use `themeColumn.getCellByRowName(configName)`.
- **Nested configurations (insert column).** To make an assembly config select a configured PART's
  config: insert the part with `root.occurrences.addFromConfiguration(partRow, transform)` (the part
  and assembly must be in the SAME project; `partRow` comes from the part DataFile's
  `configurationTable`), then `assemblyTable.columns.addInsertColumn(occurrence)` ->
  `ConfigurationInsertColumn`, and per assembly config set `cell.row = partRow` — again addressing the
  cell by `getCellByRowName(assemblyConfigName)`. The set row must belong to the inserted part's table.
  Verified live end-to-end: switching one assembly config drove the base geometry, a config-driven
  circular-pattern quantity, AND the inserted part's configuration together.
- **Circular pattern instances can silently drop when config-driven.** A `CircularPatternFeature`
  whose `quantity` is a configured parameter updates the quantity value, but if the pattern geometry
  (e.g. a bolt circle whose diameter is ALSO config-driven) pushes instances off the body or overlaps
  them, fewer instances materialize than `quantity` says. Not a configurations bug — keep patterned
  features within the body across all configs, or the instance count won't match the parameter.

## Active document, save, copy, delete

- **Active document → identity:** `app.activeDocument` → `Document(.name, .isSaved, .isModified,
  .version [the Fusion APP version it was saved with, NOT a file version], .dataFile)`.
  `Document.dataFile` is the A360 `DataFile`; for a never-saved doc it is null / raises — guard
  it (`doc_get` does, and reports `has_data_file=false`).
- **Saving the active doc:** `Document.saveAs(name, DataFolder, description, tag) -> bool` saves
  the LIVE session — including a never-saved doc — distinct from `data_upload_file` (local file) and
  `doc_copy` (existing saved cloud file). Right after `saveAs`, `doc.dataFile.id` is a
  LOCAL pre-upload handle (a temp `.f3d` path), NOT the lineage URN — cloud processing assigns
  the `urn:` id a moment later. So `doc_save_as` returns `document_id=null` unless `.id`
  already `startswith("urn:")`, and tells the caller to confirm via `doc_get`
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
