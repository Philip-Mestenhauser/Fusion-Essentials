# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: insert a saved cloud document into the active design as an occurrence.

  insert_occurrence -> insert a saved cloud document (by lineage URN / web URL) into the active
                       design as a new component OCCURRENCE — as an external reference (default)
                       or embedded — under the root or a named component. Optionally remove a
                       named existing occurrence first. WRITES to the design.

General-purpose: this is the API equivalent of Insert > Insert Derive / Insert into Current
Design. It says nothing about WHY you are inserting — it just creates the occurrence. Composing
inserts into a fixture assembly, swapping a model in a CAM template, or laying out a kit is the
job of a skill that calls this, not of this tool.

The new occurrence is placed at the identity transform; position it afterward with a joint
(see the `joint` block) or by editing its transform.

KEY CONSTRAINT: inserting as an EXTERNAL REFERENCE (`as_reference=true`, the default) requires
the inserted document to be in the SAME PROJECT as the host document — Fusion's
Occurrences.addByInsert enforces this. Embedding (`as_reference=false`) has no such constraint.

Grounded in adsk.core / adsk.fusion:
  - app.data.findFileById(urn) -> DataFile          (accepts a web URL too; the URN is decoded)
  - Component.occurrences (Occurrences).addByInsert(dataFile, Matrix3D, isReferencedComponent)
  - adsk.core.Matrix3D.create()  (identity transform)
  - Occurrence.deleteMe()  (removes an occurrence + its joints)
