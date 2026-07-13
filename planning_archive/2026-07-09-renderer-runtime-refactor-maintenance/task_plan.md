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
阶段 1、阶段 2、阶段 3、阶段 4、阶段 5、阶段 6、阶段 7、阶段 8 完成。阶段 8 已进一步隔离 Qt/Tk 主 HUD 入口：legacy session functions 只返回 renderer-unavailable，不再获取 HUD 单例锁或启动 runtime；renderer mode 始终挂起 native active-title 并关闭 background watcher，旧 `--legacy-active-session-diagnostics` 只作为兼容 no-op；`docs/RENDERER_MODE_STRATEGY.md` 已改成架构约束文档，`docs/HUD_RUNTIME_ACCEPTANCE_CHECKLIST.md` 固化性能回归和 DEBUG 错误 HUD 验收。

新增任务进入阶段 9：**Exact Payload 审计 Viewer**。本阶段以“按用户要求新增 detached PySide6 主查看窗，同时保持现有顶部/底部 HUD 完全不变”为硬约束，当前状态为 `planned`。

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
- [x] macOS recursive sessions tree 不再落到高成本全树 polling；评估 FSEvents 或显式只 watch 当前 session + session index。
- [x] fallback polling 默认只用于开发诊断，并在 HUD 错误面板标记为 degraded。
- [x] 文件事件 debounce 从固定 0.75s 改为按事件类型分层：当前 session append 快、全树变更慢。
- **状态：** complete

### 阶段 6：renderer payload 与 DOM 更新收敛
- [x] payload 拆分为 current-session、budget、settings、overlay、diagnostics。
- [x] JS 端按局部 payload 更新对应 DOM，避免每次重刷 top/bottom 所有字段。
- [x] CDP target discovery 改为长连接/订阅式状态，连接断开直接错误 HUD。
- [x] settings command 从 localStorage polling 改为 settings bridge callback/runtime event。
- **状态：** complete

### 阶段 7：桌面气泡 IPC 重构
- [x] 用 push IPC 或 watcher 唤醒替代 PySide helper 160ms state file polling。
- [x] 主进程只在 `active_work_items` 变化或 keepalive 必要时发送状态。
- [x] helper 错误通过统一 `runtime_error` 回传，而不是只写日志。
- [x] 气泡点击命令不再靠 60ms command poll；改为事件唤醒。
- **状态：** complete

### 阶段 8：删除 legacy fallback 与收口文档
- [x] 删除或隔离 Qt/Tk 主 HUD残余入口。
- [x] 删除 renderer mode 下不再允许的 native active-title fallback。
- [x] 更新 `docs/RENDERER_MODE_STRATEGY.md`，把“愿景”改成“架构约束”。
- [x] 增加 `docs/HUD_RUNTIME_REFACTOR_PLAN.md` 到长期路线文档。
- [x] 增加性能回归测试和 DEBUG 错误 HUD 验收 checklist。
- **状态：** complete

## 增量任务：Exact Payload 审计 Viewer（2026-07-09）

### 任务目标
为当前会话新增“精确 payload 审计”能力，让用户看到每轮**真实发出的请求内容**与**真实收到的响应内容**，而不仅是 token 数字。

本轮收敛方案：
- 以 **方案 B（会话地图）** 作为主导航骨架。
- 吸收 **方案 C（最终态 / 完整时间序列双层回放）** 作为详情展示方式。
- Detached PySide6 Viewer 作为本功能主查看面。

### 硬约束
- **顶部 HUD 与底部 HUD 的现有内容、文案、布局、尺寸、视觉完全不变。**
- 现有 HUD 只增加交互热区，不新增按钮、不改文案、不改层级。
- 允许为该功能新增 detached PySide6 主查看窗；这是用户对默认 renderer-first 产品面的显式覆盖。
- 审计数据只保留**当前会话**，不做跨会话历史库。
- 审计默认**全程开启**、**不脱敏**。
- 详情默认显示“最终请求 + 最终回复”，并支持展开到“完整时间序列”。

### 成功标准
| 维度 | 目标 |
|------|------|
| 内容精度 | 对已捕获轮次，Viewer 中展示真实 request/response payload，而不是 JSONL 摘要替身 |
| HUD 稳定性 | 顶部/底部 HUD 的现有 UI 零变化；只新增打开 Viewer 的交互 |
| 会话追溯 | 支持当前会话内的任务首轮、Top10 高消耗轮次、全部历史轮次跳转 |
| 详情层级 | 默认最终态；可展开同一轮内 request / delta / tool call / tool output / final response |
| 存储边界 | 仅当前会话 sidecar 持久化；切换会话后不保留旧会话完整 payload |
| 平台 | PySide6 Viewer 代码按 Win/mac 通用行为实现与验证 |

