# Renderer Runtime Contracts

## Scenario: Deterministic renderer session following

### 1. Scope / Trigger

- Trigger: the user selects an existing Codex sidebar row or creates a conversation while the renderer identity, local persistence, snapshot build, and HUD application can complete at different times.
- Scope: renderer observation, CDP binding delivery, `ActiveSessionTracker` reconciliation, visible-first snapshot delivery, deferred work-overlay aggregation, and failure feedback.

### 2. Signatures

- JavaScript: `postActiveSession(reason, overrideRef)` sends `{sessionId, rendererSessionId, selectionSeq, title, url, reason, newSession, pendingSession, matchedBy, observedAt}`.
- Python: `ActiveSessionTracker.observe_conversation_ref(..., renderer_session_id="", selection_seq=0, observed_at_ms=0) -> bool`.
- Snapshot: `ParsedSession.selection_seq`, `selection_observed_at_ms`, `follow_state`, `follow_reason`, and `follow_timing` cross the tracker/parser/UI boundary unchanged. `followTiming` attributes renderer observation, Python receipt/resolution, snapshot start/build, and renderer application.
- Identity/cache: session payloads carry canonical `sessionId`, exact raw `rendererSessionId`, and `cachedPreview=false`. A renderer-only preview sets `cachedPreview=true` for the current sequence without acknowledging it.
- Runtime events: `active_session_changed` produces the visible `sessionSwitch` domain; a successful visible update schedules `active_work_refresh_requested`.
- Work overlay: `_refresh_visible_current_work_item(context, items, snapshot) -> list[WorkStatusItem]` projects a refreshed current-session snapshot onto an already-visible bubble before recent-work aggregation completes.
- Critical transport: `_RendererBinding(..., retry_same_target=True)` is used only for the active-session binding.
- Cost preview: `JsonlSessionParser.parse_file_tail_preview(...)` may expose cumulative token counters, while `SessionSnapshotCache.snapshot_for(...)` owns stabilization of the last complete `cumulative_cost_usd` during an append-triggered hydrate.

### 3. Contracts

- Every genuinely different renderer selection receives a monotonically increasing `selectionSeq`. Work from an older non-zero sequence cannot replace the current selection.
- Renderer reinjection preserves the previous page-realm selection/applied counters. Both startup `currentSession` and lightweight `sessionSwitch` payloads raise the local selection counter to their applied sequence before the next click. A script reload must never restart at sequence 1 while Python already holds a larger sequence.
- Preserve `rendererSessionId` until the selection is superseded. A provisional `client-new-thread:*` ID is pending identity, not proof of a new conversation.
- Provisional recovery is allowed only when one exact-title entry in `session_index.jsonl` has the same canonical ID as an exact `state_5.sqlite` thread row and that row points to an existing rollout. Zero candidates remain `awaiting-persistence`; multiple candidates remain `ambiguous-persisted-identity`.
- A sidebar click updates the existing request line immediately with `reading-session-data`; missing renderer binding reports `renderer-channel-unavailable`. Python replaces that provisional feedback with a more specific follow reason when available.
- Binding acceptance is not application acknowledgement. The renderer suppresses a duplicate only after `activeSessionAppliedSeqName >= selectionSeq`; an unchanged resend for the current sequence must wake Python so a failed first HUD update can retry.
- The first active-session refresh forces cached budget reuse and `refresh_active_work_items=False`, even when unrelated session/file events were coalesced into the same tick. Only after the visible `sessionSwitch` succeeds may `active_work_refresh_requested` rebuild recent work, and its result is accepted only for the current sequence.
- Deferred active-work aggregation has a 1.2 second selection quiet window. Another selection inside that window sends another visible-first payload and resets the deadline; file events may refresh lightweight state but cannot start the multi-file scan before the deadline.
- `active_work_items_for_snapshot` runs in `_RendererActiveWorkPump`, never on the renderer loop thread. The pump coalesces pending requests and returns sequence-bound results; the loop applies only a result whose sequence still matches both tracker and latest snapshot.
- A full current-session refresh must replace the matching already-visible work item from the fresh snapshot before requesting `_RendererActiveWorkPump`. A `task_complete` file event therefore publishes `recent` without waiting for the multi-file scan; this fast path must not create a previously hidden completed bubble.
- The final work-overlay publication cache rejects an item whose effective `updated_at` predates the cached item for the same ID. This prevents an older pump result from reverting `recent` to active, while a newer user steer remains allowed to restore the active card.
- Warm `SessionSnapshotCache` hits shallow-clone only fields mutated by runtime enrichment (`request`, warnings, active-work list, timing). Parsed request/tool history remains shared read-only; `copy.deepcopy` is forbidden on the session-switch path because large historical sessions made it cost hundreds of milliseconds.
- The visible-first build copies budget totals/warnings from the latest snapshot and skips the live app-error DOM probe. `UsageSummaryCache.summarize(...)` and `_visible_app_error(...)` are deferred because provider-filter aggregation and CDP probing were each measured in the hundreds of milliseconds on a real App switch.
- The visible `sessionSwitch` payload is sent before work-overlay updates or current-session watcher reconfiguration. Both operations are deferred with the non-lightweight refresh because rebuilding Windows watcher workers was measured on the click-to-HUD path.
- Renderer payload `Runtime.evaluate` reuses the persistent active-session binding websocket with command-ID response routing. If that channel is not ready or disconnects, `_send_update` falls back to the existing ephemeral CDP command and the critical binding recovery contract remains active.
- Codex handles a sidebar click and its React route transition in the same renderer task. A CDP update sent from the binding callback can therefore remain queued for roughly 150-300 ms even when Python snapshot work and CDP round trips are otherwise fast. The renderer keeps up to 48 exact identity keys for previously confirmed session payloads and applies a cache hit synchronously in the capture-phase click handler before Codex route work begins.
- Renderer preview lookup uses only `rendererSessionId` or canonical `sessionId`; title, prefix, and newest-session lookup are forbidden. A preview must retain the new `selectionSeq` but must not raise `activeSessionAppliedSeqName`, suppress same-sequence follow-ups, or replace the later Python-authoritative payload.
- A bounded tail must clear its tail-window `cumulative_cost_usd`; those records cannot prove the whole-session cost. For append-only growth of the same file identity, `SessionSnapshotCache` keeps the prior complete cost until incremental hydration publishes the replacement.
- The top `本会话` amount is the confirmed session cumulative cost. Live request tokens may be included while a request runs, but `_request_cost(snapshot)` must not be added to that amount.
- A critical active-session binding may reconnect to the same CDP target after disconnect. Auxiliary bindings retain same-target suppression to avoid idle retry loops.
- The runtime remains event-driven. Selection-specific timers are bounded; no idle active-session poll is permitted.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Canonical ID and rollout already exist | Parse only the selected session and apply `sessionSwitch` immediately. |
| Provisional ID has one fully verified persisted candidate | Confirm that canonical session without another click and keep `newSession=false`. |
| Provisional ID has zero or multiple candidates | Stay pending and show `awaiting-persistence` or `ambiguous-persisted-identity`; never guess. |
| A later `session-map` event arrives | Invalidate mapping caches and retry the retained current identity immediately. |
| A/B work completes after selection C | Reject it when its non-zero sequence differs from the tracker sequence. |
| HUD script is reinjected after sequence N | Preserve/restore N so the next renderer observation is greater than or equal to the Python tracker generation. |
| First session payload is transported but not applied | Bounded duplicate delivery for the same sequence wakes another visible refresh. |
| Active-session binding disconnects | Preserve the current selection, show `renderer-channel-unavailable`, and permit same-target recovery. |
| Visible session update succeeds | Schedule recent-work aggregation as a separate event. |
| Current visible session appends `task_complete` | Publish its existing bubble as `recent` from the refreshed snapshot before recent-work aggregation returns. |
| Older active-work result arrives after current completion | Keep the newer completed item; do not regress to a square active card. |
| A newer user steer follows completion | Accept the newer timestamp/task identity and restore the active card. |
| Append-only file growth has a complete cached snapshot | Keep the cached complete session cost until incremental hydration publishes the new complete cost. |
| Cold bounded tail excludes earlier token events | Treat its tail-window cost as unknown; never label it as the cumulative session cost. |
| Request is still running | Live token estimates may appear in `本会话`, but its amount excludes the running round estimate. |
| Exact selected ID has a confirmed renderer payload cache entry | Apply the visible preview synchronously, keep the sequence unacknowledged, then replace it with the Python-authoritative payload. |
| Exact selected ID has no cache entry | Show `reading-session-data` immediately and wait for the normal strict mapping/snapshot/CDP path. |
| Session-file writes share the selection tick | Send the visible cached-budget/session-only payload first; defer the file-derived aggregates. |
| Another selection arrives during the work quiet window | Apply the new selection first and move the deferred-work deadline forward. |
| Background work result belongs to an older sequence | Discard it without changing the selected session or overlay. |

