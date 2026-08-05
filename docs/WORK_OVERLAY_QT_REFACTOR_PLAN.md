# Work Overlay Qt 安全拆分计划

## 1. 目标与边界

拆分前的 `src/codex_usage_hud/ui/work_overlay_qt.py` 为 5,959 行，同时承担纯数据投影、布局计算、主题计算、PySide6 控件、原生热点窗口、文件监听、命令写入、气泡渲染和多阶段动画。当前 facade 已收口为 93 行，旧职责已迁入下列 owner。

本计划的目标是拆分职责和降低回归面，不重做当前已经验收的气泡视觉。Renderer 仍是唯一主 HUD；PySide6 仅保留为桌面气泡 helper，不能重新演变成独立主 HUD。

本计划不处理以下事项：

- 不恢复 `qt_hud.py` 或 `tk_hud.py`。
- 不改变气泡动画的时长、轨迹、颜色、停顿、easing 或阶段顺序。
- 不把 Renderer 性能问题转移到 PySide6。
- 不在拆分阶段顺便重做 hover、工作目录热点或完成勾选交互。
- 不处理本次已接受的 PySide6 包体积问题。

## 2. 已确认的重项结论

1. `WORK_OVERLAY_POINTER_SYNC_MS = 60` 的定时路径只读取指针位置、计算 hover 命中，并在透明度目标变化时更新窗口属性。单次操作轻量，本轮可忽略。
2. `RENDERER_EVENT_IDLE_WAIT_SECONDS = 30.0` 的空闲唤醒不会在无事件时重建 snapshot、全会话扫描或推送 CDP payload。它仍违反最终的零 recurring CPU 目标，但本次发布可接受。
3. Renderer/CLI 巨型文件已经完成拆分。
4. `qt_hud.py`、`tk_hud.py` 已删除；当前剩余结构重项是 `work_overlay_qt.py`。
5. 全扫描和 polling fallback 的性能余量按当前决定跳过。

## 3. 动画冻结契约

以下实现属于拆分期间的冻结区。移动可以，行为调整不可以：

- `WORK_OVERLAY_QT_TRANSITION_ANIMATIONS_ENABLED` 必须保持为 `True`。
- card 到 completed circle 的 shrink、pause、move、shift 阶段及持续时间。
- completed circle 恢复为 card 的 fade、shift、descend 阶段。
- completed dismiss 的能量环湮灭效果。
- 多项目同时发生形态变化时，当前只串行处理额外 transition 的策略。
- switch pending 的启动、完成、超时、粒子轨道、caption 透明度和 scale 曲线。
- shimmer 文本动画、completed badge 绘制、卡片让路轨迹。
- transition watchdog、临时 widget 清理和完成后热点重建时序。
- Windows 原生热点窗口的透明度和命中行为。

第一轮拆分禁止修改任何动画数值。若必须修改数值，应停止当前机械拆分，单独建立视觉变更任务。

## 4. 目标模块结构

保留 `ui/work_overlay_qt.py` 作为兼容 facade，并在其运行入口内部延迟导入 PySide6 owner，确保普通 CLI/Renderer 导入不会提前加载 Qt。

| 目标模块 | 主要职责 | 目标规模 |
|---|---|---:|
| `ui/work_overlay_qt.py` | 显式兼容导出、延迟调用 Qt runtime | 不超过 120 行 |
| `ui/work_overlay/constants.py` | 尺寸、时长、主题默认值和小型类型 | 约 100-180 行 |
| `ui/work_overlay/model.py` | item 标准化、休息提醒、排序、过滤、elapsed 文本 | 约 450-600 行 |
| `ui/work_overlay/geometry.py` | rect、slot、transition 检测、插值和轨迹纯函数 | 约 750-950 行 |
| `ui/work_overlay/theme.py` | 颜色、对比度、palette 和 payload signature | 约 350-500 行 |
| `ui/work_overlay/qt_hotspots.py` | close、workdir、check 和 action 原生热点窗口 | 约 300-450 行 |
| `ui/work_overlay/qt_visuals.py` | shimmer、pending、energy ring、completed badge | 约 950-1,200 行 |
| `ui/work_overlay/qt_rendering.py` | card/badge 构建更新、布局和 widget geometry | 约 750-1,000 行 |
| `ui/work_overlay/qt_transitions.py` | animation group、phase、watchdog 和收尾 | 约 900-1,150 行 |
| `ui/work_overlay/qt_window.py` | OverlayWindow 生命周期、命令、状态和 owner 组装 | 约 650-900 行 |
| `ui/work_overlay/qt_runtime.py` | QApplication、watcher、retry、heartbeat、pointer timer | 约 250-400 行 |

