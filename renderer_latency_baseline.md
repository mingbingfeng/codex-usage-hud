# Renderer Latency Baseline

- Measured at: `2026-07-09T03:07:40.535707Z`
- Sessions root: `C:\Users\zjxqm\.codex\sessions`
- Session file: `C:\Users\zjxqm\.codex\sessions\2026\07\09\rollout-2026-07-09T10-49-08-019f44c7-59fe-7bc0-85cf-137062c731f5.jsonl`
- Session size: `1013318` bytes, `551` lines
- Synthetic session: `False`

| Operation | Median ms | P90 ms | Max ms | Iterations |
|-----------|-----------|--------|--------|------------|
| current_session_parse_full | 28.169 | 29.314 | 30.394 | 7 |
| renderer_payload_build | 6.820 | 7.009 | 7.184 | 7 |
| usage_summary_full_scan | 195.670 | 213.923 | 222.266 | 7 |
| usage_summary_refresh_current_file | 10.930 | 11.626 | 11.816 | 7 |
| file_watcher_poll_signature | 163.925 | 179.542 | 180.281 | 7 |
| append_then_incremental_parse_and_payload | 43.600 | 46.136 | 46.955 | 7 |

## Regression Budgets

| Operation | P90 ms | Budget ms | Status |
|-----------|--------|-----------|--------|
| current_session_parse_full | 29.314 | 50.000 | PASS |
| renderer_payload_build | 7.009 | 25.000 | PASS |
| usage_summary_full_scan | 213.923 | 250.000 | PASS |
| usage_summary_refresh_current_file | 11.626 | 25.000 | PASS |
| file_watcher_poll_signature | 179.542 | 250.000 | PASS |
| append_then_incremental_parse_and_payload | 46.136 | 250.000 | PASS |

## Notes

- This local harness does not measure live CDP transport, renderer DOM paint, or user-visible end-to-end latency.
- current_session_parse_full and usage_summary_refresh_current_file run against temporary copies of the selected session file to avoid concurrent writes from the live Codex session skewing local parser timings.
- append_then_incremental_parse_and_payload writes only to a temporary copy of the selected session file.
- file_watcher_poll_signature represents the polling fallback scan cost, not native watcher delivery latency.