### 5. Good/Base/Bad Cases

- Good: click A -> B -> C; exact cache hits repaint in the capture handler, C remains the final authoritative selection, and later work aggregation cannot repaint A or B.
- Good: a visible active bubble receives `task_complete`; the session-file event turns it into `recent` immediately, and a late older pump result cannot turn it square again.
- Good: a complete session costs `$1`, an appended round costs `$2`, and the visible sequence is `$1` while hydrating then `$3` when complete, never `$2`.
- Base: Codex exposes `client-new-thread:*` before persistence; HUD remains explicit pending and converges after the exact local evidence appears.
- Base: a cold partial tail has no prior complete cost; it remains explicitly loading and does not publish the sum of tail-window rounds as a session total.
- Bad: title-key the renderer cache, mark a cached preview as applied, discard the provisional ID after one miss, accept transport delivery as application ACK, synchronously parse 16 recent files before the visible update, or reconnect every auxiliary binding forever.
- Bad: add `_request_cost(snapshot)` to the top `本会话` amount or retain the partial parser's tail-only `cumulative_cost_usd`; both make the amount jump between round and session totals.
- Bad: leave the current visible bubble unchanged until `_RendererActiveWorkPump` finishes, or blindly let an older pump result overwrite a newer completed item.

### 6. Tests Required

- `tests/test_active_session.py`: initial provisional miss followed by mapping reconciliation, duplicate-title ambiguity, stale sequence rejection, and disconnect state preservation.
- `tests/test_renderer_hud.py`: raw/canonical identity fields, applied-sequence dedup, immediate click feedback, same-target critical binding retry, and disconnect callback payload.
- `tests/test_renderer_hud.py`: exact-ID payload cache markers, `cachedPreview` ACK exclusion, and authoritative payloads carrying both identity fields.
- `tests/test_ui.py`: visible-first scan exclusion, separate work refresh, stale snapshot rejection, unchanged current-sequence retry, and specific runtime diagnostics.
- `tests/test_ui.py`: a `session_file_changed` renderer-loop refresh publishes the current visible item as `recent` before any pump result; an older active result is rejected and a newer resumed task is accepted.
- `tests/test_parser.py`: a bounded partial tail clears its non-authoritative cumulative cost.
- `tests/test_ui.py`: an append preview preserves the last complete cost until hydration.
- `tests/test_renderer_hud.py`: a running request cannot add its round estimate to the top session amount.
- Live acceptance: alternate canonical rows at least 50 times, exercise rapid A -> B -> C, and select a persisted provisional row without a second click.

### 7. Wrong vs Correct

#### Wrong

```python
changed = tracker.observe_conversation_ref(**payload)
if changed:
    publish_active_session_changed()
snapshot = build_snapshot(refresh_active_work_items=True)
```

This drops an unchanged retry after a failed first HUD application and puts the multi-file work scan on the click path.

#### Correct

```python
changed = tracker.observe_conversation_ref(**payload)
if changed or payload.selection_seq == tracker.selection_seq:
    publish_active_session_changed()
snapshot = build_snapshot(refresh_active_work_items=False)
# After visible application succeeds:
publish("active_work_refresh_requested")
```

