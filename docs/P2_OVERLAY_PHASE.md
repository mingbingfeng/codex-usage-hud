# P2 Overlay Phase Evidence

Date: 2026-07-31 (Asia/Shanghai)

## Result

P2 exited with both P0 manifests empty. The detached work-overlay behavior and
visible bubble UI remain unchanged; ownership moved out of the runtime coordinator.

## Ownership

- `overlay_ipc.py` owns additive v1 state, command, acknowledgement, and transition
  contracts plus all sidecar paths. Missing `schemaVersion` remains accepted as
  legacy v0 during migration.
- `overlay_projection.py` owns work-item/background payload projection, payload
  ordering and limiting, visible/dismissed cache behavior, runtime visible selection,
  and published-item stabilization.
- `desktop_overlay.py` owns PySide probing, helper supervision, atomic state
  publication, heartbeat/restart policy, command reading, transition audit, and
  sidecar cleanup.
- `overlay_command_pump.py` owns the event-driven command-file watcher and delegates
  to an injected handler.
- `overlay_commands.py` owns session/background/rest/runtime-error routing through
  explicit window-action, session-controller, event, and clock ports.
- `loading_feedback.py` owns the detached loading/restart feedback helper lifecycle.
- `runtime_orchestration.py` retains composition, context input extraction, and
  compatibility forwarding. It no longer defines `DesktopWorkOverlay`, the command
  pump, loading feedback implementation, or command-routing rules.

No overlay owner imports `cli`, `runtime_orchestration`, or the Renderer facade.

## IPC Compatibility

- State remains flat and retains all existing item/UI fields; v1 adds
  `schemaVersion`, `messageType`, `messageId`, `createdAt`, `revision`,
  `producerInstanceId`, and `ackPath`.
- Commands retain existing action-specific fields; the real helper now adds a unique
  `requestId` and v1 envelope.
- The command reader does not advance past an unterminated JSONL tail and deduplicates
  v1 commands by `requestId`.
- Correlated v1 acknowledgement rows use a dedicated `*-acks.jsonl` sidecar.
- Transition rows retain legacy flat audit fields and add v1 event, producer, and
  state-revision correlation.
- Unknown v1 major versions and malformed required fields are rejected; v0 readers
  and writers remain compatible for the migration window.

## Behavioral Fixes Preserved

- An empty initial snapshot records an empty baseline without writing state or
  launching a helper.
- Background-usage notifications may still publish on the first snapshot.
- Unchanged state does not rewrite until the explicit keepalive boundary.
- Switch-completed hold remains visible across the following update.
- Current App, background usage, and other active sessions (including CLI) precede
  completed history, so completed badges cannot evict an active CLI bubble at the
  six-item screen limit.

## Validation

All commands ran from `E:\Project\codex-usage-hud`.

```powershell
python -m pytest tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_holds_switch_completed_for_next_update tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_skips_unchanged_state_until_keepalive tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_starts_when_pyside6_available tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_suppresses_first_snapshot_items -q
# 4 passed

python -m pytest tests/test_architecture.py tests/test_runtime_boundaries.py tests/test_overlay_commands.py tests/test_desktop_overlay.py tests/test_p0_baseline_gate.py -q
# 53 passed

python -m pytest tests/test_runtime_boundaries.py tests/test_desktop_overlay.py tests/test_overlay_commands.py tests/test_ui.py -q -k "overlay or background_usage_to_work_item or active_cli_task_survives_live_like_six_item_limit"
# 134 passed, 1 skipped

python -m pytest -m "not ui and not qt_ui" -q
# passed; only explicit skips

python -m pytest -m qt_ui -q
# 3 passed, 38 explicit removed-legacy skips

python -m compileall -q src tests tools
ruff check src/codex_usage_hud/overlay_ipc.py src/codex_usage_hud/overlay_projection.py src/codex_usage_hud/desktop_overlay.py src/codex_usage_hud/overlay_command_pump.py src/codex_usage_hud/overlay_commands.py src/codex_usage_hud/loading_feedback.py src/codex_usage_hud/ui/work_overlay_qt.py tests/test_architecture.py tests/test_runtime_boundaries.py tests/test_overlay_commands.py tests/test_desktop_overlay.py
git diff --check
python tools/check_facade_patch_inventory.py
# facade inventory monotonic: 73 paths / 410 references
```

The former Qt timing candidate also passed in ten fresh pytest processes before its
manifest entry was removed. Both `p0_confirmed_failures.json` and
`p0_flaky_candidates.json` now have empty `entries` arrays.

## Real Helper Evidence

`tests/test_desktop_overlay.py::test_real_helper_round_trip_artifacts` launches the
real PySide helper subprocess with the offscreen platform and verifies:

- v1 state JSON and monotonic revision;
- command JSONL and `requestId` round trip;
- correlated acknowledgement JSONL;
- card-to-completed transition JSONL;
- switch-completed hold;
- clean helper shutdown and sidecar cleanup.

The existing isolated Qt interaction tests additionally verify restart, background
open/dismiss, and completed-check hotspot routing. No standalone Qt/Tk HUD product
surface was added or restored.

## Phase Boundary

P2 did not change Renderer injection, CDP transport, visible overlay copy/layout,
active-session sequence semantics, polling policy, or app-server authority. macOS
package/live smoke remains a later matrix requirement and was not claimed by this
Windows P2 phase.
