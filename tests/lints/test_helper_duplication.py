"""Lint: a shared helper lives in exactly ONE module - its home (CLAUDE.md "Reuse before you write").

Fusion-Essentials keeps its shared conventions in a handful of ``_``-prefixed modules
(``_common``, ``_cam_common``, ``_data_common``, ``_export``, ...). A helper re-implemented locally
inside a tool file instead of imported from its home diverges silently the next time the home module
is fixed or extended - the local copy keeps stale (or wrong) behavior forever. This is a DENYLIST
lint: for each known shared symbol, its definition must appear in its home module and NOWHERE else
under ``tools/``. Growing this list is how a future consolidation locks itself in.

The pattern accepts one optional leading underscore on the symbol: a private re-roll
(``def _target_sketch(...)`` of ``_common.target_sketch``) is the same duplication wearing a
module-private name. A same-named definition that is genuinely a DIFFERENT job (a legal-values
tuple named like a name->enum map) is named in ``_ALLOWLIST`` with a reason; the staleness test
keeps each entry real (still defined there, still different from the home definition).
"""

import os
import re

from conftest import TOOLS_DIR

# symbol -> (home module, is a function def vs a plain module-level assignment)
# Each entry names the ONE file allowed to define it; every other tool module must import it from
# there instead of re-implementing it.
_DENYLIST = {
    "get_cam": ("_cam_common", "def"),
    "find_setup": ("_cam_common", "def"),
    "find_operation": ("_cam_common", "def"),
    "walk_operations": ("_cam_common", "def"),
    "setup_names": ("_cam_common", "def"),
    "op_state_tally": ("_cam_common", "def"),
    "op_state_facts": ("_cam_common", "def"),
    "op_primary_state": ("_cam_common", "def"),
    "validity_basis": ("_cam_common", "def"),
    # The ONE 'max_results' clamp every capped read runs under (CAM and non-CAM alike): one home so
    # the not-a-number fallback and the 1..ceiling hold cannot be right in one read and stale in the
    # next (each read still passes its OWN default/ceiling pair).
    "clamp_rows": ("_cam_common", "def"),
    # The ONE create-flow rename-with-disclosure: sets entity.name, reads it back, returns
    # (final_name, warning-or-None). A per-tool try/except-pass copy is the swallowed-rename defect
    # this helper replaced - the payload must disclose a rename that declined or landed deduped.
    "apply_rename": ("_common", "def"),
    # The ONE CAM tree traversal + refusal resolver (setups/ops/folders/patterns; a duplicated name
    # is refused with each hit's setup path) - every cam_* tool resolves names through these.
    "walk_cam_tree": ("_cam_common", "def"),
    "tree_nodes": ("_cam_common", "def"),
    "resolve_cam_node": ("_cam_common", "def"),
    "operations_under": ("_cam_common", "def"),
    # The ONE 'parameters' request parser ({name: expression} or 'name=value, ...'): the operation
    # and setup parameter editors validate the SAME two wire forms, so a second copy is how one of
    # them starts accepting a form the other refuses.
    "parse_parameters": ("_cam_common", "def"),
    # The ONE CAM library folder-tree walk (tool / post / template libraries all nest folders under
    # a LibraryLocations root) plus its collect-the-asset-urls projection and its raise-tolerant
    # child read. A local re-roll is how one library read loses the depth/folder bound - an
    # unbounded cloud enumeration is a measured way to hang the add-in - while the others keep it.
    "walk_library_folders": ("_cam_common", "def"),
    "library_assets": ("_cam_common", "def"),
    "library_children": ("_cam_common", "def"),
    "_b64url_decode": ("_data_common", "def"),
    # The ONE DataFile epoch-seconds -> ISO-8601-UTC conversion (None for anything that is not a
    # number); the raw integer ships beside it, so a second copy is a second convention.
    "_epoch_iso": ("_data_read", "def"),
    "_urn_candidates": ("_data_common", "def"),
    # The ONE DataFile reference resolver (lineage URN / web URL, or a name scoped to a project,
    # refusing a name that matches several) plus the name-carries-the-true-extension fact every
    # file-scoped data tool gates on - a per-tool copy is how one of them starts guessing.
    "resolve_file_reference": ("_data_common", "def"),
    "name_extension": ("_data_common", "def"),
    "FUSION_NATIVE_EXTENSIONS": ("_data_common", "assign"),
    # The ONE folder-PATH walk from a project root: it hands back the folder, its cleaned path, or
    # the miss facts (segment, deepest folder reached, that folder's subfolder names - None when the
    # enumeration RAISED). A second copy is how one cloud tool starts reporting an UNREAD folder as
    # an empty one while the others say unknown.
    "navigate_folder_path": ("_data_common", "def"),
    # The ONE timeline by-name resolve-and-refuse: exact case-insensitive match, 'name@index' for a
    # repeated name, a miss listing what IS there. design_delete_feature, design_edit_timeline and
    # the FeatureRef kind all answer the same wire form through it; a second copy is how one string
    # names two different features across two tools.
    "resolve_timeline_object": ("_inputs", "def"),
    # The ONE active-document identity read, and the same one the write guard stamps 'acted_on'
    # from: a local re-roll is how a read names one document while the write that follows it
    # names another.
    "_active_identity": ("_write_guard", "def"),
    "sanitize": ("_export", "def"),
    "component_by_name": ("_export", "def"),
    "verify_written": ("_export", "def"),
    # The ONE output-path prep every file writer runs: strip the request, APPEND the format's
    # extension when the name lacks it, create the directory. A local re-roll is how one exporter
    # appends and the next silently writes PNG bytes to a .jpg name.
    "prepare_out_path": ("_export", "def"),
    # The ONE clock-bounded doEvents-pumping wait an async Fusion write is gated on - one home so
    # the bound, the pump and the give-up cannot be right in one export tool and stale in the next.
    "pump_until": ("_export", "def"),
    # The ONE adsk.drawing enum member read BY NAME (None on an absent family/member, never a
    # raise) and the ONE drawing-standard decode - behaviorally identical re-rolls survive every
    # test, so this entry is the only thing that keeps them from coming back.
    "enum_value": ("_drawing_common", "def"),
    "standard_label": ("_drawing_common", "def"),
    "coordinate_unit": ("_drawing_common", "def"),
    # The drawing tables and the DrawingDocument cast: the size<->standard pairing Fusion silently
    # ignores at creation and RAISES on at assignment, the strategy family, the unit a drawing's own
    # numbers are authored in, and the documentReferences-carrying cast. A per-tool copy is how one
    # drawing tool starts offering a size or strategy the other refuses.
    "active_drawing_document": ("_drawing_common", "def"),
    "SHEET_SIZE_MAP": ("_drawing_common", "assign"),
    "DIMENSION_STRATEGIES": ("_drawing_common", "assign"),
    "DOCUMENT_UNIT": ("_drawing_common", "assign"),
    "ptxyz": ("_common", "def"),
    "target_sketch": ("_common", "def"),
    "resolve_or_recent_sketch": ("_common", "def"),
    "timeline_health": ("_common", "def"),
    "result_bodies": ("_common", "def"),
    "body_facts": ("_common", "def"),
    # The ONE abort for a partial-computing createInput transaction (trim, boundary fill): a
    # per-tool copy is how a refused cancel silently stops being reported in one of them.
    "cancel_input": ("_common", "def"),
    # The ONE mode gate for a Features.*.add() that returns nothing, plus the refusal text a site
    # with no feature-independent effect check returns: a per-tool copy is how one tool keeps
    # calling a landed direct-mode edit a failure after the shared rule is fixed.
    "direct_feature_absence": ("_common", "def"),
    "no_feature_error": ("_common", "def"),
    "failed_effect_remedy": ("_common", "def"),
    # The body census a feature-free effect check counts on: one home so the resolve-ONCE rule and
    # the measured "the pieces land in the TARGET's parentComponent" scoping cannot be right in one
    # tool and stale in the next.
    "census_host": ("_common", "def"),
    "body_count": ("_common", "def"),
    # The ONE same-component test. Component wrappers are measured never identity-stable, so this
    # cannot be re-rolled as `a is b` anywhere: one home keeps the token-then-name hedge in step.
    "same_component": ("_common", "def"),
    # The ONE (nativeObject or self).entityToken read - the key any two body references are compared
    # or de-duplicated on. A native and its occurrence proxy carry DIFFERENT tokens of their own, so
    # a local `safe(lambda: b.entityToken)` re-roll is how one de-dup counts a body twice while the
    # same-body guard beside it never fires.
    "native_token": ("_common", "def"),
    # The ONE feature-path resolver (sweep / pipe / path pattern / on-path datum): one home so the
    # 'sketch:<name>' chain rule and the single-handle chaining rule cannot be right in one tool
    # and stale in the next.
    "build_path": ("_common", "def"),
    # The timeline walk and the EXACT-match/'name@index' matcher behind FeatureRef: one home, so a
    # tool cannot re-roll the matcher with a substring fallback.
    "_timeline_objects": ("_inputs", "def"),
    "_match_timeline_objects": ("_inputs", "def"),
    "open_profile_from_sketch": ("_common", "def"),
    "most_recent_body": ("_common", "def"),
    "resolve_entity_ref": ("_common", "def"),
    "resolve_entity_refs": ("_common", "def"),
    # The ONE entity-anchored position grammar - a sketch ref's optional third segment
    # (':start/:end/:mid/:center') and the resolve to that SketchPoint. sketch_dimension and
    # sketch_constrain both read it; a local copy is how one verb accepts an anchor form the other
    # rejects, which is exactly the asymmetry this home removes.
    "parse_anchor_ref": ("_common", "def"),
    "anchor_point": ("_common", "def"),
    "midpoint_sketch_point": ("_common", "def"),
    "SKETCH_ANCHORS": ("_common", "assign"),
    "entity_component": ("_inputs", "def"),
    "axis_line_of": ("_inputs", "def"),
    # The ONE assembly-context walk that lifts a possibly-foreign entity into the single occurrence
    # placing its owner - and REFUSES a component placed several times. A copy is how one consumer
    # starts proxying into an arbitrary instance.
    "single_placement": ("_inputs", "def"),
    "all_occurrences": ("_common", "def"),
    "occurrence_paths": ("_common", "def"),
    "component_contains": ("_common", "def"),
    "null_feature_note": ("_common", "def"),
    "design_wide_counts": ("_common", "def"),
    "CM_TO_UNIT": ("_common", "assign"),
    "OPERATIONS": ("_common", "assign"),
    # The ONE count/item(i) walk over a Fusion collection - a local re-roll drops the guard that
    # makes an ABSENT collection yield nothing instead of raising, and a skipped unreadable item
    # into a None in the caller's list.
    "iter_collection": ("_common", "def"),
    # The ONE 'that body, or the most recent one' resolution every whole-body edit runs (the tool
    # supplies only its own no-body sentence) - a copy is how one of them stops refusing an
    # ambiguous name.
    "resolve_body_or_recent": ("_common", "def"),
    # The ONE boolean-flag read that answers None for unreadable. A re-roll is written as
    # safe(getter, False), which is exactly the confident-False this exists to stop.
    "read_flag": ("_common", "def"),
    # The ONE integer-count read that answers None for unreadable (a body's lumps, a built path's
    # entities). A re-roll is written inline as `int(n) if isinstance(n, int) else None` beside a
    # DIFFERENT one written safe(read, 0), which is how two tools start disagreeing about whether an
    # unreadable count is a zero.
    "counted": ("_common", "def"),
    # The ONE no-volume-change band every material-changing feature judges "the API reported success
    # but nothing moved" against; a site whose signal is not a volume keeps its own named tolerance.
    "NO_VOLUME_CHANGE_CM3": ("_common", "assign"),
    # The literal-or-parameter-expression length trio (extrude distance, offset plane) - one home
    # beside the Distance kind.
    "looks_like_expression": ("_inputs", "def"),
    # World axis key -> origin ConstructionAxis: the name map + the entity accessor, one home.
    "WORLD_AXIS_ATTRS": ("_inputs", "assign"),
    "world_construction_axis": ("_inputs", "def"),
    "length_value_input": ("_inputs", "def"),
    "expression_report": ("_inputs", "def"),
    # The plane-then-face two-pass every '*_to_surface' operand resolves through, and the label its
    # payload publishes: one home beside the SurfaceRef kind that declares them, so the curved-face
    # contract and the resolved-entity read-back can never be right in one sketch tool and stale in
    # the other.
    "resolve_surface": ("_inputs", "def"),
    "surface_ref_label": ("_inputs", "def"),
    "unit_vector": ("_geom", "def"),
    "unit_vector_between": ("_geom", "def"),
    "evaluator_normal_at": ("_geom", "def"),
    "body_aabb": ("_geom", "def"),
    "owning_bodies": ("_geom", "def"),
    "volumes": ("_geom", "def"),
    "volume_delta": ("_geom", "def"),
    # The face-count counterpart of the volume pair - the signal a topology-changing feature
    # (delete-face, split-face) verifies with; one home so both read the same "unreadable is not
    # zero" contract.
    "face_counts": ("_geom", "def"),
    "face_count_delta": ("_geom", "def"),
    # The two not-touching reads a JOIN is verified with: the BRep lump count, and the AABB gap that
    # stands in for it on a body kind carrying no lumps. One home so "unreadable is not zero" and
    # "a positive gap PROVES they cannot touch" cannot be right in one combine tool and stale in the
    # other.
    "lump_count": ("_geom", "def"),
    "aabb_gap": ("_geom", "def"),
    # The thread-table walk that turns a designation into a ThreadInfo, shared by the tapped hole
    # and the thread-an-existing-cylinder tools.
    # The ONE sketch local -> world frame: sketch_create publishes it on the way in and sketch_get on
    # the way out, so a second copy is how the numbers a caller PLACES against stop matching the ones
    # it VERIFIES against.
    "sketch_world_frame": ("_sketch_detail", "def"),
    "resolve_thread_info": ("_threads", "def"),
    "build_joint_geometry": ("_joints", "def"),
    "apply_motion": ("_joints", "def"),
    "find_joint": ("_joints", "def"),
    "all_joints": ("_joints", "def"),
    "current_joint_type": ("_joints", "def"),
    # The ONE JointOrigin walk + its leaf ops - the traversal joint_create_edit and model_inspect share
    # (resolve-one / collect-names / read-axes all sit on all_joint_origins).
    "all_joint_origins": ("_joints", "def"),
    "find_joint_origins_by_name": ("_joints", "def"),
    "jo_assembly_proxy": ("_joints", "def"),
    "jo_reference_names": ("_joints", "def"),
    # The ONE moved-but-uncaptured position read and the refusal every joint CREATE returns while it
    # is set. A local safe(..., False) re-roll is both the confident-False this exists to stop and
    # how one creation tool starts running through a pending move the others refuse.
    "pending_position": ("_joints", "def"),
    "pending_move_guard": ("_joints", "def"),
    "PENDING_MOVE_REFUSAL": ("_joints", "assign"),
    # The orient + refresh-then-grab capture mechanics both screenshot tools share.
    "apply_named_view": ("_view_common", "def"),
    "capture_png_b64": ("_view_common", "def"),
}


