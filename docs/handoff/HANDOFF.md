# Codex Usage HUD 交接

- 本次交接时间：2026-07-21 20:12:18 +08:00（Asia/Shanghai）
- 当前任务名：`usage-insights-safe-cleanup`
- 当前小项目/模块：Renderer 设置弹窗 / 空间清理 / 会话永久删除
- 本次交接已覆盖上一次交接。

## 1. 当前项目最终目标

将设置界面的“用量洞察”改造成专注清理的“空间清理”页：

1. 默认垃圾清理流程只有两个主要动作：`扫描垃圾` -> `确认清理`。
2. 增加“会话管理”，通过官方 `codex delete --force <UUID>` 永久删除主会话及其关联子任务。
3. 保留 revision、opaque ID、路径边界、指纹、活动任务门禁、SQLite 备份/恢复等安全合同。
4. Renderer 是唯一产品界面；不向 Qt/Tk 遗留界面增加新功能。
5. 最终 UI 必须与用户已确认的设计稿一致，并在真实 Codex Renderer 中截图验收。

## 2. 当前正在处理的问题

后端、Renderer 状态机和功能测试已基本完成，但最新真实截图对稿显示：功能结构正确，视觉仍没有达到已确认设计稿。任务因此从提交收尾退回到 UI 修正阶段。

用户最新明确要求：“UI方面我挺在意的，你要确保跟设计图一致哦”。视觉一致性是提交前硬门槛，不能仅凭单元测试通过宣称完成。

当前尚未应用本轮视觉修正。本轮只完成了只读独立对稿，确认以下 P0 差异：

1. 空间清理弹窗当前约 `980 x 720px`，设计稿主体约 `572px` 高；下半部空白明显。
2. “垃圾清理 / 会话管理”当前横向铺满，设计稿为左侧紧凑 segmented control，每项至少 `96px`、高约 `31px`。
3. 空间清理仍复用通用“关闭”底栏，核心动作又位于 body；设计稿要求固定约 `50px` 的专属操作栏。
4. 扫描前当前为横排文案和按钮；设计稿为垂直居中空状态：`62px` 圆形扫描图标、`17px` 标题、`12px` 说明、`38px` 单主按钮。
5. 扫描结果缺少绿色汇总带、稳定 `54px` 分类行、checkbox/影响/容量/chevron、黄色深度清理行和紧凑保护摘要。
6. 会话列表没有设计稿的六列表头；工作目录和时间被混入副文本。桌面版应为：复选框、会话、工作目录、最后活动、状态、占用。
7. 会话工具栏当前为原生 select 和“全选当前筛选”；设计稿为搜索框、紧凑筛选控件，且全选位于表头。
8. 永久删除确认仍是通用确认卡，缺少红色危险图标、三项摘要、黄色提示和独立操作栏。
9. 窄窗口存在理论溢出：overlay padding 为 `24px`，dialog 宽高计算只扣除了 `32px/24px`，没有完整扣除双侧 padding。

## 3. 已完成的修改

### 成功

- 新增安全清理核心：白名单扫描、分级清理、opaque ID、revision/token、进程与路径门禁、SQLite 离线维护、备份/恢复。
- 默认安全清理已收敛为扫描后自动 preview，用户下一次主要点击即可确认清理；只有深度清理继续保留强确认。
- 新增会话永久删除核心：能力探测、根会话 inventory、父子归并、活动会话保护、批量串行删除、逐项核验。
- 永久删除只调用官方 `codex delete --force`，不直接修改 `state_5.sqlite`，不 raw 删除 rollout。
- 隔离 `CODEX_HOME` 中已真实验证 active、archived、父子会话级联删除；未删除用户真实会话。
- Renderer 已实现“垃圾清理 / 会话管理”、搜索/筛选/选择清理、受保护态和永久删除确认状态机。
- 已补充 safe cleanup、session cleanup、Renderer、CLI/runtime 和文档测试。
- 设计稿已落库：`docs/designs/space-cleanup-session-delete-v1.html` 与同名 PNG。
- 先前全量 pytest、Ruff、compileall、JS 语法和 diff check 曾通过；这是 UI 对稿回退之前的记录，本次交接未重新执行全量门禁。

### 部分有效

- Renderer v34 实机已验证会话筛选会清空已选项、当前/运行中会话不可选、无横向溢出、无 console/window error。
- 该轮实拍证明功能状态正确，但视觉对设计稿仍有明显偏差，不能作为最终 UI 验收。

### 未验证

