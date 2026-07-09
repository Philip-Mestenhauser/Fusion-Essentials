# Reference - building a tool from a vendor page

Loaded on demand by the `build-tool-from-url` skill when a step needs the reasoning behind it, not
just the instruction. No shop-specific data here - only the API facts and verified findings that
justify how the skill is built.

## The document tool library needs a CAM PRODUCT, never a CAM SETUP

Verified live, and a real mistake to guard against: `cam_edit_tools(scope='document')` resolves via
`get_cam()` (`_cam_common.py`), which needs `doc.products.itemByProductType('CAMProductType')` to
exist. On a document that has never had CAM data, that product doesn't exist yet - `get_cam()`
returns the error `"This document has no CAM (Manufacture) data. Open a document with setups, or
create them in the Manufacture workspace."` That error text (and separately, `cam_create_setup`'s own
tool description, which says "switch to Manufacture once if the doc has no CAM data") both read as if
a SETUP is the fix. It is not. The CAM product is created just by entering the Manufacture workspace
(`view_switch_workspace(workspace='manufacture')` -> `match.activate()`, nothing CAM-specific beyond
that) - a setup is an entirely separate structure (a machining job: selects bodies, holds toolpaths)
that has nothing to do with the tool library.

Confirmed by a live A/B on 2026-07-08: creating a scratch `cam_create_setup`, then successfully
adding+listing a tool, then DELETING the scratch setup (`cam_delete`) - the added tool remained in
`cam_edit_tools(action='list', scope='document')` completely unaffected by the setup's removal. So
Phase 1's discovery probe should be tried FIRST; only fall back to `view_switch_workspace` if it
specifically fails with the "no CAM data" message, and never reach for `cam_create_setup` as part of
this skill - it does needless work with its own side effects (an operator now has an unwanted setup
sitting on their document, machining a body they may not want machined yet).

## Why there's no vendor-vocabulary lookup table

