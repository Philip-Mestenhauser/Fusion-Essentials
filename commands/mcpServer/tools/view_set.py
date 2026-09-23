# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Move the camera, isolate/show/hide, toggle style, and RESTORE the prior visual state.

Camera orientation is set via explicit eye/target/upVector: assigning camera.viewOrientation does
not reliably move the eye/target. The snapshot stack is module-level so it survives between MCP
calls within one session."""

import json
import math
import types

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _geom
from . import _inputs
from . import _view_common
from . import _write_guard

app = adsk.core.Application.get()

# Monotonic call counter -> the per-response 'request_echo' tracer. A REPEATED seq means the
# handler never re-ran (a replayed response); an advancing seq whose 'received' echo is stale
# under new inputs means the wrong args reached the server. Cleared on reload.
_CALL_SEQ = 0

_ACTIONS = ("snapshot", "orient", "isolate", "show", "hide", "clear_isolation", "display",
    "style", "restore", "save_view", "apply_view", "list_views")
# The occurrence cap snapshot/restore/clear_isolation run under. A larger assembly gets a PARTIAL
# snapshot, so every payload built off the walk publishes 'truncated' and the count it stopped at.
_MAX_OCC = 1000

_TARGET = _inputs.OccurrenceRefList("target",
        description="One occurrence to isolate, by fullPathName/name or handle.")
# hide/show also reach single BODIES, which occurrence-granular visibility cannot; isolate stays
# occurrence-only. with_kinds so the handler branches occurrence-vs-body.
_VIS_TARGET = _inputs.TargetRefList("target", with_kinds=True,
        contract=("isolate: exactly one occurrence. show/hide: one or more occurrences or bodies. "
                  "Use handles or unambiguous names/paths."))
_FOCUS = _inputs.OccurrenceRef("focus",
        description="Occurrence or sketch name to frame the view on (orient).")

# The way forward a SHARED sketch name gets on this input, replacing the default rename-one remedy
# (a shared sketch name usually comes from two referenced documents, where renaming means editing
# a different document).
_FOCUS_SKETCH_REMEDY = ("'focus' carries no component scope, so no spelling of it picks one of them. "
                        "It does take an occurrence fullPathName - frame the placement of the "
                        "component you mean (design_get(include=['tree']) lists the paths).")

# Saved-state stack, keyed by _doc_key (one key per DOCUMENT) so snapshots don't cross documents.
# Each entry: {"camera": <Camera copy>, "visualStyle": int, "occ": {fullPath: (bulb, isolated)}}
_SNAPSHOTS = {}

# The named-orientation table: view_direction is eye - target, the direction FROM the model TO
# the camera.
_ORIENTATIONS = _view_common.VIEW_DIRECTIONS
_STYLES = {
"shaded": "ShadedVisualStyle",
"shaded-hidden-edges": "ShadedWithHiddenEdgesVisualStyle",
"shaded-edges": "ShadedWithVisibleEdgesOnlyVisualStyle",
"wireframe": "WireframeVisualStyle",
"wireframe-hidden-edges": "WireframeWithHiddenEdgesVisualStyle",
"wireframe-edges": "WireframeWithVisibleEdgesOnlyVisualStyle",
}

# Camera projection: a wire key -> its adsk.core.CameraTypes member name. Fusion's third camera
# type, PerspectiveWithOrthoFaces, is NOT offered here - assigning it coerces to plain Perspective.
_PROJECTIONS = {
    "orthographic": "OrthographicCameraType",
    "perspective": "PerspectiveCameraType",
}


def _camera_type(key):
    """The adsk.core.CameraTypes member a projection key names."""
    return getattr(adsk.core.CameraTypes, _PROJECTIONS[key])


def _projection_key(value):
    """The projection key a CameraTypes value carries - perspective_ortho_faces included, since a
    user's camera can already be in that mode - falling back to the stringified value so a
    read-back mismatch names what it found."""
    for key in _PROJECTIONS:
        if _camera_type(key) == value:
            return key
    if value == adsk.core.CameraTypes.PerspectiveWithOrthoFacesCameraType:
        return "perspective_ortho_faces"
    return str(value)


def _is_perspective(value):
    """True for either of Fusion's perspective camera types - the ones perspectiveAngle applies to.
    PerspectiveWithOrthoFaces counts: view_set cannot set that mode, but a camera already in it
    accepts a written angle."""
    return value in (_camera_type("perspective"),
                     adsk.core.CameraTypes.PerspectiveWithOrthoFacesCameraType)


# A closed document's snapshot describes a viewport and an occurrence set that closed with it, so
# the shared key registry's eviction drops it in the same pass.
_write_guard.on_key_evicted(lambda key: _SNAPSHOTS.pop(key, None))


def _carry_snapshot(old_key, new_key):
    """Move this document's saved state onto the key it answers now - a save re-keys an open
    document, and the snapshot is the only copy of its pre-explore camera/visibility state."""
    snap = _SNAPSHOTS.pop(old_key, None)
    if snap is not None:
        _SNAPSHOTS[new_key] = snap


_write_guard.on_key_renamed(_carry_snapshot)


def _doc_key():
    """The key the snapshot store holds the active document's saved state under; "<active>" stands
    in for a call where no document reads at all."""
    key = _write_guard.document_key()
    return "<active>" if key is None else key


def _show_with_ancestors(occ):
    """Turn on this occurrence's light bulb AND every ancestor's, each read back; returns (names
    whose bulb reads on, names whose bulb did NOT). A nested occurrence is visible only when its
    whole assemblyContext chain is lit."""
    lit, stuck = [], []
    cur = occ
    guard = 0
    while cur and guard < 64:
        nm = safe(lambda cur=cur: cur.name)
        safe(lambda cur=cur: setattr(cur, "isLightBulbOn", True))
        if _common.read_flag(lambda cur=cur: cur.isLightBulbOn) is True:
            lit.append(nm)
        else:
            stuck.append(nm or "?")
        cur = safe(lambda cur=cur: cur.assemblyContext)  # parent occurrence; None at root
        guard += 1
    return lit, stuck


# ---------------------------------------------------------------------------
# action handlers
# ---------------------------------------------------------------------------

def _capped_occurrences(design):
    """(occurrences, truncated) - the occurrence walk every snapshot/restore/clear_isolation runs
    over, bounded by _MAX_OCC. `truncated` is True when the design holds MORE than the cap, which
    is what turns "all visibility saved" into a claim the payload has to qualify."""
    occs = _common.all_occurrences(design, cap=_MAX_OCC + 1)
    return occs[:_MAX_OCC], len(occs) > _MAX_OCC


def _do_snapshot(design):
    vp = app.activeViewport
    occ_state = {}
    occs, truncated = _capped_occurrences(design)
    for o in occs:
        fp = safe(lambda o=o: o.fullPathName)
        if fp is None:
            continue
        occ_state[fp] = (bool(safe(lambda o=o: o.isLightBulbOn, True)),
                         bool(safe(lambda o=o: o.isIsolated, False)))
    # Per-component display-folder bulbs, keyed by _common.native_identity: a name is non-unique
    # and a bare entityToken is DOCUMENT-LOCAL, so every document's root answers the same one. A
    # component whose identity does not read is skipped rather than keyed with every other.
    folder_state = {}
    for comp in _view_common.all_display_components(design):
        ident = _common.native_identity(comp)
        if ident is None:
            continue
        folder_state[ident] = {
            attr: _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr))
            for attr in _view_common.DISPLAY_FOLDERS.values()}
    # Camera objects are snapshots by value when read; store a copy.
    cam = vp.camera
    # ONE key read, used for both the store and the payload: a 'saved_for' naming a key the
    # snapshot is not stored under is a pointer to nothing.
    key = _doc_key()
    _SNAPSHOTS[key] = {
                         "camera": cam,
                         "visualStyle": int(safe(lambda: vp.visualStyle, 0)),
                         "occ": occ_state,
                         "folders": folder_state,
                         "truncated": truncated,
    }
    note = ("Current camera, visual style, and all occurrence visibility saved. "
            "Explore freely; call view_set(restore) to put it all back.")
    # 'saved_for' is the store key, and for an unsaved document that key is a session token no
    # tool takes as an argument - so the document it belongs to is named beside it.
    out = {"action": "snapshot", "saved_for": key,
           "document": safe(lambda: app.activeDocument.name),
           "occurrences_saved": len(occ_state),
           "visual_style": int(safe(lambda: vp.visualStyle, 0))}
    if truncated:
        out["truncated"] = True
        out["occurrence_cap"] = _MAX_OCC
        note = (f"PARTIAL: this assembly holds more than {_MAX_OCC} occurrences, so only the first "
                f"{len(occ_state)} had their visibility saved - restore will not reinstate the "
                "rest. Camera and visual style are complete.")
    out["note"] = note
    return ok(out)


def _camera_axes(cam):
    """The camera's screen-plane unit vectors (right, up), or None if eye/target/up will not read
    or are degenerate. Both boxes in a framing ratio are measured on THESE axes, so whatever the
    viewport's aspect and the fit's own margin are, they are the same for both and cancel."""
    eye, tgt, up = safe(lambda: cam.eye), safe(lambda: cam.target), safe(lambda: cam.upVector)
    if eye is None or tgt is None or up is None:
        return None
    lx, ly, lz = tgt.x - eye.x, tgt.y - eye.y, tgt.z - eye.z
    ux, uy, uz = up.x, up.y, up.z
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    un = math.sqrt(ux * ux + uy * uy + uz * uz)
    if not ln or not un:
        return None
    lx, ly, lz = lx / ln, ly / ln, lz / ln
    ux, uy, uz = ux / un, uy / un, uz / un
    rx, ry, rz = ly * uz - lz * uy, lz * ux - lx * uz, lx * uy - ly * ux
    rn = math.sqrt(rx * rx + ry * ry + rz * rz)
    if not rn:                      # up parallel to the look direction - no screen plane
        return None
    return (rx / rn, ry / rn, rz / rn), (ux, uy, uz)


def _screen_span(bb, right, up):
    """(width, height) a WORLD-AXIS-ALIGNED box spans on the camera's screen axes. Exact for an
    AABB: each world extent contributes its own length times that axis's screen component."""
    dx = abs(bb.maxPoint.x - bb.minPoint.x)
    dy = abs(bb.maxPoint.y - bb.minPoint.y)
    dz = abs(bb.maxPoint.z - bb.minPoint.z)
    return (dx * abs(right[0]) + dy * abs(right[1]) + dz * abs(right[2]),
            dx * abs(up[0]) + dy * abs(up[1]) + dz * abs(up[2]))


