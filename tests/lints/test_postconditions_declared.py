"""Lint: every WRITE/DESTRUCTIVE tool declares postconditions - or carries a reasoned exemption.

The postcondition kernel (tools/_assert.py) is the third kind system: _inputs types what a tool is
GIVEN, _outputs types what it RETURNS, _assert types what it DID (capture -> mutate -> verify).
Verifying the effect INLINE, in the handler, is the audited NORM here - most write tools construct
their payload fields or author their error text from a live read-back, so the verify logic stays
where the values it reads feed straight into the response (the _EXEMPT table below is that audit's
ledger, one line per tool naming which class its inline verification falls into). The kernel
DECLARATION (``postconditions=[...]``) is for a DETACHABLE effect - one a shared _assert.Postcondition
kind can capture/verify without touching handler-local payload assembly. This lint makes the choice a
structural requirement either way: a write tool either passes postconditions=[...] to
Item.create_tool_item, or appears in _EXEMPT with a one-line audited reason. Entries come and go
as tools gain or lose postconditions; the gap: COUNT is what only shrinks (_GAP_CEILING below),
and adding a new write tool here needs the same deliberation as adding a naming-vocabulary verb.
"""

import inspect
import re

from conftest import load_tool, register_all_tools


def _postconditions_of(item):
    """Walk the handler wrapper chain (write-guard -> assert) to the declared postconditions."""
    h = item.handler
    seen = set()
    while h is not None and id(h) not in seen:
        seen.add(id(h))
        posts = getattr(h, "__assert_postconditions__", None)
        if posts is not None:
            return posts
        h = getattr(h, "__wrapped__", None)
    return None


