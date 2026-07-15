# Codex App pet and session-overlay evidence

Date: 2026-07-15 (Asia/Shanghai)

## Evidence sources

- Installed desktop package: `E:\Work\CodexRelocated\app\resources\app.asar`, 199,368,680 bytes, modified 2026-07-14.
- Desktop logs: `C:\Users\zjxqm\AppData\Local\Codex\Logs\2026\07\15\codex-desktop-22d67242-05ee-4151-852c-30310f8257b1-1956-t0-i1-023055-0.log`.
- HUD runtime evidence: `C:\Users\zjxqm\AppData\Local\codex-usage-hud\work-overlay-transitions.jsonl`, `work-overlay-7052-1784084887570.json`, `renderer_fallback.log`, and `renderer_cdp_state.json`.

## Live renderer evidence

The Codex desktop log identifies release `26.707.71524` and proves that the same conversation is represented by two renderer windows:

- main renderer: `rendererWebContentsId=1`, `rendererWindowAppearance=primary`, `role=owner`;
- pet renderer: `rendererWebContentsId=2`, `rendererWindowAppearance=avatarOverlay`, `role=follower`.

For conversation `019f63ae-c8ad-7001-81ce-3a67cfab7c38`, the log records `thread/start`, `thread_stream_view_activity_changed active=true`, `thread/resume`, `thread/read`, and subsequent `thread_stream_role_changed`. The pet window is not an independent work-directory watcher; it follows the same conversation ID and app-server/thread event stream.

The current HUD runtime independently records live overlay transitions such as `card -> completed`, `completed -> card`, and `status_changed` for the same session IDs. This confirms that the local square bubble can consume a session-keyed event-driven state stream, but it does not prove that its payload is identical to the Codex pet payload.

Live CDP interaction was blocked in this run. `renderer_cdp_state.json` pointed to port `54469`, but `netstat` showed only `TIME_WAIT`; `renderer_fallback.log` and `daemon.log` recorded `renderer_cdp_port_reassign_failed`, `renderer_cdp_port_restart_card_unavailable`, and `Timed out waiting for CDP command response`. Therefore no claim is made that the pet notification row was clicked successfully during this run.

## Packaged pet module evidence

The archive contains these relevant modules:

- `webview/assets/avatar-mascot-button-C6QI44bt.js`
- `webview/assets/avatar-overlay-page-NEQOnYbT.js`
- `webview/assets/avatar-overlay-native-page-3NHd7zoc.js`
- `webview/assets/avatar-overlay-mascot-size-DA0NX45H.js`
- `webview/assets/avatar-overlay-open-state-signal-YKnlkTne.js`
- `webview/assets/pet-egg-ChQL-xNy.js`
- `webview/assets/pet-install-state-Bmk33G_M.js` and `pet-install-state-BVr0gNS4.js`
- `webview/assets/pets-settings-CeAjCh2C.js`

The extracted module logic establishes the following contracts:

1. `avatar-overlay-native-page` obtains local conversation data and builds the overlay session list. The mascot's own click dispatches `open-current-main-window`; it is a focus/open-current-window action, not a target-thread selector.
2. `use-avatar-overlay-selection` maps a session into a notification containing `localConversationId`, `status`, `updatedAtMs`, `waitingRequest`, and `action: { path: actionPath }`.
3. Activating a notification with an open action dispatches `open-in-main-window` with `path: action.path`. This is the target-session jump mechanism exposed by the packaged UI boundary.
4. The notification renderer has explicit branches for `tool`, `exec`, `network`, `permission`, and `plan` waiting states, so tool-action progress is represented as structured session notifications rather than only the last assistant text.

These are package/code findings, not a promise that the private action path or internal module names are stable across Codex releases.

## Comparison with this project

The local PySide6 overlay already has the compatible outer contract:

- `work_item_to_overlay_dict()` emits `sessionId`, `targetTitle`, `status`, `statusText`, `lastText`, `progress`, `updatedAt`, and accounting fields.
- `work_overlay_qt.switch_item()` emits `activateSession` with the session ID, title, and workdir.
- The renderer runtime maps active-session callbacks to `active_session_changed`, then refreshes the current snapshot/work overlay; bounded composer follow-ups cover the new-session canonical-ID gap without adding idle polling.
- Completion is represented by `status="recent"`, which drives the existing square-card to completion-circle transition.

