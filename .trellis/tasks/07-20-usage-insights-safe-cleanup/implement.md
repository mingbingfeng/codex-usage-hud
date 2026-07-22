# 空间清理与会话永久删除实施计划

## 1. 准备与合同测试

- [x] 启动任务后读取 backend spec、renderer strategy、privacy 文档和相关测试。
- [x] 为 `UsageSummaryCache.insights()` 添加会话/model/provider/cache-ratio/cost-coverage 的单元测试，先证明不触发额外 parse/目录扫描。
- [x] 为 safe cleanup inventory/preview/confirmation/helper plan 添加纯临时目录测试，覆盖 revision、opaque ID、path escape、reparse、fingerprint、锁和活动任务拒绝。
- [x] 为 Windows/macOS cache adapter 添加路径白名单、相关进程整类跳过和 unsupported fail-closed 合同测试。

## 2. 用量洞察数据层

- [x] 扩展 `_UsageCacheEntry`，在一次 records parse 中保存 day/week session/model/provider contributions；避免二次读取。
- [x] 新增 cache-only `insights(...)` 聚合和 RuntimeContext 的 `usage_insights_payload`。
- [x] 接入后台用量 store 的脱敏摘要；不把 detail prompt 放入 insights payload，不与会话总量相加。
- [x] 标记归档/无 canonical ID 会话为不可跳转；缺失价格事件输出 coverage 而不是 `$0`。
- [x] 在预算/会话文件/Provider/background revision 事件与显式刷新命令上发布 `usageInsights` domain。

验证：

```powershell
python -m pytest tests/test_ui.py -q -k "usage_summary_cache or usage_insights"
python -m pytest tests/test_background_usage.py -q
```

## 3. 安全清理核心

- [x] 新增 `core/safe_cleanup.py` 的数据类、manager、worker 与平台适配器合同。
- [x] 组合 Codex candidate，不复制/放宽 `CodexFileManager` 原有安全检查。
- [x] 实现 HUD runtime 固定日志/残留白名单和旧 backup 识别，保护 settings/state/database。
- [x] 实现 Windows/macOS 用户 temp 与 NuGet/npm/pip/VS/VS Code/Chrome/Edge cache definitions；相关进程运行时跳过。
- [x] 扩展常规磁盘清理：Windows 当前用户 DirectX/显卡着色器缓存、macOS Homebrew/Xcode 缓存默认为 safe；超过 7 天的 Windows/macOS 系统诊断报告为独立 consent，且非 SQLite 项不要求备份。
- [x] 实现分级 payload、默认选择、dry-run、备份成本/同卷净收益和单次 confirmation token。

验证：

```powershell
python -m pytest tests/test_codex_file_manager.py tests/test_safe_cleanup.py -q
```

## 4. SQLite 离线维护 helper

- [x] 实现 `logs_2.sqlite` schema allowlist、24 小时 cutoff 估算/删除、收益阈值、checkpoint/VACUUM 和 integrity/关键查询验证。
- [x] 实现 `background-usage.sqlite3` 30 天历史清理，保持 schema/version 与主库文件。
- [x] 实现强制 backup API、可选外部目录、空间检查、同卷计算、失败前不修改和验证失败恢复。
- [x] 新增 versioned 原子 plan/result 文件与 hidden CLI helper 参数；兼容 frozen/source 启动命令。
- [x] helper 等待父/目标 PID 和锁释放；过期、schema 变化、指纹变化、锁定或未退出均跳过且写明确结果。

验证：

```powershell
python -m pytest tests/test_safe_cleanup.py tests/test_background_usage.py -q -k "sqlite or maintenance or backup or restore"
python -m codex_usage_hud --cleanup-maintenance-helper --cleanup-plan-file <temporary-test-plan>
```

## 5. Runtime 关闭与恢复

- [x] RuntimeContext 在每次 fresh snapshot 后维护活动任务门禁；背景用量 runtime 支持显式 wait-idle/close 顺序。
- [x] settings command 在停止进程前检查活动任务，并把确认/备份/恢复选择传给 safe cleanup worker。
- [x] 扩展 Windows/macOS adapter：精确识别独立 CLI 并阻断离线维护；请求 Codex App 退出、等待 PID、记录是否原先运行；失败则保持 HUD 和数据不变。
- [x] 新增 daemon maintenance terminal exit code；helper 完成后恢复原 daemon/HUD 启动形态，避免 daemon 提前重开日志或产生重复实例。
- [x] 下次 HUD 启动读取 result，重新扫描并展示实际回收/失败/恢复状态。

验证：

```powershell
python -m pytest tests/test_ui.py tests/test_daemon.py tests/test_safe_cleanup.py -q -k "cleanup or restart or active_task or daemon"
```

## 6. Renderer UI 与动作