The current sequence remains retryable until renderer application, while recent work is refreshed after the user-visible session fields.

For non-selection session-file events, refresh the already-visible current item from the fresh snapshot before starting background aggregation. Treat the pump as discovery/reconciliation, not as the completion-state authority for that item.

For append-triggered cost previews, the correct rule is:

```python
# Wrong: this is only the sum of token events inside the bounded tail.
preview.confirmed.cumulative_cost_usd = tail_window_cost
top_session_cost += running_request_cost

# Correct: keep the last complete value until incremental hydration replaces it.
preview.confirmed.cumulative_cost_usd = cached.confirmed.cumulative_cost_usd
top_session_cost = confirmed_session_cost
```

For a renderer cache hit, the correct preview rule is:

```javascript
const cached = cache.get(rendererSessionId); // exact identity only
applySessionSwitch({ ...cached, selectionSeq, cachedPreview: true });
// Do not advance appliedSeq. Python confirmation owns the ACK.
```

## Scenario: Visible App error classification

### 1. Scope / Trigger

- Trigger: CDP DOM probing finds visible alert-, toast-, notification-, or error-styled content and may project it into the current request and work overlay.

### 2. Signatures

- CDP payload: `appError: string` in `CdpDomSnapshot`.
- Normalization boundary: `snapshot_from_evaluate_result(...) -> CdpDomSnapshot | None`.

### 3. Contracts

- Visual severity is candidate evidence, not proof of a failed request. A red `role="alert"` surface may be an informational permission advisory.
- The Codex `Full access is on` advisory, including its bounded/truncated risk copy, normalizes to `app_error=""` before `build_snapshot()` can set `request.status="error"`.
- Confirmed request failures such as retry-limit, rate-limit, network, and server errors retain their existing `appError` text.

### 4. Validation & Error Matrix

| Visible content | Required result |
|---|---|
| `Full access is on` permission advisory | Empty `app_error`; no request or work-overlay error transition. |
| Advisory truncated after `ChatGPT will be able to run commands, use the internet` | Empty `app_error`. |
| `429 Too Many Requests` or `exceeded retry limit` | Preserve the error text. |

### 5. Good/Base/Bad Cases

- Good: Codex cold start renders the full-access warning; session bubbles and the bottom HUD keep the JSONL-derived request state.
- Base: no visible error candidate produces an empty `appError`.
- Bad: treat every red alert/error icon as a request failure and copy permission guidance into `request.error`.

### 6. Tests Required

- `tests/test_cdp_probe.py`: feed the complete full-access advisory through `snapshot_from_evaluate_result()` and assert `snapshot.app_error == ""`.
- Keep a positive assertion that a real `429`/retry-limit message remains unchanged.

### 7. Wrong vs Correct

```python
# Wrong: DOM styling alone becomes runtime request state.
snapshot.request.error = value["appError"]

# Correct: normalize known non-error advisories at the CDP payload boundary.
snapshot = snapshot_from_evaluate_result(result)
assert snapshot.app_error == ""
```

## Scenario: Active session reconciliation after composer send

### 1. Scope / Trigger

- Trigger: a new Codex conversation can remain in `renderer-new-session` after the first message because the default-disabled composer badge disables the existing input watcher.
- Scope: injected renderer active-session reporting through the CDP binding, followed by Python tracker/snapshot/work-overlay refresh.

### 2. Signatures

- JavaScript: `scheduleActiveSessionSendFollowup(reason = "composer-send", expectedSessionId = "")`.
- JavaScript: `postActiveSession(reason, overrideRef)` sends canonical and raw identities plus `selectionSeq` and observation timing.
- Python: `ActiveSessionTracker.observe_conversation_ref(..., renderer_session_id="", selection_seq=0, observed_at_ms=0)`.
- Parser: `JsonlSessionParser.latest_task_segment_start(records, task_started_index) -> int` scopes terminal markers to the latest non-empty `user_message`, top-level `compacted`, or `event_msg.context_compacted` continuation boundary in the task.
- Runtime events: `active_session_changed` wakes the renderer loop; the subsequent refresh may publish `active_work_refresh_requested`.

### 3. Contracts

- Composer submit, and fallback Enter/click handlers when `composerBadgeEnabled` is false, trigger bounded follow-ups at 32, 120, 320, 800, and 1600 ms.
- The form `submit` listener is independent of the optional composer token badge.
- A canonical renderer UUID is the only key allowed to resolve a rollout path. Provisional or unmapped refs remain pending.
- Signature deduplication suppresses unchanged follow-ups only after the corresponding `selectionSeq` has been applied by the HUD.
- A steered `user_message` may continue the current task without another `task_started`. In JSONL record order, it invalidates earlier `final_answer`, `task_complete`, and `turn_aborted` markers until a new terminal marker appears after that message.
- A context-compaction handoff may emit a synthetic `final_answer` and then resume the same task without another user message. `compacted` and `context_compacted` invalidate terminal markers before the handoff but preserve the task start, prompt, ordinal, and usage history.
- Work-overlay completion must use the latest continuation segment, not the latest task-start segment alone; otherwise an active card can revert to `status="recent"` while a steered or compacted continuation is still running.
- All listeners and timers are removed by `removeActiveSessionWatchers()`.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Badge disabled, composer Enter/click | Active-session follow-up is installed and sent. |
| Badge enabled, composer Enter/click | Existing composer watcher handles it; active-session fallback does not duplicate it. |
| Composer form submit | Active-session follow-up is sent regardless of badge setting. |
| Canonical UUID not yet exposed | Keep `renderer-new-session`/pending; never choose a title or newest rollout fallback. |
| Follow-up unchanged, sequence not applied | Repeat the bounded binding payload so Python can retry the visible update. |
| Follow-up unchanged, sequence applied | Signature dedup suppresses the duplicate payload. |
| Non-empty user message follows a terminal marker without a new task start | Clear parsed task completion, abort, and final-answer state; keep or restore the work item as a card. |
| `compacted` / `context_compacted` follows a synthetic handoff final | Clear terminal markers before the compaction boundary; resumed CLI activity remains a card. |
| A real final answer/task completion follows the compaction boundary | Accept only that later terminal and transition the card to `status="recent"`. |
| A new final answer or task completion follows that user message | Accept the later marker and transition the card to `status="recent"`. |
| Renderer watchers removed | Timers and submit/keydown listeners are detached. |

