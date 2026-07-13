# Behavior Spec (generated)

_Auto-generated from the test suite by `tests/gen_spec.py`. Do not edit by
hand — every line below is pinned by a passing test. Re-run the generator
after changing tests._

**Tools with a test file:** 155  |  **Behaviors pinned:** 2734

## `_cam_common`

> Unit tests for ``_cam_common.py`` -- the shared CAM read logic behind cam_get.

**SetupsCap**
- under cap untruncated and unchanged
- at cap truncates and flags
**ModelListsCap**
- under cap untruncated
- at cap truncates and flags
**OperationsCap**
- under cap untruncated
- at cap truncates and flags
**OperationSummaryStateNaming**
- operation state 1 is named out of date
**ReferencesCap**
- under cap untruncated
- at cap truncates and flags
**InvalidationReasons**
- design changed line is a categorical reason
- parameter delta line is counted not added as a reason
- machine changed line sets the flag not a reason
- noncategorical invalidated line is dropped
- duplicate reasons are deduped
- reasons are capped
- blank message log yields nothing
**OpPrimaryState**
- suppressed outranks error generating and state
- error outranks generating and state
- generating outranks operation state
- state 3 is no toolpath
- state 1 is out of date
- state 0 is valid
**Hms**
- zero seconds
- formats hours minutes seconds
- rounds fractional seconds
- non numeric input falls back to zero
**MachiningTimeConstants**
- feed scale is 100 percent not a 0 to 1 fraction
- rapid feed is 10 58 centimeters per second
- tool change time is 1 5 seconds
- total seconds sums across setups
- setup without a valid toolpath reports an error not a crash

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

## `_export`

> Unit tests for ``_export.py`` - the export-to-disk substrate shared by design_export/mesh_export: filename sanitizing, the component-by-name resolver, the file-landed verifier, and the one-file-per-top-level-occurrence split orchestration.

**Sanitize**
- drops instance suffix
- keeps safe chars
- swaps illegal chars
- empty becomes part
- all illegal becomes part
**ComponentByName**
- finds matching component
- no match returns none
- empty component list returns none
**VerifyWritten**
- existing nonempty file passes
- missing file is an error
- empty file is an error
**TopLevelOccurrences**
- lists every occurrence in order
- no occurrences is empty list
- unreadable occurrences collection is empty list
**SplitByOccurrence**
- one record per occurrence named and extensioned
- duplicate stems disambiguated
- failure lands in errors not files
- empty occurrence list yields nothing

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
- origin point is flagged
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
- handle locator carries sketch and area
- loop count distinguishes ring from region
- empty when no profiles
**ProgressiveDisclosure**
- default omits the heavy entity xray
- default points at the deeper level
- include entities adds the xray
**XrayCaps**
- under cap untruncated and unchanged
- entities at cap truncates and flags
- constraints at cap truncates and flags
- dimensions at cap truncates and flags
**UnitsScaling**
- default mm scales positions 10x
- units field named in overview
- units field named in xray
- cm units pass through unscaled
- inch units scale by cm to unit factor
- area scales squared
- profile centroid scales linearly
- unknown units rejected
- handle locator area stays raw cm regardless of units
- dimension value scaled to display units
- angular dimension value not length scaled

## `_view_common`

> Unit tests for ``_view_common.py`` - the shared camera-orientation table for the standard named views. view_screenshot and view_inspect must produce the SAME camera for a given named view even though they consume opposite sign conventions (view_screenshot's look_direction is the negation of view_inspect's view_direction); this pins that relationship so the two can't silently desync.

**ViewDirection**
- known views return unit vectors
- unknown view returns none
- front points toward minus y
- top points toward plus z
- right points toward plus x
**LookDirection**
- unknown view returns none
- front looks along plus y
- known views return unit vectors
**SignRelationship**
- look direction is negated view direction for every named view
**UpVector**
- unknown view returns none
- top and bottom use y up
- faces and isos otherwise use z up
- up vector is the same for both conventions
**OrthoFace**
- six true faces are ortho
- iso corners and unknown are not ortho

## `active_component`

> Unit tests for active-component targeting in sketch_core.py + model_extrude.py.

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
- stuck appearance bites
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
**PartialFailureComponentBodies**
- one body fails others still colored and reported
- all bodies fail returns error
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
- areCoincidentFacesIncluded failure surfaces as error

## `assembly_joints_advanced`

> Unit tests for ``assembly_joints_advanced.py`` - assembly_capture_position, joint_create_as_built, assembly_constrain.

**CapturePosition**
- capture when pending
- capture with nothing pending errors
- phantom capture bites
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
- compute failed constraint bites
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
- unknown units errors not silently treated as mm
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
**Orientation**
- identity rotation reads axis aligned basis
- 90deg z rotation basis
- axes omitted when coordinate system unavailable
**Health**
- all healthy
- broken joint surfaced
- suppressed joint is not broken
- unknown rollup state is not a problem
- stale joint health flagged when timeline is clean
- no stale flag when timeline also shows the error
- timeline problem surfaced
- health message deduped
**Caps**
- occurrences under cap untruncated and unchanged
- occurrences at cap truncates and flags
- joints under cap untruncated and unchanged
- joints at cap truncates and flags
- default caps are generous enough for a normal model

## `assembly_transform`

> Unit tests for ``assembly_transform.py`` - occurrence ground/move + rigid group.

**Ground**
- lock to parent
- unground from parent releases lock
- stuck flag bites
- only sets ground to parent
- no grounded param is rejected by strict schema
- substring match
- missing occurrence errors
- no change requested errors
- ambiguous name refused not wrong instance
- exact full path targets the right instance
**Move**
- move that does not take bites
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
- rotate about construction axis infinite line
- unreadable edge geometry errors
- combined rotate and translate preserves pivot
**RigidGroup**
- group reporting fewer members bites
- groups named occurrences
- include children flag
- needs at least two
- missing reported
- accepts a list not just comma string
- list with blank entries filtered
**MoveNote**
- jointed move note differs from free move

