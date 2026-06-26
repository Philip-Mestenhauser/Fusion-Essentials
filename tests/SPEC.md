# Behavior Spec (generated)

_Auto-generated from the test suite by `tests/gen_spec.py`. Do not edit by
hand — every line below is pinned by a passing test. Re-run the generator
after changing tests._

**Tools with a test file:** 15  |  **Behaviors pinned:** 133

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

