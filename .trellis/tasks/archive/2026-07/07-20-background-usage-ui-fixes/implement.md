# Implementation Plan

## 1. Lock In Regression Tests

- [x] Add a UI test proving `background_usage + recent` is still a card and cannot enter completed layout/transition classification.
- [x] Add a mixed-shape steady-refresh test proving grouped completed/card widget records update by item ID rather than visible-list position.
- [x] Replace the old “background reminder takes the first visible slot” expectation with live-like cases proving the current task stays first at item limits 1 and 6.
- [x] Extend helper command tests to prove a successful `openBackgroundUsage` enqueue invokes the Codex refocus path and publishes activation metadata, while dismiss does not.
- [x] Extend renderer command tests for a query/open response containing a selected detail preview with Prompt removed.
- [x] Add renderer script contract assertions for stale-response return, bounded timeout and scroll capture/restore helpers.

## 2. Fix Bubble Interaction

- [x] Harden `_item_is_completed()` with the background-usage kind guard.
- [x] Match steady-state widget updates by kind and item ID, with a full rebuild fallback when the existing record set cannot cover the visible payload.
- [x] Partition current active work before background, completed and other active groups so item-limit cropping cannot remove the current task.
- [x] Refocus Codex after queuing a background usage open command, preserving best-effort behavior and cross-platform helpers.
- [x] Keep confirmation, session switching and ordinary session completion behavior unchanged.

Validation checkpoint:

```powershell
python -m pytest tests/test_ui.py -q
```

## 3. Fix Initial Loading And Response Delivery

- [x] Build a Prompt-free selected detail preview alongside background usage queries.
- [x] Return list + selected preview in the first bubble-open response and remove the duplicate renderer query.
- [x] Make stale query/detail responses terminally ignored instead of triggering another force refresh.
- [x] Clear one-shot background responses only after renderer acknowledgement; retain them on failed delivery.
- [x] Add a bounded loading timeout with retryable error state.
- [x] Keep HTTP fallback and lazy Prompt detail compatible.

Validation checkpoint:

```powershell
python -m pytest tests/test_background_usage.py tests/test_settings_bridge.py tests/test_renderer_hud.py tests/test_ui.py -q
```

## 4. Preserve Scroll Positions

- [x] Capture history, detail and narrow-layout body scroll positions before background markup replacement.
- [x] Restore history by filter key and detail by event ID after synchronous and async repaint.
- [x] Reset only when the filter result set changes or a new event has no saved detail position.
- [x] Verify repeated selection, detail hydration and Prompt toggling do not jump either pane.

## 5. Full And Live Verification

- [x] Run focused and full automated checks.
- [x] Restart the real HUD/helper so the running process loads the patch.
- [x] Verify a real background bubble remains square before and after link interaction.
- [x] Reproduce the reported title/shape swap from the clipboard, then verify the same live JSON in a fresh helper renders completed sessions as circles and all background usage records as cards.
- [x] With multiple background reminders and completed sessions present, verify the live overlay JSON plus helper windows still expose the `current=true` task within the configured item limit.
- [x] Minimize Codex, click the helper link through its native hotspot, and verify the window is restored/foreground plus the correct Tab/event is visible.
- [x] Use CDP to record initial loading duration, row count, selected event, error state and both scroll positions across interaction.
- [x] Inspect overlay command JSONL and renderer logs for a single open/query flow without repeated `backgroundUsage` update failures.

Live evidence: after the desktop unlocked, a system-level click hit the native
helper link HWND, restored the minimized Codex window, made its exact main HWND
foreground, and opened the matching background-usage event in renderer v32.

Final gate:

```powershell
python -m pytest tests/test_background_usage.py tests/test_settings_bridge.py tests/test_renderer_hud.py tests/test_ui.py -q
python -m pytest -q
python -m compileall -q src tests tools
git diff --check
```

## Risk And Rollback Points

- Window activation can be denied by Windows foreground rules; retain the command and expose the result rather than treating focus failure as navigation failure.
- Request deduplication must not suppress a user-initiated refresh with changed filters; sequence and filter signatures remain distinct.
- Preview assembly must strip Prompt before transport. Any test showing Prompt in query/open payload blocks delivery.
- Scroll restoration must not apply an old event's detail offset to a newly selected event.
- Ordinary session bubbles and active-session switching are outside the change boundary; any regression there is a rollback condition.
