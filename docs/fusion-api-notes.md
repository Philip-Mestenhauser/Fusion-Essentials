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
- **Document-scoped tool library:** `CAM.documentToolLibrary` -> `DocumentToolLibrary` (`.count`/`.item`/
  `.add`/`.remove`/`.updateTool(tool)`/`.operationsByTool(tool)` -> an index/len-accessible
  `OperationVector`, not a Python list). Distinct from the shared `ToolLibrary` family
  (`ToolLibraries.toolLibraryAtURL`); this is the per-document tool set `cam_edit_tools(scope='document')`
  manages.
- **Job health flags:** `.hasError`/`.error`/`.hasWarning`/`.warning` are exposed by Setup, Operation, AND
  NCProgram alike; `Operation.operationState` (0=valid, 1=out_of_date, 2=suppressed, 3=no_toolpath) and
  `.isGenerating` are Operation-only. Op state/validity is only trustworthy once the **Manufacture**
  workspace has been entered (`app.userInterface.activeWorkspace.id == 'CAMEnvironment'`) - from the
  Design workspace these flags can read stale/valid against changed geometry.
- **Why an op is out of date:** the reason lives in `Operation.messageLog` (NOT `.warning`/`.error`, which
  are empty for a plain invalidation) as lines like `"<ts> I Invalidated: Design changed: Op1: WCS
  origin"` (the category) or `"...different value for parameter '...'"` (one of many per-parameter
  deltas). A machine-definition change logs `"External changed: machine.<field>"` instead of an
  `Invalidated:` line.
- `adsk.cam.Machine` has no `.name` - the human label is `.description` (e.g. "Haas with A-axis"),
  falling back to `.vendor` + `.model` ("HAAS A-axis") when `.description` is empty.
- **Create a new Setup:** `cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation |
  TurningOperation)` -> `SetupInput` (`.models = [BRepBody|Occurrence, ...]`, `.name = str`);
  `cam.setups.add(input)` -> `Setup`. No workspace switch needed.
