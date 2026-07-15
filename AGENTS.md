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

See `docs/RENDERER_MODE_STRATEGY.md` before changing renderer injection,
session tracking, refresh scheduling, or platform integration.

## Optional codebase graph memory

When a cross-file architecture or call-graph question benefits from indexed
evidence, optionally use `codebase-memory-mcp` before broad manual tracing:

```text
codebase-memory-mcp cli list_projects '{}'
codebase-memory-mcp cli index_status '{"project":"<name>"}'
codebase-memory-mcp cli get_architecture '{"project":"<name>"}'
codebase-memory-mcp cli search_code '{"project":"<name>","pattern":"<regex>"}'
```

Use the index as a navigation aid only; verify file paths, lines, and behavior
against the current workspace. Do not enable automatic indexing by default.

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

## GitHub network fallback

If GitHub `git push` or `git pull` fails because of network or HTTP transport
issues, retry through the local `v2rayN` HTTP proxy on `127.0.0.1:10808` and
force Git to use HTTP/1.1 for that retry.

Preferred retry pattern:

```powershell
git -c http.proxy=http://127.0.0.1:10808 -c http.version=HTTP/1.1 pull
git -c http.proxy=http://127.0.0.1:10808 -c http.version=HTTP/1.1 push
```
