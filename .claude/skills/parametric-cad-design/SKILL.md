---
name: parametric-cad-design
description: >-
  Use when designing or modelling in Fusion through the fusion-essentials tools - building a
  part, a surfaced product, an assembly, or a machining job, not running a fixed procedure.
  Practice read out of Autodesk's own sample designs: what to settle before the first feature,
  how a sketch carries intent, the feature order of a moulded or surfaced part, how an
  assembly's freedom is structured, and which machining strategy fits. Rules say when they apply
  and what to read back; recipes are ordered tool sequences with a bar for done and an exemplar
  to X-ray.
---

# Designing in Fusion

Rules for judgment, recipes for sequence, a bar for done - read out of the designs Autodesk ships as samples.

## Kernel

**declare-acceptance-and-interfaces**
When you take on a brief: write what the design must satisfy and each interface as a checkable number - seating faces, aligned axes, clearance, wall thickness. Except a throwaway probe. Prove `workspace_orient`: the active document, its units and what is already in it.

**sequence-is-the-design**
When the first feature is next: decide the feature order before drawing: form, then fillets, then shell, then bosses and holes, then cosmetics - a timeline built in that order edits cleanly, one built by accretion does not. Except a one-feature part. Prove `design_get`: the timeline reads as the order you planned.

**encode-intended-changeability**
When a value carries a decision: type a wall thickness or a pitch ONCE and reference it by name everywhere else; promote it to a named parameter only when a family or a configuration is expected. Except a measured value, held in one parameter saying so. Prove `sketch_get`: dimensions whose expression is another dimension's name, not a repeated literal.

**build-and-observe-in-milestones**
When a milestone lands: read it back against intent before the next feature: a bounding box, a volume, a screenshot - never only at the end. Except a milestone a write already verified. Prove `model_inspect`: bounding box and volume against the numbers you wrote down.

**compare-the-artifact-with-acceptance**
When the design looks finished: compare it with the acceptance you wrote and say what you could not meet; a screenshot that reads as the object is part of the acceptance. Except acceptance the brief left open. Prove `view_screenshot`: does it read as the object it should be.

## Playbooks

- **Plan** (`plan`) - before the first component exists - naming, variants, what is bought. Read `playbooks/plan.md` or call `sys_get_guidance(section="plan")`.
- **Sketch** (`sketch`) - any profile that must survive a size change or drive a feature. Read `playbooks/sketch.md` or call `sys_get_guidance(section="sketch")`.
- **Model** (`model`) - turning sketches into a part - order, carving, patterns, frozen bodies. Read `playbooks/model.md` or call `sys_get_guidance(section="model")`.
- **Surface** (`surface`) - a shell, skin or product form that no extrude or revolve describes. Read `playbooks/surface.md` or call `sys_get_guidance(section="surface")`.
- **Assemble** (`assemble`) - more than one component - how they are held, joined and moved. Read `playbooks/assemble.md` or call `sys_get_guidance(section="assemble")`.
- **Validate** (`validate`) - the geometry exists and must be proved against the brief. Read `playbooks/validate.md` or call `sys_get_guidance(section="validate")`.
- **Finish** (`finish`) - handing the work on. Read `playbooks/finish.md` or call `sys_get_guidance(section="finish")`.
- **Manufacture** (`manufacture`) - a machining job - setups, strategy choice, what a toolpath must prove. Read `playbooks/manufacture.md` or call `sys_get_guidance(section="manufacture")`.

## Recipes

- `sketch-anchored-profile` (sketch) - a bracket, plate or revolved profile that must resize by intent. `sys_get_guidance(recipe="sketch-anchored-profile")`.
- `sketch-link-between-bores` (sketch) - a rocker, lever, connecting link or any web joining round bosses. `sys_get_guidance(recipe="sketch-link-between-bores")`.
- `sketch-organic-outline` (sketch) - a mouse, handle or shell silhouette that must stay smooth while its proportions change. `sys_get_guidance(recipe="sketch-organic-outline")`.
- `model-moulded-part` (model) - a basket, cover, drawer front or housing with a wall, bosses and clips. `sys_get_guidance(recipe="model-moulded-part")`.
- `model-frozen-body-with-interfaces` (model) - a purchased part, an imported STEP or a generative outcome that needs holes and seats. `sys_get_guidance(recipe="model-frozen-body-with-interfaces")`.
- `model-parametric-family` (model) - one part in several sizes. `sys_get_guidance(recipe="model-parametric-family")`.
- `surface-swept-bottle` (surface) - a container or housing whose section changes along a curved spine. `sys_get_guidance(recipe="surface-swept-bottle")`.
- `surface-skin-into-parts` (surface) - a product whose top, base and middle share one outer surface. `sys_get_guidance(recipe="surface-skin-into-parts")`.
- `assemble-part-modelled-in-place` (assemble) - a rocker, bracket or lever designed between parts that already sit where they belong. `sys_get_guidance(recipe="assemble-part-modelled-in-place")`.
- `assemble-screw-motion` (assemble) - a threaded cap, lead screw or any turn-to-advance pair. `sys_get_guidance(recipe="assemble-screw-motion")`.
- `manufacture-choose-a-strategy` (manufacture) - a face, pocket, wall, hole or free-form surface needs an operation. `sys_get_guidance(recipe="manufacture-choose-a-strategy")`.
- `manufacture-prove-a-toolpath` (manufacture) - an operation generated and must be trusted. `sys_get_guidance(recipe="manufacture-prove-a-toolpath")`.
