---
name: parametric-cad-design
description: >-
  Use when designing or modelling in Fusion through the fusion-essentials tools - building a
  part or an assembly, not running a fixed procedure. Task-agnostic practice: what to settle
  before the first feature, how intent is carried in parameters and sketches, how an assembly's
  degrees of freedom are structured, and the reads that prove what you built is what you meant.
  Each rule states when it applies, what to do, where it does not apply, and what to read back.
  Guidance to apply while the design is still moving, not a checklist to recite.
---

# Designing in Fusion

Aimed at the gap automated checks miss.

## Kernel - every design

**declare-acceptance-and-interfaces**
When you take on a brief: state what the design must satisfy, and each interface as a checkable expectation - seating faces, aligned axes, clearance. Except a throwaway probe. Prove `workspace_orient`: the active document and its units.

**encode-intended-changeability**
When a value carries a decision: hold it in a named, commented parameter and derive the rest; a buried literal freezes while geometry moves. Except a measured value, in one parameter saying so; audits only read. Prove `param_get`: each driving parameter's expression and comment.

**build-and-observe-in-milestones**
When a milestone lands: read it back against intent, not at the end; a half-turn seating error shows in a bounding box. Except a milestone already proved under verify-the-write. Prove `model_inspect`: the bounding box of what was built.

**prove-structure-before-detail**
When detail is next: prove the structure first - detail on a part sitting on the wrong plane is drawn twice. Except detail that is itself the requirement. Prove `model_measure_relation`: perpendicular, coaxial or concentric where claimed.

**compare-the-artifact-with-acceptance**
When the design looks finished: compare it against the acceptance you wrote, and report each item you could not meet. Except acceptance the brief left open. Prove `view_screenshot`: whether it reads as the object it should be.

## Plan

**name-parts-for-what-they-are**
When a part enters the tree: a made part takes a functional name, a bought part its supplier number. Except imported names. Prove `design_get`: the component names in the tree.

**variants-are-configurations**
When the brief names several variants: author them as configurations of one document; check the active row before calling a failed compute a defect. Except a single variant. Prove `design_get`: the configuration table and the active row.

## Sketch

**couple-what-moves-together**
When two profiles must move together: draw them in one sketch so an edit carries; give independent features their own sketch; pick a profile by measured area, not index. Except geometry projected from a body. Prove `sketch_get`: each profile's area, centroid and handle.

**constraints-carry-relationship**
When a sketch must survive a size change: carry symmetry, coincidence, midpoint and tangency as constraints; dimensions set only size. Except a reference sketch you will not resize. Prove `sketch_get`: is_fully_constrained and the constraint list.

## Model

**pattern-only-identical-intent**
When the same feature repeats: build one and pattern it; copies answering different requirements are modelled apart. Except copies that merely share a shape. Prove `design_get`: the pattern feature in the timeline.

**let-the-process-shape-the-part**
When the part will be manufactured: take terminations, draft and minimum internal radius from that process; treat edge treatment as design. Except an unsettled process, which is stated rather than assumed. Prove `find_geometry`: the resulting faces, their normals and radii.

## Assemble

**connected-reference-path**
When an assembly has a fixed moving mechanism: establish one intentional reference path; do not ground parts to hide missing joints. Except floating or multiple independent mechanisms. Prove `assembly_get`: grounding and joint connectivity; `joint_drive`: home and representative extreme positions; `assembly_inspect_interference`: overlaps at each pose.

**exercise-the-mechanism**
When the mechanism has a driven joint: drive it to home and a representative extreme; keep moving joints few, the rest rigid. Except a joint with no travel of interest. Prove `joint_drive`: the value at each pose; `assembly_inspect_interference`: overlaps at each pose, intended fit or defect.
Example: a nut overlapping a plain shaft is a thread modelled as a cylinder.

## Validate

**close-the-declared-interfaces**
When the declared interfaces are built: measure each and report the number - contact where parts engage, clearance where they move. Except an interface the brief left open. Prove `model_measure_between`: the distance or angle at each interface.

**grade-structure-not-counts**
When counts and volumes look right: read plane normals, axis directions and containment - a flat stack passes every count while nothing nests. Except a genuinely planar design. Prove `find_geometry`: face normals and linear-edge directions.

**measurements-test-the-brief**
When a measurement disagrees with the brief: report the disagreement - a measurement is evidence about the artifact; intent comes from the brief. Except a brief quoting a measured fact about existing hardware. Prove `model_measure_between`: the measured value beside the brief's.

## Finish

**verify-the-write** (safety invariant)
When a write reports success: confirm the effect through a read other than the tool's claim; read a refusal's reason before retrying. Except a deferred write, confirmed through the poller it names. Prove `design_get`: the timeline entry the write claims.

**leave-the-artifact-openable**
When the work is handed on: leave the saved state valid, look at the result, and say what you could not do. Except a scratch document. Prove `doc_get`: the document's save state.