# Write tools WITHOUT a kernel declaration. Format: name -> the audited reason it lacks one.
# Each reason carries its class from a per-handler audit of the actual verify code:
#   inline: - real verification lives in the handler because it constructs payload fields or
#             authors error text (the inseparability rule). Appropriate permanently. Machine-checked:
#             TestInlineExemptionsActuallyReadBack requires the read-back shape AFTER the mutation
#             the reason NAMES - as a call token `foo()` (snaps.add(), deleteMe()) or a property
#             assignment token `attr=` (isGroundToParent=, expression=). A reason that names no
#             mutation cannot confirm.
#   effect: - the payload's own claim IS a live read-back of the mutated state; nothing further
#             to independently verify.
#   gap:    - NO effective read-back exists; the platform can report success while changing
#             nothing and the tool returns ok. Each is a defect awaiting an inline gate or a
#             new kernel kind, not an accepted state. The gap: COUNT is ratcheted shrink-only
#             below (_GAP_CEILING): a new gap: is a deliberate, visible diff to that number,
#             never a quiet exit from the inline read-back detector.
# A few async/system entries keep bespoke reasons. Entries come and go as tools gain or lose
# postconditions; what only shrinks is the gap: count.
_EXEMPT = {
    'appearance_set': 'inline: each appearance= assignment is read back and a mismatch is an error or lands in failed; an OCCURRENCE-level write additionally re-reads EVERY body and compares Appearance.id, never the name (measured: same-named appearances are distinct assets, so a name compare calls a body that kept its own color reached) - reached / bodies_not_reached / unverified_bodies are published, and reaching no body while at least one demonstrably kept another is an error',
    'drawing_add_sketch': 'inline: sketches.add() is followed by a per-collection count diff on the created sketch - lines/rectangles/arcs/circles/ellipses - and any collection short of its requested curve count is an error(...) naming what landed and what did not',
    'drawing_edit_sheet': 'inline: Sheets.add() is followed by a re-read of the sheet count and of the name the created sheet REPORTS, the name= / sheetSize= / orientation= sets are each re-read off the sheet and a silent no-op becomes an error(...) naming what it still reports, copy() facts are read off the returned sheet, and tidyUp is gated on the isModified transition publishing modified_confirmed; deleteMe() alone cannot be verified in-call - a drawing delete is invisible inside its own transaction, so the payload publishes the boolean and marks the count a reading, never a verification',
    'drawing_dimension': 'gap: adsk.drawing has NO dimension entity class, so the dimensions placed cannot be counted, listed or re-read - autoDimension() returning true plus an isModified flip is the whole gate, and it cannot discriminate a document that was already modified',
    'drawing_insert_image': 'gap: the Images collection exposes only createInput and insert - no count, item or delete - so an inserted image cannot be re-read; insert() returning true plus an isModified flip is the whole gate, and it cannot discriminate a document that was already modified',
    'assembly_capture_position': 'inline: snaps.add() is gated and the snapshot count must advance; discard_pending gates revertPendingSnapshot() and re-reads hasPendingSnapshot; delete gates deleteMe() and re-reads the collection for a survivor',
    'design_edit_timeline': 'inline: rollTo/moveToEnd/isSuppressed=/groups.add/deleteMe/deleteAllAfterMarker each gate on a read-back taken after them - markerPosition plus isRolledBack, isSuppressed, and the group and timeline counts; attributes.add() gates on itemByName plus a value match and publishes previous_value, and the attribute deleteMe() gates on itemByName reading None in the same call, with a RAISING read-back refused as UNCONFIRMED rather than published as a claimed delete',
    'mesh_repair': 'inline: meshRepairFeatures.add() is followed by a re-read of the INPUT mesh (a MeshFeature reports no bodies of its own) - triangle and vertex counts, is_closed and volume - and an unverifiable or unrepaired result is an error(...)',
    'mesh_shell': 'inline: meshShellFeatures.add() is followed by a re-read of the INPUT mesh (a MeshFeature reports no bodies of its own) - triangle and vertex counts plus the volume delta - and a shell that moved none of them, or an unreadable one, is an error(...); the thickness that landed is read off the feature ModelParameter and a mismatch is an error',
    'mesh_smooth': 'inline: meshSmoothFeatures.add() is followed by a full nodeCoordinatesAsDouble diff on the INPUT mesh - measured, the triangle/vertex counts and is_closed all hold still across a successful smooth, so the coordinate diff is the only verdict and an unmoved or unreadable one is an error(...); the smoothness that landed is read off the feature ModelParameter and a mismatch is an error',
    'mesh_separate': 'inline: meshSeparateFeatures.add() is followed by a before/after census of the OWNING component mesh body names - the pieces are auto-named and the input is consumed, so the new-name set IS the payload and fewer than two new names is an error(...)',
    'mesh_reverse_normal': 'inline: meshReverseNormalFeatures.add() is followed by a signed-volume sign check and a componentwise negation check of normalVectorsAsDouble on the INPUT mesh - neither moving is an error(...) and neither being readable is reported UNVERIFIED',
    'assembly_constrain': 'inline: the created constraint healthState is read after add() - error, warning and an UNREADABLE state all refuse, naming the delete path - plus a timeline-health delta that refuses an add which left other features unhealthy, and a before/after occurrence-transform diff whose moved rows the payload is assembled from',
    'assembly_edit_relations': 'inline: each acting action gates on a read-back taken after it - the isSuppressed= set is re-read, deleteMe() is followed by a re-list of the kind, the isReversed= set by a re-read, and setMotionData() by the valueOne/valueTwo ratio plus an isReversed= re-read; every flag read-back is sentinelled, so an UNREADABLE flag is refused as UNCONFIRMED, never published as a confirmed False (set_occurrences mutates nothing: the platform refuses the edit, so the action refuses up front)',
    'assembly_edit_contacts': 'inline: each acting action gates on a read-back taken after it - contactSets.add() is followed by a re-list of the design\'s sets and a member re-read, the occurencesAndBodies= and isSuppressed= and name= sets are each re-read, deleteMe() is followed by a re-list, and the isContactAnalysisEnabled= / isContactSetAnalysis= flags are re-read after assignment',
    'assembly_ground': 'inline: the isGroundToParent= assignment is re-read (safe) and a flag that did not take is an error',
    'assembly_move': 'inline: the transform is re-read after the transform2= set; an unchanged pose errors, position is the actual',
    'assembly_rigid_group': 'inline: rg.occurrences count is read back after add() against the requested member set',
    'cam_activate_setup': 'inline: target.isActive is re-read after activate() and gates the claim',
    'cam_apply_template': 'inline: allOperations is recounted around createFromCAMTemplate2(); no growth is an error',
    'cam_create_operation': 'inline: operations recounted around add(); stale post-launch reads are omitted',
    'cam_create_machine': 'effect: the payload re-resolves the created machine through the same _cam_common.resolve_machine query an assignment uses, after importMachine and a machineAtURL load-back; a create that does not resolve back returns isError',
    'cam_create_setup': 'inline: cam.setups is re-listed after add() to confirm the setup landed',
    'cam_delete': 'inline: deleteMe() bool is read at the call site and authors the named decline error',
    'cam_edit_folders': 'inline: addFolder()/moveInto() and the name= rename gate on read-backs; create trusts addFolder returning a live object',
    'cam_edit_operation': 'inline: params re-read for .error after each expression= set (unevaluated -> rollback+error); the observed value is the payload',
    'cam_edit_setup': 'inline: params re-read for .error after each expression= set (unevaluated -> rollback+error); machine/stock/fixture/wcs writes each re-read and gated',
    'cam_edit_tools': 'inline: no path trusts an API return - after add()/remove() the library is persisted and RE-READ from its url, and a count that disagrees is an error; the auto-assigned tool numbers are checked twice (off the in-memory tools, then against the set the re-read stored library holds), and an edit or a preset change re-reads the parameter expression and the preset names off that same re-read, since updateTool/updateToolLibrary returning true is evidence of neither - a document-scope target has no url to re-read, so the payload publishes verified_in_memory_only',
    'cam_generate': 'effect: the launch handle is the effect; cam_get_status confirms completion separately',
    'cam_reorder': 'inline: moveBefore()/moveAfter() bool is read and reported as a named error on false',
    'cam_save_template': 'inline: the template is re-fetched at new_url (templateAtURL) after importTemplate()',
    'cam_select_geometry': 'inline: applyCurveSelections() is followed by a re-read of what the operation now HOLDS - the selection count, the paths and segments Fusion resolved off outputGeometry, the entity set the selection reports, and its own error/warning channel - and a rejected selection or a count of 0 is an error naming what to re-select; the holes path gates on the applied face count the same way (the async generation this can launch is confirmed separately by cam_get_status, never here)',
    'cam_show_toolpath': 'inline: isLightBulbOn is re-read after every isLightBulbOn= set; failed toggles error or are listed',
    'cam_set_nc_comment': 'inline: each program comment/name expression= write is re-read post-set; before/after is the payload',
    'data_create_folder': 'inline: the parent folder is re-listed after add() to confirm the new folder landed',
    'data_create_project': 'inline: dataProjects is re-queried after add() to confirm the new project landed',
    'data_delete_file': 'inline: deleteMe() bool is read at the call site and authors the decline message',
    'data_move_file': 'inline: the move() bool is gated, then the file is re-resolved and its parentFolder read back - the folder identity authors both the did-not-take error and the to_folder payload',
    'data_delete_folder': 'inline: folder.deleteMe() bool is read at the call site and authors the decline message',
    'data_switch_hub': 'inline: activeHub is re-read after the activeHub= assignment; a mismatch authors the getter-only error',
    'data_upload_file': 'inline: a synchronous uploadState=failed after uploadFile() errors; completion is the poller (data_get_upload_status)',
    'design_activate_component': 'inline: activeComponent is re-read and compared to the target after activate()',
    'design_configure': 'inline: every cell write and the activate() are read back; a mismatch is an error',
    'design_delete_feature': "inline: the primary verify is an ABSENCE re-read - the timeline census of objects carrying the name is taken before and after deleteMe(), a count that did not drop is an error naming what the timeline still carries, and a census that could not settle it publishes deleted:null with the unverified note; the timeline-health diff around the same call authors the downstream warning beside it",
    'design_delete_occurrence': "inline: the primary verify is an ABSENCE re-read - the assembly occurrence-path census is taken before and after deleteMe(), a path still present after it is an error, and a census that never carried the path publishes deleted:null with the unverified note; the timeline-health diff around the same call authors the downstream warning beside it",
    'design_recompute': 'inline: timeline health is diffed around computeAll(); new errors are named in new_errors',
    'design_set_mode': 'inline: designType is re-read (current_design_type) after the designType= assignment and gates the converted claim',
    'doc_activate': 'activation is ASYNC - is_active cannot be confirmed synchronously (doc_get confirms)',
    'doc_close': 'inline: close() bool per document feeds the closed/errors lists and the combined message',
    'doc_copy': 'inline: post-copy() rename reads copied.name back and authors the rename_warning field',
    'doc_insert_derive': 'inline: healthState/documentReference.isOutOfDate/isDerived and the user-parameter delta are all read back after add() and gate or annotate the payload',
    'doc_insert_occurrence': 'inline: addByInsert() return is gated, the occurrence is checked isValid, and the reference link is verified isReferencedComponent',
    'doc_new': 'creation is its own evidence - the new doc IS the active document the guard stamps',
    'doc_open': 'open is ASYNC - the note directs to workspace_orient for confirmation',
    'doc_restore_version': 'inline: promote() is gated and a findFileById re-fetch authors the pending/note fields; cloud promote lags',
    'doc_save_as': 'inline: the cloud id is ASYNC; after saveAs() the urn prefix check builds document_id in the handler',
    'doc_update_xref': 'inline: each ref isOutOfDate is re-read after getLatestVersion() or the version= set; a still-stale ref is an error',
    'drawing_create': 'inline: df.id is read back after createDrawing() and a missing file_id authors the specific failure text',
    'joint_create_origin': 'inline: a computed anchor is read back and rolled back via deleteMe() past 0.001cm error',
    'joint_drive': 'inline: rotationValue/slideValue are re-read after each rotationValue=/slideValue= set so value_now reports the clamped actual',
    'joint_edit': 'inline: post-edit computeAll() + a timeline health walk build timeline_errors_after and note',
    'joint_motion_link': 'inline: setMotionData failure rolls the link back via deleteMe() and authors the ratio error',
    'mesh_combine': 'inline: target triangle+body counts are diffed around add(); an unchanged target mesh is an error',
    'mesh_delete': 'inline: the delete is gated at the call - a deleteMe() returning false is an error, an add() that raises is an error - and then a FRESH design-wide mesh walk re-counts the meshes named like the target in its OWNING component; anything but one fewer is an error (measured: the held wrapper keeps reading isValid and its entityToken still resolves the removed body, so only the census can tell)',
    'mesh_generate_face_groups': 'inline: faceGroups.count is read back after add() and zero authors the no-groups failure',
    'mesh_insert': 'inline: the imported mesh_list count is gated after add(); zero authors the empty-import error',
    'mesh_plane_cut': 'inline: a pre-flight bbox-corners-vs-plane test refuses a non-intersecting plane BEFORE any mutation (skipped, never refused, when the geometry is unreadable or the two are in different frames); after add() the mesh body count gates became_split for split_body and the target triangle count is diffed for trim/split_faces, where unchanged and annihilated-to-zero are both errors that attempt a timeline-verified rollback',
    'mesh_reduce': 'inline: triangle counts are diffed around add(); an unreduced mesh is an error',
    'mesh_remesh': 'inline: triangle counts are diffed around add(); an unchanged count is flagged in the payload',
    'mesh_to_brep': 'inline: BRep body tokens are diffed around add() and author the no-body-produced error',
    'model_base_feature': 'inline: startEdit()/finishEdit() bools are checked; a failed start deletes the orphan scope',
    'model_construction': 'effect: the created datum name + its real geometry (normal/direction/position, read back per kind) are live; construction geometry has no healthState',
    'model_create_component': 'inline: rename mismatch is read back as name_warning; activate() bool reported as-is',
    'model_set_material': 'inline: each body material= assignment is read back by name; mismatches land in failed, all-fail errors',
    'model_shell': 'inline: body volume/faces are diffed around add(); an unchanged body is an error',
    'model_stitch': 'inline: each result body isSolid is read back after add(); became_solid reports the observed truth',
    'model_sweep': 'inline: a new-body sweep via add() with no result bodies is an error; is_solid drives the note',
    'param_add': 'inline: timeline health is diffed before/after; a regression rolls back via deleteMe()',
    'param_delete': 'inline: timeline health is diffed around deleteMe() and a regression is a named error',
    'param_set': 'inline: the summary is re-read after the expression= set; unchanged-with-a-new-expression is an error',
    'param_set_favorite': 'effect: the payload favorite field IS the live isFavorite re-read after the set',
    'pmi_create': 'inline: add() returning null is an error and the created annotation is re-read (name/text/markup) into the payload',
    'pmi_delete': 'inline: deleteMe() bool is gated, then the name is re-resolved and isValid re-read; a survivor is an error',
    'pmi_edit': 'inline: each action re-reads its own set (name=, segments=, isLightBulbOn=, annotationTextPoint=) or call (markUpToDate(), convertImportedToFusionPMI()) and gates the claim',
    'save_as_mesh': 'inline: meshBodies count is diffed around addByTriangleMeshData(); a body that did not land is an error',
    'sketch_add_3d_line': 'inline: the created line start/end geometry is read back after addByTwoPoints(); constraint failures surface',
    'sketch_add_geometry': 'inline: each draw call return object (addByCenterRadius() and kin) is checked and a None authors the no-entity error',
    'sketch_constrain': 'inline: isFixed is read back after the isFixed= set for fix/unfix; other kinds gate on the add call return',
    'sketch_create': 'inline: sketches.add() return is gated; the new sketch name/frame is read back into payload',
    'sketch_delete_entity': 'inline: the curve/point/constraint collection count is diffed around deleteMe(); no drop is an error',
    'sketch_dimension': 'inline: the reported value is dim.parameter.expression read back after the addDistanceDimension()-family call sets it',
    'sketch_project': 'inline: addressable entity counts are diffed around each action call - project2() / projectToSurface() / intersectWithSketchPlane(); zero created is an error, and intersect additionally attributes the created curves back to their source entities',
    "sketch_set_text": "inline: sketchTexts count is diffed around add() on create and the created text's definition objectType is matched against the requested mode; expression before/after read on edit, and the font read back off the landed/edited SketchText on both paths",
    "sys_set_preferences": "inline: setattr() assigns the member and the same member is re-read after it - a read-back that differs from the request is an error naming requested, current and previous, and the ok payload publishes previous beside now",
    "design_set_name": 'inline: the name= assignment is followed by a read of the entity name - the payload publishes the name that LANDED (the platform dedupes a taken name to "Name (1)"), and a name that reads back unchanged is an error(...)',
    "design_add_instance": "inline: addExistingComponent() is followed by a re-walk of root.allOccurrences - the assembly paths it added ARE the payload, and an occurrence returned while the tree gained none is an error(...)",
    "design_move_occurrence": "inline: moveToComponent() is followed by a re-walk of root.allOccurrences plus a body bounding-box re-read through the returned proxy - an unchanged tree is an error(...) and a shifted world position is published as a warning",
    'surface_delete_face': 'inline: face counts are diffed around the features add() and author bodies_consumed and the warning',
    'surface_reverse_normal': 'inline: isParamReversed counts are diffed around add() into reversed_confirmed',
    'surface_untrim': 'inline: face area is diffed around add() into extent_grew and the conditional note',
    'sys_execute_script': 'arbitrary user code - there is no declared effect to verify',
    'sys_reload_addin': 'restarts the server itself - nothing left in-process to verify',
    'sys_request_selection': 'effect is a user interaction, not a model mutation',
    'view_screenshot': 'inline: the only mutation is the optional file_path PNG - fh.write() is followed by _export.verify_written on the landed path, and an absent or zero-byte file becomes an error(...) that suppresses the inline image; the kernel cannot see it either way, since this tool returns image/text content blocks rather than an ok() payload',
    'view_set': 'camera/visibility state actions - inline read-backs; not model mutations',
    'view_section': 'section analyses are view state; clear() has inline count read-back',
    'view_switch_workspace': 'workspace state read-back is inline; camera/UI state, not model state',
}


