# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block that DELETES one PMI annotation by name. DESTRUCTIVE. deleteMe()'s bool is
gated and the name is re-resolved afterwards - a decline or a survivor is an error, never a false
ok."""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _pmi

app = adsk.core.Application.get()


def handler(annotation="", component="") -> dict:
    """See TOOL_DESCRIPTION."""
    d = _common.design()
    if not d:
        return error("No active design. Create or open a document first (see doc_new).")
    ann, comp, ferr = _pmi.find_annotation(d, annotation, component)
    if ferr:
        return error(ferr)
    name = safe(lambda: ann.name)
    kind = _pmi.kind_of(ann)
    comp_name = safe(lambda: comp.name)
    if not safe(lambda: ann.isDeletable, True):
        return error(f"'{name}' ({kind}) reports isDeletable=false - the platform refuses to "
                     "delete it (e.g. PMI owned by an imported folder). Nothing was changed.")
    try:
        deleted = bool(ann.deleteMe())
    except Exception as e:
        return error(f"deleteMe() failed: {e}")
    if not deleted:
        return error(f"deleteMe() declined for '{name}' ({kind}) - the annotation was NOT deleted.")
    survivor, _c, _miss = _pmi.find_annotation(d, name, comp_name or "")
    if survivor is not None or bool(safe(lambda: ann.isValid, False)):
        return error(f"deleteMe() reported success but '{name}' still resolves in "
                     f"'{comp_name}' - treat the delete as failed.")
    remaining = sum(1 for _ in _pmi.walk_annotations(d))
    return ok({
        "deleted": name,
        "kind": kind,
        "component": comp_name,
        "remaining_pmi": remaining,
    })


TOOL_DESCRIPTION = (
"Delete ONE PMI annotation by its name from pmi_get (component= disambiguates a name that exists "
"in several components). The deletion is verified: deleteMe()'s decline is reported as an error, "
"and the name is re-resolved afterwards to confirm the annotation is gone. Imported PMI deletes "
"remove that imported record permanently for this design."
)

tool = (
    Tool.create_simple(name="pmi_delete", description=TOOL_DESCRIPTION)
    .add_input_property("annotation", {"type": "string",
        "description": "The PMI's name (from pmi_get). Exact match, case-insensitive."})
    .add_input_property("component", {"type": "string",
        "description": "Component to look in - required only when the name exists in several."})
    .add_required_input("annotation")
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
