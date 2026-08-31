---
name: parametric-cad-design
description: >-
  Use when designing or modelling in Fusion through the fusion-essentials tools - building a part
  or an assembly, not running a fixed procedure. Task-agnostic practice: planning before the first
  feature, carrying intent in parameters and sketches, structuring an assembly and its degrees of
  freedom, and the reads that prove what you built is what you meant. Each rule names the defect it
  prevents. Guidance to apply while the design is still moving, not a checklist to recite.
---

# Designing in Fusion

A model can pass every count, dimension and automated check and still be the wrong object. These
practices are aimed at that gap.

## Before the first feature

**1. Plan the skeleton; declare the interfaces.** Choose the driving parameters and the construction
geometry every part is positioned off, and state each interface as a checkable expectation - which
faces seat, which axes align, what clearance belongs between them. Otherwise the mechanism's
position depends on the order its joints were made.

**2. Name a part for what it is.** Made parts take a functional name; bought parts take their
supplier part number. The tree is how a reader learns intent and a builder learns what to order.

## Carrying intent

**3. Drive the design from named, commented parameters.** A literal scattered through features is
frozen: rescale and it stays put while the geometry around it moves, silently. Beyond sizes, a
parameter can hold a RULE - clamped to a range, rounded to the increment stock is sold in, chosen on
a threshold. Keep a derived value and its override as separate parameters so one can be changed
without destroying the other, mark the few meant to be touched, and give each a comment carrying the
judgement behind it. A measurement is the honest exception to the no-literals rule: park it in one
named parameter that says so, and derive the rest from it. Units follow the domain, not the document.
Reading someone else's design assumes none of this - expressions come back wherever their author put
them, sketch dimensions included, so follow the expression rather than expecting a tidy table.

**4. One sketch, many profiles.** Geometry sharing a sketch stays related, so moving a line carries
everything referencing it. Approaching one sketch per feature means the unit of work is wrong.
`sketch_get` returns each profile's area, centroid and handle - choose a region by what it measures,
never by a guessed index.

**5. Dimensions set size; constraints set relationship.** Symmetry, coincidence, midpoint, tangency
and a sketch-level pattern survive a size change that an equivalent pile of dimensions will not; a
good profile sketch can carry no dimensions at all and still be rigid.

**6. Build once, then pattern - geometry and assemblies alike.** Repeated identical geometry means
the wrong feature was chosen. Above the part too: joint one member to its station and pattern the
jointed sub-assembly, and a few joints serve many occurrences.

**7. Soften what you made, and let the process shape it.** Edge treatment is a large part of a
finished part's features, not an afterthought - it is most of what separates a recognisable object
from a stack of primitives, and a variable-radius fillet is how a member gets a waist. A part that
will be manufactured also carries its process's constraints: how a feature terminates, where
material must taper to release, the smallest achievable internal radius. Take those from the process
rather than a feature default, and where the process is unsettled, say so instead of assuming.

## Structuring the assembly

**8. Ground one part; let the chain carry the rest.** Ground exactly one occurrence
(`ground_to_parent`); everything else reaches ground through joints. Grounding several conceals
whether the mechanism is connected at all. Nest sub-assemblies so a stage owns its own parts rather
than every part sitting as a sibling - but nest around the moving joints, not across them: a joint
meant to be DRIVEN wants two siblings, and one made between a part and its own parent can read back
its commanded value and then be put back by the solver. `assembly_get` shows the wiring, and a
driven value is worth re-reading after the next drive, not only at the drive that set it.

**9. Few degrees of freedom, many fasteners.** The joints that move are the mechanism and should be
few enough to name; the rest are rigid. Joint a correctly placed part where it stands rather than
letting the joint move it, and use a rigid group where parts travel as one body.

## Proving it

**10. Perceive while building.** Compare a cheap spatial read against intent at each milestone
rather than auditing once at the end. A mirror about the wrong axis, a part seated half a turn out,
a stray body stretching a component across the scene - each is obvious in a bounding box the moment
it happens and laborious to unpick later. `model_inspect`.

**11. Grade 3D structure, not counts.** Counts and volumes pass while the structure is wrong - the
characteristic failure being a flat stack, every part sketched on one plane and extruded along one
axis, so nothing nests or rotates. Read plane normals, axis directions and containment:
`find_geometry` returns face normals, `model_measure_relation` answers perpendicular / coaxial /
concentric - and an angle of zero proves parallel, not coaxial.

**12. Engage where it must, clear where it moves.** Prove contact by measurement and clearance by
`assembly_inspect_interference`. Do not aim for "nothing touches" - that produces assemblies whose
parts never reach each other and pass every check. Interference is not automatically a defect
either: a nut overlapping a plain shaft is a thread modelled as a cylinder, a pin overlapping its
bore is a press fit. Overlap between parts sharing a degree of freedom always is one.

**13. A design may hold variants.** One document can carry several configurations, and a template
parks alternates it is not using - suppressed features, a joint wired for the inactive arrangement.
A failed compute there may belong to the variant that is switched off, so check what is active
(`design_get(include=['configurations'])`) before calling anything broken. Author variants as
configurations rather than forked documents, so they cannot drift apart.

## Finishing

**Verify the write, then look at the result.** A call can report success while changing nothing, so
confirm an effect through a channel other than the tool's own claim. A brief's numbers and a
document's prose are claims; a measurement outranks them. Then look (`view_screenshot`) and ask
whether it reads as the thing it should be - a broken symmetry or a flat plate assembly is obvious
in an image and invisible in the numbers. A refusal carries its reason and usually its remedy: read
it rather than retrying with a changed argument. Report what you could not do, and leave the saved
state valid; the artifact is what the next person opens.
