# 多 Profile 用量统计与 CLI 会话交互

## Goal

让 HUD 在同时捕获 Codex App 与 Codex CLI 会话时，能够按 `model_provider` 正确归因模型价格、token 与费用；profile 仅用于发现和展示。用户可选择合并统计或仅查看指定 provider 范围，同时避免把 CLI 会话错误地当作 Codex App 会话执行工作目录跳转。

## Background

- 用户的 Codex App 使用默认 profile，`model_provider = "custom"`。
- 用户的 Codex CLI 使用 `muyuan` profile。
- 当前 PySide 会话气泡能够捕获并显示 CLI 会话，且 CLI token / 费用会进入 HUD 统计。
- 当前设置中的模型单价与统计范围没有 profile / provider 维度。
- 当前点击会话气泡中的工作目录，默认按 Codex App 会话处理；CLI 会话应改为打开对应终端窗口，若无法可靠定位则允许第一阶段不提供跳转。

## Confirmed Facts

- 当前机器的真实 JSONL `session_meta.payload` 已包含 `source`、`originator`、`model_provider`：CLI 样本为 `source=cli`、`originator=codex-tui`、`model_provider=muyuan`；Codex App 样本为 `source=vscode`、`originator=Codex Desktop`、`model_provider=custom`。
- `session_meta` 不包含所选 profile 名称；当多个 profile 指向同一 `model_provider` 时，仅靠现有历史 JSONL 无法区分具体 profile。
- `ParsedSession` 当前只从 `session_meta` 提取会话 ID、时间和 cwd，没有保留 client source、originator 或 model provider。
- 当前 `ModelPrice` 已支持可选 `provider` / `base_url`，`UsageCalculator` 也有按 provider / base URL 匹配价格的能力；但 `CostEstimator` 调用时没有传入这两个上下文，现有会话计价未完整利用该能力。
- 当前 `UserConfig.model_prices` 和 `pricing_url` 都是全局单份配置；renderer 设置页显示单张价格表，仅提供高级的渠道与 Base URL 列。
- `UsageSummaryCache` 当前扫描 `sessions` 与 `archived_sessions` 下全部 JSONL 后直接合并，缓存键不含任何统计范围过滤条件。
- PySide 工作气泡当前把 `source` 用作选择/活动来源标签，而不是 App/CLI 客户端类型；所有点击统一写入 `activateSession` 命令并进入 Codex App 会话切换控制器。
- 对本机 `sessions` 与 `archived_sessions` 的 973 个真实 JSONL 做了只读抽样汇总，全部 `session_meta` 都有 `model_provider`；当前数据中不存在缺失 provider 的历史记录。
- `session_meta.payload.source` 不是稳定字符串枚举：顶层会话可为 `cli`、`vscode`、`exec`，子代理则是包含 `subagent.thread_spawn` 的对象；`originator` 能区分 `Codex Desktop` 与 `codex-tui`，因此 App/CLI 客户端分类必须由一个共享解析器综合 `originator` 与 `source` 得出，不能由气泡 UI 自行判断。

## Requirements

