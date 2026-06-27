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
阶段 7：在没有本地 Mac、且本轮不使用远程 Mac 的前提下推进 macOS 验证与发行策略。自动化验证、Windows 实机验证和 macOS CI smoke 已完成；macOS 桌面交互验证暂缓，当前以 CI smoke 作为代码级保障。

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
- [x] Windows 手动：安装 PySide6 后运行 `codex-hud --daemon` 验证桌面气泡、点击切换和 renderer HUD 注入
- [ ] macOS 手动：安装 PySide6 后运行 `codex-hud --daemon` 验证桌面气泡、点击切换和 renderer HUD 注入
- **状态：** partial

### 阶段 7：无本地 Mac 的推进方案
- [x] 新增 macOS CI smoke：在 GitHub Actions `macos-latest` 上安装 `codex-usage-hud[desktop-overlay]`，跑 `python -m compileall -q src tests tools` 和任务相关 `pytest`
- [x] 产出一份 macOS 人工验证 checklist，供未来真实 Mac 环境使用
- [x] 决定本轮采用的无本地 Mac 验证路径：只保留 GitHub Actions macOS smoke，不使用远程 Mac
- [x] 在 macOS 人工验证完成前，明确桌面气泡对 macOS 仍为“待实机确认”，不阻塞 Windows 路线继续推进
- **状态：** complete

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

## 没有本地 Mac 的推荐方案
1. GitHub Actions `macos-latest` smoke 回归
   - 用途：先补齐“能装、能导入、能跑测试”的最低保障，不解决真实桌面置顶、点击切换、窗口权限等交互问题。
   - 适合当前项目：仓库目前没有现成的 Actions workflow，最适合作为下一步先落地的低成本方案。
   - 建议执行：新增一个只跑 macOS smoke 的 workflow，先覆盖 optional extra 安装、相关 pytest 和 `compileall`。
2. AWS EC2 Mac 短租做一次人工验证
   - 用途：需要真实 macOS 环境时，远程连上去跑 `codex-hud --daemon`，验证 overlay 置顶、dismiss、点击切换和 renderer HUD 注入。
   - 适合当前项目：一次性验证最直接；做完即可沉淀录屏、截图和 checklist 结果。
   - 代价：比 CI 贵，但比长期持有设备轻；适合“先验证再决定是否长期支持”。
3. MacStadium 远程 Mac / 虚拟化
   - 用途：如果后续要持续维护 macOS 桌面体验、安装包、签名或多轮人工回归，适合用作长期环境。
   - 适合当前项目：只有当 macOS 会成为持续交付目标时才值得上；当前阶段可能偏重。
4. Codemagic 托管 macOS CI/CD
   - 用途：如果下一步重点转向打包、签名、发布而不是仅做一次 UI 验证，可以直接把 macOS 构建链放到托管平台。
   - 适合当前项目：适合后续发行策略阶段，不是当前“补一个人工验证”的最短路径。

## 推荐顺序
1. 先做 GitHub Actions macOS smoke，尽快把“代码级兼容性”补齐。
2. 本轮不做远程 Mac；macOS 桌面交互验证保持待实机确认状态。
3. 如果后续确定要长期维护 macOS 安装包/签名，再评估真实 Mac 设备、MacStadium 或 Codemagic。

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| PowerShell 不支持 Bash here-doc `python - <<'PY'` | 1 | 改用 PowerShell here-string 管道到 `python -` |
| README 精确补丁上下文不匹配 | 1 | 先读取局部上下文，再用更小补丁插入 FAQ |

## 下一步
1. 保留并观察 GitHub Actions macOS smoke 结果，把它作为当前 macOS 代码级回归入口。
2. 在文档和发布说明里继续明确：macOS 桌面气泡路径目前只有 CI smoke，真实桌面交互仍待未来实机确认。
3. 之后单独规划发行策略，决定 Windows/macOS 安装包是否内置 PySide6，以及何时再引入真实 Mac 验证。
