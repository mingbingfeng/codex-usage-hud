# Codex Project Instructions

## Product direction

Renderer mode is the canonical product direction. Keep future HUD work on the
in-renderer/CDP surface and keep it compatible with both Windows and macOS.

Qt/Tk HUD modes are deprecated legacy surfaces. Do not add new product behavior
to Qt/Tk, do not make Qt/Tk the default or recommended path, and do not solve
renderer performance issues by switching users back to Qt/Tk. Touch Qt/Tk only
for removal, migration, critical maintenance, or tests needed while legacy code
still exists.

Renderer performance work should move toward event-driven updates: no page,
session, config, or filesystem event should mean no recurring CPU work. Polling
is acceptable only as a temporary fallback with conservative backoff and should
be retired whenever a reliable listener exists.

## Codebase graph memory

For every project-related task, first run
`codebase-memory-mcp cli index_status '{"project":"E-Project-codex-usage-hud"}'`,
then use `search_graph` first (or `search_code`) with the task terms before
manual tracing. Skip this for non-project tasks. The index is navigation only:
verify paths, lines, and behavior in the current workspace. Do not automatically
reindex.

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
