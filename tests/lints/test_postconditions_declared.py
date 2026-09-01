"""Lint: every WRITE/DESTRUCTIVE tool is accounted for - a kernel declaration, a verification
classification, or a reasoned exemption. Exactly one of the three, never two.

The postcondition kernel (tools/_assert.py) is the third kind system: _inputs types what a tool is
GIVEN, _outputs types what it RETURNS, _assert types what it DID (capture -> mutate -> verify).
Verifying the effect INLINE, in the handler, is the audited NORM here - most write tools construct
their payload fields or author their error text from a live read-back, so the verify logic stays
where the values it reads feed straight into the response (the _EXEMPT table below is that audit's
ledger, one line per tool naming which class its inline verification falls into). The kernel
DECLARATION (``postconditions=[...]``) is for a DETACHABLE effect - one a shared _assert.Postcondition
kind can capture/verify without touching handler-local payload assembly.

The third route is a ``verification=Verification(...)`` classification at registration
(mcp_primitives/item.py): a closed kind - inline / effect / deferred / external / dynamic / gap -
carrying STRUCTURED references instead of prose. Every one of them is resolved here rather than
believed: an ``evidence_test`` node id must name a test file, class and function that exist and
that no other tool claims; a ``deferred`` poller must be a registered read tool; an ``external``
``evidence_receipt`` must name a receipt row that RECORDS AN OBSERVATION (a skipped or pending row
is refused - those record the absence of one); ``dynamic`` reaches exactly one tool; a ``gap``
carries a defect id that must resolve to an OPEN row of the defect ledger, and counts against the
same ceiling. The obligation each kind's evidence test carries is stated in Verification's own
docstring - what this lint checks is that the named test is REAL, not what it asserts.

The trade a declaring tool makes, stated where it is enforced: it gives up the positional
source-scan below - which re-derives on every run that a read-back FOLLOWS the mutation its reason
names - for a resolved reference to an evidence test that was mutation-proved to bite at the moment
of migration. What this lint checks forever after is that the reference stays real.

Entries come and go as tools gain declarations; the gap COUNT is what only shrinks (_GAP_CEILING
below, counted across both routes), and adding a new write tool here needs the same deliberation
as adding a naming-vocabulary verb.
"""

import ast
import inspect
import os
import re
from types import SimpleNamespace

import pytest

