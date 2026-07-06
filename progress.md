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
- 本轮把 renderer loop 从“事件只设置 wake flag”推进到 event type -> handler 分派：
  - `RuntimeEventBus.drain()` 在 tick 开始时取出待处理事件。
  - `_RendererEventRefreshRequest` 汇总 handler 请求。
  - `runtime_error`、`active_session_changed`、`session_file_changed`、`settings_changed`、`settings_command_received`、`overlay_command_received`、`budget_window_changed` 分别显式请求 snapshot/diagnostics。
  - `renderer_layout_changed` 只唤醒 keepalive，不请求 snapshot，避免拖拽/缩放/展开触发 Python snapshot rebuild。
- 新增回归测试：
  - `test_renderer_loop_handles_layout_event_without_snapshot_refresh`
- 修复附件回调中的旧拼写问题：
  - `wake_active_session_refresh()` -> `request_active_session_refresh()`
- 本轮完成阶段 1 收尾：
  - 移除 renderer loop 中的 `_renderer_runtime_signature()` 刷新保护；signature drift 不再主动重建 snapshot。
  - 移除 legacy bridge wakeup、`update_state.phase == downloading`、`active_work_refresh_pending` 对 snapshot 的直接布尔触发。
  - 新增 `update_state_changed` 和 `active_work_refresh_requested` 内部事件，让这些状态变化进入同一个 event -> handler 分派。
  - `snapshot_requested` 现在只由初始化缺少 snapshot 或 `event_refresh_request.snapshot` 触发。
  - `RuntimeEvent.to_payload()` 显式包含 `type`、`source`、`timestamp`、`session`、`context`、`error`。
  - `RuntimeErrorRegistry` 发布 `runtime_error` 时同时填充事件 `error` 字段。
- 阶段 1 状态已在 `task_plan.md` 标记为 complete。

### 未执行
- 未做真实 CDP/DOM paint 端到端延迟测量；当前 harness 只覆盖本地 parser/payload/cache/fallback scan。
- 已删除 `renderer-unmatched+activity` fallback；renderer-unmatched 现在直接进入显式错误路径。

### 下一步
1. 进入阶段 2：正常模式结构化 diagnostic、删除失败后静默换路径逻辑，并补错误不被 fallback 掩盖的测试。
2. 继续拆 settings command polling 和 overlay command polling。
3. 调研 Codex app-server active thread 能力，决定是否作为显式权威源候选。

### 本轮验证
- `python -m pytest tests/test_measure_renderer_latency.py -q` 通过。
- `python tools/measure_renderer_latency.py --iterations 1 --warmups 0 --json-output renderer_latency_baseline.json --markdown-output renderer_latency_baseline.md` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_measure_renderer_latency.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。

## 会话：2026-07-06

### 本次目标
继续推进阶段 2，把 runtime error 从 DEBUG HUD 可见扩展为 normal mode 也有结构化 diagnostic 记录。

### 已完成
- 为 `RuntimeErrorRegistry` 增加 normal-mode diagnostic callback：
  - `record()` 写 `runtime_error_recorded`。
  - `resolve()` 写 `runtime_error_resolved`。
  - callback 异常不会打断业务路径。
- `RuntimeContext.__post_init__`、active-session error、CDP update failure、renderer file event source 都会确保 registry 绑定 `_append_runtime_error_diagnostic`。
- `_append_runtime_error_diagnostic()` 把 runtime error payload 写入既有 `renderer_fallback.log`，字段包含：
  - `source`
  - `severity`
  - `code`
  - `message`
  - `context`
  - `count`
  - `firstSeenAt`
  - `lastSeenAt`
- 修复 `_append_renderer_diagnostic()` 对 dict/list 等结构化字段的过滤逻辑，避免 `value not in {"", None}` 在 dict 字段上触发 `TypeError`。
- 扩展测试：
  - renderer-unmatched active session 会写入 `renderer_fallback.log`。
  - CDP update failure 会写入 `renderer_fallback.log`。
  - registry 级别验证 record/resolve 会调用 diagnostic callback。
- `task_plan.md` 已将阶段 2 的“正常模式结构化日志/diagnostic”标记为完成。

