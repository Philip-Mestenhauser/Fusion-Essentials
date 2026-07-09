---
name: read-first-review
description: >-
  Use when the user asks to review, audit, or evaluate the reasonableness of the MCP tool code in
  this repo - one file, several named files, a family/directory, or "the changed files" / "my diff".
  Reviews by READING each source file WHOLE (and its paired test) rather than grepping, judged
  through four lenses (maintainability, interpretability, vision-transfer, uniformity-vs-bloat), and
  optionally records only sharp, trend-bearing findings into a ledger so runs compound. It DOGFOODS
  the wire - calls or reads a tool's tools-list entry to feel what a downstream agent gets. NOT for
  finding a specific known bug (use targeted debugging) and NOT a linter - it judges seams,
  contracts, and whether the wire teaches, which lints cannot. Requires the fusion-essentials MCP
  server for the dogfooding step (falls back to source-only when it is unavailable).
allowed-tools: >-
  Read
  Glob
  Bash
  Grep
  Write
  Edit
  fusion-essentials:*
---

# Read-first code review

A repeatable review method for this MCP server's tool surface. Its one non-negotiable rule:
**understand code by READING it whole, not by grepping.** Grep manufactures blind spots and pulls the
reviewer into steering a hundred searches; a full read spends tokens where they matter. A finding
always ORIGINATES in a read; grep is allowed ONLY afterward, to measure how far a smell you already
found spreads. When reviewing a registered TOOL, **dogfood the wire** - call it (or read its
tools-list entry) to experience what a downstream agent gets - before judging whether the wire teaches.

The output must stay SHARP and SMALLER than the code it critiques (lens #4 constrains the review
itself): a per-file verdict, named seams only, resolved findings DELETED not archived. "No findings /
healthy" is a valid and valuable result - it stops a future agent from "fixing" good code.

## CONFIGURATION (edit to adapt)

- **LEDGER_DIR**: where per-run findings and the running trend list live. Default: `plans/code-review/`.
  Set to any working directory. If it does not exist on the first run, create it and seed two files:
  `TRENDS.md` (a header + an empty list of cross-file seams) and `PROGRESS.md` (a header + a "Done"
  and a "Remaining" section). The ledger is OPTIONAL: for a one-off review of a single file, skip it
  and just report; use it when the user wants runs to compound across a family or a campaign.
- **SOURCE_ROOT**: the tool sources. Default: `commands/mcpServer/tools/`. Each source's paired test is
  `tests/unit/test_<name>.py` (some helpers are exercised only through a consuming tool's test).
- **EXEMPTION_TABLE**: the repo's audited list of write tools that verify their effect INLINE rather
  than via a declared `postconditions=`. Default: `tests/lints/test_postconditions_declared.py`'s
  `_EXEMPT` dict (each entry carries a reason prefixed `inline:` / `effect:` / `gap:`). This table is
  the real contract and is invisible to a source-only read - consult it before recording any
  "missing postcondition" finding (see the confirm-before-recording rule below).

## Before you start (when using the ledger): load the accumulated context

Read these in order every run - they carry what prior runs learned, which is what makes runs compound:
1. `LEDGER_DIR/TRENDS.md` - the cross-file seams already found + the discriminators earned. You are
   testing each new file AGAINST these; a file that adds a 3rd instance of a watched seam promotes it
   toward a lint proposal.
2. `LEDGER_DIR/PROGRESS.md` - what's reviewed, what's next, what verdicts landed.
3. The relevant `LEDGER_DIR/findings/<file>.md` if re-reviewing a file.

## Resolve the target set

- One file / named files: review exactly those.
- A family or directory ("the cam tools", "surfaces"): `Glob` the sources under SOURCE_ROOT, review each.
- "Changed files" / "my diff": `git diff --name-only` (unstaged) unioned with `git diff --name-only
  --staged`; keep files under SOURCE_ROOT and their paired tests. This is the mode for reviewing a
  work-in-flight change before commit.
- Order worst-suspected first: large files, `_*common` helpers (junk-drawer risk), and any file that
  touches a trend the ledger already watches.

## The method, per file (pair source with its test)

1. **Read the SOURCE whole.** Top to bottom, no skimming, no grep-to-orient. For each function/class
   ask: what ONE job? Does its length match its DOMAIN (healthy) or come from a CONTRACT fragmented
   across files (rot)? Does any contract it relies on live elsewhere, where it could drift?
2. **Find + read the TEST whole.** Ask the load-bearing question: **does the test prove the contract
   BITES, and does the CONTRACT itself demand enough to be safe?** A green test only proves "no code
   violates its contract" - never that the contract is complete. Locate the un-attempted case (that
   is where the next live-eval finding will land - see the verify-the-effect spectrum below).
