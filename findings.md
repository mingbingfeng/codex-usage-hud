# 发现与决策：Renderer HUD 重构与性能调优

## 当前实现事实
- Renderer mode 是当前产品方向，`docs/RENDERER_MODE_STRATEGY.md` 已明确不应通过 Qt/Tk 回退解决 renderer 性能问题。
- renderer 模式下，`build_runtime_context()` 会调用 `platform.suspend_native_active_title(True)`，并让 `ActiveSessionTracker` 不启动后台 title watcher。
- 当前 active session 主路径是 renderer 注入脚本：
  - 从 sidebar/header/location 读取 session id/title。
  - click、history、targeted MutationObserver 触发上报。
  - 40ms debounce 后通过 CDP binding 传给 Python。
- Python 侧 `ActiveSessionTracker.observe_conversation_ref()` 负责把 renderer ref 映射到本地 JSONL：
  - 优先 thread id -> state db / session file。
  - 再 title -> session_index / state db。
  - 映射失败时当前仍可能进入 unmatched source，而不是直接错误。
- 当前 usage snapshot：
  - 当前会话 refresh 通过 `JsonlSessionParser.parse_file_incremental()` 复用 `JsonlTailState`。
  - `JsonlTailState` 保存 offset、file identity、最后完整行、records 和累计 snapshot。
  - append 时只从上次 offset 之后 JSON-decode 新增完整行；partial trailing line 等下一次补齐。
  - truncate、rotate、session switch 会重置 tail state 并重新建立当前 session snapshot。
  - 日/周预算聚合走 `UsageSummaryCache`，按 mtime/size 缓存。
  - 当前 session 文件变化时可只替换该文件预算贡献。
- renderer 主循环已有事件驱动雏形：
  - `_RendererFileEventSource` 监听 settings、session_index、state db、sessions tree、当前 session 文件。
  - `_renderer_runtime_signature()` 用 session stat、selection source、settings mtime、预算窗口、update state、command status 判断是否刷新。
  - 无变化时跳过 snapshot 和 CDP payload push。
- 仍存在周期性工作：
  - 运行中快档默认 500ms。
  - 空闲默认 1.5s。
  - event-driven watcher 可把 idle wait 拉到 30s。
  - active work items 最多 5s 重扫。
  - PySide overlay helper 160ms 读 state file，60ms pointer sync。
  - settings command 仍有 1s localStorage polling fallback。
- JS 端 HUD 更新：
  - `window.__codexUsageHudUpdate(payload)` 一次刷新 top/bottom 多数 data-field。
  - 运行中 row elapsed 每秒局部 tick。
  - stale guard 10s 后一次性标警。
  - layout 用 MutationObserver / ResizeObserver + requestAnimationFrame 合帧。
- 文件监听：
  - Windows 用 `ReadDirectoryChangesW`。
  - macOS kqueue 只覆盖非 recursive tree；recursive tree 会 fallback polling。
  - polling fallback 会全树生成 stat token，sessions 多时成本高。
  - 当前 Windows watcher 没有显式处理 `bytes_returned == 0` 的缓冲溢出补偿。

## 关键频次
| 链路 | 当前频次 |
|------|----------|
| renderer active session 上报 | 40ms debounce |
| composer attachments 上报 | 80ms debounce |
| renderer 快档刷新 | `poll_ms`，默认 500ms |
| renderer 空闲刷新 | 1.5s，event-driven idle 可到 30s |
| 文件 watcher fallback | 5s polling |
| 文件事件 debounce | 0.75s |
| active work 重扫 | 事件触发或 5s |
| PySide overlay state poll | 160ms |
| PySide overlay pointer sync | 60ms |
| work overlay keepalive | 5s |
| renderer settings command poll | 1s |
| JS running row tick | 1s |
| JS stale guard | 10s |

## 阶段 0 本机基线
运行命令：

