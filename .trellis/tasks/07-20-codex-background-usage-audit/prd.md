# Codex App 后台用量审计与气泡提醒

## Goal

让 Codex App 发起、但不属于任何用户可见会话或显式子代理的模型请求在 HUD 中可见、可追溯、可确认，避免用户只看到会话用量而误以为 Provider 多扣费。

## Background

- Codex App 的本地 `logs_2.sqlite` 会记录后台线程、模型、工作目录、Prompt、请求时间、`total_usage_tokens`、`estimated_token_count` 与 `process_uuid`。
- 普通会话存在于 `state_5.sqlite.threads`；显式子代理可由 `thread_spawn_edges.child_thread_id` 识别。已确认的 Context-aware suggestions 与 Memory consolidation 线程既不在可见会话表，也没有子代理父子关系。
- 这些请求不能归入任一会话顶部 HUD；本机日志也没有 Provider 账单所需的精确输入、缓存输入与输出拆分，因此费用只能使用 HUD 价格表估算。
- 产品坚持 local-first：Codex 原始 SQLite 始终只读，历史审计保存在 HUD 自有本地数据库，功能不得联网或访问中转站。

## Requirements

### R1. 后台任务识别

- 仅从本机 Codex SQLite 增量读取候选记录，并把同一后台线程的多次 API 请求聚合为一个后台任务事件。
- 候选线程须经过短暂 grace period，并同时满足：属于本机 Codex App 进程、未出现在 `state_5.sqlite.threads`、未出现在 `thread_spawn_edges.child_thread_id`。
- 可识别的内部功能显示稳定的人类可读名称，包括 Context-aware suggestions、Memory consolidation，以及日志中出现的自动标题/描述类功能；无法识别的签名显示“未知后台任务”，不得静默丢弃。
- 原始数据库缺失、被锁定、字段变化或单行日志异常时不得影响主 HUD；记录本地诊断并等待后续文件事件重试。

### R2. 本地历史与费用说明

- 使用 HUD 自有 SQLite 保存扫描游标、后台任务、逐请求明细、Prompt、模型、工作目录、进程归因、估算 token/费用和确认状态。
- Codex 原始 SQLite 使用只读连接；不得修改、迁移或锁住原始库。
- Tokens 明确标为“本机日志值”；费用明确标为“HUD 估算”，不得称为 Provider 账单或精确扣费。
- 费用使用当前 App provider 对应的 HUD 模型价格表计算并保存价格来源快照。缺少价格时仍保存使用量，费用显示不可用。
- Prompt 仅在用户选中某一事件时按需加载；列表和常规 renderer HUD payload 不携带 Prompt。

### R3. 当天气泡提醒

- 只有本地日期为今天、尚未确认的后台任务可生成气泡；更早历史只进入设置页。
- 复用现有 PySide6 工作气泡 helper，但使用独立的 `background_usage` 类型，不伪装成会话 `recent`，不参与会话切换、完成动画或 shimmer。
- 气泡使用琥珀色矩形，显示功能、模型、请求数、本机日志 tokens 和 HUD 估算费用。
- 关闭按钮显示对勾。点击后由主进程持久化该 `eventId` 的确认状态；同一事件即使后续追加请求也不再弹出。
- 底部热点显示“查看后台用量记录”。点击打开 renderer 设置页“后台用量”Tab 并选中对应事件；查看详情不自动确认。
- `work_overlay_max_items=0` 时不显示气泡，但仍完整记录历史。

### R4. 设置页后台用量 Tab

- 在 renderer 设置弹窗新增“后台用量”Tab，不改变会话顶部 HUD 的用量或费用。
- 页面包含当日费用、当日 tokens、后台任务数、请求数、使用模型摘要；费用处始终显示“估算”。
- 左侧显示后台任务历史，支持日期范围、功能与模型筛选，并区分未确认、已确认和纯历史状态。
- 右侧显示任务归因、线程、进程、工作目录、逐请求时间线、模型、tokens、估算费用与请求内容。
- 从气泡跳转时直接打开该 Tab 并高亮对应事件；普通打开时恢复当前筛选或选择最新事件。
- UI 文案使用“Codex App 后台用量”或“非会话用量”，不得使用“偷偷使用”。

### R5. 事件驱动与跨平台

- 初次扫描按日志自增 ID 定位有限历史窗口，后续只读取新 ID；不得反复扫描约 554 MB 的完整日志库。
- 监听 `logs_2.sqlite`、`-wal`、`-shm` 和会话状态库变化；扫描在后台 worker 执行，主 renderer 循环不得阻塞。
- 原生文件事件可用时不得新增空闲轮询；降级轮询沿用 `FileChangeWatcher` 的保守 fallback 并暴露诊断。
- Python 数据层与 renderer 设置页兼容 Windows 和 macOS；PySide6 helper 缺失时历史审计仍可用。

## Acceptance Criteria

- [x] 合成 SQLite 中，一个不在可见会话/子代理集合内的线程被聚合为一个事件，多条请求明细与累计 tokens 正确。
- [x] 可见会话、显式子代理、未过 grace period 的线程不会生成后台任务事件。
- [x] 已知功能得到稳定分类，未知功能仍以“未知后台任务”进入历史。
- [x] 原始 Codex SQLite 只读，扫描游标使第二次扫描只处理新增日志行。
- [x] 当天未确认事件生成 `background_usage` 气泡；昨日事件不弹；点击对勾后重启 HUD 也不再弹。
- [x] 点击气泡详情热点打开设置页“后台用量”并选中正确 `eventId`，且事件仍保持未确认。
- [x] 设置页可以筛选历史、查看逐请求明细和按需 Prompt；常规 HUD payload 不含 Prompt。
- [x] 所有费用 UI 均标注“HUD 估算”，价格缺失时不伪造金额；会话顶部 HUD 统计保持不变。
- [x] SQLite/WAL 文件事件触发后台增量扫描；无文件事件时无新增扫描或 snapshot 循环。
- [x] PySide6 不可用或源数据库暂时不可读时，renderer HUD 主流程仍正常运行并保留可诊断错误。
- [x] 聚焦测试、完整 `python -m pytest`、`compileall` 与 `git diff --check` 通过；真实本机日志只读验收能识别已知后台线程，且不把敏感 Prompt 写入测试产物。

## Out Of Scope

- 访问 New API、中转站或任何外部 Provider 的账单 API。
- 声称费用与 Provider 扣费完全一致，或反推精确缓存 token。
- 将后台用量并入某个会话、日预算或周预算统计。
- 为已确认事件提供“重新弹出”行为，或自动删除 Codex 原始日志。
