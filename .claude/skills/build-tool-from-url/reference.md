# Reference - building a tool from a vendor page

Loaded on demand by the `build-tool-from-url` skill when a step needs the reasoning behind it, not
just the instruction. No shop-specific data here - only the facts that justify how the skill is
built. Each contract is stated once, here or in `SKILL.md`, not both.

## The document tool library needs a CAM PRODUCT, never a CAM SETUP

`cam_edit_tools(scope='document')` needs the document's CAM product to exist. Entering the
Manufacture workspace once (`view_switch_workspace(workspace='manufacture')`) is what creates it -
nothing else is required. A setup is an entirely separate structure (a machining job: selects
bodies, holds toolpaths) with no relation to the tool library; never create one to satisfy a
tool-library precondition - it is needless work with its own side effects (an unwanted setup
machining a body the operator may not want machined yet). The tools' own error text teaches this
same fix when the CAM product is missing. The skill defers this step until after Phase 4's gate, so
a failed gate never changes the workspace.

## Discovering the `from_type` vocabulary

`cam_edit_tools(action='list_types')` returns the live list of tool geometry families the add
action can clone (it walks Fusion's bundled sample libraries at run time, so the list always matches
the installed Fusion). It needs no scope, no library, and no CAM product - safe to call first.

## Why there's no vendor-vocabulary lookup table

There is no enum anywhere to mirror into a static table - the vocabulary is whatever the installed
Fusion's sample libraries contain (which is why `list_types` reads it live), and any hand-written
mapping from vendor phrasing to `from_type` would silently drift. Classification is deliberately
ordinary agent reasoning: vendor phrasing for the same geometry family varies too much across sites
("corner radius end mill" vs. "bull nose" vs. a bare radius value that implies the same thing) to
enumerate. Reading a spec sheet and picking the best-matching entry from a known list is a skill an
agent already applies elsewhere - no special-purpose classifier needed.

## WebFetch is lossy - use a raw fetch + deterministic parse, and mine JSON-LD first

