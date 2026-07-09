# Renderer Latency Baseline

- Measured at: `2026-07-09T02:39:06.407273Z`
- Sessions root: `C:\Users\zjxqm\.codex\sessions`
- Session file: `C:\Users\zjxqm\.codex\sessions\2026\07\09\rollout-2026-07-09T09-24-12-019f4479-98e8-71e1-9104-1855bb3e63e8.jsonl`
- Session size: `2059664` bytes, `639` lines
- Synthetic session: `False`

| Operation | Median ms | P90 ms | Max ms | Iterations |
|-----------|-----------|--------|--------|------------|
| current_session_parse_full | 14.990 | 15.718 | 15.984 | 7 |
| renderer_payload_build | 0.776 | 0.820 | 0.841 | 7 |
| usage_summary_full_scan | 64.222 | 65.617 | 67.379 | 7 |
| usage_summary_refresh_current_file | 8.080 | 8.294 | 10.432 | 7 |
| file_watcher_poll_signature | 52.193 | 52.735 | 52.815 | 7 |
| append_then_incremental_parse_and_payload | 15.313 | 15.922 | 16.238 | 7 |

## Regression Budgets

| Operation | P90 ms | Budget ms | Status |
|-----------|--------|-----------|--------|
| current_session_parse_full | 15.718 | 50.000 | PASS |
| renderer_payload_build | 0.820 | 25.000 | PASS |
| usage_summary_full_scan | 65.617 | 250.000 | PASS |
| usage_summary_refresh_current_file | 8.294 | 25.000 | PASS |
| file_watcher_poll_signature | 52.735 | 250.000 | PASS |
| append_then_incremental_parse_and_payload | 15.922 | 250.000 | PASS |

## Notes

- This local harness does not measure live CDP transport, renderer DOM paint, or user-visible end-to-end latency.
- current_session_parse_full and usage_summary_refresh_current_file run against temporary copies of the selected session file to avoid concurrent writes from the live Codex session skewing local parser timings.
- append_then_incremental_parse_and_payload writes only to a temporary copy of the selected session file.
- file_watcher_poll_signature represents the polling fallback scan cost, not native watcher delivery latency.
