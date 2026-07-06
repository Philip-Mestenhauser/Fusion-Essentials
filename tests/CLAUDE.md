# Writing a test here

This is the test-authoring recipe for `tests/`. Read the root [CLAUDE.md](../CLAUDE.md) for the
constitution and [tests/README.md](README.md) for the full harness explanation (how `conftest.py`
mocks `adsk`, the fake pattern, the triage for which tools need tests at all); this file is the
short, mandatory-for-new-tests version of "which pattern to copy."

## The canonical pattern (mandatory for new tests)

Every test imports its tool with `conftest.load_tool("<tool>")`, then sets up ALL state with a
`@pytest.fixture` and `monkeypatch.setattr` — never an imperative `mod.app = …` poke at module or
test-body level. A fixture's patches undo themselves after the test runs, so nothing leaks into the
next one; an imperative assignment does not undo itself.

Pick the shape that matches what you're testing:

- **A rich read** (`<domain>_get`, or a router that dispatches to `_slice_*`/measurement-core
  helpers) → copy **`test_design_get.py`** or **`test_model_inspect.py`**. A fixture stubs the
  router's internal slice functions with `monkeypatch.setattr`; tests assert the ROUTER's
  composition (default = the orientation slice only, each `include=` adds exactly its slice, the
  note advertises the rest) — not the underlying Fusion calls, which are covered by live validation,
  not re-mocked here.
- **A tool that needs a fuller fake object model** (bodies, occurrences, components) → copy
  **`test_model_mirror.py`**. It builds a design with conftest's shared `make_design(...)` /
  `MakeComp` / `MakeDesign` fakes and wires it into the tool module with `install(mod, design)`,
  inside a fixture. `install` patches BOTH seams a tool can read `design()` through — its own
  `_common` and, when the tool also imports `_inputs`, `_inputs._common` too (see "the dual-seam
  trap" below) — plus `adsk.fusion.Design.cast` and `adsk.core.ObjectCollection.create`. If
  `MakeComp`/`MakeDesign` lack a surface your tool needs, extend them in `conftest.py` — don't fork a
  bespoke `Fake*` hierarchy into your test file.
- **A pure function** (parse/encode/convert, no Fusion at all) → copy **`test_quoting.py`**. No
  `adsk` surface to fake; call the function and round-trip the result.

Then: read the tool, list its `_helper` functions and the `handler`, and write one test per
specific, plausible bug (not one per function) — the name should read like a spec line
(`test_picks_largest_body_by_volume`); it ends up in `SPEC.md`. Assert on concrete values (`adsk.*`
mocks return a truthy child `Mock` for anything unmodeled, so `assert result is not None` proves
nothing). Cover sizes 0, 1, 2, N for anything taking a collection, and the guards (bad units, no
active design, missing/ambiguous target) alongside the happy path.

## The legacy bespoke pattern exists in most files — do not copy it

Most of the suite predates the shared fakes: a test file defines its own local `_install(...)`
function that pokes module-level seams imperatively (`mod.app = FakeApp()`), plus its own `Fake*`
class hierarchy. Most test files still carry that bespoke shape; a growing minority (the newest
test files) use the shared `make_design(...)`/`install(mod, ...)` pair the way a new test needing a
fuller fake object model should. This is why `conftest.py` carries a large snapshot/restore autouse
fixture — it compensates for state the bespoke pattern leaves behind, which is also why the suite
still passes under `-p randomly` despite the leak surface.

**Do not copy the bespoke pattern for a new test.** When you touch an existing test file for an
unrelated reason, migrating it to `monkeypatch`/the shared fakes is welcome opportunistically — there
is no dedicated migration effort, so don't block unrelated work on it either.

## The dual-seam trap

A tool that resolves geometry/occurrences through a typed kind in `_inputs.py` reads the active
design through TWO import paths: its own `from . import _common` AND `_inputs`'s own `from . import
_common` (imported again inside `_inputs.py`). Patching only `mod._common.design` leaves
`mod._inputs._common.design` pointing at the real (absent, in tests) Fusion app — so an `_inputs`
kind's resolution silently fails even though the handler's own reads work. Patch BOTH seams to the
SAME design object, inside a fixture so it's torn down: `conftest.install(mod, design)` does this for
you; if you patch by hand, patch `mod._common.design` and `mod._inputs._common.design` together.

## Prove a test actually bites

A test that can't fail is decoration. After writing one, sanity-check it by temporarily breaking the
code it covers (flip a comparison, change a constant) and confirming the right test goes red — then
restore the code. Do this especially for a new guard or cap: a `truncated` flag or an ambiguity
refusal is easy to write in a way that always passes.

## Regenerating docs

`py -3 tests/gen_spec.py` rebuilds `tests/SPEC.md` from test names after adding/renaming tests;
`--check` fails if it's stale (also enforced by `test_generated_docs_current.py`).
