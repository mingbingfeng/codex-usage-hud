# Renderer Latency Baseline

- Measured at: `2026-07-09T01:48:22.283478Z`
- Sessions root: `C:\Users\zjxqm\.codex\sessions`
- Session file: `C:\Users\zjxqm\.codex\sessions\2026\07\09\rollout-2026-07-09T09-24-12-019f4479-98e8-71e1-9104-1855bb3e63e8.jsonl`
- Session size: `810313` bytes, `239` lines
- Synthetic session: `False`

| Operation | Median ms | P90 ms | Max ms | Iterations |
|-----------|-----------|--------|--------|------------|
| current_session_parse_full | 6.516 | 6.744 | 8.328 | 7 |
| renderer_payload_build | 1.139 | 1.230 | 1.515 | 7 |
| usage_summary_full_scan | 67.752 | 68.125 | 68.266 | 7 |
| usage_summary_refresh_current_file | 3.372 | 3.880 | 3.937 | 7 |
| file_watcher_poll_signature | 54.798 | 55.416 | 58.903 | 7 |
| append_then_incremental_parse_and_payload | 10.239 | 10.784 | 12.053 | 7 |

## Regression Budgets

| Operation | P90 ms | Budget ms | Status |
|-----------|--------|-----------|--------|
| current_session_parse_full | 6.744 | 50.000 | PASS |
| renderer_payload_build | 1.230 | 25.000 | PASS |
| usage_summary_full_scan | 68.125 | 250.000 | PASS |
| usage_summary_refresh_current_file | 3.880 | 25.000 | PASS |
| file_watcher_poll_signature | 55.416 | 250.000 | PASS |
| append_then_incremental_parse_and_payload | 10.784 | 250.000 | PASS |

## Notes

- This local harness does not measure live CDP transport, renderer DOM paint, or user-visible end-to-end latency.
- current_session_parse_full and usage_summary_refresh_current_file run against temporary copies of the selected session file to avoid concurrent writes from the live Codex session skewing local parser timings.
- append_then_incremental_parse_and_payload writes only to a temporary copy of the selected session file.
- file_watcher_poll_signature represents the polling fallback scan cost, not native watcher delivery latency.