```powershell
python tools/measure_renderer_latency.py --iterations 1 --warmups 0 --json-output renderer_latency_baseline.json --markdown-output renderer_latency_baseline.md
```

样本：
- sessions root: `C:\Users\zjxqm\.codex\sessions`
- session file: `C:\Users\zjxqm\.codex\sessions\2026\07\03\rollout-2026-07-03T14-48-53-019f26bc-b231-7181-8ed6-25525ea3cf54.jsonl`
- session size: 545,644 bytes
- session lines: 132

低迭代结果：

| 操作 | median ms | 说明 |
|------|-----------|------|
| `current_session_parse_full` | 7.111 | 当前 session 全文件 parse |
| `renderer_payload_build` | 1.141 | Python payload 构建，不含 CDP/DOM |
| `usage_summary_full_scan` | 69.765 | sessions + archived_sessions 全量预算扫描 |
| `usage_summary_refresh_current_file` | 2.708 | 单文件贡献替换 |
| `file_watcher_poll_signature` | 55.724 | polling fallback 的全树 stat token |
| `append_then_parse_and_payload` | 20.158 | 阶段 0 旧基线：临时副本 append 后 full parse + payload |

阶段 4 后，性能脚本把 append 场景改名为 `append_then_incremental_parse_and_payload`，并复用 `JsonlTailState` 测量 append 后增量解析 + payload 构建。

限制：
- 该 harness 不测真实 CDP transport、renderer DOM paint、用户可见端到端延迟。
- append 场景只写临时副本，不修改真实 Codex JSONL。
- `file_watcher_poll_signature` 代表 fallback polling 成本，不代表 native watcher 事件送达成本。

## 外部资料结论
- Chrome DevTools Protocol 支持 `Runtime.addBinding` / `Runtime.bindingCalled`，适合 renderer -> Python 的事件通道。
- Chrome DevTools Protocol 支持 `Page.addScriptToEvaluateOnNewDocument`，适合 renderer bootstrap。
- MDN 对 `MutationObserver`、`ResizeObserver`、`requestAnimationFrame` 的语义支持当前 targeted observer + rAF 合帧方向。
- Microsoft `ReadDirectoryChangesW` 文档说明目录更改缓冲可能溢出；溢出时需要重新枚举目录补偿。
- OpenAI Codex app-server 文档显示其提供 JSON-RPC、`thread/list`、`thread/loaded/list`、`thread/tokenUsage/updated`、`turn/item`、`fs/watch` 等能力。
- app-server 是潜在的更权威 usage/work 数据源，但目前未知它是否能提供“当前 Codex App 窗口正在看的 active thread”。不能在未实测前直接替换 renderer active-session bridge。

## 风险与重构机会
| 风险 | 影响 | 重构方向 |
|------|------|----------|
| 当前会话全文件 parse | 已从当前 session refresh 主路径移除；历史/非当前候选仍可能全量解析 | JSONL tail parser / 后续 active work 候选增量化 |
| 多层 fallback 掩盖失败 | DOM/协议变化不易发现 | DEBUG 错误 HUD + 显式错误事件 |
| native title / latest JSONL fallback | 可能显示错会话 | renderer mode 单一 active-session 权威源 |
| Windows watcher 溢出未补偿 | 高写入时漏事件 | 溢出时枚举补偿并报警 |
| macOS recursive tree polling | sessions 多时成本高 | FSEvents 或只 watch 当前 session + index |
| overlay 160ms 文件 polling | 空闲也有周期 IO | push IPC 或 watcher 唤醒 |
| settings command localStorage polling | 周期性 CDP evaluate | CDP binding / custom event |
| payload 单体刷新 | 局部变化也刷新 top/bottom 多字段 | payload 分区和局部 DOM 更新 |

