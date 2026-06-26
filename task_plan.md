# 任务计划：恢复 PySide6 桌面级会话气泡

## 目标
主 HUD 保持 Codex renderer/CDP 注入，不回退到 Qt/Tk 独立 HUD；会话方形气泡和完成态圆气泡作为独立 PySide6 桌面 overlay 恢复。未安装 PySide6 时 renderer HUD 仍可正常运行，并给出清晰诊断。

## 摘要
- PySide6 支持 macOS，现有 `work_overlay_qt.py` 的桌面气泡栈作为 Windows/macOS 恢复基础。
- 主 HUD 继续保持 renderer-only；会话方形气泡/完成态圆气泡作为独立 PySide6 桌面 overlay 恢复。

## 非目标
- 不把 PySide6 放回默认依赖。
- 不恢复 Qt/Tk 独立 HUD fallback。
- 不改成沿 Codex App 左边框或右边框绘制气泡。
- 不在本轮处理 macOS 安装包和自动更新发行策略。

## 当前阶段
阶段 8：PySide6 桌面会话气泡 optional helper 已恢复；自动化验证已完成，等待 Windows/macOS 安装 PySide6 后手动验证。

## 各阶段

### 阶段 0：恢复上下文与修正方向
- [x] 读取 `task_plan.md`、`progress.md`、`findings.md`
- [x] 运行 session catchup
- [x] 确认用户最终方向：长期采用 Qt/PySide6 桌面级气泡，不采用 Codex App 边框绘制方案
- **状态：** complete

### 阶段 1：保持 renderer-only 主 HUD
- [x] `run_hud_session()` 只进入 renderer 会话
- [x] 旧配置值 `auto`、`qt`、`tk`、`tkinter`、`pyside6` 统一归一为 `renderer`
- [x] `--qt-hud`、`--tk-hud`、`--no-renderer-hud` 保留兼容解析，但不启动旧独立 HUD
- [x] renderer/CDP 不可用时返回诊断，不 fallback 到 Qt/Tk
- **状态：** complete

### 阶段 2：恢复 PySide6 桌面气泡 helper
- [x] `DesktopWorkOverlay` 改为 PySide6 optional desktop overlay
- [x] renderer 会话按 `_work_overlay_item_limit_for_context(context)` 创建 overlay，而不是固定禁用
- [x] 当 `work_overlay_max_items <= 0` 时不启用桌面气泡
- [x] PySide6 不可用时不影响 renderer HUD，并记录一次 `work_overlay_unavailable` 诊断
- [x] helper 异常退出后增加 60s backoff，避免刷新循环反复启动失败进程
- **状态：** complete

### 阶段 3：依赖、导入图与打包
- [x] 默认 dependencies 保持不包含 PySide6
- [x] 增加 optional extra：`desktop-overlay = ["PySide6>=6.8"]`
- [x] `import codex_usage_hud.cli` 不加载 `PySide6`、`work_overlay_qt`、`qt_hud`、`tk_hud`
- [x] Windows PyInstaller 默认构建不 hidden-import `PySide6` / `tkinter.font`
- **状态：** complete

### 阶段 4：复用既有气泡能力
- [x] 继续复用 `active_work_items`
- [x] 继续复用 `work_item_to_overlay_dict`
- [x] 继续复用 state file / command file 协议
- [x] 保留完成态圆形动画、dismiss 行为、点击 `activateSession` 命令流
- [x] 点击切换会话继续走 `SessionSwitchController`；macOS/Windows 优先 CDP，Windows 保留 search shortcut fallback
- **状态：** complete

### 阶段 5：设置页与文档
- [x] renderer 设置页把 `work_overlay_max_items` 描述为“PySide6 桌面气泡数量（0 为关闭）”
- [x] renderer 设置页移除“HUD 显示方案”字段和选项，让 PySide6 桌面气泡数量字段左移到原位置
- [x] renderer 设置页新增“气泡依赖 PySide6”状态块，显示已安装版本、需要安装环境、立即安装、已安装立即启用或立即重启
- [x] README / README_EN 说明 `codex-usage-hud[desktop-overlay]`
- [x] README / README_EN 明确 `work_overlay_max_items` 表示 PySide6 桌面级会话气泡数量，`0` 为关闭
- [x] CHANGELOG 记录恢复 optional PySide6 desktop work-bubble overlay
- **状态：** complete

### 阶段 6：验证
- [x] 单测：renderer 设置页不再展示“HUD 显示方案”和 `display_mode` 选项
- [x] 单测：renderer 设置页展示“气泡依赖 PySide6”状态块和安装/启用动作
- [x] 单测：`installDesktopOverlay` 会触发 PySide6 optional dependency 安装流程
- [x] 单测：`enableDesktopOverlay` 会重新探测 PySide6 并无需重启启用桌面气泡
- [x] 单测：PySide6 可用时 `DesktopWorkOverlay` 启动 helper
- [x] 单测：PySide6 不可用时不影响 renderer HUD，并记录诊断
- [x] 单测：renderer 会话按配置上限创建 overlay
- [x] 单测：`import codex_usage_hud.cli` 不 eager import PySide6 / Qt overlay 模块
- [x] 回归：现有气泡 payload、完成圆点、动画、dismiss、点击命令测试继续通过
- [x] `python -m pytest -q`
- [x] `python -m compileall -q src tests tools`
- [x] `git diff --check`
- [ ] Windows 手动：安装 PySide6 后运行 `codex-hud --daemon` 验证桌面气泡、点击切换和 renderer HUD 注入
- [ ] macOS 手动：安装 PySide6 后运行 `codex-hud --daemon` 验证桌面气泡、点击切换和 renderer HUD 注入
- **状态：** partial

## 关键设计决策
| 决策 | 理由 |
|------|------|
| 主 HUD renderer-only | 继续保持跨平台统一运行面，避免恢复 Qt/Tk 独立 HUD fallback |
| PySide6 只作为 desktop-overlay optional extra | 未安装 PySide6 的用户仍可使用 renderer HUD |
| `DesktopWorkOverlay` 运行时探测 PySide6 | 默认 CLI 导入路径不加载 PySide6，也不提前加载 `work_overlay_qt` |
| 继续使用现有 state/command 协议 | 最大限度复用完成态圆气泡、dismiss、点击切换等已验证行为 |
| 不采用 Codex App 边框绘制方案 | 用户已确认后续长期使用 Qt/PySide6 桌面级气泡 |

## 验收标准
- 默认启动只尝试 renderer 主 HUD，不启动 Qt/Tk 独立 HUD。
- `work_overlay_max_items > 0` 且 PySide6 可用时，renderer 会话启动 PySide6 桌面气泡 helper。
- PySide6 不可用时 renderer HUD 正常工作，并只记录清晰诊断。
- `import codex_usage_hud.cli` 不加载 PySide6 / Qt overlay 模块。
- README、README_EN、设置页文案都明确 `work_overlay_max_items` 是 PySide6 桌面级会话气泡数量。

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| PowerShell 不支持 Bash here-doc `python - <<'PY'` | 1 | 改用 PowerShell here-string 管道到 `python -` |
| README 精确补丁上下文不匹配 | 1 | 先读取局部上下文，再用更小补丁插入 FAQ |

## 下一步
1. 在 Windows 源码环境安装 optional extra：`python -m pip install -e ".[desktop-overlay]"`
2. 运行 `codex-hud --daemon`，验证方形运行气泡、完成态圆气泡、dismiss、点击切换会话和 renderer HUD 注入。
3. 在 macOS 重复同样手动验证，重点确认 Codex App CDP/debug 启动和 PySide6 overlay 置顶行为。