### 5. Good/Base/Bad Cases

- Good: first message creates a canonical UUID within the follow-up window; the tracker resolves the exact rollout and the completed work item reaches `status="recent"` without a session click.
- Good: a user steers immediately after a final answer; the existing circle returns to a card and stays non-terminal until the continuation finishes.
- Good: context compaction writes a handoff summary, the next model continues with commentary/tools, and the same CLI item remains a square until its later real final.
- Base: the page remains blank or exposes only `client-new-thread:*`; HUD stays explicitly pending.
- Bad: using a global idle poll, unconstrained title match, or newest JSONL to force a visible session; this can show another conversation's usage.
- Bad: treating any terminal marker after the latest `task_started` as current after a later user message; steered input does not reliably emit another `task_started`.
- Bad: treating the `final_answer` immediately before `compacted` as task completion; it is transport for the next context, not a user-visible terminal.

### 6. Tests Required

- `tests/test_renderer_hud.py`: assert the fallback composer handler, badge-disabled guard, bounded delays, submit/keydown cleanup, and existing composer watcher reuse markers.
- `tests/test_active_session.py`: preserve provisional/unmapped pending behavior and exact UUID mapping behavior.
- `tests/test_parser.py`: assert a user steer or context-compaction boundary invalidates prior completion/final-answer markers and later markers restore them.
- `tests/test_ui.py`: preserve renderer event wakeups, file watcher mapping invalidation, completed overlay payload behavior, and `recent -> active` recovery after steered input or context compaction without a new task start.
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

For terminal work state, the correct record-boundary rule is:

```python
# Wrong: this can reuse a final answer from before a steered user message.
start_index = task_started_index + 1
final_answer_at = latest_final_answer(records[start_index:])

# Correct: terminal markers must belong to the latest user-message segment.
start_index = latest_task_segment_start(records, task_started_index)
final_answer_at = latest_final_answer(records[start_index:])
```

The same helper must return the first record after the latest compaction marker;
checking only user messages leaves a handoff summary visible as a false completion.

## Scenario: Exact renderer mapping becomes available

### 1. Scope / Trigger

- Trigger: renderer reports a canonical UUID before the matching DB row exists, or retains a provisional row while Codex commits its canonical index/DB records.
- Scope: `ActiveSessionTracker` exact lookup, renderer file-event wakeup, and the renderer-loop synthetic `active_session_changed` event.

### 2. Signatures

- `ActiveSessionTracker.path_from_renderer_thread_id(thread_id: str) -> Path | None`.
- `_RendererFileEventSource._should_wake_immediately(reasons: set[str]) -> bool`.
- File-watch reason: `session-map` for `state_5.sqlite` or the session index.

### 3. Contracts

- A missing canonical exact row remains `renderer-pending-map`; newest-file, prefix, and recursive-session fallbacks are forbidden.
- A retained provisional identity may use only the exact-title, unique-index, exact-DB-ID, existing-rollout recovery contract defined above.
- Any event set containing `session-map` wakes the renderer loop immediately, even when batched with `settings` or another non-critical reason.
- The loop invalidates the tracker mapping cache before appending its exact-mapping `active_session_changed` event.
- `sessions-root` and settings-only events retain their debounce to avoid refresh storms.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Canonical UUID has no DB row | Render pending and record the normal unmatched diagnostic. |
| State DB/index changes | Clear negative mapping cache and immediately retry the retained canonical or provisional identity. |
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
- Settings command: `openUsageInsightsSession(sessionId, targetTitle, workdir)` calls `SessionSwitchController.activate_session(..., backend_names=("cdp",))`.
- External observation only: pet notifications expose an internal `localConversationId`, `status`, `updatedAtMs`, `waitingRequest`, and opaque `actionPath`; these fields are evidence for behavior comparison, not a local API signature.

### 3. Contracts

- `sessionId` / canonical conversation UUID is the primary identity for local state, overlay matching, and session switching.
- `targetTitle` and `workdir` are display/context fields. They cannot replace an unavailable canonical UUID except inside the constrained provisional recovery contract above; newest-rollout guessing remains forbidden.
- Progress text must come from structured `status`, `statusText`, `lastText`, `progress`, and `updatedAt` fields produced by the existing snapshot/parser path, not from a second renderer DOM reader.
- A renderer active-session event wakes the runtime event bus and refreshes the exact current session/work overlay. Bounded follow-up timers are allowed only for selection application acknowledgement and provisional new-session reconciliation.
- The external pet's `open-in-main-window` / `actionPath` behavior may be used as a reverse-engineering reference, but the local stable jump boundary remains `activateSession(sessionId)` through the existing session-switch controller.
- A settings Top10 jump is CDP-only. CDP may expand the exact workdir project, then bounded known nested controls (`show more` or explicit sidebar section toggles), rechecking the canonical UUID after every expansion. It must stop with an error when the UUID remains unavailable.
- Settings Top10 jumps must never fall through to a keyboard/search/clipboard backend. A stale search shortcut can leave focus in the composer and paste the target title into the user's draft.
- When a canonical UUID is supplied, title matching is not an identity fallback. A jump succeeds only after the renderer's active row reports the requested canonical UUID.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Canonical session ID available | Refresh exact session and allow `activateSession`. |
| Top10 target is inside a collapsed project or nested `show more` list | Expand known layers for a bounded number of cycles, requery the exact UUID, click once, and confirm the active UUID. |
| Top10 canonical UUID is still absent after bounded expansion | Return a CDP error and leave the composer/clipboard untouched; do not use keyboard search. |
| `client-new-thread:*`, blank, or unmapped ID | Keep explicit pending state unless one candidate passes every constrained provisional-recovery check. |
| Structured activity/status update | Update preview through the event-driven snapshot path. |
| CDP target unavailable | Record the renderer/CDP error and keep the bounded fallback; do not copy private pet IPC. |
| External pet asset or action name changes | Local behavior remains governed by the local contract and tests. |

