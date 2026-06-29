# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read-only MCP building block: walk the component/occurrence tree of the active design.

  design_get_tree -> the assembly structure under the root (or a named component),
                        to a bounded depth, flagging external references (X-refs) and
                        resolving each to its source document UID + name + URL.

This is the general "what's inside this assembly / container?" tool: walk any component to
see its child occurrences and bodies, and which children are external references you can open
with doc_open. (For example, a CAM setup that selects a container component as its
model/fixture — this lets you see what that container actually holds.)

Grounded in adsk.fusion:
  - Design.rootComponent.occurrences (top-level) / Occurrence.childOccurrences
  - Occurrence: .name, .isReferencedComponent, .bRepBodies.count, .component
  - Occurrence.documentReference.dataFile -> .id / .name / .fusionWebURL  (X-ref source)
Read-only; runs on the main thread.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common

app = adsk.core.Application.get()

# Bounds so a deep/wide assembly can't blow the main-thread budget or the response.
_DEFAULT_DEPTH = 3
_MAX_DEPTH = 8
_MAX_NODES = 2000


def handler(component: str = "", max_depth: int = _DEFAULT_DEPTH) -> dict:
    """Return the occurrence tree under the root, or under a named component."""
    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry).")

    try:
        depth = max(1, min(int(max_depth), _MAX_DEPTH))
    except Exception:
        depth = _DEFAULT_DEPTH

    root = design.rootComponent
    counter = {"n": 0, "truncated": False}

    want = (component or "").strip().lower()
    if want:
        # Find the first occurrence whose name (or its component's name) matches.
        start = _find_occurrence_by_name(root, want)
        if not start:
            return error(f"Component/occurrence not found: '{component}'.")
        tree = _walk_occurrence(start, 0, depth, counter)
        payload = {"root": component, "max_depth": depth,
    "truncated": counter["truncated"], "tree": tree}
    else:
        children = []
        try:
            for occ in root.occurrences:
                if counter["n"] >= _MAX_NODES:
                    counter["truncated"] = True
                    break
                children.append(_walk_occurrence(occ, 0, depth, counter))
        except Exception as e:
            return error(f"Could not read root occurrences: {e}")
        payload = {"root": root.name, "max_depth": depth,
    "node_count": counter["n"], "truncated": counter["truncated"],
    "children": children}

    return ok(payload)


def _find_occurrence_by_name(root, want_lower):
    """Depth-first search for an occurrence matching a name (bounded)."""
    stack = []
    try:
        stack = list(root.occurrences)
    except Exception:
        return None
    seen = 0
    while stack and seen < _MAX_NODES:
        occ = stack.pop()
        seen += 1
        nm = safe(lambda: occ.name, "")
        comp_nm = safe(lambda: occ.component.name, "")
        if want_lower in (nm or "").lower() or want_lower == (comp_nm or "").lower():
            return occ
        try:
            stack.extend(list(occ.childOccurrences))
        except Exception:
            pass
    return None


def _walk_occurrence(occ, depth, max_depth, counter):
    counter["n"] += 1
    if counter["n"] >= _MAX_NODES:
        counter["truncated"] = True

    node = {
    "name": safe(lambda: occ.name),
    # fullPathName is the UNAMBIGUOUS instance key (name is only locally unique — the same "Bolt:1"
    # recurs under every sub-assembly). Name-consuming tools (view_set_visibility, model_arrange,
    # measure_bbox, assembly_*, joint_*, ...) resolve by it via the OccurrenceRef kind; emit it so the
    # agent can author the key instead of a name that silently grabs the wrong instance.
    "full_path": safe(lambda: occ.fullPathName),
    "component": safe(lambda: occ.component.name),
    "is_reference": safe(lambda: occ.isReferencedComponent, False),
    "body_count": safe(lambda: occ.bRepBodies.count, 0),
    "child_count": safe(lambda: occ.childOccurrences.count, 0),
    }

    if node["is_reference"]:
        try:
            dr = occ.documentReference
            if dr:
                node["source_version"] = safe(lambda: dr.version)
                node["is_out_of_date"] = safe(lambda: dr.isOutOfDate)
                df = safe(lambda: dr.dataFile)
                if df:
                    node["source_id"] = safe(lambda: df.id)
                    node["source_name"] = safe(lambda: df.name)
                    node["source_url"] = safe(lambda: df.fusionWebURL)
        except Exception:
            pass

    # Recurse until depth limit or node cap.
    if depth + 1 < max_depth and node["child_count"] and counter["n"] < _MAX_NODES:
        kids = []
        try:
            for child in occ.childOccurrences:
                if counter["n"] >= _MAX_NODES:
                    counter["truncated"] = True
                    break
                kids.append(_walk_occurrence(child, depth + 1, max_depth, counter))
        except Exception:
            pass
        if kids:
            node["children"] = kids
    elif node["child_count"]:
        # Children exist but we stopped descending; signal it.
        node["children_truncated"] = True

    return node


TOOL_DESCRIPTION = (
    "Show the assembly structure (component/occurrence tree) of the active design, to a "
    "bounded depth. For each node: its name, its full_path (the UNAMBIGUOUS instance key — "
    "pass THIS, not the name, to tools that act on an occurrence), the component it "
    "instances, body count, and child count. Nodes that are EXTERNAL REFERENCES are flagged and resolved to their "
    "source document id (UID), name, and openable URL — so you can see what a Component "
    "Container actually holds and open referenced parts with doc_open. Pass "
    "'component' to start at a named component/occurrence (else the whole root), and "
    "'max_depth' (default 3, max 8)."
)

tool = (
    Tool.create_simple(name="design_get_tree", description=TOOL_DESCRIPTION)
    .add_input_property("component", {"type": "string",
            "description": "Optional component/occurrence name to start from."})
    .add_input_property("max_depth", {"type": "integer",
            "description": "How deep to walk (default 3, max 8)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
