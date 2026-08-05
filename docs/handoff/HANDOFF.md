# 项目交接

- 本次交接时间：2026-08-04 12:43:55 +08:00（Asia/Shanghai）
- 当前任务名：`work_overlay_qt.py` 安全拆分与气泡动画保持
- 当前小项目/模块：PySide6 桌面工作气泡 helper
- 本次交接已覆盖上一次交接。

## 1. 当前项目最终目标

Renderer/CDP 保持唯一主 HUD；PySide6 只承担桌面气泡 helper。当前已完成 `work_overlay_qt.py` 的 P0-P5 职责拆分和人工视觉验收，降低拆分前约 5,959 行单文件带来的维护和回归风险，同时保持已接受的气泡动画、热点点击、hover 透明度、休息提醒及状态读取行为。

## 2. 当前正在处理的问题

当前代码审查确认：

- 原 5 个重项中，Renderer/CLI 巨型文件已经解决；全扫描/polling fallback 性能余量按用户决定跳过。
- 60ms pointer sync 单次仅取鼠标坐标、做 hover hit-test，并只在透明度目标变化时写窗口属性，当前接受，不作为发布阻断。
- Renderer 无其他 deadline 时最多 30 秒唤醒一次。空 wake 会采样更新状态、提醒、预算窗口和事件队列，但无事件时不会重建 snapshot、全会话扫描或推送 CDP payload，当前接受为技术债。
- 独立 `qt_hud.py`、`tk_hud.py` 已删除；剩余主要结构重项是 `src/codex_usage_hud/ui/work_overlay_qt.py`。
- 用户要求生成拆分计划，并且不能影响当前气泡动画效果。

## 3. 已完成的修改

### 已提交

- `ddfe29d 删除独立 Qt/Tk HUD`
  - 删除独立 Qt/Tk 主 HUD。
  - 保留 PySide6 桌面气泡能力。
- `c71c8f7 重构 Renderer 与运行时模块边界`
  - 完成 Renderer/runtime/overlay 大规模模块化拆分。
  - 清理一次性迁移文件和旧交接文档。
- `60142c4 fix`
  - 修复删除会话时 `Pending deleted-session usage snapshot is unavailable.` 竞态。
  - committed 行保存已处理 transaction ID；只有能证明已由恢复路径提交的事务才幂等成功。
  - 未知 transaction ID 仍抛出原错误，避免吞掉真实用量丢失。
  - 增加账本级和 `SessionCleanupManager.execute()` 集成回归测试。

### 本次新建文档和拆分 owner

- `docs/WORK_OVERLAY_QT_REFACTOR_PLAN.md`
  - 完整记录目标模块、动画冻结边界、P0-P5 阶段、验收门禁和回退条件。
- `docs/handoff/HANDOFF.md`
- `docs/handoff/NEW_THREAD_PROMPT.md`
- `src/codex_usage_hud/ui/work_overlay/` 下的纯 owner、Qt 叶子 owner、渲染 owner、动画 owner、window owner 和 runtime owner。

当前拆分仍未提交；未执行 push、rebase、amend、reset 或用户进程清理。

## 4. 关键文件列表

- `AGENTS.md`：Renderer 唯一主 HUD、事件驱动和构建日志约束。
- `docs/RENDERER_MODE_STRATEGY.md`：Renderer/overlay 架构规范。
- `docs/WORK_OVERLAY_QT_REFACTOR_PLAN.md`：下一阶段唯一执行计划。
- `src/codex_usage_hud/ui/work_overlay_qt.py`：当前 93 行兼容 facade。
- `src/codex_usage_hud/ui/work_overlay/qt_window.py`：OverlayWindow 生命周期、状态、命令和 owner 组合。
- `src/codex_usage_hud/ui/work_overlay/qt_runtime.py`：QApplication、watcher、retry、heartbeat 和 pointer timer。
- `src/codex_usage_hud/ui/work_overlay/qt_rendering.py`、`qt_transitions.py`、`qt_visuals.py`、`qt_hotspots.py`：对应 Qt owner。
- `src/codex_usage_hud/desktop_overlay.py`：父进程侧 helper supervision、状态发布和 15 秒 keepalive。
- `src/codex_usage_hud/desktop_overlay_setup.py`：helper 启动入口和 20 秒 stale 配置。
- `src/codex_usage_hud/runtime_orchestration.py`：30 秒 Renderer idle wait 常量和 composition root。
- `src/codex_usage_hud/renderer_event_loop.py`：空 wake 后的采样、refresh decision 和等待逻辑。
- `tools/demo_work_overlay_transition.py`：真实 PySide6 动画演示和人工验收工具。
- `tests/test_ui.py`：当前大量 overlay 纯函数、helper lifecycle 和真实 Qt 回归测试。
- `tests/test_desktop_overlay.py`：父进程侧状态发布和 transition audit 测试。
- `tests/test_overlay_window.py`：overlay 窗口/refocus 行为测试。
- `tests/test_overlay_transition_audit.py`：transition audit owner 测试。
- `src/codex_usage_hud/core/deleted_usage.py`：已提交的删除会话用量幂等修复。
- `tests/test_session_cleanup.py`：已提交的删除会话集成竞态回归测试。