## `assert_kinds`

> Unit tests for ``_assert.py`` - the postcondition kernel (verify-the-effect).

**WrapContract**
- confirmed effect merges evidence
- handler values win over evidence
- hard reason converts ok into error
- soft reason marks unconfirmed not error
- error results pass through unverified
- capture value reaches verify
- verify crash degrades to unconfirmed never false pass
- no postconditions returns handler unwrapped
- wrapper exposes declaration for the lint
**VersionAdvanced**
- false success still modified bites
- real save confirms
- already current noop skips the check
**ReferencesFresh**
- surviving stale reference bites
- all fresh confirms
**DeliverablesExist**
- every listed file is verified
- one missing listed file bites
- single file path shape is covered
- claiming no deliverable at all bites
- list entry without a path bites
**FeatureHealthy**
- healthy added feature confirms with count
- compute failed feature bites with name and message
- compute warning is evidence not failure
- only items added by the call are gated
- no new timeline items skips silently
- no timeline design skips silently
**FileLanded**
- missing file bites
- empty file bites
- real file confirms and supplies size
- missing path key is named

## `assert_strength`

> Lint: every test must be able to fail for the RIGHT reason.

**AssertStrength**
- no test relies on a bare iserror flag alone

## `axis_vectors_shared`

> Lint: the world-axis x/y/z -> unit-vector map is _inputs._AXIS_VECS, never a local copy.

**AxisVectorsShared**
- no local world axis vector map
- the lint bites

## `cam_activate_setup`

> cam_activate_setup: the name guard, miss-lists-available, and the activation read-back (an activate() the platform accepts but that does not take must surface as an error).

- blank setup name is refused
- cam gate error is surfaced
- unknown setup lists available names
- activates and reports the setup name
- activate that does not take is an error
- activate raising is surfaced not swallowed

## `cam_common`

> Unit tests for the shared CAM finders in ``_cam_common`` - find_setup / find_operation / walk_operations / setup_names. These are the ONE case-insensitive resolution every CAM tool shares, so 'Setup1' vs 'setup1' resolves the same everywhere. allOperations fakes carry the count/item collection shape - the measured live protocol.

**FindSetup**
- found case insensitive
- not found returns available
- empty cam is safe
**SetupNames**
- lists all setup names
**WalkOperations**
- flattens across setups countitem
- empty setup walks to nothing
**FindOperation**
- found case insensitive across setups
- not found returns available

## `cam_compare`

> Unit tests for ``cam_compare.py`` -- cam_compare_operations, the diff over two CAM operations' parameters. Covers the diff logic (same vs differing parameters, not-present-on-one-side) and the bounded-read cap on 'differences'.

**Guards**
- missing operation names refused
- no cam data errors
- operation not found errors
**DiffLogic**
- matching parameters are not differences
- differing value reported on both sides
- parameter only on one side reported as not present
- reports tool descriptions
**Caps**
- under cap untruncated and unchanged
- at cap truncates and flags

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
- phantom setup that never lands bites
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
- folder into folder move resolves
**MoveRefused**
- moveinto false surfaces as an error not a silent success
- moves completed before a refusal are reported and kept

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
- operation nested in a folder resolves

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
- stock switches mode to solid before assigning
- fixtures are enabled before assigning
- params and bodies together
**Machine**
- assigns machine and reads it back
- unknown machine is error
- assignment that does not take is error
- machine counts as something to do
**WCS**
- binds origin to geometry and sets mode
- binds axes for orientation
- unknown wcs key is error
- bad wcs handle is error
- bind that reads back empty is error
- wcs counts as something to do

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
- persist whose url reread disagrees bites
- add validates all refs before adding
- add requires refs
**AddRich**
- create from type
- create with description override and holder
- create with presets
- preset add failure errors not silent skip
- missing diameter param errors not silent drop
- diameter setter raise propagates
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
- created library that does not load back bites
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
- unknown handle lists active
- latest resolves to last handle
- stall warning when nothing generating but ood remains
- errored op surfaced while still generating
- setup error blocks via readiness
- pump budget is clamped
**StatusLivePoll**
- no handle reports inline generation
- document completed only when nothing generating
- live errored op flagged not generating forever
- target by name reports that setups state
- target by name errored op is its own bucket
- target not found errors
- live poll pumps then returns non blocking

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

## `cam_post`

> Unit tests for ``cam_post`` - create-or-reuse an NC Program for the scope, then post it to disk.

**Guards**
- no cam
- requires output folder
- requires program name
- post config not found
- bad units rejected
- refuses when no valid toolpaths
- unknown scope is error
**PostResolution**
- resolves post by name in personal folder
**CloudPostScope**
- local scope default returns ready post configuration
- cloud resolves post by name and posts
- cloud post not found lists candidates
- cloud ambiguous name refused
**CreateOrReuse**
- creates program when none exists
- reuses existing program not duplicated
- output params reach the program
- operations collection carries the target setup
**PostWritesFile**
- posts document and reports written file
- declared output is minted
- no file written is error even when api returns true
- created program is rolled back on failed post
- reused program is not deleted on failed post
- missing output folder param is error
- program error is partial even with file
- partial when api false but file appeared
- post raising is error and rolls back
**PostLog**
- is failure marker
- reads error and warning lines skipping information
- no log returns empty
- failed stub is error with the log reason not listed as a deliverable

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

## `cam_show_toolpath`

> Unit tests for ``cam_show_toolpath.py`` — CAM toolpath display control.

**Guards**
- unknown action errors
- no active document
**List**
- reports every op and state
**Isolate**
- shows only target
- partial name is refused not substring matched
- exact match is case insensitive
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
- isolate covers folder nested ops
- show folder matches camfolder child
**Fit**
- show with fit applies the fit to the camera
- show without fit leaves the camera alone
- fit api refusal raises not false success

## `cam_templates`

> Unit tests for ``cam_templates.py`` navigation logic.

