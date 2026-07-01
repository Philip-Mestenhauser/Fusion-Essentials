# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: the COLD-BOOT orientation call - one cheap read that situates the agent.

  workspace_orient -> a single structured situational report of the OPEN document: what it is (doc +
                      units + mode), what it CONTAINS (component/body/sketch/joint counts + a depth-1
                      browser digest), its HEALTH (timeline errors, broken joints, grounding), whether
                      CAM data exists (without switching to Manufacture), and - the key part -
                      budget-aware POINTERS telling the agent which targeted tool to use to refine each
                      area, especially when the design is too large to dump wholesale. Read-only.

WHY THIS EXISTS (the progressive-disclosure posture): an agent arrives at an open document BLIND. The
old habit was to fish - design_get(include=['tree']), then assembly_probe, then cam_get, then
design_get, then screenshots - each a round trip, each a guess about what's even
relevant, and on a large assembly that cost is unbounded. This call inverts that: ONE cheap broad read
hands over the key variables + a map of what's here + pointers to the narrow tools, so every SUBSEQUENT
call is a targeted, lighter-weight refinement instead of an exploratory probe. Orient cheaply first;
drill on demand. It composes signals the deeper tools expose (CAM detection a la cam_get, timeline
health a la design_ops, joint/ground rollup a la assembly_probe) into one digest - it does not replace
them; it points at them.

Grounded in adsk.* (all reads, defensive):
  - app.activeDocument / .activeProduct.productType ; userInterface.activeWorkspace
  - Design.cast(activeProduct) ; design.designType ; unitsManager.defaultLengthUnits
  - rootComponent.occurrences (top-level digest) / .allOccurrences.count / .bRepBodies / .sketches /
    .joints (count + healthState + occurrence wiring) / .isGrounded
  - design.timeline.item(i).healthState (0 healthy / 1 warning / 2 error / 3 suppressed)
  - document.documentReferences.item(i).isOutOfDate -> stale external-component status (any doc)
  - document.products.itemByProductType('CAMProductType') -> CAM exists? + setups/op counts