# The measured gap: count. Shrink-only: closing a gap (an inline gate or a kernel kind lands)
# lowers it; raising it means a NEW tool shipped with no effective read-back, which is a
# deliberate decision this number makes visible instead of a free exit from the detector.
_GAP_CEILING = 2


class TestPostconditionsDeclared:
    def test_the_gap_count_only_shrinks(self):
        gaps = sorted(t for t, r in _EXEMPT.items() if r.startswith("gap:"))
        assert len(gaps) <= _GAP_CEILING, (
            f"{len(gaps)} gap: exemptions exceed the ceiling of {_GAP_CEILING}. A gap: entry is a "
            "tool whose success cannot be verified at all - adding one is a deliberate decision: "
            "raise _GAP_CEILING in the same diff with the new tool's named defect, or give the "
            "tool a real read-back.\n  " + "\n  ".join(gaps))
        if len(gaps) < _GAP_CEILING:
            raise AssertionError(
                f"only {len(gaps)} gap: exemptions remain - lower _GAP_CEILING to {len(gaps)} to "
                "lock the win in:\n  " + "\n  ".join(gaps))

    def test_every_write_tool_declares_or_is_exempt(self):
        missing = []
        for it in register_all_tools():
            ann = it.primitive.annotations
            if ann is None or ann.read_only is not False:
                continue                      # read tools mutate nothing to verify
            if _postconditions_of(it):
                continue
            if it.get_name() in _EXEMPT:
                continue
            missing.append(it.get_name())
        assert not missing, (
            "write tools with neither postconditions=[...] nor a reasoned _EXEMPT entry:\n  "
            + "\n  ".join(sorted(missing)))

    def test_exemptions_only_name_real_undeclared_write_tools(self):
        # a stale exemption (tool removed, renamed, or since migrated) must be deleted, not linger.
        items = {it.get_name(): it for it in register_all_tools()}
        stale = []
        for name in _EXEMPT:
            it = items.get(name)
            if it is None:
                stale.append(f"{name} (no such tool)")
                continue
            ann = it.primitive.annotations
            if ann is None or ann.read_only is not False:
                stale.append(f"{name} (not a write tool)")
            elif _postconditions_of(it):
                stale.append(f"{name} (already migrated - drop the exemption)")
        assert not stale, "stale _EXEMPT entries:\n  " + "\n  ".join(stale)

    def test_declared_postconditions_are_postcondition_kinds(self):
        kernel = load_tool("_assert")
        for it in register_all_tools():
            posts = _postconditions_of(it)
            for p in posts or []:
                assert isinstance(p, kernel.Postcondition), (
                    f"{it.get_name()}: postconditions must be _assert.Postcondition kinds, got {type(p)}")