**ApplyTemplateEnumValidation**
- unknown generate is rejected not silently skipped
- unknown location is rejected
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
**SaveTemplateRename**
- rename failure propagates
- rename succeeds reports correct name
- saved template that does not load back bites

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
**CmToUnit**
- is the inverse of unit to cm
- mm is ten per cm
**Ptxyz**
- scales and rounds
- none point is none
**ResultBodies**
- empty feature bodies
- collects bodies in order
- filters none bodies
- none feature is safe
- unreadable bodies is safe
**TargetSketch**
- named sketch found
- named sketch not found
- no name returns most recent
- no name no sketches is none
**AllComponents**
- reads the collection off the design not the root
- falls back to root when the design lacks the collection
- no root is empty
**ResolveSketchDesignWide**
- finds a sub component sketch while root is active
- active component searched first for a shared name
**AllSketchNames**
- spans every component
**ResolveEntityRef**
- resolves line by index
- resolves point by index
- bad type is none
- out of range is none
- malformed ref is none
**Operations**
- maps every verb to a feature operation attribute name
**MinDistance**
- success returns result and no error
- measure exception is surfaced as error
- none result is an error not a silent none

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

## `data_get_upload_status`

> Unit tests for ``data_get_upload_status.py`` - the poller that reports a data_upload_file upload's real state instead of the caller re-listing files and guessing when cloud translation finished.

**Guards**
- no uploads registered errors
- unknown handle lists active handles
- file name no match errors listing handles
**StateReporting**
- uploading state when transfer still in progress
- processing state when transfer done but cloud still working
- complete state reports landed version and pops entry
- failed state reported and pops entry
- latest resolves to most recent handle
- file name lookup finds most recent match and folder disambiguates
- never blocks returns immediately without pumping

## `data_management`

> Unit tests for the cloud data-model tools' shared string/tree logic + handler guards.

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
- unmodified document is a noop
- false success still modified is an error
- kernel passes a real save
- save document item declares the postcondition
- save false return is an error
- refuses never saved doc
- no active document
**CloseDocument**
- closes active by default discarding
- close named
- close all
- unmatched name errors
- close failure with no successful close is an error
- close all partial failure reports ok with errors
**ActivateDocument**
- activate taken reports true
- activate async pending reports pending not true
- requires name
- unmatched errors
**FindOpenDocument**
- exact match case insensitive
- partial name is refused
- shared name is ambiguous not first match
- urn disambiguates a name twin
- web url resolves via embedded urn
- urn with no matching open doc is a clean miss
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
**SaveDocumentAs**
- reports written lineage urn
- name collision is flagged with existing urn
- no collision when same name absent
- saveas declined is an error
- requires name and project

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

## `dead_code`

> Lint: no dead module-level code under commands/mcpServer/, tests/, or the doc generators.

**NoUnusedImports**
- every import is used or a named seam
- import seam table matches reality
**NoUnreferencedDefinitions**
- every module level definition is referenced somewhere
- definition exempt table matches reality
- every test file definition is referenced in its file

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
**Activate**
- switches configuration clean
- unknown configuration errors
- new timeline error after switch is surfaced

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
- occurrence by name
- missing named target errors
- ambiguous name refused not first instance
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
- execute true but no file written is a split failure
**ExportOne**
- stl arg order is geom then path
- non stl arg order is path then geom
- execute false is a failure
- exception captured as error string
**FileExistenceGate**
- execute true but no file written is a failure
- empty file is also a failure
**ResolveTargetExtra**
- handle resolving to non body is not found
- no active design errors
**DxfExport**
- sketch happy path
- missing sketch and face errors
- both sketch and face errors
- empty sketch errors
- sketch not found errors
- face happy path cleans up scratch sketch
- face no geometry errors and cleans up
- extension auto appended

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
**FindOccurrenceByName**
- component name roots at first instance
- exact occurrence name resolves
- ambiguous occurrence name is refused not first matched
- miss returns no error
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
- activation that does not take bites
- unknown component errors and lists
- ambiguous name refused not first match
- activate root via empty
- activate root falls back to deactivate
- activate returns false errors
- deactivate raises surfaces as error

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
- errors surfaced by the recompute are named
- errors present at the start are not new
- compute failure is an error
- no active design errors

## `doc_citations`

> Lint: every file a doc or comment cites exists, and prose cites bare basenames.

**DocCitations**
- cited files exist
- prose cites bare basenames

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
**Caps**
- under cap untruncated and unchanged
- at cap truncates and flags
- unsaved exceptions computed over the full list even when capped
**Versions**
- newest first and capped
- flags latest and open version
- unsaved document has no history
**XrefTree**
- deep stale reference is found
- all current true only when every ref fresh
- cap truncates and blocks all current
- unreadable reference blocks all current
- max depth bounds walk and flags partial
- occurrence row carries xref kind
**XrefTreeDerive**
- derive row present with kind field
- stale derive flips all current false
- empty derive features no crash
- component without features attribute no crash
- unreadable derive reference blocks all current
- both kinds present and rolled up together
- cap is shared across both kinds
**UsedIn**
- drawing reference appears and is typed
- mixed parents rolled up by type
- no references is empty not error
- cap truncates and blocks query complete
- unreadable parent does not read as none
- unsaved document has no where used
- query failure is unknown not empty
**SliceRouter**
- default projection omits cloud slices but advertises them
- include adds only the requested slice
- include used in adds where used slice

## `doc_insert_derive`

> Unit tests for ``doc_insert_derive.py``: guards, the open-or-reuse-source-document bookkeeping, and the inline verify (healthState / documentReference.isOutOfDate / isDerived / parameter delta).

