# HUD Runtime Completion Audit

Last updated: 2026-08-04

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

## 2026-08-03 Evidence Refresh

The current worktree now passes the full automated suite:

```text
python -m pytest -q                         PASS (only existing skips)
python -m compileall -q src tests tools     PASS
git diff --check                             PASS
python tools/check_facade_patch_inventory.py PASS (0 paths, 0 references)
targeted P7/P9/P10/P11 owner Ruff checks       PASS
```

The checked-in `tests/contracts/facade_patch_inventory.json` now records the
post-P8 empty extraction (`0 paths / 0 references`). The dedicated inventory
unit tests still cover historical migration metadata and reject any reintroduced
facade patch path.

The structural result is `runtime_orchestration.py` at 352 lines with the
composition root and explicit compatibility forwarding only. P7.2 moved the
data-only `RendererSessionPorts` contract to `renderer_session_ports.py` (106
lines), and P7.4 moved renderer refresh/invalidation policies to
`renderer_runtime_policies.py` (349 lines). P9 moved one-shot context/resource
and bridge/client assembly to `renderer_runtime_assembly.py` (263 lines),
leaving `renderer_runtime.py` at 409 lines with startup/window/CDP attachment,
worker/event-loop wiring, and final shutdown. P7.3 moved
overlay window/refocus actions to `overlay_window.py` (104 lines), and P7.5
moved transition-audit projection/JSONL persistence to
`overlay_transition_audit.py` (97 lines). P9 moved pure helper-health and
backoff decisions to `overlay_supervision.py` (187 lines), leaving
`desktop_overlay.py` at 857 lines before the later P11 channel extraction. P7.6 moved state signature/envelope/writer contracts to
`overlay_state.py` (82 lines). P7.7 moved pure event normalization to
`renderer_event_normalization.py` (131 lines), leaving
`renderer_event_loop.py` at 716 lines with explicit compatibility re-exports.
P7.1 still owns the pure wait planner (`renderer_wait.py`, 150 lines), and P8
acceptance reports validate evidence scope and reject daemon-only
or smoke-host artifacts as real-App passes. P9 also moved pure top-level
task/activity/trail projection to `renderer_activity_projection.py` (488 lines),
leaving `renderer_payload_builder.py` at 1560 lines before the later P11 request
projection extraction, with final envelope assembly and compatibility wrappers.
P10 moved pure activity trail
filtering/deduplication to `renderer_activity_trail.py` (202 lines), split
`renderer_assets/layout.py` into five static fragments, and moved the settings
support/about markup to `renderer_assets/settings_support_panels.py` (113
lines), leaving the fixed manifest and template hash unchanged. P11 moved pure
event reduction to `renderer_event_reduction.py` (166 lines), overlay command
JSONL/ack I/O to `overlay_command_channel.py` (119 lines), and request/task
projection to `renderer_request_projection.py` (380 lines), leaving
`desktop_overlay.py` at 816 lines and `renderer_payload_builder.py` at 1441
lines. The P7.2-P7.7, P8, P9, P10, and P11 owner slices are implemented and
focused tests pass. The earlier role-list-fix package remains historical
evidence. The current canonical-id selection-identity package proof is:

- wheel: `tmp/final-wheel-20260804-canonical-id/codex_usage_hud-1.0.5-py3-none-any.whl`
- wheel SHA-256: `6941c88962bfe7f8479e82b20f27d74fe8838634a50fbfd21d49eb44fcb998be`
- onefile: `tmp/final-pyinstaller-20260804-canonical-id/dist/codex-hud-canonical-id.exe`
- onefile SHA-256: `710278f495d8e1ca9fbfb7ac18ba4a920be860f4d6845e5dc1bbc99c21501cf3`
- fresh-venv import resolves all 18 ordered assets, ending with `15_router`;
  current template is `624498` bytes with SHA-256
  `fae278f4b50e52b580084b841ef903e7bead3cdf3210870e9375eea0b2477d1e` and
  empty-catalog bundle is `624470` bytes with SHA-256
  `d60d7dd601c13e270946ccb6d6734673a19c0a733ebafd09b1d01dbe3306c2a4`;
  PyInstaller build completes and the hidden `--help` process exits 0

The canonical-id change is intentionally narrower than a visible UI change:
when the canonical thread UUID is unchanged, raw renderer-ID prefix and title
updates remain payload updates but no longer allocate a new authoritative
`selectionSeq`. Different canonical IDs and provisional/title-only/new-session
boundaries retain their existing sequence behavior. Node regression coverage,
the focused renderer/active-session suites, and the full automated suite pass.
The daemon used for the latest real-App follow capture was restarted at
2026-08-04 06:47:51 (PID 24092). The
`real-codex-app-active-session-follow-timing-20260804.json` artifact was
captured at 07:07:29 and proves a current-process normal existing-thread
follow, ending at `selectionSeq=227` and `appliedSeq=227`. It does not
specifically trigger the header-title/empty-raw-ID transient guard or inject
an acknowledgement failure/retry, so those fault-path gates remain unproven.

