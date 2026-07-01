# Behavior Spec (generated)

_Auto-generated from the test suite by `tests/gen_spec.py`. Do not edit by
hand — every line below is pinned by a passing test. Re-run the generator
after changing tests._

**Tools with a test file:** 96  |  **Behaviors pinned:** 1863

## `_data_read`

> Unit tests for ``data_read.py`` — the project/file read cores behind data_get.

**ChildFolderByName**
- exact match
- case insensitive
- whitespace trimmed
- missing returns none
- robust to broken folder
**ProjectResolution**
- by name case insensitive
- by id
- missing identifier errors
- unknown project lists available
**WholeProject**
- lists all files recursively
- nested file records its path
**FolderScoping**
- scopes to named folder only
- folder is case insensitive
- nested folder path
- recursive true descends into subfolders
- recursive false immediate only
- stray slashes tolerated
- missing folder errors with hint
- missing nested segment names the level
**FileSummary**
- all fields populated
- empty folder path becomes project root
- broken getter yields none not crash
**Truncation**
- whole project truncates at max files
- recursive field always true for whole project

## `_sketch_detail`

> Unit tests for ``sketch_detail.py`` — read the full structure of one sketch.

**SubComponentResolution**
- finds sketch in active sub component
- unknown name lists sub component sketches
**Entities**
- lines indexed with construction flag
- circle geometry
- counts summary
**Constraints**
- perpendicular links two lines
- horizontal links one line
- coincident links point and entity
- constraint total
**Dimensions**
- dimension name value expr
**ConstraintState**
- reports fully constrained flag
- reports not fully constrained
- dimension driving flag
**EllipseAndPolygon**
- ellipse enumerated
- polygon lists all its lines
- constraint referencing ellipse resolves
**ArcAndPoint**
- arc center and radius
- point position
**DimensionTally**
- driving dimension count
- dimension with no parameter is safe
**VectorItems**
- count item collection expanded
- len getitem vector expanded
- single entity is not a vector
**UnknownConstraint**
- unknown class name derived
**Guards**
- missing sketch
- no name lists available
**Profiles**
- emits per profile records with handles
- sorted largest area first
- handle is a composite self healing token
- loop count distinguishes ring from region
- empty when no profiles
**ProgressiveDisclosure**
- default omits the heavy entity xray
- default points at the deeper level
- include entities adds the xray

## `active_component`

> Unit tests for the active-component targeting fix in sketch_core.py + model_extrude.py.

**SketchesTargetComponent**
- uses active component when present
- falls back to root when no active
- falls back to root when active is none
**ExtrudeTargetComponent**
- uses active component when present
- falls back to root

## `appearance_set`

> Unit tests for ``appearance_set.py`` — set a body/occurrence/component color.

**ParseColor**
- hex with hash
- hex without hash
- rgb triplet
- empty rejected
- bad hex length rejected
- non hex rejected
- out of range rgb rejected
- wrong rgb count rejected
- non integer rgb rejected
- negative component rejected
**Apply**
- color a body by name
- color a single face by handle
- body handle still colors the body
- long body name not mistaken for handle
- color an occurrence
- color a component applies to all bodies
- opacity passed through
- default appearance name from color
**Guards**
- bad color errors before touching design
- bad opacity errors
- no active design errors
- missing target errors
- no base appearance errors
- component with no bodies errors
- no editable color property errors
**ResolveExtra**
- component target applies to its bodies
- empty target is whole design
**BaseAppearanceFallback**
- falls back to material library when design has none

## `assembly_interference`

> Unit tests for assembly_interference — the physical-fit 'check my work' tool.

**OwningOccurrence**
- prefers parent component name
- falls back to assembly context then body name
**InterferenceHandler**
- reports pairs by occurrence with volume
- aggregates volume per pair
- clear when results empty
- short circuits under two occurrences
- pairs sorted by descending volume
- coincident flag echoed
- occurrences checked count
- owning name falls back when parent component name empty
- self pair note when same occurrence overlaps
- no design errors

## `assembly_joints_advanced`

> Unit tests for ``joints_advanced.py`` — assembly_capture_position, joint_create_as_built, assembly_constrain.

**CapturePosition**
- capture when pending
- capture with nothing pending errors
- status reports pending and count
- revert deletes latest snapshot
- revert with no snapshots errors
- unknown action
**AsBuiltJoint**
- rigid as built passes null geometry
- missing occurrence errors
- requires two distinct
- same local name different path is allowed
- same object twice still rejected
**AssemblyConstraint**
- missing occurrence errors
- resolves both occurrences
**AssemblyConstraintSnaps**
- snap specs resolve and build relationship
- snap carries offset value
- unresolvable snap errors
**MultiRelationshipConstraint**
- relationships list builds one constraint many rels
- per relationship flip respected
- single pair still works
- bad relationship item errors
- relationships must be a list
**ConstraintValueEncoding**
- offset scaled to cm
- offset inch scaling
- angle uses deg string not offset
- zero offset is real zero

## `assembly_probe`

> Unit tests for ``assembly_probe.py`` — structured kinematic state of an assembly.

**Guards**
- unknown units
**Probe**
- reports root bodies not just occurrences
- no root bodies is empty and no note
- positions scaled to display units
- ground flags and grounded list
- joint type and dof mapping
- rigid and cylindrical dof
- all motion types and dof
- unknown motion type is question mark with null dof
- positions scaled to cm and inch
- occurrence joint cross index
- include joints false skips
- as built joints are visible
- broken as built joint breaks health
**Health**
- all healthy
- broken joint surfaced
- suppressed joint is not broken
- stale joint health flagged when timeline is clean
- no stale flag when timeline also shows the error
- timeline problem surfaced
- health message deduped

## `assembly_transform`

> Unit tests for ``assembly.py`` â€” occurrence ground/move + rigid group.

**Ground**
- lock to parent
- unground from parent releases lock
- only sets ground to parent
- no grounded param is rejected by strict schema
- substring match
- missing occurrence errors
- no change requested errors
- ambiguous name refused not wrong instance
- exact full path targets the right instance
**Move**
- translate sets transform
- translation scaled to cm
- missing occurrence errors
- zero move errors
- rotate world axis
- multi axis rotation
- single and multi rejected together
- move jointed occurrence proceeds with warning
- quiet suppresses the jointed warning
- unjointed move has no warning
- rotate about edge handle
- combined rotate and translate preserves pivot
**RigidGroup**
- groups named occurrences
- include children flag
- needs at least two
- missing reported
- accepts a list not just comma string
- list with blank entries filtered
**MoveNote**
- jointed move note differs from free move

## `cam_create_operation`

> Unit tests for ``cam_create_operation`` — apply a CAM milling operation.

**Guards**
- no cam
- setup not found
- bad strategy
- tool ref out of range
- missing tool ref
**Create**
- creates operation with tool
- create then generate
- default generates
**DocumentToolScope**
- creates op from document library
- document index out of range
- empty document library
- document scope ignores url
- no ref at all errors

## `cam_create_setup`

> Unit tests for ``cam_create_setup.py`` — create a CAM (Manufacture) setup on a part.

**OperationType**
- default is milling
- turning
- unknown type errors
**ModelSelection**
- all root bodies when omitted
- named body
- body by handle
- missing named model errors
- no bodies at all errors
**NamingAndGuards**
- custom name
- blank name not assigned
- no cam product errors
**OutputFields**
- model count and names reported
- single body model count one
- setup creation failure reported

## `cam_delete`

> Unit tests for ``cam_delete`` — delete any CAM entity (setup / operation / folder / pattern).

**Guards**
- no cam
- requires entity
- not found
- ambiguous name
**Delete**
- delete operation
- delete folder
- delete nested pattern
- delete op nested in folder
- delete setup
- deleteme false is error

## `cam_edit_folders`

> Unit tests for ``cam_edit_folders`` — interrogate / create / rename CAM folders + move operations in.

**Guards**
- no cam
- unknown action
- setup not found
**List**
- lists folders with contents
**Create**
- create folder
- create requires name
**Rename**
- rename folder
- rename unknown folder
**Move**
- move ops into folder
- move unknown operation
- move requires ops and folder

## `cam_edit_operation`

> Unit tests for ``cam_edit_operation.py`` — set CAM operation parameters (feeds/speeds/stepdown/...).