Handler runs on the main thread; WRITES to the design.
"""

import base64
import json
import re

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        try:
            design = adsk.fusion.Design.cast(
                app.activeDocument.products.itemByProductType('DesignProductType'))
        except Exception:
            design = None
    return design


# --- identifier resolution (URN, or a Fusion web URL whose URN is base64url-embedded) ---

def _b64url_decode(seg):
    s = seg.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    try:
        return base64.b64decode(s).decode('utf-8', 'strict')
    except Exception:
        return None


def _resolve_data_file(raw):
    raw = (raw or "").strip()
    candidates = [raw]
    if '://' in raw or raw.lower().startswith('http'):
        for seg in re.split(r'[/?#&=]+', raw):
            if len(seg) >= 16:
                dec = _b64url_decode(seg)
                if dec and dec.startswith('urn:adsk'):
                    candidates.append(dec)
    for m in re.findall(r'urn:adsk[\w\.\:\-]+', raw):
        candidates.append(m)
    for c in candidates:
        df = _safe(lambda c=c: app.data.findFileById(c))
        if df:
            return df, c
    return None, None


# --- target component / occupant resolution ---

def _find_component(design, name):
    """Find a Component by name (root if empty/root, or any occurrence's component)."""
    name = (name or "").strip()
    root = design.rootComponent
    if not name or name == _safe(lambda: root.name):
        return root, "root component"
    try:
        for occ in root.allOccurrences:
            comp = _safe(lambda occ=occ: occ.component)
            if comp is None:
                continue
            if (_safe(lambda occ=occ: occ.name) or "") == name or \
               (_safe(lambda comp=comp: comp.name) or "") == name:
                return comp, f"component '{_safe(lambda comp=comp: comp.name)}'"
    except Exception:
        pass
    return None, None


def _find_child_occurrence(component, occ_name):
    """Find a direct child occurrence of `component` by name (or bare component name)."""
    occ_name = (occ_name or "").strip()
    try:
        for o in component.occurrences:
            if (_safe(lambda o=o: o.name) or "") == occ_name or \
               (_safe(lambda o=o: o.component.name) or "") == occ_name:
                return o
    except Exception:
        pass
    return None


def handler(document_id: str = "", into_component: str = "", as_reference: bool = True,
            remove_existing: str = "") -> dict:
    """Insert a saved cloud document into the active design as an occurrence.

    document_id: lineage URN (or web URL) of the saved cloud document to insert. into_component:
    the component to insert into (default: the root component). as_reference: insert as an
    external reference (default true; requires same project) or embedded (false). remove_existing:
    optional name of an existing child occurrence to delete first (its joints go with it).
    WRITES to the design.
    """
    raw = (document_id or "").strip()
    if not raw:
        return _error("Provide 'document_id' — the lineage URN (or web URL) of the saved cloud "
                      "document to insert.")

    design = _design()
    if not design:
        return _error("No active design. Open the host document first.")

    data_file, resolved = _resolve_data_file(raw)
    if not data_file:
        return _error(f"Could not resolve '{raw}' to a saved document. Pass a lineage URN or web "
                      "URL (from list_project_files). The document must be SAVED to the cloud.")

    comp, comp_desc = _find_component(design, into_component)
    if not comp:
        return _error(f"Component '{into_component}' not found. Use get_component_tree to list "
                      "components, or omit 'into_component' to insert under the root.")

    # Optionally remove a named existing occurrence first (its joints are removed with it).
    removed = None
    if (remove_existing or "").strip():
        existing = _find_child_occurrence(comp, remove_existing)
        if not existing:
            return _error(f"No child occurrence named '{remove_existing}' in {comp_desc}. Check "
                          "the name with get_component_tree (start at that component).")
        removed = _safe(lambda: existing.name)
        ok = _safe(lambda: existing.deleteMe(), False)
        if not ok:
            return _error(f"Failed to remove existing occurrence '{removed}' (deleteMe returned "
                          "false). It may be referenced/locked.")

    # Insert at the identity transform (position afterward with a joint or transform edit).
    try:
        transform = adsk.core.Matrix3D.create()
        new_occ = comp.occurrences.addByInsert(data_file, transform, bool(as_reference))
    except Exception as e:
        hint = ("(Inserting as an external reference requires the document to be in the SAME "
                "PROJECT as the host design — save it into this project first, or pass "
                "as_reference=false to embed it.)") if as_reference else ""
        return _error(f"Insert failed: {e}. {hint}")
    if not new_occ:
        return _error("addByInsert returned nothing (the insert did not produce an occurrence).")

    return _ok({
        "inserted": True,
        "document_name": _safe(lambda: data_file.name),
        "document_id": resolved,
        "into_component": comp_desc,
        "new_occurrence_name": _safe(lambda: new_occ.name),
        "is_reference": _safe(lambda: new_occ.isReferencedComponent),
        "removed_occurrence": removed,
        "note": ("Inserted at the identity transform. Position it with a joint (see the `joint` "
                 "block) or by editing its transform. If an occurrence was removed, its joints "
                 "went with it."),
    })


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


TOOL_DESCRIPTION = (
    "Insert a SAVED cloud document into the active design as a new component occurrence — the API "
    "equivalent of Insert into Current Design. 'document_id' is the lineage URN (or web URL) of "
    "the document to insert. 'into_component' is the component to insert into (default: the root "
    "component). 'as_reference' inserts it as an external reference (default true — requires the "
    "document to be in the SAME PROJECT as the host) or embedded (false). Optional "
    "'remove_existing' = the name of an existing child occurrence to delete first (its joints go "
    "with it). The new occurrence is placed at the identity transform — position it afterward "
    "with the `joint` block or a transform edit. WRITES to the design. Generic: this just creates "
    "the occurrence; how you use it (fixtures, template model swap, layouts) is up to you."
)

tool = (
    Tool.create_with_string_input(
        name="insert_occurrence",
        description=TOOL_DESCRIPTION,
        input_param_name="document_id",
        input_param_description="Lineage URN (or web URL) of the saved cloud document to insert.",
    )
    .add_input_property("into_component", {"type": "string",
                                           "description": "Component to insert into (default: root component)."})
    .add_input_property("as_reference", {"type": "boolean",
                                         "description": "Insert as external reference (default true; requires same project) or embedded (false)."})
    .add_input_property("remove_existing", {"type": "string",
                                            "description": "Optional name of an existing child occurrence to delete first."})
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