### 本轮验证
- `python -m pytest tests/test_ui.py::BudgetHelperTests::test_build_snapshot_records_renderer_unmatched_runtime_error -q` 先失败（`renderer_fallback.log` 不存在），实现 diagnostic callback 和 dict 字段过滤修复后通过。
- `python -m pytest tests/test_runtime_errors.py tests/test_ui.py::BudgetHelperTests::test_build_snapshot_records_renderer_unmatched_runtime_error tests/test_ui.py::BudgetHelperTests::test_record_cdp_update_failure_adds_runtime_error -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。

### 下一步
1. 继续阶段 2：删除「失败后静默换路径」逻辑，优先从 `docs/FALLBACK_INVENTORY.md` 中标为删除的路径开始。
2. 为每个已接入 runtime error 来源补“不被 fallback 掩盖”的测试矩阵。
3. 保持 renderer mode 为唯一产品路径，不把 Qt/Tk 或 native title polling 作为性能问题解决方案。

### 继续推进：阶段 2 收口
- 按用户要求，DEBUG HUD 现在在 debug 开启且无 runtime error 时也显示初始化状态行：
  - `debug.ready`
  - `DEBUG HUD active`
- 按用户批准的方案 A，保留 renderer 内 Runtime errors 面板，不改成 PySide 气泡：
  - 默认从右下角改到左下角，降低遮挡主视线的概率。
  - 标题栏支持拖动，拖动后位置写入既有 `codexUsageHudPanelState:v5`。
  - 正文独立滚动并设置 `user-select: text`，可直接选中复制错误内容。
- 删除 settings command 的 localStorage/CDP polling fallback：
  - renderer JS 不再写 `codexUsageHudSettingsCommand:v1`。
  - Python renderer loop 不再调用 `client.take_settings_command()`。
  - `RendererHudClient.take_settings_command()` API 已删除。
  - settings command 保留 settings bridge callback/event path。
- 删除 CDP update failure 的 hidden reinstall retry：
  - `_send_update()` 未 ack 时直接 failed。
  - 不再 `_install(... force=True)` 后二次 `_send_update()`。
  - 旧 `runtime_update_failed_retrying` diagnostic 已删除。
  - `cdp.update_failed` runtime error 是该失败的唯一 diagnostic 来源。
- 补齐已接入 runtime error 来源的“不被 fallback 掩盖”测试：
  - `active_session.unmatched_thread`
  - `cdp.update_failed`
  - `file_watcher.degraded`
  - `file_watcher.overflow`
- 更新 `task_plan.md`：阶段 2 标记为 complete。
- 更新 `docs/FALLBACK_INVENTORY.md`：settings command polling 和 CDP reinstall retry 标记为 Done。

### 阶段 2 targeted 验证
- `python -m pytest tests/test_renderer_hud.py::RendererHudPayloadTests::test_renderer_script_renders_debug_error_hud -q` 先失败（脚本没有 `DEBUG HUD active` 初始化行），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_does_not_poll_local_storage_settings_commands_when_idle -q` 先失败（idle tick 调用了 `take_settings_command()`），删除 loop polling 后通过。
- `python -m pytest tests/test_renderer_hud.py::RendererHudPayloadTests::test_update_payload_reports_failed_update_without_reinstall_retry -q` 先失败（client 执行 `_install(... force=True)`），删除 hidden reinstall retry 后通过。
- `python -m pytest tests/test_renderer_hud.py::RendererHudPayloadTests::test_payload_from_snapshot_formats_compact_hud_lines tests/test_renderer_hud.py::RendererHudClientTests::test_client_does_not_expose_renderer_settings_polling_fallback -q` 先失败（脚本和 client 仍暴露 settings command polling fallback），删除 JS localStorage command fallback 和 client polling API 后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_runtime_failures_retry_without_tk_fallback -q` 先失败（仍写 `runtime_update_failed_retrying`），删除旧 diagnostic 后通过。
- `python -m pytest tests/test_renderer_hud.py::RendererHudPayloadTests::test_renderer_script_renders_debug_error_hud tests/test_renderer_hud.py::RendererHudPayloadTests::test_update_payload_reports_failed_update_without_reinstall_retry tests/test_renderer_hud.py::RendererHudClientTests::test_client_does_not_expose_renderer_settings_polling_fallback tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_does_not_poll_local_storage_settings_commands_when_idle tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_bridge_settings_command_without_cdp_poll tests/test_ui.py::BudgetHelperTests::test_build_snapshot_records_renderer_unmatched_runtime_error tests/test_ui.py::BudgetHelperTests::test_record_cdp_update_failure_adds_runtime_error tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_records_degraded_polling tests/test_ui.py::DaemonLifecycleTests::test_renderer_file_event_source_records_overflow_without_polluting_reasons tests/test_ui.py::DaemonLifecycleTests::test_renderer_runtime_failures_retry_without_tk_fallback -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `rg -n "settingsCommandKey|codexUsageHudSettingsCommand|settings_poll|runtime_update_failed_retrying|_install\\(websocket_url, target_id, force=True\\)|take_settings_command" src\\codex_usage_hud docs` 无匹配。
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
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_layout_event_without_snapshot_refresh -q` 先失败（layout event 被 generic bridge wakeup 折叠成第二次 snapshot），实现 event handler 分派后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_layout_event_without_snapshot_refresh tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_settings_runtime_event tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_keeps_wakeup_for_active_session_event_during_wait tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_tracker_active_session_callback tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_bridge_settings_command_without_cdp_poll tests/test_ui.py::DaemonLifecycleTests::test_run_renderer_hud_session_drains_work_overlay_commands_with_window_prep -q` 通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- `python -m pytest tests/test_runtime_errors.py::RuntimeEventBusTests::test_event_payload_includes_source_timestamp_session_and_error_context -q` 先失败（`RuntimeEventBus.publish()` 不接受 `error` 且 event 没有 `to_payload()`），实现后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_does_not_use_runtime_signature_as_refresh_trigger -q` 先失败（renderer loop 仍调用 `_renderer_runtime_signature()`），移除 signature 驱动后通过。
- `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_publishes_update_state_event_before_refresh -q` 先失败（update-state 变化没有发布 runtime event），实现 `update_state_changed` 后通过。
- `python -m pytest tests/test_runtime_errors.py tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_skips_snapshot_when_runtime_signature_is_unchanged tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_does_not_use_runtime_signature_as_refresh_trigger tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_publishes_update_state_event_before_refresh tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_layout_event_without_snapshot_refresh tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_runtime_error_event tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_settings_runtime_event tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_keeps_wakeup_for_active_session_event_during_wait tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_wakes_for_tracker_active_session_callback tests/test_ui.py::DaemonLifecycleTests::test_renderer_loop_handles_bridge_settings_command_without_cdp_poll tests/test_ui.py::DaemonLifecycleTests::test_run_renderer_hud_session_drains_work_overlay_commands_with_window_prep -q` 先失败（旧测试未包含新的 `active_work_refresh_requested` 内部事件），更新断言后通过。
- `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py tests/test_runtime_errors.py tests/test_file_watcher.py -q` 通过。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。

## 错误日志
| 时间戳 | 错误 | 尝试次数 | 解决方案 |
|--------|------|---------|---------|
| 2026-07-06 | 新增 parser offset 测试在 Windows 上多 2 bytes | 1 | 根因是 `Path.write_text()` 文本模式换行转换；测试改用 `newline="\n"` 固定 LF 后通过 |
| 2026-07-06 | pytest node id 类名写错：新用例实际属于 `DaemonLifecycleTests`，误写为 `BudgetHelperTests` | 1 | 用 `rg` 定位测试类后改用正确 node id |
| 2026-07-06 | Codex manual helper 请求 developers.openai.com `HEAD` 返回 403 | 1 | 改用本机 `codex app-server --help` 和 `generate-json-schema --experimental` 做 app-server 协议实测 |
| 2026-07-03 | pytest node id 类名写错：`WorkOverlayTests` 不存在 | 1 | 用 `rg` 查到测试属于 `BudgetHelperTests`，改用正确 node id 后通过 |

## 2026-07-06 阶段 3 收口
- 完成 active-session 单一权威源阶段：
  - renderer mode 默认继续使用 renderer bridge 作为唯一 active-session 权威源。
  - 新增隐藏手动诊断开关 `--legacy-active-session-diagnostics`。
  - 默认不启用 legacy watcher；手动开启时才不挂起 native active-title，并允许 background watcher 启动用于对照诊断。
- app-server POC：
  - `codex app-server --help` 显示 app-server 为 experimental，支持 `stdio://`、`unix://`、`ws://IP:PORT`、daemon/proxy/schema 生成。
  - 本机生成 experimental JSON schema 后确认存在 `thread/list`、`thread/loaded/list`、`thread/read`、`thread/status/changed`、`thread/tokenUsage/updated`。
  - `thread/loaded/list` 只返回当前加载在内存中的 thread ids；`ThreadStatus.active` 表示 active turn 状态，不表示 UI 当前选中或聚焦窗口。
  - 未发现 “当前 Codex App 窗口正在看的 active thread” 字段或通知。
  - 结论：app-server 不接入默认 active-session 路径，只保留为未来显式权威源候选。