The selected 1.94 MB local session baseline in
`tmp/final_renderer_latency_small_20260803.md` passes every configured budget.
The newest 4.36 MB session run exceeds the parse and current-file refresh budgets,
so input-size sensitivity remains visible rather than being treated as a pass.
The benchmark still excludes live CDP transport, renderer paint, and real-app
idle CPU sampling.

Real Windows Codex App evidence captured on 2026-08-03 is stored under
`output/playwright/`:

- `real-codex-app-interaction-20260803.json` plus PNGs prove a live
  `app://-/index.html` HUD startup, the DEBUG ready row, opening and closing
  all five current settings tabs, root replacement, and remove/reinject.
- `real-codex-app-session-switch-20260803.json` proves two existing thread IDs
  switch through the real CDP controller and match the selected sidebar rows.
- `real-codex-app-visible-latency-20260803.json` measures visible HUD stability
  at about `1481ms` to the 1.8M session and `545ms` back to the original
  session; both miss the `<150ms` target.
- `real-codex-app-active-session-segments-20260803.json` adds renderer/Python
  follow-timing markers for the same real-App path. The two switches reached
  `payloadSendStartedAt` `48ms` and `41ms` after `observedAt`; the largest
  Python segment was `received_to_resolved` at `43ms` and `39ms`, while the
  remaining build, snapshot, and payload-send tail was `5ms` and `2ms`.
- `real-codex-app-active-session-browser-timeline-20260803.json` adds a
  temporary page-side MutationObserver/requestAnimationFrame timeline. The
  first payload/renderer target appeared at `35.7ms` and `50.8ms`, and the
  sidebar target row at `295.9ms` and `242.7ms`; the full stable criteria took
  `1129.8ms` and `2391.1ms` across `6` and `4` consecutive frames. The probe
  was removed after each switch and did not write storage or product bindings.
- `live-idle-sample-20260803-settled.json` records a low `0.189%` normalized
  CPU candidate with stable RSS, but the current agent's own rollout JSONL
  changed during the window, so it is not an idle acceptance pass.
- `real-codex-app-idle-settled-20260803.json` records a read-only 60-second
  real-App Renderer sample with zero HUD-root mutations and about `0.0065%`
  normalized complete-App CPU. Four watched session JSONL files changed during
  the interval, so the artifact is explicitly `INVALIDATED` and `p8_eligible`
  is false; complete-App CPU is not a HUD-only measurement.
- `real-codex-app-theme-narrow-20260803.json` records a scoped light-theme
  payload apply/restore and a `760x520` live-renderer viewport geometry check;
  native appearance settings and a real window resize gesture remain unrun.
- `real-codex-app-cdp-update-failed-20260803.json` records a real-App injected
  `cdp.update_failed` row and matching structured log record, followed by a
  successful restore and resolved diagnostic.
- `real-codex-app-active-session-role-list-fix-20260803.json` records the
  confirmed sidebar-container root cause and the post-fix real-App results:
  the active row is found inside a 7-thread `[role='list']`, raw/canonical
  selection converges in `138.9-167.7ms`, and authoritative `appliedSeq`
  confirmation takes `996.5-1263.2ms`. The existing threads were restored;
  no Codex request, storage write, or product binding write was made.

## Task Plan Success Standards

