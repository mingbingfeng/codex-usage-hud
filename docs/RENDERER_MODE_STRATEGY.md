# Renderer Mode Strategy

## Standing Decision

Renderer mode is the product path for this project.

- The HUD should remain inside the Codex App renderer/CDP surface.
- Windows and macOS must stay first-class compatibility targets.
- Qt/Tk HUD modes are deprecated legacy surfaces.
- Do not add new product behavior to Qt/Tk.
- Do not use Qt/Tk fallback as the answer to renderer performance problems.
- Qt/Tk code may be touched only for removal, migration, critical maintenance,
  or tests required while legacy code remains in the tree.

## Performance Vision

The target is event-driven renderer HUD behavior:

```text
no relevant event -> no recurring HUD CPU work
```

Polling should be treated as a fallback, not as the architecture. If polling is
temporarily required, it should be scoped, slow when idle, cancellable, and
removed once a reliable listener exists.

## Preferred Architecture

1. Renderer bootstrap installs one small controller script through CDP.
2. The controller subscribes to browser-side events and keeps stable references
   to the HUD root, Codex header, composer, and active thread markers.
3. Python sends updates only when local state changes, not on a fixed refresh
   loop.
4. The renderer script performs DOM reads only in response to relevant events,
   coalesced through a single animation-frame scheduler.
5. Platform-specific work stays behind platform adapters. The renderer contract
   should not depend on Windows-only UIA, Win32 window messages, or macOS-only
   APIs except behind explicit adapter boundaries.

## Event Sources To Prefer

- Codex session JSONL changes: filesystem watcher where available, with a slow
  fallback scan only when native watching is unavailable.
- Active session changes: CDP/DOM route, sidebar, title, or URL observation
  before native window title polling.
- In renderer mode, renderer-observed active session is the default authority.
  Native title/CDP-ref legacy tracking may only be enabled by explicit diagnostic
  switches and must not silently replace renderer failures.
- Renderer layout changes: targeted `MutationObserver` on known header,
  composer, and thread-list nodes rather than whole-document observation.
- Window and viewport changes: `resize`, relevant `scroll`, visual viewport,
  and focused input events, coalesced through `requestAnimationFrame`.
- Settings and commands: explicit DOM/custom events or a bridge callback rather
  than recurring localStorage polling.
- Budget/config changes: file watcher or explicit settings-save event rather
  than unconditional reload on every frame.

## Renderer Script Rules

- Avoid observing `document.documentElement` with broad `subtree`, `attributes`,
  and `characterData` options during normal operation.
- Cache located DOM anchors and invalidate them only when a targeted observer
  proves the anchor changed or disappeared.
- Avoid repeated `querySelectorAll("div")` and broad layout scans on every
  update.
- Avoid interleaving style writes and layout reads. Batch reads first, then
  writes.
- Keep one scheduler per concern and coalesce bursts into one animation frame.
- Stop timers and observers when the HUD is removed or the target page changes.
- Prefer `ResizeObserver`/targeted `MutationObserver` over fixed intervals.
- Keep stale-state UI as a one-shot timeout that is reset by real updates, not
  as a repeating heartbeat.

## Python Runtime Rules

- Do not rebuild snapshots on a fixed interval when the session file, active
  session, settings, and update state have not changed.
- Do not rescan all sessions or archived sessions unless a filesystem event,
  time-window boundary, or explicit invalidation requires it.
- Separate fast current-session updates from slower aggregate budget summaries.
- Keep CDP target discovery cached and invalidate on connection failure, target
  change, or browser navigation.
- Use platform adapters for launch, focus, process watching, and filesystem
  watching so the same renderer contract works on Windows and macOS.

## Migration Shape

1. Keep renderer mode as default and supported on both Windows and macOS.
2. Add an internal event bus for `session_file_changed`,
   `active_session_changed`, `settings_changed`, `budget_window_changed`,
   `renderer_layout_changed`, and `update_state_changed`.
3. Convert the renderer loop from fixed refresh to event-triggered snapshot
   builds.
4. Replace broad renderer DOM observation with targeted observers and cached
   anchors.
5. Keep slow polling only behind feature-detected fallback paths.
6. Remove or quarantine legacy Qt/Tk paths after renderer parity is complete.