# (module without .py, symbol) -> why this same-named definition is a DIFFERENT job, not a re-roll
# of the shared helper. Each reason must describe the local definition's own job. Kept honest by
# test_allowlist_entries_still_exist_and_still_differ: the entry must still match the pattern in
# that module AND its definition must still differ from the home module's - a local copy that
# becomes identical to home is a true duplicate and loses its exemption.
_ALLOWLIST = {
    ("mesh_combine", "OPERATIONS"):
        "operation key -> MeshCombineOperationTypes enum-member map (the mesh enum family), "
        "not _common.OPERATIONS' name -> FeatureOperations map",
    ("model_combine", "OPERATIONS"):
        "legal-values tuple (combine needs an existing target, so no 'new'); the name->enum map "
        "it resolves through IS _common.OPERATIONS",
    ("surface_ops", "OPERATIONS"):
        "legal-values tuple of the keys this tool accepts; the name->enum map it resolves "
        "through IS _common.OPERATIONS",
    ("sketch_constrain", "DIMENSION_STRATEGIES"):
        "autoConstrain's SKETCH dimension-strategy family (edge_aligned / symmetric_chain / ... - "
        "adsk.fusion members), a different enum family from _drawing_common's adsk.drawing "
        "DimensionStrategyTypes map (overall / ordinate / ...); no member is shared",
    ("surface_edit", "result_bodies"):
        "projection over _common.result_bodies -> (names, any_solid); it delegates to the shared "
        "walk rather than re-implementing it",
}