### 5. Good/Base/Bad Cases

- Good: one canonical session ID flows from renderer binding to tracker, snapshot, `statusText`/`progress`, and overlay click command.
- Good: a Top10 target hidden behind both a collapsed project and `show more` is expanded and activated by exact UUID through CDP, then the active UUID is confirmed.
- Base: the pet package confirms an opaque target path, but local CDP is absent; use the local session switch backend and report the missing live gate.
- Bad: hard-code a private `actionPath`, use title/workdir as identity, let Top10 fall through to a keyboard shortcut, or add an idle whole-document poll to imitate the pet's freshness.

### 6. Tests Required

- `tests/test_renderer_hud.py`: assert active-session event and bounded follow-up contracts, including pending canonical-ID behavior.
- `tests/test_active_session.py`: assert exact UUID mapping and rejection of provisional/unmatched fallback.
- `tests/test_ui.py`: assert `statusText`/`progress` payload propagation, `card_to_completed` transitions, and `activateSession` fields.
- `tests/test_cdp_probe.py`: assert project and nested expansion are bounded, UUID matching precedes title matching, and activation waits for the requested active UUID.
- `tests/test_platforms.py` and `tests/test_ui.py`: assert `backend_names=("cdp",)` skips the keyboard backend for `openUsageInsightsSession` while ordinary overlay activation keeps its existing fallback policy.
- Live acceptance, when a CDP target is available: record the target session ID, status transitions, and whether a target-session click required a second Codex App interaction.

### 7. Wrong vs Correct

#### Wrong

```python
# A failed CDP lookup falls through to an old shortcut and can paste into the composer.
controller.activate_session(session_id=session_id, title=title)
```

#### Correct

```python
# Top10 stays on the canonical CDP path and fails closed.
controller.activate_session(
    session_id=session_id,
    title=title,
    workdir=workdir,
    backend_names=("cdp",),
)
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

## Scenario: Concurrent work-overlay shape transitions

### 1. Scope / Trigger

- Trigger: one event-driven overlay refresh changes more than one visible work item
  between a card and a completed badge, which is common when notification-only
  providers are visible alongside the primary provider.
- Scope: `work_overlay_qt.py` visible-item state, animation scheduling, and
  top-level interaction hotspots only.

### 2. Signatures

- `_transition_changes(old_items, new_items) -> list[tuple[str, str]]`.
- `_defer_other_transition_items(old_items, new_items, transition_item_id)`.
- `_matched_overlay_item_records(records, items)` pairs grouped widget records
  with visible payload items by widget kind and stable item ID.
- `_interactive_hotspot_opacity(base_opacity, hovered) -> float`.
- `WORK_OVERLAY_HOTSPOT_HIT_ALPHA` and
  `WORK_OVERLAY_HOTSPOT_HOVER_ALPHA` define native hit-test and own-hover
  visibility independently from the parent bubble opacity.

### 3. Contracts

- Preserve the existing animation path and timing for the first shape change.
- Keep every later changing item at its currently displayed kind until its own
  turn; do not mark the entire target list as already displayed.
- On transition completion, re-render the latest raw payload so the next queued
  item can transition. Provider is not part of the animation identity; `id` is.
- Completed widgets are built before card widgets, so `_item_widgets` is grouped
  by shape and is not guaranteed to have the same order as `visible_items`.
  Steady-state payload refreshes and geometry record arrays must match by
  `(widget kind, item id)`; positional `zip(records, visible_items)` is forbidden.
- If every visible item cannot be matched to one existing record, rebuild the
  widget structure instead of applying a partial update to stale records.
- Already visible work items survive a transient candidate-scan omission until
  an explicit terminal state, the activity stale deadline, or normal
  provider/item-limit selection removes them.
- Active cards are ordered by `sessionStartedAt` descending, with task/update
  timestamps used only as fallbacks, so a newer session stays above an older
  session across payload refreshes.
- The visible workdir hover layer is card-only. Completed badges retain their
  painted arc workdir text and use a visually inert native hotspot over that arc
  to activate the corresponding App session; the hotspot must not draw a
  rectangular workdir layer or overlap the check/dismiss hotspot.
- The card QLabel remains the only workdir text renderer. Its top-level hotspot
  stays inside the exact label bounds and may paint only a background or
  underline; it must not redraw, elide, or widen across adjacent footer content.
- Transparent top-level hotspots must retain non-zero effective alpha at the
  configured `0.22` bubble-hover opacity. Their own hover raises only that
  hotspot to `WORK_OVERLAY_HOTSPOT_HOVER_ALPHA`; the recurring pointer sync may
  update the stored base opacity but cannot dim a currently hovered hotspot.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| One card-to-badge change | Existing single-transition behavior is unchanged. |
| Multiple shape changes in one payload | Apply one transition, then apply the next from the deferred display state. |
| New/removed item only | Rebuild without inventing a shape transition. |
| Background cards and completed sessions share one unchanged payload structure | Each background item stays in its card record and each completed session stays in its badge record across content-only refreshes. |
| A visible item has no matching kind/ID record | Rebuild the structure; never reuse another item's widget by list position. |
| Card workdir hover | Keep the full QLabel text and highlight only its exact bounds; do not paint a second string or expand over the status label. |
| Completed badge workdir | Keep only the painted arc text; its invisible native hotspot activates the App session without drawing a rectangular layer. |
| Bubble opacity is `0.22` | `WindowFromPoint` still resolves the workdir/check hotspot rather than the window beneath it. |
| Completed check hover/click | Raise the hotspot to its own hover opacity, show a clear outline, and invoke the existing annihilation dismissal. |

### 5. Good/Base/Bad Cases

- Good: `custom` completes while `muyuan` resumes; both animate in sequence.
- Good: three background cards followed by two completed sessions may build
  records as `completed, completed, card, card, card`; subsequent refreshes
  still update all five records by their own IDs.
- Good: a low-opacity card keeps one full workdir label, while the completed
  check hotspot remains natively hit-testable and becomes obvious on hover.
- Base: only one visible session changes kind; it follows the original path.
- Bad: assigning the full target list to `previous_visible_items` before the
  first animation ends, which causes later changes to be redrawn abruptly.
- Bad: zipping the grouped widget-record list with logical visible-item order;
  completed badges then display background titles while completed sessions are
  painted into square cards.
- Bad: using alpha `1` for a layered hotspot at bubble opacity `0.22`, or
  redrawing workdir text in the hotspot window; the former can become native
  click-through and the latter creates duplicate or ellipsized text.

### 6. Tests Required

- `tests/test_ui.py`: assert that simultaneous card-to-completed and
  completed-to-card changes leave the later item deferred, then detectable on
  the following pass.
- `tests/test_ui.py`: assert a live-like mixed list of background cards and
  completed sessions matches every grouped widget record to the same item ID.
- `tests/test_ui.py`: assert completed workdirs do not create an external link
  layer, idle card hotspots keep the QLabel anchor size, and own-hover opacity
  overrides the low bubble opacity.
- Windows live source-helper gate: at bubble hover opacity `0.22`, verify
  `WindowFromPoint` resolves both hotspot HWNDs, then use a real mouse click on
  the completed check and assert the annihilation window appears and the badge
  is dismissed.

### 7. Wrong vs Correct

#### Wrong

```python
previous_visible_items = target_items
start_transition(first_change)
```

#### Correct

```python
transition_items = defer_other_transition_items(displayed_items, target_items, first_id)
start_transition(first_change, displayed_items, transition_items)
previous_visible_items = transition_items
```

#### Wrong: update grouped widgets by logical list position

```python
for record, item in zip(item_widgets, visible_items):
    update_item_widget(record, item)
