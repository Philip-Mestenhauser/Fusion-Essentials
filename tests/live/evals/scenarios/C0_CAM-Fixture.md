---
id: C0_CAM-Fixture
fixture: none
---

## Prompt

GOAL - the CAM FIXTURE SET: one design holding six small demo parts as separate components, plus
this document's tool library, saved once so that every later machining eval starts from a copy of
it and never from a file shipped with the repository.

The active document is new and empty; work in millimetres. Every part is its own component, named
as below, its solid modelled where the component sits. Spread the five machined parts along X on
the XY plane at a 120 mm pitch and the tray family in a row behind them at a 40 mm pitch, so no
two parts overlap and each can be selected as a setup's model on its own. Placement is yours; the
dimensions are not - a later run is compared against these numbers, so model them as stated and
read each one back. Drive the bracket's two pocket depths and the shaft's groove position with
named user parameters; everything else may be typed.

1. BRACKET (2.5D milling): a 60 x 40 x 20 mm block, top face up, its long side along X.
   - Pocket A: rectangular, 24 x 16 mm, 6 mm deep, 3 mm corner radii, centred 16 mm from the left
     end on the width's centreline, with a 6 mm diameter island standing to the top at its centre.
   - Pocket B: round, 12 mm in diameter, 5 mm deep, centred 10 mm from the right end.
   - Boss: round, 10 mm in diameter, standing 4 mm proud of the top, centred 34 mm from the left
     end on the width's centreline.
   - Four 5 mm through holes at the corners, 7 mm in from each edge.
   - Two counterbored holes 34 mm from the left end, 8 mm in from each long edge: 5.5 mm through,
     9 mm counterbore 4 mm deep.
   - A 1 mm x 45 deg chamfer on the top outer edge.
   - A sketch on the top face carrying the text FE, 5 mm tall, in the strip between pocket A and
     the far long edge, clear of the corner hole - left as a sketch, never cut, for an engraving
     pass later. A sketch on a face may pick up the face's edges as projected curves; delete any
     that appear, so the sketch holds the text and nothing else, and read its curve count back.
2. SHAFT (turning plus milled flats): 20 mm in diameter, 80 mm long, its axis along X.
   - End A steps down to 16 mm in diameter over its last 25 mm, with a modelled M16 x 1.5 thread
     over the outer 12 mm of that step.
   - A groove 3 mm wide and 1.5 mm deep, centred 15 mm from end B.
   - Two opposite flats on the 20 mm body, 2 mm deep and 12 mm long, centred 30 mm from end B.
   - A 1 mm x 45 deg chamfer on both end faces.
3. SADDLE (3D and multi-axis): 50 x 40 mm footprint, 25 mm tall at its tallest.
   - The top is a swept surface: a convex arc of 60 mm radius across the 40 mm width (the profile)
     swept along a concave arc of 80 mm radius running the 50 mm length (the path) - a saddle, not
     a cylinder. The top then reads about 25 mm at the middle of each short end, about 21 mm at
     the part's centre, and lower still along the long sides.
   - The four side walls are drafted 10 deg, the top smaller than the base.
   - A 2 mm fillet where the top meets the walls; if the fillet will not build, say so and leave
     the edge sharp.
4. CUP (rest machining): a thin-walled cup, 40 mm outer diameter, 30 mm tall, 1.5 mm wall, 2 mm
   floor, open at the top, a 3 mm fillet on the inner floor corner and a 1 mm fillet on the rim.