- TDD/验证记录：
  - `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_legacy_active_session_diagnostics_flag_is_opt_in tests/test_ui.py::DaemonLifecycleTests::test_build_runtime_context_can_enable_legacy_active_session_diagnostics -q` 先失败（参数不存在；手动诊断仍挂起 native title），实现后通过。
  - `python -m pytest tests/test_ui.py::DaemonLifecycleTests::test_legacy_active_session_diagnostics_flag_is_opt_in tests/test_ui.py::DaemonLifecycleTests::test_build_runtime_context_can_enable_legacy_active_session_diagnostics tests/test_ui.py::DaemonLifecycleTests::test_build_runtime_context_uses_renderer_bridge_instead_of_native_title_watcher -q` 通过。

## 2026-07-06 阶段 4 收口
- 提交并推送阶段 1-3 收口改动：
  - commit: `44d1bf5 refactor(renderer): expose runtime errors`
  - branch/upstream: `codex/renderer-event-dispatcher` -> `origin/codex/renderer-event-dispatcher`
- 新增当前 session JSONL 增量解析状态：
  - `JsonlTailState`
  - `JsonlSessionParser.parse_file_incremental()`
  - 保存 path、file identity、offset、完整物理行数、最后完整行、records、累计 snapshot。
- 增量读取行为：
  - append 只读取上次 offset 之后的新增 bytes。
  - 只 JSON-decode 新增完整行。
  - trailing partial line 不推进 offset，等下一次 append 补齐后再解析。
  - truncate、rotate、session switch 触发 tail state reset。
