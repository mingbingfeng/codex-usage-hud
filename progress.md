# 进度日志：PySide6 桌面级会话气泡恢复

## 会话：2026-06-26

### 本次推进目标
按用户给定计划恢复 PySide6 桌面级会话气泡：主 HUD 继续 renderer-only，会话方形气泡/完成态圆气泡通过可选 PySide6 helper 恢复；未安装 PySide6 时不影响 renderer HUD。

### 本次已完成
- **状态：** 自动化实现完成；Windows/macOS 实机 PySide6 overlay 验证待做。
- 读取 `$guihua`、`$zhongwen` 技能说明。
- 恢复并修正三份任务文档：
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
- 确认用户最终方向：后续长期采用 Qt/PySide6 桌面级气泡，不采用沿 Codex App 边框绘制方案。
- `DesktopWorkOverlay` 恢复为 optional PySide6 desktop overlay。
- renderer 会话重新按 `_work_overlay_item_limit_for_context(context)` 创建 overlay。
- `work_overlay_max_items <= 0` 时桌面气泡保持关闭。
- PySide6 不可用时不启动 helper，并记录一次 `work_overlay_unavailable` renderer diagnostic。
- helper 异常退出或启动失败后增加 60s backoff。
- `work_overlay_qt.py` 改为 helper 路径惰性导入，默认 CLI import 不加载 PySide6 / Qt overlay 模块。
- `pyproject.toml` 新增 optional extra：`desktop-overlay = ["PySide6>=6.8"]`。
- README / README_EN 明确 `codex-usage-hud[desktop-overlay]` 和 `work_overlay_max_items` 的含义。
- renderer 设置页文案改为“PySide6 桌面气泡数量（0 为关闭）”，并新增“气泡依赖 PySide6”状态块。
- 增加 PySide6 可用时 helper 启动单测。
- 移除 renderer 设置页里的“HUD 显示方案”字段和选项。
- “PySide6 桌面气泡数量（0 为关闭）”字段已左移占用原显示方案位置。
- 新增“气泡依赖 PySide6”状态块：
  - 已安装时显示 PySide6 版本号。
  - 未安装时显示“需要安装环境”、立即安装、已安装立即启用。
  - 需要重启时显示“立即重启”。
- 新增 `installDesktopOverlay` 和 `enableDesktopOverlay` 设置命令。

### 已验证
| 验证 | 结果 | 备注 |
|------|------|------|
| `python -m pytest tests/test_renderer_hud.py tests/test_ui.py::DaemonLifecycleTests::test_renderer_install_desktop_overlay_starts_optional_dependency_install tests/test_ui.py::DaemonLifecycleTests::test_renderer_enable_desktop_overlay_rechecks_and_enables_without_restart tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_skips_when_pyside6_unavailable tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_starts_when_pyside6_available -q` | passed | 覆盖设置页脚本、新安装/启用命令和 overlay 可用性分支 |
| `python -m pytest tests/test_config.py tests/test_renderer_hud.py tests/test_build_exe.py tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_skips_when_pyside6_unavailable tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_starts_when_pyside6_available tests/test_ui.py::DaemonLifecycleTests::test_run_renderer_hud_session_drains_work_overlay_commands_with_window_prep tests/test_ui.py::DaemonLifecycleTests::test_cli_import_does_not_eagerly_import_qt_hud -q` | passed | 覆盖 optional overlay、导入隔离、renderer 会话创建 overlay |
| `python -m pytest tests/test_renderer_hud.py tests/test_ui.py tests/test_build_exe.py tests/test_platforms.py -q` | passed | 覆盖 renderer 设置、UI 回归、打包命令、平台 mock |
| `python -m pytest tests/test_renderer_hud.py tests/test_ui.py tests/test_build_exe.py -q` | passed | 设置页调整后的 UI/打包相关回归 |
| `python -m pytest -q` | passed | 默认全量单测通过 |
| `python -m compileall -q src tests tools` | passed | Python 编译检查通过 |
| `git diff --check` | passed | 无 whitespace 错误 |

### 剩余工作
- Windows 手动验证：安装 PySide6 后运行 `codex-hud --daemon`，验证方形运行气泡、完成态圆气泡、dismiss、点击切换会话、renderer HUD 注入。
- macOS 手动验证：安装 PySide6 后运行 `codex-hud --daemon`，验证 Codex debug/CDP 启动、PySide6 overlay 置顶、点击切换会话、renderer HUD 注入。
- 后续发行策略：决定 Windows/macOS 安装包是否内置 PySide6，或继续只通过 optional extra 提供桌面气泡。
- 后续平台化自动更新 UI/逻辑，避免 macOS 复用 Windows installer 语义。

### 手动验证命令
```powershell
python -m pip install -e ".[desktop-overlay]"
codex-hud --daemon
```

确认项：
- renderer HUD 注入 Codex App。
- 运行中的会话显示方形桌面气泡。
- 完成会话收缩为圆形完成态气泡。
- dismiss 后不会重复出现已关闭气泡。
- 点击气泡触发 `activateSession` 并切换到目标会话。
- 关闭 HUD 后 helper 子进程和 state file 清理正常。

## 错误日志
| 时间戳 | 错误 | 尝试次数 | 解决方案 |
|--------|------|---------|---------|
| 2026-06-26 | PowerShell 不支持 Bash here-doc `python - <<'PY'` | 1 | 改用 PowerShell here-string：`@' ... '@ \| python -` |
| 2026-06-26 | README 精确补丁上下文不匹配 | 1 | 读取局部上下文后改用更小补丁 |

## 五问重启检查
| 问题 | 答案 |
|------|------|
| 我在哪里？ | 自动化实现和验证已完成，等待 Windows/macOS 手动 PySide6 overlay 验证 |
| 我要去哪里？ | 做实机验证，之后再决定安装包是否内置 PySide6 |
| 目标是什么？ | 主 HUD renderer-only，同时恢复 PySide6 桌面级会话气泡 |
| 我学到了什么？ | PySide6 可以作为 optional helper 恢复，不需要破坏默认 CLI 导入图和 renderer-only 主路径 |
| 我做了什么？ | 恢复 DesktopWorkOverlay、添加 optional extra、更新设置/文档/测试并记录验证结果 |

---
*每个阶段完成后或遇到错误时更新此文件。*