## Runtime Error 初始实现
- 新增 `RuntimeErrorEvent` 和 `RuntimeErrorRegistry`，按 `source + code` 聚合重复错误。
- 新增 `RuntimeEventBus` 和 `RuntimeEvent`，作为 renderer runtime 的轻量 in-process 事件出口。
- payload 字段使用 camelCase：
  - `firstSeenAt`
  - `lastSeenAt`
  - `runtimeErrors`
- `RuntimeErrorRegistry.record()` 会把短 code 规范成 `source.code`，例如 `source=active_session` + `code=unmatched_thread` -> `active_session.unmatched_thread`。
- Renderer payload 支持：
  - `debug`
  - `runtimeErrors`
- DEBUG 面板只在 `debug=true` 且 `runtimeErrors` 非空时显示。
- 当前 DEBUG 开关为环境变量 `CODEX_USAGE_HUD_DEBUG`。
- 已接入的首批错误：
  - `active_session.unmatched_thread`：`build_snapshot()` 看到 `renderer-unmatched...` selection source 时记录；renderer 已匹配或新会话时 resolve。
  - `file_watcher.degraded`：`_RendererFileEventSource` 发现 watcher 不是 event-driven 时记录；恢复 native/event-driven 时 resolve。
  - `file_watcher.overflow`：Windows `ReadDirectoryChangesW` 返回 `bytes_returned == 0` 时，worker 发起目录补偿，renderer event source 记录 runtime error，并只把业务 reasons 交给刷新逻辑。
  - `cdp.update_failed`：renderer payload update 失败时记录；下一次 update 成功时 resolve。
- `RuntimeContext` 持有共享 `runtime_events` 和 `runtime_errors`；`RuntimeErrorRegistry.record/resolve` 会发布 `runtime_error` 事件。
- renderer loop 订阅 `runtime_error` 事件并设置现有 wake event，因此 registry 变化可以触发新一轮 renderer refresh。
- renderer loop 订阅 `active_session_changed` 事件并设置 active-session refresh + command wake event。
- renderer loop 订阅 `session_file_changed` / `settings_changed` / `settings_command_received` / `overlay_command_received` 并设置 command wake event。
- active session 的两个入口现在会发布 `active_session_changed`：
  - tracker background callback -> context `{"reason": "tracker_callback"}`。
  - renderer bridge `observe_conversation_ref()` changed -> context `{"reason": "renderer_bridge"}`。
- `_RendererFileEventSource` 现在会在实际 wake 的同一时刻发布 coalesced runtime events：
  - `session` / `sessions-root` -> `session_file_changed`。
  - `settings` -> `settings_changed`。
  - event context 包含 coalesced `reasons` 和 `paths`。
- settings bridge command callback 现在发布 `settings_command_received`：
  - source 为 `settings_bridge`。
  - context 包含 `action`、`id` 和原始 command。
- work overlay command pump 现在发布 `overlay_command_received`：
  - source 为 `work_overlay`。
  - context 包含 `action`、`sessionId`、`title`、`current` 和处理结果状态。
- 限制：当前主要 runtime 事件已接入 event bus；settings command 的 localStorage polling fallback 和 overlay command pump 的 60ms polling 仍未删除。

## Normal-mode Runtime Error Diagnostic
- runtime error 现在不只进入 DEBUG HUD payload，也会在 normal mode 写入 `renderer_fallback.log`。
- 写入入口是 `RuntimeErrorRegistry.diagnostic_callback`：
  - `record()` 调用 action `recorded`。
  - `resolve()` 调用 action `resolved`。
  - callback 异常被 registry 捕获，避免 diagnostic 文件系统问题影响 HUD 主路径。
- `RuntimeContext.__post_init__` 会把默认 callback 绑定到 `_append_runtime_error_diagnostic()`；针对测试中构造的轻量 context，active-session error、CDP update failure 和 renderer file event source 入口也会再次确保绑定。
- `_append_runtime_error_diagnostic()` 复用既有 `_append_renderer_diagnostic()`，stage 格式为：
  - `runtime_error_recorded`
  - `runtime_error_resolved`
