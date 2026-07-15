# Renderer 会话激活快路径与结构化状态语义

## Goal

优化现有 `activateSession` 的 renderer/CDP 快路径：先尝试精确 session ID 激活，失败时才准备窗口并重试；贯通会话 ID、结构化状态与事件驱动刷新语义，补测试与 live latency 验证。

## Confirmed facts and constraints

- Codex App 宠物通知点击发生在同一个 Electron renderer 内；本轮实测同会话 DOM dispatch 约 `1.8ms`，但跨会话 acceptance 被通知 tray 生命周期阻断。
- 本地桌面 overlay 当前会先做窗口准备，再通过命令文件、Python command pump 和 `CdpSessionSwitchBackend` 进行 CDP 切换；窗口准备不是成功的 CDP 激活所必需的正常前置条件。
- `src/codex_usage_hud/platforms/cdp_probe.py` 已按精确 session ID 查询 `data-app-action-sidebar-thread-id`，并保留标题/搜索路径作为已有兼容回退。
- `work_item_to_overlay_dict()` 已输出 `sessionId`、`status`、`statusText`、`lastText`、`progress`、`updatedAt` 等结构化字段；不能另建宠物私有 payload 或从 DOM 二次读取状态。
- `active_session_changed` 已是 renderer loop 的标准事件，会触发精确当前会话、session-file 和 work-overlay 刷新；成功的 overlay 激活应复用该事件语义。
- Renderer/CDP 是规范路径；不增加 Qt/Tk 新产品行为，不复制 Codex App 私有 IPC、压缩模块名或 `actionPath`。

## Requirements

### R1. CDP-first activation

- `activateSession` 命令收到后，若存在 session ID，先直接调用现有 `SessionSwitchController`/CDP backend。
- 成功、`already-active` 或已发出 `switch-requested` 时，不执行前置窗口准备。
- 仅在 CDP transport/target/backend 失败时执行一次窗口准备并重试；逻辑上的 `thread-not-found`、缺失目标或标题不匹配不通过窗口准备伪造成功。
- 保留现有 target cache、精确 ID 优先、标题/搜索兼容回退和一次性 sidebar reveal 行为。

### R2. Canonical identity and structured activation state

- `sessionId`/canonical conversation UUID 是激活、事件 session key、overlay matching 的唯一身份；`targetTitle`、`workdir` 只作展示和兼容上下文。
- `overlay_command_received` 的结构化 context 必须包含 requested/active session ID、requested/active title、backend、status、matchedBy、ok 和端到端 latency（若命令带有合法 `requestedAt`）。
- 现有 `WorkStatusItem` → overlay payload 字段继续作为状态唯一来源，至少保持 `status`、`statusText`、`lastText`、`progress`、`updatedAt` 的一致传播。

### R3. Event-driven refresh

- 成功的 overlay session activation 发布一次 `active_session_changed`，source 为本地 overlay，context 带 activation result；renderer loop 据此刷新精确当前 session 和 active work snapshot。
- 失败的 activation 仍发布现有 command event/诊断，但不得伪造 `active_session_changed` 或扫描最新 rollout。
- 不新增 idle whole-document polling；不把 command event 变成固定周期刷新。
- 相同 activation 结果不能造成重复高频事件；沿用现有 event loop/signature/refresh coalescing。

### R4. Verification and live measurement

- 补充单元测试覆盖 CDP-first、CDP 失败后一次窗口准备重试、结构化 activation event、成功事件唤醒和失败不唤醒 active session。
- 补充/保持 overlay payload 的 canonical ID 与结构化状态字段测试。
- 在当前 `lastSuccessfulPort` 对应的 live renderer 上记录本地激活 requestedAt→processed 的 latency；不得把宠物私有 dispatch 时间冒充本地端到端延迟。

## Acceptance Criteria

- [ ] AC1：CDP target 正常时，overlay 点击先走 CDP，不调用窗口准备；成功切换目标会话并发布一次 `active_session_changed`。
- [ ] AC2：CDP target/transport 失败时，系统最多执行一次窗口准备和一次重试；正常失败不进入无限重试或 idle polling。
- [ ] AC3：成功事件携带 canonical session ID 和结构化 activation context，renderer loop 刷新精确 session/work overlay；不会依赖用户再次点击 Codex sidebar。
- [ ] AC4：overlay payload 保持 `sessionId`、`status`、`statusText`、`lastText`、`progress`、`updatedAt`，且无 `actionPath`、私有 IPC 或压缩包模块依赖。
- [ ] AC5：现有 active-session、renderer、file watcher、work-overlay 测试通过；新增 focused tests 通过。
- [ ] AC6：live verification 记录本地 activation latency 和结果状态，主 Codex 会话恢复到测试前状态。

## Out of scope

- 复制 Codex App 私有 IPC、minified module filename 或 `actionPath`。
- 重新设计 HUD/桌面 bubble 外观或动画。
- 放宽 canonical UUID 到 rollout 的精确映射约束。
- 新增 Qt/Tk 产品行为或用固定 polling 模拟宠物通知。

## Open questions

无阻塞产品问题；实现选择受上述 contracts 约束。