**EditOperation**
- sets param dict
- accepts name equals value strings
- unknown param reported
- unknown operation
- invalid value reports and does not partially apply
- no parameters errors
- no operation name errors
- changed records evaluated value
**ParseParameters**
- string without equals errors
- string skips blank chunks
- non dict non string errors
**FindOperation**
- falls back to allOperations when operations missing
- unknown operation lists available names

## `cam_edit_setup`

> Unit tests for ``cam_edit_setup`` — generalized editing of a CAM setup.

**Guards**
- no cam
- setup not found
- nothing to do
- unknown parameter fails before applying
- bad body ref
**Parameters**
- sets wcs and stock params
- parameters accept string form
**Bodies**
- sets models
- sets fixtures and stock
- params and bodies together

## `cam_edit_tools`

> Unit tests for ``cam_edit_tools`` — read & manage CAM tool libraries + their tools.

**Guards**
- unknown action
- unknown scope
- target not found
- where used requires document
**List**
- lists tools
- list filters by tool type
- list libraries when no library given
**Add**
- add multiple
- add validates all refs before adding
- add requires refs
**AddRich**
- create from type
- create with description override and holder
- create with presets
- unknown from type errors before adding
- entry needs type or ref
**Remove**
- remove multiple high to low
- remove out of range
**Edit**
- edit parameters and persist document
- edit unknown parameter before applying
**WhereUsed**
- where used lists operations
**CreateLibrary**
- create empty local
- create with seeds
- hub descends to team folder
- refuses document scope
- requires name
- bad seed before import

## `cam_generate`

> Unit tests for ``cam_generate.py`` — launch/poll toolpath generation.

**FindTarget**
- matches setup by name ci
- matches operation
- matches folder
- unknown name returns none
- empty name returns none
**CollectOpHealth**
- warnings and errors separated
- empty toolpath derived from warning text
- warning text stripped
**GenerateHandler**
- whole document calls generate all
- target not found errors
- skip valid short circuits already valid operation
- skip valid false forces regen of valid op
**StatusHandler**
- no generations errors
- unknown handle lists active
- latest resolves to last handle
- stall warning when nothing generating but ood remains
- errored op surfaced while still generating
- setup error blocks via readiness
- pump budget is clamped

## `cam_get`

> Tests for `cam_get` — the CAM rich read (setups default + include= deeper slices).

**DefaultSlice**
- default returns setups only
- default note advertises remaining
**IncludeSlices**
- include adds the slice
- setup filter passes through
- multiple includes
**CamPointers**
- stale ops point at cam generate
- out of date machine points at edit setup
- clean setup gets no pointers
- sums across setups
- router emits pointers on stale default
**OrientationDedup**
- default keeps setup invalidation reasons
- operations drops setup reasons keeps context
- unrelated include keeps setup reasons
**Guards**
- unknown include errors
- no cam data guard
**OperationRazor**
- healthy op collapses
- abnormal op keeps its flags
**Bounding**
- operations capped and flagged
- nc programs summarizes post parameters
**LibrarySlice**
- router includes library and passes scope
- slice delegates to read library
- templates slice delegates with location
- templates slice defaults location and depth
**DeepZoom**
- parameters requires operation
- tool requires operation
- grouped visible params sections and filters
**NormalizeInclude**
- comma string
- list lowercased
- none empty

## `cam_reorder`

> Unit tests for ``cam_reorder`` — reorder a CAM operation/folder/pattern before or after another.

**Guards**
- no cam
- bad position
- entity not found
- reference not found
- entity equals reference
**Reorder**
- move after
- move before
- reorder nested entity
- move declined is error

## `cam_select_geometry`

> Unit tests for ``cam_select_geometry`` — set the machining geometry (and optional heights) on a CAM operation, then optionally regenerate.

**Guards**
- bad selection
- no cam
- op not found
- handle resolve error propagates
**CurveSelection**
- chain applies and sets knobs
- pocket uses pocket builder and ignores chain knobs
- zero selections is error
**Holes**
- holes sets holefaces directly
- diameter filter keeps in range
- diameter filter empty is error
- holes on nonhole op errors
- holes on bore uses circularFaces
- holes prefers holeFaces when both absent irrelevant
**Heights**
- sets mode and offset
- heights set before selection
- missing height param errors
**Generate**
- generate waits on future and reports valid
- empty no warning reports observed state and causes
- warning is surfaced
- generate false skips

## `cam_set_nc_comment`

> Unit tests for ``cam_set_nc_comment.handler`` — the empty-input guard and multi-program behaviour.

**EmptyInputGuard**
- empty comment and no set name is refused
- whitespace only comment no set name refused
- real comment goes through
- set name only is allowed
**MultiProgramPreValidation**
- uneditable program aborts before any write
- all editable applies to all
**Quoting**
- quote wraps in single quotes
- quote escapes embedded apostrophe
- unquote strips matching quotes
- unquote leaves unquoted string
- unquote none is none
- quote unquote round trip
**ProgramTargeting**
- targets only named program
- before after reported unquoted
- unknown program lists available
- no nc programs errors
- comment and name both set
- uneditable name aborts before any write

## `cam_templates`

> Unit tests for ``cam_templates.py`` navigation logic.

**FindTemplateByName**
- unknown location is rejected
- finds template in root
- match is case insensitive
- descends into subfolders
- not found returns none
**AsCamTemplate**
- passthrough when already a template
- recovers template from a list result
- recovers template from a collection result
- returns none when no template present
**WalkLibrary**
- asset url paired to template by stem
- template without matching asset gets none url
- descends and reports nested templates
- depth limit flags folders truncated
**SaveOperationsValidation**
- missing template name
- missing operations list
- setup not found lists available
- missing operations named in error

## `cold_start_onboarding`

> The cold-start front door: a contextless agent must be routed to the orientation tools.

**ServerInstructions**
- instructions returned on initialize
- instructions route to both orientation tools
**NaiveOnboardingSearchHitsFrontDoor**
- getting started surfaces orientation
- help surfaces capability map
- overview and start here surface front door

## `common`

> Unit tests for the shared tool helpers (tools/_common.py).

**ResponseBuilders**
- ok wraps payload as json text
- error sets flag and mirrors message
- underscore aliases are gone
**Safe**
- returns value
- swallows exception returns default
**Scale**
- known units
- default is mm
- unknown unit is none
- case and whitespace insensitive
**TargetComponent**
- returns active component when set
- falls back to root when no active

## `data_get`

> Tests for `data_get` — the cloud rich read (hub/projects/folders/files), scope-driven.

**ScopeDispatch**
- default lists projects
- project lists files
- project with folders shows tree
- include hubs lists hubs
- folder path passed to files
**Guards**
- unknown include errors
- cloud error propagates

## `data_management`

> Unit tests for the former ``data_management`` tools — pure string/tree logic + handler guards.

**SplitPath**
- empty is no segments
- simple path
- backslashes normalized
- stray and leading trailing slashes dropped
- segments are trimmed
**ResolveFolderPath**
- empty segments resolves to root
- full existing path resolves
- case insensitive match
- missing segment reported
**FolderPathString**
- builds slash path excluding root
- immediate child of root
**AgentDescription**
- prefixes marker
- idempotent no double prefix
- empty is just the marker
**SaveDocument**
- save tags description with marker
- refuses never saved doc
- no active document
**CloseDocument**
- closes active by default discarding
- close named
- close all
- unmatched name errors
- close failure reported in errors
**ActivateDocument**
- activate taken reports true
- activate async pending reports pending not true
- requires name
- unmatched errors
**FindOpenDocument**
- exact then substring
- substring when no exact
**DeleteFolderGate**
- empty folder deletes without recursive confirm
- nonempty force without recursive confirm returns preview and refuses
- nonempty with recursive confirm deletes
- recursive confirm must match name
- subtree counts walks recursively
**CreateProject**
- creates and reports id
- blank name errors
- duplicate name refused
**CreateFolder**
- creates at root
- mkdir p reports auto created parents
- duplicate in same parent refused
- missing project lists available
- requires project identifier
**UploadFile**
- file not found errors
- requires project
- upload state finished maps to word
- upload state processing and unknown
- existing nested folder target
- missing folder without create path errors
- create path makes missing folders
**ListFolders**
- lists tree with paths
- max depth clamped to at least one
- invalid max depth defaults

