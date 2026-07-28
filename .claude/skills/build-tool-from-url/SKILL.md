---
name: build-tool-from-url
description: >-
  Use when the user gives a vendor product-page URL for a cutting tool (Harvey Tool, Hoffmann
  Group, or similar) and asks to add/build/import that tool into the CAM job, or to "build a tool
  from this link". Scrapes the page for cutting geometry (diameter, flute count, corner radius,
  length of cut, coating), classifies it against Fusion's available tool geometry families, and
  adds it to the active document's CAM tool library via cam_edit_tools, with the vendor product
  id/name recorded on the tool. Runs without approval round-trips except when classification or an
  extracted dimension is genuinely ambiguous - then it asks once, with a structured choice.
  Requires the fusion-essentials MCP server.
allowed-tools: >-
  Bash
  WebFetch
  AskUserQuestion
  fusion-essentials:cam_edit_tools
  fusion-essentials:cam_get
  fusion-essentials:view_switch_workspace
---

# Build a CAM tool from a vendor product-page URL

This is a **team-owned, repeatable procedure** for turning a vendor's tool-catalog page into a real
tool in the active CAM job's tool library. It composes fusion-essentials building blocks plus one
raw web fetch. Read it as a sequence to follow exactly, and EDIT the CONFIGURATION block to adapt it
to your shop. `reference.md` (loaded on demand) carries the reasoning behind each contract - this
file states each one once.

It runs **without asking the operator to approve steps**, with one exception: whenever the next step
genuinely has more than one reasonable answer, it stops and asks via a structured `AskUserQuestion`
instead of guessing. That is the skill's only kind of human interaction - never a free-text
confirmation, never a silent pick between two plausible reads.

## CONFIGURATION (edit these for your shop)

```
# How vendor metadata is folded into the tool's description if the structured product-id/link/vendor
# fields cannot be applied on the tool itself. {name}/{product_id}/{vendor} expand.
DESCRIPTION_TEMPLATE = "{name} - {product_id} ({vendor})"

# Tie-breaker ONLY: when Phase 3's classification is close between two from_type candidates and
# neither reading is clearly wrong, prefer this family before asking the operator. Leave blank to
# always ask on a genuine tie. This is NOT a vendor-vocabulary lookup table - classification is the
# agent's judgment call against the live from_type list (Phase 1), not a table lookup.
TIE_BREAK_PREFERENCE = ""   # e.g. "bull nose end mill" - your shop's most-stocked family

# Presets (feeds/speeds) are OPPORTUNISTIC ONLY - captured if the vendor page happens to publish
# them, never hunted for, never gate anything. Nothing to configure here.
```

DEPENDENCY-LIGHT BY DESIGN: the skill needs no pre-seeded vendor-vocabulary table and no fixed list
of supported vendors. It discovers Fusion's actual tool geometry families at run time (Phase 1) and
classifies the vendor page's free text against that real list with ordinary reasoning. A vendor site
that renders its spec sheet as text (both Harvey Tool and Hoffmann Group do) works out of the box; a
page that hides specs behind JS-rendered widgets or images-of-text under-extracts, which Phase 4's
gate catches and reports rather than silently adding a wrong tool.

## Rules (follow exactly)

- **Run the phases in order.** Each phase consumes values recorded by earlier ones; restate the
  recorded values after each phase so the next uses them verbatim.
- **Discover before you classify.** Phase 1 reads Fusion's real `from_type` vocabulary before the
  vendor page is fetched, so Phase 3 classifies against ground truth, not a guess at what Fusion
  supports.
- **Never guess a parameter name.** The reconcile step reads the tool's REAL parameter list
  (`cam_edit_tools(action='parameters')`) and edits only names that read back. If a vendor field has
  no matching parameter, the skill asks or records a gap - it never writes a fabricated name.
