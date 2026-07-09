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
  fusion-essentials:workspace_orient
  fusion-essentials:cam_get
  fusion-essentials:cam_edit_tools
---

# Build a CAM tool from a vendor product-page URL

This is a **team-owned, repeatable procedure** for turning a vendor's tool-catalog page into a real
tool in the active CAM job's tool library. It composes only fusion-essentials building blocks plus
one general-purpose web fetch. Read it as a sequence to follow exactly, and EDIT the CONFIGURATION
block to adapt it to your shop.

It runs **without asking the operator to approve steps**, with one exception: whenever the next step
genuinely has more than one reasonable answer (which geometry family a vendor's prose describes,
which extracted dimension a vague label refers to), stop and ask via a structured `AskUserQuestion`
instead of guessing. That is the skill's only kind of human interaction - never a free-text
confirmation, never picking silently between two plausible reads.

## CONFIGURATION (edit these for your shop)

```
# How vendor metadata is folded into the tool's description if the JSON schema check (Phase 5) finds
# no structured product-id/link/vendor fields for a non-holder tool. {name}/{product_id}/{vendor} expand.
DESCRIPTION_TEMPLATE = "{name} - {product_id} ({vendor})"

# Tie-breaker ONLY: when Phase 3's classification is close between two from_type candidates and
# neither reading is clearly wrong, prefer this family before asking the operator. Leave blank to
# always ask on a genuine tie. This is NOT a vendor-vocabulary lookup table - classification itself
# is the agent's judgment call against the live from_type list (Phase 1), not a table lookup.
TIE_BREAK_PREFERENCE = ""   # e.g. "bull nose end mill" - your shop's most-stocked family

# Presets (feeds/speeds) are OPPORTUNISTIC ONLY - captured if the vendor page happens to publish
# them, never hunted for, never gate anything. No shop knob needed; nothing to configure here.
```

DEPENDENCY-LIGHT BY DESIGN: the skill does not require a pre-seeded vendor-vocabulary table or a
fixed list of supported vendors. It discovers Fusion's ACTUAL available tool geometry families at
run time (Phase 1) and classifies the vendor page's free text against that real list using ordinary
reasoning - the same way an agent reads any other ambiguous spec. A vendor site that renders enough
of its spec sheet as text (both Harvey Tool and Hoffmann Group do) works out of the box; a page that
hides its specs behind JS-rendered widgets or images-of-text will under-extract, which Phase 4's gate
catches and reports rather than silently adding a wrong tool.

## Rules (follow exactly)

- **Run the phases in order.** Each phase consumes values recorded by earlier ones.
- **Discover before you classify.** Phase 1 reads Fusion's real `from_type` vocabulary BEFORE the
  vendor page is even fetched, so classification in Phase 3 has real ground truth, not a guess at
  what Fusion supports.
- **No approval round-trips - except genuine ambiguity.** Do not ask the operator to confirm normal
  progress. DO stop and ask, via the deterministic `AskUserQuestion` shape below, the moment a step
  has more than one reasonable answer: which `from_type` a vendor description maps to, or which
  extracted value a vaguely-labeled dimension refers to. This is a UX default, not a fallback of last
  resort - prefer asking over guessing whenever a real fork exists.
- **CONFIRM handshake (DETERMINISTIC - always the same structured control, never free text).** Every
  ambiguity prompt in this skill uses `AskUserQuestion` with:
  - header: a short label for what's uncertain (e.g. `"Tool geometry"`, `"Dimension"`)
  - question: state the fork plainly, naming the vendor's own words that caused it
  - options: the plausible candidates, in order, each with a one-line reason; when
    `TIE_BREAK_PREFERENCE` names a family, put it first and mark it "(shop default)"
  (Rationale: same reproducibility reasoning as `insert-into-template`'s face-confirm handshake - a
  fixed structured control instead of ad-hoc chat prose.)
- **Pass the GATE (Phase 4) before any write.** If any gate assertion fails, STOP and report the
  failing assertion with its evidence. Do not add a tool built on incomplete data.
- **Carry state forward.** After each phase, restate the recorded values so the next phase uses them
  verbatim.

## Phase 1 - Discover the live `from_type` vocabulary (READ, self-healing CAM-product check)

