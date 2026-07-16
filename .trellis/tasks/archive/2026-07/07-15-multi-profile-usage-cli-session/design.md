# 多 Provider 用量统计与 CLI 会话交互设计

## Scope and delivery shape

这是一个复杂但紧耦合的单任务：provider 注册表、配置迁移、会话归因和 renderer 展示共享同一 `model_provider` 合同；拆成子任务会让每项都依赖未落地的数据模型和迁移层，不能独立交付。实施时仍按以下可验证切片推进：核心模型与迁移、聚合与计费、renderer 设置与会话交互、端到端回归。

## Boundaries

- `session_meta.payload.model_provider` 是会话计费、筛选和聚合的唯一稳定通道键；空值归为内部规范键 `unknown`，显示名为“未知通道”。
- Codex profile 是注册表的展示别名来源，不进入历史归因和配置主键。多个 profile 解析到同一 provider 时合并。
- 客户端类型由解析层综合 `originator` 与 `source` 计算（包括子代理的父客户端），输出 `app`、`cli` 或 `unknown`；任何 UI 不直接猜测原始 `source`。
- renderer 是唯一产品 UI。Qt/Tk 不增加相应功能；遗留调用只做迁移/兼容维护。

## Data model and migration

`UserConfig` 新增 provider 维度的嵌套设置（名称可按现有命名风格最终确定）：

- `provider_settings[provider]`：该 provider 的 `model_prices`、`pricing_url` 和 `weekly_adjustment_usd`。
- `provider_scope`：`all` 或 `custom`；`custom` 时保存 `selected_providers`。有效范围始终额外并入当前 App 必选 provider。

读取配置时建立 provider 注册表的并集：基础 Codex 配置、profile 引用、已保存 provider 设置、近 30 天会话中出现的 provider，再附加 `unknown`（仅实际存在缺失记录时）。注册表条目保留 profile 别名和“历史通道”状态。

旧配置只在读入时迁移为内存中的新模型，并在下一次正常保存时持久化：无 provider 的价格与全局 `pricing_url` 复制到迁移时注册表中所有已发现 provider；带 provider 的价格仅进入匹配通道；全局 `weekly_adjustment_usd` 仅进入 App 必选 provider。迁移后新增 provider 使用内置默认价格。旧字段保留读取兼容，写出新字段后不再作为运行时权威。

## Runtime flow

```text
Codex config + profiles + recent JSONL ──> ProviderRegistry
session JSONL ──> ParsedSession(provider, client_kind)
ProviderRegistry + UserConfig ──> effective provider scope
ParsedSession + provider-specific prices ──> cost/summary contributions
effective scope ──> renderer payload, bubbles, budgets, alerts, settings
```

`ParsedSession` 在首次 `session_meta` 时保留 provider 和客户端分类。`CostEstimator` 的每次调用将该 provider（以及已有的 base URL，如可得）传入 `UsageCalculator`，让现有 provider-aware 匹配生效。

`UsageSummaryCache` 保留“每 JSONL 文件增量贡献”的缓存职责，不把用户范围塞入缓存键或改变全量缓存语义；在贡献替换后的汇总边界统一以有效 provider 范围过滤，保证气泡、token、费用、预算、提醒使用同一谓词且不重复扫描文件。

App 必选 provider 先使用基础配置默认值。活动 App 会话的真实 provider 到达后，替换为唯一必选 provider；旧回退 provider 仅在其原本已被选中时继续保留。CLI provider 永不成为 App 必选 provider。

## Renderer behavior

- 设置页以 provider 切换控件显示价格、URL 和补充额度，展示关联 profile；历史来源的条目明确标记。
- 范围控件只有“全选/自定义”两种持久化语义。App provider 显示 `Codex App · 必选`、保持勾选且不可取消。
- CLI 气泡显示 `CLI · <provider>`，工作目录为普通文本；不绑定 `activateSession`、不显示悬停提示、不尝试终端定位。App 气泡保留现有跳转。
- 设置或会话文件事件仅触发既有事件驱动快照刷新；不引入范围轮询或全会话定时扫描。

## Compatibility, rollback, and risks

- Windows/macOS 都通过相同 JSONL 与 Codex config 解析层工作，平台差异只留在既有会话路径/renderer 桥。
- 配置写入必须原子化，保留未知顶层字段。迁移测试覆盖旧配置、部分 provider 配置、共享 provider profile 和失败的价格拉取。
- 任何配置解析失败回退到现有全局行为与内置价格，记录可诊断错误，不删除用户旧数据。
- 回滚点是新 provider 配置字段及 renderer payload；仍可读取旧字段，因此代码回滚不会使旧设置不可读。