- **CONFIRM handshake (DETERMINISTIC - always the same structured control, never free text).** Every
  ambiguity prompt uses `AskUserQuestion` with:
  - header: a short label for what's uncertain (e.g. `"Tool geometry"`, `"Dimension"`)
  - question: state the fork plainly, naming the vendor's own words that caused it
  - options: the plausible candidates, in order, each with a one-line reason; when
    `TIE_BREAK_PREFERENCE` names a family, put it first and mark it "(shop default)"
- **The stop/ask points are exactly these** (each via the handshake above, never ad-hoc chat):
  1. **Phase 2** - the page yields no cutting diameter, no flute count, or no length dimension: STOP
     and report what was and was not found; do not build.
  2. **Phase 2/3** - a bare number's unit cannot be inferred, or a single "length" label could be
     either overall length or length of cut: ASK the `Dimension` question before classifying.
  3. **Phase 3** - two geometry families are both plausible and neither is clearly wrong, and
     `TIE_BREAK_PREFERENCE` does not cover it: ASK the `Tool geometry` question.
  4. **Phase 4** - any gate assertion fails: STOP and report the failing assertion; do not build.
  5. **Phase 5** - the tool's real parameter list has NO match for a vendor spec field (zero-match):
     STOP and ASK which Fusion parameter it maps to, or confirm it stays unapplied - never write a
     guessed name.
  6. **Phase 6** - any field ends in the `unknown` state: ASK for the missing correlation or
     confirmation as a direct question, not a buried note.
- **Pass the GATE (Phase 4) before any write or workspace change.** The CAM product is ensured only
  after the gate passes (Phase 5), so a failed gate leaves the document untouched.

## Phase 1 - Discover the live `from_type` vocabulary (READ)

`cam_edit_tools` clones a sample tool of a requested geometry family (`from_type`) from Fusion's
bundled sample libraries; there is no fixed enum, and the set is whatever `Milling Tools (Metric)`,
`Hole Making Tools (Metric)`, and `Cutting Tools (Metric)` currently contain. Read it directly:

`cam_edit_tools(action='list_types')`

This returns the `types` list with no preconditions - no scope, no library, no CAM product needed,
and no workspace change. Record that list; it IS the ground truth Phase 3 classifies against.

-> Record: `available_from_types` (the returned list).

## Phase 2 - Fetch the RAW page and extract deterministically (READ, Bash - not WebFetch)

**Do not use `WebFetch` for the numbers.** WebFetch converts HTML to Markdown and summarizes it with
a small model before Claude sees it; that step is not configurable and is lossy by design (see
`reference.md`). A dimension that gets machined into part geometry must not pass through an
unverified paraphrase. Fetch the raw page via `Bash` and parse it with a deterministic script:

1. **Fetch raw HTML**, e.g. `curl -s -A "Mozilla/5.0" "<url>"` (a UA header avoids some vendor bot
   blocks). On Windows without a POSIX curl, use PowerShell's `Invoke-WebRequest`.
2. **Mine structured data FIRST, before stripping scripts.** Many vendor pages embed a schema.org
   `Product` block as `<script type="application/ld+json">...</script>` carrying the product name,
   `sku`/`mpn` (product id), `brand` (vendor), and sometimes dimensions as clean key/value JSON -
   more reliable than scraping the rendered table. Parse EVERY `ld+json` block and record any
   name/sku/mpn/brand/dimension fields. Do this before step 3, which deletes these scripts.
3. **Reduce to a label-adjacent text stream** (not a full DOM parse): strip `<script>`/`<style>`
   blocks, replace every remaining tag with a separator marker, collapse whitespace/`&nbsp;`, then
   collapse RUNS of empty separators down to one (vendor spec tables put empty cells between a label
   and its value; skipping past those, not just the next token, is required).