# Headroom around the framed entity. Well above 1.0 on purpose: a frame drawn tight to the
# subject reads as claustrophobic and hides the context that makes the subject legible.
_FRAME_MARGIN = 2.0


def _frame_world_spans(vp):
    """(width, height) of what the viewport currently shows, in MODEL units, off the viewport's own
    view->model mapping - four reads that move no camera. Exact on an orthographic camera; on a
    perspective one it is the span at the depth the mapping picks."""
    w, h = safe(lambda: vp.width), safe(lambda: vp.height)
    if not w or not h:
        return None

    def at(px, py):
        return safe(lambda: vp.viewToModelSpace(adsk.core.Point2D.create(float(px), float(py))))

    left, right = at(0, h / 2.0), at(w, h / 2.0)
    top, bottom = at(w / 2.0, 0), at(w / 2.0, h)
    if left is None or right is None or top is None or bottom is None:
        return None

    def span(p, q):
        return math.sqrt((p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2)

    across, down = span(left, right), span(top, bottom)
    if across <= 0 or down <= 0:
        return None
    return across, down


def _frame_ratio(cam, focus_bb, frame_w, frame_h):
    """How far the camera's extents must scale for 'focus_bb' to fill a frame_w x frame_h frame, or
    None when the box or camera basis will not read. The LARGER per-axis ratio wins (the focus must
    fit across AND down) and it is not capped at 1, so a bigger focus legitimately zooms out."""
    axes = _camera_axes(cam)
    if axes is None or focus_bb is None or not frame_w or not frame_h:
        return None
    right, up = axes
    fw, fh = _screen_span(focus_bb, right, up)
    if fw <= 0 and fh <= 0:
        # A point sketch, or an entity seen exactly edge-on, spans nothing on either screen axis:
        # there is no size to scale to, but re-aiming at it is still the right answer.
        return 0.0
    return max(fw / frame_w, fh / frame_h) * _FRAME_MARGIN


def _aim_camera(cam, target, orientation):
    """Aim one camera at target and return the standoff fallback, if any."""
    if orientation:
        dx, dy, dz = _ORIENTATIONS[orientation]
        ux, uy, uz = _view_common.up_vector(orientation)
        dmag = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dist, fallback = _view_common.standoff_distance(cam)
        cam.target = target
        cam.eye = adsk.core.Point3D.create(target.x + dx / dmag * dist,
                                           target.y + dy / dmag * dist,
                                           target.z + dz / dmag * dist)
        cam.upVector = adsk.core.Vector3D.create(ux, uy, uz)
        return fallback
    eye, old_target = cam.eye, cam.target
    cam.eye = adsk.core.Point3D.create(eye.x + target.x - old_target.x,
                                       eye.y + target.y - old_target.y,
                                       eye.z + target.z - old_target.z)
    cam.target = target
    return None


def _point_values(point):
    """Return a readable point as an xyz tuple, else None."""
    if point is None:
        return None
    values = safe(lambda: (float(point.x), float(point.y), float(point.z)))
    return values if values is not None and all(math.isfinite(v) for v in values) else None


def _same_point(left, right):
    """Whether two readable points agree within camera read-back tolerance."""
    a, b = _point_values(left), _point_values(right)
    return a is not None and b is not None and all(abs(x - y) <= 1e-6 for x, y in zip(a, b))


def _box_corners(bb):
    """Return the eight world corners of a readable axis-aligned box."""
    lo, hi = safe(lambda: bb.minPoint), safe(lambda: bb.maxPoint)
    if lo is None or hi is None:
        return []
    return [adsk.core.Point3D.create(x, y, z)
            for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]


def _projected_box(vp, bb):
    """Return the focus box bounds in viewport pixels, else None."""
    points = [safe(lambda p=p: vp.modelToViewSpace(p)) for p in _box_corners(bb)]
    if len(points) != 8 or any(p is None for p in points):
        return None
    xs = [safe(lambda p=p: float(p.x)) for p in points]
    ys = [safe(lambda p=p: float(p.y)) for p in points]
    if any(v is None or not math.isfinite(v) for v in xs + ys):
        return None
    return min(xs), min(ys), max(xs), max(ys)


_PERSPECTIVE_FRAME_ATTEMPTS = 5
_PERSPECTIVE_FILL_TOLERANCE = 0.12


def _perspective_frame(vp, cam, focus_bb, target, label):
    """Frame a perspective focus from projected readbacks or return an honest error."""
    width, height = safe(lambda: float(vp.width)), safe(lambda: float(vp.height))
    if not width or not height or width <= 0 or height <= 0:
        current = safe(lambda: vp.camera)
        current_target = _point_values(safe(lambda: current.target))
        current_type = safe(lambda: current.cameraType)
        projection = "unreadable" if current_type is None else _projection_key(current_type)
        return None, (f"Could not read the viewport size while framing '{label}'; camera target "
                      f"reads {current_target if current_target is not None else 'unreadable'} and "
                      f"projection reads '{projection}', so the focus was not applied.")
    desired_w, desired_h = width / _FRAME_MARGIN, height / _FRAME_MARGIN
    last = None
    attempts = 0
    smooth_error = None
    for attempts in range(1, _PERSPECTIVE_FRAME_ATTEMPTS + 1):
        read_cam, cam_smooth_error = _view_common.apply_camera(vp, cam, fit=False)
        smooth_error = smooth_error or cam_smooth_error
        actual_target = safe(lambda: read_cam.target)
        actual_type = safe(lambda: read_cam.cameraType)
        bounds = _projected_box(vp, focus_bb)
        if not _is_perspective(actual_type):
            projection = "unreadable" if actual_type is None else _projection_key(actual_type)
            found = _point_values(actual_target)
            return None, (f"Camera target reads "
                          f"{found if found is not None else 'unreadable'} and projection reads "
                          f"'{projection}'; perspective framing on '{label}' is unverified.")
        if not _same_point(actual_target, target):
            found = _point_values(actual_target)
            return None, (f"Aimed the camera at '{label}', but its target reads "
                          f"{found if found is not None else 'unreadable'}; framing is unverified.")
        if bounds is None:
            return None, (f"Camera target reads the focus '{label}' and projection reads "
                          f"'{_projection_key(actual_type)}', but its projected bounds could not "
                          "be read; framing is unverified.")
        left, top, right, bottom = bounds
        span_w, span_h = right - left, bottom - top
        if span_w <= 0 and span_h <= 0:
            return {"camera": read_cam, "frame_fill": None,
                    "no_measurable_size": True, "smooth_error": smooth_error}, None
        fill = max(span_w / desired_w, span_h / desired_h)
        contained = left >= 0 and top >= 0 and right <= width and bottom <= height
        last = (bounds, fill, contained, actual_type)
        if (contained and 1 - _PERSPECTIVE_FILL_TOLERANCE <= fill
                <= 1 + _PERSPECTIVE_FILL_TOLERANCE):
            return {"camera": read_cam, "frame_fill": round(fill, 4),
                    "smooth_error": smooth_error}, None
        eye = safe(lambda: read_cam.eye)
        target_read = safe(lambda: read_cam.target)
        eye_values, target_values = _point_values(eye), _point_values(target_read)
        if (eye_values is None or target_values is None
                or not math.isfinite(fill) or fill <= 0):
            break
        ex, ey, ez = eye_values
        tx, ty, tz = target_values
        dx, dy, dz = ex - tx, ey - ty, ez - tz
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance <= 0:
            break
        next_distance = distance * fill
        corners = _box_corners(focus_bb)
        depth = max((abs((p.x - tx) * dx / distance + (p.y - ty) * dy / distance
                         + (p.z - tz) * dz / distance) for p in corners), default=0.0)
        next_distance = max(next_distance, depth * 1.05 + 1e-6)
        if not math.isfinite(next_distance) or abs(next_distance - distance) <= 1e-7:
            break
        cam = read_cam
        cam.eye = adsk.core.Point3D.create(tx + dx / distance * next_distance,
                                           ty + dy / distance * next_distance,
                                           tz + dz / distance * next_distance)
    if last is None:
        detail = "projected bounds were unreadable"
        actual_type = safe(lambda: vp.camera.cameraType)
        projection = "unreadable" if actual_type is None else _projection_key(actual_type)
    else:
        bounds, fill, contained, actual_type = last
        detail = f"projected bounds={tuple(round(v, 2) for v in bounds)}, fill={fill:.3f}"
        projection = _projection_key(actual_type)
    return None, (f"Camera target reads the focus '{label}' and projection reads '{projection}', "
                  f"but framing did not verify after {attempts} "
                  f"attempt{'s' if attempts != 1 else ''} "
                  f"({detail}); the camera remains at that read-back state.")


def _do_orient(design, orientation, focus, fit, projection="", perspective_angle_deg=None):
    vp = app.activeViewport
    applied = {}
    cam = vp.camera

    target = cam.target
    focus_bb = None
    if focus:
        names = [n.strip() for n in (focus if isinstance(focus, list) else [focus]) if str(n).strip()]
        boxes, labels, kinds = [], [], []
        for nm in names:
            o, focus_err = _FOCUS.resolve(nm)
            if o is None:
                sk, sketch_err = _common.find_sketch(design, nm, remedy=_FOCUS_SKETCH_REMEDY)
                if sketch_err:
                    return error(sketch_err)
                if sk is None:
                    return error(f"'focus': nothing named '{nm}' to frame - no occurrence and no "
                                 f"sketch carries that name."
                                 + (f" Occurrence lookup said: {focus_err}" if focus_err else ""))
                o, focus_err = sk, None
            if focus_err:
                return error(focus_err)
            labels.append(safe(lambda o=o: o.name) or nm)
            bx, kind = _view_common.focus_box(o)
            if bx is not None:
                boxes.append(bx)
                kinds.append(kind)
        if not boxes:
            return error(f"'focus': none of {labels} has a readable bounding box, so there is "
                         "nothing to frame on. Re-run with fit=false to re-aim only.")
        bb = _geom.union_box(boxes)
        focus_bb = bb
        o = types.SimpleNamespace(name=", ".join(labels), boundingBox=bb)
        target = adsk.core.Point3D.create((bb.minPoint.x + bb.maxPoint.x) / 2,
                                          (bb.minPoint.y + bb.maxPoint.y) / 2,
                                          (bb.minPoint.z + bb.maxPoint.z) / 2)
        applied["focus"] = safe(lambda: o.name)
        applied["extents"] = "solids" if all(k == "solids" for k in kinds) else "all_geometry"

    orientation_key = ""
    if orientation:
        orientation_key = orientation.strip().lower()
        if orientation_key not in _ORIENTATIONS:
            return error(f"Unknown orientation '{orientation}'. Valid: {', '.join(_ORIENTATIONS)}.")
        applied["orientation"] = orientation_key

    want_key = ""
    if projection:
        want_key = projection.strip().lower()
        if want_key not in _PROJECTIONS:
            return error(f"Unknown projection '{projection}'. Valid: {', '.join(_PROJECTIONS)}.")

    angle_deg = None
    if perspective_angle_deg is not None:
        try:
            angle_deg = float(perspective_angle_deg)
        except (TypeError, ValueError):
            return error(f"'perspective_angle_deg' must be a number (got '{perspective_angle_deg}').")
        if not 1 <= angle_deg < 150:
            return error("'perspective_angle_deg' is a field-of-view angle Fusion accepts from 1 "
                         f"to just under 150 degrees (got {angle_deg}).")
        effective = _camera_type(want_key) if want_key else safe(lambda: cam.cameraType)
        if effective is None:
            return error(f"'perspective_angle_deg'={angle_deg} needs a perspective camera, but "
                         "the camera's cameraType could not be read - pass "
                         "projection='perspective' in the same call to set it explicitly.")
        if not _is_perspective(effective):
            return error(f"'perspective_angle_deg'={angle_deg} needs a perspective camera, but the "
                         f"projection in effect is '{_projection_key(effective)}'. Pass "
                         "projection='perspective' in the same call.")

    frame = None
    if (focus and fit and not want_key
            and not _is_perspective(safe(lambda: cam.cameraType))):
        frame = _frame_world_spans(vp)

    standoff_fallback = _aim_camera(cam, target, orientation_key)
    if want_key:
        cam.cameraType = _camera_type(want_key)
    if angle_deg is not None:
        # Camera.perspectiveAngle reads and writes radians; this tool accepts degrees.
        cam.perspectiveAngle = math.radians(angle_deg)

    smooth_error = None
    projection_staged = False
    if focus and want_key:
        # fit=False: staging the projection must not fit the whole model first - the frame spans
        # below are read off THIS staged view, not a fit-everything one.
        staged_cam, smooth_error = _view_common.apply_camera(vp, cam, fit=False)
        projection_staged = True
        got_type = safe(lambda: staged_cam.cameraType)
        staged_target = _point_values(safe(lambda: staged_cam.target))
        staged_target_text = staged_target if staged_target is not None else "unreadable"
        staged_fit = safe(lambda: staged_cam.isFitView)
        staged_fit_text = (str(staged_fit).lower()
                           if isinstance(staged_fit, bool) else "unreadable")
        if got_type is None:
            return error(f"After staging projection '{want_key}', camera target reads "
                         f"{staged_target_text}, isFitView reads '{staged_fit_text}', and cameraType "
                         "could not be read back - projection and focus are unverified.")
        got_key = _projection_key(got_type)
        if got_key != want_key:
            return error(f"After staging projection '{want_key}', camera target reads "
                         f"{staged_target_text}, isFitView reads '{staged_fit_text}', and projection "
                         f"reads back '{got_key}' - the requested projection did not take and focus "
                         "is unverified.")
        cam = staged_cam
        standoff_fallback = _aim_camera(cam, target, orientation_key)
        if fit and not _is_perspective(got_type):
            frame = _frame_world_spans(vp)

    ratio = None
    perspective_frame = None
    is_perspective_focus = (focus and fit
                            and _is_perspective(safe(lambda: cam.cameraType)))
    if is_perspective_focus:
        perspective_frame, framing_error = _perspective_frame(
            vp, cam, focus_bb, target, applied.get("focus"))
        if framing_error:
            return error(framing_error)
        cam = perspective_frame["camera"]
        smooth_error = smooth_error or perspective_frame.get("smooth_error")
        if perspective_frame.get("no_measurable_size"):
            applied["frame_ratio"] = None
            applied["no_measurable_size"] = True
        else:
            applied["frame_fill"] = perspective_frame["frame_fill"]
    elif focus and fit:
        if frame is None:
            if projection_staged:
                return error(f"Projection reads '{want_key}' after staging the projection, but "
                             f"the viewport frame could not be read before aiming at "
                             f"'{applied.get('focus')}'; the focus was not applied.")
            return error(f"Could not read what the viewport currently shows, so the view could not "
                         f"be framed on '{applied.get('focus')}' and the camera was NOT moved. Re-run "
                         "with fit=false to re-aim only.")
        ratio = _frame_ratio(cam, focus_bb, *frame)
        extents = safe(lambda: cam.viewExtents)
        if ratio == 0.0:
            applied["frame_ratio"] = None
            applied["no_measurable_size"] = True
            ratio = None
        elif ratio is None or extents is None:
            return error(f"Could not measure '{applied.get('focus')}' against the current view (a "
                         "bounding box, the camera's axes, or its extents would not read), so the "
                         "view is NOT framed on it. Re-run with fit=false to re-aim only.")
        if ratio is not None:
            cam.viewExtents = extents * ratio
    elif fit or (want_key and not projection_staged):
        cam.isFitView = True

    if perspective_frame is None:
        _, final_smooth_error = _view_common.apply_camera(
            vp, cam, fit=bool(safe(lambda: cam.isFitView)))
        smooth_error = smooth_error or final_smooth_error

    note = "Camera aimed. Call view_screenshot to capture."
    if focus:
        if fit:
            note = (f"Camera aimed and framed on '{applied.get('focus')}'. Call view_screenshot "
                    "to capture.")
        else:
            note = (f"Camera re-aimed at '{applied.get('focus')}' WITHOUT zooming to it - "
                    "fit=false keeps the current eye-to-target distance. Pass fit=true (the "
                    "default) to frame it.")
    if standoff_fallback is not None:
        cm = f"{standoff_fallback:g}"
        applied["standoff_fallback_cm"] = standoff_fallback
        note += (f" standoff_fallback_cm={cm}: the camera's eye-target distance did not read as a "
                 f"positive number, so {cm} cm stood in as the standoff for this orient.")

    if want_key:
        got_type = safe(lambda: vp.camera.cameraType)
        if got_type is None:
            return error(f"Set projection '{want_key}' but the camera's cameraType could not be "
                         "read back - the projection is unverified.")
        got_key = _projection_key(got_type)
        if got_key != want_key:
            return error(f"Set projection '{want_key}' but the viewport camera reads back "
                         f"'{got_key}' - the change did not take.")
        applied["projection"] = got_key
        if not fit and not projection_staged:
            note += (" Changing the projection recomputes the camera extents, so the view was "
                     "fitted even though fit was false.")

    if focus:
        actual_target = safe(lambda: vp.camera.target)
        if not _same_point(actual_target, target):
            found = _point_values(actual_target)
            return error(f"Aimed the camera at '{applied.get('focus')}', but its target reads "
                         f"{found if found is not None else 'unreadable'} - the focus did not take.")

    if angle_deg is not None:
        raw = safe(lambda: vp.camera.perspectiveAngle)
        if raw is None:
            got_type = safe(lambda: vp.camera.cameraType)
            where = f" (projection '{_projection_key(got_type)}')" if got_type is not None else ""
            return error(f"Set 'perspective_angle_deg'={angle_deg} but the camera's "
                         f"perspectiveAngle could not be read back{where} - the field of view is "
                         "unverified.")
        actual = round(math.degrees(raw), 4)
        applied["perspective_angle_deg"] = actual
        if abs(actual - angle_deg) > 0.05:
            applied["perspective_angle_requested_deg"] = round(angle_deg, 4)
            note += (" The camera settled on a different perspective angle than requested - "
                     "'perspective_angle_deg' is what it reads back.")

    if ratio is not None:
        want = safe(lambda: cam.viewExtents)
        got = safe(lambda: vp.camera.viewExtents)
        if want is None or got is None or abs(got - want) > max(1e-6, abs(want) * 0.02):
            target_state = ("target reads the requested focus" if _same_point(
                safe(lambda: vp.camera.target), target) else "target does not read the requested focus")
            projection_state = _projection_key(safe(lambda: vp.camera.cameraType))
            return error(f"Set the camera extents to frame '{applied.get('focus')}' "
                         f"({'unreadable' if want is None else format(want, '.4f')}) but the "
                         f"viewport reads back "
                         f"{'unreadable' if got is None else format(got, '.4f')} - the framing did "
                         f"not verify; {target_state} and projection reads '{projection_state}', "
                         "so the camera remains partially changed.")
        applied["frame_ratio"] = round(ratio, 6)
    # isSmoothTransition is CONSUMED on assignment and reads back at its default (measured
    # 2705.1.25), so this publishes the value ASSIGNED, present only when the setter did not raise.
    if smooth_error:
        note += f" isSmoothTransition could not be set: {smooth_error}."
    else:
        applied["smooth_transition"] = False
    return ok({"action": "orient", "applied": applied, "note": note})

def _partial_suffix(done):
    """The sentence a mid-list failure appends naming what ALREADY changed. A multi-target
    hide/show/isolate that fails on target 3 has already mutated targets 1-2, and a bare error
    naming none of them leaves the caller unable to put them back."""
    names = [n for n in done if n]
    if not names:
        return ""
    return (f" Already changed before this failure: {', '.join(names[:10])}"
            + (f" (+{len(names) - 10} more)" if len(names) > 10 else "") + ".")


def _do_visibility(design, action, target):
    if action == "clear_isolation":
        cleared = 0
        stuck = []
        unconfirmed = []
        occs, truncated = _capped_occurrences(design)
        for o in occs:
            if safe(lambda o=o: o.isIsolated):
                nm = safe(lambda o=o: o.fullPathName) or safe(lambda o=o: o.name) or "?"
                try:
                    o.isIsolated = False
                except Exception:
                    stuck.append(nm)
                    continue
                # Read it back: a write the platform swallows would otherwise be counted as cleared.
                # A read-back that DECLINES saw no value at all, so it is neither cleared nor stuck.
                got = _common.read_flag(lambda o=o: o.isIsolated)
                if got is False:
                    cleared += 1
                elif got is None:
                    unconfirmed.append(nm)
                else:
                    stuck.append(nm)
        out = {"action": action, "cleared_count": cleared}
        note = ""
        if truncated:
            out["truncated"] = True
            out["occurrence_cap"] = _MAX_OCC
            note = (f"PARTIAL: only the first {_MAX_OCC} occurrences were checked - an "
                    "isolation past the cap is still set.")
        if stuck:
            out["stuck"] = stuck[:20]
            note += (f" WARNING: {len(stuck)} occurrence(s) still read isIsolated true after the "
                     "clear - see 'stuck'.")
        if unconfirmed:
            out["not_confirmed"] = unconfirmed[:20]
            note += (f" WARNING: isIsolated did not read back on {len(unconfirmed)} occurrence(s), "
                     "so the clear is unconfirmed there - see 'not_confirmed'.")
        if note:
            out["note"] = note.strip()
        return ok(out)
    if not target:
        return error(f"Provide 'target' for {action}.")
    if action == "isolate":
        # isolate is occurrence-granular in Fusion (Occurrence.isIsolated; a body has no isolate).
        matches, target_err = _TARGET.resolve(target)
        if target_err:
            return error(target_err)
        if len(matches) > 1:
            return error(f"isolate needs exactly one occurrence; {len(matches)} targets resolved. "
                         "Fusion holds one isolation at a time - isolating a second occurrence "
                         "clears the first. For a sub-assembly, isolate its PARENT occurrence: its "
                         "children stay visible. For any other set, snapshot, clear_isolation, then "
                         "hide what you do not want; restore puts the saved visibility back.")
        pairs = [(o, "occurrence") for o in matches]
    else:
        # hide/show also reach single BODIES - the only lever for a ROOT-level body or one body of a
        # multi-body component, which occurrence visibility cannot address.
        pairs, target_err = _VIS_TARGET.resolve(target)
        if target_err:
            return error(target_err)
    affected = []
    ancestors_lit = []
    body_states = []
    for ent, kind in pairs:
        nm = safe(lambda ent=ent: ent.name)
        if kind == "occurrence":
            # Every occurrence write is READ BACK, like the body branch below: the platform accepts
            # a bulb/isolation assignment and can leave the state where it was.
            try:
                if action == "isolate":
                    ent.isIsolated = True
                    got = _common.read_flag(lambda ent=ent: ent.isIsolated)
                    if got is not True:
                        return error(f"Set isolate on '{nm}' but isIsolated reads back {got} - the "
                                     "change did not take." + _partial_suffix(affected))
                elif action == "show":
                    # An occurrence stays hidden if any ANCESTOR occurrence's bulb is off - so turning
                    # on a nested child alone does nothing visible. Light up the whole ancestor chain.
                    lit, stuck = _show_with_ancestors(ent)
                    if stuck:
                        return error(f"Failed to show '{nm}': the light bulb does not read back on "
                                     f"for {', '.join(stuck[:5])}, so it stays hidden."
                                     + _partial_suffix(affected))
                    ancestors_lit.extend(a for a in lit if a != nm)
                elif action == "hide":
                    ent.isLightBulbOn = False
                    got = _common.read_flag(lambda ent=ent: ent.isLightBulbOn)
                    if got is not False:
                        return error(f"Set hide on '{nm}' but isLightBulbOn reads back {got} - the "
                                     "change did not take." + _partial_suffix(affected))
            except Exception as e:
                return error(f"Failed to {action} '{nm}': {e}" + _partial_suffix(affected))
        else:
            # A BODY's browser bulb. isLightBulbOn is the body's OWN bulb; isVisible is the effective
            # state (ancestor bulbs roll up into it) - gate the claim on the bulb read-back, report both.
            want = action == "show"
            try:
                ent.isLightBulbOn = want
            except Exception as e:
                return error(f"Failed to {action} body '{nm}': {e}" + _partial_suffix(affected))
            if want:
                # A shown body stays invisible while an ancestor occurrence is dark - light the chain,
                # the same teaching 'show' applies to a nested occurrence.
                occ = safe(lambda ent=ent: ent.assemblyContext)
                if occ is not None:
                    lit, stuck = _show_with_ancestors(occ)
                    if stuck:
                        return error(f"Set show on body '{nm}' but the ancestor bulb does not read "
                                     f"back on for {', '.join(stuck[:5])}, so the body stays "
                                     "hidden." + _partial_suffix(affected))
                    ancestors_lit.extend(lit)
            got = safe(lambda ent=ent: ent.isLightBulbOn)
            if got is not want:
                return error(f"Set {action} on body '{nm}' but isLightBulbOn reads back {got} - "
                             "the change did not take." + _partial_suffix(affected))
            body_states.append({"body": nm, "light_bulb_on": bool(got),
                                "visible": bool(safe(lambda ent=ent: ent.isVisible, want))})
        affected.append(nm)
    out = {"action": action, "target": target, "affected": affected,
    "note": "Visibility changed. view_screenshot to view; view_set(restore) to undo."}
    if body_states:
        out["bodies"] = body_states
        # snapshot/restore records OCCURRENCE bulbs only - be honest about the undo path for bodies.
        out["note"] = ("Visibility changed. view_screenshot to view. Body bulbs are NOT captured by "
                       "snapshot/restore - undo a body with the opposite hide/show.")
        if any(b["light_bulb_on"] and not b["visible"] for b in body_states):
            out["note"] += (" A body reads light_bulb_on but visible:false - an ancestor occurrence "
                            "is hidden; show that occurrence too.")
    if ancestors_lit:
        out["ancestors_also_shown"] = sorted(set(a for a in ancestors_lit if a))
    return ok(out)


def _do_style(style):
    if not style or style.strip().lower() not in _STYLES:
        return error(f"Provide 'style' - one of: {', '.join(_STYLES)}.")
    vp = app.activeViewport
    before = int(safe(lambda: vp.visualStyle, 0))
    vp.visualStyle = getattr(adsk.core.VisualStyles, _STYLES[style.strip().lower()])
    vp.refresh()
    return ok({"action": "style", "style": style.strip().lower(),
        "visual_style_before": before, "visual_style_after": int(vp.visualStyle)})


def _do_display(design, categories, visible):
    """Toggle the non-body display FOLDERS (sketches / construction / origins / joints) design-wide
    via each component's folder bulb - the switch that clears construction clutter from product
    shots without touching any entity's own bulb. Every write is read back; a component whose bulb
    does not land is reported stuck, never silently skipped."""
    if visible is None:
        return error("Provide 'visible' - true to show the chosen categories, false to hide them.")
    # A permissive client can deliver the boolean as a STRING ('false' is truthy to bool()) -
    # parse the two legal words, refuse anything else instead of guessing a direction.
    if isinstance(visible, str):
        word = visible.strip().lower()
        if word not in ("true", "false"):
            return error(f"'visible' must be true or false; got '{visible}'.")
        visible = (word == "true")
    if isinstance(categories, str) and categories.strip().startswith("["):
        # A permissive client can deliver the array as its JSON text - decode before splitting.
        try:
            categories = json.loads(categories)
        except ValueError:
            pass
    cats = categories if isinstance(categories, list) else (
        [c.strip() for c in str(categories).split(",") if c.strip()] if categories else [])
    cats = [str(c).strip() for c in cats if str(c).strip()]
    cats = cats or list(_view_common.DISPLAY_FOLDERS)      # omitted = every category
    unknown = [c for c in cats if c not in _view_common.DISPLAY_FOLDERS]
    if unknown:
        return error(f"Unknown display categories: {', '.join(unknown)}. "
                     f"Valid: {', '.join(_view_common.DISPLAY_FOLDERS)}.")

    want = bool(visible)
    set_counts = {c: 0 for c in cats}
    stuck = []
    comps = _view_common.all_display_components(design)
    for comp in comps:
        cname = safe(lambda comp=comp: comp.name) or "?"
        for cat in cats:
            attr = _view_common.DISPLAY_FOLDERS[cat]
            if _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr)) is want:
                continue                                   # already there - nothing to write
            safe(lambda comp=comp, attr=attr: setattr(comp, attr, want))
            now = _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr))
            if now is want:
                set_counts[cat] += 1
            else:
                stuck.append({"component": cname, "category": cat,
                              "reads": now})
    out = {
        "action": "display",
        "visible": want,
        "categories": cats,
        "components_walked": len(comps),
        "folders_set": set_counts,
        "note": (("Shown" if want else "Hidden") + ": " + ", ".join(cats) + " (the per-component "
                 "folder bulbs; each entity's own bulb is untouched, so re-showing restores what "
                 "was individually visible before). view_screenshot to see the result."),
    }
    if stuck:
        out["stuck"] = stuck
        out["note"] += (f" WARNING: {len(stuck)} folder bulb(s) did not land or could not be "
                        "read back - see 'stuck'.")
    return ok(out)