`cam_edit_tools` clones a sample tool of a requested geometry family (`from_type`) from Fusion's
bundled sample libraries; there is no fixed enum of valid values; the set is whatever `Milling Tools
(Metric)`, `Hole Making Tools (Metric)`, and `Cutting Tools (Metric)` currently contain. Discover it
by deliberately probing with a placeholder that cannot match:

`cam_edit_tools(action='add', scope='document', add_tools=[{"from_type": "__probe__"}])`

This fails without adding anything (`_do_add` validates every entry before adding any - see
reference.md), and its error text lists every available type: `"No sample tool of type '__probe__'.
Available types: <comma-separated list>"`. Parse and record that list - this IS the ground truth
Phase 3 classifies against, not general CAM vocabulary.

**If the probe instead errors with "no CAM (Manufacture) data"** (the document has never had its CAM
product initialized), the fix is `view_switch_workspace(workspace='manufacture')` - entering the
Manufacture workspace is what creates the document's CAM product; this itself is the ONLY prerequisite,
nothing else. Re-run the probe once after switching. **Do NOT call `cam_create_setup` to satisfy
this** - a setup is a machining job (selects bodies, holds toolpaths), a wholly separate structure
from the document's tool library, and building one is unnecessary work with its own side effects to
clean up. (Verified live: a probe against a setup-free, just-switched-to-Manufacture document
resolves and lists the vocabulary correctly - no setup involved.)