4. **For each checklist field, regex its known label text(s), allow a short trailing symbol after
   the label** (Hoffmann suffixes labels with a symbol letter - "Overall length L", "Cutter diameter
   Dc"; Harvey Tool's labels don't, so try the plain label first, the symbol-suffixed form second),
   **then capture the first following number+unit token.** Checklist: tool type / profile, cutting
   diameter, shank diameter, overall length, length of cut / flute length, number of flutes, corner
   radius, coating, material, taper (if any), product id, vendor name.
5. **Every extracted field carries its raw matched substring as evidence** (not just the parsed
   value) so Phase 6 can show the operator the literal source text.

**Unit inference.** Capture the unit WITH the number and pass it straight into Fusion's expression
parser (`"3 mm"`, `"0.125 in"`) - one less place for a conversion error. When a number appears BARE
(no unit on or beside it), infer the unit in this order, and ASK if none resolves it: (1) an explicit
unit in the field's column header or a units legend on the page; (2) a page-wide unit declaration (a
metric/imperial locale, a "dimensions in mm" note); (3) the unit carried by a sibling dimension in
the same spec block. If two readings stay plausible (a bare `3` that could be 3 mm or 3 in), do NOT
assume - raise the `Dimension` handshake. Never default a bare number to a unit.

VALIDATE the minimum viable set: a cutting diameter, a flute count, and at least one length
dimension (overall length or length of cut). If any of the three is missing, STOP and report exactly
what the page did and didn't yield - do not build from partial data, and do not fall back to
WebFetch's paraphrase to fill the gap.

If the raw fetch itself fails (network error, the vendor blocks non-browser requests, or the markup
matches no known label pattern for a required field), THAT is the moment to fall back to `WebFetch` -
but flag in the final report that the fallback path was used, so its numbers weigh less than a
raw-parsed field.

-> Record: the extracted field:value pairs (each with its raw matched substring), any `ld+json`
fields, the product id, the vendor name, and which fetch path was used (raw-parsed vs. WebFetch
fallback).

## Phase 3 - Classify the geometry family (agent judgment, ambiguity -> ask)

Read the extracted tool-type/profile description and corroborating dimensions, and choose the single
best match from `available_from_types` (Phase 1). The load-bearing geometric signal when vendor prose
is vague or misleading is the corner-radius-to-diameter ratio (`reference.md` states the full rule):
a radius near half the diameter reads ball-nose; a small radius reads bull-nose/corner-radius; zero
reads flat; a stated included angle reads chamfer/countersink. The dimensional ratio outranks the
vendor's own category label when they disagree.

- If exactly one candidate is clearly the best read: proceed with it.
- If two or more are plausible and neither is clearly wrong: if `TIE_BREAK_PREFERENCE` names one of
  them, use it and note that in the report; otherwise raise the `Tool geometry` handshake and proceed
  only with the operator's choice.
- If an extracted dimension is itself ambiguous (a single "length" that could be overall length or
  length of cut, where the two readings change the classification or the diameter/length ratio),
  raise the `Dimension` handshake before classifying.

-> Record: `from_type` (exactly as it appears in `available_from_types`), and whether it was resolved
by clear match, tie-break default, or operator choice.

## Phase 4 - Validate preconditions (the GATE)

Assert ALL, each with its evidence value. If ANY fails, STOP and report it - do not build the tool
and do not change the workspace.
- [ ] The vendor page yielded a cutting diameter, a flute count, and at least one length dimension
      (Phase 2).
- [ ] `from_type` is resolved and is a member of `available_from_types` (Phase 1 + 3) - not a
      free-text guess.

## Phase 5 - Ensure the CAM product, build the tool, then reconcile every field (WRITE)

**Step 0 - ensure the CAM product (the only state change, and only now that the gate has passed).**
The document tool library needs the document's CAM product to exist. Call `cam_get`; if it errors
with "no CAM (Manufacture) product yet", the fix is `view_switch_workspace(workspace='manufacture')` -
entering the Manufacture workspace is what creates the CAM product, and is the ONLY prerequisite.
Do NOT call `cam_create_setup`: a setup is a machining job, a separate structure from the tool
library, and building one is needless work with its own side effects (`reference.md`).