def _do_restore(design):
    key = _doc_key()
    snap = _SNAPSHOTS.get(key)
    if not snap:
        # Name the DOCUMENT, not the store key: for an unsaved document that key is an
        # 'unsaved:N' session token - not a name, not a URN, not doc_get's 'open:N' address - so
        # no tool accepts it and no read reports it. The key stands in only if the name will not read.
        doc_label = safe(lambda: app.activeDocument.name) or key
        return error(f"No snapshot saved for '{doc_label}'. Call view_set(snapshot) first. "
    "(Snapshots are held in memory for this session only - reloading the add-in "
    "clears them. To recover a clean state without a snapshot, use "
    "clear_isolation then show the components you want.)")
    vp = app.activeViewport
    restored_occ = 0
    missing = 0
    # restore visibility per occurrence (clear isolation first so bulbs apply cleanly)
    by_path = {}
    occs, walk_truncated = _capped_occurrences(design)
    for o in occs:
        fp = safe(lambda o=o: o.fullPathName)
        if fp is not None:
            by_path[fp] = o
    # clear any current isolation
    failed = []                  # every write whose read-back did NOT confirm the snapshot value
    for o in by_path.values():
        if safe(lambda o=o: o.isIsolated):
            safe(lambda o=o: setattr(o, "isIsolated", False))
    for fp, (bulb, isolated) in snap["occ"].items():
        o = by_path.get(fp)
        if not o:
            missing += 1
            continue
        # Read every bulb/isolation back: counting ATTEMPTS as restored reports a state that was
        # never reinstated, and the snapshot below is popped on that count.
        safe(lambda o=o, bulb=bulb: setattr(o, "isLightBulbOn", bulb))
        ok_bulb = _common.read_flag(lambda o=o: o.isLightBulbOn) is bool(bulb)
        ok_iso = True
        if isolated:
            safe(lambda o=o: setattr(o, "isIsolated", True))
            ok_iso = _common.read_flag(lambda o=o: o.isIsolated) is True
        if ok_bulb and ok_iso:
            restored_occ += 1
        else:
            failed.append(fp)
    # Restore the display-folder bulbs a 'display' toggle may have moved, keyed on the same
    # _common.native_identity the snapshot stored. A component whose identity does not read, and a
    # bulb whose snapshot read was None, are both left alone.
    folders = snap.get("folders") or {}
    if folders:
        for comp in _view_common.all_display_components(design):
            saved = folders.get(_common.native_identity(comp))
            if not saved:
                continue
            for attr, val in saved.items():
                if val is None:
                    continue
                safe(lambda comp=comp, attr=attr, val=val: setattr(comp, attr, val))
                if _common.read_flag(lambda comp=comp, attr=attr: getattr(comp, attr)) is not val:
                    failed.append(f"{safe(lambda comp=comp: comp.name) or '?'}:{attr}")
    # restore visual style + camera - both read back / raised-checked, never silently swallowed
    safe(lambda: setattr(vp, "visualStyle", snap["visualStyle"]))
    style_now = safe(lambda: int(vp.visualStyle))
    style_restored = style_now == snap["visualStyle"]
    if not style_restored:
        failed.append("visualStyle")
    camera_error = None
    smooth_error = None
    try:
        read_back, smooth_error = _view_common.restore_camera(vp, snap["camera"])
        if not _view_common.camera_restored(read_back, snap["camera"]):
            camera_error = "read-back eye/target do not match the saved camera"
            failed.append("camera")
    except Exception as e:
        camera_error = str(e)
        failed.append("camera")
    vp.refresh()
    truncated = bool(snap.get("truncated")) or walk_truncated
    # The snapshot is the ONLY copy of the pre-explore state: pop it just when everything it holds
    # went back, so a partly-failed restore can be retried instead of being unrecoverable.
    if not failed:
        _SNAPSHOTS.pop(key, None)
    out = {"action": "restore", "restored_occurrences": restored_occ,
           "missing_occurrences": missing,
           "visual_style_restored": style_restored,
           "camera_restored": camera_error is None,
           "snapshot_kept": bool(failed),
           "note": "Camera, visual style, and visibility restored to the pre-snapshot state."}
    if truncated:
        out["truncated"] = True
        out["occurrence_cap"] = _MAX_OCC
        out["note"] = (f"PARTIAL: this assembly is past the {_MAX_OCC}-occurrence cap, so "
                       f"{restored_occ} occurrence(s) were restored and the rest keep whatever "
                       "visibility they carry now. Camera and visual style are restored in full.")
    if failed:
        out["failed_restores"] = failed[:20]
        out["failed_restore_count"] = len(failed)
        out["note"] = (f"PARTIAL RESTORE: {len(failed)} saved state(s) did not read back as "
                       f"restored ({', '.join(str(f) for f in failed[:5])})"
                       + (f"; the camera assignment failed: {camera_error}" if camera_error else "")
                       + ". The snapshot was KEPT so view_set(restore) can be retried.")
    if smooth_error:
        out["note"] += f" isSmoothTransition could not be set: {smooth_error}."
    return ok(out)


