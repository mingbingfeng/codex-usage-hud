# HUD Runtime Refactor Plan

## Direction

The HUD runtime should become renderer-authoritative, event-driven, and fail-fast.

The long-term target is:

```text
relevant event -> minimal state update -> targeted renderer/overlay push
no relevant event -> no recurring snapshot, scan, or payload work
```

This is a refactor track, not a patch track. The goal is to remove ambiguity in the
runtime graph and make failures visible during development instead of hiding them
behind fallback behavior.

## Product Rules

- Renderer mode remains the canonical product surface.
- Qt/Tk standalone HUD implementations are removed and must not be restored as product fallback.
- In renderer mode, active session selection must have one authority at a time.
- Fallback behavior must be opt-in diagnostic behavior, not default product logic.
- DEBUG mode should expose runtime errors in an error HUD.
- Normal mode should still write structured diagnostics, but must not silently switch
  to unrelated detection mechanisms.

## Performance Targets

| Interaction | Target |
|-------------|--------|
| Active session switch visible in HUD | < 150 ms after renderer observes switch |
| Current session JSONL append visible in HUD | < 250 ms |
| Settings save visible in HUD | < 250 ms |
| Overlay item change visible in desktop bubble | < 250 ms |
| Idle runtime with no events | no snapshot rebuild, no all-session scan, no CDP payload push |

Targets are engineering targets, not user-facing guarantees. They should be measured
with a local latency harness before and after each phase.

## Current Runtime Summary

Current renderer mode is already partially event-driven:

- The injected renderer script observes Codex sidebar/header/location and reports
  active session changes through CDP binding.
- Python coalesces filesystem changes through `_RendererFileEventSource`.
- Runtime events are dispatched through typed handlers that decide whether a
  snapshot or domain-only payload is required.
- Renderer DOM updates are split by payload domain and are mostly driven by
  targeted observers plus animation-frame scheduling.

The remaining issues are architectural:

- Legacy Qt/Tk entry points remain only as compatibility stubs.
- Some full-budget rebuild paths remain correct but expensive.
- File watcher polling fallback remains as a degraded diagnostic path when native
  events are unavailable.

## Architecture Target

### Event Bus

Introduce an internal runtime event bus with typed events:

- `active_session_changed`
- `session_file_changed`
- `settings_changed`
- `budget_window_changed`
- `renderer_layout_changed`
- `overlay_command_received`
- `runtime_error`

The renderer loop should become a dispatcher that reacts to these events. Snapshot
builders should not be called from a periodic loop when no event invalidates their
inputs.

### Error HUD

Add a renderer-injected error panel used by DEBUG mode.

Each runtime error should include:

- `source`
- `severity`
- `code`
- `message`
- `context`
- `first_seen_at`
- `last_seen_at`

Examples:

- `active_session.unmatched_thread`
- `renderer.anchor_missing`
- `cdp.binding_disconnected`
- `jsonl.parse_failed`
- `file_watcher.overflow`
- `overlay.ipc_failed`

### Active Session

Renderer mode should treat the renderer bridge as the active session authority.

If session id/title cannot be mapped to a JSONL path, the HUD should show a visible
debug error instead of selecting the latest JSONL or native title.

OpenAI Codex app-server remains a future authority candidate only. The local
schema POC found loaded-thread, thread-status, and token-usage APIs, but no
field or notification that proves which thread is currently selected in the
Codex App window. Do not use app-server as an implicit fallback for renderer
active-session failures unless a later POC proves current-window active-thread
semantics.

### Usage State

Replace full-file current-session parsing with a tail parser:

- store file id/path, offset, mtime, size
- parse only complete newly appended JSONL rows
- update current request, session totals, task history, heavy rounds, activity trail
- fall back to full rebuild only on truncate, rotation, or parser version changes

Budget aggregation should keep per-file contribution records and replace only the
changed file contribution when possible.

### File Watching

File watching should be reliable before relying on it for responsiveness:

- Windows `ReadDirectoryChangesW` overflow must trigger directory reconciliation.
- macOS renderer mode now uses the narrower watch model: current session file
  plus session index/state db/settings, without recursive sessions tree polling.
- Polling fallback is marked as degraded state in diagnostics.
- Current session file append wakes immediately; all-session tree, settings, and
  mapping changes keep slower debounce/coalescing.

### Renderer Payload

Split payloads by invalidation domain:

- current session
- budget
- settings
- overlay
- diagnostics

The JS side should update only affected DOM sections. The current all-in-one
`__codexUsageHudUpdate(payload)` can remain as a compatibility wrapper during
migration.

### Desktop Overlay

The PySide desktop overlay no longer reads the state file every 160 ms.

Preferred directions:

1. Done: native file watcher in helper wakes only on state-file or directory changes.
2. Done: command file watcher wakes the main process instead of 60 ms command polling.
3. Future option: replace the file-backed channel with direct push IPC if needed.

Overlay errors should enter the shared `runtime_error` channel.

## Migration Phases

1. Baseline and fallback inventory. Done.
2. Runtime event bus and diagnostic model. Done.
3. DEBUG error HUD. Done.
4. Renderer-authoritative active session. Done.
5. Incremental JSONL parser and budget contribution cache. Done.
6. Reliable file watcher reconciliation. Done.
7. Split renderer payloads and remove settings command polling. Done.
8. Watcher-based desktop overlay IPC. Done.
9. Remove or quarantine legacy fallback paths. In progress.

## Validation Gates

Each phase should run:

```powershell
python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q
python -m compileall -q src tests tools
git diff --check
```

Performance-sensitive phases should also run:

```powershell
python tools/measure_renderer_latency.py --markdown-output renderer_latency_baseline.md
```

Review the generated `Regression Budgets` table before accepting the phase.
Manual DEBUG HUD checks live in `docs/HUD_RUNTIME_ACCEPTANCE_CHECKLIST.md`.

## First Implementation Slice

Start with observability, not deletion:

1. Add runtime error model and DEBUG error HUD.
2. Add latency markers around active session changes, JSONL changes, snapshot builds,
   CDP payload pushes, and overlay updates.
3. Produce a fallback inventory report.
4. Convert one fallback path at a time into explicit errors with tests.

This avoids removing fallback behavior blindly while still moving toward the desired
fail-fast architecture.
