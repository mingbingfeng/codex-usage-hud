# Real session evidence (2026-07-18)

From `~/.codex/sessions/2026/07/18/`:

- Parent user thread: `019f73b9-454f-7ea0-8c78-e50e8956d80a` (`thread_source=user`, originator `codex-tui`)
- Subagent Rawls: `019f73d7-d835-7f90-a844-08df4f5b0deb` (`thread_source=subagent`, `parent_thread_id` → parent, `agent_nickname=Rawls`)
- Subagent Singer: `019f73d8-1bc1-7de2-9e9f-ff2c86d2dc13` (same parent)
- Subagent Avicenna: `019f73d7-8022-7c73-b895-7a6bf8db0a9e`

Some child rollouts also append a second `session_meta` rewriting parent id; parser must keep **first** meta.

Root cause of multi bubbles + premature completed badges:
`active_work_items_for_snapshot` treated every recent jsonl as an independent session.