def _named_views(design):
    return safe(lambda: design.namedViews)


def _do_save_view(design, view_name):
    """Save the CURRENT camera as a persistent Named View in the document, re-aimed later with
    apply_view. A named view stores the CAMERA ONLY - not section state or visibility."""
    name = (view_name or "").strip()
    if not name:
        return error("Provide 'view_name' to save the current camera as a named view.")
    nvs = _named_views(design)
    if nvs is None:
        return error("This design does not expose Named Views.")
    vp = app.activeViewport
    # Overwrite an existing same-named view. itemByName THROWS when absent, so the guard wraps the
    # LOOKUP only: a declined deleteMe() has to be reported, or the add below leaves two views
    # answering to one name and no later apply_view can tell them apart.
    try:
        ex = nvs.itemByName(name)
    except Exception:
        ex = None
    if ex:
        if safe(lambda: ex.deleteMe(), False) is not True:
            return error(f"A named view '{name}' already exists and deleteMe() refused to remove "
                         "it - saving now would leave two views sharing that name. Choose another "
                         "'view_name'.")
    nv = safe(lambda: nvs.add(vp.camera, name))
    if not nv:
        return error(f"Failed to save named view '{name}'.")
    return ok({"action": "save_view", "view_name": safe(lambda: nv.name),
        "total_named_views": safe(lambda: nvs.count),
        "note": "Camera saved as a persistent named view. Recall it with "
        "apply_view, or pair with view_section for a section perspective."})


