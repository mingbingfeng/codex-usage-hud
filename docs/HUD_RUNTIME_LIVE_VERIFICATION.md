# HUD Runtime Live Verification

Last updated: 2026-07-08

This checklist is for the remaining items that are not fully proven by:

- automated tests
- local latency baselines
- in-app browser smoke-host pages

For a mixed automated/manual run, start with:

```powershell
python tools/run_live_acceptance.py --prepare-mode debug
```

That command reruns the automated phase gate, writes a timestamped report under
`artifacts/live_acceptance/`, stops any existing HUD, and starts the HUD in
DEBUG mode for the live checks below.

For the normal-mode diagnostics pass, use:

```powershell
python tools/run_live_acceptance.py --prepare-mode normal --skip-automated-checks
```

To persist manual observations into the generated report, provide a JSON file:

```powershell
python tools/run_live_acceptance.py --prepare-mode none --skip-automated-checks --manual-observations path\to\manual_observations.json
```

Example shape:

```json
{
  "active_session_latency": {
    "status": "fail",
    "observed_ms": 2500,
    "note": "Visible HUD switch took about 2-3 seconds."
  },
  "idle_cpu": {
    "status": "not-run",
    "note": "No Task Manager capture was recorded."
  }
}
```

If you want the tool to collect a simple HUD-process idle CPU sample for the
launched daemon, add:

```powershell
python tools/run_live_acceptance.py --prepare-mode debug --idle-cpu-sample-seconds 60
```

Use this runbook when you need final evidence from a real Codex App window.

## Scope

These are the remaining live-runtime items called out in
[HUD_RUNTIME_COMPLETION_AUDIT.md](/E:/Project/codex-usage-hud/docs/HUD_RUNTIME_COMPLETION_AUDIT.md):

1. Real Codex App end-to-end latency
2. Real Codex App idle CPU / no background work
3. Real Codex App page-lifecycle confirmation beyond localhost smoke-host pages

## Preparation

1. Stop any existing HUD instance:

```powershell
codex-hud --stop
```

2. Enable DEBUG mode for the verification run:

```powershell
$env:CODEX_USAGE_HUD_DEBUG = "1"
```

3. Start the renderer HUD:

```powershell
codex-hud --daemon
```

4. Confirm the following files are available for evidence collection:

- HUD config:
  `%LOCALAPPDATA%\codex-usage-hud\hud_settings.json`
- daemon log:
  `%LOCALAPPDATA%\codex-usage-hud\daemon.log`
- runtime diagnostics:
  `%LOCALAPPDATA%\codex-usage-hud\renderer_fallback.log`

5. If possible, start a screen recording before the checks below.

## Live Checks

### 1. DEBUG HUD Ready Row

Goal:
- verify the runtime errors panel appears in a real Codex App renderer and shows
  the ready row with no active errors

Steps:
1. Open Codex App with the HUD attached.
2. Expand the runtime errors panel.
3. Verify the panel shows:
   - `debug.ready`
   - `DEBUG HUD active`
4. Drag the panel to a visibly different position.
5. Refresh or navigate enough to force HUD reattachment / redraw.
6. Verify the panel remains expanded and the position persists.

Evidence to capture:
- screen recording or screenshots before/after drag

### 2. Active Session Switch Latency

Goal:
- verify visible active-session HUD state follows the currently selected Codex
  thread within the `<150ms` target

Suggested setup:
- two threads with clearly different titles or visible token totals

Steps:
1. Open two different Codex threads.
2. Start screen recording.
3. Click thread A, then thread B, multiple times.
4. Compare the click moment with the HUD state change frame-by-frame.

Pass condition:
- HUD state visibly tracks the selected thread within about 9 frames at 60fps
  (`~150ms`)

Evidence to capture:
- screen recording
- optional note of observed worst-case frame delta

### 3. Current Session Append Latency

Goal:
- verify current-session usage reacts to real request activity within the
  `<250ms` target

Steps:
1. Open a live Codex thread.
2. Start screen recording.
3. Submit a short prompt that triggers visible request activity.
4. Observe the first visible request / token usage change in the HUD.

Pass condition:
- visible current-session request/usage state reacts within about 15 frames at
  60fps (`~250ms`)

Evidence to capture:
- screen recording
- optional note of observed worst-case frame delta

### 4. Idle CPU / No Background Work

Goal:
- verify there is no sustained recurring CPU work when the HUD is attached but
  no relevant session/settings/layout/overlay events occur

Steps:
1. Leave Codex App on a stable idle thread.
2. Do not type, switch sessions, resize panels, or trigger overlay commands for
   at least 60 seconds.
3. Observe the HUD process in Task Manager.

Recommended evidence:
- Task Manager `Details` or `Processes` view
- optional Resource Monitor if needed

Pass condition:
- no sustained CPU usage from the HUD process
- no obvious periodic bursts that suggest recurring snapshot rebuilds or payload
  pushes during idle

### 5. Normal-Mode Diagnostics

Goal:
- verify non-DEBUG mode still writes structured diagnostics in a real Codex App
  run

Steps:
1. Stop the HUD:

```powershell
codex-hud --stop
```

2. Disable DEBUG mode:

```powershell
Remove-Item Env:CODEX_USAGE_HUD_DEBUG -ErrorAction SilentlyContinue
```

3. Start the HUD again:

```powershell
codex-hud --daemon
```

4. Reproduce one known runtime error if practical.
5. Inspect `renderer_fallback.log`.

Pass condition:
- a new JSON line appears with:
  - `source`
  - `severity`
  - `code`
  - `message`
  - `context`
  - `firstSeenAt`
  - `lastSeenAt`

## Remaining Manual-Only Items

If the project wants a strict completion claim, these are the most important
manual-only evidence items:

- real Codex App active-session latency under `<150ms`
- real Codex App current-session update latency under `<250ms`
- real Codex App idle CPU / no recurring background work

## Close-Out Guidance

After running this checklist:

1. Update [progress.md](/E:/Project/codex-usage-hud/progress.md) with:
   - commands run
   - which live checks passed
   - which checks were not run
   - any platform-specific constraints
2. Update [HUD_RUNTIME_COMPLETION_AUDIT.md](/E:/Project/codex-usage-hud/docs/HUD_RUNTIME_COMPLETION_AUDIT.md)
   to move any newly-proven items out of `Partially proven` / `Unverified locally`.