-> Record: CAM product confirmed present (switching into Manufacture if it wasn't), and
`available_from_types` (the parsed list).

## Phase 2 - Fetch the RAW page and extract deterministically (READ, Bash - not WebFetch)

**Do not use the `WebFetch` tool for the numbers.** Per its own documented behavior, WebFetch
"converts the response to Markdown when the server returns HTML, and runs the prompt against the
content using a small, fast model" before Claude ever sees it - the conversion step is not
configurable, and it is documented as lossy BY DESIGN for exactly this reason (Anthropic's own
guidance for byte-exact extraction: "use curl via Bash for the unprocessed page"). This is not
theoretical: the first live run of this skill used WebFetch and got a wrong overall-length value
(76mm sample-default vs. 57mm actual) that only surfaced because the operator happened to compare a
screenshot of their own browser against the built tool. A spec number silently paraphrased by a
model standing between the page and the tool is not acceptable for a dimension that gets machined
into part geometry.

Instead, fetch the RAW page via `Bash` (`curl` or, on Windows without a POSIX curl, PowerShell's
`Invoke-WebRequest`) and parse it yourself with a deterministic script - no model paraphrase between
the page's bytes and the extracted number. Verified live against both sample vendors (2026-07-08):

1. **Fetch raw HTML**, e.g.: `curl -s -A "Mozilla/5.0" "<url>"` (a UA header avoids some vendor bot
   blocks - both sample sites served cleanly with one).
2. **Reduce to a label-adjacent text stream**, not a full DOM parse (neither sample site's spec table
   needs one): strip `<script>`/`<style>` blocks, replace every remaining tag with a separator
   marker, collapse whitespace/`&nbsp;`, then collapse RUNS of empty separators down to one (vendor
   spec tables commonly have empty in-between cells - e.g. Hoffmann's markup put 2-3 empty `<td>`s
   between a label and its value; skipping past those, not just the very next token, is required).
3. **For each checklist field, regex-match its known label text(s), allow a short trailing symbol
   after the label** (e.g. Hoffmann suffixes labels with a symbol letter: "Overall length L", "Cutter
   diameter Dc" - Harvey Tool's labels don't do this, so try the plain label first, the
   symbol-suffixed form second), **then capture the first following number+unit token.** Checklist:
   tool type / profile description, cutting diameter, shank diameter, overall length, length of cut /
   flute length / cutting edge length, number of flutes, corner radius, coating, material, taper (if
   any), product id/part number, vendor name.
4. **Every extracted field carries its raw matched substring as evidence** (not just the parsed
   value) - so Phase 6's report can show the operator the literal text the number came from, not just
   a number to trust.

VALIDATE the minimum viable set is present: a cutting diameter, a flute count, and at least one
length dimension (overall length or length of cut). If any of these three is missing, STOP here and
report exactly what the page did and didn't yield - do not proceed to build a tool from partial data,
and do not fall back to WebFetch's paraphrase to fill the gap.

If the raw fetch itself fails (network error, the vendor blocks non-browser requests, or the page's
markup doesn't match any known label pattern for a required field), THAT is the moment to fall back
to `WebFetch` - but flag in the final report that the fallback path was used and its numbers are
therefore less trustworthy than a raw-parsed field, per the caveat above.

-> Record: the extracted field:value pairs (each with its raw matched substring), the product id,
the vendor name, and which fetch path was used (raw-parsed vs. WebFetch fallback, so Phase 6 can
disclose it and weight confidence accordingly).

## Phase 3 - Classify the geometry family (agent judgment, ambiguity -> ask)

Read the extracted tool-type/profile description (and corroborating dimensions - e.g. a corner
radius equal to half the cutting diameter reads as a ball-nose/ball-end profile; a corner radius
much smaller than the diameter reads as a bull-nose/corner-radius end mill; "drill" or "spot drill"
language reads as a drilling geometry) and choose the single best match from `available_from_types`
(Phase 1). This is ordinary reasoning against a known list, not a table lookup - vendor phrasing
varies too much across sites to enumerate in advance.

If exactly one candidate is clearly the best read: proceed with it.

If two or more candidates are plausible and neither is clearly wrong:
- If `TIE_BREAK_PREFERENCE` (CONFIGURATION) names one of the tied candidates, use it and note in the
  final report that the shop default broke the tie.
- Otherwise, raise the CONFIRM handshake (Rules section) offering the plausible candidates. Proceed
  only with the operator's choice.

If an extracted dimension is itself ambiguous (e.g. a page labels a single "length" that could
plausibly be either overall length or length of cut, and the two readings would change the
classification or the diameter/length ratio meaningfully), raise the same handshake shape for the
dimension before classifying.

-> Record: `from_type` (the resolved value, exactly as it appears in `available_from_types`), and
whether it was resolved by clear match, tie-break default, or operator choice.

## Phase 4 - Validate preconditions (the GATE)

Assert ALL, each with its evidence value. If ANY fails, STOP and report it - do not add the tool.
- [ ] The document's CAM product was confirmed present in Phase 1 (already gated there - restate it
      here, don't re-check). No CAM setup is required - only the tool library, which the CAM product
      provides on its own.
- [ ] The vendor page yielded a cutting diameter, a flute count, and at least one length dimension
      (Phase 2).
- [ ] `from_type` is resolved and is a member of `available_from_types` (Phase 1 + 3) - not a
      free-text guess.

## Phase 5 - Build the tool, THEN apply every extracted dimension you can (WRITE)

**The `add_tools` entry itself only overrides `diameter` and `description` (plus `holder`/
`presets`).** Every other extracted dimension - overall length, flute length/length of cut,
shoulder length, shank diameter if it differs from the cutting diameter, corner radius - has NO home
on the add call. Building the tool from `from_type` alone and stopping there silently keeps the
SAMPLE tool's values for all of those fields, which routinely disagree with the vendor page (verified
live: a from_type='flat end mill' sample carried its own ~76mm overall length and 3 flutes,
independent of a vendor spec that said 57mm OAL and 4 flutes - overall length was never touched, and
would have shipped wrong silently if not cross-checked in Phase 6). So this phase is TWO steps, not
one - do not stop after the add:

**Step 1 - add.** Convert the extracted diameter to a bare expression string carrying its own unit
suffix (e.g. `"3 mm"`, `"0.125 in"`) - Fusion's expression parser handles the unit, one less place to
introduce a conversion error. Try the structured-metadata path first, fall back to the description
template:
1. Attempt `cam_edit_tools(action='add', scope='document', add_tools=[{"from_type": <from_type>,
   "diameter": "<value> <unit>", "description": "<vendor tool name/type>", "product_id":
   "<product id>", "vendor": "<vendor name>" [, "presets": [...] if the page published feeds/speeds]}])`.
2. **A successful, error-free `add` response does NOT prove `product_id`/`vendor` actually landed -
   it must be verified by reading them back in Step 2, never trusted from the return value alone.**
   Verified live: the add call returned `{"added": 1, ...}` with no error text, yet the tool's actual
   `tool_productId` and `tool_vendor` parameters were BOTH still empty strings afterward - the keys
   were silently accepted and silently ignored, not applied and not rejected. There is no "the call
   errored, so fall back to description" signal to catch this case; only reading the live parameter
   values back catches it.
3. Presets: only include the `presets` key if the vendor page actually published spindle
   speed/feed values (Phase 2) - never fabricate one, never treat its absence as a problem.

**Step 2 - reconcile EVERY extracted field against the just-added tool, field by field - including
`product_id`/`vendor`, which Step 1 cannot confirm on its own.** For each field Phase 2 extracted
that is NOT diameter (overall length, flute length / length of cut, shoulder length, shank diameter,
corner radius, product id, vendor, ...):
- Read the newly-added tool's actual parameters (`cam_edit_tools(action='list', scope='document')`
  gives diameter/flutes/type/description only - for the REST, read the tool's full parameter set,
  e.g. via `sys_execute_script` reading `tool.parameters` off the document tool library, matching by
  the Fusion parameter name for that dimension). Compare each to the vendor value.