## 5. 当前 Git 状态摘要

- 仓库：`E:\Project\codex-usage-hud`
- 分支：`main`
- HEAD：`60142c42bf5926bce85085a14a7b92c6005f0329`，提交标题 `fix`
- 相对 `origin/main`：ahead 3
- 交接文档生成前：工作树干净
- 当前工作树包含拆分 owner、计划/基线/handoff 文档，以及 facade 和测试的未提交修改。
- 未执行 push、rebase、amend 或 reset。

## 6. `git diff --stat` 摘要

当前 tracked diff 主要是 facade 收口和测试 owner 路径/架构门禁；新建 owner、计划、基线和 handoff 文件仍为 untracked，因此普通 `git diff --stat` 不显示其内容。

## 7. 重要 diff 变化说明

### `60142c4 fix`

- `src/codex_usage_hud/core/deleted_usage.py`：新增 transaction ID 规范化、committed transaction 识别和幂等 commit；44 行新增。
- `tests/test_session_cleanup.py`：新增删除后用量刷新抢先 recovery 的管理器级竞态测试；74 行变化。
- `tests/test_ui.py`：扩展 pending recovery 测试，验证重复 commit 不重复事件且未知 transaction 仍报错；18 行变化。
- 合计：3 文件，134 insertions、2 deletions。

### 本次文档

- `docs/WORK_OVERLAY_QT_REFACTOR_PLAN.md`：新建，不改变运行行为。
- `docs/handoff/HANDOFF.md`、`NEW_THREAD_PROMPT.md`：完整覆盖交接内容，不改变运行行为。

## 8. 已尝试的优化方案及结果

- 删除会话 usage commit 幂等修复：`成功`。聚焦测试、Renderer/UI 组合、完整 pytest、compileall 和 diff check 均通过后已提交。
- `codebase-memory-mcp` full 索引重建：`成功`。状态 `ready`，4,992 nodes、21,079 edges，可检索到新增幂等方法。
- 共享压缩索引 artifact：`失败/未生成`。服务返回 `artifact_present=false`，本机索引仍可用。
- Renderer/CLI 大文件拆分：`成功`。已提交为 `c71c8f7`。
- 独立 Qt/Tk HUD 删除：`成功`。已提交为 `ddfe29d`。
- 60ms pointer sync 影响审查：`成功`。确认单次轻量，本轮决定忽略；未改代码。
- 30 秒 Renderer idle wake 影响审查：`成功`。确认无事件时不做 snapshot/scan/CDP push，本轮接受；未改代码。
- `work_overlay_qt.py` 拆分：`P0-P5 代码、自动化门禁和人工视觉验收已完成`。facade 93 行，owner 已建立；真实 demo harness 退出问题已记录为工具限制。
- 拆分前动画录屏和确定性视觉基线：`已验证`，证据见 `docs/WORK_OVERLAY_QT_P0_BASELINE.md`。
- 当前聚焦/架构/边界/桌面 overlay 测试、UI marker、完整 pytest、compileall 和 diff check：`已通过`。
- 拆分后真实 demo：`已唯一尝试但 harness 约 184 秒未退出`；日志推进到连续 `rect_to_circle` 完成状态后卡住，用户已完成人工视觉验收，已停止本任务残留 PID，按用户要求不再重复。
- pointer timer 事件驱动或退避优化：`按用户决定跳过`，不属于本次拆分。

## 9. 后续可能继续优化的方向

- 完成 `work_overlay_qt.py` 的 P0-P5 安全拆分。
- 拆分稳定后，可单独让 pointer timer 在隐藏/无锚点时停止，远离气泡时退避。
- 长期将 Renderer 空闲等待从 30 秒上限改为完全由 deadline/event 驱动。
- 长期减少 desktop overlay 15 秒状态 keepalive 写入，但必须先建立等价 helper liveness 机制。
- 若未来处理打包体积，再单独调整 PySide6/Qt 打包策略；当前用户已明确跳过。

## 10. 下一步最小执行计划

严格以 `docs/WORK_OVERLAY_QT_REFACTOR_PLAN.md` 为准：

1. 当前任务无剩余必需代码工作；不再重复自动 demo harness。
2. 如继续独立工作，只处理用户明确提出的后续问题；不做 pointer 优化，不 push。

## 11. 未解决问题

- P5 的 UI marker、完整 pytest、compileall 和 diff check 已通过。
- 拆分后自动 demo 已尝试一次，约 184 秒后出现“动画/状态流程推进但控制器未退出”的工具卡点；用户已完成手动视觉验收，本任务启动的残留 PID 已终止，不再重复。
- 60ms pointer timer 和 Renderer 30 秒 idle wake 仍存在，但当前已接受。
- desktop overlay 有 15 秒 keepalive 状态写入；影响低，但不满足最终零 recurring work 方向。
- PySide6/Qt 包体积问题按用户要求跳过。
- macOS 上的气泡动画和热点拆分后验收条件当前无法确认，需按 `docs/MACOS_VALIDATION.md` 补证据。
- 当前计划/交接文档尚未提交。

