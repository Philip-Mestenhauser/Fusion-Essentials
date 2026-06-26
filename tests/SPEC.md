# Behavior Spec (generated)

_Auto-generated from the test suite by `tests/gen_spec.py`. Do not edit by
hand — every line below is pinned by a passing test. Re-run the generator
after changing tests._

**Tools with a test file:** 20  |  **Behaviors pinned:** 257

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

