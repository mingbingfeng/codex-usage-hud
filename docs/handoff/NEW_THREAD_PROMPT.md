只在当前 workspace root `E:\Project\codex-usage-hud` 内继续工作。先完整阅读 `docs/handoff/HANDOFF.md` 和 `docs/WORK_OVERLAY_QT_REFACTOR_PLAN.md`，再核对当前 workspace 的 `AGENTS.md`、Git 状态和相关文件；以当前 workspace 文件和 Git 证据为准。目标是完成 `work_overlay_qt.py` 安全拆分的 P5 最终收口：Renderer/CDP 必须继续是唯一主 HUD，必须保留 PySide6 桌面气泡 helper，不能恢复 `qt_hud.py`/`tk_hud.py`，不能改变动画时长、轨迹、颜色、透明度、easing、停顿、phase 顺序或热点行为。

当前 HEAD 为 `60142c42bf5926bce85085a14a7b92c6005f0329`，`main` 相对 `origin/main` ahead 3。P0-P5 已完成：facade 为 93 行，纯 owner、热点、视觉、渲染、动画、window、runtime owner 已建立；架构测试、overlay 聚焦测试、边界/桌面 overlay 测试、compileall、diff check 和人工视觉验收均已通过。P0 真实基线见 `docs/WORK_OVERLAY_QT_P0_BASELINE.md`。pointer 优化、Qt 包体积和全扫描/polling fallback 按用户决定跳过。

最终门禁已完成：`python -m pytest -m ui`、一次完整 `python -m pytest -q`、完整 compileall、diff check 和人工视觉验收均通过。拆分后 `tools/demo_work_overlay_transition.py` 已唯一尝试，约 184 秒后因 harness 控制器未退出而超时；流程日志已推进到连续 `rect_to_circle` 完成状态，残留 PID 已停止，按用户要求不再重复。不要 push、amend、rebase、reset、清理用户改动，也不要把 pointer 优化、视觉改版或新功能混入本次拆分。
