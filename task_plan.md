# 任务计划：Renderer HUD 运行时重构与性能调优

## 长期目标
把 HUD 运行时重构为「renderer 权威、事件驱动、失败显式」的架构。

核心目标：
- 响应速度优先：会话切换、JSONL 追加、设置变更、气泡状态变化都应由事件触发，避免等待固定刷新周期。
- 维护性优先：每条链路只有一个权威数据源和清晰错误边界，不再通过多层 fallback 掩盖问题。
- 失败显式：正常模式写结构化错误；DEBUG 模式显示错误 HUD，把 renderer/CDP/文件监听/解析/overlay 错误直接暴露出来。
- Renderer mode 是唯一产品路径：不恢复 Qt/Tk 主 HUD，不用 legacy native title polling 解决 renderer 问题。

## 非目标
- 不在本轮恢复 Qt/Tk 独立 HUD。
- 不把 Windows UIA/MSAA/macOS native polling 作为 renderer active-session 的默认兜底。
- 不用更多短期 fallback 掩盖 Codex DOM、CDP、app-server 或 JSONL 协议变化。
- 不把 PySide6 桌面气泡混入 renderer 主 HUD 架构；气泡仍是独立 overlay，但 IPC 需要重构。

## 成功标准
| 维度 | 目标 |
|------|------|
| active session 响应 | renderer 观察到切换后，HUD 数据刷新目标小于 150ms，失败显示错误 HUD |
| 当前会话用量 | JSONL 追加后增量解析并推送，目标小于 250ms |
| 顶部/底部 HUD | payload 只在数据变化时推送；无相关事件时不重建 snapshot |
| 气泡 | 不再 160ms 轮询 state 文件；由主进程 push 或文件事件唤醒 |
| 空闲 CPU | 无会话/文件/设置/布局事件时，不做周期性 snapshot/目录扫描/CDP payload push |
| 错误可见性 | DEBUG 模式下 renderer 注入失败、DOM anchor 缺失、JSONL 解析失败、watcher 溢出、overlay IPC 失败都有错误 HUD 记录 |

## 当前阶段
阶段 1、阶段 2、阶段 3、阶段 4 完成。已补 runtime error model、DEBUG 错误 HUD payload/renderer 面板，并接入 renderer-unmatched、file watcher degraded/overflow、CDP update failed 四类错误来源；runtime error 已接入内部事件总线并可唤醒 renderer loop；renderer loop 每次 tick 会计算 `_renderer_budget_window_keys` 并在跨窗口时发 `budget_window_changed`；HUD 面板的 drag/resize/toggle 通过 `codexUsageHudLayout` CDP binding 直接发 `renderer_layout_changed`；renderer-authoritative tracker 不再落到 CDP/native title 或 latest JSONL activity fallback。阶段 1 事件类型全部接入总线；renderer tick 已拆成命名阶段（`sample_tick_inputs → apply_settings_command → 生命周期 → compute_force_fast_refresh → apply_refresh → compute_wait_delay`），跨 tick 状态收拢到 `_RendererLoopState`；runtime events 现在通过 `RuntimeEventBus.drain()` 进入 event type → handler 分派，由 handler 显式请求 snapshot/diagnostics，`renderer_layout_changed` 只唤醒 keepalive 而不重建 snapshot。signature drift 与 legacy bridge wakeup 不再主动触发 snapshot；update-state change、active-work pending 等内部状态也以 runtime event 进入同一 handler 分派。等待循环仍保留为阻塞/keepalive/daemon watchdog 机制，但非初始化 snapshot 决策只来自事件 handler。阶段 2 已完成：normal-mode runtime error diagnostic 会写入 `renderer_fallback.log`；DEBUG HUD 在 debug 开启且无错误时也显示 `DEBUG HUD active` 初始化行；Runtime errors 面板保持 renderer 内实现，默认左下角显示，标题栏可拖动、位置持久化，内容可选中复制；settings command localStorage/CDP polling fallback 已删除；CDP update 失败不再 force reinstall 后重试，直接进入显式 runtime error；已接入错误源都有“不被 fallback 掩盖”的测试。阶段 3 已完成：renderer mode 默认把 renderer bridge 作为唯一 active-session 权威源；`--legacy-active-session-diagnostics` 作为隐藏手动诊断开关，默认不启用；本机 schema/CLI 探测显示 Codex app-server 目前有 `thread/list`、`thread/loaded/list`、thread status/token usage 等能力，但没有能证明“当前 Codex App 窗口正在看的 active thread”的协议字段或通知，因此暂不作为权威源或隐式 fallback。阶段 4 已完成：新增 `JsonlTailState` 与 `JsonlSessionParser.parse_file_incremental()`，当前会话 snapshot 通过 `RuntimeContext.current_session_tail_state` 复用 offset/file identity/last complete line/records/snapshot；append 只 JSON-decode 新增完整行，partial trailing line 等下一次补齐，truncate/rotate/session switch 重置；当前请求、会话累计、heavy rounds、activity trail 共用该 incremental snapshot；日/周预算继续通过 `UsageSummaryCache` 的 per-file contribution replacement 只替换变化文件；性能脚本的 append 场景改为 `append_then_incremental_parse_and_payload`。