Read-only; runs on the main thread.
"""

import adsk.core
import adsk.fusion
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common

app = adsk.core.Application.get()

# Budget thresholds: above these, a whole-design dump (design_get(include=['tree']) with no target, find_geometry
# over the whole design) is expensive/large, so the pointers steer to a TARGETED call instead. Tuned so
# small designs (the common case) get the full picture and only genuinely-large ones get steered.
_BIG_OCCURRENCES = 40        # above this, design_get(include=['tree']) is heavy -> suggest a target
_BIG_BODIES = 60             # above this, whole-design find_geometry is heavy -> suggest target=...
_DIGEST_LIMIT = 25           # top-level occurrences listed in the browser digest (not the full tree)


def _design_mode(design):
    """'parametric' | 'direct' | None - read from designType (ParametricDesignType == 1)."""
    dt = safe(lambda: design.designType)
    if dt is None:
        return None
    return "parametric" if int(dt) == 1 else "direct"


def _data_identity(doc):
    """WHERE the active document lives in the data model: its lineage URN + version + web URL, and the
    hub / project / folder that contain it - so an orienting agent knows its place in the data model
    (and has the URN that doc_copy / doc_open / data_* need) WITHOUT a separate doc_get call.

    An UNSAVED document has no DataFile yet, so the cloud identity is null and saved=false - surfaced
    plainly rather than guessed. Every read is defensive: a missing/erroring folder or project field
    just stays null (cloud reads fail in surprising ways) and never breaks the orient.

    Grounded in adsk.core: Document.dataFile -> DataFile(.id/.versionNumber/.latestVersionNumber/
    .fusionWebURL, .parentFolder, .parentProject); DataFolder(.name/.id); DataProject(.name/.id,
    .parentHub); DataHub(.name)."""
    ident = {
        "saved_to_cloud": False,
        "document_id": None,          # lineage URN - the id doc_open / doc_copy / data_delete_file use
        "version_number": None,
        "latest_version_number": None,
        "web_url": None,
        "hub": None,
        "project": None,
        "project_id": None,
        "folder": None,
        "folder_id": None,
    }
    df = safe(lambda: doc.dataFile)
    if not df:
        return ident       # never-saved doc: no data-model identity yet (saved_to_cloud stays false)
    ident["saved_to_cloud"] = True
    ident["document_id"] = safe(lambda: df.id)
    ident["version_number"] = safe(lambda: df.versionNumber)
    ident["latest_version_number"] = safe(lambda: df.latestVersionNumber)
    ident["web_url"] = safe(lambda: df.fusionWebURL)

    folder = safe(lambda: df.parentFolder)
    if folder is not None:
        ident["folder"] = safe(lambda: folder.name)
        ident["folder_id"] = safe(lambda: folder.id)
    project = safe(lambda: df.parentProject)
    if project is not None:
        ident["project"] = safe(lambda: project.name)
        ident["project_id"] = safe(lambda: project.id)
        hub = safe(lambda: project.parentHub)
        if hub is not None:
            ident["hub"] = safe(lambda: hub.name)
    return ident


def _overall_bbox(root, design):
    """The whole-design world-aligned bounding box: size (x/y/z) + center, in the design's display
    units - the 'how big is this thing, and where is it relative to the origin' read every modelling
    decision needs. None when there's no solid geometry yet (an empty/sketch-only design).

    Grounded in adsk.fusion: Component.boundingBox -> BoundingBox3D(.minPoint/.maxPoint) in cm; we
    scale to the design's defaultLengthUnits so the numbers match everything else the agent sees."""
    bb = safe(lambda: root.boundingBox)
    mn = safe(lambda: bb.minPoint) if bb is not None else None
    mx = safe(lambda: bb.maxPoint) if bb is not None else None
    if mn is None or mx is None:
        return None
    units = safe(lambda: design.unitsManager.defaultLengthUnits) or "cm"
    # internal API length is cm; convert to display units via the units manager (robust to any unit).
    def conv(v_cm):
        out = safe(lambda: design.unitsManager.convert(v_cm, "cm", units))
        return out if out is not None else v_cm
    xmn, ymn, zmn = safe(lambda: mn.x, 0.0), safe(lambda: mn.y, 0.0), safe(lambda: mn.z, 0.0)
    xmx, ymx, zmx = safe(lambda: mx.x, 0.0), safe(lambda: mx.y, 0.0), safe(lambda: mx.z, 0.0)
    return {
        "units": units,
        "size": {"x": round(conv(xmx - xmn), 4), "y": round(conv(ymx - ymn), 4),
                 "z": round(conv(zmx - zmn), 4)},
        "center": {"x": round(conv((xmx + xmn) / 2), 4), "y": round(conv((ymx + ymn) / 2), 4),
                   "z": round(conv((zmx + zmn) / 2), 4)},
    }


def _view_state():
    """What the CAMERA is currently showing - so a screenshot-driven agent knows whether it needs to
    reframe before its first view_screenshot. Returns projection (perspective/orthographic) + the eye
    and target world points (rounded), or None if no viewport.

    Grounded in adsk.core: app.activeViewport.camera -> Camera(.cameraType, .eye, .target). cameraType
    is an enum; 0 == OrthographicCameraType, 1 == PerspectiveCameraType (PerspectiveWithOrthoFacesCameraType
    also perspective) - we map defensively and fall back to the raw value."""
    vp = safe(lambda: app.activeViewport)
    cam = safe(lambda: vp.camera) if vp is not None else None
    if cam is None:
        return None
    ct = safe(lambda: cam.cameraType)
    projection = {0: "orthographic", 1: "perspective", 2: "perspective"}.get(ct, ct)

    def pt(p):
        if p is None:
            return None
        return {"x": round(safe(lambda: p.x, 0.0), 3), "y": round(safe(lambda: p.y, 0.0), 3),
                "z": round(safe(lambda: p.z, 0.0), 3)}
    return {
        "projection": projection,
        "eye": pt(safe(lambda: cam.eye)),
        "target": pt(safe(lambda: cam.target)),
    }


