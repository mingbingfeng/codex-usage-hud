# 进度日志

## 会话：2026-06-18

### 运行约定 / 项目记忆
- 后续修改 `codex-usage-hud` 源码后，默认重启步骤固定为：
  1. 先检查是否已有 HUD 进程在运行
  2. 若有，先关闭当前运行实例
  3. 再启动编译版/源码版，而不是安装版
- 本机源码版当前约定启动方式：
  - 工作目录：`D:\AI\codex-hud\codex-usage-hud`
  - 启动命令语义：`pythonw -m codex_usage_hud --daemon --no-startup-prompt`
- 已踩坑记录：
  - Renderer 顶部折叠条的横向滚动不能在每次 HUD 刷新时先清空再重建 overflow 动画状态，否则动画时间轴会不断重置，视觉上会像“完全不滚动”。

### 阶段 1：需求与发现
- **状态：** complete
- **开始时间：** 2026-06-18T16:26:54+08:00
- 执行的操作：
  - 读取 `guihua` 技能说明。
  - 确认项目根目录此前没有 `task_plan.md`、`findings.md`、`progress.md`。
  - 运行 session catchup，无额外输出。
  - 检查当前 git 状态，确认已有多处用户/前序改动，按当前工作树继续。
  - 阅读动画核心函数和现有测试。
- 创建/修改的文件：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### 阶段 2：规划与结构
- **状态：** complete
- 执行的操作：
  - 把用户强调的垂直路径、让路回位、反向恢复整理为实现约束。
  - 明确目标测试集中在 `WorkOverlayTransitionTests`。
- 创建/修改的文件：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### 阶段 3：实现
- **状态：** complete
- 执行的操作：
  - 修改完成路径：收缩圆右边缘锚定到卡片右边缘，移动阶段 x 不变、只改变 y。
  - 修改恢复路径：顶部圆沿同一右侧轨道返回，再展开成矩形。
  - 增加过渡期矩形让路偏移和已完成圆 slot shift 时序。
  - 增加完成前卡片矩形记忆，用于恢复路径目标位置。
  - 完成态稳定圆短暂简化为 check 圆，后续已按用户要求恢复信息型徽章。
  - 将恢复过渡改为蓝色恢复态，恢复展开阶段内容渐入。
- 创建/修改的文件：
  - `src/codex_usage_hud/ui/work_overlay_qt.py`
  - `tests/test_ui.py`

## 测试结果
| 测试 | 输入 | 预期结果 | 实际结果 | 状态 |
|------|------|---------|---------|------|
| `python -m pytest tests/test_ui.py -k WorkOverlayTransitionTests -q` | 过渡相关单测 | 全部通过 | 17 passed, 162 deselected | pass |
| `python -m compileall -q src tests tools` | 语法编译 | 无语法错误 | 通过 | pass |
| 坐标复核脚本 | 正/反向关键进度 | 圆形阶段 x 固定，只改变 y | 正向/反向圆形阶段 x 均为 262 | pass |
| `python -m pytest tests/test_ui.py -q` | 全量 UI 单测 | 全部通过 | 178 passed, 1 failed；失败为 `TkSnapshotPumpTests.test_snapshot_pump_is_single_flight_while_worker_is_busy` 等待线程启动超时 | investigating |
| `python -m pytest tests/test_ui.py -k WorkOverlayTransitionTests -q` | 更新后的过渡相关单测 | 全部通过 | 18 passed, 162 deselected | pass |
| `git diff --check` | diff 空白检查 | 无 whitespace error | 通过 | pass |
| `python .\tools\demo_work_overlay_transition.py --scale 3 --once` | 慢速 demo | 完整执行并退出 | 5/5 stable final state, finished; closing overlay | pass |
| `python -m pytest tests/test_ui.py -q -k "not TokenHudWindowLifecycleTests and not TkSnapshotPumpTests"` | 排除当前 Tk 环境问题后的 UI 测试 | 全部通过 | 122 passed, 58 deselected | pass |
| `python -m pytest tests/test_ui.py -k "completed_task_requires_seen_running_overlay_before_showing_completed or historical_completed_overlay_item_does_not_show_on_startup or only_historical_completed_overlay_items_do_not_show_on_startup" -q` | 启动完成态过滤 | 历史完成项启动不显示，运行后完成仍显示 | 3 passed, 178 deselected | pass |
| `python -m pytest tests/test_ui.py -k WorkOverlayTransitionTests -q` | 启动过滤改动后复查动画过渡 | 全部通过 | 18 passed, 163 deselected | pass |
| `python -m compileall -q src tests tools` | 启动过滤改动后语法编译 | 无语法错误 | 通过 | pass |
| `git diff --check` | 启动过滤改动后 diff 空白检查 | 无 whitespace error | 通过 | pass |
| `python -m pytest tests/test_ui.py -k WorkOverlayTransitionTests -q` | 恢复完成态信息徽章后复查动画过渡 | 全部通过 | 18 passed, 163 deselected | pass |
| `python -m compileall -q src tests tools` | 恢复完成态信息徽章后语法编译 | 无语法错误 | 通过 | pass |
| `git diff --check` | 恢复完成态信息徽章后 diff 空白检查 | 无 whitespace error | 通过 | pass |

## 错误日志
| 时间戳 | 错误 | 尝试次数 | 解决方案 |
|--------|------|---------|---------|
| 2026-06-18T16:xx:xx+08:00 | `TkSnapshotPumpTests.test_snapshot_pump_is_single_flight_while_worker_is_busy` 等待 `started` 事件超时 | 1 | 单独重跑该测试判断是否为偶发时序问题 |
| 2026-06-18T16:xx:xx+08:00 | demo 中 `_prepare_completed_badge_moves` 局部 `target_rect` 覆盖传入的 `QRectF`，导致 tuple 没有 `.top()` | 1 | 将局部变量重命名为 `target_slot_rect` |
| 2026-06-18T16:xx:xx+08:00 | Python 3.14 Tk 环境缺少 `init.tcl`，全量 UI 测试中的 Tk 窗口用例无法可靠运行 | 1 | 记录环境限制；用目标测试、compileall、demo、排除 Tk 环境用例的 UI 测试验证本次改动 |

## 五问重启检查
| 问题 | 答案 |
|------|------|
| 我在哪里？ | 阶段 7：完成态信息徽章恢复完成 |
| 我要去哪里？ | 等待用户复核或后续要求 |
| 目标是什么？ | 完成气泡动画符合设计和用户补充要求，启动不显示历史完成圆，完成态保留上一版信息徽章 |
| 我学到了什么？ | 见 findings.md |
| 我做了什么？ | 收紧完成态 seen-key 过滤，补启动历史完成项回归测试，恢复完成态信息徽章并复查动画测试 |

---
*每个阶段完成后或遇到错误时更新此文件*
