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
- Good: a complete session costs `$1`, an appended round costs `$2`, and the visible sequence is `$1` while hydrating then `$3` when complete, never `$2`.
- Base: Codex exposes `client-new-thread:*` before persistence; HUD remains explicit pending and converges after the exact local evidence appears.
- Base: a cold partial tail has no prior complete cost; it remains explicitly loading and does not publish the sum of tail-window rounds as a session total.
- Bad: title-key the renderer cache, mark a cached preview as applied, discard the provisional ID after one miss, accept transport delivery as application ACK, synchronously parse 16 recent files before the visible update, or reconnect every auxiliary binding forever.
- Bad: add `_request_cost(snapshot)` to the top `本会话` amount or retain the partial parser's tail-only `cumulative_cost_usd`; both make the amount jump between round and session totals.

### 6. Tests Required

- `tests/test_active_session.py`: initial provisional miss followed by mapping reconciliation, duplicate-title ambiguity, stale sequence rejection, and disconnect state preservation.
- `tests/test_renderer_hud.py`: raw/canonical identity fields, applied-sequence dedup, immediate click feedback, same-target critical binding retry, and disconnect callback payload.
- `tests/test_renderer_hud.py`: exact-ID payload cache markers, `cachedPreview` ACK exclusion, and authoritative payloads carrying both identity fields.
- `tests/test_ui.py`: visible-first scan exclusion, separate work refresh, stale snapshot rejection, unchanged current-sequence retry, and specific runtime diagnostics.
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

## Scenario: Active session reconciliation after composer send

### 1. Scope / Trigger

- Trigger: a new Codex conversation can remain in `renderer-new-session` after the first message because the default-disabled composer badge disables the existing input watcher.
- Scope: injected renderer active-session reporting through the CDP binding, followed by Python tracker/snapshot/work-overlay refresh.

### 2. Signatures

- JavaScript: `scheduleActiveSessionSendFollowup(reason = "composer-send", expectedSessionId = "")`.
- JavaScript: `postActiveSession(reason, overrideRef)` sends canonical and raw identities plus `selectionSeq` and observation timing.
- Python: `ActiveSessionTracker.observe_conversation_ref(..., renderer_session_id="", selection_seq=0, observed_at_ms=0)`.
- Runtime events: `active_session_changed` wakes the renderer loop; the subsequent refresh may publish `active_work_refresh_requested`.

### 3. Contracts

- Composer submit, and fallback Enter/click handlers when `composerBadgeEnabled` is false, trigger bounded follow-ups at 32, 120, 320, 800, and 1600 ms.
- The form `submit` listener is independent of the optional composer token badge.
- A canonical renderer UUID is the only key allowed to resolve a rollout path. Provisional or unmapped refs remain pending.
- Signature deduplication suppresses unchanged follow-ups only after the corresponding `selectionSeq` has been applied by the HUD.
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
| Renderer watchers removed | Timers and submit/keydown listeners are detached. |

### 5. Good/Base/Bad Cases

- Good: first message creates a canonical UUID within the follow-up window; the tracker resolves the exact rollout and the completed work item reaches `status="recent"` without a session click.
- Base: the page remains blank or exposes only `client-new-thread:*`; HUD stays explicitly pending.
- Bad: using a global idle poll, unconstrained title match, or newest JSONL to force a visible session; this can show another conversation's usage.

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
- External observation only: pet notifications expose an internal `localConversationId`, `status`, `updatedAtMs`, `waitingRequest`, and opaque `actionPath`; these fields are evidence for behavior comparison, not a local API signature.

### 3. Contracts

- `sessionId` / canonical conversation UUID is the primary identity for local state, overlay matching, and session switching.
- `targetTitle` and `workdir` are display/context fields. They cannot replace an unavailable canonical UUID except inside the constrained provisional recovery contract above; newest-rollout guessing remains forbidden.
- Progress text must come from structured `status`, `statusText`, `lastText`, `progress`, and `updatedAt` fields produced by the existing snapshot/parser path, not from a second renderer DOM reader.
- A renderer active-session event wakes the runtime event bus and refreshes the exact current session/work overlay. Bounded follow-up timers are allowed only for selection application acknowledgement and provisional new-session reconciliation.
- The external pet's `open-in-main-window` / `actionPath` behavior may be used as a reverse-engineering reference, but the local stable jump boundary remains `activateSession(sessionId)` through the existing session-switch controller.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Canonical session ID available | Refresh exact session and allow `activateSession`. |
| `client-new-thread:*`, blank, or unmapped ID | Keep explicit pending state unless one candidate passes every constrained provisional-recovery check. |
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
| Card workdir hover | Keep the full QLabel text and highlight only its exact bounds; do not paint a second string or expand over the status label. |
| Completed badge workdir | Keep only the painted arc text; its invisible native hotspot activates the App session without drawing a rectangular layer. |
| Bubble opacity is `0.22` | `WindowFromPoint` still resolves the workdir/check hotspot rather than the window beneath it. |
| Completed check hover/click | Raise the hotspot to its own hover opacity, show a clear outline, and invoke the existing annihilation dismissal. |

### 5. Good/Base/Bad Cases

- Good: `custom` completes while `muyuan` resumes; both animate in sequence.
- Good: a low-opacity card keeps one full workdir label, while the completed
  check hotspot remains natively hit-testable and becomes obvious on hover.
- Base: only one visible session changes kind; it follows the original path.
- Bad: assigning the full target list to `previous_visible_items` before the
  first animation ends, which causes later changes to be redrawn abruptly.
- Bad: using alpha `1` for a layered hotspot at bubble opacity `0.22`, or
  redrawing workdir text in the hotspot window; the former can become native
  click-through and the latter creates duplicate or ellipsized text.

### 6. Tests Required

- `tests/test_ui.py`: assert that simultaneous card-to-completed and
  completed-to-card changes leave the later item deferred, then detectable on
  the following pass.
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
