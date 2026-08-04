# P3 Snapshot And Settings Phase Evidence

Completed: 2026-07-31 (Asia/Shanghai)

## Ownership

- `runtime_context.py` owns the resources and immutable configuration handles of
  one renderer runtime invocation. Its construction starts no worker or cache;
  composition explicitly attaches optional resources and `close()` releases each
  owned resource once in reverse dependency order.
- `runtime_config.py` owns data-path discovery, CLI override persistence,
  provider refresh, and reload application through `ConfigApplyPorts`.
- `session_snapshots.py` owns selected-session cold preview, canonical hydration,
  cache cloning, waiting/missing state, and stale selection rejection.
- `snapshot_builder.py` owns snapshot enrichment, visible-error holding, budget
  reuse, current-session activity, and text projection.
- `runtime_commands.py` owns injected background-usage, cleanup, insights, and
  general settings command dispatch. Every response receives additive top-level
  `requestId` and `action` correlation fields.
- `runtime_settings.py` remains the pure owner of settings merge, changed-key
  classification, partial payload domains, and correlated response envelopes.

`runtime_orchestration.py` now composes the above owners and retains only small
compatibility forwarding functions. It no longer defines `RuntimeContext`.

## Preserved Contracts

- The selected renderer row remains keyed only by its canonical UUID. Snapshot
  hydration preserves `selectionSeq` capture before parsing and rejects stale
  completions.
- Cold preview, canonical upgrade while queued, pending local mapping, and clone
  isolation retain their existing response shapes.
- CLI override values remain effective after settings reload. Provider registry,
  application provider, parser pricing, usage cache, background usage, and rest
  reminder refresh through explicit ports.
- Settings, background usage, cleanup, and insights acknowledgements/errors carry
  their command request ID and action without changing existing payload fields.
- Session cleanup keeps the data-safe terminal order: prepare usage snapshot,
  delete and verify, commit the verified ledger, refresh the session inventory and
  usage projection, then publish the matching terminal payload. The renderer
  loading layer closes only on that matching terminal `requestId`; an older
  completion cannot close a newer dialog.

## Validation

All commands ran from `E:\Project\codex-usage-hud`.

```powershell
python -m pytest tests/test_snapshot_builder.py tests/test_session_snapshots.py tests/test_runtime_commands.py tests/test_runtime_context.py tests/test_runtime_settings.py tests/test_runtime_boundaries.py tests/test_session_cleanup.py tests/test_session_cleanup_runtime.py -q
# 49 passed

python -m pytest tests/test_ui.py -q -k "renderer_settings_command or renderer_rest_reminder or renderer_apply_display_mode or renderer_install_desktop_overlay or renderer_enable_desktop_overlay or renderer_exit_command or renderer_dismiss_warnings or renderer_check_update or renderer_install_update or renderer_background_usage or renderer_session_cleanup_command or renderer_usage_insights"
# 16 passed

python -m pytest tests/test_renderer_hud.py -q -k "session_cleanup"
# 3 passed

python -m pytest -m "not ui and not qt_ui" -q
# passed; only explicit skips

python -m pytest -m qt_ui -q
# 3 passed, 38 explicit removed-legacy skips

python -m compileall -q src tests tools
ruff check src/codex_usage_hud/runtime_context.py src/codex_usage_hud/runtime_orchestration.py src/codex_usage_hud/runtime_config.py src/codex_usage_hud/runtime_commands.py src/codex_usage_hud/runtime_settings.py src/codex_usage_hud/session_snapshots.py src/codex_usage_hud/snapshot_builder.py tests/test_runtime_context.py tests/test_runtime_commands.py tests/test_session_cleanup_runtime.py
git diff --check
```

## Phase Boundary

P3 does not change the renderer script bundle, renderer payload-domain order,
settings UI copy/layout, storage or binding keys, the `selectionSeq/appliedSeq`
contract, event-driven scheduling, desktop-overlay behavior, or platform support.
P4 owns the next extraction: watcher reconciliation, renderer bridge callbacks,
connection health, event reduction, and renderer runtime lifecycle.
