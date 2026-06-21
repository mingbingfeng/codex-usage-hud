# 新增 Qt 独立 HUD，并保留 Tk 最终兜底

## Summary
- 用 PySide6 实现新的独立 HUD，作为 `renderer` 不可用时的首选 fallback，解决当前 Tk 顶部展开卡顿、冷启动慢、动画不流畅的问题。
- 不废弃 `tk` 模式：配置和 CLI 保留 `auto | renderer | qt | tk`；旧 `tk` 值继续有效，作为最终兜底路径。
- 本计划不再包含删除、隐藏或停止维护 Tk 的工作；Qt 是新增独立 HUD 路线，Tk 是保留的稳定 fallback。
- Renderer 注入仍是默认优先路径；Renderer 不可用或用户选择 `qt` 时启动 Qt 独立窗口；Qt 不可用、启动失败或用户选择 `tk` 时启动 Tk 独立窗口。

## Scope Update
- Qt 模式作为新的首选独立窗口实现推进，目标是改善 Tk 模式当前的交互卡顿和启动体验。
- Tk 模式继续作为受支持的最终兜底：显式选择 `tk`、Qt 依赖缺失、Qt 初始化失败、Qt 在特定平台不可用时，都应能回退到 Tk。
- 迁移过程中不得移除 Tk 配置值、CLI 参数、运行入口、测试覆盖或构建依赖；涉及共享 formatter/数据结构的调整必须保持 Tk 行为兼容。

## Key Changes
- 新增 `QtHudWindow`，承接当前 `TokenHudWindow` 的运行契约：`update_display()`、`run()`、`close()`、`exit_reason`、`mode_switch_request`、`restart_codex_for_renderer()`、`should_refresh_snapshot()`、`refresh_delay_ms()` 等，方便 CLI 调度逻辑最小化迁移。
- Qt HUD 使用两个 frameless/topmost/translucent 窗口：顶部会话/预算 HUD 和请求流水 HUD。窗口定位、吸附、拖拽、resize、隐藏/恢复、透明度、topmost 策略与现有 Tk 行为保持一致。
- 顶部展开面板预构建并缓存，点击时只切状态并用 `QPropertyAnimation` 或 `QVariantAnimation` 做 160-220ms 尺寸/透明度动画；不允许展开时销毁重建控件树。
- 顶部内容复用现有数据格式函数，优先从 `renderer_hud.py` 的 payload/detail/progress 结构抽取公共 formatter，避免 Tk/Renderer/Qt 三套业务格式发散。
- 设置面板增加 Qt 选项：显示模式选项改为“自动：优先 Renderer，失败回退 Qt，Qt 失败再回退 Tk”、“Renderer 内嵌 HUD”、“Qt 独立窗口”、“Tk 独立窗口”；立即切换时发出 `renderer <-> qt <-> tk` 模式切换。
- CLI/配置扩展：
  - `VALID_DISPLAY_MODES = {"auto", "renderer", "qt", "tk"}`。
  - `effective_display_mode()` 返回 `renderer`、`qt` 或 `tk`，其中 `auto` 的运行时解析顺序是 Renderer -> Qt -> Tk。
  - 保留 `--tk-hud` / `--no-renderer-hud` 兼容语义，新增 `--qt-hud` 作为 Qt 独立窗口强制入口；`--hud-mode` choices 改为 `auto, renderer, qt, tk`。
  - 保留 `run_tk_hud_session()`，新增 `run_qt_hud_session()`；共享的独立窗口准备逻辑命名为 standalone，例如 `_prepare_codex_window_for_standalone()`。
  - `HUD_SWITCH_TO_TK`、`DAEMON_STARTUP_TK` 等现有 Tk 信号继续保留；新增对应 Qt 信号，避免破坏旧调用点。
- 将 `tk_hud.py` 中纯业务 formatter 迁入 neutral module，例如 `ui/hud_formatters.py`，供 Tk/Renderer/Qt 复用；Tk 控件实现保留并作为最终兜底入口导出。

