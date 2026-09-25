# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create one T-spline Form from a control cage and check Fusion's copy of it
record by record, removing the Form when a check fails. WRITES. FormFeatures.add() in a direct
design crashed Fusion (measured), so a design that is not parametric is refused before it."""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _assert
from . import _continuity
from . import _form_common
from . import _inputs
from . import _outputs
from . import _tsm

# An open Form edit reads designType direct too, so this one guard covers both refusals.
_PARAMETRIC_GUARD = _inputs.ModeGuard(
    _inputs.MODE_PARAMETRIC,
    fix_hint="Switch to parametric mode first (design_set_mode), then retry.")
_COMPONENT = _inputs.OccurrenceRef("component")

# A seam above this reads sharp: every smooth seam of a loaded cage read 0.000 deg (measured).
SMOOTH_DEG = 0.01
BBOX_TOL_CM = 1e-4

# Unknown keys in either object are refused by _cage_in_units.
_SHAPE = {"type": "object", "required": ["shape", "size", "spans"],
          "properties": {"shape": {"type": "string", "enum": ["box", "cylinder"]},
                         "size": {"type": "array", "items": {"type": "number"}},
                         "spans": {"type": "array", "items": {"type": "integer"}},
                         "capped": {"type": "boolean", "default": True}}}


def _tuples(kind, n):
    """A list of fixed-length `kind` tuples - what _tsm's shape check accepts."""
    return {"type": "array", "items": {"type": "array", "items": {"type": kind},
                                       "minItems": n, "maxItems": n}}


_CAGE = {"type": "object", "required": ["vertices", "faces"],
         "properties": {"vertices": _tuples("number", 3), "faces": _tuples("integer", 4),
                        "creases": _tuples("integer", 2)}}

RETURNS = [
    _outputs.ReturnsName("form", of="Form"),
    _outputs.ReturnsHandle(key="edge", require="edge"),
]

_NOTE = ("A Form has no parameters. To change it: form_get(include=['cage']), edit the cage, "
         "form_create it, then delete the Form it replaces.")


def _component(design, raw):
    """(component, its occurrence or None at the root, error): `raw`'s, else the active one."""
    remedy = " - open its source document to build a Form there."
    if isinstance(raw, str) and raw.strip():
        occ, err = _COMPONENT.resolve(raw)
        if err:
            return None, None, err
        ref = _common.read_flag(lambda: occ.isReferencedComponent)
        if ref is not False:
            return None, None, f"'{raw}' " + ("places a component from another document" if ref
                                              else "did not read whether its component is local"
                                              ) + remedy
        comp = safe(lambda: occ.component)
    else:
        comp = _common.target_component(design)
        occ = safe(lambda: design.activeOccurrence)
    if comp is None:
        return None, None, f"'{raw}' has no component to build in."
    if safe(lambda: comp.parentDesign == design) is not True:
        return None, None, (f"'{safe(lambda: comp.name) or raw}' does not read as a component of "
                            "the active design" + remedy)
    return comp, occ, None


def _cage_in_units(primitive, cage):
    """(cage in the call's units, error) from exactly one of the two inputs."""
    if (primitive is None) == (cage is None):
        return None, ("Pass exactly one of 'primitive' (a box or cylinder) and 'cage' (vertices, "
                      "faces, creases) - got " + ("both." if primitive is not None else "neither."))
    name, given = ("primitive", primitive) if primitive is not None else ("cage", cage)
    keys = _SHAPE["properties"] if primitive is not None else _CAGE["properties"]
    if not isinstance(given, dict) or set(given) - set(keys):
        return None, (f"'{name}' is an object of {', '.join(keys)} - got "
                      f"{sorted(set(given) - set(keys)) if isinstance(given, dict) else given!r}.")
    if primitive is not None:
        return _tsm.primitive(primitive.get("shape"), primitive.get("size"),
                              primitive.get("spans"), primitive.get("capped", True))
    return {k: cage.get(k) or [] for k in ("vertices", "faces", "creases")}, None


