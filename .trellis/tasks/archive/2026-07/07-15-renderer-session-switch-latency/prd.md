# 修复 renderer 会话切换延迟

## Goal

让 Codex App 内切换会话后，renderer HUD 在本地会话映射尚未提交时保持明确的 pending 状态，并在映射可用的第一时间重新解析，不再引入固定的秒级等待。

## Confirmed facts

- Renderer 点击处理会即时通过 CDP binding 发布 canonical thread UUID；检测点击不是瓶颈。
- `ActiveSessionTracker.path_from_renderer_thread_id()` 对未命中映射缓存 `None` 达 2 秒（`src/codex_usage_hud/platforms/active_session.py:824-869`）。
- `session-map` 文件事件才会清除该缓存（`src/codex_usage_hud/cli.py:6575-6577`），但 `_RendererFileEventSource` 只让理由恰为 `{"session"}` 的事件立即唤醒；`session-map` 会经过 0.75 秒防抖（`src/codex_usage_hud/cli.py:2421-2505`）。
- 真实运行日志持续记录 `active_session.unmatched_thread`；一次 pending refresh 的 Python snapshot 时间为 345.6ms，而非多秒预算扫描。
- 热缓存下真实本机的全量预算扫描约 85ms、增量约 3ms，因此不是本缺陷的主因。
- `docs/RENDERER_MODE_STRATEGY.md` 要求 state/session-map 文件事件立即重新解析已选 UUID，不得等待下一次点击或轮询。

## Requirements

- R1. `session-map` 变化必须立即唤醒 renderer 循环并清除精确 thread-to-path 映射缓存。
- R2. 未映射 renderer UUID 的短期负缓存不得遮蔽刚提交的 state-db 映射；映射事件后首次解析必须查询 SQLite。
- R3. 保持严格 renderer mapping：不得退回标题匹配、递归扫描或最新文件猜测。
- R4. `sessions-root`、设置与普通 session JSONL 的去抖行为保持不变，避免把文件写入风暴转为 HUD 刷新风暴。
- R5. 修改后仍兼容 Windows/macOS renderer mode，并保留 pending 可见诊断。

## Acceptance criteria

- AC1. `session-map` 事件在有其他同批理由时仍立即唤醒，并在循环内触发 `active_session_changed` 精确重解析。
- AC2. 已缓存 `None` 的 renderer UUID 在 `invalidate_mapping_cache()` 后能够立即查到新 state-db row。
- AC3. 自动测试覆盖 session-map 的即时唤醒、缓存失效及不使用标题/文件树 fallback。
- AC4. 运行 `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q`、`python -m compileall -q src tests tools` 和 `git diff --check` 通过。
- AC5. `python tools/measure_renderer_latency.py --markdown-output renderer_latency_baseline.md` 完成，且不会把切换路径重新引入全量预算扫描。

## Out of scope

- 多 Profile 用量归因、价格与 CLI 气泡交互。
- Qt/Tk 产品行为。
- 改变 Codex App 自身将 renderer UUID 写入 state database 的时机。

## Open question

无。用户已要求并行处理；修复边界由 renderer 架构契约和现有运行证据确定。