```

#### Correct: preserve item identity across shape grouping

```python
for record, item in matched_overlay_item_records(item_widgets, visible_items):
    update_item_widget(record, item)
```

#### Wrong: layered hotspot becomes click-through

```python
fill = QColor(255, 255, 255, 1)
hotspot.setWindowOpacity(0.22)
```

#### Correct: preserve native hit alpha and own-hover visibility

```python
fill = QColor(255, 255, 255, WORK_OVERLAY_HOTSPOT_HIT_ALPHA)
hotspot.setWindowOpacity(_interactive_hotspot_opacity(base_opacity, hovered))
```

## Scenario: Atomic work-overlay state delivery

### 1. Scope / Trigger

- Trigger: `DesktopWorkOverlay` atomically replaces the JSON state file while the
  PySide6 helper watches both that file and its parent directory with
  `QFileSystemWatcher`.
- Scope: cross-process state reads, bounded retry scheduling, watcher reattachment,
  helper shutdown, and preservation of the currently rendered bubbles.

### 2. Signatures

- Writer: `write_json_object(path, payload)` writes a sibling temporary file and
  replaces the state path.
- Reader: `OverlayWindow.poll_state() -> bool`; `False` means the state was
  transiently unreadable and the caller must schedule a bounded retry. `True`
  means the read was handled, including an explicit terminal state.
- Retry: `WORK_OVERLAY_STATE_READ_RETRY_MS` must remain shorter than
  `WORK_OVERLAY_STATE_READ_FAILURE_GRACE_SECONDS * 1000`.

### 3. Contracts

- Atomic replacement remains mandatory, but it does not guarantee that a Windows
  file-change callback can read the destination at the exact callback instant.
- A transient `OSError` or `JSONDecodeError` preserves the last rendered items and
  schedules one single-shot retry. It must not clear the shell, hide the overlay,
  or exit the helper.
- The retry uses the same refresh entry point as file and directory events so the
  replaced file path is re-added to `QFileSystemWatcher` before and after reading.
- Retry elapsed time uses `time.monotonic()`. Repeated failures remain bounded by
  the existing failure grace; they do not create an idle polling loop.
- The helper shuts down immediately when the owner process is gone, or after a
  readable state says `close=true` or is genuinely stale. Continuous unreadability
  past the grace records `work_overlay_helper.state_read_failed` and then shuts down.
- Helper exit stops the pointer/retry/stale timers and removes watched file and
  directory paths before returning from the Qt runner.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| First read overlaps atomic replacement | Keep current bubbles and retry after the bounded delay. |
| Retry reads valid state | Cancel the retry, reattach the file watcher, render, and resume the stale deadline. |
| Multiple watcher events arrive before retry | Keep one active single-shot retry; do not postpone it indefinitely. |
| State remains unreadable beyond the grace | Emit `state_read_failed` once and shut down. |
| Owner PID no longer exists | Shut down without waiting for the read grace. |
| Readable state has `close=true` or is stale | Shut down normally. |

### 5. Good/Base/Bad Cases

- Good: a Windows replace notification arrives during the namespace swap; the old
  bubbles stay visible, the 80 ms retry reads the complete JSON, and the helper PID
  does not change.
- Base: a normal file event reads successfully once and schedules only the next
  conservative stale check.
- Bad: record the first failed read and wait for the 15 second writer keepalive;
  that next callback sees an expired 1.2 second grace, exits the helper, and makes
  every bubble disappear until the parent restarts it.

### 6. Tests Required

- `tests/test_ui.py`: run the real PySide6 event loop offscreen with the first
  `Path.read_text()` raising `OSError`; assert a second read and clean exit occur
  before the failure grace expires.
- `tests/test_ui.py`: assert the retry is single-shot, connected to the watcher
  refresh entry point, shorter than the failure grace, and does not restore a
  recurring `WORK_OVERLAY_POLL_MS` timer.
- Renderer gate: run `tests/test_renderer_hud.py`, `tests/test_active_session.py`,
  and `tests/test_ui.py` together.

### 7. Wrong vs Correct

#### Wrong

```python
state = read_state()
if state is None:
    remember_first_failure()
    return  # No event is guaranteed before the grace expires.