- R1. HUD 必须以 JSONL `session_meta.payload.model_provider` 作为稳定的计费通道主键；profile 仅用于发现、命名和展示，不作为历史用量归因主键。
- R2. 模型单价配置必须按 model provider 隔离，设置页在单价列表上方提供通道切换入口，并可附带展示关联的 profile 名称。
- R3. 用户必须能通过一个全局 provider 范围配置勾选一个或多个 provider；单选是勾选集合大小为 1 的一致行为，不再额外维护一套“单选/多选模式”。
- R4. provider 范围必须统一控制展示与统计：未勾选 provider 的会话气泡不展示，其 token、费用、预算进度、超额提醒和其他派生汇总也不计入；已勾选 provider 的数据正常展示并合并统计。
- R5. CLI 会话必须带有区别于 Codex App 会话的来源类型；点击 CLI 会话工作目录时，不得触发 Codex App 内部跳转。
- R6. 若无法可靠找到 CLI 所属终端窗口，CLI 工作目录跳转可以暂不实现，但界面行为必须明确且不能误跳。
- R7. 兼容 Windows 与 macOS，并保持 renderer 模式为产品主路径；不得向 Qt/Tk 旧 HUD 增加新产品行为。PySide 会话气泡若属于当前辅助壳层，只改必要的会话来源与交互逻辑。
- R8. 现有单 profile 用户升级后应保持原有统计结果和使用体验，配置迁移不得丢失已有自定义单价。
- R9. 多个 profile 指向同一 model provider 时必须合并为一个计费通道，共用一套模型单价且不得重复统计。
- R10. CLI 产生的会话气泡必须有明确、可辨识的 CLI 来源标记，并同时展示其 provider，避免与 Codex App 会话混淆。
- R11. provider 范围配置支持“全选”和“自定义范围”两种持久化语义：旧用户升级默认全选；全选状态下以后发现的新 provider 自动纳入；用户取消任一 provider 后进入自定义范围，之后新发现的 provider 默认不勾选。
- R12. 设置页 provider 列表取以下来源的并集：当前 Codex 配置、profile 引用、HUD 已保存单价，以及最近一个月会话历史中实际出现的 provider。仅由历史补充且已不在当前配置中的项目标记为“历史通道”，仍允许勾选和配置单价。
- R13. “最近一个月”只限制从会话历史发现 provider 的扫描窗口，不改变现有日/周用量与预算统计窗口，也不限制当前配置/profile/已保存价格中的 provider。
- R14. 旧全局价格迁移到按 provider 分组的配置时：无 provider 的旧价格复制给迁移时已发现的每个 provider；显式带 provider 的旧价格只进入对应通道；迁移后新发现的 provider 使用内置默认价格模板，不继承任一用户自定义通道价格。
- R15. `pricing_url` 按 provider 独立保存；设置页“拉取”只更新当前 provider 页签。旧全局 URL 迁移时复制给已有 provider，单个 provider 拉取失败不得修改其他通道。
- R16. Codex App 当前使用的 provider 是统计范围中的强制成员：设置页保持勾选且不可取消，并标记为 `Codex App · 必选`。用户只能选择是否叠加其他 provider，不能把产品基线 App 通道排除。
- R17. App provider 应优先由当前 Codex App 会话的 `session_meta.model_provider` 确认，并用基础 Codex 配置的默认 `model_provider` 作为启动/无会话回退；profile 专用 provider 不得误判为 App 必选通道。
- R18. `weekly_adjustment_usd` 按 provider 保存并只随已纳入范围的 provider 汇总；旧全局补充额度仅迁移到 Codex App 必选 provider，不复制到其他通道。日/周预算上限保持全局一套，作用于当前范围的费用合计。
- R19. 无法读取 `model_provider` 的历史会话必须归入可见的“未知通道”，不得猜测归属或静默丢弃；它在全选范围中默认纳入，用户进入自定义范围后可自行取消。
- R20. 真实 Codex App 会话的 provider 与基础配置默认值不一致时，真实会话 provider 必须立即成为唯一必选通道；原默认 provider 仅在用户此前已选择时保留。无活动 App 会话时才使用基础配置默认值作为必选回退。

## Acceptance Criteria