- 本轮视觉修正尚未实施，因此扫描前、扫描结果、会话管理、永久删除确认四张最终对稿截图均未生成。
- macOS 真实设备人工 UI 验收未记录；当前只有平台合同测试和验证清单。
- 当前 daemon/CDP 运行状态未在交接时复核。前一轮记录为 HUD PID `15976`、CDP 端口 `58803`、Codex 主进程 PID `28548`，可能已经过期。

## 4. 关键文件列表

- `.trellis/tasks/07-20-usage-insights-safe-cleanup/prd.md`：需求和验收标准。
- `.trellis/tasks/07-20-usage-insights-safe-cleanup/design.md`：安全清理、会话删除和 Renderer 数据合同。
- `.trellis/tasks/07-20-usage-insights-safe-cleanup/implement.md`：已完成步骤、历史实机验收和剩余提交步骤。
- `.trellis/tasks/07-20-usage-insights-safe-cleanup/redesign-proposal.md`：用户确认的改版范围。
- `docs/designs/space-cleanup-session-delete-v1.html`：可量取尺寸/结构的设计源。
- `docs/designs/space-cleanup-session-delete-v1.png`：最终视觉基准，197,335 bytes，修改时间 2026-07-21 15:10:50。
- `src/codex_usage_hud/ui/renderer_hud.py`：本轮主要修改面。当前锚点：`safeCleanupPanelHtml()` 约 6403，`storagePanelHtml()` 约 6431，`sessionCleanupPanelHtml()` 约 6481，`renderSettingsModal()` 约 7346；相关 CSS 从约 2994 开始。
- `tests/test_renderer_hud.py`：补视觉结构合同和命令边界测试。
- `src/codex_usage_hud/core/safe_cleanup.py`：安全清理 inventory/preview/plan/helper。
- `src/codex_usage_hud/core/session_cleanup.py`：会话 inventory、能力探测、官方删除与核验。
- `src/codex_usage_hud/cli.py`：两个 cleanup domain 的 worker/runtime 接线；该文件混有其他并行改动。
- `tests/test_safe_cleanup.py`、`tests/test_session_cleanup.py`、`tests/test_ui.py`：核心与跨层覆盖。
- `.trellis/spec/backend/safe-cleanup-contracts.md`：应在最终实现后同步确认的可执行合同。

## 5. 当前 Git 状态摘要

- 仓库：`E:\Project\codex-usage-hud`
- 分支：`main`
- 相对 `origin/main`：ahead 1
- 当前任务：`usage-insights-safe-cleanup`，状态 `in_progress`，P1，assignee `fbm`
- 工作树包含约 43 个变更路径，既有本任务，也有 Top10/后台用量、CDP 会话跳转、updater/release、Renderer runtime 等并行改动。
- 本任务文件已有一批暂存；共享大文件仍同时包含未暂存改动。
- 未跟踪文件 `docs/UPDATE_DELIVERY.md` 属于并行 updater/release 工作，不得纳入本任务。
- 禁止 `git add .`，禁止回退或覆盖未暂存并行改动。

高风险共享文件：

- `src/codex_usage_hud/cli.py`
- `src/codex_usage_hud/ui/renderer_hud.py`
- `tests/test_renderer_hud.py`
- `tests/test_ui.py`
- `src/codex_usage_hud/core/__init__.py`

## 6. git diff --stat 摘要

未暂存：

- 19 files changed
- 7,100 insertions
- 721 deletions
- 最大差异：`cli.py` +2598/-294、`renderer_hud.py` +2114/-208、`tests/test_ui.py` +1443/-40

已暂存：

- 25 files changed
- 7,731 insertions
- 13 deletions
- 新设计 PNG：197,335 bytes
- 主要新增：`safe_cleanup.py` 3,194 行、`session_cleanup.py` 844 行、`test_safe_cleanup.py` 1,574 行、`test_session_cleanup.py` 357 行

注意：暂存区和未暂存区存在同一路径的双重修改，例如 `README.md`、`.trellis/spec/backend/safe-cleanup-contracts.md`。提交前必须按任务意图重新精确核对。

## 7. 重要 diff 变化说明

