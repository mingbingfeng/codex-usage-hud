# HUD Runtime Acceptance Checklist

Use this checklist after renderer runtime, active-session, file watcher, payload,
or desktop overlay IPC changes.

## Automated Gates

Run the phase gate:

```powershell
python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q
python -m compileall -q src tests tools
git diff --check
```

Run the latency harness for performance-sensitive changes:

```powershell
python tools/measure_renderer_latency.py --markdown-output renderer_latency_baseline.md
```

Review the `Regression Budgets` section in the generated Markdown. Any `FAIL`
row requires either a fix or an explicit note in the phase log explaining why
the regression is accepted.

## Performance Smoke

- Idle renderer HUD with no session/settings/layout/overlay events does not
  rebuild snapshots or push CDP payloads.
- Current session JSONL append refreshes via incremental parse.
- Settings bridge commands do not use localStorage polling.
- Desktop overlay state updates occur on state-file/directory events or
  keepalive only; unchanged payloads do not rewrite state.
- Desktop overlay click commands wake the main process through file events.

## DEBUG Error HUD Smoke

Set DEBUG mode before starting the HUD:

```powershell
$env:CODEX_USAGE_HUD_DEBUG = "1"
```

Verify:

- The renderer runtime errors panel appears with the `DEBUG HUD active` ready
  row when no errors are present.
- A renderer active-session mapping failure records `active_session.*`.
- A CDP update or target disconnect failure records `cdp.update_failed`.
- A file watcher overflow/degraded state records `file_watcher.*`.
- A desktop overlay helper error records `work_overlay_helper.*`.
- Error rows can be selected/copied.
- The panel can be dragged and its position persists after HUD refresh.

## Normal-Mode Diagnostics

With DEBUG disabled, verify internal runtime errors write structured records to
`renderer_fallback.log` without switching to unrelated fallback paths.

Required fields:

- `source`
- `severity`
- `code`
- `message`
- `context`
- `firstSeenAt`
- `lastSeenAt`

## Manual Regression Notes

Record the following in `progress.md` for each phase:

- Commands run.
- Any latency budget `FAIL` rows.
- Any accepted remaining fallback.
- Any platform-specific smoke not run locally.