## Implementation Details
- 新建 `src/codex_usage_hud/ui/qt_hud.py`：
  - 延迟导入 PySide6，缺失时给清晰错误。
  - 使用 `QApplication.instance() or QApplication(...)`，`setQuitOnLastWindowClosed(False)`。
  - 用 `QWidget` + `QPainter` 实现圆角、进度条、活动轨迹、chip、警告条；文字省略用 `QFontMetrics.elidedText()`。
  - 顶部和请求窗口均支持 collapsed/expanded 两套预构建内容，展开只切可见性和动画属性。
  - 使用 `QTimer` 实现窗口 follow、跑马灯、运行时长刷新和动画 settle，不在用户交互期间做重型 snapshot 刷新。
- 更新 `src/codex_usage_hud/cli.py`：
  - 新增 Qt fallback 文案、日志、函数名、启动 prompt；保留 Tk 相关文案用于最终兜底和显式 `tk` 模式。
  - `run_hud_session()` 中 Renderer 失败后 fallback 到 Qt；Qt 缺失或启动失败时 fallback 到 Tk。
  - Renderer 设置命令 `switchMode` 支持 `qt` 和 `tk`。
  - `_prepare_codex_window_for_tk()` 重命名为 `_prepare_codex_window_for_standalone()`。
- 更新 `src/codex_usage_hud/ui/renderer_hud.py`：
  - 设置弹窗和模式切换文案增加 Qt，同时保留 Tk 入口。
  - `activeDisplayMode()` / `effectiveRuntimeMode()` 识别 `qt` 和 `tk`。
  - `applyDisplayMode` 切换时可提交 `display_mode: "qt"` 或 `"tk"`，并分别显示 Qt/Tk 独立窗口 loading 文案。
- 更新文档和构建：
  - README/README_EN 从 “Tk fallback” 改为 “Qt fallback with Tk final fallback”。
  - `tools/build_exe.py` 保留 `tkinter.font` hidden import，并增加/确认 PySide6 收集。
  - `ui/__init__.py` 同时导出 `QtHudWindow` 和 `TokenHudWindow`，其中 Qt 是首选独立 HUD，Tk 是最终兜底。

## Test Plan
- 配置/CLI：
  - `normalize_display_mode("qt") == "qt"`，`normalize_display_mode("tk") == "tk"`。
  - `--hud-mode qt` 强制跳过 Renderer 并启动 Qt；`--hud-mode tk` / `--tk-hud` 强制启动 Tk。
  - Renderer 设置中选择 Qt 会返回 `switchMode="qt"`；选择 Tk 会返回 `switchMode="tk"`。
  - `auto` 模式下模拟 Renderer 失败进入 Qt；再模拟 PySide6 缺失或 Qt 启动失败进入 Tk。
- Qt HUD 单元/集成：
  - 创建/关闭 `QtHudWindow` 不崩溃，两个窗口存在并能 update snapshot。
  - 顶部展开不重建主控件树：展开前后核心 widget identity 保持稳定。
  - 展开动画存在并完成后尺寸等于配置高度。
  - collapsed/expanded 下 session、budget、activity trail、warnings、copy payload 显示正确。
  - 请求流水展开保留滚动位置和最新行高亮。
- 回归：
  - Renderer HUD 测试覆盖 `qt` 和 `tk` 文案、mode、切换命令。
  - Daemon startup prompt 选择独立窗口时默认走 Qt；Qt 不可用时走 Tk。
  - Work overlay command 仍能先准备 Codex 窗口再激活 session。
  - `python -m compileall src tests`，并跑 `python -m pytest tests/test_config.py tests/test_renderer_hud.py tests/test_ui.py -q`。

## Assumptions
- `PySide6>=6.8` 已是项目运行依赖，因此不新增 WebView2 或浏览器内核依赖。
- 旧 Tk 模式继续兼容：旧配置里的 `"display_mode": "tk"` 保持显式 Tk 独立窗口语义。
- Tk 代码在 Qt 稳定后仍保留，但定位为最终兜底：仅在显式选择 `tk`、Qt 依赖缺失、Qt 初始化失败或平台兼容问题时启用。
- 启动/loading 小窗可暂时继续使用现有 Tk helper，因为它不是顶部展开卡顿来源；后续可独立评估是否迁移到 Qt。
