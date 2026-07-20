# Technical Design

## Boundaries

```text
PySide6 background card link
  -> helper command JSONL (`openBackgroundUsage`)
  -> command pump
       +-> enqueue renderer settings command
       +-> bounded Codex window refocus
  -> `_handle_renderer_settings_command`
  -> one-shot `backgroundUsage` domain response
  -> renderer background-usage state + DOM
```

- `ui/work_overlay_qt.py` owns the visual-state invariant that background usage is always a card.
- `cli.py` owns helper command routing, Codex window activation and the typed response assembled from `BackgroundUsageRuntime`.
- `ui/renderer_hud.py` owns request sequencing, loading state, selected-event state and scroll restoration.
- `core/background_usage.py` and the SQLite schema remain unchanged; measured local query time is already below 3 ms.

## Window Activation Flow

1. The helper writes `openBackgroundUsage` with `eventId` as it does today.
2. `_handle_work_overlay_commands` invokes the background command callback first so renderer work is queued immediately.
3. After a successful enqueue, it calls the existing delayed refocus helper. The short delay lets the renderer begin opening the modal before the desktop window is restored.
4. The activation result is attached to the existing `overlay_command_received` event context and logged. Failure does not cancel the renderer command.

This reuses `_refocus_codex_window_after_work_overlay_switch()` on Windows and macOS rather than introducing another platform API.

## Square-Only Invariant

`_item_is_completed()` will explicitly return false for `_item_is_background_usage(item)` before checking `status="recent"`. Because layout, transition detection and widget selection all consume this predicate, one guard prevents background items from entering completed rows or card-to-circle transitions without changing session behavior.

## Current-Task Slot Priority

The live failure is deterministic slot starvation, not geometric overlap:

```text
raw:      2 background + 4 completed + 1 current active + 1 other active
limit:    6
current:  position 7 after existing ordering
visible:  first 6 only, so the current task is removed
```

`_ordered_overlay_items()` will partition non-background, non-completed items with `current=true` into a dedicated `current_active` group. The final order becomes:

```text
current_active (newest first)
background_usage (newest first)
completed (existing chronological order)
other_active (newest first)
```

The existing `item_limit` slice can then remain simple: the current active item is structurally guaranteed to occupy the first slot before any notification or historical item. When no current active item exists, the previous relative order of all remaining groups is unchanged. This avoids a second reservation/cropping algorithm and also keeps the current card at the top rather than technically selected but potentially clipped at the bottom of the screen.

## Single-Flight Initial Data

The current infinite load is a request-order bug, not a database latency problem. The corrected flow is:

```text
Bubble open:
  openBackgroundUsage command
    -> query(today, eventId)
    -> build selected detail preview without Prompt
    -> one `open` response
    -> render modal with list + selected detail

Normal tab open / refresh / filter:
  one `query` command
    -> query(current filters)
    -> build latest/selected detail preview without Prompt
    -> render list + detail
```

- A small CLI projection helper augments the existing query payload with `selectedDetail`. It copies the selected detail, removes `prompt`, and records only whether Prompt is available.
- The open command carries the same query payload in its first response, eliminating the open -> query duplicate round trip.
- The renderer ignores a query/detail response whose request ID is not current and returns immediately; it never starts another request from a stale response.
- After the preview is painted, the existing detail command may hydrate Prompt for the selected event. This second request is not on the path to visible list/detail content.
- A successful `backgroundUsage` domain update clears any one-shot open/query/detail response. A failed CDP update retains it so the existing renderer retry path can deliver it later.
- A request-scoped timeout clears the loading flag and exposes an error. Each new request replaces the prior timer; matching response or modal teardown clears it.

The HTTP fallback continues to work when `selectedDetail` is absent: the renderer paints the list, then uses the existing detail endpoint.

## Scroll State

Before replacing background-usage markup, the renderer captures:

- settings body `scrollTop` for narrow layouts;
- history pane `scrollTop` for the current filter key;
- detail pane `scrollTop`, keyed by selected `eventId`.

After `innerHTML` replacement, it restores those positions synchronously and once on the next animation frame to cover layout settling. Selection changes save the old event position before changing `selectedEventId`; a never-viewed event has no stored detail position and starts at zero. Range/feature/model changes rotate the filter key and intentionally start the new history result at zero.

## Compatibility And Failure Modes

- Prompt remains absent from list, preview and normal HUD payloads.
- If detail preview creation fails, the list still renders and the existing detail request handles the selected event.
- If window refocus fails, renderer navigation still proceeds and diagnostics retain the status/reason/HWND.
- No source scanning, database migration, polling interval or usage-accounting code changes.
- Rollback is isolated to the three UI/runtime files and their tests; the background-usage database remains compatible.

## Verification Strategy

- Unit tests cover the square invariant, current-task slot priority at limits 1 and 6, foreground callback ordering/context, preview Prompt stripping, one-shot response clearing and typed command shapes.
- Renderer contract tests cover one initial request, stale-response ignore behavior, timeout state and scroll-state helpers.
- Focused tests use `python -m pytest` because `rtk pytest` is not authoritative in this repository.
- Live verification restarts the real HUD, verifies the current task remains visible beside multiple background reminders, uses a real helper click and CDP DOM inspection, measures query-to-visible state, tests both scroll panes, and checks restore/foreground from a minimized Codex window.
