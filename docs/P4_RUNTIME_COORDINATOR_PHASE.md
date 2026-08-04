# P4 Runtime Coordinator Phase

Date: 2026-07-31

## Ownership

- `renderer_file_events.py` owns watcher specs, typed change batches, debounce,
  overflow reconciliation, and degraded diagnostics.
- `renderer_bridge.py` owns callback normalization and fixed binding installation
  order.
- `renderer_connection.py` owns connection health, bounded probes, follow healing,
  and lightweight diagnostic pushes.
- `renderer_event_loop.py` owns typed event reduction, tick sampling, refresh/domain
  execution, and scheduled waits.
- `renderer_runtime.py` owns startup feedback, pre-refresh application, session loop
  controls, and strict session resource shutdown.
- `active_work.py` owns coalesced background active-work refreshes.

`run_renderer_hud_session` now assembles these owners. Its remaining nested
callbacks build Renderer payloads or publish overlay projections and are the input
to P5; watcher, bridge, connection, event reduction, refresh sequencing, retry,
watchdog, and resource-shutdown rules no longer live in nested handlers.

## Lifecycle Contract

`RendererSessionResources` accepts partial construction and closes each attached
resource once. Shutdown is isolated per resource and runs in reverse ownership
order:

1. active-work pump
2. file events
3. overlay command pump
4. settings bridge
5. active-session tracker callback
6. runtime-event subscription
7. Renderer client
8. update manager
9. desktop overlay
10. runtime context

The outer session `finally` owns the final close, including failures before the
inner run loop starts. The normal loop `finally` may close earlier; idempotence
makes the outer close a no-op in that case.

## Deterministic Evidence

The P4 owner and architecture suite passed with 100 tests:

```text
python -m pytest tests/test_active_work.py tests/test_renderer_bridge.py \
  tests/test_renderer_connection.py tests/test_renderer_event_loop.py \
  tests/test_renderer_runtime.py tests/test_overlay_commands.py \
  tests/test_snapshot_builder.py tests/test_architecture.py -q
100 passed
```

Additional affected boundaries passed:

```text
python -m pytest tests/test_renderer_connection.py tests/test_active_session.py -q
44 passed

python -m pytest tests/test_runtime_boundaries.py tests/test_runtime_ports.py \
  tests/test_runtime_context.py -q
26 passed
```

The owner tests cover coalescing, stale work replacement, binding order, channel
loss/restoration, bounded probes/healing, layout-only updates, settings wakes,
full/partial refresh accounting, retry deadlines, daemon deadlines, shutdown
during partial construction, close exceptions, and fake-clock idle no-work
counters.

`compileall`, scoped Ruff, and `git diff --check` passed after the final P4 edits.

## Live Windows Evidence

The current worktree HUD was restarted in DEBUG Renderer mode by:

```text
python tools/run_live_acceptance.py --prepare-mode debug \
  --skip-automated-checks --idle-cpu-sample-seconds 60
```

Artifacts are under:

```text
artifacts/live_acceptance/20260731-114424/
```

The script's immediate CPU sample was 2.110%. It began at process launch and
included cold startup, CDP attachment, watcher creation, and active CLI-session
events, so it is retained as a failed cold-start sample and is not treated as idle
evidence.

After the same HUD process stabilized, a second 60-second sample measured 0.588%,
below the acceptance script's 1% threshold. The live overlay state belonged to PID
35212 and contained one active `clientKind=cli` item for the current conversation.
Its revisions continued to follow real CLI activity without introducing recurring
full-snapshot timing entries during the settled observation.

A read-only CDP DOM probe selected the real `app://-/index.html` target and found
`#codex-usage-hud-root` visible with `display: block`, a 1273 by 760 bounding box,
eight direct children, and populated current-session/day/week text. This confirms
the restarted process injected a non-empty Renderer HUD rather than only starting
the daemon and desktop helper.

This proves the reported CLI bubble is present in the real helper state after the
fix. Frame-level active-session latency, current-session latency, DEBUG panel drag
persistence, and screenshot acceptance still require direct visual interaction in
the Codex App and are not claimed by this phase record.

## Current Guardrails

```text
runtime_orchestration.py  5962 lines
renderer_event_loop.py    1092 lines
renderer_runtime.py        439 lines
```

`renderer_event_loop.py` and `renderer_runtime.py` are within their roadmap size
guardrails. The total `runtime_orchestration.py <= 400` terminal requirement belongs
to the remaining P5-P8 ownership moves and is not complete.
