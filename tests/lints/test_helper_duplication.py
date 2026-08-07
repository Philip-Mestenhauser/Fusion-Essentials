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
    # The ONE CAM tree traversal + refusal resolver (setups/ops/folders/patterns; a duplicated name
    # is refused with each hit's setup path) - every cam_* tool resolves names through these.
    "walk_cam_tree": ("_cam_common", "def"),
    "tree_nodes": ("_cam_common", "def"),
    "resolve_cam_node": ("_cam_common", "def"),
    "operations_under": ("_cam_common", "def"),
    "_b64url_decode": ("_data_common", "def"),
    "_urn_candidates": ("_data_common", "def"),
    # The ONE DataFile reference resolver (lineage URN / web URL, or a name scoped to a project,
    # refusing a name that matches several) plus the name-carries-the-true-extension fact every
    # file-scoped data tool gates on - a per-tool copy is how one of them starts guessing.
    "resolve_file_reference": ("_data_common", "def"),
    "name_extension": ("_data_common", "def"),
    "FUSION_NATIVE_EXTENSIONS": ("_data_common", "assign"),
    "sanitize": ("_export", "def"),
    "component_by_name": ("_export", "def"),
    "verify_written": ("_export", "def"),
    # The ONE clock-bounded doEvents-pumping wait an async Fusion write is gated on - one home so
    # the bound, the pump and the give-up cannot be right in one export tool and stale in the next.
    "pump_until": ("_export", "def"),
    # The ONE adsk.drawing enum member read BY NAME (None on an absent family/member, never a
    # raise) and the ONE drawing-standard decode - behaviorally identical re-rolls survive every
    # test, so this entry is the only thing that keeps them from coming back.
    "enum_value": ("_drawing_common", "def"),
    "standard_label": ("_drawing_common", "def"),
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
    # The ONE feature-path resolver (sweep / pipe / path pattern / on-path datum): one home so the
    # 'sketch:<name>' chain rule and the single-handle-auto-chains rule cannot be right in one tool
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
    "entity_component": ("_inputs", "def"),
    "axis_line_of": ("_inputs", "def"),
    "all_occurrences": ("_common", "def"),
    "occurrence_paths": ("_common", "def"),
    "component_contains": ("_common", "def"),
    "null_feature_note": ("_common", "def"),
    "design_wide_counts": ("_common", "def"),
    "CM_TO_UNIT": ("_common", "assign"),
    "OPERATIONS": ("_common", "assign"),
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
    # The thread-table walk that turns a designation into a ThreadInfo, shared by the tapped hole
    # and the thread-an-existing-cylinder tools.
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
