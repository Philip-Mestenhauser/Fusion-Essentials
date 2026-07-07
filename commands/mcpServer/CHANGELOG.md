# Changelog

All notable changes to the Fusion-Essentials MCP server.

## 0.2.0 - 2026-07-06

### Removed
- Unused prompt/resource primitive framework inherited from the Autodesk sample (never registered or served); the server now declares only the tools capability.

### Fixed
- tools/list now sends each tool's readOnlyHint/destructiveHint annotations and
  strict-schema additionalProperties on the wire (they were declared and tested but
  dropped by the hand-built list entry).

### Changed
- initialize honors a requested protocolVersion only if the server implements it,
  otherwise it answers 2025-03-26 instead of blind-echoing the request.
- Tool execution failures return isError results the calling agent can read and
  self-correct from, instead of JSON-RPC -32603 protocol errors; unknown tool is
  reported as -32602.
- The expect_document convention is stated once in the server's initialize
  instructions; the per-tool schema property carries a one-line description
  (about 19 KB off the tools/list payload).

### Added
- Argument name validation before dispatch: an unknown or missing-required argument
  gets an actionable isError refusal instead of a raw Python TypeError.
- Single version source (version.py) reported by server_info and GET /health.
- Per-family enablement checkboxes (appearance, cam, data, drawing, mesh, save, surface) in Settings -> MCP Server; disabled families are not registered or advertised.

## 0.1.0

- Initial MCP server: 137 tools, streamable HTTP on 127.0.0.1:27182.
