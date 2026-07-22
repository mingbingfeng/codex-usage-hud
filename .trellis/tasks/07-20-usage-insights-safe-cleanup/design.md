# 空间清理与会话永久删除设计

## 1. 边界与模块

本任务保留 `CodexFileManager` 作为 Codex 私有目录 inventory 与 raw 临时项删除的安全基座，使用 `core/safe_cleanup.py` 作为面向用户的垃圾清理编排层，并新增独立 `core/session_cleanup.py` 负责会话 inventory 与官方删除命令编排。会话永久删除不混入普通路径清理，也不放宽原管理器对未知 Codex 数据的保护：

1. `CodexFileManager` 的临时 staging/clone 候选及受保护空间摘要。
2. HUD runtime 根内固定白名单日志/残留，以及 Windows/macOS 固定白名单系统缓存。
3. `logs_2.sqlite` 与 `background-usage.sqlite3` 的离线行级维护计划。
4. `state_5.sqlite`、session index、rollout 和 spawn tree 的只读会话投影；实际删除委托给 `codex delete --force`。

`safe_cleanup.py` 与 `session_cleanup.py` 都不导入 `cli.py`。进程停止、HUD 退出、恢复启动、当前会话和活动 work IDs 通过构造函数回调及维护计划字段注入，避免核心层反向依赖 UI/runtime。

## 2. 数据合同

### 2.1 Usage insights（已迁移）

扩展 `UsageSummaryCache` 的 `_UsageCacheEntry`，保存同一批已解析 records 生成的：

- canonical session id / JSONL 相对标识；
- day/week `UsageSummary`；
- day/week 按 model 聚合；
- `model_provider`；
- 最新事件时间。

新增只读 `insights(...)`，只遍历缓存 entry，不触发目录扫描。CLI 在完整预算聚合或显式“刷新洞察”后生成：

```text
usageInsights
  revision, generatedAt
  today/week totals
  sessions[]: id, title, provider, tokens, costUsd, cacheRatio, actionable
  models[]: model, provider, tokens, costUsd, cacheRatio
  providers[]: provider, tokens, costUsd, cacheRatio
  background: requestCount, totalTokens, estimatedCostUsd, pendingCount
  costCoverage: pricedEventCount, totalEventCount, hasCompleteCost
```

`usageInsights` 及其排行继续由既有“用量总览”消费，不再作为空间清理页的一级内容。该 domain 的事件驱动、隐私和聚合合同保持不变。

会话动作复用既有 `SessionSwitchController.activate_session(...)`；后台动作切换到既有 `backgroundUsage` Tab。无法证明 canonical id 或属于 `archived_sessions` 的条目不显示会话跳转动作。后台摘要单独展示，不与会话统计相加；费用 coverage 不完整时 UI 显示“部分可估算”，而不是 `$0` 或完整总额。

正常启动的预算聚合会使 cache 变热；如果显式打开 Tab 时 cache 仍冷，不允许在 renderer loop 同步递归扫描，改由一次性 worker 完成并通过 domain 事件返回。

### 2.2 Cleanup inventory

`SafeCleanupManager` 返回与现有 renderer domain 相容但语义更明确的 payload：

```text
safeCleanup
  revision, generatedAt, platform
  totals: reclaimableBytes, consentBytes, protectedBytes, backupBytes
  groups[]: id, category, tier, bytes, items, impact, retention, requiresOffline,
            blockedReason, relatedProcesses
  operation: id, requestId, action, state, progress, confirmationToken,
             selectedIds, results[], estimatedBytes, actualBytes, backupPath
```

每个可执行对象的绝对路径、lstat、指纹、approved root 和具体动作只保存在 Python 内部 inventory；renderer 只收到 opaque ID 和中性标签。tier 为 `safe`、`consent`、`protected`。

### 2.3 Session cleanup inventory

新增 `SessionCleanupManager`，只读打开 Codex state DB 并结合 session index/rollout 存在性构建主会话 inventory：

```text
sessionCleanup
  revision, generatedAt
  capability: available, command, reason
  totals: selectable, blocked, bytes, descendants
  sessions[]: id, title, workdirName, updatedAt, status, bytes,
              descendantCount, selectable, blockedReason
  operation: requestId, action, state, confirmationToken,
             selectedIds, sessionCount, descendantCount, results[]
```

canonical UUID、rollout 路径和 spawn tree 只保存在 Python inventory。`scan()` 生成新 revision 并失效旧 token；`preview()` 只接受可选 opaque IDs 并签发短期单次 token；`execute()` 逐项解析 UUID 后调用 `codex delete --force`，每项完成后重新查询 DB/index/rollout 核验。

### 2.4 清理来源

