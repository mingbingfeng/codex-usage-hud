# Renderer Latency Baseline

- Measured at: `2026-07-03T06:53:50.480779Z`
- Sessions root: `C:\Users\zjxqm\.codex\sessions`
- Session file: `C:\Users\zjxqm\.codex\sessions\2026\07\03\rollout-2026-07-03T14-48-53-019f26bc-b231-7181-8ed6-25525ea3cf54.jsonl`
- Session size: `545644` bytes, `132` lines
- Synthetic session: `False`

| Operation | Median ms | P90 ms | Max ms | Iterations |
|-----------|-----------|--------|--------|------------|
| current_session_parse_full | 7.111 | 7.111 | 7.111 | 1 |
| renderer_payload_build | 1.141 | 1.141 | 1.141 | 1 |
| usage_summary_full_scan | 69.765 | 69.765 | 69.765 | 1 |
| usage_summary_refresh_current_file | 2.708 | 2.708 | 2.708 | 1 |
| file_watcher_poll_signature | 55.724 | 55.724 | 55.724 | 1 |
| append_then_parse_and_payload | 20.158 | 20.158 | 20.158 | 1 |

## Notes

- This local harness does not measure live CDP transport, renderer DOM paint, or user-visible end-to-end latency.
- append_then_parse_and_payload writes only to a temporary copy of the selected session file.
- file_watcher_poll_signature represents the polling fallback scan cost, not native watcher delivery latency.
