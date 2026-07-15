# Renderer 新会话首事件与完成气泡回归修复

## Goal

修复 renderer HUD 在 Codex App 新会话中的两个连续回归：

1. 新会话发送首条消息并收到回复后，HUD 仍显示“新会话 等待首个会话事件”。
2. 任务完成后，桌面 work overlay 仍保持方形工作卡；只有点击 Codex App 切换会话后才变成圆形完成气泡。

用户价值：会话身份、当前用量和任务完成状态应在 Codex App 自己产生事件后自动收敛，不需要用户额外点击一次会话来唤醒 HUD。

## Background And Constraints

- Renderer/CDP 是规范产品路径；本修复只修改 renderer/CDP 事件链，不向 Qt/Tk legacy HUD 增加产品行为。
- 当前会话选择契约要求 renderer 提供的 canonical UUID 通过精确 state DB 映射到 rollout；不能用标题、最新文件或其他 activity 文件替代未完成映射。
- `client-new-thread:*` 和空白新会话页必须在真正的 canonical ref 到达前保持显式 pending。
- 保留现有 HUD 外观、方形工作卡和圆形完成气泡的视觉行为，只修复触发和数据刷新时序。
- 保留工作区已有未提交改动，不回滚或覆盖无关修改。

## Confirmed Evidence

### Active-session event gap

- `src/codex_usage_hud/ui/renderer_hud.py:3423-3488` 从侧边栏行、URL 和空白新会话头部读取活动会话。
- `src/codex_usage_hud/ui/renderer_hud.py:3498-3603` 的 active-session 观察来源是侧边栏/头部 `MutationObserver`、history patch、点击和 bootstrap。
- 工作区已有的 `src/codex_usage_hud/ui/renderer_hud.py:5591-5648` composer watcher 确实会监听 Enter/发送按钮，但整个函数在 `composerBadgeEnabled` 为 false 时立即退出；当前默认值就是 false。因此 active-session 事件被错误地绑定到了可选的 token badge 功能上，且仍缺少独立 form submit 入口。
- `src/codex_usage_hud/ui/renderer_hud.py:3539-3547` 的 `activeSessionComposerSubmitButton()` 只在 badge-enabled watcher 中被调用，默认关闭 badge 时无法识别发送控件。
- `src/codex_usage_hud/platforms/active_session.py:554-595` 会把 provisional/new-session ref 清空为 pending，并拒绝无精确映射的 fallback；这是正确的安全边界，但在没有后续 ref 事件时会永久停留。
- `src/codex_usage_hud/cli.py:5731-5750` 只有收到 renderer active-session payload 后才会更新 tracker 并发布 `active_session_changed`。

### Completion shape delay

- `src/codex_usage_hud/cli.py:6744-6762` 在 active-session 轻量刷新时会暂时复用旧的 `active_work_items`，随后依赖下一次 active-work refresh 或 session-file event 完成全量刷新。
- `src/codex_usage_hud/cli.py:6762-6784` 只有刷新得到新的 `active_work_items` 后才把状态写给 desktop work overlay。
- `src/codex_usage_hud/cli.py:3850-3896` 只有 snapshot 识别到 `task_completed_at` 或完成宽限期后的 `final_answer_at` 才生成 `status="recent"`；overlay 再依据该状态把 card 判定为 completed/circle。
- 当新会话仍停留在 renderer-new-session/pending，精确 session path 没有进入当前 snapshot 与 session-file watch 链；点击会话会补发 canonical ref，同时唤醒 active-session refresh，因此两个现象会在同一次点击后一起消失。

### Ruled-out hypothesis

- `src/codex_usage_hud/cli.py:2377-2405` 已监听 state DB、session index、sessions tree 和当前 session 文件。
- `src/codex_usage_hud/platforms/file_watcher.py:545-563` 已将 SQLite 基础文件的 `-wal` 和 `-shm` 变化匹配为同一 watch spec；当前本机 `state_5.sqlite` 的 journal mode 也确认为 WAL。因此本次修复不重复添加 WAL 监听，而聚焦缺失的 renderer send/follow-up 事件。

## Requirements

### R1. Send-triggered active-session reconciliation

在 composer 的发送按钮点击、表单提交或可识别的 Enter 发送路径上，触发一次 renderer active-session 重读，并安排有限次数的短延迟 follow-up，使 Codex 完成 URL/侧边栏/状态提交后能上报 canonical session ref。

要求：

- 复用现有 `readActiveSessionRef()`、`postActiveSession()` 和 binding transport。
- 不在页面空闲时增加持续轮询；follow-up 必须有界并在 watcher 清理时取消。
- canonical ref 未出现时继续显示 pending，不使用标题或最新 rollout 猜测。

### R2. Completion refresh without session click

当 R1 获取 canonical ref 后，active-session change 必须唤醒 renderer loop，完成当前 session path 绑定、session-file event 监听和 active work snapshot 刷新，使 `status="recent"` 在完成后自动写入 overlay。

### R3. Preserve existing behavior

- 普通侧边栏切换、history 路由、已映射会话和真正的空白新会话行为保持不变。
- 不改变 HUD 面板、桌面 overlay 的布局、颜色、动画或点击跳转语义。
- 新事件重复触发必须被现有 signature 去重，不能造成高频 CDP 更新。

### R4. Regression coverage

补充或调整测试，覆盖：

- renderer 脚本确实安装 composer send/submit 事件和清理逻辑；
- send follow-up 能在 ref 从 new/pending 变为 canonical 后提交新 payload；
- active-session refresh 后 work overlay 能从 card 进入 completed 状态；
- provisional/unmapped ref 仍不会退回 activity/title fallback。

## Acceptance Criteria

- AC1：在 Codex App 新建会话，发送一条消息并等待 Codex 回复；无需点击侧边栏，HUD 在 canonical ref 可用后从“新会话 等待首个会话事件”切换到该会话标题和真实用量。
- AC2：同一条消息完成后，无需点击 Codex App，方形工作卡自动完成到圆形气泡；转换延迟不依赖下一次会话点击。
- AC3：若 canonical ref 仍不可用，HUD 保持明确 pending，且不会显示另一条历史会话的用量。
- AC4：现有 active-session、renderer HUD、file watcher 和 work overlay 测试通过；新增回归测试通过。
- AC5：源码检查确认没有为此修复引入 idle whole-document polling 或 Qt/Tk 新路径。

## Out Of Scope

- 逆向或复制 Codex App 宠物功能的视觉、数据源或点击交互。
- 重新设计 HUD/overlay 外观。
- 放宽精确 UUID 到 rollout 的映射契约。
- 通过固定周期扫描 sessions tree 规避 renderer 事件缺失。

## Open Questions

无阻塞的产品问题。修复边界已明确为“发送事件触发有限 reconciliation + 保持精确映射契约”；进入实现前需要用户确认规划产物。