# ── the inline: shape check - an 'inline:' claim is machine-checked, not taken on faith ─────────
#
# An entry classed 'inline:' asserts the handler verifies its effect in its own body. The check
# below is POSITIONAL: the reason must NAME the mutation - a call token `foo()` or a property
# assignment token `attr=` - and the read-back evidence must appear AFTER that mutation in the
# handler's source (or a same-module helper it directly calls - action= dispatchers put the verify
# in _do_*/_slice_* helpers). Read-back evidence is a state re-read idiom - a safe() read, a
# live-object property re-read (.count/.healthState/.isValid/.expression/...), a timeline_health
# diff, a deleteMe() rollback - or a call to a same-module helper whose own source re-reads state
# (a summary/verify reader counts at its call site). The evidence window opens at the START of the
# mutation's line (minus the mutation token itself), so the `did = safe(lambda: x.deleteMe())`
# call-site idiom counts, while evidence that only precedes the mutation never does. An error(...)
# gate must exist somewhere on the surface. Deliberately simple, reviewable text heuristics (the
# no-first-match lint's approach), not AST guessing. A tool the detector cannot confirm is either
# reclassified to 'gap:' (with a named defect) or gets a real read-back - the detector is never
# weakened to pass it.

_ERROR_CALL = re.compile(r"\berror\(")
_READBACK = re.compile(
    r"\bsafe\(|\.count\b|\.healthState\b|timeline_health|\.isValid\b|\.isOutOfDate\b|"
    r"\.isActive\b|\.isGroundToParent\b|\.isLightBulbOn\b|\.isFavorite\b|\.isReferencedComponent\b|"
    r"\.deleteMe\(\)|computeAll|current_design_type|\.appearance\b|\.expression\b|\.value\b|\.name\b|"
    r"verify_written|latestVersionNumber|versionNumber|\.area\b|\.volume\b|entityToken")