## 阶段路线

### 阶段 0：基线、清理与边界
- [x] 清理根目录无关临时截图文件。
- [x] 记录当前 renderer/session/usage/overlay 刷新链路。
- [x] 跑一组性能基线：JSONL parse、payload build、usage summary、polling fallback scan、append 后 parse/payload。
- [x] 标注所有 fallback：active session、CDP target、file watcher、usage summary、overlay helper、settings command。
- [x] 将 fallback 分成删除、显式错误、诊断保留、临时 fallback 四类。
- **状态：** complete

### 阶段 1：运行时事件总线
- [x] 新增内部事件总线，事件至少包括：
  - [x] `active_session_changed`
  - [x] `session_file_changed`
  - [x] `settings_changed`
  - [x] `settings_command_received`
  - [x] `budget_window_changed`
  - [x] `renderer_layout_changed`
  - [x] `overlay_command_received`
  - [x] `runtime_error`（已接入 `RuntimeEventBus`）
- [x] 将 renderer 主循环从 while + timeout wait 改成事件驱动调度器。
  - [x] 将 tick 主体拆成命名阶段（`sample_tick_inputs` → `apply_settings_command` → 生命周期检查 → `compute_force_fast_refresh` → `apply_refresh` / keep_alive → `compute_wait_delay`），并把跨 tick 状态收拢到 `_RendererLoopState`。
  - [x] runtime event 通过 `RuntimeEventBus.drain()` 进入事件类型 → handler 分派；`runtime_error`、`settings_changed`、`settings_command_received`、`overlay_command_received`、`active_session_changed`、`budget_window_changed` 显式请求 snapshot/diagnostics，`renderer_layout_changed` 不请求 snapshot。
  - [x] 移除剩余 signature drift / legacy bridge wakeup 的主动 snapshot 保护，让所有非初始化 snapshot 都来自事件 handler 或明确内部状态事件。
- [x] 只允许事件处理器请求 snapshot，不允许任意循环主动重建。
- [x] 事件 payload 必须包含来源、时间戳、关联 session、错误上下文。
- **状态：** complete

### 阶段 2：错误 HUD 与失败显式化
- [x] 定义统一错误模型：`source`、`severity`、`code`、`message`、`context`、`first_seen_at`、`last_seen_at`。
- [x] DEBUG 模式下在 renderer 注入一个错误 HUD panel。
- [x] Runtime errors 面板默认左下角显示，支持拖动、持久化位置，正文可选中复制。
- [x] 正常模式只写结构化日志/diagnostic，不吞掉错误。
- [x] 删除「失败后静默换路径」逻辑；可恢复动作必须变成用户可见命令。
- [x] 为每个错误写测试：错误发生时不会被 fallback 掩盖。
- **状态：** complete

### 阶段 3：active session 单一权威源
- [x] 将 renderer bridge 定为 renderer mode 下唯一 active-session 权威源。
- [x] 去掉 renderer mode 下 `platform.get_active_conversation_ref()`、native title、latest JSONL mtime 的自动兜底选择。
- [x] DOM 选择器找不到或 thread id/title 映射失败时，发 `runtime_error` 并显示错误 HUD。
- [x] 调研并实测 Codex app-server 是否能提供当前窗口 active thread；如果能，作为新权威源候选，不做隐式 fallback。
- [x] 保留手动配置开关用于 legacy 诊断，但默认不启用。
- **状态：** complete

