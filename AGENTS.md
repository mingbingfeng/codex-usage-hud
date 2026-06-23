# Codex Project Instructions

## codebase-memory-mcp

Project name: `E-Project-codex-usage-hud`.

Automatically prefer codebase-memory-mcp for:
- locating symbols, files, and feature entry points
- finding callers, callees, and impact scope
- architecture overview, hotspots, and dependency boundaries
- complex cross-file relationship queries

Use codebase-memory-mcp to narrow scope before reading source. Do not treat the graph as final truth; confirm behavior with source reads and normal verification.

After large pulls, branch switches, or broad refactors, refresh the index manually:

```powershell
codebase-memory-mcp cli index_repository '{"repo_path":"E:/Project/codex-usage-hud","mode":"full","persistence":false}'
codebase-memory-mcp cli index_status '{"project":"E-Project-codex-usage-hud"}'
```

If codebase-memory-mcp is unavailable in the current client, continue with normal local source inspection and state that the MCP graph was unavailable.