**Guards**
- empty document id errors
- no active design
- direct mode refused
- unresolvable document id
**SourceDocumentBookkeeping**
- opens when not already open then closes on success
- reuses already open document and leaves it open
- open returning nothing errors
- source with no design product errors and closes
**AddFailures**
- null add errors not false ok
- add raising errors
- missing derive features collection errors
**HealthStateVerify**
- error health state bites
- warning health state does not error
**DocumentReferenceVerify**
- out of date at creation errors
**IsDerivedVerify**
- no derived body or occurrence errors
- derived body reported in payload
- occurrence fallback when no bodies
- preexisting occurrence not counted as newly derived
**ParameterVerify**
- warns when flags set and zero landed
- no warning when both flags false
- reports imported count honestly
- flags forwarded to derive input
**IntoComponent**
- empty uses root
- named occurrence resolves its component
- unknown occurrence errors
**PayloadContract**
- payload keys match returns
- document metadata reported

## `doc_insert_occurrence`

> Unit tests for ``doc_insert_occurrence.py`` placement transform + occurrence resolution.

**Placement**
- default identity
- invalid inserted occurrence bites
- position scales to cm
- rotation built
- bad units
- bad rotate axis
**AlwaysReference**
- inserts as reference
- embedded result bites
**ResolveDataFile**
- plain urn resolves directly
- urn extracted from surrounding text
- web url base64 segment decoded
- unresolvable returns none
**B64UrlDecode**
- roundtrip
- garbage returns none
**IntoComponent**
- empty uses root
- named occurrence resolves its component
- unknown occurrence errors
- ambiguous into component refused not first match
**RemoveExisting**
- missing errors
- removes then inserts
- delete returns false errors
- ambiguous remove existing refused
**HandlerGates**
- empty document id errors
- no active design
- unresolvable document errors
- addByInsert returns nothing errors

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
**CloseDocument**
- close active document success
- close returning false is now an error
- close all partial failure reports ok with errors
- no open documents errors
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

## `doc_restore_version`

> Tests for `doc_restore_version` - promoting a prior cloud version back to latest.

**RestoreHonesty**
- confirmed when new tip appears
- promote false is an error not a false ok
- pending when tip did not advance
**Guards**
- restoring the latest is a noop
- unknown version errors and lists available
- no cloud datafile is guarded

## `doc_update_xref`

> Unit tests for ``doc_update_xref.py`` - refresh out-of-date external references.

**Guards**
- no active document
- no refs reports zero
- name not found lists available
**UpdateBehavior**
- updates out of date refs
- skips up to date refs when flag set
- force updates up to date when flag false
- still stale after true return is an error
- false return from get latest reported as error
- get latest raises propagates
- name filter updates only matching
**DeriveReferences**
- derive refresh uses the version setter not get latest version
- stale derive is enumerated and refreshed
- setter failure is reported honestly not swallowed
- up to date derive is skipped
- empty derive features no crash
- missing products attribute no crash
- both kinds refreshed together and counted
- name filter matches a derive by its source name
- zero document references but derive present still refreshes

## `docstring_restatement`

> Measurement: handler docstrings that merely restate the wire description.

**DocstringRestatement**
- restatement count does not regress

## `drawing_create`

> Unit tests for ``drawing_create.py`` - create a 2D drawing from the active design.

**HappyPath**
- creates and returns file id
- uses automatic creation mode
- declared returns are present
- settings requested echoes config
**Guards**
- unsaved design refused
- create returns null is error
- no active design errors
- create exception is reported not swallowed
- missing file id on created drawing errors
- sheet size wrong standard is refused
- portrait on largest sheet is refused
- unknown sheet type is refused
- unknown standard rejected
**InputMapping**
- isometric toggle reaches the input
- standard units content map to enums
- sheet size maps to enum
- orientation and scope map to enums
- sheet types enables only the listed kinds
- auto dimension off disables it
- auto dimension strategy enables and sets strategy
- omit fasteners and keywords reach global prefs
- view style maps to enum on all sheet types

## `drawing_export`

> Unit tests for ``drawing_export.py`` - export the ACTIVE 2D drawing to PDF on local disk.

**HappyPath**
- exports pdf and verifies file
- extension appended when missing
- declared returns are present
**SheetSelection**
- sheet range is passed to options and echoed
- no range defaults to all and sets no range
- line weights default true and togglable
- openpdf is forced false
**FileLandedGate**
- execute true but no file is a failure
- execute false is a failure
- export exception is reported
**Guards**
- active doc not a drawing errors
- missing path errors
- dxf format rejected

## `drawing_update`

> Unit tests for ``drawing_update.py`` - refresh the active drawing's out-of-date references.

**HappyPath**
- stale reference is refreshed and version advances
- gates on references not the lying isuptodate
- declared returns are present
**NoOp**
- current references do not refresh
- zero references is a no op
**HonestyGate**
- still stale after refresh is an error
- kernel confirms a clean refresh
- item declares references fresh
- update exception is reported
- unreadable references refuse to refresh blind
**Guards**
- active doc not a drawing errors

## `edit_joint`

> Unit tests for ``joint_edit`` - edit an EXISTING joint in place (no remaking).

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
**RotationDriveRedirect**
- rotation deg redirects to joint drive
**ReselectInputs**
- reselect joint origin name inputs
**AutoRecompute**
- edit runs computeAll
- reports downstream errors after recompute

## `enum_families_measured`

> Lint: every adsk enum family the tools reference is MEASURED - first contact fails loudly.

**EnumFamiliesMeasured**
- every referenced family is measured
- every consumed behavior key is measured

## `evergreen_no_baggage`

> Lint: the codebase is EVERGREEN - no comment/docstring/string narrates its own history, points back at the planning document that produced it, or leaves process notes for a future maintainer (see tools/CLAUDE.md, "Module docstrings").

**NoHistoricalOrPlanBaggage**
- no file narrates history or points at a plan
- allowlist entries still exist and still trip
- the lint bites

## `fake_shapes_exist`

> Lint: every public attribute a SHARED fake exposes exists on its live adsk counterpart.

**SharedFakeShapesExist**
- every shared fake attribute exists live
- allowlist entries still trip

## `family_gating`

> Unit tests for per-family tool gating.