def _selection_echo():
    """A COMPACT echo of what the user currently has selected in Fusion - the cheapest bridge from the
    human's intent to an actionable handle. One short record per selection (kind + name/owner); for the
    full geometry detail + direction vectors the agent calls sys_get_selection. Returns (count, list).

    Grounded in adsk.core: ui.activeSelections (.count/.item(i)); Selection.entity. Kept deliberately
    shallow (no areas/centroids/handles) so the cold-boot read stays cheap - sys_get_selection is the
    deep version this points at."""
    ui = safe(lambda: app.userInterface)
    sels = safe(lambda: ui.activeSelections) if ui is not None else None
    count = safe(lambda: sels.count, 0) if sels is not None else 0
    out = []
    for i in range(min(count or 0, 10)):
        ent = safe(lambda i=i: sels.item(i).entity)
        if ent is None:
            continue
        tname = safe(lambda: type(ent).__name__) or "Unknown"
        rec = {"type": tname}
        # name/owner depending on kind - enough to know WHAT was clicked, not full geometry
        if tname in ("BRepFace", "BRepEdge", "BRepVertex"):
            rec["kind"] = {"BRepFace": "face", "BRepEdge": "edge", "BRepVertex": "vertex"}[tname]
            rec["body"] = safe(lambda: ent.body.name)
            occ = safe(lambda: ent.assemblyContext)
            rec["occurrence"] = safe(lambda: occ.fullPathName) if occ is not None else None
        elif tname == "BRepBody":
            rec["kind"] = "body"
            rec["name"] = safe(lambda: ent.name)
        elif tname == "Occurrence":
            rec["kind"] = "occurrence"
            rec["name"] = safe(lambda: ent.fullPathName)
        else:
            rec["kind"] = "other"
            rec["name"] = safe(lambda: ent.name)
        out.append(rec)
    return (count or 0), out


def _timeline_health(design):
    """(errors, warnings, suppressed, total) feature counts from the parametric timeline.
    Mirrors design_ops' mapping (2 error / 1 warning / 3 suppressed). Empty errors == nothing broken."""
    errors = warnings = suppressed = total = 0
    tl = safe(lambda: design.timeline)
    if tl is None:
        return errors, warnings, suppressed, total      # direct-mode designs have no timeline
    for i in range(safe(lambda: tl.count, 0) or 0):
        total += 1
        hs = safe(lambda i=i: tl.item(i).healthState)
        if hs == 2:
            errors += 1
        elif hs == 1:
            warnings += 1
        elif hs == 3:
            suppressed += 1
    return errors, warnings, suppressed, total


def _joint_rollup(root):
    """(joint_count, broken_joints[names]) - a joint is BROKEN only when it failed to COMPUTE:
    healthState 1 (warning) or 2 (error). healthState 3 (SUPPRESSED) is intentional - the author parked
    it (e.g. an alternate joint in a fixture template) - so it is NOT broken. Same signal assembly_probe
    surfaces, rolled to a count + names here."""
    broken = []
    jc = safe(lambda: root.joints)
    n = safe(lambda: jc.count, 0) if jc else 0
    for i in range(n or 0):
        j = safe(lambda i=i: jc.item(i))
        if j is None:
            continue
        hs = safe(lambda j=j: j.healthState)
        if hs in (1, 2):                            # warning/error only; 3=suppressed is intentional
            nm = safe(lambda j=j: j.name) or f"#{i}"
            broken.append(nm)
    return (n or 0), broken


def _grounded_count(root):
    grounded = 0
    occs = safe(lambda: root.occurrences)
    for i in range(safe(lambda: occs.count, 0) or 0 if occs else 0):
        if safe(lambda i=i: occs.item(i).isGrounded, False):
            grounded += 1
    return grounded