from conftest import is_write_tool, load_tool, register_all_tools

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    'assembly_move': 'inline: the transform is re-read after the transform2= set; an unchanged pose errors, position is the actual',
    'assembly_rigid_group': 'inline: rg.occurrences count is read back after add() against the requested member set',
    'cam_activate_setup': 'inline: target.isActive is re-read after activate() and gates the claim',
    'cam_apply_template': 'inline: allOperations is recounted around createFromCAMTemplate2(); no growth is an error',
    'cam_create_operation': 'inline: operations recounted around add(); stale post-launch reads are omitted',
    'cam_create_machine': 'effect: the payload re-resolves the created machine through the same _cam_common.resolve_machine query an assignment uses, after importMachine and a machineAtURL load-back; a create that does not resolve back returns isError',
    'cam_create_setup': 'inline: cam.setups is re-listed after add() to confirm the setup landed',
    'cam_delete': 'inline: deleteMe() bool is read at the call site and authors the named decline error',
    'cam_delete_machine': 'inline: deleteAsset() is gated on its own bool, then the Local library assets are re-walked AND the name re-resolved through the assignment query - either read still finding the machine is an error(...), never a reported delete',
    'cam_edit_folders': 'inline: addFolder()/moveInto() and the name= rename gate on read-backs; create trusts addFolder returning a live object',
    'cam_edit_operation': 'inline: params re-read for .error after each expression= set (unevaluated -> rollback+error); the observed value is the payload, and the isSuppressed= set is re-read with hasToolpath on both sides - a flag that did not take, or one that stops reading, is an error(...) rather than a confirmed state',
    'cam_edit_setup': 'inline: params re-read for .error after each expression= set (unevaluated -> rollback+error); machine/stock/fixture/wcs writes each re-read and gated',
    'cam_edit_tools': 'inline: no path trusts an API return - after add()/remove() the library is persisted and RE-READ from its url, and a count that disagrees is an error; the auto-assigned tool numbers are checked twice (off the in-memory tools, then against the set the re-read stored library holds), and an edit or a preset change re-reads the parameter expression and the preset names off that same re-read, since updateTool/updateToolLibrary returning true is evidence of neither - a document-scope target has no url to re-read, so the payload publishes verified_in_memory_only',
    'cam_reorder': 'inline: moveBefore()/moveAfter() bool is read and reported as a named error on false',
    'cam_delete_template': 'inline: deleteAsset() is gated on its own bool, then the LOCAL template library assets are re-walked AND the deleted url re-loaded through templateAtURL - either read still finding the template is an error(...), and a walk that could not answer or did not finish is reported UNCONFIRMED rather than as a delete',
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
    'doc_close': 'inline: close() bool per document feeds the closed/errors lists and the combined message',
    'doc_copy': 'inline: post-copy() rename reads copied.name back and authors the rename_warning field',
    'doc_insert_derive': 'inline: healthState/documentReference.isOutOfDate/isDerived and the user-parameter delta are all read back after add() and gate or annotate the payload',
    'doc_insert_occurrence': 'inline: addByInsert() return is gated, the occurrence is checked isValid, and the reference link is verified isReferencedComponent',
    'doc_new': 'creation is its own evidence - the new doc IS the active document the guard stamps',
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
    'model_create_component': 'inline: rename mismatch is read back as name_warning; activate() bool reported as-is',
    'model_set_material': 'inline: each body material= assignment is read back by name; mismatches land in failed, all-fail errors',
    'model_shell': 'inline: body volume/faces are diffed around add(); an unchanged body is an error',
    'model_stitch': 'inline: each result body isSolid is read back after add(); became_solid reports the observed truth',
    'model_sweep': 'inline: a new-body sweep via add() with no result bodies is an error; is_solid drives the note',
    'param_add': 'inline: timeline health is diffed before/after; a regression rolls back via deleteMe()',
    'param_delete': 'inline: timeline health is diffed around deleteMe() and a regression is a named error',
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
    'view_screenshot': 'inline: the only mutation is the optional file_path PNG - fh.write() is followed by _export.verify_written on the landed path, and an absent or zero-byte file becomes an error(...) that suppresses the inline image; the kernel cannot see it either way, since this tool returns image/text content blocks rather than an ok() payload',
    'view_set': 'camera/visibility state actions - inline read-backs; not model mutations',
    'view_section': 'section analyses are view state; clear() has inline count read-back',
    'view_switch_workspace': 'workspace state read-back is inline; camera/UI state, not model state',
}


# The measured gap count, over BOTH routes (a 'gap:' _EXEMPT reason and a kind="gap" declaration).
# Shrink-only: closing a gap (an inline gate or a kernel kind lands) lowers it; raising it means a
# NEW tool shipped with no effective read-back, which is a deliberate decision this number makes
# visible instead of a free exit from the detector. The ceiling is an alarm that UN-RINGS itself:
# the shrink-only half of the test below forces the number back down the moment a gap closes, so
# a tool parked here while its evidence is unrecorded cannot quietly stay parked.
_GAP_CEILING = 3


def _verification_of(item):
    """The registration's verification classification, or None (mcp_primitives/item.py)."""
    return getattr(item, "verification", None)


def _gap_tools(items):
    """Every tool whose accounting says its effect cannot be verified, from either route."""
    gaps = {t for t, r in _EXEMPT.items() if r.startswith("gap:")}
    for it in items:
        v = _verification_of(it)
        if v is not None and v.kind == "gap":
            gaps.add(it.get_name())
    return sorted(gaps)