5. PLATE (hole and pocket recognition): 80 x 60 x 12 mm, laid out so no feature comes within 3 mm
   of another or of an edge, except the open pocket, which opens through one long edge:
   - a grid of six 4 mm through holes, two rows of three at a 15 mm pitch;
   - three 6.5 mm through holes in a row at a 15 mm pitch;
   - two counterbored holes: 5.5 mm through, 9 mm counterbore 4 mm deep;
   - two blind holes 5 mm in diameter, 8 mm deep, with a 118 deg drill point;
   - two M5 tapped holes, modelled thread, 10 mm deep;
   - a rectangular pocket 30 x 20 mm, 4 mm deep, 3 mm corner radii, with a 6 mm round island;
   - a round pocket 12 mm in diameter, 4 mm deep, with a 1 mm fillet on its floor edge;
   - a slot 20 mm long and 6 mm wide, 3 mm deep, with full-round ends;
   - an open pocket on one long edge, 20 mm along the edge, 10 mm in, 3 mm deep;
   - a square through pocket 10 x 10 mm.
6. TRAY FAMILY (arrange and additive): eight parts, one component each, grouped under a parent
   component named TRAY, each standing on the XY plane at its own spot: a 15 mm cube; a cylinder
   12 mm in diameter and 20 mm tall; a hexagonal prism 14 mm across flats and 10 mm tall; an L
   bracket 25 x 15 x 10 mm with 3 mm walls; a ring 20 mm outside, 14 mm inside, 6 mm tall; a wedge
   20 x 12 mm at the base, 8 mm tall at one end and 0 at the other; a sphere 12 mm in diameter
   (it needs support to print); a plate 30 x 20 x 2 mm.

Then the document's TOOL LIBRARY. First list the tool types the shipped sample libraries offer;
then build fifteen tools from those types, each carrying its size in its description, a holder,
its flute count, and one preset named ALU with a spindle speed and a cutting feed of your choosing
(bare numbers): a 50 mm face mill; flat end mills of 10, 6 and 4 mm (3, 3 and 2 flutes); ball end
mills of 8 and 3 mm (2 flutes); a 10 mm 90 deg chamfer mill; drills of 4, 5 and 6.5 mm; an M5 tap;
a general turning insert, a 3 mm wide grooving insert and a threading insert; and one probe. A
type the library does not offer is reported, not faked with another. Around that library:
- Sizing: a diameter you set on a sample whose shoulder matched its cutter carries the shoulder
  with it; a sample with a shoulder of its own keeps it. Make sure at least one sized tool comes
  from each kind, and report per tool which shoulder followed and which kept its own, as the
  add's own read-back says it; set a shoulder yourself where the read-back says it did not
  follow.
- Identity: give three tools a product id and a vendor as well; the listing must show the holder,
  the product id and the vendor on each of those rows.
- A tracking parameter: on one tool, edit a parameter whose expression tracks another parameter
  of the same tool (the tool's own parameter listing shows which ones do) to a literal; the edit's
  warning names the relationship it broke, and the library read again holds the literal.
- Presets: on one drill add a second preset given in unit strings (a feed such as '35 in/min');
  attempt on one mill a preset whose feed is an expression that cannot evaluate, quote the
  refusal, and confirm nothing landed; remove that drill's second preset and read the names back;
  give the turning inserts a preset carrying a surface speed.
- Every tool's assigned number is read back from the listing and is unique.
- A new library: attempt to create one at the local scope, named CAM-Fixture-Lib-<stamp>, seeded
  with two of the document's tools by reference. If it lands, list it back by its url and say that
  no route here removes it again; if it is refused, quote the refusal exactly and go on - no run
  has yet proven a seeded local create, and the refusal is the finding.
- Other scopes: list the cloud and hub tool libraries by name and by url; attempt one write at the
  shipped scope and quote the refusal.
- Two reads, one answer: read the document library through the rich CAM read's library slice and
  through the library listing; the two must agree row for row, and say so.

Screenshots: one per part, framed on that part alone, and one of the whole layout, each written to
a file under one output folder you choose for this run (name the folder after the document); list
the paths in your report.

Save: save the document as CAM-Fixture-<stamp> into project {{PROJECT}}, folder {{FOLDER}} - the
project and folder named in tests/live/cloud_config.local.json, filled in here by the proctor or by
the operator - where <stamp> is today's date as YYYYMMDD followed by a two-character run tag of
your choosing (add a letter if the name is taken). Save once, at the end; every later eval copies
this document and never edits it.

Report: each part's component name and the fresh reads of its stated dimensions (each overall
box; the bracket's pocket depths, island, boss height and hole diameters; the shaft's step, groove
and flat positions; the saddle's height at the centre, at a short end's middle and at a corner;
the cup's wall and floor; the plate's holes by diameter with their depths and the pocket depths);
the tray family's eight boxes; the user parameters; the tool library as listed back with number,
type, diameter, shoulder, flutes, holder, presets and identity per tool, and which shoulders
followed; the preset refusal quoted; the local library's url; the cloud and hub listings and the
shipped-scope refusal; the two-reads agreement; the screenshot paths; the saved document's name
and link.