## 12. 风险点、禁止改动项和注意事项

- Renderer/CDP 是唯一主 HUD，禁止恢复 `qt_hud.py`、`tk_hud.py`。
- 必须保留 `work_overlay_qt.py` 所代表的 PySide6 桌面气泡能力。
- 拆分阶段禁止改变动画时长、轨迹、easing、颜色、透明度、停顿和 phase 顺序。
- 禁止把机械拆分与视觉改版、pointer 优化或新功能混入同一提交。
- 禁止仅凭单元测试宣称动画无回归；必须运行真实 helper 和 demo。
- 普通 CLI/Renderer 导入不得提前加载 PySide6。
- 保持 Windows native hotspot 的 bounded pool；禁止反复 close/recreate 导致 USER handle 泄漏。
- 保持 watcher transient-read retry、heartbeat、stale shutdown 和 state/command sidecar 语义。
- 不使用 `git reset --hard`、`git checkout --` 或无差别清理覆盖用户修改。
- 当前分支领先远端 3 个提交，未经授权不得 push。
- 当前 `60142c4` 提交标题仅为 `fix`；是否改写提交信息未获授权，不要擅自 rebase/amend。

## 13. 验收标准

每个拆分阶段必须满足：

- 相关纯函数和 owner 测试通过。
- `python -m pytest tests/test_desktop_overlay.py tests/test_overlay_window.py tests/test_overlay_transition_audit.py -q` 通过。
- `python -m pytest tests/test_ui.py -k "work_overlay or transition" -q` 通过。
- `python -m pytest -m ui` 通过。
- 完整 `python -m pytest -q` 通过，只有既有 skip。
- `python -m compileall -q src tests tools` 通过。
- `git diff --check` 通过。
- CLI/Renderer import smoke 确认不提前加载 PySide6。
- 真实 demo 中 card/completed/dismiss/pending/hover/hotspot 与拆分前录屏一致。
- 任一可见动画差异、点击区域错位、helper 资源泄漏或 watcher 回归都视为阶段失败。

## 14. 已出现的所有关键数据

- `work_overlay_qt.py` facade：93 行；拆分前基线：5,959 行。
- 当前 owner 行数：`constants.py` 122、`model.py` 514、`geometry.py` 763、`theme.py` 391、`qt_hotspots.py` 384、`qt_visuals.py` 1,172、`qt_rendering.py` 1,085、`qt_transitions.py` 933、`qt_window.py` 918、`qt_runtime.py` 171。
- pointer sync：60ms，约 16.7 次/秒，仅 helper 存活时运行。
- Renderer idle wait 上限：30.0 秒；存在更早 deadline 时会提前唤醒。
- desktop overlay keepalive：15.0 秒。
- helper stale 配置：20.0 秒。
- 当前 HEAD：`60142c42bf5926bce85085a14a7b92c6005f0329`。
- 当前分支相对远端：ahead 3。
- 删除会话修复提交：3 文件，134 insertions、2 deletions。
- full codebase-memory index：4,992 nodes、21,079 edges、`ready`。
- index artifact：`artifact_present=false`。
- 删除会话修复验证：
  - `tests/test_session_cleanup.py`：16 项通过。
  - `tests/test_ui.py -k deleted_session_ledger`：2 项通过。
  - Renderer/active-session/UI 组合测试：通过，只有既有 skip。
  - 完整 pytest：通过，只有既有 skip。
  - compileall、`git diff --check`：通过。
- 当前气泡动画关键常量：
  - completed badge animation：520ms。
  - card shrink：220ms。
  - transition pause：140ms。
  - move：280ms。
  - shift：240ms。
  - completed annihilation：1,200ms。
  - shimmer timer：30ms。
  - switch pending timer：120ms。

## 15. 计划颗粒度细化记录

### 细化前

- 单一 `work_overlay_qt.py`：5,959 行。
- 一个 `run_work_overlay_helper_qt()` 内定义 8 个 Qt 类和大量闭包状态。
- `OverlayWindow` 单类约 2,600 行，同时承担命令、状态、render、geometry、animation 和 native hotspot 管理。

### 细化后目标

- 1 个不超过约 120 行的 facade。
- 4 个无 Qt 的纯 owner：constants、model、geometry、theme。
- 6 个 Qt owner：hotspots、visuals、rendering、transitions、window、runtime。
- 单个目标模块原则上不超过约 1,200 行。

### 变化原因

- 先隔离纯计算可以在完全不触碰 QWidget/animation 的情况下减少约 1,700 行。
- 独立热点和 visual widget 可以机械移动并保留原 paint/timer 逻辑。
- transition 最后单独移动，可以将动画回归集中在一个可回退阶段。

### 对比结论

该颗粒度优先保护动画行为，不追求一次性重写。第一轮只建立 owner 边界；更彻底的 TransitionController 或 pointer 事件驱动优化必须在拆分和真实视觉验收完成后另开任务。
