# Fusion-Essentials
 A small set of QoL improvements for your Fusion workflow.

## Installation
You have a few options for installing Fusion Essentials. The easiest way is to download the repo as a zip file and following these instructions [here](https://medium.com/@arstein/installing-and-running-fusion-360-add-ins-3ffcd7546adc) to install the add-in.
If you are familiar with git, you can clone the repo into your add-ins folder.

## Features
1. **Add Holder** This command provides a quick way to add a toolholder to the tool library, currently it only supports single body toolholders (this will be fixed in the future).
2. **Clean Chamfer** This command will take a set of surfaces that form a existing chamfer and turn them into a single freeform surface with the isocurves aligned to the original surfaces. This is useful for interpolating chamfers with a ball endmill, although it is made largely obsolete by the Pencil operation.
3. **Automatically Enable Design History** This command will automatically enable design history for for what it perceives to be a newly imported file.
4. **Automatically Switch Units** This command will automatically switch the units of a newly imported file to the units of the current document.
5. **Ability to Change Settings** You can enable/disable or change the default units and the settings will persist between sessions. There is no guarantee that they will persist over updates of the add-in, until a 1.0 release is made.
6. **Color Holes** This command will color all same sized holes in a part and tell you what nominal size they might be based on the defaults in common CAD software.
7. **Update Tools from Library** This command in the Manufacturing workspace will replace tools in you document with identical tools form a library that they came from.
8. **MCP Server** Hosts a local [Model Context Protocol](https://modelcontextprotocol.io) server so an AI agent (Claude, or any MCP client) can interact with your live Fusion session — read what's in your projects, open files by their data-model ID, screenshot the viewport, and optionally run Fusion API scripts. It is **off by default** and runs only on your own machine. See the [MCP Server README](commands/mcpServer/README.md) for setup, the full tool list, and the security details.

## MCP Server

Fusion-Essentials can expose your Fusion session to an AI agent over the Model Context
Protocol. Enable **MCP Server** in the Fusion-Essentials settings (off by default), reload
the add-in, and connect an MCP client to `http://127.0.0.1:27182/mcp`.

The tools let an agent inspect and navigate your data and CAM programs, for example:
`data_list_projects` / `data_list_files` (with openable URLs), `doc_open`,
`design_get_tree` (assembly + external references), `view_list_workspaces` /
`view_switch_workspace`, `view_screenshot`, `view_set_visibility` (isolate/show/hide components
for a focused screenshot), `view_inspect` (orient/isolate/wireframe the camera and
restore it), `view_section` (cut the model to see inside), and a CAM set — `cam_get_setups`,
`cam_get_operations`, `cam_get_references`, `sys_get_tool_list` (tool sheet),
`cam_get_time`, `cam_get_nc_programs`, `cam_compare_operations` (diff two operations to
understand a strategy), `param_get` / `param_set`, `design_get_timeline` (how a design
is built), `design_get_configurations` (read/switch a configured design's configurations),
`doc_new` and the sketch set (`sketch_get`, `sketch_create`, `sketch_add_geometry`)
for starting a design and drawing on it, `sys_request_selection` / `sys_get_selection`
(hand off to the user to click a face/edge/vertex/body/component and read it back),
`joint_create_origin` (place a coordinate frame / WCS anchor programmatically),
`model_measure_bbox` (extents in world or part-space, to drive stock),
`cam_activate_setup`, `cam_show_toolpath` (show/hide individual operations' toolpaths), and toolpath templates
(`cam_list_templates`, `cam_apply_template`, `cam_save_template`). It can also manage data — `data_list_folders`, `data_create_project`,
`data_create_folder`, and `data_upload_file` (which uploads local CAD and lets Fusion translate
STEP/IGES/etc. into a Fusion design). Folder tools accept nested paths and can create
missing folders along the way. A gated `sys_execute_script` runs arbitrary Fusion
Python; it is disabled by default and must be turned on explicitly because it lets a
connected agent run code in your session.

Full instructions, the complete tool list, client configuration, and security notes are in
the [MCP Server README](commands/mcpServer/README.md).

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or
  http://www.apache.org/licenses/LICENSE-2.0)
- MIT license ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)

at your option.

### Contribution

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in the
work by you, as defined in the Apache-2.0 license, shall be dual licensed as above, without any
additional terms or conditions.