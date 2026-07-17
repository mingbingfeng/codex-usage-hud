# Technical Design

## Boundaries

The change remains inside the renderer active-session pipeline:

```text
Codex sidebar DOM
  -> renderer observation {selectionSeq, rawRendererSessionId, title, observedAt}
  -> CDP binding
  -> ActiveSessionTracker selected-ref state
  -> exact/provisional reconciliation
  -> visible-first sessionSwitch payload
  -> background active-work refresh
  -> renderer applied acknowledgement and delayed-stage feedback
```

`src/codex_usage_hud/ui/renderer_hud.py` owns DOM observation and renderer-side
application state. `platforms/active_session.py` owns identity normalization and
reconciliation. `cli.py` owns event scheduling, snapshot phases, diagnostics, and
payload delivery.

## Selection Contract

Each genuinely different renderer selection receives a monotonically increasing
`selectionSeq`. The payload preserves both `sessionId` (canonical when available)
and `rendererSessionId` (the exact raw/provisional row identity). Python stores an
immutable selected-ref record containing sequence, raw ID, title, flags, reason,
and observation time.

Tracker state may progress only within the same sequence:

```text
observed -> awaiting-canonical / awaiting-map -> confirmed
         -> ambiguous (still pending)
```

An older sequence may complete parsing or mapping but cannot mutate current
selection state or produce an applicable renderer payload.

## Provisional Reconciliation

The tracker retains provisional identity while pending. On initial observation and
every `session-map` event it resolves exact-title candidates from
`session_index.jsonl`, then verifies each candidate's exact `threads.id` row and
existing rollout path. Exactly one candidate confirms the selection. Zero candidates
means `awaiting-persistence`; multiple candidates mean `ambiguous-title`. No prefix,
newest-file, recursive scan, or stale-activity fallback is allowed.

The constrained fallback is necessary because current Codex DOM/React state and
`state_5.sqlite` expose no direct provisional-to-canonical relation.

## Visible-First Refresh

The active-session event synchronously builds only the selected session snapshot via
`SessionSnapshotCache.snapshot_for()`, reuses stale-safe budget totals, and reuses the
last active-work list. It sends the `sessionSwitch` domain immediately.

A separate `active_work_refresh_requested` event performs recent-session aggregation
after the visible payload. Its result is accepted only if its selection sequence still
matches. This keeps overlay accuracy without placing the 16-file scan on click latency.

## Feedback And Telemetry

Session-switch payloads gain stable diagnostic fields: `selectionSeq`, `followState`,
`followReason`, `observedAt`, and elapsed milliseconds. The existing session/request
surface shows feedback only while delayed or failed; no new card/modal is introduced.

Milestones are logged with the same sequence: renderer observed, binding received,
identity confirmed, snapshot built, payload applied. A renderer apply acknowledgement
updates the current sequence. Binding disconnect or failed apply reports a transport
reason and initiates only bounded recovery for that selection.

## Compatibility And Rollback

- Payload fields are additive and ignored by older renderer consumers.
- Existing `newSession` and `pendingSession` semantics remain available.
- Provider scope and CDP restart code are not refactored.
- Rollback is limited to the active-session tracker/payload/event changes and tests;
  unrelated uncommitted CDP restart edits must remain intact.
