# 发现与决策：PySide6 桌面级会话气泡恢复

## 当前实现事实
- 主 HUD 已保持 renderer-only：`run_hud_session()` 只调用 `run_renderer_hud_session()`。
- 旧配置值 `auto`、`qt`、`tk`、`tkinter`、`pyside6` 读取后统一归一为 `renderer`。
- 旧 CLI 参数 `--qt-hud`、`--tk-hud`、`--no-renderer-hud` 仍作为兼容 alias 保留，但不会启动 Qt/Tk 独立 HUD。
- renderer 初始连接失败或窗口不可用时返回 `RENDERER_HUD_UNAVAILABLE` 并写入 renderer 诊断，不再 fallback。
- `DesktopWorkOverlay` 已恢复为可选 PySide6 桌面 overlay：当 `work_overlay_max_items > 0` 且 PySide6 可用时启动 helper。
- PySide6 不可用时，`DesktopWorkOverlay` 不启动 helper，不影响 renderer HUD，并记录一次 `work_overlay_unavailable` 诊断。
- helper 启动失败或异常退出后会设置 60s backoff，避免刷新循环不断拉起失败进程。
- `run_work_overlay_helper()` 惰性导入 `work_overlay_qt.py`，默认 CLI import 不加载 Qt overlay 模块。
- 默认依赖不包含 PySide6；`pyproject.toml` 新增 `desktop-overlay = ["PySide6>=6.8"]` optional extra。
- Windows PyInstaller 默认构建不 hidden-import `PySide6` 或 `tkinter.font`。
- renderer 设置页文案已改为“PySide6 桌面气泡数量（0 为关闭）”，并新增“气泡依赖 PySide6”状态块。
- renderer 设置页已移除“HUD 显示方案”字段和 `display_mode` 下拉选项；保存时仍写入 `display_mode: "renderer"` 作为兼容字段。
- “气泡依赖 PySide6”状态块会根据后端 `desktopOverlayDependency` 显示：
  - 已安装：显示 PySide6 版本号。
  - 未安装且源码/pip 环境可安装：显示“需要安装环境”、“立即安装”和“已安装，立即启用”。
  - 需要重启：显示“立即重启”。
- `installDesktopOverlay` 会后台启动 `pip install PySide6>=6.8`；安装完成后用户可点“已安装，立即启用”重新探测。
- `enableDesktopOverlay` 会重新探测 PySide6、清理 overlay 可用性缓存，并在当前配置数量为 0 时恢复默认可用数量。
- README / README_EN 已说明 `codex-usage-hud[desktop-overlay]` 和 `work_overlay_max_items` 的含义。

## 导入图事实
- `import codex_usage_hud.cli` 不加载：
  - `PySide6`
  - `PySide6.QtCore`
  - `codex_usage_hud.ui.work_overlay_qt`
  - `codex_usage_hud.ui.qt_hud`
  - `codex_usage_hud.ui.tk_hud`
- PySide6 探测使用 `importlib.util.find_spec("PySide6")`，不会触发 Qt 模块导入。
- 只有 `codex-hud --work-overlay-helper <state-file>` 子进程路径会惰性进入 `work_overlay_qt.py`。

## 复用能力
| 能力 | 当前状态 |
|------|----------|
| `active_work_items` | 继续作为桌面气泡数据源 |
| `work_item_to_overlay_dict` | 继续输出 helper payload |
| state file / command file | 继续作为 renderer 主进程和 PySide6 helper 的 IPC |
| 完成态圆形动画 | 保留在 `work_overlay_qt.py` |
| dismiss 行为 | 保留既有逻辑和回归测试 |
| 点击 `activateSession` | 保留命令流，主进程继续用 `SessionSwitchController` 处理 |
| 会话切换 backend | macOS/Windows 优先 CDP；Windows 保留 search shortcut fallback |

## 关键决策
| 决策 | 理由 |
|------|------|
| 主 HUD renderer-only，桌面气泡 PySide6 optional | 符合“主 HUD 不回退 Qt/Tk，但桌面级气泡长期用 PySide6”的目标 |
| 不采用沿 Codex App 边框绘制方案 | 用户已明确后续长期采用 Qt/PySide6 桌面级气泡 |
| PySide6 不放入默认 dependencies | 避免 CLI、renderer HUD、打包路径重新强依赖 Qt |
| PySide6 缺失只诊断不报错退出 | 未安装 optional extra 的用户仍应能使用 renderer HUD |
| helper 失败加 backoff | 避免每次刷新都创建失败子进程和重复诊断 |

## 已验证事实
- 目标小集合通过：
  - `python -m pytest tests/test_config.py tests/test_renderer_hud.py tests/test_build_exe.py tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_skips_when_pyside6_unavailable tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_starts_when_pyside6_available tests/test_ui.py::DaemonLifecycleTests::test_run_renderer_hud_session_drains_work_overlay_commands_with_window_prep tests/test_ui.py::DaemonLifecycleTests::test_cli_import_does_not_eagerly_import_qt_hud -q`
- 任务相关集合通过：
  - `python -m pytest tests/test_renderer_hud.py tests/test_ui.py tests/test_build_exe.py tests/test_platforms.py -q`
- 全量单测通过：
  - `python -m pytest -q`
- 编译检查通过：
  - `python -m compileall -q src tests tools`
- whitespace 检查通过：
  - `git diff --check`
- Windows 实机验证通过：
  - 用户确认安装 PySide6 后运行 `codex-hud --daemon`，PySide6 桌面气泡无问题。
- 本轮设置页调整目标测试通过：
  - `python -m pytest tests/test_renderer_hud.py tests/test_ui.py::DaemonLifecycleTests::test_renderer_install_desktop_overlay_starts_optional_dependency_install tests/test_ui.py::DaemonLifecycleTests::test_renderer_enable_desktop_overlay_rechecks_and_enables_without_restart tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_skips_when_pyside6_unavailable tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_starts_when_pyside6_available -q`
  - `python -m pytest tests/test_renderer_hud.py tests/test_ui.py tests/test_build_exe.py -q`

## 剩余风险
| 风险 | 影响 | 当前处理 |
|------|------|----------|
| 尚未在真实 macOS 验证 PySide6 overlay 置顶和点击切换 | macOS 窗口层级/权限可能与 Windows 不同 | 已保留 PySide6 helper 路径；需实机验证 |
| 默认 Windows 安装包不含 PySide6 | 安装包用户默认看不到桌面气泡 | 当前按 optional extra 处理，是否内置 PySide6 是后续发行策略 |
| macOS 自动更新仍不是完整发行方案 | macOS 用户无法复用 Windows installer 语义 | 保留为后续平台化任务 |

## 新会话优先阅读
1. `src/codex_usage_hud/cli.py`
2. `src/codex_usage_hud/ui/work_overlay_qt.py`
3. `src/codex_usage_hud/ui/renderer_hud.py`
4. `src/codex_usage_hud/config.py`
5. `tests/test_ui.py`
6. `tests/test_renderer_hud.py`
7. `README.md`
8. `README_EN.md`

## macOS 手动验证前提
- 安装 optional extra：`python -m pip install -e ".[desktop-overlay]"`
- Codex App 需要暴露本地 CDP/debug target，renderer HUD 才能注入和跟随当前会话。
- macOS 需要检查方形运行气泡、完成态圆气泡、dismiss、点击切换会话和 renderer HUD 注入。

---
*每执行2次查看/搜索/浏览器操作后更新此文件，避免新发现丢失。*
