# Behavior Spec (generated)

_Auto-generated from the test suite by `tests/gen_spec.py`. Do not edit by
hand — every line below is pinned by a passing test. Re-run the generator
after changing tests._

**Tools with a test file:** 55  |  **Behaviors pinned:** 694

## `active_component`

> Unit tests for the active-component targeting fix in sketches.py + extrude.py.

**SketchesTargetComponent**
- uses active component when present
- falls back to root when no active
- falls back to root when active is none
**ExtrudeTargetComponent**
- uses active component when present
- falls back to root

## `api_doc`

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

## `arrange`

> Unit tests for ``arrange.py`` — pack shapes within a sketch-profile boundary (Arrange feature).

**SolverType**
- true shape default
- rectangular
- unknown solver errors
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

## `assembly`

> Unit tests for ``assembly.py`` — occurrence ground/move + rigid group.

**Ground**
- ground pins in space
- unground from parent releases lock
- both flags independent
- substring match
- missing occurrence errors
- no change requested errors
**Move**
- translate sets transform
- translation scaled to cm
- missing occurrence errors
- zero move errors
- rotate world axis
- multi axis rotation
- single and multi rejected together
- rotate about edge handle
**RigidGroup**
- groups named occurrences
- include children flag
- needs at least two
- missing reported

## `assembly_probe`

> Unit tests for ``assembly_probe.py`` — structured kinematic state of an assembly.

**Guards**
- unknown units
**Probe**
- positions scaled to display units
- ground flags and grounded list
- joint type and dof mapping
- rigid and cylindrical dof
- occurrence joint cross index
- include joints false skips
**Health**
- all healthy
- broken joint surfaced
- timeline problem surfaced
- health message deduped

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
- no cam product errors

## `cam_edit_operation`

> Unit tests for ``cam_edit_operation.py`` — set CAM operation parameters (feeds/speeds/stepdown/...).

**EditOperation**
- sets param dict
- accepts name equals value strings
- unknown param reported
- unknown operation
- invalid value reports and does not partially apply
- no parameters errors

## `cam_info`

> Unit tests for ``cam_info.py`` pure helpers.

**Hms**
- zero
- under a minute pads seconds
- minutes and seconds padded
- hours not padded minutes seconds are
- rounds fractional seconds
- garbage input is safe
**OperationSummary**
- valid state name
- invalid state is out of date
- no toolpath state is out of date
- suppressed invalid is not out of date
- warning text surfaced
- unknown state falls back to raw value

## `cam_templates`

> Unit tests for ``cam_templates.py`` navigation logic.

**FindTemplateByName**
- unknown location is rejected
- finds template in root
- match is case insensitive
- descends into subfolders
- not found returns none

## `capture_views`

> Unit tests for ``view_screenshot_multi.py`` — capture several orthographic/iso views in one call.

**ParseViews**
- default set
- explicit comma list
- whitespace and case tolerant
- dedupes preserving order
- unknown view errors
- all keyword expands to six orthos

## `combine`

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
- comma string tools parsed
- keep tools flag
- new component flag

## `common`

> Unit tests for the shared tool helpers (tools/_common.py).

**ResponseBuilders**
- ok wraps payload as json text
- error sets flag and mirrors message
- underscore aliases are the same callables
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

## `component_tree`

> Unit tests for ``component_tree.py`` occurrence search.

**FindOccurrenceByName**
- substring match on occurrence name
- exact match on component name
- descends into children
- no match returns none
- empty tree returns none

## `configurations`

> Unit tests for ``configurations.py`` pure logic.

**RowSummary**
- marks active row
- non active row
- none id is never active
**FindRow**
- match by name
- match by id when no name matches
- name wins over id collision
- no match returns none
**CollectTruncation**
- truncates at row cap
- no truncation under cap

## `construction`

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

## `create_component`

> Unit tests for ``model_create_component.py`` — make a new empty component occurrence.

**CreateComponent**
- creates component
- names the component
- placed at position scales to cm
- no position uses identity
- activate makes it edit target
- no activate by default
- unknown units errors
- orientation rotation
- unknown rotate axis errors
- no active design errors

