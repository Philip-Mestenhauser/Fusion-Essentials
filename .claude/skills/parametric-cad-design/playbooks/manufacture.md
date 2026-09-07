## Manufacture

**rig-is-part-of-the-job** - Hold the fixture as its own components beside the part, select only the part body as the model, and take the work coordinate system from the stock; the fixture is geometry the toolpaths must avoid, not decoration when a setup is created; not for a setup sheet exercise with no fixture. Prove: `cam_get`: selected_models names the part only and the wcs origin_mode is stated.

**steep-and-shallow-are-two-strategies** - Parallel finishes shallow areas and skips steep ones by default; 3D contour does the opposite; one flag on each takes the other's areas, and scallop finishes both at a constant stepover a tenth of the tool when finishing a 3D form; not for a flat part, where face and 2D contour suffice. Prove: `cam_compare_operations`: machineSteepAreas or machineShallowAreas is the only difference between the pair.

**pocket-or-adaptive** - Adaptive takes deep stepdowns at a light radial load and needs no leads or compensation; 2D pocket takes shallow stepdowns with a finishing pass and cutter compensation - pick adaptive for bulk removal, pocket when the wall finish comes from the same operation when roughing a pocket; not for a slot, which wants the slot strategy on a closed slot contour. Prove: `cam_compare_operations`: optimalLoad and maximumStepdown on adaptive; finishing passes and compensation on pocket.

**geometry-selection-is-half-the-operation** - Compare their geometry selections - chains, extension modes, stock contours, boundaries - because a parameter diff cannot see them when two operations read as identical; not for operations that differ in parameters already. Prove: `cam_get`: each operation's references: the chains and faces it was given.

### Recipes

#### Choosing a strategy for a feature

Use when a face, pocket, wall, hole or free-form surface needs an operation.

1. `cam_get` - read the setup's allowed strategies. Read back: allowed true for the candidates; a false one cannot be created.
2. `find_geometry` - classify the feature: flat top (face), pocket floor (adaptive or pocket), wall (2D contour), hole (drill, bore, thread), curved surface (parallel, contour, scallop), edge (chamfer). Read back: the face kinds and normals.
3. `cam_create_operation` - create the candidate with a named tool. Read back: its name and state.
4. `cam_select_geometry` - give it the chains or faces; set the chain extension where the cut must run past the wall. Read back: the selection read back.
5. `cam_generate` - generate. Read back: a handle, then cam_get_status until completed.
6. `cam_inspect_toolpaths` - inspect the result. Read back: the operation is not among empty_toolpaths; an empty one means the wrong strategy or selection.
7. `cam_get` - read the time slice. Read back: machining time above zero for the operation.
8. `cam_compare_operations` - when unsure between two strategies, create both and compare. Read back: the handful of parameters that differ name the trade-off.

Bar - measure: cam_inspect_toolpaths lists no empty toolpath, cam_get's time slice reads above zero, and the operation is valid with no warning. Eyes: the toolpath covers the feature and nothing else.
Exemplar: 2D - Overview of toolpaths (urn:adsk.wipprod:dm.lineage:VJJsAJVXQmiDOFFl6-xErw) - 22 operations named for their variant; compare '2D Contour2' with its 'multiple passes', 'Trimmed' and 'Rest machining' siblings. Access: Autodesk Design Samples; needs hub access, read only

#### Proving a toolpath before posting

Use when an operation generated and must be trusted.

1. `cam_get` - read the operation with its parameters and tool. Read back: state valid, no warning text, the tool it uses.
2. `cam_inspect_toolpaths` - read the toolpath census. Read back: the operation is valid and not empty.
3. `cam_get` - read the time slice. Read back: machining time above zero, feed and rapid distances.
4. `cam_show_toolpath` - show the toolpath and take a screenshot. Read back: the passes lie on the feature, inside the stock.
5. `cam_post` - post to a scratch folder with the machine's post. Read back: the file landed with a size above zero.
6. `cam_get` - re-read after posting. Read back: no operation went out of date.

Bar - measure: the time slice reads above zero, the census lists the operation as not empty, and the posted file exists with a size. Eyes: the shown toolpath stays on the feature, inside the stock and clear of the fixture.
