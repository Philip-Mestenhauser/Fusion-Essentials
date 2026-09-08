---
id: S2a_Hardware-Structure
fixture: S1_Foundation
---

## Prompt

The active document is the gyroscope's foundation: sketch-only components around a shared
skeleton (a centre point and three perpendicular pivot lines), with the interfaces between parts
planned on those lines.

GOAL - give every part its solid, each owned by its own component, at its sketched position on the
skeleton. Never move an occurrence to solve a problem.

Work part by part: read the part's sketch, build its solid, read the result, then move to the next.
Do not design the whole machine before the first extrude; the reads after each part are where the
plan gets corrected.

The machine has to work, so:

- The carrier rests on the pedestal and the crank mounts on the frame: real flush contact, face on
  face, never penetration. Extend or add geometry if the plan left a support short.
- Everything that moves stays clear: rotor and shaft inside the inner ring, inner ring inside outer
  ring, outer ring inside the carrier's reach and the frame opening. Rings are bands, coplanar and
  symmetric about the ring plane. Leave the shaft short of the inner ring; its seats come later.
- The frame's central opening stays open.
- Each ring will later tilt 30 degrees either way, so leave the volume it sweeps through empty: the
  member a ring pivots in is a ring or a fork, never a cup with a floor.
- The foundation may lack a closed profile for some part (the shaft, usually). Make what is missing,
  on the skeleton, and say which parts you found and which you made.
- No pivot pins, no pivot bores, no joints yet.

Check with fresh reads and report the values: one connected solid per component with its volume;
the two support contacts proven by a near-zero distance or the interference check's coincident-face
listing; zero overlapping pairs; the radial clearance at each nesting step; the rings' mid-planes on
the shared centre; the rotor coaxial with its shaft; the timeline healthy; and how far you believe
each ring can tilt.

## Grader notes

- The A/B partner of S2a_Hardware-Structure: the same machine and the same checks, with the motion
  clause cut to one sentence and an explicit "work part by part, read after each" instruction in
  place of the engagement contract's prose.
- What it measures: the A brief stalls the executor after its first dozen calls in every run so far
  (it reads the foundation, then thinks past the ten-minute watch without acting), and the thinking
  cap passed to the CLI does not bound a turn (a stalled turn read 42k estimated thinking tokens
  under a 16k cap). If B builds where A stalls, the brief's shape is the cause and the fix is
  prompt-side; if B stalls too, the stage itself is too large for one blind run and splits.
- A good result is the A file's: eight solids on the skeleton, supports touching, rings nested in one
  plane with gaps, an open carrier, zero overlaps.
