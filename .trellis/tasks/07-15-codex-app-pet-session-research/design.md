# Renderer 新会话事件与完成气泡修复设计

## Architecture

保持现有 renderer/CDP 主链：Codex 页面事件 -> injected JS `postActiveSession` -> CDP binding -> `ActiveSessionTracker` -> runtime event bus -> renderer loop -> snapshot/work overlay。

修复点放在 injected JS 的事件入口：保留 badge-enabled 时已有的 composer input watcher，在 badge-disabled 时增加独立的 active-session fallback，并始终补充 form submit capture listener；所有路径调用现有 bounded follow-up，不引入新的后台轮询线程或 Qt/Tk 路径。

## Data Flow

1. 用户点击 composer send、提交 composer form 或使用可识别的 Enter 发送；badge 开启时复用既有 input watcher，badge 关闭时使用 active-session fallback。
2. JS 立即读取 `readActiveSessionRef()` 并调用 `postActiveSession("composer-send", ref)`。
3. JS 在短窗口内按递增延迟重读 ref；如果 Codex 此时已发布 URL/侧边栏 canonical UUID，signature 改变后通过 binding 发送一次新 payload。
4. Python `observe_renderer_active_session()` 保持现有字段转换，调用 `observe_conversation_ref()`；精确 state DB 映射成功后 tracker 发布 `active_session_changed`。
5. renderer loop 由 runtime event 唤醒，重新解析当前 session，重新挂接精确 session-file watch，并刷新 active work items。
6. 完成事件被 parser 识别为 `recent` 后，`DesktopWorkOverlay.update()` 写入 completed payload，现有 helper 执行 card-to-circle 动画。

## Contracts

- `postActiveSession()` 的 signature 去重仍是唯一重复抑制机制。
- `ActiveSessionTracker` 继续把 provisional/new-session 清空为 pending；没有 canonical UUID 时不得猜测 rollout。
- follow-up 是一次性、有界的事件后补偿，不是 idle polling；所有 timer 必须由 `removeActiveSessionWatchers()` 取消。
- work overlay 只消费 `WorkStatusItem.status`，不直接读取 renderer DOM。

## Compatibility And Risks

- Codex DOM 的按钮/表单标签可能漂移，因此沿用现有 label 识别，并同时覆盖 click、submit、Enter；无匹配时现有 sidebar/history observer 仍工作。
- 发送 follow-up 可能增加短时 CDP binding 消息，但 signature 去重和有限窗口限制了开销。
- 如果 Codex 仍不暴露 canonical ref，系统继续显示 pending，这是数据一致性要求，不用最新文件替代。
- 当前 file watcher 已能匹配 SQLite WAL sidecars，不需要扩大 watcher 范围；保留现有 `session-map` invalidation 逻辑。

## Rollback

修改集中在 renderer injected script 和对应回归测试；若 live acceptance 暴露 DOM 兼容性问题，可撤销 composer listener/follow-up，既有 sidebar/history 路径不受影响。
