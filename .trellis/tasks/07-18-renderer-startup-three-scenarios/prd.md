# 明确 Renderer HUD 三场景启动契约

## Goal

将 Renderer HUD 启动收敛为三种可预期、可验证的路径，确保用户在 Codex App 未启动、已启动但无 CDP、以及已通过 CDP 启动时，都能获得稳定的启动结果和恰当的交互反馈。

## Background

- 本任务中用户所述 `CPD` 按 `CDP` 理解。
- Renderer 模式是唯一产品方向；不得回退到 Qt/Tk HUD 或为其增加新产品行为。
- 历史实现已尝试过相同的三场景分流，但实测曾出现场景 1 启动反馈偶发缺失、场景 2 重启提示自行消失，以及重启后重复拉起/使用过期端口的回归。
- 场景 2 本次不再使用 renderer 启动面板承载交互，而是复用现有 PySide6 会话气泡的悬浮、透明和可交互能力。
- 现有 `DesktopWorkOverlay` 仅在 PySide6 已安装且“会话气泡最大显示数”大于 0 时启动；官方 Windows 安装包当前不捆绑 PySide6，源码/pip 环境通过 `codex-usage-hud[desktop-overlay]` 提供。
- 现有 CDP 端口选择只使用显式环境变量、`lastRequestedPort`、`lastSuccessfulPort` 和默认端口，不会从已运行 Codex Desktop 的命令行参数中发现未知端口。
- 2026-07-18 的本机实证显示 Codex Desktop 主进程和 renderer 子进程命令行均包含 `--remote-debugging-port=59629`，且该端口的 `/json/version` 可访问、`/json/list` 包含 `title=Codex` 和 `url=app://-/index.html` 的可注入主页。
- 当日最新启动日志记录了先在过期端口 59905 超时、再分配 59629 并进入重启提示的路径，证明现有端口来源会导致已-CDP 场景被错分。

## Requirements

### R1. Codex App 未启动

- HUD 必须直接以 CDP 模式拉起 Codex App，然后连接、注入并启动 Renderer HUD。
- 一次用户启动操作必须完成整条链路，不得要求用户重新启动 HUD。
- 过程中不得出现空白或无内容的 HUD 气泡。

### R2. Codex App 已启动但未提供可用 CDP

- HUD 不得自动终止用户已运行的 Codex App。
- HUD 必须使用现有 PySide6 会话气泡风格显示需要重启的状态，保持悬浮、透明，并提供明确可点击的重启交互区。
- 该系统级重启气泡不受“会话气泡最大显示数”设置影响；即使普通会话气泡设为 0，仍必须能显示重启交互。
- 用户不点击时，该气泡必须无限期保持，不得超时、自行消失或被普通刷新覆盖。
- 用户点击重启后，HUD 必须终止现有 Codex App，然后严格进入 R1 路径；不得二次拉起或重复提示。
- 若 PySide6 未安装或 helper 无法启动，允许使用现有轻量启动卡作为唯一应急交互退路；该退路必须同样持续等待用户点击，并写入明确运行时诊断。

### R3. Codex App 已以 CDP 模式启动

- 必须先评估并验证现有运行时是否能可靠发现实际 CDP 端口和可注入的 Codex renderer target。
- 若可靠发现，HUD 必须直接连接现有 CDP 实例并在一次启动中完成注入，不得增加不必要的场景 2 兜底。
- 只有在当前 Codex App 实际没有可用 CDP renderer target，或经有界验证无法确定其端口时，才可进入 R2。
- 端口发现必须从经现有进程识别规则确认的 Codex Desktop 进程提取 `--remote-debugging-port`，不得把 npm/standalone Codex CLI 进程当作 Desktop。
- 命令行只用于产生有界候选端口；候选端口还必须通过本机 HTTP 和现有 Codex 主页 target 选择逻辑验证后才能被选中。

### R4. 边界与兼容性

- 保持 Renderer HUD 现有会话跟踪、主题、用量统计和气泡交互行为不变。
- 启动和端口发现必须兼容 Windows 和 macOS；平台特定进程操作保持在现有边界内。
- 保留并兼容当前工作区的未提交修改，不回退无关改动。

### R5. CLI 会话压缩续作状态

- Codex CLI 为上下文压缩生成的交接 `final_answer` 不得被当作真实任务完成。
- `compacted` / `context_compacted` 之后仍在输出 commentary、reasoning 或工具活动时，会话气泡必须保持方形运行态。
- 只有压缩续作之后的新 `final_answer` / `task_complete` 才能将气泡切换为圆形完成态。

## Acceptance Criteria

- [x] AC1: Codex App 完全未运行时，从一次 HUD 启动到 Renderer HUD 可用全程成功，且只拉起一个 Codex App 实例。
- [x] AC2: Codex App 已运行但无可用 CDP 时，PySide6 重启气泡在用户不操作的情况下持续存在，且不会杀掉当前 App。
- [x] AC2a: 将普通会话气泡数设为 0 时，场景 2 仍显示 PySide6 重启气泡。
- [x] AC2b: 模拟 PySide6 缺失或 helper 启动失败时，轻量启动卡接管交互、保持不自行消失，且诊断记录标明降级原因。
- [x] AC3: 点击 PySide6 气泡的重启区后，旧 App 被终止，随后严格按 AC1 完成，无重复拉起、无重复提示。
- [x] AC4: Codex App 已经暴露可用 CDP renderer target 时，一次 HUD 启动直接附加并成功注入，不显示重启气泡。
- [x] AC5: 端口状态文件过期、配置端口与实际端口不同、以及多个本地候选端口时，只有经 `/json/version` 和 renderer target 验证的端口可直接连接。
- [x] AC6: 启动状态、用户点击和重启恢复路径有定向自动化测试，并通过 renderer 相关完整测试集。
- [x] AC7: 在 Windows 上完成三场景的真实 Codex App 验收；macOS 通过平台分支自动化测试与静态边界审查。
- [x] AC8: 当前 CLI 会话发生上下文压缩后，压缩前的交接终态被清除，继续执行时保持方形气泡，后续真实终态仍可变为圆形。

## Out Of Scope

- 不新增 Qt/Tk HUD 功能，不将其恢复为默认或推荐模式。
- 不改造普通会话气泡的视觉语言或其他会话交互。
- 不引入持续扫描全端口范围的高频轮询。
- 不把 PySide6 加入官方 Windows 安装包，不修改现有 desktop-overlay 发布策略。