```

#### Correct

```python
state_read = overlay.poll_state()
if not state_read and not state_read_retry_timer.isActive():
    state_read_retry_timer.start(WORK_OVERLAY_STATE_READ_RETRY_MS)
```

The one-shot timer closes the event-delivery gap without adding recurring idle CPU
work or replacing the file watcher as the primary event source.

## Scenario: Three-scenario renderer startup

### 1. Scope / Trigger

- Trigger: HUD startup must distinguish an absent Codex Desktop process, a running
  Desktop process without a usable debugger endpoint, and a running Desktop process
  that already exposes a usable CDP renderer target.
- Scope: renderer startup classification, bounded process command-line discovery,
  CDP endpoint validation, one-shot launch/restart ownership, and the persistent
  PySide6 restart action plus its lightweight fallback.

### 2. Signatures

- Startup value: `RendererStartupPlan(scenario, port=None, port_source="", reason="")`.
  Valid scenarios are `launch`, `attach`, `restart-required`, and the recovery-only
  `attach-launched` state.
- Process evidence: `_CodexDesktopProcess(pid, name, executable_path, command_line)`;
  remote debugging accepts both `--remote-debugging-port=<port>` and
  `--remote-debugging-port <port>`.
- Endpoint evidence: `cdp_version_info(port, timeout_seconds) -> dict` followed by
  `list_targets(port, timeout_seconds)` and `pick_page_target(targets)`.
- Overlay state: top-level `systemAction` is separate from `items` and carries
  `id`, `action`, `title`, `message`, `label`, and `persistent`.
- Overlay commands are append-only JSONL: `systemActionReady` and `restartCodex`
  both carry the matching `actionId`; a restart request also carries `requestedAt`.

### 3. Contracts

- No Desktop process -> select a currently bindable launch port, launch Codex once
  with CDP flags, and attach to that launch without another launch attempt from
  window preparation.
- A running Desktop process is attachable only when its bounded candidate port has
  a valid `/json/version` protocol identity and `pick_page_target()` selects a main
  Codex page. A TCP listener, persisted port, or command-line flag alone is not
  sufficient.
- Windows command-line discovery must filter rows through
  `is_codex_client_process()` so npm/standalone `codex.exe` is never Desktop
  evidence. macOS discovery must retain an executable inside
  `*.app/Contents/MacOS/`, including an unquoted app path containing spaces.
- A running Desktop process without a verified target enters `restart-required`.
  It must not be stopped until the user clicks the persistent action.
- `systemAction` bypasses the ordinary item limit, stale timestamp, and dismiss
  behavior while its owner PID is alive. `work_overlay_max_items=0` suppresses
  session bubbles only.
- Parent waits are awakened by `FileChangeWatcher` and helper process exit. The
  5-second watcher fallback is degraded reconciliation, not a 200 ms primary poll.
- On click, stop the verified Desktop family first, then allocate a fresh bindable
  port, record `renderer_restart_requested_by_user`, launch once, and re-enter with
  `launched_codex=True` so the next plan is `attach-launched`.
- Missing/unready PySide6 records `renderer_restart_overlay_fallback` and transfers
  the same indefinite action to `HudLoadingFeedback`; failure of optional state
  persistence must never turn a successful app launch into a launch failure.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| No verified Desktop process | `launch`; choose a bindable port and call the debugger launcher once. |
| Desktop command line exposes a live valid Codex CDP port | `attach` to that exact port; do not show restart UI. |
| Candidate listens but `/json/version` is invalid | Reject it and continue through the bounded candidate list. |
| Version is valid but no main Codex page target exists | Reject it; a running Desktop falls into `restart-required`. |
| Persisted/configured launch port is occupied | Allocate one fresh localhost port before the one launch. |
| PySide6 helper never reports ready or exits | Record the fallback reason and show the lightweight persistent action. |
| Command action ID does not match the active system action | Ignore that system-action command; preserve unrelated overlay commands. |
| User leaves the restart action untouched | Keep it visible and leave Codex running indefinitely. |
| User clicks restart repeatedly | Append and consume one matching restart request only. |
| Launch-port persistence cannot resolve or write its path | Continue the successful launch without persistence. |

### 5. Good/Base/Bad Cases

- Good: Codex already runs with CDP on a non-default port; process evidence finds the
  port, both HTTP checks pass, and one HUD invocation attaches without a restart.
- Good: ordinary bubbles are disabled, but the PySide6 restart card remains visible
  past the normal stale deadline and one click produces one stop/select/launch chain.
- Base: process command-line discovery fails or all candidates are rejected; keep the
  current app alive and request explicit restart rather than guessing a port.
- Base: PySide6 is absent in the official package; the lightweight card owns the
  action and waits on its request file event.
- Bad: scan a port range, trust a listening or persisted port without CDP identity,
  treat npm `codex.exe` as Desktop, poll the command file every 200 ms, or let
  `_prepare_codex_window_for_renderer()` launch after classification.

### 6. Tests Required

- `tests/test_ui.py`: process identity, both flag forms, macOS paths with spaces,
  candidate version/target rejection, occupied launch ports, all startup plans,
  no second window-preparation launch, and stop -> select -> launch ordering.
- `tests/test_ui.py`: `item_limit=0`, owner/stale persistence, helper-ready and
  fast-click backlog, action-ID matching, helper-exit fallback, and event-driven
  lightweight-card waiting.
- `tests/test_ui.py`: run the real PySide6 helper offscreen and assert a visible
  native restart hotspot, one ready command, one restart command, and clean exit.
- `tests/test_cdp_probe.py`: assert `/json/version` is requested locally with the
  bounded timeout and returns protocol identity.
- Full renderer gate: `python -m pytest tests/test_renderer_hud.py
  tests/test_active_session.py tests/test_ui.py -q`, then `python -m pytest`.
- Windows live acceptance order is Scenario 3 first, then user-clicked Scenario 2
  restart into Scenario 1; never stop the active app merely to prepare the test.

### 7. Wrong vs Correct

#### Wrong

```python
port = persisted_port_or_default()
client = RendererHudClient(port=port)
if not client.attach():
    stop_codex()
    launch_codex_with_cdp(port)
