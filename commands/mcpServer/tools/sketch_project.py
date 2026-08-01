# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: project (include) existing model geometry into a sketch - Fusion's Project
command. WRITES. Uses Sketch.project2(entities, isLinked): isLinked=true keeps the created sketch
curves parametrically linked to the source geometry (they update when it moves); false makes an
independent static copy.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, all_sketch_names
from . import _common
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

# The geometry to project: find_geometry handles at edges/faces/vertices (a face projects all its
# edges). require="any" so faces, edges, and vertices are all accepted; project2 rejects anything it
# can't project and that surfaces as the mutation error.
_ENTITIES = _inputs.GeometryHandleList("entities", require="any", required=True,
    description="find_geometry handle(s) to project into the sketch (edges/faces/vertices; a face "
                "projects all of its edges).")

# The sketch-entity ref kinds sketch_constrain / sketch_dimension can address ('<type>:<index>') -
# _common.ENTITY_REF_KINDS is the single source of truth; defer to it instead of a local copy that can
# drift (a projected elliptical arc / conic curve is still created but not addressable this way).
_ADDRESSABLE = _common.ENTITY_REF_KINDS

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsValue("entity_refs",
        "the created sketch-entity refs ('line:0','circle:1',...) to constrain/dimension",
        consumers=["sketch_constrain", "sketch_dimension"]),
]


def _addressable_counts(sketch) -> dict:
    """Per-collection counts for the ref-addressable kinds, in creation order (matches
    _common.resolve_entity_ref's indexing so the reported refs resolve back to these entities)."""
    counts = {}
    for kind in _ADDRESSABLE:
        coll = _common.entity_collection(sketch, kind)
        counts[kind] = safe(lambda coll=coll: coll.count, 0) if coll is not None else 0
    return counts


def _new_refs(before: dict, after: dict) -> list:
    """The '<type>:<index>' refs for entities that appeared in each addressable collection - the tail
    range [before, after) each holds the newly-projected entities of that kind."""
    refs = []
    for kind in _ADDRESSABLE:
        for i in range(before.get(kind, 0), after.get(kind, 0)):
            refs.append(f"{kind}:{i}")
    return refs


def handler(entities="", sketch_name: str = "", link: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    sk, requested = _common.resolve_or_recent_sketch(design, sketch_name)
    if not sk:
        if (sketch_name or "").strip():
            names = all_sketch_names(design)
            return error(f"No sketch named '{sketch_name}'. Available: "
                         + (", ".join(n for n in names if n) or "(none)")
                         + ". Create one with sketch_create.")
        return error("No sketch to project into. Create one first with sketch_create.")

    ents, eerr = _ENTITIES.resolve(entities)
    if eerr:
        return error(eerr)

    before = _addressable_counts(sk)
    try:
        # project2(entities, isLinked): the modern Project (project is deprecated and has no link
        # control). Pass the resolved live entities as a list.
        created = sk.project2(ents, bool(link))
    except Exception as e:
        return error(f"Projection failed in sketch '{safe(lambda: sk.name)}': {e}")

    # Honesty read-back: a projection that adds nothing is a failure, not a false ok. Trust the count
    # of entities the API says it created, then confirm the collections actually grew.
    created_count = len(created) if created is not None else 0
    after = _addressable_counts(sk)
    addressable_delta = sum(after[k] - before[k] for k in _ADDRESSABLE)
    if created_count <= 0 and addressable_delta <= 0:
        return error(
            f"Projection created no sketch entities in '{safe(lambda: sk.name)}'. The geometry may "
            "already be projected, or lies out of the sketch plane's projectable set. Nothing was added.")

    refs = _new_refs(before, after)
    note = ("Geometry projected. 'entity_refs' are '<type>:<index>' handles for sketch_constrain / "
            f"sketch_dimension ({'/'.join(_ADDRESSABLE)}). "
            + ("Linked: the curves update when the source geometry moves."
               if link else "Static copy: the curves do NOT track the source geometry.")
            + " Extrude a resulting profile via sketch_get -> model_extrude.")
    if created_count > len(refs):
        note += (" Some projected curves are non-addressable types (elliptical arc/conic curve) - use "
                 "sketch_get(include_entities=true) to inspect them.")

    return ok({
        "projected": True,
        "sketch": safe(lambda: sk.name),
        "linked": bool(link),
        "created_count": created_count,
        "entity_refs": refs,
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Project existing model geometry into a sketch - Fusion's Project command - creating sketch "
    "curves/points from edges, faces (all of their edges), or vertices. 'entities' are find_geometry "
    "handles; 'sketch_name' is the target sketch (omit = most recent). 'link' = true keeps the "
    "projected curves parametrically LINKED to the source (they update when it moves); false makes an "
    "independent static copy. Reports the created '<type>:<index>' entity refs so you can "
    "immediately sketch_constrain / sketch_dimension them, or extrude a resulting profile.\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="sketch_project", description=TOOL_DESCRIPTION)
    .add_input_property("entities", _ENTITIES.schema())
    .add_required_input("entities")
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch to project INTO (omit = most recently created sketch)."})
    .add_input_property("link", {"type": "boolean",
            "description": "Keep the projected curves parametrically linked to the source geometry (default true); false = static copy."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