class TestPostconditionsDeclared:
    def test_the_gap_count_only_shrinks(self):
        gaps = _gap_tools(register_all_tools())
        assert len(gaps) <= _GAP_CEILING, (
            f"{len(gaps)} gap entries exceed the ceiling of {_GAP_CEILING}. A gap is a tool whose "
            "success cannot be verified at all - adding one is a deliberate decision: raise "
            "_GAP_CEILING in the same diff with the new tool's named defect, or give the tool a "
            "real read-back.\n  " + "\n  ".join(gaps))
        if len(gaps) < _GAP_CEILING:
            raise AssertionError(
                f"only {len(gaps)} gap entries remain - lower _GAP_CEILING to {len(gaps)} to "
                "lock the win in:\n  " + "\n  ".join(gaps))

    def test_every_write_tool_declares_or_is_exempt(self):
        missing = []
        for it in register_all_tools():
            if not is_write_tool(it):
                continue                      # read tools mutate nothing to verify
            if _postconditions_of(it):
                continue
            if _verification_of(it) is not None:
                continue
            if it.get_name() in _EXEMPT:
                continue
            missing.append(it.get_name())
        assert not missing, (
            "write tools with none of postconditions=[...], verification=Verification(...) or a "
            "reasoned _EXEMPT entry:\n  " + "\n  ".join(sorted(missing)))

    def test_exemptions_only_name_real_undeclared_write_tools(self):
        items = {it.get_name(): it for it in register_all_tools()}
        stale = [f"{name} ({why})" for name in _EXEMPT
                 if (why := _accounting_conflict(items.get(name), name))]
        assert not stale, "stale _EXEMPT entries:\n  " + "\n  ".join(stale)

    def test_declared_postconditions_are_postcondition_kinds(self):
        kernel = load_tool("_assert")
        for it in register_all_tools():
            posts = _postconditions_of(it)
            for p in posts or []:
                assert isinstance(p, kernel.Postcondition), (
                    f"{it.get_name()}: postconditions must be _assert.Postcondition kinds, got {type(p)}")


# ── the verification classification - every structured reference is RESOLVED, never believed ─────
#
# A declaration names a pytest node id, a poller tool, a live receipt row or a defect id. Each is
# looked up against the thing it points at, so a test deleted or renamed out from under a tool
# fails HERE rather than leaving a claim nobody can spend. A node id is resolved by PARSING its
# file with ast - neither importing the test module nor running pytest's collection, so a
# reference costs one parse and a broken one cannot take the lint down with it.

_DYNAMIC_TOOL = "sys_execute_script"          # the one caller-authored effect (the script hatch)
_NODE_ID = re.compile(r"^tests/[\w/]+\.py(?:::\w+){1,2}$")
_RECEIPT_REF = re.compile(r"^tests/live/[\w.]+\.md#\w+$")
_DEFECT_ID = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
# A receipt bucket that records the ABSENCE of an observation. A reference to one of these names a
# row that exists but proves nothing, which is what a gap is for (the receipt's own header classes
# a skipped row "Not verified - excused").
_EMPTY_BUCKETS = ("skipped", "pending")
# An OPEN row of the defect ledger: an unticked checkbox opening the line, then the id.
_OPEN_ROW = r"^- \[ \] {id}\b"
# The defect ledger's filename under plans/, as ONE literal both the resolver and its bite fixture
# spend. The file is UNTRACKED (the plans tree is gitignored), so it is present on a working
# machine and absent from a clean checkout - which is why the gap-id check skips rather than
# passes when it cannot find it.
_LEDGER_NAME = "fix-backlog.md"


def _resolve_node_id(node_id):
    """'' when the node id resolves to a real test, else why it does not.

    Resolves '<file>.py::test_x' and '<file>.py::TestClass::test_x' by PARSING the file: the class
    is looked up at module level and the test function inside it, so a renamed or deleted test is
    a miss rather than a claim that still reads well."""
    if not _NODE_ID.match(node_id):
        return "not a 'tests/<file>.py::[Class::]test_name' node id"
    parts = node_id.split("::")
    path = os.path.join(REPO_ROOT, *parts[0].split("/"))
    if not os.path.isfile(path):
        return f"no such test file: {parts[0]}"
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    body, where = tree.body, parts[0]
    if len(parts) == 3:
        cls = next((n for n in tree.body
                    if isinstance(n, ast.ClassDef) and n.name == parts[1]), None)
        if cls is None:
            return f"{parts[0]} defines no class {parts[1]}"
        body, where = cls.body, f"{parts[0]}::{parts[1]}"
    fn = parts[-1]
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn
               for n in body):
        return f"{where} defines no test named {fn}"
    return ""


def _resolve_receipt(ref):
    """'' when the reference names a receipt row that records an OBSERVATION, else why it does not.

    The row must be a real TABLE row - the tool named in the first cell, not merely mentioned in
    the file's prose - and its bucket cell must not be one of _EMPTY_BUCKETS: a skipped or pending
    row says the tool was not driven, so pointing a verification at it would cite the absence of
    evidence as evidence."""
    if not _RECEIPT_REF.match(ref):
        return "not a 'tests/live/<receipt>.md#<tool>' reference"
    rel, _, anchor = ref.partition("#")
    path = os.path.join(REPO_ROOT, *rel.split("/"))
    if not os.path.isfile(path):
        return f"no such receipt: {rel}"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    row = re.search(r"^\|\s*" + re.escape(anchor) + r"\s*\|([^|]*)\|", text, re.M)
    if row is None:
        return f"{rel} carries no row for '{anchor}'"
    bucket = row.group(1).strip().lower()
    if bucket.startswith(_EMPTY_BUCKETS):
        return (f"{rel}'s row for '{anchor}' reads '{row.group(1).strip()}' - it records no "
                "observation, so it is not evidence of anything")
    return ""