**FamilyOf**
- cam get is cam
- find geometry is find
- workspace orient is workspace
- empty string is safe
- none is safe
**GateableFamilies**
- excludes hub and orientation families
- every member is a real registered family
**RegistryUnregister**
- removes a registered tool
- unknown name returns false
- module level wrapper mirrors the instance method
**PointerFiltering**
- disabled family pointer dropped others kept
- empty registry keeps every pointer
**EntrySettingsKeyContract**
- family enabled keys match gateable families exactly
- default settings defaults every family key to true

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
**Perception**
- planar face reports outward normal
- normal omitted when evaluator raises
- normal omitted when face has no evaluator
- linear edge reports unit direction
- diagonal edge direction is normalized

## `generated_docs_current`

> Lint: the generated docs (TEST_SPEC/TOOL_MANIFEST/TOOL_POINTER_MAP + the CLAUDE.md map) match the live tree.

**GeneratedDocsAreCurrent**
- generator check passes

## `generators`

> Unit tests for the doc generators (gen_spec / gen_manifest / gen_wiring).

**ToolHandlerMap**
- sibling tools sharing a stem keep their own handlers
- chained builder calls still resolve
- create with string input form resolves
- registration without a name or handler is absent
**GenSpecTransforms**
- humanize strips prefix and underscores
- module doc summary is first paragraph flattened
- render counts files and behaviors
**GenManifestFamilies**
- first matching prefix wins and leftovers group as other
- catalog escapes pipes in kind hints
**Splice**
- splice replaces only between markers
- check mode reports stale without writing
- current content reports true
- missing markers raise systemexit

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

## `helper_duplication`

> Lint: a shared helper lives in exactly ONE module - its home (CLAUDE.md "Reuse before you write").

**HelperDefinedOnlyInItsHomeModule**
- denylisted symbols have exactly one definition

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
- component name with single body resolves to it
- occurrence suffix resolves to the component body
- multi body component name refuses with body names
- body name wins over component name
- resolves a handle
- face handle walks to its owning body
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
- sketch line handle axis
- curved edge rejected
- unknown axis string
- composite handle resolves via token
- non string raw does not crash
**AxisRefFace**
- planar face gives the normal as direction
- cylinder face gives its axis normalized
- composite face handle resolves via token
- world axis still resolves with face types wired
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
**BodyBrepKind**
- brep resolves a solid
- brep resolves a surface
- brep rejects a mesh with redirect
**BodyNameAmbiguity**
- ambiguous name is refused with candidates
- a single named body still resolves
- one body reached by two paths is not falsely ambiguous
- a handle is never ambiguous even when name is duplicated
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
**ProfileHandleLocator**
- dead token resolves via sketch area locator
- area disambiguates same centroid profiles
- wrong area is a miss not a nearest grab
- far centroid is a miss
- legacy named sketch resolves design wide
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
- ambiguous occurrence name errors with candidates
**TargetRefList**
- body handles pass through
- container occurrence selected as occurrence
- component name maps to its occurrence
- component with no occurrence errors
- component with multiple occurrences refused
- mixed body and container
- empty optional is empty list
- one bad element fails whole list
**TargetRefEdgeAndConstruction**
- edge handle resolves when allowed
- edge handle refused under default allow
- construction axis resolves when allowed
- construction plane resolves when allowed
- original body kind unaffected by the extension

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
- unknown axis keyword errors
- circular edge is an axis entity for auto
- motion setter failure reports error

## `joint_create_edit`

> Unit tests for ``joint_create_edit.py`` pure logic.

**FindJointOrigin**
- empty name returns none
- root jo returned directly
- name is trimmed before lookup
- not found anywhere returns none
**ApplyMotion**
- rigid
- slider uses axis index
- unsupported type reports error
**ApplyMotionPinSlot**
- default slide is next frame axis
- explicit slide axis used
- slide equal to rotation refused
- custom entity repoints rotation slide stays frame
**SlideAxisHelpers**
- blank defaults to none
- valid distinct axis
- same as rotation errors
- unknown axis errors
- slide name default is perpendicular
**CreatePinSlot**
- create pin slot reports default slide axis
- create pin slot explicit slide axis
- create pin slot slide equal axis refused before build
**EditRotationRedirect**
- rotation deg redirects to joint drive
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
**OccurrenceScopedJO**
- scoped spec resolves to proxy
- resolve input falls through to scoped jo
- plain occurrence name is not treated as scoped
- missing occurrence returns none
**ResolveErrorListsJointOrigins**
- error names each jo and owner
- listing is capped with overflow count
- no jos keeps error unadorned
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
- add failure on input paths hints the proxy fix
- add failure other errors unadorned

## `joint_create_origin`

> Unit tests for ``joint_create_origin.py``.

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
**HandlerGuards**
- no active design errors
- unknown anchor errors
- unknown target errors
- unknown keypoint errors
- unknown units errors
**HandlerCoordinateAnchor**
- coordinates at scales by the unit factor
- target origin ignores xyz and reports zero location
- creates joint origin and reports frame axes
- custom name is applied to the new joint origin
**BboxCenterGeometry**
- center is the bbox midpoint
- z line runs along requested world axis
- flip reverses the oriented axis
**OrientAxisFromFace**
- planar face handle aligns z to the face normal
**FaceCenterGeometry**
- planar face builds via planar factory
- non planar face is rejected
- non face handle is rejected
**BboxCenterHandler**
- reports computed anchor and readback in display units
- wrong landing point errors and rolls back
- missing target errors
**BboxCenterAmbiguousTarget**
- ambiguous name is refused

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
- finds root joint by exact name
- finds as built joint
- unknown name returns none
**AllJoints**
- walks root and subcomponents and asbuilt
- dedups root when allcomponents includes it
- dedups root reached via distinct proxy
- empty design is safe
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

> Unit tests for ``joint_create_edit.py``'s autonomous geometry-snap resolver.

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

## `layout`

> Lint: the test tree stays organized by KIND.

**Layout**
- each test is in its correct bucket
- no behavior tests under live
- lint set has no stale names

## `live_api_facts_wiring`

> The live_api_facts wiring: the generated measured facts actually POPULATE the mocks.