def _pattern(symbol, kind):
    # `_?`: a re-roll hiding behind a leading underscore is the same duplication.
    if kind == "def":
        return re.compile(r"^def _?" + re.escape(symbol) + r"\(", re.M)
    return re.compile(r"^_?" + re.escape(symbol) + r"\s*=", re.M)


def _all_tool_files():
    return [fn for fn in sorted(os.listdir(TOOLS_DIR))
            if fn.endswith(".py") and fn != "__init__.py"]


def _read(fn):
    return open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read()


def _bracket_delta(line):
    return sum(line.count(o) for o in "([{") - sum(line.count(c) for c in ")]}")


def _definition_block(src, match):
    """The full statement starting at `match`: a def's body (lines until the next column-0 line) or
    a possibly-multi-line assignment (lines until brackets balance). Good enough for a lint's
    same-or-different comparison; strings containing brackets can only over-extend the block."""
    lines = src[match.start():].splitlines()
    block = [lines[0]]
    depth = _bracket_delta(lines[0])
    for line in lines[1:]:
        if depth <= 0 and line and not line[0].isspace():
            break
        block.append(line)
        depth += _bracket_delta(line)
    return "\n".join(block).strip()


def _normalized(block, symbol):
    """The block with the symbol's optional leading underscore dropped, so `_OPERATIONS = (...)`
    compares against home's `OPERATIONS = (...)` on content, not on the private prefix."""
    return re.sub(r"\b_" + re.escape(symbol) + r"\b", symbol, block)