## `data_switch_hub`

> Unit tests for ``data_switch_hub.py`` — list Autodesk data hubs and switch the active one.

**List**
- lists all hubs with active flag
- default action is list
- unnamed hub gets placeholder
- single hub is active
**Switch**
- switch by name
- switch by id
- switch case insensitive name
- already active is noop
- unknown hub errors and lists available
- switch requires hub
- unknown action errors
**SwitchGetterOnly**
- silent noop setter reports honest error not false success
- raising setter reports honest error

## `design_configure`

> Unit tests for ``design_configure`` — the configured-design build+switch tool.

**Guards**
- unknown action
- no active design
- column action requires configured design
**Create**
- create converts design
- create is idempotent when already configured
- create refuses unsaved document
- create proceeds when saved
**AddConfiguration**
- add row
- add row requires name
**RenameConfiguration**
- rename changes row name
- rename unknown row errors
- rename requires both names
- rename to existing name errors
**AddParameter**
- param column and expressions by row name
- missing parameter errors
- value for unknown row is reported
**SuppressVisibility**
- suppress sets is suppressed
- visibility sets is visible
**AppearanceTheme**
- appearance adds column before rows then links
**AddInsert**
- insert and map each config by name
- unknown part errors
- map to unknown part config errors
- map to unknown assembly config errors
- insert config defaults to first part row

## `design_delete_feature`

> Unit tests for ``design_delete_feature.py`` — delete one timeline feature by name.

**HealthHelper**
- rolls up errors and warnings
- none timeline empty
**FindByName**
- exact match preferred over substring
**Delete**
- deletes named feature
- substring match
**Guards**
- empty feature errors
- no active design errors
- direct design no timeline errors
- missing feature errors
- ambiguous name refused
- group refused
- delete me false reported
- no entity guard
- preexisting warnings surface without new error
- downstream error after delete reported

## `design_delete_occurrence`

> Unit tests for ``design_delete_occurrence.py`` — delete one component occurrence.

**TimelineHealthHelper**
- rolls up errors and warnings
- no timeline is empty
**JointNamesHelper**
- lists joint names
- none when no joints
**Delete**
- deletes named occurrence
- substring match
- reports removed joints
- no joints warning when unjointed
- reports grounded state
**Guards**
- no active design errors
- missing occurrence errors
- empty occurrence errors
- ambiguous name refused not wrong instance
- exact full path targets right instance
- delete me false reports pattern child
- timeline error after delete is reported

## `design_export`

> Unit tests for ``design_export.py`` — export a body/component/whole-design to a neutral CAD file.

**FormatDispatch**
- step uses step options
- iges uses iges options
- sat uses sat options
- stl uses stl options
- unknown format errors
**TargetResolution**
- whole design when no target
- body by name
- body by handle
- long body name not mistaken for handle
- missing named target errors
**PathHandling**
- missing path errors
- extension auto appended
**SplitByComponent**
- one file per occurrence
- filenames sanitized and extensioned
- duplicate stems disambiguated
- no occurrences errors
- partial failure records failed list
- all fail exported false
**Sanitize**
- drops instance suffix
- keeps safe chars
- swaps illegal chars
- empty becomes part
- all illegal becomes part
**ExportOne**
- stl arg order is geom then path
- non stl arg order is path then geom
- execute false is a failure
- exception captured as error string
**ResolveTargetExtra**
- handle resolving to non body is not found
- no active design errors

## `design_get`

> Tests for `design_get` - the first RICH READ (one tool, default slice + include= deeper slices).

**DefaultSlice**
- default is the dense orientation
- default omits noise when healthy
- default emits pointers for hidden content
- default no pointers when only obvious content
- default points at cam when cam present
- default surfaces health detail when unhealthy
- default note advertises remaining slices
**IncludeSlices**
- include adds the slice
- include mode adds full capability map
- multiple includes
**Fingerprint**
- counts both joint collections
- as built only still counts
- counts user parameters
- zero counts omitted
**ContentPointers**
- parameters present points at param tools
- obvious classes get no pointer
- only present classes pointed
- empty contents no pointers
**HasCam**
- cam present
- no cam
- no parent document is safe
**TimelineSlice**
- entity type group
- entity type class name
- object summary maps health label
- object summary message only when present
- slice returns all with marker count
- slice include suppressed false
- slice group filter
- slice summary states and exceptions
- slice no timeline errors
**TimelineRazor**
- healthy row drops noise
- abnormal row keeps its flags
- tree scope params pass through
- configurations degrades for non configured design
**Guards**
- unknown include errors
- no active design guard
**NormalizeInclude**
- none empty
- comma string
- list lowercased
**RootBodies**
- root body names lists direct bodies
- no root bodies returns empty

## `design_mode`