def _browser_digest(root):
    """A DEPTH-1 digest of the top-level occurrences - name, component, child + body counts, grounded,
    is-x-ref - NOT the full tree. The point is orientation ('what are the major pieces?'), not the
    exhaustive structure (that's design_get(include=['tree'])'s job, on demand)."""
    digest = []
    occs = safe(lambda: root.occurrences)
    count = safe(lambda: occs.count, 0) if occs else 0
    for i in range(min(count or 0, _DIGEST_LIMIT)):
        o = safe(lambda i=i: occs.item(i))
        if o is None:
            continue
        digest.append({
        "name": safe(lambda o=o: o.name),
        "component": safe(lambda o=o: o.component.name),
        "children": safe(lambda o=o: o.childOccurrences.count, 0),
        "bodies": safe(lambda o=o: o.bRepBodies.count, 0),
        "grounded": bool(safe(lambda o=o: o.isGrounded, False)),
        "is_xref": bool(safe(lambda o=o: o.isReferencedComponent, False)),
        })
    return digest, (count or 0)


def _xref_health(doc):
    """(xref_count, out_of_date[names]) for the document's external references - for ANY document.

    Whenever a document references external components (an assembly of inserted parts, a CAM doc whose
    models are X-refs, a template - any file with xrefs), a reference pointing at an OLDER version of
    its source shows STALE geometry and misses newer features (a joint origin added after the reference
    was made). DocumentReference.isOutOfDate is the authoritative flag (the same one doc_update_xref
    acts on); surfacing it here means the orienting agent learns the external-component status up front
    - on every open, not just templates - instead of acting on the wrong geometry. Read-only (we never
    call getLatestVersion)."""
    refs = safe(lambda: doc.documentReferences)
    n = safe(lambda: refs.count, 0) if refs is not None else 0
    ood = []
    for i in range(n or 0):
        ref = safe(lambda i=i: refs.item(i))
        if ref is None:
            continue
        if bool(safe(lambda ref=ref: ref.isOutOfDate, False)):
            nm = safe(lambda ref=ref: ref.dataFile.name) or safe(lambda ref=ref: ref.name) or f"#{i}"
            ood.append(nm)
    return (n or 0), ood


def _cam_summary(doc):
    """(has_cam, {setups, total_operations, ungenerated_operations}) WITHOUT switching to Manufacture.
    itemByProductType('CAMProductType') is None when the document has no CAM data."""
    cam = safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return False, None
    setups = safe(lambda: cam.setups)
    n_setups = safe(lambda: setups.count, 0) if setups else 0
    total_ops = ungenerated = 0
    for i in range(n_setups or 0):
        s = safe(lambda i=i: setups.item(i))
        ops = safe(lambda s=s: s.allOperations) if s else None
        n = safe(lambda: ops.count, 0) if ops else 0
        total_ops += n or 0
        for k in range(n or 0):
            op = safe(lambda k=k: ops.item(k))
            # an op with no valid toolpath still needs generating
            if op is not None and not safe(lambda op=op: op.hasToolpath, False):
                ungenerated += 1
    return True, {"setups": n_setups or 0, "total_operations": total_ops,
    "ungenerated_operations": ungenerated}