The reproducible conclusion is to copy the pet's data boundary and event semantics, not its private IPC or minified assets: canonical conversation/session ID as the primary key; structured status/activity as the preview source; explicit target activation; and event-driven refresh with bounded reconciliation for provisional IDs.

## Risk boundary and next live gate

- Do not title-match or substitute the newest rollout when the canonical session ID is missing.
- Do not hard-code the private `actionPath` or copy `open-in-main-window` as an undocumented external API. The local implementation should keep its `activateSession(sessionId)` contract and use the existing CDP/session-switch backend.
- A complete end-to-end pet click result still requires a live Codex renderer target and an observable notification-row click. Until that gate passes, the pet jump behavior is package-confirmed but live-click-unconfirmed.

## Current HUD CDP and live DOM evidence (2026-07-15, 12:11-12:16 +08:00)

The remembered last-CDP-port record is real and is used by the renderer bootstrap:

- `C:\Users\zjxqm\AppData\Local\codex-usage-hud\renderer_cdp_state.json` contains `lastSuccessfulPort=58655` and an update time of `2026-07-15T12:11:08.206196+08:00`.
- The running HUD is PID `21304` (`python.exe -m codex_usage_hud.cli`). The Codex desktop process is PID `16608` and was launched with `--remote-debugging-port=58655`.
- `http://127.0.0.1:58655/json` returned two live page targets: `app://-/index.html?initialRoute=%2Favatar-overlay` and `app://-/index.html`. The first is the avatar overlay renderer; the second is the main Codex renderer.

The live avatar target currently exposes `data-testid="avatar-mascot-button"` and `data-testid="avatar-overlay-notification-badge"`. Its notification row is a normal renderer `role="button"` with an `aria-label` ending in `打开通知`; its live React props include an `onClick` handler whose visible shape is `()=>{C&&o?.(t)}`. The main target exposes exact session rows such as `data-app-action-sidebar-thread-id="local:019f63e8-9d44-7ab0-b280-576d1c15c658"`. This proves the click starts inside the already-running renderer and receives a concrete session object before any local HUD command path is involved. It does not prove the private callback or native action dispatch is a stable public API.

The latency difference has a structural explanation. The local desktop overlay writes an `activateSession` JSON command to a file, waits for the `FileChangeWatcher`, then runs window preparation, invokes the Python `CdpSessionSwitchBackend`, performs target discovery plus `Runtime.evaluate`, and may retry after a `0.16s` sidebar-reveal wait. It also has a `0.08s` refocus delay. The pet notification handler is already in the Electron renderer and can dispatch its internal selection/open action directly. Therefore the pet path avoids the extra process boundary, filesystem wakeup, window-foreground preparation, CDP command round trip, and bounded retry. This is why it can feel materially faster; it is not evidence that the private IPC should be copied.

## Live click acceptance result (2026-07-15)

The live click was executed against the running `/avatar-overlay` target. At click time the only notification row represented the current provisional session `local:client-new-thread:2eafb974-732f-4d14-bf9b-3b7fbabcbed0` (`继续宠物会话研究`), so this was a same-session notification activation rather than a cross-session jump. The DOM click dispatch completed in approximately `1.8ms`; this is renderer dispatch time only and is not a measured end-to-end navigation latency.

For a cross-session attempt, the main renderer was switched through its exact sidebar ID to `local:019f63e8-9d44-7ab0-b280-576d1c15c658` (`排查 linux.do 访问问题`) in approximately `430ms`. After that switch the avatar page remained alive but its notification tray had collapsed/rebuilt to mascot-only DOM with zero notification rows, so there was no second pet notification row to click. The main renderer was then restored to `继续宠物会话研究`. This leaves the cross-session pet-click gate unproven, but the failure mode is now observed as overlay state/tray lifecycle, not missing CDP connectivity.
