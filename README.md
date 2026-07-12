# Fusion-Essentials
 A small set of QoL improvements for your Fusion workflow.

## Installation
You have a few options for installing Fusion Essentials. The easiest way is to download the repo as a zip file and following these instructions [here](https://medium.com/@arstein/installing-and-running-fusion-360-add-ins-3ffcd7546adc) to install the add-in.
If you are familiar with git, you can clone the repo into your add-ins folder.

## Features
1. **Add Tool Holder** This command provides a quick way to add a single-body toolholder to the tool library.
2. **Clean Chamfer** This command will take a set of surfaces that form an existing chamfer and turn them into a single freeform surface with the isocurves aligned to the original surfaces. This is useful for interpolating chamfers with a ball endmill, although it is made largely obsolete by the Pencil operation.
3. **Automatically Enable Design History** This command will automatically enable design history for what it perceives to be a newly imported file.
4. **Automatically Switch Units** This command will automatically switch the units of a newly imported file to the units of the current document.
5. **Ability to Change Settings** You can enable/disable features or change the default units, and the settings persist between sessions.
6. **Color Holes** This command will color all same-sized holes in a part and tell you what nominal size they might be based on the defaults in common CAD software.
7. **Update Tools from Libraries** This command in the Manufacturing workspace will replace tools in your document with identical tools from the library they came from.
8. **MCP Server** Hosts a local [Model Context Protocol](https://modelcontextprotocol.io) server so an AI agent (Claude, or any MCP client) can interact with your live Fusion session — read what's in your projects, open files by their data-model ID, screenshot the viewport, and optionally run Fusion API scripts. It is **off by default** and runs only on your own machine. See the [MCP Server README](commands/mcpServer/README.md) for setup, the full tool list, and the security details.

## MCP Server

Fusion-Essentials can expose your Fusion session to an AI agent over the Model Context
Protocol. Enable **MCP Server** in the Fusion-Essentials settings (off by default), reload
the add-in, and connect an MCP client to `http://127.0.0.1:27182/mcp`.

The tools let an agent inspect and act across your Fusion session — the data model (browse
projects/files, open by ID, upload CAD), the design (read the assembly tree and timeline, sketch,
model, joint, measure), the viewport (screenshot, isolate, section to see inside), and CAM (read
setups/operations/tools, time, templates). A connected agent typically starts with
`workspace_orient` — one read that reports what's open, its health, and where to look next — then
drills in with scoped reads. A gated `sys_execute_script` runs arbitrary Fusion Python; it is
disabled by default and must be turned on explicitly, because it lets a connected agent run code in
your session.

The server exposes **137 typed tools** built on three enforced contracts: typed inputs that refuse
ambiguous references, declared outputs (including real pass/fail verdicts with their evidence), and
postconditions that re-read ground truth after every write so a "success" that changed nothing
becomes an error. The authoritative per-tool inventory is generated:
[`tests/generated/MANIFEST.md`](tests/generated/MANIFEST.md). Full setup, client configuration, the design philosophy,
and security notes are in the [MCP Server README](commands/mcpServer/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the add-in conventions, the MCP tool-authoring recipe,
and how to run the test suite (`py -3 -m pytest -q` — no Fusion session needed).

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