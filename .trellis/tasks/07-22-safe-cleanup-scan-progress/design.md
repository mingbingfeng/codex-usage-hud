# Design: Safe Cleanup Scan Progressive Feedback

## Boundaries

| Layer | Responsibility |
| --- | --- |
| `SafeCleanupManager.scan` | 分阶段扫描；阶段边界 `mark_operation` + 可选 partial inventory 快照 |
| `_SafeCleanupWorker` | 串行执行；publish 每次 snapshot（含 scanning partial） |
| `session_cleanup` | 扫描开始 mark scanning；完成前至少 phase 文案；可不 partial 行 |
| `renderer_hud.js` 内嵌 | 根据 `operation.state/phase/progress/groups` 渲染启动壳、扫描条、渐进列表、重扫遮罩 |
| Contracts | 扩展 operation 中性字段；groups 仍无路径 |

## Operation payload extension

Existing:

```text
id, requestId, action, state, progress, error, ...
```

Add during `state=scanning` (and clear/ignore when completed):

```text
phase: "hud" | "codex" | "processes" | "caches" | "backups" | "sqlite" | "preview" | "sessions" | "merge" | "capability"
phaseLabel: neutral Chinese/English label already UI-mapped
phaseIndex: 1-based int
phaseCount: int
discoveredGroups: int   # count of groups currently in payload
discoveredBytes: int    # reclaimable safe bytes in current partial totals
```

`progress`: 0–99 while scanning (stage weights), 100 on terminal states.

## Scan phases (junk)

| Index | phase key | Weight (approx) | Work |
| --- | --- | --- | --- |
| 1 | hud | 12 | `_scan_hud_runtime` |
| 2 | codex | 16 | `_scan_codex_candidates` |
| 3 | processes | 8 | running process names |
| 4 | caches | 40 | each cache definition (progress subdivides by definition count) |
| 5 | backups | 12 | `_scan_old_backups` |
| 6 | sqlite | 12 | sqlite targets |
| (post) | preview | — | worker 内 default preview；UI 可显示「生成默认安全预览」或直接 completed |

Stage callback publishes:

1. Update `operation` scanning fields
2. Rebuild temporary inventory from items accumulated so far（revision 可用 `scanning-{requestId}` 或空 revision 策略见下）
3. `snapshot()` → event bus

### Partial revision rule

- **Scanning partials**: `revision=""` or `revision` 以 `scanning:` 前缀；`defaultSelectedIds` 可填但 UI **不得** 当 preview 启用确认。
- **Final scan**: 正式 revision + state 将经 preview 变为 `preview`（现有 worker 逻辑）。
- UI 判定「已扫描可确认」：`operation.state === "preview"` + confirmationToken（现网）。
- UI 判定「扫描中」：`state in scanning|accepted` 或 pendingRequestId，且无可用 preview token。

保留旧结果重扫：

- 客户端在发起 rescan 时保留 `lastStableData`（上一正式 snapshot）
- 新 scanning partial 覆盖操作条，列表可显示 dimmed `lastStableData` 直到 final 到达

## Progress publishing API (Python)

```python
def _publish_scan_progress(
    self,
    *,
    request_id: str,
    phase: str,
    phase_index: int,
    phase_count: int,
    progress: int,
    items: list[CleanupItem],
    generated_at: float,
) -> None:
    # mark_operation + temporary inventory to_payload without permanent revision
```

Worker already publishes after mark_operation at enqueue; scan() will call an optional `on_progress` callback if set by worker, OR manager holds `progress_publisher: Callable[[dict], None] | None`.

Prefer: **worker sets** `manager.progress_publisher = self._publish` during scan, cleared after.

## UI structure (renderer)

### Junk not scanned + not busy

Existing empty state.

### Junk scanning, no stable revision (or first scan)

1. Optional boot shell if `groups.length === 0` and progress &lt; first group
2. Else: scan-strip + scanning summary-band + progressive rows

### Junk scanning with lastStableData (rescan)

scan-strip + dimmed lastStable list + rescan chip; footer confirm disabled.

### Junk complete/preview

Existing result UI; strip hidden.

### Session

Mirror strip + empty/boot or skeleton table; no need for partial sessions in v1.

## Labels (UI map)

```text
hud → HUD 诊断
codex → Codex 临时项
processes → 相关应用状态
caches → 应用与开发缓存
backups → 旧清理备份
sqlite → 历史数据库
preview → 生成默认安全预览
sessions → 读取会话索引
merge → 归并主会话与子任务
capability → 校验删除能力
```

## Compatibility

- Old UIs ignoring new fields: still work (progress 0→100).
- Tests that assert final snapshot only: still pass if final shape unchanged.
- confirmationToken never on scanning partials.

## Rollout / rollback

- Feature is pure UX + extra publishes; rollback = stop publishing partials + UI falls back to button label only.
- No settings flag required for v1.

## Risks

| Risk | Mitigation |
| --- | --- |
| Partial inventory 误启用确认 | UI 硬门槛：仅 preview+token |
| 频繁 publish 卡 UI | 阶段边界 + caches 每 N 个 definition 节流（至少每 definition 一次上限） |
| 列表跳动 | 固定占位顺序；或按 tier 稳定排序 |
| 线程安全 | 仅 worker 线程写 manager；主线程只读 payload 事件 |