def handler() -> dict:
    """Cold-boot orientation: one read that situates the agent in the open document. Read-only.

    Returns the document identity + units + modelling mode, content counts (components/bodies/sketches/
    joints) with a depth-1 browser digest, a health rollup (timeline errors/warnings, broken joints,
    grounded count), whether CAM data exists (+ setup/operation counts), and a POINTERS block naming
    the targeted tool to refine each area - steering away from whole-design dumps when the design is
    large. Call this FIRST on an open document; use the pointers to drill down cheaply.
    """
    out = {
    "fusion_version": safe(lambda: app.version),
    "document": None,
    "workspace": safe(lambda: app.userInterface.activeWorkspace.name),
    "product": safe(lambda: app.activeProduct.productType),
    }

    doc = safe(lambda: app.activeDocument)
    if doc is None:
        return error("No active document. Open or create one first (see doc_new / doc_open).")
    out["document"] = {
    "name": safe(lambda: doc.name),
    "saved": bool(safe(lambda: doc.isSaved, False)),
    "modified": bool(safe(lambda: doc.isModified, False)),
    # WHERE it lives in the data model: URN + version + web URL + hub/project/folder. Lets the agent
    # orient within the data model (and get the URN doc_copy/doc_open/data_* need) in this one read.
    "data_model": _data_identity(doc),
    }

    # Camera state (what a screenshot will show - reframe or not?) and the user's current selection
    # (the cheapest bridge from human intent to a handle) are document-independent - report on EVERY
    # path, including a non-Design document.
    out["view"] = _view_state()
    sel_count, sel = _selection_echo()
    out["selection"] = {"count": sel_count, "selected": sel}

    design = _common.design()
    pointers = {}

    xref_count, out_of_date = _xref_health(doc)   # doc-level: works even without an active Design

    if design is None:
        # A document is open but it's not a Design (e.g. a drawing). Report what we can + a pointer.
        has_cam, cam = _cam_summary(doc)
        out["has_design"] = False
        out["has_cam"] = has_cam
        if cam:
            out["cam"] = cam
        out["references"] = {"count": xref_count, "out_of_date": out_of_date}
        note = ("A document is open but no Design product is active. "
    "Switch to the Design workspace, or use the CAM tools if has_cam is true.")
        if out_of_date:
            note = (f"WARNING: {len(out_of_date)} out-of-date reference(s) - run doc_update_xref. " + note)
        out["note"] = note
        return ok(out)

    root = safe(lambda: design.rootComponent)
    mode = _design_mode(design)
    occ_total = safe(lambda: root.allOccurrences.count, 0) or 0
    body_total = safe(lambda: root.bRepBodies.count, 0) or 0
    sketch_total = safe(lambda: root.sketches.count, 0) or 0
    param_total = safe(lambda: design.userParameters.count, 0) or 0

    errors, warnings, suppressed, tl_total = _timeline_health(design)
    joint_count, broken_joints = _joint_rollup(root)
    grounded = _grounded_count(root)
    digest, top_level = _browser_digest(root)
    has_cam, cam = _cam_summary(doc)

    out["has_design"] = True
    out["design"] = {
    "root_component": safe(lambda: root.name),
    "units": safe(lambda: design.unitsManager.defaultLengthUnits),
    "mode": mode,
    "top_level_occurrences": top_level,
    "total_occurrences": occ_total,
    "bodies": body_total,
    "sketches": sketch_total,
    "parameters": param_total,
    # Overall world-aligned size + center: the 'how big, and where vs. the origin' read that governs
    # every dimension chosen afterward. None for an empty/sketch-only design (no solid geometry).
    "overall_bbox": _overall_bbox(root, design),
    }
    out["health"] = {
    "timeline_features": tl_total,
    "timeline_errors": errors,
    "timeline_warnings": warnings,
    "timeline_suppressed": suppressed,
        "joint_count": joint_count,
        "broken_joints": broken_joints,
        "grounded_occurrences": grounded,
        # Out-of-date references are a HEALTH problem - a template with stale parts shows the wrong
        # geometry - so they count against is_healthy, alongside timeline errors and broken joints.
        "out_of_date_references": out_of_date,
        "is_healthy": (errors == 0 and not broken_joints and not out_of_date),
    }
    out["references"] = {"count": xref_count, "out_of_date": out_of_date}
    out["has_cam"] = has_cam
    if cam:
        out["cam"] = cam
    out["browser_digest"] = digest

    # ── POINTERS: the heart of progressive disclosure. Name the TARGETED tool for each area, and when
    # the design is large, steer AWAY from whole-design dumps toward a scoped call. The agent reads
    # these instead of guessing which family to probe - situational awareness without the round trips.
    large = occ_total > _BIG_OCCURRENCES or body_total > _BIG_BODIES
    pointers["assembly_structure"] = (
        f"design_get(include=['tree'], component='<name from browser_digest>') - {occ_total} occurrences is large; "
        "scope to a component rather than dumping the whole tree."
        if occ_total > _BIG_OCCURRENCES else
        "design_get(include=['tree']) - small enough to walk the whole assembly in one call.")
    pointers["geometry"] = (
        f"find_geometry(target='<occurrence/body>') - {body_total} bodies; always scope by target "
        "(filter by kind/radius/nearest_to) rather than scanning the whole design."
        if body_total > _BIG_BODIES else
        "find_geometry(target='<part>', kind=...) to get stable handles for jointing/filleting.")
    if joint_count or grounded:
        pointers["kinematics"] = "assembly_probe() for full per-occurrence position/ground/joint state."
    if param_total:
        pointers["parameters"] = (
            f"param_get() to read the {param_total} user parameter(s); param_set / param_add to change them.")
    if sel_count:
        pointers["selection"] = (
            f"sys_get_selection() for full detail (geometry + direction vectors + handles) on the "
            f"{sel_count} entity(ies) the user has selected - likely what they mean by 'this'.")
    if broken_joints or errors:
        pointers["fix_health"] = ("design_recompute() then re-orient - there are "
                                  f"{errors} timeline error(s) and {len(broken_joints)} broken joint(s).")
    if out_of_date:
        pointers["fix_references"] = (
            f"doc_update_xref() - {len(out_of_date)} external reference(s) are OUT OF DATE "
            f"({', '.join(out_of_date[:5])}). Stale references show the wrong geometry (and miss newer "
            "features); refresh before relying on, machining, or inserting this part.")
    if has_cam:
        pointers["cam"] = ("cam_get() for the machining job; "
                           + (f"{cam['ungenerated_operations']} operation(s) need generating."
                              if cam and cam.get("ungenerated_operations") else "toolpaths look generated."))
    out["pointers"] = pointers

    # Lead the note with the FINDINGS (facts an agent should see first), not a laundered "UNHEALTHY"
    # verdict. is_healthy is a conservative OR; on a deliberately-configured doc (a fixture/CAM template
    # with parked joints or intentionally-pinned references) these can be BY DESIGN. State what was
    # found + point at the check; let the agent judge whether it's a problem here.
    if errors or broken_joints or out_of_date:
        bits = []
        if errors:
            bits.append(f"{errors} timeline error(s)")
        if broken_joints:
            bits.append(f"{len(broken_joints)} joint(s) failed to compute")
        if out_of_date:
            bits.append(f"{len(out_of_date)} out-of-date reference(s)")
        verdict = (f"Attention ({', '.join(bits)}) - see health + the fix_* pointer(s). These CAN be "
                   "intentional on a fixture/CAM template (parked alternates, pinned refs); confirm "
                   "before treating as broken. ")
    else:
        verdict = "No compute errors, failed joints, or stale references. "
    out["note"] = (
        verdict +
        "Use 'pointers' to drill down with scoped calls instead of whole-design dumps."
        + (" Design is LARGE - prefer scoped calls." if large else "")
        + ("" if out["document"]["data_model"]["saved_to_cloud"] else
           " Document is UNSAVED - no URN/project yet; save before addressing it by id."))
    return ok(out)


TOOL_DESCRIPTION = (
    "GETTING STARTED / where am I: cold-boot orientation - call FIRST on an open document for one cheap "
    "situational read instead of fishing across tool families. (New to this server? Start with "
    "sys_capability_map for the tool families, then this.) Returns the document + its data-model "
    "location (hub/project/folder + "
    "URN), overall bounding box, camera state, current selection, content counts + a depth-1 browser "
    "digest, a health rollup (timeline errors, broken joints, out-of-date references -> is_healthy), CAM "
    "presence, and a 'pointers' block naming the targeted tool to refine each area (design_get(include=['tree']), "
    "find_geometry, assembly_probe, cam_get). Orient here, then drill down with those scoped calls."
)

tool = Tool.create_simple(name="workspace_orient", description=TOOL_DESCRIPTION).strict_schema()
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
