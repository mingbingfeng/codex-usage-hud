# Live Acceptance Report

- Generated at: `2026-07-09T02:40:08.857435Z`
- Prepare mode: `debug`
- Output dir: `E:\Project\codex-usage-hud\artifacts\live_acceptance\20260709-103850`

## Runtime Paths

- daemon_log: `C:\Users\zjxqm\AppData\Local\codex-usage-hud\daemon.log`
- renderer_diagnostic: `C:\Users\zjxqm\AppData\Local\codex-usage-hud\renderer_fallback.log`
- settings: `C:\Users\zjxqm\AppData\Local\codex-usage-hud\hud_settings.json`

## Automated Checks

| Check | Status | Command / Artifact |
|-------|--------|--------------------|
| phase_gate_pytest | PASS | C:\Users\zjxqm\AppData\Local\Programs\Python\Python314\python.exe -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_file_watcher.py -q |
| compileall | PASS | C:\Users\zjxqm\AppData\Local\Programs\Python\Python314\python.exe -m compileall -q src tests tools |
| git_diff_check | PASS | git diff --check |
| latency_harness | PASS | All regression budgets passed. |
| stop_hud | PASS | C:\Users\zjxqm\AppData\Local\Programs\Python\Python314\python.exe -m codex_usage_hud --stop |
| idle_cpu_sample | PASS | HUD process average CPU during idle sample: 0.706% |

## HUD Preparation

- status: `PASS`
- mode: `debug`
- pid: `36760`

## Manual Checks

### DEBUG HUD Ready Row

- status: `pending`
- Open Codex App with the HUD attached.
- Expand the runtime errors panel and confirm `debug.ready` and `DEBUG HUD active` are visible.
- Drag the panel, then refresh or redraw the HUD and confirm the position persists.

### Active Session Switch Latency

- status: `pending`
- Open two Codex threads with visibly different titles or totals.
- Record multiple thread switches.
- Confirm the HUD tracks the selected thread within about 9 frames at 60fps (~150ms).

### Current Session Append Latency

- status: `pending`
- Open a live Codex thread.
- Submit a short prompt that produces visible request activity.
- Confirm the HUD reacts within about 15 frames at 60fps (~250ms).

### Idle CPU / No Background Work

- status: `pending`
- Leave Codex App on a stable idle thread for at least 60 seconds.
- Do not type, switch sessions, resize panels, or trigger overlay commands.
- Confirm there is no sustained HUD CPU usage or obvious periodic bursts.