- [ ] AC1. 同时存在默认 profile 与 `muyuan` profile 会话时，每条用量记录可被稳定归入正确 `model_provider`，不能仅凭当前全局配置猜测；多个 profile 共享 provider 时归入同一通道。
- [ ] AC2. 用户可在设置页分别查看并编辑每个 provider 通道的模型单价，切换 provider 不串值；关联 profile 仅作辅助展示。
- [ ] AC3. 用户可通过同一个配置勾选一个或多个 provider；单选是多选集合大小为 1 的一致行为。
- [ ] AC4. provider 范围变化后，HUD 会同步刷新会话气泡、token、费用、预算进度、提醒与其他派生汇总；未勾选 provider 不展示、不计入且不会重复计数。
- [ ] AC5. 旧配置升级后自动映射到合理的 provider 通道，已有单价和默认统计结果保持兼容。
- [ ] AC6. 点击 Codex App 会话工作目录仍执行原有 App 跳转；CLI 会话工作目录为无悬停提示的普通文本，不触发 App 跳转或任何终端猜测逻辑。
- [ ] AC7. profile 被重命名、删除、配置缺失或会话无法归因时，有确定的回退/展示策略，不导致统计记录静默丢失。
- [ ] AC8. Windows 与 macOS 的配置发现、会话识别和 renderer 数据流均有对应验证；不新增 Qt/Tk 产品功能。
- [ ] AC9. 两个 profile 指向同一 provider 时，设置页只产生一个计费通道，相关会话只按 provider 汇总一次。
- [ ] AC10. `source=cli` / `originator=codex-tui` 的会话气泡带有 CLI 标记和 provider 名称；Codex App 气泡不被误标为 CLI。
- [ ] AC11. 旧用户升级后统计结果不因范围配置迁移而减少；全选状态自动包含新 provider，自定义范围不自动扩大。
- [ ] AC12. provider 列表包含当前配置、profile、已保存价格以及最近一个月历史会话的并集；仅历史存在的 provider 有清晰的“历史通道”状态。
- [ ] AC13. 超过一个月且不再被当前配置/profile/已保存价格引用的 provider 不再因历史扫描单独出现在列表中；日/周汇总窗口行为保持不变。
- [ ] AC14. 旧全局价格迁移后，各已发现 provider 的初始费用计算与升级前一致；后续修改一个 provider 的价格不影响其他 provider，新 provider 不复制其他通道的自定义价格。
- [ ] AC15. 每个 provider 可保存独立价格 URL；拉取当前 provider 价格时其他 provider 的 URL 和价格保持不变，失败时当前已有价格也不被破坏。
- [ ] AC16. Codex App provider 在范围配置中始终勾选且不可取消；任何允许保存范围的路径都会强制保留它，其他 provider 可自由勾选。
- [ ] AC17. Codex App 尚无活动会话时可由基础配置确认必选 provider；App 会话出现后以真实会话 provider 校正，CLI profile provider 不会因此变成必选。
- [ ] AC18. 切换 provider 范围时补充额度只包含所选通道；旧补充额度迁移后总金额不增加，预算上限继续对筛选后的合计生效。
- [ ] AC19. 缺少 `model_provider` 的历史会话显示并统计在“未知通道”；全选状态包含它，且不会被错误归入 Codex App 必选通道或任一 CLI provider。
- [ ] AC20. App 会话 provider 出现后会校正必选通道；基础配置 provider 只在无 App 会话时强制包含，校正不会无故扩大用户的自定义范围。

## Technical Notes

- CLI 气泡使用紧凑标签 `CLI · <provider>`；Codex App 气泡不额外添加 `App` 标签，非默认 provider 可在现有辅助信息区域展示。子代理按解析后的父客户端来源正确标记。
- 本次 MVP 不实现 CLI 终端跳转：CLI 工作目录显示为普通文本，不可点击、不发送 `activateSession`，也不增加悬停提示；Codex App 会话保持原有跳转行为。
- “未知通道”仅用于缺少 `model_provider` 的兼容记录；它不是配置/profile 发现所得的 provider，也不能替代 Codex App 必选通道。
- App 必选 provider 以活跃 App 会话为准，基础配置仅为无活跃 App 会话的启动回退；校正时不自动加入旧回退 provider。

## Out of Scope

- CLI 终端窗口定位、激活或工作目录跳转。
- 将 profile 名称作为历史计费、价格或预算通道的持久化主键。
- Qt/Tk 旧 HUD 的功能扩展；仅保留迁移或必要兼容维护。
- 修改 Codex App、Codex CLI 或其配置格式；HUD 仅读取其现有会话和配置数据。