- [x] 新增 `usageInsights`/`safeCleanup` payload fields 和独立 domains，保持其他 domain partial update 合同。
- [x] 用量排行实现保留在既有“用量总览”；空间清理页不再重复渲染排行。
- [x] 将旧文件列表替换为三类清理摘要与一键 dry-run；实现 consent、backup path、自动关闭/恢复二次确认，SQLite 备份不可关闭。
- [x] 保持设置 modal 尺寸、滚动、主题、键盘/焦点和长文本布局稳定；不触碰 Qt/Tk HUD。

验证：

```powershell
python -m pytest tests/test_renderer_hud.py tests/test_settings_bridge.py tests/test_ui.py -q -k "insights or cleanup or payload_domain or settings"
```

## 7. 已确认改版增量

- [x] 将一级入口改为“空间清理”，页内实现“垃圾清理 / 会话管理”分段，保持旧 `storage` state key 兼容。
- [x] `_SafeCleanupWorker` 在 scan 后自动对默认 safe IDs 生成 preview/token；移除显式“生成清理预览”，默认流程收敛为“扫描垃圾 -> 确认清理”。
- [x] 深度清理默认折叠且不选 SQLite；修改 consent/backup 选项时静默重建 preview，新 token 返回前禁用执行。
- [x] 新增 `core/session_cleanup.py`：能力探测、根会话 inventory、父子归并、opaque ID、revision/token、活动会话门禁、逐项官方删除及执行后核验。
- [x] RuntimeContext/CLI 接入 `sessionCleanupScan/Preview/Execute/Cancel` worker 与 domain；canonical UUID/绝对路径不进入 renderer payload。
- [x] Renderer 实现会话搜索、筛选、批量选择、受保护态、关联子任务数、不可恢复确认和部分失败结果。
- [x] 添加 session manager、CLI worker、renderer 状态机与两步垃圾清理 focused tests。

验证：

```powershell
python -m pytest tests/test_session_cleanup.py tests/test_safe_cleanup.py tests/test_renderer_hud.py tests/test_ui.py -q -k "session_cleanup or safe_cleanup"
```

## 8. 文档、全量检查与真实演练

- [x] 更新 `docs/PRIVACY.md` 和 macOS 验证清单，记录本地写入例外、备份和 fail-closed 行为。
- [x] 在完全临时的 SQLite fixture 上演练 dry-run、成功备份、删除阈值、VACUUM、验证失败恢复和 result 重读。
- [x] 在 Windows 真实 Renderer 中验证布局、打开高消耗会话、后台详情、一键 dry-run、活动任务阻止、外部备份目录和关闭/恢复。
  - 2026-07-20 已验证真实布局、scan/dry-run、确认项优先显示、不可变 consent/备份目录和二次确认；未执行真实关闭/恢复。
  - 2026-07-21 扩展常规缓存后再次连接真实 Codex Renderer：默认选中 48 个 safe 项、预计约 1.1 GB，HUD 日志可见且标记需退出后维护，551 MB Codex SQLite 历史保持未选；预览后 consent 控件锁定，`scrollWidth == clientWidth`，随后取消预览并关闭设置。截图：`C:\Users\zjxqm\AppData\Local\Temp\codex-usage-hud-cleanup-platform-preview.png`。
  - 同次只读核对命中当前用户 DirectX shader cache；未创建 maintenance plan/result/SQLite 备份，`logs_2.sqlite` 为 100,564 行、其中 85,465 行早于 24 小时，未执行 DELETE/VACUUM。
  - 2026-07-21 跨盘净释放收口后重启工作区 HUD 并连接真实 Codex Renderer：默认仍为 48 个 safe 项、约 1.1 GB，HUD 日志默认选中，551 MB SQLite 历史未选；勾选确认项并输入 E: 临时备份目录后，预览显示源盘预计释放 1.6 GB、648 MB 备份存到 E: 且不占用源盘，二次确认复用同一净释放合同，控件锁定且无横向溢出。随后取消二次确认和预览、关闭设置，未执行清理。截图：`C:\Users\zjxqm\AppData\Local\Temp\codex-usage-hud-cleanup-cross-volume-preview.png`。
  - 同轮完全临时演练使用 C: SQLite fixture 与 E: backup 目录：删除 2 条旧行、保留 2 条新行，E: 备份保留全部 4 条，源库与备份 integrity 均为 `ok`；临时目录已自动删除。真实库保持 100,564 行且 integrity 为 `ok`，无新 plan/result/backup。
  - 2026-07-21 Top10 根会话归并后重启源码 HUD，并在真实 Codex Renderer 的近 7 天范围分别验证 Top10用量与 Top10金额：两者均为 9 个唯一根会话，逐条回查 `session_meta` 后子 agent 行为 0；左/右栏宽度为 340/718 px，长标题和目录实际触发 ellipsis，工作目录末级名称同时出现在左栏与右侧详情，`scrollWidth == clientWidth == 1058`。截图：`C:\Users\zjxqm\AppData\Local\Temp\codex-usage-hud-top10-root-sessions-7d.png`。
  - 2026-07-21 垃圾清理默认扫描命中 229 个 safe 项（约 4.0 GB）、286 个保护项（约 18 GB），深度清理 629 MB 默认折叠未选；点击确认后活动任务门禁返回“检测到 1 个活动任务”，未关闭应用、未生成 plan/result、未启动 maintenance helper、未修改数据。
