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
- Qt/Tk standalone HUD paths remain deprecated and must not become product fallback.
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
- `_renderer_runtime_signature()` skips snapshot rebuilds when important inputs are
  unchanged.
- Renderer DOM layout is mostly driven by targeted observers and animation-frame
  scheduling.

The remaining issues are architectural:

- Current-session JSONL parsing is still full-file per refresh.
- Several paths still degrade into alternate detection mechanisms.
- Desktop overlay IPC is state-file polling based.
- Settings commands still have a localStorage polling path.
- File watcher overflow/degraded states are not visible enough.

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
debug error instead of selecting the latest JSONL or native title. Manual diagnostic
modes can still exist behind explicit flags.

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
- macOS recursive sessions tree watching needs FSEvents or a narrower watch model.
- Polling fallback should be marked as degraded state in diagnostics.
- Current session file append should use a shorter debounce than all-session tree
  changes.

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

The PySide desktop overlay should stop reading the state file every 160 ms.

Preferred directions:

1. Push IPC from main process to helper.
2. Native file watcher in helper that wakes only on state-file changes.
3. Command channel that wakes the main process instead of 60 ms command polling.

Overlay errors should enter the shared `runtime_error` channel.

## Migration Phases

1. Baseline and fallback inventory.
2. Runtime event bus and diagnostic model.
3. DEBUG error HUD.
4. Renderer-authoritative active session.
5. Incremental JSONL parser and budget contribution cache.
6. Reliable file watcher reconciliation.
7. Split renderer payloads and remove settings command polling.
8. Push-based desktop overlay IPC.
9. Remove or quarantine legacy fallback paths.

## Validation Gates

Each phase should run:

```powershell
python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q
python -m compileall -q src tests tools
git diff --check
```

Performance phases should also run a latency harness. The harness does not exist yet;
create it before changing the refresh scheduler so baseline data is available.

## First Implementation Slice

Start with observability, not deletion:

1. Add runtime error model and DEBUG error HUD.
2. Add latency markers around active session changes, JSONL changes, snapshot builds,
   CDP payload pushes, and overlay updates.
3. Produce a fallback inventory report.
4. Convert one fallback path at a time into explicit errors with tests.

This avoids removing fallback behavior blindly while still moving toward the desired
fail-fast architecture.
