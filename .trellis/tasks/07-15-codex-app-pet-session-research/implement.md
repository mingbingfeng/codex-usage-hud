# Renderer 新会话事件与完成气泡修复实施计划

## Checklist

- [x] 在 `renderer_hud.py` 增加 composer 事件 listener 的稳定名称、安装和清理逻辑。
- [x] 将 `activeSessionComposerSubmitButton()` 接入 click/submit/Enter 路径，复用 `scheduleActiveSessionSendFollowup()`。
- [x] 为发送后的 canonical ref 提供有限递增 follow-up，并确保事件重复由现有 signature 去重。
- [x] 在 `tests/test_renderer_hud.py` 增加脚本契约测试和发送后重读行为测试。
- [x] 在现有 active-session/work-overlay 测试覆盖中验证 wakeup 到 completed payload 的链路。
- [x] 运行 focused pytest，再运行 Trellis quality check 所需的完整测试/静态检查。
- [ ] 使用本机 Codex App 做 live acceptance：新会话首条消息、回复完成、无需点击即可完成圆形气泡。当前阻塞：本机没有可用的 `9229/9444` CDP listener。

## Validation Commands

```powershell
rtk python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q
rtk python -m pytest tests/test_daemon.py tests/test_file_watcher.py -q
```

Live acceptance must record:

- renderer active-session payload reason and canonical session id;
- tracker selection source and resolved rollout path;
- completion payload status and overlay transition audit;
- whether any extra session click was required.

## Risky Files And Rollback Points

- `src/codex_usage_hud/ui/renderer_hud.py`: injected JS lifecycle and CDP binding surface.
- `src/codex_usage_hud/cli.py`: only inspect/adjust if tests prove refresh scheduling still loses the completion update; do not broaden into a polling workaround.
- `tests/test_renderer_hud.py`, `tests/test_ui.py`: regression coverage.

Rollback point: revert only the new composer event constants/listeners and tests; preserve existing exact mapping and pending-state logic.

## Pre-Start Review

Implementation is intentionally blocked until the user reviews this PRD/design/implementation plan or explicitly asks to proceed. After approval, run `trellis-before-dev` before editing runtime code, then execute this checklist in order.
