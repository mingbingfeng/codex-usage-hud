# Technical Design

## Boundaries

```text
Codex logs_2.sqlite (read-only) ----+
                                     +-> BackgroundUsageScanner
Codex state_5.sqlite (read-only) ---+       |
                                             v
                                  HUD background-usage.sqlite3
                                    |                    |
                           pending summaries      paged/query details
                                    |                    |
                           PySide6 helper          Settings bridge
                                    |                    |
                       dismiss/open commands      Renderer settings Tab
                                    +----------> runtime command queue
```

- `core/background_usage.py` owns source decoding, typed domain records, classification, cost estimation, local schema and repository queries. UI code never parses raw Codex log text.
- The scanner receives paths, current App process identity/provider/prices and a clock. It never imports renderer or Qt modules.
- Codex databases are opened with SQLite URI `mode=ro`, short busy timeout and query-only pragmas. The HUD audit database is the only writable store.
- Background usage is a parallel product domain. It does not mutate `ParsedSession`, `UsageSummary`, daily/weekly budgets, or the current-session snapshot.

## Source Projection

The decoder recognizes records from `codex_core::session::handlers`, `codex_core::session::turn` and request feedback tags. It normalizes untrusted log bodies into:

```text
BackgroundThreadEvidence
  thread_id, process_uuid, feature_key, feature_label
  prompt, cwd, first_seen_at, last_seen_at

BackgroundRequestEvidence
  source_log_id, thread_id, occurred_at, model, endpoint
  total_usage_tokens, estimated_input_tokens
```

Malformed records return no projection plus a diagnostic counter. Exact log grammar and feature signatures live in one decoder table. Unknown signatures still produce a typed `unknown` feature.

## Classification

1. Read new log rows after `source_cursor.last_log_id`; initial import locates a bounded history start without scanning all rows.
2. Upsert thread and request evidence into the HUD store, keyed by thread ID and source log ID.
3. Read the visible thread IDs and `thread_spawn_edges.child_thread_id` from `state_5.sqlite`.
4. Keep newly observed candidates pending until the grace deadline. On a source/state file event, re-evaluate pending candidates.
5. Suppress/delete the projected candidate when it becomes a visible thread or explicit child; otherwise mark it `background_usage` once the deadline expires and the `process_uuid` is attributed to Codex App.
6. Upsert later requests into the same event. Confirmation is event-level and never reset by an upsert.

App process attribution uses local runtime evidence already available to the HUD. When attribution cannot be proven, the candidate stays out of notifications and is recorded as a diagnostic rather than being labeled as App usage.

## Local Schema

Schema versioning is owned by the new module and uses idempotent `CREATE TABLE IF NOT EXISTS` plus a metadata version:

- `scan_state(source_key PRIMARY KEY, last_log_id, initialized_at, updated_at)`
- `process_evidence(process_uuid PRIMARY KEY, app_evidence, last_seen_at)`
- `background_events(event_id PRIMARY KEY, thread_id UNIQUE, process_uuid, feature_key, feature_label, prompt, cwd, provider, first_seen_at, last_seen_at, confirmed_at, classification_state, request_count, total_tokens, estimated_cost_usd, cost_available)`
- `background_requests(request_id PRIMARY KEY, event_id, source_log_id UNIQUE, occurred_at, model, endpoint, total_tokens, estimated_input_tokens, estimated_cached_tokens, estimated_output_tokens, estimated_cost_usd, price_snapshot_json)`

Indexes cover `last_seen_at`, `feature_key`, `model` and `event_id`. Prompt is excluded from summaries and only selected by the detail query.

## Token And Cost Estimate

- `total_tokens` is the log's non-negative `total_usage_tokens`.
- `estimated_input_tokens = clamp(estimated_token_count, 0, total_tokens)`.
- `estimated_output_tokens = total_tokens - estimated_input_tokens`.
- For later requests in the same thread, the previous estimated input prefix is a best-effort cached-input estimate, bounded by the current input. The first request has zero estimated cached input.
- `UsageCalculator` and the current App provider price table calculate the amount. The per-request price inputs are persisted as JSON so history does not silently change when settings change.
- Every API and UI projection exposes `costSource="estimate"`; missing model price yields `estimatedCostUsd=null`.

