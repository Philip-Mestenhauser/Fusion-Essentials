# Release-hardening findings ledger (2026-07-01)

Compiled from an 8-agent full-repo review (every tool file + test file read completely).
This file is the SINGLE SOURCE for the work orders in WORK-ORDERS.md — agents consume a
class section here instead of re-reviewing. Line numbers are as of commit 7378e77 + the
MCP-Server branch working tree; verify each site before editing (code may have drifted).

Severity: [!] = fix before public release. [~] = should fix. [.] = polish.

## Class A — Swallowed mutations reporting success ("the mutation sweep")

The repo's cardinal rule (CLAUDE.md honesty contract): a mutation must either raise into
an error() or be verified after the fact. These sites attempt a change inside
`safe()`/`try/except: pass`, can silently fail, and still report success. Fix = let it
raise, or verify and report honestly (copy `model_create_component`'s rename read-back or
`mesh_export`'s file-exists gate). Update the matching test in the same commit.

1. [!] model_mirror.py:72-75 — `isCombine` set in try/pass; L92 reports `"joined": bool(join)`.
2. [!] model_arrange.py:121-122 — `objectSpacing` via safe(); payload reports spacing applied.
3. [!] show_toolpath.py:108-109 — `_set_bulb` mutation in safe(); every show/hide/isolate reports ok unverified. Also L112-123: `_fit_operation` computes a bbox it never uses and reports `fit: true` regardless; wire description claims toolpath-extents fit (L220-221) — unbacked.
4. [!] cam_edit_tools.py:366-382 — diameter override + preset setters in safe(); dropped changes still report "added".
5. [!] cam_templates.py:450-455 — template rename/description in try/pass; failure saves under wrong name with `saved: true`.
6. [!] design_mode.py:402-407 — `activateRootComponent()`/`deactivate()` safe()-swallowed, then ok reported.
7. [!] doc_update_xref.py:72 — `safe(getLatestVersion, False)` wraps a MUTATION; a raise is then misreported as "returned false".
8. [~] sketch_core.py:260,275 — draw mutations swallowed in safe().
9. [~] surface_create.py:305 — continuity set under safe(), reported as applied unverified.
10. [~] assembly_interference.py:86-89 + 118 — `areCoincidentFacesIncluded` try/pass, echoed as applied.
11. [~] doc_lifecycle.py:573-590 — close_document returns ok even when the only targeted close failed.
12. [~] model_hole.py:310 + 331 — `safe(setToClearanceHole)` while note claims "TAGGED".
13. [~] design_export.py:216-227 — success not gated on the file existing (mesh_export.py:299-314 is the in-repo model to copy).
14. [~] appearance_set.py:168-179 — component-loop failure doesn't surface bodies already colored (partial success rule).
15. [.] mesh_edit.py:131-141 — `generated: true` not gated on face_group_count actually changing.

## Class B — Hand-rolled resolvers where a typed `_inputs` kind exists

First-match / substring name resolution = the wrong-instance risk the kinds exist to
refuse. Fix = adopt the kind (`add_input_property(*kind.as_property())` + resolve via the
kind), matching how design_delete_occurrence / view_section already do it.

1. [!] doc_insert_occurrence.py:79-107 — `into_component`/`remove_existing` first-match (use OccurrenceRef).
2. [!] design_mode.py:347-374 — `_find_occurrence` first-match, no ambiguity refusal (OccurrenceRef).
3. [!] design_configure.py:80-88 — `_resolve_body` first-match across components (BodyRef).
4. [!] view_inspect.py:119-132 — substring occurrence resolver (OccurrenceRef/OccurrenceRefList).
5. [~] model_pattern.py:45-62 — `_resolve_occurrences` hand-rolled (OccurrenceRefList); plain-string schema.
6. [~] model_arrange.py:52-68 — `_find_occurrences` hand-rolled (OccurrenceRefList).
7. [~] sketch_core.py:215-227 — `_target_sketch` bypasses `_common.resolve_sketch`; same in surface_create.py:68-76.
8. [~] model_revolve.py:69-83 — `_resolve_axis` hand-rolled (AxisRef is close; extend the kind if needed).
9. [.] view_screenshot.py:248 — `fit_to` hand-typed string schema (resolver already shared; adopt OccurrenceRef property).
10. [.] find_geometry.py:87-93 — `_resolve_target` silently aggregates all same-named occurrences (judgment: may be intended; document or refuse).

## Class C — Confirmed bugs (behavioral)

1. [!] _inputs.py ~L1095 — `TargetRef.resolve` does `occ, _ = _resolve_occurrence(...)`, discarding the ambiguity error (candidate list) and falling to a generic miss; adjacent "first-match-wins" comment is stale.
2. [!] _write_guard.py — `acted_on` stamps the PRE-handler document identity; wrong for doc-switching writes (doc_new/doc_open/doc_activate). Fix: re-read identity post-handler (or per-tool override).
3. [!] joint_drive vs joint_edit CONTRADICTION — joint_create_edit.py:654-658 refuses `rotationValue` as "unsafe (closes the server connection)" and redirects to assembly_move; joint_drive.py:110 sets `jm.rotationValue` and calls itself the sanctioned path. Reconcile ON A LIVE DOCUMENT (the refusal cites a reproduced crash; joint_drive's tests pass but mocks can't prove connection safety). Whichever survives, fix the other's wire text.
4. [~] cam_edit_operation.py:57 — `_find_operation` prefers `setup.operations`, so folder-nested ops are unreachable (siblings walk recursively; copy them). Note test at 184-194 currently ENCODES the flawed lookup.
5. [~] cam_edit_folders.py:66 — `_find_op` uses allOperations which omits folders: folder-into-folder move can't resolve.
6. [~] view_inspect.py:86-87 — `_SNAPSHOTS` keyed by document NAME; duplicate doc names collide.
7. [~] surface_ops.py:261 — stitch reports default tolerance `0.01` in caller's units; default is 0.01 mm (false for cm/in).
8. [~] joint_at_geometry.py:132-133 — unknown `axis` silently coerces to Z instead of erroring.
9. [~] assembly_joints_advanced.py:186 — `UNIT_TO_CM.get(..., 0.1)` silently treats unknown units as mm (joint_create errors on the same input).
10. [.] sketch_set_text.py:160-161 — `_MAX` cap truncates silently, no `truncated` flag.
11. [.] CLAUDE.md — the parenthetical claiming cam_generate returns write="read" is stale (cam_generate is write="write"; the read label is on cam_get_status).

## Class D — Unbounded reads (stated rule: cap + `truncated`)

1. [!] assembly_probe.py:139-155, 191-207 — occurrences/joints arrays uncapped; ALSO description claims "every occurrence" but only top-level root.occurrences are reported.
2. [~] mesh_ops.py:209-221 — mesh_get emits uncapped `meshes` array.
3. [~] doc_get.py:95-113 — `open_documents` uncapped (big assemblies load hundreds of reference docs).
4. [~] _sketch_detail.py:218-245 — opt-in X-ray emits unbounded entities/constraints/dimensions.
5. [~] cam_compare.py — `differences` list uncapped.
6. [.] _cam_common.py:227, 430 — caps exist but no `truncated` flags.
7. [.] sys_selection.py:272-277 — selection echo loop uncapped (workspace_orient caps at 10; copy).

## Class E — Debris / public embarrassment (mechanical removals)

1. [!] tests/test_assembly_transform.py — file is mojibake-corrupted (UTF-8 read as cp1252: "â€"/"â"€" in docstring + comments). Re-encode/rewrite the comments.
2. [!] sketch_set_text.py:87 — wire note names nonexistent tool "set_sketch_text" (also test_sketch_text_create.py docstring cites "set_sketch_text.py").
3. [!] _sketch_detail.py:321-337 — dead TOOL_DESCRIPTION (with typo), nonsense migration narrative ("the old 'sketch_get' name was merged into 'sketch_get'"), empty register_tool() in a _-helper.
4. [!] _data_common.py docstring + MAP_BLURB (L19) — stale `data_model_ops.py` name; MAP_BLURB LEAKS into the generated CLAUDE.md helper map. Also doc_lifecycle.py:16 same stale name.
5. [!] cam_generate.py:370-376 — shipped review-request comment ("Flag for maintainer if a stricter reading of write= is wanted").
6. [!] Review-artifact labels in shipped code: mesh_export.py:105,168,297 ("Bug A" x3); mesh_edit.py:268 ("Bug B") + L5-17 audit narrative ("the original audit wrongly dismissed... That was WRONG"); design_mode.py:298 ("THE FIX (live-verified)"), :449 ("Option-B wrapper").
7. [~] Dead code: model_mirror.py:37 `_PLANES`; model_construction.py:59-60 `_PLANES`/`_AXIS_VEC`; assembly_transform.py:39 `_AXES`; unused UNIT_TO_CM imports (model_fillet_chamfer.py:29, model_construction.py:40, sketch_set_text.py:34, find_geometry.py:42).
8. [~] Empty/dead section headers: view_workspaces.py:127-130; sys_selection.py:299-302; _data_read.py:244-247; cam_templates.py:512-513.
9. [~] Roadmap promises in wire strings: doc_lifecycle.py:199-203 ("not yet built"); model_compute_holder note ("a dedicated library tool family is coming").
10. [~] False/stale wire claims: sys_capability_map.py:36 CAM summary claims "post" (no post tool); cam_edit_tools.py:595 action description omits create_library; cam_get.py:414-417 include description omits library/templates slices; data_switch_hub.py:148 input description says "switch (default here)" but handler default is "list"; model_combine.py:12-13,112-113 stale "BY NAME" (handles supported); model_mirror description claims origin-planes-only (PlaneRef supports names/handles).
11. [~] sys_reload_addin.py:126-131 — purge NOTE says "keep this module for safety" while the loop purges everything including itself.
12. [.] Stale test docstrings: test_view_screenshot.py:1 cites "get_screenshot.py"; four joint/assembly test docstrings cite pre-rename filenames (joint.py, assembly.py, joint_origin.py, joints_advanced.py).
13. [.] tests/tool-wiring.md is STALE (gen_wiring.py --check fails) — regenerate.
14. [.] show_toolpath.py filename does not match tool cam_show_toolpath (only mismatch in family).
15. [.] Description tweaks: surface_edit _TRIM_DESC leaks implementation ("createInput opens a partial-compute transaction... cancel()"); sys_execute_script description "the general way to perform actions in Fusion" mis-steers agents away from the typed tools; cam_set_nc_comment.py:88-91 before/after narrative comment.

## Class F — Missed Choice kinds (free-string enums, values only in prose)

sketch_core `kind`; sketch_constrain `constraint`; sketch_dimension `dim_type`;
view_screenshot `view`; view_screenshot_multi `views` items; view_inspect
`action`/`orientation`/`style`; sys_selection `what`/`require`; joint_create/edit `axis`;
joint_create_origin `anchor`/`target`/`keypoint`; joint_drive inline units enum (L167,
bypasses _inputs.UNITS); data_switch_hub `action`; model_arrange `solver`.

## Class G — Duplication to consolidate (the missing family helpers)

1. `_get_cam` duplicated in 13/15 CAM tool files (4 variants) while `_cam_common.get_cam` exists and claims to be shared. Consolidate; tests monkeypatch the shared seam.
2. JointGeometry keypoint table x3: joint_create_edit._jg_from_entity, joint_at_geometry._joint_geometry_for, partially joint_create_origin._geometry_from_args → new `_joints.py`.
3. `_apply_motion` x2 (divergent axis semantics) + joint_motion_link's weaker `_find_joint` fork (root-only, misses asBuiltJoints; joint_drive imports the good one) → `_joints.py`.
4. Camera orientation vectors x3 WITH OPPOSITE SIGN CONVENTIONS: view_screenshot._VIEW_VECTORS (look-dir), view_inspect._ORIENTATIONS (view-dir), view_section._aim_at_cut → new `_view_common.py`.
5. URN/base64url decode x2: doc_open + doc_insert_occurrence (L49-73) → _data_common.
6. Export substrate x2: design_export/mesh_export share _sanitize/_component_by_name/split orchestration verbatim → new `_export.py`.
7. `_target_sketch` + `_OPERATIONS` x2 (model_extrude L48-67 / model_revolve) → _common.
8. `_resolve_entity` x2 (sketch_constrain / sketch_dimension) → shared.
9. `_CM_TO_UNIT` + `_ptxyz` x2 (model_inspect / model_measure_between) → _common.
10. _data_read.py:91-119 + 174-186 re-implements _data_common._find_project/_child_folder_by_name; also data_ops' folder-tree read core belongs in _data_read.
11. `_FAMILY_PREFIXES` duplicated gen_manifest.py / gen_wiring.py.
12. NOTE: joint_create_edit.py (891 ln) is the involuntary _joints.py — the extraction is what stops it growing.

## Class H — Test gaps + test-side leaks

1. cam_compare: ZERO tests over pure diff logic (Tier-1 by the repo's own triage).
2. _cam_common untested Tier-1 logic: `_invalidation_reasons` regex, `_op_primary_state`, `_hms`, machining-time math (file records a past 100x unit bug at 649-655); state-1 named "invalid" (443) vs "out_of_date" (270) — one condition, two names.
3. joint_create_origin: handler never invoked by tests (guards/coords/frame_axes untested).
4. cam_edit_folders: moveInto-returns-False untested.
5. sys_reload_addin._purge_addin_modules: now-real pure logic, untested.
6. Leaks: test_design_configure pokes `dc._resolve_*`/`dc._doc_is_saved` (outside conftest's restored seam set — real order-dependence risk); test_cam_edit_tools mutates adsk.cam.LibraryLocations unrestored (352-355); test_cam_generate assigns adsk.cam.*.cast directly (56-57, 140-141); test_sketch_get_merge.py:41 raw sys.modules assignment; test_view_inspect un-restored adsk.core patches (139-148); test_assembly_interference leaves `_common.design = None`.
7. test_show_toolpath TestFit (287-291) enshrines the fake `fit` flag (fix with Class A #3).
8. tests/README.md overstates canonical-pattern adoption (hard counts: 68/97 bespoke _install, 54 imperative seam pokes, 1/97 uses make_design/install, rich_read_caller 0 users; it also misdescribes test_model_inspect). Rewrite when harness policy is decided.

## Class I — Policy tensions (need a decision, then mechanical fixes)

1. write="read" tools that mutate view/doc state: view_inspect save_view (persists/deletes NamedViews), view_section clear (deletes USER-created sections), sys_request_selection (clears selection), cam_get_status (pump advances a mutation). readOnlyHint clients auto-approve these. Decide: relabel, split verbs, or document the Acquire exemption explicitly in CLAUDE.md.
2. In-handler blocking despite the no-sleep rule: cam_select_geometry._generate_and_wait hot-polls up to 25 s (123-135); cam_get_status sleeps 0.1 s/tick (248-255, self-documented).
3. Docstring policy: ~100% of files violate "default NO docstring". DECISION (proposed): allow short factual module docstrings (what + the one load-bearing API gotcha); ban war-story/review-artifact/migration narrative; move API-grounding blocks to docs/fusion-api-notes.md. Then the scrub is mechanical (banned-phrase grep).
4. One-tool-per-file: 9 legacy files carry 2-7 tools (doc_lifecycle 7; mesh_ops 5; param_ops 5; data_ops 4; design_mode 3; assembly_joints_advanced 3; assembly_transform 3; view_workspaces 2; sys_selection 2). DECISION (proposed): grandfather + require one-per-file for NEW tools + split opportunistically when a file is already open for fixes. No split sprint.

## Named exemplars (what "good" looks like — reference these in prompts)

- Rich read + canonical test: design_get.py + test_design_get.py; data_get.py + test_data_get.py; model_inspect.py + test_model_inspect.py.
- Honesty: model_create_component (rename read-back), cam_delete (deleteMe-false), mesh_export (file-exists gate), surface_edit (commit-or-cancel), data_switch_hub (verify-after-write), joint_motion_link (rollback).
- Guards: doc_open (declare-intent crash guard), data_ops delete_folder (double-ack + blast radius).
- Acquire: find_geometry. Orient: workspace_orient.
- Resolver adoption done right: design_delete_occurrence.
- Lint style: test_tool_naming, test_write_status_annotations, test_handle_resolution_uniform.
