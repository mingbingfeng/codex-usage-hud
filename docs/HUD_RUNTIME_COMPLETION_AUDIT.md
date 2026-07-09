# HUD Runtime Completion Audit

Last updated: 2026-07-09

This audit evaluates the current renderer-runtime work against:

- `task_plan.md`
- `docs/HUD_RUNTIME_ACCEPTANCE_CHECKLIST.md`
- `docs/HUD_RUNTIME_LIVE_VERIFICATION.md`
- current automated tests
- current local latency baseline
- in-app browser smoke-host verification

Status terms used below:

- `Proven`: direct current-state evidence exists.
- `Partially proven`: strong indirect or scoped evidence exists, but not at the full real-runtime scope.
- `Observed fail`: direct local evidence exists that the current behavior misses the target.
- `Accepted`: the current behavior or evidence level was explicitly accepted by the user for close-out, and will not be investigated further in this round.
- `Unverified locally`: no sufficiently strong local evidence yet.

## Evidence Sources

- Automated gates:
  - `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_file_watcher.py -q`
  - `python -m compileall -q src tests tools`
  - `git diff --check`
- Local latency baseline:
  - [renderer_latency_baseline.md](/E:/Project/codex-usage-hud/renderer_latency_baseline.md)
- Key automated tests:
  - [tests/test_ui.py](/E:/Project/codex-usage-hud/tests/test_ui.py)
  - [tests/test_renderer_hud.py](/E:/Project/codex-usage-hud/tests/test_renderer_hud.py)
  - [tests/test_file_watcher.py](/E:/Project/codex-usage-hud/tests/test_file_watcher.py)
- Smoke-host browser evidence:
  - recorded in [progress.md](/E:/Project/codex-usage-hud/progress.md)
- Latest mixed live acceptance run:
  - [live_acceptance_report.md](/E:/Project/codex-usage-hud/artifacts/live_acceptance/20260709-094804/live_acceptance_report.md)
  - [manual_observations.json](/E:/Project/codex-usage-hud/artifacts/live_acceptance/20260709-094804/manual_observations.json)

## Task Plan Success Standards

| Requirement | Status | Evidence |
|---|---|---|
| active session response under 150ms; failure shows error HUD | Observed fail | The 2026-07-09 mixed live acceptance run captured a user-observed visible switch latency of about `2-3s` before the HUD top-line session data reflected the newly selected thread. That misses the `<150ms` target by a wide margin. The same run also left a new `active_session.unmatched_thread` record in `renderer_fallback.log`, so real-runtime failure visibility is present. |
| current session usage append refresh under 250ms | Partially proven | Latest `append_then_incremental_parse_and_payload` P90 is `157.377ms` in [renderer_latency_baseline.md](/E:/Project/codex-usage-hud/renderer_latency_baseline.md), which stays within the stated `<250ms` target. This is still a local pipeline measure, not full live CDP + renderer paint latency. |
| top/bottom HUD payload only updates on change; no idle snapshot rebuild | Partially proven | Tests cover no localStorage polling, unchanged runtime-signature idle path, and layout events without snapshot rebuild: `test_renderer_loop_does_not_poll_local_storage_settings_commands_when_idle`, `test_renderer_loop_skips_snapshot_when_runtime_signature_is_unchanged`, `test_renderer_loop_handles_layout_event_without_snapshot_refresh`. No live production CPU profile was taken. |
| bubble no longer polls state every 160ms; push or file-event wakeup | Proven | `test_work_overlay_helper_uses_qfilesystemwatcher_for_state_updates`, `test_work_overlay_command_pump_uses_file_watcher_event`, `test_desktop_work_overlay_skips_unchanged_state_until_keepalive`. |
| idle CPU has no recurring snapshot/scan/payload work without relevant events | Unverified locally | Event-driven loop and idle-path tests are strong, but the 2026-07-09 live run did not capture Task Manager or process CPU sampling evidence. |
| DEBUG mode shows renderer/CDP/file/overlay failures in error HUD | Proven | Automated payload/tests plus smoke-host browser verification for `file_watcher.degraded` and `work_overlay_helper.state_read_failed`. |

## Acceptance Checklist

### Automated Gates

| Requirement | Status | Evidence |
|---|---|---|
| phase gate passes | Proven | Current worktree passed pytest subset, compileall, and `git diff --check`. |
| latency harness budgets pass | Proven | Latest [renderer_latency_baseline.md](/E:/Project/codex-usage-hud/renderer_latency_baseline.md) has all `Regression Budgets` rows at `PASS`. |

### Performance Smoke