> Unit tests for design_mode.py — get_mode_handler (design_get's mode slice) / design_set_mode / model_base_feature.

**GetMode**
- no active design
- reports parametric and capabilities
- reports direct and capabilities
- counts base features
- in base feature edit true when editing
- capability map matches modeguard
**SetMode**
- no active design
- bad target
- parametric to direct refused without confirm
- parametric to direct succeeds with confirm
- direct to parametric is free
- idempotent noop when already target
- assignment exception surfaces not swallowed
**BaseFeature**
- no active design
- refused in direct names parametric
- bad action
- start opens a scope
- start names the base feature
- start errors and cleans up when startEdit returns false
- finish closes the captured open scope
- finish closes multiple captured scopes
- finish named also closes an enumerable feature
- finish unknown name is not an error
- finish no open scope is idempotent
- finish works while design reads direct
**BaseFeatureWrapper**
- inner op runs inside scope and scope finishes
- scope finishes in finally when inner raises
- open scope error short circuits before any scope
- startEdit false in wrapper errors without running inner
**RunInBaseFeature**
- direct runs inner directly with no scope
- parametric runs inner inside atomic scope
- parametric finishes in finally when inner raises
- parametric open failure returns error not crash
**ActivateComponent**
- no active design
- activate by occurrence name
- activate by component name
- unknown component errors and lists
- activate root via empty
- activate root falls back to deactivate
- activate returns false errors

## `design_ops`

> Unit tests for ``design_ops.py`` — the whole-design timeline tools split out of parameters.py.

**TimelineHealthHelper**
- rolls up errors and warnings
- no timeline is empty
**HealthHandler**
- reports healthy
- reports errors
- no active design errors
**RecomputeHandler**
- recomputes and reports health
- compute failure is an error
- no active design errors

## `doc_get`

> Tests for `doc_get` — the session rich read (active doc identity + open-doc list).

**ActiveIdentity**
- saved doc surfaces urn and state
- unsaved doc has no urn
- modified doc flags stale urn
**OpenList**
- terse healthy doc collapses
- summary leads with unsaved exceptions
- modified dependency doc keeps its flag
**Guards**
- no active document errors

## `doc_insert_occurrence`

> Unit tests for ``insert_occurrence.py`` placement transform.

**Placement**
- default identity
- position scales to cm
- rotation built
- bad units
- bad rotate axis
**ResolveDataFile**
- plain urn resolves directly
- urn extracted from surrounding text
- web url base64 segment decoded
- unresolvable returns none
**B64UrlDecode**
- roundtrip
- garbage returns none
**FindComponent**
- empty name returns root
- root name returns root
- match by occurrence name
- match by component name
- unknown returns none
**FindChildOccurrence**
- match by occurrence name
- match by component name
- no match returns none
**HandlerGates**
- empty document id errors
- no active design
- unresolvable document errors
- component not found errors
- remove existing missing errors
- addByInsert returns nothing errors
- remove existing then insert

## `doc_lifecycle`

> Unit tests for the two CLOUD-COPY handlers in doc_lifecycle: save_document_as_handler (Document.saveAs of the active doc) and copy_document_handler (DataFile.copy of a saved file).

**SaveDocumentAs**
- requires name
- requires destination project
- no active document
- unknown project lists available
- missing folder without create path errors
- saves to root and tags description
- create path makes nested folders
- document id null until urn assigned
- document id surfaced when urn
- saveas false return is an error
- resolves project by id
**CopyDocument**
- requires a source
- requires destination project
- unknown document id errors
- copy by id into root reports xrefs
- copy applies requested rename
- duplicate name in destination refuses
- copy by name needs source project
- create path makes nested destination
- copy by name resolves source in named project
- copy by name unknown source project errors
- copy by name missing file lists seen
- copy returning nothing is an error
- rename failure surfaces warning not error
**DeleteDocument**
- requires document id
- requires confirm name
- unknown file errors
- name mismatch refuses
- open file refused
- referenced file refused without force
- referenced file deleted with force
- unreferenced file deleted
- confirm name whitespace forgiven
- delete me false reported
**DeleteHelpers**
- is document open true when matching
- is document open false when absent
- is document open empty id false
- parent ref summary empty when no refs
- parent ref summary lists refs
- xref summary empty when no children
**NewDocument**
- creates and reports active
- add returning nothing is an error

## `doc_open`

> Unit tests for ``doc_open.py`` identifier parsing.

**B64UrlDecode**
- decodes real urn segment
- restores missing padding
- invalid base64 returns none
**UrnCandidates**
- bare urn is first candidate
- extracts urn from web url
- urn with version suffix kept as is
- candidates are deduped
- plain garbage yields only itself
**CamTemplateGuard**
- refuses api open and does not resolve or open
- normal open requires force api open
- bare open refuses without declaring intent
- cam flag wins over force

## `edit_joint`

> Unit tests for ``joint_edit`` — edit an EXISTING joint in place (no remaking).

**FindAndGuards**
- unknown joint errors
- no edits requested errors
**RollTo**
- rolls before then after
**Flip**
- set flip
- unset flip
**Motion**
- change to slider on axis
- change to rigid
- unknown joint type errors
**OffsetAngle**
- offset sets expression with units
- offset inch units
- angle sets degrees expression
- offset counts as an edit
- unknown units errors
**Limits**
- rotation limits enable and set radians
- rotation rest value
- linear limits on slider in cm
- linear rest value
- rotation limit on slider errors
- linear limit on revolute errors
**WorldAxis**
- world axis uses custom construction axis
- world axis without type reuses current
- unknown world axis errors
**RotationDriveRefused**
- rotation deg is refused with redirect
**ReselectInputs**
- reselect joint origin name inputs
**AutoRecompute**
- edit runs computeAll
- reports downstream errors after recompute

## `find_geometry`

> Unit tests for ``find_geometry.py`` — query geometry, return stable handles.

**Guards**
- unknown units
- unresolved target
**Find**
- returns handles and scaled positions
- kind filter cylinder only
- radius filter
- nearest to sorts
- every match has a handle
**NestedAssembly**
- nested occurrence resolved by full path
- nested also reachable by local name
- whole design includes nested and root bodies

## `handle_resolution_uniform`

> Lint + behavioural anchor: handle resolution is UNIFORM across every InputKind.

**FindEntityByTokenIsCentralised**
- findentitybytoken called only inside resolve token entity
- no tool guesses handle vs name by length
**EveryHandleKindAcceptsACompositeHandle**
- geometry handle
- body ref
- plane ref
- axis ref

## `inputs`

> Unit tests for the typed INPUT KINDS framework (_inputs.py).

**GeometryHandle**
- resolves planar face
- rejects wrong geometry kind
- stale handle error
- contract note names the required kind
- schema includes contract note
**IsHandle**
- composite handle
- long bare token
- int and index selectors are not handles
**SelfHealingHandle**
- live token resolves via fast path
- stale token recovers via locator
- stale token no matching geometry errors
- bare token still works
**GeometryHandleList**
- resolves list of edge handles
- accepts comma string
- one bad handle fails with index
- wrong kind in list rejected
- empty optional returns empty list
- schema is array
**BodyRef**
- resolves a handle
- resolves a short name
- long name is NOT mistaken for a handle
- unresolvable reports name guidance
**PlaneRef**
- origin alias
- alias front maps to xz
- construction plane by name
- planar face handle
- curved face handle rejected
- unknown string
- long construction plane name not mistaken for handle
- contract note mentions all three sources
- non string raw does not crash
- composite face handle resolves
**AxisRef**
- world axis
- edge handle axis
- curved edge rejected
- unknown axis string
- composite handle resolves via token
- non string raw does not crash
**DistanceUnits**
- distance scaled by units
- distance nonzero guard
- unit field returns scale
- unknown unit
- unit field schema emits enum
**SharedInputs**
- as property splats name and schema
- units property factory
- boolean op subset
- world axis
- joint motion full set
- joint motion subset preserves capability difference
**Choice**
- valid option
- invalid option
- default when empty
- schema emits enum
- schema enum with default notes it
**ResolveInputs**
- resolves all with unit dependency
- first failure short circuits
**Generation**
- contract block lists each input
- apply to tool adds properties and required
**BodyKind**
- default kind is any for backcompat
- solid kind resolves a solid
- solid kind rejects a surface with redirect
- solid kind rejects a mesh with redirect
- surface kind resolves a surface
- surface kind rejects a solid
- mesh kind resolves a mesh
- mesh kind rejects a brep solid
- mesh resolves by name from meshBodies
- mesh by name searches occurrence meshBodies
- any kind accepts solid surface and mesh
- list kind checks every element before returning
- list all correct kind resolves in order
- surface list alias
**ModeGuard**
- current design type reads parametric
- current design type reads direct
- current design type unknown when unreadable
- parametric guard passes in parametric
- direct guard fails in parametric
- error names the REQUIRED mode not inverted
- parametric guard error names parametric
- base feature guard passes inside a base feature scope
- base feature guard fails without scope
- contract note
**ProfileRef**
- resolves a handle first
- handle to non profile rejected
- legacy selector by sketch and index
- legacy selector blank sketch uses most recent
- legacy index out of range
- legacy unknown sketch
**ProfileRefList**
- resolves handles in order
- order is PRESERVED not sorted
- duplicates are NOT deduped
- mixed handles and legacy selectors
- one bad element fails with index
**OccurrenceRef**
- exact fullpathname wins
- exact name resolves when unique
- ambiguous name is REFUSED not guessed
- unique substring resolves
- miss lists available paths
- required blank errors
**OccurrenceRefList**
- resolves each by path in order
- comma string accepted
- one ambiguous element fails whole list
**TargetRef**
- empty is whole design
- handle to body
- handle to face
- handle to mesh
- occurrence by fullpath
- component by name
- body by name
- allow restricts kind
- unresolvable errors

## `joint_at_geometry`

> Unit tests for ``joint_at_geometry.py`` — joint two parts at geometry handles.

**JointGeometryRules**
- cylinder face uses MIDDLE not center
- cone face also uses middle
- planar face uses CENTER
- circular edge uses center
- line edge uses middle
- vertex uses point
**Handler**
- unknown motion
- unresolved handle
- revolute forced world axis
- revolute auto axis uses geometry axis
- slider auto axis from geometry
- reports health warning when joint fails to compute
- healthy joint no warning
- rigid motion has null axis
- ball motion uses two world directions
- slider forced world axis
- cylindrical forced world axis
- auto axis with no geometry axis falls back to world z
- unknown axis keyword defaults to world z
- circular edge is an axis entity for auto
- motion setter failure reports error

## `joint_create_edit`

> Unit tests for ``joint.py`` pure logic.

**FindJointOrigin**
- empty name returns none
- root jo returned directly
- name is trimmed before lookup
- not found anywhere returns none
**ApplyMotion**
- rigid
- slider uses axis index
- unsupported type reports error
**ApplyLimits**
- rotation in radians
- linear in cm
- rest values
- rotation on slider errors
- linear on revolute errors
**ResolveInputHandle**
- handle resolves to joint geometry at real face
- non token falls through to jo name
- unresolvable spec errors naming all paths
**FmtNum**
- whole number drops trailing zero
- fractional kept
**JgFromEntity**
- planar face center
- cylinder face middle not center
- circular edge center
- line edge middle
- vertex uses point
- unsupported entity errors
**CurrentJointType**
- maps each motion class
- unknown class is empty
- no motion is empty
**WorldAxisEntity**
- picks axis by index
**FaceExtentAndPlanar**
- extent projects onto axis
- no bbox returns zeros
- is planar true only for surface type zero
**CreateHandler**
- requires both inputs
- unknown joint type errors
- unknown axis errors
- unknown units errors
- revolute dispatches axis and echoes axis field
- rigid has null axis field
- ball has null axis field
- ball uses valid pitch and yaw directions
- offset scaled to cm on value input
- offset inch scaling
- angle converted to radians
- flip sets is flipped
- no offset angle reported as none

## `joint_create_origin`

> Unit tests for ``joint_origin.py`` pure logic.

**Vec**
- rounds components
- none passthrough
**KpName**
- known value maps to name
- unknown value falls back to str
**GeometryFromArgsValidation**
- sketch line without sketch name errors
- sketch point without sketch name errors
- sketch line missing sketch errors
- sketch line index out of range errors
**GeometryAnchor**
- planar face uses createByPlanarFace
- cylinder face uses nonplanar
- edge uses createByCurve
- vertex uses createByPoint
- bad geometry handle errors

## `joint_drive`

> Unit tests for ``joint_drive`` — the Drive Joints command (set a joint's value).

**Guards**
- no value given errors
- unknown units
- joint not found
- as built joint is drivable by name
- rigid is refused
- slider rejects angle
- revolute rejects distance
**RevoluteDrive**
- angle set in radians
- value read back in degrees
- over limit warns
**SliderDrive**
- distance set in cm
- distance read back in mm
- inch distance
- below min warns
**CylindricalDrive**
- drives both values
- cylindrical angle only

## `joint_motion_link`

> Unit tests for joint_motion_link — couple two joints with a ratio (the Motion Link command).

**FindJoint**
- exact then case insensitive
**HandlerGuards**
- requires both names
- rejects same joint
- unknown joint lists available
- no design
**LinkCreation**
- createInput gets two joints not a collection
- ratio flows through setMotionData
- default ratio is one
- negative ratio links reversed with magnitude
- zero ratio rejected
- non numeric ratio rejected
- numeric string ratio accepted
- ratio failure rolls back link and errors
- bad joint dof gets actionable hint

## `joint_snaps`

> Unit tests for the matured ``joint.py`` autonomous geometry-snap resolver.

**ParseSnap**
- plain name is joint origin
- occurrence with snap
- occurrence with top snap
- cylinder snap
- unknown suffix not treated as snap
- occurrence colon in name without snap
**PickFace**
- top picks highest face
- bottom picks lowest face
- center picks largest planar face
- top bottom skip nonplanar cylinder wall
**DirectionalFaces**
- right is max x
- left is min x
- back is max y
- front is min y
- top is max z
- bottom is min z
- directional snaps parse
- empty body returns none

## `main_thread_timeout`

> Unit tests for main-thread task timeout correctness.

**CancelReturnsWhetherItWon**
- cancel pending task returns true
- cancel already claimed task returns false
- cancel empty id returns false
**ReapStale**
- reaps tasks older than ttl
- reap is a noop when all fresh
- missing created stamp is not reaped
**ItemEnforceTimeoutFlag**
- defaults to enforced
- can opt out
- execute api script item is timeout exempt

## `mesh_combine`

> Unit tests for ``mesh_combine.py`` — boolean join/cut/intersect/merge of MESH bodies.

**Operations**
- join
- cut
- intersect
- merge
- multiple tool bodies
- comma string tools parsed
**Algorithm**
- default enhanced
- legacy
**MeshKindEnforcement**
- brep in tools list redirected no mutation
- brep target redirected
**SameBodyGuard**
- target in tools rejected
**BaseFeatureRouting**
- direct no scope
- parametric opens and finishes scope
**ChoiceGuards**
- bad operation rejected
- bad algorithm rejected
**MutationSurfaces**
- add failure surfaces
- create input raise surfaces
- create input none surfaces
**NoCollection**
- missing mesh combine features errors
**NonParametricSuccess**
- none feature is success via target body
- none feature in parametric scope is success
**Result**
- reports result bodies
- no design errors

## `mesh_edit`

> Unit tests for ``mesh_edit.py`` — the WRITE-half mesh tools (mesh_generate_face_groups, mesh_plane_cut) plus the mesh_to_brep face-groups hint.

**FaceGroups**
- direct generates without scope
- fast method resolves enum
- parametric routes through base feature scope
- direct does not open a scope
- add failure surfaces not swallowed
- none feature with face groups is success
- none feature in parametric scope is success
- brep handle rejected with redirect
- missing features collection errors
- create input none errors
- create input raise surfaces
**PlaneCut**
- trim with construction plane handle
- each cut type resolves enum
- each fill resolves enum
- origin alias plane resolves
- split body reports two bodies
- split body became split true when count increases
- split body became split false when count unchanged
- trim has no became split signal
- split faces has no became split signal
- flip sets is flipped
- parametric routes through base feature scope
- add failure surfaces
- none feature is success via mesh body set
- none feature in parametric scope is success
- brep handle rejected with redirect
- missing features collection errors
- planar face handle reduced to its geometry
- create input none errors
**MeshToBrepHint**
- prismatic convert failure mentions face groups tool
- faceted convert failure omits the hint

## `mesh_export`

> Unit tests for ``mesh_export.py`` â€” the mesh-aware export (OBJ/3MF/STL) and the BRep->MeshBody tessellation (save_as_mesh).

**ExportFormatDispatch**
- obj uses obj options and executes
- 3mf uses c3mf options
- stl uses stl options
- default format is 3mf
- bad format rejected by choice
**ExportTarget**
- whole design when no target
- body by name
- mesh body by handle redirects to its component
- mesh body by name redirects to its component
- false success when no file written is error
- brep target that writes a file still succeeds
- component name fallback target
- occurrence by name target
- occurrence by full path target
- missing named target errors
- missing path errors
**ExportRefinement**
- refinement applied when supported
- bad refinement rejected
- refinement not applied still reports requested key
**ExportSplitByComponent**
- one file per occurrence
- filenames sanitized
- duplicate stems disambiguated
- no occurrences errors
- split reports per occurrence failure without aborting
- split all fail reports not exported
**SaveAsMesh**
- direct tessellates adds mesh no scope
- parametric routes through base feature scope
- quality passed to calculator
- optional name renames the mesh
- bad quality rejected
- mesh source rejected
- calculate failure surfaces
- add failure surfaces not swallowed
**Weld**
- box corners merge 24 to 8
- distinct vertices are preserved
- malformed input returned unchanged

## `mesh_ops`

> Unit tests for ``mesh_ops.py`` — the MESH environment (adsk.fusion.MeshBody).

**MeshGet**
- lists meshes with counts
- empty when no meshes
- no design errors
- named component scopes to that component
- unknown component name errors
- dedup same mesh listed once
**MeshMeasure**
- measures a mesh body
- non watertight carries warning
**MeshInsert**
- gates on base feature scope in parametric when scope cannot open
- parametric succeeds even when scope is invisible to a guard
- works in parametric with visible scope
- works in direct without scope
- bad extension rejected
- missing file rejected
- named target component imports into it
- unknown target component errors
- unknown units rejected
- empty import result errors
- import failure surfaces not swallowed
**MeshReduce**
- proportion reduces and reports pct
- proportion out of range rejected
- facecount sets lowercase field as valueinput
- facecount below one rejected
- max deviation sets valueinput scaled to cm
- add failure surfaces
- non numeric value rejected
- max deviation below zero rejected
- missing reduce features collection errors
- create input none errors
- slow note for large source mesh
- no slow note for small mesh
- none feature is success in place
- parametric routes through base feature scope
**MeshRemesh**
- remesh reports before after
- missing remesh features collection errors
- none feature is success in place
- parametric routes through base feature scope
**MeshToBrep**
- prismatic converts and reports method
- non watertight refused up front
- organic without extension is honest error
- conversion add failure surfaces
- none feature with new brep body is success
- none feature with no new body is real failure with hint
- parametric routes through base feature scope
- brep handle to convert is redirected
- missing convert features collection errors
- create input none errors
- faceted method resolves enum

## `model_arrange`

> Unit tests for ``arrange.py`` — pack shapes within a sketch-profile boundary (Arrange feature).

**SolverType**
- true shape default
- rectangular
- unknown solver errors
- rect alias resolves to rectangular
- true alias normalizes in payload
- solver case insensitive
**Boundary**
- named sketch profile used as envelope
- missing boundary errors
- boundary with no profile errors
**Shapes**
- each shape added as component
- missing shape reported
- no shapes errors
- feature created
**Spacing**
- spacing scaled to cm
- spacing inches scaled to cm
- zero spacing not set and reported zero
- unknown units errors

## `model_combine`

> Unit tests for ``combine.py`` — boolean join/cut/intersect of solid bodies.

**Guards**
- unknown operation
- target not found
- no tools
- tool not found
- tool same as target
**Combine**
- join sets operation
- cut sets operation
- intersect sets operation
- operation case insensitive
- multiple tools all added
- bodies remaining reports count
- keep tools defaults false
- comma string tools parsed
- keep tools flag
- new component flag

## `model_compute_holder`

> Unit tests for ``model_compute_holder.py`` + the pure half of ``_holder.py``.

**BuildHolderData**
- segment units cm to mm
- multiple segments in order
- metadata passthrough
- guid and reference guid match
- empty profile gives no segments
**Handler**
- happy path returns segments and json no library
- name defaults to active document
- no active design errors
- axis not an axis errors
- invalid end datum errors
- empty profile errors
- bad body handle errors

## `model_construction`

> Unit tests for ``construction.py`` — point / axis / plane construction datums.

**Guards**
- unknown units
- unknown kind
- bad axis
- bad plane
- direct modeling env error is friendly
**Construction**
- point scales coords
- axis direction and origin
- plane offset from named plane
- point scales inches
- axis through field reports raw coords
- point at field reports raw coords
- custom name applied
- generic exception is reported
**ParametricConstraint**
- point at coord refused in parametric
- world axis at coord refused in parametric
- point at coord works in direct
- edge axis uses setByEdge and works in parametric
- offset plane works in parametric

## `model_create_component`

> Unit tests for ``model_create_component.py`` — make a new empty component occurrence.

**CreateComponent**
- creates component
- names the component
- rejected rename surfaces warning not false success
- placed at position scales to cm
- no position uses identity
- activate makes it edit target
- no activate by default
- unknown units errors
- orientation rotation
- rotation angle converted to radians
- rotation origin scaled to cm
- rotate axis none when no rotation
- position scaled inches
- unknown rotate axis errors
- no active design errors

## `model_extrude`

> Unit tests for ``extrude.py`` — turn a sketch profile into a solid.

**Guards**
- unknown units
- zero distance
- unknown operation
- no sketch named
- profile index out of range
- no profile in sketch
**ProfileIndexResolution**
- single int
- default zero
- all keyword
- list
- comma string
- out of range in list reports
- garbage string
**ProfileHandle**
- composite handle is a handle
- long bare token is a handle
- index selectors are not handles
**MultiProfileExtrude**
- all profiles extruded in one call
- list of profiles
- single still reports scalar
**Extrude**
- basic new body scales distance to cm
- inch scaling
- most recent sketch when unnamed
- operation mapping cut
- symmetric flag passed
- negative distance allowed
**Taper**
- taper uses one side extent with deg string
- symmetric suppresses taper path
- zero taper is plain distance
**AsSurface**
- default extrude is solid unchanged
- as surface true sets isSolid false
- open path auto surface when no closed profile
- no profile and no curves points at surface path
**ToObject**
- extrude to face uses to entity extent
- to object overrides distance
- bad to object handle errors
**TargetBodies**
- cut scoped to bodies
- target bodies by handle
- target bodies rejected on new
- bad target body errors

## `model_fillet_chamfer`

> Unit tests for ``fillet.py`` — model_fillet + model_chamfer.

**Guards**
- unknown units
- nonpositive radius
- body not found
- bad edge filter
- nonnumeric radius
- no matching edges errors
**Fillet**
- fillet all edges scaled
- fillet convex filter
- fillet concave filter
- default most recent body
- unknown convexity included under filter
- radius echoed rounded in payload
**Chamfer**
- chamfer scales distance
- two distance chamfer
- equal distance when no second
**EdgeHandles**
- fillet specific edges via handles
- edges take precedence over body
- bad edge handle errors

## `model_hole`

> Unit tests for ``model_hole`` — the real HoleFeatures building block (not a sketch+extrude-cut).

**Guards**
- unknown type
- missing diameter
- no points
- counterbore requires its dims
- through and depth conflict or missing
**Simple**
- simple blind
- simple through uses positive direction
- multiple points one feature
**CounterboreCountersink**
- counterbore passes three dims
- countersink passes angle
**Tapped**
- tapped builds thread info and taps
- unknown tap designation errors
**ClearanceFastener**
- clearance tags hole and sizes from table
- fit changes diameter
- fastener overrides explicit diameter
- unknown fastener size errors
- unknown fastener type errors
- bad fit errors
- counterbore with fastener keeps counterbore

## `model_inspect`

> Tests for `model_inspect` — the measurement rich read (bbox default + include=['mass']; mesh routing).

**DefaultAndDispatch**
- solid default is bbox
- design default is bbox
- include mass adds properties
- mesh target routes to mesh stats
- mesh is not a valid include
**Guards**
- unresolvable target errors
- unknown include errors
**NormalizeInclude**
- comma string
- none empty

## `model_measure_between`

> Tests for `model_measure_between` — distance / angle between two targets.

**Distance**
- distance default mode scales to mm
- distance in cm
**Angle**
- angle returns degrees
**Guards**
- unknown mode errors
- bad units errors
- unresolvable a errors
- measure failure surfaced

## `model_mirror`

> Unit tests for ``mirror.py`` — mirror solid bodies across an origin plane.

**Guards**
- bad plane
- no bodies
- body not found
**Mirror**
- mirror across yz
- comma string bodies
- join sets iscombine
- join defaults false
- mirror across xz
- multiple result bodies collected
- zero result bodies is empty list

## `model_pattern`

> Unit tests for ``patterns.py`` — rectangular & circular component patterns.

**Resolution**
- exact name
- substring fallback
- missing reported
- comma separated multiple
**Rectangular**
- single direction scales spacing
- two directions
- quantity one must be positive
- unknown direction
- unknown units
- unknown direction two errors
- single row direction two none in payload
- spacing scaled inches
**Circular**
- basic full ring
- axis selection
- symmetric flag
- quantity must be at least two
- unknown axis
- partial arc angle string
- symmetric defaults false
**BodyTargets**
- rectangular patterns bodies by name
- circular patterns bodies by handle
- bodies take precedence over occurrences
- bad body name errors
**BodyOwningComponent**
- circular builds on bodys parent component
- rectangular builds on bodys parent component

## `model_revolve`

> Unit tests for ``revolve.py`` — revolve a sketch profile about an axis.

**Guards**
- unknown operation
- zero angle
- no sketch named
- profile out of range
- bad axis
**Revolve**
- full revolve converts deg to radians
- partial angle
- axis x resolves
- axis sketch line
- operation cut mapping
- symmetric flag
- two sided asymmetric
- fake rejects the nonexistent method name
- second angle ignored when symmetric

## `occurrence_ref_lint`

> Lint: single-occurrence resolution must go through the shared OccurrenceRef resolver.

**RoutedToolsStayOnSharedResolver**
- no routed tool hand rolls a substring name match
- routed tools reference the shared resolver
**SharedResolverBehaviour**
- fullpath beats a same named instance
- ambiguous bare name errors

## `output_contracts`

> Lint: every tool that DECLARES outputs (a RETURNS spec) must honour the contract.

**DeclaredOutputs**
- at least the known producers declare returns
- returns entries are output kinds
- declared key appears in source
- description carries the produces block

## `outputs`

> Unit tests for the typed OUTPUT KINDS framework (_outputs.py).

**ProducesNote**
- handle note names key and consumers
- note without consumers omits arrow
- urn and name labels
**ProducesBlock**
- block has header and one bullet per output
**AssertPresentTopLevel**
- present returns empty
- missing returns error naming key
- null value counts as missing
**AssertPresentInList**
- handle inside a matches list is found
- in list but no item has the key errors
- empty list is missing
- top level key not treated as in list when flag off

## `param_ops`

> Unit tests for ``param_ops.py`` pure logic (the param_* tools).

**ParamSummary**
- numeric value used directly
- text param falls back to textValue
**FindParameter**
- found in user parameters first
- falls back to all parameters
- missing returns none
**SetValidation**
- empty name is error
- empty expression is error
- zero expression passes the empty guard
**TimelineHealth**
- rolls up errors and warnings
**AddHandler**
- add rejects duplicate
- add succeeds when timeline stays healthy
- add rolls back on new timeline error
- add requires name and expression
**AddBatch**
- batch adds all
- batch stops and reports the failing entry
- single param path still works
**SetCreateOrUpdate**
- set existing updates
- set missing without create errors
- set missing with create makes user param
**DeleteHandler**
- delete refuses if referenced
- reference match is word boundary
- delete unknown param errors
**FavoriteHandler**
- sets favorite flag
- unknown param errors
- favorite set failure surfaces
- empty name errors
**GetHandler**
- no active design
- lists user parameters only by default
- include model parameters dedups user names
- single named user param
- single named model param falls through to all
- single named missing errors
**DeleteHandlerExtra**
- delete me false reported
- timeline error after delete reported
- empty name errors
**AddFavorite**
- favorite reported from param state

## `polyline`

> Unit tests for the polyline / closed_path sketch kind in sketch_core.py.

**PolylineChaining**
- open polyline segment count
- closed polyline segment count
- consecutive segments share endpoint
- close welds last to first
- needs at least two points

## `quoting`

> Unit tests for the text-parameter quoting helpers.

**Unquote**
- strips single quotes
- strips double quotes
- unquoted string passes through
- none passes through
- mismatched quotes not stripped
- single char not treated as quoted
**Quote**
- wraps in single quotes
- escapes inner single quote
- empty string
**RoundTrip**
- quote then unquote recovers text

## `show_toolpath`

> Unit tests for ``cam_show_toolpath.py`` — CAM toolpath display control.

**Guards**
- unknown action errors
- no active document
**List**
- reports every op and state
**Isolate**
- shows only target
- substring match when no exact
- exact beats substring
- unmatched operation errors
**ShowHide**
- show turns on
- hide turns off
- show on pathless op warns
- missing operation arg errors
**HideAll**
- hides only ops with toolpaths
**ShowFolder**
- shows named setup only
- unknown folder errors
- missing folder arg errors
- show folder skips pathless ops
- show folder matches camfolder child
**Fit**
- show with fit reports fitted
- show without fit does not fit

## `sketch_constrain`

> Unit tests for ``sketch_constrain.py`` — apply geometric constraints to sketch entities.

**ResolveEntity**
- line index
- arc circle point
- bad type
- out of range
- malformed
- noninteger index
- negative index
- empty ref
**TwoCurve**
- perpendicular
- parallel equal tangent concentric collinear
- two curve needs entity two
**PointCurve**
- midpoint
- coincident
- point curve needs entity two
**SingleLine**
- horizontal
- vertical
- constraint returning nothing is error
- fix sets isfixed
- unfix
- fix failure is reported not a false success
**Symmetry**
- symmetry uses symmetry line
- symmetry needs symmetry line
**Guards**
- unknown constraint
- missing sketch
- unresolvable entity

## `sketch_core`

> Unit tests for ``sketch_core.py`` pure logic.

**ScaleWiring**
- sketches uses the shared scale
**ResolvePlane**
- xy alias
- top alias maps to xy
- front alias maps to xz
- right alias maps to yz
- whitespace and case tolerant
- named construction plane fallback
- unresolvable plane returns none
**NewKinds**
- ellipse
- slot
- point
- spline
- center rectangle
- is construction marks curve
- non construction default
- ellipse needs positive radius
**CoreKinds**
- circle radius scaled to cm
- line points scaled
- arc sweep converted to radians
- polygon radius scaled
- unknown kind errors
- unknown units errors
- missing required params listed
- polygon needs three sides
- summary reports counts
**ParsePoints**
- list pairs
- dict pairs
- too few points
- malformed pair
- not a list
**Polyline**
- open polyline segment count
- closed path adds closing segment
**TargetSketch**
- named sketch resolved
- default is most recent
- missing named sketch errors
**Draw3dLine**
- end off plane detected and scaled
- on plane end not flagged
- missing end point errors
**SketchWorldFrame**
- origin reported in mm
- axes reported as world unit vectors
- xz plane y maps to negative world z
- unreadable frame is none
- partial frame is none

## `sketch_dimension`

> Unit tests for ``sketch_dimension.py`` — dimensional constraints + driven values.

**Dispatch**
- distance two lines
- horizontal orientation
- radius one circle
- diameter
- angle two lines
- vertical orientation
**RadialTextPoint**
- offset one radius along x from center
- zero radius uses unit offset
- missing center falls back to unit point
**PointOf**
- line uses start sketch point
- point returns itself
**Guards**
- unknown dim type
- bad entity one
- distance needs entity two
- value optional
- value set failure is reported
- dimension returning nothing is error

## `sketch_get_merge`

> Unit tests for the merged sketch_get read tool (chunk B of the refactor).

**SketchGetRouting**
- no name lists summary
- name delegates to detail engine
- whitespace name treated as no name

## `sketch_set_text`

> Unit tests for ``sketch_set_text.py`` — set/create sketch-text strings.

**QuoteUnquote**
- quote wraps in single quotes
- quote escapes inner single quote
- unquote strips single quotes
- unquote strips double quotes
- unquote passes unquoted through
- unquote none is none
- unquote single char not stripped
- quote round trips for quote free text
**IterSketchTexts**
- collects across components and sketches
- name filter limits to one sketch
- no texts yields empty
**EditHandler**
- sets all texts and reports before after
- index selects one text within sketch
- index out of range is error
- index counter is per sketch
- no text in named sketch errors
- no text in design errors
- recompute runs in parametric
- recompute skipped in direct mode
- set failure is reported
- max cap limits changes
- none text errors
**Create**
- creates text with scaled height
- create position scaled
- create requires sketch name
- create unknown units
- create nonpositive height
- create missing sketch

## `sketch_text_create`

> Unit tests for ``set_sketch_text.py`` CREATE path — make new sketch text from scratch.

**CreateText**
- creates text in named sketch
- create requires sketch name
- create missing sketch errors
- create rejects nonpositive height

## `surface_create`

> Unit tests for surface_create.py — CREATE open (non-solid) surface bodies.

**SurfaceExtrude**
- sets isSolid false and reports it
- reports result is solid read back
- zero distance guard
- unknown operation rejected
- from edge curves uses edge profile
- no sketch no curves errors
- unknown units rejected
- join op and symmetric passed through
- sketch with no curves errors
**SurfaceRevolve**
- sets isSolid false
- reports result is solid read back
- zero angle guard
- non numeric angle rejected
- unknown axis rejected
**SurfacePatch**
- patch over closed edge loop
- single edge passes edge for autocomplete
- null feature errors
- unknown operation rejected
- boundaries patches every loop in one call
- boundaries reports per loop failure without aborting
- neither boundary nor boundaries errors
- unknown continuity rejected
- continuity tangent set on input
- boundaries all fail reports zero patched

## `surface_edit`

> Unit tests for surface_edit.py — EDIT open surface bodies (trim/extend/offset/thicken).

**SurfaceTrim**
- commits via add on success
- trim selects a cell before add
- keep smaller keeps smallest cell
- keep by index keeps that cell
- keep list of indices
- keep int index keeps that cell
- keep out of range index falls back to larger
- bad keep falls back to larger default
- no cells cancels and reports no intersection
- cancels open transaction when add raises
- cancels when add returns null feature
- wrong kind surface gets redirect before any transaction
**SurfaceExtend**
- extends from open edges
- rejects edges from more than one body
- zero distance guard
- unknown units rejected
- unknown extend type rejected
- tangent extend type resolves enum
**OffsetThickenKind**
- offset produces a surface
- thicken produces a solid
- thicken symmetric passed
- thicken zero thickness guard
- offset unknown operation rejected
- offset unknown units rejected
- thicken unknown units rejected
- thicken unknown operation rejected
- thicken join op maps enum

## `surface_ops`

> Unit tests for ``surface_ops.py`` — LOFT / STITCH / UNSTITCH (surface<->solid bridge).

**Loft**
- three profiles added in order
- reports is solid read back
- surface loft reports not solid
- as surface sets isSolid false on input
- fewer than two rejected
- rails and centerline both rejected
- centerline set on input
- rails added and counted
- unknown operation rejected
**Stitch**
- became solid true on watertight
- became solid false when gaps remain
- rejects solid input
- fewer than two rejected
- tolerance scaled to cm
- default tolerance when omitted
- unknown units rejected
- unknown operation rejected
- became solid false when only some result bodies closed
**Unstitch**
- explode body uses add not createInput
- peel faces
- needs target or faces
- target and faces both rejected
- null feature is error

## `sys_api_doc`

> Unit tests for ``api_doc.py`` — live Fusion-API documentation search.

**ClassFilterFrom**
- extracts titlecase class
- namespace only has no class
- empty is none
**Trim**
- none is empty
- short doc unchanged
- long doc truncated with ellipsis
**Signature**
- returns signature string for function
- unsignable returns none
**LoadModulesFilter**
- namespace filter scopes modules
- no filter loads all in scope
- class filter keeps its namespace
**Validation**
- empty pattern errors
- invalid regex errors
- bad category errors
- unknown filter namespace errors
**ClassSearch**
- class name match
- namespace filter scopes classes
**MemberSearch**
- member name match carries signature
- class filter scopes members
- property vs function kind
**DescriptionSearch**
- matches docstring text not name
**Caps**
- max results clamped and truncation flagged
- max results never exceeds hard cap

## `sys_capability_map`

> Unit tests for ``sys_capability_map`` - the LIVE family index (breadth map).

**FamilyMap**
- groups by prefix with summary entry and count
- counts sum to tool total
- unmapped family falls back honestly
- note cross links to find tool
**FamilyOf**
- prefix split

## `sys_find_tool`

> Unit tests for ``sys_find_tool`` — search the server's own tools + input-kinds by keyword.

**Guards**
- empty query errors
**ToolSearch**
- matches name
- matches description and inputs
- name match outranks description only
- summary is first sentence
- no match reports
**KindSearch**
- profile query surfaces ProfileRef
- body query surfaces BodyRef
- include kinds false omits them
- kinds note points to convention

## `sys_selection`

> Unit tests for the ``sys_selection.py`` MCP tool's pure logic.

**Unit**
- normalizes to length one
- arbitrary vector normalized
- zero vector returns none
- none input returns none
**FaceDirection**
- planar face returns normal
- cylindrical face returns axis
- sphere has no direction
**EdgeDirection**
- linear edge direction is end minus start
- circular edge direction is plane normal
**Classify**
- face is classified with direction
- edge is classified as edge
- unknown entity falls through to other
**RequireFlag**
- require face matches a face
- require edge flags mismatch when face selected
- nothing selected is an error

## `tier2_misc`

> Unit tests for assorted Tier-2 helpers: doc_update_xref, cam_generate.

**RefName**
- reads datafile name
- missing datafile falls back
**UpdateXrefHandler**
- no active document
- no references is a clean noop
- updates out of date ref
- up to date ref is skipped
- force refresh when only out of date false
- name filter targets one ref
- unknown name lists available
- get latest false is an error
**LiveReadiness**
- counts states
- active op captured with real progress
- errored op bucketed separately not as ood
- setup level error tallied
- nc program level error tallied
- clean job is ready
- cam unavailable returns error

## `tool_autodiscovery`

> Lint/contract for the AUTO-DISCOVERED tool registration (entry.py::_collect_items).

**AutoDiscovery**
- every swept module registers a tool
- sweep registers a full nonempty set
- tool names are unique
- gated tool not in swept set
- helper modules have no register tool
- explicitly referenced modules are importable with their entry points
- entry does not attribute access swept or gated modules

## `tool_naming`

> Lint/contract for the TOOL NAMING SCHEMA (CLAUDE.md "Read vs Edit").

**ToolNaming**
- every name is domain verb
- verb is in the closed set
- verb kind matches write status

## `view_inspect`

> Unit tests for ``view_inspect.py`` — the agent's view verbs.

**Guards**
- unknown action
- no design
**Visibility**
- hide turns bulb off
- isolate sets flag
- isolate requires single match
- exact name beats substring
- show lights ancestor chain
- clear isolation resets all
- unmatched target errors
- missing target errors
**Style**
- wireframe sets visual style
- unknown style errors
**Orient**
- unknown orientation errors
- focus unknown occurrence errors
- front orientation sets up vector
- front eye placed on minus y at preserved distance
- top orientation uses plus y up
- focus only translates eye by target delta
**NamedViews**
- save view adds
- save view overwrites same name
- save view requires name
- apply view moves camera
- apply unknown view lists available
- list views reports builtin flag
**SnapshotRestore**
- restore without snapshot errors
- snapshot then restore puts bulbs back
- restore reinstates isolation
- restore counts missing occurrences

## `view_screenshot`

> Unit tests for ``get_screenshot.py`` _isolate_for_fit — the fit_to visibility helper.

**IsolateForFit**
- hides others and restores
- substring match
- no match returns none
- already hidden others not restored on
**ActiveComponentNote**
- root active no note
- sub component active warns and names it
- none design is safe
**OrthoCameraVectors**
- front looks along plus y z up
- right looks along minus x
- top looks down z
- all six faces are pure world axes
- iso vectors are unit length
- current and unknown return none
- only the six faces force orthographic

## `view_screenshot_multi`

> Unit tests for ``view_screenshot_multi.py`` — capture several orthographic/iso views in one call.

**ParseViews**
- default set
- explicit comma list
- whitespace and case tolerant
- dedupes preserving order
- unknown view errors
- all keyword expands to six orthos
- only separators falls back to default

## `view_section`

> Unit tests for ``view_section.py`` — the Section Analysis cutaway tool.

**Guards**
- unknown action
- no design
- cut requires plane or through
- through unknown occurrence
**PlaneCut**
- xy plane uses xy construction plane at zero
- alias front maps to xz
- offset mm converted to cm
- flip and hatch propagate
- default auto view bare plane does not raise
- auto view skipped for non origin plane handle
**ThroughCenter**
- xy uses z center
- front uses y center
- through adds explicit offset on top of center
- through defaults to xz when no plane
- through substring match
**AutoViewAim**
- yz cut aims camera down plus x with z up
- flip reverses the revealing side
- top cut uses y up because normal is z
**ListClear**
- list reports sections
- clear removes all

## `view_workspaces`

> Unit tests for ``view_workspaces.py`` — list/switch Fusion workspaces.

**List**
- lists all and flags active
- none active
**SwitchGuards**
- empty workspace errors
- not found lists available
**SwitchMatching**
- alias resolves to id
- cam alias resolves
- match by exact id
- match by name case insensitive
**SwitchState**
- already active does not reactivate
- activation failure errors

## `workspace_orient`

> Unit tests for ``workspace_orient.py`` — the cold-boot orientation call.

**Guards**
- no active document
- document without a design
**Orientation**
- reports document and design identity
- reports parameters count and pointer
- no param pointer when zero
- healthy rollup
- timeline errors make it unhealthy
- broken joint surfaced by name
- suppressed joint is not broken
- healthy note says so
- direct mode has no timeline
- browser digest is depth one
- digest capped for wide assemblies
**Cam**
- no cam
- cam present with ungenerated ops
**ExternalReferences**
- no references is clean
- references all current is healthy
- out of date reference is flagged for attention
- ood reported even without an active design
**Pointers**
- small design points to whole tree
- large assembly steers to scoped tree
- many bodies steers geometry to target
- broken health adds fix pointer
- kinematics pointer only when joints or grounding
**DataModel**
- saved doc reports full location and urn
- unsaved doc has no urn and note warns
- data model present even without a design
**Bbox**
- bbox reported in display units
- bbox none when no geometry
**ViewState**
- orthographic camera
- perspective camera
**SelectionEcho**
- no selection is empty
- selected body echoed with pointer
- selected face reports body and occurrence

## `write_guard`

> Tests for the write-document binding guard (_write_guard) - the concurrency targeting fix.

**ActedOnStamp**
- successful write is stamped
- error result is not stamped
- handler does not see expect document
**ExpectDocumentGuard**
- match by name proceeds
- match by urn proceeds
- mismatch refuses without calling handler
- omitted expect document proceeds
**IntegrationThroughItem**
- write tool gains expect document read does not

## `write_status_annotations`

> Lint: every registered tool must declare a write-status annotation.

**WriteStatusDeclared**
- every tool declares read only hint
- read only tools are not destructive
- hints serialize into the tool payload

