# Implement checklist

1. [x] `ParsedSession` 增加 `thread_source` / `parent_thread_id` / `agent_nickname` / `is_subagent`
2. [x] `WorkStatusItem` 同步携带 multi-agent 身份字段
3. [x] `extract_session_thread_identity` + `parse_records` 填充（first session_meta wins）
4. [x] `cli.is_subagent_session` + `active_work_items_for_snapshot` 过滤全部 subagent
5. [x] `_stabilize_published_work_overlay_items` / visible cache 不复活 subagent
6. [x] `_work_item_from_snapshot` title 回落 `agent_nickname`
7. [x] `tests/test_parser.py` multi-agent meta
8. [x] `tests/test_ui.py` parent+2 completed subagents → 仅父气泡
9. [x] 定向 pytest 通过

## Validation

```bash
python -m pytest tests/test_parser.py::MultiAgentSessionMetaTests   tests/test_ui.py -q -k "filter_completed_subagent or MultiAgent or active_work_items_follow"
```

## Rollback

Revert parser 字段 + cli 过滤 + 测试即可。