### 阶段 9：精确 payload 捕获链路
- [ ] 设计新的 renderer/CDP 审计捕获链路，明确“精确 payload”优先于 JSONL/SSE 摘要。
- [ ] 在 renderer 注入脚本中增加请求拦截点，捕获真实 outbound request body。
- [ ] 在 renderer 注入脚本中增加响应捕获点，记录 final response，并为后续完整时间序列保留 stream/tool event。
- [ ] 新增专用 CDP binding，把 audit event 从 renderer 推回 Python。
- [ ] 对未捕获成功的轮次保留索引，但显式标记 `payload_missing`，不得伪造正文。
- **状态：** pending

### 阶段 10：当前会话审计持久化与索引
- [ ] 在 `hud_runtime_dir()` 下新增当前会话 audit sidecar 方案，按 `session_id` 持久化。
- [ ] 定义 audit event schema，至少包含：
  - [ ] `session_id`
  - [ ] `task_index`
  - [ ] `round_index`
  - [ ] `event_type`
  - [ ] `timestamp`
  - [ ] `request/response correlation id`
  - [ ] `payload body / stream chunk / tool event`
- [ ] 新增“任务首轮”显式索引，确保每次新需求都能定位到对应首轮。
- [ ] 新增会话级 heavy rounds 排名，支持 Top3（HUD 原入口）、Top10（Viewer 导航）和全部历史轮次。
- [ ] 会话切换时清理非当前会话 sidecar，确保 retention 仍为“仅当前会话”。
- **状态：** pending

### 阶段 11：HUD 入口联动与 Detached Viewer
- [ ] 保持顶部 HUD 现有 Top3 重轮次展示不变，仅为现有节点增加打开 Viewer 的交互。
- [ ] 保持顶部活动轨迹展示不变，仅为现有任务相关节点增加打开 Viewer 的交互。
- [ ] 保持底部轮次流水展示不变，仅为现有轮次行增加打开 Viewer 的交互。
- [ ] 新增 detached PySide6 Viewer 主窗：
  - [ ] 默认打开到最后一轮已确认轮次。
  - [ ] 支持上一轮 / 下一轮。
  - [ ] 支持从 Top3 / 任务首轮 / 全部历史 / Top10 锚点进入。
  - [ ] 左侧导航展示任务首轮、Top10、全部历史轮次。
  - [ ] 主体默认显示最终请求 / 最终回复。
  - [ ] 提供“展开完整时间序列”视图。
- [ ] 明确 Viewer 是新功能主界面，HUD 只承担摘要与入口职责。
- **状态：** pending

### 阶段 12：验证与交付
- [ ] 为 audit capture / sidecar / 索引 / Viewer 导航补自动化测试。
- [ ] 为“HUD UI 零变化”补回归测试，重点覆盖：
  - [ ] 顶部 heavy rounds 文案与布局不变
  - [ ] 顶部 activity trail 文案与布局不变
  - [ ] 底部 request rows 文案与布局不变
- [ ] 为最终态 / 完整时间序列双层视图补一致性测试。
- [ ] 为 Win/mac Viewer 生命周期、复制、导航、锚点跳转补验证。
- [ ] 更新实现文档与验收 runbook，新增 detached Viewer 的启动、跳转和数据边界说明。
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
| `git diff --check` 因 `renderer_latency_baseline.json/.md` 生成 CRLF 报 trailing whitespace | 1 | `tools/measure_renderer_latency.py` 改为显式 `open(..., newline="\n")` 写出 JSON/Markdown，并重生基线文件 |

## 下一步
1. 进入阶段 9，先验证“精确 payload”在现有 renderer/CDP 链路里的可捕获性与稳定字段边界。
2. 明确 audit sidecar schema 与当前会话清理策略，避免一开始就引入跨会话历史库。
3. 在不改 HUD 现有 UI 的前提下，为 Top3、活动轨迹节点、底部轮次行补打开 Viewer 的锚点交互。
4. 以“方案 B 主骨架 + 方案 C 双层回放”落地 detached PySide6 Viewer。
5. 为“HUD UI 零变化”补回归测试，把这条用户约束变成自动化保护。