## `data_hubs`

> Unit tests for ``data_hubs.py`` — list Autodesk data hubs and switch the active one.

**List**
- lists all hubs with active flag
- default action is list
**Switch**
- switch by name
- switch by id
- switch case insensitive name
- already active is noop
- unknown hub errors and lists available
- switch requires hub
- unknown action errors

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
- activate named
- requires name
- unmatched errors
**ListOpenDocuments**
- lists with state and flags active
**FindOpenDocument**
- exact then substring
- substring when no exact
**DeleteFolderGate**
- empty folder deletes without recursive confirm
- nonempty force without recursive confirm returns preview and refuses
- nonempty with recursive confirm deletes
- recursive confirm must match name
- subtree counts walks recursively

## `data_model`

> Unit tests for ``data_model.py`` — the project/file lister and its folder filter.

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
- missing named target errors
**PathHandling**
- missing path errors
- extension auto appended

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

## `extrude`

> Unit tests for ``extrude.py`` — turn a sketch profile into a solid.

**Guards**
- unknown units
- zero distance
- unknown operation
- no sketch named
- profile index out of range
- no profile in sketch
**Extrude**
- basic new body scales distance to cm
- inch scaling
- most recent sketch when unnamed
- operation mapping cut
- symmetric flag passed
- negative distance allowed
**ToObject**
- extrude to face uses to entity extent
- to object overrides distance
- bad to object handle errors
**TargetBodies**
- cut scoped to bodies
- target bodies by handle
- target bodies rejected on new
- bad target body errors

## `fillet`

> Unit tests for ``fillet.py`` — model_fillet + model_chamfer.

**Guards**
- unknown units
- nonpositive radius
- body not found
- bad edge filter
**Fillet**
- fillet all edges scaled
- fillet convex filter
- fillet concave filter
- default most recent body
**Chamfer**
- chamfer scales distance
- two distance chamfer
- equal distance when no second
**EdgeHandles**
- fillet specific edges via handles
- edges take precedence over body
- bad edge handle errors

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

## `get_screenshot`

> Unit tests for ``get_screenshot.py`` _isolate_for_fit — the fit_to visibility helper.

**IsolateForFit**
- hides others and restores
- substring match
- no match returns none
- already hidden others not restored on

## `inputs`

> Unit tests for the typed INPUT KINDS framework (_inputs.py).

**GeometryHandle**
- resolves planar face
- rejects wrong geometry kind
- stale handle error
- contract note names the required kind
- schema includes contract note
**GeometryHandleList**
- resolves list of edge handles
- accepts comma string
- one bad handle fails with index
- wrong kind in list rejected
- empty optional returns empty list
- schema is array
**PlaneRef**
- origin alias
- alias front maps to xz
- construction plane by name
- planar face handle
- curved face handle rejected
- unknown string
- contract note mentions all three sources
**AxisRef**
- world axis
- edge handle axis
- curved edge rejected
- unknown axis string
**DistanceUnits**
- distance scaled by units
- distance nonzero guard
- unit field returns scale
- unknown unit
**Choice**
- valid option
- invalid option
- default when empty
**ResolveInputs**
- resolves all with unit dependency
- first failure short circuits
**Generation**
- contract block lists each input
- apply to tool adds properties and required

## `insert_occurrence`

> Unit tests for ``insert_occurrence.py`` placement transform.

**Placement**
- default identity
- position scales to cm
- rotation built
- bad units
- bad rotate axis

## `inspect_view`

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
- restore counts missing occurrences

## `joint`

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

## `joint_origin`

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

## `joints_advanced`

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

## `main_thread_timeout`

