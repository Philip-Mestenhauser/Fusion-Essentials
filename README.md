# Fusion-Essentials

A set of quality-of-life improvements for your Fusion workflow, plus a local MCP server that lets an
AI assistant work inside your open Fusion session. Both ship in the same add-in and run entirely on
your own machine.

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
5. **Ability to Change Settings** You can enable/disable features or change the default units, and the settings persist between sessions. There is no guarantee that they will persist over updates of the add-in, until a 1.0 release is made.
6. **Color Holes** This command will color all same-sized holes in a part and tell you what nominal size they might be based on the defaults in common CAD software.
7. **Update Tools from Libraries** This command in the Manufacturing workspace will replace tools in your document with identical tools from the library they came from.

## The MCP server

[Model Context Protocol](https://modelcontextprotocol.io) is the standard way an AI assistant talks
to software outside itself. Fusion-Essentials can run a small MCP server on your machine, and any
MCP client (Claude, or another) can connect to it and work in whatever document you have open:
reading a design, sketching, modelling, assembling, measuring, taking screenshots, setting up CAM.

The tools are workflow-agnostic. Each does a single job and assumes nothing about how your shop
works, so a repeatable procedure is something you assemble in your client out of whichever calls it
needs.
Anything that changes the model reads the design back afterwards, ensuring LLM tools can act more like an incrimental designer, than a script shotgun.

The server is **off by default**. Setup, connecting a client, the full tool list, permissions, and
why it is built the way it is: [MCP Server README](commands/mcpServer/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the add-in conventions, the MCP tool-authoring recipe,
and how to run the test suite (`py -3 -m pytest -q`, no Fusion session needed).

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