- **NC program Comment/Name:** the UI's Comment field is the CAM parameter `nc_program_comment` on
  `NCProgram.parameters` (NOT `.postParameters`, which is why it isn't in the post-parameters list); the
  Name field is the sibling parameter `nc_program_name`. Both are string parameters whose `.expression`
  must be QUOTED (e.g. `"'Job 1234'"`).
- **Toolpath visibility:** `Operation.isLightBulbOn` (settable bool) shows/hides one operation's
  rendered toolpath; it only renders in the Manufacture workspace. This is a plain data property -
  distinct from the simulation/in-process-stock UI commands (`Iron*`/`Simulation*`), which are modal and
  unsafe to drive from a script.
- **Generation launch + future lifetime:** `CAM.generateAllToolpaths(skipValid: bool)` ->
  `GenerateToolpathFuture` (the whole-document counterpart to `CAM.generateToolpath(target)`); the future
  exposes `.numberOfOperations`/`.numberOfCompleted` (both raise "Generation not started" if read on the
  same tick as the launch - they only populate after the event loop spins once) alongside
  `.isGenerationCompleted`. Holding a reference to the Future is not just for polling - if it is
  garbage-collected, Fusion ABANDONS the in-progress generation, so the caller must keep it alive (a
  module-level registry) until `isGenerationCompleted`.
- **Walking the CAM tree:** `Setup.allOperations` / `Folder.allOperations` return ONLY `Operation`
  objects - `CAMFolder`/`CAMPattern` are omitted. To reach everything (delete, reorder, move-into,
  edit-by-name across nested folders), walk `.operations` and recurse into `.folders` + `.patterns`
  explicitly.
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

- **Add/delete are user-parameter-only, health-guarded:** `UserParameters.add(name, ValueInput, unit,
  comment)` -> `Parameter`; `Parameter.deleteMe()` (user params only — model/feature params have no
  `deleteMe`). `Parameter.isFavorite` is a settable bool (the favorites-list flag). `param_add`/
  `param_delete` snapshot `Design.timeline.item(i).healthState` (0 healthy / 1 warning / 2 error / 3
  suppressed) before and after the edit; a NEW error after an add rolls it back via `deleteMe()`, and a
  regression after a delete is reported (the delete itself is not undone).

**Fusion expression-language syntax** (matters when authoring `param_set` expressions):
function ARGS are separated by **`;`**, not `,` — e.g. `if(cond; then; else)`, `max(a; b)`,
`min(a; b)`. Conditionals nest: `if(StockX>=2 in; if(StockY/2>=13 mm; 10 mm; 5 mm); 5 mm)`.
Units mix freely in one expression (`StockX + Wall_Taper_Width_Min*2`, in + mm) and the result
carries the parameter's own unit. Round-up-to-increment idiom: `ceil(x/inc)*inc`. Text params
take a QUOTED string expression: `'text'` (unit shows as "Text"). References can be negated
(`-d242`). A common template idiom is a user param aliasing a computed one
(`StockX = Calc_StockX`) so the value auto-computes but can be overtyped to break the link.

## Sketches

- **Creating a sketch:** `Component.sketches.add(planarEntity)` -> `Sketch` (planarEntity = an
  xY/xZ/yZ `ConstructionPlane`, or a planar `BRepFace`). `Sketch.isComputeDeferred = True` batches
  edits so a multi-entity add computes once. Every curve/point exposes `.isConstruction` and a
  stable `.entityToken`.
- **Drawing geometry:** `Sketch.sketchCurves.{sketchLines,sketchCircles,sketchArcs,sketchEllipses}`,
  `Sketch.sketchPoints`. `SketchLines.addByTwoPoints` / `addTwoPointRectangle` /
  `addCenterPointRectangle` / `addScribedPolygon`; `SketchCircles.addByCenterRadius(center,
  radius_cm)`; `SketchArcs.addByCenterStartSweep(center, start, sweepAngle_radians)`;
  `adsk.core.Point3D.create(x, y, z)` (cm; z=0 stays on the sketch plane). **`addCenterToCenterSlot`
  is a method on the SKETCH itself, not `sketchLines`** (confirmed live), and its `width` argument
  must be an `adsk.core.ValueInput`, not a bare float.
- **Constraints:** `Sketch.geometricConstraints.add<Type>(...)` - `addPerpendicular`/`addParallel`/
  `addTangent`/`addEqual`/`addConcentric`/`addCollinear` (two curves); `addMidPoint`/`addCoincident`
  (point + curve); `addHorizontal`/`addVertical` (one line); `addSymmetry(entityOne, entityTwo,
  symmetryLine)`. Fix/unfix is `SketchEntity.isFixed`. Each constraint exposes the entities it
  references via typed attributes (`.line`/`.lineOne`/`.lineTwo`/`.point`/`.entity`/`.entityOne`/
  `.entityTwo`, sometimes a vector like `PolygonConstraint.lines`) - map them back to ids via
  `entityToken`.
- **Dimensions:** `Sketch.sketchDimensions.addDistanceDimension(pointOne, pointTwo, orientation,
  textPoint)` / `addRadialDimension(curve, textPoint)` / `addDiameterDimension(curve, textPoint)` /
  `addAngularDimension(lineOne, lineTwo, textPoint)`. The returned dimension's `.parameter.expression`
  drives its value. **For a radial/diameter dimension the text-point is NOT cosmetic** - Fusion
  derives the radial direction from `(textPoint - center)`, so a text point AT the curve's center
  (natural when the curve sits on the sketch origin) gives a zero-length vector and the add raises
  "Some input argument is invalid"; offset the text point from the center first.
- **Sketch text:** a `SketchText`'s content is NOT settable via its definition
  (`MultiLineTextDefinition` has no `.text`). The writable handle is `SketchText.textParameter` - a
  `ModelParameter` whose `.expression` is the QUOTED string (e.g. `"'Label Text'"`). No
  assembly-context proxy is needed for the write (verified live). Create one via
  `SketchTexts.createInput2(text, height_cm)` + `.setAsMultiLine(startPoint, endPoint,
  HorizontalAlignments, VerticalAlignments, angle)` + `SketchTexts.add(input)`.

## Model feature signatures

Signatures confirmed live for the modelling building-block tools (model_create_component,
model_fillet/model_chamfer, model_combine, model_mirror, model_pattern_rectangular/circular,
model_revolve, model_extrude, model_arrange).

- **Component creation:** `rootComponent.occurrences.addNewComponent(Matrix3D)` -> `Occurrence`
  (`.component`, `.activate()`).
- **Fillet:** `Component.features.filletFeatures.createInput()` -> input;
  `input.addConstantRadiusEdgeSet(ObjectCollection(edges), radius: ValueInput, isTangentChain)`.
- **Chamfer:** `Component.features.chamferFeatures.createInput(ObjectCollection(edges),
  isTangentChain)` -> input; `input.setToEqualDistance(distance: ValueInput)`. `body.edges` ->
  `BRepEdges`; `edge.isConvex` gives the convex/concave filter.
- **Combine:** `Component.features.combineFeatures.createInput(targetBody,
  ObjectCollection(toolBodies))` -> input; `.operation =
  FeatureOperations.{Join|Cut|Intersect}FeatureOperation`; `.isKeepToolBodies = bool`;
  `CombineFeatures.add(input)` -> `CombineFeature`.
- **Mirror:** `Component.features.mirrorFeatures.createInput(ObjectCollection(bodies),
  mirrorPlane)` -> input (`mirrorPlane` is a planar entity, e.g. an xY/xZ/yZ ConstructionPlane);
  `MirrorFeatures.add(input)` -> `MirrorFeature` (`.bodies`).
- **Pattern:** `features.rectangularPatternFeatures.createInput(inputEntities, directionOneEntity,
  quantityOne, distanceOne, PatternDistanceType)`, `.setDirectionTwo(entity, qtyTwo, distTwo)`;
  `features.circularPatternFeatures.createInput(inputEntities, axis)`, `.quantity` / `.totalAngle`
  / `.isSymmetric`. `rootComponent.x/y/zConstructionAxis` are the world-axis direction/axis
  entities. **The pattern's input entities and its direction/axis entity must belong to the SAME
  component** - Fusion raises `InternalValidationError getObjectPath` patterning a sub-component
  body against root's construction axis; build the axis + feature in the entities' owning
  component.
- **Revolve:** `Component.features.revolveFeatures.createInput(profile, axis,
  FeatureOperations)` -> input; `RevolveFeatureInput.setAngleExtent(isSymmetric: bool, angle:
  ValueInput)`; axis is a `ConstructionAxis` or a straight `SketchLine`. For an asymmetric
  two-sided revolve use **`setTwoSideAngleExtent(angleOne, angleTwo)`** - there is no
  `setTwoSidesExtent` (raises `AttributeError`).
- **Extrude:** `Component.features.extrudeFeatures.createInput(profile, FeatureOperations)` ->
  `ExtrudeFeatureInput`; `.setDistanceExtent(isSymmetric: bool, distance: ValueInput)`;
  `.setOneSideExtent(DistanceExtentDefinition, direction, taperAngle)` for a taper.
- **Arrange:** `Component.features.arrangeFeatures.createInput(ArrangeSolverTypes.*)` ->
  `ArrangeFeatureInput`; `input.setProfileOrFaceEnvelope([profile|planarFace, ...])` ->
  `Arrange2DProfileOrFaceEnvelopeInput` (`.objectSpacing` = clearance between parts, cm);
  `input.arrangeComponents.add(occurrence)` per shape; `arrangeFeatures.add(input)` ->
  `ArrangeFeature`.

## Construction geometry (model_construction)

`ConstructionPointInput.setByPoint(Point3D)` and `ConstructionAxisInput.setByLine(InfiniteLine3D)`
are DIRECT-EDIT-ONLY - their API docstrings state they fail in PARAMETRIC modeling mode, and there
is no parametric method that places a point/axis at a raw x/y/z (every parametric constructor needs
existing geometry: a vertex, edge, sketch point, or planar face). So a coordinate point or
world-axis only works when `design.designType == DirectDesignType`; in a parametric design, sketch
a point first, or for an axis use `ConstructionAxisInput.setByEdge(edge)` (parametric-legal).
`ConstructionPlaneInput.setByOffset(planarEntity, ValueInput)` is parametric-legal in both modes.

## Surfaces (open, non-solid bodies)

The discriminator throughout is `BRepBody.isSolid == False` - an open surface has no end caps. Never
wrap a feature `.add()` in `safe()`: a None feature with no exception is the silent-success trap;
assert the returned feature/body and read `isSolid` back rather than assuming it.

- **Building an open profile:** `Component.createOpenProfile(curves, isChained)` /
  `createBRepEdgeProfile(edges)` -> an OPEN profile (as opposed to a closed sketch profile).
- **Extrude/revolve as a surface:** `ExtrudeFeatures.createInput(profile, op)` /
  `RevolveFeatures.createInput(profile, axis, op)` with `.isSolid = False` and the usual
  `setDistanceExtent`/`setAngleExtent`.
- **Patch:** `PatchFeatures.createInput(boundaryCurve: Base, op)` -> `PatchFeatureInput`
  (`.continuity`); `.add(input)` -> `PatchFeature`. Confirmed: patch may only create a NEW body or a
  NEW component - no join/cut/intersect.
- **Trim is a two-phase transaction:** `TrimFeatures.createInput(trimTool)` opens a partial-compute
  transaction and populates `input.bRepCells` - you MUST either commit it via `TrimFeatures.add(input)`
  or abort it via `TrimFeatureInput.cancel()`; leaving it open (e.g. by letting an exception escape
  unhandled) risks a corrupted state / crash. **A SELECTED `BRepCell` (`isSelected=True`) is REMOVED**
  by the trim, so to KEEP a cell leave it `isSelected=False`; `add()` raises "No cells are selected"
  if none are marked for removal.
- **Extend:** `ExtendFeatures.createInput(edges: ObjectCollection, distance, extendType,
  isChainingEnabled=True)`.
- **Offset (surface -> surface):** `OffsetFeatures.createInput(entities: ObjectCollection, distance,
  op, isChainSelection=True)`.
- **Thicken (surface -> solid):** `ThickenFeatures.createInput(inputFaces, thickness, isSymmetric, op,
  isChainSelection=True)`.
- **Loft:** `Component.features.loftFeatures.createInput(FeatureOperations)` -> `LoftFeatureInput`;
  `.loftSections.add(section)` IN ORDER (the ordering is load-bearing, never sort/reorder);
  `.centerLineOrRails.addCenterLine(curve)` XOR repeated `.addRail(curve)` (mutually exclusive);
  `.isSolid = bool`. `LoftFeatures.add(input)` -> `LoftFeature` (`.bodies`, `.isSolid`).
- **Stitch:** `Component.features.stitchFeatures.createInput(ObjectCollection, ValueInput,
  FeatureOperations)` -> `StitchFeatureInput`; `StitchFeatures.add(input)` -> `StitchFeature`
  (`.bodies`). `BRepBody.isSolid` on each result body is the ground truth for whether the stitch
  actually closed - a gap wider than tolerance leaves the result a surface; report that honestly
  rather than assuming the operation always produces a solid.
- **Unstitch:** `Component.features.unstitchFeatures.add(ObjectCollection, isChainSelection)` ->
  `UnstitchFeature` - note there is no `createInput`; `add()` takes the faces/bodies collection
  directly.

## Measurement (model_inspect / model_measure_between)

- **Bounding box:** `BRepBody/Occurrence/Component.boundingBox` -> `BoundingBox3D`
  (`.minPoint`/`.maxPoint`, world-axis-aligned). `app.measureManager.getOrientedBoundingBox(geometry,
  lengthVec, widthVec)` -> `OrientedBoundingBox3D`, for measuring in an arbitrary (e.g. Joint
  Origin) frame; `JointOrigin.secondaryAxisVector` (X) / `.thirdAxisVector` (Y) /
  `.primaryAxisVector` (Z) give that frame.
- **Physical properties:** `Component/Occurrence/BRepBody.getPhysicalProperties(CalculationAccuracy)`
  -> `PhysicalProperties` (`.mass` kg / `.volume` cm^3 / `.area` cm^2 / `.density` kg/cm^3 /
  `.centerOfMass` cm; inertia getters in kg*cm^2).
- **Distance/angle:** `app.measureManager.measureMinimumDistance(entityOne, entityTwo)` and
  `.measureAngle(entityOne, entityTwo)` -> `MeasureResults` (`.value` in cm for distance, RADIANS
  for angle; `.positionOne`/`.positionTwo` are the closest/defining points in cm, `.positionThree` a
  third defining point where relevant).

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
- **Ground / move / rigid group basics:** `Occurrence.isGroundToParent` (bool, get/set) is the
  stateless parent lock `assembly_ground` sets; `Occurrence.transform` (Matrix3D) is the free-move
  handle `assembly_move` edits directly. `rootComponent.rigidGroups.add(ObjectCollection,
  includeChildren)` -> `RigidGroup` locks several occurrences together.

## Assembly analysis and relationships

- **Interference:** `Design.createInterferenceInput(ObjectCollection of occurrences)` ->
  `InterferenceInput` (`.areCoincidentFacesIncluded` bool); `Design.analyzeInterference(input)` ->
  `InterferenceResults` (`InterferenceResult.entityOne`/`.entityTwo` are `BRepBody`;
  `.interferenceBody` is a `BRepBody` with `.volume` in cm^3). An interference-result body exposes its
  owning part via `parentComponent.name` - its `assemblyContext` reads None even on a real assembly, so
  prefer `parentComponent.name` over `assemblyContext` when reporting which occurrence a result belongs to.
- **Kinematic probe:** `rootComponent.occurrences` / `.allOccurrences` -> `Occurrence.transform2.translation`,
  `.isGrounded`, `.isGroundToParent`, `.bRepBodies`, `.name`. `rootComponent.joints` (regular joints) and
  `.asBuiltJoints` are SEPARATE collections - read both or as-built joints are invisible.
  `Joint.jointMotion.jointType` enum -> friendly name + DOF: 0=rigid(0), 1=revolute(1), 2=slider(1),
  3=cylindrical(2), 4=pin_slot(2), 5=planar(3), 6=ball(3). `Design.isFullyConstrained` is SKETCH-only (not
  a whole-assembly DOF signal) - report joint-level DOF from the motion types instead.
- **healthState enum** (Joint/TimelineObject/etc.): 0=healthy, 1=warning, 2=error, 3=SUPPRESSED
  (intentional - not broken). Only 1/2 indicate an actual compute failure; a suppressed entity is
  parked on purpose (e.g. an alternate joint in a fixture template).
- **Position capture (Design.snapshots):** `.hasPendingSnapshot` (bool), `.add()` (valid only when a
  snapshot is pending) -> `Snapshot`, `Snapshot.deleteMe()` to revert the latest capture. A jointed
  occurrence's pose from a free `Occurrence.transform` move is transient until captured this way.
- **As-built joint:** `rootComponent.asBuiltJoints.createInput(occ1, occ2, geometry_or_None)` ->
  `.add(input)` -> a rigid joint mating two occurrences where they already are (null geometry = rigid).
- **Assembly constraint (Constrain Components):** `rootComponent.assemblyConstraints.createInput()` ->
  `input.geometricRelationships.add(entityOne, entityTwo, isFlipped, ValueInput)` (repeatable - several
  pairs solve TOGETHER in one constraint) -> `assemblyConstraints.add(input)`. Entities must be
  root-proxy BRep/sketch/construction entities (assembly-context proxies, same as a joint input).

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

## Joint creation, driving, and linking

- **Create:** `Component.joints.createInput(inputOne, inputTwo)` -> `JointInput` (each input a
  `JointGeometry` or `JointOrigin`); `JointInput.setAs<Type>JointMotion(...)`; `.offset`/`.angle`/
  `.isFlipped`. `Joints.add(jointInput)` -> `Joint`. `JointDirections` enum: X=0, Y=1, Z=2, Custom=3
  (Custom pairs with a world construction axis entity so the motion is a TRUE world direction instead of
  the joint geometry's local frame - X/Y/Z alone are relative to that local frame).
- **Ball joint pitch/yaw:** `setAsBallJointMotion` REQUIRES `pitchDirection=ZAxisJointDirection`,
  `yawDirection=XAxisJointDirection` - any other pair raises "Invalid parameter pitchDirection".
- **Joint-at-geometry** (`Design.findEntityByToken`, JointGeometry factories):
  `Design.findEntityByToken(handle)` -> `[entity]` (`entity.assemblyContext` = its occurrence).
  `JointGeometry.createByNonPlanarFace(cylOrConeFace, JointKeyPointTypes.MiddleKeyPoint)` for a
  cylinder/cone face - **`CenterKeyPoint` is INVALID on a cylinder/cone** ("Key point type should not be
  CenterKeyPoint..."), Middle/Start only. `createByPlanarFace(face, edge_or_None, CenterKeyPoint)` for a
  planar face; `createByCurve(edge, keypoint)`/`createByPoint(vertex_or_point)` for an edge/vertex. A
  construction-point datum is REJECTED in assembly/edit-in-place context ("Environment is not
  supported"). An `'<occ>:origin'` snap COLLAPSES both parts to (0,0,0) - zero offset, a degenerate
  mechanism; a `'<occ>:cylinder'` snap is ambiguous on a multi-cylinder part.
- **Joint origin orientation:** `createByPoint(point)` -> position only, Z = world Z (or the sketch
  plane's normal). `createByCurve(curve, keypoint)` -> Z runs ALONG the curve (verified: a sketch line
  pointing (1,1,1) yields Z = [0.577, 0.577, 0.577]); X is auto-orthonormal. So an arbitrary orientation
  needs a direction line (`sketch_add_3d_line`) to anchor on, not a bare coordinate.
- **Drive** (`RevoluteJointMotion`/`SliderJointMotion`/`CylindricalJointMotion`): `.rotationValue` (rad)
  / `.slideValue` (cm) - "Setting this value is the equivalent of using the Drive Joints command."
  Setting it poses the mechanism and is safe to call from the MCP server (a driven revolute joint keeps
  the connection live). This is `joint_drive`'s job; `joint_edit` changes only the joint definition
  (type/axis/snaps/limits) and redirects posing to `joint_drive`.
  `.rotationLimits`/`.slideLimits` -> `JointLimits` (`.isMinimumValueEnabled`/`.minimumValue`,
  `.isMaximumValueEnabled`/`.maximumValue`) to validate a commanded value against.
- **Motion link:** `rootComponent.motionLinks.createInput(jointOne, jointTwo)` -> `MotionLinkInput`
  (takes the TWO joints directly, NOT an `ObjectCollection`) -> `MotionLinks.add(input)` -> `MotionLink`.
  The ratio is set AFTER add via `MotionLink.setMotionData(motionOneType, valueOne, motionTwoType,
  valueTwo, isReversed)` - `motionOneType`/`motionTwoType` are **`JointMotionTypes` enum values**
  (`joint.jointMotion.jointType`), NOT the `JointMotion` objects themselves (confirmed live: passing the
  objects raises "Wrong number or type of arguments"). A k:1 ratio (joint_two moves k per unit of
  joint_one) is `valueOne=1, valueTwo=k`; `isReversed=True` links the motions in opposite directions.
  There is no `.ratios` property.

## Timeline objects

`Design.timeline` (`Timeline`): `.count`, `.item(i)` -> `TimelineObject` (`.name`, `.index`, `.isGroup`,
`.entity`, `.healthState`). The underlying feature/sketch/joint/etc. is `TimelineObject.entity`;
deleting it (`entity.deleteMe() -> bool`) removes the timeline object too - "Works for parametric and
non-parametric" designs. `deleteMe()` returning False (no exception) means Fusion declined; it does not
raise.

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
  it; do not try to re-find an open scope by name. (Direct designs need no scope.) The scope itself is
  opened via `Component.features.baseFeatures.add()` -> `BaseFeature` (`.startEdit()`/`.finishEdit()`).
- **Tessellation emits unwelded vertices.** `body.meshManager.createMeshCalculator().calculate()`
  returns one node per triangle corner — a box yields 24 nodes for 8 real vertices — so feeding its
  `nodeCoordinatesAsDouble`/`nodeIndices` straight to `addByTriangleMeshData` produces a topologically
  OPEN mesh: `MeshBody.isClosed` is False even for a watertight solid, and `mesh_to_brep` then refuses
  it. Merge coincident vertices (dedupe coordinates at a tight tolerance, remap the indices) before the
  add; the normals stay per-corner and geometry is unchanged. (`save_as_mesh` does this; see `_weld`.)
- **An open base-feature edit scope is undetectable from the public API** - `BaseFeature` has no
  `isEditing` property, so code cannot re-check "is the scope still open" after `startEdit()`; a
  recheck-after-open guard gives a false negative against a write that actually succeeded. Treat the
  open/close done via `try`/`finally` (`run_in_base_feature` in `design_mode.py`) as authoritative.
- **Mesh import:** `Component.meshBodies.add(fullFilename, MeshUnits, baseOrFormFeature)` ->
  `MeshBodyList`. `MeshUnits` enum members: `Millimeter`/`Centimeter`/`Meter`/`Inch`/`Foot` +
  `MeshUnit` suffix.
- **Mesh stats:** `MeshBody.displayMesh` -> `TriangleMesh` (`.triangleCount`/`.nodeCount`); `.mesh` ->
  `PolygonMesh` (`.triangleCount`/`.polygonCount`/`.nodeCount`); `.isClosed`/`.isOriented`/
  `.boundingBox`/`.entityToken`.
- **Reduce:** `Component.features.meshReduceFeatures.createInput(mesh)` -> `MeshReduceFeatureInput`.
  `.meshReduceTargetType` via `MeshReduceTargetTypes.{Proportion,FaceCount,MaximumDeviation}...`.
  **`.proportion` / `.facecount` (lowercase, confirmed live) / `.maximumDeviation` each need an
  `adsk.core.ValueInput`, not a bare float/int** - the live API rejects a raw number ("argument 2 of
  type Ptr<ValueInput>"); wrap every one in `ValueInput.createByReal(...)`. `.meshReduceMethodType`
  via `MeshReduceMethodTypes.{Uniform,Adaptive}...`.
- **Remesh:** `Component.features.meshRemeshFeatures.createInput(mesh)` -> `.add(inp)` regenerates a
  cleaner, more uniform triangulation in place.
- **Face groups:** `Component.features.meshGenerateFaceGroupsFeatures.createInput(mesh)` ->
  `input.method = MeshGenerateFaceGroupsMethodTypes.{Fast,Accurate}...`. Segments a mesh into planar
  face groups - a PRISMATIC `mesh_to_brep` convert REQUIRES this first, failing otherwise with
  `MESH_FAILED_BREP - Use Generate Face Groups`.
- **Plane cut:** `Component.features.meshPlaneCutFeatures.createInput(mesh, cutPlane)` -> input
  (`cutPlane` = a `core.Plane` OR a `ConstructionPlane`); `input.cutType =
  MeshPlaneCutTypes.{Trim,SplitBody,SplitFaces}...`; `input.fillType =
  MeshPlaneCutFillTypes.{NoFill,Minimal,Uniform}...`; `input.isFlipped = bool`. `split_body` only
  actually separates the mesh into two bodies when it is watertight - on a non-watertight mesh the cut
  applies but yields one body (the API does not split it).
- **Combine:** `Component.features.meshCombineFeatures.createInput(targetBody: MeshBody,
  toolBodies: list[MeshBody])` -> `MeshCombineFeatureInput`; `input.operation =
  MeshCombineOperationTypes.{Join,Cut,Intersect,Merge}...`; `input.algorithm =
  MeshCombineAlgorithmTypes.{Legacy,Enhanced}...` (default Enhanced - fewer triangles).
- **Convert to BRep:** `Component.features.meshConvertFeatures.createInput([mesh])` -> `.add(inp)`.
  `.meshConvertMethodType` via `MeshConvertMethodTypes.{Prismatic,Faceted,Organic}...` - Organic is
  gated behind the Product Design Extension (probe for `MeshConvertMethodTypes.
  OrganicMeshConvertMethodType` before offering it; refuse rather than silently falling back to
  another method). A non-watertight mesh (`isClosed=false`) has no closed volume to convert - refuse
  up front instead of letting `add()` fail opaquely.
- **Every mesh-feature `add()` above "returns nothing in the case where the feature is
  non-parametric"** (a DIRECT design OR an `add()` inside an open BaseFeature edit scope) - a `None`
  return in those modes IS success, not failure. Verify success by re-reading the mesh's own state
  (triangle count, face-group count, the new BRep body) rather than the feature object.
- **Mesh export factories:** `design.exportManager.createOBJExportOptions(geometry, filename)` /
  `createC3MFExportOptions(...)` / `createSTLExportOptions(...)` - all take `(geometry, filename)`;
  `geometry` may be a `BRepBody`/`MeshBody`/`Occurrence`/`Component`. **Exporting a bare `MeshBody` to
  a file: `execute()` returns True but WRITES NOTHING** (a Fusion limitation - export only tessellates
  a BRep to a file) - redirect the export to the mesh's `parentComponent` to actually get a file.
- **Tessellating a BRep body for export/save-as-mesh:**
  `brep_body.meshManager.createMeshCalculator().setQuality(TriangleMeshQualityOptions.
  <Low|Normal|High|VeryHigh>QualityTriangleMesh).calculate()` -> `TriangleMesh`
  (`.nodeCoordinatesAsDouble`/`.nodeIndices`/`.normalVectorsAsDouble`/`.normalIndices`/
  `.triangleCount`/`.nodeCount`). This step is read-only and needs no base-feature scope; only the
  following `addByTriangleMeshData` write does.

## Neutral CAD file export (design_export)

`design.exportManager.createSTEPExportOptions(fullPath, geometry)` / `createIGESExportOptions(fullPath,
geometry)` / `createSATExportOptions(fullPath, geometry)` all take `(path, geometry)`; **`createSTLExportOptions(geometry, fullPath)` reverses the argument order** to `(geometry, path)`. `geometry`
may be a `Component` (whole design = root component), an `Occurrence`, or a `BRepBody`.
`exportManager.execute(options) -> bool` - a truthy return is NOT proof a file was written; verify the
path exists and is non-empty before reporting success.

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
- **Creating projects/folders and uploading:** `app.data.dataProjects.add(name, purpose,
  contributors)` -> `DataProject`; `DataProject.rootFolder.dataFolders.add(name)` -> `DataFolder`.
  `DataFolder.uploadFile(fullPath)` -> `DataFileFuture` (`.uploadState` 0=processing/1=finished/
  2=failed; `.dataFile` is only populated once finished) - async, do not block on it.
- **Deleting a folder:** `data.findFolderById(id)` -> `DataFolder`; `DataFolder.deleteMe() -> bool`
  (parallel to `DataFile.deleteMe()` above; also guarded, irreversible).
- `DataFile` also exposes `.fileExtension`, `.versionNumber`, and `.latestVersionNumber` alongside
  `.id` / `.versionId` / `.fusionWebURL`.
- **Locating a file in the hub/project/folder tree from the file itself:** `DataFile.parentFolder` ->
  `DataFolder(.name, .id)`; `DataFile.parentProject` -> `DataProject(.name, .id, .parentHub ->
  DataHub(.name))`. Each read is defensive (a folder/project/hub field can fail on a cloud read) — an
  unsaved document has no `DataFile` yet, so this identity is null until the doc is saved.
- **Inserting a saved document as an occurrence:** `Component.occurrences.addByInsert(dataFile,
  Matrix3D, isReferencedComponent)` -> `Occurrence`. `isReferencedComponent=True` (an external
  reference) REQUIRES the source document to be in the SAME PROJECT as the host - Fusion enforces
  this itself and raises on mismatch; embedding (`False`) has no such constraint.
- **Hub switching:** `Data.activeHub` is documented GETTER-ONLY ("Gets the active DataHub") - there
  is no public setter. An assignment may raise or silently no-op, so verify the id actually changed
  before reporting success. A switch that DOES take effect closes every open document (Fusion
  reloads the data context) and invalidates open URNs (they are hub-scoped) - re-resolve after.
- **Document-level external references** (distinct from the per-occurrence
  `Occurrence.documentReference` used for CAM x-ref resolution above): `app.activeDocument.
  documentReferences` (iterable) -> `DocumentReference(.isOutOfDate, .version, .dataFile)`;
  `.getLatestVersion() -> bool` refreshes one reference to its newest cloud version.
- **Opening/inspecting a freshly-copied multi-reference CAM/Manufacture document via the API can
  crash Fusion (verified live, two crashes).** A `DataFile.copy`'d doc with several external
  references (an RFA model container + cloud part/machine refs) is unsafe to touch via
  `app.documents.openUsingContext` OR by walking its reference graph to "pre-warm" it - both crash
  the session (socket drops, server dies). The hazard is the heavy synchronous cloud
  reference-resolution itself, not one specific call, and CAM-ness can't be auto-detected without
  triggering it (inspecting the DataFile IS the crash) - the caller must declare intent so the API
  open path can be refused in favor of a manual UI open (`doc_open`'s `is_cam_template` flag).

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
- **`app.documents` is a SUPERSET of the user's visible tabs:** opening an assembly loads its
  referenced components as real `Document` objects too (`.isVisible=True` means loaded, not
  tabbed) — walk the whole collection when closing "everything", not just the visible tabs.
- **Creating a new document:** `app.documents.add(DocumentTypes.FusionDesignDocumentType) ->
  Document` makes it active, but `app.activeDocument is doc` can read False immediately after
  creation (the active reference resolves separately) — compare by `.name` instead of identity.
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
  exact-match check and, for files, a refusal driven by `DataFile.hasParentReferences` (bool) /
  `.parentReferences` (a `DataFiles` collection - force to override).
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

## Appearance override (appearance_set)

- `Design.appearances` (Appearances): `.addByCopy(appearance, name)` -> `Appearance` (a NEW editable
  instance — you cannot edit a library appearance in place), `.itemByName`, count/item.
- `Appearance.appearanceProperties` -> `Properties`; a `ColorProperty` has a settable `.value`
  (`adsk.core.Color`). An appearance can expose several properties by different localized names, so set
  every property whose runtime type is `ColorProperty` rather than looking up one by name.
- `adsk.core.Color.create(r, g, b, opacity)` — each channel 0-255.
- `BRepBody.appearance` / `Occurrence.appearance` are settable; assigning `None` removes the override.
  A `Component` has no single `.appearance` — set each of its `bRepBodies` individually.
- `Application.materialLibraries[i].appearances` is the fallback base-appearance source when the
  design itself has none yet to copy (a fresh design has no appearances until a body exists).

## Geometry handles (find_geometry / entityToken)

- `Occurrence.bRepBodies` / `body.faces` / `body.edges` / `body.vertices` are proxied into assembly
  context automatically when read off an `Occurrence`.
- `BRepFace.geometry.surfaceType` (Cylinder/Plane/Cone/Sphere/... via `adsk.core.SurfaceTypes`), `.area`,
  `.centroid`; a cylindrical face's geometry adds `.radius` + `.origin` + `.axis`.
- `BRepEdge.geometry.curveType` (Circle3D/Line3D/Arc3D/... via `adsk.core.Curve3DTypes`), `.length`; a
  circular/arc edge's geometry adds `.center` + `.radius`.
- **`entity.entityToken` is NOT a guaranteed-stable handle.** `Design.findEntityByToken(token)` resolves
  it back only while the token is still live — querying the SAME face/edge twice can mint a DIFFERENT
  token, and an older token can fail to resolve even with no model edit in between. Treat a token as
  short-lived: use it in the calls immediately after the query that minted it, and re-query rather than
  assuming a stale-handle failure means the geometry changed. `find_geometry` also mints a composite,
  self-healing handle (token + kind + a world-position locator) so a stale token can re-resolve to the
  same entity by geometry — see `_inputs.make_handle` / `_inputs._refind_by_locator`.

## Live API introspection (sys_get_api_doc)

The `adsk.core`/`adsk.fusion`/`adsk.cam`/`adsk.drawing`/`adsk.sim` Python wrapper modules are already
imported into the running Fusion process and each class's methods/properties carry real `__doc__`
strings and (for callables) inspectable signatures (`inspect.signature`). Searching them live via
`inspect.getmembers` means the docs always match the installed Fusion version with no bundled/hosted
database to go stale.

## Script execution (sys_execute_script: Python.Run / PTransaction)

- The script text is written to a temp file and run via `app.executeTextCommand('Python.Run "<path>"')`
  — a NESTED interpreter. `Python.Run` does not itself call the script's `run(context)`; the tool appends
  `run(None)` to the script text so it executes inside that nested interpreter.
- **An exception the script raises is caught INSIDE `Python.Run`** and comes back to the caller as a
  result STRING, not as a propagating Python exception — so a failing script's `except`/abort branch
  here usually does not fire. Wrapping the run in `PTransaction.Start`/`PTransaction.Commit` groups the
  script's changes into one timeline/undo step, but `PTransaction.Abort` only covers an error that
  escapes `Python.Run` itself (rare). Treat this as "grouped, undoable" — not "atomic, rolls back on
  error"; a partially-failed script's changes can still commit.
- `Python.Run`'s `RuntimeError` message on failure embeds both the add-in's own console log lines since
  the last call and the script's inner traceback — strip the console-noise lines and keep the LAST
  traceback block to surface just the script's own error.
- The Windows path passed to `Python.Run` must use forward slashes inside the quoted command string —
  backslashes are mis-handled there on both platforms, forward slashes work on Windows and macOS alike.

## Add-in reload (Script.stop/run, sys.modules cache)

- The add-in's own MCP server is torn down by `Script.stop()`, so calling it synchronously from inside
  an in-flight MCP request would kill the TaskManager/HTTP server before the response for THIS call
  could flush. The fix is a DEFERRED reload: schedule a `threading.Timer` that fires a dedicated
  `adsk.core.CustomEventHandler` on the main thread, outside any MCP call, which then does
  `Script.stop()` -> purge -> `Script.run()`.
- **`Script.run()` re-executes the add-in entry point but does NOT clear Python's import cache** — a
  bare re-run hands back the STALE cached module objects from `sys.modules`, so edits to already-loaded
  files never take effect (only brand-new files would appear). The fix is to delete every `sys.modules`
  entry whose `__file__` lives under the add-in's root folder before calling `Script.run()` again — this
  covers both import namespaces Fusion uses (`commands.mcpServer.*` and the `__main__<encoded-path>...`
  script namespace) while leaving `adsk.*`, the stdlib, and other add-ins untouched.
- `app.scripts.itemByPath(root)` (falling back to `itemsByName(folder_basename)`) locates the add-in's
  own `Script` object for the `stop()`/`run()` calls.

## Selection (ui.activeSelections)

- `ui.activeSelections` (`Selections`): `.count`, `.item(i)`, `.clear()`; `Selection.entity` / `.point`.
  Reading it never blocks — `ui.selectEntity()` WOULD block the main thread awaiting a user pick, so a
  selection-reading tool must poll `activeSelections` instead of calling it.
- Per-entity detail by runtime type: `BRepFace` (`.area`/`.centroid`/`.geometry`/`.body`), `BRepEdge`
  (`.length`/`.geometry`/`.body`), `BRepVertex` (`.geometry`/`.body`), `BRepBody`
  (`.name`/`.volume`/`.isSolid`/`.parentComponent`), `Occurrence` (`.name`/`.fullPathName`/`.component`),
  `Component`.
- A face/edge's outward DIRECTION for machining-axis or joint-origin use: a planar face's `.geometry.
  normal` (falling back to `face.evaluator.getNormalAtPoint(face.centroid)`), a cylinder/cone/torus
  face's `.geometry.axis`, a linear edge's `endVertex - startVertex`, a circular/arc edge's
  `.geometry.normal` (its plane normal = rotation axis). A sphere face has no single direction.

## Viewport / camera (view_inspect / view_screenshot / view_screenshot_multi)

- `Viewport.camera` -> `Camera(.eye, .target, .upVector, .viewOrientation, .isFitView, .cameraType)`,
  `.visualStyle` (`VisualStyles` enum), `.fit()`, `.refresh()`. `cameraType` is an enum: 0 =
  `OrthographicCameraType`, 1/2 = perspective variants (`PerspectiveCameraType` /
  `PerspectiveWithOrthoFacesCameraType`). `Viewport.saveAsImageFile(path, width, height) -> bool`
  re-renders the viewport to a PNG/etc. file at the given pixel size.
- **Setting `camera.viewOrientation` does NOT reliably move the camera's eye/target in this API flow**
  (confirmed live: assigning `FrontViewOrientation` left the eye sitting on the previous isometric
  vector). The reliable path is to set `eye`/`target`/`upVector` explicitly to exact world-axis unit
  vectors, then assign the camera back to the viewport once (a single move, not a partial one) and call
  `.fit()`. Convention (Fusion default, Z up): FRONT looks along +Y, TOP along -Z, RIGHT along -X.
  Force `cameraType = OrthographicCameraType` on the 6 true orthographic faces for zero perspective
  parallax.
- `Occurrence.isLightBulbOn` / `.isIsolated` / `.isVisible` / `.name` / `.fullPathName` drive
  isolate/show/hide. **A nested occurrence renders hidden if ANY ancestor occurrence's light bulb is
  off** — showing a leaf occurrence alone does nothing visible unless its whole `assemblyContext` chain
  is also lit.

## Section analysis (view_section)

- `Design.analyses.sectionAnalyses.createInput(cutPlaneEntity, distance_cm)` -> `SectionAnalysisInput`
  (`cutPlaneEntity` = a `ConstructionPlane` or a planar `BRepFace`; distance in CM, positive along the
  plane normal). `SectionAnalyses.add(input)` -> `SectionAnalysis` (`.flip`, `.isHatchShown`, `.name`,
  `.deleteMe()`).
- **Camera-aim convention:** a section keeps the +normal half, with the cut's interior faces toward
  +normal — so the REVEALING camera position sits on the +normal side (view direction `eye - target`
  equal to +normal); `flip=true` reverses which side is kept, so the revealing side reverses too.
  Without aiming the camera this way after a cut, it stays wherever it was — often on the solid (wrong)
  side, where the model looks uncut.

## Verifying a new tool

1. **Static:** stub `adsk.*` plus the repo packages in `sys.modules` (faithful temp-package
   tree), import the module, register it, and exercise the handler against stub objects —
   including the error paths. Run with Fusion's bundled Python (path in CONTRIBUTING.md).
2. **Live:** `sys_reload_addin` to load it, then drive it via a POST to `127.0.0.1:27182/mcp`
   (`tools/call`). Confirm against a real document.
