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