def _do_apply_view(design, view_name):
    """Jump the camera to a saved named view (your own, or a built-in like 'Home')."""
    name = (view_name or "").strip()
    if not name:
        return error("Provide 'view_name' to apply.")
    nvs = _named_views(design)
    if nvs is None:
        return error("This design does not expose Named Views.")
    try:
        nv = nvs.itemByName(name)
    except Exception:
        nv = None
    if not nv:
        names = [safe(lambda v=v: v.name) for v in _common.iter_collection(nvs)]
        return error(f"No named view '{name}'. Saved views: {', '.join(n for n in names if n) or '(none)'}.")
    # NamedView.apply() RETURNS a bool ("true if the operation was successful", per the installed
    # binding doc) - swallowing it reported a camera move that never happened.
    try:
        did = nv.apply()
    except Exception as e:
        return error(f"Applying named view '{name}' failed: {e}")
    if did is False:
        return error(f"apply() returned false for named view '{name}' - the camera was not moved.")
    app.activeViewport.refresh()
    return ok({"action": "apply_view", "view_name": safe(lambda: nv.name),
        "note": "Camera moved to the named view (camera only - does not change any active "
        "section cut or visibility). If a section is live and this view was a "
        "section perspective, re-issue view_section(cut, ...) to recut for this angle."})


