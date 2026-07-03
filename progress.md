# 进度日志：Renderer HUD 运行时重构与性能调优

## 会话：2026-07-03

### 本次目标
响应用户要求，把后续 HUD 工作从补丁式修复切换为长期重构：提升响应速度、降低维护复杂度、删除默认 fallback 链路，并在 DEBUG 模式下通过错误 HUD 暴露失败。

### 已完成
- 使用 `guihua` 规划技能恢复并切换规划上下文。
- 确认 codebase-memory-mcp canonical 项目：
  - `E-Project-codex-usage-hud`
  - root path `E:/Project/codex-usage-hud`
- 清理项目根目录无关临时截图文件：
  - `.clip_composer.png`
  - `.clip_now.png`
  - `.clipboard_shot.png`
- 将 `task_plan.md` 切换为本次长期重构计划。
- 将 `findings.md` 切换为本次 renderer/session/usage/overlay 链路发现。
- 新增正式项目文档：
  - `docs/HUD_RUNTIME_REFACTOR_PLAN.md`
- 记录长期方向：
  - renderer 权威
  - 事件驱动
  - 失败显式
  - DEBUG 错误 HUD
  - JSONL 增量解析
  - overlay IPC push 化
- 新增阶段 0 性能基线工具：
  - `tools/measure_renderer_latency.py`
  - `tests/test_measure_renderer_latency.py`
- 运行本机低迭代 smoke，并输出：
  - `renderer_latency_baseline.json`
  - `renderer_latency_baseline.md`
- 新增 fallback 清单：
  - `docs/FALLBACK_INVENTORY.md`
- 新增 runtime error 诊断模型：
  - `src/codex_usage_hud/core/runtime_errors.py`
  - `RuntimeErrorEvent`
  - `RuntimeErrorRegistry`
- 新增 runtime event bus：
  - `src/codex_usage_hud/core/runtime_events.py`
  - `RuntimeEvent`
  - `RuntimeEventBus`
- `RuntimeContext` 现在持有共享 `runtime_events`，并把 `runtime_errors` 绑定到同一个 bus。
- `RuntimeErrorRegistry.record/resolve` 会发布 `runtime_error` event；renderer loop 订阅该事件并唤醒刷新。
- Renderer payload 新增 DEBUG 诊断字段：
  - `debug`
  - `runtimeErrors`
- Renderer 注入脚本新增 DEBUG runtime error panel：
  - `codex-usage-hud-runtime-errors`
  - `renderRuntimeErrors`
- 接入首批 runtime error 来源：
  - `active_session.unmatched_thread`
  - `file_watcher.degraded`
  - `file_watcher.overflow`
  - `cdp.update_failed`
- Windows file watcher 现在处理 `ReadDirectoryChangesW` 的 `bytes_returned == 0` 溢出信号：
  - 立即枚举当前 worker specs 的匹配路径作为补偿。
  - 回调业务 reasons 和补偿 paths。
  - 通过 `_RendererFileEventSource` 记录 `file_watcher.overflow` runtime error。
  - overflow 哨兵不会进入普通 `file_change_reasons`，避免污染刷新分支判断。
- Active session resolver 现在不再让 `renderer-unmatched` 落到 latest JSONL activity fallback：
  - 不调用 `platform.detect_active_session()`。
  - 清空 `auto_session_file`。
  - 返回 `(None, "renderer-unmatched")`。
  - `build_snapshot()` 继续记录 `active_session.unmatched_thread` runtime error。
- Renderer-authoritative tracker 现在不再让等待态落到 CDP/native/latest fallback：
  - `start_background_watcher=False` 且无 renderer selection 时返回 `renderer-waiting`。
  - 不调用 `platform.get_active_conversation_ref()`。
  - resolver 看到 `renderer-waiting` 时不调用 `platform.detect_active_session()`。
- 新增/扩展测试：
  - `tests/test_runtime_errors.py`
  - `tests/test_renderer_hud.py`
  - `tests/test_ui.py`
  - `tests/test_file_watcher.py`