def _edge_handle(edge, occ):
    """find_geometry's handle for one edge read through `occ`, or None when that proxy does not read."""
    # A Form's bodies are native, read in component space; find_geometry's handles and the
    # stale-token re-find both work on the occurrence proxy, in world space.
    if occ is not None:
        edge = safe(lambda: edge.createForAssemblyContext(occ))
        if edge is None:
            return None
    p = safe(lambda: edge.pointOnEdge)
    return _inputs.make_handle(edge, _inputs.edge_kind(safe(lambda: edge.geometry)),
                               safe(lambda: (p.x, p.y, p.z)))


def _verify(design, facts, text, cage_cm, closed, creased):
    """(problems, bodies, sharp [(edge, deg)], unread seams) - the checks a new Form must pass."""
    ff, problems = facts["ff"], []
    n_tb = _common.counted(lambda: ff.tSplineBodies.count)
    if n_tb != 1:
        problems.append(f"the Form holds {n_tb} T-spline bodies, not 1")
    bodies = list(_common.iter_collection(safe(lambda: ff.bodies)))
    if not bodies:
        problems.append("the Form holds no B-Rep body")
    solid = [_common.read_flag(lambda b=b: b.isSolid) for b in bodies]
    if any(s is not closed for s in solid):
        problems.append(f"isSolid reads {solid} for a {'closed' if closed else 'open'} cage")
    health, msg = _form_common.health_label(ff), safe(lambda: ff.errorOrWarningMessage)
    if health != "healthy" or msg:
        problems.append(f"the Form reads {health}" + (f": {msg}" if msg else ""))
    now = _common.counted(lambda: design.timeline.count)
    if facts["count_before"] is None or now != facts["count_before"] + 1:
        problems.append(f"the timeline reads {now} items, {facts['count_before']} before")
    if not isinstance(facts["readback"], str):
        problems.append("the T-spline body's TSM did not read back")
    else:
        problems += _tsm.compare(text, facts["readback"])
    box = _form_common.body_box_cm(bodies)
    lo = [min(p[i] for p in cage_cm["vertices"]) - BBOX_TOL_CM for i in range(3)]
    hi = [max(p[i] for p in cage_cm["vertices"]) + BBOX_TOL_CM for i in range(3)]
    if bodies and (box is None or any(box[0][i] < lo[i] or box[1][i] > hi[i] for i in range(3))):
        problems.append(f"the body's box {box} (cm) is not inside the cage's")
    seams = [row for b in bodies for row in _continuity.body_seams(b)]
    sharp = [(e, a) for e, a in seams if a is not None and a > SMOOTH_DEG]
    unread = sum(1 for _e, a in seams if a is None)
    if not creased and sharp:
        problems.append(f"{len(sharp)} seam(s) of an uncreased cage read up to "
                        f"{max(a for _e, a in sharp):.3f} deg, not smooth")
    if not creased and unread:
        problems.append(f"{unread} seam angle(s) did not read, so smoothness is unproven")
    return problems, bodies, sharp, unread


def _shape_facts(bodies, closed):
    """The measured result: B-Rep face count, volume or area, and the box corners in cm."""
    faces = [_common.counted(lambda b=b: b.faces.count) for b in bodies]
    key, attr = ("volume_cm3", "volume") if closed else ("area_cm2", "area")
    amounts = [_common.measured(lambda b=b: getattr(b, attr), 1.0, 9) for b in bodies]
    return {"brep_faces": sum(faces) if None not in faces else None,
            key: round(sum(amounts), 9) if None not in amounts else None,
            "box_cm": _form_common.body_box_cm(bodies)}


