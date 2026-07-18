# 修复 multi-agent 子会话误出桌面气泡与完成态

## Goal

Codex CLI multi-agent v2 会为每个 subagent 写独立 jsonl。HUD 桌面会话气泡当前把子会话当成独立“会话”，导致：

1. 只有 1 个主 CLI 会话在跑时，气泡却显示多个；
2. 子 agent 完成后，主任务尚未结束就出现圆形“已完成”徽章（如 Rawls / Singer）。

本任务要求：桌面工作气泡只反映 **用户主会话（user thread）** 的工作状态；subagent 不得单独占气泡、不得冒充完成态。

## Requirements

1. 解析 Codex session_meta 中的 multi-agent 字段：
   - `thread_source`（`user` / `subagent` 等）
   - `parent_thread_id`
   - `agent_nickname`（可选展示信息）
2. 桌面工作气泡候选集默认 **排除** `thread_source == "subagent"` 的会话文件。
3. 完成态（`recent` / 圆形徽章）只能来自 **非 subagent** 会话的 task 结束；子会话 `task_complete` 不得单独变成可见完成气泡。
4. 普通单会话 CLI / Desktop 行为不变：无 multi-agent 时气泡逻辑与现网一致。
5. 有自动化回归：构造 parent + 多个 completed subagent 文件，断言气泡只剩父会话且父会话未完成时无 completed 圆标。

## Non-Goals

- 不在本任务做“子 agent 明细折叠进父气泡”的完整 UI 设计（可后续增强）。
- 不改变主 HUD（renderer/Qt 内嵌）用量统计对所有 jsonl 的聚合策略（除非气泡路径共用过滤函数时一并复用）。
- 不改 Codex 本身 multi-agent 协议。

## Acceptance Criteria

- [x] 实盘形态：1 个 user 父会话 + N 个已完成 subagent jsonl → `active_work_items_for_snapshot` 最多 1 个气泡，且 id 为父会话。
- [x] 父会话仍 `running`/`active` 时，列表中 **不出现** subagent 的 `recent` 完成态项。
- [x] 无 `thread_source` 的历史会话文件仍可正常出气泡（兼容旧日志）。
- [x] 相关单元测试通过：`tests/test_ui.py` 中新增 multi-agent 用例 + 现有 work overlay 回归不破。
- [x] `ruff check` / 定向 pytest 通过。

## Notes

- 实盘证据：`~/.codex/sessions/2026/07/18/` 下
  - 父：`019f73b9-454f-7ea0-8c78-e50e8956d80a`（`thread_source=user`）
  - 子：`...d835...` Rawls、`...1bc1...` Singer（`thread_source=subagent`, `parent_thread_id` 指向父）
- 用户确认：主因是 Codex CLI multi-agent 协作，而非“假会话”。
