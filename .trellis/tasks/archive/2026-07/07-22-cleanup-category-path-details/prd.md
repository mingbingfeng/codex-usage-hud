# Cleanup categories, path details, and reveal action

## Goal

把设置里的“垃圾清理”从“内部删除单元列表”改成用户可审计的清理清单：同一来源和类型的目标合并为一条可选择分类，展开后能看到每个实际目标的完整本地路径，并能安全地复制路径或在系统文件管理器中定位。

用户明确要求本地清理工具显示绝对路径，因为用户需要知道将删除的确切内容；路径不得上传到远端或写入无关日志，但不再以“隐私合同”为由从本地 Renderer 隐藏。

## Confirmed Facts

- `platform_cache_definitions()` 将 Windows `%TEMP%` 注册为 `expired_children`；`SafeCleanupManager._scan_cache_definition()` 为每个过期顶层子项创建一个 `CleanupItem`（`src/codex_usage_hud/core/safe_cleanup.py:1442`, `:2961`）。
- `CleanupItem.to_payload()` 将每个删除单元固定编码为 `items: 1`，`CleanupInventory.to_payload()` 再逐项输出；同类条目因此在 UI 中重复出现（`src/codex_usage_hud/core/safe_cleanup.py:244-251`, `:270-306`）。
- Renderer 只渲染前 8 个安全项，默认选择和预览却覆盖全部安全项；行尾箭头没有详情处理器（`src/codex_usage_hud/ui/renderer_hud.py:6914-6936`）。这会让未显示的目标进入确认范围，属于透明度和授权反馈缺陷。
- 本机同尺寸临时目录抽查包含多份不同路径、同体积的 Visual Studio Installer 解包树，证明截图中的重复行来自不同删除单元而非重复 ID；本任务仍按现有白名单规则聚合，未知来源保留通用分类。
- 删除安全性依赖 Python 端 inventory revision、opaque ID、canonical path、lstat/fingerprint、路径边界和进程/锁重验；这些约束必须保留。Renderer 发送的“打开位置”只能带 opaque ID，不能带用户可编辑的任意路径。
- Renderer 是唯一产品面；Qt/Tk 不新增行为。Windows 和 macOS 都需要支持本地定位动作。

## Requirements

### R1. 分类聚合

- 后端或 Renderer 必须把同一清理来源、类别、tier、保留期和风险说明的删除单元聚合为一个用户可见分类。
- 分类显示聚合后的总字节数、实际目标数、文件总数、最旧/最新时间范围和来源名称；不能再把每个顶层子项伪装成 `1 项` 的同名行。
- 受保护、正在使用、近期未过期和可执行目标必须保持可区分，聚合不得跨越安全边界。
- 本任务按现有白名单清理规则聚合，不新增基于目录内容的应用归属猜测。现有定义已经能证明来源时显示应用名称；通用 `%TEMP%` 子项统一回退到“过期用户临时数据”。后续若增加应用识别，只能使用可信文件元数据并单独测试。

### R2. 详情与绝对路径

- 分类行提供真实可用的展开/折叠交互；展开后展示每个实际目标的绝对路径、文件/目录类型、大小、文件数、最后修改时间、保留状态和当前可执行/受保护原因。
- 绝对路径只在本地 Renderer、复制路径动作和本地系统文件管理器跳转中使用，不上传、不写入公开日志、不进入远端请求；更新隐私文档和 Renderer payload 合同以反映这一点。
- 路径显示必须可换行/横向滚动而不造成设置弹窗溢出；默认折叠分类保持紧凑，避免把完整路径塞进首屏每一行。
- 首层分类行只显示来源摘要（例如 `%TEMP%`）和聚合统计；完整绝对路径按用户确认的方案放在展开详情中。

### R3. 复制与定位

- 每个详情目标提供“复制路径”和“打开位置”操作；打开位置在 Windows 使用 Explorer，在 macOS 使用 Finder。
- 新增命令只接受 inventory revision + opaque item ID，由 Python 端从当前 inventory 解析 canonical path 并重新校验；Renderer 不能提交聚合组 ID 或任意路径直接执行。
- safe/consent/protected 项只要保存的路径仍可验证都允许只读定位；对过期 revision、未知 ID、路径边界变化、目标消失或 reparse point，定位动作失败并给出可理解的本地错误，不执行外部进程。
- 文件目标应定位并选中文件；目录目标应打开目录本身。系统命令使用参数数组和 `shell=False`，兼容 Windows/macOS。

### R4. 选择、预览与执行一致

- 分类选择必须映射到所有可见子项的 opaque ID；取消分类必须从确认集合移除全部子项。
- 任何进入确认的目标都必须在当前可见分类或其展开详情中可追溯，不能再用 `slice(0, 8)` 静默隐藏已选目标。
- 预览、第二次确认、执行结果按分类汇总，同时保留每个子项的成功/跳过/失败状态和路径定位能力。
- 继续保持“扫描 → 确认清理”的两步主流程；路径详情、备份和深度清理不能变成额外的主按钮流程。

### R5. 跨平台与视觉质量

- 只修改 Renderer/CDP 和共享 Python 清理链路；不向 Qt/Tk 增加产品行为。
- 布局与交互以自动化测试 + 结构断言为主验收；真实 Renderer 手动实测**不作为**任务完成硬门槛，实现/检查完成后只需向用户提醒推荐实测链路。
- 遵循现有空间清理视觉结构：紧凑分类行、明确状态色、可扫描的详情列表、危险动作局部化；不引入卡片堆叠或营销式说明。

## Acceptance Criteria

- [ ] Windows `%TEMP%` 中多个同类过期目标在首层只显示一条聚合分类，大小/目标数/文件数与后端扫描总数一致（由单元/结构测试与 payload 断言覆盖）。
- [ ] 分类展开后可看到每个目标的绝对路径及完整元数据；未知来源不被错误标成某个应用。
- [ ] 复制路径返回精确本地路径；打开位置只允许通过 opaque ID 定位扫描目标，伪造路径或过期 ID 不会启动 Explorer/Finder。
- [ ] 分类选择、预览和执行的目标集合一致；不存在确认范围内不可追溯的隐藏目标。
- [ ] 文件和目录定位分别在 Windows Explorer 与 macOS Finder 上通过平台适配器测试（注入 launcher / 断言 argv）；无 shell 注入路径。
- [ ] 更新 `tests/test_safe_cleanup.py`、`tests/test_renderer_hud.py`、必要的 `tests/test_ui.py`，并更新 `docs/PRIVACY.md` 与 `.trellis/spec/backend/safe-cleanup-contracts.md`。
- [ ] 实现/检查收尾时向用户给出推荐真实 Renderer 实测链路（见 `implement.md`「用户推荐实测链路」）；**不要求**会话内完成真机截图或 live 交互验收。

## Recommended Manual Check (user-owned, optional)

任务完成**不依赖**下列步骤。收尾时提醒用户可按此链路自测：

1. 重启本机 workspace Renderer helper，打开设置 → 垃圾清理。
2. 扫描后确认同类 `%TEMP%` 目标合并为一条分类，展开后为完整绝对路径且统计与扫描一致。
3. 复制路径成功；「打开位置」只对扫描项生效，且不应对真实用户数据执行清理。
4. 勾选/取消分类后预览目标集合与合计字节同步变化。
5. 宽屏、约 760px、约 520px 下无横向溢出与明显 console/window error。