**EnumSeeding**
- every measured enum family is seeded on the mock modules
- seeded values are ints not child mocks
**BehaviorFlagsConsumedAtCallTime**
- fake vector zero normalize follows the measured flag
- collection item out of range follows the measured flag

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

## `mcp_server`

> Wire-level tests for SimpleMCPServer's JSON-RPC protocol behavior.

**InitializeProtocolVersionNegotiation**
- initialize with supported version echoes it
- initialize with unsupported version responds with supported version
- initialize with no protocol version responds with default
**ToolsCallUnknownTool**
- unknown tool is a protocol error with code 32602
**ToolsCallArgumentValidation**
- unknown argument is an error result and handler not called
- missing required argument is an error result and handler not called
- lenient schema passes unknown keys to handler
- schema omitted arg reaches the handler
- omitted args table matches reality
- valid call with optional argument omitted succeeds
- empty schema tool with no arguments succeeds
- omitted arguments default to empty dict
**ToolsCallHandlerExceptions**
- handler exception becomes iserror result not protocol error
**NotificationsAndPing**
- notification without id returns none
- ping returns empty result

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
**NoOpGate**
- unchanged target bites
- target triangle change passes
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
- none feature with zero face groups is a failure
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
- phantom body that never lands bites
- landed body grows the count and passes
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
- under cap untruncated and unchanged
- at cap truncates and flags
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
- unreduced count is an error not success
- proportion 100 keep everything is not gated
- parametric routes through base feature scope
**MeshRemesh**
- remesh reports before after
- unchanged count is flagged not asserted
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
- spacing setter raise surfaces as error

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
- rotation pivot is world origin not the placement
- rotate axis none when no rotation
- position scaled inches
- unknown rotate axis errors
- no active design errors
**DesignIntentPromotion**
- part intent is promoted to hybrid
- hybrid intent is left alone
- assembly intent is left alone
- intent absent is untouched

## `model_draft`

> Unit tests for ``model_draft.py`` - taper faces to a pull direction (the Draft feature).

**Guards**
- angle not a number
- zero angle rejected
- angle out of range rejected
- no active design
- face resolution error propagates
- pull direction error propagates
**Draft**
- happy path reports read back count
- createinput gets faces and plane
- symmetric flows into set single angle
- default not symmetric
- flip sets direction flipped
- health error reported not false ok
- add returns none is error
- declared outputs present

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
- symmetric with taper uses symmetric extent
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
- set to clearance hole failure propagates
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

## `model_measure_relation`

> Tests for `model_measure_relation` - named geometric predicates over two entities.

**Coaxial**
- same axis line passes
- parallel but offset axes FAIL
- non parallel axes fail
- offset exactly at tolerance passes
- offset just over tolerance fails
- wrong kind planar faces refused
- units invariant verdict mm vs in
**Parallel**
- parallel axes pass
- anti parallel still parallel
- perpendicular axes not parallel
- boundary at one degree
- two planar faces parallel
**Perpendicular**
- ninety degrees passes
- parallel is not perpendicular
- boundary half degree off
**Flush**
- coplanar faces pass
- parallel but stepped fails
- tilted faces fail
- offset boundary
- cylinder face refused
**Clearance**
- clears passes
- too close fails
- boundary at required gap
- measure failure surfaced
**Touching**
- within gap passes
- far apart fails
- zero distance flags overlap
**Concentric**
- coincident centers pass
- offset centers fail and point at coaxial
- boundary exactly at tolerance
- an arc edge also supplies a center
- cylindrical faces use their axis base point
- straight edge is refused as non circular
**Guards**
- unknown relation errors
- bad units errors
- negative tolerance deg errors
- no active design errors
- unresolvable entity surfaced
- passed is a declared output

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
- iscombine raise surfaces as error

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

## `model_set_material`

> Unit tests for ``model_set_material.py`` - assign a PHYSICAL material to bodies/component.

**HappyPath**
- assigns and reads back density in kg per m3
- case insensitive exact match
- declared outputs present
**SearchGuards**
- no match lists nearest candidates
- ambiguous across libraries is refused
- document material wins over ambiguous libraries
- empty material name errors
- no catalog reports no materials
**PartialSuccess**
- partial success surfaced
- all bodies fail is error
**DesignGuard**
- no active design

## `model_shell`

> Unit tests for ``model_shell.py`` - hollow a solid body into a thin-walled shell.

**ClosedShell**
- hollows most recent body into closed shell
- reports volume removed and face delta
- targets named solid body
**OpenShell**
- removes given faces and derives body
**Direction**
- inside sets inside thickness only
- outside sets outside thickness only
- both sets both thicknesses
- cm units scale thickness
**Guards**
- no active design
- bad units
- zero thickness rejected
- negative thickness rejected
- missing named body reports name
- wrong kind body redirects
**Honesty**
- unchanged body reports error not ok
- no feature returned is error
- add raising surfaces as error
**OutputContract**
- feature output is minted

## `model_split`

> Unit tests for ``model_split.py`` - SplitBody / SplitFace dispatched by 'split'.

**CutterGuard**
- no cutter is error
- both cutters is error
- bad split kind is error
- no active design
**SplitBody**
- two bodies is ok
- single body is error not silent ok
- target resolution error propagates
- cutter via tool body
- health error reported
- declared outputs present
**SplitFace**
- face delta reported
- no new faces is error
- missing faces is error
- faces reach createinput as collection

## `model_sweep`

> Unit tests for ``model_sweep.py`` - sweep a profile along a path into a solid/surface.

**Solid**
- solid sweep along path sketch
- path sketch seeds createpath with chain
- multiple result bodies collected
**Surface**
- open profile falls back to surface
- as surface forces surface off closed profile
**EdgePath**
- single edge path chains from seed
- bad edge handle errors
**Options**
- operation echoed
- orientation parallel applied
- target bodies rejected on new
- target bodies unresolved errors on cut
**Guards**
- missing profile
- unknown sketch profile
- unknown path sketch
- bad operation
- bad orientation
- no active design
**Honesty**
- no body created is error
- add returning none is error
- createinput failure surfaces
- add failure surfaces
**CrossComponentHost**
- sweep is built on the profiles owning component
- declared returns present in payload