| Requirement | Status | Evidence |
|---|---|---|
| active session response under 150ms; failure shows error HUD | Observed fail | The role-list fix removes the empty raw-ID/title-only observation: live existing-thread switches now find the selected row in `[role='list']` and converge raw/canonical state in `138.9-167.7ms`. Authoritative `appliedSeq` confirmation remains `996.5-1263.2ms`, so the visible stable target is still not met. |
| current session usage append refresh under 250ms | Partially proven | Latest `append_then_incremental_parse_and_payload` P90 is `157.377ms` in [renderer_latency_baseline.md](/E:/Project/codex-usage-hud/renderer_latency_baseline.md), which stays within the stated `<250ms` target. This is still a local pipeline measure, not full live CDP + renderer paint latency. |
| top/bottom HUD payload only updates on change; no idle snapshot rebuild | Partially proven | Tests cover no localStorage polling, unchanged runtime-signature idle path, and layout events without snapshot rebuild: `test_renderer_loop_does_not_poll_local_storage_settings_commands_when_idle`, `test_renderer_loop_skips_snapshot_when_runtime_signature_is_unchanged`, `test_renderer_loop_handles_layout_event_without_snapshot_refresh`. No live production CPU profile was taken. |
| bubble no longer polls state every 160ms; push or file-event wakeup | Proven | `test_work_overlay_helper_uses_qfilesystemwatcher_for_state_updates`, `test_work_overlay_command_pump_uses_file_watcher_event`, `test_desktop_work_overlay_skips_unchanged_state_until_keepalive`. |
| idle CPU has no recurring snapshot/scan/payload work without relevant events | Unverified locally | The daemon candidate and the newer real-App sample both have input changes during the 60-second window. The newer artifact also saw zero HUD-root mutations, but its complete-App CPU cannot isolate HUD work; treat both as observations, not a no-event idle pass. |
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
| panel appears with `DEBUG HUD active` ready row when no errors are present | Proven | Automated/smoke-host evidence plus the real-App `debugReadyState` in `real-codex-app-interaction-20260803.json` showed `debug.ready`, `DEBUG HUD active`, and zero errors. |
| renderer active-session mapping failure records `active_session.*` | Proven | Automated evidence via `test_build_snapshot_records_renderer_unmatched_runtime_error` plus browser smoke-host rendering of `active_session.unmatched_thread` with select/copy proof of context text. |
| CDP update/target disconnect failure records `cdp.update_failed` | Proven | Automated evidence plus `real-codex-app-cdp-update-failed-20260803.json`: a throwing update function in the real App produced the structured record, showed one error, then resolved after restore. |
| file watcher overflow/degraded records `file_watcher.*` | Proven | `test_renderer_file_event_source_records_degraded_polling`, `test_renderer_file_event_source_records_overflow_without_polluting_reasons`, and browser smoke-host rendering of `file_watcher.degraded`. |
| desktop overlay helper error records `work_overlay_helper.*` | Proven | `test_work_overlay_helper_runtime_error_writes_normal_mode_diagnostic` and browser smoke-host rendering of `work_overlay_helper.state_read_failed`. |
| error rows can be selected/copied | Partially proven | Browser smoke-host proved page-context `select` and `copy` extraction for both `file_watcher.degraded` and `work_overlay_helper.state_read_failed`. Native browser shortcut copy is still policy-limited in this environment. |
| panel can be dragged and position persists after HUD refresh | Proven | Browser smoke-host proved drag from roughly `(16, 526)` to `(136, 468)`, then reload preserved both position and expanded state. |

### Normal-Mode Diagnostics

| Requirement | Status | Evidence |
|---|---|---|
| structured records written to `renderer_fallback.log` without fallback path switching | Proven | JSON-record assertions now cover `active_session.unmatched_thread`, `cdp.update_failed`, `file_watcher.degraded` recorded/resolved, and `work_overlay_helper.state_read_failed`. |
| required fields `source`, `severity`, `code`, `message`, `context`, `firstSeenAt`, `lastSeenAt` are present | Proven | Field-level assertions in [tests/test_ui.py](/E:/Project/codex-usage-hud/tests/test_ui.py) parse actual JSON lines from `renderer_fallback.log`. |

### Real Windows Interaction

| Requirement | Status | Evidence |
|---|---|---|
| live Renderer startup and usable root | Proven | `real-codex-app-interaction-20260803.json` selected `app://-/index.html`; root/style were visible, ready, bound, and `rootCount=1`. |
| all current settings tabs open and close | Proven | The same artifact opened `settings`, `storage`, `backgroundUsage`, `support`, and `about`; every tab stayed inside the live modal and close restored `hidden=true`. No setting was changed. |
| root replacement preserves one functional HUD | Proven | The artifact removed the live root, called the public update with retained payload, and observed `replaced=true`, `rootCount=1`, and `ready=true`. |
| remove/reinject lifecycle | Proven | The artifact observed root/style/globals absent after remove, then a fresh `RendererHudClient.update_payload` returned `status=ok` and restored one ready root. |
| active session selection follows real sidebar | Proven | `real-codex-app-session-switch-20260803.json` selected two existing session IDs through CDP; controller results, active rows, and read-only CDP snapshots agreed. |
| current request updates visible usage under 250ms | Not run | No new request was submitted during this evidence pass. |
| Renderer theme payload applies and restores on the real App page | Partially proven | `real-codex-app-theme-narrow-20260803.json` applied a light payload in about 100ms and restored dark; native Codex appearance settings were not changed. |
| narrow Renderer viewport remains ready and non-overlapping | Partially proven | The same artifact measured a `760x520` emulated viewport with one ready root, top/request panels inside the viewport, and no overlap; native window resize was not performed. |