def _do_list_views(design):
    nvs = _named_views(design)
    if nvs is None:
        return error("This design does not expose Named Views.")
    views = []
    for nv in _common.iter_collection(nvs):
        views.append({"name": safe(lambda nv=nv: nv.name),
        "built_in": safe(lambda nv=nv: nv.isBuiltIn)})
    return ok({"action": "list_views", "count": len(views), "named_views": views})


def _trace(action, target, orientation, focus, style, view_name, projection="",
           perspective_angle_deg=None, categories=None, visible=None):
    """A fresh per-call tracer: a monotonic seq + an echo of the args the handler received."""
    global _CALL_SEQ
    _CALL_SEQ += 1
    echo = {"action": action}
    for k, v in (("target", target), ("orientation", orientation), ("focus", focus),
                 ("style", style), ("view_name", view_name), ("projection", projection),
                 ("perspective_angle_deg", perspective_angle_deg), ("categories", categories)):
        if v:
            echo[k] = v
    if visible is not None:                       # False is a real answer here, never dropped
        echo["visible"] = visible
    return {"seq": _CALL_SEQ, "received": echo}


def _with_trace(result, trace):
    """Inject the request tracer into a successful ok() result's JSON payload; a no-op on an error."""
    if result.get("isError"):
        return result
    try:
        payload = json.loads(result["content"][0]["text"])
        payload["request_echo"] = trace
        result["content"][0]["text"] = json.dumps(payload, indent=2)
    except Exception:
        pass
    return result


