# Renderer Runtime Contracts

## Scenario: Active session reconciliation after composer send

### 1. Scope / Trigger

- Trigger: a new Codex conversation can remain in `renderer-new-session` after the first message because the default-disabled composer badge disables the existing input watcher.
- Scope: injected renderer active-session reporting through the CDP binding, followed by Python tracker/snapshot/work-overlay refresh.

### 2. Signatures

- JavaScript: `scheduleActiveSessionSendFollowup(reason = "composer-send", expectedSessionId = "")`.
- JavaScript: `postActiveSession(reason, overrideRef)` sends `{sessionId, title, url, reason, newSession, pendingSession, matchedBy, observedAt}`.
- Python: `ActiveSessionTracker.observe_conversation_ref(session_id, title, source="renderer", new_session=False, pending_session=False)`.
- Runtime events: `active_session_changed` wakes the renderer loop; the subsequent refresh may publish `active_work_refresh_requested`.

### 3. Contracts

- Composer submit, and fallback Enter/click handlers when `composerBadgeEnabled` is false, trigger bounded follow-ups at 32, 120, 320, 800, and 1600 ms.
- The form `submit` listener is independent of the optional composer token badge.
- A canonical renderer UUID is the only key allowed to resolve a rollout path. Provisional or unmapped refs remain pending.
- Signature deduplication prevents unchanged follow-ups from producing repeated binding payloads.
- All listeners and timers are removed by `removeActiveSessionWatchers()`.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Badge disabled, composer Enter/click | Active-session follow-up is installed and sent. |
| Badge enabled, composer Enter/click | Existing composer watcher handles it; active-session fallback does not duplicate it. |
| Composer form submit | Active-session follow-up is sent regardless of badge setting. |
| Canonical UUID not yet exposed | Keep `renderer-new-session`/pending; never choose a title or newest rollout fallback. |
| Follow-up unchanged | Signature dedup suppresses duplicate payload. |
| Renderer watchers removed | Timers and submit/keydown listeners are detached. |

### 5. Good/Base/Bad Cases

- Good: first message creates a canonical UUID within the follow-up window; the tracker resolves the exact rollout and the completed work item reaches `status="recent"` without a session click.
- Base: the page remains blank or exposes only `client-new-thread:*`; HUD stays explicitly pending.
- Bad: using a global idle poll, title match, or newest JSONL to force a visible session; this can show another conversation's usage.

### 6. Tests Required

- `tests/test_renderer_hud.py`: assert the fallback composer handler, badge-disabled guard, bounded delays, submit/keydown cleanup, and existing composer watcher reuse markers.
- `tests/test_active_session.py`: preserve provisional/unmapped pending behavior and exact UUID mapping behavior.
- `tests/test_ui.py`: preserve renderer event wakeups, file watcher mapping invalidation, and completed overlay payload behavior.
- Live acceptance: send in a new Codex App conversation and verify canonical ref, rollout path, `status="recent"`, and card-to-circle transition without clicking another session.

### 7. Wrong vs Correct

#### Wrong

```javascript
function ensureComposerInputWatchers() {
  if (!composerBadgeEnabled) return;
  input.addEventListener("keydown", handlers.keydown, true);
}
```

This makes active-session reconciliation depend on an unrelated optional badge.

#### Correct

```javascript
document.addEventListener("submit", submit, true);
if (!composerBadgeEnabled) {
  document.addEventListener("keydown", keydown, true);
}
```

The session contract remains active when the visual badge is disabled, while the existing badge-enabled watcher is reused without duplicate keydown/click handlers.

## Scenario: Exact renderer mapping becomes available

### 1. Scope / Trigger

- Trigger: renderer reports a canonical conversation UUID before Codex commits the matching `threads` row in `state_5.sqlite`.
- Scope: `ActiveSessionTracker` exact lookup, renderer file-event wakeup, and the renderer-loop synthetic `active_session_changed` event.

### 2. Signatures

- `ActiveSessionTracker.path_from_renderer_thread_id(thread_id: str) -> Path | None`.
- `_RendererFileEventSource._should_wake_immediately(reasons: set[str]) -> bool`.
- File-watch reason: `session-map` for `state_5.sqlite` or the session index.