def _resolve_defect(defect_id):
    """'' when the defect id is well shaped AND opens a row of the defect ledger, else why it does not.

    Returns None - not a verdict - when the ledger file is absent, which the caller turns into a
    visible skip. A well-shaped id that no ledger carries is exactly the shape this exists to
    catch, so answering '' on a missing file would pass the mutant it was written for."""
    if not _DEFECT_ID.match(defect_id or ""):
        return f"{defect_id!r} is not a ledger id like 'DRAW-1'"
    path = os.path.join(REPO_ROOT, "plans", _LEDGER_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not re.search(_OPEN_ROW.format(id=re.escape(defect_id)), text, re.M):
        return f"the defect ledger carries no OPEN row for {defect_id}"
    return ""


def _accounting_conflict(item, name):
    """Why an _EXEMPT row for `name` is stale, or '' when that row is the tool's ONLY account.

    A tool accounted for twice is a tool whose two accounts can disagree, so a declaration of
    either kind retires the row rather than sitting beside it."""
    if item is None:
        return "no such tool"
    if not is_write_tool(item):
        return "not a write tool"
    if _postconditions_of(item):
        return "double-accounted: postconditions=[...] - drop the exemption"
    if _verification_of(item) is not None:
        return "double-accounted: verification=Verification(...) - drop the exemption"
    return ""


def _poller_problem(items, poller_name):
    """Why a deferred declaration's named poller cannot confirm the effect, or ''."""
    poller = items.get(poller_name)
    if poller is None:
        return f"poller '{poller_name}' is not a registered tool"
    if is_write_tool(poller):
        return f"poller '{poller_name}' is a write, not a read that confirms"
    return ""


def _fake_item(name, write=True, posts=None, verification=None):
    """A registered Item's shape as the checks above read it: the name, the write annotation, the
    handler wrapper chain, the declaration. Doctoring one is how the registry-side detectors are
    self-tested - the real registry offers no way to stage a conflict."""
    def handler(**kwargs):
        return None
    if posts is not None:
        handler.__assert_postconditions__ = posts
    return SimpleNamespace(
        get_name=lambda: name, handler=handler, verification=verification,
        primitive=SimpleNamespace(annotations=SimpleNamespace(read_only=not write)))


def _duplicate_evidence_claims(items):
    """node id -> the tools claiming it, for every node id claimed more than once."""
    claimed = {}
    for it in items:
        v = _verification_of(it)
        if v is None or not v.evidence_test:
            continue
        claimed.setdefault(v.evidence_test, []).append(it.get_name())
    return {node: sorted(tools) for node, tools in claimed.items() if len(tools) > 1}


class TestVerificationDeclarations:
    """The declaration side: a closed kind whose every reference resolves."""

    def test_every_declaration_is_a_verification_kind(self):
        items = register_all_tools()        # also bootstraps the mcpServer package path
        from mcpServer.mcp_primitives.item import Verification
        wrong = []
        for it in items:
            v = _verification_of(it)
            if v is None:
                continue
            if not isinstance(v, Verification):
                wrong.append(f"{it.get_name()}: {type(v)}")
            elif v.kind not in Verification.KINDS:
                wrong.append(f"{it.get_name()}: kind {v.kind!r}")
        assert not wrong, ("verification= must be a Verification kind from the closed set "
                           f"{list(Verification.KINDS)}:\n  " + "\n  ".join(wrong))

    def test_every_declared_evidence_test_resolves(self):
        broken = []
        for it in register_all_tools():
            v = _verification_of(it)
            if v is None or not v.evidence_test:
                continue
            why = _resolve_node_id(v.evidence_test)
            if why:
                broken.append(f"{it.get_name()} -> {v.evidence_test}: {why}")
        assert not broken, (
            "these tools name an evidence_test that does not resolve - the test was renamed, moved "
            "or deleted, so the declaration claims a proof nobody can run. Point the declaration at "
            "the test that now carries the obligation, or write one:\n  " + "\n  ".join(broken))

    def test_no_evidence_test_is_claimed_by_two_tools(self):
        shared = _duplicate_evidence_claims(register_all_tools())
        assert not shared, (
            "one test cannot carry two tools' obligations - each needs its own biting proof:\n  "
            + "\n  ".join(f"{node}: {', '.join(t)}" for node, t in sorted(shared.items())))

    def test_a_deferred_declaration_names_a_registered_read_tool_as_its_poller(self):
        items = {it.get_name(): it for it in register_all_tools()}
        bad = []
        for name, it in items.items():
            v = _verification_of(it)
            if v is None or v.kind != "deferred":
                continue
            why = _poller_problem(items, v.poller)
            if why:
                bad.append(f"{name} -> {why}")
        assert not bad, ("a deferred effect is confirmed by a NAMED read tool the payload sends "
                         "the caller to:\n  " + "\n  ".join(sorted(bad)))

    def test_an_external_receipt_names_a_row_of_a_live_receipt(self):
        broken = []
        for it in register_all_tools():
            v = _verification_of(it)
            if v is None or not v.evidence_receipt:
                continue
            why = _resolve_receipt(v.evidence_receipt)
            if why:
                broken.append(f"{it.get_name()} -> {v.evidence_receipt}: {why}")
        assert not broken, ("an evidence_receipt points at the receipt row that carries the tool's "
                            "live evidence:\n  " + "\n  ".join(broken))

    def test_dynamic_reaches_only_the_script_hatch(self):
        others = sorted(it.get_name() for it in register_all_tools()
                        if (_verification_of(it) is not None
                            and _verification_of(it).kind == "dynamic"
                            and it.get_name() != _DYNAMIC_TOOL))
        assert not others, (
            f"kind='dynamic' says the requested effect is CALLER-AUTHORED, which is true of "
            f"{_DYNAMIC_TOOL} alone - every other tool declares its own effect and can be held to "
            "it. A second consumer is a redesign decision, not a classification:\n  "
            + "\n  ".join(others))

    def test_a_gap_declaration_resolves_to_an_open_ledger_row(self):
        bad, unresolvable = [], []
        for it in register_all_tools():
            v = _verification_of(it)
            if v is None or v.kind != "gap":
                continue
            why = _resolve_defect(v.defect_id)
            if why is None:
                unresolvable.append(f"{it.get_name()} -> {v.defect_id}")
            elif why:
                bad.append(f"{it.get_name()} -> {why}")
        assert not bad, ("a gap names the id of a defect the defect ledger still carries OPEN, so an "
                         "unverifiable tool is tracked where it can be closed:\n  "
                         + "\n  ".join(sorted(bad)))
        if unresolvable:
            pytest.skip(
                "the defect ledger is not in this checkout, so these gap ids could not be resolved: "
                + ", ".join(sorted(unresolvable)) + ". The ledger is untracked (the plans tree is "
                "gitignored) and lives on the working machine, so a clean checkout cannot see it - "
                "this check SKIPS visibly there rather than passing on a file it never opened. Run "
                "it where the ledger is present.")

    def test_the_reference_resolvers_bite(self, tmp_path, monkeypatch):
        # Each resolver must FAIL on the shapes it exists to catch, or a renamed test keeps its
        # declaration green. Checked against a real file, not just malformed strings.
        probe = tmp_path / "tests" / "unit"
        probe.mkdir(parents=True)
        (probe / "test_probe.py").write_text(
            "class TestThing:\n    def test_real(self):\n        pass\n\n\ndef test_loose():\n"
            "    pass\n", encoding="utf-8")
        import test_postconditions_declared as mod
        monkeypatch.setattr(mod, "REPO_ROOT", str(tmp_path))
        assert _resolve_node_id("tests/unit/test_probe.py::TestThing::test_real") == ""
        assert _resolve_node_id("tests/unit/test_probe.py::test_loose") == ""
        assert "defines no test" in _resolve_node_id(
            "tests/unit/test_probe.py::TestThing::test_renamed")
        assert "defines no class" in _resolve_node_id(
            "tests/unit/test_probe.py::TestGone::test_real")
        assert "no such test file" in _resolve_node_id("tests/unit/test_absent.py::test_real")
        # a method is not reachable as a module-level test, and a loose one is not in the class
        assert "defines no test" in _resolve_node_id("tests/unit/test_probe.py::test_real")
        assert "defines no test" in _resolve_node_id(
            "tests/unit/test_probe.py::TestThing::test_loose")
        assert "node id" in _resolve_node_id("test_probe.py::test_real")
        assert "node id" in _resolve_node_id("tests/unit/test_probe.py")
        live = tmp_path / "tests" / "live"
        live.mkdir()
        (live / "R.md").write_text(
            "The run drove doc_save and mesh_export end to end.\n"
            "| model_extrude | covered | volume delta read back |\n"
            "| sys_reload_addin | skipped: restarts the server mid-sweep |  |\n"
            "| cam_post | pending |  |\n", encoding="utf-8")
        assert _resolve_receipt("tests/live/R.md#model_extrude") == ""
        # a bucket that records the ABSENCE of an observation is not evidence of one
        assert "records no observation" in _resolve_receipt("tests/live/R.md#sys_reload_addin")
        assert "records no observation" in _resolve_receipt("tests/live/R.md#cam_post")
        # named in the file's PROSE but in no table row - a mention is not a recorded run
        assert "carries no row" in _resolve_receipt("tests/live/R.md#doc_save")
        assert "no such receipt" in _resolve_receipt("tests/live/Absent.md#doc_save")
        assert "reference" in _resolve_receipt("tests/live/R.md")
        # the defect ledger: an OPEN row resolves, a closed one and an absent one do not, and a
        # ledger this checkout does not carry answers None so the caller can SKIP visibly
        assert _resolve_defect("DRAW-1") is None          # no ledger under the patched root yet
        plans = tmp_path / "plans"
        plans.mkdir()
        (plans / _LEDGER_NAME).write_text(
            "- [ ] DRAW-1 (the audited drawing_dimension gap) no dimension entity class.\n"
            "- [x] OLD-9 (closed) the read-back landed.\n"
            "- [ ] DRAW-10 a neighbour whose id merely starts the same way.\n",
            encoding="utf-8")
        assert _resolve_defect("DRAW-1") == ""
        assert _resolve_defect("DRAW-10") == ""
        assert "no OPEN row" in _resolve_defect("OLD-9")        # ticked is closed, not open
        assert "no OPEN row" in _resolve_defect("DRAW-2")       # well shaped, in no ledger row
        assert "not a ledger id" in _resolve_defect("draw-1")   # lowercase is not an id
        assert "not a ledger id" in _resolve_defect("DRAW1")
        assert "not a ledger id" in _resolve_defect(None)

    def test_the_registry_detectors_bite(self):
        # The three checks that read the REGISTRY rather than a file, driven through the real
        # helpers on doctored items - each one live-proved on a real state, and self-covered here
        # so a helper rewritten to always answer clean cannot pass silently.
        from mcpServer.mcp_primitives.item import Verification
        node = "tests/unit/test_probe.py::TestThing::test_real"
        clean = _fake_item("model_extrude")
        assert _accounting_conflict(clean, "model_extrude") == ""
        assert "no such tool" in _accounting_conflict(None, "gone_tool")
        assert "not a write tool" in _accounting_conflict(_fake_item("design_get", write=False),
                                                          "design_get")
        assert "postconditions" in _accounting_conflict(
            _fake_item("doc_save", posts=["a postcondition"]), "doc_save")
        # the double-accounting the pilot exists to prevent: an _EXEMPT row beside a declaration
        declared = _fake_item("param_set",
                              verification=Verification(kind="inline", evidence_test=node))
        assert "double-accounted" in _accounting_conflict(declared, "param_set")

        items = {"doc_get": _fake_item("doc_get", write=False), "doc_save": _fake_item("doc_save")}
        assert _poller_problem(items, "doc_get") == ""
        assert "not a registered tool" in _poller_problem(items, "doc_get_status")
        assert "is a write" in _poller_problem(items, "doc_save")

        one_each = [_fake_item("a", verification=Verification(kind="inline", evidence_test=node)),
                    _fake_item("b", verification=Verification(kind="effect",
                                                              evidence_test=node + "_other")),
                    _fake_item("c", verification=Verification(kind="dynamic"))]
        assert _duplicate_evidence_claims(one_each) == {}
        shared = one_each[:1] + [_fake_item("d", verification=Verification(kind="effect",
                                                                          evidence_test=node))]
        assert _duplicate_evidence_claims(shared) == {node: ["a", "d"]}


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