- Codex 临时项：从 `CodexFileManager` candidate 转换，不复制其删除实现。
- HUD 日志：runtime 根内精确名称 `crash.log`、`renderer_fallback.log`、`daemon.log*`、`window_tracker.log*`、`hud_geometry.log*`、`work-overlay-transitions.jsonl`、非活动 `work-overlay-*-commands.jsonl` 和明确失效 loading/overlay 残留。
- 旧备份：只识别 `logs_2.sqlite.pre-cleanup-*` 及 HUD 自己生成且超过 7 天的 backup 命名；不扫描任意用户备份目录中的未知文件。
- 系统缓存：平台适配器返回 approved root + 相关进程 + 清理策略。`CacheDefinition` 同时声明 `safe` 或 `consent` tier；用户 temp 与诊断目录只产生超过各自保留期且树内无较新文件的顶层候选，其他可再生成缓存以精确缓存根为单位。Windows 当前用户 DirectX/显卡着色器缓存以及 macOS Homebrew/Xcode 缓存属于 `safe`；超过 7 天的 Windows/macOS 崩溃与错误报告属于 `consent`。
- SQLite：inventory 只记录 schema/时间列审计和可删行/bytes 估算，不把表内容带入 payload。

## 3. 扫描、预览与确认

扫描只由打开 Tab 后点击或显式刷新触发，并在单 worker 中运行。流程：

1. 解析 approved roots，拒绝 reparse root。
2. 调用 Codex inventory，扫描 HUD/system 白名单，审计 SQLite schema 和 cutoff 以前行数。
3. 为每个执行单元保存 opaque ID、canonical path、lstat/fingerprint、owner gate 和动作。
4. 发布新 revision；旧 confirmation 全部失效。
5. scan worker 立即对默认 safe 选择调用 preview，计算预计删除、备份成本、净同卷收益和需要关闭的进程，签发短 TTL 单次 token；renderer 不再暴露独立预览按钮。
6. execute 消费 token 并再次校验选择；活动任务门禁在任何进程停止动作之前执行。

`safe` 默认选择；`consent` 必须由独立布尔字段和二次确认解锁。未知/受保护项没有可执行 ID，不能通过篡改 renderer command 选择。

修改深度清理选择、保留期或备份目录时，renderer 发起静默 preview 请求并在返回前禁用“确认清理”。默认 safe-only token 可直接执行，不再增加第三层通用确认；包含深度 SQLite 维护时继续显示不可恢复历史和关闭/恢复影响的强确认。

会话删除使用完全独立的 scan/preview/execute 状态机。最终确认显示主会话与关联子任务数量；当前/运行中/未解析项在 Python inventory 层没有执行权限，篡改 renderer payload 也会使整次请求失败。

## 4. 离线维护子进程

新增隐藏 CLI 参数 `--cleanup-maintenance-helper --cleanup-plan-file <path>`，启动命令沿用 frozen/source 双路径 helper 模式。计划文件位于 HUD runtime 根，使用原子写入，包含：

- plan id、父 HUD PID、待退出目标 PID、创建/过期时间；
- 仅 Python 端签发的动作、canonical path、指纹/schema/cutoff；
- 备份策略与 canonical 备份根；
- 启动前 Codex/HUD 状态及恢复命令；
- result path。

执行顺序：

1. 主进程确认无活动任务并完成后台用量增量归档。
2. 主进程检测到独立 Codex CLI 时拒绝离线 SQLite 项；否则请求 Codex App 正常退出。超时或无法证明退出则取消，HUD 继续运行。
3. 启动维护 helper，随后关闭 background runtime、file manager 和 HUD。
4. helper 等待父 PID/目标 PID 消失并确认 DB/路径未锁；超时则不改数据。
5. 先处理不依赖 SQLite 的白名单路径，再执行强制 SQLite backup、integrity check、DELETE、checkpoint/VACUUM、二次 integrity/关键查询。
6. SQLite 失败时从本次 backup 恢复；每个动作独立记录 deleted/skipped/failed/restored。
7. 原子写 result，删除已消费 plan 中的敏感绝对路径字段或删除 plan。
8. 按启动前状态恢复 Codex App 与 HUD。daemon 模式使用专用 maintenance 退出码让 daemon 进程彻底退出，helper 完成后再恢复 daemon 启动命令；非 daemon 模式恢复原 HUD 启动命令。

helper 不主动强杀仍存活进程、不绕过锁、不执行过期 plan。恢复启动失败不改变清理结果，但必须写入单独状态供下次 HUD 展示。第一版不记录或重放独立 CLI 终端现场；发现这类进程即 fail closed。

## 5. SQLite 事务与回滚

### logs_2.sqlite

- 先验证已知表、时间字段及关键索引；未知 schema 整库跳过。
- cutoff 为执行时刻减 24 小时。
- 使用参数化 DELETE，只删除已识别日志表的旧行。
- 估算收益达到阈值后才 checkpoint/VACUUM；主库文件本身永不删除。

### background-usage.sqlite3

- 先关闭 `BackgroundUsageRuntime` 并等待 idle。
- cutoff 为执行时刻减 30 天。
- 按现有外键/事件关系顺序删除已识别历史，保留 schema/version；主库文件本身永不删除。