An earlier draft of this skill considered a static table mapping vendor phrasing ("corner radius",
"ball nose", "square end") to Fusion `from_type` values. That was dropped: `cam_edit_tools`'s
`_build_type_map()` (`commands/mcpServer/tools/cam_edit_tools.py`) builds `{tool_type: (library_url,
index)}` at RUN TIME by walking Fusion's bundled sample libraries (`Milling Tools (Metric)`, `Hole
Making Tools (Metric)`, `Cutting Tools (Metric)`) and reading each sample tool's own `tool_type`
parameter - there is no enum anywhere in the source to mirror into a table, and any hand-written
table would silently drift from whatever the installed Fusion version actually ships. Classifying
against the live-discovered list (Phase 1) is both simpler and correct-by-construction.

The classification step itself is deliberately left to ordinary agent reasoning rather than a rules
engine: vendor phrasing for the same geometry family varies too much across sites ("corner radius
end mill" vs. "bull nose" vs. a radius value that implies the same thing without naming it) to
enumerate. An agent reading a spec sheet and picking the best-matching entry from a known list is the
same skill it already applies elsewhere - no special-purpose classifier needed.

## The `from_type` discovery probe, and why it's cheap and safe

`cam_edit_tools(action='add', scope='document', add_tools=[{"from_type": "__probe__"}])` is
deliberately built to fail: `_sample_for_type` in `cam_edit_tools.py` returns an error
`"No sample tool of type 'x'. Available types: ..."` before any document mutation happens (`_do_add`
validates every entry via `_build_entry` BEFORE adding any of them - see the "build ALL entries
before adding any" comment in the source). So the probe is read-only in effect despite being routed
through a write-shaped tool: nothing lands in the tool library, and the error text is the only
useful output. This is the ONLY reliable way to enumerate the vocabulary today - there is no
`action='list_types'` or equivalent.

**Only `scope='document'` reaches this error - checked live against the source, not assumed.**
`_resolve_target(scope, library)` branches before `_build_entry` is ever called: `scope='document'`
calls `get_cam()` (fails cleanly if no CAM product is open, with ITS OWN error, never reaching
`_build_entry`); a shared scope (`local`/`cloud`/`hub`) instead needs a `library` name/url resolved
first, which also fails or succeeds before `_build_entry` runs. So the type-vocabulary error text is
reachable ONLY through `scope='document'` with a CAM product already open - there is no scope that
discovers the vocabulary "for free" without one. This is why Phase 1 folds the CAM-job precondition
into the SAME step as vocabulary discovery instead of deferring it to the Phase 4 gate: discovery
itself can't happen without it, so checking it twice would be redundant, not extra-safe.

## WebFetch is documented as lossy - use a raw fetch + deterministic parse instead

The skill originally used the built-in `WebFetch` tool for Phase 2. That was a real design mistake,
caught the hard way: the first live run extracted "overall length: 76mm" via WebFetch when the
vendor page actually said 57mm, and the mismatch only surfaced because the operator independently
compared their own browser screenshot of the page against the built tool. Anthropic's own
documentation for WebFetch says the conversion-to-markdown-then-summarize-with-a-small-model step
"is not configurable" and is "lossy by design" for exactly this class of task, recommending `curl`
via Bash for the unprocessed page when byte-exact extraction matters (confirmed via the
`claude-code-guide` agent, 2026-07-08). A dimension that gets machined into part geometry is exactly
the kind of value that must not pass through an unverified paraphrase step.

**The fix: fetch raw HTML (`curl -s -A "Mozilla/5.0" "<url>"` via Bash), then parse deterministically
with a script - never route the numbers through WebFetch's model.** Verified live against both sample
vendor pages (2026-07-08) that this generalizes:

- **Hoffmann Group** (`hoffmann-group.com/.../p/<id>`): raw fetch succeeds (a UA header avoided a bot
  block that occurred without one, on at least one attempt). Spec table markup is `<td>label</td>` /
  `<td>value&nbsp;unit</td>` pairs, but with 2-3 EMPTY `<td>` cells between label and value in the
  observed markup, and labels carry a trailing symbol letter ("Overall length L", "Cutter diameter
  Dc", "Number of effective cutters... Z") that must be tolerated, not matched literally. A naive
  "grab the first number right after the label text" regex misses the value entirely because of the
  empty-cell gap - collapsing RUNS of empty separators (not just single ones) before matching is
  required, confirmed by direct inspection of the tag-stripped text stream.
- **Harvey Tool** (`harveytool.com/products/tool-details-<id>`): raw fetch succeeds with no special
  headers needed in testing. Markup is `<span class="dimension-text">Label:</span>` /
  `<span class="dimension-value">Value</span>` pairs - a different tag shape than Hoffmann's table,
  but the same "strip tags to a text stream, then regex the label-adjacent number" approach handles
  both without a vendor-specific parser, PROVIDED the empty-separator-collapse step above is present.

**The general extraction shape that works on both** (a sketch, not a shipped script - author it fresh
per run, since a hardcoded parser would be one more thing to keep in sync with two vendors' HTML,
against the whole point of staying dependency-light):
1. Strip `<script>`/`<style>` blocks.
2. Replace every remaining tag with a single separator marker (not deleted - the boundary matters).
3. Collapse whitespace/`&nbsp;` runs to a single space.
4. Collapse RUNS of the separator marker (not just consecutive ones from step 2, but ones left after
   whitespace collapse too) down to one - this is the step that was missing on the first attempt and
   caused every field to come back empty against the real Hoffmann page.
5. For each checklist field, regex: `<label>[A-Za-z]{0,3}\s*<sep>?\s*([0-9][0-9.,/]*\s*(?:mm|in|deg|
   rpm|min|&quot;|")?)`, trying the plain label first and a symbol-suffixed variant second.
6. Keep the raw matched substring alongside the parsed value - Phase 6 shows both so the operator can
   eyeball the literal source text, not just trust a number.

A vendor site that renders specs as an image, a PDF datasheet, or a JS-only widget with no server-
rendered text will fail step 1 outright (nothing to grep) - Phase 4's gate (minimum viable field set)
is what catches that and stops the skill, with an explicit fallback to WebFetch flagged as
lower-confidence rather than silently accepted as equally good.

## The corner-radius-to-diameter ratio as a disambiguation signal

This is the load-bearing geometric check the skill's classification step should lean on when vendor
prose alone is ambiguous or (as with the Hoffmann example above) actively misleading:
- corner radius == 0 (or absent/negligible): a flat/square end mill.
- 0 < corner radius < diameter/2: a bull-nose / corner-radius end mill.
- corner radius == diameter/2 (within rounding): a ball-nose / full-radius end mill.
- a stated included angle or taper with no flute-length-scale radius: a chamfer mill or countersink,
  not an end mill family at all.
Vendor terminology is not always precise about this distinction (Hoffmann's "slot drill" naming a
ball-nose profile is a real example, not a hypothetical) - the dimensional ratio is more reliable
than the vendor's own category label when the two disagree.

## The `product_id` / `vendor` fields: RESOLVED live - they exist, but fail SILENTLY, not loudly

Resolved on the first real run (2026-07-08), against `T2-Reference-Drive01 v1` after switching it
into Manufacture. `tool_productId` and `tool_vendor` ARE real parameters on a non-holder (`flat end
mill`) tool - confirmed by reading the tool's full `.parameters` collection via `sys_execute_script`
(the parameter names are `tool_productId`/`tool_vendor`, not the holder JSON's `product-id`/`vendor`
keys - different naming, same concept). BUT: passing `product_id`/`vendor` on the `cam_edit_tools`
`add_tools` entry did NOT error, and did NOT apply them either - `_do_add` returned
`{"added": 1, ...}` cleanly, yet a subsequent parameter read-back showed both fields as empty
strings (`''`). This is a THIRD failure mode beyond "errors" and "applies cleanly" that the original
design didn't anticipate: a silent no-op that looks identical to success from the return value alone.

**The only way to catch this is reading the live parameter values back after the add** - there is no
error text to branch on, so Step 1's original "if the call errors, fall back to description" logic
never fires for this case; it needs Step 2's read-back to catch it and a subsequent
`action='edit'` to actually land the values:
```
cam_edit_tools(action='edit', scope='document', tool=<index>,
                parameters={"tool_productId": "'<id>'", "tool_vendor": "'<vendor>'"})
```
(Note the quoted-string expression form - `tool.parameters.itemByName(...).expression = "'text'"`,
matching how `tool_description`'s own expression is stored: a Python string literal, not a bare
value.) After this edit, the read-back confirmed both fields populated correctly. So: `product_id`/
`vendor` DO have a structured home on a cutting tool (no need for the `DESCRIPTION_TEMPLATE`
fallback in practice) - the add-path integration for them is just broken/no-op'd in the current
`cam_edit_tools.py`, and `action='edit'` is the reliable path until that's fixed tool-side. This is
an MCP-tool gap worth fixing upstream in `_build_entry` (the `product_id`/`vendor` keys are accepted
in the schema but evidently dropped somewhere in the JSON-build path before `Tool.createFromJson`),
not something to keep working around in every skill that hits it.

## Some dimensions are FORMULA-DERIVED, not free parameters - a genuine, un-fixable gap

Not every mismatch is fixable by an `edit`. Verified live: `tool_shoulderLength`'s expression on a
`flat end mill` tool is the literal string `"tool_fluteLength"` - it is DEFINED as equal to flute
length, not an independent value. Setting `tool_shoulderLength` directly to a different expression
would work syntactically, but breaks the tool's own internal formula (the two fields silently
diverge instead of one another tracking the other, which is presumably intentional tool-modeling
behavior, not a bug). The Hoffmann sample tool had a vendor-stated "shoulder length" of 21mm that
does not equal its flute length (16mm) - these are two genuinely different measurements the vendor
tracks separately that Fusion's tool model does not.

This is a real, permanent gap for THIS tool type's parameter model, not a skill oversight - per the
operator's own call (2026-07-08), the right behavior is to leave the formula alone (accept
`tool_shoulderLength == tool_fluteLength` as Fusion's modeling choice) and report the vendor's
un-appliable value plainly in Phase 6, rather than overwrite a derived formula to force an exact
match. Treat any OTHER formula-derived parameter the same way if encountered: check
`.expression` before assuming a field is independently settable - if it references another parameter
name rather than holding a literal, overwriting it changes the tool's internal relationship, not just
one field, and that decision belongs to the operator, not a silent default.

## Presets are opportunistic, unlike `insert-into-template`'s `PART_PARAMS`

`insert-into-template`'s `PART_PARAMS` (PartX/Y/Z) is a load-bearing optional feature - when present,
it drives stock resizing, so its absence is worth a note to the operator. Feeds/speeds presets here
are different: they're a nice-to-have capture of data the vendor page might already show (a
materials table, a max RPM), never something the skill computes, derives, or needs for the tool to
be usable. So their absence gets no note in the final report - it isn't a gap, just nothing to
capture this time.
