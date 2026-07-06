# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Refresh the ACTIVE drawing's out-of-date references to the latest saved source design (the API
equivalent of the Refresh button). Staleness is judged per reference - DocumentReference.isOutOfDate
is the real signal (DrawingDocument.isUpToDate reports True even while a reference is stale) - and a
refresh that leaves a reference stale is reported as a failure. The refresh dirties the drawing but
does NOT save it: doc_save afterward, then drawing_export. WRITES (in-session state).
"""

import adsk.core
import adsk.drawing

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _assert
from . import _outputs

app = adsk.core.Application.get()

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsValue("is_up_to_date", "whether the drawing's references are current after the refresh"),
]


def _active_drawing_doc():
    """The active document cast to a DrawingDocument, or None (active doc is not a drawing)."""
    doc = safe(lambda: app.activeDocument)
    return safe(lambda: adsk.drawing.DrawingDocument.cast(doc))


def _reference_state(dd):
    """Walk documentReferences -> (stale_count, [{index, is_out_of_date, version}]). Per-reference
    isOutOfDate is the RELIABLE staleness signal (DrawingDocument.isUpToDate reports True even while a
    reference is stale, so it is deliberately not consulted)."""
    refs = safe(lambda: dd.documentReferences)
    if refs is None:
        return None, []
    out, stale = [], 0
    count = safe(lambda: refs.count, 0) or 0
    for i in range(count):
        r = safe(lambda k=i: refs.item(k))
        if r is None:
            continue
        ood = safe(lambda rr=r: rr.isOutOfDate)
        ver = safe(lambda rr=r: rr.version)
        if ood:
            stale += 1
        out.append({"index": i, "is_out_of_date": bool(ood), "version": ver})
    return stale, out


def handler() -> dict:
    """Refresh the active drawing's out-of-date references, verifying the stale state actually clears."""
    dd = _active_drawing_doc()
    if dd is None or safe(lambda: dd.drawing) is None:
        return error("No drawing to update: the active document is not a drawing. Open the drawing "
                     "(doc_open a reviewed drawing, or open it in the Fusion UI) and make it active, "
                     "then retry.")

    stale_before, refs_before = _reference_state(dd)
    if stale_before is None:
        return error("The drawing's document references could not be read, so its staleness cannot be "
                     "determined - refusing to refresh blind.")
    if stale_before == 0:
        return ok({
            "updated": False,
            "stale_references_before": 0,
            "is_up_to_date": True,
            "references": refs_before,
            "note": "Drawing references are already up to date - nothing to refresh. Edit and SAVE the "
                    "source design first, then this refreshes the drawing's views to match.",
        })

    try:
        call_result = dd.updateAllReferences()
    except Exception as ex:
        return error(f"updateAllReferences failed: {ex}")

    # updateAllReferences returning true is not on its own proof the stale state cleared - the
    # ReferencesFresh postcondition on this tool's Item re-walks the references and fails the call if
    # any isOutOfDate survived. The re-read here only feeds the payload's 'references' evidence.
    _stale_after, refs_after = _reference_state(dd)

    return ok({
        "updated": True,
        "stale_references_before": stale_before,
        "is_up_to_date": True,
        "references": refs_after,
        "update_call_result": bool(call_result),
        "note": ("Refreshed the drawing's out-of-date references to the latest source design (views "
                 "regenerated; each reference's 'version' now reflects what the views show). The drawing "
                 "is modified in-session but NOT saved - call doc_save to persist a new version, then "
                 "drawing_export for the PDF."),
    })


TOOL_DESCRIPTION = (
    "Refresh the ACTIVE 2D DRAWING's out-of-date references to the latest source design - the API "
    "equivalent of the 'Refresh' button, regenerating the drawing's views after the source design was "
    "edited and SAVED. Use it to close the round-trip: edit the component, doc_save the design, then "
    "drawing_update to bring the drawing's views current. Operates on whichever drawing is the active "
    "document (open a reviewed drawing and make it active first). Gated on the drawing's up-to-date "
    "state before and after - a refresh that does not clear the out-of-date flag is returned as an "
    "error, never a false ok. If the drawing is already up to date, it reports that and does nothing. "
    "The refresh dirties the drawing but does NOT save it - call doc_save afterward to persist a new "
    "version, then drawing_export for the PDF. WRITES (mutates the drawing's references/views)."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

tool = (
    Tool.create_simple(name="drawing_update", description=FULL_DESCRIPTION)
    .strict_schema()
)

# enforce_timeout=False: updateAllReferences regenerates views against the latest source and is a
# blocking, uninterruptible main-thread call that can run past the server's call timeout for a large
# drawing. The reference read-back is the real proof of success, so we wait for it rather than
# false-failing on a timeout.
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             enforce_timeout=False,
                             postconditions=[_assert.ReferencesFresh()])


def register_tool():
    register(item)
