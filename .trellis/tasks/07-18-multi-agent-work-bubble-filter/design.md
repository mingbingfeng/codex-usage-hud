# Design: multi-agent work bubble filter

## Boundary

| Layer | Change |
|---|---|
| `core/parser.py` | 从 **首个** `session_meta` 解析 multi-agent 身份字段，写入 `ParsedSession` |
| `cli.py` | 构建桌面气泡时跳过 subagent；item title 优先 `agent_nickname` 仅在未来需要展示时用（本任务主路径是过滤） |
| tests | 用合成 jsonl 覆盖 parent+subagents |

## Contracts

### ParsedSession 新字段（可选默认空）

- `thread_source: str = ""` — raw meta `thread_source`
- `parent_thread_id: str = ""` — meta `parent_thread_id` 或 source.subagent.thread_spawn.parent_thread_id
- `agent_nickname: str = ""` — 展示名

### 判定

```text
is_subagent_session(snapshot) :=
  thread_source.strip().lower() == "subagent"
  OR parent_thread_id non-empty
```

优先 `thread_source == "subagent"`；`parent_thread_id` 作兜底（旧/残缺 meta）。

### session_meta 选择

保持现有 “first session_meta wins” 语义：subagent 文件首条 meta 是子身份，第二条可能是父回写——**必须用第一条** 才能识别 subagent（当前 `session_meta_payload` 已是 first-win，与实盘一致）。

### Bubble pipeline

`active_work_items_for_snapshot`:

1. current snapshot → item（若是 subagent 当前选中，仍可显示 current，避免完全不可见；**但** CLI 主跟随时通常 current 是父或当前活跃子）
2. recent files → parse → **skip if subagent and not current**
3. cache / select 逻辑不变

更干净的策略（本任务采用）：

- **一律跳过 subagent**（含 current）：主会话跟随时 current 应是父会话或用户可见主线程；若 CDP 偶发指向 subagent 文件，仍应回落父会话气泡（通过 parent 不跳过）。若只有 subagent 路径、父文件不可见，允许 current subagent 显示一次（`current=True` 例外），避免“气泡全空”。

最终策略：

```text
if is_subagent and not current:
    drop
if is_subagent and current:
    keep (current-only exception)
```

## Data flow

```
jsonl session_meta(first)
  -> ParsedSession.thread_source / parent_thread_id / agent_nickname
  -> _work_item_from_snapshot / active_work_items_for_snapshot filter
  -> DesktopWorkOverlay items
```

## Compatibility

- 旧 jsonl 无 multi-agent 字段：行为与现在一致。
- Desktop vscode 会话：通常无 subagent meta，不受影响。

## Risks

| Risk | Mitigation |
|---|---|
| 父会话 id 与文件名不一致、多个 rollout 同 id | 现有 path key 仍按 path 扫；item id 用 session_id，可能合并同 id——本任务不改合并语义 |
| 用户希望看到子 agent 进度 | Non-goal；后续可折叠到父卡片 |
