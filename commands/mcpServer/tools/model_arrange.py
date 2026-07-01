# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: ARRANGE (nest/pack) component occurrences within a sketch-profile boundary.

  arrange -> create an Arrange feature that packs the given component occurrences inside a 2D
             envelope defined by a sketch profile (your boundary shape). True-shape nesting fits the
             actual outlines; rectangular nests bounding boxes. WRITES.

This is the API equivalent of the Manufacture/Design "Arrange" command (nesting). Give it a boundary
(a named sketch whose profile is the envelope) and the occurrences to lay out; Fusion solves the
placement. General-purpose - it just nests whatever occurrences you pass.

NOTE: the advanced true-shape nesting can require a Fusion extension on some accounts; this catches
the gate and reports it instead of failing opaquely (rectangular is the lighter fallback).

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Component.features.arrangeFeatures.createInput(ArrangeSolverTypes.*) -> ArrangeFeatureInput
  - input.setProfileOrFaceEnvelope([profile|planarFace,...]) -> Arrange2DProfileOrFaceEnvelopeInput
      (.objectSpacing = clearance between parts, cm)
  - input.arrangeComponents.add(occurrence) per shape ; arrangeFeatures.add(input) -> ArrangeFeature
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, resolve_sketch
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_SOLVERS = {
"true_shape": "Arrange2DTrueShapeSolverType",
"trueshape": "Arrange2DTrueShapeSolverType",
"true": "Arrange2DTrueShapeSolverType",
"rectangular": "Arrange2DRectangularSolverType",
"rect": "Arrange2DRectangularSolverType",
}


def _find_sketch(design, name):
    # Whole-design resolve (active component first) so the boundary sketch can live in an activated
    # sub-component, not only the root component.
    return resolve_sketch(design, (name or "").strip())


def _find_occurrences(design, shapes):
    """Resolve a comma-string (or list) of occurrence names/fullPathNames via the shared OccurrenceRef
    logic (fullPathName-preferring, ambiguity-refusing - no silent wrong-instance grab).
    Returns (occurrences, resolved_names, errors)."""
    if isinstance(shapes, str):
        wanted = [s.strip() for s in shapes.split(",") if s.strip()]
    else:
        wanted = [str(s).strip() for s in (shapes or []) if str(s).strip()]
    found, resolved, errors = [], [], []
    for want in wanted:
        occ, err = _inputs._resolve_occurrence(want, want)
        if occ is not None:
            found.append(occ)
            resolved.append(safe(lambda o=occ: o.name))
        else:
            errors.append(err)
    return found, resolved, errors


def handler(boundary_sketch: str = "", shapes: str = "", solver: str = "true_shape",
            spacing: float = 0.0, units: str = "mm") -> dict:
    """Arrange (nest) component occurrences within a sketch-profile boundary.

    boundary_sketch: name of the sketch whose (first) profile defines the envelope to pack into.
    shapes: occurrence name(s) to arrange (comma-separated). solver: true_shape (fit actual
    outlines) | rectangular (fit bounding boxes). spacing: minimum clearance between parts (in
    'units'). WRITES an Arrange feature.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    solver_key = (solver or "true_shape").strip().lower()
    if solver_key not in _SOLVERS:
        return error("Unknown solver '%s'. Use 'true_shape' or 'rectangular'." % solver)

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    sketch = _find_sketch(design, boundary_sketch)
    if not sketch:
        return error(f"No sketch named '{boundary_sketch}' for the boundary. Use sketch_get.")
    profiles = safe(lambda: sketch.profiles)
    if not profiles or safe(lambda: profiles.count, 0) == 0:
        return error(f"Boundary sketch '{boundary_sketch}' has no closed profile to use as the "
    "envelope. Draw a closed boundary shape first.")
    envelope_profile = profiles.item(0)

    if not (shapes or "").strip() if isinstance(shapes, str) else not shapes:
        return error("Provide 'shapes' - the occurrence name(s) to arrange (comma-separated).")
    occs, resolved, errors = _find_occurrences(design, shapes)
    if errors:
        return error("; ".join(errors))
    if not occs:
        return error("Provide 'shapes' - at least one occurrence to arrange.")

    af = safe(lambda: design.rootComponent.features.arrangeFeatures)
    if af is None:
        return error("This design does not expose Arrange features.")

    try:
        ST = adsk.fusion.ArrangeSolverTypes
        inp = af.createInput(getattr(ST, _SOLVERS[solver_key]))
        if not inp:
            return error("Could not create the arrange input (solver may be unavailable).")
        env = inp.setProfileOrFaceEnvelope([envelope_profile])
        if env is None:
            return error("Could not set the boundary envelope from the sketch profile.")
        if spacing:
            safe(lambda: setattr(env, "objectSpacing",
                                  adsk.core.ValueInput.createByReal(float(spacing) * k)))
        for o in occs:
            inp.arrangeComponents.add(o)
        feature = af.add(inp)
    except Exception as e:
        msg = str(e)
        if any(t in msg.lower() for t in ("extension", "entitle", "license", "subscrib")):
            return error(f"Arrange ({solver_key}) appears to need a Fusion extension on this "
                          f"account: {msg}. Try solver='rectangular', or enable the extension.")
        return error(f"Arrange failed: {msg}")
    if not feature:
        return error("Arrange returned no feature.")

    return ok({
        "arranged": True,
        "feature": safe(lambda: feature.name),
        "solver": "true_shape" if "TrueShape" in _SOLVERS[solver_key] else "rectangular",
        "boundary_sketch": safe(lambda: sketch.name),
        "arranged_count": len(resolved),
        "shapes": resolved,
        "spacing": round(float(spacing), 6) if spacing else 0.0,
        "units": units,
        "note": "Shapes arranged within the boundary. Pair with view_screenshot (top) to view the nest.",
    })


TOOL_DESCRIPTION = (
"ARRANGE (nest/pack) component occurrences within a 2D boundary defined by a sketch profile - "
"the Arrange command. 'boundary_sketch' = the sketch whose closed profile is the envelope to "
"pack into; 'shapes' = the occurrence name(s) to lay out (comma-separated). 'solver': "
"'true_shape' (nest the actual part outlines, tightest) or 'rectangular' (nest bounding boxes). "
"'spacing' = minimum clearance between parts (in 'units'). WRITES an Arrange feature. Note: "
"true-shape nesting can need a Fusion extension on some accounts (the tool reports that and you "
"can fall back to 'rectangular'). Pair with view_screenshot (top view) to see the layout."
)

tool = (
    Tool.create_simple(name="model_arrange", description=TOOL_DESCRIPTION)
    .add_input_property("boundary_sketch", {"type": "string",
            "description": "Name of the sketch whose profile is the boundary envelope."})
    .add_input_property("shapes", {"type": "string",
            "description": "Occurrence name(s) to arrange (comma-separated)."})
    .add_input_property("solver", {"type": "string",
            "description": "true_shape (actual outlines) | rectangular (bounding boxes). Default true_shape."})
    .add_input_property("spacing", {"type": "number",
            "description": "Minimum clearance between parts, in 'units'."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