### 当前结论
- 当前实现已有事件驱动雏形，但仍保留周期性刷新和多层 fallback。
- 用户明确希望失败直接暴露，所以后续应先建设可观察性和错误 HUD，再逐步删除 fallback。
- 这次重构的第一刀不应直接删兜底，而应先定义 runtime error model、latency markers 和 fallback inventory，确保删除时问题能被看见。
- 阶段 0 已完成：已有可重复运行的本地基线 harness 和 fallback inventory。
- 低迭代本机基线显示：当前 session full parse 约 7.1ms，payload build 约 1.1ms，usage full scan 约 69.8ms，polling fallback scan 约 55.7ms，append 后 full parse+payload 约 20.2ms。
- DEBUG 错误 HUD 的基础链路已存在：runtime registry -> payload -> renderer panel。
- 当前 `runtime_error` 已接入 event bus 并可唤醒 renderer loop。
- `active_session_changed` 已接入 event bus，tracker callback 和 renderer bridge changed 都会发布事件并唤醒 renderer loop。
- `_RendererFileEventSource` 已接入 event bus，coalesced file watcher wake 会发布 `session_file_changed` / `settings_changed`，renderer loop 会订阅并唤醒。
- settings bridge command 已接入 event bus，会发布 `settings_command_received`。
- work overlay command pump 已接入 event bus，会发布 `overlay_command_received`。
- settings command 的 localStorage polling fallback 和 overlay command pump 的 60ms polling 仍未删除。
- renderer mode 下的 CDP/native active ref 和 latest JSONL fallback 已隔离；后续应继续拆 polling fallback，并调研 app-server active thread 能力。

### 未执行
- 未将 renderer 主循环改成事件总线 dispatcher。
- 未做真实 CDP/DOM paint 端到端延迟测量；当前 harness 只覆盖本地 parser/payload/cache/fallback scan。
- 已删除 `renderer-unmatched+activity` fallback；renderer-unmatched 现在直接进入显式错误路径。

### 下一步
1. 继续拆 settings command polling 和 overlay command polling。
2. 调研 Codex app-server active thread 能力，决定是否作为显式权威源候选。