def handler(primitive=None, cage=None, origin=None, name: str = "", component: str = "",
            units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    k = _common.scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    cage_u, cerr = _cage_in_units(primitive, cage)
    if cerr:
        return error(cerr)
    at = [0.0, 0.0, 0.0] if origin is None else origin
    if not isinstance(at, (list, tuple)) or len(at) != 3 or any(
            _common.measured(lambda c=c: c) is None for c in at):
        return error(f"'origin' is three numbers [x, y, z] - got {origin!r}.")
    verr = _tsm.validate(cage_u)
    if not verr:
        cage_cm = _tsm.translated(cage_u, k, [c * k for c in at])
        verr = _tsm.validate(cage_cm, _tsm.MAX_REACH_CM)
        verr = verr and f"in cm, moved by 'origin', {verr}"
    if verr:
        return error(f"The cage was refused before Fusion saw it: {verr}")
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    good, mode_err = _PARAMETRIC_GUARD.check(design)
    if not good:
        return mode_err
    comp, occ, oerr = _component(design, component)
    if oerr:
        return error(oerr)
    text = _tsm.emit(cage_cm)
    census = _tsm.parse(text)[1]
    facts, ferr = _form_common.create_form(design, comp, text, name)
    if ferr:
        return error(ferr)
    creased = bool(cage_cm["creases"])
    problems, bodies, sharp, unread = _verify(design, facts, text, cage_cm, census["closed"],
                                              creased)
    ff, label = facts["ff"], facts["label"]
    if problems:
        left = _form_common.retire(design, ff, label, facts["count_before"])
        return error(f"'{label}' failed {len(problems)} read-back check(s): "
                     + "; ".join(problems[:3]) + "." + (left or " It was removed."))
    shape = _shape_facts(bodies, census["closed"])
    box = shape.pop("box_cm")
    extent = [round(box[1][i] - box[0][i], 9) for i in range(3)] if box else None
    out = {"created": True, "form": label,
           "timeline_index": safe(lambda: ff.timelineObject.index),
           "tspline_body": facts["tspline_body"],
           "result_bodies": [safe(lambda b=b: b.name) for b in bodies],
           "is_solid": census["closed"], **shape,
           "extent": [round(v / k, 6) for v in extent] if extent else None, "units": units or "mm",
           "cage": {key: census[key] for key in ("vertices", "faces", "closed", "stars",
                                                 "creases")},
           "readback": "exact",
           "sharp_edges": [{"edge": _edge_handle(e, occ), "angle_deg": round(a, 3)}
                           for e, a in sharp],
           "note": _NOTE}
    marker, count = facts["marker_before"], facts["count_before"]
    if isinstance(marker, int) and isinstance(count, int) and marker < count:
        out["inserted_before"] = safe(lambda: design.timeline.item(out["timeline_index"] + 1).name)
    drift = ([f"The B-Rep body reads '{out['result_bodies'][0]}', not '{facts['tspline_body']}'."]
             if out["result_bodies"][0] != facts["tspline_body"] else [])
    if facts["rename_warning"] or drift:
        out["rename_warning"] = " ".join([facts["rename_warning"] or ""] + drift).strip()
    if creased and unread:
        out["seams_unread"] = unread
    if creased and not sharp:
        out["note"] += (f" No two-faced B-Rep edge reads sharper than {SMOOTH_DEG} deg, though "
                        "the crease records read back exactly; find_geometry lists the edges.")
    record = {"cage_hash": _tsm.canonical_hash(cage_cm), "brep_faces": shape["brep_faces"],
              "volume_cm3": shape.get("volume_cm3"), "area_cm2": shape.get("area_cm2"),
              "extent_cm": extent, "box_cm": [list(c) for c in box] if box else None}
    if not _form_common.store_record(ff, record):
        out["record_stored"] = False
    return ok(out)


TOOL_DESCRIPTION = (
    "Create a T-spline Form's B-Rep body from a box [x,y,z] or 8-sided cylinder [diameter, height] "
    "primitive centred on origin, or a quad cage; form_get(include=['cage']) reads one back.\n"
    + _outputs.produces_block(RETURNS))

tool = (
    Tool.create_simple(name="form_create", description=TOOL_DESCRIPTION)
    .add_input_property("primitive", _SHAPE)
    .add_input_property("cage", _CAGE)
    .add_input_property("origin", {"type": "array", "items": {"type": "number"}})
    .add_input_property("name", {"type": "string"})
    .add_input_property(*_COMPONENT.as_property(brief=True))
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    postconditions=[_assert.FeatureHealthy(), _assert.SurfaceAreaAdded()],
    verification=Verification(
        kind="inline", rung="geometry",
        evidence_test="tests/unit/test_form_create.py::TestFormCreate"
                      "::test_a_repaired_readback_rolls_back"))


def register_tool():
    register(item)