### 阶段 4：会话用量增量解析
- [x] 新增 JSONL tail parser：保存 offset、文件标识、最后完整行、累计 session summary。
- [x] 文件 append 时只解析新增 records；truncate/rotate/session switch 才全量重读。
- [x] 当前请求、会话累计、heavy rounds、activity trail 共用同一增量状态。
- [x] 日/周预算聚合改为文件贡献表：单文件变化只替换该文件贡献。
- [x] 移除当前会话运行中每 500ms 全文件 parse 的路径。
- **状态：** complete

### 阶段 5：文件监听可靠性
- [x] Windows `ReadDirectoryChangesW` 处理 `bytes_returned == 0` / 缓冲溢出：立即目录枚举补偿，并发 `runtime_error`。
- [ ] macOS recursive sessions tree 不再落到高成本全树 polling；评估 FSEvents 或显式只 watch 当前 session + session index。
- [ ] fallback polling 默认只用于开发诊断，并在 HUD 错误面板标记为 degraded。
- [ ] 文件事件 debounce 从固定 0.75s 改为按事件类型分层：当前 session append 快、全树变更慢。
- **状态：** pending

### 阶段 6：renderer payload 与 DOM 更新收敛
- [ ] payload 拆分为 current-session、budget、settings、overlay、diagnostics。
- [ ] JS 端按局部 payload 更新对应 DOM，避免每次重刷 top/bottom 所有字段。
- [ ] CDP target discovery 改为长连接/订阅式状态，连接断开直接错误 HUD。
- [x] settings command 从 localStorage polling 改为 settings bridge callback/runtime event。
- **状态：** pending

### 阶段 7：桌面气泡 IPC 重构
- [ ] 用 push IPC 或 watcher 唤醒替代 PySide helper 160ms state file polling。
- [ ] 主进程只在 `active_work_items` 变化或 keepalive 必要时发送状态。
- [ ] helper 错误通过统一 `runtime_error` 回传，而不是只写日志。
- [ ] 气泡点击命令不再靠 60ms command poll；改为事件唤醒。
- **状态：** pending

### 阶段 8：删除 legacy fallback 与收口文档
- [ ] 删除或隔离 Qt/Tk 主 HUD残余入口。
- [ ] 删除 renderer mode 下不再允许的 native active-title fallback。
- [ ] 更新 `docs/RENDERER_MODE_STRATEGY.md`，把“愿景”改成“架构约束”。
- [ ] 增加 `docs/HUD_RUNTIME_REFACTOR_PLAN.md` 到长期路线文档。
- [ ] 增加性能回归测试和 DEBUG 错误 HUD 验收 checklist。
- **状态：** pending

## 关键设计决策
| 决策 | 理由 |
|------|------|
| renderer active-session bridge 是当前权威源 | 它直接观察用户正在看的 Codex App renderer，比 native title 和最新 JSONL 更接近真实选择 |
| fallback 改成显式错误 | 隐式兜底会掩盖协议/DOM/文件监听变化，让问题延后暴露 |
| JSONL 解析改增量 | 当前全文件 parse 是响应速度和 CPU 的主要长期风险 |
| overlay IPC 要 push 化 | 160ms 文件轮询与“无事件无工作”的目标冲突 |
| 错误 HUD 只在 DEBUG 默认显示 | 普通用户不被内部错误打扰，开发者能立即看到真实失败 |

## 验收命令
初期每个阶段至少跑：
```powershell
python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q
python -m compileall -q src tests tools
git diff --check
```

性能阶段额外需要：
```powershell
python tools/measure_renderer_latency.py
```

`tools/measure_renderer_latency.py` 已创建；本机基线输出见 `renderer_latency_baseline.json` / `renderer_latency_baseline.md`。

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| `renderer_fallback.log` 未生成 | 1 | RED 测试发现 runtime error diagnostic 尚未接入 normal mode；为 `RuntimeErrorRegistry` 增加 diagnostic callback，并修复 `_append_renderer_diagnostic` 对 dict 字段的过滤逻辑 |

## 下一步
1. 进入阶段 5：收口 macOS recursive sessions tree watcher 与 fallback polling degraded 标记。
2. 继续按 `docs/FALLBACK_INVENTORY.md` 逐项删除或隔离 fallback（下一重点：overlay command polling / desktop overlay state polling）。
3. 使用 `renderer_layout_changed` 事件驱动 renderer payload 拆分（阶段 6），让布局变化只更新局部 DOM。
4. app-server 只保留为未来显式权威源候选；除非协议出现当前窗口 active thread 字段/通知并经过 POC 验证，不接入默认 active-session 路径。