目标不是追求最少行数，而是让动画、渲染、交互、运行时 IO 和纯计算各自只有一个明确 owner。

## 5. 分阶段执行计划

### P0：冻结基线

状态：已完成。证据见 `docs/WORK_OVERLAY_QT_P0_BASELINE.md` 和
`docs/WORK_OVERLAY_QT_P0_PURE_OUTPUTS.json`。

执行：

1. 运行当前相关自动化测试并保存命令结果。
2. 使用 `tools/demo_work_overlay_transition.py` 录制拆分前真实效果。
3. 覆盖 card -> completed、第二个并发变化、completed -> card、dismiss、switch pending 启动/完成。
4. 记录当前全部动画常量、阶段顺序和关键纯函数输出。
5. 确认默认 Python 导入不会加载 `PySide6` 或 `ui.work_overlay_qt`。

退出条件：基线证据完整；若当前真实动画已经异常，先修复现状，不进入拆分。

### P1：迁出纯函数

状态：已完成。`constants.py`、`model.py`、`geometry.py`、`theme.py` 已完成机械迁移，兼容 facade 保留原公开导出。

执行：

1. 新建 `constants.py`、`model.py`、`geometry.py`、`theme.py`。
2. 只做机械移动和导入调整，不改变表达式、常量和值域。
3. `work_overlay_qt.py` 暂时保留显式 re-export，避免演示工具和现有测试一次性失效。
4. 将纯函数测试迁移到对应 owner 测试文件。

预计效果：先移走约 1,700 行，不触碰任何 QWidget、QTimer、paintEvent 或 animation group。

退出条件：所有纯函数结果与拆分前完全一致，完整测试通过。

### P2：迁出独立 Qt 叶子组件

状态：已完成。热点组件迁入 `qt_hotspots.py`，视觉组件迁入 `qt_visuals.py`，未改变 paint/timer 常量和行为。

执行：

1. 将 `CloseButtonWindow`、`WorkdirLinkWindow`、`ClickHotspotWindow` 移到 `qt_hotspots.py`。
2. 将 `ShimmerTextLabel`、`CardSwitchPendingOverlayWidget`、`EnergyRingAnnihilationWidget`、`CompletedBadgeWidget` 移到 `qt_visuals.py`。
3. `paintEvent`、timer interval、颜色和几何公式原样移动。
4. Qt owner 只由 `qt_runtime.py` 在 helper 启动后延迟导入。

退出条件：真实热点点击、completed 勾选、pending、shimmer 和完成圆盘效果均与基线一致。

### P3：拆分 OverlayWindow 的非动画职责

状态：已完成。渲染和布局 owner 为 `qt_rendering.py`，状态/命令/生命周期保留在 `qt_window.py`。

执行：

1. 将卡片和 completed row 的构建、更新、geometry 同步迁入 `qt_rendering.py`。
2. 将状态读取、命令写入、heartbeat、runtime error、hover/pointer 和热点池管理留给 `qt_window.py` 或注入的小 owner。
3. 动画方法仍保留在原位置，不同时改变 transition 状态字段。
4. 保持 bounded native window pool 策略，禁止恢复反复 close/recreate 的实现。

退出条件：OverlayWindow 非动画行为通过真实 PySide6 lifecycle 测试，动画仍通过基线对照。

### P4：机械迁移动画冻结区

状态：已完成。动画、phase、watchdog 和 cleanup 已整体迁入 `qt_transitions.py`；与 HEAD 的 AST 对照无差异。

执行：

1. 将 `_animate_*`、`_start_*_transition`、各 phase、watchdog 和 `_end_transition` 整体迁入 `qt_transitions.py`。
2. 第一版优先使用窄 mixin 或等价的机械 owner，保持现有 `self` 字段和调用顺序。
3. 不在本阶段改写为全新状态机或 TransitionController。
4. 动画迁移完成后，`qt_window.py` 只负责生命周期和组合。

