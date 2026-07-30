# Renderer Mode Architecture Constraints

This document is normative. Treat it as the renderer-mode architecture contract,
not as a product vision or optional strategy.

## Product Surface

- Renderer mode is the only product HUD surface.
- The HUD must stay inside the Codex App renderer/CDP surface.
- Windows and macOS are first-class compatibility targets.
- Qt/Tk standalone HUD implementations have been removed.
- Do not restore Qt/Tk as a product surface or fallback for renderer
  availability, correctness, or performance problems.
- PySide6 remains an optional desktop-helper runtime for work bubbles and other
  detached helpers; it is not a standalone main HUD.

## Active Session Authority

- In renderer mode, renderer-observed session state is the default authority.
- Native title polling, UIA/MSAA, and macOS native title routes must not silently
  replace renderer active-session failures.
- If renderer session id/title cannot be mapped to a local session, record a
  runtime error and keep the failure visible in DEBUG diagnostics.
- app-server may become a future explicit authority only after a POC proves it
  exposes current-window active-thread semantics.

### Strict renderer session contract

- The selected renderer row's canonical thread UUID is the only mapping key.
  Do not title-match, recursively scan session files, or select a newest-file
  substitute when that UUID is not yet in the local state database.
- `client-new-thread:*` is a provisional new-session alias, not an unmatched
  conversation. Render an explicit pending state until Codex publishes the
  canonical UUID and exact rollout mapping.
- A state/session-map file event must re-resolve the already selected UUID
  immediately. It must not wait for another click, title event, or poll.
- Renderer bootstrap starts with the configured or last successful CDP port.
  When that port cannot expose a renderer target, one bounded recovery may
  allocate a fresh local port and restart only the verified Codex desktop
  process family with CDP enabled. CLI `codex.exe` processes are never part of
  this restart set.

## Event-Driven Runtime

The target invariant is:

```text
no relevant event -> no snapshot rebuild, no all-session scan, no CDP payload push
```

Required event sources:

- renderer active-session bridge
- current session file watcher
- settings file/command bridge
- budget window boundary events
- renderer layout binding
- desktop overlay command/state events
- runtime error registry

Polling is allowed only as a feature-detected fallback with conservative backoff
and a degraded diagnostic. It must not become the primary architecture.

## Renderer Script

- Use targeted observers on known Codex anchors. Avoid broad whole-document
  `MutationObserver` scans during normal operation.
- Cache DOM anchors and invalidate them only when a targeted observer proves the
  anchor changed or disappeared.
- Batch DOM reads and writes through one animation-frame scheduler per concern.
- Prefer `ResizeObserver`, targeted `MutationObserver`, and explicit bindings
  over fixed intervals.
- Keep stale-state UI as a one-shot timeout reset by real updates.
- Stop timers, observers, and bindings when the HUD is removed or target page
  changes.

## Python Runtime

- Snapshot builds must be requested by event handlers or explicit internal state
  events, not by an unconditional fixed loop.
- Current-session JSONL updates should use the incremental tail parser.
- Budget aggregation should use per-file contribution replacement when possible.
- CDP target discovery should keep subscribed target state and fail explicitly
  after disconnect instead of silently rescanning.
- File watcher overflow and degraded states must be recorded as runtime errors
  or diagnostics.
- Desktop overlay state and commands should be event/watcher awakened; unchanged
  overlay payloads must not rewrite state.

## Legacy Boundary

- CLI/config legacy display-mode aliases normalize to `renderer`.
- Public legacy session functions remain only as compatibility stubs that
  return renderer-unavailable without starting Qt/Tk runtimes.
- Hidden legacy diagnostic flags must not enable renderer-mode active-session
  fallback paths.
- Release and troubleshooting docs should recommend renderer fixes, not Qt/Tk
  fallback.

## Required Validation

Before declaring renderer runtime work complete, run:

```powershell
python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q
python -m compileall -q src tests tools
git diff --check
```

For performance-sensitive changes, also run:

```powershell
python tools/measure_renderer_latency.py --markdown-output renderer_latency_baseline.md
```

Use `docs/HUD_RUNTIME_ACCEPTANCE_CHECKLIST.md` for manual performance and DEBUG
error HUD acceptance.
