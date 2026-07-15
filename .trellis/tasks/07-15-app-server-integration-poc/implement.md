# App-server 接入证明执行计划

## Ordered Checklist

- [x] 记录当前 Git、Codex Desktop/app-server 进程、CDP targets、active canonical session 和 owner/follower 基线。
- [x] 检查本机 Desktop/CLI app-server help、版本、schema 生成与可用 transport；保存精简字段清单。
- [x] 检查当前 app-server stdio 的父子关系、handle/pipe/listener，证明能否附着现有 transport。
- [x] 从正式 schema/源码边界区分 read/list、subscribe/attach、resume/start 和写操作。
- [x] 若安全，启动隔离 app-server，仅 initialize 并执行最小 thread list/read；记录进程与 schema 版本。
- [x] 仅在存在明确只读订阅时验证 existing live turn 事件；否则记录 no-go，不调用 resume。
- [x] 对齐 threadId/turnId/itemId/requestId、状态生命周期、顺序、重复和缺失字段。
- [x] 复查 Desktop owner/follower、active session、进程连接和日志，确认无副作用。
- [x] 输出 Windows live 结果、macOS 风险矩阵及 go/no-go；不改 HUD 代码。

## Verification

- 所有实验命令及退出码可复现。
- 实验前后 Codex Desktop 主进程、app-server PID、active session 与 stream role 对比一致。
- Git diff 只允许当前 Trellis task artifacts；并发任务路径不得纳入。
- 不产生新的 rollout、turn、approval response 或 session metadata 变更。

## Risk And Rollback

- 最大风险是错误调用 resume/subscribe 导致 stream role 变化，因此任何未明确标为只读的协议调用都不执行。
- 隔离进程设短超时并在验证后正常退出；不得终止 Desktop 持有的 app-server。
- 若观察到 Desktop 重连、角色变化或 active session 改变，立即停止并记录恢复后的基线。

## Review Gate

本计划获用户确认后运行 `task.py start`。Phase 2 仅执行只读/隔离 POC；任何生产接入必须另建实现任务。
