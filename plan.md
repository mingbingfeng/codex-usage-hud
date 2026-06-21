\# 将独立 HUD 从 Tk 迁移到 Qt



\## Summary

\- 用 PySide6 实现新的独立 fallback HUD，替代当前 Tk 主 HUD，解决顶部展开卡顿、冷启动慢、动画不流畅的问题。

\- 直接废弃 `tk` 模式命名：配置和 CLI 改为 `auto | renderer | qt`；旧 `tk` 值不再兼容，按无效值处理为默认 `auto`。

\- Renderer 注入仍是默认优先路径；Renderer 不可用或用户选择 `qt` 时启动 Qt 独立窗口。



\## Key Changes

\- 新增 `QtHudWindow`，承接当前 `TokenHudWindow` 的运行契约：`update\_display()`、`run()`、`close()`、`exit\_reason`、`mode\_switch\_request`、`restart\_codex\_for\_renderer()`、`should\_refresh\_snapshot()`、`refresh\_delay\_ms()` 等，方便 CLI 调度逻辑最小化迁移。

\- Qt HUD 使用两个 frameless/topmost/translucent 窗口：顶部会话/预算 HUD 和请求流水 HUD。窗口定位、吸附、拖拽、resize、隐藏/恢复、透明度、topmost 策略与现有 Tk 行为保持一致。

\- 顶部展开面板预构建并缓存，点击时只切状态并用 `QPropertyAnimation` 或 `QVariantAnimation` 做 160-220ms 尺寸/透明度动画；不允许展开时销毁重建控件树。

\- 顶部内容复用现有数据格式函数，优先从 `renderer\_hud.py` 的 payload/detail/progress 结构抽取公共 formatter，避免 Tk/Renderer/Qt 三套业务格式发散。

\- 设置面板迁到 Qt：显示模式选项改为“自动：优先 Renderer，失败回退 Qt”、“Renderer 内嵌 HUD”、“Qt 独立窗口”；立即切换时发出 `renderer <-> qt` 模式切换。

\- CLI/配置重命名：

&#x20; - `VALID\_DISPLAY\_MODES = {"auto", "renderer", "qt"}`。

&#x20; - `effective\_display\_mode()` 返回 `qt` 或 `renderer`。

&#x20; - 移除 `--tk-hud` / `--no-renderer-hud`，新增 `--qt-hud` / `--no-renderer-hud` 只作为 Qt 独立窗口强制入口；`--hud-mode` choices 改为 `auto, renderer, qt`。

&#x20; - `run\_tk\_hud\_session()` 重命名为 `run\_qt\_hud\_session()`；`HUD\_SWITCH\_TO\_TK`、`DAEMON\_STARTUP\_TK` 等命名改为 Qt。

\- 保留 `tk\_hud.py` 中纯业务 formatter 的可复用部分时，需要迁入 neutral module，例如 `ui/hud\_formatters.py`；Tk 控件实现不再作为默认入口导出。



\## Implementation Details

\- 新建 `src/codex\_usage\_hud/ui/qt\_hud.py`：

&#x20; - 延迟导入 PySide6，缺失时给清晰错误。

&#x20; - 使用 `QApplication.instance() or QApplication(...)`，`setQuitOnLastWindowClosed(False)`。

&#x20; - 用 `QWidget` + `QPainter` 实现圆角、进度条、活动轨迹、chip、警告条；文字省略用 `QFontMetrics.elidedText()`。

&#x20; - 顶部和请求窗口均支持 collapsed/expanded 两套预构建内容，展开只切可见性和动画属性。

&#x20; - 使用 `QTimer` 实现窗口 follow、跑马灯、运行时长刷新和动画 settle，不在用户交互期间做重型 snapshot 刷新。

\- 更新 `src/codex\_usage\_hud/cli.py`：

&#x20; - 所有 fallback 文案、日志、函数名、启动 prompt 从 Tk 改为 Qt。

&#x20; - `run\_hud\_session()` 中 Renderer 失败后 fallback 到 Qt。

&#x20; - Renderer 设置命令 `switchMode` 从 `tk` 改为 `qt`。

&#x20; - `\_prepare\_codex\_window\_for\_tk()` 重命名为 `\_prepare\_codex\_window\_for\_standalone()`。

\- 更新 `src/codex\_usage\_hud/ui/renderer\_hud.py`：

&#x20; - 设置弹窗和模式切换文案改为 Qt。

&#x20; - `activeDisplayMode()` / `effectiveRuntimeMode()` 识别 `qt`。

&#x20; - `applyDisplayMode` 切换时提交 `display\_mode: "qt"` 并显示 Qt 独立窗口 loading 文案。

\- 更新文档和构建：

&#x20; - README/README\_EN 从 “Tk fallback” 改为 “Qt fallback”。

&#x20; - `tools/build\_exe.py` 移除 `tkinter.font` hidden import；保留 PySide6 收集。

&#x20; - `ui/\_\_init\_\_.py` 导出 `QtHudWindow`，不再导出 `TokenHudWindow` 作为主 HUD。



\## Test Plan

\- 配置/CLI：

&#x20; - `normalize\_display\_mode("qt") == "qt"`，`normalize\_display\_mode("tk") == "auto"`。

&#x20; - `--hud-mode qt` 强制跳过 Renderer；`--tk-hud` 报 argparse 错误。

&#x20; - Renderer 设置中选择 Qt 会返回 `switchMode="qt"`。

\- Qt HUD 单元/集成：

&#x20; - 创建/关闭 `QtHudWindow` 不崩溃，两个窗口存在并能 update snapshot。

&#x20; - 顶部展开不重建主控件树：展开前后核心 widget identity 保持稳定。

&#x20; - 展开动画存在并完成后尺寸等于配置高度。

&#x20; - collapsed/expanded 下 session、budget、activity trail、warnings、copy payload 显示正确。

&#x20; - 请求流水展开保留滚动位置和最新行高亮。

\- 回归：

&#x20; - Renderer HUD 测试更新 `tk` 文案和 mode 为 `qt`。

&#x20; - Daemon startup prompt 选择独立窗口时走 Qt。

&#x20; - Work overlay command 仍能先准备 Codex 窗口再激活 session。

&#x20; - `python -m compileall src tests`，并跑 `python -m pytest tests/test\_config.py tests/test\_renderer\_hud.py tests/test\_ui.py -q`。



\## Assumptions

\- `PySide6>=6.8` 已是项目运行依赖，因此不新增 WebView2 或浏览器内核依赖。

\- 旧 Tk 模式不做兼容迁移：旧配置里的 `"display\_mode": "tk"` 会回落到默认 `auto`。

\- 启动/loading 小窗可暂时继续使用现有 Tk helper，因为它不是主 HUD fallback，也不是顶部展开卡顿来源。