## Remaining Unverified Or Partially Verified Items

These are the main remaining gaps before claiming the entire objective is fully closed:

1. Real Codex App end-to-end latency state:
   - the previous split evidence narrowed Python observed-to-payload-send to
     `41-48ms` and first payload/renderer observation to `35.7-50.8ms`
   - the `[role='list']` fix is now verified in the real App and prevents the
     empty raw-ID/title-only selection variant
   - raw/canonical selection converges in `138.9-167.7ms`, but authoritative
     `appliedSeq` confirmation remains `996.5-1263.2ms`; the `<150ms` target
     remains an observed fail
   - current session usage refresh `<250ms` still lacks full live CDP + renderer paint evidence because no new request was submitted
2. Real live-runtime idle CPU proof:
   - the low-CPU candidate is invalidated by the current agent rollout JSONL changing during the window
   - no recurring background snapshot/payload work under an attached Codex App session remains unproven at the required no-event boundary
3. Real live-runtime proof inside a real Codex App window rather than the localhost smoke host for:
   - remaining file-watcher and overlay failure injection (CDP failure is now
     directly captured in `real-codex-app-cdp-update-failed-20260803.json`)
4. Native Windows interaction scope still missing for:
   - a user-driven theme change
   - a real drag/resize gesture and persistence check
5. Separation of evidence classes in final close-out:
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

Update on 2026-08-03:

- Real Windows startup, DEBUG readiness, settings-tab open/close, thread ID
  selection, root replacement, and remove/reinject are now directly captured.
- The active-session timing target remains an observed fail: visible stable HUD
  transitions took `545-1481ms`.
- The new active-session split capture shows Python follow timing from
  `observedAt` to `payloadSendStartedAt` of `41-48ms`, and page-side first
  payload/renderer target observation at `35.7-50.8ms`; sidebar rows appeared at
  `242.7-295.9ms`, while the complete stable criteria took `1129.8-2391.1ms`.
  This is evidence refinement, not a latency fix, and the temporary page probe
  was removed after the capture.
- A targeted follow-up confirmed the production root cause: real sidebar rows
  live under `[role='list']`, while the old container fallback observed only a
  row-local parent. `activeSessionContainer()` now prefers that list in both
  the row-ancestor and document-fallback paths. The real App recovered the
  canonical raw ID and title, but `appliedSeq` still lagged by roughly
  `996.5-1263.2ms`; this is a correctness fix and root-cause isolation, not a
  completed latency fix.
- A follow-up code fix now keeps canonical thread UUID as the Renderer
  selection identity. Same-thread raw-ID prefix/title rebuilds still send the
  updated payload without allocating a new `selectionSeq`; distinct canonical
  IDs and provisional/title-only/new-session boundaries remain distinct. The
  current source, fresh wheel, onefile `--help`, focused Node regression, and
  full automated suite pass. The latest current-process normal existing-thread
  follow is recorded in
  `real-codex-app-active-session-follow-timing-20260804.json`; its dedicated
  header-guard and acknowledgement failure/retry scenarios remain untested.
- A settled low-CPU candidate (`0.189%` normalized) is retained for context but
  is not accepted because the current agent rollout JSONL changed.

What still keeps completion from being fully proven is now primarily proof
quality at the live Codex App and platform boundary:

1. real active-session visible latency remains far above the `<150ms` target;
   the new split evidence does not yet identify a production fix point
2. real Codex App current-session live latency is still not directly measured
3. real Codex App idle CPU / no-background-work evidence is still missing
4. macOS package/startup/Renderer interaction is unavailable on this host
5. a small set of diagnostics remain proven through smoke-host pages rather than
   a live Codex App failure injection

There are no longer any open regression-budget failures in the latest local
baseline. The remaining work is now split between:

1. documenting live-runtime failures and gaps accurately
2. adding better live-observation tooling
3. investigating the visible active-session latency and related renderer/CDP
   boundary behavior

Use [HUD_RUNTIME_LIVE_VERIFICATION.md](/E:/Project/codex-usage-hud/docs/HUD_RUNTIME_LIVE_VERIFICATION.md)
as the concrete runbook for those remaining live-runtime checks.

## Current Conclusion

P0-P11 structural ownership, P7.2-P7.7 owner cutovers, compatibility migration,
automated tests, current source/wheel resource loading, PyInstaller packaging,
and the captured Windows interaction subset are proven on the current worktree.
The overall task remains open for the active-session latency target,
current-request latency, a valid no-event idle sample, native Windows gesture
coverage, and available macOS package/startup/Renderer evidence.
The Chromium lifecycle probe remains valid smoke-host evidence and must not be
reported as a real Codex App capture.