- diagnostic record 保留结构化字段：
  - `source`
  - `severity`
  - `code`
  - `message`
  - `context`
  - `count`
  - `firstSeenAt`
  - `lastSeenAt`
- `_append_renderer_diagnostic()` 的字段过滤必须使用 `value is not None and value != ""`，不能用 set membership；runtime error context 是 dict，`value not in {"", None}` 会触发 `TypeError` 并导致 callback 被 registry 静默吞掉。
- 当前已用文件写入测试覆盖：
  - `active_session.unmatched_thread`
  - `cdp.update_failed`
- registry 级别测试覆盖 record/resolve callback。后续仍需为 file watcher degraded/overflow 等错误源补“不被 fallback 掩盖”的测试矩阵。

## 阶段 2 收口：失败显式化
- DEBUG HUD 现在在 `debug=true` 且没有 runtime error 时也显示初始化行：
  - `debug.ready`
  - `DEBUG HUD active`
  - 用于实测确认 DEBUG HUD 已注入，而不是等出错后才出现。
- Runtime errors 面板继续留在 renderer HUD 内部，而不是改成 PySide 气泡：
  - 这符合 renderer mode 为产品主路径的约束。
  - 可用性问题通过位置、拖动和文本选择解决。
  - 面板状态复用既有 `codexUsageHudPanelState:v5`，避免新增跨进程 overlay 状态。
- settings command 的 localStorage/CDP polling fallback 已删除：
  - renderer JS 不再声明 `settingsCommandKey`。
  - renderer JS 不再执行 `localStorage.setItem(settingsCommandKey, ...)`。
  - `RendererHudClient.take_settings_command()` 已删除。
  - renderer loop 不再调用 `client.take_settings_command()`。
  - settings command 只通过 settings bridge callback 进入 `settings_command_received` runtime event。
- CDP payload update 失败后不再 force reinstall HUD script 并重试：
  - `RendererHudClient.update_payload()` 如果 `__codexUsageHudUpdate(payload)` 未 ack，会直接返回 failed。
  - Python loop 通过 `_record_cdp_update_failure()` 记录 `cdp.update_failed`。
  - 旧 `runtime_update_failed_retrying` diagnostic 已删除，避免暗示还有隐藏重试/fallback。
- 阶段 2 已接入错误源的测试矩阵：
  - `active_session.unmatched_thread`：不会 fallback 到 latest JSONL activity，并写 normal diagnostic。
  - `cdp.update_failed`：不会 force reinstall/retry，不切 Qt/Tk，写 normal diagnostic。
  - `file_watcher.degraded`：polling fallback 标记为 warning，并写 normal diagnostic。
  - `file_watcher.overflow`：overflow 哨兵不污染业务 reasons，同时记录 warning 和 normal diagnostic。
- 当前仍保留的 polling/fallback 不属于阶段 2 已删除范围：
  - file watcher native 不可用时的 degraded polling，后续阶段 5 处理。
  - desktop overlay state/command polling，后续阶段 7 处理。
  - renderer failure backoff delay，保留为避免失败时 tight loop；它不切换数据源、不重装脚本、不隐藏错误。

## Runtime Event Bus 初始实现
- 新增 `src/codex_usage_hud/core/runtime_events.py`：
  - `RuntimeEvent(type, source, timestamp, session, context)`
  - `RuntimeEventBus.subscribe(callback)`
  - `RuntimeEventBus.publish(...)`
  - `RuntimeEventBus.drain()`
- `RuntimeErrorRegistry` 使用自身 clock 作为 runtime error event timestamp，避免 registry payload 时间和 bus event 时间不一致。
- runtime error event context：
  - record: `{"action": "recorded", "error": event.to_payload()}`
  - resolve: `{"action": "resolved", "code": event.code, "error": event.to_payload()}`