### 3. Contracts

- A missing exact row remains `renderer-pending-map`; no title, newest-file, or recursive-session fallback is permitted.
- Any event set containing `session-map` wakes the renderer loop immediately, even when batched with `settings` or another non-critical reason.
- The loop invalidates the tracker mapping cache before appending its exact-mapping `active_session_changed` event.
- `sessions-root` and settings-only events retain their debounce to avoid refresh storms.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Canonical UUID has no DB row | Render pending and record the normal unmatched diagnostic. |
| State DB/index changes | Clear negative mapping cache and immediately retry that UUID. |
| `session-map` plus settings in one callback | Wake immediately; preserve both reasons for the single loop pass. |
| Sessions-root JSONL append only | Retain normal debounce/incremental refresh behavior. |

### 5. Good/Base/Bad Cases

- Good: the row is committed after the renderer click; the next `session-map` event resolves the selected UUID without another click.
- Base: the row is still absent after the event; HUD stays visibly pending until a later mapping event.
- Bad: retain the negative cache through the mapping event, debounce that event, or guess another session by title.

### 6. Tests Required

- `tests/test_active_session.py`: cache invalidation clears a negative exact-path result.
- `tests/test_ui.py`: `session-map` wakes immediately with a long debounce, including a mixed `session-map`/`settings` batch.
- Renderer regression suite: preserve strict unmapped UUID pending behavior.

### 7. Wrong vs Correct

#### Wrong

```python
return reasons == {"session"}
```

This delays an exact mapping becoming available whenever SQLite/index writes are classified as `session-map`.

#### Correct

```python
return "session" in reasons or "session-map" in reasons
```

The mapping event is latency-critical while unrelated filesystem writes remain debounced.
## Scenario: Pet-inspired session preview and jump boundary

### 1. Scope / Trigger

- Trigger: comparing the Codex App pet overlay with the local PySide6 work bubble to improve progress freshness and target-session activation.
- Scope: local `WorkStatusItem` / desktop overlay payloads and renderer event reconciliation only. Do not import Codex App's private minified modules or IPC.

### 2. Signatures

- Local payload: `work_item_to_overlay_dict(item: WorkStatusItem) -> dict[str, object]`.
- Local command: `activateSession` with `sessionId`, `targetTitle`, `title`, `workdir`, `requestedAt`, and `current`.
- External observation only: pet notifications expose an internal `localConversationId`, `status`, `updatedAtMs`, `waitingRequest`, and opaque `actionPath`; these fields are evidence for behavior comparison, not a local API signature.

### 3. Contracts

- `sessionId` / canonical conversation UUID is the primary identity for local state, overlay matching, and session switching.
- `targetTitle` and `workdir` are display/context fields. They must not replace an unavailable canonical UUID by title-matching or newest-rollout guessing.
- Progress text must come from structured `status`, `statusText`, `lastText`, `progress`, and `updatedAt` fields produced by the existing snapshot/parser path, not from a second renderer DOM reader.
- A renderer active-session event wakes the runtime event bus and refreshes the exact current session/work overlay. Bounded follow-up timers are allowed only for provisional new-session reconciliation.
- The external pet's `open-in-main-window` / `actionPath` behavior may be used as a reverse-engineering reference, but the local stable jump boundary remains `activateSession(sessionId)` through the existing session-switch controller.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Canonical session ID available | Refresh exact session and allow `activateSession`. |
| `client-new-thread:*`, blank, or unmapped ID | Keep explicit pending state; do not title-match. |
| Structured activity/status update | Update preview through the event-driven snapshot path. |
| CDP target unavailable | Record the renderer/CDP error and keep the bounded fallback; do not copy private pet IPC. |
| External pet asset or action name changes | Local behavior remains governed by the local contract and tests. |

### 5. Good/Base/Bad Cases

- Good: one canonical session ID flows from renderer binding to tracker, snapshot, `statusText`/`progress`, and overlay click command.
- Base: the pet package confirms an opaque target path, but local CDP is absent; use the local session switch backend and report the missing live gate.
- Bad: hard-code a private `actionPath`, use title/workdir as identity, or add an idle whole-document poll to imitate the pet's freshness.

### 6. Tests Required

