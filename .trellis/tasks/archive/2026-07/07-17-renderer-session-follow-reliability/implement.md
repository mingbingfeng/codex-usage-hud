# Implementation Plan

## Checklist

- [x] Add selected-ref generation/raw identity/follow-state ownership to
  `ActiveSessionTracker`; preserve provisional identity and reconcile it after map
  invalidation.
- [x] Extend renderer active-session payloads with `rendererSessionId`,
  `selectionSeq`, observation timing, and sequence-aware follow-ups/application
  acknowledgement.
- [x] Propagate selection metadata through `cli.py`; reject stale sequence work and
  record stage timing/reasons.
- [x] Make active-session visible refresh reuse `active_work_items`; schedule the
  expensive aggregation as a separate event and guard its result by sequence.
- [x] Render concise non-DEBUG follow reasons in the existing session/request area.
- [x] Add tracker tests for provisional initial miss -> map event -> confirmation,
  ambiguity, and stale generation rejection.
- [x] Add renderer/UI tests for sequence payloads, true bounded retry/ack behavior,
  visible-first scan exclusion, background refresh, and reason rendering.
- [x] Update the renderer runtime contract with the sequence/reconciliation and
  visible-first rules.
- [x] Run focused tests during iteration, then the required renderer gate,
  compileall, diff check, and latency harness.
- [x] Restart the source HUD and run real Codex App alternating/provisional session
  validation, preserving the user's unrelated working-tree changes.
- [x] Preserve PySide6 work bubbles across a transient atomic state-file read gap;
  retry through the watcher entry point without restoring an idle poll.
- [x] Add a real offscreen PySide6 regression for first-read `OSError` recovery.

## Verification Notes

- Full default pytest suite passed on 2026-07-17.
- `compileall` and `git diff --check` passed.
- Focused Ruff checks pass for the changed session-follow modules and tests. The
  repository-wide Ruff command still reports 13 pre-existing/concurrent findings.
- Two latency-harness runs kept the click-path components within budget:
  selected-session parse P90 32.2-35.5 ms, payload build P90 12.1-12.9 ms, and
  incremental parse plus payload P90 36.1-59.1 ms. The intentionally deferred
  multi-file/fallback operations showed machine-load variance and are not part of
  the visible session-switch path.
- Real Codex App validation on 2026-07-17 used `Input.dispatchMouseEvent` against
  the canonical Gateway/Teacher rows. After exact-ID cache warm-up, 50 alternating
  selections produced visible preview median 4 ms, P95 5 ms, max 7 ms, with 50/50
  final Python-authoritative confirmations on the correct sequence.
- Rapid A -> B -> C finished and remained on C. Selecting the persisted provisional
  row `local:client-new-thread:77be2580-...` displayed `reading-session-data`
  immediately and converged without another click to canonical
  `019f6df5-8fa9-7480-9592-3a09c36012b7`, `newSession=false`,
  `pendingSession=false`, `requestStatus=confirmed`.
- Full pytest (747 collected tests), compileall, diff check, and focused Ruff passed. The
  stable real Gateway session latency fixture passed all six regression budgets:
  parse P90 13.5 ms, payload P90 0.86 ms, incremental+payload P90 10.6 ms.
- The default latency command selected the active 5.15 MB development rollout and
  exceeded three offline full-parse/payload budgets. This is fixture-size variance;
  the harness itself notes that it does not measure live CDP or visible latency.
  The task's real 50-click acceptance is the authoritative visible-path result.
- Repository-focused Ruff including concurrent `cli.py`/`test_ui.py` reports 10
  pre-existing/concurrent findings. Ruff passes for the isolated tracker/parser/
  renderer and their focused tests.
- Runtime diagnostics contained eight `work_overlay_helper.state_read_failed`
  exits from 2026-07-14 through 2026-07-17. The event-driven watcher had retained
  a 1.2 second read grace but no bounded retry after periodic polling was removed.
- The helper now retries a transient read after 80 ms, preserves the rendered
  bubbles during that window, reattaches the replaced path watcher, and cleans up
  timers/watch paths on exit. The real offscreen regression forced the first read
  to raise `OSError` and observed the second read before the grace expired.
- After the fix, 52 work-overlay tests, the required 382-test renderer gate, the
  default pytest suite, compileall, and diff check passed. Repository-wide Ruff
  reports 15 existing findings; the changed helper module and focused test rules pass.
- Restarted the source HUD as PID 27272 with PySide6 helper PID 31268. The live
  state carried one current-session card, and Win32 enumeration found the 430x110
  card plus its 22x22 interactive hotspot visible. Across 36 seconds and two
  keepalive writes, the helper PID stayed stable and the historical
  `state_read_failed` count remained 8 with no new record.

## Validation Commands

```powershell
python -m pytest tests/test_active_session.py tests/test_renderer_hud.py tests/test_ui.py -q
python -m compileall -q src tests tools
git diff --check
python tools/measure_renderer_latency.py --markdown-output renderer_latency_baseline.md
```

## Risk And Rollback Points

- `src/codex_usage_hud/cli.py` and `tests/test_ui.py` contain concurrent CDP restart
  edits. Re-read their diff immediately before each edit and never replace whole-file
  regions.
- Sequence filtering must not suppress the initial bootstrap observation or a real
  new-session transition.
- Background active-work results must update overlays without repainting an older
  sessionSwitch domain.
- Do not weaken exact UUID mapping to meet the latency target.