- session 关联从 error context 的 `sessionPath`、`session`、`sessionId`、`threadId` 中提取第一个非空值。
- renderer loop 现在会在 tick 开始时 `RuntimeEventBus.drain()`，并将事件交给显式 handler 汇总 refresh intent：
  - `runtime_error` -> diagnostics snapshot。
  - `active_session_changed` -> active-session snapshot，且可走轻量 active-work 复用路径。
  - `session_file_changed` -> snapshot。
  - `settings_changed` / `settings_command_received` -> fast snapshot。
  - `overlay_command_received` -> fast snapshot。
  - `budget_window_changed` -> fast budget snapshot。
  - `update_state_changed` -> fast snapshot。
  - `active_work_refresh_requested` -> fast snapshot。
  - `renderer_layout_changed` -> 只唤醒 loop/keepalive，不请求 snapshot；拖拽、缩放、展开等布局事件由 renderer 端已完成 DOM 状态处理。
- `command_refresh_requested` 仍作为跨线程 wake event 使用，但不再意味着“必然 snapshot”。如果 wake 对应已 drain 的 event 且 handler 没有请求 snapshot，则 tick 会复用现有 snapshot 并只调用 overlay keepalive。
- `RuntimeEvent.to_payload()` 现在显式输出 `type`、`source`、`timestamp`、`session`、`context`、`error`，runtime error registry 同时把错误 payload 放在 `context["error"]` 和事件 `error` 字段中，方便后续诊断/序列化。
- signature drift / legacy bridge wakeup 不再主动触发 snapshot；`_renderer_runtime_signature()` 已不在 renderer loop 中调用。
- loop 中 snapshot 决策现在只有两种来源：
  - 初始化还没有 `latest_snapshot`。
  - `event_refresh_request.snapshot`，即事件 handler 显式请求。
- 阻塞 wait 仍用于事件唤醒、overlay keepalive、daemon watchdog 和失败 backoff；它不再代表固定周期 snapshot rebuild。

## Active Session Fallback 收口
- `ActiveSessionTracker(start_background_watcher=False)` 现在代表 renderer-authoritative 模式：
  - 没有 renderer selection 时，`current_path()` 返回 `None`，source 为 `renderer-waiting`。
  - 不调用 `platform.get_active_conversation_ref()`。
  - 不调用 native title polling / event fallback。
- `SessionPathResolver` 看到 `renderer-waiting` 会清空 `auto_session_file` 并返回 `(None, "renderer-waiting")`，不再扫描 latest JSONL。
- `SessionPathResolver` 现在把 `renderer-unmatched*` 视为 renderer 权威链路的显式失败状态：
  - 不再调用 `platform.detect_active_session()`。
  - 清空 `auto_session_file`。
  - 返回 `(None, "renderer-unmatched")`。
- 这删除了 renderer mode 下的 CDP/native active ref、native title、latest JSONL activity 默认 fallback，避免 renderer DOM/thread 映射失败或等待 renderer 事件时自动显示错误 session。
- `build_snapshot()` 仍会通过 `_record_active_session_runtime_error()` 记录 `active_session.unmatched_thread`，DEBUG HUD 可显示该错误；普通 payload 会进入 waiting/missing 路径，而不是错误地绑定到另一个 session。
- 目前 `ui-unmatched` / `cdp-unmatched` 仍可走 legacy activity fallback，用于后续“诊断保留或隔离”阶段；本次只切断 renderer mode 的 unsafe fallback。
- `--legacy-active-session-diagnostics` 是隐藏手动诊断开关：
  - 默认 `False`，renderer mode 仍调用 `platform.suspend_native_active_title(True)` 并关闭 background watcher。
  - 显式开启后不挂起 native active-title，并允许 background watcher 启动，用于对照诊断 legacy active-session 链路。
  - 该开关不是产品 fallback，不应作为 renderer 问题的默认解决方案。

