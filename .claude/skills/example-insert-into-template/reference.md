# Reference - Workflow Template methodology

Loaded on demand by the `insert-into-template` skill when a crawl result is ambiguous.
Distilled from the AU2024 class *Templates, Configurations, and Containers for Agile
Prototype Machining in Autodesk Fusion* (MFG3914) and the Fusion-Essentials tool notes.
No shop-specific data here -- only the published framework concepts and the API facts needed
to read a template correctly.

## The core idea: reconfiguring, not reprogramming

Replace the model in a template and have toolpaths regenerate; switch fixtures or machines via
a configuration table -- all while keeping joints and CAM selections intact. The structures
below exist to make that possible.

## Component Containers

A Component is a container for CAD. A CAM setup's Model selection points at the *container*,
not the geometry inside it -- so the setup keeps its selection even when the container's
contents are replaced. A Body is just geometry and must live inside a Component; a Component
can be empty (only its origin planes/axes).

Consequence for crawling: a setup's `models` / `fixtures` / `stockSolids` entries are usually
**container Occurrences** (named like `Model Component`, `Fixture Container`, `Stock
Container`). Reading only the top level shows the container, not its contents. You MUST
descend `Occurrence.childOccurrences` to find the real parts.

## Joint Origin Containers (JOC) and joint survival

A JOC is an empty component with a Joint Origin at its coordinate origin, joined to the real
geometry, so joints survive model replacement. Joints survive across files via Save-As lineage
from a common ancestor (shared EntityIDs let Fusion auto-repair joints). This is why
replaceable fixtures are often named like "...Save-as and replace to make a new fixture."

## The Replaceable Fixturing Assembly (RFA) - what to expect inside a setup

A setup's Model selection is a Container nesting three standardized typed files:

| Typed file | Role |
|---|---|
| **Vise Type** | Grips the stock; standardized attachment points for jaws / zero-point systems. |
| **Clamping Unit / Pallet Type** | Mounts the vise to the machine; defines the Machine Model Attachment point used in simulation. |
| **WCS Type** | A **simple cube** that explicitly defines the Z and X directions for the CAM setup, so the WCS is always accurately located. |

So when `get_component_tree` descends a setup's model/fixture container, expect: a Clamping
Unit/Pallet + a Vise + a WCS cube + the machined model.

## Two common mistakes to avoid

1. **Treating a container as one opaque object.** An early automated crawl read `Fixture
   Container` as a single part. Wrong -- descend it; the structure only becomes legible when
   you read the nesting.
2. **Calling the WCS cube a placeholder.** A lone cube referenced by a setup is almost
   certainly the WCS Type -- it defines the WCS orientation. Do not label it a placeholder;
   descend and label it correctly.

## Selectionless toolpaths and parametric stock

- **Selectionless toolpaths** (e.g. 3D Adaptive, Bore) point at the Model Container and use
  geometry recognition + diameter ranges, so they regenerate automatically when a new part is
  inserted. This is the property Phase 3 checks for.
- **Parametric stock** is driven by user parameters; jaws adjust to the workpiece via
  configured joints. The stock params live in `Design.userParameters` (named like stock /
  fixture dimensions); their `.expression` may reference other parameters (the parametric
  linkage). These are the params Phase 4 adjusts to the new part's bounding box.

## How this maps to the Fusion API (for the building blocks)

- A CAM setup's `models` / `fixtures` / `stockSolids` are typically container Occurrences;
  `get_component_tree` descends them and resolves external references.
- An external reference is `Occurrence.isReferencedComponent == True`; it resolves via
  `Occurrence.documentReference.dataFile` -> `.id` (lineage UID / URN), `.name`,
  `.fusionWebURL`.
- CAM data is reachable WITHOUT switching to the Manufacture workspace -- the `get_cam_*`
  building blocks already do this.
- `compare_operations` reports exact parameter expressions (including float jitter like
  `38.10000000000001`) deliberately. Do not round or filter; reason about precision.

## Determinism checklist (why the phases are ordered this way)

- Orient and crawl are READ-only so the agent establishes ground truth before any change --
  same starting state, same plan.
- The Phase 3 gate converts the crawl into explicit pass/fail assertions; a failed assertion
  stops the run instead of improvising.
- Post-mutation re-reads (`get_machining_time`, `compare_operations`, screenshot) verify the
  change did what was intended, closing the loop.