## Grader notes

- A good result, opened in Fusion: fourteen components (five parts, TRAY with eight children) on a
  grid, none overlapping, every one measuring as stated - the grader re-measures the bracket's
  pockets, island and holes, the shaft's step, groove and flats, the saddle's height at the three
  named points (about 25, 21 and 21.6 mm), the cup's wall and floor, the plate's holes by diameter
  and the pocket depths; the FE sketch uncut; a document library of fifteen tools with unique
  numbers, holders, the stated flutes and an ALU preset each, three carrying identity; the
  document saved once under a CAM-Fixture- stamp in the chain's folder; a local library named
  after the stamp; a report whose shoulder column matches the add's sized rows.
- What a weak agent does: models a cylindrical top instead of a saddle, forgets the island or the
  open pocket, cuts the engraving sketch, lets parts overlap, leaves a 10 mm cutter on a sample's
  12 mm shoulder without noticing, gives two tools one number, invents a tool type the library
  lacks, saves twice under two stamps, or reports the preset refusal as a landed preset.
- Axis this discriminates: MCP tooling on the modelling side - model_hole (counterbore, blind with
  a drill point, tapped), model_thread on the shaft, model_draft or a tapered extrude on the saddle,
  model_sweep for the doubly curved top, sketch_set_text left uncut, model_create_component with the
  TRAY parent, param_add for the drivers - and cam_edit_tools: action='list_types', action='add'
  with add_tools=[{from_type, diameter, description, holder, product_id, vendor, presets}] and its
  'sized' rows, action='edit' for tool_numberOfFlutes and tool_shoulderDiameter with the
  formula_source warning, action='add_preset' / 'remove_preset' with unit strings and the
  unevaluable refusal, action='create_library' at scope='local', action='list' at scope='cloud'
  and 'hub' with 'library', a write at scope='fusion' refused, and cam_get(include=['library'])
  against action='list'.
- First A/B to run: none - this scenario runs once per chain and its document is the fixture every
  later brief copies. If the fixture is wrong, run it again into the same folder; later briefs take
  the newest stamp.
- Coverage (the coverage map, section 2): A1, A2, A3, A4, A6, A8, A9, A10 -
  eight checks. Grade each from the report's library section and the listing read back; A2's
  stepped case and A8 have no sweep row today, so this run is their first measurement.
- Measured before any run: cam_edit_tools clones a shipped sample per type (cam_get(include=
  ['library_types']) is the vocabulary read live; 'center drill' lives only in an Inch sample
  library and the probe in the Probes library) and auto-assigns free tool numbers; a diameter
  override on a plain-shank sample carries the shoulder with it while a stepped sample keeps its
  own; a preset's feed and speed take a bare number or a unit string and an unevaluable expression
  is refused and rolled back; a formula-tracking row's edit warns and overwrites the relationship;
  create_library is proven only as the document-scope refusal, and the hub scope cannot host one
  through the API; doc_save_as with create_path=true lands the document and its web link settles a
  few seconds later; a face move crashes Fusion, so a saddle built by moving faces is a wrong
  route, not a slow one.