## Runtime Integration

- A `BackgroundUsageRuntime` owns one scanner worker, a coalescing event and one `FileChangeWatcher` over the source/state SQLite files. SQLite WAL/SHM changes are covered by the watcher's SQLite sibling handling.
- Startup performs one bounded import; native file events coalesce another incremental scan. No recurring renderer refresh is added.
- Scan completion publishes only when event summaries changed. The renderer main loop then refreshes the helper payload and background-tab revision.
- `DesktopWorkOverlay` serializes pending events as `kind="background_usage"`. These items bypass session selection and terminal-state stabilization.

## Helper Commands

The helper emits typed file commands:

```json
{"action":"dismissBackgroundUsage","eventId":"..."}
{"action":"openBackgroundUsage","eventId":"..."}
```

The existing command pump remains the sole decoder/dispatcher:

- `dismissBackgroundUsage` calls the repository confirmation method and republishes overlay items.
- `openBackgroundUsage` enqueues a renderer command that opens the settings modal at `backgroundUsage` with `eventId`; it does not confirm.

## Settings Bridge Contract

Localhost endpoints expose typed projections, not database rows, for diagnostics
and non-Codex fixtures:

- `GET /background-usage?range=today|7d|30d&feature=&model=&eventId=` returns summary, filtered event rows, selected detail without Prompt, available filter values and a revision.
- `GET /background-usage/detail?eventId=` returns the selected event, requests and Prompt.
- `POST /background-usage/confirm` accepts `{eventId}` and returns the updated event.

The real Codex renderer cannot fetch localhost because the app's `connect-src`
CSP excludes `127.0.0.1`. Product UI queries therefore use the existing CDP
settings-command binding with request IDs:

- `backgroundUsageQuery` carries the range/feature/model/event filters.
- `backgroundUsageDetail` carries the selected `eventId` and is the only response
  allowed to contain Prompt.
- Python returns a one-shot typed `backgroundUsageResponse` in the
  `backgroundUsage` payload domain; the renderer ignores stale request IDs.

Standalone fixtures without the binding retain the HTTP fallback. Existing
settings requests and saves are unchanged, and the HUD never disables or
bypasses the Codex page CSP.

## Renderer UI

- Add `backgroundUsage` to the existing modal tab reducer, markup builder and styles.
- Use an unframed master-detail layout matching the accepted concept: compact metric strip, filter toolbar, history list and request detail.
- The page responds to `openBackgroundUsage` commands, fetches the current revision and highlights the requested event.
- Amount text uses `估算` and tooltips explain that local logs lack exact billing buckets. Prompt is collapsed and escaped before DOM insertion.
- Narrow layouts stack history over detail; stable min/max sizes prevent count/model text from shifting controls.

## Compatibility And Failure Modes

- Missing `logs_2.sqlite`, older schemas, locked databases, missing PySide6 or unsupported feature signatures degrade independently and register runtime diagnostics.
- Existing settings bridge, provider tabs, storage page, work bubbles and session switching retain their contracts.
- Database initialization is additive. Rollback consists of removing the feature code; the standalone HUD audit DB can remain without affecting older versions.
- No source prompt, token record, credential or audit content leaves localhost.

## Verification Strategy

- Unit tests build minimal source/state SQLite fixtures and assert decoding, classification, cursoring, aggregation, confirmation and price snapshots.
- Settings bridge tests call new endpoints and assert Prompt is absent from list responses.
- Overlay tests cover item rendering, checkmark hit target, typed commands, no session switch and persistent dismissal wiring.
- Renderer tests cover tab markup, filters, lazy detail fetch, jump command and estimate labels.
- Runtime tests assert file events coalesce scans and idle state performs no recurring work.
- A read-only local smoke prints only event IDs/classification/counts for the known Context-aware suggestions and Memory consolidation examples.
- The 2026-07-20 local smoke processed 3,399 relevant rows in 0.222 seconds and found both known threads. Their HUD estimates were `$0.411004` and `$0.548041`, explaining the observed Provider/HUD difference when combined with visible-session usage.
