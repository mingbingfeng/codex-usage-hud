# 空间清理与会话永久删除

## Goal

将设置页一级入口收敛为专注处理本地空间的“空间清理”：默认垃圾清理只需“扫描垃圾”与“确认清理”两个主要动作，复杂的 SQLite 历史维护折叠到“深度清理”；同页新增“会话管理”，通过 Codex 官方删除命令永久删除用户明确选择的主会话及其关联子任务。用量排行继续保留在既有“用量总览”，不再占用清理页。

## Background

- 当前“存储”页只在用户点击后扫描 `CODEX_HOME` 元数据，raw 清理仅允许超过 7 天、未被配置引用且名称可识别的临时 staging/clone；其余数据大量处于只展示不可清理状态，安全边界强但日常收益低（`src/codex_usage_hud/core/codex_file_manager.py:413`, `src/codex_usage_hud/core/codex_file_manager.py:1048`）。
- 当前实现已经具备 inventory revision、opaque ID、canonical path、lstat/fingerprint、锁检测、二次确认和退出后队列，适合作为新清理合同的安全基础（`src/codex_usage_hud/core/codex_file_manager.py:413`, `src/codex_usage_hud/core/codex_file_manager.py:438`, `src/codex_usage_hud/core/codex_file_manager.py:525`, `src/codex_usage_hud/core/codex_file_manager.py:646`）。
- 现有本地数据足以支撑洞察：会话 token/费用、缓存命中、日/周额度、Provider、模型、后台用量和请求历史均已由 renderer/runtime 读取；`UsageSummaryCache` 已按会话文件缓存日/周贡献，无需遥测、网络依赖或新增空闲轮询（`src/codex_usage_hud/cli.py:4426`）。
- 2026-07-20 本机只读审计显示，`logs_2.sqlite` 为 614,039,552 bytes、96,677 行；早于 24 小时的数据约占 444-466 MiB，早于 7 天的数据仅约 3-5 MiB，因此默认保留最近 24 小时才能产生实际收益。`state_5.sqlite` 约 4 MiB、包含约 1,029 个线程，收益不足以抵消会话映射风险。
- 同次审计发现旧 `logs_2.sqlite.pre-cleanup-*` 备份约 120 MiB；HUD 自有日志/审计共 42 个文件、约 15.68 MiB。HUD 白名单包括 `crash.log`、`renderer_fallback.log`、`daemon.log*`、`window_tracker.log*`、`hud_geometry.log*`、`work-overlay-transitions.jsonl` 和历史 `work-overlay-*-commands.jsonl`。
- `hud_settings.json`、活动 overlay 状态、`renderer_cdp_state.json` 和 `background-usage.sqlite3` 不是普通日志；`logs_2.sqlite` 是 Codex OTel/SSE 日志源，也是 HUD 请求跟踪与后台用量增量归档的数据源，不能在相关进程运行时直接修改。
- 用户已明确同意：HUD 自有日志进入默认一键清理；`logs_2.sqlite` 默认保留最近 24 小时；备份目录可由用户指定到其他磁盘；无活动任务且完成明确确认后，可自动关闭 Codex App 和 HUD，清理后恢复启动。只读代码审计进一步确认，任意独立 Codex CLI 的终端/stdin/cwd/argv 无法可靠重建，因此第一版检测到独立 CLI 时必须阻止离线 SQLite 维护并提示手动退出，不能强杀后宣称已恢复。

## Requirements

### R1. 空间清理为一级用户功能

- 将设置页一级“用量洞察”入口替换为“空间清理”，保持 renderer-only、local-first、无遥测、无网络依赖；Qt/Tk 遗留设置页不新增该功能。
- 页内只保留“垃圾清理”和“会话管理”两个分段。会话、模型、Provider、后台用量和缓存效率排行继续由既有“用量总览”承载，不在清理页重复展示。
- “垃圾清理”默认只暴露两个主要动作：扫描前的“扫描垃圾”，以及扫描完成后的“确认清理”；重新扫描是次要动作。
- 扫描完成后，后端必须自动为默认安全选择生成绑定 inventory revision 的 preview 与单次 confirmation token。UI 不再要求用户显式点击“生成清理预览”。
- SQLite 历史、保留期、备份目录和关闭/恢复影响折叠进“深度清理”，默认关闭且不选中；修改深度清理选择或备份目录后静默重建 preview，新 token 返回前禁用确认清理。

### R2. 分级一键清理

