# Live Acceptance Report

- Generated at: `2026-07-28T06:01:38.512586Z`
- Prepare mode: `normal`
- Output dir: `output\live_acceptance_overflow`

## Runtime Paths

- daemon_log: `C:\Users\zjxqm\AppData\Local\codex-usage-hud\daemon.log`
- renderer_diagnostic: `C:\Users\zjxqm\AppData\Local\codex-usage-hud\renderer_fallback.log`
- settings: `C:\Users\zjxqm\AppData\Local\codex-usage-hud\hud_settings.json`

## Automated Checks

| Check | Status | Command / Artifact |
|-------|--------|--------------------|
| stop_hud | PASS | C:\Users\zjxqm\AppData\Local\Programs\Python\Python314\python.exe -m codex_usage_hud --stop |

## HUD Preparation

- status: `PASS`
- mode: `normal`
- pid: `9032`

## Manual Checks

### Normal-Mode Diagnostics

- status: `pending`
- Reproduce one known runtime error if practical.
- Inspect `renderer_fallback.log`.
- Confirm a new JSON line includes source, severity, code, message, context, firstSeenAt, and lastSeenAt.