> Unit tests for the main-thread task timeout correctness (maintainer block #3).

**CancelReturnsWhetherItWon**
- cancel pending task returns true
- cancel already claimed task returns false
- cancel empty id returns false
**ItemEnforceTimeoutFlag**
- defaults to enforced
- can opt out
- execute api script item is timeout exempt

## `measure_bounding_box`

> Unit tests for the ``model_measure_bbox`` MCP tool.

**UnitConversion**
- world extents in mm
- world extents in inches
- unknown units is an error not a crash
**MeasurableGeometry**
- zero bodies returns none
- single body is used directly
- picks largest body by volume
- brep body passed through unchanged
**ResultContract**
- ok shape
- error shape carries message

## `mirror`

> Unit tests for ``mirror.py`` — mirror solid bodies across an origin plane.

**Guards**
- bad plane
- no bodies
- body not found
**Mirror**
- mirror across yz
- comma string bodies
- join sets iscombine

## `open_document`

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

## `parameters`

> Unit tests for ``parameters.py`` pure logic.

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
- health handler reports healthy
**AddHandler**
- add rejects duplicate
- add succeeds when timeline stays healthy
- add rolls back on new timeline error
- add requires name and expression
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

## `patterns`

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
**Circular**
- basic full ring
- axis selection
- symmetric flag
- quantity must be at least two
- unknown axis
**BodyTargets**
- rectangular patterns bodies by name
- circular patterns bodies by handle
- bodies take precedence over occurrences
- bad body name errors

## `polyline`

> Unit tests for the polyline / closed_path sketch kind in sketches.py.

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

## `revolve`

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
- second angle ignored when symmetric

## `section_view`

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
**ThroughCenter**
- xy uses z center
- front uses y center
- through adds explicit offset on top of center
- through defaults to xz when no plane
- through substring match
**ListClear**
- list reports sections
- clear removes all

## `selection`

> Unit tests for the ``selection.py`` MCP tool's pure logic.

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

## `set_nc_program_comment`

> Unit tests for ``cam_set_nc_comment.handler`` — the empty-input guard and multi-program behaviour.

**EmptyInputGuard**
- empty comment and no set name is refused
- whitespace only comment no set name refused
- real comment goes through
- set name only is allowed
**MultiProgramPreValidation**
- uneditable program aborts before any write
- all editable applies to all

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

## `sketch_constraint`

> Unit tests for ``sketch_constrain.py`` — apply geometric constraints to sketch entities.

**ResolveEntity**
- line index
- arc circle point
- bad type
- out of range
- malformed
**TwoCurve**
- perpendicular
- parallel equal tangent concentric collinear
- two curve needs entity two
**PointCurve**
- midpoint
**SingleLine**
- horizontal
- fix sets isfixed
- unfix
**Symmetry**
- symmetry uses symmetry line
- symmetry needs symmetry line
**Guards**
- unknown constraint
- missing sketch
- unresolvable entity

## `sketch_detail`

> Unit tests for ``sketch_detail.py`` — read the full structure of one sketch.

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
**Guards**
- missing sketch
- no name lists available

## `sketch_dimension`

> Unit tests for ``sketch_dimension.py`` — dimensional constraints + driven values.

**Dispatch**
- distance two lines
- horizontal orientation
- radius one circle
- diameter
- angle two lines
**Guards**
- unknown dim type
- bad entity one
- distance needs entity two
- value optional

## `sketch_get_merge`

> Unit tests for the merged sketch_get read tool (chunk B of the refactor).

**SketchGetRouting**
- no name lists summary
- name delegates to detail engine
- whitespace name treated as no name

## `sketch_text_create`

> Unit tests for ``set_sketch_text.py`` CREATE path — make new sketch text from scratch.

**CreateText**
- creates text in named sketch
- create requires sketch name
- create missing sketch errors
- create rejects nonpositive height

## `sketches`

> Unit tests for ``sketches.py`` pure logic.

**Scale**
- mm
- cm
- inch aliases agree
- default when blank is mm
- case and whitespace tolerant
- unknown unit is none
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

## `tier2_misc`

> Unit tests for assorted Tier-2 helpers: doc_update_xref, timeline, cam_generate.

**RefName**
- reads datafile name
- missing datafile falls back
**EntityType**
- group returns timelinegroup
- entity class name
- none entity returns none
**LiveOpTally**
- counts states
- active op captured with real progress
- cam unavailable returns none

## `visibility`

> Unit tests for ``visibility.py`` occurrence resolution + state read.

**FindOccurrences**
- exact match preferred over substring
- substring fallback when no exact
- matches on full path
- no match returns empty
**OccState**
- snapshots all visibility flags