- If `tool_productId`/`tool_vendor` came back empty despite Step 1's attempt, set them explicitly via
  the same `action='edit'` mechanism used for dimensions below - do NOT silently fall back to the
  description-only template just because Step 1 didn't error; only use the description fallback if
  an explicit `edit` attempt to set them ALSO fails to land the value.
- **If a Fusion parameter exists for that dimension and differs from the vendor value**: set it via
  `cam_edit_tools(action='edit', scope='document', tool=<index>, parameters={<param name>:
  <expression>})` (same pattern used for `tool_numberOfFlutes` when flute count was wrong - see
  reference.md). Do this for EVERY correctable mismatch found, not just the first one noticed.
- **If a Fusion parameter for that dimension does NOT exist on this tool type, or you cannot
  correlate the vendor's label to a Fusion parameter with confidence** (e.g. the vendor's "gauge
  length" or "shoulder length" has no obvious counterpart on the tool's parameter list): DO NOT
  guess, DO NOT silently drop it, and DO NOT silently leave the sample's stale value in place as if
  it were correct. Record it as an EXPLICIT GAP for Phase 6's report: the field name, the vendor's
  value, and why it couldn't be applied (no matching parameter / no confident correlation). This is
  the skill's honesty contract - a value that could not be verified or applied must be SAID, not
  swallowed.

-> Record: which metadata path succeeded (structured fields vs. description fallback); every
dimension that was reconciled (field, vendor value, sample-tool value before, value after); and every
dimension that could NOT be applied, with the reason (no matching Fusion parameter vs. no confident
correlation).

## Phase 6 - Verify and report EVERY extracted field, not just diameter (READ)

1. `cam_edit_tools(action='list', scope='document')` plus the fuller parameter read from Phase 5 Step
   2 - confirm the new tool's ACTUAL current values for every dimension Phase 2 extracted, not just
   diameter and flutes.
2. **Build a field-by-field comparison table: vendor value vs. built-tool value, for every field
   extracted in Phase 2.** A field lands in exactly one of three states, and the report must say
   which for EACH one:
   - **Applied and matches** - the tool's value equals the vendor's value (the common case for
     diameter, and for anything reconciled in Phase 5 Step 2).
   - **Could not be applied** - Phase 5 Step 2 found no matching parameter or no confident
     correlation; state the vendor's value plainly and that it is NOT reflected in the built tool.
   - **Not attempted** - a field Phase 2 extracted that isn't a tool dimension at all (coating,
     material, twist angle, etc. - informational, not geometry `cam_edit_tools` models) - say so
     rather than omitting it, so the operator knows it was seen and consciously not applied, not
     missed.
3. **If ANY field is "could not be applied," end the report with a direct ask**, not just a note
   buried in a table: name the field(s), state you could not confidently locate or apply them, and
   ask the operator for the missing correlation (which Fusion parameter, if any, corresponds to
   "shoulder length" on this tool type) or confirm the mismatch is acceptable. This is a genuine
   `AskUserQuestion`-shaped moment per the Rules section - treat an unresolved dimension mismatch
   with the same seriousness as a classification ambiguity, not as a footnote.
4. Report in full: the tool's index and description, the resolved `from_type` and how it was
   resolved, the complete field-by-field table from step 2, and whether presets were captured. The
   operator should be able to see, at a glance, EVERY vendor spec value and whether the built tool
   actually carries it - never just the two or three fields the list action happens to surface.
