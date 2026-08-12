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

## Validation policy

Use the staged validation command for ordinary, isolated changes:

```powershell
python tools/run_validation.py
```

Run the full default pytest regression gate when a change has a broad or
uncertain impact. This is required for changes that:

- cross module or runtime-owner boundaries, change shared contracts, payloads,
  schemas, settings persistence, pytest configuration, dependencies, or build
  and packaging behavior;
- touch Renderer/CDP injection, daemon startup/restart, process detection,
  session tracking or cleanup, event-loop scheduling, refresh policy, JSONL/
  SQLite/filesystem watching, or Windows/macOS integration;
- fix a regression or flaky test, change behavior without narrow regression
  coverage, or have an impact surface that cannot be confidently bounded; or
- are being prepared for a release, tag, or an explicit merge/release gate.

Use:

```powershell
python tools/run_validation.py --full
```

The default full gate follows `pyproject.toml` and excludes tests marked
`ui`. When real widget lifecycle behavior changes, also run the UI markers:

```powershell
python -m pytest -o addopts="" -m "ui or qt_ui" -q
```

To run every collected marker in one command, including UI tests, use:

```powershell
python -m pytest -o addopts="" -q --durations=10
```