- 清理结果分为“可直接清理”“确认后清理”“始终保护”，每类显示项目数、预计可回收空间、影响和需要关闭的应用。
- “可直接清理”默认选中且仅包含固定白名单、执行前可完整重验、不会改变账号/配置/会话/统计语义的数据：过期且未引用的 Codex staging/clone、HUD 自有当前/旋转诊断日志、历史 overlay 命令/审计日志、失效运行时残留、过期清理备份，以及 Windows/macOS 明确可再生成的系统/开发工具缓存。
- 常规缓存覆盖：超过 24 小时且树内无较新文件的用户临时项、NuGet、npm、pip、VS/VS Code 缓存，以及 Chrome/Edge 页面/代码/GPU/Shader cache；Windows 额外覆盖当前用户的 DirectX/显卡着色器缓存，macOS 额外覆盖 Homebrew 下载缓存与 Xcode DerivedData。相关应用运行时整类跳过；不清 Cookie、密码、书签、扩展、项目源码、全局工具、休眠文件或系统级目录。
- “确认后清理”默认不选中，可包含不影响后续启动/对话但会损失历史的项目：超过 7 天的 Windows/macOS 崩溃与错误报告；`logs_2.sqlite` 默认保留最近 24 小时；`background-usage.sqlite3` 默认保留最近 30 天。用户必须看到保留期和不可恢复内容后单独同意。非 SQLite 诊断项不应误要求数据库备份。
- HUD 日志虽然进入默认清理，仍需提示旧诊断信息会消失；它们只从已解析 HUD runtime 根和固定文件名/旋转后缀识别，不跟随 symlink，不删除根外显式路径。
- 未知文件、配置、凭据、secrets、reparse point、活动会话、活动插件运行时、无法完整验证的对象，以及 `state_5.sqlite`、`goals_1.sqlite`、`memories_1.sqlite` 始终保护；通用确认不能解锁。

### R3. SQLite 离线维护与备份

- 不删除 SQLite 主库，不单独删除 WAL/SHM，不在 Codex/HUD 使用数据库时删除行、checkpoint 或 VACUUM；开始前先让 HUD 完成后台用量增量归档并关闭相关连接。
- SQLite 维护在独立维护子进程中执行。子进程等待 HUD/Codex 退出，再验证数据库 canonical path、身份、schema、锁、WAL/SHM 状态和计划指纹；任一验证失败则保持源库不变并分项报告。
- `logs_2.sqlite` 仅删除时间阈值以前的已识别日志行；`background-usage.sqlite3` 仅删除阈值以前的已识别审计行。只在预计收益达到实现中定义并测试的阈值时 checkpoint/VACUUM。
- SQLite 修改必须先用 SQLite backup API 创建完整备份并执行 integrity check。用户可输入/选择任意本地备份目录，包括其他磁盘；备份目录无效、空间不足或备份失败时不得开始修改，不提供无备份绕过。
- 成功备份不会在同一次维护中被静默删除；已识别且超过 7 天的旧清理备份可在后续扫描中作为“可直接清理”项单独列出。备份目录若与源库同卷，dry-run 必须单列备份占用，不能把它计为净释放空间。
- 修改后执行 integrity check 和关键查询验证。若验证失败，关闭连接并从本次备份恢复；恢复失败必须保持结果文件并明确标为需人工处理，不能显示清理成功。

### R4. 两步体验、活动门禁与恢复

- 用户打开空间清理后点击“扫描垃圾”；系统显式扫描、自动选中“可直接清理”，并在同一后台动作中生成默认 dry-run/confirmation token。
- 真正执行前显示：各类别项目数、预计回收字节、同卷备份成本、是否关闭 Codex/HUD、会损失的历史，以及恢复启动目标。
- 所有路径项继续使用 inventory revision、opaque ID、canonical path、lstat/fingerprint、路径边界和锁状态重验，防止扫描后路径变化或 TOCTOU 删除。
- 只要当前会话、后台工作项或任一已观察会话仍处于运行态，流程就暂停并保持数据不变；不能强杀、绕过锁或用“用户已确认”覆盖活动任务门禁。
- 无活动任务且用户明确确认自动关闭后，流程可关闭 Codex App 和 HUD/daemon。独立 Codex CLI 存在时，离线 SQLite 项保持不变并提示用户先手动退出；不能自动终止无法原样恢复的 CLI。维护子进程必须等待精确目标 PID 与数据库锁释放，执行后按启动前状态恢复 Codex App 与原 HUD/daemon 启动形态。
- 清理完成后重新扫描或读取维护结果，显示实际删除行数/项目数、实际回收量、备份位置、失败项和仍受保护空间；不得只根据删除前估算宣称成功。

### R6. 会话永久删除

- 会话管理只展示主会话。子 agent 和嵌套子 agent 归并到其 canonical 根会话，并显示关联子任务数量；不得作为重复顶层行。
- 会话 inventory 从本地 `state_5.sqlite` 的 threads/spawn 关系及 rollout 映射只读构建。renderer 只接收 opaque item ID、标题、工作目录尾部、最后活动时间、状态、估算占用和后代数量；canonical UUID、绝对路径、prompt/response 只保留在 Python 端。
- 当前会话、运行中的主会话、存在活动子 agent 的会话、临时未持久化会话、canonical UUID 或 rollout 关系无法证明的会话不可选择，通用确认不能解除这些门禁。
- 永久删除必须调用官方非交互命令 `codex delete --force <canonical UUID>`。不得直接改写 `state_5.sqlite`，不得 raw 删除 rollout，也不得在官方命令不可用时降级为自实现删除。
- HUD 启动/扫描时探测当前 Codex CLI 的删除能力。能力缺失时保持列表只读并明确显示不可用原因。
- 删除使用独立 inventory revision、opaque item ID 和单次 confirmation token。最终确认明确列出主会话数、关联子任务数及不可恢复性，不复用垃圾清理确认。
- 批量删除按主会话逐个执行；每项命令完成后重新核对 state DB、active/archived rollout 和会话索引。部分失败逐项报告，未删除项保持可重试，不能把整批显示为成功。