```

This trusts stale state, constructs a client for the wrong endpoint, and stops the
user's app without an explicit action.

#### Correct

```python
plan = _renderer_startup_plan(launched_codex=launched_codex)
if plan.scenario == RENDERER_STARTUP_RESTART_REQUIRED:
    return wait_for_explicit_restart_action()
if plan.scenario == RENDERER_STARTUP_LAUNCH:
    launch_codex_app(debugger=True)  # Exactly once for the selected bindable port.
return attach_renderer_without_window_prepare_launch(plan.port)
```

Classification owns launch/restart policy; CDP validation owns attach identity, and
the overlay command owns user consent.

## Scenario: Renderer-to-Python local detail RPC under Codex CSP

### 1. Scope / Trigger

- Trigger: an injected renderer surface needs local HUD data that is too large or
  sensitive for the ordinary snapshot, such as an on-demand Prompt or request
  timeline.

### 2. Signatures

- Renderer commands use the existing `codexUsageHudSettingsCommand` CDP binding:
  `openBackgroundUsage { eventId }`, `backgroundUsageQuery { id, filters }`, and
  `backgroundUsageDetail { id, eventId }`.
- Python returns one-shot
  `settingsCommandStatus.backgroundUsageResponse` with `kind`, `requestId`,
  optional `eventId`, `payload`, and optional `error`. `open` and `query`
  payloads may include `selectedDetail { eventId, hasPrompt, ... }`; they must
  never include `selectedDetail.prompt`.

### 3. Contracts

- Codex Desktop's page CSP does not include `http://127.0.0.1:*` in
  `connect-src`; injected product UI must not rely on renderer `fetch()` to a
  localhost bridge.
- A bubble jump performs exactly one `openBackgroundUsage` query. Its first
  response contains the requested day's list, the selected event, and a
  Prompt-free detail preview. A normal tab open performs exactly one query and
  selects the most recent matching event when no explicit `eventId` is given.
- Query/open responses contain summaries, request attribution, and a selected
  detail preview but no Prompt. `hasPrompt` may advertise availability; only a
  detail response for the selected `eventId` may contain Prompt.
- Renderer state tracks separate query/detail request IDs and ignores stale or
  mismatched responses. Ignoring a stale response is terminal and must not
  schedule a replacement query.
- Query/detail loading timers are request-scoped and bounded to five seconds.
  A timeout clears the matching loading state, shows a retryable error, and
  cannot mutate a newer request.
- Python clears a one-shot open/query/detail response only after the
  `backgroundUsage` domain update is acknowledged. A failed renderer update
  retains the response for retry.
- Before replacing background-usage markup, capture settings-body scroll by
  filter key, history scroll by filter key, and detail scroll by `eventId`.
  Restore synchronously and on the next animation frame. A loading placeholder
  is not a loaded detail and must never overwrite the saved detail offset.
- Localhost HTTP endpoints may remain for diagnostics and standalone fixtures.
  Product code must not call `Page.setBypassCSP` or otherwise weaken Codex CSP.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| CDP settings binding is available | Send typed RPC; do not attempt localhost fetch. |
| Binding is absent in a standalone fixture | The localhost HTTP fallback may be used. |
| Runtime or event is unavailable | Return a typed error response; keep the renderer and existing selection usable. |
| A late response ID does not match current state | Ignore it without changing loading, selection, or detail. |
| List/query response contains Prompt | Fail the contract and remove Prompt from the summary projection. |
| A query or detail exceeds five seconds | End only that request's loading state and show a retryable timeout error. |
| Renderer delivery fails | Keep the one-shot response so the next domain update can retry it. |
| Selection/detail repaint replaces the scroll containers | Restore history for the current filter and detail for the selected event. |
| A new filter or never-viewed event is selected | Start its corresponding scroll position at zero. |

### 5. Good/Base/Bad Cases

- Good: a bubble jump returns the list and matching Prompt-free preview in one
  `open` response, paints them immediately, then sends one detail request whose
  response alone may contain Prompt.
- Good: selecting an event at history offset 260 and detail offset 170 preserves
  those offsets across detail hydration and Prompt repaint; revisiting the event
  restores 170.
- Base: the visual fixture has no binding and reads its mock localhost endpoint.
- Bad: disable CSP globally, fetch localhost from the real Codex document, include
  Prompt in normal HUD payloads, apply a response without matching its ID, start a
  new query after rejecting a stale response, or save a loading placeholder's
  zero scroll offset over a loaded event's offset.

### 6. Tests Required

- `tests/test_ui.py`: assert query/detail commands call the background runtime with
  normalized filters, return typed responses with matching request IDs, and strip
  Prompt from open/query previews.
- `tests/test_renderer_hud.py`: assert both binding commands, response handling,
  stale-response terminal returns, bounded timers, scroll capture/restore, lazy
  detail loading, and the HTTP fixture fallback remain in the script contract.
- Live renderer acceptance: use the helper JSONL command path, then inspect the
  real DOM through CDP for the active `backgroundUsage` tab, selected `eventId`,
  populated request detail, no error text, one initial query, and stable history
  and detail scroll offsets.

### 7. Wrong vs Correct

#### Wrong

```javascript
await fetch("http://127.0.0.1:57322/background-usage");
```

The Codex document blocks this at `connect-src`, even when the HTTP server has
valid CORS headers.

#### Correct

```javascript
const requestId = submitBackgroundUsageCommand("backgroundUsageQuery", { filters });
scheduleBackgroundUsageRequestTimeout("query", requestId);

// A late response is consumed by doing nothing; it never starts another query.
if (response.requestId !== backgroundUsageState.queryRequestId) return;
clearBackgroundUsageRequestTimeout("query");
backgroundUsageState.data = response.payload;
backgroundUsageState.detail = backgroundUsageSelectedDetail(
  response.payload,
  response.payload.selectedEventId,
);
```

The renderer paints the first list/preview response only when its `requestId`
matches. A later detail payload additionally requires the same selected
`eventId`; scroll capture runs before either repaint.
