# Event-Driven Windows Docking

This document describes the Windows-only docking path used by the Qt/Tk HUD.  The
goal is to keep the HUD one z-order level above the Codex desktop window without
using global topmost state or timer-based window polling.

## Modules

`codex_usage_hud.platforms.windows_event_dock.NativeHudWindowManager`

- Binds each HUD top-level HWND to the Codex HWND with `GWLP_HWNDPARENT`.
- Normalizes toolkit HWNDs through `GetAncestor(..., GA_ROOT)` before owner
  binding or native movement.  This is required for Tk, whose `winfo_id()` can
  refer to an inner child HWND rather than the native top-level wrapper.
- Clears accidental topmost state with `SetWindowPos(HWND_NOTOPMOST, ...)`.
- Applies geometry with one native `SetWindowPos` call using `SWP_NOACTIVATE`.
- Owner binding is guarded by `CODEX_USAGE_HUD_EVENT_DOCK_OWNER`.  Tk defaults
  this off because cross-process ownership can stall Electron/Chromium message
  processing in Codex; it keeps event-driven geometry but uses the legacy
  topmost visibility fallback.

`codex_usage_hud.platforms.windows_event_dock._WinEventHookThread`

- Registers out-of-context WinEvent hooks with `SetWinEventHook`.
- Listens to `EVENT_OBJECT_LOCATIONCHANGE`, visibility/minimize/destroy events,
  foreground changes, reorder events, and name changes.
- Filters events to the Codex root HWND and emits toolkit-thread wakeups.

`codex_usage_hud.platforms.windows_event_dock._UiaBoundingRectangleThread`

- Initializes UI Automation on an STA worker thread through the existing ctypes
  `_UiaProbe`.
- Registers `IUIAutomationPropertyChangedEventHandler` on the Codex root subtree.
- Watches `UIA_BoundingRectanglePropertyId` and related properties so Chromium
  layout changes inside the same HWND can trigger immediate HUD recomputation.
- This hook is opt-in via `CODEX_USAGE_HUD_EVENT_DOCK_UIA=1`; WinEvent docking
  is the default because subtree UIA property hooks can be expensive in Electron.

`codex_usage_hud.ui.qt_hud._QtHudWindowImpl`

- Starts `WindowsEventDockBridge` on real Windows desktops.
- Avoids `WindowStaysOnTopHint` on Windows.
- Stops the follow timer when event docking is active.
- Does not hide the HUD just because Codex is not foreground; z-order occlusion is
  delegated to DWM through owner binding.

`codex_usage_hud.ui.tk_hud.TokenHudWindow`

- Uses the same bridge as the Qt HUD.
- Avoids Tk `-topmost` only when owner binding is active.
- Disables the Tk `after()` follow loop after the bridge starts successfully.
- Uses a tiny Tk main-thread dispatcher only to drain coalesced WinEvent signals;
  it does not poll Codex HWND position or UIA geometry.
- Commits attached HUD geometry through native `SetWindowPos` instead of Tk
  `geometry(...)` while owner-z mode is active.

## Scenario Coverage

Window move and resize

`EVENT_OBJECT_LOCATIONCHANGE` is delivered by the system while the Codex HWND is
moving or resizing.  The callback never moves toolkit widgets directly from the
hook thread; it wakes the UI thread, then the UI thread recomputes ROI geometry
and commits the final HUD rectangle through `SetWindowPos`.

Session title and width changes

Codex session switches often do not create a new top-level HWND.  When
`CODEX_USAGE_HUD_EVENT_DOCK_UIA=1` is enabled, the bridge also watches UIA
property changes in the Codex subtree.  Name changes and `BoundingRectangle`
changes invalidate the ROI and call the same attach path that window movement
uses.

Sidebar and bottom panel expansion

Chromium reflows caused by internal controls can be surfaced through opt-in UIA
`BoundingRectangle` changes.  The handler uses root-subtree registration instead
of targeting a single cached element, so it survives ROI element replacement
during virtual DOM updates.

Precise z-order occlusion

When owner binding is enabled, the HUD is an owned top-level window of Codex, not
a global topmost window.  DWM keeps it above Codex but below unrelated foreground
applications.  Tk keeps this mode opt-in because it has proven unsafe against the
Codex Electron window on some systems; its default event-driven mode uses
foreground/minimize WinEvents to show or hide the topmost fallback without
polling window geometry.

## Runtime Switch

Set `CODEX_USAGE_HUD_EVENT_DOCK=0` to disable the event-driven docking bridge and
fall back to the legacy timer path.  Qt offscreen tests also use the legacy path
so automated tests do not install global hooks.

Set `CODEX_USAGE_HUD_EVENT_DOCK_UIA=1` to add the UIA
`BoundingRectangleProperty` event hook on top of WinEvent docking.

Set `CODEX_USAGE_HUD_EVENT_DOCK_OWNER=1` to force cross-process owner binding.
Leave it unset for Tk unless validating the owner-z path specifically.