3. **CONFIRM BEFORE RECORDING** (the single most important discipline - it catches false positives).
   Some contracts are invisible to a source-only read. The classic trap: "this write tool has no
   `postconditions=`" - but a write tool that verifies INLINE is legitimately exempt via the
   EXEMPTION_TABLE. Check that table (and any similar audited mechanism) before recording a
   missing-verification finding. More generally: before you write down a defect, confirm it exists -
   reproduce it live (`sys_execute_script` on a scratch doc, or drive the tool and read the result
   back) rather than trusting a static read of `adsk.*`. A plausible-looking bug is often correct code
   whose API contract you misread.
4. **If it's a registered tool: DOGFOOD THE WIRE.** Read its `tools/list` entry or call it live. Judge
   whether description + notes + errors teach a cold agent AT THE LAYER THAT LOADS WHEN NEEDED (session
   facts -> server instructions; failure-time teaching -> a shared error, stated once; a tool's purpose
   + next step -> its lean description). If the MCP server is unavailable, judge from source and MARK
   the finding source-only-unverified.
5. **Write the verdict** - to `LEDGER_DIR/findings/<file>.md` ONLY if non-healthy or trend-bearing; a
   healthy file gets a one-line note in PROGRESS.md ("HEALTHY, length earned"). For a one-off review
   without a ledger, just state the verdict in your reply.
6. **Roll recurring seams into TRENDS.md.** A seam in 3+ files is a trend and earns a lint proposal
   (a discrete script catching the shape, per lens #1).

## The LENS - four questions to hold while reading (record findings by what they ARE)

1. **Maintainability** - does the code FORCE agents into its abstractions, or can a lazy tool hand-roll
   around them? Is there a discrete SCRIPT (lint) catching the known-bad shape, and would it catch a
   NEW variant, not just the known one?
2. **Interpretability** - would a fresh agent, reading cold, build the RIGHT mental model? Or does a
   contract live in one file while its owner documents a different one? Does a TEST's name/docstring
   teach an intent the code has repudiated?
3. **Vision-transfer** - does the code teach the next agent it can USE the MCP tools to feel what the
   server gives an agent? (This is why the reviewer dogfoods - to not be the agent who misses the loop.)
4. **Uniformity vs ledger-bloat** - is the pattern applied consistently? AND is the review output
   staying smaller than the code it critiques?

## Discriminators (apply these every file - the reusable judgment)

- **Density that mirrors DOMAIN complexity is HEALTHY - do not consolidate it.** (Bodies are ambiguous;
  planes have several source-shapes: long resolvers are fidelity, not bloat.) Density from a CONTRACT
  FRAGMENTED across files is ROT - fix by relocating the contract to its OWNER (which sometimes ADDS a
  line).
- **Consolidate a shared INVARIANT (good); never dedupe shared SYNTAX (bad).** Test: is there a real
  rule underneath (a fullPathName is the unique key), or just matching characters? Deduping mere syntax
  relocates complexity into the framework where the next author goes blind to it.
- **A `_*common` file should hold ONLY what >1 tool consumes.** The junk-drawer tell is not the name -
  it is SIZE + A SINGLE DOMINANT CONSUMER (one tool's private body parked in a "shared" file).
- **Verify-the-effect is a SPECTRUM** (the sharpest reusable insight this method produces):
  rung 1 = reports a COUNT (a wrong result with the right count passes);
  rung 2 = reports the effect EXISTS (a body was made - but maybe the wrong one);
  rung 3 = reads the RIGHT COUNT/VALUE back off the feature and errors on mismatch;
  rung 4 = verifies the RIGHT GEOMETRY (volume/bbox/file-on-disk) - reachable ONLY by an independent
  read (a live eval), not a unit test.
  A unit suite structurally tops out at ~rung 2-3. So **GREEN != SAFE**: it means "no gaps in what the
  contracts ATTEMPT," and a live-eval is the only discovery mechanism for un-attempted cases. When you
  find a material-mutating tool sitting at rung 1 (a bare count), that is a real finding: name the rung
  it should reach (a pattern's count suffices; a material-removal feature needs a volume read-back).

## What "done" produces (and must NOT)

- Produces: per-file verdicts + named seams; (with a ledger) updated TRENDS/PROGRESS; and - if a trend
  hit 3 files - a concrete lint proposal (a discrete script catching the shape).
- Must NOT: restate the code, mark-and-KEEP resolved findings (delete them), grow the ledger past the
  code's mass, or invent make-work (e.g. a test to dedupe two fakes when coverage is already real).

## Final message
Terse: files reviewed, verdicts, any promoted trend, the single most actionable finding. If a ledger
was used, point the user at LEDGER_DIR for the durable detail. Offer the next target set.