class TestHelperDefinedOnlyInItsHomeModule:
    def test_denylisted_symbols_have_exactly_one_definition(self):
        offenders = []
        for symbol, (home, kind) in _DENYLIST.items():
            home_file = home + ".py"
            pattern = _pattern(symbol, kind)
            defined_in = []
            for fn in _all_tool_files():
                if pattern.search(_read(fn)):
                    defined_in.append(fn)
            elsewhere = [fn for fn in defined_in
                         if fn != home_file and (fn[:-3], symbol) not in _ALLOWLIST]
            if elsewhere:
                offenders.append(
                    f"'{symbol}' is defined outside its home module {home_file} in: "
                    f"{', '.join(elsewhere)} - import it from {home} instead of re-implementing it "
                    f"(a leading-underscore variant counts; a genuinely different job gets an "
                    f"_ALLOWLIST entry with a reason)"
                )
            if home_file not in defined_in:
                offenders.append(
                    f"'{symbol}' is not defined in its declared home module {home_file} - "
                    f"the denylist entry is stale, fix it or move the definition back"
                )
        assert not offenders, "helper duplication:\n  " + "\n  ".join(offenders)

    def test_allowlist_entries_still_exist_and_still_differ(self):
        # An entry that no longer matches anything is dead weight hiding a regression check; an
        # entry whose local definition became a verbatim copy of home's is a true duplicate that
        # must collapse, not stay exempted.
        stale = []
        for (mod_name, symbol), reason in _ALLOWLIST.items():
            assert reason.strip(), f"({mod_name}, {symbol}) allowlist entry needs a reason"
            assert symbol in _DENYLIST, f"({mod_name}, {symbol}): '{symbol}' is not denylisted"
            home, kind = _DENYLIST[symbol]
            path = os.path.join(TOOLS_DIR, mod_name + ".py")
            if not os.path.exists(path):
                stale.append(f"({mod_name}, {symbol}): no such module - remove the entry")
                continue
            src = _read(mod_name + ".py")
            m = _pattern(symbol, kind).search(src)
            if not m:
                stale.append(f"({mod_name}, {symbol}): the module no longer defines it - "
                             f"remove the entry")
                continue
            home_src = _read(home + ".py")
            hm = _pattern(symbol, kind).search(home_src)
            if hm and (_normalized(_definition_block(src, m), symbol)
                       == _normalized(_definition_block(home_src, hm), symbol)):
                stale.append(f"({mod_name}, {symbol}): now identical to {home}'s definition - "
                             f"a true duplicate; collapse it onto {home} and remove the entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)