def handler(action: str = "", target=None, orientation: str = "", focus: str = "",
            style: str = "", fit: bool = True, view_name: str = "", projection: str = "",
            perspective_angle_deg=None, categories=None, visible=None) -> dict:
    """See TOOL_DESCRIPTION."""
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    # Camera-projection inputs only reach the camera through 'orient' - accepting them on another
    # action would report ok while changing no projection at all.
    if action != "orient" and (projection or perspective_angle_deg is not None):
        return error("'projection'/'perspective_angle_deg' apply to action='orient', not "
                     f"action='{action}'.")
    design = _common.design()
    if not design:
        return error("No active design. Open a document with design geometry first.")
    trace = _trace(action, target, orientation, focus, style, view_name, projection,
                   perspective_angle_deg, categories, visible)
    try:
        if action == "snapshot":
            result = _do_snapshot(design)
        elif action == "orient":
            result = _do_orient(design, orientation, focus, fit, projection, perspective_angle_deg)
        elif action in ("isolate", "show", "hide", "clear_isolation"):
            result = _do_visibility(design, action, target)
        elif action == "display":
            result = _do_display(design, categories, visible)
        elif action == "style":
            result = _do_style(style)
        elif action == "restore":
            result = _do_restore(design)
        elif action == "save_view":
            result = _do_save_view(design, view_name)
        elif action == "apply_view":
            result = _do_apply_view(design, view_name)
        elif action == "list_views":
            result = _do_list_views(design)
        else:
            result = error("unreachable")
    except Exception as e:
        return error(f"view_set({action}) failed: {e}")
    return _with_trace(result, trace)


