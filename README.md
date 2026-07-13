# Fusion-Essentials

An essential toolkit for driving Autodesk Fusion with LLMs — a local MCP server whose typed tools
let an AI agent read, model, assemble, verify, and machine in your live Fusion session — plus a
small set of quality-of-life improvements for your own Fusion workflow.

## The MCP server (the toolkit)

Fusion-Essentials hosts a local [Model Context Protocol](https://modelcontextprotocol.io) server so
an AI agent (Claude, or any MCP client) can drive your live Fusion session: orient, read the data
model and design structure, sketch, model, joint, measure, screenshot, and set up CAM.

Three enforced contracts make the tools trustworthy: typed inputs that refuse ambiguous references
instead of guessing, declared outputs (real pass/fail verdicts beside their evidence), and
postconditions that re-read ground truth after every write — a "success" that changed nothing
becomes an error. The surface is built for token-efficient driving: one cheap orientation read
(`workspace_orient`), progressive disclosure on every rich read, and result payloads that point to
the right next tool instead of dumping depth unprompted.

It is **off by default** and runs only on your own machine. Setup, client configuration, the
how-to-drive-Fusion-well doctrine, demonstrated workflows, and security notes:
[MCP Server README](commands/mcpServer/README.md). The authoritative per-tool inventory is
generated from the live registry: [TOOL_MANIFEST.md](tests/generated/TOOL_MANIFEST.md).

## Installation

You have a few options for installing Fusion Essentials. The easiest way is to download the repo as
a zip file and follow the instructions
[here](https://medium.com/@arstein/installing-and-running-fusion-360-add-ins-3ffcd7546adc) to
install the add-in. If you are familiar with git, you can clone the repo into your add-ins folder.

## QoL features

1. **Add Tool Holder** This command provides a quick way to add a single-body toolholder to the tool library.
2. **Clean Chamfer** This command will take a set of surfaces that form an existing chamfer and turn them into a single freeform surface with the isocurves aligned to the original surfaces. This is useful for interpolating chamfers with a ball endmill, although it is made largely obsolete by the Pencil operation.
3. **Automatically Enable Design History** This command will automatically enable design history for what it perceives to be a newly imported file.
4. **Automatically Switch Units** This command will automatically switch the units of a newly imported file to the units of the current document.
5. **Ability to Change Settings** You can enable/disable features or change the default units, and the settings persist between sessions.
6. **Color Holes** This command will color all same-sized holes in a part and tell you what nominal size they might be based on the defaults in common CAD software.
7. **Update Tools from Libraries** This command in the Manufacturing workspace will replace tools in your document with identical tools from the library they came from.

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