退出条件：全部自动化、真实 demo、录屏逐场景对比通过；任一轨迹或时序差异都必须回退本阶段。

### P5：兼容收口与可选轻量优化

状态：已完成（代码门禁和手动视觉验收均通过；真实 demo harness 的退出问题已记录并跳过重复重试）。

执行：

1. 将仓库内测试和演示工具迁移到真实 owner。
2. `work_overlay_qt.py` 只保留明确兼容表面和 lazy runtime entry。
3. 增加架构测试，禁止普通 CLI/Renderer 导入提前加载 PySide6。
4. 可选优化 pointer timer：overlay 隐藏或无锚点时停止；远离气泡时退避到 250ms；接近或透明度变化时恢复 60ms。

注意：pointer timer 优化不是本次拆分验收的必要条件，不应与动画迁移混在同一提交。

当前 P5 证据：

- `work_overlay_qt.py` 为 93 行，仅保留纯 owner re-export、兼容 helper 和 lazy runtime entry。
- facade 导入不会加载 `PySide6` 或 `qt_runtime`；`qt_runtime.py` 只在 helper 启动后导入 `QApplication`、watcher、timer 和 `OverlayWindow`。
- 新增架构测试覆盖 facade 尺寸、惰性导入、纯 owner 无 Qt 依赖和 runtime 导入方向。
- 已通过 `tests/test_ui.py -k "work_overlay or transition"`、架构/边界/桌面 overlay 聚焦测试、compileall 和 `git diff --check`。
- 已通过 `python -m pytest -m ui`（38 skipped, 1243 deselected）和一次完整 `python -m pytest -q`。
- 拆分后真实 demo 已唯一尝试，约 184 秒后因控制器未退出而被超时终止；动画日志在终止前已推进到连续 `rect_to_circle` 完成状态。该 harness 限制不作为产品失败，用户已完成人工视觉验收；本任务残留 PID 已停止，不再重复尝试。
- 按用户决定跳过 pointer timer 优化；不扩大本次拆分范围。

## 6. 每阶段验证门禁

至少执行：

```powershell
python -m pytest tests/test_desktop_overlay.py tests/test_overlay_window.py tests/test_overlay_transition_audit.py -q
python -m pytest tests/test_ui.py -k "work_overlay or transition" -q
python -m pytest -m ui
python -m pytest -q
python -m compileall -q src tests tools
git diff --check
```

视觉验收必须使用真实 PySide6 helper，而不仅是单元测试：

```powershell
python tools/demo_work_overlay_transition.py --auto --once --scale 1 --speed 1
```

每阶段至少检查：

- card -> completed circle。
- 多卡片连续完成时的让路和串行 transition。
- completed circle -> running card。
- completed dismiss 能量环。
- switch pending 启动、完成和超时。
- hover 透明度、关闭按钮、工作目录和完成勾选热点。
- 休息提醒气泡的倒计时和操作按钮。

## 7. 停止与回退条件

出现以下任一情况立即停止当前阶段：

- 动画时长、轨迹、颜色、停顿或顺序出现可见变化。
- completed 勾选、工作目录或关闭热点丢失、错位或不可点击。
- helper 退出后遗留窗口或 USER handle 数量持续增长。
- 普通 CLI/Renderer 导入开始提前加载 PySide6。
- watcher transient read retry 或 heartbeat 行为退化。
- 默认测试通过但 `python -m pytest -m ui` 失败。

回退只回退当前机械阶段，不重置或覆盖用户其他工作区修改。

## 8. 提交策略

建议每个阶段单独提交，提交前保持工作树边界清晰：

1. P0 只提交基线/契约测试或文档。
2. P1 只提交纯 owner 抽取。
3. P2 只提交 Qt 叶子组件移动。
4. P3 只提交非动画 OverlayWindow 拆分。
5. P4 单独提交动画 owner 机械移动。
6. P5 单独提交兼容收口；pointer 优化另开提交。

禁止把动画调整、视觉改版、性能优化和机械拆分混入同一提交。