TOOL_DESCRIPTION = (
    "View-state verbs: aim the camera, isolate/show/hide, set the visual style, toggle the "
    "non-body display folders, snapshot and restore - no geometry changes."
)

tool = (
    Tool.create_simple(name="view_set", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.Choice("action", _ACTIONS, required=True).as_property())
    .add_required_input("action")
    .add_input_property(*_VIS_TARGET.as_property())
    .add_input_property("view_name", {"type": "string",
            "description": "For save_view / apply_view."})
    .add_input_property(*_inputs.Choice("orientation", list(_ORIENTATIONS),
            description="For 'orient'.").as_property())
    .add_input_property("focus", {"type": ["string", "array"], "items": {"type": "string"},
            "description": "Occurrence or sketch to frame on; a list frames their union."})
    .add_input_property(*_inputs.Choice("projection", list(_PROJECTIONS)).as_property())
    .add_input_property("perspective_angle_deg", {"type": "number"})
    .add_input_property(*_inputs.Choice("style", list(_STYLES),
            description="For 'style'.").as_property())
    .add_input_property("fit", {"type": "boolean"})
    .add_input_property("categories", {"type": "array",
            "items": {"type": "string", "enum": ["sketches", "construction", "origins", "joints"]},
            "description": "For 'display'; omit = all of them."})
    .add_input_property("visible", {"type": "boolean"})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline", rung="value",
        evidence_test="tests/unit/test_view_set.py::TestVisibilityReadBack"
                      "::test_a_hide_the_platform_swallows_is_an_error"))


def register_tool():
    register(item)