- `RuntimeContext` 新增 `current_session_tail_state`。
- `build_snapshot()` 当前 session 路径改为 `context.parser.parse_file_incremental()`，并把 state 写回 context。
- 当前请求、会话累计、heavy rounds、activity trail 继续从同一个 `ParsedSession` 读取，因此共享当前 session tail state。
- 日/周预算聚合继续使用既有 `UsageSummaryCache` per-file contribution replacement：
  - 当前 session 文件变化且无需全量预算刷新时，只刷新该文件贡献。
  - 非当前 session 或预算窗口变化仍可触发全量扫描。
- 性能脚本更新：
  - append 场景从 `append_then_parse_and_payload` 改为 `append_then_incremental_parse_and_payload`。
  - append 测量使用临时 session 副本和复用的 `JsonlTailState`。
- TDD/验证记录：
  - `python -m pytest tests/test_parser.py::JsonlSessionParserTests::test_incremental_parse_reads_only_appended_complete_records tests/test_parser.py::JsonlSessionParserTests::test_incremental_parse_preserves_incomplete_trailing_line tests/test_parser.py::JsonlSessionParserTests::test_incremental_parse_resets_after_truncate_or_rotation -q` 先失败（`JsonlTailState` 不存在），实现后通过。
  - `python -m pytest tests/test_ui.py::BudgetHelperTests::test_build_snapshot_uses_incremental_parser_for_current_session -q` 先失败（仍调用 `parse_file`），实现后通过。
  - `python -m pytest tests/test_measure_renderer_latency.py -q` 先失败（性能脚本仍输出旧 append 指标），实现后通过。
  - `python -m pytest tests/test_parser.py tests/test_ui.py -q` 通过。

## 五问重启检查
| 问题 | 答案 |
|------|------|
| 我在哪里？ | 阶段 1、2、3、4 完成；当前 session refresh 已切到 JSONL tail state |
| 我要去哪里？ | 进入阶段 5，收口 macOS sessions tree watcher / fallback polling degraded 标记，并继续拆 overlay polling |
| 目标是什么？ | renderer 权威、事件驱动、失败显式、响应速度优先 |
| 我学到了什么？ | 当前 session 可以先消除全文件 I/O：append 只读新增完整行；预算聚合已有单文件贡献替换基础 |
| 我做了什么？ | 提交并推送阶段 1-3；收口阶段 4：新增 `JsonlTailState`、接入 `build_snapshot()`、更新性能 append 基线 |

---
*每个阶段完成后或遇到错误时更新此文件。*