### 本轮验证
- `python -m pytest tests/test_measure_renderer_latency.py -q` 通过。
- `python tools/measure_renderer_latency.py --iterations 1 --warmups 0 --json-output renderer_latency_baseline.json --markdown-output renderer_latency_baseline.md` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_measure_renderer_latency.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_file_watcher.py::FileChangeWatcherTests::test_windows_overflow_reconciles_matching_specs -q` 先失败（缺少 `_emit_overflow_reconciliation`），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_records_overflow_without_polluting_reasons -q` 先失败（overflow 哨兵进入普通 reasons；随后暴露 `_path_key` 作用域问题），实现后通过。
- `python -m pytest tests/test_file_watcher.py -q` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_records_degraded_polling tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_records_overflow_without_polluting_reasons tests/test_runtime_errors.py -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_publishes_runtime_events -q` 先失败（file watcher 未发布 runtime events），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_settings_runtime_event -q` 先失败（renderer loop 未订阅 `settings_changed`），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_publishes_runtime_events tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_settings_runtime_event -q` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_debounces_native_events tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_publishes_runtime_events tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_records_degraded_polling tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_records_overflow_without_polluting_reasons tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_settings_runtime_event tests/test_file_watcher.py tests/test_runtime_errors.py -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_bridge_settings_command_without_cdp_poll -q` 先失败（settings bridge command 未发布 `settings_command_received`），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_bridge_settings_command_without_cdp_poll tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_publishes_runtime_events tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_settings_runtime_event tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_keeps_wakeup_for_active_session_event_during_wait tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event tests/test_runtime_errors.py -q` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_run_renderer_hud_session_drains_work_overlay_commands_with_window_prep -q` 先失败（work overlay command pump 未发布 `overlay_command_received`），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_bridge_settings_command_without_cdp_poll tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_publishes_runtime_events tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_settings_runtime_event tests/test_ui.py::DaemonLifecycleTests::test_run_renderer_hud_session_drains_work_overlay_commands_with_window_prep tests/test_ui.py::BudgetHelperTests::test_tk_work_overlay_command_pump_prepares_window_before_switching_session tests/test_ui.py::BudgetHelperTests::test_current_session_overlay_command_refocuses_codex_after_already_active_result tests/test_runtime_errors.py -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_tracker_active_session_callback -q` 先失败（缺少 `active_session_changed` 事件发布），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_keeps_wakeup_for_active_session_event_during_wait tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_tracker_active_session_callback -q` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_active_session_bridge_event_without_cdp_poll tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_forwards_renderer_new_session_marker tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_keeps_wakeup_for_active_session_event_during_wait tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_tracker_active_session_callback tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event -q` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_active_session_bridge_event_without_cdp_poll tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_forwards_renderer_new_session_marker tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_keeps_wakeup_for_active_session_event_during_wait tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_tracker_active_session_callback tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event tests/test_runtime_errors.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_active_session.py::SessionPathResolverTests::test_resolver_does_not_fallback_to_activity_for_unresolved_renderer_switch -q` 先失败（旧逻辑选中 latest JSONL），实现后通过。
- `python -m pytest tests/test_active_session.py::SessionPathResolverTests -q` 通过。
- `python -m pytest tests/test_active_session.py::SessionPathResolverTests tests/test_ui.py::BudgetHelperTests::test_build_snapshot_records_renderer_unmatched_runtime_error -q` 通过。
- `python -m pytest tests/test_active_session.py -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_active_session.py::ActiveSessionTrackerTests::test_renderer_authoritative_tracker_skips_cdp_ref_without_renderer_selection -q` 先失败（旧逻辑调用 CDP ref 并选中 session），实现后通过。
- `python -m pytest tests/test_active_session.py::SessionPathResolverTests::test_resolver_does_not_fallback_to_activity_while_renderer_tracker_waits -q` 先失败（旧逻辑选中 latest JSONL），实现后通过。
- `python -m pytest tests/test_active_session.py -q` 通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_build_runtime_context_uses_renderer_bridge_instead_of_native_title_watcher -q` 通过。
- `python -m pytest tests/test_runtime_errors.py -q` 先失败（缺少 `codex_usage_hud.core.runtime_events`），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_build_runtime_context_uses_renderer_bridge_instead_of_native_title_watcher -q` 先失败（`RuntimeContext` 缺少 `runtime_events`），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event -q` 先失败（runtime error event 不唤醒 renderer loop），实现后通过。
- `python -m pytest tests/test_runtime_errors.py tests/test_renderer_hud.py tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_tracker_active_session_callback tests/test_ui.py::DaemonLifecycleTests::test_build_runtime_context_uses_renderer_bridge_instead_of_native_title_watcher -q` 通过。
- `python -m pytest tests/test_file_watcher.py -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。

## 错误日志
| 时间戳 | 错误 | 尝试次数 | 解决方案 |
|--------|------|---------|---------|
| 2026-07-03 | pytest node id 类名写错：`WorkOverlayTests` 不存在 | 1 | 用 `rg` 查到测试属于 `BudgetHelperTests`，改用正确 node id 后通过 |

## 五问重启检查
| 问题 | 答案 |
|------|------|
| 我在哪里？ | 阶段 0 完成；runtime error model / DEBUG 错误 HUD 已落地，事件总线仍在阶段 1 |
| 我要去哪里？ | 把普通 active/session/settings/file events 迁移到 event bus，并继续拆 settings/overlay polling |
| 目标是什么？ | renderer 权威、事件驱动、失败显式、响应速度优先 |
| 我学到了什么？ | DEBUG 错误 HUD 可以先挂在现有 snapshot/payload 流上，后续再被事件总线驱动 |
| 我做了什么？ | 新增 runtime error 模型、DEBUG 错误面板、三类错误来源接入和测试 |

---
*每个阶段完成后或遇到错误时更新此文件。*