- `safe_cleanup.py`：新增面向用户的安全清理编排，组合 Codex 临时项、HUD 日志、系统缓存与 SQLite 深度维护。
- `session_cleanup.py`：新增只读会话投影和官方命令删除编排，canonical UUID/绝对路径不进入 Renderer payload。
- `cli.py`：新增 safe/session cleanup worker、命令分发和 domain 发布；同时混有并行功能，不可整文件盲目暂存。
- `renderer_hud.py`：新增清理页面、会话管理、确认界面及大量其他 Renderer 变更；本轮应只编辑空间清理专属 CSS/markup。
- `tests/test_renderer_hud.py`：已有清理状态机测试，但尚缺本轮设计稿要求的紧凑分段、扫描前居中态、六列表头、危险确认摘要等结构合同。
- `safe-cleanup-contracts.md`：记录安全边界、官方删除命令和 Renderer 选择清理合同；最终 UI 完成后只补真正稳定的新视觉/布局合同。
- `docs/PRIVACY.md`：记录用户显式触发的本地删除、SQLite 备份/维护例外。
- `docs/MACOS_VALIDATION.md` 与 macOS smoke：记录跨平台合同和人工验收边界。

## 8. 已尝试的优化方案及结果

- 后端安全清理与离线维护：`成功`。临时 fixture 覆盖备份、阈值删除、完整性检查和恢复。
- 官方会话删除：`成功`。隔离 `CODEX_HOME` 验证父子树、active/archived 数据消失。
- 两步默认清理：`成功`。scan 自动生成默认 safe preview/token，第二个主要动作直接 execute。
- 会话筛选清空选择：`成功`。真实 Renderer v34 验证搜索、状态、时间筛选都会将已选 1 清为 0。
- 当前视觉实现：`部分有效`。交互和信息架构成立，但尺寸、分段、表格、空状态、操作栏和危险确认没有对齐设计。
- 浏览器插件打开本地设计 HTML：`失败`。被安全策略禁止，标签已清理；不要绕过。使用仓库 PNG/HTML 做基准，真实 Codex App 继续走现有 CDP 验收路径。
- 当前暂存区质量：`失败`。`git diff --cached --check` 报 Trellis `check.jsonl`、`implement.jsonl`、`task.json` 的 trailing whitespace；未暂存 `git diff --check` 通过。

## 9. 后续可能继续优化的方向

- 空间清理弹窗专属高度和内部滚动，不改其他设置 tab。
- 图标统一复用现有 Renderer icon API/已存在 SVG 图标，不引入新依赖。
- 会话六列在 760px 以上稳定展示，520-759px 用明确 grid areas 收敛，520px 以下隐藏表头并把目录/时间放入副行。
- 只有内容列表纵向滚动；overlay、dialog、workspace 全部禁止横向溢出。
- 危险确认使用独立 `data-tone="danger"` 或等价作用域，避免污染安全清理和其他通用确认。
- 不改变默认 safe-only 的两步状态机；深度 SQLite 维护才保留额外强确认。

## 10. 下一步最小执行计划

1. 先读本交接、PRD、design、implement、redesign proposal、safe cleanup spec 和 `docs/RENDERER_MODE_STRATEGY.md`，不要重复后端研究。
2. 对照设计 HTML/PNG，只修改 `renderer_hud.py` 的空间清理专属 CSS/markup，完成四个状态：
   - 扫描前
   - 扫描结果
   - 会话管理
   - 永久删除确认
3. 在 `tests/test_renderer_hud.py` 增加视觉结构合同：
   - 紧凑 segmented control
   - 扫描前居中状态
   - 绿色汇总带、54px 分类行、黄色深度清理行
   - 会话表头和六列
   - 危险图标、三项摘要和独立危险确认结构
4. 运行 focused tests、JS 语法、Python compile/lint。
5. 重启源码 HUD，用现有 CDP 路径在真实 Codex Renderer 截取四张最终状态图；只做 scan/preview/确认界面，不执行真实清理，不删除真实会话。
6. 检查 desktop 和窄窗口：尺寸、行高、列宽、ellipsis、内部滚动、`scrollWidth == clientWidth`、console/window error 为空。
7. 视觉对稿通过后运行全量 pytest、Ruff、compileall、JS syntax、`git diff --check` 和 `git diff --cached --check`。
8. 修复 Trellis 文件行尾问题，精确拆分共享文件的本任务 hunks；不要使用 `git add .`，也不要回退并行改动。
9. 按 Trellis 3.3 更新真正需要固化的 spec；3.4 提交前向用户展示最终 commit plan。用户确认后提交但不推送，建议提交信息：`feat: add safe cleanup and permanent session deletion`。
10. 最后执行 `trellis-finish-work` 归档任务和记录 journal。

## 11. 未解决问题

- 四个 UI 状态尚未按设计稿完成最终视觉修正。
- 最终真实 Renderer 对稿截图尚未生成。
- 窄窗口响应式布局和 padding 计算尚未修正/验证。
- 本轮新增视觉结构测试尚未编写。
- 当前工作树混有多个并行任务，尚未完成精确暂存拆分。
- 暂存区 diff check 因 Trellis 文件行尾空白失败。
- 当前 daemon/CDP 进程是否仍为先前 PID/端口，当前无法确认。

