# Renderer 会话激活快路径与结构化状态语义实施计划

## Ordered checklist

- [ ] Read current renderer runtime contracts and inspect all activation/event consumers.
- [ ] Add a small result/context normalizer for activation audit fields; reuse existing `SessionSwitchResult` instead of duplicating payload parsing.
- [ ] Change `_handle_work_overlay_command()` to attempt CDP activation first and defer window preparation to one recoverable failure retry.
- [ ] Publish normalized activation context through `overlay_command_received`; publish one `active_session_changed` on successful activation.
- [ ] Preserve post-success refocus and structured `WorkStatusItem` overlay payload behavior.
- [ ] Add focused tests for fast path, fallback retry, event context, success wakeup, failure non-wakeup, and structured payload fields.
- [ ] Run focused pytest, then the full Trellis quality checks required for the touched backend/tests.
- [ ] Verify live renderer activation latency through the recorded CDP port and restore the original selected Codex session.
- [ ] Update backend renderer contract spec if the event/context contract is durable, then prepare commit review.

## Validation commands

```powershell
rtk python -m pytest tests/test_cdp_probe.py tests/test_active_session.py tests/test_renderer_hud.py tests/test_ui.py -q
rtk python -m pytest tests/test_daemon.py tests/test_file_watcher.py -q
git diff --check
```

Live evidence must record:

- requested and active canonical session IDs;
- activation `status`, `backend`, `matchedBy`, and whether window preparation ran;
- `requestedAt` to processed latency when available;
- structured overlay fields and the `active_session_changed` wakeup;
- final restored Codex session.

## Risky files and rollback points

- `src/codex_usage_hud/cli.py`: command ordering, activation event projection, runtime wakeup.
- `tests/test_ui.py`: command pump and overlay event contracts.
- `tests/test_cdp_probe.py`: existing exact-ID activation behavior.
- `.trellis/spec/backend/renderer-runtime-contracts.md`: durable semantic contract update.

Rollback point: revert only the CDP-first command ordering and new activation event/context assertions; keep the existing exact session ID and structured overlay payload contracts.

## Review gate

Do not copy private Codex App IPC, compressed module names, or `actionPath`. Do not add idle polling, title-based identity substitution, or Qt/Tk product behavior.
