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