## `no_duplicate_defs`

> Lint: no module under commands/mcpServer/ defines the same top-level function/class name twice.

**NoDuplicateDefs**
- no module shadows a top level def
- the lint bites

## `no_first_match_resolvers`

> Lint: the substring-first-match smell is banned across EVERY tool module, not just the ones already fixed (see ``test_occurrence_ref_lint.py`` for the resolver this smell should route through instead).

**NoFirstMatchResolverAnywhere**
- no tool hand rolls a substring name match
- allowlist entries still exist and still trip the smell
- reversed pattern bites
- containment and indexed shapes bite
- correct exact match is not flagged

## `no_hand_cast_product`

> Lint: acquire the active Design / CAM product through the resolver, not a hand cast.

**NoHandCastProduct**
- no design cast of active product
- no manual cam cast
- the lint bites

## `no_hand_seeded_enums`

> Lint: a MEASURED adsk enum member is never hand-assigned in a unit test - it comes seeded.

**NoHandSeededEnums**
- measured enum members are never hand assigned
- allowlist files still trip
- the lint bites

## `occurrence_ref_lint`

> Lint: single-occurrence resolution must go through the shared OccurrenceRef resolver.

**RoutedToolsStayOnSharedResolver**
- no routed tool hand rolls a substring name match
- routed tools reference the shared resolver
- the lint bites
**SharedResolverBehaviour**
- fullpath beats a same named instance
- ambiguous bare name errors

## `operations_shared`

> Lint: the operation-keyword -> FeatureOperations map is _common.OPERATIONS, never a local copy.

**OperationsShared**
- no local feature operations map
- the lint bites

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
**ReturnsVerdict**
- full verdict shape passes
- missing contract key is named
- non boolean passed rejected
- undeclared relation rejected
- produces note names the contract keys
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
- silent no op assignment bites
- setting the current expression is already current
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

## `postconditions_declared`

> Lint: every WRITE/DESTRUCTIVE tool declares postconditions - or carries a reasoned exemption.

**PostconditionsDeclared**
- every write tool declares or is exempt
- exemptions only name real undeclared write tools
- declared postconditions are postcondition kinds

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
**ClosedPathConstraintHonesty**
- addCoincident failure surfaces as error
- addCoincident success still closes the loop
**MarkConstructionHonesty**
- setattr failure propagates
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

## `sketch_delete_entity`

> Unit tests for ``sketch_delete_entity.py`` - surgically remove one sketch curve/point or constraint.

**DeleteCurve**
- delete line shrinks collection
- delete circle
- delete point
- out of range index errors
- delete that removed nothing is an error
- delete exception is reported
**DeleteConstraint**
- delete constraint shrinks collection
- constraint out of range errors
- constraint delete no effect is error
**Guards**
- missing sketch
- malformed target
- unknown type
- noninteger index

## `sketch_dimension`

> Unit tests for ``sketch_dimension.py`` — dimensional constraints + driven values.

**Dispatch**
- distance two lines
- horizontal orientation
- radius one circle
- diameter
- angle two lines
- vertical orientation
- distance to a circle anchors at its center
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

> Unit tests for sketch_get's routing between its two depths, and the summary's design-wide walk.

**SketchGetRouting**
- no name lists summary
- name delegates to detail engine
- whitespace name treated as no name
**SketchSummaryWalk**
- lists sub component sketches tagged with their owner

## `sketch_project`

