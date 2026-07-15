# Codex App app-server 接入可行性证明

## Goal

用最小、可撤销、默认只读的 POC 证明 HUD 是否存在可稳定复用的 app-server 事件入口。证明范围包括：能否附着 Codex Desktop 当前连接或安全建立观察者连接、能否收到当前 turn 的结构化事件、是否干扰 owner/follower 角色，以及 schema 与 Windows/macOS 的兼容风险。

只有全部证明门槛通过后，才讨论 HUD 接入设计；本任务不直接修改生产 HUD 状态链。

## Confirmed Facts

- Codex Desktop 当前以 stdio 启动一个 `codex app-server` 子进程，Electron 主进程持有该 transport。
- 主 renderer 是 active conversation stream 的 owner；宠物 renderer 可作为相同 canonical conversation UUID 的 follower。
- 宠物状态来自 renderer 内部 conversation store 对 thread/turn/request 通知的派生，不来自 WebSocket、HTTP、通用 worker bridge 或无参数 shared snapshot。
- preload 没有暴露公开的 app-server notification subscription；现有 renderer 内部消息 envelope、store callbacks 和 follower 协调均属于私有实现。
- HUD 当前已有 canonical `sessionId`、rollout/state/session-file watcher 和结构化 `WorkStatusItem`，不能在 POC 中被替换或形成第二权威状态源。

## Requirements

### R1. Discover supported protocol and transport boundaries

- 从本机 Codex Desktop/CLI 的 help、schema、进程树和当前连接证据确认 app-server 支持的 transport、初始化握手、thread read/list/resume/subscribe 语义及版本信息。
- 区分正式可调用协议、Desktop 主进程私有路由和 renderer 私有 store API。
- 不猜测或复制压缩模块标识、私有 action route 或 React/Fiber 状态。

### R2. Prove or disprove safe observation

- 优先寻找不接管 Desktop stdio、不注入宠物 store、不修改当前会话的观察入口。
- 若只能启动第二个 app-server 进程，先证明它是否能只读看到既有 thread，并明确它能否订阅正在运行的 turn。
- 在没有证据证明安全前，不调用可能改变角色或状态的 `thread/resume`、turn start、approval response、archive、metadata update 等操作。

### R3. Verify event and identity semantics

- 记录可观察事件的 method、顶层字段、thread/turn/item/request ID、顺序、重复和完成生命周期；敏感正文只做字段存在性验证，不写入研究 artifacts。
- 证明 app-server thread ID 与本地 canonical `sessionId` 的等价边界，并保留 `client-new-thread:*` provisional 约束。
- 判断结构化 waiting/tool/exec/network/permission/plan 状态来自原始协议事件还是 Desktop renderer 的二次派生。

### R4. Prove non-interference

- 观察前后记录 Desktop owner/follower、当前 active session、app-server PID/transport 和 Codex App 可用性。
- POC 不得抢占 owner、触发 resume、改变当前会话、生成新 turn、影响审批请求或导致 Desktop 重连。
- 任一非预期状态变化立即停止实验并记录恢复方式。

### R5. Assess stability and cross-platform viability

- 区分协议 schema、CLI capability、Electron IPC envelope、日志格式和平台进程管理的稳定性等级。
- Windows live 证据必须实测；macOS 若无 live 环境，只能给出源码/协议层风险，不得声称已验证。
- 明确版本探测、能力协商、断线恢复、背压和 schema 漂移所需成本。

## Acceptance Criteria

- [x] AC1：确认现有 Desktop app-server transport 是否存在安全附着点；若不存在，给出可复现证据。
- [x] AC2：证明第二观察者能或不能读取既有 thread，并能或不能接收当前 turn 的实时结构化事件。
- [x] AC3：实验前后 owner/follower、active session 和 Desktop 连接状态一致，无用户可见副作用。
- [x] AC4：列出实际事件字段、生命周期、sessionId 语义、缺失字段及重复/顺序风险。
- [x] AC5：给出 Windows 实测结论与 macOS 风险矩阵，并标注已验证和未验证项。
- [x] AC6：形成明确的 go/no-go 结论；只有 go 才提出最小 HUD 接入边界，no-go 则保持现有事件驱动 snapshot 路径。

## Out of Scope

- 修改 HUD 生产代码或切换状态权威。
- 复制 Electron 私有 IPC、renderer store、压缩模块标识、宠物 DOM/React Fiber 或内部 action route。
- 发送新 prompt、批准请求、切换会话或人为制造有副作用的 Codex App 状态。
- 用固定轮询、日志全文尾随或宠物 DOM 作为长期数据源。

## Stop Conditions

- 找到经过能力协商、只读、可恢复且不影响 owner/follower 的事件订阅入口；或
- 证明 Desktop stdio 不可附着，第二 app-server 无法观察既有 live turn，剩余路径均依赖私有 IPC/store 或有状态 resume。