| Requirement | Status | Evidence |
|---|---|---|
| idle HUD does not rebuild snapshots or push payloads without relevant events | Partially proven | `test_renderer_loop_skips_snapshot_when_runtime_signature_is_unchanged`, `test_renderer_loop_handles_layout_event_without_snapshot_refresh`. The 2026-07-09 live run still lacks direct CPU/process sampling proof. |
| current session JSONL append refreshes via incremental parse | Proven | `test_build_snapshot_uses_incremental_parser_for_current_session`; local latency baseline shows append path under budget. |
| settings bridge commands do not use localStorage polling | Proven | `test_renderer_loop_does_not_poll_local_storage_settings_commands_when_idle`, `test_client_does_not_expose_renderer_settings_polling_fallback`. |
| desktop overlay state updates only on events/keepalive; unchanged payloads do not rewrite state | Proven | `test_desktop_work_overlay_skips_unchanged_state_until_keepalive`, `test_work_overlay_helper_uses_qfilesystemwatcher_for_state_updates`. |
| desktop overlay click commands wake main process through file events | Proven | `test_work_overlay_command_pump_uses_file_watcher_event`. |

### DEBUG Error HUD Smoke

| Requirement | Status | Evidence |
|---|---|---|
| panel appears with `DEBUG HUD active` ready row when no errors are present | Proven | `test_renderer_script_renders_debug_error_hud`; in-app browser smoke-host ready page showed `debug.ready` + `DEBUG HUD active`. |
| renderer active-session mapping failure records `active_session.*` | Proven | Automated evidence via `test_build_snapshot_records_renderer_unmatched_runtime_error` plus browser smoke-host rendering of `active_session.unmatched_thread` with select/copy proof of context text. |
| CDP update/target disconnect failure records `cdp.update_failed` | Proven | Automated evidence via `test_record_cdp_update_failure_adds_runtime_error` plus browser smoke-host rendering of `cdp.update_failed` with select/copy proof of context text. |
| file watcher overflow/degraded records `file_watcher.*` | Proven | `test_renderer_file_event_source_records_degraded_polling`, `test_renderer_file_event_source_records_overflow_without_polluting_reasons`, and browser smoke-host rendering of `file_watcher.degraded`. |
| desktop overlay helper error records `work_overlay_helper.*` | Proven | `test_work_overlay_helper_runtime_error_writes_normal_mode_diagnostic` and browser smoke-host rendering of `work_overlay_helper.state_read_failed`. |
| error rows can be selected/copied | Partially proven | Browser smoke-host proved page-context `select` and `copy` extraction for both `file_watcher.degraded` and `work_overlay_helper.state_read_failed`. Native browser shortcut copy is still policy-limited in this environment. |
| panel can be dragged and position persists after HUD refresh | Proven | Browser smoke-host proved drag from roughly `(16, 526)` to `(136, 468)`, then reload preserved both position and expanded state. |

### Normal-Mode Diagnostics

| Requirement | Status | Evidence |
|---|---|---|
| structured records written to `renderer_fallback.log` without fallback path switching | Proven | JSON-record assertions now cover `active_session.unmatched_thread`, `cdp.update_failed`, `file_watcher.degraded` recorded/resolved, and `work_overlay_helper.state_read_failed`. |
| required fields `source`, `severity`, `code`, `message`, `context`, `firstSeenAt`, `lastSeenAt` are present | Proven | Field-level assertions in [tests/test_ui.py](/E:/Project/codex-usage-hud/tests/test_ui.py) parse actual JSON lines from `renderer_fallback.log`. |

## Remaining Unverified Or Partially Verified Items

These are the main remaining gaps before claiming the entire objective is fully closed:

1. Real Codex App end-to-end latency state:
   - active session response `<150ms` is now contradicted by a local observed result of about `2-3s`
   - current session usage refresh `<250ms` still lacks full live CDP + renderer paint evidence
2. Real live-runtime idle CPU proof:
   - no recurring background snapshot/payload work under an attached Codex App session
3. Real live-runtime proof inside a real Codex App window rather than the localhost smoke host for:
   - `cdp.update_failed`
4. Separation of evidence classes in final close-out:
   - automated tests
   - smoke-host browser verification
   - items that still require manual verification against a real Codex App window

## Current Audit Conclusion

The renderer-runtime refactor is substantially complete at the code, benchmark,
and smoke-host verification level, but it is not yet complete at the visible
real-runtime UX boundary.

Update on 2026-07-09:

- The user explicitly accepted the current live-runtime behavior for the three
  main close-out checks and chose not to continue investigating the measured
  latency discrepancy in this round.
- That means the remaining work is administrative and release-oriented:
  documentation reconciliation, cleanup of temporary instrumentation, and final
  integration steps.

What still keeps completion from being fully proven is now primarily proof
quality at the live Codex App runtime boundary:

1. the latest live run observed active-session visible latency around `2-3s`,
   which is far above the `<150ms` target
2. real Codex App current-session live latency is still not directly measured
3. real Codex App idle CPU / no-background-work evidence is still missing
4. a small set of items are still proven through smoke-host pages rather than a
   live Codex App window

There are no longer any open regression-budget failures in the latest local
baseline. The remaining work is now split between:

1. documenting live-runtime failures and gaps accurately
2. adding better live-observation tooling
3. investigating the visible active-session latency and related renderer/CDP
   boundary behavior

Use [HUD_RUNTIME_LIVE_VERIFICATION.md](/E:/Project/codex-usage-hud/docs/HUD_RUNTIME_LIVE_VERIFICATION.md)
as the concrete runbook for those remaining live-runtime checks.
