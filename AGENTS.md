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

## Verbose build output

When running verbose build or packaging commands (for example `python tools/build_exe.py`, `python tools/build_installer.py`, PyInstaller, or Inno Setup), redirect full stdout/stderr to a log file and only surface a concise summary or log tail in the conversation. Do not stream full build logs into agent context unless explicitly requested.

Preferred pattern:

```powershell
python tools/build_exe.py *> build_exe.log
Get-Content build_exe.log -Tail 80
```

In Bash-compatible shells:

```bash
python tools/build_exe.py > build_exe.log 2>&1
tail -n 80 build_exe.log
```