## 12. 风险点、禁止改动项和注意事项

- 禁止对用户真实会话执行永久删除。
- 禁止对真实 `state_5.sqlite`、rollout、session index 做手写删除。
- 禁止在本轮视觉验收中执行真实垃圾清理、DELETE/VACUUM 或生成真实维护计划。
- 禁止把 Qt/Tk 作为新功能承载面或回退方案。
- 禁止新增轮询；Renderer 保持事件驱动。
- 禁止绕过活动任务、进程、路径、revision/token 或备份门禁。
- 禁止 `git add .`、`git reset --hard`、`git checkout --` 或整文件覆盖共享改动。
- `renderer_hud.py`、`cli.py`、`tests/test_ui.py` 等含并行改动，编辑和暂存必须按精确作用域核对。
- 设计 HTML 无需通过浏览器插件打开；本地插件安全策略拒绝属于已知限制，不要绕过。
- UI 对稿需要真实 Codex Renderer 证据，单元测试和静态 HTML 不足以验收。

## 13. 验收标准

- 一级导航显示“空间清理”，页内仅“垃圾清理 / 会话管理”。
- 默认清理流程只有“扫描垃圾”和“确认清理”两个主要动作。
- 扫描前、扫描结果、会话管理、永久删除确认与设计稿在布局、尺寸、层次、颜色和操作栏上相符。
- 桌面会话列表有六列表头；选中行、受保护行、状态 badge、容量列稳定。
- 弹窗无多余大面积空白；列表内部滚动，整体无横向滚动。
- 窄窗口内容不重叠、不溢出、长文本 ellipsis 或合理换行。
- 真实 Renderer 四张状态图通过逐项对比，console/window error 为空。
- 永久删除仍只走官方 `codex delete --force`；当前/运行中/未解析会话不可选。
- focused tests、完整 pytest、Ruff、compileall、JS syntax、staged/unstaged diff check 全部通过。
- 未删除真实会话，未修改真实 SQLite 数据。
- 本任务精确暂存、用户确认 commit plan 后提交，不推送。

## 14. 已出现的所有关键数据

- 设计稿模拟窗口高约 `572px`。
- 紧凑 segmented control：每项至少 `96px`、高约 `31px`。
- 清理/会话稳定行高约 `54px`。
- 永久删除确认卡：宽约 `500px`、圆角 `8px`。
- 当前实拍空间清理弹窗约 `980 x 720px`。
- Renderer v34 会话 inventory：1,014 个主会话、36 个关联子任务。
- Renderer v34 受保护筛选：当前/运行中共显示 2 条受保护会话。
- 会话筛选验证：已选数量从 1 清为 0，按钮禁用。
- Renderer v34：横向溢出为 0，console/window error 为空。
- 历史垃圾扫描：229 个 safe 项，约 `4.0 GB`；286 个受保护项，约 `18 GB`；深度清理约 `629 MB`，默认折叠未选。
- 历史平台扩展扫描：48 个 safe 项，约 `1.1 GB`。
- 真实 `logs_2.sqlite` 历史只读记录：100,564 行，其中约 85,465 行早于 24 小时；未执行 DELETE/VACUUM。
- 设计 PNG：197,335 bytes。
- 未暂存 diff：19 files，+7,100/-721。
- 已暂存 diff：25 files，+7,731/-13。
- 分支：`main...origin/main [ahead 1]`。
- 前一轮运行状态（本次未复核）：HUD PID `15976`、CDP `58803`、Codex PID `28548`。
- 先前实际截图路径记录在 `implement.md`；新会话应重新生成最终截图，不复用旧图宣称完成。

## 颗粒度细化记录

### 细化前

- 结论仅为“功能完成、Renderer v34 通过筛选与无溢出验证、准备提交”。

### 细化后

- 独立对稿将视觉问题拆成弹窗尺寸、分段控件、专属底栏、扫描前空状态、扫描结果行、会话六列表格、危险确认和窄窗响应式八个可执行项。
- 任务明确退回 UI 实施和真实截图验收，不再把“功能结构正确”视为“设计稿一致”。

### 变化原因与结论

用户明确将 UI 一致性设为重要要求；对比设计 PNG 与实际 v34 截图后，差异足以阻止提交。后续必须先完成视觉修正和真实 Renderer 对稿，再进入质量门和提交。