WebFetch converts HTML to Markdown and summarizes it with a small model before the content is seen;
that step is not configurable and is lossy by design. A dimension that gets machined into part
geometry must not pass through an unverified paraphrase. So: fetch the raw page (`curl -s -A
"Mozilla/5.0" "<url>"` via Bash - the UA header avoids some vendor bot blocks) and parse
deterministically (author the extraction fresh per run; a hardcoded parser drifts against the
vendors' HTML):

1. **Mine structured data first.** Parse every `<script type="application/ld+json">` block before
   anything strips scripts. A schema.org `Product` block commonly carries `name`, `sku`/`mpn`
   (product id), `brand` (vendor), and sometimes dimensions as clean JSON - more trustworthy than
   scraping the rendered table, and destroyed if scripts are stripped first.
2. Strip `<script>`/`<style>` blocks.
3. Replace every remaining tag with a single separator marker (not deleted - the boundary matters).
4. Collapse whitespace/`&nbsp;` runs to a single space.
5. Collapse RUNS of the separator down to one - vendor spec tables put empty cells between a label
   and its value (Hoffmann's markup has 2-3), and skipping only single separators misses fields.
6. For each checklist field, regex the label (tolerating a short trailing symbol letter - "Overall
   length L", "Cutter diameter Dc"), then capture the first following number+unit token.
7. Keep the raw matched substring alongside the parsed value - the report shows both.

A vendor site that renders specs as an image, a PDF, or a JS-only widget yields nothing to parse -
Phase 4's gate catches that and stops the skill, with WebFetch as an explicitly lower-confidence
fallback, never a silent equal.

## Unit inference for bare numbers

A dimension keeps its own unit token, passed straight into Fusion's expression parser (`"3 mm"`,
`"0.125 in"`). A BARE number (no unit) is inferred in order: the field's column header/units legend,
then a page-wide unit declaration (locale, a "dimensions in mm" note), then a sibling dimension's
unit in the same spec block. If two readings stay plausible (`3` as 3 mm or 3 in), the skill asks
rather than assumes - a machined length is not a place to default a unit.

## The corner-radius-to-diameter ratio as a disambiguation signal

The load-bearing geometric check when vendor prose is ambiguous or misleading:
- corner radius == 0 (or absent/negligible): a flat/square end mill.
- 0 < corner radius < diameter/2: a bull-nose / corner-radius end mill.
- corner radius == diameter/2 (within rounding): a ball-nose / full-radius end mill.
- a stated included angle or taper with no flute-length-scale radius: a chamfer mill or countersink,
  not an end mill family at all.
Vendor terminology is imprecise about this (a "slot drill" naming a ball-nose profile is a real
case) - the dimensional ratio outranks the vendor's own category label when they disagree.

## Reading a tool's real parameters - never guess a name

`cam_edit_tools(action='parameters', scope='document', tool=<index>)` returns every parameter of one
tool: its `name`, `expression`, evaluated `value`, and a `formula_source` flag for a parameter whose
expression is another parameter's name. This is how the reconcile step (Phase 5) learns the ACTUAL
parameter names on the built tool, so it edits real names instead of guessing - the defect this read
exists to close. `action='list'` returns only a summary (diameter/flutes/type/description/number and
the tool's product identity); the full per-dimension read is `action='parameters'`.

## `product_id` / `vendor` land on the tool at add time - and a clean add proves it

Passing `product_id`/`vendor` on an `add_tools` entry sets the tool's `tool_productId`/`tool_vendor`
parameters, and the add reads them back and ERRORS if either failed to land - so an error-free
`{"added": N}` is a verified claim, no post-add confirmation needed. The `action='list'` summary does
not surface these two fields, so a later audit reads them via `action='parameters'`, not a list row.

## Formula-derived dimensions - report, don't overwrite

Some tool dimensions are formulas tracking another parameter (a flat end mill's `tool_shoulderLength`
is the expression `tool_fluteLength`, not an independent value); `action='parameters'` flags these
with `formula_source`. Editing such a parameter succeeds but returns a warning naming the
relationship being overwritten. The shop policy: leave the formula intact - accept Fusion's
tool-modeling choice - and report the vendor's un-appliable value as `deliberately not applied` in
Phase 6 rather than forcing an exact match that silently breaks the tool's internal relationship. A
warning-free edit means the target held a literal and was safe to set.

## Fusion parameter names for the reconcile step - NEEDS-LIVE-VERIFICATION

The reconcile step matches each vendor spec field to a Fusion tool parameter. The table below is the
current best knowledge, NOT an asserted contract: a name is filled in only where the repo's own code
or tests already use it (source cited); a field whose Fusion parameter name the repo has not yet
confirmed is left blank, to be filled by the one-time live probe.

The AUTHORITATIVE source for any name is always the live read on a real tool -
`cam_edit_tools(action='parameters', scope='document', tool=<i>)` - not this table. When a blank row
matters for a build, read the real names rather than guessing.

| Vendor spec field | Fusion parameter | Evidence |
|---|---|---|
| cutting / cutter diameter | `tool_diameter` | cam_edit_tools.py (diameter override), test_cam_edit_tools.py |
| number of flutes | `tool_numberOfFlutes` | cam_edit_tools.py (summary), test_cam_edit_tools.py (edit) |
| length of cut / flute length | `tool_fluteLength` | cam_edit_tools.py (`_formula_source`), test_cam_edit_tools.py |
| shoulder length | `tool_shoulderLength` (often formula = `tool_fluteLength`) | cam_edit_tools.py, test_cam_edit_tools.py |
| product id / part number | `tool_productId` | cam_edit_tools.py (`_build_entry`) |
| vendor name | `tool_vendor` | cam_edit_tools.py (`_build_entry`) |
| spindle speed (preset) | `tool_spindleSpeed` | cam_edit_tools.py (preset path) |
| cutting feed (preset) | `tool_feedCutting` (mill) / `tool_feedPlunge` (drill) | cam_edit_tools.py (`_FEED_PARAM_CANDIDATES`) |
| overall length (OAL) | *(blank - verify live)* | not in repo; read via `action='parameters'` |
| shank / shaft diameter | *(blank - verify live)* | not in repo; read via `action='parameters'` |
| corner radius | *(blank - verify live)* | not in repo; read via `action='parameters'` |
| taper / included angle | *(blank - verify live)* | not in repo; read via `action='parameters'` |

**To confirm the blank rows once (a live probe, run with a document open):** build one tool of each
relevant family, then `cam_edit_tools(action='parameters', scope='document', tool=<i>)` and read the
returned `name`s; the OAL / shank / corner-radius / taper parameter names are authoritative from that
read. Fill the blank rows from it and drop the NEEDS-LIVE-VERIFICATION marking on this section.

## Presets are opportunistic

Feeds/speeds presets are a nice-to-have capture of data the vendor page might already show (a
materials table, a max RPM), never something the skill computes, derives, or needs for the tool to
be usable. Their absence gets no note in the final report - it isn't a gap, just nothing to capture
this time.
