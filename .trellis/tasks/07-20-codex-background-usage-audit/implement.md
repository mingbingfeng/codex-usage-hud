# Implementation Plan

## 1. Domain And Storage

- [x] Add typed background event/request projections and the centralized Codex log decoder.
- [x] Add read-only source/state SQLite access, bounded initial cursor and incremental row scan.
- [x] Add HUD-owned SQLite schema, repository queries, event-level confirmation and schema/version tests.
- [x] Add feature signature classification, visible-session/subagent exclusion and grace-period tests.
- [x] Reuse provider price tables and `UsageCalculator` for explicitly labeled estimates; test missing prices and persisted price snapshots.

Validation checkpoint:

```powershell
python -m pytest tests/test_background_usage.py -q
```

## 2. Event-Driven Runtime

- [x] Add a coalescing scanner worker and `FileChangeWatcher` integration for log/state SQLite changes.
- [x] Publish changes into the existing renderer runtime event path without adding an idle loop.
- [x] Add runtime diagnostics for missing/incompatible/locked source databases.
- [x] Verify clean shutdown and that repeated unchanged events do not rewrite overlay state.

## 3. PySide6 Bubble

- [x] Add a `background_usage` overlay payload variant and ordering/visibility rules.
- [x] Render the amber card, checkmark close control, estimate labels and background-history link hotspot.
- [x] Emit `dismissBackgroundUsage` and `openBackgroundUsage` commands with `eventId`.
- [x] Extend the main command pump to persist confirmation or enqueue the renderer jump without switching sessions.
- [x] Cover pure helper logic and the real offscreen PySide6 lifecycle when available.

Validation checkpoint:

```powershell
python -m pytest tests/test_ui.py -q
```

## 4. Settings Bridge And Renderer Tab

- [x] Add summary/list/detail/confirm repository callbacks and localhost endpoints with strict event ID validation.
- [x] Add the “后台用量” Tab, metrics, filters, history master list and request detail panel.
- [x] Fetch Prompt only for selected detail, escape all source text, and keep Prompt out of standard HUD payloads.
- [x] Handle `openBackgroundUsage` by opening the modal, selecting the Tab and highlighting the requested event.
- [x] Add responsive styles and explicit loading, empty, unavailable and stale states.

Validation checkpoint:

```powershell
python -m pytest tests/test_settings_bridge.py tests/test_renderer_hud.py -q
```

## 5. Integration And Acceptance

- [x] Run focused domain/runtime/UI tests and fix cross-layer contract drift.
- [x] Run the known real local threads through a read-only smoke without printing Prompt or copying source databases.
- [x] Restart the real HUD and verify: today-only bubble, checkmark persistence across restart, detail link jump, filters, request expansion and estimate labels.
- [x] Verify no background event changes a session HUD total, provider aggregate, daily budget or weekly budget.
- [x] Inspect overlay JSON and transition log for the new payload/commands.

Final gate:

```powershell
python -m pytest tests/test_background_usage.py tests/test_settings_bridge.py tests/test_renderer_hud.py tests/test_ui.py -q
python -m pytest -q
python -m compileall -q src tests tools
git diff --check
```

## Risk And Rollback Points

- Source log grammar may drift: decoder failures must be row-local and surfaced as diagnostics; do not widen parsing heuristics without a fixture.
- `logs_2.sqlite` is large: abort integration if any path performs a full scan after initialization or blocks the renderer loop.
- Prompt is sensitive: abort UI integration if list/normal snapshot payloads contain Prompt.
- Qt helper is optional: a helper failure must never disable history persistence or renderer settings.
- Changes to session usage accounting are forbidden. Any diff to its aggregation semantics is rolled back before integration.