_CALLED_NAME = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# the mutation anchors an inline: reason names: `foo()` (a call, matched by its last name part)
# and `attr=` (a glued property-assignment token; the lookahead keeps prose like `key=value` out).
_ANCHOR_CALL_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\(\)")
_ANCHOR_SET_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*)=(?![=A-Za-z0-9_'\"])")


def _original_handler(item):
    """Unwrap the write-guard/assert chain (__wrapped__) to the tool module's own handler."""
    h = item.handler
    seen = set()
    while h is not None and id(h) not in seen:
        seen.add(id(h))
        nxt = getattr(h, "__wrapped__", None)
        if nxt is None:
            return h
        h = nxt
    return h


def _handler_parts(item):
    """The source parts the shape check scans, in order: the handler itself, then every same-module
    function it directly calls (depth 1, sorted by name). Dispatch handlers (action= routers) verify
    inside their _do_*/leaf helpers, so the handler body alone would under-read them; keeping the
    parts SEPARATE keeps the positional check honest (source order across different functions is
    meaningless). Returns None when no source exists."""
    h = _original_handler(item)
    try:
        handler_src = inspect.getsource(h)
    except (OSError, TypeError):
        return None
    module_globals = getattr(h, "__globals__", {})
    parts = [handler_src]
    for called in sorted(set(_CALLED_NAME.findall(handler_src))):
        fn = module_globals.get(called)
        if (callable(fn) and getattr(fn, "__module__", None) == h.__module__
                and called != h.__name__):
            try:
                parts.append(inspect.getsource(fn))
            except (OSError, TypeError):
                pass
    return parts


def _anchor_patterns(reason):
    """Source patterns for every mutation token the reason names: `foo()` matches the call
    `foo(...)`; `attr=` matches the property assignment `.attr = ...`."""
    pats = []
    for name in _ANCHOR_CALL_TOKEN.findall(reason):
        pats.append(re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*\("))
    for name in _ANCHOR_SET_TOKEN.findall(reason):
        pats.append(re.compile(r"\.\s*" + re.escape(name) + r"\s*=(?!=)"))
    return pats


def _readback_evidence(parts):
    """The evidence pattern for these parts: the _READBACK idioms, plus a call to any same-module
    helper in `parts` whose own source re-reads state (its call site is where that read-back runs)."""
    reader_names = []
    for part in parts:
        m = re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", part)
        if m and _READBACK.search(part):
            reader_names.append(re.escape(m.group(1)))
    if not reader_names:
        return _READBACK
    return re.compile(_READBACK.pattern + r"|\b(?:" + "|".join(reader_names) + r")\s*\(")


def _confirms_inline(reason, parts):
    """True when some part contains a mutation the reason names WITH read-back evidence after it
    (window from the mutation's line start, the mutation token itself excluded) and an error(...)
    gate exists on the surface."""
    if parts is None:
        return False
    anchors = _anchor_patterns(reason)
    if not anchors:
        return False                      # the reason names no mutation - nothing to check after
    if not any(_ERROR_CALL.search(p) for p in parts):
        return False
    evidence = _readback_evidence(parts)
    for part in parts:
        for pat in anchors:
            for m in pat.finditer(part):
                line_start = part.rfind("\n", 0, m.start()) + 1
                window = part[line_start:m.start()] + part[m.end():]
                if evidence.search(window):
                    return True
    return False


class TestInlineExemptionsActuallyReadBack:
    def test_every_inline_entry_reads_back_after_its_named_mutation(self):
        items = {it.get_name(): it for it in register_all_tools()}
        unconfirmed = []
        for name, reason in _EXEMPT.items():
            if not reason.startswith("inline:"):
                continue
            item = items.get(name)
            if item is None:
                continue    # test_exemptions_only_name_real_undeclared_write_tools reports it
            if not _confirms_inline(reason, _handler_parts(item)):
                unconfirmed.append(name)
        assert not unconfirmed, (
            "these _EXEMPT entries are classed 'inline:' (the handler verifies its effect in its own "
            "body), but the detector CANNOT confirm the claim: the reason must NAME the mutation "
            "(`foo()` or `attr=`), that mutation must exist in the handler or its directly-called "
            "same-module helpers, and read-back evidence must appear AFTER it (plus an error(...) "
            "gate on the surface). For each: name the real mutation in the reason, make the "
            "read-back real, or reclassify the entry to 'gap:' with a named defect. Do NOT weaken "
            "this detector to pass it:\n  " + "\n  ".join(sorted(unconfirmed)))

    def test_the_shape_check_bites(self):
        # (a) against a REAL registered handler with no read-back: sys_reload_addin restarts the
        # server and reads nothing back - if it were classed 'inline:' the lint must fire, whether
        # the reason names no mutation or names one the source does not carry.
        items = {it.get_name(): it for it in register_all_tools()}
        parts = _handler_parts(items["sys_reload_addin"])
        assert not _confirms_inline("inline: the restart is verified", parts)
        assert not _confirms_inline("inline: restart() is gated and read back", parts)
        # (b) POSITION is load-bearing: the same mutation+evidence confirms when the re-read
        # follows the mutation, and does NOT when every re-read precedes it.
        after = ("def handler():\n"
                 "    feature.deleteMe()\n"
                 "    n = safe(lambda: body.count)\n"
                 "    if n == before:\n"
                 "        return error('nothing was deleted')\n")
        before = ("def handler():\n"
                  "    n = safe(lambda: body.count)\n"
                  "    if n == 0:\n"
                  "        return error('nothing to delete')\n"
                  "    feature.deleteMe()\n"
                  "    return {'deleted': True}\n")
        assert _confirms_inline("inline: deleteMe() is gated by a count re-read", [after])
        assert not _confirms_inline("inline: deleteMe() is gated by a count re-read", [before])
        # ...the call-site idiom (safe() wrapping the mutation on its own line) still confirms...
        call_site = ("def handler():\n"
                     "    did = safe(lambda: node.deleteMe(), False)\n"
                     "    if not did:\n"
                     "        return error('declined')\n")
        assert _confirms_inline("inline: deleteMe() bool is read at the call site", [call_site])
        # ...and an assignment anchor (`attr=`) is positional the same way.
        set_after = ("def handler():\n"
                     "    occ.isGroundToParent = True\n"
                     "    if not safe(lambda: occ.isGroundToParent):\n"
                     "        return error('did not take')\n")
        set_before = ("def handler():\n"
                      "    was = safe(lambda: occ.isGroundToParent)\n"
                      "    if was:\n"
                      "        return error('already grounded')\n"
                      "    occ.isGroundToParent = True\n"
                      "    return {'grounded': True}\n")
        assert _confirms_inline("inline: re-read after the isGroundToParent= set", [set_after])
        assert not _confirms_inline("inline: re-read after the isGroundToParent= set", [set_before])
        # (c) an error() gate is still required, a reason naming no mutation never confirms, and
        # missing source (a builtin) is never confirmed.
        no_gate = "def handler():\n    x.deleteMe()\n    n = safe(lambda: body.count)\n"
        assert not _confirms_inline("inline: deleteMe() then re-read", [no_gate])
        assert not _confirms_inline("inline: the effect is read back and gated", [after])
        assert not _confirms_inline("inline: deleteMe() is gated", None)
        # (d) a same-module reader helper called after the mutation counts at its call site.
        helper_readback = ("def handler():\n"
                           "    p.expression = expr\n"
                           "    after = _summary(p)\n"
                           "    if after == before:\n"
                           "        return error('did not take')\n")
        helper_src = "def _summary(p):\n    return {'expression': safe(lambda: p.expression)}\n"
        assert _confirms_inline("inline: re-read after the expression= set",
                                [helper_readback, helper_src])
        assert not _confirms_inline("inline: re-read after the expression= set",
                                    [helper_readback.replace("after = _summary(p)\n    ", "")])