### R5. 隐私、平台与兼容性

- renderer payload 只传类别、相对/中性标签、大小、时间、风险、影响、聚合 token/费用和操作状态；不传 prompt、response、凭据、绝对敏感路径或原始数据库内容。
- Windows 与 macOS 使用同一 inventory/确认/维护结果合同；平台适配器只负责白名单路径、相关进程识别、退出等待和恢复启动。
- 保持 Renderer 事件驱动合同：无页面、会话、配置、文件或显式用户操作事件时，不新增周期性 CPU/磁盘工作。
- 更新 `docs/PRIVACY.md`：默认只读仍成立，但用户显式触发的白名单缓存删除、SQLite 本地备份/维护和恢复流程属于新的本地例外。

## Acceptance Criteria

- [ ] 一级导航显示“空间清理”，页内只有“垃圾清理”和“会话管理”；用量排行继续留在“用量总览”。
- [ ] 默认垃圾清理从扫描前到执行只需“扫描垃圾”与“确认清理”两个主要动作；扫描返回的 token 绑定当次 revision/默认选择，修改深度选项时会静默刷新并暂时禁用执行。
- [ ] 深度清理默认关闭且 SQLite 默认不选中；受保护空间只做摘要，不伪装为可清理垃圾。
- [ ] 一次清理流程明确区分可直接清理、确认后清理和始终保护；可直接清理默认选中，SQLite 历史默认不选中。
- [ ] 默认一键清理能识别 HUD 当前/旋转诊断日志和历史 overlay 审计日志，且不会删除 `hud_settings.json`、`renderer_cdp_state.json`、活动 overlay 状态或 `background-usage.sqlite3` 文件本身。
- [ ] Windows/macOS 白名单系统/开发缓存可 dry-run；Windows 当前用户着色器缓存与 macOS Homebrew/Xcode 缓存默认选中，超过 7 天的系统诊断报告仅在单独同意后选中；相关应用运行时整类跳过，reparse point、未知目录和白名单根外路径保持不变。
- [ ] `logs_2.sqlite` 只删除 24 小时阈值以前的已识别行；`background-usage.sqlite3` 只删除 30 天阈值以前的已识别行；两者均保留主库并通过 integrity check。
- [ ] SQLite 备份目录可配置到其他磁盘；备份失败不修改源库，验证失败能从本次备份恢复；同卷备份不被计入净回收量。
- [ ] 活动任务存在时不会关闭进程或修改数据；独立 Codex CLI 存在时不会强杀或修改离线 SQLite；其余空闲且确认后的流程可关闭 Codex App/HUD/daemon、等待锁释放、执行维护，并按启动前状态恢复。
- [ ] `state_5.sqlite`、`goals_1.sqlite`、`memories_1.sqlite`、配置、凭据、secrets、未知项和 reparse point 始终不被通用一键清理修改。
- [ ] 执行结果使用重新扫描/维护结果核对实际回收量，并逐项显示删除、跳过、失败、恢复和保护状态。
- [ ] 会话管理按 canonical 根会话展示，子 agent 不重复出现；当前/运行中/未解析会话不可选，renderer payload 不包含 canonical UUID、绝对路径、prompt 或 response。
- [ ] 永久删除只通过 `codex delete --force <UUID>` 执行；CLI 不支持时功能禁用且无 raw fallback。隔离 `CODEX_HOME` 演练证明 active/archived 会话及父子树删除后 state DB、rollout 与索引均重新核验。
- [ ] 批量删除逐项报告成功/失败；部分失败不会误报整批成功，也不会影响未执行或受保护会话。
- [ ] focused tests、完整 `python -m pytest`、`compileall`、`git diff --check`、Windows 真实 Renderer/dry-run/备份/SQLite 清理演练通过；macOS 通过平台合同测试并留下人工验证清单。

## Out Of Scope

- 云端同步、遥测、远程账单查询或上传清理报告。
- 直接 raw 删除会话 JSONL、手写 Codex state DB、插件、登录状态、配置、凭据、目标或记忆数据库；会话删除只允许走当前 Codex CLI 官方能力。
- 清理 Cookie、密码、书签、扩展、项目依赖目录、全局工具、回收站/废纸篓、休眠文件、系统更新缓存或需要管理员权限的系统目录。回收站/废纸篓需要独立的原生 OS 删除与可恢复性合同，不能复用普通路径清理。
- 修改 Codex/HUD 未识别表、猜测性清理未知文件，或在数据库/任务活动时强制解除锁。
- 在 Qt/Tk 遗留 HUD 中复制新功能。
