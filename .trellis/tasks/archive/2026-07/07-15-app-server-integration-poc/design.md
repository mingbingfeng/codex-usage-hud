# App-server 接入证明设计

## Approach

采用分级证据路径，每一级失败后才进入下一项；默认不发送协议请求：

1. 静态能力发现：本机二进制 help/schema、Desktop 命令行、日志与协议定义。
2. 现有连接边界：确认 stdio 所有权、是否存在 socket/pipe/listen endpoint，以及 renderer 可见的公共订阅面。
3. 隔离观察者：如二进制支持，在独立临时进程中完成 initialize 并只调用无状态 read/list 类方法。
4. Live 事件证明：仅在协议明确提供只读 subscribe/attach 时，订阅一个已知 canonical thread；不得用 resume 模拟 subscribe。
5. 非干扰审计：比较实验前后的 PID、owner/follower、active session 和 Desktop 日志。

## Evidence Model

每条结论标记为：

- `live-confirmed`：本机运行时直接观察。
- `schema-confirmed`：本机二进制生成的 schema/help 明确支持。
- `package-private`：仅由 Desktop 私有实现证明。
- `unverified`：缺少运行环境或安全实验条件。

研究输出只保存 method、字段名、ID 类型、时间与状态转换，不保存消息正文、凭据或审批内容。

## Safety Boundary

- 允许：进程/端口/pipe 枚举、help/schema、只读日志、CDP target 元数据、隔离进程 initialize、明确无状态的 list/read。
- 条件允许：协议文档明确为观察用途的 subscribe/attach，且能指定 existing thread 而不改变角色。
- 禁止：resume/start/turn start、approval response、metadata write、archive、私有 IPC 注入、store/Fiber 读取和固定轮询。

## Decision Matrix

Go 必须同时满足：

- 有版本化或可能力探测的入口；
- 可接收 existing live turn 事件；
- 不改变 owner/follower 或 active session；
- 可用 canonical UUID 对齐本地 session；
- Windows 可恢复，macOS 有可实现的同构 transport；
- 失败时可无损退回现有 snapshot/watch 路径。

任一条件不满足即 no-go，不以私有 renderer store 补洞。

## Rollback

POC 使用独立短生命周期进程和内存内事件采样。退出观察者、确认 Desktop PID/角色/active session 未变即完成回滚；本任务不修改生产配置或源码。
