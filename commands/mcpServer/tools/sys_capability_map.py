# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: a LIVE, factual index of the server's tool FAMILIES (the breadth map).

sys_capability_map() lists every tool family (by name prefix) with a one-line summary, its entry-point
tool, and tool count - read live from the registry so it can't drift. Pair with sys_find_tool to search
within a family. Read-only, no adsk.*.
"""

from ._common import ok
from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register, get_tools, family_of, has_tool, GATED_TOOLS

# Per-family FACTS: a one-line factual summary + the entry-point tool (the tool that starts that
# family's workflow - a fact about the family, not advice). Families not listed here still appear,
# derived from the registry, so a new family can't be silently omitted.
_FAMILY = {
    "sketch":    ("2D sketching: create sketches and add/constrain/dimension geometry.", "sketch_create"),
    "model":     ("Solid feature modeling: extrude/revolve/fillet/hole/pattern/combine + inspect/measure.", "model_extrude"),
    "surface":   ("Open (non-solid) surface modeling: extrude/revolve/patch/trim/thicken.", "surface_extrude"),
    "mesh":      ("Mesh bodies (STL/OBJ/3MF): import, edit, reduce/remesh, convert to BRep.", "mesh_insert"),
    "assembly":  ("Assembly kinematics: joints, grounding, move/capture, interference, probe.", "assembly_get"),
    "joint":     ("Joints between components: create/edit/drive joints and joint origins.", "joint_create"),
    "cam":       ("Manufacture (CAM): setups, operations, templates, tool libraries, generate toolpaths.", "cam_create_setup"),
    "data":      ("Cloud data model: hubs, projects, folders, files (create/list/upload/delete).", "data_get"),
    "doc":       ("Document lifecycle: open/new/save/close/activate/copy + insert/update references.", "doc_get"),
    "design":    ("The active design as a whole: read structure, mode, recompute, configure, delete.", "design_get"),
    "view":      ("Viewport/camera: screenshots, sections, isolate/orient, workspace switch.", "view_screenshot"),
    "param":     ("User/model parameters: add/get/set/delete + favorites.", "param_get"),
    "sys":       ("Server/session utilities: find tools, this map, API docs, selection, the script hatch.", "sys_find_tool"),
    "find":      ("Find existing geometry (faces/edges/...) as stable handles for other tools.", "find_geometry"),
    "appearance":("Body/occurrence/face appearance (color) override.", "appearance_set"),
    "pmi":       ("PMI / 3D annotations on model geometry: leader notes with GD&T symbols, hole/thread callouts, imported PMI.", "pmi_get"),
    "workspace": ("Cold-boot orientation: where you are + what's here + pointers to the right deep tool.", "workspace_orient"),
    "save":      ("Tessellate a BRep body into a persistent mesh body in the design.", "save_as_mesh"),
}


# family-of-name is shared with the registry's family-gating helper (mcp_primitives/registry.py) -
# one definition, so this map and the gating checkboxes can never disagree on what a "family" is.
_family_of = family_of


def handler() -> dict:
    """See TOOL_DESCRIPTION."""
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

    # GATED tools: registered-but-disabled unless a specific settings checkbox allows them, derived
    # from GATED_TOOLS (the same map entry.py's sweep acts on - see its _GATED_TOOL_MODULES) rather
    # than a hand-maintained list here, so this can't drift stale. 'enabled_now' is a live registry
    # check (has_tool), not a settings-file read, so it never needs a second source of truth either.
    gated_tools = [
        {"tool": name, "enabled_now": has_tool(name),
         "enable_path": f"Fusion Essentials Settings command -> MCP Server tab -> '{label}' checkbox"}
        for name, label in sorted(GATED_TOOLS.items())
    ]

    return ok({
        "family_count": len(out),
        "tool_count": sum(f["tool_count"] for f in out),
        "families": out,
        "gated": {
            "tools": gated_tools,
            # The rows here are the ones that CAN be server-disabled, so the client-deny diagnosis
            # holds only for a row reading enabled_now true; for a false one the server is the
            # answer, and its own enable_path is beside it.
            "note": ("A gated tool with enabled_now false is disabled ON THIS SERVER - the client "
                     "cannot see it until the checkbox at its enable_path is ticked. A tool this "
                     "map names as present (enabled_now true, or any tool in 'families') that the "
                     "client reports as 'No such tool available' is hidden by CLIENT permission "
                     "config (a deny rule), not missing from the server - check the client's "
                     "permissions."),
        },
        "note": ("The BREADTH map (what families exist + each one's entry tool). To go deeper, search "
                 "within a family with sys_find_tool (e.g. sys_find_tool('surface')). Facts about the "
                 "registry, not a recommended order."),
    })


TOOL_DESCRIPTION = (
    "GETTING STARTED / overview / start here / help: LIST every tool FAMILY this server has - each with a "
    "one-line summary, its entry-point tool, and tool count. The BREADTH map: answers 'what CAN this "
    "server do?' for a cold agent that doesn't yet know which capabilities exist (surface? mesh? "
    "config?). Then call workspace_orient for the active document's state. Read live from the "
    "running server - never stale. Pair with sys_find_tool to search WITHIN a family. Factual "
    "index - no workflow advice. Takes no arguments."
)

tool = Tool.create_simple(name="sys_capability_map", description=TOOL_DESCRIPTION).strict_schema()
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=False)


def register_tool():
    register(item)
