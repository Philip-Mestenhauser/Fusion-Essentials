# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: a LIVE, factual index of the server's tool FAMILIES (the breadth map).

  sys_capability_map() -> every tool family (by name prefix), each with a one-line factual summary, its
                          entry-point tool, and how many tools it has. The BREADTH counterpart to
                          sys_find_tool's DEPTH: this answers "what CAN this server do?" so a cold agent
                          that doesn't yet know Fusion has surface/mesh/config tooling can SEE the map,
                          then sys_find_tool to search the branch it needs.

Why this exists: sys_find_tool needs a query, which needs already suspecting the capability exists. With
no "show me everything" tool, a cold agent is blind at breadth. This is a FACTUAL registry index - not
advice. Families/entry-points/counts are facts about what's registered; there is NO methodology, no
"you should", no workflow order. LIVE from the registry every call (like sys_find_tool), so it can't
drift as tools are added or removed.

Read-only, no adsk.* - pure registry introspection. Safe anytime.
"""

from ._common import ok
from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register, get_tools

# Per-family FACTS: a one-line factual summary + the entry-point tool (the tool that starts that
# family's workflow - a fact about the family, not advice). Families not listed here still appear,
# derived from the registry, so a new family can't be silently omitted.
_FAMILY = {
    "sketch":    ("2D sketching: create sketches and add/constrain/dimension geometry.", "sketch_create"),
    "model":     ("Solid feature modeling: extrude/revolve/fillet/hole/pattern/combine + inspect/measure.", "model_extrude"),
    "surface":   ("Open (non-solid) surface modeling: extrude/revolve/patch/trim/thicken.", "surface_extrude"),
    "mesh":      ("Mesh bodies (STL/OBJ/3MF): import, edit, reduce/remesh, convert to BRep.", "mesh_insert"),
    "assembly":  ("Assembly kinematics: joints, grounding, move/capture, interference, probe.", "assembly_probe"),
    "joint":     ("Joints between components: create/edit/drive joints and joint origins.", "joint_create"),
    "cam":       ("Manufacture (CAM): setups, operations, templates, tool libraries, generate, post.", "cam_create_setup"),
    "data":      ("Cloud data model: hubs, projects, folders, files (create/list/upload/delete).", "data_get"),
    "doc":       ("Document lifecycle: open/new/save/close/activate/copy + insert/update references.", "doc_get"),
    "design":    ("The active design as a whole: read structure, mode, recompute, configure, delete.", "design_get"),
    "view":      ("Viewport/camera: screenshots, sections, isolate/orient, workspace switch.", "view_screenshot"),
    "param":     ("User/model parameters: add/get/set/delete + favorites.", "param_get"),
    "sys":       ("Server/session utilities: find tools, this map, API docs, selection, the script hatch.", "sys_find_tool"),
    "find":      ("Find existing geometry (faces/edges/...) as stable handles for other tools.", "find_geometry"),
    "appearance":("Body/occurrence/face appearance (color) override.", "appearance_set"),
    "workspace": ("Cold-boot orientation: where you are + what's here + pointers to the right deep tool.", "workspace_orient"),
    "save":      ("Tessellate a BRep body into a persistent mesh body in the design.", "save_as_mesh"),
}


def _family_of(name):
    """The family of a tool = its first underscore segment (cam_get -> 'cam'). data_get/doc_get etc.
    group correctly; single-word tools (find_geometry) use the whole first segment."""
    return (name or "").split("_", 1)[0]


def handler() -> dict:
    """Read the LIVE family index: every tool family, its factual summary, entry-point tool, and tool
    count. Breadth map - pair with sys_find_tool to search within a family. Read-only, no args."""
    families = {}
    for item in get_tools():
        prim = getattr(item, "primitive", None)
        name = getattr(prim, "name", None)
        if not name:
            continue
        fam = _family_of(name)
        families.setdefault(fam, []).append(name)

    out = []
    for fam in sorted(families):
        members = sorted(families[fam])
        summary, entry = _FAMILY.get(fam, (None, None))
        # Fallbacks keep an UNMAPPED family honest: entry = a *_get/*_create if present, else the first
        # member; summary states only the fact that it's a family of N tools.
        if entry not in members:
            entry = next((m for m in members if m.endswith("_get") or "_create" in m), members[0])
        rec = {
            "family": fam,
            "summary": summary or f"{fam} tools.",
            "entry_tool": entry,
            "tool_count": len(members),
        }
        out.append(rec)

    return ok({
        "family_count": len(out),
        "tool_count": sum(f["tool_count"] for f in out),
        "families": out,
        "note": ("The BREADTH map (what families exist + each one's entry tool). To go deeper, search "
                 "within a family with sys_find_tool (e.g. sys_find_tool('surface')). Facts about the "
                 "registry, not a recommended order."),
    })


TOOL_DESCRIPTION = (
    "GETTING STARTED / overview / start here / help: LIST every tool FAMILY this server has - each with a "
    "one-line summary, its entry-point tool, and tool count. The BREADTH map: answers 'what CAN this "
    "server do?' for a cold agent that doesn't yet know which capabilities exist (surface? mesh? "
    "config?). Then call workspace_orient for the active document's state. LIVE from the registry "
    "(never stale). Pair with sys_find_tool to search WITHIN a family. Factual index - no workflow "
    "advice. Read-only, no args."
)

tool = Tool.create_simple(name="sys_capability_map", description=TOOL_DESCRIPTION).strict_schema()
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=False)


def register_tool():
    register(item)