## Codex app-server Active Thread POC
- `node .../fetch-codex-manual.mjs` 在本机因 developers.openai.com `HEAD` 403 未能获取 Codex manual；随后使用本机 `codex app-server --help` 和生成的 experimental JSON schema 做协议级实测。
- 本机 Codex CLI 暴露 `codex app-server`，支持 `stdio://`、`unix://`、`ws://IP:PORT` 以及 `generate-json-schema --experimental`。
- 本机生成 schema 显示 app-server 有：
  - `thread/list`
  - `thread/loaded/list`
  - `thread/read`
  - `thread/status/changed`
  - `thread/tokenUsage/updated`
- `thread/loaded/list` response 只说明 “Thread ids for sessions currently loaded in memory”，不等价于当前 Codex App 窗口选中的 thread。
- `ThreadStatus` 的 `active` 表示线程有 active turn，并带 `waitingOnApproval` / `waitingOnUserInput` 等 flag；它不是 UI focus/selected/current-window 语义。
- schema 未发现可证明当前窗口正在查看的 active thread 字段或通知（如 focused/current/selected window thread）。
- 结论：app-server 可作为未来 usage/work 数据源候选，但截至本次 POC 不能替换 renderer bridge，也不能作为 renderer active-session 的隐式 fallback。

## File Watcher Overflow 补偿
- Windows `ReadDirectoryChangesW` 文档中的缓冲溢出表现为成功返回但 `bytes_returned == 0`；此时原始变更记录已丢失，不能解析 buffer。
- 当前补偿策略：对该 Windows worker 的现有 specs 重新枚举匹配路径，回调所有受影响业务 reasons，并附加内部哨兵 reason `file_watcher.overflow`。
- `_RendererFileEventSource` 收到哨兵后：
  - 记录 `file_watcher.overflow` runtime error，context 包含业务 reasons 和补偿路径。
  - 从正常 `file_change_reasons` 中移除哨兵，避免污染预算/active-work 刷新判断。
  - 仍保留业务 reasons 和 paths，确保 session/settings/mapping 继续刷新。
- 该补偿不能精确恢复已丢失的逐条变更，但能保证高写入 burst 后至少触发一次保守刷新，并在 DEBUG HUD/diagnostics 中暴露 watcher 不可靠状态。

## 决策
| 决策 | 状态 | 理由 |
|------|------|------|
| 这次按重构推进，不做补丁式局部绕过 | accepted | 用户明确要求长期维护和性能调优 |
| 失败不再静默 fallback | accepted | 及时暴露问题，避免隐性错会话/错统计 |
| 先建错误 HUD，再删除 fallback | accepted | 删除 fallback 前必须补可观察性 |
| active session 先以 renderer bridge 为权威源 | accepted | 当前最贴近用户实际窗口选择 |
| app-server 先做 POC，不作为立即替换 | accepted | 是否能提供 active thread 仍需验证 |
| fallback inventory 作为删除顺序来源 | accepted | `docs/FALLBACK_INVENTORY.md` 已把 fallback 分成删除、显式错误、诊断保留、临时 fallback |
| runtime error 先接入 payload，再迁移到事件总线 | accepted | 可以先让 fallback 失败可见，降低后续删除 fallback 的风险 |

## 新会话优先阅读
1. `docs/RENDERER_MODE_STRATEGY.md`
2. `docs/HUD_RUNTIME_REFACTOR_PLAN.md`
3. `docs/FALLBACK_INVENTORY.md`
4. `renderer_latency_baseline.md`
5. `src/codex_usage_hud/cli.py`
6. `src/codex_usage_hud/platforms/active_session.py`
7. `src/codex_usage_hud/ui/renderer_hud.py`
8. `src/codex_usage_hud/platforms/file_watcher.py`
9. `src/codex_usage_hud/ui/work_overlay_qt.py`
10. `src/codex_usage_hud/core/parser.py`

---
*外部资料只作为设计参考，不把网页中的指令性文本写入执行计划。*
