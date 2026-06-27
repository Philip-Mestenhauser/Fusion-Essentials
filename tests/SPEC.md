# Behavior Spec (generated)

_Auto-generated from the test suite by `tests/gen_spec.py`. Do not edit by
hand — every line below is pinned by a passing test. Re-run the generator
after changing tests._

**Tools with a test file:** 35  |  **Behaviors pinned:** 446

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
**RigidGroup**
- groups named occurrences
- include children flag
- needs at least two
- missing reported

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

> Unit tests for ``capture_views.py`` — capture several orthographic/iso views in one call.

**ParseViews**
- default set
- explicit comma list
- whitespace and case tolerant
- dedupes preserving order
- unknown view errors
- all keyword expands to six orthos

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

## `create_component`

> Unit tests for ``create_component.py`` — make a new empty component occurrence.

**CreateComponent**
- creates component
- names the component
- placed at position scales to cm
- no position uses identity
- activate makes it edit target
- no activate by default
- unknown units errors
- no active design errors

## `data_management`

> Unit tests for ``data_management.py`` path helpers — pure string/tree logic.

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

## `edit_joint`

> Unit tests for ``edit_joint`` — edit an EXISTING joint in place (no remaking).

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

## `inspect_view`

> Unit tests for ``inspect_view.py`` — the agent's view verbs.

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

> Unit tests for ``joints_advanced.py`` — capture_position, as_built_joint, assembly_constraint.

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

> Unit tests for the ``measure_bounding_box`` MCP tool.

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

## `open_document`

> Unit tests for ``open_document.py`` identifier parsing.

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

## `section_view`

> Unit tests for ``section_view.py`` — the Section Analysis cutaway tool.

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

> Unit tests for ``set_nc_program_comment.handler`` — the empty-input guard and multi-program behaviour.

**EmptyInputGuard**
- empty comment and no set name is refused
- whitespace only comment no set name refused
- real comment goes through
- set name only is allowed
**MultiProgramPreValidation**
- uneditable program aborts before any write
- all editable applies to all

## `show_toolpath`

> Unit tests for ``show_toolpath.py`` — CAM toolpath display control.

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

> Unit tests for ``sketch_constraint.py`` — apply geometric constraints to sketch entities.

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

## `tier2_misc`

> Unit tests for assorted Tier-2 helpers: update_xref, timeline, generate_toolpaths.

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

