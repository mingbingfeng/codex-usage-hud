# Renderer 会话激活快路径与结构化状态语义设计

## Architecture

保留现有本地 command file 作为桌面 overlay 到 daemon 的稳定 transport，优化 command pump 内部的顺序：

```text
overlay click
  -> activateSession command(sessionId, targetTitle, workdir, requestedAt)
  -> FileChangeWatcher wakeup
  -> CDP SessionSwitchController (fast path)
       ├─ success/already-active/switch-requested -> publish activation result + active_session_changed
       └─ transport/target/backend failure -> prepare Codex window once -> retry CDP once
  -> renderer event loop
  -> exact session snapshot + structured WorkStatusItem
  -> work overlay payload/status transition
```

The pet behavior is used only as a semantic reference. The local activation boundary remains `SessionSwitchController.activate_session()` and the local structured `WorkStatusItem` payload.

## Data contracts

### Command input

`activateSession` keeps the existing fields:

- `sessionId`: canonical identity and required primary match when present.
- `targetTitle`, `title`, `workdir`: display/context and compatibility fallback only.
- `requestedAt`: optional wall-clock timestamp used for bounded latency diagnostics.
- `current`: existing refocus/overlay behavior flag.

### Activation event context

`overlay_command_received` remains the command audit event and gains a normalized result projection:

```json
{
  "action": "activateSession",
  "requestedSessionId": "...",
  "activeSessionId": "...",
  "requestedTitle": "...",
  "activeTitle": "...",
  "backend": "cdp",
  "status": "switch-requested",
  "matchedBy": "session-id",
  "ok": true,
  "latencyMs": 42.1
}
```

On successful activation, publish one `active_session_changed` event with `source="work_overlay"`, the active/session key, and the same normalized activation context. The existing runtime handler coalesces this into an active-session snapshot refresh.

### Structured work state

Do not add a second pet-shaped state reader. The existing parser produces `WorkStatusItem`; `work_item_to_overlay_dict()` remains the single projection for `sessionId`, `status`, `statusText`, `lastText`, `progress`, and `updatedAt`. The activation event identifies which session to refresh; it does not manufacture progress or status text.

## Fast-path and fallback rules

1. Call `SessionSwitchController.activate_session()` before `_prepare_codex_window_for_work_overlay_switch()`.
2. If result is successful, return it without window preparation.
3. If result is a recoverable CDP/backend transport failure, prepare the window once and retry once.
4. If the result is a logical target failure (`thread-not-found`, missing target input, title mismatch, or disabled backend), do not prepare/retry as if focus could create the session.
5. Keep existing post-success refocus behavior for user-visible focus, but do not use it as a prerequisite for CDP activation.
6. Publish `active_session_changed` only for successful/active activation; failures remain command/diagnostic events.

## Compatibility and risks

- The existing session switch script, target cache, sidebar reveal, title fallback, and cross-platform backend interfaces remain unchanged.
- A hidden but reachable Codex renderer can now switch before Windows foreground preparation; the existing post-success refocus still brings the window forward when required.
- A failed first CDP attempt may add one bounded window-prepare/retry path, but it is no longer paid on every successful click.
- Latency uses `requestedAt` only for diagnostics; invalid or missing timestamps produce no latency field.
- No private Codex package field or internal IPC name crosses the local boundary.

## Rollback

Revert the CDP-first ordering, activation result projection, and success event emission together. Preserve the existing command file, session switch backend, structured WorkStatusItem projection, and failure diagnostics.