> Unit tests for ``sketch_project.py`` - project existing model geometry into a sketch (Fusion's Project command, via Sketch.project2(entities, isLinked)).

**Project**
- projects and reports created count
- link true flows to project2
- link false flows to project2
- refs are type index from count delta
- refs offset by preexisting entities
- projected entities passed through
- note flags non addressable curves
**Honesty**
- zero created is error not false ok
**Guards**
- no sketch is error
- named missing sketch is error
- entity resolve error is surfaced
- no active design
**ReturnsContract**
- declared entity refs present in payload

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
- truncated is false when under the cap
- none text errors
**Create**
- creates text with scaled height
- create position scaled
- create requires sketch name
- create unknown units
- create nonpositive height
- create missing sketch
- create reports verified count delta
- create count delta from nonzero base
- create silent noop is error not false ok
- create add returns none is error
- create uses sketch plane not world coordinates

## `sketch_text_create`

> Unit tests for ``sketch_set_text.py`` CREATE path - make new sketch text from scratch.

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
- solid result contradicts the sheet note
- zero distance guard
- unknown operation rejected
- from edge curves uses edge profile
- no sketch no curves errors
- unknown units rejected
- join op and symmetric passed through
- sketch with no curves errors
- surface extrude built on the sketchs owning component
**SurfaceRevolve**
- sets isSolid false
- reports result is solid read back
- zero angle guard
- non numeric angle rejected
- unknown axis rejected
- surface revolve built on the sketchs owning component
**SurfacePatch**
- patch over closed edge loop
- single edge passes edge for autocomplete
- null feature errors
- unknown operation rejected
- boundaries patches every loop in one call
- boundaries reports per loop failure without aborting
- neither boundary nor boundaries errors
- unknown continuity rejected
- continuity set failure surfaces as error
- continuity tangent set on input
- boundaries all fail reports zero patched

## `surface_delete_face`

> Unit tests for surface_delete_face - delete faces, optionally healing, with a face-count read-back.

- plain delete reports face count delta
- heal routes to deleteFaceFeatures
- consumed body is reported
- heal failure is error pointing to no heal
- heal null feature is error
- faces from two bodies tracked
- missing faces rejected

## `surface_edit`

> Unit tests for surface_edit.py — EDIT open surface bodies (trim/extend/offset/thicken).

**SurfaceTrim**
- commits via add on success
- trim selects a cell before add
- trim that removes no area bites
- trim that shrinks area passes
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
- thicken that stays a surface bites
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
- loft built on the profiles owning component
**Stitch**
- became solid true on watertight
- became solid false when gaps remain
- rejects solid input
- fewer than two rejected
- tolerance scaled to cm
- default tolerance when omitted
- default tolerance reported in caller units not raw cm
- unknown units rejected
- unknown operation rejected
- became solid false when only some result bodies closed
**Unstitch**
- explode body uses add not createInput
- peel faces
- needs target or faces
- target and faces both rejected
- null feature is error

## `surface_reverse_normal`

> Unit tests for surface_reverse_normal - flip open-surface normals with an isParamReversed read-back.

- confirms flip via isparamreversed readback
- noop reported honestly not confirmed
- all input bodies handed to add
- solid body rejected
- missing bodies rejected
- null feature is error

## `surface_untrim`

> Unit tests for surface_untrim - restore a trimmed surface face to its natural extent.

- extent grew true when area increases
- no growth reported honestly
- loop type maps to enum
- extension scaled to cm
- solid face rejected
- unknown loop type rejected
- missing faces rejected
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

## `sys_execute_script`

> Unit tests for ``sys_execute_script``'s pure error-shaping helper.

**ExtractScriptError**
- returns only the inner traceback
- console noise lines stripped
- single traceback returned whole
- non traceback text passes through
**HandlerGuard**
- script without run function is rejected

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

## `sys_reload_addin`

> Unit tests for ``sys_reload_addin._purge_addin_modules`` -- the sys.modules cache-bust that lets a reload re-import EDITED files instead of handing back the stale cached objects. The only pure logic here is which module names it purges vs keeps, by comparing each module's __file__ against the add-in's root folder; that decision is exercised directly against the real ``sys.modules`` dict via ``monkeypatch.setitem`` (auto-restored, so the test can't leak a fake module entry into the rest of the suite).

**PurgeAddinModules**
- purges a module whose file is under the addin root
- keeps a module whose file is outside the addin root
- keeps a module with no file attribute
- skips none entries without raising
- return value counts only the purged modules

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
**SelectionCap**
- under cap untruncated and unchanged
- at cap truncates and flags

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

## `tool_citations`

> Lint: a tool name cited in the constitution docs resolves to a registered tool.

**ToolCitations**
- cited tool names are registered
- not a tool entries still cited and still not tools

## `tool_naming`

> Lint/contract for the TOOL NAMING SCHEMA (CLAUDE.md "Read vs Edit").

**ToolNaming**
- every name is domain verb
- verb is in the closed set
- exemption tables match reality
- verb kind matches write status

## `tool_verify_complete`

> Gate: every registered tool is accounted for in the live tool verification.

**ToolVerifyComplete**
- every tool is covered excluded or pending
- no stale table entries
- pending and covered are disjoint

## `tool_verify_receipt`

> The tool_verify receipt: the source hash + VERIFIED_TOOLS.md stamp binding a green live run to the exact tool source it exercised.

**SourceHash**
- same tree hashes identically
- content change changes hash
- rename changes hash
- crlf and lf hash identically
- non py and pycache are ignored
- paths hash with forward slashes
**VerifiedReceipt**
- round trip write then check is current
- check goes red when source changes after stamp
- check goes red without a receipt
- check goes red on a stampless receipt
- receipt carries stamp counts and ledger rows

## `unit_coverage_complete`

> Gate: every tool module is exercised by a unit test, or excused with a recorded reason.

**UnitCoverageComplete**
- every tool module is tested or excused
- no stale excuses

## `units_scaled`

> Lint: unit<->cm conversion routes through _common.scale, never a re-inlined or copied table.

**UnitsScaled**
- no raw unit to cm access outside common
- no local unit table copy outside common
- the lint bites

## `units_typed`

> Lint: a length/coordinate input carries its unit in a typed selector, not in loose prose.

**UnitsAreTyped**
- numeric input naming a unit has a units selector

## `version`

> Version story: one version source (version.py), reported on the wire and matched by the changelog.

- version is semver shaped
- changelog top entry matches version
- server info version comes from version module

## `view_inspect`

> Unit tests for ``view_inspect.py`` — the agent's view verbs.

**Guards**
- unknown action
- no design
**Visibility**
- hide turns bulb off
- isolate sets flag
- isolate requires single match
- ambiguous substring refused not first match
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
- same named documents do not collide

## `view_screenshot`

> Unit tests for ``view_screenshot.py`` _isolate_for_fit - the fit_to visibility helper.

**IsolateForFit**
- hides others and restores
- substring match
- no match returns none
- ambiguous name refused not first match
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
- list input
- list input all keyword expands
- empty list falls back to default

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

## `wire_ascii`

> Lint: every agent-facing wire string is pure ASCII (CLAUDE.md "Tool descriptions").

**ToolDescriptionsAreAscii**
- every tool description is ascii
- every input description is ascii
**DescriptionConstantsAreAscii**
- every description constant is ascii

## `wire_budget`

> Wire budget: the tools/list payload an agent pays for every session stays under a ceiling.

- tools list payload within budget
- no single tool exceeds wire ceiling
- no description exceeds ceiling
- override table matches reality

## `wire_shape`

> Wire format: the JSON-RPC tools/list response includes annotations and strict schemas.

**ToolsListWireFormat**
- tools list sends annotations on the wire
- all entries have required keys
- read only tools have correct annotations
- no audience priority lastmodified in annotations
- strict schema tool has additional properties false
- destructive tools marked on the wire
- all descriptions non empty
- read only hint is bool
- destructive hint when present is bool

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
**DesignWideCounts**
- sketches in sub components are counted with empty root
- bodies summed across root and sub components
- single component design matches root
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
- doc switching write reports the new doc
**IntegrationThroughItem**
- write tool gains expect document read does not

## `write_status_annotations`

> Lint: every registered tool must declare a write-status annotation.

**WriteStatusDeclared**
- every tool declares read only hint
- read only tools are not destructive
- hints serialize into the tool payload