**Step 1 - add.** Pass the extracted diameter as a bare unit-bearing expression string (`"3 mm"`,
`"0.125 in"`) - the expression parser handles the unit:
```
cam_edit_tools(action='add', scope='document', add_tools=[{
    "from_type": <from_type>, "diameter": "<value> <unit>",
    "description": "<vendor tool name/type>",
    "product_id": "<product id>", "vendor": "<vendor name>"
    [, "presets": [...] only if the page published feeds/speeds]}])
```
The add sets and reads back `tool_productId`/`tool_vendor`, erroring if either fails to land - so an
error-free `added: 1` is a verified claim for those two fields. If the add errors naming them, retry
via `action='edit'`; only if that also fails, use the `DESCRIPTION_TEMPLATE` fallback and disclose it
in Phase 6. Include `presets` only if the vendor page actually published spindle-speed/feed values -
never fabricate one.

**Step 2 - reconcile EVERY other extracted dimension against the just-added tool.** The add call
overrides only `diameter` and `description`; every other dimension (overall length, flute length /
length of cut, shoulder length, shank diameter, corner radius, taper) keeps the SAMPLE tool's value,
which routinely disagrees with the vendor page. So, for each non-diameter field Phase 2 extracted:
- **Read the tool's REAL parameter list:** `cam_edit_tools(action='parameters', scope='document',
  tool=<index>)`. It returns every parameter with its `name`, `expression`, and `value`, and flags
  formula-derived parameters (`formula_source`). Match each vendor field to a parameter by that real
  name - never a guessed one. (`reference.md` carries the vendor-field -> parameter-name table, which
  is marked NEEDS-LIVE-VERIFICATION for the names the repo has not yet confirmed.)
- **If a parameter matches and its value differs from the vendor value:** set it with
  `cam_edit_tools(action='edit', scope='document', tool=<index>, parameters={<name>: <expression>})`.
  Do this for EVERY correctable mismatch, not just the first noticed. If the matched parameter is
  formula-derived (`formula_source` set), leave the formula intact per shop policy and record the
  vendor value as `deliberately not applied` (`reference.md`).
- **Zero-match STOP:** if the real parameter list has NO parameter matching a vendor spec field, do
  NOT write a guessed name and do NOT silently drop it - STOP and raise the handshake asking which
  parameter that field maps to (if any), or confirm it stays unapplied.

-> Record, per field: its outcome state (below) and, for applied fields, the before/after values.

## Phase 6 - Verify and report EVERY extracted field (READ)

1. Re-read the tool: `cam_edit_tools(action='list', scope='document')` for the summary plus
   `cam_edit_tools(action='parameters', scope='document', tool=<index>)` for the full parameter set.
   Confirm the tool's ACTUAL current value for every dimension Phase 2 extracted.
2. **Build a field-by-field table: vendor value vs. built-tool value, for every field.** Each field
   lands in exactly ONE of four states, and the report must say which for EACH:
   - **applied** - the tool's parameter now equals the vendor value (diameter, and every field
     reconciled in Step 2).
   - **failed** - a matching parameter was found and an edit attempted, but it errored or the
     read-back did not land; name the parameter and the error.
   - **unknown** - after reading the real parameter list, no parameter matched the vendor field, or
     no correlation was confident; state the vendor value plainly and that it is NOT in the tool.
   - **deliberately not applied** - the field was read but intentionally not mapped, with the reason:
     either it is not a geometry dimension `cam_edit_tools` models (coating, material, twist angle -
     informational), or the matching parameter is formula-derived and shop policy leaves the formula
     intact.
3. **If ANY field is `unknown`, end the report with a direct ask** (per the Rules) - name the
   field(s), state you could not confidently locate or apply them, and ask the operator for the
   missing correlation or to confirm the mismatch is acceptable. Treat an unresolved dimension with
   the same seriousness as a classification ambiguity, not as a footnote.
4. Report in full: the tool's index and description, the resolved `from_type` and how it was
   resolved, the complete four-state table, which metadata path succeeded (structured fields vs.
   description fallback), and whether presets were captured.