备份使用 `sqlite3.Connection.backup()`，完成后对目标执行 `PRAGMA integrity_check`。源库修改在显式事务中完成；VACUUM 在事务提交后执行。修改后验证失败时关闭所有连接，将损坏源库移到本次 plan 的隔离名，再原子恢复 backup。没有成功 backup 时绝不进入 DELETE。

## 6. 平台适配器

`CleanupPlatformAdapter` 提供：白名单 cache definitions、相关进程检测、Codex App/CLI 退出请求、PID 存活检查、同卷判断和恢复启动元数据。

- Windows 复用 Codex desktop process listener/启动逻辑，并额外识别独立 CLI `codex.exe` 作为阻断条件；系统缓存路径来自 `%TEMP%`、`%LOCALAPPDATA%`、`%APPDATA%` 和用户 NuGet 根，包括精确的 D3D/显卡着色器缓存。旧诊断只扫描当前用户 `CrashDumps` 与 WER Archive/Queue 的过期顶层项。
- macOS 使用 `~/Library/Caches`、`~/Library/Application Support` 内精确应用 cache 路径、`~/.npm`/`~/.cache/pip`/`~/.nuget`，并覆盖 Homebrew cache、Xcode DerivedData 与用户 `Library/Logs` 下精确诊断根；Codex App 使用精确 PID + `osascript quit`/`open`，独立 CLI PID 作为阻断条件。

回收站/废纸篓不进入普通路径白名单：其内容具备用户恢复语义，Windows 还需要 Shell API 才能保证只处理当前用户对象，后续必须以独立 consent action 设计。

所有 adapter 失败均 fail closed。Linux 不承诺产品支持；返回 unsupported/protected，不猜测路径。

## 7. Renderer 交互

一级 Tab 文案改为“空间清理”，内部可保留旧 `storage` state key 以兼容本地 UI 状态。页内使用“垃圾清理 / 会话管理”分段：

1. 垃圾清理扫描前只显示一个主动作“扫描垃圾”。
2. 扫描后直接显示默认 safe preview、受保护摘要和折叠的“深度清理”；底部只保留次要“重新扫描”和主动作“确认清理”。
3. 备份目录、保留期和自动关闭/恢复影响只在深度清理展开且相关项选中时出现。
4. 会话管理提供搜索、状态/时间筛选、主会话整行列表和批量选择；子 agent 只显示为数量。
5. 永久删除最终确认使用红色危险动作，并明确不可恢复；CLI capability 缺失或会话受保护时禁用选择/动作。

不新增嵌套卡片、营销说明或巨型标题。按钮使用现有设置动作样式；熟悉动作优先使用现有 icon/语义。任何动态值都有固定/受限布局，长路径仅显示中性尾部标签并可换行。

## 8. 兼容、回滚与隐私

- 旧 `fileManagement` domain 在迁移期间保留给底层 Codex inventory 测试；新 UI 消费 `safeCleanup` 与 `sessionCleanup`，不再展示旧的全量文件列表。
- 任一新 domain 缺失时，空间清理显示对应的“尚未扫描/不可用”状态，其他 HUD domain 不受影响。
- 回滚代码即可恢复旧 Tab；维护 plan 有版本字段，未知版本 helper 拒绝执行。
- 更新 `docs/PRIVACY.md`，明确所有备份和修改均为用户显式触发、本地执行、绝不上传。

## 9. 关键风险

- 错删或 TOCTOU：沿用 revision/opaque ID/path boundary/lstat/fingerprint/lock 并在 helper 内二次重验。
- SQLite schema 漂移：严格 schema allowlist；不匹配即跳过整库。
- 关闭时仍有任务：同时检查当前 snapshot、active work 和待完成 background scan；任何一项活跃即拒绝。
- helper 启动后主进程未退出：超时不改数据。
- 备份占满同一磁盘：preview 单列 backup bytes，并在创建前检查可用空间；不足则拒绝。
- daemon 与 helper 争抢恢复：新增专用 terminal exit code 让 daemon 完全退出并释放日志；helper 完成后只恢复一个 daemon/HUD 启动形态。
- 独立 CLI 无法无损恢复：将其作为离线维护阻断条件，不强杀、不伪造 detached “恢复”。
- 费用覆盖误报：缓存 entry 同步保存 priced/total event count；不完整费用不得作为可比较总额排序依据而不带标记。
- 页面局部更新丢状态：`safeCleanup` 与 `sessionCleanup` 更新保留页内分段、筛选、滚动与已输入备份目录，不把整个 modal 重置为初始值。
- 官方删除能力变化：每次会话 scan 重新探测；不支持 `delete --force` 时只读展示，不走 raw fallback。
- 批量删除部分失败：逐个串行执行并逐项核验，已成功项不回滚，失败/未执行项明确保留并可重新扫描。