- [x] 真实 `logs_2.sqlite` 只在用户当前已给出的明确授权范围内执行；执行前再次确认无活动任务并记录 backup/行数/大小，执行后验证 Codex/HUD 请求跟踪、会话切换和后台用量。
  - 本轮仅只读核对保留期以前的 85,444 行在预览前后不变，未创建备份、未执行 DELETE/VACUUM。
  - 用户未授权对真实数据库执行破坏性清理；本项按授权边界完成，不以任务确认替代数据删除确认。
- [x] 运行全量质量门：

```powershell
python -m pytest tests/test_safe_cleanup.py tests/test_codex_file_manager.py tests/test_background_usage.py tests/test_renderer_hud.py tests/test_settings_bridge.py tests/test_ui.py tests/test_daemon.py -q
python -m pytest -q
python -m compileall -q src tests tools
git diff --check
```

- [x] 检查 `git status` 只包含本任务文件；更新 spec，提交实现并归档任务。
  - 2026-07-22：spec 已补视觉/布局合同；用户确认实机验收通过后进入提交/归档。共享文件（`cli.py` / `renderer_hud.py` / `test_ui.py` 等）仍混有并行改动，提交须精确拆分，禁止 `git add .`。
- [x] 在隔离 `CODEX_HOME` 创建 active、archived 和父子会话 fixture，真实执行 `codex delete --force` 并核验 state DB、session index 与 rollout；绝不对用户真实会话执行删除。
  - 2026-07-21 使用随机 UUID、仅复制真实 schema/migration 的临时 `CODEX_HOME` 调用 `codex-cli 0.144.6`：未归档父会话删除后父子树从 DB/index/rollout 消失，归档会话保持；再删除归档会话后全部为空。真实 state DB 仅做随机 UUID 碰撞只读检查。
- [x] 重启源码 HUD，在真实 Codex Renderer 验证空间清理两段式布局、两步垃圾清理、会话筛选/受保护态/最终确认、无横向溢出及无 console error。
  - 2026-07-21 Renderer v34 实机：1014 个主会话、36 个关联子任务；状态、时间、搜索三类筛选均将“已选 1 / 永久删除可用”清为“已选 0 / 按钮禁用”。当前/运行中筛选显示 2 条受保护会话，横向溢出为 0，console/window error 为空。截图：`C:\Users\zjxqm\AppData\Local\Temp\codex-usage-hud-session-filter-clear-v34.png`。
  - 2026-07-22 用户实机验收通过：视觉对稿与功能状态满足设计稿，标记完成。

## 10. UI 视觉对稿修正（本轮）

- [x] 空间清理弹窗专属高度 `572px`，隐藏通用关闭底栏，改用固定约 `50px` 清理操作栏。
- [x] 左侧紧凑 segmented control（每项 min 96px / 高约 31px）。
- [x] 扫描前居中空状态：62px 扫描图标、17px 标题、12px 说明、38px 主按钮。
- [x] 扫描结果：绿色汇总带、54px 分类行、黄色深度清理行、保护摘要。
- [x] 会话工具栏改为搜索 + 紧凑 filter chips；全选位于表头。
- [x] 会话桌面六列表头与稳定列；520-759 使用 grid areas；<520 隐藏表头并显示副行。
- [x] 永久删除确认：`data-tone="danger"`、危险图标、三项摘要、黄色提示、独立操作栏。
- [x] 补充 `tests/test_renderer_hud.py` 视觉结构合同；focused tests / compileall / JS syntax 通过。
- [x] 真实 Codex Renderer 四状态截图对稿与窄窗 `scrollWidth == clientWidth` 验收。
  - 2026-07-22 用户实测通过并确认标记完成；未对真实会话执行永久删除，未对真实库执行破坏性清理。

## 9. 回滚点

- 数据层与 UI domain 可独立回滚；未知 domain 被 renderer 忽略。
- safe cleanup manager 不替换原 `CodexFileManager`，出现问题可隐藏新入口而保留旧安全引擎。
- 维护 helper 在任何源库修改前必须已拥有通过 integrity check 的 backup；不提供关闭 backup 的绕过，否则整项跳过。
- 真实数据演练若任一验证失败，立即停止后续类别，保留 result/backup，不继续扩大清理范围。
- 会话永久删除出现能力或核验异常时隐藏/禁用删除动作；保留只读 inventory，不回退到 raw 删除。