- `tests/test_renderer_hud.py`: assert active-session event and bounded follow-up contracts, including pending canonical-ID behavior.
- `tests/test_active_session.py`: assert exact UUID mapping and rejection of provisional/unmatched fallback.
- `tests/test_ui.py`: assert `statusText`/`progress` payload propagation, `card_to_completed` transitions, and `activateSession` fields.
- Live acceptance, when a CDP target is available: record the target session ID, status transitions, and whether a target-session click required a second Codex App interaction.

### 7. Wrong vs Correct

#### Wrong

```python
# Treating a private external route as a stable public contract.
open_codex_route(item["actionPath"])
```

#### Correct

```python
# Keep the local stable command boundary and canonical identity.
write_overlay_command({"action": "activateSession", "sessionId": item["sessionId"]})
```

## Scenario: App-server observer capability gate

### 1. Scope / Trigger

- Trigger: considering Codex app-server as a renderer HUD event source for live thread, turn, item, approval, or token-usage state.
- Scope: capability discovery and observer safety. This contract does not make app-server the active-session authority and does not replace the rollout/session-file `WorkStatusItem` path.

### 2. Signatures

- Persisted reads: `thread/list`, `thread/read`, `thread/turns/list`, `thread/items/list`, and `thread/loaded/list`.
- Stateful join: `thread/resume({ threadId, ...optionalOverrides })` followed by `thread/unsubscribe({ threadId })`.
- Required future capability: a versioned read-only subscribe/attach operation that cannot change stream role, thread configuration, approval routing, permissions, model, cwd, or runtime workspace roots.

### 3. Contracts

- Treat a Desktop-owned `stdio://` app-server as process-private unless that exact process advertises an external listener. Starting another app-server process creates another in-memory server and is not attachment.
- `thread/list` and `thread/read` prove persisted visibility only. They do not prove that the client receives another process's live notifications or loaded-thread state.
- Never call `thread/resume` solely to observe HUD status. In the generated 0.144.2 protocol it rejoins a running thread and accepts state-affecting overrides, so it is not a read-only subscription boundary.
- Do not use Electron IPC, renderer stores, worker bridges, DOM, or React Fiber to fill a missing app-server observer capability.
- App-server can become a HUD authority only after capability negotiation and live non-interference tests pass on Windows and macOS. Failure must leave the existing event-driven snapshot path unchanged.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Desktop app-server exposes only stdio | Report `observer-unavailable`; do not attempt to attach to inherited handles. |
| Separate process can read a canonical thread but reports `notLoaded` | Classify as persisted-only visibility; do not claim real-time support. |
| Schema has `unsubscribe` but live entry requires `resume` | Reject the integration as stateful. |
| Windows daemon/control socket is unsupported | Reject daemon/proxy as a cross-platform HUD transport. |
| Versioned read-only attach exists in a future release | Run identity, ordering, disconnect, role, and cross-platform acceptance before enabling it. |
| Observer changes owner/follower, active session, or thread configuration | Stop immediately, disconnect, and keep app-server disabled. |

### 5. Good/Base/Bad Cases

- Good: a future Desktop process advertises a documented observer endpoint; HUD attaches read-only, receives canonical thread events, survives disconnect, and leaves owner/follower unchanged on Windows and macOS.
- Base: an isolated app-server can list/read rollout history but has no loaded threads or passive live events; retain the local snapshot/watch path.
- Bad: call `thread/resume` from HUD to make notifications appear, or inject into the Desktop renderer's private conversation store.

### 6. Tests Required

- Capability unit test: no read-only attach method or no external endpoint keeps app-server integration disabled.
- Protocol test: list/read success without passive events remains `persisted-only`, never `live`.
- Non-interference integration test: observer connect/disconnect does not change active session, stream role, approval routing, or thread configuration.
- Cross-platform acceptance: exercise the same capability and fallback on Windows and macOS before changing the default authority.

### 7. Wrong vs Correct

#### Wrong

```python
# Resume is a stateful rejoin, not an observation API.
client.request("thread/resume", {"threadId": session_id})
```

#### Correct

```python
capability = probe_read_only_app_server_observer()
if not capability.cross_platform_safe:
    keep_existing_snapshot_authority()
```
